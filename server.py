from __future__ import annotations

import json
import mimetypes
import re
import traceback
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from app.article_store import article_store
from app.config import settings
from app.deepseek import available as deepseek_available
from app.serper_images import available as serper_images_available
from app.exporter import ExportError, export_article, validate_export_bytes
from app.file_ingest import FileIngestError, ingest_file
from app.image_fetch import ImageFetchError, fetch_image
from app.home_feed import home_feed
from app.pipeline import generate_article, research, restore_original, revise_article, undo_revision
from app.tavily import available as tavily_available

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"


class Handler(BaseHTTPRequestHandler):
    server_version = "DataElementGovernance/30.0"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "service": "数据要素治理",
                    "version": "30.0",
                    "tavilyConfigured": tavily_available(),
                    "deepseekConfigured": deepseek_available(),
                    "deepseekModel": settings.deepseek_model if deepseek_available() else None,
                    "serperImagesConfigured": serper_images_available(),
                    "codeVisualsAvailable": True,
                    "serperSearchFallbackConfigured": bool(settings.serper_api_key),
                    "imageSearchProvider": "serper" if serper_images_available() else None,
                    "strictSources": True,
                    "exports": ["docx", "pdf"],
                    "uploads": ["txt", "md", "csv", "json", "html", "docx", "pdf"],
                    "revision": True,
                }
            )
            return
        if path == "/api/home-feed":
            self._json(home_feed())
            return
        if path == "/api/image":
            self._proxy_image(parsed.query)
            return
        if path.startswith("/api/article/"):
            article_id = path.rsplit("/", 1)[-1].strip()
            record = article_store.get(article_id)
            if not record:
                self._json({"error": "文章草稿已过期，请重新生成。"}, status=410)
            else:
                article = record.get("article") or {}
                article["articleId"] = article_id
                article["historyDepth"] = article_store.history_depth(article_id)
                self._json(article)
            return
        if path == "/api/export":
            self._export_query(parsed.query, head_only=False)
            return
        if path.startswith("/api/"):
            self._json({"error": "API route not found"}, status=404)
            return
        self._static(path)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/export":
            try:
                self._export_query(parsed.query, head_only=True)
            except Exception as exc:
                self._json({"error": str(exc)[:300]}, status=422)
            return
        if not parsed.path.startswith("/api/"):
            self._static(parsed.path, head_only=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/upload":
                self._upload()
                return
            payload = self._read_json()
            if path == "/api/search":
                self._json(research(payload))
                return
            if path == "/api/generate":
                self._json(generate_article(payload))
                return
            if path == "/api/revise":
                self._json(revise_article(payload))
                return
            if path == "/api/article/undo":
                self._json(undo_revision(str(payload.get("articleId") or "")))
                return
            if path == "/api/article/restore":
                self._json(restore_original(str(payload.get("articleId") or "")))
                return
            if path == "/api/export":
                self._export(payload)
                return
            self._json({"error": "API route not found"}, status=404)
        except (ValueError, FileIngestError) as exc:
            self._json({"error": str(exc)}, status=400)
        except ExportError as exc:
            self._json({"error": str(exc)}, status=422)
        except RuntimeError as exc:
            self._json({"error": str(exc)}, status=422)
        except Exception as exc:
            if settings.app_env == "development":
                traceback.print_exc()
            self._json({"error": "Server error", "detail": str(exc)[:500]}, status=500)

    def _upload(self) -> None:
        content_type = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("上传接口需要 multipart/form-data")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("没有收到文件")
        if length > 13 * 1024 * 1024:
            raise ValueError("单个文件不能超过 12MB")
        body = self.rfile.read(length)
        pseudo_message = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        message = BytesParser(policy=email_policy).parsebytes(pseudo_message)
        file_part = None
        if message.is_multipart():
            for part in message.iter_parts():
                if part.get_param("name", header="content-disposition") == "file":
                    file_part = part
                    break
        if file_part is None:
            raise ValueError("没有找到上传文件字段")
        filename = file_part.get_filename() or "上传资料.txt"
        data = file_part.get_payload(decode=True) or b""
        source = ingest_file(filename, data, file_part.get_content_type())
        self._json({"source": source})

    def _export_query(self, query_string: str, *, head_only: bool = False) -> None:
        params = parse_qs(query_string)
        self._export({
            "articleId": (params.get("articleId") or [""])[0],
            "format": (params.get("format") or [""])[0],
            "title": (params.get("title") or [""])[0],
        }, head_only=head_only)

    def _export(self, payload: dict, *, head_only: bool = False) -> None:
        article_id = str(payload.get("articleId") or "").strip()
        fmt = str(payload.get("format") or "").lower().strip()
        if not article_id:
            raise ValueError("articleId is required")
        record = article_store.get(article_id)
        if not record:
            self._json({"error": "文章草稿已过期，请重新生成后导出。"}, status=410)
            return
        title_override = str(payload.get("title") or "").strip()[:140]
        if title_override:
            current = list((record.get("article") or {}).get("titleCandidates") or [])
            record["article"]["titleCandidates"] = [title_override, *[x for x in current if x != title_override]]
        body, filename, content_type = export_article(record, fmt)
        expect_images = bool((record.get("article") or {}).get("images") or (record.get("article") or {}).get("coverImage"))
        # Missing external images must never block a valid document export or cause
        # fake placeholders. The exporter skips unavailable images; structure checks
        # remain strict, while media presence is reported rather than making download impossible.
        validate_export_bytes(body, fmt, expect_images=False)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        base_ascii = re.sub(r"[^A-Za-z0-9._-]+", "-", filename.rsplit(".", 1)[0]).strip(".-") or "data-elements-governance"
        safe_ascii = f"{base_ascii}.{fmt}"
        self.send_header("Content-Disposition", "attachment; filename=\"{}\"; filename*=UTF-8\'\'{}".format(safe_ascii, quote(filename)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _proxy_image(self, query_string: str) -> None:
        params = parse_qs(query_string)
        url = (params.get("url") or [""])[0]
        if not url:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing image URL")
            return
        try:
            image = fetch_image(url)
        except ImageFetchError as exc:
            self.send_error(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        except Exception:
            self.send_error(HTTPStatus.BAD_GATEWAY, "Image could not be loaded")
            return
        self.send_response(200)
        self.send_header("Content-Type", image.content_type)
        self.send_header("Content-Length", str(len(image.data)))
        self.send_header("Cache-Control", "public, max-age=1800")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(image.data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4_000_000:
            raise ValueError("Request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _json(self, data: dict, *, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, request_path: str, *, head_only: bool = False) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.exists() or not candidate.is_file():
            candidate = WEB_ROOT / "index.html"
        data = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(candidate.name)
        self.send_response(200)
        self.send_header("Content-Type", (mime or "application/octet-stream") + ("; charset=utf-8" if (mime or "").startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        if candidate.suffix.lower() in {".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp"}:
            self.send_header("Cache-Control", "public, max-age=300")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer((settings.host, settings.port), Handler)
    print("\n数据要素治理 V30.0")
    print(f"  http://{settings.host}:{settings.port}")
    print(f"  Tavily: {'configured' if tavily_available() else 'NOT CONFIGURED'}")
    print(f"  DeepSeek: {settings.deepseek_model if deepseek_available() else 'NOT CONFIGURED (generation disabled)'}")
    print(f"  Image System: local code visuals + {'Serper / Google Images' if serper_images_available() else 'no Serper'}")
    print("  Upload: TXT / MD / CSV / JSON / HTML / DOCX / PDF")
    print("  Export: Word / PDF · revision history enabled")
    print("  Search: Tavily primary · compact multi-query · semantic family match · optional Serper fallback")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
