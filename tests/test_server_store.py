import unittest
import tempfile

from usage_notify.server_store import (
    client_id_for_token,
    connect_server,
    store_usage_events,
    upsert_clients,
)


class ServerStoreTest(unittest.TestCase):
    def test_store_usage_events_accepts_and_deduplicates_by_client(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = f"{directory}/server.db"
            event = {
                "event_id": "event-1",
                "client_id": "client-1",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "timestamp": "2026-05-06T10:00:00Z",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "model": "gpt-5.5",
                "source": "codex-tui-log",
                "schema_version": 1,
            }

            with connect_server(db_path) as connection:
                upsert_clients(connection, {"client-1": "secret-token"})
                self.assertEqual(client_id_for_token(connection, "secret-token"), "client-1")
                first = store_usage_events(connection, "client-1", [event])
                second = store_usage_events(connection, "client-1", [event])

            self.assertEqual(first["accepted"], ["event-1"])
            self.assertEqual(first["duplicates"], [])
            self.assertEqual(second["accepted"], [])
            self.assertEqual(second["duplicates"], ["event-1"])

    def test_store_usage_events_rejects_client_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = f"{directory}/server.db"
            event = {
                "event_id": "event-1",
                "client_id": "client-2",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "timestamp": "2026-05-06T10:00:00Z",
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "source": "codex-tui-log",
                "schema_version": 1,
            }

            with connect_server(db_path) as connection:
                result = store_usage_events(connection, "client-1", [event])

            self.assertEqual(result["accepted"], [])
            self.assertEqual(result["duplicates"], [])
            self.assertEqual(result["rejected"][0]["error"], "client_id does not match token")


if __name__ == "__main__":
    unittest.main()

