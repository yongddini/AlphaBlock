"""거래별 내역 출력 — `--trades` / `--equity` / `--persist` (WAN-106).

CLI가 요약 1행만 내던 구조에 거래 단위 출력을 얹었다. 검증의 핵심은 셋:

1. **격자 방어** — 파일 출력은 단일 조합 전용이다(조용히 마지막 조합만 내보내지 않는다).
2. **요약과 일치** — CSV 행 수·최종 시드가 요약표의 `num_trades`·`total_return`과 맞는다.
3. **적재는 조용하지 않다** — 실행 지문이 붙고, 같은 지문 재적재는 기본이 거부다.

합성 데이터를 로더 자리에 끼워 넣어 실 DB 없이 CLI 전체 경로를 돈다
(`tests/test_run_cli.py`와 같은 방식). ⚠️ 합성 데이터에서 **지정가(B안)는 거의 체결되지
않는다**(`backtest/synthetic.py` 독스트링이 경고하는 성질) — 그래서 거래가 있어야 하는
검증은 종가 진입(A안)으로, **미체결 셋업**이 있어야 하는 검증은 지정가로 돈다. 두 경로
모두 실데이터 회귀는 `tests/test_run_regression_real_data.py`가 따로 본다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest.report import COL_ENTRY_UTC, COL_EQUITY_AFTER, COL_EXIT_REASON
from backtest.run import main
from backtest.trade_store import BacktestRunStore
from tests.test_run_cli import synthetic_loader  # noqa: F401 - pytest 픽스처 재사용

_INITIAL_CAPITAL = 10_000.0
"""`default_backtest_config`의 초기자본 — 시드 곡선을 수익률로 되돌릴 때 쓴다."""

_BASE = ["--symbol", "BTCUSDT", "--tf", "1h", "--quiet", "--format", "csv"]
_CLOSE = [*_BASE, "--entry-mode", "close"]


def _run(tmp_path: Path, argv: list[str], *extra: str) -> int:
    return main([*argv, "--out", str(tmp_path / "summary.csv"), *extra])


def _summary(tmp_path: Path) -> pd.DataFrame:
    return pd.read_csv(tmp_path / "summary.csv")


# ------------------------------------------------------------------ CSV 출력


def test_trades_csv_matches_the_summary_row(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    """행 수 == `num_trades`, 최종 `시드(후)`/초기자본 − 1 == `total_return`(완료기준)."""
    trades_path = tmp_path / "trades.csv"

    assert _run(tmp_path, _CLOSE, "--trades", str(trades_path)) == 0

    summary = _summary(tmp_path)
    frame = pd.read_csv(trades_path)
    assert len(frame) == int(summary.loc[0, "num_trades"]) > 0
    realized = frame[COL_EQUITY_AFTER].iloc[-1] / _INITIAL_CAPITAL - 1.0
    assert realized == pytest.approx(float(summary.loc[0, "total_return"]), abs=1e-9)


def test_trades_csv_carries_utc_and_korean_labels(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    """파일은 KST·UTC 병기이고 청산사유는 사람이 읽는 한글이다(완료기준)."""
    trades_path = tmp_path / "trades.csv"

    assert _run(tmp_path, _CLOSE, "--trades", str(trades_path)) == 0

    frame = pd.read_csv(trades_path)
    assert COL_ENTRY_UTC in frame.columns
    assert "진입시각(KST)" in frame.columns
    assert set(frame[COL_EXIT_REASON]) <= {"익절", "부분익절", "손절", "기간종료"}


def test_equity_csv_ends_at_the_final_seed(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    equity_path = tmp_path / "equity.csv"

    assert _run(tmp_path, _CLOSE, "--equity", str(equity_path)) == 0

    curve = pd.read_csv(equity_path)
    total_return = float(_summary(tmp_path).loc[0, "total_return"])
    assert curve["시드"].iloc[-1] / _INITIAL_CAPITAL - 1.0 == pytest.approx(total_return, abs=1e-9)


def test_trades_csv_refuses_a_grid(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    synthetic_loader: None,  # noqa: F811
) -> None:
    """조용히 마지막 조합만 내보내지 않는다 — 그 파일이 나중에 채택 수치로 인용된다."""
    code = _run(tmp_path, _CLOSE, "--trades", str(tmp_path / "t.csv"), "--tp-r", "1.5,2.0")

    assert code == 2
    assert "--persist" in capsys.readouterr().err
    assert not (tmp_path / "t.csv").exists()


# ------------------------------------------------------------------ DB 적재


def test_persist_writes_trades_and_seed_curve(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    db = tmp_path / "runs.db"

    assert _run(tmp_path, _CLOSE, "--persist", "--persist-db", str(db)) == 0

    summary = _summary(tmp_path)
    with BacktestRunStore(db) as store:
        runs = store.list_runs()
        assert len(runs) == 1
        run = runs[0]
        assert run.num_trades == int(summary.loc[0, "num_trades"]) > 0
        assert run.total_return == pytest.approx(float(summary.loc[0, "total_return"]))
        assert run.fingerprint.entry_mode == "close"
        result = store.load_result(run.run_id)
        curve = store.equity_frame(run.run_id)
    assert len(result.trades) == run.num_trades
    # 시드곡선은 **엔진 원본**이다(A안은 봉 단위라 거래 수보다 점이 많다). 복원된 결과의
    # 곡선은 거래 단위로 다시 만든 것이라 MDD가 다를 수 있고, 그래서 지표의 정본은
    # 적재된 요약이다(`load_result` 독스트링).
    assert len(curve) > run.num_trades
    assert curve["equity"].iloc[-1] == pytest.approx(run.final_equity)
    assert result.metrics.final_equity == pytest.approx(run.final_equity)


def test_persist_keeps_the_setups_that_never_filled(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    """ "살 뻔했는데 못 산 자리"가 남는다 — 지금 화면에 아예 없던 정보다(완료기준).

    합성 데이터의 지정가는 체결되지 않으므로 이 실행은 **전부 미체결**이다. 그래도
    적재는 된다: "안 돌렸다"와 "돌렸는데 아무것도 안 채워졌다"는 다른 사실이고,
    후자를 화면에서 볼 수 있어야 규칙을 판단할 수 있다.
    """
    db = tmp_path / "runs.db"

    # 존폭 필터를 끈다(WAN-159 기본값 1.28) — 합성 존은 1.28×ATR보다 넓어 전부 걸러지면
    # "적격이었지만 미체결"이라는 이 테스트의 대상이 사라진다. 필터는 여기 검증 대상이 아니다.
    assert (
        _run(
            tmp_path, [*_BASE, "--max-zone-width-atr", "none"], "--persist", "--persist-db", str(db)
        )
        == 0
    )

    with BacktestRunStore(db) as store:
        run = store.list_runs()[0]
        assert run.fingerprint.entry_mode == "zone_limit"
        assert run.eligible_setups and run.eligible_setups > 0
        setups = store.setups_frame(run.run_id)
        unfilled = store.setups_frame(run.run_id, only_unfilled=True)
    assert len(setups) == run.eligible_setups
    assert len(unfilled) == run.eligible_setups - (run.num_filled or 0)
    assert not unfilled["filled"].any()


def test_persist_covers_every_cell_of_a_grid(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    """파일 출력과 달리 적재는 격자에서도 된다 — 조합마다 지문이 달라 섞이지 않는다."""
    db = tmp_path / "runs.db"

    assert _run(tmp_path, _CLOSE, "--persist", "--persist-db", str(db), "--tp-r", "1.5,2.0") == 0

    with BacktestRunStore(db) as store:
        runs = store.list_runs()
    assert len(runs) == 2
    assert len({r.run_id for r in runs}) == 2


def test_rerunning_the_same_combo_is_refused_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    synthetic_loader: None,  # noqa: F811
) -> None:
    db = tmp_path / "runs.db"
    assert _run(tmp_path, _CLOSE, "--persist", "--persist-db", str(db)) == 0

    code = _run(tmp_path, _CLOSE, "--persist", "--persist-db", str(db))

    assert code == 2
    assert "--persist-replace" in capsys.readouterr().err

    assert _run(tmp_path, _CLOSE, "--persist", "--persist-db", str(db), "--persist-replace") == 0
    with BacktestRunStore(db) as store:
        assert len(store.list_runs()) == 1


def test_summary_only_runs_carry_no_artifacts(
    tmp_path: Path,
    synthetic_loader: None,  # noqa: F811
) -> None:
    """인자를 안 주면 예전 그대로다 — 무거운 산출물을 들고 다니지 않는다."""
    from backtest.run import (
        RunOptions,
        build_parser,
        grid_from_args,
        options_from_args,
        run_grid_full,
    )

    args = build_parser().parse_args(["--symbol", "BTCUSDT", "--tf", "1h"])
    assert options_from_args(args).collect_artifacts is False

    rows, artifacts = run_grid_full(grid_from_args(args), RunOptions(), log=False)

    assert rows
    assert artifacts == []
