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

## 백테스트 성과 리포트 & 파라미터 스윕 (WAN-19)

WAN-18 컨플루언스 시그널(오더블록 + RSI·EMA·VWMA 게이트)을 WAN-8 백테스트
엔진에 태워 **재현 가능한 성과 리포트**를 만들고, 핵심 파라미터를 소규모 그리드로
스윕해 비교표를 낸다.

```bash
# 저장된 데이터(data/ohlcv.db, WAN-6 수집분)로 BTC 1h 리포트 + 스윕
uv run python scripts/backtest_report.py --symbols BTC/USDT:USDT --timeframes 1h

# 여러 심볼·타임프레임을 한 번에
uv run python scripts/backtest_report.py \
    --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT --timeframes 15m,1h,4h

# 저장 데이터가 없어도 시드로 고정된 합성 OHLCV로 재현 가능하게 실행(데모/CI)
uv run python scripts/backtest_report.py --synthetic --timeframes 1h,4h
```

저장된 데이터가 없거나 `--synthetic`이면 시드로 고정된 합성 OHLCV
(`backtest.synthetic.make_synthetic_ohlcv`)로 대체해 **항상 결정적으로** 실행된다.

### 출력물 (`--out-dir`, 기본 `out/backtest/`)

- `<심볼>_<타임프레임>/sweep.csv` — 파라미터 조합별 성과 비교표(정렬됨).
- `<심볼>_<타임프레임>/best_trades.csv`, `best_equity.csv` — 추천(best) 조합의 거래·자본곡선.
- `sweep_combined.csv` — 모든 조합을 한 파일로.

각 행에는 재현을 위해 **심볼·타임프레임·기간(`start_time`/`end_time`/`num_bars`)·시드·
스윕 파라미터**가 함께 기록된다. 성과 지표는 총수익률·MDD·승률·손익비(profit factor)·
샤프(타임프레임에서 유도한 연율화 계수 적용)·거래 수다.

### 스윕 축 (과적합 방지: 3축 × 소수 값 = 12 조합)

`backtest.sweep.ParamGrid`가 정의하는 3개 축만 다룬다:

1. **RSI 게이트 임계값** — `rsi_overbought ∈ {70, 75, 80}` (과매도는 `100 - overbought`로 대칭).
2. **EMA 추세 편향 on/off** — `use_ema_trend ∈ {True, False}`.
3. **손절·익절 배수** — `(stop_loss_pct, take_profit_pct) ∈ {(2%,4%), (3%,6%)}`.

`--sort-by`(기본 `sharpe`)로 정렬 기준을 바꾼다(`total_return`·`win_rate`·
`profit_factor`·`num_trades`도 가능). 최상위 조합이 "추천(best)"으로 상세 리포트된다.

### 해석과 기본 파라미터 제안

저장된 BTC/USDT:USDT 1h 구간에서의 예시 스윕에서는 **EMA 추세 게이트를 끄고
(`use_ema_trend=False`) 손절 2%·익절 4%**로 둔 조합이 샤프 기준 최상위였다(총수익률
약 +5.7%, MDD 약 3.1%). RSI 임계값 축은 이 구간에서 결과에 유의미한 차이를 만들지
않았다(거래 수가 적어 RSI 게이트가 결정 요인이 아니었음). 다만 이는 **소규모 표본의
단일 구간 예시**이므로, `config.ConfluenceParams`/`BacktestConfig` 기본값을 바꾸기
전에 더 넓은 기간·심볼에서 재확인할 것을 권한다(워크포워드·교차검증은 WAN-19 범위 밖,
후속 이슈). 현재 코드 기본값(전 게이트 활성, 완전 컨플루언스)은 **보수적(선별적)**
설정으로 유지한다.

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
