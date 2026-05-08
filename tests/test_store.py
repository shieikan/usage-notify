import tempfile
import unittest

from usage_notify.collector import UsageEvent
from usage_notify.store import connect, insert_events, pending_events, status_counts


class StoreTest(unittest.TestCase):
    def test_insert_events_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = f"{directory}/events.db"
            event = UsageEvent(
                event_id="event-1",
                client_id="client-1",
                session_id="session-1",
                turn_id="turn-1",
                occurred_at="2026-05-06T10:00:00Z",
                input_tokens=10,
                cached_input_tokens=4,
                output_tokens=5,
                reasoning_output_tokens=2,
                total_tokens=15,
                model="gpt-5.5",
                source="codex-tui-log",
                schema_version=1,
            )

            with connect(db_path) as connection:
                self.assertEqual(insert_events(connection, [event]), 1)
                self.assertEqual(insert_events(connection, [event]), 0)
                self.assertEqual(status_counts(connection), {"pending": 1})
                pending = pending_events(connection, 10)

            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["event_id"], "event-1")
            self.assertEqual(pending[0]["client_id"], "client-1")
            self.assertEqual(pending[0]["cached_input_tokens"], 4)
            self.assertEqual(pending[0]["reasoning_output_tokens"], 2)


if __name__ == "__main__":
    unittest.main()
