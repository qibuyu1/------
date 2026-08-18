from __future__ import annotations

"""Small persistent archive of generated articles.

The live ArticleStore is intentionally in-memory and short-lived because it contains
runtime image data and revision state.  History has a different job: let the editor
find and read past drafts even after a server restart.  We therefore persist a compact
text-first snapshot and deliberately strip large data-URI images.
"""

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any


class HistoryStore:
    def __init__(self, *, max_items: int = 80, ttl_seconds: int = 60 * 60 * 24 * 30, path: Path | None = None):
        root = Path(__file__).resolve().parents[1]
        self.path = path or Path(os.getenv("DEG_HISTORY_FILE", str(root / "data" / "article_history.json")))
        self.max_items = max(10, int(max_items))
        self.ttl_seconds = max(60 * 60 * 24, int(ttl_seconds))
        self._lock = threading.RLock()

    def record(self, article: dict[str, Any], *, query: str, article_id: str = "") -> str:
        now = time.time()
        history_id = str(article.get("historyRecordId") or "").strip() or uuid.uuid4().hex
        item = {
            "historyId": history_id,
            "articleId": str(article_id or article.get("articleId") or "")[:120],
            "query": str(query or "")[:240],
            "title": str(article.get("recommendedTitle") or (article.get("titleCandidates") or [""])[0] or query)[:180],
            "createdAt": float(article.get("createdAt") or now),
            "updatedAt": now,
            "snapshot": _compact_article(article),
        }
        with self._lock:
            rows = self._load_locked()
            old = next((x for x in rows if x.get("historyId") == history_id), None)
            if old:
                item["createdAt"] = float(old.get("createdAt") or item["createdAt"])
            rows = [x for x in rows if x.get("historyId") != history_id]
            rows.insert(0, item)
            self._save_locked(self._prune(rows))
        return history_id

    def update_by_article_id(self, article_id: str, article: dict[str, Any], *, query: str = "") -> str:
        key = str(article_id or "").strip()
        with self._lock:
            rows = self._load_locked()
            hit = next((x for x in rows if str(x.get("articleId") or "") == key), None)
        if hit:
            copy = dict(article)
            copy["historyRecordId"] = hit.get("historyId")
            return self.record(copy, query=query or str(hit.get("query") or ""), article_id=key)
        return self.record(article, query=query, article_id=key)

    def list(self, *, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._prune(self._load_locked())
            self._save_locked(rows)
        return [
            {k: deepcopy(row.get(k)) for k in ("historyId", "articleId", "query", "title", "createdAt", "updatedAt")}
            for row in rows[: max(1, min(100, int(limit)))]
        ]

    def get(self, history_id: str) -> dict[str, Any] | None:
        key = str(history_id or "").strip()
        with self._lock:
            rows = self._prune(self._load_locked())
            hit = next((x for x in rows if str(x.get("historyId") or "") == key), None)
        if not hit:
            return None
        article = deepcopy(hit.get("snapshot") or {})
        article["historyRecordId"] = key
        article["articleId"] = str(hit.get("articleId") or article.get("articleId") or "")
        article["archived"] = True
        article["archivedAt"] = hit.get("updatedAt")
        article.setdefault("recommendedTitle", hit.get("title") or hit.get("query") or "历史稿件")
        article.setdefault("titleCandidates", [article["recommendedTitle"]])
        return article

    def _prune(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cutoff = time.time() - self.ttl_seconds
        clean = [x for x in rows if float(x.get("updatedAt") or x.get("createdAt") or 0) >= cutoff]
        clean.sort(key=lambda x: float(x.get("updatedAt") or 0), reverse=True)
        return clean[: self.max_items]

    def _load_locked(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    def _save_locked(self, rows: list[dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            # History is a convenience feature; it must never make article generation fail.
            return


def _safe_image(image: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(image, dict):
        return None
    out = {k: deepcopy(v) for k, v in image.items() if k not in {"bytes", "data"}}
    url = str(out.get("url") or "")
    if url.startswith("data:image/"):
        out["url"] = ""
        out["archivedImageOmitted"] = True
    return out


def _compact_article(article: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "recommendedTitle", "titleCandidates", "deck", "markdown", "socialSummary", "keyClaims",
        "riskNotes", "sourceNotes", "sourceList", "sourceCount", "model", "generationMeta", "visualReport",
        "historyDepth", "contentVersion", "understoodBrief", "understoodBriefPlan", "warnings", "qualityMode", "query",
    }
    out = {k: deepcopy(article.get(k)) for k in keep if k in article}
    cover = _safe_image(article.get("coverImage"))
    if cover:
        out["coverImage"] = cover
    visuals = []
    for visual in article.get("visuals") or []:
        if not isinstance(visual, dict):
            continue
        item = {k: deepcopy(v) for k, v in visual.items() if k != "image"}
        item["image"] = _safe_image(visual.get("image"))
        visuals.append(item)
    out["visuals"] = visuals[:9]
    # Rebuild blocks client-side when images are omitted. This avoids storing several
    # megabytes of base64 PNG per historical article.
    out["blocks"] = []
    out["images"] = []
    out["visualStatus"] = "ready"
    return out


history_store = HistoryStore()
