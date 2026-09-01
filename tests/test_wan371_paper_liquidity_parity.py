"""페이퍼 장부의 유동성 구분이 백테스트와 같은 값인지 (WAN-371).

WAN-371 이전에는 러너가 `PaperTradeRecorder`에 `entry_liquidity`를 넘기지 않아 기본값
(테이커)으로 갔다 — **백테스트는 메이커 2bp·슬리피지 0인 지정가 진입을 페이퍼는 테이커
4bp＋슬리피지 5bp로 계산**했고, WAN-370 이후에는 익절까지 갈렸다(백테 메이커 vs 페이퍼
테이커). WAN-392가 지갑 정산을 장부 행(`net_pct`)에 묶은 뒤라 이 오차는 표시 층이 아니라
**자본 곡선·MDD·다음 베팅 크기**까지 움직인다.

이 파일은 그 정렬을 **라벨이 아니라 동작으로** 건다:

1. 같은 진입가·청산가를 백테스트 비용 경로(`_to_trade`)와 페이퍼 장부에 각각 먹여
   **순손익률이 같은 값**인지 — 익절·손절 양쪽에서.
2. 채택 진입 유동성이 백테스트 B안 후보의 기본값과 **같은 객체**인지.
3. 청산 유동성이 **사유별로 실제로 갈리는지**(익절 수수료 < 손절 수수료).
4. 프로덕션 호출부가 유동성을 **명시적으로** 넘기는지(AST 배선 가드).

⚠️ 여기서 백테스트 쪽 비용은 하나도 건드리지 않는다 — 페이퍼를 백테에 맞출 뿐이다.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.zone_limit_backtest import _Candidate, _to_trade
from common.costs import CostModel, Liquidity
from config.settings import Settings
from live.paper import ClosedTrade, PaperPosition
from live.zone_limit_runner import build_paper_recorder
from paper.store import (
    ADOPTED_ENTRY_LIQUIDITY,
    LEGACY_ENTRY_LIQUIDITY,
    PaperTradeRecorder,
    PaperTradeStore,
    adopted_exit_liquidity,
    legacy_exit_liquidity,
)
from strategy.models import OrderBlockDirection, SignalExitReason

_ENTRY = 100.0
_STOP = 98.0
_ENTRY_TIME = 1_000
_EXIT_TIME = 2_000

#: 같은 셋업을 두 회계에 먹인다 — 익절은 위로, 손절은 아래로 끝난 거래.
_EXITS: dict[SignalExitReason, float] = {
    SignalExitReason.TAKE_PROFIT: 103.0,
    SignalExitReason.STOP_LOSS: 98.0,
}

_SIGNAL_TO_ENGINE: dict[SignalExitReason, ExitReason] = {
    SignalExitReason.TAKE_PROFIT: ExitReason.TAKE_PROFIT,
    SignalExitReason.STOP_LOSS: ExitReason.STOP_LOSS,
}


def _closed(reason: SignalExitReason) -> ClosedTrade:
    return ClosedTrade(
        position=PaperPosition(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            direction=OrderBlockDirection.BULLISH,
            entry_time=_ENTRY_TIME,
            entry_price=_ENTRY,
            stop_price=_STOP,
        ),
        exit_time=_EXIT_TIME,
        exit_price=_EXITS[reason],
        reason=reason,
    )


def _backtest_return_pct(reason: SignalExitReason, cfg: BacktestConfig) -> float:
    """같은 셋업을 **백테스트 비용 경로 그대로** 통과시킨 순수익률(%).

    페이퍼 장부의 분수는 진입 **원(raw)** 노셔널 대비이고 채택 진입은 메이커라 슬리피지가
    0이므로 `Trade.return_pct`(= 실현손익 ÷ 진입 체결 노셔널)와 같은 분모다.
    """
    cand = _Candidate(
        side=PositionSide.LONG,
        entry_time=_ENTRY_TIME,
        entry_price=_ENTRY,
        exit_time=_EXIT_TIME,
        exit_price=_EXITS[reason],
        reason=_SIGNAL_TO_ENGINE[reason],
        stop_price=_STOP,
    )
    trade = _to_trade(cand, equity=10_000.0, cfg=cfg)
    assert trade is not None
    return trade.return_pct * 100.0


@pytest.mark.parametrize("reason", list(SignalExitReason))
def test_paper_net_pct_matches_backtest_for_every_exit_reason(reason: SignalExitReason) -> None:
    """페이퍼 장부와 백테스트가 **같은 순손익률**을 낸다 — 청산 사유마다.

    두 경로가 갈리면(예: 진입을 테이커로 계산하거나 익절을 테이커로 계산하면) 여기서
    죽는다. 이것이 WAN-371의 완료 기준 2번이다.
    """
    cfg = BacktestConfig()
    recorder = PaperTradeRecorder(
        _NullStore(),
        cost_model=cfg.cost_model,
        entry_liquidity=ADOPTED_ENTRY_LIQUIDITY,
        exit_liquidity=adopted_exit_liquidity,
    )
    record = recorder.build(_closed(reason))
    assert record is not None
    assert record.net_pct == pytest.approx(_backtest_return_pct(reason, cfg), rel=1e-12)


@pytest.mark.parametrize("reason", list(SignalExitReason))
def test_legacy_taker_accounting_does_not_match_backtest(reason: SignalExitReason) -> None:
    """돌연변이 확인 — 옛 회계(전부 테이커)로 되돌리면 위 등식이 **깨진다**.

    이 테스트가 없으면 위 등식이 「우연히 같은 값」일 때도 통과한다.
    """
    cfg = BacktestConfig()
    recorder = PaperTradeRecorder(
        _NullStore(),
        cost_model=cfg.cost_model,
        entry_liquidity=LEGACY_ENTRY_LIQUIDITY,
        exit_liquidity=legacy_exit_liquidity,
    )
    record = recorder.build(_closed(reason))
    assert record is not None
    backtest_pct = _backtest_return_pct(reason, cfg)
    assert record.net_pct != pytest.approx(backtest_pct, rel=1e-9)
    # 옛 회계는 **더 비싸다**(진입 슬리피지 + 테이커 요율).
    assert record.net_pct < backtest_pct


def test_adopted_entry_liquidity_is_the_backtest_candidate_default() -> None:
    """채택 진입 유동성이 백테스트 B안 후보의 기본값과 같은 값이어야 한다.

    두 곳이 서로 다른 상수를 들면 패리티 대조가 무의미해진다(WAN-37/305). 후보 기본값을
    바꾸면 이 테스트가 페이퍼 쪽도 함께 옮기라고 알려 준다.
    """
    default = {f.name: f.default for f in dataclasses.fields(_Candidate)}["entry_liquidity"]
    assert ADOPTED_ENTRY_LIQUIDITY is default is Liquidity.MAKER


@pytest.mark.parametrize("reason", list(SignalExitReason))
def test_adopted_exit_liquidity_delegates_to_the_single_source(reason: SignalExitReason) -> None:
    """청산 유동성 판정은 `BacktestConfig.exit_liquidity` 한 곳에서만 나온다(WAN-370)."""
    cfg = BacktestConfig()
    assert adopted_exit_liquidity(reason) is cfg.exit_liquidity(_SIGNAL_TO_ENGINE[reason])


def test_exit_liquidity_actually_splits_by_reason() -> None:
    """라벨이 아니라 **동작으로** — 익절 거래의 수수료가 손절 거래보다 실제로 싸다.

    고정 `Liquidity` 하나를 넘기면(= 「청산」을 한 덩어리로 취급하면) 두 값이 같아져 죽는다.
    """
    model = CostModel()
    recorder = PaperTradeRecorder(
        _NullStore(),
        cost_model=model,
        entry_liquidity=ADOPTED_ENTRY_LIQUIDITY,
        exit_liquidity=adopted_exit_liquidity,
    )
    tp = recorder.build(_closed(SignalExitReason.TAKE_PROFIT))
    sl = recorder.build(_closed(SignalExitReason.STOP_LOSS))
    assert tp is not None and sl is not None
    # 익절은 지정가 reduce-only(메이커·슬리피지 0), 손절은 시장가(테이커·슬리피지).
    assert tp.fee_pct < sl.fee_pct
    assert tp.slippage_pct == 0.0
    assert sl.slippage_pct > 0.0


def test_runner_recorder_uses_the_adopted_accounting() -> None:
    """러너가 실제로 만드는 기록기가 채택 회계로 돈다(배선 자체를 동작으로 확인).

    `build_paper_recorder`는 `run_zone_limit_runner`가 부르는 바로 그 함수다.
    """
    settings = Settings(costs=BacktestConfig().cost_model)
    recorder = build_paper_recorder(_NullStore(), settings)
    assert recorder.exit_liquidity_for(SignalExitReason.TAKE_PROFIT) is Liquidity.MAKER
    assert recorder.exit_liquidity_for(SignalExitReason.STOP_LOSS) is Liquidity.TAKER
    for reason in SignalExitReason:
        record = recorder.build(_closed(reason))
        assert record is not None
        assert record.net_pct == pytest.approx(
            _backtest_return_pct(reason, BacktestConfig()), rel=1e-12
        )


# --------------------------------------------------------------------------- #
# 배선 가드 — 기본값에 기대는 프로덕션 호출부를 AST로 막는다
# --------------------------------------------------------------------------- #

#: 프로덕션 코드(테스트·아카이브 제외). 이 안에서 `PaperTradeRecorder`를 `cost_model`과
#: 함께 만들면 유동성 구분을 **명시**해야 한다.
_PRODUCTION_DIRS = ("live", "paper", "execution", "cli", "dashboard", "scripts")


def _recorder_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PaperTradeRecorder"
    ]


def test_production_callers_pass_liquidity_explicitly() -> None:
    """`PaperTradeRecorder(cost_model=...)`를 만들면서 유동성을 안 넘기면 실패한다.

    🚨 이것이 WAN-371 버그의 정확한 모양이다 — 러너가 `entry_liquidity`를 안 넘겨 기본값
    (테이커)으로 갔고, 그 사실이 **아무 데도 안 보였다**. 기본값을 고치는 대신 호출부가
    명시하게 하고(이슈 작업범위 2번) 그 명시를 이 가드가 강제한다.
    """
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    checked = 0
    for directory in _PRODUCTION_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _recorder_calls(tree):
                checked += 1
                keywords = {kw.arg for kw in call.keywords}
                if "cost_model" not in keywords:
                    continue  # 레거시 fee_rate 경로는 유동성을 읽지도 않는다.
                missing = {"entry_liquidity", "exit_liquidity"} - keywords
                if missing:
                    offenders.append(
                        f"{path.relative_to(root)}:{call.lineno} — {sorted(missing)} 미지정"
                    )
    assert checked > 0, "프로덕션 호출부를 하나도 못 찾았다 — 가드가 헛돌고 있다."
    assert not offenders, (
        "페이퍼 장부 기록기를 만들면서 유동성 구분을 명시하지 않았습니다(WAN-371).\n"
        "채택 회계는 entry_liquidity=ADOPTED_ENTRY_LIQUIDITY · "
        "exit_liquidity=adopted_exit_liquidity 입니다.\n" + "\n".join(offenders)
    )


class _NullStore(PaperTradeStore):
    """`build`만 쓰는 테스트용 — DB를 열지 않는다(영속화는 이 파일의 관심사가 아니다)."""

    def __init__(self) -> None:  # noqa: D107 — 부모 생성자를 일부러 부르지 않는다.
        pass
