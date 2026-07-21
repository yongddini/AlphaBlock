"""WAN-120: 엄격 인과 밴드 — 잔여 1분 룩어헤드가 WAN-119의 판정을 흔드나.

WAN-119는 밴드의 20번째 표본에 **체결 순간의 현재가**를 넣는 `intrabar_live`가 사용자가
트레이딩뷰에서 보고 지정가를 거는 값임을 보이고, 15m 플러스가 그 밴드에서 살아남는다고
판정했다((a) 되살아남). 다만 그 모드에는 **1분짜리 잔여 룩어헤드**가 남는다: 밴드는
close(t)로 계산되는데 체결 판정(`low(t) <= 지정가`)도 **같은 1분봉 안**이라, 주문 가격을
그 분의 결과를 조금 알고 고르는 셈이다. 15m 봉의 룩어헤드를 1분으로 **줄인** 것이지
0으로 만든 게 아니다.

WAN-119 §5가 그 크기를 쟀지만 **2심볼 · 1년 · 원 손익**의 지표적 대조였다(BTC 손익
+1.9% · ETH −8.5% — 작고 방향이 갈림). 이 모듈이 그 진단을 **못 박은 창의 6심볼 격자**로
올린다(WAN-119 §6 권고 3). 진입가 **정본**을 확정하는 결정(WAN-120)이라면 그 1분까지
닫고 가는 게 맞고, 재-베이스라인보다 **먼저** 하면 정본을 한 번만 바꾸면 된다.

## 사다리 — 밴드의 20번째 표본만 다른 4단

| 단 | `band_bar` | 20번째 표본 | 성질 |
| -- | -- | -- | -- |
| `L1` | — | 볼린저 off | 존 근단 지정가(증분의 기준선). WAN-114/115/119의 `L1`. |
| `L2` | `tap` | 탭 봉 **최종 종가** | **채택 기본값**. 봉 내부 체결엔 룩어헤드(WAN-115). |
| `L2i` | `intrabar_live` | **그 순간의 현재가** | WAN-119 권고. 잔여 1분 룩어헤드가 남는다. |
| `L2c` | `intrabar_causal` | 직전 **1분봉** 종가 | **이 이슈**. 그 1분까지 0으로. |

`L2c`는 그 분이 **시작될 때 이미 알던 가격**만 쓴다 = 분당 주문을 재호가하는 봇이 실제로
할 수 있는 것. `L2i`와 **오직 지연 한 칸**만 다르다.

⚠️ **`L2p`(`prev_closed`)는 이 표에 없다.** WAN-119가 그 단의 질문(룩어헤드의 몫이
얼마냐)을 이미 닫았고 — 답은 "그 −4.88%p는 룩어헤드가 아니라 **현재 봉을 버린 대가**"였다
— 이 이슈의 질문은 `L2i` 대 `L2c`다. 격자 시간을 그 대조에 쓴다.

## 판정이 답하는 질문

**잔여 1분 룩어헤드가 WAN-119의 판정을 흔드나.** 흔든다 = `L2c`에서 (1) 채택 기본값의
절대 수익률 부호가 뒤집히거나 (2) 볼린저 증분의 부호가 뒤집힌다. 흔들지 않는다면 WAN-119
§2의 판정은 그 1분에 기대고 있지 않다는 뜻이고, 정본 전환(재-베이스라인) 판단이 이 축에서
자유로워진다.

⚠️ **이 표는 「어느 밴드가 더 버나」를 고르는 표가 아니다.** `L2c`가 `L2i`보다 낫든
못하든, 재는 것은 **판정의 안정성**이다. 수익으로 밴드를 고르면 그건 최적화이고, 진입가
정본은 **정확성** 문제다(WAN-119 §6-1).

## 기본값은 바꾸지 않는다

`ConfluenceParams()`는 불변이다(`band_bar="tap"`). 이 모듈은 **측정만** 한다 — 진입가의
정본을 바꾸는 것은 WAN-70/84/88/95/111/114가 선 엔진 정의를 흔드는 **명시적
재-베이스라인**이고 **사용자 결정**이다(CLAUDE.md). 판정·권고는 `docs/decisions/wan120.md`.

## 창·격자는 WAN-111/114/115/119와 같다

창을 **2023-07-14~2026-07-15**로 못 박은 6심볼 × 15m·1h × IS/OOS × 3렌즈 격자다.
덕분에 `L1`·`L2`·`L2i` 행은 `wan119_intrabar_live_band.csv`의 같은 단과 **비트 단위로
일치**해야 하고, 그것이 이 표의 검산이다(테스트가 파라미터 동일성을 고정한다).

## 재현

```
uv run python -m backtest.wan120_strict_causal_band            # 격자 재실행(~60분)
uv run python -m backtest.wan120_strict_causal_band --from-csv # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from backtest.harness import (
    IS_FRACTION,
    LEGACY_BAND_BAR,
    LEGACY_OB_PARAMS,
    SEGMENT_IS,
    SEGMENT_OOS,
    FillPreset,
    RunRow,
    Segment,
    build_config,
    build_params,
    build_row,
    detect_order_blocks,
    fill_preset,
    iter_seeds,
    load_market_data,
    normalize_symbol,
    run_once,
    slice_market,
)
from backtest.run import parse_date_ms
from strategy.models import ConfluenceParams, DeviationFilterParams

#: 심볼 유니버스 = 6심볼(WAN-111) — WAN-114/115/119와 같은 표본이라야 그 CSV와 검산된다.
ALL_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

#: WAN-107 공동 작업 TF. 15m이 주인공(플러스 전체가 볼린저에 얹힌 축)이고 1h는 대조군이다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 렌즈 이름은 harness의 프리셋 이름 그대로 — 이름이 같아야 WAN-111/114/115/119 표와 같은 뜻이다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

OFFICIAL_LENS = "baseline"

#: WAN-111/114/115/119와 같은 못 박은 창 — 그래야 `L1`·`L2`·`L2i` 행이 그 리포트와 검산된다.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)

#: 채택 기본값의 볼린저(SMA20 ± 2σ). `band_bar`만 갈아끼워 나머지 단을 만든다.
#: ⚠️ `band_bar`를 **명시**한다 — WAN-132가 필드 기본값을 `intrabar_live`로 옮겼으므로
#: 생략하면 이 사다리의 `L2`(= 당시 채택 기본값) 단이 조용히 다른 밴드로 돈다.
_BOLLINGER = DeviationFilterParams(
    anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0, band_bar=LEGACY_BAND_BAR
)


# --------------------------------------------------------------------------- #
# 사다리 — 밴드의 20번째 표본만 다른 4단
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rung:
    """사다리 한 단 — 채택 기본값(`ConfluenceParams()`) 대비 **덮어쓸 필드**로 정의한다.

    기준을 채택 기본값에 두는 이유는 WAN-111/114/115/119와 같다: 부품 값을 상수로 박아 두면
    기본값이 재-베이스라인으로 움직일 때(WAN-112 같은) 이 리포트만 혼자 옛 엔진을 돈다.
    """

    name: str
    label: str
    updates: Mapping[str, object] = field(default_factory=dict)


def _rung(name: str, label: str, band_bar: str | None) -> Rung:
    """`band_bar`만 갈아끼운 단(`None`이면 볼린저 자체를 끈다).

    네 단이 **오직 이 필드 하나로** 갈리는 것이 이 표의 전제다 — 다른 필드가 섞이면
    `L2c`와 `L2i`의 차이가 "지연 한 칸" 때문인지 알 수 없다. WAN-119의 `_rung`과 같은
    조립이며, 테스트가 두 모듈의 `L1`·`L2`·`L2i` 파라미터 동일성을 고정한다.
    """
    filt = None if band_bar is None else _BOLLINGER.model_copy(update={"band_bar": band_bar})
    return Rung(
        name=name,
        label=label,
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": "first_tap_free",
            "deviation_filter": filt,
        },
    )


#: `L1`·`L2`·`L2i`는 WAN-119 사다리에서 그대로 가져왔다(같은 창·같은 설정이라 그 CSV와
#: 검산된다). `L2c`가 이 이슈가 새로 얹는 단이고, `L2i`와 **오직 지연 한 칸**만 다르다.
RUNGS: tuple[Rung, ...] = (
    _rung("L1", "볼린저 off (존 근단 지정가)", None),
    _rung("L2", "볼린저 on · 탭 봉 종가 (= 채택 기본값 · 룩어헤드)", "tap"),
    _rung("L2i", "볼린저 on · 봉내 현재가 (WAN-119 권고 · 잔여 1분 룩어헤드)", "intrabar_live"),
    _rung("L2c", "볼린저 on · 직전 1분봉 종가 (엄격 인과 · 잔여분 0)", "intrabar_causal"),
)

RUNGS_BY_NAME: dict[str, Rung] = {r.name: r for r in RUNGS}

LADDER: tuple[str, ...] = tuple(r.name for r in RUNGS)

#: 볼린저 없는 바닥(증분의 기준선) · 채택 기본값 · WAN-119 권고 · 이 이슈의 엄격 인과.
BASE_RUNG = "L1"
TAP_RUNG = "L2"
LIVE_RUNG = "L2i"
CAUSAL_RUNG = "L2c"

#: 이 표가 재는 증분 셋. `L1→L2`가 WAN-114의 +16.20%p, `L1→L2i`가 WAN-119의 +16.70%p.
STEPS: tuple[tuple[str, str], ...] = (
    (BASE_RUNG, TAP_RUNG),
    (BASE_RUNG, LIVE_RUNG),
    (BASE_RUNG, CAUSAL_RUNG),
)

#: 단 → 밴드 성질(표에 그대로 찍는다 — 문서와 코드가 갈라지지 않게).
BAND_LABELS: dict[str, str] = {
    TAP_RUNG: "tap (룩어헤드)",
    LIVE_RUNG: "intrabar_live (잔여 1분)",
    CAUSAL_RUNG: "intrabar_causal (잔여 0)",
}


def rung_params(rung: Rung, *, fill: FillPreset, seed: int = 0) -> ConfluenceParams:
    """사다리 한 단의 `ConfluenceParams`.

    `build_params(base=...)`로 조립하므로 진입 방식(`entry_mode`)·RSI 판정 기준
    (`rsi_mode`)·체결 가정이 CLI·다른 리포트와 **같은 규칙**으로 묶인다(WAN-41/95 한 세트).
    오프셋·익절 R을 넘기지 않으므로 둘 다 채택 기본값(2bp · 1.5R)을 따라간다.
    """
    base = ConfluenceParams().model_copy(update=dict(rung.updates))
    return build_params(entry_mode="zone_limit", fill=fill, seed=seed, base=base)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


class CausalBandRow(RunRow):
    """격자 한 셀 — `RunRow`(harness 공용 좌표+지표)에 사다리 단만 얹는다."""

    level: str


def segments() -> tuple[Segment, ...]:
    """IS(앞 2/3) · OOS(뒤 1/3). 각 구간은 초기자본에서 새로 시작하는 독립 백테스트다."""
    return (
        Segment(name=SEGMENT_IS, window=0, start_fraction=0.0, end_fraction=IS_FRACTION),
        Segment(name=SEGMENT_OOS, window=0, start_fraction=IS_FRACTION, end_fraction=1.0),
    )


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    lenses: Sequence[str] = LENS_NAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    log: bool = True,
) -> list[CausalBandRow]:
    """사다리 × 심볼 × TF × 렌즈 × 구간 격자를 돈다.

    구간마다 오더블록을 한 번만 탐지해 네 단이 공유한다 — 탐지는 컨플루언스 파라미터와
    무관하므로(존은 규칙 이전에 존재한다) 결과가 바뀌지 않는다. 이 공유가 비교의 전제다:
    네 단이 **같은 존 집합**을 보고, 다른 건 오직 밴드의 20번째 표본이다.
    """
    fills = tuple(fill_preset(name) for name in lenses)
    rows: list[CausalBandRow] = []
    for symbol in (normalize_symbol(s) for s in symbols):
        for timeframe in timeframes:
            market = load_market_data(
                symbol, timeframe, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end)
            )
            if market.empty or market.df_1m.empty:
                _log(log, f"[wan120] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
                continue
            cfg = build_config(timeframe)
            for segment in segments():
                window = slice_market(market, segment)
                if window.empty:
                    continue
                ob_result = detect_order_blocks(window, LEGACY_OB_PARAMS)
                for rung in RUNGS:
                    for fill in fills:
                        for seed in iter_seeds(fill):
                            params = rung_params(rung, fill=fill, seed=seed)
                            outcome = run_once(
                                window, params=params, cfg=cfg, order_block_result=ob_result
                            )
                            row = build_row(
                                outcome, window, segment=segment, params=params, fill_name=fill.name
                            )
                            rows.append(CausalBandRow(level=rung.name, **row.model_dump()))
                _log(
                    log,
                    f"[wan120] {symbol} {timeframe} {segment.name}: "
                    f"{len(window.htf_df)}봉 · 존 {len(ob_result.order_blocks)}개 → "
                    f"{len(RUNGS)}단 완료",
                )
    return rows


def _log(enabled: bool, message: str) -> None:
    if enabled:
        import sys

        print(message, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[CausalBandRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[CausalBandRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고치려고 격자를 다시 돌리지 않는다)."""
    frame = pd.read_csv(path)
    return [CausalBandRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[CausalBandRow]) -> pd.DataFrame:
    """심볼별 성과 — 시드를 **심볼 안에서 먼저** 접는다(WAN-111/114/115/119와 같은 순서).

    `pen_5bp_drop_50`은 시드 5개를 도는데, 심볼 평균을 내기 전에 심볼 안에서 접어야
    증분 델타를 심볼별로 짝지을 수 있다(순서를 바꾸면 뺄 대상이 사라진다).
    """
    frame = rows_to_frame(rows)
    return (
        frame.groupby(["level", "timeframe", "segment", "fill", "symbol"], as_index=False)
        .agg(
            total_return=("total_return", "mean"),
            win_rate=("win_rate", "mean"),
            max_drawdown=("max_drawdown", "mean"),
            num_trades=("num_trades", "mean"),
            fill_rate=("fill_rate", "mean"),
            mean_r=("mean_r", "mean"),
            sharpe=("sharpe", "mean"),
            seeds=("seed", "count"),
        )
        .reset_index(drop=True)
    )


def rung_summary(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """단별 심볼평균 — 본표(L1/L2/L2i/L2c × TF × 렌즈 × IS/OOS).

    `positive`(플러스 심볼 수)를 평균 옆에 두는 이유는 WAN-111과 같다: **평균만 보면
    심볼 하나가 끌어올린 것이 안 보인다**.
    """
    grouped = symbol_frame.groupby(["timeframe", "segment", "fill", "level"], as_index=False).agg(
        total_return=("total_return", "mean"),
        return_min=("total_return", "min"),
        return_max=("total_return", "max"),
        positive=("total_return", lambda s: float((s > 0).sum())),
        symbols=("total_return", "count"),
        win_rate=("win_rate", "mean"),
        max_drawdown=("max_drawdown", "mean"),
        num_trades=("num_trades", "mean"),
        fill_rate=("fill_rate", "mean"),
        mean_r=("mean_r", "mean"),
    )
    return _sorted(grouped, ["timeframe", "segment", "fill", "level"])


def incremental(symbol_frame: pd.DataFrame) -> pd.DataFrame:
    """볼린저의 증분 기여 — 세 밴드 정의를 나란히(`L1→L2` / `L1→L2i` / `L1→L2c`).

    델타는 **심볼별로 짝지어** 계산한 뒤 평균한다(심볼평균끼리 빼는 것과 값은 같지만,
    이렇게 해야 `symbols_up`을 셀 수 있다). 심볼 6개는 서로 상관된 표본이라(크립토 베타)
    `symbols_up`은 **유의성이 아니라 방향의 일관성**만 말한다(WAN-104/114/115/119와 같은 규칙).
    """
    records: list[dict[str, object]] = []
    for (timeframe, segment, lens), view in symbol_frame.groupby(
        ["timeframe", "segment", "fill"], sort=False
    ):
        pivots = {
            column: view.pivot_table(index="symbol", columns="level", values=column)
            for column in ("total_return", "num_trades", "max_drawdown", "win_rate", "fill_rate")
        }
        returns = pivots["total_return"]
        for prev, cur in STEPS:
            if prev not in returns.columns or cur not in returns.columns:
                continue
            delta = (returns[cur] - returns[prev]).dropna()
            if delta.empty:
                continue
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "lens": lens,
                    "step": f"{prev}→{cur}",
                    "band_bar": BAND_LABELS.get(cur, "—"),
                    "delta_return": float(delta.mean()),
                    "symbols_up": float((delta > 0).sum()),
                    "symbols": float(len(delta)),
                    "delta_trades_pct": _relative(pivots["num_trades"], prev, cur),
                    "delta_win_rate": _mean_delta(pivots["win_rate"], prev, cur),
                    "delta_mdd": _mean_delta(pivots["max_drawdown"], prev, cur),
                }
            )
    frame = pd.DataFrame(records)
    return _sorted(frame, ["timeframe", "segment", "lens"])


def _mean_delta(pivot: pd.DataFrame, prev: str, cur: str) -> float:
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    return float((pivot[cur] - pivot[prev]).dropna().mean())


def _relative(pivot: pd.DataFrame, prev: str, cur: str) -> float:
    """거래 수의 상대 변화(%) — 심볼평균 기준.

    수익 델타 옆에 거래 수가 있어야 "선별이 바뀐 건가, 가격만 바뀐 건가"를 읽을 수 있다
    (WAN-96의 비대칭을 부품 축에서 읽는 방법 — WAN-114/115/119와 같은 헬퍼).
    """
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    before = float(pivot[prev].dropna().mean())
    after = float(pivot[cur].dropna().mean())
    if not before:
        return float("nan")
    return (after - before) / before


_ORDERINGS: dict[str, tuple[str, ...]] = {
    "segment": SEGMENT_ORDER,
    "lens": LENS_NAMES,
    "fill": LENS_NAMES,
    "level": LADDER,
}


def _sorted(frame: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
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
    return symbol.split("/")[0]


# --------------------------------------------------------------------------- #
# 판정 — 잔여 1분이 WAN-119의 판정을 흔드나
# --------------------------------------------------------------------------- #

SHAKEN_RETURN = "⚠️ 흔들림 (채택 기본값 부호 반전)"
SHAKEN_DELTA = "⚠️ 흔들림 (볼린저 증분 부호 반전)"
STABLE = "유지"


def _shake_label(
    *, live_return: float, causal_return: float, live_delta: float, causal_delta: float
) -> str:
    """`L2i`(잔여 1분) → `L2c`(잔여 0)에서 **판정이 흔들리나**.

    이슈가 물은 것은 "잔여 룩어헤드가 판정을 흔드는지"이지 "어느 밴드가 더 버는지"가
    **아니다**. 그래서 크기가 아니라 **부호**로 판정한다 — WAN-119의 판정은 두 문장이었고
    (채택 기본값이 플러스로 남는다 · 볼린저 증분이 플러스다), 그 둘 중 하나라도 `L2c`에서
    뒤집히면 그 판정은 잔여 1분에 기대고 있었다는 뜻이다.

    ⚠️ **비율(회복률)을 쓰지 않는 이유**는 WAN-115 §1h가 걸린 그 부호 함정이다 — 기준이
    음수인 셀(1h)에서 비율은 뜻을 잃고 "유지"로 오독된다. 부호 대조는 그 함정이 없다.
    """
    if (live_return > 0) != (causal_return > 0):
        return SHAKEN_RETURN
    if (live_delta > 0) != (causal_delta > 0):
        return SHAKEN_DELTA
    return STABLE


def verdict(steps: pd.DataFrame, summary: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """「잔여 1분 룩어헤드가 WAN-119의 판정을 흔드나」를 숫자에서 직접 읽어 문장으로.

    공식 렌즈(`baseline`)만 판정 대상이다(토대 2) — 나머지는 민감도로 병기한다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = steps[
            (steps["timeframe"] == timeframe)
            & (steps["segment"] == segment)
            & (steps["lens"] == OFFICIAL_LENS)
        ].set_index("step")
        levels = summary[
            (summary["timeframe"] == timeframe)
            & (summary["segment"] == segment)
            & (summary["fill"] == OFFICIAL_LENS)
        ].set_index("level")
        wanted = {f"{BASE_RUNG}→{r}" for r in (LIVE_RUNG, CAUSAL_RUNG)}
        if not wanted <= set(view.index) or not {LIVE_RUNG, CAUSAL_RUNG} <= set(levels.index):
            continue
        live_delta = float(view.loc[f"{BASE_RUNG}→{LIVE_RUNG}", "delta_return"])
        causal_delta = float(view.loc[f"{BASE_RUNG}→{CAUSAL_RUNG}", "delta_return"])
        live_return = float(levels.loc[LIVE_RUNG, "total_return"])
        causal_return = float(levels.loc[CAUSAL_RUNG, "total_return"])
        causal_pos = int(levels.loc[CAUSAL_RUNG, "positive"])
        n = int(levels.loc[CAUSAL_RUNG, "symbols"])
        label = _shake_label(
            live_return=live_return,
            causal_return=causal_return,
            live_delta=live_delta,
            causal_delta=causal_delta,
        )
        lines.append(
            f"- **{timeframe} {segment}**: 채택 기본값 절대 수익률이 `intrabar_live` "
            f"**{live_return * 100:+.2f}%** → `intrabar_causal` **{causal_return * 100:+.2f}%**"
            f"({causal_pos}/{n}심볼, **{(causal_return - live_return) * 100:+.2f}%p**) · "
            f"볼린저 증분 **{live_delta * 100:+.2f}%p** → **{causal_delta * 100:+.2f}%p** "
            f"→ **{label}**"
        )
    return lines


def lens_stability(summary: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """판정이 렌즈에 의존하는지 — 세 렌즈 전부에서 `L2i`→`L2c` 부호가 유지되나.

    WAN-104의 오프셋 판정이 렌즈에 통째로 의존한 전례가 있어(그래서 CLAUDE.md가 그 부류를
    따로 경고한다), 부호 안정성도 세 렌즈에서 나란히 본다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        cells: list[str] = []
        for lens in LENS_NAMES:
            view = summary[
                (summary["timeframe"] == timeframe)
                & (summary["segment"] == segment)
                & (summary["fill"] == lens)
            ].set_index("level")
            if not {LIVE_RUNG, CAUSAL_RUNG} <= set(view.index):
                continue
            live = float(view.loc[LIVE_RUNG, "total_return"])
            causal = float(view.loc[CAUSAL_RUNG, "total_return"])
            flip = "" if (live > 0) == (causal > 0) else " ⚠️부호반전"
            cells.append(f"`{lens}` {live * 100:+.2f}% → {causal * 100:+.2f}%{flip}")
        if cells:
            lines.append(f"- **{timeframe} {segment}**: " + " · ".join(cells))
    return lines


#: 「가격 쪽 손상」으로 부를 거래 수 변화의 상한. WAN-115/119가 관찰한 +3.0%/+1.9%가 이 안에
#: 들어오는 크기이고, 그 정도면 "같은 셋업에 같은 빈도로 들어갔다"고 읽을 수 있다.
PRICE_ONLY_TRADE_SHIFT = 0.10


def selection_vs_price(symbol_frame: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """손상이 「선별」인가 「가격」인가 — 거래 수가 그대로인데 수익만 움직이면 가격이다.

    WAN-115/119가 밴드 정의 축에서 확인한 성질(거래는 거의 안 움직이는데 수익만 움직인다)이
    지연 한 칸에서도 성립하는지 본다. 성립하면 잔여 1분의 몫도 순수하게 진입 **가격**이다.

    ⚠️ 여기서 재는 건 **`L2i` 대비** 변화다(`incremental`의 `L1→X`를 빼서 만들면 안 된다 —
    분모가 `L1`이라 같은 이름의 다른 수가 된다). 이 이슈의 대조는 `L2i`↔`L2c`이므로 기준을
    `L2i`에 둔다(WAN-119가 `L2`에 둔 것과 다른 자리 — 질문이 다르기 때문이다).
    """
    lines: list[str] = []
    view = symbol_frame[
        (symbol_frame["segment"] == segment) & (symbol_frame["fill"] == OFFICIAL_LENS)
    ]
    for timeframe in DEFAULT_TIMEFRAMES:
        cell = view[view["timeframe"] == timeframe]
        if cell.empty:
            continue
        pivots = {
            column: cell.pivot_table(index="symbol", columns="level", values=column)
            for column in ("total_return", "num_trades", "fill_rate")
        }
        if not {LIVE_RUNG, CAUSAL_RUNG} <= set(pivots["total_return"].columns):
            continue
        d_ret = _mean_delta(pivots["total_return"], LIVE_RUNG, CAUSAL_RUNG)
        trades = _relative(pivots["num_trades"], LIVE_RUNG, CAUSAL_RUNG)
        d_fill = _mean_delta(pivots["fill_rate"], LIVE_RUNG, CAUSAL_RUNG)
        kind = "가격" if abs(trades) < PRICE_ONLY_TRADE_SHIFT else "선별+가격"
        lines.append(
            f"- **{timeframe} {segment}** `{CAUSAL_RUNG}` vs `{LIVE_RUNG}`: 수익 "
            f"**{d_ret * 100:+.2f}%p**인데 거래 수는 **{trades * 100:+.1f}%** "
            f"(체결률 {d_fill * 100:+.2f}%p) — **{kind}** 쪽 손상"
        )
    return lines


def per_symbol_spread(symbol_frame: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """심볼별 잔여분 — WAN-119 §5가 본 「방향이 갈린다」가 6심볼에서도 성립하나.

    §5는 2심볼(BTC +1.9% · ETH −8.5%)로 "작고 방향이 체계적이지 않다"고 봤다. 체계적
    편향이면 6심볼이 **한 방향으로 몰려야** 한다 — 그래서 평균이 아니라 **몇 개가 어느
    쪽인지**를 적는다(WAN-111이 편중을 평균 옆에 놓은 것과 같은 이유).
    """
    lines: list[str] = []
    view = symbol_frame[
        (symbol_frame["segment"] == segment) & (symbol_frame["fill"] == OFFICIAL_LENS)
    ]
    for timeframe in DEFAULT_TIMEFRAMES:
        cell = view[view["timeframe"] == timeframe]
        if cell.empty:
            continue
        pivot = cell.pivot_table(index="symbol", columns="level", values="total_return")
        if not {LIVE_RUNG, CAUSAL_RUNG} <= set(pivot.columns):
            continue
        delta = (pivot[CAUSAL_RUNG] - pivot[LIVE_RUNG]).dropna()
        if delta.empty:
            continue
        up = int((delta > 0).sum())
        detail = " · ".join(f"{_short(s)} {v * 100:+.2f}%p" for s, v in delta.items())
        lines.append(
            f"- **{timeframe} {segment}**: 인과 쪽이 나은 심볼 **{up}/{len(delta)}** "
            f"(평균 {delta.mean() * 100:+.2f}%p) — {detail}"
        )
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103/108/110/111/114/115/119와 같은 헬퍼)."""
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
    "delta_return",
    "delta_trades_pct",
    "delta_mdd",
    "delta_win_rate",
)


def _pct(frame: pd.DataFrame) -> pd.DataFrame:
    """비율 열을 % 소수 2자리로. 계측하지 않은 칸은 `—`(0으로 적으면 오독된다)."""
    out = frame.copy()
    for col in _PERCENT_COLUMNS:
        if col in out.columns:
            out[col] = (out[col] * 100).round(2)
    for col in ("num_trades", "mean_r", "sharpe", "positive", "symbols", "symbols_up"):
        if col in out.columns:
            out[col] = out[col].round(2)
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].map(_short)
    return out.astype(object).where(out.notna(), "—")


_SUMMARY_VIEW = (
    "timeframe",
    "segment",
    "fill",
    "level",
    "total_return",
    "positive",
    "symbols",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
    "mean_r",
)

_STEP_VIEW = (
    "timeframe",
    "segment",
    "lens",
    "step",
    "band_bar",
    "delta_return",
    "symbols_up",
    "symbols",
    "delta_trades_pct",
    "delta_win_rate",
    "delta_mdd",
)

_SYMBOL_VIEW = (
    "timeframe",
    "segment",
    "level",
    "symbol",
    "total_return",
    "win_rate",
    "max_drawdown",
    "num_trades",
    "fill_rate",
)


def ladder_table() -> str:
    """사다리 정의를 표로 — 어떤 필드가 언제 켜지는지 문서와 코드가 갈라지지 않게."""
    records = []
    for rung in RUNGS:
        params = rung_params(rung, fill=fill_preset(OFFICIAL_LENS))
        filt = params.deviation_filter
        records.append(
            {
                "level": rung.name,
                "label": rung.label,
                "deviation_filter": "볼린저(SMA20±2σ)" if filt else "off",
                "band_bar": filt.band_bar if filt else "—",
                "offset_bps": params.zone_limit_offset_bps,
            }
        )
    return _md_table(pd.DataFrame(records))


def write_summary(rows: Sequence[CausalBandRow], path: Path) -> None:
    symbol_frame = per_symbol(rows)
    summary = rung_summary(symbol_frame)
    steps = incremental(symbol_frame)

    official = _sorted(
        symbol_frame[symbol_frame["fill"] == OFFICIAL_LENS],
        ["timeframe", "segment", "level", "symbol"],
    )

    lines = [
        "# WAN-120: 엄격 인과 밴드 — 잔여 1분 룩어헤드가 WAN-119의 판정을 흔드나",
        "",
        "재현: `uv run python -m backtest.wan120_strict_causal_band` (요약만 재생성: `--from-csv`)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 격자다"
        "(WAN-111/114/115/119와 같은 창). "
        "토대(존 지정가 + 오프셋 2bp · 공식 렌즈 `baseline` · 비용 현재값 · 롱 온리)를 "
        "고정 입력으로 받고 **밴드의 20번째 표본만** 바꾼다.",
        "",
        '⚠️ **기본값은 바꾸지 않았다**(`band_bar="tap"` · `ALPHABLOCK_LIVE_TRADING=false`). '
        "측정 이슈이며 진입가 정본 전환은 **사용자 결정**이다 — `docs/decisions/wan120.md`.",
        "",
        "## 사다리",
        "",
        ladder_table(),
        "",
        "`L1`·`L2`·`L2i`는 WAN-119 사다리와 같은 단이므로 그 CSV와 **비트 단위로 일치**해야 "
        "한다(이 표의 검산). `L2c`가 이 이슈가 얹은 단이고 `L2i`와 **지연 한 칸**만 다르다.",
        "",
        "## 판정 — 공식 렌즈(`baseline`) OOS",
        "",
        *verdict(steps, summary),
        "",
        "판정 기준은 **부호**다(크기가 아니라) — 이슈가 물은 것은 「잔여 1분이 판정을 "
        "흔드나」이지 「어느 밴드가 더 버나」가 아니다. 수익으로 밴드를 고르면 최적화이고, "
        "진입가 정본은 **정확성** 문제다.",
        "",
        "### 렌즈 안정성 (세 렌즈 전부에서 부호가 유지되나)",
        "",
        *lens_stability(summary),
        "",
        "### 심볼별 잔여분 (방향이 갈리나, 한쪽으로 몰리나)",
        "",
        *per_symbol_spread(symbol_frame),
        "",
        "### 손상은 선별인가 가격인가",
        "",
        *selection_vs_price(symbol_frame),
        "",
        "## 본표 — 단별 심볼평균",
        "",
        _md_table(_pct(summary[list(_SUMMARY_VIEW)])),
        "",
        "## 증분 — 볼린저의 기여를 세 밴드 정의로",
        "",
        _md_table(_pct(steps[list(_STEP_VIEW)])),
        "",
        "## 심볼별 (공식 렌즈 `baseline`)",
        "",
        _md_table(_pct(official[list(_SYMBOL_VIEW)])),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

DEFAULT_CSV = Path("backtest/reports/wan120_strict_causal_band.csv")
DEFAULT_SUMMARY = Path("backtest/reports/wan120_strict_causal_band_summary.md")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-120 엄격 인과 밴드 격자")
    parser.add_argument("--symbols", default=",".join(ALL_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lenses", default=",".join(LENS_NAMES))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 저장된 CSV로 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = rows_from_csv(args.csv)
    else:
        rows = run_report(
            [s for s in args.symbols.split(",") if s],
            timeframes=[t for t in args.timeframes.split(",") if t],
            lenses=[lens for lens in args.lenses.split(",") if lens],
            start=args.start,
            end=args.end,
        )
        if not rows:
            print("결과 행이 없습니다 — 데이터를 확인하세요.")
            return 1
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(args.csv, index=False)

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    write_summary(rows, args.summary)
    print(f"행 {len(rows)}개 · CSV {args.csv} · 요약 {args.summary}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
