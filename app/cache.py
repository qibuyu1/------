from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any


class TTLCache:
    """Small thread-safe in-memory TTL/LRU cache for upstream responses.

    The app is intentionally dependency-light. This cache avoids repeated external
    API calls for identical searches while keeping cached data isolated from caller
    mutation via deepcopy.
    """

    def __init__(self, *, max_items: int = 128, ttl_seconds: int = 300):
        self.max_items = max(1, int(max_items))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._lock = RLock()
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            item = self._items.get(key)
            if item is None:
                return None
            created, value = item
            if now - created >= self.ttl_seconds:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return deepcopy(value)

    def put(self, key: str, value: Any) -> Any:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            self._items[key] = (now, deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _purge(self, now: float) -> None:
        expired = [key for key, (created, _) in self._items.items() if now - created >= self.ttl_seconds]
        for key in expired:
            self._items.pop(key, None)
