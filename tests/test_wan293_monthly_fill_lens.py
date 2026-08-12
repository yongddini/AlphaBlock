"""WAN-293 체결 보수화 렌즈 월별 재측정의 자(尺)를 동작으로 고정한다.

지키는 것:

1. **시드 평균** — 탈락 렌즈의 방향·북 행이 (키)로 시드 간 평균되고, 한 시드에 없는 달은
   0으로 쳐서 **고정 시드 수**로 나눈다.
2. **baseline 불변** — `num_seeds=1`(시드 하나) 평균은 원값을 그대로 보존한다(완료기준 2의
   근거 — baseline 팔은 WAN-288과 같은 값).
3. **잔존율·장세 분해** — 숏 델타·regime split·잔존율이 렌즈별로 올바로 계산되고, 기준이
   0 근처면 잔존율을 정의하지 않는다(WAN-115 부호 함정 가드).
4. **CSV 왕복·append 병합** — 렌즈 키를 포함해 보존·덮어쓴다.
5. **엔진 seed 배선** — `run_cells`/`build_params`가 시드를 후보 생성으로 흘리고, 탈락 없는
   렌즈에서는 시드가 무관하다(비트 재현).
6. **커밋된 baseline 팔이 WAN-288 CSV를 재현** — 렌즈 축이 baseline 결과를 안 건드린다.
7. **요약 렌더** — 잔존율 표·TF 표·경고가 나온다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from backtest import harness
from backtest.wan169_leverage_book import run_cells
from backtest.wan288_monthly_long_short import (
    ARM_LONG_ONLY,
    ARM_LONG_SHORT,
    BookMonthlyRow,
    DirMonthlyRow,
)
from backtest.wan293_monthly_fill_lens import (
    DEFAULT_LENSES,
    REF_LENS,
    LensBookRow,
    LensDirRow,
    _average_book,
    _average_dir,
    book_from_csv,
    book_to_frame,
    build_summary_markdown,
    dir_from_csv,
    dir_to_frame,
    merge_book,
    merge_dir,
    regime_split,
    residual_pct,
    short_delta_by_month,
)

# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def _dir(
    symbol: str,
    tf: str,
    direction: str,
    month: str,
    pp: float,
    trades: int,
    *,
    buy_hold: float = 0.0,
    gap: bool = False,
) -> DirMonthlyRow:
    return DirMonthlyRow(
        symbol=harness.normalize_symbol(symbol),
        timeframe=tf,
        direction=direction,
        month=month,
        net_pp_sum=pp,
        num_trades=trades,
        symbol_buy_hold=buy_hold,
        funding_gap=gap,
    )


def _book(
    scope: str, arm: str, month: str, ret: float, btc: float, exits: int = 1
) -> BookMonthlyRow:
    return BookMonthlyRow(
        scope=scope,
        arm=arm,
        month=month,
        monthly_return=ret,
        equity_end=1.0 + ret,
        num_exits=exits,
        btc_buy_hold=btc,
    )


def _lbook(
    lens: str, scope: str, arm: str, month: str, ret: float, btc: float, seeds: int = 1
) -> LensBookRow:
    return LensBookRow(
        lens=lens,
        scope=scope,
        arm=arm,
        month=month,
        monthly_return=ret,
        equity_end=1.0 + ret,
        num_exits=1.0,
        btc_buy_hold=btc,
        num_seeds=seeds,
    )


# --------------------------------------------------------------------------- #
# 1. 시드 평균 · 2. baseline 불변
# --------------------------------------------------------------------------- #


def test_average_dir_folds_across_seeds_with_missing_as_zero() -> None:
    # 시드 0: 두 달, 시드 1: 한 달만(다른 달은 탈락으로 없음) → 고정 시드 수 2로 나눈다.
    seed0 = [
        _dir("BTCUSDT", "1h", "short", "2024-09", 4.0, 2, buy_hold=-0.1),
        _dir("BTCUSDT", "1h", "short", "2024-10", 2.0, 1, buy_hold=0.05),
    ]
    seed1 = [_dir("BTCUSDT", "1h", "short", "2024-09", 2.0, 1, buy_hold=-0.1)]
    out = _average_dir([seed0, seed1], "pen_5bp_drop_50", 2)
    by_month = {r.month: r for r in out}
    # 2024-09: (4.0 + 2.0)/2 = 3.0, 거래 (2+1)/2 = 1.5
    assert by_month["2024-09"].net_pp_sum == 3.0
    assert by_month["2024-09"].num_trades == 1.5
    # 2024-10: 시드 1에 없음 → (2.0 + 0)/2 = 1.0
    assert by_month["2024-10"].net_pp_sum == 1.0
    assert by_month["2024-10"].num_trades == 0.5
    assert all(r.lens == "pen_5bp_drop_50" and r.num_seeds == 2 for r in out)
    # 결정적 메타(장세)는 보존
    assert by_month["2024-09"].symbol_buy_hold == -0.1


def test_average_dir_single_seed_preserves_values() -> None:
    """완료기준 2 근거 — num_seeds=1 평균은 원값 그대로(baseline 팔 불변)."""
    rows = [_dir("ETHUSDT", "4h", "long", "2025-01", 3.14159, 7, buy_hold=0.2)]
    out = _average_dir([rows], REF_LENS, 1)
    assert len(out) == 1
    assert out[0].net_pp_sum == 3.14159
    assert out[0].num_trades == 7.0
    assert out[0].lens == REF_LENS
    assert out[0].num_seeds == 1


def test_average_book_folds_across_seeds() -> None:
    seed0 = [_book("all", ARM_LONG_SHORT, "2024-09", 0.10, -0.08)]
    seed1 = [_book("all", ARM_LONG_SHORT, "2024-09", 0.20, -0.08)]
    out = _average_book([seed0, seed1], "drop_50", 2)
    assert len(out) == 1
    assert abs(out[0].monthly_return - 0.15) < 1e-12
    assert out[0].num_seeds == 2
    assert out[0].lens == "drop_50"


# --------------------------------------------------------------------------- #
# 3. 잔존율 · 장세 분해
# --------------------------------------------------------------------------- #


def test_short_delta_and_regime_split_per_lens() -> None:
    rows = [
        _lbook("pen_5bp", "all", ARM_LONG_ONLY, "2024-08", 0.05, 0.10),
        _lbook("pen_5bp", "all", ARM_LONG_SHORT, "2024-08", 0.03, 0.10),  # 상승: 숏 −0.02
        _lbook("pen_5bp", "all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _lbook("pen_5bp", "all", ARM_LONG_SHORT, "2024-09", 0.01, -0.08),  # 하락: 숏 +0.05
        # 다른 렌즈는 걸러져야 한다
        _lbook("baseline", "all", ARM_LONG_SHORT, "2024-09", 9.9, -0.08),
    ]
    delta = short_delta_by_month(rows, "pen_5bp", "all")
    assert abs(delta["2024-08"] - (0.03 - 0.05)) < 1e-12
    assert abs(delta["2024-09"] - (0.01 - (-0.04))) < 1e-12
    down, up, net = regime_split(rows, "pen_5bp", "all")
    assert abs(down - 0.05) < 1e-12
    assert abs(up - (-0.02)) < 1e-12
    assert abs(net - 0.03) < 1e-12


def test_residual_pct_and_zero_guard() -> None:
    assert residual_pct(0.03, 0.06) == 0.5
    got = residual_pct(-0.01, 0.05)
    assert got is not None and abs(got - (-0.2)) < 1e-12
    # 기준이 0 근처면 정의하지 않는다(WAN-115 부호 함정)
    assert residual_pct(0.03, 0.0) is None
    assert residual_pct(0.03, 1e-12) is None


# --------------------------------------------------------------------------- #
# 4. CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def test_dir_roundtrip(tmp_path: Path) -> None:
    rows = [
        LensDirRow(
            lens="pen_5bp_drop_50",
            symbol=harness.normalize_symbol("BTCUSDT"),
            timeframe="1h",
            direction="short",
            month="2024-09",
            net_pp_sum=3.0,
            num_trades=1.5,
            symbol_buy_hold=-0.1,
            funding_gap=False,
            num_seeds=5,
        )
    ]
    path = tmp_path / "dir.csv"
    dir_to_frame(rows).to_csv(path, index=False)
    assert dir_from_csv(path) == rows


def test_book_roundtrip(tmp_path: Path) -> None:
    rows = [_lbook("pen_5bp", "all", ARM_LONG_SHORT, "2024-09", 0.01, -0.08, seeds=1)]
    path = tmp_path / "book.csv"
    book_to_frame(rows).to_csv(path, index=False)
    assert book_from_csv(path) == rows


def test_merge_dir_overwrites_by_lens_key() -> None:
    existing = [
        LensDirRow(
            lens="baseline",
            symbol=harness.normalize_symbol("BTCUSDT"),
            timeframe="1h",
            direction="short",
            month="2024-09",
            net_pp_sum=3.0,
            num_trades=2.0,
            symbol_buy_hold=0.0,
            funding_gap=False,
            num_seeds=1,
        )
    ]
    new = [
        LensDirRow(
            lens="baseline",
            symbol=harness.normalize_symbol("BTCUSDT"),
            timeframe="1h",
            direction="short",
            month="2024-09",
            net_pp_sum=5.0,  # 덮어씀
            num_trades=4.0,
            symbol_buy_hold=0.0,
            funding_gap=False,
            num_seeds=1,
        ),
        LensDirRow(
            lens="pen_5bp",  # 다른 렌즈 → 새 행
            symbol=harness.normalize_symbol("BTCUSDT"),
            timeframe="1h",
            direction="short",
            month="2024-09",
            net_pp_sum=1.0,
            num_trades=1.0,
            symbol_buy_hold=0.0,
            funding_gap=False,
            num_seeds=1,
        ),
    ]
    merged = merge_dir(existing, new)
    assert len(merged) == 2
    by_lens = {r.lens: r for r in merged}
    assert by_lens["baseline"].net_pp_sum == 5.0
    assert by_lens["pen_5bp"].net_pp_sum == 1.0


def test_merge_book_overwrites_by_lens_key() -> None:
    existing = [_lbook("baseline", "all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08)]
    new = [
        _lbook("baseline", "all", ARM_LONG_ONLY, "2024-09", 0.02, -0.08),  # 덮어씀
        _lbook("pen_5bp", "all", ARM_LONG_ONLY, "2024-09", 0.01, -0.08),  # 새 렌즈
    ]
    merged = merge_book(existing, new)
    assert len(merged) == 2
    by_lens = {r.lens: r for r in merged}
    assert by_lens["baseline"].monthly_return == 0.02
    assert by_lens["pen_5bp"].monthly_return == 0.01


# --------------------------------------------------------------------------- #
# 5. 엔진 seed 배선
# --------------------------------------------------------------------------- #


def test_run_cells_accepts_seed_param() -> None:
    """WAN-293 배선 — `run_cells`가 seed 인자를 받는다(탈락 렌즈 시드 평균의 전제)."""
    assert "seed" in inspect.signature(run_cells).parameters


def test_build_params_threads_seed_only_matters_for_dropout() -> None:
    drop = harness.fill_preset("pen_5bp_drop_50")
    assert drop.dropout_rate > 0
    p3 = harness.build_params(fill=drop, seed=3)
    assert p3.fill_dropout_seed == 3
    assert p3.fill_dropout_rate == drop.dropout_rate
    assert p3.fill_penetration_bps == drop.penetration_bps
    # 탈락 없는 렌즈는 dropout_rate=0 → 시드가 후보 생성 RNG를 만들지 않는다(무관).
    for name in ("baseline", "pen_5bp"):
        preset = harness.fill_preset(name)
        assert preset.dropout_rate == 0.0
    # dropout 렌즈는 5개 시드로 고정(WAN-96 관행 · 재현 가능)
    assert drop.seeds == (0, 1, 2, 3, 4)


# --------------------------------------------------------------------------- #
# 6. 커밋된 baseline 팔이 WAN-288 CSV를 재현 (완료기준 2)
# --------------------------------------------------------------------------- #


def test_committed_baseline_lens_reproduces_wan288() -> None:
    """완료기준 2 — wan293의 `baseline` 렌즈 행이 wan288 CSV를 비트 재현한다.

    두 CSV를 읽어 대조한다(엔진 재실행 없음). 렌즈 축은 baseline 결과를 안 건드려야 하므로,
    per-cell(방향)·per-TF 북 스코프에서 wan293 baseline == wan288이어야 한다. `all` 스코프는
    wan293가 커버한 TF 집합에 따라 달라지므로(전 TF 집계) 대조에서 뺀다.
    """
    import pandas as pd
    import pytest

    reports = Path("backtest/reports")
    w288_cell = reports / "wan288_monthly_by_cell.csv"
    w288_book = reports / "wan288_monthly_book.csv"
    w293_cell = reports / "wan293_monthly_by_cell.csv"
    w293_book = reports / "wan293_monthly_book.csv"
    if not all(p.exists() for p in (w288_cell, w288_book, w293_cell, w293_book)):
        pytest.skip("리포트 CSV가 없음(측정 미실행 환경)")

    c288 = pd.read_csv(w288_cell)
    c293 = pd.read_csv(w293_cell)
    base = c293[c293.lens == REF_LENS]
    assert len(base) > 0
    tfs = sorted(base.timeframe.unique())
    key = ["symbol", "timeframe", "direction", "month"]
    ref = c288[c288.timeframe.isin(tfs)][key + ["net_pp_sum", "num_trades"]]
    merged = base.merge(ref, on=key, suffixes=("_293", "_288"))
    assert len(merged) == len(ref) > 0
    assert (merged.net_pp_sum_293 - merged.net_pp_sum_288).abs().max() < 1e-9
    assert (merged.num_trades_293 - merged.num_trades_288).abs().max() < 1e-9

    b288 = pd.read_csv(w288_book)
    b293 = pd.read_csv(w293_book)
    base_b = b293[(b293.lens == REF_LENS) & (b293.scope != "all")]
    assert len(base_b) > 0
    scopes = sorted(base_b.scope.unique())
    bkey = ["scope", "arm", "month"]
    ref_b = b288[b288.scope.isin(scopes)][bkey + ["monthly_return", "equity_end", "num_exits"]]
    merged_b = base_b.merge(ref_b, on=bkey, suffixes=("_293", "_288"))
    assert len(merged_b) == len(ref_b) > 0
    assert (merged_b.monthly_return_293 - merged_b.monthly_return_288).abs().max() < 1e-9
    assert (merged_b.num_exits_293 - merged_b.num_exits_288).abs().max() < 1e-9


# --------------------------------------------------------------------------- #
# 7. 요약 렌더
# --------------------------------------------------------------------------- #


def test_summary_renders_key_sections() -> None:
    book_rows = [
        _lbook("baseline", "all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _lbook("baseline", "all", ARM_LONG_SHORT, "2024-09", 0.06, -0.08),
        _lbook("baseline", "1h", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _lbook("baseline", "1h", ARM_LONG_SHORT, "2024-09", 0.06, -0.08),
        _lbook("pen_5bp", "all", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _lbook("pen_5bp", "all", ARM_LONG_SHORT, "2024-09", 0.02, -0.08),
        _lbook("pen_5bp", "1h", ARM_LONG_ONLY, "2024-09", -0.04, -0.08),
        _lbook("pen_5bp", "1h", ARM_LONG_SHORT, "2024-09", 0.02, -0.08),
    ]
    dir_rows = [
        LensDirRow(
            lens="baseline",
            symbol=harness.normalize_symbol("BTCUSDT"),
            timeframe="1h",
            direction="short",
            month="2024-09",
            net_pp_sum=5.0,
            num_trades=2.0,
            symbol_buy_hold=-0.1,
            funding_gap=False,
            num_seeds=1,
        )
    ]
    md = build_summary_markdown(dir_rows, book_rows, funding_notes={"pen_5bp": "BTC"})
    assert "잔존율" in md
    assert "롱-온리 북 vs 롱+숏 북" in md
    assert "복리 착시" in md
    assert "WAN-98" in md
    assert "2024-09" in md
    # 렌즈가 순서대로 나온다
    assert md.index("`baseline`") < md.index("`pen_5bp`")


def test_default_lenses_are_the_three_band() -> None:
    assert DEFAULT_LENSES == ("baseline", "pen_5bp", "pen_5bp_drop_50")
    assert REF_LENS == "baseline"
