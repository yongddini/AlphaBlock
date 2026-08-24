"""WAN-365: 소급 취소 → 인과 재-베이스라인 — 라벨이 아니라 **동작**으로 고정한다.

WAN-364가 「축이 실제로 두 층을 함께 움직이는가」를 고정했다면(그 파일은 그대로 유효하다),
이 파일은 **기본값이 옮겨졌다는 사실과 그 파급**을 고정한다:

1. **채택 기본값이 인과다** — `ConfluenceParams()`가 `"bar_close"`이고, 그 값을 두 곳
   (`ADOPTED_INVALIDATION_CANCEL`·파라미터)이 **같이** 본다. 리터럴이 두 곳에 살면 갈라진다.
2. **핀이 실제로 옛 후보 집합을 만든다** — `pin_invalidation_cancel`을 라벨이 아니라
   「무효화 봉의 탭이 사라지는가」로 건다.
3. **미지정과 `bar_open`이 갈린다** — CLI 축이 WAN-159(`none`)·WAN-273(`off`)과 같은 규약을
   따르는지. 안 가르면 「옛 동작」 라벨을 단 채 조용히 인과로 도는 실패가 된다.
4. **북은 격자를 거부한다** — 한 실행이 한 지갑이라 두 취소 시점을 한 표에 넣을 수 없다.
5. **옛 결론 리포트가 실제로 핀을 물고 있다** — 파급 처리가 빠진 모듈이 있으면 그 표는
   조용히 인과 엔진으로 다시 돈다(「안 바꿨다고 믿으면서 바뀐 것」).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from backtest import harness
from backtest.run import build_parser, grid_from_args, iter_combos, main
from backtest.zone_limit_backtest import (
    ADOPTED_INVALIDATION_CANCEL,
    _Candidate,
    invalidation_cutoff,
)
from strategy.models import ConfluenceParams

# --------------------------------------------------------------------------- #
# 1. 채택 기본값이 옮겨졌고, 한 곳에서만 산다
# --------------------------------------------------------------------------- #


def test_adopted_default_is_causal_and_lives_in_one_place() -> None:
    assert ConfluenceParams().invalidation_cancel == "bar_close"
    # 리터럴을 다시 적지 않고 파라미터에서 읽으므로 두 곳이 갈라질 수 없다.
    assert ADOPTED_INVALIDATION_CANCEL is ConfluenceParams().invalidation_cancel
    assert harness.LEGACY_INVALIDATION_CANCEL == "bar_open"
    # 순수 함수의 기본 인자도 같은 값을 물려받는다.
    assert invalidation_cutoff(1_000, htf_ms=60_000) == 61_000


def test_other_adopted_defaults_did_not_move() -> None:
    """이 재-베이스라인은 **취소 시점 하나만** 움직인다(완료기준 6)."""
    params = ConfluenceParams()
    assert params.max_zone_width_atr == 1.28
    assert params.take_profit_r == 1.5
    assert params.zone_limit_offset_bps == 2.0
    assert params.limit_valid_bars == 24
    assert params.deviation_filter is not None
    assert params.deviation_filter.band_bar == "intrabar_live"
    assert params.rsi_gate_mode == "unconditional"
    assert params.short_enabled is False


# --------------------------------------------------------------------------- #
# 2. 핀 — 라벨이 아니라 후보 집합으로 건다
# --------------------------------------------------------------------------- #


def test_pin_helper_only_touches_the_cancel_field() -> None:
    base = ConfluenceParams()
    pinned = harness.pin_invalidation_cancel(base)
    assert pinned.invalidation_cancel == "bar_open"
    assert pinned.model_dump(exclude={"invalidation_cancel"}) == base.model_dump(
        exclude={"invalidation_cancel"}
    )
    assert harness.pin_invalidation_cancel(base, "bar_close").invalidation_cancel == "bar_close"


def test_build_params_splits_unset_from_explicit() -> None:
    """`offset_bps` 규약 — `None`(미지정)은 손대지 않고, 값은 덮어쓴다."""
    assert harness.build_params().invalidation_cancel == "bar_close"
    assert harness.build_params(invalidation_cancel="bar_open").invalidation_cancel == "bar_open"
    # 옛 엔진을 물고 있는 base를 넘겨도 미지정이면 그 값을 지키지 않고 base를 존중한다.
    legacy = harness.pin_invalidation_cancel(ConfluenceParams())
    assert harness.build_params(base=legacy).invalidation_cancel == "bar_open"


# --------------------------------------------------------------------------- #
# 3. CLI 축 — 미지정과 `bar_open`을 가른다
# --------------------------------------------------------------------------- #


def _grid(argv: list[str]) -> object:
    return grid_from_args(build_parser().parse_args(argv))


def test_cli_unspecified_is_adopted_and_bar_open_is_explicit() -> None:
    grid = _grid([])
    assert grid.invalidation_cancels == (None,)  # type: ignore[attr-defined]
    combo = iter_combos(grid)[0]  # type: ignore[arg-type]
    assert combo.invalidation_cancel is None, "미지정을 CLI가 값으로 복사하면 기본값이 갈라진다"

    legacy = _grid(["--invalidation-cancel", "bar_open"])
    assert legacy.invalidation_cancels == ("bar_open",)  # type: ignore[attr-defined]

    both = _grid(["--invalidation-cancel", "bar_close,bar_open"])
    assert both.invalidation_cancels == ("bar_close", "bar_open")  # type: ignore[attr-defined]
    assert len(iter_combos(both)) == 2  # type: ignore[arg-type]


def test_cli_rejects_unknown_cancel_token() -> None:
    with pytest.raises(ValueError, match="invalidation-cancel"):
        _grid(["--invalidation-cancel", "bar_middle"])


def test_book_refuses_a_cancel_grid(capsys: pytest.CaptureFixture[str]) -> None:
    """한 실행 = 한 지갑이라 두 취소 시점을 한 표에 넣을 수 없다(WAN-316과 같은 이유)."""
    code = main(["--positions", "book", "--invalidation-cancel", "bar_close,bar_open"])
    assert code == 2
    assert "한 지갑" in capsys.readouterr().err


def test_book_accepts_a_single_cancel_value() -> None:
    """북 경로는 `bar_open`을 **받아** 옛 채택 북을 그대로 재현할 수 있어야 한다."""
    parser = build_parser()
    args = parser.parse_args(["--positions", "book", "--invalidation-cancel", "bar_open"])
    assert args.invalidation_cancel == "bar_open"
    # 거부 목록에 들어 있으면 이 경로가 아예 막힌다 — 그러면 옛 북 재현 명령이 없어진다.
    from backtest.run import _book_rejected_flags

    assert "--invalidation-cancel" not in _book_rejected_flags(args)


# --------------------------------------------------------------------------- #
# 4. 파급 — 옛 결론 리포트가 실제로 핀을 물고 있다
# --------------------------------------------------------------------------- #

#: 엔진 진입점의 **뿌리** — 여기서 전이 폐포를 구한다.
_ENGINE_ROOT = "build_zone_limit_candidates"

#: 엔진 파라미터를 나르는 키워드.
_PARAM_KWARGS = frozenset({"params", "confluence_params", "pool_params"})

#: 고정하지 **않는** 모듈과 그 이유 — 결정문 §4-2의 표와 같은 집합이어야 한다.
#:
#: 🚨 **기준은 「이 팔 = 인자 없는 채택 실행」이라는 동일성 검산을 계약으로 갖는가**이다.
#: 그런 모듈에 옛 엔진을 핀으로 박으면 **라벨이 거짓이 된다** — 이 저장소가 가장 경계하는
#: 실패다(WAN-91/95/112/123/159). 그 표가 낡는 것은 맞는 결과이고(`LEGACY_MAX_ZONE_WIDTH_ATR`
#: 독스트링의 「지금 채택된 것을 재는 리포트는 고정하지 않는다」 규약 그대로), 그 검산이
#: 깨지는 것은 틀린 결과다. ⚠️ 이 집합은 **추측이 아니라 테스트가 골라냈다** — 처음에 넷을
#: 잘못 고정했더니 각 모듈의 `≡ run_once` 검산이 실패해 드러났다.
_UNPINNED_BY_DESIGN: dict[str, str] = {
    # 「지금 채택된 것」을 재는 리포트다 — 기본값이 움직이면 그 수치는 낡아야 맞다.
    "wan95_zone_limit_report.py": "채택 성과 재산출 대상",
    # 이 축이 그 모듈의 실험 변수다(두 팔이 자기 입력으로 명시한다).
    "wan364_invalidation_cancel.py": "축이 실험 변수",
    # 아래 넷은 「이 팔 == 인자 없는 채택 실행」을 실데이터 검산으로 **못 박아 둔** 모듈이다.
    "wan197_guard_with_filter.py": "default 팔 ≡ 인자 없는 backtest.run (검산)",
    "wan204_ob_extension_tp.py": "팔 A ≡ harness.run_once (검산)",
    "wan248_zone_position_null.py": "실제 팔 ≡ run_once warm-OOS (검산)",
    "wan350_conservative_null.py": "팔 A = 채택 기본값 그 자체 (검산)",
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _engine_reaching_functions() -> frozenset[str]:
    """`build_zone_limit_candidates`에 (이름 기준) 닿는 함수의 전이 폐포.

    호출부가 부르는 이름이 `run_once`처럼 뿌리에서 몇 단계 떨어져 있어도 잡으려는 것이다 —
    `build_cell`(wan142)·`run_random_control_b_evals`(wan70)처럼 **한 모듈이 남에게 빌려주는
    셀 빌더**가 실제로 그 자리였다. 목록을 손으로 적으면 그런 것이 빠진다.
    """
    calls: dict[str, set[str]] = {}
    for path in sorted(pathlib.Path("backtest").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = {_call_name(c) for c in ast.walk(node) if isinstance(c, ast.Call)}
                calls.setdefault(node.name, set()).update(names - {""})
    reach = {_ENGINE_ROOT}
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in reach and called & reach:
                reach.add(name)
                changed = True
    return frozenset(reach)


def test_every_legacy_report_pins_the_old_cancel_time() -> None:
    """파급 처리가 빠진 모듈은 그 표가 조용히 인과 엔진으로 다시 돈다.

    ⚠️ 이 테스트는 **문자열이 아니라 「엔진에 닿는 함수에 파라미터를 넘기는가」**로 대상을
    고른다 — 새 리포트를 추가하면서 핀을 잊으면 여기서 걸린다. 새 모듈이 채택 규칙을 따라야
    하면(WAN-305) `_UNPINNED_BY_DESIGN`에 이유와 함께 적는다.

    ⚠️ **고정 지점까지 강제하지는 않는다** — 호출부에서 감싸도, 그 모듈의 파라미터 빌더에서
    `invalidation_cancel=`로 넘겨도 된다(둘 다 실제로 쓰인다). 여기서 잡는 것은 **한 모듈이
    이 축을 아예 언급조차 하지 않는 것**이다.
    """
    reach = _engine_reaching_functions()
    assert "run_once" in reach and "run_cells" in reach and "build_cell" in reach
    missing: list[str] = []
    for path in sorted(pathlib.Path("backtest").glob("wan*.py")):
        if path.name in _UNPINNED_BY_DESIGN:
            continue
        src = path.read_text()
        tree = ast.parse(src)
        local = {
            n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        feeds_engine = any(
            isinstance(n, ast.Call)
            and _call_name(n) in reach
            and _call_name(n) not in local
            and {k.arg for k in n.keywords} & _PARAM_KWARGS
            for n in ast.walk(tree)
        )
        if not feeds_engine and "run_cells(" not in src:
            continue
        if "pin_invalidation_cancel" in src or "LEGACY_INVALIDATION_CANCEL" in src:
            continue
        missing.append(path.name)
    assert not missing, f"취소 시점을 명시하지 않은 리포트 모듈: {missing}"


def test_unpinned_modules_are_declared_not_accidental() -> None:
    """예외는 **선언**이어야 한다 — 목록이 낡으면(파일이 사라지면) 시끄럽게 죽는다."""
    for name in _UNPINNED_BY_DESIGN:
        assert (pathlib.Path("backtest") / name).exists(), name


# --------------------------------------------------------------------------- #
# 5. 실데이터 — 핀이 라벨이 아니라 **후보 집합**을 바꾼다
# --------------------------------------------------------------------------- #

_REAL_SYMBOL, _REAL_TF = "BTC/USDT:USDT", "4h"
_REAL_START, _REAL_END = "2024-01-01", "2026-07-22"


def test_pin_actually_removes_break_bar_taps_on_real_data() -> None:
    """`pin_invalidation_cancel`이 「그 시절 엔진」을 실제로 만드는가.

    라벨만 검사하면 파라미터가 엔진에 안 닿아도 통과한다 — 그래서 **되살아난 거래가
    0건인지**로 건다(WAN-364 §2의 `entry_after_invalidation` 관측 필드 재사용).
    """
    from backtest.run import parse_date_ms
    from backtest.zone_limit_backtest import build_zone_limit_candidates

    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        need_1m=True,
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")

    ob_result = harness.detect_order_blocks(market)
    cfg = harness.build_config(_REAL_TF)

    def _cands(params: ConfluenceParams) -> list[_Candidate]:
        cands, _stats = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            _REAL_TF,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
        )
        return list(cands)

    adopted = _cands(harness.build_params())
    pinned = _cands(harness.pin_invalidation_cancel(harness.build_params()))
    assert adopted, "실데이터가 있는데 후보가 비었다"
    assert len(pinned) < len(adopted), "핀이 무효화 봉의 탭을 하나도 안 지웠다(라벨만 붙었다)"
    assert all(not c.entry_after_invalidation for c in pinned)
    assert any(c.entry_after_invalidation for c in adopted)


def test_cancel_grid_without_positions_folds_to_per_cell() -> None:
    """콤마 격자는 `--positions` 없이 주면 per-cell로 접힌다(다른 전략 축과 같은 규약).

    단일값은 **접히지 않는다** — 그게 「옛 채택 북 재현」 레시피이기 때문이다.
    """
    from backtest.run import _book_from_args

    parser = build_parser()
    single = parser.parse_args(["--invalidation-cancel", "bar_open"])
    assert _book_from_args(single) is not None, "단일값이 per-cell로 접히면 옛 북 재현이 사라진다"

    grid = parser.parse_args(["--invalidation-cancel", "bar_close,bar_open"])
    assert _book_from_args(grid) is None, "격자가 북으로 가면 한 지갑에 두 팔이 들어간다"
