import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from usage_notify.cloudflare_api import request_daily_report


class CloudflareApiTest(unittest.TestCase):
    def test_request_daily_report(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                received["path"] = self.path
                received["authorization"] = self.headers.get("Authorization")
                received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                body = json.dumps({"content": "report content"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            content = request_daily_report(url, "admin-secret", "2026-05-07", True, 5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(content, "report content")
        self.assertEqual(received["path"], "/v1/reports/daily")
        self.assertEqual(received["authorization"], "Bearer admin-secret")
        self.assertEqual(received["body"], {"date": "2026-05-07", "send_discord": True})


if __name__ == "__main__":
    unittest.main()
