import unittest
import tempfile

from usage_notify.collector import collect_turn_from_log, parse_codex_usage_line


class CollectorTest(unittest.TestCase):
    def test_parse_codex_usage_line(self):
        line = (
            "2026-05-06T10:56:53.008725Z  INFO "
            "session_loop{thread_id=019dfced}:submission_dispatch{}:"
            "turn{otel.name=\"session_task.turn\" thread.id=019dfced-28da "
            "turn.id=019dfcee-cac7 model=gpt-5.5 "
            "codex.turn.token_usage.input_tokens=22268 "
            "codex.turn.token_usage.cached_input_tokens=7552 "
            "codex.turn.token_usage.output_tokens=462 "
            "codex.turn.token_usage.reasoning_output_tokens=87 "
            "codex.turn.token_usage.total_tokens=22730}: close"
        )

        event = parse_codex_usage_line(line, "client-a")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.client_id, "client-a")
        self.assertEqual(event.session_id, "019dfced-28da")
        self.assertEqual(event.turn_id, "019dfcee-cac7")
        self.assertEqual(event.model, "gpt-5.5")
        self.assertEqual(event.input_tokens, 22268)
        self.assertEqual(event.cached_input_tokens, 7552)
        self.assertEqual(event.output_tokens, 462)
        self.assertEqual(event.reasoning_output_tokens, 87)
        self.assertEqual(event.total_tokens, 22730)

    def test_parse_ignores_non_usage_line(self):
        event = parse_codex_usage_line("2026-05-06T00:00:00Z INFO no usage here", "client")
        self.assertIsNone(event)

    def test_parse_ignores_usage_fragment_without_timestamp(self):
        event = parse_codex_usage_line("+codex.turn.token_usage.input_tokens=10", "client")
        self.assertIsNone(event)

    def test_collect_turn_from_log(self):
        line = (
            "2026-05-06T10:56:53.008725Z  INFO "
            "turn{thread.id=session-1 turn.id=turn-1 model=gpt-5.5 "
            "codex.turn.token_usage.input_tokens=100 "
            "codex.turn.token_usage.output_tokens=25 "
            "codex.turn.token_usage.total_tokens=125}: close\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/codex-tui.log"
            with open(path, "w", encoding="utf-8") as file:
                file.write(line)

            event = collect_turn_from_log(path, "client-1", "turn-1")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.total_tokens, 125)
        self.assertEqual(event.client_id, "client-1")


if __name__ == "__main__":
    unittest.main()
