"""백테스트 거래 저장소 (WAN-106).

핵심은 세 가지다:

1. **실행 지문 없이는 적재도 조회도 안 된다** — 거래 행만 남은 고아 데이터를 조용히
   돌려주면 "이 거래가 어느 엔진 것인지" 알 수 없는 채로 판단하게 된다.
2. **같은 지문의 재적재는 거부** — 조용한 중복 적재도, 조용한 덮어쓰기도 없다.
3. **복원한 결과가 원본과 같은 숫자** — 요약표 · 화면(`trades_to_display_frame`) · DB
   세 출력이 갈라지지 않아야 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest import BacktestConfig
from backtest.metrics import build_metrics
from backtest.models import BacktestResult, PositionSide
from backtest.report import COL_EQUITY_AFTER, trades_to_display_frame
from backtest.substep import ZoneLimitStatus
from backtest.trade_store import (
    ENGINE_SOURCE_FILES,
    BacktestRunStore,
    DuplicateRunError,
    RunFingerprint,
    UnknownRunError,
    engine_revision,
    engine_source_revision,
    scan_engine_source_tree,
)
from backtest.zone_limit_backtest import SetupDiagnostic, ZoneLimitStats
from strategy.models import ConfluenceParams, OrderBlockParams
from tests.test_trade_display_frame import _win_then_loss

_CFG = BacktestConfig(
    initial_capital=10_000.0,
    fee_rate=0.0,
    slippage=0.0,
    position_fraction=1.0,
    risk_sizing=None,
    take_profit_pct=0.10,
    stop_loss_pct=0.05,
)


def _fingerprint(**overrides: object) -> RunFingerprint:
    base: dict[str, object] = {
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "entry_mode": "zone_limit",
        "fill": "baseline",
        "confluence_json": ConfluenceParams().model_dump_json(),
        "order_block_json": OrderBlockParams().model_dump_json(),
        "config_json": _CFG.model_dump_json(),
        "revision": "abc1234",
    }
    base.update(overrides)
    return RunFingerprint(**base)


def _setup(trigger_time: int, *, filled: bool) -> SetupDiagnostic:
    return SetupDiagnostic(
        trigger_time=trigger_time,
        tap_bar_time=trigger_time,
        tap_close=100.0,
        side=PositionSide.LONG,
        limit_price=None if not filled else 99.5,
        stop_price=95.0,
        filled=filled,
        dropped=False,
        status=ZoneLimitStatus.FILLED_EXITED if filled else ZoneLimitStatus.NO_TOUCH,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BacktestRunStore]:
    with BacktestRunStore(tmp_path / "runs.db") as opened:
        yield opened


# ------------------------------------------------------------------ 실행 지문


def test_fingerprint_rejects_empty_engine_params() -> None:
    """지문이 껍데기면 없느니만 못하다 — 빈 파라미터는 만들어지지도 않는다."""
    with pytest.raises(ValueError, match="비어 있습니다"):
        _fingerprint(confluence_json="{}")
    with pytest.raises(ValueError, match="JSON이 아닙니다"):
        _fingerprint(config_json="")


def test_fingerprint_changes_with_every_engine_axis() -> None:
    """파라미터·엔진 버전·코드 리비전이 다르면 다른 실행이다.

    특히 **코드 리비전**이 빠지면 엔진 버그를 고쳐도 키가 같아 옛 결과를 꺼내 준다 —
    이 저장소가 WAN-91/95/112에서 반복해 당한 "바꿨다고 믿으면서 안 바뀐" 사고다.
    """
    base = _fingerprint()
    assert base.run_id == _fingerprint().run_id
    assert base.run_id != _fingerprint(revision="deadbee").run_id
    assert base.run_id != _fingerprint(engine_version="wan106.99").run_id
    assert base.run_id != _fingerprint(segment="oos").run_id
    tweaked = ConfluenceParams(take_profit_r=2.0).model_dump_json()
    assert base.run_id != _fingerprint(confluence_json=tweaked).run_id


def test_engine_revision_is_a_string_even_without_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """git이 없어도 적재를 막지 않는다 — 대신 "모른다"가 지문에 남는다."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("git 없음")

    monkeypatch.setattr("backtest.trade_store.subprocess.run", _boom)
    assert engine_revision() == "unknown"


# ---------------------------------------------- 엔진 소스 지문 (WAN-253 §2)


def _write_fake_repo(root: Path) -> None:
    """엔진 목록의 모든 파일 + 대표 비-엔진 파일을 가짜 레포로 만든다.

    실 레포를 건드리지 않고 (a) 엔진 변경 → 지문 변화 · (b) 비-엔진 변경 → 지문 불변 ·
    (c) 미분류 감지를 검증하려고, `engine_source_revision(root=)`가 읽는 상대 경로 구조를
    그대로 재현한다.
    """
    for rel in ENGINE_SOURCE_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    # 대표 비-엔진 파일들(리포트·라이브 집행·아카이브)도 스캔 루트 안에 둔다.
    for rel in (
        "backtest/wan999_demo_report.py",
        "backtest/run.py",
        "backtest/archive/old.py",
        "execution/engine.py",
        "strategy/parity/chart.py",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# non-engine {rel}\n", encoding="utf-8")


def test_engine_source_revision_changes_when_engine_file_changes(tmp_path: Path) -> None:
    """(a) 엔진 파일이 바뀌면 소스 지문이 달라진다 → 캐시 키 무효화."""
    _write_fake_repo(tmp_path)
    before = engine_source_revision(root=tmp_path)
    assert before.startswith("eng:")
    (tmp_path / "strategy" / "confluence.py").write_text("# changed\n", encoding="utf-8")
    assert engine_source_revision(root=tmp_path) != before


def test_engine_source_revision_stable_when_non_engine_changes(tmp_path: Path) -> None:
    """(b) 비-엔진 파일(리포트·라이브 집행·아카이브)이 바뀌어도 지문은 불변 → 캐시 유지."""
    _write_fake_repo(tmp_path)
    before = engine_source_revision(root=tmp_path)
    for rel in (
        "backtest/wan999_demo_report.py",
        "backtest/run.py",
        "execution/engine.py",
        "strategy/parity/chart.py",
    ):
        (tmp_path / rel).write_text("# edited non-engine\n", encoding="utf-8")
    # 스캔 루트 밖(대시보드·문서)은 애초에 지문 입력이 아니다.
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / "app.py").write_text("# ui\n", encoding="utf-8")
    assert engine_source_revision(root=tmp_path) == before


def test_engine_source_revision_missing_file_is_unknown(tmp_path: Path) -> None:
    """목록의 파일을 못 읽으면(삭제·비-레포) 조용히 죽지 않고 'unknown'으로 접는다."""
    _write_fake_repo(tmp_path)
    (tmp_path / "strategy" / "confluence.py").unlink()
    assert engine_source_revision(root=tmp_path) == "unknown"


def test_scan_detects_unclassified_new_engine_file(tmp_path: Path) -> None:
    """(c) 스캔 루트에 미분류 `.py`가 생기면 잡아낸다 — 새 엔진 파일이 목록 없이 못 스며든다."""
    _write_fake_repo(tmp_path)
    assert scan_engine_source_tree(root=tmp_path) == []
    (tmp_path / "strategy" / "mystery_signal.py").write_text("# new engine?\n", encoding="utf-8")
    assert scan_engine_source_tree(root=tmp_path) == ["strategy/mystery_signal.py"]


def test_scan_real_repo_has_no_unclassified_files() -> None:
    """(c) 실 레포의 엔진 스캔 루트가 전부 분류돼 있다 — 미분류가 남으면 실패.

    새 엔진 파일을 추가했다면 `ENGINE_SOURCE_FILES`에, 새 비-엔진 파일이면 `_is_declared_
    non_engine` 규칙에 넣어야 이 테스트가 통과한다(조용한 stale 서빙 방지, WAN-106 공포).
    """
    assert scan_engine_source_tree() == []


def test_engine_source_files_exist_and_are_sorted() -> None:
    """목록이 정렬돼 있고(결정성) 실제로 존재한다 — 엔진 파일 삭제·오타를 잡는다."""
    assert list(ENGINE_SOURCE_FILES) == sorted(ENGINE_SOURCE_FILES)
    repo_root = Path(__file__).resolve().parent.parent
    missing = [rel for rel in ENGINE_SOURCE_FILES if not (repo_root / rel).is_file()]
    assert missing == []


def test_engine_source_revision_on_real_repo_is_prefixed() -> None:
    """실 레포에서 실제 지문이 뽑히고 `eng:` 접두로 옛 git 해시와 구분된다(§3)."""
    rev = engine_source_revision()
    assert rev.startswith("eng:")
    assert len(rev) == len("eng:") + 12


# ------------------------------------------------------------------ 적재·조회


def test_save_and_restore_matches_the_summary_numbers(store: BacktestRunStore) -> None:
    """요약표 · 화면 표 · DB 세 출력이 같은 숫자를 낸다(이슈 완료기준)."""
    result = _win_then_loss()
    fingerprint = _fingerprint()

    run_id = store.save_run(fingerprint, result, stats=ZoneLimitStats(eligible=4, filled=2))
    restored = store.load_result(run_id)

    assert len(restored.trades) == len(result.trades)
    assert restored.trades == result.trades  # 부분 청산까지 원본 그대로 복원된다.
    assert restored.metrics.total_return == pytest.approx(result.metrics.total_return)
    assert restored.metrics.num_trades == result.metrics.num_trades

    summary = store.summary(run_id)
    assert summary.num_trades == result.metrics.num_trades
    assert summary.fill_rate == pytest.approx(0.5)

    frame = trades_to_display_frame(restored)
    assert len(frame) == summary.num_trades
    assert frame[COL_EQUITY_AFTER].iloc[-1] == pytest.approx(summary.final_equity)


def test_equity_curve_is_persisted(store: BacktestRunStore) -> None:
    result = _win_then_loss()
    run_id = store.save_run(_fingerprint(), result)

    curve = store.equity_frame(run_id)

    assert len(curve) == len(result.equity_curve)
    assert curve["equity"].iloc[-1] == pytest.approx(result.metrics.final_equity)


def test_duplicate_fingerprint_is_refused_unless_replace(store: BacktestRunStore) -> None:
    """조용한 중복 적재도, 조용한 덮어쓰기도 만들지 않는다."""
    result = _win_then_loss()
    fingerprint = _fingerprint()
    store.save_run(fingerprint, result)

    with pytest.raises(DuplicateRunError, match="--persist-replace"):
        store.save_run(fingerprint, result)

    run_id = store.save_run(fingerprint, result, replace=True)
    assert len(store.list_runs()) == 1
    # 덮어쓰기가 자식 행까지 갈아 끼운다(옛 거래가 유령으로 남지 않는다).
    assert len(store.load_result(run_id).trades) == len(result.trades)


def test_same_symbol_different_settings_do_not_mix(store: BacktestRunStore) -> None:
    """같은 심볼·TF를 다른 설정으로 두 번 돌린 결과가 섞이면 안 된다."""
    result = _win_then_loss()
    store.save_run(_fingerprint(), result)
    other = _fingerprint(confluence_json=ConfluenceParams(take_profit_r=3.0).model_dump_json())
    store.save_run(other, result)

    runs = store.list_runs(symbol="BTC/USDT:USDT", timeframe="1h")

    assert len(runs) == 2
    assert len({r.run_id for r in runs}) == 2


def test_lookup_without_a_fingerprint_is_refused(store: BacktestRunStore) -> None:
    """지문 행이 없는 run_id는 거래가 있어도 조회되지 않는다(완료기준 — 동작으로 막기)."""
    result = _win_then_loss()
    run_id = store.save_run(_fingerprint(), result)
    # 지문만 지우고 거래 행은 남긴다 = 고아 데이터.
    with store._lock, store._conn:  # noqa: SLF001 - 고아 상태를 일부러 만든다
        store._conn.execute("DELETE FROM backtest_runs WHERE run_id = ?", (run_id,))

    for call in (store.load_result, store.setups_frame, store.equity_frame, store.summary):
        with pytest.raises(UnknownRunError, match="실행 지문이 없는"):
            call(run_id)


# ------------------------------------------------------------------ 미체결 셋업


def test_unfilled_setups_are_queryable_apart_from_trades(store: BacktestRunStore) -> None:
    """ "살 뻔했는데 못 산 자리"가 체결된 거래와 구분되게 남는다(이슈 완료기준)."""
    result = _win_then_loss()
    setups = [_setup(1_000, filled=True), _setup(2_000, filled=False), _setup(3_000, filled=False)]

    run_id = store.save_run(_fingerprint(), result, setups=setups)

    everything = store.setups_frame(run_id)
    unfilled = store.setups_frame(run_id, only_unfilled=True)

    assert len(everything) == 3
    assert list(unfilled["setup_no"]) == [2, 3]
    assert not unfilled["filled"].any()
    # 미체결은 지정가가 봉내에 움직여 단일 주문 가격이 없을 수 있다(WAN-119).
    assert unfilled["limit_price"].isna().all()
    assert set(unfilled["status"]) == {ZoneLimitStatus.NO_TOUCH.value}


def test_setups_are_empty_when_the_engine_cannot_diagnose_them(store: BacktestRunStore) -> None:
    """셋업 진단이 없는 실행은 빈 표다 — 0건과 "셀 줄 모름"을 화면이 문구로 가른다."""
    run_id = store.save_run(_fingerprint(entry_mode="close"), _win_then_loss())

    assert store.setups_frame(run_id).empty


# ------------------------------------------------------------------ 라벨


def test_label_shows_which_engine_the_trades_came_from() -> None:
    label = _fingerprint(segment="oos").label()

    assert "BTC/USDT:USDT" in label
    assert "B안(존-지정가)" in label
    assert "oos" in label
    assert "abc1234" in label


def test_created_at_is_recorded(store: BacktestRunStore) -> None:
    stamp = int(datetime(2026, 7, 20, tzinfo=UTC).timestamp() * 1000)

    run_id = store.save_run(_fingerprint(), _win_then_loss(), created_at=stamp)

    assert store.summary(run_id).created_at == stamp


def test_empty_result_persists_without_rows(store: BacktestRunStore) -> None:
    """거래 0건도 적재된다 — "안 돌렸다"와 "돌렸는데 거래가 없었다"는 다른 사실이다."""
    metrics = build_metrics(initial_capital=10_000.0, equities=[10_000.0], trades=[])
    empty = BacktestResult(config=_CFG, trades=[], equity_curve=[], metrics=metrics)

    run_id = store.save_run(_fingerprint(), empty)

    assert store.summary(run_id).num_trades == 0
    assert store.load_result(run_id).trades == []


def test_restored_result_is_not_a_recomputation(store: BacktestRunStore) -> None:
    """복원은 조회다 — 캔들 없이도 결과가 나와야 한다(대시보드가 계산을 안 하는 근거)."""
    result: BacktestResult = _win_then_loss()
    run_id = store.save_run(_fingerprint(), result)

    restored = store.load_result(run_id)

    assert [t.entry_time for t in restored.trades] == [t.entry_time for t in result.trades]
    assert [t.exits[-1].reason for t in restored.trades] == [
        t.exits[-1].reason for t in result.trades
    ]
