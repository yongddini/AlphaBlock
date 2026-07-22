"""WAN-124 2단: 게이트 제거 엔진의 매칭 널 — 무력화 축을 옮겨야 했다.

[WAN-123](../docs/decisions/wan123.md)이 재탭 RSI 게이트를 빼
(`rsi_gate_mode="unconditional"`) 채택 기본값을 옮긴 뒤, WAN-84/88/111의 「엣지 없음」이
게이트 off에서도 성립하는지 다시 검정한다. 이슈(WAN-124)의 지시는 "WAN-88식 매칭 널을 새
기본값으로 다시 돌린다"였다.

## 그대로 다시 돌릴 수 없다 — 널의 대조군이 곧 새 채택 기본값이다

WAN-70/88의 매칭 널은 **RSI 게이트를 무력화한 풀**(`rsi_oversold=100`/`rsi_overbought=0`
→ RSI가 워밍업만 끝나면 항상 통과)에서 방향·시각대를 맞춰 뽑은 표본을 대조군으로 썼다.
그런데 게이트가 없는 엔진에서는 시뮬레이터가 RSI를 **읽지도 않는다**
(`backtest/substep.py`가 `rsi_gate_mode == "unconditional"`에서 단락 평가한다) — 그래서 그
두 오버라이드는 **아무것도 하지 않고**, 무력화 풀이 실제 후보 집합과 **글자 그대로
같아진다**. 실측(BTC 1h, 2025-01-01~2025-07-01):

| `rsi_gate_mode` | 실제 후보 | 무력화 풀 | 같은가 |
| -- | --: | --: | -- |
| `first_tap_free`(WAN-122까지) | 33 | 52 | 아니오 |
| `unconditional`(WAN-123 채택) | 52 | 52 | **예** |

읽는 법이 두 가지인데 **둘 다 같은 곳을 가리킨다**:

1. **널이 퇴화한다.** 풀 = 실제이므로 부트스트랩은 "엔진이 무작위를 이기는가"가 아니라
   "단일 포지션 시퀀싱이 뽑은 순서가 같은 후보들의 무작위 표본을 이기는가"를 잰다. 진입
   규칙은 양쪽에 똑같이 들어 있어 **정의상 검정 대상이 아니다**. 그런데도 p값은 멀쩡히
   나오고, 심지어 **기대했던 답**("엣지 없음 유지")을 낸다 — 틀린 이유로 맞는 답을 주는,
   이 저장소가 반복해 겪은 조용한 실패(WAN-91·WAN-95·WAN-100·WAN-112)의 정확한 재현이다.
2. **질문은 이미 답이 나와 있다.** 위 표의 두 번째 열을 보면 **WAN-88이 대조군으로 쓰던
   바로 그 풀이 지금의 채택 기본값**이다(52 = 52). WAN-88은 게이트가 고른 33건이 그 52건
   유니버스에서 무작위로 뽑은 표본과 **구분되지 않는다**고 결론냈다. 게이트를 뺀다는 것은
   그 대조군 자체를 매매한다는 뜻이므로, 「엣지 없음」은 게이트 off에서 **재검정이 아니라
   전제**다.

## 그래서 무력화 축을 옮긴다 — 남은 선별 규칙은 볼린저뿐이다

게이트를 뺀 뒤 진입 규칙에서 **어느 셋업을 취할지 고르는** 부품은 볼린저 하나다
(`deviation_filter` — 밴드가 존보다 불리하면 진입 자체를 건너뛴다, WAN-75 규칙 3). 나머지
(오더블록 존·재탭·지정가·고정 1.5R)는 "고르는" 규칙이 아니다. 그래서 이 모듈의 널은
**볼린저를 무력화한 풀**(`deviation_filter=None` = 존 근단 지정가에 무조건 진입 =
WAN-114 사다리의 존-단독 가격)에서 방향·시각대를 맞춰 뽑는다.

물음: **볼린저 규칙이 같은 존 유니버스에서 무작위로 뽑은 같은 크기·같은 방향·같은 시각대
표본을 이기는가.** WAN-70/88의 물음("RSI 타이밍이 …")과 **같은 모양**이고 무력화 대상만
살아 있는 부품으로 바뀌었다.

⚠️ **이 널은 「선별」과 「가격」을 가르지 못한다** — WAN-114/115/119가 남긴 그 질문이 여기서도
그대로다. 볼린저는 셋업을 고르는 동시에 **진입가를 옮긴다**(존 근단 → 밴드가). 풀은 존
근단 가격이라, 실제가 널을 이겨도 그것이 "볼린저가 좋은 셋업을 고른다"인지 "볼린저가 더
좋은 가격에 넣는다"인지 이 표는 구분하지 않는다. WAN-114 §가격-대-선별 · WAN-115(같은
셋업에 같은 빈도로 들어가는데 수익만 −4.88%p)가 **가격 쪽**을 가리키므로, 유의성이 나오면
**「선별 엣지 확인」이 아니라 「가격 효과 재확인」으로 먼저 읽어야 한다.**

## 축 · 창 · 자

* **심볼 6개**(WAN-111) · **TF 2개**(15m·1h, WAN-107 공동 작업 TF) · **구간** IS/OOS.
* **렌즈 2개**: `baseline`(공식, WAN-104) · `pen_5bp`(민감도) — **WAN-88의 널과 같은 축**.
  ⚠️ 이슈(WAN-124)는 널에도 3렌즈를 요구했지만 스트레스 렌즈(`pen_5bp_drop_50`)는 **뺀다**
  — 이유는 아래 「스트레스 렌즈」. 3렌즈 요구는 **3단 표**(`wan123_fill_conservatism.py`)가
  채운다. 거기가 그 렌즈가 실제로 뜻을 갖는 자리다(부트스트랩이 없어 난수가 하나뿐이다).
  `--lenses`로 명시하면 여기서도 돌릴 수 있다(기본이 아닐 뿐이다).
* **창**: WAN-111/114/115/119/120과 **같은 못 박은 창**(2023-07-14~2026-07-15). `--years N`은
  마지막 봉 기준으로 미끄러진다(CLAUDE.md).
* **구간 분할**: harness(`slice_market`, 시간 2/3)를 쓴다 — WAN-88의 `_split_bars`(봉 수
  2/3 + IS 꼬리 워밍업 차용)와 **다른 경계**다. 이 표의 실제 수익 행이
  `wan123_fill_conservatism.csv`의 `unconditional` 행과 **비트 단위로 일치**하는 것이 두
  모듈의 상호 검산인데, 그쪽은 harness 격자라 경계를 맞춰야 한다. ⚠️ 그 대가로 이 표는
  **WAN-88 표와 셀 대 셀로 맞댈 수 없다**(엔진도 다르므로 어차피 불가능하다).
* **자**: 유효 셀 = 실제 거래 `MIN_TRADES_FOR_VERDICT`건 이상, 유의 = p≤0.05 **이면서**
  실제>무작위평균 — WAN-70/84/88과 **같은 자**다(다른 자로 재면 "판정이 바뀌었다"와 "자를
  바꿨다"를 구분할 수 없다).

## 스트레스 렌즈(`pen_5bp_drop_50`)를 널에서 빼는 이유 — 이슈 요구와 다른 점

WAN-88이 그 렌즈를 널에서 **아예 제외**한 근거가 여기서도 그대로다: 탈락 추첨은 시드마다
결과가 크게 널뛰는데(같은 셀이 −11%~+30%) 매칭 널은 그 위에 **부트스트랩이라는 두 번째
난수**를 얹는다. 두 잡음이 겹치면 p값은 "엔진이 뭘 하는가"가 아니라 **"시드를 어떻게
뽑았는가"** 를 잰다 — 임의 모수(탈락률 50%)가 판정을 좌우하게 두지 않는다는 WAN-97의 원칙.

여기에 실측 근거가 하나 더 붙는다: 그 렌즈는 시드 5개를 도므로 **널 격자 계산의 71%**
(7 렌즈-시드 중 5)를 혼자 먹는데, 그렇게 얻은 p값을 **판정에서는 쓰지 않는다**. 판정에
쓰지 않을 숫자를 위해 격자를 3.5배로 늘리는 것은 값을 주지 않는다.

**대신 3단 표가 3렌즈를 전부 낸다**(`wan123_fill_conservatism.py`) — 그쪽은 부트스트랩이
없어 난수가 **탈락 추첨 하나뿐**이라 시드 5개 평균이 뜻을 갖는다. 즉 이슈의 「3렌즈」 요구는
**그 렌즈가 뜻을 갖는 표에서** 충족된다.

## 재현

```
uv run python -m backtest.wan123_matched_null              # 격자 재실행(길다)
uv run python -m backtest.wan123_matched_null --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    LEGACY_OB_PARAMS,
    SEGMENT_IS,
    SEGMENT_OOS,
    FillPreset,
    build_config,
    build_params,
    detect_order_blocks,
    fill_preset,
    iter_seeds,
    load_market_data,
    normalize_symbol,
    pin_band_bar,
    segments_for,
    slice_market,
)
from backtest.run import parse_date_ms
from backtest.wan70_random_control_b import run_random_control_b_segment
from strategy.models import ConfluenceParams

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

#: 널을 돌릴 렌즈 — **WAN-88의 `NULL_FILL_LEVELS`와 같은 축**(공식 + 민감도).
#: 스트레스 렌즈를 뺀 이유는 모듈 docstring 「스트레스 렌즈」 — 시드 잡음 × 부트스트랩
#: 잡음이라 p값이 엔진이 아니라 시드를 재고, 판정에 쓰지도 않으면서 격자의 71%를 먹는다.
#: 3렌즈는 3단 표(`wan123_fill_conservatism.py`)가 낸다.
LENS_NAMES: tuple[str, ...] = ("baseline", "pen_5bp")

OFFICIAL_LENS = "baseline"

#: 널에서 뺀 렌즈. `--lenses`로 명시하면 돌릴 수는 있어서 렌더가 이 이름을 안다.
STRESS_LENS = "pen_5bp_drop_50"

#: WAN-111/114/115/119/120과 같은 못 박은 창.
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 이 리포트는 IS/OOS만 낸다(이슈 완료기준). 전 구간은 둘의 혼합이라 널에 새 정보를 주지
#: 않으면서 격자 시간을 50% 늘린다.
SEGMENT_ORDER: tuple[str, ...] = (SEGMENT_IS, SEGMENT_OOS)

#: 부트스트랩 반복 수 — WAN-70/84/88과 같은 값(같은 자로 재야 판정 이동을 읽을 수 있다).
BOOTSTRAP_ITERATIONS = 200

#: 판정에 포함할 셀의 최소 실제 거래 수 — WAN-70/84/88과 같은 자.
#: (`tests/test_wan123_matched_null.py`가 WAN-88 값과의 일치를 고정한다.)
MIN_TRADES_FOR_VERDICT = 20

#: 부트스트랩 난수 시드(이 리포트 고유).
BOOTSTRAP_SEED = 124

#: 널 풀의 무력화 축 — **볼린저를 끈다**(모듈 docstring 「무력화 축을 옮긴다」).
#: 채택 기본값 대비 **덮어쓸 필드**로 정의하는 이유는 WAN-114 사다리와 같다: 값을 상수로
#: 박아 두면 기본값이 재-베이스라인으로 움직일 때 이 리포트만 혼자 옛 엔진을 돈다.
NEUTRALIZED_POOL_UPDATES: dict[str, object] = {"deviation_filter": None}

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CSV = REPORTS_DIR / "wan123_matched_null.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan123_matched_null_summary.md"


# --------------------------------------------------------------------------- #
# 파라미터
# --------------------------------------------------------------------------- #


def adopted_params(fill: FillPreset, seed: int) -> ConfluenceParams:
    """검정 대상 = **지금 채택된 기본값 그대로** + 렌즈만 얹은 것.

    WAN-88과 같은 선택이다(그 모듈 docstring 「검정 엔진」): "지금 채택된 것에 엣지가
    있는가"를 묻는 리포트는 기본값을 고정하지 **않는다** — 기본값이 움직이면 이 수치는
    낡은 것이 되어야 맞다. 고정하는 쪽은 결론을 옛 엔진 수치에 박아 둔 리포트다
    (`harness.LEGACY_RSI_GATE_MODE`).

    ⚠️ **밴드 표본(`band_bar`)만은 그 원칙의 예외로 당시 값(`tap`)에 고정한다** —
    WAN-132가 기본값을 `intrabar_live`로 옮겼는데, 이 모듈의 실제 팔이 `wan123_fill_
    conservatism`의 채택 팔과 **48셀 차이 0.00e+00**으로 맞물리는 것이 두 모듈의 상호
    검산이고 그쪽은 `wan111` CSV와의 비트 일치 때문에 `tap`에 묶여 있다. 즉 여기만
    새 밴드를 따라가면 WAN-124가 낸 판정(볼린저 축 11셀 유의)의 검산이 통째로 끊긴다.
    새 밴드에서의 널 재검은 **별도 이슈**다(WAN-132 §후속) — 이 표를 조용히 다른 밴드로
    돌려 옛 결론 옆에 새 숫자를 두는 것보다 낫다.
    """
    return pin_band_bar(build_params(fill=fill, seed=seed, max_zone_width_atr=None))


def neutralized_pool_params(fill: FillPreset, seed: int) -> ConfluenceParams:
    """널 풀 = 채택 기본값에서 **볼린저만 끈 것**(= 존 근단 지정가에 무조건 진입).

    렌즈(`fill`)·시드는 실제와 **같게** 둔다. 체결 가정이 다르면 널이 규칙이 아니라 체결
    보수화를 재게 된다.
    """
    return adopted_params(fill, seed).model_copy(update=NEUTRALIZED_POOL_UPDATES)


def describe_engine() -> str:
    """이 리포트가 검정한 엔진의 지문 — CSV·md만 봐도 어떤 정의로 돌았는지 드러나게.

    `adopted_params`가 실제로 조립하는 것을 그대로 찍는다(밴드 고정 포함) — 지문이
    실행과 어긋나면 지문이 아니라 장식이다(WAN-88 `describe_engine`과 같은 규칙).
    """
    p = pin_band_bar(ConfluenceParams(max_zone_width_atr=None))
    return (
        f"entry_mode={p.entry_mode}, rsi_mode={p.rsi_mode}, short_enabled={p.short_enabled}, "
        f"take_profit_mode={p.take_profit_mode}, take_profit_r={p.take_profit_r}, "
        f"rsi_gate_mode={p.rsi_gate_mode}, retap_mode={p.retap_mode}, "
        f"zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"band_bar={p.deviation_filter.band_bar if p.deviation_filter else None}"
    )


# --------------------------------------------------------------------------- #
# 결과 모델
# --------------------------------------------------------------------------- #


class NullRow(BaseModel):
    """한 (심볼, TF, 구간, 렌즈, 시드)의 매칭 널 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    fill: str
    seed: int
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


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SymbolTfTask:
    """fan-out 한 단위 = (심볼, TF).

    워커가 **자기 데이터를 자기가 로드**한다(`backtest.run._CellTask`와 같은 패턴) —
    부모가 로드해 넘기면 심볼당 수백 MB의 1분봉을 프로세스 경계로 pickle해야 한다.
    """

    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    iterations: int
    lens_names: tuple[str, ...]


def run_symbol_timeframe(task: _SymbolTfTask, *, log: bool = True) -> list[NullRow]:
    """한 (심볼, TF)의 IS/OOS × 렌즈 × 시드 널을 낸다."""
    market = load_market_data(
        task.symbol, task.timeframe, start_ms=task.start_ms, end_ms=task.end_ms
    )
    if market.empty or market.df_1m.empty:
        return []

    cfg = build_config(task.timeframe)
    rows: list[NullRow] = []
    for segment in segments_for(oos=True):
        if segment.name not in SEGMENT_ORDER:
            continue  # 전 구간은 널에 새 정보를 주지 않는다(모듈 상수 참고).
        seg = slice_market(market, segment)
        if seg.empty or seg.df_1m.empty:
            continue
        ob_result = detect_order_blocks(seg, LEGACY_OB_PARAMS)

        for lens_name in task.lens_names:
            preset = fill_preset(lens_name)
            for seed in iter_seeds(preset):
                real_p = adopted_params(preset, seed)
                pool_p = neutralized_pool_params(preset, seed)
                result = run_random_control_b_segment(
                    seg.htf_df,
                    seg.df_1m,
                    task.timeframe,
                    symbol=task.symbol,
                    segment="IS" if segment.name == SEGMENT_IS else "OOS",
                    gate=lens_name,
                    confluence_params=real_p,
                    backtest_config=cfg,
                    order_block_result=ob_result,
                    iterations=task.iterations,
                    seed=BOOTSTRAP_SEED,
                    funding_rates=seg.funding_rates,
                    pool_params=pool_p,
                )
                row = NullRow(
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    segment=segment.name,
                    fill=lens_name,
                    seed=seed,
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
                )
                rows.append(row)
                if log:
                    print(
                        f"[wan123-null] {task.symbol} {task.timeframe} {segment.name} "
                        f"{lens_name} seed={seed}: real={row.real_total_return:.4f} "
                        f"n={row.real_num_trades} pool={row.pool_size} p={row.random_p_value}",
                        flush=True,
                    )
    return rows


def run_report(
    symbols: Sequence[str] = ALL_SYMBOLS,
    *,
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    lens_names: Sequence[str] = LENS_NAMES,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> list[NullRow]:
    """6심볼 × 2TF × IS/OOS × 3렌즈 격자를 돈다."""
    tasks = [
        _SymbolTfTask(
            symbol=normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            iterations=iterations,
            lens_names=tuple(lens_names),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(tasks) <= 1:
        return [row for task in tasks for row in run_symbol_timeframe(task, log=log)]

    rows: list[NullRow] = []
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for result in executor.map(_run_task_logged, tasks):
            rows.extend(result)
    return rows


def _run_task_logged(task: _SymbolTfTask) -> list[NullRow]:
    return run_symbol_timeframe(task, log=True)


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[NullRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path) -> list[NullRow]:
    """저장된 원본 CSV를 행으로 되읽는다(요약만 고칠 때 격자를 다시 돌지 않는다).

    `NullRow`로 검증하며 통과하므로 요약이 CSV와 갈라질 수 없다(WAN-111 패턴).
    """
    frame = pd.read_csv(path)
    return [NullRow.model_validate(record) for record in frame.to_dict(orient="records")]


def is_significant(row: NullRow, alpha: float = 0.05) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제가 무작위 평균보다 우월(WAN-70/84/88과 같은 자).

    방향 조건이 붙는 이유는 WAN-70과 같다 — 실제가 무작위보다 나쁜데 p가 낮은 것은
    하방이라 채택 근거가 아니다.
    """
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def _eligible(rows: Sequence[NullRow], lens: str) -> list[NullRow]:
    return [
        r
        for r in rows
        if r.fill == lens
        and r.random_p_value is not None
        and r.real_num_trades >= MIN_TRADES_FOR_VERDICT
    ]


def build_verdict(
    rows: Sequence[NullRow], *, lens: str = OFFICIAL_LENS, alpha: float = 0.05
) -> str:
    """한 렌즈에서 "엣지 있다/없다/일부에만" 판정(WAN-70/84/88 임계값 그대로)."""
    scoped = [r for r in rows if r.fill == lens and r.random_p_value is not None]
    if not scoped:
        return f"판정 불가(fill={lens}): 유효한 셀이 없다(거래 0건 또는 데이터 부족)."
    eligible = _eligible(rows, lens)
    excluded = len(scoped) - len(eligible)
    sig = [r for r in eligible if is_significant(r, alpha)]
    total = len(eligible)
    if total == 0:
        verdict = "**판정 불가**(유효 표본 셀 없음)"
    elif not sig:
        verdict = "**엣지 없다**"
    elif len(sig) == total:
        verdict = "**엣지 있다**"
    else:
        cells = ", ".join(f"{r.symbol.split('/')[0]}/{r.timeframe}/{r.segment}" for r in sig)
        verdict = f"**특정 TF·심볼에서만 있다**({cells})"
    return (
        f"fill={lens}: 유효 셀 {total}개(거래 {MIN_TRADES_FOR_VERDICT}건 미만 {excluded}개 "
        f"제외) 중 {len(sig)}개가 p≤{alpha} & 실제>무작위평균. 판정: {verdict}"
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.{digits}f}"


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None or pd.isna(value) else f"{value * 100:.{digits}f}%"


_TF_ORDER = {"15m": 0, "1h": 1}
_SEG_ORDER = {SEGMENT_IS: 0, SEGMENT_OOS: 1}


def _sorted_rows(rows: Sequence[NullRow]) -> list[NullRow]:
    lens_order = {name: i for i, name in enumerate(LENS_NAMES)}
    return sorted(
        rows,
        key=lambda r: (
            r.symbol,
            _TF_ORDER.get(r.timeframe, 9),
            _SEG_ORDER.get(r.segment, 9),
            lens_order.get(r.fill, 9),
            r.seed,
        ),
    )


def null_table(rows: Sequence[NullRow], *, lens: str) -> str:
    """한 렌즈의 셀별 표. 스트레스 렌즈는 시드가 5개라 행이 5배가 된다."""
    header = (
        "| 심볼 | TF | 구간 | 실제수익 | n | 풀 | 무작위평균 | 95% CI | p | 유의 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | -- | --: | -- |"
    )
    body = []
    for r in _sorted_rows([row for row in rows if row.fill == lens]):
        ci = (
            f"[{_fmt(r.random_ci_low)}, {_fmt(r.random_ci_high)}]"
            if r.random_ci_low is not None
            else "—"
        )
        thin = r.real_num_trades < MIN_TRADES_FOR_VERDICT
        mark = "표본부족" if thin else ("**✓**" if is_significant(r) else "")
        seed_note = f" (s{r.seed})" if lens == STRESS_LENS else ""
        body.append(
            f"| {r.symbol.split('/')[0]}{seed_note} | {r.timeframe} | {r.segment} | "
            f"{_fmt(r.real_total_return)} | {r.real_num_trades} | {r.pool_size} | "
            f"{_fmt(r.random_mean_return)} | {ci} | {_fmt(r.random_p_value)} | {mark} |"
        )
    return header + "\n" + "\n".join(body)


def pool_growth_note(rows: Sequence[NullRow]) -> str:
    """풀(볼린저 off)이 실제 거래보다 얼마나 큰지 — 널이 퇴화하지 않았다는 증거.

    풀 = 실제이면 널은 자기 자신을 검정한다(모듈 docstring). 이 수치가 그 사고가 **이
    표에서는 일어나지 않았다**는 CSV 상의 증거다 — 코드가 막고 있지만(같은 파라미터면
    `run_random_control_b_segment`가 거부한다), 막혔다는 것이 산출물에도 보여야 한다.
    """
    scoped = [r for r in rows if r.fill == OFFICIAL_LENS and r.real_num_trades > 0]
    if not scoped:
        return "풀 크기를 비교할 셀이 없다."
    ratios = [r.pool_size / r.real_num_trades for r in scoped]
    worst = min(ratios)
    return (
        f"공식 렌즈 {len(scoped)}셀에서 무력화 풀은 실제 거래 수의 평균 "
        f"**{sum(ratios) / len(ratios):.2f}배**(최소 {worst:.2f}배)다 — 풀이 실제와 같아지는 "
        "퇴화(모듈 docstring 「그대로 다시 돌릴 수 없다」)는 이 표에서 일어나지 않았다."
    )


def summarize_lens_dependence(rows: Sequence[NullRow]) -> str:
    """공식 렌즈에서 유의한 셀이 관통 요구 한 번에 살아남는가(WAN-88 §체결 가정 재현)."""
    official = {
        (r.symbol, r.timeframe, r.segment)
        for r in _eligible(rows, OFFICIAL_LENS)
        if is_significant(r)
    }
    if not official:
        return (
            "공식 렌즈(`baseline`, 낙관)에서도 유의한 셀이 없다 — 체결 가정을 조이기 전부터 "
            "엣지 증거가 없으므로 관통 민감도는 이 판정에 영향을 주지 않는다."
        )
    penalized = {
        (r.symbol, r.timeframe, r.segment) for r in _eligible(rows, "pen_5bp") if is_significant(r)
    }
    lost = official - penalized
    cells = ", ".join(f"{s.split('/')[0]}/{t}/{g}" for s, t, g in sorted(lost))
    if not lost:
        return (
            f"공식 렌즈에서 유의한 셀 {len(official)}개가 `pen_5bp`(관통 요구)에서도 전부 "
            "유지된다 — 유의성이 「스치듯 닿은 체결」에 기대고 있지 않다."
        )
    return (
        f"⚠️ 공식 렌즈에서 유의한 셀 {len(official)}개 중 **{len(lost)}개가 `pen_5bp`에서 "
        f"유의성을 잃는다**({cells}) — 그 유의성은 **지정가를 스치듯 닿고 되돌아선 체결**에 "
        "실려 있고, 그것이야말로 실거래에서 큐 우선순위 때문에 가장 안 될 체결이다"
        "(WAN-96 비대칭 · WAN-88 §판정의 재현)."
    )


def build_conclusion(rows: Sequence[NullRow]) -> str:
    """판정 문장 — 숫자는 전부 행에서 계산한다.

    문장에 숫자를 박아 두면 데이터·엔진이 움직인 뒤에도 옛 숫자가 남아 리포트가 조용히
    거짓말을 한다(WAN-88 `_implications`의 원칙 그대로).
    """
    eligible = _eligible(rows, OFFICIAL_LENS)
    sig = [r for r in eligible if is_significant(r)]
    oos_eligible = [r for r in eligible if r.segment == SEGMENT_OOS]
    oos_sig = [r for r in oos_eligible if is_significant(r)]

    if not eligible:
        headline = (
            "**판정 불가(표본 부족)** — 공식 렌즈에서 거래 "
            f"{MIN_TRADES_FOR_VERDICT}건 이상인 셀이 하나도 없다."
        )
    elif not sig:
        headline = (
            f"**엣지 없다** — 공식 렌즈(`baseline`)에서 유효 셀 {len(eligible)}개 중 "
            "p≤0.05 & 실제>무작위평균인 셀은 **0개**다. 게이트를 뺀 뒤에도 "
            "WAN-84/88/111의 「엣지 없음」이 유지된다."
        )
    elif len(sig) == len(eligible):
        headline = (
            f"**엣지 있다** — 공식 렌즈에서 유효 셀 {len(eligible)}개가 **전부** "
            "p≤0.05 & 실제>무작위평균이다."
        )
    else:
        cells = ", ".join(f"{r.symbol.split('/')[0]}/{r.timeframe}/{r.segment}" for r in sig)
        headline = (
            f"**일부 셀에만 유의성이 있다** — 공식 렌즈 유효 셀 {len(eligible)}개 중 "
            f"{len(sig)}개가 유의하다({cells}). 전 셀 일관이 아니므로 「엣지 확인」으로 "
            "읽으면 안 된다."
        )
    oos_line = (
        f"OOS만 보면 유효 셀 {len(oos_eligible)}개 중 유의 {len(oos_sig)}개다 — 채택 판단은 "
        "이 줄이 기준이다(IS 유의는 과최적화와 구분되지 않는다)."
        if oos_eligible
        else f"OOS 유효 셀(거래 {MIN_TRADES_FOR_VERDICT}건 이상)이 없어 OOS 판정은 불가하다."
    )
    return "\n\n".join(
        [
            headline,
            oos_line,
            build_verdict(rows, lens=OFFICIAL_LENS),
            f"참고(민감도): {build_verdict(rows, lens='pen_5bp')}",
            summarize_lens_dependence(rows),
            pool_growth_note(rows),
            _implications(rows),
        ]
    )


def _implications(rows: Sequence[NullRow]) -> str:
    """이 결과가 말하는 것 / 말하지 않는 것."""
    eligible = _eligible(rows, OFFICIAL_LENS)
    sig = [r for r in eligible if is_significant(r)]
    price_line = (
        "- **말하지 않는 것**: 유의한 셀이 있어도 그것은 **「볼린저가 좋은 셋업을 고른다」의 "
        "증거가 아니다** — 이 널의 풀은 존 근단 가격이고 실제는 밴드가라, 「선별」과 「가격」이 "
        "섞여 있다(모듈 docstring). WAN-114/115가 **가격 쪽**을 가리키므로 유의성은 "
        "「가격 효과 재확인」으로 먼저 읽어야 한다.\n"
        if sig
        else "- **말하지 않는 것**: 볼린저가 **가격으로도** 값을 더하지 않는다는 뜻은 아니다 — "
        "WAN-114가 15m OOS에서 잰 +16.20%p 증분은 이 널이 부정하는 대상이 아니다. 이 표가 "
        "말하는 것은 그 증분이 **무작위 대조 대비 유의하지 않다**는 것이다.\n"
    )
    return (
        "### 이 결과가 말하는 것 / 말하지 않는 것\n\n"
        "- **말하는 것**: WAN-88이 대조군으로 쓰던 풀(RSI 무력화 = 존에 닿으면 진입)이 "
        "**지금의 채택 기본값 그 자체**다(모듈 docstring 실측 표) — 그래서 「게이트를 빼도 "
        "무작위와 구분 안 됨이 유지되는가」는 **재검정이 아니라 전제**였다. 이 표는 그 자리에 "
        "남은 유일한 선별 규칙(볼린저)을 무력화 축으로 놓고 다시 물은 것이다.\n"
        f"{price_line}"
        "- **말하지 않는 것**: 실거래 승인이 아니다. `ALPHABLOCK_LIVE_TRADING` 기본값 "
        "`false`는 그대로다([WAN-86 결정 2](../../docs/decisions/wan86.md))."
    )


def build_summary_markdown(rows: Sequence[NullRow], *, csv_path: Path) -> str:
    stress_rows = [r for r in rows if r.fill == STRESS_LENS]
    stress_section = (
        "## 스트레스 렌즈 `pen_5bp_drop_50` (기본이 아님 — `--lenses`로 명시해 돌린 결과)\n\n"
        "⚠️ **이 표의 p값으로 판정하지 말 것.** 탈락 추첨은 시드마다 크게 널뛰는데 매칭 널은 "
        "그 위에 부트스트랩이라는 두 번째 난수를 얹는다 — 두 잡음이 겹치면 p값은 「엔진이 뭘 "
        "하는가」가 아니라 「시드를 어떻게 뽑았는가」를 잰다. WAN-88이 이 렌즈를 널에서 아예 "
        "제외한 이유다(그 모듈 docstring 「체결 가정」).\n\n"
        f"{null_table(rows, lens=STRESS_LENS)}\n\n"
        if stress_rows
        else ""
    )
    lens_count = len({r.fill for r in rows}) or len(LENS_NAMES)
    return (
        "# WAN-124 2단 — 게이트 제거 엔진의 매칭 널\n\n"
        f"6심볼 × 2TF(15m·1h) × IS/OOS × {lens_count}렌즈, 못 박은 창 "
        f"{DEFAULT_START}~{DEFAULT_END}, 로컬 `data/ohlcv.db` 실데이터.\n"
        "재현: `uv run python -m backtest.wan123_matched_null` "
        "(요약만: `--from-csv`). 원자료: "
        f"`{csv_path}`.\n\n"
        "## 이 리포트가 검정한 엔진\n\n"
        f"**지금 채택된 기본값 그대로** — `{describe_engine()}` + 펀딩비 반영(실제·널 "
        "양쪽에 동일하게). 전략 파라미터는 하나도 바꾸지 않았다(검증 전용, 파라미터 탐색 금지).\n\n"
        "> 🚨 **WAN-88의 널을 그대로 다시 돌리지 않았다 — 돌릴 수 없다.**\n"
        "> 게이트가 없는 엔진에서는 그 널의 무력화 풀(RSI 무력화)이 **실제 후보 집합과 글자\n"
        "> 그대로 같아진다**(BTC 1h 실측: `first_tap_free` 33 vs 52 → `unconditional` 52 vs 52).\n"
        "> 즉 **WAN-88이 대조군으로 쓰던 풀이 지금의 채택 기본값**이고, 널은 자기 자신을\n"
        "> 검정하면서도 p값을 멀쩡히 뱉는다. 그래서 무력화 축을 **남은 유일한 선별 규칙인\n"
        "> 볼린저**로 옮겼다 — 근거·읽는 법은 모듈 docstring과\n"
        "> [`docs/decisions/wan124.md`](../../docs/decisions/wan124.md).\n\n"
        "> **공식 렌즈는 `baseline`**([WAN-104](../../docs/decisions/wan104.md)) · `pen_5bp`는\n"
        "> 민감도. **스트레스 렌즈(`pen_5bp_drop_50`)는 널에서 뺐다** — WAN-88과 같은 축이다.\n"
        "> 탈락 추첨의 시드 잡음 위에 부트스트랩 난수를 얹으면 p값이 엔진이 아니라 시드를\n"
        "> 재기 때문이다(모듈 docstring 「스트레스 렌즈」). ⚠️ 이슈(WAN-124)는 널에도 3렌즈를\n"
        "> 요구했지만, **그 요구는 3단 표가 채운다** —\n"
        "> [`wan123_fill_conservatism_summary.md`](wan123_fill_conservatism_summary.md)는\n"
        "> 부트스트랩이 없어 난수가 탈락 추첨 하나뿐이라 그 렌즈가 뜻을 갖는 자리다.\n\n"
        "## 공식 렌즈 `baseline` — 셀별 결과\n\n"
        f"{null_table(rows, lens=OFFICIAL_LENS)}\n\n"
        "`p` = 무작위 반복 중 실제 총수익률 이상을 낸 비율(단측). 95% CI는 무작위 분포의\n"
        "2.5~97.5 백분위수. `풀` = 볼린저를 끈 존-단독 후보 수(표본추출 대상).\n"
        "널 정의(방향·시각대를 맞춘 재표본추출)는 `backtest/wan70_random_control_b.py`\n"
        "모듈 docstring 참고 — 롱 온리 전략이므로 널도 롱 온리다(`real_short=0`).\n\n"
        "## 민감도 렌즈 `pen_5bp`\n\n"
        f"{null_table(rows, lens='pen_5bp')}\n\n"
        f"{stress_section}"
        "## 결론\n\n"
        f"{build_conclusion(rows)}\n"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-124 2단: 게이트 제거 엔진의 매칭 널")
    parser.add_argument("--symbols", nargs="+", default=list(ALL_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--lenses", nargs="+", default=list(LENS_NAMES))
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument(
        "--jobs", type=int, default=1, help="(심볼, TF) 단위 병렬 워커 수(기본 1 = 직렬)."
    )
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
        print(f"[wan123-null] {args.csv_out}에서 {len(rows)}행 읽음")
    else:
        rows = run_report(
            args.symbols,
            timeframes=tuple(args.timeframes),
            start=args.start,
            end=args.end,
            lens_names=tuple(args.lenses),
            iterations=args.iterations,
            jobs=args.jobs,
        )
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(args.csv_out, index=False)
        print(f"[wan123-null] {len(rows)}행 → {args.csv_out}")

    summary = build_summary_markdown(rows, csv_path=args.csv_out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan123-null] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
