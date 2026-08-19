"""타임라인 적재 프로파일러 (WAN-324 §0).

계측 도구라 「무엇을 재는가」보다 **재느라 대상을 바꾸지 않는가**가 중요하다 — 프로덕션
경로에 오버헤드나 패치가 남으면 그 순간 이 측정도 신뢰를 잃는다.
"""

from __future__ import annotations

import pytest

from live.timeline_profile import (
    SHAPE_PER_CELL,
    SHAPE_SHARED,
    DayProfile,
    SymbolProfile,
    profile_day,
    render_profile,
)
from live.trade_timeline import resolve_day_window

_SYMBOL = "BTC/USDT:USDT"
_DAY = "2026-07-15"
_DAY_MS = 86_400_000


def test_timed_store_loads_restores_the_original_method() -> None:
    """계측은 구간에서만 살아 있고 끝나면 원복한다 — 프로덕션에 패치가 남지 않는다."""
    from data.storage import OhlcvStore
    from live.timeline_profile import _timed_store_loads

    original = OhlcvStore.load
    with _timed_store_loads() as totals:
        assert OhlcvStore.load is not original
        assert totals == {}
    assert OhlcvStore.load is original


def test_unknown_shape_is_rejected() -> None:
    """모양 이름을 잘못 주면 거부한다 — 조용히 한쪽으로 접으면 라벨만 붙는다."""
    from live.timeline_profile import _load_markets

    with pytest.raises(ValueError, match="알 수 없는 적재 모양"):
        _load_markets(_SYMBOL, ["1h"], shape="쉐어드", start_ms=0, end_ms=1)


def test_render_profile_reports_stages_and_contention() -> None:
    """리포트는 단계·규모와 함께 **어느 머신에서 쟀는지**를 찍는다(완료 기준 1)."""
    profile = DayProfile(
        day_key="2026-07-20",
        shape=SHAPE_SHARED,
        warmup_days=120,
        jobs=1,
        machine="Darwin arm64 · CPU 8",
        measured_at_ms=1_784_500_000_000,
        wall_s=135.5,
        symbols=(SymbolProfile(_SYMBOL, htf_sql_s=0.14, sql_1m_s=0.97, reads_1m=1),),
    )
    text = render_profile(profile)
    assert "1분봉 SQL 읽기 (1회)" in text
    assert "Darwin arm64" in text
    assert "디스크를 다툰다" in text  # 경합을 함께 적으라는 경고가 리포트에 남는다.
    assert "KST" in text  # 시각은 KST(WAN-172).


@pytest.fixture
def _real_day() -> tuple[int, int, str]:
    from backtest.harness import load_market_data

    start_ms, end_ms, day_key = resolve_day_window(_DAY)
    if load_market_data(
        _SYMBOL, "1h", start_ms=start_ms - 30 * _DAY_MS, end_ms=end_ms, need_1m=False, funding=False
    ).empty:
        pytest.skip(f"{_SYMBOL} 실데이터가 없어 프로파일 회귀를 건너뜁니다(CI 기본).")
    return start_ms, end_ms, day_key


def test_shared_shape_reads_1m_once_per_symbol(_real_day: tuple[int, int, str]) -> None:
    """공유 모양은 심볼당 1분봉을 **한 번만** 읽는다 — WAN-324의 헤드라인을 동작으로 고정.

    ⚠️ 라벨이 아니라 **읽은 횟수**로 잰다: 이름만 `shared`이고 여전히 TF마다 읽으면
    이 테스트가 걸린다(WAN-91/95/112/123/159 부류의 조용한 실패 방지).
    """
    start_ms, end_ms, day_key = _real_day
    timeframes = ["15m", "1h", "2h", "4h"]
    common = {
        "day_start_ms": start_ms,
        "day_end_ms": end_ms,
        "day_key": day_key,
        "symbols": [_SYMBOL],
        "timeframes": timeframes,
        "warmup_days": 30,
    }
    shared = profile_day(shape=SHAPE_SHARED, **common)  # type: ignore[arg-type]
    per_cell = profile_day(shape=SHAPE_PER_CELL, **common)  # type: ignore[arg-type]

    assert shared.reads_1m == 1
    assert per_cell.reads_1m == len(timeframes)
    # 같은 일을 한다 — 셀 규모(봉 수·행 수)가 두 모양에서 같아야 시간 비교가 성립한다.
    assert [(c.htf_bars, c.bars_1m, c.rows) for s in shared.symbols for c in s.cells] == [
        (c.htf_bars, c.bars_1m, c.rows) for s in per_cell.symbols for c in s.cells
    ]


def test_cli_timeline_profile_routes() -> None:
    """`alphablock timeline-profile`이 라우팅되고 **기본 모양이 지금 엔진**이다."""
    from cli.main import build_parser, cmd_timeline_profile

    ns = build_parser().parse_args(["timeline-profile", "--day", "2026-07-20"])
    assert ns.func is cmd_timeline_profile
    assert ns.day == "2026-07-20"
    assert ns.shape == SHAPE_SHARED  # 기본은 지금 도는 모양(측정 도구는 채택 규칙을 본다).
    assert ns.warmup_days is None  # 적재 경로와 같은 기본 워밍업을 물려받는다.

    old = build_parser().parse_args(["timeline-profile", "--shape", SHAPE_PER_CELL])
    assert old.shape == SHAPE_PER_CELL
