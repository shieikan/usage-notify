from __future__ import annotations

import json
import urllib.error
import urllib.request

from .store import payload_json


class UploadError(RuntimeError):
    pass


def upload_events(
    api_base_url: str,
    token: str,
    events: list[dict[str, object]],
    timeout_seconds: float,
) -> tuple[list[str], list[str]]:
    if not events:
        return [], []

    url = api_base_url.rstrip("/") + "/v1/usage-events"
    request = urllib.request.Request(
        url,
        data=payload_json(events),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "usage-notify/0.1",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise UploadError(f"HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise UploadError(str(error.reason)) from error

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise UploadError("upload API returned invalid JSON") from error

    accepted = [str(item) for item in parsed.get("accepted", [])]
    duplicates = [str(item) for item in parsed.get("duplicates", [])]
    return accepted, duplicates
