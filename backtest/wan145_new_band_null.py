"""WAN-145 §1·§3: 봉내 라이브 밴드에서의 매칭 널 — 롱 축 재검 + 숏 축 최초 측정.

[WAN-132](../docs/decisions/wan132.md)가 진입가 정본을 `band_bar="tap"` →
`"intrabar_live"`로 옮기면서 파급을 **재산출이 아니라 `tap` 명시 고정**으로 처리했다
(28개 모듈). 그 선택 자체는 옳지만 대가로 **엣지 판정 계열 전체가 옛 밴드의 기록으로
얼어붙었다** — WAN-124의 매칭 널(`wan123_matched_null`)도 그중 하나다. 즉 지금 CLAUDE.md가
인용하는 「엣지 없음」은 전부 **지금 매매하지 않는 밴드** 위의 숫자다. 이 모듈이 같은 자로
**새 밴드에서 다시** 낸다.

동시에 **숏 축을 처음으로 잰다**(구 WAN-148, 이 이슈 §3). WAN-89가 WAN-87의 숏 비활성화
근거(옛 −3.82%)를 무너뜨렸지만, 그 되살아난 숏 수익이 **무작위 진입과 구분되는지**는 한
번도 검정되지 않았다 — 롱 축은 다섯 번(WAN-70/84/88/111/124) 물었고 매번 "구분되지 않는다"는
답이 나온 그 검정이다. wan89.md §6-2가 못 박았다: *"숏 축 매칭 널을 내기 전에는 「엣지」라는
단어를 쓰지 않는다."*

## 무력화 축은 볼린저다 (WAN-124를 그대로 물려받는다)

게이트가 없는 엔진(`rsi_gate_mode="unconditional"`, WAN-123)에서는 WAN-70/88의 RSI 무력화
오버라이드가 **아무것도 하지 않아** 풀이 실제 후보 집합과 글자 그대로 같아진다(널이 자기
자신을 검정한다). 그래서 무력화 축은 **남은 유일한 선별 규칙인 볼린저**다 —
근거·실측표는 `backtest/wan123_matched_null.py` 모듈 docstring과
[`docs/decisions/wan124.md`](../docs/decisions/wan124.md). 이 모듈은 **축을 바꾸지 않고 밴드만
바꾼다**(그래야 WAN-124 표와 셀 대 셀로 맞댈 수 있다).

⚠️ **이 널은 「선별」과 「가격」을 가르지 못한다** — 풀은 존 근단 가격이고 실제는 밴드가라,
실제가 널을 이겨도 그것이 "볼린저가 좋은 셋업을 고른다"인지 "더 좋은 가격에 넣는다"인지
구분되지 않는다. WAN-96/114/115/120이 전부 **가격** 쪽을 가리키므로 유의성은
**「가격 효과 재확인」으로 먼저 읽는다**(가르는 것은 WAN-131 소관).

## WAN-124와 무엇이 같고 무엇이 다른가

| 축 | WAN-124(`wan123_matched_null`) | 이 모듈 |
| -- | -- | -- |
| 밴드(`band_bar`) | `tap`(고정) | **`intrabar_live`(채택 기본값 — 고정하지 않는다)** |
| 렌즈 | `baseline` + `pen_5bp` | **`baseline` 단독**(WAN-128) |
| 팔 | 롱 온리 하나 | **`long_only` · `short_only` · `both`**(WAN-89 정의 재사용) |
| 무력화 축 | 볼린저 | 볼린저(같음) |
| 자 | 거래 20건 · p≤0.05 & 실제>무작위평균 | 같음 |
| 창·심볼·TF·구간 | 2023-07-14~2026-07-15 · 6심볼 · 15m·1h · IS/OOS | 같음 |
| 부트스트랩 | 200회 · 시드 124 | 200회 · **시드 124(같은 값)** |

시드를 같은 값으로 두는 이유는 자를 같게 두는 이유와 같다 — 밴드만 다른 두 표를 맞대려면
**밴드 말고 다른 것이 움직이면 안 된다**.

## 장세 라벨은 WAN-89의 `buy_hold`를 그대로 쓴다

숏은 장세 의존이 가장 클 후보라 IS/OOS를 나눠 내는데, 그 구간이 실제로 상승장/하락장인지는
**각자 정의하지 않는다** — WAN-89(그리고 WAN-139)와 **같은 함수**(`_buy_hold`)를 같은 창·같은
구간 분할에 적용해 같은 행에 싣는다. 라벨 축이 둘이 되면 자유 파라미터가 두 배가 된다.

## 재현

```
uv run python -m backtest.wan145_new_band_null --tf 1h --jobs 6
uv run python -m backtest.wan145_new_band_null --tf 15m --jobs 6 --append
uv run python -m backtest.wan145_new_band_null --from-csv    # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import run_random_control_b_segment
from backtest.wan89_short_autopsy import ARMS_BY_NAME, Arm, _buy_hold
from strategy.models import ConfluenceParams

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CSV = REPORTS_DIR / "wan145_new_band_null.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan145_new_band_null_summary.md"

#: 못 박은 창 — WAN-111/114/115/119/120/124/137/143과 동일(`--years N`은 미끄러진다).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 6심볼(WAN-111).
ALL_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "TRXUSDT",
)

#: WAN-107 공동 작업 TF.
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

#: 팔은 **WAN-89에서 가져온다**(§3 요구: 팔 정의 재사용). 이름을 여기서 다시 정의하면
#: 두 리포트가 같은 라벨로 다른 설정을 돌 수 있다.
ARM_NAMES: tuple[str, ...] = ("long_only", "short_only", "both")

#: §1(롱 축)의 팔 — 채택 기본값 그 자체다.
LONG_ARM = "long_only"

#: 공식 렌즈는 `baseline` 단독(WAN-128). 민감도 렌즈는 폐지가 아니라 옵트인이지만,
#: 신규 리포트는 단독으로 낸다.
OFFICIAL_LENS = harness.BASELINE_FILL.name

#: 자 — WAN-70/84/88/124와 **같은 값**이라야 「판정이 바뀌었다」와 「자를 바꿨다」가 갈린다.
MIN_TRADES_FOR_VERDICT = 20
ALPHA = 0.05

#: 부트스트랩 — WAN-124와 같은 반복 수·시드(위 표 「무엇이 같고 무엇이 다른가」).
BOOTSTRAP_ITERATIONS = 200
BOOTSTRAP_SEED = 124

#: 널 풀의 무력화 축 = 볼린저 off(WAN-124 `NEUTRALIZED_POOL_UPDATES`와 같은 값).
NEUTRALIZED_POOL_UPDATES: dict[str, object] = {"deviation_filter": None}

#: 편중 진단에서 빼 볼 심볼 — WAN-89에서 숏 네 셀이 전부 이 심볼에 기대고 있었다.
LEAVE_OUT_SYMBOL = "ETH"

SEGMENT_ORDER: tuple[str, ...] = (harness.SEGMENT_IS, harness.SEGMENT_OOS)


# --------------------------------------------------------------------------- #
# 파라미터
# --------------------------------------------------------------------------- #


def arm_of(name: str) -> Arm:
    """WAN-89의 팔 정의를 이름으로 가져온다(정의를 이 모듈에서 다시 쓰지 않는다)."""
    return ARMS_BY_NAME[name]


def real_params(arm: Arm) -> ConfluenceParams:
    """검정 대상 = **지금 채택된 기본값 + 팔의 롱/숏 스위치**.

    ⚠️ `band_bar`를 **고정하지 않는다** — 그게 이 모듈의 존재 이유다. WAN-124는 검산
    (`wan123_fill_conservatism` 48셀 · `wan111` CSV 비트 일치)을 지키려고 `tap`에 묶여
    있고, 그래서 새 밴드 판정은 **별도로** 내야 한다(WAN-132 §6).
    """
    return arm.params()


def pool_params(arm: Arm) -> ConfluenceParams:
    """널 풀 = 실제에서 **볼린저만 끈 것**(= 존 근단 지정가에 무조건 진입).

    롱/숏 스위치·렌즈·시드는 실제와 같게 둔다 — 다르면 널이 규칙이 아니라 팔이나 체결
    가정을 재게 된다.
    """
    return real_params(arm).model_copy(update=NEUTRALIZED_POOL_UPDATES)


def describe_engine() -> str:
    """이 리포트가 검정한 엔진의 지문 — 산출물만 봐도 어떤 밴드로 돌았는지 드러나게."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_mode={p.rsi_mode}, "
        f"rsi_gate_mode={p.rsi_gate_mode}, retap_mode={p.retap_mode}, "
        f"zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_mode={p.take_profit_mode}, take_profit_r={p.take_profit_r}, "
        f"band_bar={band}"
    )


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class NullRow(BaseModel):
    """한 (심볼, TF, 구간, 팔)의 매칭 널 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    arm: str
    fill: str
    real_total_return: float
    real_num_trades: int
    real_long: int
    real_short: int
    pool_size: int
    """볼린저 무력화 풀(체결된 후보) 크기 — 표본추출 대상 전체."""
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int
    bucket_fallback_count: int
    buy_hold: float
    """구간 바이앤홀드 = 장세 라벨(WAN-89 `_buy_hold`와 **같은 함수**)."""

    @field_validator("*", mode="before")
    @classmethod
    def _nan_to_none(cls, value: object) -> object:
        """CSV 왕복에서 빈 칸(→ `NaN`)을 `None`으로 되돌린다(`harness.RunRow`와 같은 가드)."""
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF) — 워커가 자기 데이터를 자기가 로드한다."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    iterations: int
    arm_names: tuple[str, ...]


def run_cell(task: _Task, *, log: bool = True) -> list[NullRow]:
    """한 (심볼, TF)의 IS/OOS × 팔 널을 낸다."""
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []

    rows: list[NullRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name not in SEGMENT_ORDER:
            continue  # 전 구간은 두 구간의 혼합이라 널에 새 정보를 주지 않는다.
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = harness.detect_order_blocks(window, harness.LEGACY_OB_PARAMS)
        buy_hold = _buy_hold(window.htf_df)

        for arm_name in task.arm_names:
            arm = arm_of(arm_name)
            cfg = arm.config(task.timeframe)
            result = run_random_control_b_segment(
                window.htf_df,
                window.df_1m,
                task.timeframe,
                symbol=task.symbol,
                segment="IS" if segment.name == harness.SEGMENT_IS else "OOS",
                gate=arm_name,
                confluence_params=real_params(arm),
                backtest_config=cfg,
                order_block_result=ob_result,
                iterations=task.iterations,
                seed=BOOTSTRAP_SEED,
                funding_rates=window.funding_rates,
                pool_params=pool_params(arm),
            )
            row = NullRow(
                symbol=task.symbol,
                timeframe=task.timeframe,
                segment=segment.name,
                arm=arm_name,
                fill=OFFICIAL_LENS,
                real_total_return=result.real_total_return,
                real_num_trades=result.real_num_trades,
                real_long=result.real_long,
                real_short=result.real_short,
                pool_size=result.pool_size,
                random_mean_return=result.random_mean_return,
                random_ci_low=result.random_ci_low,
                random_ci_high=result.random_ci_high,
                random_p_value=result.random_p_value,
                iterations=result.iterations,
                bucket_fallback_count=result.bucket_fallback_count,
                buy_hold=buy_hold,
            )
            rows.append(row)
            if log:
                print(
                    f"[wan145-null] {task.symbol} {task.timeframe} {segment.name} {arm_name}: "
                    f"real={row.real_total_return:.4f} n={row.real_num_trades} "
                    f"(L{row.real_long}/S{row.real_short}) pool={row.pool_size} "
                    f"p={row.random_p_value}",
                    flush=True,
                )
    return rows


def _run_task_logged(task: _Task) -> list[NullRow]:
    return run_cell(task, log=True)


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    arm_names: Sequence[str] = ARM_NAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> list[NullRow]:
    """6심볼 × 2TF × IS/OOS × 3팔 격자를 돈다.

    `jobs`는 **성능 노브이지 결과 축이 아니다**(WAN-121) — (심볼, TF) 단위로만 갈라
    제출 순서대로 모으므로 직렬과 행·순서가 같다.
    """
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            iterations=iterations,
            arm_names=tuple(arm_names),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]

    rows: list[NullRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[NullRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[NullRow]:
    """저장된 원본을 행으로 되읽는다 — 요약과 CSV가 갈라질 수 없게(WAN-111 패턴)."""
    frame = pd.read_csv(path)
    return [NullRow.model_validate(record) for record in frame.to_dict(orient="records")]


def is_significant(row: NullRow, alpha: float = ALPHA) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제>무작위평균(WAN-70/84/88/124와 같은 자)."""
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def eligible_rows(rows: Sequence[NullRow], *, arm: str | None = None) -> list[NullRow]:
    """유효 셀 = p값이 나왔고 실제 거래가 `MIN_TRADES_FOR_VERDICT`건 이상."""
    return [
        r
        for r in rows
        if (arm is None or r.arm == arm)
        and r.random_p_value is not None
        and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def arm_summary(rows: Sequence[NullRow]) -> pd.DataFrame:
    """(TF × 구간 × 팔) 심볼평균 + 유의 셀 수 + ETH 제외 평균.

    평균 옆에 `positive`(플러스 심볼 수)와 `ex_eth`(ETH 제외 평균)를 나란히 두는 이유는
    WAN-89/111과 같다 — **평균만 보면 심볼 하나가 만든 값이 안 보인다**.
    """
    records: list[dict[str, object]] = []
    frame = rows_to_frame(rows)
    if frame.empty:
        return pd.DataFrame(records)
    for (timeframe, segment, arm), view in frame.groupby(
        ["timeframe", "segment", "arm"], sort=False
    ):
        values = [float(v) for v in view["real_total_return"]]
        ex_eth = [
            float(r["real_total_return"])
            for _, r in view.iterrows()
            if _short(str(r["symbol"])) != LEAVE_OUT_SYMBOL
        ]
        cells = [
            r for r in rows if r.timeframe == timeframe and r.segment == segment and r.arm == arm
        ]
        eligible = eligible_rows(cells)
        records.append(
            {
                "timeframe": timeframe,
                "segment": segment,
                "arm": arm,
                "real_mean": _mean(values),
                "positive": float(sum(1 for v in values if v > 0)),
                "symbols": float(len(values)),
                "ex_eth_mean": _mean(ex_eth),
                "eligible": float(len(eligible)),
                "significant": float(sum(1 for r in eligible if is_significant(r))),
                "random_mean": _mean(
                    [r.random_mean_return for r in cells if r.random_mean_return is not None]
                ),
                "trades": _mean([float(r.real_num_trades) for r in cells]),
                "buy_hold": _mean([r.buy_hold for r in cells]),
            }
        )
    return _sorted(pd.DataFrame(records))


_ORDERINGS: dict[str, tuple[str, ...]] = {
    "segment": SEGMENT_ORDER,
    "arm": ARM_NAMES,
    "timeframe": DEFAULT_TIMEFRAMES,
}


def _sorted(frame: pd.DataFrame) -> pd.DataFrame:
    """의미 순서로 정렬 — 알파벳 순이면 IS/OOS도 팔 순서도 뜻을 잃는다."""
    if frame.empty:
        return frame
    out = frame.copy()
    helpers: list[str] = []
    for key, order in _ORDERINGS.items():
        if key not in out.columns:
            continue
        helper = f"_order_{key}"
        out[helper] = out[key].map({name: i for i, name in enumerate(order)})
        helpers.append(helper)
    return out.sort_values(helpers).drop(columns=helpers).reset_index(drop=True)


def significance_counts(rows: Sequence[NullRow], *, arm: str) -> tuple[int, int]:
    """(유의 셀 수, 유효 셀 수)."""
    eligible = eligible_rows(rows, arm=arm)
    return sum(1 for r in eligible if is_significant(r)), len(eligible)


def per_timeframe_counts(rows: Sequence[NullRow], *, arm: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for timeframe in DEFAULT_TIMEFRAMES:
        scoped = [r for r in rows if r.timeframe == timeframe]
        out[timeframe] = significance_counts(scoped, arm=arm)
    return out


def verdict(rows: Sequence[NullRow], *, arm: str) -> str:
    """한 팔의 판정 문장 — (a) 구분된다 / (b) 구분 안 됨 / (c) TF·구간에 갈림.

    숫자는 전부 행에서 계산한다. 문장에 숫자를 박아 두면 재실행 뒤 리포트가 조용히
    거짓말을 한다(WAN-88 `_implications`의 원칙).
    """
    sig, total = significance_counts(rows, arm=arm)
    if total == 0:
        return (
            f"`{arm}`: **⚠️ 판정 불가** — 거래 {MIN_TRADES_FOR_VERDICT}건 이상인 유효 셀이 "
            "하나도 없다(표본 부족)."
        )
    by_tf = per_timeframe_counts(rows, arm=arm)
    tf_note = " · ".join(f"{tf} {s}/{t}" for tf, (s, t) in by_tf.items() if t)
    if sig == 0:
        head = "**(b) 무작위와 구분되지 않는다**"
    elif sig == total:
        head = "**(a) 무작위와 구분된다**"
    else:
        head = "**(c) 일부 셀에만 유의성이 있다 — TF·구간에 갈린다**"
    return f"`{arm}`: 유효 셀 {total}개 중 유의 {sig}개({tf_note}) → {head}"


def eth_dependence(rows: Sequence[NullRow], *, arm: str, segment: str) -> list[str]:
    """유의성이 나와도 ETH 하나에 기대고 있으면 그렇게 적는다(§3 요구 1)."""
    summary = arm_summary(rows)
    lines: list[str] = []
    if summary.empty:
        return lines
    view = summary[(summary["arm"] == arm) & (summary["segment"] == segment)]
    for _, record in view.iterrows():
        mean = record["real_mean"]
        ex = record["ex_eth_mean"]
        if mean is None or ex is None or pd.isna(mean) or pd.isna(ex):
            continue
        flip = "부호가 뒤집힌다" if float(mean) > 0 >= float(ex) else "부호는 유지된다"
        lines.append(
            f"- **{record['timeframe']} {segment}** `{arm}`: 심볼평균 {float(mean) * 100:+.2f}% → "
            f"ETH 제외 {float(ex) * 100:+.2f}% ({flip})"
        )
    return lines


def pool_growth_note(rows: Sequence[NullRow]) -> str:
    """풀이 실제보다 크다는 것이 「널이 퇴화하지 않았다」는 CSV 상의 증거다."""
    scoped = [r for r in rows if r.real_num_trades > 0 and r.pool_size > 0]
    if not scoped:
        return "풀 크기를 비교할 셀이 없다."
    ratios = [r.pool_size / r.real_num_trades for r in scoped]
    return (
        f"{len(scoped)}셀에서 무력화 풀은 실제 거래 수의 평균 "
        f"**{sum(ratios) / len(ratios):.2f}배**(최소 {min(ratios):.2f}배)다 — 풀이 실제와 "
        "같아지는 퇴화(WAN-124가 발견한 함정)는 이 표에서 일어나지 않았다. 코드가 막고 "
        "있지만(같은 파라미터면 `run_random_control_b_segment`가 거부한다) 막혔다는 것이 "
        "산출물에도 보여야 한다."
    )


def build_conclusion(rows: Sequence[NullRow]) -> str:
    """롱 축·숏 축 판정 문장을 나란히."""
    lines = [
        "### 롱 축 (§1) — 새 밴드에서 「엣지 없음」이 유지되는가",
        "",
        verdict(rows, arm=LONG_ARM),
        "",
        "### 숏 축 (§3) — 처음 재는 축이다",
        "",
    ]
    for arm in ("short_only", "both"):
        lines.append(verdict(rows, arm=arm))
        lines.append("")
    lines.extend(
        [
            "⚠️ **숏 축 판정은 롱 축 판정의 반박이 아니다** — 「엣지 없음」"
            "(WAN-84/88/111/114/124)은 **RSI 타이밍·볼린저를 롱에서** 물어 얻은 답이고, "
            "이 표의 숏 행은 **처음 묻는 질문**이다.",
            "",
            "⚠️ **`baseline`은 낙관 렌즈다**(「닿으면 체결」 · 큐 우선순위 미모델링) — 유의 "
            "셀이 나와도 그 상한 위의 값이다. 실제 해소는 틱·호가 데이터(WAN-98, Canceled) "
            "소관이다.",
            "",
            pool_growth_note(rows),
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _md_table(frame: pd.DataFrame) -> str:
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


_PERCENT_COLUMNS = (
    "real_mean",
    "ex_eth_mean",
    "random_mean",
    "buy_hold",
    "real_total_return",
)


def _rounded(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in _PERCENT_COLUMNS:
        if col in out.columns:
            out[col] = (out[col].astype(float) * 100).round(2)
    for col in ("positive", "symbols", "eligible", "significant", "trades"):
        if col in out.columns:
            out[col] = out[col].astype(float).round(2)
    return out.astype(object).where(out.notna(), "—")


def cell_table(rows: Sequence[NullRow], *, arm: str) -> str:
    """한 팔의 셀별 표 — 실제/무작위평균/p값/거래수(§3 완료기준의 열)."""
    header = (
        "| 심볼 | TF | 구간 | 실제수익 | n | L/S | 풀 | 무작위평균 | 95% CI | p | 유의 |\n"
        "| -- | -- | -- | --: | --: | -- | --: | --: | -- | --: | -- |"
    )
    scoped = sorted(
        (r for r in rows if r.arm == arm),
        key=lambda r: (
            DEFAULT_TIMEFRAMES.index(r.timeframe) if r.timeframe in DEFAULT_TIMEFRAMES else 9,
            SEGMENT_ORDER.index(r.segment) if r.segment in SEGMENT_ORDER else 9,
            r.symbol,
        ),
    )
    body = []
    for r in scoped:
        ci = (
            f"[{r.random_ci_low:.3f}, {r.random_ci_high:.3f}]"
            if r.random_ci_low is not None and r.random_ci_high is not None
            else "—"
        )
        thin = r.real_num_trades < MIN_TRADES_FOR_VERDICT
        mark = "표본부족" if thin else ("**✓**" if is_significant(r) else "")
        body.append(
            f"| {_short(r.symbol)} | {r.timeframe} | {r.segment} | "
            f"{r.real_total_return * 100:+.2f}% | {r.real_num_trades} | "
            f"{r.real_long}/{r.real_short} | {r.pool_size} | "
            f"{'—' if r.random_mean_return is None else f'{r.random_mean_return * 100:+.2f}%'} | "
            f"{ci} | "
            f"{'—' if r.random_p_value is None else f'{r.random_p_value:.3f}'} | {mark} |"
        )
    return header + "\n" + "\n".join(body)


_SUMMARY_VIEW = (
    "timeframe",
    "segment",
    "arm",
    "real_mean",
    "positive",
    "symbols",
    "ex_eth_mean",
    "random_mean",
    "significant",
    "eligible",
    "trades",
    "buy_hold",
)


def build_summary_markdown(rows: Sequence[NullRow], *, csv_path: Path) -> str:
    summary = arm_summary(rows)
    view = summary[list(_SUMMARY_VIEW)] if not summary.empty else summary
    lines = [
        "# WAN-145 §1·§3 — 봉내 라이브 밴드에서의 매칭 널 (롱 축 재검 + 숏 축 최초 측정)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 6심볼 × 2TF(15m·1h) × IS/OOS × "
        "3팔 격자. 렌즈는 **`baseline` 단독**(WAN-128), 무력화 축은 **볼린저**(WAN-124와 같음).",
        "",
        f"재현: `uv run python -m backtest.wan145_new_band_null` (요약만: `--from-csv`). "
        f"원자료: `{csv_path}`.",
        "",
        "## 이 리포트가 검정한 엔진",
        "",
        f"**지금 채택된 기본값 그대로** — `{describe_engine()}` + 펀딩비 반영(실제·널 양쪽에 "
        "동일하게). 전략 파라미터는 하나도 바꾸지 않았다(검증 전용).",
        "",
        "> 🚨 **밴드를 고정하지 않은 것이 이 리포트의 요점이다.** WAN-124의 널"
        '(`wan123_matched_null`)은 검산을 지키려고 `band_bar="tap"`에 묶여 있어 **지금 '
        "매매하지 않는 밴드**를 잰다. 여기서는 채택 기본값(`intrabar_live`)을 그대로 따라간다.",
        "",
        "> ⚠️ **팔 정의는 WAN-89에서 가져왔고 장세 라벨(`buy_hold`)도 같은 함수를 쓴다** — "
        "라벨을 각자 정의하면 자유 파라미터가 두 배가 된다.",
        "",
        "## 1. 팔 × TF × 구간 요약",
        "",
        "`real_mean` = 심볼평균 실제 수익 / `ex_eth_mean` = ETH 제외 평균 / "
        "`significant`/`eligible` = 유의 셀 / 유효 셀(거래 "
        f"{MIN_TRADES_FOR_VERDICT}건 이상) / `buy_hold` = 구간 바이앤홀드(장세 라벨).",
        "",
        _md_table(_rounded(view)) if not summary.empty else "행이 없다.",
        "",
        "## 2. 롱 축 (§1) — 셀별 결과",
        "",
        cell_table(rows, arm=LONG_ARM),
        "",
        "`p` = 무작위 반복 중 실제 총수익률 이상을 낸 비율(단측). 95% CI는 무작위 분포의 "
        "2.5~97.5 백분위수. `풀` = 볼린저를 끈 존-단독 후보 수(표본추출 대상). "
        "널 정의(방향·시각대를 맞춘 재표본추출)는 `backtest/wan70_random_control_b.py` 참고.",
        "",
        "## 3. 숏 축 (§3) — 셀별 결과",
        "",
        "### `short_only` (롱을 빼고 숏만)",
        "",
        cell_table(rows, arm="short_only"),
        "",
        "### `both` (롱+숏 — 동시 1포지션 슬롯을 다툰다)",
        "",
        cell_table(rows, arm="both"),
        "",
        "## 4. 심볼 편중 — ETH leave-one-out",
        "",
        *[
            line
            for arm in ARM_NAMES
            for segment in SEGMENT_ORDER
            for line in eth_dependence(rows, arm=arm, segment=segment)
        ],
        "",
        "## 결론",
        "",
        build_conclusion(rows),
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-145 §1·§3 새 밴드 매칭 널")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--arms", type=str, default=",".join(ARM_NAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수")
    parser.add_argument("--out-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 CSV에 이번 실행 행을 덧붙인다(TF를 나눠 돌릴 때).",
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan145-null] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        rows = run_report(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            arm_names=tuple(a.strip() for a in str(args.arms).split(",") if a.strip()),
            start=args.start,
            end=args.end,
            iterations=args.iterations,
            jobs=args.jobs,
        )
        if args.append and out_csv.exists():
            rows = rows_from_csv(out_csv) + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
        print(f"[wan145-null] {len(rows)}행 → {out_csv}")

    Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out_md).write_text(build_summary_markdown(rows, csv_path=out_csv), encoding="utf-8")
    print(f"[wan145-null] summary → {args.out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
