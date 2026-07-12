"""dashboard.app 스모크 테스트 (streamlit `AppTest`).

실제 렌더링을 구동해 예외 없이 화면이 그려지는지, 데이터 유무에 따른
분기가 동작하는지 확인한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config.settings import get_settings
from data.models import Candle
from data.storage import OhlcvStore

_STEP = 3_600_000


@pytest.fixture
def seeded_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    db_path = str(tmp_path / "ohlcv.db")
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            Candle(
                "BTC/USDT:USDT",
                "1h",
                i * _STEP,
                100.0 + i,
                105.0 + i,
                95.0 + i,
                100.0 + i,
                10.0,
            )
            for i in range(30)
        )
    monkeypatch.setenv("ALPHABLOCK_DB_PATH", db_path)
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()


def test_app_renders_price_chart_and_metrics_when_data_available(seeded_db_path: str) -> None:
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    assert at.title[0].value == "AlphaBlock — 통합 트레이딩 대시보드"
    # 분석 탭의 백테스트 성과 지표 6종이 실제로 그려졌는지 라벨로 확인한다.
    # 개수(len)로 단언하지 않는다: streamlit 1.59+는 모든 탭을 한 번에 렌더하므로
    # Health/페이퍼 탭의 지표(러너 상태 등)까지 at.metric 에 섞여 개수가 환경에 따라 달라진다.
    metric_labels = {m.label for m in at.metric}
    assert {
        "Total Return",
        "Max Drawdown",
        "Win Rate",
        "Profit Factor",
        "Sharpe",
        "Trades",
    } <= metric_labels
    # 시드 데이터로는 시그널이 없어 거래 0건 — 값도 의미 있게 검증한다.
    metrics_by_label = {m.label: m.value for m in at.metric}
    assert metrics_by_label["Trades"] == "0"


def test_app_health_tab_renders_without_error(seeded_db_path: str) -> None:
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    # Health 탭이 실제로 그려졌는지 소제목으로 확인한다.
    subheaders = [s.value for s in at.subheader]
    assert "데이터 신선도" in subheaders
    assert "실시간 러너" in subheaders


def test_app_shows_warning_when_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHABLOCK_DB_PATH", str(tmp_path / "empty.db"))
    get_settings.cache_clear()
    try:
        at = AppTest.from_file("dashboard/app.py")
        at.run(timeout=30)

        assert not at.exception
        assert at.warning
    finally:
        get_settings.cache_clear()
