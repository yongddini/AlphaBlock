"""backtest/대시보드 경로 ↔ 실시간 러너 경로 시그널 집합 패리티 (WAN-85).

WAN-59(대시보드가 컨플루언스 전략을 아예 안 씀)와 WAN-56(존 병합이 렌더링에만
반영되고 백테스트 시그널에는 미반영) 이후에도, 엔진 기본값이 바뀔 때마다 백테스트
경로만 갱신되고 실시간 페이퍼 러너(`live.runner.SignalRunner`)는 옛 파라미터로 남는
위험이 반복됐다. 이 테스트는 (1) `Settings.confluence`의 기본값이 전략 엔진 기본값과
어긋나지 않는지, (2) 그 설정으로 같은 OHLCV를 태웠을 때 대시보드/백테스트 경로
(`dashboard.pipeline.run_pipeline`)와 실시간 경로(`ConfluenceStrategy(settings.confluence)`)
가 동일한 진입·청산 시그널 집합을 내는지 검증한다.
"""

from __future__ import annotations

from backtest.synthetic import make_synthetic_ohlcv
from config.settings import Settings
from dashboard.pipeline import run_pipeline
from strategy.confluence import ConfluenceStrategy
from strategy.models import ConfluenceParams, OrderBlockParams


def test_settings_confluence_defaults_match_strategy_defaults() -> None:
    """설정 기본값이 전략 엔진 기본값과 어긋나면(파라미터 드리프트) 즉시 실패해야 한다."""
    assert Settings().confluence == ConfluenceParams()


def test_live_and_backtest_paths_produce_identical_signal_set() -> None:
    """같은 구간·심볼에 대해 실시간 경로와 백테스트 경로가 같은 시그널 집합을 낸다."""
    df = make_synthetic_ohlcv(symbol="BTC/USDT:USDT", timeframe="1h", bars=800, seed=7)
    settings = Settings()

    # 실시간 경로: live.runner.SignalRunner가 쓰는 것과 동일한 구성.
    live_result = ConfluenceStrategy(settings.confluence, OrderBlockParams()).run(df)

    # 백테스트/대시보드 경로: dashboard.pipeline.run_pipeline (CLI 리포트와 공유, WAN-59).
    pipeline = run_pipeline(df, OrderBlockParams(), ConfluenceParams())

    live_entries = [
        (e.time, e.direction, e.price, e.confirmed) for e in live_result.confirmed_entries
    ]
    backtest_entries = [(s.trigger_time, s.direction, s.price, True) for s in pipeline.signals]
    assert live_entries == backtest_entries
    assert len(live_entries) > 0  # 합성 데이터에서 실제로 신호가 나오는지도 함께 확인.

    live_exits = [(x.time, x.direction, x.price, x.exit_reason) for x in live_result.exits]
    backtest_exits = [
        (s.planned_exit.time, s.direction, s.planned_exit.price, s.planned_exit.reason)
        for s in pipeline.signals
        if s.planned_exit is not None
    ]
    assert live_exits == backtest_exits
