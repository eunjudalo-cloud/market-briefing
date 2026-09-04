"""렌더링: HTML 이메일 / plain-text / 아카이브."""

from pathlib import Path

from briefing.render import archive, renderer

from _sample_payload import sample_output, sample_payload

FIX = Path(__file__).parent / "fixtures"


def test_email_html_structure_and_escaping():
    html = renderer.render_email_html(sample_payload(), sample_output())

    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<title>데일리 마켓 브리핑 — 2026-09-04</title>" in html
    assert "투자 자문이나 매매 추천이 아닙니다" in html
    assert 'scope="col"' in html                       # 표 접근성
    assert "prefers-color-scheme:dark" in html         # 다크 모드
    assert "max-width:480px" in html                   # 모바일

    # 변수 이스케이프: 뉴스 제목의 & 가 &amp; 로
    assert "매수 우위 &amp; 지수 방어" in html
    assert "매수 우위 & 지수 방어" not in html

    # 배지 / 링크
    assert "공시" in html and "수급" in html
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260903900001" in html
    # 외부 리소스(스크립트/스타일/이미지 CDN) 없음
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert "cdn" not in html.lower() and "googleapis" not in html.lower()


def test_email_html_handles_missing_data():
    p = sample_payload()
    p.kospi = None
    p.kosdaq = None
    p.flows.data.clear()
    p.news = []
    html = renderer.render_email_html(p, sample_output())
    assert "데이터 없음" in html
    assert "수집된 헤드라인이 없습니다" in html


def test_plain_text_strips_table_pipes_and_quote():
    md = "# 제목\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n> 경고문\n"
    txt = renderer.render_plain_text(md)
    assert "|---|" not in txt
    assert "|" not in txt
    assert "※ 경고문" in txt


def test_archive_build_index(tmp_path: Path):
    (tmp_path / "브리핑_2026-09-03.md").write_text("# 3일 브리핑\n\n내용", encoding="utf-8")
    (tmp_path / "브리핑_2026-09-04.md").write_text("# 4일 브리핑\n\n최신 내용", encoding="utf-8")
    (tmp_path / "브리핑_잘못된이름.md").write_text("무시", encoding="utf-8")

    out = archive.build_index(tmp_path)
    assert out is not None and out.name == "index.html"
    html = out.read_text(encoding="utf-8")

    # 최신순
    assert html.index("2026-09-04") < html.index("2026-09-03")
    assert 'href="브리핑_2026-09-04.md"' in html
    assert "잘못된이름" not in html
    # 최신 미리보기 삽입
    assert "최신 내용" in html


def test_write_sample_fixture():
    """샘플 HTML 을 fixtures 에 저장 (시각 회귀/미리보기용)."""
    html = renderer.render_email_html(sample_payload(), sample_output())
    (FIX / "sample_briefing.html").write_text(html, encoding="utf-8")
    assert (FIX / "sample_briefing.html").stat().st_size > 2000
