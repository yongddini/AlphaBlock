"""WAN-122 Phase 2 — A안(종가 진입) 고정 리포트 인벤토리를 동작으로 고정한다.

이 테스트는 `docs/decisions/wan122.md` §2의 인벤토리를 **회귀로 잠근다**. A안 폐기는
여러 PR에 걸친 재-베이스라인이라(Phase 3 = 사용자 결정), 그 사이에 아카이브 리포트가
A안 시그니처를 무의식적으로 잃거나(= 조용한 제거) B안 리포트가 A안으로 오분류되면
인벤토리가 문서와 어긋난다. 그런 변경은 이 테스트를 깨서 **의식적 갱신**을 강제한다.

인벤토리를 손대는 정당한 변경(Phase 3 등)은 이 파일과 `wan122.md`를 함께 고친다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backtest.sweep import CLOSE_ENTRY_DEFAULTS
from strategy.models import ConfluenceParams

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKTEST = _REPO_ROOT / "backtest"

#: 아카이브 판정 = 무수정 보존(과거 엔진 기록 / 대조축). (모듈 파일, A안 시그니처 문자열).
#: 시그니처가 사라지면 아카이브 리포트가 A안 정체성을 잃은 것이라 테스트가 잡는다.
A_PLAN_ARCHIVED_REPORTS: dict[str, str] = {
    "wan68_short_gate_analysis.py": "CLOSE_ENTRY_DEFAULTS",
    "wan75_deviation_filter_analysis.py": 'entry_mode="close"',
    "wan77_volume_decomposition.py": 'entry_mode="close"',
    # wan81은 param 고정이 아니라 A안 봉-단위 호출 경로를 직접 쓴다(wan122.md §2-2).
    "wan81_engine_replacement_report.py": "generate_confluence_signals",
    "wan87_long_only_report.py": 'entry_mode="close"',
    "wan91_funding_cost_report.py": 'entry_mode="close"',
}

#: 유지 판정 = A안을 대조축으로 존치. A vs B가 한 모듈에 공존한다.
A_PLAN_KEEP_ACTIVE: dict[str, tuple[str, ...]] = {
    "wan95_zone_limit_report.py": ("ZONE_LIMIT_PARAMS = ConfluenceParams()", 'entry_mode="close"'),
}

#: A안 아님 — 인벤토리에서 명시 제외(오분류 정정, wan122.md §2-3).
A_PLAN_EXCLUDED: tuple[str, ...] = ("wan96_fill_conservatism_report.py",)


def _source(name: str) -> str:
    path = _BACKTEST / name
    assert path.exists(), f"인벤토리가 가리키는 모듈이 없다: {path}"
    return path.read_text(encoding="utf-8")


def test_adopted_default_is_b_plan() -> None:
    """폐기의 전제: 채택 기본값은 B안(`zone_limit`)이고 A안은 옵트인이다."""
    assert ConfluenceParams().entry_mode == "zone_limit"


def test_close_entry_anchor_is_a_plan() -> None:
    """A안 경로 스위치의 앵커(`CLOSE_ENTRY_DEFAULTS`)는 종가 진입으로 못 박혀 있다."""
    assert CLOSE_ENTRY_DEFAULTS.entry_mode == "close"
    # A안은 지정가 전용 노브를 들면 안 된다(라벨 위조 방지, WAN-112/159).
    assert CLOSE_ENTRY_DEFAULTS.zone_limit_offset_bps == 0.0
    assert CLOSE_ENTRY_DEFAULTS.max_zone_width_atr is None


@pytest.mark.parametrize(("name", "signature"), sorted(A_PLAN_ARCHIVED_REPORTS.items()))
def test_archived_reports_keep_a_plan_signature(name: str, signature: str) -> None:
    """아카이브 리포트는 A안 시그니처를 유지한다(무의식 제거 방지)."""
    assert signature in _source(name), (
        f"{name}이 A안 시그니처 {signature!r}를 잃었다 — 아카이브 판정과 어긋난다. "
        "Phase 3 등으로 의도한 변경이면 wan122.md 인벤토리를 함께 갱신하라."
    )


@pytest.mark.parametrize(("name", "signatures"), sorted(A_PLAN_KEEP_ACTIVE.items()))
def test_keep_active_reports_hold_both_arms(name: str, signatures: tuple[str, ...]) -> None:
    """유지 리포트는 A안·B안 두 팔을 모두 갖는다(대조축)."""
    src = _source(name)
    for sig in signatures:
        assert sig in src, f"{name}이 대조 팔 {sig!r}를 잃었다 — A vs B 대조가 깨진다."


@pytest.mark.parametrize("name", A_PLAN_EXCLUDED)
def test_excluded_reports_are_not_a_plan(name: str) -> None:
    """제외 모듈은 A안 종가 고정을 갖지 않는다(오분류 재발 방지)."""
    assert 'entry_mode="close"' not in _source(name), (
        f"{name}이 A안으로 바뀌었다 — 인벤토리(wan122.md §2-3)를 재검토하라."
    )
