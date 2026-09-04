"""OPENDART 공시 수집 + 카테고리 필터 + 접수시각 확정.

절차
  1. list.json (crtfc_key, bgn_de, end_de, 페이지네이션) 로 후보 목록 조회
  2. 보고서명 정규화 후 REPORT_NAME_CATEGORIES 매칭 ([정정] 접두 허용) — 1차 필터
  3. 접수시각 확정:
     - DART "최근공시" RSS(todayRSS.xml) 의 <pubDate> 로 rcept_no 별 정확한 시각 매핑
       (당일 공시에 대해 정확. list.json 의 rcept_dt 는 날짜 단위라 이것으로 보완)
     - RSS 에 없으면(주로 전일 저녁분) rcept_dt 날짜만 사용하고 time_parsed=False 로 표시,
       수집 창 날짜에 걸치면 보수적으로 포함
       (참고: DART 공시 상세 페이지에는 접수 '시각'이 노출되지 않아 상세 파싱 대신 RSS 를 사용)
  4. key_figures: 보고서 본문에서 계약금액/증자규모 등 best-effort 추출 (실패 시 빈 dict)
"""

from __future__ import annotations

import logging
import re
import time as _time
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import feedparser

from ..http_client import make_session, request_with_retry
from ..models import Disclosure, DisclosureCategory

log = logging.getLogger("briefing.dart")

LIST_API = "https://opendart.fss.or.kr/api/list.json"
TODAY_RSS = "https://dart.fss.or.kr/api/todayRSS.xml"
VIEWER_MAIN = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
KST = ZoneInfo("Asia/Seoul")

# PRD F2.3 — 통과 대상 보고서명(정규화 형태) → 분류
REPORT_NAME_CATEGORIES: dict[str, DisclosureCategory] = {
    "영업(잠정)실적": DisclosureCategory.EARNINGS,
    "연결재무제표기준영업(잠정)실적": DisclosureCategory.EARNINGS,
    "매출액또는손익구조30%(대규모법인15%)이상변동": DisclosureCategory.EARNINGS,
    "유상증자결정": DisclosureCategory.CAPITAL_INCREASE,
    "무상증자결정": DisclosureCategory.CAPITAL_INCREASE,
    "유무상증자결정": DisclosureCategory.CAPITAL_INCREASE,
    "감자결정": DisclosureCategory.CAPITAL_REDUCTION,
    "최대주주변경": DisclosureCategory.LARGEST_SHAREHOLDER_CHANGE,
    "최대주주변경을수반하는주식양수도계약체결": DisclosureCategory.LARGEST_SHAREHOLDER_CHANGE,
    "단일판매공급계약체결": DisclosureCategory.SUPPLY_CONTRACT,
}

_NORMALIZE_RE = re.compile(r"\s+|[·ㆍ・,()]")
_RCPNO_RE = re.compile(r"rcpNo=(\d+)")
_CORRECTION_RE = re.compile(r"\[[^\]]*정정[^\]]*\]")  # [정정] [기재정정] [첨부정정] 등


def _normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("", name)


def normalize_report_name(name: str) -> tuple[str, bool]:
    """(정규화된 보고서명, 정정공시 여부)."""
    is_correction = bool(_CORRECTION_RE.search(name))
    cleaned = _CORRECTION_RE.sub("", name)
    return _normalize(cleaned).strip(), is_correction


# 정규화된 보고서명 접두 → 분류 (긴 접두부터 검사)
_NORMALIZED_CATEGORIES: list[tuple[str, DisclosureCategory]] = sorted(
    ((_normalize(k), v) for k, v in REPORT_NAME_CATEGORIES.items()),
    key=lambda kv: len(kv[0]),
    reverse=True,
)

# 접두 매칭으로 못 잡는 표기 변형 대응 (모든 토큰이 포함되면 매칭).
# 예: "매출액또는손익구조30%(대규모법인은15%)이상변동",
#     "매출액또는손익구조30%(대규모법인은100분의15)이상변동"
_TOKEN_RULES: list[tuple[tuple[str, ...], DisclosureCategory]] = [
    (("매출액또는손익구조", "이상변동"), DisclosureCategory.EARNINGS),
]


def match_category(report_name: str) -> tuple[DisclosureCategory | None, bool]:
    normalized, is_correction = normalize_report_name(report_name)
    for key, cat in _NORMALIZED_CATEGORIES:
        if normalized == key or normalized.startswith(key):
            return cat, is_correction
    for tokens, cat in _TOKEN_RULES:
        if all(t in normalized for t in tokens):
            return cat, is_correction
    return None, is_correction


# ---------------------------------------------------------------------------
# list.json
# ---------------------------------------------------------------------------

def _scrub_key(text: str, api_key: str) -> str:
    if api_key and api_key in text:
        text = text.replace(api_key, "***")
    return re.sub(r"(crtfc_key=)[^&\s\"']+", r"\1***", text)


def _fetch_list(session, api_key: str, bgn_de: str, end_de: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "crtfc_key": api_key,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": page,
            "page_count": 100,
        }
        try:
            resp = request_with_retry(
                session, "GET", LIST_API, params=params, retries=3, timeout=15
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"OPENDART list 요청 실패: {_scrub_key(str(exc), api_key)}"
            ) from None
        data = resp.json()
        status = data.get("status")
        if status == "013":  # 조회된 데이터가 없습니다
            break
        if status != "000":
            raise RuntimeError(f"OPENDART list 오류 status={status} message={data.get('message')}")
        rows.extend(data.get("list", []))
        total_page = int(data.get("total_page", 1))
        if page >= total_page:
            break
        page += 1
    return rows


# ---------------------------------------------------------------------------
# 접수시각 확정
# ---------------------------------------------------------------------------

def _rss_time_map(session) -> dict[str, datetime]:
    """todayRSS.xml 에서 rcept_no -> 접수시각(KST) 매핑."""
    try:
        resp = request_with_retry(session, "GET", TODAY_RSS, retries=2, timeout=15)
    except Exception as exc:  # noqa: BLE001
        log.warning("DART todayRSS 요청 실패: %s — 접수시각은 날짜 단위로만 확정합니다.", exc)
        return {}

    parsed = feedparser.parse(resp.content)
    out: dict[str, datetime] = {}
    for e in parsed.entries:
        link = e.get("link") or e.get("id") or ""
        m = _RCPNO_RE.search(link)
        if not m:
            continue
        st = e.get("published_parsed") or e.get("updated_parsed")
        if not st:
            continue
        dt_utc = datetime(*st[:6], tzinfo=timezone.utc)
        out[m.group(1)] = dt_utc.astimezone(KST)
    log.info("DART todayRSS 접수시각 매핑 %d건", len(out))
    return out


# ---------------------------------------------------------------------------
# key_figures best-effort 추출
# ---------------------------------------------------------------------------

_NUM = r"(-?[0-9][0-9,]{2,})"          # 최소 3자리 숫자(콤마 포함)
_PCT = r"(-?[0-9]+(?:\.[0-9]+)?)\s*%?"


def _eok(num: str) -> str:
    """원 단위 문자열 → '약 NNN억원' (읽기용). 실패 시 원문 반환."""
    try:
        v = int(num.replace(",", ""))
    except ValueError:
        return num
    if abs(v) >= 10**8:
        return f"약 {v / 10**8:,.0f}억원"
    return f"{v:,}원"


_FIGURE_PATTERNS: dict[DisclosureCategory, list[tuple[str, str]]] = {
    DisclosureCategory.SUPPLY_CONTRACT: [
        ("계약금액", r"계약금액\s*총액\s*\(원\)\s*" + _NUM),
        ("최근매출액", r"최근\s*매출액\s*\(원\)\s*" + _NUM),
        ("매출액대비", r"매출액\s*대비\s*\(\s*%\s*\)\s*" + _PCT),
        ("계약상대", r"계약상대[^\S\r\n]{0,3}(?:방|자)?\s*[:：]?\s*([^\r\n<()%]{2,30}?)\s*(?:최근|-|\d)"),
    ],
    DisclosureCategory.CAPITAL_INCREASE: [
        ("신주의수", r"신주의?\s*수\s*\(주\)\s*" + _NUM),
        ("자금조달금액", r"(?:자금조달금액|증자전후|시설자금|운영자금)[^0-9\-]{0,12}" + _NUM),
        ("증자방식", r"증자방식\s*[:：]?\s*([가-힣제3자배정주주우선공모일반]{2,20})"),
    ],
    DisclosureCategory.CAPITAL_REDUCTION: [
        ("감자비율", r"감자\s*비율[^0-9\-]{0,8}" + _PCT),
        ("감자방법", r"감자\s*방법\s*[:：]?\s*([가-힣 ]{2,20})"),
    ],
    DisclosureCategory.EARNINGS: [
        ("매출액", r"매출액[^가-힣0-9\-]{0,12}" + _NUM),
        ("영업이익", r"영업이익[^가-힣0-9\-]{0,12}" + _NUM),
    ],
}

_TAG_RE = re.compile(r"<[^>]+>")
# 실제 페이지: viewDoc("20260903900613", "11566209", "0", "0", "0", "HTML", "")
#  - 큰/작은 따옴표, 인자 6~7개, dtd 가 "HTML" 같은 단어일 수 있음 → 앞 6개만 캡처
_VIEWDOC_RE = re.compile(
    r"""viewDoc\(\s*["'](\d+)["']\s*,\s*["'](\d+)["']\s*,"""
    r"""\s*["']([^"']*)["']\s*,\s*["']([^"']*)["']\s*,"""
    r"""\s*["']([^"']*)["']\s*,\s*["']([^"']*)["']"""
)
VIEWER_DOC = "https://dart.fss.or.kr/report/viewer.do"
_FIGURE_EXTRACT_MAX = 20  # 하루 공시가 많을 때 상세 조회 상한
_FIGURE_REQUEST_GAP = 0.4  # 상세 요청 간 최소 간격(초)


def _extract_key_figures(session, rcept_no: str, category: DisclosureCategory) -> dict[str, str]:
    patterns = _FIGURE_PATTERNS.get(category)
    if not patterns:
        return {}
    try:
        main = request_with_retry(
            session, "GET", VIEWER_MAIN.format(rcept_no=rcept_no), retries=2, timeout=15
        )
        m = _VIEWDOC_RE.search(main.text)
        if not m:
            log.debug("viewDoc 파라미터 매칭 실패 rcept_no=%s", rcept_no)
            return {}
        rcp, dcm, ele, off, length, dtd = m.groups()
        viewer = request_with_retry(
            session, "GET", VIEWER_DOC,
            params={
                "rcpNo": rcp, "dcmNo": dcm, "eleId": ele or "0",
                "offset": off or "0", "length": length or "0", "dtd": dtd or "HTML",
            },
            retries=2, timeout=20,
        )
        viewer.encoding = viewer.apparent_encoding or viewer.encoding
        text = re.sub(r"\s+", " ", _TAG_RE.sub(" ", viewer.text))
        _MONEY = {"계약금액", "최근매출액", "자금조달금액", "매출액", "영업이익"}
        _RATE = {"매출액대비", "감자비율"}
        figures: dict[str, str] = {}
        for label, pat in patterns:
            mm = re.search(pat, text)
            if not mm:
                continue
            val = mm.group(1).strip()
            if label in _MONEY:
                val = _eok(val)
            elif label in _RATE:
                val = f"{val}%"
            figures[label] = val
        return figures
    except Exception as exc:  # noqa: BLE001
        log.debug("key_figures 추출 실패 rcept_no=%s: %s", rcept_no, exc)
        return {}


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

def collect(
    target_date: date,
    window_start: datetime,
    window_end: datetime,
    *,
    api_key: str = "",
    extract_figures: bool = True,
) -> list[Disclosure]:
    if not api_key:
        log.warning("OPENDART_API_KEY 미설정 — 공시 수집을 건너뜁니다.")
        return []

    session = make_session()
    bgn_de = window_start.astimezone(KST).strftime("%Y%m%d")
    end_de = window_end.astimezone(KST).strftime("%Y%m%d")
    window_dates = {
        window_start.astimezone(KST).date(),
        window_end.astimezone(KST).date(),
    }

    raw = _fetch_list(session, api_key, bgn_de, end_de)
    log.info("DART list 원본 %d건 (%s~%s)", len(raw), bgn_de, end_de)

    time_map = _rss_time_map(session)

    out: list[Disclosure] = []
    seen_rcept: set[str] = set()
    for row in raw:
        report_nm = row.get("report_nm", "")
        category, is_correction = match_category(report_nm)
        if category is None:
            continue

        rcept_no = str(row.get("rcept_no", "")).strip()
        rcept_dt = str(row.get("rcept_dt", "")).strip()
        if rcept_no in seen_rcept:
            continue
        seen_rcept.add(rcept_no)

        exact = time_map.get(rcept_no)
        if exact is not None:
            received_at = exact
            time_parsed = True
            if not (window_start <= received_at <= window_end):
                continue  # 정확한 시각이 창 밖
        else:
            try:
                d = datetime.strptime(rcept_dt, "%Y%m%d").date()
            except ValueError:
                continue
            if d not in window_dates:
                continue
            # 시각 미상: 정렬/표기 오해를 줄이려 정오로 둔다(00:00 이 아님)
            received_at = datetime.combine(d, time(12, 0), tzinfo=KST)
            time_parsed = False

        out.append(
            Disclosure(
                corp_name=row.get("corp_name", "").strip(),
                stock_code=(row.get("stock_code") or "").strip() or None,
                report_name=report_nm.strip(),
                category=category,
                received_at=received_at,
                url=VIEWER_MAIN.format(rcept_no=rcept_no),
                key_figures={},
                is_correction=is_correction,
                time_parsed=time_parsed,
            )
        )

    # 정렬: 날짜 → (시각확정 우선) → 시각
    out.sort(key=lambda x: (x.received_at.date(), not x.time_parsed, x.received_at))

    # key_figures 는 상위 N건만, 요청 간 간격을 두고 추출
    if extract_figures:
        for i, d in enumerate(out[:_FIGURE_EXTRACT_MAX]):
            rc = _RCPNO_RE.search(d.url)
            if not rc:
                continue
            if i:
                _time.sleep(_FIGURE_REQUEST_GAP)
            d.key_figures = _extract_key_figures(session, rc.group(1), d.category)

    log.info(
        "DART 대상 공시 %d건 (시각확정 %d, 날짜만 %d, 수치추출 %d)",
        len(out),
        sum(1 for d in out if d.time_parsed),
        sum(1 for d in out if not d.time_parsed),
        sum(1 for d in out if d.key_figures),
    )
    return out
