"""WAN-373: 새 북 측정 모듈이 「익절 비용 회계」를 잊으면 조용히 옛 회계로 돈다.

WAN-370이 익절 청산 유동성 축(`take_profit_liquidity`)을 만들면서, 옛 리포트 20여 개가
공유하는 **북 층 함수들의 기본값을 옛 값**(`taker`)에 뒀다(`wan169.run_cells` ·
`book_cli.iter_book_segments`/`build_book_rows`). 한 곳으로 그 표들을 통째로 보존하는
대신, **새 모듈이 명시를 잊으면 라벨은 「오늘 엔진」인데 숫자는 옛 회계**가 된다.

`docs/decisions/wan370.md` §2-2가 그 위험을 스스로 적어 뒀지만 **기록은 사람이 읽어야
작동하고 테스트는 안 읽어도 작동한다** — 이 파일이 그 문장을 동작으로 옮긴다.

🚨 이건 가정이 아니라 **이미 한 번 일어난 사고**다: WAN-273 파급이 남긴
`include_reentry=False` 기본값이 wan288/291/293/300/301/302를 전부 재진입 꺼진 북으로
돌게 했고 WAN-300은 결론까지 다시 냈다(재측정 = WAN-304). 글자 그대로 같은 모양이다 —
옛 CSV 보존을 위해 공유 헬퍼의 기본값을 옛 규칙에 뒀고 새 리포트가 그걸 물려받았다.

⚠️ **WAN-365 가드와 방향이 반대다.** 그쪽(`test_wan365_invalidation_rebaseline.py`)은
*「옛 리포트가 새 엔진으로 새는 것」*을 잡아 **핀을 강제**하고, 이쪽은 *「새 리포트가 옛
회계로 새는 것」*을 잡아 **명시를 강제**한다. 그래서 「무엇이 예외인가」의 뜻도 반대다 —
**옛 회계가 정답인 모듈**이 예외 목록에 들어간다.

⚠️ **가드는 「축을 언급했는가」까지만 본다** — 옛 값을 넘겼는지 채택 값을 넘겼는지는
모듈마다 옳은 답이 다르므로 강제하지 않는다(WAN-365 가드도 같은 선을 긋는다). 잡는 것은
**아무 생각 없이 기본값을 물려받는 것** 하나다.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Callable

from backtest import harness

_BACKTEST = pathlib.Path("backtest")

#: 북 층 진입점 — 이 이름들이 도달 분석의 뿌리다. 이 셋이 `take_profit_liquidity`를
#: **파라미터로 나르는** 함수 전부이고(테스트가 아래에서 확인한다), 그 기본값이 옛 값이다.
_BOOK_ROOTS = frozenset({"run_cells", "iter_book_segments", "build_book_rows"})

#: 북 층이 사는 모듈 — 도달 분석만으로는 **이름 충돌**로 과대 추정된다(`run_cell`·
#: `run_grid`처럼 흔한 이름이 여러 모듈에 산다). 실제로 wan123/wan149/wan176/wan229는
#: per-cell 모듈인데 이름만으로는 북에 닿는 것처럼 보였다. **북을 쓰려면 반드시
#: import해야 하므로** 두 조건을 AND로 걸어 그 넷을 걷어낸다.
_BOOK_MODULES = frozenset(
    {
        "backtest.wan169_leverage_book",
        "backtest.book_cli",
    }
)

#: 이 축을 언급하지 **않아도** 되는 모듈과 그 이유 — `docs/decisions/wan370.md` §2-3의
#: 「북 측정 모듈 전부는 기본값으로 한 곳에서 보존된다」 목록과 같은 집합이어야 한다.
#:
#: 🚨 **기준은 「그 모듈의 공개 CSV가 옛 회계의 기록인가」**이다. 전부 WAN-370 **이전**에
#: 낸 표라 결론 문장이 옛 비용 위에 서 있고, 그래서 기본값(`taker`)을 물려받는 것이
#: **맞는 결과**다(WAN-370이 "한 줄도 안 고쳤다"고 적은 그 모듈들). 새로 만드는 모듈은
#: 이 목록에 들어갈 수 없다 — 새 측정·대조 도구의 기본값은 채택 규칙이어야 한다(WAN-305).
_LEGACY_BY_DESIGN: dict[str, str] = {
    "wan180_leverage_book_nine.py": "옛 회계 기록 — 9종목 북 (WAN-180)",
    "wan244_capacity_cap.py": "옛 회계 기록 — 용량 상한 (WAN-244)",
    "wan261_reentry_book.py": "옛 회계 기록 — 재진입 북 (WAN-261)",
    "wan264_reentry_book_stress.py": "옛 회계 기록 — 재진입 북 렌즈 (WAN-264)",
    "wan269_reentry_book_band.py": "옛 회계 기록 — 재진입 band (WAN-269)",
    "wan271_reentry_book_band_stress.py": "옛 회계 기록 — band × pen_5bp (WAN-271)",
    "wan276_stop_gap_fill.py": "옛 회계 기록 — 손절 갭 체결 (WAN-276)",
    "wan277_stop_gap_reentry.py": "옛 회계 기록 — 손절 갭 × 재진입 (WAN-277)",
    "wan280_reentry_short_transition.py": "옛 회계 기록 — 숏 전이 (WAN-280)",
    "wan282_resistance_short_mirror.py": "옛 회계 기록 — 저항-숏 미러 (WAN-282)",
    "wan284_resistance_short_profit_null.py": "옛 회계 기록 — 숏 수익 널 (WAN-284)",
    "wan288_monthly_long_short.py": "옛 회계 기록 — 월별 롱/숏 (WAN-288)",
    "wan293_monthly_fill_lens.py": "옛 회계 기록 — 월별 × 체결 렌즈 (WAN-293)",
    "wan300_universe_size.py": "옛 회계 기록 — 종목 수 사다리 (WAN-300)",
    "wan301_short_book_risk.py": "옛 회계 기록 — 숏 북 리스크 (WAN-301)",
    "wan304_universe_reentry.py": "옛 회계 기록 — 종목 수 재측정 (WAN-304)",
    "wan312_stop_r_multiple.py": "옛 회계 기록 — 손절 R 배수 스트레스 (WAN-312/316)",
    "wan323_partial_tp_ladder.py": "옛 회계 기록 — 반익절 래더 (WAN-323)",
    "wan327_partial_bar_impact.py": "옛 회계 기록 — 부분 봉 영향 (WAN-327)",
    "wan330_partial_tp_ladder_4tf.py": "옛 회계 기록 — 래더 4TF (WAN-330)",
    "wan336_same_step_tp.py": "옛 회계 기록 — 같은 분 익절 (WAN-336)",
    "wan346_conservative_book.py": "옛 회계 기록 — 보수적 북 (WAN-346)",
    "wan359_tick_targeted_tp.py": "옛 회계 기록 — 틱 표적 익절 (WAN-359)",
    "wan364_invalidation_cancel.py": "옛 회계 기록 — 취소 시점 축 (WAN-364)",
}


# --------------------------------------------------------------------------- #
# 도달 분석 — WAN-365의 기계를 축만 갈아 재사용한다
# --------------------------------------------------------------------------- #


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _imported_modules(tree: ast.Module) -> set[str]:
    """이 모듈이 import하는 모듈의 **점 표기 이름** 집합.

    `from backtest.book_cli import ...`와 `from backtest import book_cli` 두 형태를 모두
    같은 이름(`backtest.book_cli`)으로 모은다 — 한쪽만 보면 표기법을 바꾸는 것만으로
    가드를 피할 수 있다.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def _book_reaching_functions(root: pathlib.Path) -> frozenset[str]:
    """`_BOOK_ROOTS`에 (이름 기준) 닿는 함수의 전이 폐포.

    호출부가 부르는 이름이 `book_segments_for_payloads`처럼 뿌리에서 몇 단계 떨어져 있어도
    잡으려는 것이다 — 실제로 wan346/wan359/wan364가 그 자리다(`run_cells`를 직접 부르지
    않고 남이 감싸 둔 것을 빌려 쓴다). 목록을 손으로 적으면 그런 것이 빠진다.
    """
    calls: dict[str, set[str]] = {}
    for path in sorted(root.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = {_call_name(c) for c in ast.walk(node) if isinstance(c, ast.Call)}
                calls.setdefault(node.name, set()).update(names - {""})
    reach = set(_BOOK_ROOTS)
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in reach and called & reach:
                reach.add(name)
                changed = True
    return frozenset(reach)


def _feeds_the_book(path: pathlib.Path, *, reach: frozenset[str]) -> bool:
    """이 모듈이 북 층을 실제로 돌리는가 — **import ∧ 호출**을 함께 본다."""
    tree = ast.parse(path.read_text())
    if not (_imported_modules(tree) & _BOOK_MODULES):
        return False
    local = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return any(
        isinstance(n, ast.Call) and _call_name(n) in reach and _call_name(n) not in local
        for n in ast.walk(tree)
    )


def _modules_missing_the_axis(
    root: pathlib.Path, *, exempt: frozenset[str] = frozenset()
) -> list[str]:
    """북 층을 돌리면서 이 축을 **아예 언급조차 안 한** `wan*.py` 모듈."""
    reach = _book_reaching_functions(root)
    missing: list[str] = []
    for path in sorted(root.glob("wan*.py")):
        if path.name in exempt or not _feeds_the_book(path, reach=reach):
            continue
        if "take_profit_liquidity" in path.read_text().lower():
            continue
        missing.append(path.name)
    return missing


# --------------------------------------------------------------------------- #
# 1. 축이 존재하고, 두 이름이 실제로 갈린다
# --------------------------------------------------------------------------- #


def test_the_axis_has_two_distinct_names() -> None:
    """가드가 지키는 대상 — 옛 값과 채택 값이 같아지면 이 가드는 뜻을 잃는다."""
    assert harness.LEGACY_TAKE_PROFIT_LIQUIDITY is not harness.ADOPTED_TAKE_PROFIT_LIQUIDITY


def test_book_layer_defaults_are_still_the_legacy_value() -> None:
    """가드의 **전제** — 북 층 기본값이 옛 값이라서 「잊으면 샌다」가 성립한다.

    기본값이 채택 값으로 뒤집히면(별도 결정 이슈) 이 가드의 이유가 사라지므로, 그때
    조용히 남지 않고 여기서 시끄럽게 죽어야 한다.
    """
    import inspect

    from backtest import book_cli
    from backtest import wan169_leverage_book as wan169

    targets: list[Callable[..., object]] = [
        wan169.run_cells,
        wan169.build_book_rows,
        book_cli.build_book_rows,
        book_cli.iter_book_segments,
    ]
    for fn in targets:
        default = inspect.signature(fn).parameters["take_profit_liquidity"].default
        assert default is harness.LEGACY_TAKE_PROFIT_LIQUIDITY, fn.__qualname__


# --------------------------------------------------------------------------- #
# 2. 가드 본체 — 지금 저장소가 초록불이어야 한다
# --------------------------------------------------------------------------- #


def test_every_book_module_names_the_take_profit_liquidity_axis() -> None:
    """북 층을 돌리는 새 모듈이 축을 안 넘기면 여기서 걸린다.

    ⚠️ 예외는 `_LEGACY_BY_DESIGN`에 **이유와 함께 선언**한다 — 그 목록은 「옛 회계가 정답인
    모듈」의 스냅샷이고, 새 모듈은 들어갈 수 없다(WAN-305).
    """
    reach = _book_reaching_functions(_BACKTEST)
    # 도달 분석이 실제로 뿌리 너머까지 갔는지 — 폐포가 뿌리만 남으면 가드가 헐거워진다.
    assert reach >= _BOOK_ROOTS
    assert "book_segments_for_payloads" in reach and "run_book" in reach

    missing = _modules_missing_the_axis(_BACKTEST, exempt=frozenset(_LEGACY_BY_DESIGN))
    assert not missing, (
        "북 층을 돌리면서 `take_profit_liquidity`를 언급조차 하지 않은 모듈: "
        f"{missing} — `harness.ADOPTED_TAKE_PROFIT_LIQUIDITY`를 명시하거나, 옛 회계가 "
        "정답이면 `_LEGACY_BY_DESIGN`에 이유와 함께 선언할 것."
    )


def test_legacy_by_design_modules_are_declared_not_accidental() -> None:
    """예외는 **선언**이어야 한다 — 목록이 낡으면(파일이 사라지면) 시끄럽게 죽는다."""
    for name, reason in _LEGACY_BY_DESIGN.items():
        assert (_BACKTEST / name).exists(), name
        assert reason.strip(), name


def test_legacy_by_design_has_no_stale_entries() -> None:
    """북을 더 이상 안 도는 모듈이 목록에 남아 있으면 예외가 조용히 쌓인다."""
    reach = _book_reaching_functions(_BACKTEST)
    stale = [n for n in _LEGACY_BY_DESIGN if not _feeds_the_book(_BACKTEST / n, reach=reach)]
    assert not stale, f"북 층을 더 이상 돌리지 않는데 예외 목록에 남아 있다: {stale}"


def test_the_two_recomputed_modules_are_not_exempt() -> None:
    """wan366·wan370은 **채택 회계로 다시 돈 모듈**이라 예외가 아니라 명시 쪽이다."""
    for name in ("wan366_causal_ablation.py", "wan370_cost_decomposition.py"):
        assert name not in _LEGACY_BY_DESIGN
        assert "ADOPTED_TAKE_PROFIT_LIQUIDITY" in (_BACKTEST / name).read_text()


# --------------------------------------------------------------------------- #
# 3. 가드가 **실제로 잡는다** — 합성 모듈로 동작 확인 (완료기준 1)
# --------------------------------------------------------------------------- #

_STUB_BOOK_CLI = """
def iter_book_segments(*, take_profit_liquidity=None):
    return []
"""

_STUB_WAN169 = """
def run_cells(*, take_profit_liquidity=None):
    return []

def book_segments_for_payloads(payloads):
    return run_cells()
"""


def _fake_backtest(tmp_path: pathlib.Path, module_src: str) -> pathlib.Path:
    root = tmp_path / "backtest"
    root.mkdir()
    (root / "book_cli.py").write_text(_STUB_BOOK_CLI)
    (root / "wan169_leverage_book.py").write_text(_STUB_WAN169)
    (root / "wan999_fake.py").write_text(module_src)
    return root


def test_guard_catches_a_new_module_that_forgets_the_axis(tmp_path: pathlib.Path) -> None:
    root = _fake_backtest(
        tmp_path,
        "from backtest.wan169_leverage_book import run_cells\n\n"
        "def run_report():\n    return run_cells()\n",
    )
    assert _modules_missing_the_axis(root) == ["wan999_fake.py"]


def test_guard_passes_when_the_axis_is_named(tmp_path: pathlib.Path) -> None:
    root = _fake_backtest(
        tmp_path,
        "from backtest import harness\n"
        "from backtest.wan169_leverage_book import run_cells\n\n"
        "def run_report():\n"
        "    return run_cells(take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY)\n",
    )
    assert _modules_missing_the_axis(root) == []


def test_guard_catches_the_axis_through_a_borrowed_wrapper(tmp_path: pathlib.Path) -> None:
    """뿌리를 직접 안 불러도 잡는다 — wan346/wan359/wan364가 실제로 그 자리다."""
    root = _fake_backtest(
        tmp_path,
        "from backtest.wan169_leverage_book import book_segments_for_payloads\n\n"
        "def run_report():\n    return book_segments_for_payloads([])\n",
    )
    assert _modules_missing_the_axis(root) == ["wan999_fake.py"]


def test_guard_recognizes_the_from_package_import_form(tmp_path: pathlib.Path) -> None:
    """`from backtest import book_cli` 표기로 바꿔도 피할 수 없다."""
    root = _fake_backtest(
        tmp_path,
        "from backtest import book_cli\n\n"
        "def run_report():\n    return book_cli.iter_book_segments()\n",
    )
    assert _modules_missing_the_axis(root) == ["wan999_fake.py"]


def test_guard_ignores_a_per_cell_module_that_never_touches_the_book(
    tmp_path: pathlib.Path,
) -> None:
    """per-cell 전용 모듈까지 잡으면 가드가 헐거워진다 — import가 없으면 대상이 아니다.

    이름 충돌(`run_cells` 같은 흔한 이름)만으로 대상을 고르면 wan123/wan149/wan176/wan229가
    끌려 들어왔다 — 그래서 **import ∧ 호출**을 함께 본다.
    """
    root = _fake_backtest(
        tmp_path,
        "def run_cells():\n    return []\n\ndef run_report():\n    return run_cells()\n",
    )
    assert _modules_missing_the_axis(root) == []


def test_exempt_list_actually_silences_the_guard(tmp_path: pathlib.Path) -> None:
    root = _fake_backtest(
        tmp_path,
        "from backtest.wan169_leverage_book import run_cells\n\n"
        "def run_report():\n    return run_cells()\n",
    )
    assert _modules_missing_the_axis(root, exempt=frozenset({"wan999_fake.py"})) == []


def test_stub_fixture_matches_the_real_book_layer() -> None:
    """합성 픽스처가 **실재하는 이름**을 쓰는지 — 아니면 3절의 확인이 허구가 된다."""
    from backtest import book_cli
    from backtest import wan169_leverage_book as wan169
    from backtest import wan336_same_step_tp as wan336

    assert hasattr(wan169, "run_cells") and hasattr(wan169, "build_book_rows")
    assert hasattr(book_cli, "iter_book_segments")
    # 뿌리에서 한 단계 떨어진 「빌려 쓰는 래퍼」 — wan346/wan359/wan364가 이걸 부른다.
    assert hasattr(wan336, "book_segments_for_payloads")
    for stub in (_STUB_WAN169, _STUB_BOOK_CLI):
        assert "take_profit_liquidity" in stub, "픽스처가 축을 안 나르면 통과 팔이 무의미하다"
