"""WAN-428 부록 — 스토캐스틱 팔의 `Zone Count` 캡 회귀 테스트.

이 표의 값어치는 둘이다: **팔이 WAN-424의 그 팔과 같다**는 것, 그리고 **캡이 라벨이 아니라
실제로 후보를 거른다**는 것. 그래서 라벨이 아니라 **동작**으로 건다.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backtest import wan428_zone_rank_stoch_arm as stoch
from backtest.wan424_stoch_ob_arm import WAN423_REFERENCE
from backtest.wan428_zone_rank_census import CellArchive
from backtest.wan428_zone_rank_stoch_arm import (
    CAP_NAMES,
    CAPS,
    COMBOS,
    PRIMARY_SEGMENT,
    RANK_SEGMENTS,
    SEGMENTS,
    CapRow,
    _ratio,
    cap_candidates,
    combo_label,
    render_summary,
)
from strategy.models import OrderBlock, OrderBlockDirection

_H = 3_600_000
_REAL_DB = Path("data/ohlcv.db")


def _ob(confirmed: int) -> OrderBlock:
    return OrderBlock(
        direction=OrderBlockDirection.BULLISH,
        top=101.0,
        bottom=99.0,
        start_time=max(0, confirmed - _H),
        confirmed_time=confirmed,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
    )


# --------------------------------------------------------------------------- #
# 캡이 **동작**한다 — 라벨이 아니다
# --------------------------------------------------------------------------- #


class _FakeCand:
    """후보의 최소 모양 — `cap_candidates`가 읽는 두 필드만 있다."""

    def __init__(self, zone_key: frozenset[int] | None, trigger_time: int) -> None:
        self.zone_key = zone_key
        self.trigger_time = trigger_time

    def __eq__(self, other: object) -> bool:  # 비교로 어느 후보가 남았는지 본다
        return (
            isinstance(other, _FakeCand)
            and self.zone_key == other.zone_key
            and self.trigger_time == other.trigger_time
        )

    def __hash__(self) -> int:
        return hash((self.zone_key, self.trigger_time))


@dataclasses.dataclass
class _FakePayload:
    symbol: str
    timeframe: str
    candidates: dict[str, tuple[_FakeCand, ...]]


def _fixture() -> tuple[list[_FakePayload], dict[tuple[str, str], CellArchive]]:
    # 존 셋: 1h·2h·3h 확정 → t=4h에서 순위는 3h(1위) · 2h(2위) · 1h(3위)다.
    archive = (_ob(1 * _H), _ob(2 * _H), _ob(3 * _H))
    cands = tuple(_FakeCand(frozenset({i}), 4 * _H) for i in range(3))
    payload = _FakePayload("BTCUSDT", "1h", {"full": cands, "is": cands})
    return [payload], {("BTCUSDT", "1h"): CellArchive("BTCUSDT", "1h", archive)}


@pytest.mark.parametrize(
    ("cap", "expected_indices"),
    [(1, {2}), (2, {1, 2}), (3, {0, 1, 2}), (5, {0, 1, 2}), (10, {0, 1, 2})],
)
def test_cap_keeps_exactly_the_top_n_by_recency(cap: int, expected_indices: set[int]) -> None:
    """🚨 캡이 **실제로** 후보를 거른다 — 그리고 남는 것은 **최신 확정순** 상위 N개다."""
    payloads, archives = _fixture()
    out = cap_candidates(payloads, archives, cap=cap, ranked_cache={})  # type: ignore[arg-type]
    kept = {next(iter(k)) for c in out[0].candidates["full"] if (k := c.zone_key) is not None}
    assert kept == expected_indices


def test_cap_none_returns_the_payloads_untouched() -> None:
    """무제한은 **원본 그대로** 돌려준다(비트 동일) — 사본을 만들면 「캡 없음」이 다른 판이 된다."""
    payloads, archives = _fixture()
    out = cap_candidates(payloads, archives, cap=None, ranked_cache={})  # type: ignore[arg-type]
    # `is`로 물으면 mypy가 타입이 다르다고 막으므로 **동일 객체 id**로 본다(사본을 만들면 깨진다).
    assert id(out[0]) == id(payloads[0])


def test_cap_ranks_at_the_tap_not_at_the_last_bar() -> None:
    """순위는 **탭 시각**에 매긴다 — 같은 후보가 시각에 따라 다른 캡에 걸린다.

    ⚠️ 이슈가 *「아카이브 전체를 잘라 두지 말 것」*이라 경고한 자리다: 한 번 잘라 두면
    「그때는 1위였는데 지금은 3위」인 존을 놓친다.
    """
    archive = (_ob(1 * _H), _ob(2 * _H), _ob(3 * _H))
    archives = {("BTCUSDT", "1h"): CellArchive("BTCUSDT", "1h", archive)}
    # 인덱스 0(1h 확정)은 t=1h30m에선 **1위**, t=4h에선 **3위**다.
    early = [_FakePayload("BTCUSDT", "1h", {"full": (_FakeCand(frozenset({0}), _H + 1_800_000),)})]
    late = [_FakePayload("BTCUSDT", "1h", {"full": (_FakeCand(frozenset({0}), 4 * _H),)})]
    kept_early = cap_candidates(early, archives, cap=1, ranked_cache={})  # type: ignore[arg-type]
    kept_late = cap_candidates(late, archives, cap=1, ranked_cache={})  # type: ignore[arg-type]
    assert len(kept_early[0].candidates["full"]) == 1  # 그 시점 1위라 남는다
    assert len(kept_late[0].candidates["full"]) == 0  # 그 시점 3위라 캡 1에 걸린다


def test_cap_skips_the_cold_cut_segment() -> None:
    """🚨 **실제 버그의 회귀** — `is`에 캡을 걸면 쓰레기가 나온다(실측 627 → 8거래).

    아카이브는 방향별로 **「최신 먼저」**(`bullish_obs.insert(0, …)` — 원본 `unshift` 이식)이고
    `is`는 창의 **앞부분**이라 빠지는 것이 **최신 존 = 리스트의 머리**다. 머리가 잘리면 남은
    전부가 상수만큼 밀린다(BTC 4h 실측: 같은 인덱스 = 같은 존인 칸 **0/244** · 오프셋이 정확히
    강세 `+62`·약세 `+119`). 그러면 `is` 후보가 엉뚱한 존의 순위를 받아 캡에 전멸한다.

    ⚠️ 이 테스트의 이전 판은 *「캡은 모든 구간에 걸린다」*를 **assert 했다** — 그 assertion이
    버그를 인코딩했고, 공개 표에 `is` 쓰레기 행을 실었다.
    """
    payloads, archives = _fixture()
    out = cap_candidates(payloads, archives, cap=1, ranked_cache={})  # type: ignore[arg-type]
    assert set(out[0].candidates) == {"full", "is"}
    assert len(out[0].candidates["full"]) == 1, "순위를 매길 수 있는 구간에는 캡이 걸린다"
    assert len(out[0].candidates["is"]) == 3, "🚨 `is`는 손대지 않는다(아카이브가 다른 판)"


def test_cap_leaves_the_cold_cut_alone_even_without_an_archive() -> None:
    """존 대장이 없는 칸에서도 `is`는 그대로다 — 비우면 그 구간이 「캡 걸린 판」으로 오해된다."""
    payloads, _ = _fixture()
    out = cap_candidates(payloads, {}, cap=1, ranked_cache={})  # type: ignore[arg-type]
    assert out[0].candidates["full"] == ()
    assert len(out[0].candidates["is"]) == 3


def test_cap_empties_a_rankable_cell_with_no_archive() -> None:
    """존 대장이 없는 칸의 **순위 대상 구간**은 비운다 — 통과시키면 그 칸만 캡이 안 걸린다."""
    payloads, _ = _fixture()
    out = cap_candidates(payloads, {}, cap=1, ranked_cache={})  # type: ignore[arg-type]
    assert out[0].candidates["full"] == ()


# --------------------------------------------------------------------------- #
# 팔이 WAN-424의 그 팔과 같다 — 호출 인자로 고정
# --------------------------------------------------------------------------- #


def test_placement_args_match_wan424_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """🚨 배치 인자가 갈라지면 **다른 팔을 쟀다**가 된다 — 호출 인자 전체를 대조한다."""
    import backtest.wan424_stoch_ob_arm as wan424

    calls: list[dict[str, object]] = []

    def _spy(*_args: object, **kwargs: object) -> list[object]:
        calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(wan424, "iter_book_segments", _spy)
    monkeypatch.setattr(stoch, "iter_book_segments", _spy)

    wan424.place([], hold=4, floor=0.04, threshold=25.0)
    stoch.place_arm_segments([])

    assert len(calls) == 2
    assert calls[0] == calls[1], f"배치 인자가 갈렸습니다: {calls[0]} != {calls[1]}"


def test_segments_match_wan424_and_ranking_drops_the_cold_cut() -> None:
    """배치는 세 구간 · 순위는 두 구간 — `is`는 다시 탐지한 판이라 인덱스가 다르다."""
    import backtest.wan424_stoch_ob_arm as wan424

    assert SEGMENTS == wan424.SEGMENTS
    assert set(RANK_SEGMENTS) < set(SEGMENTS)
    assert "is" not in RANK_SEGMENTS
    assert PRIMARY_SEGMENT in RANK_SEGMENTS


def test_combos_come_from_the_pinned_reference_not_a_new_grid() -> None:
    """격자를 새로 뒤지지 않는다 — WAN-424가 기준값으로 못 박은 칸뿐이다(WAN-161)."""
    assert set(COMBOS) == set(WAN423_REFERENCE)
    assert len(COMBOS) == 2


def test_caps_are_the_original_indicator_values() -> None:
    """캡 값은 원본 지표의 `Zone Count` 네 값 + 무제한이다 — 결과를 보고 고른 값이 아니다."""
    assert CAPS == (None, 10, 5, 3, 1)
    pine = Path("strategy/reference/fluxchart_volumized_ob.pine").read_text(encoding="utf-8")
    assert (
        'zoneCount == "One" ? 1 : zoneCount == "Low" ? 3 : zoneCount == "Medium" ? 5 : 10' in pine
    )
    assert set(CAP_NAMES) == set(CAPS)


# --------------------------------------------------------------------------- #
# 표시 가드 — 숫자는 맞는데 읽으면 틀리는 자리
# --------------------------------------------------------------------------- #


def test_ratio_refuses_a_near_zero_mdd() -> None:
    """낙폭이 거의 없는 칸에서 `수익/MDD`가 폭발한다 — 내지 않는다(WAN-115/330/395)."""
    assert _ratio(0.5, 0.25) == "2.00"
    assert _ratio(0.5, 0.004) == "—"
    assert _ratio(0.5, float("nan")) == "—"
    assert _ratio(float("nan"), 0.25) == "—"


def _cap_row(cap: int, name: str, *, trades: int, mdd: float, ret: float) -> CapRow:
    return CapRow(
        combo=combo_label(COMBOS[0]),
        cap=cap,
        cap_name=name,
        segment=PRIMARY_SEGMENT,
        num_trades=trades,
        mean_net_r=0.18,
        se_net_r=0.05,
        win_rate=0.55,
        compound_return=ret,
        compound_mdd=mdd,
        compound_ruined=False,
        fixed_return=ret,
        fixed_mdd=mdd,
    )


def test_summary_headline_reads_mdd_and_return_together() -> None:
    """★ 판정 표가 **거래 수·수익·MDD·수익/MDD를 한 줄에** 낸다 — MDD만 보면 WAN-378 착시다."""
    caps = pd.DataFrame(
        [
            _cap_row(0, "무제한(오늘)", trades=679, mdd=0.328, ret=1.12).model_dump(),
            _cap_row(3, "Low(3) = 원본 기본", trades=554, mdd=0.225, ret=1.01).model_dump(),
        ]
    )
    checks = pd.DataFrame(
        columns=["check", "combo", "segment", "metric", "left", "right", "abs_diff"]
    )
    body = render_summary(pd.DataFrame(), checks, caps=caps)
    assert "수익/MDD" in body
    assert "679" in body and "554" in body  # 거래 수를 반드시 같이 낸다
    assert "3.41" in body or "3.40" in body  # 1.12 / 0.328
    assert "4.49" in body or "4.48" in body  # 1.01 / 0.225
    assert "WAN-378" in body  # 착시 경고가 본문에 있다


def test_summary_says_the_arm_is_not_adopted_and_has_no_15m() -> None:
    """🚨 이 팔은 채택이 아니고 **15m이 없다** — 그 두 문장이 본문에서 사라지면 안 된다."""
    checks = pd.DataFrame(
        columns=["check", "combo", "segment", "metric", "left", "right", "abs_diff"]
    )
    body = render_summary(pd.DataFrame(), checks)
    assert "채택이 아니다" in body
    assert "15m" in body
    assert "셀 비교 금지" in body


def test_summary_renders_nan_as_dash() -> None:
    checks = pd.DataFrame(
        columns=["check", "combo", "segment", "metric", "left", "right", "abs_diff"]
    )
    caps = pd.DataFrame(
        [_cap_row(0, "무제한(오늘)", trades=1, mdd=float("nan"), ret=float("nan")).model_dump()]
    )
    body = render_summary(pd.DataFrame(), checks, caps=caps)
    assert "nan" not in body.lower()


# --------------------------------------------------------------------------- #
# 적재된 표 — 기준값이 실제로 재현됐나(있을 때만)
# --------------------------------------------------------------------------- #


def test_published_checksum_rows_all_pass() -> None:
    if not stoch.CHECKSUM_CSV.exists():
        pytest.skip("적재된 검산 CSV가 없어 건너뜁니다.")
    frame = pd.read_csv(stoch.CHECKSUM_CSV)
    bad = frame[(frame.abs_diff > 1e-9) & (~frame.metric.str.contains("skipped"))]
    assert bad.empty, bad.to_string()


def test_published_cap_table_covers_every_cap_and_combo() -> None:
    if not stoch.CAP_CSV.exists():
        pytest.skip("적재된 캡 CSV가 없어 건너뜁니다.")
    frame = pd.read_csv(stoch.CAP_CSV)
    assert set(frame.cap.unique()) == {c or 0 for c in CAPS}
    assert set(frame.combo.unique()) == {combo_label(c) for c in COMBOS}
    # 🚨 `is` 행이 없어야 한다 — 캡을 안 걸었으므로 그 행은 뜻이 없다.
    assert set(frame.segment.unique()) == set(RANK_SEGMENTS)
    # 무제한이 캡보다 거래가 많거나 같다 — 캡은 **거르는** 축이다.
    for combo in frame.combo.unique():
        for segment in frame.segment.unique():
            sub = frame[(frame.combo == combo) & (frame.segment == segment)]
            unlimited = int(sub[sub.cap == 0].num_trades.iloc[0])
            for _, row in sub[sub.cap > 0].iterrows():
                assert int(row.num_trades) <= unlimited, (combo, segment, row.cap_name)


# --------------------------------------------------------------------------- #
# (d) 캡 감소율 ↔ 순위 몫 — 판정이 아니라 용량 포화 여부의 지문
# --------------------------------------------------------------------------- #


def _cap_row_n(cap: int, trades: int) -> CapRow:
    return CapRow(
        combo=combo_label(COMBOS[0]),
        cap=cap,
        cap_name=CAP_NAMES[cap or None],
        segment=PRIMARY_SEGMENT,
        num_trades=trades,
        mean_net_r=0.1,
        se_net_r=0.05,
        win_rate=0.55,
        compound_return=1.0,
        compound_mdd=0.3,
        compound_ruined=False,
        fixed_return=1.0,
        fixed_mdd=0.3,
    )


def _ranked_trades(ranks: list[int]) -> dict[str, dict[str, list[Any]]]:
    from backtest.wan428_zone_rank_census import TradeRank as TR

    return {
        combo_label(COMBOS[0]): {
            PRIMARY_SEGMENT: [
                TR(
                    arm=combo_label(COMBOS[0]),
                    segment=PRIMARY_SEGMENT,
                    symbol="BTCUSDT",
                    timeframe="4h",
                    trigger_time=_H,
                    entry_time=_H,
                    net_r=0.1,
                    is_stop=False,
                    is_reentry=False,
                    tap_index=0,
                    archive_index=0,
                    rank=r,
                    rank_bar_close=r,
                    alive_zones=20,
                    zone_age_ms=_H,
                )
                for r in ranks
            ]
        }
    }


def test_cap_share_observation_is_zero_when_capacity_is_not_binding() -> None:
    """용량에 안 걸린 팔에서는 `실제 감소율 == 순위 몫`이 **정확히** 성립한다(실측 8/8)."""
    trades = _ranked_trades([1, 1, 2, 3, 4, 7])  # 순위 >3 이 2/6
    caps = [_cap_row_n(0, 6), _cap_row_n(3, 4)]  # 6 → 4 = 감소 2/6
    rows = stoch.checksum_rows(trades, {}, reference={}, caps=caps, full_coordinates=False)
    obs = [r for r in rows if "관측" in r.metric and r.combo.endswith("원본 기본")]
    assert obs, "(d) 행이 안 나왔습니다."
    assert obs[0].abs_diff == pytest.approx(0.0)


def test_cap_share_observation_is_nonzero_when_the_book_re_places() -> None:
    """🚨 0이 아니면 **틀린 게 아니라 재배치가 일어났다**는 뜻이다 — 그래서 실패로 세지 않는다."""
    trades = _ranked_trades([1, 1, 2, 3, 4, 7])  # 순위 >3 이 2/6 = 33.3%
    caps = [_cap_row_n(0, 6), _cap_row_n(3, 5)]  # 빈 슬롯을 하나 채워 5건 남음
    rows = stoch.checksum_rows(trades, {}, reference={}, caps=caps, full_coordinates=False)
    obs = [r for r in rows if "관측" in r.metric and r.combo.endswith("원본 기본")]
    assert obs[0].abs_diff > 0.1
    # 그리고 그 행은 「관측」이라 종료 코드 계산에서 빠진다.
    assert "관측" in obs[0].metric


def test_cap_share_observation_counts_unranked_as_removed() -> None:
    """순위를 못 잰 후보는 캡에 **걸리는 쪽**으로 센다 — 그게 `cap_candidates`의 동작이다."""
    trades = _ranked_trades([1, 2])
    trades[combo_label(COMBOS[0])][PRIMARY_SEGMENT][1] = dataclasses.replace(
        trades[combo_label(COMBOS[0])][PRIMARY_SEGMENT][1], rank=None
    )
    caps = [_cap_row_n(0, 2), _cap_row_n(3, 1)]
    rows = stoch.checksum_rows(trades, {}, reference={}, caps=caps, full_coordinates=False)
    obs = [r for r in rows if "관측" in r.metric and r.combo.endswith("원본 기본")]
    assert obs[0].right == pytest.approx(0.5)  # 순위 없는 1건이 「제거될 것」으로 센다
