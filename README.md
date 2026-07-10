# AlphaBlock

![CI](../../actions/workflows/ci.yml/badge.svg)

오더블록(Order Block) 기반 암호화폐 자동매매 프로그램.

## 개발 환경

- OS: macOS
- Python: 3.11+
- 의존성 관리: [uv](https://docs.astral.sh/uv/)

## 셋업

```bash
# uv 설치 (미설치 시)
curl -LsSf https://astral.sh/uv/install.sh | sh

cd ~/AlphaBlock

# 의존성 설치 (런타임 + 개발)
uv sync --dev

# 환경변수 준비
cp .env.example .env   # 이후 .env 값 채우기 (커밋 금지)
```

## 프로젝트 구조

```
AlphaBlock/
├── data/        # 시세 수집·저장 (OHLCV, 웹소켓)
├── strategy/    # 오더블록 탐지·시그널 생성
├── execution/   # 주문 실행·포지션 관리
├── backtest/    # 백테스팅 엔진
├── dashboard/   # 통합 트레이딩 웹 대시보드 (Streamlit)
├── config/      # 설정 로딩 (pydantic-settings)
├── tests/       # 테스트
├── .env.example # 환경변수 예시 (.env는 커밋 금지)
├── .github/     # CI (ruff · mypy · pytest)
└── pyproject.toml
```

## 대시보드

캔들+오더블록(강세/약세·활성/무효화)+진입·청산 시그널 차트와 백테스트 성과
(수익곡선·MDD·승률·손익비·샤프·거래 목록)를 한 화면에서 확인하는 로컬 실행형
Streamlit 앱이다. 외부 노출/인증은 범위 밖이며 로컬에서만 실행한다.

```bash
uv run streamlit run dashboard/app.py
```

사이드바에서 심볼·타임프레임·기간을 바꾸면 해당 구간 OHLCV로 오더블록 탐지와
백테스트를 다시 실행해 화면을 갱신한다. 데이터는 `ALPHABLOCK_DB_PATH`(기본
`data/ohlcv.db`, WAN-6 수집 결과)에서 읽으며, 저장된 데이터가 없으면 먼저
데이터 수집을 실행하라는 안내를 보여준다.

## 품질 검사

```bash
uv run ruff check .      # 린트
uv run ruff format .     # 포맷
uv run mypy .            # 타입 체크
uv run pytest            # 테스트
```

pre-commit 훅으로 커밋 시 자동 검사:

```bash
uv run pre-commit install
```

## CI

`main` 브랜치로의 push와 모든 PR에서 [GitHub Actions](.github/workflows/ci.yml)가 로컬과 동일한 품질 게이트(`ruff check`, `ruff format --check`, `mypy`, `pytest`)를 순서대로 실행한다. 하나라도 실패하면 워크플로우가 실패로 표시된다.

## 설정

설정은 환경변수 또는 `.env`(접두사 `ALPHABLOCK_`)로 주입한다. `config.settings.Settings` 참고.
실거래(`ALPHABLOCK_LIVE_TRADING`)는 기본 `false`이며, 검증 전까지 활성화하지 않는다.

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
