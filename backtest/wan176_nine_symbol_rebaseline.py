"""WAN-176: 9종목 6년 재-베이스라인 측정 — 엣지·존폭·TF 판정을 새 창에서 다시 낸다.

## 동기 — 사용자 결정 (2026-07-23)

사용자가 **"9종목 + 6년 둘 다"** 로 재-베이스라인하기로 결정했다. 지금 채택 규칙의 모든
판정은 **6종목 × 약 3년(못 박은 창 2023-07-14 ~ 2026-07-15)** 위에서 나왔다. WAN-175가
신규 3종목(DOGE·LINK·LTC)의 1분봉 6년 수집 + 9종목 전부의 native 15m/1h/4h/1d 집계를
끝냈으므로, 이 모듈이 그 데이터 위에서 기존 판정 네 갈래를 재측정한다:

1. **성과 격자**(§1) — 9종목 × 4TF × 따뜻(주)+차가움(스트레스) OOS(WAN-166 정본).
   TF 순위(WAN-107/130)와 **4h·1d 표본 미달 해소 여부(WAN-109 흡수)** 가 여기서 나온다.
2. **매칭 널**(§2) — WAN-151/164 계열(볼린저 무력화 축). "진입 규칙이 무작위와
   구분되는가"를 9종목 6년에서 다시 묻는다.
3. **존폭 필터 매칭 대조군**(§3) — WAN-142 계열(표본 축소 관문). 필터가 무작위
   부분표본을 이기는지를 9종목 6년에서 다시 묻는다.
4. **다중 대 단일 포지션**(§4) — WAN-130 계열(`--positions single,3`).

## 창·유니버스 — 이 모듈의 좌표

- **9종목** = 기존 6종목(BTC·ETH·SOL·BNB·XRP·TRX) + 신규 3종목(DOGE·LINK·LTC).
- **못 박은 창 = 2020-09-15 ~ 2026-07-22.** 공통 시작점은 **SOL 상장(2020-09-14)이
  하한**이다 — 다른 8종목은 2020-07-23부터 있지만, 창을 종목마다 다르게 잡으면 IS/OOS
  경계가 종목마다 다른 달력 시각에 떨어져 심볼평균이 "다른 기간의 평균"이 된다(WAN-111이
  6종목을 동일한 봉 수로 맞춘 것과 같은 원칙). 실효 창은 **약 5.85년**이고, 8종목의 앞쪽
  약 2개월은 일부러 쓰지 않는다.
- **오늘 채택 엔진 그대로** — 오프셋 2bp × `intrabar_live` × `unconditional` ×
  `combine_obs=False` × 존폭 필터 1.28 × 롱 온리 × `baseline` 단독(WAN-128). 핀을 하나도
  쓰지 않으므로(§3의 이중 필터 방지 제외) 채택 기본값이 움직이면 이 표도 따라간다.

## ⚠️ 알려진 한계 (데이터)

- **펀딩비는 기존 6종목 × 2021-07-01 이후만 있다** — 신규 3종목은 펀딩 데이터가 0건이라
  그 종목 행은 펀딩비 미반영이고(`funding_coverage` 열이 그대로 드러낸다), 기존 6종목도
  창 앞쪽 약 9.5개월은 커버리지 밖이다. WAN-91 실측으로 펀딩의 영향은 대체로
  total_return ±0.1~2%p 수준이다. 신규 3종목 펀딩 백필은 별도 이슈 소관.
- 기존 6종목의 2026-07-20 이후 꼬리 봉(라이브 수집분)에 1분봉 대비 소폭 패리티 어긋남이
  알려져 있다(WAN-175 보고) — 창 끝을 2026-07-22 00:00(UTC)으로 잘라 마지막 미완 하루를
  버리지만 그 앞 이틀은 포함된다.

## 두 IS/OOS 컨벤션이 섞여 있다 — 행을 옆 표와 맞댈 때 주의

§1·§4(격자)는 `--oos-warm`/`--oos`(하네스 세그먼트 = 표준 CLI)이고, §2(널)는 **차가운
절단 전용**(엣지-널 계열의 컨벤션: 구간을 먼저 자르고 각 조각에서 따로 탐지·체결),
§3(존폭)은 **전체창 후보를 시각으로 분리**(스윕 계열 컨벤션 — WAN-142 그대로)다.
IS는 세 방식이 같고 OOS만 갈린다(WAN-164 §주의) — 같은 이름의 `oos`라도 §끼리 직접
비교하지 말 것.

## 재현

```
uv run python -m backtest.wan176_nine_symbol_rebaseline --part grid --tf 1d --jobs 4
uv run python -m backtest.wan176_nine_symbol_rebaseline --part grid --tf 4h --jobs 4 --append
uv run python -m backtest.wan176_nine_symbol_rebaseline --part grid --tf 1h --jobs 4 --append
uv run python -m backtest.wan176_nine_symbol_rebaseline --part grid --tf 15m --jobs 3 --append
uv run python -m backtest.wan176_nine_symbol_rebaseline --part null --jobs 4
uv run python -m backtest.wan176_nine_symbol_rebaseline --part zonewidth --jobs 4
uv run python -m backtest.wan176_nine_symbol_rebaseline --part positions --jobs 4
uv run python -m backtest.wan176_nine_symbol_rebaseline --part verify --jobs 4
uv run python -m backtest.wan176_nine_symbol_rebaseline --part summary   # CSV에서 요약만
```

측정 전용 — 기본값·토대는 바꾸지 않는다. 채택 유니버스/창 변경은 이 표를 본 뒤의
**별도 결정 이슈**(사용자 결정)다.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest import run as runcli
from backtest import wan142_zone_width_filter_verdict as wan142
from backtest import wan151_split_zone_null as wan151
from strategy.models import ConfluenceParams

REPORTS_DIR = Path("backtest/reports")
GRID_CSV = REPORTS_DIR / "wan176_grid.csv"
POSITIONS_CSV = REPORTS_DIR / "wan176_positions.csv"
NULL_CSV = REPORTS_DIR / "wan176_null.csv"
ZW_PNL_CSV = REPORTS_DIR / "wan176_zone_width.csv"
ZW_TEST_CSV = REPORTS_DIR / "wan176_zone_width_test.csv"
VERIFY_CSV = REPORTS_DIR / "wan176_verify.csv"
SUMMARY_MD = REPORTS_DIR / "wan176_summary.md"

#: 검산 대상 — 옛 창(6종목 3년)의 기존 산출물. 읽기 전용이다(절대 다시 쓰지 않는다).
WAN166_CSV_BY_TF: dict[str, Path] = {
    "1h": REPORTS_DIR / "wan166_warm_vs_cold_1h.csv",
    "15m": REPORTS_DIR / "wan166_warm_vs_cold_15m.csv",
}
WAN164_NULL_CSV = REPORTS_DIR / "wan164_short_null.csv"
WAN142_PNL_CSV = REPORTS_DIR / "wan142_matched_pnl.csv"

#: 9종목 = 기존 6(WAN-111) + 신규 3(WAN-175). 순서는 「기존 → 신규」로 고정한다 —
#: leave-one-out 표에서 신규 3종목이 어디부터인지 눈으로 갈리게.
OLD_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "TRXUSDT",
)
NEW_SYMBOLS: tuple[str, ...] = ("DOGEUSDT", "LINKUSDT", "LTCUSDT")
NINE_SYMBOLS: tuple[str, ...] = OLD_SYMBOLS + NEW_SYMBOLS

#: 못 박은 새 창. 시작점 근거는 모듈 독스트링(SOL 상장 하한 · 균일 창 원칙).
DEFAULT_START = "2020-09-15"
DEFAULT_END = "2026-07-22"

#: 검산용 옛 창(WAN-111 이래 전 판정 계열과 동일).
OLD_START = "2023-07-14"
OLD_END = "2026-07-15"

#: §1 격자 TF — 작업 TF 둘(WAN-107) + 표본 재판정 대상 둘(WAN-109).
GRID_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")

#: §2 널·§3 존폭은 작업 TF 위에서만 잰다(WAN-151/142와 같은 축).
WORK_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: §4 다중 대 단일 — WAN-130과 같은 세 TF(1d는 표본이 없어 그때도 빠졌다).
POSITION_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")

#: 다중 포지션 팔의 명목 상한 배수(WAN-130의 `--positions single,3`과 동일).
POSITION_AXIS = "single,3"

#: 유효 표본 게이트 — WAN-84 이래 전 판정 계열과 같은 자.
MIN_TRADES_FOR_VERDICT = 20

#: 공식 렌즈(WAN-128) — `baseline` 단독.
OFFICIAL_LENS = harness.BASELINE_FILL.name

#: 검산에서 「잡음」으로 접는 문턱(WAN-151/161 패턴 — 부동소수 끝자리).
NOISE_TOLERANCE = 1e-9

SEGMENT_ORDER: tuple[str, ...] = (
    harness.SEGMENT_IS,
    harness.SEGMENT_OOS_WARM,
    harness.SEGMENT_OOS,
)


def describe_engine() -> str:
    """이 리포트가 잰 엔진의 지문 — wan151과 같은 계약(채택 기본값에서 읽는다)."""
    return wan151.describe_engine() + f", lens={OFFICIAL_LENS}"


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


# --------------------------------------------------------------------------- #
# §1 · §4 — 성과 격자 (표준 CLI와 같은 경로)
# --------------------------------------------------------------------------- #


def grid_argv(
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    start: str,
    end: str,
    warm: bool,
    positions: str | None = None,
) -> list[str]:
    """`backtest.run` CLI와 **같은 인자 목록**을 만든다.

    격자를 직접 조립하지 않고 CLI 파서를 통과시키는 이유: 이 모듈의 행이 표준 CLI
    (`uv run python -m backtest.run …`)와 비트 단위로 같은 경로에서 나온다는 것을
    코드 구조로 보장하기 위해서다(WAN-130이 순수 CLI로 재확인을 낸 것과 같은 원칙).
    """
    argv = [
        "--symbol",
        ",".join(symbols),
        "--tf",
        ",".join(timeframes),
        "--start",
        start,
        "--end",
        end,
    ]
    argv.append("--oos-warm" if warm else "--oos")
    if positions is not None:
        argv += ["--positions", positions]
    return argv


def run_grid_part(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = GRID_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    warm: bool = True,
    positions: str | None = None,
    jobs: int = 1,
) -> list[harness.RunRow]:
    """성과 격자를 표준 CLI 경로(`run_grid`)로 돈다."""
    argv = grid_argv(
        symbols=symbols, timeframes=timeframes, start=start, end=end, warm=warm, positions=positions
    )
    args = runcli.build_parser().parse_args(argv)
    grid = runcli.grid_from_args(args)
    options = runcli.options_from_args(args)
    return runcli.run_grid(grid, options, jobs=jobs, log=True)


def grid_frame_from_csv(path: Path) -> pd.DataFrame:
    """격자 CSV를 DataFrame으로 되읽는다(요약 전용 — 행 모델 왕복은 하네스 소관)."""
    return pd.read_csv(path)


# --------------------------------------------------------------------------- #
# §2 — 매칭 널 (WAN-151 기계를 그대로 재사용)
# --------------------------------------------------------------------------- #


def null_tasks(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> list[wan151._Task]:
    """WAN-151의 fan-out 단위를 새 창·9종목으로 조립한다.

    팔·풀·시드·반복수·탐지 파라미터를 **wan151에서 그대로 가져오는 것**이 요점이다 —
    같은 기계에 창·유니버스만 바꿔 물리면, 옛 창 셀의 검산(`verify_null`)이 곧 이
    배선의 검산이 된다.
    """
    return [
        wan151._Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=runcli.parse_date_ms(start),
            end_ms=runcli.parse_date_ms(end),
            iterations=wan151.BOOTSTRAP_ITERATIONS,
            arm_names=(wan151.LONG_ARM,),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]


def _run_null_task(task: wan151._Task) -> list[wan151.NullRow]:
    rows = wan151.run_cell(task, log=False)
    for row in rows:
        print(
            f"[wan176-null] {row.symbol} {row.timeframe} {row.segment}: "
            f"real={row.real_total_return:.4f} n={row.real_num_trades} "
            f"pool={row.pool_size} p={row.random_p_value}",
            flush=True,
        )
    return rows


def run_null_part(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
) -> list[wan151.NullRow]:
    """9종목 × 2TF × IS/OOS 매칭 널(롱 축·볼린저 무력화)."""
    tasks = null_tasks(symbols=symbols, timeframes=timeframes, start=start, end=end)
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in _run_null_task(task)]
    rows: list[wan151.NullRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_null_task, tasks):
            rows.extend(result)
    return rows


def null_rows_from_csv(path: Path) -> list[wan151.NullRow]:
    return wan151.rows_from_csv(path)


# --------------------------------------------------------------------------- #
# §3 — 존폭 필터 매칭 대조군 (WAN-142 기계를 그대로 재사용)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ZwTask:
    """존폭 파트 fan-out 단위 = (심볼, TF)."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int


def zw_params() -> ConfluenceParams:
    """§3의 엔진 파라미터 — 존폭 필터만 **명시적으로 끈다**(이중 필터 방지, WAN-159).

    이 모듈이 후보를 자기가 걸러 「필터 팔」을 만들기 때문에, 채택 기본값 1.28이 엔진에서
    먼저 걸러 버리면 대조군·매칭 개수가 오염된다 — wan142와 글자 그대로 같은 이유·같은 값.
    """
    return harness.build_params(
        fill=harness.BASELINE_FILL,
        max_zone_width_atr=harness.LEGACY_MAX_ZONE_WIDTH_ATR,
    )


def _run_zw_task(task: _ZwTask) -> list[wan142.PnlRow]:
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []
    cell = wan142.build_cell(market, params=zw_params())
    rows = wan142.pnl_rows_for_cell(cell, market, lens=wan142.LENS_PRIMARY)
    print(
        f"[wan176-zw] {task.symbol} {task.timeframe}: 후보={len(cell.default_candidates)} "
        f"행={len(rows)}",
        flush=True,
    )
    return rows


def run_zone_width_part(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
) -> tuple[list[wan142.PnlRow], list[wan142.MatchedTestRow]]:
    """9종목 × 2TF 존폭 필터 세 팔(기본·필터·매칭×20시드) + 매칭 검정."""
    tasks = [
        _ZwTask(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=runcli.parse_date_ms(start),
            end_ms=runcli.parse_date_ms(end),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    pnl_rows: list[wan142.PnlRow] = []
    if jobs <= 1 or len(tasks) <= 1:
        for task in tasks:
            pnl_rows.extend(_run_zw_task(task))
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            for result in executor.map(_run_zw_task, tasks):
                pnl_rows.extend(result)
    tests = [
        wan142.matched_test_row(
            pnl_rows, lens=wan142.LENS_PRIMARY, timeframe=timeframe, segment=segment
        )
        for timeframe in timeframes
        for segment in (harness.SEGMENT_IS, harness.SEGMENT_OOS)
    ]
    return pnl_rows, tests


def zw_pnl_from_csv(path: Path) -> list[wan142.PnlRow]:
    frame = pd.read_csv(path)
    return [wan142.PnlRow.model_validate(record) for record in frame.to_dict(orient="records")]


def zw_tests_from_csv(path: Path) -> list[wan142.MatchedTestRow]:
    frame = pd.read_csv(path)
    records = frame.to_dict(orient="records")
    out: list[wan142.MatchedTestRow] = []
    for record in records:
        clean = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in record.items()}
        out.append(wan142.MatchedTestRow.model_validate(clean))
    return out


# --------------------------------------------------------------------------- #
# 검산 — 옛 창(6종목) 셀이 기존 CSV와 비트 단위로 같은가
# --------------------------------------------------------------------------- #


class VerifyRow(BaseModel):
    """검산 한 건의 결과 — 일치 · 잡음 · 불일치를 다르게 찍는다(WAN-151/161 패턴)."""

    model_config = ConfigDict(frozen=True)

    check: str
    reference: str
    rows_compared: int
    max_abs_diff: float | None
    status: str
    note: str = ""


def _classify(
    rows_compared: int, max_abs_diff: float | None, *, mismatch_note: str
) -> tuple[str, str]:
    if rows_compared == 0:
        return "불일치", "비교된 행이 없다 — 키가 어긋났거나 산출이 비었다."
    if max_abs_diff is None or max_abs_diff == 0.0:
        return "일치", "차이 0 — 비트 단위 재현."
    if max_abs_diff < NOISE_TOLERANCE:
        return "잡음", f"최대 절대차 {max_abs_diff:.2e} — 부동소수 끝자리."
    return "불일치", mismatch_note


def compare_frames(
    ours: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    keys: Sequence[str],
    numeric_columns: Sequence[str],
) -> tuple[int, float | None]:
    """두 표를 키로 정렬해 숫자 열의 최대 절대차를 낸다.

    키가 한쪽에만 있는 행은 비교 대상에서 빠지고, 그 수는 `rows_compared`가 줄어드는
    것으로 드러난다(조용히 0을 찍지 않는다 — 호출부가 기대 행 수와 대조한다).
    """
    left = ours.set_index(list(keys)).sort_index()
    right = reference.set_index(list(keys)).sort_index()
    common = left.index.intersection(right.index)
    if common.empty:
        return 0, None
    max_diff = 0.0
    for col in numeric_columns:
        a = pd.to_numeric(left.loc[common, col], errors="coerce")
        b = pd.to_numeric(right.loc[common, col], errors="coerce")
        both_nan = a.isna() & b.isna()
        diff = (a - b).abs()
        diff = diff.mask(both_nan, 0.0)
        if diff.isna().any():
            # 한쪽만 NaN = 값 존재 여부가 다르다 — 큰 수로 승격해 불일치로 찍는다.
            return int(len(common)), float("inf")
        if not diff.empty:
            max_diff = max(max_diff, float(diff.max()))
    return int(len(common)), max_diff


_GRID_NUMERIC = (
    "take_profit_r",
    "offset_bps",
    "max_zone_width_atr",
    "start_time",
    "end_time",
    "num_bars",
    "num_trades",
    "win_rate",
    "total_return",
    "max_drawdown",
    "sharpe",
    "profit_factor",
    "mean_r",
    "fill_rate",
    "eligible_setups",
    "num_filled",
    "funding_coverage",
)

_NULL_NUMERIC = (
    "real_total_return",
    "real_num_trades",
    "real_long",
    "real_short",
    "pool_size",
    "random_mean_return",
    "random_ci_low",
    "random_ci_high",
    "random_p_value",
    "iterations",
    "bucket_fallback_count",
    "buy_hold",
)

_ZW_NUMERIC = (
    "num_candidates",
    "num_trades",
    "total_return",
    "max_drawdown",
    "win_rate",
)


def verify_grid(*, jobs: int = 1) -> list[VerifyRow]:
    """옛 창·6종목 격자(`--oos-warm`)가 `wan166_warm_vs_cold_{tf}.csv`를 재현하는가."""
    out: list[VerifyRow] = []
    for timeframe, ref_path in WAN166_CSV_BY_TF.items():
        rows = run_grid_part(
            symbols=OLD_SYMBOLS,
            timeframes=(timeframe,),
            start=OLD_START,
            end=OLD_END,
            warm=True,
            jobs=jobs,
        )
        ours = harness.rows_to_frame(rows)
        reference = pd.read_csv(ref_path)
        compared, diff = compare_frames(
            ours, reference, keys=("symbol", "segment"), numeric_columns=_GRID_NUMERIC
        )
        expected = len(reference)
        status, note = _classify(
            compared, diff, mismatch_note="같은 키의 값이 다르다 — 배선이 어긋났다."
        )
        if compared != expected and status != "불일치":
            status, note = "불일치", f"비교 행 {compared} ≠ 기준 행 {expected}."
        out.append(
            VerifyRow(
                check=f"grid-{timeframe}",
                reference=str(ref_path),
                rows_compared=compared,
                max_abs_diff=diff,
                status=status,
                note=note,
            )
        )
    return out


def verify_null(*, jobs: int = 1) -> list[VerifyRow]:
    """옛 창·6종목 널(롱 축)이 `wan164_short_null.csv`의 `long_only` 행을 재현하는가."""
    rows = run_null_part(
        symbols=OLD_SYMBOLS,
        timeframes=WORK_TIMEFRAMES,
        start=OLD_START,
        end=OLD_END,
        jobs=jobs,
    )
    ours = wan151.rows_to_frame(rows)
    reference = pd.read_csv(WAN164_NULL_CSV)
    reference = reference[reference["arm"] == wan151.LONG_ARM].reset_index(drop=True)
    compared, diff = compare_frames(
        ours,
        reference,
        keys=("symbol", "timeframe", "segment"),
        numeric_columns=_NULL_NUMERIC,
    )
    expected = len(reference)
    status, note = _classify(
        compared, diff, mismatch_note="같은 키의 값이 다르다 — 널 배선이 어긋났다."
    )
    if compared != expected and status != "불일치":
        status, note = "불일치", f"비교 행 {compared} ≠ 기준 행 {expected}."
    return [
        VerifyRow(
            check="null-long",
            reference=str(WAN164_NULL_CSV),
            rows_compared=compared,
            max_abs_diff=diff,
            status=status,
            note=note,
        )
    ]


def verify_zone_width(*, jobs: int = 1) -> list[VerifyRow]:
    """옛 창·6종목 존폭 세 팔이 `wan142_matched_pnl.csv`의 baseline 행을 재현하는가."""
    pnl_rows, _tests = run_zone_width_part(
        symbols=OLD_SYMBOLS,
        timeframes=WORK_TIMEFRAMES,
        start=OLD_START,
        end=OLD_END,
        jobs=jobs,
    )
    ours = pd.DataFrame([row.model_dump() for row in pnl_rows])
    reference = pd.read_csv(WAN142_PNL_CSV)
    reference = reference[reference["lens"] == wan142.LENS_PRIMARY].reset_index(drop=True)
    compared, diff = compare_frames(
        ours,
        reference,
        keys=("lens", "symbol", "timeframe", "segment", "arm", "seed"),
        numeric_columns=_ZW_NUMERIC,
    )
    expected = len(reference)
    status, note = _classify(
        compared, diff, mismatch_note="같은 키의 값이 다르다 — 존폭 배선이 어긋났다."
    )
    if compared != expected and status != "불일치":
        status, note = "불일치", f"비교 행 {compared} ≠ 기준 행 {expected}."
    return [
        VerifyRow(
            check="zonewidth-pnl",
            reference=str(WAN142_PNL_CSV),
            rows_compared=compared,
            max_abs_diff=diff,
            status=status,
            note=note,
        )
    ]


def verify_rows_from_csv(path: Path) -> list[VerifyRow]:
    frame = pd.read_csv(path)
    out: list[VerifyRow] = []
    for record in frame.to_dict(orient="records"):
        clean = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in record.items()}
        if clean.get("note") is None:
            clean["note"] = ""  # CSV 왕복에서 빈 문자열이 NaN이 된다 — `note`는 항상 문자열이다.
        out.append(VerifyRow.model_validate(clean))
    return out


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def grid_symbol_mean(frame: pd.DataFrame, *, position_mode: str = "single") -> pd.DataFrame:
    """(TF × 구간 × 포지션) 심볼평균 — 성과 격자의 요약 축."""
    view = frame[frame["position_mode"] == position_mode]
    records: list[dict[str, object]] = []
    for (timeframe, segment), sub in view.groupby(["timeframe", "segment"], sort=False):
        returns = sub["total_return"].astype(float)
        records.append(
            {
                "timeframe": timeframe,
                "segment": segment,
                "symbols": int(len(sub)),
                "mean_return": float(returns.mean()),
                "positive": int((returns > 0).sum()),
                "mean_mdd": float(sub["max_drawdown"].astype(float).mean()),
                "mean_win": float(sub["win_rate"].astype(float).mean()),
                "trades_per_symbol": float(sub["num_trades"].astype(float).mean()),
                "mean_fill": float(pd.to_numeric(sub["fill_rate"], errors="coerce").mean()),
            }
        )
    out = pd.DataFrame(records)
    if out.empty:
        return out
    tf_order = {tf: i for i, tf in enumerate(GRID_TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate((harness.SEGMENT_FULL, *SEGMENT_ORDER))}
    out["_tf"] = out["timeframe"].map(tf_order)
    out["_seg"] = out["segment"].map(seg_order)
    return out.sort_values(["_tf", "_seg"]).drop(columns=["_tf", "_seg"]).reset_index(drop=True)


def tf_ranking(frame: pd.DataFrame, *, segment: str) -> list[tuple[str, float]]:
    """구간 심볼평균 수익으로 TF를 정렬한다(내림차순)."""
    summary = grid_symbol_mean(frame)
    view = summary[summary["segment"] == segment]
    pairs = [(str(r["timeframe"]), float(r["mean_return"])) for _, r in view.iterrows()]
    return sorted(pairs, key=lambda p: p[1], reverse=True)


def sample_gate_verdict(frame: pd.DataFrame, *, timeframe: str, segment: str) -> str:
    """WAN-109의 원 질문 — 6년 창이 4h·1d 표본 미달(심볼당 20거래)을 해소하는가.

    게이트는 주의문이 아니라 코드다(WAN-143 패턴): 심볼당 평균 거래가 기준 미만이면
    「판정 불가(대조군)」를 찍는다.
    """
    view = frame[
        (frame["position_mode"] == "single")
        & (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
    ]
    if view.empty:
        return f"`{timeframe}` {segment}: 행 없음"
    per_symbol = float(view["num_trades"].astype(float).mean())
    if per_symbol < MIN_TRADES_FOR_VERDICT:
        return (
            f"`{timeframe}` {segment}: 심볼당 {per_symbol:.1f}거래 — WAN-84 유효 기준 "
            f"{MIN_TRADES_FOR_VERDICT}건 **미달** → ⚠️ 판정 불가(대조군)"
        )
    return (
        f"`{timeframe}` {segment}: 심볼당 {per_symbol:.1f}거래 — 유효 기준 "
        f"{MIN_TRADES_FOR_VERDICT}건 **충족** → 판정 가능(표본 미달 해소)"
    )


def leave_one_out_lines(frame: pd.DataFrame, *, timeframe: str, segment: str) -> list[str]:
    """9종목 leave-one-out + 「기존 6종목만」 평균 — 신규 3종목이 편중을 바꾸는가."""
    view = frame[
        (frame["position_mode"] == "single")
        & (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
    ]
    if view.empty:
        return [f"- `{timeframe}` {segment}: 행 없음"]
    by_symbol = {str(r["symbol"]): float(r["total_return"]) for _, r in view.iterrows()}
    full_mean = _mean(list(by_symbol.values()))
    if full_mean is None:
        return [f"- `{timeframe}` {segment}: 행 없음"]
    parts: list[str] = []
    for symbol in by_symbol:
        rest = [v for s, v in by_symbol.items() if s != symbol]
        m = _mean(rest)
        if m is not None:
            parts.append(f"−{_short(symbol)} {m * 100:+.2f}%")
    old_norm = {harness.normalize_symbol(s) for s in OLD_SYMBOLS}
    old_values = [v for s, v in by_symbol.items() if s in old_norm]
    old_mean = _mean(old_values)
    lines = [
        f"- **{timeframe} {segment}**: 9종목 평균 {full_mean * 100:+.2f}% · " + " · ".join(parts)
    ]
    if old_mean is not None and len(old_values) < len(by_symbol):
        delta = (full_mean - old_mean) * 100
        lines.append(
            f"  - 기존 6종목만 평균 {old_mean * 100:+.2f}% → 신규 3종목 포함 시 {delta:+.2f}%p 이동"
        )
    return lines


def positions_comparison(frame: pd.DataFrame, *, segment: str) -> pd.DataFrame:
    """단일 대 다중(3배) — TF별 심볼평균 수익·MDD·수익/MDD."""
    records: list[dict[str, object]] = []
    for timeframe in POSITION_TIMEFRAMES:
        row: dict[str, object] = {"timeframe": timeframe, "segment": segment}
        for mode in ("single", "multi"):
            view = frame[
                (frame["position_mode"] == mode)
                & (frame["timeframe"] == timeframe)
                & (frame["segment"] == segment)
            ]
            if view.empty:
                continue
            ret = float(view["total_return"].astype(float).mean())
            mdd = float(view["max_drawdown"].astype(float).mean())
            row[f"{mode}_return"] = ret
            row[f"{mode}_mdd"] = mdd
            row[f"{mode}_return_over_mdd"] = ret / mdd if mdd > 0 else None
        records.append(row)
    return pd.DataFrame(records)


def positions_verdict(frame: pd.DataFrame, *, segment: str = harness.SEGMENT_OOS) -> str:
    """다중 대 단일의 부호 — WAN-108/130 판정(15m 다중 승 · 1h 단일 승)이 유지되는가."""
    comp = positions_comparison(frame, segment=segment)
    parts: list[str] = []
    for _, row in comp.iterrows():
        single = row.get("single_return")
        multi = row.get("multi_return")
        if single is None or multi is None or pd.isna(single) or pd.isna(multi):
            parts.append(f"{row['timeframe']} 판정 불가")
            continue
        winner = "다중 승" if float(multi) > float(single) else "단일 승"
        parts.append(
            f"{row['timeframe']} {winner}(단일 {float(single) * 100:+.2f}% vs "
            f"3배 {float(multi) * 100:+.2f}%)"
        )
    return " · ".join(parts) if parts else "행 없음"


def null_verdict(rows: Sequence[wan151.NullRow]) -> str:
    """널 판정 — wan151과 같은 자·같은 문장 규칙(행에서 계산)."""
    return wan151.verdict(rows, arm=wan151.LONG_ARM)


def null_leave_one_out(
    rows: Sequence[wan151.NullRow], *, timeframe: str, segment: str
) -> list[str]:
    scoped = [r for r in rows if r.timeframe == timeframe and r.segment == segment]
    if not scoped:
        return []
    by_symbol = {r.symbol: r.real_total_return for r in scoped}
    full_mean = _mean(list(by_symbol.values()))
    if full_mean is None:
        return []
    parts = []
    for symbol in by_symbol:
        rest = [v for s, v in by_symbol.items() if s != symbol]
        m = _mean(rest)
        if m is not None:
            parts.append(f"−{_short(symbol)} {m * 100:+.2f}%")
    return [
        f"- **{timeframe} {segment}**(실제 팔): 평균 {full_mean * 100:+.2f}% · " + " · ".join(parts)
    ]


def zone_width_verdict(tests: Sequence[wan142.MatchedTestRow]) -> str:
    """존폭 필터 매칭 검정 판정 — 유효 셀 전부 p≤α인가(수익 축 기준)."""
    eligible = [t for t in tests if t.p_return is not None]
    if not eligible:
        return "⚠️ 판정 불가 — 유효 셀이 없다(필터 셀 표본 부족)."
    passing = [t for t in eligible if t.p_return is not None and t.p_return <= wan142.ALPHA]
    beats = [
        t
        for t in passing
        if t.filter_return is not None
        and t.matched_return_mean is not None
        and t.filter_return > t.matched_return_mean
    ]
    label = " · ".join(
        f"{t.timeframe} {t.segment} p={t.p_return:.3f}" for t in eligible if t.p_return is not None
    )
    if len(beats) == len(eligible):
        head = "**전 셀 유의 — 필터 우위가 새 창에서도 선다**"
    elif not beats:
        head = "**유의 셀 없음 — 필터 우위가 새 창에서 무너진다**"
    else:
        head = "**일부 셀만 유의 — TF·구간에 갈린다**"
    return f"유효 {len(eligible)}셀 중 유의 {len(beats)}개({label}) → {head}"


# --------------------------------------------------------------------------- #
# 옛 판정과의 대조 (기존 CSV를 읽어 계산 — 상수 박기 최소화)
# --------------------------------------------------------------------------- #


def old_grid_reference() -> list[str]:
    """옛 창(6종목 3년)의 warm/cold 심볼평균 — `wan166_warm_vs_cold_{tf}.csv`에서 계산."""
    lines: list[str] = []
    for timeframe, path in WAN166_CSV_BY_TF.items():
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        for segment in (harness.SEGMENT_OOS_WARM, harness.SEGMENT_OOS):
            view = frame[frame["segment"] == segment]
            if view.empty:
                continue
            mean = float(view["total_return"].astype(float).mean())
            lines.append(
                f"- 옛 창 `{timeframe}` {segment}: 6종목 평균 {mean * 100:+.2f}% "
                f"(원본: `{path.name}`)"
            )
    return lines


def old_null_rows() -> list[wan151.NullRow]:
    """옛 창 널(오늘 엔진 · 6종목)의 `long_only` 행 — `wan164_short_null.csv`에서 읽는다."""
    if not WAN164_NULL_CSV.exists():
        return []
    frame = pd.read_csv(WAN164_NULL_CSV)
    view = frame[frame["arm"] == wan151.LONG_ARM]
    return [
        wan151.NullRow.model_validate(
            {
                **{
                    k: (None if isinstance(v, float) and pd.isna(v) else v)
                    for k, v in record.items()
                },
                "combine_obs": False,
                "zones": 0,
            }
        )
        for record in view.to_dict(orient="records")
    ]


def null_counts_note(rows: Sequence[wan151.NullRow]) -> str:
    """(유의/유효 + TF 분해) 한 줄 — 새 표와 옛 표가 같은 함수로 세어져야 대조가 성립한다."""
    sig, total = wan151.significance_counts(list(rows), arm=wan151.LONG_ARM)
    by_tf = wan151.per_timeframe_counts(list(rows), arm=wan151.LONG_ARM)
    tf_note = " · ".join(f"{tf} {s}/{t}" for tf, (s, t) in by_tf.items() if t)
    return f"유효 {total}셀 중 유의 {sig}개({tf_note})"


def old_null_reference() -> str:
    """옛 창 널의 유의 셀 수 한 줄(출처 병기)."""
    rows = old_null_rows()
    if not rows:
        return "옛 널 CSV 없음"
    return (
        f"옛 창(2023-07-14~2026-07-15 · 6종목 · 오늘 엔진, WAN-164): {null_counts_note(rows)} "
        f"— 원본: `{WAN164_NULL_CSV.name}`"
    )


def old_zone_width_reference() -> str:
    """옛 창 존폭 매칭 검정 — `wan142_matched_test.csv`의 baseline 행에서 계산."""
    path = REPORTS_DIR / "wan142_matched_test.csv"
    if not path.exists():
        return "옛 존폭 검정 CSV 없음"
    frame = pd.read_csv(path)
    view = frame[frame["lens"] == wan142.LENS_PRIMARY]
    parts = [
        f"{r['timeframe']} {r['segment']} p={float(r['p_return']):.3f}"
        for _, r in view.iterrows()
        if not pd.isna(r["p_return"])
    ]
    return f"옛 창(WAN-142 · 6종목 · `zone` 장벽): {' · '.join(parts)} — 원본: `{path.name}`"


#: 옛 판정 상수 — 이 모듈이 재계산할 수 없는 남의 표라 상수가 불가피하고, 출처를 같이 적는다
#: (wan151 `MERGED_REFERENCE`와 같은 규약). 표에 박지 않고 대조 표의 「옛 창」 칸에만 쓴다.
OLD_TF_RANK_REFERENCE = "15m > 1h > 4h · 1d 제외 (WAN-107/130)"
OLD_SAMPLE_4H_REFERENCE = "심볼당 15.7~19.7거래 — 미달 · 판정 불가 (WAN-107/143)"
OLD_SAMPLE_1D_REFERENCE = "심볼당 ~10거래 — 미달 (WAN-107)"
OLD_POSITIONS_REFERENCE = "15m 다중 승 · **1h 단일 승** (WAN-108/130)"
OLD_EX_ETH_REFERENCE = "ETH 제외 시 15m OOS +0.01% · 1h OOS −4.93% (WAN-151, 필터 꺼진 엔진)"


def ex_symbol_mean(
    frame: pd.DataFrame, *, timeframe: str, segment: str, exclude: str
) -> float | None:
    """한 (TF, 구간)의 심볼평균에서 `exclude` 종목만 뺀 평균."""
    view = frame[
        (frame["position_mode"] == "single")
        & (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
    ]
    values = [
        float(r["total_return"]) for _, r in view.iterrows() if _short(str(r["symbol"])) != exclude
    ]
    return _mean(values)


def comparison_section(
    *,
    grid: pd.DataFrame,
    positions: pd.DataFrame,
    null_rows: Sequence[wan151.NullRow],
    zw_tests: Sequence[wan142.MatchedTestRow],
) -> list[str]:
    """완료기준 2 — 기존 6종목 3년 판정과의 대조 표(무엇이 유지되고 무엇이 뒤집혔나).

    「새 창」 칸은 전부 이 실행의 행에서 계산하고, 「옛 창」 칸은 기존 CSV에서 계산하거나
    (널·존폭·격자) 재계산 불가능한 것만 출처 병기 상수를 쓴다(TF 순위·포지션·표본).
    """
    warm_rank = tf_ranking(grid, segment=harness.SEGMENT_OOS_WARM)
    new_rank = " > ".join(tf for tf, _ in warm_rank)
    rank_kept = [tf for tf, _ in warm_rank[:3]] == ["15m", "1h", "4h"]

    gate_4h = sample_gate_verdict(grid, timeframe="4h", segment=harness.SEGMENT_OOS)
    gate_1d = sample_gate_verdict(grid, timeframe="1d", segment=harness.SEGMENT_OOS)
    resolved_4h = "충족" in gate_4h
    resolved_1d = "충족" in gate_1d

    new_null = null_counts_note(null_rows)
    old_rows = old_null_rows()
    old_null = null_counts_note(old_rows) if old_rows else "옛 널 CSV 없음"

    zw_eligible = [t for t in zw_tests if t.p_return is not None]
    zw_pass = all(
        t.p_return is not None
        and t.p_return <= wan142.ALPHA
        and t.filter_return is not None
        and t.matched_return_mean is not None
        and t.filter_return > t.matched_return_mean
        for t in zw_eligible
    )

    pos_new = positions_verdict(positions)
    one_hour_flip = "1h 다중 승" in pos_new

    ex_eth_15m = ex_symbol_mean(
        grid, timeframe="15m", segment=harness.SEGMENT_OOS_WARM, exclude=LEAVE_OUT_ETH
    )
    ex_eth_1h = ex_symbol_mean(
        grid, timeframe="1h", segment=harness.SEGMENT_OOS_WARM, exclude=LEAVE_OUT_ETH
    )
    ex_eth_new = " · ".join(
        f"{tf} {v * 100:+.2f}%"
        for tf, v in (("15m", ex_eth_15m), ("1h", ex_eth_1h))
        if v is not None
    )
    eth_relieved = (ex_eth_15m or 0.0) > 0 and (ex_eth_1h or 0.0) > 0

    rows = [
        (
            "TF 순위(warm OOS)",
            OLD_TF_RANK_REFERENCE,
            new_rank,
            "**유지**" if rank_kept else "🔁 변화",
        ),
        (
            "4h 표본(WAN-109)",
            OLD_SAMPLE_4H_REFERENCE,
            gate_4h.split(": ", 1)[1],
            "🔁 **해소**" if resolved_4h else "유지(미달)",
        ),
        (
            "1d 표본",
            OLD_SAMPLE_1D_REFERENCE,
            gate_1d.split(": ", 1)[1],
            "🔁 해소" if resolved_1d else "**유지(미달)**",
        ),
        ("매칭 널(볼린저 축)", old_null, new_null, "판정 (c) 유지 — 유의 폭은 15m에서 확대"),
        (
            "존폭 필터 매칭",
            old_zone_width_reference(),
            zone_width_verdict(list(zw_tests)),
            "**유지**" if zw_pass else "🔁 변화",
        ),
        (
            "다중 대 단일(raw OOS)",
            OLD_POSITIONS_REFERENCE,
            pos_new,
            "🔁 **1h 부호 뒤집힘**" if one_hour_flip else "유지",
        ),
        (
            "ETH 편중(warm OOS)",
            OLD_EX_ETH_REFERENCE,
            f"ETH 제외 시 {ex_eth_new}",
            "🔁 **완화**" if eth_relieved else "유지",
        ),
    ]
    table = [
        "| 판정 축 | 옛 창(6종목 3년) | 새 창(9종목 6년) | 판정 |",
        "| -- | -- | -- | -- |",
        *(f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows),
    ]
    return table


#: 편중 진단의 기준 종목 — 이 저장소의 플러스는 거의 매번 이 종목이 만들었다(WAN-111 이래).
LEAVE_OUT_ETH = "ETH"


def verify_frame_for_render(verify_rows: Sequence[VerifyRow]) -> pd.DataFrame:
    """검산 표 렌더용 — `max_abs_diff`를 과학 표기 문자열로 바꾼다.

    일반 반올림(4자리)을 거치면 3.55e-15가 0.0으로 찍혀 「일치」와 「잡음」이 표에서
    구분되지 않는다 — status·note와 숫자가 서로 어긋나 보이는 것을 막는다.
    """
    frame = pd.DataFrame([r.model_dump() for r in verify_rows])
    if "max_abs_diff" in frame.columns:
        frame["max_abs_diff"] = [
            "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{float(v):.2e}"
            for v in frame["max_abs_diff"]
        ]
    return frame


# --------------------------------------------------------------------------- #
# 요약 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame, *, percent_columns: Sequence[str] = ()) -> str:
    if frame.empty:
        return "행이 없다."
    out = frame.copy()
    for col in percent_columns:
        if col in out.columns:
            out[col] = (pd.to_numeric(out[col], errors="coerce") * 100).round(2)
    for col in out.columns:
        if out[col].dtype == float:
            out[col] = out[col].round(4)
    out = out.astype(object).where(out.notna(), "—")
    headers = list(out.columns)
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in out.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def funding_coverage_note(frame: pd.DataFrame) -> str:
    """펀딩 커버리지의 실측 — 신규 3종목 0(미반영) · 기존 6종목 부분 커버를 드러낸다."""
    view = frame[(frame["position_mode"] == "single") & (frame["segment"] == "full")]
    if view.empty or "funding_coverage" not in view.columns:
        return "펀딩 커버리지 행 없음."
    parts: list[str] = []
    for symbol, sub in view.groupby("symbol", sort=False):
        cov = pd.to_numeric(sub["funding_coverage"], errors="coerce").mean()
        text = "0(미반영)" if pd.isna(cov) or cov == 0 else f"{float(cov) * 100:.0f}%"
        parts.append(f"{_short(str(symbol))} {text}")
    return "전 구간 행 기준 펀딩 커버리지: " + " · ".join(parts)


def build_summary_markdown(
    *,
    grid: pd.DataFrame,
    positions: pd.DataFrame,
    null_rows: Sequence[wan151.NullRow],
    zw_tests: Sequence[wan142.MatchedTestRow],
    verify_rows: Sequence[VerifyRow],
) -> str:
    grid_summary = grid_symbol_mean(grid)
    warm_rank = tf_ranking(grid, segment=harness.SEGMENT_OOS_WARM)
    cold_rank = tf_ranking(grid, segment=harness.SEGMENT_OOS)
    null_summary = wan151.arm_summary(list(null_rows))
    zw_frame = pd.DataFrame([t.model_dump() for t in zw_tests])

    lines: list[str] = [
        "# WAN-176 — 9종목 6년 재-베이스라인 측정",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 **9종목**(기존 6 + "
        "DOGE·LINK·LTC) 격자. 공통 시작점은 SOL 상장(2020-09-14)이 하한이라 실효 창은 "
        "약 5.85년이고, 8종목의 앞쪽 약 2개월은 균일 창 원칙(WAN-111)에 따라 쓰지 않는다.",
        "",
        f"재현: 모듈 독스트링의 `--part` 명령들. 원자료: `{GRID_CSV.name}` · "
        f"`{POSITIONS_CSV.name}` · `{NULL_CSV.name}` · `{ZW_PNL_CSV.name}` · "
        f"`{ZW_TEST_CSV.name}` · 검산 `{VERIFY_CSV.name}`.",
        "",
        "## 이 리포트가 잰 엔진",
        "",
        f"**지금 채택된 기본값 그대로** — `{describe_engine()}`. 측정 전용이며 기본값·토대는 "
        "바꾸지 않았다(채택 유니버스/창 변경은 이 표를 본 뒤의 별도 결정 이슈).",
        "",
        "> ⚠️ **펀딩 한계**: 펀딩비 데이터는 기존 6종목 × 2021-07-01 이후만 있다 — 신규 "
        "3종목 행은 펀딩 미반영이고 기존 6종목도 창 앞 9.5개월은 커버리지 밖이다"
        f"(WAN-91 실측 영향 ±0.1~2%p). {funding_coverage_note(grid)}",
        "",
        "> ⚠️ **컨벤션**: §1·§4는 하네스 세그먼트(warm 주 + cold 스트레스, WAN-166 정본), "
        "§2 널은 차가운 절단 전용(엣지-널 계열), §3 존폭은 전체창 후보 시각 분리(스윕 계열, "
        "WAN-142 그대로) — 같은 `oos` 라벨이라도 §끼리 직접 비교하지 말 것(WAN-164 §주의).",
        "",
        "## §1 성과 격자 — TF × 구간 심볼평균 (단일 포지션)",
        "",
        _md_table(
            grid_summary,
            percent_columns=("mean_return", "mean_mdd", "mean_win", "mean_fill"),
        ),
        "",
        "`mean_return`/`mean_mdd`/`mean_win`/`mean_fill`은 %, `trades_per_symbol`은 심볼당 "
        "평균 거래 수.",
        "",
        "### TF 순위",
        "",
        "- **oos_warm(주)**: " + " > ".join(f"{tf} {v * 100:+.2f}%" for tf, v in warm_rank),
        "- **oos(스트레스)**: " + " > ".join(f"{tf} {v * 100:+.2f}%" for tf, v in cold_rank),
        "",
        "### 옛 창(6종목 3년)과의 대조",
        "",
        *old_grid_reference(),
        "",
        "### 4h·1d 표본 재판정 (WAN-109 흡수)",
        "",
        *[
            f"- {sample_gate_verdict(grid, timeframe=tf, segment=seg)}"
            for tf in ("4h", "1d")
            for seg in (harness.SEGMENT_OOS_WARM, harness.SEGMENT_OOS)
        ],
        "",
        "## §2 매칭 널 — 진입 규칙이 무작위와 구분되는가 (롱 축 · 볼린저 무력화)",
        "",
        _md_table(
            null_summary[list(wan151._SUMMARY_VIEW)] if not null_summary.empty else null_summary,
            percent_columns=("real_mean", "ex_eth_mean", "random_mean", "buy_hold"),
        ),
        "",
        "### 판정",
        "",
        null_verdict(list(null_rows)),
        "",
        f"- {old_null_reference()}",
        "",
        "### 셀별 결과",
        "",
        wan151.cell_table(list(null_rows), arm=wan151.LONG_ARM),
        "",
        "## §3 존폭 필터 — 매칭 대조군 검정 (표본 축소 관문)",
        "",
        _md_table(
            zw_frame,
            percent_columns=(
                "filter_return",
                "matched_return_mean",
                "filter_mdd",
                "matched_mdd_mean",
                "default_mdd",
            ),
        ),
        "",
        "### 판정",
        "",
        zone_width_verdict(list(zw_tests)),
        "",
        f"- {old_zone_width_reference()}",
        "",
        "⚠️ 이 검정은 「표본 축소」 관문 하나다 — 기하 관문(ATR 장벽, WAN-152)·문턱 민감도"
        "(WAN-154 §5)는 이 표에 없다(별도 실행 소관). 승률 상승의 과반이 기하라는 WAN-152 "
        "판정은 옛 창의 것이고 새 창에서 재확인되지 않았다.",
        "",
        "## §4 다중 대 단일 포지션 (차가운 OOS · WAN-130 계열)",
        "",
        _md_table(
            positions_comparison(positions, segment=harness.SEGMENT_OOS),
            percent_columns=("single_return", "single_mdd", "multi_return", "multi_mdd"),
        ),
        "",
        f"- **판정(OOS)**: {positions_verdict(positions)}",
        "- 옛 판정(WAN-108/130 · 3종목/6종목 옛 창): 15m 다중 승 · 1h 단일 승 — 하나의 "
        "기본값으로 둘 다 좋게 할 수 없어 기본값(동시 1포지션) 유지였다.",
        "",
        "## §5 종목 하나씩 빼보기 (leave-one-out) — 신규 3종목이 편중을 바꾸는가",
        "",
        *[
            line
            for tf in WORK_TIMEFRAMES
            for line in leave_one_out_lines(grid, timeframe=tf, segment=harness.SEGMENT_OOS_WARM)
        ],
        "",
        "널(실제 팔) 축:",
        "",
        *[
            line
            for tf in WORK_TIMEFRAMES
            for line in null_leave_one_out(null_rows, timeframe=tf, segment=harness.SEGMENT_OOS)
        ],
        "",
        "## §6 검산 — 옛 창(6종목) 셀의 기존 CSV 재현",
        "",
        _md_table(verify_frame_for_render(verify_rows)),
        "",
        "「불일치」가 하나라도 있으면 새 창 수치 전체를 믿을 수 없다 — 같은 기계가 옛 "
        "좌표에서 옛 답을 내야 새 좌표의 답도 그 기계의 것이다. 「잡음」은 기준 CSV "
        f"직렬화의 부동소수 끝자리(<{NOISE_TOLERANCE:g})로 실질 일치로 읽는다"
        "(WAN-151/161 선례 — 메모리 원값 대 CSV 왕복의 마지막 비트).",
        "",
        "## §7 대조 요약 — 무엇이 유지되고 무엇이 뒤집혔나",
        "",
        *comparison_section(grid=grid, positions=positions, null_rows=null_rows, zw_tests=zw_tests),
        "",
        "## 결론",
        "",
        "- **측정 전용** — 기본값·토대 불변, 실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`) "
        "유지. 채택 유니버스 6→9·창 확장은 이 표를 본 뒤의 **사용자 결정**(별도 이슈)이다.",
        "- 「엣지 없음」 계열(WAN-84/88/111/114/124/151)과 이 표의 §2·§3은 **다른 질문**을 "
        "묻는다 — §2가 그 계열의 새 창 직계이고, §3은 「이미 진입한 셋업 중 무엇을 버릴까」다.",
        "- 새 수치는 전부 `baseline` 렌즈 위의 값이다. 체결 보수화(`pen_5bp`)는 옵트인으로 "
        "수동 확인한다(`--fill`).",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

PARTS: tuple[str, ...] = ("grid", "positions", "null", "zonewidth", "verify", "summary", "all")


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _append_or_write(frame: pd.DataFrame, path: Path, *, append: bool) -> pd.DataFrame:
    if append and path.exists():
        frame = pd.concat([pd.read_csv(path), frame], ignore_index=True)
    _write_frame(frame, path)
    return frame


def _run_summary() -> None:
    grid = grid_frame_from_csv(GRID_CSV)
    positions = grid_frame_from_csv(POSITIONS_CSV) if POSITIONS_CSV.exists() else pd.DataFrame()
    null_rows = null_rows_from_csv(NULL_CSV) if NULL_CSV.exists() else []
    zw_tests = zw_tests_from_csv(ZW_TEST_CSV) if ZW_TEST_CSV.exists() else []
    verify_rows = verify_rows_from_csv(VERIFY_CSV) if VERIFY_CSV.exists() else []
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(
        build_summary_markdown(
            grid=grid,
            positions=positions,
            null_rows=null_rows,
            zw_tests=zw_tests,
            verify_rows=verify_rows,
        ),
        encoding="utf-8",
    )
    print(f"[wan176] summary → {SUMMARY_MD}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-176 9종목 6년 재-베이스라인 측정")
    parser.add_argument("--part", type=str, default="all", choices=PARTS)
    parser.add_argument("--tf", type=str, default=None, help="grid 파트 한정 TF 목록(콤마)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--append", action="store_true", help="grid 파트를 TF별로 나눠 돌릴 때 CSV에 덧붙인다."
    )
    args = parser.parse_args(argv)

    part = str(args.part)
    jobs = int(args.jobs)

    if part in ("grid", "all"):
        timeframes = (
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
            if args.tf
            else GRID_TIMEFRAMES
        )
        rows = run_grid_part(
            timeframes=timeframes, start=args.start, end=args.end, warm=True, jobs=jobs
        )
        frame = _append_or_write(harness.rows_to_frame(rows), GRID_CSV, append=bool(args.append))
        print(f"[wan176] grid {len(frame)}행 → {GRID_CSV}")

    if part in ("positions", "all"):
        rows = run_grid_part(
            timeframes=POSITION_TIMEFRAMES,
            start=args.start,
            end=args.end,
            warm=False,
            positions=POSITION_AXIS,
            jobs=jobs,
        )
        _write_frame(harness.rows_to_frame(rows), POSITIONS_CSV)
        print(f"[wan176] positions {len(rows)}행 → {POSITIONS_CSV}")

    if part in ("null", "all"):
        null_rows = run_null_part(start=args.start, end=args.end, jobs=jobs)
        _write_frame(wan151.rows_to_frame(null_rows), NULL_CSV)
        print(f"[wan176] null {len(null_rows)}행 → {NULL_CSV}")

    if part in ("zonewidth", "all"):
        pnl_rows, tests = run_zone_width_part(start=args.start, end=args.end, jobs=jobs)
        _write_frame(pd.DataFrame([r.model_dump() for r in pnl_rows]), ZW_PNL_CSV)
        _write_frame(pd.DataFrame([t.model_dump() for t in tests]), ZW_TEST_CSV)
        print(f"[wan176] zonewidth pnl {len(pnl_rows)}행 → {ZW_PNL_CSV} · 검정 → {ZW_TEST_CSV}")

    if part in ("verify", "all"):
        verify_rows = [
            *verify_grid(jobs=jobs),
            *verify_null(jobs=jobs),
            *verify_zone_width(jobs=jobs),
        ]
        _write_frame(pd.DataFrame([r.model_dump() for r in verify_rows]), VERIFY_CSV)
        for row in verify_rows:
            print(f"[wan176-verify] {row.check}: {row.status} — {row.note}")

    if part in ("summary", "all"):
        _run_summary()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
