"""WAN-428 §1 — 존 순위 인구조사의 회귀 테스트.

이 표의 값어치는 **순위가 화면 규칙과 같은 자로 매겨졌다**는 것 하나에 달려 있다. 그래서
라벨이 아니라 **동작**으로 건다:

* 빠른 랭커(`rank_at`)와 `strategy.models.select_active`를 **실제로 부르는** 기준
  (`rank_at_via_select_active`)이 같은 값을 내는지(WAN-403 `pool_k` 부류 예방).
* `alive_at`의 성질(무효화는 살아 있다 · 소멸은 빠진다 · 방향은 따로)을 그대로 따르는지 —
  누가 그것을 「고치면」 이 테스트가 먼저 죽는다.
* 병합 존(`zone_key` 원소 2개+)을 **거부**하는지(라벨만 붙는 실행 방지).
* 검산 (c)가 **잘못된 인덱스에 실제로 반응**하는지(돌연변이 확인).
* 요약이 **상쇄된 분모로 큰 %를 찍지 않는지**(WAN-115/330/395 부호 함정).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan428_zone_rank_census import (
    PINE_MAX_ORDER_BLOCKS,
    RANK_BUCKETS,
    RENDER_LIMIT_LOW,
    SHARE_DENOMINATOR_GUARD,
    CellArchive,
    RankRow,
    TradeRank,
    _Ranked,
    _zone_index,
    below_render_limit,
    bucket_label,
    census_rows,
    checksum_rows,
    rank_at,
    rank_at_via_select_active,
    render_summary,
    share_is_meaningful,
    trades_frame,
    trades_from_frame,
    verdict_lines,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockParams

_H = 3_600_000
_REAL_DB = Path("data/ohlcv.db")


def _ob(
    *,
    confirmed: int,
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    break_time: int | None = None,
    swept_time: int | None = None,
    tapped_times: tuple[int, ...] = (),
    top: float = 101.0,
    bottom: float = 99.0,
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=max(0, confirmed - _H),
        confirmed_time=confirmed,
        ob_volume=1.0,
        ob_low_volume=0.5,
        ob_high_volume=0.5,
        breaker=break_time is not None,
        break_time=break_time,
        swept_time=swept_time,
        tapped_times=tapped_times,
    )


def _ranks(archive: list[OrderBlock], time_ms: int, **kwargs: int) -> list[int | None]:
    ranked = _Ranked.build(archive)
    return [rank_at(ranked, archive, i, time_ms, **kwargs) for i in range(len(archive))]


# --------------------------------------------------------------------------- #
# 순위 = `select_active`의 규칙 그대로
# --------------------------------------------------------------------------- #


def test_fast_ranker_equals_select_active_on_every_query_point() -> None:
    """🚨 이 등식이 이 모듈의 자격 증명이다 — 두 벌로 갈라지면 「같은 자」가 거짓이 된다."""
    archive = [
        _ob(confirmed=1 * _H),
        _ob(confirmed=3 * _H, break_time=9 * _H),
        _ob(confirmed=5 * _H, swept_time=8 * _H),
        _ob(confirmed=5 * _H, top=120.0, bottom=118.0),  # 동률(confirmed_time 같음)
        _ob(confirmed=7 * _H, direction=OrderBlockDirection.BEARISH),
        _ob(confirmed=2 * _H, direction=OrderBlockDirection.BEARISH, swept_time=6 * _H),
    ]
    ranked = _Ranked.build(archive)
    for step in range(0, 13):
        t = step * _H
        for index in range(len(archive)):
            assert rank_at(ranked, archive, index, t) == rank_at_via_select_active(
                archive, index, t
            ), f"index={index} t={t}"


def test_rank_is_newest_first_and_one_based() -> None:
    archive = [_ob(confirmed=1 * _H), _ob(confirmed=2 * _H), _ob(confirmed=3 * _H)]
    # t=4h: 셋 다 살아 있고 최신 확정순이면 3h가 1위다.
    assert _ranks(archive, 4 * _H) == [3, 2, 1]


def test_rank_is_counted_per_direction() -> None:
    """약세 존이 끼어도 롱 거래의 순위를 밀지 않는다.

    원본도 방향별로 센다(`bullishOrderBlocks`)."""
    archive = [
        _ob(confirmed=1 * _H),
        _ob(confirmed=2 * _H, direction=OrderBlockDirection.BEARISH),
        _ob(confirmed=3 * _H, direction=OrderBlockDirection.BEARISH),
        _ob(confirmed=4 * _H),
    ]
    assert _ranks(archive, 5 * _H) == [2, 2, 1, 1]


def test_broken_but_unswept_zone_still_competes() -> None:
    """`alive_at`은 무효화(`breaker`)를 소멸로 보지 않는다 — 원본이 breaker 박스를 계속 그린다.

    🚨 누가 「깨진 존은 빼야지」로 고치면 순위가 통째로 달라지고 이 테스트가 먼저 죽는다.
    """
    archive = [_ob(confirmed=1 * _H), _ob(confirmed=2 * _H, break_time=3 * _H)]
    assert _ranks(archive, 5 * _H) == [2, 1]


def test_swept_zone_drops_out_of_the_ranking() -> None:
    archive = [_ob(confirmed=1 * _H), _ob(confirmed=2 * _H, break_time=3 * _H, swept_time=4 * _H)]
    # t=3h30m: 아직 안 소멸 → 2개 경쟁 / t=5h: 소멸 → 1개.
    assert _ranks(archive, 3 * _H + 1_800_000) == [2, 1]
    assert _ranks(archive, 5 * _H) == [1, None]


def test_rank_is_none_when_target_is_not_alive_yet() -> None:
    """지어내지 않는다 — 확정 전이면 `None`이고 0도 1도 아니다(WAN-367)."""
    archive = [_ob(confirmed=5 * _H)]
    assert _ranks(archive, 1 * _H) == [None]


def test_confirm_delay_actually_shifts_the_rank() -> None:
    """민감도 축이 **동작**한다 — 같은 봉에서 확정된 경쟁 존이 지연을 주면 빠진다."""
    archive = [_ob(confirmed=1 * _H), _ob(confirmed=4 * _H)]
    tap = 4 * _H  # 경쟁 존이 확정된 **그 봉**에서의 탭.
    assert _ranks(archive, tap) == [2, 1]
    # 「확정 봉이 닫힌 뒤에야 그린다」면 4h 존은 아직 화면에 없다 → 1h 존이 1위.
    assert _ranks(archive, tap, confirm_delay_ms=_H) == [1, None]


# --------------------------------------------------------------------------- #
# 조인 가드
# --------------------------------------------------------------------------- #


def test_zone_index_rejects_a_merged_key() -> None:
    """병합 클러스터의 「몇 위」는 정의되지 않는다 — 조용히 통과시키면 라벨만 붙는다."""
    assert _zone_index(frozenset({7})) == 7
    assert _zone_index(None) is None
    with pytest.raises(ValueError, match="zone_key 원소가 2개"):
        _zone_index(frozenset({3, 9}))


# --------------------------------------------------------------------------- #
# 버킷 · 집계
# --------------------------------------------------------------------------- #


def test_buckets_cover_every_rank_and_mark_the_unmeasured() -> None:
    assert bucket_label(None) == "?"
    labels = {bucket_label(r) for r in range(1, 60)}
    assert labels == {name for name, _low, _high in RANK_BUCKETS}
    assert bucket_label(RENDER_LIMIT_LOW) == "3"
    assert bucket_label(RENDER_LIMIT_LOW + 1) == "4-5"
    assert bucket_label(11) == "11+"


def _trade(rank: int | None, net_r: float, *, tf: str = "1h", reentry: bool = False) -> TradeRank:
    return TradeRank(
        segment="oos_warm",
        symbol="BTCUSDT",
        timeframe=tf,
        trigger_time=1 * _H,
        entry_time=1 * _H,
        net_r=net_r,
        is_stop=net_r < 0,
        is_reentry=reentry,
        tap_index=0,
        archive_index=0,
        rank=rank,
        rank_bar_close=rank,
        alive_zones=10,
    )


def test_census_rows_shares_and_sums_close() -> None:
    trades = [_trade(1, -1.0), _trade(2, 1.5), _trade(4, -1.0), _trade(12, 1.5)]
    rows = [r for r in census_rows({"oos_warm": trades}) if r.scope == "all"]
    assert {r.rank_bucket for r in rows} == {"1", "2", "4-5", "11+"}
    assert sum(r.num_trades for r in rows) == len(trades)
    assert sum(r.trade_share for r in rows) == pytest.approx(1.0)
    assert sum(r.net_r_sum for r in rows) == pytest.approx(sum(t.net_r for t in trades))
    assert sum(r.abs_net_r_share for r in rows) == pytest.approx(1.0)


def test_census_rows_split_base_and_reentry() -> None:
    trades = [_trade(1, 1.0), _trade(5, -1.0, reentry=True)]
    rows = census_rows({"oos_warm": trades})
    base = [r for r in rows if r.scope == "base"]
    reentry = [r for r in rows if r.scope == "reentry"]
    assert [r.rank_bucket for r in base] == ["1"]
    assert [r.rank_bucket for r in reentry] == ["4-5"]


def test_below_render_limit_means_rank_four_and_beyond() -> None:
    trades = [_trade(1, 1.0), _trade(3, 1.0), _trade(4, -2.0), _trade(30, -3.0)]
    stats = below_render_limit(trades)
    assert stats["num_ranked"] == 4.0
    assert stats["num_beyond"] == 2.0  # 4위·30위
    assert stats["trade_share"] == pytest.approx(0.5)
    assert stats["net_r_sum"] == pytest.approx(-5.0)
    assert stats["net_r_total"] == pytest.approx(-3.0)


def test_below_render_limit_skips_unranked_trades() -> None:
    stats = below_render_limit([_trade(1, 1.0), _trade(None, -9.0)])
    assert stats["num_ranked"] == 1.0
    assert stats["net_r_total"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 분모 가드 — 숫자는 맞는데 읽으면 틀리는 자리
# --------------------------------------------------------------------------- #


def test_share_guard_rejects_a_cancelling_denominator() -> None:
    assert share_is_meaningful(-8.0, 10.0) is True
    assert share_is_meaningful(0.1, 10.0) is False  # 버킷들이 상쇄 → 비율이 폭발한다
    assert share_is_meaningful(0.0, 0.0) is False
    assert share_is_meaningful(float("nan"), 10.0) is False
    # 자가 상수라 경계가 정확히 그 값이다(결과를 보고 옮기지 못하게).
    assert share_is_meaningful(SHARE_DENOMINATOR_GUARD * 10.0, 10.0) is True


def _row(bucket: str, net_r_sum: float) -> RankRow:
    return RankRow(
        segment="oos_warm",
        scope="all",
        rank_bucket=bucket,
        num_trades=10,
        trade_share=0.25,
        mean_net_r=net_r_sum / 10,
        net_r_sum=net_r_sum,
        net_r_share=net_r_sum / 0.1,  # 🚨 상쇄된 분모의 CSV 값(그대로 찍으면 수천 %)
        abs_net_r_share=abs(net_r_sum) / 20.0,
        stop_rate=0.5,
        mean_alive_zones=20.0,
    )


def test_summary_does_not_print_an_exploded_share() -> None:
    """돌연변이 확인 — CSV의 `net_r_share`를 그냥 찍으면 `+5000%`류가 본문에 나온다."""
    census = pd.DataFrame(
        [_row("1", 10.0).model_dump(), _row("2", -9.9).model_dump()]
    )  # 합 0.1 · Σ|버킷| 19.9 → 가드가 막는다
    checks = pd.DataFrame(columns=["check", "segment", "metric", "left", "right", "abs_diff"])
    body = render_summary(census, checks)
    # 본문의 어떤 백분율도 |200%|를 넘지 않는다 — 넘으면 상쇄된 분모를 그대로 찍은 것이다.
    percents = [abs(float(m)) for m in re.findall(r"(-?\d+(?:\.\d+)?)%", body)]
    assert percents, "백분율이 하나도 없습니다 — 표가 비었습니다."
    assert max(percents) <= 200.0, f"상쇄된 분모로 큰 %를 찍었습니다: {max(percents)}"
    # 그 자리는 「—」다(조용히 0으로 위장하지 않는다).
    assert "| — |" in body


def test_summary_renders_nan_as_dash_not_nan() -> None:
    """`nan%`가 본문에 새면 옛 리포트가 겪은 표시 사고다(WAN-395 부수 수리)."""
    census = pd.DataFrame(
        [
            {
                **_row("1", -8.0).model_dump(),
                "mean_net_r": float("nan"),
                "mean_alive_zones": float("nan"),
            },
            _row("2", -12.0).model_dump(),
        ]
    )
    checks = pd.DataFrame(columns=["check", "segment", "metric", "left", "right", "abs_diff"])
    body = render_summary(census, checks)
    assert "nan" not in body.lower()


def test_summary_says_cold_segments_were_not_measured() -> None:
    """안 잰 것을 안 잰 것으로 적는다(WAN-194/367) — 이 표에는 차가운 절단이 없다."""
    checks = pd.DataFrame(columns=["check", "segment", "metric", "left", "right", "abs_diff"])
    body = render_summary(pd.DataFrame(), checks)
    assert "차가운 절단" in body and "안 쟀다" in body


# --------------------------------------------------------------------------- #
# 검산 (c) — 돌연변이 확인
# --------------------------------------------------------------------------- #


def _archives(tapped: tuple[int, ...]) -> dict[tuple[str, str], CellArchive]:
    return {
        ("BTCUSDT", "1h"): CellArchive(
            symbol="BTCUSDT", timeframe="1h", archive=(_ob(confirmed=0, tapped_times=tapped),)
        )
    }


def test_checksum_c_passes_when_the_tap_time_is_in_the_zone() -> None:
    trades = {"oos_warm": [_trade(1, 1.0)]}  # trigger_time = 1h
    rows = checksum_rows(trades, _archives((1 * _H,)), adopted_coordinates=False)
    mismatch = next(r for r in rows if r.metric == "num_mismatched")
    assert mismatch.abs_diff == 0.0


def test_checksum_c_fires_when_the_archive_index_is_wrong() -> None:
    """🚨 인덱스가 어긋나면 이 검산이 **실제로** 걸린다 — 안 걸리면 검산이 아니다."""
    trades = {"oos_warm": [_trade(1, 1.0)]}  # trigger_time = 1h
    rows = checksum_rows(trades, _archives((7 * _H,)), adopted_coordinates=False)
    mismatch = next(r for r in rows if r.metric == "num_mismatched")
    assert mismatch.abs_diff == 1.0


def test_checksum_c_skips_reentry_trades() -> None:
    """재진입의 `trigger_time`은 탭이 아니라 재무장 체결 시각이라 이 술어가 성립하지 않는다."""
    trades = {"oos_warm": [_trade(1, 1.0, reentry=True)]}
    rows = checksum_rows(trades, _archives((7 * _H,)), adopted_coordinates=False)
    mismatch = next(r for r in rows if r.metric == "num_mismatched")
    assert mismatch.abs_diff == 0.0


def test_checksum_b_counts_unranked_trades() -> None:
    trades = {"oos_warm": [_trade(1, 1.0), _trade(None, -1.0)]}
    rows = checksum_rows(trades, _archives((1 * _H,)), adopted_coordinates=False)
    unranked = next(r for r in rows if r.metric == "num_unranked")
    assert unranked.abs_diff == 1.0


def test_checksum_a_is_skipped_off_adopted_coordinates() -> None:
    """좁혀 돈 판을 공개 CSV와 대조하면 배선 오류처럼 보인다 — 조용히 통과시키지 않고 건너뛴다."""
    rows = checksum_rows(
        {"oos_warm": [_trade(1, 1.0)]}, _archives((1 * _H,)), adopted_coordinates=False
    )
    assert any(r.metric == "skipped_not_adopted_coordinates" for r in rows)


# --------------------------------------------------------------------------- #
# 실데이터 — 랭커가 진짜 아카이브에서도 `select_active`와 같은가(있을 때만)
# --------------------------------------------------------------------------- #


def test_real_archive_ranker_matches_select_active() -> None:
    if not _REAL_DB.exists():
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    market = harness.load_market_data(
        harness.normalize_symbol("BTCUSDT"),
        "4h",
        start_ms=parse_date_ms("2024-01-01"),
        end_ms=parse_date_ms("2024-04-01"),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    archive = harness.detect_order_blocks(market, OrderBlockParams()).order_blocks
    assert len(archive) > 5
    ranked = _Ranked.build(archive)
    # 실제 탭 시각에서만 묻는다 — 그것이 이 표가 쓰는 질의 점이다.
    queries = [(index, tap) for index, ob in enumerate(archive) for tap in ob.tapped_times[:3]]
    assert queries, "실데이터에 탭이 하나도 없습니다 — 창을 확인하십시오."
    for index, tap in queries[:200]:
        assert rank_at(ranked, archive, index, tap) == rank_at_via_select_active(
            archive, index, tap
        ), f"index={index} tap={tap}"


def test_real_archive_ranks_are_dense_from_one() -> None:
    """같은 시각의 순위는 1부터 빈틈없이 매겨진다 — 방향별 목록의 위치이기 때문이다."""
    if not _REAL_DB.exists():
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    market = harness.load_market_data(
        harness.normalize_symbol("BTCUSDT"),
        "4h",
        start_ms=parse_date_ms("2024-01-01"),
        end_ms=parse_date_ms("2024-04-01"),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        pytest.skip("실데이터가 없어 건너뜁니다(CI 기본).")
    archive = harness.detect_order_blocks(market, OrderBlockParams()).order_blocks
    ranked = _Ranked.build(archive)
    probe = archive[len(archive) // 2].confirmed_time + _H
    bullish = [
        rank_at(ranked, archive, i, probe)
        for i, ob in enumerate(archive)
        if ob.direction is OrderBlockDirection.BULLISH
    ]
    alive = sorted(r for r in bullish if r is not None)
    assert alive == list(range(1, len(alive) + 1))


# --------------------------------------------------------------------------- #
# 거래 단위 CSV 왕복 — `--from-csv`가 판정 줄을 복원한다
# --------------------------------------------------------------------------- #


def test_trades_csv_round_trip_preserves_unmeasured_ranks() -> None:
    """🚨 `NaN`을 `None`으로 되돌린다 — 안 그러면 「못 잰 거래」가 유효한 값으로 둔갑한다."""
    trades = [_trade(4, -1.0), _trade(None, 2.0, tf="4h", reentry=True)]
    frame = trades_frame({"oos_warm": trades})
    assert trades_from_frame(frame)["oos_warm"] == trades


def test_from_csv_verdict_needs_the_trades_file() -> None:
    """거래 원자료가 없으면 판정 줄을 **지어내지 않는다**(WAN-194/367)."""
    checks = pd.DataFrame(columns=["check", "segment", "metric", "left", "right", "abs_diff"])
    body = render_summary(pd.DataFrame(), checks, trades_by_segment=None)
    assert "좌표를 확인하십시오" in body


def test_verdict_reports_the_pine_memory_cap() -> None:
    """원본이 기억조차 안 하는 구간(31위+)을 판정 줄이 따로 낸다."""
    trades = [_trade(1, 1.0), _trade(PINE_MAX_ORDER_BLOCKS + 1, -4.0)]
    lines = verdict_lines(pd.DataFrame(), {"oos_warm": trades})
    body = "\n".join(lines)
    assert f"{PINE_MAX_ORDER_BLOCKS}개" in body
    assert "-4.0R" in body


# --------------------------------------------------------------------------- #
# 원본 pine 대조 — 이 결론은 **파일에서** 건다(WAN-400/405 방법)
# --------------------------------------------------------------------------- #

_PINE = Path("strategy/reference/fluxchart_volumized_ob.pine")


def test_pine_memory_cap_constant_matches_the_vendored_source() -> None:
    """`PINE_MAX_ORDER_BLOCKS`가 원본 파일의 그 값이다 — 재-벤더링하면 이 테스트가 먼저 죽는다."""
    text = _PINE.read_text(encoding="utf-8")
    assert f"const int maxOrderBlocks = {PINE_MAX_ORDER_BLOCKS}" in text
    # 그 캡이 **데이터 리스트**에 걸린다(렌더 정리가 아니다).
    assert "if bullishOrderBlocksList.size() > maxOrderBlocks" in text
    assert "bullishOrderBlocksList.pop()" in text


def test_pine_zone_count_gate_is_dead_code() -> None:
    """🚨 이슈 코멘트의 선행 조사 답 — `i < bullishOrderBlocks`가 세우는 값을 아무도 읽지 않는다.

    그래서 원본에서도 `Zone Count`는 **표시 설정**이다. 누가 원본을 갱신해 그 값을 읽기
    시작하면 이 테스트가 죽고, 그때는 §2의 전제가 실제로 달라진다.
    """
    lines = _PINE.read_text(encoding="utf-8").splitlines()
    hits = [(i + 1, ln.strip()) for i, ln in enumerate(lines) if "Breaked" in ln]
    # 초기화 2줄 + 대입 2줄뿐 — **읽는 줄이 없다**.
    assert len(hits) == 4, hits
    assert [ln for _n, ln in hits] == [
        "bullishBreaked = 0",
        "bullishBreaked := 1",
        "bearishBreaked = 0",
        "bearishBreaked := 1",
    ]
    # 그 가드는 `breaker` 존에만 · **탭이 아니라 스윙 고·저점**이 안에 드는지를 본다.
    assert any("i < bullishOrderBlocks and top.y < currentOB.top" in ln for ln in lines)
    assert any("i < bearishOrderBlocks and btm.y > currentOB.bottom" in ln for ln in lines)


def test_summary_carries_the_pine_comparison_note() -> None:
    checks = pd.DataFrame(columns=["check", "segment", "metric", "left", "right", "abs_diff"])
    body = render_summary(pd.DataFrame(), checks)
    assert "죽은 코드" in body and "maxOrderBlocks" in body
