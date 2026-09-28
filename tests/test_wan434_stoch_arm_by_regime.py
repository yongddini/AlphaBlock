"""WAN-434: 월봉 장세 라벨 · 판정 상수 · 검산 · 몰림 · 거래 내역을 동작으로 고정한다.

지키는 것:

1. **축을 새로 만들지 않았다** — 보유 봉·TF·종목·부호 자·하한 쌍이 WAN-424/432/433의 **그 객체**다.
2. **장세 구간과 판정 상수를 결과를 보고 옮기지 않는다**(WAN-161) — 날짜·우선순위가 이슈 표 그대로.
3. **라벨이 실제로 거래를 가른다** — 조정은 상승장을 이기고, 창 밖 시각은 조용히 채우지 않고 죽는다.
4. **버킷이 거래를 잃지도 겹치지도 않는다** — 합치면 한 덩어리와 같고, 검산이 그 등식을 건다.
5. **자본 경로가 `wan424._equity_path`와 같은 식·같은 순서**다(§F 검산의 전제).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest import wan424_stoch_ob_arm as w424
from backtest import wan432_stoch_floor_robustness as w432
from backtest import wan433_stoch_floor_by_year as w433
from backtest import wan434_stoch_arm_by_regime as m


def _ms(y: int, mo: int, d: int, h: int = 0) -> int:
    return int(dt.datetime(y, mo, d, h, tzinfo=dt.UTC).timestamp() * 1000)


def _rec(
    *,
    entry: int,
    exit_: int | None = None,
    net: float = 0.1,
    gross: float | None = None,
    segment: str = "full",
    symbol: str = "BTC/USDT:USDT",
) -> m.TradeRec:
    p = m.label_period(entry)
    g = net + 0.02 if gross is None else gross
    return m.TradeRec(
        segment=segment,
        symbol=symbol,
        timeframe="4h",
        entry_time=entry,
        exit_time=entry + 3_600_000 if exit_ is None else exit_,
        entry_price=100.0,
        stop_price=95.0,
        exit_price=101.0,
        exit_reason="시간청산",
        stop_width=0.05,
        k_at_entry=10.0,
        net_r=net,
        gross_r=g,
        cost_r=g - net,
        residual_r=0.0,
        win=net > 0,
        period=p.key,
        regime=p.regime,
        back_half=False,
    )


def _row(regime: str, *, net: float, se: float, gross: float, gse: float) -> m.RegimeRow:
    return m.RegimeRow(
        hold=4,
        floor=0.04,
        threshold=25.0,
        segment="full",
        level=m.LEVEL_REGIME,
        bucket=regime,
        num_trades=100,
        win_rate=0.5,
        mean_net_r=net,
        se_net_r=se,
        sum_net_r=net * 100,
        mean_gross_r=gross,
        se_gross_r=gse,
        mean_cost_r=gross - net,
        median_stop_width=0.05,
        identity_max_abs=0.0,
    )


# --- 1. 축 ------------------------------------------------------------------------------


def test_axes_are_inherited_not_redefined() -> None:
    assert m.HOLD_BARS is w424.HOLD_BARS
    assert m.TIMEFRAMES is w424.TIMEFRAMES
    assert m.SYMBOLS is w424.SYMBOLS
    assert m.SIGN_SIGMA is w432.SIGN_SIGMA
    assert m.CHECKSUM_TOL is w432.CHECKSUM_TOL
    assert m.ADOPTED_FLOOR is w432.ADOPTED_FLOOR
    assert m.LOOSE_FLOOR is w433.LOOSE_FLOOR
    assert m.GRID_FLOORS == (0.01, 0.04)
    assert m.CLUSTER_COMBO == (4, 0.04, 25.0)
    assert set(m.TRADE_EXPORT_COMBOS) == {(4, 0.04, 25.0), (4, 0.04, 15.0)}


# --- 2. 장세 구간은 이슈 표 그대로 ------------------------------------------------------


def test_periods_are_the_issue_table() -> None:
    """🚨 결과를 보고 옮기지 않는다 — 날짜 하나가 바뀌면 판정의 뜻이 바뀐다."""
    got = {p.key: (p.regime, p.start_ms, p.end_ms) for p in m.PERIODS}
    assert got == {
        "bull_1": ("bull", _ms(2020, 9, 15), _ms(2021, 11, 10)),
        "correction_1": ("correction", _ms(2021, 4, 14), _ms(2021, 7, 20)),
        "bear_1": ("bear", _ms(2021, 11, 10), _ms(2022, 11, 21)),
        "bull_2": ("bull", _ms(2022, 11, 21), _ms(2025, 10, 6)),
        "correction_2": ("correction", _ms(2024, 3, 14), _ms(2024, 8, 5)),
        "correction_3": ("correction", _ms(2025, 1, 20), _ms(2025, 4, 7)),
        "bear_2": ("bear", _ms(2025, 10, 6), None),
    }
    assert (m.BEAR_FRONT, m.BEAR_BACK) == ("bear_1", "bear_2")


def test_correction_beats_bull_and_boundaries_are_half_open() -> None:
    """조정은 상승장 안에 겹치고 **조정이 이긴다** · 경계는 그날 00:00 UTC · `[시작, 끝)`."""
    assert m.label_period(_ms(2021, 5, 1)).key == "correction_1"
    assert m.label_period(_ms(2021, 4, 13, 23)).key == "bull_1"
    assert m.label_period(_ms(2021, 7, 20)).key == "bull_1"
    assert m.label_period(_ms(2021, 11, 9, 23)).key == "bull_1"
    assert m.label_period(_ms(2021, 11, 10)).key == "bear_1"
    assert m.label_period(_ms(2022, 11, 21)).key == "bull_2"
    assert m.label_period(_ms(2024, 6, 1)).key == "correction_2"
    assert m.label_period(_ms(2025, 10, 6)).key == "bear_2"
    assert m.label_period(_ms(2030, 1, 1)).key == "bear_2"


def test_label_outside_window_dies_instead_of_inventing() -> None:
    """창 앞 시각을 조용히 `bull`로 채우면 라벨이 지어진다(WAN-367)."""
    with pytest.raises(ValueError):
        m.label_period(_ms(2020, 9, 14, 23))


# --- 3. 판정 상수 -----------------------------------------------------------------------


def test_verdict_regime_agnostic_wins_first() -> None:
    bull = _row("bull", net=0.1, se=0.02, gross=0.15, gse=0.02)
    bear = _row("bear", net=0.2, se=0.05, gross=0.25, gse=0.05)
    assert m.regime_verdict(bull, bear) == m.AGNOSTIC


def test_verdict_bear_bias_uses_gross_and_bull_not_decided() -> None:
    bull = _row("bull", net=0.01, se=0.05, gross=0.03, gse=0.05)
    bear = _row("bear", net=0.05, se=0.05, gross=0.2, gse=0.05)
    assert m.regime_verdict(bull, bear) == m.BEAR_BIAS
    assert m.regime_verdict(bear, bull) == m.BULL_BIAS


def test_verdict_closes_when_neither_regime_is_decided_positive() -> None:
    bull = _row("bull", net=-0.05, se=0.05, gross=-0.02, gse=0.05)
    bear = _row("bear", net=0.03, se=0.05, gross=0.05, gse=0.05)
    assert m.regime_verdict(bull, bear) == m.CLOSE_ARM
    assert m.regime_verdict(None, bear) == m.CLOSE_ARM


def test_verdict_both_gross_decided_but_one_net_not_is_outside_the_constants() -> None:
    """두 장세 다 gross 양수·결정인데 net이 한쪽만 결정 — 상수 1~4 어디에도 안 든다."""
    bull = _row("bull", net=0.15, se=0.05, gross=0.2, gse=0.05)
    bear = _row("bear", net=0.05, se=0.05, gross=0.2, gse=0.05)
    assert m.regime_verdict(bull, bear) == m.OTHER


def test_star_needs_both_bears_decided_in_the_same_direction() -> None:
    f = dataclasses.replace(_row("bear", net=0.2, se=0.05, gross=0.2, gse=0.05), bucket="bear_1")
    b = dataclasses.replace(f, bucket="bear_2")
    assert m.star_verdict(f, b)[0] is True
    undecided = dataclasses.replace(b, se_net_r=0.5)
    assert m.star_verdict(f, undecided)[0] is False
    flipped = dataclasses.replace(b, mean_net_r=-0.2)
    assert m.star_verdict(f, flipped)[0] is False
    assert m.star_verdict(f, None)[0] is False


# --- 4. 버킷은 거래를 잃지도 겹치지도 않는다 ----------------------------------------------


def _mixed_trades() -> dict[str, list[m.TradeRec]]:
    entries = [
        _ms(2020, 10, 1),
        _ms(2021, 5, 1),
        _ms(2022, 3, 1),
        _ms(2022, 6, 1),
        _ms(2023, 6, 1),
        _ms(2024, 6, 1),
        _ms(2025, 2, 1),
        _ms(2025, 12, 1),
        _ms(2026, 3, 1),
    ]
    nets = [0.3, -1.0, 0.5, -0.2, 0.1, 0.4, -1.0, 0.8, -0.3]
    return {"full": [_rec(entry=e, net=n) for e, n in zip(entries, nets, strict=True)]}


def test_labels_actually_split_the_trades() -> None:
    rows = m.bucket_rows(_mixed_trades(), hold=4, floor=0.04, threshold=25.0)
    regimes = {r.bucket: r.num_trades for r in rows if r.level == m.LEVEL_REGIME}
    periods = {r.bucket: r.num_trades for r in rows if r.level == m.LEVEL_PERIOD}
    assert regimes == {"bull": 2, "bear": 4, "correction": 3}
    assert periods["bear_1"] == 2 and periods["bear_2"] == 2
    assert sum(regimes.values()) == sum(periods.values()) == 9


def test_pooling_the_buckets_reproduces_the_whole() -> None:
    trades = _mixed_trades()
    rows = [
        r
        for r in m.bucket_rows(trades, hold=4, floor=0.04, threshold=25.0)
        if r.level == m.LEVEL_REGIME
    ]
    n = sum(r.num_trades for r in rows)
    pooled = sum(r.sum_net_r for r in rows) / n
    whole = sum(t.net_r for t in trades["full"]) / len(trades["full"])
    assert pooled == pytest.approx(whole, abs=1e-12)


def test_checksum_against_a_reference_csv(tmp_path: Path) -> None:
    trades = _mixed_trades()
    rows = m.bucket_rows(trades, hold=4, floor=0.04, threshold=25.0)
    recs = trades["full"]
    n = len(recs)
    ref = pd.DataFrame(
        [
            {
                "hold": 4,
                "floor": 0.04,
                "threshold": 25.0,
                "segment": "full",
                "num_trades": n,
                "mean_net_r": sum(r.net_r for r in recs) / n,
                "win_rate": sum(r.win for r in recs) / n,
                "gross_r": sum(r.gross_r for r in recs) / n,
                "cost_r": sum(r.cost_r for r in recs) / n,
            }
        ]
    )
    path = tmp_path / "ref.csv"
    ref.to_csv(path, index=False)
    lines, worst = m.checksum_wan432(rows, reference_csv=path)
    assert m.checksum_passes(worst), lines
    ref.loc[0, "num_trades"] = n + 1
    ref.to_csv(path, index=False)
    lines, worst = m.checksum_wan432(rows, reference_csv=path)
    assert not m.checksum_passes(worst)
    _lines, worst = m.checksum_wan432(rows, reference_csv=path, adopted=False)
    assert math.isnan(worst) and m.checksum_passes(worst)


# --- 5. 자본 경로 · §F ------------------------------------------------------------------


def test_equity_path_matches_wan424_formula_and_order() -> None:
    """같은 식 · 같은 순서(청산 시각 · 안정 정렬) — 같은 청산 시각은 원래 순서를 지킨다."""
    base = _ms(2022, 1, 1)
    recs = [
        _rec(entry=base, exit_=base + 5, net=1.5),
        _rec(entry=base + 1, exit_=base + 3, net=-1.0),
        _rec(entry=base + 2, exit_=base + 3, net=-0.5),
        _rec(entry=base + 3, exit_=base + 9, net=0.7),
    ]
    path = m.equity_path(recs)
    ordered = sorted(((r.exit_time, r.net_r) for r in recs), key=lambda x: x[0])
    ret, mdd, _ruined = w424._equity_path([r for _t, r in ordered], compound=False)
    assert path[-1][1] - 1.0 == pytest.approx(ret, abs=1e-15)
    assert max(d for _r, _e, d in path) == pytest.approx(mdd, abs=1e-15)
    assert [r.net_r for r, _e, _d in path] == [-1.0, -0.5, 1.5, 0.7]


def test_max_drawdown_span_finds_peak_and_trough() -> None:
    base = _ms(2022, 1, 1)
    recs = [
        _rec(entry=base, exit_=base + 1, net=2.0),
        _rec(entry=base, exit_=base + 2, net=-1.0, symbol="ETH/USDT:USDT"),
        _rec(entry=base, exit_=base + 3, net=-1.0, symbol="SOL/USDT:USDT"),
        _rec(entry=base, exit_=base + 4, net=3.0),
    ]
    span = m.max_drawdown_span(m.equity_path(recs))
    assert span is not None
    assert (span.peak_time, span.trough_time) == (base + 1, base + 3)
    assert (span.trades, span.symbols) == (2, 2)
    assert span.mdd == pytest.approx(1.0 - 1.0 / 1.02)


def test_equity_checksum_reads_the_written_columns(tmp_path: Path) -> None:
    base = _ms(2022, 1, 1)
    recs = [_rec(entry=base, exit_=base + i, net=n) for i, n in enumerate([1.0, -2.0, 0.5])]
    frame = m.trades_frame({(4, 0.04, 25.0): recs, (4, 0.04, 15.0): recs[:2]})
    path = m.equity_path(recs)
    ref_rows = []
    for (hold, floor, thr), rs in (((4, 0.04, 25.0), recs), ((4, 0.04, 15.0), recs[:2])):
        p = m.equity_path(rs)
        ref_rows.append(
            {
                "scope": m.WAN430_SCOPE,
                "hold": hold,
                "floor": floor,
                "threshold": thr,
                "segment": "full",
                "num_trades": len(rs),
                "fixed_return": p[-1][1] - 1.0,
                "fixed_mdd": max(d for _r, _e, d in p),
            }
        )
    ref_path = tmp_path / "wan430.csv"
    pd.DataFrame(ref_rows).to_csv(ref_path, index=False)
    lines, worst = m.equity_checksum(frame, reference_csv=ref_path)
    assert m.checksum_passes(worst), lines
    assert path[-1][1] > 0
    broken = frame.copy()
    broken.loc[broken.index[-1], "누적자본(고정1%)"] += 0.01
    _lines, worst = m.equity_checksum(broken, reference_csv=ref_path)
    assert not m.checksum_passes(worst)


def test_trades_frame_has_the_issue_columns() -> None:
    recs = [_rec(entry=_ms(2022, 1, 1))]
    frame = m.trades_frame({(4, 0.04, 25.0): recs})
    for col in (
        "조합",
        "종목",
        "시간봉",
        "진입시각(UTC)",
        "청산시각(UTC)",
        "진입가",
        "손절가",
        "청산가",
        "청산사유",
        "손절폭(%)",
        "진입시%K",
        "gross_R",
        "비용_R",
        "net_R",
        "장세",
        "앞뒤",
        "누적자본(고정1%)",
        "고점대비낙폭",
    ):
        assert col in frame.columns


# --- 6. §E 몰림 -------------------------------------------------------------------------


def test_block_constants_are_pinned() -> None:
    assert m.BLOCK_MS == 86_400_000
    assert (m.BOOTSTRAP_DRAWS, m.BOOTSTRAP_SEED) == (2000, 434)


def test_concurrent_counts_treat_same_instant_exit_as_closed() -> None:
    base = _ms(2022, 1, 1)
    recs = [
        _rec(entry=base, exit_=base + 10),
        _rec(entry=base + 5, exit_=base + 20),
        _rec(entry=base + 10, exit_=base + 30),
    ]
    assert m.concurrent_counts(recs) == [1, 2, 2]


def test_block_bootstrap_widens_when_trades_cluster_in_a_day() -> None:
    """같은 날 같은 부호로 몰린 거래는 독립 증거가 아니다 — 블록 오차가 독립 오차보다 커야 한다."""
    recs = []
    for d in range(30):
        sign = 1.0 if d % 2 else -1.0
        for h in range(5):
            recs.append(_rec(entry=_ms(2022, 1, 1 + d, h), net=sign + 0.01 * h))
    row = m.cluster_row("full", recs)
    assert row.se_block > 1.5 * row.se_iid
    assert row.effective_n < row.num_trades / 2
    assert row.trades_per_day_max == 5 and row.days == 30
    assert m.block_bootstrap_se(recs) == m.block_bootstrap_se(recs)  # 시드 고정


def test_months_to_decide_is_not_invented_for_non_positive_mean() -> None:
    recs = [_rec(entry=_ms(2022, 1, d), net=-0.1 * d) for d in range(1, 20)]
    row = m.cluster_row("oos_warm", recs)
    assert math.isnan(row.months_to_decide_iid) and math.isnan(row.months_to_decide_block)


def test_render_summary_runs_on_synthetic_rows() -> None:
    trades = _mixed_trades()
    rows = m.bucket_rows(trades, hold=4, floor=0.04, threshold=25.0)
    clusters = [m.cluster_row("full", trades["full"])]
    text = m.render_summary(rows, clusters, adopted=False)
    assert "## 판정" in text and "월봉 장세 구간" in text
    assert harness.SEGMENT_FULL in text


def test_arm_cell_progress_is_display_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`progress=True`는 찍기만 한다 — 같은 순서·같은 값이다(WAN-434 옵트인)."""

    def fake(payload: tuple[str, str], min_width: float = 0.03) -> w424.ArmCell:
        return w424.ArmCell(payload[0], payload[1], {}, (), ())

    monkeypatch.setattr(w424, "_cell_arms", fake)
    payloads = [("BTC", "1h"), ("ETH", "4h")]
    quiet = w424.build_arm_cells(payloads, jobs=1)  # type: ignore[arg-type]
    loud = w424.build_arm_cells(payloads, jobs=1, progress=True)  # type: ignore[arg-type]
    assert quiet == loud
    out = capsys.readouterr().out
    assert "칸 1/2 (BTC 1h)" in out and "칸 2/2 (ETH 4h)" in out


def test_star_names_the_losing_direction() -> None:
    """두 하락장이 모두 **마이너스**로 결정되면 채택 자격이 아님을 문장이 밝힌다."""
    f = dataclasses.replace(_row("bear", net=-0.2, se=0.05, gross=-0.2, gse=0.05), bucket="bear_1")
    ok, line = m.star_verdict(f, dataclasses.replace(f, bucket="bear_2"))
    assert ok is True and "마이너스" in line and "채택 자격 아님" in line
