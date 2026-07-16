"""WAN-115: 볼린저 진입가 룩어헤드 재검 — 15m 플러스가 미래 정보의 산물인가.

WAN-95가 각주로 적고 넘어간 성질이 있다: 볼린저 밴드는 **탭 봉의 SMA20**(= 그 봉 종가를
포함)으로 계산되는데, 지정가 체결은 그 봉 **내부**에서 일어난다. 즉 그 봉이 닫혀야 나오는
가격에 그 봉 도중 주문이 걸린 것으로 취급한다 — 엄밀히 룩어헤드다. 당시 판단은 "실무
영향은 SMA20 한 틱 차이 수준이라 작다"였다.

**WAN-114가 그 전제를 뒤집었다.** 진입 규칙 3부품(RSI 게이트·재탭·볼린저) 중 값을 더하는
건 볼린저 하나뿐이고, **15m 플러스(+3.91%) 전체가 이 부품에 얹혀 있다**(`L1→L2` 증분
+16.20%p). 기여가 클수록 거기 섞인 룩어헤드의 절대 크기도 커지므로, 이제 각주가 아니라
**작업 층 1층(엔진 정확성)의 선행 재검 대상**이다 — 아래층 측정(WAN-90/113 익절 최적화)이
신뢰할 진입가 위에서 돌려면 이것이 먼저 닫혀야 한다.

## 무엇을 재나 — 밴드 봉 기준만 바꾼 3단

WAN-114 사다리의 `L1`·`L2`를 그대로 가져오고 **`L2p`(교정) 한 단만 새로 얹는다**. 세 단의
차이는 오직 **밴드를 어느 봉에서 읽나**뿐이다:

| 단 | 볼린저 | `band_bar` | 뜻 |
| -- | -- | -- | -- |
| `L1` | off | — | 볼린저 없음(존 근단 지정가). WAN-114의 `L1`과 같은 설정. |
| `L2` | on | `tap` | **채택 기본값**(= WAN-114의 `L2`). 탭 봉 자신의 SMA20 = 룩어헤드. |
| `L2p` | on | `prev_closed` | **교정**. 직전 확정봉 SMA20 — 탭 봉이 열리기 전에 확정된 값. |

핵심 비교는 **`L1→L2`(+16.20%p, 룩어헤드 포함) vs `L1→L2p`(교정 후)** 이고, `L2→L2p`가
곧 **룩어헤드가 만든 몫**이다. 판정은 교정 후 15m 증분이 (a) 대체로 유지 / (b) 상당 부분
소멸 / (c) 전부 소멸 중 어디냐다.

## 왜 `L2p`가 룩어헤드 없는 밴드인가

직전 확정봉의 SMA20/σ는 탭 봉이 **열리기 전에** 이미 확정돼 있다. 그래서 그 값으로 계산한
지정가는 탭 봉 시작 시점에 실제로 주문판에 걸어 둘 수 있다 — 사용자의 실매매가 성립한다.
반면 `tap`은 탭 봉 종가를 알아야 나오는 가격이라, 그 봉 내부 체결을 시뮬레이션하는 B안
에서는 미래 정보를 쓴 것이다. 대가로 `L2p`는 워밍업이 한 봉 늘어난다(구간 첫 봉은 직전
봉이 없어 판정 불가) — 구간당 셋업 1개 미만에 걸리는 크기다.

⚠️ **A안(종가 진입)은 이 룩어헤드가 없다** — 탭 봉 종가에 진입하므로 그 시점엔 탭 봉이
이미 닫혀 있다. 이 재검이 채택 경로(B안 지정가)만의 문제인 이유다. 다만 `band_bar`는
`deviation_band_at`(A안·B안 공유)에 있으므로 A안에서도 옵트인으로 동작한다.

## 기본값은 바꾸지 않는다

`ConfluenceParams()`는 불변이다(`band_bar="tap"`). 이 모듈은 **측정만** 한다 — 교정을
기본값으로 올리는 것은 그 위에 선 모든 리포트(WAN-70/84/88/95/111/114)의 엔진 정의를
흔드는 **명시적 재-베이스라인**이고, CLAUDE.md가 "지나가는 김에 바꾸지 않는다"고 못 박은
그 층이다. 판정과 권고는 아래 §5로 낸다.

## 창·격자는 WAN-114와 같다

창을 **2023-07-14~2026-07-15**로 못 박은 6심볼 × 15m·1h × IS/OOS 격자다(`--years N`은 창이
미끄러져 심볼마다 다른 기간을 본다 — CLAUDE.md). 덕분에 이 표의 `L1`·`L2` 행은
`wan114_entry_ablation.csv`의 같은 단과 **비트 단위로 일치**해야 하고, 그것이 이 재검의
검산이다(테스트가 파라미터 동일성을 고정한다). 공식 렌즈는 `baseline`(토대 2)이고
`pen_5bp`·`pen_5bp_drop_50`을 민감도로 병기한다.

## 재현

```
uv run python -m backtest.wan115_bollinger_lookahead_recheck          # 격자 재실행(~40분)
uv run python -m backtest.wan115_bollinger_lookahead_recheck --from-csv   # 요약만 재생성
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

#: 심볼 유니버스 = 6심볼(WAN-111). 3심볼 표본이 채택 수치를 이고 있었다는 것이 그 이슈의
#: 결론이라, 룩어헤드의 몫도 6심볼에서 재야 "밴드의 힘"과 "심볼 하나의 운"이 갈린다.
ALL_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

#: WAN-107 공동 작업 TF. 15m이 이 재검의 주인공(플러스 전체가 볼린저에 얹힌 축)이고
#: 1h는 대조군이다(그쪽은 볼린저도 마이너스였다).
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 공식 렌즈 + 민감도 + 스트레스(토대 2). 이름은 harness에서 가져오므로 CLI(`--fill`)·
#: WAN-114 표와 **같은 뜻**이 보장된다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

OFFICIAL_LENS = "baseline"

#: WAN-114와 같은 못 박은 창 — 그래야 `L1`·`L2` 행이 그 리포트와 검산된다.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)

#: 채택 기본값의 볼린저(SMA20 ± 2σ). `band_bar`만 갈아끼워 교정 단을 만든다.
_BOLLINGER = DeviationFilterParams(anchor="sma", sma_length=20, width_kind="stdev", width_value=2.0)


# --------------------------------------------------------------------------- #
# 사다리 — 밴드 봉 기준만 다른 3단
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rung:
    """사다리 한 단 — 채택 기본값(`ConfluenceParams()`) 대비 **덮어쓸 필드**로 정의한다.

    기준을 채택 기본값에 두는 이유는 WAN-111/114와 같다: 부품 값을 상수로 박아 두면
    기본값이 재-베이스라인으로 움직일 때(WAN-112 같은) 이 리포트만 혼자 옛 엔진을 돈다.
    """

    name: str
    label: str
    updates: Mapping[str, object] = field(default_factory=dict)


#: `L1`·`L2`는 WAN-114 사다리에서 그대로 가져왔다(같은 창·같은 설정이라 그 CSV와 검산된다).
#: `L2p`가 이 이슈가 새로 얹는 단이고, `L2`와 **오직 `band_bar`만** 다르다.
RUNGS: tuple[Rung, ...] = (
    Rung(
        name="L1",
        label="볼린저 off (존 근단 지정가)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": "first_tap_free",
            "deviation_filter": None,
        },
    ),
    Rung(
        name="L2",
        label="볼린저 on · 탭 봉 밴드 (= 채택 기본값 · 룩어헤드)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": "first_tap_free",
            "deviation_filter": _BOLLINGER,
        },
    ),
    Rung(
        name="L2p",
        label="볼린저 on · 직전 확정봉 밴드 (교정)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": "first_tap_free",
            "deviation_filter": _BOLLINGER.model_copy(update={"band_bar": "prev_closed"}),
        },
    ),
)

RUNGS_BY_NAME: dict[str, Rung] = {r.name: r for r in RUNGS}

LADDER: tuple[str, ...] = tuple(r.name for r in RUNGS)

#: 볼린저 없는 바닥(증분의 기준선) · 룩어헤드 포함 현행 · 교정.
BASE_RUNG = "L1"
TAP_RUNG = "L2"
PREV_RUNG = "L2p"

#: 이 재검이 재는 증분 두 개. `L1→L2`가 WAN-114의 +16.20%p이고, `L1→L2p`가 교정 후 잔존분.
STEPS: tuple[tuple[str, str], ...] = ((BASE_RUNG, TAP_RUNG), (BASE_RUNG, PREV_RUNG))


def rung_params(rung: Rung, *, fill: FillPreset, seed: int = 0) -> ConfluenceParams:
    """사다리 한 단의 `ConfluenceParams`.

    `build_params(base=...)`로 조립하므로 진입 방식(`entry_mode`)·RSI 판정 기준
    (`rsi_mode`)·체결 가정이 CLI·다른 리포트와 **같은 규칙**으로 묶인다(WAN-41/95 한 세트).
    오프셋·익절 R을 넘기지 않으므로 둘 다 채택 기본값(2bp · 1.5R)을 따라간다 — 이슈가
    요구한 "baseline + offset 2bp" 토대가 그대로 들어온다.
    """
    base = ConfluenceParams().model_copy(update=dict(rung.updates))
    return build_params(entry_mode="zone_limit", fill=fill, seed=seed, base=base)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


class RecheckRow(RunRow):
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
) -> list[RecheckRow]:
    """사다리 × 심볼 × TF × 렌즈 × 구간 격자를 돈다.

    구간마다 오더블록을 한 번만 탐지해 세 단이 공유한다 — 탐지는 컨플루언스 파라미터와
    무관하므로(존은 규칙 이전에 존재한다) 결과가 바뀌지 않는다. 이 공유가 재검의 전제다:
    세 단이 **같은 존 집합**을 보고, 다른 건 오직 밴드를 어느 봉에서 읽느냐다.
    """
    fills = tuple(fill_preset(name) for name in lenses)
    rows: list[RecheckRow] = []
    for symbol in (normalize_symbol(s) for s in symbols):
        for timeframe in timeframes:
            market = load_market_data(
                symbol, timeframe, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end)
            )
            if market.empty or market.df_1m.empty:
                _log(log, f"[wan115] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
                continue
            cfg = build_config(timeframe)
            for segment in segments():
                window = slice_market(market, segment)
                if window.empty:
                    continue
                ob_result = detect_order_blocks(window)
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
                            rows.append(RecheckRow(level=rung.name, **row.model_dump()))
                _log(
                    log,
                    f"[wan115] {symbol} {timeframe} {segment.name}: "
                    f"{len(window.htf_df)}봉 · 존 {len(ob_result.order_blocks)}개 → "
                    f"{len(RUNGS)}단 완료",
                )
    return rows


def _log(enabled: bool, message: str) -> None:
    if enabled:
        import sys

        print(message, file=sys.stderr)


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[RecheckRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[RecheckRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고치려고 격자를 다시 돌리지 않는다)."""
    frame = pd.read_csv(path)
    return [RecheckRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[RecheckRow]) -> pd.DataFrame:
    """심볼별 성과 — 시드를 **심볼 안에서 먼저** 접는다(WAN-111/114와 같은 순서).

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
    """단별 심볼평균 — 본표(L1/L2/L2p × TF × 렌즈 × IS/OOS).

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
    """볼린저의 증분 기여 — 룩어헤드 포함(`L1→L2`)과 교정 후(`L1→L2p`)를 나란히.

    델타는 **심볼별로 짝지어** 계산한 뒤 평균한다(심볼평균끼리 빼는 것과 값은 같지만,
    이렇게 해야 `symbols_up`을 셀 수 있다). 심볼 6개는 서로 상관된 표본이라(크립토 베타)
    `symbols_up`은 **유의성이 아니라 방향의 일관성**만 말한다(WAN-104/114와 같은 규칙).
    """
    records: list[dict[str, object]] = []
    for (timeframe, segment, lens), view in symbol_frame.groupby(
        ["timeframe", "segment", "fill"], sort=False
    ):
        pivots = {
            column: view.pivot_table(index="symbol", columns="level", values=column)
            for column in ("total_return", "num_trades", "max_drawdown", "win_rate")
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
                    "band_bar": "tap (룩어헤드)" if cur == TAP_RUNG else "prev_closed (교정)",
                    "delta_return": float(delta.mean()),
                    "symbols_up": float((delta > 0).sum()),
                    "symbols": float(len(delta)),
                    "delta_trades_pct": _relative(pivots["num_trades"], prev, cur),
                    "delta_mdd": _mean_delta(pivots["max_drawdown"], prev, cur),
                    "delta_win_rate": _mean_delta(pivots["win_rate"], prev, cur),
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
    (WAN-96의 비대칭을 부품 축에서 읽는 방법 — WAN-114와 같은 헬퍼).
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
# 판정
# --------------------------------------------------------------------------- #

#: 판정 구간 — 교정 후 증분이 원래의 몇 %가 남았나로 (a)/(b)/(c)를 가른다. 이슈가 문장으로
#: 요구한 세 갈래를 코드에 박아 두는 이유는 WAN-114와 같다: 사람이 손으로 적으면 재실행
#: 때 숫자와 갈라진다(WAN-95가 겪은 사고).
KEEP_MOSTLY = 0.70
KEEP_PARTIAL = 0.30


#: 기준 증분(`L1→L2`)이 0 이하면 잔존 비율이 정의되지 않는다 — 아래 `_verdict_label` 참고.
NO_BASE_LABEL = "판정 대상 아님 (기준 증분 ≤ 0)"


def _verdict_label(kept: float, *, base: float) -> str:
    """잔존 비율 → 이슈의 (a)/(b)/(c).

    ⚠️ **기준 증분이 0 이하면 판정하지 않는다.** 비율은 부호가 같아야 뜻이 있는데, 볼린저가
    애초에 값을 깎던 셀(1h처럼 `L1→L2`가 음수)에서는 교정이 **더 깎아도** 비율이 100%를
    넘어 "(a) 대체로 유지"로 찍힌다 — "유지될 플러스가 없다"를 "잘 유지됐다"로 읽게 만드는
    부호 함정이다. 그런 셀은 잔존이 아니라 **증분의 부호 자체**를 봐야 한다(§2 표).
    """
    if base <= 0:
        return NO_BASE_LABEL
    if kept >= KEEP_MOSTLY:
        return "(a) 대체로 유지"
    if kept >= KEEP_PARTIAL:
        return "(b) 상당 부분 소멸"
    return "(c) 전부 소멸"


def verdict(steps: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """「교정 후 볼린저 증분이 얼마나 남나」를 숫자에서 직접 읽어 문장으로 낸다.

    공식 렌즈(`baseline`)만 판정 대상이다(토대 2) — 나머지는 민감도로 병기한다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = steps[
            (steps["timeframe"] == timeframe)
            & (steps["segment"] == segment)
            & (steps["lens"] == OFFICIAL_LENS)
        ].set_index("step")
        tap_step, prev_step = f"{BASE_RUNG}→{TAP_RUNG}", f"{BASE_RUNG}→{PREV_RUNG}"
        if not {tap_step, prev_step} <= set(view.index):
            continue
        tap = float(view.loc[tap_step, "delta_return"])
        prev = float(view.loc[prev_step, "delta_return"])
        up = int(view.loc[prev_step, "symbols_up"])
        n = int(view.loc[prev_step, "symbols"])
        kept = prev / tap if tap else float("nan")
        # 잔존 비율은 소수 1자리로 적는다 — `.0f`로 반올림하면 경계값(0.6988)이 "70% 잔존"인데
        # 라벨은 (b)로 찍혀 표가 자기모순처럼 읽힌다.
        kept_text = f"**{kept * 100:.1f}% 잔존**, " if tap > 0 else ""
        lines.append(
            f"- **{timeframe} {segment}**: 볼린저 증분이 탭 봉 밴드 **{tap * 100:+.2f}%p** → "
            f"직전 확정봉 밴드 **{prev * 100:+.2f}%p** ({up}/{n}심볼 상승) — "
            f"{kept_text}룩어헤드의 몫 **{(prev - tap) * 100:+.2f}%p** "
            f"→ {_verdict_label(kept, base=tap)}"
        )
    return lines


def lens_sensitivity(steps: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """교정 후 증분이 **체결 낙관에 얼마나 기대나**(렌즈별 `L1→L2p`).

    공식 렌즈가 상한인 이상(토대 2), 교정 후 남은 몫도 렌즈를 조이면 어떻게 되는지 함께
    읽어야 한다 — WAN-114가 볼린저를 "세 렌즈 전부 생존"으로 읽은 그 자리다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        for lens in LENS_NAMES:
            view = steps[
                (steps["timeframe"] == timeframe)
                & (steps["segment"] == segment)
                & (steps["lens"] == lens)
            ].set_index("step")
            tap_step, prev_step = f"{BASE_RUNG}→{TAP_RUNG}", f"{BASE_RUNG}→{PREV_RUNG}"
            if not {tap_step, prev_step} <= set(view.index):
                continue
            tap = float(view.loc[tap_step, "delta_return"])
            prev = float(view.loc[prev_step, "delta_return"])
            lines.append(
                f"- **{timeframe} {segment}** `{lens}`: `L1→L2` {tap * 100:+.2f}%p → "
                f"`L1→L2p` **{prev * 100:+.2f}%p** (룩어헤드의 몫 {(prev - tap) * 100:+.2f}%p)"
            )
    return lines


def adopted_delta(summary: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """채택 기본값 자체가 교정으로 얼마나 움직이나(`L2` → `L2p` 절대 수익률).

    증분(§판정)이 "볼린저가 번 몫"이라면 이쪽은 **채택 기본값의 성적표가 어디로 가나**다 —
    재-베이스라인 여부를 사용자가 판단할 때 보는 숫자가 이것이다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = summary[
            (summary["timeframe"] == timeframe)
            & (summary["segment"] == segment)
            & (summary["fill"] == OFFICIAL_LENS)
        ].set_index("level")
        if not {TAP_RUNG, PREV_RUNG} <= set(view.index):
            continue
        tap = float(view.loc[TAP_RUNG, "total_return"])
        prev = float(view.loc[PREV_RUNG, "total_return"])
        tap_pos, prev_pos = (
            int(view.loc[TAP_RUNG, "positive"]),
            int(view.loc[PREV_RUNG, "positive"]),
        )
        n = int(view.loc[PREV_RUNG, "symbols"])
        lines.append(
            f"- **{timeframe} {segment}**: 채택 기본값 `L2` {tap * 100:+.2f}%({tap_pos}/{n}) → "
            f"교정 `L2p` **{prev * 100:+.2f}%**({prev_pos}/{n}) "
            f"— **{(prev - tap) * 100:+.2f}%p**"
        )
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103/108/110/111/114와 같은 헬퍼)."""
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


def write_summary(rows: Sequence[RecheckRow], path: Path) -> None:
    symbol_frame = per_symbol(rows)
    summary = rung_summary(symbol_frame)
    steps = incremental(symbol_frame)

    official = _sorted(
        symbol_frame[symbol_frame["fill"] == OFFICIAL_LENS],
        ["timeframe", "segment", "level", "symbol"],
    )

    lines = [
        "# WAN-115: 볼린저 진입가 룩어헤드 재검",
        "",
        "재현: `uv run python -m backtest.wan115_bollinger_lookahead_recheck` "
        "(요약만 재생성: `--from-csv`)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 격자다(WAN-111/114와 같은 창). "
        "토대(존 지정가 + 오프셋 2bp · 공식 렌즈 `baseline` · 비용 현재값 · 롱 온리)를 "
        "**고정 입력**으로 받고 **밴드를 어느 봉에서 읽나만** 바꾼다.",
        "",
        "## 무엇을 재나",
        "",
        "볼린저 밴드는 **탭 봉의 SMA20**(= 그 봉 종가 포함)으로 계산되는데 지정가 체결은 그 봉 "
        "**내부**에서 일어난다 — 그 봉이 닫혀야 나오는 가격에 그 봉 도중 주문이 걸린 셈이라 "
        "엄밀히 룩어헤드다(WAN-95가 각주로 적고 넘어간 성질). **WAN-114가 그 전제를 뒤집었다**: "
        "진입 규칙 3부품 중 값을 더하는 건 볼린저 하나뿐이고 **15m 플러스 전체가 이 부품에 "
        "얹혀 있다**(`L1→L2` +16.20%p). 기여가 클수록 거기 섞인 룩어헤드의 절대 크기도 커진다.",
        "",
        ladder_table(),
        "",
        "`L1`·`L2`는 WAN-114 사다리에서 **그대로** 가져왔다(같은 창·같은 설정이라 "
        "`wan114_entry_ablation.csv`의 같은 단과 검산된다). `L2p`가 이 이슈가 새로 얹는 단이고 "
        "`L2`와 **오직 `band_bar`만** 다르다. 직전 확정봉의 SMA20/σ는 탭 봉이 **열리기 전에** "
        "확정돼 있으므로 그 가격으로 지정가를 실제로 걸어 둘 수 있다 = 룩어헤드 없음.",
        "",
        "> ⚠️ **공식 수치는 `baseline`이고 그것은 상한이다**(토대 2). 큐 우선순위를 "
        "모델링하지 않으므로 `pen_5bp`·`pen_5bp_drop_50`을 민감도로 반드시 함께 읽는다.",
        "",
        "> ⚠️ **A안(종가 진입)에는 이 룩어헤드가 없다** — 탭 봉 종가에 진입하므로 그 시점엔 탭 "
        "봉이 이미 닫혀 있다. 이 재검은 채택 경로(B안 지정가)만의 문제다.",
        "",
        "## 판정 — 공식 렌즈(`baseline`) OOS",
        "",
        *verdict(steps, segment=SEGMENT_OOS),
        "",
        "IS 대조:",
        "",
        *verdict(steps, segment=SEGMENT_IS),
        "",
        "## 채택 기본값 자체의 이동 — `L2` → `L2p` (공식 렌즈 OOS)",
        "",
        *adopted_delta(summary, segment=SEGMENT_OOS),
        "",
        "IS 대조:",
        "",
        *adopted_delta(summary, segment=SEGMENT_IS),
        "",
        "## 교정 후 증분의 체결 의존도 — 렌즈별",
        "",
        *lens_sensitivity(steps, segment=SEGMENT_OOS),
        "",
        "## 1. 본표 — 단별 심볼평균 (L1/L2/L2p × TF × 렌즈 × IS/OOS)",
        "",
        "`positive` = 플러스 심볼 수 / `symbols` = 심볼 수. **평균만 보면 심볼 하나가 "
        "끌어올린 것이 안 보인다**(WAN-111).",
        "",
        _md_table(_pct(summary[list(_SUMMARY_VIEW)])),
        "",
        "## 2. 볼린저 증분 — 룩어헤드 포함(`L1→L2`) vs 교정 후(`L1→L2p`)",
        "",
        "`delta_return` = 심볼별로 짝지어 뺀 뒤 평균한 수익률 차 / `symbols_up` = 그 방향으로 "
        "움직인 심볼 수 / `delta_trades_pct` = 거래 수의 상대 변화. 심볼 6개는 서로 상관된 "
        "표본이라(크립토 베타) `symbols_up`은 **유의성이 아니라 방향의 일관성**만 말한다.",
        "",
        _md_table(_pct(steps[list(_STEP_VIEW)])),
        "",
        "## 3. 심볼별 원 수치 — 공식 렌즈(`baseline`)",
        "",
        "민감도 렌즈를 포함한 전 행은 원본 CSV에 있다.",
        "",
        _md_table(_pct(official[list(_SYMBOL_VIEW)])),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-115 볼린저 룩어헤드 재검")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lens", type=str, default=",".join(LENS_NAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--out-csv", type=str, default="backtest/reports/wan115_bollinger_lookahead.csv"
    )
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan115_bollinger_lookahead_summary.md"
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
        print(f"[wan115] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in args.tf.split(",") if t.strip()),
            lenses=tuple(x.strip() for x in args.lens.split(",") if x.strip()),
            start=args.start,
            end=args.end,
        )
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows, Path(args.out_md))
    print(f"[wan115] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
