"""dashboard.live_chart 테스트 (WAN-147).

핵심은 **로직 이중화 감시**다: 브라우저가 봉내에서 밴드를 다시 계산하므로
`LIVE_BAND_JS`(JS)와 `strategy.realtime_band.RealtimeBand`(파이썬 정본)가 두 벌이 된다.
`test_js_band_matches_realtime_band`가 Node로 그 JS를 **실제로 실행해** 두 구현이 같은
값을 내는지 고정한다 — 라벨과 실제가 갈라지는 사고(WAN-91/95/100/112)를 동작으로 막는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from dashboard.live_chart import (
    LIVE_BAND_JS,
    band_js_params,
    build_live_config,
    live_stream_url,
)
from strategy.models import ConfluenceParams, DeviationFilterParams
from strategy.realtime_band import RealtimeBand

_NODE = shutil.which("node")

_STEP = 3_600_000


def _df(n: int, *, start_close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [i * _STEP for i in range(n)],
            "open": [start_close + i for i in range(n)],
            "high": [start_close + i + 2 for i in range(n)],
            "low": [start_close + i - 2 for i in range(n)],
            "close": [start_close + i * 1.5 for i in range(n)],
            "volume": [10.0] * n,
        }
    )


def _run_js_band(cases: list[dict[str, object]]) -> list[float | None]:
    """`LIVE_BAND_JS`를 Node로 실행해 케이스별 밴드 값을 받는다."""
    script = (
        LIVE_BAND_JS
        + "\nconst cases = JSON.parse(process.argv[2]);\n"
        + "const out = cases.map((c) => computeLiveBand(c.closes, c.price, c.params));\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    assert _NODE is not None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "band.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run(  # noqa: S603 - 테스트가 만든 스크립트만 실행한다
            [_NODE, str(path), json.dumps(cases)],
            capture_output=True,
            text=True,
            check=True,
        )
    result: list[float | None] = json.loads(proc.stdout)
    return result


@pytest.mark.skipif(_NODE is None, reason="Node가 없어 JS 패리티를 검증할 수 없다")
def test_js_band_matches_realtime_band() -> None:
    """JS `computeLiveBand` == 파이썬 `RealtimeBand.value` (정본은 파이썬)."""
    filters = [
        # 채택 기본값(볼린저 SMA20 ± 2σ).
        DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0),
        DeviationFilterParams(anchor="sma", sma_length=5, width_kind="pct", width_value=0.02),
        DeviationFilterParams(anchor="close", sma_length=20, width_kind="pct", width_value=0.01),
        DeviationFilterParams(anchor="close", sma_length=8, width_kind="stdev", width_value=1.5),
    ]
    # 큰 가격(BTC 스케일)과 작은 가격을 함께 넣어 부동소수점 상쇄 오차까지 본다.
    price_seeds = [
        [60_000.0 + i * 137.25 for i in range(30)],
        [1.2345 + i * 0.0007 for i in range(30)],
        [100.0 - i * 3.5 for i in range(30)],
    ]

    cases: list[dict[str, object]] = []
    expected: list[float | None] = []
    for filter_params in filters:
        need = max(filter_params.sma_length - 1, 0)
        for closes in price_seeds:
            for direction_sign in (1, -1):
                # 충분히 시딩된 경우와 워밍업(한 개 모자란 경우) 둘 다.
                for window in (closes[-need:] if need else [], closes[-need:][1:]):
                    live_price = closes[-1] * 1.003
                    band = RealtimeBand.seed_from_closed(window, filter_params)
                    expected.append(band.value(live_price, direction_sign))
                    cases.append(
                        {
                            "closes": list(window),
                            "price": live_price,
                            "params": band_js_params(filter_params, direction_sign),
                        }
                    )

    actual = _run_js_band(cases)
    assert len(actual) == len(expected)
    for got, want, case in zip(actual, expected, cases, strict=True):
        if want is None:
            assert got is None, case
        else:
            assert got is not None, case
            assert got == pytest.approx(want, rel=1e-12, abs=1e-12), case


@pytest.mark.skipif(_NODE is None, reason="Node가 없어 JS 패리티를 검증할 수 없다")
def test_js_band_warmup_returns_null() -> None:
    """확정봉이 `sma_length-1`개에 못 미치면 JS도 값을 지어내지 않는다."""
    filter_params = DeviationFilterParams(
        anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0
    )
    values = _run_js_band(
        [
            {
                "closes": [100.0, 101.0],
                "price": 102.0,
                "params": band_js_params(filter_params, 1),
            }
        ]
    )
    assert values == [None]


def test_live_stream_url_uses_binance_futures_kline() -> None:
    assert live_stream_url("BTC/USDT:USDT", "15m") == (
        "wss://fstream.binance.com/ws/btcusdt@kline_15m"
    )


def test_build_live_config_seeds_band_window_from_closed_bars() -> None:
    frame = _df(60)
    conf = ConfluenceParams()
    config = build_live_config(
        frame, symbol="BTCUSDT", timeframe="1h", conf_params=conf, band_color="#fff"
    )
    assert config is not None
    assert config.interval_ms == _STEP
    assert config.last_bar_open_ms == int(frame["open_time"].iloc[-1])
    filter_params = conf.deviation_filter
    assert filter_params is not None
    # 20번째 표본 자리는 현재가 몫이라 창은 `sma_length-1`개다(`RealtimeBand`와 같은 계약).
    assert config.band_closes is not None
    assert len(config.band_closes) == filter_params.sma_length - 1
    assert list(config.band_closes) == frame["close"].tolist()[-(filter_params.sma_length - 1) :]
    assert config.band_params == band_js_params(filter_params, 1)


def test_build_live_config_disabled_for_unsupported_timeframe_and_empty_frame() -> None:
    conf = ConfluenceParams()
    assert (
        build_live_config(
            _df(60), symbol="BTCUSDT", timeframe="7m", conf_params=conf, band_color="#fff"
        )
        is None
    )
    assert (
        build_live_config(
            _df(0), symbol="BTCUSDT", timeframe="1h", conf_params=conf, band_color="#fff"
        )
        is None
    )


def test_build_live_config_drops_band_when_not_live_computable() -> None:
    """`atr` 폭·필터 없음·창 부족이면 밴드 라이브를 끄되 캔들 라이브는 남긴다."""
    conf_atr = ConfluenceParams(
        deviation_filter=DeviationFilterParams(
            anchor="sma", sma_length=20, width_kind="atr", width_value=1.0
        )
    )
    config = build_live_config(
        _df(60), symbol="BTCUSDT", timeframe="1h", conf_params=conf_atr, band_color="#fff"
    )
    assert config is not None and config.band_closes is None and config.band_color is None

    config = build_live_config(
        _df(60), symbol="BTCUSDT", timeframe="1h", conf_params=None, band_color="#fff"
    )
    assert config is not None and config.band_closes is None

    # 확정봉이 창(19개)보다 적으면 밴드만 끈다.
    config = build_live_config(
        _df(5), symbol="BTCUSDT", timeframe="1h", conf_params=ConfluenceParams(), band_color="#fff"
    )
    assert config is not None and config.band_closes is None


def test_live_payload_shape() -> None:
    config = build_live_config(
        _df(60),
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        conf_params=ConfluenceParams(),
        band_color="#00e5ff",
    )
    assert config is not None
    payload = config.to_payload()
    assert payload["streamUrl"] == "wss://fstream.binance.com/ws/btcusdt@kline_15m"
    assert payload["bandColor"] == "#00e5ff"
    assert payload["intervalMs"] == 900_000
    assert isinstance(payload["bandCloses"], list)
    assert set(payload).issuperset({"symbol", "timeframe", "lastBarOpenMs", "liveColors"})


def test_build_live_config_ignores_unclosed_tail_bars() -> None:
    """미확정 봉(리샘플 꼬리)은 밴드 창·기준 시각에서 제외한다."""
    frame = _df(40)
    frame["closed"] = [True] * 39 + [False]
    config = build_live_config(
        frame, symbol="BTCUSDT", timeframe="1h", conf_params=ConfluenceParams(), band_color="#fff"
    )
    assert config is not None
    assert config.last_bar_open_ms == int(frame["open_time"].iloc[-2])
    assert config.band_closes is not None
    assert list(config.band_closes) == frame["close"].tolist()[-20:-1]
