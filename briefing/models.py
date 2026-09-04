"""정규화 데이터 모델 (pydantic v2).

수집기 → payload → 분석 → 렌더 전 과정에서 이 모델들만 주고받는다.
2단계에서 수집기 내부 구현이 바뀌어도 이 인터페이스는 유지한다.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class DisclosureCategory(str, Enum):
    """PRD F2.3 통과 대상 분류."""

    EARNINGS = "실적"
    CAPITAL_INCREASE = "증자"
    CAPITAL_REDUCTION = "감자"
    LARGEST_SHAREHOLDER_CHANGE = "최대주주변경"
    SUPPLY_CONTRACT = "공급계약"


class BasisType(str, Enum):
    """눈여겨볼 종목의 근거 유형."""

    DISCLOSURE = "disclosure"
    FLOW = "flow"


class Market(str, Enum):
    KOSPI = "코스피"
    KOSDAQ = "코스닥"


class InvestorType(str, Enum):
    FOREIGN = "외국인"
    INSTITUTION = "기관"
    INDIVIDUAL = "개인"


class IndexQuote(BaseModel):
    name: str
    close: float | None = None
    change: float | None = None
    change_pct: float | None = None


class InvestorFlowItem(BaseModel):
    name: str
    code: str
    net_buy_krw_eok: float  # 순매수 금액(억원), 매도 우위면 음수
    net_buy_qty: int | None = None


class InvestorFlows(BaseModel):
    """투자자구분 × 시장 → 순매수 상위 리스트."""

    data: dict[InvestorType, dict[Market, list[InvestorFlowItem]]] = Field(default_factory=dict)

    def top(self, investor: InvestorType, market: Market) -> list[InvestorFlowItem]:
        return self.data.get(investor, {}).get(market, [])


class Disclosure(BaseModel):
    corp_name: str
    stock_code: str | None = None
    report_name: str
    category: DisclosureCategory
    received_at: datetime  # KST, tz-aware
    url: str
    key_figures: dict[str, str] = Field(default_factory=dict)
    is_correction: bool = False  # 보고서명에 [정정] 포함
    time_parsed: bool = True  # False = 상세 접수시각 파싱 실패로 날짜만 사용(보수적 포함)


class NewsHeadline(BaseModel):
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    # 저작권: 본문/요약은 저장하지 않는다.


class BriefingPayload(BaseModel):
    """수집 결과 통합 — 분석기 입력."""

    target_date: date
    window_start: datetime
    window_end: datetime
    kospi: IndexQuote | None = None
    kosdaq: IndexQuote | None = None
    flows: InvestorFlows = Field(default_factory=InvestorFlows)
    disclosures: list[Disclosure] = Field(default_factory=list)
    news: list[NewsHeadline] = Field(default_factory=list)


class Pick(BaseModel):
    name: str
    code: str
    basis_type: BasisType
    basis_text: str  # 반드시 공시 문구 또는 수급 수치를 포함
    source_url: str | None = None


class BriefingOutput(BaseModel):
    """분석기 산출 — 렌더러 입력."""

    market_summary: str
    picks: list[Pick] = Field(default_factory=list)
    generated_at: datetime
    fallback_used: bool = False  # True = OpenAI 미사용/실패로 규칙 기반 대체
