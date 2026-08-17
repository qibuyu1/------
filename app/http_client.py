from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class UpstreamError(RuntimeError):
    pass


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | list[Any] | None = None,
    timeout: int = 30,
    retries: int = 2,
    retry_statuses: tuple[int, ...] = (408, 425, 429, 500, 502, 503, 504),
) -> dict[str, Any]:
    """Small resilient JSON client for upstream APIs.

    Search providers occasionally return transient 429/5xx errors or reset a
    connection. Retrying a couple of times makes the UI much less brittle while
    still failing quickly for authentication/validation errors.
    """
    body = None
    merged_headers = {
        "User-Agent": "DataElementGovernance/9.0",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    if headers:
        merged_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    attempts = max(1, int(retries) + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=body, headers=merged_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:1200]
            last_error = UpstreamError(f"Upstream HTTP {exc.code}: {details}")
            if exc.code not in retry_statuses or attempt >= attempts - 1:
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = UpstreamError(f"Upstream connection failed: {exc.reason}")
            if attempt >= attempts - 1:
                raise last_error from exc
        except TimeoutError as exc:
            last_error = UpstreamError("Upstream request timed out")
            if attempt >= attempts - 1:
                raise last_error from exc
        except json.JSONDecodeError as exc:
            raise UpstreamError("Upstream returned invalid JSON") from exc

        # Short jittered exponential backoff. Search should feel responsive even
        # when a provider has a brief hiccup.
        time.sleep(min(2.8, (0.45 * (2**attempt)) + random.random() * 0.18))

    raise UpstreamError(str(last_error or "Upstream request failed"))


def with_query(url: str, params: dict[str, Any]) -> str:
    cleaned = {k: v for k, v in params.items() if v not in (None, "", [], {})}
    return f"{url}?{urllib.parse.urlencode(cleaned, doseq=True)}"
