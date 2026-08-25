"""손절폭 가드(0.3%) 탈락률은 어디서 오는가 — 레짐 · 존 천장 · 체결 모델 (WAN-328 §1·§2).

## 이 모듈이 답하는 질문

`alphablock fills --day 2026-08-17`이 서버에서 잰 값: **체결 13건 중 12건(92%)이 손절폭
가드에 걸렸다**(손절폭 0.03~0.26%). 그런데 같은 가드를 백테로 잰 값은 **15m 38.5%**
(WAN-197) · **44~46%**(WAN-161)다. 이슈는 그 두 배 격차를 「라이브 대 백테의 갈림」으로 읽고
가설을 하나 세웠다 — 라이브는 봉내 라이브 밴드를 **틱마다** 다시 계산하니(WAN-132/256) 진입가가
존 무효화 경계에 더 바짝 붙어 1R이 좁아지는 것 아니냐.

이 모듈은 그 가설을 **기각하고** 격차의 출처를 다른 데서 지목한다. 두 축을 함께 잰다:

* **§A 레짐 축** — 같은 채택 엔진의 가드 탈락률을 **월별로** 낸다. WAN-197의 38.5%는 **6년
  평균**이고, 탈락률은 변동성 레짐을 따라 크게 움직인다. 「6년 평균」과 「저변동성 하루」를
  나란히 놓으면 두 배가 벌어지는 것이 정상인지 아닌지가 이 열로 판정된다.
* **§B 구조 천장 축** — 진입가는 존 안으로 클램프되고(`deviation_entry_price`) 손절 참조가는
  존 무효화 경계다. 따라서 **손절폭 ≤ 존 높이 + 오프셋**이 항상 성립한다. 즉 셋업마다
  `존 높이 / 진입가 + 2bp`라는 **구조적 천장**이 있고, 그 천장이 0.3%보다 낮으면 진입가를
  어디에 잡아도 가드에 걸린다. 이 열이 「존이 얇아서(불가피)」와 「진입가가 깊어서(체결 모델
  의존)」를 가른다 — 이슈 §1이 요구한 *갈리는 지점이 진입가인지 무효화 경계인지 존 자체인지*.
* **§C 체결 모델 축(= §2 가설 검정)** — 엔진은 봉내 밴드의 표본으로 **1분봉 종가**를 쓰면서
  터치는 **저가**로 판정한다(롱). 라이브 러너는 틱마다 표본과 터치를 **같은 가격으로 동시에**
  굴리므로 그 비대칭이 없다. `observe_path_fill`(WAN-328 옵트인, 측정 전용)이 같은 체결 봉을
  틱 추종으로 다시 풀어 `p <= 지정가(p)`의 고정점을 내고, 두 체결가의 차이(bp)와 **그 차이가
  가드 판정을 뒤집는 셋업 수**를 센다. 가설이 참이면 틱 추종 체결가가 계통적으로 경계 쪽으로
  붙고 탈락률이 올라야 한다.

## 좌표

채택 좌표 그대로 — 12종목 × 15m·1h·2h·4h × 못 박은 6년 창(2020-09-15~2026-07-22) ·
`baseline` 단독 · **핀 하나도 없음**(WAN-305, `ConfluenceParams()`·`OrderBlockParams()`).
가드 값도 채택값 0.3%(`PositionSizingParams.min_stop_distance_fraction`) 그대로 읽는다 — 이 모듈은
가드를 **재는** 것이지 바꾸지 않는다.

## 🚨 이 모듈이 재지 **못하는** 것

* **사고 당일(2026-08-17)** — 로컬 `ohlcv.db`는 상위TF가 채택 창 끝(2026-07-22)에서 멈추고,
  `live_limit_orders`는 **0행**이다(페이퍼 러너는 서버에서 돈다). 그 날의 라이브 대 백테
  **같은 셋업 조인**(§1 표)은 서버에서만 나온다 — 그 도구가 `live.stop_width_parity`이고
  CLI는 `alphablock stop-width`다. 이 모듈은 그 표의 **백테 쪽 기준선**과 **메커니즘**을 낸다.
* **틱 데이터** — §C는 1분봉 OHLC 근사다(WAN-98 Canceled · WAN-246 선례). 같은 봉 안의 체결가
  차이만 재고 틱 모델이 **더 이른 봉**에서 체결했을 가능성은 재지 않는다.
* **가드를 바꿨을 때의 손익** — WAN-76/79/197 소관이고 **재-베이스라인 = 사용자 결정**이다.

## 성격

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`LeverageBookParams()`·가드 0.3% 그대로 ·
실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지). 전부 `baseline` 렌즈 위의 값이고
**「엣지 없음」(WAN-84/88/111/114/124/151/201) 불변**(다른 질문).
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.zone_limit_backtest import SetupDiagnostic, build_zone_limit_candidates
from execution.sizing import PositionSizingParams

__all__ = [
    "GUARD_FRACTION",
    "CellWork",
    "SetupRow",
    "SummaryRow",
    "build_summary_markdown",
    "run_cell",
    "run_grid",
    "setup_rows",
    "summarize",
]

GRID_CSV = Path("backtest/reports/wan328_stop_width_setups.csv")
SUMMARY_CSV = Path("backtest/reports/wan328_stop_width_monthly.csv")
SUMMARY_MD = Path("backtest/reports/wan328_stop_width_summary.md")

#: 손절폭 가드 = 채택값을 **읽는다**(리터럴 0.003을 다시 적지 않는다 — 값이 바뀌면 이 표가
#: 조용히 옛 가드를 재게 된다). WAN-76/79 소관이고 이 모듈은 관측자다.
GUARD_FRACTION: float = PositionSizingParams().min_stop_distance_fraction


# --------------------------------------------------------------------------- #
# 셀 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CellWork:
    """한 (심볼, TF) 셀의 실행 단위."""

    symbol: str
    timeframe: str
    start: str
    end: str
    db_path: str = harness.DB_PATH


@dataclass(frozen=True)
class SetupRow:
    """체결된 셋업 하나의 손절폭 해부 — 이 표의 원자 단위."""

    symbol: str
    timeframe: str
    month: str
    """체결 봉의 UTC 연-월(`YYYY-MM`). 레짐 축(§A)의 시간 눈금이다."""
    trigger_time: int
    is_long: bool
    entry_price: float
    """엔진 체결가(봉내 라이브 밴드가 1분봉 **종가** 표본으로 낸 지정가)."""
    stop_price: float
    """손절 참조가 = 진입 근거 오더블록의 무효화 경계."""
    stop_width_pct: float
    """손절폭 = `|진입가 − 손절가| / 진입가 × 100`. 가드는 이 값을 0.3%와 비교한다."""
    zone_ceiling_pct: float | None
    """구조적 천장 = `존 높이 / 진입가 × 100`(+ 오프셋만큼 더 벌 수 있다).

    진입가가 존 근단(가장 유리한 자리)이었을 때의 손절폭이다. 이 값이 가드보다 낮으면
    **진입가를 어디에 잡아도** 가드에 걸린다 = 「존이 얇아서」 부류."""
    guard_passed: bool
    path_fill_price: float | None
    """§C: 같은 봉을 틱 추종으로 봤을 때의 체결가(`observe_path_fill`). 그 봉 경로 안에서
    체결 조건이 성립하지 않으면 `None` — 엔진 쪽이 **더 관대**하다는 뜻이다."""
    path_stop_width_pct: float | None
    path_guard_passed: bool | None


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC).strftime("%Y-%m")


def setup_rows(
    symbol: str, timeframe: str, diagnostics: Sequence[SetupDiagnostic]
) -> list[SetupRow]:
    """체결 셋업 진단을 손절폭 해부 행으로 바꾼다 (순수 함수 — 테스트 진입점)."""
    rows: list[SetupRow] = []
    for diag in diagnostics:
        entry = diag.limit_price
        if not diag.filled or entry is None or entry <= 0.0:
            continue
        width = abs(entry - diag.stop_price) / entry * 100.0
        ceiling = (diag.zone_height / entry * 100.0) if diag.zone_height is not None else None
        path = diag.path_fill_price
        path_width = (
            abs(path - diag.stop_price) / path * 100.0 if path is not None and path > 0.0 else None
        )
        rows.append(
            SetupRow(
                symbol=symbol,
                timeframe=timeframe,
                month=_month(diag.trigger_time),
                trigger_time=int(diag.trigger_time),
                is_long=diag.side.name == "LONG",
                entry_price=float(entry),
                stop_price=float(diag.stop_price),
                stop_width_pct=width,
                zone_ceiling_pct=ceiling,
                guard_passed=width >= GUARD_FRACTION * 100.0,
                path_fill_price=path,
                path_stop_width_pct=path_width,
                path_guard_passed=(
                    None if path_width is None else path_width >= GUARD_FRACTION * 100.0
                ),
            )
        )
    return rows


def run_cell(work: CellWork) -> list[SetupRow]:
    """한 셀의 체결 셋업 전부를 해부한다. 채택 엔진 그대로 · 핀 없음(WAN-305)."""
    from backtest.run import parse_date_ms  # 지연 import(사이클 회피 — book_cli 선례)

    market = harness.load_market_data(
        work.symbol,
        work.timeframe,
        start_ms=parse_date_ms(work.start),
        end_ms=parse_date_ms(work.end),
        need_1m=True,
        funding=False,
        db_path=work.db_path,
    )
    if market.empty or market.df_1m.empty:
        return []
    sink: list[SetupDiagnostic] = []
    build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        work.timeframe,
        params=harness.pin_invalidation_cancel(harness.build_params(fill=harness.BASELINE_FILL)),
        cfg=harness.legacy_build_config(work.timeframe),
        order_block_result=harness.detect_order_blocks(market),
        setup_sink=sink,
        # §C: 같은 봉의 틱 추종 체결가를 함께 관측한다(측정 전용 — 체결·손익 불변).
        observe_path_fill=True,
    )
    return setup_rows(work.symbol, work.timeframe, sink)


def _cell_worker(work: CellWork) -> tuple[list[SetupRow], str]:
    rows = run_cell(work)
    return rows, f"{work.symbol} {work.timeframe} setups={len(rows)}"


def run_grid(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    db_path: str = harness.DB_PATH,
    jobs: int = 1,
    log: bool = True,
) -> list[SetupRow]:
    """격자 실행. `jobs`는 **순수 성능 노브**다(직렬과 행이 같다 — WAN-121 관행)."""
    works = [
        CellWork(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start=start,
            end=end,
            db_path=db_path,
        )
        for timeframe in timeframes
        for symbol in symbols
    ]
    rows: list[SetupRow] = []
    done = 0

    def _absorb(cell: list[SetupRow], note: str) -> None:
        nonlocal done
        done += 1
        rows.extend(cell)
        if log:
            print(f"[wan328] ({done}/{len(works)}) {note}", flush=True)

    if jobs and jobs != 1:
        with ProcessPoolExecutor(max_workers=jobs if jobs > 0 else None) as pool:
            futures = [pool.submit(_cell_worker, work) for work in works]
            for fut in as_completed(futures):
                cell, note = fut.result()
                _absorb(cell, note)
    else:
        for work in works:
            cell, note = _cell_worker(work)
            _absorb(cell, note)
    return sorted(rows, key=lambda r: (r.timeframe, r.symbol, r.trigger_time))


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SummaryRow:
    """한 (TF, 월) 묶음의 가드 탈락 해부 — §A 레짐 축의 행."""

    timeframe: str
    month: str
    """`ALL`이면 창 전체(= WAN-197식 「6년 평균」과 같은 자)."""
    filled: int
    guard_rejected: int
    guard_reject_rate: float
    ceiling_below_guard: int
    """구조적 천장이 가드보다 낮은 셋업 수 = **진입가와 무관하게 불가피한 탈락**."""
    ceiling_share_of_rejects: float | None
    """탈락 중 「존이 얇아서」의 몫. 나머지가 「진입가가 깊어서」다."""
    median_stop_width_pct: float | None
    p10_stop_width_pct: float | None
    p90_stop_width_pct: float | None
    median_zone_ceiling_pct: float | None
    path_observed: int
    """§C 틱 추종 체결가가 나온 셋업 수(엔진이 체결시킨 것 중)."""
    path_unfilled: int
    """엔진은 체결시켰는데 **틱 추종으로는 그 봉에서 안 닿는** 셋업 수."""
    median_path_delta_bps: float | None
    """|틱 추종 체결가 − 엔진 체결가| 중앙값(bp)."""
    path_deeper_share: float | None
    """틱 추종 체결가가 **경계 쪽으로 더 깊은**(= 손절폭이 더 좁은) 셋업의 비율.
    가설이 참이면 1에 가깝고 손절폭도 계통적으로 줄어야 한다."""
    path_guard_reject_rate: float | None
    """틱 추종 체결가로 판정한 가드 탈락률(관측된 셋업만)."""
    engine_guard_reject_rate_on_path_subset: float | None
    """같은 부분집합을 엔진 체결가로 판정한 탈락률 — 위 열과 **짝으로만** 읽는다."""


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _summarize_group(timeframe: str, month: str, rows: Sequence[SetupRow]) -> SummaryRow:
    filled = len(rows)
    rejected = [r for r in rows if not r.guard_passed]
    unavoidable = [
        r
        for r in rows
        if r.zone_ceiling_pct is not None and r.zone_ceiling_pct < GUARD_FRACTION * 100.0
    ]
    widths = [r.stop_width_pct for r in rows]
    ceilings = [r.zone_ceiling_pct for r in rows if r.zone_ceiling_pct is not None]
    observed = [r for r in rows if r.path_stop_width_pct is not None]
    deltas = [
        abs(r.path_fill_price - r.entry_price) / r.entry_price * 10_000.0
        for r in observed
        if r.path_fill_price is not None
    ]
    deeper = [
        r
        for r in observed
        if r.path_stop_width_pct is not None and r.path_stop_width_pct < r.stop_width_pct
    ]
    return SummaryRow(
        timeframe=timeframe,
        month=month,
        filled=filled,
        guard_rejected=len(rejected),
        guard_reject_rate=(len(rejected) / filled) if filled else 0.0,
        ceiling_below_guard=len(unavoidable),
        ceiling_share_of_rejects=(len(unavoidable) / len(rejected)) if rejected else None,
        median_stop_width_pct=_median(widths),
        p10_stop_width_pct=_quantile(widths, 0.10),
        p90_stop_width_pct=_quantile(widths, 0.90),
        median_zone_ceiling_pct=_median(ceilings),
        path_observed=len(observed),
        path_unfilled=filled - len(observed),
        median_path_delta_bps=_median(deltas),
        path_deeper_share=(len(deeper) / len(observed)) if observed else None,
        path_guard_reject_rate=(
            sum(1 for r in observed if r.path_guard_passed is False) / len(observed)
            if observed
            else None
        ),
        engine_guard_reject_rate_on_path_subset=(
            sum(1 for r in observed if not r.guard_passed) / len(observed) if observed else None
        ),
    )


def summarize(rows: Sequence[SetupRow]) -> list[SummaryRow]:
    """(TF × 월) 행 + TF마다 창 전체 `ALL` 행. `ALL`이 WAN-197식 평균과 같은 자다."""
    out: list[SummaryRow] = []
    for timeframe in sorted({r.timeframe for r in rows}, key=harness.DEFAULT_TIMEFRAMES.index):
        cell = [r for r in rows if r.timeframe == timeframe]
        out.append(_summarize_group(timeframe, "ALL", cell))
        for month in sorted({r.month for r in cell}):
            out.append(_summarize_group(timeframe, month, [r for r in cell if r.month == month]))
    return out


# --------------------------------------------------------------------------- #
# 입출력
# --------------------------------------------------------------------------- #


def _write(rows: Sequence[SetupRow] | Sequence[SummaryRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([asdict(r) for r in rows]).to_csv(path, index=False)


def setups_from_csv(path: Path = GRID_CSV) -> list[SetupRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    records = frame.astype(object).where(pd.notna(frame), None).to_dict("records")
    return [SetupRow(**record) for record in records]


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _fmt(value: float | None, digits: int = 3, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _verdict_path(rows: Sequence[SummaryRow]) -> str:
    """§2 가설 판정: 틱 재산정이 진입가를 경계에 붙이나 — 지지/기각."""
    alls = [r for r in rows if r.month == "ALL" and r.path_observed > 0]
    if not alls:
        return "⚠️ 판정 불가 — 틱 추종 체결가가 관측된 셋업이 없다."
    worse = [
        r
        for r in alls
        if r.path_guard_reject_rate is not None
        and r.engine_guard_reject_rate_on_path_subset is not None
        and r.path_guard_reject_rate > r.engine_guard_reject_rate_on_path_subset + 0.05
    ]
    deltas = [r.median_path_delta_bps for r in alls if r.median_path_delta_bps is not None]
    biggest = max(deltas) if deltas else 0.0
    if worse:
        return (
            f"**(a) 지지** — {len(worse)}/{len(alls)} TF에서 틱 추종 체결가의 가드 탈락률이 "
            f"엔진보다 5%p 넘게 높다(체결가 차이 중앙값 최대 {biggest:.2f}bp)."
        )
    return (
        f"**(b) 기각** — 어느 TF에서도 틱 추종 체결가가 가드 탈락률을 5%p 넘게 올리지 않는다"
        f"(체결가 차이 중앙값 최대 **{biggest:.2f}bp**). 틱 재산정은 진입가를 존 무효화 경계 "
        f"쪽으로 계통적으로 붙이지 않는다."
    )


def build_summary_markdown(rows: Sequence[SummaryRow]) -> str:
    guard_pct = GUARD_FRACTION * 100.0
    lines: list[str] = [
        "# WAN-328 — 손절폭 가드 탈락률의 출처: 레짐 · 존 천장 · 체결 모델",
        "",
        f"재현: `uv run python -m backtest.wan328_stop_width_parity`(가드 {guard_pct:.1f}% · "
        "채택 좌표 · 핀 없음). 요약만 재생성은 `--from-csv`.",
        "",
        "⚠️ **측정 전용 — 어떤 기본값도 바꾸지 않았다**(가드는 WAN-76/79 소관 · 재-베이스라인 = "
        "사용자 결정). 전부 `baseline` 렌즈 위의 값이고 「엣지 없음」(WAN-84/88/111/114/124/"
        "151/201) 불변.",
        "",
        "## §A 레짐 축 — 가드 탈락률은 창에 따라 크게 움직인다",
        "",
        "`ALL` 행이 WAN-197/161이 인용한 「6년 평균」과 같은 자다. 월 행이 그 평균이 감추는 "
        "폭을 보여 준다.",
        "",
        "| TF | 구간 | 체결 | 가드 탈락 | 탈락률 | 손절폭 p10 | 중앙값 | p90 | 존 천장 중앙값 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for row in rows:
        if row.month != "ALL":
            continue
        lines.append(
            f"| {row.timeframe} | 전체 | {row.filled} | {row.guard_rejected} | "
            f"{_pct(row.guard_reject_rate)} | {_fmt(row.p10_stop_width_pct)}% | "
            f"{_fmt(row.median_stop_width_pct)}% | {_fmt(row.p90_stop_width_pct)}% | "
            f"{_fmt(row.median_zone_ceiling_pct)}% |"
        )
    monthly = [r for r in rows if r.month != "ALL" and r.filled >= 20]
    if monthly:
        best = min(monthly, key=lambda r: r.guard_reject_rate)
        worst = max(monthly, key=lambda r: r.guard_reject_rate)
        lines += [
            "",
            f"📌 **월별 진폭(체결 20건 이상 달만)** — 최저 {best.timeframe} {best.month} "
            f"**{_pct(best.guard_reject_rate)}**({best.filled}건) ↔ 최고 {worst.timeframe} "
            f"{worst.month} **{_pct(worst.guard_reject_rate)}**({worst.filled}건). "
            "**한 숫자로 「가드 탈락률」을 말할 수 없다** — 창을 밝히지 않은 인용은 뜻이 없다.",
        ]
    lines += [
        "",
        "## §B 구조 천장 — 탈락의 몫은 「존이 얇아서」인가 「진입가가 깊어서」인가",
        "",
        "진입가는 존 안으로 클램프되고 손절 참조가는 존 무효화 경계라 **손절폭 ≤ 존 높이 + "
        "오프셋**이다. `존 높이 / 진입가`가 가드보다 낮으면 진입가를 **어디에 잡아도** 걸린다.",
        "",
        "| TF | 가드 탈락 | 그중 존이 얇아서 | 몫 |",
        "| -- | --: | --: | --: |",
    ]
    for row in rows:
        if row.month != "ALL":
            continue
        lines.append(
            f"| {row.timeframe} | {row.guard_rejected} | {row.ceiling_below_guard} | "
            f"{_pct(row.ceiling_share_of_rejects)} |"
        )
    lines += [
        "",
        "## §C 체결 모델 (= 이슈 §2 가설 검정)",
        "",
        "엔진은 밴드 표본으로 1분봉 **종가**를 쓰면서 터치는 **저가**로 판정한다(롱). 라이브는 "
        "틱마다 둘을 같은 가격으로 굴린다. 같은 체결 봉을 틱 추종으로 다시 푼 값과 비교한다.",
        "",
        "| TF | 관측 | 틱으론 미체결 | 체결가차 중앙값 | 더 깊은 비율 | 탈락률(틱) | "
        "탈락률(엔진, 같은 부분집합) |",
        "| -- | --: | --: | --: | --: | --: | --: |",
    ]
    for row in rows:
        if row.month != "ALL":
            continue
        lines.append(
            f"| {row.timeframe} | {row.path_observed} | {row.path_unfilled} | "
            f"{_fmt(row.median_path_delta_bps, 2)}bp | {_pct(row.path_deeper_share)} | "
            f"{_pct(row.path_guard_reject_rate)} | "
            f"{_pct(row.engine_guard_reject_rate_on_path_subset)} |"
        )
    lines += [
        "",
        f"**판정** — {_verdict_path(rows)}",
        "",
        "⚠️ **1분봉 OHLC 근사이지 틱이 아니다**(WAN-98 Canceled · WAN-246 선례) — 같은 봉 안의 "
        "체결가 차이만 재고, 틱 모델이 **더 이른 봉**에서 체결했을 가능성은 재지 않는다. "
        "「틱으론 미체결」 열은 그 반대 방향의 관측이다: 엔진의 종가-표본/저가-터치 비대칭이 "
        "**라이브보다 관대한** 체결을 내주는 경우가 있다.",
        "",
        "## 라이브 쪽은 이 표에 없다",
        "",
        "로컬 `ohlcv.db`는 상위TF가 채택 창 끝에서 멈추고 `live_limit_orders`는 0행이다"
        "(페이퍼 러너는 서버에서 돈다). 사고 당일의 **같은 셋업 조인**(이슈 §1)과 최근 N일 "
        "**거부 사유·손절폭 분포**(§3)는 서버에서 `alphablock stop-width`로 낸다"
        "(`live.stop_width_parity`).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-328 손절폭 가드 탈락률 해부(레짐 · 존 천장 · 체결 모델)"
    )
    parser.add_argument("--symbols", nargs="+", default=None, help="대상 심볼(기본 채택 12종목)")
    parser.add_argument("--timeframes", nargs="+", default=None, help="대상 TF(기본 채택 4TF)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=None, help="셀 병렬(순수 성능 노브)")
    parser.add_argument(
        "--append", action="store_true", help="기존 CSV에 이어붙인다(같은 셀은 덮어쓴다)"
    )
    parser.add_argument(
        "--from-csv", action="store_true", help="격자를 다시 돌지 않고 요약만 재생성"
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = setups_from_csv()
        if not rows:
            print(f"[wan328] {GRID_CSV} 가 없습니다 — 먼저 격자를 돌리세요.")
            return 1
    else:
        fresh = run_grid(
            args.symbols or harness.DEFAULT_SYMBOLS,
            args.timeframes or harness.DEFAULT_TIMEFRAMES,
            start=args.start,
            end=args.end,
            jobs=args.jobs if args.jobs is not None else harness.default_jobs(),
        )
        rows = fresh
        if args.append:
            replaced = {(r.timeframe, r.symbol) for r in fresh}
            kept = [r for r in setups_from_csv() if (r.timeframe, r.symbol) not in replaced]
            rows = sorted([*kept, *fresh], key=lambda r: (r.timeframe, r.symbol, r.trigger_time))
        _write(rows, GRID_CSV)

    summary = summarize(rows)
    _write(summary, SUMMARY_CSV)
    SUMMARY_MD.write_text(build_summary_markdown(summary), encoding="utf-8")
    print(f"[wan328] 셋업 {len(rows)}행 → {GRID_CSV}")
    print(f"[wan328] 요약 {len(summary)}행 → {SUMMARY_CSV} · {SUMMARY_MD}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
