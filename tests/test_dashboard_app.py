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
    assert len(at.metric) == 6


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
