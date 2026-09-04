"""CLI 진입점: python -m briefing [--date YYYY-MM-DD] [--force] [--send]"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from .logging_setup import setup_logging
from .pipeline import run


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="briefing",
        description="데일리 마켓 브리핑 생성기",
    )
    parser.add_argument(
        "--date", type=_parse_date, default=None,
        help="대상일 YYYY-MM-DD (기본: 오늘, KST)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="기존 산출물이 있어도 재생성",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="메일 발송 생략 (기본값)",
    )
    parser.add_argument(
        "--send", dest="dry_run", action="store_false",
        help="메일 실제 발송 (SMTP_* / MAIL_* 설정 필요)",
    )
    args = parser.parse_args(argv)

    setup_logging(args.date)
    return run(args.date, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
