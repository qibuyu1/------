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
                self._items.popitem(last=False)
        return article_id

    def get(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            created, record = item
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def update(self, article_id: str, article: dict[str, Any], *, save_history: bool = True) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            created, record = item
            if save_history:
                history = record.setdefault("history", [])
                history.append(deepcopy(record.get("article") or {}))
                if len(history) > self.max_history:
                    del history[:-self.max_history]
            record["article"] = deepcopy(article)
            self._items[article_id] = (created, record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def undo(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            created, record = item
            history = record.setdefault("history", [])
            if not history:
                return None
            record["article"] = history.pop()
            self._items[article_id] = (created, record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def restore_original(self, article_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return None
            created, record = item
            current = deepcopy(record.get("article") or {})
            original = deepcopy(record.get("original") or {})
            if current != original:
                history = record.setdefault("history", [])
                history.append(current)
                if len(history) > self.max_history:
                    del history[:-self.max_history]
            record["article"] = original
            self._items[article_id] = (created, record)
            self._items.move_to_end(article_id)
            return deepcopy(record)

    def history_depth(self, article_id: str) -> int:
        with self._lock:
            self._purge_locked()
            item = self._items.get(article_id)
            if not item:
                return 0
            return len(item[1].get("history") or [])

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
        "rawContent": str(src.get("rawContent") or src.get("snippet") or "")[:12_000],
        "origin": src.get("origin") or ("upload" if src.get("type") == "upload" else "search"),
        "selectedByUser": bool(src.get("selectedByUser", True)),
    }


article_store = ArticleStore()
