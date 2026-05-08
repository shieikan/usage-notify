from __future__ import annotations

import json
import urllib.error
import urllib.request


class CloudflareApiError(RuntimeError):
    pass


def request_daily_report(
    api_base_url: str,
    admin_token: str,
    report_date: str | None,
    send_discord: bool,
    timeout_seconds: float,
) -> str:
    url = api_base_url.rstrip("/") + "/v1/reports/daily"
    payload: dict[str, object] = {"send_discord": send_discord}
    if report_date:
        payload["date"] = report_date
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
            "User-Agent": "usage-notify/0.1",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise CloudflareApiError(f"HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise CloudflareApiError(str(error.reason)) from error

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as error:
        raise CloudflareApiError("Cloudflare report API returned invalid JSON") from error

    content = parsed.get("content")
    if not isinstance(content, str):
        raise CloudflareApiError("Cloudflare report API response did not include content")
    return content

