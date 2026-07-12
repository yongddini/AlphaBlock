"""실시간(봉내) RSI 계산기 — 라이브·백테스트 공용 상태 머신 (WAN-41).

트레이딩뷰가 화면에 그리는 RSI는 **매 틱마다 진행 중인 봉을 다시 계산**한 값이다.
그때 `close`는 확정 종가가 아니라 **현재가**이고, Wilder RMA 상태는 직전 확정봉
값에서 고정한 채 현재 봉만 새로 얹는다(Pine의 rollback). 참조: `strategy/reference/
tradingview_rsi.pine`, 이슈 WAN-41의 "실시간 RSI 구현 명세" 코멘트.

RSI **수식**은 `strategy/indicators.rsi()`(Wilder RMA)와 **완전히 동일**하다 — "실시간"은
수식이 아니라 실행 방식(현재가를 임시 종가로 얹는 것)의 문제다. 이 모듈은 그 실행
방식을 O(1) 증분 상태 머신으로 구현한다:

    change   = current_price - last_closed_close
    gain     = max(change, 0);  loss = max(-change, 0)
    avg_gain = (avg_gain_prev * (n - 1) + gain) / n     # n = length (기본 14)
    avg_loss = (avg_loss_prev * (n - 1) + loss) / n
    rsi_live = 100 - 100 / (1 + avg_gain / avg_loss)

`value(current_price)`는 상태를 바꾸지 않고 현재가를 얹은 실시간 RSI를 반환한다.
봉이 **마감**되면 `commit(closed_price)`로 그 종가를 확정 상태에 반영한다.

## 라이브 vs 백테스트 (동일 함수 공유)

- **라이브**: 웹소켓 현재가로 `value()`를 매 틱 호출하고, 상위TF 봉이 마감될 때
  `commit()`으로 상태를 굴린다. 1분봉이 필요 없다.
- **백테스트**: 과거 상위TF OHLC만으로는 "존에 닿은 순간의 가격"을 알 수 없어
  1분봉을 서브스텝으로 써서 `current_price`를 공급한다(`backtest.substep`). 1분봉은
  RSI 수식 때문이 아니라 **봉 내부 경로 재현**을 위해 필요하다.

두 경로가 같은 `RealtimeRsi` 상태 머신을 쓰므로 값이 일치한다(단위 테스트로 검증).

## 시딩 규칙 (`indicators.rsi`와의 패리티)

확정봉 종가를 시간 순서대로 `commit()`하면 `_wilder_rma`(=`indicators.rsi`)와 동일한
RMA 상태가 만들어진다: 첫 종가는 변화량이 없어 건너뛰고, 최초 `length`개의 유효
변화량 단순평균(SMA)을 시드로 삼은 뒤 재귀 스무딩한다. 따라서 임의의 확정 시퀀스
`closes`에 대해, `closes[:i]`를 커밋한 뒤 `value(closes[i])`는 `indicators.rsi`의
`i`번째 값과 일치한다(워밍업 구간은 둘 다 값 없음).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


def _rsi_from(avg_gain: float, avg_loss: float) -> float | None:
    """RMA 상승/하락 평균에서 RSI를 낸다. `indicators.rsi`의 엣지 케이스와 일치.

    `avg_loss == 0`이면 100, `avg_gain == 0`이면 0, 둘 다 0(완전 평탄)이면 정의되지
    않아 `None`(트레이딩뷰 원본은 `down==0`을 먼저 평가해 100을 주지만, `indicators.rsi`는
    `0/0 → NaN`을 내므로 그 동작에 맞춰 값 없음으로 취급한다).
    """
    if avg_loss == 0.0:
        return None if avg_gain == 0.0 else 100.0
    if avg_gain == 0.0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


@dataclass
class RealtimeRsi:
    """봉내 실시간 RSI 상태 머신 (Wilder RMA, O(1) 증분).

    사용법::

        rsi = RealtimeRsi(length=14)
        for close in closed_bar_closes:      # 확정봉 시딩/진행
            rsi.commit(close)
        live = rsi.value(current_price)      # 진행 중 봉의 실시간 RSI (None=워밍업)

    확정봉 종가 리스트로 한 번에 시딩하려면 :meth:`seed_from_closed`를 쓴다. 값은
    상태를 바꾸지 않는 :meth:`value`로 조회하고, 봉이 마감되면 :meth:`commit`로 상태를
    굴린다.
    """

    length: int
    #: 직전 확정봉까지의 Wilder RMA(상승/하락). 시드 미형성이면 None.
    avg_gain: float | None = None
    avg_loss: float | None = None
    #: 직전 확정봉 종가. 아직 아무 봉도 커밋 안 됐으면 None.
    last_close: float | None = None
    #: 시드(SMA) 형성 전 누적 상태.
    _seed_gain_sum: float = field(default=0.0, repr=False)
    _seed_loss_sum: float = field(default=0.0, repr=False)
    _seed_count: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.length < 1:
            raise ValueError(f"length는 1 이상이어야 합니다: {self.length}")

    @classmethod
    def seed_from_closed(cls, closes: Sequence[float], length: int = 14) -> RealtimeRsi:
        """확정봉 종가 시퀀스로 시딩된 상태 머신을 만든다.

        `closes`는 시간 오름차순 확정봉 종가여야 한다(호출자가 정렬·확정봉 필터를
        마친 값). 각 종가를 순서대로 커밋한 것과 동일하다.
        """
        state = cls(length=length)
        for close in closes:
            state.commit(float(close))
        return state

    @property
    def ready(self) -> bool:
        """실시간 값을 낼 수 있을 만큼 시딩됐는지 여부."""
        if self.last_close is None:
            return False
        # 시드가 이미 형성됐거나(정상 상태), 다음 한 봉이면 시드가 완성되는 시점.
        return self.avg_gain is not None or self._seed_count == self.length - 1

    def _project(self, current_price: float) -> tuple[float, float] | None:
        """현재가를 진행 중 봉의 임시 종가로 얹은 (avg_gain, avg_loss). 워밍업이면 None."""
        if self.last_close is None:
            return None
        change = current_price - self.last_close
        gain = change if change > 0.0 else 0.0
        loss = -change if change < 0.0 else 0.0
        n = self.length
        if self.avg_gain is None or self.avg_loss is None:
            # 아직 시드 형성 전 — 이 변화량이 시드를 완성할 때만 값이 존재한다.
            if self._seed_count != n - 1:
                return None
            return (self._seed_gain_sum + gain) / n, (self._seed_loss_sum + loss) / n
        return (
            (self.avg_gain * (n - 1) + gain) / n,
            (self.avg_loss * (n - 1) + loss) / n,
        )

    def value(self, current_price: float) -> float | None:
        """상태를 바꾸지 않고 현재가를 얹은 실시간 RSI를 반환한다(워밍업이면 None)."""
        projected = self._project(float(current_price))
        if projected is None:
            return None
        return _rsi_from(*projected)

    def commit(self, closed_price: float) -> None:
        """봉이 마감됐을 때 그 확정 종가로 RMA 상태를 굴린다."""
        price = float(closed_price)
        if self.last_close is None:
            self.last_close = price
            return
        change = price - self.last_close
        gain = change if change > 0.0 else 0.0
        loss = -change if change < 0.0 else 0.0
        n = self.length
        if self.avg_gain is None or self.avg_loss is None:
            self._seed_gain_sum += gain
            self._seed_loss_sum += loss
            self._seed_count += 1
            if self._seed_count == n:
                self.avg_gain = self._seed_gain_sum / n
                self.avg_loss = self._seed_loss_sum / n
        else:
            self.avg_gain = (self.avg_gain * (n - 1) + gain) / n
            self.avg_loss = (self.avg_loss * (n - 1) + loss) / n
        self.last_close = price
