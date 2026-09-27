"""WAN-432: 하한 스윕의 **지름길**과 **판정**을 동작으로 고정한다.

이 모듈이 지키는 것 셋:

1. **복제한 워커가 갈라지지 않는다** — `wan432.cell_arms_at`를 WAN-424의 하한으로 부르면
   `wan424._cell_arms`와 **같은 객체**여야 한다(모듈 독스트링 「복제한 이유」).
2. **지름길이 성립한다** — 「낮은 하한으로 만들고 올려 거른 것」 ≡ 「처음부터 그 하한으로
   만든 것」. 이게 깨지면 하한 4% 행이 WAN-424 공개 CSV와 안 맞는다(완료기준 4).
3. **판정을 사람이 안 한다** — argmax·뒤집힘·등수·좌표 가드가 전부 코드다.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest import wan424_stoch_ob_arm as w424
from backtest import wan432_stoch_floor_robustness as m
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.wan169_leverage_book import CellPayload
from backtest.zone_limit_backtest import _Candidate

H = 3_600_000


def _cand(*, entry_time: int, stop_price: float, entry_price: float = 100.0) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=entry_time + H,
        exit_price=entry_price * 1.01,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=stop_price,
        take_profit_price=entry_price * 1.075,
        trigger_time=entry_time,
        tap_index=0,
        is_reentry=False,
    )


def _steps(n_bars: int, *, per_bar: int = 60) -> list[SubStep]:
    out = []
    for i in range(n_bars * per_bar):
        t = i * 60_000
        close = 100.0 + 0.01 * i
        out.append(
            SubStep(time=t, high=close + 0.5, low=99.999, close=close, htf_bar_time=(t // H) * H)
        )
    return out


# --- 1. 축·상수가 이슈 사양 그대로인가 -------------------------------------------------


def test_floors_are_the_six_wan430_points() -> None:
    """자를 새로 쓰지 않는다 — WAN-430 §2와 같은 여섯 점이고 팔 풀은 가장 낮은 점에서 만든다."""
    assert m.FLOORS == (0.005, 0.01, 0.015, 0.02, 0.03, 0.04)
    assert m.ARM_FLOOR == 0.005
    assert m.ADOPTED_FLOOR == 0.04
    assert m.ADOPTED_FLOOR in m.FLOORS


def test_other_axes_are_inherited_not_redefined() -> None:
    """%K 문턱·보유 봉·TF 집합은 WAN-424 §1 값을 **그대로** 쓴다(축은 하한 하나다)."""
    assert m.THRESHOLDS is w424.THRESHOLDS
    assert m.HOLD_BARS is w424.HOLD_BARS
    assert m.TIMEFRAMES is w424.TIMEFRAMES
    assert m.SYMBOLS is w424.SYMBOLS
    assert len(m.TIMEFRAMES) == 9


# --- 2. 지름길: 낮게 만들고 올려 거른 것 ≡ 처음부터 그 하한 ---------------------------


def test_arm_pool_low_floor_then_filter_equals_direct_pool() -> None:
    cands = [
        _cand(entry_time=0, stop_price=99.5),  # 0.5%
        _cand(entry_time=H, stop_price=98.0),  # 2%
        _cand(entry_time=2 * H, stop_price=96.0),  # 4%
    ]
    low = w424.arm_pool(cands, min_width=0.005)
    assert [c.entry_time for c in low] == [0, H, 2 * H]
    for floor in m.FLOORS:
        direct = w424.arm_pool(cands, min_width=floor)
        via_low = [c for c in low if w424.stop_width(c) >= floor]
        assert direct == via_low, f"하한 {floor}에서 지름길이 깨졌다"


def test_time_exit_is_independent_of_pool_size() -> None:
    """지름길이 성립하는 **이유** — 후보가 늘어도 남는 후보의 청산이 안 바뀐다."""
    steps = _steps(6)
    wide = _cand(entry_time=0, stop_price=96.0)
    narrow = _cand(entry_time=60_000, stop_price=99.5)
    alone = w424.derive_hold_arms([wide], substeps=steps, holds=(4,))[4]
    crowded = w424.derive_hold_arms([wide, narrow], substeps=steps, holds=(4,))[4]
    assert len(alone) == 1 and len(crowded) == 2
    assert alone[0] == crowded[0]


# --- 3. 복제한 워커가 WAN-424와 갈라지지 않는가 ----------------------------------------


class _FakeMarket:
    def __init__(self, df_1m: pd.DataFrame) -> None:
        self.df_1m = df_1m


def _install_fakes(monkeypatch: pytest.MonkeyPatch, steps: list[SubStep]) -> None:
    """데이터 층을 두 모듈에 **똑같이** 끼운다 — 남는 차이는 하한 인자 하나뿐이다."""
    market = _FakeMarket(pd.DataFrame())
    monkeypatch.setattr(harness, "load_market_data", lambda *a, **k: market)
    monkeypatch.setattr(harness, "slice_market", lambda mk, seg: mk)
    for mod in (m, w424):
        monkeypatch.setattr(mod, "build_substeps", lambda df, tf_ms: steps)
        monkeypatch.setattr(mod, "stoch_series", lambda store, sym, tf: ([0], [42.0]))
        monkeypatch.setattr(mod, "OhlcvStore", lambda path: object())


def _payload(cands: list[_Candidate]) -> CellPayload:
    by_segment = {
        seg: tuple(cands) for seg in (harness.SEGMENT_FULL, harness.SEGMENT_IS, harness.SEGMENT_OOS)
    }
    return CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        boundary_ms=0,
        candidates=by_segment,
        funding={},
        rows=(),
        reentry_candidates={},
    )


def test_cell_arms_at_matches_wan424_worker_at_the_same_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """🚨 복제 가드 — 같은 하한이면 **같은 객체**여야 한다(라벨이 아니라 값으로)."""
    steps = _steps(8)
    cands = [
        _cand(entry_time=0, stop_price=99.5),
        _cand(entry_time=60_000, stop_price=98.0),
        _cand(entry_time=120_000, stop_price=96.0),
    ]
    payload = _payload(cands)
    _install_fakes(monkeypatch, steps)
    mine = m.cell_arms_at((payload, w424.STOP_WIDTH_LOG_FLOOR))
    theirs = w424._cell_arms(payload)
    assert mine == theirs


def test_cell_arms_at_lower_floor_is_a_superset(monkeypatch: pytest.MonkeyPatch) -> None:
    """낮은 하한은 **더 많이** 담고, 올려 거르면 높은 하한과 같아진다(돌연변이 확인)."""
    steps = _steps(8)
    cands = [
        _cand(entry_time=0, stop_price=99.5),
        _cand(entry_time=60_000, stop_price=98.0),
        _cand(entry_time=120_000, stop_price=96.0),
    ]
    payload = _payload(cands)
    _install_fakes(monkeypatch, steps)
    low = m.cell_arms_at((payload, m.ARM_FLOOR))
    high = m.cell_arms_at((payload, w424.STOP_WIDTH_LOG_FLOOR))
    for hold in w424.HOLD_BARS:
        for seg, cands_low in low.arms[hold].items():
            kept = tuple(c for c in cands_low if w424.stop_width(c) >= w424.STOP_WIDTH_LOG_FLOOR)
            assert kept == high.arms[hold][seg]
    assert len(low.arms[4][harness.SEGMENT_FULL]) > len(high.arms[4][harness.SEGMENT_FULL])


# --- 4. 판정은 코드가 낸다 --------------------------------------------------------------


def _row(
    *,
    floor: float,
    segment: str,
    mean: float,
    n: int = 100,
    se: float = 0.01,
    hold: int = 4,
    thr: float = 25.0,
) -> m.FloorRow:
    return m.FloorRow(
        hold=hold,
        floor=floor,
        threshold=thr,
        segment=segment,
        num_trades=n,
        win_rate=0.5,
        mean_net_r=mean,
        se_net_r=se,
        gross_r=mean + 0.1,
        cost_r=0.1,
        identity_max_abs=0.0,
        median_stop_width=0.02,
        fixed_return=0.0,
        fixed_mdd=0.0,
        compound_return=0.0,
        compound_mdd=0.0,
        compound_ruined=False,
    )


def test_sign_is_decided_uses_two_sigma() -> None:
    assert _row(floor=0.04, segment="oos_warm", mean=0.03, se=0.01).sign_is_decided
    assert not _row(floor=0.04, segment="oos_warm", mean=0.015, se=0.01).sign_is_decided
    assert not _row(floor=0.04, segment="oos_warm", mean=0.5, se=math.nan, n=1).sign_is_decided


def test_floor_argmax_and_rank() -> None:
    rows = [
        _row(floor=0.005, segment="oos_warm", mean=0.10),
        _row(floor=0.02, segment="oos_warm", mean=0.30),
        _row(floor=0.04, segment="oos_warm", mean=0.20),
    ]
    best = m.floor_argmax(rows, threshold=25.0, hold=4, segment="oos_warm")
    assert best is not None and best.floor == 0.02
    assert m.floor_rank(rows, threshold=25.0, hold=4, segment="oos_warm", floor=0.04) == 2
    assert m.floor_rank(rows, threshold=25.0, hold=4, segment="oos_warm", floor=0.005) == 3


def test_floor_argmax_ignores_empty_cells() -> None:
    rows = [
        _row(floor=0.04, segment="oos_warm", mean=9.9, n=0),
        _row(floor=0.02, segment="oos_warm", mean=0.1),
    ]
    best = m.floor_argmax(rows, threshold=25.0, hold=4, segment="oos_warm")
    assert best is not None and best.floor == 0.02


def test_verdict_flags_the_flip() -> None:
    """앞구간과 뒷구간이 다른 하한을 고르면 「앞구간에서 고른 값」이라고 코드가 찍는다."""
    rows = [
        _row(floor=0.04, segment="is", mean=0.50),
        _row(floor=0.005, segment="is", mean=0.10),
        _row(floor=0.04, segment="oos_warm", mean=0.01),
        _row(floor=0.005, segment="oos_warm", mean=0.20),
    ]
    flips = m.flip_census(rows, thresholds=(25.0,), holds=(4,))
    assert len(flips) == 1
    assert flips[0].is_floor == 0.04 and flips[0].oos_floor == 0.005
    assert flips[0].flipped
    assert "앞구간에서 고른 값이다" in m.verdict(flips)


def test_verdict_says_it_carries_when_argmax_agrees() -> None:
    rows = [
        _row(floor=0.04, segment="is", mean=0.50),
        _row(floor=0.005, segment="is", mean=0.10),
        _row(floor=0.04, segment="oos_warm", mean=0.50),
        _row(floor=0.005, segment="oos_warm", mean=0.10),
    ]
    flips = m.flip_census(rows, thresholds=(25.0,), holds=(4,))
    assert not flips[0].flipped
    assert "넘어간다" in m.verdict(flips)


# --- 5. 검산 가드 -----------------------------------------------------------------------


def test_checksum_is_skipped_off_the_adopted_coordinate() -> None:
    """🚨 좁혀 돈 판을 공개 CSV와 대조하면 좌표 차이가 배선 오류로 보인다(WAN-381)."""
    assert not m.on_adopted_coordinates(
        symbols=("BTC/USDT:USDT",), timeframes=m.TIMEFRAMES, holds=m.HOLD_BARS
    )
    assert not m.on_adopted_coordinates(symbols=m.SYMBOLS, timeframes=("4h",), holds=m.HOLD_BARS)
    assert not m.on_adopted_coordinates(symbols=m.SYMBOLS, timeframes=m.TIMEFRAMES, holds=(4,))
    assert m.on_adopted_coordinates(symbols=m.SYMBOLS, timeframes=m.TIMEFRAMES, holds=m.HOLD_BARS)
    lines, worst = m.checksum_wan424(
        [_row(floor=0.04, segment="oos_warm", mean=0.0)], adopted=False
    )
    assert math.isnan(worst)
    assert lines[0].startswith("⏭️")


def test_checksum_catches_a_moved_cell(tmp_path: Path) -> None:
    """돌연변이 확인 — 값을 하나만 흔들어도 검산이 잡는다."""
    ref = pd.DataFrame(
        [
            {
                "hold": 4,
                "floor": 0.04,
                "threshold": 25.0,
                "segment": "oos_warm",
                "num_trades": 100,
                "mean_net_r": 0.2,
                "se_net_r": 0.01,
                "fixed_return": 0.0,
                "fixed_mdd": 0.0,
                "compound_return": 0.0,
                "compound_mdd": 0.0,
            }
        ]
    )
    path = tmp_path / "ref.csv"
    ref.to_csv(path, index=False)
    same = [_row(floor=0.04, segment="oos_warm", mean=0.2, n=100, se=0.01)]
    lines, worst = m.checksum_wan424(same, reference_csv=path)
    assert worst == 0.0 and lines[0].startswith("✅")
    moved = [_row(floor=0.04, segment="oos_warm", mean=0.25, n=100, se=0.01)]
    lines, worst = m.checksum_wan424(moved, reference_csv=path)
    assert worst == pytest.approx(0.05) and lines[0].startswith("❌")


def test_checksum_only_looks_at_the_adopted_floor(tmp_path: Path) -> None:
    """스윕이 더한 새 하한 행은 대조 대상이 아니다(기준 CSV에 없는 게 정상이다)."""
    ref = pd.DataFrame(
        [
            {
                "hold": 4,
                "floor": 0.04,
                "threshold": 25.0,
                "segment": "oos_warm",
                "num_trades": 100,
                "mean_net_r": 0.2,
                "se_net_r": 0.01,
                "fixed_return": 0.0,
                "fixed_mdd": 0.0,
                "compound_return": 0.0,
                "compound_mdd": 0.0,
            }
        ]
    )
    path = tmp_path / "ref.csv"
    ref.to_csv(path, index=False)
    rows = [
        _row(floor=0.04, segment="oos_warm", mean=0.2, n=100, se=0.01),
        _row(floor=0.005, segment="oos_warm", mean=-9.9, n=100, se=0.01),
    ]
    lines, worst = m.checksum_wan424(rows, reference_csv=path)
    assert worst == 0.0 and lines[0].startswith("✅")


# --- 6. 표 왕복·렌더 --------------------------------------------------------------------


def test_frame_round_trip() -> None:
    rows = [
        _row(floor=0.005, segment="is", mean=0.1),
        _row(floor=0.04, segment="oos_warm", mean=-0.2),
    ]
    assert m.frame_to_rows(m.rows_to_frame(rows)) == rows


def test_summary_reports_verdict_identity_and_rank() -> None:
    rows = [
        _row(floor=0.04, segment="is", mean=0.50),
        _row(floor=0.005, segment="is", mean=0.10),
        _row(floor=0.04, segment="oos_warm", mean=0.01),
        _row(floor=0.005, segment="oos_warm", mean=0.20),
    ]
    text = m.render_summary(rows, adopted=False, floors=(0.005, 0.04))
    assert "앞구간에서 고른 값이다" in text
    assert "완료기준 3" in text and "완료기준 4" in text
    assert "채택 근거가 아니다" in text
    assert "1/2" in text or "2/2" in text


# --- 7. 뒤집힘의 **방향** ---------------------------------------------------------------
#
# 🚨 개수만 세면 헤드라인이 데이터와 반대가 된다. 실제로 이 모듈의 첫 완주가 그랬다 —
# 8/15가 뒤집히는데 **그 8개 전부 뒷구간이 더 조인 하한을 고르고** 뒷구간 argmax가
# 13/15조합에서 채택값이라, 「앞구간에서 고른 값」은 데이터와 정반대였다.


def _pair(
    *, floor: float, is_mean: float, oos_mean: float, hold: int, thr: float = 25.0
) -> list[m.FloorRow]:
    return [
        _row(floor=floor, segment="is", mean=is_mean, hold=hold, thr=thr),
        _row(floor=floor, segment="oos_warm", mean=oos_mean, hold=hold, thr=thr),
    ]


def test_verdict_reports_the_opposite_direction_when_oos_tightens() -> None:
    """앞구간은 느슨한 하한을, 뒷구간은 채택값을 고르면 「방향이 반대」라고 찍는다."""
    rows: list[m.FloorRow] = []
    for hold in (4, 8, 12):
        # 앞구간 최적 = 0.5%(느슨) · 뒷구간 최적 = 4%(채택값) → 「올린」 뒤집힘
        rows += _pair(floor=0.005, is_mean=0.50, oos_mean=-0.10, hold=hold)
        rows += _pair(floor=0.04, is_mean=0.10, oos_mean=0.30, hold=hold)
    flips = m.flip_census(rows, thresholds=(25.0,), holds=(4, 8, 12))
    assert all(f.flipped for f in flips)
    assert all(f.is_floor == 0.005 and f.oos_floor == m.ADOPTED_FLOOR for f in flips)

    text = m.verdict(flips)
    assert "방향이 반대다" in text
    # 🚨 돌연변이 — 옛 「개수만 세는」 판정은 여기서 정반대 문장을 냈다.
    assert "앞구간에서 고른 값이다" not in text
    # 한쪽으로 기울지 않게 반대편 경고도 함께 실린다.
    assert "채택값이 검증된 것도 아니다" in text
    assert "채택 근거가 아니" in text


def test_verdict_still_blames_is_when_the_flip_loosens_the_floor() -> None:
    """반대 방향(뒷구간이 더 느슨한 하한을 고른다)에서는 옛 문장이 그대로 나와야 한다."""
    rows: list[m.FloorRow] = []
    for hold in (4, 8, 12):
        rows += _pair(floor=0.04, is_mean=0.50, oos_mean=-0.10, hold=hold)
        rows += _pair(floor=0.005, is_mean=0.10, oos_mean=0.30, hold=hold)
    flips = m.flip_census(rows, thresholds=(25.0,), holds=(4, 8, 12))
    text = m.verdict(flips)
    assert "앞구간에서 고른 값이다" in text
    assert "방향이 반대다" not in text


def test_verdict_does_not_claim_reverse_direction_without_a_majority() -> None:
    """올린 뒤집힘이어도 뒷구간 argmax가 채택값이 **아니면** 그 문장을 쓰지 않는다."""
    rows: list[m.FloorRow] = []
    for hold in (4, 8, 12):
        rows += _pair(floor=0.005, is_mean=0.50, oos_mean=-0.10, hold=hold)
        rows += _pair(floor=0.02, is_mean=0.10, oos_mean=0.30, hold=hold)  # 채택값이 아니다
        rows += _pair(floor=0.04, is_mean=0.05, oos_mean=0.01, hold=hold)
    flips = m.flip_census(rows, thresholds=(25.0,), holds=(4, 8, 12))
    assert all(f.oos_floor == 0.02 for f in flips)
    assert "방향이 반대다" not in m.verdict(flips)


# --- 8. 검산 합격선이 **보고와 종료 코드에서 같다** ---------------------------------------
#
# 🚨 이 모듈의 첫 완주가 정확히 여기서 죽었다 — 표에는 「≈ 통과(1.78e-15)」라 찍고
# `main()`은 `worst == 0.0`을 요구해 **종료 코드 1**을 냈다. 「성공이 실패와 같은 모양」
# (WAN-194/318 §3/321의 거울상)이라 사람이 실패한 실행으로 읽는다.


def test_checksum_passes_accepts_csv_round_trip_noise() -> None:
    assert m.checksum_passes(0.0)
    assert m.checksum_passes(1.78e-15)  # 첫 완주가 실제로 낸 값
    assert m.checksum_passes(math.nan)  # 좁혀 돈 좌표 = 대조 건너뜀
    assert not m.checksum_passes(1e-6)
    # 돌연변이 — `worst == 0.0`으로 되돌리면 이 줄이 깨진다.
    assert m.checksum_passes(m.CHECKSUM_TOL / 2)
    assert not m.checksum_passes(m.CHECKSUM_TOL)


def test_checksum_mark_and_exit_code_read_the_same_line(tmp_path: Path) -> None:
    """「≈」로 찍힌 실행은 반드시 종료 코드 0이어야 한다 — 두 자가 갈라지면 안 된다."""
    ref = pd.DataFrame(
        [
            {
                "hold": 4,
                "floor": 0.04,
                "threshold": 25.0,
                "segment": "oos_warm",
                "num_trades": 100,
                "mean_net_r": 0.2,
                "se_net_r": 0.01,
                "fixed_return": 0.0,
                "fixed_mdd": 0.0,
                "compound_return": 0.0,
                "compound_mdd": 0.0,
            }
        ]
    )
    path = tmp_path / "ref.csv"
    ref.to_csv(path, index=False)
    # CSV 텍스트 왕복 끝자리만큼만 어긋난 행.
    nudged = [_row(floor=0.04, segment="oos_warm", mean=0.2 + 1e-15, n=100, se=0.01)]
    lines, worst = m.checksum_wan424(nudged, reference_csv=path)
    assert lines[0].startswith("≈") and worst > 0.0
    assert m.checksum_passes(worst), "≈로 찍고 종료 코드 1을 내면 안 된다"

    moved = [_row(floor=0.04, segment="oos_warm", mean=0.25, n=100, se=0.01)]
    lines, worst = m.checksum_wan424(moved, reference_csv=path)
    assert lines[0].startswith("❌") and not m.checksum_passes(worst)


# --- 9. 비용인가 시장인가 ---------------------------------------------------------------


def _split_rows(*, is_gross: float, oos_gross: float) -> list[m.FloorRow]:
    """바닥 하한에서 채택 하한으로 갈 때 gross/비용이 정해진 만큼 움직이는 두 칸씩."""

    def mk(floor: float, segment: str, gross: float, cost: float) -> m.FloorRow:
        r = _row(floor=floor, segment=segment, mean=gross - cost)
        return dataclasses.replace(r, gross_r=gross, cost_r=cost)

    return [
        mk(m.ARM_FLOOR, "is", 0.10, 0.06),
        mk(m.ADOPTED_FLOOR, "is", 0.10 + is_gross, 0.02),
        mk(m.ARM_FLOOR, "oos_warm", 0.10, 0.06),
        mk(m.ADOPTED_FLOOR, "oos_warm", 0.10 + oos_gross, 0.02),
    ]


def test_cost_vs_market_splits_net_into_gross_and_cost() -> None:
    rows = _split_rows(is_gross=0.20, oos_gross=0.20)
    splits = {s.segment: s for s in m.cost_vs_market(rows, segments=("is", "oos_warm"))}
    got = splits["oos_warm"]
    assert got.delta_gross == pytest.approx(0.20)
    assert got.delta_cost == pytest.approx(0.04)
    # 항등식 — Δnet = Δgross + Δ비용 감소.
    assert got.delta_net == pytest.approx(got.delta_gross + got.delta_cost)
    assert got.share_is_readable and got.gross_share == pytest.approx(0.20 / 0.24)


def test_split_share_is_hidden_when_gross_and_net_disagree() -> None:
    """🚨 「시장 몫 −32%」는 비율이 아니다 — 부호가 갈리면 내지 않는다(WAN-115/395)."""
    rows = _split_rows(is_gross=-0.01, oos_gross=0.20)
    splits = {s.segment: s for s in m.cost_vs_market(rows, segments=("is", "oos_warm"))}
    assert splits["is"].delta_net > m.NOISE_R  # 분모는 잡음선 밖인데
    assert splits["is"].delta_gross < 0  # 분자가 반대 부호라
    assert not splits["is"].share_is_readable  # 비율을 안 낸다
    assert "—" in m.render_summary(rows, adopted=False, floors=(m.ARM_FLOOR, m.ADOPTED_FLOOR))


def test_split_reading_is_derived_from_the_table_not_hardcoded() -> None:
    """데이터가 바뀌면 문장도 바뀐다 — 숫자를 산문에 박아 두면 문장만 낡는다."""
    only_oos = m.cost_vs_market(
        _split_rows(is_gross=-0.01, oos_gross=0.20), segments=("is", "oos_warm")
    )
    both = m.cost_vs_market(_split_rows(is_gross=0.20, oos_gross=0.20), segments=("is", "oos_warm"))
    assert "기계적인 비용 절감뿐" in m.split_reading(only_oos)
    assert "시장 몫도 구간을 넘어간다" in m.split_reading(both)
    # 한쪽 구간이 없으면 지어내지 않는다.
    assert "판정하지 않는다" in m.split_reading([s for s in both if s.segment == "is"])


def test_split_reading_uses_the_warm_oos_segment() -> None:
    """🚨 뒷구간은 따뜻한 `oos_warm`이다 — 차가운 `oos` 키를 쓰면 조용히 접힌다(실제로 그랬다)."""
    assert harness.SEGMENT_OOS_WARM == "oos_warm"
    assert harness.SEGMENT_OOS != harness.SEGMENT_OOS_WARM
    rows = _split_rows(is_gross=-0.01, oos_gross=0.20)
    assert "판정하지 않는다" not in m.split_reading(
        m.cost_vs_market(rows, segments=("is", harness.SEGMENT_OOS_WARM))
    )
