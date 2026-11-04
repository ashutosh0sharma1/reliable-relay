import asyncio
import contextlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from relay import ConflictError, RelayStore, ValidationError

ROOT = Path(__file__).resolve().parent
store = RelayStore(ROOT / "relay-demo.sqlite3")


def scenario(kind):
    if kind not in ("success", "duplicate", "retry", "dead_letter"):
        raise ValidationError("Unknown scenario.")
    names = {"success": ("Avery Brooks", "Northstar Studio"), "duplicate": ("Jordan Lee", "Canvas Works"),
             "retry": ("Morgan Patel", "Harbor Creative"), "dead_letter": ("Riley Chen", "Fieldnotes Lab")}
    name, company = names[kind]
    event_id = "evt_" + uuid.uuid4().hex[:10]
    payload = {"event_id": event_id, "name": name, "company": company,
               "email": name.split()[0].lower() + "@example.com",
               "simulate_failures": {"success": 0, "duplicate": 0, "retry": 1, "dead_letter": 10}[kind]}
    result = store.ingest(payload)
    if kind == "duplicate":
        store.ingest(payload)
    return result


async def worker():
    while True:
        store.tick()
        await asyncio.sleep(0.15)


@asynccontextmanager
async def lifespan(app):
    if not store.snapshot()["events"]:
        for kind in ("success", "duplicate", "retry", "dead_letter"):
            scenario(kind)
    task = asyncio.create_task(worker())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    store.close()


app = FastAPI(title="Reliable Relay · local portfolio demo", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
              description="Synthetic-only lead ingestion and SQLite delivery queue. CRM is an in-process simulation. No external API calls.")


@app.middleware("http")
async def local_write_guard(request: Request, call_next):
    # Prevent a foreign webpage from issuing cross-origin mutations to this local demo.
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in ("http://127.0.0.1:8807", "http://localhost:8807"):
        return JSONResponse({"detail": "Local demo requests only."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValidationError)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(ConflictError)
async def conflict(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
def health():
    store.snapshot()
    return {"status": "ok", "mode": "local_synthetic_demo", "sink": "in_process_mock_crm", "database": "SQLite", "max_attempts": 3}


@app.get("/docs", include_in_schema=False)
def docs():
    return FileResponse(ROOT / "static" / "api.html")


@app.get("/api/state")
def state():
    return store.snapshot()


@app.post("/api/events", status_code=201, openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
    "type": "object", "required": ["event_id", "name", "email", "company"], "additionalProperties": False,
    "properties": {"event_id": {"type": "string", "example": "evt_example_001"}, "name": {"type": "string", "example": "Demo Person"},
                   "email": {"type": "string", "example": "demo@example.com"}, "company": {"type": "string", "example": "Example Studio"},
                   "simulate_failures": {"type": "integer", "minimum": 0, "maximum": 10, "default": 0}}
}}}}})
async def ingest(request: Request):
    body = await request.body()
    if len(body) > 8192:
        raise HTTPException(413, "Demo payload limit is 8 KiB.")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValidationError("Expected valid JSON.")
    result = store.ingest(payload)
    return JSONResponse(result, status_code=200 if result["duplicate"] else 201)


@app.post("/api/scenarios/{kind}", status_code=201)
def create_scenario(kind: str):
    return scenario(kind)


@app.post("/api/events/{event_id}/replay")
def replay(event_id: str):
    try:
        return store.replay(event_id)
    except KeyError:
        raise HTTPException(404, "Event not found.")


@app.post("/api/reset")
def reset():
    store.reset()
    return {"reset": True}


@app.get("/api/evidence")
def evidence():
    return {"project": "Reliable Relay", "disclosure": "Self-initiated portfolio demo. Synthetic data and a simulated CRM; no production deployment or customer metrics.",
            "implementation": {"runtime": "Python / FastAPI", "persistence": "SQLite WAL", "idempotency": "event_id plus canonical payload SHA-256",
                               "retry": "3 attempts, 0.6s then 1.2s backoff", "delivery_boundary": "Mock CRM insert and queue state commit atomically in one SQLite transaction"},
            **store.snapshot()}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
