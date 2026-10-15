# 스토캐스틱 팔 페이퍼 켜기 — WAN-451 ③ 단계 런북

> 팔(스토캐스틱 × 오더블록)을 엔진 C와 함께 페이퍼로 돌리기 시작하는 순서다. 실주문은 없다 —
> `ALPHABLOCK_LIVE_TRADING=false` 그대로다. 켜는 순간이 **규칙이 바뀌는 리셋 이음매**다.

## 켜면 무엇이 바뀌나

| 항목 | 지금 | 팔을 켜면 |
| -- | -- | -- |
| 감시 시리즈 | 설정 종목 × 15m·1h·2h·4h(또는 0개) | **그 주 목록 31종목 × 10TF**(15m·1h·2h·3h·4h·6h·8h·12h·1d·1w) + 목록에서 빠졌지만 포지션·주문이 남은 칸 |
| 진입 | 칸당 대기 주문 1개 | **탭마다 독립 주문**(정본 북과 같다 · 칸에 포지션이 있을 때만 막힌다) |
| 진입 조건 | 볼린저 지정가 | + 첫 탭만 · 손절폭 ≥ 4% · 직전 확정봉 %K < 25 · 이번 주 목록 안 · 몰림 규칙(직전 24h 신호 수 · BTC 4h −2.5%) |
| 체결 | 닿으면 체결 | **5bp 관통해야 체결**(정본 `pen_5bp`) |
| 손절 · 청산 | 존 경계 · 고정 R 익절 | **2배 손절** · 익절 없음 · **4봉 뒤 시간 청산**(직전 1분 종가) |
| 크기 | 거래당 1% | **2.5524%**(맞춤 크기 2.8172% × λ 0.906) × 몰림 예산 × 폭락 시 0.5 |
| 같은 1분 | — | 가격 순서(체결가 > 앞 포지션 손절가면 체결이 먼저) |
| 오래된 존 | 1500봉 창만 | **2년치**로 처음 탐지 + 창 밖 존 기억 |
| 일 손실 서킷브레이커 5% | 켬 | **끔**(정본에 없는 규칙) |
| 알림 | 체결·청산·일일 요약 | 팔 규칙으로 건너뛴 것(목록 · 몰림 · %K · 손절폭 · 칸 점유)은 **일일 요약**에만 모은다 |

정본(백테스트)과 같은 규칙인지는 켜기 전 관문이 확인했다 — 실제 러너 재생 거래 ≡ 정본 거래
(2026-04-01 ~ 07-10 · 31종목 × 10TF · 신호 38 = 38 · 거래 13 = 13 · `live/arm_gate.py`).

## 순서

### 0. 배포

```bash
cd ~/AlphaBlock && ./scripts/deploy.sh
```

### 1. 수집기에 종목 목록·팔 데이터를 켠다 (켜기 며칠 전에 해 둔다)

`.env`에 한 줄을 넣고 수집기를 다시 띄운다:

```bash
cd ~/AlphaBlock && echo 'ALPHABLOCK_UNIVERSE_ENABLED=true' >> .env
```

```bash
sudo systemctl restart alphablock-collector
```

- 수집기가 상장 USDT 무기한 전체의 일봉을 모으고(첫 적재 약 6분), 월요일 00:00 UTC(KST 09:00)
  뒤에 그 주 31종목을 정한다. 목록이 정해지면 31종목 × 전 TF를 받기 시작하고, 상위TF 이력을
  **2년**까지 뒤로 늘린다(백그라운드 · 십수 분 · DB 약 +300MB).
- 러너는 아직 옛 설정 그대로 돈다(팔 스위치가 꺼져 있으면 목록을 안 본다).

### 2. 준비됐나 점검한다

```bash
cd ~/AlphaBlock && uv run alphablock arm-check
```

- **§2 이번 주 목록 · §3 데이터(최신 · 2년 이력)가 ✅**여야 한다. §3에 막힌 쌍이 남아 있으면
  수집기 이력 확장이 아직 도는 중이거나 데이터 구멍이다(`uv run alphablock backfill --repair`).
- §1 설정은 이 단계에서는 ❌가 정상이다(아직 안 바꿨다) — 무엇을 넣어야 하는지 그대로 보여 준다.

### 3. 설정을 바꾼다

`.env`에서 아래 값으로 맞춘다(같은 키가 이미 있으면 그 줄을 고친다 — 두 번 쓰면 뒤가 이긴다):

```
ALPHABLOCK_LIVE_ARM_ENABLED=true
ALPHABLOCK_LIVE_ZONE_LIMIT_ENABLED=true
ALPHABLOCK_LIVE_TSMOM_ENABLED=true
ALPHABLOCK_UNIVERSE_ENABLED=true
ALPHABLOCK_CONFLUENCE__RETAP_MODE=once
ALPHABLOCK_LIVE_REENTRY_ENTRY_RULE=off
ALPHABLOCK_CONFLUENCE__FILL_PENETRATION_BPS=5
ALPHABLOCK_RISK_SIZING__RISK_PER_TRADE=0.025523832
ALPHABLOCK_LIVE_TICK_FEED_ENABLED=false
```

- 마지막 줄(틱 피드 끄기)은 선택이다. 팔은 1분봉으로만 판정해 틱을 안 쓴다 — 꺼도 결과가 같고
  웹소켓 하나를 아낀다.
- 러너는 위 값 중 하나라도 어긋나면 **기동을 거부한다**(조용히 덮어쓰지 않는다).

다시 점검한다 — 이번에는 전부 ✅여야 한다(§4 장부 경고는 다음 단계가 지운다):

```bash
cd ~/AlphaBlock && uv run alphablock arm-check
```

### 4. 리셋하고 켠다 (리셋 이음매)

```bash
cd ~/AlphaBlock && sudo scripts/paper-reset.sh --yes
```

- 러너·대시보드를 멈추고, 장부(거래 · 포지션 · 주문 · 세션)와 상태 테이블(엔진 스냅샷 · 엔진 C 원장 ·
  팔 신호 장부)을 **CSV로 내보낸 뒤** 비우고, 다시 띄운다. 시세는 안 건드린다.
- ⚠️ **엔진 C 원장도 비워진다** — 팔과 엔진 C가 같은 날 0에서 함께 시작해야 정본(합성)과 같은
  선상이다. 엔진 C 크기(k·λ)도 이번 재산출로 바뀌었다(1.590·0.885 → 1.569·0.906).
- `data/live_runtime_state.json`(Health 탭 상태 파일)은 스크립트가 안 지운다 — 옛 표시가 남아
  보이면 러너를 멈춘 상태에서 지운다.

### 5. 첫 기동을 지켜본다

```bash
journalctl -u alphablock-live -f
```

- 처음 한 번은 310 시리즈마다 **2년치로 오더블록을 찾는다** — 서버에서 **약 10분**(로컬 1분).
  그동안 폴링이 늦다. 그 뒤로는 매분 몇 초다.
- 기동 로그에 「스토캐스틱 팔 켜짐(WAN-451): 하한 4% · %K < 25 · 손절 ×2 · ts4 · 폭락 ×0.5 · 서킷브레이커
  꺼짐 · 매주 종목 목록 · 몰림 규칙(그림자 주문)」 한 줄이 찍힌다.
- 메모리: 팔 310 시리즈 실측 약 213MB(서버 측정 WAN-451 ④).

## 켠 뒤 확인

| 무엇 | 어떻게 |
| -- | -- |
| 이번 주 목록 | `uv run alphablock universe` |
| 데이터·설정 | `uv run alphablock arm-check`(켠 뒤에도 그대로 쓸 수 있다) |
| 팔 신호가 쌓이나 | `sqlite3 data/ohlcv.db "SELECT COUNT(*), SUM(counted) FROM arm_signals;"` |
| 진입·거부 | 텔레그램 체결 알림 · 일일 요약의 「팔 규칙으로 건너뜀」 |
| 엔진 C | 매일 KST 09:00 「📈 엔진 C 리밸런싱」 · `uv run alphablock tsmom-parity` |

팔 진입은 드물다(손절폭 ≥ 4% · %K < 25 · 몰림 규칙) — 정본 기준 31종목 × 10TF에서 100일에 십수 건이다.
며칠 동안 진입이 없어도 이상이 아니다.

## 되돌리기

리셋 이음매에서 한다 — `.env`의 `ALPHABLOCK_LIVE_ARM_ENABLED=false`(그리고 바꾼 값들을 원래대로) +
`sudo scripts/paper-reset.sh --yes`. 리셋 없이 끄면 팔 포지션이 옛 규칙 러너에 남아 섞인다.

## 실제 장부가 정본과 같은가 — 주기적으로(예: 매주)

서버에서 돌리지 않는다(정본 후보 생성이 2년치 탐지라 2코어 서버에서는 몇 시간 · 러너와 코어를 다툰다).
서버 DB를 내려받아 로컬에서 돌린다:

```bash
scp rocky@<서버>:~/AlphaBlock/data/ohlcv.db ~/Downloads/server-ohlcv.db
```

```bash
cd ~/AlphaBlock && uv run alphablock arm-parity --db ~/Downloads/server-ohlcv.db
```

- 첫 가동 세션부터 BTC 1분봉 끝까지, **실제 주간 목록**으로 정본 팔을 돌려 장부와 맞댄다 —
  몰림 신호(장부 `arm_signals`) · 거래(진입 시각·가격 · 2배 손절가 · 크기 배율 · 청산 시각·사유).
  로컬 M1 약 30분(31종목 × 10TF).
- 어긋나면 종료 코드 1이고 거래마다 원인 후보를 붙인다 — **러너 공백**(따라잡기 한도 48시간을 넘는
  틈) · 그 밖은 **데이터 또는 로직**이다. 후자는 같은 기간을 재생 대조(`uv run python -m
  live.arm_gate`)로 돌려 「러너가 그 데이터로 다시 돌면 정본과 같나」를 보면 가른다.
- ⚠️ 서버 DB 파일이 크다(수 GB) — 러너가 쓰는 중에 복사하면 깨질 수 있으니
  `scripts/db-backup.sh`(검증하는 백업)로 뜬 파일을 내려받는다.
