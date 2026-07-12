"""common.heartbeat 하트비트 스토어 테스트 (WAN-31)."""

from __future__ import annotations

from pathlib import Path

from common.heartbeat import Heartbeat, HeartbeatStore


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = HeartbeatStore(tmp_path / "missing.json", label="collector")
    beat = store.load()
    assert beat.updated_at is None
    assert beat.label == "collector"


def test_beat_records_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "hb.json"
    clock = {"t": 1_000}
    store = HeartbeatStore(path, label="collector", now_ms=lambda: clock["t"])

    assert store.beat() is True
    assert path.exists()

    # 다른 인스턴스로 다시 읽어도 값이 남아 있다.
    reloaded = HeartbeatStore(path).load()
    assert reloaded.updated_at == 1_000
    assert reloaded.label == "collector"


def test_min_interval_throttles_writes(tmp_path: Path) -> None:
    path = tmp_path / "hb.json"
    clock = {"t": 0}
    store = HeartbeatStore(
        path, label="collector", min_interval_ms=5_000, now_ms=lambda: clock["t"]
    )

    assert store.beat() is True  # 첫 기록
    clock["t"] = 2_000
    assert store.beat() is False  # 스로틀(간격 미달)
    assert store.load().updated_at == 0

    clock["t"] = 6_000
    assert store.beat() is True  # 간격 초과 → 기록
    assert store.load().updated_at == 6_000


def test_load_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "hb.json"
    path.write_text("{ not json", encoding="utf-8")
    beat = HeartbeatStore(path, label="collector").load()
    assert beat == Heartbeat(label="collector")
