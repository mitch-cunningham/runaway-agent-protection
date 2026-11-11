"""Provider-neutral, in-process runaway admission guard. Python 3.10+.
A skill is guidance; this module enforces limits only where the host wires it in.
"""
from __future__ import annotations
import contextvars, functools, hashlib, inspect, json, threading, time, uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

class Stop(RuntimeError):
    """Local terminal stop. Catch outside the SDK retry loop; do not auto-retry."""

@dataclass(frozen=True)
class Policy:
    job_calls: int = 100
    job_seconds: float = 3600
    identical_request_limit: int = 6
    target_hosts: tuple[str, ...] = ()

    def __post_init__(self):
        for key in ('job_calls', 'identical_request_limit'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'{key} must be a positive integer')
        if isinstance(self.job_seconds, bool) or not isinstance(self.job_seconds, (int, float)) or not 0 < self.job_seconds < float('inf'):
            raise ValueError('job_seconds must be positive and finite')
        if any(not h or '/' in h or ':' in h for h in self.target_hosts):
            raise ValueError('target_hosts must contain bare hostnames, not URLs')

@dataclass
class Job:
    id: str
    started: float
    lock: object = field(default_factory=threading.Lock)
    calls: int = 0
    fingerprint: str = ''
    streak: int = 0
    stopped: str = ''

CURRENT: contextvars.ContextVar[Job | None] = contextvars.ContextVar('runaway_protection_job', default=None)

def new_job(job_id: str | None = None) -> Job:
    return Job(id=job_id or uuid.uuid4().hex, started=time.monotonic())

@contextmanager
def job_scope(job: Job | None = None, current=CURRENT):
    """Reuse inherited Job. A scheduler must retain this object across retries.
    A job ID string alone is not a persistent ledger or a replay barrier.
    """
    inherited = current.get()
    if inherited is not None and job is not None and job is not inherited:
        raise ValueError('A child cannot replace its inherited job and reset its counters')
    active = inherited or job or new_job()
    token = current.set(active)
    try:
        yield active
    finally:
        current.reset(token)

def fingerprint_request_body(body: dict) -> str:
    """Last real user input + tool observations across OpenAI, Claude, Gemini.
    Assistant prose and request IDs do not demonstrate progress. Use a custom
    fingerprinter for other protocols; hashes are local and no text is logged.
    """
    messages = body.get('messages', body.get('input', body.get('contents', [])))
    user, tools = '', []
    if isinstance(messages, str):
        user = messages
    elif isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get('role')
            content = message.get('content', message.get('parts', []))
            if role == 'tool' or message.get('type') == 'function_call_output':
                tools = [message.get('content', message.get('output', ''))]
                continue
            blocks = content if isinstance(content, list) else []
            results = [b for b in blocks if isinstance(b, dict) and (b.get('type') == 'tool_result' or 'functionResponse' in b)]
            if results:
                tools = [{"content": b.get("content"), "is_error": bool(b.get("is_error", False))} if b.get("type") == "tool_result" else {"name": b["functionResponse"].get("name"), "response": b["functionResponse"].get("response")} for b in results]
            if role == 'user':
                actual = [b for b in blocks if b not in results] if blocks else content
                if actual:
                    user = actual
                    if not results:
                        tools = []
    return hashlib.sha256(json.dumps([user, tools], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def admit(job: Job, body: dict, policy: Policy, now: float | None = None,
          fingerprinter: Callable = fingerprint_request_body) -> None:
    """Count a physical attempt immediately before dispatch. No I/O or billing."""
    now = time.monotonic() if now is None else now
    with job.lock:
        if job.stopped:
            raise Stop(job.stopped)
        reason = ''
        if now - job.started >= policy.job_seconds:
            reason = f'Job reached its {policy.job_seconds:g}s admission deadline.'
        elif job.calls >= policy.job_calls:
            reason = f'Job reached its {policy.job_calls}-request limit.'
        else:
            fp = fingerprinter(body)
            streak = job.streak + 1 if fp == job.fingerprint else 1
            if streak > policy.identical_request_limit:
                reason = f'Job repeated the same input and tool observation {policy.identical_request_limit} times without progress.'
            else:
                job.fingerprint, job.streak = fp, streak
        if reason:
            job.stopped = reason + ' Stop this job; wait for an explicit new instruction.'
            raise Stop(job.stopped)
        job.calls += 1

def example_turn_wrapper(run_turn: Callable, current=CURRENT):
    """Sync and async examples. Frameworks must propagate context to threads."""
    if inspect.iscoroutinefunction(run_turn):
        @functools.wraps(run_turn)
        async def wrapped(*args, **kwargs):
            with job_scope(current=current):
                return await run_turn(*args, **kwargs)
    else:
        @functools.wraps(run_turn)
        def wrapped(*args, **kwargs):
            with job_scope(current=current):
                return run_turn(*args, **kwargs)
    return wrapped

def default_model_request(request) -> bool:
    path = request.url.path.rstrip('/')
    return request.method.upper() == 'POST' and (path.endswith(('/chat/completions', '/responses', '/messages')) or path.endswith((':generateContent', ':streamGenerateContent')))

@dataclass
class Installation:
    policy: Policy
    current: object
    matcher: Callable
    fingerprinter: Callable
    sync: Callable
    async_send: Callable
    wrapped_sync: Callable | None = None
    wrapped_async: Callable | None = None

    def close(self):
        """Remove only this installation; do not clobber another wrapper."""
        import httpx
        if httpx.Client.send is not self.wrapped_sync or httpx.AsyncClient.send is not self.wrapped_async:
            raise RuntimeError('Transport wrappers changed; refusing an unsafe uninstall')
        httpx.Client.send, httpx.AsyncClient.send = self.sync, self.async_send
        delattr(httpx.Client, '_runaway_protection_installation')

def install(policy: Policy, current=CURRENT, is_model_request: Callable = default_model_request,
            fingerprinter: Callable = fingerprint_request_body) -> Installation:
    """Intercept supported HTTPX model POSTs on explicitly supplied hosts.
    Missing scope raises Stop; never create a fresh counter for each side call.
    Stops are LOCAL exceptions, not fake provider auth/quota errors. The host
    must treat Stop (or a wrapping SDK exception with a stopped Job) as final.
    """
    import httpx
    if not policy.target_hosts:
        raise ValueError('Specify the actual provider/proxy hostnames before installing')
    existing = getattr(httpx.Client, '_runaway_protection_installation', None)
    if existing:
        if (existing.policy, existing.current, existing.matcher, existing.fingerprinter) == (policy, current, is_model_request, fingerprinter):
            return existing
        raise RuntimeError('A different guard is already installed; do not stack guards')
    inst = Installation(policy, current, is_model_request, fingerprinter, httpx.Client.send, httpx.AsyncClient.send)
    hosts = {h.lower() for h in policy.target_hosts}

    def prepare(request):
        if request.url.host.lower() not in hosts or not is_model_request(request):
            return
        job = current.get()
        if job is None:
            raise Stop('Model request has no logical job scope; connect the framework adapter before dispatch.')
        try:
            body = json.loads(request.content) if request.content else {}
        except Exception as exc:
            raise Stop('Cannot inspect this model request; supply a compatible adapter.') from exc
        if not isinstance(body, dict):
            raise Stop('Model request body must be an object; supply a compatible adapter.')
        admit(job, body, policy, fingerprinter=fingerprinter)

    @functools.wraps(inst.sync)
    def send(client, request, *args, **kwargs):
        prepare(request)
        return inst.sync(client, request, *args, **kwargs)

    @functools.wraps(inst.async_send)
    async def asend(client, request, *args, **kwargs):
        prepare(request)
        return await inst.async_send(client, request, *args, **kwargs)

    inst.wrapped_sync, inst.wrapped_async = send, asend
    httpx.Client.send, httpx.AsyncClient.send = send, asend
    httpx.Client._runaway_protection_installation = inst
    return inst
