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
├── live/        # 실시간 시그널 러너 + 텔레그램 알림 (페이퍼)
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

WAN-23 재설계 컨플루언스 전략을 WAN-8 백테스트 엔진에 태워 **재현 가능한 성과
리포트**를 만들고, 진입 RSI 임계값을 소규모로 스윕해 비교표를 낸다. 평가 대상
전략(TF별 자기완결):

- **진입** = 활성(비-breaker) 오더블록의 **첫 탭** + RSI 게이트(롱=과매도, 숏=과매수).
- **익절** = 진입가 너머 **가장 가까운 EMA/VWMA 선 도달**(동적, 전량 청산).
- **손절** = 진입 근거 오더블록의 **무효화(breaker, distal 경계 이탈)**.
- **동시봉 손절 우선**, **TF당 동시 1포지션**(피라미딩·역전 없음)은 전략·엔진이 보장.

각 타임프레임은 **독립 단위**로 개별 평가하며, 지표 파라미터(RSI 14, EMA 20/60/120/
240/365, VWMA 100)는 **전 TF 공통 고정**이다. 익절·손절이 모두 전략의 동적 규칙으로
결정되므로 백테스트의 고정 %손절·익절 배수는 이 전략 성과에 관여하지 않는다.

```bash
# 저장된 데이터(data/ohlcv.db, WAN-6 수집분)로 BTC 전 타임프레임 리포트 + 스윕
uv run python scripts/backtest_report.py --symbols BTC/USDT:USDT

# 여러 심볼·타임프레임을 한 번에 (기본 TF = 15m,1h,2h,4h,1d)
uv run python scripts/backtest_report.py \
    --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT

# 저장 데이터가 없어도 시드로 고정된 합성 OHLCV로 재현 가능하게 실행(데모/CI)
uv run python scripts/backtest_report.py --synthetic --timeframes 1h,4h
```

저장된 데이터가 없거나 `--synthetic`이면 시드로 고정된 합성 OHLCV
(`backtest.synthetic.make_synthetic_ohlcv`)로 대체해 **항상 결정적으로** 실행된다.

### 출력물 (`--out-dir`, 기본 `out/backtest/`)

- `<심볼>_<타임프레임>/sweep.csv` — 파라미터 조합별 성과 비교표(정렬됨).
- `<심볼>_<타임프레임>/best_trades.csv`, `best_equity.csv` — 추천(best) 조합의 거래·자본곡선.
- `sweep_combined.csv` — 모든 (심볼·TF) 조합을 한 파일로.

각 행에는 재현을 위해 **심볼·타임프레임·기간(`start_time`/`end_time`/`num_bars`)·시드·
스윕 파라미터(`rsi_oversold`/`rsi_overbought`)**가 함께 기록된다. 성과 지표는
총수익률·MDD·승률·손익비(profit factor)·샤프(타임프레임에서 유도한 연율화 계수
적용)·거래 수다.

### 스윕 축 (과적합 억제: 노브 최소화)

WAN-23 확정 설계에서는 지표(RSI14·EMA·VWMA)와 청산 규칙(선 도달 익절·오더블록
무효화 손절)이 **고정**이라 튜닝 자유도가 낮다. 따라서 `backtest.sweep.ParamGrid`는
유일하게 남는 저-자유도 진입 노브인 **진입 RSI 임계값** 한 축만 소규모로 다룬다:

- **RSI 게이트 임계값** — `rsi_overbought ∈ {70, 75, 80}` (과매도는 `100 - overbought`로 대칭).

`--sort-by`(기본 `sharpe`)로 정렬 기준을 바꾼다(`total_return`·`win_rate`·
`profit_factor`·`num_trades`도 가능). 최상위 조합이 "추천(best)"으로 상세 리포트된다.

### 해석과 기본 파라미터 제안

RSI 임계값 한 축만 스윕하므로 비교표는 각 TF에서 진입 게이트의 민감도를 보여준다.
임계값을 넓히면(예: `rsi_overbought=70`) 진입이 늘고, 좁히면(`80`) 더 선별적이 된다.
어떤 임계값이 TF별로 우위인지는 표에서 샤프·총수익률·MDD로 비교한다.

지표·청산 규칙이 고정이므로 이 스윕의 인샘플 결과만으로 `config.ConfluenceParams`
기본값을 바꾸는 것은 권하지 않는다. **소규모 표본·단일 구간**의 인샘플 성과이므로,
기본값 반영 전 **WAN-22 워크포워드/OOS**로 견고성을 확인한다. 현재 코드 기본값
(RSI 14, 과매수 70/과매도 30, EMA/VWMA 전 목표선, 오더블록 무효화 손절)은 재설계
확정 규칙 그대로 유지한다.

## 워크포워드/아웃오브샘플(OOS) 검증 (WAN-22)

WAN-19 스윕은 인샘플(IS) 성과를 최대화하는 RSI 임계값을 고르므로 특정 구간에
과적합될 위험이 있다. `backtest.walkforward`는 데이터를 **겹치지 않는 롤링
(IS, OOS) 윈도우**로 나눠, 각 윈도우에서 IS 구간으로 파라미터를 고르고(`run_sweep`
재사용) 그 값을 고정해 **보지 않은** OOS 구간에 적용한 성과를 측정한다. IS·OOS
성과 격차(`return_gap`/`sharpe_gap`, IS − OOS)가 크면 과적합 신호다.

```bash
# 저장된 데이터로 BTC 1h 워크포워드 (IS 720봉≈30일, OOS 168봉≈7일)
uv run python scripts/walkforward_report.py --symbols BTC/USDT:USDT \
    --timeframes 1h --is-bars 720 --oos-bars 168

# 저장 데이터가 없어도 시드로 고정된 합성 OHLCV로 재현 가능하게 실행(데모/CI)
uv run python scripts/walkforward_report.py --synthetic --timeframes 1h
```

- `--warmup-bars`(기본 400)는 OOS 신호 생성 시 지표(최장 EMA 365봉) 워밍업을 위해
  IS 꼬리에서 빌려오는 과거 컨텍스트일 뿐, 백테스트 엔진에는 OOS 구간의 봉만
  전달되므로 그 이전 시각의 신호는 체결되지 않는다(미래 데이터 누수 없음).
- 결과(`<out-dir>/<심볼>_<타임프레임>/walkforward.csv`)는 재현을 위해 윈도우별
  심볼·타임프레임·IS/OOS 구간·시드·선택 파라미터(`rsi_oversold`/`rsi_overbought`)와
  IS/OOS 성과 지표를 함께 기록한다.
- 룩어헤드 방지는 `tests/test_walkforward.py`에서 한 윈도우의 `oos_end` 이후 데이터를
  바꿔도 그 윈도우의 결과가 완전히 동일함을 테스트로 검증한다.

## 실시간 시그널 러너 + 텔레그램 알림 (WAN-25, 페이퍼)

WAN-23 컨플루언스 전략을 저장소(WAN-6 수집분)에 **주기적으로 재평가**해, 새 진입/
청산 신호가 뜨면 **텔레그램**으로 폰에 알림을 보내는 **페이퍼(무주문) 러너**다.
실제 주문은 하지 않고(`ALPHABLOCK_LIVE_TRADING`은 계속 `false`) 가상 포지션만
추적한다. 실주문 연결은 WAN-9의 몫이다.

- **폴링**: `ALPHABLOCK_LIVE_POLL_INTERVAL_SECONDS`(기본 60초)마다 각 시리즈(심볼·TF)를
  재평가한다. 심볼·TF는 `ALPHABLOCK_LIVE_SIGNAL_SYMBOLS`·`..._TIMEFRAMES`(기본 BTC 1h).
- **페이퍼 포지션**: 확정 진입에서 열고 계획 청산(익절=선 도달 / 손절=오더블록 무효화)
  에서 닫으며, 청산 시 실현 손익률(%)을 알림에 포함한다(TF당 동시 1포지션).
- **중복 방지**: 시리즈별 **워터마크**(마지막 처리 신호 시각)를
  `ALPHABLOCK_LIVE_SIGNAL_STATE_PATH`(기본 `data/live_signals_state.json`, 커밋 금지)에
  저장한다. 처음 보는 시리즈는 과거 신호를 조용히 프라이밍하고, 이후 새 확정봉의
  신호만 보낸다. 러너를 재시작해도 같은 신호를 다시 보내지 않는다.
- **재시도**: 네트워크 오류·HTTP 429는 지수 백오프로 재시도하고, 429의 `retry_after`를
  존중한다. 전송이 최종 실패해도 러너 루프는 멈추지 않는다.

### 텔레그램 봇 준비

1. 텔레그램에서 **@BotFather** 와 대화 → `/newbot` → 이름·유저네임을 정하면
   **봇 토큰**(`123456:ABC-...`)을 발급한다.
2. 방금 만든 봇과 대화를 **먼저 시작**(아무 메시지나 전송)한다. 봇은 자신에게
   말을 건 적 없는 사용자에게 메시지를 보낼 수 없다.
3. **chat_id 확인**: 브라우저에서
   `https://api.telegram.org/bot<토큰>/getUpdates` 를 열면 `message.chat.id` 에
   숫자 chat_id가 보인다(내게 보내려면 개인 chat_id, 그룹이면 그룹 id).
4. `.env`에 주입(커밋 금지):

   ```bash
   ALPHABLOCK_TELEGRAM_BOT_TOKEN=123456:ABC-...   # 비밀
   ALPHABLOCK_TELEGRAM_CHAT_ID=123456789
   ```

### 실행

```bash
# 텔레그램 연결 확인용 테스트 메시지 1건 전송
uv run python -m live.runner --test-message

# 상시 폴링 루프(백그라운드 상주). Ctrl+C로 종료.
uv run python -m live.runner

# 한 번만 폴링하고 종료(점검용)
uv run python -m live.runner --once

# 텔레그램 없이 메시지를 로그로만 출력(드라이런)
uv run python -m live.runner --dry-run --once
```

토큰·chat_id가 없으면 자동으로 **드라이런**(메시지를 로그로만 출력)으로 동작하므로,
설정 전에도 어떤 알림이 나갈지 로컬에서 확인할 수 있다. 저장소에 데이터가 없으면
먼저 데이터 수집(`python -m data.collector`)을 돌려 봉을 채운다.

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

## 브랜치 보호 · 머지 정책

`main`은 다음 규칙으로 보호된다(설정 스크립트: [`scripts/setup-branch-protection.sh`](scripts/setup-branch-protection.sh)):

- 모든 변경은 PR을 거친다 — `main` 직접 push 금지.
- CI 워크플로우의 **`quality`** 잡(위 품질 게이트)이 **green**이어야만 머지 가능하다. **CI가 실패한 PR은 `main`에 머지되지 않는다.**
- 머지 전 브랜치를 최신 base로 최신화(strict)하고, 리뷰 코멘트를 해결해야 한다.
- **머지는 사람이 누른다.** CI green + PM 검증 후 저장소 소유자가 직접 머지한다. Claude Code(개발 러너)는 PR을 자동 머지하지 않는다.

보호 규칙 변경에는 저장소 관리자 권한이 필요하므로, 소유자가 아래를 1회 실행해 적용한다:

```bash
./scripts/setup-branch-protection.sh
# 선택: CI green 시 자동 머지 예약을 허용하려면 (수동 머지와 병행 가능)
gh api --method PATCH /repos/yongddini/AlphaBlock -f allow_auto_merge=true
```

## 설정

설정은 환경변수 또는 `.env`(접두사 `ALPHABLOCK_`)로 주입한다. `config.settings.Settings` 참고.
실거래(`ALPHABLOCK_LIVE_TRADING`)는 기본 `false`이며, 검증 전까지 활성화하지 않는다.

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
