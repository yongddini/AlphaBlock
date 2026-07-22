"""존폭 필터의 「선별 대 기하」 분리 — 좁은 존을 고른 것인가, 익절선이 가까워진 것인가
(WAN-152, WAN-142 §다음 1 스핀아웃).

## 배경 — WAN-142가 관문 하나를 통과시키고 남은 하나를 지목했다

WAN-142(`wan142_zone_width_filter_verdict`)가 존폭 필터의 **「표본 축소」 대안을 배제**했다.
같은 개수를 무작위로 뽑은 매칭 대조군(시드 20개)을 필터가 네 셀 전부 이겼고(p=0.048 하한),
드물게 ETH 편중도 아니었다. **그런데 결정문서가 스스로 채택 권고를 보류했다** — 배제한
대안이 하나뿐이고 **「기하」가 열려 있기 때문**이다.

채택 엔진은 손절 = 진입가 → 존 무효화 경계라, **존이 좁으면 1R이 작아지고 고정 1.5R 익절
목표도 절대가격 기준으로 가까워진다.** 가까운 목표는 더 자주 닿는다. 그래서 같은 결과가 두
가지로 똑같이 설명된다:

* **선별 가설(a)** — 좁은 존이 진짜로 더 안 뚫린다(알파 후보).
* **기하 가설(b)** — 그냥 익절선이 가까워졌다(규칙이 아니라 목표 거리 정책).

**승률이 그 서명이다**(WAN-142: `default` 48.42% · `matched_random` 47.08% · `filter_bot3`
**57.97%** — 15m OOS). 매칭 팔은 기본과 승률이 같은데 필터만 +10%p 뛴다.

## 실험 — 장벽 거리를 존폭에서 떼어낸 뒤 같은 대조를 다시 한다

WAN-133의 k·ATR 장벽이 선례다. 손절을 **진입가 ∓ k·ATR**로 고정하면(`stop_loss_override`,
WAN-143이 봉내 라이브 밴드에 배선) 1R = k·ATR이라 **존폭이 목표 거리를 못 움직인다**.
그 상태에서 세 팔(`default` · `filter_bot3` · `matched_random`)을 다시 돌린다:

* 필터 우위가 **남으면** → 존폭이 장벽 거리와 독립으로 성과를 가른다 = **(a) 선별**.
* **사라지면** → 우위는 목표 거리 기하의 산물 = **(b) 기하**.
* 두 장벽 모두에서 우위가 없으면 = **(c) 둘 다 0**.

⚠️ **WAN-133이 같은 관문을 이미 통과시켰지만 그건 옛 엔진 위였다** — `tap` 밴드 + 병합 존
(`harness.pin_band_bar`·`LEGACY_OB_PARAMS`). 그 뒤 WAN-132(진입가 정본 `intrabar_live`)와
WAN-149(병합 폐지)가 축을 둘 옮겼고, **오늘의 엔진에서 이 관문을 세운 적은 없다.**
그래서 이 모듈은 **핀을 하나도 쓰지 않는다**(회귀 테스트가 동작으로 고정한다).

## 두 장벽이 같은 셋업을 보게 만든다

k·ATR 오버라이드는 ATR을 못 찾는 셋업(워밍업·갭)에서 `None`을 돌려 그 주문을 미체결로
남긴다(WAN-143: 봉내 경로의 정적 경로 대비 유일한 차이). 그대로 두면 두 장벽의 **셋업
집합이 달라져** 「장벽만 바꿨다」가 성립하지 않으므로, 이 모듈은 두 장벽의 후보를
`(탭 시각, 체결 시각, 방향)` 키로 **교집합**한 뒤 존폭/ATR이 유효한 것만 남긴 **공통 풀**
위에서 잰다. 그래서 이 표의 `zone` 장벽 수치는 WAN-142와 **비트 단위로 같지 않다**(그쪽은
전 후보, 이쪽은 공통 풀) — 셀을 직접 비교하지 말 것. 이 표 안의 두 장벽 대조만 성립한다.

재현: `python -m backtest.wan152_selection_vs_geometry`
(요약만 재생성: `--from-csv`).

## WAN-154 확장 — 세 번째 장벽 + 거래당 실현 손익비

WAN-154가 이 모듈을 **확장**한다(새 파이프라인 금지가 이슈 사양이다):

* **`zone_height` 장벽**(§0): 1R = 존 윗 경계 − 아랫 경계. `zone`은 진입가에 묶인 자,
  `atr`는 존과 끊긴 자, `zone_height`는 그 사이다 — 세 자에서 모두 필터가 이기면 판정이
  자에 의존하지 않는다.
* **거래당 실현 손익비 열**(§1′): `mean_net_r`·`cost_r`·손익분기 승률 등. ⚠️
  `harness.mean_r`은 **쓰지 않는다** — 그 값은 청산 사유로 ±1.5/−1.0을 넣는 승률의 대수적
  재탕이다(WAN-154 PM 정정). 여기서는 **실현 손익 ÷ 그 거래의 리스크 금액**(수수료·슬리피지·
  펀딩 반영 후)을 거래 단위로 잰다.
* **가드·문턱·렌즈 축**: `pnl_rows_for_cell(guard=…, threshold_fraction=…, lens=…)`.
  가드(`min_stop_distance_fraction`)는 시퀀싱(`position_size`)에서만 걸리므로 후보 재시뮬
  없이 재시퀀싱만으로 축이 돈다. 렌즈(`pen_5bp`)는 체결 집합을 바꾸므로 **셀 자체를 그
  파라미터로 다시 빌드**해야 한다(오케스트레이터 `backtest.wan154_stop_width_audit` 소관).

전체 실행은 `python -m backtest.wan154_stop_width_audit`가 오케스트레이션한다.
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.models import BacktestConfig, ExitReason
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    _FeatureExtractor,
    harness_prepare,
)
from backtest.wan133_geometry_vs_selection import (
    ARM_DEFAULT,
    ARM_FILTER,
    FILTER_FRACTION,
    MIN_TRADES_FOR_PNL,
    REPORTS_DIR,
    STOP_GUARD_FRACTION,
    _bare,
    _write_csv,
    _zwa,
    atr_by_tap_time,
    make_atr_stop_override,
)
from backtest.wan142_zone_width_filter_verdict import (
    ALPHA,
    ARM_MATCHED,
    MATCH_SEEDS,
    SEED_AGGREGATE,
)
from backtest.zone_limit_backtest import (
    StopLossContext,
    StopLossOverride,
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

LENS_PRIMARY = "baseline"
"""공식 렌즈(WAN-104/128) 단독. 신규 리포트는 3렌즈를 병기하지 않는다."""

BARRIER_ZONE = "zone"
"""채택 기본값의 장벽 — 손절 = 존 무효화 경계 → **1R이 존폭에 비례한다**(기하가 산다)."""

BARRIER_ATR = "atr"
"""기하 통제 팔 — 손절 = 진입가 ∓ `ATR_K`·ATR로 **존폭과 무관하게 고정**(WAN-133 선례)."""

BARRIER_ZONE_HEIGHT = "zone_height"
"""세 번째 장벽(WAN-154 §0) — 1R = **존 윗 경계 − 아랫 경계**(존 높이), 진입가와 무관.

`zone` 장벽은 「존 크기」가 아니다 — 볼린저가 진입가를 존 안쪽으로 재산정하면 아랫 경계까지
거리가 존 높이보다 짧아진다. `zone_height`는 존 크기를 반영하되 진입가에 묶이지 않는 중간
자다: `atr`(존과 완전히 끊김)·`zone`(진입가에 묶임) 사이. ⚠️ WAN-143이 같은 규칙을 잰 적이
있으나 **병합 존 시절**이라(병합이 존 높이를 직접 부풀린다) 그 표는 이 축에 못 쓴다.
⚠️ 여기서는 **통제 장벽**일 뿐이다 — 「익절 규칙을 바꿀까」는 WAN-155 소관.
"""

BARRIERS: tuple[str, ...] = (BARRIER_ZONE, BARRIER_ATR, BARRIER_ZONE_HEIGHT)

ATR_K = 1.5
"""ATR 장벽의 배수. WAN-133의 대표값 `PRIMARY_K`와 같은 1.5 — 존폭/ATR 중앙값 근처라
장벽 거리가 채택 팔과 같은 자릿수에 놓인다(팔 간 차이가 「거리 수준」이 아니라 「존폭에
연동되는가」에서 오도록)."""

ARMS: tuple[str, ...] = (ARM_DEFAULT, ARM_FILTER, ARM_MATCHED)


def make_zone_height_stop_override() -> StopLossOverride:
    """손절 = 진입가 ∓ (존 윗 경계 − 아랫 경계)로 고정하는 오버라이드 (WAN-154 §0).

    존 높이는 진입가와 무관하므로 볼린저가 진입가를 존 안쪽으로 밀어도 1R이 줄지 않는다.
    높이가 비양수인 퇴화 존(있어서는 안 되지만)은 None으로 제외한다 — `atr` 오버라이드가
    ATR을 못 찾을 때와 같은 계약이다.
    """

    def resolve(ctx: StopLossContext) -> float | None:
        height = ctx.order_block.top - ctx.order_block.bottom
        if height <= 0:
            return None
        return ctx.entry_price - height if ctx.is_long else ctx.entry_price + height

    return resolve


GEOMETRY_SHARE_BAR = 0.7
"""장벽을 고정했을 때 필터 우위가 이만큼 이상 사라지면 「기하가 대부분」으로 읽는다.

`zone` 장벽의 (필터 − 매칭) 격차 대비 `atr` 장벽에서 남는 격차의 **소멸 비율**이다.
0.7은 「대부분」의 조작적 정의이고, 판정의 주 근거는 이 비율이 아니라 `atr` 장벽의 시드
순위 p값이다(비율은 크기, p값은 유의)."""


# --------------------------------------------------------------------------- #
# 셀 — 두 장벽의 공통 후보 풀
# --------------------------------------------------------------------------- #

CandidateKey = tuple[int, int, int]
"""(탭 시각, 체결 시각, 방향) — 두 장벽에서 같은 셋업을 짝짓는 키.

장벽(손절)은 **체결 판정을 바꾸지 않으므로**(체결은 지정가 터치로만 정해진다) 같은 셋업은
두 팔에서 같은 탭·체결 시각을 갖는다. `stop_loss_override`가 `None`을 돌려 미체결로 끝난
셋업만 한쪽에서 사라지고, 교집합이 정확히 그 차이를 걷어낸다.
"""


def candidate_key(cand: _Candidate) -> CandidateKey:
    return (cand.trigger_time, cand.entry_time, int(cand.side.value == "long"))


@dataclass
class GeoCell:
    """한 (심볼, TF)의 두 장벽 공통 후보 풀 + 후보별 존폭/ATR."""

    symbol: str
    timeframe: str
    is_boundary: int = 0
    by_barrier: dict[str, list[_Candidate]] = field(default_factory=dict)
    """장벽별 공통 풀 후보(같은 순서·같은 셋업, 청산만 다르다)."""
    zwa: list[float | None] = field(default_factory=list)
    """`by_barrier` 각 리스트와 **같은 순서**로 정렬된 zone_width_atr."""
    n_raw: dict[str, int] = field(default_factory=dict)
    """교집합 전 장벽별 후보 수(풀 축소 규모를 표에 적기 위한 진단값)."""

    @property
    def n_pool(self) -> int:
        return len(self.zwa)


def build_cell(market: MarketData, *, params: ConfluenceParams) -> GeoCell:
    """두 장벽으로 후보를 만들고 공통 풀로 정렬한다.

    ⚠️ 오더블록 탐지·컨플루언스 파라미터에 **핀을 쓰지 않는다** — 오늘의 채택 기본값
    (`combine_obs=False`(WAN-149) · `intrabar_live`(WAN-132) · `unconditional`(WAN-123) ·
    오프셋 2bp(WAN-112))이 그대로 돈다. WAN-133이 `LEGACY_OB_PARAMS`로 병합을 고정한 것과
    정반대이고, 그게 이 이슈의 요점이다.
    """
    cell = GeoCell(symbol=market.symbol, timeframe=market.timeframe)
    if market.empty or market.df_1m.empty:
        return cell
    frame = harness_prepare(market.htf_df)
    extractor = _FeatureExtractor.build(frame)
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    cell.is_boundary = start + int((end - start) * IS_FRACTION)
    cfg = harness.build_config(market.timeframe)
    # 오더블록은 장벽과 무관하므로 한 번만 탐지해 두 팔이 공유한다.
    obr = harness.detect_order_blocks(market)
    atr_map = atr_by_tap_time(frame)

    overrides: dict[str, StopLossOverride | None] = {
        BARRIER_ZONE: None,
        BARRIER_ATR: make_atr_stop_override(atr_map, ATR_K),
        BARRIER_ZONE_HEIGHT: make_zone_height_stop_override(),
    }
    built: dict[str, list[_Candidate]] = {}
    for barrier in BARRIERS:
        override = overrides[barrier]
        cands, _ = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            market.timeframe,
            params=params,
            cfg=cfg,
            order_block_result=obr,
            stop_loss_override=override,
        )
        built[barrier] = cands
        cell.n_raw[barrier] = len(cands)

    indexed = {b: {candidate_key(c): c for c in cands} for b, cands in built.items()}
    # 공통 풀 = 두 장벽에 모두 있고 존폭/ATR이 유효한 셋업(탭 시각 순).
    common = set(indexed[BARRIERS[0]])
    for barrier in BARRIERS[1:]:
        common &= set(indexed[barrier])
    keys = sorted(
        k
        for k in common
        if _zwa(extractor, indexed[BARRIER_ZONE][k]) is not None  # noqa: E501
    )
    cell.zwa = [_zwa(extractor, indexed[BARRIER_ZONE][k]) for k in keys]
    for barrier in BARRIERS:
        cell.by_barrier[barrier] = [indexed[barrier][k] for k in keys]
    return cell


def is_threshold(cell: GeoCell, *, fraction: float = FILTER_FRACTION) -> float | None:
    """IS 공통 풀의 zone_width_atr 하위 `fraction` 문턱(OOS에 적용 — 룩어헤드 없음).

    기본은 하위 1/3(WAN-133/142/152의 유일한 문턱). WAN-154 §5가 1/4·1/2를 같은 격자에서
    병기해 「유일하게 통하는 값 = IS에서 고른 자유 파라미터」인지 확인한다.
    """
    zone_cands = cell.by_barrier.get(BARRIER_ZONE, [])
    is_vals = [
        z
        for z, cand in zip(cell.zwa, zone_cands, strict=True)
        if z is not None and cand.trigger_time < cell.is_boundary
    ]
    if len(is_vals) < 3:
        return None
    return float(pd.Series(is_vals).quantile(fraction))


# --------------------------------------------------------------------------- #
# 손익 행
# --------------------------------------------------------------------------- #


class PnlRow(BaseModel):
    """한 (장벽, 심볼, TF, 구간, 팔, 시드) 셀의 손익.

    WAN-154가 열을 늘렸다 — **기존 열의 값은 비트 단위로 같다**(회귀 테스트가 고정).
    새 열은 §1′(거래당 실현 손익비)·§2(베팅 크기)와 축 좌표(렌즈·가드·문턱)다.
    """

    model_config = ConfigDict(frozen=True)

    barrier: str
    symbol: str
    timeframe: str
    segment: str
    arm: str
    seed: int
    """`matched_random`의 추첨 시드. 다른 팔과 시드 평균 행은 `SEED_AGGREGATE`(−1)."""
    num_candidates: float
    num_trades: float
    total_return: float
    max_drawdown: float
    win_rate: float
    # --- 축 좌표 (WAN-154). 기본값 = 이 모듈의 원 실행과 같은 좌표라 옛 CSV도 읽힌다. ---
    lens: str = LENS_PRIMARY
    """체결 렌즈. `baseline`(기본) 또는 `pen_5bp`(§4 옵트인 — 셀 자체가 다시 빌드된다)."""
    guard: float = STOP_GUARD_FRACTION
    """이 행을 시퀀싱한 최소 손절폭 가드(`min_stop_distance_fraction`). §3-B의 축."""
    threshold_fraction: float = FILTER_FRACTION
    """필터 문턱(존폭 하위 분위). §5의 축. `default`·`matched` 팔에도 좌표로 남는다
    (매칭 크기가 이 문턱의 필터 크기를 따르므로)."""
    # --- §1′ 거래당 실현 손익비 — 전부 **그 거래 자신의 1R**(= 그 장벽의 손절 거리 ×
    # 수량 = 리스크 금액)로 정규화한 값이다. 장벽마다 1R 정의가 다르므로 장벽 간 비교는
    # 「같은 자 안에서의 팔 대조」로만 한다. ---
    mean_net_r: float | None = None
    """거래당 (실현 손익 ÷ 리스크 금액) 평균 — 수수료·슬리피지·펀딩이 전부 빠진 뒤."""
    net_r_win: float | None = None
    """승리 거래(실현 손익 > 0)의 net R 평균."""
    net_r_loss: float | None = None
    """패배 거래의 net R 평균."""
    cost_r_median: float | None = None
    """거래당 (왕복 비용 ÷ 리스크 금액) 중앙값. 비용 = 수수료(진입+청산) + 청산 슬리피지 +
    펀딩. 좁은 손절일수록 이 값이 커진다 — 비용은 리스크가 아니라 거래 규모에 붙기 때문."""
    breakeven_win_rate: float | None = None
    """손익분기 승률 = (1 + cost_r) / ((R − cost_r) + (1 + cost_r)), cost_r = 중앙값,
    R = 고정 익절 배수(1.5). 이 승률 아래면 이겨도 진다."""
    win_rate_margin: float | None = None
    """실제 승률 − 손익분기 승률(여유). 음수면 그 셀은 비용이 신호를 먹고 있다."""
    profit_factor: float | None = None
    """총이익 ÷ |총손실|. 손실 거래가 없으면 None(무한대를 지어내지 않는다)."""
    # --- §2 베팅 크기 — 레버리지 상한과 실효 리스크. ---
    cap_hit_rate: float | None = None
    """레버리지 상한(1.0) 발동 비율 = 손절 거리 분수 < `risk_per_trade`/`leverage`인 거래
    비율. 발동하면 원하는 수량을 못 사서 실효 리스크가 1% 아래로 깎인다."""
    effective_risk_mean: float | None = None
    """거래당 실효 리스크 평균 = min(`risk_per_trade`, `leverage` × 손절 거리 분수).
    자본과 무관한 해석식이라 시퀀싱 순서에 의존하지 않는다."""
    guard_reject_rate: float | None = None
    """이 팔 후보 중 가드에 걸리는(손절 거리 분수 < guard) 비율 — **후보 단위**의 결정적
    값이다(시퀀싱 순서 무관). 필터 × 가드 정면 충돌(§3-B)의 크기."""


@dataclass(frozen=True)
class TradeStats:
    """한 팔(후보 목록)의 시퀀싱 결과 요약 — 기존 4지표 + WAN-154 §1′·§2 지표."""

    total_return: float
    max_drawdown: float
    win_rate: float
    num_trades: int
    mean_net_r: float | None
    net_r_win: float | None
    net_r_loss: float | None
    cost_r_median: float | None
    breakeven_win_rate: float | None
    win_rate_margin: float | None
    profit_factor: float | None
    cap_hit_rate: float | None
    effective_risk_mean: float | None


def apply_guard(cfg: BacktestConfig, guard: float | None) -> BacktestConfig:
    """`min_stop_distance_fraction`(WAN-79 가드)만 갈아끼운 config.

    가드는 시퀀싱(`_to_trade` → `position_size`)에서만 읽히므로 후보 재시뮬 없이 이 값만
    바꿔 재시퀀싱하면 §3-B 가드 축이 돈다. `None`이면 손대지 않는다(채택 기본값 0.3%)."""
    if guard is None or cfg.risk_sizing is None:
        return cfg
    sizing = cfg.risk_sizing.model_copy(update={"min_stop_distance_fraction": guard})
    return cfg.model_copy(update={"risk_sizing": sizing})


def breakeven_win_rate_for(cost_r: float, take_profit_r: float) -> float:
    """손익분기 승률(WAN-154 §1′ 식) — 이길 때 +R−cost, 질 때 −1−cost의 기대값 0 조건."""
    return (1.0 + cost_r) / ((take_profit_r - cost_r) + (1.0 + cost_r))


@dataclass(frozen=True)
class TradeRecord:
    """시퀀싱된 거래 하나의 손절폭·실현 손익비 레코드 (WAN-154 §1′·§3-A 공용).

    `trade_stats`(팔 요약)와 손절폭 버킷 진단표가 **같은 산식**을 쓰도록 한 곳에 둔다 —
    두 표가 각자 계산하면 산식이 조용히 갈라진다.
    """

    stop_frac: float
    """손절 거리 분수 = |진입 체결가 − 손절 참조가| ÷ 진입 체결가."""
    net_r: float
    """실현 손익(수수료·슬리피지·펀딩 반영 후) ÷ 리스크 금액(수량 × 손절 거리)."""
    cost_r: float
    """왕복 비용(진입·청산 수수료 + 청산 슬리피지 + 펀딩) ÷ 리스크 금액."""
    pnl: float
    """실현 손익(절대 금액) — profit factor 집계용."""
    win: bool
    stopped: bool
    """청산 사유가 손절인지(§3-A 생존율 = 1 − 손절 비율)."""
    cap_hit: bool | None
    """레버리지 상한 발동 여부. `risk_pct` 사이징이 아니면 None."""
    effective_risk: float | None
    """실효 리스크 = min(`risk_per_trade`, `leverage` × 손절 거리 분수)."""


def per_trade_records(
    cands: list[_Candidate],
    market: MarketData,
    timeframe: str,
    *,
    guard: float | None = None,
) -> list[TradeRecord]:
    """후보 목록을 프로덕션 시퀀서로 배치하고 거래당 레코드를 낸다.

    * 리스크 금액 = 수량 × |진입 체결가 − 손절 참조가| — **그 거래 자신의 1R**이다.
      `harness.mean_r`(청산 사유 → ±1.5/−1.0)은 승률의 대수적 재탕이라 쓰지 않는다
      (WAN-154 PM 정정).
    * 상한 발동 = 손절 거리 분수 < `risk_per_trade`/`leverage` — `position_size`의 clamp
      대수식과 동치라(수량·자본이 약분된다) 시퀀싱 순서·복리에 의존하지 않는다.
    """
    cfg = apply_guard(harness.build_config(timeframe), guard)
    sizing = cfg.risk_sizing
    records: list[TradeRecord] = []
    for cand, trade in sequence_with_candidates(cands, cfg, market.funding_rates):
        stop_dist = abs(trade.entry_price - cand.stop_price)
        risk_amount = trade.quantity * stop_dist
        if risk_amount <= 0:
            continue
        slip = sum(abs(f.price - cand.exit_price) * f.quantity for f in trade.exits)
        cost = trade.entry_fee + sum(f.fee for f in trade.exits) + trade.funding_cost + slip
        stop_frac = stop_dist / trade.entry_price
        cap_hit: bool | None = None
        effective_risk: float | None = None
        if sizing is not None and sizing.sizing_mode == "risk_pct":
            cap_hit = stop_frac < sizing.risk_per_trade / sizing.leverage
            effective_risk = min(sizing.risk_per_trade, sizing.leverage * stop_frac)
        records.append(
            TradeRecord(
                stop_frac=stop_frac,
                net_r=trade.realized_pnl / risk_amount,
                cost_r=cost / risk_amount,
                pnl=trade.realized_pnl,
                win=trade.realized_pnl > 0,
                stopped=cand.reason is ExitReason.STOP_LOSS,
                cap_hit=cap_hit,
                effective_risk=effective_risk,
            )
        )
    return records


def _aggregate_records(records: Sequence[TradeRecord]) -> dict[str, float | None]:
    """레코드 → §1′·§2 집계(요약 행·버킷 행 공용)."""
    if not records:
        empty: dict[str, float | None] = {
            name: None
            for name in (
                "mean_net_r",
                "net_r_win",
                "net_r_loss",
                "cost_r_median",
                "breakeven_win_rate",
                "win_rate_margin",
                "profit_factor",
                "cap_hit_rate",
                "effective_risk_mean",
                "win_rate",
                "survival_rate",
            )
        }
        return empty
    wins = [r.net_r for r in records if r.win]
    losses = [r.net_r for r in records if not r.win]
    gains_amt = sum(r.pnl for r in records if r.win)
    losses_amt = sum(-r.pnl for r in records if not r.win)
    cost_r_median = statistics.median(r.cost_r for r in records)
    breakeven = breakeven_win_rate_for(cost_r_median, ConfluenceParams().take_profit_r)
    win_rate = len(wins) / len(records)
    cap = [r.cap_hit for r in records if r.cap_hit is not None]
    eff = [r.effective_risk for r in records if r.effective_risk is not None]
    return {
        "mean_net_r": statistics.fmean(r.net_r for r in records),
        "net_r_win": statistics.fmean(wins) if wins else None,
        "net_r_loss": statistics.fmean(losses) if losses else None,
        "cost_r_median": cost_r_median,
        "breakeven_win_rate": breakeven,
        "win_rate_margin": win_rate - breakeven,
        "profit_factor": (gains_amt / losses_amt) if losses_amt > 0 else None,
        "cap_hit_rate": (sum(cap) / len(cap)) if cap else None,
        "effective_risk_mean": statistics.fmean(eff) if eff else None,
        "win_rate": win_rate,
        "survival_rate": 1.0 - sum(1 for r in records if r.stopped) / len(records),
    }


def trade_stats(
    cands: list[_Candidate],
    market: MarketData,
    timeframe: str,
    *,
    guard: float | None = None,
) -> TradeStats:
    """후보 목록 → 시퀀싱 손익 + 거래당 실현 손익비·베팅 크기 지표.

    시퀀싱·비용·펀딩은 프로덕션 경로 그대로다(`sequence_with_candidates`). 기존 4지표는
    예전 `_metrics_for`와 같은 경로(`build_result_from_trades`)라 **비트 단위로 같고**,
    새 지표는 `per_trade_records`(같은 시퀀싱을 한 번 더 돈다)에서 온다.
    """
    cfg = apply_guard(harness.build_config(timeframe), guard)
    trades = [t for _, t in sequence_with_candidates(cands, cfg, market.funding_rates)]
    m = build_result_from_trades(trades, cfg, timeframe).metrics
    agg = _aggregate_records(per_trade_records(cands, market, timeframe, guard=guard))
    return TradeStats(
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        win_rate=m.win_rate,
        num_trades=m.num_trades,
        mean_net_r=agg["mean_net_r"],
        net_r_win=agg["net_r_win"],
        net_r_loss=agg["net_r_loss"],
        cost_r_median=agg["cost_r_median"],
        breakeven_win_rate=agg["breakeven_win_rate"],
        win_rate_margin=None
        if agg["breakeven_win_rate"] is None
        else m.win_rate - agg["breakeven_win_rate"],
        profit_factor=agg["profit_factor"],
        cap_hit_rate=agg["cap_hit_rate"],
        effective_risk_mean=agg["effective_risk_mean"],
    )


def pnl_rows_for_cell(
    cell: GeoCell,
    market: MarketData,
    *,
    lens: str = LENS_PRIMARY,
    guard: float | None = None,
    threshold_fraction: float = FILTER_FRACTION,
) -> list[PnlRow]:
    """장벽 × 세 팔의 구간별 손익.

    **매칭 대조군의 추첨은 장벽에 걸쳐 같은 시드로 같은 순번을 뽑는다** — 공통 풀이 같은
    순서라 시드 `s`가 모든 장벽에서 **글자 그대로 같은 셋업 집합**을 고른다. 그래야 장벽 간
    차이가 「어느 거래를 뽑았나」가 아니라 **장벽 하나**에서만 온다.

    `lens`는 좌표 라벨일 뿐이다 — 렌즈는 체결 집합을 바꾸므로 **셀을 그 렌즈 파라미터로
    빌드한 뒤** 여기에 그 이름을 넘겨야 한다(§4). `guard`/`threshold_fraction`은 실제로
    시퀀싱/필터를 바꾼다(§3-B·§5).
    """
    if not cell.zwa:
        return []
    threshold = is_threshold(cell, fraction=threshold_fraction)
    zone_cands = cell.by_barrier[BARRIER_ZONE]
    barriers = [b for b in BARRIERS if b in cell.by_barrier]
    cfg = apply_guard(harness.build_config(cell.timeframe), guard)
    guard_used = (
        cfg.risk_sizing.min_stop_distance_fraction
        if cfg.risk_sizing is not None
        else (guard or 0.0)
    )
    rows: list[PnlRow] = []

    def _guard_reject_rate(cands: list[_Candidate]) -> float | None:
        """후보 단위 가드 탈락률(결정적 — 시퀀싱 순서 무관)."""
        if not cands:
            return None
        below = sum(
            1 for c in cands if abs(c.entry_price - c.stop_price) < guard_used * c.entry_price
        )
        return below / len(cands)

    def _row(barrier: str, arm: str, seed: int, cands: list[_Candidate], segment: str) -> PnlRow:
        s = trade_stats(cands, market, cell.timeframe, guard=guard)
        return PnlRow(
            barrier=barrier,
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            segment=segment,
            arm=arm,
            seed=seed,
            num_candidates=float(len(cands)),
            num_trades=float(s.num_trades),
            total_return=s.total_return,
            max_drawdown=s.max_drawdown,
            win_rate=s.win_rate,
            lens=lens,
            guard=guard_used,
            threshold_fraction=threshold_fraction,
            mean_net_r=s.mean_net_r,
            net_r_win=s.net_r_win,
            net_r_loss=s.net_r_loss,
            cost_r_median=s.cost_r_median,
            breakeven_win_rate=s.breakeven_win_rate,
            win_rate_margin=s.win_rate_margin,
            profit_factor=s.profit_factor,
            cap_hit_rate=s.cap_hit_rate,
            effective_risk_mean=s.effective_risk_mean,
            guard_reject_rate=_guard_reject_rate(cands),
        )

    for segment in (SEGMENT_IS, SEGMENT_OOS):
        idx_seg = [
            i
            for i, cand in enumerate(zone_cands)
            if (cand.trigger_time < cell.is_boundary) == (segment == SEGMENT_IS)
        ]
        if threshold is None:
            idx_filter: list[int] = []
        else:
            idx_filter = [i for i in idx_seg if (cell.zwa[i] or 0.0) <= threshold]
        k = min(len(idx_filter), len(idx_seg))
        # 시드별 추첨은 **인덱스** 위에서 한 번만 한다 → 모든 장벽이 같은 셋업을 본다.
        drawn_by_seed = {
            seed: (random.Random(seed).sample(idx_seg, k) if k else []) for seed in MATCH_SEEDS
        }
        for barrier in barriers:
            cands = cell.by_barrier[barrier]
            rows.append(
                _row(barrier, ARM_DEFAULT, SEED_AGGREGATE, [cands[i] for i in idx_seg], segment)
            )
            rows.append(
                _row(barrier, ARM_FILTER, SEED_AGGREGATE, [cands[i] for i in idx_filter], segment)
            )
            seed_rows = [
                _row(barrier, ARM_MATCHED, seed, [cands[i] for i in idx], segment)
                for seed, idx in drawn_by_seed.items()
            ]
            rows.extend(seed_rows)
            if seed_rows:
                rows.append(_mean_row(seed_rows))
    return rows


def _val(x: float | None) -> float | None:
    """CSV 왕복으로 None이 NaN이 된 값을 다시 None으로 정규화한다."""
    if x is None:
        return None
    return None if math.isnan(x) else x


#: `_mean_row`·`symbol_mean`이 평균을 내는 WAN-154 신규 지표 열(None-안전 평균).
_MEAN_FIELDS: tuple[str, ...] = (
    "mean_net_r",
    "net_r_win",
    "net_r_loss",
    "cost_r_median",
    "breakeven_win_rate",
    "win_rate_margin",
    "profit_factor",
    "cap_hit_rate",
    "effective_risk_mean",
    "guard_reject_rate",
)


def _none_safe_mean(values: Sequence[float | None]) -> float | None:
    vals = [v for v in (_val(x) for x in values) if v is not None]
    return statistics.fmean(vals) if vals else None


def _mean_row(seed_rows: Sequence[PnlRow]) -> PnlRow:
    """시드 평균 행(팔 간 대조표에 쓰는 대표값). 좌표는 시드만 빼고 전부 같다."""
    head = seed_rows[0]
    n = len(seed_rows)
    update: dict[str, object] = {
        "seed": SEED_AGGREGATE,
        "num_candidates": sum(r.num_candidates for r in seed_rows) / n,
        "num_trades": sum(r.num_trades for r in seed_rows) / n,
        "total_return": sum(r.total_return for r in seed_rows) / n,
        "max_drawdown": sum(r.max_drawdown for r in seed_rows) / n,
        "win_rate": sum(r.win_rate for r in seed_rows) / n,
    }
    for name in _MEAN_FIELDS:
        update[name] = _none_safe_mean([getattr(r, name) for r in seed_rows])
    return head.model_copy(update=update)


# --------------------------------------------------------------------------- #
# 집계 · 검정
# --------------------------------------------------------------------------- #


def symbol_mean(
    rows: Sequence[PnlRow],
    *,
    barrier: str,
    timeframe: str,
    segment: str,
    arm: str,
    seed: int = SEED_AGGREGATE,
) -> dict[str, float | None]:
    """6심볼 심볼평균(수익·MDD·승률은 단순평균, 거래수·후보수는 합).

    거래 20건 미만 셀은 평균에서 뺀다(`MIN_TRADES_FOR_PNL`, WAN-133/142와 같은 기준) —
    사이징 바닥(WAN-79)에 걸려 표본이 무너진 필터 셀이 평균의 1/6을 차지하는 것을 막는다.
    """
    sub = [
        r
        for r in rows
        if r.barrier == barrier
        and r.timeframe == timeframe
        and r.segment == segment
        and r.arm == arm
        and r.seed == seed
    ]
    excluded = [r for r in sub if r.num_trades < MIN_TRADES_FOR_PNL]
    sub = [r for r in sub if r.num_trades >= MIN_TRADES_FOR_PNL]
    if not sub:
        out: dict[str, float | None] = {
            "total_return": None,
            "max_drawdown": None,
            "win_rate": None,
            "num_trades": None,
            "num_candidates": None,
            "n_symbols": 0.0,
            "n_excluded": float(len(excluded)),
        }
        out.update({name: None for name in _MEAN_FIELDS})
        return out
    n = len(sub)
    result: dict[str, float | None] = {
        "total_return": sum(r.total_return for r in sub) / n,
        "max_drawdown": sum(r.max_drawdown for r in sub) / n,
        "win_rate": sum(r.win_rate for r in sub) / n,
        "num_trades": sum(r.num_trades for r in sub),
        "num_candidates": sum(r.num_candidates for r in sub),
        "n_symbols": float(n),
        "n_excluded": float(len(excluded)),
    }
    for name in _MEAN_FIELDS:
        result[name] = _none_safe_mean([getattr(r, name) for r in sub])
    return result


class MatchedTestRow(BaseModel):
    """한 (장벽, TF, 구간)의 필터 팔 vs 매칭 대조군 시드 분포.

    ⚠️ 검정 심볼 집합은 **필터 셀이 유효 표본(거래 20건)을 가진 심볼**로 두 팔에 똑같이
    적용한다(WAN-142와 같은 규칙) — 한쪽만 게이트를 걸면 심볼 구성 차이가 p값에 섞인다.
    """

    model_config = ConfigDict(frozen=True)

    barrier: str
    timeframe: str
    segment: str
    n_symbols: int
    n_seeds: int
    filter_return: float | None
    matched_return_mean: float | None
    p_return: float | None
    """단측 순위 p — 매칭 대조군이 필터 이상으로 벌 확률."""
    filter_win_rate: float | None
    matched_win_rate_mean: float | None
    p_win_rate: float | None
    """단측 순위 p — 매칭 대조군의 승률이 필터 이상일 확률. **이 이슈의 핵심 지표다**
    (기하 효과의 서명이 승률이기 때문)."""
    filter_mdd: float | None
    matched_mdd_mean: float | None
    p_mdd: float | None
    default_mdd: float | None
    filter_trades: float | None
    matched_trades: float | None
    trade_gap_pct: float | None
    """두 팔의 **최종 거래 수** 격차(%). 후보 수는 정확히 맞췄어도 동시 1포지션 시퀀서가
    두 팔을 다르게 깎는다. 양수면 매칭이 더 많이 거래한다 = 노출이 커 MDD가 부풀려진다
    = **필터에 유리한 잔차**다."""


def matched_test_row(
    rows: Sequence[PnlRow], *, barrier: str, timeframe: str, segment: str
) -> MatchedTestRow:
    """필터 팔을 매칭 대조군 시드 분포 위에 놓고 단측 순위 p를 낸다(수익·승률·MDD)."""
    empty = MatchedTestRow(
        barrier=barrier,
        timeframe=timeframe,
        segment=segment,
        n_symbols=0,
        n_seeds=0,
        filter_return=None,
        matched_return_mean=None,
        p_return=None,
        filter_win_rate=None,
        matched_win_rate_mean=None,
        p_win_rate=None,
        filter_mdd=None,
        matched_mdd_mean=None,
        p_mdd=None,
        default_mdd=None,
        filter_trades=None,
        matched_trades=None,
        trade_gap_pct=None,
    )

    def _pick(arm: str, seed: int) -> dict[str, PnlRow]:
        return {
            r.symbol: r
            for r in rows
            if r.barrier == barrier
            and r.timeframe == timeframe
            and r.segment == segment
            and r.arm == arm
            and r.seed == seed
        }

    filt = _pick(ARM_FILTER, SEED_AGGREGATE)
    base = _pick(ARM_DEFAULT, SEED_AGGREGATE)
    symbols = sorted(s for s, r in filt.items() if r.num_trades >= MIN_TRADES_FOR_PNL)
    if not symbols:
        return empty

    def _avg(picked: dict[str, PnlRow], attr: str) -> float | None:
        vals = [getattr(picked[s], attr) for s in symbols if s in picked]
        return sum(vals) / len(vals) if len(vals) == len(symbols) else None

    f_ret, f_wr = _avg(filt, "total_return"), _avg(filt, "win_rate")
    f_mdd, f_trades = _avg(filt, "max_drawdown"), _avg(filt, "num_trades")
    d_mdd = _avg(base, "max_drawdown")
    seed_ret: list[float] = []
    seed_wr: list[float] = []
    seed_mdd: list[float] = []
    seed_trades: list[float] = []
    for seed in MATCH_SEEDS:
        picked = _pick(ARM_MATCHED, seed)
        r, w, m = (
            _avg(picked, "total_return"),
            _avg(picked, "win_rate"),
            _avg(picked, "max_drawdown"),
        )
        if r is None or w is None or m is None:
            continue
        seed_ret.append(r)
        seed_wr.append(w)
        seed_mdd.append(m)
        t = _avg(picked, "num_trades")
        if t is not None:
            seed_trades.append(t)
    if not seed_ret or f_ret is None or f_wr is None or f_mdd is None:
        return empty

    n = len(seed_ret)
    # 단측 순위 p(+1 보정) — 매칭 대조군이 필터를 이기거나 비긴 시드의 비율.
    p_ret = (sum(1 for v in seed_ret if v >= f_ret) + 1) / (n + 1)
    p_wr = (sum(1 for v in seed_wr if v >= f_wr) + 1) / (n + 1)
    p_mdd = (sum(1 for v in seed_mdd if v <= f_mdd) + 1) / (n + 1)
    m_trades = sum(seed_trades) / len(seed_trades) if seed_trades else None
    gap_pct = (
        (m_trades - f_trades) / f_trades * 100.0 if f_trades and m_trades is not None else None
    )
    return MatchedTestRow(
        barrier=barrier,
        timeframe=timeframe,
        segment=segment,
        n_symbols=len(symbols),
        n_seeds=n,
        filter_return=f_ret,
        matched_return_mean=sum(seed_ret) / n,
        p_return=p_ret,
        filter_win_rate=f_wr,
        matched_win_rate_mean=sum(seed_wr) / n,
        p_win_rate=p_wr,
        filter_mdd=f_mdd,
        matched_mdd_mean=sum(seed_mdd) / n,
        p_mdd=p_mdd,
        default_mdd=d_mdd,
        filter_trades=f_trades,
        matched_trades=m_trades,
        trade_gap_pct=gap_pct,
    )


def leave_one_out(
    rows: Sequence[PnlRow],
    *,
    barrier: str,
    timeframe: str,
    arm: str,
    segment: str = SEGMENT_OOS,
) -> str:
    """심볼 하나씩 빼고 본 OOS total_return 심볼평균 — 편중 확인."""
    sub = [
        r
        for r in rows
        if r.barrier == barrier
        and r.timeframe == timeframe
        and r.segment == segment
        and r.arm == arm
        and r.seed == SEED_AGGREGATE
        and r.num_trades >= MIN_TRADES_FOR_PNL
    ]
    if len(sub) < 2:
        return "표본 부족"
    parts: list[str] = []
    for drop in sub:
        rest = [r.total_return for r in sub if r.symbol != drop.symbol]
        parts.append(f"−{_bare(drop.symbol)} {sum(rest) / len(rest) * 100:+.2f}%")
    return " · ".join(parts)


def guard_note(rows: Sequence[PnlRow], *, barrier: str, timeframe: str, segment: str) -> str:
    """사이징 바닥(WAN-79)에 걸려 유효 표본이 무너진 필터 셀의 비율.

    필터는 「좁은 존」을 고르는데 가드는 「짧은 손절폭」을 거절한다 — 정면 충돌이다.
    ATR 장벽에서는 손절 거리가 존폭과 무관해지므로 **이 비율 자체가 판정의 재료**다.
    """
    sub = [
        r
        for r in rows
        if r.barrier == barrier
        and r.timeframe == timeframe
        and r.segment == segment
        and r.arm == ARM_FILTER
        and r.seed == SEED_AGGREGATE
    ]
    if not sub:
        return "표본 없음"
    lost = [r for r in sub if r.num_trades < MIN_TRADES_FOR_PNL]
    names = ", ".join(_bare(r.symbol) for r in lost) or "없음"
    return (
        f"필터 셀 {len(sub)}개 중 유효 표본(거래 {MIN_TRADES_FOR_PNL}건) 미달 "
        f"{len(lost)}개({len(lost) / len(sub) * 100:.0f}%) — {names}"
    )


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class VerdictKind(StrEnum):
    """한 TF의 판정 종류 — **문장이 아니라 이 값이 정본이다**.

    ⚠️ 판정 분기를 문장 부분문자열로 찾지 말 것 — 문구를 한 글자만 손대도 조용히 폴백으로
    떨어진다(WAN-142가 실제로 겪고 열거형으로 고친 사고).
    """

    SELECTION = "selection"  # (a) 장벽을 고정해도 필터 우위가 남는다
    GEOMETRY = "geometry"  # (b) 장벽을 고정하면 우위가 사라진다
    NEITHER = "neither"  # (c) 두 장벽 모두 우위 없음
    INDETERMINATE = "indeterminate"  # ⚠️ 판정 불가(유효 표본 부족)


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    text: str

    def __str__(self) -> str:
        return self.text


def _find(
    tests: Sequence[MatchedTestRow], *, barrier: str, timeframe: str, segment: str = SEGMENT_OOS
) -> MatchedTestRow | None:
    return next(
        (
            t
            for t in tests
            if t.barrier == barrier and t.timeframe == timeframe and t.segment == segment
        ),
        None,
    )


def geometry_share(
    tests: Sequence[MatchedTestRow], *, timeframe: str, segment: str = SEGMENT_OOS
) -> float | None:
    """`zone` 장벽의 (필터 − 매칭) 승률 격차 중 장벽 고정으로 **사라지는** 비율.

    승률을 쓰는 이유는 이슈가 지목한 기하의 서명이 승률이기 때문이다(가까운 목표는 더 자주
    닿는다). `zone` 격차가 0 이하면 애초에 설명할 우위가 없으므로 None(뜻을 잃는다).
    """
    zone, atr_row = (
        _find(tests, barrier=BARRIER_ZONE, timeframe=timeframe, segment=segment),
        _find(tests, barrier=BARRIER_ATR, timeframe=timeframe, segment=segment),
    )
    if zone is None or atr_row is None:
        return None
    if (
        zone.filter_win_rate is None
        or zone.matched_win_rate_mean is None
        or atr_row.filter_win_rate is None
        or atr_row.matched_win_rate_mean is None
    ):
        return None
    zone_gap = zone.filter_win_rate - zone.matched_win_rate_mean
    atr_gap = atr_row.filter_win_rate - atr_row.matched_win_rate_mean
    if zone_gap <= 1e-9:
        return None
    return 1.0 - atr_gap / zone_gap


def verdict(tests: Sequence[MatchedTestRow], *, timeframe: str) -> Verdict:
    """한 TF의 (a) 선별 / (b) 기하 / (c) 둘 다 0 판정. OOS 매칭 검정으로 낸다."""
    zone = _find(tests, barrier=BARRIER_ZONE, timeframe=timeframe)
    atr_row = _find(tests, barrier=BARRIER_ATR, timeframe=timeframe)
    if zone is None or atr_row is None or zone.p_return is None or atr_row.p_return is None:
        return Verdict(
            VerdictKind.INDETERMINATE,
            f"**{timeframe}**: ⚠️ 판정 불가 — 한쪽 장벽의 유효 표본(거래 "
            f"{MIN_TRADES_FOR_PNL}건) 셀이 없다.",
        )
    if zone.n_symbols < 3 or atr_row.n_symbols < 3:
        return Verdict(
            VerdictKind.INDETERMINATE,
            f"**{timeframe}**: ⚠️ 판정 불가(대조군) — 필터 팔의 유효 심볼이 "
            f"`{BARRIER_ZONE}` {zone.n_symbols}개 · `{BARRIER_ATR}` {atr_row.n_symbols}개뿐이라 "
            "심볼평균이 의사결정 가치를 갖지 못한다.",
        )
    share = geometry_share(tests, timeframe=timeframe)
    share_txt = "—" if share is None else f"{share * 100:.0f}%"
    detail = (
        f"`{BARRIER_ZONE}` 장벽: 필터 수익 {_pct(zone.filter_return, signed=True)} vs 매칭 "
        f"{_pct(zone.matched_return_mean, signed=True)}(p={zone.p_return:.3f}) · 승률 "
        f"{_pct(zone.filter_win_rate)} vs {_pct(zone.matched_win_rate_mean)}"
        f"(p={_p(zone.p_win_rate)}) → `{BARRIER_ATR}` 장벽: 수익 "
        f"{_pct(atr_row.filter_return, signed=True)} vs "
        f"{_pct(atr_row.matched_return_mean, signed=True)}(p={atr_row.p_return:.3f}) · 승률 "
        f"{_pct(atr_row.filter_win_rate)} vs {_pct(atr_row.matched_win_rate_mean)}"
        f"(p={_p(atr_row.p_win_rate)}) · 승률 격차 소멸분 {share_txt}"
    )
    zone_sig = zone.p_return <= ALPHA
    atr_sig = atr_row.p_return <= ALPHA
    if atr_sig:
        return Verdict(
            VerdictKind.SELECTION,
            f"**{timeframe}**: **(a) 선별이 실재한다** — 장벽 거리를 {ATR_K:g}·ATR로 고정해 "
            "존폭이 익절선을 못 움직이게 묶은 뒤에도 필터가 같은 수의 무작위 대조군을 "
            f"유의하게 이긴다({detail}). 🚨 **채택이 아니다** — 재-베이스라인은 사용자 "
            "결정이고, 이 표는 `baseline`(닿으면 체결) 렌즈 위의 값이다.",
        )
    if zone_sig:
        return Verdict(
            VerdictKind.GEOMETRY,
            f"**{timeframe}**: **(b) 기하가 대부분이다** — 채택 장벽에서는 필터가 대조군을 "
            "이기지만, 장벽 거리를 존폭에서 떼어내면 그 우위가 유의성을 잃는다"
            f"({detail}). 즉 WAN-142가 잰 것은 「좁은 존을 고르는 규칙」이 아니라 "
            "**익절 목표가 가까워지는 산수**였다 — 이 저장소가 WAN-96/114/115/120/124에서 "
            "거듭 만난 「가격·기하지 선별이 아니다」 계열의 또 한 사례다.",
        )
    return Verdict(
        VerdictKind.NEITHER,
        f"**{timeframe}**: **(c) 두 장벽 모두 우위가 없다** — 공통 풀 위에서는 채택 장벽에서도 "
        f"필터가 무작위 대조군을 유의하게 이기지 못한다({detail}). ⚠️ **WAN-142를 반박하는 "
        "것이 아니라 풀이 다르다** — 그쪽은 전 후보이고 이 표는 두 장벽 공통 풀이다.",
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass
class ExperimentResult:
    pnl_rows: list[PnlRow] = field(default_factory=list)
    matched_tests: list[MatchedTestRow] = field(default_factory=list)
    pool_notes: list[str] = field(default_factory=list)


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> ExperimentResult:
    """6심볼 × {15m,1h} × 두 장벽 × 세 팔 — 공통 풀 위의 손익과 매칭 검정."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    result = ExperimentResult()
    # 🚨 이중 필터 방지(WAN-159): 좁은 존 팔을 후보 리스트로 직접 만드므로 엔진의 채택
    # 기본값 필터(1.28)를 꺼야 대조군·매칭이 오염되지 않는다.
    params = harness.build_params(
        fill=harness.fill_preset(LENS_PRIMARY),
        max_zone_width_atr=harness.LEGACY_MAX_ZONE_WIDTH_ATR,
    )
    for symbol in symbols:
        norm = harness.normalize_symbol(symbol)
        for timeframe in timeframes:
            market = harness.load_market_data(
                norm, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, db_path=db_path
            )
            cell = build_cell(market, params=params)
            result.pnl_rows.extend(pnl_rows_for_cell(cell, market))
            raw_txt = " · ".join(f"`{b}` {cell.n_raw.get(b, 0)}" for b in BARRIERS)
            note = f"{_bare(norm)} {timeframe}: 후보 {raw_txt} → 공통 풀 {cell.n_pool}"
            result.pool_notes.append(note)
            print(f"[wan152] {note}", flush=True)
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                result.matched_tests.append(
                    matched_test_row(
                        result.pnl_rows, barrier=barrier, timeframe=timeframe, segment=segment
                    )
                )
    return result


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%" if signed else f"{value * 100:.2f}%"


def _p(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def build_summary_markdown(result: ExperimentResult, *, timeframes: Sequence[str]) -> str:
    lines: list[str] = []
    lines.append("# WAN-152 존폭 필터의 「선별 대 기하」 분리 — 장벽을 고정하면 우위가 남는가\n")
    symbols = sorted({_bare(r.symbol) for r in result.pnl_rows}) or ["—"]
    lines.append(
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)}, 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**, **오늘의 채택 기본값**(`ConfluenceParams()` · "
        "`OrderBlockParams()` — 존 지정가 offset 2bp(WAN-112) · `intrabar_live` 밴드"
        "(WAN-132) · `unconditional` 게이트(WAN-123) · 고정 1.5R · 롱 온리 · "
        "`combine_obs=False`(WAN-149)). 공식 렌즈 **`baseline` 단독**(WAN-128).\n"
    )
    lines.append(
        f"장벽: `{BARRIER_ZONE}` = 손절이 존 무효화 경계(채택 기본값 — **1R이 존폭에 "
        f"비례**) · `{BARRIER_ATR}` = 손절이 진입가 ∓ {ATR_K:g}·ATR(**존폭과 무관하게 고정**, "
        "WAN-133 선례 · WAN-143이 봉내 라이브 밴드에 배선한 `stop_loss_override`) · "
        f"`{BARRIER_ZONE_HEIGHT}` = 손절이 진입가 ∓ 존 높이(WAN-154 §0 — 존 크기 반영, "
        "진입가 무관). 세 팔은 "
        f"`{ARM_DEFAULT}` · `{ARM_FILTER}`(존폭 하위 1/3, IS 문턱 → OOS 적용) · "
        f"`{ARM_MATCHED}`(같은 개수 무작위, 시드 {len(MATCH_SEEDS)}개)다. ⚠️ 판정(§아래)은 "
        f"WAN-152 원 사양대로 `{BARRIER_ZONE}` vs `{BARRIER_ATR}`로 내고, 세 장벽 강건성 "
        "판정은 WAN-154 요약(`wan154_summary.md`)이 낸다.\n"
    )
    lines.append(
        "📌 **핀을 하나도 쓰지 않는다** — `harness.LEGACY_COMBINE_OBS`·`LEGACY_BAND_BAR`·"
        "`pin_band_bar`·`LEGACY_OB_PARAMS`는 옛 리포트 재현용이라 그대로 물려받으면 "
        "**병합 시절 + 옛 밴드 숫자**가 나온다(WAN-142 선례). 회귀 테스트가 라벨이 아니라 "
        "**후보 집합**으로 이것을 고정한다.\n"
    )
    lines.append(
        "⚠️ **WAN-142와 셀을 직접 비교하지 말 것** — 그쪽은 전 후보 위이고 이 표는 "
        "**두 장벽 공통 풀** 위다(아래 §0). 이 표 안의 두 장벽 대조만 성립한다.\n"
    )
    lines.append("재현: `python -m backtest.wan152_selection_vs_geometry`.\n")

    lines.append("## 판정\n")
    for timeframe in timeframes:
        lines.append(f"* {verdict(result.matched_tests, timeframe=timeframe).text}")
    lines.append("")

    lines.append(_pool_section(result))
    lines.append(_matched_section(result, timeframes))
    lines.append(_arm_section(result, timeframes))
    lines.append(_trap_section(result, timeframes))
    lines.append("## 결론\n")
    lines.append(_conclusion(result, timeframes))
    return "\n".join(lines)


def _pool_section(result: ExperimentResult) -> str:
    lines = ["## §0 공통 풀 — 두 장벽이 같은 셋업을 보게 만든다\n"]
    lines.append(
        "k·ATR 오버라이드는 ATR을 못 찾는 셋업(워밍업·갭)에서 `None`을 돌려 그 주문을 "
        "**미체결**로 남긴다(WAN-143: 봉내 경로가 정적 경로와 다른 유일한 지점). 그대로 두면 "
        "두 장벽의 셋업 집합이 달라 「장벽만 바꿨다」가 성립하지 않으므로, "
        "`(탭 시각, 체결 시각, 방향)` 키로 **교집합**한 뒤 존폭/ATR이 유효한 것만 남긴다.\n"
    )
    for note in result.pool_notes:
        lines.append(f"* {note}")
    lines.append("")
    return "\n".join(lines)


def _matched_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["## §1 장벽별 매칭 검정 — 필터 우위가 장벽 고정에서 살아남는가\n"]
    lines.append(
        "p는 단측 순위값(매칭 대조군이 필터를 이기거나 비긴 시드 비율, +1 보정)이라 하한이 "
        f"1/{len(MATCH_SEEDS) + 1} ≈ {1 / (len(MATCH_SEEDS) + 1):.3f}이다. **승률 열이 이 "
        "이슈의 핵심 지표다** — 존폭이 익절선을 당기는 기하 효과의 서명이기 때문이다.\n"
    )
    lines.append(
        "| 장벽 | TF | 구간 | 심볼 | 필터 수익 | 매칭 수익 | p(수익) | 필터 승률 | 매칭 승률 | "
        "p(승률) | 필터 MDD | 매칭 MDD | p(MDD) | 거래 잔차 |\n" + "| -- " * 14 + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                t = _find(
                    result.matched_tests, barrier=barrier, timeframe=timeframe, segment=segment
                )
                if t is None:
                    continue
                if t.trade_gap_pct is None:
                    gap = "—"
                else:
                    warn = " 🚨" if abs(t.trade_gap_pct) > 5.0 else ""
                    gap = f"{t.trade_gap_pct:+.1f}%{warn}"
                lines.append(
                    f"| `{barrier}` | {timeframe} | {segment} | {t.n_symbols} | "
                    f"{_pct(t.filter_return, signed=True)} | "
                    f"{_pct(t.matched_return_mean, signed=True)} | {_p(t.p_return)} | "
                    f"{_pct(t.filter_win_rate)} | {_pct(t.matched_win_rate_mean)} | "
                    f"{_p(t.p_win_rate)} | {_pct(t.filter_mdd)} | {_pct(t.matched_mdd_mean)} | "
                    f"{_p(t.p_mdd)} | {gap} |"
                )
    lines.append("")
    lines.append("**승률 격차의 소멸분(기하의 몫, OOS):**\n")
    for timeframe in timeframes:
        share = geometry_share(result.matched_tests, timeframe=timeframe)
        zone = _find(result.matched_tests, barrier=BARRIER_ZONE, timeframe=timeframe)
        atr_row = _find(result.matched_tests, barrier=BARRIER_ATR, timeframe=timeframe)
        if zone is None or atr_row is None:
            lines.append(f"* {timeframe}: 표본 없음")
            continue
        zg = (
            None
            if zone.filter_win_rate is None or zone.matched_win_rate_mean is None
            else zone.filter_win_rate - zone.matched_win_rate_mean
        )
        ag = (
            None
            if atr_row.filter_win_rate is None or atr_row.matched_win_rate_mean is None
            else atr_row.filter_win_rate - atr_row.matched_win_rate_mean
        )
        share_txt = "—(존 장벽 격차가 0 이하라 비율이 뜻을 잃는다)"
        if share is not None:
            mostly = " ≥ 기준" if share >= GEOMETRY_SHARE_BAR else " < 기준"
            share_txt = f"{share * 100:.0f}%(기준 {GEOMETRY_SHARE_BAR:.0%}{mostly})"
        lines.append(
            f"* **{timeframe}**: `{BARRIER_ZONE}` 격차 "
            f"{'—' if zg is None else f'{zg * 100:+.2f}%p'} → `{BARRIER_ATR}` 격차 "
            f"{'—' if ag is None else f'{ag * 100:+.2f}%p'} · 소멸분 {share_txt}"
        )
    lines.append("")
    return "\n".join(lines)


def _arm_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["### 세 팔 대조표 (심볼평균, `baseline`)\n"]
    lines.append(
        f"⚠️ 심볼평균은 거래 {MIN_TRADES_FOR_PNL}건 미만 셀을 제외한 값이다(제외 셀 수 병기). "
        f"`{ARM_MATCHED}` 행은 시드 평균이다.\n"
    )
    lines.append(
        "| 장벽 | TF | 구간 | 팔 | 후보 | 거래 | total_return | MDD | 승률 | 심볼(제외) |\n"
        + "| -- " * 10
        + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                for arm in ARMS:
                    m = symbol_mean(
                        result.pnl_rows,
                        barrier=barrier,
                        timeframe=timeframe,
                        segment=segment,
                        arm=arm,
                    )
                    nc, nt = m.get("num_candidates"), m.get("num_trades")
                    lines.append(
                        f"| `{barrier}` | {timeframe} | {segment} | `{arm}` | "
                        f"{'—' if nc is None else f'{nc:.0f}'} | "
                        f"{'—' if nt is None else f'{nt:.0f}'} | "
                        f"{_pct(m.get('total_return'), signed=True)} | "
                        f"{_pct(m.get('max_drawdown'))} | {_pct(m.get('win_rate'))} | "
                        f"{m.get('n_symbols'):.0f}({m.get('n_excluded'):.0f}) |"
                    )
    lines.append("")
    lines.append("### 심볼별 분해 (OOS)\n")
    lines.append(
        "| 장벽 | TF | 심볼 | 기본 거래/수익/승률 | 필터 거래/수익/승률 | 매칭 거래/수익/승률 |\n"
        + "| -- " * 6
        + "|"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for symbol in DEFAULT_SYMBOLS:
                picked = {
                    r.arm: r
                    for r in result.pnl_rows
                    if r.barrier == barrier
                    and r.timeframe == timeframe
                    and r.segment == SEGMENT_OOS
                    and r.symbol == harness.normalize_symbol(symbol)
                    and r.seed == SEED_AGGREGATE
                }
                if len(picked) < len(ARMS):
                    continue
                cells = []
                for arm in ARMS:
                    r = picked[arm]
                    warn = " ⚠️" if r.num_trades < MIN_TRADES_FOR_PNL else ""
                    cells.append(
                        f"{r.num_trades:.0f} / {r.total_return * 100:+.2f}% / "
                        f"{r.win_rate * 100:.1f}%{warn}"
                    )
                lines.append(
                    f"| `{barrier}` | {timeframe} | {_bare(symbol)} | " + " | ".join(cells) + " |"
                )
    lines.append("")
    return "\n".join(lines)


def _trap_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["## §2 함정 4개\n"]
    lines.append(
        "**1) 거래 수 오염** — 후보는 맞췄어도 동시 1포지션 시퀀서가 두 팔을 다르게 깎는다.\n"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                t = _find(
                    result.matched_tests, barrier=barrier, timeframe=timeframe, segment=segment
                )
                if t is None or t.trade_gap_pct is None:
                    continue
                if abs(t.trade_gap_pct) <= 5.0:
                    bias = "5% 이내"
                elif t.trade_gap_pct > 0:
                    bias = "🚨 매칭이 더 많이 거래한다 — 노출이 커 **필터에 유리한 잔차**"
                else:
                    bias = "🚨 필터가 더 많이 거래한다 — **필터에 불리한 잔차**"
                lines.append(
                    f"* `{barrier}` {timeframe} {segment}: 필터 {t.filter_trades:.0f}건 vs 매칭 "
                    f"{t.matched_trades:.0f}건({t.trade_gap_pct:+.1f}%) — {bias}"
                )
    lines.append("")
    lines.append("**2) IS→OOS 부호 안정성** — IS에서만 서는 우위는 과최적화의 서명이다.\n")
    for barrier in BARRIERS:
        for timeframe in timeframes:
            lines.append(f"* {_sign_note(result, barrier=barrier, timeframe=timeframe)}")
    lines.append("")
    lines.append("**3) 심볼 편중(OOS total_return leave-one-out):**\n")
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for arm in (ARM_FILTER, ARM_MATCHED):
                loo = leave_one_out(result.pnl_rows, barrier=barrier, timeframe=timeframe, arm=arm)
                lines.append(f"* `{barrier}` {timeframe} `{arm}`: {loo}")
    lines.append("")
    lines.append(
        f"**4) 사이징 바닥 충돌**(WAN-79 `min_stop_distance_fraction={STOP_GUARD_FRACTION:.1%}`) "
        "— 필터는 「좁은 존」을 고르는데 가드는 「짧은 손절폭」을 거절한다. ATR 장벽은 손절 "
        "거리를 존폭에서 떼어내므로 이 충돌이 완화되는지가 함께 보인다.\n"
    )
    for barrier in BARRIERS:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                note = guard_note(
                    result.pnl_rows, barrier=barrier, timeframe=timeframe, segment=segment
                )
                lines.append(f"* `{barrier}` {timeframe} {segment}: {note}")
    lines.append("")
    return "\n".join(lines)


def _sign_note(result: ExperimentResult, *, barrier: str, timeframe: str) -> str:
    """IS·OOS의 (필터 − 매칭) 수익 격차 부호가 같은지 한 문장."""
    is_row = _find(result.matched_tests, barrier=barrier, timeframe=timeframe, segment=SEGMENT_IS)
    oos = _find(result.matched_tests, barrier=barrier, timeframe=timeframe, segment=SEGMENT_OOS)
    if (
        is_row is None
        or oos is None
        or is_row.filter_return is None
        or is_row.matched_return_mean is None
        or oos.filter_return is None
        or oos.matched_return_mean is None
    ):
        return f"`{barrier}` {timeframe}: 표본 부족"
    gi = is_row.filter_return - is_row.matched_return_mean
    go = oos.filter_return - oos.matched_return_mean
    same = (gi > 0) == (go > 0)
    label = "**같다**" if same else "🚨 **뒤집힌다**"
    return (
        f"`{barrier}` {timeframe}: IS 격차 {gi * 100:+.2f}%p → OOS {go * 100:+.2f}%p — 부호가 "
        f"{label}"
    )


def _conclusion(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    verdicts = {tf: verdict(result.matched_tests, timeframe=tf) for tf in timeframes}
    selection = [tf for tf, v in verdicts.items() if v.kind is VerdictKind.SELECTION]
    geometry = [tf for tf, v in verdicts.items() if v.kind is VerdictKind.GEOMETRY]
    neither = [tf for tf, v in verdicts.items() if v.kind is VerdictKind.NEITHER]
    tail = (
        "🚨 **어느 쪽이든 「엣지 찾았다」로 인용 금지** — 이 표는 `baseline`(닿으면 체결) 위의 "
        "값이고, 이 저장소의 모든 플러스가 그렇듯 심볼 편중을 leave-one-out과 함께 읽어야 "
        "한다. **기본값·토대는 바꾸지 않았다**(측정 전용 — `ConfluenceParams()` 불변, 실거래 "
        "보류 `ALPHABLOCK_LIVE_TRADING=false` 유지). 채택 논의로 올리는 것은 **사용자 결정**"
        "이고 **개발자 임의 착수 금지**다(WAN-112/123/132/149와 같은 부류)."
    )
    if geometry and not selection:
        return (
            f"**(b) 존폭 필터의 우위는 기하가 대부분이다({', '.join(geometry)}) — 채택 권고 "
            "없음.** 장벽 거리를 존폭에서 떼어내 "
            f"{ATR_K:g}·ATR로 고정하자 필터의 우위가 유의성을 잃는다. 즉 WAN-142가 잰 "
            "「같은 개수를 무작위로 뽑은 대조군보다 낫다」는 **좁은 존을 고르는 규칙**이 아니라 "
            "**익절 목표가 가까워지는 산수**였다. WAN-96/114/115/120/124가 거듭 지목한 "
            "「가격·기하지 선별이 아니다」 계열에 존폭 축이 합류한다. 📌 **WAN-133의 (a) "
            "선별 판정은 옛 엔진**(`tap` 밴드 + 병합 존) **위의 기록으로 남는다** — 오늘의 "
            "엔진에서 같은 관문을 세운 결과가 이 표이고, 축이 둘 바뀐 뒤에는 서지 않는다. " + tail
        )
    if selection and not geometry:
        return (
            f"**(a) 선별이 실재한다({', '.join(selection)}) — 그래도 채택 권고는 아니다.** "
            f"장벽 거리를 {ATR_K:g}·ATR로 고정해 존폭이 익절선을 못 움직이게 묶은 뒤에도 "
            "필터가 같은 수의 무작위 대조군을 유의하게 이긴다. WAN-142가 「표본 축소」를 "
            "배제한 데 이어 **「기하」도 배제되므로**, 존폭은 이 저장소에서 처음으로 두 관문을 "
            "오늘의 엔진 위에서 통과한 선별 축 후보다. 🚨 **그래도 채택은 별도 재-베이스라인 "
            "결정 이슈다** — 체결 보수화(WAN-98 틱·호가)·사이징 바닥 충돌(§2-4)이 남아 있고, "
            "이 표는 낙관 렌즈 위의 값이다. " + tail
        )
    if neither and not selection and not geometry:
        return (
            "**(c) 두 장벽 모두 우위가 없다 — 채택 권고 없음.** 공통 풀 위에서는 채택 장벽"
            "에서도 필터가 무작위 대조군을 유의하게 이기지 못한다. ⚠️ **WAN-142를 반박하는 "
            "것이 아니라 풀이 다르다** — 그쪽은 전 후보이고 이 표는 두 장벽 공통 풀이라 "
            "ATR 워밍업 셋업이 빠져 있다. " + tail
        )
    return (
        "**⚠️ TF에 갈린다 — 채택 권고 없음.** 선별이 남는 "
        f"TF({', '.join(selection) or '없음'}) · 기하로 설명되는 "
        f"TF({', '.join(geometry) or '없음'}) · 둘 다 0인 "
        f"TF({', '.join(neither) or '없음'})가 갈린다. 하나의 기본값으로 두 작업 TF를 다 "
        "좋게 할 수 없고, 「TF마다 다른 필터」는 IS에서 고르는 새 자유 파라미터다"
        "(WAN-108/143이 경계한 자리). " + tail
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _load_from_csv(pnl_path: Path) -> ExperimentResult:
    """CSV만으로 요약을 재생성한다(격자 재실행 없이 문장·표만 고칠 때)."""
    pnl = [PnlRow.model_validate(rec) for rec in pd.read_csv(pnl_path).to_dict("records")]
    barriers = tuple(dict.fromkeys(r.barrier for r in pnl))
    timeframes = tuple(dict.fromkeys(r.timeframe for r in pnl))
    tests = [
        matched_test_row(pnl, barrier=b, timeframe=tf, segment=seg)
        for b in barriers
        for tf in timeframes
        for seg in (SEGMENT_IS, SEGMENT_OOS)
    ]
    return ExperimentResult(pnl_rows=pnl, matched_tests=tests)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-152 존폭 필터 선별 대 기하 분리")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--pnl-out", type=Path, default=REPORTS_DIR / "wan152_barrier_pnl.csv")
    parser.add_argument("--test-out", type=Path, default=REPORTS_DIR / "wan152_matched_test.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan152_summary.md")
    parser.add_argument(
        "--append", action="store_true", help="기존 pnl CSV에 이어 붙인다(TF 분할 실행)."
    )
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 기존 CSV로 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
    if args.from_csv:
        result = _load_from_csv(args.pnl_out)
        timeframes = tuple(dict.fromkeys(r.timeframe for r in result.pnl_rows))
    else:
        result = run_experiment(
            symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=timeframes,
            start=args.start,
            end=args.end,
            db_path=args.db,
        )
        frame = _rows_to_frame(result.pnl_rows)
        if args.append and args.pnl_out.exists():
            frame = pd.concat([pd.read_csv(args.pnl_out), frame], ignore_index=True)
        _write_csv(frame, args.pnl_out)
        print(f"[wan152] pnl → {args.pnl_out}")
        # 이어 붙였으면 검정·요약은 **합친 표** 위에서 다시 낸다(풀 메모는 이번 실행 몫만
        # 남으므로 그대로 물려준다 — CSV에는 그 열이 없다).
        notes = result.pool_notes
        result = _load_from_csv(args.pnl_out)
        result.pool_notes = notes
        timeframes = tuple(dict.fromkeys(r.timeframe for r in result.pnl_rows))
    _write_csv(_rows_to_frame(result.matched_tests), args.test_out)
    print(f"[wan152] matched_test → {args.test_out}")

    summary = build_summary_markdown(result, timeframes=timeframes)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan152] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
