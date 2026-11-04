"""Reliable Relay: deterministic, local-only workflow demonstration."""
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path


class ValidationError(ValueError):
    pass


class ConflictError(ValueError):
    pass


def validate(payload):
    if not isinstance(payload, dict):
        raise ValidationError("Expected a JSON object.")
    allowed = {"event_id", "name", "email", "company", "simulate_failures"}
    if set(payload) - allowed:
        raise ValidationError("Unknown payload fields are not accepted.")
    result = {}
    for key, maximum in (("event_id", 80), ("name", 100), ("email", 200), ("company", 100)):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise ValidationError(f"{key} must be a non-empty string of at most {maximum} characters.")
        result[key] = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", result["event_id"]):
        raise ValidationError("event_id may contain letters, numbers, underscores and hyphens.")
    result["email"] = result["email"].lower()
    # Synthetic-only input intentionally prevents accidentally entering real leads.
    if not re.fullmatch(r"[a-z0-9._+-]+@example\.(com|org|net)", result["email"]):
        raise ValidationError("Use a synthetic email at example.com, example.org or example.net.")
    failures = payload.get("simulate_failures", 0)
    if isinstance(failures, bool) or not isinstance(failures, int) or not 0 <= failures <= 10:
        raise ValidationError("simulate_failures must be an integer between 0 and 10.")
    result["simulate_failures"] = failures
    return result


class RelayStore:
    MAX_ATTEMPTS = 3
    BACKOFF_SECONDS = (0.6, 1.2)

    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                total_attempts INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL DEFAULT 0, replay_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, next_at REAL,
                last_error TEXT, crm_id TEXT
            );
            CREATE TABLE IF NOT EXISTS crm (
                event_id TEXT PRIMARY KEY REFERENCES events(event_id), crm_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, email TEXT NOT NULL, company TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(event_id),
                action TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def _log(self, event_id, action, detail, now):
        self.db.execute("INSERT INTO audit(event_id,action,detail,at) VALUES(?,?,?,?)",
                        (event_id, action, detail, now))

    def ingest(self, payload, now=None):
        data = validate(payload)  # Validation happens before any write.
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now = time.time() if now is None else now
        with self.lock, self.db:
            old = self.db.execute("SELECT * FROM events WHERE event_id=?", (data["event_id"],)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ConflictError("This event ID already belongs to a different payload.")
                self.db.execute("UPDATE events SET duplicate_count=duplicate_count+1 WHERE event_id=?", (data["event_id"],))
                self._log(data["event_id"], "duplicate_ignored", "Same event ID and payload. No new queue item or CRM write.", now)
                return {"event_id": data["event_id"], "duplicate": True, "status": old["status"]}
            self.db.execute("""INSERT INTO events(event_id,payload,fingerprint,status,failures,created_at,updated_at,next_at)
                VALUES(?,?,?,'queued',?,?,?,?)""", (data["event_id"], encoded, fingerprint, data["simulate_failures"], now, now, now))
            self._log(data["event_id"], "accepted", "Validated synthetic lead; persisted to SQLite queue.", now)
            return {"event_id": data["event_id"], "duplicate": False, "status": "queued"}

    def tick(self, now=None):
        now = time.time() if now is None else now
        processed = 0
        with self.lock, self.db:
            ready = self.db.execute("""SELECT * FROM events WHERE status IN ('queued','retrying')
                AND next_at <= ? ORDER BY created_at LIMIT 20""", (now,)).fetchall()
            for row in ready:
                event_id = row["event_id"]
                attempt = row["attempts"] + 1
                self._log(event_id, "attempt_started", f"Delivery attempt {attempt}/{self.MAX_ATTEMPTS} to the in-process mock CRM.", now)
                if attempt <= row["failures"]:
                    exhausted = attempt >= self.MAX_ATTEMPTS
                    status = "dead_letter" if exhausted else "retrying"
                    delay = None if exhausted else self.BACKOFF_SECONDS[attempt - 1]
                    self.db.execute("""UPDATE events SET status=?,attempts=?,total_attempts=total_attempts+1,
                        updated_at=?,next_at=?,last_error=? WHERE event_id=?""",
                        (status, attempt, now, None if exhausted else now + delay, "Simulated CRM 503: service unavailable", event_id))
                    self._log(event_id, "dead_letter" if exhausted else "retry_scheduled",
                              "Retry limit reached. Awaiting manual replay." if exhausted else f"Simulated 503. Retry scheduled in {delay:.1f}s.", now)
                else:
                    data = json.loads(row["payload"])
                    crm_id = "CRM-" + hashlib.sha256(event_id.encode()).hexdigest()[:8].upper()
                    self.db.execute("INSERT OR IGNORE INTO crm VALUES(?,?,?,?,?,?)",
                                    (event_id, crm_id, data["name"], data["email"], data["company"], now))
                    self.db.execute("""UPDATE events SET status='delivered',attempts=?,total_attempts=total_attempts+1,
                        updated_at=?,next_at=NULL,last_error=NULL,crm_id=? WHERE event_id=?""", (attempt, now, crm_id, event_id))
                    self._log(event_id, "delivered", f"Stored {crm_id} in local mock CRM. No network request was made.", now)
                processed += 1
        return processed

    def replay(self, event_id, now=None):
        now = time.time() if now is None else now
        with self.lock, self.db:
            row = self.db.execute("SELECT status FROM events WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                raise KeyError(event_id)
            if row["status"] != "dead_letter":
                raise ConflictError("Only dead-letter events can be replayed.")
            # This is an explicit demo action: remove the simulated sink outage.
            self.db.execute("""UPDATE events SET status='queued',attempts=0,failures=0,replay_count=replay_count+1,
                updated_at=?,next_at=?,last_error=NULL WHERE event_id=?""", (now, now, event_id))
            self._log(event_id, "replayed", "Operator cleared the simulated outage and requeued this event. Original payload and history retained.", now)
            return {"event_id": event_id, "status": "queued"}

    def snapshot(self):
        with self.lock:
            events = [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY created_at DESC")]
            for row in events:
                row["payload"] = json.loads(row["payload"])
            audit = [dict(row) for row in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 600")]
            crm = [dict(row) for row in self.db.execute("SELECT * FROM crm ORDER BY created_at DESC")]
        return {"events": events, "audit": audit, "crm": crm,
                "summary": {"received": len(events), "delivered": sum(e["status"] == "delivered" for e in events),
                            "active": sum(e["status"] in ("queued", "retrying") for e in events),
                            "dead_letter": sum(e["status"] == "dead_letter" for e in events),
                            "duplicates": sum(e["duplicate_count"] for e in events), "crm_records": len(crm)}}

    def reset(self):
        with self.lock, self.db:
            self.db.execute("DELETE FROM audit")
            self.db.execute("DELETE FROM crm")
            self.db.execute("DELETE FROM events")
