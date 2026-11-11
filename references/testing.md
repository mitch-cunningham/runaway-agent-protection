# Test without model spend

From the repository root, with Python 3.10+:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The suite uses HTTPX MockTransport and local state; no API key or live model call
is required. It checks exact call/time/repeat boundaries, permanent stopped state,
fresh jobs, nested and async scopes, concurrency, changed tool observations across
OpenAI/Claude/Gemini shapes, inference-only HTTP matching, missing-scope rejection,
and sync/async HTTP interception before a request leaves the process.

Before production rollout also test your framework's actual dispatch path, scheduled
executions, raw thread workers, SDK retries, and queued replays. The reference helper
cannot prove protection for a framework or transport it never intercepts. Verify
multi-process/restart replay against your durable adapter; these are not implemented
by this in-memory example. Confirm normal unrelated jobs continue after one stops.
