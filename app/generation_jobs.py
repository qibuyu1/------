from __future__ import annotations

"""Small in-process async task queue for write/revise operations.

Long LLM calls must never keep the browser/proxy connection open.  This queue is
also intentionally conservative about eviction: pending/running work is protected
from TTL and capacity trimming so a slow but healthy upstream request cannot be
silently forgotten while it is still executing.
"""

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from typing import Any, Callable


_TERMINAL = {"ready", "error", "cancelled"}


class GenerationJobStore:
    def __init__(self, *, max_items: int = 64, ttl_seconds: int = 60 * 45, workers: int = 4):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="article-task")

    def start(
        self,
        payload: dict[str, Any],
        worker: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        kind: str = "generate",
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = time.time()
        entry = {
            "generationJobId": job_id,
            "kind": str(kind or "generate")[:40],
            "status": "pending",
            "stage": "queued",
            "createdAt": now,
            "updatedAt": now,
            "article": None,
            "articleId": "",
            "error": "",
            "cancelled": False,
        }
        with self._lock:
            self._purge_locked()
            self._items[job_id] = entry
            self._trim_locked()

        safe_payload = deepcopy(payload)
        safe_payload["_generationJobId"] = job_id

        def run() -> None:
            if self._is_cancelled(job_id):
                return
            self._update(job_id, status="running", stage="writing" if kind == "generate" else "revising")
            try:
                article = worker(safe_payload)
            except Exception as exc:
                if self._is_cancelled(job_id):
                    return
                self._update(job_id, status="error", stage="failed", error=str(exc)[:1200])
                return
            if self._is_cancelled(job_id):
                return
            article_id = str((article or {}).get("articleId") or "")
            self._update(job_id, status="ready", stage="done", article=article, articleId=article_id)

        future = self._pool.submit(run)
        with self._lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget_future(job_id))
        return self.get(job_id) or {
            "generationJobId": job_id, "kind": kind, "status": "pending", "stage": "queued"
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(str(job_id or "").strip())
            if not item:
                return None
            out = deepcopy(item)
        now = time.time()
        out["elapsedMs"] = max(0, int((now - float(out.get("createdAt") or now)) * 1000))
        out.pop("cancelled", None)
        return out

    def cancel(self, job_id: str) -> bool:
        key = str(job_id or "").strip()
        future: Future[Any] | None = None
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
            future = self._futures.get(key)
        # Python cannot interrupt an already-running network call, but cancelling a
        # queued future prevents wasted work. Running workers observe the marker and
        # discard their result instead of publishing stale state.
        if future is not None:
            future.cancel()
        return True

    def _forget_future(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def is_cancelled(self, job_id: str) -> bool:
        return self._is_cancelled(str(job_id or "").strip())

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
        expired = [
            key for key, item in self._items.items()
            if str(item.get("status") or "") in _TERMINAL
            and float(item.get("updatedAt") or item.get("createdAt") or 0) < cutoff
        ]
        for key in expired:
            self._items.pop(key, None)
            self._futures.pop(key, None)

    def _trim_locked(self) -> None:
        if len(self._items) <= self.max_items:
            return
        overflow = len(self._items) - self.max_items
        terminal = sorted(
            ((key, item) for key, item in self._items.items() if str(item.get("status") or "") in _TERMINAL),
            key=lambda pair: float(pair[1].get("updatedAt") or pair[1].get("createdAt") or 0),
        )
        for key, _ in terminal[:overflow]:
            self._items.pop(key, None)
            self._futures.pop(key, None)
        # If every entry is actively running, temporarily exceed max_items rather
        # than deleting a live task. It will be trimmed after tasks become terminal.


generation_jobs = GenerationJobStore()
