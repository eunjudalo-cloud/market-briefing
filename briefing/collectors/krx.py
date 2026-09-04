"""KRX / 공공데이터 수집 — 전일 지수 + 투자자별 순매수 상위 10.

1순위: KRX 정보데이터시스템 getJsonData.cmd
2순위(지수 한정): 공공데이터포털 금융위 지수시세 (DATA_GO_KR_KEY 필요)

⚠ 검증 필요: 아래 BLD 코드와 응답 필드명은 공개 문서가 없어 커뮤니티 관례를 따른 값이다.
   실제 거래일 응답으로 1회 검증한 뒤 확정할 것 (PRD 11장 남은 확인 항목).
   응답 구조가 다르면 파싱에서 걸러져 빈 결과 + 경고 로그로 처리되고, 파이프라인은 계속된다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from ..http_client import make_session, request_with_retry
from ..models import IndexQuote, InvestorFlowItem, InvestorFlows, InvestorType, Market

log = logging.getLogger("briefing.krx")

BASE = "http://data.krx.co.kr"
GETJSON_URL = f"{BASE}/comm/bldAttendant/getJsonData.cmd"
PRIME_URL = f"{BASE}/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101"

BLD_INDEX = "dbms/MDC/STAT/standard/MDCSTAT00301"          # 전체지수 시세
BLD_INVESTOR_NETBUY_TOP = "dbms/MDC/STAT/standard/MDCSTAT02601"  # 투자자별 순매수상위종목

# KRX 투자자구분 코드 (관례값)
_INVESTOR_CD = {
    InvestorType.FOREIGN: "9000",      # 외국인
    InvestorType.INSTITUTION: "7050",  # 기관합계
    InvestorType.INDIVIDUAL: "8000",   # 개인
}
_MARKET_CD = {Market.KOSPI: "STK", Market.KOSDAQ: "KSQ"}
_INDEX_MIDCLASS = {Market.KOSPI: ("02", "코스피"), Market.KOSDAQ: ("03", "코스닥")}


def _to_float(s: object) -> float | None:
    if s is None:
        return None
    txt = str(s).replace(",", "").strip()
    if txt in ("", "-", "N/A"):
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def _to_int(s: object) -> int | None:
    f = _to_float(s)
    return int(f) if f is not None else None


class _KrxClient:
    def __init__(self) -> None:
        self.session = make_session({
            "Referer": PRIME_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })
        try:
            request_with_retry(self.session, "GET", PRIME_URL, retries=1, timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.debug("KRX 프라이밍 요청 실패(무시): %s", exc)

    def get_json(self, bld: str, **params: str) -> list[dict]:
        payload = {"bld": bld, "locale": "ko_KR",
                   "share": "1", "money": "1", "csvxls_isNo": "false"}
        payload.update(params)
        resp = request_with_retry(
            self.session, "POST", GETJSON_URL, data=payload, retries=3, timeout=15
        )
        body = resp.text.strip()
        if body in ("", "LOGOUT") or body.startswith("<"):
            raise RuntimeError(f"KRX 비정상 응답: {body[:60]!r}")
        data = resp.json()
        for key in ("output", "OutBlock_1", "block1", "list"):
            if isinstance(data.get(key), list):
                return data[key]
        # 첫 번째 리스트형 값
        for v in data.values():
            if isinstance(v, list):
                return v
        return []


def _fetch_index(client: _KrxClient, trd_dd: str, market: Market) -> IndexQuote | None:
    midclass, want_name = _INDEX_MIDCLASS[market]
    try:
        rows = client.get_json(BLD_INDEX, idxIndMidclssCd=midclass, trdDd=trd_dd)
    except Exception as exc:  # noqa: BLE001
        log.warning("KRX 지수 조회 실패 [%s]: %s", market.value, exc)
        return None

    for r in rows:
        name = (r.get("IDX_NM") or r.get("IDX_NM_KOR") or "").strip()
        if name == want_name:
            return IndexQuote(
                name=want_name,
                close=_to_float(r.get("CLSPRC_IDX") or r.get("CLSPRC")),
                change=_to_float(r.get("CMPPREVDD_IDX") or r.get("PRV_DD_CMPR")),
                change_pct=_to_float(r.get("FLUC_RT") or r.get("UPDN_RATE")),
            )
    log.warning("KRX 지수 응답에서 '%s' 행을 찾지 못함 (필드 예: %s)",
                want_name, list(rows[0].keys()) if rows else "빈 응답")
    return None


def _fetch_flow(
    client: _KrxClient, trd_dd: str, market: Market, investor: InvestorType
) -> list[InvestorFlowItem]:
    try:
        rows = client.get_json(
            BLD_INVESTOR_NETBUY_TOP,
            mktId=_MARKET_CD[market],
            invstTpCd=_INVESTOR_CD[investor],
            trdDd=trd_dd, strtDd=trd_dd, endDd=trd_dd,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("KRX 순매수 상위 조회 실패 [%s/%s]: %s",
                    investor.value, market.value, exc)
        return []

    items: list[InvestorFlowItem] = []
    for r in rows:
        name = (r.get("ISU_ABBRV") or r.get("ISU_NM") or r.get("KOR_SECN_NM") or "").strip()
        code = (r.get("ISU_SRT_CD") or r.get("ISU_CD") or "").strip()
        net_val = _to_float(r.get("NETBID_TRDVAL") or r.get("NETASK_TRDVAL")
                            or r.get("NET_TRDVAL") or r.get("TRDVAL"))
        if not name or net_val is None:
            continue
        items.append(InvestorFlowItem(
            name=name, code=code,
            net_buy_krw_eok=round(net_val / 1e8, 1),  # 원 → 억원
            net_buy_qty=_to_int(r.get("NETBID_TRDVOL") or r.get("NET_TRDVOL")),
        ))
        if len(items) >= 10:
            break
    if not items and rows:
        log.warning("KRX 순매수 응답 파싱 실패 [%s/%s] (필드 예: %s)",
                    investor.value, market.value, list(rows[0].keys()))
    return items


def _index_from_datagokr(key: str, trd_dd: str, market: Market) -> IndexQuote | None:
    """공공데이터포털 금융위_지수시세정보 fallback (지수만)."""
    url = ("https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/"
           "getStockMarketIndex")
    idx_name = "코스피" if market is Market.KOSPI else "코스닥"
    try:
        session = make_session()
        resp = request_with_retry(
            session, "GET", url, retries=2, timeout=15,
            params={
                "serviceKey": key, "resultType": "json",
                "beginBasDt": trd_dd, "endBasDt": trd_dd,
                "idxNm": idx_name, "numOfRows": 10,
            },
        )
        items = (resp.json().get("response", {}).get("body", {})
                 .get("items", {}).get("item", []))
        if isinstance(items, dict):
            items = [items]
        for it in items:
            if it.get("idxNm", "").strip() == idx_name:
                return IndexQuote(
                    name=idx_name,
                    close=_to_float(it.get("clpr")),
                    change=_to_float(it.get("vs")),
                    change_pct=_to_float(it.get("fltRt")),
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("공공데이터포털 지수 fallback 실패 [%s]: %s", idx_name, exc)
    return None


def collect(
    target_date: date,
    window_start: datetime,
    window_end: datetime,
    *,
    data_go_kr_key: str | None = None,
) -> tuple[IndexQuote | None, IndexQuote | None, InvestorFlows]:
    """전일(직전 영업일) 지수 2종 + 투자자별 순매수 상위 10."""
    # 전일 = 수집 창 시작일 (직전 영업일)
    trd_dd = window_start.strftime("%Y%m%d")
    log.info("KRX 수집 trdDd=%s", trd_dd)

    client = _KrxClient()

    kospi = _fetch_index(client, trd_dd, Market.KOSPI)
    kosdaq = _fetch_index(client, trd_dd, Market.KOSDAQ)

    if (kospi is None or kosdaq is None) and data_go_kr_key:
        log.info("KRX 지수 일부 실패 — 공공데이터포털로 대체 시도")
        kospi = kospi or _index_from_datagokr(data_go_kr_key, trd_dd, Market.KOSPI)
        kosdaq = kosdaq or _index_from_datagokr(data_go_kr_key, trd_dd, Market.KOSDAQ)

    flows = InvestorFlows(data={})
    for investor in InvestorType:
        flows.data[investor] = {}
        for market in Market:
            flows.data[investor][market] = _fetch_flow(client, trd_dd, market, investor)

    total_flow = sum(len(v) for m in flows.data.values() for v in m.values())
    log.info("KRX 완료: 지수 %s/%s, 순매수 항목 %d",
             "O" if kospi else "X", "O" if kosdaq else "X", total_flow)
    return kospi, kosdaq, flows
