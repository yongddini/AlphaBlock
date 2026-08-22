"""scripts/wan353-server-zone-audit.sh 회귀 테스트 (WAN-353).

이 스크립트가 존재하는 이유는 **순서**다 — 지문 확인 → (미스면) 되채우기 → 그다음에
`--zone-audit`. 그래서 잠글 계약도 라벨이 아니라 **동작**이다:

1. 캐시가 미스면 **되채우기를 실제로 돌리고**, 적중이면 **돌리지 않는다**.
   (순서를 안 지키면 WAN-334를 네 번 포기하게 만든 「매번 처음부터 재계산」이 재현된다.)
2. 워커 기본값이 **2**로 실제 명령에 실린다 — 서버가 2코어 1GB이기 때문이다(WAN-324/354).
   이슈 본문의 `--jobs 4`를 그대로 옮기면 안 된다.
3. `data_gap_skips` 파일이 없으면 **「확인 불가」를 찍는다**(WAN-194 원칙: 지어내지 않는다).
   🚨 이 갈래는 개발 중 실제로 조용히 죽어 있었다 — `else` 앞의 줄바꿈이 빠져 bash가 분기를
   통째로 삼켰고 **아무것도 안 찍으면서 종료 코드 0**을 냈다. 이 저장소가 반복해 경계하는
   「실패가 성공과 같은 모양」(WAN-194/318/321)이라 동작으로 못 박는다.

`uv`를 가짜로 갈아끼워 실제 백테를 돌리지 않고 **어떤 명령이 어떤 인자로 불렸는지**만 잰다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "wan353-server-zone-audit.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash 없음")

#: 가짜 `uv` — 호출 인자를 로그에 적고, `--no-stale` 조회에만 적중/미스를 흉내 낸다.
_FAKE_UV = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$UV_CALL_LOG"
case "$*" in
  *--no-stale*)
    if [ "${CACHE_HIT:-0}" = 1 ]; then
      echo "백테 대조 엔진: **... (eng:deadbeef)**"
    else
      echo "🚨 백테 대조 **아직 계산 안 됨** — 48/48칸 캐시 미스"
    fi
    ;;
  *) echo "(가짜 uv)" ;;
esac
"""


def _run(tmp_path: Path, *, cache_hit: bool, extra: list[str] | None = None) -> tuple[str, str]:
    """스크립트를 가짜 `uv`로 돌리고 (리포트 본문, `uv` 호출 로그)를 낸다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uv"
    fake.write_text(_FAKE_UV, encoding="utf-8")
    fake.chmod(0o755)

    log = tmp_path / "uv-calls.log"
    log.touch()
    out = tmp_path / "report.md"

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["UV_CALL_LOG"] = str(log)
    env["CACHE_HIT"] = "1" if cache_hit else "0"
    env["DAYS"] = "2026-08-17"
    env["RUNTIME_STATE"] = str(tmp_path / "absent-runtime-state.json")

    proc = subprocess.run(
        ["bash", str(SCRIPT), "-o", str(out), *(extra or [])],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out.read_text(encoding="utf-8"), log.read_text(encoding="utf-8")


def test_cache_miss_triggers_backfill_before_the_audit(tmp_path: Path) -> None:
    """미스면 되채우기가 **감사보다 먼저** 돈다 — 순서가 이 스크립트의 존재 이유다."""
    report, calls = _run(tmp_path, cache_hit=False)

    assert "--persist-cache" in calls, "미스인데 되채우기를 안 돌렸다"
    lines = calls.splitlines()
    persist = next(i for i, line in enumerate(lines) if "--persist-cache" in line)
    audit = next(i for i, line in enumerate(lines) if "--zone-audit" in line)
    assert persist < audit, "되채우기가 감사 뒤에 돌면 감사는 매번 처음부터 계산한다"
    assert "→ **미스**: 되채우기 대상." in report


def test_cache_hit_skips_the_backfill(tmp_path: Path) -> None:
    """적중이면 되채우기를 **돌리지 않는다** — 있는 캐시를 다시 굽지 않는다."""
    report, calls = _run(tmp_path, cache_hit=True)

    assert "--persist-cache" not in calls
    assert "--zone-audit" in calls, "감사는 언제나 돈다"
    assert "되채울 날짜가 없다" in report


def test_skip_backfill_flag_suppresses_the_write_but_not_the_audit(tmp_path: Path) -> None:
    """`-n`은 **쓰는 단계만** 끈다 — 감사 자체는 그대로 돈다(결과는 틀리지 않는다)."""
    _report, calls = _run(tmp_path, cache_hit=False, extra=["-n"])

    assert "--persist-cache" not in calls
    assert "--zone-audit" in calls


def test_default_jobs_is_two_not_four(tmp_path: Path) -> None:
    """서버는 2코어 1GB다(WAN-324/354). 기본값이 실제 명령에 실리는지 **인자로** 잰다."""
    _report, calls = _run(tmp_path, cache_hit=False)

    audit = next(line for line in calls.splitlines() if "--zone-audit" in line)
    assert "--jobs 2" in audit
    assert "--jobs 4" not in calls


def test_jobs_override_reaches_every_command(tmp_path: Path) -> None:
    """`-j`로 덮으면 되채우기·감사 **양쪽**에 실린다(러너와 코어를 다투지 않게)."""
    _report, calls = _run(tmp_path, cache_hit=False, extra=["-j", "1"])

    heavy = [
        line for line in calls.splitlines() if "--persist-cache" in line or "--zone-audit" in line
    ]
    assert heavy, "무거운 명령이 하나도 안 불렸다"
    assert all("--jobs 1" in line for line in heavy)


def test_missing_runtime_state_says_unverifiable(tmp_path: Path) -> None:
    """상태 파일이 없으면 **「확인 불가」를 찍는다** — 조용히 빈 절을 내지 않는다.

    개발 중 이 갈래가 실제로 삼켜져 아무것도 안 찍으면서 종료 코드 0을 냈다.
    """
    report, _calls = _run(tmp_path, cache_hit=False)

    section = report.split("## 5.")[1].split("## 6.")[0]
    assert "확인 불가" in section
    assert "absent-runtime-state.json" in section
