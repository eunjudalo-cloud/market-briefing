"""분석기: 규칙 기반 폴백 / 금지표현 필터 / pick 검증."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from briefing.analyze import openai_client as oc
from briefing.models import (
    BasisType,
    BriefingPayload,
    Disclosure,
    DisclosureCategory,
    InvestorFlowItem,
    InvestorFlows,
    InvestorType,
    Market,
)

KST = ZoneInfo("Asia/Seoul")


def _payload() -> BriefingPayload:
    flows = InvestorFlows(data={
        InvestorType.FOREIGN: {
            Market.KOSPI: [
                InvestorFlowItem(name="에이종목", code="000111", net_buy_krw_eok=321.0),
                InvestorFlowItem(name="비종목", code="000222", net_buy_krw_eok=188.0),
            ],
            Market.KOSDAQ: [],
        },
        InvestorType.INSTITUTION: {Market.KOSPI: [], Market.KOSDAQ: []},
        InvestorType.INDIVIDUAL: {Market.KOSPI: [], Market.KOSDAQ: []},
    })
    disc = [
        Disclosure(
            corp_name="가온전자", stock_code="123450",
            report_name="단일판매ㆍ공급계약체결",
            category=DisclosureCategory.SUPPLY_CONTRACT,
            received_at=datetime(2026, 9, 3, 19, 30, tzinfo=KST),
            url="https://dart.fss.or.kr/x",
            key_figures={"계약금액": "480억원"},
        ),
    ]
    return BriefingPayload(
        target_date=date(2026, 9, 4),
        window_start=datetime(2026, 9, 3, 18, 0, tzinfo=KST),
        window_end=datetime(2026, 9, 4, 7, 0, tzinfo=KST),
        kospi=None, kosdaq=None, flows=flows, disclosures=disc, news=[],
    )


def test_forbidden_detection_and_strip():
    assert oc.contains_forbidden("삼성전자 매수 추천")
    assert oc.contains_forbidden("반등이 기대된다")
    assert not oc.contains_forbidden("코스피 2712.34, 외국인 순매수 321억원")
    cleaned = oc.strip_forbidden_sentences("코스피는 0.5% 올랐다. 추가 상승할 것으로 보인다.")
    assert "상승할" not in cleaned
    assert "코스피는" in cleaned


def test_generate_without_key_uses_rule_based():
    out = oc.generate(_payload(), model="gpt-4o", api_key="", tz=KST)
    assert out.fallback_used is True
    assert out.picks, "공시 기반 pick 이 있어야 한다"
    assert out.picks[0].code == "123450"
    assert out.picks[0].basis_type == BasisType.DISCLOSURE
    assert "480억원" in out.picks[0].basis_text
    assert not oc.contains_forbidden(out.market_summary)


def test_summary_grounding():
    from briefing.models import IndexQuote

    p = _payload()  # kospi/kosdaq = None
    assert oc.summary_is_grounded("전일 공급계약 공시 1건", p) is True
    assert oc.summary_is_grounded("코스피와 코스닥 모두 1% 이상 상승했다", p) is False
    assert oc.summary_is_grounded("코스피 2712.34(+0.49%)", p) is False

    p.kospi = IndexQuote(name="코스피", close=2712.34, change=13.2, change_pct=0.49)
    p.kosdaq = IndexQuote(name="코스닥", close=764.1, change=-2.0, change_pct=-0.26)
    assert oc.summary_is_grounded("코스피 0.49% 상승", p) is True


def test_generate_no_market_data_skips_openai(monkeypatch):
    from briefing.models import BriefingPayload
    empty = BriefingPayload(
        target_date=date(2026, 9, 4),
        window_start=datetime(2026, 9, 3, 18, 0, tzinfo=KST),
        window_end=datetime(2026, 9, 4, 7, 0, tzinfo=KST),
    )

    def _boom(*a, **k):
        raise AssertionError("OpenAI 를 호출하면 안 된다")

    monkeypatch.setattr(oc, "_call_openai", _boom)
    out = oc.generate(empty, model="gpt-4o", api_key="sk-dummy", tz=KST)
    assert out.fallback_used is True
    assert out.picks == []
    assert "없음" in out.market_summary


def test_validate_picks_drops_unknown_and_forbidden():
    payload = _payload()
    raw = [
        {"name": "가온전자", "code": "123450", "basis_type": "disclosure",
         "basis_text": "공급계약 480억원", "source_url": None},
        {"name": "허구", "code": "999999", "basis_type": "flow",
         "basis_text": "순매수 100억원", "source_url": None},       # 미확인 코드
        {"name": "에이종목", "code": "000111", "basis_type": "flow",
         "basis_text": "지금 매수 추천", "source_url": None},        # 금지표현
        {"name": "비종목", "code": "000222", "basis_type": "flow",
         "basis_text": "근거없음", "source_url": None},             # 수치/키워드 없음
    ]
    picks = oc._validate_picks(raw, payload)
    assert [p.code for p in picks] == ["123450"]
    # 공시 종목엔 source_url 이 payload 에서 채워진다
    assert picks[0].source_url == "https://dart.fss.or.kr/x"
