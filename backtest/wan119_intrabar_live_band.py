"""WAN-119: 봉내 라이브 볼린저 밴드 — 사용자가 실제로 보는 값으로 재면 무엇이 남나.

사용자 관찰(2026-07-16): **"트레이딩뷰는 봉 시작과 동시에 볼린저 띠가 생기고, 봉이
어떻게 움직이느냐에 따라 값이 좀 달라진다."** 정확한 관찰이고, 이 이슈의 출발점이다.
트레이딩뷰는 **형성 중인 봉**에 밴드를 실시간으로 그리며 SMA20의 20번째 표본에 **그
순간의 현재가**를 넣는다. 즉 사용자가 보고 지정가를 거는 밴드는 `tap`도 `prev_closed`도
아니다.

WAN-115가 밴드를 두 갈래로 정리했지만 **둘 다 사용자의 실매매가 아니다**:

| 단 | `band_bar` | 20번째 표본 | 성질 |
| -- | -- | -- | -- |
| `L1` | — | 볼린저 off | 존 근단 지정가(증분의 기준선). WAN-114/115의 `L1`. |
| `L2` | `tap` | 탭 봉 **최종 종가** | **채택 기본값**. 봉 내부 체결엔 룩어헤드(WAN-115). |
| `L2p` | `prev_closed` | 직전 **확정봉** | WAN-115 교정. 룩어헤드 없음, but 보수적. |
| `L2i` | `intrabar_live` | **그 순간의 현재가** | **이 이슈**. 룩어헤드 없이 재현 가능. |

WAN-115는 `L2`→`L2p`가 15m OOS 증분을 **−4.88%p** 깎고 그것이 채택 기본값의 부호를
뒤집는다는 것(**+3.91% → −0.97%**)을 보였다. 그 −4.88%p는 두 가지가 섞인 값이다:
**(1) 미래 정보의 몫**(있으면 안 되는 것)과 **(2) 현재 봉이 담고 있던 실재하는 정보**
(`prev_closed`가 통째로 버린 것). `L2i`가 그 둘을 가른다 — 현재 봉을 쓰되 **미래는
안 쓰기** 때문이다.

## 판정이 답하는 질문

15m 플러스가 `L2i`에서 (a) 되살아남 / (b) 부분 회복(여전히 ≤0) / (c) 여전히 소멸 중
어디인가. `L2p`가 하한이고 `L2`가 (룩어헤드로 부풀린) 상한이라, `L2i`는 그 사이 어딘가에
떨어질 것으로 예상되지만 **어디인지는 재 봐야 안다** — 그게 이 격자다.

## ⚠️ 한계 — 1분봉 근사이지 틱이 아니다 (이슈 작업범위 2)

진짜 틱이 없으므로 "현재가"의 최대 해상도는 1분봉 서브스텝이고, 체결 순간의 현재가를
**그 1분봉 종가로 근사**한다(`backtest.substep`). 여기엔 **1분짜리 잔여 룩어헤드**가
남는다: 밴드는 그 1분봉 종가로 계산되는데 체결 판정(`low <= 지정가`)은 그 **같은 1분봉
안**에서 일어나므로, 엄밀히는 그 1분의 결과를 조금 아는 셈이다. 15m 봉(`tap`)의 룩어헤드를
1분으로 **줄인** 것이지 0으로 만든 게 아니다.

이 관행 자체는 새로 만든 게 아니다 — `RealtimeRsi`가 체결 스텝에서 이미 같은 방식으로
현재가를 쓴다(WAN-41 이후 채택 경로의 규약). 다만 **RSI는 게이트(통과/탈락)이고 밴드는
가격**이라, WAN-115가 "손상은 선별이 아니라 가격"이라고 밝힌 이상 잔여 편향의 성격이
같지 않다. 크기는 이 표가 재지 않는다(재려면 "직전 서브스텝 종가" 단이 하나 더 필요하다)
— `docs/decisions/wan119.md` §한계에 후속 후보로 남긴다.

## 기본값은 바꾸지 않는다

`ConfluenceParams()`는 불변이다(`band_bar="tap"`). 이 모듈은 **측정만** 한다 — 진입가의
정본을 바꾸는 것은 WAN-70/84/88/95/111/114가 선 엔진 정의를 흔드는 **명시적
재-베이스라인**이고 **사용자 결정**이다(CLAUDE.md). 판정·권고는 `docs/decisions/wan119.md`.

## 창·격자는 WAN-111/114/115와 같다

창을 **2023-07-14~2026-07-15**로 못 박은 6심볼 × 15m·1h × IS/OOS × 3렌즈 격자다.
덕분에 `L1`·`L2`·`L2p` 행은 `wan115_bollinger_recheck.csv`의 같은 단과 **비트 단위로
일치**해야 하고, 그것이 이 표의 검산이다(테스트가 파라미터 동일성을 고정한다).

## 재현

```
uv run python -m backtest.wan119_intrabar_live_band          # 격자 재실행(~60분)
uv run python -m backtest.wan119_intrabar_live_band --from-csv   # 요약만 재생성
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

#: 심볼 유니버스 = 6심볼(WAN-111) — WAN-114/115와 같은 표본이라야 그 CSV와 검산된다.
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

#: 렌즈 이름은 harness의 프리셋 이름 그대로 — 이름이 같아야 WAN-111/114/115 표와 같은 뜻이다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

OFFICIAL_LENS = "baseline"

#: WAN-111/114/115와 같은 못 박은 창 — 그래야 `L1`·`L2`·`L2p` 행이 그 리포트와 검산된다.
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

    기준을 채택 기본값에 두는 이유는 WAN-111/114/115와 같다: 부품 값을 상수로 박아 두면
    기본값이 재-베이스라인으로 움직일 때(WAN-112 같은) 이 리포트만 혼자 옛 엔진을 돈다.
    """

    name: str
    label: str
    updates: Mapping[str, object] = field(default_factory=dict)


def _rung(name: str, label: str, band_bar: str | None) -> Rung:
    """`band_bar`만 갈아끼운 단(`None`이면 볼린저 자체를 끈다).

    네 단이 **오직 이 필드 하나로** 갈리는 것이 이 표의 전제다 — 다른 필드가 섞이면
    `L2i`와 `L2p`의 차이가 "봉내 움직임" 때문인지 알 수 없다.
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


#: `L1`·`L2`·`L2p`는 WAN-114/115 사다리에서 그대로 가져왔다(같은 창·같은 설정이라 그 CSV와
#: 검산된다). `L2i`가 이 이슈가 새로 얹는 단이고, `L2`/`L2p`와 **오직 `band_bar`만** 다르다.
RUNGS: tuple[Rung, ...] = (
    _rung("L1", "볼린저 off (존 근단 지정가)", None),
    _rung("L2", "볼린저 on · 탭 봉 종가 (= 채택 기본값 · 룩어헤드)", "tap"),
    _rung("L2p", "볼린저 on · 직전 확정봉 (WAN-115 교정 · 보수적)", "prev_closed"),
    _rung("L2i", "볼린저 on · 봉내 현재가 (트레이딩뷰/실매매 충실)", "intrabar_live"),
)

RUNGS_BY_NAME: dict[str, Rung] = {r.name: r for r in RUNGS}

LADDER: tuple[str, ...] = tuple(r.name for r in RUNGS)

#: 볼린저 없는 바닥(증분의 기준선) · 룩어헤드 포함 현행 · WAN-115 교정 · 이 이슈의 라이브.
BASE_RUNG = "L1"
TAP_RUNG = "L2"
PREV_RUNG = "L2p"
LIVE_RUNG = "L2i"

#: 이 표가 재는 증분 셋. `L1→L2`가 WAN-114의 +16.20%p, `L1→L2p`가 WAN-115의 교정 후 잔존분.
STEPS: tuple[tuple[str, str], ...] = (
    (BASE_RUNG, TAP_RUNG),
    (BASE_RUNG, PREV_RUNG),
    (BASE_RUNG, LIVE_RUNG),
)

#: 단 → 밴드 성질(표에 그대로 찍는다 — 문서와 코드가 갈라지지 않게).
BAND_LABELS: dict[str, str] = {
    TAP_RUNG: "tap (룩어헤드)",
    PREV_RUNG: "prev_closed (교정·보수적)",
    LIVE_RUNG: "intrabar_live (실매매)",
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


class LiveBandRow(RunRow):
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
) -> list[LiveBandRow]:
    """사다리 × 심볼 × TF × 렌즈 × 구간 격자를 돈다.

    구간마다 오더블록을 한 번만 탐지해 네 단이 공유한다 — 탐지는 컨플루언스 파라미터와
    무관하므로(존은 규칙 이전에 존재한다) 결과가 바뀌지 않는다. 이 공유가 비교의 전제다:
    네 단이 **같은 존 집합**을 보고, 다른 건 오직 밴드의 20번째 표본이다.
    """
    fills = tuple(fill_preset(name) for name in lenses)
    rows: list[LiveBandRow] = []
    for symbol in (normalize_symbol(s) for s in symbols):
        for timeframe in timeframes:
            market = load_market_data(
                symbol, timeframe, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end)
            )
            if market.empty or market.df_1m.empty:
                _log(log, f"[wan119] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
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
                            rows.append(LiveBandRow(level=rung.name, **row.model_dump()))
                _log(
                    log,
                    f"[wan119] {symbol} {timeframe} {segment.name}: "
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


def rows_to_frame(rows: Sequence[LiveBandRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[LiveBandRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고치려고 격자를 다시 돌리지 않는다)."""
    frame = pd.read_csv(path)
    return [LiveBandRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[LiveBandRow]) -> pd.DataFrame:
    """심볼별 성과 — 시드를 **심볼 안에서 먼저** 접는다(WAN-111/114/115와 같은 순서).

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
    """단별 심볼평균 — 본표(L1/L2/L2p/L2i × TF × 렌즈 × IS/OOS).

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
    """볼린저의 증분 기여 — 세 밴드 정의를 나란히(`L1→L2` / `L1→L2p` / `L1→L2i`).

    델타는 **심볼별로 짝지어** 계산한 뒤 평균한다(심볼평균끼리 빼는 것과 값은 같지만,
    이렇게 해야 `symbols_up`을 셀 수 있다). 심볼 6개는 서로 상관된 표본이라(크립토 베타)
    `symbols_up`은 **유의성이 아니라 방향의 일관성**만 말한다(WAN-104/114/115와 같은 규칙).
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
    (WAN-96의 비대칭을 부품 축에서 읽는 방법 — WAN-114/115와 같은 헬퍼).
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

#: 「되살아남」 판정의 회복 문턱 — `L2p`(하한)에서 `L2`(룩어헤드 상한)까지의 간극 중
#: `L2i`가 얼마나 되찾나. 이슈가 문장으로 요구한 (a)/(b)/(c)를 코드에 박아 두는 이유는
#: WAN-114/115와 같다: 사람이 손으로 적으면 재실행 때 숫자와 갈라진다(WAN-95의 사고).
RECOVER_PARTIAL = 0.30

#: 간극(`L2 - L2p`)이 0 이하면 회복 비율이 정의되지 않는다 — 아래 `_verdict_label` 참고.
NO_GAP_LABEL = "판정 대상 아님 (룩어헤드의 몫 ≤ 0)"


def _verdict_label(recovered: float, *, gap: float, live_return: float) -> str:
    """회복 비율 + 절대 부호 → 이슈의 (a)/(b)/(c).

    이슈가 물은 것은 "**15m 플러스**가 되살아나느냐"이므로 1차 기준은 **채택 기본값의
    절대 수익률 부호**다 — 간극을 아무리 되찾아도 여전히 마이너스면 "되살아났다"고 할 수
    없다.

    ⚠️ **간극이 0 이하면 판정하지 않는다.** 비율은 부호가 같아야 뜻이 있는데, 룩어헤드가
    오히려 값을 깎던 셀에서는 `L2i`가 더 깎아도 비율이 양수로 찍혀 "회복"으로 읽힌다 —
    WAN-115 §1h가 걸린 그 부호 함정과 같은 부류다. 그런 셀은 **증분의 부호**만 본다.
    """
    if gap <= 0:
        return NO_GAP_LABEL
    if live_return > 0:
        return "(a) 되살아남"
    if recovered / gap >= RECOVER_PARTIAL:
        return "(b) 부분 회복 (여전히 ≤0)"
    return "(c) 여전히 소멸"


def verdict(steps: pd.DataFrame, summary: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """「사용자가 보는 밴드로 재면 15m 플러스가 살아나나」를 숫자에서 직접 읽어 문장으로.

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
        wanted = {f"{BASE_RUNG}→{r}" for r in (TAP_RUNG, PREV_RUNG, LIVE_RUNG)}
        if not wanted <= set(view.index) or LIVE_RUNG not in levels.index:
            continue
        tap = float(view.loc[f"{BASE_RUNG}→{TAP_RUNG}", "delta_return"])
        prev = float(view.loc[f"{BASE_RUNG}→{PREV_RUNG}", "delta_return"])
        live = float(view.loc[f"{BASE_RUNG}→{LIVE_RUNG}", "delta_return"])
        live_return = float(levels.loc[LIVE_RUNG, "total_return"])
        live_pos = int(levels.loc[LIVE_RUNG, "positive"])
        n = int(levels.loc[LIVE_RUNG, "symbols"])
        gap, recovered = tap - prev, live - prev
        share = f"{recovered / gap * 100:.1f}% 회복" if gap > 0 else "회복 비율 정의 안 됨"
        lines.append(
            f"- **{timeframe} {segment}**: 볼린저 증분이 `tap` **{tap * 100:+.2f}%p** · "
            f"`prev_closed` **{prev * 100:+.2f}%p** · `intrabar_live` **{live * 100:+.2f}%p** "
            f"— 룩어헤드의 몫 {gap * 100:.2f}%p 중 **{share}**, 채택 기본값 절대 수익률 "
            f"**{live_return * 100:+.2f}%**({live_pos}/{n}심볼) "
            f"→ {_verdict_label(recovered, gap=gap, live_return=live_return)}"
        )
    return lines


def adopted_ladder(summary: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """채택 기본값의 성적표가 세 밴드 정의에서 어디로 가나(절대 수익률).

    증분(§판정)이 "볼린저가 번 몫"이라면 이쪽은 **성적표**다 — 재-베이스라인 여부를
    사용자가 판단할 때 보는 숫자가 이것이다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = summary[
            (summary["timeframe"] == timeframe)
            & (summary["segment"] == segment)
            & (summary["fill"] == OFFICIAL_LENS)
        ].set_index("level")
        if not {TAP_RUNG, PREV_RUNG, LIVE_RUNG} <= set(view.index):
            continue
        cells = []
        for rung in (TAP_RUNG, PREV_RUNG, LIVE_RUNG):
            value = float(view.loc[rung, "total_return"])
            pos = int(view.loc[rung, "positive"])
            n = int(view.loc[rung, "symbols"])
            cells.append(f"`{rung}` **{value * 100:+.2f}%**({pos}/{n})")
        live = float(view.loc[LIVE_RUNG, "total_return"])
        tap = float(view.loc[TAP_RUNG, "total_return"])
        lines.append(
            f"- **{timeframe} {segment}**: " + " · ".join(cells) + f" — 채택 기본값(`{TAP_RUNG}`) "
            f"대비 **{(live - tap) * 100:+.2f}%p**"
        )
    return lines


#: 「가격 쪽 손상」으로 부를 거래 수 변화의 상한. WAN-115가 `prev_closed`에서 관찰한 +3.0%가
#: 이 안에 들어오는 크기이고, 그 정도면 "같은 셋업에 같은 빈도로 들어갔다"고 읽을 수 있다.
PRICE_ONLY_TRADE_SHIFT = 0.10


def selection_vs_price(symbol_frame: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """손상이 「선별」인가 「가격」인가 — 거래 수가 그대로인데 수익만 움직이면 가격이다.

    WAN-115가 `prev_closed`에서 확인한 성질(거래 +3.0%인데 수익 −4.88%p)이 `intrabar_live`
    에서도 성립하는지 본다. 성립하면 세 밴드 정의는 **같은 셋업에 같은 빈도로 들어가되 값만
    다르게 매기는** 것이고, 곧 이 축의 차이는 순수하게 진입 **가격**이다.

    ⚠️ 여기서 재는 건 **`L2`(채택 기본값) 대비** 변화이므로 `incremental`의 `L1→X` 증분을
    빼서 만들면 안 된다 — 그 둘의 차는 분모가 `L1`이라(볼린저 없는 단은 체결률 100%라 거래가
    가장 많다) 같은 이름의 다른 수가 된다. 그래서 심볼 프레임에서 `L2`→X를 직접 낸다.
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
        for rung in (PREV_RUNG, LIVE_RUNG):
            if not {TAP_RUNG, rung} <= set(pivots["total_return"].columns):
                continue
            d_ret = _mean_delta(pivots["total_return"], TAP_RUNG, rung)
            trades = _relative(pivots["num_trades"], TAP_RUNG, rung)
            d_fill = _mean_delta(pivots["fill_rate"], TAP_RUNG, rung)
            kind = "가격" if abs(trades) < PRICE_ONLY_TRADE_SHIFT else "선별+가격"
            lines.append(
                f"- **{timeframe} {segment}** `{rung}` vs 채택 기본값(`{TAP_RUNG}`): 수익 "
                f"**{d_ret * 100:+.2f}%p**인데 거래 수는 **{trades * 100:+.1f}%** "
                f"(체결률 {d_fill * 100:+.2f}%p) — **{kind}** 쪽 손상"
            )
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103/108/110/111/114/115와 같은 헬퍼)."""
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


def write_summary(rows: Sequence[LiveBandRow], path: Path) -> None:
    symbol_frame = per_symbol(rows)
    summary = rung_summary(symbol_frame)
    steps = incremental(symbol_frame)

    official = _sorted(
        symbol_frame[symbol_frame["fill"] == OFFICIAL_LENS],
        ["timeframe", "segment", "level", "symbol"],
    )

    lines = [
        "# WAN-119: 봉내 라이브 볼린저 밴드 — 사용자가 보는 값으로 재면 무엇이 남나",
        "",
        "재현: `uv run python -m backtest.wan119_intrabar_live_band` (요약만 재생성: `--from-csv`)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 격자다"
        "(WAN-111/114/115와 같은 창). "
        "토대(존 지정가 + 오프셋 2bp · 공식 렌즈 `baseline` · 비용 현재값 · 롱 온리)를 "
        "**고정 입력**으로 받고 **밴드의 20번째 표본만** 바꾼다.",
        "",
        "## 무엇을 재나",
        "",
        '사용자 관찰: **"트레이딩뷰는 봉 시작과 동시에 볼린저 띠가 생기고, 봉이 어떻게 '
        '움직이느냐에 따라 값이 좀 달라진다."** 트레이딩뷰는 형성 중인 봉에 밴드를 실시간으로 '
        "그리며 SMA20의 20번째 표본에 **그 순간의 현재가**를 넣는다. 즉 사용자가 보고 지정가를 "
        "거는 밴드는 `tap`도 `prev_closed`도 아니다.",
        "",
        ladder_table(),
        "",
        "`L1`·`L2`·`L2p`는 WAN-114/115 사다리에서 **그대로** 가져왔다(같은 창·같은 설정이라 "
        "`wan115_bollinger_recheck.csv`의 같은 단과 검산된다). `L2i`가 이 이슈가 새로 얹는 단이고 "
        "`L2`/`L2p`와 **오직 `band_bar`만** 다르다.",
        "",
        "WAN-115가 잰 `L2`→`L2p`의 −4.88%p(15m OOS)는 두 가지가 섞인 값이다: **미래 정보의 몫** "
        "(있으면 안 되는 것)과 **현재 봉이 담고 있던 실재하는 정보**"
        "(`prev_closed`가 통째로 버린 것). "
        "`L2i`가 그 둘을 가른다 — 현재 봉을 쓰되 미래는 안 쓰기 때문이다.",
        "",
        "> ⚠️ **공식 수치는 `baseline`이고 그것은 상한이다**(토대 2). 큐 우선순위를 "
        "모델링하지 않으므로 `pen_5bp`·`pen_5bp_drop_50`을 민감도로 반드시 함께 읽는다.",
        "",
        "> ⚠️ **1분봉 근사이지 틱이 아니다.** 체결 순간의 현재가를 그 1분봉 종가로 근사하므로 "
        "**1분짜리 잔여 룩어헤드**가 남는다(밴드는 그 1분봉 종가로 계산되는데 체결 판정은 같은 "
        "1분봉 안에서 일어난다). 15m 봉의 룩어헤드를 1분으로 **줄인** 것이지 0으로 만든 게 "
        "아니다 — `docs/decisions/wan119.md` §한계.",
        "",
        "## 판정 — 공식 렌즈(`baseline`) OOS",
        "",
        *verdict(steps, summary, segment=SEGMENT_OOS),
        "",
        "IS 대조:",
        "",
        *verdict(steps, summary, segment=SEGMENT_IS),
        "",
        "## 채택 기본값의 성적표 — 세 밴드 정의 (공식 렌즈 OOS)",
        "",
        *adopted_ladder(summary, segment=SEGMENT_OOS),
        "",
        "IS 대조:",
        "",
        *adopted_ladder(summary, segment=SEGMENT_IS),
        "",
        "## 손상은 선별인가 가격인가 (공식 렌즈 OOS)",
        "",
        "거래 수가 거의 그대로인데 수익만 움직이면 **같은 셋업에 같은 빈도로 들어가되 값만 다르게 "
        "매긴 것** = 가격이다(WAN-96/115와 같은 읽기).",
        "",
        *selection_vs_price(symbol_frame, segment=SEGMENT_OOS),
        "",
        "## 1. 본표 — 단별 심볼평균 (L1/L2/L2p/L2i × TF × 렌즈 × IS/OOS)",
        "",
        "`positive` = 플러스 심볼 수 / `symbols` = 심볼 수. **평균만 보면 심볼 하나가 "
        "끌어올린 것이 안 보인다**(WAN-111).",
        "",
        _md_table(_pct(summary[list(_SUMMARY_VIEW)])),
        "",
        "## 2. 볼린저 증분 — 세 밴드 정의",
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


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

CSV_PATH = Path("backtest/reports/wan119_intrabar_live_band.csv")
SUMMARY_PATH = Path("backtest/reports/wan119_intrabar_live_band_summary.md")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-119 봉내 라이브 볼린저 밴드 3자 비교")
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 저장된 원본 CSV로 요약만 재생성한다.",
    )
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = rows_from_csv(args.csv)
    else:
        rows = run_report(start=args.start, end=args.end)
        if not rows:
            print("행이 없습니다 — 데이터를 확인하세요.")
            return 1
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(args.csv, index=False)

    write_summary(rows, args.summary)
    print(f"원본 {args.csv} · 요약 {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
