"""테스트/미리보기 공용 샘플 payload + output."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from briefing.models import (
    BasisType,
    BriefingOutput,
    BriefingPayload,
    Disclosure,
    DisclosureCategory,
    IndexQuote,
    InvestorFlowItem,
    InvestorFlows,
    InvestorType,
    Market,
    NewsHeadline,
    Pick,
)

KST = ZoneInfo("Asia/Seoul")


def _flow(prefix: str, base: float) -> list[InvestorFlowItem]:
    return [
        InvestorFlowItem(name=f"{prefix}{i + 1}", code=f"{5930 + i * 7:06d}",
                         net_buy_krw_eok=round(base - i * 63.4, 1))
        for i in range(10)
    ]


def sample_payload() -> BriefingPayload:
    flows = InvestorFlows(data={
        InvestorType.FOREIGN: {
            Market.KOSPI: _flow("삼성전자 외 ", 1240.0),
            Market.KOSDAQ: _flow("에코프로 외 ", 410.0),
        },
        InvestorType.INSTITUTION: {
            Market.KOSPI: _flow("현대차 외 ", 730.0),
            Market.KOSDAQ: _flow("HLB 외 ", 260.0),
        },
        InvestorType.INDIVIDUAL: {
            Market.KOSPI: _flow("LG엔솔 외 ", 980.0),
            Market.KOSDAQ: _flow("알테오젠 외 ", 300.0),
        },
    })
    disclosures = [
        Disclosure(
            corp_name="가온전자", stock_code="123450",
            report_name="단일판매ㆍ공급계약체결",
            category=DisclosureCategory.SUPPLY_CONTRACT,
            received_at=datetime(2026, 9, 3, 19, 12, tzinfo=KST),
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260903900001",
            key_figures={"계약금액": "480억원", "최근매출액대비": "12.3%"},
        ),
        Disclosure(
            corp_name="라온바이오", stock_code="234560",
            report_name="유상증자결정",
            category=DisclosureCategory.CAPITAL_INCREASE,
            received_at=datetime(2026, 9, 4, 6, 25, tzinfo=KST),
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260904900002",
            key_figures={"증자방식": "제3자배정", "자금조달금액": "300억원"},
        ),
    ]
    news = [
        NewsHeadline(title="코스피, 외국인 순매수 전환에 강보합 마감",
                     url="https://example.com/n/1", source="한국경제",
                     published_at=datetime(2026, 9, 4, 6, 40, tzinfo=KST)),
        NewsHeadline(title='"반도체 수급 개선"…기관 이틀째 매수 우위 & 지수 방어',
                     url="https://example.com/n/2", source="연합뉴스",
                     published_at=datetime(2026, 9, 4, 5, 55, tzinfo=KST)),
        NewsHeadline(title="단일판매·공급계약 공시 잇따라…중소형주 변동성 확대",
                     url="https://example.com/n/3", source="머니투데이",
                     published_at=datetime(2026, 9, 4, 1, 10, tzinfo=KST)),
    ]
    return BriefingPayload(
        target_date=date(2026, 9, 4),
        window_start=datetime(2026, 9, 3, 18, 0, tzinfo=KST),
        window_end=datetime(2026, 9, 4, 7, 0, tzinfo=KST),
        kospi=IndexQuote(name="코스피", close=2712.34, change=13.21, change_pct=0.49),
        kosdaq=IndexQuote(name="코스닥", close=764.12, change=-2.03, change_pct=-0.26),
        flows=flows, disclosures=disclosures, news=news,
    )


def sample_output() -> BriefingOutput:
    return BriefingOutput(
        market_summary="코스피 2,712.34(+0.49%)는 외국인 순매수 전환, 코스닥 764.12(-0.26%)는 기관 매도 우위.",
        picks=[
            Pick(name="가온전자", code="123450", basis_type=BasisType.DISCLOSURE,
                 basis_text="단일판매·공급계약 체결, 계약금액 480억원(최근 매출액 대비 12.3%)",
                 source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260903900001"),
            Pick(name="삼성전자 외 1", code="005930", basis_type=BasisType.FLOW,
                 basis_text="전일 외국인 코스피 순매수 1위, +1,240.0억원"),
            Pick(name="라온바이오", code="234560", basis_type=BasisType.DISCLOSURE,
                 basis_text="제3자배정 유상증자 결정, 자금조달금액 300억원",
                 source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260904900002"),
        ],
        generated_at=datetime(2026, 9, 4, 7, 0, tzinfo=KST),
        fallback_used=False,
    )
