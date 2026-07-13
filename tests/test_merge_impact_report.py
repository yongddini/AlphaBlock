"""WAN-56 병합 영향 리포트 스크립트 스모크 테스트(합성 데이터, 재현 가능)."""

from __future__ import annotations

from scripts.merge_impact_report import build_report


def test_build_report_synthetic_emits_table_and_reduces_entries() -> None:
    report = build_report(
        ["BTC/USDT:USDT", "ETH/USDT:USDT"],
        ["1h", "4h"],
        synthetic=True,
        synthetic_bars=2000,
    )
    # 표 헤더와 합계 행이 있고, 각 (심볼·TF) 셀이 렌더된다.
    assert "raw 진입" in report and "merged 진입" in report
    assert "**합계**" in report
    assert report.count("BTC/USDT:USDT") + report.count("ETH/USDT:USDT") >= 4
    # 팽창률 표기(×)가 최소 한 번 등장한다(병합이 겹치는 진입을 줄였다).
    assert "×" in report
