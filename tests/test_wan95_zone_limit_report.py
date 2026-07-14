"""backtest.wan95_zone_limit_report 단위 테스트 (WAN-95).

3심볼×4TF×3년 실데이터 재산출은 `backtest/reports/wan95_zone_limit_*.csv`·
`wan95_zone_limit_summary.md`(재현: `python -m backtest.wan95_zone_limit_report`)로
별도 확인한다. 여기서는 결정적 합성 데이터로 지정가/종가 두 변형의 배선(비용 비대칭·
펀딩 전달·체결률 집계)과 리포트 테이블 생성만 검증한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan95_zone_limit_report import (
    CLOSE_ENTRY_PARAMS,
    ZONE_LIMIT_PARAMS,
    build_delta_frame,
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


def test_close_preset_differs_only_in_entry_and_rsi_mode() -> None:
    """대조군은 진입 방식·RSI 모드만 다르다 — 격리 변수는 진입 방식 하나여야 한다."""
    assert (
        ZONE_LIMIT_PARAMS.model_copy(update={"entry_mode": "close", "rsi_mode": "closed_bar"})
        == CLOSE_ENTRY_PARAMS
    )


def test_run_symbol_timeframe_emits_both_variants_with_fill_rate() -> None:
    """한 셀에서 지정가·종가 두 행이 나오고, 체결률은 지정가에만 붙는다."""
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
    assert [r.entry_mode for r in rows] == ["zone_limit", "close"]
    zl, close = rows
    # 체결률은 지정가 전환의 기회비용 축 — 지정가 행에만 존재한다.
    assert zl.eligible_setups is not None and zl.num_filled is not None
    assert zl.fill_rate is None or 0.0 <= zl.fill_rate <= 1.0
    assert close.fill_rate is None and close.eligible_setups is None


def test_funding_coverage_survives_windowing_on_close_variant() -> None:
    """종가 변형도 펀딩 커버리지를 잃지 않는다(창 재집계 시 유실 방지, WAN-95).

    창으로 자르는 건 거래 집계일 뿐 펀딩 커버리지를 바꾸지 않는다. 유실되면 "펀딩을
    반영했는가"를 리포트에서 확인할 수 없다.
    """
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
    """CSV/델타/마크다운 생성이 두 변형 모두를 담아 렌더된다."""
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
    delta = build_delta_frame(frame)
    assert {"close_return", "zone_limit_return", "return_delta"} <= set(delta.columns)

    md = build_markdown(frame, delta)
    assert "WAN-95" in md
    assert "python -m backtest.wan95_zone_limit_report" in md
    assert "체결률" in md
    # 15m 재판단(WAN-91 권고 재검토)과 낙관 편향 한계가 리포트에 함께 남아야 한다.
    assert "TF 채택 판단" in md
    assert "한계" in md


def test_tf_verdict_frame_counts_positive_symbols_per_entry_mode() -> None:
    """TF 판단표가 진입 방식별로 플러스 심볼 수·평균 수익률을 집계한다.

    "15m이 종가에선 0/3, 지정가에선 3/3"처럼 채택 판단의 근거가 되는 수치라, 집계
    규칙이 어긋나면 잘못된 권고로 이어진다.
    """
    frame = pd.DataFrame(
        [
            {
                "symbol": s,
                "timeframe": "15m",
                "entry_mode": mode,
                "total_return": ret,
                "max_drawdown": 0.1,
                "fill_rate": 0.3 if mode == "zone_limit" else None,
            }
            for s, mode, ret in [
                ("A", "close", -0.2),
                ("B", "close", -0.1),
                ("A", "zone_limit", 0.1),
                ("B", "zone_limit", 0.2),
            ]
        ]
    )
    verdict = build_tf_verdict_frame(frame)
    close_row = verdict[verdict["entry_mode"] == "close"].iloc[0]
    zl_row = verdict[verdict["entry_mode"] == "zone_limit"].iloc[0]
    assert close_row["positive_symbols"] == 0
    assert close_row["num_symbols"] == 2
    assert zl_row["positive_symbols"] == 2
    assert zl_row["mean_return"] == pytest.approx(0.15)
    assert zl_row["mean_fill_rate"] == pytest.approx(0.3)
