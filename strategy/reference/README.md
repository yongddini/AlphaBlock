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


---

# 참조 구현 2 — LuxAlgo *Order Block Detector* (WAN-405 · **옵트인 측정 전용**)

원본: [Order Block Detector — LuxAlgo](https://www.tradingview.com/u/LuxAlgo/) · **CC BY-NC-SA
4.0**, © LuxAlgo. 원문 Pine: [`luxalgo_ob_detector.pine`](./luxalgo_ob_detector.pine)
(**사용자가 제공한 원문 그대로** · 라이선스 헤더 보존).
이식: [`strategy/lux_order_blocks.py`](../lux_order_blocks.py).

🚨 **채택 탐지기가 아니다** — 채택 경로는 위 FluxCharts 이식이고, 이쪽은
`harness.detect_order_blocks(detector="lux")`로만 들어온다(안 켜면 비트 재현).

## 탐지 규칙 (원문에서 추출 · 테스트가 조건식을 대조한다)

* `length` = 5(원본 기본), 방향별 렌더 3개(`bull_ext_last`/`bear_ext_last`).
* `upper = ta.highest(length)` · `lower = ta.lowest(length)` — 인자 하나짜리라 각각
  `high`/`low`가 기본 소스다(현재 봉 **포함** 최근 `length`봉).
* 추세 상태 `os := high[length] > upper ? 0 : low[length] < lower ? 1 : os[1]`.
* 생성: `phv = ta.pivothigh(volume, length, length)`가 참일 때
  **강세**(`os == 1`) `[hl2[length], low[length]]` · **약세**(`os == 0`) `[high[length], hl2[length]]`.
  박스 왼쪽 변은 `time[length]`(피벗 봉) — 즉 **고정 `length`봉 지연**.
* 소멸: `Wick`(기본)이면 `target_bull = lower` · `target_bear = upper`이고
  `bull ? target < 존바닥 : target > 존천장`이면 배열에서 **제거**. `breaker` 단계가 없다.
* 🚨 **생성 → 소멸이 같은 봉에서 연달아** 돈다 → 갓 태어난 존도 그 `length`봉 안에 바닥이
  뚫렸으면 **태어나자마자 지워진다**(「탄생 시점 소급 검사」 · 우리 이식이 그대로 재현).

## 이식이 원본과 **의도적으로** 다른 점 (★사용자 결정: 「(가) 의도를 이식」)

1. **소멸 루프의 결함을 재현하지 않는다** — 원문은 `for element in target_array` 안에서 그
   배열을 `array.remove` 하고(인덱스가 밀려 건너뛰는 원소가 생긴다) `array.indexof`가
   **값**의 첫 인덱스를 돌려준다(바닥 값이 겹치면 엉뚱한 존을 지운다). 우리는 **의도**를
   구현하므로 **원본보다 더 많이 죽인다**.
2. **렌더링을 이식하지 않는다** — 원문은 `barstate.isfirst`에 박스를 만들고 `islast`에서
   좌표만 갈아끼우며 `box.delete()`가 **없어** 존이 줄면 옛 박스가 화면에 남는다. 화면
   아티팩트라 백테스트에 안 들어온다.
3. **존의 생애를 지우지 않고 기록한다**(WAN-47 생존 편향) — 소멸 **시점**은 원본과 같다.
4. **확정봉만 먹는다**(`closed=True`, WAN-314) — 원문은 형성 중인 봉을 틱마다 다시 계산해
   `phv`가 켜졌다 꺼졌다 한다. 우리가 더 보수적인 쪽이고, **`length`봉 지연은 그대로**다.
5. **`Mitigation Methods = Close` 옵션은 안 옮겼다** — 우리 쪽 같은 축
   (`zone_invalidation`)의 기본값이 `wick`이라 원본 기본과 맞는다. 옮기려면 그 축과 **한
   곳에서** 다뤄야 한다(두 곳에 같은 노브가 생기면 조용히 갈라진다).
6. ⚠️ **`ta.pivothigh`의 동률 처리는 우리가 고른 해석이다** — 내장 함수라 원문에 구현이
   없어 좌우 **모두 강부등호**로 갔다(거래량 동률은 드물다).
