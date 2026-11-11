# Framework integration

These are adapter patterns, not claims of automatic support for every SDK.

## Python: one run, including retries

Install the HTTPX guard once before creating model clients. Set `target_hosts` to
actual endpoints. Bind `job_scope()` around the outer job, not each request.

For a synchronous or asynchronous turn entry point:

```python
from scripts.guard import Policy, install, example_turn_wrapper
install(Policy(target_hosts=("api.anthropic.com", "api.openai.com", "api.x.ai")))
agent.run_turn = example_turn_wrapper(agent.run_turn)
# The wrapper supports `def` and `async def`. Await async calls normally.
```

The reference hook counts `Client.send` calls, including SDK retries that call it
again. A custom transport/proxy may retry below that hook; disable those retries or
move admission below them before claiming physical-attempt coverage.

Nested calls inherit the same Job. `asyncio.create_task` and `asyncio.to_thread`
propagate context on supported Python versions. A raw thread or executor may not:
use a fresh `contextvars.copy_context()` for each submission, or explicitly carry
the same Job object. Do not reuse one Context concurrently across threads.

## Scheduler, queue, or continuation

```python
from scripts.guard import new_job, job_scope
job = new_job("stable-execution-id")
# Retain this object for this execution, including its retried/queued continuations.
with job_scope(job):
    run_execution()
# A replay uses `job_scope(job)` again, not `new_job(job.id)`.
```

The ID is a label, not a lookup. Use a framework-owned durable row keyed by execution
ID for independent processes/restarts. Atomically check stopped state, deadline,
physical request count, and no-progress state immediately before dispatch. Parent and
child accounting must share the root execution identity. An independent new cron
execution gets fresh counters without a new human approval.

Fail only the affected job. Put the operator-visible notice in the outer supervisor,
deduplicated by job identity; do not emit one alert per rejected retry.

## Claude, GPT, Grok, Gemini, and proxies

The optional HTTPX hook understands common REST paths and the matching message
shapes for Anthropic, OpenAI-compatible APIs, and Gemini. Provider branding does not
guarantee the transport: OAuth desktop clients, websocket streams, gRPC, and proxy
endpoints may be different. Verify interception with a mock transport and inspect
request boundaries. Supply `is_model_request` and/or `fingerprinter` when needed.
Never log request bodies, API keys, bearer tokens, or memory content.

## JavaScript, other runtimes, and opaque hosted agents

Reimplement `admit` at the native model dispatch boundary: atomic job lock/transaction,
stopped latch, deadline, call count, and protocol-aware no-progress fingerprint.
Count retries below the SDK's retry loop. Propagate root identity through async
context and queues. A JavaScript wrapper must not claim this Python HTTPX hook protects it.
If the host exposes no enforceable hook, use documented native limits and explain
remaining gaps. A system prompt alone is advisory, not runtime protection.

## Reviews

Keep reviews enabled when desired. Mark review jobs so they cannot start reviews,
deduplicate each input snapshot, and retain accounting on automatic retries. Avoid
replaying review instructions into the foreground conversation or durable memory.
A model downgrade is not a reset of a stopped job.
