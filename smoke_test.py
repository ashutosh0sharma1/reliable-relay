"""Exercise the running loopback demo, then leave four clean synthetic scenarios."""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8807"


def request(path, payload=None, method="GET", raw=None, headers=None):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_terminal(event_id):
    for _ in range(35):
        _, state = request("/api/state")
        event = next(e for e in state["events"] if e["event_id"] == event_id)
        if event["status"] in ("delivered", "dead_letter"):
            return event
        time.sleep(0.1)
    raise AssertionError("Worker failed to reach a terminal state.")


assert request("/health")[1]["mode"] == "local_synthetic_demo"
request("/api/reset", method="POST")
before = request("/api/state")[1]
assert request("/api/events", method="POST", raw=b"not-json")[0] == 422
assert request("/api/events", {"event_id": "bad"}, "POST")[0] == 422
assert request("/api/events", method="POST", raw=b"x" * 8193)[0] == 413
assert request("/api/reset", method="POST", headers={"Origin": "https://foreign.example"})[0] == 403
assert request("/api/state")[1] == before
print("PASS: invalid JSON, incomplete payload and oversized payload rejected without writes; foreign-origin mutation blocked.")

payload = {"event_id": "http_test", "name": "Demo Person", "email": "demo@example.com", "company": "Example Studio"}
assert request("/api/events", payload, "POST")[0] == 201
assert request("/api/events", payload, "POST")[0] == 200
assert request("/api/events", {**payload, "company": "Conflict"}, "POST")[0] == 409
assert wait_terminal("http_test")["status"] == "delivered"
assert request("/api/state")[1]["summary"]["crm_records"] == 1
assert request("/api/events/http_test/replay", method="POST")[0] == 409
print("PASS: HTTP create/duplicate/conflict semantics; one CRM row; delivered event cannot replay.")

dead_id = request("/api/scenarios/dead_letter", method="POST")[1]["event_id"]
assert wait_terminal(dead_id)["attempts"] == 3
assert request(f"/api/events/{dead_id}/replay", method="POST")[0] == 200
replayed = wait_terminal(dead_id)
assert replayed["status"] == "delivered" and replayed["total_attempts"] == 4
assert request("/api/events/missing/replay", method="POST")[0] == 404
print("PASS: real background retry timing, capped attempts, dead-letter replay and missing-event response.")

request("/api/reset", method="POST")
ids = [request(f"/api/scenarios/{kind}", method="POST")[1]["event_id"] for kind in ("success", "duplicate", "retry", "dead_letter")]
for event_id in ids:
    wait_terminal(event_id)
summary = request("/api/state")[1]["summary"]
assert summary == {"received": 4, "delivered": 3, "active": 0, "dead_letter": 1, "duplicates": 1, "crm_records": 3}
assert request("/api/evidence")[0] == 200
print("PASS: clean demo fixture left in place:", json.dumps(summary))
print("HTTP smoke checks complete. No external CRM or network service was contacted.")
