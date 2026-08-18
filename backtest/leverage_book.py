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

## cap-only 레버리지 (WAN-180, 옵트인 팔 B)

`leverage_mode="cap_only"`는 **지갑 명목 상한만 N배로 키우고 거래당 크기는 1배 그대로**
둔다(= 같은 크기 포지션을 더 많이 동시에). 목적은 밀림(스킵)을 직접 줄이는 것이고, 그
대가로 최대 동시 리스크가 결합 팔보다 커질 수 있다 — 그 교환이 WAN-180 팔 B의 판정
대상이다. ⚠️ **거래당 명목 천장도 1배(기본 leverage)로 남는다** — 상한을 키운 만큼
개별 거래가 커지면 그건 cap-only가 아니라 결합의 반쪽이다. **WAN-213부터 `cap_only`가
클래스 기본값(배수 5)이다** — 채택 북이다. WAN-169 중립 기준점(`combined` · 배수 1)은
`LEGACY_BOOK_PARAMS`로 명시하며, 그 값에서 기존 결과가 비트 단위로 재현된다.

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
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field

from backtest.models import BacktestConfig, Trade
from backtest.portfolio import LiquidationEvent
from backtest.zone_limit_backtest import _Candidate, _to_trade
from data.models import FundingRate

# 사이징 회계(배수·상한·cap-only 합성)의 정본은 `execution.leverage`다 — 백테스트 배치와
# 라이브 집행이 같은 함수를 공유하기 위함이다(WAN-171). 여기서는 하위 호환을 위해
# 재수출한다(기존 `from backtest.leverage_book import LeverageBookParams …` import·CSV 재현
# 불변). 배치 전용(`apply_book_leverage`·`run_leverage_book`)만 이 모듈에 남는다.
from execution.leverage import (
    LEGACY_BOOK_PARAMS,
    BookSizing,
    LeverageBookParams,
    LeverageMode,
    resolve_book_sizing,
    scale_sizing_params,
    sizing_notional_cap,
)

logger = logging.getLogger(__name__)

#: 칸 식별자 = (종목, 타임프레임). 사용자 정의의 진입 단위다.
CellKey = tuple[str, str]

# 하위 호환 재수출을 명시(정적 분석이 「미사용 import」로 오해하지 않게).
__all__ = [
    "LEGACY_BOOK_PARAMS",
    "BookCell",
    "BookOutcome",
    "BookSizing",
    "BookStats",
    "CellKey",
    "LeverageBookParams",
    "LeverageMode",
    "PlacedSetup",
    "SkippedSetup",
    "apply_book_leverage",
    "resolve_book_sizing",
    "run_leverage_book",
    "scale_sizing_params",
    "sizing_notional_cap",
]


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


@dataclass(frozen=True)
class SkippedSetup:
    """배치되지 못한 후보 하나의 기록 (WAN-180 밀림 기회비용의 원자료).

    `equity`는 스킵 판정 순간의 공유 자본이다 — 「그때 이 셋업을 넣었다면」의 가상
    사이징(격리 상한 계산)이 이 값 위에서 선다. 후보(`candidate`)는 격리 청산 결과
    (`exit_price`·`exit_time`·`stop_price`)를 이미 품고 있어(`build_zone_limit_candidates`
    산출물 그대로) 가상 손익은 재시뮬레이션 없이 `_to_trade`로 값이 매겨진다.
    """

    cell: CellKey
    reason: str
    """`"cell_busy"`(칸 점유) · `"notional"`(북 명목 상한 소진) · `"sizing"`(사이징 거부)."""
    candidate: _Candidate
    equity: float


@dataclass(frozen=True)
class PlacedSetup:
    """배치된 거래 하나의 회계 스냅샷 (WAN-180 기회비용 표의 「실현」 쪽 대조군)."""

    cell: CellKey
    equity: float
    """진입 순간의 공유 자본."""
    risk_amount: float
    """손절까지 갔을 때의 손실(수수료·펀딩 제외) — 거래당 net R의 분모."""
    realized_pnl: float


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
    """동시 리스크 합 / 공유 자본의 최댓값. 거래당 N%가 몇 %까지 겹쳤는가.

    ⚠️ **주문을 낼 때 「계획한」 리스크**다 — 손절 한 번 = 정확히 1R(존 무효화 경계에서
    그 값 그대로 체결)이라는 가정 위 값이라, 손절 체결 보수화 축(WAN-276 α · WAN-312 k)에
    **설계상 불변**이다. 실제로 물릴 수 있는 비율은 아래 `max_effective_concurrent_risk_ratio`.
    """
    max_effective_concurrent_risk_ratio: float = 0.0
    """**실효** 동시 리스크 = (동시 리스크 합 × `stress_risk_multiple`) / 공유 자본의 최댓값
    (WAN-312).

    「손절 한 번이 계획 1R이 아니라 k·R이면 지금 열려 있는 칸들이 자본의 몇 %를 깰 수 있나」
    다. `stress_risk_multiple=1.0`(기본)이면 계획값과 **정확히 같아** 예전 CSV가 비트
    재현되고, k>1이면 계획값과 벌어진 폭이 곧 「cap_only 5배의 안전 논리가 기대고 있던
    가정의 크기」다(WAN-213 근거 열의 스트레스)."""
    placed: int = 0
    """실제로 배치된(거래가 된) 후보 수."""
    clamped_entries: int = 0
    """명목 상한에 걸려 **축소 진입**된 건수(레버리지·정책·유동성 한도 전부 포함)."""
    adv_capped_entries: int = 0
    """유동성 한도(WAN-244, `max_notional_adv_fraction`)이 **구속 제약**이었던 진입 건수 —
    희망 명목이 `k×ADV_usd`보다 컸고 최종 명목이 그 상한에 붙은 경우다. 상한이 꺼져 있으면
    항상 0이라 기본 실행이 비트 재현된다(카운터를 세지 않으므로). `clamped_entries`의 부분집합."""
    first_adv_cap_time: int | None = None
    """유동성 한도가 **처음** 구속한 진입의 시각(ms). 상한이 한 번도 안 걸렸으면 None."""
    first_adv_cap_equity: float | None = None
    """그 첫 구속 순간의 공유 자본(USD) — 「자본 얼마부터 상한이 발동하나」(WAN-244 판정 c)."""
    skipped_cell_busy: int = 0
    """자기 칸에 이미 포지션이 있어 스킵된 건수 — 칸당 1포지션(사용자 정의)의 계측."""
    skipped_notional: int = 0
    """북 명목 상한 **소진**으로 스킵된 건수(여유분 ≤ 0)."""
    skipped_sizing: int = 0
    """사이징이 거부해 스킵된 건수(손절 거리 최소치 미달 등) — 상한과 무관하며 단일
    포지션 경로에서도 똑같이 일어난다(`backtest.portfolio.PortfolioStats`와 같은 분리)."""
    liquidations: list[LiquidationEvent] = field(default_factory=list)
    skip_records: list[SkippedSetup] = field(default_factory=list)
    """스킵된 후보 하나하나의 기록(WAN-180) — 카운터의 원자료라 합이 항상 카운터와 같다."""
    placed_records: list[PlacedSetup] = field(default_factory=list)
    """배치된 거래 하나하나의 회계 스냅샷(WAN-180) — `placed`와 길이가 같다."""

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
        update={
            "risk_sizing": scale_sizing_params(
                cfg.risk_sizing, book.leverage_multiple, mode=book.leverage_mode
            )
        }
    )


@dataclass(frozen=True)
class _Reduction:
    """부분 청산 하나가 북 회계에 일으키는 변화 (WAN-323 반익절 래더).

    북은 원래 진입 시점의 `notional`·`risk_amount` 스냅샷을 최종 청산까지 들고 있고
    「도중에 줄었다」는 이벤트가 없었다 — 그대로 두면 이미 덜어낸 명목·위험을 계속 세서
    **하필 래더에 불리한 방향으로** 편향된다(래더의 존재 이유가 위험을 일찍 더는 것이다).
    """

    time: int
    notional: float
    """이 부분 청산이 해제하는 명목(진입 명목 × 그 체결의 수량 비중)."""
    risk: float
    """이 부분 청산이 해제하는 리스크 금액."""
    cash: float
    """이 시점에 공유 현금에 실현되는 손익 — 그 체결의 그로스 − 그 체결 수수료 −
    진입 수수료의 수량 비중. **펀딩은 최종 청산에 남긴다**(구간 배분을 여기서 또 하면
    `_to_trade`의 산식과 갈라진다). 합계가 어긋나지 않는 것은 최종 청산이
    `realized_pnl − 이미 반영한 누계`를 내기 때문이다 — **총액은 정의상 정확**하고
    근사되는 것은 **시점**뿐이다."""


@dataclass
class _OpenBookPosition:
    """열린 칸 하나의 회계 상태."""

    cell: CellKey
    trade: Trade
    exit_time: int
    notional: float
    risk_amount: float
    """손절까지 갔을 때의 손실(수수료·펀딩 제외). 최악 가정 청산 검사용."""
    reductions: list[_Reduction] = field(default_factory=list)
    """아직 반영하지 않은 부분 청산(시각 오름차순). 래더를 안 켜면 **항상 비어 있다**."""
    credited: float = 0.0
    """이미 공유 현금에 반영한 손익 누계. 최종 청산은 `realized_pnl − credited`를 낸다."""


def _reductions_for(
    trade: Trade, partial_count: int, notional: float, risk: float
) -> list[_Reduction]:
    """부분 청산 체결들을 북 회계 이벤트로 바꾼다 (WAN-323).

    `trade.exits`는 **부분들 → 최종** 순서이므로 앞 `partial_count`개가 부분 청산이다
    (`zone_limit_backtest._to_trade`가 그 순서로 만든다). 부분이 없으면 빈 리스트라
    북이 예전과 **비트 단위로** 같이 돈다.
    """
    if partial_count <= 0 or trade.quantity <= 0.0:
        return []
    out: list[_Reduction] = []
    for fill in trade.exits[:partial_count]:
        share = fill.quantity / trade.quantity
        gross = trade.side.sign * (fill.price - trade.entry_price) * fill.quantity
        out.append(
            _Reduction(
                time=fill.time,
                notional=notional * share,
                risk=risk * share,
                cash=gross - fill.fee - trade.entry_fee * share,
            )
        )
    return out


def _validate_cells(cells: Sequence[BookCell]) -> None:
    """칸 키 중복을 거부한다 — 같은 (종목, TF)가 두 번 들어오면 「칸당 1포지션」이
    조용히 「같은 칸 2포지션」이 된다(census의 `validate_single_position`과 같은 이유)."""
    seen: set[CellKey] = set()
    for cell in cells:
        if cell.key in seen:
            raise ValueError(f"칸이 중복됐습니다: {cell.key} — 칸 = (종목, TF)는 유일해야 합니다.")
        seen.add(cell.key)


def _notional_cap(cfg: BacktestConfig, equity: float) -> float:
    """이 자본에서 허용되는 열린 명목 합의 상한 — `sizing_notional_cap`(공용)에 위임.

    배수는 이미 `apply_book_leverage`가 `risk_sizing.leverage`에 실었으므로 여기서 또
    곱하지 않는다(두 곳이 각자 곱하면 상한이 N²배). 라이브 집행과 같은 상한식을 쓰기
    위해 `execution.leverage.sizing_notional_cap`을 공유한다(WAN-171).
    """
    assert cfg.risk_sizing is not None  # apply_book_leverage가 보장.
    return sizing_notional_cap(cfg.risk_sizing, equity)


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


class _FundingIndex:
    """칸 하나의 펀딩 정산을 시각 bisect로 자르는 색인 (성능 전용 — 손익 불변).

    `_to_trade`(`_funding_cost_for`)는 넘겨받은 리스트 **전체**를 훑으며 보유 구간
    `[entry, exit)` 밖을 걸러낸다 — 거래마다 O(전체 정산 수)라 6년·9종목 격자(WAN-180)
    에서 병목이 된다. 정산이 시각 오름차순일 때 구간을 미리 잘라 넘기면 **같은 부분집합을
    같은 순서로 누적**하므로 결과가 비트 단위로 같다(필터는 잘린 리스트에서 전부 no-op).
    오름차순이 아니면(정상 데이터에선 없다) 자르는 것 자체가 다른 부분집합이 될 수 있어
    전체 리스트로 물러난다 — 빨라지려다 값이 달라지는 것이 최악이다.
    """

    __slots__ = ("_rates", "_times")

    def __init__(self, rates: Sequence[FundingRate]) -> None:
        self._rates = rates
        times = [r.funding_time for r in rates]
        sorted_ok = all(times[i] <= times[i + 1] for i in range(len(times) - 1))
        self._times: list[int] | None = times if sorted_ok else None

    def window(self, start_ms: int, end_ms: int) -> Sequence[FundingRate] | None:
        """`[start_ms, end_ms)` 정산만 — `_funding_cost_for`의 필터와 같은 반개구간."""
        if self._times is None:
            return self._rates or None
        lo = bisect_left(self._times, start_ms)
        hi = bisect_left(self._times, end_ms)
        return self._rates[lo:hi] or None


_MAX_TIME = 1 << 62


def run_leverage_book(
    cells: Sequence[BookCell],
    cfg: BacktestConfig,
    book: LeverageBookParams,
    *,
    eval_from_ms: int | None = None,
    stress_risk_multiple: float = 1.0,
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

    `stress_risk_multiple`(WAN-312, 옵트인 스트레스 노브)은 **최악 가정에서 한 포지션이
    잃는 크기**를 계획 1R의 k배로 본다 — 열린 포지션이 전부 손절까지 갔을 때의 손실이
    `k × Σrisk`라고 보고 청산 검사와 `max_effective_concurrent_risk_ratio`를 낸다. 계획
    리스크(`max_concurrent_risk_ratio`)는 **손대지 않아** 둘의 벌어짐이 표에서 읽힌다.
    `1.0`(기본)이면 `Σrisk × 1.0 == Σrisk`라 예전과 **비트 단위로 같다**. 이 노브는 손절
    **체결가**를 바꾸지 않는다 — 실현 손익 쪽은 후보 변환(`wan312.apply_stop_multiple`)이
    담당하고, 이 노브는 「아직 안 났지만 날 수 있는 손실」의 자 하나만 바꾼다.

    반환 거래 목록은 **배치(진입 시각) 순**이다 — 자본곡선은 청산 시각 순으로 다시
    정렬해 만든다(`build_result_from_trades`가 그렇게 한다).
    """
    if stress_risk_multiple < 1.0:
        raise ValueError(
            "stress_risk_multiple은 1.0 이상이어야 합니다(계획 1R보다 유리한 손절 체결은 "
            f"이 스트레스 축이 아닙니다): {stress_risk_multiple} (WAN-312)."
        )
    _validate_cells(cells)
    eff_cfg = apply_book_leverage(cfg, book)
    # cap-only(WAN-180 팔 B)는 북 상한만 N배다 — 거래당 사이징(크기·천장)은 원본 1배
    # 설정으로 잰다. combined에서는 두 설정이 같아 아래 산식이 기존 경로와 비트 일치한다.
    size_cfg = cfg if book.leverage_mode == "cap_only" else eff_cfg

    merged: list[tuple[_Candidate, BookCell]] = []
    for cell in cells:
        for cand in cell.candidates:
            if eval_from_ms is not None and cand.trigger_time < eval_from_ms:
                continue  # straddle 회계 (b): 워밍업 셋업은 배치조차 하지 않는다.
            merged.append((cand, cell))
    merged.sort(key=lambda pair: (pair[0].entry_time, pair[0].exit_time, pair[1].key))

    funding_index = {cell.key: _FundingIndex(cell.funding_rates) for cell in cells}

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

    def settle_due(now: int) -> None:
        """`now` 이전의 **부분 청산(축소)과 최종 청산**을 시각순으로 반영한다.

        반개구간 규약: `exit_time == now`도 닫는다 — 같은 시각의 청산·재진입(같은 칸
        연속 거래)이 겹침으로 세어지지 않는다(census `[entry, exit)`와 같은 경계).

        WAN-323: 부분 청산은 명목·리스크를 그만큼 **줄이고** 그 몫의 손익을 그 시점에
        실현한다. 최종 청산은 `realized_pnl − 이미 반영한 누계`를 내므로 **총액은 정의상
        `_to_trade`와 정확히 같다**. 래더를 안 켜면 축소 이벤트가 없어 예전과 비트 동일하다
        (정렬 키 `(시각, 종류, 칸)`이 종류 1(청산)만 남아 옛 `(exit_time, cell)`과 같다).
        """
        nonlocal cash
        while True:
            events: list[tuple[int, int, CellKey]] = []
            for position in open_by_cell.values():
                if position.reductions and position.reductions[0].time <= now:
                    # 같은 포지션은 축소가 청산보다 먼저다(부분 청산 시각 ≤ 최종 청산 시각).
                    events.append((position.reductions[0].time, 0, position.cell))
                elif position.exit_time <= now:
                    events.append((position.exit_time, 1, position.cell))
            if not events:
                return
            events.sort()
            _, kind, cell_key = events[0]
            position = open_by_cell[cell_key]
            if kind == 0:
                reduction = position.reductions.pop(0)
                advance(reduction.time)
                cash += reduction.cash
                position.credited += reduction.cash
                position.notional -= reduction.notional
                position.risk_amount -= reduction.risk
            else:
                advance(position.exit_time)
                cash += position.trade.realized_pnl - position.credited
                del open_by_cell[cell_key]

    for cand, cell in merged:
        settle_due(cand.entry_time)
        advance(cand.entry_time)

        if cell.key in open_by_cell:
            stats.skipped_cell_busy += 1  # 칸당 1포지션(사용자 정의).
            stats.skip_records.append(SkippedSetup(cell.key, "cell_busy", cand, cash))
            continue
        open_notional = sum(p.notional for p in open_by_cell.values())
        # 사이징 결정(배수·북 상한·cap-only 합성 여유)은 라이브 집행과 **공유하는**
        # `resolve_book_sizing`이 낸다 — 두 경로가 상한식을 복제하면 갈라진다(WAN-171,
        # WAN-95/112/123의 조용한 실패 방지). combined는 합성 여유 = 실제 open_notional이라
        # 기존 CSV와 비트 일치하고, cap-only는 `min(거래당 천장, 북 여유)`를 합성한다.
        assert cfg.risk_sizing is not None  # apply_book_leverage가 보장.
        sizing = resolve_book_sizing(
            cfg.risk_sizing, book, equity=cash, open_notional=open_notional
        )
        if sizing.cap_exhausted:
            stats.skipped_notional += 1
            stats.skip_records.append(SkippedSetup(cell.key, "notional", cand, cash))
            continue
        rates = funding_index[cell.key].window(cand.entry_time, cand.exit_time)
        trade = _to_trade(cand, cash, size_cfg, rates, sizing.synthetic_open)
        if trade is None:
            stats.skipped_sizing += 1
            stats.skip_records.append(SkippedSetup(cell.key, "sizing", cand, cash))
            continue

        notional = trade.entry_price * trade.quantity
        wanted = _unclamped_notional(cand, size_cfg, cash)
        if wanted > 0.0 and notional < wanted * (1.0 - 1e-9):
            stats.clamped_entries += 1
        # WAN-244 유동성 한도가 **구속 제약**이었는지: 희망 명목이 `k×ADV_usd`를 넘었고
        # (상한이 실제로 깎았고) 최종 명목이 그 상한에 붙었으면 발동으로 센다. 레버리지
        # 상한이 더 작아 그쪽이 구속한 경우(명목 < ADV 상한)는 세지 않는다.
        assert size_cfg.risk_sizing is not None  # apply_book_leverage가 보장.
        adv_frac = size_cfg.risk_sizing.max_notional_adv_fraction
        if adv_frac is not None and cand.adv_usd is not None:
            adv_cap = adv_frac * cand.adv_usd
            if adv_cap < wanted * (1.0 - 1e-9) and notional <= adv_cap * (1.0 + 1e-9):
                stats.adv_capped_entries += 1
                if stats.first_adv_cap_time is None:
                    stats.first_adv_cap_time = trade.entry_time
                    stats.first_adv_cap_equity = cash
        risk_amount = abs(trade.entry_price - cand.stop_price) * trade.quantity
        open_by_cell[cell.key] = _OpenBookPosition(
            cell=cell.key,
            trade=trade,
            exit_time=cand.exit_time,
            notional=notional,
            risk_amount=risk_amount,
            # WAN-323: 부분 청산이 있으면 그 시각에 명목·리스크를 덜어내는 이벤트를 예약한다.
            reductions=_reductions_for(trade, len(cand.partial_exits), notional, risk_amount),
        )
        trades.append(trade)
        stats.placed += 1
        stats.placed_records.append(
            PlacedSetup(
                cell=cell.key,
                equity=cash,
                risk_amount=risk_amount,
                realized_pnl=trade.realized_pnl,
            )
        )
        _observe(stats, cand.entry_time, cash, open_by_cell, book, stress_risk_multiple)

    settle_due(_MAX_TIME)
    return BookOutcome(trades=trades, stats=stats, effective_config=eff_cfg)


def _observe(
    stats: BookStats,
    time: int,
    cash: float,
    open_by_cell: dict[CellKey, _OpenBookPosition],
    book: LeverageBookParams,
    stress_risk_multiple: float = 1.0,
) -> None:
    """진입 직후의 북 상태를 계측하고 최악 가정 청산을 검사한다(WAN-103 결정 4 재사용).

    최악 가정: 열린 포지션이 **전부 동시에** 손절까지 간다. 각 포지션은 손절에 닿는
    순간 청산되므로 손절 거리가 최대 역행폭이다 — 실제 가격 경로를 몰라도 참인 상한이다.
    이 이벤트가 있다고 백테스트 자본이 실제로 전멸했다는 뜻은 **아니지만**, 그 배수는
    구조적으로 마진콜 사거리 안에 있다는 신호다(발생 건수가 판정 열).

    ⚠️ 그 「손절 거리가 최대 역행폭」은 **손절 한 번 = 정확히 1R 체결**이라는 가정이다 —
    `stress_risk_multiple=k`(WAN-312)를 주면 한 포지션의 최악 손실을 `k × risk_amount`로
    보고 같은 검사를 다시 한다(계획 리스크 열은 불변, 실효 리스크 열이 따로 나온다).
    """
    concurrency = len(open_by_cell)
    if concurrency > stats.peak_concurrency:
        stats.peak_concurrency = concurrency
        stats.peak_concurrency_time = time

    open_notional = sum(p.notional for p in open_by_cell.values())
    total_risk = sum(p.risk_amount for p in open_by_cell.values())
    # k=1.0이면 `× 1.0`이 항등이라 아래 두 열·청산 검사가 전부 예전 값과 비트 일치한다.
    effective_risk = total_risk * stress_risk_multiple
    if cash > 0:
        stats.max_open_notional_ratio = max(stats.max_open_notional_ratio, open_notional / cash)
        stats.max_concurrent_risk_ratio = max(stats.max_concurrent_risk_ratio, total_risk / cash)
        stats.max_effective_concurrent_risk_ratio = max(
            stats.max_effective_concurrent_risk_ratio, effective_risk / cash
        )

    worst_equity = cash - effective_risk
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
