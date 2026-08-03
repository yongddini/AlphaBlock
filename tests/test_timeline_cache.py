"""당일 백테 타임라인 캐시 테스트 (WAN-239).

- 지문: 리비전·파라미터가 바뀌면 `run_id`가 갈라지고, 그대로면 안정적이다(엔진 판별).
- 설명형 엔진 이름(Ⅰ): 손으로 짓지 않고 **실제 파라미터에서** 나온다(채택 노브 반영).
- 저장소 왕복: 저장한 행이 그대로 복원되고, 거래 0건 셀도 "계산했음"으로 남는다(미스와 구분).
- 중복 적재는 기본 거부(`replace=True`가 명시적 덮어쓰기).
- 조회는 지금 지문과 일치하는 셀만 꺼내고(리비전 바뀌면 미스), 미스는 폴백하지 않는다.
- `persist_day` → `load_cached_day` 왕복(무거운 백테는 스텁으로 대체).
- 렌더러가 엔진 배지·캐시 상태 노트를 표에 얹는다(완료 기준 4-c).
- CLI 라우팅: `--persist-cache`/`--recompute` 플래그.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from live.timeline_cache import (
    CachedCell,
    DuplicateTimelineCacheError,
    TimelineCacheStore,
    cell_fingerprint,
    current_engine_label,
    describe_engine,
    load_cached_day,
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
    assert name == "오프셋2bp · 라이브밴드 · 게이트없음 · 필터1.28 · 1.5R · 롱온리 · 단일포지션"


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
    """`backtest_timeline_by_cell`을 스텁으로 갈아 무거운 백테를 피한다."""

    def _fake(**_kwargs: object) -> dict[tuple[str, str], list[TimelineRow]]:
        return mapping

    monkeypatch.setattr("live.timeline_cache.backtest_timeline_by_cell", _fake)


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
