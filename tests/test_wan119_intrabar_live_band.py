"""WAN-119 봉내 라이브 밴드 3자 비교의 사다리 정의·집계·판정 테스트 (합성 행으로).

이 리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 네 단이 오직 `band_bar`만
다른지(다른 게 같이 움직이면 "봉내 움직임의 몫"이라는 말이 거짓이 된다), `L1`·`L2`·`L2p`가
정말 WAN-115의 같은 단인지(아니면 "그 CSV와 검산된다"가 거짓말이다), 그리고 판정 문장이
표의 숫자에서 실제로 계산되는지 — 특히 **부호 함정 가드**(WAN-115가 1h에서 걸린 그것).
"""

from __future__ import annotations

import pytest

from backtest.harness import LEGACY_RSI_GATE_MODE, fill_preset
from backtest.wan115_bollinger_lookahead_recheck import RUNGS_BY_NAME as WAN115_RUNGS
from backtest.wan115_bollinger_lookahead_recheck import rung_params as wan115_rung_params
from backtest.wan119_intrabar_live_band import (
    BASE_RUNG,
    DEFAULT_END,
    DEFAULT_START,
    LADDER,
    LIVE_RUNG,
    NO_GAP_LABEL,
    PREV_RUNG,
    RUNGS_BY_NAME,
    TAP_RUNG,
    LiveBandRow,
    _verdict_label,
    adopted_ladder,
    incremental,
    per_symbol,
    rung_params,
    rung_summary,
    segments,
    selection_vs_price,
    verdict,
)
from strategy.models import ConfluenceParams

_BASELINE = fill_preset("baseline")


def _row(
    *,
    level: str,
    symbol: str = "BTC/USDT:USDT",
    total_return: float = 0.1,
    timeframe: str = "15m",
    segment: str = "oos",
    fill: str = "baseline",
    seed: int = 0,
    num_trades: int = 100,
    max_drawdown: float = 0.1,
) -> LiveBandRow:
    return LiveBandRow(
        level=level,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        fill=fill,
        seed=seed,
        start_time=0,
        end_time=1000,
        num_bars=100,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=max_drawdown,
        sharpe=1.0,
        profit_factor=1.2,
        mean_r=0.2,
        fill_rate=0.5,
        eligible_setups=200,
        num_filled=num_trades,
        funding_coverage=1.0,
    )


# --------------------------------------------------------------------------- #
# 사다리 정의 — 이 리포트의 주장이 참인지
# --------------------------------------------------------------------------- #


def test_ladder_is_the_four_rungs_in_order() -> None:
    assert LADDER == ("L1", "L2", "L2p", "L2i")
    assert (BASE_RUNG, TAP_RUNG, PREV_RUNG, LIVE_RUNG) == ("L1", "L2", "L2p", "L2i")


def test_tap_rung_is_exactly_the_adopted_default() -> None:
    """`L2`가 **WAN-122까지의** 채택 기본값이라는 표의 라벨이 참이어야 한다.

    ⚠️ WAN-123(RSI 게이트 제거)이 이 동일성을 **한 필드만큼** 깼다: 사다리는
    `rsi_gate_mode="first_tap_free"`를 고정하는데 현재 기본값은 `unconditional`이다.
    그 고정은 의도적이다 — 풀어 두면 이 표가 WAN-114/115와 비트 단위로 맞물리지 않게
    되고, `L1→L2` 증분이 "볼린저의 기여"가 아니라 "볼린저 + 게이트 제거"가 된다.
    나머지 필드는 여전히 기본값을 물려받아야 하므로 그것을 확인한다.
    """
    adopted = ConfluenceParams()
    tap = rung_params(RUNGS_BY_NAME[TAP_RUNG], fill=_BASELINE)
    assert tap.deviation_filter == adopted.deviation_filter
    assert tap.retap_mode == adopted.retap_mode
    # WAN-123: 고정한 게이트 ≠ 현재 기본값. 이 부등호가 깨지면 사다리가 조용히
    # 새 엔진으로 옮겨 갔다는 뜻이다.
    assert tap.rsi_gate_mode == LEGACY_RSI_GATE_MODE != adopted.rsi_gate_mode
    # 토대: 오프셋 2bp(WAN-112) · 고정 1.5R(WAN-81)를 기본값에서 물려받는다.
    assert tap.zone_limit_offset_bps == adopted.zone_limit_offset_bps == pytest.approx(2.0)
    assert tap.take_profit_r == adopted.take_profit_r == pytest.approx(1.5)


@pytest.mark.parametrize(
    ("level", "band_bar"),
    [(TAP_RUNG, "tap"), (PREV_RUNG, "prev_closed"), (LIVE_RUNG, "intrabar_live")],
)
def test_each_bollinger_rung_carries_its_band_bar(level: str, band_bar: str) -> None:
    filt = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE).deviation_filter
    assert filt is not None
    assert filt.band_bar == band_bar


@pytest.mark.parametrize("level", [PREV_RUNG, LIVE_RUNG])
def test_rungs_differ_from_the_adopted_default_only_by_band_bar(level: str) -> None:
    """`L2` → `L2p`/`L2i`가 **오직 `band_bar`만** 다르다.

    이게 깨지면 `L2p`/`L2i`의 차이를 "밴드 정의의 몫"이라고 부를 수 없다 — 두 부품이 같이
    움직인 값이 된다.
    """
    tap = rung_params(RUNGS_BY_NAME[TAP_RUNG], fill=_BASELINE)
    other = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE)
    assert tap.deviation_filter is not None
    assert other.deviation_filter is not None
    reverted = other.model_copy(
        update={"deviation_filter": other.deviation_filter.model_copy(update={"band_bar": "tap"})}
    )
    assert reverted == tap


def test_base_rung_has_bollinger_off() -> None:
    """`L1`은 볼린저가 꺼진 바닥 — 증분의 기준선이다."""
    assert rung_params(RUNGS_BY_NAME[BASE_RUNG], fill=_BASELINE).deviation_filter is None


@pytest.mark.parametrize("level", ["L1", "L2", "L2p"])
def test_shared_rungs_match_wan115_bit_for_bit(level: str) -> None:
    """`L1`·`L2`·`L2p`가 WAN-115의 같은 단과 **완전히 같은 파라미터**다.

    이 동치가 이 리포트의 검산 근거다("`wan115_bollinger_recheck.csv`의 같은 단과 일치").
    깨지면 세 단을 비교할 수 없고, 이슈가 인용한 −4.88%p도 이 표와 무관해진다.
    """
    mine = rung_params(RUNGS_BY_NAME[level], fill=_BASELINE)
    theirs = wan115_rung_params(WAN115_RUNGS[level], fill=_BASELINE)
    assert mine == theirs


def test_window_is_pinned_like_wan115() -> None:
    """창이 미끄러지면(`--years N`) 두 리포트가 다른 기간을 봐 검산이 깨진다."""
    from backtest.wan115_bollinger_lookahead_recheck import DEFAULT_END as W115_END
    from backtest.wan115_bollinger_lookahead_recheck import DEFAULT_START as W115_START

    assert (DEFAULT_START, DEFAULT_END) == (W115_START, W115_END)


def test_segments_split_is_two_thirds() -> None:
    is_seg, oos_seg = segments()
    assert is_seg.start_fraction == 0.0
    assert is_seg.end_fraction == pytest.approx(2 / 3)
    assert oos_seg.start_fraction == pytest.approx(2 / 3)
    assert oos_seg.end_fraction == 1.0


# --------------------------------------------------------------------------- #
# 판정 — 숫자에서 실제로 계산되는지 + 부호 함정 가드
# --------------------------------------------------------------------------- #


def test_verdict_label_calls_a_revived_plus_regardless_of_recovery_share() -> None:
    """이슈가 물은 건 "15m **플러스**가 살아나나"다 — 1차 기준은 절대 부호다."""
    assert _verdict_label(0.01, gap=0.05, live_return=0.02) == "(a) 되살아남"


def test_verdict_label_partial_recovery_stays_negative() -> None:
    assert _verdict_label(0.03, gap=0.05, live_return=-0.01) == "(b) 부분 회복 (여전히 ≤0)"


def test_verdict_label_no_recovery() -> None:
    assert _verdict_label(0.001, gap=0.05, live_return=-0.01) == "(c) 여전히 소멸"


@pytest.mark.parametrize("gap", [0.0, -0.05])
def test_verdict_label_refuses_to_judge_when_lookahead_share_is_not_positive(gap: float) -> None:
    """부호 함정 가드 — 룩어헤드가 오히려 값을 깎던 셀(1h)에서 비율은 뜻을 잃는다.

    WAN-115가 1h에서 걸린 그것: 기준이 음수면 "더 깎였는데 172% 유지"처럼 읽힌다. 그런
    셀은 회복 비율이 아니라 **증분의 부호**만 본다.
    """
    assert _verdict_label(0.02, gap=gap, live_return=-0.01) == NO_GAP_LABEL


def _grid(returns: dict[str, float]) -> list[LiveBandRow]:
    """단별 수익률만 다른 최소 격자(심볼 2개 × 4단)."""
    return [
        _row(level=level, symbol=symbol, total_return=value)
        for level, value in returns.items()
        for symbol in ("BTC/USDT:USDT", "ETH/USDT:USDT")
    ]


def test_verdict_reads_the_three_increments_from_the_table() -> None:
    """판정 문장의 숫자가 표에서 나온다 — 손으로 적으면 재실행 때 갈라진다(WAN-95의 사고)."""
    rows = _grid({"L1": -0.10, "L2": 0.06, "L2p": 0.01, "L2i": 0.04})
    frame = per_symbol(rows)
    lines = verdict(incremental(frame), rung_summary(frame), segment="oos")
    assert len(lines) == 1
    line = lines[0]
    # L1→L2 = +16.00%p · L1→L2p = +11.00%p · L1→L2i = +14.00%p
    assert "+16.00%p" in line and "+11.00%p" in line and "+14.00%p" in line
    # 룩어헤드의 몫 5.00%p 중 3.00%p 회복 = 60.0%
    assert "60.0% 회복" in line
    # L2i 절대 수익률이 플러스(+4%) → (a)
    assert "(a) 되살아남" in line


def test_verdict_reports_still_gone_when_live_stays_negative() -> None:
    rows = _grid({"L1": -0.10, "L2": 0.06, "L2p": -0.02, "L2i": -0.018})
    frame = per_symbol(rows)
    line = verdict(incremental(frame), rung_summary(frame), segment="oos")[0]
    assert "(c) 여전히 소멸" in line
    assert "-1.80%" in line  # 채택 기본값 절대 수익률을 함께 적는다


def test_adopted_ladder_reports_all_three_band_definitions() -> None:
    """성적표는 세 밴드 정의를 나란히 — 재-베이스라인 판단이 보는 숫자다."""
    rows = _grid({"L1": -0.10, "L2": 0.06, "L2p": 0.01, "L2i": 0.04})
    line = adopted_ladder(rung_summary(per_symbol(rows)), segment="oos")[0]
    assert "`L2` **+6.00%**(2/2)" in line
    assert "`L2p` **+1.00%**(2/2)" in line
    assert "`L2i` **+4.00%**(2/2)" in line
    assert "-2.00%p" in line  # 채택 기본값 대비


def test_incremental_pairs_deltas_per_symbol() -> None:
    """델타는 심볼별로 짝지어 뺀 뒤 평균한다 — 그래야 `symbols_up`을 셀 수 있다."""
    rows = [
        _row(level="L1", symbol="BTC/USDT:USDT", total_return=0.0),
        _row(level="L1", symbol="ETH/USDT:USDT", total_return=0.0),
        _row(level="L2i", symbol="BTC/USDT:USDT", total_return=0.10),
        _row(level="L2i", symbol="ETH/USDT:USDT", total_return=-0.02),
    ]
    steps = incremental(per_symbol(rows)).set_index("step")
    assert steps.loc["L1→L2i", "delta_return"] == pytest.approx(0.04)
    assert steps.loc["L1→L2i", "symbols_up"] == 1.0  # BTC만 올랐다
    assert steps.loc["L1→L2i", "symbols"] == 2.0


def test_selection_vs_price_measures_trades_relative_to_the_adopted_default() -> None:
    """거래 수 변화의 분모가 `L2`(채택 기본값)여야 한다 — `L1`이 아니다.

    `incremental`의 `L1→X` 증분을 빼서 만들면 분모가 `L1`이 된다. `L1`은 볼린저가 꺼져
    체결률 100%라 거래가 가장 많으므로(WAN-114), 같은 이름의 **다른 수**가 조용히 나온다.
    """
    rows = [
        # L2 = 100건 · L2i = 110건 → L2 대비 +10.0% (L1 대비로 재면 +5.0%로 절반이 된다)
        _row(level="L1", symbol="BTC/USDT:USDT", total_return=0.0, num_trades=200),
        _row(level="L2", symbol="BTC/USDT:USDT", total_return=0.10, num_trades=100),
        _row(level="L2i", symbol="BTC/USDT:USDT", total_return=0.06, num_trades=110),
    ]
    line = selection_vs_price(per_symbol(rows), segment="oos")[0]
    assert "+10.0%" in line
    assert "-4.00%p" in line  # 수익은 L2 대비로 뺀다
    assert "선별+가격" in line  # 10%는 문턱(<10%) 밖이다
