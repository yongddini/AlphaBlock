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
    _open_backtest_tab(at, "load_reference_tab")

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
    _open_backtest_tab(at, "load_reference_tab")
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
    _open_backtest_tab(at, "load_reference_tab")

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
    _open_backtest_tab(at, "load_reference_tab", timeout=30)

    assert not at.exception
    # 병합 화면(WAN-289)의 성과 카드 6종(목업 확정 · 한글)이 실제로 그려졌는지 라벨로
    # 확인한다. 개수(len)로 단언하지 않는다: streamlit 1.59+는 모든 탭을 한 번에
    # 렌더하므로 Health/페이퍼 탭의 지표(러너 상태 등)까지 at.metric 에 섞여 개수가
    # 환경에 따라 달라진다.
    metric_labels = {m.label for m in at.metric}
    assert {
        "총수익",
        "MDD",
        "승률",
        "거래수",
        "체결률",
        "최종 시드",
    } <= metric_labels
    # 옛 영문 카드(WAN-289 이전)가 라벨만 남아 있지 않다.
    assert "Total Return" not in metric_labels
    assert "Sharpe" not in metric_labels
    # 적재된 실행(_win_then_loss)은 거래 2건 — 조회한 값이 그대로 지표에 뜬다(WAN-199).
    metrics_by_label = {m.label: m.value for m in at.metric}
    assert metrics_by_label["거래수"] == "2"
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
    _open_backtest_tab(at, "load_reference_tab", timeout=30)

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
    # 목업 카드 3종의 셋째 — DB 무결성(WAN-289 §3). doctor와 같은 판정을 캐시로 재사용.
    assert "DB 무결성" in subheaders
    metric_labels = {m.label for m in at.metric}
    assert "quick_check" in metric_labels


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
    _open_backtest_tab(at, "load_reference_tab", timeout=30)

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
    _open_backtest_tab(at, "load_reference_tab", timeout=30)

    assert not at.exception
    assert any("--persist" in i.value for i in at.info)


def test_saved_trades_tab_renders_stored_trades_with_fingerprint(seeded_db_path: str) -> None:
    """WAN-106→289: 계산 없이 조회한 거래 표 + 실행 지문 배지 + 청산사유 칩이 그려진다."""
    _seed_backtest_run(seeded_db_path)

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception
    _open_backtest_tab(at, "load_reference_tab", timeout=30)

    assert not at.exception
    captions = [c.value for c in at.caption]
    # 지금 보고 있는 게 어느 엔진의 거래인지가 화면에서 사라지면 안 된다(WAN-65/95).
    assert any("실행 지문" in c and "B안(존-지정가)" in c for c in captions)
    radio_labels = {r.label for r in at.radio}
    assert "청산사유" in radio_labels
    # 사용자의 원 요청("어디서 손절났는지")이 선택지로 실제로 있고, 어휘는 잔고 탭과
    # 같은 칩 3갈래다(WAN-289 — `live_board.REASON_FILTER_OPTIONS` 재사용).
    reason_radio = next(r for r in at.radio if r.label == "청산사유")
    assert list(reason_radio.options) == ["전체", "익절만", "손절만"]


def test_backtest_tabs_are_lazy_and_demoted_reference(seeded_db_path: str) -> None:
    """WAN-220: cold start는 백테스트 탭을 자동 로드하지 않는다.

    "라벨은 바뀌었는데 동작은 그대로"(WAN-91/95/112/123) 부류를 동작으로 막는다 —
    로드 전에는 "불러오기" 버튼과 "참고·대조" 안내만 보이고 무거운 성과 지표는 없으며,
    버튼을 눌러야 비로소 조회가 실행된다.

    ⚠️ WAN-245에서 분석·저장된 거래가 **한 탭으로 합쳐졌다**(사용자 결정 2026-08-11) —
    지연 로딩 버튼도 하나다. 강등 원칙(WAN-220)은 그대로다.
    """
    _seed_backtest_run(seeded_db_path)
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=30)
    assert not at.exception

    # 합쳐진 백테스트 탭의 지연 로딩 버튼이 존재한다(분석 + 저장된 거래).
    button_keys = {b.key for b in at.button}
    assert "load_reference_tab" in button_keys

    # 강등된 탭의 "참고·대조" 성격이 화면에 드러난다(약속·기대수익 아님).
    from dashboard.app import _BACKTEST_REFERENCE_NOTE

    assert _BACKTEST_REFERENCE_NOTE in [c.value for c in at.caption]
    assert "참고" in _BACKTEST_REFERENCE_NOTE and "대조" in _BACKTEST_REFERENCE_NOTE

    # 로드 전에는 병합 화면의 백테스트 카드("총수익")가 그려지지 않는다 → cold start 빠름.
    # (잔고 탭은 "누적 실현손익" 등 다른 라벨이라 겹치지 않는다.)
    assert "총수익" not in {m.label for m in at.metric}

    # 버튼을 누르면 비로소 조회가 실행돼 카드가 뜬다.
    _open_backtest_tab(at, "load_reference_tab", timeout=30)
    assert not at.exception
    assert "총수익" in {m.label for m in at.metric}


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


# --- WAN-245: 차트-우선 재설계 ------------------------------------------------


def test_main_chart_tab_renders_on_cold_start_without_a_load_button(seeded_db_path: str) -> None:
    """첫 화면 = 차트(WAN-245). 심볼·TF 선택이 **버튼 없이** 바로 그려진다.

    백테스트 탭은 여전히 지연 로딩(버튼)인데 메인 차트는 아니다 — 최근 봉 + 존 4개만
    읽으므로 cold start에서 바로 그려도 가볍다(WAN-202 흡수의 핵심).
    """
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    labels = {s.label for s in at.selectbox}
    assert "심볼" in labels
    radio_labels = {r.label for r in at.radio}
    assert "타임프레임" in radio_labels
    assert "현재 오픈 포지션" in {s.value for s in at.subheader}


def test_main_chart_timeframe_toggle_offers_derived_2h(seeded_db_path: str) -> None:
    """2h가 토글에 있다 — 저장된 시리즈에서 만들면 파생 TF라 영영 안 뜬다(WAN-24).

    옛 대시보드의 TF 드롭다운은 `list_series()`(물리 저장 행)에서 만들어져 2h가 아예
    선택지에 없었다. 이 화면은 러너 설정에서 목록을 만들어 그 구멍을 막는다.
    """
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    timeframe_radio = next(r for r in at.radio if r.label == "타임프레임")
    # 라벨은 목업대로 **원문 TF**다(한글이 아니다) — 한글은 차트 좌상단 OHLC 범례에서만.
    assert list(timeframe_radio.options[:4]) == ["15m", "1h", "2h", "4h"]


def test_main_chart_switches_timeframe_without_error(seeded_db_path: str) -> None:
    """TF를 바꿔도 예외 없이 다시 그린다(파생 2h 경로 포함)."""
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    assert not at.exception

    next(r for r in at.radio if r.label == "타임프레임").set_value("2h")
    at.run(timeout=60)

    assert not at.exception


def test_balance_tab_shows_the_wallet_equity_curve_section(seeded_db_path: str) -> None:
    """잔고 탭(구 「페이퍼 성과」)에 지갑 곡선 자리가 생긴다(WAN-245).

    거래가 없으면 곡선 대신 안내가 뜨므로 여기서는 소제목만 확인한다 — 곡선·MDD 구간의
    숫자는 `tests/test_dashboard_live_board.py`가 순수 함수로 고정한다.
    """
    from paper.store import PaperTradeStore

    with PaperTradeStore(seeded_db_path) as store:
        store.upsert_record(
            _paper_record(exit_time=5 * _STEP, equity_after=10_050.0, realized_pnl=50.0)
        )
        store.upsert_record(
            _paper_record(exit_time=6 * _STEP, equity_after=10_020.0, realized_pnl=-30.0)
        )

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    # 목업의 카드 5개 + MDD 구간 캡션이 잔고 탭에 그려진다.
    metric_labels = {m.label for m in at.metric}
    assert {"지갑 잔고", "누적 실현손익", "미실현손익", "MDD (최대 낙폭)", "승률 · 거래"} <= (
        metric_labels
    )
    assert any(c.value.startswith("에쿼티 곡선") for c in at.caption)
    # 청산사유 칩(전체/익절만/손절만)이 목업대로 세 갈래다.
    reason_radio = next(r for r in at.radio if r.label == "청산사유")
    assert list(reason_radio.options) == ["전체", "익절만", "손절만"]


def test_status_pill_reflects_the_real_runner_state_not_a_decoration(
    seeded_db_path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """상단 pill(목업)의 점 색은 **실제 판정**에서 온다 — 늘 초록인 장식이면 러너가
    죽어도 화면이 멀쩡해 보인다. 상태파일이 없으면 「폴링 기록 없음」이다."""
    monkeypatch.setenv("ALPHABLOCK_LIVE_RUNTIME_STATE_PATH", str(tmp_path / "state.json"))
    get_settings.cache_clear()
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    pill = next(c.value for c in at.caption if "페이퍼 러너" in c.value)
    assert "폴링 기록 없음" in pill  # 러너를 안 돌린 환경
    assert not pill.startswith("🟢")  # 죽은/모르는 러너를 초록으로 칠하지 않는다
    assert "틱 피드" in pill  # WAN-256 기본값(live_tick_feed_enabled=True)


def test_tab_labels_follow_the_mockup(seeded_db_path: str) -> None:
    """탭 이름이 목업 표기와 같다(차트 · 잔고 · 거래내역 · Health · 분석 · 거래).

    ⚠️ 목업은 **4탭**인데 화면은 6탭이다 — 「진입/미진입 장부」(WAN-217/219)와 「거래
    타임라인」(WAN-234)은 사양이 제거를 말한 적이 없어 뒤에 남겨 뒀다(개발자 판단).
    이 테스트는 그 상태를 **명시적으로** 고정한다 — 지우기로 정해지면 여기가 먼저 깨진다.
    """
    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    assert [t.label for t in at.tabs] == [
        "차트",
        "잔고 · 거래내역",
        "진입/미진입 장부",
        "거래 타임라인",
        "Health",
        "분석 · 거래 (참고·대조)",
    ]


def test_full_universe_label_follows_the_adopted_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WAN-318 §6: 「채택 N종목×MTF 전부」 라벨이 좌표에서 파생된다 — 하드코딩 금지.

    예전엔 「9종목」이 문자열 상수였는데 셀 수만 계산돼, 유니버스가 12종목이 된 뒤
    (WAN-307) 화면에 **「9종목 × 4TF = 48셀」이라는 자기모순**이 떴다. 이 저장소가 가장
    경계하는 「라벨과 동작이 어긋남」(WAN-91/95/112/123/159 계열)이 사용자 화면에 그대로
    노출된 것이라, **좌표를 바꾸면 라벨도 바뀌는지**를 동작으로 잠근다.
    """
    import backtest.harness as harness
    from dashboard.app import full_universe_label, full_universe_shape

    # 지금 좌표에서: 숫자가 실제 기본값과 같고 셀 수와 모순되지 않는다.
    n_symbols, n_timeframes, n_cells = full_universe_shape()
    assert n_symbols == len(harness.DEFAULT_SYMBOLS)
    assert n_timeframes == len(harness.DEFAULT_TIMEFRAMES)
    assert n_cells == n_symbols * n_timeframes
    assert full_universe_label() == f"채택 {n_symbols}종목×{n_timeframes}TF 전부 (WAN-290)"

    # 좌표를 바꾸면 라벨이 따라온다(다음 재-베이스라인에서 또 어긋나지 않게).
    monkeypatch.setattr(harness, "DEFAULT_SYMBOLS", ("BTCUSDT", "ETHUSDT"))
    monkeypatch.setattr(harness, "DEFAULT_TIMEFRAMES", ("1h", "4h", "1d"))
    assert full_universe_shape() == (2, 3, 6)
    assert full_universe_label() == "채택 2종목×3TF 전부 (WAN-290)"


def test_full_universe_reads_the_disk_cache_without_the_button(
    seeded_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WAN-297 §1: 야간 크론이 채워 둔 하루는 **버튼 없이** 뜬다(세션이 끊겨도).

    옛 판은 이 모드가 `st.session_state`만 봐서, 크론이 디스크에 48셀을 잘 넣어 두어도
    쳐다보지 않았다 — 앱 재시작·새 브라우저 세션이면 무조건 버튼을 다시 눌러야 했다
    (사용자가 2026-08-15 날짜에서 겪은 화면). 이 테스트는 **새 앱 세션**(AppTest 한 판 =
    세션 상태 비어 있음)에서 디스크 캐시만으로 행이 뜨는지 본다.

    무거운 백테는 스텁으로 갈아 두되 **조회가 그걸 부르면 터지게** 해서, 「캐시를 읽었다」가
    라벨이 아니라 동작으로 고정된다(자동 재계산 금지 · WAN-239 §3).
    """
    from datetime import date

    from backtest.trade_store import engine_source_revision
    from dashboard.app import full_universe_label
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS
    from live.timeline_cache import TimelineCacheStore, adopted_universe, cell_fingerprint
    from live.trade_timeline import SOURCE_BACKTEST, TimelineRow

    day = date(2026, 8, 15)
    day_key = day.isoformat()
    symbols, timeframes = adopted_universe()
    revision = engine_source_revision()  # 화면이 조회에 쓰는 것과 같은 지문(리비전 키 검산)
    row = TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=symbols[0],
        timeframe=timeframes[0],
        is_long=True,
        status="청산",
        reserve_ms=None,
        limit_price=None,
        fill_ms=1_786_000_000_000,
        fill_price=100.0,
        stop_price=None,
        take_profit_price=None,
        exit_ms=1_786_003_600_000,
        exit_price=101.5,
        exit_reason="take_profit",
        pnl_pct=1.5,
        pnl_amount=15.0,
    )
    store = TimelineCacheStore(seeded_db_path)
    try:
        for symbol in symbols:
            for timeframe in timeframes:
                fingerprint = cell_fingerprint(
                    symbol,
                    timeframe,
                    day_key,
                    warmup_days=DEFAULT_WARMUP_DAYS,
                    revision=revision,
                )
                first_cell = symbol == symbols[0] and timeframe == timeframes[0]
                store.save_cell(fingerprint, [row] if first_cell else [])
    finally:
        store.close()

    def _explode(**_kwargs: object) -> object:
        raise AssertionError("조회 경로가 무거운 백테를 다시 돌리면 안 된다(WAN-239 §3).")

    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", _explode)

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    at.date_input(key="timeline_day").set_value(day)
    at.radio(key="timeline_target").set_value(full_universe_label())
    at.run(timeout=60)

    assert not at.exception
    captions = [c.value for c in at.caption]
    assert any("디스크 캐시" in c for c in captions), captions
    # 「아직 계산 안 됨」 안내가 뜨면 안 된다(= 버튼을 요구하는 옛 화면).
    assert not any("아직 계산 안 됨" in info.value for info in at.info)
    # WAN-325 완료 기준 2 — 지금 엔진 캐시로 떴으므로 「옛 엔진」 경고가 붙으면 안 된다.
    assert not any("옛 엔진 결과입니다" in w.value for w in at.warning)


def test_stale_engine_rows_are_shown_with_a_warning_and_no_diff(
    seeded_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WAN-325 완료 기준 1·3 — 엔진이 바뀐 뒤에도 빈 화면 대신 옛 판이 **경고와 함께** 뜨고,
    3열 대조(페이퍼 | 차이 | 백테)는 **그리지 않는다**.

    엔진 소스 지문을 가짜로 바꿔(= 배포로 엔진이 바뀐 상황) 지금 지문으로는 한 칸도 못
    찾게 만든다. 그래도 디스크에 남아 있는 옛 판이 떠야 하고(사용자 요청: 「그냥 두면 안돼?
    안없어진채로?」), 「차이」 열은 **엔진이 바뀐 몫**을 재게 되므로 꺼져야 한다 — 그대로
    두면 🔴 판정 갈림이 무더기로 뜨는데 그건 틀린 신호다(WAN-295가 재려던 것과 다른 것).

    라벨 문구가 아니라 **동작**을 고정한다: 행이 뜨는가 · 경고가 붙는가 · 대조가 꺼지는가.
    무거운 백테는 부르면 터지게 해서 자동 재계산 금지(WAN-239 §3)도 함께 잠근다.
    """
    from datetime import date

    from dashboard.app import full_universe_label
    from live.live_vs_backtest import DEFAULT_WARMUP_DAYS
    from live.timeline_cache import TimelineCacheStore, adopted_universe, cell_fingerprint
    from live.trade_timeline import SOURCE_BACKTEST, TimelineRow

    day = date(2026, 8, 15)
    day_key = day.isoformat()
    symbols, timeframes = adopted_universe()
    stale_revision = "eng:0ldeng1ne00"  # 그 날짜에 남아 있는 옛 엔진 판
    row = TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=symbols[0],
        timeframe=timeframes[0],
        is_long=True,
        status="청산",
        reserve_ms=None,
        limit_price=None,
        fill_ms=1_786_000_000_000,
        fill_price=100.0,
        stop_price=None,
        take_profit_price=None,
        exit_ms=1_786_003_600_000,
        exit_price=101.5,
        exit_reason="take_profit",
        pnl_pct=1.5,
        pnl_amount=15.0,
    )
    store = TimelineCacheStore(seeded_db_path)
    try:
        for symbol in symbols:
            for timeframe in timeframes:
                fingerprint = cell_fingerprint(
                    symbol,
                    timeframe,
                    day_key,
                    warmup_days=DEFAULT_WARMUP_DAYS,
                    revision=stale_revision,
                )
                first_cell = symbol == symbols[0] and timeframe == timeframes[0]
                store.save_cell(
                    fingerprint, [row] if first_cell else [], created_at=1_755_000_000_000
                )
    finally:
        store.close()

    def _explode(**_kwargs: object) -> object:
        raise AssertionError("조회 경로가 무거운 백테를 다시 돌리면 안 된다(WAN-239 §3).")

    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", _explode)

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    at.date_input(key="timeline_day").set_value(day)
    at.radio(key="timeline_target").set_value(full_universe_label())
    at.run(timeout=60)

    assert not at.exception
    warnings = [w.value for w in at.warning]
    infos = [i.value for i in at.info]
    # (1) 옛 판이 실제로 떴고 경고가 붙었다 — 빈 화면이 아니다.
    assert any("옛 엔진 결과입니다" in w for w in warnings), warnings
    assert any(stale_revision in w for w in warnings), warnings
    assert not any("아직 계산 안 됨" in i for i in infos), infos
    # (2) 3열 대조는 꺼졌다(「차이」가 서로 다른 엔진을 빼지 않는다 — 완료 기준 3).
    assert any("엔진이 달라 대조하지 않습니다" in i for i in infos), infos
    # (3) 배지도 옛 판을 가리킨다(지금 엔진 이름표를 달고 옛 행을 내주지 않는다).
    assert any(stale_revision in c.value for c in at.caption), [c.value for c in at.caption]


def test_timeline_default_day_is_yesterday_kst(seeded_db_path: str) -> None:
    """WAN-326 §5 완료 기준 6: 타임라인 탭 첫 화면 날짜가 **어제(KST)** 다.

    옛 기본값은 **오늘**이었는데 캐시를 채우는 야간 크론은 **전일(KST)** 을 적재하므로
    (KST 00:30) 화면을 열면 정의상 항상 캐시 미스였다 — 「아직 계산 안 됨」이 첫인상이던
    진짜 원인이 이 날짜 축이다.

    ⚠️ **리터럴 날짜가 아니라 「오늘 − 1일」로 잠근다**(이슈 명시) — 리터럴로 박으면
    테스트가 하루 뒤에 깨지거나, 더 나쁘게는 계산식이 틀려도 통과한다.
    """
    from datetime import datetime, timedelta

    from common.timefmt import KST
    from dashboard.app import default_timeline_day

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    # 계산식 자체 — 화면이 읽는 그 함수가 「오늘(KST) − 1일」을 낸다.
    today_kst = datetime.now(tz=KST).date()
    assert default_timeline_day() == today_kst - timedelta(days=1)
    # 위젯이 실제로 그 값으로 그려졌다(함수만 맞고 화면은 옛 값인 상태를 막는다).
    assert at.date_input(key="timeline_day").value == default_timeline_day()


def test_timeline_default_target_is_the_full_universe(seeded_db_path: str) -> None:
    """WAN-326 완료 기준 1·2·4: 첫 화면 대조 대상이 **「채택 좌표 전부」**이고
    「라이브 칸만」은 **선택지로 그대로 남아 있다**.

    라벨 문자열이 아니라 **기본 선택이 어느 쪽인지**를 잠근다(이슈 명시) — 라벨은 좌표에서
    파생되므로(WAN-318 §6) 문자열로 박으면 다음 재-베이스라인에서 또 어긋난다. 그래서
    `full_universe_label()`이 내는 값과 대조하고, 선택지 개수·존치만 따로 본다.
    """
    from dashboard.app import _TARGET_LIVE_CELLS, full_universe_label

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)

    assert not at.exception
    radio = at.radio(key="timeline_target")
    # (1) 기본 선택이 넓은 쪽이다.
    assert radio.value == full_universe_label()
    # (2) 「라이브 칸만」은 지우지 않았다(사용자 결정: 존치).
    assert set(radio.options) == {full_universe_label(), _TARGET_LIVE_CELLS}


def test_timeline_live_cells_option_still_works_unchanged(seeded_db_path: str) -> None:
    """WAN-326 완료 기준 2: 「라이브 칸만」을 고르면 예전과 똑같이 동작한다(WAN-234 규약).

    기본이 바뀐 뒤에도 그 선택지가 **살아 있는 경로**인지를 동작으로 본다 — 라이브 대조
    체크박스가 그려지고(옛 화면의 진입점), 예외 없이 렌더된다.
    """
    from dashboard.app import _TARGET_LIVE_CELLS

    at = AppTest.from_file("dashboard/app.py")
    at.run(timeout=60)
    at.radio(key="timeline_target").set_value(_TARGET_LIVE_CELLS)
    at.run(timeout=60)

    assert not at.exception
    checkbox_keys = {c.key for c in at.checkbox}
    assert "timeline_include_bt" in checkbox_keys
    assert "timeline_recompute" in checkbox_keys
