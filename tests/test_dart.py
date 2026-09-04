"""DART 수집기: 카테고리 매칭 + 접수시각 확정/창 필터."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from briefing.collectors import dart
from briefing.models import DisclosureCategory

KST = ZoneInfo("Asia/Seoul")
FIX = Path(__file__).parent / "fixtures"


def test_match_category():
    cases = {
        "단일판매ㆍ공급계약체결": (DisclosureCategory.SUPPLY_CONTRACT, False),
        "유상증자결정": (DisclosureCategory.CAPITAL_INCREASE, False),
        "감자결정": (DisclosureCategory.CAPITAL_REDUCTION, False),
        "[기재정정]최대주주변경": (DisclosureCategory.LARGEST_SHAREHOLDER_CHANGE, True),
        "연결재무제표기준영업(잠정)실적(공정공시)": (DisclosureCategory.EARNINGS, False),
        # B-DART-1: 표기 변형(대규모법인은15% / 100분의15)도 실적으로 매칭
        "매출액또는손익구조30%(대규모법인은15%)이상변동": (DisclosureCategory.EARNINGS, False),
        "매출액또는손익구조30%(대규모법인은100분의15)이상변동": (DisclosureCategory.EARNINGS, False),
        "[기재정정]매출액또는손익구조30%(대규모법인은15%)이상변동": (DisclosureCategory.EARNINGS, True),
        "분기보고서": (None, False),
        "주주총회소집결의": (None, False),
    }
    for name, (cat, corr) in cases.items():
        got_cat, got_corr = dart.match_category(name)
        assert got_cat == cat, name
        assert got_corr == corr, name


def test_viewdoc_regex_matches_current_dart_format():
    # 실제 페이지 형식: 큰따옴표, 7인자, dtd="HTML"
    sample = 'foo; viewDoc("20260903900613", "11566209", "0", "0", "0", "HTML", ""); bar'
    m = dart._VIEWDOC_RE.search(sample)
    assert m is not None
    assert m.groups()[:3] == ("20260903900613", "11566209", "0")
    assert m.group(6) == "HTML"


def test_collect_filters_categories_and_resolves_time(monkeypatch):
    raw = json.loads((FIX / "dart_list_sample.json").read_text(encoding="utf-8"))["list"]
    monkeypatch.setattr(dart, "_fetch_list", lambda *a, **k: raw)
    monkeypatch.setattr(dart, "_extract_key_figures", lambda *a, **k: {})
    monkeypatch.setattr(dart, "make_session", lambda *a, **k: object())

    # RSS 정확한 접수시각 (일부 종목만)
    time_map = {
        "20260903900002": datetime(2026, 9, 3, 19, 30, tzinfo=KST),  # 창 안
        "20260904900003": datetime(2026, 9, 4, 6, 25, tzinfo=KST),   # 창 안
        "20260904900005": datetime(2026, 9, 4, 8, 10, tzinfo=KST),   # 창 밖(07시 이후)
        "20260903700006": datetime(2026, 9, 3, 14, 0, tzinfo=KST),   # 창 밖(18시 이전)
    }
    monkeypatch.setattr(dart, "_rss_time_map", lambda *a, **k: time_map)

    window_start = datetime(2026, 9, 3, 18, 0, tzinfo=KST)
    window_end = datetime(2026, 9, 4, 7, 0, tzinfo=KST)

    out = dart.collect(
        datetime(2026, 9, 4).date(), window_start, window_end, api_key="dummy",
    )

    names = {d.corp_name for d in out}
    assert names == {"가온전자", "라온바이오", "대성산업"}

    by_name = {d.corp_name: d for d in out}
    assert by_name["가온전자"].time_parsed is False       # T-1, RSS 없음 → 날짜만, 보수적 포함
    assert by_name["라온바이오"].time_parsed is True
    assert by_name["대성산업"].time_parsed is True
    assert by_name["대성산업"].is_correction is True
    assert "새벽전자" not in names                          # 정확 시각이 창 밖
    assert "한낮기업" not in names                          # 정확 시각이 창 밖
    assert "무관기업" not in names                          # 비대상 카테고리

    # 정렬: 날짜 → (시각확정 우선) → 시각
    keys = [(d.received_at.date(), not d.time_parsed, d.received_at) for d in out]
    assert keys == sorted(keys)
    assert [d.corp_name for d in out] == ["라온바이오", "가온전자", "대성산업"]


def test_collect_without_key_returns_empty():
    out = dart.collect(
        datetime(2026, 9, 4).date(),
        datetime(2026, 9, 3, 18, 0, tzinfo=KST),
        datetime(2026, 9, 4, 7, 0, tzinfo=KST),
        api_key="",
    )
    assert out == []
