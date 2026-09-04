"""한국거래소(KRX) 영업일 판정.

주말 + 공휴일 하드코딩. KRX 임시휴장(연말 폐장일 등)은 목록에 직접 반영해야 한다.
표에 없는 연도는 주말만 반영되므로, 매년 KRX 휴장일 안내로 갱신할 것.
TODO: 지수 데이터가 비어 있으면 임시휴장으로 간주하는 훅.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

log = logging.getLogger("briefing.calendar")

# 2026년 KRX 정규장 휴장일. 대체공휴일 포함. 구현 확정 전 재검증 필요.
KRX_HOLIDAYS: set[date] = {
    date(2026, 1, 1),    # 신정
    date(2026, 2, 16),   # 설 연휴
    date(2026, 2, 17),   # 설날
    date(2026, 2, 18),   # 설 연휴
    date(2026, 3, 1),    # 삼일절
    date(2026, 3, 2),    # 삼일절 대체공휴일(3/1 일요일)
    date(2026, 5, 5),    # 어린이날
    date(2026, 5, 24),   # 부처님오신날
    date(2026, 5, 25),   # 부처님오신날 대체공휴일(5/24 일요일)
    date(2026, 6, 6),    # 현충일(토요일, 대체 없음)
    date(2026, 8, 15),   # 광복절
    date(2026, 8, 17),   # 광복절 대체공휴일(8/15 토요일)
    date(2026, 9, 24),   # 추석 연휴
    date(2026, 9, 25),   # 추석
    date(2026, 9, 26),   # 추석 연휴(토요일)
    date(2026, 9, 28),   # 추석 대체공휴일
    date(2026, 10, 3),   # 개천절
    date(2026, 10, 5),   # 개천절 대체공휴일(10/3 토요일)
    date(2026, 10, 9),   # 한글날
    date(2026, 12, 25),  # 성탄절
    date(2026, 12, 31),  # 연말 폐장일(KRX 휴장)
    # --- 2027 (KRX 공식 휴장일 안내로 재검증 필요. 임시선거일 등 추가 가능) ---
    date(2027, 1, 1),    # 신정
    date(2027, 2, 8),    # 설 연휴
    date(2027, 2, 9),    # 설날
    date(2027, 2, 10),   # 설 연휴
    date(2027, 3, 1),    # 삼일절
    date(2027, 5, 5),    # 어린이날
    date(2027, 5, 13),   # 부처님오신날
    date(2027, 6, 7),    # 현충일 대체공휴일(6/6 일요일)
    date(2027, 8, 16),   # 광복절 대체공휴일(8/15 일요일)
    date(2027, 9, 14),   # 추석 연휴
    date(2027, 9, 15),   # 추석
    date(2027, 9, 16),   # 추석 연휴
    date(2027, 10, 4),   # 개천절 대체공휴일(10/3 일요일)
    date(2027, 10, 11),  # 한글날 대체공휴일(10/9 토요일)
    date(2027, 12, 25),  # 성탄절
    date(2027, 12, 31),  # 연말 폐장일
}

_COVERED_YEARS = {d.year for d in KRX_HOLIDAYS}


def is_holiday(d: date) -> bool:
    if d.year not in _COVERED_YEARS:
        log.warning(
            "%d년 공휴일 테이블이 없습니다 — 주말만 반영됩니다. calendar_kr.KRX_HOLIDAYS 갱신 필요.",
            d.year,
        )
    return d in KRX_HOLIDAYS


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def prev_business_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while not is_business_day(cur):
        cur -= timedelta(days=1)
    return cur
