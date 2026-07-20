"""WAN-133 「기하 대 선별」 테스트 — 손절 오버라이드가 진입을 안 바꾸고, ATR 배선이 맞다.

핵심 회귀를 **동작으로** 고정한다:

* **`stop_loss_override=None`은 인자 미지정과 비트 단위로 같다**(기본값 불변 — WAN-88/95/111/117
  CSV 재현의 근거).
* **손절 오버라이드는 청산·1R만 바꾸고 진입(체결) 집합은 그대로다** — 체결은 지정가 터치·RSI
  게이트로만 정해지고 손절선을 안 보므로, 진입 시각·진입가·체결 통계가 기본과 같아야 한다.
  이게 깨지면 "손절이 진입을 바꾼" 배선 버그다(존폭↔뚫림 상관을 같은 셋업 위에서 재는 전제).
* **ATR 배선** — 오버라이드가 진입가 ∓ k·ATR(pos−1)을 정확히 내고, ATR을 못 찾으면 그 셋업을
  제외한다. `atr_by_tap_time`이 직전 확정봉(pos−1) ATR을 탭 봉 시각에 건다.
* **라이브 밴드 가드** — 봉내 밴드는 1R을 체결 순간에 내므로 손절 오버라이드를 거부한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.models import BacktestConfig
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan133_geometry_vs_selection import (
    ARM_DEFAULT,
    ARM_FILTER,
    STOP_BUCKETS,
    STOP_GUARD_FRACTION,
    PnlRow,
    _bucket_of,
    atr_by_tap_time,
    degeneracy_note,
    make_atr_stop_override,
    pnl_symbol_mean,
)
from backtest.zone_limit_backtest import (
    StopLossContext,
    build_zone_limit_candidates,
)
from strategy.indicators import atr
from strategy.models import ConfluenceParams, DeviationFilterParams


def _synthetic_pair(bars: int = 600, span: int = 120) -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe="1h", bars=bars, seed=7)
    htf_ms = timeframe_to_ms("1h")
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _engine_params() -> ConfluenceParams:
    # wan137 테스트와 같은 관행 — 이 시드는 숏 셋업만 내고, 볼린저는 작은 데이터셋에서 후보를
    # 모두 걸러낼 수 있어 꺼 둔다. 엔진 훅(손절 오버라이드) 배선만 본다.
    return ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )


def _cfg() -> BacktestConfig:
    return BacktestConfig()


# --------------------------------------------------------------------------- #
# 엔진 훅 — 손절 오버라이드는 진입을 바꾸지 않는다
# --------------------------------------------------------------------------- #


def test_stop_override_none_reproduces_default_bitwise() -> None:
    """`stop_loss_override=None`은 인자를 아예 안 준 것과 비트 단위로 같다(기본값 불변)."""
    htf, one_min = _synthetic_pair()
    params = _engine_params()
    base, base_stats = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=_cfg())
    explicit, exp_stats = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=_cfg(), stop_loss_override=None
    )
    assert [(c.entry_time, c.exit_time, c.exit_price, c.stop_price, c.reason) for c in base] == [
        (c.entry_time, c.exit_time, c.exit_price, c.stop_price, c.reason) for c in explicit
    ]
    assert (base_stats.eligible, base_stats.filled) == (exp_stats.eligible, exp_stats.filled)


def test_stop_override_keeps_entries_identical_changes_stop() -> None:
    """손절 오버라이드는 진입(체결) 집합을 그대로 두고 손절가만 바꾼다.

    진입 시각·진입가·체결 통계(eligible/filled)가 기본과 같아야 한다 — 손절은 진입을 안
    건드린다는 것이 「같은 셋업 위에서 존폭↔뚫림을 다시 잰다」는 이 이슈의 전제다. 손절가는
    실제로 달라져야 한다(훅이 죽은 코드가 아님).
    """
    htf, one_min = _synthetic_pair()
    params = _engine_params()
    base, base_stats = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=_cfg())

    calls: list[StopLossContext] = []

    def spy_stop(ctx: StopLossContext) -> float | None:
        calls.append(ctx)
        # 숏이면 손절은 진입가 위. 기본(존 상단)보다 확실히 다른 값으로 민다.
        return ctx.entry_price * (1.05 if not ctx.is_long else 0.95)

    overridden, over_stats = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=_cfg(), stop_loss_override=spy_stop
    )
    assert calls  # 훅이 셋업 루프에서 실제로 불린다.
    # 진입 집합·체결 통계 불변.
    assert [(c.entry_time, c.entry_price) for c in base] == [
        (c.entry_time, c.entry_price) for c in overridden
    ]
    assert (base_stats.eligible, base_stats.filled) == (over_stats.eligible, over_stats.filled)
    # 손절가는 실제로 달라졌다(적어도 한 후보에서).
    assert any(b.stop_price != o.stop_price for b, o in zip(base, overridden, strict=True))


def test_stop_override_none_return_excludes_setup() -> None:
    """오버라이드가 None을 돌려주면 그 셋업이 후보에서 빠진다(유효 장벽 불가)."""
    htf, one_min = _synthetic_pair()
    params = _engine_params()
    base, _ = build_zone_limit_candidates(htf, one_min, "1h", params=params, cfg=_cfg())
    assert base  # 기본에는 후보가 있다.

    def none_stop(ctx: StopLossContext) -> float | None:
        return None

    excluded, stats = build_zone_limit_candidates(
        htf, one_min, "1h", params=params, cfg=_cfg(), stop_loss_override=none_stop
    )
    assert excluded == []
    assert stats.filled == 0


def test_stop_override_rejected_on_live_band() -> None:
    """봉내 라이브 밴드는 1R을 체결 순간에 내므로 손절 오버라이드를 거부한다."""
    htf, one_min = _synthetic_pair()
    params = ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        short_enabled=True,
        deviation_filter=DeviationFilterParams(
            width_kind="stdev", width_value=2.0, anchor="sma", band_bar="intrabar_live"
        ),
    )

    def any_stop(ctx: StopLossContext) -> float | None:
        return ctx.entry_price

    with pytest.raises(ValueError, match="stop_loss_override"):
        build_zone_limit_candidates(
            htf, one_min, "1h", params=params, cfg=_cfg(), stop_loss_override=any_stop
        )


# --------------------------------------------------------------------------- #
# ATR 배선
# --------------------------------------------------------------------------- #


def test_make_atr_stop_override_computes_fixed_distance() -> None:
    """손절 = 진입가 ∓ k·ATR (롱=아래, 숏=위). ATR 없으면 None."""
    atr_map = {1_000: 4.0}
    override = make_atr_stop_override(atr_map, k=1.5)

    long_ctx = StopLossContext(
        is_long=True,
        entry_price=100.0,
        default_stop=90.0,
        trigger_time=1_000,
        order_block=None,  # type: ignore[arg-type]
    )
    assert override(long_ctx) == pytest.approx(100.0 - 1.5 * 4.0)

    short_ctx = StopLossContext(
        is_long=False,
        entry_price=100.0,
        default_stop=110.0,
        trigger_time=1_000,
        order_block=None,  # type: ignore[arg-type]
    )
    assert override(short_ctx) == pytest.approx(100.0 + 1.5 * 4.0)

    missing = StopLossContext(
        is_long=True,
        entry_price=100.0,
        default_stop=90.0,
        trigger_time=999,
        order_block=None,  # type: ignore[arg-type]
    )
    assert override(missing) is None


def test_atr_by_tap_time_uses_prev_confirmed_bar() -> None:
    """탭 봉 open_time → 직전 확정봉(pos−1) ATR14. pos=0은 매핑 없음(직전이 없다)."""
    htf = make_synthetic_ohlcv(timeframe="1h", bars=100, seed=3)
    frame = htf.sort_values("open_time").reset_index(drop=True)
    atr14 = [float(v) for v in atr(frame, length=14).tolist()]
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]

    mapping = atr_by_tap_time(frame)
    assert times[0] not in mapping  # 직전 봉이 없어 매핑되지 않는다.
    # 유효한 pos에서 mapping[times[pos]] == atr14[pos-1].
    for pos in range(1, len(times)):
        prev = atr14[pos - 1]
        if prev == prev and prev > 0:
            assert mapping[times[pos]] == pytest.approx(prev)


# --------------------------------------------------------------------------- #
# 널 퇴화 검사 (WAN-124 가드 취지)
# --------------------------------------------------------------------------- #


def _pnl_row(arm: str, num_candidates: int) -> PnlRow:
    return PnlRow(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        segment="oos",
        arm=arm,
        num_candidates=num_candidates,
        num_trades=num_candidates,
        total_return=0.0,
        max_drawdown=0.0,
        win_rate=0.5,
    )


def test_degeneracy_note_flags_non_splitting_filter() -> None:
    """필터가 표본을 거의 다 통과시키면(≥95%) 퇴화로 표시한다 — 두 팔 대조가 무의미하다."""
    rows = [_pnl_row(ARM_DEFAULT, 1000), _pnl_row(ARM_FILTER, 990)]  # 99% 통과.
    note = degeneracy_note(rows, timeframe="15m", segment="oos")
    assert "퇴화" in note and "선별 효과로 읽지 말 것" in note


def test_degeneracy_note_passes_real_split() -> None:
    """실제로 가르는 필터(하위 1/3 수준)는 퇴화가 아니다."""
    rows = [_pnl_row(ARM_DEFAULT, 1000), _pnl_row(ARM_FILTER, 320)]  # 32% 통과.
    note = degeneracy_note(rows, timeframe="15m", segment="oos")
    assert "정상(퇴화 아님)" in note


def test_degeneracy_note_handles_empty() -> None:
    """후보가 없으면 지어내지 않고 판정 불가로 둔다."""
    assert "판정 불가" in degeneracy_note([], timeframe="15m", segment="oos")


# --------------------------------------------------------------------------- #
# 20건 게이트 · 사이징 바닥 진단 (WAN-79 충돌)
# --------------------------------------------------------------------------- #


def _pnl_row_n(sym: str, arm: str, trades: int, ret: float) -> PnlRow:
    return PnlRow(
        symbol=sym,
        timeframe="15m",
        segment="oos",
        arm=arm,
        num_candidates=trades * 3,
        num_trades=trades,
        total_return=ret,
        max_drawdown=0.05,
        win_rate=0.5,
    )


def test_pnl_symbol_mean_excludes_thin_cells() -> None:
    """거래 20건 미만 셀은 심볼평균에서 빠진다 — 표본 붕괴 셀이 1/N을 차지하면 안 된다."""
    rows = [
        _pnl_row_n("A", ARM_FILTER, 100, 0.10),
        _pnl_row_n("B", ARM_FILTER, 100, 0.10),
        _pnl_row_n("C", ARM_FILTER, 13, 5.00),  # 13건짜리 대박 — 빠져야 한다.
    ]
    gated = pnl_symbol_mean(rows, timeframe="15m", segment="oos", arm=ARM_FILTER)
    assert gated["n_symbols"] == 2
    assert gated["n_excluded"] == 1
    assert gated["total_return"] == pytest.approx(0.10)
    # gated=False면 원값(13건 셀이 평균을 끌어올린다) — 대조용 경로가 살아 있어야 한다.
    raw = pnl_symbol_mean(rows, timeframe="15m", segment="oos", arm=ARM_FILTER, gated=False)
    assert raw["n_symbols"] == 3
    assert raw["total_return"] == pytest.approx((0.10 + 0.10 + 5.00) / 3)


def test_stop_guard_fraction_matches_engine_default() -> None:
    """진단 상수가 엔진 기본값과 갈라지면 표가 조용히 거짓말을 한다 — 동작으로 묶는다."""
    from execution.sizing import PositionSizingParams

    assert PositionSizingParams().min_stop_distance_fraction == STOP_GUARD_FRACTION


def test_stop_buckets_split_on_guard_boundary() -> None:
    """버킷 경계가 가드(0.3%)와 정확히 일치해야 「미만/이상」 집계가 뜻을 갖는다."""
    below = [hi for lo, hi, _ in STOP_BUCKETS if lo < STOP_GUARD_FRACTION]
    assert max(below) == pytest.approx(STOP_GUARD_FRACTION)
    assert _bucket_of(0.0029) == "0.2~0.3%"
    assert _bucket_of(0.0031) == "0.3~0.5%"
