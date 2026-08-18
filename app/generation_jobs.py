from __future__ import annotations

"""Small in-process async generation queue.

The public POST /api/generate request must return quickly even when DeepSeek or
source hydration takes longer than a reverse proxy's request timeout.  The heavy
work runs in a worker thread; the browser polls a lightweight status endpoint.

This module deliberately stores only transient generation state. Final articles
continue to live in article_store, so the existing revise/export flows are
unchanged.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any, Callable


class GenerationJobStore:
    def __init__(self, *, max_items: int = 48, ttl_seconds: int = 60 * 30, workers: int = 2):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="article-generation")

    def start(self, payload: dict[str, Any], worker: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = time.time()
        entry = {
            "generationJobId": job_id,
            "status": "pending",
            "stage": "queued",
            "createdAt": now,
            "updatedAt": now,
            "article": None,
            "error": "",
            "cancelled": False,
        }
        with self._lock:
            self._purge_locked()
            self._items[job_id] = entry
            self._trim_locked()

        safe_payload = deepcopy(payload)

        def run() -> None:
            if self._is_cancelled(job_id):
                return
            self._update(job_id, status="running", stage="writing")
            try:
                article = worker(safe_payload)
            except Exception as exc:
                if self._is_cancelled(job_id):
                    return
                self._update(job_id, status="error", stage="failed", error=str(exc)[:800])
                return
            if self._is_cancelled(job_id):
                return
            self._update(job_id, status="ready", stage="done", article=article)

        self._pool.submit(run)
        return self.get(job_id) or {"generationJobId": job_id, "status": "pending", "stage": "queued"}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(str(job_id or "").strip())
            if not item:
                return None
            out = deepcopy(item)
        now = time.time()
        out["elapsedMs"] = max(0, int((now - float(out.get("createdAt") or now)) * 1000))
        # Internal cancellation marker is not useful to the browser.
        out.pop("cancelled", None)
        return out

    def cancel(self, job_id: str) -> bool:
        key = str(job_id or "").strip()
        with self._lock:
            self._purge_locked()
            item = self._items.get(key)
            if not item:
                return False
            item["cancelled"] = True
            item["status"] = "cancelled"
            item["stage"] = "cancelled"
            item["updatedAt"] = time.time()
            item["article"] = None
            return True

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            item = self._items.get(job_id)
            return not item or bool(item.get("cancelled"))

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            item = self._items.get(job_id)
            if not item or item.get("cancelled"):
                return
            item.update(fields)
            item["updatedAt"] = time.time()

    def _purge_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [key for key, item in self._items.items() if float(item.get("createdAt") or 0) < cutoff]
        for key in expired:
            self._items.pop(key, None)

    def _trim_locked(self) -> None:
        if len(self._items) <= self.max_items:
            return
        ordered = sorted(self._items.items(), key=lambda pair: float(pair[1].get("createdAt") or 0))
        for key, _ in ordered[: max(0, len(self._items) - self.max_items)]:
            self._items.pop(key, None)


generation_jobs = GenerationJobStore()
