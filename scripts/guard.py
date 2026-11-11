"""
runaway-agent-protection — generic runtime guardrail for LLM-agent runaway loops.

Problem this solves
--------------------
Agent frameworks (Hermes, LangChain, custom harnesses) can enter three failure
modes that pure prompting never reliably prevents, because the LLM itself is
the thing deciding whether to continue:

  1. A "logical job" (one user turn, including every retry and automatic
     continuation the framework spawns on top of it) runs away and keeps
     calling the model indefinitely.
  2. The agent gets stuck re-issuing the same request with no new input and
     no new tool result — a stall, not progress.
  3. Once stopped, something in the framework auto-retries or auto-resumes
     the same job, silently repeating the failure.

This module enforces hard stops for all three at the HTTP layer, below the
model/framework's own control flow, so a confused or compromised agent loop
cannot talk its way past the limit. It is intentionally framework-agnostic:
hook `install()` into whatever boundary your framework calls "one turn."

Design
------
- A `Job` is the unit of accounting: one external request plus every retry
  and auto-continuation chained to it. Frameworks propagate a `Job` across
  recursive/nested calls using a `contextvars.ContextVar` — see
  `example_turn_wrapper()` below for the pattern used to wrap a turn loop.
- Three independent triggers, each configurable via `Policy`:
    * `job_calls`              -- max model requests per job
    * `job_seconds`            -- max wall-clock seconds per job
    * `identical_request_limit`-- max consecutive identical requests
      (fingerprinted from the last user message + last tool result) with
      no new input and no progress
- Once a `Job` is marked `stopped`, every subsequent request on that job
  --- including ones a scheduler auto-replays --- is rejected with the same
  reason. There is no automatic restart. A fresh human/job id gets fresh
  counters; nothing here blocks a deliberately new request.
- Enforcement point: wraps `httpx.Client.send` / `httpx.AsyncClient.send`
  (the transport almost every Python LLM SDK uses) and filters to a
  configurable set of target hosts, so unrelated traffic (search, other
  providers, tool calls) is untouched.

This module ships framework-agnostic. See `references/hermes-integration.md`
for the exact plugin wiring used to apply it inside Hermes Agent.
"""
from __future__ import annotations
import contextvars, functools, hashlib, json, logging, threading, time, uuid
from dataclasses import dataclass, field
from typing import Callable, Iterable

log = logging.getLogger("runaway_agent_protection")


class Stop(RuntimeError):
    """Raised internally, and surfaced to the caller, when a job must halt."""


@dataclass
class Policy:
    job_calls: int = 100           # max model requests per logical job
    job_seconds: int = 3600        # max wall-clock seconds per logical job
    identical_request_limit: int = 6   # max consecutive identical requests
    target_hosts: tuple[str, ...] = ()  # hostnames this guard intercepts


@dataclass
class Job:
    id: str
    started: float
    lock: object = field(default_factory=threading.Lock)
    calls: int = 0
    fingerprint: str = ""
    streak: int = 0
    stopped: str = ""


CURRENT: "contextvars.ContextVar[Job | None]" = contextvars.ContextVar(
    "runaway_protection_job", default=None
)


def new_job() -> Job:
    return Job(id=uuid.uuid4().hex, started=time.time())


def fingerprint_request_body(
    body: dict, user_role: str = "user", tool_roles: Iterable[str] = ("tool",)
) -> str:
    """Hash the user message + last tool result so a no-progress replay
    (same input, same last observation) is detected regardless of framework
    wire format. Override for frameworks with a different message shape."""
    msgs = body.get("messages", body.get("input", []))
    user = tool = ""
    if isinstance(msgs, str):
        user = msgs
    elif isinstance(msgs, list):
        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get("role") == user_role:
                user = m.get("content", "")
            if m.get("role") in tool_roles or m.get("type") == "function_call_output":
                tool = m.get("content", m.get("output", ""))
    return hashlib.sha256(
        json.dumps([user, tool], sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def admit(job: Job, body: dict, policy: Policy, now: float | None = None) -> None:
    """Raise Stop() if this request must be blocked; otherwise count it.

    Safe to call directly (no HTTP layer required) if you want to gate
    requests yourself instead of using install().
    """
    now = time.time() if now is None else now
    with job.lock:
        if job.stopped:
            raise Stop(job.stopped)  # never silently replay a stopped job
        if now - job.started >= policy.job_seconds:
            job.stopped = (
                f"This job reached its {policy.job_seconds}s wall-clock limit. "
                "Send a new instruction to continue."
            )
            raise Stop(job.stopped)
        if job.calls >= policy.job_calls:
            job.stopped = (
                f"This job reached its {policy.job_calls}-request limit. "
                "Send a new instruction to continue."
            )
            raise Stop(job.stopped)
        fp = fingerprint_request_body(body)
        job.streak = job.streak + 1 if job.fingerprint == fp else 1
        job.fingerprint = fp
        if job.streak > policy.identical_request_limit:
            job.stopped = (
                f"Stopped after {policy.identical_request_limit} consecutive "
                "identical requests with no new user input or tool result."
            )
            raise Stop(job.stopped)
        job.calls += 1


def install(policy: Policy, current: "contextvars.ContextVar[Job | None]" = CURRENT):
    """Patch httpx.Client/AsyncClient.send to enforce `policy` on requests
    to any host in `policy.target_hosts`. Idempotent per-process.

    Requests are attributed to whatever Job is active in `current` for the
    calling context; if none is active, a fresh single-request Job is made
    (so untracked/side calls are still gated, never silently exempted).
    """
    import httpx

    if getattr(httpx.Client, "_runaway_protection_installed", False):
        return
    sync = httpx.Client.send
    asyn = httpx.AsyncClient.send
    hosts = {h.lower() for h in policy.target_hosts}

    def _is_target(request) -> bool:
        try:
            return request.url.host.lower() in hosts
        except Exception:
            return False

    def _prepare(request):
        job = current.get() or new_job()
        try:
            body = json.loads(request.content) if request.content else {}
        except Exception:
            body = {}
        try:
            admit(job, body, policy)
            return None
        except Stop as e:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": "runaway-agent-protection stop: " + str(e),
                        "type": "rate_limit_error",
                        "code": "runaway_protection_stop",
                    }
                },
                headers={"retry-after": "1"},
                request=request,
            )

    @functools.wraps(sync)
    def send(client, request, *a, **kw):
        if not _is_target(request):
            return sync(client, request, *a, **kw)
        blocked = _prepare(request)
        return blocked if blocked is not None else sync(client, request, *a, **kw)

    @functools.wraps(asyn)
    async def asend(client, request, *a, **kw):
        if not _is_target(request):
            return await asyn(client, request, *a, **kw)
        import asyncio
        blocked = await asyncio.to_thread(_prepare, request)
        return blocked if blocked is not None else await asyn(client, request, *a, **kw)

    httpx.Client.send = send
    httpx.AsyncClient.send = asend
    httpx.Client._runaway_protection_installed = True


def example_turn_wrapper(run_turn: Callable, current: "contextvars.ContextVar" = CURRENT):
    """Reference pattern for wiring a framework's turn/run loop so retries
    and auto-continuations inside one turn share a single Job, while a
    genuinely new turn gets a fresh Job and fresh counters.

        wrapped_run_turn = example_turn_wrapper(original_run_turn)

    See references/hermes-integration.md for the concrete Hermes hook.
    """
    @functools.wraps(run_turn)
    def wrapped(*args, **kwargs):
        inherited = current.get()
        job = inherited or new_job()
        token = current.set(job)
        try:
            return run_turn(*args, **kwargs)
        finally:
            current.reset(token)
    return wrapped
