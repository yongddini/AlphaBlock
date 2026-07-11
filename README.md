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
├── live/        # 실시간 시그널 러너 + 텔레그램 알림 (페이퍼) · 하트비트
├── backtest/    # 백테스팅 엔진
├── paper/       # 페이퍼 거래 영속·성과 집계·백테스트 대비 패리티 (WAN-33)
├── dashboard/   # 통합 트레이딩 웹 대시보드 (Streamlit)
├── cli/         # 실행 CLI (alphablock collect/live/status)
├── config/      # 설정 로딩 (pydantic-settings)
├── scripts/     # 운영 스크립트 (launchd 데몬 설치·해제 등)
├── tests/       # 테스트
├── .env.example # 환경변수 예시 (.env는 커밋 금지)
├── .github/     # CI (ruff · mypy · pytest)
└── pyproject.toml
```

## 대시보드

세 개의 탭(분석 · 페이퍼 성과 · 운영 상태)으로 구성된 로컬 실행형 Streamlit 앱이다.
외부 노출/인증은 범위 밖이며 로컬에서만 실행한다.

```bash
uv run streamlit run dashboard/app.py
```

**분석 탭 (WAN-15)** — 캔들+오더블록(강세/약세·활성/무효화)+진입·청산 시그널 차트와
백테스트 성과(수익곡선·MDD·승률·손익비·샤프·거래 목록)를 한 화면에서 확인한다.
사이드바에서 심볼·타임프레임·기간을 바꾸면 해당 구간 OHLCV로 오더블록 탐지와
백테스트를 다시 실행해 화면을 갱신한다. 데이터는 `ALPHABLOCK_DB_PATH`(기본
`data/ohlcv.db`, WAN-6 수집 결과)에서 읽으며, 저장된 데이터가 없으면 먼저
데이터 수집을 실행하라는 안내를 보여준다.

### 운영 상태(Health) 탭 (WAN-30)

"시스템이 지금 잘 돌고 있는가?"를 한 화면으로 확인한다. 상단 **종합 상태 배지**
(정상 / 일부 지연 / 멈춤)와 새로고침 버튼 아래에 다섯 섹션을 보여준다.

- **데이터 신선도** — 시리즈(심볼·TF)별 최신 봉 시각·지연·봉 수. TF 주기 대비
  지연이 `ALPHABLOCK_HEALTH_STALE_MULTIPLIER`(기본 2.5)배를 넘으면 🔴 지연으로
  표시해 수집이 멈췄음을 즉시 식별한다.
- **펀딩비 상태** — 심볼별 최신 펀딩비·다음 정산 시각·예측/확정 구분·갱신 지연.
- **실시간 러너 상태** — 마지막 폴링 시각(하트비트)·마지막 알림 시각, 살아있음/멈춤
  판정. 러너가 돈 흔적이 없으면 "미실행"으로 안내한다.
- **현재 페이퍼 포지션** — 시리즈별 방향·진입가/시각·현재가·미실현 손익(%)·익절선·손절가.
- **최근 신호/알림** — 최근 N건(진입/익절/손절)의 시각·심볼·TF·방향·가격.

러너 상태·포지션·신호 이력은 시그널 러너(WAN-25)가 매 폴링마다 남기는 운영 상태
파일 `ALPHABLOCK_LIVE_RUNTIME_STATE_PATH`(기본 `data/live_runtime_state.json`,
커밋 금지)에서 읽는다. 실주문/`live_trading`은 건드리지 않고 페이퍼 상태만 표시한다.

## 페이퍼 트레이딩 성과 & 백테스트 대비 패리티 (WAN-33)

WAN-25 페이퍼 러너가 **실제로 무슨 거래를 냈고 그게 돈이 됐는지**, 그리고 **백테스트가
약속한 성과와 비슷한지**를 집계한다. 실계좌(WAN-9/WAN-27) 전환 판단의 근거다.

### 거래 영속 (`paper_trades` 테이블)

러너가 익절/손절로 포지션을 닫을 때마다 그 거래를 `ALPHABLOCK_DB_PATH`(기본
`data/ohlcv.db`)의 `paper_trades` 테이블에 **거래 단위로 누적**한다. 심볼·TF·방향·
진입/청산 시각·가격·사유와 함께 손익을 저장한다:

- `gross_pct` — 방향을 반영한 가격 손익률(%).
- `fee_pct` — 왕복 수수료 비용률(`2 × ALPHABLOCK_PAPER_FEE_RATE × 100`).
- `funding_pct` — 보유 구간 펀딩비용률(WAN-16 수집분 + WAN-20 모델 재사용).
- `net_pct` — 모든 비용을 반영한 순손익률 = `gross − fee − funding`.
- `risk_pct` / `r_multiple` — 손절 거리(오더블록 무효화) 기준 리스크와 **R 배수**.

`(symbol, timeframe, entry_time, exit_time)`을 기본키로 UPSERT하므로 러너 재시작·
재평가로 같은 거래가 다시 들어와도 중복되지 않는다. 기록 실패는 러너 폴링·알림을
막지 않는다(견고성). 실주문/`live_trading`은 건드리지 않는 **페이퍼 한정**이다.

### 리포트 CLI

```bash
# 누적된 페이퍼 거래로 성과(전체·심볼/TF별) + 백테스트 대비 패리티 리포트
uv run python scripts/paper_report.py

# 특정 기간·심볼만, 패리티 없이 성과만
uv run python scripts/paper_report.py --symbols BTC/USDT:USDT --start 2024-01-01 --no-parity
```

- **성과 집계** — 총 PnL(복리 %·R 배수 합)·승률·손익비(payoff)·손익 팩터(profit
  factor)·MDD·거래 수를 전체 및 심볼·TF별로 낸다.
- **패리티** — 같은 기간·시리즈를 WAN-8 백테스트로 재실행해 **거래 수·승률·평균 R**을
  비교한다. 손익 비용(수수료·펀딩비)은 양쪽에 동일하게 적용해, 남는 차이가 거래
  선택·체결 타이밍(실시간 데이터 지연·프라이밍 등)에서 비롯되도록 한다. 차이가
  임계값을 넘는 시리즈는 `⚠`로 표시한다.
- 결과 표는 `--out-dir`(기본 `out/paper/`)에 CSV(거래 원장·성과 요약·패리티)로 저장한다.

### 대시보드 "페이퍼 성과" 탭

같은 성과·패리티를 대시보드에서 표로 보고 **CSV로 내보낼** 수 있다(전체 성과 지표,
시리즈별 표, 거래 원장, 패리티 비교표 + 다운로드 버튼). 누적된 페이퍼 거래가 없으면
러너를 먼저 돌리라고 안내한다.

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
- **운영 상태 기록**: 매 폴링마다 하트비트·현재 페이퍼 포지션·최근 신호 이력을
  `ALPHABLOCK_LIVE_RUNTIME_STATE_PATH`(기본 `data/live_runtime_state.json`, 커밋 금지)에
  남겨, 대시보드 **운영 상태(Health) 탭**(WAN-30)이 러너 생존·포지션·신호를 읽는다.

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

## 상시 구동: 실행 CLI + 데몬화 (WAN-31)

수집기와 시그널 러너를 **한 줄 명령**으로 실행하고, macOS `launchd`로 **로그인 시
자동 시작·크래시 시 자동 재시작**되게 상주시킨다. 상시로 돌아야 최신 봉 지연이 TF
주기 수준으로 유지되고, 실시간 시그널·알림(WAN-25)도 의미가 있다.

> 안전: 데몬은 **수집·시그널·알림까지만** 한다. 실주문은 없다(`live_trading=false` 유지).

### 실행 CLI (`alphablock`)

`pyproject`의 `[project.scripts]`로 노출된다. 기존 `python -m ...` 진입점을 감싼다.

```bash
uv run alphablock collect          # 데이터 수집기(백필 + 실시간 스트림 상주)
uv run alphablock collect --once   # 백필만 1회 수행하고 종료
uv run alphablock collect --no-repair-on-start  # 시작 시 갭 자동 복구 끄기(WAN-35)
uv run alphablock backfill --repair  # 저장된 시리즈의 내부 갭을 1회 탐지·복구 — WAN-35
uv run alphablock live             # 실시간 시그널 러너(페이퍼) 상주
uv run alphablock live --once      # 러너 1회 폴링 후 종료(점검)
uv run alphablock status           # 운영 상태(Health) 요약을 콘솔에 출력
uv run alphablock watch            # 운영 상태 워치(이상 시 텔레그램 경고) 상주 — WAN-32
```

`alphablock status`는 대시보드 Health 탭과 같은 판정을 텍스트로 보여준다 —
수집기·러너 생존, 데이터 신선도, 오픈 페이퍼 포지션을 한눈에 확인한다.

### 하트비트

수집기는 스트림 수신 메시지마다, 러너는 매 폴링마다 **하트비트(마지막 동작 시각)**를
상태 파일(`data/collector_heartbeat.json`, `data/live_runtime_state.json`)에 남긴다.
Health 대시보드/`alphablock status`가 이 값으로 "프로세스가 살아있는지"를 판정한다
(기대 간격의 `ALPHABLOCK_HEALTH_STALE_MULTIPLIER`배를 넘겨 갱신이 없으면 **멈춤**).

### 데몬 설치·해제 (macOS launchd)

`scripts/`의 설치 스크립트가 plist 템플릿(`scripts/launchd/*.template`)의 경로
자리표시자를 실제 값(저장소 경로·`uv` 경로·로그 경로)으로 치환해
`~/Library/LaunchAgents/`에 설치하고 로드한다. `RunAtLoad`(로그인 시 시작)와
`KeepAlive`(크래시 시 재시작), 절전 방지를 위한 `caffeinate` 래핑을 적용한다.

```bash
./scripts/install-daemons.sh            # 수집기 + 러너 둘 다 설치·시작
./scripts/install-daemons.sh collector  # 수집기만
./scripts/install-daemons.sh live       # 러너만

./scripts/uninstall-daemons.sh          # 둘 다 해제(로그는 남김)
```

- **재부팅·크래시 자동 복구**: 로그인하면 `RunAtLoad`로 다시 뜨고, 프로세스가 죽으면
  `KeepAlive`가 `ThrottleInterval`(10초) 간격으로 되살린다.
- **로그**: 기본 `logs/collector.log`, `logs/live.log`(리포지토리 밖 위치는
  `ALPHABLOCK_LOG_DIR`로 지정). `logs/`는 `.gitignore`에 포함된다.

```bash
launchctl list | grep alphablock                 # 등록·구동 상태
tail -f logs/collector.log logs/live.log         # 실시간 로그
uv run alphablock status                          # 상태 요약
```

로그 파일이 무한정 커지지 않게 하려면 macOS `newsyslog`(예: `/etc/newsyslog.d/`에
`alphablock.conf` 추가)로 로테이션을 설정한다.

## 운영 상태 자동 경고 (WAN-32)

Health 대시보드(WAN-30)·`alphablock status`는 화면을 **열어야** 상태를 볼 수 있다.
`alphablock watch`는 같은 판정 로직을 **주기적으로(기본 10분) 자동 점검**해, 수집·러너가
조용히 멈추면 **텔레그램으로 폰에 경고**를 보낸다(WAN-25의 전송 경로 재사용). 화면을 보고
있지 않아도 "수집이 하루 넘게 멈췄는데 아무도 몰랐다"는 상황을 막는다.

```bash
uv run alphablock watch                 # 상주(기본 10분마다 점검)
uv run alphablock watch --once          # 1회 점검 후 종료(점검용)
uv run alphablock watch --dry-run       # 텔레그램 전송 없이 경고를 로그로만 출력
uv run alphablock watch --test-message  # 텔레그램 연결 확인용 메시지 1건 전송
```

**경고 대상** — 각 항목이 WAN-30 기준으로 stale(빨강)이면 경고한다:

- **데이터 수집 지연**: 시리즈(심볼·TF)의 최신 봉이 TF 주기 대비 stale(지연시간 명시).
- **펀딩비 갱신 지연**: 심볼의 펀딩 갱신이 정산 주기 대비 stale.
- **러너/수집기 하트비트 끊김**: 돌던 프로세스의 하트비트가 끊김. 한 번도 실행되지 않은
  (미실행) 프로세스는 "복구할 대상"이 없으므로 경고하지 않는다.

**플래핑 방지**: 동일 이상은 쿨다운(기본 60분) 내 1회만 보내고, 계속되면 쿨다운마다 1회씩
리마인더를 보낸다. 이상이 사라지면 **"✅ 정상 복구"** 알림을 1회 보낸다. 발효 중인 경고
상태는 `data/health_watch_state.json`에 남겨, 워치를 재시작해도 중복 경고를 보내거나 복구
알림을 놓치지 않는다.

**설정**(`.env`):

```bash
ALPHABLOCK_HEALTH_WATCH_INTERVAL_SECONDS=600      # 점검 주기(초)
ALPHABLOCK_HEALTH_WATCH_COOLDOWN_SECONDS=3600     # 동일 이상 쿨다운(초)
ALPHABLOCK_HEALTH_STALE_MULTIPLIER=2.5            # stale 판정 배수(WAN-30과 공유)
ALPHABLOCK_HEALTH_WATCH_STATE_PATH=data/health_watch_state.json
```

텔레그램 토큰·chat_id는 WAN-25와 동일하게 `ALPHABLOCK_TELEGRAM_*`로 주입한다(미설정 시
경고를 로그로만 남기는 드라이런으로 동작). 상주는 위 launchd 방식으로 상시 구동할 수 있다.

> 안전: 워치는 **읽기·경고만** 한다. 실주문·`live_trading`은 건드리지 않는다.

## OHLCV 데이터 갭 자동 탐지·복구 백필 (WAN-35)

WAN-30/32는 수집이 **멈췄다는 사실**은 알리지만, 멈춘 동안 생긴 **구멍(누락 봉)**은
그대로 남는다. 구멍이 있는 캔들 위에서 돌린 백테스트·페이퍼 성과(WAN-33)는 조용히
왜곡된다. WAN-35는 저장된 시리즈(심볼·TF)의 **내부 누락 봉만** 찾아 그 구간만
재수집(UPSERT)해 자동으로 메운다.

```bash
uv run alphablock backfill --repair   # 저장된 모든 시리즈의 내부 갭을 1회 탐지·복구
uv run alphablock backfill --repair --dry-run  # 복구 실패해도 텔레그램 경고 없이 로그만
```

수집 데몬(`alphablock collect`)은 **시작 시 1회 자동으로** 갭을 점검·복구한다
(`--repair-on-start`, 기본 켬). 끄려면 `--no-repair-on-start` 또는
`ALPHABLOCK_REPAIR_ON_START=false`.

**탐지 규칙**(`data/gaps.py`, 순수 함수 `find_gaps`) — 연속한 두 저장 봉 사이 간격이
TF 주기를 초과하면 그 사이를 갭으로 본다. 경계 처리:

- **신규 상장 이전 구간**: 저장된 첫 봉 이전은 보지 않는다(상장 전엔 봉이 없으므로 갭 아님).
- **현재 진행 중인 봉**: 저장된 마지막 봉 이후(형성 중/미수집 최신 구간)는 갭으로 잡지
  않는다 — 그 구간은 재시작 백필(`backfill_all`)의 몫이고, 복구는 오직 저장된 데이터
  **사이의 구멍**만 메운다.
- **갭이 없으면 거래소 API를 호출하지 않는다.**

**복구**(`data/repair.py`) — 탐지된 구간만 WAN-6 수집기 로직(`backfill_symbol`,
레이트리밋·지수 백오프 재시도 재사용)으로 다시 받아 UPSERT한다. 저장소 기본키가
`(symbol, timeframe, open_time)`이라 덮어쓰기는 무해하다. 거래소에도 없는 구간(상장
공백·거래 정지 등)은 복구 후에도 잔여로 **보고만** 하고 무한 재시도하지 않는다.
시리즈 단위로 예외를 격리해 한 시리즈 실패가 나머지 복구·수집 데몬을 죽이지 않는다.

**가시성** — 시리즈별 "채운 봉 수/잔여 봉 수/오류"를 로그와 마지막 복구 요약
파일(`data/repair_state.json`)에 남기고, Health 대시보드 "데이터 갭 복구" 섹션과
`alphablock status`에 노출한다. 복구 실패가 있으면 WAN-32 텔레그램 경고 경로로 알린다.

**설정**(`.env`):

```bash
ALPHABLOCK_REPAIR_ON_START=true                  # 수집 데몬 시작 시 갭 자동 복구
ALPHABLOCK_REPAIR_STATE_PATH=data/repair_state.json
```

> 안전: 복구는 **데이터 계층 한정**이다. 실주문·`live_trading`은 건드리지 않는다.

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

## 바이낸스 선물 테스트넷 드라이런 (WAN-27)

실계좌에 붙이기 전에, 바이낸스 USDⓈ-M **선물 테스트넷**에서 실제 주문으로 실행 경로
(진입→포지션 조회→손절/익절 부착→취소→청산→정합)를 자금 위험 없이 검증한다.
WAN-9 실행 브로커(`CcxtLiveBroker`)를 **테스트넷 엔드포인트로** 배선해 그대로 쓴다.

**키 격리(안전).** 테스트넷 키(`ALPHABLOCK_TESTNET_API_KEY/SECRET`)는 실계좌 키와
완전히 별도 필드다. `use_testnet=true`면 `create_exchange`가 `set_sandbox_mode(True)`로
테스트넷 엔드포인트를 쓰고 **테스트넷 키만** 주입한다 — 실계좌 키는 테스트넷 경로로
절대 새지 않으며, 그 역도 성립한다(테스트로 보장: `tests/test_exchange.py`).

### 준비

1. https://testnet.binancefuture.com 에서 테스트넷 API 키를 발급한다(가짜 자금).
2. `.env`에 아래를 채운다(실계좌 키는 비워두거나 그대로 두면 된다):

```bash
ALPHABLOCK_USE_TESTNET=true
ALPHABLOCK_LIVE_TRADING=true          # 테스트넷에 "실주문"을 넣기 위해 필요
ALPHABLOCK_TESTNET_API_KEY=...
ALPHABLOCK_TESTNET_API_SECRET=...
```

### 실행 (수동)

```bash
uv run python -m scripts.testnet_dryrun --symbol BTC/USDT:USDT --qty 0.001
```

스크립트는 세 전제(`use_testnet`·`live_trading`·테스트넷 키)를 모두 확인한 뒤에만
동작하며, 하나라도 빠지면 즉시 거부한다(실계좌 경로에는 접근하지 않는다). 진입/청산은
브로커 `place_order`를 재사용하고, 손절/익절·포지션·잔고 조회·취소는 거래소 특화 API라
하부 ccxt 인스턴스(`broker.exchange`)를 직접 호출한다. 각 단계는 로그로 남는다.

> 실제 테스트넷 호출은 네트워크·키가 필요해 자동화 테스트에서 돌리지 않는다. 자동
> 테스트는 sandbox 배선/키 격리만 모킹으로 검증한다(`pytest`).

## 설정

설정은 환경변수 또는 `.env`(접두사 `ALPHABLOCK_`)로 주입한다. `config.settings.Settings` 참고.
실거래(`ALPHABLOCK_LIVE_TRADING`)는 기본 `false`이며, 검증 전까지 활성화하지 않는다.
바이낸스 선물 테스트넷 검증은 위 "바이낸스 선물 테스트넷 드라이런(WAN-27)" 참고.

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
