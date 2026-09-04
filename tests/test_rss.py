"""RSS 수집기: 파싱 / 창 필터 / 중복 제거 / 본문 미저장."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from briefing.collectors import rss
from briefing.config import FeedConfig
from briefing.models import NewsHeadline

FIX = Path(__file__).parent / "fixtures"


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content


def _fake_fetch(content: bytes):
    def _inner(session, method, url, **kw):
        return _FakeResp(content)
    return _inner


def test_parse_and_window_filter(monkeypatch):
    content = (FIX / "rss_hankyung_sample.xml").read_bytes()
    monkeypatch.setattr(rss, "request_with_retry", _fake_fetch(content))

    feeds = [FeedConfig(name="한국경제", url="https://example.com/feed")]
    now = datetime.now(timezone.utc)
    wide = rss.collect(feeds, now - timedelta(days=30), now + timedelta(days=1), 15)
    assert wide, "창을 넓게 잡으면 항목이 있어야 한다"
    assert all(isinstance(n, NewsHeadline) for n in wide)
    assert all(n.title and n.url for n in wide)
    assert all(n.source == "한국경제" for n in wide)

    # 창을 과거로 완전히 벗어나게 잡으면 0건
    empty = rss.collect(
        feeds,
        datetime(2000, 1, 1, tzinfo=timezone.utc),
        datetime(2000, 1, 2, tzinfo=timezone.utc),
        15,
    )
    assert empty == []


def test_dedup_and_max_items(monkeypatch):
    content = (FIX / "rss_hankyung_sample.xml").read_bytes()
    monkeypatch.setattr(rss, "request_with_retry", _fake_fetch(content))

    # 같은 피드를 두 번 → 완전히 중복
    feeds = [
        FeedConfig(name="한국경제", url="https://example.com/a"),
        FeedConfig(name="한국경제", url="https://example.com/b"),
    ]
    now = datetime.now(timezone.utc)
    res = rss.collect(feeds, now - timedelta(days=30), now + timedelta(days=1), 5)
    titles = [n.title for n in res]
    assert len(titles) == len(set(titles)), "중복 제목이 제거되어야 한다"
    assert len(res) <= 5


def test_entry_datetime_uses_utc_struct_time():
    # B-RSS-1: feedparser 의 published_parsed(UTC struct_time) 를 그대로 UTC 로 해석해야 함
    import time as _t

    entry = {"published_parsed": _t.struct_time((2026, 9, 4, 6, 0, 0, 4, 247, 0))}
    dt = rss._entry_datetime(entry)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 9 and dt.day == 4
    assert dt.hour == 6 and dt.minute == 0
    assert dt.tzinfo == timezone.utc


def test_similar_titles_are_deduped(monkeypatch):
    assert rss._similar(
        rss._normalize_title("코스피 1%대 상승 출발해 6600대로 코스닥도 올라"),
        rss._normalize_title("코스피 1%대 상승 출발해 6600대로"),
    )
    assert not rss._similar(
        rss._normalize_title("삼성전자 자사주 매입"),
        rss._normalize_title("현대차 유상증자 결정"),
    )


def test_headline_model_has_no_body_field():
    n = NewsHeadline(title="t", url="u", source="s")
    assert not hasattr(n, "summary")
    assert not hasattr(n, "content")
    assert set(n.model_dump().keys()) == {"title", "url", "source", "published_at"}
