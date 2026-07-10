"""strategy.parity(WAN-13) 스모크 테스트.

fixture 로딩 → 탐지 → 차트 렌더링 → 리포트 생성까지 파이프라인 전체가
동작하는지, 그리고 두 샘플 fixture가 탐지기와 100% 일치하는지 확인한다
(fixture 자체가 알고리즘 명세를 손으로 계산한 값이므로, 일치는 탐지기가
명세대로 동작함을 재확인하는 것이지 실제 TV 스크린샷과의 일치를 뜻하지
않는다 — `strategy/parity/README.md` 참고).
"""

from __future__ import annotations

from pathlib import Path

from strategy.order_blocks import detect_order_blocks
from strategy.parity.chart import render_order_block_chart
from strategy.parity.fixtures import FIXTURES_DIR, list_fixtures, load_fixture
from strategy.parity.report import compare_to_fixture


def test_at_least_two_fixtures_with_distinct_symbol_and_timeframe() -> None:
    paths = list_fixtures()
    assert len(paths) >= 2
    fixtures = [load_fixture(p) for p in paths]
    keys = {(f.symbol, f.timeframe) for f in fixtures}
    assert len(keys) == len(fixtures)  # 모두 서로 다른 심볼/타임프레임


def test_bullish_fixture_matches_detector_exactly() -> None:
    fixture = load_fixture(FIXTURES_DIR / "btcusdt_1h_bullish_sample.json")
    result = detect_order_blocks(fixture.to_dataframe(), fixture.params)
    report = compare_to_fixture(result, fixture)

    assert report.match_rate == 1.0
    assert report.extra_our_obs == []
    assert all(m.matched for m in report.matches)


def test_bearish_fixture_matches_detector_exactly() -> None:
    fixture = load_fixture(FIXTURES_DIR / "ethusdt_15m_bearish_sample.json")
    result = detect_order_blocks(fixture.to_dataframe(), fixture.params)
    report = compare_to_fixture(result, fixture)

    assert report.match_rate == 1.0
    assert report.extra_our_obs == []


def test_report_to_table_contains_symbol_and_match_rate() -> None:
    fixture = load_fixture(FIXTURES_DIR / "btcusdt_1h_bullish_sample.json")
    result = detect_order_blocks(fixture.to_dataframe(), fixture.params)
    report = compare_to_fixture(result, fixture)

    table = report.to_table()
    assert "BTCUSDT" in table
    assert "100.0%" in table


def test_mismatched_fixture_is_reported_as_unmatched() -> None:
    fixture = load_fixture(FIXTURES_DIR / "btcusdt_1h_bullish_sample.json")
    result = detect_order_blocks(fixture.to_dataframe(), fixture.params)

    tampered = fixture.model_copy(
        update={"tv_order_blocks": [fixture.tv_order_blocks[0].model_copy(update={"top": 999.0})]}
    )
    report = compare_to_fixture(result, tampered)

    assert report.match_rate == 0.0
    assert report.matches[0].matched is False
    assert report.matches[0].top_diff_pct is not None
    assert report.matches[0].top_diff_pct > 0


def test_render_order_block_chart_writes_png(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES_DIR / "btcusdt_1h_bullish_sample.json")
    result = detect_order_blocks(fixture.to_dataframe(), fixture.params)

    output = render_order_block_chart(
        fixture.to_dataframe(),
        result.order_blocks,
        tmp_path / "chart.png",
        title="test",
    )

    assert output.exists()
    assert output.stat().st_size > 0


def test_render_order_block_chart_handles_empty_order_blocks(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURES_DIR / "btcusdt_1h_bullish_sample.json")

    output = render_order_block_chart(fixture.to_dataframe(), [], tmp_path / "chart_empty.png")

    assert output.exists()
