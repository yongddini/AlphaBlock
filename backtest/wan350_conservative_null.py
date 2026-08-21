"""WAN-350: 「엣지 없음」을 오늘 좌표 · 가장 보수적인 체결 가정 위에서 다시 묻는다.

## 동기 — 사용자 지시 (2026-08-21, "그걸 돌려보자. 나는 그 엣지없다는 말을 어떻게든 해소해야겠어")

[WAN-346](../docs/decisions/wan346.md)이 채택 북의 `oos_warm`에서 두 설명을 **실측으로
죽였다**: **베타**(같은 창 12종목 buy&hold 평균 −6.6% · 중앙값 −26.6%인데 롱 온리로
+854%)와 **복리 착시**(사이징을 초기 자본에 못 박아도 +854%, 거래당 net R은 0.1985 →
0.1952로 거의 불변). 남은 유력 용의자는 **체결 낙관**이고 그것도 WAN-346 팔 D가 쟀다
(거래당 net R 0.1985 → 0.1089 = **절반이 되지만 부호는 남는다**).

그런데 **팔 D가 버텨도 「엣지 있음」이 되지 않는다.** 이 저장소의 「엣지 없음」
(WAN-84/88/111/114/124/151/201/248)이 실제로 말한 것은 *"같은 존에 **무작위 시각**으로
진입해도 비슷하게 나온다"*이고, **그 널은 오늘 좌표의 보수 가정 위에서 돌린 적이 없다.**
비어 있던 칸은 정확히 하나 — **팔 D 위의 타이밍 매칭 널**이고, 이 모듈이 그것을 낸다.

## 무엇을 재는가 (그리고 무엇을 재지 않는가)

- **널 = 타이밍 축**: 같은 오더블록 유니버스 · 같은 거래 수 · 같은 방향 · 같은 시각대
  버킷에서 **어느 셋업이 체결되는지만** 무작위화한다(WAN-70 매칭 널). 묻는 것은
  *"우리 규칙의 진입 **타이밍**이 우연 대비 값을 더하는가"* 하나다.
- **무력화 축 = 볼린저**(`deviation_filter=None`). 🚨 **RSI 게이트 축은 쓸 수 없다** —
  게이트가 없는 오늘 엔진에서는 풀 == 실제가 되어 널이 **자기 자신을 검정**한다
  (WAN-124 발견 · `run_random_control_b_segment`가 `ValueError`로 거부한다).
- **재지 않는 것**: (b) 위치 널(가짜 존 — WAN-248, 다른 질문) · 큐 우선순위 실측
  (틱·호가 WAN-98, Canceled) · 「선별 대 가격」 갈래(WAN-352가 판정 관문에서 뺐다).

## 팔 — WAN-346의 보수 축 2×2에서 두 모서리

| 팔 | 렌즈 | 같은 분 익절 | 뜻 |
| -- | -- | -- | -- |
| `A` | `baseline` | 허용 | 채택 엔진 그대로 = 기준선 |
| `D` | `pen_5bp` | **금지** | 가장 보수적 — 두 축을 쌓음 |

두 축은 **직교한다**: 가로 `pen_5bp`는 *「주문이 채워지느냐」*(큐 우선순위, WAN-96/124),
세로 `no_same_step_tp`는 *「채워진 뒤 그 1분 안의 순서」*(WAN-336).

🚨 **두 축은 실제 팔과 무력화 풀에 똑같이 걸린다.** 한쪽만 걸면 「실제는 보수, 널은 낙관」인
잡종 대조가 되어 p값이 규칙이 아니라 **가정 차이**를 재게 된다 — `_build_both_pools`가 두
생성을 한 함수에 묶어 이 실패를 구조적으로 막는다.

## 구간 — 따뜻한 연속 규약 (WAN-166)

주 수치는 `oos_warm`이고 `full`을 기준선으로 병기한다. **둘은 한 번의 후보 생성에서
나온다**(`run_random_control_b_evals`) — 창이 같고 평가 경계만 다르므로 존·지표·후보를
다시 태울 이유가 없다. 그래서 구간을 하나 더 내는 비용이 부트스트랩(싸다)뿐이다.

⚠️ **차가운 `oos`는 이 격자에 없다** — 차가운 절단은 구간마다 존 탐지부터 다시 해야 해
후보를 공유할 수 없고, 비용이 그대로 두 배가 된다. 차가운 축은 대신 **검산(§verify)**이
1h에서 낸다(그 축이 곧 옛 널 계열의 규약이라 대조 상대가 거기 있다).

## 🚨 재진입은 이 널에 없다 — 알고 뺀 것이고, 그 이유가 검산이다

채택 북은 재진입 ON(band, WAN-273)인데 **이 표의 두 팔은 재진입을 돌지 않는다.** 원칙
(WAN-305 「측정은 채택 규칙 위에서」)에 어긋나 보이지만 매칭 널에서는 그럴 수 없다:

1. **널이 구조적으로 표현하지 못한다.** 매칭 널은 *미리 만든 풀에서 실제와 같은 개수를
   뽑는* 방식인데, 재진입 후보는 **부모 거래가 익절로 청산돼야** 생긴다. 즉 개수 자체가
   샘플에 의존해 「같은 개수를 맞춘다」는 널의 정의가 성립하지 않는다.
2. **비용이 200배로 는다.** 부트스트랩이 싼 이유는 셋업당 서브스텝 시뮬레이션을 정확히
   1회만 하기 때문인데(WAN-70 §성능), 재진입은 표본마다 새로 시뮬레이션해야 한다.
3. **검산이 깨진다.** 완료기준 4의 대조 상대(`wan176_null.csv`·`wan201_matched_null.csv`)가
   전부 재진입-off 계열이다. 재진입을 넣으면 「판정이 바뀌었다」와 「축을 하나 더 움직였다」를
   가를 수 없다.

**재진입이 들어갈 자리는 북 널이다**(이슈가 이미 다음 단계로 지목 — 북은 배치가 싸다).
그래서 이 표는 이슈 본문대로 **탐색·귀속**이고 채택 근거가 아니다(WAN-341).

## 재현

```
uv run python -m backtest.wan350_conservative_null --part null --tf 4h --jobs 4
uv run python -m backtest.wan350_conservative_null --part null --tf 2h --jobs 4 --append
uv run python -m backtest.wan350_conservative_null --part null --tf 1h --jobs 4 --append
uv run python -m backtest.wan350_conservative_null --part null --tf 15m --jobs 4 --append
uv run python -m backtest.wan350_conservative_null --part verify --jobs 4
uv run python -m backtest.wan350_conservative_null --part summary        # CSV에서 요약만
```

측정 전용 — `ConfluenceParams()`·`LeverageBookParams()` 기본값은 바꾸지 않는다. 보수 팔은
전부 옵트인이고 끄면 엔진이 비트 단위로 예전과 같다. 실거래 보류 유지.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest import wan151_split_zone_null as wan151
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import (
    RandomControlBResult,
    run_random_control_b_evals,
    run_random_control_b_segment,
)
from backtest.wan70_random_control_b import (
    Segment as NullSegment,
)
from backtest.wan89_short_autopsy import _buy_hold
from backtest.wan176_nine_symbol_rebaseline import NEW_SYMBOLS, NINE_SYMBOLS
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
NULL_CSV = REPORTS_DIR / "wan350_conservative_null.csv"
LOO_CSV = REPORTS_DIR / "wan350_conservative_null_loo.csv"
VERIFY_CSV = REPORTS_DIR / "wan350_verify.csv"
SUMMARY_MD = REPORTS_DIR / "wan350_conservative_null_summary.md"

#: 검산 상대 — 읽기 전용(절대 다시 쓰지 않는다). 둘 다 `wan151.NullRow` 스키마다.
WAN176_NULL_CSV = REPORTS_DIR / "wan176_null.csv"  # 필터 켜짐 × 9종목 × 6년 · 차가운 IS/OOS
WAN201_NULL_CSV = REPORTS_DIR / "wan201_matched_null.csv"  # 필터 꺼짐 × 9종목 × 6년

#: 채택 좌표 — **핀이 하나도 없다**(WAN-305). 유니버스·TF·창을 기본값에서 읽으므로
#: 재-베이스라인이 오면 이 표도 따라간다.
SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS  # 12종목 (WAN-307)
TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES  # 15m·1h·2h·4h (WAN-252)
START: str = harness.DEFAULT_START
END: str = harness.DEFAULT_END

#: 자 — WAN-70/84/88/124/145/151/164/201과 **같은 값**이라야 「판정이 바뀌었다」와
#: 「자를 바꿨다」가 갈린다. 값을 여기서 새로 쓰지 않고 wan151에서 물려받는 것이 요점이다.
MIN_TRADES_FOR_VERDICT = wan151.MIN_TRADES_FOR_VERDICT
ALPHA = wan151.ALPHA
BOOTSTRAP_ITERATIONS = wan151.BOOTSTRAP_ITERATIONS
BOOTSTRAP_SEED = wan151.BOOTSTRAP_SEED
NEUTRALIZED_POOL_UPDATES = wan151.NEUTRALIZED_POOL_UPDATES
LONG_ARM = wan151.LONG_ARM

#: 탐지 파라미터 = 채택 기본값(분리 존, WAN-149). 상수를 `OrderBlockParams()`로 두는 것이
#: 요점이다 — 핀을 박으면 재-베이스라인이 와도 이 표만 옛 존으로 돈다.
ADOPTED_OB_PARAMS = OrderBlockParams()

#: 따뜻한 연속 규약(WAN-166)의 두 창. `full`은 기준선, `oos_warm`이 주 수치다.
#: 널 기계의 `Segment` 리터럴로 좁혀 둔다 — 그래야 호출부가 `type: ignore` 없이 통과하고,
#: 값이 `harness`의 라벨과 같은지는 회귀 테스트가 지킨다(두 곳이 갈리면 CSV의 구간 이름이
#: 다른 리포트와 안 맞는다).
SEGMENT_FULL: NullSegment = "full"
SEGMENT_OOS_WARM: NullSegment = "oos_warm"
SEGMENT_ORDER: tuple[NullSegment, ...] = (SEGMENT_FULL, SEGMENT_OOS_WARM)

NOISE_TOLERANCE = 1e-9


# --------------------------------------------------------------------------- #
# 팔 — WAN-346 보수 축 2×2의 두 모서리
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    """보수 축의 한 팔.

    `lens`가 `None`이면 채택 렌즈(`baseline`)다 — 「채택 기본값을 호출부가 복사하지 않는다」는
    이 저장소의 규약(WAN-159 `UNSET` 계열)이라 팔 A는 렌즈를 **명시하지 않는다**.
    """

    name: str
    lens: str | None
    no_same_step_tp: bool
    label: str

    @property
    def lens_name(self) -> str:
        return self.lens or harness.BASELINE_FILL.name

    @property
    def is_adopted(self) -> bool:
        """이 팔이 **채택 엔진 그대로**인가 — 옛 널 기록과 대조할 수 있는 유일한 팔."""
        return self.lens is None and not self.no_same_step_tp

    def params(self) -> ConfluenceParams:
        """검정 대상 = 채택 기본값 + 렌즈. 존폭 필터(1.28)·밴드·게이트·오프셋은 안 건드린다.

        `build_params`에 `max_zone_width_atr`를 안 넘기므로 센티넬 `UNSET`이 되어 채택
        기본값 1.28을 그대로 물려받는다(WAN-159 규약 — 여기 `None`을 넣으면 「필터 끔」이다).
        """
        return harness.build_params(
            fill=harness.fill_preset(self.lens_name),
            base=wan151.arm_of(LONG_ARM).params(),
        )

    def pool_params(self) -> ConfluenceParams:
        """널 풀 = 실제에서 **볼린저만 끈 것**(WAN-124/145/151/164/201과 같은 무력화 축).

        렌즈는 `ConfluenceParams`에 실려 있으므로 이 복사가 **같은 렌즈를 그대로 물려받는다**
        — 그래서 두 집합이 같은 체결 가정 위에 선다(가정 차이가 p값에 섞이지 않는다).
        """
        return self.params().model_copy(update=NEUTRALIZED_POOL_UPDATES)


ARMS: tuple[Arm, ...] = (
    Arm("A", None, False, "채택 엔진(현행) = 기준선"),
    Arm("D", "pen_5bp", True, "가장 보수적 — 체결 보수화 + 같은 분 익절 금지"),
)
ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}
ARM_ORDER: tuple[str, ...] = tuple(a.name for a in ARMS)
ADOPTED_ARM = "A"
MOST_CONSERVATIVE_ARM = "D"


def describe_engine() -> str:
    """이 리포트가 검정한 엔진의 지문 — 산출물만 봐도 어떤 존·밴드·필터로 돌았는지 드러나게."""
    p = ConfluenceParams()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, "
        f"limit_valid_bars={p.limit_valid_bars}, "
        f"combine_obs={ADOPTED_OB_PARAMS.combine_obs}"
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
    arm_label: str
    lens: str
    no_same_step_tp: bool
    combine_obs: bool
    """탐지에 넘어간 존 정책 — 상수 라벨을 따로 쓰지 않고 `ADOPTED_OB_PARAMS`(탐지에 실제로
    넘긴 그 객체)에서 읽어 "분리로 돌고 병합 라벨이 붙는" WAN-95 부류를 막는다."""
    max_zone_width_atr: float | None
    """실제로 엔진에 넘어간 존폭 필터 값 — 「필터 끔」 라벨을 단 채 1.28로 도는 이중 필터
    (WAN-159가 경계한 자리)가 산출물에서 바로 보이게 한다."""
    real_total_return: float
    real_num_trades: int
    real_long: int
    real_short: int
    pool_size: int
    random_mean_return: float | None
    random_ci_low: float | None
    random_ci_high: float | None
    random_p_value: float | None
    iterations: int
    bucket_fallback_count: int
    zones: int
    buy_hold: float


def rows_to_frame(rows: Sequence[NullRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[NullRow]:
    """저장된 원본을 행으로 되읽는다 — 요약과 CSV가 갈라질 수 없게(WAN-111 패턴)."""
    frame = pd.read_csv(path)
    return [NullRow.model_validate(record) for record in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 셀 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF) — 워커가 자기 데이터를 자기가 로드한다.

    팔은 **태스크 안에서** 돈다. 두 팔이 같은 시장 데이터·같은 존 탐지 결과를 나눠 쓰므로
    로딩(6년 1분봉)과 탐지를 팔마다 반복하지 않는다.
    """

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    arm_names: tuple[str, ...]
    iterations: int


def _row_from(
    result: RandomControlBResult,
    *,
    arm: Arm,
    segment: str,
    zones: int,
    buy_hold: float,
) -> NullRow:
    params = arm.params()
    return NullRow(
        symbol=result.symbol,
        timeframe=result.timeframe,
        segment=segment,
        arm=arm.name,
        arm_label=arm.label,
        lens=arm.lens_name,
        no_same_step_tp=arm.no_same_step_tp,
        combine_obs=ADOPTED_OB_PARAMS.combine_obs,
        max_zone_width_atr=params.max_zone_width_atr,
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
        zones=zones,
        buy_hold=buy_hold,
    )


def _from_ms(frame: pd.DataFrame, start_ms: int) -> pd.DataFrame:
    """평가 경계 이후의 봉만 — 구간별 buy&hold를 그 구간에서 재기 위해서다."""
    view = frame[frame["open_time"].astype("int64") >= start_ms]
    return view if not view.empty else frame.iloc[-1:]


def run_cell(task: _Task, *, log: bool = True) -> list[NullRow]:
    """한 (심볼, TF)의 `full`·`oos_warm` × 팔 널을 낸다.

    창을 **한 번만** 태우고 두 평가창을 낸다(`run_random_control_b_evals`) — 따뜻한 연속
    규약(WAN-166)에서 `oos_warm`은 같은 연속 실행의 뒷부분이라 정의상 후보를 공유한다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []

    ob_result = harness.detect_order_blocks(market, ADOPTED_OB_PARAMS)
    warm_from = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    if warm_from is None:  # pragma: no cover - WARM_OOS_SEGMENT는 항상 경계를 갖는다
        raise RuntimeError("따뜻한 OOS 경계를 구하지 못했습니다.")
    evals: tuple[tuple[NullSegment, int | None], ...] = (
        (SEGMENT_FULL, None),
        (SEGMENT_OOS_WARM, warm_from),
    )
    #: 대조용 buy&hold는 **그 구간의 것**이라야 한다 — 전 구간 값을 `oos_warm` 행에 달면
    #: 「6년 상승분」을 뒷구간 성적 옆에 나란히 두게 되어 베타 비교가 통째로 거짓이 된다.
    #: 존 재고는 두 창이 공유하지만(따뜻한 규약) 시장 수익률은 공유하지 않는다.
    buy_hold_by_segment = {
        SEGMENT_FULL: _buy_hold(market.htf_df),
        SEGMENT_OOS_WARM: _buy_hold(_from_ms(market.htf_df, warm_from)),
    }
    cfg = wan151.arm_of(LONG_ARM).config(task.timeframe)

    rows: list[NullRow] = []
    for arm_name in task.arm_names:
        arm = ARMS_BY_NAME[arm_name]
        results = run_random_control_b_evals(
            market.htf_df,
            market.df_1m,
            task.timeframe,
            symbol=task.symbol,
            evals=evals,
            gate=LONG_ARM,
            confluence_params=arm.params(),
            backtest_config=cfg,
            order_block_result=ob_result,
            iterations=task.iterations,
            seed=BOOTSTRAP_SEED,
            funding_rates=market.funding_rates,
            pool_params=arm.pool_params(),
            no_same_step_tp=arm.no_same_step_tp,
        )
        for segment in SEGMENT_ORDER:
            result = results[segment]
            row = _row_from(
                result,
                arm=arm,
                segment=segment,
                zones=len(ob_result.order_blocks),
                buy_hold=buy_hold_by_segment[segment],
            )
            rows.append(row)
            if log:
                print(
                    f"[wan350] {task.symbol} {task.timeframe} {segment} arm={arm.name}"
                    f"(lens={arm.lens_name}, noSameStepTP={arm.no_same_step_tp}): "
                    f"real={row.real_total_return:.4f} n={row.real_num_trades} "
                    f"pool={row.pool_size} p={row.random_p_value}",
                    flush=True,
                )
    return rows


def _run_task_logged(task: _Task) -> list[NullRow]:
    return run_cell(task, log=True)


def run_null(
    *,
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[str] = TIMEFRAMES,
    arm_names: Sequence[str] = ARM_ORDER,
    start: str = START,
    end: str = END,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> list[NullRow]:
    """(심볼 × TF) 격자를 돈다. `jobs`는 **성능 노브이지 결과 축이 아니다**(WAN-121)."""
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            arm_names=tuple(arm_names),
            iterations=iterations,
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


def is_significant(row: NullRow, alpha: float = ALPHA) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제>무작위평균(WAN-70/84/88/124/145/151과 같은 자)."""
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def eligible_rows(rows: Sequence[NullRow]) -> list[NullRow]:
    """유효 셀 = p값이 나왔고 실제 거래가 `MIN_TRADES_FOR_VERDICT`건 이상."""
    return [
        r
        for r in rows
        if r.random_p_value is not None and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def significance_counts(rows: Sequence[NullRow]) -> tuple[int, int]:
    """(유의 셀 수, 유효 셀 수)."""
    eligible = eligible_rows(rows)
    return sum(1 for r in eligible if is_significant(r)), len(eligible)


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _scoped(
    rows: Sequence[NullRow],
    *,
    arm: str | None = None,
    timeframe: str | None = None,
    segment: str | None = None,
    exclude_symbol: str | None = None,
) -> list[NullRow]:
    return [
        r
        for r in rows
        if (arm is None or r.arm == arm)
        and (timeframe is None or r.timeframe == timeframe)
        and (segment is None or r.segment == segment)
        and (exclude_symbol is None or _short(r.symbol) != exclude_symbol)
    ]


def grid_summary(rows: Sequence[NullRow]) -> pd.DataFrame:
    """(팔 × TF × 구간) 유의 셀 수 + 심볼평균 + 플러스 심볼 수.

    평균 옆에 `positive`(플러스 심볼 수)를 두는 이유는 WAN-89/111과 같다 — **평균만 보면
    심볼 하나가 만든 값이 안 보인다**. 판정 열은 `significant`/`eligible`이고 평균은 참고다.
    """
    records: list[dict[str, object]] = []
    for arm in ARM_ORDER:
        for timeframe in TIMEFRAMES:
            for segment in SEGMENT_ORDER:
                cells = _scoped(rows, arm=arm, timeframe=timeframe, segment=segment)
                if not cells:
                    continue
                significant, eligible = significance_counts(cells)
                values = [c.real_total_return for c in cells]
                records.append(
                    {
                        "arm": arm,
                        "timeframe": timeframe,
                        "segment": segment,
                        "significant": significant,
                        "eligible": eligible,
                        "symbols": len(cells),
                        "real_mean": _mean(values),
                        "positive": sum(1 for v in values if v > 0),
                        "random_mean": _mean(
                            [
                                c.random_mean_return
                                for c in cells
                                if c.random_mean_return is not None
                            ]
                        ),
                        "trades": _mean([float(c.real_num_trades) for c in cells]),
                        "thin": sum(1 for c in cells if c.real_num_trades < MIN_TRADES_FOR_VERDICT),
                    }
                )
    return pd.DataFrame(records)


class LooRow(BaseModel):
    """종목 하나를 뺀 뒤의 (팔 × 구간) 유의 셀 수 — 「유의가 한 종목에 기대는가」."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    excluded: str
    significant: int
    eligible: int
    real_mean: float | None


def leave_one_out(rows: Sequence[NullRow]) -> list[LooRow]:
    """종목을 하나씩 빼고 유의 셀 수를 다시 센다.

    📌 **북 LOO와 성격이 다르다** — 북은 지갑을 **다시 배치**해야 하지만(WAN-316 스코프 패턴)
    per-cell 널은 셀이 곧 심볼이라 **재계산이 아니라 재집계**다. 그래서 공짜이고, 대신
    「그 종목이 빠지면 다른 칸의 자본이 남는다」 같은 북 효과는 여기에 **없다**.
    """
    out: list[LooRow] = []
    symbols = sorted({_short(r.symbol) for r in rows})
    for arm in ARM_ORDER:
        for segment in SEGMENT_ORDER:
            base = _scoped(rows, arm=arm, segment=segment)
            if not base:
                continue
            for excluded in ("(없음)", *symbols):
                cells = (
                    base
                    if excluded == "(없음)"
                    else _scoped(rows, arm=arm, segment=segment, exclude_symbol=excluded)
                )
                significant, eligible = significance_counts(cells)
                out.append(
                    LooRow(
                        arm=arm,
                        segment=segment,
                        excluded=excluded,
                        significant=significant,
                        eligible=eligible,
                        real_mean=_mean([c.real_total_return for c in cells]),
                    )
                )
    return out


def loo_to_frame(rows: Sequence[LooRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


# --------------------------------------------------------------------------- #
# 검산 — 팔 A의 널이 옛 기록을 비트 재현하는가 (완료기준 4)
# --------------------------------------------------------------------------- #

#: 검산은 1h 한정으로 돌려 비용을 묶는다 — 두 대조 상대가 모두 1h를 갖고 있고(wan176은
#: 15m·1h, wan201은 15m·1h·4h), 재현 대상은 **기계**이지 특정 TF가 아니다.
VERIFY_TIMEFRAMES: tuple[str, ...] = ("1h",)

#: 옛 널 계열의 구간 규약 = **차가운 절단**(창을 먼저 자르고 그 안에서 다시 탐지).
#: 이 격자의 따뜻한 규약과 다르므로 두 표의 OOS 수치를 나란히 인용하면 안 된다.
VERIFY_SEGMENTS: tuple[str, ...] = (harness.SEGMENT_IS, harness.SEGMENT_OOS)

_VERIFY_NUMERIC = (
    "real_total_return",
    "real_num_trades",
    "real_long",
    "real_short",
    "pool_size",
    "random_mean_return",
    "random_ci_low",
    "random_ci_high",
    "random_p_value",
    "iterations",
    "bucket_fallback_count",
    "buy_hold",
)


class VerifyRow(BaseModel):
    """한 검산의 결과 — 「일치 · 잡음 · 불일치」를 다르게 찍는다(WAN-151/161 패턴)."""

    model_config = ConfigDict(frozen=True)

    check: str
    reference: str
    rows_compared: int
    max_abs_diff: float | None
    status: str
    note: str


def _classify(rows_compared: int, max_abs_diff: float | None) -> tuple[str, str]:
    """검산 결과를 세 갈래로 찍는다 — 조용한 통과를 만들지 않는다.

    `rows_compared == 0`을 **일치로 접지 않는 것**이 요점이다(키가 어긋나면 비교할 것이
    없어 "차이 0"이 나오는데, 그건 재현이 아니라 대조 실패다 — WAN-333이 이름 붙인 부류).
    """
    if rows_compared == 0:
        return "불일치", "비교된 행이 없다 — 키가 어긋났거나 산출이 비었다."
    if max_abs_diff is None or max_abs_diff == 0.0:
        return "일치", "차이 0 — 비트 단위 재현."
    if max_abs_diff < NOISE_TOLERANCE:
        return "잡음", f"최대 절대차 {max_abs_diff:.2e} — 부동소수 끝자리(메모리 원값 대 CSV 왕복)."
    return "불일치", "같은 키의 값이 다르다 — 배선이 어긋났다."


def _compare(ours: pd.DataFrame, reference: pd.DataFrame) -> tuple[int, float | None]:
    keys = ["symbol", "timeframe", "segment"]
    left = ours.set_index(keys).sort_index()
    right = reference.set_index(keys).sort_index()
    common = left.index.intersection(right.index)
    if common.empty:
        return 0, None
    max_diff = 0.0
    for col in _VERIFY_NUMERIC:
        a = pd.to_numeric(left.loc[common, col], errors="coerce")
        b = pd.to_numeric(right.loc[common, col], errors="coerce")
        both_nan = a.isna() & b.isna()
        diff = (a - b).abs().mask(both_nan, 0.0)
        if diff.isna().any():
            return int(len(common)), float("inf")
        if not diff.empty:
            max_diff = max(max_diff, float(diff.max()))
    return int(len(common)), max_diff


def _verify_funding(symbol: str, funding: Sequence[object]) -> Sequence[object]:
    """검산 전용 — DOGE·LINK·LTC의 펀딩을 **비워** 옛 실행 당시의 데이터 상태로 되돌린다.

    🚨 **데이터가 그 뒤 바뀌었다.** wan176(2026-07-23경)·wan201(2026-07-27경)이 돌 때 이 세
    종목은 `funding_rate` 테이블에 **0행**이었고, WAN-292(2026-08-12)가 상장 시점부터
    백필한 지금은 실데이터가 있다. 그래서 같은 좌표·같은 코드를 줘도 그 CSV가 안 나온다 —
    거래 집합은 비트 동일한데 **펀딩 비용만** 달라진다(WAN-312가 같은 자리에서 겪었고,
    「핀은 파라미터를 고정하지 데이터 상태를 고정하지 못한다」로 기록했다).

    ⚠️ **검산 전용이다** — 오늘 좌표 격자(§1)에는 절대 쓰지 않는다(실데이터를 버리는 것이 된다).
    """
    return () if _short(symbol) in {_short(s) for s in NEW_SYMBOLS} else funding


@dataclass(frozen=True)
class _VerifyTask:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    max_zone_width_atr: float | None


def run_verify_cell(task: _VerifyTask) -> list[wan151.NullRow]:
    """옛 규약(차가운 절단 · 구간마다 재탐지)으로 한 셀을 돈다 — `wan151.run_cell`과 같은 자.

    보수 팔의 두 노브를 **끈 채** 도는 것이 요점이다(팔 A) — 켜면 재현이 성립하지 않는다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []
    arm = ARMS_BY_NAME[ADOPTED_ARM]
    real = arm.params().model_copy(update={"max_zone_width_atr": task.max_zone_width_atr})
    pool = real.model_copy(update=NEUTRALIZED_POOL_UPDATES)
    cfg = wan151.arm_of(LONG_ARM).config(task.timeframe)

    rows: list[wan151.NullRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name not in VERIFY_SEGMENTS:
            continue
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = harness.detect_order_blocks(window, ADOPTED_OB_PARAMS)
        result = run_random_control_b_segment(
            window.htf_df,
            window.df_1m,
            task.timeframe,
            symbol=task.symbol,
            segment="IS" if segment.name == harness.SEGMENT_IS else "OOS",
            gate=LONG_ARM,
            confluence_params=real,
            backtest_config=cfg,
            order_block_result=ob_result,
            iterations=BOOTSTRAP_ITERATIONS,
            seed=BOOTSTRAP_SEED,
            funding_rates=_verify_funding(task.symbol, window.funding_rates),  # type: ignore[arg-type]
            pool_params=pool,
        )
        rows.append(
            wan151.NullRow(
                symbol=task.symbol,
                timeframe=task.timeframe,
                segment=segment.name,
                arm=LONG_ARM,
                fill=wan151.OFFICIAL_LENS,
                combine_obs=ADOPTED_OB_PARAMS.combine_obs,
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
                zones=len(ob_result.order_blocks),
                buy_hold=_buy_hold(window.htf_df),
            )
        )
    return rows


def _verify_one(
    *,
    check: str,
    reference_csv: Path,
    max_zone_width_atr: float | None,
    jobs: int,
) -> VerifyRow:
    tasks = [
        _VerifyTask(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(START),
            end_ms=parse_date_ms(END),
            max_zone_width_atr=max_zone_width_atr,
        )
        for symbol in NINE_SYMBOLS
        for timeframe in VERIFY_TIMEFRAMES
    ]
    if jobs <= 1 or len(tasks) <= 1:
        rows = [row for task in tasks for row in run_verify_cell(task)]
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            for result in executor.map(run_verify_cell, tasks):
                rows.extend(result)
    if not reference_csv.exists():
        return VerifyRow(
            check=check,
            reference=str(reference_csv),
            rows_compared=0,
            max_abs_diff=None,
            status="생략",
            note="대조 CSV가 없다 — 저장소에 그 리포트가 없으면 검산할 상대가 없다.",
        )
    ours = wan151.rows_to_frame(rows)
    reference = pd.read_csv(reference_csv)
    reference = reference[reference["arm"] == LONG_ARM]
    reference = reference[reference["timeframe"].isin(list(VERIFY_TIMEFRAMES))].reset_index(
        drop=True
    )
    compared, diff = _compare(ours, reference)
    status, note = _classify(compared, diff)
    if status != "불일치" and compared != len(reference):
        status, note = "불일치", f"비교 행 {compared} ≠ 기준 행 {len(reference)}."
    return VerifyRow(
        check=check,
        reference=str(reference_csv),
        rows_compared=compared,
        max_abs_diff=diff,
        status=status,
        note=note,
    )


def run_verify(*, jobs: int = 1) -> list[VerifyRow]:
    """두 검산 — 노브를 끈 기계가 옛 널 계열을 비트 재현하는가.

    이것이 통과해야 §1의 「팔 D에서 판정이 이렇게 됐다」가 **가정의 몫**으로 읽힌다 —
    안 통과하면 리팩터가 숫자를 움직였다는 뜻이라 표 전체가 무효다.
    """
    return [
        _verify_one(
            check="armA-filter-on-vs-wan176",
            reference_csv=WAN176_NULL_CSV,
            max_zone_width_atr=ConfluenceParams().max_zone_width_atr,
            jobs=jobs,
        ),
        _verify_one(
            check="armA-filter-off-vs-wan201",
            reference_csv=WAN201_NULL_CSV,
            max_zone_width_atr=None,
            jobs=jobs,
        ),
    ]


def verify_to_frame(rows: Sequence[VerifyRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


# --------------------------------------------------------------------------- #
# 요약 렌더링
# --------------------------------------------------------------------------- #


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _arm_table() -> str:
    """팔 정의를 `ARMS`에서 그린다 — 표를 손으로 적으면 팔을 더할 때 라벨만 낡는다."""
    return "".join(
        f"| `{a.name}` | `{a.lens_name}` | "
        f"{'금지' if a.no_same_step_tp else '허용'} | {a.label} |\n"
        for a in ARMS
    )


def _grid_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(행 없음)_\n"
    head = (
        "| 팔 | TF | 구간 | 유의/유효 | 실제 심볼평균 | 플러스 "
        "| 무작위 평균 | 평균 거래 | 표본미달 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: |\n"
    )
    body = "".join(
        f"| {r.arm} | {r.timeframe} | {r.segment} | "
        f"**{int(r.significant)}/{int(r.eligible)}** | {_pct(r.real_mean)} | "
        f"{int(r.positive)}/{int(r.symbols)} | {_pct(r.random_mean)} | "
        f"{r.trades:.1f} | {int(r.thin)} |\n"
        for r in frame.itertuples()
    )
    return head + body


def _headline(rows: Sequence[NullRow]) -> str:
    """완료기준 2 — 보수화가 널 통과 여부를 바꾸는가."""
    lines: list[str] = []
    for segment in SEGMENT_ORDER:
        parts: list[str] = []
        for arm in ARM_ORDER:
            significant, eligible = significance_counts(_scoped(rows, arm=arm, segment=segment))
            parts.append(f"팔 {arm} **{significant}/{eligible}**")
        lines.append(f"- `{segment}`: " + " · ".join(parts))
    return "\n".join(lines)


def _loo_table(rows: Sequence[LooRow], *, segment: str) -> str:
    scoped = [r for r in rows if r.segment == segment]
    if not scoped:
        return "_(행 없음)_\n"
    excluded = sorted({r.excluded for r in scoped}, key=lambda e: (e != "(없음)", e))
    head = "| 제외 종목 | " + " | ".join(f"팔 {a} 유의/유효" for a in ARM_ORDER) + " |\n"
    head += "| -- | " + " | ".join("--:" for _ in ARM_ORDER) + " |\n"
    body = ""
    for name in excluded:
        cells: list[str] = []
        for arm in ARM_ORDER:
            match = [r for r in scoped if r.arm == arm and r.excluded == name]
            cells.append(f"{match[0].significant}/{match[0].eligible}" if match else "—")
        body += f"| {name} | " + " | ".join(cells) + " |\n"
    return head + body


def _verify_table(rows: Sequence[VerifyRow]) -> str:
    if not rows:
        return "_(검산 미실행 — `--part verify`)_\n"
    head = (
        "| 검산 | 대조 상대 | 행 | 최대 절대차 | 판정 | 비고 |\n| -- | -- | --: | --: | -- | -- |\n"
    )
    body = "".join(
        f"| `{r.check}` | `{Path(r.reference).name}` | {r.rows_compared} | "
        f"{'—' if r.max_abs_diff is None else f'{r.max_abs_diff:.2e}'} | "
        f"**{r.status}** | {r.note} |\n"
        for r in rows
    )
    return head + body


def build_summary(
    rows: Sequence[NullRow],
    loo: Sequence[LooRow],
    verify: Sequence[VerifyRow],
) -> str:
    grid = grid_summary(rows)
    timeframes = [tf for tf in TIMEFRAMES if any(r.timeframe == tf for r in rows)]
    symbols = sorted({_short(r.symbol) for r in rows})
    return f"""# WAN-350 — 「엣지 없음」을 가장 보수적인 체결 가정 위에서 다시 묻는다

**타이밍 매칭 널 × 보수 팔 A·D** (per-cell · 롱 축 · 무력화 축 = 볼린저)

- 엔진: `{describe_engine()}`
- 좌표: {len(symbols)}종목({", ".join(symbols)}) × {", ".join(timeframes)}
  × {START}~{END} · **핀 없음**
- 자: 유효 셀 = 거래 ≥ {MIN_TRADES_FOR_VERDICT}건 · 유의 = `p ≤ {ALPHA}` **이면서**
  실제 > 무작위평균 · 부트스트랩 {BOOTSTRAP_ITERATIONS}회(시드 {BOOTSTRAP_SEED})
  — WAN-70/84/88/124/145/151/164/201과 **같은 값**
- 구간: 따뜻한 연속 규약(WAN-166). `oos_warm`이 주 수치, `full`은 기준선.

| 팔 | 렌즈 | 같은 분 익절 | 뜻 |
| -- | -- | -- | -- |
{_arm_table()}

## §0 헤드라인 — 보수화가 널 통과 여부를 바꾸는가 (완료기준 2)

{_headline(rows)}

🚨 **읽는 법**: 이 표가 답하는 질문은 *"우리 규칙의 진입 **타이밍**이 같은 존에 무작위로
들어간 것보다 나은가"* **하나**다. 유의 셀이 늘어도 **「엣지 있음」이 아니다** — 유의는
수익이 아니고(WAN-124: 유의하면서 돈을 잃는 셀이 있다), 이 널은 「선별」과 「가격」을
가르지 않으며(풀은 존 근단가·실제는 밴드가), **per-cell이라 채택 근거가 아니다**(WAN-341).

## §1 격자 — 팔 × TF × 구간 (완료기준 1)

{_grid_table(grid)}

`유의/유효` = p≤{ALPHA} & 실제>무작위평균인 셀 / 거래 {MIN_TRADES_FOR_VERDICT}건 이상인 셀.
`표본미달` = 거래가 {MIN_TRADES_FOR_VERDICT}건에 못 미쳐 판정에서 빠진 셀 수
(주의문이 아니라 **집계**다).

## §2 leave-one-out — 유의가 한 종목에 기대는가 (완료기준 3)

### `oos_warm` (주 수치)

{_loo_table(loo, segment=SEGMENT_OOS_WARM)}

### `full`

{_loo_table(loo, segment=SEGMENT_FULL)}

📌 **북 LOO와 성격이 다르다** — per-cell 널은 셀이 곧 심볼이라 **재집계**이고(공짜),
북처럼 지갑을 다시 배치하지 않는다(WAN-316). 그래서 「그 종목이 빠지면 다른 칸의 자본이
남는다」 같은 북 효과는 여기에 **없다**.

## §3 검산 — 노브를 끄면 옛 기록이 비트 재현되는가 (완료기준 4)

{_verify_table(verify)}

이것이 통과해야 §1의 「팔 D에서 판정이 이렇게 됐다」를 **가정의 몫**으로 읽을 수 있다 —
안 통과하면 리팩터가 숫자를 움직였다는 뜻이라 표 전체가 무효다.

⚠️ **검산은 옛 규약(차가운 절단) 위에서 돈다** — §1의 따뜻한 `oos_warm`과 **다른 질문의
답**이라 두 표의 OOS 수치를 나란히 인용하면 안 된다(CLAUDE.md 「두 IS/OOS 컨벤션」).
🚨 검산은 DOGE·LINK·LTC의 펀딩을 **비워** 옛 실행 당시의 데이터 상태를 복원한다
(WAN-292 백필 이후 같은 코드로도 그 CSV가 안 나온다 — WAN-312가 같은 자리에서 겪었다).
**§1 격자에는 그 복원을 쓰지 않는다**(오늘 좌표는 실데이터로 돈다).

## §4 ⚠️ 범위 · 인용 금지

- 🚨 **재진입이 이 널에 없다** — 채택 북은 재진입 ON(band, WAN-273)인데 매칭 널은 그것을
  구조적으로 표현하지 못한다(재진입 후보는 부모가 익절해야 생겨 「같은 개수를 맞춘다」가
  성립하지 않고, 표본마다 재시뮬이라 비용이 200배가 되며, 검산 상대가 전부 재진입-off다).
  **재진입이 들어갈 자리는 북 널이고 그게 다음 단계다.**
- ⚠️ **per-cell이라 채택 근거가 아니다**(WAN-341 「판단은 북에서만」) — 이 표는 **탐색·귀속**이다.
- ⚠️ **`pen_5bp`는 실측이 아니라 민감도다** — 큐 우선순위 실측은 틱·호가(WAN-98, **Canceled**) 소관.
- ⚠️ **차가운 `oos`는 §1에 없다**(구간마다 재탐지라 후보를 공유할 수 없어 비용이 두 배).
  차가운 축은 §3이 1h에서 낸다.
- ⚠️ **(b) 위치 널**(가짜 존 · WAN-248)은 **다른 질문**이고 이 표에 없다.
- **측정 전용** — `ConfluenceParams()`·`LeverageBookParams()` 기본값 불변 · 보수 팔은 전부
  옵트인(끄면 엔진이 비트 재현) · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _write(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def _merge_append(new: pd.DataFrame, path: Path) -> pd.DataFrame:
    """`--append`: 같은 키의 옛 행은 새 행으로 갈아끼우고 나머지는 보존한다.

    TF마다 따로 돌려야 할 만큼 무거운 격자라(15m 한 셀이 후보 생성만 220초) 이어 붙이는
    경로가 필요하다. 키를 덮어쓰는 것이 요점이다 — 안 그러면 같은 셀이 두 번 실리고
    유의 셀 수가 조용히 두 배가 된다.
    """
    if not path.exists():
        return new
    keys = ["symbol", "timeframe", "segment", "arm"]
    old = pd.read_csv(path)
    merged = pd.concat([old, new], ignore_index=True)
    return merged.drop_duplicates(subset=keys, keep="last").reset_index(drop=True)


def _summarize_from_csv() -> str:
    rows = rows_from_csv(NULL_CSV) if NULL_CSV.exists() else []
    verify = (
        [VerifyRow.model_validate(r) for r in pd.read_csv(VERIFY_CSV).to_dict(orient="records")]
        if VERIFY_CSV.exists()
        else []
    )
    return build_summary(rows, leave_one_out(rows), verify)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-350 보수 가정 위의 타이밍 매칭 널")
    parser.add_argument("--part", choices=("null", "verify", "summary"), default="null")
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--tf", default=",".join(TIMEFRAMES))
    parser.add_argument("--arms", default=",".join(ARM_ORDER))
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 붙인다(키 덮어쓰기)")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 다시 만든다")
    args = parser.parse_args(argv)

    if args.from_csv or args.part == "summary":
        SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_MD.write_text(_summarize_from_csv(), encoding="utf-8")
        print(f"[wan350] 요약 갱신: {SUMMARY_MD}")
        return 0

    if args.part == "verify":
        verify = run_verify(jobs=args.jobs)
        _write(verify_to_frame(verify), VERIFY_CSV)
        for row in verify:
            print(f"[wan350-verify] {row.check}: {row.status} ({row.note})")
        SUMMARY_MD.write_text(_summarize_from_csv(), encoding="utf-8")
        return 0 if all(r.status != "불일치" for r in verify) else 1

    arm_names = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    unknown = [a for a in arm_names if a not in ARMS_BY_NAME]
    if unknown:
        parser.error(f"알 수 없는 팔: {unknown} (지원: {', '.join(ARM_ORDER)})")
    rows = run_null(
        symbols=[s.strip() for s in args.symbols.split(",") if s.strip()],
        timeframes=[t.strip() for t in args.tf.split(",") if t.strip()],
        arm_names=arm_names,
        iterations=args.iterations,
        jobs=args.jobs,
    )
    frame = rows_to_frame(rows)
    if args.append:
        frame = _merge_append(frame, NULL_CSV)
    _write(frame, NULL_CSV)
    all_rows = rows_from_csv(NULL_CSV)
    _write(loo_to_frame(leave_one_out(all_rows)), LOO_CSV)
    SUMMARY_MD.write_text(_summarize_from_csv(), encoding="utf-8")
    print(f"[wan350] {len(rows)}행 산출 → {NULL_CSV} · 요약 {SUMMARY_MD}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
