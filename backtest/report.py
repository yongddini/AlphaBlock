"""백테스트 결과 리포트 (표·CSV·요약 텍스트).

`BacktestResult`를 사람이 읽는 요약 문자열, pandas DataFrame, CSV 파일로
변환한다. 재현을 위해 요약에는 파라미터(설정)와 시드를 함께 출력한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from backtest.models import BacktestResult, ExitReason, PositionSide, TradeFill
from common import timefmt
from strategy.models import ConfluenceParams, OrderBlockParams


def _engine_labels(confluence: ConfluenceParams | None) -> tuple[str, str]:
    """리포트에 실을 진입 방식·RSI 모드 라벨(WAN-95).

    호출부가 파라미터를 주지 않으면 `"unknown"`이다 — 예전처럼 `ConfluenceParams()`
    기본값으로 채우면, 채택 기본값이 바뀐 순간(WAN-95: close → zone_limit) **A안
    엔진이 낸 결과에 "zone_limit" 라벨이 붙는다.** 리포트가 어느 엔진의 결과인지
    모르면 "모른다"고 적어야지, 그럴듯한 기본값으로 지어내면 안 된다.
    """
    if confluence is None:
        return "unknown", "unknown"
    return confluence.entry_mode, confluence.rsi_mode


def trades_to_dataframe(
    result: BacktestResult,
    *,
    confluence: ConfluenceParams | None = None,
    order_block: OrderBlockParams | None = None,
) -> pd.DataFrame:
    """거래 목록을 DataFrame으로. 부분 청산은 마지막 청산 사유로 대표한다.

    `confluence`/`order_block`을 주면 `entry_mode`/`rsi_mode`/`combine_obs`를 매 행에
    함께 싣는다(WAN-65) — 이 파일만 봐도 어떤 실행 설정으로 나온 거래인지 알 수
    있게 한다. 주지 않으면 각 파라미터의 기본값을 쓴다(호출부가 실제 값을 모르는
    저수준 호출 — 대부분의 실제 리포트 경로는 값을 명시적으로 넘긴다).
    """
    entry_mode, rsi_mode = _engine_labels(confluence)
    ob = order_block or OrderBlockParams()
    rows: list[dict[str, object]] = []
    for tr in result.trades:
        rows.append(
            {
                "side": tr.side.value,
                "entry_time": tr.entry_time,
                "entry_price": tr.entry_price,
                "quantity": tr.quantity,
                "exit_time": tr.exit_time,
                "num_exits": len(tr.exits),
                "last_exit_reason": tr.exits[-1].reason.value,
                "entry_fee": tr.entry_fee,
                "exit_fees": sum(f.fee for f in tr.exits),
                "funding_cost": tr.funding_cost,
                "realized_pnl": tr.realized_pnl,
                "return_pct": tr.return_pct,
                "entry_mode": entry_mode,
                "rsi_mode": rsi_mode,
                "combine_obs": ob.combine_obs,
                "sizing_mode": result.config.sizing_mode,
                "risk_per_trade": result.config.risk_per_trade,
                "funding_coverage": result.metrics.funding_coverage,
            }
        )
    columns = [
        "side",
        "entry_time",
        "entry_price",
        "quantity",
        "exit_time",
        "num_exits",
        "last_exit_reason",
        "entry_fee",
        "exit_fees",
        "funding_cost",
        "realized_pnl",
        "return_pct",
        "entry_mode",
        "rsi_mode",
        "combine_obs",
        "sizing_mode",
        "risk_per_trade",
        "funding_coverage",
    ]
    return pd.DataFrame(rows, columns=columns)


# --- 사람이 읽는 거래 표 (WAN-146 · WAN-106 공용) -----------------------------
#
# 화면(대시보드)과 파일(CSV 내보내기)이 **같은 함수**를 쓰게 하려고 여기에 둔다.
# 각자 구현하면 두 곳의 숫자가 갈라지고, 이 저장소는 그 부류의 사고(WAN-91/95/100/112)로
# 여러 번 결론이 뒤집혔다. `trades_to_dataframe`(엔진 원본 컬럼)은 그대로 두고, 이
# 함수가 그 위에 **표시용 파생 컬럼**(KST 시각·명목금액·시드 변화·한글 사유)을 얹는다.

#: 표시 전용 시간대. ⚠️ **저장·계산은 UTC 그대로다** — 데이터에 로컬 시각을 섞으면
#: 백테스트 재현이 깨진다. 변환은 이 표시 계층에서만 일어난다.
#: WAN-172에서 `common.timefmt`로 올렸다(cli·live·대시보드가 같은 시간대를 쓴다).
KST = timefmt.KST

#: 청산 사유 한글 라벨. 원문(`stop_loss`)은 사용자가 읽는 표에 그대로 나가면 안 된다.
EXIT_REASON_LABELS: Mapping[ExitReason, str] = {
    ExitReason.TAKE_PROFIT: "익절",
    ExitReason.PARTIAL_TAKE_PROFIT: "부분익절",
    ExitReason.STOP_LOSS: "손절",
    ExitReason.END_OF_DATA: "기간종료",
}

SIDE_LABELS: Mapping[PositionSide, str] = {
    PositionSide.LONG: "롱",
    PositionSide.SHORT: "숏",
}

COL_NO = "#"
COL_SIDE = "방향"
COL_ENTRY_KST = "진입시각(KST)"
COL_EXIT_KST = "청산시각(KST)"
COL_ENTRY_UTC = "진입시각(UTC)"
COL_EXIT_UTC = "청산시각(UTC)"
COL_HOLDING_HOURS = "보유(시간)"
COL_ENTRY_PRICE = "진입가"
COL_EXIT_PRICE = "청산가"
COL_NUM_EXITS = "청산횟수"
COL_QUANTITY = "수량"
COL_NOTIONAL = "진입금액"
COL_NOTIONAL_PCT = "시드대비%"
COL_EXIT_REASON = "청산사유"
COL_PNL = "손익"
COL_RETURN_PCT = "수익률%"
COL_EQUITY_BEFORE = "시드(전)"
COL_EQUITY_AFTER = "시드(후)"


def format_time_kst(ms: int) -> str:
    """epoch ms → `YYYY-MM-DD HH:MM`(KST). 표시 계층 전용(저장·계산은 UTC 그대로).

    구현은 `common.timefmt.format_kst` 하나뿐이다(WAN-172) — 운영 로그·텔레그램·
    `alphablock status`가 같은 함수를 쓴다. 이 이름은 대시보드·저장된 거래 조회가
    쓰던 기존 진입점이라 그대로 둔다.
    """
    return timefmt.format_kst(ms)


#: 내부 호출부용 별칭. 표시 시각 포맷은 이 모듈 한 곳에서만 정의한다 — 화면·CSV·DB
#: 조회가 각자 포맷하면 같은 거래의 시각이 화면마다 달라 보인다(WAN-146/106 공용 규칙).
_fmt_time_kst = format_time_kst


def _fmt_time_utc(ms: int) -> str:
    """데이터 열용 UTC 시각. KST 전환(WAN-172)이 이 열을 건드리지 않는다."""
    return timefmt.format_utc(ms)


def _avg_exit_price(result_trade_exits: list[TradeFill]) -> float:
    """부분 청산이 있으면 수량 가중 평균 체결가. 청산이 없으면 NaN(있을 수 없는 값)."""
    total_qty = sum(f.quantity for f in result_trade_exits)
    if total_qty <= 0:
        return float("nan")
    return sum(f.price * f.quantity for f in result_trade_exits) / total_qty


def trades_to_display_frame(result: BacktestResult, *, include_utc: bool = False) -> pd.DataFrame:
    """거래 목록을 **사람이 읽는** 표로 (WAN-146 · WAN-106 공용).

    `trades_to_dataframe`가 엔진 원본 컬럼을 그대로 내보내는 반면, 이 함수는 사용자가
    표에서 답을 찾는 세 질문("언제 샀나 · 얼마 넣었나 · 내 돈이 어떻게 변했나")에 맞춰
    컬럼을 고르고 파생값을 만든다:

    * **시각은 KST**(`Asia/Seoul`). 표시 계층 전용이고 내부는 UTC 그대로다.
      `include_utc=True`면 UTC 열을 함께 싣는다(파일 내보내기 — WAN-106).
    * **진입금액** = 진입 체결가 × 진입 수량(명목). 그 시점 시드 대비 비중도 함께.
    * **시드 변화** = `시드(전)` → `시드(후)`. 초기자본에서 시작해 거래별 순손익을
      누적한 값이라 **마지막 행의 `시드(후)`는 `metrics.final_equity`와 같다**
      (엔진의 현금 잔고가 곧 초기자본 + 실현손익 누계이기 때문 — 테스트로 고정).
    * **청산사유는 한글**(익절/부분익절/손절/기간종료).

    `청산가`는 부분 청산이 있으면 **수량 가중 평균** 체결가이고, `청산사유`는 마지막
    청산의 사유다(`청산횟수`로 부분 청산 여부를 함께 드러낸다).

    매 행에서 값이 같은 엔진 라벨(`entry_mode` 등)은 **싣지 않는다** — 표를 밀어내기
    때문이며, 대신 호출부가 표 밖(캡션·expander)에 보존한다(WAN-65의 요구는 "그 파일만
    봐도 설정을 안다"이지 "매 행에 반복한다"가 아니다).
    """
    rows: list[dict[str, object]] = []
    equity = result.metrics.initial_capital
    for no, tr in enumerate(result.trades, start=1):
        equity_before = equity
        equity_after = equity_before + tr.realized_pnl
        equity = equity_after
        notional = tr.entry_price * tr.quantity
        row: dict[str, object] = {
            COL_NO: no,
            COL_SIDE: SIDE_LABELS[tr.side],
            COL_ENTRY_KST: _fmt_time_kst(tr.entry_time),
            COL_EXIT_KST: _fmt_time_kst(tr.exit_time),
            COL_HOLDING_HOURS: (tr.exit_time - tr.entry_time) / 3_600_000,
            COL_ENTRY_PRICE: tr.entry_price,
            COL_EXIT_PRICE: _avg_exit_price(tr.exits),
            COL_NUM_EXITS: len(tr.exits),
            COL_QUANTITY: tr.quantity,
            COL_NOTIONAL: notional,
            COL_NOTIONAL_PCT: (notional / equity_before * 100.0) if equity_before else 0.0,
            COL_EXIT_REASON: EXIT_REASON_LABELS.get(tr.exits[-1].reason, tr.exits[-1].reason.value),
            COL_PNL: tr.realized_pnl,
            COL_RETURN_PCT: tr.return_pct * 100.0,
            COL_EQUITY_BEFORE: equity_before,
            COL_EQUITY_AFTER: equity_after,
        }
        if include_utc:
            row[COL_ENTRY_UTC] = _fmt_time_utc(tr.entry_time)
            row[COL_EXIT_UTC] = _fmt_time_utc(tr.exit_time)
        rows.append(row)
    return pd.DataFrame(rows, columns=list(display_columns(include_utc=include_utc)))


def display_columns(*, include_utc: bool = False) -> tuple[str, ...]:
    """`trades_to_display_frame`의 컬럼 순서. 거래가 0건이어도 표 골격이 같아야 한다."""
    columns = [
        COL_NO,
        COL_SIDE,
        COL_ENTRY_KST,
        COL_EXIT_KST,
        COL_HOLDING_HOURS,
        COL_ENTRY_PRICE,
        COL_EXIT_PRICE,
        COL_NUM_EXITS,
        COL_QUANTITY,
        COL_NOTIONAL,
        COL_NOTIONAL_PCT,
        COL_EXIT_REASON,
        COL_PNL,
        COL_RETURN_PCT,
        COL_EQUITY_BEFORE,
        COL_EQUITY_AFTER,
    ]
    if include_utc:
        columns.insert(columns.index(COL_EXIT_KST) + 1, COL_ENTRY_UTC)
        columns.insert(columns.index(COL_ENTRY_UTC) + 1, COL_EXIT_UTC)
    return tuple(columns)


def equity_to_dataframe(result: BacktestResult) -> pd.DataFrame:
    """자본곡선을 DataFrame으로 (`time`, `equity`)."""
    return pd.DataFrame(
        {
            "time": [p.time for p in result.equity_curve],
            "equity": [p.equity for p in result.equity_curve],
        }
    )


COL_TIME_KST = "시각(KST)"
COL_TIME_UTC = "시각(UTC)"
COL_EQUITY = "시드"


def equity_to_display_frame(result: BacktestResult, *, include_utc: bool = False) -> pd.DataFrame:
    """시드곡선을 **사람이 읽는** 표로 (WAN-106).

    `trades_to_display_frame`과 같은 규칙이다: 시각은 KST이고(`include_utc=True`면 UTC를
    병기 — 파일 내보내기), 저장·계산은 UTC 그대로다. 값은 엔진의 자본곡선
    (`result.equity_curve`) 그대로라 마지막 점이 `metrics.final_equity`와 같다.
    """
    columns = (
        [COL_TIME_KST, COL_EQUITY] if not include_utc else [COL_TIME_KST, COL_TIME_UTC, COL_EQUITY]
    )
    rows: list[dict[str, object]] = []
    for point in result.equity_curve:
        row: dict[str, object] = {COL_TIME_KST: _fmt_time_kst(point.time)}
        if include_utc:
            row[COL_TIME_UTC] = _fmt_time_utc(point.time)
        row[COL_EQUITY] = point.equity
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def summary_dict(
    result: BacktestResult,
    *,
    confluence: ConfluenceParams | None = None,
    order_block: OrderBlockParams | None = None,
) -> dict[str, object]:
    """지표·핵심 파라미터를 담은 평면 딕셔너리 (직렬화·로깅용).

    `confluence`/`order_block`을 주면 `entry_mode`/`rsi_mode`/`combine_obs`도 함께
    싣는다(WAN-65, `trades_to_dataframe` 참고).
    """
    entry_mode, rsi_mode = _engine_labels(confluence)
    ob = order_block or OrderBlockParams()
    m = result.metrics
    c = result.config
    return {
        "entry_mode": entry_mode,
        "rsi_mode": rsi_mode,
        "combine_obs": ob.combine_obs,
        "initial_capital": m.initial_capital,
        "final_equity": m.final_equity,
        "total_return": m.total_return,
        "max_drawdown": m.max_drawdown,
        "win_rate": m.win_rate,
        "profit_factor": m.profit_factor,
        "sharpe": m.sharpe,
        "num_trades": m.num_trades,
        "num_wins": m.num_wins,
        "num_losses": m.num_losses,
        "gross_profit": m.gross_profit,
        "gross_loss": m.gross_loss,
        "avg_win": m.avg_win,
        "avg_loss": m.avg_loss,
        "total_funding_cost": m.total_funding_cost,
        "funding_coverage": m.funding_coverage,
        "fee_rate": c.fee_rate,
        "funding_enabled": c.funding_enabled,
        "slippage": c.slippage,
        "position_fraction": c.position_fraction,
        "sizing_mode": c.sizing_mode,
        "risk_per_trade": c.risk_per_trade,
        "stop_loss_pct": c.stop_loss_pct,
        "take_profit_pct": c.take_profit_pct,
        "seed": c.seed,
    }


def _fmt(value: float | None, *, pct: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%" if pct else f"{value:,.2f}"


def format_summary(
    result: BacktestResult,
    *,
    confluence: ConfluenceParams | None = None,
    order_block: OrderBlockParams | None = None,
) -> str:
    """성과 지표를 정렬된 표 형태의 문자열로 반환."""
    entry_mode, rsi_mode = _engine_labels(confluence)
    ob = order_block or OrderBlockParams()
    m = result.metrics
    c = result.config
    lines = [
        "=== Backtest Summary ===",
        f"{'Initial Capital':<20}{_fmt(m.initial_capital):>16}",
        f"{'Final Equity':<20}{_fmt(m.final_equity):>16}",
        f"{'Total Return':<20}{_fmt(m.total_return, pct=True):>16}",
        f"{'Max Drawdown':<20}{_fmt(m.max_drawdown, pct=True):>16}",
        f"{'Win Rate':<20}{_fmt(m.win_rate, pct=True):>16}",
        f"{'Profit Factor':<20}{_fmt(m.profit_factor):>16}",
        f"{'Sharpe':<20}{_fmt(m.sharpe):>16}",
        f"{'Trades':<20}{m.num_trades:>16}",
        f"{'Wins / Losses':<20}{f'{m.num_wins} / {m.num_losses}':>16}",
        f"{'Avg Win / Loss':<20}{f'{m.avg_win:,.2f} / {m.avg_loss:,.2f}':>16}",
        f"{'Funding Cost':<20}{_fmt(m.total_funding_cost):>16}",
        f"{'Funding Coverage':<20}{_fmt_coverage(m.funding_coverage):>16}",
        "--- Params ---",
        f"entry_mode={entry_mode} rsi_mode={rsi_mode} combine_obs={ob.combine_obs}",
        f"fee_rate={c.fee_rate} slippage={c.slippage} "
        f"position_fraction={c.position_fraction} seed={c.seed}",
        f"sizing_mode={c.sizing_mode} risk_per_trade={c.risk_per_trade}",
        f"stop_loss_pct={c.stop_loss_pct} take_profit_pct={c.take_profit_pct} "
        f"partial_take_profit_pct={c.partial_take_profit_pct}",
        f"funding_enabled={c.funding_enabled} "
        f"funding_include_predicted={c.funding_include_predicted} "
        f"funding_missing_policy={c.funding_missing_policy}",
    ]
    sizing_banner = sizing_mode_banner(result)
    if sizing_banner:
        lines.append(sizing_banner)
    banner = funding_coverage_banner(result)
    if banner:
        lines.append(banner)
    return "\n".join(lines)


def sizing_mode_banner(result: BacktestResult) -> str | None:
    """`risk_sizing=None`(전액 진입 모드)이면 리포트 상단에 띄울 경고 배너, 아니면 None.

    리스크 기반 사이징이 켜져 있으면(기본) 배너가 없다. 꺼져 있으면 매 거래가
    손절 거리와 무관하게 동일 비율의 자본을 쓴다는 것을 명시한다(WAN-65 조용한
    실패 방지 — `BacktestEngine`도 같은 조건에서 로그 경고를 낸다).
    """
    if result.config.risk_sizing is not None:
        return None
    return (
        f"⚠️  risk_sizing=None (전액 진입 모드, position_fraction="
        f"{result.config.position_fraction:.0%}): 손절 거리와 무관하게 매 거래가 동일 "
        "비율의 자본을 씁니다. 손익비·MDD·R 배수가 리스크 정규화되지 않았으므로 성과 "
        "판단에 주의하세요."
    )


def _fmt_coverage(value: float | None) -> str:
    """펀딩 커버리지 표시: 미사용이면 'N/A (off)', 그 외 백분율."""
    if value is None:
        return "N/A (off)"
    return f"{value * 100:.1f}%"


def funding_coverage_banner(result: BacktestResult) -> str | None:
    """펀딩 커버리지가 1.0 미만이면 리포트 상단에 띄울 경고 배너 문자열, 아니면 None.

    커버리지가 완전(1.0)하거나 펀딩 미사용(None)이면 배너가 없다. 1.0 미만이면
    "결측 구간을 0으로 때웠다 → 비용 과소 계상"임을 명시한다(WAN-63 조용한 실패 방지).
    """
    coverage = result.metrics.funding_coverage
    if coverage is None or coverage >= 1.0:
        return None
    return (
        f"⚠️  펀딩 데이터 커버리지 {coverage:.1%} (<100%): 결측 구간의 펀딩비를 0으로 "
        "처리했습니다. 표시된 비용·순손익은 실제보다 과소 계상됐을 수 있습니다. "
        "백테스트 구간 전체의 펀딩 이력을 백필한 뒤 재산출하세요."
    )


def long_short_breakdown(result: BacktestResult) -> pd.DataFrame:
    """롱/숏 방향별 성과 분해표. 펀딩비의 방향별 비대칭을 드러낸다(WAN-63).

    무기한 선물에서 펀딩비는 롱/숏에 반대 부호로 작용하므로, 방향을 합쳐 보면
    숏이 받은(또는 낸) 펀딩비가 가려진다. 각 방향에 대해 거래 수·승률·순손익·평균
    수익률·누적 펀딩비·수수료 합계를 따로 집계한다.
    """
    rows: list[dict[str, object]] = []
    for side in (PositionSide.LONG, PositionSide.SHORT):
        side_trades = [t for t in result.trades if t.side is side]
        n = len(side_trades)
        wins = sum(1 for t in side_trades if t.is_win)
        realized = sum(t.realized_pnl for t in side_trades)
        funding = sum(t.funding_cost for t in side_trades)
        fees = sum(t.entry_fee + sum(f.fee for f in t.exits) for t in side_trades)
        avg_ret = sum(t.return_pct for t in side_trades) / n if n else 0.0
        rows.append(
            {
                "side": side.value,
                "num_trades": n,
                "num_wins": wins,
                "win_rate": wins / n if n else 0.0,
                "realized_pnl": realized,
                "avg_return_pct": avg_ret,
                "funding_cost": funding,
                "fees": fees,
            }
        )
    columns = [
        "side",
        "num_trades",
        "num_wins",
        "win_rate",
        "realized_pnl",
        "avg_return_pct",
        "funding_cost",
        "fees",
    ]
    return pd.DataFrame(rows, columns=columns)


def format_long_short(result: BacktestResult) -> str:
    """`long_short_breakdown`을 사람이 읽는 표 문자열로 반환한다."""
    df = long_short_breakdown(result)
    lines = ["=== Long / Short Breakdown ==="]
    header = (
        f"{'side':>5} {'trades':>7} {'wins':>5} {'win%':>7} "
        f"{'realized':>12} {'avg_ret':>9} {'funding':>12} {'fees':>12}"
    )
    lines.append(header)
    for _, r in df.iterrows():
        lines.append(
            f"{str(r['side']):>5} {int(r['num_trades']):>7} {int(r['num_wins']):>5} "
            f"{float(r['win_rate']) * 100:>6.1f}% {float(r['realized_pnl']):>12,.2f} "
            f"{float(r['avg_return_pct']) * 100:>8.2f}% {float(r['funding_cost']):>12,.2f} "
            f"{float(r['fees']):>12,.2f}"
        )
    return "\n".join(lines)


def write_trades_csv(
    result: BacktestResult,
    path: str | Path,
    *,
    confluence: ConfluenceParams | None = None,
    order_block: OrderBlockParams | None = None,
) -> Path:
    """거래 목록을 CSV로 저장하고 경로를 반환."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    trades_to_dataframe(result, confluence=confluence, order_block=order_block).to_csv(
        out, index=False
    )
    return out


def write_equity_csv(result: BacktestResult, path: str | Path) -> Path:
    """자본곡선을 CSV로 저장하고 경로를 반환."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    equity_to_dataframe(result).to_csv(out, index=False)
    return out
