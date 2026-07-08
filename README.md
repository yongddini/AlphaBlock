# AlphaBlock

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
├── config/      # 설정 로딩 (pydantic-settings)
├── tests/       # 테스트
├── .env.example # 환경변수 예시 (.env는 커밋 금지)
├── .github/     # CI (ruff · mypy · pytest)
└── pyproject.toml
```

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

## 설정

설정은 환경변수 또는 `.env`(접두사 `ALPHABLOCK_`)로 주입한다. `config.settings.Settings` 참고.
실거래(`ALPHABLOCK_LIVE_TRADING`)는 기본 `false`이며, 검증 전까지 활성화하지 않는다.

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
