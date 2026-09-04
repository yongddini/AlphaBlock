"""LuxAlgo *Order Block Detector*의 탐지 규칙 이식 — **옵트인 측정 전용** (WAN-405).

우리 채택 탐지기는 FluxCharts *Volumized Order Blocks*의 이식이고
(`strategy/order_blocks.py`, WAN-7), 이 모듈은 **이름만 같은 다른 지표**인 LuxAlgo
*Order Block Detector*를 같은 자리에 끼울 수 있게 옮긴 것이다. 원문 사양은
[`strategy/reference/luxalgo_ob_detector.pine`](reference/luxalgo_ob_detector.pine).

🚨 **기본값이 아니다** — 채택 경로는 이 모듈을 부르지 않는다. 부르는 곳은 측정 축
(`backtest.wan169_leverage_book._Task.detector="lux"`)뿐이고, 그 축을 안 켜면 이 파일은
import조차 되지 않는 것과 같은 결과를 낸다(끄면 **비트 재현**).

## 두 탐지기가 무엇이 다른가 (WAN-405 §부록)

| 축 | FluxCharts (채택) | LuxAlgo (이 모듈) |
| -- | -- | -- |
| 존을 만드는 사건 | 종가가 스윙 고점 **돌파** | **거래량 국소 최대** 봉(`pivothigh(volume,5,5)`) |
| 존 경계 | 되돌아보며 최저 저가 탐색(여러 봉) | `[hl2[5], low[5]]` — **그 봉 하나의 아래 절반** |
| 생성 지연 | 가변 | **고정 5봉** |
| 무효화 비교 | 그 봉의 저가 vs 존 바닥 | **최근 5봉 최저** vs 존 바닥 |
| 존폭 상한 | `ATR(10) × 3.5` (탐지 단계 컷) | **없음** |
| 병합 | `combine_obs` | **없음** |

## 🚨 바꾸지 않은 것 — 무효화·생애주기·손절선은 **우리 것 그대로**

이 모듈이 다시 정의하는 것은 **「무엇을 존이라 부를 것인가」 하나**다. 그래야 손익 차이를
「탐지가 좋아서」로 귀속할 수 있다(축을 둘 흔들면 못 가른다). 그래서:

* 산출 타입이 `OrderBlockResult`로 **같다** — 시그널 층(`signals`/`retap_signals`)은
  `strategy.order_blocks.signals_for_archive`를 **그대로 재사용**한다(사본 금지).
* 존의 생애를 **지우지 않고 기록**한다(WAN-47) — 원본은 배열에서 빼 버리지만 백테스트에서
  그러면 생존자 편향이 생긴다. 소멸 **시점**은 원본과 같다.
* 손절선은 여전히 존 아랫변(강세)이고 그 존이 죽는 시각이 손절과 같은 선이다.

## 📌 소멸 규칙은 사실상 우리 것과 같다 — 단 「태어나는 순간」만 다르다

원본의 `remove_mitigated`는 **매 봉** 돈다. 그래서 `min(low[t-4..t]) < 존바닥`이 **처음
참이 되는 봉**은 정의상 **그 봉의 저가가 처음 존 바닥 아래로 간 봉**이다(창은 현재 봉에서만
새 저가를 얻는다). 즉 확정 이후로는 우리 규칙과 **글자 그대로 같다** —
🚨 **`length`봉은 「지울 기회가 length번」이지 「length봉 뒤에 지워진다」가 아니다.**

⚠️ **다만 탄생 시점의 소급 검사는 우리에 없던 동작이다.** 존 바닥은 `low[length]`인데 소멸
검사는 그 **이후 length봉**을 보고, 생성과 소멸이 **같은 봉에서 연달아** 돈다 — 그래서 존이
확정되는 순간 이미 가격이 바닥 아래로 갔으면 **태어나자마자 지워진다**(사용자 결정으로
원본 정의대로 넣었다 · `birth_mitigation`으로 끌 수 있고 그 크기는 인구조사가 센다).

## ⚠️ 원본 소멸 루프의 결함은 **재현하지 않는다** (★사용자 결정: 「(가) 의도를 이식」)

원본 루프는 순회 중 그 배열을 수정하고 `array.indexof`가 값의 첫 인덱스를 돌려줘, 건너뛰는
존과 엉뚱하게 지워지는 존이 생긴다. 그 런타임 세부를 파이썬으로 「충실히」 옮기는 것은
**무엇을 이식했는지 알 수 없는 코드**가 되므로(이 저장소가 가장 경계하는 「라벨과 동작이
어긋남」) 우리는 **의도**를 구현한다 — 그래서 **원본보다 더 많이 죽는다**. 이 차이는
`tests/test_lux_order_blocks.py`가 「알려진 차이」로 고정한다(나중에 버그로 오해해 고치려다
원본에서 멀어지지 않게).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockResult,
    select_active,
)
from strategy.order_blocks import OrderBlockDetector, signals_for_archive


class LuxOrderBlockParams(BaseModel):
    """LuxAlgo 탐지 파라미터 — **원본 기본값 그대로**.

    🚨 **스윕 금지**(WAN-405 범위 밖 · WAN-161: 앞구간에서 눈금을 고르는 위험). 눈금을
    흔드는 것은 이 탐지기가 채택 후보로 올라간 **뒤에** 별건이다.
    """

    model_config = ConfigDict(frozen=True)

    length: int = Field(default=5, ge=1)
    """`Volume Pivot Length`(원본 기본 5) — 피벗 좌우 폭 · 생성 지연 · 소멸 창 길이를 **동시에**
    정한다(원본이 그 셋을 같은 인자로 쓴다)."""
    zone_limit: int = Field(default=3, ge=1)
    """방향별 렌더 개수(원본 `bull_ext_last`/`bear_ext_last` = 3). 렌더 뷰에만 쓰고 시그널은
    아카이브 전체에서 낸다(WAN-47) — FluxCharts 이식의 `zone_limit`과 같은 규약이다."""
    birth_mitigation: bool = True
    """탄생 시점 소급 검사(위 문단). `True`(기본)가 **원본 정의**다 — 끄면 「이미 죽은 자리에
    지정가를 거는」 셋업이 생긴다. 반사실로만 끈다."""


@dataclass(eq=False)
class _RawLuxZone:
    """탐지 진행 중 상태를 갖는 존 — 원본은 배열 원소 세 개(top/btm/left)로 들고 있다."""

    top: float
    bottom: float
    direction: OrderBlockDirection
    start_time: int
    confirmed_time: int
    pivot_volume: float
    breaker: bool = False
    break_time: int | None = None
    born_dead: bool = False
    """확정된 그 봉에서 곧바로 소멸했는가(탄생 시점 소급 검사에 걸림) — 인구조사용 관측."""
    tapped_times: list[int] = field(default_factory=list)
    _inside: bool = False

    def to_model(self) -> OrderBlock:
        return OrderBlock(
            direction=self.direction,
            top=self.top,
            bottom=self.bottom,
            start_time=self.start_time,
            confirmed_time=self.confirmed_time,
            # 🚨 LuxAlgo는 거래량을 **탐지에만** 쓰고 존에 싣지 않는다(WAN-405 §부록).
            # 모델이 요구하는 자리라 피벗 봉의 거래량을 메타데이터로 넣고 분해는 비워 둔다 —
            # 체결·청산·사이징 어디에도 쓰이지 않는 사후 분석용 필드다(WAN-77).
            ob_volume=self.pivot_volume,
            ob_low_volume=0.0,
            ob_high_volume=0.0,
            breaker=self.breaker,
            break_time=self.break_time,
            # 원본은 소멸 = 배열에서 제거이므로 「무효화 뒤에도 살아 있는 breaker 구간」이
            # 없다. 그래서 소멸 시각이 곧 무효화 시각이다(FluxCharts의 2단계와 다르다).
            swept_time=self.break_time,
            tapped_times=tuple(self.tapped_times),
            # LuxAlgo에는 변위(displacement) 개념이 없다 — 지어내지 않는다.
            displacement_atr=None,
        )


def _pivot_high(values: list[float], t: int, length: int) -> bool:
    """`ta.pivothigh(values, length, length)`가 봉 `t`에서 확정되는가.

    중심은 `t - length`이고 좌우 `length`봉보다 **모두 커야** 한다(동률은 피벗이 아니다).
    """
    center = t - length
    if center - length < 0:
        return False
    pivot = values[center]
    if any(values[i] >= pivot for i in range(center - length, center)):
        return False
    return all(values[i] < pivot for i in range(center + 1, t + 1))


def _rolling_extreme(values: list[float], length: int, *, maximum: bool) -> list[float]:
    """`ta.highest`/`ta.lowest` — 현재 봉을 **포함한** 최근 `length`봉의 극값.

    FluxCharts 이식(`strategy.order_blocks._rolling_max/_rolling_min`)과 **같은 규약**이다
    (`min_periods=1`이라 워밍업 구간은 있는 만큼으로 잰다).
    """
    series = pd.Series(values).rolling(window=length, min_periods=1)
    result = series.max() if maximum else series.min()
    return [float(v) for v in result.tolist()]


class LuxOrderBlockDetector:
    """LuxAlgo 탐지기 — `OrderBlockDetector`와 **같은 계약**(`run(df) -> OrderBlockResult`).

    같은 타입을 내는 것이 요점이다: 뒤에 붙는 시그널·후보·북 층이 하나도 안 바뀌어야
    「탐지기만 바꿨다」가 참이 된다.
    """

    def __init__(self, params: LuxOrderBlockParams | None = None) -> None:
        self.params = params or LuxOrderBlockParams()

    def run(self, df: pd.DataFrame) -> OrderBlockResult:
        # 확정봉 필터·정렬은 채택 탐지기와 **같은 함수**를 쓴다(사본을 만들면 갈라진다).
        frame = OrderBlockDetector._prepare(df)
        n = len(frame)
        if n == 0:
            return OrderBlockResult(order_blocks=[], signals=[])

        highs = frame["high"].astype(float).tolist()
        lows = frame["low"].astype(float).tolist()
        closes = frame["close"].astype(float).tolist()
        volumes = frame["volume"].astype(float).tolist()
        times = frame["open_time"].astype("int64").tolist()

        length = self.params.length
        upper = _rolling_extreme(highs, length, maximum=True)
        lower = _rolling_extreme(lows, length, maximum=False)

        archive: list[_RawLuxZone] = []
        active_bull: list[_RawLuxZone] = []
        active_bear: list[_RawLuxZone] = []
        os_state = 0

        for t in range(n):
            # ① 추세 상태 — FluxCharts의 `swing_type`과 같은 모양이고 길이만 다르다.
            if t >= length:
                lag = t - length
                if highs[lag] > upper[t]:
                    os_state = 0
                elif lows[lag] < lower[t]:
                    os_state = 1

            # ② 생성 — 거래량 피벗 봉의 절반. 원본과 **같은 순서**로 생성이 먼저다.
            if _pivot_high(volumes, t, length):
                lag = t - length
                mid = (highs[lag] + lows[lag]) / 2.0
                if os_state == 1:
                    zone = _RawLuxZone(
                        top=mid,
                        bottom=lows[lag],
                        direction=OrderBlockDirection.BULLISH,
                        start_time=times[lag],
                        confirmed_time=times[t],
                        pivot_volume=volumes[lag],
                    )
                    archive.append(zone)
                    active_bull.append(zone)
                else:
                    zone = _RawLuxZone(
                        top=highs[lag],
                        bottom=mid,
                        direction=OrderBlockDirection.BEARISH,
                        start_time=times[lag],
                        confirmed_time=times[t],
                        pivot_volume=volumes[lag],
                    )
                    archive.append(zone)
                    active_bear.append(zone)

            # ③ 소멸 — 갓 태어난 존도 **같은 봉에서** 검사 대상이다(탄생 시점 소급 검사).
            #    `birth_mitigation=False`면 그 반사실로 확정 봉을 건너뛴다.
            active_bull = self._step(
                active_bull,
                is_bullish=True,
                target=lower[t],
                t=t,
                highs=highs,
                lows=lows,
                times=times,
            )
            active_bear = self._step(
                active_bear,
                is_bullish=False,
                target=upper[t],
                t=t,
                highs=highs,
                lows=lows,
                times=times,
            )

        models = [zone.to_model() for zone in archive]
        # 🚨 시그널 층은 채택 탐지기의 **그 함수**다 — 존 정의만 바꾸고 탭·취소 규칙은 그대로.
        signals = signals_for_archive(models, times, highs, lows, closes)
        retap_signals = signals_for_archive(models, times, highs, lows, closes, include_retaps=True)
        rendered = select_active(models, times[-1], limit=self.params.zone_limit, combine=False)
        return OrderBlockResult(
            order_blocks=models,
            signals=signals,
            retap_signals=retap_signals,
            rendered_order_blocks=rendered,
        )

    def _step(
        self,
        zones: list[_RawLuxZone],
        *,
        is_bullish: bool,
        target: float,
        t: int,
        highs: list[float],
        lows: list[float],
        times: list[int],
    ) -> list[_RawLuxZone]:
        """이번 봉의 소멸·탭을 갱신하고 **아직 살아 있는 존**을 돌려준다.

        구조는 `OrderBlockDetector._invalidate`와 나란하다 — 다른 것은 비교 대상이
        「그 봉의 극값」이 아니라 **최근 `length`봉의 극값**(`target`)이라는 것 하나다.
        """
        still_active: list[_RawLuxZone] = []
        for zone in zones:
            born_now = zone.confirmed_time == times[t]
            if born_now and not self.params.birth_mitigation:
                # 반사실 팔: 확정 봉의 소급 검사를 건너뛴다(원본 정의가 아니다).
                mitigated = False
            else:
                mitigated = target < zone.bottom if is_bullish else target > zone.top
            if mitigated:
                zone.breaker = True
                zone.break_time = times[t]
                zone.born_dead = born_now
            # 탭(재진입) 전이 기록 — 무효화 봉의 탭도 남긴다(시그널 층이 `cancelled`로 찍는다).
            inside = lows[t] <= zone.top and highs[t] >= zone.bottom
            if inside and not zone._inside and times[t] > zone.confirmed_time:
                zone.tapped_times.append(times[t])
            zone._inside = inside
            if not mitigated:
                still_active.append(zone)
        return still_active


def detect_lux_order_blocks(
    df: pd.DataFrame, params: LuxOrderBlockParams | None = None
) -> OrderBlockResult:
    """`LuxOrderBlockDetector(params).run(df)`의 편의 함수."""
    return LuxOrderBlockDetector(params).run(df)
