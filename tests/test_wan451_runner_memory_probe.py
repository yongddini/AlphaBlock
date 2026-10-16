"""WAN-451 ④ · WAN-453 러너 메모리 측정기 — 작은 구성으로 실제 러너가 돌고 결과가 표로 나오는지.

WAN-453: 기본 측정이 「켜는 날의 러너」 배선(팔 10TF · 그 주 종목 목록 · 몰림 신호 장부 · 2년치
존 기억)과 같아야 한다 — 옛 측정은 그 부품 없이 9TF로 재 환산했다.
"""

from __future__ import annotations

import pytest

from data.storage import _DERIVED_TIMEFRAMES
from data.universe import ARM_TIMEFRAMES
from scripts import wan451_runner_memory_probe as probe


def test_probe_timeframes_follow_the_runner() -> None:
    """측정 TF가 러너 팔 TF와 · 파생 원천이 저장소와 같다(어긋나면 다른 러너를 잰다)."""
    assert tuple(ARM_TIMEFRAMES) == probe.ARM_TFS
    assert probe.DERIVED_SOURCE == _DERIVED_TIMEFRAMES


def test_history_covers_the_first_boot_window() -> None:
    """시드 이력 = 러너가 첫 기동에 읽는 창(max(창 1500봉, 존 기억 일수)) — 파생 2h는 1h에 두 배."""
    need = probe.history_bars(probe.ARM_TFS, lookback=1500, bootstrap_days=730)
    assert need["15m"] == 730 * 96
    assert need["1h"] == 2 * 730 * 12  # 2h 존 기억 8,760봉 = 1h 17,520봉(1h 자기 몫 17,520과 같다)
    assert need["1d"] == 1500  # 창이 존 기억(730일)보다 길다
    assert need["1w"] == probe.MAX_HISTORY_BARS["1w"]
    assert "2h" not in need  # 파생 TF는 저장하지 않는다
    assert need["3h"] == 730 * 8  # 3h는 저장소에 원천으로 있다


def test_legacy_probe_runs_the_real_runner_and_does_work() -> None:
    r = probe.run_probe(
        symbols=2, timeframes=("15m", "1h", "2h", "1w"), polls=2, lookback=300, minute_bars=600
    )
    assert r.series == 8
    assert r.primed_series == 8  # 모든 시리즈가 실제로 가동됐다
    assert r.zones > 0  # 탐지가 실제로 돌았다(0이면 메모리 과소평가)
    assert len(r.steady_poll_seconds) == 2
    assert r.peak_rss_mb >= r.baseline_rss_mb > 0
    assert not r.production and r.bootstrap_days == 0


def test_production_probe_runs_the_arm_wiring() -> None:
    """켜는 날의 배선: 팔 10TF 전부가 가동되고, 목록이 시리즈를 지우지 않는다."""
    r = probe.run_probe(
        symbols=2,
        timeframes=("15m",),  # production에서는 무시된다(팔 10TF)
        polls=1,
        lookback=200,
        minute_bars=600,
        bootstrap_days=30,
        production=True,
    )
    assert r.production and r.timeframes == list(ARM_TIMEFRAMES)
    assert r.series == 2 * len(ARM_TIMEFRAMES)
    assert r.primed_series == r.series  # 목록 동기화가 시리즈를 빼지 않았다
    assert r.zones > 0


def test_render_reports_one_config_and_flags_hollow_runs() -> None:
    ok = probe.ProbeResult(
        31, list(probe.ARM_TFS), 310, 200.0, 560.0, 600.0, [3.0], 310, 900, 730, True, 4
    )
    text = probe.render(ok, {"MemTotal": 1000.0, "MemAvailable": 800.0})
    assert "| 310(310) | 900 | 4 | **360** | 560 | 600.0 | 3.00 |" in text
    assert "종목 목록 · 몰림 신호 장부 · 존 기억 730일" in text
    assert "들어간다" in text and "측정 무효" not in text
    hollow = probe.ProbeResult(31, list(probe.ARM_TFS), 310, 200.0, 210.0, 1.0, [0.1], 310, 0)
    assert "측정 무효 의심" in probe.render(hollow, {})
    tight = probe.render(ok, {"MemTotal": 1000.0, "MemAvailable": 500.0})
    assert "업그레이드 검토" in tight


def test_main_seeds_in_a_separate_process(capsys: pytest.CaptureFixture[str]) -> None:
    """기본 실행 = 시드 프로세스 → 측정 프로세스 → 표(작은 구성으로 끝까지)."""
    rc = probe.main(
        [
            "--symbols",
            "1",
            "--bootstrap-days",
            "10",
            "--lookback",
            "100",
            "--minute-bars",
            "300",
            "--polls",
            "1",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "## WAN-453 켜기 전 서버 재측정" in out
    assert "| 10(10) |" in out  # 1종목 × 10TF가 전부 가동됐다
    assert "측정 무효" not in out
