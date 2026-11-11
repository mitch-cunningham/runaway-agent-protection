# runaway-agent-protection

A small, framework-agnostic Hermes/LLM-agent skill: hard runtime stops
against runaway agent loops.

Agent frameworks can run away in three ways that prompting alone can't
reliably stop, because the model is the thing deciding whether to keep
going:

1. A single logical job (one turn + every retry/auto-continuation
   chained to it) keeps calling the model indefinitely.
2. The agent gets stuck re-issuing the same request with no new input
   and no new tool result.
3. A scheduler or retry mechanism auto-resumes a job that already hit a
   limit, silently repeating the failure.

This enforces three structural stops at the HTTP transport layer — below
the model/framework's own control flow — so a confused or compromised
loop cannot reason its way past the limit:

- **Call cap** — stop a job after N model requests (default 100)
- **Time cap** — stop a job after N seconds wall-clock (default 3600)
- **Identical-repeat cap** — stop after N consecutive requests with no
  new user input or tool result (default 6)

Once a job stops, it stays stopped. Nothing auto-restarts it.

## Quick start

```python
from scripts.guard import Policy, install

install(Policy(
    job_calls=100,
    job_seconds=3600,
    identical_request_limit=6,
    target_hosts=("api.your-provider.com",),
))
```

See [`SKILL.md`](./SKILL.md) for the full write-up, how to wire "one
job" into your framework's turn loop, and what this intentionally does
not cover. See [`references/hermes-integration.md`](./references/hermes-integration.md)
for exact Hermes Agent plugin wiring, and
[`references/testing.md`](./references/testing.md) for how to verify
all three triggers with simulated requests — no real model spend
required.

## Why this exists

Built after an incident where a background review/continuation loop
kept firing model requests overnight with no new work happening,
exhausting a subscription before anyone noticed. The fix had to be
structural (can't be talked past by the model) rather than another
instruction the model could ignore under load.

## License

MIT — see [LICENSE](./LICENSE).
