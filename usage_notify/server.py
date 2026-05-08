from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

from .server_store import client_id_for_token, connect_server, store_usage_events, upsert_clients


def run_server(host: str, port: int, db_path: str, token_map: dict[str, str]) -> None:
    class Handler(UsageNotifyHandler):
        server_db_path = db_path
        server_token_map = token_map

    with connect_server(db_path) as connection:
        upsert_clients(connection, token_map)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"serving usage API on http://{host}:{port}")
    server.serve_forever()


class UsageNotifyHandler(BaseHTTPRequestHandler):
    server_db_path: str
    server_token_map: dict[str, str]

    def do_POST(self) -> None:
        if self.path != "/v1/usage-events":
            self._json_response(404, {"error": "not found"})
            return

        token = self._bearer_token()
        if token is None:
            self._json_response(401, {"error": "missing bearer token"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._json_response(400, {"error": "invalid json"})
            return

        events = payload.get("events")
        if not isinstance(events, list):
            self._json_response(400, {"error": "events must be an array"})
            return

        with connect_server(self.server_db_path) as connection:
            upsert_clients(connection, self.server_token_map)
            client_id = client_id_for_token(connection, token)
            if client_id is None:
                self._json_response(403, {"error": "invalid token"})
                return
            result = store_usage_events(connection, client_id, events)

        status = 202 if not result["rejected"] else 207
        self._json_response(status, result)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _bearer_token(self) -> str | None:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            return None
        return authorization[len(prefix) :]

    def _json_response(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

