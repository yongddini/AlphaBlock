"""컨플루언스 전략 성과 평가 & 파라미터 스윕 (WAN-19).

WAN-23 재설계 컨플루언스 전략(`strategy.confluence`)을 WAN-8 백테스트 엔진
(`backtest.engine`)에 태워 **엔드투엔드 성과 리포트**를 만들고, 핵심 파라미터를
소규모 그리드로 스윕해 비교표를 생성한다. 결과는 재현을 위해 심볼·타임프레임·기간·
시드·파라미터를 함께 기록한다.

## 평가 대상 전략 (WAN-23, TF별 자기완결)

- **진입** = 활성(비-breaker) 오더블록 첫 탭 + RSI 게이트(롱=과매도, 숏=과매수).
- **익절** = 진입가 너머 가장 가까운 EMA/VWMA 선 도달(동적, 전량 청산).
- **손절** = 진입 근거 오더블록의 무효화(breaker, distal 경계 이탈).
- **동시봉 손절 우선**, **TF당 동시 1포지션**(피라미딩·역전 없음)은 전략·엔진이
  이미 보장한다(`ConfluenceStrategy`가 진입가 기준으로 청산을 미리 계산해
  `planned_exit`로 실어 보내고, `BacktestEngine`이 한 번에 한 포지션만 보유).

익절·손절이 모두 전략의 동적 규칙(`planned_exit`)으로 결정되므로, 백테스트 설정의
고정 %손절·익절 배수는 이 전략의 성과에 관여하지 않는다(계획 청산이 있는 포지션은
고정 %경로를 타지 않는다). 따라서 손절·익절 배수는 스윕 축이 아니다.

## 구성

- `evaluate()` — 하나의 (파라미터 조합, OHLCV)로 컨플루언스 → 백테스트를 실행해
  `BacktestResult`를 반환한다.
- `ParamGrid` / `SweepPoint` — 스윕 축(그리드) 정의. WAN-23은 지표·청산 규칙이
  고정이라 자유도가 낮으므로, **진입 RSI 임계값 한 축만** 소규모로 스윕한다
  (기본 3점). 과매도는 과매수에 대칭(`100 - overbought`)으로 유도한다.
- `run_sweep()` — 그리드의 모든 조합을 실행해 정렬된 `SweepReport`를 반환한다.
- `SweepReport` — 조합별 성과 행(`SweepRunRow`)을 담고, DataFrame·CSV·비교표
  문자열·추천 기본값(best)을 제공한다.

## 재현성

각 행은 심볼·타임프레임·기간(`start_time`/`end_time`/`num_bars`)·시드·스윕
파라미터를 포함한다. 샤프 지수는 타임프레임에서 유도한 연율화 계수
(`bars_per_year`)로 연율화한다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, BacktestResult
from data.models import FundingRate
from strategy.confluence import ConfluenceStrategy
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 타임프레임 → 밀리초 / 연율화 계수
# --------------------------------------------------------------------------- #

_MINUTE_MS = 60_000
_YEAR_MS = 365 * 24 * 60 * 60 * 1000

# 지원 타임프레임(분 단위)의 밀리초 길이.
_TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "3d": 4320,
    "1w": 10080,
}


def timeframe_to_ms(timeframe: str) -> int:
    """타임프레임 문자열(예: ``"1h"``)을 봉 간격(ms)으로 변환한다."""
    try:
        return _TIMEFRAME_MINUTES[timeframe] * _MINUTE_MS
    except KeyError as exc:
        supported = ", ".join(_TIMEFRAME_MINUTES)
        raise ValueError(f"지원하지 않는 타임프레임: {timeframe!r} (지원: {supported})") from exc


def bars_per_year(timeframe: str) -> float:
    """타임프레임의 연간 봉 수(샤프 연율화 계수)를 반환한다."""
    return _YEAR_MS / timeframe_to_ms(timeframe)


# --------------------------------------------------------------------------- #
# 단일 평가: 컨플루언스 → 백테스트
# --------------------------------------------------------------------------- #


def evaluate(
    df: pd.DataFrame,
    *,
    confluence_params: ConfluenceParams | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    order_block_result: OrderBlockResult | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
) -> BacktestResult:
    """컨플루언스 시그널을 생성하고 백테스트 엔진으로 시뮬레이션해 결과를 반환한다.

    WAN-23 `ConfluenceStrategy`가 만든 확정 진입(`order_block_signals`)을 WAN-8
    `BacktestEngine`에 그대로 전달한다. 진입 각각에는 전략이 미리 계산한 청산
    (`planned_exit`: EMA/VWMA 선 도달 익절 또는 오더블록 무효화 손절)이 실려 있어,
    엔진은 고정 %TP/SL이 아니라 그 계획대로 청산한다.

    `order_block_result`를 주면 오더블록 탐지를 재실행하지 않고 재사용한다(스윕에서
    동일 오더블록에 대해 여러 파라미터를 평가할 때 탐지 중복을 피한다). 오더블록
    탐지는 `confluence_params`와 무관하므로 이 재사용은 결과를 바꾸지 않는다.

    `funding_rates`(WAN-20)를 주고 `backtest_config.funding_enabled=True`이면 엔진이
    보유 구간의 펀딩비를 손익에 반영한다. 그래야 스윕/워크포워드의 파라미터 선택과
    성과 평가가 실거래(WAN-9) 손익 모델(수수료·슬리피지·펀딩비)과 일관된다.
    """
    strategy = ConfluenceStrategy(confluence_params, order_block_params)
    confluence = strategy.run(df, order_block_result)
    engine = BacktestEngine(backtest_config)
    return engine.run(df, confluence.order_block_signals, funding_rates)


# --------------------------------------------------------------------------- #
# 파라미터 그리드
# --------------------------------------------------------------------------- #


class SweepPoint(BaseModel):
    """스윕 그리드의 한 셀(파라미터 조합).

    WAN-23은 지표(RSI14·EMA·VWMA)와 청산 규칙(선 도달 익절·오더블록 무효화 손절)이
    고정이라 튜닝 자유도가 낮다. 유일하게 남는 저-자유도 진입 노브인 **RSI 게이트
    임계값** 한 축만 담는다. 과매도 임계값은 과매수에 대칭(``100 - overbought``)으로
    유도한다.
    """

    model_config = ConfigDict(frozen=True)

    rsi_overbought: float

    @property
    def rsi_oversold(self) -> float:
        """과매수 임계값에 대칭인 과매도 임계값."""
        return 100.0 - self.rsi_overbought


@dataclass(frozen=True)
class ParamGrid:
    """스윕 축 정의. WAN-23 확정 설계상 지표·청산이 고정이므로 축을 최소화한다.

    기본 그리드는 진입 RSI 임계값 한 축(3점)뿐이다. 과매도는 대칭 유도이므로
    실질 스윕 대상은 `rsi_overbought` 하나다.
    """

    rsi_overbought: Sequence[float] = (70.0, 75.0, 80.0)
    """RSI 게이트 과매수 임계값 후보. 과매도는 ``100 - overbought``로 대칭 유도."""

    def points(self) -> Iterator[SweepPoint]:
        """그리드의 모든 조합을 `SweepPoint`로 열거한다(결정적 순서)."""
        for overbought in self.rsi_overbought:
            yield SweepPoint(rsi_overbought=overbought)

    @property
    def size(self) -> int:
        """총 조합 수."""
        return len(self.rsi_overbought)


def apply_sweep_point(base_confluence: ConfluenceParams, point: SweepPoint) -> ConfluenceParams:
    """기준 컨플루언스 파라미터에 스윕 셀의 RSI 임계값을 덮어써 반환한다.

    지표·청산 규칙은 WAN-23 확정 설계대로 고정이므로 RSI 게이트 임계값만 바꾼다.
    백테스트 설정은 손대지 않는다(고정 %손절·익절은 이 전략의 계획 청산에
    관여하지 않으므로 스윕 대상이 아니다).
    """
    return base_confluence.model_copy(
        update={
            "rsi_overbought": point.rsi_overbought,
            "rsi_oversold": point.rsi_oversold,
        }
    )


# --------------------------------------------------------------------------- #
# 스윕 결과
# --------------------------------------------------------------------------- #


class SweepRunRow(BaseModel):
    """스윕 한 조합의 식별자 + 성과 지표 (한 행)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    start_time: int | None
    end_time: int | None
    num_bars: int
    # --- 스윕 파라미터 ---
    rsi_oversold: float
    rsi_overbought: float
    # --- 성과 지표 ---
    total_return: float
    max_drawdown: float
    win_rate: float
    profit_factor: float | None
    sharpe: float | None
    num_trades: int
    seed: int


# CSV/DataFrame 컬럼 순서.
_ROW_COLUMNS = [
    "symbol",
    "timeframe",
    "start_time",
    "end_time",
    "num_bars",
    "rsi_oversold",
    "rsi_overbought",
    "total_return",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "sharpe",
    "num_trades",
    "seed",
]

# 정렬 가능한(높을수록 좋은) 성과 지표.
_SORTABLE_METRICS = ("sharpe", "total_return", "win_rate", "profit_factor", "num_trades")


def _sort_key(row: SweepRunRow, metric: str) -> tuple[float, float, float]:
    """정렬 키: (기준 지표, 총수익률, -MDD). None은 -inf로 취급(항상 하위)."""
    primary = getattr(row, metric)
    primary_val = float("-inf") if primary is None else float(primary)
    return (primary_val, row.total_return, -row.max_drawdown)


class SweepReport(BaseModel):
    """`run_sweep()`의 반환값. 조합별 성과 행과 정렬 기준을 담는다."""

    model_config = ConfigDict(frozen=True)

    rows: list[SweepRunRow]
    sort_by: str

    def to_dataframe(self) -> pd.DataFrame:
        """성과 행을 DataFrame으로 (`_ROW_COLUMNS` 순서)."""
        records = [row.model_dump() for row in self.rows]
        return pd.DataFrame(records, columns=_ROW_COLUMNS)

    def best(self) -> SweepRunRow | None:
        """정렬 기준 최상위 조합. 행이 없으면 None."""
        return self.rows[0] if self.rows else None

    def to_table(self) -> str:
        """정렬된 비교표 문자열(콘솔용)."""
        header = (
            f"{'rsi_os':>6} {'rsi_ob':>6} "
            f"{'return%':>9} {'mdd%':>7} {'win%':>6} {'pf':>6} {'sharpe':>8} {'trades':>7}"
        )
        lines = [f"=== Parameter Sweep (sorted by {self.sort_by}) ===", header, "-" * len(header)]
        for row in self.rows:
            pf = "N/A" if row.profit_factor is None else f"{row.profit_factor:.2f}"
            sharpe = "N/A" if row.sharpe is None else f"{row.sharpe:.2f}"
            lines.append(
                f"{row.rsi_oversold:>6.0f} {row.rsi_overbought:>6.0f} "
                f"{row.total_return * 100:>9.2f} {row.max_drawdown * 100:>7.2f} "
                f"{row.win_rate * 100:>6.1f} {pf:>6} {sharpe:>8} {row.num_trades:>7}"
            )
        return "\n".join(lines)


def _build_row(
    result: BacktestResult,
    *,
    symbol: str,
    timeframe: str,
    point: SweepPoint,
    start_time: int | None,
    end_time: int | None,
    num_bars: int,
) -> SweepRunRow:
    m = result.metrics
    return SweepRunRow(
        symbol=symbol,
        timeframe=timeframe,
        start_time=start_time,
        end_time=end_time,
        num_bars=num_bars,
        rsi_oversold=point.rsi_oversold,
        rsi_overbought=point.rsi_overbought,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        win_rate=m.win_rate,
        profit_factor=m.profit_factor,
        sharpe=m.sharpe,
        num_trades=m.num_trades,
        seed=result.config.seed,
    )


def run_sweep(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    grid: ParamGrid | None = None,
    base_confluence: ConfluenceParams | None = None,
    base_backtest: BacktestConfig | None = None,
    order_block_params: OrderBlockParams | None = None,
    funding_rates: Sequence[FundingRate] | None = None,
    sort_by: str = "sharpe",
) -> SweepReport:
    """그리드의 모든 파라미터 조합을 백테스트해 정렬된 `SweepReport`를 반환한다.

    `base_backtest`가 없으면 타임프레임에서 유도한 연율화 계수로 샤프를 연율화한다.
    `sort_by`는 높을수록 좋은 지표여야 한다(`_SORTABLE_METRICS`).

    `funding_rates`(WAN-20)는 모든 조합 평가에 그대로 전달된다. `base_backtest.
    funding_enabled=True`이면 각 조합의 손익에 펀딩비가 반영되어, 펀딩비를 뺀 손익이
    아니라 실거래 조건과 일관된 손익으로 파라미터가 선택·비교된다.
    """
    if sort_by not in _SORTABLE_METRICS:
        raise ValueError(f"sort_by는 {_SORTABLE_METRICS} 중 하나여야 합니다: {sort_by!r}")

    grid = grid or ParamGrid()
    base_conf = base_confluence or ConfluenceParams()
    base_bt = base_backtest or BacktestConfig(annualization_factor=bars_per_year(timeframe))

    num_bars = len(df)
    start_time = int(df["open_time"].min()) if num_bars else None
    end_time = int(df["open_time"].max()) if num_bars else None

    # 오더블록 탐지는 컨플루언스 파라미터와 무관하므로 그리드 전체에서 한 번만 실행.
    ob_result = OrderBlockDetector(order_block_params).run(df)

    rows: list[SweepRunRow] = []
    for point in grid.points():
        confluence = apply_sweep_point(base_conf, point)
        result = evaluate(
            df,
            confluence_params=confluence,
            order_block_params=order_block_params,
            backtest_config=base_bt,
            order_block_result=ob_result,
            funding_rates=funding_rates,
        )
        rows.append(
            _build_row(
                result,
                symbol=symbol,
                timeframe=timeframe,
                point=point,
                start_time=start_time,
                end_time=end_time,
                num_bars=num_bars,
            )
        )

    rows.sort(key=lambda r: _sort_key(r, sort_by), reverse=True)
    return SweepReport(rows=rows, sort_by=sort_by)


def write_sweep_csv(report: SweepReport, path: str | Path) -> Path:
    """스윕 비교표를 CSV로 저장하고 경로를 반환한다."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    report.to_dataframe().to_csv(out, index=False)
    return out


@dataclass(frozen=True)
class MultiSweepReport:
    """여러 (심볼, 타임프레임)에 걸친 스윕 결과 묶음."""

    reports: list[SweepReport] = field(default_factory=list)

    def combined_dataframe(self) -> pd.DataFrame:
        """모든 리포트의 행을 하나의 DataFrame으로 이어붙인다."""
        frames = [r.to_dataframe() for r in self.reports]
        if not frames:
            return pd.DataFrame(columns=_ROW_COLUMNS)
        return pd.concat(frames, ignore_index=True)
