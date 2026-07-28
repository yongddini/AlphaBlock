"""backtest.wan95_zone_limit_report 단위 테스트 (WAN-95, A안 비교팔 제거 후 WAN-200 §A).

9종목×작업 TF×6년 실데이터 재산출은 `backtest/reports/wan95_zone_limit_recompute.csv`·
`wan95_zone_limit_summary.md`(재현: `python -m backtest.wan95_zone_limit_report`)로 별도
확인한다. 여기서는 결정적 합성 데이터로 지정가(B안) 배선(펀딩 전달·체결률 집계)과 리포트
테이블 생성만 검증한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan95_zone_limit_report import (
    ZONE_LIMIT_PARAMS,
    build_markdown,
    build_tf_verdict_frame,
    rows_to_frame,
    run_symbol_timeframe,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams
from strategy.order_blocks import OrderBlockDetector

_SYMBOL = "TEST/USDT:USDT"
_TIMEFRAME = "1h"


def _synthetic_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    htf = make_synthetic_ohlcv(timeframe=_TIMEFRAME, bars=600, seed=7)
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    span = 120
    start = int(htf["open_time"].iloc[-span])
    minutes = span * (htf_ms // 60_000)
    one_min = make_synthetic_ohlcv(
        timeframe="1m", bars=minutes, seed=11, start_time_ms=start, swing_period=180
    )
    return htf, one_min


def _funding_rates(df: pd.DataFrame) -> list[FundingRate]:
    start = int(df["open_time"].iloc[0])
    end = int(df["open_time"].iloc[-1])
    interval = 8 * 60 * 60_000
    return [
        FundingRate(symbol=_SYMBOL, funding_time=t, rate=0.0001)
        for t in range(start, end, interval)
    ]


def test_zone_limit_params_are_the_adopted_defaults() -> None:
    """채택 프리셋은 저장소 기본값 그 자체 — 리포트가 곧 "채택 기본값 성과"여야 한다."""
    assert ConfluenceParams() == ZONE_LIMIT_PARAMS
    assert ZONE_LIMIT_PARAMS.entry_mode == "zone_limit"
    assert ZONE_LIMIT_PARAMS.rsi_mode == "realtime"
    assert ZONE_LIMIT_PARAMS.short_enabled is False  # WAN-87 롱 온리 유지.


def test_run_symbol_timeframe_emits_only_zone_limit_with_fill_rate() -> None:
    """A안 제거 후 한 셀은 지정가(B안) 한 행만 내고, 체결률이 붙는다(WAN-200 §A)."""
    htf, one_min = _synthetic_pair()
    ob_result = OrderBlockDetector().run(htf)
    rows = run_symbol_timeframe(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        order_block_result=ob_result,
    )
    assert [r.entry_mode for r in rows] == ["zone_limit"]
    (zl,) = rows
    # 체결률은 지정가 전환의 기회비용 축 — 지정가 행에만 존재한다.
    assert zl.eligible_setups is not None and zl.num_filled is not None
    assert zl.fill_rate is None or 0.0 <= zl.fill_rate <= 1.0


def test_funding_coverage_present_on_zone_limit_row() -> None:
    """지정가 행이 펀딩 커버리지를 잃지 않는다 — "펀딩을 반영했는가"의 증거(WAN-95)."""
    htf, one_min = _synthetic_pair()
    ob_result = OrderBlockDetector().run(htf)
    rows = run_symbol_timeframe(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        order_block_result=ob_result,
    )
    for row in rows:
        assert row.funding_coverage is not None, f"{row.entry_mode} 행의 커버리지가 유실됐다"
        assert 0.0 <= row.funding_coverage <= 1.0


def test_frames_and_markdown_render() -> None:
    """B안 단독 CSV/마크다운 생성이 렌더된다(델타표 없음, WAN-200 §A)."""
    htf, one_min = _synthetic_pair()
    ob_result = OrderBlockDetector().run(htf)
    rows = run_symbol_timeframe(
        htf,
        one_min,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        funding_rates=_funding_rates(htf),
        order_block_result=ob_result,
    )
    frame = rows_to_frame(rows)
    for col in ("symbol", "timeframe", "entry_mode", "total_return", "fill_rate"):
        assert col in frame.columns
    assert set(frame["entry_mode"]) == {"zone_limit"}

    md = build_markdown(frame)
    assert "WAN-95" in md
    assert "python -m backtest.wan95_zone_limit_report" in md
    assert "체결률" in md
    # 15m 재판단(WAN-91 권고 재검토)과 낙관 편향 한계가 리포트에 함께 남아야 한다.
    assert "TF 채택 판단" in md
    assert "한계" in md
    # A안(종가) 비교팔은 WAN-200 §A로 제거됐다 — 델타표 섹션이 없어야 한다.
    assert "## 종가 → 지정가 델타" not in md


def test_tf_verdict_frame_counts_positive_symbols() -> None:
    """TF 판단표가 진입 방식별로 플러스 심볼 수·평균 수익률을 집계한다(B안 단독).

    "15m 지정가 2/3 플러스"처럼 채택 판단의 근거가 되는 수치라, 집계 규칙이 어긋나면
    잘못된 권고로 이어진다.
    """
    frame = pd.DataFrame(
        [
            {
                "symbol": s,
                "timeframe": "15m",
                "entry_mode": "zone_limit",
                "total_return": ret,
                "max_drawdown": 0.1,
                "fill_rate": 0.3,
            }
            for s, ret in [("A", -0.1), ("B", 0.2), ("C", 0.2)]
        ]
    )
    verdict = build_tf_verdict_frame(frame)
    zl_row = verdict[verdict["entry_mode"] == "zone_limit"].iloc[0]
    assert zl_row["positive_symbols"] == 2
    assert zl_row["num_symbols"] == 3
    assert zl_row["mean_return"] == pytest.approx(0.1)
    assert zl_row["mean_fill_rate"] == pytest.approx(0.3)
