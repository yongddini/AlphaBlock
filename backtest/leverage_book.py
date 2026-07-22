"""타임프레임·종목 가로지르는 레버리지 북 (WAN-169, 옵트인).

사용자 정의(2026-07-22): *"타임프레임이 다른 곳에서 다중 진입이 허용된다고 한 거야. 같은
프레임에서는 익절하기 전엔 한 번만 들어가야지."* 진입 단위 = **(종목, 타임프레임) 칸**이고,
칸 안에서는 청산 전 1포지션, BTC 15m·BTC 1h·ETH 1h…는 별개 칸이라 동시에 열릴 수 있으며,
동시에 열린 칸들이 **하나의 지갑(공유 자본)** 을 나눠 쓴다.

## 기존 엔진과 무엇이 다른가

* 현행 다중 포지션(`backtest.portfolio`, WAN-103)은 **한 (종목, TF) 안에서** 여러 존에
  겹쳐 진입한다 — 사용자가 "하지 말자"고 한 그것이다. 이 북은 반대로 칸 안 스택을
  금지하고(칸당 1포지션), 대신 **칸 사이**를 하나의 공통 시간축·공유 자본으로 묶는다.
* 채택 기본 경로(동시 1포지션, `_sequence_and_cost`)는 **칸 하나**를 독립 자본으로
  돌린 것과 같다 — 실제로 칸이 하나뿐인 북은 그 경로와 **비트 단위로 같은 거래**를
  낸다(`tests/test_leverage_book.py`가 고정). 여러 칸이 모이면 실현 손익이 공유 현금에
  쌓여 다음 진입의 사이징 자본이 되고, 명목 상한이 칸 전체에 걸린다.

## 레버리지 = 매 거래 사이징 N배 (사용자 확정 2026-07-22)

`leverage_multiple = N`은 **상한만 여는 노브가 아니라 매 거래의 크기를 N배로 키운다**
(사용자 원문: *"한번의 진입이 원래 1%였다면 3배일때는 3% 이런식으로 … 모든거를
레버리지대로 했을 때의 테스트"*):

* 거래당 리스크 = `risk_per_trade × N` (1% → N%).
* 거래당 명목 천장 = `leverage × N` (`fixed_notional` 모드면 `notional_fraction × N`).
* 북 전체 명목 상한 = `공유 자본 × (기본 leverage × N)` — 여유가 남으면 축소 진입
  (clamp), 없으면 스킵(`execution.sizing.position_size`의 `open_notional` 경로 그대로,
  WAN-103 결정 2와 같은 의미).

그래서 한 칸만 열려도 손절 시 손실이 1배의 N배이고, 여러 칸이 함께 손절나면 그 손실이
칸 수만큼 겹친다 — **청산(계좌 전멸)이 1배엔 없던 실제 변수로 들어온다**. 이 모듈은
WAN-103 결정 4의 최악 가정 검사(열린 포지션 전부 동시 손절)를 공유 자본 위에서 수행해
`LiquidationEvent`로 계측한다(발생 건수가 WAN-169 판정의 필수 열이다).

## 따뜻한 연속 OOS × straddle 회계 (b) — 배치 안 함 (사용자 결정 2026-07-22)

`eval_from_ms`를 주면 **탭(`trigger_time`)이 그 시각 이후인 셋업만** 신선한 초기자본으로
배치한다(WAN-166 규약 그대로). 워밍업 구간에 탭이 나 평가 경계를 넘어 사는(straddle)
포지션은 **자본·레버리지 자리를 점유하지 않는다** — WAN-169 Approved 코멘트가 확정한
**(b) 배치 안 함**이고, 정본 리포트(WAN-166/155/161) 규약과 일치한다. 되돌리기 쉬운
옵트인 회계라 숫자가 이상하면 (a) 현실 반영(점유)로 재측정할 수 있다(같은 엔진에 축 추가).

## 이 모듈이 하지 않는 것

기본값·토대·사이징 기본값은 바꾸지 않는다 — `ConfluenceParams()`·`risk_sizing`·기본
경로는 이 모듈을 import조차 하지 않는다(WAN-103 옵트인 패턴). 부분 청산·포지션 증액·
심볼 간 상관 모델·실주문 경로(`execution`, `live`)도 없다. 셋업 탐색·체결 시뮬레이션은
`build_zone_limit_candidates`(채택 엔진 그대로)가 칸마다 이미 끝낸 것을 받는다 — 이
모듈은 그 후보들을 **하나의 공통 시간축에서 배치하는 회계**만 한다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from backtest.models import BacktestConfig, Trade
from backtest.portfolio import DEFAULT_MAINTENANCE_MARGIN_RATE, LiquidationEvent
from backtest.zone_limit_backtest import _Candidate, _to_trade
from data.models import FundingRate
from execution.sizing import PositionSizingParams

logger = logging.getLogger(__name__)

#: 칸 식별자 = (종목, 타임프레임). 사용자 정의의 진입 단위다.
CellKey = tuple[str, str]


class LeverageBookParams(BaseModel):
    """레버리지 북 회계 파라미터 (WAN-169).

    이 객체를 만드는 곳에서만 북이 돈다 — 기본 경로는 이 모듈을 모른다(옵트인).
    """

    model_config = ConfigDict(frozen=True)

    leverage_multiple: float = Field(default=1.0, gt=0)
    """사이징 배수 N. **매 거래의 크기를 N배**로 키우고(리스크 1% → N%) 북 전체 명목
    상한도 N배가 된다(모듈 독스트링). 1.0이면 채택 사이징 그대로에 자본 공유만 얹는다."""
    maintenance_margin_rate: float = Field(default=DEFAULT_MAINTENANCE_MARGIN_RATE, ge=0, lt=1)
    """최악 가정 청산 검사에 쓰는 유지증거금률(명목 대비, WAN-103 결정 4 재사용)."""


@dataclass(frozen=True)
class BookCell:
    """북의 칸 하나 — (종목, TF)의 후보와 그 심볼의 펀딩비.

    `candidates`는 `build_zone_limit_candidates`가 낸 그대로다(비용 미반영 원가 셋업).
    펀딩비가 칸에 붙어 있는 이유는 심볼마다 다르기 때문이다 — 북 전체에 한 시퀀스를
    쓰면 BTC 포지션이 ETH 펀딩을 내는 조용한 오배선이 된다.
    """

    symbol: str
    timeframe: str
    candidates: Sequence[_Candidate]
    funding_rates: Sequence[FundingRate] = ()

    @property
    def key(self) -> CellKey:
        return (self.symbol, self.timeframe)


@dataclass
class BookStats:
    """북 실행 진단 (WAN-169 리포트가 소비).

    `max_concurrent_risk_ratio`가 이슈의 「통합 최대 동시 리스크」(WAN-108이 1안 12% vs
    2안 55.7%로 가른 지표를 이 북 위에서 처음 잰다)이고, `liquidations`가 「청산 발생
    건수」다(둘 다 판정 필수 열).
    """

    peak_concurrency: int = 0
    peak_concurrency_time: int | None = None
    concurrency_histogram: dict[int, int] = field(default_factory=dict)
    """동시 k칸 보유였던 **시간**(ms) 합. 첫 배치 시도부터 마지막 청산까지를 잰다."""
    max_open_notional_ratio: float = 0.0
    """`열린 명목 합 / 공유 자본`의 최댓값 — 상한(기본 leverage × N)을 실제로 얼마나 썼나."""
    max_concurrent_risk_ratio: float = 0.0
    """동시 리스크 합 / 공유 자본의 최댓값. 거래당 N%가 몇 %까지 겹쳤는가."""
    placed: int = 0
    """실제로 배치된(거래가 된) 후보 수."""
    clamped_entries: int = 0
    """명목 상한에 걸려 **축소 진입**된 건수."""
    skipped_cell_busy: int = 0
    """자기 칸에 이미 포지션이 있어 스킵된 건수 — 칸당 1포지션(사용자 정의)의 계측."""
    skipped_notional: int = 0
    """북 명목 상한 **소진**으로 스킵된 건수(여유분 ≤ 0)."""
    skipped_sizing: int = 0
    """사이징이 거부해 스킵된 건수(손절 거리 최소치 미달 등) — 상한과 무관하며 단일
    포지션 경로에서도 똑같이 일어난다(`backtest.portfolio.PortfolioStats`와 같은 분리)."""
    liquidations: list[LiquidationEvent] = field(default_factory=list)

    @property
    def liquidated(self) -> bool:
        return bool(self.liquidations)

    def time_share(self, concurrency: int) -> float:
        """동시 `concurrency`칸이던 시간 비중(0~1). 전체 시간이 0이면 0."""
        total = sum(self.concurrency_histogram.values())
        return self.concurrency_histogram.get(concurrency, 0) / total if total else 0.0


@dataclass(frozen=True)
class BookOutcome:
    """북 실행 결과 — 배치 순서의 거래 목록 + 진단 + 실제 쓰인 설정.

    `effective_config`는 배수가 실린 사이징까지 반영된 값이다 — 리포트가 이걸 그대로
    실어야 "어느 사이징으로 돈 결과인가"가 CSV에서 읽힌다(WAN-103 `apply_portfolio_leverage`
    와 같은 이유). 자본곡선·지표는 `build_result_from_trades(outcome.trades, ...)`로 만든다.
    """

    trades: list[Trade]
    stats: BookStats
    effective_config: BacktestConfig


def scale_sizing_params(sizing: PositionSizingParams, multiple: float) -> PositionSizingParams:
    """사이징 파라미터에 배수 N을 싣는다 — 「매 거래 크기 N배」의 구현.

    `risk_pct` 모드는 `risk_per_trade`, `fixed_notional` 모드는 `notional_fraction`이
    거래 크기를 정하므로 둘 다 N배 하고, 거래·북 공용 명목 천장(`leverage`)도 N배 한다.
    상한만 키우고 크기를 안 키우면 그것이 정확히 폐기된 cap-only 모델이다(이슈 코멘트
    2026-07-22가 대체한 그 문장) — 세 필드를 한 곳에서 함께 키워 그 어긋남을 막는다.
    """
    return sizing.model_copy(
        update={
            "risk_per_trade": sizing.risk_per_trade * multiple,
            "notional_fraction": sizing.notional_fraction * multiple,
            "leverage": sizing.leverage * multiple,
        }
    )


def apply_book_leverage(cfg: BacktestConfig, book: LeverageBookParams) -> BacktestConfig:
    """`leverage_multiple`을 사이징에 실은 북 실행용 설정을 낸다.

    `risk_sizing=None`(전액 진입 모드)은 거부한다 — 그 모드에는 「거래당 리스크」라는
    개념이 없어 배수를 실을 자리가 없고, 조용히 무시하면 "N배로 돌렸다"는 라벨을 단
    1배 결과가 된다(WAN-95 부류).
    """
    if cfg.risk_sizing is None:
        raise ValueError(
            "레버리지 북은 리스크 사이징(risk_sizing)이 필요합니다 — 전액 진입 모드"
            "(risk_sizing=None)에는 거래당 리스크가 없어 배수를 정의할 수 없습니다(WAN-169)."
        )
    return cfg.model_copy(
        update={"risk_sizing": scale_sizing_params(cfg.risk_sizing, book.leverage_multiple)}
    )


@dataclass
class _OpenBookPosition:
    """열린 칸 하나의 회계 상태."""

    cell: CellKey
    trade: Trade
    exit_time: int
    notional: float
    risk_amount: float
    """손절까지 갔을 때의 손실(수수료·펀딩 제외). 최악 가정 청산 검사용."""


def _validate_cells(cells: Sequence[BookCell]) -> None:
    """칸 키 중복을 거부한다 — 같은 (종목, TF)가 두 번 들어오면 「칸당 1포지션」이
    조용히 「같은 칸 2포지션」이 된다(census의 `validate_single_position`과 같은 이유)."""
    seen: set[CellKey] = set()
    for cell in cells:
        if cell.key in seen:
            raise ValueError(f"칸이 중복됐습니다: {cell.key} — 칸 = (종목, TF)는 유일해야 합니다.")
        seen.add(cell.key)


def _notional_cap(cfg: BacktestConfig, equity: float) -> float:
    """이 자본에서 허용되는 열린 명목 합의 상한.

    `position_size`의 clamp와 같은 식(`equity × leverage`, `max_notional_fraction`과 min)
    이어야 한다 — 여기서 "여유 있음"이라 판정한 진입을 사이징이 0으로 거부하면 그 스킵이
    사이징 거부로 잘못 분류된다(`backtest.portfolio._notional_cap`과 같은 계약). 배수는
    이미 `apply_book_leverage`가 `risk_sizing.leverage`에 실었으므로 여기서 또 곱하지
    않는다 — 두 곳이 각자 곱하면 상한이 N²배가 된다.
    """
    assert cfg.risk_sizing is not None  # apply_book_leverage가 보장.
    cap = equity * cfg.risk_sizing.leverage
    if cfg.risk_sizing.max_notional_fraction is not None:
        cap = min(cap, equity * cfg.risk_sizing.max_notional_fraction)
    return cap


def _unclamped_notional(cand: _Candidate, cfg: BacktestConfig, equity: float) -> float:
    """명목 상한이 없었다면 이 후보가 가졌을 명목가 — 축소 진입 판정용.

    `backtest.portfolio._unclamped_notional`과 같은 식이되 `risk_sizing=None` 분기가
    없다(`apply_book_leverage`가 거부하므로). 진단에만 쓰이고 손익에는 들어가지 않는다.
    """
    sizing = cfg.risk_sizing
    assert sizing is not None
    if sizing.sizing_mode == "fixed_notional":
        return equity * sizing.notional_fraction
    stop_distance = abs(cand.entry_price - cand.stop_price)
    if stop_distance <= 0.0:
        return 0.0
    return (equity * sizing.risk_per_trade / stop_distance) * cand.entry_price


_MAX_TIME = 1 << 62


def run_leverage_book(
    cells: Sequence[BookCell],
    cfg: BacktestConfig,
    book: LeverageBookParams,
    *,
    eval_from_ms: int | None = None,
) -> BookOutcome:
    """칸별 후보를 하나의 공통 시간축에서 공유 자본으로 배치한다.

    진입 시각 오름차순으로 훑으며(동률이면 청산 시각 → 칸 키 순 — 실행마다 같은 순서),
    새 진입 시각에 도달하면 그때까지 청산된 포지션의 손익을 공유 현금에 실현하고, **자기
    칸이 비어 있는지**(칸당 1포지션) → 북 명목 여유(스킵/축소) → 사이징 순으로 검사한다.

    `eval_from_ms`(WAN-166 따뜻한 연속 OOS)를 주면 탭(`trigger_time`)이 그 시각 이후인
    후보만 배치한다 — 워밍업 후보는 **배치조차 하지 않으므로**(straddle 회계 (b), 사용자
    결정) 경계를 넘어 사는 워밍업 포지션이 평가 초입의 자본·칸·레버리지 자리를 점유하지
    않는다. 호출부는 후보를 **전체 창에서 연속으로** 만들어 넘겨야 한다(존 재고·지표가
    데워진 상태 — `run_zone_limit_backtest_verbose(eval_from_ms=...)`와 같은 규약).

    반환 거래 목록은 **배치(진입 시각) 순**이다 — 자본곡선은 청산 시각 순으로 다시
    정렬해 만든다(`build_result_from_trades`가 그렇게 한다).
    """
    _validate_cells(cells)
    eff_cfg = apply_book_leverage(cfg, book)

    merged: list[tuple[_Candidate, BookCell]] = []
    for cell in cells:
        for cand in cell.candidates:
            if eval_from_ms is not None and cand.trigger_time < eval_from_ms:
                continue  # straddle 회계 (b): 워밍업 셋업은 배치조차 하지 않는다.
            merged.append((cand, cell))
    merged.sort(key=lambda pair: (pair[0].entry_time, pair[0].exit_time, pair[1].key))

    cash = eff_cfg.initial_capital
    open_by_cell: dict[CellKey, _OpenBookPosition] = {}
    trades: list[Trade] = []
    stats = BookStats()
    last_event: int | None = None

    def advance(end: int) -> None:
        nonlocal last_event
        if last_event is not None and end > last_event:
            concurrency = len(open_by_cell)
            stats.concurrency_histogram[concurrency] = (
                stats.concurrency_histogram.get(concurrency, 0) + end - last_event
            )
        last_event = end

    def close_due(now: int) -> None:
        """`now` 이전에 청산된 포지션의 손익을 공유 현금에 실현한다(시각순).

        반개구간 규약: `exit_time == now`도 닫는다 — 같은 시각의 청산·재진입(같은 칸
        연속 거래)이 겹침으로 세어지지 않는다(census `[entry, exit)`와 같은 경계).
        """
        nonlocal cash
        due = sorted(
            (p for p in open_by_cell.values() if p.exit_time <= now),
            key=lambda p: (p.exit_time, p.cell),
        )
        for position in due:
            advance(position.exit_time)
            cash += position.trade.realized_pnl
            del open_by_cell[position.cell]

    for cand, cell in merged:
        close_due(cand.entry_time)
        advance(cand.entry_time)

        if cell.key in open_by_cell:
            stats.skipped_cell_busy += 1  # 칸당 1포지션(사용자 정의).
            continue
        open_notional = sum(p.notional for p in open_by_cell.values())
        if open_notional >= _notional_cap(eff_cfg, cash):
            stats.skipped_notional += 1
            continue
        trade = _to_trade(cand, cash, eff_cfg, cell.funding_rates or None, open_notional)
        if trade is None:
            stats.skipped_sizing += 1
            continue

        notional = trade.entry_price * trade.quantity
        wanted = _unclamped_notional(cand, eff_cfg, cash)
        if wanted > 0.0 and notional < wanted * (1.0 - 1e-9):
            stats.clamped_entries += 1
        risk_amount = abs(trade.entry_price - cand.stop_price) * trade.quantity
        open_by_cell[cell.key] = _OpenBookPosition(
            cell=cell.key,
            trade=trade,
            exit_time=cand.exit_time,
            notional=notional,
            risk_amount=risk_amount,
        )
        trades.append(trade)
        stats.placed += 1
        _observe(stats, cand.entry_time, cash, open_by_cell, book)

    close_due(_MAX_TIME)
    return BookOutcome(trades=trades, stats=stats, effective_config=eff_cfg)


def _observe(
    stats: BookStats,
    time: int,
    cash: float,
    open_by_cell: dict[CellKey, _OpenBookPosition],
    book: LeverageBookParams,
) -> None:
    """진입 직후의 북 상태를 계측하고 최악 가정 청산을 검사한다(WAN-103 결정 4 재사용).

    최악 가정: 열린 포지션이 **전부 동시에** 손절까지 간다. 각 포지션은 손절에 닿는
    순간 청산되므로 손절 거리가 최대 역행폭이다 — 실제 가격 경로를 몰라도 참인 상한이다.
    이 이벤트가 있다고 백테스트 자본이 실제로 전멸했다는 뜻은 **아니지만**, 그 배수는
    구조적으로 마진콜 사거리 안에 있다는 신호다(발생 건수가 판정 열).
    """
    concurrency = len(open_by_cell)
    if concurrency > stats.peak_concurrency:
        stats.peak_concurrency = concurrency
        stats.peak_concurrency_time = time

    open_notional = sum(p.notional for p in open_by_cell.values())
    total_risk = sum(p.risk_amount for p in open_by_cell.values())
    if cash > 0:
        stats.max_open_notional_ratio = max(stats.max_open_notional_ratio, open_notional / cash)
        stats.max_concurrent_risk_ratio = max(stats.max_concurrent_risk_ratio, total_risk / cash)

    worst_equity = cash - total_risk
    maintenance = open_notional * book.maintenance_margin_rate
    if worst_equity <= maintenance:
        stats.liquidations.append(
            LiquidationEvent(
                time=time,
                concurrency=concurrency,
                equity=cash,
                worst_equity=worst_equity,
                maintenance_margin=maintenance,
            )
        )
        logger.warning(
            "북 청산 트리거(최악 가정): t=%d, 동시 %d칸, 자본 %.2f, 전부 손절 시 %.2f ≤ "
            "유지증거금 %.2f — 이 배수는 마진콜 사거리 안에 있습니다(WAN-169).",
            time,
            concurrency,
            cash,
            worst_equity,
            maintenance,
        )
