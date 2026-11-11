---
name: runaway-agent-protection
description: Use when an LLM agent needs hard runtime stops against runaway loops.
version: 1.0.0
author: Mitch Cunningham
license: MIT
platforms: [linux, macos, windows]
---

# Runaway Agent Protection

A small, framework-agnostic pattern for stopping an LLM agent that has
gone runaway: too many requests, too much wall-clock time, or stuck
repeating itself with no new input. It enforces at the HTTP transport
layer — below the model's own control flow — so a confused or
compromised agent loop cannot reason its way past the limit.

This exists because prompting alone ("stop after N tries") is not a
runtime guarantee: the LLM is the thing deciding whether to keep going,
so a stuck loop, a buggy auto-continuation, or an adversarial prompt can
all talk past an instruction-only limit. The fix has to sit outside the
model's own judgment.

## When to use this

- You run an agent (Hermes, LangChain, a custom harness, anything built
  on an OpenAI-compatible or httpx-based SDK) unattended — cron jobs,
  background workers, long autonomous tasks.
- You've had, or want to prevent, an incident where a background loop or
  retry mechanism kept firing model requests well past the point anyone
  noticed, burning budget or quota with no new work happening.
- You want a stop that is structural (HTTP-layer), not advisory
  (instructions the model could ignore under load or confusion).

## What it protects against

| Failure mode | Protection |
|---|---|
| A single logical job (one turn + its retries/continuations) runs forever | `job_calls` cap: stop after N model requests |
| A job runs for an unbounded amount of wall-clock time | `job_seconds` cap: stop after N seconds |
| The agent re-issues the same request with no new input or tool result | `identical_request_limit`: stop after N consecutive identical requests |
| A scheduler/retrier auto-resumes a job that was already stopped | Once `Job.stopped` is set, every further request on that job id is rejected with the same reason — no silent auto-restart |

## Quick start

```python
from scripts.guard import Policy, install

install(Policy(
    job_calls=100,
    job_seconds=3600,
    identical_request_limit=6,
    target_hosts=("api.openai.com",),   # or your provider's host
))
```

That's it for the HTTP-layer enforcement. The remaining piece is telling
your framework what counts as "one job" — see below.

## Wiring "one job" into your framework

A job must span every retry and automatic continuation that belongs to
one human-initiated turn, otherwise a framework that silently retries on
failure resets your counters every time and the cap never bites.

The pattern is a `contextvars.ContextVar` that the framework's turn/run
function sets once at the top of a turn and every nested/recursive call
inherits:

```python
from scripts.guard import example_turn_wrapper

original_run_turn = agent.run_turn
agent.run_turn = example_turn_wrapper(original_run_turn)
```

See `references/hermes-integration.md` for the exact hook used to wire
this into Hermes Agent's `TurnFacadeMixin.run_conversation`, and
`references/testing.md` for how to verify the three triggers without
spending real model calls.

## What this does NOT do

- It does not touch your provider's own account-level rate limits or
  billing. It is a local, cheap, structural backstop in front of them.
- It does not decide *why* a job should stop beyond the three triggers
  above — it has no opinion on task correctness.
- It does not auto-resume anything. Recovery is a deliberate decision by
  whoever (or whatever) sends the next instruction — never automatic.
- It is not a replacement for provider-side budget/quota controls; use
  both.

## Scope this skill intentionally does not cover

- Multi-process/multi-host job coordination (this is in-process; for a
  shared ledger across processes, back `Job` with a database row instead
  of an in-memory dataclass — the `admit()` function is pure and easy to
  swap).
- Non-HTTP transports (gRPC, local models called via a different client
  library) — the same `admit()`/`Policy` logic applies, only the
  `install()` interception point changes.

## Files

- `scripts/guard.py` — the guard itself: `Policy`, `Job`, `admit()`,
  `install()`, and `example_turn_wrapper()`.
- `references/hermes-integration.md` — exact plugin wiring for Hermes
  Agent (config, plugin.yaml, where to hook `run_conversation`).
- `references/testing.md` — how to test all three triggers with
  simulated requests, no real model spend.
