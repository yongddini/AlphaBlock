"""WAN-149 §3: 존 병합 폐지 전후 4개 TF 손익 격자.

사용자 요청("15m 1h 4h 1d를 다 돌리고")대로 **네 TF 전부**에서 병합 ON(옛 기본값) →
OFF(새 기본값) 두 팔을 같은 실행에서 낸다. 그 밖의 축은 **전부 채택 기본값 고정**이다
(오프셋 2bp × `intrabar_live` 밴드 × `unconditional` 게이트 × 고정 1.5R × 롱 온리),
공식 렌즈는 `baseline` 단독(WAN-128), 창은 WAN-111/114/117/134/137/138/143과 같은
못 박은 창(2023-07-14~2026-07-15) · 6심볼이다.

## 🚨 「데이터가 분리를 골랐다」로 인용 금지

기본값 전환은 **사용자 판단**이고 이 표는 그 판단의 **비용을 기록**하는 것이지 근거가
아니다. WAN-134 부검의 공식 판정은 (c)「병합은 손절률에 중립」 · 채택 권고 없음이었고,
분리 팔이 이기는 칸이 있어도 **거래 수가 5~8% 늘어** 두 팔은 아예 다른 거래를 한다.
게다가 그 차이는 「병합이 존폭의 대리변수」라 존폭 효과일 수 있다(WAN-142 소관).

## 왜 새 격자를 짜지 않는가 (WAN-101 규칙)

원 수치는 범용 CLI로 나온다 — 그래서 **격자를 다시 짜지 않고 `backtest.run.run_grid`를
그대로 호출한다**(WAN-111/123과 같은 패턴). `--combine-obs true,false`가 그 축이고,
이 모듈이 더 하는 일은 CLI에 없는 **사후 분해와 판정 문장**뿐이다:

* 심볼평균 대조(ON → OFF)와 거래 수 증감,
* leave-one-out(심볼 하나가 결과를 다 만드는가),
* **표본 게이트**(WAN-143 `verdict()` 재사용) — 4h·1d는 판정하지 않고
  「⚠️ 판정 불가(대조군)」를 **코드가** 찍는다. 주의문이 아니라 게이트다.

## 재현

```
uv run python -m backtest.wan149_merge_grid --jobs auto
uv run python -m backtest.wan149_merge_grid --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest.harness import (
    BASELINE_FILL,
    SEGMENT_IS,
    SEGMENT_OOS,
    RunRow,
    build_params,
    normalize_symbol,
)
from backtest.run import JOBS_AUTO, Grid, RunOptions, parse_date_ms, parse_jobs, run_grid

#: 6심볼(WAN-111 유니버스). 3심볼 시절 수치와 섞이지 않도록 전부 같은 창에서 돈다.
ALL_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

#: 사용자 요청 TF 넷. 15m·1h가 작업 TF(WAN-107)이고 4h·1d는 **대조군**이다.
TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")

#: 작업 TF — 판정 문장을 내는 축(WAN-107). 나머지는 표본 게이트가 막는다.
WORKING_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 두 팔. `True` = 옛 기본값(병합) · `False` = 새 기본값(분리).
MERGE_ON = True
MERGE_OFF = False

MIN_TRADES_PER_SYMBOL = 20
"""WAN-84 유효 기준 — OOS 심볼당 거래가 이보다 적으면 판정하지 않는다(WAN-143 §게이트).

WAN-107/130이 4h를 「보류(표본 부족)」로 둔 자다(심볼당 15.7·18.3거래). 1d는 10거래다.
게이트가 없으면 대조군 열이 판정문을 갖게 되고, 그 문장은 다음 이슈에서 근거로
재인용된다 — 이 저장소가 여러 번 겪은 실패다. 표본을 늘려 재판정하는 것은 WAN-109 소관.
"""

SEGMENTS: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)


# --------------------------------------------------------------------------- #
# 격자 (범용 CLI 그대로)
# --------------------------------------------------------------------------- #


def build_grid(symbols: Sequence[str] = ALL_SYMBOLS) -> Grid:
    """채택 기본값 × 병합 두 팔. 병합 말고는 **아무것도 고정하지 않는다**.

    `rsi_gate_mode`·`band_bar`를 옛 값으로 고정하지 않는 것이 **의도**다 — 이 표는
    "옛 리포트를 재현"하는 것이 아니라 **오늘의 채택 기본값 위에서** 병합 축만 갈라
    보는 것이다. 고정하면 이미 폐기된 게이트·밴드 위의 대조가 되어, 사용자가 지금
    돌리는 엔진의 전환 비용을 말해 주지 못한다.
    """
    defaults = build_params()
    return Grid(
        symbols=tuple(normalize_symbol(s) for s in symbols),
        timeframes=TIMEFRAMES,
        entry_modes=("zone_limit",),
        take_profit_rs=(defaults.take_profit_r,),
        offsets_bps=(defaults.zone_limit_offset_bps,),
        fills=(BASELINE_FILL,),
        combine_obs=(MERGE_ON, MERGE_OFF),
        # 존폭 필터는 **고정하지 않는다** — 이 표는 밴드·게이트를 고정하지 않고 오늘의 채택
        # 기본값 위에서 병합 축만 갈라 보는 것이 의도라(위 독스트링), WAN-159의 필터도 그대로
        # 따라간다. 옛(필터 꺼진) 수치는 스냅샷이고 새 기본값 위의 재측정은 별도 이슈다.
    )


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = JOBS_AUTO,
    log: bool = True,
) -> list[RunRow]:
    """6심볼 × 4TF × 2팔 × (IS/OOS) 격자."""
    options = RunOptions(start_ms=parse_date_ms(start), end_ms=parse_date_ms(end), oos=True)
    return run_grid(build_grid(symbols), options, log=log, jobs=jobs)


def rows_to_frame(rows: Sequence[RunRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[RunRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고칠 때 격자 재실행 방지).

    `RunRow`로 검증하며 통과하므로 요약이 CSV와 갈라질 수 없다.
    """
    frame = pd.read_csv(path)
    return [RunRow.model_validate(record) for record in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def per_symbol(rows: Sequence[RunRow]) -> pd.DataFrame:
    """(TF, 구간, 팔, 심볼) 한 행. 공식 렌즈 단독이라 시드는 1개다."""
    frame = rows_to_frame(rows)
    return frame[["timeframe", "segment", "combine_obs", "symbol"] + list(_METRICS)].copy()


_METRICS: tuple[str, ...] = (
    "total_return",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
)


def arm_summary(rows: Sequence[RunRow]) -> pd.DataFrame:
    """팔별 심볼평균 + 플러스 심볼 수."""
    view = per_symbol(rows)
    grouped = view.groupby(["timeframe", "segment", "combine_obs"], as_index=False).agg(
        total_return=("total_return", "mean"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "sum"),
        fill_rate=("fill_rate", "mean"),
        n_symbols=("symbol", "nunique"),
    )
    positive = (
        view.assign(positive=view["total_return"] > 0)
        .groupby(["timeframe", "segment", "combine_obs"], as_index=False)["positive"]
        .sum()
    )
    return grouped.merge(positive, on=["timeframe", "segment", "combine_obs"], how="left")


def cell(frame: pd.DataFrame, timeframe: str, segment: str, combine: bool) -> dict[str, float]:
    """요약 한 칸. 없으면 빈 dict(호출부가 판정을 접는다).

    ⚠️ 숫자 판정을 `isinstance(v, (int, float))`로 하지 않는다 — pandas가 내주는
    `numpy.int64`는 `int`의 서브클래스가 **아니라서** 그 조건이 거래 수 열을 통째로
    조용히 떨어뜨린다. 판정문이 `KeyError`로 죽거나(운이 좋으면) 열이 빈 채로 지나간다.
    """
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["combine_obs"] == combine)
    ]
    if sub.empty:
        return {}
    numeric: dict[str, float] = {}
    for key, value in sub.iloc[0].items():
        if isinstance(value, bool) or not pd.api.types.is_number(value):
            continue
        numeric[str(key)] = float(value)
    return numeric


def delta_table(summary: pd.DataFrame) -> pd.DataFrame:
    """ON → OFF 델타 표 — 이 리포트의 본문."""
    records: list[dict[str, object]] = []
    for timeframe in TIMEFRAMES:
        for segment in SEGMENTS:
            on, off = (
                cell(summary, timeframe, segment, MERGE_ON),
                cell(summary, timeframe, segment, MERGE_OFF),
            )
            if not on or not off:
                continue
            trades_on, trades_off = on["num_trades"], off["num_trades"]
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "return_on": on["total_return"],
                    "return_off": off["total_return"],
                    "return_delta": off["total_return"] - on["total_return"],
                    "mdd_on": on["max_drawdown"],
                    "mdd_off": off["max_drawdown"],
                    "trades_on": trades_on,
                    "trades_off": trades_off,
                    "trades_pct": (trades_off / trades_on - 1.0) if trades_on else float("nan"),
                    "positive_on": on["positive"],
                    "positive_off": off["positive"],
                    "n_symbols": on["n_symbols"],
                }
            )
    return pd.DataFrame(records)


def leave_one_out(rows: Sequence[RunRow], timeframe: str, segment: str) -> pd.DataFrame:
    """심볼 하나를 뺐을 때 새 기본값(분리) 팔의 심볼평균이 어디로 가나."""
    view = per_symbol(rows)
    sub = view[
        (view["timeframe"] == timeframe)
        & (view["segment"] == segment)
        & (view["combine_obs"] == MERGE_OFF)
    ]
    if sub.empty:
        return pd.DataFrame()
    base = float(sub["total_return"].mean())
    records = [
        {
            "dropped": symbol,
            "total_return": float(sub[sub["symbol"] != symbol]["total_return"].mean()),
            "delta": float(sub[sub["symbol"] != symbol]["total_return"].mean()) - base,
        }
        for symbol in sorted(sub["symbol"].unique())
    ]
    return pd.DataFrame(records).sort_values("delta").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 판정 (표본 게이트 = 코드)
# --------------------------------------------------------------------------- #


def trades_per_symbol(summary: pd.DataFrame, timeframe: str) -> float:
    """표본 게이트의 입력 — 기준점 팔(병합 ON)의 OOS 심볼당 거래 수."""
    on = cell(summary, timeframe, SEGMENT_OOS, MERGE_ON)
    if not on or not on.get("n_symbols"):
        return 0.0
    return on["num_trades"] / on["n_symbols"]


def verdict(summary: pd.DataFrame, timeframe: str) -> str:
    """공식 렌즈 심볼평균으로 그 TF의 전환 비용을 한 문장으로.

    ⚠️ **표본이 WAN-84 기준(OOS 심볼당 20거래)에 못 미치면 판정하지 않는다** — 숫자는
    그대로 내되 문장 앞에 「판정 불가(대조군)」를 박는다. 4h·1d가 정확히 그 자리다.
    """
    is_on, is_off = (
        cell(summary, timeframe, SEGMENT_IS, MERGE_ON),
        cell(summary, timeframe, SEGMENT_IS, MERGE_OFF),
    )
    oos_on, oos_off = (
        cell(summary, timeframe, SEGMENT_OOS, MERGE_ON),
        cell(summary, timeframe, SEGMENT_OOS, MERGE_OFF),
    )
    if not (is_on and is_off and oos_on and oos_off):
        return "판정 불가(데이터 없음)."

    is_delta = is_off["total_return"] - is_on["total_return"]
    oos_delta = oos_off["total_return"] - oos_on["total_return"]
    per_symbol_trades = trades_per_symbol(summary, timeframe)
    if per_symbol_trades < MIN_TRADES_PER_SYMBOL:
        tag = (
            f"⚠️ **판정 불가(대조군)** — OOS 심볼당 {per_symbol_trades:.1f}거래로 WAN-84 유효 "
            f"기준({MIN_TRADES_PER_SYMBOL}건) 미달이다(WAN-107/130의 4h 「보류」와 같은 자리). "
            "아래 숫자는 방향을 보는 참고값이지 채택 근거가 아니다"
        )
    elif is_delta > 0 and oos_delta > 0:
        tag = "(a) 분리가 두 구간 모두에서 이긴다 — 전환 비용이 음수(= 이득)"
    elif is_delta <= 0 and oos_delta <= 0:
        tag = "(b) 분리가 두 구간 모두에서 진다 — 전환은 알고 받는 손해"
    else:
        tag = "(c) 구간에 갈린다 — IS→OOS 뒤집힘(이 저장소 상습 패턴)"
    return (
        f"{tag}. 심볼평균 total_return: "
        f"IS {is_on['total_return'] * 100:+.2f}% → {is_off['total_return'] * 100:+.2f}% "
        f"(Δ{is_delta * 100:+.2f}%p) · "
        f"OOS {oos_on['total_return'] * 100:+.2f}% → {oos_off['total_return'] * 100:+.2f}% "
        f"(Δ{oos_delta * 100:+.2f}%p). "
        f"OOS 거래 수 {oos_on['num_trades']:.0f} → {oos_off['num_trades']:.0f}건"
        f"({(oos_off['num_trades'] / oos_on['num_trades'] - 1) * 100:+.1f}%) — "
        "⚠️ 두 팔은 **같은 거래를 다르게 처리한 것이 아니라 아예 다른 거래를 한다**."
    )


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #

_PCT_COLUMNS: tuple[str, ...] = (
    "total_return",
    "return_on",
    "return_off",
    "return_delta",
    "win_rate",
    "max_drawdown",
    "mdd_on",
    "mdd_off",
    "fill_rate",
    "trades_pct",
    "delta",
)


def _pct(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in _PCT_COLUMNS:
        if column in out.columns:
            out[column] = (out[column] * 100).round(2)
    return out


def _md_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(데이터 없음)_"
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "| " + " | ".join("--" for _ in frame.columns) + " |"
    body = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in record) + " |"
        for record in frame.itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *body])


def write_summary(rows: Sequence[RunRow], path: Path) -> None:
    summary = arm_summary(rows)
    deltas = delta_table(summary)
    lines = [
        "# WAN-149 §3 존 병합 폐지 — 4개 TF 손익 격자 (병합 ON → OFF)",
        "",
        "재현: `uv run python -m backtest.wan149_merge_grid`"
        " (요약만 재생성은 `--from-csv`) · 원본 CSV: `wan149_merge_grid.csv`",
        "",
        "6심볼 · 못 박은 창(2023-07-14~2026-07-15) · 공식 렌즈 `baseline` 단독(WAN-128) · "
        "그 밖의 축은 **오늘의 채택 기본값 그대로**(오프셋 2bp × `intrabar_live` × "
        "`unconditional` × 고정 1.5R × 롱 온리).",
        "",
        "🚨 **「데이터가 분리를 골랐다」로 인용 금지** — 기본값 전환은 **사용자 판단**이고 "
        "이 표는 그 **비용의 기록**이다. WAN-134 공식 판정은 (c)「병합은 손절률에 중립」 · "
        "채택 권고 없음이었다. ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/145)은 이 전환으로 "
        '뒤집히지 않는다** — 아래는 전부 낙관 렌즈(`baseline` = "닿으면 체결") 위의 값이다.',
        "",
        "## 1. 판정 — 작업 TF는 판정하고 대조군은 게이트가 막는다",
        "",
    ]
    for timeframe in TIMEFRAMES:
        role = "작업 TF" if timeframe in WORKING_TIMEFRAMES else "대조군"
        lines += [f"* **{timeframe}** ({role}): {verdict(summary, timeframe)}", ""]
    lines += [
        "⚠️ **4h·1d는 「대조군」이라 판정하지 않는다** — 표본 게이트(OOS 심볼당 "
        f"{MIN_TRADES_PER_SYMBOL}거래, WAN-84)가 **코드에서** 문장을 바꾼다. 표본을 늘려 "
        "재판정하는 것은 WAN-109 소관이다.",
        "",
        "## 2. ON → OFF 델타",
        "",
        "`trades_pct` = 거래 수 증감. **이 열이 WAN-134가 채택을 권고하지 않은 이유다** — "
        "두 팔은 같은 거래를 다르게 처리한 게 아니라 아예 다른 거래를 한다. 수익이 는 것이 "
        "규칙이 좋아져서인지 그냥 더 많이 베팅해서인지 이 표는 **못 가른다**. "
        "⚠️ 그 차이는 「병합이 존폭의 대리변수」(wan134.md:45)라 존폭 효과일 수 있다 — "
        "거래 수를 맞춘 재검은 WAN-142 소관이다.",
        "",
        _md_table(_pct(deltas)),
        "",
        "## 3. leave-one-out — 심볼 하나가 결과를 다 만드는가 (분리 팔, OOS)",
        "",
        "`delta` = (그 심볼을 뺀 5심볼 평균) − (6심볼 평균). **음수면 그 심볼이 평균을 "
        "끌어올리고 있었다**는 뜻이다.",
        "",
    ]
    for timeframe in WORKING_TIMEFRAMES:
        lines += [
            f"### {timeframe} OOS",
            "",
            _md_table(_pct(leave_one_out(rows, timeframe, SEGMENT_OOS))),
            "",
        ]
    lines += [
        "## 4. 팔별 원 수치",
        "",
        _md_table(_pct(summary)),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--jobs",
        type=parse_jobs,
        default=JOBS_AUTO,
        help="(심볼, TF) 단위 병렬 워커 수. auto/0이면 CPU 코어 수(기본)",
    )
    parser.add_argument("--out-csv", type=str, default="backtest/reports/wan149_merge_grid.csv")
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan149_merge_grid_summary.md"
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 저장된 --out-csv에서 요약만 재생성한다",
    )
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan149] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        rows = run_report(symbols, start=args.start, end=args.end, jobs=args.jobs)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan149] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
