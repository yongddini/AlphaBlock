"""scripts.backtest_report 재현 증적(run_config.json) 단위 테스트 (WAN-65).

CLI 전체를 구동하지 않고, 리포트 디렉터리마다 남기는 실행 설정 JSON 작성 함수만
검증한다(entry_mode/rsi_mode/combine_obs 등 CSV에 담기 부담스러운 부가 필드의
"escape hatch").
"""

from __future__ import annotations

import json
from pathlib import Path

from backtest.models import BacktestConfig
from execution import PositionSizingParams
from scripts.backtest_report import _git_commit_hash, _write_run_config_json
from strategy.models import ConfluenceParams, OrderBlockParams


def test_write_run_config_json_captures_full_settings(tmp_path: Path) -> None:
    conf = ConfluenceParams(entry_mode="zone_limit", rsi_mode="realtime")
    ob = OrderBlockParams(combine_obs=False, swing_length=7, zone_count="high")
    cfg = BacktestConfig(
        risk_sizing=PositionSizingParams(risk_per_trade=0.02),
        fee_rate=0.0004,
        slippage=0.0005,
        funding_enabled=True,
    )

    path = _write_run_config_json(
        tmp_path / "run_config.json", confluence=conf, order_block_params=ob, backtest_config=cfg
    )

    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entry_mode"] == "zone_limit"
    assert payload["rsi_mode"] == "realtime"
    assert payload["combine_obs"] is False
    assert payload["retap_rule"] == "first_tap"
    assert payload["swing_length"] == 7
    assert payload["zone_count"] == "high"
    assert payload["sizing_mode"] == "risk_sizing"
    assert payload["risk_per_trade"] == 0.02
    assert payload["funding_enabled"] is True
    # 재현 증적: git 저장소 안에서 돌면 커밋 해시가, 아니면 None이 들어간다(예외 없음).
    assert "commit" in payload


def test_git_commit_hash_returns_string_or_none() -> None:
    result = _git_commit_hash()
    assert result is None or isinstance(result, str)
