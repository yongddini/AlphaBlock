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
from paper.store import PaperTradeRecord
from strategy.models import ConfluenceParams, OrderBlockParams

_STEP = 3_600_000


def _open_backtest_tab(at: AppTest, button_key: str, *, timeout: int = 60) -> None:
    """WAN-220: 백테스트(분석·저장된 거래) 탭은 지연 로딩된다 — cold start에서는
    "불러오기" 버튼만 보이므로, 내용을 단언하기 전에 그 버튼을 눌러 로드한다.
    """
    button = next(b for b in at.button if b.key == button_key)
    button.click()
    at.run(timeout=timeout)


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
    """기본 기간이 전 구간이 아니다(WAN-188) — 전 구간은 옵트인 체크박스로만.

    WAN-199 이후 분석 탭은 적재된 B안 실행을 조회하므로, 뷰 슬라이더가 뜨려면 그 (심볼·TF)의
    실행이 하나 있어야 한다 — 성과·거래는 그 실행 기준이고 슬라이더는 차트 뷰만 좁힌다.
    """
    from dashboard.app import _DEFAULT_WINDOW_DAYS, _ms_to_datetime
    from dashboard.data_access import series_bounds

    _seed_backtest_run(long_span_db_path, timeframe="1d")
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    assert not at.exception
    _open_backtest_tab(at, "load_analysis_tab")

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

    _seed_backtest_run(long_span_db_path, timeframe="1d")
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    assert not at.exception
    _open_backtest_tab(at, "load_analysis_tab")
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
    _seed_backtest_run(seeded_db_path)
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    _open_backtest_tab(at, "load_analysis_tab")

    assert not at.exception
    line_toggles = [c for c in at.checkbox if c.label.startswith(("EMA ", "VWMA "))]
    assert line_toggles, "표시선 토글이 사라졌다 — 끄는 것이지 없애는 게 아니다"
    assert not any(c.value for c in line_toggles)


def test_period_widget_datetime_conversion_is_kst_and_roundtrips() -> None:
    """기간/재생 위젯은 KST 벽시계로 보이되 질의 ms는 UTC 등가 그대로다(WAN-193).

    사용자 결정(2026-07-26): 화면 날짜는 전부 KST. 단 저장·질의는 UTC 등가 ms 불변이라
    경계 왕복(`_ms_to_datetime`↔`_datetime_to_ms`)이 원 ms를 되돌려야 한다 — "라벨만
    KST, 데이터 축은 UTC" 원칙을 동작으로 고정한다.
    """
    from datetime import UTC, datetime

    from dashboard.app import _datetime_to_ms, _ms_to_datetime

    # 2021-01-01 00:00:00 UTC → KST 벽시계는 09:00, 같은 순간(ms)을 가리킨다.
    ms = int(datetime(2021, 1, 1, 0, 0, tzinfo=UTC).timestamp() * 1000)
    dt = _ms_to_datetime(ms)
    assert (dt.hour, dt.minute) == (9, 0)  # KST 벽시계
    assert dt.year == 2021 and dt.month == 1 and dt.day == 1

    # 위젯이 벽시계만 돌려줘도(tz 유무 무관) 같은 ms로 되돌아온다 → 질의는 UTC 등가 불변.
    assert _datetime_to_ms(dt) == ms
    assert _datetime_to_ms(dt.replace(tzinfo=None)) == ms


def test_run_config_badge_text_reports_current_settings() -> None:
    """WAN-65: 배지 문구가 진입 방식·RSI·사이징·병합·펀딩비 반영 여부를 담는다.

    진입 방식은 채택 엔진(B안 존-지정가) 단독이다 — A안(종가) 배지 분기는
    WAN-208에서 제거됐다.
    """
    conf = ConfluenceParams(rsi_mode="closed_bar", max_zone_width_atr=None)
    ob = OrderBlockParams(combine_obs=True)
    sized = BacktestConfig(
        risk_sizing=PositionSizingParams(risk_per_trade=0.01), funding_enabled=True
    )
    text = _run_config_badge_text(conf, ob, sized)
    assert "B안(존-지정가)" in text
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
    # WAN-199: 분석 탭은 적재된 B안 실행을 조회한다 — 그 실행을 하나 넣어 둔다.
    _seed_backtest_run(seeded_db_path)
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    assert at.title[0].value == "AlphaBlock — 통합 트레이딩 대시보드"
    _open_backtest_tab(at, "load_analysis_tab", timeout=30)

    assert not at.exception
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
    # 적재된 실행(_win_then_loss)은 거래 2건 — 조회한 값이 그대로 지표에 뜬다(WAN-199).
    metrics_by_label = {m.label: m.value for m in at.metric}
    assert metrics_by_label["Trades"] == "2"
    # 분석 탭 상단 실행 설정 배지(WAN-65)가 그려진다. 지문의 `entry_mode`가 zone_limit이라
    # 배지가 자동으로 "B안(존-지정가)"로 뜬다(WAN-199 완료 기준). 기본 BacktestConfig는
    # risk_sizing=None이라 "사이징 미적용"으로 warning 색으로 강조된다.
    warnings = [w.value for w in at.warning]
    assert any("진입: B안(존-지정가)" in w and "사이징:" in w for w in warnings)


def test_app_trade_table_is_korean_time_and_keeps_engine_labels(seeded_db_path: str) -> None:
    """WAN-146: 거래 표가 KST 안내와 함께 그려지고, 엔진 라벨은 표 밖에 보존된다."""
    _seed_backtest_run(seeded_db_path)
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    _open_backtest_tab(at, "load_analysis_tab", timeout=30)

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


def _seed_backtest_run(db_path: str, *, timeframe: str = "1h") -> str:
    """저장된 거래·분석 탭이 조회할 B안(존-지정가) 실행 하나를 DB에 넣는다 (WAN-106/199)."""
    from backtest.trade_store import BacktestRunStore, RunFingerprint
    from tests.test_trade_display_frame import _win_then_loss

    fingerprint = RunFingerprint(
        symbol="BTC/USDT:USDT",
        timeframe=timeframe,
        entry_mode="zone_limit",
        fill="baseline",
        confluence_json=ConfluenceParams().model_dump_json(),
        order_block_json=OrderBlockParams().model_dump_json(),
        config_json=BacktestConfig().model_dump_json(),
        revision="abc1234",
    )
    with BacktestRunStore(db_path) as store:
        return store.save_run(fingerprint, _win_then_loss())


def test_analysis_tab_hints_to_persist_when_no_zone_limit_run(seeded_db_path: str) -> None:
    """WAN-199: OHLCV는 있는데 이 (심볼·TF)의 B안 실행이 적재돼 있지 않으면, 분석 탭은
    화면에서 A안으로 재계산하지 않고 넣는 방법을 안내한다(조용한 7분 대기 금지)."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    _open_backtest_tab(at, "load_analysis_tab", timeout=30)

    assert not at.exception
    infos = [i.value for i in at.info]
    assert any(
        "BTC/USDT:USDT · 1h" in i and "적재된 채택 엔진(B안 존-지정가) 실행이 없습니다" in i
        for i in infos
    )


def test_saved_trades_tab_hints_how_to_persist_when_empty(seeded_db_path: str) -> None:
    """적재된 게 없으면 "빈 화면"이 아니라 **넣는 방법**을 보여준다 (WAN-106)."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    _open_backtest_tab(at, "load_saved_tab", timeout=30)

    assert not at.exception
    assert any("--persist" in i.value for i in at.info)


def test_saved_trades_tab_renders_stored_trades_with_fingerprint(seeded_db_path: str) -> None:
    """WAN-106: 계산 없이 조회한 거래 표 + 실행 지문 배지 + 청산사유 필터가 그려진다."""
    _seed_backtest_run(seeded_db_path)

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    _open_backtest_tab(at, "load_saved_tab", timeout=30)

    assert not at.exception
    captions = [c.value for c in at.caption]
    # 지금 보고 있는 게 어느 엔진의 거래인지가 화면에서 사라지면 안 된다(WAN-65/95).
    assert any("실행 지문" in c and "B안(존-지정가)" in c for c in captions)
    radio_labels = {r.label for r in at.radio}
    assert "청산사유" in radio_labels
    # 사용자의 원 요청("어디서 손절났는지")이 선택지로 실제로 있다.
    reason_radio = next(r for r in at.radio if r.label == "청산사유")
    assert "손절" in list(reason_radio.options)


def test_backtest_tabs_are_lazy_and_demoted_reference(seeded_db_path: str) -> None:
    """WAN-220: cold start는 백테스트 탭을 자동 로드하지 않는다.

    "라벨은 바뀌었는데 동작은 그대로"(WAN-91/95/112/123) 부류를 동작으로 막는다 —
    로드 전에는 "불러오기" 버튼과 "참고·대조" 안내만 보이고 무거운 성과 지표는 없으며,
    버튼을 눌러야 비로소 조회가 실행된다.
    """
    _seed_backtest_run(seeded_db_path)
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception

    # 두 백테스트 탭의 지연 로딩 버튼이 존재한다(분석·저장된 거래).
    button_keys = {b.key for b in at.button}
    assert {"load_analysis_tab", "load_saved_tab"} <= button_keys

    # 강등된 탭의 "참고·대조" 성격이 화면에 드러난다(약속·기대수익 아님).
    from dashboard.app import _BACKTEST_REFERENCE_NOTE

    assert _BACKTEST_REFERENCE_NOTE in [c.value for c in at.caption]
    assert "참고" in _BACKTEST_REFERENCE_NOTE and "대조" in _BACKTEST_REFERENCE_NOTE

    # 로드 전에는 분석 탭의 백테스트 지표("Total Return")가 그려지지 않는다 → cold start 빠름.
    # (페이퍼 탭은 "총수익률(지갑)" 한글 라벨이라 겹치지 않는다.)
    assert "Total Return" not in {m.label for m in at.metric}

    # 버튼을 누르면 비로소 조회가 실행돼 지표가 뜬다.
    _open_backtest_tab(at, "load_analysis_tab", timeout=30)
    assert not at.exception
    assert "Total Return" in {m.label for m in at.metric}


def test_live_tabs_render_on_cold_start_without_loading_backtest(seeded_db_path: str) -> None:
    """WAN-220 라이브-우선: 라이브·운영 탭(Health)은 cold start에서 즉시 그려진다 —
    백테스트 탭을 열지 않아도 첫 화면에 운영 정보가 있어야 한다."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    subheaders = [s.value for s in at.subheader]
    assert "데이터 신선도" in subheaders
    assert "실시간 러너" in subheaders


def _paper_record(
    *,
    exit_time: int,
    equity_after: float | None,
    realized_pnl: float | None = None,
    symbol: str = "BTC/USDT:USDT",
    net_pct: float = 1.0,
) -> PaperTradeRecord:
    from strategy.models import OrderBlockDirection, SignalExitReason

    return PaperTradeRecord(
        symbol=symbol,
        timeframe="1h",
        direction=OrderBlockDirection.BULLISH,
        entry_time=exit_time - _STEP,
        entry_price=100.0,
        exit_time=exit_time,
        exit_price=101.0,
        reason=SignalExitReason.TAKE_PROFIT,
        gross_pct=net_pct,
        fee_pct=0.0,
        funding_pct=0.0,
        net_pct=net_pct,
        realized_pnl=realized_pnl,
        equity_after=equity_after,
    )


def test_wallet_balance_sums_all_cells_not_last_snapshot() -> None:
    """WAN-237: 여러 칸 동시거래에서 현재 잔고는 지갑 합계다(마지막 스냅샷 아님).

    실측 재현 — 손절 2건(BNB·DOGE)이 각자 초기자본 10,000에서 자기 손실만 뺀 채로
    기록된 장부(칸별 독립 자본)에서, 현재 잔고는 초기자본 + 두 손실의 합이어야 하고
    (현재 잔고 − 초기자본) == 총 손익이 성립한다.
    """
    from dashboard.app import _wallet_balance

    records = [
        _paper_record(
            exit_time=1 * _STEP,
            symbol="BNB/USDT:USDT",
            realized_pnl=-37.881,
            equity_after=9_962.119,
        ),
        _paper_record(
            exit_time=2 * _STEP,
            symbol="DOGE/USDT:USDT",
            realized_pnl=-34.8033,
            equity_after=9_965.1967,
        ),
    ]
    initial = 10_000.0
    balance = _wallet_balance(records, initial_equity=initial)
    total_pnl = -37.881 + -34.8033
    assert balance is not None
    # 마지막 거래 스냅샷(9,965.20)이 아니라 지갑 합계여야 한다.
    assert balance == pytest.approx(initial + total_pnl)
    assert balance == pytest.approx(9_927.3157)
    # 완료 기준 1: 현재 잔고 − 초기자본 == 총 손익.
    assert balance - initial == pytest.approx(total_pnl)


def test_wallet_balance_none_when_any_row_lacks_dollar_pnl() -> None:
    """WAN-237/207: 달러 손익이 없는 옛 행이 섞이면 None(재구성 불가) — 억지 역산 금지."""
    from dashboard.app import _wallet_balance

    records = [
        _paper_record(exit_time=1 * _STEP, realized_pnl=-40.0, equity_after=9_960.0),
        _paper_record(exit_time=2 * _STEP, realized_pnl=None, equity_after=None),  # 옛 %-only 행
    ]
    assert _wallet_balance(records, initial_equity=10_000.0) is None


def test_wallet_balance_none_when_empty() -> None:
    """거래가 없으면 잔고 재구성 불가(None)."""
    from dashboard.app import _wallet_balance

    assert _wallet_balance([], initial_equity=10_000.0) is None
