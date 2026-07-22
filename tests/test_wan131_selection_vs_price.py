"""WAN-131 테스트 — 볼린저 「선별 대 가격」 분리.

두 겹으로 지킨다:

1. **엔진(`DeviationFilterParams.select_only`)이 라벨이 아니라 동작이다** — 기본값(`False`)은
   비트 단위로 예전과 같고, `True`는 규칙 3(기각·선별)은 그대로 두되 규칙 2 진입가를 밴드가에서
   **존 근단**으로 되돌린다. 조용한 실패(WAN-91/95/112/123)를 막는다.
2. **모듈이 오늘 엔진을 돈다** — 세 앵커(A·C·Cadopt)의 설정이 wan151 사다리(`L1`·`L2`·`L2u`)와
   같고, 검산·판정·선별 identity가 동작한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest.wan131_selection_vs_price import (
    ARMS_BY_NAME,
    FLOAT_NOISE,
    ArmRow,
    arm_params,
    arm_table,
    build_summary_markdown,
    contamination_lines,
    crosscheck_against_ablation,
    decomposition,
    describe_engine,
    per_symbol,
    rows_from_csv,
    rows_to_frame,
    selection_identity,
    verdict_lines,
)
from backtest.zone_limit_backtest import _IntrabarLiveLimit
from strategy.models import (
    ConfluenceParams,
    DeviationFilterParams,
    OrderBlock,
    OrderBlockDirection,
)
from strategy.realtime_band import RealtimeBand

# --------------------------------------------------------------------------- #
# 1. 엔진 — select_only는 동작이다
# --------------------------------------------------------------------------- #


def test_select_only_defaults_to_false_and_preserves_equality() -> None:
    """기본값은 `False`라 채택 기본값이 흔들리지 않는다(비트 단위 보존)."""
    assert DeviationFilterParams(width_value=2.0).select_only is False
    adopted = ConfluenceParams().deviation_filter
    assert adopted is not None
    assert adopted.select_only is False
    assert ConfluenceParams() == ConfluenceParams()


def _bullish_ob() -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=100.0,
        bottom=90.0,
        start_time=0,
        confirmed_time=0,
        ob_volume=1.0,
        ob_low_volume=1.0,
        ob_high_volume=1.0,
    )


def _provider(*, select_only: bool) -> _IntrabarLiveLimit:
    """밴드가 존 안(90~100)에 오는 결정적 공급자 — 종가·현재가 모두 95면 σ=0 → band=95."""
    filt = DeviationFilterParams(
        anchor="sma",
        sma_length=20,
        width_kind="stdev",
        width_value=2.0,
        select_only=select_only,
    )
    params = ConfluenceParams(
        entry_mode="zone_limit",
        rsi_mode="realtime",
        zone_limit_offset_bps=2.0,
        deviation_filter=filt,
    )
    return _IntrabarLiveLimit(
        band=RealtimeBand.seed_from_closed([95.0] * 19, filt),
        order_block=_bullish_ob(),
        is_long=True,
        params=params,
        stop_price=90.0,
        lines=[],
    )


def test_select_only_true_uses_zone_proximal_not_band_price() -> None:
    """🚨 핵심 동작 — 규칙 2에서 `True`는 밴드가(95)가 아니라 존 근단(100)에서 진입한다.

    같은 밴드(95, 존 안)를 두 값으로 돌린다: `False`는 밴드가 재산정(95)에 오프셋을,
    `True`는 존 근단(롱 = top = 100)에 오프셋을 얹는다. 라벨이 아니라 **가격이 갈린다**.
    """
    offset = 1.0 + 2.0 / 10_000.0
    full = _provider(select_only=False).limit_price(95.0)
    select = _provider(select_only=True).limit_price(95.0)
    assert full == pytest.approx(95.0 * offset, rel=1e-12)  # 재산정가(밴드)
    assert select == pytest.approx(100.0 * offset, rel=1e-12)  # 존 근단(재산정 안 함)
    assert select != pytest.approx(full)


def test_select_only_keeps_rule_3_rejection() -> None:
    """선별은 그대로다 — 밴드가 존 전체보다 불리하면 `True`도 주문을 걸지 않는다(규칙 3).

    오프셋이 이 기각을 되살리지 않는 것과 같은 계약(WAN-99). 존 아래(50)에 밴드가 서면
    두 값 모두 `None`이라, `select_only`는 **가격만** 바꾸고 선별은 안 바꾼다.
    """
    filt_full = DeviationFilterParams(
        anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0, select_only=False
    )
    filt_sel = filt_full.model_copy(update={"select_only": True})

    def provider(filt: DeviationFilterParams) -> _IntrabarLiveLimit:
        return _IntrabarLiveLimit(
            band=RealtimeBand.seed_from_closed([50.0] * 19, filt),
            order_block=_bullish_ob(),
            is_long=True,
            params=ConfluenceParams(
                entry_mode="zone_limit", rsi_mode="realtime", deviation_filter=filt
            ),
            stop_price=90.0,
            lines=[],
        )

    assert provider(filt_full).limit_price(50.0) is None
    assert provider(filt_sel).limit_price(50.0) is None


# --------------------------------------------------------------------------- #
# 2. 모듈 — 팔 정의가 오늘 엔진이다
# --------------------------------------------------------------------------- #


def test_arms_all_pin_filter_off_and_follow_adopted_band_and_zone() -> None:
    """세 팔 모두 존폭 필터 끔(사용자 결정) · 밴드는 채택 기본값(`intrabar_live`)을 따라간다."""
    for name in ("A", "B", "C", "Cadopt"):
        p = arm_params(ARMS_BY_NAME[name])
        assert p.max_zone_width_atr is None  # 사용자 결정: 필터 끔 명시
        dev = p.deviation_filter
        if dev is not None:
            assert dev.band_bar == "intrabar_live"  # 채택 밴드를 고정하지 않는다
    assert "max_zone_width_atr=None" in describe_engine()
    assert "combine_obs=False" in describe_engine()


def test_arm_configs_match_the_wan151_anchors() -> None:
    """A(볼린저 off) · B(선별만) · C(선별+가격) · Cadopt(채택 기본값·필터 끔)."""
    a = arm_params(ARMS_BY_NAME["A"])
    assert a.deviation_filter is None
    assert a.rsi_gate_mode == "first_tap_free"
    assert a.retap_mode == "every_tap"

    b = arm_params(ARMS_BY_NAME["B"])
    c = arm_params(ARMS_BY_NAME["C"])
    assert b.deviation_filter is not None and b.deviation_filter.select_only is True
    assert c.deviation_filter is not None and c.deviation_filter.select_only is False
    assert b.rsi_gate_mode == "first_tap_free" == c.rsi_gate_mode
    # B와 C는 select_only 하나만 다르다 — 그게 「가격」을 격리하는 근거다.
    assert b.deviation_filter.model_copy(update={"select_only": False}) == c.deviation_filter

    cadopt = arm_params(ARMS_BY_NAME["Cadopt"])
    assert cadopt == ConfluenceParams(max_zone_width_atr=None)
    assert cadopt.rsi_gate_mode == "unconditional"


def test_arm_table_marks_anchors() -> None:
    table = arm_table()
    assert "L1" in table and "L2" in table and "L2u" in table
    assert "(신규)" in table  # B팔은 wan151 앵커가 없다


# --------------------------------------------------------------------------- #
# 집계 · 판정용 픽스처
# --------------------------------------------------------------------------- #


def _row(
    *,
    arm: str,
    symbol: str = "BTC/USDT:USDT",
    segment: str = "oos",
    timeframe: str = "1h",
    total_return: float = 0.1,
    num_trades: int = 100,
    eligible_setups: int = 200,
) -> ArmRow:
    return ArmRow(
        arm=arm,
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        window=0,
        entry_mode="zone_limit",
        take_profit_r=1.5,
        offset_bps=2.0,
        retap_mode="every_tap",
        position_mode="single",
        portfolio_leverage=None,
        combine_obs=False,
        fill="baseline",
        seed=0,
        start_time=0,
        end_time=1,
        num_bars=100,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.1,
        sharpe=1.0,
        profit_factor=1.2,
        mean_r=0.2,
        fill_rate=0.8,
        eligible_setups=eligible_setups,
        num_filled=int(num_trades * 1.2),
        funding_coverage=1.0,
    )


#: (심볼, 구간) → 팔 → (total_return, num_trades). eligible은 A만 크고 B·C·Cadopt는 같다
#: (선별 동일). 값은 CSV 왕복이 정확한 리터럴(부동소수 합 금지).
_GRID: dict[tuple[str, str], dict[str, tuple[float, int]]] = {
    ("BTC/USDT:USDT", "is"): {
        "A": (-0.06, 198),
        "B": (-0.064, 180),
        "C": (-0.04, 141),
        "Cadopt": (0.03, 164),
    },
    ("BTC/USDT:USDT", "oos"): {
        "A": (0.02, 117),
        "B": (0.03, 109),
        "C": (0.005, 78),
        "Cadopt": (0.057, 91),
    },
    ("ETH/USDT:USDT", "is"): {
        "A": (0.1, 180),
        "B": (0.12, 165),
        "C": (0.14, 130),
        "Cadopt": (0.2, 150),
    },
    ("ETH/USDT:USDT", "oos"): {
        "A": (0.1, 110),
        "B": (0.14, 100),
        "C": (0.2, 70),
        "Cadopt": (0.34, 85),
    },
}


def _grid_rows() -> list[ArmRow]:
    """두 심볼 × 네 팔 × 두 구간 — 앵커(A·C·Cadopt)와 선별 identity(B==C)가 성립하는 픽스처."""
    rows: list[ArmRow] = []
    for (symbol, segment), arms in _GRID.items():
        for arm, (ret, trades) in arms.items():
            elig = 300 if arm == "A" else 280  # A만 크고 B·C·Cadopt는 같다(선별 동일).
            rows.append(
                _row(
                    arm=arm,
                    symbol=symbol,
                    segment=segment,
                    total_return=ret,
                    num_trades=trades,
                    eligible_setups=elig,
                )
            )
    return rows


def test_decomposition_pairs_symbols_and_orders_effects() -> None:
    steps = decomposition(per_symbol(_grid_rows()))
    oos = steps[steps["segment"] == "oos"]
    assert list(oos["effect"]) == [
        "선별 (B−A)",
        "가격 (C−B)",
        "볼린저 총합 (C−A)",
        "게이트 제거 (Cadopt−C)",
    ]
    sel = oos[oos["effect"] == "선별 (B−A)"].iloc[0]
    # BTC oos: 0.03-0.02=+0.01 · ETH oos: 0.14-0.10=+0.04 → 평균 +0.025
    assert sel["delta_return"] == pytest.approx(0.025)
    assert sel["symbols_up"] == 2.0


def test_verdict_lines_produce_a_tag() -> None:
    lines = verdict_lines(decomposition(per_symbol(_grid_rows())))
    text = "\n".join(lines)
    assert "선별(B−A)" in text
    assert "종합 판정" in text


def test_selection_identity_flags_when_b_and_c_diverge() -> None:
    """B·C의 eligible이 같으면 「순수 가격」, 다르면 조용히 넘기지 않는다."""
    clean, worst = selection_identity(per_symbol(_grid_rows()))
    assert worst == 0.0
    assert "모든 셀 차이 0" in clean

    bad = _grid_rows()
    # C 팔 하나의 eligible을 흔든다 → 선별이 갈린다.
    bad = [r for r in bad if not (r.arm == "C" and r.symbol == "BTC/USDT:USDT")]
    bad.append(_row(arm="C", symbol="BTC/USDT:USDT", total_return=0.005, eligible_setups=200))
    note, worst2 = selection_identity(per_symbol(bad))
    assert worst2 is not None and worst2 > 0
    assert "선별이 갈린다" in note


def test_contamination_lines_report_fill_rate_component() -> None:
    frame = per_symbol(_grid_rows())
    lines = contamination_lines(decomposition(frame), frame)
    text = "\n".join(lines)
    assert "체결률 성분" in text
    assert "선별(A→B)은 깨끗하다" in text


# --------------------------------------------------------------------------- #
# 검산 — A·C·Cadopt ≡ wan151 사다리
# --------------------------------------------------------------------------- #


_ANCHOR_LEVEL = {"A": "L1", "C": "L2", "Cadopt": "L2u"}


def _write_ablation(tmp_path: Path, *, bump: float = 0.0) -> Path:
    """`_GRID`의 A·C·Cadopt 행을 wan151 사다리(L1/L2/L2u)로 옮겨 적는다 — 검산이 참이 되게.

    `bump`를 주면 첫 L1 행의 수익만 흔들어 「어긋남」을 만든다(부동소수·불일치 테스트).
    """
    import pandas as pd

    records = []
    bumped = False
    for (symbol, segment), arms in _GRID.items():
        for arm, level in _ANCHOR_LEVEL.items():
            ret, trades = arms[arm]
            if bump and level == "L1" and not bumped:
                ret += bump
                bumped = True
            records.append(
                {
                    "symbol": symbol,
                    "timeframe": "1h",
                    "segment": segment,
                    "level": level,
                    "total_return": ret,
                    "num_trades": trades,
                }
            )
    path = tmp_path / "abl.csv"
    pd.DataFrame(records).to_csv(path, index=False)
    return path


def test_crosscheck_passes_when_anchors_match(tmp_path: Path) -> None:
    note, worst = crosscheck_against_ablation(_grid_rows(), _write_ablation(tmp_path))
    assert worst == 0.0
    assert "차이 0" in note


def test_crosscheck_reports_mismatch(tmp_path: Path) -> None:
    note, worst = crosscheck_against_ablation(_grid_rows(), _write_ablation(tmp_path, bump=0.07))
    assert worst is not None and worst > 0
    assert "어긋난다" in note


def test_crosscheck_names_float_noise(tmp_path: Path) -> None:
    note, worst = crosscheck_against_ablation(_grid_rows(), _write_ablation(tmp_path, bump=1e-16))
    assert worst is not None and 0 < worst <= FLOAT_NOISE
    assert "부동소수 오차 이내" in note
    assert "어긋난다" not in note


def test_crosscheck_says_so_when_missing(tmp_path: Path) -> None:
    note, worst = crosscheck_against_ablation(_grid_rows(), tmp_path / "missing.csv")
    assert worst is None
    assert "검산 불가" in note


# --------------------------------------------------------------------------- #
# 왕복 · 렌더
# --------------------------------------------------------------------------- #


def test_rows_round_trip_through_csv(tmp_path: Path) -> None:
    rows = _grid_rows()
    path = tmp_path / "grid.csv"
    rows_to_frame(rows).to_csv(path, index=False)
    assert rows_from_csv(path) == rows


def test_summary_carries_the_key_markers(tmp_path: Path) -> None:
    summary = build_summary_markdown(
        _grid_rows(),
        csv_path=Path("x.csv"),
        ablation_csv=_write_ablation(tmp_path),
    )
    assert "# WAN-131" in summary
    assert "필터 미적용 격자" in summary
    assert "eligible_setups" in summary
    assert "인용 금지 경고" in summary
    assert "차이 0" in summary
