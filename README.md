# 데일리 마켓 브리핑 자동화

매 영업일 오전 7시(KST) 기준으로 전일 시장 데이터 · 마감 후 공시 · 뉴스 헤드라인을 수집해
브리핑(`.md`)을 만들고 이메일로 보낸다. 상세 사양은 [`PRD.md`](PRD.md), 개발 절차는
[`개발_프롬프트.md`](개발_프롬프트.md) 참조.

## 현재 상태 — 3단계(이메일/브리핑 디자인)

- **HTML 이메일** `render/template.email.html.j2`: 인라인 CSS, table 레이아웃, 640px,
  모바일(`max-width:480px`) 반응형, 라이트/다크(`prefers-color-scheme`), 시스템 폰트,
  외부 리소스 없음. 상승=빨강/하락=파랑, `[공시]`/`[수급]` 배지, 표 `th scope`.
- **plain-text 파트**: Markdown 정돈본(`render_plain_text`).
- **아카이브** `output/index.html`: 과거 브리핑 목록(최신순) + 최신 미리보기. 정적.
- 파이프라인이 `.md` + `.html` + `index.html` 을 생성하고, 메일은 HTML+plain 멀티파트로 발송.
- 4단계: 전체 검토.

### 이전 단계 요약 — 2단계(실제 수집·분석·발송)

- **RSS**: feedparser 로 5개 매체 실수집. 수집 창 필터, 제목 정규화 중복 제거,
  시장·종목 키워드 우선 정렬, 최대 15건. 제목·링크·매체·발행시각만 저장(본문 미저장).
- **OPENDART**: `list.json` + 카테고리 필터(F2.3) + 접수시각 확정.
  `OPENDART_API_KEY` 필요. 키 없으면 공시 섹션은 건너뛴다.
- **KRX**: `getJsonData.cmd` + 공공데이터포털(지수) 폴백. 실패 시 "데이터 없음"으로 진행.
- **분석(OpenAI)**: `gpt-4o` 1회 호출, JSON 스키마 강제, 근거 검증, 금지표현/미근거 지수언급
  필터, 실패 시 규칙 기반 폴백. `OPENAI_API_KEY` 필요.
- **메일**: SMTP STARTTLS 발송 구현. 기본은 `--dry-run`(미발송). `--send` 로 실제 발송.
- 3단계에서 이메일 HTML 디자인, 4단계에서 전체 검토.

### 알려진 제약 / 확인 필요

| 항목 | 내용 |
|---|---|
| KRX `bld` 코드 | 공개 문서가 없어 관례값 사용 — 실제 거래일 응답으로 1회 검증 필요. 개발 환경에서는 KRX 가 자동화 요청을 차단(LOGOUT/400)해 지수·수급이 비어 나올 수 있음. |
| DART 접수시각 | 공시 상세 페이지에 '시각'이 없어, DART 최근공시 RSS(`todayRSS.xml`)의 `pubDate` 로 확정. 전일 저녁분은 RSS 범위를 벗어나면 날짜만 사용하고 `(시각 추정)` 표기 후 보수적으로 포함. |
| RSS User-Agent | 한국경제 등은 데스크톱 Chrome UA 를 403 차단 → 피드 리더용 UA 로 요청. |

## 설치

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # 값은 2단계부터 필요
```

Python 3.11 기준. Windows에서 시간대(zoneinfo) 사용을 위해 `tzdata` 가 포함되어 있다.

## 실행

```
python -m briefing --date 2026-09-03 --dry-run
```

옵션:

| 옵션 | 설명 |
|---|---|
| `--date YYYY-MM-DD` | 대상일 (기본: 오늘, KST) |
| `--force` | 기존 산출물이 있어도 재생성 |
| `--dry-run` | 메일 미발송 (기본값) |
| `--send` | 메일 실제 발송 (SMTP_* / MAIL_* 설정 필요) |

`.env` 에 `OPENAI_API_KEY` 가 있으면 요약·종목 선별에 `gpt-4o` 를 쓰고,
없으면 규칙 기반으로 대체한다. `OPENDART_API_KEY` 가 없으면 공시 섹션은 비운다.

비영업일(주말·공휴일)이면 아무 것도 생성하지 않고 로그만 남긴다.
당일 산출물이 이미 있으면 `--force` 없이는 건너뛴다(멱등).

## 산출물

| 경로 | 내용 |
|---|---|
| `output/브리핑_YYYY-MM-DD.md` | 브리핑 본문 |
| `data/YYYY-MM-DD/*.json` | 수집 원본 스냅샷 (재현용) |
| `logs/run_YYYYMMDD.log` | 실행 로그 (비밀값 마스킹) |

## 구조

```
briefing/
  __main__.py        CLI 진입점
  config.py          .env(Settings) + feeds.yaml 로딩
  models.py          정규화 데이터 모델 (pydantic)
  pipeline.py        오케스트레이션
  calendar_kr.py     영업일 판정
  logging_setup.py   로거 + 비밀값 마스킹
  http_client.py     공용 HTTP(타임아웃/재시도, 4xx 즉시 중단)
  collectors/        krx.py / dart.py / rss.py
  analyze/           openai_client.py (OpenAI 호출 + 검증 + 규칙 기반 폴백)
  render/            renderer.py + template.md.j2 (+ markdown_to_html)
  deliver/           mailer.py (SMTP STARTTLS)
config/feeds.yaml    RSS 피드 목록
tests/               스모크 + 수집기/분석기/메일러 단위 테스트, fixtures/
```

## 테스트

```
pytest -q
```
