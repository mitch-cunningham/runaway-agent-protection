# Runaway Agent Protection

A portable agent skill and optional Python reference adapter for stopping runaway
model calls while keeping agents useful. Works as integration guidance for Claude,
GPT, Grok, Gemini, Hermes, and custom runtimes; it does not require Hermes.

**Three controls:** per-job request cap, admission deadline, and repeated-input stop.
Normal messages, tools, memory, handoffs, reviews, and scheduled jobs stay available.
There is no added daily budget, model restriction, or human-only approval gate.

## Install the skill

Clone this repository into the skill directory your agent supports, or copy its
contents into a folder named `runaway-agent-protection` there. Keep `SKILL.md`,
`scripts/`, and `references/` together. For a host using `.agents/skills/`:

```sh
git clone https://github.com/mitch-cunningham/runaway-agent-protection.git \
  .agents/skills/runaway-agent-protection
```

Other hosts may use `.claude/skills/`, `~/.hermes/skills/`, or an organization-managed
skill directory. Consult your host's supported skill installation method.

Ask your agent:

> Use runaway-agent-protection to audit our model-call loop and add runtime stops.
> Preserve our existing capabilities, schedules, and budgets. Test without model spend.

**Installing the skill alone does not install runtime enforcement.** The agent needs
configuration/code access to integrate the limits, and must report unsupported hosts.

## Optional Python HTTPX adapter

From this repository, install the dependency and bind a job around the framework run:

```sh
python -m pip install -r requirements.txt
```

```python
from scripts.guard import Policy, install, job_scope, Stop

installation = install(Policy(target_hosts=("api.openai.com", "api.anthropic.com", "api.x.ai")))
with job_scope() as job:  # retries and continuations must keep this same Job
    try:
        agent.run("Your task")  # replace with your framework's actual run method
    except Stop as exc:
        print(str(exc))  # report once; do not automatically retry
```

Use actual provider/proxy hosts. The default matcher covers common OpenAI Responses,
Chat Completions, Anthropic Messages, and Gemini generation POST paths. It leaves
usage checks, auth, files, and unrelated tool traffic alone. Custom paths need a
matcher; non-HTTPX runtimes need a native adapter. Handle an SDK-wrapped exception
as terminal when `job.stopped` is set. Never re-create Job on every dispatch.

- [Skill instructions](SKILL.md)
- [Framework integration and async/scheduler examples](references/framework-integration.md)
- [Optional Hermes integration](references/hermes-integration.md)
- [Tests and limitations](references/testing.md)
- [Companion: Cost-Aware Model Routing](https://github.com/mitch-cunningham/cost-aware-model-routing)

## Coverage and limits

The reference helper is in-process. It cannot persist counters across restarts,
coordinate separate worker processes, prevent an authorized program from removing
it, cancel requests already running, or impose a provider billing cap. A production
multi-worker adapter needs durable accounting and cancellation. Provider budget
controls remain separate. A request count is not a dollar or subscription-credit cap.

Version 2 fixes missing-scope bypasses, adds async turn scopes and common provider
message formats, and raises a local `Stop` instead of fabricating a retryable HTTP
quota error. Update older adapters accordingly; `new_job()` now uses a monotonic clock.

MIT licensed. See [LICENSE](LICENSE).
