# Optional Hermes integration

Hermes is one adapter target. The core skill and guard do not depend on it.
Confirm installed Hermes APIs before implementing this example; internals can change.

1. Back up the relevant profile configuration and plugin directory.
2. Copy `scripts/guard.py` into your plugin directory. Load it once under a canonical
   module name; use that same module for Policy, CURRENT, Job, and wrappers.
3. Install its HTTPX hook with the real inference hosts for the intended profiles.
4. Bind the native turn entry point (`TurnFacadeMixin.run_conversation` in versions
   exposing it) with `example_turn_wrapper`. Explicitly propagate the Job through
   queued turns and thread workers. Native turn wrappers alone do not solve queue,
   restart, or cross-process replay; add durable execution accounting where needed.
5. Preserve existing enabled plugins, budgets, credentials, schedules, and models.
   Do not replace `plugins.enabled` with a list containing only this plugin.
6. Treat a stopped Job as final in the gateway supervisor, outside SDK retries.
7. Restart and verify all actual model-hosting processes: gateway(s), dashboard,
   desktop/web backend, and workers. Test as the service account, not just root.

An installation sketch inside your plugin's `register(ctx)`:

```python
# g is your single imported guard module, including its shared CURRENT ContextVar.
from agent.turn_facade import TurnFacadeMixin

g.install(g.Policy(target_hosts=("your-actual-provider-host.example",)))
if not getattr(TurnFacadeMixin, '_runaway_guard_wrapped', False):
    TurnFacadeMixin.run_conversation = g.example_turn_wrapper(TurnFacadeMixin.run_conversation)
    TurnFacadeMixin._runaway_guard_wrapped = True
```

This is only the in-process skeleton, not a complete production Hermes plugin.
Adapt native cancellation, durable queued execution IDs, review deduplication, and
service initialization for your version before declaring full coverage.

## Keep background reviews useful

Do not disable automatic reviews as the default runaway remedy. Preserve them,
prevent review-on-review recursion, deduplicate snapshots, and count actual attempts.
Where supported, `auxiliary.background_review.provider` and `.model` can select a
separate review runtime. Discover available models and verify actual fork routing;
do not paste a historical model ID or silently fall back to an expensive main model.
See the companion cost-aware routing skill for that separate workflow.
