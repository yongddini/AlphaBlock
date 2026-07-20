"""WAN-124 3단: 게이트 제거가 늘린 거래는 「스치듯 닿은 체결」에 몰려 있나.

[WAN-123](../docs/decisions/wan123.md)이 재탭 RSI 게이트를 빼자 거래 집합 자체가 넓어졌고
체결률이 ~51% → ~81%로 뛰었다. 그런데 그 81%는 **`baseline`("닿으면 체결") 위의 값**이라
큐 우선순위 걱정이 줄어든 게 아니라 **커졌다** — 낙관 가정에 기대는 체결이 더 많아졌다는
뜻이기 때문이다. 이 모듈이 그 낙관을 정량화한다.

물음(WAN-96식): **게이트가 새로 들여보낸 거래가 유독 「지정가를 스치듯 닿고 되돌아선
체결」에 몰려 있는가.** WAN-96의 진단 도구는 **비대칭**이다 — 관통을 요구했을 때 *거래는
조금* 줄어드는데 *수익은 많이* 사라지면, 그 수익은 관통 없는 체결에 실려 있다는 뜻이다.
그 비대칭을 **게이트 on/off 두 팔에서 각각** 재고 맞댄다.

## 축

* **게이트 2개**: `first_tap_free`(WAN-81~122 채택) · `unconditional`(WAN-123 채택).
  격자 **축이 아니라 핀**이라 `run_grid`를 두 번 돈다(`Grid.rsi_gate_mode`).
* **심볼 6개**(WAN-111) · **TF 2개**(15m·1h, WAN-107) · **구간** 전 구간·IS·OOS.
* **렌즈 3개**: `baseline`(공식, WAN-104) · `pen_5bp`(민감도) · `pen_5bp_drop_50`(스트레스).
* **창**: WAN-111/114/115/119/120과 **같은 못 박은 창**(2023-07-14~2026-07-15).

## 이 표는 자기 자신을 검산한다

`first_tap_free` 팔은 **WAN-111 격자와 같은 엔진·같은 창·같은 경로**(`run_grid`)다 — 그래서
그 팔의 행이 `wan111_symbol_universe.csv`와 **비트 단위로 일치**해야 한다(`--check-wan111`이
확인한다). 일치하면 이 표의 `unconditional` 팔이 낸 이동은 **게이트 제거의 몫**이지 창·
엔진·경로가 흔들린 결과가 아니다.

⚠️ **`wan95_zone_limit_summary.md`의 「거래 +19~27%」와 이 표의 이동을 같은 수치로 인용하지
말 것** — 그쪽은 `--years 3`(미끄러지는 창) 3심볼이고 이 표는 못 박은 창 6심볼이다.
CLAUDE.md가 이미 그 비교를 금지한다.

## 왜 범용 CLI(`backtest.run`)가 아닌가 (WAN-101 규칙)

원 수치는 CLI 격자 그대로다 — 그래서 격자를 다시 짜지 않고 `run_grid`를 호출한다. 다만
`rsi_gate_mode`는 CLI 플래그로 **열려 있지 않고**(핀이다), WAN-96 비대칭 분해와 판정
문장은 CLI가 내지 못한다(WAN-101 예외 조항: 사후 분해·결론 문장).

## 재현

```
uv run python -m backtest.wan123_fill_conservatism              # 격자 재실행(길다)
uv run python -m backtest.wan123_fill_conservatism --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest.harness import (
    LEGACY_BAND_BAR,
    LEGACY_RSI_GATE_MODE,
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    RunRow,
    build_params,
    fill_preset,
    normalize_symbol,
)
from backtest.run import Grid, RunOptions, parse_date_ms, run_grid
from strategy.models import RsiGateMode

#: 심볼 유니버스 = 6심볼(WAN-111).
ALL_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

#: WAN-107 공동 작업 TF.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 공식 렌즈 + 민감도 + 스트레스(CLAUDE.md 토대 2).
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

OFFICIAL_LENS = "baseline"
PENALIZED_LENS = "pen_5bp"
STRESS_LENS = "pen_5bp_drop_50"

#: WAN-111/114/115/119/120과 같은 못 박은 창.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS)

#: 게이트 두 팔. 키가 CSV의 `gate` 열이 된다.
#:
#: ⚠️ `"none"`을 쓰면 안 된다 — 게이트 **판정**만 없앨 뿐 워밍업 요구(`rsi is not None`)를
#: 남겨 RSI 워밍업 구간의 탭을 계속 막는다(WAN-123 §경고). 채택 기본값은 `unconditional`이다.
GATE_ARMS: dict[str, RsiGateMode] = {
    "first_tap_free": LEGACY_RSI_GATE_MODE,
    "unconditional": "unconditional",
}

#: 채택 기본값의 게이트 — 이 팔이 「지금 돌고 있는 것」이다.
ADOPTED_ARM = "unconditional"

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CSV = REPORTS_DIR / "wan123_fill_conservatism.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan123_fill_conservatism_summary.md"
WAN111_CSV = REPORTS_DIR / "wan111_symbol_universe.csv"


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def build_grid(symbols: Sequence[str], gate: RsiGateMode, timeframes: Sequence[str]) -> Grid:
    """한 게이트 팔의 격자 — 게이트 말고는 **전부 채택 기본값을 따라간다**.

    익절 R·오프셋을 `build_params()`에서 읽는 이유는 WAN-111/114와 같다: 값을 상수로 박아
    두면 기본값이 재-베이스라인으로 움직일 때 이 리포트만 혼자 옛 엔진을 돈다. 이 표에서
    **게이트만이 축**이어야 그 이동을 게이트의 몫으로 읽을 수 있다.

    ⚠️ **밴드 표본(`band_bar`)만은 예외로 당시 값(`tap`)에 고정한다**(WAN-132가 기본값을
    `intrabar_live`로 옮겼다). 위 원칙과 어긋나 보이지만 이 팔들의 검산이 그걸 요구한다 —
    `first_tap_free` 팔이 `wan111_symbol_universe.csv`와 **비트 단위로 일치**하는 것이
    「이동이 게이트의 몫」이라는 주장의 근거이고, 그쪽 CSV는 탭 봉 종가 밴드에서 나왔다.
    새 밴드에서의 게이트 재검은 별도 이슈 소관이다(WAN-132 §후속).
    """
    defaults = build_params()
    return Grid(
        symbols=tuple(normalize_symbol(s) for s in symbols),
        timeframes=tuple(timeframes),
        entry_modes=("zone_limit",),
        take_profit_rs=(defaults.take_profit_r,),
        offsets_bps=(defaults.zone_limit_offset_bps,),
        fills=tuple(fill_preset(name) for name in LENS_NAMES),
        band_bar=LEGACY_BAND_BAR,
        rsi_gate_mode=gate,
    )


class GateRow(RunRow):
    """`RunRow` + 게이트 팔 라벨.

    `RunRow`를 상속하는 이유는 열 순서·타입이 CLI/WAN-111 CSV와 **같아야** 검산이 되기
    때문이다 — 열을 새로 정의하면 두 CSV를 나란히 놓고 읽을 수 없다.
    """

    gate: str


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    log: bool = True,
) -> list[GateRow]:
    """게이트 2팔 × 6심볼 × 2TF × 3렌즈 × 3구간 격자를 돈다."""
    options = RunOptions(start_ms=parse_date_ms(start), end_ms=parse_date_ms(end), oos=True)
    rows: list[GateRow] = []
    for arm, gate in GATE_ARMS.items():
        if log:
            print(f"[wan123-fill] === 게이트 팔: {arm} ===", flush=True)
        grid_rows = run_grid(build_grid(symbols, gate, timeframes), options, log=log, jobs=jobs)
        rows.extend(GateRow(gate=arm, **row.model_dump()) for row in grid_rows)
    return rows


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[GateRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[GateRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고칠 때 격자를 다시 돌지 않는다)."""
    frame = pd.read_csv(path)
    return [GateRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[GateRow]) -> pd.DataFrame:
    """심볼별 성과 — **시드를 먼저 접는다**.

    `pen_5bp_drop_50`은 시드 5개를 도는데 심볼 평균을 내기 전에 **심볼 안에서** 시드를
    접어야 한다(WAN-111 `per_symbol`과 같은 이유). 순서를 바꾸면 심볼 축이 사라진다.
    """
    frame = rows_to_frame(rows)
    return frame.groupby(["gate", "timeframe", "segment", "fill", "symbol"], as_index=False).agg(
        total_return=("total_return", "mean"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "mean"),
        fill_rate=("fill_rate", "mean"),
        eligible_setups=("eligible_setups", "mean"),
    )


def symbol_mean(rows: Sequence[GateRow]) -> pd.DataFrame:
    """심볼평균 — 채택 판단이 읽는 축(WAN-111/114와 같은 집계 순서)."""
    view = per_symbol(rows)
    return view.groupby(["gate", "timeframe", "segment", "fill"], as_index=False).agg(
        total_return=("total_return", "mean"),
        plus_symbols=("total_return", lambda s: int((s > 0).sum())),
        symbols=("total_return", "count"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "mean"),
        fill_rate=("fill_rate", "mean"),
    )


#: 비대칭·델타 표의 열. **비어 있어도 열은 있어야 한다** — `pd.DataFrame([])`는 열이
#: 없어서 `frame["gate"]`가 `KeyError`로 터진다. 격자를 `--symbols`로 좁히면 짝이 되는
#: 렌즈 행이 없는 실행이 실제로 생기므로, 빈 표는 예외가 아니라 정상 경로다.
_ASYMMETRY_COLUMNS: tuple[str, ...] = (
    "gate",
    "timeframe",
    "segment",
    "base_return",
    "pen_return",
    "base_trades",
    "pen_trades",
    "trade_retention",
    "return_retention",
    "base_fill_rate",
)

_DELTA_COLUMNS: tuple[str, ...] = (
    "timeframe",
    "segment",
    "fill",
    "old_return",
    "new_return",
    "return_delta",
    "old_trades",
    "new_trades",
    "trade_growth",
    "old_fill_rate",
    "new_fill_rate",
    "new_plus_symbols",
    "symbols",
)


def _lookup(view: pd.DataFrame, **keys: object) -> pd.Series | None:
    mask = pd.Series(True, index=view.index)
    for column, value in keys.items():
        mask &= view[column] == value
    hit = view[mask]
    return None if hit.empty else hit.iloc[0]


# --------------------------------------------------------------------------- #
# WAN-96 비대칭
# --------------------------------------------------------------------------- #


def asymmetry_table(rows: Sequence[GateRow]) -> pd.DataFrame:
    """게이트 팔 × TF × 구간의 **WAN-96 비대칭** — 거래는 얼마 줄고 수익은 얼마 사라지나.

    `baseline` → `pen_5bp`로 갈 때의 잔존율을 낸다:

    * `trade_retention` = 관통을 요구해도 남는 **거래**의 비율.
    * `return_retention` = 남는 **수익**의 비율.

    둘의 간극이 WAN-96이 읽은 그 비대칭이다 — 거래는 거의 그대로인데 수익만 사라지면 그
    수익은 **관통 없이 스치듯 닿은 체결**에 실려 있다.

    ⚠️ `return_retention`은 기준 수익이 **음수이거나 0에 가까우면 뜻을 잃는다**(WAN-115가
    1h 증분에서 겪은 부호 함정 그대로 — 더 나빠지는데 비율이 100%를 넘게 찍힌다). 그런
    셀은 `None`으로 두고 표에 `—`로 적는다.
    """
    view = symbol_mean(rows)
    records: list[dict[str, object]] = []
    for gate in GATE_ARMS:
        for timeframe in DEFAULT_TIMEFRAMES:
            for segment in SEGMENT_ORDER:
                base = _lookup(
                    view, gate=gate, timeframe=timeframe, segment=segment, fill=OFFICIAL_LENS
                )
                pen = _lookup(
                    view, gate=gate, timeframe=timeframe, segment=segment, fill=PENALIZED_LENS
                )
                if base is None or pen is None:
                    continue
                base_return = float(base["total_return"])
                base_trades = float(base["num_trades"])
                records.append(
                    {
                        "gate": gate,
                        "timeframe": timeframe,
                        "segment": segment,
                        "base_return": base_return,
                        "pen_return": float(pen["total_return"]),
                        "base_trades": base_trades,
                        "pen_trades": float(pen["num_trades"]),
                        "trade_retention": (
                            float(pen["num_trades"]) / base_trades if base_trades > 0 else None
                        ),
                        # 부호 함정 가드 — 기준이 양수일 때만 잔존율이 뜻을 가진다.
                        "return_retention": (
                            float(pen["total_return"]) / base_return if base_return > 0 else None
                        ),
                        "base_fill_rate": float(base["fill_rate"]),
                    }
                )
    return pd.DataFrame(records, columns=list(_ASYMMETRY_COLUMNS))


def gate_delta_table(rows: Sequence[GateRow]) -> pd.DataFrame:
    """렌즈별 **게이트 제거의 이동**(`unconditional` − `first_tap_free`).

    ⚠️ `return_delta`는 **복리 총수익률의 차이**이지 「새로 들어온 거래의 손익」이 아니다
    (WAN-114 사다리의 증분 델타와 같은 관례). 새 거래만 격리해 손익을 매기려면 셋업 단위
    증분 분해가 필요하다 — WAN-104가 오프셋에서 한 그 작업이고, 이 표의 범위 밖이다.
    """
    view = symbol_mean(rows)
    records: list[dict[str, object]] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in SEGMENT_ORDER:
            for lens in LENS_NAMES:
                old = _lookup(
                    view, gate="first_tap_free", timeframe=timeframe, segment=segment, fill=lens
                )
                new = _lookup(
                    view, gate=ADOPTED_ARM, timeframe=timeframe, segment=segment, fill=lens
                )
                if old is None or new is None:
                    continue
                old_trades = float(old["num_trades"])
                records.append(
                    {
                        "timeframe": timeframe,
                        "segment": segment,
                        "fill": lens,
                        "old_return": float(old["total_return"]),
                        "new_return": float(new["total_return"]),
                        "return_delta": float(new["total_return"]) - float(old["total_return"]),
                        "old_trades": old_trades,
                        "new_trades": float(new["num_trades"]),
                        "trade_growth": (
                            float(new["num_trades"]) / old_trades - 1.0 if old_trades > 0 else None
                        ),
                        "old_fill_rate": float(old["fill_rate"]),
                        "new_fill_rate": float(new["fill_rate"]),
                        "new_plus_symbols": int(new["plus_symbols"]),
                        "symbols": int(new["symbols"]),
                    }
                )
    return pd.DataFrame(records, columns=list(_DELTA_COLUMNS))


# --------------------------------------------------------------------------- #
# WAN-111 검산
# --------------------------------------------------------------------------- #

#: 검산에서 맞대는 지표. 좌표(심볼·TF·구간·렌즈·시드)로 조인한 뒤 이 열들이 같아야 한다.
_CHECK_METRICS: tuple[str, ...] = (
    "total_return",
    "num_trades",
    "win_rate",
    "max_drawdown",
    "fill_rate",
    "eligible_setups",
)


def check_against_wan111(rows: Sequence[GateRow], path: Path = WAN111_CSV) -> str:
    """`first_tap_free` 팔이 WAN-111 격자와 비트 단위로 같은지 확인한다.

    같으면 이 표의 `unconditional` 팔이 낸 이동은 **게이트 제거의 몫**이다. 어긋나면 창·
    엔진·경로 중 무언가가 함께 움직였다는 뜻이라, 그걸 모른 채 델타를 「게이트 효과」로
    읽는 것이 정확히 이 저장소가 반복해 겪은 사고다.
    """
    if not path.exists():
        return f"⚠️ 검산 생략: `{path}`가 없다."
    old = pd.read_csv(path)
    mine = rows_to_frame([r for r in rows if r.gate == "first_tap_free"])
    if mine.empty:
        return "⚠️ 검산 생략: `first_tap_free` 팔이 이 실행에 없다."

    keys = ["symbol", "timeframe", "segment", "fill", "seed"]
    merged = mine.merge(old, on=keys, how="inner", suffixes=("_new", "_old"))
    if merged.empty:
        return "⚠️ 검산 불가: 좌표가 겹치는 행이 없다(창 또는 축이 다르다)."

    mismatched: list[str] = []
    for metric in _CHECK_METRICS:
        diff = (merged[f"{metric}_new"] - merged[f"{metric}_old"]).abs()
        worst = float(diff.max())
        if worst > 1e-9:
            mismatched.append(f"{metric}(최대 차 {worst:.3e})")
    if mismatched:
        return (
            f"🚨 **검산 실패** — `first_tap_free` 팔 {len(merged)}행이 `{path}`와 어긋난다: "
            f"{', '.join(mismatched)}. 게이트 말고 다른 것이 함께 움직였다는 뜻이므로 "
            "이 표의 델타를 「게이트 효과」로 읽으면 안 된다."
        )
    return (
        f"✅ **검산 통과** — `first_tap_free` 팔 {len(merged)}행이 `{path}`와 "
        f"{len(_CHECK_METRICS)}개 지표 전부 비트 단위로 일치한다. 이 표의 `unconditional` "
        "팔이 낸 이동은 **게이트 제거의 몫**이다."
    )


# --------------------------------------------------------------------------- #
# 렌더 · 판정
# --------------------------------------------------------------------------- #


def _as_float(value: object) -> float | None:
    """표 셀을 float로 좁힌다. 없는 값(`None`/NaN)은 **지어내지 않고** `None`으로 남긴다.

    `itertuples()`가 `Any`를 주므로 좁히는 자리가 여기 하나뿐이다 — 렌더 함수마다 다시
    좁히면 「부호 함정 가드가 낸 `None`」이 어딘가에서 0.0으로 조용히 바뀐다.
    """
    if value is None or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return None if pd.isna(number) else number


def _pct(value: object, digits: int = 2) -> str:
    number = _as_float(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


def _num(value: object, digits: int = 1) -> str:
    number = _as_float(value)
    return "—" if number is None else f"{number:.{digits}f}"


def render_asymmetry(table: pd.DataFrame) -> str:
    header = (
        "| 게이트 | TF | 구간 | 체결률 | base 수익 | pen 수익 | base 거래 | pen 거래 | "
        "거래 잔존 | 수익 잔존 |\n| -- | -- | -- | --: | --: | --: | --: | --: | --: | --: |"
    )
    body = [
        f"| {r.gate} | {r.timeframe} | {r.segment} | {_pct(r.base_fill_rate)} | "
        f"{_pct(r.base_return)} | {_pct(r.pen_return)} | {_num(r.base_trades)} | "
        f"{_num(r.pen_trades)} | {_pct(r.trade_retention)} | {_pct(r.return_retention)} |"
        for r in table.itertuples()
    ]
    return header + "\n" + "\n".join(body)


def render_gate_delta(table: pd.DataFrame) -> str:
    header = (
        "| TF | 구간 | 렌즈 | 게이트 on | 게이트 off | 델타 | 거래 증가 | "
        "체결률 on→off | off 플러스 심볼 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | -- | --: |"
    )
    body = [
        f"| {r.timeframe} | {r.segment} | {r.fill} | {_pct(r.old_return)} | "
        f"{_pct(r.new_return)} | {_pct(r.return_delta)}p | {_pct(r.trade_growth)} | "
        f"{_pct(r.old_fill_rate)} → {_pct(r.new_fill_rate)} | "
        f"{r.new_plus_symbols}/{r.symbols} |"
        for r in table.itertuples()
    ]
    return header + "\n" + "\n".join(body)


def build_conclusion(rows: Sequence[GateRow]) -> str:
    """판정 문장 — 숫자는 전부 행에서 계산한다(문장에 박으면 재실행 때 갈라진다)."""
    asym = asymmetry_table(rows)
    delta = gate_delta_table(rows)
    parts: list[str] = []

    for timeframe in DEFAULT_TIMEFRAMES:
        old = asym[
            (asym["gate"] == "first_tap_free")
            & (asym["timeframe"] == timeframe)
            & (asym["segment"] == SEGMENT_OOS)
        ]
        new = asym[
            (asym["gate"] == ADOPTED_ARM)
            & (asym["timeframe"] == timeframe)
            & (asym["segment"] == SEGMENT_OOS)
        ]
        if old.empty or new.empty:
            continue
        o, n = old.iloc[0], new.iloc[0]
        parts.append(
            f"- **{timeframe} OOS**: 관통을 요구하면(`baseline`→`pen_5bp`) 게이트 off는 거래의 "
            f"{_pct(n['trade_retention'])}가 남는데 수익은 {_pct(n['return_retention'])}가 "
            f"남는다(게이트 on: 거래 {_pct(o['trade_retention'])} / 수익 "
            f"{_pct(o['return_retention'])}). 체결률은 {_pct(o['base_fill_rate'])} → "
            f"{_pct(n['base_fill_rate'])}."
        )

    oos_delta = delta[(delta["segment"] == SEGMENT_OOS) & (delta["fill"] == OFFICIAL_LENS)]
    for r in oos_delta.itertuples():
        parts.append(
            f"- **{r.timeframe} OOS 공식 렌즈**: 게이트 제거로 거래가 "
            f"{_pct(r.trade_growth)} 늘고 수익은 {_pct(r.old_return)} → {_pct(r.new_return)}"
            f"({_pct(r.return_delta)}p)로 움직였다."
        )
    return "\n".join(parts) if parts else "판정할 셀이 없다(격자가 비어 있음)."


def build_summary_markdown(rows: Sequence[GateRow], *, csv_path: Path) -> str:
    asym = asymmetry_table(rows)
    delta = gate_delta_table(rows)
    return (
        "# WAN-124 3단 — 게이트 제거 엔진의 체결 보수화 재검\n\n"
        "게이트 2팔(`first_tap_free`=WAN-122까지 · `unconditional`=WAN-123 채택) × 6심볼 × "
        "2TF(15m·1h) × 전 구간/IS/OOS × 3렌즈, 못 박은 창 "
        f"{DEFAULT_START}~{DEFAULT_END}, 로컬 `data/ohlcv.db` 실데이터.\n"
        "재현: `uv run python -m backtest.wan123_fill_conservatism` (요약만: `--from-csv`). "
        f"원자료: `{csv_path}`.\n\n"
        "## 검산\n\n"
        f"{check_against_wan111(rows)}\n\n"
        "⚠️ **`wan95_zone_limit_summary.md`의 「거래 +19~27%」와 이 표의 이동을 같은 수치로\n"
        "인용하지 말 것** — 그쪽은 `--years 3`(미끄러지는 창) 3심볼, 이 표는 못 박은 창\n"
        "6심볼이다(CLAUDE.md가 그 비교를 금지한다).\n\n"
        "## WAN-96 비대칭 — 거래는 얼마 줄고 수익은 얼마 사라지나\n\n"
        "`baseline` → `pen_5bp`(지정가 5bp 관통 요구)로 갈 때의 잔존율이다. **거래 잔존은\n"
        "높은데 수익 잔존이 낮으면**, 그 수익은 관통 없이 **스치듯 닿은 체결**에 실려 있다 —\n"
        "실거래에서 큐 우선순위 때문에 가장 안 될 체결이다(WAN-96).\n\n"
        f"{render_asymmetry(asym)}\n\n"
        "⚠️ 기준(`base 수익`)이 음수인 셀은 **수익 잔존을 `—`로 둔다** — 더 나빠지는데 비율이\n"
        "100%를 넘게 찍혀 「유지」로 읽히는 부호 함정 때문이다(WAN-115가 1h 증분에서 겪었다).\n\n"
        "## 게이트 제거의 이동 (렌즈별)\n\n"
        f"{render_gate_delta(delta)}\n\n"
        "⚠️ `델타`는 **복리 총수익률의 차이**이지 「새로 들어온 거래의 손익」이 아니다\n"
        "(WAN-114 사다리의 증분 델타와 같은 관례).\n\n"
        "## 결론\n\n"
        f"{build_conclusion(rows)}\n\n"
        "⚠️ **체결률이 올랐다고 큐 우선순위 걱정이 준 것이 아니다** — 그 체결률은 전부\n"
        '`baseline`("닿으면 체결") 위의 값이다. 게이트 off가 더 많은 체결에 기댄다는 것은\n'
        "**낙관 가정에 더 많이 기댄다**는 뜻이고, 위 비대칭 표가 그 대가를 잰 것이다.\n"
        "실제 해소는 틱·호가 데이터(WAN-98) 소관이다.\n\n"
        "⚠️ **「엣지 없음」(WAN-84/88/111) 판정은 이 표가 다루지 않는다** — 매칭 널은\n"
        "`backtest/wan123_matched_null.py`(WAN-124 2단) 소관이다.\n"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-124 3단: 게이트 제거 엔진 체결 보수화 재검")
    parser.add_argument("--symbols", nargs="+", default=list(ALL_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수.")
    parser.add_argument("--csv-out", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = rows_from_csv(args.csv_out)
        print(f"[wan123-fill] {args.csv_out}에서 {len(rows)}행 읽음")
    else:
        rows = run_report(
            args.symbols,
            timeframes=tuple(args.timeframes),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
        )
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(args.csv_out, index=False)
        print(f"[wan123-fill] {len(rows)}행 → {args.csv_out}")

    summary = build_summary_markdown(rows, csv_path=args.csv_out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan123-fill] summary → {args.summary_out}")
    print(check_against_wan111(rows))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
