"""분석 탭 디스크 캐시 (WAN-188).

캐시는 **성능 장치**이므로 두 가지를 동작으로 고정한다:

1. 캐시에서 꺼낸 결과가 다시 계산한 결과와 **같다**(표현 최적화이지 숫자를 바꾸지 않는다).
2. 엔진이 바뀌면(코드 리비전·파라미터·창) 키가 갈라져 **옛 결과를 꺼내 오지 않는다** —
   WAN-106 원칙. 이게 깨지면 "고쳤는데 옛 숫자가 보이는" 조용한 실패가 된다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.models import BacktestConfig
from backtest.sweep import default_backtest_config
from dashboard.analysis_cache import AnalysisCache, cache_key
from dashboard.pipeline import run_pipeline
from strategy.models import ConfluenceParams, OrderBlockParams

_STEP = 3_600_000


def _frame(bars: int = 120) -> pd.DataFrame:
    """탐지가 실제로 무언가를 찾도록 오르내리는 합성 봉."""
    rows = []
    for i in range(bars):
        swing = 10.0 * ((i % 12) - 6)
        base = 100.0 + i * 0.5 + swing
        rows.append(
            {
                "symbol": "BTC/USDT:USDT",
                "timeframe": "1h",
                "open_time": i * _STEP,
                "open": base,
                "high": base + 5.0,
                "low": base - 5.0,
                "close": base + (2.0 if i % 3 else -2.0),
                "volume": 10.0 + i,
                "closed": True,
            }
        )
    frame = pd.DataFrame(rows)
    frame["open_datetime"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    return frame


def _params() -> tuple[OrderBlockParams, ConfluenceParams, BacktestConfig]:
    ob = OrderBlockParams()
    conf = ConfluenceParams(
        entry_mode="close",
        rsi_mode="closed_bar",
        zone_limit_offset_bps=0.0,
        max_zone_width_atr=None,
    )
    return ob, conf, default_backtest_config("1h")


def _key(**overrides: object) -> str:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start_ms": 0,
        "end_ms": 1_000,
        "params_key": "{}",
        "revision": "abc1234",
    }
    base.update(overrides)
    return cache_key(**base)  # type: ignore[arg-type]


def test_roundtrip_preserves_every_number(tmp_path: Path) -> None:
    """복원한 결과가 다시 계산한 결과와 같다 — 캐시는 숫자를 바꾸지 않는다."""
    ob, conf, cfg = _params()
    result = run_pipeline(_frame(), ob, conf, cfg)
    cache = AnalysisCache(tmp_path)
    cache.store("k", result)

    restored = cache.load("k")
    assert restored is not None
    assert restored.backtest.metrics.model_dump() == result.backtest.metrics.model_dump()
    assert [t.model_dump() for t in restored.backtest.trades] == [
        t.model_dump() for t in result.backtest.trades
    ]
    assert [z.model_dump() for z in restored.order_blocks] == [
        z.model_dump() for z in result.order_blocks
    ]
    assert [s.model_dump() for s in restored.signals] == [s.model_dump() for s in result.signals]


def test_miss_returns_none_instead_of_raising(tmp_path: Path) -> None:
    assert AnalysisCache(tmp_path).load("nope") is None


def test_corrupt_cache_falls_back_to_recompute(tmp_path: Path) -> None:
    """손상된 파일은 예외가 아니라 캐시 미스다 — 화면은 떠야 한다."""
    cache = AnalysisCache(tmp_path)
    path = cache.path_for("k")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not gzip at all")
    assert cache.load("k") is None


def test_key_changes_with_code_revision() -> None:
    """리비전이 키에 실린다 — 엔진을 고쳐도 옛 결과가 나오면 안 된다(WAN-106)."""
    assert _key(revision="abc1234") != _key(revision="def5678")


def test_key_requires_revision_explicitly() -> None:
    """`revision`에 기본값이 없다 — 잊으면 조용히 낡는 대신 즉시 터진다."""
    with pytest.raises(TypeError):
        cache_key(  # type: ignore[call-arg]
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            start_ms=0,
            end_ms=1_000,
            params_key="{}",
        )


@pytest.mark.parametrize(
    "override",
    [
        {"symbol": "ETH/USDT:USDT"},
        {"timeframe": "15m"},
        {"start_ms": 1},
        {"end_ms": 999},
        {"params_key": '{"take_profit_r": 2.0}'},
    ],
)
def test_key_changes_with_every_input(override: dict[str, object]) -> None:
    """심볼·TF·창·파라미터 중 무엇이 달라도 키가 갈라진다."""
    assert _key(**override) != _key()


def test_cache_lives_next_to_the_db(tmp_path: Path) -> None:
    """DB를 갈아끼우면 캐시도 갈라진다(테스트의 tmp DB가 저절로 격리되는 이유)."""
    a = AnalysisCache.for_db(tmp_path / "one" / "ohlcv.db").path_for("k")
    b = AnalysisCache.for_db(tmp_path / "two" / "ohlcv.db").path_for("k")
    assert a != b
    assert a.parent == tmp_path / "one" / "cache" / "analysis"


def test_store_leaves_no_temp_files(tmp_path: Path) -> None:
    """원자적 교체 — 반쯤 쓰인 파일이 남으면 다음 실행이 그걸 읽는다."""
    ob, conf, cfg = _params()
    cache = AnalysisCache(tmp_path)
    cache.store("k", run_pipeline(_frame(60), ob, conf, cfg))
    assert [p.name for p in tmp_path.iterdir()] == ["k.json.gz"]
