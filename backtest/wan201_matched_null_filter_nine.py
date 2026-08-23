"""WAN-201: 채택 좌표(존폭 필터 1.28 × 9종목 × 6년)에서 매칭 널 재검 — 볼린저 축.

## 동기 — 사용자 지시 (2026-07-27, "그걸 무작위랑 돌려봐야지")

"우리 규칙이 같은 오더블록 존에 무작위로 진입한 것보다 나은가"(매칭 널 = 대조군 실험,
볼린저 무력화 축)를 지금 실제로 매매하는 완전한 채택 좌표에서 다시 묻는다. 축은
**존폭 필터(1.28 켜짐/꺼짐) × 유니버스(9종목) × 창(못 박은 6년)** 이고, 팔은 롱 하나다.

## 🚨 발견 — WAN-176 §2가 이미 「필터 켜짐 × 9종목 × 6년」이었다

WAN-201 이슈 본문은 「필터 켜짐 × 9종목」 판이 없다고 봤지만, **그건 사실이 아니다.**
WAN-176 §2의 널(`wan176_null.csv`)은 `wan151.run_cell` → `arm.params()`를 **핀 없이**
쓰는데, 그 값은 채택 기본값(`ConfluenceParams()`)의 `max_zone_width_atr=1.28`을 그대로
물려받는다(WAN-159가 필터를 채택 기본값으로 올린 뒤라 그렇다 — WAN-159 커밋이 WAN-176
커밋의 조상이다). WAN-176 자신의 검산(`wan176_verify.csv`의 `null-long`)이 옛 창 6종목
널을 **필터 켜짐 6종목인 `wan164_short_null.csv`와 비트 단위로 재현**하는 것이 그 증거다.

즉 이슈가 요구한 헤드라인(필터 1.28 × 9종목 × 6년 롱 축 매칭 널)은 **이미 계산돼 있다** —
`wan176_null.csv` + `wan176_summary.md` §2. 이 모듈은 그것을 **검산으로 비트 재현**해 발견을
못 박고(§verify), 실제로 **비어 있던 네 번째 모서리** = **필터 꺼짐 × 9종목 × 6년**을 낸다.
그래야 이슈가 원한 「어느 축(종목수·필터)이 유의 폭을 움직였나」를 2×2로 가를 수 있다:

| | 필터 꺼짐 | 필터 켜짐(1.28) |
| -- | -- | -- |
| 6종목 · 3년 | WAN-151 (`wan151_split_zone_null.csv`) | WAN-164 (`wan164_short_null.csv`) |
| 9종목 · 6년 | **이 모듈** (`wan201_matched_null.csv`) | WAN-176 §2 (`wan176_null.csv`) |

⚠️ 세로축(6→9종목)은 창(3년→6년)이 함께 움직여 순수 종목 효과가 아니다 — 요약이 이 혼입을
명시한다. **가로축(필터 off→on)은 같은 유니버스·창 위에서 갈리므로 깨끗하다** — 그게 이슈의
핵심 질문(WAN-164의 15m 롱 유의 강화가 9종목에서도 사는가)에 대한 직접 대조다.

## 왜 「필터 꺼짐 × 9종목」을 핀으로 만드나 (이슈의 「핀 금지」와의 관계)

이슈는 "핀 금지 — 채택 기본값(=필터 1.28)이 자동으로 따라와야 한다"고 했다. 그 지시는
「그 좌표가 곧 새 판」이라는 전제였는데, 그 판(필터 켜짐)은 WAN-176 §2로 이미 존재한다.
새로 필요한 것은 **필터를 끈** 모서리뿐이고, 끄려면 `max_zone_width_atr=None`을 **명시**해야
한다(안 그러면 채택 기본값 1.28로 돌아 「필터 끔」 라벨을 단 채 조용히 켜진 이중 필터가
된다 — WAN-159가 경계한 자리). 그래서 필터 값은 이 모듈의 **명시 축**이다:
- 필터 켜짐(검산 팔) = `arm.params()` 그대로(핀 없음, = 1.28) → `wan176_null.csv` 재현.
- 필터 꺼짐(새 모서리) = `max_zone_width_atr=None` 명시.

## 재사용 (새 파이프라인 없음, WAN-151/164/176 그대로)

- 매칭 널 기계 = `run_random_control_b_segment`(WAN-70), 무력화 축 = 볼린저
  (`deviation_filter=None`, WAN-124/145/151). 팔·풀·자·시드·반복수·행 모델·집계·판정·
  ETH leave-one-out은 전부 `wan151`에서 가져온다 — 움직인 것은 필터·유니버스·창뿐이다.
- 펀딩 대리(WAN-180 BTC 도너)는 **적용하지 않는다** — 널 계열(WAN-151/164/176 §2)이
  한 번도 적용한 적이 없고, `wan176_null.csv`(검산·대조 상대)와 펀딩 처리를 맞춰야 가로축
  대조가 깨끗하다. 신규 3종목은 펀딩 0(미반영)이고 요약이 그렇게 적는다(WAN-91 실측 영향
  ±0.1~2%p).

## 재현

```
uv run python -m backtest.wan201_matched_null_filter_nine --part null --tf 1h --jobs 6
uv run python -m backtest.wan201_matched_null_filter_nine --part null --tf 4h --jobs 6 --append
uv run python -m backtest.wan201_matched_null_filter_nine --part null --tf 15m --jobs 6 --append
uv run python -m backtest.wan201_matched_null_filter_nine --part verify --jobs 6
uv run python -m backtest.wan201_matched_null_filter_nine --part summary   # CSV에서 요약만
```

측정 전용 — 기본값·토대는 바꾸지 않는다. 유의 셀이 나와도 그 자체가 엣지 채택 근거가
아니다(§인용 금지 2종). 재활성화·재-베이스라인은 별개의 사용자 결정 이슈다.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest import wan151_split_zone_null as wan151
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import run_random_control_b_segment
from backtest.wan89_short_autopsy import ARMS_BY_NAME, Arm, _buy_hold
from backtest.wan176_nine_symbol_rebaseline import (
    DEFAULT_END,
    DEFAULT_START,
    NEW_SYMBOLS,
    NINE_SYMBOLS,
    OLD_END,
    OLD_START,
    OLD_SYMBOLS,
)
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
NULL_CSV = REPORTS_DIR / "wan201_matched_null.csv"
VERIFY_CSV = REPORTS_DIR / "wan201_verify.csv"
SUMMARY_MD = REPORTS_DIR / "wan201_matched_null_summary.md"

#: 검산·대조 상대 — 읽기 전용(절대 다시 쓰지 않는다). 전부 `wan151.NullRow` 스키마다.
WAN176_NULL_CSV = REPORTS_DIR / "wan176_null.csv"  # 필터 켜짐 × 9종목 × 6년
WAN164_NULL_CSV = REPORTS_DIR / "wan164_short_null.csv"  # 필터 켜짐 × 6종목 × 3년
WAN151_NULL_CSV = REPORTS_DIR / "wan151_split_zone_null.csv"  # 필터 꺼짐 × 6종목 × 3년

#: 작업 TF(WAN-182: 15m·1h·4h). 4h는 표본 미달이면 판정 게이트가 대조군으로 찍는다.
WORK_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")

#: 검산은 1h 한정으로 돌려 비용을 묶는다 — 15m 재현은 WAN-176 자신의 검산이 이미 덮는다
#: (`wan176_verify.csv`의 `null-long`이 옛 창 15m·1h를 함께 재현). 여기 1h는 **9종목 6년**
#: 켜짐 팔이 `wan176_null.csv`를 재현하는지를 직접 못 박아 「wan176 §2 = 필터 켜짐」을 증명한다.
VERIFY_TIMEFRAMES: tuple[str, ...] = ("1h",)

#: 필터 축의 두 값. `None` = 끄기(새 모서리) · `1.28` = 채택 기본값(검산 팔).
FILTER_OFF: float | None = None
FILTER_ON: float | None = ConfluenceParams().max_zone_width_atr  # = 1.28 (채택 기본값에서 읽는다)

#: 롱 축 하나(이슈 범위 — 숏은 WAN-145/164가 이미 (c)로 냈다).
LONG_ARM = wan151.LONG_ARM

#: 부트스트랩·자·렌즈·탐지 파라미터는 wan151에서 그대로 물려받는다(대조가 성립하려면
#: 필터 말고 다른 것이 움직이면 안 된다 — WAN-151 §시드 규약과 같은 이유).
BOOTSTRAP_ITERATIONS = wan151.BOOTSTRAP_ITERATIONS
BOOTSTRAP_SEED = wan151.BOOTSTRAP_SEED
ADOPTED_OB_PARAMS = OrderBlockParams()  # 채택 기본값(분리 존, WAN-149)
OFFICIAL_LENS = wan151.OFFICIAL_LENS
NOISE_TOLERANCE = 1e-9


def describe_engine() -> str:
    """이 리포트가 검정한 엔진의 지문(필터 축은 별도 열로 드러난다)."""
    return wan151.describe_engine()


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


# --------------------------------------------------------------------------- #
# 셀 실행 — wan151.run_cell을 필터 축으로 파라미터화한 것
# --------------------------------------------------------------------------- #


def real_params(arm: Arm, *, max_zone_width_atr: float | None) -> ConfluenceParams:
    """검정 대상 = 채택 기본값에서 **존폭 필터만** 축으로 덮어쓴 것.

    켜짐 팔은 `arm.params()`(= 채택 기본값 1.28)에 같은 값을 다시 얹어 항등이고, 꺼짐 팔은
    `None`으로 끈다. 밴드·게이트·오프셋·자·시드는 손대지 않는다.
    """
    return arm.params().model_copy(update={"max_zone_width_atr": max_zone_width_atr})


def pool_params(arm: Arm, *, max_zone_width_atr: float | None) -> ConfluenceParams:
    """널 풀 = 실제에서 **볼린저만 끈 것**(WAN-124/145/151과 같은 무력화 축)."""
    return real_params(arm, max_zone_width_atr=max_zone_width_atr).model_copy(
        update=wan151.NEUTRALIZED_POOL_UPDATES
    )


@dataclass(frozen=True)
class _Task:
    """fan-out 한 단위 = (심볼, TF, 필터값) — 워커가 자기 데이터를 자기가 로드한다."""

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    max_zone_width_atr: float | None
    iterations: int


def run_cell(task: _Task, *, log: bool = True) -> list[wan151.NullRow]:
    """한 (심볼, TF, 필터값)의 IS/OOS 롱 축 널을 낸다 — wan151.run_cell의 필터 파라미터판.

    존 탐지·풀·자·시드·행 모델은 wan151과 글자 그대로 같다. 다른 것은 `real_params`/
    `pool_params`가 필터 축을 받는다는 것뿐이다.
    """
    market = harness.load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []

    arm = ARMS_BY_NAME[LONG_ARM]
    real = real_params(arm, max_zone_width_atr=task.max_zone_width_atr)
    pool = pool_params(arm, max_zone_width_atr=task.max_zone_width_atr)
    cfg = arm.config(task.timeframe)

    rows: list[wan151.NullRow] = []
    for segment in harness.segments_for(oos=True):
        if segment.name not in wan151.SEGMENT_ORDER:
            continue  # 전 구간은 두 구간의 혼합이라 널에 새 정보를 주지 않는다.
        window = harness.slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        ob_result = harness.detect_order_blocks(window, ADOPTED_OB_PARAMS)
        buy_hold = _buy_hold(window.htf_df)
        result = run_random_control_b_segment(
            window.htf_df,
            window.df_1m,
            task.timeframe,
            symbol=task.symbol,
            segment="IS" if segment.name == harness.SEGMENT_IS else "OOS",
            gate=LONG_ARM,
            confluence_params=harness.pin_invalidation_cancel(real),
            backtest_config=cfg,
            order_block_result=ob_result,
            iterations=task.iterations,
            seed=BOOTSTRAP_SEED,
            funding_rates=window.funding_rates,
            pool_params=harness.pin_invalidation_cancel(pool),
        )
        row = wan151.NullRow(
            symbol=task.symbol,
            timeframe=task.timeframe,
            segment=segment.name,
            arm=LONG_ARM,
            fill=OFFICIAL_LENS,
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
            buy_hold=buy_hold,
        )
        rows.append(row)
        if log:
            filt = "off" if task.max_zone_width_atr is None else f"{task.max_zone_width_atr:g}"
            print(
                f"[wan201-null] {task.symbol} {task.timeframe} {segment.name} filter={filt}: "
                f"real={row.real_total_return:.4f} n={row.real_num_trades} "
                f"pool={row.pool_size} zones={row.zones} p={row.random_p_value}",
                flush=True,
            )
    return rows


def _run_task_logged(task: _Task) -> list[wan151.NullRow]:
    return run_cell(task, log=True)


def run_null(
    *,
    symbols: Sequence[str] = NINE_SYMBOLS,
    timeframes: Sequence[str] = WORK_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    max_zone_width_atr: float | None = FILTER_OFF,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> list[wan151.NullRow]:
    """(심볼 × TF) 격자를 한 필터값으로 돈다. `jobs`는 성능 노브다(WAN-121)."""
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            max_zone_width_atr=max_zone_width_atr,
            iterations=iterations,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_cell(task, log=log)]
    rows: list[wan151.NullRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


# --------------------------------------------------------------------------- #
# 검산 — 발견을 못 박는다 (필터 켜짐 = wan176, 필터 꺼짐/켜짐 6종목 = wan151/wan164)
# --------------------------------------------------------------------------- #


_NULL_NUMERIC = (
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


def _classify(rows_compared: int, max_abs_diff: float | None) -> tuple[str, str]:
    if rows_compared == 0:
        return "불일치", "비교된 행이 없다 — 키가 어긋났거나 산출이 비었다."
    if max_abs_diff is None or max_abs_diff == 0.0:
        return "일치", "차이 0 — 비트 단위 재현."
    if max_abs_diff < NOISE_TOLERANCE:
        return "잡음", f"최대 절대차 {max_abs_diff:.2e} — 부동소수 끝자리(메모리 원값 대 CSV 왕복)."
    return "불일치", "같은 키의 값이 다르다 — 배선이 어긋났다."


def _compare_null(ours: pd.DataFrame, reference: pd.DataFrame) -> tuple[int, float | None]:
    keys = ["symbol", "timeframe", "segment"]
    left = ours.set_index(keys).sort_index()
    right = reference.set_index(keys).sort_index()
    common = left.index.intersection(right.index)
    if common.empty:
        return 0, None
    max_diff = 0.0
    for col in _NULL_NUMERIC:
        a = pd.to_numeric(left.loc[common, col], errors="coerce")
        b = pd.to_numeric(right.loc[common, col], errors="coerce")
        both_nan = a.isna() & b.isna()
        diff = (a - b).abs().mask(both_nan, 0.0)
        if diff.isna().any():
            return int(len(common)), float("inf")
        if not diff.empty:
            max_diff = max(max_diff, float(diff.max()))
    return int(len(common)), max_diff


@dataclass(frozen=True)
class VerifyRow:
    check: str
    reference: str
    rows_compared: int
    max_abs_diff: float | None
    status: str
    note: str


def _verify_one(
    *,
    check: str,
    reference_csv: Path,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    start: str,
    end: str,
    max_zone_width_atr: float | None,
    jobs: int,
) -> VerifyRow:
    rows = run_null(
        symbols=symbols,
        timeframes=timeframes,
        start=start,
        end=end,
        max_zone_width_atr=max_zone_width_atr,
        jobs=jobs,
        log=False,
    )
    ours = wan151.rows_to_frame(rows)
    reference = pd.read_csv(reference_csv)
    reference = reference[reference["arm"] == LONG_ARM].reset_index(drop=True)
    reference = reference[reference["timeframe"].isin(list(timeframes))].reset_index(drop=True)
    compared, diff = _compare_null(ours, reference)
    status, note = _classify(compared, diff)
    expected = len(reference)
    if compared != expected and status != "불일치":
        status, note = "불일치", f"비교 행 {compared} ≠ 기준 행 {expected}."
    return VerifyRow(
        check=check,
        reference=str(reference_csv),
        rows_compared=compared,
        max_abs_diff=diff,
        status=status,
        note=note,
    )


def run_verify(*, jobs: int = 1) -> list[VerifyRow]:
    """세 검산 — 발견(wan176 §2 = 필터 켜짐)을 비트 단위로 증명한다."""
    return [
        # 발견의 직접 증명: 필터 켜짐 × 9종목 × 6년(1h) ≡ wan176_null.csv 1h 롱 행.
        _verify_one(
            check="filter-on-9sym-vs-wan176",
            reference_csv=WAN176_NULL_CSV,
            symbols=NINE_SYMBOLS,
            timeframes=VERIFY_TIMEFRAMES,
            start=DEFAULT_START,
            end=DEFAULT_END,
            max_zone_width_atr=FILTER_ON,
            jobs=jobs,
        ),
        # 필터 꺼짐 기계가 옳은가: 필터 꺼짐 × 6종목 × 3년(1h) ≡ wan151(필터 꺼진 시절).
        _verify_one(
            check="filter-off-6sym-vs-wan151",
            reference_csv=WAN151_NULL_CSV,
            symbols=OLD_SYMBOLS,
            timeframes=VERIFY_TIMEFRAMES,
            start=OLD_START,
            end=OLD_END,
            max_zone_width_atr=FILTER_OFF,
            jobs=jobs,
        ),
        # 필터 켜짐 기계가 옳은가: 필터 켜짐 × 6종목 × 3년(1h) ≡ wan164 롱 행.
        _verify_one(
            check="filter-on-6sym-vs-wan164",
            reference_csv=WAN164_NULL_CSV,
            symbols=OLD_SYMBOLS,
            timeframes=VERIFY_TIMEFRAMES,
            start=OLD_START,
            end=OLD_END,
            max_zone_width_atr=FILTER_ON,
            jobs=jobs,
        ),
    ]


def verify_rows_from_csv(path: Path) -> list[VerifyRow]:
    frame = pd.read_csv(path)
    out: list[VerifyRow] = []
    for record in frame.to_dict(orient="records"):
        clean = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in record.items()}
        if clean.get("note") is None:
            clean["note"] = ""
        out.append(VerifyRow(**clean))  # type: ignore[arg-type]
    return out


# --------------------------------------------------------------------------- #
# 2×2 분해 · 판정
# --------------------------------------------------------------------------- #


def _load_null(path: Path) -> list[wan151.NullRow]:
    """저장된 널 CSV를 `wan151.NullRow`로 되읽는다(롱 축만).

    ⚠️ `wan164_short_null.csv`는 `combine_obs`·`zones` 열이 없다(그 시절 행 모델이 달랐다).
    WAN-176 `old_null_rows`와 같은 규약으로 없는 열은 기본값(분리·0)으로 채운다 — 이 두 열은
    널 판정(유의 셀 수)에 안 쓰이므로 대조가 왜곡되지 않는다.
    """
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    view = frame[frame["arm"] == LONG_ARM]
    rows: list[wan151.NullRow] = []
    for record in view.to_dict(orient="records"):
        clean: dict[str, object] = {
            k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in record.items()
        }
        clean.setdefault("combine_obs", False)
        clean.setdefault("zones", 0)
        rows.append(wan151.NullRow.model_validate(clean))
    return rows


def verdict_all_tfs(rows: Sequence[wan151.NullRow]) -> str:
    """§1 판정 — wan151.verdict과 같은 자·같은 문장이되 TF 분해에 4h를 포함한다.

    `wan151.verdict`은 `DEFAULT_TIMEFRAMES=(15m,1h)`만 세어 괄호 분해에서 4h가 빠지고,
    그러면 「유의 N개(15m …·1h …)」의 괄호 합이 N과 안 맞아 보인다(이 모듈은 4h도 돈다).
    sig/total은 전 TF에서 세고, 분해만 `WORK_TIMEFRAMES`로 넓힌다.
    """
    sig, total = wan151.significance_counts(rows, arm=LONG_ARM)
    if total == 0:
        return (
            f"`{LONG_ARM}`: **⚠️ 판정 불가** — 거래 {wan151.MIN_TRADES_FOR_VERDICT}건 이상인 "
            "유효 셀이 하나도 없다(표본 부족)."
        )
    parts: list[str] = []
    for tf in WORK_TIMEFRAMES:
        s, t = wan151.significance_counts([r for r in rows if r.timeframe == tf], arm=LONG_ARM)
        if t:
            parts.append(f"{tf} {s}/{t}")
    tf_note = " · ".join(parts)
    if sig == 0:
        head = "**(b) 무작위와 구분되지 않는다**"
    elif sig == total:
        head = "**(a) 무작위와 구분된다**"
    else:
        head = "**(c) 일부 셀에만 유의성이 있다 — TF·구간에 갈린다**"
    return f"`{LONG_ARM}`: 유효 셀 {total}개 중 유의 {sig}개({tf_note}) → {head}"


def _counts(rows: Sequence[wan151.NullRow], *, timeframes: Sequence[str]) -> str:
    scoped = [r for r in rows if r.timeframe in timeframes]
    sig, total = wan151.significance_counts(scoped, arm=LONG_ARM)
    by_tf = {
        tf: wan151.significance_counts([r for r in scoped if r.timeframe == tf], arm=LONG_ARM)
        for tf in timeframes
    }
    tf_note = " · ".join(f"{tf} {s}/{t}" for tf, (s, t) in by_tf.items() if t)
    return f"유의 {sig}/{total}" + (f" ({tf_note})" if tf_note else "")


def decomposition_table(
    *,
    off9: Sequence[wan151.NullRow],
    on9: Sequence[wan151.NullRow],
    off6: Sequence[wan151.NullRow],
    on6: Sequence[wan151.NullRow],
) -> list[str]:
    """2×2 표 — 셀은 「유의/유효 (TF 분해)」. 비교 가능한 공통 TF(15m·1h)만 센다.

    (WAN-176 §2·WAN-151·WAN-164 널은 15m·1h만 있고 4h가 없으므로, 4열 대조는 공통 TF로
    맞춘다. 새 모서리의 4h는 §새-모서리 표에서 따로 게이트와 함께 낸다.)
    """
    common = ("15m", "1h")
    rows = [
        ("6종목 · 3년", _counts(off6, timeframes=common), _counts(on6, timeframes=common)),
        ("9종목 · 6년", _counts(off9, timeframes=common), _counts(on9, timeframes=common)),
    ]
    return [
        "| 유니버스 · 창 | 필터 꺼짐 | 필터 켜짐(1.28) |",
        "| -- | -- | -- |",
        *(f"| {a} | {b} | {c} |" for a, b, c in rows),
    ]


def axis_decomposition_lines(
    *,
    off9: Sequence[wan151.NullRow],
    on9: Sequence[wan151.NullRow],
    off6: Sequence[wan151.NullRow],
    on6: Sequence[wan151.NullRow],
) -> list[str]:
    """두 축의 이동을 문장으로 — 어느 축이 유의 폭을 움직였나(완료기준 2)."""
    common = ("15m", "1h")

    def sig_total(rows: Sequence[wan151.NullRow]) -> tuple[int, int]:
        scoped = [r for r in rows if r.timeframe in common]
        return wan151.significance_counts(scoped, arm=LONG_ARM)

    s_off9, t_off9 = sig_total(off9)
    s_on9, t_on9 = sig_total(on9)
    s_off6, t_off6 = sig_total(off6)
    s_on6, t_on6 = sig_total(on6)
    return [
        "- **필터 축(같은 유니버스·창 위 — 깨끗한 대조)**:",
        f"  - 9종목·6년: 꺼짐 {s_off9}/{t_off9} → 켜짐 {s_on9}/{t_on9} "
        f"({'강화' if s_on9 > s_off9 else '약화' if s_on9 < s_off9 else '동일'})",
        f"  - 6종목·3년: 꺼짐 {s_off6}/{t_off6} → 켜짐 {s_on6}/{t_on6} "
        f"({'강화' if s_on6 > s_off6 else '약화' if s_on6 < s_off6 else '동일'}) "
        "— WAN-164가 본 「필터가 15m 롱 유의를 강화」",
        "- **유니버스·창 축(6종목3년 → 9종목6년 — 창이 함께 움직여 혼입)**:",
        f"  - 필터 꺼짐: {s_off6}/{t_off6} → {s_off9}/{t_off9}",
        f"  - 필터 켜짐: {s_on6}/{t_on6} → {s_on9}/{t_on9}",
        "  - ⚠️ 세로 이동은 종목수(6→9)와 창(3년→6년)이 뒤섞인 값이다 — 순수 종목 효과가 "
        "아니다(WAN-111은 창을 못 박고 종목만 넓혀 희석을 봤다).",
    ]


def eth_leave_one_out_lines(rows: Sequence[wan151.NullRow]) -> list[str]:
    """완료기준 3(a) — ETH 하나 빼면 심볼평균 부호가 유지되는가(전 TF·구간)."""
    lines: list[str] = []
    for tf in WORK_TIMEFRAMES:
        for seg in wan151.SEGMENT_ORDER:
            for line in wan151.eth_dependence(
                [r for r in rows if r.timeframe == tf], arm=LONG_ARM, segment=seg
            ):
                lines.append(line)
    return lines or ["- (행 없음)"]


def four_h_gate_lines(rows: Sequence[wan151.NullRow]) -> list[str]:
    """새 모서리의 4h를 표본 게이트와 함께 낸다 — 미달이면 코드가 판정 불가를 찍는다."""
    lines: list[str] = []
    for seg in wan151.SEGMENT_ORDER:
        scoped = [r for r in rows if r.timeframe == "4h" and r.segment == seg]
        if not scoped:
            lines.append(f"- `4h` {seg}: 행 없음")
            continue
        per_symbol = sum(r.real_num_trades for r in scoped) / len(scoped)
        eligible = wan151.eligible_rows(scoped, arm=LONG_ARM)
        sig = sum(1 for r in eligible if wan151.is_significant(r))
        if per_symbol < wan151.MIN_TRADES_FOR_VERDICT:
            lines.append(
                f"- `4h` {seg}: 심볼당 {per_symbol:.1f}거래 — WAN-84 유효 기준 "
                f"{wan151.MIN_TRADES_FOR_VERDICT}건 **미달** → ⚠️ 판정 불가(대조군)"
            )
        else:
            lines.append(
                f"- `4h` {seg}: 심볼당 {per_symbol:.1f}거래 — 유효 기준 충족 → "
                f"유의 {sig}/{len(eligible)}"
            )
    return lines


def new_symbol_note(rows: Sequence[wan151.NullRow]) -> str:
    """신규 3종목 펀딩 미반영(0)을 드러낸다 — 널 계열은 대리를 얹지 않는다."""
    new_norm = {harness.normalize_symbol(s) for s in NEW_SYMBOLS}
    present = sorted({_short(r.symbol) for r in rows if r.symbol in new_norm})
    return (
        f"신규 3종목({', '.join(present) or '없음'})은 펀딩 데이터 0행 = **펀딩 미반영**이다. "
        "이 널 계열(WAN-151/164/176 §2)은 WAN-180 대리(BTC 도너)를 얹지 않는다 — 대조 상대인 "
        "`wan176_null.csv`와 펀딩 처리를 맞춰야 필터 축 대조가 깨끗하다(WAN-91 실측 영향 "
        "±0.1~2%p)."
    )


# --------------------------------------------------------------------------- #
# 요약 렌더
# --------------------------------------------------------------------------- #


def _verify_frame_for_render(verify_rows: Sequence[VerifyRow]) -> pd.DataFrame:
    frame = pd.DataFrame([vars(r) for r in verify_rows])
    if "max_abs_diff" in frame.columns:
        frame["max_abs_diff"] = [
            "—" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{float(v):.2e}"
            for v in frame["max_abs_diff"]
        ]
    return frame


def _md_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "행이 없다."
    headers = list(frame.columns)
    lines = [
        "| " + " | ".join(str(h) for h in headers) + " |",
        "| " + " | ".join("--" for _ in headers) + " |",
    ]
    for _, record in frame.iterrows():
        lines.append("| " + " | ".join(str(record[h]) for h in headers) + " |")
    return "\n".join(lines)


def build_summary_markdown(
    *,
    off9: Sequence[wan151.NullRow],
    on9: Sequence[wan151.NullRow],
    off6: Sequence[wan151.NullRow],
    on6: Sequence[wan151.NullRow],
    verify_rows: Sequence[VerifyRow],
) -> str:
    summary = wan151.arm_summary(list(off9))
    view = summary[list(wan151._SUMMARY_VIEW)] if not summary.empty else summary
    lines: list[str] = [
        "# WAN-201 — 채택 좌표(존폭 필터 × 9종목 × 6년)에서 매칭 널 (롱 축 · 볼린저 무력화)",
        "",
        f"창을 **{DEFAULT_START} ~ {DEFAULT_END}** 로 못 박은 **9종목**(기존 6 + "
        "DOGE·LINK·LTC) × 작업 TF(15m·1h·4h) × IS/OOS 매칭 널. 렌즈 `baseline` 단독"
        "(WAN-128), 무력화 축 **볼린저**(WAN-124/145/151), 팔 **롱 축 단독**.",
        "",
        "## 🚨 발견 — WAN-176 §2가 이미 「필터 켜짐 × 9종목 × 6년」이었다",
        "",
        "WAN-201 이슈 본문은 「필터 켜짐 × 9종목」 판이 없다고 봤지만 사실이 아니다. "
        "`wan176_null.csv`는 `arm.params()`를 **핀 없이** 써 채택 기본값 "
        f"`max_zone_width_atr={FILTER_ON:g}`(WAN-159)을 그대로 물려받는다. WAN-176 자신의 "
        "검산이 그 널을 필터 켜짐 6종목인 `wan164_short_null.csv`와 비트 재현하는 것이 증거다. "
        "따라서 이슈의 헤드라인은 **이미 계산돼 있고**(= WAN-176 §2), 비어 있던 모서리는 "
        "**필터 꺼짐 × 9종목 × 6년** 하나였다 — 이 리포트가 그것을 낸다.",
        "",
        "> ⚠️ 이슈의 검산 요구(「필터 끄면 WAN-176 §2 비트 재현」)는 전제가 뒤집혀 성립하지 "
        "않는다 — WAN-176 §2는 필터 **켜짐**이다. 대신 §검산이 **필터 켜짐 9종목이 "
        "`wan176_null.csv`를 재현**함을 못 박아 그 발견을 증명한다.",
        "",
        "## 이 리포트가 검정한 엔진",
        "",
        f"**지금 채택된 기본값 그대로** — `{describe_engine()}`. 필터만 축으로 켜고 끈다. "
        "측정 전용이며 기본값·토대는 바꾸지 않았다.",
        "",
        f"> {new_symbol_note(off9)}",
        "",
        "## §1 새 모서리 — 필터 꺼짐 × 9종목 × 6년 (TF × 구간 요약)",
        "",
        "`real_mean` = 심볼평균 실제 수익 / `ex_eth_mean` = ETH 제외 평균 / "
        "`significant`/`eligible` = 유의 셀 / 유효 셀(거래 "
        f"{wan151.MIN_TRADES_FOR_VERDICT}건 이상) / `zones` = 구간 탐지 존 수.",
        "",
        wan151._md_table(wan151._rounded(view)) if not summary.empty else "행이 없다.",
        "",
        "### 판정 (필터 꺼짐 × 9종목)",
        "",
        verdict_all_tfs(list(off9)),
        "",
        "### 4h 표본 게이트 (WAN-143/176 재사용)",
        "",
        *four_h_gate_lines(off9),
        "",
        "## §2 2×2 분해 — 어느 축이 유의 폭을 움직였나 (완료기준 2)",
        "",
        "셀은 「유의/유효 (TF 분해)」이고 **공통 TF(15m·1h)만** 센다(대조 상대 널이 4h를 "
        "안 가졌다). 필터 켜짐 팔은 남의 표에서 읽는다(같은 자·같은 함수).",
        "",
        *decomposition_table(off9=off9, on9=on9, off6=off6, on6=on6),
        "",
        *axis_decomposition_lines(off9=off9, on9=on9, off6=off6, on6=on6),
        "",
        "출처: 필터 꺼짐×9 = 이 실행 · 필터 켜짐×9 = `wan176_null.csv`(WAN-176 §2) · "
        "필터 꺼짐×6 = `wan151_split_zone_null.csv`(WAN-151) · 필터 켜짐×6 = "
        "`wan164_short_null.csv`(WAN-164).",
        "",
        "## §3 셀별 결과 (필터 꺼짐 × 9종목)",
        "",
        wan151.cell_table(list(off9), arm=LONG_ARM),
        "",
        "## §4 인용 금지 2종 (완료기준 3)",
        "",
        "### (a) ETH leave-one-out — 유의성이 ETH 하나에 기대는가",
        "",
        *eth_leave_one_out_lines(off9),
        "",
        "### (b) 「선별」과 「가격」을 못 가른다",
        "",
        "이 널은 풀이 **존 근단 가격**이고 실제가 **밴드가**라, 실제가 널을 이겨도 그것이 "
        '"볼린저가 좋은 셋업을 고른다(선별)"인지 "더 좋은 가격에 넣는다(가격)"인지 '
        "구분하지 못한다 — **WAN-131 소관**이다. 유의 셀이 나와도 「엣지 찾았다」로 인용 금지.",
        "",
        "## §5 검산 — 발견을 비트 단위로 못 박는다",
        "",
        _md_table(_verify_frame_for_render(verify_rows)),
        "",
        "`filter-on-9sym-vs-wan176`가 「일치/잡음」이면 **WAN-176 §2 = 필터 켜짐**이 증명된다"
        "(발견의 직접 증거). 나머지 둘은 필터 꺼짐/켜짐 기계가 옛 창 6종목에서 옛 답"
        "(WAN-151/164)을 내는지 확인한다. 「잡음」은 부동소수 끝자리"
        f"(<{NOISE_TOLERANCE:g})로 실질 일치다(WAN-151/161 선례).",
        "",
        "## 결론",
        "",
        "- **측정 전용** — 기본값·토대 불변, 실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`) "
        "유지. 유의 셀은 엣지 채택 근거가 아니다(§4 인용 금지 2종).",
        "- 「엣지 없음」 계열(WAN-84/88/111/114/124/151)과 이 표는 **같은 질문의 새 좌표**다 — "
        "필터를 켜고 9종목으로 넓혀도 답이 (a)/(b)/(c) 중 무엇인지를 위 판정이 낸다.",
        "- 필터 축(off→on)은 같은 유니버스·창 위에서 갈리므로 깨끗하고, 유니버스·창 축은 "
        "창 혼입이 있어 방향만 읽는다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

PARTS: tuple[str, ...] = ("null", "verify", "summary", "all")


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _run_summary() -> None:
    off9 = _load_null(NULL_CSV)
    on9 = _load_null(WAN176_NULL_CSV)
    off6 = _load_null(WAN151_NULL_CSV)
    on6 = _load_null(WAN164_NULL_CSV)
    verify_rows = verify_rows_from_csv(VERIFY_CSV) if VERIFY_CSV.exists() else []
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(
        build_summary_markdown(off9=off9, on9=on9, off6=off6, on6=on6, verify_rows=verify_rows),
        encoding="utf-8",
    )
    print(f"[wan201] summary → {SUMMARY_MD}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-201 존폭 필터 × 9종목 매칭 널")
    parser.add_argument("--part", type=str, default="all", choices=PARTS)
    parser.add_argument("--tf", type=str, default=None, help="null 파트 한정 TF 목록(콤마)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--append", action="store_true", help="null 파트를 TF별로 나눠 돌릴 때 CSV에 덧붙인다."
    )
    args = parser.parse_args(argv)

    part = str(args.part)
    jobs = int(args.jobs)

    if part in ("null", "all"):
        timeframes = (
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
            if args.tf
            else WORK_TIMEFRAMES
        )
        rows = run_null(
            timeframes=timeframes,
            start=args.start,
            end=args.end,
            max_zone_width_atr=FILTER_OFF,
            iterations=int(args.iterations),
            jobs=jobs,
        )
        frame = wan151.rows_to_frame(rows)
        if args.append and NULL_CSV.exists():
            frame = pd.concat([pd.read_csv(NULL_CSV), frame], ignore_index=True)
        _write(frame, NULL_CSV)
        print(f"[wan201] null {len(frame)}행 → {NULL_CSV}")

    if part in ("verify", "all"):
        verify_rows = run_verify(jobs=jobs)
        _write(pd.DataFrame([vars(r) for r in verify_rows]), VERIFY_CSV)
        for row in verify_rows:
            print(f"[wan201-verify] {row.check}: {row.status} — {row.note}")

    if part in ("summary", "all"):
        _run_summary()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
