# Testing the guard without spending real model calls

All three triggers can be verified directly against `admit()` and, for
the HTTP interception layer, against a stubbed `httpx` request — no
network calls, no provider spend.

## 1. Unit-test the policy logic directly

```python
import time
from scripts.guard import Policy, Job, new_job, admit, Stop

policy = Policy(job_calls=100, job_seconds=3600, identical_request_limit=6,
                 target_hosts=("example.com",))

def body(msg="hi", tool=""):
    return {"messages": [{"role": "user", "content": msg},
                          {"role": "tool", "content": tool}]}

# --- call cap ---
job = new_job()
allowed = 0
try:
    for i in range(150):
        admit(job, body(f"m{i}"), policy)
        allowed += 1
except Stop as e:
    print("stopped at", allowed + 1, "->", e)
assert allowed == 100

# stopped jobs never silently resume
try:
    admit(job, body("after"), policy)
    raise AssertionError("should have stayed stopped")
except Stop:
    pass

# --- wall-clock cap ---
job2 = new_job()
job2.started = time.time() - 3601
try:
    admit(job2, body("x"), policy)
    raise AssertionError("should have stopped on time limit")
except Stop:
    pass

# --- identical-request cap ---
job3 = new_job()
allowed3 = 0
try:
    for i in range(10):
        admit(job3, body("same", "same"), policy)
        allowed3 += 1
except Stop:
    pass
assert allowed3 == 6

# --- distinct calls never false-trigger ---
job4 = new_job()
for i in range(20):
    admit(job4, body(f"distinct-{i}", f"tool-{i}"), policy)
print("all assertions passed")
```

## 2. Test the HTTP interception layer with a fake request

This confirms `install()` actually wraps `httpx.Client.send` and returns
HTTP 429 once a job is exhausted — without any real network call,
because the test drains the budget via `admit()` directly first and only
checks the *next* request is blocked.

```python
import json, httpx
from scripts.guard import Policy, install, CURRENT, new_job, admit

policy = Policy(job_calls=100, job_seconds=3600, identical_request_limit=6,
                 target_hosts=("example.com",))
install(policy)

class FakeURL:
    def __init__(self, host): self.host = host

class FakeRequest:
    def __init__(self, host, body):
        self.url = FakeURL(host)
        self.content = json.dumps(body).encode()
        self.method = "POST"

client = httpx.Client()
job = new_job()
token = CURRENT.set(job)
try:
    for i in range(100):
        admit(job, {"messages": [{"role": "user", "content": f"x{i}"}]}, policy)

    req = FakeRequest("example.com", {"messages": [{"role": "user", "content": "x100"}]})
    resp = client.send(req)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "runaway_protection_stop"
    print("HTTP-level block confirmed")

    # non-target host must pass through untouched
    other = FakeRequest("other-host.example.com", {"messages": []})
    # (calling client.send(other) would hit the real network in this fake
    #  setup since 'other' bypasses the guard by design; just confirm the
    #  host filter decision instead)
finally:
    CURRENT.reset(token)
```

## 3. What "passed" means before you trust this in production

- All three triggers fire at exactly their configured threshold, not
  one-off (off-by-one is the most common bug here — test the boundary,
  not just "eventually stops").
- A stopped job stays stopped on the next call (no silent resume).
- A fresh `Job` (new id) is never affected by a previous job's state —
  confirms a genuinely new human turn isn't penalized for history it
  didn't cause.
- Legitimate iteration (20 distinct calls in the test above) never
  false-triggers the identical-request guard.

Only after these pass should the guard be wired into a real agent's
turn loop (see `hermes-integration.md`) and watched against one real,
deliberately-triggered scenario before being trusted unattended.
