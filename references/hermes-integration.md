# Hermes Agent integration

How to wire `scripts/guard.py` into Hermes Agent specifically, as a
profile-scoped plugin.

## 1. Plugin files

Hermes plugins live under `~/.hermes/plugins/<name>/` (or
`<profile>/plugins/<name>/` for a profile-scoped install). Two files:

`plugins/runaway-agent-protection/plugin.yaml`:

```yaml
name: runaway-agent-protection
version: 1.0.0
description: Runtime runaway protection (job calls/time/identical-repeat stop).
```

`plugins/runaway-agent-protection/__init__.py`:

```python
import importlib.util, sys
from pathlib import Path

def register(ctx):
    name = 'runaway_agent_protection_guard'
    g = sys.modules.get(name)
    if g is None:
        s = importlib.util.spec_from_file_location(
            name, Path(__file__).parent / 'guard.py'
        )
        g = importlib.util.module_from_spec(s)
        sys.modules[name] = g
        s.loader.exec_module(g)
    from scripts.guard import Policy
    g.install(Policy(
        job_calls=100,
        job_seconds=3600,
        identical_request_limit=6,
        target_hosts=("your-provider-host.example.com",),
    ))
```

Copy `scripts/guard.py` alongside `__init__.py`, or reference it from a
shared location — either works since `install()` only needs importing.

## 2. Enable the plugin

```bash
hermes config set plugins.enabled '["runaway-agent-protection"]'
```

(Append to the existing list rather than overwrite if other plugins are
already enabled.)

## 3. Hook the turn loop so retries/continuations share one Job

Hermes's turn loop lives in `agent.turn_facade.TurnFacadeMixin.run_conversation`.
Wrap it the same way `example_turn_wrapper()` does, inside `guard.py`'s
`install()` or a follow-up call:

```python
def _wrap_turn(current):
    from agent.turn_facade import TurnFacadeMixin
    if getattr(TurnFacadeMixin, '_runaway_protection_wrapped', False):
        return
    original = TurnFacadeMixin.run_conversation

    def run(agent, *args, **kwargs):
        inherited = current.get()
        job = inherited or new_job()
        token = current.set(job)
        try:
            return original(agent, *args, **kwargs)
        finally:
            current.reset(token)

    TurnFacadeMixin.run_conversation = run
    TurnFacadeMixin._runaway_protection_wrapped = True
```

Call `_wrap_turn(CURRENT)` once from your plugin's `register()`, after
`install()`.

### Scoping to one provider/profile

If you only want this guard on a specific provider's requests (e.g. one
model provider used by one profile, not every provider in a multi-agent
Hermes deployment), filter in the wrapper:

```python
def run(agent, *args, **kwargs):
    if str(getattr(agent, 'provider', '')) != 'your-provider-name':
        return original(agent, *args, **kwargs)
    ...
```

This keeps the guard's blast radius to exactly the traffic you intend —
important if other profiles/providers already have their own separate
budget controls and you don't want two guards fighting over the same
job accounting.

## 4. Background review / other auxiliary tasks

If your Hermes profile also runs automatic background reviews
(`auxiliary.background_review`), that is a *separate* concern from this
guard — disable it natively via config, don't try to make this guard
do it:

```yaml
auxiliary:
  background_review:
    enabled: false   # false = skip automatic post-turn forks; manual /refine still works
```

## 5. Existing per-profile protections

Do not install two guards against the same provider/host in the same
profile — their job/streak counters will race and double-count. If a
profile already has a protection plugin for a different provider (e.g.
a separate subscription-budget guard for a different model backend),
this plugin should filter to its own `target_hosts` / provider name so
the two never overlap.
