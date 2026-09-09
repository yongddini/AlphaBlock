"""WAN-412 — 방향 팔(롱 온리 · 숏만 · 롱+숏) 격자의 회귀 테스트.

이 파일이 지키는 것은 **라벨이 아니라 동작**이다(WAN-91/95/112/123/159가 반복해 경계한
자리). 이 모듈은 **같은 payload를 세 번 배치**해 팔을 만드는 구조라, 「숏만」이 이름만
그렇고 조용히 롱을 섞어 도는 실패가 특히 쉽다 — 그래서 축이 실제로 걸리는지를 (1) 후보를
판 뒤 남은 방향, (2) 배치된 거래의 방향, (3) 숏 후보의 **기하**로 삼중으로 고정한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backtest import btc_day_regime as regime_mod
from backtest import harness
from backtest import wan412_short_direction_book as wan412
from backtest.models import ExitReason, PositionSide
from backtest.wan169_leverage_book import CellPayload
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE
from backtest.wan388_merge_x_retap import NOISE_R
from backtest.zone_limit_backtest import _Candidate

_REAL_DB = Path("data/ohlcv.db")


# --------------------------------------------------------------------------- #
# 팔 — 격자가 방향 셋인가
# --------------------------------------------------------------------------- #


def test_grid_is_the_three_direction_arms() -> None:
    assert {a.name for a in wan412.ARMS} == {"long_only", "short_only", "both"}
    assert wan412.ARM_LONG_ONLY.sides == (PositionSide.LONG,)
    assert wan412.ARM_SHORT_ONLY.sides == (PositionSide.SHORT,)
    assert set(wan412.ARM_BOTH.sides) == {PositionSide.LONG, PositionSide.SHORT}


def test_exactly_one_arm_is_the_adopted_book() -> None:
    """🚨 검산 (a)의 기준이 하나여야 한다 — 둘이면 「채택 북」이 두 개인 표가 된다."""
    adopted = [a for a in wan412.ARMS if a.is_adopted]
    assert [a.name for a in adopted] == ["long_only"]


# --------------------------------------------------------------------------- #
# 후보를 방향으로 판다 — base와 **재진입 둘 다**
# --------------------------------------------------------------------------- #


def _cand(side: PositionSide, *, entry: float, stop: float, tp: float | None) -> _Candidate:
    return _Candidate(
        side=side,
        entry_time=1,
        entry_price=entry,
        exit_time=2,
        exit_price=entry,
        reason=ExitReason.TAKE_PROFIT,
        stop_price=stop,
        take_profit_price=tp,
    )


def _payload(*, base: tuple[_Candidate, ...], reentry: tuple[_Candidate, ...]) -> CellPayload:
    return CellPayload(
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        boundary_ms=0,
        candidates={harness.SEGMENT_FULL: base},
        funding={harness.SEGMENT_FULL: ()},
        rows=(),
        reentry_candidates={harness.SEGMENT_FULL: reentry},
    )


_LONG = _cand(PositionSide.LONG, entry=100.0, stop=98.0, tp=103.0)
_SHORT = _cand(PositionSide.SHORT, entry=100.0, stop=102.0, tp=97.0)


@pytest.mark.parametrize(
    ("arm", "want_base", "want_reentry"),
    [
        (wan412.ARM_LONG_ONLY, [PositionSide.LONG], [PositionSide.LONG]),
        (wan412.ARM_SHORT_ONLY, [PositionSide.SHORT], [PositionSide.SHORT]),
        (
            wan412.ARM_BOTH,
            [PositionSide.LONG, PositionSide.SHORT],
            [PositionSide.LONG, PositionSide.SHORT],
        ),
    ],
)
def test_scoped_cuts_both_base_and_reentry_candidates(
    arm: wan412.Arm, want_base: list[PositionSide], want_reentry: list[PositionSide]
) -> None:
    """🚨 재진입까지 판다 — 한쪽만 걸면 「숏만」 팔에 롱 재진입이 남는 잡종이 된다.

    개수만 보면 안 보이는 실패라(합계는 그럴듯하다) **남은 후보의 방향**으로 건다.
    """
    payload = _payload(base=(_LONG, _SHORT), reentry=(_LONG, _SHORT))
    (out,) = wan412.scoped([payload], arm)
    assert [c.side for c in out.candidates[harness.SEGMENT_FULL]] == want_base
    assert [c.side for c in out.reentry_candidates[harness.SEGMENT_FULL]] == want_reentry


def test_scoped_keeps_the_coordinate_untouched() -> None:
    """방향은 **배치 축**이지 좌표가 아니다 — 경계·펀딩·격리 행을 건드리면 팔이 못 비교된다."""
    payload = _payload(base=(_LONG, _SHORT), reentry=())
    (out,) = wan412.scoped([payload], wan412.ARM_SHORT_ONLY)
    assert out.boundary_ms == payload.boundary_ms
    assert out.funding == payload.funding
    assert out.rows == payload.rows


# --------------------------------------------------------------------------- #
# 배선 — 축·회계가 실제로 넘어가나
# --------------------------------------------------------------------------- #


def test_cell_kwargs_name_the_adopted_take_profit_liquidity() -> None:
    """🚨 잊으면 옛 회계(익절 테이커)로 도는데 라벨은 「채택」이다(WAN-370/373)."""
    kwargs = wan412._cell_kwargs()
    assert kwargs["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert kwargs["reentry"] is True  # 후보에 **항상** 실어 두고 방향으로만 판다


@pytest.mark.parametrize("short_enabled", [True, False])
def test_build_payloads_forwards_the_short_axis_and_adopted_accounting(
    monkeypatch: pytest.MonkeyPatch, short_enabled: bool
) -> None:
    """축이 후보 생성에 도달하지 않으면 세 팔이 **같은 롱 후보**를 나눠 쓴다."""
    seen: dict[str, Any] = {}

    def fake_run_cells(symbols: Any, timeframes: Any, **kwargs: Any) -> list[CellPayload]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(wan412, "run_cells", fake_run_cells)
    wan412.build_payloads(
        ["BTC/USDT:USDT"],
        ["1h"],
        short_enabled=short_enabled,
        start="2024-01-01",
        end="2024-02-01",
        jobs=1,
    )
    assert seen["short_enabled"] is short_enabled
    assert seen["combine_obs"] is ADOPTED_COMBINE_OBS
    assert seen["retap_mode"] == ADOPTED_RETAP_MODE
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert seen["reentry"] is True


def test_place_uses_the_adopted_book_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    """재진입은 **켜서** 배치한다 — `scoped()`가 방향으로만 팠으므로 끄면 채택 규칙이 아니다."""
    seen: dict[str, Any] = {}

    def fake_iter(payloads: Any, **kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(wan412, "iter_book_segments", fake_iter)
    wan412.place([], start_ms=0, end_ms=1, segments=["full"])
    assert seen["include_reentry"] is True
    assert seen["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    assert seen["min_stop_distance_fraction"] == ADOPTED_STOP_GUARD
    assert seen["compound_sizing"] is False  # 복리 총수익은 판정 자가 아니다(WAN-346)


# --------------------------------------------------------------------------- #
# 검산 — 라벨이 아니라 동작
# --------------------------------------------------------------------------- #


def _row(arm: wan412.Arm, **over: Any) -> wan412.ArmRow:
    base: dict[str, Any] = {
        "arm": arm.name,
        "label": arm.label,
        "combine_obs": ADOPTED_COMBINE_OBS,
        "retap_mode": ADOPTED_RETAP_MODE,
        "segment": PRIMARY_OOS,
        "adopted_arm": arm.is_adopted,
        "num_cells": 48,
        "num_symbols": 12,
        "num_trades": 1000,
        "win_rate": 0.4,
        "mean_net_r": -0.12,
        "gross_r": 0.0,
        "slippage_r": 0.02,
        "entry_fee_r": 0.03,
        "take_profit_fee_r": 0.01,
        "stop_fee_r": 0.04,
        "other_fee_r": 0.0,
        "funding_r": 0.01,
        "cost_r": 0.11,
        "identity_max_abs": 0.0,
        "stop_width_p50": 0.005,
        "stop_width_p90": 0.01,
        "entry_in_zone_p50": 0.3,
        "retap_trades": 100,
        "retap_trade_share": 0.1,
        "reentry_trades": 50,
        "zone_retap_and_reentry": 3,
        "total_return_flat": -0.5,
        "max_drawdown": 0.6,
        "return_over_mdd": None,
        "peak_concurrency": 14,
        "max_concurrent_risk": 0.11,
        "max_effective_concurrent_risk": 0.17,
        "liquidation_events": 0,
        "symbols_below_gate": 0,
        "min_symbol_trades": 30,
        "net_r_stderr": 0.004,
        "long_trades": 1000 if arm.keeps(PositionSide.LONG) else 0,
        "short_trades": 1000 if arm.keeps(PositionSide.SHORT) else 0,
        "long_win_rate": 0.4 if arm.keeps(PositionSide.LONG) else None,
        "short_win_rate": 0.4 if arm.keeps(PositionSide.SHORT) else None,
        "long_mean_net_r": -0.12 if arm.keeps(PositionSide.LONG) else None,
        "short_mean_net_r": -0.12 if arm.keeps(PositionSide.SHORT) else None,
        "long_net_r_sum": -120.0 if arm.keeps(PositionSide.LONG) else 0.0,
        "short_net_r_sum": -120.0 if arm.keeps(PositionSide.SHORT) else 0.0,
        "btc_return_corr": 0.54,
        "btc_days": 600,
    }
    base.update(over)
    return wan412.ArmRow(**base)


def test_direction_checksum_catches_a_mislabeled_arm() -> None:
    """「숏만」에 롱이 섞이면 **차가 0이 아니어야 한다** — 이 검산이 곧 팔의 자격 증명이다."""
    clean = [_row(wan412.ARM_SHORT_ONLY), _row(wan412.ARM_LONG_ONLY)]
    assert all(c.abs_diff == 0.0 for c in wan412.checksum_direction(clean))

    dirty = [_row(wan412.ARM_SHORT_ONLY, long_trades=7)]
    (row,) = wan412.checksum_direction(dirty)
    assert row.check == "b_short_only_has_no_long"
    assert row.abs_diff == 7.0


def test_short_geometry_checksum_is_mirrored_and_catches_a_flipped_candidate() -> None:
    """숏 라벨을 달고 손절이 진입 **아래**면 그건 「롱을 숏이라 부른 것」이다."""
    good = wan412.checksum_short_geometry([_payload(base=(_SHORT,), reentry=(_SHORT,))])
    by_metric = {c.metric: c for c in good}
    assert by_metric["short_candidates"].left == 2.0
    assert by_metric["stop_not_above_entry"].abs_diff == 0.0
    assert by_metric["take_profit_not_below_entry"].abs_diff == 0.0

    flipped = _cand(PositionSide.SHORT, entry=100.0, stop=98.0, tp=103.0)
    bad = {
        c.metric: c for c in wan412.checksum_short_geometry([_payload(base=(flipped,), reentry=())])
    }
    assert bad["stop_not_above_entry"].abs_diff == 1.0
    assert bad["take_profit_not_below_entry"].abs_diff == 1.0


# --------------------------------------------------------------------------- #
# 판정 줄 — 사람이 표를 보고 정하지 않는다
# --------------------------------------------------------------------------- #


def _verdict(
    base_net: float,
    both_net: float,
    *,
    base_corr: float,
    both_corr: float,
    stderr: float = 0.0005,
) -> str:
    """판정 줄. 🚨 `stderr` 기본값이 **작다** — 부호 결정 관문을 통과시켜 놓고 **노이즈선**을
    따로 시험하기 위해서다(두 관문을 한 픽스처에서 재면 어느 쪽이 걸렀는지 못 가른다)."""
    return wan412._verdict(
        [
            _row(
                wan412.ARM_LONG_ONLY,
                mean_net_r=base_net,
                btc_return_corr=base_corr,
                net_r_stderr=stderr,
            ),
            _row(
                wan412.ARM_BOTH,
                mean_net_r=both_net,
                btc_return_corr=both_corr,
                net_r_stderr=stderr,
            ),
        ]
    )


def test_verdict_reads_offset_only_when_both_axes_agree() -> None:
    assert "(가) 상쇄됨" in _verdict(-0.12, -0.10, base_corr=0.54, both_corr=0.20)
    # 🚨 net R은 좋아졌지만 방향 노출이 **더 커졌다**면 「상쇄」가 아니다.
    assert "(가) 상쇄됨" not in _verdict(-0.12, -0.10, base_corr=0.54, both_corr=0.70)


def test_verdict_calls_a_worse_book_the_mirror_image() -> None:
    assert "(나) 거울상" in _verdict(-0.12, -0.15, base_corr=0.54, both_corr=0.50)


def test_verdict_uses_the_inherited_noise_line_not_a_new_one() -> None:
    """판정선을 이 모듈이 새로 고르면 WAN-388/389/394 표와 **다른 자로** 읽게 된다."""
    assert NOISE_R == 0.005
    assert "(다) 무의" in _verdict(-0.12, -0.1160, base_corr=0.54, both_corr=0.20)
    assert "(가) 상쇄됨" in _verdict(-0.12, -0.1140, base_corr=0.54, both_corr=0.20)


def test_verdict_refuses_to_call_a_sign_it_cannot_decide() -> None:
    """🚨 **이 격자가 실제로 데인 자리다.** 노이즈선만 보면 오차보다 작은 차를 판정으로 찍는다.

    실측(2026-09-09): 5종목 20칸에서 **−0.0099R**이던 차가 12종목 48칸에서 **+0.0056R**로
    **부호가 뒤집혔는데**, 옛 판정은 둘 다 「판정」으로 찍었다(각각 (나)·(가)). 진짜 효과라면
    유니버스를 넓혔다고 부호가 뒤집히지 않는다. 아래 픽스처가 그 48칸 실측 그대로다.
    """
    line = _verdict(-0.1204, -0.1148, base_corr=0.444, both_corr=0.056, stderr=0.0105)
    assert "(다) 부호 미정" in line
    assert "(가) 상쇄됨" not in line
    # 같은 차라도 오차가 작으면 판정이 선다 — 관문이 **크기가 아니라 비(比)**를 본다.
    assert "(가) 상쇄됨" in _verdict(
        -0.1204, -0.1148, base_corr=0.444, both_corr=0.056, stderr=0.0005
    )


def test_sign_gate_combines_both_arms_uncertainty() -> None:
    """두 팔의 표준오차를 **합성**한다 — 한쪽만 보면 관문이 헐거워진다."""
    base = _row(wan412.ARM_LONG_ONLY, mean_net_r=-0.12, net_r_stderr=0.004)
    both = _row(wan412.ARM_BOTH, mean_net_r=-0.11, net_r_stderr=0.004)
    # 합성 σ = √(0.004² + 0.004²) ≈ 0.00566 → 2σ ≈ 0.0113 > 0.01이라 **부호 미정**이다.
    assert not wan412._sign_is_decided(0.01, base, both)
    # 한쪽 σ(0.004)만 봤다면 2σ = 0.008 < 0.01이라 통과했을 값이다.
    assert wan412._sign_is_decided(0.02, base, both)


# --------------------------------------------------------------------------- #
# BTC 방향 축
# --------------------------------------------------------------------------- #


def test_kst_day_boundary_is_utc_plus_nine() -> None:
    """WAN-172의 날 축 판 — UTC 15:00이 이미 다음 KST 날이다."""
    utc_1500 = pd.Timestamp("2024-08-09 15:00:00", tz="UTC").value // 1_000_000
    assert regime_mod.kst_day_of(utc_1500) == "2024-08-10"
    assert regime_mod.kst_day_of(utc_1500 - 60_000) == "2024-08-09"


def _regime(rows: dict[str, float]) -> regime_mod.DailyRegime:
    return regime_mod.DailyRegime(frame=pd.DataFrame({"ret": pd.Series(rows)}))


def test_quantiles_are_cut_over_traded_days_only() -> None:
    """🚨 전 기간 일봉으로 자르면 **거래가 없는 날이 경계를 움직인다**."""
    reg = _regime({f"2024-01-{d:02d}": (d - 5) / 100 for d in range(1, 11)})
    traded = pd.Series(["2024-01-01", "2024-01-02", "2024-01-09", "2024-01-10"])
    out = reg.quantiles_for(traded, q=2)
    assert sorted(out.index) == sorted(traded.unique())
    assert out.loc["2024-01-01", "quantile"] == 1
    assert out.loc["2024-01-10", "quantile"] == 2


def test_daily_correlation_is_day_level_not_trade_level() -> None:
    """🚨 거래 단위로 재면 거래가 많은 날(= 내린 날)이 상관을 통째로 지배한다.

    한 날에 거래를 100건 몰아 줘도 그 날의 무게가 **1일**이어야 한다 — 그래서 거래를
    복제해도 상관이 안 변한다.
    """
    reg = _regime({"2024-01-01": -0.03, "2024-01-02": 0.00, "2024-01-03": 0.03})
    day_ms = {
        "2024-01-01": pd.Timestamp("2024-01-01 03:00", tz="UTC").value // 1_000_000,
        "2024-01-02": pd.Timestamp("2024-01-02 03:00", tz="UTC").value // 1_000_000,
        "2024-01-03": pd.Timestamp("2024-01-03 03:00", tz="UTC").value // 1_000_000,
    }

    class _T:
        def __init__(self, ms: int) -> None:
            self.entry_time = ms

    pts = (("2024-01-01", -1.0), ("2024-01-02", 0.0), ("2024-01-03", 1.0))
    thin = [(_T(day_ms[d]), r) for d, r in pts]
    fat = thin + [(_T(day_ms["2024-01-01"]), -1.0)] * 99
    corr_thin, days_thin = wan412._daily_correlation(thin, reg)  # type: ignore[arg-type]
    corr_fat, days_fat = wan412._daily_correlation(fat, reg)  # type: ignore[arg-type]
    assert days_thin == days_fat == 3
    assert corr_thin is not None and corr_fat is not None
    assert corr_thin == pytest.approx(corr_fat)


# --------------------------------------------------------------------------- #
# 실데이터 — 숏 기하가 진짜 엔진에서도 뒤집혀 있나
# --------------------------------------------------------------------------- #


def _real_data_available() -> bool:
    if not _REAL_DB.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{_REAL_DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE symbol = ? AND timeframe = ?"
                " AND open_time >= ? AND open_time < ?",
                ("BTC/USDT:USDT", "4h", _REAL_START, _REAL_END),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return bool(row and row[0] > 100)


#: 못 박은 실데이터 창 — 창을 고정해야 재현된다(`--years N` 미끄러짐 교훈).
_REAL_START = 1_704_067_200_000  # 2024-01-01 UTC
_REAL_END = 1_719_792_000_000  # 2024-07-01 UTC


@pytest.mark.skipif(not _real_data_available(), reason="실데이터(data/ohlcv.db) 없음")
def test_short_arm_really_produces_mirrored_shorts_on_real_data() -> None:
    """🚨 스텁이 아니라 **진짜 엔진**이 숏을 내는지 — 그리고 그 기하가 뒤집혀 있는지.

    합성 테스트는 `scoped()`가 방향으로 판다는 것까지만 보증한다. 「`short_enabled=True`가
    실제로 숏 후보를 만드는가」는 엔진이 답할 문제이고, 안 만들면 `short_only` 팔이
    **거래 0건인 채로 조용히** 표에 오른다(WAN-378이 이름 붙인 부류).
    """
    payloads = wan412.build_payloads(
        ["BTC/USDT:USDT"],
        ["4h"],
        short_enabled=True,
        start="2024-01-01",
        end="2024-07-01",
        jobs=1,
        cold_segments=False,
    )
    shorts = [
        c
        for p in payloads
        for cands in p.candidates.values()
        for c in cands
        if c.side is PositionSide.SHORT
    ]
    assert shorts, "short_enabled=True인데 숏 후보가 하나도 없다 — 축이 안 걸렸다"
    checks = {c.metric: c for c in wan412.checksum_short_geometry(payloads)}
    assert checks["stop_not_above_entry"].abs_diff == 0.0
    assert checks["take_profit_not_below_entry"].abs_diff == 0.0
    # 그리고 롱도 여전히 나온다 — 숏 게이트가 롱을 죽이면 `both`가 `short_only`가 된다.
    longs = [
        c
        for p in payloads
        for cands in p.candidates.values()
        for c in cands
        if c.side is PositionSide.LONG
    ]
    assert longs


# --------------------------------------------------------------------------- #
# §1 분위 축은 팔 셋이 **공유**한다
# --------------------------------------------------------------------------- #


class _FakeSegment:
    """`BookSegment`의 §1이 쓰는 면만 흉내 낸다 — 거래·방향·진입 시각."""

    def __init__(self, segment: str, pairs: list[tuple[Any, Any]]) -> None:
        self.segment = segment
        self._pairs = pairs

    def trades_with_placements(self) -> list[tuple[Any, Any]]:
        return self._pairs


class _FakeTrade:
    def __init__(self, ms: int, side: PositionSide, net: float) -> None:
        self.entry_time = ms
        self.side = side
        self.realized_pnl = net


class _FakePlacement:
    risk_amount = 1.0


def _ms(day: str, hour: int = 3) -> int:
    return int(pd.Timestamp(f"{day} {hour:02d}:00", tz="UTC").value // 1_000_000)


def test_regime_axis_is_shared_across_arms() -> None:
    """🚨 팔마다 자기 거래일로 자르면 축이 갈린다 — 파일럿에서 실제로 그랬다.

    같은 버킷의 `days`·`btc_ret_mean`이 팔에 따라 달라지면 *「BTC 내린 날 숏이 몇 건
    하는가」*라는 이 이슈의 질문에 **답할 수 없다**(두 표가 다른 「내린 날」을 말한다).
    """
    days = {f"2024-01-{d:02d}": (d - 5) / 100 for d in range(1, 11)}
    reg = _regime(days)
    # 두 팔이 **정반대 날들**에서만 거래한다 — 옛 방식이면 축이 완전히 갈렸을 판이다.
    long_seg = _FakeSegment(
        PRIMARY_OOS,
        [
            (_FakeTrade(_ms(f"2024-01-{d:02d}"), PositionSide.LONG, -1.0), _FakePlacement())
            for d in (1, 2)
        ],
    )
    short_seg = _FakeSegment(
        PRIMARY_OOS,
        [
            (_FakeTrade(_ms(f"2024-01-{d:02d}"), PositionSide.SHORT, 1.0), _FakePlacement())
            for d in (9, 10)
        ],
    )
    placed = [(wan412.ARM_LONG_ONLY, long_seg), (wan412.ARM_SHORT_ONLY, short_seg)]
    buckets = wan412.regime_buckets(placed, reg)  # type: ignore[arg-type]

    rows = {
        (r.arm, r.quantile): r
        for arm, seg in placed
        for r in wan412.build_regime_rows(seg, arm=arm, buckets=buckets)  # type: ignore[arg-type]
        if r.direction == "all"
    }
    for q in range(1, regime_mod.NUM_QUANTILES + 1):
        left, right = rows[("long_only", q)], rows[("short_only", q)]
        assert left.days == right.days
        assert left.btc_ret_mean == pytest.approx(right.btc_ret_mean)
    # 그리고 「거래가 하나도 없는 버킷」이 사라지지 않는다 — 그게 이 이슈의 답이다.
    assert rows[("short_only", 1)].trades == 0
    assert rows[("long_only", 1)].trades > 0
    # `매매일`은 공유 축의 `일수`와 다른 열이다(그 팔이 실제로 거래한 날).
    assert rows[("long_only", 1)].traded_days <= rows[("long_only", 1)].days
