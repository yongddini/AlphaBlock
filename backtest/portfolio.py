"""동시 다중 포지션 + 포트폴리오 레버리지 회계 (WAN-103).

`backtest.zone_limit_backtest._sequence_and_cost`는 체결 후보를 시간순으로 훑으며
**직전 포지션이 청산된 뒤에 진입하는 것만** 채택한다(WAN-23의 동시 1포지션 규칙).
그래서 한 존을 들고 있는 동안 다른 존의 첫 탭은 통째로 버려진다 — WAN-83이 그 소각을
계측했다(series 범위 163건 스킵 중 34건이 영영 진입을 잃음, global 범위에서는 1,968건
스킵·554건 소각). 이 모듈은 그 제약을 풀어 **여러 존에 동시 진입**하고, 늘어난 총
명목가치를 레버리지 한도 아래에서 관리한다.

## 회계 규칙 (결정 근거는 `docs/decisions/wan103.md`)

* **사이징 자본 = 실현 현금**(`cash`). 미실현손익은 사이징에 반영하지 않는다 — 열린
  포지션의 평가손익을 알려면 모든 심볼의 공통 시간축 마크가 필요한데, 이 엔진은
  셋업별 1분봉만 본다. 현금 기준은 동시 1포지션 엔진과 **같은 정의**라 대조표의 두
  열이 같은 자를 쓴다(단일 포지션에서는 진입 시점에 미실현이 0이므로 완전히 동일).
* **명목 상한 = 자본 × leverage**, 열린 포지션 전체 합에 걸린다. 여유분이 남으면
  **축소 진입**(clamp), 여유가 0이면 스킵 — `execution.sizing.position_size`의 기존
  clamp 의미를 포트폴리오로 그대로 넓힌 것이다(결정 2).
* **같은 존은 동시 1포지션**. 서로 다른 존만 동시 허용한다(결정 1). 존 식별자는
  `_Candidate.zone_key`(병합 존 재계산에도 안정적, WAN-83).
* **거래당 리스크 1% 유지**, 별도의 총 리스크 상한은 두지 않는다(결정 3). 대신 동시
  리스크 합의 최댓값을 계측해 리포트에 싣는다.
* **청산은 최악 가정으로 계측**한다(결정 4). 열린 포지션이 **전부 동시에 손절까지**
  갔다고 보고 그때의 자본이 유지증거금 아래로 내려가는지 검사한다. 각 포지션은 손절에
  닿는 순간 청산되므로 손절 거리가 그 포지션의 최대 역행폭이다 — 즉 이 검사는 실제
  가격 경로를 몰라도 **참인 상한**이다(느슨한 근사가 아니라 보수적 한계).

## 이 모듈이 하지 않는 것

부분 청산·포지션 증액·심볼 간 상관 모델은 없다. 실주문 경로(`execution`, `live`)도
건드리지 않는다 — 라이브는 아직 지정가조차 집행하지 못한다(WAN-45).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from backtest.models import BacktestConfig, Trade
from data.models import FundingRate

logger = logging.getLogger(__name__)

#: 바이낸스 USDT-M 무기한 1티어 유지증거금률의 보수적 근사(BTC/ETH는 0.4%, 알트는 더 높다).
#: 정확한 티어 테이블은 명목 구간별로 달라지지만, 이 엔진의 청산 검사는 **최악 가정**이라
#: 티어를 정밀화해도 결론이 바뀌지 않는다(§4 참고) — 그래서 상수 하나로 둔다.
DEFAULT_MAINTENANCE_MARGIN_RATE = 0.005


class PortfolioParams(BaseModel):
    """동시 다중 포지션 회계 파라미터 (WAN-103).

    이 객체를 **주지 않으면** 엔진은 동시 1포지션 경로를 그대로 탄다 — 기본값을 바꾸지
    않는다는 뜻이다(결정 5, `ConfluenceParams()`는 손대지 않았다).
    """

    model_config = ConfigDict(frozen=True)

    leverage: float = Field(default=1.0, gt=0)
    """포트폴리오 총 명목 상한 배수. `열린 명목 합 ≤ 자본 × leverage`(결정 2).

    `cfg.risk_sizing.leverage`를 덮어쓴다 — 레버리지를 스윕하는 축이 여기 하나뿐이어야
    "어느 레버리지로 돈 결과인가"가 한 곳에서 읽힌다.
    """
    max_concurrent: int | None = Field(default=None, ge=1)
    """동시 보유 포지션 수 상한. None이면 무제한(명목 상한만으로 통제, 결정 1)."""
    one_per_zone: bool = True
    """같은 존(`zone_key`)에 동시 2포지션을 금지할지. 기본 True(결정 1).

    끄면 같은 존의 재탭이 이미 열린 포지션 위에 겹쳐 쌓인다 — 한 존이 무효화되면 그
    포지션들이 **함께** 손절되므로 리스크가 존 단위로 배가된다. 기본값은 그것을 막는다.
    """
    maintenance_margin_rate: float = Field(default=DEFAULT_MAINTENANCE_MARGIN_RATE, ge=0, lt=1)
    """청산 검사에 쓰는 유지증거금률(명목 대비, 결정 4)."""


@dataclass
class _OpenPosition:
    """열린 포지션 하나의 회계 상태."""

    trade: Trade
    exit_time: int
    zone_key: frozenset[int] | None
    notional: float
    """진입 명목가(= 체결가 × 수량). 명목 상한 검사의 단위."""
    risk_amount: float
    """이 포지션이 손절까지 갔을 때의 손실(수수료·펀딩 제외). 최악 가정 청산 검사용."""


@dataclass
class LiquidationEvent:
    """최악 가정 청산 트리거 하나 (결정 4).

    "이 시점에 열려 있던 포지션이 **전부 동시에** 손절됐다면 유지증거금을 못 냈다"는
    뜻이다. 실제로 그렇게 됐다는 뜻이 **아니다** — 상한 검사라 실제 경로는 이보다 낫다.
    그래도 이 플래그가 켜지면 그 레버리지는 후보에서 뺀다: 백테스트가 우연히 살아남았을
    뿐 구조적으로 마진콜 사거리 안에 들어와 있다는 신호이기 때문이다.
    """

    time: int
    concurrency: int
    equity: float
    worst_equity: float
    """열린 포지션이 전부 손절됐을 때의 자본."""
    maintenance_margin: float


@dataclass
class PortfolioStats:
    """포트폴리오 실행 진단 (WAN-103 리포트가 소비).

    `peak_concurrency`가 사용자가 물은 "역대 최대 겹침"이며, N배 레버리지 시나리오의
    N이 바로 이 값이다.
    """

    peak_concurrency: int = 0
    peak_concurrency_time: int | None = None
    concurrency_histogram: dict[int, int] = field(default_factory=dict)
    """동시 k개 보유였던 **시간**(ms) 합. 시간 가중이라 "대개 몇 개를 들고 있었나"를 답한다."""
    max_open_notional_ratio: float = 0.0
    """`열린 명목 합 / 자본`의 최댓값 — 레버리지 상한을 실제로 얼마나 썼는지."""
    max_concurrent_risk_ratio: float = 0.0
    """동시 리스크 합 / 자본의 최댓값(결정 3의 계측). 거래당 1%가 몇 %까지 겹쳤는가."""
    clamped_entries: int = 0
    """명목 상한에 걸려 **축소 진입**된 건수."""
    skipped_notional: int = 0
    """명목 상한 **소진**으로 스킵된 건수(여유분 ≤ 0)."""
    skipped_sizing: int = 0
    """사이징이 거부해 스킵된 건수(손절 거리 최소치 미달 등, `min_stop_distance_fraction`).

    명목 상한과 **무관**하며 동시 1포지션 경로에서도 똑같이 일어난다 — 두 사유를 한
    칸에 세면 "레버리지를 올려도 스킵이 안 줄어든다"가 상한 탓인지 사이징 탓인지
    구분할 수 없다.
    """
    skipped_zone: int = 0
    """같은 존에 이미 포지션이 있어 스킵된 건수(`one_per_zone`)."""
    skipped_max_concurrent: int = 0
    """`max_concurrent` 상한으로 스킵된 건수."""
    liquidations: list[LiquidationEvent] = field(default_factory=list)

    @property
    def liquidated(self) -> bool:
        return bool(self.liquidations)

    def time_share(self, concurrency: int) -> float:
        """동시 `concurrency`개였던 시간 비중(0~1). 전체 시간이 0이면 0."""
        total = sum(self.concurrency_histogram.values())
        return self.concurrency_histogram.get(concurrency, 0) / total if total else 0.0


class CandidateLike(Protocol):
    """시퀀서가 후보에게 요구하는 최소 정보.

    `_Candidate`(B안 전용 자료형)를 여기서 import하면 `zone_limit_backtest` ↔ `portfolio`가
    순환 import가 된다. 회계 규칙은 진입 경로와 무관하므로 필요한 필드만 구조적으로 받는다.
    """

    @property
    def entry_time(self) -> int: ...
    @property
    def entry_price(self) -> float: ...
    @property
    def exit_time(self) -> int: ...
    @property
    def stop_price(self) -> float: ...
    @property
    def zone_key(self) -> frozenset[int] | None: ...


C = TypeVar("C", bound=CandidateLike)

#: 콜백이 받는 후보 타입은 **반공변**이다 — `_Candidate`만 받는 `_to_trade`를
#: `ToTrade[_Candidate]`로 넘길 수 있어야 하기 때문이다.
C_contra = TypeVar("C_contra", bound=CandidateLike, contravariant=True)


class ToTrade(Protocol[C_contra]):
    """후보 → `Trade` 변환(비용·사이징). 진입 경로가 자기 것을 넘긴다.

    `open_notional`을 받는 이유는 명목 상한이 포트폴리오 전체에 걸리기 때문이다 —
    사이징이 그 여유분을 알아야 축소 진입/스킵을 판정할 수 있다.
    """

    def __call__(
        self,
        cand: C_contra,
        equity: float,
        cfg: BacktestConfig,
        funding_rates: Sequence[FundingRate] | None,
        open_notional: float,
    ) -> Trade | None: ...


def _notional_cap(cfg: BacktestConfig, equity: float, portfolio: PortfolioParams) -> float:
    """이 자본에서 허용되는 열린 명목 합의 상한.

    `position_size`의 clamp와 **같은 식**이어야 한다 — 여기서 "여유 있음"이라 판정한
    진입을 사이징이 상한으로 0을 내면, 그 스킵이 사이징 거부로 잘못 분류된다.

    `risk_sizing=None`(고정 비율 모드)이면 사이징은 상한을 아예 보지 않으므로 이 상한이
    **유일한** 통제 수단이다 — 그 모드에서 포지션은 축소되지 않고, 상한을 넘기는
    시점부터 스킵만 된다.
    """
    cap = equity * portfolio.leverage
    if cfg.risk_sizing is not None and cfg.risk_sizing.max_notional_fraction is not None:
        cap = min(cap, equity * cfg.risk_sizing.max_notional_fraction)
    return cap


def _unclamped_notional(cand: CandidateLike, cfg: BacktestConfig, equity: float) -> float:
    """명목 상한이 없었다면 이 후보가 가졌을 명목가 — 축소 진입 판정용.

    사이징 모드마다 식이 다르다(WAN-108): `risk_pct`면 `리스크금액 / 손절거리 × 진입가`,
    `fixed_notional`이면 `자본 × notional_fraction`, 리스크 사이징 자체가 없으면
    `자본 × position_fraction`이다. **`position_size`와 같은 식이어야** 한다 — 갈라지면
    축소되지 않은 진입이 `clamped_entries`로 잘못 세어진다(`fixed_notional`은 명목이
    손절 거리와 무관해서, 리스크 식을 그대로 쓰면 손절이 먼 자리마다 오탐이 난다).

    진입가는 아직 비용 반영 전이라 근사지만(메이커 진입은 슬리피지가 0이라 오차는 수수료
    수준), 이 값은 **"상한에 걸렸는가"를 세는 진단**에만 쓰이고 손익에는 들어가지 않는다.
    """
    if cfg.risk_sizing is None:
        return equity * cfg.position_fraction
    if cfg.risk_sizing.sizing_mode == "fixed_notional":
        return equity * cfg.risk_sizing.notional_fraction
    stop_distance = abs(cand.entry_price - cand.stop_price)
    if stop_distance <= 0.0:
        return 0.0
    return (equity * cfg.risk_sizing.risk_per_trade / stop_distance) * cand.entry_price


def sequence_portfolio(
    candidates: Sequence[C],
    cfg: BacktestConfig,
    to_trade: ToTrade[C],
    *,
    portfolio: PortfolioParams,
    funding_rates: Sequence[FundingRate] | None = None,
) -> tuple[list[tuple[C, Trade]], PortfolioStats]:
    """후보를 동시 다중 포지션으로 배치하고 (거래, 진단)을 낸다.

    진입 시각 오름차순으로 훑되, 동시 1포지션 엔진과 달리 **겹치는 후보를 버리지 않는다**.
    새 진입 시각에 도달하면 그때까지 청산된 포지션의 손익을 현금에 실현하고(사이징 자본
    갱신), 남은 명목 여유분·존 중복·동시 상한을 검사해 진입 여부와 크기를 정한다.

    반환하는 거래 목록은 **진입 시각 순**이다 — 자본곡선은 청산 시각 순으로 다시 정렬해
    만들어야 하므로(`build_result_from_trades`가 그렇게 한다) 여기서는 배치 순서를 남긴다.
    """
    ordered = sorted(candidates, key=lambda c: (c.entry_time, c.exit_time))
    cash = cfg.initial_capital
    open_positions: list[_OpenPosition] = []
    paired: list[tuple[C, Trade]] = []
    stats = PortfolioStats()
    last_event: int | None = None

    def close_due(now: int) -> None:
        """`now` 이전에 청산된 포지션의 손익을 현금에 실현한다(시각순)."""
        nonlocal cash, last_event
        due = sorted((p for p in open_positions if p.exit_time <= now), key=lambda p: p.exit_time)
        for position in due:
            _advance(stats, last_event, position.exit_time, len(open_positions))
            last_event = position.exit_time
            cash += position.trade.realized_pnl
            open_positions.remove(position)

    for cand in ordered:
        close_due(cand.entry_time)
        _advance(stats, last_event, cand.entry_time, len(open_positions))
        last_event = cand.entry_time

        if (
            portfolio.one_per_zone
            and cand.zone_key is not None
            and any(p.zone_key == cand.zone_key for p in open_positions)
        ):
            stats.skipped_zone += 1
            continue
        if portfolio.max_concurrent is not None and len(open_positions) >= portfolio.max_concurrent:
            stats.skipped_max_concurrent += 1
            continue

        open_notional = sum(p.notional for p in open_positions)
        if open_notional >= _notional_cap(cfg, cash, portfolio):
            stats.skipped_notional += 1
            continue
        trade = to_trade(cand, cash, cfg, funding_rates, open_notional)
        if trade is None:
            # 상한 여유는 있는데 사이징이 거부했다 — 손절 거리 최소치 미달 등.
            stats.skipped_sizing += 1
            continue

        notional = trade.entry_price * trade.quantity
        wanted = _unclamped_notional(cand, cfg, cash)
        if wanted > 0.0 and notional < wanted * (1.0 - 1e-9):
            stats.clamped_entries += 1
        risk_amount = abs(trade.entry_price - cand.stop_price) * trade.quantity
        open_positions.append(
            _OpenPosition(
                trade=trade,
                exit_time=cand.exit_time,
                zone_key=cand.zone_key,
                notional=notional,
                risk_amount=risk_amount,
            )
        )
        paired.append((cand, trade))
        _observe(stats, cand.entry_time, cash, open_positions, portfolio)

    close_due(_MAX_TIME)
    return paired, stats


#: `close_due`가 남은 포지션을 전부 청산시키기 위한 상한(어떤 실제 ms 시각보다 크다).
_MAX_TIME = 1 << 62


def _advance(stats: PortfolioStats, start: int | None, end: int, concurrency: int) -> None:
    """`[start, end)` 동안 동시 `concurrency`개였음을 시간 히스토그램에 기록한다."""
    if start is None or end <= start:
        return
    stats.concurrency_histogram[concurrency] = (
        stats.concurrency_histogram.get(concurrency, 0) + end - start
    )


def _observe(
    stats: PortfolioStats,
    time: int,
    cash: float,
    open_positions: list[_OpenPosition],
    portfolio: PortfolioParams,
) -> None:
    """진입 직후의 포트폴리오 상태를 계측하고 최악 가정 청산을 검사한다(결정 4)."""
    concurrency = len(open_positions)
    if concurrency > stats.peak_concurrency:
        stats.peak_concurrency = concurrency
        stats.peak_concurrency_time = time

    open_notional = sum(p.notional for p in open_positions)
    total_risk = sum(p.risk_amount for p in open_positions)
    if cash > 0:
        stats.max_open_notional_ratio = max(stats.max_open_notional_ratio, open_notional / cash)
        stats.max_concurrent_risk_ratio = max(stats.max_concurrent_risk_ratio, total_risk / cash)

    # 최악 가정: 열린 포지션이 전부 동시에 손절까지 간다. 각 포지션은 손절에 닿는 순간
    # 청산되므로 손절 거리가 최대 역행폭이다 — 실제 가격 경로를 몰라도 참인 상한이다.
    worst_equity = cash - total_risk
    maintenance = open_notional * portfolio.maintenance_margin_rate
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
            "청산 트리거(최악 가정): t=%d, 동시 %d포지션, 자본 %.2f, 전부 손절 시 %.2f ≤ "
            "유지증거금 %.2f — 이 레버리지는 마진콜 사거리 안에 있습니다(WAN-103 결정 4).",
            time,
            concurrency,
            cash,
            worst_equity,
            maintenance,
        )
