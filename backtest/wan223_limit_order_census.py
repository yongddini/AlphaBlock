"""WAN-223 §1 — 지정가 주문 관리 census (만료 후 재진입 구멍의 크기 · GO/STOP 게이트).

## 무엇을 세나 (사용자 제안 2026-07-31)

지금 엔진은 지정가 주문을 **탭(바깥→안 전이) 순간**에 만들고 `limit_valid_bars`(채택
기본값 24봉)가 지나면 취소한다. 사용자가 짚은 구멍: *"취소하면 다음엔 못 건다"* — 주문이
만료로 취소된 뒤 가격이 곧 같은 존에 다시 닿았는데 「바깥→안」 새 탭이 없어(존 근처에 계속
머묾) 주문을 다시 걸지 못하는 재진입 유실. 이 모듈은 그 구멍의 **크기**를 재서, 대기 모델
(§2 형성-즉시 예약)을 지을 가치가 있는지 GO/STOP을 가린다.

## 핵심 지표 = 만료로 놓친 재진입 (두 패스 차이)

같은 셋업 풀(같은 `retap_signals`)을 **두 번** 돌린다 — 유일한 차이는 유효기간이다:

* **`base`** = 채택 기본값(`limit_valid_bars=24`).
* **`wait`** = 무기한 대기(`limit_valid_bars=None`, 존 무효화까지). 이게 "형성-즉시 예약"의
  체결 효과에 가장 가까운 대리(순수 대기 지정가 — 만료가 없다).

핵심: **`filled_wait − filled_base`** = 만료를 없애면 늘어나는 체결 수다. 두 패스의 셋업
집합이 **동일**하므로(재탭도 양쪽에 똑같이 들어 있다), 이 차이는 **재탭이 이미 복구한 체결을
뺀** 순증이다 — 즉 사용자가 말한 "재탭 실패로 놓친 재진입"의 크기 그 자체다. 같은 셋업이
24봉 안에 닿으면 무기한에서도(더 일찍) 닿으므로 `filled_wait ≥ filled_base`가 성립한다
(무효화가 먼저 오면 두 패스가 똑같이 취소 — `substep`이 만료보다 무효화를 먼저 본다).

## 부가 지표

* **깔때기(§1b 흡수)**: eligible → 상태 분해(no_touch·expired·invalidated·cond_fail) →
  filled → entries(단일 포지션 시퀀싱 후) → 진입 빈도(평균 며칠에 한 번). WAN-225 흡수.
* **슬롯참 체결 거부**: `filled_base − trades_base` = 체결됐지만 칸이 이미 포지션 중이라
  진입이 안 된 수(칸 단위 슬롯 경합). 명목 상한 거부는 **북(WAN-180) 소관**이라 여기서
  재측정하지 않는다(실측 6년 7건으로 드묾 — 요약에 기록).
* **동시 대기 분포**: 한 칸에서 미체결 지정가가 동시에 몇 개 걸려 있나(24봉 대기 지평
  상한 근사) — 「가까운 주문 우선」 배분이 필요한지의 대리.

## 성격

측정 전용. `ConfluenceParams()`·`OrderBlockParams()` **채택 기본값 그대로** 돌리며 옛 핀
(`LEGACY_*`)을 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · 못 박은 6년 창
(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지). "체결↑ = 좋다"가
**아니다** — 「엣지 없음」(WAN-84/88/111/114/124/151) 불변이고, 늘어난 체결이 수익을
더하는지는 `total_return` 델타로 함께 본다(전부 `baseline` 낙관 렌즈 위 값).

⚠️ **컴퓨트**: 무기한 대기 패스(`wait`)는 셋업이 무효화까지 스캔해 15m·6년에서 초선형으로
느리다(단일 15m 셀도 >2분). 그래서 기본 `--tf`는 **4h·1h**이고, 15m 전 구간은 무거운
후속 컴퓨트로 남긴다(요약에 명시). 4h·1h는 못 박은 6년 창에서 셀당 ~17s·~80s.

## 재현

```
uv run python -m backtest.wan223_limit_order_census --tf 4h,1h --jobs 6
uv run python -m backtest.wan223_limit_order_census --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.run import parse_date_ms
from backtest.substep import ZoneLimitStatus
from backtest.sweep import timeframe_to_ms
from backtest.zone_limit_backtest import SetupDiagnostic
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan223_limit_order_census.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan223_limit_order_census_summary.md"

#: 못 박은 채택 창(WAN-182). `--years N`은 미끄러지므로 쓰지 않는다.
DEFAULT_START = harness.DEFAULT_START
DEFAULT_END = harness.DEFAULT_END

#: 채택 유니버스 9종목(WAN-182).
#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
ALL_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS

#: 기본 TF = 4h·1h(컴퓨트 실현 가능). 15m 전 구간의 무기한 패스는 무거운 후속(모듈 독스트링).
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h")

#: 신규 3종목 — 펀딩 0행이라 `total_return` 열이 낙관적(WAN-178 백필 전). census의 핵심
#: 지표(체결·만료·진입 수)는 펀딩과 무관하므로 대리를 얹지 않는다(wan167 census 관행).
FUNDING_GAP_SYMBOLS: frozenset[str] = frozenset(
    harness.normalize_symbol(s) for s in ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
)

#: 판정 문턱 — 핵심 지표(만료로 놓친 재진입)가 채택 진입 대비 차지하는 비율.
#: 20% 이상이면 구멍이 커 대기 모델(§2)을 지을 값이 있고(단 수익 델타도 봐야 함),
#: 5% 미만이면 드물어 「여기서 멈추고 기록만」(이슈 §1의 STOP 조건)이다. 사이는 경계.
SIGNIFICANT_MISS_SHARE = 0.20
NEGLIGIBLE_MISS_SHARE = 0.05

#: 대기 모델이 늘린 체결이 **수익도 더하는가**의 문턱(심볼평균 total_return 델타, %p).
#: 이보다 작으면 "체결은 늘지만 수익은 안 따라온다"(WAN-222 관측)라 GO 근거가 약하다.
MATERIAL_RETURN_DELTA_PCT = 1.0


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CellRow(BaseModel):
    """한 (심볼, TF, 구간)의 census 한 줄 — 두 패스(24봉 vs 무기한) 나란히.

    `window_*`는 census의 공통 분모(못 박은 창)를 행마다 싣는다 — CSV만으로 재계산이
    닫히게(`--from-csv`).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    window_start: int
    window_end: int
    window_days: float

    # 깔때기 (base = 채택 24봉 패스)
    eligible: int
    filled_base: int
    trades_base: int
    no_touch: int
    expired: int
    invalidated: int
    cond_failed: int

    # 무기한 대기 패스
    filled_wait: int
    trades_wait: int

    # 핵심 지표 + 부가
    missed_reentries: int
    """`filled_wait − filled_base` — 만료로 놓친 재진입(재탭 복구분 제외)."""
    slot_busy_skips: int
    """`filled_base − trades_base` — 체결됐으나 칸이 이미 포지션 중이라 진입 안 됨."""
    peak_concurrent_waiting: int
    """한 칸에서 동시에 걸린 미체결 지정가 수의 최대(24봉 대기 지평 상한 근사)."""
    mean_concurrent_waiting: float

    # 수익 델타 (전부 baseline 낙관 렌즈 위 값 · 단위 %[퍼센트], 프랙션 아님)
    total_return_base: float
    total_return_wait: float
    mdd_base: float
    mdd_wait: float
    funding_coverage: float | None
    """펀딩 커버리지. 신규 3종목은 0.0(총수익 열 낙관 — 핵심 지표엔 무관)."""

    @property
    def days_per_entry(self) -> float | None:
        """평균 며칠에 한 번 진입(채택 24봉 · 단일 포지션). 진입 0이면 None."""
        return self.window_days / self.trades_base if self.trades_base else None

    @property
    def missed_share(self) -> float | None:
        """놓친 재진입 ÷ 채택 진입 수. 진입 0이면 None."""
        return self.missed_reentries / self.trades_base if self.trades_base else None


# --------------------------------------------------------------------------- #
# 순수 함수 (테스트가 여기를 고정한다)
# --------------------------------------------------------------------------- #


def peak_and_mean_concurrent(
    trigger_times: Sequence[int],
    *,
    horizon_ms: int,
    window_start: int,
    window_end: int,
) -> tuple[int, float]:
    """동시에 걸린 미체결 지정가 수의 (최대, 시간가중 평균).

    각 셋업의 대기 구간을 `[trigger, trigger + horizon_ms)`로 근사한다(24봉 대기 지평
    상한 — 실제로는 더 일찍 체결·무효화될 수 있으므로 **상한**이다). 반개구간이라 경계가
    맞닿은 두 구간은 동시에 세지 않는다. 창 밖은 잘라낸다.
    """
    if window_end <= window_start:
        raise ValueError(f"창이 비었습니다: [{window_start}, {window_end})")
    if horizon_ms <= 0:
        raise ValueError(f"horizon_ms는 양수여야 합니다: {horizon_ms}")
    events: list[tuple[int, int]] = []
    for t in trigger_times:
        lo = max(int(t), window_start)
        hi = min(int(t) + horizon_ms, window_end)
        if hi > lo:
            events.append((lo, +1))
            events.append((hi, -1))
    if not events:
        return 0, 0.0
    events.sort(key=lambda e: (e[0], e[1]))  # 같은 시각이면 닫힘(-1) 먼저 = 반개구간
    span = window_end - window_start
    peak = 0
    active = 0
    weighted = 0
    prev = events[0][0]
    for time, delta in events:
        if time > prev:
            weighted += active * (time - prev)
            prev = time
        active += delta
        peak = max(peak, active)
    return peak, weighted / span if span else 0.0


def _status_counts(statuses: Sequence[ZoneLimitStatus]) -> Counter[ZoneLimitStatus]:
    return Counter(statuses)


def aggregate_symbol_mean(rows: Sequence[CellRow], field: str) -> float:
    """심볼평균(단순) — 한 TF·구간 안 심볼들의 평균. 대상 없으면 0.0."""
    vals = [float(getattr(r, field)) for r in rows]
    return sum(vals) / len(vals) if vals else 0.0


def verdict(rows: Sequence[CellRow]) -> str:
    """GO/STOP 판정 — 만료 구멍이 대기 모델(§2)을 지을 만큼 큰가.

    자 둘: (1) 놓친 재진입이 채택 진입 대비 차지하는 비율(칸별을 합산), (2) 무기한 대기가
    수익도 더하는가(심볼평균 total_return 델타). 숫자는 전부 행에서 계산한다 — 문장에
    박아 두면 재실행 뒤 리포트가 거짓말을 한다(WAN-164 패턴).
    """
    total_trades = sum(r.trades_base for r in rows)
    total_missed = sum(r.missed_reentries for r in rows)
    share = total_missed / total_trades if total_trades else 0.0
    ret_base = aggregate_symbol_mean(rows, "total_return_base")
    ret_wait = aggregate_symbol_mean(rows, "total_return_wait")
    ret_delta = ret_wait - ret_base
    coords = (
        f"놓친 재진입 = 채택 진입의 **{share * 100:.1f}%** "
        f"({total_missed}건 / 진입 {total_trades}건) · 무기한 대기 수익 델타 "
        f"**{ret_delta:+.2f}%p**(심볼평균 total_return)"
    )
    material = ret_delta >= MATERIAL_RETURN_DELTA_PCT
    if share >= SIGNIFICANT_MISS_SHARE and material:
        return (
            f"**(a) GO — 구멍이 크고 수익도 는다.** {coords}. 만료로 놓치는 재진입이 "
            f"진입의 {SIGNIFICANT_MISS_SHARE * 100:.0f}% 이상이고 무기한 대기가 수익을 "
            f"{MATERIAL_RETURN_DELTA_PCT:.0f}%p 이상 더한다 — §2(형성-즉시 예약)를 지어 "
            "§3에서 대조할 값이 있다. ⚠️ 단 채택은 재-베이스라인 = 사용자 결정이고, 늘어난 "
            "체결은 전부 `baseline` 낙관 렌즈 위 값이다(큐 우선순위 실측은 WAN-98 Canceled)."
        )
    if share < NEGLIGIBLE_MISS_SHARE or not material:
        why = (
            f"놓친 재진입이 진입의 {NEGLIGIBLE_MISS_SHARE * 100:.0f}% 미만"
            if share < NEGLIGIBLE_MISS_SHARE
            else f"무기한 대기 수익 델타가 {MATERIAL_RETURN_DELTA_PCT:.0f}%p 미만"
        )
        return (
            f"**(b) STOP — 여기서 멈추고 기록만.** {coords}. {why}이라, 만료를 없애는 "
            "대기 모델을 지어도 실익이 작다(이슈 §1의 STOP 조건 · WAN-222의 '체결은 늘지만 "
            "수익은 안 따라온다'와 정합). §2/§3는 사용자가 이 크기를 알고도 원하면 별도로."
        )
    return (
        f"**(c) 경계 — 크기를 알고 결정할 것.** {coords}. 구멍이 무시할 수준은 아니나 "
        f"일상({SIGNIFICANT_MISS_SHARE * 100:.0f}% 이상)도 아니거나 수익 델타가 애매하다. "
        "§2 착수는 이 크기를 알고 내리는 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 실행 (칸별 두 패스)
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    """이 census가 돌린 엔진 지문 — 산출물만 봐도 어떤 엔진인지 드러나게(WAN-164 패턴)."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"combine_obs={OrderBlockParams().combine_obs}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, limit_valid_bars={p.limit_valid_bars}, "
        f"short_enabled={p.short_enabled}"
    )


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF) 칸 — 워커가 자기 데이터를 자기가 로드한다."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def run_cell(task: _Task, *, log: bool = True) -> CellRow | None:
    """한 칸을 24봉·무기한 두 패스로 돌려 census 한 줄을 낸다.

    두 패스는 같은 오더블록·같은 셋업 풀을 공유하고(OB 탐지 1회) `limit_valid_bars`만
    다르다 — 그래서 `filled_wait − filled_base`가 순수하게 만료의 몫이다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms, need_1m=True
    )
    if market.empty or market.df_1m.empty:
        return None
    ob = harness.detect_order_blocks(market, OrderBlockParams())
    cfg = harness.legacy_build_config(task.timeframe)
    params_base = harness.build_params()  # 채택 기본값(limit_valid_bars=24)
    params_wait = harness.build_params(limit_valid_bars=None)  # 무기한 대기

    sink_base: list[SetupDiagnostic] = []
    out_base = harness.run_once(
        market,
        params=harness.pin_invalidation_cancel(params_base),
        cfg=cfg,
        order_block_result=ob,
        setup_sink=sink_base,
    )
    out_wait = harness.run_once(
        market, params=harness.pin_invalidation_cancel(params_wait), cfg=cfg, order_block_result=ob
    )

    counts = _status_counts([d.status for d in sink_base])
    window_start, window_end = task.start_ms, task.end_ms
    window_days = (window_end - window_start) / 86_400_000.0
    horizon_ms = timeframe_to_ms(task.timeframe) * (params_base.limit_valid_bars or 0)
    peak, mean = peak_and_mean_concurrent(
        [d.trigger_time for d in sink_base],
        horizon_ms=horizon_ms,
        window_start=window_start,
        window_end=window_end,
    )
    stats_base = out_base.stats
    stats_wait = out_wait.stats
    assert stats_base is not None and stats_wait is not None  # run_once(single)이 보장
    row = CellRow(
        symbol=task.symbol,
        timeframe=task.timeframe,
        segment=harness.SEGMENT_FULL,
        window_start=window_start,
        window_end=window_end,
        window_days=window_days,
        eligible=stats_base.eligible,
        filled_base=stats_base.filled,
        trades_base=len(out_base.result.trades),
        no_touch=counts.get(ZoneLimitStatus.NO_TOUCH, 0),
        expired=counts.get(ZoneLimitStatus.CANCELLED_EXPIRED, 0),
        invalidated=counts.get(ZoneLimitStatus.CANCELLED_INVALIDATED, 0),
        cond_failed=counts.get(ZoneLimitStatus.CANCELLED_CONDITION_FAILED, 0),
        filled_wait=stats_wait.filled,
        trades_wait=len(out_wait.result.trades),
        missed_reentries=stats_wait.filled - stats_base.filled,
        slot_busy_skips=stats_base.filled - len(out_base.result.trades),
        peak_concurrent_waiting=peak,
        mean_concurrent_waiting=mean,
        total_return_base=out_base.result.metrics.total_return * 100.0,
        total_return_wait=out_wait.result.metrics.total_return * 100.0,
        mdd_base=out_base.result.metrics.max_drawdown * 100.0,
        mdd_wait=out_wait.result.metrics.max_drawdown * 100.0,
        funding_coverage=out_base.result.metrics.funding_coverage,
    )
    if log:
        print(
            f"[wan223] {task.symbol} {task.timeframe}: eligible={row.eligible} "
            f"expired={row.expired} filled {row.filled_base}→{row.filled_wait} "
            f"(miss={row.missed_reentries}) trades {row.trades_base}→{row.trades_wait} "
            f"ret {row.total_return_base:.1f}%→{row.total_return_wait:.1f}%",
            flush=True,
        )
    return row


def _run_task_logged(task: _Task) -> CellRow | None:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[CellRow]:
    """9종목 × TF 칸을 돌아 census 행을 모은다.

    `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121) — (심볼, TF) 단위로만 갈라 제출
    순서대로 모으므로 직렬과 행·순서가 같다.
    """
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        results = [run_cell(task, log=log) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            results = list(executor.map(_run_task_logged, tasks))
    return [r for r in results if r is not None]


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[CellRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CellRow.model_fields))


def cells_from_csv(path: Path) -> list[CellRow]:
    frame = pd.read_csv(path)
    return [CellRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _cell_table(rows: Sequence[CellRow], timeframe: str) -> list[str]:
    scoped = sorted((r for r in rows if r.timeframe == timeframe), key=lambda r: r.symbol)
    lines = [
        f"### {timeframe}",
        "",
        "| 심볼 | eligible | 만료 | filled(24→∞) | 놓친 재진입 | 진입(24→∞) | "
        "진입 빈도 | 슬롯거부 | 동시대기(peak) | ret(24→∞) |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for r in scoped:
        dpe = f"{r.days_per_entry:.1f}일" if r.days_per_entry is not None else "—"
        fund = "†" if r.symbol in FUNDING_GAP_SYMBOLS else ""
        lines.append(
            f"| {_short(r.symbol)}{fund} | {r.eligible} | {r.expired} | "
            f"{r.filled_base}→{r.filled_wait} | **{r.missed_reentries}** | "
            f"{r.trades_base}→{r.trades_wait} | {dpe} | {r.slot_busy_skips} | "
            f"{r.peak_concurrent_waiting} | {r.total_return_base:.1f}%→{r.total_return_wait:.1f}% |"
        )
    # TF 소계
    sub = [r for r in scoped]
    if sub:
        tot_trades = sum(r.trades_base for r in sub)
        tot_miss = sum(r.missed_reentries for r in sub)
        share = tot_miss / tot_trades if tot_trades else 0.0
        ret_b = aggregate_symbol_mean(sub, "total_return_base")
        ret_w = aggregate_symbol_mean(sub, "total_return_wait")
        lines += [
            "",
            f"**{timeframe} 합계**: 놓친 재진입 {tot_miss}건 / 진입 {tot_trades}건 "
            f"= **{share * 100:.1f}%** · 심볼평균 total_return {ret_b:.1f}% → {ret_w:.1f}% "
            f"(**{ret_w - ret_b:+.2f}%p**).",
        ]
    return lines


def build_summary_markdown(rows: Sequence[CellRow], *, cells_csv: Path) -> str:
    timeframes = sorted({r.timeframe for r in rows}, key=lambda t: timeframe_to_ms(t), reverse=True)
    window = next(iter({(r.window_start, r.window_end) for r in rows}), (0, 0))
    lines = [
        "# WAN-223 §1 — 지정가 주문 관리 census (만료 후 재진입 구멍 · GO/STOP 게이트)",
        "",
        "**성격** 측정 전용. 채택 기본값 그대로(`ConfluenceParams()`·`OrderBlockParams()`) "
        "돌리며 옛 핀은 하나도 물려받지 않는다. 렌즈 `baseline` 단독(WAN-128) · 못 박은 "
        "6년 창(WAN-182) · 기본값·토대 불변(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "## 이 census가 돌린 엔진",
        "",
        f"`{describe_engine()}` + 펀딩비 반영.",
        "",
        "## 방법 — 두 패스(유일 차이 = 유효기간)",
        "",
        "같은 셋업 풀(같은 오더블록·같은 `retap_signals`)을 두 번 돌린다: `base`"
        "(`limit_valid_bars=24`, 채택) vs `wait`(`=None`, 무기한 대기 = 형성-즉시 예약의 "
        "체결 대리). **핵심 지표 = `filled_wait − filled_base`** = 만료를 없애면 늘어나는 "
        "체결. 두 패스의 셋업 집합이 같아 이 차이는 **재탭이 이미 복구한 체결을 뺀** 순증 "
        "— 사용자가 짚은 「만료 후 재탭 실패로 놓친 재진입」의 크기다.",
        "",
        "⚠️ **15m은 여기에 없다** — 무기한 패스가 셋업을 무효화까지 스캔해 15m·6년에서 "
        "초선형으로 느리다(단일 셀 >2분, 무기한은 더). 15m 전 구간은 무거운 후속 컴퓨트로 "
        "남긴다. 4h·1h가 두 작업 TF의 대표이고, 15m 방향은 그 위에서 판단한다.",
        "",
        f"재현: `uv run python -m backtest.wan223_limit_order_census --tf "
        f"{','.join(DEFAULT_TIMEFRAMES)} --jobs 6` (요약만: `--from-csv`). 원자료: `{cells_csv}`. "
        f"창=[{window[0]}, {window[1]}).",
        "",
        "## 칸별 census (§1 + §1b 진입 빈도)",
        "",
        "`만료` = 24봉 유효기간으로 취소된 셋업 · `놓친 재진입` = `filled_wait − filled_base` "
        "· `진입 빈도` = 평균 며칠에 한 번(채택 24봉·단일 포지션) · `슬롯거부` = 체결됐으나 "
        "칸이 포지션 중이라 진입 못한 수 · `동시대기(peak)` = 한 칸 동시 미체결 지정가 최대"
        "(24봉 지평 상한 근사). †=신규 종목(펀딩 0행 → total_return 낙관, 핵심 지표엔 무관).",
        "",
    ]
    for tf in timeframes:
        lines += _cell_table(rows, tf)
        lines.append("")
    lines += [
        "## 부가 — 명목 상한 거부 (북 소관)",
        "",
        "「명목 한도 체결 거부」는 칸을 가로지르는 **레버리지 북**(cap_only 5배, WAN-213)에서만 "
        "나며 이 칸별 census엔 없다. WAN-180 실측: **동시 최대 6칸 · 명목 스킵 6년 7건**으로 "
        "드물다 — 「가까운 주문 우선」 배분(§2)이 실제로 바꿀 여지는 그만큼 작다. 슬롯거부(위 "
        "표)는 칸 **안** 단일 포지션 경합이라 성격이 다르다(그건 북이 아니라 익절/손절 회전 문제).",
        "",
        "## 판정 — 대기 모델(§2)을 지을 값이 있는가",
        "",
        verdict(rows),
        "",
        f"판정 자: 놓친 재진입 ÷ 진입 ≥ {SIGNIFICANT_MISS_SHARE * 100:.0f}% **그리고** 수익 "
        f"델타 ≥ {MATERIAL_RETURN_DELTA_PCT:.0f}%p → (a) GO · < {NEGLIGIBLE_MISS_SHARE * 100:.0f}% "
        f"**또는** 수익 델타 < {MATERIAL_RETURN_DELTA_PCT:.0f}%p → (b) STOP · 사이 → (c). "
        "문턱은 코드 상수다(`SIGNIFICANT_MISS_SHARE`·`NEGLIGIBLE_MISS_SHARE`·"
        "`MATERIAL_RETURN_DELTA_PCT`).",
        "",
        "⚠️ **이 표는 채택 근거가 아니라 크기 조사다** — 만료 구멍이 커도 「대기 모델을 "
        "채택하라」가 아니고(그 손익·라이브 충실도는 §2/§3·WAN-45 소관), 「엣지 없음」"
        "(WAN-84/88/111/114/124/151)도 그대로다. 늘어난 체결은 전부 `baseline`(닿으면 체결) "
        "낙관 렌즈 위 값이라 큐 "
        "우선순위(WAN-98 Canceled) 실측 없이는 이점 검증이 반쪽이다. **기본값·토대 불변**"
        "(측정 전용 · `ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-223 §1 지정가 주문 관리 census")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_md = Path(args.out_md)

    if args.from_csv:
        rows = cells_from_csv(out_cells)
        print(f"[wan223] CSV에서 {len(rows)}행 로드 — 백테스트 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(rows).to_csv(out_cells, index=False)
        print(f"[wan223] census {len(rows)}행 → {out_cells}")

    if not rows:
        print("[wan223] 행이 없습니다 — 데이터 창을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(build_summary_markdown(rows, cells_csv=out_cells), encoding="utf-8")
    print(f"[wan223] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
