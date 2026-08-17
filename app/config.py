from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """Small .env loader; document/export libraries are installed separately."""
    env_path = path or (Path(__file__).resolve().parents[1] / ".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


@dataclass(frozen=True)
class Settings:
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "").strip()
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    serper_api_key: str = os.getenv("SERPER_API_KEY", "").strip()
    host: str = os.getenv("HOST", "127.0.0.1").strip()
    port: int = int(os.getenv("PORT", "8787"))
    app_env: str = os.getenv("APP_ENV", "development").strip()


settings = Settings()
