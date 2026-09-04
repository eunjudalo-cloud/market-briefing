"""경제지 RSS 헤드라인 수집.

- config/feeds.yaml 의 매체별 피드를 feedparser 로 파싱
- 수집 창(window_start ~ window_end) 내 발행분만
- 저장 필드: title / url / source / published_at  (본문·요약은 저장하지 않음 — 저작권)
- 제목 정규화 후 중복 제거, 시장·종목 키워드 포함 항목 우선 정렬, 최대 max_items
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone

import feedparser

from ..config import FeedConfig
from ..http_client import make_session, request_with_retry
from ..models import NewsHeadline

log = logging.getLogger("briefing.rss")

# 일부 매체(예: 한국경제)가 데스크톱 Chrome UA 를 403 으로 차단 → 피드 리더용 UA 사용
FEED_UA = "Mozilla/5.0 (compatible; MarketBriefingBot/1.0; +https://example.invalid/bot)"

PRIORITY_KEYWORDS = (
    "코스피", "코스닥", "증시", "지수", "실적", "어닝", "공급계약", "수주", "계약",
    "증자", "감자", "최대주주", "인수", "합병", "외국인", "기관", "순매수", "순매도",
    "공시", "상장", "IPO", "배당", "자사주",
)

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^0-9A-Za-z가-힣]+")


def _normalize_title(title: str) -> str:
    return _PUNCT_RE.sub("", _WS_RE.sub("", title)).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}


def _similar(a: str, b: str, threshold: float = 0.82) -> bool:
    """정규화 제목이 사실상 같은 기사인지 판정.

    - 문자 bigram Jaccard >= threshold (표현만 다른 재작성), 또는
    - 짧은 쪽(14자 이상)이 긴 쪽에 그대로 포함 (속보 → 종합 확장판)
    """
    short, long = sorted((a, b), key=len)
    if len(short) >= 14 and short in long:
        return True
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return a == b
    inter = len(ga & gb)
    return inter / (len(ga) + len(gb) - inter) >= threshold


def _entry_datetime(entry) -> datetime | None:
    # feedparser 의 *_parsed 는 UTC struct_time → calendar.timegm 으로 epoch 변환
    # (time.mktime 은 로컬 시각으로 해석해 UTC offset 만큼 어긋남)
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return None


def _priority_score(title: str) -> int:
    return sum(1 for kw in PRIORITY_KEYWORDS if kw in title)


def _parse_feed(source: str, url: str, session) -> list[tuple[NewsHeadline, datetime | None]]:
    try:
        resp = request_with_retry(session, "GET", url, retries=2, timeout=12, backoff=1.2)
    except Exception as exc:  # noqa: BLE001
        log.warning("RSS 요청 실패 [%s] %s: %s", source, url, exc)
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        log.warning("RSS 파싱 실패 [%s]: %r", source, getattr(parsed, "bozo_exception", None))
        return []

    out: list[tuple[NewsHeadline, datetime | None]] = []
    for e in parsed.entries:
        title = (e.get("title") or "").strip()
        link = (e.get("link") or "").strip()
        if not title or not link:
            continue
        dt = _entry_datetime(e)
        out.append((
            NewsHeadline(title=title, url=link, source=source, published_at=dt),
            dt,
        ))
    return out


def collect(
    feeds: list[FeedConfig],
    window_start: datetime,
    window_end: datetime,
    max_items: int,
) -> list[NewsHeadline]:
    session = make_session({
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "User-Agent": FEED_UA,
    })

    ws = window_start.astimezone(timezone.utc)
    we = window_end.astimezone(timezone.utc)

    collected: list[tuple[NewsHeadline, datetime | None]] = []
    for feed in feeds:
        rows = _parse_feed(feed.name, feed.url, session)
        log.info("RSS [%s] 원본 %d건", feed.name, len(rows))
        collected.extend(rows)

    # 수집 창 필터 (발행시각이 없으면 보수적으로 제외)
    in_window: list[NewsHeadline] = []
    for item, dt in collected:
        if dt is None:
            continue
        if ws <= dt <= we:
            in_window.append(item)

    # 중복 제거: 정규화 완전일치 + bigram 유사도 (먼저 등장한 것 유지)
    kept_norms: list[str] = []
    deduped: list[NewsHeadline] = []
    for item in in_window:
        key = _normalize_title(item.title)
        if any(key == k or _similar(key, k) for k in kept_norms):
            continue
        kept_norms.append(key)
        deduped.append(item)

    # 키워드 우선 → 최신순 정렬
    deduped.sort(
        key=lambda n: (
            _priority_score(n.title),
            n.published_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    result = deduped[:max_items]
    log.info(
        "RSS 최종 %d건 (창 내 %d, 중복 제거 후 %d)",
        len(result), len(in_window), len(deduped),
    )
    return result
