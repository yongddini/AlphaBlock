"""당일 백테 타임라인 캐시 테스트 (WAN-239).

- 지문: 리비전·파라미터가 바뀌면 `run_id`가 갈라지고, 그대로면 안정적이다(엔진 판별).
- 설명형 엔진 이름(Ⅰ): 손으로 짓지 않고 **실제 파라미터에서** 나온다(채택 노브 반영).
- 저장소 왕복: 저장한 행이 그대로 복원되고, 거래 0건 셀도 "계산했음"으로 남는다(미스와 구분).
- 중복 적재는 기본 거부(`replace=True`가 명시적 덮어쓰기).
- 조회는 지금 지문과 일치하는 셀만 꺼내고(리비전 바뀌면 미스), 미스는 폴백하지 않는다.
- `persist_day` → `load_cached_day` 왕복(무거운 백테는 스텁으로 대체).
- 렌더러가 엔진 배지·캐시 상태 노트를 표에 얹는다(완료 기준 4-c).
- CLI 라우팅: `--persist-cache`/`--recompute` 플래그.

WAN-297이 더한 것:

- 캐시에 담기는 것은 **셋업 전부**(청산·미진입·미체결·건너뜀)다 — 좁은 판(청산만)을 담고
  넓게 읽으면 「계산은 됐는데 미체결 행이 없는」 조용한 실패가 된다.
- 「채택 좌표 전부」 경로가 **디스크 캐시**를 읽는다(세션이 끊겨도 재계산 없이 뜬다).
- 화면 버튼(`compute_and_persist_day`)과 야간 크론(`persist_day`)이 **같은 함수**를 타
  산출물이 갈라지지 않는다 — 화면이 그리는 행이 곧 디스크에 담긴 행이다.
- 정리(pruning)는 기준 없이는 거부하고, 세기와 삭제가 갈라져 있다(`--prune-apply`).

WAN-325가 더한 것:

- 엔진이 바뀌어 미스일 때 **보관 중인 옛 엔진 판**을 라벨 달아 내준다(`allow_stale=True`) —
  기본값은 예전 그대로 안 내준다.
- 지금 엔진 셀이 있으면 **언제나 그쪽이 이긴다**(옛 것이 새 것을 못 가린다).
- 옛 판을 내줄 때 **배지(`label`)가 그 판의 것으로 바뀐다** — 배지가 지금 엔진을 가리키면서
  행은 옛 엔진인 상태가 이 저장소가 금지하는 「조용히 내주기」다.
- 옛 리비전이 여럿이어도 표에 오르는 것은 **한 판**이고, 캐시 버전이 다른 셀은 행의 의미가
  달라 후보에서 아예 뺀다.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from live.timeline_cache import (
    CachedCell,
    CachedEngine,
    DuplicateTimelineCacheError,
    TimelineCacheStore,
    adopted_universe,
    cell_fingerprint,
    compute_and_persist_day,
    current_engine_label,
    describe_engine,
    load_cached_day,
    load_full_universe_day,
    persist_day,
)
from live.trade_timeline import SOURCE_BACKTEST, TimelineRow

_SYMBOL = "BTCUSDT"
_TF = "1h"
_DAY = "2026-08-02"
_REV = "abc1234"


def _bt_row(*, fill_ms: int, symbol: str = _SYMBOL, timeframe: str = _TF) -> TimelineRow:
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=symbol,
        timeframe=timeframe,
        is_long=True,
        status="청산",
        reserve_ms=None,
        limit_price=None,
        fill_ms=fill_ms,
        fill_price=101.0,
        stop_price=None,
        take_profit_price=None,
        exit_ms=fill_ms + 3_600_000,
        exit_price=116.0,
        exit_reason="take_profit",
        pnl_pct=15.0,
        pnl_amount=150.0,
        zone_start_time=fill_ms - 7_200_000,
        zone_confirmed_time=fill_ms - 3_600_000,
    )


# --------------------------------------------------------------------------- #
# 지문 · 설명형 이름
# --------------------------------------------------------------------------- #


def test_fingerprint_run_id_changes_with_revision() -> None:
    """리비전이 다르면 `run_id`가 갈라진다 — 엔진이 바뀌면 옛 셀을 안 꺼낸다."""
    a = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="rev-a")
    b = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="rev-b")
    assert a.run_id != b.run_id
    # 같은 입력이면 안정적(결정적 해시).
    again = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="rev-a")
    assert a.run_id == again.run_id


def test_fingerprint_run_id_changes_with_warmup_and_day() -> None:
    base = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    assert base.run_id != cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=90, revision=_REV).run_id
    assert (
        base.run_id
        != cell_fingerprint(_SYMBOL, _TF, "2026-08-01", warmup_days=120, revision=_REV).run_id
    )


def test_engine_name_reflects_adopted_knobs() -> None:
    """(Ⅰ) 설명형 이름은 실제 파라미터에서 나온다 — 채택 채택 노브가 그대로 보인다."""
    name = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV).engine_name()
    # WAN-384가 존폭 필터를 껐다 — 이름이 파라미터에서 나오므로 토큰도 저절로 바뀐다.
    assert name == "오프셋2bp · 라이브밴드 · 게이트없음 · 필터없음 · 1.5R · 롱온리 · 단일포지션"


def test_display_label_carries_hash() -> None:
    fp = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    assert fp.display_label() == f"{fp.engine_name()} ({_REV})"


def test_current_engine_label_matches_fingerprint() -> None:
    """라벨은 셀 지문과 같은 이름을 낸다(모든 셀이 같은 파라미터를 공유)."""
    label = current_engine_label(revision=_REV)
    fp = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    assert label == f"{fp.engine_name()} ({_REV})"


def test_describe_engine_handles_filter_off() -> None:
    from backtest.harness import BASELINE_FILL, build_params

    params = build_params(fill=BASELINE_FILL, max_zone_width_atr=None)
    assert "필터없음" in describe_engine(params.model_dump_json())


def test_fingerprint_uses_adopted_params() -> None:
    """지문의 파라미터가 백테 셀이 실제로 쓰는 것과 같다(라벨↔계산 갈라짐 방지)."""
    from backtest.harness import BASELINE_FILL, build_config, build_params
    from strategy.models import OrderBlockParams

    fp = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    assert fp.confluence_json == build_params(fill=BASELINE_FILL).model_dump_json()
    assert fp.order_block_json == OrderBlockParams().model_dump_json()
    assert fp.config_json == build_config(_TF).model_dump_json()
    assert fp.fill == BASELINE_FILL.name


# --------------------------------------------------------------------------- #
# 저장소 왕복
# --------------------------------------------------------------------------- #


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = TimelineCacheStore(tmp_path / "cache.db")
    fp = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    rows = [_bt_row(fill_ms=1_700_000_000_000), _bt_row(fill_ms=1_700_003_600_000)]
    store.save_cell(fp, rows)

    cell = store.load_cell(fp)
    assert isinstance(cell, CachedCell)
    assert len(cell.rows) == 2
    got = cell.rows[0]
    assert (got.source, got.fill_ms, got.fill_price, got.exit_reason, got.pnl_amount) == (
        SOURCE_BACKTEST,
        1_700_000_000_000,
        101.0,
        "take_profit",
        150.0,
    )
    assert got.zone_start_time == 1_700_000_000_000 - 7_200_000
    store.close()


def test_empty_cell_is_a_hit_not_a_miss(tmp_path: Path) -> None:
    """거래 0건 셀도 저장되면 '계산했고 거래 없음'이라 조회에서 히트다(미스와 구분)."""
    store = TimelineCacheStore(tmp_path / "cache.db")
    fp = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    store.save_cell(fp, [])
    cell = store.load_cell(fp)
    assert cell is not None
    assert cell.rows == ()
    store.close()


def test_duplicate_save_rejected_and_replace(tmp_path: Path) -> None:
    store = TimelineCacheStore(tmp_path / "cache.db")
    fp = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    store.save_cell(fp, [_bt_row(fill_ms=1_700_000_000_000)])
    with pytest.raises(DuplicateTimelineCacheError):
        store.save_cell(fp, [_bt_row(fill_ms=1_700_000_000_000)])
    # replace=True는 덮어쓴다(행 수가 바뀌는 것으로 확인).
    store.save_cell(fp, [], replace=True)
    cell = store.load_cell(fp)
    assert cell is not None and cell.rows == ()
    store.close()


def test_load_miss_on_revision_change(tmp_path: Path) -> None:
    """엔진(리비전)이 바뀌면 옛 셀은 미스로 취급된다 — 조용한 폴백 없음."""
    store = TimelineCacheStore(tmp_path / "cache.db")
    old = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="old-rev")
    store.save_cell(old, [_bt_row(fill_ms=1_700_000_000_000)])
    new = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="new-rev")
    assert store.load_cell(new) is None  # 미스
    # 옛 셀은 지워지지 않고 남아 있다(엔진 간 대조·이력 보존).
    assert store.load_cell(old) is not None
    store.close()


# --------------------------------------------------------------------------- #
# 고수준: 적재 · 조회 (무거운 백테는 스텁)
# --------------------------------------------------------------------------- #


def _stub_by_cell(
    monkeypatch: pytest.MonkeyPatch, mapping: dict[tuple[str, str], list[TimelineRow]]
) -> None:
    """`backtest_setup_by_cell`을 스텁으로 갈아 무거운 백테를 피한다(WAN-297: 셋업 전부)."""

    def _fake(**_kwargs: object) -> dict[tuple[str, str], list[TimelineRow]]:
        return mapping

    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", _fake)


def test_persist_then_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = {
        (_SYMBOL, _TF): [_bt_row(fill_ms=1_700_000_000_000)],
        ("ETHUSDT", _TF): [],  # 거래 0건 — 그래도 적재된다.
    }
    _stub_by_cell(monkeypatch, mapping)
    store = TimelineCacheStore(tmp_path / "cache.db")

    report = persist_day(
        store,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=[_SYMBOL, "ETHUSDT"],
        timeframes=[_TF],
        warmup_days=120,
        revision=_REV,
    )
    assert set(report.persisted) == {(_SYMBOL, _TF), ("ETHUSDT", _TF)}
    assert report.skipped == ()
    assert report.total_rows == 1
    assert _REV in report.label

    # 조회는 두 셀 다 히트(0건 셀 포함), 미스 없음.
    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL, "ETHUSDT"],
        timeframes=[_TF],
        warmup_days=120,
        revision=_REV,
    )
    assert set(result.hits) == {(_SYMBOL, _TF), ("ETHUSDT", _TF)}
    assert result.misses == ()
    assert len(result.rows) == 1
    store.close()


def test_persist_skips_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mapping = {(_SYMBOL, _TF): [_bt_row(fill_ms=1_700_000_000_000)]}
    _stub_by_cell(monkeypatch, mapping)
    store = TimelineCacheStore(tmp_path / "cache.db")
    args = dict(
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        warmup_days=120,
        revision=_REV,
    )
    first = persist_day(store, **args)  # type: ignore[arg-type]
    assert first.persisted == ((_SYMBOL, _TF),)
    second = persist_day(store, **args)  # type: ignore[arg-type]
    assert second.persisted == ()
    assert second.skipped == ((_SYMBOL, _TF),)
    store.close()


def test_load_cached_day_reports_misses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """캐시에 없는 셀은 미스로 돌려주고 폴백하지 않는다(완료 기준 3)."""
    _stub_by_cell(monkeypatch, {(_SYMBOL, _TF): [_bt_row(fill_ms=1_700_000_000_000)]})
    store = TimelineCacheStore(tmp_path / "cache.db")
    persist_day(
        store,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        warmup_days=120,
        revision=_REV,
    )
    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL, "ETHUSDT"],  # ETH는 적재 안 됨 → 미스
        timeframes=[_TF],
        warmup_days=120,
        revision=_REV,
    )
    assert result.hits == ((_SYMBOL, _TF),)
    assert result.misses == (("ETHUSDT", _TF),)
    assert result.all_hit is False
    store.close()


# --------------------------------------------------------------------------- #
# 렌더러 · CLI
# --------------------------------------------------------------------------- #


def test_render_includes_engine_label_and_note() -> None:
    from live.trade_timeline import DayTimeline, render_day_timeline

    timeline = DayTimeline(day_key=_DAY, live=(), backtest=())
    out = render_day_timeline(
        timeline, engine_label="오프셋2bp (abc1234)", status_note="🚨 아직 계산 안 됨"
    )
    assert "백테 대조 엔진: **오프셋2bp (abc1234)**" in out
    assert "🚨 아직 계산 안 됨" in out


def test_cli_trades_persist_flags() -> None:
    from cli.main import build_parser, cmd_trades

    ns = build_parser().parse_args(["trades", "--day", _DAY, "--persist-cache"])
    assert ns.func is cmd_trades
    assert ns.persist_cache is True
    assert ns.recompute is False
    ns2 = build_parser().parse_args(["trades", "--recompute"])
    assert ns2.recompute is True
    assert ns2.persist_cache is False


# --------------------------------------------------------------------------- #
# WAN-297 §1 — 캐시는 셋업 전부를 담고, 「채택 좌표 전부」가 디스크에서 읽는다
# --------------------------------------------------------------------------- #


def _setup_row(*, status: str, symbol: str = _SYMBOL, timeframe: str = _TF) -> TimelineRow:
    """청산이 아닌 셋업 행(미체결·건너뜀 등) — 체결·손익 칸이 비어 있다."""
    return TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=symbol,
        timeframe=timeframe,
        is_long=True,
        status=status,
        reserve_ms=None,
        limit_price=None,
        fill_ms=None,
        fill_price=None,
        stop_price=None,
        take_profit_price=None,
        exit_ms=None,
        exit_price=None,
        exit_reason=None,
        pnl_pct=None,
        pnl_amount=None,
        zone_start_time=1_699_000_000_000,
        zone_confirmed_time=1_699_003_600_000,
    )


def test_persist_day_computes_setup_rows_not_only_closed_trades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """적재가 `backtest_setup_by_cell`을 탄다 — 라벨이 아니라 **호출한 함수**로 고정한다.

    청산 거래만 담으면 「채택 좌표 전부」 모드(셋업 3열 대조)가 캐시를 히트하고도 미체결·
    건너뜀 행을 잃는다. 그 조용한 실패를 막는 자리라, 좁은 쪽 함수를 부르면 테스트가 깨진다.
    """
    called: list[str] = []

    def _fake_setups(**_kwargs: object) -> dict[tuple[str, str], list[TimelineRow]]:
        called.append("setup")
        return {(_SYMBOL, _TF): []}

    def _fake_trades(**_kwargs: object) -> dict[tuple[str, str], list[TimelineRow]]:
        raise AssertionError("청산 거래만 내는 함수를 부르면 안 된다(WAN-297).")

    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", _fake_setups)
    monkeypatch.setattr("live.trade_timeline.backtest_timeline_by_cell", _fake_trades)
    store = TimelineCacheStore(":memory:")
    persist_day(
        store,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        warmup_days=120,
        revision=_REV,
    )
    store.close()
    assert called == ["setup"]


def test_non_closed_setup_rows_survive_the_round_trip(tmp_path: Path) -> None:
    """미체결·건너뜀 행이 그대로 복원된다(스키마가 셋업 행을 담는다 — 이슈 §1-4)."""
    rows = [
        _bt_row(fill_ms=1_700_000_000_000),
        _setup_row(status="미체결"),
        _setup_row(status="건너뜀(존폭)"),
    ]
    store = TimelineCacheStore(tmp_path / "cache.db")
    fingerprint = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    store.save_cell(fingerprint, rows)
    cell = store.load_cell(fingerprint)
    store.close()
    assert cell is not None
    assert [r.status for r in cell.rows] == ["청산", "미체결", "건너뜀(존폭)"]
    assert cell.rows[1].fill_ms is None and cell.rows[1].pnl_pct is None


def test_full_universe_survives_a_dropped_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완료 기준 1 — 적재된 하루는 **새 스토어 객체**(= 끊긴 세션)에서 재계산 없이 뜬다.

    적재 후 조회가 무거운 계산을 다시 부르면 스텁이 터진다(폴백 없음 · WAN-239 §3).
    """
    symbols, timeframes = adopted_universe()
    mapping: dict[tuple[str, str], list[TimelineRow]] = {
        (sym, tf): [] for sym in symbols for tf in timeframes
    }
    mapping[(symbols[0], timeframes[0])] = [
        _bt_row(fill_ms=1_700_000_000_000, symbol=symbols[0], timeframe=timeframes[0])
    ]
    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", lambda **_k: mapping)
    db = tmp_path / "cache.db"
    writer = TimelineCacheStore(db)
    persist_day(
        writer,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=list(symbols),
        timeframes=list(timeframes),
        revision=_REV,
    )
    writer.close()

    def _explode(**_kwargs: object) -> dict[tuple[str, str], list[TimelineRow]]:
        raise AssertionError("조회는 무거운 계산으로 폴백하지 않는다.")

    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", _explode)
    reader = TimelineCacheStore(db)
    result = load_full_universe_day(reader, day_key=_DAY, revision=_REV)
    reader.close()
    assert result.all_hit is True
    assert len(result.hits) == len(symbols) * len(timeframes)
    assert len(result.rows) == 1


def test_full_universe_misses_after_engine_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완료 기준 2 — 엔진(소스 지문)이 바뀌면 옛 캐시를 **내주지 않는다**(동작으로 고정)."""
    symbols, timeframes = adopted_universe()
    monkeypatch.setattr(
        "live.timeline_cache.backtest_setup_by_cell",
        lambda **_k: {(sym, tf): [] for sym in symbols for tf in timeframes},
    )
    store = TimelineCacheStore(tmp_path / "cache.db")
    persist_day(
        store,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=list(symbols),
        timeframes=list(timeframes),
        revision="old-engine",
    )
    assert load_full_universe_day(store, day_key=_DAY, revision="old-engine").all_hit is True
    after = load_full_universe_day(store, day_key=_DAY, revision="new-engine")
    store.close()
    assert after.all_hit is False
    assert len(after.misses) == len(symbols) * len(timeframes)


def test_screen_button_and_cron_share_one_persist_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """완료 기준 4 — 화면 버튼 경로가 낸 행 == 디스크에 담긴 행 == 크론이 담는 행.

    `compute_and_persist_day`(버튼)는 `persist_day`(크론)를 그대로 타고, **적재한 뒤 다시
    읽어** 돌려준다. 그래서 "화면에는 떴는데 캐시에는 없다"가 구조적으로 불가능하다.
    """
    symbols, timeframes = adopted_universe()
    mapping: dict[tuple[str, str], list[TimelineRow]] = {
        (sym, tf): [] for sym in symbols for tf in timeframes
    }
    mapping[(symbols[0], timeframes[0])] = [
        _bt_row(fill_ms=1_700_000_000_000, symbol=symbols[0], timeframe=timeframes[0]),
        _setup_row(status="미체결", symbol=symbols[0], timeframe=timeframes[0]),
    ]
    monkeypatch.setattr("live.timeline_cache.backtest_setup_by_cell", lambda **_k: mapping)
    store = TimelineCacheStore(tmp_path / "cache.db")
    report, from_button = compute_and_persist_day(
        store,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        revision=_REV,
    )
    from_disk = load_full_universe_day(store, day_key=_DAY, revision=_REV)
    store.close()
    assert len(report.persisted) == len(symbols) * len(timeframes)
    assert from_button.rows == from_disk.rows
    assert [r.status for r in from_button.rows] == ["청산", "미체결"]


def test_button_recompute_replaces_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """버튼을 두 번 눌러도 「이미 적재됨」으로 죽지 않는다(명시적 재계산 = 덮어쓰기)."""
    symbols, timeframes = adopted_universe()
    monkeypatch.setattr(
        "live.timeline_cache.backtest_setup_by_cell",
        lambda **_k: {(sym, tf): [] for sym in symbols for tf in timeframes},
    )
    store = TimelineCacheStore(tmp_path / "cache.db")
    args = dict(day_start_ms=0, day_end_ms=86_400_000, day_key=_DAY, revision=_REV)
    compute_and_persist_day(store, **args)  # type: ignore[arg-type]
    report, _ = compute_and_persist_day(store, **args)  # type: ignore[arg-type]
    store.close()
    assert report.skipped == ()
    assert len(report.persisted) == len(symbols) * len(timeframes)


# --------------------------------------------------------------------------- #
# WAN-297 §2-6 — 정리(pruning): 기준 없이는 거부 · 세기와 삭제가 갈라져 있다
# --------------------------------------------------------------------------- #


def _seed_cell(store: TimelineCacheStore, *, day: str, revision: str) -> str:
    fingerprint = cell_fingerprint(_SYMBOL, _TF, day, warmup_days=120, revision=revision)
    return store.save_cell(fingerprint, [_bt_row(fill_ms=1_700_000_000_000)])


def test_prune_without_criteria_is_refused(tmp_path: Path) -> None:
    """기준 없는 일괄 삭제는 거부한다 — "무엇을 지웠는지 모르는 DB"를 만들지 않는다(WAN-194)."""
    store = TimelineCacheStore(tmp_path / "cache.db")
    _seed_cell(store, day=_DAY, revision=_REV)
    with pytest.raises(ValueError, match="정리 기준"):
        store.stale_cells()
    store.close()


def test_prune_candidates_are_old_revisions_and_old_days(tmp_path: Path) -> None:
    store = TimelineCacheStore(tmp_path / "cache.db")
    current = _seed_cell(store, day="2026-08-10", revision="now")
    old_engine = _seed_cell(store, day="2026-08-10", revision="then")
    old_day = _seed_cell(store, day="2026-07-01", revision="now")

    by_revision = {c.run_id for c in store.stale_cells(keep_revision="now")}
    assert by_revision == {old_engine}

    by_day = {c.run_id for c in store.stale_cells(before_day="2026-08-01")}
    assert by_day == {old_day}

    union = {c.run_id for c in store.stale_cells(keep_revision="now", before_day="2026-08-01")}
    assert union == {old_engine, old_day}
    assert current not in union
    store.close()


def test_delete_cells_removes_rows_and_leaves_the_rest(tmp_path: Path) -> None:
    store = TimelineCacheStore(tmp_path / "cache.db")
    keep = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="now")
    store.save_cell(keep, [_bt_row(fill_ms=1_700_000_000_000)])
    stale = _seed_cell(store, day=_DAY, revision="then")

    assert store.delete_cells([stale]) == 1
    assert store.delete_cells([stale]) == 0  # 없는 id는 조용히 통과
    remaining = store.load_cell(keep)
    rows_left = store._conn.execute(  # noqa: SLF001 — 행까지 지워졌는지 직접 본다
        "SELECT COUNT(*) FROM timeline_cache_rows WHERE run_id = ?", (stale,)
    ).fetchone()[0]
    store.close()
    assert remaining is not None and len(remaining.rows) == 1
    assert rows_left == 0


# --------------------------------------------------------------------------- #
# WAN-297 §2 — CLI: `--days N` 되채우기 · 정리 플래그
# --------------------------------------------------------------------------- #


def test_cli_trades_backfill_and_prune_flags() -> None:
    from cli.main import build_parser, cmd_trades

    ns = build_parser().parse_args(["trades", "--day", _DAY, "--persist-cache", "--days", "3"])
    assert ns.func is cmd_trades
    assert ns.persist_cache is True and ns.days == 3
    # 기본은 하루치 — 옛 크론 줄이 그대로 돈다(동작 불변).
    assert build_parser().parse_args(["trades", "--persist-cache"]).days == 1
    prune = build_parser().parse_args(["trades", "--prune-cache", "--prune-before", "2026-08-01"])
    assert prune.prune_cache is True
    assert prune.prune_before == "2026-08-01"
    assert prune.prune_apply is False  # 세기가 기본, 삭제는 명시 옵트인


def test_cli_persist_backfills_n_days_ending_at_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--days 3`은 `--day`에서 **거슬러** 3일치를 적재한다(배포 뒤 되채우기, WAN-318 §5 자동화)."""
    from cli.main import build_parser, cmd_trades
    from config.settings import Settings

    monkeypatch.setattr(
        "live.timeline_cache.backtest_setup_by_cell", lambda **_k: {(_SYMBOL, _TF): []}
    )
    db = str(tmp_path / "cache.db")
    ns = build_parser().parse_args(
        [
            "trades",
            "--db",
            db,
            "--day",
            "2026-08-10",
            "--symbol",
            _SYMBOL,
            "--tf",
            _TF,
            "--persist-cache",
            "--days",
            "3",
        ]
    )
    assert cmd_trades(ns, Settings(db_path=db)) == 0
    out = capsys.readouterr().out
    for day in ("2026-08-08", "2026-08-09", "2026-08-10"):
        assert day in out
    store = TimelineCacheStore(db)
    try:
        days = {
            str(row[0])
            for row in store._conn.execute(  # noqa: SLF001 — 어느 날이 담겼는지 직접 본다
                "SELECT DISTINCT day_key FROM timeline_cache_cells"
            )
        }
    finally:
        store.close()
    assert days == {"2026-08-08", "2026-08-09", "2026-08-10"}


def test_cli_prune_counts_by_default_and_deletes_only_with_apply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--prune-cache`는 읽기 전용(세기)이고, 삭제는 `--prune-apply`에만 일어난다."""
    from cli.main import build_parser, cmd_trades
    from config.settings import Settings

    db = str(tmp_path / "cache.db")
    store = TimelineCacheStore(db)
    _seed_cell(store, day="2026-07-01", revision="then")
    store.close()

    args = ["trades", "--db", db, "--prune-cache", "--prune-before", "2026-08-01"]
    assert cmd_trades(build_parser().parse_args(args), Settings(db_path=db)) == 0
    assert "세기만 했습니다" in capsys.readouterr().out
    store = TimelineCacheStore(db)
    try:
        assert len(store.stale_cells(before_day="2026-08-01")) == 1  # 안 지워졌다
    finally:
        store.close()

    assert (
        cmd_trades(build_parser().parse_args([*args, "--prune-apply"]), Settings(db_path=db)) == 0
    )
    assert "삭제 1셀" in capsys.readouterr().out
    store = TimelineCacheStore(db)
    try:
        assert store.stale_cells(before_day="2026-08-01") == ()
    finally:
        store.close()


def test_cli_prune_refuses_when_all_criteria_are_disabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """리비전 기준을 빼고 날짜도 안 주면 「전부 삭제」가 되므로 종료 코드 2로 거부한다."""
    from cli.main import build_parser, cmd_trades
    from config.settings import Settings

    db = str(tmp_path / "cache.db")
    ns = build_parser().parse_args(
        ["trades", "--db", db, "--prune-cache", "--prune-keep-all-revisions", "--prune-apply"]
    )
    assert cmd_trades(ns, Settings(db_path=db)) == 2
    assert "정리 기준이 없습니다" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# WAN-325 — 옛 엔진 결과를 **라벨 달아** 보여준다(지우지 않고 보관 중인 행을 살려 쓴다)
# --------------------------------------------------------------------------- #


def _seed_engine(
    store: TimelineCacheStore,
    *,
    revision: str,
    cells: Sequence[tuple[str, str]],
    created_at: int,
    fill_ms: int = 1_700_000_000_000,
) -> None:
    """한 리비전으로 여러 칸을 적재한다(옛 엔진 판을 흉내 내는 픽스처)."""
    for symbol, timeframe in cells:
        fingerprint = cell_fingerprint(symbol, timeframe, _DAY, warmup_days=120, revision=revision)
        store.save_cell(
            fingerprint,
            [_bt_row(fill_ms=fill_ms, symbol=symbol, timeframe=timeframe)],
            created_at=created_at,
        )


def test_stale_engine_is_not_served_unless_asked(tmp_path: Path) -> None:
    """기본값은 예전 그대로 — 옵트인하지 않으면 옛 판을 **안** 내준다(WAN-239 §3 불변)."""
    store = TimelineCacheStore(tmp_path / "cache.db")
    _seed_engine(store, revision="old-engine", cells=[(_SYMBOL, _TF)], created_at=1_000)

    strict = load_cached_day(
        store, day_key=_DAY, symbols=[_SYMBOL], timeframes=[_TF], revision="new-engine"
    )
    store.close()
    assert strict.rows == ()
    assert strict.misses == ((_SYMBOL, _TF),)
    assert strict.stale is None and strict.is_stale is False


def test_stale_engine_is_served_with_a_label(tmp_path: Path) -> None:
    """완료 기준 1 — 엔진이 바뀌면 빈 화면 대신 옛 판이 뜨고 **경고가 실제로 붙는다**.

    문구가 아니라 동작을 고정한다: (a) 행이 실제로 나오고, (b) `stale`이 채워져 호출부가
    배너를 띄울 수 있고, (c) **배지(`label`)가 옛 판의 것으로 바뀐다** — 배지가 지금
    엔진을 가리키면서 행은 옛 엔진인 상태가 이 저장소가 금지하는 「조용히 내주기」다.
    """
    store = TimelineCacheStore(tmp_path / "cache.db")
    _seed_engine(store, revision="old-engine", cells=[(_SYMBOL, _TF)], created_at=1_755_000_000_000)

    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        revision="new-engine",
        allow_stale=True,
    )
    store.close()
    assert result.is_stale is True
    assert result.stale is not None
    assert result.stale.revision == "old-engine"
    assert result.hits == ((_SYMBOL, _TF),) and result.misses == ()
    assert len(result.rows) == 1
    # 배지가 옛 판을 가리킨다(지금 엔진 배지가 아니다).
    assert result.label == result.stale.display_label()
    assert "old-engine" in result.label
    assert result.label != current_engine_label(revision="new-engine")
    # 적재 시각이 배너에 실릴 수 있게 남아 있다(KST 표시 — WAN-172).
    assert "KST" in result.stale.created_label()


def test_current_engine_wins_over_stale(tmp_path: Path) -> None:
    """완료 기준 2 — 지금 엔진 캐시가 있으면 그쪽이 우선이고 경고가 뜨지 않는다."""
    store = TimelineCacheStore(tmp_path / "cache.db")
    _seed_engine(
        store,
        revision="old-engine",
        cells=[(_SYMBOL, _TF)],
        created_at=2_000,
        fill_ms=1_700_000_000_000,
    )
    _seed_engine(
        store,
        revision="new-engine",
        cells=[(_SYMBOL, _TF)],
        created_at=1_000,  # 옛 판이 **더 최근에** 적재됐어도 지금 엔진이 이긴다
        fill_ms=1_700_007_200_000,
    )

    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        revision="new-engine",
        allow_stale=True,
    )
    store.close()
    assert result.stale is None
    assert result.all_hit is True
    assert [r.fill_ms for r in result.rows] == [1_700_007_200_000]  # 새 판의 행


def test_stale_pick_is_one_engine_only(tmp_path: Path) -> None:
    """완료 기준 4 · §5 — 옛 리비전이 여럿이면 **하나만** 쓰고 섞지 않는다.

    같은 커버리지면 더 최근 판이 이기고, 그 판에 없는 칸은 다른 판에서 메우지 않고 그냥
    미스로 남는다(여러 엔진의 셀을 한 표에 섞는 것이 금지된 바로 그것).
    """
    store = TimelineCacheStore(tmp_path / "cache.db")
    _seed_engine(
        store,
        revision="engine-older",
        cells=[(_SYMBOL, _TF), ("ETHUSDT", _TF)],
        created_at=1_000,
        fill_ms=1_700_000_000_000,
    )
    _seed_engine(
        store,
        revision="engine-newer",
        cells=[(_SYMBOL, _TF)],
        created_at=9_000,
        fill_ms=1_700_014_400_000,
    )

    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL, "ETHUSDT"],
        timeframes=[_TF],
        revision="new-engine",
        allow_stale=True,
    )
    store.close()
    # 커버리지가 큰 판(2칸)이 이긴다 — 「더 최근」은 커버리지가 같을 때의 타이브레이크다.
    assert result.stale is not None and result.stale.revision == "engine-older"
    assert result.hits == ((_SYMBOL, _TF), ("ETHUSDT", _TF))
    # 한 판에서만 왔다: 다른 리비전이 만든 행(fill_ms)이 섞이지 않는다.
    assert {r.fill_ms for r in result.rows} == {1_700_000_000_000}


def test_stale_does_not_hide_a_more_complete_current_engine(tmp_path: Path) -> None:
    """옛 판이 지금 엔진보다 **덜** 덮으면 갈아타지 않는다(부분이라도 오늘 판이 낫다)."""
    store = TimelineCacheStore(tmp_path / "cache.db")
    _seed_engine(store, revision="old-engine", cells=[(_SYMBOL, _TF)], created_at=9_000)
    _seed_engine(
        store,
        revision="new-engine",
        cells=[(_SYMBOL, _TF), ("ETHUSDT", _TF)],
        created_at=1_000,
        fill_ms=1_700_014_400_000,
    )

    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL, "ETHUSDT", "SOLUSDT"],
        timeframes=[_TF],
        revision="new-engine",
        allow_stale=True,
    )
    store.close()
    assert result.stale is None
    assert result.misses == (("SOLUSDT", _TF),)
    assert {r.fill_ms for r in result.rows} == {1_700_014_400_000}


def test_stale_ignores_cells_from_an_older_cache_version(tmp_path: Path) -> None:
    """🚨 캐시 버전이 다른 셀은 후보에서 뺀다 — 「행의 의미」가 달라서다.

    `wan305.1` 셀은 **청산 거래만** 담고 지금 버전은 **셋업 전부**를 담는다. 옛 버전 셀을
    「옛 엔진 결과」라며 내주면 미체결·건너뜀 행이 통째로 빠진 표가 「계산됨」으로 떠서,
    WAN-297이 이름 붙인 「계산은 됐는데 미체결 행이 없는」 조용한 실패가 재현된다.
    """
    store = TimelineCacheStore(tmp_path / "cache.db")
    base = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision="old-engine")
    ancient = base.model_copy(update={"cache_version": "wan305.1"})
    store.save_cell(ancient, [_bt_row(fill_ms=1_700_000_000_000)], created_at=9_000)

    result = load_cached_day(
        store,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        revision="new-engine",
        allow_stale=True,
    )
    store.close()
    assert result.stale is None
    assert result.rows == ()
    assert result.misses == ((_SYMBOL, _TF),)


def test_persist_day_stamps_created_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """적재 시각이 실제로 남는다 — 「언제 계산된 판인가」가 배너에 실릴 수 있게(WAN-325 §2).

    옛 판은 호출부가 값을 안 넘겨 0으로 남아 있는데, 그때는 지어내지 않고 「적재 시각 미상」이다.
    """
    monkeypatch.setattr(
        "live.timeline_cache.backtest_setup_by_cell", lambda **_k: {(_SYMBOL, _TF): []}
    )
    store = TimelineCacheStore(tmp_path / "cache.db")
    persist_day(
        store,
        day_start_ms=0,
        day_end_ms=86_400_000,
        day_key=_DAY,
        symbols=[_SYMBOL],
        timeframes=[_TF],
        revision=_REV,
    )
    engines = store.day_engines(_DAY, warmup_days=120, fill=_baseline_fill_name())
    store.close()
    assert len(engines) == 1
    assert engines[0].created_at > 1_700_000_000_000  # 지어낸 0이 아니라 진짜 시각
    assert "KST" in engines[0].created_label()

    unknown = CachedEngine(
        revision="then", engine_version="x", engine_name="n", created_at=0, cells=()
    )
    assert unknown.created_label() == "적재 시각 미상"


def _baseline_fill_name() -> str:
    from backtest.harness import BASELINE_FILL

    return str(BASELINE_FILL.name)


def test_cli_trades_marks_stale_engine_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """터미널도 옛 판을 라벨 달아 낸다 — 배지가 옛 판이고 상태 줄이 그 사실을 밝힌다."""
    from cli.main import build_parser, cmd_trades
    from config.settings import Settings

    db = str(tmp_path / "cache.db")
    store = TimelineCacheStore(db)
    _seed_engine(
        store, revision="eng:oldoldold12", cells=[(_SYMBOL, _TF)], created_at=1_755_000_000_000
    )
    store.close()

    argv = ["trades", "--db", db, "--day", _DAY, "--symbol", _SYMBOL, "--tf", _TF]
    assert cmd_trades(build_parser().parse_args(argv), Settings(db_path=db)) == 0
    out = capsys.readouterr().out
    assert "옛 엔진 결과입니다" in out
    assert "eng:oldoldold12" in out  # 배지가 옛 판의 지문을 단다

    # `--no-stale`은 예전처럼 「아직 계산 안 됨」으로 남는다(스크립트용 엄격 조회).
    assert cmd_trades(build_parser().parse_args([*argv, "--no-stale"]), Settings(db_path=db)) == 0
    strict = capsys.readouterr().out
    assert "아직 계산 안 됨" in strict
    assert "옛 엔진 결과입니다" not in strict


# --------------------------------------------------------------------------- #
# WAN-335 — 셋업 행 왕복이 무손실이어야 한다(조인 키가 캐시에서 사라지고 있었다)
# --------------------------------------------------------------------------- #


def test_setup_row_roundtrip_is_lossless(tmp_path: Path) -> None:
    """🚨 캐시 왕복이 셋업 행의 **어떤 칸도 버리지 않는다** (WAN-335).

    옛 행 스키마는 「백테 행은 예약·목표가·손절 칸이 없다」는 전제로 다섯 열을 버렸는데,
    그건 거래 행(`cell_timeline_trades`) 시절 이야기였고 WAN-297이 담기는 것을 **셋업 행**
    으로 넓힌 뒤로는 거짓이었다. 하필 버린 것 중 `tap_index`가 **조인 키의 일부**
    (`live.setup_compare.setup_key`)라 캐시에서 읽은 행은 라이브와 **절대 안 짝지어졌고**,
    `stop_price`는 손절폭 그 자체라 파리티 비교의 대상이 통째로 없었다.

    필드를 하나씩 세지 않고 **행 전체 동등성**으로 고정한다 — 나중에 칸이 늘어나도 이
    테스트가 함께 걸려야 같은 사고가 반복되지 않는다.
    """
    row = TimelineRow(
        source=SOURCE_BACKTEST,
        symbol=_SYMBOL,
        timeframe=_TF,
        is_long=False,
        status="미체결",
        reserve_ms=1_000,
        limit_price=100.25,
        fill_ms=2_000,
        fill_price=100.5,
        stop_price=99.75,
        take_profit_price=101.5,
        exit_ms=3_000,
        exit_price=101.5,
        exit_reason="take_profit",
        pnl_pct=1.0,
        pnl_amount=10.0,
        zone_start_time=10,
        zone_confirmed_time=20,
        tap_index=2,
        is_reentry=True,
    )
    fingerprint = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    with TimelineCacheStore(tmp_path / "cache.db") as store:
        store.save_cell(fingerprint, [row])
        cell = store.load_cell(fingerprint)

    assert cell is not None
    assert cell.rows == (row,)


def test_setup_row_roundtrip_survives_a_pre_wan335_database(tmp_path: Path) -> None:
    """옛 DB(좁은 행 테이블)를 열어도 새 열이 ALTER로 붙어 INSERT가 죽지 않는다.

    서버 DB에는 이미 옛 스키마의 `timeline_cache_rows`가 있다 — 마이그레이션이 없으면 배포
    직후 야간 크론이 통째로 실패한다(옛 적재분 행은 새 열이 NULL이지만 캐시 버전이 갈라져
    어차피 로드되지 않는다).
    """
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE timeline_cache_rows (run_id TEXT NOT NULL, row_no INTEGER NOT NULL,"
            " symbol TEXT NOT NULL, timeframe TEXT NOT NULL, is_long INTEGER NOT NULL,"
            " status TEXT NOT NULL, fill_ms INTEGER, fill_price REAL, exit_ms INTEGER,"
            " exit_price REAL, exit_reason TEXT, pnl_pct REAL, pnl_amount REAL,"
            " zone_start_time INTEGER, zone_confirmed_time INTEGER,"
            " PRIMARY KEY (run_id, row_no))"
        )

    row = _bt_row(fill_ms=2_000)
    fingerprint = cell_fingerprint(_SYMBOL, _TF, _DAY, warmup_days=120, revision=_REV)
    with TimelineCacheStore(db) as store:
        store.save_cell(fingerprint, [row])
        cell = store.load_cell(fingerprint)

    assert cell is not None
    assert cell.rows == (row,)
