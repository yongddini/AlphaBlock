"""WAN-126: 다중TF 오더블록 겹침 캐스케이드 — 선별 대 가격 분리.

WAN-84/88/111/114/124가 반복해서 낸 결론은 "좋은 존과 나쁜 존을 가르는 선별 규칙이 아직
하나도 없다"였다. 이 리포트는 사용자 제안(상위TF POI + 하위TF refinement)이 그 **선별 규칙**
인지, 아니면 볼린저처럼 **가격 효과**(좁은 존 → 작은 1R → 가까운 고정 R 익절)일 뿐인지를
3팔로 가른다:

* `A`(대조) — 겹침 무시, 상위TF 존 진입 = 채택 기본값.
* `B`(선별만) — 겹침 요구, **상위TF 존** 진입(1R 불변).
* `C`(선별+가격) — 겹침 요구, **하위TF 겹침 존** 진입(좁아진 1R까지).

`B − A` = 순수 선별 효과, `C − B` = 순수 가격 효과. 엔진·룩어헤드 가드·겹침 정의는
`backtest.multi_tf_overlap`에 있고 그 회귀 테스트가 미래 하위TF 존 차단을 동작으로 고정한다.

## 사양 (사용자 확정 · docs/decisions/wan126.md)

* 방향 일치(수요↔수요) · 캐스케이드 첫 겹침에서 정지 · 정본 겹침 정의 `contained`
  (민감도 `proximal_in`·`touch`, 중첩 관계).
* 사다리 1h→15m→5m→1m · 15m→5m→1m (사용자 결정 (A): 5m 수집 완료).
* 공식 렌즈 `baseline` 단독(WAN-128). 창은 못 박는다(`--start`/`--end`).
* 셀당 20거래 미만은 판정 불가(WAN-84) — 표에는 남기되 결론에서 제외.
* `C−B`는 볼린저가 가격을 덮어써 가격 효과의 **하한**이다(wan126.md §3).
* 심볼 편중(leave-one-out) · `B`/`C` 거래 수 오염(>5%면 `C−B` 순수 가격으로 인용 금지) ·
  바닥 TF별 분해를 병기한다.

## 재현

```
uv run python -m backtest.wan126_multi_tf_overlap --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan126_multi_tf_overlap --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan126_multi_tf_overlap --from-csv         # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    MarketData,
    build_config,
    build_params,
    load_market_data,
    mean_r,
    normalize_symbol,
    pin_band_bar,
    segments_for,
    slice_market,
)
from backtest.multi_tf_overlap import (
    OVERLAP_DEFINITIONS,
    MultiTfOverlapParams,
    ZoneProvider,
    indexed_zone_provider,
)
from backtest.run import parse_date_ms
from backtest.zone_limit_backtest import (
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

#: 상위TF **아래로** 내려갈 사다리(사용자 확정 (A): 5m 포함).
LADDERS: dict[str, tuple[str, ...]] = {
    "1h": ("15m", "5m", "1m"),
    "15m": ("5m", "1m"),
}

DEFAULT_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"
SEGMENT_ORDER: tuple[str, ...] = ("full", "is", "oos")
MIN_TRADES = 20  # WAN-84 유효 기준.

#: 하위TF 오더블록 아카이브 디스크 캐시(gitignore된 data/cache 아래). 1분봉 탐지가 심볼당
#: 8분+(초선형)이라 (심볼, TF, 창)마다 한 번만 탐지하고 재사용한다 — 탐지는 결정적이므로
#: 캐시가 정당하다(wan126.md §5·[[project_1m_ob_detection_slow]]).
DEFAULT_CACHE_DIR = "data/cache/wan126_ob"


class OverlapRow(BaseModel):
    """3팔 격자 한 셀."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    definition: str
    """`contained`/`proximal_in`/`touch`. `A` 팔은 `none`(겹침 무관)."""
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    fill_rate: float | None
    eligible_setups: int | None
    mean_r: float | None
    sharpe: float | None
    n_from_15m: int
    n_from_5m: int
    n_from_1m: int
    """겹침을 찾은 바닥 TF별 거래 수(시퀀싱된 거래 기준). `A`는 전부 0."""


# --------------------------------------------------------------------------- #
# 탐지 · 실행
# --------------------------------------------------------------------------- #


def _cache_path(cache_dir: str, symbol: str, tf: str, start_ms: int, end_ms: int) -> Path:
    safe = symbol.replace("/", "_").replace(":", "_")
    return Path(cache_dir) / f"{safe}_{tf}_{start_ms}_{end_ms}.json"


def detect_ltf_archives(
    symbol: str,
    ladder: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
    log: bool = False,
) -> dict[str, OrderBlockResult]:
    """사다리의 하위TF 오더블록을 **전체 창에서** 탐지한다(룩어헤드는 질의 시 클리핑).

    탐지는 인과적이라 시각 T의 존은 창 끝과 무관하게 같다 — 그래서 구간마다 다시 탐지하지
    않고 한 번만 하고 `indexed_zone_provider`가 T까지 클리핑해 쓴다.

    `cache_dir`을 주면 (심볼, TF, 창)별 결과를 JSON으로 캐시한다 — 1분봉 탐지가 심볼당
    8분+(초선형)이라 재실행·15m 축에서 반드시 재사용해야 한다. 캐시 파일은 gitignore된
    `data/cache` 아래이고 탐지가 결정적이라 (심볼, 창) 키만으로 정합적이다.
    """
    archives: dict[str, OrderBlockResult] = {}
    for tf in ladder:
        cache = _cache_path(cache_dir, symbol, tf, start_ms, end_ms) if cache_dir else None
        if cache is not None and cache.exists():
            archives[tf] = OrderBlockResult.model_validate_json(cache.read_text(encoding="utf-8"))
            if log:
                print(f"[wan126] cache hit {symbol} {tf} ({len(archives[tf].order_blocks)} obs)")
            continue
        df = load_market_data(
            symbol, tf, start_ms=start_ms, end_ms=end_ms, need_1m=False, funding=False
        ).htf_df
        if df.empty:
            continue  # 데이터 없는 칸(예: 과거 5m 결측)은 사다리에서 조용히 빠진다.
        result = OrderBlockDetector().run(df)
        archives[tf] = result
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(result.model_dump_json(), encoding="utf-8")
            if log:
                print(f"[wan126] cached {symbol} {tf} ({len(result.order_blocks)} obs)")
    return archives


def _run_cell(
    market: MarketData,
    htf_obr: OrderBlockResult,
    provider: ZoneProvider | None,
    overlap: MultiTfOverlapParams | None,
    *,
    segment_name: str,
    take_profit_r: float,
) -> tuple[OverlapRow, int, int]:
    """한 팔·한 구간을 돌려 행 + (거래수, 시퀀싱된 후보 오염 비교용 거래수)를 낸다."""
    # ⚠️ 밴드는 WAN-132 이전 값(`tap`)으로 고정한다 — 이 표의 겹침 판정 수치가 그
    # 밴드에서 나왔고, 봉내 라이브 밴드는 진입가를 서브스텝마다 다시 내므로 팔 간
    # 비교의 축(겹침 정의)이 흐려진다.
    params = pin_band_bar(build_params(entry_mode="zone_limit"))
    cfg = build_config(market.timeframe)
    candidates, stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=htf_obr,
        overlap=overlap,
        zone_provider=provider,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)
    trades = [t for _, t in paired]
    result = build_result_from_trades(trades, cfg, market.timeframe)
    counts: Counter[str] = Counter(c.refinement_tf for c, _ in paired if c.refinement_tf)
    m = result.metrics
    arm = overlap.arm if overlap is not None else "A"
    definition = overlap.definition if (overlap is not None and overlap.arm != "A") else "none"
    row = OverlapRow(
        symbol=market.symbol,
        timeframe=market.timeframe,
        segment=segment_name,
        arm=arm,
        definition=definition,
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        fill_rate=stats.fill_rate,
        eligible_setups=stats.eligible,
        mean_r=mean_r(result, take_profit_r),
        sharpe=m.sharpe,
        n_from_15m=counts.get("15m", 0),
        n_from_5m=counts.get("5m", 0),
        n_from_1m=counts.get("1m", 0),
    )
    return row, m.num_trades, len(candidates)


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("1h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
    log: bool = True,
) -> list[OverlapRow]:
    """3팔 격자를 돈다: 심볼 × TF × 구간 × (A + B·C × 3정의)."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    take_profit_r = build_params(entry_mode="zone_limit").take_profit_r
    rows: list[OverlapRow] = []
    for timeframe in timeframes:
        ladder = LADDERS[timeframe]
        for symbol in symbols:
            sym = normalize_symbol(symbol)
            market = load_market_data(sym, timeframe, start_ms=start_ms, end_ms=end_ms)
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan126] skip {sym} {timeframe}: 데이터 없음")
                continue
            archives = detect_ltf_archives(
                sym, ladder, start_ms=start_ms, end_ms=end_ms, cache_dir=cache_dir, log=log
            )
            # 하위TF 존은 **병합하지 않는다**(combine=False). 두 가지 이유: (1) "그 자리에
            # 뚜렷한 하위TF 오더블록이 있는가"라는 refinement 질문에는 병합 전 개별 존이 더
            # 충실하다(병합은 인접 존을 더 넓은 블록으로 뭉쳐 겹침 판정을 흐린다). (2) 1분봉
            # 아카이브는 병합(O(활성²))이 셋업마다 돌면 격자가 몇 시간이 된다. combine=True는
            # 옵트인 민감도로 남는다(엔진·테스트는 두 경우 모두 지원). wan126.md §7 참고.
            provider = indexed_zone_provider(archives, combine=False)
            if log:
                have = ",".join(archives) or "(없음)"
                print(f"[wan126] {sym} {timeframe}: 하위TF 존 {have} — 격자 시작")
            for segment in segments_for(oos=True):
                seg_market = slice_market(market, segment)
                if seg_market.empty or seg_market.df_1m.empty:
                    continue
                htf_obr = OrderBlockDetector().run(seg_market.htf_df)
                configs: list[MultiTfOverlapParams | None] = [None]
                for definition in OVERLAP_DEFINITIONS:
                    configs.append(
                        MultiTfOverlapParams(arm="B", definition=definition, ladder=ladder)
                    )
                    configs.append(
                        MultiTfOverlapParams(arm="C", definition=definition, ladder=ladder)
                    )
                for overlap in configs:
                    cell_provider = None if overlap is None else provider
                    row, _, _ = _run_cell(
                        seg_market,
                        htf_obr,
                        cell_provider,
                        overlap,
                        segment_name=segment.name,
                        take_profit_r=take_profit_r,
                    )
                    rows.append(row)
            if log:
                print(f"[wan126] {sym} {timeframe}: done ({len([r for r in rows])} rows total)")
    return rows


# --------------------------------------------------------------------------- #
# 집계 · 분해
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[OverlapRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[OverlapRow]:
    frame = pd.read_csv(path)
    return [OverlapRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def _symbol_mean(
    frame: pd.DataFrame, timeframe: str, segment: str, arm: str, definition: str
) -> float:
    sub = frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["arm"] == arm)
        & (frame["definition"] == definition)
    ]
    return float(sub["total_return"].mean()) if len(sub) else float("nan")


def decomposition(frame: pd.DataFrame, *, definition: str = "contained") -> pd.DataFrame:
    """`B − A`(선별)·`C − B`(가격)를 심볼평균 total_return으로 분해한다."""
    records: list[dict[str, object]] = []
    for timeframe in sorted(set(frame["timeframe"])):
        for segment in SEGMENT_ORDER:
            a = _symbol_mean(frame, timeframe, segment, "A", "none")
            b = _symbol_mean(frame, timeframe, segment, "B", definition)
            c = _symbol_mean(frame, timeframe, segment, "C", definition)
            if pd.isna(a) and pd.isna(b) and pd.isna(c):
                continue
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "definition": definition,
                    "A": a,
                    "B": b,
                    "C": c,
                    "selection_B_minus_A": b - a,
                    "price_C_minus_B": c - b,
                }
            )
    return pd.DataFrame(records)


def trade_contamination(frame: pd.DataFrame, *, definition: str = "contained") -> pd.DataFrame:
    """`B`/`C`의 거래 수 차이(%). 5% 초과면 `C−B`를 순수 가격 효과로 인용하지 않는다."""
    records: list[dict[str, object]] = []
    for timeframe in sorted(set(frame["timeframe"])):
        for segment in SEGMENT_ORDER:
            b = frame[
                (frame["timeframe"] == timeframe)
                & (frame["segment"] == segment)
                & (frame["arm"] == "B")
                & (frame["definition"] == definition)
            ]
            c = frame[
                (frame["timeframe"] == timeframe)
                & (frame["segment"] == segment)
                & (frame["arm"] == "C")
                & (frame["definition"] == definition)
            ]
            if b.empty or c.empty:
                continue
            b_trades = int(b["num_trades"].sum())
            c_trades = int(c["num_trades"].sum())
            pct = abs(c_trades - b_trades) / b_trades if b_trades else float("nan")
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "B_trades": b_trades,
                    "C_trades": c_trades,
                    "diff_pct": pct,
                    "contaminated": bool(pct > 0.05) if b_trades else False,
                }
            )
    return pd.DataFrame(records)


def _loo_arm_mean(base: pd.DataFrame, arm: str, definition: str, drop: str | None) -> float:
    sub = base[(base["arm"] == arm) & (base["definition"] == definition)]
    if drop is not None:
        sub = sub[sub["symbol"] != drop]
    return float(sub["total_return"].mean()) if len(sub) else float("nan")


def symbol_bias(
    frame: pd.DataFrame, *, definition: str = "contained", segment: str = "oos"
) -> pd.DataFrame:
    """leave-one-out — 심볼 하나를 빼면 `B−A`·`C−B`가 어디로 가나."""
    records: list[dict[str, object]] = []
    for timeframe in sorted(set(frame["timeframe"])):
        base = frame[(frame["timeframe"] == timeframe) & (frame["segment"] == segment)]
        symbols = sorted(set(base["symbol"]))
        if len(symbols) < 2:
            continue
        for drop in [None, *symbols]:
            a = _loo_arm_mean(base, "A", "none", drop)
            b = _loo_arm_mean(base, "B", definition, drop)
            c = _loo_arm_mean(base, "C", definition, drop)
            records.append(
                {
                    "timeframe": timeframe,
                    "dropped": "(none)" if drop is None else drop.split("/")[0],
                    "selection_B_minus_A": b - a,
                    "price_C_minus_B": c - b,
                }
            )
    return pd.DataFrame(records)


#: 매칭 널 퇴화 임계 — 겹침 필터가 풀의 이 비율 이상을 그대로 통과시키면(=거의 안 걸러내면)
#: 실제 팔(`B`)이 풀(`A`)과 사실상 같아져 부트스트랩 널이 자기 자신을 검정한다. WAN-124가
#: `run_random_control_b_segment`에서 풀==실제를 ValueError로 막은 것과 같은 취지다.
NULL_DEGENERATE_THRESHOLD = 0.95


def matched_null(
    frame: pd.DataFrame, *, definition: str = "contained", segment: str = "oos"
) -> pd.DataFrame:
    """매칭 널(WAN-70/124식): 겹침 요구 진입(`B`)이 같은 상위TF 존 풀(`A`)의 무작위 진입을
    이기는가 — **단, WAN-124 퇴화 가드를 먼저 적용한다.**

    `B`는 정의상 `A`의 부분집합이다(겹침이 없는 셋업만 뺀 것). 그 겹침 필터가 풀의
    `NULL_DEGENERATE_THRESHOLD`(95%) 이상을 그대로 통과시키면 `B`가 `A`와 거의 같아져
    부트스트랩이 "자기 자신에서 뽑은 부분집합"과 비교하게 된다 — WAN-124가 경계한 퇴화다.
    그때는 p값이 무의미하므로 **부트스트랩을 돌리지 않고 퇴화로 보고**한다(그 사실 자체가
    "필터가 아무것도 안 걸러낸다"는 이 이슈의 핵심 발견이다). 거래 수는 격자 CSV에 이미
    있으므로 재실행 없이 계산한다.
    """
    records: list[dict[str, object]] = []
    for timeframe in sorted(set(frame["timeframe"])):
        a = frame[
            (frame["timeframe"] == timeframe)
            & (frame["segment"] == segment)
            & (frame["arm"] == "A")
        ]
        b = frame[
            (frame["timeframe"] == timeframe)
            & (frame["segment"] == segment)
            & (frame["arm"] == "B")
            & (frame["definition"] == definition)
        ]
        if a.empty or b.empty:
            continue
        pool_trades = int(a["num_trades"].sum())
        real_trades = int(b["num_trades"].sum())
        frac = real_trades / pool_trades if pool_trades else float("nan")
        records.append(
            {
                "timeframe": timeframe,
                "pool_trades_A": pool_trades,
                "real_trades_B": real_trades,
                "overlap_fraction": frac,
                "degenerate": bool(frac >= NULL_DEGENERATE_THRESHOLD),
                "real_return_B": float(b["total_return"].mean()),
                "pool_return_A": float(a["total_return"].mean()),
                "effect_B_minus_A": float(b["total_return"].mean() - a["total_return"].mean()),
            }
        )
    return pd.DataFrame(records)


def sample_gate(frame: pd.DataFrame) -> pd.DataFrame:
    """셀당 거래 수 — 20 미만은 판정 불가(표에는 남긴다)."""
    grp = (
        frame.groupby(["timeframe", "segment", "arm", "definition"], as_index=False)
        .agg(min_trades=("num_trades", "min"), mean_trades=("num_trades", "mean"))
        .reset_index(drop=True)
    )
    grp["ok"] = grp["min_trades"] >= MIN_TRADES
    return grp


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, rec in frame.iterrows():
        lines.append("| " + " | ".join(str(rec[h]) for h in headers) + " |")
    return "\n".join(lines)


_PCT_COLS = frozenset(
    {
        "A",
        "B",
        "C",
        "selection_B_minus_A",
        "price_C_minus_B",
        "total_return",
        "win_rate",
        "max_drawdown",
        "fill_rate",
        "diff_pct",
        "overlap_fraction",
        "real_return_B",
        "pool_return_A",
        "effect_B_minus_A",
    }
)


def _fmt(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if col in _PCT_COLS:
            out[col] = (out[col].astype(float) * 100).round(2)
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(lambda s: str(s).split("/")[0])
    return out.astype(object).where(out.notna(), "—")


def verdict(frame: pd.DataFrame, *, definition: str = "contained") -> list[str]:
    """공식 렌즈 OOS `B−A`(선별)·`C−B`(가격)의 부호로 (a)/(b)/(c)를 읽는다.

    ⚠️ **오염 게이트**(사양 §3): `C−B`가 플러스여도 `B`/`C` 거래 수 차이가 5% 초과면 그것은
    "순수 가격 효과"가 아니라 "거래 집합 차이 + 가격"이라 (b)로 인용하지 않고 **(c)**로
    내린다 — WAN-114가 빠진 "선별과 가격을 못 가른다"는 함정을 자로 막는다.
    """
    dec = decomposition(frame, definition=definition)
    gate = sample_gate(frame)
    contam = trade_contamination(frame, definition=definition)
    lines: list[str] = []
    for timeframe in sorted(set(frame["timeframe"])):
        row = dec[(dec["timeframe"] == timeframe) & (dec["segment"] == "oos")]
        if row.empty:
            continue
        sel = float(row["selection_B_minus_A"].iloc[0])
        price = float(row["price_C_minus_B"].iloc[0])
        b_gate = gate[
            (gate["timeframe"] == timeframe)
            & (gate["segment"] == "oos")
            & (gate["arm"] == "B")
            & (gate["definition"] == definition)
        ]
        c_row = contam[(contam["timeframe"] == timeframe) & (contam["segment"] == "oos")]
        contaminated = (not c_row.empty) and bool(c_row["contaminated"].iloc[0])
        diff_pct = float(c_row["diff_pct"].iloc[0]) if not c_row.empty else float("nan")
        note = ""
        if not b_gate.empty and not bool(b_gate["ok"].iloc[0]):
            note = f" ⚠️ 표본 미달(최소 {int(b_gate['min_trades'].iloc[0])}거래 < {MIN_TRADES})"
        price_usable = price > 0 and not contaminated
        if sel > 0 and price_usable:
            tag = "(a) 선별·가격 모두 플러스"
        elif sel > 0 >= price:
            tag = "(a?) 선별만 플러스 — 유효 선별 규칙 후보"
        elif sel <= 0 < price and price_usable:
            tag = "(b) 선별 0/음수, 가격만 플러스 → 볼린저 부류(가격 효과)"
        elif sel <= 0 < price and contaminated:
            tag = (
                f"(c) 선별 0/음수 · 가격 `C−B`는 플러스지만 거래 수 {diff_pct * 100:.0f}% "
                "차이(오염)라 순수 가격 효과로 못 읽음 — 규칙 값 확인 안 됨"
            )
        else:
            tag = "(c) 둘 다 0/음수 — 규칙 값 없음"
        lines.append(
            f"- **{timeframe} OOS** ({definition}): 선별 `B−A` {sel * 100:+.2f}%p · "
            f"가격 `C−B` {price * 100:+.2f}%p → **{tag}**{note}"
        )
    return lines


def write_summary(rows: Sequence[OverlapRow], path: Path) -> None:
    frame = rows_to_frame(rows)
    dec = decomposition(frame)
    contam = trade_contamination(frame)
    bias = symbol_bias(frame)
    gate = sample_gate(frame)
    null = matched_null(frame)
    lines = [
        "# WAN-126: 다중TF 겹침 캐스케이드 — 선별 대 가격 분리",
        "",
        "재현: `uv run python -m backtest.wan126_multi_tf_overlap`",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · 공식 렌즈 `baseline` 단독(WAN-128) · "
        "채택 기본값(존 지정가 + 오프셋 2bp + 롱 온리 + 고정 1.5R) 고정, 겹침만 옵트인.",
        "",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결, 큐 우선순위 미모델링) — 수치는 **상한**이다.",
        "> ⚠️ `C−B`는 볼린저가 최종 가격을 덮어써 가격 효과의 **하한**이다(wan126.md §3).",
        "> ⚠️ 사다리 5m 칸: 사용자 결정 (A)로 5m 수집 완료 — 1h→15m→5m→1m · 15m→5m→1m.",
        "",
        "## 판정 — 공식 렌즈 OOS `contained`(정본)",
        "",
        *verdict(frame),
        "",
        "## 1. 선별/가격 분해 (`B−A` / `C−B`, 심볼평균 total_return, `contained`)",
        "",
        _md_table(_fmt(dec)),
        "",
        "## 2. 거래 수 오염 — `B` vs `C` (5% 초과면 `C−B` 순수 가격 인용 금지)",
        "",
        _md_table(_fmt(contam)),
        "",
        "## 3. 심볼 편중 (leave-one-out, OOS `contained`)",
        "",
        _md_table(_fmt(bias)),
        "",
        "## 4. 매칭 널 (WAN-70/124식) — `B`가 상위존 풀 `A`의 무작위 진입을 이기나",
        "",
        f"⚠️ **퇴화 가드**: `overlap_fraction`(= `B`거래/`A`거래)이 "
        f"{NULL_DEGENERATE_THRESHOLD:.0%} 이상이면 겹침 필터가 풀을 거의 그대로 통과시킨 "
        "것이라 `B`가 `A`와 사실상 같아진다 — 부트스트랩이 자기 자신을 검정하므로(WAN-124) "
        "**돌리지 않고 퇴화로 표시**한다. 그 자체가 「필터가 아무것도 안 걸러낸다」는 발견이다.",
        "",
        _md_table(_fmt(null)),
        "",
        "## 5. 표본 게이트 (셀당 최소 거래 수, 20 미만 판정 불가)",
        "",
        _md_table(_fmt(gate)),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="1h", help="콤마로 여러 개(예: 1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default="backtest/reports/wan126_multi_tf_overlap.csv"
    )
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan126_multi_tf_overlap_summary.md"
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument("--cache-dir", type=str, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--no-cache", action="store_true", help="LTF 아카이브 디스크 캐시를 쓰지 않는다"
    )
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan126] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        cache_dir = None if args.no_cache else args.cache_dir
        rows = run_report(symbols, timeframes, start=args.start, end=args.end, cache_dir=cache_dir)
        if args.append and out_csv.exists():
            existing = rows_from_csv(out_csv)
            keep = [r for r in existing if r.timeframe not in set(timeframes)]
            rows = keep + rows
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan126] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
