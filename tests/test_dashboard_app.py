"""dashboard.app 스모크 테스트 (streamlit `AppTest`).

실제 렌더링을 구동해 예외 없이 화면이 그려지는지, 데이터 유무에 따른
분기가 동작하는지 확인한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from backtest.models import BacktestConfig
from config.settings import get_settings
from dashboard.app import _run_config_badge_text
from data.models import Candle
from data.storage import OhlcvStore
from execution import PositionSizingParams
from strategy.models import ConfluenceParams, OrderBlockParams

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


def test_run_config_badge_text_reports_current_settings() -> None:
    """WAN-65: 배지 문구가 진입 방식·RSI·사이징·병합·펀딩비 반영 여부를 담는다."""
    conf = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar")
    ob = OrderBlockParams(combine_obs=True)
    sized = BacktestConfig(
        risk_sizing=PositionSizingParams(risk_per_trade=0.01), funding_enabled=True
    )
    text = _run_config_badge_text(conf, ob, sized)
    assert "A안" in text
    assert "확정봉" in text
    assert "리스크 1.0%" in text
    assert "병합: ON" in text
    assert "펀딩비: 반영됨" in text


def test_run_config_badge_text_flags_full_position_mode() -> None:
    """risk_sizing=None(전액 진입)이면 배지 문구에 "사이징 미적용"이 드러난다."""
    conf = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    ob = OrderBlockParams(combine_obs=False)
    unsized = BacktestConfig(risk_sizing=None, funding_enabled=False)
    text = _run_config_badge_text(conf, ob, unsized)
    assert "B안" in text
    assert "실시간" in text
    assert "사이징 미적용" in text
    assert "병합: OFF" in text
    assert "펀딩비: 미반영" in text


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
    # 분석 탭 상단 실행 설정 배지(WAN-65)가 그려진다. WAN-91부터 `funding_enabled`
    # 기본값이 True인데, 대시보드는 아직 실제 funding_rates를 조회해 넘기지 않으므로
    # 커버리지가 0%로 나와 "비정상" 취급 — caption이 아니라 warning으로 렌더된다.
    # (조용히 caption으로 숨기지 않는 것 자체가 WAN-91의 의도, `_render_run_config_badge` 참고.)
    warnings = [w.value for w in at.warning]
    assert any("진입:" in w and "사이징:" in w for w in warnings)


def test_app_health_tab_renders_without_error(seeded_db_path: str) -> None:
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    # Health 탭이 실제로 그려졌는지 소제목으로 확인한다.
    subheaders = [s.value for s in at.subheader]
    assert "데이터 신선도" in subheaders
    assert "실시간 러너" in subheaders


def test_app_auto_refresh_toggle_and_last_updated(seeded_db_path: str) -> None:
    """자동 새로고침(WAN-48): 사이드바 토글과 마지막 갱신 시각 캡션이 그려진다."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    toggle_labels = {t.label for t in at.toggle}
    assert "운영 상태 자동 갱신" in toggle_labels
    # Health 탭 상단에 마지막 갱신 시각(UTC)이 표시된다.
    captions = [c.value for c in at.caption]
    assert any(c.startswith("마지막 갱신:") for c in captions)


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
