import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from relay import ConflictError, RelayStore, ValidationError


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = RelayStore(self.path)
        self.lead = {"event_id": "evt_test", "name": "Demo Person", "email": "demo@example.com", "company": "Sample Studio", "simulate_failures": 0}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_success_persists_exactly_one_crm_record(self):
        self.store.ingest(self.lead, now=100)
        self.store.tick(now=100)
        self.store.tick(now=101)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["summary"]["crm_records"], 1)
        self.assertEqual(snapshot["events"][0]["attempts"], 1)

    def test_duplicate_before_and_after_delivery_has_no_extra_sink_write(self):
        self.store.ingest(self.lead, now=100)
        self.assertTrue(self.store.ingest(self.lead, now=100)["duplicate"])
        self.store.tick(now=100)
        self.store.ingest(self.lead, now=101)
        self.store.tick(now=101)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["summary"]["received"], 1)
        self.assertEqual(snapshot["summary"]["duplicates"], 2)
        self.assertEqual(snapshot["summary"]["crm_records"], 1)

    def test_concurrent_duplicate_ingestion_stays_one_event(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.ingest(self.lead, now=100), range(20)))
        self.assertEqual(sum(not x["duplicate"] for x in results), 1)
        self.store.tick(now=100)
        self.assertEqual(self.store.snapshot()["summary"]["crm_records"], 1)

    def test_conflicting_payload_is_rejected_without_mutation(self):
        self.store.ingest(self.lead, now=100)
        before = self.store.snapshot()
        with self.assertRaises(ConflictError):
            self.store.ingest({**self.lead, "company": "Changed"}, now=101)
        self.assertEqual(before, self.store.snapshot())

    def test_invalid_payload_has_no_side_effects(self):
        for change in ({"email": "real@business.com"}, {"name": ""}, {"simulate_failures": True}, {"simulate_failures": -1}, {"unexpected": "x"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.store.ingest({**self.lead, **change}, now=100)
        self.assertEqual(self.store.snapshot(), {"events": [], "audit": [], "crm": [], "summary": {"received": 0, "delivered": 0, "active": 0, "dead_letter": 0, "duplicates": 0, "crm_records": 0}})

    def test_transient_failure_obeys_backoff_then_succeeds(self):
        self.store.ingest({**self.lead, "simulate_failures": 1}, now=100)
        self.store.tick(now=100)
        self.assertEqual(self.store.tick(now=100.5), 0)
        self.store.tick(now=100.6)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["events"][0]["status"], "delivered")
        self.assertEqual(snapshot["events"][0]["attempts"], 2)
        self.assertEqual(snapshot["summary"]["crm_records"], 1)

    def test_retry_limit_moves_to_dead_letter_and_stops(self):
        self.store.ingest({**self.lead, "simulate_failures": 10}, now=100)
        for moment in (100, 101, 103, 110, 1000):
            self.store.tick(now=moment)
        event = self.store.snapshot()["events"][0]
        self.assertEqual((event["status"], event["attempts"]), ("dead_letter", 3))
        self.assertEqual(self.store.snapshot()["summary"]["crm_records"], 0)

    def test_manual_replay_preserves_history_and_delivers_once(self):
        self.store.ingest({**self.lead, "simulate_failures": 10}, now=100)
        for moment in (100, 101, 103):
            self.store.tick(now=moment)
        self.store.replay("evt_test", now=105)
        self.store.tick(now=105)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["events"][0]["total_attempts"], 4)
        self.assertEqual(snapshot["events"][0]["replay_count"], 1)
        self.assertEqual(snapshot["summary"]["crm_records"], 1)
        self.assertIn("dead_letter", [row["action"] for row in snapshot["audit"]])
        with self.assertRaises(ConflictError):
            self.store.replay("evt_test", now=106)

    def test_queued_event_survives_connection_restart(self):
        self.store.ingest(self.lead, now=100)
        self.store.close()
        self.store = RelayStore(self.path)
        self.store.tick(now=101)
        self.assertEqual(self.store.snapshot()["summary"]["crm_records"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
