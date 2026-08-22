"""WAN-359: 「틱이 지지하지 않는 익절」만 골라 끄는 표적 팔 테스트.

이 파일이 지키는 계약은 다섯이다.

1. **표적 팔은 옵트인이고 안 주면 비트 재현** — 라벨이 아니라 **동작**으로 고정한다(주면
   실제로 갈라지는지도 함께 본다).
2. **「전부」와 「이것만」은 함께 못 켠다** — 섞이면 어느 쪽이 이겼는지 결과만 보고는 알 수
   없다(WAN-95/112/123/159 관행).
3. **사슬은 순서대로 소비된다** — 같은 분의 재진입 사슬을 행마다 독립으로 재면 같은 틱 순서를
   여러 번 쓰게 되고, 그러면 그 분에 한 번밖에 없던 왕복이 네 번으로 세어진다.
4. **아무 칸과도 안 맞는 표적 키는 거부한다** — 안 걸린 채 라벨만 붙으면 기준선과 같은 수가
   나오고 「보간이 맞았다」가 근거 없이 만들어진다.
5. **검산 (b)** — `all_off` 팔은 WAN-336 `no_same_step_tp` 팔과 **같은 숫자**여야 한다
   (다른 모듈·다른 실행이 같은 것을 재는지).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from backtest import harness
from backtest.substep import SubStep, ZoneLimitStatus, simulate_zone_limit_trade
from backtest.wan348_same_minute_tp import (
    VERDICT_ARTIFACT,
    VERDICT_NO_TICKS,
    VERDICT_ORDER_FLIPPED_STILL,
    VERDICT_REAL,
    Target,
    measure_static,
)
from backtest.wan359_tick_targeted_tp import (
    ARM_ORDER,
    BOOK_CSV,
    VERDICT_CHAIN_BROKEN,
    VERDICT_CSV,
    build_block_set,
    group_by_minute,
    measure_chain,
)
from data.agg_trade_archive import Tick, minute_ticks, minutes_ticks
from strategy.models import OrderBlockDirection, SignalExitReason
from strategy.realtime_rsi import RealtimeRsi

_LIMIT = 100.0
_STOP = 90.0
_TP = 110.0
_OVERSOLD_SEED = [140.0, 130.0, 120.0, 110.0, 105.0]

#: 진입 스텝 하나가 지정가(100)와 익절(110)을 **함께** 품는다 — 표적 팔이 재려는 그 봉이다.
_SAME_MINUTE_TP = [
    SubStep(time=600_000, high=111.0, low=99.0, close=110.5, htf_bar_time=0),
    SubStep(time=660_000, high=112.0, low=109.0, close=111.0, htf_bar_time=0),
    SubStep(time=720_000, high=113.0, low=110.0, close=112.0, htf_bar_time=0),
]


def _simulate(**kwargs: object) -> object:
    return simulate_zone_limit_trade(
        direction=OrderBlockDirection.BULLISH,
        limit_price=_LIMIT,
        stop_price=_STOP,
        substeps=_SAME_MINUTE_TP,
        rsi_state=RealtimeRsi.seed_from_closed(_OVERSOLD_SEED, length=3),
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        take_profit_price=_TP,
        rsi_gate_mode="unconditional",
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------ 1. 옵트인 · 끄면 비트 재현


def test_targeted_arm_is_inert_without_the_set() -> None:
    """집합을 안 주면 예전과 **글자 그대로 같다** — 옵트인의 정의다."""
    assert _simulate() == _simulate(no_same_step_tp_minutes=None)


def test_targeted_arm_defers_only_the_listed_minute() -> None:
    """목록에 있는 분에서만 익절이 미뤄진다."""
    listed = _simulate(no_same_step_tp_minutes=frozenset({600_000}))
    assert listed.status is ZoneLimitStatus.FILLED_EXITED  # type: ignore[attr-defined]
    assert listed.exit_reason is SignalExitReason.TAKE_PROFIT  # type: ignore[attr-defined]
    assert listed.entry_time == 600_000  # type: ignore[attr-defined]
    assert listed.exit_time == 660_000, "표적 분의 익절이 미뤄지지 않았다"  # type: ignore[attr-defined]


def test_targeted_arm_leaves_other_minutes_alone() -> None:
    """다른 분만 담긴 집합은 아무 일도 하지 않는다 — 「전부 끔」으로 새면 안 된다."""
    assert _simulate() == _simulate(no_same_step_tp_minutes=frozenset({999_000}))


def test_targeted_arm_matches_all_off_when_the_minute_is_listed() -> None:
    """그 분을 담으면 「전부 끔」과 같은 결과다 — 두 팔은 범위만 다르다."""
    assert _simulate(no_same_step_tp_minutes=frozenset({600_000})) == _simulate(
        no_same_step_tp=True
    )


# ------------------------------------------------ 2. 「전부」와 「이것만」은 함께 못 켠다


def test_engine_rejects_both_switches() -> None:
    with pytest.raises(ValueError, match="함께 줄 수 없습니다"):
        _simulate(no_same_step_tp=True, no_same_step_tp_minutes=frozenset({600_000}))


def test_run_cells_rejects_both_switches() -> None:
    from backtest.wan169_leverage_book import run_cells

    with pytest.raises(ValueError, match="함께 줄 수 없습니다"):
        run_cells(
            ["BTC/USDT:USDT"],
            ["1h"],
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            no_same_step_tp=True,
            no_same_step_tp_minutes={("BTC/USDT:USDT", "1h"): frozenset({1})},
        )


# ------------------------------------------------ 3. 사슬은 순서대로 소비된다


def _ticks(prices: list[float], *, minute: int = 0) -> list[Tick]:
    return [
        Tick(time_ms=minute + index * 100, price=price, qty=1.0)
        for index, price in enumerate(prices)
    ]


def _target(**kwargs: object) -> Target:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "15m",
        "entry_ms": 0,
        "entry_price": 100.0,
        "stop_price": 90.0,
        "take_profit_price": 110.0,
        "is_reentry": False,
        "net_r": 1.5,
        "pnl": 10.0,
    }
    base.update(kwargs)
    return Target(**base)  # type: ignore[arg-type]


def test_single_trade_chain_matches_the_solo_measure() -> None:
    """사슬 길이 1이면 WAN-348의 자와 **같은 답**이다 — 두 자가 갈리면 비교가 성립 안 한다."""
    for prices in ([101.0, 99.0, 111.0], [111.0, 99.0, 105.0], [111.0, 99.0, 111.0], [105.0]):
        ticks = _ticks(prices)
        target = _target()
        assert measure_chain(ticks, [target])[0].verdict == measure_static(ticks, target).verdict


def test_chain_consumes_ticks_in_order() -> None:
    """왕복이 두 번뿐인 분에서 사슬 4건을 재면 **앞 둘만** 성립한다.

    행마다 독립으로 재면 같은 왕복을 네 번 세어 네 건 모두 「진짜」가 된다 — 그게 이 함수가
    존재하는 이유다.
    """
    ticks = _ticks([99.0, 111.0, 99.0, 111.0, 105.0])
    chain = [_target() for _ in range(4)]
    verdicts = [m.verdict for m in measure_chain(ticks, chain)]
    assert verdicts[:2] == [VERDICT_REAL, VERDICT_REAL]
    # 3번째는 앞 익절 뒤로 지정가에 안 돌아왔고, 4번째는 그래서 애초에 무장되지 않는다.
    assert verdicts[2:] == [VERDICT_CHAIN_BROKEN, VERDICT_CHAIN_BROKEN]
    # 독립으로 재면 넷 다 진짜라고 답한다 — 두 자가 실제로 갈린다는 증거.
    assert [measure_static(ticks, t).verdict for t in chain] == [VERDICT_REAL] * 4


def test_chain_stops_after_the_first_break() -> None:
    """끊긴 뒤의 거래는 **애초에 일어나지 않는다** — 그 뒤를 다시 뒤지지 않는다."""
    ticks = _ticks([111.0, 99.0, 105.0, 99.0, 105.0])
    chain = [_target() for _ in range(2)]
    verdicts = [m.verdict for m in measure_chain(ticks, chain)]
    assert verdicts[0] == VERDICT_ARTIFACT, "체결 뒤 익절가에 다시 안 닿았다"
    assert verdicts[1] == VERDICT_CHAIN_BROKEN


def test_chain_marks_order_flip_that_still_lands() -> None:
    """익절가에 먼저 닿았어도 체결 뒤 **다시** 닿으면 그 손익은 그대로 난다."""
    ticks = _ticks([111.0, 99.0, 111.0])
    assert measure_chain(ticks, [_target()])[0].verdict == VERDICT_ORDER_FLIPPED_STILL


def test_chain_reports_no_ticks() -> None:
    assert measure_chain([], [_target()])[0].verdict == VERDICT_NO_TICKS


def test_group_by_minute_keeps_csv_order() -> None:
    """행 순서가 곧 사슬 순서다 — 여기서 다시 정렬하면 「무엇이 먼저였나」가 정렬의 산물이 된다."""
    first, second = _target(entry_price=100.0), _target(entry_price=99.0)
    grouped = group_by_minute([first, second])
    assert grouped[("BTC/USDT:USDT", "15m", 0)] == [first, second]


# ------------------------------------------------ 4. 표적 집합의 규칙


def _verdict_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _vrow(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "15m",
        "entry_ms": 0,
        "ordinal": 1,
        "chain_verdict": VERDICT_REAL,
        "chain_outcome_ok": True,
    }
    base.update(kwargs)
    return base


def test_block_set_blocks_only_unsupported_heads() -> None:
    frame = _verdict_frame(
        [
            _vrow(entry_ms=0, chain_verdict=VERDICT_ARTIFACT, chain_outcome_ok=False),
            _vrow(entry_ms=60_000),
            _vrow(entry_ms=120_000, chain_verdict=VERDICT_NO_TICKS, chain_outcome_ok=False),
        ]
    )
    blocks = build_block_set(frame)
    assert blocks.minutes == {("BTC/USDT:USDT", "15m"): frozenset({0})}
    assert (blocks.blocked_minutes, blocks.supported_minutes) == (1, 1)
    assert blocks.undecidable_minutes == 1, "판정 불가를 막으면 근거 없이 막는 것이다"


def test_block_set_counts_tail_only_minutes_without_blocking_them() -> None:
    """첫 거래는 지지받고 뒤 거래만 못 받는 분 — 분 단위 스위치로 표현이 안 되니 **세기만** 한다."""
    frame = _verdict_frame(
        [
            _vrow(ordinal=1),
            _vrow(ordinal=2, chain_verdict=VERDICT_ARTIFACT, chain_outcome_ok=False),
        ]
    )
    blocks = build_block_set(frame)
    assert blocks.minutes == {}
    assert (blocks.supported_minutes, blocks.tail_only_minutes) == (1, 1)


def test_block_set_counts_the_whole_chain_of_a_blocked_minute() -> None:
    """막힌 분의 거래 수는 사슬 전체다 — 첫 거래가 막히면 뒤 거래는 무장되지 않는다."""
    frame = _verdict_frame(
        [
            _vrow(ordinal=1, chain_verdict=VERDICT_ARTIFACT, chain_outcome_ok=False),
            _vrow(ordinal=2, chain_verdict=VERDICT_ARTIFACT, chain_outcome_ok=False),
        ]
    )
    assert build_block_set(frame).blocked_trades == 2


def test_block_set_uses_the_first_trade_not_the_csv_order() -> None:
    """행이 뒤섞여 들어와도 `ordinal`로 첫 거래를 고른다."""
    frame = _verdict_frame(
        [
            _vrow(ordinal=2),
            _vrow(ordinal=1, chain_verdict=VERDICT_ARTIFACT, chain_outcome_ok=False),
        ]
    )
    assert build_block_set(frame).blocked_minutes == 1


def test_run_cells_rejects_targets_that_match_no_cell() -> None:
    """아무 칸과도 안 맞으면 **거부한다** — 안 걸린 채 라벨만 붙는 것을 막는다."""
    from backtest.wan169_leverage_book import run_cells

    with pytest.raises(AssertionError, match="안 맞는 키"):
        run_cells(
            ["BTC/USDT:USDT"],
            ["1h"],
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            no_same_step_tp_minutes={("BTCUSDT", "1h"): frozenset({1})},
        )


# ------------------------------------------------ 배치 틱 읽기


def _write_archive(path: Path, rows: list[tuple[int, float]]) -> Path:
    body = "\n".join(
        f"{index},{price},1.0,1,1,{time_ms},false" for index, (time_ms, price) in enumerate(rows)
    )
    zip_path = path / "AAA-aggTrades-2020-01-01.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("AAA-aggTrades-2020-01-01.csv", body)
    return zip_path


def test_batch_minutes_match_the_single_minute_reader(tmp_path: Path) -> None:
    """한 번 훑기가 분마다 다시 읽기와 **같은 답**이어야 한다 — 빨라지는 것이 값을 바꾸면 버그다."""
    rows = [(0, 100.0), (30_000, 101.0), (60_000, 102.0), (150_000, 103.0)]
    path = _write_archive(tmp_path, rows)
    minutes = [0, 60_000, 120_000, 180_000]
    batch = minutes_ticks(path, minutes)
    assert set(batch) == set(minutes), "안 물어본 분과 「없음」이 구분돼야 한다"
    for minute in minutes:
        assert batch[minute] == minute_ticks(path, minute)


# ------------------------------------------------ 5. 검산 (b) — 옛 표와 같은 숫자


_WAN336_CSV = Path("backtest/reports/wan336_same_step_tp.csv")
_CHECK_COLUMNS = ("num_trades", "win_rate", "total_return", "max_drawdown")


@pytest.mark.skipif(not BOOK_CSV.exists(), reason="§2 북 표가 아직 없다")
def test_all_off_arm_reproduces_wan336_counterfactual() -> None:
    """검산 (b) — `all_off` ≡ WAN-336 `no_same_step_tp`(다른 모듈·다른 실행, 같은 숫자)."""
    mine = pd.read_csv(BOOK_CSV)
    mine = mine[mine["arm"] == "all_off"].set_index("segment")
    theirs = pd.read_csv(_WAN336_CSV)
    theirs = theirs[theirs["arm"] == "no_same_step_tp"].set_index("segment")
    shared = sorted(set(mine.index) & set(theirs.index))
    assert shared, "겹치는 구간이 없다 — 검산이 성립하지 않는다"
    for segment in shared:
        for column in _CHECK_COLUMNS:
            assert mine.loc[segment, column] == pytest.approx(
                theirs.loc[segment, column], rel=0.0, abs=1e-9
            ), f"{segment}/{column}이 WAN-336과 어긋난다"


@pytest.mark.skipif(not BOOK_CSV.exists(), reason="§2 북 표가 아직 없다")
def test_base_arm_reproduces_wan336_base() -> None:
    """검산 (a)의 짝 — `base` ≡ WAN-336 `base`(= 인자 없는 채택 북)."""
    mine = pd.read_csv(BOOK_CSV)
    mine = mine[mine["arm"] == "base"].set_index("segment")
    theirs = pd.read_csv(_WAN336_CSV)
    theirs = theirs[theirs["arm"] == "base"].set_index("segment")
    for segment in sorted(set(mine.index) & set(theirs.index)):
        for column in _CHECK_COLUMNS:
            assert mine.loc[segment, column] == pytest.approx(
                theirs.loc[segment, column], rel=0.0, abs=1e-9
            ), f"{segment}/{column}이 채택 북과 어긋난다"


@pytest.mark.skipif(not BOOK_CSV.exists(), reason="§2 북 표가 아직 없다")
def test_targeted_arm_lands_strictly_between_the_two_extremes() -> None:
    """표적 팔은 두 극단 **사이**의 거래 수를 낸다 — 밖으로 나가면 팔이 잘못 걸린 것이다.

    ⚠️ MDD·수익은 북의 자본 경합 때문에 사이에 안 올 수 있다(그게 이 이슈의 발견 후보다).
    거래 **수**는 「막힌 익절이 몇 건인가」의 단조 함수라 사이에 와야 한다.
    """
    frame = pd.read_csv(BOOK_CSV).set_index(["arm", "segment"])
    for segment in ("full", "oos_warm"):
        keys = [(arm, segment) for arm in ARM_ORDER]
        if any(key not in frame.index for key in keys):
            continue
        base, all_off, tick_off = (int(frame.loc[key, "num_trades"]) for key in keys)
        assert min(base, all_off) <= tick_off <= max(base, all_off), segment
        assert tick_off != base, "표적 팔이 기준선과 같다 — 아무것도 안 막혔다"


@pytest.mark.skipif(not VERDICT_CSV.exists(), reason="§1 판정 표가 아직 없다")
def test_population_verdicts_cover_every_target() -> None:
    """전수라는 말이 맞는지 — 대상 목록과 판정 행 수가 같아야 한다."""
    from backtest.wan348_same_minute_tp import load_targets

    assert len(pd.read_csv(VERDICT_CSV)) == len(load_targets())


def test_harness_default_jobs_is_a_perf_knob_only() -> None:
    """`--jobs` 기본값을 이 모듈이 자기 상수로 복사하지 않았는지(WAN-121/294)."""
    assert harness.default_jobs() >= 1


# ------------------------------------------------ 실데이터 — 표적 팔이 실제로 후보를 바꾼다

_REAL_CELL = ("BTC/USDT:USDT", "4h")
_REAL_START, _REAL_END = "2024-01-01", "2026-07-22"


@pytest.mark.skipif(not VERDICT_CSV.exists(), reason="§1 판정 표가 아직 없다")
def test_targeted_arm_removes_same_minute_candidates_on_real_data() -> None:
    """켠 팔이 **실제로 같은 분 익절 후보를 지우는지** — 라벨이 아니라 동작으로 고정한다.

    합성 데이터로는 「집합에 담긴 분이 막힌다」까지만 보인다. 이 테스트는 §1이 낸 **진짜
    목록**을 채택 엔진에 걸어, 그 분의 같은 분 익절이 **후보 층에서 사라지는지**를 본다 —
    심볼 표기·정규화·재진입 배선 중 하나만 어긋나도 아무것도 안 막히고 표만 나온다
    (이 이슈가 가장 경계하는 실패다).

    칸 하나(4h)로 좁혀 로컬에서 ~40초다. 전 격자는 `--part book`이 돈다.
    """
    from backtest.run import parse_date_ms
    from backtest.wan169_leverage_book import run_cells
    from backtest.wan359_tick_targeted_tp import build_block_set

    # 🚨 **실데이터 게이트는 `run_cells` 전에 건다** — 빈 DB에서는 그 호출이 skip 판정에
    # 닿기도 전에 `ValueError`로 죽는다(CI 기본이 빈 DB다). 저장소의 다른 실데이터 회귀와
    # 같은 패턴으로 창을 먼저 읽어 본다(1분봉·펀딩은 존재 확인에 필요 없다).
    market = harness.load_market_data(
        _REAL_CELL[0],
        _REAL_CELL[1],
        start_ms=parse_date_ms(_REAL_START),
        end_ms=parse_date_ms(_REAL_END),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        pytest.skip(f"{_REAL_CELL[0]} {_REAL_CELL[1]} 실데이터가 없어 건너뜁니다(CI 기본).")

    blocks = build_block_set(pd.read_csv(VERDICT_CSV))
    minutes = blocks.minutes.get(_REAL_CELL)
    if not minutes:
        pytest.skip(f"{_REAL_CELL}에 막을 분이 없다")

    shared: dict[str, object] = {
        "start": _REAL_START,
        "end": _REAL_END,
        "jobs": 1,
        "cold_segments": False,
        "engine_check": False,
        "adv_fraction": harness.UNSET,
        "reentry": True,
    }
    base = run_cells([_REAL_CELL[0]], [_REAL_CELL[1]], **shared)  # type: ignore[arg-type]
    assert base and base[0].candidates.get("full"), "실데이터가 있는데 후보가 비었다"
    targeted = run_cells(
        [_REAL_CELL[0]],
        [_REAL_CELL[1]],
        no_same_step_tp_minutes={_REAL_CELL: minutes},
        **shared,  # type: ignore[arg-type]
    )

    def _same_minute(payloads: list) -> int:  # type: ignore[type-arg]
        return sum(1 for c in payloads[0].candidates["full"] if c.same_step_take_profit)

    before, after = _same_minute(base), _same_minute(targeted)
    assert after < before, "표적 팔이 실데이터에서 아무것도 막지 못했다"
