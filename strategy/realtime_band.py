"""실시간(봉내) 이격 밴드 계산기 — 라이브·백테스트 공용 상태 머신 (WAN-119).

트레이딩뷰가 화면에 그리는 볼린저 밴드는 **매 틱마다 진행 중인 봉을 다시 계산**한
값이다. SMA20의 20번째 표본이 확정 종가가 아니라 **그 순간의 현재가**이므로, 봉이
형성되는 동안 밴드가 계속 움직인다. 사용자 관찰 그대로다(WAN-119): "봉 시작과 동시에
띠가 생기고, 봉이 어떻게 움직이느냐에 따라 값이 좀 달라진다."

이 모듈은 그 실행 방식을 상태 머신으로 구현한다 — `strategy.realtime_rsi.RealtimeRsi`와
**같은 모양**이다(`seed_from_closed` / `commit` / `value`). 밴드 **수식**은
`ConfluenceStrategy.deviation_filter_components`(= `indicators.sma`/`stdev`)와 동일하고,
"실시간"은 수식이 아니라 **20번째 표본에 무엇을 넣느냐**의 문제다:

    확정봉 19개 종가 + 현재가 → SMA20 / σ20 → band = anchor - direction_sign*width

## 왜 필요한가 (`tap`·`prev_closed`와 무엇이 다른가)

| 모드 | 20번째 표본 | 성질 |
| -- | -- | -- |
| `tap` | 탭 봉 **최종 종가** | 봉 중간 체결 시 미래 종가 참조 = **룩어헤드**(WAN-115) |
| `prev_closed` | 직전 **확정봉** 종가 | 룩어헤드 없음, but 현재 봉을 통째로 버려 **보수적** |
| `intrabar_live` | **그 순간의 현재가** | 룩어헤드 없이 재현 가능 · 트레이딩뷰/실매매에 충실 |
| `intrabar_causal` | 직전 **1분봉** 종가 | `intrabar_live`의 잔여 1분 룩어헤드까지 0 (WAN-120) |

앞 세 모드는 봉이 **딱 닫히는 순간에만** 일치한다(그때 현재가 = 탭 봉 종가). 그래서 A안
(탭 봉 종가 진입)에서는 `intrabar_live`가 `tap`과 **정확히 같은 값**이고, 차이는 봉
내부에서 체결되는 B안(지정가)에서만 생긴다 — `deviation_band_at` 참고.

이 상태 머신은 `intrabar_live`·`intrabar_causal` **둘 다**를 떠받친다 — 밴드 **수식**은
같고 두 모드는 `value(...)`에 **어느 가격을 넣느냐**만 다르기 때문이다(현재가 / 직전
서브스텝 종가). 그 지연선은 호출부가 쥔다(`backtest.zone_limit_backtest`의
`_IntrabarLiveLimit`) — 이 클래스는 "넣어 준 가격으로 밴드를 낸다"까지만 안다.

## 워밍업 (`indicators.sma`와의 패리티)

`sma(length=20)`의 최초 유효값은 인덱스 19다(= 확정봉 20개). 실시간 값은 확정봉
**19개 + 현재가**로 20표본을 채우므로, 확정봉이 `length-1`개 쌓인 시점부터 값이 나온다
— `tap` 모드와 **같은 봉에서** 워밍업이 풀린다(`prev_closed`처럼 한 봉 늦지 않는다).

## ⚠️ 한계 — 1분봉 근사 (틱 아님)

백테스트에서 공급되는 "현재가"의 최대 해상도는 1분봉 서브스텝이다(`backtest.substep`).
진짜 틱이 없으므로 체결 순간의 현재가를 **그 1분봉 종가로 근사**한다. 이는 WAN-96/98이
남긴 틱 한계와 같은 부류이며, `RealtimeRsi`가 체결 스텝에서 이미 쓰고 있는 것과 **같은
관행**이다(`substep.simulate_zone_limit_trade`). 자세한 성질과 잔여 편향은
`docs/decisions/wan119.md` §한계 참고.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from strategy.models import DeviationFilterParams


def _population_stdev(samples: Sequence[float], mean: float) -> float:
    """모표준편차(`ddof=0`) — `indicators.stdev`(`rolling.std(ddof=0)`)와 같은 정의.

    표본 수가 작아(기본 20) 2-패스로 곧바로 계산한다. 제곱합 누적(O(1) 증분)은
    BTC처럼 큰 가격에서 상쇄 오차가 커지므로 쓰지 않는다.
    """
    variance = sum((s - mean) ** 2 for s in samples) / len(samples)
    return math.sqrt(variance)


@dataclass
class RealtimeBand:
    """봉내 실시간 이격 밴드 상태 머신 (WAN-119).

    사용법::

        band = RealtimeBand.seed_from_closed(closed_htf_closes, filter_params)
        for step in substeps:                  # 1분 서브스텝
            if step.htf_bar_time != current:   # 상위TF 봉 마감
                band.commit(last_close_of_that_bar)
            live = band.value(step.close, direction_sign=1)   # None = 워밍업

    `value`는 상태를 바꾸지 않고 현재가를 20번째 표본으로 얹은 밴드를 낸다. 봉이
    마감되면 `commit`으로 확정 종가를 굴린다 — `RealtimeRsi`와 같은 계약이다.

    `atr` 폭은 지원하지 않는다(`__post_init__`에서 거부): ATR은 형성 중인 봉의
    고가·저가가 있어야 하는데, 그것을 안다는 건 그 봉이 닫혔다는 뜻이라 **실시간 값이
    될 수 없다**. 조용히 확정 ATR로 대체하면 "라벨과 실제 실행이 갈라지는" 그 부류의
    버그가 된다(WAN-95의 교훈) — 그래서 값을 지어내지 않고 거부한다.
    """

    filter_params: DeviationFilterParams
    #: 직전 확정봉 종가들(최근 `sma_length-1`개만 유지). 20번째 자리는 현재가 몫이다.
    _window: deque[float] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        if self.filter_params.width_kind == "atr":
            raise ValueError(
                "band_bar='intrabar_live'는 width_kind='atr'를 지원하지 않습니다 — "
                "ATR은 형성 중인 봉의 고가·저가를 알아야 하므로 실시간 값이 될 수 없습니다."
            )
        maxlen = self._window_size
        if self._window.maxlen != maxlen:
            self._window = deque(self._window, maxlen=maxlen)

    @property
    def _window_size(self) -> int:
        """유지할 확정봉 수 = `sma_length - 1`(나머지 한 자리가 현재가)."""
        return max(self.filter_params.sma_length - 1, 0)

    @property
    def _needs_window(self) -> bool:
        """SMA 기준선이나 σ 폭을 쓰면 확정봉 창이 필요하다."""
        params = self.filter_params
        return params.anchor == "sma" or params.width_kind == "stdev"

    @classmethod
    def seed_from_closed(
        cls, closes: Sequence[float], filter_params: DeviationFilterParams
    ) -> RealtimeBand:
        """확정봉 종가 시퀀스로 시딩된 상태 머신을 만든다.

        `closes`는 **탭 봉 직전까지의** 상위TF 확정봉 종가(시간 오름차순)여야 한다 —
        탭 봉 자신의 종가를 넣으면 그것이 곧 WAN-115가 잡아낸 룩어헤드다. 각 종가를
        순서대로 `commit`한 것과 동일하며, 창을 넘는 옛 종가는 자동으로 밀려난다.
        """
        state = cls(filter_params=filter_params)
        for close in closes:
            state.commit(float(close))
        return state

    @property
    def ready(self) -> bool:
        """실시간 값을 낼 수 있을 만큼 시딩됐는지 여부."""
        if not self._needs_window:
            return True
        return len(self._window) >= self._window_size

    def commit(self, closed_price: float) -> None:
        """봉이 마감됐을 때 그 확정 종가로 창을 굴린다."""
        if self._window_size == 0:
            return
        self._window.append(float(closed_price))

    def value(self, live_price: float, direction_sign: int) -> float | None:
        """현재가를 20번째 표본으로 얹은 밴드(`anchor - direction_sign*width`).

        워밍업이라 판정할 수 없으면 `None`. 반환값의 의미·부호 규약은
        `ConfluenceStrategy.deviation_band_at`과 **동일**하다(롱은 하단선, 숏은 상단선).
        """
        if not self.ready:
            return None
        price = float(live_price)
        params = self.filter_params

        samples: list[float] | None = None
        if self._needs_window:
            samples = [*self._window, price]

        if params.anchor == "sma":
            assert samples is not None
            anchor_val = sum(samples) / len(samples)
        else:
            anchor_val = price

        if params.width_kind == "pct":
            width_val = anchor_val * params.width_value
        else:  # "stdev" — `atr`은 __post_init__에서 이미 거부됐다.
            assert samples is not None
            mean = sum(samples) / len(samples)
            width_val = _population_stdev(samples, mean) * params.width_value

        if math.isnan(anchor_val) or math.isnan(width_val):
            return None
        return anchor_val - direction_sign * width_val
