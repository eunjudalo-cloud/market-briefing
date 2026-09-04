"""설정 로딩: .env(Settings) + config/feeds.yaml.

민감값은 여기서만 읽고, 로그/스냅샷/메일로 새어나가지 않게 한다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _dir(env_name: str, default: Path) -> Path:
    """환경변수로 경로를 덮어쓸 수 있게 한다 (CI/배포에서 사용)."""
    v = os.environ.get(env_name)
    return Path(v).expanduser().resolve() if v else default


# 산출물/스냅샷/로그 경로 — BRIEFING_OUTPUT_DIR 등으로 재정의 가능
OUTPUT_DIR = _dir("BRIEFING_OUTPUT_DIR", PROJECT_ROOT / "output")
DATA_DIR = _dir("BRIEFING_DATA_DIR", PROJECT_ROOT / "data")
LOGS_DIR = _dir("BRIEFING_LOGS_DIR", PROJECT_ROOT / "logs")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # OPENDART
    opendart_api_key: str = ""

    # 공공데이터포털 (선택)
    data_go_kr_key: str | None = None

    # 이메일(SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    mail_from: str = ""
    mail_to: str = ""

    # 기타
    briefing_tz: str = "Asia/Seoul"
    news_max_items: int = 15


class FeedConfig(BaseModel):
    name: str
    url: str


class FeedsFile(BaseModel):
    feeds: list[FeedConfig] = []
    max_items: int = 15


def load_feeds(path: Path | None = None) -> FeedsFile:
    path = path or (CONFIG_DIR / "feeds.yaml")
    if not path.exists():
        return FeedsFile()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return FeedsFile.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
