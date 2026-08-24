"""WAN-366: 인과 엔진 위에서 부품을 다시 분해한다 — 「이제 다시 해봐야지」의 1단계.

## 한 줄

WAN-365가 소급 취소를 인과로 바꿨는데 **지금 채택된 값들은 전부 그 결함 엔진의 표를 비교해
골라진 것**이다. 이 모듈은 그중 **후보 집합 층**(볼린저 · 존폭 필터 · 손절폭 가드 · 재진입)을
사다리로 다시 세워 **인과 엔진에서 아직도 값을 더하는 부품이 있는가** 하나를 묻는다.

## §0 — 먼저 싸게: 지워진 탭이 존폭에 치우쳐 있나 (탐지 층 · 1분봉 안 읽음)

WAN-364 §2 인구조사는 「무효화 봉에서 난 탭」이 전체의 몇 %인지만 냈고 **존폭별로 안
쪼갰다**. 그런데 존폭 필터가 고르는 것은 **좁은 존**이고, 존이 좁다 = 손절선이 진입가에
가깝다 = **가격이 조금만 더 내려가도 존이 깨진다.** 소급 취소가 지운 것이 정확히 「그 봉
안에서 존이 깨진 탭」이므로 **좁은 존일수록 버그가 더 많이 보호해 줬을 구조**다.

* 좁은 존의 무효화 봉 탭 비율이 넓은 존보다 **높으면** 필터의 측정된 우위(WAN-142/152/154/
  203)가 그만큼 **버그에 업혀 있었다**는 직접 증거다.
* 존폭에 **평평하면** 오염은 공통이고 필터 **고유의** 문제는 아니다.

⚠️ **이 표는 채택 근거가 아니다** — 탐지 층 인구조사라 손익을 안 재고 사다리를 대체하지도
않는다. **사다리를 어떻게 읽을지를 미리 정해 주는 것**이 쓸모다.

🚨 **손절폭 가드(0.3%)도 같은 축이다** — WAN-328이 「존폭 필터와 손절폭 가드는 **같은 양을
두 자로 잰다**」고 적어 뒀다(둘 다 존의 두께를 재고 저변동성 구간에서 겹친다).

## §1 — 사다리 다섯 단 (채택 북)

| 단 | 무엇을 켜나 | 볼린저 | 존폭 1.28 | 가드 0.3% | 재진입 |
| -- | -- | -- | -- | -- | -- |
| `L0` | 존 단독 | 끔 | 끔 | 끔 | 끔 |
| `L1` | ＋ 볼린저(진입가 재산정) | **켬** | 끔 | 끔 | 끔 |
| `L2` | ＋ 존폭 필터 | 켬 | **켬** | 끔 | 끔 |
| `L3` | ＋ 손절폭 가드 | 켬 | 켬 | **켬** | 끔 |
| `L4` | ＋ 재진입 ON(band) = **오늘의 채택 기본값** | 켬 | 켬 | 켬 | **켬** |

📌 **다섯 단인데 후보 생성은 세 번뿐이다** — 축 넷 중 둘만 후보를 바꾼다.

* **볼린저 · 존폭 필터**는 후보 생성 축이다(`run_cells(bollinger=, max_zone_width_atr=)`).
* **손절폭 가드**는 **사이징** 축이라 같은 후보를 다시 배치하면 된다
  (`build_book_rows(min_stop_distance_fraction=)`) — wan76 §3·WAN-197이 쓰던 성질이다.
* **재진입**은 payload에 **별도 dict**로 실려 배치에서 켜고 끈다(`include_reentry`, WAN-261).

그래서 `L2`·`L3`·`L4`가 **한 번의 후보 생성**을 나눠 쓰고, 그 생성이 곧 **채택 북**이다
(검산 (a)가 그것을 못 박는다).

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 4TF(15m·1h·2h·4h) 한 지갑 · 못 박은 6년 창 · cap_only
5배 · 오프셋 2bp · 유동성 한도 채택값 · `baseline` 렌즈. **취소 시점은 인과**(인자를 안 주면
`ConfluenceParams().invalidation_cancel == "bar_close"`, WAN-365) — 이 표의 전부다.

🚨 **판단은 북에서 낸다**(WAN-341). per-cell 격리 행도 CSV에 남지만 **탐색·귀속 진단용**이고
채택 근거가 아니다.

## 검산

* **(a) `L4` ≡ 인자 없는 채택 북** — `wan336.verify_adopted_identity`(펀딩 대리 무동작) +
  `book_cli.build_book_rows` 기본 인자 행과의 대조. 이 등식이 서면 사다리 꼭대기가 실제로
  오늘 페이퍼가 뛰는 그 규칙이다.
* **(b) `L2`·`L3`가 같은 후보를 본다** — 가드는 사이징 축이라 후보 수가 **같아야** 한다.
  다르면 가드가 후보 생성에 샌 것이다.
* **(c) 조인 문턱의 부분집합** — `L2`의 셋업이 `L1`의 **부분집합**이다(개수가 아니라 집합으로
  — 개수만 보면 같은 개수의 다른 셋업이 통과한다, WAN-161 선례).

재현:

```
uv run python -m backtest.wan366_causal_ablation --census-only              # §0만(싸다)
uv run python -m backtest.wan366_causal_ablation --rungs L2,L3,L4 --jobs 4  # 채택 생성 먼저
uv run python -m backtest.wan366_causal_ablation --rungs L1 --jobs 4 --append
uv run python -m backtest.wan366_causal_ablation --rungs L0 --jobs 4 --append
uv run python -m backtest.wan366_causal_ablation --from-csv                 # 요약만
```
"""

from __future__ import annotations

import argparse
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, _segment_cells, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS, verify_adopted_identity
from execution.sizing import PositionSizingParams
from strategy.indicators import atr
from strategy.models import ConfluenceParams

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan366_causal_ablation.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan366_causal_ablation_loo.csv"
CENSUS_CSV_PATH = REPORTS_DIR / "wan366_break_bar_by_width.csv"
SUMMARY_PATH = REPORTS_DIR / "wan366_causal_ablation_summary.md"

#: 파괴선 — MDD가 이 선을 넘으면 「청산 0건」이라도 계좌는 사실상 끝났다(WAN-312 §4).
RUIN_MDD = 0.50

#: 채택 존폭 문턱(WAN-159). §0의 「좁다/넓다」 경계이자 사다리 `L2`가 켜는 값이다.
ADOPTED_ZONE_WIDTH = 1.28

#: 채택 손절폭 가드(WAN-79). 사다리 `L3`가 켜는 값이고 `0.0`이 끔이다.
ADOPTED_STOP_GUARD = 0.003

#: §0 존폭 버킷 경계(존폭 ÷ ATR14). **채택 문턱 1.28이 경계에 놓이는 것**이 요점 —
#: 그래야 「필터가 사는 쪽」과 「버리는 쪽」이 표에서 갈라진다.
WIDTH_EDGES: tuple[float, ...] = (0.6, 0.9, ADOPTED_ZONE_WIDTH, 1.8, 2.6)

CSV_KEYS: tuple[str, ...] = ("level", "segment")
LOO_CSV_KEYS: tuple[str, ...] = ("level", "segment", "excluded")
CENSUS_KEYS: tuple[str, ...] = ("symbol", "timeframe", "bucket")

_FLOOR_NOTE = "6년 MDD는 2018·2020-03 폭락을 **포함하지 않는** 창이라 천장이 아니라 **바닥선**이다"


# --------------------------------------------------------------------------- #
# 사다리 정의
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rung:
    """사다리 한 단.

    `gen`은 **후보 생성 그룹**이다 — 같은 `gen`의 단들은 후보를 한 번만 만들고 배치에서
    갈린다(가드·재진입은 후보를 안 바꾼다). 이 필드가 이 모듈의 컴퓨트 설계 전부다.
    """

    name: str
    adds: str
    gen: str
    bollinger: bool
    zone_width: float | None
    guard: float
    reentry: bool

    @property
    def is_adopted(self) -> bool:
        """이 단이 **인자 없는 채택 북** 그 자체인가 — 검산 (a)를 걸 수 있는 유일한 단."""
        return (
            self.bollinger
            and self.zone_width == ADOPTED_ZONE_WIDTH
            and self.guard == ADOPTED_STOP_GUARD
            and self.reentry
        )


RUNGS: tuple[Rung, ...] = (
    Rung("L0", "존 단독(볼린저·필터·가드·재진입 전부 끔)", "G0", False, None, 0.0, False),
    Rung("L1", "＋ 볼린저 진입가 재산정", "G1", True, None, 0.0, False),
    Rung("L2", f"＋ 존폭 필터 {ADOPTED_ZONE_WIDTH}", "G2", True, ADOPTED_ZONE_WIDTH, 0.0, False),
    Rung(
        "L3",
        f"＋ 손절폭 가드 {ADOPTED_STOP_GUARD:.1%}",
        "G2",
        True,
        ADOPTED_ZONE_WIDTH,
        ADOPTED_STOP_GUARD,
        False,
    ),
    Rung(
        "L4",
        "＋ 재진입 ON(band) = 오늘의 채택 기본값",
        "G2",
        True,
        ADOPTED_ZONE_WIDTH,
        ADOPTED_STOP_GUARD,
        True,
    ),
)
RUNGS_BY_NAME: dict[str, Rung] = {r.name: r for r in RUNGS}
LADDER: tuple[str, ...] = tuple(r.name for r in RUNGS)
BASE_RUNG = "L0"
ADOPTED_RUNG = "L4"


def generation_of(gen: str) -> tuple[Rung, ...]:
    """이 생성 그룹이 먹이는 단들(생성 1회 → 배치 N회)."""
    return tuple(r for r in RUNGS if r.gen == gen)


def rungs_to_generations(names: Sequence[str]) -> tuple[str, ...]:
    """요청한 단들을 덮는 생성 그룹 — 순서는 `RUNGS` 정의 순이다."""
    wanted = {RUNGS_BY_NAME[n].gen for n in names}
    seen: list[str] = []
    for rung in RUNGS:
        if rung.gen in wanted and rung.gen not in seen:
            seen.append(rung.gen)
    return tuple(seen)


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class WidthCensusRow(BaseModel):
    """§0 — 존폭 버킷 하나의 「무효화 봉에서 난 탭」 비율(탐지 층만)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    bucket: str
    bucket_lo: float
    bucket_hi: float
    narrow: bool
    """채택 문턱(1.28) 이하인가 — 필터가 **사는** 쪽인가."""
    active_taps: int
    """무효화 **전** 봉에서 난 탭 — 소급 취소 엔진에서도 후보가 되던 시그널."""
    break_bar_taps: int
    """무효화 **봉에서** 난 탭 — 소급 취소가 통째로 버리던 시그널."""
    break_bar_share: float


class LadderRow(BaseModel):
    """한 (단, 구간)의 북 집계 — 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    level: str
    adds: str
    generation: str
    bollinger: bool
    zone_width_atr: float | None
    stop_guard: float
    reentry: bool
    segment: str

    num_cells: int
    num_candidates: int
    """이 배치가 받은 후보 총수 — 검산 (b)(가드가 후보를 안 바꾼다)의 자."""
    num_trades: int
    win_rate: float
    total_return: float
    """⚠️ 6년 복리라 실현 수익이 아니다(WAN-169/213) — 아래 거래당 net R과 나란히 읽는다."""
    mean_net_r: float
    """거래당 실현 net R = 복리와 무관한 「실력」(WAN-154 `mean_net_r`와 같은 자)."""
    net_r: float
    profit_factor: float | None

    max_drawdown: float
    return_over_mdd: float | None
    ruin: bool
    peak_concurrency: int
    max_concurrent_risk: float
    liquidation_events: int
    reentry_trades: int


class LadderLooRow(BaseModel):
    """종목 하나를 뺀 **지갑 재배치**(라벨 필터가 아니다 — WAN-316 스코프 패턴)."""

    model_config = ConfigDict(frozen=True)

    level: str
    segment: str
    excluded: str
    num_trades: int
    total_return: float
    max_drawdown: float
    mean_net_r: float


# --------------------------------------------------------------------------- #
# §0 — 탐지 층 인구조사 (싸다)
# --------------------------------------------------------------------------- #


def bucket_label(ratio: float) -> tuple[str, float, float]:
    """존폭÷ATR를 버킷 라벨·경계로 — 경계는 `WIDTH_EDGES`이고 1.28이 그중 하나다."""
    lo = 0.0
    for edge in WIDTH_EDGES:
        if ratio <= edge:
            return (f"({lo:.2f}, {edge:.2f}]", lo, edge)
        lo = edge
    return (f"({lo:.2f}, ∞)", lo, math.inf)


def census_cell(symbol: str, timeframe: str, *, start_ms: int, end_ms: int) -> list[WidthCensusRow]:
    """한 칸의 존폭별 「무효화 봉 탭」 — 1분봉을 읽지 않는다(탐지 층에서 끝난다).

    존폭÷ATR는 **엔진이 필터에 쓰는 그 값**이다: `(top − bottom) ÷ ATR14[pos−1]`(탭 봉
    **직전 확정봉** — 탭 봉 자신의 ATR은 그 봉 종가를 알아야 나오므로 룩어헤드다).
    라벨이 아니라 같은 산식을 쓰는 것이 요점 — 다른 자로 재면 이 표가 필터를 설명하지 못한다.
    """
    market = harness.load_market_data(
        harness.normalize_symbol(symbol),
        timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=False,
        funding=False,
    )
    if market.empty:
        return []
    result = harness.detect_order_blocks(market)
    frame = market.htf_df
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    frame = frame.sort_values("open_time").reset_index(drop=True)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    time_to_pos = {t: i for i, t in enumerate(times)}
    length = ConfluenceParams().zone_width_atr_length
    atr_vals = [float(v) for v in atr(frame, length=length).tolist()]

    tallies: dict[str, list[int]] = {}
    bounds: dict[str, tuple[float, float]] = {}
    for signal in result.retap_signals:
        if signal.status not in ("active", "cancelled"):
            continue
        pos = time_to_pos.get(signal.trigger_time)
        if pos is None or pos < 1:
            continue
        atr_value = atr_vals[pos - 1]
        if math.isnan(atr_value) or atr_value <= 0.0:
            # 엔진은 판정 불가를 **기각**한다(WAN-158) — 그 부류를 지우지 않고 따로 센다.
            label, lo, hi = ("판정불가(ATR 워밍업)", math.nan, math.nan)
        else:
            ob = signal.order_block
            label, lo, hi = bucket_label((ob.top - ob.bottom) / atr_value)
        bounds.setdefault(label, (lo, hi))
        tally = tallies.setdefault(label, [0, 0])
        tally[0 if signal.status == "active" else 1] += 1

    rows: list[WidthCensusRow] = []
    for label, (active, broken) in tallies.items():
        lo, hi = bounds[label]
        total = active + broken
        rows.append(
            WidthCensusRow(
                symbol=harness.normalize_symbol(symbol),
                timeframe=timeframe,
                bucket=label,
                bucket_lo=lo,
                bucket_hi=hi,
                narrow=bool(hi <= ADOPTED_ZONE_WIDTH),
                active_taps=active,
                break_bar_taps=broken,
                break_bar_share=broken / total if total else 0.0,
            )
        )
    return sorted(rows, key=lambda r: math.inf if math.isnan(r.bucket_lo) else r.bucket_lo)


def run_census(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    log: bool = True,
) -> list[WidthCensusRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    rows: list[WidthCensusRow] = []
    for symbol in symbols:
        for timeframe in timeframes:
            cell = census_cell(symbol, timeframe, start_ms=start_ms, end_ms=end_ms)
            if not cell:
                if log:
                    print(f"[wan366] {symbol} {timeframe}: 데이터 없음 — 건너뜀", flush=True)
                continue
            rows.extend(cell)
    return rows


# --------------------------------------------------------------------------- #
# §1 — 사다리 (후보 생성 3회 → 배치 5회)
# --------------------------------------------------------------------------- #


def _assert_adopted_base() -> None:
    """이 모듈의 라벨이 **오늘의 채택 기본값**과 같은지 — 어긋나면 시끄럽게 죽는다.

    사다리 꼭대기가 「오늘의 채택 기본값」이라는 주장은 상수 세 개에 걸려 있다. 기본값이
    움직이면 라벨만 남고 표가 다른 엔진을 가리키므로(WAN-91/95/112/123/159가 반복해 경계한
    자리) 여기서 **동작으로** 막는다.
    """
    params = ConfluenceParams()
    if params.max_zone_width_atr != ADOPTED_ZONE_WIDTH:
        raise AssertionError(
            f"채택 존폭 문턱이 {params.max_zone_width_atr}로 바뀌었습니다 — 이 모듈의 "
            f"`ADOPTED_ZONE_WIDTH`({ADOPTED_ZONE_WIDTH})와 라벨을 함께 고치세요."
        )
    if params.invalidation_cancel != "bar_close":
        raise AssertionError(
            f"취소 시점 기본값이 {params.invalidation_cancel!r}입니다 — 이 표는 **인과 엔진** "
            "위의 사다리라(WAN-365) 소급 취소가 기본이면 제목이 거짓이 됩니다."
        )
    guard = PositionSizingParams().min_stop_distance_fraction
    if guard != ADOPTED_STOP_GUARD:
        raise AssertionError(
            f"채택 손절폭 가드가 {guard}로 바뀌었습니다 — `ADOPTED_STOP_GUARD`와 라벨을 "
            "함께 고치세요."
        )


def generation_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    gen: str,
    *,
    start: str,
    end: str,
    jobs: int,
) -> list[CellPayload]:
    """생성 그룹 하나의 후보 — 이 그룹의 단들이 **같은 후보**를 나눠 쓴다.

    ⚠️ `engine_check`는 **채택 단이 있는 그룹에서만** 켠다 — 그 검산은 격리 성과가
    `harness.run_once`(사다리 축이 없는 per-cell)와 비트 일치하는지 보는 것이라, 축을 켠
    그룹에서는 **당연히** 어긋난다(WAN-336/346/364 관행 그대로).
    """
    rungs = generation_of(gen)
    if not rungs:
        raise ValueError(f"모르는 생성 그룹: {gen!r}")
    head = rungs[0]
    kwargs = dict(ADOPTED_CELL_KWARGS)
    kwargs["reentry"] = any(r.reentry for r in rungs)
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        engine_check=any(r.is_adopted for r in rungs),
        bollinger=head.bollinger,
        # 채택 문턱은 **센티넬로 물려받는다**(핀이 아니다) — `_assert_adopted_base`가 그
        # 값이 1.28임을 동작으로 고정하므로 라벨과 엔진이 갈라질 수 없다.
        max_zone_width_atr=(
            harness.UNSET if head.zone_width == ADOPTED_ZONE_WIDTH else head.zone_width
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def _guard_arg(rung: Rung) -> float | None:
    """배치에 넘길 가드 — 채택값이면 `None`(손대지 않는다)이라 비트 재현된다."""
    return None if rung.guard == ADOPTED_STOP_GUARD else rung.guard


def place_rung(
    payloads: Sequence[CellPayload],
    rung: Rung,
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
) -> list[BookSegment]:
    """한 단의 채택 북 배치 — 후보는 그룹이 이미 만들었다(가드·재진입만 여기서 갈린다)."""
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=rung.reentry,
        min_stop_distance_fraction=_guard_arg(rung),
    )


def _candidate_total(payloads: Sequence[CellPayload], segment: str, *, reentry: bool) -> int:
    """이 배치가 받는 후보 총수 — 검산 (b)(가드는 후보를 안 바꾼다)의 자."""
    return sum(
        len(cell.candidates)
        for cell in _segment_cells(payloads, segment, "", include_reentry=reentry)
    )


def _to_row(*, rung: Rung, segment: BookSegment, payloads: Sequence[CellPayload]) -> LadderRow:
    row = segment.row
    pairs: list[tuple[Trade, PlacedSetup]] = segment.trades_with_placements()
    rs = [net_r(t, p) for t, p in pairs]
    return LadderRow(
        level=rung.name,
        adds=rung.adds,
        generation=rung.gen,
        bollinger=rung.bollinger,
        zone_width_atr=rung.zone_width,
        stop_guard=rung.guard,
        reentry=rung.reentry,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_candidates=_candidate_total(payloads, segment.segment, reentry=rung.reentry),
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        mean_net_r=sum(rs) / len(rs) if rs else 0.0,
        net_r=sum(rs),
        profit_factor=segment.result.metrics.profit_factor,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        ruin=row.max_drawdown >= RUIN_MDD,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        liquidation_events=row.liquidation_events,
        reentry_trades=sum(1 for _t, p in pairs if p.is_reentry),
    )


def _loo_rows(
    *,
    rung: Rung,
    payloads: Sequence[CellPayload],
    symbols: Sequence[str],
    start_ms: int,
    end_ms: int,
) -> list[LadderLooRow]:
    """종목을 하나씩 뺀 **지갑 재배치** — 후보는 이미 있으니 배치만 다시 한다.

    🚨 라벨 필터가 아니다(WAN-316 스코프 패턴) — 종목을 빼면 그 칸이 잡던 자본·명목 자리가
    비어 **다른 칸이 그 자리를 쓴다**. 라벨로 거르면 그 재배치가 안 일어나 「빼도 그대로」라는
    잘못된 인상을 준다.
    """
    present = {p.symbol for p in payloads}
    unmatched = [s for s in symbols if s not in present]
    if present and unmatched:
        raise AssertionError(
            f"leave-one-out이 아무 칸도 빼지 못했습니다: {unmatched} — 심볼 표기가 "
            f"payload({sorted(present)[0]!r} 형식)와 어긋납니다."
        )
    proxied, _note = apply_funding_proxy(payloads)
    rows: list[LadderLooRow] = []
    for excluded in ("-", *symbols):
        scoped = [p for p in proxied if p.symbol != excluded]
        if not scoped:
            continue
        for seg in iter_book_segments(
            scoped,
            book=LeverageBookParams(),
            segments=(PRIMARY_OOS,),
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=rung.reentry,
            min_stop_distance_fraction=_guard_arg(rung),
        ):
            pairs = seg.trades_with_placements()
            rs = [net_r(t, p) for t, p in pairs]
            rows.append(
                LadderLooRow(
                    level=rung.name,
                    segment=seg.segment,
                    excluded=excluded,
                    num_trades=seg.row.num_trades,
                    total_return=seg.row.total_return,
                    max_drawdown=seg.row.max_drawdown,
                    mean_net_r=sum(rs) / len(rs) if rs else 0.0,
                )
            )
    return rows


def run_generation(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    gen: str,
    *,
    levels: Sequence[str],
    start: str,
    end: str,
    jobs: int,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> tuple[list[LadderRow], list[LadderLooRow]]:
    """생성 그룹 하나 — 후보를 **한 번** 만들고 그 그룹의 단들을 배치한다."""
    _assert_adopted_base()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    payloads = generation_payloads(symbols, timeframes, gen, start=start, end=end, jobs=jobs)
    wanted = [r for r in generation_of(gen) if r.name in levels]
    rows: list[LadderRow] = []
    loo: list[LadderLooRow] = []
    by_level: dict[str, list[LadderRow]] = {}
    for rung in wanted:
        if rung.is_adopted:
            identity = verify_adopted_identity(payloads, start_ms=start_ms, end_ms=end_ms)
            if log:
                print(f"[wan366] 검산(a) 채택 경로 최대차: {identity:.2e}", flush=True)
        placed = place_rung(payloads, rung, start_ms=start_ms, end_ms=end_ms, segments=segments)
        level_rows = [_to_row(rung=rung, segment=seg, payloads=payloads) for seg in placed]
        by_level[rung.name] = level_rows
        rows.extend(level_rows)
        loo.extend(
            _loo_rows(
                rung=rung,
                payloads=payloads,
                symbols=[harness.normalize_symbol(s) for s in symbols],
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
        if log:
            print(f"[wan366] {rung.name}({rung.adds}): {len(level_rows)}행", flush=True)
    _check_guard_axis(by_level)
    return rows, loo


def _check_guard_axis(by_level: dict[str, list[LadderRow]]) -> None:
    """검산 (b) — 가드는 **사이징** 축이라 후보 수를 못 바꾼다.

    `L2`와 `L3`는 같은 payload를 가드만 바꿔 배치한 것이라 `num_candidates`가 같아야 한다.
    다르면 가드가 후보 생성에 샌 것이고, 그러면 이 사다리의 컴퓨트 설계(생성 3회) 자체가
    틀린 것이다 — 라벨이 아니라 **동작**으로 잡는다.
    """
    left, right = by_level.get("L2"), by_level.get("L3")
    if not left or not right:
        return
    right_by_segment = {r.segment: r for r in right}
    for row in left:
        peer = right_by_segment.get(row.segment)
        if peer is not None and peer.num_candidates != row.num_candidates:
            raise AssertionError(
                f"검산(b) 실패 — {row.segment}에서 가드만 바꿨는데 후보 수가 달라졌습니다"
                f"({row.num_candidates} != {peer.num_candidates}). 가드는 사이징 축이라 "
                "후보를 바꿀 수 없습니다(WAN-197)."
            )


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    levels: Sequence[str] = LADDER,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    on_generation: Callable[[list[LadderRow], list[LadderLooRow]], None] | None = None,
    log: bool = True,
) -> tuple[list[LadderRow], list[LadderLooRow]]:
    """요청한 단들을 덮는 생성 그룹을 차례로 돈다.

    📌 그룹마다 즉시 적재한다(`on_generation`) — 그룹은 각자 독립 지갑이라 중간에 끊겨도
    끝난 그룹은 보존된다. **끊길 수 없는 것은 한 그룹 안의 4TF뿐이다**(북은 이어붙일 수
    없다 — WAN-316).
    """
    rows: list[LadderRow] = []
    loo: list[LadderLooRow] = []
    for gen in rungs_to_generations(levels):
        t0 = time.time()
        gen_rows, gen_loo = run_generation(
            symbols,
            timeframes,
            gen,
            levels=levels,
            start=start,
            end=end,
            jobs=jobs,
            segments=segments,
            log=log,
        )
        rows.extend(gen_rows)
        loo.extend(gen_loo)
        if on_generation is not None:
            on_generation(gen_rows, gen_loo)
        if log:
            print(f"[wan366] 생성 {gen}: {len(gen_rows)}행 ({time.time() - t0:.0f}s)", flush=True)
    return rows, loo


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[LadderRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def loo_to_frame(rows: Sequence[LadderLooRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def census_to_frame(rows: Sequence[WidthCensusRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _missing(value: object) -> bool:
    """`None`과 **NaN을 함께** 결측으로 본다 — CSV 왕복이 `None`을 NaN으로 바꾼다."""
    return value is None or (isinstance(value, float) and pd.isna(value))


def _pct(value: object) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:.2f}%"  # type: ignore[arg-type]


def _pp(value: object) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:+.2f}%p"  # type: ignore[arg-type]


def _num(value: object, digits: int = 4) -> str:
    return "—" if _missing(value) else f"{float(value):.{digits}f}"  # type: ignore[arg-type]


def _signed(value: object, digits: int = 4) -> str:
    return "—" if _missing(value) else f"{float(value):+.{digits}f}"  # type: ignore[arg-type]


def _pick(frame: pd.DataFrame, level: str, segment: str) -> pd.Series | None:
    hit = frame[(frame["level"] == level) & (frame["segment"] == segment)]
    return None if hit.empty else hit.iloc[0]


def _segments_present(frame: pd.DataFrame) -> list[str]:
    present = set(frame["segment"].unique())
    return [s for s in SEGMENT_ORDER if s in present]


def _levels_present(frame: pd.DataFrame) -> list[str]:
    present = set(frame["level"].unique())
    return [level for level in LADDER if level in present]


# --------------------------------------------------------------------------- #
# §0 렌더
# --------------------------------------------------------------------------- #


def _census_by_side(census: pd.DataFrame) -> pd.DataFrame:
    """TF × 「좁은 존(≤1.28) / 넓은 존」으로 접은 표 — 필터가 사는 쪽과 버리는 쪽."""
    graded = census[census["bucket_lo"].notna()].copy()
    if graded.empty:
        return graded
    graded["side"] = graded["narrow"].map({True: "좁은 존(≤1.28)", False: "넓은 존(>1.28)"})
    grouped = (
        graded.groupby(["timeframe", "side"], as_index=False)[["active_taps", "break_bar_taps"]]
        .sum()
        .assign(
            total=lambda f: f["active_taps"] + f["break_bar_taps"],
        )
    )
    grouped["break_bar_share"] = grouped["break_bar_taps"] / grouped["total"].where(
        grouped["total"] > 0
    )
    order = {tf: i for i, tf in enumerate(harness.DEFAULT_TIMEFRAMES)}
    grouped["_o"] = grouped["timeframe"].map(lambda t: order.get(str(t), 99))
    return grouped.sort_values(["_o", "side"]).drop(columns="_o").reset_index(drop=True)


def _census_verdict(census: pd.DataFrame) -> str:
    """§0 한 문장 — 지워진 탭이 좁은 존에 치우쳐 있나."""
    side = _census_by_side(census)
    if side.empty:
        return "⚠️ **판정 불가** — 등급이 매겨진 탭이 없다."
    narrow = side[side["side"].str.startswith("좁은")]
    wide = side[side["side"].str.startswith("넓은")]
    if narrow.empty or wide.empty:
        return "⚠️ **판정 불가** — 좁은 존과 넓은 존이 둘 다 있어야 대조가 성립한다."
    n_share = float(narrow["break_bar_taps"].sum()) / float(narrow["total"].sum())
    w_share = float(wide["break_bar_taps"].sum()) / float(wide["total"].sum())
    delta = n_share - w_share
    per_tf = []
    for tf in narrow["timeframe"]:
        n = narrow[narrow["timeframe"] == tf]
        w = wide[wide["timeframe"] == tf]
        if n.empty or w.empty:
            continue
        per_tf.append((str(tf), float(n["break_bar_share"].iloc[0] - w["break_bar_share"].iloc[0])))
    same_sign = per_tf and all((d > 0) == (delta > 0) for _tf, d in per_tf)
    if abs(delta) < 0.01:
        verdict = (
            "📌 **판정: 평평하다** — 좁은 존과 넓은 존의 「무효화 봉 탭」 비율 차이가 "
            f"**{delta * 100:+.2f}%p**로 1%p 미만이다. 소급 취소의 오염은 **존폭 축에 공통**"
            "이고 존폭 필터 **고유의** 문제는 아니다."
        )
    elif delta > 0:
        verdict = (
            "🚨 **판정: 좁은 존이 더 많이 지워졌다** — 무효화 봉 탭 비율이 좁은 존 "
            f"**{n_share * 100:.2f}%** vs 넓은 존 **{w_share * 100:.2f}%**"
            f"(**{delta * 100:+.2f}%p**). "
            "존폭 필터의 측정된 우위(WAN-142/152/154/203)가 그만큼 **버그에 업혀 있었다**는 "
            "직접 증거다 — 필터가 고르던 좁은 존일수록 소급 취소가 지는 거래를 더 자주 지워 줬다."
        )
    else:
        verdict = (
            "📌 **판정: 오히려 넓은 존이 더 많이 지워졌다** — 무효화 봉 탭 비율이 좁은 존 "
            f"**{n_share * 100:.2f}%** vs 넓은 존 **{w_share * 100:.2f}%**"
            f"(**{delta * 100:+.2f}%p**). "
            "이슈가 세운 구조적 가설(좁을수록 더 보호받았다)과 **반대 방향**이라, 필터가 "
            "다른 축보다 **더** 오염됐다고 볼 근거는 이 표에 없다."
        )
    tf_bit = (
        f" TF {len(per_tf)}개 전부 같은 방향이다."
        if same_sign
        else " ⚠️ TF마다 방향이 갈리니 심볼평균 하나로 읽지 말 것."
    )
    return verdict + tf_bit


def _render_census(census: pd.DataFrame) -> list[str]:
    if census.empty:
        return []
    side = _census_by_side(census)
    pooled = (
        census.groupby(["bucket", "bucket_lo"], as_index=False)[["active_taps", "break_bar_taps"]]
        .sum()
        .sort_values("bucket_lo")
    )
    pooled["total"] = pooled["active_taps"] + pooled["break_bar_taps"]
    pooled["share"] = pooled["break_bar_taps"] / pooled["total"].where(pooled["total"] > 0)
    parts = [
        "## §0 지워진 탭이 존폭에 치우쳐 있나 (탐지 층 · 1분봉 안 읽음)",
        "",
        _census_verdict(census),
        "",
        "### 존폭 버킷별 (12종목 × 4TF 합)",
        "",
        "| 존폭 ÷ ATR14 | 무효화 전 탭 | 무효화 봉 탭 | 합 | 무효화 봉 비율 |",
        "| -- | --: | --: | --: | --: |",
    ]
    parts += [
        f"| {r.bucket} | {int(r.active_taps):,} | {int(r.break_bar_taps):,} | "
        f"{int(r.total):,} | {_pct(r.share)} |"
        for r in pooled.itertuples()
    ]
    parts += [
        "",
        "### TF × 「필터가 사는 쪽 / 버리는 쪽」",
        "",
        "| TF | 존폭 | 무효화 전 탭 | 무효화 봉 탭 | 무효화 봉 비율 |",
        "| -- | -- | --: | --: | --: |",
    ]
    parts += [
        f"| {r.timeframe} | {r.side} | {int(r.active_taps):,} | {int(r.break_bar_taps):,} | "
        f"{_pct(r.break_bar_share)} |"
        for r in side.itertuples()
    ]
    ungraded = census[census["bucket_lo"].isna()]
    if not ungraded.empty:
        n = int(ungraded["active_taps"].sum() + ungraded["break_bar_taps"].sum())
        parts += [
            "",
            f"⚠️ ATR 워밍업이라 등급을 못 매긴 탭 **{n:,}건**은 위 두 표에서 뺐다 — 엔진은 그 "
            "부류를 **기각**하므로(WAN-158) 존폭 대조의 대상이 아니다. 지우지 않고 CSV에 남긴다.",
        ]
    parts += [
        "",
        "⚠️ **이 표는 채택 근거가 아니다** — 탐지 층 인구조사라 **손익을 안 재고** 사다리를 "
        "대체하지 않는다. 쓸모는 **사다리를 어떻게 읽을지 미리 정해 주는 것**이다.",
        "",
        "📌 존폭 ÷ ATR14는 **엔진이 필터에 쓰는 그 값**이다(탭 봉 **직전 확정봉** ATR14 — 탭 "
        "봉 자신의 ATR은 룩어헤드다). 다른 자로 재면 이 표가 필터를 설명하지 못한다.",
        "",
        "🚨 **손절폭 가드(0.3%)도 같은 축이다** — WAN-328이 「존폭 필터와 손절폭 가드는 같은 "
        "양을 두 자로 잰다」고 적어 뒀다. 이 표는 「존의 두께」 축 **전체**가 이 버그에 얼마나 "
        "얽혀 있는지를 한 번에 보여 준다.",
        "",
    ]
    return parts


# --------------------------------------------------------------------------- #
# §1 렌더
# --------------------------------------------------------------------------- #

#: 증분을 읽을 때 「0과 구분되지 않는다」로 볼 거래당 net R 폭. WAN-120이 "+0.07%p는 0과
#: 구분되지 않는데 코드가 부호만 보고 「뒤집혔다」로 찍었다"고 남긴 함정을 자로 막는다.
NET_R_NOISE = 0.005


@dataclass(frozen=True)
class Increment:
    """사다리 한 단의 증분(다음 단 − 이 단) — 이 표가 실제로 묻는 양."""

    step: str
    component: str
    segment: str
    d_mean_net_r: float
    d_total_return: float
    d_max_drawdown: float
    d_trades: int


def increments(frame: pd.DataFrame) -> list[Increment]:
    """이웃한 두 단이 **둘 다 있을 때만** 증분을 낸다(없는 단을 0으로 메우지 않는다)."""
    out: list[Increment] = []
    levels = _levels_present(frame)
    for lo, hi in zip(LADDER, LADDER[1:], strict=False):
        if lo not in levels or hi not in levels:
            continue
        for segment in _segments_present(frame):
            a, b = _pick(frame, lo, segment), _pick(frame, hi, segment)
            if a is None or b is None:
                continue
            out.append(
                Increment(
                    step=f"{lo}→{hi}",
                    component=RUNGS_BY_NAME[hi].adds,
                    segment=segment,
                    d_mean_net_r=float(b["mean_net_r"]) - float(a["mean_net_r"]),
                    d_total_return=float(b["total_return"]) - float(a["total_return"]),
                    d_max_drawdown=float(b["max_drawdown"]) - float(a["max_drawdown"]),
                    d_trades=int(b["num_trades"]) - int(a["num_trades"]),
                )
            )
    return out


def _verdict(frame: pd.DataFrame) -> str:
    """완료기준 2 — **한 문장 판정**: 인과 엔진에서 값을 더하는 부품이 있는가.

    🚨 고르는 구간은 `is`다(WAN-161/90/111: **OOS는 선택 축이 아니다**). `oos_warm`은
    **확인과 뒤집힘 세기**에만 쓴다.
    """
    incs = increments(frame)
    if not incs:
        return (
            "⚠️ **판정 불가** — 이웃한 두 단이 함께 있어야 증분이 성립한다"
            f"(지금 있는 단: {', '.join(_levels_present(frame)) or '없음'})."
        )
    by_step: dict[str, dict[str, Increment]] = {}
    for inc in incs:
        by_step.setdefault(inc.step, {})[inc.segment] = inc

    adders: list[str] = []
    flips: list[str] = []
    for step, per_segment in by_step.items():
        is_inc = per_segment.get(harness.SEGMENT_IS)
        oos_inc = per_segment.get(PRIMARY_OOS)
        if is_inc is None or oos_inc is None:
            continue
        if abs(is_inc.d_mean_net_r) < NET_R_NOISE and abs(oos_inc.d_mean_net_r) < NET_R_NOISE:
            continue
        if is_inc.d_mean_net_r > 0 and oos_inc.d_mean_net_r > 0:
            adders.append(
                f"**{step}**({is_inc.component}, IS {is_inc.d_mean_net_r:+.4f}R · "
                f"{PRIMARY_OOS} {oos_inc.d_mean_net_r:+.4f}R)"
            )
        elif (is_inc.d_mean_net_r > 0) != (oos_inc.d_mean_net_r > 0):
            flips.append(
                f"{step}(IS {is_inc.d_mean_net_r:+.4f}R → {PRIMARY_OOS} "
                f"{oos_inc.d_mean_net_r:+.4f}R)"
            )
    flip_bit = (
        f" ⚠️ 앞구간→뒷구간에서 부호가 뒤집힌 단: {' · '.join(flips)} — 「뒷구간이 X를 "
        "골랐다」는 채택 근거가 아니다(WAN-161/90/111 과최적화 함정)."
        if flips
        else ""
    )
    top = _pick(frame, ADOPTED_RUNG, PRIMARY_OOS)
    base = _pick(frame, BASE_RUNG, PRIMARY_OOS)
    level_bit = ""
    if top is not None and base is not None:
        level_bit = (
            f" 사다리 양 끝 거래당 net R({PRIMARY_OOS}): 존 단독 "
            f"{float(base['mean_net_r']):+.4f}R → 채택 기본값 {float(top['mean_net_r']):+.4f}R."
        )
    if not adders:
        return (
            "📌 **판정: 인과 엔진에서 거래당 실력을 더하는 부품이 없다** — 앞구간·뒷구간 "
            "**둘 다** 거래당 net R 증분이 양수인 단이 하나도 없다. 그러면 다음 답은 "
            "「파라미터를 더 뒤진다」가 아니라 **「이 규칙 집합에는 없다」**이고, 그건 실패가 "
            "아니라 **결론**이다(이슈 §「그래서 이 이슈가 하는 것」)." + flip_bit + level_bit
        )
    return (
        f"📌 **판정: 값을 더하는 부품이 {len(adders)}개 있다** — {' · '.join(adders)}. "
        "앞구간에서 고르고 뒷구간에서 확인한 것이라(OOS는 선택 축이 아니다) **다음 단계는 "
        "그 축만 쓰는 것**이다." + flip_bit + level_bit
    )


def _next_line(frame: pd.DataFrame) -> str:
    """완료기준 5 — 다음 이슈에 넘길 한 줄."""
    incs = increments(frame)
    if not incs:
        return "⚠️ 사다리가 덜 찼다 — 남은 단을 마저 돌린 뒤에 판정한다."
    survivors = sorted(
        {
            inc.step
            for inc in incs
            if inc.segment in (harness.SEGMENT_IS, PRIMARY_OOS) and inc.d_mean_net_r > 0
        }
    )
    both: list[str] = []
    for step in survivors:
        judged = (harness.SEGMENT_IS, PRIMARY_OOS)
        pair = [i for i in incs if i.step == step and i.segment in judged]
        if len(pair) == 2 and all(i.d_mean_net_r > 0 for i in pair):
            both.append(step)
    if not both:
        return (
            "**쓸 축이 없다.** 인과 엔진에서 두 구간 모두 거래당 실력을 더하는 부품이 없으므로 "
            "「살아남은 축만 스윕」할 대상이 비어 있다 — 다음 이슈는 **파라미터 스윕이 아니라** "
            "규칙 집합 자체를 묻는 쪽(대조군 실험 WAN-355 계열 · 다른 진입 규칙)이어야 한다."
        )
    return (
        f"**쓸 축: {' · '.join(both)}.** 그 단이 켜는 부품만 스윕하고, 판정은 다시 채택 "
        "북에서 낸다(WAN-341). 나머지 축은 이 표가 지지하지 않으므로 건드리지 않는다."
    )


def _render_ladder(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    parts = [
        "## §1 사다리 (채택 북 · 12종목 × 4TF 한 지갑 · 인과 엔진)",
        "",
        _verdict(frame),
        "",
    ]
    for segment in _segments_present(frame):
        seg_rows = [
            row
            for level in _levels_present(frame)
            if (row := _pick(frame, level, segment)) is not None
        ]
        if not seg_rows:
            continue
        mark = " (주 수치, WAN-166)" if segment == PRIMARY_OOS else ""
        parts += [
            f"### `{segment}`{mark}",
            "",
            "| 단 | 켜는 것 | 후보 | 거래 | 승률 | 총수익 | 거래당 netR | MDD | 수익/MDD | 청산 |",
            "| -- | -- | --: | --: | --: | --: | --: | --: | --: | --: |",
        ]
        for row in seg_rows:
            ruin = " 🚨" if bool(row["ruin"]) else ""
            parts.append(
                f"| `{row['level']}` | {row['adds']} | {int(row['num_candidates']):,} | "
                f"{int(row['num_trades']):,} | {_pct(row['win_rate'])} | "
                f"{_pct(row['total_return'])} | {_num(row['mean_net_r'])} | "
                f"{_pct(row['max_drawdown'])}{ruin} | {_num(row['return_over_mdd'], 2)} | "
                f"{int(row['liquidation_events'])} |"
            )
        parts.append("")

    incs = increments(frame)
    if incs:
        parts += [
            "### 증분 (다음 단 − 이 단)",
            "",
            "| 단 | 부품 | 구간 | Δ거래당 netR | Δ총수익 | ΔMDD | Δ거래 |",
            "| -- | -- | -- | --: | --: | --: | --: |",
        ]
        parts += [
            f"| {i.step} | {i.component} | `{i.segment}` | {_signed(i.d_mean_net_r)} | "
            f"{_pp(i.d_total_return)} | {_pp(i.d_max_drawdown)} | {i.d_trades:+,} |"
            for i in incs
        ]
        parts += [
            "",
            "🚨 **판정 자는 거래당 net R이다** — 총수익 %는 6년 복리라 **실현 수익이 아니고**"
            "(WAN-169/213) 거래 수가 다른 단끼리는 더 크게 어긋난다. MDD는 위험의 모양이지 "
            "실력이 아니다.",
            "",
            f"⚠️ 거래당 net R 증분이 **±{NET_R_NOISE:.3f}R 안**이면 「0과 구분되지 않는다」로 "
            "읽는다 — 부호만 보고 「뒤집혔다」로 쓰지 않기 위한 자다(WAN-120 선례).",
            "",
        ]
    return parts


def _render_loo(loo: pd.DataFrame) -> list[str]:
    if loo.empty:
        return []
    parts = [
        f"## 종목 leave-one-out (`{PRIMARY_OOS}` · **지갑 재배치**)",
        "",
        "| 단 | 뺀 종목 | 거래 | 총수익 | MDD | 거래당 netR |",
        "| -- | -- | --: | --: | --: | --: |",
    ]
    parts += [
        f"| `{r.level}` | {r.excluded} | {int(r.num_trades):,} | {_pct(r.total_return)} | "
        f"{_pct(r.max_drawdown)} | {_num(r.mean_net_r)} |"
        for r in loo.itertuples()
    ]
    graded = loo[loo["excluded"] != "-"]
    notes: list[str] = []
    for level in [level for level in LADDER if level in set(loo["level"].unique())]:
        cut = graded[graded["level"] == level]
        if cut.empty:
            continue
        positives = int((cut["mean_net_r"] > 0).sum())
        notes.append(f"`{level}` {positives}/{len(cut)} 종목 제외 판에서 거래당 net R 양수")
    parts += [
        "",
        "🚨 **라벨 필터가 아니라 지갑 재배치다**(WAN-316 스코프 패턴) — 종목을 빼면 그 칸이 "
        "잡던 자본·명목 자리가 비어 **다른 칸이 그 자리를 쓴다**. 라벨로 거르면 그 재배치가 "
        "안 일어나 「빼도 그대로」라는 잘못된 인상을 준다.",
        "",
        ("· ".join(notes) if notes else ""),
        "",
        "⚠️ 옛 판정들의 「플러스는 전부 ETH가 만든다」(WAN-111/119/124/151)가 인과 엔진에서도 "
        "그런지는 위 표의 `-ETH` 행으로 읽는다 — **어느 한 종목을 빼서 부호가 바뀌면 그 단의 "
        "결론은 그 종목의 것**이다.",
        "",
    ]
    return parts


def build_summary(frame: pd.DataFrame, loo: pd.DataFrame, census: pd.DataFrame) -> str:
    parts = [
        "# WAN-366 — 인과 엔진 위에서 부품을 다시 분해한다",
        "",
        "재현: `uv run python -m backtest.wan366_causal_ablation --census-only` → "
        "`--rungs L2,L3,L4 --jobs 4` → `--rungs L1 --jobs 4 --append` → "
        "`--rungs L0 --jobs 4 --append` (요약만: `--from-csv`)",
        "",
        "🚨 **측정 전용이다** — `ConfluenceParams()`·`LeverageBookParams()` 기본값을 하나도 "
        "안 건드렸다. 사다리의 모든 단은 **옵트인**이고 아무것도 안 주면 채택 북이 나온다.",
        "",
    ]
    parts += _render_census(census)
    parts += _render_ladder(frame)
    parts += _render_loo(loo)
    parts += [
        "## 다음 이슈에 넘길 한 줄 (완료기준 5)",
        "",
        _next_line(frame) if not frame.empty else "⚠️ 사다리를 아직 안 돌렸다.",
        "",
        "## 읽는 법 · 경고",
        "",
        "* 🚨 **기본값 전환 제안이 아니다** — 무엇이 나오든 채택은 **재-베이스라인 = 사용자 "
        "결정**이고 개발자 임의 착수 금지다.",
        "* 🚨 **뒷구간(OOS)은 고르는 축이 아니다** — 앞구간에서 고르고 뒷구간은 **확인과 "
        "뒤집힘 세기**에만 쓴다. 기댓값이 음수인 엔진 위에서 축을 여럿 뒤지면 앞구간에서 좋아 "
        "보이는 조합은 **반드시** 나온다(WAN-161: 배수 argmax가 8셀 중 7에서 뒤집혔다 · "
        "WAN-90 · WAN-111).",
        "* ⚠️ **총수익 %는 복리 착시**(WAN-169/213) — 판정은 **거래당 net R**과 MDD로 낸다. "
        f"{_FLOOR_NOTE}.",
        "* ⚠️ 전부 `baseline`(닿으면 체결) 렌즈 위 값이다. 체결 보수화(`pen_5bp`)는 별개 축이고 "
        "큐 우선순위 실측은 WAN-98(Canceled) 소관이다.",
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 그쪽은 *진입 규칙이 "
        "무작위와 구분되는가*이고 이 사다리는 *어느 부품이 값을 더하는가*다. 대조군 실험은 "
        "별건(WAN-355 계열).",
        "* ⚠️ **옛 사다리(WAN-114/145/151) 결론과 셀을 직접 비교하지 말 것** — 그쪽은 소급 취소 "
        "엔진 · 3~6종목 · 3년 창 · per-cell이고 이 표는 인과 엔진 · 12종목 · 6년 · **북**이다. "
        "겹치는 것은 **질문**이지 좌표가 아니다.",
        "* ⚠️ **이 사다리가 안 다루는 축**: 익절 배수 1.5R · 지정가 오프셋 2bp(청산·후보 축) · "
        "cap_only 5배 · 배수 · 작업 TF · 유니버스(회계·사이징 축). 순서가 있어서 뒤로 미룬 "
        "것이다 — **거래당 net R이 음수면 어떤 배수도 그 부호를 못 바꾼다**(WAN-90).",
        "",
    ]
    return "\n".join(p for p in parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-366 인과 엔진 부품 사다리")
    parser.add_argument("--symbols", default=None, help="쉼표 구분(기본: 채택 12종목)")
    parser.add_argument("--tf", default=None, help="쉼표 구분(기본: 채택 4TF)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--rungs", default=None, help=f"쉼표 구분(기본: {','.join(LADDER)})")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 쓴다")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    parser.add_argument(
        "--census-only", action="store_true", help="§0(탐지 층 인구조사)만 — 1분봉을 안 읽는다"
    )
    parser.add_argument("--skip-census", action="store_true", help="§0을 건너뛴다(이미 냈다면)")
    return parser.parse_args(argv)


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh
    if fresh.empty:
        return existing
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=list(keys), keep="last").reset_index(drop=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    symbols = (
        [s.strip() for s in args.symbols.split(",")] if args.symbols else harness.DEFAULT_SYMBOLS
    )
    timeframes = [t.strip() for t in args.tf.split(",")] if args.tf else harness.DEFAULT_TIMEFRAMES

    if args.from_csv:
        frame, loo, census = _read(CSV_PATH), _read(LOO_CSV_PATH), _read(CENSUS_CSV_PATH)
        if frame.empty and census.empty:
            print(f"[wan366] {CSV_PATH}도 {CENSUS_CSV_PATH}도 없습니다 — 먼저 돌리세요.")
            return 1
        SUMMARY_PATH.write_text(build_summary(frame, loo, census), encoding="utf-8")
        print(f"[wan366] 요약 재생성: {SUMMARY_PATH}")
        return 0

    census_frame = _read(CENSUS_CSV_PATH)
    if not args.skip_census:
        t0 = time.time()
        fresh = census_to_frame(run_census(symbols, timeframes, start=args.start, end=args.end))
        if not fresh.empty:
            census_frame = _merge(census_frame, fresh, CENSUS_KEYS)
            census_frame.to_csv(CENSUS_CSV_PATH, index=False)
        print(
            f"[wan366] §0 인구조사: {CENSUS_CSV_PATH} ({len(census_frame)}행, "
            f"{time.time() - t0:.0f}s)",
            flush=True,
        )

    if args.census_only:
        SUMMARY_PATH.write_text(
            build_summary(_read(CSV_PATH), _read(LOO_CSV_PATH), census_frame), encoding="utf-8"
        )
        print(f"[wan366] 요약: {SUMMARY_PATH}")
        return 0

    levels = [level.strip() for level in args.rungs.split(",")] if args.rungs else list(LADDER)
    unknown = [level for level in levels if level not in RUNGS_BY_NAME]
    if unknown:
        print(f"[wan366] 모르는 단: {unknown} (가능: {', '.join(LADDER)})")
        return 2

    base_rows = _read(CSV_PATH) if args.append else pd.DataFrame()
    base_loo = _read(LOO_CSV_PATH) if args.append else pd.DataFrame()

    def persist(rows: list[LadderRow], loo: list[LadderLooRow]) -> None:
        nonlocal base_rows, base_loo
        base_rows = _merge(base_rows, rows_to_frame(rows), CSV_KEYS)
        base_loo = _merge(base_loo, loo_to_frame(loo), LOO_CSV_KEYS)
        base_rows.to_csv(CSV_PATH, index=False)
        base_loo.to_csv(LOO_CSV_PATH, index=False)
        print(f"[wan366] 적재: {CSV_PATH} ({len(base_rows)}행)", flush=True)

    run_report(
        symbols,
        timeframes,
        levels=levels,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        on_generation=persist,
    )
    SUMMARY_PATH.write_text(build_summary(base_rows, base_loo, census_frame), encoding="utf-8")
    print(f"[wan366] 요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
