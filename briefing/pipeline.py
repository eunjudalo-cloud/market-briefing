"""오케스트레이션: 영업일 판정 → 수집 → 스냅샷 → 분석 → 렌더 → (발송).

부분 실패 허용: 한 수집기가 실패해도 나머지로 브리핑을 생성한다.
치명 오류(렌더 불가 등)만 종료코드 1.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import calendar_kr
from .analyze import openai_client
from .collectors import dart, krx, rss
from .config import DATA_DIR, OUTPUT_DIR, get_settings, load_feeds
from .deliver import mailer
from .models import BriefingOutput, BriefingPayload, InvestorFlows
from .render import archive, renderer

log = logging.getLogger("briefing.pipeline")


def _window(target_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    prev = calendar_kr.prev_business_day(target_date)
    start = datetime.combine(prev, time(18, 0), tzinfo=tz)
    end = datetime.combine(target_date, time(7, 0), tzinfo=tz)
    return start, end


def _snapshot(target_date: date, name: str, obj: object) -> None:
    folder = DATA_DIR / target_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("스냅샷 저장: %s", path)


def run(
    target_date: date | None = None,
    *,
    force: bool = False,
    dry_run: bool = True,
) -> int:
    settings = get_settings()
    tz = ZoneInfo(settings.briefing_tz)

    if target_date is None:
        target_date = datetime.now(tz).date()

    log.info(
        "=== 브리핑 파이프라인 시작: %s (force=%s, dry_run=%s) ===",
        target_date, force, dry_run,
    )

    if not calendar_kr.is_business_day(target_date):
        log.info("%s 은(는) 영업일이 아닙니다. 종료합니다.", target_date)
        return 0

    out_path = OUTPUT_DIR / f"브리핑_{target_date.isoformat()}.md"
    if out_path.exists() and not force:
        log.info("이미 생성됨(멱등 스킵): %s", out_path)
        return 0

    window_start, window_end = _window(target_date, tz)
    log.info("수집 창: %s ~ %s", window_start, window_end)

    # --- 수집 (부분 실패 허용) ---
    kospi = kosdaq = None
    flows: InvestorFlows | None = None
    if settings.include_krx:
        try:
            kospi, kosdaq, flows = krx.collect(
                target_date, window_start, window_end,
                data_go_kr_key=settings.data_go_kr_key,
            )
            _snapshot(target_date, "krx", {
                "kospi": kospi.model_dump(mode="json") if kospi else None,
                "kosdaq": kosdaq.model_dump(mode="json") if kosdaq else None,
                "flows": flows.model_dump(mode="json") if flows else None,
            })
        except Exception:
            log.exception("KRX 수집 실패 — 지수/수급 섹션을 비웁니다.")
    else:
        log.info("KRX 수집 비활성화 (include_krx=false) — 지수·수급 섹션 생략")

    disclosures = []
    try:
        disclosures = dart.collect(
            target_date, window_start, window_end,
            api_key=settings.opendart_api_key,
        )
        _snapshot(target_date, "dart", [d.model_dump(mode="json") for d in disclosures])
    except Exception:
        log.exception("DART 수집 실패 — 공시 근거 없이 진행합니다.")

    news = []
    try:
        feeds = load_feeds()
        news = rss.collect(
            feeds.feeds, window_start, window_end,
            feeds.max_items or settings.news_max_items,
        )
        _snapshot(target_date, "rss", [n.model_dump(mode="json") for n in news])
        log.info("RSS 헤드라인 %d건", len(news))
    except Exception:
        log.exception("RSS 수집 실패 — 뉴스 섹션을 비웁니다.")

    payload = BriefingPayload(
        target_date=target_date,
        window_start=window_start,
        window_end=window_end,
        kospi=kospi,
        kosdaq=kosdaq,
        flows=flows or InvestorFlows(),
        disclosures=disclosures,
        news=news,
    )

    # --- 분석 ---
    try:
        output = openai_client.generate(
            payload,
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            tz=tz,
        )
    except Exception:
        log.exception("분석 실패 — 최소 요약으로 대체합니다.")
        output = BriefingOutput(
            market_summary="분석 생성에 실패했습니다.",
            picks=[],
            generated_at=datetime.now(tz),
            fallback_used=True,
        )

    # --- 렌더 (Markdown 실패 시 치명, HTML 실패는 폴백) ---
    try:
        md = renderer.render_markdown(payload, output)
        renderer.write_markdown(md, out_path)
    except Exception:
        log.exception("렌더링 실패 — 브리핑을 생성하지 못했습니다.")
        return 1

    html_body: str | None = None
    try:
        html_body = renderer.render_email_html(payload, output)
        (OUTPUT_DIR / f"브리핑_{target_date.isoformat()}.html").write_text(
            html_body, encoding="utf-8"
        )
    except Exception:
        log.exception("HTML 렌더링 실패 — 메일은 Markdown 변환본으로 대체합니다.")

    # --- 아카이브 index.html 갱신 ---
    try:
        archive.build_index(OUTPUT_DIR)
    except Exception:
        log.exception("아카이브 생성 실패(무시).")

    # --- 발송 ---
    sent = mailer.send(
        subject=f"[마켓 브리핑] {target_date.isoformat()}",
        markdown_body=md,
        html_body=html_body,
        md_path=out_path,
        settings=settings,
        dry_run=dry_run,
    )

    # --- 종료코드 (스케줄러 재시도 판단용) ---
    empty = not (kospi or kosdaq or disclosures or news
                 or any(v for m in payload.flows.data.values() for v in m.values()))
    log.info("=== 완료: %s ===", out_path)
    if empty:
        log.warning("모든 데이터 소스가 비어 있습니다 (빈 브리핑).")
        return 3
    if not dry_run and not sent:
        log.warning("메일 발송에 실패했습니다.")
        return 2
    return 0
