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

메인 차트는 TradingView가 오픈소스로 공개한 캔들 엔진
[`lightweight-charts`](https://github.com/tradingview/lightweight-charts)를
`dashboard/lightweight_chart.py`가 직접 임베드해 그린다(WAN-54, Plotly→엔진 교체).
휠 확대·드래그 이동·크로스헤어가 트레이딩뷰 그대로고, 오더블록 3,000개 이상에서도
줌·팬이 끊기지 않는다(존을 시리즈가 아니라 캔버스 프리미티브 하나로 그려 시리즈
개수를 늘리지 않음). 캔들 아래 RSI(14) 서브패널이 시간축·크로스헤어를 공유해
진입이 RSI 어디서 났는지 바로 보인다. 존은 **수명 구간**(확정~무효화/소멸,
없으면 마지막 봉)에만 그려지고, 사이드바 **표시 필터**(진입한 존·활성·지지·깨짐·
소멸, 기본은 진입+활성)로 무엇을 볼지 고른다. **시점 재생** 슬라이더로 과거 특정
시각에 화면이 어떤 존 집합을 보여줬는지 재현할 수 있다. 캔들은 초기에 최근
1,500봉만 그리고 좌측 끝으로 스크롤하면 이미 전송된 데이터에서 이어서 채운다(서버
왕복 없는 클라이언트 사이드 지연 로딩).

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

### 상시 구동 + 북마크 (WAN-48)

매번 터미널에서 `streamlit run`을 치지 않아도 되게, 대시보드도 수집기·러너와 같은
macOS `launchd` 데몬으로 상주시킨다. **설치는 1회**, 이후엔 브라우저 북마크만으로
항상 최신 상태를 본다.

```bash
./scripts/install-daemons.sh dashboard   # 대시보드만 상주 등록·시작
./scripts/install-daemons.sh             # 수집기 + 러너 + 대시보드 셋 다
```

- **북마크**: 설치 후 `http://localhost:8501` 을 북마크한다. `RunAtLoad`로 로그인 시
  자동으로 뜨고 `KeepAlive`로 죽으면 되살아나므로, **재부팅해도** 북마크만 누르면 열린다.
- **포트**: `ALPHABLOCK_DASHBOARD_PORT`(기본 8501). 예:
  `ALPHABLOCK_DASHBOARD_PORT=9000 ./scripts/install-daemons.sh dashboard`.
- **이메일 프롬프트 없음**: `--server.headless true`로 실행해 Streamlit 첫 실행 이메일
  입력 프롬프트가 뜨지 않는다. `127.0.0.1` 로만 바인딩한다(외부 노출·인증 없음, 읽기 전용).
- **자동 새로고침**: 운영 상태(Health) 탭이 `ALPHABLOCK_DASHBOARD_REFRESH_SECONDS`
  (기본 60초, 0이면 끔) 주기로 **스스로 갱신**된다 — 사이드바 토글로 끄고 켤 수 있고,
  탭 상단에 **마지막 갱신 시각**이 표시된다. 무거운 분석·백테스트 탭은 자동 갱신 대상이
  아니며(캐시 유지), 가벼운 파일·DB 읽기인 운영 상태만 자주 갱신된다.
- **로그·해제**: 로그는 `logs/dashboard.log`. 해제는
  `./scripts/uninstall-daemons.sh dashboard`(로그는 남김).

## 🚨 데이터 수집 운영 — 조용한 정지를 막는 규칙 (WAN-156)

2026-07-21에 **BNB·XRP·TRX가 5일간 수집되지 않은 채 아무 경고도 뜨지 않았다.**
수집기는 살아 있었고, 갭 복구는 매번 「이상 없음」이었고, 검증도 통과였다. 세 장치가
동시에 조용했던 이유와 대책을 여기 못 박는다.

### 1. `.env`가 코드 기본값을 덮어쓴다 — 그리고 **재시작해야 반영된다**

`ALPHABLOCK_SYMBOLS`가 설정돼 있으면 `config.settings._default_symbols()`의 6종목
(WAN-111 유니버스)이 아니라 **`.env` 값이 이긴다.** 사고 당시 `.env`에 3종목만 있었다.

> ⚠️ **`.env`를 고쳐도 이미 돌고 있는 수집기·러너는 바뀌지 않는다.** 설정은 프로세스
> **시작 시 한 번** 읽는다. 반드시 재시작할 것:
> `./scripts/install-daemons.sh` 로 등록했다면 데몬을 내렸다 올린다.

**설정과 실제가 어긋나면 `alphablock status`에 보인다** — 수집 대상 심볼 목록을 찍고,
「설정에 있으나 저장된 봉이 없음」·「저장돼 있으나 수집 대상이 아님(낡습니다)」을
경고한다. 이번 사고의 본질은 그 어긋남이 **아무 화면에도 없었던 것**이다.

### 2. 갭 검사는 「꼬리 정지」를 구조적으로 못 잡는다 → 신선도 판정을 함께 본다

`data.gaps.find_gaps`는 봉과 봉 **사이**만 본다. 시리즈가 통째로 멈추면 「사이」가
없어 갭이 0이다 — 그래서 5일 멈춘 시리즈가 전 TF `gaps_found: 0`이었다.

`data.freshness`가 그 구멍을 메운다(`find_gaps`는 건드리지 않았다 — 내부 갭 계산은
정확하다). 복구·검증 층이 **정지를 결함으로 보고**한다:

```bash
uv run python -m cli.main backfill   # 갭 0이어도 정지가 있으면 종료 코드 1 + 🚨 목록
uv run python -m cli.main verify     # 「꼬리 신선도」 섹션. 정지면 판정 실패
```

정지가 감지되면 **텔레그램으로도 나간다**(`data.repair.alert_on_failure`). 그 출구가
막혀 있으면(`ALPHABLOCK_TELEGRAM_BOT_TOKEN`·`_CHAT_ID` 미설정) 경고는 만들어지고 아무
데도 가지 않는다 — 사고 당시 `live.health_watch`가 정확히 그 상태였다. **페이퍼를
방치하려면 토큰 설정이 전제다.**

**펀딩비도 같은 자로 본다.** OHLCV 백필은 펀딩비를 채우지 않으므로(별도 경로)
따로 밀린다 — 비어 있으면 롱 온리 엔진 수익률이 실제보다 좋게 나온다(WAN-91 배선).

### 3. 밀린 구간 복구 순서 — `backfill`은 「구멍」만 메운다

정지 구간은 **꼬리**라 `backfill`(갭 복구) 대상이 아니다. `history`로 먼저 채운다.
🚨 **1분봉 기본 룩백은 3일**(`_default_backfill_lookback_days`)이라 그보다 오래 멈췄으면
`--days`를 넉넉히 명시해야 한다.

```bash
# 0) 수집기·대시보드 중지 (하나씩 돌린다)
uv run python -m cli.main history --days 10 --timeframes 1m 15m 1h 4h 1d \
  --symbols BNB/USDT:USDT XRP/USDT:USDT TRX/USDT:USDT
uv run python -m cli.main backfill    # 잔여 내부 갭
uv run python -m cli.main verify      # 갭·중복·정합성 + 꼬리 신선도
uv run python -m cli.main collect     # 수집기 재시작
uv run python -m cli.main watch       # 경고가 사라지는지 확인
```

⚠️ **복구 전에 `--years 3` 같은 미끄러지는 창으로 백테스트를 돌리지 말 것** — 심볼마다
다른 기간을 보게 되어 심볼 편중 판정에 기간 차이가 섞인다. 창을 `--start`/`--end`로
못 박으면 안전하다.

### 4. 구멍 뚫린 데이터로는 지표를 계산하지 않는다 (거부)

`live.runner`의 평가 창은 `df.tail(N)` = **「마지막 N개 행」이지 「최근 N개 봉 기간」이
아니다.** 구멍이 있으면 볼린저 SMA20·RSI가 멀리 떨어진 봉을 이웃으로 취급해 **에러도
경고도 없이 그럴듯한 숫자**를 낸다. 하필 이 저장소에서 볼린저는 필터가 아니라
**지정가를 얼마에 걸지 정하는 값**(WAN-132 `intrabar_live`)이라, 그 결과는 틀린 가격에
주문을 거는 것이다.

그래서 러너는 창이 불연속이면 **평가를 건너뛴다**(로그로 알린다). 갭을 메우면 다음
폴링부터 정상 평가된다. 조용히 틀린 값을 내는 것보다 아무 값도 안 내는 것이 낫다.

### 4-1. 수집 대상이 아닌 TF는 「결함」이 아니라 「미추적」이다 (WAN-157)

DB에는 예전에 받아 둔 뒤 `ALPHABLOCK_TIMEFRAMES`에서 빠진 **5분봉**이 6종목 남아 있다
(2026-07-18 정지). 위 §2 신선도 판정을 그대로 적용하면 그 6건이 **매 실행마다** 종료
코드 1 + 텔레그램 경고를 만든다. 판정 자체는 옳지만 — DB에 실제로 멈춘 시리즈가 있다 —
🚨 **고칠 계획이 없는 항목이 매번 빨간불이면 사람이 경고 전체를 무시하게 되고, 그게
정확히 이번 사고(5일간 아무도 몰랐다)의 재발 경로다.** 알림을 켜 놓고 무시하는 것은
알림이 없는 것과 같다.

**선택: 「판정에서 제외 + 별도 표시」(이슈의 C안).** 5분봉을 되살리는 A안은 채택 엔진이
쓰지도 않는 TF(신호는 15m·1h, 체결은 1분봉)에 수집·저장 비용을 계속 내는 것이고,
DB에서 지우는 B안은 **되돌리려면 거래소 이력 한계에 걸릴 수 있는 비가역 삭제**다.
C안만이 비용도 비가역성도 없다.

⚠️ **C안의 함정은 「제외했으니 안 보인다」가 되는 것**이다 — 그러면 이번 사고와 같은
종류의 침묵을 새로 만든다. 그래서 `data.repair.repair_all(tracked_timeframes=...)`는
빠진 시리즈를 버리지 않고 `RepairSummary.untracked_series`에 담고, `backfill` ·
`alphablock status` · 대시보드 운영 상태 탭이 세 곳 모두에서
**「저장돼 있으나 수집 대상이 아님(낡습니다)」로 계속 찍는다**
(§1의 심볼 축 문구와 같은 자). 요약하면 **보이되 울지 않는다** — 종료 코드에 안 들어가고
텔레그램도 안 탄다. 회귀 테스트(`tests/test_untracked_series.py`)가 **양쪽 명제와**
「수집 대상 TF가 멈추면 여전히 결함」까지 동작으로 고정한다.

> 5분봉을 다시 쓰려면 `ALPHABLOCK_TIMEFRAMES`에 `"5m"`을 넣고 수집기를 재시작한 뒤
> `history`로 밀린 구간을 채운다(§3). 그러면 자동으로 판정 대상으로 돌아온다.

### 5. DB 동시 접근 — WAL 저널 (`database is locked` 대책)

`data/ohlcv.db` 하나를 네 래퍼(`OhlcvStore`·`FundingRateStore`·`PaperTradeStore`·
`BacktestRunStore`)가 열고, 수집기·러너·대시보드·백테스트가 **동시에** 돈다. 연결
설정이 없어 SQLite 기본값(`journal_mode=delete` · `busy_timeout=5초`)으로 돌던 것이
1.4GB DB 대량 삽입 중 `sqlite3.OperationalError: database is locked`를 냈다.

`data.sqlite_util.configure_connection`이 모든 연결에 **WAL + `busy_timeout` 30초**를
건다. WAL은 읽기와 쓰기가 서로를 막지 않는다 — 상시 DB를 읽는 페이퍼 러너(WAN-45)의
전제다. `ohlcv.db-wal`·`ohlcv.db-shm` 파일이 생기는 것은 정상이며 `.gitignore`가 잡는다.

## 페이퍼 트레이딩 성과 & 백테스트 대비 패리티 (WAN-33)

WAN-25 페이퍼 러너가 **실제로 무슨 거래를 냈고 그게 돈이 됐는지**, 그리고 **백테스트가
약속한 성과와 비슷한지**를 집계한다. 실계좌(WAN-9/WAN-27) 전환 판단의 근거다.

### 거래 영속 (`paper_trades` 테이블)

러너가 익절/손절로 포지션을 닫을 때마다 그 거래를 `ALPHABLOCK_DB_PATH`(기본
`data/ohlcv.db`)의 `paper_trades` 테이블에 **거래 단위로 누적**한다. 심볼·TF·방향·
진입/청산 시각·가격·사유와 함께 손익을 저장한다:

- `gross_pct` — 방향을 반영한 가격 손익률(%). 슬리피지·수수료 미반영.
- `fee_pct` — 왕복 수수료 비용률. 공용 비용 모델(아래)의 메이커/테이커 구분을 반영.
- `slippage_pct` — 진입·청산 슬리피지 비용률(≥0). 테이커 체결에만 붙는다.
- `funding_pct` — 보유 구간 펀딩비용률(WAN-16 수집분 + WAN-20 모델 재사용).
- `net_pct` — 모든 비용을 반영한 순손익률 = `gross − fee − slippage − funding`.
- `risk_pct` / `r_multiple` — 손절 거리(오더블록 무효화) 기준 리스크와 **R 배수**.

`(symbol, timeframe, entry_time, exit_time)`을 기본키로 UPSERT하므로 러너 재시작·
재평가로 같은 거래가 다시 들어와도 중복되지 않는다. 기록 실패는 러너 폴링·알림을
막지 않는다(견고성). 실주문/`live_trading`은 건드리지 않는 **페이퍼 한정**이다.

### 체결 비용 모델 — 백테스트/페이퍼 공용 (WAN-37)

수수료·슬리피지는 `common.costs.CostModel` **한 곳**에서 정의하고, 백테스트
(`backtest.engine`·`backtest.zone_limit_backtest`)와 페이퍼(`paper.store`)가 같은
파라미터·같은 산식을 쓴다(설정 단일 소스: `ALPHABLOCK_COSTS__*`). 두 경로가 다른
비용을 들지 않으므로 패리티 리포트의 잔여 차이는 **체결 타이밍·데이터**로 좁혀진다
— 실제로 동일 진입/청산이면 페이퍼와 백테스트의 거래 단위 순손익이 정확히 일치한다
(합성 시나리오 단위 테스트 `tests/test_costs.py`로 검증).

체결 방식별 비대칭을 명시한다:

- **시장가(테이커) 진입 — A안**: 테이커 수수료(`ALPHABLOCK_COSTS__TAKER_FEE_RATE`,
  기본 0.04%) + 슬리피지(`ALPHABLOCK_COSTS__SLIPPAGE_BPS`, 기본 5bps)가 진입에 붙는다.
- **지정가(메이커) 진입 — B안**: 메이커 수수료(`ALPHABLOCK_COSTS__MAKER_FEE_RATE`,
  기본 0.02%)가 붙고 **진입 슬리피지는 없다**(가격을 지정하므로). 대신 미체결 위험은
  `fill_rate`로 별도 추적한다. 비용을 넣으면 거래가 잦은 B안이 더 깎이지만, 메이커
  수수료·무슬리피지 이점이 이를 일부 상쇄한다 — 그래서 A/B 비교(`backtest.ab_run`)는
  이 비대칭을 반영해야 공정하다.
- **청산**: 손절·익절 도달은 시장가 성격이라 양쪽 모두 테이커(수수료+슬리피지)로 본다.
- **펀딩비**: 보유 구간 `[진입, 청산)`에 정산된 요율을 WAN-16 수집분으로 실제 가감한다.

봉 내 체결 가정: 진입은 시그널 확정 봉의 참조가(A안=종가/확정봉, B안=존 근단 지정가),
청산은 손절/익절 도달가로 본다 — 백테스트 엔진의 가정과 동일하며, 위 슬리피지를 이
참조가에 불리하게 적용한다.

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
  비교한다. 손익 비용(수수료·슬리피지·펀딩비)은 공용 비용 모델(WAN-37)로 양쪽에 동일
  적용해, 남는 차이가 거래 선택·체결 타이밍(실시간 데이터 지연·프라이밍 등)에서
  비롯되도록 한다. 차이가 임계값을 넘는 시리즈는 `⚠`로 표시한다.
- 결과 표는 `--out-dir`(기본 `out/paper/`)에 CSV(거래 원장·성과 요약·패리티)로 저장한다.

### 대시보드 "페이퍼 성과" 탭

같은 성과·패리티를 대시보드에서 표로 보고 **CSV로 내보낼** 수 있다(전체 성과 지표,
시리즈별 표, 거래 원장, 패리티 비교표 + 다운로드 버튼). 누적된 페이퍼 거래가 없으면
러너를 먼저 돌리라고 안내한다.

### 성과 다이제스트 텔레그램 발송 (WAN-36)

대시보드/CLI를 직접 열지 않아도 **성과 요약이 폰으로 오도록**, 같은 성과·패리티 집계를
짧은 마크다운 다이제스트로 만들어 WAN-32와 같은 텔레그램 경로로 보낸다.

```bash
# 최근 7일 다이제스트 미리보기(발송 없음 — 설정과 무관)
uv run python scripts/paper_digest.py --dry-run

# 특정 기간, 패리티 비교 없이
uv run python scripts/paper_digest.py --since 2024-01-01 --until 2024-01-08 --no-parity
```

- 담기는 것: 기간 내 **거래 수·승률·순손익률(%)·합계 R·MDD**, 시리즈(심볼·TF)별
  **상위/하위**, 백테스트 **패리티 불일치(`⚠️`) 시리즈** 요약.
- 기간은 `--since/--until`로 지정하거나, 없으면 `--days`(기본 `ALPHABLOCK_PAPER_DIGEST_DAYS=7`)
  창을 지금 기준으로 잡는다.
- 거래가 0건인 기간이면 "거래 없음 + 러너 상태 한 줄"로 짧게 보낸다(무음 실패 방지).
- **발송 안전장치**: 실제 발송은 `ALPHABLOCK_PAPER_DIGEST_ENABLED=true`이고 텔레그램
  (`ALPHABLOCK_TELEGRAM_*`)이 설정된 경우에만 한다. 기본은 꺼짐이며, `--dry-run`은 설정과
  무관하게 stdout 미리보기만 한다. 페이퍼 한정 — 실주문·`live_trading`은 건드리지 않는다.

주 1회 자동 발송은 cron/launchd로 스크립트를 스케줄링하면 된다(예: 매주 월요일 09:00 UTC).
`ALPHABLOCK_PAPER_DIGEST_WEEKDAY`(0=월)·`ALPHABLOCK_PAPER_DIGEST_HOUR_UTC`는 스케줄러가
참고하는 값으로, 스크립트 자체는 스케줄링하지 않는다.

```bash
# crontab 예: 매주 월요일 09:00 UTC 에 최근 7일 다이제스트 발송
0 9 * * 1  cd /path/to/AlphaBlock && uv run python scripts/paper_digest.py
```

## ⚠️ 생존자 편향 수정 — 이전 백테스트 수치 무효 (WAN-47)

**WAN-47 이전에 산출된 모든 백테스트·스윕·워크포워드·A/B 성과 수치는 신뢰할 수
없다.** 오더블록 탐지기가 트레이딩뷰 렌더링 인디케이터를 충실히 이식한 탓에, 깨진
뒤 되쓸린 존을 자료구조에서 **삭제**하고 표시 개수(`zone_count`)·스캔 창
(`max_distance_to_last_bar`)으로 결과를 잘라, `detect()`가 "끝까지 살아남은 소수의
존"만 백테스트 신호원으로 넘겼다. 손실로 끝난 존이 구조적으로 배제되는 **생존자
편향**이다.

WAN-47은 **탐지와 렌더링을 분리**했다. `OrderBlockResult.order_blocks`는 이제 생성된
모든 존의 **전체 생애주기 아카이브**(`break_time`·`swept_time`·`tapped_times` 포함)를
담고, 신호는 이 아카이브 전체에서 look-ahead 없이 생성된다. "지금 차트에 그릴 박스"
(트레이딩뷰 패리티)는 `rendered_order_blocks` / `active_at(t, limit=..., combine=...)`
렌더 뷰로 파생한다.

측정(BTC/USDT:USDT 1h, 2,930봉, `data/ohlcv.db`):

| 항목 | 수정 전 | 수정 후 |
| --- | --- | --- |
| `detect()` 반환 존 수 | 6 (살아남은 것만) | **92** (bull 48, bear 44; 깨짐 77, 소멸 72) |
| 렌더 뷰(트레이딩뷰 패리티) | 6 | 5 (변함없음) |
| A안 백테스트 거래 수 | 6 | **15** |
| 그중 **손절** 거래 | 1 | **7** (정상 출현) |
| 총수익률 | +2.1% | **−5.7%** |

수정 전 +2.1%는 손실 케이스를 배제한 착시였고, 손절이 정상 반영되자 실제로는
손실 전략임이 드러난다. **WAN-19(스윕)·WAN-22(워크포워드)·WAN-41(A/B) 리포트는 이
수정 후 재산출해야 한다** — 아래 CLI를 다시 실행하면 새 수치가 나온다. 재현 증적으로
커밋되는 `backtest/reports/wan41_ab_report.csv`는 WAN-47 기준으로 갱신했다(A/B 모두
손실 거래가 나타난다). 이전 커밋의 성과 수치·해석은 이 수정 이전 값이므로 무효다.

## ⚠️ 존 병합 미적용 수정 — 진입 부풀림 (WAN-56)

> 🔁 **WAN-149가 `combine_obs` 기본값을 `False`(원본 존 단위 분리)로 옮겼다**(사용자 결정
> 2026-07-21, [docs/decisions/wan149.md](docs/decisions/wan149.md)). 아래 절은 **병합
> 경로가 어떻게 동작하는가**의 기록으로 유효하고, `True`는 옵트인으로 존치한다 — 다만
> **채택 기본값의 서술은 아니다**. 옛 수치를 결론에 박아 둔 리포트는
> `harness.LEGACY_COMBINE_OBS`로 명시 고정돼 있다.

**WAN-56 이전에 산출된 모든 백테스트·스윕·워크포워드·A/B 성과 수치는(WAN-47 재산출분
포함) 신뢰할 수 없다.** 존 병합(`combine_obs`, 겹치는 동일방향 존을 합집합으로 병합)은
구현·기본 활성(`True`)돼 있었지만 **적용 지점이 렌더링 뷰 하나뿐**이었다. 즉 차트에
보이는 존은 병합된 것, 백테스트가 거래하는 존은 병합 전 원본이라, 사용자의 트레이딩뷰
화면과 백테스트가 **서로 다른 존 집합**을 봤다(WAN-47과 같은 계열 — 렌더링용 처리를
백테스트에 안 태움).

WAN-56은 **병합을 탐지 파이프라인으로 끌어올렸다.** 원본이 매 봉 `combineOBsFunc`을
호출하듯, `combine_obs=True`면 신호도 각 봉 시점의 병합 존 기준으로 생성된다
(`strategy/order_blocks.py::_generate_merged_signals`). 핵심은 **look-ahead 회피**다 —
전 구간을 한 번에 병합하면 미래에 생길 존과 미리 합쳐진 존으로 과거에 진입하게 되므로,
**그 시점까지 확정·미소멸한 존만**으로 병합 상태를 시간 순 재생한다(회귀 테스트로 고정:
데이터를 T에서 잘라도 T 이전 신호 불변). "병합 단위당 첫 탭 1회"(R1) 불변식으로 진입
부풀림을 제거하고, 병합 존의 손절선은 합집합 원단(distal) 경계를 따른다. 아카이브
(`order_blocks`)는 두 경로 모두 **원본 단위**로 보존해 병합 전/후를 대조할 수 있다.
`combine_obs=False`는 원본 단위 경로로 남아 비교 가능하다.

측정(3심볼 BTC/ETH/SOL × 3TF 15m·1h·4h × 3년, `data/ohlcv.db`, 신호 단계 직접 비교):

| 항목 | 병합 전(raw) | 병합 후(merged) |
| --- | --- | --- |
| 활성 진입 신호 총수 | 9,586 | **7,762** (팽창률 **1.23×**) |
| 예: BTC 1h | 608 | **476** |
| 예: SOL 4h | 184 | **161** |

겹치는 존을 각각 세어 진입이 ~1.23배 부풀려져 있었고, 그만큼 거래 수·수수료·펀딩비
추정이 왜곡됐다. 병합 존은 경계가 합집합이라 손절 거리(진입가→distal)도 달라진다. 전체
표는 `reports/wan56_merge_impact.md`(아래 CLI로 재생성):

```bash
uv run python scripts/merge_impact_report.py --out reports/wan56_merge_impact.md
```

**WAN-19(스윕)·WAN-22(워크포워드)·WAN-46/41(A/B) 리포트는 이 수정 후 재산출해야 한다** —
해당 CLI는 이제 병합 적용 신호를 자동으로 쓰므로 다시 실행하면 새 수치가 나온다. 이전
커밋의 성과 수치·해석은 이 수정 이전 값이므로 무효다.

### ✅ 재산출 완료 (WAN-58) — 병합 존 + 공용 비용 모델 기준

위 4개 리포트를 **WAN-56(병합) + WAN-37(비용 모델)이 모두 머지된 뒤** 재실행했다. 전체
비교표·심볼별 분해·복리 병기·판정은 [`reports/wan58_recompute.md`](reports/wan58_recompute.md),
권위 있는 CSV는 `backtest/reports/wan58_*.csv`(신규 파일 — 기존 `wan46_*`/`wan50_*` CSV는 병합
전 베이스라인으로만 남김). 병합 효과와 비용 효과를 분리하려고 A/B는 **gross(비용 전) / net(비용 후)**
두 벌로 낸다(`backtest.ab_run.add_cost_args`, `--fee-rate/--maker-fee-rate/--slippage`).

핵심 결과(net = 실거래 근사, WAN-57 판정 기준):

| 리포트 | 이전 결론 | 병합+net 재산출 | 뒤집힘? |
| --- | --- | --- | --- |
| **WAN-50** (4h A/B, WAN-57 게이트) | 4h B안 우월 | **4h B안 우월 — net 3심볼 모두 +**(BTC +1.1%/ETH +39%/SOL +54.5% 복리), A는 net 음(−) | ❌ 유지·강화 |
| WAN-46 (A/B 본실험) | B > A | B > A 유지(net PF: B 1.10 vs A 0.70). 진입 1.30× 축소 | ❌ 유지 |
| WAN-19 (스윕) | (미커밋) | A-진입 스윕 6/15 조합만 양(+), ETH 전 TF 부진 | — |
| WAN-22 (워크포워드) | (미커밋) | A-진입 OOS 대체로 부진(SOL 4h만 +21.3%), IS→OOS 격차는 작음 | — |

**비용 비대칭이 B(메이커·슬리피지 0)에 유리하게 작동**해 4h에서 B 우위가 오히려 넓어졌고
(gross→net: B +4.39%→+3.99% vs A +0.10%→−0.28%), old 대비 심볼 편중도 완화됐다(ETH 강세 전환).
따라서 **WAN-57(4h `entry_mode` 기본값 B안 전환)의 게이트는 net 기준으로 통과**다.

## ⚠️ 리스크 사이징 미배선 + 손절 체결가 버그 수정 (WAN-65)

**WAN-65 이전에 산출된 모든 백테스트·스윕·워크포워드·A/B 성과 수치(WAN-19/22/46/50/58
포함)는 무효다.** 모든 백테스트 진입점이 `BacktestConfig()`를 직접 생성하면서
`risk_sizing`을 넘기지 않아, 설정(`risk_sizing_enabled=True`)과 무관하게 실제로는
매 거래가 자본 전액(`position_fraction=1.0`)으로 진입했다. 손절 거리가 0.4%든
14.3%든 손실 크기가 그대로였다는 뜻이라, MDD·payoff·R 배수가 리스크 정규화되지
않은 채로 산출됐다. 같은 조사에서 손절(`stop_loss`)로 청산됐는데 수익률이 양수인
거래(`+4.02%`)도 발견했다 — 무효화(breaker) 판정은 wick 기준인데 체결가는 그 봉의
종가를 그대로 썼기 때문에, 봉이 반전해 유리하게 마감하면 "손절인데 이익"이 됐다.

수정: `backtest/sweep.py::default_backtest_config()`를 모든 진입점(스윕·CLI 리포트·
워크포워드·A/B 러너·존-지정가 파이프라인·페이퍼 패리티·대시보드)의 **단일 설정
소스**로 확정해 `settings.effective_risk_sizing`을 배선했고, `risk_sizing=None`으로
실행되면 경고를 내도록 했다(WAN-59/WAN-63과 같은 재발 방지 패턴). 손절 체결가는
무효화 봉 종가와 오더블록 무효화 경계 중 진입가에 더 불리한 쪽으로 clamp해, 손절이
구조적으로 이익을 낼 수 없게 했다.

**리포트 자기서술**: "구현은 됐는데 실행 경로에 안 붙어 조용히 틀린 값이 나온다"는
패턴(WAN-47/56/59/63/65)의 근본 원인 중 하나가 "파일만 봐서는 어떤 설정으로 나온
숫자인지 알 수 없다"는 것이었다. 거래 단위·요약·스윕 CSV 모두 핵심 4개 컬럼
(`entry_mode`/`sizing_mode`/`combine_obs`/`funding_coverage`)을 실어, `scripts/
backtest_report.py`는 조합 디렉터리마다 `run_config.json`(전체 설정 + git 커밋
해시)도 함께 쓴다. 대시보드 분석 탭 상단에는 `진입: A안(봉 마감 종가) · RSI: 확정봉
· 사이징: 리스크 1% · 병합: ON · 펀딩비: 반영됨` 형태의 실행 설정 배지가 뜨고,
사이징 미적용이거나 펀딩 커버리지가 100% 미만이면 경고색으로 강조된다.

측정(3심볼 BTC/ETH/SOL × 5TF 15m/1h/2h/4h/1d, `data/ohlcv.db`, sharpe 최상위 조합 기준):

| 지표 | BEFORE(전액 진입) | AFTER(리스크 사이징) |
| --- | --- | --- |
| 평균 MDD | 22.0% | **9.5%**(15/15 조합에서 축소) |
| 손절 거래의 자본 대비 손실(평균/중앙값) | 제멋대로(0.4%~14%+) | **0.98% / 0.98%**(목표 1.0%) |
| 이익을 낸 손절 거래 | 24/276건(8.7%, 실측 재현) | **0건** |
| 진입 명목가치(자본 대비, 평균) | 90.9% | 63.2%(손절 거리에 반비례해 변동) |

전체 비교표·재현 커맨드는 [`reports/wan65_recompute.md`](reports/wan65_recompute.md),
권위 있는 CSV는 `backtest/reports/wan65_*.csv` 참고.

## 메인 엔진 규칙 (WAN-81, WAN-87로 숏 비활성화 반영 — 현재 기본값)

`ConfluenceParams()` 기본값이 정의하는 **현재** 메인 엔진 규칙. 아래 "백테스트 성과
리포트(WAN-19)"·"숏 처리 기본값(WAN-69)" 절과 그 아래 이어지는 여러 절이 서술하는
"구 규칙"(오더블록 첫 탭 + RSI 극단 게이트 + EMA60/VWMA100 선 익절 + 롱 온리, WAN-23/
WAN-66/WAN-69)은 **이 이슈로 전면 교체됐다** — 이 섹션이 현재 진실이고, 아래 이어지는
절들은 그 규칙이 확정되기까지의 의사결정 이력(과거 시점 스냅샷)으로 남겨둔다.

- **진입가** = 활성(병합) 오더블록 탭 시 **볼린저밴드(SMA 20 ± 2σ)** 로 재산정. 밴드가
  존 위/겹침/아래에 있으면 각각 존 경계 진입/밴드가 진입/진입 없음(롱·숏 대칭).
- **진입 조건** = **RSI 게이트 없음 — 첫 탭·재탭 모두 무조건 진입**(워밍업 NaN 포함)
  (`rsi_gate_mode="unconditional"`, **WAN-123** = [WAN-116](docs/decisions/wan123.md)
  결정 B). 재탭은 계속 평가하되 그 탭에 RSI 조건이 걸리지 않는다. 병합 존은 구성 존이
  개별적으로 자기 몫의 첫 탭을 셀 수 있다(WAN-81 §5, 구 WAN-82 버그 흡수).
  - WAN-81~122의 「첫 탭 면제 + 재탭 RSI 게이트」는 WAN-114 ablation에서 **게이트만의
    기여가 12셀 중 8셀 음수**(1h는 6셀 전부)로 나와 뺐다 — 거래를 13~14% 쳐내는데
    그 쳐냄이 순손해였다. `rsi_gate_mode="first_tap_free"` 옵트인으로 되돌릴 수 있다.
  - ⚠️ 게이트를 끌 때 `"none"`을 쓰면 **절반만 꺼진다**(워밍업 구간 탭이 계속 막힌다).
    자세한 것은 [`docs/decisions/wan123.md`](docs/decisions/wan123.md) §3.
- **숏 비활성화**(기본, WAN-87 — [WAN-86 결정 1](docs/decisions/wan86.md)로 WAN-81의
  숏 활성화를 사용자가 데이터에 근거해 재번복). 숏 경로 코드는 남아 있어
  `short_enabled=True`로 재검증할 수 있다.
- **익절** = **고정 1:1.5R**(진입가 → 오더블록 무효화 경계 거리의 1.5배). 볼린저로
  진입가가 유리해지면 1R이 줄어 익절 목표도 함께 가까워진다. EMA·VWMA는 익절
  판정에서 완전히 빠졌다.
- **손절** = 진입 근거 오더블록의 **무효화(breaker, distal 경계 이탈)**. 변경 없음.
- **동시봉 손절 우선**, **TF당 동시 1포지션**(피라미딩·역전 없음)은 전략·엔진이 보장.

재산출 성과(3심볼×4TF×3년, 구 엔진 대비 비교, §5 되살아난 진입 수, 갭D 기각 수)는
[`backtest/reports/wan81_summary.md`](backtest/reports/wan81_summary.md) 참고(재현:
`uv run python -m backtest.wan81_engine_replacement_report`) — 단, 이 리포트는
**숏 활성화 신 엔진**(`NEW_ENGINE_PARAMS`에 `short_enabled=True` 고정) 기준이라
현재 기본값(롱 온리)과는 다르다. 현재 기본값(롱 온리) 재산출은 아래 WAN-87 절 참고.

⚠️ **기존 성과 수치 무효**: 아래 절들을 포함해 WAN-19/22/46/50/58/68/70/71/73/74/75/76
등 이 이슈 이전의 모든 백테스트 리포트는 구 엔진 기준이라 더 이상 유효하지 않다.
WAN-81/WAN-84 리포트(아래 두 절)도 **숏 활성화 전제**라 WAN-87 이후 기본값과는 달라
현재 기본값 성과로는 무효다 — 숏 활성화 자체의 검증 기록으로만 유효하다.

## 신 엔진 엣지 재검정 — 매칭 널·OOS·비용·롱숏 (WAN-84, 현재 기준)

WAN-81 엔진 교체 **이후 처음으로** 신 엔진 기본값(`ConfluenceParams()`) 그대로 통계적
엣지를 재검정한다. WAN-70(무작위 진입 매칭 널)·WAN-71(엣지 소재지)·WAN-22/50(워크포워드)의
방법론을 **신 엔진 청산 규칙(고정 1.5R + 브레이커 손절)** 으로 재실행한다. **아래
수치는 신 엔진 기준이며, 구 엔진 리포트 수치와 혼동하면 안 된다.**

- **매칭 널 판정** = **엣지 없다**. 3심볼×4TF×IS/OOS 유효 셀 17개(거래 20건 이상) 중
  p≤0.05 & 실제>무작위평균인 셀은 **0개**. 볼린저 진입가·첫탭 무조건·1.5R 익절·숏
  활성화로 규칙이 크게 바뀌었지만, WAN-70(구 엔진)의 "진입 타이밍에 엣지 없음" 결론이
  신 엔진에서도 유지된다.
- **비용 민감도** = 다수 셀이 1.5x/2.0x에서 음전환(예: BTC 1h OOS +5.0%→-4.1%).
  플러스 셀조차 비용에 취약하다.
- **롱/숏 분해** = OOS 롱 평균 +3.07%(유효 9셀) vs 숏 평균 -3.82%(유효 7셀) —
  **숏이 롱 수익을 갉아먹는다.** 신 엔진이 숏을 기본 활성화했지만, 구 엔진(WAN-64)에서
  숏이 전 심볼·전 TF 손실이던 패턴이 신 규칙에서도 재현된다.
- **워크포워드(변형 B)** = 15m OOS 평균 -2.1%(PF 0.93), 1h +0.7%(PF 1.12),
  4h +0.5%(PF 1.38), 1d -0.1%. IS→OOS 저하가 크고 PF는 1 근처로 엣지 신호가 약하다.

리포트: [`backtest/reports/wan84_summary.md`](backtest/reports/wan84_summary.md)
(재현: `uv run python -m backtest.wan84_new_engine_validation`). 원자료 CSV는
`backtest/reports/wan84_*.csv`, 워크포워드는 `backtest/reports/wan50_ab_walkforward*.csv`
(신 기본값으로 갱신). 이 "엣지 없음" 결론은 라이브/포트폴리오/종목 확장에 자원을 더
넣기 전에 진입 로직을 재검토해야 함을 시사한다(WAN-70 결론의 신 엔진 재확인).

⚠️ **다음 갈림길 결정 완료(WAN-86) → 코드 반영 완료(WAN-87)**: 숏 유지 여부·실거래
보류·다음 방향 3개 항목을 [`docs/decisions/wan86.md`](docs/decisions/wan86.md)에
사용자 결정으로 기록했다(2026-07-14). 요약: ① 숏 **비활성화**(`short_enabled=False`,
데이터 우선 — WAN-87에서 `ConfluenceParams()` 기본값에 반영 완료), ② 실거래 계속
보류(`ALPHABLOCK_LIVE_TRADING=false`), ③ 규칙 개선 **지속**(단 이후 규칙 변경은
매칭 널 + 비용 민감도 관문을 통과해야 채택). 롱 온리 기본값 재산출은
[`backtest/reports/wan87_long_only_summary.md`](backtest/reports/wan87_long_only_summary.md)
참고(재현: `uv run python -m backtest.wan87_long_only_report`).

## 범용 백테스트 CLI — `python -m backtest.run` (WAN-101)

**파라미터를 바꿔 다시 돌려보는 일에 더는 티켓·개발·PR이 필요 없다.** 값에 콤마를 주면
데카르트 곱으로 격자를 돌고 조합별 1행을 낸다.

```bash
# 1. 익절 R 스윕 — 1.5R이 맞는 값인가
uv run python -m backtest.run --tp-r 1.0,1.5,2.0,3.0

# 2. 체결 가정 비교 — "닿으면 체결"이 얼마나 낙관인가 (WAN-96 축)
uv run python -m backtest.run --tf 15m --fill baseline,pen_5bp,pen_5bp_drop_50

# 3. TF 비교
uv run python -m backtest.run --tf 15m,1h,4h,1d

# 4. 심볼 비교 + CSV 저장
uv run python -m backtest.run --symbol BTCUSDT,ETHUSDT,SOLUSDT --format csv --out out/sweep.csv

# 5. OOS 검증 — IS에서 좋았던 게 OOS로 넘어오는가 (과최적화 방어)
uv run python -m backtest.run --tp-r 1.0,1.5,2.0,3.0 --oos
```

출력 예(1h · 3년 · 최근 실데이터):

```
[고정] tf=1h · segment=full · entry=zone_limit · off_bp=0 · fill=pen_5bp · seed=0
       symbol  tp_r  return%   win%   mdd%  trades  fill%  meanR  sharpe
------------------------------------------------------------------------
BTC/USDT:USDT   1.5     0.99  46.88  14.59     128  29.60   0.16    1.16
ETH/USDT:USDT   1.5    11.43  48.53  11.80     136  30.26   0.21    6.81
```

값이 하나뿐인 축은 `[고정]` 줄로 접고 실제로 스윕한 축만 열로 펼친다. `--format
csv|json`은 접힌 축까지 전 열을 남긴다(진행 로그는 stderr로 나가므로 `--format csv |
...` 파이프가 안전하다).

### 기본값 = 채택 기본값 (회귀 고정)

인자를 아무것도 주지 않으면 `ConfluenceParams()`(WAN-95/87 채택 기본값: 존 지정가 +
실시간 RSI + 롱 온리 + 볼린저 + 고정 1.5R) 그대로 돈다. **CLI는 자기만의 기본값을 갖지
않는다** — 그러면 기존 리포트와 다른 엔진을 돌리게 되고 두 숫자를 비교하는 논의가
통째로 무의미해지기 때문이다. 이 성질은 테스트로 고정돼 있다:

- `tests/test_harness.py` — CLI 파라미터 == WAN-95/96/99 리포트의 기준선 파라미터,
  그리고 CLI가 리포트와 **같은 엔진 함수**를 같은 인자로 호출함(CI에서 항상 실행).
- `tests/test_run_regression_real_data.py` — 실데이터 산출 숫자가 리포트 셀과 일치함
  (`data/ohlcv.db`가 있을 때만 실행, CI에서는 skip).

실제로 기본값 실행은 WAN-95 재산출표(3심볼 × 15m/1h)와 WAN-99 `pen_5bp` 표(3심볼 × 1h)의
해당 셀을 **1e-9 이내로 재현**한다.

### 주요 인자

| 인자 | 설명 |
| -- | -- |
| `--symbol` | 콤마 복수. 축약형(`BTCUSDT`)·정식(`BTC/USDT:USDT`) 모두 허용 |
| `--tf` | 콤마 복수 (`15m,1h,4h,1d`) |
| `--years` / `--start` `--end` | 최근 N년 또는 명시 구간(`YYYY-MM-DD`) |
| `--entry-mode` | `close`(A안) / `zone_limit`(B안, 기본). 콤마로 둘 다 주면 한 표에서 비교 |
| `--tp-r` | 고정 R 익절 배수 |
| `--offset-bps` | 지정가 오프셋(WAN-99). 지정가 전용 |
| `--fill` | `baseline` `pen_1bp` `pen_5bp` `drop_25` `drop_50` `pen_5bp_drop_50` (WAN-96 축) |
| `--fill-penetration-bps` / `--fill-dropout-rate` | 프리셋 대신 직접 지정 |
| `--long-only` / `--short-enabled` | 숏 게이트 |
| `--funding` / `--no-funding` | 펀딩비 반영(기본 반영) |
| `--fee` / `--maker-fee` / `--slippage` | 비용 가정 오버라이드 |
| `--oos` / `--walkforward N` | IS(앞 2/3)/OOS(뒤 1/3) 분할, 또는 N개 롤링 창 |
| `--format` / `--out` | `table`(기본) / `csv` / `json`, 파일 저장 |

### 진입 경로는 라벨이 아니라 스위치다

`--entry-mode close`는 A안(`backtest.sweep.evaluate` → `BacktestEngine`),
`zone_limit`은 B안(`run_zone_limit_backtest_verbose`)을 탄다(WAN-95). 지정가 전용 노브를
종가 진입에 주면 **조용히 무시하지 않고 거부한다** — 무시하면 "오프셋 5bp로 돌렸다"고
믿는 사용자에게 오프셋이 없는 결과에 `off_bp=5` 라벨만 붙여 주게 된다. 두 방식을 한 표에
같이 올리면 종가 성과를 1분봉 커버 창으로 한정해 기간을 맞춘다(`--fair-window`, 기본 자동).

### 공통 골격 (`backtest/harness.py`)

데이터 로딩 · 파라미터 조립 · 경로 스위치 · 구간 분할 · 렌더를 모아 둔 모듈이다. CLI와
리포트 스크립트(`wanNN_*.py`)가 같은 골격을 공유해야 두 결과를 나란히 놓고 읽을 수
있다. 일회성 리포트 스크립트의 아카이브·래퍼화는 후속 이슈(WAN-94)에서 다룬다 — 각
리포트의 재현 커맨드(`python -m backtest.wanNN_*`)가 CLAUDE.md·README·리포트 md에
문서화돼 있어, 옮기는 일 자체가 별도 결정이기 때문이다.

## 백테스트 성과 리포트 & 파라미터 스윕 (WAN-19, 구 엔진 — 이력)

WAN-23 재설계 컨플루언스 전략을 WAN-8 백테스트 엔진에 태워 **재현 가능한 성과
리포트**를 만들고, 진입 RSI 임계값을 소규모로 스윕해 비교표를 낸다. 평가 대상
전략(TF별 자기완결, **WAN-81로 대체된 구 규칙**):

- **진입** = 활성(비-breaker) 오더블록의 **첫 탭** + RSI 게이트(롱=과매도, 숏=과매수).
- **익절** = 진입가 너머 **가장 가까운 익절선 도달**(동적, 전량 청산). 익절선은
  **EMA 60 + VWMA 100 두 개뿐**이다(WAN-66).
- **손절** = 진입 근거 오더블록의 **무효화(breaker, distal 경계 이탈)**.
- **동시봉 손절 우선**, **TF당 동시 1포지션**(피라미딩·역전 없음)은 전략·엔진이 보장.

각 타임프레임은 **독립 단위**로 개별 평가하며, 지표 파라미터(RSI 14, 익절선 EMA 60 +
VWMA 100)는 **전 TF 공통 고정**이다. 차트에는 EMA 20/60/120/240/365를 함께 그리지만
(`display_ema_lengths`) 이는 **표시용**일 뿐 익절 판정에는 EMA 60만 쓴다 — WAN-23 명세가
"차트 표시선"과 "익절 목표선"을 한 배열로 뒤섞어 적어 EMA 20에서 조기 익절하던 버그를
WAN-66에서 바로잡았다. 익절·손절이 모두 전략의 동적 규칙으로 결정되므로 백테스트의
고정 %손절·익절 배수는 이 전략 성과에 관여하지 않는다.

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
적용)·거래 수다. `sizing_mode`(`risk_sizing`/`full_position`)와 `risk_per_trade`도
함께 실려(WAN-65) 어떤 사이징으로 나온 숫자인지 파일만 봐도 알 수 있다.

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
(RSI 14, 과매수 70/과매도 30, 익절선 EMA 60 + VWMA 100, 오더블록 무효화 손절)은 재설계
확정 규칙 그대로 유지한다(WAN-66 이후 익절선은 EMA 60 + VWMA 100 두 개).

## 숏 처리 기본값 — 롱 온리 채택 (WAN-69, WAN-81로 번복 → WAN-87로 재번복 — 이력)

⚠️ **WAN-69의 롱 온리 결정은 WAN-81 사용자 확정 규칙으로 한 차례 뒤집혔다가,
WAN-87([WAN-86 결정 1](docs/decisions/wan86.md))에서 다시 롱 온리로 되돌아왔다.**
`short_enabled` 기본값은 현재 `False`다(위 "메인 엔진 규칙" 참고). 아래는 WAN-69
당시의 의사결정 기록으로 남겨둔다.

`ConfluenceParams.short_enabled` 기본값이 `True`(숏 허용)에서 **`False`(롱 온리)**
로 바뀌었다. WAN-68 OOS 비교(`backtest/reports/wan68_short_variant_comparison.csv`,
3심볼×4TF×IS/OOS)에서 세 후보의 12셀 평균 OOS 총수익률·우월 셀 수:

| 후보 | 평균 OOS 총수익률 | 무게이트 대비 우월 셀 |
| -- | -- | -- |
| BTC 일봉 레짐 게이트 C(종가<EMA200) | +3.08% | 10/12 |
| BTC 일봉 레짐 게이트 D(EMA200 기울기 하락) | +2.90% | 10/12 |
| 롱 온리(숏 완전 제거) | +1.58% | 8/12 |
| (기준) 무게이트 | +0.78% | — |

표면적으로는 레짐 게이트가 롱 온리보다 앞서지만(C/D는 우월 셀 집합도 동일), 이미
WAN-70·WAN-74가 매칭 널(실제 거래의 방향·시각대 분포를 맞춘 무작위 대조군) 검정으로
**게이트 도입이 거래 수만 줄일 뿐 통계적으로 유의한 엣지를 만들지 못함**을 확인했다
(WAN-68 무작위 대조군도 12셀 중 1셀만 p≤0.05). 다수 셀이 소표본(OOS n<10)이라 이
근소한 차이(+1.3%p 안팎)를 신뢰하기 어렵고, 레짐 게이트를 채택하면 라이브/페이퍼
경로에 BTC 일봉 데이터 상시 조회 배선이 새로 필요하다. 차이가 유의하지 않으면
단순한 쪽을 택한다는 기준(이슈 본문)에 따라 **롱 온리를 기본값으로 채택**했다.

(WAN-87 현재 안내: 기본값이 다시 `false`(롱 온리)다. 숏을 켜려면(재검증 목적)
`ALPHABLOCK_CONFLUENCE__SHORT_ENABLED=true` 또는 `ConfluenceParams(short_enabled=True)`.)

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

## 존-지정가 페이퍼 러너 — 체결률 실측 (WAN-45, 기본값)

📌 **`python -m live.runner`는 채택 기본값(`entry_mode="zone_limit"`)에서 존-지정가
페이퍼 러너로 위임한다.** 활성 오더블록의 탭을 감지해 채택 규칙 그대로(존 근단 →
볼린저 봉내 재산정(WAN-132) → 오프셋 2bp) 지정가를 걸어두고, 1분봉 틱(수집기 웹소켓
→ 저장소 경유)으로 체결/만료/무효화를 굴린다. **1순위 목적은 체결률 실측**이다 —
모든 백테스트 판정이 서 있는 `baseline`("닿으면 체결") 낙관 가정을 실제 시장에서
확인한다(걸린/체결/만료·취소, 예약→체결 소요, 관통 폭을 `live_limit_orders` 테이블에
누적). 백테스트와 **같은 부품**(`IntrabarLiveLimit`·`RealtimeRsi`·`RealtimeBand`)을
공유하며 테스트가 `is`로 고정한다.

```bash
# 상시 페이퍼 러너(수집 데몬이 함께 돌아야 1분봉 틱이 들어온다)
uv run python -m live.runner

# 체결률 실측 요약 표(백테스트 baseline 가정과 나란히 읽는 표)
uv run python -m live.fill_report
```

러너 세션(가동 시간)도 장부에 남는다 — 로컬 맥 운영이라 생기는 중단 구간을 체결률
분모에서 걸러 읽기 위해서다(재시작 시 이전 대기 주문은 복원하지 않고 `discarded_restart`로
마감). 페이퍼 한정: 실주문 API는 부르지 않는다(`ALPHABLOCK_LIVE_TRADING=false` 불변).

## 실시간 시그널 러너 + 텔레그램 알림 (WAN-25, 페이퍼 · A안 경로)

⚠️ **아래 A안(봉 마감 종가) 러너는 `ALPHABLOCK_CONFLUENCE__ENTRY_MODE=close`로 명시했을
때만 돈다** — 채택 기본값에서는 위 존-지정가 러너가 기본이다(WAN-122가 A안 경로 폐기 예정).

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
uv run alphablock history --days 180 --timeframes 1m  # 과거 창 대량 백필(1m 6개월 등) — WAN-44
uv run alphablock verify           # OHLCV 갭·중복·상위TF 정합성 검증 — WAN-44
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
./scripts/install-daemons.sh            # 수집기 + 러너 + 대시보드 셋 다 설치·시작
./scripts/install-daemons.sh collector  # 수집기만
./scripts/install-daemons.sh live       # 러너만
./scripts/install-daemons.sh dashboard  # 대시보드만(WAN-48, http://localhost:8501)

./scripts/uninstall-daemons.sh          # 셋 다 해제(로그는 남김)
```

- **재부팅·크래시 자동 복구**: 로그인하면 `RunAtLoad`로 다시 뜨고, 프로세스가 죽으면
  `KeepAlive`가 `ThrottleInterval`(10초) 간격으로 되살린다.
- **로그**: 기본 `logs/collector.log`, `logs/live.log`, `logs/dashboard.log`(리포지토리
  밖 위치는 `ALPHABLOCK_LOG_DIR`로 지정). `logs/`는 `.gitignore`에 포함된다.

```bash
launchctl list | grep alphablock                            # 등록·구동 상태
tail -f logs/collector.log logs/live.log logs/dashboard.log # 실시간 로그
uv run alphablock status                                     # 상태 요약
```

로그 파일이 무한정 커지지 않게 하려면 macOS `newsyslog`(예: `/etc/newsyslog.d/`에
`alphablock.conf` 추가)로 로테이션을 설정한다.

### 리눅스 서버 상시 구동 (WAN-174, systemd)

로컬 맥은 ASTx가 바이낸스 선물 웹소켓을 막아 실시간 수집이 불가하므로(WAN-174 진단),
수집기·페이퍼 러너·대시보드를 **리눅스 서버**(오라클 무료 티어)에서 돌린다. launchd 판과
대칭인 systemd 스크립트를 쓴다:

```bash
./scripts/setup-server.sh               # 서버 1회 셋업(스왑 2GB + uv + 의존성)
./scripts/install-systemd.sh            # 셋 다 설치·시작(부팅 시 자동 시작 포함)
./scripts/uninstall-systemd.sh          # 해제(로그는 남김)
```

서버 프로비저닝·`.env`/DB 이전(WAL 체크포인트)·검증 절차·DB 배치 설계(수집=서버 /
백테스트=로컬)는 [docs/ops/server-migration.md](docs/ops/server-migration.md) 참고.
대시보드는 서버 `127.0.0.1` 전용 — `ssh -N -L 8501:127.0.0.1:8501 <서버>` 터널로 접속한다.

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

## 1분봉(1m) 대량 백필 & 무결성 검증 (WAN-44)

WAN-41(존-지정가 + 실시간 RSI)의 백테스트는 **1분봉 서브스텝**으로 봉 내부 경로를
재구성해야 성립한다. `alphablock collect --once`의 순방향 백필은 저장된 마지막 봉
*다음*부터만 채우고 1m 기본 룩백도 3일이라, 과거를 6개월/3년으로 넓게 채우지 못한다.
WAN-44는 **과거 창(window) 대량 백필** 엔트리포인트와 **무결성 검증** 명령을 추가한다.

### 과거 대량 백필 (`alphablock history`)

`[현재-days, 현재)` 창을 심볼×TF별로 페이징 백필한다. 저장된 마지막 봉과 무관하게
**창 시작부터** 받으므로 과거 방향을 넓게 메운다. 저장소가 `(symbol, timeframe,
open_time)` UPSERT라 겹치는 구간은 무해하며(멱등), **중단 후 재실행하면 이미 채운
구간은 그대로 두고 빠진 구간만** 다시 받는다.

```bash
# BTC 1m 최근 6개월(180일) — WAN-41 언블록용 최소 데이터
uv run alphablock history --days 180 --symbols "BTC/USDT:USDT" --timeframes 1m

# 3년(1095일)으로 확장, 세 심볼 모두
uv run alphablock history --days 1095 \
  --symbols "BTC/USDT:USDT" "ETH/USDT:USDT" "SOL/USDT:USDT" --timeframes 1m
```

장시간 실행에 대비해 `20,000`봉마다 진행률(현재 커서 시각)을 로그한다. 레이트리밋·
지수 백오프 재시도는 WAN-6 `backfill_symbol`을 그대로 재사용한다.

### 무결성 검증 (`alphablock verify`)

저장된 OHLCV가 백테스트에 쓸 만큼 온전한지 세 가지로 본다:

```bash
uv run alphablock verify   # 갭·중복·정렬 + 1m→상위TF 리샘플 정합성
uv run alphablock verify --strict   # 갭이 하나라도 있으면 실패(기본은 갭은 경고만)
```

1. **연속성(갭)**: `data.gaps.find_gaps`(WAN-35) 재사용 — 저장 봉 사이 내부 누락 구간.
2. **중복·정렬**: 기본키가 중복을 막지만, 총 봉 수 대비 고유 `open_time` 수와 오름차순을
   방어적으로 확인한다.
3. **상위TF 정합성**: 1m을 15m/1h/4h/1d로 리샘플한 결과가 거래소에서 직접 받아 저장한
   상위 TF 봉과 (최근 표본 버킷에서) OHLCV까지 일치하는지 대조한다. 1m 커버리지가 온전한
   버킷만 리샘플되므로 갭이 만드는 오탐은 없다. **불일치·중복·역순은 실패(exit 1)**,
   갭은 거래소 원인일 수 있어 기본은 경고만 한다(`--strict`로 실패 처리).

> 참고: 과거 수집 데몬이 **형성 중이던 상위TF 봉을 미확정 상태로 저장**해 둔 seam 봉이
> 있으면 정합성 검증에서 잡힌다. 해당 상위TF를 짧은 창으로 다시 받으면(`alphablock
> history --days 4 --timeframes 15m 1h 4h 1d`) 최종값으로 덮어써져 해소된다.

### 실측치 (2026-07-12 기준, 로컬 `data/ohlcv.db`)

| 항목 | 실측 |
| --- | --- |
| BTC/USDT:USDT 1m 3년 백필(WAN-51) | 1,576,801봉 / **415.9s** (2023-07-13~) |
| 전 심볼 15m 3년 백필(WAN-51) | 각 105,121봉 / ≈30s (2023-07-13~) |
| BTC/USDT:USDT 1h 3년 확장(WAN-51) | 26,281봉 / 7.6s (4개월→3년) |
| DB 파일 크기(3년 창 정렬 후) | **≈580 MB** (`data/ohlcv.db`, WAN-51 이전 ≈387 MB) |
| 1m 전체 로드(1.58M행) | BTC 4.6s / ETH 5.0s |
| 갭 / 중복 | **0 / 0** (전 심볼·TF) |
| 1m→상위TF 정합성 | **통과**(seam 봉 재수집 후) |

**인덱스**: 별도 인덱스 불필요 — 기본키 `(symbol, timeframe, open_time)`이 곧 범위 조회
인덱스라 `EXPLAIN QUERY PLAN`이 `SEARCH ... USING INDEX sqlite_autoindex_ohlcv_1
(symbol=? AND timeframe=? AND open_time>? AND open_time<?)`로 커버한다.

> 안전: 백필·검증 모두 **데이터 계층 한정**(공개 시세 조회)이다. 실주문·`live_trading`은
> 건드리지 않는다.

### 백필 창 정렬 & 완료 확인 (WAN-51)

A/B 본실험(WAN-46)의 데이터 창이 심볼·TF마다 제각각(BTC 1h만 4개월, 전 심볼 15m만
1년)이라 비교가 성립하지 않았다. **원인은 거래소 제약이 아니라 우리 수집 설정·이력**이다:

- **15m 1년**: `ALPHABLOCK_BACKFILL_LOOKBACK_DAYS`의 15m 만 365일이었다 → 1095일(3년)로 통일.
- **BTC 1h 4개월**: `alphablock collect`의 순방향 백필(`backfill_all`)은 저장된 마지막 봉
  *다음*부터만 채운다. BTC 1h가 한 번 짧은 룩백(≈120일)으로 시딩된 뒤로는 최신 봉만
  덧붙고 **과거 방향은 영영 안 메워졌다**(ETH·SOL은 3년 룩백으로 시딩돼 정상). 과거를 넓게
  메우는 건 `alphablock history --days N`의 몫인데, 그게 창을 다 못 채워도 조용했다.

**재발 방지 — 완료 확인**: `alphablock history`가 각 시리즈의 저장된 가장 오래된 봉이
요청 창 시작에 도달했는지 확인해 `[OK]`/`[미완(창 시작 미도달)]`로 출력하고, 미달 시
로그 경고를 남긴다(`SeriesBackfillResult.reached_requested_start`). 재시도·지수 백오프·
진행률 로그는 WAN-6/WAN-44에서 이미 재사용한다. 정렬 후 전 심볼 × (15m·1h·4h·1d·1m)의
창 시작이 모두 2023-07로 맞춰졌고(4h·1d는 그 이전부터), A/B 커버리지 리포트의
`coverage_ratio`가 전 쌍 1.0이다.

> A/B 재주행 결과(3년 정렬 후): 합산 B안 profit_factor는 여전히 **0.97(<1.0)**·평균
> 손익 소폭 음수 → **WAN-46 결론(기본값 보류) 유지**. 표본은 커졌으나 B안이 인샘플에서
> 손익분기를 넘지 못하므로 진입 기본값 전환 판단은 WAN-50 워크포워드/OOS 게이트가 맡는다.

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

### `.env.example` 편집 규칙 (WAN-40)

새 설정을 추가할 때는 파일 **끝에 무작정 붙이지 말고 관련 기능 섹션 말미**에 넣는다(예:
리스크 관련 키는 리스크 섹션 아래). 그래야 문서 구조가 유지되고 리뷰가 쉽다. `.env.example`
은 `.gitattributes` 의 `merge=union` 으로 지정돼 있어, 두 브랜치가 서로 다른 블록을 파일에
추가해도 병합 충돌 없이 양쪽 추가분이 모두 보존된다. 다만 **union 은 같은 키를 양쪽에서
다르게 *수정*한 경우까지 해소하지 못하고 두 줄을 모두 남긴다** — 이렇게 생긴 중복 키는
`tests/test_env_example.py` 가 CI에서 잡으므로, 충돌 없이 병합됐더라도 결과를 검토하고
중복을 정리한다. 같은 테스트가 `.env.example` 키와 `Settings` 필드의 1:1 대응도 검증하니,
새 필드를 추가하면 `.env.example` 문서화도 함께 갱신한다.

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
