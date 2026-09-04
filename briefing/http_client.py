"""공용 HTTP 헬퍼: 타임아웃 + 재시도 + 공통 User-Agent."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger("briefing.http")

DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def make_session(headers: dict[str, str] | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    if headers:
        s.headers.update(headers)
    return s


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retries: int = DEFAULT_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
    backoff: float = 1.5,
    sleep=time.sleep,
    **kwargs: Any,
) -> requests.Response:
    """성공 시 Response, 모든 재시도 실패 시 마지막 예외를 raise."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            # 4xx(요청 자체 오류)는 재시도해도 동일 → 즉시 중단
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                log.warning("요청 실패(재시도 안 함) %s %s: %s", method, url, exc)
                break
            if attempt < retries:
                wait = backoff ** attempt
                log.warning(
                    "요청 실패(%d/%d) %s %s: %s — %.1fs 후 재시도",
                    attempt, retries, method, url, exc, wait,
                )
                sleep(wait)
            else:
                log.warning(
                    "요청 최종 실패(%d/%d) %s %s: %s",
                    attempt, retries, method, url, exc,
                )
    assert last_exc is not None
    raise last_exc
