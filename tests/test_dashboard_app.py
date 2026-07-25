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


@pytest.fixture
def long_span_db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """기본 기간(최근 180일)보다 훨씬 긴 시리즈 — 창 축소가 실제로 일어나는지 보려면 필요."""
    db_path = str(tmp_path / "long.db")
    day = 86_400_000
    with OhlcvStore(db_path) as store:
        store.upsert_candles(
            Candle("BTC/USDT:USDT", "1d", i * day, 100.0 + i, 105.0 + i, 95.0 + i, 100.0 + i, 10.0)
            for i in range(800)
        )
    monkeypatch.setenv("ALPHABLOCK_DB_PATH", db_path)
    get_settings.cache_clear()
    yield db_path
    get_settings.cache_clear()


def test_window_load_matches_the_old_full_load_slice(long_span_db_path: str) -> None:
    """WAN-188 회귀 고정: 고른 구간만 읽어도 **캔들 집합이 예전과 같다**.

    예전에는 전 구간을 로드해 `full_df[(>= start) & (<= end)]`로 잘랐다. 지금은 SQL로
    그 구간만 읽는데, `load_ohlcv`의 `end_ms`가 **배타**라 `+1`을 넘긴다 — 이 `+1`이
    빠지면 마지막 봉 하나가 조용히 사라진다(화면과 지표가 어긋나는 자리).
    """
    from dashboard.data_access import load_ohlcv, series_bounds

    bounds = series_bounds(long_span_db_path, "BTC/USDT:USDT", "1d")
    assert bounds is not None
    first_ms, last_ms = bounds
    full = load_ohlcv(long_span_db_path, "BTC/USDT:USDT", "1d")

    for start_ms, end_ms in ((first_ms, last_ms), (first_ms + 10 * 86_400_000, last_ms - 1)):
        expected = full[(full["open_time"] >= start_ms) & (full["open_time"] <= end_ms)]
        actual = load_ohlcv(
            long_span_db_path, "BTC/USDT:USDT", "1d", start_ms=start_ms, end_ms=end_ms + 1
        )
        assert list(actual["open_time"]) == list(expected["open_time"])


def test_analysis_defaults_to_recent_window_not_the_whole_history(long_span_db_path: str) -> None:
    """기본 기간이 전 구간이 아니다(WAN-188) — 전 구간은 옵트인 체크박스로만."""
    from dashboard.app import _DEFAULT_WINDOW_DAYS, _ms_to_datetime
    from dashboard.data_access import series_bounds

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    period = next(s for s in at.slider if s.label == "기간")
    start_dt, end_dt = period.value
    # 800일치를 넣었는데 기본 창은 최근 180일이다.
    assert (end_dt - start_dt).days == _DEFAULT_WINDOW_DAYS
    bounds = series_bounds(long_span_db_path, "BTC/USDT:USDT", "1d")
    assert bounds is not None
    assert start_dt > _ms_to_datetime(bounds[0])
    # 옛 구간을 보는 길이 사라지지는 않았다.
    assert "전 구간 보기(느림)" in {c.label for c in at.checkbox}


def test_full_range_checkbox_really_widens_the_window(long_span_db_path: str) -> None:
    """옵트인이 라벨만 붙은 게 아니라 **실제로 전 구간을 편다**(WAN-188).

    켰는데 창이 그대로면 사용자는 옛 구간을 볼 길을 잃는다 — 이 저장소가 반복해서 당한
    "라벨은 바뀌었는데 동작은 그대로"(WAN-91/95/112/123) 부류를 동작으로 막는다.
    """
    from dashboard.app import _ms_to_datetime
    from dashboard.data_access import series_bounds

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    assert not at.exception
    narrow_start, _ = next(s for s in at.slider if s.label == "기간").value

    next(c for c in at.checkbox if c.label == "전 구간 보기(느림)").set_value(True)
    at.run(timeout=60)
    assert not at.exception

    wide_start, _ = next(s for s in at.slider if s.label == "기간").value
    bounds = series_bounds(long_span_db_path, "BTC/USDT:USDT", "1d")
    assert bounds is not None
    assert wide_start < narrow_start
    assert wide_start == _ms_to_datetime(bounds[0])


def test_analysis_display_lines_are_off_by_default(seeded_db_path: str) -> None:
    """표시선 6개는 기본 꺼짐(WAN-188) — 페이로드의 58%인데 채택 규칙이 안 쓴다."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    line_toggles = [c for c in at.checkbox if c.label.startswith(("EMA ", "VWMA "))]
    assert line_toggles, "표시선 토글이 사라졌다 — 끄는 것이지 없애는 게 아니다"
    assert not any(c.value for c in line_toggles)


def test_run_config_badge_text_reports_current_settings() -> None:
    """WAN-65: 배지 문구가 진입 방식·RSI·사이징·병합·펀딩비 반영 여부를 담는다."""
    conf = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar", max_zone_width_atr=None)
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


def test_app_trade_table_is_korean_time_and_keeps_engine_labels(seeded_db_path: str) -> None:
    """WAN-146: 거래 표가 KST 안내와 함께 그려지고, 엔진 라벨은 표 밖에 보존된다."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    assert "거래 목록" in [s.value for s in at.subheader]
    captions = [c.value for c in at.caption]
    assert any("한국시간(KST)" in c for c in captions)
    # 표 본문에서 뺀 엔진 라벨 6개는 삭제가 아니라 이동이다(expander 안 캡션).
    assert any("entry_mode=" in c and "funding_coverage=" in c for c in captions)


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


def _seed_backtest_run(db_path: str) -> str:
    """저장된 거래 탭이 읽을 실행 하나를 DB에 넣는다 (WAN-106)."""
    from backtest.trade_store import BacktestRunStore, RunFingerprint
    from tests.test_trade_display_frame import _win_then_loss

    fingerprint = RunFingerprint(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        entry_mode="zone_limit",
        fill="baseline",
        confluence_json=ConfluenceParams().model_dump_json(),
        order_block_json=OrderBlockParams().model_dump_json(),
        config_json=BacktestConfig().model_dump_json(),
        revision="abc1234",
    )
    with BacktestRunStore(db_path) as store:
        return store.save_run(fingerprint, _win_then_loss())


def test_saved_trades_tab_hints_how_to_persist_when_empty(seeded_db_path: str) -> None:
    """적재된 게 없으면 "빈 화면"이 아니라 **넣는 방법**을 보여준다 (WAN-106)."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    assert any("--persist" in i.value for i in at.info)


def test_saved_trades_tab_renders_stored_trades_with_fingerprint(seeded_db_path: str) -> None:
    """WAN-106: 계산 없이 조회한 거래 표 + 실행 지문 배지 + 청산사유 필터가 그려진다."""
    _seed_backtest_run(seeded_db_path)

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)

    assert not at.exception
    captions = [c.value for c in at.caption]
    # 지금 보고 있는 게 어느 엔진의 거래인지가 화면에서 사라지면 안 된다(WAN-65/95).
    assert any("실행 지문" in c and "B안(존-지정가)" in c for c in captions)
    radio_labels = {r.label for r in at.radio}
    assert "청산사유" in radio_labels
    # 사용자의 원 요청("어디서 손절났는지")이 선택지로 실제로 있다.
    reason_radio = next(r for r in at.radio if r.label == "청산사유")
    assert "손절" in list(reason_radio.options)
