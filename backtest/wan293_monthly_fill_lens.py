"""WAN-293 — WAN-288/291 롱/숏 월별을 **체결 보수화 렌즈**로 재측정 (baseline·pen_5bp·drop_50).

사용자 요청(2026-08-12): WAN-291 월별 표가 전부 `baseline`(닿으면 체결) **낙관** 위 값이라
상한으로 읽어야 하는데, **얼마나 부풀려졌는지 밴드로 보이게** 한다. 세 렌즈로 조이면서
**숏 순기여(롱+숏 북 − 롱-온리 북)와 롱 북이 각 렌즈에서 얼마나 버티는지(잔존율)** 를 본다.

특히 숏은 15m·1h에 몰려 있고(WAN-291) 15m은 체결 민감성이 가장 큰 축(WAN-96/124)이라, 이
낙관이 숏 판정을 얼마나 부풀렸는지 눈에 안 보인다 — 그 상·하한 밴드를 이 표가 준다.

## 기계 재사용 (새 파이프라인 금지)

* **월별·북·방향 상세 = WAN-288 `build_rows`** 를 렌즈·시드마다 그대로 호출한다. 이 모듈은
  그 위에 **렌즈 축(baseline/pen_5bp/pen_5bp_drop_50)** 과 **탈락 렌즈의 시드 5개 평균**만
  얹고, `baseline` 팔은 WAN-288과 비트 단위로 같다(완료기준 2 — 회귀 테스트가 고정).
* **렌즈 정의 = `harness.FILL_PRESETS`** 재사용(새 렌즈 정의 금지 · WAN-128). 후보 생성에
  `fill`(WAN-264)과 `seed`(WAN-293, `run_cells`에 새로 뚫음)를 흘려 넣는다 — 렌즈는 **후보
  집합**을 바꾸므로 렌즈·시드마다 후보 생성을 다시 한다(WAN-264 주석).
* **3렌즈 병기는 이 이슈의 명시 요구다** — WAN-128의 「신규 리포트는 baseline 단독」은 일반
  리포트 규칙이고, 이 이슈는 **체결 민감도 밴드 자체가 목적**인 옵트인 스트레스 리포트다
  (WAN-96/124/264와 같은 부류). 두 민감도 렌즈는 `--fill`로 언제든 확인 가능한 옵트인이다.

## 산출 (완료기준 1·2·3)

1. **per-cell 방향 상세**(렌즈별) — (심볼 × TF × 방향 × 월)의 격리 순수익 %p, 탈락 렌즈는
   시드 5개 평균.
2. **월별 합산 두 줄**(렌즈별) — 롱-온리 북 vs 롱+숏 북(둘 다 cap_only 5배 · WAN-180 스냅샷
   북) + 숏 델타.
3. **잔존율** — 「`baseline` 대비 숏 순기여(하락 달)가 각 렌즈에서 몇 % 남나」를 전체·TF별로.

## ⚠️ 경고 (요약에 그대로)

* 세 렌즈 전부 **모델(흉내)이지 실측이 아니다** — 진짜 큐 우선순위·부분 체결은 틱·호가
  (WAN-98, **Canceled**) 소관. 이 표는 「낙관이 얼마나 컸나」의 상·하한 밴드일 뿐이다.
* 월별 %는 5배 북 복리 착시(WAN-213) — 방향·분포로만. 잔존율은 순기여(%p)·순합으로 병기.
* 「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변 — 숏 수익의 **체결 취약성**을 보는
  것이지 엣지를 뒤집는 게 아니다.
* 기본값·토대 불변(`ConfluenceParams()`·`short_enabled==False`·`LeverageBookParams()`) ·
  `ALPHABLOCK_LIVE_TRADING=false` 유지.

## 재현

```
# 4h·1h·2h 3렌즈(dropout 5시드). 15m은 셀당 무거우니(WAN-203) --append로 뒤에.
uv run python -m backtest.wan293_monthly_fill_lens --tf 4h,1h,2h --jobs 4
uv run python -m backtest.wan293_monthly_fill_lens --tf 15m --append --jobs 4
uv run python -m backtest.wan293_monthly_fill_lens --from-csv                     # 요약만
```
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan288_monthly_long_short import (
    ADOPTED_MODE,
    ADOPTED_MULTIPLE,
    ALL_SYMBOLS,
    ARM_LONG_ONLY,
    ARM_LONG_SHORT,
    DEFAULT_END,
    DEFAULT_START,
    LONG_RED_MONTHS,
    BookMonthlyRow,
    DirMonthlyRow,
    build_rows,
)

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELL_CSV = REPORTS_DIR / "wan293_monthly_by_cell.csv"
DEFAULT_BOOK_CSV = REPORTS_DIR / "wan293_monthly_book.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan293_monthly_summary.md"

#: 이 이슈의 렌즈 밴드(baseline → 겨냥형 → 최악). WAN-128의 「baseline 단독」은 일반 리포트
#: 규칙이고, 이 리포트는 체결 민감도 밴드 자체가 목적인 옵트인 스트레스다(WAN-96/124/264).
DEFAULT_LENSES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")
REF_LENS = "baseline"

#: 기본 TF = 4h·1h·2h(컴퓨트 실현 가능). 15m은 셀당 무거우니(WAN-203) `--append`로 뒤에.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "2h")

#: 잔존율 기준을 0으로 나누지 않기 위한 하한(WAN-115 부호 함정 가드).
_RESIDUAL_EPS = 1e-9


# --------------------------------------------------------------------------- #
# 렌즈 태그를 얹은 행 모델
# --------------------------------------------------------------------------- #


class LensDirRow(BaseModel):
    """WAN-288 `DirMonthlyRow` + 렌즈 태그 + 시드 평균 메타.

    탈락 렌즈(`drop_*`·`pen_5bp_drop_50`)는 `num_seeds`개 시드 평균이라 `net_pp_sum`·
    `num_trades`가 분수일 수 있다(그래서 float). `baseline`은 `num_seeds=1`이라 WAN-288 값과
    같다(완료기준 2).
    """

    model_config = ConfigDict(frozen=True)

    lens: str
    symbol: str
    timeframe: str
    direction: str
    month: str
    net_pp_sum: float
    num_trades: float
    symbol_buy_hold: float
    funding_gap: bool
    num_seeds: int


class LensBookRow(BaseModel):
    """WAN-288 `BookMonthlyRow` + 렌즈 태그 + 시드 평균 메타.

    ⚠️ 복리 곡선의 월 분해라 절대값 인용 금지(WAN-213 복리 착시). 탈락 렌즈는 시드 평균.
    """

    model_config = ConfigDict(frozen=True)

    lens: str
    scope: str
    arm: str
    month: str
    monthly_return: float
    equity_end: float
    num_exits: float
    btc_buy_hold: float
    num_seeds: int


# --------------------------------------------------------------------------- #
# 시드 평균 (탈락 렌즈)
# --------------------------------------------------------------------------- #


def _average_dir(
    per_seed: Sequence[Sequence[DirMonthlyRow]], lens: str, num_seeds: int
) -> list[LensDirRow]:
    """(심볼 × TF × 방향 × 월) 키로 시드 간 `net_pp_sum`·`num_trades`를 평균한다.

    한 시드에서 그 달에 거래가 하나도 없으면(탈락) 그 시드의 기여는 0이라, 합을 **고정 시드
    수**로 나눈다(WAN-96 관행). `symbol_buy_hold`·`funding_gap`은 시드에 무관(결정적)해 아무
    시드 값이나 쓴다.
    """
    acc_pp: dict[tuple[str, str, str, str], float] = defaultdict(float)
    acc_n: dict[tuple[str, str, str, str], float] = defaultdict(float)
    meta: dict[tuple[str, str, str, str], DirMonthlyRow] = {}
    for rows in per_seed:
        for r in rows:
            key = (r.symbol, r.timeframe, r.direction, r.month)
            acc_pp[key] += r.net_pp_sum
            acc_n[key] += r.num_trades
            meta[key] = r
    out: list[LensDirRow] = []
    for key in sorted(acc_pp):
        r = meta[key]
        out.append(
            LensDirRow(
                lens=lens,
                symbol=r.symbol,
                timeframe=r.timeframe,
                direction=r.direction,
                month=r.month,
                net_pp_sum=acc_pp[key] / num_seeds,
                num_trades=acc_n[key] / num_seeds,
                symbol_buy_hold=r.symbol_buy_hold,
                funding_gap=r.funding_gap,
                num_seeds=num_seeds,
            )
        )
    return out


def _average_book(
    per_seed: Sequence[Sequence[BookMonthlyRow]], lens: str, num_seeds: int
) -> list[LensBookRow]:
    """(스코프 × 팔 × 월) 키로 시드 간 `monthly_return`·`equity_end`·`num_exits`를 평균한다."""
    acc_ret: dict[tuple[str, str, str], float] = defaultdict(float)
    acc_eq: dict[tuple[str, str, str], float] = defaultdict(float)
    acc_ex: dict[tuple[str, str, str], float] = defaultdict(float)
    meta: dict[tuple[str, str, str], BookMonthlyRow] = {}
    for rows in per_seed:
        for r in rows:
            key = (r.scope, r.arm, r.month)
            acc_ret[key] += r.monthly_return
            acc_eq[key] += r.equity_end
            acc_ex[key] += r.num_exits
            meta[key] = r
    out: list[LensBookRow] = []
    for key in sorted(acc_ret):
        r = meta[key]
        out.append(
            LensBookRow(
                lens=lens,
                scope=r.scope,
                arm=r.arm,
                month=r.month,
                monthly_return=acc_ret[key] / num_seeds,
                equity_end=acc_eq[key] / num_seeds,
                num_exits=acc_ex[key] / num_seeds,
                btc_buy_hold=r.btc_buy_hold,
                num_seeds=num_seeds,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 격자 조립 — 렌즈 × 시드
# --------------------------------------------------------------------------- #


def build_lens_rows(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    lenses: Sequence[str] = DEFAULT_LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
) -> tuple[list[LensDirRow], list[LensBookRow], dict[str, str]]:
    """각 렌즈(탈락은 시드 평균)의 방향 상세 + 두-팔 북 월별을 낸다.

    렌즈마다 `run_cells(fill=렌즈, seed=s, short_enabled=True)`로 후보를 다시 생성하고, WAN-288
    `build_rows`로 월별·북·방향을 접은 뒤 시드 평균한다. `baseline`은 `fill=None`·`seed=0`이라
    WAN-288 경로와 비트 단위로 같다(완료기준 2).
    """
    dir_out: list[LensDirRow] = []
    book_out: list[LensBookRow] = []
    notes: dict[str, str] = {}
    for lens in lenses:
        fill = None if lens == REF_LENS else harness.fill_preset(lens)
        seeds = fill.seeds if fill is not None else (0,)
        per_seed_dir: list[list[DirMonthlyRow]] = []
        per_seed_book: list[list[BookMonthlyRow]] = []
        for seed in seeds:
            cells = run_cells(
                symbols,
                timeframes,
                start=start,
                end=end,
                jobs=jobs,
                short_enabled=True,
                fill=fill,
                seed=seed,
                # WAN-305 명시 핀: wan293 CSV는 재진입 꺼진 북(wan288 계열)의 동결 기록이다.
                reentry=False,
            )
            if funding_proxy:
                cells, note = apply_funding_proxy(cells)
                if note:
                    notes[lens] = note
            dir_rows, book_rows = build_rows(cells, start=start, end=end)
            per_seed_dir.append(dir_rows)
            per_seed_book.append(book_rows)
            print(
                f"[wan293] {lens} seed={seed}: 방향 {len(dir_rows)}행 · 북 {len(book_rows)}행",
                flush=True,
            )
        dir_out.extend(_average_dir(per_seed_dir, lens, len(seeds)))
        book_out.extend(_average_book(per_seed_book, lens, len(seeds)))
    return dir_out, book_out, notes


# --------------------------------------------------------------------------- #
# 프레임 · CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def dir_to_frame(rows: Sequence[LensDirRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(LensDirRow.model_fields))


def book_to_frame(rows: Sequence[LensBookRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(LensBookRow.model_fields))


def dir_from_csv(path: Path) -> list[LensDirRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [LensDirRow.model_validate(rec) for rec in frame.to_dict("records")]


def book_from_csv(path: Path) -> list[LensBookRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [LensBookRow.model_validate(rec) for rec in frame.to_dict("records")]


def merge_dir(existing: Sequence[LensDirRow], new: Sequence[LensDirRow]) -> list[LensDirRow]:
    """--append: (렌즈, 심볼, TF, 방향, 월) 키로 덮어쓴다(새 TF를 잇거나 재실행)."""
    keyed = {(r.lens, r.symbol, r.timeframe, r.direction, r.month): r for r in existing}
    for r in new:
        keyed[(r.lens, r.symbol, r.timeframe, r.direction, r.month)] = r
    return [keyed[k] for k in sorted(keyed)]


def merge_book(existing: Sequence[LensBookRow], new: Sequence[LensBookRow]) -> list[LensBookRow]:
    """--append: (렌즈, 스코프, 팔, 월) 키로 덮어쓴다.

    ⚠️ `all` 스코프는 전 TF 집계라 단일 TF만 append하면 재계산 불가다 — 새로 온 스코프만
    덮어쓰고 기존 `all`은 남긴다(WAN-288 `merge_book`과 같은 규약).
    """
    keyed = {(r.lens, r.scope, r.arm, r.month): r for r in existing}
    for r in new:
        keyed[(r.lens, r.scope, r.arm, r.month)] = r
    return [keyed[k] for k in sorted(keyed)]


# --------------------------------------------------------------------------- #
# 분석 (요약용 · 테스트 대상)
# --------------------------------------------------------------------------- #


def short_delta_by_month(
    book_rows: Sequence[LensBookRow], lens: str, scope: str
) -> dict[str, float]:
    """이 렌즈·스코프의 월별 숏 기여분 = (롱+숏 북 월수익률) − (롱-온리 북 월수익률)."""
    lo = {
        r.month: r.monthly_return
        for r in book_rows
        if r.lens == lens and r.scope == scope and r.arm == ARM_LONG_ONLY
    }
    ls = {
        r.month: r.monthly_return
        for r in book_rows
        if r.lens == lens and r.scope == scope and r.arm == ARM_LONG_SHORT
    }
    return {month: ls[month] - lo.get(month, 0.0) for month in sorted(ls)}


def regime_split(
    book_rows: Sequence[LensBookRow], lens: str, scope: str
) -> tuple[float, float, float]:
    """(하락 달 숏 기여 합, 상승 달 숏 기여 합, 순합) — BTC 바이앤홀드 부호로 달을 가른다."""
    delta = short_delta_by_month(book_rows, lens, scope)
    btc = {
        r.month: r.btc_buy_hold
        for r in book_rows
        if r.lens == lens and r.scope == scope and r.arm == ARM_LONG_SHORT
    }
    down = sum(d for m, d in delta.items() if btc.get(m, 0.0) < 0)
    up = sum(d for m, d in delta.items() if btc.get(m, 0.0) >= 0)
    return down, up, down + up


def tf_short_net_pp(book_rows: Sequence[LensBookRow], lens: str, scope: str) -> float:
    """이 렌즈·스코프의 숏 순기여 순합(하락+상승 달) — 잔존율의 자."""
    return regime_split(book_rows, lens, scope)[2]


def residual_pct(value: float, ref: float) -> float | None:
    """`value / ref`(잔존율). 기준이 0 근처면 정의하지 않는다(WAN-115 부호 함정 가드)."""
    if abs(ref) < _RESIDUAL_EPS:
        return None
    return value / ref


def short_net_by_lens(
    book_rows: Sequence[LensBookRow], scope: str
) -> dict[str, tuple[float, float, float]]:
    """스코프의 렌즈별 (하락, 상승, 순합) 숏 기여 — 렌즈 순서는 `DEFAULT_LENSES`."""
    lenses = _present_lenses(book_rows)
    return {lens: regime_split(book_rows, lens, scope) for lens in lenses}


def _present_lenses(rows: Sequence[LensDirRow] | Sequence[LensBookRow]) -> list[str]:
    seen = {r.lens for r in rows}
    ordered = [lens for lens in DEFAULT_LENSES if lens in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def _present_scopes(book_rows: Sequence[LensBookRow]) -> list[str]:
    scopes = {r.scope for r in book_rows}
    tf_scopes = sorted((s for s in scopes if s != "all"), key=timeframe_to_ms)
    return (["all"] if "all" in scopes else []) + tf_scopes


def _agg_scope(book_rows: Sequence[LensBookRow]) -> str:
    scopes = _present_scopes(book_rows)
    return "all" if "all" in scopes else (scopes[0] if scopes else "")


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _fmt_residual(value: float | None) -> str:
    return "—(기준≈0)" if value is None else f"{value * 100:.0f}%"


def _two_line_table(book_rows: Sequence[LensBookRow], lens: str, scope: str) -> str:
    lo = {
        r.month: r
        for r in book_rows
        if r.lens == lens and r.scope == scope and r.arm == ARM_LONG_ONLY
    }
    ls = {
        r.month: r
        for r in book_rows
        if r.lens == lens and r.scope == scope and r.arm == ARM_LONG_SHORT
    }
    delta = short_delta_by_month(book_rows, lens, scope)
    months = sorted(set(lo) | set(ls))
    lines = [
        "| 월 | 장세(BTC BH) | 롱-온리 북 | 롱+숏 북 | 숏 기여(델타) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for month in months:
        lo_r = lo.get(month)
        ls_r = ls.get(month)
        ref = ls_r if ls_r is not None else lo_r
        btc = ref.btc_buy_hold if ref is not None else 0.0
        regime = "🔻하락" if btc < 0 else "🔺상승"
        lo_s = _fmt_pct(lo_r.monthly_return) if lo_r else "—"
        ls_s = _fmt_pct(ls_r.monthly_return) if ls_r else "—"
        d_s = _fmt_pct(delta.get(month, 0.0))
        red = " ⬅빨간달" if month in LONG_RED_MONTHS else ""
        lines.append(f"| {month}{red} | {regime} ({btc * 100:+.1f}%) | {lo_s} | {ls_s} | {d_s} |")
    return "\n".join(lines)


def _residual_table(book_rows: Sequence[LensBookRow], scope: str) -> str:
    """렌즈 × (하락/상승/순합 숏 기여) + baseline 대비 잔존율(순합·하락)."""
    ref_down, _ref_up, ref_net = regime_split(book_rows, REF_LENS, scope)
    lines = [
        f"### 스코프 `{scope}` — 숏 순기여 잔존율 (baseline 대비)\n",
        "| 렌즈 | 하락 달 숏 기여 | 상승 달 숏 기여 | 순합 | 순합 잔존율 | 하락 잔존율 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for lens in _present_lenses(book_rows):
        down, up, net = regime_split(book_rows, lens, scope)
        net_res = _fmt_residual(residual_pct(net, ref_net))
        down_res = _fmt_residual(residual_pct(down, ref_down))
        lines.append(
            f"| {lens} | {_fmt_pct(down)} | {_fmt_pct(up)} | {_fmt_pct(net)} | "
            f"{net_res} | {down_res} |"
        )
    return "\n".join(lines)


def _tf_residual_table(book_rows: Sequence[LensBookRow]) -> str:
    """TF × 렌즈 숏 순기여 순합 + baseline 대비 잔존율 — 「15m이 특히 취약한가」의 자."""
    tf_scopes = [s for s in _present_scopes(book_rows) if s != "all"]
    lenses = _present_lenses(book_rows)
    header = "| TF | " + " | ".join(f"{lens}" for lens in lenses) + " | "
    header += " | ".join(f"{lens} 잔존율" for lens in lenses if lens != REF_LENS) + " |"
    sep = "| --- | " + " | ".join(["---"] * len(lenses)) + " | "
    sep += " | ".join(["---"] * (len(lenses) - 1)) + " |"
    lines = [header, sep]
    for tf in tf_scopes:
        nets = {lens: tf_short_net_pp(book_rows, lens, tf) for lens in lenses}
        ref = nets[REF_LENS]
        cells = [_fmt_pct(nets[lens]) for lens in lenses]
        res = [_fmt_residual(residual_pct(nets[lens], ref)) for lens in lenses if lens != REF_LENS]
        lines.append(f"| {tf} | " + " | ".join(cells) + " | " + " | ".join(res) + " |")
    return "\n".join(lines)


def _verdict_line(book_rows: Sequence[LensBookRow]) -> str:
    """숏 순기여가 pen_5bp에서 얼마나 버티나 / 15m이 특히 취약한가 한 문단(완료기준 2)."""
    agg = _agg_scope(book_rows)
    if not agg:
        return "**판정**: 북 행이 없어 판정 표본이 없다."
    _d, _u, ref_net = regime_split(book_rows, REF_LENS, agg)
    pen_net = (
        regime_split(book_rows, "pen_5bp", agg)[2] if _has_lens(book_rows, "pen_5bp") else None
    )
    worst_net = (
        regime_split(book_rows, "pen_5bp_drop_50", agg)[2]
        if _has_lens(book_rows, "pen_5bp_drop_50")
        else None
    )
    pen_res = _fmt_residual(residual_pct(pen_net, ref_net)) if pen_net is not None else "N/A"
    worst_res = _fmt_residual(residual_pct(worst_net, ref_net)) if worst_net is not None else "N/A"

    # 15m 취약성 — 15m 스코프가 있으면 그 잔존율을, 없으면 가장 짧은 TF를 본다.
    tf_scopes = [s for s in _present_scopes(book_rows) if s != "all"]
    tf_note = ""
    if tf_scopes and _has_lens(book_rows, "pen_5bp"):
        shortest = tf_scopes[0]
        ref_tf = tf_short_net_pp(book_rows, REF_LENS, shortest)
        pen_tf = tf_short_net_pp(book_rows, "pen_5bp", shortest)
        tf_res = _fmt_residual(residual_pct(pen_tf, ref_tf))
        tf_note = (
            f" 가장 짧은 축 `{shortest}`의 pen_5bp 잔존율은 **{tf_res}** — 짧은 TF일수록 "
            "마진 체결 의존이 크다는 WAN-96/124 관찰의 숏 축 재확인이다."
        )
    return (
        f"**판정 — 숏 순기여가 체결 보수화에서 얼마나 버티나(스코프 `{agg}`)**: baseline 순합 "
        f"{_fmt_pct(ref_net)} → pen_5bp 잔존 **{pen_res}** → drop_50 잔존 **{worst_res}**. "
        f"잔존율이 낮을수록 숏 수익이 「스치듯 닿은 체결」에 실림(실전 괴리 큼).{tf_note}"
    )


def _has_lens(rows: Sequence[LensBookRow], lens: str) -> bool:
    return any(r.lens == lens for r in rows)


def build_summary_markdown(
    dir_rows: Sequence[LensDirRow],
    book_rows: Sequence[LensBookRow],
    *,
    cell_csv: Path = DEFAULT_CELL_CSV,
    book_csv: Path = DEFAULT_BOOK_CSV,
    funding_notes: dict[str, str] | None = None,
) -> str:
    agg = _agg_scope(book_rows)
    lenses = _present_lenses(book_rows)

    parts: list[str] = []
    parts.append(
        "# WAN-293 — 롱/숏 월별을 체결 보수화 렌즈로 재측정 (baseline → pen_5bp → drop_50)\n"
    )
    parts.append(
        "> 사용자 요청(2026-08-12). WAN-291 월별 표가 전부 `baseline`(닿으면 체결) **낙관** 위 "
        "값이라, 체결을 조이면서 **숏 순기여(롱+숏 북 − 롱-온리 북)가 각 렌즈에서 얼마나 "
        "버티는지(잔존율)** 를 본다. 측정 전용 · `short_enabled`는 이 모듈 안에서만 True · "
        "cap_only 5배(WAN-180 스냅샷 북) · oos_warm 정본(WAN-166) · 탈락 렌즈는 시드 5개 평균.\n"
    )
    if funding_notes:
        note = next((v for v in funding_notes.values() if v), "")
        if note:
            parts.append(f"신규 3종목 펀딩 대리: {note}\n")

    parts.append("## 숏 순기여 잔존율 (핵심 — 완료기준 3)\n")
    if agg:
        parts.append(_residual_table(book_rows, agg) + "\n")
    parts.append("### TF별 숏 순기여 · 잔존율 (「15m이 특히 취약한가」)\n")
    parts.append(_tf_residual_table(book_rows) + "\n")
    parts.append(_verdict_line(book_rows) + "\n")

    for lens in lenses:
        parts.append(f"## 렌즈 `{lens}` — 롱-온리 북 vs 롱+숏 북 (스코프 `{agg}`)\n")
        if agg:
            down, up, net = regime_split(book_rows, lens, agg)
            parts.append(
                f"하락 달 숏 기여 **{_fmt_pct(down)}** · 상승 달 **{_fmt_pct(up)}** · "
                f"순합 **{_fmt_pct(net)}**.\n"
            )
            parts.append(_two_line_table(book_rows, lens, agg) + "\n")

    parts.append("## ⚠️ 경고 (인용 시 함께 옮길 것)\n")
    parts.append(
        "- 세 렌즈 전부 **모델(흉내)이지 실측이 아니다** — 진짜 큐 우선순위·부분 체결은 "
        "틱·호가(WAN-98, **Canceled**) 소관. 이 표는 「낙관이 얼마나 컸나」의 상·하한 밴드일 "
        "뿐이다.\n"
        "- 월별 수익률 %는 5배 북을 복리로 굴린 값이라 **실현 수익이 아니다**(WAN-213 복리 착시) "
        "— 방향·분포로만. 잔존율은 순기여(%p)·순합으로 병기했다.\n"
        "- **「하락장에서 숏이 번다」는 거의 동어반복** — 숏은 가격 내리면 벌게 설계됨. 채택 "
        "근거가 아니다. 이 표가 묻는 것은 그 숏 수익이 **체결 가정을 조이면 얼마나 남나**이다.\n"
        "- 「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변 — 이 표는 숏 수익의 **체결 "
        "취약성**을 보는 것이지 엣지를 뒤집는 게 아니다.\n"
        "- 기본값·토대 불변(`ConfluenceParams()`·`short_enabled==False`·`LeverageBookParams()`) · "
        "`ALPHABLOCK_LIVE_TRADING=false` 유지.\n"
    )

    parts.append("## 재현\n")
    parts.append(
        "```\n"
        "uv run python -m backtest.wan293_monthly_fill_lens --tf 4h,1h,2h --jobs 4\n"
        "uv run python -m backtest.wan293_monthly_fill_lens --tf 15m --append --jobs 4\n"
        "uv run python -m backtest.wan293_monthly_fill_lens --from-csv   # 요약만 재생성\n"
        "```\n"
        f"원자료: `{cell_csv}`(방향 상세) · `{book_csv}`(두-팔 북). cap_only={ADOPTED_MODE} "
        f"배수={ADOPTED_MULTIPLE}.\n"
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-293 월별 롱/숏 체결 보수화 렌즈")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--fill", type=str, default=",".join(DEFAULT_LENSES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--jobs",
        type=int,
        default=harness.default_jobs(),
        help="(심볼, TF) 단위 병렬 워커 수(미지정이면 ALPHABLOCK_BACKTEST_JOBS, 기본 4, WAN-294)",
    )
    parser.add_argument("--no-funding-proxy", action="store_true", help="신규 종목 펀딩 대리 끔.")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument("--append", action="store_true", help="새 TF를 기존 CSV에 이어 붙인다.")
    parser.add_argument("--cell-csv", type=Path, default=DEFAULT_CELL_CSV)
    parser.add_argument("--book-csv", type=Path, default=DEFAULT_BOOK_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    out_cell: Path = args.cell_csv
    out_book: Path = args.book_csv
    out_md: Path = args.summary
    funding_notes: dict[str, str] = {}

    if args.from_csv:
        dir_rows = dir_from_csv(out_cell)
        book_rows = book_from_csv(out_book)
        print(f"[wan293] CSV 로드 — 방향 {len(dir_rows)}행 · 북 {len(book_rows)}행 (재실행 없음)")
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        lenses = tuple(s.strip() for s in str(args.fill).split(",") if s.strip())
        # 지원하지 않는 렌즈는 여기서 거부(조용히 무시 금지, WAN-95 교훈).
        for lens in lenses:
            if lens != REF_LENS:
                harness.fill_preset(lens)
        dir_rows, book_rows, funding_notes = build_lens_rows(
            symbols,
            timeframes,
            lenses=lenses,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            funding_proxy=not args.no_funding_proxy,
        )
        if args.append:
            if out_cell.exists():
                dir_rows = merge_dir(dir_from_csv(out_cell), dir_rows)
            if out_book.exists():
                book_rows = merge_book(book_from_csv(out_book), book_rows)
        out_cell.parent.mkdir(parents=True, exist_ok=True)
        dir_to_frame(dir_rows).to_csv(out_cell, index=False)
        book_to_frame(book_rows).to_csv(out_book, index=False)
        print(f"[wan293] 방향 {len(dir_rows)}행 → {out_cell}")
        print(f"[wan293] 북 {len(book_rows)}행 → {out_book}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            dir_rows, book_rows, cell_csv=out_cell, book_csv=out_book, funding_notes=funding_notes
        ),
        encoding="utf-8",
    )
    print(f"[wan293] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
