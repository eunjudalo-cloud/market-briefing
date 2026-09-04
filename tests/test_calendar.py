"""영업일 판정 + 연도 커버리지."""

from datetime import date

from briefing import calendar_kr


def test_weekend_and_known_holidays():
    assert not calendar_kr.is_business_day(date(2026, 9, 5))   # 토
    assert not calendar_kr.is_business_day(date(2026, 9, 6))   # 일
    assert not calendar_kr.is_business_day(date(2026, 9, 25))  # 추석
    assert calendar_kr.is_business_day(date(2026, 9, 4))       # 금, 영업일


def test_prev_business_day_skips_weekend():
    # 2026-09-07 월요일 → 직전 영업일 2026-09-04 금요일
    assert calendar_kr.prev_business_day(date(2026, 9, 7)) == date(2026, 9, 4)


def test_2027_covered():
    assert 2027 in calendar_kr._COVERED_YEARS
    assert not calendar_kr.is_business_day(date(2027, 1, 1))   # 신정


def test_uncovered_year_warns(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="briefing.calendar"):
        calendar_kr.is_holiday(date(2035, 1, 2))
    assert any("공휴일 테이블" in r.message for r in caplog.records)
