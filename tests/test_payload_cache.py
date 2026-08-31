"""WAN-394 §0 — 후보 payload 디스크 캐시의 회귀 테스트.

이 파일이 지키는 것은 **라벨이 아니라 동작**이다. 캐시의 실패 모드는 이 저장소가 가장
경계하는 부류다 — 엔진이 바뀌었는데 히트하면 **「고쳤다고 믿으면서 옛 엔진 결과를 인용」**
하게 되고(WAN-364가 6년치 표를 통째로 얼린 그 부류), 캐시는 그것을 **자동화**한다. 그래서
「미스가 나야 한다」를 상수 비교가 아니라 **실제로 다시 계산했는가**로 건다.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from backtest import harness
from backtest import payload_cache as pc
from backtest.confirmation_arm import ARM_BASE, derive_arm_candidates
from backtest.models import ExitReason, PositionSide
from backtest.substep import SubStep
from backtest.trade_store import (
    ENGINE_SOURCE_FILES,
    UNKNOWN_REVISION,
    engine_source_revision,
)
from backtest.wan169_leverage_book import CellPayload, _run_tasks, _Task, arm_key
from backtest.zone_limit_backtest import _Candidate

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 픽스처 — 합성 칸(실데이터 불필요)
# --------------------------------------------------------------------------- #


def _task(**overrides: Any) -> _Task:
    base: dict[str, Any] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "4h",
        "start_ms": 0,
        "end_ms": 1_000,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
        "cold_segments": False,
        "engine_check": False,
    }
    base.update(overrides)
    return _Task(**base)


def _cand(price: float = 100.0) -> _Candidate:
    return _Candidate(
        side=PositionSide.LONG,
        entry_time=0,
        entry_price=price,
        exit_time=60_000,
        exit_price=90.0,
        reason=ExitReason.STOP_LOSS,
        stop_price=90.0,
    )


def _payload(task: _Task, *, multiples: tuple[float, ...] = (), tag: float = 100.0) -> CellPayload:
    arms: dict[str, dict[str, tuple[_Candidate, ...]]] = {
        arm_key(ARM_BASE, m): {"full": (_cand(tag + m),)}
        for m in (multiples or task.confirmation_multiples)
    }
    return CellPayload(
        symbol=task.symbol,
        timeframe=task.timeframe,
        boundary_ms=500,
        candidates={"full": (_cand(tag),)},
        funding={"full": ()},
        rows=(),
        reentry_candidates={"full": (_cand(tag + 0.5),)},
        arm_candidates=arms,
    )


@pytest.fixture
def cache(tmp_path: Path) -> pc.PayloadCache:
    return pc.PayloadCache(tmp_path / "payloads", revision="pay:test")


# --------------------------------------------------------------------------- #
# 1. 키는 `_Task` 그 자체다 — 손으로 나열하지 않는다
# --------------------------------------------------------------------------- #


def test_every_task_field_except_the_multiples_is_in_the_key() -> None:
    """🚨 **빠뜨려서 조용히 틀리는 경로를 없앤 것이 이 캐시의 핵심 설계다.**

    필드를 손으로 나열하면 새 축이 생겼을 때 그 축이 키에서 빠진 줄 아무도 모른다. 그래서
    `task_spec`은 `dataclasses.fields`를 훑고, 이 테스트가 **딱 하나의 예외**만 허용한다.
    """
    from dataclasses import fields

    names = {f.name for f in fields(_Task)}
    assert set(pc.task_spec(_task())) == names - pc._SUBSET_FIELDS
    assert set(pc._SUBSET_FIELDS) == {"confirmation_multiples"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retap_mode", "once"),
        ("combine_obs", True),
        ("bollinger", False),
        ("reentry", False),
        ("invalidation_cancel", "bar_open"),
        ("take_profit_r", 1.0),
        ("no_same_step_tp", True),
        ("max_zone_width_atr", None),
        ("short_enabled", True),
        ("seed", 7),
        ("symbol", "ETH/USDT:USDT"),
        ("timeframe", "15m"),
        ("end_ms", 2_000),
        ("cold_segments", True),
    ],
)
def test_changing_any_engine_axis_misses(cache: pc.PayloadCache, field: str, value: object) -> None:
    """엔진 축을 하나라도 바꾸면 **미스**다 — 히트하면 옛 엔진 결과를 인용하게 된다."""
    task = _task()
    cache.store(task, _payload(task))
    assert cache.load(task) is not None
    assert cache.load(replace(task, **{field: value})) is None  # type: ignore[arg-type]


def test_unset_and_explicit_none_are_different_keys(cache: pc.PayloadCache) -> None:
    """🚨 「필터 끔(`None`)」과 「채택 기본값(`UNSET`)」이 한 키를 공유하면 WAN-159가 못 박은
    구분이 캐시에서 무너진다 — `str()`로 뭉개면 실제로 그렇게 된다."""
    unset = _task(max_zone_width_atr=harness.UNSET)
    off = _task(max_zone_width_atr=None)
    assert pc.fingerprint(unset).key != pc.fingerprint(off).key


def test_placement_axes_are_not_task_fields_at_all() -> None:
    """📌 완료기준 3(「가드·재진입 배치를 바꿔도 히트한다」)은 규칙이 아니라 **타입의 성질**이다.

    손절폭 가드·`include_reentry`·복리는 `iter_book_segments`의 배치 인자라 `_Task`에 아예
    없다 — 키에 들어갈 수가 없다. 그 사실이 깨지는 날(누가 `_Task`에 넣는 날) 이 테스트가
    먼저 실패해 캐시의 존재 이유를 다시 보게 만든다.
    """
    from dataclasses import fields

    names = {f.name for f in fields(_Task)}
    assert not names & {"min_stop_distance_fraction", "include_reentry", "compound_sizing", "guard"}


# --------------------------------------------------------------------------- #
# 2. 소스 지문 — 엔진뿐 아니라 **러너**가 바뀌어도 미스여야 한다
# --------------------------------------------------------------------------- #


def _mirror_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in (*ENGINE_SOURCE_FILES, *pc.RUNNER_SOURCE_FILES):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, dst)
    return root


@pytest.mark.parametrize("rel", ["strategy/order_blocks.py", *pc.RUNNER_SOURCE_FILES])
def test_touching_engine_or_runner_sources_changes_the_revision(tmp_path: Path, rel: str) -> None:
    """🚨 러너(`wan169`의 배선 · `wan228`의 재무장 파생)는 **엔진 목록에 없다**.

    그 둘이 지문에서 빠지면 「재진입 파생을 고쳤는데 캐시가 히트하는」 구멍이 남는다 —
    엔진 파일 하나와 러너 두 파일 전부에 대해 **실제로 바이트를 바꿔** 확인한다.
    """
    root = _mirror_repo(tmp_path)
    before = pc.payload_source_revision(root)
    assert before != UNKNOWN_REVISION
    path = root / rel
    path.write_text(path.read_text() + "\n# touched\n", encoding="utf-8")
    assert pc.payload_source_revision(root) != before


def test_runner_sources_are_outside_the_engine_list() -> None:
    """이 목록이 존재하는 이유 자체 — 겹치면 `RUNNER_SOURCE_FILES`가 불필요한 중복이 된다."""
    assert not set(pc.RUNNER_SOURCE_FILES) & set(ENGINE_SOURCE_FILES)
    for rel in pc.RUNNER_SOURCE_FILES:
        assert (REPO_ROOT / rel).exists()


def test_revision_is_unknown_when_sources_are_missing(tmp_path: Path) -> None:
    """비-레포 환경에서 조용히 아무 값이나 쓰지 않는다 — 「모른다」를 남긴다."""
    assert pc.payload_source_revision(tmp_path) == UNKNOWN_REVISION
    assert engine_source_revision(tmp_path) == UNKNOWN_REVISION


# --------------------------------------------------------------------------- #
# 3. 익절 배수 — 부분집합이면 히트, 모자라면 미스, 저장은 합집합
# --------------------------------------------------------------------------- #


def test_multiples_are_deliberately_out_of_the_key() -> None:
    a = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(1.5,))
    b = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(0.6, 1.5))
    assert pc.fingerprint(a).key == pc.fingerprint(b).key


def test_subset_of_multiples_hits_and_is_trimmed_to_what_was_asked(
    cache: pc.PayloadCache,
) -> None:
    """넉넉히 담아 둔 캐시에서 **요청한 배수만** 돌려준다 — 남은 키가 붙어 나가면 그 payload는
    「같은 인자로 새로 돌린 것」과 다르고, 완료기준 1(비트 동일)이 깨진다."""
    wide = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(0.6, 0.8, 1.0, 1.5))
    cache.store(wide, _payload(wide))
    narrow = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(1.5,))
    got = cache.load(narrow)
    assert got is not None
    assert set(got.arm_candidates) == {arm_key(ARM_BASE, 1.5)}


def test_missing_a_multiple_is_a_miss_not_a_partial_fill(cache: pc.PayloadCache) -> None:
    """🚨 부분만 채워 돌려주면 격자에 **구멍이 뚫린 채** 「캐시 히트」로 보고된다."""
    narrow = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(1.5,))
    cache.store(narrow, _payload(narrow))
    wide = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(0.6, 1.5))
    assert cache.load(wide) is None


def test_store_merges_multiples_into_a_union(cache: pc.PayloadCache) -> None:
    """캐시가 **쌓인다** — 배수를 하나 더 재려고 후보 생성을 다시 하지 않아도 된다."""
    first = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(1.5,))
    cache.store(first, _payload(first))
    second = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(0.6,))
    cache.store(second, _payload(second))
    both = _task(confirmation_arms=(ARM_BASE,), confirmation_multiples=(0.6, 1.5))
    got = cache.load(both)
    assert got is not None
    assert set(got.arm_candidates) == {arm_key(ARM_BASE, 0.6), arm_key(ARM_BASE, 1.5)}


def test_one_multiple_is_the_same_whatever_else_was_asked_for() -> None:
    """🚨 **부분집합 매칭이 정당한 이유**가 이 성질이다 — 배수마다 청산이 독립이라
    「1.5R만 요청」과 「넷 중 1.5R」이 글자 그대로 같다. 깨지면 캐시가 조용히 다른 수를 낸다.
    """
    steps = [
        SubStep(time=0, high=101.0, low=99.0, close=100.0, htf_bar_time=0),
        SubStep(time=60_000, high=104.0, low=100.0, close=103.0, htf_bar_time=0),
        SubStep(time=120_000, high=130.0, low=95.0, close=128.0, htf_bar_time=0),
    ]
    times = [s.time for s in steps]
    cands = [_cand()]
    alone = derive_arm_candidates(
        cands, arm=ARM_BASE, multiples=[1.5], substeps=steps, substep_times=times
    )[1.5]
    together = derive_arm_candidates(
        cands, arm=ARM_BASE, multiples=[0.6, 0.8, 1.0, 1.5], substeps=steps, substep_times=times
    )[1.5]
    assert alone == together


# --------------------------------------------------------------------------- #
# 4. 리비전 · 스키마
# --------------------------------------------------------------------------- #


def test_a_new_revision_does_not_read_the_old_one(tmp_path: Path) -> None:
    task = _task()
    old = pc.PayloadCache(tmp_path / "p", revision="pay:old")
    old.store(task, _payload(task))
    new = pc.PayloadCache(tmp_path / "p", revision="pay:new")
    assert new.load(task) is None
    assert old.load(task) is not None  # 옛 적재분은 **지워지지 않는다**


def test_a_schema_bump_invalidates_everything(
    cache: pc.PayloadCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    """직렬화 형식이 바뀌면 스키마 버전이 무효화를 담당한다(`timeline_cache`와 같은 규약)."""
    task = _task()
    cache.store(task, _payload(task))
    monkeypatch.setattr(pc, "CACHE_SCHEMA_VERSION", "wan394.test")
    assert pc.PayloadCache(cache.directory, revision=cache.revision).load(task) is None


def test_a_corrupt_file_is_a_miss_not_a_crash(cache: pc.PayloadCache) -> None:
    """캐시는 성능 노브이지 결과 축이 아니다 — 못 읽으면 그냥 계산한다."""
    task = _task()
    cache.store(task, _payload(task))
    cache.path_for(task).write_bytes(b"not a gzip")
    assert cache.load(task) is None


# --------------------------------------------------------------------------- #
# 5. `run_cells` 배선 — 미스만 계산하고 산출은 캐시 유무와 같다
# --------------------------------------------------------------------------- #


def test_only_misses_are_computed_and_the_output_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """완료기준 1·2·4를 한 번에 — **실제 재계산 여부**로 건다(라벨이 아니라 동작).

    무거운 `run_cell`을 세는 스파이로 갈아끼운다: 첫 실행은 두 칸을 다 계산하고, 두 번째는
    **하나도** 계산하지 않아야 하며, 두 산출이 같아야 한다.
    """
    calls: list[_Task] = []

    def _spy(task: _Task, log: bool = False) -> CellPayload:
        calls.append(task)
        return _payload(task, tag=float(len(task.symbol)))

    monkeypatch.setattr("backtest.wan169_leverage_book.run_cell", _spy)
    tasks = [_task(), _task(symbol="ETH/USDT:USDT")]

    cold = pc.PayloadCache(tmp_path / "p", revision="pay:test")
    first = _run_tasks(tasks, jobs=1, cache=cold)
    assert len(calls) == 2
    assert "미스 2칸" in capsys.readouterr().out

    warm = pc.PayloadCache(tmp_path / "p", revision="pay:test")
    second = _run_tasks(tasks, jobs=1, cache=warm)
    assert len(calls) == 2  # 하나도 다시 계산하지 않았다
    assert (warm.hits, warm.misses) == (2, 0)
    assert "히트 2칸" in capsys.readouterr().out

    assert [p.candidates for p in second] == [p.candidates for p in first]
    assert [p.reentry_candidates for p in second] == [p.reentry_candidates for p in first]
    assert [p.symbol for p in second] == [p.symbol for p in first]


def test_no_cache_means_the_old_path_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """`payload_cache=None`(기본)이면 캐시 코드를 **한 줄도** 타지 않는다."""
    monkeypatch.setattr(pc.PayloadCache, "load", lambda *a, **k: pytest.fail("캐시를 타면 안 된다"))
    monkeypatch.setattr(
        "backtest.wan169_leverage_book.run_cell", lambda task, log=False: _payload(task)
    )
    assert len(_run_tasks([_task()], jobs=1, cache=None)) == 1


def test_a_partly_warm_run_computes_only_the_missing_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """섞인 경우가 실제 사용이다 — 순서가 뒤섞이면 칸 라벨이 다른 후보에 붙는다."""
    monkeypatch.setattr(
        "backtest.wan169_leverage_book.run_cell",
        lambda task, log=False: _payload(task, tag=float(len(task.symbol))),
    )
    cache = pc.PayloadCache(tmp_path / "p", revision="pay:test")
    warm_task = _task(symbol="ETH/USDT:USDT")
    cache.store(warm_task, _payload(warm_task, tag=float(len(warm_task.symbol))))

    calls: list[str] = []

    def _counting(task: _Task, log: bool = False) -> CellPayload:
        calls.append(task.symbol)
        return _payload(task, tag=float(len(task.symbol)))

    monkeypatch.setattr("backtest.wan169_leverage_book.run_cell", _counting)
    out = _run_tasks([_task(), warm_task, _task(symbol="SOL/USDT:USDT")], jobs=1, cache=cache)
    assert calls == ["BTC/USDT:USDT", "SOL/USDT:USDT"]
    assert [p.symbol for p in out] == ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]


def test_read_only_and_write_only_modes(tmp_path: Path) -> None:
    task = _task()
    writer = pc.PayloadCache(tmp_path / "p", revision="pay:test", read=False)
    assert writer.load(task) is None  # 읽기를 끄면 언제나 미스
    writer.store(task, _payload(task))
    assert pc.PayloadCache(tmp_path / "p", revision="pay:test").load(task) is not None

    no_write = pc.PayloadCache(tmp_path / "q", revision="pay:test", write=False)
    no_write.store(task, _payload(task))
    assert not (tmp_path / "q").exists()


# --------------------------------------------------------------------------- #
# 6. 정리 — 자동 삭제 없음(WAN-194/297)
# --------------------------------------------------------------------------- #


def test_prune_counts_by_default_and_deletes_only_with_apply(tmp_path: Path) -> None:
    task = _task()
    pc.PayloadCache(tmp_path / "p", revision="pay:old").store(task, _payload(task))
    pc.PayloadCache(tmp_path / "p", revision="pay:new").store(task, _payload(task))

    stale = pc.prune_stale(tmp_path / "p", revision="pay:new")
    assert [s.revision for s in stale] == ["pay:old"]
    assert (tmp_path / "p" / "pay:old").exists()  # 세기만 했다

    pc.prune_stale(tmp_path / "p", revision="pay:new", apply=True)
    assert not (tmp_path / "p" / "pay:old").exists()
    assert (tmp_path / "p" / "pay:new").exists()  # 현재 리비전은 건드리지 않는다


def test_cli_refuses_to_delete_without_a_criterion(tmp_path: Path) -> None:
    """기준 없는 삭제는 거부한다 — 무엇을 지웠는지 모르는 상태를 스스로 만들지 않는다."""
    with pytest.raises(SystemExit):
        pc.main(["--dir", str(tmp_path), "--apply"])
    with pytest.raises(SystemExit):
        pc.main(["--dir", str(tmp_path)])
    assert pc.main(["--dir", str(tmp_path), "--stats"]) == 0


def test_stats_report_the_current_revision(tmp_path: Path) -> None:
    task = _task()
    pc.PayloadCache(tmp_path / "p", revision="pay:here").store(task, _payload(task))
    (stat,) = pc.revision_stats(tmp_path / "p", revision="pay:here")
    assert (stat.revision, stat.files, stat.current) == ("pay:here", 1, True)
    assert stat.bytes > 0
