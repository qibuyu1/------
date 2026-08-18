from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from typing import Any


class ArticleStore:
    def __init__(self, *, max_items: int = 64, ttl_seconds: int = 60 * 60 * 4, max_history: int = 10):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self.max_history = max_history
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def put(self, article: dict[str, Any], *, sources: list[dict[str, Any]], query: str) -> str:
        article_id = uuid.uuid4().hex
        clean_article = deepcopy(article)
        record = {
            "article": clean_article,
            "original": deepcopy(clean_article),
            "history": [],
            "sources": [_export_source(x) for x in sources[:16]],
            "query": query,
        }
        with self._lock:
            self._purge_locked()
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            while len(self._items) > self.max_items:
                removable = next((key for key, (_, rec) in self._items.items() if str((rec.get("article") or {}).get("visualStatus") or "ready") != "pending"), None)
                if removable is None:
                    break
                self._items.pop(removable, None)
        return article_id

    def get(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            _touched, record = item
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def update(self, article_id: str, article: dict[str, Any], *, save_history: bool = True) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            _touched, record = item
            if save_history:
                history = record.setdefault("history", [])
                history.append(deepcopy(record.get("article") or {}))
                if len(history) > self.max_history:
                    del history[:-self.max_history]
            record["article"] = deepcopy(article)
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return deepcopy(record)


    def update_runtime_fields(
        self, article_id: str, fields: dict[str, Any], *,
        expected_visual_token: str | None = None, expected_content_version: int | None = None,
        sync_original: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically merge background/runtime fields without creating revision history.

        Visual work is asynchronous. A worker must never replace the entire article
        snapshot it started from because the user may have revised/undone the draft
        meanwhile. Token/version guards make the merge compare-and-swap-like.
        """
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            _touched, record = item
            current = record.get("article") or {}
            if expected_visual_token is not None and str(current.get("visualJobToken") or "") != str(expected_visual_token):
                return None
            if expected_content_version is not None and int(current.get("contentVersion") or 0) != int(expected_content_version):
                return None
            current = deepcopy(current)
            current.update(deepcopy(fields))
            record["article"] = current
            if sync_original:
                original = deepcopy(record.get("original") or {})
                # Only runtime visual fields are synced to the immutable first-draft
                # snapshot; prose/title/source facts remain exactly as first written.
                for key in ("visualJobToken", "visualStatus", "visualReport", "visuals", "coverImage", "images", "blocks", "warnings"):
                    if key in fields:
                        original[key] = deepcopy(fields[key])
                record["original"] = original
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def update_sources(self, article_id: str, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Replace the persisted revision evidence without touching article history."""
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            _touched, record = item
            record["sources"] = [_export_source(x) for x in sources[:16]]
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def undo(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            _touched, record = item
            history = record.setdefault("history", [])
            if not history:
                return None
            record["article"] = history.pop()
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def restore_original(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            _touched, record = item
            current = deepcopy(record.get("article") or {})
            original = deepcopy(record.get("original") or {})
            def semantic(value: dict[str, Any]) -> dict[str, Any]:
                cleaned = deepcopy(value)
                for key in ("visualJobToken", "visualError"):
                    cleaned.pop(key, None)
                return cleaned
            if semantic(current) != semantic(original):
                history = record.setdefault("history", [])
                history.append(current)
                if len(history) > self.max_history:
                    del history[:-self.max_history]
            record["article"] = original
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def history_depth(self, article_id: str) -> int:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return 0
            _touched, record = item
            self._items[article_id] = (time.time(), record)
            self._items.move_to_end(article_id)
            return len(record.get("history") or [])

    def _purge_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [key for key, (created, _) in self._items.items() if created < cutoff]
        for key in expired:
            self._items.pop(key, None)


def _export_source(src: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(src.get("id") or "")[:220],
        "type": src.get("type"),
        "title": str(src.get("title") or "")[:500],
        "source": str(src.get("source") or "")[:240],
        "publishedAt": src.get("publishedAt"),
        "url": str(src.get("url") or "")[:3000],
        "authors": list(src.get("authors") or [])[:8],
        "citations": src.get("citations"),
        "readCount": src.get("readCount"),
        "snippet": str(src.get("snippet") or "")[:1500],
        # Uploaded reports are primary editorial material and often place decisive
        # evidence in later pages. Keep a larger revision snapshot than web snippets.
        "rawContent": str(src.get("rawContent") or src.get("snippet") or "")[:(24_000 if (src.get("type") == "upload" or src.get("origin") == "upload") else 14_000)],
        "verifiedDescription": str(src.get("verifiedDescription") or "")[:3000],
        "sourceImages": [str(x)[:3000] for x in (src.get("sourceImages") or []) if str(x).startswith(("http://", "https://"))][:12],
        "sourceVerified": bool(src.get("sourceVerified")),
        "sourceUsable": bool(src.get("sourceUsable") or src.get("sourceVerified")),
        "sourceStatus": str(src.get("sourceStatus") or "")[:120],
        "sourceConfidence": str(src.get("sourceConfidence") or "")[:80],
        "origin": src.get("origin") or ("upload" if src.get("type") == "upload" else "search"),
        "selectedByUser": bool(src.get("selectedByUser", True)),
    }


article_store = ArticleStore()
