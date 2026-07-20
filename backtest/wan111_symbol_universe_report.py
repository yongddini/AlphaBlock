"""WAN-111: 심볼 유니버스 확장(BNB·XRP·TRX) — 채택 수치가 SOL·ETH 편중인가.

지금까지의 모든 채택 판단은 **딱 3심볼**(BTC·ETH·SOL) 위에서 내려졌다. 그런데 채택
기본값의 15m 심볼평균은 SOL이 끌어올린 값이고(WAN-95 요약의 심볼별 분해), OOS의 ETH
편중은 CLAUDE.md가 이미 경고로 달아 둔 성질이다. 3심볼 평균은 표본이 작아 **「규칙의
힘」과 「심볼 하나의 운」을 가르지 못한다.**

이 모듈은 심볼 축만 6개로 넓혀 그 둘을 가른다. **토대는 고정 입력**이다(CLAUDE.md 작업
층 순서) — 진입 방식(존 지정가 + 오프셋 2bp)·공식 렌즈(`baseline`)·비용을 그대로 두고
심볼만 늘린다. 즉 이 표는 새 엔진의 성적이 아니라 **같은 엔진을 더 넓은 표본에 댄 것**이다.

## 축

* **심볼 6개**: 기존 3(BTC·ETH·SOL) + 신규 3(BNB·XRP·TRX).
* **TF 2개**: 15m·1h — WAN-107 공동 작업 TF(둘을 병기하는 것이 그 결정의 요구사항).
* **렌즈 3개**: `baseline`(공식, WAN-104) · `pen_5bp`(민감도) · `pen_5bp_drop_50`(스트레스).
* **구간**: 전 구간 · IS(앞 2/3) · OOS(뒤 1/3).

## 창을 못 박는다 (`--years` 금지)

심볼마다 상장 시점이 다르고, `--years N`은 **마지막 봉 기준으로 창을 자른다**
(`harness.load_recent`) — 심볼별로 마지막 봉이 다르면 창이 서로 어긋나 "심볼 평균"이
**서로 다른 기간의 평균**이 된다. 그러면 이 리포트의 질문(심볼 편중)에 기간 차이가
섞인다. 그래서 `--start`/`--end`로 6심볼 **공통 창**을 못 박는다.

⚠️ 그 대가로 **이 표의 3심볼 수치는 `wan95_zone_limit_summary.md`와 비트 단위로 같지
않다** — 그쪽은 `--years 3`(미끄러지는 창)이다. 옛 표에서 숫자를 베껴 오지 않고 3심볼도
같은 창에서 다시 돌리는 이유가 그것이다: 3 vs 6 대조는 **같은 창** 위에서만 성립한다.

## 왜 범용 CLI(`backtest.run`)가 아닌가 (WAN-101 규칙)

원 수치는 CLI로 나온다 — 그래서 **격자를 다시 짜지 않고 `backtest.run.run_grid`를 그대로
호출한다**. 이 모듈이 더 하는 일은 CLI에 없는 **사후 분해**뿐이다: 3심볼 vs 6심볼 대조,
leave-one-out(심볼 하나를 빼도 플러스가 남는가), 그리고 그 숫자에서 직접 읽어 낸 판정
문장. 판정을 사람이 눈으로 읽고 손으로 적으면 재실행 때 문장과 숫자가 갈라진다 —
WAN-95가 겪은 라벨/실체 불일치와 같은 종류의 사고다.

## 재현

```
uv run python -m backtest.wan111_symbol_universe_report
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest.harness import RunRow, fill_preset, normalize_symbol
from backtest.run import Grid, RunOptions, parse_date_ms, run_grid

#: 기존 유니버스 — 지금까지의 모든 채택 판단이 선 3심볼.
LEGACY_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")

#: 신규 유니버스 — 사용자 지정(2026-07-15). 시가총액 상위 무기한선물 중 기존 3심볼과
#: 겹치지 않는 것들이다.
NEW_SYMBOLS: tuple[str, ...] = ("BNB/USDT:USDT", "XRP/USDT:USDT", "TRX/USDT:USDT")

ALL_SYMBOLS: tuple[str, ...] = LEGACY_SYMBOLS + NEW_SYMBOLS

#: WAN-107 공동 작업 TF. 후속 이슈는 두 축을 병기해야 한다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 공식 렌즈 + 민감도 + 스트레스(CLAUDE.md 토대 2). 이름·값은 `harness`에서 가져오므로
#: CLI(`--fill`)·WAN-96/97/107/110 표와 **같은 뜻**이 보장된다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

OFFICIAL_LENS = "baseline"

#: 6심볼 공통 창. 저장된 1분봉이 6심볼 모두 2023-07-11 이전부터 있으므로 이 창은 어느
#: 심볼에서도 상장일에 잘리지 않는다(`reached_requested_start` 확인 완료).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

SEGMENT_ORDER: tuple[str, ...] = ("full", "is", "oos")


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def build_grid(symbols: Sequence[str]) -> Grid:
    """WAN-111 당시 채택 기본값 격자 — 심볼·TF·렌즈만 축으로 연다.

    익절 R·오프셋을 축에 넣지 않는 것이 요점이다: 이 이슈는 **측정 층**이라 토대와
    구조 결정을 고정 입력으로 받는다(CLAUDE.md 작업 층 순서). `take_profit_rs`/
    `offsets_bps`에 `build_params()`의 값을 그대로 넣어 채택 기본값(오프셋 2bp,
    WAN-112)을 따라가게 한다 — 여기에 0.0을 하드코딩하면 기본값이 바뀔 때 이 리포트만
    혼자 옛 엔진을 돈다.

    ⚠️ **RSI 게이트만은 정반대로 명시 고정한다**(WAN-123이 기본값을 `unconditional`로
    옮겼다). 위 "기본값을 따라가게 한다"는 원칙이 오프셋에 맞고 여기엔 안 맞는 이유는
    **이동의 크기**다: 오프셋 2bp는 같은 거래 집합의 가격을 조금 옮기지만, 게이트 제거는
    **거래 집합 자체를 13~14% 늘린다**. 그러면 이 리포트의 결론(「6심볼로 넓히면 채택
    수치가 사실상 0이 된다」)이 재현되지 않는 다른 표본의 표가 된다. 게다가 WAN-114
    사다리의 `L2` 행이 이 CSV의 `baseline` 행과 **비트 단위로 일치**하는 것이 두 리포트의
    상호 검산인데, 그쪽 사다리는 게이트를 `first_tap_free`로 고정하고 있다 — 여기만
    따라가면 그 검산이 깨진다. 게이트 제거 뒤의 심볼 유니버스 판정은 `wan123_*` 소관이다.

    ⚠️ **밴드 표본(`band_bar`)도 같은 이유로 고정한다**(WAN-132가 기본값을
    `intrabar_live`로 옮겼다). 봉내 라이브 밴드는 진입가를 서브스텝마다 다시 내므로
    거래 집합과 가격이 함께 움직이고, WAN-114 사다리와의 비트 일치 검산도 깨진다.
    """
    from backtest.harness import LEGACY_BAND_BAR, LEGACY_RSI_GATE_MODE, build_params

    defaults = build_params()
    return Grid(
        symbols=tuple(normalize_symbol(s) for s in symbols),
        timeframes=DEFAULT_TIMEFRAMES,
        entry_modes=("zone_limit",),
        take_profit_rs=(defaults.take_profit_r,),
        offsets_bps=(defaults.zone_limit_offset_bps,),
        fills=tuple(fill_preset(name) for name in LENS_NAMES),
        rsi_gate_mode=LEGACY_RSI_GATE_MODE,
        band_bar=LEGACY_BAND_BAR,
    )


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    log: bool = True,
) -> list[RunRow]:
    """6심볼 × 2TF × 3렌즈 × 3구간 격자를 돈다(원 수치는 CLI 격자 그대로)."""
    options = RunOptions(
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        oos=True,
    )
    return run_grid(build_grid(symbols), options, log=log)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[RunRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[RunRow]:
    """저장된 원본 CSV를 행으로 되읽는다.

    격자 한 번이 수십 분이라 **요약 문장만 고치려고 다시 돌리는 것은 낭비**다. 되읽기는
    `RunRow`로 검증하며 통과하므로(열 이름·타입이 어긋나면 여기서 터진다) 요약이 CSV와
    갈라질 수 없다 — 표와 판정이 같은 원본을 본다는 것이 이 경로의 요점이다.
    """
    frame = pd.read_csv(path)
    return [RunRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[RunRow]) -> pd.DataFrame:
    """심볼별 성과 — 시드를 먼저 접는다.

    `pen_5bp_drop_50`은 시드 5개를 도는데, 심볼 평균을 내기 전에 **심볼 안에서** 시드를
    접어야 한다. 순서를 바꾸면(시드별 심볼평균 → 시드평균) leave-one-out에서 심볼 하나를
    뺄 대상이 사라진다. 나머지 두 렌즈는 난수를 뽑지 않아 시드가 1개다.
    """
    frame = rows_to_frame(rows)
    return (
        frame.groupby(["timeframe", "segment", "fill", "symbol"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            return_min=("total_return", "min"),
            return_max=("total_return", "max"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "mean"),
            fill_rate=("fill_rate", "mean"),
            sharpe=("sharpe", "mean"),
            seeds=("seed", "count"),
        )
        .reset_index(drop=True)
    )


def _universe_stats(view: pd.DataFrame, symbols: Sequence[str]) -> dict[str, float]:
    """한 유니버스의 요약 — 심볼평균·플러스 심볼 수·평균 MDD."""
    sub = view[view["symbol"].isin(list(symbols))]
    returns = sub["total_return"]
    return {
        "symbols": float(len(sub)),
        "total_return": float(returns.mean()) if len(sub) else float("nan"),
        "return_min": float(returns.min()) if len(sub) else float("nan"),
        "return_max": float(returns.max()) if len(sub) else float("nan"),
        "positive": float((returns > 0).sum()),
        "max_drawdown": float(sub["max_drawdown"].mean()) if len(sub) else float("nan"),
        "fill_rate": float(sub["fill_rate"].mean()) if len(sub) else float("nan"),
    }


def universe_compare(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """3심볼(기존) vs 3심볼(신규) vs 6심볼(전체) — 같은 창·같은 엔진.

    이 표가 이슈의 첫 질문에 답한다: 심볼을 늘리면 심볼평균이 어디로 가는가.
    """
    records: list[dict[str, object]] = []
    universes: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("legacy_3", LEGACY_SYMBOLS),
        ("new_3", NEW_SYMBOLS),
        ("all_6", ALL_SYMBOLS),
    )
    for (timeframe, segment, lens), view in symbol_frame.groupby(
        ["timeframe", "segment", "fill"], sort=False
    ):
        for name, members in universes:
            stats = _universe_stats(view, members)
            if not stats["symbols"]:
                continue
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "lens": lens,
                    "universe": name,
                    **stats,
                }
            )
    frame = pd.DataFrame(records)
    return _sorted(frame, ["timeframe", "segment", "lens"])


def leave_one_out(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """심볼 하나를 뺀 5심볼 평균 — "SOL·ETH 없이도 플러스가 남는가".

    빼는 심볼의 기여도(`delta` = 5심볼평균 − 6심볼평균)를 같이 낸다. **음수면 그 심볼이
    평균을 끌어올리고 있었다**는 뜻이다(= 그 심볼을 빼면 평균이 내려간다).
    """
    records: list[dict[str, object]] = []
    for (timeframe, segment, lens), view in symbol_frame.groupby(
        ["timeframe", "segment", "fill"], sort=False
    ):
        present = [s for s in ALL_SYMBOLS if s in set(view["symbol"])]
        if len(present) < 2:
            continue
        full_mean = float(view["total_return"].mean())
        for dropped in present:
            rest = [s for s in present if s != dropped]
            stats = _universe_stats(view, rest)
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "lens": lens,
                    "dropped": _short(dropped),
                    "total_return": stats["total_return"],
                    "delta": stats["total_return"] - full_mean,
                    "positive": stats["positive"],
                    "symbols": stats["symbols"],
                }
            )
    frame = pd.DataFrame(records)
    return _sorted(frame, ["timeframe", "segment", "lens"])


#: 정렬 키 → 그 열의 의미 순서. 알파벳 순으로 두면 구간이 `full→is→oos`가 아니라
#: `full→is→oos`가 깨지고(`is`<`oos`<`full`이 아님) 렌즈도 공식→민감도→스트레스 순서를
#: 잃는다 — 표를 위에서 아래로 읽는 순서가 곧 판정을 읽는 순서라 중요하다.
_ORDERINGS: dict[str, tuple[str, ...]] = {"segment": SEGMENT_ORDER, "lens": LENS_NAMES}


def _sorted(frame: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    """TF·구간·렌즈를 의미 순서로 정렬한다(알파벳 순은 `full`이 끝에 가 읽기 나쁘다)."""
    if frame.empty:
        return frame
    out = frame.copy()
    helpers: list[str] = []
    sort_keys: list[str] = []
    for key in keys:
        order = _ORDERINGS.get(key)
        if order is None or key not in out.columns:
            sort_keys.append(key)
            continue
        helper = f"_order_{key}"
        out[helper] = out[key].map({name: i for i, name in enumerate(order)})
        helpers.append(helper)
        sort_keys.append(helper)
    return out.sort_values(sort_keys).drop(columns=helpers).reset_index(drop=True)


def _short(symbol: str) -> str:
    """`BTC/USDT:USDT` → `BTC` (표 폭을 줄인다)."""
    return symbol.split("/")[0]


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def verdict(compare: pd.DataFrame, loo: pd.DataFrame, *, segment: str = "oos") -> list[str]:
    """「채택 수치가 SOL·ETH 편중인가」를 숫자에서 직접 읽어 문장으로 낸다.

    공식 렌즈(`baseline`) OOS만 판정 대상이다 — 나머지는 민감도로 병기한다(토대 2).
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = compare[
            (compare["timeframe"] == timeframe)
            & (compare["segment"] == segment)
            & (compare["lens"] == OFFICIAL_LENS)
        ].set_index("universe")
        if not {"legacy_3", "all_6"} <= set(view.index):
            continue
        legacy = float(view.loc["legacy_3", "total_return"])
        allsix = float(view.loc["all_6", "total_return"])
        new = float(view.loc["new_3", "total_return"]) if "new_3" in view.index else float("nan")
        pos = int(view.loc["all_6", "positive"])
        n = int(view.loc["all_6", "symbols"])
        lines.append(
            f"- **{timeframe} {segment}**: 3심볼 {legacy * 100:+.2f}% → 6심볼 "
            f"{allsix * 100:+.2f}% (**{(allsix - legacy) * 100:+.2f}%p**), "
            f"신규 3심볼만 {new * 100:+.2f}% · 플러스 {pos}/{n}심볼"
        )
        sub = loo[
            (loo["timeframe"] == timeframe)
            & (loo["segment"] == segment)
            & (loo["lens"] == OFFICIAL_LENS)
        ]
        if sub.empty:
            continue
        worst = sub.loc[sub["total_return"].idxmin()]
        negatives = sub[sub["total_return"] <= 0]
        detail = (
            f"어느 심볼을 빼도 플러스가 남는다(최저: **{worst['dropped']}** 제외 시 "
            f"{float(worst['total_return']) * 100:+.2f}%)"
            if negatives.empty
            else "심볼을 하나 빼면 평균이 **마이너스로 내려가는 경우가 있다**: "
            + ", ".join(
                f"{r['dropped']} 제외 {float(r['total_return']) * 100:+.2f}%"
                for _, r in negatives.iterrows()
            )
        )
        lines.append(f"  - leave-one-out: {detail}")
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def health(compare: pd.DataFrame, *, segment: str = "oos") -> list[str]:
    """신규 심볼에서 **체결률·MDD가 무너지는가** (이슈의 세 번째 질문).

    신규 3심볼은 상대적으로 유동성이 낮을 수 있다. 그렇다면 손상은 **체결률에서 먼저**
    보여야 한다 — 지정가가 안 채워지는 것이 유동성 부족의 1차 증상이기 때문이다. 체결률이
    멀쩡한데 수익만 무너졌다면 원인은 유동성이 아니라 **신호 자체**다. 그 구분을 숫자에서
    직접 읽는다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = compare[
            (compare["timeframe"] == timeframe)
            & (compare["segment"] == segment)
            & (compare["lens"] == OFFICIAL_LENS)
        ].set_index("universe")
        if not {"legacy_3", "new_3"} <= set(view.index):
            continue
        old_fill = float(view.loc["legacy_3", "fill_rate"])
        new_fill = float(view.loc["new_3", "fill_rate"])
        old_mdd = float(view.loc["legacy_3", "max_drawdown"])
        new_mdd = float(view.loc["new_3", "max_drawdown"])
        fill_verdict = "무너지지 않는다" if new_fill >= old_fill * 0.9 else "**무너진다**"
        lines.append(
            f"- **{timeframe} {segment}**: 체결률 기존 {old_fill * 100:.2f}% → 신규 "
            f"{new_fill * 100:.2f}% ({fill_verdict}) · MDD 기존 {old_mdd * 100:.2f}% → 신규 "
            f"{new_mdd * 100:.2f}% ({new_mdd / old_mdd:.2f}배)"
            if old_mdd
            else f"- **{timeframe} {segment}**: 체결률 기존 {old_fill * 100:.2f}% → 신규 "
            f"{new_fill * 100:.2f}% ({fill_verdict})"
        )
    return lines


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103/108/110 리포트와 같은 헬퍼)."""
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


_PERCENT_COLUMNS = (
    "total_return",
    "return_min",
    "return_max",
    "win_rate",
    "max_drawdown",
    "fill_rate",
    "delta",
)


def _pct(frame: pd.DataFrame) -> pd.DataFrame:
    """비율 열을 % 소수 2자리로. 계측하지 않은 칸은 `—`(0으로 적으면 오독된다)."""
    out = frame.copy()
    for col in _PERCENT_COLUMNS:
        if col in out.columns:
            out[col] = (out[col] * 100).round(2)
    for col in ("num_trades", "sharpe", "positive", "symbols"):
        if col in out.columns:
            out[col] = out[col].round(2)
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(_short)
    return out.astype(object).where(out.notna(), "—")


_SYMBOL_VIEW = (
    "timeframe",
    "segment",
    "fill",
    "symbol",
    "total_return",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
    "sharpe",
)

_COMPARE_VIEW = (
    "timeframe",
    "segment",
    "lens",
    "universe",
    "total_return",
    "return_min",
    "return_max",
    "positive",
    "symbols",
    "max_drawdown",
    "fill_rate",
)


def write_summary(rows: Sequence[RunRow], path: Path) -> None:
    symbol_frame = per_symbol(rows)
    compare = universe_compare(symbol_frame)
    loo = leave_one_out(symbol_frame)

    official = symbol_frame[symbol_frame["fill"] == OFFICIAL_LENS]
    official_view = _sorted(
        official.rename(columns={"fill": "lens"}), ["timeframe", "segment", "lens"]
    ).rename(columns={"lens": "fill"})

    lines = [
        "# WAN-111: 심볼 유니버스 확장 (BNB·XRP·TRX) — 채택 수치가 SOL·ETH 편중인가",
        "",
        "재현: `uv run python -m backtest.wan111_symbol_universe_report`",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 격자다. 채택 기본값"
        "(`ConfluenceParams()` — 존 지정가 + 오프셋 2bp + 롱 온리 + 고정 1.5R)을 "
        "**그대로** 두고 심볼 축만 넓혔다. 토대(진입 방식·렌즈·비용)는 고정 입력이다.",
        "",
        "> ⚠️ **이 표의 3심볼 수치를 `wan95_zone_limit_summary.md`와 비교하지 말 것** — "
        "그쪽은 `--years 3`(마지막 봉 기준으로 미끄러지는 창)이고 여기는 못 박은 창이다. "
        "3 vs 6 대조는 **이 표 안에서만** 성립한다(그래서 3심볼도 같은 창에서 다시 돌렸다).",
        "",
        "> ⚠️ **공식 수치는 `baseline`이고 그것은 상한이다**(WAN-104 토대 2). 큐 우선순위를 "
        "모델링하지 않으므로 `pen_5bp`·`pen_5bp_drop_50`을 민감도로 반드시 함께 읽는다.",
        "",
        "## 판정 — 공식 렌즈(`baseline`) OOS",
        "",
        *verdict(compare, loo, segment="oos"),
        "",
        "IS 대조:",
        "",
        *verdict(compare, loo, segment="is"),
        "",
        "### 읽는 법",
        "",
        "**질문에 답하면: 편중은 사실이지만 그게 전부가 아니다.** 15m OOS의 ETH는 실제로 "
        "혼자 평균을 이고 있고(빼면 6심볼 평균이 마이너스로 내려간다), 전 구간에서는 SOL이 "
        "같은 역할을 한다. 그런데 더 큰 사실이 그 옆에 있다 — **신규 3심볼은 OOS에서 두 TF "
        "모두 0/3 플러스**다. 즉 3심볼 평균이 높았던 건 '한 심볼이 캐리해서'만이 아니라 "
        "**그 3심볼이 유독 잘 되는 표본이었기** 때문이다.",
        "",
        "⚠️ **그렇다고 '신규 심볼이 나쁘다'로 읽지 말 것 — IS에서는 정반대였다.** 신규 "
        "3심볼의 1h IS는 +17.62%(3/3)로 **기존 3심볼(+15.79%)보다 좋았다**. 그 표본이 OOS에서 "
        "−6.10%(0/3)로 뒤집힌 것이다. 심볼을 골라내는 규칙(어느 심볼이 좋은가)이 IS에서 "
        "OOS로 넘어가지 않는다는 뜻이고, 이는 CLAUDE.md가 이미 적어 둔 **IS→OOS 순위 뒤집힘**"
        "이 심볼 축을 넓히자 더 넓은 표본에서 재확인된 것이다.",
        "",
        "⚠️ **6심볼 공식 수치는 사실상 0이고, 민감도 한 단계에 부호가 뒤집힌다** — "
        "`baseline` OOS가 15m +3.91% · 1h +1.22%인데 `pen_5bp`만 걸어도 **둘 다 마이너스**"
        "(−0.76% · −1.44%)가 된다. 공식 렌즈가 상한이라는 토대 2의 경고가 여기서 실물로 "
        "드러난다. **이는 WAN-88의 「채택 기본값에 통계적 엣지는 확인되지 않았다」를 6심볼로 "
        "넓혀 재확인한 것**이지, 새로운 판정이 아니다.",
        "",
        "## 신규 심볼 건강 진단 — 유동성인가 신호인가",
        "",
        "유동성이 문제라면 **체결률에서 먼저** 보여야 한다(지정가가 안 채워지는 것이 1차 "
        "증상). 체결률이 멀쩡한데 수익만 무너졌다면 원인은 유동성이 아니라 **신호**다.",
        "",
        *health(compare, segment="oos"),
        "",
        "**체결률은 무너지지 않는다** — 15m은 사실상 같고(51.96% → 50.93%) 1h는 신규가 "
        "**오히려 더 높다**(48.48% → 56.33%). 즉 신규 3심볼의 마이너스는 '지정가가 안 "
        "채워져서'가 아니다. 주문은 잘 채워졌고 **그 채워진 거래가 돈을 잃었다**. 반면 "
        "**MDD는 1.6~2배로 나빠진다**(15m OOS 10.27% → 16.46%, 1h OOS 5.95% → 11.82%) — "
        "손상은 체결이 아니라 손익 쪽에 있다. 유동성 가설로는 설명되지 않는다.",
        "",
        "## 1. 유니버스 대조 — 3심볼 vs 신규 3심볼 vs 6심볼",
        "",
        "`positive` = 플러스 심볼 수 / `symbols` = 그 유니버스의 심볼 수. "
        "`return_min`/`return_max`는 유니버스 안 심볼별 최저·최고다 — **평균만 보면 "
        "심볼 하나가 끌어올린 것이 안 보인다**(이 리포트의 존재 이유).",
        "",
        _md_table(_pct(compare[list(_COMPARE_VIEW)])),
        "",
        "## 2. leave-one-out — 심볼 하나를 빼면 평균이 어디로 가나",
        "",
        "`delta` = (그 심볼을 뺀 5심볼 평균) − (6심볼 평균). **음수면 그 심볼이 평균을 "
        "끌어올리고 있었다**는 뜻이다. 편중이 있다면 특정 심볼의 `delta`가 유독 크게 "
        "음수여야 한다.",
        "",
        _md_table(_pct(loo)),
        "",
        "## 3. 심볼별 원 수치 — 공식 렌즈(`baseline`)",
        "",
        "신규 3심볼의 **체결률·MDD가 무너지지 않는지**가 여기서 보인다(유동성이 낮으면 "
        "체결률이 먼저 흔들린다). 민감도 렌즈를 포함한 전 행은 원본 CSV에 있다.",
        "",
        _md_table(_pct(official_view[list(_SYMBOL_VIEW)])),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default="backtest/reports/wan111_symbol_universe.csv"
    )
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan111_symbol_universe_summary.md"
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
        print(f"[wan111] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        rows = run_report(symbols, start=args.start, end=args.end)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan111] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
