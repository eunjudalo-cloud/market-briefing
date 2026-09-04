"""로깅 설정: 파일(logs/run_YYYYMMDD.log) + 콘솔, 비밀값 마스킹.

Windows 콘솔 인코딩(cp949)에서 한글이 깨지지 않도록 스트림을 UTF-8 로 재설정한다.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from .config import LOGS_DIR

_SECRET_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(
        r"(?i)((?:api[_-]?key|apikey|token|secret|password|passwd|pwd|crtfc_key|serviceKey)"
        r"\s*[=:]\s*)([^\s&\"']+)"),
     r"\1***"),
    (re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"), r"\1***"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "***"),
]


def _mask(text: str) -> str:
    for pat, repl in _SECRET_RES:
        text = pat.sub(repl, text)
    return text


class SecretMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:
            return True
        masked = _mask(msg)
        if masked != msg:
            record.msg = masked
            record.args = None
        return True


def setup_logging(target_date: date | None = None) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = (target_date or datetime.now()).strftime("%Y%m%d")
    logfile = LOGS_DIR / f"run_{stamp}.log"

    logger = logging.getLogger("briefing")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    mask = SecretMaskingFilter()

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(mask)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.addFilter(mask)
    try:
        ch.stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    logger.addHandler(ch)

    return logger
