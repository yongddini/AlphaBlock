"""WAN-450 — 캐리 일반화 · 명목 훑기 · 가정 상한 · 공개 표 검산."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest import wan436_stoch_crowd_rules as w
from backtest import wan447_funding_carry as fc
from backtest import wan449_tsmom_second_engine as e
from backtest import wan450_carry_recent_funding as x

DAY = 86_400_000
MIN = 60_000
T0 = 1_609_459_200_000  # 2021-01-01


def test_constants_are_the_issue_spec() -> None:
    assert x.FRACTIONS == (0.0, 0.5, 1.0)
    assert x.VERDICT_FRACTION == 0.5
    assert x.SERIES == (fc.SERIES_ACTUAL, fc.SERIES_TRAILING)
    assert x.CAPS == (3.0, 5.0)
    assert x.TOP_DAY_SHARE == 0.01 and x.CARRY_LEGS == 2.0


def _fund() -> fc.Funding:
    times = np.array([T0 + d * DAY + 8 * 3_600_000 for d in range(1, 6)], dtype=np.int64)
    return fc.Funding(times, np.array([0.001, -0.0005, 0.002, 0.0, 0.0007]))


def _idx(n: int = 8) -> pd.DatetimeIndex:
    return pd.date_range("2021-01-01", periods=n, freq="D").as_unit("ms")


def test_carry_daily_generalises_the_wan449_half_carry() -> None:
    """비율 0.5 · 같은 펀딩이면 WAN-449 함수와 같은 값(일반화가 값을 안 바꾼다)."""
    f = _fund()
    rows = {s: list(zip(f.times.tolist(), f.rates.tolist(), strict=True)) for s in e.ASSETS}
    mine = x.carry_daily(f, _idx(), 0.5)
    assert mine is not None
    pd.testing.assert_series_equal(mine, e.carry_daily(rows, _idx()))


def test_carry_daily_zero_fraction_is_no_carry_and_scales_with_fraction() -> None:
    f = _fund()
    assert x.carry_daily(f, _idx(), 0.0) is None
    half, full = x.carry_daily(f, _idx(), 0.5), x.carry_daily(f, _idx(), 1.0)
    assert half is not None and full is not None
    assert full.iloc[1] == pytest.approx(0.001) and half.iloc[1] == pytest.approx(0.0005)
    assert full.iloc[0] == pytest.approx(-fc.CARRY_COST)  # 진입 비용은 첫날 한 번 · 비율만큼


def test_carry_daily_flat_scenarios() -> None:
    """고정 시나리오는 같은 정산 시각에 요율만 바꾼다 · 0 고정은 비용만 낸다(죽지 않는다)."""
    f = _fund()
    flat = x.carry_daily(fc.flat(f, 0.0001), _idx(), 1.0)
    zero = x.carry_daily(fc.flat(f, 0.0), _idx(), 1.0)
    assert flat is not None and zero is not None
    assert flat.iloc[1:6].to_numpy() == pytest.approx([0.0001] * 5)
    assert float(zero.iloc[1:].abs().max()) == 0.0
    far = fc.Funding(np.array([T0 + 500 * DAY], dtype=np.int64), np.array([0.001]))
    with pytest.raises(ValueError, match="전부 0"):
        x.carry_daily(far, _idx(), 0.5)


def _trade(entry_min: int, exit_min: int, net_r: float, *, stop: float = 95.0) -> w.PlacedTrade:
    return w.PlacedTrade(
        symbol="AAA",
        timeframe="1h",
        entry_time=T0 + entry_min * MIN,
        exit_time=T0 + exit_min * MIN,
        net_r=net_r,
        stopped=net_r < 0,
        back=False,
        size=1.0,
        entry_price=100.0,
        stop_price=stop,
        path_times=np.array([], dtype=np.int64),
        path_lows=np.array([]),
        path_closes=np.array([]),
    )


def _trades() -> list[w.PlacedTrade]:
    return [
        _trade(0, 600, 1.5),
        _trade(10, 700, -1.2),
        _trade(20, 20, -1.0),  # 보유 0분
        _trade(2000, 5000, 0.8, stop=99.0),  # 손절폭 1% → 명목이 크다
        _trade(2100, 2300, -1.1),
    ]


def test_sweep_matches_mtm_path() -> None:
    """막지 않은 훑기 ≡ `w.mtm_path`(확정 자본 · 상한 초과 횟수) — 같은 사건 순서."""
    risk = 0.03
    sw = x.sweep(_trades(), risk)
    path = w.mtm_path(_trades(), risk=risk)
    assert path.realized is not None
    assert sw.final_equity == pytest.approx(float(path.realized[-1]), abs=1e-12)
    assert sw.breaches == path.breaches
    assert sw.kept.all()
    # 진입 명목 ÷ 자본 = 리스크 × 크기 × 진입가 ÷ 손절폭(자본과 무관)
    assert sw.notional == pytest.approx([0.6, 0.6, 0.6, 3.0, 0.6])


def test_sweep_room_blocks_only_entries_that_exceed_it() -> None:
    risk = 0.03
    sw = x.sweep(_trades(), risk, lambda _ms: 1.0)
    # 둘째(0.6+0.6>1) · 셋째 · 넷째(3.0>1)가 막힌다 — 다섯째는 넷째가 안 들어가서 들어간다
    assert sw.kept.tolist() == [True, False, False, False, True]
    kept = [t for t, ok in zip(_trades(), sw.kept, strict=True) if ok]
    path = w.mtm_path(kept, risk=risk)
    assert path.realized is not None
    assert sw.final_equity == pytest.approx(float(path.realized[-1]), abs=1e-12)
    assert x.sweep(_trades(), risk, lambda _ms: 100.0).kept.all()


def test_daily_max_carries_open_positions_into_the_next_day() -> None:
    idx = _idx(5)
    sw = x.sweep(_trades(), 0.03)
    dm = x.daily_max(sw.event_time, sw.event_ratio, idx)
    assert dm.iloc[0] > 1.7  # 첫날 세 거래가 겹친다(0.6 × 3)
    # 넷째 거래는 둘째 날 열려 넷째 날 닫힌다 — 사건이 없는 셋째 날에도 열려 있다
    assert dm.iloc[2] == pytest.approx(dm.iloc[1], rel=0.2) and dm.iloc[2] > 2.5
    assert dm.iloc[4] == 0.0
    assert x.daily_max(np.array([], dtype=np.int64), np.array([]), idx).sum() == 0.0


def test_carry_gross_is_two_legs_after_funding_starts() -> None:
    f = _fund()  # 첫 정산 = 둘째 날
    g = x.carry_gross(f, _idx(4), 0.5)
    assert g.tolist() == [0.0, 1.0, 1.0, 1.0]
    assert x.carry_gross(f, _idx(4), 0.0).sum() == 0.0


def _panel(fr: float, ys: list[float], cagr: float) -> x.Panel:
    yr = pd.Series(ys, index=[2018, 2019])
    return x.Panel(
        fr, fc.SERIES_TRAILING, e.Variant("p", 1.0, 1.0, 0.35, "2021-05-19", yr, yr), cagr
    )


def test_carry_share_is_a_plain_difference() -> None:
    diff, d = x.carry_share(_panel(0.5, [0.12, 0.30], 0.20), _panel(0.0, [0.10, 0.25], 0.18))
    assert diff.tolist() == pytest.approx([0.02, 0.05]) and d == pytest.approx(0.02)


# ───────────── 공개 표 검산(커밋된 CSV끼리 — 실데이터 없이 CI에서 돈다) ─────────────


def _panels() -> pd.DataFrame:
    return pd.read_csv(x.PANELS_CSV)


def _pick(frame: pd.DataFrame, group: str, fr: float, series: str) -> pd.Series:
    hit = frame[(frame["group"] == group) & (frame["fraction"] == fr) & (frame["series"] == series)]
    assert len(hit) == 1
    return hit.iloc[0]


def test_published_zero_carry_panel_is_the_wan449_main_variant() -> None:
    """완료기준 3 — 캐리 0% · 실제 ≡ WAN-449 정본(공개 일별 CSV의 해마다 수익)."""
    row = _pick(_panels(), "판", 0.0, fc.SERIES_ACTUAL)
    eq = pd.read_csv(e.DAILY_CSV, index_col=0, parse_dates=True)["combo_eq"]
    for y, v in e.yearly(eq).items():
        assert row[f"y{y}"] == pytest.approx(float(v), abs=1e-9)
    assert row["lambda"] == pytest.approx(0.885, abs=5e-4) and row["k"] == pytest.approx(
        1.590, abs=5e-4
    )


def test_published_six_panels_and_verdict() -> None:
    frame = _panels()
    main = frame[frame["group"] == "판"]
    assert len(main) == 6
    assert (main["mdd"] - 0.35).abs().max() < 1e-3  # 판마다 MDD 35% 맞춤
    judge = _pick(frame, "판", x.VERDICT_FRACTION, fc.SERIES_TRAILING)
    ys = pd.Series({y: judge[f"y{y}"] for y in e.YEARS})
    ok, med, neg = e.verdict(ys)
    assert bool(judge["verdict_ok"]) is ok
    assert med == pytest.approx(judge["median_2018_2025"]) and neg == judge["negative_years"]
    # 캐리 0%는 펀딩 시나리오와 무관하다
    a, b = (_pick(frame, "판", 0.0, s) for s in x.SERIES)
    assert a["cagr"] == b["cagr"]
    # 같은 캐리 비율이면 최근 12개월 고정이 실제 펀딩보다 작게 더한다(이 이슈의 답의 방향)
    for fr in (0.5, 1.0):
        act, trail = (_pick(frame, "판", fr, s) for s in x.SERIES)
        assert a["cagr"] < trail["cagr"] < act["cagr"]


def test_published_caps_block_nothing_when_the_peak_is_below_the_cap() -> None:
    """막힌 진입 0건은 「총 명목 최대 < 상한」일 때만이다(WAN-367 — 0의 이유를 값으로)."""
    caps = pd.read_csv(x.MARGIN_CAPS_CSV)
    daily = pd.read_csv(x.MARGIN_DAILY_CSV, index_col=0)
    assert len(caps) == len(x.CAPS) * len(x.FRACTIONS)
    for r in caps.to_dict("records"):
        peak = float(daily[f"total_f{r['fraction']:.1f}"].max())
        assert (r["blocked"] == 0) == (peak <= r["cap"])
    assert (caps["blocked"] > 0).any()  # 적어도 한 칸은 실제로 막힌다(팔이 동작했다)
