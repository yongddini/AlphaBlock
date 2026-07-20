"""롱 온리 채택 기본값으로 매칭 널·OOS 재검정 (WAN-88).

배경은 이슈 WAN-88 본문 참고. WAN-84(PR #64)의 "엣지 없음" 판정(유효 17셀 중 p≤0.05 &
실제>무작위 **0셀**)은 **숏 활성화 전제**로 산출됐다. 그 뒤 채택 설정이 세 번 움직였다:

1. **WAN-87**(PR #66) — 숏 비활성화(`short_enabled=False`). WAN-84 OOS 분해에서 롱
   +3.07%(9셀) vs 숏 −3.82%(7셀)였으므로, 숏을 뺀 뒤의 판정은 달라질 수 있다.
2. **WAN-95**(PR #69) — 기본 진입이 지정가(`zone_limit`) + 메이커 수수료로 전환.
3. **WAN-100**(PR #73) — 채택(지정가) 경로에 「첫 탭 RSI 무관 진입」 배선. 체결률
   29%→50%, 거래 수 1.5배로 **표본 자체가 바뀌었다**.

즉 **현재 채택된 기본 설정에서 엣지가 있는지 없는지를 아무도 검정하지 않은 상태**다.
`wan87_long_only_summary.md`는 수익률 재산출이지 널 대조·유의성 검정이 아니다. 이
모듈이 그 빈칸을 채운다.

## 검정 엔진 = 채택 기본값 그대로

`ADOPTED_PARAMS`는 `harness.build_params()`가 조립한 **`ConfluenceParams()` 그대로**다
(지정가 + 볼린저 진입가 + 첫 탭 면제/재탭 RSI + 고정 1.5R + 롱 온리). WAN-84가
`NEW_ENGINE_PARAMS`에 `short_enabled=True`를 **명시 고정**한 것과 정반대의 선택인데,
이유가 있다: WAN-84는 "숏 활성화 신 엔진"이라는 **과거 정의를 보존**하는 리포트이고,
이 모듈은 "**지금 채택된 것**에 엣지가 있는가"를 묻는다. 기본값이 움직이면 이 리포트의
수치는 낡은 것이 되어야 맞다 — 그래서 고정하지 않는다. 대신 `describe_engine()`이 실행
시점의 핵심 필드를 리포트에 적어, 어떤 정의로 돌았는지가 CSV·md만 봐도 드러나게 한다.

## 체결 가정 — 공식 기준선은 `pen_5bp` (WAN-97)

[WAN-97](../docs/decisions/wan97.md) 결정 1이 **공식 체결 기준선을 `pen_5bp`**(지정가
5bp 관통 요구, 탈락 0%)로 확정했다. 이 모듈은 널을 두 가정에서 돌린다:

- **`baseline`**(닿으면 체결) — WAN-84와 **같은 체결 가정**이라 대조표의 축이 된다.
- **`pen_5bp`**(공식) — 판정의 주 수치.

**`pen_5bp_drop_50`(최악 가정)은 널에서 제외한다.** WAN-97 결정 1이 이미 그것을 기준선
후보에서 뺀 이유가 여기서 배로 커지기 때문이다: 탈락 추첨은 시드마다 결과가 크게
널뛰는데(같은 셀이 −11%~+30%), 매칭 널은 그 위에 **부트스트랩 재표본추출이라는 두 번째
난수**를 얹는다. 두 잡음이 겹치면 p값은 "엔진이 뭘 하는가"가 아니라 "시드를 어떻게
뽑았는가"를 재게 된다. 임의 모수(탈락률 50%)가 판정을 좌우하게 두지 않는다는 WAN-97의
원칙 그대로다.

## 펀딩비 — 이번에는 넘긴다 (WAN-91 배선의 조용한 구멍)

WAN-84가 재실행한 세 모듈은 `funding_rates`를 **넘기지 않았다**. `funding_enabled`
기본값이 True여도 호출부가 데이터를 안 넘기면 `funding_missing_policy="zero"`가 에러도
경고도 없이 펀딩 0으로 지나간다 — WAN-65·WAN-91이 두 번 잡은 "조용한 실패"의 재발이다.
이 모듈은 `FundingRateStore`로 조회한 실제 펀딩비를 **실제·널 양쪽에 동일하게** 넘긴다
(`run_random_control_b_segment(funding_rates=...)`, WAN-88이 추가한 인자). 매칭 널은
방향·시간대를 맞춘 대조군이라 펀딩비가 양쪽에 붙어 상당 부분 상쇄되지만, 롱 온리는
펀딩을 **지불하는 쪽**이라 절대 수익률에는 남는다. `funding_coverage`를 CSV에 실어
얼마나 반영됐는지 드러낸다.

## IS/OOS 분할 — WAN-84와 같은 경계

`wan68_short_gate_analysis._split_bars`(앞 2/3 IS, 뒤 1/3 OOS) + `_segment_window`
(워밍업은 IS 꼬리에서만 빌려 OOS 누수 없음)를 그대로 쓴다. 대조표가 성립하려면 WAN-84와
**같은 자리에서 자른** 셀이어야 하기 때문이다 — 경계가 다르면 "숏을 뺐더니 달라졌다"와
"자른 데가 달라서 달라졌다"를 구분할 수 없다.

## 판정

`build_verdict`는 WAN-70/84와 동일한 임계값(실제 거래 `_MIN_TRADES_FOR_VERDICT`건 이상인
셀만, p≤0.05 & 실제>무작위평균이면 유의)을 쓴다. 임계값을 물려받는 것이 핵심이다 —
같은 자로 재야 "숏 제거로 판정이 바뀌었는가"에 답할 수 있다.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    LEGACY_BAND_BAR,
    LEGACY_RSI_GATE_MODE,
    FillPreset,
    build_config,
    build_params,
    fill_preset,
    pin_band_bar,
)
from backtest.models import BacktestConfig, Trade
from backtest.wan68_short_gate_analysis import _split_bars
from backtest.wan70_random_control_b import (
    RandomControlBResult,
    Segment,
    _segment_window,
    run_random_control_b_segment,
)
from backtest.wan71_edge_decomposition import (
    COST_MULTIPLIERS,
    BuyHoldRow,
    _buy_hold_table,
    buy_hold_return,
)
from backtest.zone_limit_backtest import (
    _sequence_and_cost,
    build_result_from_trades,
    build_zone_limit_candidates,
)
from data.models import FundingRate
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

DEFAULT_SYMBOLS: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_YEARS: float = 3.0
_BOOTSTRAP_ITERATIONS = 200
_YEAR_MS = int(365.25 * 24 * 60 * 60 * 1000)
_DEFAULT_SEED = 88

#: 널을 돌릴 체결 가정. `baseline`은 WAN-84 대조용, `pen_5bp`는 WAN-97 공식 기준선.
#: 최악 가정(`pen_5bp_drop_50`)을 뺀 이유는 모듈 docstring "체결 가정" 참고.
NULL_FILL_LEVELS: tuple[str, ...] = ("baseline", "pen_5bp")

#: WAN-97 결정 1이 정한 공식 체결 기준선 — 모든 채택 판단의 주 수치.
OFFICIAL_FILL = "pen_5bp"

#: 판정에 포함할 셀의 최소 실제 거래 수(WAN-70/84와 동일 — 같은 자로 재야 대조가 성립).
_MIN_TRADES_FOR_VERDICT = 20

#: WAN-84 원자료(숏 포함, 교정 전 엔진) — 대조표의 좌변.
WAN84_NULL_CSV = Path("backtest/reports/wan84_random_entry_new_engine.csv")


#: WAN-88 발표 수치가 나온 오프셋(bp). 채택 기본값은 WAN-112부터 2bp지만 이 엔트로피
#: 검정(실제 vs 무작위 널)은 0bp에서 돌았고, 그 「유의 셀 0개」 판정이 문서의 결론이다.
#: 기본값을 따라가게 두면 결론 문장과 재현 숫자가 어긋난다 — 이 리포트는 당시 엔진의 기록이다.
PINNED_OFFSET_BPS = 0.0

#: 같은 이유로 RSI 게이트도 당시 값으로 고정한다(WAN-123이 기본값을 `unconditional`로
#: 옮겼다). 이 모듈의 「유효 16셀 중 유의 0개」는 **게이트가 켜진 거래 집합**의 판정이다 —
#: 게이트를 빼면 거래가 13~14% 늘어 표본 자체가 달라지므로 같은 표가 아니다.
#: ⚠️ 위 docstring의 "기본값을 고정하지 않는다"는 원칙은 **WAN-123 이전 서술**이다. 게이트
#: 제거 뒤의 널 재검은 이 모듈을 다시 돌리는 게 아니라 `wan123_*`이 새로 낸다(같은 이슈가
#: 두 엔진의 표를 하나의 md에 섞지 않도록).
PINNED_RSI_GATE_MODE = LEGACY_RSI_GATE_MODE

#: WAN-132가 밴드 정본을 `intrabar_live`로 옮기기 전의 값(탭 봉 종가). 이 표의 「엣지
#: 없음」 판정과 「`baseline` 유의 4셀이 `pen_5bp`에 전멸」이라는 관찰은 전부 그 밴드
#: 위에서 나왔다 — 고정하지 않으면 조용히 새 밴드로 다시 돈다.
PINNED_BAND_BAR = LEGACY_BAND_BAR


def adopted_params(fill: FillPreset) -> ConfluenceParams:
    """WAN-88 당시 채택 기본값 + 체결 가정만 얹은 파라미터.

    `harness.build_params`를 거치는 이유는 `entry_mode`/`rsi_mode`를 한 세트로 묶는
    규칙(WAN-41/95)을 이 모듈이 따로 재구현하지 않기 위해서다. 전략 필드는 하나도
    바꾸지 않는다 — 이 이슈는 검증 전용이고 파라미터 탐색은 금지다. 당시 값으로 명시
    고정하는 것은 오프셋(`PINNED_OFFSET_BPS`)·RSI 게이트(`PINNED_RSI_GATE_MODE`)와
    밴드 표본(`PINNED_BAND_BAR`, WAN-132) 셋이다.
    """
    return pin_band_bar(
        build_params(
            fill=fill,
            offset_bps=PINNED_OFFSET_BPS,
            base=ConfluenceParams(rsi_gate_mode=PINNED_RSI_GATE_MODE),
        ),
        PINNED_BAND_BAR,
    )


def describe_engine() -> str:
    """이 리포트가 실제로 돌린 엔진의 핵심 필드 — 리포트에 박아 두는 엔진 지문.

    `ConfluenceParams()`(= 지금의 채택 기본값)가 아니라 **고정한 엔진**을 찍는다. 지문이
    실행과 어긋나면 지문이 아니라 장식이다.
    """
    p = pin_band_bar(
        ConfluenceParams(
            zone_limit_offset_bps=PINNED_OFFSET_BPS, rsi_gate_mode=PINNED_RSI_GATE_MODE
        ),
        PINNED_BAND_BAR,
    )
    band_bar = p.deviation_filter.band_bar if p.deviation_filter else "—"
    return (
        f"entry_mode={p.entry_mode}, rsi_mode={p.rsi_mode}, short_enabled={p.short_enabled}, "
        f"take_profit_mode={p.take_profit_mode}, take_profit_r={p.take_profit_r}, "
        f"rsi_gate_mode={p.rsi_gate_mode}, retap_mode={p.retap_mode}, "
        f"zone_limit_offset_bps={p.zone_limit_offset_bps}, band_bar={band_bar}"
    )


# --------------------------------------------------------------------------- #
# 결과 모델
# --------------------------------------------------------------------------- #


class NullCellRow(BaseModel):
    """한 (심볼, TF, 구간, 체결 가정)의 매칭 널 결과.

    `RandomControlBResult`(WAN-70)에 **체결 가정 축과 체결률**을 더한 것이다. WAN-70의
    모델을 그대로 쓰지 않는 이유는 그것의 `gate` 필드가 "RSI 게이트 프리셋"을 뜻하는데
    여기서 축은 게이트가 아니라 체결 가정이기 때문이다 — 같은 열에 다른 뜻을 담으면
    두 리포트의 CSV를 나란히 놓고 읽을 수 없다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    fill: str
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
    fill_rate: float | None
    """eligible 셋업 중 실제로 체결된 비율 — 체결 가정을 조이면 표본이 얼마나 얕아지는지."""
    eligible_setups: int | None
    funding_coverage: float | None
    """펀딩비가 구간의 몇 %를 덮었는지. None이면 펀딩 미반영."""


class CostRow(BaseModel):
    """한 (심볼, TF, 구간, 체결 가정)에서 비용 배율별 채택 전략 성과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    fill: str
    cost_multiplier: float
    total_return: float
    num_trades: int
    win_rate: float


def _to_null_row(
    result: RandomControlBResult,
    *,
    fill: str,
    fill_rate: float | None,
    eligible_setups: int | None,
    funding_coverage: float | None,
) -> NullCellRow:
    return NullCellRow(
        symbol=result.symbol,
        timeframe=result.timeframe,
        segment=result.segment,
        fill=fill,
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
        fill_rate=fill_rate,
        eligible_setups=eligible_setups,
        funding_coverage=funding_coverage,
    )


# --------------------------------------------------------------------------- #
# 셀 단위 오케스트레이션
# --------------------------------------------------------------------------- #


def run_cell(
    seg_htf: pd.DataFrame,
    seg_1m: pd.DataFrame,
    seg_pure: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str,
    segment: Segment,
    order_block_result: OrderBlockResult,
    backtest_config: BacktestConfig,
    funding_rates: Sequence[FundingRate] | None = None,
    order_block_params: OrderBlockParams | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = _DEFAULT_SEED,
    cost_multipliers: tuple[float, ...] = COST_MULTIPLIERS,
    fill_levels: tuple[str, ...] = NULL_FILL_LEVELS,
) -> tuple[list[NullCellRow], list[CostRow], BuyHoldRow]:
    """한 구간에서 세 산출물(매칭 널, 비용 민감도, 바이앤홀드)을 체결 가정별로 낸다.

    `seg_pure`는 워밍업 컨텍스트가 섞이지 않은 구간 그 자체(바이앤홀드 창) —
    `wan71_edge_decomposition.run_cell`·`wan84_new_engine_validation.run_cell`과 같은
    이유다(OOS `seg_htf`에는 IS 꼬리 워밍업 봉이 섞여 있다).
    """
    cfg = backtest_config
    null_rows: list[NullCellRow] = []
    cost_rows: list[CostRow] = []

    for fill_name in fill_levels:
        preset = fill_preset(fill_name)
        params = adopted_params(preset)

        # 체결률(`ZoneLimitStats`)은 널 경로가 돌려주지 않으므로 후보를 직접 만들어
        # 얻고, 그 후보를 비용 배율 재시퀀싱에 재사용한다(배율마다 재시뮬레이션하지
        # 않는다 — 비용은 시퀀싱 단계에서만 붙으므로 결과는 같다).
        #
        # 대가: `run_random_control_b_segment`가 내부에서 실제 후보를 **다시** 만든다
        # (셀당 시뮬레이션 3회 = 여기 1 + 널의 실제 1 + 널의 무력화 풀 1). 셀당 수십 초
        # 규모라 이 리포트에서는 감수하고, 대신 WAN-70의 함수 계약을 건드리지 않는 쪽을
        # 택했다 — 그 함수는 WAN-70/84 CSV의 재현 기준이라 반환값을 바꾸면 두 리포트의
        # 회귀 기준이 같이 흔들린다.
        real_candidates, stats = build_zone_limit_candidates(
            seg_htf,
            seg_1m,
            timeframe,
            params=params,
            cfg=cfg,
            order_block_params=order_block_params,
            order_block_result=order_block_result,
        )

        null_result = run_random_control_b_segment(
            seg_htf,
            seg_1m,
            timeframe,
            symbol=symbol,
            segment=segment,
            gate=fill_name,
            confluence_params=params,
            order_block_params=order_block_params,
            backtest_config=cfg,
            order_block_result=order_block_result,
            iterations=iterations,
            seed=seed,
            funding_rates=funding_rates,
        )

        funding_cov: float | None = None
        for mult in cost_multipliers:
            mult_cfg = cfg.model_copy(
                update={
                    "fee_rate": cfg.fee_rate * mult,
                    "maker_fee_rate": (
                        None if cfg.maker_fee_rate is None else cfg.maker_fee_rate * mult
                    ),
                    "slippage": cfg.slippage * mult,
                }
            )
            trades = _sequence_and_cost(real_candidates, mult_cfg, funding_rates)
            result = build_result_from_trades(trades, mult_cfg, timeframe)
            cost_rows.append(
                CostRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    segment=segment,
                    fill=fill_name,
                    cost_multiplier=mult,
                    total_return=result.metrics.total_return,
                    num_trades=result.metrics.num_trades,
                    win_rate=result.metrics.win_rate,
                )
            )
            if mult == 1.0:
                funding_cov = _funding_coverage_for(trades, funding_rates)

        null_rows.append(
            _to_null_row(
                null_result,
                fill=fill_name,
                fill_rate=stats.fill_rate,
                eligible_setups=stats.eligible,
                funding_coverage=funding_cov,
            )
        )

    closes = seg_pure["close"] if "close" in seg_pure.columns else pd.Series(dtype=float)
    buy_hold_row = BuyHoldRow(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        buy_hold_return=buy_hold_return(closes, cfg),
        num_bars=len(seg_pure),
    )
    return null_rows, cost_rows, buy_hold_row


def _funding_coverage_for(
    trades: Sequence[Trade], funding_rates: Sequence[FundingRate] | None
) -> float | None:
    """펀딩비가 실제로 붙은 거래의 비율.

    "데이터는 넘겼는데 정말 반영됐는가"를 CSV에서 확인할 수 있어야 하기 때문이다
    (WAN-91의 조용한 실패 재발 방지 — 그때는 `funding_enabled`만 True이고 데이터가
    없어 전부 0이었는데 아무도 몰랐다). 펀딩을 아예 안 넘겼으면 None이라 "미반영"이
    명시적으로 드러난다. 보유 구간이 펀딩 정산 시각(8시간마다)을 넘지 않은 짧은
    거래는 정상적으로 0이므로, 이 비율이 1.0보다 작은 것 자체는 이상이 아니다.
    """
    if not funding_rates:
        return None
    if not trades:
        return 0.0
    return sum(1 for t in trades if t.funding_cost != 0.0) / len(trades)


# --------------------------------------------------------------------------- #
# 심볼×TF 전체
# --------------------------------------------------------------------------- #


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    one_min_df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    funding_rates: Sequence[FundingRate] | None = None,
    order_block_params: OrderBlockParams | None = None,
    backtest_config: BacktestConfig | None = None,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    seed: int = _DEFAULT_SEED,
    cost_multipliers: tuple[float, ...] = COST_MULTIPLIERS,
    fill_levels: tuple[str, ...] = NULL_FILL_LEVELS,
) -> tuple[list[NullCellRow], list[CostRow], list[BuyHoldRow]]:
    """한 (심볼, TF)에 대해 IS/OOS 전체 산출물을 낸다 (WAN-84와 같은 분할 경계)."""
    frame = htf_df.sort_values("open_time").reset_index(drop=True)
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)].reset_index(drop=True)
    n = len(frame)
    if n < 30:
        return [], [], []

    is_end = _split_bars(n)
    warmup_bars = min(is_end, max(60, n // 6))
    cfg = backtest_config or build_config(timeframe)

    is_htf, is_1m = _segment_window(
        frame, one_min_df, timeframe, context_start=0, seg_start=0, seg_end=is_end
    )
    oos_htf, oos_1m = _segment_window(
        frame,
        one_min_df,
        timeframe,
        context_start=max(0, is_end - warmup_bars),
        seg_start=is_end,
        seg_end=n,
    )
    is_pure = frame.iloc[0:is_end].reset_index(drop=True)
    oos_pure = frame.iloc[is_end:n].reset_index(drop=True)

    is_ob = OrderBlockDetector(order_block_params).run(is_htf) if not is_htf.empty else None
    oos_ob = OrderBlockDetector(order_block_params).run(oos_htf) if not oos_htf.empty else None

    null_rows: list[NullCellRow] = []
    cost_rows: list[CostRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    for segment_label, seg_htf, seg_1m, seg_pure, ob_result in (
        ("IS", is_htf, is_1m, is_pure, is_ob),
        ("OOS", oos_htf, oos_1m, oos_pure, oos_ob),
    ):
        if seg_htf.empty or seg_1m.empty or ob_result is None:
            continue
        nr, cr, bh = run_cell(
            seg_htf,
            seg_1m,
            seg_pure,
            timeframe,
            symbol=symbol,
            segment=segment_label,  # type: ignore[arg-type]
            order_block_result=ob_result,
            backtest_config=cfg,
            funding_rates=funding_rates,
            order_block_params=order_block_params,
            iterations=iterations,
            seed=seed,
            cost_multipliers=cost_multipliers,
            fill_levels=fill_levels,
        )
        null_rows.extend(nr)
        cost_rows.extend(cr)
        buy_hold_rows.append(bh)
    return null_rows, cost_rows, buy_hold_rows


# --------------------------------------------------------------------------- #
# 재현 실행: 로컬 실데이터, 심볼 단위 병렬 fan-out (WAN-70/71/84 패턴)
# --------------------------------------------------------------------------- #

DEFAULT_DB_PATH = Path("data/ohlcv.db")
REPORTS_DIR = Path("backtest/reports")
DEFAULT_CACHE_DIR = Path("data/cache")


@dataclass(frozen=True)
class _SymbolTask:
    symbol: str
    one_min_full: pd.DataFrame
    htf_frames: dict[str, pd.DataFrame]
    funding_rates: list[FundingRate]
    timeframes: tuple[str, ...]
    years: float
    iterations: int
    seed: int
    fill_levels: tuple[str, ...]


@dataclass(frozen=True)
class _SymbolResult:
    null_rows: list[NullCellRow]
    cost_rows: list[CostRow]
    buy_hold_rows: list[BuyHoldRow]


def _run_symbol_task(task: _SymbolTask) -> _SymbolResult:
    """한 심볼 × 전체 TF를 순차 처리한다.

    심볼 단위로 나누는 이유는 WAN-70과 같다 — 1분봉은 한 심볼의 모든 TF가 공유하는
    대용량 DataFrame이라, TF 단위로 쪼개면 같은 1분봉을 TF 수만큼 중복 pickle한다.
    """
    one_min_full = task.one_min_full
    m_max = int(one_min_full["open_time"].max())
    req_start = m_max - int(task.years * _YEAR_MS)
    null_rows: list[NullCellRow] = []
    cost_rows: list[CostRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    for timeframe in task.timeframes:
        htf_df = task.htf_frames.get(timeframe)
        if htf_df is None or htf_df.empty:
            continue
        start = max(req_start, int(htf_df["open_time"].min()))
        htf_win = htf_df[htf_df["open_time"] >= start].reset_index(drop=True)
        one_min_win = one_min_full[one_min_full["open_time"] >= start].reset_index(drop=True)
        nr, cr, bh = run_symbol_timeframe(
            htf_win,
            one_min_win,
            symbol=task.symbol,
            timeframe=timeframe,
            funding_rates=task.funding_rates,
            iterations=task.iterations,
            seed=task.seed,
            fill_levels=task.fill_levels,
        )
        null_rows.extend(nr)
        cost_rows.extend(cr)
        buy_hold_rows.extend(bh)
        for r in nr:
            print(
                f"[wan88] {task.symbol} {timeframe} {r.segment} fill={r.fill}: "
                f"real={r.real_total_return:.4f} n={r.real_num_trades} "
                f"p={r.random_p_value} fill_rate={r.fill_rate}"
            )
    return _SymbolResult(null_rows, cost_rows, buy_hold_rows)


def run_experiment(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    years: float = DEFAULT_YEARS,
    iterations: int = _BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    seed: int = _DEFAULT_SEED,
    fill_levels: tuple[str, ...] = NULL_FILL_LEVELS,
) -> _SymbolResult:
    """로컬 `data/ohlcv.db` 실데이터로 심볼×TF 전부에 대해 산출물을 낸다.

    펀딩비는 심볼당 1회 조회해 그 심볼의 모든 TF·구간이 공유한다 — `_funding_cost_for`가
    보유 구간으로 잘라 쓰므로 구간별로 미리 자를 필요가 없다.
    """
    try:
        from data.funding import FundingRateStore
        from data.storage import OhlcvStore
    except Exception:  # pragma: no cover - 저장소 미가용
        return _SymbolResult([], [], [])
    if not db_path.exists():
        return _SymbolResult([], [], [])

    tasks: list[_SymbolTask] = []
    with OhlcvStore(db_path, cache_dir=cache_dir) as store:
        funding_store = FundingRateStore(str(db_path))
        for symbol in symbols:
            one_min_full = store.load(symbol, "1m")
            if one_min_full.empty:
                continue
            htf_frames = {tf: store.load(symbol, tf) for tf in timeframes}
            m_max = int(one_min_full["open_time"].max())
            rates = funding_store.get_rates(
                symbol,
                start_ms=m_max - int(years * _YEAR_MS),
                end_ms=m_max,
                include_predicted=True,
            )
            tasks.append(
                _SymbolTask(
                    symbol=symbol,
                    one_min_full=one_min_full,
                    htf_frames=htf_frames,
                    funding_rates=rates,
                    timeframes=timeframes,
                    years=years,
                    iterations=iterations,
                    seed=seed,
                    fill_levels=fill_levels,
                )
            )

    null_rows: list[NullCellRow] = []
    cost_rows: list[CostRow] = []
    buy_hold_rows: list[BuyHoldRow] = []
    if jobs <= 1 or len(tasks) <= 1:
        for task in tasks:
            r = _run_symbol_task(task)
            null_rows.extend(r.null_rows)
            cost_rows.extend(r.cost_rows)
            buy_hold_rows.extend(r.buy_hold_rows)
        return _SymbolResult(null_rows, cost_rows, buy_hold_rows)

    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        for r in executor.map(_run_symbol_task, tasks):
            null_rows.extend(r.null_rows)
            cost_rows.extend(r.cost_rows)
            buy_hold_rows.extend(r.buy_hold_rows)
    return _SymbolResult(null_rows, cost_rows, buy_hold_rows)


def _default_jobs() -> int:
    cpu = os.cpu_count() or 1
    return max(1, cpu - 1)


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _fmt(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _pct(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def is_significant(row: NullCellRow, alpha: float = 0.05) -> bool:
    """유의 셀 = p≤alpha **이면서** 실제가 무작위 평균보다 우월.

    방향 조건이 붙는 이유는 WAN-70과 같다 — 실제가 무작위보다 나쁜데 p가 낮은 것은
    하방 엣지라 채택 근거가 아니다.
    """
    return (
        row.random_p_value is not None
        and row.random_p_value <= alpha
        and row.random_mean_return is not None
        and row.real_total_return > row.random_mean_return
    )


def build_verdict(
    rows: list[NullCellRow],
    *,
    fill: str = OFFICIAL_FILL,
    alpha: float = 0.05,
    min_trades: int = _MIN_TRADES_FOR_VERDICT,
) -> str:
    """한 체결 가정에서 "엣지 있다/없다/일부에만 있다" 판정(WAN-70/84 임계값 그대로)."""
    scoped = [r for r in rows if r.fill == fill and r.random_p_value is not None]
    if not scoped:
        return f"판정 불가(fill={fill}): 유효한 셀이 없다(거래 0건 또는 데이터 부족)."
    eligible = [r for r in scoped if r.real_num_trades >= min_trades]
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
        f"fill={fill}: 유효 셀 {total}개(거래 {min_trades}건 미만 {excluded}개 제외) 중 "
        f"{len(sig)}개가 p≤{alpha} & 실제>무작위평균. 판정: {verdict}"
    )


def _eligible_significant(rows: list[NullCellRow], fill: str) -> set[tuple[str, str, Segment]]:
    """한 체결 가정에서 유의(표본 충족 + p≤0.05 + 방향)한 셀 좌표."""
    return {
        (r.symbol, r.timeframe, r.segment)
        for r in rows
        if r.fill == fill and r.real_num_trades >= _MIN_TRADES_FOR_VERDICT and is_significant(r)
    }


def summarize_fill_dependence(rows: list[NullCellRow]) -> str:
    """`baseline`에서만 유의한 셀 = **스치듯 닿은 체결에 기댄 유의성**.

    이 리포트에서 가장 중요한 문단이다. 공식 기준선(`pen_5bp`)과 낙관 가정(`baseline`)의
    판정이 갈리면, 그 차이는 "지정가를 관통하지 못하고 스치기만 한 체결"이 만든 것이다 —
    실거래에서 큐 우선순위 때문에 **가장 안 될 가능성이 높은** 체결이다(WAN-97 결정 1).
    거래 수는 거의 그대로인데 유의성만 사라진다면, 그 유의성은 엣지가 아니라 체결 가정의
    산물이라는 뜻이다.
    """
    base_sig = _eligible_significant(rows, "baseline")
    official_sig = _eligible_significant(rows, OFFICIAL_FILL)
    only_baseline = base_sig - official_sig
    if not base_sig:
        return (
            "`baseline`(낙관)에서도 유의한 셀이 없다 — 체결 가정을 조이기 전부터 "
            "엣지 증거가 없으므로 이 축은 판정에 영향을 주지 않는다."
        )
    if not only_baseline:
        return (
            f"`baseline`에서 유의한 셀 {len(base_sig)}개가 공식 기준선에서도 유지된다 — "
            "유의성이 체결 가정에 기대고 있지 않다."
        )

    by_tf: dict[str, int] = {}
    for _symbol, timeframe, _segment in only_baseline:
        by_tf[timeframe] = by_tf.get(timeframe, 0) + 1
    tf_note = (
        f"전부 **{next(iter(by_tf))}**이다"
        if len(by_tf) == 1
        else "TF별로 " + ", ".join(f"{tf} {n}개" for tf, n in sorted(by_tf.items())) + "다"
    )
    cells = ", ".join(f"{s.split('/')[0]}/{t}/{g}" for s, t, g in sorted(only_baseline))

    # 그 셀들에서 거래가 얼마나 빠졌는지 — "표본이 줄어서 유의성을 잃었다"는 대안 설명을
    # 배제하기 위한 수치다.
    drops: list[float] = []
    for key in only_baseline:
        base = next(
            (r for r in rows if (r.symbol, r.timeframe, r.segment) == key and r.fill == "baseline"),
            None,
        )
        off = next(
            (
                r
                for r in rows
                if (r.symbol, r.timeframe, r.segment) == key and r.fill == OFFICIAL_FILL
            ),
            None,
        )
        if base and off and base.real_num_trades:
            drops.append((base.real_num_trades - off.real_num_trades) / base.real_num_trades)
    drop_note = (
        f"그 셀들에서 관통 요구가 걷어낸 거래는 평균 **{sum(drops) / len(drops) * 100:.1f}%**에 "
        "불과하다"
        if drops
        else "거래 감소폭은 산출되지 않았다"
    )

    return (
        f"⚠️ **유의성이 전부 체결 가정에 기대고 있다.** `baseline`(낙관)에서 유의한 셀 "
        f"{len(base_sig)}개 중 **{len(only_baseline)}개가 공식 기준선(`pen_5bp`)에서 유의성을 "
        f"잃는다**({cells}) — 그리고 그 셀들은 {tf_note}. {drop_note} — 즉 표본이 줄어서가 "
        "아니라, **수익이 지정가를 스치듯 닿고 되돌아선 소수의 체결에 실려 있어서**다. "
        "그 체결이야말로 실거래에서 큐 우선순위 때문에 가장 안 될 체결이므로, 이 유의성은 "
        "채택 근거가 될 수 없다. [WAN-97](../../docs/decisions/wan97.md) §2-2가 수익률로 관찰한 "
        "「관통 민감도」를 **유의성 판정에서 다시 확인한 것**이며, `baseline` 단독 인용 금지 "
        "규칙(WAN-97 결정 1)이 왜 필요한지를 보여주는 사례다."
    )


def _null_table(rows: list[NullCellRow]) -> str:
    header = (
        "| 심볼 | TF | 구간 | fill | 실제수익 | n | 체결률 | 무작위평균 | 95% CI | p | 유의 |\n"
        "| -- | -- | -- | -- | --: | --: | --: | --: | -- | --: | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    fill_order = {name: i for i, name in enumerate(NULL_FILL_LEVELS)}
    ordered = sorted(
        rows,
        key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.segment, fill_order.get(r.fill, 9)),
    )
    body = []
    for r in ordered:
        ci = (
            f"[{_fmt(r.random_ci_low, 3)}, {_fmt(r.random_ci_high, 3)}]"
            if r.random_ci_low is not None
            else "—"
        )
        thin = r.real_num_trades < _MIN_TRADES_FOR_VERDICT
        mark = "표본부족" if thin else ("**✓**" if is_significant(r) else "")
        body.append(
            f"| {r.symbol.split('/')[0]} | {r.timeframe} | {r.segment} | {r.fill} | "
            f"{_fmt(r.real_total_return, 3)} | {r.real_num_trades} | {_pct(r.fill_rate)} | "
            f"{_fmt(r.random_mean_return, 3)} | {ci} | {_fmt(r.random_p_value, 3)} | {mark} |"
        )
    return header + "\n" + "\n".join(body)


def _cost_table(rows: list[CostRow], *, fill: str = OFFICIAL_FILL) -> str:
    header = (
        "| 심볼 | TF | 구간 | 1.0x | 1.5x | 2.0x | 부호 반전 |\n"
        "| -- | -- | -- | --: | --: | --: | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    scoped = [r for r in rows if r.fill == fill]
    keys = sorted(
        {(r.symbol, r.timeframe, r.segment) for r in scoped},
        key=lambda k: (k[0], order.get(k[1], 9), k[2]),
    )
    body = []
    for symbol, timeframe, segment in keys:
        by_mult = {
            r.cost_multiplier: r.total_return
            for r in scoped
            if (r.symbol, r.timeframe, r.segment) == (symbol, timeframe, segment)
        }
        base = by_mult.get(1.0)
        worst = by_mult.get(2.0)
        flipped = (
            "**예**" if base is not None and worst is not None and base > 0 >= worst else "아니오"
        )
        body.append(
            f"| {symbol.split('/')[0]} | {timeframe} | {segment} | "
            f"{_fmt(by_mult.get(1.0), 3)} | {_fmt(by_mult.get(1.5), 3)} | "
            f"{_fmt(by_mult.get(2.0), 3)} | {flipped} |"
        )
    return header + "\n" + "\n".join(body)


# --------------------------------------------------------------------------- #
# WAN-84 대조표
# --------------------------------------------------------------------------- #


class ContrastRow(BaseModel):
    """한 셀에서 WAN-84(숏 포함, 교정 전) → WAN-88(롱 온리, 교정 후)의 판정 이동."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: Segment
    wan84_total_return: float | None
    wan84_num_trades: int | None
    wan84_p_value: float | None
    wan84_significant: bool | None
    wan88_total_return: float | None
    wan88_num_trades: int | None
    wan88_p_value: float | None
    wan88_significant: bool | None


def load_wan84_rows(path: Path = WAN84_NULL_CSV) -> pd.DataFrame:
    """WAN-84 원자료를 읽는다. 없으면 빈 프레임(대조표를 건너뛴다)."""
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _as_segment(value: str) -> Segment | None:
    """CSV에서 읽은 구간 문자열을 `Segment`로 좁힌다(모르는 값이면 None)."""
    return value if value in ("IS", "OOS") else None  # type: ignore[return-value]


def build_contrast(
    rows: list[NullCellRow],
    wan84: pd.DataFrame,
    *,
    fill: str = "baseline",
    min_trades: int = _MIN_TRADES_FOR_VERDICT,
) -> list[ContrastRow]:
    """WAN-84와 같은 체결 가정(`baseline`) 셀끼리 맞대어 판정 이동을 낸다.

    `fill="baseline"`이 기본인 이유: WAN-84는 체결 보수화 노브가 생기기 전(WAN-96 이전)
    엔진이라 **닿으면 체결**로 돌았다. 공식 기준선(`pen_5bp`)과 맞대면 "숏을 뺐더니"와
    "체결을 조였더니"가 섞여 대조가 성립하지 않는다.
    """
    by_cell = {(r.symbol, r.timeframe, r.segment): r for r in rows if r.fill == fill}
    wan84_by_cell: dict[tuple[str, str, Segment], pd.Series] = {}
    if not wan84.empty:
        for _, w in wan84.iterrows():
            segment = _as_segment(str(w["segment"]))
            if segment is None:
                continue  # 모르는 구간 라벨은 대조에서 빼는 게 조용히 섞는 것보다 낫다.
            wan84_by_cell[(str(w["symbol"]), str(w["timeframe"]), segment)] = w

    out: list[ContrastRow] = []
    for key in sorted(set(by_cell) | set(wan84_by_cell)):
        new = by_cell.get(key)
        old = wan84_by_cell.get(key)
        old_sig: bool | None = None
        if old is not None and pd.notna(old["random_p_value"]):
            old_sig = bool(
                float(old["random_p_value"]) <= 0.05
                and float(old["real_total_return"]) > float(old["random_mean_return"])
                and int(old["real_num_trades"]) >= min_trades
            )
        new_sig: bool | None = None
        if new is not None and new.random_p_value is not None:
            new_sig = bool(is_significant(new) and new.real_num_trades >= min_trades)
        out.append(
            ContrastRow(
                symbol=key[0],
                timeframe=key[1],
                segment=key[2],
                wan84_total_return=None if old is None else float(old["real_total_return"]),
                wan84_num_trades=None if old is None else int(old["real_num_trades"]),
                wan84_p_value=(
                    None
                    if old is None or pd.isna(old["random_p_value"])
                    else float(old["random_p_value"])
                ),
                wan84_significant=old_sig,
                wan88_total_return=None if new is None else new.real_total_return,
                wan88_num_trades=None if new is None else new.real_num_trades,
                wan88_p_value=None if new is None else new.random_p_value,
                wan88_significant=new_sig,
            )
        )
    return out


def _contrast_table(rows: list[ContrastRow]) -> str:
    header = (
        "| 심볼 | TF | 구간 | WAN-84 수익 | WAN-84 n | WAN-84 p | "
        "WAN-88 수익 | WAN-88 n | WAN-88 p | 판정 이동 |\n"
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: | -- |"
    )
    order = {"15m": 0, "1h": 1, "4h": 2, "1d": 3}
    ordered = sorted(rows, key=lambda r: (r.symbol, order.get(r.timeframe, 9), r.segment))
    body = []
    for r in ordered:
        move = _describe_move(r.wan84_significant, r.wan88_significant)
        body.append(
            f"| {r.symbol.split('/')[0]} | {r.timeframe} | {r.segment} | "
            f"{_fmt(r.wan84_total_return, 3)} | {r.wan84_num_trades or '—'} | "
            f"{_fmt(r.wan84_p_value, 3)} | {_fmt(r.wan88_total_return, 3)} | "
            f"{r.wan88_num_trades or '—'} | {_fmt(r.wan88_p_value, 3)} | {move} |"
        )
    return header + "\n" + "\n".join(body)


def _describe_move(old: bool | None, new: bool | None) -> str:
    if old is None or new is None:
        return "—"
    if old and new:
        return "유의 유지"
    if not old and new:
        return "**무의미 → 유의**"
    if old and not new:
        return "유의 → 무의미"
    return "무의미 유지"


def summarize_contrast(rows: list[ContrastRow]) -> str:
    """대조표 한 줄 요약 — 숏 제거로 유의 셀 수가 어떻게 움직였는지."""
    judged = [
        r for r in rows if r.wan84_significant is not None and r.wan88_significant is not None
    ]
    if not judged:
        return "대조 가능한 셀이 없다(WAN-84 원자료 부재 또는 표본 부족)."
    old_sig = sum(1 for r in judged if r.wan84_significant)
    new_sig = sum(1 for r in judged if r.wan88_significant)
    gained = [r for r in judged if not r.wan84_significant and r.wan88_significant]
    lost = [r for r in judged if r.wan84_significant and not r.wan88_significant]
    parts = [
        f"대조 가능 {len(judged)}셀 중 유의 셀: WAN-84(숏 포함) **{old_sig}개** → "
        f"WAN-88(롱 온리) **{new_sig}개**."
    ]
    if gained:
        cells = ", ".join(f"{r.symbol.split('/')[0]}/{r.timeframe}/{r.segment}" for r in gained)
        parts.append(f"새로 유의해진 셀: {cells}.")
    if lost:
        cells = ", ".join(f"{r.symbol.split('/')[0]}/{r.timeframe}/{r.segment}" for r in lost)
        parts.append(f"유의성을 잃은 셀: {cells}.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# 리포트 산출
# --------------------------------------------------------------------------- #


def write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _fill_rate_note(rows: list[NullCellRow]) -> str:
    """체결 가정을 조이면 표본(유효 셀)이 얼마나 얕아지는지 — PM 지적 3번."""
    lines = []
    for fill in NULL_FILL_LEVELS:
        scoped = [r for r in rows if r.fill == fill]
        if not scoped:
            continue
        eligible = sum(1 for r in scoped if r.real_num_trades >= _MIN_TRADES_FOR_VERDICT)
        fill_rates = [r.fill_rate for r in scoped if r.fill_rate is not None]
        mean_fill = sum(fill_rates) / len(fill_rates) if fill_rates else None
        trades = sum(r.real_num_trades for r in scoped)
        lines.append(f"| {fill} | {len(scoped)} | {eligible} | {trades} | {_pct(mean_fill)} |")
    header = (
        "| fill | 전체 셀 | 유효 셀(n≥20) | 총 거래수 | 평균 체결률 |\n"
        "| -- | --: | --: | --: | --: |"
    )
    return header + "\n" + "\n".join(lines)


def build_summary_markdown(
    null_rows: list[NullCellRow],
    cost_rows: list[CostRow],
    buy_hold_rows: list[BuyHoldRow],
    contrast_rows: list[ContrastRow],
    *,
    null_report_path: Path,
    cost_report_path: Path,
    buy_hold_report_path: Path,
    contrast_report_path: Path,
) -> str:
    official = build_verdict(null_rows, fill=OFFICIAL_FILL)
    baseline = build_verdict(null_rows, fill="baseline")
    contrast_note = summarize_contrast(contrast_rows)
    return (
        "# WAN-88 롱 온리 채택 기본값 — 매칭 널·OOS 재검정\n\n"
        "3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS × 체결 가정(baseline/pen_5bp), "
        "로컬 `data/ohlcv.db` 실데이터 3년. 재현: "
        "`uv run python -m backtest.wan88_long_only_validation`.\n"
        f"원자료: `{null_report_path}`(매칭 널), `{cost_report_path}`(비용 민감도), "
        f"`{buy_hold_report_path}`(바이앤홀드), `{contrast_report_path}`(WAN-84 대조).\n\n"
        "## 이 리포트가 검정한 엔진\n\n"
        f"**WAN-88 당시 채택 기본값 그대로** — `{describe_engine()}` + 펀딩비 반영.\n"
        "전략 파라미터는 하나도 바꾸지 않았다(이 이슈는 검증 전용, 파라미터 탐색 금지).\n\n"
        "> 🔁 **지금은 채택 기본값과 오프셋 하나가 다르다** — "
        "[WAN-112](../../docs/decisions/wan112.md)가 `zone_limit_offset_bps`를 0.0 → 2.0(2bp)\n"
        "> 으로 올렸다. 이 검정의 **「유의 셀 0개」 판정은 0bp에서 나온 결론**이라, 숫자가\n"
        "> 기본값을 따라 조용히 움직이면 결론 문장과 어긋난다 — 그래서 `PINNED_OFFSET_BPS`로\n"
        "> 당시 엔진을 명시 고정했다. **이 리포트는 그 엔진의 기록이다.**\n\n"
        "> **공식 체결 기준선은 `pen_5bp`**([WAN-97](../../docs/decisions/wan97.md) 결정 1).\n"
        "> `baseline`(닿으면 체결)은 WAN-84와 같은 가정이라 **대조표의 축으로만** 싣는다 —\n"
        "> 단독 인용 금지. 최악 가정(`pen_5bp_drop_50`)은 널에서 뺐다: 탈락 추첨의 시드\n"
        "> 잡음 위에 부트스트랩 난수를 얹으면 p값이 엔진이 아니라 시드를 재게 된다\n"
        "> (모듈 docstring 「체결 가정」 참고).\n\n"
        "> **펀딩비를 넘겼다.** WAN-84가 재실행한 세 모듈은 `funding_rates`를 넘기지 않아\n"
        '> 펀딩 0으로 조용히 지나갔다(`funding_missing_policy="zero"`). 이 리포트는 실제·널\n'
        "> **양쪽에 동일하게** 펀딩을 붙인다 — 매칭 널이라 상당 부분 상쇄되지만, 롱 온리는\n"
        "> 펀딩을 지불하는 쪽이라 절대 수익률에는 남는다. CSV의 `funding_coverage` 참고.\n\n"
        "## 매칭 널 — 셀별 결과\n\n"
        f"{_null_table(null_rows)}\n\n"
        "`p` = 무작위 반복 중 실제 총수익률 이상을 낸 비율(단측). 95% CI는 무작위 분포의\n"
        "2.5~97.5 백분위수. 널 정의(방향·시각대를 맞춘 재표본추출)는\n"
        "`backtest/wan70_random_control_b.py` 모듈 docstring 참고 — 롱 온리 전략이므로\n"
        "널도 롱 온리다(`real_short=0`).\n\n"
        "## 체결 가정이 표본에 미치는 영향\n\n"
        f"{_fill_rate_note(null_rows)}\n\n"
        "## 판정\n\n"
        f"- **공식 기준선**: {official}\n"
        f"- 참고(WAN-84 대조축): {baseline}\n\n"
        f"{summarize_fill_dependence(null_rows)}\n\n"
        "## WAN-84(숏 포함) 대조표\n\n"
        "⚠️ **방법론 차이를 먼저 읽을 것.** 두 열은 같은 자(거래 20건 이상, p≤0.05 &\n"
        "실제>무작위평균)로 쟀지만 엔진이 세 가지 다르다: (1) 숏 포함 → 롱 온리(WAN-87),\n"
        "(2) 교정 전 → 「첫 탭 면제」 배선 후(WAN-100), (3) 펀딩 미반영 → 반영(WAN-88).\n"
        '따라서 이 표는 **"숏만 뺐을 때의 순효과"가 아니라 "채택 설정이 WAN-84 이후\n'
        '움직인 총효과"** 다. 체결 가정만은 맞췄다(양쪽 `baseline`).\n\n'
        '🚨 **이 표의 WAN-88 열은 `baseline`이라 공식 판정이 아니다.** 아래 "유의 셀이\n'
        '늘었다"는 요약을 **단독으로 인용하면 정확히 반대의 결론에 이른다** — 공식\n'
        "기준선(`pen_5bp`)에서 유의 셀은 **0개**이고, 여기서 늘어난 셀은 전부 위 「판정」\n"
        '절이 지목한 **스치듯 닿은 체결에 기댄 15m 셀**이다. 이 표는 "WAN-84 이후 무엇이\n'
        '움직였나"를 보는 용도이지 **채택 근거가 아니다**.\n\n'
        f"{_contrast_table(contrast_rows)}\n\n"
        f"{contrast_note}\n\n"
        "## 비용 민감도 (공식 기준선 `pen_5bp`, 동일 후보 재시퀀싱)\n\n"
        f"{_cost_table(cost_rows)}\n\n"
        "배율은 테이커·메이커·슬리피지에 모두 걸린다. WAN-84에서 BTC 1h OOS가 +5.0% →\n"
        "−4.1%로 음전환한 취약성이 롱 온리에서도 남는지 보는 표다(작업범위 4번째 항목).\n\n"
        "## 바이앤홀드 벤치마크\n\n"
        f"{_buy_hold_table(buy_hold_rows)}\n\n"
        "## 결론\n\n"
        f"{build_conclusion(null_rows, contrast_rows)}\n"
    )


def build_conclusion(rows: list[NullCellRow], contrast_rows: list[ContrastRow]) -> str:
    """ "엣지 있음/없음/판정 불가" 중 하나를 명시하고 근거 수치를 인용한다."""
    verdict = build_verdict(rows, fill=OFFICIAL_FILL)
    scoped = [r for r in rows if r.fill == OFFICIAL_FILL and r.random_p_value is not None]
    eligible = [r for r in scoped if r.real_num_trades >= _MIN_TRADES_FOR_VERDICT]
    sig = [r for r in eligible if is_significant(r)]
    oos_eligible = [r for r in eligible if r.segment == "OOS"]
    oos_sig = [r for r in oos_eligible if is_significant(r)]

    if not eligible:
        headline = (
            "**판정 불가(표본 부족)** — 공식 기준선에서 거래 "
            f"{_MIN_TRADES_FOR_VERDICT}건 이상인 셀이 하나도 없다."
        )
    elif not sig:
        headline = (
            "**엣지 없다** — 공식 기준선(`pen_5bp`)에서 유효 셀 "
            f"{len(eligible)}개 중 p≤0.05 & 실제>무작위평균인 셀은 **0개**다. "
            "숏을 뺀 뒤에도 WAN-84의 '진입 타이밍에 엣지 없음' 결론이 유지된다."
        )
    elif len(sig) == len(eligible):
        headline = (
            f"**엣지 있다** — 공식 기준선에서 유효 셀 {len(eligible)}개가 **전부** "
            "p≤0.05 & 실제>무작위평균이다."
        )
    else:
        cells = ", ".join(f"{r.symbol.split('/')[0]}/{r.timeframe}/{r.segment}" for r in sig)
        headline = (
            f"**일부 셀에만 엣지가 있다** — 공식 기준선 유효 셀 {len(eligible)}개 중 "
            f"{len(sig)}개가 유의하다({cells}). 전 셀 일관이 아니므로 "
            "'엣지 확인'으로 읽으면 안 된다."
        )
    oos_line = (
        f"OOS만 보면 유효 셀 {len(oos_eligible)}개 중 유의 {len(oos_sig)}개다 — "
        "채택 판단은 이 줄이 기준이다(IS 유의는 과최적화와 구분되지 않는다)."
        if oos_eligible
        else f"OOS 유효 셀(거래 {_MIN_TRADES_FOR_VERDICT}건 이상)이 없어 OOS 판정은 불가하다."
    )
    contrast_line = (
        f"{summarize_contrast(contrast_rows)} **단, 이 대조는 `baseline` 축이라 공식 판정이 "
        "아니다** — 위 「판정」 절의 경고를 함께 읽을 것."
    )
    return "\n\n".join(
        [
            headline,
            oos_line,
            verdict,
            summarize_fill_dependence(rows),
            contrast_line,
            _implications(rows),
        ]
    )


def _implications(rows: list[NullCellRow], *, working_tf: str = "1h") -> str:
    """이 결과가 말하는 것 / 말하지 않는 것 — 리포트가 결론에서 멈추지 않게.

    작업 TF(WAN-97 결정 2의 1h) OOS 수치는 **행에서 계산한다**. 문장에 숫자를 박아 두면
    데이터·엔진이 움직인 뒤에도 옛 숫자가 그대로 남아 리포트가 조용히 거짓말을 한다 —
    이 저장소가 WAN-96/99에서 겪은 "결론은 살아남고 근거는 죽는" 사고의 축소판이다.
    """
    tf_oos = sorted(
        (
            r
            for r in rows
            if r.fill == OFFICIAL_FILL and r.timeframe == working_tf and r.segment == "OOS"
        ),
        key=lambda r: r.symbol,
    )
    if tf_oos:
        detail = ", ".join(f"{r.symbol.split('/')[0]} {_pct(r.real_total_return)}" for r in tf_oos)
        plus = sum(1 for r in tf_oos if r.real_total_return > 0)
        tf_line = (
            f"공식 기준선 {working_tf} OOS는 {plus}/{len(tf_oos)}심볼이 플러스다({detail}). "
            "다만 그 수익이 **같은 오더블록에 무작위로 진입해도 나오는 수준**이라, 우리가 값을 "
            "매기는 대상은 RSI·타이밍 규칙이 아니라 오더블록 존 자체이거나 시장 베타일 수 있다."
        )
    else:
        tf_line = f"작업 TF({working_tf}) OOS 셀이 없어 수익률 수준은 이 리포트에서 말하지 않는다."
    return (
        "### 이 결과가 말하는 것 / 말하지 않는 것\n\n"
        "- **말하는 것**: 채택 기본값(롱 온리 + 지정가 + 첫 탭 면제)의 진입 타이밍에 "
        "매칭 널 대비 유의한 엣지가 있다는 증거는 **공식 기준선에서 없다**. WAN-87의 숏 "
        "제거는 절대 수익률을 크게 끌어올렸지만(대조표의 수익 열: 음수 셀이 대거 양전환) "
        "**통계적 판정은 바꾸지 못했다** — 숏이 롱의 엣지를 가리고 있던 게 아니라, 드러날 "
        "엣지가 애초에 없었다는 뜻이다.\n"
        "- **말하는 것**: [WAN-97](../../docs/decisions/wan97.md)의 **15m 제외 결정을 독립적인 "
        "지표로 재확인**한다. WAN-97은 수익률의 관통 민감도(10.3×)를 근거로 15m을 뺐는데, 이 "
        "리포트는 **유의성 자체가 관통 요구 한 번에 전부 사라진다**는 걸 보였다 — 다른 지표, "
        "같은 결론이다.\n"
        f'- **말하지 않는 것**: "전략이 손실이다"라고 말하지 않는다. {tf_line}\n'
        "- **말하지 않는 것**: 실거래 승인이 아니다. `ALPHABLOCK_LIVE_TRADING` 기본값 "
        "`false`는 그대로다([WAN-86 결정 2](../../docs/decisions/wan86.md))."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-88 롱 온리 채택 기본값 매칭 널·OOS 재검정",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--iterations", type=int, default=_BOOTSTRAP_ITERATIONS)
    parser.add_argument(
        "--fills",
        nargs="+",
        default=list(NULL_FILL_LEVELS),
        help="널을 돌릴 체결 가정(기본: baseline pen_5bp).",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=_default_jobs(),
        help="심볼 단위 병렬 워커 수(기본 cpu_count()-1). 1이면 순차 실행.",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="1분봉 parquet 캐시(data/cache/)를 쓰지 않는다."
    )
    parser.add_argument("--null-out", type=Path, default=REPORTS_DIR / "wan88_matched_null.csv")
    parser.add_argument("--cost-out", type=Path, default=REPORTS_DIR / "wan88_cost_sensitivity.csv")
    parser.add_argument("--buy-hold-out", type=Path, default=REPORTS_DIR / "wan88_buy_hold.csv")
    parser.add_argument(
        "--contrast-out", type=Path, default=REPORTS_DIR / "wan88_wan84_contrast.csv"
    )
    parser.add_argument(
        "--summary-out", type=Path, default=REPORTS_DIR / "wan88_long_only_summary.md"
    )
    args = parser.parse_args(argv)

    result = run_experiment(
        db_path=args.db,
        symbols=tuple(args.symbols),
        timeframes=tuple(args.timeframes),
        years=args.years,
        iterations=args.iterations,
        jobs=args.jobs,
        cache_dir=None if args.no_cache else DEFAULT_CACHE_DIR,
        fill_levels=tuple(args.fills),
    )
    contrast_rows = build_contrast(result.null_rows, load_wan84_rows())

    write_csv(_rows_to_frame(result.null_rows), args.null_out)
    write_csv(_rows_to_frame(result.cost_rows), args.cost_out)
    write_csv(_rows_to_frame(result.buy_hold_rows), args.buy_hold_out)
    write_csv(_rows_to_frame(contrast_rows), args.contrast_out)
    print(
        f"[wan88] null={len(result.null_rows)}행 → {args.null_out}, "
        f"cost={len(result.cost_rows)}행 → {args.cost_out}, "
        f"buy_hold={len(result.buy_hold_rows)}행 → {args.buy_hold_out}, "
        f"contrast={len(contrast_rows)}행 → {args.contrast_out}"
    )

    summary = build_summary_markdown(
        result.null_rows,
        result.cost_rows,
        result.buy_hold_rows,
        contrast_rows,
        null_report_path=args.null_out,
        cost_report_path=args.cost_out,
        buy_hold_report_path=args.buy_hold_out,
        contrast_report_path=args.contrast_out,
    )
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan88] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
