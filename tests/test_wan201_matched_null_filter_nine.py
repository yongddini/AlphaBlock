"""backtest.wan201_matched_null_filter_nine 테스트 (WAN-201).

이 파일이 지키는 것:

1. **좌표가 이슈 결정 그대로다** — 9종목·못 박은 6년·작업 TF(15m·1h·4h), 옛 창 검산
   좌표는 판정 계열과 동일.
2. **필터 축이 라벨이 아니라 파라미터다** — 꺼짐 = `max_zone_width_atr=None` 명시,
   켜짐 = 채택 기본값 1.28. 켜짐 팔은 `arm.params()`와 항등, 풀은 볼린저만 끈다.
3. **기계를 wan151에서 가져온다** — 행 모델·시드·반복수·자·집계·판정이 전부 wan151.
4. **검산이 일치·잡음·불일치를 다르게 찍는다**(WAN-151/161 패턴).
5. **2×2 분해·4h 게이트가 행에서 계산된다**(주의문이 아니라 코드).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest import wan151_split_zone_null as wan151
from backtest.wan89_short_autopsy import ARMS_BY_NAME
from backtest.wan176_nine_symbol_rebaseline import (
    DEFAULT_END,
    DEFAULT_START,
    OLD_END,
    OLD_START,
)
from backtest.wan201_matched_null_filter_nine import (
    ADOPTED_OB_PARAMS,
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    FILTER_OFF,
    FILTER_ON,
    LONG_ARM,
    VERIFY_TIMEFRAMES,
    WORK_TIMEFRAMES,
    VerifyRow,
    _classify,
    _compare_null,
    _Task,
    axis_decomposition_lines,
    decomposition_table,
    describe_engine,
    four_h_gate_lines,
    pool_params,
    real_params,
    run_cell,
    verdict_all_tfs,
    verify_rows_from_csv,
)
from strategy.models import ConfluenceParams

# ------------------------------------------------------- 1. 좌표


def test_window_and_tfs_match_issue() -> None:
    assert DEFAULT_START == "2020-09-15"
    assert DEFAULT_END == "2026-07-22"
    assert WORK_TIMEFRAMES == ("15m", "1h", "4h")
    # 옛 창 검산 좌표는 판정 계열과 동일해야 옛 답의 재현이 배선 검산이 된다.
    assert (OLD_START, OLD_END) == (wan151.DEFAULT_START, wan151.DEFAULT_END)


def test_verify_tfs_are_cheap_1h_only() -> None:
    """검산은 1h 한정(비용) — 15m 재현은 WAN-176 자신의 검산이 덮는다."""
    assert VERIFY_TIMEFRAMES == ("1h",)


def test_engine_is_adopted_default() -> None:
    text = describe_engine()
    assert "combine_obs=False" in text
    assert "band_bar=intrabar_live" in text
    assert ADOPTED_OB_PARAMS.combine_obs is False


# ------------------------------------------------------- 2. 필터 축은 파라미터


def test_filter_values_read_from_adopted_default() -> None:
    assert FILTER_OFF is None
    assert FILTER_ON == 1.28
    assert ConfluenceParams().max_zone_width_atr == FILTER_ON


def test_real_params_filter_off_pins_none() -> None:
    arm = ARMS_BY_NAME[LONG_ARM]
    off = real_params(arm, max_zone_width_atr=FILTER_OFF)
    assert off.max_zone_width_atr is None
    # 밴드·게이트·오프셋은 손대지 않는다.
    assert off.deviation_filter is not None
    assert off.deviation_filter.band_bar == "intrabar_live"
    assert off.rsi_gate_mode == ConfluenceParams().rsi_gate_mode


def test_real_params_filter_on_is_identity_with_arm_params() -> None:
    """켜짐 팔은 채택 기본값에 같은 1.28을 다시 얹는 항등 — wan176 재현의 근거."""
    arm = ARMS_BY_NAME[LONG_ARM]
    on = real_params(arm, max_zone_width_atr=FILTER_ON)
    assert on == arm.params()
    assert on.max_zone_width_atr == 1.28


def test_pool_turns_off_bollinger_but_keeps_filter() -> None:
    arm = ARMS_BY_NAME[LONG_ARM]
    for filt in (FILTER_OFF, FILTER_ON):
        pool = pool_params(arm, max_zone_width_atr=filt)
        assert pool.deviation_filter is None  # 무력화 축 = 볼린저
        assert pool.max_zone_width_atr == filt  # 필터는 실제 팔과 같은 값


def test_reuses_wan151_row_and_ruler() -> None:
    assert BOOTSTRAP_ITERATIONS == wan151.BOOTSTRAP_ITERATIONS == 200
    assert BOOTSTRAP_SEED == wan151.BOOTSTRAP_SEED == 124
    assert LONG_ARM == wan151.LONG_ARM == "long_only"


def test_task_carries_filter_axis() -> None:
    task = _Task(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        start_ms=0,
        end_ms=1,
        max_zone_width_atr=FILTER_OFF,
        iterations=200,
    )
    assert task.max_zone_width_atr is None


def test_run_cell_empty_market_returns_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 시장이면 조용히 빈 리스트 — 엔진을 돌리지 않고도 배선 경로를 밟는다."""

    class _Empty:
        empty = True
        df_1m = pd.DataFrame()

    monkeypatch.setattr(
        "backtest.wan201_matched_null_filter_nine.harness.load_market_data",
        lambda *a, **k: _Empty(),
    )
    task = _Task(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        start_ms=0,
        end_ms=1,
        max_zone_width_atr=FILTER_OFF,
        iterations=1,
    )
    assert run_cell(task, log=False) == []


# ------------------------------------------------------- 3. 검산 분류


def test_classify_distinguishes_exact_noise_mismatch() -> None:
    assert _classify(10, 0.0)[0] == "일치"
    assert _classify(10, 1e-15)[0] == "잡음"
    assert _classify(10, 1e-3)[0] == "불일치"
    assert _classify(0, None)[0] == "불일치"


def test_compare_null_max_abs_diff_and_missing_keys() -> None:
    cols = {
        c: 0.0
        for c in wan151.NullRow.model_fields
        if c not in ("symbol", "timeframe", "segment", "arm", "fill")
    }
    ours = pd.DataFrame(
        [
            {"symbol": "A", "timeframe": "1h", "segment": "is", **cols, "real_total_return": 1.0},
            {"symbol": "B", "timeframe": "1h", "segment": "is", **cols, "real_total_return": 2.0},
        ]
    )
    ref = pd.DataFrame(
        [
            {"symbol": "A", "timeframe": "1h", "segment": "is", **cols, "real_total_return": 1.0},
            {"symbol": "B", "timeframe": "1h", "segment": "is", **cols, "real_total_return": 2.5},
            {"symbol": "C", "timeframe": "1h", "segment": "is", **cols, "real_total_return": 9.0},
        ]
    )
    compared, diff = _compare_null(ours, ref)
    assert compared == 2  # "C"는 비교 대상에 못 든다
    assert diff is not None
    assert abs(diff - 0.5) < 1e-12


def test_verify_rows_round_trip(tmp_path: Path) -> None:
    rows = [
        VerifyRow(
            check="filter-on-9sym-vs-wan176",
            reference="x.csv",
            rows_compared=9,
            max_abs_diff=0.0,
            status="일치",
            note="차이 0 — 비트 단위 재현.",
        ),
        VerifyRow(
            check="filter-off-6sym-vs-wan151",
            reference="y.csv",
            rows_compared=12,
            max_abs_diff=None,
            status="일치",
            note="",
        ),
    ]
    path = tmp_path / "verify.csv"
    pd.DataFrame([vars(r) for r in rows]).to_csv(path, index=False)
    assert verify_rows_from_csv(path) == rows


# ------------------------------------------------------- 4. 2×2 분해 · 4h 게이트


def _null_row(
    *, symbol: str, tf: str, seg: str, ret: float, n: int, rand: float, p: float
) -> wan151.NullRow:
    return wan151.NullRow(
        symbol=f"{symbol}/USDT:USDT",
        timeframe=tf,
        segment=seg,
        arm=LONG_ARM,
        fill="baseline",
        combine_obs=False,
        real_total_return=ret,
        real_num_trades=n,
        real_long=n,
        real_short=0,
        pool_size=n * 2,
        random_mean_return=rand,
        random_ci_low=rand - 0.1,
        random_ci_high=rand + 0.1,
        random_p_value=p,
        iterations=200,
        bucket_fallback_count=0,
        zones=100,
        buy_hold=0.1,
    )


def test_decomposition_table_counts_common_tfs_only() -> None:
    """대조 상대 널이 4h를 안 가졌으므로 2×2는 공통 TF(15m·1h)만 센다."""
    # 필터 꺼짐 × 9: 15m 두 셀 유의, 1h 한 셀 유의, 4h는 무시돼야 한다.
    off9 = [
        _null_row(symbol="BTC", tf="15m", seg="oos", ret=0.5, n=100, rand=0.1, p=0.01),
        _null_row(symbol="ETH", tf="15m", seg="oos", ret=0.5, n=100, rand=0.1, p=0.01),
        _null_row(symbol="BTC", tf="1h", seg="oos", ret=0.5, n=100, rand=0.1, p=0.01),
        _null_row(symbol="BTC", tf="4h", seg="oos", ret=9.9, n=100, rand=0.1, p=0.01),
    ]
    table = "\n".join(decomposition_table(off9=off9, on9=[], off6=[], on6=[]))
    assert "9종목 · 6년" in table
    assert "유의 3/3" in table  # 15m 2 + 1h 1, 4h 제외
    assert "4h" not in table


def test_axis_decomposition_reports_filter_and_universe() -> None:
    off9 = [_null_row(symbol="BTC", tf="1h", seg="oos", ret=0.1, n=100, rand=0.5, p=0.9)]
    on9 = [_null_row(symbol="BTC", tf="1h", seg="oos", ret=0.9, n=100, rand=0.1, p=0.01)]
    lines = "\n".join(axis_decomposition_lines(off9=off9, on9=on9, off6=[], on6=[]))
    assert "필터 축" in lines
    assert "유니버스·창 축" in lines
    assert "강화" in lines  # 꺼짐 0/1 → 켜짐 1/1


def test_four_h_gate_blocks_thin_cells() -> None:
    thin = [
        _null_row(symbol="BTC", tf="4h", seg="oos", ret=0.1, n=5, rand=0.0, p=0.01),
        _null_row(symbol="ETH", tf="4h", seg="oos", ret=0.1, n=7, rand=0.0, p=0.01),
    ]
    text = "\n".join(four_h_gate_lines(thin))
    assert "판정 불가(대조군)" in text


def test_verdict_breakdown_includes_4h_and_sums() -> None:
    """§1 판정 괄호 분해가 15m·1h·4h를 모두 세어 합이 총 유의 수와 맞는다."""
    rows = [
        _null_row(symbol="BTC", tf="15m", seg="oos", ret=0.5, n=100, rand=0.1, p=0.01),
        _null_row(symbol="ETH", tf="1h", seg="oos", ret=0.5, n=100, rand=0.1, p=0.01),
        _null_row(symbol="SOL", tf="4h", seg="oos", ret=0.5, n=100, rand=0.1, p=0.01),
        _null_row(symbol="XRP", tf="4h", seg="oos", ret=0.1, n=100, rand=0.5, p=0.9),
    ]
    text = verdict_all_tfs(rows)
    assert "유의 3개" in text
    assert "15m 1/1" in text and "1h 1/1" in text and "4h 1/2" in text
    assert "(c)" in text  # 일부만 유의


def test_four_h_gate_passes_when_sample_sufficient() -> None:
    thick = [
        _null_row(symbol="BTC", tf="4h", seg="oos", ret=0.5, n=30, rand=0.1, p=0.01),
        _null_row(symbol="ETH", tf="4h", seg="oos", ret=0.5, n=25, rand=0.1, p=0.01),
    ]
    text = "\n".join(four_h_gate_lines(thick))
    assert "유효 기준 충족" in text
