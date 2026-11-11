---
name: runaway-agent-protection
description: Add or audit runtime protection against runaway AI agent calls, retry storms, repeated background reviews, or unattended token drains. Use for Claude, GPT, Grok, Gemini, Hermes, or custom agent frameworks while preserving normal messages, tools, memory, collaboration, and scheduled jobs.
license: MIT
metadata:
  compatibility: Instructions apply to any agent. The optional Python HTTPX adapter requires Python 3.10+ and httpx; other runtimes need their own dispatch adapter.
  version: "2.0.0"
  author: Mitch Cunningham
---

# Runaway Agent Protection

Keep the agent useful. Stop the runaway job rather than disabling the organization.
A downloaded skill provides instructions; actual protection requires a host runtime
hook or native framework limit. Do not claim protection is installed until tested.

## 1. Inspect before changing

Identify the framework, actual provider or proxy endpoints, request dispatch boundary,
retry behavior, scheduler, background workers, and existing protections. Preserve the
user's models, credentials, budgets, schedules, memory, and permissions unless the
request specifically changes them. Keep useful existing limits; avoid duplicate guards.

## 2. Account for one logical job

A job includes its physical model attempts, SDK retries, automatic continuations,
and related child work. A scheduled execution is already authorized by its schedule;
it does not need another human approval. Give each new scheduled execution a new job.
A retry, replay, or resumed execution of the same job retains its original counters.

Use a stable execution identity and shared accounting. The included in-process
helper uses the same `Job` object and a `ContextVar`. An ID string alone does not
restore state. Queued retries, process restarts, multiple workers, and multiple
machines require a durable shared ledger in the framework adapter. Report that
limitation if you cannot add it; do not silently reset counters.

## 3. Apply three limits at the model-call boundary

Suggested starting values, adjustable to real workloads:

- 100 physical model attempts per logical job, including SDK retries.
- A one-hour admission deadline: reject subsequent calls after that time.
- Six consecutive calls with the same user input and latest tool observations.
  Allow six; block the seventh before it reaches the provider. Changed tool
  observations count as progress. Assistant prose or request IDs alone do not.

Use a lock or transaction so parallel children cannot race past the call count.
The deadline does not cancel an already-running request or tool: configure SDK
request timeouts and host cancellation separately if needed.

## 4. Stop once, then wait

On a limit, latch the job as stopped, cancel or discard its queued continuations,
and report the reason once. Treat the stop as final outside SDK/framework retries;
never turn it into a provider sign-in error, quota exhaustion, or automatic retry.
Do not auto-restart, change job IDs, swap models, or use another credential to evade it.
An explicitly new instruction can start a new job. Unrelated jobs continue normally.

No-progress detection is a heuristic. Customize it for the framework's tool protocol
and test legitimate iteration; do not pretend it measures task success.

## 5. Preserve background learning

Automatic reviews and memory maintenance can remain enabled. Prevent reviews from
spawning further reviews, and deduplicate reviews of the same conversation snapshot.
Put their real provider attempts under job accounting too. Do not disable all reviews,
block all bot messages, or add human-only admission as a substitute for loop control.
Lower-cost review routing is separate; see the companion
[cost-aware-model-routing skill](https://github.com/mitch-cunningham/cost-aware-model-routing).

## 6. Integrate, test, and report

Read [framework integration](references/framework-integration.md) for portable sync,
async, scheduler, thread, and non-Python patterns. Use
[scripts/guard.py](scripts/guard.py) only for supported HTTPX transports; install it
with explicit provider hosts AND a logical job scope. Untagged model calls are rejected
rather than receiving a new counter per request. Unknown transports need a native adapter.
Hermes users can additionally read [the Hermes guide](references/hermes-integration.md).

Run [the offline checks](references/testing.md) before deployment. Verify normal work,
a new cron execution, changing tool results, exact stop boundaries, parallel requests,
and stopped-job replay. Restart every long-lived process that hosts model clients,
including web or desktop backends, and verify the actual service account can write
its ledger. Avoid real model calls except a small bounded canary when necessary.

Report briefly: changed scope, enforced limits, tested paths, unsupported adapters,
and rollback location. Never invent dollar costs or subscription percentages from
request counts or output tokens. No prompt-only or in-process mechanism is a billing
guarantee or a security boundary against code with permission to remove the guard.
