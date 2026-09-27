"""WAN-428 §2 — 채택 북 `Zone Count` 캡 격자.

무엇을 **동작으로** 고정하는가:
- 캡·배치 구현이 **하나**다(부록의 `cap_candidates` · `wan408.place`를 빌려 쓴다 — 사본을
  만들면 부록 ↔ 채택 북 대조가 조용히 갈라진다, WAN-95/112/123).
- 지갑 층 열이 정의를 잃으면 **비율을 내지 않는다**(WAN-115/386/388 관행).
- 검산 (d)는 **관측**이라 실패로 세지 않는다 — 0이 아닌 것이 재배치의 증거다(WAN-316/389).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from backtest import harness
from backtest.wan408_loss_clustering import build_payloads as wan408_build_payloads
from backtest.wan408_loss_clustering import place as wan408_place
from backtest.wan428_zone_cap_book import (
    BOOK_CAP_CSV,
    CAPS,
    PRIMARY_SEGMENT,
    SEGMENTS,
    BookCapRow,
    CapChecksumRow,
    _stderr,
    _wallet,
    build_payloads,
    cap_candidates,
    checksum_rows,
    place,
    render_summary,
    wallet_defined,
)
from backtest.wan428_zone_rank_census import TradeRank
from backtest.wan428_zone_rank_stoch_arm import cap_candidates as stoch_cap_candidates


def _row(
    cap: int | None,
    *,
    segment: str = PRIMARY_SEGMENT,
    trades: int = 1000,
    net: float = -0.12,
    total_return: float = -0.5,
    mdd: float = 0.3,
) -> BookCapRow:
    from backtest.wan428_zone_rank_stoch_arm import CAP_NAMES

    defined = wallet_defined(total_return, mdd)
    return BookCapRow(
        cap=cap,
        cap_name=CAP_NAMES[cap],
        segment=segment,
        num_trades=trades,
        win_rate=0.4,
        mean_net_r=net,
        stderr_net_r=0.01,
        total_return=total_return,
        max_drawdown=mdd,
        return_over_mdd=(total_return / mdd) if defined else None,
        wallet_defined=defined,
        liquidation_events=0,
        peak_concurrency=7,
        stop_rate=0.6,
    )


# --------------------------------------------------------------------------- #
# 배선 — 구현이 하나여야 한다
# --------------------------------------------------------------------------- #


def test_the_cap_is_the_appendixs_implementation_not_a_copy() -> None:
    """캡이 사본이면 부록 ↔ 채택 북 대조가 「같은 자로 쟀다」를 주장할 수 없다."""
    assert cap_candidates is stoch_cap_candidates


def test_candidates_and_placement_come_from_wan408() -> None:
    """후보 생성·배치가 `book_cli.run_book_segments`의 단일 소스(wan408)와 **같은 객체**다."""
    assert build_payloads is wan408_build_payloads
    assert place is wan408_place


def test_the_cold_cut_is_not_in_the_reported_segments() -> None:
    """🚨 `is`는 아카이브 인덱스가 다른 판이다 — 부록이 그 자리에서 쓰레기를 냈다."""
    assert SEGMENTS == ("full", "oos_warm")
    assert "is" not in SEGMENTS
    assert PRIMARY_SEGMENT in SEGMENTS


def test_the_caps_include_the_originals_default_and_no_cap() -> None:
    assert CAPS[0] is None  # 무제한 = 오늘 엔진
    assert 3 in CAPS  # 원본 `Low` 기본값
    assert 10 in CAPS  # 원본 `High`


# --------------------------------------------------------------------------- #
# 지갑 층 — 정의를 잃으면 비율을 내지 않는다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("total_return", "mdd", "expected"),
    [
        (-0.5, 0.30, True),
        (-0.999999, 0.99, True),
        (-1.0, 0.30, False),  # 자본이 0을 뚫었다
        (-1.5, 1.00, False),
        (0.5, 1.00, False),  # MDD 100% = 포화
    ],
)
def test_wallet_defined_matches_the_repo_predicate(
    total_return: float, mdd: float, expected: bool
) -> None:
    """WAN-386/388/410과 **같은 술어**여야 한다(자를 새로 쓰면 표가 갈라진다)."""
    assert wallet_defined(total_return, mdd) is expected


def test_undefined_wallet_prints_the_words_not_a_ratio() -> None:
    """🚨 포화된 행에 퍼센트를 찍으면 읽는 사람이 그것을 비교한다."""
    saturated = _row(None, total_return=-1.0, mdd=1.0)
    assert _wallet(saturated) == ("정의 상실", "정의 상실", "—")
    live = _row(None, total_return=-0.5, mdd=0.3)
    ret, mdd, ratio = _wallet(live)
    assert "%" in ret and "%" in mdd and ratio != "—"


def test_the_summary_never_prints_a_ratio_for_a_dead_wallet() -> None:
    rows = [_row(None, total_return=-1.0, mdd=1.0), _row(3, total_return=-1.0, mdd=1.0)]
    body = render_summary(rows, [])
    assert "정의 상실" in body
    assert "-100.00%" not in body


def test_the_summary_carries_the_trade_count_and_the_wan378_warning() -> None:
    """거래 수를 빼고 MDD만 읽으면 WAN-378 착시를 그대로 산다."""
    rows = [_row(None, trades=14843), _row(3, trades=11000)]
    body = render_summary(rows, [])
    assert "14,843" in body and "11,000" in body
    assert "WAN-378" in body


def test_the_summary_says_the_appendix_does_not_transfer_here() -> None:
    """부록(용량 미포화)의 결론을 채택 북으로 옮겨 읽지 말라는 경고가 본문에 있어야 한다."""
    body = render_summary([_row(None)], [])
    assert "옮겨 읽지 말" in body
    assert "WAN-316/389" in body


# --------------------------------------------------------------------------- #
# 통계 — 2σ 관문
# --------------------------------------------------------------------------- #


def test_stderr_is_the_sample_standard_error() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    expected = math.sqrt((sum((v - 2.5) ** 2 for v in values) / 3) / 4)
    assert _stderr(values) == pytest.approx(expected)
    assert _stderr([1.0]) == 0.0
    assert _stderr([]) == 0.0


def _delta_line(body: str, cap_name: str) -> str:
    """Δ 표의 그 줄 — 🚨 본 표에도 같은 캡 이름이 있어 **절 머리 뒤에서** 찾아야 한다."""
    lines = body.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("### 무제한 대비 Δ"))
    return next(line for line in lines[start:] if line.startswith(f"| {cap_name}"))


def test_a_delta_inside_two_sigma_is_not_called_decided() -> None:
    """WAN-381/412 관문 — 오차보다 작은 차를 판정으로 찍지 않는다."""
    base = _row(None, net=-0.1207)
    tiny = _row(3, net=-0.1180)  # Δ +0.0027 · 잡음선(0.005) 안
    assert _delta_line(render_summary([base, tiny], []), "Low(3)").rstrip().endswith("아니오 |")

    big = _row(3, net=-0.0500)  # Δ +0.0707 · 2σ(±0.028) 밖
    assert _delta_line(render_summary([base, big], []), "Low(3)").rstrip().endswith("예 |")


def test_a_delta_outside_two_sigma_but_inside_the_noise_line_is_not_decided() -> None:
    """🚨 관문이 **둘**이다 — 오차 밖이어도 ±0.005R 규약 안이면 「0과 구분되지 않는다」."""
    base = _row(None, net=-0.1207)
    base = base.model_copy(update={"stderr_net_r": 0.0001})
    near = _row(3, net=-0.1177).model_copy(update={"stderr_net_r": 0.0001})  # Δ +0.0030
    assert _delta_line(render_summary([base, near], []), "Low(3)").rstrip().endswith("아니오 |")


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def _trade(rank: int | None) -> TradeRank:
    return TradeRank(
        arm="adopted",
        segment=PRIMARY_SEGMENT,
        symbol="BTC/USDT:USDT",
        timeframe="4h",
        trigger_time=1,
        entry_time=2,
        net_r=-0.1,
        is_stop=True,
        is_reentry=False,
        tap_index=0,
        archive_index=0,
        rank=rank,
        rank_bar_close=rank,
        alive_zones=10,
        zone_age_ms=0,
    )


def test_checksum_a_only_fires_on_the_adopted_coordinates() -> None:
    rows = [_row(None)]
    assert not [c for c in checksum_rows(rows, {}, adopted_coordinates=False) if "(a)" in c.metric]
    assert [c for c in checksum_rows(rows, {}, adopted_coordinates=True) if "(a)" in c.metric]


def test_checksum_d_is_labelled_observation_so_it_cannot_fail_the_run() -> None:
    """🚨 재배치가 있으면 (d)는 0이 아닌 게 **정상**이다 — 실패로 세면 채택 북이 못 돈다."""
    base = _row(None, trades=1000)
    capped = _row(3, trades=900)  # 감소율 10%
    trades = {PRIMARY_SEGMENT: [_trade(1)] * 800 + [_trade(9)] * 200}  # 순위 몫 20%
    checks = checksum_rows([base, capped], trades, adopted_coordinates=False, caps=(3,))
    (row,) = [c for c in checks if "(d)" in c.metric]
    assert "관측" in row.metric
    assert row.left == pytest.approx(0.10)
    assert row.right == pytest.approx(0.20)
    assert row.abs_diff == pytest.approx(0.10)


def test_checksum_d_counts_unrankable_trades_as_cut() -> None:
    """순위를 못 잰 거래(`rank is None`)는 캡에서 **잘린다**.

    몫에 넣지 않으면 기대치가 낮아져 (d)가 없는 재배치를 보고한다.
    """
    base = _row(None, trades=100)
    capped = _row(3, trades=50)
    trades = {PRIMARY_SEGMENT: [_trade(1)] * 50 + [_trade(None)] * 50}
    checks = checksum_rows([base, capped], trades, adopted_coordinates=False, caps=(3,))
    (row,) = [c for c in checks if "(d)" in c.metric]
    assert row.right == pytest.approx(0.50)


def test_the_exit_code_ignores_observation_rows() -> None:
    """`main`의 종료 코드 술어를 값으로 고정한다(관측 행이 1을 내면 이 표를 못 돌린다)."""
    checks = [
        CapChecksumRow(metric="(d) 관측 · Low(3) × oos_warm", left=0.1, right=0.2, abs_diff=0.1),
        CapChecksumRow(metric="(a) 무제한 팔 거래 수", left=1.0, right=1.0, abs_diff=0.0),
    ]
    bad = [c for c in checks if "관측" not in c.metric and c.abs_diff > 1e-6]
    assert bad == []


# --------------------------------------------------------------------------- #
# 공개 CSV
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not BOOK_CAP_CSV.exists(), reason="공개 CSV 미적재")
def test_the_published_table_has_every_cap_and_no_cold_cut() -> None:
    frame = pd.read_csv(BOOK_CAP_CSV)
    assert set(frame.segment.unique()) == set(SEGMENTS)
    for segment in SEGMENTS:
        part = frame[frame.segment == segment]
        caps = {None if pd.isna(c) else int(c) for c in part.cap}
        assert caps == set(CAPS)


@pytest.mark.skipif(not BOOK_CAP_CSV.exists(), reason="공개 CSV 미적재")
def test_the_uncapped_arm_is_the_bare_adopted_book() -> None:
    """🚨 무제한 팔이 공개 채택 북과 같은 거래 수여야 이 표가 그 북의 표다."""
    from backtest.wan409_invalidation_cascade import PUBLISHED_OOS_WARM_TRADES

    frame = pd.read_csv(BOOK_CAP_CSV)
    row = frame[(frame.segment == PRIMARY_SEGMENT) & (frame.cap.isna())].iloc[0]
    assert int(row.num_trades) == PUBLISHED_OOS_WARM_TRADES


@pytest.mark.skipif(not BOOK_CAP_CSV.exists(), reason="공개 CSV 미적재")
def test_the_published_table_round_trips_through_the_model() -> None:
    """`--from-csv`가 `cap` NaN을 `None`으로 되살린다(안 되면 요약이 죽는다)."""
    frame = pd.read_csv(BOOK_CAP_CSV)
    rows = [
        BookCapRow(**{**r, "cap": None if pd.isna(r["cap"]) else int(r["cap"])})
        for r in frame.to_dict("records")
    ]
    assert len(rows) == len(frame)
    assert any(r.cap is None for r in rows)
    assert "정의 상실" in render_summary(rows, []) or "%" in render_summary(rows, [])


def test_the_adopted_coordinates_are_the_default() -> None:
    """좁혀 도는 것이 기본이 되면 「채택 북」 라벨이 거짓이 된다(WAN-305)."""
    import inspect

    from backtest.wan428_zone_cap_book import run_measure

    sig = inspect.signature(run_measure)
    assert sig.parameters["symbols"].default is harness.DEFAULT_SYMBOLS
    assert sig.parameters["timeframes"].default is harness.DEFAULT_TIMEFRAMES
    assert sig.parameters["start"].default == harness.DEFAULT_START
    assert sig.parameters["end"].default == harness.DEFAULT_END


# --------------------------------------------------------------------------- #
# 포화 — 「정의됐다」와 「읽을 수 있다」는 다른 말이다
# --------------------------------------------------------------------------- #


def test_the_adopted_coordinate_passes_wallet_defined_but_is_saturated() -> None:
    """🚨 실측값 그대로 — 부동소수로는 정의되는데 −100%/100%에 붙어 **무정보**다."""
    from backtest.wan428_zone_cap_book import wallet_readable

    total_return, mdd = -0.9999985857315258, 0.9999990779430545  # 채택 좌표 oos_warm 실측
    assert wallet_defined(total_return, mdd) is True
    assert wallet_readable(total_return, mdd) is False


def test_a_saturated_row_prints_the_word_not_a_ratio() -> None:
    """포화 행이 `-1.00`을 달고 표에 서면 읽는 사람이 다섯 캡의 −1.00을 **비교한다**."""
    row = _row(None, total_return=-0.9999986, mdd=0.9999991)
    ret, mdd, ratio = _wallet(row)
    assert "포화" in ret and "포화" in mdd and ratio == "—"
    body = render_summary([row], [])
    assert "-1.00" not in body
    assert "포화" in body


def test_saturation_and_undefined_get_different_words() -> None:
    """한 낱말로 뭉개면 「자본이 0을 뚫었다」와 「0에 붙었다」가 구분되지 않는다."""
    saturated = _wallet(_row(None, total_return=-0.9999986, mdd=0.9999991))
    undefined = _wallet(_row(None, total_return=-1.2, mdd=1.0))
    assert saturated[0] != undefined[0]
    assert undefined[0] == "정의 상실"


def test_a_live_wallet_still_prints_its_ratio() -> None:
    """포화 가드가 멀쩡한 행까지 지우면 부록 표(MDD 22.5%)가 안 읽힌다."""
    ret, mdd, ratio = _wallet(_row(None, total_return=1.01, mdd=0.225))
    assert ret == "101.00%" and mdd == "22.50%" and ratio == "4.49"


def test_the_summary_says_the_saturation_is_half_the_answer() -> None:
    """사용자 질문이 MDD였으므로 「그 열로는 못 가른다」가 본문에 있어야 한다."""
    body = render_summary([_row(None, total_return=-0.9999986, mdd=0.9999991)], [])
    assert "포화한다" in body
    assert "거래당 net R" in body
