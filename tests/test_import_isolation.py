"""순환 임포트 회귀 방지 테스트 (WAN-42).

과거 `backtest → data → live → paper → backtest` 순환 임포트로 백테스트·대시보드·CLI가
모두 막혔다(ImportError). 원인은 하위 레이어(`data`)가 상위 레이어(`live`)를 임포트한
레이어 위반과, 패키지 `__init__` 의 eager re-export 였다.

이 테스트는 두 가지를 강제한다.

1. **격리 임포트**: 각 최상위 패키지를 *깨끗한 하위 프로세스* 에서 단독으로 임포트했을 때
   ImportError 없이 성공하는지. (기존 pytest 가 하위 모듈을 먼저 임포트해 우연히 순서가
   맞아 순환을 못 잡던 문제를 막는다.) `alphablock --help` 도 같은 방식으로 검증한다.
2. **레이어 규칙**: 하위 레이어(`data`/`strategy`/`execution`/`backtest`)의 소스가
   상위 레이어(`live`/`paper`)를 임포트하지 않는지 AST 로 정적 검사한다.

레이어 규칙 문서: `docs/architecture-layers.md`.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 단독 임포트가 성공해야 하는 최상위 패키지들.
_TOP_LEVEL_PACKAGES = [
    "common",
    "config",
    "data",
    "strategy",
    "execution",
    "backtest",
    "live",
    "paper",
    "dashboard",
    "cli",
]

#: 하위 레이어(키)가 임포트하면 안 되는 상위 레이어(값) — 레이어 위반 = 순환 위험.
_FORBIDDEN_UPWARD_IMPORTS = {
    "data": {"live", "paper", "backtest", "execution", "strategy"},
    "strategy": {"live", "paper", "backtest", "execution", "data"},
    "execution": {"live", "paper", "backtest"},
    "backtest": {"live", "paper"},
}


def _import_in_subprocess(statement: str) -> subprocess.CompletedProcess[str]:
    """새 파이썬 프로세스에서 `statement` 를 실행한다(모듈 캐시 오염 없음)."""
    return subprocess.run(
        [sys.executable, "-c", statement],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("package", _TOP_LEVEL_PACKAGES)
def test_top_level_package_imports_in_isolation(package: str) -> None:
    """각 최상위 패키지를 단독 프로세스에서 임포트해도 순환 임포트가 없다."""
    result = _import_in_subprocess(f"import {package}")
    assert result.returncode == 0, f"`import {package}` 실패:\n{result.stderr}"


def test_cli_help_runs() -> None:
    """CLI 진입점(`alphablock --help`)이 임포트 단계에서 막히지 않는다."""
    result = _import_in_subprocess("from cli.main import build_parser; build_parser()")
    assert result.returncode == 0, f"CLI 임포트/파서 조립 실패:\n{result.stderr}"


def _iter_imported_top_packages(source: str) -> set[str]:
    """소스 문자열에서 임포트하는 최상위 패키지 이름들을 뽑는다(함수 내부 임포트 포함)."""
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".", 1)[0])
        # 상대 임포트(level>0)는 같은 패키지 내부이므로 레이어 검사 대상이 아니다.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".", 1)[0])
    return imported


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN_UPWARD_IMPORTS))
def test_no_reverse_layer_imports(layer: str) -> None:
    """하위 레이어 소스가 상위 레이어를 임포트하지 않는다(레이어 규칙 정적 강제)."""
    forbidden = _FORBIDDEN_UPWARD_IMPORTS[layer]
    violations: list[str] = []
    for path in sorted((_REPO_ROOT / layer).rglob("*.py")):
        imported = _iter_imported_top_packages(path.read_text(encoding="utf-8"))
        bad = imported & forbidden
        if bad:
            rel = path.relative_to(_REPO_ROOT)
            violations.append(f"{rel} → {', '.join(sorted(bad))}")
    assert not violations, (
        f"'{layer}' 레이어가 상위 레이어를 임포트합니다(순환 위험):\n" + "\n".join(violations)
    )
