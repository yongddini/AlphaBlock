"""WAN-300 유니버스 크기 스윕의 자(尺)를 동작으로 고정한다.

지키는 것:

1. **유니버스 사다리** — 크기 9/12/15/19가 중첩 부분집합이고(작은 유니버스 ⊂ 큰
   유니버스), 채택 9종목이 언제나 바닥이며, 범위 밖 크기는 거부한다.
2. **밀림율·위험조정 지표** — `skip_notional_rate`가 모듈 정의(분모 = 거래 + 명목 밀림,
   칸바쁨 제외)대로 계산되고, 분모 0·MDD 0이면 정의하지 않는다(WAN-115 가드).
3. **CSV 왕복·append 병합** — (렌즈, 유니버스, 스코프, 구간, 제외) 키로 덮어쓰고,
   단일 TF append가 기존 `all` 스코프를 지우지 않는다(wan301 규약).
4. **펀딩 대리 일반화** — 자기 펀딩이 있는 칸은 비트 불변, 0행 칸만 최고-펀딩 도너로
   대체되며, 공백이 없으면 항등이다(WAN-180 규칙의 유니버스 일반화).
5. **부분 실행 거부** — 유니버스 구성원의 셀이 없으면 조용히 좁은 북을 만들지 않고
   ValueError로 죽는다(WAN-95 교훈).
6. **wan180 교차 대조** — 거래 집합 불일치는 RuntimeError(배선 오류), `total_return`
   발산은 의도된 것(WAN-292 실펀딩)으로만 보고한다.
7. **판정·요약 렌더** — (a/b/c) 판정과 경고(WAN-111 희석 선례 · 복리 착시 · 측정 전용)가
   나온다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import SEGMENT_FULL, SEGMENT_OOS_WARM
from backtest.wan169_leverage_book import CellPayload
from backtest.wan176_nine_symbol_rebaseline import NINE_SYMBOLS
from backtest.wan300_universe_size import (
    ALL_SYMBOLS,
    CANDIDATE_SYMBOLS,
    DEFAULT_LENSES,
    REF_LENS,
    UNIVERSE_SIZES,
    UniverseCellRow,
    UniverseRow,
    _universe_payloads,
    apply_funding_proxy_any,
    build_rows_for_cells,
    build_summary_markdown,
    cells_from_csv,
    cells_to_frame,
    cross_check_wan180,
    find_row,
    grid_from_csv,
    grid_to_frame,
    merge_grid,
    universe_symbols,
    verdict,
)
from data.models import FundingRate

# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def _row(
    lens: str = REF_LENS,
    universe: int = 9,
    scope: str = "all",
    segment: str = SEGMENT_OOS_WARM,
    exclude: str = "",
    *,
    ret: float = 0.5,
    mdd: float = 0.10,
    trades: int = 100,
    skipped_notional: int = 0,
    skipped_cell_busy: int = 5,
) -> UniverseRow:
    return UniverseRow(
        lens=lens,
        universe=universe,
        scope=scope,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=universe * 2,
        num_trades=trades,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=mdd,
        peak_concurrency=3,
        max_concurrent_risk=0.05,
        max_open_notional_ratio=1.2,
        liquidation_events=0,
        clamped_entries=2,
        skipped_cell_busy=skipped_cell_busy,
        skipped_notional=skipped_notional,
    )


def _payload(
    symbol: str,
    timeframe: str = "1h",
    *,
    funding: tuple[FundingRate, ...] = (),
) -> CellPayload:
    return CellPayload(
        symbol=symbol,
        timeframe=timeframe,
        boundary_ms=1_000,
        candidates={SEGMENT_FULL: ()},
        funding={SEGMENT_FULL: funding},
        rows=(),
    )


def _rate(symbol: str, rate: float, *, predicted: bool = False) -> FundingRate:
    return FundingRate(symbol=symbol, funding_time=1_000, rate=rate, is_predicted=predicted)


# --------------------------------------------------------------------------- #
# 1. 유니버스 사다리
# --------------------------------------------------------------------------- #


def test_universe_ladder_is_nested_and_sized() -> None:
    prev: tuple[str, ...] = ()
    for size in UNIVERSE_SIZES:
        members = universe_symbols(size)
        assert len(members) == size
        assert len(set(members)) == size, "중복 종목 금지"
        assert set(prev).issubset(set(members)), "작은 유니버스는 큰 유니버스의 부분집합"
        prev = members
    assert universe_symbols(9) == NINE_SYMBOLS
    assert set(ALL_SYMBOLS) == set(NINE_SYMBOLS) | set(CANDIDATE_SYMBOLS)


def test_universe_symbols_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        universe_symbols(8)
    with pytest.raises(ValueError):
        universe_symbols(len(ALL_SYMBOLS) + 1)


def test_candidates_do_not_overlap_nine() -> None:
    assert not set(CANDIDATE_SYMBOLS) & set(NINE_SYMBOLS)


# --------------------------------------------------------------------------- #
# 2. 지표 가드
# --------------------------------------------------------------------------- #


def test_skip_notional_rate_definition() -> None:
    r = _row(trades=90, skipped_notional=10, skipped_cell_busy=999)
    # 분모 = 거래 + 명목 밀림(칸바쁨 제외) — 모듈 docstring 정의.
    assert r.skip_notional_rate == pytest.approx(10 / 100)


def test_skip_notional_rate_zero_denominator_guard() -> None:
    r = _row(trades=0, skipped_notional=0)
    assert r.skip_notional_rate is None


def test_return_over_mdd_undefined_when_mdd_zero() -> None:
    r = _row(mdd=0.0)
    assert r.return_over_mdd is None
    r2 = _row(ret=0.4, mdd=0.2)
    assert r2.return_over_mdd == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# 3. CSV 왕복 · 병합
# --------------------------------------------------------------------------- #


def test_grid_roundtrip(tmp_path: Path) -> None:
    rows = [_row(), _row(universe=12, exclude="ETH")]
    path = tmp_path / "grid.csv"
    grid_to_frame(rows).to_csv(path, index=False)
    back = grid_from_csv(path)
    assert back == rows


def test_cells_roundtrip_preserves_none_engine_columns(tmp_path: Path) -> None:
    rows = [
        UniverseCellRow(
            lens=REF_LENS,
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment=SEGMENT_FULL,
            num_candidates=10,
            num_trades=8,
            win_rate=0.5,
            total_return=0.1,
            max_drawdown=0.05,
            engine_total_return=0.1,
            engine_num_trades=8,
        ),
        UniverseCellRow(
            lens="pen_5bp",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            segment=SEGMENT_OOS_WARM,
            num_candidates=5,
            num_trades=4,
            win_rate=0.5,
            total_return=0.05,
            max_drawdown=0.03,
        ),
    ]
    path = tmp_path / "cells.csv"
    cells_to_frame(rows).to_csv(path, index=False)
    back = cells_from_csv(path)
    assert back == rows


def test_merge_grid_overwrites_by_key_and_keeps_all_scope() -> None:
    old_all = _row(scope="all", ret=0.5)
    old_1h = _row(scope="1h", ret=0.3)
    merged = merge_grid([old_all, old_1h], [_row(scope="1h", ret=0.9)])
    by_scope = {r.scope: r for r in merged}
    assert by_scope["1h"].total_return == pytest.approx(0.9), "새 행이 덮어쓴다"
    assert by_scope["all"].total_return == pytest.approx(0.5), "all 스코프는 남는다"
    assert len(merged) == 2


def test_merge_grid_key_includes_exclude_symbol() -> None:
    merged = merge_grid([_row()], [_row(exclude="ETH")])
    assert len(merged) == 2


# --------------------------------------------------------------------------- #
# 4. 펀딩 대리 일반화
# --------------------------------------------------------------------------- #


def test_funding_proxy_identity_when_no_gap() -> None:
    payloads = [
        _payload("BTC/USDT:USDT", funding=(_rate("BTC/USDT:USDT", 0.0001),)),
        _payload("ETH/USDT:USDT", funding=(_rate("ETH/USDT:USDT", 0.0002),)),
    ]
    out, note = apply_funding_proxy_any(payloads)
    assert out == payloads, "공백이 없으면 항등(비트 불변)"
    assert note == ""


def test_funding_proxy_replaces_gapped_cell_with_highest_donor() -> None:
    btc = _payload("BTC/USDT:USDT", funding=(_rate("BTC/USDT:USDT", 0.0001),))
    trx = _payload("TRX/USDT:USDT", funding=(_rate("TRX/USDT:USDT", 0.0009),))
    gap = _payload("XMR/USDT:USDT")
    out, note = apply_funding_proxy_any([btc, trx, gap])
    assert out[0] is btc and out[1] is trx, "자기 펀딩이 있는 칸은 그대로"
    assert out[2].funding[SEGMENT_FULL] == trx.funding[SEGMENT_FULL], (
        "0행 칸은 확정 펀딩 평균 최고 도너(TRX) 시계열로 대체"
    )
    assert "TRX" in note and "XMR" in note


def test_funding_proxy_ignores_predicted_rates_for_donor_choice() -> None:
    btc = _payload("BTC/USDT:USDT", funding=(_rate("BTC/USDT:USDT", 0.0001),))
    eth = _payload(
        "ETH/USDT:USDT",
        funding=(_rate("ETH/USDT:USDT", 0.5, predicted=True),),
    )
    gap = _payload("XMR/USDT:USDT")
    out, _note = apply_funding_proxy_any([btc, eth, gap])
    # ETH는 확정 펀딩이 없어(예측뿐) 도너가 될 수 없다 — BTC가 도너다.
    assert out[2].funding[SEGMENT_FULL] == btc.funding[SEGMENT_FULL]


# --------------------------------------------------------------------------- #
# 5. 부분 실행 거부
# --------------------------------------------------------------------------- #


def test_build_rows_rejects_missing_universe_members() -> None:
    payloads = [_payload(f"{s[:-4]}/USDT:USDT") for s in NINE_SYMBOLS[:3]]
    with pytest.raises(ValueError, match="셀이 없습니다"):
        build_rows_for_cells(payloads, lens=REF_LENS, sizes=(9,))


def test_universe_payloads_selects_normalized_symbols() -> None:
    """상수(BTCUSDT 축약형) ↔ payload(BTC/USDT:USDT 정규형) 표기 차이를 넘어 실제로
    골라야 한다 — 초판 버그: 한쪽만 `_short`를 태워 교집합이 조용히 비었고 **모든 북이
    빈 칸(num_cells=0)으로 돌았다**. 동작으로 고정한다."""
    payloads = [
        _payload(f"{s[:-4]}/USDT:USDT", timeframe=tf)
        for s in universe_symbols(12)
        for tf in ("1h", "4h")
    ]
    assert len(_universe_payloads(payloads, 9, "all")) == 18
    assert len(_universe_payloads(payloads, 9, "1h")) == 9
    assert len(_universe_payloads(payloads, 12, "all")) == 24
    assert len(_universe_payloads(payloads, 12, "4h")) == 12


def test_build_rows_books_are_not_empty() -> None:
    """조립된 북 행의 `num_cells`가 유니버스 × 스코프 기대값과 일치한다(빈 북 회귀 방지)."""
    payloads = [
        _payload(f"{s[:-4]}/USDT:USDT", timeframe=tf)
        for s in universe_symbols(12)
        for tf in ("1h", "4h")
    ]
    rows = build_rows_for_cells(payloads, lens="pen_5bp", sizes=(9, 12))
    by_key = {(r.universe, r.scope, r.segment): r for r in rows}
    assert by_key[(9, "all", SEGMENT_OOS_WARM)].num_cells == 18
    assert by_key[(9, "1h", SEGMENT_FULL)].num_cells == 9
    assert by_key[(12, "all", SEGMENT_FULL)].num_cells == 24
    assert by_key[(12, "4h", SEGMENT_OOS_WARM)].num_cells == 12


# --------------------------------------------------------------------------- #
# 6. wan180 교차 대조
# --------------------------------------------------------------------------- #


def _cell_row(
    symbol: str = "BTC/USDT:USDT",
    *,
    lens: str = REF_LENS,
    segment: str = SEGMENT_FULL,
    num_candidates: int = 10,
    num_trades: int = 8,
    total_return: float = 0.1,
) -> UniverseCellRow:
    return UniverseCellRow(
        lens=lens,
        symbol=symbol,
        timeframe="1h",
        segment=segment,
        num_candidates=num_candidates,
        num_trades=num_trades,
        win_rate=0.5,
        total_return=total_return,
        max_drawdown=0.05,
    )


def _ref_csv(tmp_path: Path, *, num_trades: int = 8, total_return: float = 0.1) -> Path:
    path = tmp_path / "wan180_cells.csv"
    pd.DataFrame(
        [
            {
                "symbol": "BTC/USDT:USDT",
                "timeframe": "1h",
                "segment": SEGMENT_FULL,
                "num_candidates": 10,
                "num_trades": num_trades,
                "win_rate": 0.5,
                "total_return": total_return,
                "max_drawdown": 0.05,
            }
        ]
    ).to_csv(path, index=False)
    return path


def test_cross_check_bit_match_and_intended_divergence(tmp_path: Path) -> None:
    path = _ref_csv(tmp_path)
    lines = cross_check_wan180([_cell_row()], path)
    assert any("비트 일치" in line for line in lines)
    # 같은 거래 집합인데 total_return만 다르면 = 펀딩 발산(의도됨) — 죽지 않는다.
    lines2 = cross_check_wan180([_cell_row(total_return=0.2)], path)
    assert any("발산" in line for line in lines2)


def test_cross_check_trade_set_mismatch_raises(tmp_path: Path) -> None:
    path = _ref_csv(tmp_path, num_trades=99)
    with pytest.raises(RuntimeError, match="배선 오류"):
        cross_check_wan180([_cell_row()], path)


def test_cross_check_missing_file_skips(tmp_path: Path) -> None:
    lines = cross_check_wan180([_cell_row()], tmp_path / "absent.csv")
    assert any("생략" in line for line in lines)


# --------------------------------------------------------------------------- #
# 7. 판정 · 요약
# --------------------------------------------------------------------------- #


def _ladder(
    *,
    returns: dict[int, float],
    mdds: dict[int, float],
    skips: dict[int, int] | None = None,
) -> list[UniverseRow]:
    return [
        _row(
            universe=size,
            ret=returns[size],
            mdd=mdds[size],
            skipped_notional=(skips or {}).get(size, 0),
        )
        for size in returns
    ]


def test_verdict_a_when_capacity_beats_dilution() -> None:
    rows = _ladder(
        returns={9: 0.5, 12: 0.8, 15: 0.7, 19: 0.6},
        mdds={9: 0.10, 12: 0.10, 15: 0.12, 19: 0.15},
    )
    line = verdict(rows)
    assert "판정 (a)" in line
    assert "12" in line


def test_verdict_b_when_dilution_dominates() -> None:
    rows = _ladder(
        returns={9: 0.5, 12: 0.4, 15: 0.3, 19: 0.2},
        mdds={9: 0.10, 12: 0.10, 15: 0.10, 19: 0.10},
    )
    assert "판정 (b)" in verdict(rows)


def test_verdict_c_when_return_up_but_risk_adjusted_down() -> None:
    rows = _ladder(
        returns={9: 0.5, 12: 0.6, 15: 0.55, 19: 0.5},
        mdds={9: 0.10, 12: 0.20, 15: 0.25, 19: 0.30},
    )
    assert "판정 (c)" in verdict(rows)


def test_verdict_reports_saturation_from_skip_rate() -> None:
    rows = _ladder(
        returns={9: 0.5, 12: 0.6, 15: 0.7, 19: 0.8},
        mdds={9: 0.1, 12: 0.1, 15: 0.1, 19: 0.1},
        skips={9: 0, 12: 1, 15: 3, 19: 20},  # 19에서 20/(100+20) = 16.7% ≥ 5%
    )
    line = verdict(rows)
    assert "19종목부터 5%" in line or "포화점 근처" in line


def test_verdict_reports_no_saturation_when_skips_tiny() -> None:
    rows = _ladder(
        returns={9: 0.5, 12: 0.6, 15: 0.7, 19: 0.8},
        mdds={9: 0.1, 12: 0.1, 15: 0.1, 19: 0.1},
        skips={9: 0, 12: 0, 15: 1, 19: 2},
    )
    assert "포화 근처가 아니다" in verdict(rows)


def test_summary_renders_key_sections() -> None:
    rows = _ladder(
        returns={9: 0.5, 12: 0.6, 15: 0.7, 19: 0.8},
        mdds={9: 0.1, 12: 0.1, 15: 0.1, 19: 0.1},
    ) + [_row(universe=9, exclude="ETH")]
    md = build_summary_markdown(rows, [], adv_table=[("BCHUSDT", 1.23e8)])
    assert "WAN-300" in md
    assert "판정" in md
    assert "WAN-111" in md, "희석 선례 경고"
    assert "복리 착시" in md
    assert "재-베이스라인 = 사용자 결정" in md
    assert "leave-one-out" in md
    assert "BCHUSDT" in md, "ADV 표"
    assert "EOSUSDT" in md, "제외 후보 기록"


def test_default_lenses_are_baseline_and_pen5bp() -> None:
    assert DEFAULT_LENSES == ("baseline", "pen_5bp")
    assert REF_LENS == "baseline"


def test_find_row_distinguishes_exclude() -> None:
    rows = [_row(), _row(exclude="ETH", ret=0.1)]
    assert find_row(rows, REF_LENS, 9, "all", SEGMENT_OOS_WARM) is rows[0]
    assert find_row(rows, REF_LENS, 9, "all", SEGMENT_OOS_WARM, "ETH") is rows[1]
