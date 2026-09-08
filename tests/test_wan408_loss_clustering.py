"""WAN-408 §0 — 손실 집중·동시성 관측의 회귀 테스트.

🚨 **이 파일의 존재 이유는 완료기준 2다** — 이슈가 겪은 「대조군이 동작하지 않았다」를
**동작으로** 막는다: 종목 내 순열은 항등임을 증명하고(그게 그때의 정체다), 동작하는 널
(종목별 원형 시프트)은 **정렬을 실제로 깬다**는 것을 값으로 건다. 널이 조용히 항등으로
퇴화하면 여기서 먼저 죽는다.
"""

from __future__ import annotations

import inspect
import random

import pytest

from backtest import harness
from backtest.leverage_book import LeverageBookParams
from backtest.wan408_loss_clustering import (
    BUCKETS,
    PRIMARY_SEGMENT,
    StopEvent,
    TradeFact,
    checksums,
    concentration_rows,
    concurrency_rows,
    concurrency_share,
    correlation_rows,
    day_rows,
    identity_probe,
    leading_rows,
    leading_states,
    permute_within_symbol,
    shift_events,
)

HOUR = 3_600_000
DAY = 86_400_000
#: 2024-01-02 00:00 KST 근방 — 값이 아니라 「하루가 갈린다」만 쓴다.
BASE = 1_704_150_000_000


#: 뭉침 사이의 간격 — 🚨 **널이 뜻을 가지려면 시계열이 성겨야 한다**. 매일 손절나는 세계를
#: 지어 놓고 시프트하면 모든 종목이 여전히 모든 버킷에 있어 값이 안 움직인다(그건 널의 실패가
#: 아니라 픽스처의 실패다).
BURST_GAP_DAYS = 20

#: 종목 수 — 문턱 8이 뜻을 가지려면 8종목 이상이어야 한다(6종목이면 실측도 널도 0이라
#: 「같은 값 draw」가 정상적으로 나오고, 그건 널의 퇴화가 아니다).
SYMBOLS = 10


def _aligned_events(*, symbols: int = SYMBOLS, bursts: int = 20) -> list[StopEvent]:
    """**정렬된** 세계 — 모든 종목이 같은 날 한꺼번에 손절난다(폭락일의 극단판)."""
    return [
        StopEvent(f"S{s}", BASE + burst * BURST_GAP_DAYS * DAY)
        for burst in range(bursts)
        for s in range(symbols)
    ]


def _spread_events(*, symbols: int = SYMBOLS, bursts: int = 20) -> list[StopEvent]:
    """**어긋난** 세계 — 종목마다 다른 날에 손절난다."""
    return [
        StopEvent(f"S{s}", BASE + (burst * BURST_GAP_DAYS + s) * DAY)
        for burst in range(bursts)
        for s in range(symbols)
    ]


def _fact(symbol: str, entry: int, exit_: int, *, stop: bool, net_r: float) -> TradeFact:
    return TradeFact(
        symbol=symbol,
        timeframe="1h",
        entry_time=entry,
        exit_time=exit_,
        is_stop=stop,
        net_r=net_r,
        is_reentry=False,
    )


# --------------------------------------------------------------------------- #
# §0-2 — 널
# --------------------------------------------------------------------------- #


def test_within_symbol_permutation_is_identity_for_the_concurrency_statistic() -> None:
    """🚨 이슈가 겪은 그 자리 — 종목 **안** 순열은 이 통계량을 **하나도** 안 바꾼다.

    「대조군이 고장 났다」가 아니라 **정리**다. 이 성질이 깨지면(예: 통계량이 시각 집합이
    아닌 것을 보게 되면) 이 테스트가 먼저 죽고, 그때는 함정의 기록을 다시 써야 한다.
    """
    events = _aligned_events()
    observed = concurrency_share(events, bucket_ms=DAY, threshold=5)
    rng = random.Random(0)
    for _ in range(20):
        permuted = permute_within_symbol(events, rng)
        assert concurrency_share(permuted, bucket_ms=DAY, threshold=5) == observed


def test_identity_probe_reports_every_draw_as_identical() -> None:
    """검산 (i)는 「전부 같았다」를 숫자로 남긴다 — draw 수와 같아야 한다."""
    facts = [
        _fact(e.symbol, e.time - HOUR, e.time, stop=True, net_r=-1.0) for e in _aligned_events()
    ]
    assert identity_probe(facts, draws=17) == 17


def test_circular_shift_actually_breaks_the_alignment() -> None:
    """✅ **동작하는 널** — 시프트는 정렬을 실제로 깬다(섞기 전후 값이 같으면 죽는다)."""
    events = _aligned_events()
    observed = concurrency_share(events, bucket_ms=DAY, threshold=5)
    assert observed == pytest.approx(1.0)

    lo = min(e.time for e in events)
    hi = max(e.time for e in events) + 1
    rng = random.Random(408)
    shares = [
        concurrency_share(shift_events(events, rng, lo=lo, hi=hi), bucket_ms=DAY, threshold=5)
        for _ in range(30)
    ]
    assert all(s < observed for s in shares), "시프트가 정렬을 하나도 안 깼다 — 널이 항등이다"
    assert max(shares) < 0.5


def test_circular_shift_preserves_each_symbol_event_count() -> None:
    """널이 보존해야 하는 것 — 종목별 손절 **건수**(뭉침의 크기)는 그대로다."""
    events = _spread_events()
    rng = random.Random(1)
    shifted = shift_events(events, rng, lo=BASE, hi=BASE + 500 * DAY)
    before = sorted(e.symbol for e in events)
    after = sorted(e.symbol for e in shifted)
    assert before == after


def test_concurrency_rows_flag_a_degenerate_null() -> None:
    """검산 (n)의 재료 — 어긋난 세계에서도 시프트 draw가 실측과 같은 값이면 0이 아니게 찍힌다."""
    facts = [
        _fact(e.symbol, e.time - HOUR, e.time, stop=True, net_r=-1.0) for e in _aligned_events()
    ]
    rows = concurrency_rows(facts, segment=PRIMARY_SEGMENT, draws=25)
    assert rows, "버킷 × 문턱 행이 하나도 안 나왔다"
    # 🚨 실측이 0인 행은 널도 0이라 「같은 값」이 정상이다 — 퇴화를 세는 것은 실측이 있는 행뿐.
    informative = [r for r in rows if r.observed_share > 0]
    assert informative and all(r.num_identical_draws == 0 for r in informative)
    daily = [r for r in rows if r.bucket == "1d" and r.threshold == 5]
    assert daily and daily[0].observed_share > daily[0].null_mean
    assert daily[0].p_value == pytest.approx(1 / 26)


def test_concurrency_share_counts_stops_not_buckets() -> None:
    """🚨 **건수 기준**이라야 조용한 버킷이 폭락 버킷과 같은 무게를 갖지 않는다(WAN-388 함정)."""
    events = [StopEvent(f"S{i}", BASE) for i in range(5)] + [StopEvent("S0", BASE + 10 * DAY)]
    assert concurrency_share(events, bucket_ms=DAY, threshold=5) == pytest.approx(5 / 6)


# --------------------------------------------------------------------------- #
# §0-1 — 집중도 · 하루 경계
# --------------------------------------------------------------------------- #


def test_day_rows_fold_on_kst_days() -> None:
    """하루 경계는 **KST 자정**이다 — UTC로 접으면 사람이 읽는 「그날」과 어긋난다(WAN-172)."""
    # 2024-01-01 23:00 KST(= 14:00 UTC)와 2024-01-02 01:00 KST는 **다른 날**이다.
    kst_2400 = 1_704_121_200_000  # 2024-01-02 00:00 KST
    facts = [
        _fact("S0", kst_2400 - 2 * HOUR, kst_2400 - HOUR, stop=True, net_r=-1.0),
        _fact("S0", kst_2400, kst_2400 + HOUR, stop=True, net_r=-1.0),
    ]
    rows = day_rows(facts, segment="full")
    assert [r.day for r in rows] == ["2024-01-01", "2024-01-02"]


def test_concentration_refuses_a_share_when_the_total_is_positive() -> None:
    """🚨 분모가 「총손실」이라 총합이 양수면 비율은 뜻을 잃는다(WAN-115 부호 함정)."""
    facts = [
        _fact("S0", BASE, BASE + HOUR, stop=False, net_r=+1.0),
        _fact("S0", BASE + DAY, BASE + DAY + HOUR, stop=True, net_r=-0.5),
    ]
    rows = concentration_rows(day_rows(facts, segment="full"), segment="full")
    assert rows and all(r.share_of_total != r.share_of_total for r in rows)  # nan


def test_concentration_share_matches_a_hand_computed_case() -> None:
    facts = [
        _fact("S0", BASE, BASE + HOUR, stop=True, net_r=-9.0),
        _fact("S0", BASE + DAY, BASE + DAY + HOUR, stop=True, net_r=-1.0),
    ]
    rows = concentration_rows(day_rows(facts, segment="full"), segment="full")
    top1 = next(r for r in rows if r.top_n == 1)
    assert top1.share_of_total == pytest.approx(0.9)
    assert top1.num_days == 2


# --------------------------------------------------------------------------- #
# §0-3 — 선행 지표는 인과적이어야 한다
# --------------------------------------------------------------------------- #


def test_leading_states_only_look_backwards() -> None:
    """🚨 **엄격히 앞선 청산만** 센다 — 미래를 한 칸이라도 보면 §1의 스위치가 거짓이 된다."""
    facts = [
        _fact("S0", BASE, BASE + HOUR, stop=True, net_r=-1.0),
        _fact("S1", BASE + 2 * HOUR, BASE + 3 * HOUR, stop=True, net_r=-1.0),
        _fact("S2", BASE + 4 * HOUR, BASE + 5 * HOUR, stop=False, net_r=+1.5),
    ]
    states = leading_states(facts)
    assert [s.stops_today_before for s in states] == [0, 1, 2]
    assert [s.realized_net_r_today_before for s in states] == [0.0, -1.0, -2.0]


def test_leading_states_reset_on_the_kst_day_boundary() -> None:
    """(a)·(c)는 하루 단위 — §1의 스위치가 하루 단위라 그 자와 맞춘다."""
    kst_2400 = 1_704_121_200_000
    facts = [
        _fact("S0", kst_2400 - 3 * HOUR, kst_2400 - 2 * HOUR, stop=True, net_r=-1.0),
        _fact("S1", kst_2400 + HOUR, kst_2400 + 2 * HOUR, stop=True, net_r=-1.0),
    ]
    states = leading_states(facts)
    assert states[1].stops_today_before == 0
    assert states[1].realized_net_r_today_before == 0.0


def test_open_cells_ignore_the_day_boundary_and_use_half_open_intervals() -> None:
    """(b)는 자정에 리셋되지 않고, `exit_time == t`는 **이미 닫힌 것**으로 본다(북 규약)."""
    facts = [
        _fact("S0", BASE, BASE + 10 * DAY, stop=False, net_r=+1.0),
        _fact("S1", BASE + HOUR, BASE + 2 * HOUR, stop=True, net_r=-1.0),
        _fact("S2", BASE + 2 * HOUR, BASE + 3 * HOUR, stop=True, net_r=-1.0),
    ]
    states = leading_states(facts)
    assert states[1].open_cells_before == 1  # S0만 열려 있다
    assert states[2].open_cells_before == 1  # S1은 같은 시각에 닫혔다 → 안 센다


def test_leading_rows_cover_every_trade_once_per_indicator() -> None:
    facts = [
        _fact("S0", BASE + i * HOUR, BASE + (i + 1) * HOUR, stop=True, net_r=-1.0) for i in range(8)
    ]
    rows = leading_rows(facts, segment=PRIMARY_SEGMENT)
    for indicator in ("stops_today_before", "open_cells_before", "realized_net_r_today_before"):
        block = [r for r in rows if r.indicator == indicator]
        assert sum(r.num_trades for r in block) == len(facts)


# --------------------------------------------------------------------------- #
# 상관 · 검산 · 배선
# --------------------------------------------------------------------------- #


def test_correlation_fills_no_trade_days_with_zero() -> None:
    """거래가 없는 날의 실현 손익은 실제로 0이다 — 「둘 다 거래한 날만」으로 자르면 폭락일로
    표본이 쏠려 상관이 부풀려진다."""
    facts = [
        _fact("BTC/USDT:USDT", BASE, BASE + HOUR, stop=True, net_r=-1.0),
        _fact("S1", BASE, BASE + HOUR, stop=True, net_r=-1.0),
        _fact("BTC/USDT:USDT", BASE + DAY, BASE + DAY + HOUR, stop=False, net_r=+1.0),
    ]
    rows = correlation_rows(facts, segment="full")
    pair = next(r for r in rows if r.scope == "pair_mean")
    assert pair.num_days == 2
    assert -1.0 <= pair.value <= 1.0


def test_checksums_skip_the_published_comparison_off_adopted_coordinates() -> None:
    """🚨 좁혀 돈 판을 공개 CSV와 대조하면 좌표 차이가 배선 오류처럼 보인다 — 조용히 건너뛰지
    않고 **건너뛴다고 찍는다**."""
    rows = checksums({PRIMARY_SEGMENT: []}, [], 0, adopted_coordinates=False)
    skipped = [r for r in rows if r.metric == "skipped_not_adopted_coordinates"]
    assert skipped and skipped[0].abs_diff == 0.0


def test_place_passes_the_same_arguments_as_the_adopted_book(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """검산 (a)를 **호출 인자**로 — `run_book_segments`가 `iter_book_segments`에 넘기는 것과
    같아야 한다(WAN-330 스파이 패턴). 갈라지면 「채택 북」 라벨이 거짓이 된다."""
    from backtest import book_cli
    from backtest import wan408_loss_clustering as wan408

    seen: list[dict[str, object]] = []

    def _spy(payloads, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs)
        return []

    monkeypatch.setattr(wan408, "iter_book_segments", _spy)
    monkeypatch.setattr(wan408, "apply_funding_proxy", lambda p: (p, ""))
    wan408.place([], start_ms=0, end_ms=1, segments=["full"])

    monkeypatch.setattr(book_cli, "iter_book_segments", _spy)
    monkeypatch.setattr(book_cli, "run_cells", lambda *a, **k: [])
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (p, ""))
    book_cli.run_book_segments(
        ["BTC/USDT:USDT"],
        ["1h"],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        book=LeverageBookParams(),
        segments=["full"],
        log=False,
    )
    mine, adopted = seen
    ignored = {"start_ms", "end_ms"}
    assert {k: v for k, v in mine.items() if k not in ignored} == {
        k: v for k, v in adopted.items() if k not in ignored
    }


def test_build_payloads_requests_the_adopted_candidate_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """후보 생성 인자도 채택 북과 같아야 payload 캐시의 그 판을 히트한다(그리고 같은 후보다)."""
    from backtest import book_cli
    from backtest import wan408_loss_clustering as wan408

    seen: list[dict[str, object]] = []

    def _spy(symbols, timeframes, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(kwargs)
        return []

    monkeypatch.setattr(wan408, "run_cells", _spy)
    wan408.build_payloads(["BTC/USDT:USDT"], ["1h"], start="2024-01-01", end="2024-02-01", jobs=1)

    monkeypatch.setattr(book_cli, "run_cells", _spy)
    monkeypatch.setattr(book_cli, "iter_book_segments", lambda *a, **k: [])
    monkeypatch.setattr(book_cli, "apply_funding_proxy", lambda p: (p, ""))
    book_cli.run_book_segments(
        ["BTC/USDT:USDT"],
        ["1h"],
        start="2024-01-01",
        end="2024-02-01",
        book=LeverageBookParams(),
        segments=["full"],
        log=False,
    )
    mine, adopted = seen
    # 🚨 **기본값을 적용하고** 견준다 — 한쪽이 기본값을 명시하고 다른 쪽이 생략했을 뿐인 차이는
    # 「다른 후보」가 아니다(그걸 못 가르면 이 테스트가 라벨 대조로 전락한다).
    from backtest.wan169_leverage_book import run_cells as real_run_cells

    defaults = {
        name: param.default
        for name, param in inspect.signature(real_run_cells).parameters.items()
        if param.default is not inspect.Parameter.empty
    }
    # 이 모듈만 주는 것은 컴퓨트 노브(캐시·차가운 구간·엔진 검산)뿐이다.
    compute_only = {"payload_cache", "cold_segments", "engine_check", "jobs", "start", "end"}

    def effective(kwargs: dict[str, object]) -> dict[str, object]:
        merged = {**defaults, **kwargs}
        return {k: v for k, v in merged.items() if k not in compute_only}

    assert effective(mine) == effective(adopted)


def test_buckets_are_anchored_on_kst_midnight() -> None:
    """1d 버킷이 사람이 읽는 하루와 같아야 동시성 표와 일자 표가 같은 「하루」를 본다."""
    from backtest.wan408_loss_clustering import bucket_index

    kst_2400 = 1_704_121_200_000
    day_ms = dict(BUCKETS)["1d"]
    assert bucket_index(kst_2400, day_ms) == bucket_index(kst_2400 + 23 * HOUR, day_ms)
    assert bucket_index(kst_2400 - 1, day_ms) == bucket_index(kst_2400, day_ms) - 1


def test_module_never_builds_engine_parameters() -> None:
    """이 모듈은 **관측 전용**이다 — 전략 파라미터를 **만들거나 얹는 코드**가 있으면 안 된다.

    🚨 문자열 검색이 아니라 **AST**로 본다 — 독스트링은 그 이름들을 (「안 건드렸다」고) 적어야
    하고, 막아야 하는 것은 *부르는 것*이다(라벨이 아니라 동작, WAN-91/95/112/123/159 관행).
    """
    import ast

    from backtest import wan408_loss_clustering as wan408

    tree = ast.parse(inspect.getsource(wan408))
    forbidden_calls = {"ConfluenceParams", "OrderBlockParams", "build_params"}
    forbidden_kwargs = {"max_zone_width_atr", "min_stop_distance_fraction", "take_profit_r"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )
        assert name not in forbidden_calls, f"{name}()를 관측 전용 모듈이 부른다"
        for keyword in node.keywords:
            assert keyword.arg not in forbidden_kwargs, f"{keyword.arg}=를 관측 전용 모듈이 얹는다"
