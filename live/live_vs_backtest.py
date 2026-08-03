"""당일(KST) 라이브 vs 백테스트 대조 조회 — 같은 날 funnel을 나란히 (WAN-233).

`live.fill_report --day`(WAN-232)가 **라이브에서 오늘 무슨 일이 있었나**(주문별 예약→체결)를
낸다면, 이 도구는 그 옆에 **같은 날을 백테스트 채택 엔진으로 돌린 결과**를 붙여 진입 깔때기
네 단계(탭 → 예약 → 체결 → 진입)를 **라이브 | 백테스트 | 차이**로 보여 준다. 사용자 원문
(2026-08-02): *"그날 라이브와 백테스트와의 비교를 조회할 수 있었으면 좋겠어."*

## 무엇을 대조하나 — 수익률이 아니라 「몇 개가 어디서 빠지나」

같은 KST 하루, 같은 심볼×TF(9종목 × 15m·1h·4h = 27조합)에 대해:

1. **탭/셋업 감지** — 같은 캔들·같은 탐지 파라미터면 **거의 같아야 한다**(가장 깨끗한
   파리티 체크). 어긋나면 데이터/탐지 배선 문제.
2. **예약(존폭 필터 1.28 통과)** — 필터 로직·ATR 정의가 같으면 같아야 한다. 어긋나면
   필터 배선 차이.
3. **체결** — 🚨 **여기는 원래 갈린다.** 라이브 = 실제 틱 체결 / 백테스트 = "닿으면 체결"
   (`baseline`, 낙관 상한). **그 차이가 이 도구가 보려는 핵심 값**이다. 참고로 백테스트를
   `pen_5bp`(관통 벌점)로도 병기해 「보수적 체결이 라이브에 더 가까운지」를 읽는다.
4. **진입(집행 가드 통과)** — 손절폭 가드(0.3% WAN-79)·명목 한도·포지션 보유. 같은 가드면
   같아야 한다.

## 백테스트 쪽 규약 (함정을 피하는 배선)

* 📌 **워밍업 연속 + 그날만 평가**(WAN-166 `--oos-warm`과 같은 규약, `eval_from_ms`). 그
  하루만 「차갑게」 돌리면 존 재고·지표가 0에서 시작해 다 죽는다. 앞 구간(`--warmup-days`)을
  연속으로 태워 존·지표를 데운 뒤, **탭이 그 KST 하루 안인 셋업만** 평가한다. 워밍업 길이는
  라이브의 전-이력 존 재고를 근사하는 노브이지 파라미터가 아니다(길수록 라이브에 가깝다).
* 📌 **라이브가 본 것과 같은 캔들 스냅샷** — 백테스트는 그 KST 하루의 자정에서 끝난다
  (`end_ms = 다음 자정`). **미래 봉을 넣지 않는다**(누수 0 — 회귀 테스트가 그날 이후 봉을
  잘라도 그날 판정이 비트 동일함을 고정한다).
* 📌 **대조는 funnel(개수)에서 하고 손익·사이징으로 넘어가지 않는다.** 백테스트 채택 회계는
  레버리지 북(WAN-213 cap_only 5배·공유 자본)인데 라이브 페이퍼 배선(WAN-171)과 사이징
  축이 다를 수 있다. 북/사이징 차이가 진입 개수 대조를 오염시키지 않도록 백테스트 쪽은
  **per-cell 단일 포지션**(`run_once`의 단일 경로)으로 맞춰 「어느 셋업이 진입했나」만 본다.
* ⚠️ **체결 단계 불일치를 「버그」로 단정 금지** — 라벨(라이브=틱 / 백테스트=baseline 상한)을
  붙여 읽는다. 3·4단계가 아니라 1·2단계가 갈리면 그때가 진짜 배선 의심이다.

## 성격

순수 대조 조회다. 엔진·전략·기본값·토대 불변, `ALPHABLOCK_LIVE_TRADING=false` 유지.
라이브 쪽 숫자는 `OrderJournal.day_summary`(WAN-232)를 그대로 재사용하므로 `alphablock fills`
당일 조회와 **같은 장부·같은 창**을 본다(두 도구가 서로 검산).

## 재현

```
alphablock compare                     # 오늘(KST) 대조
alphablock compare --day 2026-08-02    # 그 날
python -m live.live_vs_backtest --day 2026-08-02 --by-cell --jobs 6
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from backtest.harness import (
    BASELINE_FILL,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    MarketData,
    build_config,
    build_params,
    detect_order_blocks,
    fill_preset,
    load_market_data,
    run_once,
)
from backtest.models import BacktestConfig
from backtest.zone_limit_backtest import _prepare_htf
from common.timefmt import KST, kst_day_bounds_for_date
from config.settings import get_settings
from live.order_journal import DaySummary, OrderJournal, PlacedOrder
from strategy.confluence import entry_candidate_signals
from strategy.indicators import atr
from strategy.models import (
    ConfluenceParams,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockResult,
)

__all__ = [
    "DEFAULT_WARMUP_DAYS",
    "BacktestFunnel",
    "CellFunnel",
    "DayComparison",
    "backtest_cell_funnel",
    "backtest_funnel",
    "cell_funnel",
    "compare_day",
    "count_taps_reservations",
    "render_comparison",
    "resolve_day_window",
]

#: 하루(ms). KST는 서머타임이 없어 하루가 정확히 24h다(`common.timefmt`가 자정 경계를 잡는다).
_DAY_MS = 86_400_000

#: 백테스트 워밍업 기본 길이(일). 라이브 러너는 전 이력을 연속으로 봐 존 재고가 가득 차
#: 있는데, 대조 하루만 돌리면 재고가 비어 탭이 적게 잡힌다. 이 길이만큼 앞 구간을 태워
#: 존·지표를 데운다 — **파라미터가 아니라 근사 노브**다(길수록 라이브에 가깝지만 그만큼
#: 느리다). 15m·긴 워밍업은 초선형으로 무거우니(WAN-203) 탐색 중에는 심볼·TF를 좁혀 돈다.
DEFAULT_WARMUP_DAYS = 120


@dataclass(frozen=True)
class CellFunnel:
    """한 (심볼, TF)의 백테스트 진입 깔때기 카운트(그 KST 하루, 워밍업 연속·단일 포지션).

    `has_data=False`면 그 셀의 데이터가 없어(수집 안 됐거나 1분봉 공백) 대조에서 뺀다 —
    0으로 세면 "탭이 없었다"와 "데이터가 없었다"가 표에서 같아 보인다(WAN-95 부류).
    """

    symbol: str
    timeframe: str
    taps: int
    reservations: int
    fills_baseline: int
    fills_pen5: int
    entries: int
    has_data: bool = True


@dataclass(frozen=True)
class BacktestFunnel:
    """27조합 백테스트 funnel — 셀별 + 집계."""

    cells: tuple[CellFunnel, ...]

    @property
    def with_data(self) -> tuple[CellFunnel, ...]:
        return tuple(c for c in self.cells if c.has_data)

    @property
    def taps(self) -> int:
        return sum(c.taps for c in self.with_data)

    @property
    def reservations(self) -> int:
        return sum(c.reservations for c in self.with_data)

    @property
    def fills_baseline(self) -> int:
        return sum(c.fills_baseline for c in self.with_data)

    @property
    def fills_pen5(self) -> int:
        return sum(c.fills_pen5 for c in self.with_data)

    @property
    def entries(self) -> int:
        return sum(c.entries for c in self.with_data)

    @property
    def missing_cells(self) -> tuple[CellFunnel, ...]:
        return tuple(c for c in self.cells if not c.has_data)


@dataclass(frozen=True)
class DayComparison:
    """하루 대조 결과 — 라이브(장부)와 백테스트(채택 엔진)를 나란히."""

    day_key: str
    live: DaySummary
    backtest: BacktestFunnel
    live_by_cell: tuple[tuple[str, str, DaySummary], ...]


def resolve_day_window(day_arg: str) -> tuple[int, int, str]:
    """`--day` 인자를 `[start_ms, end_ms)` KST 하루 창 + 날짜 키로 푼다(WAN-232와 같은 규약).

    `"today"`(인자 없이)는 지금(KST)이 속한 날, 그 밖은 `YYYY-MM-DD`. 창 경계는
    `common.timefmt.kst_day_bounds_for_date`가 잡아 `alphablock fills` 당일 조회와 같은
    자정 경계를 공유한다 — 두 도구가 서로 검산한다(CTA #3).
    """
    if day_arg == "today":
        day = datetime.now(tz=KST).date()
    else:
        day = datetime.strptime(day_arg, "%Y-%m-%d").date()
    start_ms, end_ms = kst_day_bounds_for_date(day)
    return start_ms, end_ms, day.isoformat()


def count_taps_reservations(
    ob_result: OrderBlockResult,
    market: MarketData,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    *,
    day_start_ms: int,
    day_end_ms: int,
) -> tuple[int, int]:
    """그 KST 하루에 탭한 셋업 수와 그중 존폭 필터를 통과한 수(예약)를 센다.

    엔진의 후보 빌더(`build_zone_limit_candidates`)와 **같은 탭 생성기·같은 방향 게이트·같은
    존폭 필터**를 재사용한다: 탭은 `entry_candidate_signals`가, 존폭 판정은
    `ConfluenceParams.zone_width_filter_passes`가 낸다. ATR은 빌더와 똑같이 탭 봉 **직전
    확정봉**(pos-1)에서 읽는다(룩어헤드 금지). 채택 기본값은 봉내 라이브 밴드라 볼린저 규칙 3
    기각이 후보 빌드 시점이 아니라 서브스텝에서 일어나므로(밴드가 봉내에 움직인다) 여기서
    세는 「예약」은 엔진이 실제로 만드는 후보 수와 같다 — 밴드 기각·미체결은 예약→체결 단계의
    감소다(회귀 테스트가 `체결 ≤ 예약 ≤ 탭`을 실데이터로 고정한다).
    """
    frame = _prepare_htf(market.htf_df)
    if len(frame) == 0:
        return 0, 0
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    closes = [float(v) for v in frame["close"].astype(float).tolist()]
    time_to_pos = {t: i for i, t in enumerate(times)}
    atr14: list[float] | None = None
    if params.max_zone_width_atr is not None:
        atr14 = [float(v) for v in atr(frame, length=params.zone_width_atr_length).tolist()]

    taps = 0
    reservations = 0
    for signal in entry_candidate_signals(ob_result, params, times, closes, time_to_pos):
        if signal.status != "active":
            continue
        if not (day_start_ms <= signal.trigger_time < day_end_ms):
            continue
        pos = time_to_pos.get(signal.trigger_time)
        if pos is None:
            continue
        ob = signal.order_block
        is_long = ob.direction is OrderBlockDirection.BULLISH
        if (is_long and not cfg.allow_long) or (not is_long and not cfg.allow_short):
            continue
        if not is_long and not params.short_enabled:
            continue
        taps += 1
        width_atr = atr14[pos - 1] if (atr14 is not None and pos >= 1) else None
        if params.zone_width_filter_passes(ob, width_atr):
            reservations += 1
    return taps, reservations


def cell_funnel(
    market: MarketData,
    ob_result: OrderBlockResult,
    *,
    day_start_ms: int,
    day_end_ms: int,
) -> CellFunnel:
    """미리 로드·탐지된 (심볼, TF)의 백테스트 funnel을 낸다(그 KST 하루, 워밍업 연속).

    `market`은 `[워밍업 시작, 그날 자정]`으로 잘려 있어야 한다(미래 봉 없음). `eval_from_ms`가
    탭이 그날 자정 이후인 셋업만 평가하고, 자정에서 끝나는 데이터가 그날 이후를 못 보게 한다.
    이 함수를 미리 로드된 입력에서 받는 것은 회귀 테스트가 「미래 봉을 잘라도 비트 동일」을
    고정할 수 있게 하기 위해서다(누수 0).
    """
    cfg = build_config(market.timeframe)
    params_base = build_params(fill=BASELINE_FILL)
    params_pen5 = build_params(fill=fill_preset("pen_5bp"))

    taps, reservations = count_taps_reservations(
        ob_result, market, params_base, cfg, day_start_ms=day_start_ms, day_end_ms=day_end_ms
    )
    out_base = run_once(
        market, params=params_base, cfg=cfg, order_block_result=ob_result, eval_from_ms=day_start_ms
    )
    out_pen5 = run_once(
        market, params=params_pen5, cfg=cfg, order_block_result=ob_result, eval_from_ms=day_start_ms
    )
    fills_base = out_base.stats.filled if out_base.stats is not None else 0
    fills_pen5 = out_pen5.stats.filled if out_pen5.stats is not None else 0
    # `eval_from_ms`가 후보를 탭 시각으로 걸러 낸 뒤 시퀀싱하므로, 남은 거래는 전부 그날
    # 탭이다(진입 시각도 그날 자정 이후·다음 자정 이전). 그 수가 곧 「진입」이다.
    entries = len(out_base.result.trades)
    return CellFunnel(
        symbol=market.symbol,
        timeframe=market.timeframe,
        taps=taps,
        reservations=reservations,
        fills_baseline=fills_base,
        fills_pen5=fills_pen5,
        entries=entries,
    )


def backtest_cell_funnel(
    symbol: str,
    timeframe: str,
    *,
    day_start_ms: int,
    day_end_ms: int,
    warmup_days: int,
) -> CellFunnel:
    """한 셀의 데이터를 로드·탐지해 그날 백테스트 funnel을 낸다.

    데이터가 없거나(수집 안 됨) 1분봉 공백이면 `has_data=False`로 돌려 대조에서 뺀다.
    창은 `[그날 자정 − warmup_days, 그날 자정]`이다 — 미래 봉은 애초에 로드하지 않는다.
    """
    warmup_start_ms = day_start_ms - warmup_days * _DAY_MS
    market = load_market_data(
        symbol,
        timeframe,
        start_ms=warmup_start_ms,
        end_ms=day_end_ms,
        need_1m=True,
        funding=False,
    )
    if market.htf_df.empty or market.df_1m.empty:
        return CellFunnel(symbol, timeframe, 0, 0, 0, 0, 0, has_data=False)
    ob_result = detect_order_blocks(market, OrderBlockParams())
    return cell_funnel(market, ob_result, day_start_ms=day_start_ms, day_end_ms=day_end_ms)


@dataclass(frozen=True)
class _CellTask:
    symbol: str
    timeframe: str
    day_start_ms: int
    day_end_ms: int
    warmup_days: int


def _run_cell_task(task: _CellTask) -> CellFunnel:
    return backtest_cell_funnel(
        task.symbol,
        task.timeframe,
        day_start_ms=task.day_start_ms,
        day_end_ms=task.day_end_ms,
        warmup_days=task.warmup_days,
    )


def backtest_funnel(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    day_start_ms: int,
    day_end_ms: int,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    jobs: int = 1,
) -> BacktestFunnel:
    """27조합(심볼×TF)의 백테스트 funnel을 낸다. `jobs>1`이면 (심볼, TF) 단위로 병렬."""
    tasks = [
        _CellTask(symbol, tf, day_start_ms, day_end_ms, warmup_days)
        for symbol in symbols
        for tf in timeframes
    ]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            cells = list(pool.map(_run_cell_task, tasks))
    else:
        cells = [_run_cell_task(task) for task in tasks]
    return BacktestFunnel(cells=tuple(cells))


def _live_by_cell(
    orders: Sequence[PlacedOrder],
) -> tuple[tuple[str, str, DaySummary], ...]:
    """당일 코호트를 (심볼, TF)로 갈라 각 셀의 요약을 낸다(집계 규칙은 `DaySummary.from_orders`).

    셀별 요약을 다 더하면 전체 `day_summary`와 같다 — 같은 분류를 공유하므로(회귀 테스트 고정).
    """
    grouped: dict[tuple[str, str], list[PlacedOrder]] = {}
    for order in orders:
        grouped.setdefault((order.symbol, order.timeframe), []).append(order)
    return tuple(
        (symbol, timeframe, DaySummary.from_orders(cell_orders))
        for (symbol, timeframe), cell_orders in sorted(grouped.items())
    )


def compare_day(
    journal: OrderJournal,
    *,
    day_start_ms: int,
    day_end_ms: int,
    day_key: str,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    warmup_days: int = DEFAULT_WARMUP_DAYS,
    jobs: int = 1,
) -> DayComparison:
    """하루 대조를 계산한다 — 라이브(장부)는 조회, 백테스트는 채택 엔진으로 재산출."""
    live = journal.day_summary(start_ms=day_start_ms, end_ms=day_end_ms)
    orders = journal.orders_placed_between(start_ms=day_start_ms, end_ms=day_end_ms)
    bt = backtest_funnel(
        symbols,
        timeframes,
        day_start_ms=day_start_ms,
        day_end_ms=day_end_ms,
        warmup_days=warmup_days,
        jobs=jobs,
    )
    return DayComparison(
        day_key=day_key,
        live=live,
        backtest=bt,
        live_by_cell=_live_by_cell(orders),
    )


def _short(symbol: str) -> str:
    """표에 넣을 짧은 심볼(`BTC/USDT:USDT` → `BTC`)."""
    return symbol.split("/", 1)[0]


def _diff(backtest: int, live: int) -> str:
    d = backtest - live
    return f"+{d}" if d > 0 else str(d)


def render_comparison(comp: DayComparison, *, by_cell: bool = False) -> str:
    """대조 결과를 마크다운 표로 렌더한다(탭 → 예약 → 체결 → 진입, 라이브 | 백테스트 | 차이)."""
    live = comp.live
    bt = comp.backtest
    lines: list[str] = [
        f"# 당일 라이브 vs 백테스트 대조 · {comp.day_key} (KST) — WAN-233",
        "",
        "진입 깔때기 네 단계를 나란히 본다 — **수익률이 아니라 「몇 개가 어디서 빠지나」**.",
        "차이 = 백테스트 − 라이브. 🚨 **체결 단계는 원래 갈린다**(라이브=실제 틱 체결 ·"
        ' 백테스트=`baseline` "닿으면 체결" 낙관 상한). 1·2단계가 갈리면 그때가 배선 의심이다.',
        "",
        "| 단계 | 라이브 | 백테스트 | 차이(BT−L) |",
        "| -- | --: | --: | --: |",
        f"| 탭/셋업 감지 | {live.taps} | {bt.taps} | {_diff(bt.taps, live.taps)} |",
        f"| 예약(존폭 1.28 통과) | {live.reserved} | {bt.reservations} |"
        f" {_diff(bt.reservations, live.reserved)} |",
        f"| 체결 | {live.filled} (틱) | {bt.fills_baseline} (baseline) |"
        f" {_diff(bt.fills_baseline, live.filled)} |",
        f"| 진입(집행 가드 통과) | {live.entered} | {bt.entries} |"
        f" {_diff(bt.entries, live.entered)} |",
        "",
        f"체결 백테스트 참고 렌즈: `pen_5bp`(관통 벌점) **{bt.fills_pen5}** — `baseline`"
        f" {bt.fills_baseline}보다 라이브 틱 체결({live.filled})에 더 가까우면, 라이브 체결"
        " 부족의 상당 부분은 버그가 아니라 낙관 가정의 비용이다(WAN-96/124).",
        "",
        "**여기서 갈렸으면 무엇을 의심하나**",
        "1. **탭** 이 갈리면 — 데이터 스냅샷·오더블록 탐지 배선(같은 캔들·같은 파라미터인데"
        " 다르면 수집 지연이나 탐지 경로 차이). 가장 깨끗한 파리티 체크다.",
        "2. **예약** 이 갈리면 — 존폭 필터(1.28)·ATR 정의·슬롯 점유 규칙. 탭은 같은데 예약이"
        " 다르면 필터 배선 차이다.",
        "3. **체결** 은 갈리는 게 정상 — 라이브는 실제 틱이 지정가에 닿아야 하고, 백테스트"
        " `baseline`은 「닿으면 체결」로 세 낙관적이다. `pen_5bp`가 라이브에 가까운지로 그"
        " 낙관의 크기를 읽는다.",
        "4. **진입** 이 (체결 대비) 갈리면 — 집행 가드(손절폭 0.3% WAN-79·명목 한도·포지션"
        " 보유). 같은 가드면 체결→진입 비율이 비슷해야 한다.",
    ]

    missing = bt.missing_cells
    if missing:
        names = ", ".join(f"{_short(c.symbol)} {c.timeframe}" for c in missing)
        lines.append("")
        lines.append(
            f"⚠️ 데이터 없어 백테스트에서 뺀 셀 {len(missing)}개: {names}"
            " (수집 안 됐거나 1분봉 공백 — 라이브에 그 셀 주문이 있으면 대조가 한쪽만 있다)."
        )

    lines.append("")
    lines.append(
        f"⚙️ 백테스트: 워밍업 연속(그 하루 앞 구간을 태워 존·지표를 데움) + 그날만 평가"
        f"(WAN-166 규약) · per-cell 단일 포지션 · 미래 봉 없음. 라이브 숫자는"
        f" `alphablock fills --day {comp.day_key}`와 같은 장부·같은 창이다(WAN-232)."
    )

    if by_cell:
        lines.extend(_render_by_cell(comp))
    return "\n".join(lines)


def _render_by_cell(comp: DayComparison) -> list[str]:
    """심볼×TF별 대조 표(옵션). 라이브·백테스트 어느 쪽이든 값이 있는 셀만 나열한다."""
    live_map = {(sym, tf): summary for sym, tf, summary in comp.live_by_cell}
    bt_map = {(c.symbol, c.timeframe): c for c in comp.backtest.cells}
    keys = sorted(set(live_map) | set(bt_map))

    lines = ["", f"## 심볼×TF별 대조 · {comp.day_key} (KST)", ""]
    lines.append("| 심볼 | TF | 탭(L/BT) | 예약(L/BT) | 체결(L/BT·pen5) | 진입(L/BT) |")
    lines.append("| -- | -- | -- | -- | -- | -- |")
    any_row = False
    for sym, tf in keys:
        live_cell = live_map.get((sym, tf))
        bt_cell = bt_map.get((sym, tf))
        live_taps = live_cell.taps if live_cell else 0
        live_res = live_cell.reserved if live_cell else 0
        live_fill = live_cell.filled if live_cell else 0
        live_entry = live_cell.entered if live_cell else 0
        if bt_cell is not None and bt_cell.has_data:
            bt_taps = str(bt_cell.taps)
            bt_res = str(bt_cell.reservations)
            bt_fill = f"{bt_cell.fills_baseline}·{bt_cell.fills_pen5}"
            bt_entry = str(bt_cell.entries)
        else:
            bt_taps = bt_res = bt_fill = bt_entry = "—"
        if live_cell is None and (bt_cell is None or not bt_cell.has_data):
            continue
        any_row = True
        lines.append(
            f"| {_short(sym)} | {tf} | {live_taps}/{bt_taps} | {live_res}/{bt_res} |"
            f" {live_fill}/{bt_fill} | {live_entry}/{bt_entry} |"
        )
    if not any_row:
        lines.append("| (해당 셀 없음) | | | | | |")
    lines.append("")
    lines.append(
        "각 칸 `라이브/백테스트`. 체결 칸의 백테스트는 `baseline·pen5`. `—`는 그 셀 백테스트"
        " 데이터 없음."
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-233 당일(KST) 라이브 vs 백테스트 대조 조회",
    )
    parser.add_argument("--db", default=None, help="장부 DB 경로(기본: 설정의 db_path)")
    parser.add_argument(
        "--day",
        default="today",
        metavar="YYYY-MM-DD",
        help="대조할 KST 날짜(기본: 오늘). 예: 2026-08-02",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=DEFAULT_WARMUP_DAYS,
        help=(
            "백테스트 워밍업 길이(일, 기본 %(default)s). 라이브 전-이력 존 재고를 근사하는"
            " 노브 — 길수록 라이브에 가깝지만 느리다."
        ),
    )
    parser.add_argument("--by-cell", action="store_true", help="심볼×TF별 대조 표도 출력")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="백테스트 (심볼, TF) 병렬 워커 수(기본 1 = 직렬)",
    )
    args = parser.parse_args(argv)

    db_path = args.db if args.db is not None else get_settings().db_path
    start_ms, end_ms, day_key = resolve_day_window(args.day)
    journal = OrderJournal(db_path)
    try:
        comp = compare_day(
            journal,
            day_start_ms=start_ms,
            day_end_ms=end_ms,
            day_key=day_key,
            warmup_days=args.warmup_days,
            jobs=args.jobs,
        )
    finally:
        journal.close()
    print(render_comparison(comp, by_cell=args.by_cell))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
