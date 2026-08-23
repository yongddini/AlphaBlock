"""WAN-278: 무효화 경계에 작은 손절 버퍼 — 가짜 이탈(shakeout)을 버티고 회복을 잡는가.

## 질문 (사용자 아이디어 2026-08-10, WAN-260 논의 파생)

현행 채택 엔진의 손절은 **진입 근거 오더블록의 무효화(breaker) 경계**다. 롱 기준으로 가격이
그 경계에 닿으면 손절된다. 사용자 관찰: 무효화를 **살짝 뚫고 내려갔다가 다시 올라오는**(가짜
이탈 / 흔들어 털기) 경우가 있는데, 지금은 그 작은 관통에 손절당해 이후 회복을 못 먹는다.

이 이슈는 무효화 경계 바로 아래에 **작은 완충폭(버퍼)만** 둬서 흔들기에 안 털리고 버텨서
**원래 익절 목표를 먹는** 방식을 잰다.

⚠️ **WAN-260(손절폭 비례 확대)과 다른 축이다** — WAN-260은 손절 거리를 k배로 넓히면 익절
목표도 같은 배수로 멀어진다. 이 이슈는 **버퍼가 손절만 조금 밀고 익절 목표는 원래 무효화 기준
1.5R에 고정**한다(손익비를 일부러 살짝 나쁘게 = "숨 쉴 공간만 준다").

## 왜 결과가 갈리나 — 사전 가설

버퍼는 두 가지를 맞바꾼다: (1) **구제(＋)** — 버퍼가 없었으면 흔들기에 손절났을 거래가
살아남아 원래 익절 목표에 도달. (2) **더 깊은 손절(−)** — 회복 안 하고 계속 내려가는 거래는
버퍼만큼 더 깊은 지점에서 손절나 손실이 커진다. **순효과는 가짜 이탈이 실제로 평균회귀하느냐**
에 달렸다 — 진짜 반전이면 구제 이득 > 더 깊은 손절 대가 = 순플러스, 노이즈면 무승부~소폭 손해.

## 배선 (측정 전용 · 옵트인 · 엔진 기본값 불변)

* `build_zone_limit_candidates(stop_loss_override=..., take_profit_override=...)`(WAN-143 배선 ·
  WAN-277 재확인)로 손절만 버퍼만큼 밀고 익절은 원래 무효화 기준 1.5R에 **고정**한다.
  - **손절 오버라이드**: `default_stop ∓ 버퍼`(롱은 아래로, 숏은 위로). `default_stop`은
    `StopLossContext`가 주는 원래 무효화 경계(`seed_ob.bottom`/`top`)다.
  - **익절 오버라이드**: `entry ± 1.5 × (entry − 원래 무효화 경계)`. `TakeProfitContext`의
    `order_block`이 곧 그 원래 경계라, 버퍼로 밀린 손절이 아니라 **원래 1R**로 목표를 잡는다.
* **버퍼 = 0은 오버라이드를 아예 안 건다**(override=None) — 그래서 현행 채택 엔진의
  **정확한 프로덕션 경로**이자 `harness.run_once`와 비트 일치한다(검산 기준점).

## 버퍼 축 (정본 단위 = ATR 배수)

무효화 관통 흔들기가 변동성에 비례하므로 **ATR 배수를 정본 단위**로 확정했다(이슈가 권장한
축). ATR은 탭 봉 **직전 확정봉**(`pos-1`)에서 읽는다 — 탭 봉 자신의 ATR은 그 봉 종가를 알아야
나오므로 룩어헤드다(존폭 필터 WAN-158과 같은 규칙). `--unit r_fraction`으로 **1R 분수** 민감도
축(`{0, 0.1, 0.2, 0.3}R`)도 병기할 수 있다.

## 핵심 분해 열 (救出률 · 순효과)

버퍼 후보는 손절만 바꾸므로 체결(진입) 셋업 집합이 버퍼 0과 **비트 일치**한다(엔진 계약). 그래서
**셋업 단위로** 버퍼 0 팔과 버퍼 팔의 후보를 위치로 짝지어 분해한다:

* **救出률** — 버퍼 0이었으면 손절났을(`would_be_stopped`) 셋업 중 버퍼 덕에 (a) 익절 도달
  (`rescued_tp`) · (b) 결국 더 깊은 손절(`deeper_sl`) · (c) 미청산(`unclosed_eod`).
* **순효과** — 그 셋업들의 (버퍼 실현 R − 버퍼0 실현 R) 합(`rescue_net_r`, **원래 1R 단위**).
  구제로 살아 익절한 R − 안 살아난 거래의 추가 손실 R = net.

⚠️ 손익 표(총수익·MDD·승률)는 **시퀀싱된 거래**(per-cell 단일 포지션)로 내고, 분해는
**셋업 단위**(시퀀싱 이전, 슬롯 충돌 무관)로 낸다 — "이 셋업이 숨 쉴 공간을 얻으면 살아나나"가
분해의 질문이라 단일 포지션 슬롯 경합과 섞으면 안 된다.

## 좌표

9종목 · 못 박은 6년(WAN-182) · 15m·1h·2h·4h(WAN-252) · full/is/oos_warm/oos(WAN-166) ·
**핀 없음**(`OrderBlockParams()` = 오늘 엔진 · 재진입 ON band 반영) · 렌즈 `baseline`+`pen_5bp`
(재탭 지정가라 관통 벌점 유의) · per-cell 단일 포지션(WAN-260 골격) · 신규 3종목 펀딩 대리.

## ⚠️ 경고

* 전부 `baseline`(닿으면 체결) 낙관 위 값 — 버퍼 손절 체결 자체가 "닿으면 체결"이다.
  `pen_5bp` 병기하되 큐 우선순위 미모델(WAN-98 Canceled).
* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이건 거래 관리(위험의 모양)이지
  진입 규칙 알파 주장이 아니다(WAN-90 계열). 순 net이 플러스면 "가짜 이탈 평균회귀" 패턴의
  방증이나 **매칭 널(무작위 대조)은 아니다**(후속).
* WAN-79 손절폭 가드(0.3%)와 충돌 없음 — 버퍼는 손절을 **멀리** 미는데 가드는 **좁은** 손절만
  막는다(오히려 가드가 덜 문다). 가드 변경 제안 아님.
* 채택(버퍼를 기본값으로)은 재-베이스라인 = 사용자 결정 · 개발자 임의 착수 금지.

재현:

```
uv run python -m backtest.wan278_stop_buffer --tf 4h            # 가벼운 것부터
uv run python -m backtest.wan278_stop_buffer --tf 1h --append
uv run python -m backtest.wan278_stop_buffer --tf 2h --append
uv run python -m backtest.wan278_stop_buffer --tf 15m --append  # 무겁다(WAN-203 봉내 밴드 비용)
uv run python -m backtest.wan278_stop_buffer --unit r_fraction --tf 4h  # 1R 분수 민감도 축
uv run python -m backtest.wan278_stop_buffer --from-csv         # 요약만 재생성
uv run python -m backtest.wan278_stop_buffer --checksum         # 버퍼0 팔 ≡ run_once
```
"""

from __future__ import annotations

import argparse
import math
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import (
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    FillPreset,
    MarketData,
    Segment,
    segments_for,
)
from backtest.models import BacktestConfig, ExitReason, PositionSide, Trade
from backtest.run import parse_date_ms
from backtest.wan95_zone_limit_report import apply_funding_proxy
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    StopLossContext,
    StopLossOverride,
    TakeProfitContext,
    TakeProfitOverride,
    ZoneLimitStats,
    _Candidate,
    _prepare_htf,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate
from strategy.indicators import atr
from strategy.models import OrderBlockParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

REPORTS_DIR = Path("backtest/reports")

#: 렌즈 축 — 공식(baseline) + 스트레스(pen_5bp). 재탭 지정가라 관통 벌점이 유의해 이슈가
#: 명시적으로 `pen_5bp` 병기를 요구했다.
LENS_BASELINE = "baseline"
LENS_PEN = "pen_5bp"
LENSES: tuple[str, ...] = (LENS_BASELINE, LENS_PEN)

#: 버퍼 축(정본 단위 = ATR 배수). 0 = 채택 기본값 = 검산 기준점(오버라이드 없음).
UNIT_ATR = "atr"
UNIT_R = "r_fraction"
BUFFERS_ATR: tuple[float, ...] = (0.0, 0.1, 0.25, 0.5)
BUFFERS_R: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3)

#: ATR 길이 — 존폭 필터(WAN-158, `zone_width_atr_length=14`)와 같은 기본값. 버퍼가 존폭 필터
#: 뒤에 얹히므로 같은 변동성 자를 쓰는 것이 자연스럽다.
ATR_LENGTH = 14

#: 익절 배수(채택값, WAN-81/90). 원래 무효화 기준 1R × 이 배수에 목표를 고정한다.
TP_MULTIPLE = 1.5

#: WAN-79 채택 가드 — 축이 아니라 오늘 기본값으로 고정한다(`build_config`가 주는 0.003).
GUARD = 0.003

SEGMENT_ORDER: tuple[str, ...] = ("full", SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 판정의 주 구간(WAN-166) = 따뜻한 연속 OOS. 차가운 `oos`는 스트레스로 병기한다.
PRIMARY_OOS = SEGMENT_OOS_WARM
STRESS_OOS = SEGMENT_OOS

MIN_STOPS_PER_SYMBOL = 20
"""버퍼가 건드리는 표본 게이트 — 버퍼 0이었으면 손절났을 셋업이 심볼당 이보다 적으면 판정하지
않는다(WAN-84 유효 기준의 손절-표본 판). 救出률·순효과는 이 표본 위에서만 의미가 있다."""


def buffers_for(unit: str) -> tuple[float, ...]:
    return BUFFERS_R if unit == UNIT_R else BUFFERS_ATR


def buffer_label(unit: str, buffer: float) -> str:
    return f"{buffer:.2f}{'R' if unit == UNIT_R else '·ATR'}"


# --------------------------------------------------------------------------- #
# 손절 버퍼 오버라이드 — 손절만 밀고 익절은 원래 1R에 고정
# --------------------------------------------------------------------------- #


def _atr_prev_by_time(htf_df: pd.DataFrame) -> dict[int, float]:
    """탭 봉 **직전 확정봉**(pos-1)의 ATR를 상위TF 봉 시각으로 키잉한다.

    엔진이 존폭 필터에서 `atr(frame, ...)[pos-1]`를 읽는 것과 **정확히 같은 위치**다
    (`_prepare_htf`로 같은 프레임을 만들고 같은 `atr` 헬퍼를 쓴다). 워밍업 NaN은 담지
    않는다 — 그 셋업은 ATR 버퍼를 못 재므로 버퍼 0으로 떨어진다(정직한 폴백)."""
    frame = _prepare_htf(htf_df)
    if len(frame) == 0:
        return {}
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    atr_vals = [float(v) for v in atr(frame, length=ATR_LENGTH).tolist()]
    out: dict[int, float] = {}
    for i in range(1, len(times)):
        prev = atr_vals[i - 1]
        if math.isfinite(prev) and prev > 0:
            out[times[i]] = prev
    return out


def make_buffer_overrides(
    unit: str,
    buffer: float,
    atr_prev_by_time: dict[int, float],
) -> tuple[StopLossOverride, TakeProfitOverride]:
    """버퍼 손절 오버라이드 + 원래 1R 고정 익절 오버라이드를 만든다.

    손절 = 원래 무효화 경계 ∓ 버퍼. 익절 = 진입가 ± 1.5 × (진입가 − 원래 무효화 경계) —
    버퍼로 밀린 손절이 아니라 **원래 1R**로 목표를 잡는 것이 이 이슈의 핵심이다(WAN-260과
    갈리는 지점).
    """

    def _distance(is_long: bool, entry: float, orig_stop: float, trigger_time: int) -> float:
        if unit == UNIT_R:
            one_r = (entry - orig_stop) if is_long else (orig_stop - entry)
            return buffer * one_r if one_r > 0 else 0.0
        atr_prev = atr_prev_by_time.get(trigger_time)
        return buffer * atr_prev if atr_prev is not None else 0.0

    def stop_override(ctx: StopLossContext) -> float | None:
        orig_stop = ctx.default_stop  # = seed_ob.bottom/top (원래 무효화 경계)
        dist = _distance(ctx.is_long, ctx.entry_price, orig_stop, ctx.trigger_time)
        new_stop = orig_stop - dist if ctx.is_long else orig_stop + dist
        # 버퍼는 손절을 진입가에서 **멀리** 미므로 유효 장벽이 깨질 일이 없다. 극단적으로
        # new_stop이 0 이하로 내려가는 병리(초저가 자산·거대 버퍼)에서만 원래 경계로 폴백해
        # 체결(진입) 셋업 집합이 버퍼 0과 비트 일치하도록 지킨다(None을 돌려주면 미체결이 된다).
        if new_stop <= 0:
            return orig_stop
        return new_stop

    def tp_override(ctx: TakeProfitContext) -> float | None:
        orig_stop = ctx.order_block.bottom if ctx.is_long else ctx.order_block.top
        one_r = (ctx.entry_price - orig_stop) if ctx.is_long else (orig_stop - ctx.entry_price)
        if one_r <= 0:
            return None  # 유효 1R을 못 재면 익절 목표 없음(정적 경로의 폴백과 같은 관행).
        signed = TP_MULTIPLE * one_r
        return ctx.entry_price + (signed if ctx.is_long else -signed)

    return stop_override, tp_override


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class BufferRow(BaseModel):
    """한 (심볼, TF, 구간, 렌즈, 버퍼) 셀 — 손익(시퀀싱) + 救出 분해(셋업 단위)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    lens: str
    unit: str
    buffer: float
    eligible: int
    filled: int
    """체결 셋업 수(시퀀싱 이전). 같은 렌즈·구간이면 버퍼와 무관하게 같아야 한다(엔진 계약)."""
    num_trades: int
    fill_rate: float | None
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    mean_gross_r: float | None
    n_take_profit: int
    n_stop_loss: int
    n_end_of_data: int
    # ---- 救出 분해(셋업 단위, 버퍼 0 대비). 버퍼 0 행은 전부 0/0.0. ----
    would_be_stopped: int
    """버퍼 0이었으면 손절났을 셋업 수(버퍼가 건드리는 표본)."""
    rescued_tp: int
    """그중 버퍼 덕에 원래 익절 목표에 도달한 수(구제)."""
    deeper_sl: int
    """그중 결국 더 깊은 손절로 끝난 수."""
    unclosed_eod: int
    """그중 데이터 종료까지 미청산인 수."""
    rescue_net_r: float
    """그 셋업들의 (버퍼 실현 R − 버퍼0 실현 R) 합(원래 1R 단위) = 구제 − 더 깊은 손절."""

    @property
    def return_over_mdd(self) -> float | None:
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown


@dataclass(frozen=True)
class Decomposition:
    would_be_stopped: int
    rescued_tp: int
    deeper_sl: int
    unclosed_eod: int
    net_r: float


_ZERO_DECOMP = Decomposition(0, 0, 0, 0, 0.0)


@dataclass(frozen=True)
class ArmOutcome:
    candidates: list[_Candidate]
    """평가 창 후보(체결 셋업, 시퀀싱 이전). 버퍼가 손절만 바꾸므로 버퍼 0과 진입 집합이
    비트 일치한다 — 救出 분해가 이 리스트를 버퍼 0 팔과 위치로 짝짓는다."""
    paired: list[tuple[_Candidate, Trade]]
    eligible: int
    filled: int
    fill_rate: float | None


def _gross_r(cand: _Candidate) -> float | None:
    """비용 반영 전 실현 R = 부호(청산가 − 진입가) / 1R(그 후보의 손절 기준). 1R을 못 재면 None.

    ⚠️ 버퍼 팔에서 `cand.stop_price`는 **버퍼로 밀린 손절**이라 여기 R은 실제 감수한 리스크
    기준이다(사이징이 본 1R). 救出 순효과의 「원래 1R 단위」와는 다른 자다 — 그건
    `_original_r`로 따로 잰다."""
    risk = (
        cand.entry_price - cand.stop_price
        if cand.side is PositionSide.LONG
        else cand.stop_price - cand.entry_price
    )
    if risk <= 0:
        return None
    return cand.side.sign * (cand.exit_price - cand.entry_price) / risk


def _original_r(cand: _Candidate, orig_stop: float) -> float | None:
    """원래 무효화 경계(`orig_stop`) 기준 실현 R — 버퍼 팔·버퍼0 팔을 같은 자로 비교한다."""
    risk = (
        cand.entry_price - orig_stop
        if cand.side is PositionSide.LONG
        else orig_stop - cand.entry_price
    )
    if risk <= 0:
        return None
    return cand.side.sign * (cand.exit_price - cand.entry_price) / risk


def decompose(base: Sequence[_Candidate], buffered: Sequence[_Candidate]) -> Decomposition:
    """버퍼 0(`base`)과 버퍼 팔(`buffered`)의 후보를 위치로 짝지어 救出을 분해한다.

    두 리스트는 진입 셋업 집합이 비트 일치하므로(손절 오버라이드는 진입을 안 바꾼다) 위치가
    같은 셋업을 가리킨다 — 진입 시각·가격이 어긋나면 배선 버그라 `AssertionError`로 멈춘다.
    `base`의 청산 사유가 손절인 셋업만(= 버퍼가 건드릴 수 있는 것) 센다.
    """
    if len(base) != len(buffered):
        raise AssertionError(
            f"버퍼 후보 수가 버퍼0과 다르다({len(buffered)} vs {len(base)}) — "
            "손절 오버라이드가 진입 집합을 바꿨다는 뜻이라 배선 버그다."
        )
    would = rescued = deeper = unclosed = 0
    net = 0.0
    for b, x in zip(base, buffered, strict=True):
        if b.entry_time != x.entry_time or b.trigger_time != x.trigger_time:
            raise AssertionError(
                "버퍼 후보 진입 셋업이 버퍼0과 어긋난다 — 위치 정렬이 깨졌다(배선 버그)."
            )
        if b.reason is not ExitReason.STOP_LOSS:
            continue
        # 버퍼0 팔은 오버라이드가 없어 `stop_price`가 곧 원래 무효화 경계다.
        orig_stop = b.stop_price
        would += 1
        if x.reason is ExitReason.TAKE_PROFIT:
            rescued += 1
        elif x.reason is ExitReason.STOP_LOSS:
            deeper += 1
        else:
            unclosed += 1
        base_r = _original_r(b, orig_stop)
        buf_r = _original_r(x, orig_stop)
        if base_r is not None and buf_r is not None:
            net += buf_r - base_r
    return Decomposition(would, rescued, deeper, unclosed, net)


def build_row(
    market: MarketData,
    segment: str,
    lens: str,
    unit: str,
    buffer: float,
    outcome: ArmOutcome,
    decomp: Decomposition,
    cfg: BacktestConfig,
) -> BufferRow:
    trades = [t for _, t in outcome.paired]
    metrics = build_result_from_trades(trades, cfg, market.timeframe).metrics
    reasons = Counter(cand.reason for cand, _ in outcome.paired)
    grs = [g for g in (_gross_r(cand) for cand, _ in outcome.paired) if g is not None]
    return BufferRow(
        symbol=market.symbol,
        timeframe=market.timeframe,
        segment=segment,
        lens=lens,
        unit=unit,
        buffer=buffer,
        eligible=outcome.eligible,
        filled=outcome.filled,
        num_trades=metrics.num_trades,
        fill_rate=outcome.fill_rate,
        total_return=metrics.total_return,
        max_drawdown=metrics.max_drawdown,
        win_rate=metrics.win_rate,
        sharpe=metrics.sharpe,
        mean_gross_r=statistics.fmean(grs) if grs else None,
        n_take_profit=reasons.get(ExitReason.TAKE_PROFIT, 0),
        n_stop_loss=reasons.get(ExitReason.STOP_LOSS, 0),
        n_end_of_data=reasons.get(ExitReason.END_OF_DATA, 0),
        would_be_stopped=decomp.would_be_stopped,
        rescued_tp=decomp.rescued_tp,
        deeper_sl=decomp.deeper_sl,
        unclosed_eod=decomp.unclosed_eod,
        rescue_net_r=decomp.net_r,
    )


# --------------------------------------------------------------------------- #
# 팔 실행
# --------------------------------------------------------------------------- #


def run_arm(
    seg_market: MarketData,
    *,
    params_fill: FillPreset,
    unit: str,
    buffer: float,
    atr_prev_by_time: dict[int, float],
    obr: OrderBlockResult,
    eval_from_ms: int | None,
) -> ArmOutcome:
    """한 (렌즈, 버퍼) 팔을 seg_market에서 돈다.

    버퍼 0이면 오버라이드를 걸지 않는다 — 그래서 현행 채택 엔진의 프로덕션 경로 그대로다
    (`harness.run_once`와 비트 일치, 검산이 고정). 버퍼 > 0이면 손절 버퍼 + 원래 1R 고정
    익절 오버라이드를 건다.

    `eval_from_ms`(따뜻한 연속 OOS, WAN-166)를 주면 `run_zone_limit_backtest_verbose`와 같은
    절차로 후보를 평가 창으로 좁힌다(WAN-206과 동일).
    """
    params = harness.build_params(
        entry_mode="zone_limit", take_profit_r=TP_MULTIPLE, fill=params_fill
    )
    cfg = harness.build_config(seg_market.timeframe)
    stop_override: StopLossOverride | None = None
    tp_override: TakeProfitOverride | None = None
    if buffer > 0:
        stop_override, tp_override = make_buffer_overrides(unit, buffer, atr_prev_by_time)
    sink: list[SetupDiagnostic] = []
    candidates, stats = build_zone_limit_candidates(
        seg_market.htf_df,
        seg_market.df_1m,
        seg_market.timeframe,
        params=harness.pin_invalidation_cancel(params),
        cfg=cfg,
        order_block_result=obr,
        setup_sink=sink,
        stop_loss_override=stop_override,
        take_profit_override=tp_override,
    )
    if eval_from_ms is not None:
        candidates = [c for c in candidates if c.trigger_time >= eval_from_ms]
        kept = [d for d in sink if d.trigger_time >= eval_from_ms]
        stats = ZoneLimitStats(
            eligible=len(kept),
            filled=sum(1 for d in kept if d.filled),
            penetrations=sum(1 for c in candidates if c.penetration),
            dropped=sum(1 for d in kept if d.dropped),
        )
    paired = sequence_with_candidates(candidates, cfg, seg_market.funding_rates)
    return ArmOutcome(
        candidates=candidates,
        paired=paired,
        eligible=stats.eligible,
        filled=stats.filled,
        fill_rate=stats.fill_rate,
    )


# --------------------------------------------------------------------------- #
# 격자
# --------------------------------------------------------------------------- #


def run_cell(
    market: MarketData,
    segment: Segment,
    *,
    unit: str = UNIT_ATR,
    lenses: Sequence[str] = LENSES,
) -> list[BufferRow]:
    """한 (심볼, TF, 구간)의 렌즈 × 버퍼를 돈다.

    OB 탐지·ATR 맵은 셀에서 한 번(공유). 각 렌즈에서 버퍼 0 팔을 먼저 돌아 救出 분해의
    기준점으로 잡고, 버퍼 > 0 팔은 그 후보와 위치로 짝지어 분해한다.
    """
    seg_market = harness.slice_market(market, segment)
    if seg_market.empty or seg_market.df_1m.empty:
        return []
    eval_from_ms = harness.eval_boundary_ms(market, segment)
    # ⚠️ 밴드·병합·필터를 고정하지 않는다 — 채택 기본값(`intrabar_live` WAN-132 · 필터 1.28
    # WAN-159 · `combine_obs=False` WAN-149) 위에서 재는 것이 이 이슈의 요구다(핀 금지).
    obr: OrderBlockResult = OrderBlockDetector(OrderBlockParams()).run(seg_market.htf_df)
    atr_prev_by_time = _atr_prev_by_time(seg_market.htf_df) if unit == UNIT_ATR else {}
    cfg = harness.build_config(seg_market.timeframe)

    rows: list[BufferRow] = []
    buffers = buffers_for(unit)
    for lens_name in lenses:
        fill = harness.fill_preset(lens_name)
        base_outcome = run_arm(
            seg_market,
            params_fill=fill,
            unit=unit,
            buffer=0.0,
            atr_prev_by_time=atr_prev_by_time,
            obr=obr,
            eval_from_ms=eval_from_ms,
        )
        rows.append(
            build_row(
                seg_market, segment.name, lens_name, unit, 0.0, base_outcome, _ZERO_DECOMP, cfg
            )
        )
        for buffer in buffers:
            if buffer == 0.0:
                continue
            outcome = run_arm(
                seg_market,
                params_fill=fill,
                unit=unit,
                buffer=buffer,
                atr_prev_by_time=atr_prev_by_time,
                obr=obr,
                eval_from_ms=eval_from_ms,
            )
            decomp = decompose(base_outcome.candidates, outcome.candidates)
            rows.append(
                build_row(seg_market, segment.name, lens_name, unit, buffer, outcome, decomp, cfg)
            )
    return rows


def run_report(
    symbols: Sequence[str] = harness.LEGACY_NINE_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    unit: str = UNIT_ATR,
    lenses: Sequence[str] = LENSES,
    funding_proxy: bool = True,
    log: bool = True,
) -> list[BufferRow]:
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    segments = segments_for(warm_oos=True)
    rows: list[BufferRow] = []
    for timeframe in timeframes:
        markets: dict[str, MarketData] = {}
        funding_by_symbol: dict[str, list[FundingRate]] = {}
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan278] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            markets[sym] = market
            funding_by_symbol[sym] = market.funding_rates
        if funding_proxy:
            funding_by_symbol, note = apply_funding_proxy(funding_by_symbol)
            if note and log:
                print(f"[wan278] {note}", flush=True)
            markets = {sym: _with_funding(m, funding_by_symbol[sym]) for sym, m in markets.items()}
        for sym, market in markets.items():
            for segment in segments:
                t0 = time.time()
                cell = run_cell(market, segment, unit=unit, lenses=lenses)
                rows.extend(cell)
                if log:
                    print(
                        f"[wan278] {sym} {timeframe} {segment.name}: "
                        f"{len(cell)}행 ({time.time() - t0:.0f}s)",
                        flush=True,
                    )
    return rows


def _with_funding(market: MarketData, funding: list[FundingRate]) -> MarketData:
    from dataclasses import replace

    return replace(market, funding_rates=funding)


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def _subset(
    frame: pd.DataFrame, timeframe: str, segment: str, lens: str, buffer: float
) -> pd.DataFrame:
    return frame[
        (frame["timeframe"] == timeframe)
        & (frame["segment"] == segment)
        & (frame["lens"] == lens)
        & (frame["buffer"] == buffer)
    ]


def pooled(
    frame: pd.DataFrame, timeframe: str, segment: str, lens: str, buffer: float
) -> dict[str, float | None]:
    """심볼평균 — 수익률·MDD 등은 단순평균, 거래·청산 사유·救出은 합."""
    sub = _subset(frame, timeframe, segment, lens, buffer)
    if sub.empty:
        return {}

    def avg(col: str) -> float | None:
        vals = sub[col].astype(float).dropna()
        return float(vals.mean()) if len(vals) else None

    ret, mdd = avg("total_return"), avg("max_drawdown")
    tp = int(sub["n_take_profit"].sum())
    sl = int(sub["n_stop_loss"].sum())
    eod = int(sub["n_end_of_data"].sum())
    closed = tp + sl + eod
    would = int(sub["would_be_stopped"].sum())
    return {
        "n_symbols": float(len(sub)),
        "total_return": ret,
        "max_drawdown": mdd,
        "ret_over_mdd": (ret / mdd) if (ret is not None and mdd) else None,
        "win_rate": avg("win_rate"),
        "mean_gross_r": avg("mean_gross_r"),
        "fill_rate": avg("fill_rate"),
        "num_trades": float(sub["num_trades"].sum()),
        "n_take_profit": float(tp),
        "n_stop_loss": float(sl),
        "n_end_of_data": float(eod),
        "stop_rate": (sl / closed) if closed else None,
        "n_positive": float((sub["total_return"].astype(float) > 0).sum()),
        "would_be_stopped": float(would),
        "rescued_tp": float(sub["rescued_tp"].sum()),
        "deeper_sl": float(sub["deeper_sl"].sum()),
        "unclosed_eod": float(sub["unclosed_eod"].sum()),
        "rescue_net_r": float(sub["rescue_net_r"].sum()),
        "rescue_rate": (float(sub["rescued_tp"].sum()) / would) if would else None,
    }


def leave_one_out(
    frame: pd.DataFrame, timeframe: str, lens: str, buffer: float, segment: str = PRIMARY_OOS
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인(ETH·SOL·DOGE, 이슈 필수 축)."""
    sub = _subset(frame, timeframe, segment, lens, buffer)
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    return " · ".join(parts)


def stops_per_symbol(
    frame: pd.DataFrame, timeframe: str, lens: str, segment: str = PRIMARY_OOS
) -> float:
    """버퍼가 건드리는 표본(가장 작은 양수 버퍼의 would_be_stopped) ÷ 심볼 — 게이트 입력."""
    buffers = sorted(b for b in set(frame["buffer"].astype(float)) if b > 0)
    if not buffers:
        return 0.0
    cell = pooled(frame, timeframe, segment, lens, buffers[0])
    would, symbols = cell.get("would_be_stopped"), cell.get("n_symbols")
    if not cell or would is None or not symbols:
        return 0.0
    return would / symbols


def _ret(cell: dict[str, float | None]) -> float:
    value = cell.get("total_return")
    return 0.0 if value is None else value


def best_buffer(
    frame: pd.DataFrame, timeframe: str, segment: str, lens: str
) -> tuple[float, float]:
    """그 구간에서 버퍼 0 대비 total_return 증분이 가장 큰 (버퍼, 증분%p)."""
    base = _ret(pooled(frame, timeframe, segment, lens, 0.0))
    buffers = sorted(b for b in set(frame["buffer"].astype(float)) if b > 0)
    best = (0.0, 0.0)
    for buffer in buffers:
        delta = _ret(pooled(frame, timeframe, segment, lens, buffer)) - base
        if delta > best[1]:
            best = (buffer, delta)
    return best


def verdict(frame: pd.DataFrame, timeframe: str, lens: str = LENS_BASELINE) -> str:
    """가짜 이탈이 평균회귀하는가 — 어떤 버퍼가 버퍼 0을 두 OOS(따뜻·차가움)에서 이기는가.

    (a) 어떤 버퍼가 따뜻·차가움 **둘 다** 이긴다(평균회귀 방증) / (b) 어느 버퍼도 못 이긴다
    (노이즈 → 현행 유지) / (c) 따뜻/차가움에 갈린다. 표본(버퍼가 건드리는 손절)이 심볼당
    20건 미만이면 판정하지 않는다(코드로 강제).
    """
    warm0 = pooled(frame, timeframe, PRIMARY_OOS, lens, 0.0)
    cold0 = pooled(frame, timeframe, STRESS_OOS, lens, 0.0)
    if not warm0 or not cold0:
        return "판정 불가(OOS 데이터 없음)."
    buffers = sorted(b for b in set(frame["buffer"].astype(float)) if b > 0)
    warm_base, cold_base = _ret(warm0), _ret(cold0)
    # 두 OOS를 동시에 이기는 버퍼가 있나 — 각 버퍼의 min(따뜻Δ, 차가움Δ) 최댓값으로 본다.
    best_both = (0.0, -math.inf)
    warm_best = best_buffer(frame, timeframe, PRIMARY_OOS, lens)
    cold_best = best_buffer(frame, timeframe, STRESS_OOS, lens)
    for buffer in buffers:
        wd = _ret(pooled(frame, timeframe, PRIMARY_OOS, lens, buffer)) - warm_base
        cd = _ret(pooled(frame, timeframe, STRESS_OOS, lens, buffer)) - cold_base
        both = min(wd, cd)
        if both > best_both[1]:
            best_both = (buffer, both)
    per_symbol = stops_per_symbol(frame, timeframe, lens)
    net = pooled(frame, timeframe, PRIMARY_OOS, lens, warm_best[0]).get("rescue_net_r")
    net_txt = "—" if not warm_best[0] or net is None else f"{net:+.1f}R"
    if per_symbol < MIN_STOPS_PER_SYMBOL:
        tag = (
            f"⚠️ **판정 불가(대조군)** — 따뜻한 OOS에서 버퍼가 건드리는 손절이 심볼당 "
            f"{per_symbol:.1f}건으로 20건 미만이다. 아래 숫자는 방향을 보는 참고값이지 채택 "
            "근거가 아니다"
        )
    elif best_both[1] > 0:
        tag = (
            f"(a) 가짜 이탈이 평균회귀한다 — 버퍼 {best_both[0]:.2f}가 따뜻·차가움 두 OOS를 "
            "모두 이긴다(구제 이득 > 더 깊은 손절 대가)"
        )
    elif warm_best[1] <= 0 and cold_best[1] <= 0:
        tag = "(b) 평균회귀 순효과 없음 — 어느 버퍼도 두 OOS 중 어디서도 버퍼 0을 못 이긴다(현행 유지)"  # noqa: E501
    else:
        tag = "(c) 따뜻/차가움에 갈린다 — 한쪽 OOS에서만 버퍼가 이긴다"
    warm_txt = (
        f"따뜻(주) {warm_base * 100:+.2f}% (버퍼 {warm_best[0]:.2f} Δ{warm_best[1] * 100:+.2f}%p)"
    )
    cold_txt = (
        f"차가움(스트레스) {cold_base * 100:+.2f}% "
        f"(버퍼 {cold_best[0]:.2f} Δ{cold_best[1] * 100:+.2f}%p)"
    )
    return (
        f"{tag} — 심볼평균 total_return 버퍼 0 대비 최선 증분: {warm_txt} · {cold_txt}. "
        f"따뜻 최선 버퍼 순효과 {net_txt}."
    )


def lens_note(frame: pd.DataFrame, timeframe: str) -> str:
    """체결 보수화(`pen_5bp`)에서 버퍼 우위가 뒤집히는지 — 버퍼 손절도 「닿으면 체결」이다."""
    warm_best = best_buffer(frame, timeframe, PRIMARY_OOS, LENS_BASELINE)
    if not warm_best[0]:
        return "baseline에서 버퍼 우위가 없어(증분 ≤ 0) 렌즈 민감도를 잴 대상이 없다."
    base_delta = warm_best[1]
    pen_base = _ret(pooled(frame, timeframe, PRIMARY_OOS, LENS_PEN, 0.0))
    pen_buf = pooled(frame, timeframe, PRIMARY_OOS, LENS_PEN, warm_best[0])
    if not pen_buf:
        return "—"
    pen_delta = _ret(pen_buf) - pen_base
    flipped = (base_delta > 0) != (pen_delta > 0)
    verb = "부호가 뒤집힌다" if flipped else "부호는 유지된다"
    return (
        f"버퍼 {warm_best[0]:.2f} 증분(따뜻 OOS): baseline {base_delta * 100:+.2f}%p → "
        f"pen_5bp {pen_delta * 100:+.2f}%p ({verb})."
    )


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}"


def _num(v: float | None, fmt: str = ".2f") -> str:
    return "—" if v is None else format(v, fmt)


def _grid_table(frame: pd.DataFrame, timeframe: str) -> str:
    headers = [
        "segment",
        "lens",
        "buffer",
        "return%",
        "mdd%",
        "ret/mdd",
        "win%",
        "meanR",
        "trades",
        "stop%",
        "救出/손절",
        "netR",
        "+심볼",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    unit = _unit_of(frame)
    buffers = buffers_for(unit)
    for segment in SEGMENT_ORDER:
        for lens in LENSES:
            for buffer in buffers:
                c = pooled(frame, timeframe, segment, lens, buffer)
                if not c:
                    continue
                fill = c.get("fill_rate")
                stop_rate = c.get("stop_rate")
                n_pos = c.get("n_positive")
                n_sym = c.get("n_symbols")
                would = c.get("would_be_stopped")
                rescued = c.get("rescued_tp")
                rescue_txt = "—" if not would else f"{int(rescued or 0)}/{int(would)}"
                _ = fill
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            segment,
                            lens,
                            buffer_label(unit, buffer),
                            _pct(c.get("total_return")),
                            _pct(c.get("max_drawdown")),
                            _num(c.get("ret_over_mdd")),
                            _pct(c.get("win_rate")),
                            _num(c.get("mean_gross_r"), ".3f"),
                            _num(c.get("num_trades"), ".0f"),
                            _num(None if stop_rate is None else stop_rate * 100, ".1f"),
                            rescue_txt,
                            _num(c.get("rescue_net_r"), "+.1f"),
                            "—" if n_pos is None or n_sym is None else f"{int(n_pos)}/{int(n_sym)}",
                        ]
                    )
                    + " |"
                )
    return "\n".join(lines)


def _rescue_table(frame: pd.DataFrame, timeframe: str, lens: str = LENS_BASELINE) -> str:
    """救出 3결말 분포(따뜻 OOS) — 버퍼가 건드린 손절이 어디로 갔나."""
    headers = [
        "buffer",
        "would_be_stopped",
        "→익절(구제)",
        "→더깊은손절",
        "→미청산",
        "구제율",
        "순효과R",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    unit = _unit_of(frame)
    for buffer in buffers_for(unit):
        if buffer == 0.0:
            continue
        c = pooled(frame, timeframe, PRIMARY_OOS, lens, buffer)
        if not c:
            continue
        would = int(c.get("would_be_stopped") or 0)
        rescue_rate = c.get("rescue_rate")
        lines.append(
            "| "
            + " | ".join(
                [
                    buffer_label(unit, buffer),
                    str(would),
                    str(int(c.get("rescued_tp") or 0)),
                    str(int(c.get("deeper_sl") or 0)),
                    str(int(c.get("unclosed_eod") or 0)),
                    "—" if rescue_rate is None else f"{rescue_rate * 100:.1f}%",
                    _num(c.get("rescue_net_r"), "+.1f"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _symbol_table(frame: pd.DataFrame, timeframe: str, segment: str, lens: str) -> str:
    sub = frame[
        (frame["timeframe"] == timeframe) & (frame["segment"] == segment) & (frame["lens"] == lens)
    ].copy()
    if sub.empty:
        return "(없음)"
    unit = _unit_of(frame)
    headers = ["symbol", "buffer", "return%", "mdd%", "win%", "trades", "救出/손절", "netR"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for _, r in sub.sort_values(["symbol", "buffer"]).iterrows():
        would = int(r["would_be_stopped"])
        rescue_txt = "—" if not would else f"{int(r['rescued_tp'])}/{would}"
        lines.append(
            "| "
            + " | ".join(
                [
                    _bare(str(r["symbol"])),
                    buffer_label(unit, float(r["buffer"])),
                    _pct(float(r["total_return"])),
                    _pct(float(r["max_drawdown"])),
                    _pct(float(r["win_rate"])),
                    str(int(r["num_trades"])),
                    rescue_txt,
                    _num(float(r["rescue_net_r"]), "+.1f"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _unit_of(frame: pd.DataFrame) -> str:
    units = set(frame["unit"].astype(str))
    return units.pop() if len(units) == 1 else UNIT_ATR


def _tf_order(tf: str) -> int:
    order = {"15m": 0, "1h": 1, "2h": 2, "4h": 3, "1d": 4}
    return order.get(tf, 99)


def write_summary(frame: pd.DataFrame, path: Path) -> None:
    timeframes = sorted(set(frame["timeframe"]), key=_tf_order)
    unit = _unit_of(frame)
    unit_desc = "1R 분수" if unit == UNIT_R else "ATR 배수(직전 확정봉)"
    pending = [tf for tf in ("15m", "1h", "2h") if tf not in set(timeframes)]
    pending_note = (
        f" 이번 판에 돈 TF는 **{'·'.join(timeframes)}**뿐이다 — {'·'.join(pending)}는 "
        "봉내 라이브 밴드 비용(WAN-203)이 커 `--append`로 이어붙일 후속 몫이다(wan206/276/277 "
        "패턴)."
        if pending
        else ""
    )
    lines = [
        "# WAN-278: 무효화 경계에 작은 손절 버퍼 — 가짜 이탈을 버티고 회복을 잡는가",
        "",
        "재현: `uv run python -m backtest.wan278_stop_buffer --tf 4h` "
        f"(1h·2h·15m은 `--tf 1h --append` 등).{pending_note}",
        "",
        f"창 **{harness.DEFAULT_START} ~ {harness.DEFAULT_END}**(못 박은 6년) · 9종목 · 롱 온리 "
        "· 채택 기본값 그대로(존 지정가 + 오프셋 2bp + 봉내 라이브 밴드 + 존폭 필터 1.28 + "
        f"`combine_obs=False` + `unconditional`, 핀 없음). 가드 {GUARD} 고정(축 아님). "
        "per-cell 단일 포지션.",
        "",
        f"**버퍼 축(정본 단위 = {unit_desc})**: 손절 = 원래 무효화 경계 ∓ 버퍼, 익절 = **원래 "
        f"무효화 기준 {TP_MULTIPLE}R에 고정**(버퍼로 안 움직임). 버퍼 0 = 채택 기본값 = 오버라이드 "
        "없음 = 프로덕션 경로. 렌즈 `baseline`(공식) + `pen_5bp`(스트레스 병기).",
        "",
        "> ⚠️ **WAN-260(손절폭 비례 확대)과 다른 축** — 저쪽은 익절도 같이 멀어진다. 여기선 "
        "익절이 원래 자리에 고정이다(손익비를 일부러 살짝 나쁘게 = 숨 쉴 공간).",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 버퍼 손절 체결 자체가 「닿으면 체결」이다 "
        "(`pen_5bp`를 함께 읽는다, 큐 우선순위 미모델 WAN-98 Canceled).",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124/151/201/248)은 불변 — 버퍼는 거래 관리(위험의 "
        "모양)이지 진입 규칙 알파가 아니다(WAN-90). 순 net이 플러스여도 **매칭 널은 아니다** "
        "(후속).",
        "> ⚠️ **기본값은 바꾸지 않았다**(측정 전용). 버퍼를 기본값으로 올리는 것은 명시적 "
        "재-베이스라인이자 **사용자 결정**이다. WAN-79 손절폭 가드 불변(버퍼는 손절을 멀리 "
        "미므로 가드와 충돌 없음).",
        "",
    ]
    for timeframe in timeframes:
        lines += [
            f"## {timeframe}",
            "",
            f"**판정(baseline)**: {verdict(frame, timeframe, LENS_BASELINE)}",
            "",
            f"**렌즈 민감도**: {lens_note(frame, timeframe)}",
            "",
            f"**Leave-one-out(따뜻 OOS · baseline · 최선 버퍼)**: {_loo_best(frame, timeframe)}",
            "",
            "### 救出 분해 (따뜻 OOS · baseline · 셋업 단위)",
            "",
            _rescue_table(frame, timeframe, LENS_BASELINE),
            "",
            "### 렌즈 × 버퍼 × 구간 (9종목 심볼평균; trades·救出은 합)",
            "",
            _grid_table(frame, timeframe),
            "",
            "### 심볼별 (따뜻 OOS · baseline)",
            "",
            _symbol_table(frame, timeframe, PRIMARY_OOS, LENS_BASELINE),
            "",
        ]
    lines += [
        "## 검산",
        "",
        "버퍼 0은 오버라이드를 걸지 않아 **현행 채택 엔진의 프로덕션 경로 그대로**이고 "
        "`harness.run_once`와 비트 일치한다(`--checksum`가 따뜻·차가움 양쪽에서 고정). 버퍼가 "
        "손절만 바꾸므로 **버퍼 팔의 진입 셋업 집합은 버퍼 0과 비트 일치**한다 — 救出 분해가 두 "
        "팔 후보를 위치로 짝지어 어긋나면 `AssertionError`로 멈춘다.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _loo_best(frame: pd.DataFrame, timeframe: str) -> str:
    best = best_buffer(frame, timeframe, PRIMARY_OOS, LENS_BASELINE)
    if not best[0]:
        return "버퍼 우위가 없어(증분 ≤ 0) leave-one-out 대상이 없다."
    return leave_one_out(frame, timeframe, LENS_BASELINE, best[0])


# --------------------------------------------------------------------------- #
# 검산 — 버퍼 0 팔(override=None) ≡ harness.run_once
# --------------------------------------------------------------------------- #


def run_checksum(
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> tuple[float, int, str]:
    """버퍼 0 팔(override=None)이 `harness.run_once`와 비트 일치하는지 — 따뜻·차가움 양쪽."""
    sym = harness.normalize_symbol(symbol)
    market = harness.load_market_data(
        sym,
        timeframe,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        need_1m=True,
        funding=True,
        db_path=db_path,
    )
    if market.empty or market.df_1m.empty:
        return 0.0, 0, "데이터 없음 — 검산 건너뜀"
    params = harness.build_params(
        entry_mode="zone_limit", take_profit_r=TP_MULTIPLE, fill=harness.BASELINE_FILL
    )
    max_diff = 0.0
    compared = 0
    for segment in segments_for(warm_oos=True):
        seg_market = harness.slice_market(market, segment)
        if seg_market.empty or seg_market.df_1m.empty:
            continue
        eval_from_ms = harness.eval_boundary_ms(market, segment)
        obr = OrderBlockDetector(OrderBlockParams()).run(seg_market.htf_df)
        cfg = harness.build_config(seg_market.timeframe)
        mine = run_arm(
            seg_market,
            params_fill=harness.BASELINE_FILL,
            unit=UNIT_ATR,
            buffer=0.0,
            atr_prev_by_time={},
            obr=obr,
            eval_from_ms=eval_from_ms,
        )
        mine_ret = build_result_from_trades(
            [t for _, t in mine.paired], cfg, seg_market.timeframe
        ).metrics.total_return
        outcome = harness.run_once(
            seg_market,
            params=harness.pin_invalidation_cancel(params),
            cfg=cfg,
            order_block_result=obr,
            eval_from_ms=eval_from_ms,
        )
        prod_ret = outcome.result.metrics.total_return
        max_diff = max(max_diff, abs(mine_ret - prod_ret))
        compared += 1
    if max_diff < 1e-12:
        verdict_txt = "비트 일치"
    elif max_diff < 1e-9:
        verdict_txt = "일치(부동소수 끝자리)"
    else:
        verdict_txt = "불일치 — 배선 버그"
    return max_diff, compared, verdict_txt


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[BufferRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BufferRow.model_fields))


def rows_from_csv(path: Path) -> list[BufferRow]:
    frame = pd.read_csv(path)
    return [BufferRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-278 손절 버퍼 (가짜 이탈 버티기)")
    parser.add_argument("--symbols", type=str, default=",".join(harness.LEGACY_NINE_SYMBOLS))
    parser.add_argument("--tf", type=str, default="4h", help="콤마로 여러 개(15m은 무겁다)")
    parser.add_argument("--start", type=str, default=harness.DEFAULT_START)
    parser.add_argument("--end", type=str, default=harness.DEFAULT_END)
    parser.add_argument(
        "--unit", type=str, choices=[UNIT_ATR, UNIT_R], default=UNIT_ATR, help="버퍼 단위(정본=atr)"
    )
    parser.add_argument("--out-csv", type=str, default=str(REPORTS_DIR / "wan278_stop_buffer.csv"))
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan278_stop_buffer_summary.md")
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument(
        "--no-funding-proxy", action="store_true", help="신규 3종목 펀딩 대리를 끈다"
    )
    parser.add_argument("--checksum", action="store_true", help="버퍼0 팔 ≡ run_once 검산만")
    args = parser.parse_args()

    if args.checksum:
        max_diff, compared, verdict_txt = run_checksum()
        print(f"[wan278] 검산: {compared}셀 비교 · 최대 절대차 {max_diff:.2e} → {verdict_txt}")
        return

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan278] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        rows = run_report(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            unit=args.unit,
            funding_proxy=not args.no_funding_proxy,
        )
        if args.append and out_csv.exists():
            existing = rows_from_csv(out_csv)
            keep = [r for r in existing if r.timeframe not in set(timeframes)]
            rows = keep + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows_to_frame(rows), Path(args.out_md))
    print(f"[wan278] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
