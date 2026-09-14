"""WAN-375 Phase 1 — 조건부 손익비의 **관측**: 손절폭이 좁은 거래가 실제로 더 멀리 가는가.

## 한 줄

사용자 제안(2026-08-25 · 2026-09-14): *「손절폭이 좁으면 익절을 더 멀리」* · *「1% 1.5% 2%
처럼 퍼센트로, 타임프레임별로 · 재탭/재진입 없이 첫 진입이 평균 몇 % 올랐나 · 가장 자주
닿는 구간은」* · *「볼린저밴드가 존 밑으로 떨어지면 거기서 종료」*(WAN-416 흡수). 이 모듈은
그 셋 모두의 **앞단 관측**만 낸다 — 매매를 하나도 안 바꾸고, 손익 격자(Phase 2)는 별도다.

## 어떻게 재나 — 익절을 끈 실행 한 번

채택 엔진(핀 없음 · 오늘 기본값 — 봉내 라이브 밴드 · 오프셋 2bp · 인과 취소 · 존폭 필터 끔 ·
게이트 없음 · 롱 온리)에서 **익절만 끈** 후보를 만든다(WAN-90이 쓴 `take_profit_mode="line"`
변형). 거래는 무효화 경계(손절)나 데이터 끝까지 살아 있어 MFE가 1.5R에서 검열되지 않는다.
시뮬레이터의 옵트인 순수 관측(`observe_hold_path`, WAN-375)이 체결 셋업마다 셋을 싣는다:

* **청산 스텝을 뺀 MFE**(`mfe_r_pre_exit`) — 이 값이 `X` 이상이면 「목표를 `X`에 걸었다면
  손절보다 먼저 익절됐다」와 **같은 뜻**이다(같은 스텝 손절 우선 · 진입 스텝 낙관은 엔진과 같다).
  그래서 도달률 = 그 목표의 **셋업 층 승률**이다.
* **채택 목표(1.5R) 도달 시각** — 「익절을 켰다면 그 시각에 나갔다」. 이것으로 익절 켠 판의
  셋업별 결과를 **재시뮬 없이** 되살리고, 그 복원이 실제 채택 엔진 후보와 같은지 **칸마다
  대조한다**(검산 (b)).
* **보유 중 밴드가 진입 규칙 3에 처음 걸린 시각**(롱 = 밴드 하단이 존 바닥 아래) — 진입을
  막는 바로 그 규칙을 보유 중에 다시 묻는다(새 규칙이 아니라 시간 확장).

## 🚨 이 표는 셋업 층 관측이지 북 판정이 아니다 (WAN-341)

시퀀싱 이전 **셋업 단위**다(WAN-90 규약) — 칸 슬롯·공유 자본·재진입이 없다. 익절을 끄면
「익절 후 재진입」이 **정의상 생기지 않으므로**(WAN-367: 0은 불가능과 구분 안 된다 — 여기선
불가능이다) `재진입` 열은 없다. 스코프는 셋이다 — 「첫 탭만」(`tap_index == 0` = 재탭 끔
`retap_mode="once"`와 같은 셋업) · 「존별 첫 체결」(첫 탭이 볼린저에 막히거나 미체결이면 재탭에서의
첫 체결 = WAN-413 `once_fill`) · 「전체 탭」(현행 채택). 첫 탭이 **왜** 진입이 안 됐는지도 탭
단위로 센다(볼린저가 끝까지 막음 · 미체결 · 만료 · 무효화 선행). 🚨 **판단은 북에서만
낸다** — 이 모듈의 기대 net R 열은 「어느 범위를 재볼지 고르는 지도」이지 채택 근거가 아니다.

⚠️ **「가장 자주 닿는 구간에 익절을 두라」로 읽지 말 것** — MFE는 *지나간 최고점*이다(WAN-90:
큰 MFE는 왕복이라 홀드로 못 챙긴다). 최빈 구간·도달률 곡선은 **지도**이고 답은 Phase 2 손익이다.

⚠️ **손절폭 가드(0.3%, WAN-76/79)**: 북은 손절폭이 그보다 좁은 셋업을 사이징에서 버린다.
주 표는 **가드 통과분**만 보고, 잘리는 부류(`<0.30%`)는 참고 행으로만 싣는다 — 좁은 버킷의
표본은 **가드가 이미 걸러낸 뒤의 잔존분**이다.

## §2 갈림 (착수 전에 코드로 못 박음 — `verdict_for`)

좁은 쪽(손절폭 0.30~0.50%) 대 넓은 쪽(≥1.00%)의 **청산 전 도달률**을 2R · 2.5R · 3R에서
비교한다. 세 목표 **전부** 좁은 쪽이 2σ(합성 이항 표준오차) 넘게 높고 그것이 **앞구간(`is`)과
뒷구간(`oos_warm`) 둘 다**에서 성립하면 「간다」, 세 목표 전부 2σ 넘게 낮으면 「짧다」, 그 밖은
「비슷」이다. 스코프마다 이 판정을 내고 **모두 같은 답**일 때만 그 답을 쓰며, 갈리면
「혼합」이다(세 스코프가 전부 같은 답이어야 한다 · 한 무리가 100셋업 미만이면 그 비교는 부호를 내지
않는다). 「간다」가 아니면
이슈 §2대로 **Phase 2를 착수하지 않는다**.

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목 × 15m·1h·2h·4h × 못 박은 6년(2020-09-15~2026-07-22) · 구간은 따뜻한 절단
(`trigger_time`이 WAN-166 평가 경계 이후면 `oos_warm`, 이전이면 `is`) · 비용은 채택 회계
(진입 메이커 2bp · 익절 메이커 2bp · 손절 테이커 4bp＋슬리피지 5bp, WAN-370/396).
⚠️ **차가운 `oos`는 없다**(셋업 층 관측이라 구간마다 재탐지할 이유가 없다 — 대신 구간 선택
편향 없이 앞·뒤를 둘 다 본다).

재현::

    uv run python -m backtest.wan375_conditional_rr --jobs 4
    uv run python -m backtest.wan375_conditional_rr --from-csv        # 요약만
    uv run python -m backtest.wan375_conditional_rr --pilot            # BTC 4h 한 칸
"""

from __future__ import annotations

import argparse
import math
import numbers
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan370_cost_decomposition import STOP_WIDTH_EDGES, stop_width_bucket
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    UnrestedTap,
    _Candidate,
    build_zone_limit_candidates,
)
from common.costs import Liquidity
from strategy.models import ConfluenceParams, OrderBlockParams

REPORTS_DIR = Path("backtest/reports")
REACH_CSV = REPORTS_DIR / "wan375_reach_by_width.csv"
PCT_CSV = REPORTS_DIR / "wan375_pct_by_tf.csv"
BAND_CSV = REPORTS_DIR / "wan375_band_break.csv"
CHECKSUM_CSV = REPORTS_DIR / "wan375_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan375_conditional_rr_summary.md"
#: 셋업 단위 원자료 — 커서(수십 MB) 커밋하지 않는다(`backtest/cache/`는 gitignore).
SETUPS_CSV = Path("backtest/cache/wan375/setups.csv.gz")
TAPS_CSV = Path("backtest/cache/wan375/taps.csv.gz")
TAP_OUTCOME_CSV = REPORTS_DIR / "wan375_tap_outcome.csv"
WAN408_DAILY_CSV = REPORTS_DIR / "wan408_daily.csv"

SEGMENT_IS = harness.SEGMENT_IS
SEGMENT_OOS_WARM = harness.SEGMENT_OOS_WARM
SEGMENT_FULL = harness.SEGMENT_FULL
SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM)

SCOPE_FIRST = "first"
"""첫 탭만 — `tap_index == 0`인 체결 셋업. **`retap_mode="once"`(WAN-404)와 같은 셋업 집합**이다
(셋업 층은 시퀀싱이 없어 존의 첫 탭 결과가 재탭 정책과 무관하다 — 테스트가 동작으로 고정)."""
SCOPE_FIRST_FILL = "first_fill"
"""존별 첫 체결 — 존마다 **처음 체결된** 셋업 하나(첫 탭이 볼린저에 막히거나 미체결이었으면
재탭에서의 첫 체결). 「첫 체결까지만」(WAN-413 `once_fill`)의 셋업 층 판이다."""
SCOPE_ALL = "all"
"""전체 탭 — 채택 기본값(`retap_mode="every_tap"`)이 실제로 거는 셋업 전부."""
SCOPES: tuple[str, ...] = (SCOPE_FIRST, SCOPE_FIRST_FILL, SCOPE_ALL)
SCOPE_LABELS: dict[str, str] = {
    SCOPE_FIRST: "첫 탭만",
    SCOPE_FIRST_FILL: "존별 첫 체결",
    SCOPE_ALL: "전체 탭",
}

ARM_BAND = "band"
"""진입가 팔 — 채택 규칙: 밴드가 존 위면 존 상단, 존 안이면 **밴드가**, 존 아래면 진입 없음."""
ARM_ZONE_TOP = "zone_top"
"""진입가 팔 — 볼린저는 **진입 여부만** 가른다: 밴드가 존 아래면 진입 없음, 존 안이거나 위면
**존 상단**(+오프셋)에 건다(사용자 제안 2026-09-14 · 엔진 옵트인 `select_only`, WAN-131 B팔)."""
ARMS: tuple[str, ...] = (ARM_BAND, ARM_ZONE_TOP)
ARM_LABELS: dict[str, str] = {
    ARM_BAND: "밴드가 진입(채택)",
    ARM_ZONE_TOP: "존 상단 진입(볼린저는 진입 여부만)",
}
#: 「존 상단」 팔의 복원 대조를 건너뛰는 TF — 15m 칸이 계산량의 대부분이라 그 팔만 뺀다(채택
#: 팔은 전 칸 대조). 복원 로직은 팔과 무관한 코드라 채택 팔 48칸 + 이 팔 36칸으로 충분히 선다.
ZONE_TOP_CHECKSUM_SKIP_TFS: frozenset[str] = frozenset({"15m"})

TAP_FIRST = "first_tap"
TAP_RETAP = "retap"
OUTCOME_FILLED = "filled"
OUTCOME_NEVER_RESTED = "never_rested"
"""주문이 한 번도 안 걸림 — 봉내 밴드가 줄곧 존보다 불리(규칙 3)."""
TAP_OUTCOMES: tuple[str, ...] = (
    OUTCOME_FILLED,
    OUTCOME_NEVER_RESTED,
    "no_touch",
    "cancelled_expired",
    "cancelled_invalidated",
    "cancelled_condition_failed",
)
TAP_OUTCOME_LABELS: dict[str, str] = {
    OUTCOME_FILLED: "체결",
    OUTCOME_NEVER_RESTED: "볼린저가 끝까지 막음",
    "no_touch": "미체결(데이터 끝)",
    "cancelled_expired": "만료(24봉)",
    "cancelled_invalidated": "무효화 선행",
    "cancelled_condition_failed": "조건 취소",
}

ALL_TF = "all"
R_TARGETS: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0)
PCT_TARGETS: tuple[float, ...] = tuple(round(0.005 * i, 4) for i in range(1, 11))
"""퍼센트 도달률 공선 — 0.5% 단위 0.5%~5.0%(이슈 §1-4)."""
PCT_BIN = 0.005
"""최빈 구간을 셀 때의 퍼센트 칸 폭(0.5%)."""

#: §2 갈림의 두 무리(손절폭 분수). 착수 전에 못 박은 값이다 — 결과를 보고 옮기지 않는다.
NARROW_RANGE: tuple[float, float] = (0.003, 0.005)
WIDE_MIN = 0.010
VERDICT_TARGETS: tuple[float, ...] = (2.0, 2.5, 3.0)
VERDICT_SIGMA = 2.0
VERDICT_MIN_N = 100
"""한 무리의 셋업이 이보다 적으면 그 비교는 부호를 내지 않는다(0) — 작은 표본의 우연한 차가
판정을 만들지 못하게 한다. 착수 전에 못 박은 값이다."""
VERDICT_GO = "간다"
VERDICT_SHORTER = "짧다"
VERDICT_SIMILAR = "비슷"
VERDICT_MIXED = "혼합"

KST_OFFSET_MS = 9 * 3_600_000
WORST_DAYS = 10


# --------------------------------------------------------------------------- #
# 셋업 관측 행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SetupObs:
    """익절을 끈 실행의 체결 셋업 하나 (WAN-375 Phase 1)."""

    symbol: str
    timeframe: str
    arm: str
    segment: str
    is_long: bool
    tap_index: int
    zone: str
    """존 식별자(`zone_key`를 정렬해 이은 문자열, WAN-83) — 「존별 첫 체결」의 묶음 키."""
    trigger_time: int
    entry_time: int
    entry_price: float
    stop_price: float
    exit_time: int
    exit_reason: str
    """`stop_loss` 또는 `end_of_data`(익절이 꺼져 있어 둘뿐이다)."""
    mfe_r: float | None
    mfe_r_pre_exit: float | None
    target_reach_time: int | None
    band_break_time: int | None
    band_break_r: float | None
    band_break_mfe_r: float | None

    @property
    def stop_width(self) -> float:
        """손절폭(진입가 대비 분수) — 사이징 가드와 같은 정의(`|진입−손절| / 진입`)."""
        return abs(self.entry_price - self.stop_price) / self.entry_price


def arm_params(base: ConfluenceParams, arm: str) -> ConfluenceParams:
    """진입가 팔의 파라미터. `band`는 그대로, `zone_top`은 `select_only=True`만 켠다."""
    if arm == ARM_BAND:
        return base
    if arm != ARM_ZONE_TOP:
        raise ValueError(f"모르는 진입가 팔: {arm!r}")
    deviation = base.deviation_filter
    if deviation is None:
        raise ValueError("zone_top 팔은 볼린저(진입 여부 판정)가 켜진 파라미터에서만 뜻이 있다.")
    return base.model_copy(
        update={"deviation_filter": deviation.model_copy(update={"select_only": True})}
    )


def zone_label(zone_key: frozenset[int] | None) -> str:
    """`zone_key` → 안정적 문자열. 없으면 **시끄럽게 죽는다**(묶음이 조용히 틀어지면 안 된다)."""
    if not zone_key:
        raise AssertionError("zone_key가 없는 셋업이 있습니다 — 존별 첫 체결을 묶을 수 없습니다.")
    return "-".join(str(i) for i in sorted(zone_key))


@dataclass(frozen=True)
class TapObs:
    """존 탭 하나의 결과 (WAN-375 — 「첫 탭에서 진입이 안 된 이유」)."""

    symbol: str
    timeframe: str
    arm: str
    segment: str
    tap_index: int
    zone: str
    trigger_time: int
    outcome: str


def tap_observations(
    diagnostics: Sequence[SetupDiagnostic],
    unrested: Sequence[UnrestedTap],
    *,
    symbol: str,
    timeframe: str,
    boundary_ms: int,
    arm: str = ARM_BAND,
) -> list[TapObs]:
    """주문이 걸린 탭(`setup_sink`)과 한 번도 안 걸린 탭(`unrested_sink`)을 한 표로."""
    taps: list[TapObs] = []
    for diag in diagnostics:
        outcome = OUTCOME_FILLED if diag.filled else diag.status.value
        taps.append(
            TapObs(
                symbol=symbol,
                timeframe=timeframe,
                arm=arm,
                segment=SEGMENT_OOS_WARM if diag.trigger_time >= boundary_ms else SEGMENT_IS,
                tap_index=diag.tap_index,
                zone=zone_label(diag.zone_key),
                trigger_time=diag.trigger_time,
                outcome=outcome,
            )
        )
    for tap in unrested:
        taps.append(
            TapObs(
                symbol=symbol,
                timeframe=timeframe,
                arm=arm,
                segment=SEGMENT_OOS_WARM if tap.trigger_time >= boundary_ms else SEGMENT_IS,
                tap_index=tap.tap_index,
                zone=zone_label(tap.zone_key),
                trigger_time=tap.trigger_time,
                outcome=OUTCOME_NEVER_RESTED,
            )
        )
    unknown = {t.outcome for t in taps} - set(TAP_OUTCOMES)
    if unknown:
        raise AssertionError(
            f"모르는 탭 결과가 있습니다: {sorted(unknown)} — 조용히 흡수하지 않는다."
        )
    return taps


def observations_from_candidates(
    candidates: Sequence[_Candidate],
    *,
    symbol: str,
    timeframe: str,
    boundary_ms: int,
    arm: str = ARM_BAND,
) -> list[SetupObs]:
    """관측을 켠 무-익절 후보를 관측 행으로 옮긴다. 관측이 없으면 **시끄럽게 죽는다**.

    `hold_path`가 없는 후보가 섞이면 「밴드 무너짐 0건」이 「관측 안 켬」과 구분되지 않는다
    (WAN-367) — 조용히 건너뛰지 않는다.
    """
    rows: list[SetupObs] = []
    for cand in candidates:
        probe = cand.hold_path
        if probe is None:
            raise AssertionError(
                f"{symbol} {timeframe}: hold_path 관측이 없는 후보가 있습니다 — "
                "observe_hold_path가 실제로 켜졌는지 확인하세요(라벨만 붙은 실행 방지)."
            )
        if cand.reason is ExitReason.TAKE_PROFIT:
            raise AssertionError(
                f"{symbol} {timeframe}: 익절을 끈 실행에 익절 청산이 있습니다 — 무-익절 변형이 "
                "실제로 적용되지 않았습니다."
            )
        rows.append(
            SetupObs(
                symbol=symbol,
                timeframe=timeframe,
                arm=arm,
                segment=SEGMENT_OOS_WARM if cand.trigger_time >= boundary_ms else SEGMENT_IS,
                is_long=cand.side is PositionSide.LONG,
                tap_index=cand.tap_index,
                zone=zone_label(cand.zone_key),
                trigger_time=cand.trigger_time,
                entry_time=cand.entry_time,
                entry_price=cand.entry_price,
                stop_price=cand.stop_price,
                exit_time=cand.exit_time,
                exit_reason=cand.reason.value,
                mfe_r=cand.mfe_r,
                mfe_r_pre_exit=probe.mfe_r_pre_exit,
                target_reach_time=probe.target_reach_time,
                band_break_time=probe.band_break_time,
                band_break_r=probe.band_break_r,
                band_break_mfe_r=probe.band_break_mfe_r,
            )
        )
    return rows


def derive_tp_on(obs: SetupObs) -> tuple[str, int]:
    """익절(채택 배수)을 켰다면의 청산 `(사유, 시각)` — 재시뮬 없이 관측에서 되살린다.

    청산 이전 스텝에서 목표에 닿았으면 그 시각에 익절, 아니면 무-익절 실행의 청산 그대로다
    (같은 스텝 손절 우선이라 목표가 손절 스텝에만 닿았으면 손절이다 — `mfe_r_pre_exit`가
    그 스텝을 빼는 이유와 같다). 검산 (b)가 이 복원을 실제 채택 엔진 후보와 대조한다.
    """
    if obs.target_reach_time is not None:
        return ExitReason.TAKE_PROFIT.value, obs.target_reach_time
    return obs.exit_reason, obs.exit_time


# --------------------------------------------------------------------------- #
# 비용 (채택 회계를 cfg에서 읽는다 — 요율을 복제하지 않는다, WAN-77)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CostRates:
    """거래당 비용 요율 — `BacktestConfig` 한 곳에서만 읽는다."""

    entry_fee: float
    take_profit_fee: float
    stop_fee: float
    stop_slippage: float

    @classmethod
    def from_config(cls, cfg: BacktestConfig) -> CostRates:
        """진입은 후보 기본값(메이커 — `_Candidate.entry_liquidity`), 청산은 사유별 단일 소스
        (`BacktestConfig.exit_liquidity`, WAN-370)에 물어본다. 진입 유동성을 설정에서 읽지
        않는 이유는 WAN-396이다(그 필드는 사문화됐다)."""
        model = cfg.cost_model
        entry_liquidity = _Candidate.__dataclass_fields__["entry_liquidity"].default
        assert isinstance(entry_liquidity, Liquidity)
        tp_liquidity = cfg.exit_liquidity(ExitReason.TAKE_PROFIT)
        stop_liquidity = cfg.exit_liquidity(ExitReason.STOP_LOSS)
        return cls(
            entry_fee=model.fee_rate(entry_liquidity),
            take_profit_fee=model.fee_rate(tp_liquidity),
            stop_fee=model.fee_rate(stop_liquidity),
            stop_slippage=model.slippage_for(stop_liquidity),
        )


def cost_r(width: float, target_r: float, rates: CostRates) -> tuple[float, float]:
    """손절폭 `width`(분수)인 롱에서 `(이길 때 비용 R, 질 때 비용 R)` — 근사(명목은 가격 비례).

    진입가를 1로 두면 1R = `width`다. 이기면 진입 수수료＋익절 수수료(목표가 명목),
    지면 진입 수수료＋손절 수수료＋손절 슬리피지(손절가 명목). 펀딩은 넣지 않는다.
    """
    if width <= 0.0:
        raise ValueError(f"손절폭은 양수여야 합니다: {width}")
    win_notional = 1.0 + target_r * width
    loss_notional = 1.0 - width
    c_win = (rates.entry_fee + rates.take_profit_fee * win_notional) / width
    c_loss = (rates.entry_fee + (rates.stop_fee + rates.stop_slippage) * loss_notional) / width
    return c_win, c_loss


def breakeven_win_rate(target_r: float, c_win: float, c_loss: float) -> float:
    """목표 `target_r`에서 기대 net R이 0이 되는 승률 — `(1+c_loss) / (T−c_win+1+c_loss)`."""
    return (1.0 + c_loss) / (target_r - c_win + 1.0 + c_loss)


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    checksum: bool


@dataclass(frozen=True)
class ArmCheck:
    """한 칸 · 한 진입가 팔의 검산 (b) — 대조를 안 했으면 수치가 `None`이다."""

    arm: str
    setups: int
    adopted_candidates: int | None
    entry_mismatches: int | None
    exit_mismatches: int | None


@dataclass(frozen=True)
class CellResult:
    symbol: str
    timeframe: str
    rows: tuple[SetupObs, ...]
    taps: tuple[TapObs, ...]
    checks: tuple[ArmCheck, ...]
    seconds: float


def uncensored_params(base: ConfluenceParams) -> ConfluenceParams:
    """채택 기본값에서 **익절만** 끈다(WAN-90 `_uncensored_params`와 같은 변형).

    `take_profit_mode="line"` + `use_line_take_profit=False`(기본)면 익절 목표가 `None`이라
    익절 청산이 없다. 진입가·손절·볼린저·오프셋·취소 시점은 그대로다.
    """
    if base.use_line_take_profit:
        raise ValueError("use_line_take_profit이 켜진 파라미터로는 익절을 끌 수 없습니다.")
    return base.model_copy(update={"take_profit_mode": "line"})


def run_cell(task: _Task) -> CellResult:
    """한 칸: 무-익절 관측 후보 → 관측 행, 그리고 (옵트인) 채택 엔진과의 복원 대조."""
    started = time.monotonic()
    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=True,
        funding=False,
    )
    if market.empty or market.df_1m.empty:
        raise ValueError(f"{task.symbol} {task.timeframe}: 데이터가 없습니다(창 확인).")
    boundary = harness.eval_boundary_ms(market, harness.WARM_OOS_SEGMENT)
    assert boundary is not None
    ob_result = harness.detect_order_blocks(market, OrderBlockParams())
    adopted_params = harness.build_params()
    cfg = harness.build_config(task.timeframe)
    all_rows: list[SetupObs] = []
    all_taps: list[TapObs] = []
    checks: list[ArmCheck] = []
    for arm in ARMS:
        params = arm_params(adopted_params, arm)
        diagnostics: list[SetupDiagnostic] = []
        unrested: list[UnrestedTap] = []
        uncensored, _ = build_zone_limit_candidates(
            market.htf_df,
            market.df_1m,
            task.timeframe,
            params=uncensored_params(params),
            cfg=cfg,
            order_block_result=ob_result,
            setup_sink=diagnostics,
            observe_hold_path=True,
            hold_path_target_r=params.take_profit_r,
            unrested_sink=unrested,
        )
        rows = observations_from_candidates(
            uncensored,
            symbol=task.symbol,
            timeframe=task.timeframe,
            boundary_ms=boundary,
            arm=arm,
        )
        taps = tap_observations(
            diagnostics,
            unrested,
            symbol=task.symbol,
            timeframe=task.timeframe,
            boundary_ms=boundary,
            arm=arm,
        )
        filled_taps = sum(1 for t in taps if t.outcome == OUTCOME_FILLED)
        if filled_taps != len(rows):
            # 탭 표의 「체결」과 셋업 표는 같은 셋업을 다른 층에서 센 것이다 — 갈리면 둘 중 하나가
            # 다른 것을 세고 있다(WAN-333 부류).
            raise AssertionError(
                f"{task.symbol} {task.timeframe} {arm}: 체결 탭 {filled_taps} ≠ 셋업 {len(rows)}"
            )
        check_this = task.checksum and not (
            arm == ARM_ZONE_TOP and task.timeframe in ZONE_TOP_CHECKSUM_SKIP_TFS
        )
        num_adopted: int | None = None
        entry_mismatches: int | None = None
        exit_mismatches: int | None = None
        if check_this:
            adopted, _ = build_zone_limit_candidates(
                market.htf_df,
                market.df_1m,
                task.timeframe,
                params=params,
                cfg=cfg,
                order_block_result=ob_result,
            )
            num_adopted = len(adopted)
            entry_mismatches, exit_mismatches = reconstruction_mismatches(adopted, rows)
        checks.append(
            ArmCheck(
                arm=arm,
                setups=len(rows),
                adopted_candidates=num_adopted,
                entry_mismatches=entry_mismatches,
                exit_mismatches=exit_mismatches,
            )
        )
        all_rows.extend(rows)
        all_taps.extend(taps)
    elapsed = time.monotonic() - started
    summary = " · ".join(
        f"{c.arm} 셋업 {c.setups}"
        + ("" if c.entry_mismatches is None else f"(검산 {c.entry_mismatches}/{c.exit_mismatches})")
        for c in checks
    )
    print(f"[wan375] {task.symbol} {task.timeframe}: {summary} · {elapsed:.0f}s", flush=True)
    return CellResult(
        symbol=task.symbol,
        timeframe=task.timeframe,
        rows=tuple(all_rows),
        taps=tuple(all_taps),
        checks=tuple(checks),
        seconds=elapsed,
    )


def reconstruction_mismatches(
    adopted: Sequence[_Candidate], rows: Sequence[SetupObs]
) -> tuple[int, int]:
    """검산 (b): 진입 집합이 같은가 · 익절 켠 판 복원이 채택 엔진 청산과 같은가.

    진입 집합이 다르면 청산 대조가 뜻을 잃으므로 그 칸의 청산 불일치는 전체 셋업 수로 센다.
    """
    adopted_keys = [(c.trigger_time, c.tap_index, c.entry_time, c.entry_price) for c in adopted]
    obs_keys = [(r.trigger_time, r.tap_index, r.entry_time, r.entry_price) for r in rows]
    if adopted_keys != obs_keys:
        differing = sum(1 for a, b in zip(adopted_keys, obs_keys, strict=False) if a != b)
        return differing + abs(len(adopted_keys) - len(obs_keys)), max(len(adopted), len(rows))
    exits = 0
    for cand, obs in zip(adopted, rows, strict=True):
        reason, exit_time = derive_tp_on(obs)
        if (cand.reason.value, cand.exit_time) != (reason, exit_time):
            exits += 1
    return 0, exits


def run_census(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    checksum: bool = True,
) -> list[CellResult]:
    """전 칸을 돈다. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121)."""
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            checksum=checksum,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1:
        return [run_cell(task) for task in tasks]
    with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
        return list(executor.map(run_cell, tasks))


def setups_frame(results: Sequence[CellResult]) -> pd.DataFrame:
    """관측 행 전체를 한 표로 — 파생 열(손절폭·버킷·익절 켠 판 복원)을 함께 싣는다."""
    records = []
    for result in results:
        for obs in result.rows:
            record = asdict(obs)
            reason, exit_time = derive_tp_on(obs)
            record["stop_width"] = obs.stop_width
            record["tp_on_reason"] = reason
            record["tp_on_exit_time"] = exit_time
            records.append(record)
    return _with_derived(pd.DataFrame.from_records(records))


def _with_derived(frame: pd.DataFrame) -> pd.DataFrame:
    """CSV에서 되읽은 표에도 같은 파생 열을 붙인다(한 곳에서만 계산한다)."""
    if frame.empty:
        return frame
    out = frame.copy()
    out["stop_width"] = (out["entry_price"] - out["stop_price"]).abs() / out["entry_price"]
    out["width_bucket"] = out["stop_width"].map(stop_width_bucket)
    out["mfe_pct_pre_exit"] = out["mfe_r_pre_exit"] * out["stop_width"]
    # 존별 첫 체결: 같은 칸·같은 존에서 체결 시각이 가장 이른 셋업(동률이면 탭이 이른 쪽).
    keys = ["arm", "symbol", "timeframe", "zone"]
    ordered = out.sort_values([*keys, "entry_time", "trigger_time"])
    first = ~ordered.duplicated(subset=keys, keep="first")
    out["is_first_fill"] = first.reindex(out.index)
    return out


def taps_frame(results: Sequence[CellResult]) -> pd.DataFrame:
    return pd.DataFrame.from_records([asdict(t) for r in results for t in r.taps])


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _reached(frame: pd.DataFrame, target_r: float) -> pd.Series:
    """청산 전 도달 여부 — `mfe_r_pre_exit`가 없으면(진입 스텝 청산) 못 닿은 것이다."""
    return frame["mfe_r_pre_exit"].fillna(-math.inf) >= target_r


def _scope(frame: pd.DataFrame, scope: str) -> pd.DataFrame:
    if scope == SCOPE_FIRST:
        return frame[frame["tap_index"] == 0]
    if scope == SCOPE_FIRST_FILL:
        return frame[frame["is_first_fill"].astype(bool)]
    return frame


def _segment(frame: pd.DataFrame, segment: str) -> pd.DataFrame:
    if segment == SEGMENT_FULL:
        return frame
    return frame[frame["segment"] == segment]


def _ev_at(frame: pd.DataFrame, target_r: float, rates: CostRates) -> float | None:
    """고정 `target_r` 익절의 **셋업 층** 기대 net R(펀딩·북·열린 거래 제외)."""
    widths = frame["stop_width"].to_numpy()
    reached = _reached(frame, target_r).to_numpy()
    open_end = (frame["exit_reason"] == ExitReason.END_OF_DATA.value).to_numpy()
    total = 0.0
    count = 0
    for width, hit, is_open in zip(widths, reached, open_end, strict=True):
        if not hit and is_open:
            continue  # 아직 결판이 안 났다 — 이긴 것도 진 것도 아니다.
        c_win, c_loss = cost_r(float(width), target_r, rates)
        total += (target_r - c_win) if hit else (-1.0 - c_loss)
        count += 1
    return total / count if count else None


def _loss_cost(width: float, rates: CostRates) -> float:
    """질 때 비용 R — 목표와 무관하다(목표 인자는 이길 때 명목에만 들어간다)."""
    return cost_r(width, 1.0, rates)[1]


def _breakeven_at(frame: pd.DataFrame, target_r: float, rates: CostRates) -> float | None:
    if frame.empty:
        return None
    c_win_sum = 0.0
    c_loss_sum = 0.0
    for width in frame["stop_width"].to_numpy():
        c_win, c_loss = cost_r(float(width), target_r, rates)
        c_win_sum += c_win
        c_loss_sum += c_loss
    n = len(frame)
    return breakeven_win_rate(target_r, c_win_sum / n, c_loss_sum / n)


def _per_arm(frame: pd.DataFrame, build: Callable[[pd.DataFrame], pd.DataFrame]) -> pd.DataFrame:
    """진입가 팔마다 따로 집계해 `arm` 열을 앞에 붙인다(두 팔을 한 분모에 섞지 않는다)."""
    parts = []
    for arm in ARMS:
        sub = frame[frame["arm"] == arm]
        if sub.empty:
            continue
        built = build(sub)
        if built.empty:
            continue
        built.insert(0, "arm", arm)
        parts.append(built)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def reach_rows(frame: pd.DataFrame, rates: CostRates) -> pd.DataFrame:
    return _per_arm(frame, lambda sub: _reach_rows_arm(sub, rates))


def pct_rows(frame: pd.DataFrame, rates: CostRates) -> pd.DataFrame:
    return _per_arm(frame, lambda sub: _pct_rows_arm(sub, rates))


def band_rows(frame: pd.DataFrame, bad_days: Sequence[str], rates: CostRates) -> pd.DataFrame:
    return _per_arm(frame, lambda sub: _band_rows_arm(sub, bad_days, rates))


def _reach_rows_arm(frame: pd.DataFrame, rates: CostRates) -> pd.DataFrame:
    """표 A·B — 구간 × 스코프 × TF(전체 포함) × 손절폭 버킷."""
    records: list[dict[str, object]] = []
    timeframes = [ALL_TF, *sorted(frame["timeframe"].unique(), key=_tf_order)]
    buckets = _bucket_order()
    for segment in SEGMENTS:
        seg = _segment(frame, segment)
        for scope in SCOPES:
            scoped = _scope(seg, scope)
            for tf in timeframes:
                tf_frame = scoped if tf == ALL_TF else scoped[scoped["timeframe"] == tf]
                for bucket in buckets:
                    sub = tf_frame[tf_frame["width_bucket"] == bucket]
                    if sub.empty:
                        continue
                    record: dict[str, object] = {
                        "segment": segment,
                        "scope": scope,
                        "timeframe": tf,
                        "width_bucket": bucket,
                        "guard_cut": bool(sub["stop_width"].max() < _guard_fraction()),
                        "n": len(sub),
                        "open_share": float(
                            (sub["exit_reason"] == ExitReason.END_OF_DATA.value).mean()
                        ),
                        "mfe_r_pre_exit_median": float(sub["mfe_r_pre_exit"].fillna(0.0).median()),
                        "cost_loss_r_median": float(
                            sub["stop_width"].map(lambda w: _loss_cost(float(w), rates)).median()
                        ),
                    }
                    for target in R_TARGETS:
                        key = _r_key(target)
                        record[f"reach_{key}"] = float(_reached(sub, target).mean())
                        record[f"breakeven_{key}"] = _breakeven_at(sub, target, rates)
                        record[f"ev_{key}"] = _ev_at(sub, target, rates)
                    records.append(record)
    return pd.DataFrame.from_records(records)


def _pct_rows_arm(frame: pd.DataFrame, rates: CostRates) -> pd.DataFrame:
    """퍼센트 분포 — 구간 × 스코프 × TF(전체 포함), 가드 통과분만(이슈 §1-1~4)."""
    records: list[dict[str, object]] = []
    guarded = frame[frame["stop_width"] >= _guard_fraction()]
    timeframes = [ALL_TF, *sorted(guarded["timeframe"].unique(), key=_tf_order)]
    for segment in SEGMENTS:
        seg = _segment(guarded, segment)
        for scope in SCOPES:
            scoped = _scope(seg, scope)
            for tf in timeframes:
                sub = scoped if tf == ALL_TF else scoped[scoped["timeframe"] == tf]
                if sub.empty:
                    continue
                pct = sub["mfe_pct_pre_exit"].fillna(0.0)
                bins = (pct.clip(lower=0.0) // PCT_BIN).astype(int)
                mode_bin = int(bins.value_counts().idxmax())
                record: dict[str, object] = {
                    "segment": segment,
                    "scope": scope,
                    "timeframe": tf,
                    "n": len(sub),
                    "stop_width_median": float(sub["stop_width"].median()),
                    "mfe_pct_mean": float(pct.mean()),
                    "mfe_pct_p25": float(pct.quantile(0.25)),
                    "mfe_pct_median": float(pct.median()),
                    "mfe_pct_p75": float(pct.quantile(0.75)),
                    "mfe_pct_p90": float(pct.quantile(0.90)),
                    "mode_bin_low": mode_bin * PCT_BIN,
                    "mode_bin_share": float((bins == mode_bin).mean()),
                    "open_share": float(
                        (sub["exit_reason"] == ExitReason.END_OF_DATA.value).mean()
                    ),
                }
                for target in PCT_TARGETS:
                    key = _pct_key(target)
                    reached = pct >= target
                    record[f"reach_{key}"] = float(reached.mean())
                    record[f"ev_{key}"] = _ev_pct_at(sub, target, rates)
                records.append(record)
    return pd.DataFrame.from_records(records)


def _ev_pct_at(frame: pd.DataFrame, target_pct: float, rates: CostRates) -> float | None:
    """고정 퍼센트 익절(팔 `C` 미리보기)의 셋업 층 기대 net R — 거래마다 목표 R이 다르다."""
    total = 0.0
    count = 0
    for width, mfe_pre, reason in zip(
        frame["stop_width"].to_numpy(),
        frame["mfe_r_pre_exit"].to_numpy(),
        frame["exit_reason"].to_numpy(),
        strict=True,
    ):
        target_r = target_pct / float(width)
        hit = mfe_pre is not None and not math.isnan(mfe_pre) and mfe_pre >= target_r
        if not hit and reason == ExitReason.END_OF_DATA.value:
            continue
        c_win, c_loss = cost_r(float(width), target_r, rates)
        total += (target_r - c_win) if hit else (-1.0 - c_loss)
        count += 1
    return total / count if count else None


def worst_days(path: Path = WAN408_DAILY_CSV, *, count: int = WORST_DAYS) -> tuple[str, ...]:
    """WAN-408이 채택 북 `oos_warm`에서 낸 최악 일자(KST) — 두 벌로 적지 않고 CSV에서 읽는다."""
    if not path.exists():
        return ()
    daily = pd.read_csv(path)
    warm = daily[daily["segment"] == SEGMENT_OOS_WARM].sort_values("sum_net_r")
    return tuple(str(day) for day in warm["day"].head(count))


def _kst_day(ms: int) -> str:
    return datetime.fromtimestamp((ms + KST_OFFSET_MS) / 1000, tz=UTC).date().isoformat()


def _band_rows_arm(frame: pd.DataFrame, bad_days: Sequence[str], rates: CostRates) -> pd.DataFrame:
    """밴드 무너짐 — 두 판을 **따로** 낸다(R 분포가 달라 한 표에 섞지 않는다).

    * `tp_on`(익절 켠 판 복원): 🚨 **익절 거래 중 무너졌던 비율**(규칙이 죽이는 승자) ·
      손절 거래 중 먼저 무너진 비율 · 그 순간 R · 무너짐→손절 봉 수 · 최악 일자 분해.
    * `no_tp`(익절 끈 판): 무너짐 비율 · 그 순간 R · 그때까지의 MFE · 무너짐→청산 봉 수.
    """
    guarded = frame[frame["stop_width"] >= _guard_fraction()]
    records: list[dict[str, object]] = []
    timeframes = [ALL_TF, *sorted(guarded["timeframe"].unique(), key=_tf_order)]
    bad = set(bad_days)
    for segment in SEGMENTS:
        seg = _segment(guarded, segment)
        for scope in SCOPES:
            scoped = _scope(seg, scope)
            for tf in timeframes:
                sub = scoped if tf == ALL_TF else scoped[scoped["timeframe"] == tf]
                if sub.empty:
                    continue
                tf_ms = None if tf == ALL_TF else timeframe_to_ms(tf)
                broke = sub["band_break_time"].notna()
                tp = sub[sub["tp_on_reason"] == ExitReason.TAKE_PROFIT.value]
                tp_broke = tp["band_break_time"].notna() & (
                    tp["band_break_time"] < tp["tp_on_exit_time"]
                )
                stop = sub[sub["tp_on_reason"] == ExitReason.STOP_LOSS.value]
                stop_broke = stop[stop["band_break_time"].notna()]
                gap_ms = stop_broke["tp_on_exit_time"] - stop_broke["band_break_time"]
                minutes_to_stop = gap_ms / 60_000
                record: dict[str, object] = {
                    "segment": segment,
                    "scope": scope,
                    "timeframe": tf,
                    "n": len(sub),
                    "tp_on_tp": len(tp),
                    "tp_on_tp_broke_share": float(tp_broke.mean()) if len(tp) else None,
                    "tp_on_stop": len(stop),
                    "tp_on_stop_broke_share": (len(stop_broke) / len(stop)) if len(stop) else None,
                    "tp_on_stop_break_r_p25": _quantile(stop_broke["band_break_r"], 0.25),
                    "tp_on_stop_break_r_median": _quantile(stop_broke["band_break_r"], 0.5),
                    "tp_on_stop_break_r_p75": _quantile(stop_broke["band_break_r"], 0.75),
                    "tp_on_stop_minutes_to_exit_median": _quantile(minutes_to_stop, 0.5),
                    "tp_on_stop_bars_to_exit_median": (
                        None
                        if tf_ms is None or minutes_to_stop.empty
                        else float(minutes_to_stop.median() * 60_000 / tf_ms)
                    ),
                    "no_tp_broke_share": float(broke.mean()),
                    "no_tp_break_r_median": _quantile(sub.loc[broke, "band_break_r"], 0.5),
                    "no_tp_break_mfe_r_median": _quantile(sub.loc[broke, "band_break_mfe_r"], 0.5),
                    **band_exit_effects(sub, rates),
                }
                if bad:
                    stop_days = stop["tp_on_exit_time"].map(lambda ms: _kst_day(int(ms)))
                    in_bad = stop[stop_days.isin(bad)]
                    record["bad_days_stop"] = len(in_bad)
                    record["bad_days_stop_broke_share"] = (
                        float(in_bad["band_break_time"].notna().mean()) if len(in_bad) else None
                    )
                records.append(record)
    return pd.DataFrame.from_records(records)


def band_exit_effects(frame: pd.DataFrame, rates: CostRates) -> dict[str, float | int | None]:
    """셋업 층 반사실 — 밴드가 무너진 그 1분 **종가에 시장가로** 나왔다면 R이 얼마 달라지나.

    익절 켠 판 복원 위에서 두 갈래로 **따로** 잰다(한 숫자로 섞으면 어느 쪽이 크게 움직였는지
    안 보인다):

    * **손절로 끝날 거래**가 손절 전에 무너졌으면 −1R 대신 그 순간 R — **덜 잃은 R**.
    * **익절로 끝날 거래**가 익절 전에 무너졌으면 +1.5R 대신 그 순간 R — **포기한 R**.

    📌 **비용 포함(`net`)도 낸다** — 진입 비용은 두 경우가 같아 상쇄되고 **청산 방식만** 다르다.
    밴드 청산은 시장가(테이커 수수료＋슬리피지)라 원래 손절과는 거의 같지만, 원래 익절은
    **지정가(메이커 · 슬리피지 0, WAN-370)**라 끊을 때마다 그 차이를 더 문다. 요율은 채택 회계
    `CostRates`에서 읽는다. 펀딩(보유 시간 차이)은 넣지 않는다.

    열린 거래(데이터 끝)는 뺀다. ⚠️ 대조군(같은 평균 거리 고정 손절)이 없어 「밴드가 정보를
    줬다」와 「손절을 조였을 뿐」을 **가르지 못한다** · 끊은 뒤 같은 존 재진입은 모델링하지 않는다.
    """
    target = ConfluenceParams().take_profit_r
    closed = frame[frame["tp_on_reason"] != ExitReason.END_OF_DATA.value]
    market_exit_rate = rates.stop_fee + rates.stop_slippage
    stop_saved_gross: list[float] = []
    stop_saved_net: list[float] = []
    tp_lost_gross: list[float] = []
    tp_lost_net: list[float] = []
    for reason, exit_time, break_time, break_r, width in zip(
        closed["tp_on_reason"].to_numpy(),
        closed["tp_on_exit_time"].to_numpy(),
        closed["band_break_time"].to_numpy(),
        closed["band_break_r"].to_numpy(),
        closed["stop_width"].to_numpy(),
        strict=True,
    ):
        cut = (
            break_time is not None
            and not math.isnan(break_time)
            and break_time < exit_time
            and break_r is not None
            and not math.isnan(break_r)
        )
        if not cut:
            continue
        w = float(width)
        r_at_break = float(break_r)
        band_exit_cost = market_exit_rate * (1.0 + r_at_break * w) / w
        if reason == ExitReason.TAKE_PROFIT.value:
            original_cost = rates.take_profit_fee * (1.0 + target * w) / w
            gross = target - r_at_break
            tp_lost_gross.append(gross)
            tp_lost_net.append(gross + band_exit_cost - original_cost)
        else:
            original_cost = market_exit_rate * (1.0 - w) / w
            gross = r_at_break - (-1.0)
            stop_saved_gross.append(gross)
            stop_saved_net.append(gross - (band_exit_cost - original_cost))
    n = len(closed)

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "closed": n,
        "stop_cut": len(stop_saved_gross),
        "stop_saved_gross_mean": mean(stop_saved_gross),
        "stop_saved_net_mean": mean(stop_saved_net),
        "tp_cut": len(tp_lost_gross),
        "tp_lost_gross_mean": mean(tp_lost_gross),
        "tp_lost_net_mean": mean(tp_lost_net),
        "delta_gross_r": (sum(stop_saved_gross) - sum(tp_lost_gross)) / n if n else None,
        "delta_net_r": (sum(stop_saved_net) - sum(tp_lost_net)) / n if n else None,
    }


def _quantile(series: pd.Series, q: float) -> float | None:
    clean = series.dropna()
    return None if clean.empty else float(clean.quantile(q))


# --------------------------------------------------------------------------- #
# §2 갈림 — 착수 전에 못 박은 규칙 (결과를 보고 옮기지 않는다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GapTest:
    segment: str
    scope: str
    target_r: float
    narrow_n: int
    narrow_reach: float
    wide_n: int
    wide_reach: float
    arm: str = ARM_BAND

    @property
    def diff(self) -> float:
        return self.narrow_reach - self.wide_reach

    @property
    def sigma(self) -> float:
        """두 비율 차의 합성 이항 표준오차."""
        var = self.narrow_reach * (1 - self.narrow_reach) / max(self.narrow_n, 1) + (
            self.wide_reach * (1 - self.wide_reach) / max(self.wide_n, 1)
        )
        return math.sqrt(var)

    @property
    def sign(self) -> int:
        """+1 = 좁은 쪽이 2σ 넘게 멀리, −1 = 2σ 넘게 짧게, 0 = 구분 안 됨."""
        if self.narrow_n < VERDICT_MIN_N or self.wide_n < VERDICT_MIN_N:
            return 0
        if self.diff > VERDICT_SIGMA * self.sigma:
            return 1
        if self.diff < -VERDICT_SIGMA * self.sigma:
            return -1
        return 0


def gap_tests(frame: pd.DataFrame, *, timeframe: str = ALL_TF) -> list[GapTest]:
    """진입가 팔마다 `_gap_tests_arm`을 낸다."""
    tests: list[GapTest] = []
    for arm in ARMS:
        sub = frame[frame["arm"] == arm]
        if not sub.empty:
            tests.extend(_gap_tests_arm(sub, arm=arm, timeframe=timeframe))
    return tests


def _gap_tests_arm(frame: pd.DataFrame, *, arm: str, timeframe: str) -> list[GapTest]:
    """좁은 무리 대 넓은 무리의 청산 전 도달률 비교 — 구간 × 스코프 × 목표."""
    tests: list[GapTest] = []
    base = frame if timeframe == ALL_TF else frame[frame["timeframe"] == timeframe]
    for segment in (SEGMENT_IS, SEGMENT_OOS_WARM):
        seg = _segment(base, segment)
        for scope in SCOPES:
            scoped = _scope(seg, scope)
            narrow = scoped[
                (scoped["stop_width"] >= NARROW_RANGE[0]) & (scoped["stop_width"] < NARROW_RANGE[1])
            ]
            wide = scoped[scoped["stop_width"] >= WIDE_MIN]
            for target in VERDICT_TARGETS:
                tests.append(
                    GapTest(
                        segment=segment,
                        scope=scope,
                        target_r=target,
                        narrow_n=len(narrow),
                        narrow_reach=float(_reached(narrow, target).mean()) if len(narrow) else 0.0,
                        wide_n=len(wide),
                        wide_reach=float(_reached(wide, target).mean()) if len(wide) else 0.0,
                        arm=arm,
                    )
                )
    return tests


def verdict_for_scope(tests: Sequence[GapTest], scope: str, arm: str = ARM_BAND) -> str:
    """한 스코프의 판정 — 두 구간 × 세 목표가 **전부** 같은 부호일 때만 방향을 낸다."""
    signs = [t.sign for t in tests if t.scope == scope and t.arm == arm]
    if signs and all(s == 1 for s in signs):
        return VERDICT_GO
    if signs and all(s == -1 for s in signs):
        return VERDICT_SHORTER
    return VERDICT_SIMILAR


def verdict_for(tests: Sequence[GapTest], arm: str = ARM_BAND) -> str:
    """§2 판정 — 세 스코프가 **모두** 같은 답일 때만 그 답, 하나라도 갈리면 「혼합」.

    이슈 §2의 판정은 **채택 팔(`band`)** 에서 낸다(착수 전에 못 박은 대상). `zone_top` 팔은 같은
    규칙으로 **참고** 판정을 낸다."""
    answers = {verdict_for_scope(tests, scope, arm) for scope in SCOPES}
    return answers.pop() if len(answers) == 1 else VERDICT_MIXED


def tap_outcome_rows(taps: pd.DataFrame, setups: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for arm in ARMS:
        arm_taps = taps[taps["arm"] == arm]
        if arm_taps.empty:
            continue
        built = _tap_outcome_rows_arm(arm_taps, setups[setups["arm"] == arm])
        built.insert(0, "arm", arm)
        parts.append(built)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _tap_outcome_rows_arm(taps: pd.DataFrame, setups: pd.DataFrame) -> pd.DataFrame:
    """첫 탭·재탭의 결과 분포 + 존 단위 요약 — 구간 × TF(전체 포함).

    `first_fill_on_retap_share` = 체결된 존 중 **첫 체결이 재탭에서** 난 비율(= 「첫 탭만」이
    놓치고 「존별 첫 체결」이 잡는 몫).
    """
    records: list[dict[str, object]] = []
    timeframes = [ALL_TF, *sorted(taps["timeframe"].unique(), key=_tf_order)]
    for segment in SEGMENTS:
        seg_taps = _segment(taps, segment)
        seg_setups = _segment(setups, segment)
        for tf in timeframes:
            t = seg_taps if tf == ALL_TF else seg_taps[seg_taps["timeframe"] == tf]
            st = seg_setups if tf == ALL_TF else seg_setups[seg_setups["timeframe"] == tf]
            if t.empty:
                continue
            first_fills = st[st["is_first_fill"].astype(bool)]
            zones = t.groupby(["symbol", "timeframe", "zone"])
            for tap_class, sub in (
                (TAP_FIRST, t[t["tap_index"] == 0]),
                (TAP_RETAP, t[t["tap_index"] > 0]),
            ):
                record: dict[str, object] = {
                    "segment": segment,
                    "timeframe": tf,
                    "tap_class": tap_class,
                    "taps": len(sub),
                    "zones": zones.ngroups,
                    "first_fill_on_retap_share": (
                        float((first_fills["tap_index"] > 0).mean()) if len(first_fills) else None
                    ),
                }
                for outcome in TAP_OUTCOMES:
                    record[f"share_{outcome}"] = (
                        float((sub["outcome"] == outcome).mean()) if len(sub) else None
                    )
                records.append(record)
    return pd.DataFrame.from_records(records)


# --------------------------------------------------------------------------- #
# 보조
# --------------------------------------------------------------------------- #


def _guard_fraction() -> float:
    risk_sizing = harness.build_config("1h").risk_sizing
    return risk_sizing.min_stop_distance_fraction if risk_sizing is not None else 0.0


def _bucket_order() -> list[str]:
    """버킷 라벨을 경계 순서대로 — 라벨 규칙은 WAN-370 `stop_width_bucket` 한 곳이다."""
    return [stop_width_bucket(low) for low in STOP_WIDTH_EDGES[:-1]]


def _scope_label(scope: str) -> str:
    return SCOPE_LABELS[scope]


def _tf_order(tf: str) -> int:
    return timeframe_to_ms(tf)


def _r_key(target: float) -> str:
    return f"{target:g}r".replace(".", "_")


def _pct_key(target: float) -> str:
    return f"{target * 100:g}pct".replace(".", "_")


def checksum_frame(results: Sequence[CellResult]) -> pd.DataFrame:
    return pd.DataFrame.from_records(
        [
            {
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "arm": c.arm,
                "setups": c.setups,
                "adopted_candidates": c.adopted_candidates,
                "entry_mismatches": c.entry_mismatches,
                "exit_mismatches": c.exit_mismatches,
                "cell_seconds": round(r.seconds, 1),
            }
            for r in results
            for c in r.checks
        ]
    )


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _as_float(value: object) -> float | None:
    """표 셀 값 → float. 비었거나 NaN이면 None(지어내지 않는다)."""
    if value is None or isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    number = float(value)
    return None if math.isnan(number) else number


def _pct(value: object, digits: int = 1) -> str:
    number = _as_float(value)
    return "—" if number is None else f"{number * 100:.{digits}f}%"


def _num(value: object, digits: int = 4) -> str:
    number = _as_float(value)
    return "—" if number is None else f"{number:+.{digits}f}"


def render_summary(
    reach: pd.DataFrame,
    pct: pd.DataFrame,
    band: pd.DataFrame,
    tests: Sequence[GapTest],
    checks: pd.DataFrame | None,
    bad_days: Sequence[str],
    tap_outcome: pd.DataFrame | None = None,
) -> str:
    """요약 md — 판정 문장은 코드가 낸다(사람이 표를 보고 정하지 않는다)."""
    final = verdict_for(tests, ARM_BAND)
    lines = [
        "# WAN-375 Phase 1 — 조건부 손익비 관측 (손절폭 × MFE · 퍼센트 · 밴드 무너짐)",
        "",
        "> 셋업 층 관측이다(시퀀싱·공유 자본·재진입 없음 — WAN-90 규약). **채택 근거가 아니다**"
        "(판단은 북에서, WAN-341). 전부 `baseline`(닿으면 체결) 위 값이다.",
        "",
        "> 진입가 팔 둘: "
        + " · ".join(f"`{arm}` = {ARM_LABELS[arm]}" for arm in ARMS)
        + ". 스코프 셋: "
        + " · ".join(f"`{scope}` = {SCOPE_LABELS[scope]}" for scope in SCOPES)
        + ".",
        "",
        "## §2 판정 (채택 팔 `band` · 착수 전에 못 박은 대상)",
        "",
        f"**{final}** — 좁은 쪽(손절폭 {NARROW_RANGE[0] * 100:.2f}~"
        f"{NARROW_RANGE[1] * 100:.2f}%) 대 넓은 쪽(≥{WIDE_MIN * 100:.2f}%)의 청산 전 도달률, "
        f"{'/'.join(f'{t:g}R' for t in VERDICT_TARGETS)} × `is`·`oos_warm` × 세 스코프 "
        f"(합성 이항 {VERDICT_SIGMA:g}σ · 무리당 {VERDICT_MIN_N}셋업 미만이면 부호 없음).",
        "",
    ]
    for arm in ARMS:
        prefix = "" if arm == ARM_BAND else "참고 · "
        lines.append(
            f"* {prefix}`{arm}`: **{verdict_for(tests, arm)}** ("
            + " · ".join(
                f"{_scope_label(scope)} {verdict_for_scope(tests, scope, arm)}" for scope in SCOPES
            )
            + ")"
        )
    if final != VERDICT_GO:
        lines.append(
            "* 📌 채택 팔이 「간다」가 아니므로 "
            "**이슈 §2대로 Phase 2(손익 격자)를 착수하지 않는다.**"
        )
    else:
        lines.append(
            "* 📌 「간다」 — Phase 2 착수 조건이 충족됐다"
            "(별도 PR · 팔 `A`/`B`/`C` · 버킷별 자유 선택 금지)."
        )
    lines += [
        "",
        "| 팔 | 구간 | 스코프 | 목표 | 좁은 n | 좁은 도달 | 넓은 n | 넓은 도달 | 차 | 2σ | 부호 |",
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for t in tests:
        lines.append(
            f"| {t.arm} | {t.segment} | {t.scope} | {t.target_r:g}R | {t.narrow_n:,} | "
            f"{_pct(t.narrow_reach)} | {t.wide_n:,} | {_pct(t.wide_reach)} | "
            f"{t.diff * 100:+.1f}%p | {VERDICT_SIGMA * t.sigma * 100:.1f}%p | {t.sign:+d} |"
        )

    if tap_outcome is not None and not tap_outcome.empty:
        lines += ["", "## 첫 탭에서 진입이 안 된 이유 (탭 단위)", ""]
        lines.append(
            "> 「첫 탭만」은 첫 탭에서 체결된 셋업만 센다 — 첫 탭이 막히거나 미체결이면 그 존은 "
            "빠지고, 재탭에서 처음 체결됐다면 「존별 첫 체결」에만 들어간다(마지막 열이 그 몫)."
        )
        header = " | ".join(TAP_OUTCOME_LABELS[o] for o in TAP_OUTCOMES)
        for arm in ARMS:
            for segment in (SEGMENT_OOS_WARM, SEGMENT_IS):
                sub = tap_outcome[(tap_outcome["arm"] == arm) & (tap_outcome["segment"] == segment)]
                if sub.empty:
                    continue
                lines += [
                    "",
                    f"### `{arm}` · `{segment}`",
                    "",
                    f"| TF | 탭 | 탭 수 | {header} | 첫 체결이 재탭인 존 |",
                    "| -- | -- | --: | " + " | ".join("--:" for _ in TAP_OUTCOMES) + " | --: |",
                ]
                for _, row in sub.iterrows():
                    shares = " | ".join(_pct(row[f"share_{o}"]) for o in TAP_OUTCOMES)
                    label = "첫 탭" if row["tap_class"] == TAP_FIRST else "재탭"
                    lines.append(
                        f"| {row['timeframe']} | {label} | {int(row['taps']):,} | {shares} | "
                        f"{_pct(row['first_fill_on_retap_share'])} |"
                    )

    lines += ["", "## 퍼센트 — TF별 · 세 스코프 (가드 통과분)", ""]
    lines.append(
        "> 🚨 **최빈 구간은 익절을 둘 자리가 아니다**(MFE는 지나간 최고점 — WAN-90). "
        "도달률 곡선은 「어느 범위를 재볼지」의 지도다. EV = 셋업 층 기대 net R."
    )
    for arm in ARMS:
        for segment in (SEGMENT_OOS_WARM, SEGMENT_IS):
            sub = pct[(pct["arm"] == arm) & (pct["segment"] == segment)]
            if sub.empty:
                continue
            lines += [
                "",
                f"### `{arm}` · `{segment}`",
                "",
                "| TF | 스코프 | n | 손절폭 중앙 | MFE% 평균 | p25 | 중앙 | p75 | p90 | 최빈 칸 | "
                "1% 도달/EV | 1.5% | 2% | 3% |",
                "| -- | -- | --: | --: | --: | --: | --: | --: | --: | -- | -- | -- | -- | -- |",
            ]
            for _, row in sub.iterrows():
                cells = []
                for target in (0.01, 0.015, 0.02, 0.03):
                    key = _pct_key(target)
                    cells.append(f"{_pct(row[f'reach_{key}'])} / {_num(row[f'ev_{key}'], 3)}")
                low = float(row["mode_bin_low"])
                lines.append(
                    f"| {row['timeframe']} | {_scope_label(str(row['scope']))} | "
                    f"{int(row['n']):,} | {_pct(row['stop_width_median'], 2)} | "
                    f"{_pct(row['mfe_pct_mean'], 2)} | {_pct(row['mfe_pct_p25'], 2)} | "
                    f"{_pct(row['mfe_pct_median'], 2)} | {_pct(row['mfe_pct_p75'], 2)} | "
                    f"{_pct(row['mfe_pct_p90'], 2)} | "
                    f"{low * 100:.1f}~{(low + PCT_BIN) * 100:.1f}% "
                    f"({_pct(row['mode_bin_share'])}) | " + " | ".join(cells) + " |"
                )

    lines += ["", "## 표 A·B — 손절폭 버킷 × 청산 전 도달률 · 손익분기 · 셋업 층 EV (4TF 합산)", ""]
    lines.append(
        "> 도달률 = 그 목표에 익절을 걸었다면 손절보다 먼저 익절됐을 비율(셋업 층 승률). "
        "본전 = 비용을 넣은 손익분기 승률. EV = 셋업 층 기대 net R(펀딩·북·열린 거래 제외). "
        "`<0.30%`는 **손절폭 가드가 사이징에서 버리는 부류**라 참고 행이다."
    )
    for arm in ARMS:
        for segment in (SEGMENT_OOS_WARM, SEGMENT_IS):
            for scope in SCOPES:
                sub = reach[
                    (reach["arm"] == arm)
                    & (reach["segment"] == segment)
                    & (reach["scope"] == scope)
                    & (reach["timeframe"] == ALL_TF)
                ]
                if sub.empty:
                    continue
                lines += [
                    "",
                    f"### `{arm}` · `{segment}` · {_scope_label(scope)}",
                    "",
                    "| 손절폭 | n | 열린% | MFE중앙(R) | 비용R(손절) | "
                    "1.5R 도달/본전/EV | 2R | 2.5R | 3R |",
                    "| -- | --: | --: | --: | --: | -- | -- | -- | -- |",
                ]
                for _, row in sub.iterrows():
                    cells = []
                    for target in (1.5, 2.0, 2.5, 3.0):
                        key = _r_key(target)
                        cells.append(
                            f"{_pct(row[f'reach_{key}'])} / {_pct(row[f'breakeven_{key}'])} / "
                            f"{_num(row[f'ev_{key}'], 3)}"
                        )
                    label = f"{row['width_bucket']}{' (가드 절단)' if row['guard_cut'] else ''}"
                    lines.append(
                        f"| {label} | {int(row['n']):,} | {_pct(row['open_share'])} | "
                        f"{float(row['mfe_r_pre_exit_median']):.2f} | "
                        f"{float(row['cost_loss_r_median']):.3f} | " + " | ".join(cells) + " |"
                    )

    lines += ["", "## 밴드 무너짐 (진입 규칙 3의 보유 중 확장 · 가드 통과분)", ""]
    lines.append(
        "> 두 판을 **섞지 않는다**. `익절 켠 판`은 1.5R 익절을 켰다면의 복원(검산 (b)가 엔진과 "
        "대조), `익절 끈 판`은 거래가 손절까지 사는 경로다. 🚨 **「익절 거래 중 무너졌던」 열이 "
        "핵심 판정이다** — 이 규칙이 죽이는 승자다."
    )
    for arm in ARMS:
        for segment in (SEGMENT_OOS_WARM, SEGMENT_IS):
            sub = band[(band["arm"] == arm) & (band["segment"] == segment)]
            if sub.empty:
                continue
            lines += [
                "",
                f"### `{arm}` · `{segment}`",
                "",
                "| TF | 스코프 | 익절 거래 | 🚨 그중 무너졌던 | 손절 거래 | 그중 먼저 무너진 | "
                "무너진 순간 R p25/중앙/p75 | 무너짐→손절(봉) | 익절 끈 판 무너짐 | "
                "그때 MFE(R) |",
                "| -- | -- | --: | --: | --: | --: | -- | --: | --: | --: |",
            ]
            for _, row in sub.iterrows():
                bars = _as_float(row["tp_on_stop_bars_to_exit_median"])
                lines.append(
                    f"| {row['timeframe']} | {_scope_label(str(row['scope']))} | "
                    f"{int(row['tp_on_tp']):,} | {_pct(row['tp_on_tp_broke_share'])} | "
                    f"{int(row['tp_on_stop']):,} | {_pct(row['tp_on_stop_broke_share'])} | "
                    f"{_num(row['tp_on_stop_break_r_p25'], 2)} / "
                    f"{_num(row['tp_on_stop_break_r_median'], 2)} / "
                    f"{_num(row['tp_on_stop_break_r_p75'], 2)} | "
                    f"{'—' if bars is None else f'{bars:.1f}'} | "
                    f"{_pct(row['no_tp_broke_share'])} | "
                    f"{_num(row['no_tp_break_mfe_r_median'], 2)} |"
                )
            lines += [
                "",
                f"**`{arm}` · `{segment}` — 무너진 그 1분 종가에 시장가로 나왔다면** "
                "(gross / 비용 포함 net · R)",
                "",
                "| TF | 스코프 | 닫힌 거래 | 손절 끊김 | 손절당 덜 잃은 R | 익절 끊김 | "
                "익절당 포기한 R | 전체 거래당 순효과 |",
                "| -- | -- | --: | --: | -- | --: | -- | -- |",
            ]
            for _, row in sub.iterrows():
                lines.append(
                    f"| {row['timeframe']} | {_scope_label(str(row['scope']))} | "
                    f"{int(row['closed']):,} | {int(row['stop_cut']):,} | "
                    f"{_num(row['stop_saved_gross_mean'], 3)} / "
                    f"{_num(row['stop_saved_net_mean'], 3)} | "
                    f"{int(row['tp_cut']):,} | "
                    f"{_num(row['tp_lost_gross_mean'], 3)} / {_num(row['tp_lost_net_mean'], 3)} | "
                    f"{_num(row['delta_gross_r'], 3)} / {_num(row['delta_net_r'], 3)} |"
                )
            if bad_days and "bad_days_stop" in sub.columns:
                warm_all = sub[(sub["scope"] == SCOPE_ALL) & (sub["timeframe"] == ALL_TF)]
                if segment == SEGMENT_OOS_WARM and not warm_all.empty:
                    row = warm_all.iloc[0]
                    lines += [
                        "",
                        f"**폭락일 분해** (WAN-408 `oos_warm` 최악 {len(bad_days)}일 · KST · 전체 "
                        f"탭): 그 날 손절로 끝난 셋업 {int(row['bad_days_stop']):,}건 중 밴드가 "
                        f"**먼저** 무너진 비율 {_pct(row['bad_days_stop_broke_share'])} (전 구간 "
                        f"손절 셋업은 {_pct(row['tp_on_stop_broke_share'])}).",
                    ]

    lines += ["", "## 검산", ""]
    if checks is not None and not checks.empty:
        for arm in ARMS:
            arm_checks = checks[checks["arm"] == arm]
            checked = arm_checks.dropna(subset=["entry_mismatches"])
            lines.append(
                f"* **(b) `{arm}` 익절 켠 판 복원 ≡ 엔진 후보** — 대조한 칸 "
                f"{len(checked)}/{len(arm_checks)} · 진입 집합 불일치 합 "
                f"**{int(checked['entry_mismatches'].sum())}** · 청산(사유·시각) 불일치 합 "
                f"**{int(checked['exit_mismatches'].sum())}**."
            )
        cell_seconds = checks.drop_duplicates(subset=["symbol", "timeframe"])["cell_seconds"]
        lines.append(f"* 실측 비용: 칸 합계 {cell_seconds.sum() / 3600:.2f}시간(워커 시간).")
    else:
        lines.append("* (b) 검산 표가 없다(`--from-csv`에 `wan375_checksum.csv` 없음).")
    lines += [
        "* (a) 관측이 순수하다 · (c) 걸러내기 구간이 판정을 안 바꾼다 · (d) 「첫 탭만」 ≡ "
        '`retap_mode="once"` — `tests/test_wan375_hold_path.py`가 동작으로 고정한다.',
        "",
        "## 범위 밖 · 경고",
        "",
        "* 측정 전용 · 엔진 동작·기본값·토대 불변(`ConfluenceParams()`·`LeverageBookParams()`) · "
        "손절폭 가드(0.3%)·존폭 필터(꺼짐)·익절 배수(1.5R) 안 건드렸다 · 실거래 보류 유지.",
        "* EV 열은 **셋업 층**이다(북·재진입·펀딩 없음) — 판단은 북에서(WAN-341).",
        "* `zone_top` 팔은 **전 탭에** 같은 규칙을 건다 — "
        "「첫 탭만 존 상단」은 그 팔의 「첫 탭만」 줄이다.",
        "* 밴드 청산 반사실은 비용을 넣었지만(청산 방식 차이 · 채택 요율) **펀딩·대조군은 없다** — "
        "「밴드가 정보를 줬다」와 「손절을 조였다」를 가르지 못한다(같은 평균 거리 고정 손절 팔이 "
        "필요 — Phase 2 소관) · 끊은 뒤 같은 존 재진입은 모델링하지 않는다.",
        "* 「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변 — 다른 질문이다.",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else "")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument("--no-checksum", action="store_true", help="채택 엔진 복원 대조를 끈다")
    parser.add_argument("--from-csv", action="store_true", help="저장된 셋업 표로 요약만 다시 낸다")
    parser.add_argument("--pilot", action="store_true", help="BTC 4h 한 칸만(쓰기 없음)")
    return parser.parse_args(argv)


def _write_outputs(
    frame: pd.DataFrame, checks: pd.DataFrame | None, taps: pd.DataFrame | None
) -> None:
    rates = CostRates.from_config(harness.build_config("1h"))
    bad = worst_days()
    reach = reach_rows(frame, rates)
    pct = pct_rows(frame, rates)
    band = band_rows(frame, bad, rates)
    tests = gap_tests(frame[frame["stop_width"] >= _guard_fraction()])
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    reach.to_csv(REACH_CSV, index=False)
    pct.to_csv(PCT_CSV, index=False)
    band.to_csv(BAND_CSV, index=False)
    tap_outcome = None if taps is None or taps.empty else tap_outcome_rows(taps, frame)
    if tap_outcome is not None:
        tap_outcome.to_csv(TAP_OUTCOME_CSV, index=False)
    if checks is not None:
        checks.to_csv(CHECKSUM_CSV, index=False)
    SUMMARY_PATH.write_text(
        render_summary(reach, pct, band, tests, checks, bad, tap_outcome), encoding="utf-8"
    )
    print(
        f"[wan375] 요약: {SUMMARY_PATH} · 판정(band) {verdict_for(tests, ARM_BAND)} · "
        f"참고(zone_top) {verdict_for(tests, ARM_ZONE_TOP)}",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.from_csv:
        if not SETUPS_CSV.exists():
            print(f"[wan375] 셋업 표가 없습니다: {SETUPS_CSV}", file=sys.stderr)
            return 2
        frame = _with_derived(pd.read_csv(SETUPS_CSV))
        checks = pd.read_csv(CHECKSUM_CSV) if CHECKSUM_CSV.exists() else None
        taps = pd.read_csv(TAPS_CSV) if TAPS_CSV.exists() else None
        _write_outputs(frame, checks, taps)
        return 0
    if args.pilot:
        results = run_census(["BTCUSDT"], ["4h"], jobs=1, checksum=True)
        print(checksum_frame(results).to_string(index=False))
        return 0
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    symbols = [s for s in args.symbols.split(",") if s]
    timeframes = [t for t in args.timeframes.split(",") if t]
    started = time.monotonic()
    results = run_census(symbols, timeframes, jobs=jobs, checksum=not args.no_checksum)
    frame = setups_frame(results)
    SETUPS_CSV.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(SETUPS_CSV, index=False)
    taps = taps_frame(results)
    taps.to_csv(TAPS_CSV, index=False)
    checks = checksum_frame(results)
    bad_checks = checks.dropna(subset=["entry_mismatches"])
    _write_outputs(frame, checks, taps)
    print(f"[wan375] 전체 {time.monotonic() - started:.0f}s", flush=True)
    if int(bad_checks["entry_mismatches"].sum()) or int(bad_checks["exit_mismatches"].sum()):
        print("[wan375] 🚨 검산 (b) 불일치 — 표를 인용하지 마십시오.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
