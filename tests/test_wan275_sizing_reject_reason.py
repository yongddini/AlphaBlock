"""진입 거부 사유의 구체 가드별 표기 (WAN-275).

`ExecutionEngine.on_entry`가 사이징 수량 0을 낼 때, 예전의 "사이징 수량 0 — 진입 스킵"
한 뭉치 대신 **어느 가드에 걸렸는지**를 `entry_reject_reason`(= `ExecutionOutcome.reason`)에
담는지를 동작으로 고정한다. 이 문자열이 곧 `live/trade_timeline.py`·`live/fill_report.py`가
그대로 렌더하는 값이다(WAN-194 배선).

⚠️ 라벨만 세분화한다 — 거부 동작·수량·`reason_code`(집계용, `REJECT_CODE_SIZING`)는 불변.
"""

from __future__ import annotations

from execution.broker import PaperBroker
from execution.engine import REJECT_CODE_SIZING, EntryIntent, ExecutionEngine
from execution.risk import RiskManager, RiskParams
from execution.sizing import PositionSizingParams
from strategy.models import OrderBlockDirection

_DAY0 = 1_700_000_000_000


def _engine(sizing: PositionSizingParams, *, equity: float = 10_000.0) -> ExecutionEngine:
    return ExecutionEngine(
        broker=PaperBroker(),
        risk_manager=RiskManager(RiskParams(max_leverage=100.0)),
        sizing_params=sizing,
        equity=equity,
    )


def _intent(*, entry: float, stop: float) -> EntryIntent:
    return EntryIntent(
        symbol="LINK/USDT:USDT",
        timeframe="15m",
        direction=OrderBlockDirection.BULLISH,
        entry_price=entry,
        entry_time=_DAY0,
        stop_price=stop,
        take_profit_price=entry + 1.0,
    )


def test_stop_too_tight_reports_stop_percent_and_floor() -> None:
    """LINK 15m 케이스: 손절폭 0.20% < 0.30% 하한 → 사유에 실제 %와 하한이 병기된다."""
    # 이슈 원 관찰: 진입 8.316663 · 손절 8.3 → 손절폭 ≈ 0.20%.
    engine = _engine(PositionSizingParams(risk_per_trade=0.01, leverage=100.0))  # 하한 기본 0.3%
    out = engine.on_entry(_intent(entry=8.316663, stop=8.3), now_ms=_DAY0)

    assert not out.accepted
    assert out.reason_code == REJECT_CODE_SIZING
    # 증상("수량 0")이 아니라 원인(손절폭 하한 미달)이 보인다.
    assert "0.20%" in out.reason  # 실제 손절폭
    assert "0.30%" in out.reason  # 하한
    assert "하한" in out.reason
    assert "WAN-79" in out.reason
    # 옛 catch-all 문구가 남지 않았다.
    assert out.reason != "사이징 수량 0 — 진입 스킵"


def test_reason_mapping_covers_each_guard() -> None:
    """네 갈래 사유가 각기 다른 구체 문구로 옮겨진다(집계는 여전히 `REJECT_CODE_SIZING`).

    엔진 비북 경로는 `open_notional=0`·`adv_usd=None`이라 명목 소진·용량 상한을
    end-to-end로 재현하기 어렵다(그 경로는 각각 북·ADV 옵트인 소관). 사유→문구 매핑
    함수를 직접 고정해 세 나머지 갈래의 라벨을 회귀로 남긴다. stop_too_tight는 위
    end-to-end 테스트가 담당한다."""
    from execution.engine import _sizing_reject_reason

    params = PositionSizingParams()
    notional = _sizing_reject_reason(
        "notional_exhausted", entry_price=100.0, stop_price=90.0, params=params
    )
    assert "명목" in notional and "WAN-103" in notional

    capacity = _sizing_reject_reason(
        "capacity_cap", entry_price=100.0, stop_price=90.0, params=params
    )
    assert "용량 상한" in capacity and "WAN-244" in capacity

    no_equity = _sizing_reject_reason(
        "no_equity", entry_price=100.0, stop_price=90.0, params=params
    )
    assert "자본" in no_equity

    below_min = _sizing_reject_reason(
        "below_min_qty", entry_price=100.0, stop_price=90.0, params=params
    )
    assert "최소 주문 수량" in below_min

    # 어느 갈래도 옛 catch-all 문구가 아니다.
    for reason in (notional, capacity, no_equity, below_min):
        assert reason != "사이징 수량 0 — 진입 스킵"
