# 오더블록 참조 구현 — Fluxchart Volumized Order Blocks

원본: [Volumized Order Blocks | Flux Charts](https://www.tradingview.com/script/bLdpFVuq-Order-Blocks-Flux-Charts/) — TradingView 오픈소스, **MPL-2.0**, © fluxchart.
원문 Pine: [`fluxchart_volumized_ob.pine`](./fluxchart_volumized_ob.pine).

WAN-7의 Python 이식은 이 명세를 기준으로 하며, 아래는 원문 코드에서 추출한 **탐지 알고리즘**이다. (원본 인디케이터는 존을 "그리는" 것까지가 범위이고, 실제 진입 시그널은 없다. 시그널 레이어는 AlphaBlock의 추가 설계 — 아래 "시그널(우리 확장)" 참고.)

## 파라미터 (원본 기본값)
- `swing_length` = 10 (min 3). 스윙 탐지 길이. 작을수록 작은 OB.
- `zone_invalidation` = `"Wick"` | `"Close"` (기본 Wick). 무효화 판정 기준.
- `zone_count` = High/Medium/Low/One → 방향별 렌더 개수 10/5/3/1 (기본 Low=3).
- `combine_obs` = true. 겹치는 동일방향 존 병합.
- `max_atr_mult` = 3.5, `atr_length` = 10. OB 높이가 `ATR*max_atr_mult`보다 크면 버림.
- `max_order_blocks` = 30 (방향별 내부 리스트 상한).
- `max_distance_to_last_bar` = 1750 (성능용; 최근 N봉만 탐지).

## 스윙 탐지 `findOBSwings(len)`
- `upper = highest(len)`, `lower = lowest(len)` (현재 봉 기준 롤링).
- 상태 `swingType`: `high[len] > upper` → 0(고점형), `low[len] < lower` → 1(저점형), 아니면 유지.
- `swingType`가 0으로 **바뀌는** 순간: `top = {x: bar_index[len], y: high[len], vol: volume[len]}`.
- 1로 **바뀌는** 순간: `bottom = {x: bar_index[len], y: low[len], vol: volume[len]}`.
- 즉 `len`봉 지연된 확정 스윙. top/bottom은 각각 최신값을 계속 보관(`var`).

## 강세(Bullish) OB
탐지에 쓰는 max/min은 body가 아니라 **고가/저가**(`useBody=false`).

무효화(먼저 처리): 기존 강세 OB 각각에 대해
- breaker 아님 & `(Wick? low : min(open,close)) < OB.bottom` → `breaker=true`, `breakTime=time`, `bbVolume=volume`.
- 이미 breaker & `high > OB.top` → 리스트에서 제거.

생성: `close > top.y` 이고 `top`이 아직 crossed 아니면 → `top.crossed=true` 후
- 스윙 지점부터 현재 직전까지 `i=1..(bar_index-top.x)-1` 순회하며 **가장 낮은 low**를 찾는다:
  - `boxBtm = min(low[i])`, 그 최저 봉의 `boxTop = high[i]`, `boxLoc = time[i]`.
- `top = boxTop`, `bottom = boxBtm`, `startTime = boxLoc`.
- `obVolume = volume + volume[1] + volume[2]` (최근 3봉 합).
- `obLowVolume = volume[2]` (가장 오래된 봉), `obHighVolume = volume + volume[1]` (최근 2봉).
- 필터: `abs(top-bottom) <= atr*max_atr_mult` 이면 리스트 앞에 추가(unshift), 초과 시 오래된 것 pop.

## 약세(Bearish) OB (강세와 대칭)
무효화:
- breaker 아님 & `(Wick? high : max(open,close)) > OB.top` → breaker.
- 이미 breaker & `low < OB.bottom` → 제거.

생성: `close < btm.y` 이고 `btm` crossed 아니면 →
- `i=1..(bar_index-btm.x)-1` 순회하며 **가장 높은 high**를 찾아: `boxTop=max(high[i])`, 그 봉의 `boxBtm=low[i]`, `boxLoc=time[i]`.
- `obVolume = 3봉 합`, `obLowVolume = volume + volume[1]`, `obHighVolume = volume[2]`.
- 동일 ATR 필터 후 추가.

## 볼륨 퍼센트(표시용)
`percentage = int(min(obHighVolume, obLowVolume) / max(obHighVolume, obLowVolume) * 100)`.

## 존 병합 `combineOBsFunc`
- 같은 방향(obType)인 두 존이 겹치면(IoU 교집합/합집합 * 100 > `overlap_threshold`=0) 병합.
- 병합 결과: `top=max(top)`, `bottom=min(bottom)`, `startTime=min`, `breakTime=max`, 볼륨/저볼/고볼 각각 합산, `breaker = A or B`. 병합 가능분이 없어질 때까지 반복.

## 최종 선택 `handleOrderBlocksFinal` (봉 확정 시 `barstate.isconfirmed`)
- 방향별로 리스트 앞에서부터 `zone_count`개까지만 채택 → 병합 → 유효한 것만 렌더.
- **중요**: 갱신은 봉이 **확정(closed)** 됐을 때만. 실시간 미확정봉으로 새 OB를 만들지 않는다(WAN-6의 `closed=true` 봉만 입력).

## 시그널 (우리 확장 — 원본에 없음)
원본은 존 탐지/무효화까지만 한다. AlphaBlock 진입 시그널 기본안(파라미터화):
- 활성(비-breaker) OB 존에 가격이 재진입(tap)하면 해당 방향 진입 후보.
- 무효화(breaker 전환)되면 시그널 취소.
- 구체 규칙(리테스트 확인, 손절=존 반대편 등)은 WAN-9(실행)·백테스트(WAN-8)와 함께 확정.

## 패리티 테스트 방침
- 동일 심볼·타임프레임 고정 구간(샘플 OHLCV 스냅샷)에 대해, 위 로직으로 산출한 OB의 top/bottom/방향/startTime/breaker 상태가 TradingView 원본 결과와 일치하는지 대조.
- 부동소수/시간 단위 차이를 감안한 허용 오차를 명시한다.
