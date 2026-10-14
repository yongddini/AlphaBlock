"""WAN-451 ④ 러너 메모리 측정기 — 작은 구성으로 실제 러너가 돌고 결과가 표로 나오는지."""

from __future__ import annotations

from scripts import wan451_runner_memory_probe as probe


def test_probe_runs_the_real_runner_and_does_work() -> None:
    r = probe.run_probe(
        symbols=2, timeframes=("15m", "1h", "2h", "1w"), polls=2, lookback=300, minute_bars=600
    )
    assert r.series == 8
    assert r.primed_series == 8  # 모든 시리즈가 실제로 가동됐다
    assert r.zones > 0  # 탐지가 실제로 돌았다(0이면 메모리 과소평가)
    assert len(r.steady_poll_seconds) == 2
    assert r.peak_rss_mb >= r.baseline_rss_mb > 0


def test_render_scales_nine_timeframes_to_ten_and_flags_hollow_runs() -> None:
    now = probe.ProbeResult(12, list(probe.NOW_TFS), 48, 200.0, 260.0, 3.0, [0.5], 48, 120)
    arm = probe.ProbeResult(31, list(probe.ARM_TFS), 279, 200.0, 560.0, 30.0, [3.0], 279, 900)
    text = probe.render(now, arm, {"MemTotal": 1000.0, "MemAvailable": 800.0})
    assert "| **팔 환산(31종목 × 10TF)** | 310 | — | **400**" in text  # 360 × 10/9
    assert "들어간다" in text and "측정 무효" not in text
    hollow = probe.ProbeResult(31, list(probe.ARM_TFS), 279, 200.0, 210.0, 1.0, [0.1], 279, 0)
    assert "측정 무효 의심" in probe.render(now, hollow, {})
    tight = probe.render(now, arm, {"MemTotal": 1000.0, "MemAvailable": 500.0})
    assert "업그레이드 검토" in tight
