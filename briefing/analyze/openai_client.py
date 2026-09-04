"""분석 생성: 시장 한 줄 요약 + 눈여겨볼 종목 3개.

- OpenAI 1회 호출(+최대 1회 재요청), JSON 스키마 강제 응답
- system 프롬프트에 PRD F4.4 제약 명문화
- 후처리: pick 의 종목코드·근거가 payload 와 대조되는지 검증, 금지표현 필터
- 실패/스키마 위반/키 없음 → 규칙 기반 폴백 (fallback_used=True)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..models import (
    BasisType,
    BriefingOutput,
    BriefingPayload,
    DisclosureCategory,
    InvestorType,
    Market,
    Pick,
)

log = logging.getLogger("briefing.analyze")

SYSTEM_CONSTRAINTS = (
    "너는 증시 데이터를 사실 그대로 정리하는 보조자다. 반드시 지켜라:\n"
    "1) 추측성 표현 금지 (예: ~할 듯, 기대된다, 전망, 될 것, 유망).\n"
    "2) 매수/매도/보유 추천, 목표가·밸류에이션 판단 금지.\n"
    "3) 입력으로 주어진 수치·공시 외의 사실을 만들어내지 마라.\n"
    "4) 뉴스 제목은 맥락 파악용일 뿐이다. 뉴스 제목에 있는 수치(지수 등락률 등)를 "
    "market_summary 에 인용하지 마라.\n"
    "5) kospi 또는 kosdaq 값이 null 이면 그 지수의 등락을 언급하지 마라. "
    "지수·수급 데이터가 비어 있으면 market_summary 는 공시(disclosures) 활동을 기준으로 요약하고, "
    "공시도 없으면 '전일 대상 공시가 없습니다.' 로 써라.\n"
    "6) '눈여겨볼 종목'의 근거(basis_text)에는 입력에 있는 공시 내용 또는 "
    "수급 수치를 그대로 인용하라. 종목코드(code)는 입력의 investor_net_buy_top 또는 "
    "disclosures 에 실제로 존재하는 값만 쓴다. 없으면 picks 를 비워라.\n"
    "7) 한국어로 간결하게. market_summary 는 한 문장."
)

# 지수 등락을 언급하는 표현 (grounding 검사용)
_INDEX_MOVE_RE = re.compile(
    r"(코스피|코스닥).{0,15}(상승|하락|올랐|내렸|강세|약세|급등|급락|반등|하락세|상승세|%)"
    r"|[+\-]?\d+(\.\d+)?\s*%"
)

# 생성 텍스트(요약·근거)에서 걸러낼 추측/추천성 표현.
# '순매수/순매도' 같은 수급 용어와 충돌하지 않도록 '매수/매도' 단독어는 넣지 않는다.
_FORBIDDEN_PATTERNS = [
    r"추천", r"목표가", r"비중\s*확대", r"비중\s*축소",
    r"매수\s*의견", r"매도\s*의견",
    r"매수(?:하라|하세요|를\s*고려|가\s*유효)", r"매도(?:하라|하세요)",
    r"사야", r"팔아", r"유망", r"기대", r"전망",
    r"될\s*것", r"할\s*듯", r"예상된다", r"상승할", r"하락할", r"오를", r"내릴",
]
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS))

_JSON_SCHEMA = {
    "name": "briefing_output",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "market_summary": {"type": "string"},
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "code": {"type": "string"},
                        "basis_type": {"type": "string", "enum": ["disclosure", "flow"]},
                        "basis_text": {"type": "string"},
                        "source_url": {"type": ["string", "null"]},
                    },
                    "required": ["name", "code", "basis_type", "basis_text", "source_url"],
                },
            },
        },
        "required": ["market_summary", "picks"],
    },
}


# ---------------------------------------------------------------------------
# payload 축약 (토큰 절감)
# ---------------------------------------------------------------------------

def _compact_payload(payload: BriefingPayload) -> dict:
    def q(iq):
        if not iq:
            return None
        return {"name": iq.name, "close": iq.close,
                "change": iq.change, "change_pct": iq.change_pct}

    flows = {}
    for inv in InvestorType:
        flows[inv.value] = {}
        for mk in Market:
            flows[inv.value][mk.value] = [
                {"name": it.name, "code": it.code, "net_buy_eok": it.net_buy_krw_eok}
                for it in payload.flows.top(inv, mk)[:5]
            ]

    disclosures = [
        {
            "corp_name": d.corp_name,
            "code": d.stock_code,
            "category": d.category.value,
            "report_name": d.report_name,
            "received_at": (
                d.received_at.strftime("%Y-%m-%d %H:%M")
                if d.time_parsed else d.received_at.strftime("%Y-%m-%d") + " (시각 미상)"
            ),
            "is_correction": d.is_correction,
            "key_figures": d.key_figures,
            "url": d.url,
        }
        for d in payload.disclosures
    ]

    return {
        "target_date": payload.target_date.isoformat(),
        "kospi": q(payload.kospi),
        "kosdaq": q(payload.kosdaq),
        "investor_net_buy_top": flows,
        "disclosures": disclosures,
        "news_titles": [{"source": n.source, "title": n.title} for n in payload.news],
    }


def _known_codes(payload: BriefingPayload) -> set[str]:
    codes: set[str] = set()
    for inv in InvestorType:
        for mk in Market:
            codes.update(it.code for it in payload.flows.top(inv, mk) if it.code)
    codes.update(d.stock_code for d in payload.disclosures if d.stock_code)
    return codes


# ---------------------------------------------------------------------------
# 금지표현 처리
# ---------------------------------------------------------------------------

def contains_forbidden(text: str) -> bool:
    return bool(_FORBIDDEN_RE.search(text or ""))


def _has_market_data(payload: BriefingPayload) -> bool:
    if payload.kospi or payload.kosdaq or payload.disclosures:
        return True
    return any(
        payload.flows.top(inv, mk)
        for inv in InvestorType for mk in Market
    )


def summary_is_grounded(summary: str, payload: BriefingPayload) -> bool:
    """요약이 지수 등락을 말하는데 해당 지수 데이터가 없으면 False."""
    if not _INDEX_MOVE_RE.search(summary or ""):
        return True
    if "코스피" in summary and payload.kospi is None and "코스닥" not in summary:
        return False
    if "코스닥" in summary and payload.kosdaq is None and "코스피" not in summary:
        return False
    # 지수명 없이 %/등락만 언급하는데 두 지수 모두 없음
    if payload.kospi is None and payload.kosdaq is None:
        return False
    return True


def strip_forbidden_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?。])\s+", text or "")
    kept = [p for p in parts if p and not _FORBIDDEN_RE.search(p)]
    return " ".join(kept).strip()


# ---------------------------------------------------------------------------
# 규칙 기반 폴백
# ---------------------------------------------------------------------------

def _fmt_quote(q) -> str | None:
    if q is None or q.close is None or q.change_pct is None:
        return None
    sign = "+" if q.change_pct >= 0 else ""
    return f"{q.name} {q.close:,.2f}({sign}{q.change_pct:.2f}%)"


_STRONG_CATS = {
    DisclosureCategory.SUPPLY_CONTRACT,
    DisclosureCategory.EARNINGS,
    DisclosureCategory.CAPITAL_INCREASE,
}


def _disclosure_score(d) -> tuple:
    return (
        bool(d.key_figures),                 # 수치 있는 공시 우선
        d.category in _STRONG_CATS,           # 계약·실적·증자 우선
        not d.is_correction,                  # 정정 아닌 것 우선
        d.time_parsed,                        # 시각 확정된 것 우선
    )


def _rule_based_picks(payload: BriefingPayload) -> list[Pick]:
    picks: list[Pick] = []
    ranked = sorted(
        (d for d in payload.disclosures if d.stock_code),
        key=_disclosure_score, reverse=True,
    )
    for d in ranked:
        if len(picks) >= 3:
            break
        figures = ", ".join(f"{k} {v}" for k, v in d.key_figures.items())
        when = d.received_at.strftime("%m-%d %H:%M") if d.time_parsed \
            else d.received_at.strftime("%m-%d") + " 시각 미상"
        mark = "[정정] " if d.is_correction else ""
        basis = f"{mark}{d.category.value} 공시({when})"
        if figures:
            basis += f" — {figures}"
        picks.append(Pick(name=d.corp_name, code=d.stock_code,
                          basis_type=BasisType.DISCLOSURE, basis_text=basis,
                          source_url=d.url))

    if len(picks) < 3:
        used = {p.code for p in picks}
        for it in payload.flows.top(InvestorType.FOREIGN, Market.KOSPI):
            if len(picks) >= 3:
                break
            if it.code in used or not it.code:
                continue
            picks.append(Pick(
                name=it.name, code=it.code, basis_type=BasisType.FLOW,
                basis_text=f"전일 외국인 코스피 순매수 상위, {it.net_buy_krw_eok:+,.1f}억원",
            ))
    return picks[:3]


def _rule_based(payload: BriefingPayload, tz: ZoneInfo) -> BriefingOutput:
    quotes = [s for s in (_fmt_quote(payload.kospi), _fmt_quote(payload.kosdaq)) if s]
    if quotes:
        summary = " / ".join(quotes)
    elif payload.disclosures:
        cats = sorted({d.category.value for d in payload.disclosures})
        summary = (
            f"전일 마감 후 접수 공시 중 대상 {len(payload.disclosures)}건 "
            f"({'·'.join(cats)})."
        )
    else:
        summary = "전일 대상 공시가 없습니다."
    return BriefingOutput(
        market_summary=summary,
        picks=_rule_based_picks(payload),
        generated_at=datetime.now(tz),
        fallback_used=True,
    )


# ---------------------------------------------------------------------------
# OpenAI 호출
# ---------------------------------------------------------------------------

def _call_openai(client, model: str, compact: dict, extra_system: str = "") -> dict:
    system = SYSTEM_CONSTRAINTS + (("\n" + extra_system) if extra_system else "")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": (
                "다음 JSON 데이터로 브리핑을 만들어라. "
                "market_summary(한 문장)와 picks(최대 3개, 근거는 공시/수급 수치 인용)를 채워라.\n\n"
                + json.dumps(compact, ensure_ascii=False)
            )},
        ],
        response_format={"type": "json_schema", "json_schema": _JSON_SCHEMA},
        temperature=0.2,
        max_tokens=1200,
    )
    return json.loads(resp.choices[0].message.content)


def _validate_picks(raw_picks: list[dict], payload: BriefingPayload) -> list[Pick]:
    known = _known_codes(payload)
    url_by_code = {d.stock_code: d.url for d in payload.disclosures if d.stock_code}
    out: list[Pick] = []
    for rp in raw_picks:
        code = str(rp.get("code", "")).strip()
        basis_text = str(rp.get("basis_text", "")).strip()
        if code not in known:
            log.info("pick 제거(미확인 종목코드): %s", rp)
            continue
        if not basis_text or contains_forbidden(basis_text):
            log.info("pick 제거(근거 없음/금지표현): %s", rp)
            continue
        # 근거에 수치 또는 공시 키워드가 있어야 함
        if not (re.search(r"\d", basis_text)
                or re.search(r"공시|계약|증자|감자|최대주주|실적", basis_text)):
            log.info("pick 제거(근거 불충분): %s", rp)
            continue
        bt = rp.get("basis_type")
        basis_type = BasisType.DISCLOSURE if bt == "disclosure" else BasisType.FLOW
        out.append(Pick(
            name=str(rp.get("name", "")).strip() or code,
            code=code,
            basis_type=basis_type,
            basis_text=basis_text,
            source_url=rp.get("source_url") or url_by_code.get(code),
        ))
        if len(out) >= 3:
            break
    return out


def generate(
    payload: BriefingPayload,
    *,
    model: str,
    api_key: str,
    tz: ZoneInfo,
) -> BriefingOutput:
    if not api_key:
        log.warning("OPENAI_API_KEY 미설정 — 규칙 기반 폴백 사용")
        return _rule_based(payload, tz)

    if not _has_market_data(payload):
        log.warning("지수·수급·공시 데이터가 모두 비어 있음 — OpenAI 호출 없이 규칙 기반 폴백")
        return _rule_based(payload, tz)

    try:
        from openai import OpenAI
    except Exception:  # noqa: BLE001
        log.warning("openai 패키지 import 실패 — 규칙 기반 폴백")
        return _rule_based(payload, tz)

    compact = _compact_payload(payload)
    client = OpenAI(api_key=api_key, timeout=30, max_retries=1)

    try:
        data = _call_openai(client, model, compact)
        summary = str(data.get("market_summary", "")).strip()
        picks = _validate_picks(data.get("picks", []), payload)

        # 금지표현 또는 근거 없는 지수 언급이 있으면 1회 재요청
        if contains_forbidden(summary) or not summary_is_grounded(summary, payload):
            log.info("요약 문제(금지표현/미근거 지수 언급) — 재요청")
            data = _call_openai(
                client, model, compact,
                extra_system=(
                    "이전 응답에 추측·추천 표현이 있거나, 데이터에 없는 지수 등락을 언급했다. "
                    "제공된 수치만 쓰고, 없는 지수는 언급하지 마라."
                ),
            )
            summary = str(data.get("market_summary", "")).strip()
            picks = _validate_picks(data.get("picks", []), payload) or picks

        degraded = False
        if contains_forbidden(summary):
            summary = strip_forbidden_sentences(summary)
            degraded = True
        if not summary or not summary_is_grounded(summary, payload):
            log.info("요약이 근거 부족 — 규칙 기반 요약으로 대체")
            summary = _rule_based(payload, tz).market_summary
            degraded = True
        if not picks:
            log.info("검증 통과 pick 없음 — 규칙 기반 pick 사용")
            picks = _rule_based_picks(payload)
            degraded = True

        return BriefingOutput(
            market_summary=summary,
            picks=picks,
            generated_at=datetime.now(tz),
            fallback_used=degraded,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenAI 분석 실패(%s) — 규칙 기반 폴백", exc)
        return _rule_based(payload, tz)
