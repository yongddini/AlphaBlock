"""같은 1분 순서 규약(WAN-451 · 사용자 결정 2026-10-15) — 정본 배치의 가격 순서와 배선 가드."""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from backtest import wan436_stoch_crowd_rules as w

_EMPTY = np.array([], dtype=np.int64)


def _trade(
    entry: int, exit_: int, *, price: float, stop: float, stopped: bool = True, cell: str = "A"
) -> w.PlacedTrade:
    return w.PlacedTrade(
        symbol=cell,
        timeframe="1h",
        entry_time=entry,
        exit_time=exit_,
        net_r=0.0,
        stopped=stopped,
        back=False,
        size=1.0,
        entry_price=price,
        stop_price=stop,
        path_times=_EMPTY,
        path_lows=np.array([]),
        path_closes=np.array([]),
    )


def test_adopted_rule_is_price_order() -> None:
    assert w.ADOPTED_SAME_MINUTE == w.SAME_MINUTE_PRICE_ORDER == "price_order"


@pytest.mark.parametrize(
    ("price", "stopped", "fill_first"),
    [
        (7.136, True, True),  # 체결가 > 손절가 — 내려오는 가격이 지정가에 먼저 닿는다(UNI 1d)
        (7.110, True, False),  # 같으면 청산이 먼저(옛 규약과 같다)
        (7.000, True, False),  # 손절가 아래 — 손절가를 먼저 지나 더 내려가 체결
        (7.000, False, True),  # 시간 청산은 그 1분 종가 — 그 1분의 체결은 언제나 먼저
    ],
)
def test_fill_before_exit(price: float, stopped: bool, fill_first: bool) -> None:
    prev = _trade(0, 60_000, price=8.6, stop=7.110, stopped=stopped)
    assert w.fill_before_exit(prev, _trade(60_000, 120_000, price=price, stop=6.0)) is fill_first


def test_conflicts_only_same_cell_same_minute() -> None:
    a = _trade(0, 60_000, price=10.0, stop=9.0)
    b = _trade(60_000, 120_000, price=9.5, stop=8.0)  # 같은 칸 · 같은 1분 · 체결이 먼저
    c = _trade(60_000, 120_000, price=9.5, stop=8.0, cell="B")  # 다른 칸
    d = _trade(180_000, 240_000, price=9.5, stop=8.0)  # 다음 1분 이후
    assert w.same_minute_conflicts([a, b, c, d]) == {("A", "1h", 60_000, 9.5)}


# ------------------------------------------------------------------ 배선 가드

#: 기본값(채택 규칙)을 쓰는 것이 맞는 모듈 — 채택 정본과 이 결정의 근거 표.
# wan460(WAN-460)은 정본(채택 규칙)에서 팔의 방향만 바꿔 잰다 — 채택 규칙 사용자다.
_ADOPTED_USERS = {
    "wan449_tsmom_second_engine.py",
    "wan451_same_minute_order.py",
    "wan460_trend_direction.py",
}
_PLACERS = {"place", "place_arm", "arm_trades"}
_DEFINERS = {"wan436_stoch_crowd_rules.py", "wan439_crash_size_layer.py"}


_CANON_MODULES = {
    "backtest.wan436_stoch_crowd_rules",
    "backtest.wan439_crash_size_layer",
    "backtest.wan449_tsmom_second_engine",
}


def _canon_names(tree: ast.Module, path: Path) -> tuple[set[str], set[str]]:
    """(정본 모듈 별칭, 정본 배치 함수로 쓰이는 맨 이름) — 자기 `place`를 정의한 모듈은 무관하다."""
    aliases: set[str] = set()
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "backtest":
            for a in node.names:
                if f"backtest.{a.name}" in _CANON_MODULES:
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module in _CANON_MODULES:
            bare |= {a.asname or a.name for a in node.names if a.name in _PLACERS}
    if path.name in _DEFINERS:
        bare |= _PLACERS
    return aliases, bare


def _unpinned_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases, bare = _canon_names(tree, path)
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            hit = func.attr in _PLACERS and func.value.id in aliases
        else:
            hit = isinstance(func, ast.Name) and func.id in bare
        if hit and not any(k.arg == "same_minute" for k in node.keywords):
            out.append(node.lineno)
    return out


def test_every_legacy_report_pins_the_same_minute_rule() -> None:
    """정본 배치를 부르는 옛 리포트는 규약을 **명시**한다 — 기본값이 바뀌어도 조용히 안 움직인다.

    기본값은 채택 규칙(가격 순서)이다(WAN-305 — 측정 도구의 기본값은 페이퍼가 뛰는 규칙). 옛 표를
    재현하는 모듈은 `same_minute=w.SAME_MINUTE_EXIT_FIRST`로 고정한다(WAN-365 핀과 같은 패턴).
    """
    root = Path(__file__).resolve().parents[1] / "backtest"
    bad: dict[str, list[int]] = {}
    for path in sorted(root.glob("wan*.py")):
        if path.name in _ADOPTED_USERS:
            continue
        lines = _unpinned_calls(path)
        if lines:
            bad[path.name] = lines
    assert bad == {}
