"""스모크 테스트: 파이프라인이 끝까지 돌아 브리핑 .md 를 생성한다.

수집기는 네트워크를 타지 않도록 대체한다(결정론적).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from briefing import pipeline
from briefing.collectors import dart, krx, rss
from briefing.logging_setup import setup_logging
from briefing.models import (
    Disclosure,
    DisclosureCategory,
    IndexQuote,
    InvestorFlowItem,
    InvestorFlows,
    InvestorType,
    Market,
    NewsHeadline,
)

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def _stub_collectors(monkeypatch):
    def fake_krx(target_date, ws, we, **kw):
        flows = InvestorFlows(data={
            inv: {
                mk: [InvestorFlowItem(name=f"{inv.value}{mk.value}{i}",
                                      code=f"{10000 + i:06d}",
                                      net_buy_krw_eok=100.0 - i)
                     for i in range(10)]
                for mk in Market
            }
            for inv in InvestorType
        })
        return (
            IndexQuote(name="코스피", close=2712.34, change=13.21, change_pct=0.49),
            IndexQuote(name="코스닥", close=764.12, change=-2.03, change_pct=-0.26),
            flows,
        )

    def fake_dart(target_date, ws, we, **kw):
        return [Disclosure(
            corp_name="가온전자", stock_code="123450",
            report_name="단일판매ㆍ공급계약체결",
            category=DisclosureCategory.SUPPLY_CONTRACT,
            received_at=datetime(2026, 9, 3, 19, 30, tzinfo=KST),
            url="https://dart.fss.or.kr/x", key_figures={"계약금액": "480억원"},
        )]

    def fake_rss(feeds, ws, we, max_items):
        return [NewsHeadline(title="코스피 외국인 순매수 전환", url="https://ex.com/1",
                             source="한국경제",
                             published_at=datetime(2026, 9, 4, 6, 0, tzinfo=KST))]

    monkeypatch.setattr(krx, "collect", fake_krx)
    monkeypatch.setattr(dart, "collect", fake_dart)
    monkeypatch.setattr(rss, "collect", fake_rss)


@pytest.fixture
def _isolated_dirs(tmp_path, monkeypatch):
    out = tmp_path / "output"
    data = tmp_path / "data"
    out.mkdir()
    data.mkdir()
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", out)
    monkeypatch.setattr(pipeline, "DATA_DIR", data)
    return out


def test_pipeline_generates_briefing(_stub_collectors, _isolated_dirs):
    setup_logging()
    target = date(2026, 9, 4)  # 금요일, 영업일
    out = _isolated_dirs / f"브리핑_{target.isoformat()}.md"

    rc = pipeline.run(target, force=True, dry_run=True)

    assert rc == 0
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "데일리 마켓 브리핑 — 2026-09-04" in text
    assert "## 시장 한 줄 요약" in text
    assert "## 오늘 눈여겨볼 종목 3" in text
    assert "## 뉴스 헤드라인 (제목·링크)" in text
    assert "투자 자문이나 매매 추천이 아닙니다" in text
    assert "\n1. " in text and "\n2. " in text and "\n3. " in text
    assert "가온전자(123450)" in text
    assert "480억원" in text


def test_non_business_day_is_skipped(_stub_collectors, _isolated_dirs):
    setup_logging()
    target = date(2026, 9, 5)  # 토요일
    out = _isolated_dirs / f"브리핑_{target.isoformat()}.md"

    rc = pipeline.run(target, force=True, dry_run=True)
    assert rc == 0
    assert not out.exists()
