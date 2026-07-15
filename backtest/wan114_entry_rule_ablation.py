"""WAN-114: 진입 규칙 한계 기여 격리(ablation) — 존 자체가 값인가, 규칙이 값을 더하나.

WAN-84/88/111의 매칭 널이 계속 같은 결론을 냈다: 채택 규칙대로 들어간 거래가 **같은
오더블록 존에 무작위 시점 진입한 것과 통계적으로 구분되지 않는다.** 그런데 그 검정들은
규칙을 **통째로 켠 채** 무작위와 비교하므로, "엣지 없음"이 나와도 **어느 부품이 죽은
무게인지**는 알려주지 않는다. 대기 중인 측정 이슈(WAN-90 익절 R, WAN-113 익절 구조)는
전부 이 규칙 층 **위에** 얹은 노브를 돌리는 작업이라, 규칙 층이 존-단독 대비 아무것도
더하지 못한다면 신호가 없는 층을 광내는 셈이 된다.

이 모듈은 규칙을 **하나씩 켜며 각 부품의 한계 기여(증분 델타)** 를 낸다. 매칭 널은 다시
돌리지 않는다(이미 났다) — 여기 산출물은 **부품별 델타**다.

## 부품 사다리

토대(진입 방식=존 지정가 + 오프셋 2bp, 렌즈=`baseline`, 비용 현재값, 롱 온리, 6심볼)를
**고정 입력**으로 받는다(CLAUDE.md 작업 층 순서). 진입 규칙 부품만 켜고 끈다:

| 단계 | 무엇이 켜지나 | `retap_mode` | `rsi_gate_mode` | `deviation_filter` |
| -- | -- | -- | -- | -- |
| `L0` | 존-단독 (첫 탭만, 무조건) | `once` | `first_tap_free` | `None` |
| `L0r` | + 재탭 (게이트 없음) | `every_tap` | `none` | `None` |
| `L1` | + 재탭 RSI 게이트 | `every_tap` | `first_tap_free` | `None` |
| `L2` | + 볼린저 진입가 재산정 | `every_tap` | `first_tap_free` | 볼린저 |

고정 1.5R 익절·손절(무효화)·롱 온리는 **모든 단계에 공통**이다(이슈가 L0 정의에 넣었다).
즉 이 사다리는 익절 규칙을 재지 않는다 — 그건 WAN-90/113 소관이다.

## `L2`가 곧 이슈의 `L3`(채택 기본값)다 — 사다리는 위에서 겹친다

이슈는 `L3 = 채택 기본값(전부 on)`을 따로 뒀지만, `ConfluenceParams()`가 정확히
**`L1` + 볼린저**다(재탭 `every_tap` · 게이트 `first_tap_free` · 볼린저 on). 그래서
`L2`와 `L3`은 **같은 설정이고 델타는 정의상 0**이다 — 같은 격자를 한 번 더 도는 대신
파라미터 동일성을 테스트로 증명한다(`tests/test_wan114_entry_rule_ablation.py`). 표의
`L2` 행이 곧 채택 기본값 행이다.

## `L0r`은 이슈에 없던 진단 단계다 — 왜 넣었나

이슈의 `L0 → L1`은 **두 가지를 한꺼번에** 바꾼다: (1) 재탭이 생기고(거래 수가 는다),
(2) 그 재탭에 RSI 게이트가 걸린다. 첫 탭은 어차피 면제(`first_tap_free`)라 **RSI 게이트는
재탭에만 존재**하기 때문이다. 둘을 묶어 재면 "RSI가 값을 더하나"라는 이 이슈의 핵심
질문에 답할 수 없다 — 델타가 플러스여도 그게 RSI의 선구안인지 그냥 **존 노출이 늘어서**
인지 구분되지 않는다. `L0r`(재탭은 켜되 게이트는 끈다)이 그 둘을 가른다:

* `L0 → L0r` = **재탭 노출**의 기여 (RSI 없음)
* `L0r → L1` = **RSI 게이트**의 기여 (노출 고정, 선별만 추가)

⚠️ `L0r`은 `L0`의 깨끗한 상위집합이 **아니다**: `rsi_gate_mode="none"`은 게이트를 항상
통과시키지만 시뮬레이터가 `first_tap_free or (live_rsi is not None and ...)`로 판정하므로
(`backtest/substep.py`), 워밍업 구간(RSI None)의 첫 탭은 `L0`에서는 진입하고 `L0r`에서는
막힌다. 구간 시작 14봉에만 걸리는 소수 셋업이라 방향을 뒤집을 크기는 아니지만, 이
비대칭은 델타에 섞여 있다.

## WAN-100 배선을 지킨다

첫 탭 면제는 `tap_index`를 아는 **호출부의 책임**이고(`rsi_gate_passes`는 모드만 보고
첫 탭인지 모른다), 채택 경로(B안)가 그 배선을 빠뜨려 첫 탭에도 재탭 게이트가 걸렸던 것이
WAN-100이다. `L0`은 `retap_mode="once"` + `rsi_gate_mode="first_tap_free"`라 모든 시그널이
`tap_index=0`이고, 따라서 `build_zone_limit_candidates`의
`first_tap_free = (mode == "first_tap_free" and tap_index == 0)`이 항상 참이 된다 —
**워밍업 NaN이어도 지정가 터치 즉시 체결**이다(이슈가 요구한 "L0에서도 첫 탭은 무조건 진입").

## 창을 못 박는다 · 왜 범용 CLI가 아닌가

창은 WAN-111과 **같은 못 박은 창**(2023-07-14~2026-07-15)이다. `--years N`은 마지막 봉
기준으로 창이 미끄러져 심볼마다 다른 기간을 보게 된다(CLAUDE.md). 덕분에 이 표의 `L2`
행은 `wan111_symbol_universe.csv`의 `baseline` 행과 **같은 엔진·같은 창**이라 서로 검산이
된다(실측 확인: BTC 15m OOS 222거래 / +1.23%로 일치).

범용 CLI(`backtest.run`)는 `retap_mode`·`rsi_gate_mode`·`deviation_filter`를 축으로 열지
않는다 — 이 세 노브가 격자 축이 아니므로 CLI로는 이 표를 낼 수 없다. 그래서 harness
(`backtest/harness.py`)의 골격(로딩·구간 분할·경로 스위치·행 조립)을 그대로 **재사용하는
얇은 모듈**만 짰다(WAN-101 예외 조항: 사후 증분 분해·판정 문장은 CLI로 못 낸다).

## 재현

```
uv run python -m backtest.wan114_entry_rule_ablation          # 격자 재실행(~1시간)
uv run python -m backtest.wan114_entry_rule_ablation --from-csv   # 요약만 재생성
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
from strategy.models import ConfluenceParams

#: 심볼 유니버스 = 6심볼(WAN-111). 3심볼 표본이 채택 수치를 이고 있었다는 것이 그 이슈의
#: 결론이라, 부품 델타도 6심볼에서 재야 "규칙의 힘"과 "심볼 하나의 운"이 갈린다.
ALL_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
)

#: WAN-107 공동 작업 TF. 후속 이슈는 두 축을 병기해야 한다.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 공식 렌즈 + 민감도 + 스트레스(CLAUDE.md 토대 2). 이름은 harness에서 가져오므로
#: CLI(`--fill`)·WAN-96/107/110/111 표와 **같은 뜻**이 보장된다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp", "pen_5bp_drop_50")

OFFICIAL_LENS = "baseline"

#: WAN-111과 같은 못 박은 창 — 그래야 이 표의 `L2` 행이 그 리포트와 검산된다.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 이 리포트는 **IS/OOS만** 낸다(이슈 완료기준). 전 구간은 두 구간의 혼합이라 부품
#: 델타에 새 정보를 주지 않으면서 격자 시간을 60% 늘린다 — 필요하면 CLI로 낸다.
SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)


# --------------------------------------------------------------------------- #
# 부품 사다리
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rung:
    """사다리 한 단 — 채택 기본값(`ConfluenceParams()`) 대비 **덮어쓸 필드**로 정의한다.

    기준을 채택 기본값에 두는 이유는 WAN-111 격자와 같다: 부품 값을 상수로 박아 두면
    기본값이 재-베이스라인으로 움직일 때(WAN-112 같은) 이 리포트만 혼자 옛 엔진을 돈다.
    여기 `updates`에 없는 필드는 **전부 채택 기본값을 따라간다**.
    """

    name: str
    adds: str
    """이전 단 대비 이 단이 **새로 켜는 부품**(표·판정 문장이 그대로 쓴다)."""
    updates: Mapping[str, object] = field(default_factory=dict)


#: 사다리. 순서가 곧 증분 델타의 순서다(위에서 아래로 부품이 하나씩 켜진다).
RUNGS: tuple[Rung, ...] = (
    Rung(
        name="L0",
        adds="존-단독 (첫 탭 무조건 진입)",
        updates={
            "retap_mode": "once",
            "rsi_gate_mode": "first_tap_free",
            "deviation_filter": None,
        },
    ),
    Rung(
        name="L0r",
        adds="+ 재탭 노출 (RSI 게이트 없음)",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": "none",
            "deviation_filter": None,
        },
    ),
    Rung(
        name="L1",
        adds="+ 재탭 RSI 게이트",
        updates={
            "retap_mode": "every_tap",
            "rsi_gate_mode": "first_tap_free",
            "deviation_filter": None,
        },
    ),
    Rung(
        name="L2",
        adds="+ 볼린저 진입가 재산정 (= 채택 기본값 = 이슈의 L3)",
        # `deviation_filter`를 덮어쓰지 않는다 = 채택 기본값의 볼린저(SMA20 ± 2σ)를
        # 그대로 쓴다. 나머지 둘도 기본값과 같은 값이라 이 단은 `ConfluenceParams()`와
        # **완전히 동일**하다 — 그게 아래 `ADOPTED_RUNG`의 뜻이고, 테스트가 고정한다.
        updates={"retap_mode": "every_tap", "rsi_gate_mode": "first_tap_free"},
    ),
)

RUNGS_BY_NAME: dict[str, Rung] = {r.name: r for r in RUNGS}

LADDER: tuple[str, ...] = tuple(r.name for r in RUNGS)

#: 존-단독(하한선)과 채택 기본값(상한). 판정이 이 둘의 격차를 읽는다.
BASE_RUNG = "L0"
ADOPTED_RUNG = "L2"


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


class AblationRow(RunRow):
    """격자 한 셀 — `RunRow`(harness 공용 좌표+지표)에 사다리 단만 얹는다.

    `RunRow`를 상속해 열 정의를 공유하는 이유는 CSV가 다른 리포트와 같은 뜻을 갖게 하기
    위해서다. 되읽기(`--from-csv`)도 이 모델로 검증하며 통과한다.
    """

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
) -> list[AblationRow]:
    """사다리 × 심볼 × TF × 렌즈 × 구간 격자를 돈다.

    (심볼, TF)마다 데이터를 한 번만 로드하고 **구간마다 오더블록을 한 번만 탐지해 사다리
    전체가 공유한다** — 탐지는 컨플루언스 파라미터와 무관하므로(존은 규칙 이전에 존재한다)
    결과가 바뀌지 않는다. 이 공유가 이 리포트의 전제이기도 하다: 모든 단이 **같은 존
    집합**을 보고, 다른 건 오직 그 존에 어떻게 진입하냐다.
    """
    fills = tuple(fill_preset(name) for name in lenses)
    rows: list[AblationRow] = []
    for symbol in (normalize_symbol(s) for s in symbols):
        for timeframe in timeframes:
            market = load_market_data(
                symbol, timeframe, start_ms=parse_date_ms(start), end_ms=parse_date_ms(end)
            )
            if market.empty or market.df_1m.empty:
                _log(log, f"[wan114] {symbol} {timeframe}: 데이터 없음 — 건너뜀")
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
                            rows.append(AblationRow(level=rung.name, **row.model_dump()))
                _log(
                    log,
                    f"[wan114] {symbol} {timeframe} {segment.name}: "
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


def rows_to_frame(rows: Sequence[AblationRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[AblationRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고치려고 격자를 다시 돌리지 않는다).

    `AblationRow`로 검증하며 통과하므로 열 이름·타입이 어긋나면 여기서 터진다 — 표와
    판정이 **같은 원본**을 본다는 것이 이 경로의 요점이다(WAN-111과 같은 패턴).
    """
    frame = pd.read_csv(path)
    return [AblationRow.model_validate(record) for record in frame.to_dict(orient="records")]


def per_symbol(rows: Sequence[AblationRow]) -> pd.DataFrame:
    """심볼별 성과 — 시드를 **심볼 안에서 먼저** 접는다.

    `pen_5bp_drop_50`은 시드 5개를 도는데, 심볼 평균을 내기 전에 심볼 안에서 접어야
    증분 델타를 심볼별로 짝지을 수 있다(순서를 바꾸면 뺄 대상이 사라진다 — WAN-111).
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
    """사다리 단별 심볼평균 — 이슈 완료기준의 본표(L0~L3 × TF × 렌즈 × IS/OOS).

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
    """이웃한 두 단의 **증분 델타** — 이 리포트의 산출물.

    델타는 **심볼별로 짝지어** 계산한 뒤 평균한다(심볼평균끼리 빼는 것과 값은 같지만,
    이렇게 해야 `symbols_up`(몇 심볼이 같은 방향인가)을 셀 수 있다). 심볼 6개는 서로
    상관된 표본이라(크립토 베타) `symbols_up`은 **유의성이 아니라 방향의 일관성**만
    말한다 — WAN-104가 증분 13건을 "크기는 못 믿고 방향만 본다"로 읽은 것과 같은 규칙이다.
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
        for prev, cur in zip(LADDER, LADDER[1:], strict=False):
            if prev not in returns.columns or cur not in returns.columns:
                continue
            delta = (returns[cur] - returns[prev]).dropna()
            if delta.empty:
                continue
            trades = pivots["num_trades"]
            records.append(
                {
                    "timeframe": timeframe,
                    "segment": segment,
                    "lens": lens,
                    "step": f"{prev}→{cur}",
                    "adds": RUNGS_BY_NAME[cur].adds,
                    "delta_return": float(delta.mean()),
                    "symbols_up": float((delta > 0).sum()),
                    "symbols": float(len(delta)),
                    "delta_trades_pct": _relative(trades, prev, cur),
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

    WAN-96의 비대칭("거래는 4.7%만 주는데 수익은 사라진다")을 부품 축에서 읽으려면 수익
    델타 옆에 **거래 수가 얼마나 움직였나**가 있어야 한다. 부품이 거래를 크게 줄이지
    않으면서 수익만 옮긴다면, 그 부품은 **선별이 아니라 가격**을 건드린 것이다.
    """
    if prev not in pivot.columns or cur not in pivot.columns:
        return float("nan")
    before = float(pivot[prev].dropna().mean())
    after = float(pivot[cur].dropna().mean())
    if not before:
        return float("nan")
    return (after - before) / before


#: 정렬 키 → 의미 순서. 알파벳 순이면 구간이 `is→oos`를 잃고(`is`<`oos`는 우연) 렌즈도
#: 공식→민감도→스트레스 순서가 깨진다 — 표를 위에서 아래로 읽는 순서가 곧 판정 순서다.
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


def verdict(summary: pd.DataFrame, steps: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """「진입 규칙이 존-단독 대비 값을 더하나」를 숫자에서 직접 읽어 문장으로 낸다.

    공식 렌즈(`baseline`)만 판정 대상이다(토대 2) — 나머지는 민감도로 병기한다. 문장을
    사람이 손으로 적으면 재실행 때 숫자와 갈라진다(WAN-95가 겪은 사고).
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        view = summary[
            (summary["timeframe"] == timeframe)
            & (summary["segment"] == segment)
            & (summary["fill"] == OFFICIAL_LENS)
        ].set_index("level")
        if not {BASE_RUNG, ADOPTED_RUNG} <= set(view.index):
            continue
        base = float(view.loc[BASE_RUNG, "total_return"])
        adopted = float(view.loc[ADOPTED_RUNG, "total_return"])
        base_pos, adopted_pos = (
            int(view.loc[BASE_RUNG, "positive"]),
            int(view.loc[ADOPTED_RUNG, "positive"]),
        )
        n = int(view.loc[ADOPTED_RUNG, "symbols"])
        gap = adopted - base
        headline = "규칙이 값을 더한다" if gap > 0 else "**규칙이 값을 더하지 못한다**"
        lines.append(
            f"- **{timeframe} {segment}**: 존-단독 `L0` {base * 100:+.2f}%({base_pos}/{n}) → "
            f"채택 기본값 `L2` {adopted * 100:+.2f}%({adopted_pos}/{n}) — "
            f"규칙 층 전체 기여 **{gap * 100:+.2f}%p** ({headline})"
        )
        sub = steps[
            (steps["timeframe"] == timeframe)
            & (steps["segment"] == segment)
            & (steps["lens"] == OFFICIAL_LENS)
        ]
        for _, record in sub.iterrows():
            lines.append(
                f"  - `{record['step']}` {record['adds']}: "
                f"**{float(record['delta_return']) * 100:+.2f}%p** "
                f"({int(record['symbols_up'])}/{int(record['symbols'])}심볼 상승 · "
                f"거래 {float(record['delta_trades_pct']) * 100:+.1f}%)"
            )
    return lines


def lens_sensitivity(summary: pd.DataFrame, *, segment: str = SEGMENT_OOS) -> list[str]:
    """규칙 층의 기여가 **체결 낙관에 얼마나 기대나**(렌즈별 L0 → L2 격차).

    부품마다 체결 의존도가 다를 수 있다는 것이 이슈의 방법론 주의다. 공식 렌즈가 상한인
    이상(토대 2), 규칙 층의 기여도 렌즈를 조이면 어떻게 되는지 함께 읽어야 한다.
    """
    lines: list[str] = []
    for timeframe in DEFAULT_TIMEFRAMES:
        for lens in LENS_NAMES:
            view = summary[
                (summary["timeframe"] == timeframe)
                & (summary["segment"] == segment)
                & (summary["fill"] == lens)
            ].set_index("level")
            if not {BASE_RUNG, ADOPTED_RUNG} <= set(view.index):
                continue
            base = float(view.loc[BASE_RUNG, "total_return"])
            adopted = float(view.loc[ADOPTED_RUNG, "total_return"])
            lines.append(
                f"- **{timeframe} {segment}** `{lens}`: `L0` {base * 100:+.2f}% → "
                f"`L2` {adopted * 100:+.2f}% (격차 **{(adopted - base) * 100:+.2f}%p**)"
            )
    return lines


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    """의존성 없이 파이프 마크다운 표를 만든다(WAN-103/108/110/111과 같은 헬퍼)."""
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
    "adds",
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
    records = [
        {
            "level": rung.name,
            "adds": rung.adds,
            "retap_mode": rung_params(rung, fill=fill_preset(OFFICIAL_LENS)).retap_mode,
            "rsi_gate_mode": rung_params(rung, fill=fill_preset(OFFICIAL_LENS)).rsi_gate_mode,
            "deviation_filter": (
                "볼린저(SMA20±2σ)"
                if rung_params(rung, fill=fill_preset(OFFICIAL_LENS)).deviation_filter
                else "off"
            ),
        }
        for rung in RUNGS
    ]
    return _md_table(pd.DataFrame(records))


def write_summary(rows: Sequence[AblationRow], path: Path) -> None:
    symbol_frame = per_symbol(rows)
    summary = rung_summary(symbol_frame)
    steps = incremental(symbol_frame)

    official = _sorted(
        symbol_frame[symbol_frame["fill"] == OFFICIAL_LENS],
        ["timeframe", "segment", "level", "symbol"],
    )

    lines = [
        "# WAN-114: 진입 규칙 한계 기여 격리 (ablation)",
        "",
        "재현: `uv run python -m backtest.wan114_entry_rule_ablation` "
        "(요약만 재생성: `--from-csv`)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 격자다(WAN-111과 같은 창). "
        "토대(존 지정가 + 오프셋 2bp · 공식 렌즈 `baseline` · 비용 현재값 · 롱 온리)를 "
        "**고정 입력**으로 받고 진입 규칙 부품만 켜고 끈다. 고정 1.5R 익절·손절(무효화)은 "
        "**모든 단에 공통**이라 이 표는 익절 규칙을 재지 않는다(WAN-90/113 소관).",
        "",
        "## 사다리",
        "",
        ladder_table(),
        "",
        "**`L2`가 곧 이슈의 `L3`(채택 기본값)다.** `ConfluenceParams()`가 정확히 `L1` + "
        "볼린저라 두 단은 같은 설정이고 델타는 정의상 0이다 — 같은 격자를 한 번 더 도는 "
        "대신 파라미터 동일성을 테스트가 고정한다. 표의 `L2` 행이 채택 기본값 행이다.",
        "",
        "**`L0r`은 이슈에 없던 진단 단이다.** 이슈의 `L0 → L1`은 두 가지를 한꺼번에 바꾼다 "
        "— 재탭이 생기고(노출↑), 그 재탭에 RSI 게이트가 걸린다(첫 탭은 어차피 면제라 "
        "**게이트는 재탭에만 존재**한다). 묶어 재면 「RSI가 값을 더하나」에 답할 수 없어 "
        "`L0r`(재탭 on · 게이트 off)로 둘을 갈랐다: `L0→L0r` = 재탭 노출, `L0r→L1` = RSI 선별.",
        "",
        "> ⚠️ **공식 수치는 `baseline`이고 그것은 상한이다**(토대 2). 큐 우선순위를 "
        "모델링하지 않으므로 `pen_5bp`·`pen_5bp_drop_50`을 민감도로 반드시 함께 읽는다.",
        "",
        "## 판정 — 공식 렌즈(`baseline`) OOS",
        "",
        *verdict(summary, steps, segment=SEGMENT_OOS),
        "",
        "IS 대조:",
        "",
        *verdict(summary, steps, segment=SEGMENT_IS),
        "",
        "## 규칙 층 기여의 체결 의존도 — 렌즈별 `L0` → `L2` 격차",
        "",
        *lens_sensitivity(summary, segment=SEGMENT_OOS),
        "",
        "## 1. 사다리 본표 — 단별 심볼평균 (L0~L3 × TF × 렌즈 × IS/OOS)",
        "",
        "`positive` = 플러스 심볼 수 / `symbols` = 심볼 수. **평균만 보면 심볼 하나가 "
        "끌어올린 것이 안 보인다**(WAN-111).",
        "",
        _md_table(_pct(summary[list(_SUMMARY_VIEW)])),
        "",
        "## 2. 증분 델타 — 부품별 한계 기여",
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
    parser = argparse.ArgumentParser(description="WAN-114 진입 규칙 ablation")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lens", type=str, default=",".join(LENS_NAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--out-csv", type=str, default="backtest/reports/wan114_entry_ablation.csv")
    parser.add_argument(
        "--out-md", type=str, default="backtest/reports/wan114_entry_ablation_summary.md"
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
        print(f"[wan114] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
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
    print(f"[wan114] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
