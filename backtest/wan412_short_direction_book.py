"""WAN-412 — 숏을 더하면 BTC 하락일 손실이 상쇄되는가 (오늘 엔진 · 채택 북).

## 왜

PM 사이드 실측(공개 `book_trades.csv` × 로컬 DB의 진짜 BTC 1h 시세)이 낸 것: 채택 북
`oos_warm`의 거래당 net R이 **BTC 그날 등락 5분위에 완전 단조**다(가장 많이 내린 날
−0.36R ↔ 가장 많이 오른 날 +0.38R). 그리고 **거래 수가 정확히 반대로 실린다** — 가격이
존으로 **내려와야** 진입하는 롱 온리라 **시장이 빠질 때 자동으로 더 산다**. 전체가
−0.12R인 것은 「내리는 날에 두 배 넘게 매매하기 때문」이다.

⚠️ **그 표는 그 자체로 채택 근거가 아니다** — 「BTC가 오른 날」은 그날이 끝나야 안다
(WAN-408이 「폭락일을 지우면」에 건 함정). 방향을 맞히는 대신 **반대편 다리를 다는 것**이
이 이슈다.

## 🚨 숏은 「한 번도 안 쟀다」가 아니다 — 쟀고, 판정은 채택 근거 아님이었다

이슈 본문의 *「숏은 오늘 엔진에서 한 번도 안 쟀다」*는 **PM 정정으로 틀린 것으로 밝혀졌다**
(2026-09-08 코멘트). 채택 북에서 롱↔숏을 같이 굴린 측정이 2026-08에 여섯 건 있다 —
**WAN-282**(저항-존 숏 헤지 = 이 이슈와 같은 질문) · WAN-283/284/285(매칭 널 · 베타/알파
분리) · WAN-288(월별) · **WAN-301/302**(롱+숏 북 정밀 MDD). 공식 판정은 **「채택 근거
아님」**이고, 그중 **WAN-301 헤드라인이 이 이슈의 기대와 정반대**다:

> **숏은 청산을 0건으로 유지하면서 북 낙폭을 1.3~1.8배 키운다** — 롱-온리 MDD 12.16% →
> 롱+숏 21.50%(`oos_warm` · scope=all · cap_only 5배). 그 증폭은 체결을 조여도 유지되고
> TF가 짧을수록 크다.

📌 사용자가 2026-08-12에 숏 활성화를 **결정**(WAN-287)했다가, WAN-301/302가 그 낙폭 증폭을
낸 **다음날 취소**했다. **즉 「상쇄되어 낙폭이 준다」의 반증이 이미 실측돼 있다.**

## 그래도 이 이슈가 사는 이유 셋

1. 🚨 **엔진이 그 뒤 두 번 바뀌었다** — **WAN-365 소급 취소 수정(8/23)**과 **WAN-384 존폭
   필터 폐지(8/27)**가 전부 저 측정들 **이후**다. WAN-365는 채택 북 `oos_warm`의 거래당
   net R **부호를 뒤집었고**(+0.1985 → −0.1798) 낙폭을 100%로 보냈다. **롱 온리가 그렇게
   망가진 판에서 숏의 상대적 기여가 같을 이유가 없다.**
2. 🚨 **BTC 방향별로 가른 표는 그때 없었다** — WAN-288이 **월별**로는 봤지만 **일별 BTC
   등락 5분위 × 롱/숏 거래 수**는 아무도 안 냈다. **이게 이 이슈의 진짜 새 산출물**이고,
   *「BTC 내린 날 숏이 몇 건 하는가」*가 상쇄 가능성을 직접 판정한다.
3. 낙폭이 커진 이유를 그때는 안 갈랐다 — 「숏이 져서」인지 「거래가 늘어 노출이 커져서」인지.

## 팔 (채택 좌표 · 북 · 핀 없음)

| 팔 | 후보 | 성격 |
| -- | -- | -- |
| `long_only` | 롱만 | **채택 북 그대로** (검산 (a) 기준) |
| `short_only` | 숏만 | 반사실 |
| `both` | 롱+숏 한 지갑 | 이 이슈의 질문 |

🚨 `both`는 「롱 성적 + 숏 성적」이 **아니다** — 한 지갑을 나눠 쓰므로 숏이 슬롯·자본을
먹으면 롱이 못 들어간다(WAN-316/341). 그래서 **세 팔이 한 실행에서 같은 후보를 나눠
쓰고**(방향은 **배치 축**이다 — 후보 생성은 `short_enabled=True` **한 번**),
`long_only + short_only`를 더해 `both`를 짐작하지 않는다.

## 컴퓨트 — 방향이 배치 축인 이유

`short_enabled`는 후보 생성 인자이지만, **숏 게이트는 롱 후보를 건드리지 않는다**
(WAN-282가 고정한 성질 · 이 모듈의 검산 (a)가 그것을 실제로 확인한다). 그래서 롱+숏을
한 번 만들고 `scoped()`가 방향으로 판다 — **후보 생성 한 번에 팔 셋**이다.

## 판정 표

1. 🚨 **BTC 일간 등락 5분위 × 팔 × 방향** (`wan412_btc_regime.csv`) — **이 표가 답이다.**
   특히 *「BTC 내린 날 숏이 몇 건 하는가」*가 롱 거래 수에 비해 **작으면 상쇄가 구조적으로
   불가능**하다.
2. 거래당 net R ± 표준오차 · net R 합 · 승률 · 거래 수 (네 구간).
3. MDD · **지갑 정의 여부**(WAN-386/388 `wallet_defined` 규약).
4. leave-one-out **지갑 재배치**(WAN-316).
5. **BTC 등락과 일별 성적의 상관** — `long_only`의 그 값이 `both`에서 얼마나 내려가는가.
   📌 **0에 가까워지면 그게 「상쇄됐다」의 정의다.**
6. gross · 비용 분해 · 펀딩 별도 열.

## 검산

* **(a)** `long_only` 팔 ≡ **인자 없는 채택 북**(`book_cli.build_book_rows` 기본값) —
  같은 실행에서 짧은 두 번째 후보 생성(`short_enabled=False`)을 돌려 대조한다. 이것이
  *「숏 게이트가 롱 후보를 안 건드린다」*를 주장이 아니라 **숫자**로 만든다.
* **(b)** `short_only`에 롱 거래 **0건** · `both`에 양쪽이 실제로 있다(라벨이 아니라 동작).
* **(c)** 숏 후보의 **기하가 뒤집혀 있다** — 손절가 > 진입가 · 익절가 < 진입가.

재현::

    uv run python -m backtest.wan412_short_direction_book --pilot          # 한 칸 견적
    uv run python -m backtest.wan412_short_direction_book --jobs 4         # 48칸 격자
    uv run python -m backtest.wan412_short_direction_book --from-csv       # 요약만

⚠️ **측정 전용** — `ConfluenceParams()`(`short_enabled=False` 포함)·`OrderBlockParams()`·
`LeverageBookParams()` 기본값을 하나도 안 바꾼다 · **핀 없음**(WAN-305) · 판단은 북에서
(WAN-341). 🚨 **숏 재활성화는 이 이슈가 안 한다** — **재-베이스라인 = 사용자 결정** ·
개발자 임의 착수 금지. ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값 · `pen_5bp`
미측정 · 숏 축 체결 보수화도 안 쟀다 · 6년 MDD는 폭락 미포함 **바닥선**. ⚠️ **「엣지
없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *반대편 다리를 달면 방향
노출이 줄어드는가*를 묻는다(**다른 질문**). 🚨 **「이걸로 흑자」로 기대하지 말 것** —
롱 온리 gross가 **+0.0019R**(WAN-396)이라, 숏이 그 자체로 플러스가 아니면 상쇄는
**분산을 줄이지 기대값을 못 올린다**.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeVar

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.btc_day_regime import NUM_QUANTILES, DailyRegime, kst_day_of, load_daily_regime
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import BacktestConfig, PositionSide, Trade
from backtest.payload_cache import DEFAULT_CACHE_DIR, PayloadCache
from backtest.run import parse_date_ms
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE
from backtest.wan388_merge_x_retap import (
    NOISE_R,
    ChecksumRow,
    GridRow,
    _cfg,
    _row_kwargs,
    _short,
    wallet_defined,
)
from backtest.wan389_retap_attribution import NEW_THREE, entry_in_zone

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan412_short_direction_grid.csv"
REGIME_CSV_PATH = REPORTS_DIR / "wan412_btc_regime.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan412_leave_one_out.csv"
CELL_CSV_PATH = REPORTS_DIR / "wan412_by_cell.csv"
SCOPE_CSV_PATH = REPORTS_DIR / "wan412_by_timeframe.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan412_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan412_short_direction_summary.md"

#: leave-one-out 구간 — `full`(6년 낙폭이 사는 곳)과 `oos_warm`(주 수치).
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

#: 검산 (a)가 대조하는 열 — 「같은 북인가」를 정하는 값들.
_ADOPTED_METRICS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "mean_net_r",
    "gross_r",
    "cost_r",
    "total_return_flat",
    "max_drawdown",
    "peak_concurrency",
)


# --------------------------------------------------------------------------- #
# 팔
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    """방향 팔 하나 — 후보를 **방향으로** 판 뒤 같은 북에 배치한다."""

    name: str
    label: str
    sides: tuple[PositionSide, ...]

    @property
    def is_adopted(self) -> bool:
        """오늘 채택 북과 같은 팔인가 — 검산 (a)의 기준이다."""
        return self.sides == (PositionSide.LONG,)

    def keeps(self, side: PositionSide) -> bool:
        return side in self.sides


ARM_LONG_ONLY = Arm("long_only", "롱 온리(채택 북)", (PositionSide.LONG,))
ARM_SHORT_ONLY = Arm("short_only", "숏만", (PositionSide.SHORT,))
ARM_BOTH = Arm("both", "롱+숏 한 지갑", (PositionSide.LONG, PositionSide.SHORT))

ARMS: tuple[Arm, ...] = (ARM_LONG_ONLY, ARM_SHORT_ONLY, ARM_BOTH)
ARM_BY_NAME = {arm.name: arm for arm in ARMS}


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class ArmRow(GridRow):
    """한 (팔, 구간)의 북 집계 + **방향 분해**.

    겹치는 열은 이름·정의가 WAN-388/389/394와 **같다** — 그래야 한 자로 읽힌다.
    """

    model_config = ConfigDict(frozen=True)

    net_r_stderr: float
    """거래당 net R의 표준오차 — 🚨 **부호를 못 정하는 칸을 그대로 찍기 위한 열**이다."""
    long_trades: int
    short_trades: int
    long_win_rate: float | None
    short_win_rate: float | None
    long_mean_net_r: float | None
    short_mean_net_r: float | None
    long_net_r_sum: float
    short_net_r_sum: float
    btc_return_corr: float | None
    """**일별 평균 net R ↔ 그날 BTC 등락**의 상관 (완료기준 5).

    📌 `long_only`의 이 값이 `both`에서 0에 가까워지면 그게 **「상쇄됐다」의 정의**다.
    거래가 난 날이 3일 미만이면 정의되지 않는다(`None`)."""
    btc_days: int
    """상관을 잰 날 수 — 위 열의 표본이다."""


class LooRow(ArmRow):
    """종목 하나(또는 신규 3종목)를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


class RegimeRow(BaseModel):
    """§1 — (팔 × 구간 × BTC 등락 5분위 × 방향)의 거래 수·승률·거래당 net R.

    🚨 **이 표가 이 이슈의 답이다.** `direction="all"` 행이 그 칸의 합이고, `long`/`short`
    행이 그 안의 분해다.
    """

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    quantile: int
    days: int
    btc_ret_mean: float
    direction: str
    traded_days: int
    """이 팔이 그 버킷에서 **실제로 거래한 날 수** — 버킷의 `days`(공유 축)와 다르다."""
    trades: int
    win_rate: float | None
    mean_net_r: float | None
    net_r_sum: float


class CellAttributionRow(BaseModel):
    """§3 (A) **귀속** — 채택 북 **한 지갑**이 실제로 한 거래를 (종목 × TF × 방향)으로 쪼갠다.

    🚨 **스코프가 아니다** — 이 지갑은 48칸이 자본을 나눠 쓴 그 지갑이고, 여기서 한 칸을
    떼어 본다고 그 칸만 돌린 판이 되지 않는다(그건 `ScopeRow`가 낸다). 자본 경합이 **그대로
    살아 있다** — 그 칸이 자리를 못 잡아 못 들어간 거래도 반영된 값이다.
    """

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    symbol: str
    timeframe: str
    direction: str
    trades: int
    win_rate: float | None
    mean_net_r: float | None
    net_r_sum: float


class ScopeRow(ArmRow):
    """§3 (B) **스코프** — 그 TF의 칸만으로 **지갑을 다시 배치**한다 (WAN-301/316 패턴).

    🚨 **(A)를 TF로 합친 값과 같지 않다. 같으면 그게 버그다** — 여기서는 다른 TF가 자본을
    안 먹으므로 그 12칸이 더 많이 들어간다. 두 표는 **다른 질문**에 답한다:
    (A)는 *「우리가 실제로 돌리는 지갑에서 어느 칸이 벌고 잃었나」*, (B)는 *「그 TF만
    돌리면 어떤가」*.
    """

    scope: str


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계).

    `reentry=True`를 항상 켠 채로 만든다(WAN-305 기본값) — 재진입 후보를 payload에 실어
    두고 방향으로만 판다.
    """
    return {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    short_enabled: bool,
    start: str,
    end: str,
    jobs: int,
    cold_segments: bool = True,
    cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """후보를 만든다 — **팔이 아니라 `short_enabled`가 컴퓨트 단위다**.

    `short_enabled=True` 한 번이 팔 셋을 먹인다. `False`는 **검산 (a) 전용**이다(엔진이
    직접 낸 롱 후보와 「걸러낸 롱」이 같은지 확인).
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        combine_obs=ADOPTED_COMBINE_OBS,
        retap_mode=ADOPTED_RETAP_MODE,
        short_enabled=short_enabled,
        payload_cache=cache,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def scoped(payloads: Sequence[CellPayload], arm: Arm) -> list[CellPayload]:
    """그 팔의 방향만 남긴 payload 사본.

    🚨 **base 후보와 재진입 후보 둘 다** 판다 — 한쪽만 걸면 「숏만」 팔에 롱 재진입이
    남는 잡종이 되고, 개수만 보면 안 보인다(WAN-376 §1a가 이름 붙인 급소의 방향 축 판).
    경계(`boundary_ms`)·펀딩·격리 행은 그대로 둔다 — 방향은 배치 축이지 좌표가 아니다.
    """
    out: list[CellPayload] = []
    for payload in payloads:
        out.append(
            replace(
                payload,
                candidates={
                    seg: tuple(c for c in cands if arm.keeps(c.side))
                    for seg, cands in payload.candidates.items()
                },
                reentry_candidates={
                    seg: tuple(c for c in cands if arm.keeps(c.side))
                    for seg, cands in payload.reentry_candidates.items()
                },
            )
        )
    return out


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성과 손절폭 가드를 명시한다.

    `include_reentry=True`(채택, WAN-273/305)다 — `scoped()`가 재진입 후보를 payload에
    남겨 두고 방향으로만 팠으므로 북이 그것을 base와 합쳐 배치해야 채택 규칙이 된다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
        min_stop_distance_fraction=ADOPTED_STOP_GUARD,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


# --------------------------------------------------------------------------- #
# 행 만들기
# --------------------------------------------------------------------------- #


def _net_or_none(trade: Trade, placement: PlacedSetup) -> float | None:
    """리스크 금액이 0이면 R로 정규화할 수 없다 — `_row_kwargs`와 **같은 규칙**으로 뺀다."""
    return None if placement.risk_amount <= 0 else net_r(trade, placement)


def _paired_nets(pairs: Sequence[tuple[Trade, PlacedSetup]]) -> list[tuple[Trade, float]]:
    out: list[tuple[Trade, float]] = []
    for trade, placement in pairs:
        value = _net_or_none(trade, placement)
        if value is not None:
            out.append((trade, value))
    return out


def _direction_stats(rows: Sequence[tuple[Trade, float]], side: PositionSide) -> dict[str, object]:
    picked = [net for trade, net in rows if trade.side is side]
    tag = "long" if side is PositionSide.LONG else "short"
    return {
        f"{tag}_trades": len(picked),
        f"{tag}_win_rate": (sum(1 for n in picked if n > 0) / len(picked) if picked else None),
        f"{tag}_mean_net_r": (sum(picked) / len(picked) if picked else None),
        f"{tag}_net_r_sum": sum(picked),
    }


def _daily_correlation(
    rows: Sequence[tuple[Trade, float]], regime: DailyRegime
) -> tuple[float | None, int]:
    """일별 평균 net R ↔ 그날 BTC 등락의 피어슨 상관 (완료기준 5).

    🚨 **거래 단위가 아니라 날 단위**로 잰다 — 거래 단위로 재면 거래가 많은 날(= 내린 날)이
    상관을 통째로 지배해 「상쇄됐는지」가 아니라 「거래가 어디 몰렸는지」를 재게 된다.
    """
    by_day: dict[str, list[float]] = {}
    for trade, net in rows:
        by_day.setdefault(kst_day_of(trade.entry_time), []).append(net)
    frame = pd.DataFrame(
        {
            "mean_net_r": {day: sum(v) / len(v) for day, v in by_day.items()},
            "btc_ret": {day: regime.frame["ret"].get(day, float("nan")) for day in by_day},
        }
    ).dropna()
    if len(frame) < 3:
        return None, len(frame)
    corr = float(frame["mean_net_r"].corr(frame["btc_ret"]))
    return (None if pd.isna(corr) else corr), len(frame)


def _arm_fields(arm: Arm) -> dict[str, object]:
    return {
        "arm": arm.name,
        "label": arm.label,
        "combine_obs": ADOPTED_COMBINE_OBS,
        "retap_mode": ADOPTED_RETAP_MODE,
        "adopted_arm": arm.is_adopted,
    }


def _extra_kwargs(segment: BookSegment, regime: DailyRegime | None) -> dict[str, object]:
    rows = _paired_nets(segment.trades_with_placements())
    nets = [net for _trade, net in rows]
    corr, days = (None, 0) if regime is None else _daily_correlation(rows, regime)
    return {
        "net_r_stderr": (statistics.stdev(nets) / (len(nets) ** 0.5) if len(nets) > 1 else 0.0),
        **_direction_stats(rows, PositionSide.LONG),
        **_direction_stats(rows, PositionSide.SHORT),
        "btc_return_corr": corr,
        "btc_days": days,
    }


def build_arm_rows(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    cfg: BacktestConfig,
    regime: DailyRegime | None,
    segments: Sequence[str] = SEGMENT_ORDER,
) -> tuple[list[ArmRow], list[BookSegment]]:
    view = scoped(payloads, arm)
    placed = place(view, start_ms=start_ms, end_ms=end_ms, segments=segments)
    rows = [
        ArmRow(
            **_arm_fields(arm),
            **_row_kwargs(
                segment,
                cfg,
                num_symbols=num_symbols,
                entry_position=entry_in_zone(view, segment.segment, include_reentry=True),
            ),
            **_extra_kwargs(segment, regime),
        )
        for segment in placed
    ]
    return rows, placed


def regime_buckets(placed: Sequence[tuple[Arm, BookSegment]], regime: DailyRegime) -> pd.DataFrame:
    """§1의 **공유 분위 축** — 팔 셋이 **같은 날 버킷**을 봐야 비교가 성립한다.

    🚨 팔마다 「자기가 거래한 날」로 자르면 축이 팔마다 달라진다 — 파일럿에서 실제로
    `long_only`의 분위 1이 평균 −5.62%인데 `short_only`의 분위 1은 −1.20%가 나왔다.
    그러면 *「BTC 내린 날 숏이 몇 건 하는가」*라는 이 이슈의 질문에 **답할 수 없다**
    (두 표가 다른 「내린 날」을 말한다).

    그래서 축은 **시장의 성질**로 정한다 — 이 구간에서 어느 팔이든 거래한 날들의
    **첫날~마지막날 달력 구간의 모든 BTC 일봉**으로 자른다. 거래가 하루도 없는 날도
    버킷에 들어가고(그게 시장이다) 각 팔의 거래 수는 그 위에 얹힌다.
    """
    days: set[str] = set()
    for _arm, segment in placed:
        for trade, _placement in segment.trades_with_placements():
            days.add(kst_day_of(trade.entry_time))
    if not days:
        return pd.DataFrame(columns=["ret", "quantile"])
    lo, hi = min(days), max(days)
    span = [d for d in regime.frame.index if lo <= d <= hi]
    return regime.quantiles_for(pd.Series(span))


def build_regime_rows(segment: BookSegment, *, arm: Arm, buckets: pd.DataFrame) -> list[RegimeRow]:
    """§1 판정 표 — BTC 등락 5분위 × 방향. 분위 축(`buckets`)은 **팔 셋이 공유**한다."""
    rows = _paired_nets(segment.trades_with_placements())
    if buckets.empty:
        return []
    frame = pd.DataFrame(
        {
            "kst_day": [kst_day_of(trade.entry_time) for trade, _ in rows],
            "side": ["long" if trade.side is PositionSide.LONG else "short" for trade, _ in rows],
            "net_r": [net for _, net in rows],
        }
    )
    if not frame.empty:
        frame = frame.join(buckets, on="kst_day").dropna(subset=["quantile"])
    day_q = buckets.reset_index(names="kst_day")
    out: list[RegimeRow] = []
    for quantile in range(1, NUM_QUANTILES + 1):
        cell = frame[frame["quantile"] == quantile] if not frame.empty else frame
        days_in = day_q[day_q["quantile"] == quantile]
        for direction in ("all", "long", "short"):
            picked = cell if direction == "all" or cell.empty else cell[cell["side"] == direction]
            nets = picked["net_r"] if not picked.empty else pd.Series(dtype=float)
            out.append(
                RegimeRow(
                    arm=arm.name,
                    segment=segment.segment,
                    quantile=quantile,
                    days=int(len(days_in)),
                    btc_ret_mean=float(days_in["ret"].mean()) if len(days_in) else 0.0,
                    direction=direction,
                    traded_days=int(picked["kst_day"].nunique()) if not picked.empty else 0,
                    trades=int(len(nets)),
                    win_rate=float((nets > 0).mean()) if len(nets) else None,
                    mean_net_r=float(nets.mean()) if len(nets) else None,
                    net_r_sum=float(nets.sum()) if len(nets) else 0.0,
                )
            )
    return out


def build_cell_attribution(segment: BookSegment, *, arm: Arm) -> list[CellAttributionRow]:
    """(A) 귀속 — 북이 실제로 한 거래를 칸으로 쪼갠다 (**재배치 없음**)."""
    buckets: dict[tuple[str, str], list[tuple[Trade, float]]] = {}
    for trade, placement in segment.trades_with_placements():
        value = _net_or_none(trade, placement)
        if value is not None:
            buckets.setdefault(placement.cell, []).append((trade, value))
    out: list[CellAttributionRow] = []
    for (symbol, timeframe), rows in sorted(buckets.items()):
        for direction, side in (
            ("all", None),
            ("long", PositionSide.LONG),
            ("short", PositionSide.SHORT),
        ):
            picked = rows if side is None else [(t, n) for t, n in rows if t.side is side]
            nets = [n for _t, n in picked]
            if not nets:
                continue
            out.append(
                CellAttributionRow(
                    arm=arm.name,
                    segment=segment.segment,
                    symbol=_short(symbol),
                    timeframe=timeframe,
                    direction=direction,
                    trades=len(nets),
                    win_rate=sum(1 for n in nets if n > 0) / len(nets),
                    mean_net_r=sum(nets) / len(nets),
                    net_r_sum=sum(nets),
                )
            )
    return out


def build_scope_rows(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    cfg: BacktestConfig,
    regime: DailyRegime | None,
    segments: Sequence[str] = (harness.SEGMENT_FULL, PRIMARY_OOS),
) -> list[ScopeRow]:
    """(B) 스코프 — TF마다 그 칸들만으로 **지갑을 다시 배치**한다 (라벨 필터가 아니다)."""
    view = scoped(payloads, arm)
    out: list[ScopeRow] = []
    for scope in sorted({p.timeframe for p in view}, key=timeframe_to_ms):
        kept = [p for p in view if p.timeframe == scope]
        for segment in place(kept, start_ms=start_ms, end_ms=end_ms, segments=list(segments)):
            out.append(
                ScopeRow(
                    scope=scope,
                    **_arm_fields(arm),
                    **_row_kwargs(
                        segment,
                        cfg,
                        num_symbols=len({p.symbol for p in kept}),
                        entry_position=entry_in_zone(kept, segment.segment, include_reentry=True),
                    ),
                    **_extra_kwargs(segment, regime),
                )
            )
    return out


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    cfg: BacktestConfig,
    regime: DailyRegime | None,
    log: bool = True,
) -> list[LooRow]:
    """종목 하나씩 빼고 **지갑을 다시 배치**한다 — 라벨 필터가 아니다(WAN-316)."""
    view = scoped(payloads, arm)
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in view})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for drop_label, dropped in drops:
        drop = {s.upper() for s in dropped}
        kept = [p for p in view if _short(p.symbol) not in drop]
        if not kept:
            continue
        for segment in place(kept, start_ms=start_ms, end_ms=end_ms, segments=LOO_SEGMENTS):
            rows.append(
                LooRow(
                    **_arm_fields(arm),
                    exclude=drop_label,
                    **_row_kwargs(
                        segment,
                        cfg,
                        num_symbols=len({p.symbol for p in kept}),
                        entry_position=entry_in_zone(kept, segment.segment, include_reentry=True),
                    ),
                    **_extra_kwargs(segment, regime),
                )
            )
    if log:
        print(f"[wan412] {arm.name}: leave-one-out {len(drops)}판 완료", flush=True)
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def checksum_candidate_parity(
    withshort: Sequence[CellPayload],
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    start: str,
    end: str,
    jobs: int,
    cache: PayloadCache | None,
    log: bool = True,
) -> list[ChecksumRow]:
    """검산 (a) — 숏 게이트가 **롱 후보를 건드리지 않는가**, 그리고 재진입은 얼마나 갈리나.

    🚨 이 검산이 이 모듈의 자격 증명이다. 팔 셋이 **후보 하나를 나눠 쓰므로**, 숏을 켠
    세계의 롱 후보가 롱 전용 세계의 것과 다르면 `both − long_only`가 「방향의 몫」이 아니다.
    그래서 같은 48칸을 `short_enabled=False`로 한 번 더 만들어 **칸마다** 대조한다.

    📌 **실측 결과 층이 갈렸다**(2026-09-09):

    * **base 후보 — 48칸 전부 일치**. WAN-282의 전제(*숏 게이트는 롱 후보를 안 건드린다*)는
      **탐지·시그널 층에서 참**이다. 그래서 `both`의 롱 다리는 정당하다.
    * **재진입 후보 — 46/48칸이 다르다**. 재진입은 **부모가 익절해야** 파생되는데 그 파생이
      칸당 1포지션 시퀀싱을 거치므로, 숏이 슬롯을 먹으면 롱 부모의 익절 집합이 달라진다.
      방향은 **언제나 적게** 나온다(숏이 자리를 먹어 롱이 덜 익절한다). 15m에 몰린다.

    ⚠️ **그래서 이 팔들의 재무장 일정은 「숏과 경합한 세계」의 것이다** — WAN-386/395가
    *「재무장 일정이 배수마다 고정」*이라 밝힌 것과 **같은 부류의 한계**이고, 이 검산이 그
    크기를 재서 표에 싣는다(감추지 않는다).

    📌 **그럼에도 `long_only`를 엔진 판으로 갈아끼우지 않는다** — 그러면 축이 **둘** 움직인다
    (방향 + 재무장 일정). 팔 셋이 같은 후보 세계를 공유해야 차가 순수하게 방향의 몫이다
    (*축을 하나만 흔든다*). 엔진 판은 **외부 기준**으로 이 검산에만 등장한다.
    """
    if log:
        print("[wan412] 검산(a): short_enabled=False 후보 생성 중…", flush=True)
    plain = {
        (p.symbol, p.timeframe): p
        for p in build_payloads(
            symbols,
            timeframes,
            short_enabled=False,
            start=start,
            end=end,
            jobs=jobs,
            cold_segments=False,
            cache=cache,
        )
    }
    longs = {(p.symbol, p.timeframe): p for p in scoped(withshort, ARM_LONG_ONLY)}
    seg = harness.SEGMENT_FULL
    base_diff = reentry_diff = 0
    base_cells = reentry_cells = 0
    for key, other in plain.items():
        mine = longs.get(key)
        if mine is None:
            continue
        b1 = {_identity(c) for c in mine.candidates.get(seg, ())}
        b2 = {_identity(c) for c in other.candidates.get(seg, ())}
        r1 = {_identity(c) for c in mine.reentry_candidates.get(seg, ())}
        r2 = {_identity(c) for c in other.reentry_candidates.get(seg, ())}
        base_diff += len(b1 ^ b2)
        reentry_diff += len(r1 ^ r2)
        base_cells += b1 != b2
        reentry_cells += r1 != r2
    return [
        ChecksumRow(
            check="a_short_gate_does_not_touch_long_candidates",
            arm=ARM_LONG_ONLY.name,
            segment=seg,
            metric=metric,
            left=float(value),
            right=0.0,
            abs_diff=float(value),
        )
        for metric, value in (
            ("base_candidates_mismatched", base_diff),
            ("base_cells_mismatched", base_cells),
        )
    ] + [
        ChecksumRow(
            check="a2_reentry_derivation_differs_known_limit",
            arm=ARM_LONG_ONLY.name,
            segment=seg,
            metric=metric,
            left=float(value),
            right=float(value),
            abs_diff=0.0,
        )
        for metric, value in (
            ("reentry_candidates_differing", reentry_diff),
            ("reentry_cells_differing", reentry_cells),
        )
    ]


def _identity(cand: object) -> tuple[object, ...]:
    """후보의 정체성 — 방향·진입 시각·진입가·청산 시각·손절가 (검산 (a) 조인 키)."""
    return (
        cand.side,  # type: ignore[attr-defined]
        cand.entry_time,  # type: ignore[attr-defined]
        round(float(cand.entry_price), 10),  # type: ignore[attr-defined]
        cand.exit_time,  # type: ignore[attr-defined]
        round(float(cand.stop_price), 10),  # type: ignore[attr-defined]
    )


def checksum_direction(rows: Sequence[ArmRow]) -> list[ChecksumRow]:
    """검산 (b) — 팔이 **라벨이 아니라 동작으로** 방향을 걸렀는가."""
    out: list[ChecksumRow] = []
    for row in rows:
        if row.arm == ARM_SHORT_ONLY.name:
            out.append(
                ChecksumRow(
                    check="b_short_only_has_no_long",
                    arm=row.arm,
                    segment=row.segment,
                    metric="long_trades",
                    left=float(row.long_trades),
                    right=0.0,
                    abs_diff=float(row.long_trades),
                )
            )
        if row.arm == ARM_LONG_ONLY.name:
            out.append(
                ChecksumRow(
                    check="b_long_only_has_no_short",
                    arm=row.arm,
                    segment=row.segment,
                    metric="short_trades",
                    left=float(row.short_trades),
                    right=0.0,
                    abs_diff=float(row.short_trades),
                )
            )
    return out


def checksum_short_geometry(payloads: Sequence[CellPayload]) -> list[ChecksumRow]:
    """검산 (c) — 숏 후보의 **기하가 뒤집혀 있다**(손절 > 진입 > 익절).

    라벨이 `SHORT`인데 손절이 진입 아래에 있으면 그건 「롱을 숏이라 부른 것」이고 이 표
    전체가 무효다. 위반 건수를 센다(둘 다 0이어야 한다).
    """
    checked = stop_bad = tp_bad = 0
    for payload in payloads:
        for cands in (*payload.candidates.values(), *payload.reentry_candidates.values()):
            for cand in cands:
                if cand.side is not PositionSide.SHORT:
                    continue
                checked += 1
                if cand.stop_price <= cand.entry_price:
                    stop_bad += 1
                tp = cand.take_profit_price
                if tp is not None and tp >= cand.entry_price:
                    tp_bad += 1
    counts = (
        ("short_candidates", checked, checked),
        ("stop_not_above_entry", stop_bad, 0),
        ("take_profit_not_below_entry", tp_bad, 0),
    )
    return [
        ChecksumRow(
            check="c_short_geometry_is_mirrored",
            arm=ARM_SHORT_ONLY.name,
            segment="candidates",
            metric=metric,
            left=float(left),
            right=float(right),
            abs_diff=float(abs(left - right)),
        )
        for metric, left, right in counts
    ]


# --------------------------------------------------------------------------- #
# 프레임 · CSV 왕복
# --------------------------------------------------------------------------- #


_RowT = TypeVar("_RowT", bound=BaseModel)


def _frame(rows: Sequence[BaseModel], model: type[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(model.model_fields))


def _read(path: Path, model: type[_RowT]) -> list[_RowT]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    # 🚨 pandas가 빈 칸을 NaN으로 되살리는데 pydantic이 그것을 유효한 float으로 받아
    # 요약이 `nan%`를 찍는다(WAN-395가 고친 함정) — 모델에서 한 번 None으로 되돌린다.
    return [
        model(**{k: (None if pd.isna(v) else v) for k, v in rec.items()})
        for rec in frame.to_dict("records")
    ]


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:+.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _sign_is_decided(delta: float, base: ArmRow, other: ArmRow) -> bool:
    """차의 **부호가 정해졌는가** — 두 팔의 표준오차를 합성한 2σ 밖인가 (WAN-381/394 규약).

    🚨 이 검사가 없으면 오차보다 작은 차를 판정으로 찍는다. 실제로 이 격자가 그랬다:
    5종목 20칸에서 **−0.0099R**이던 차가 12종목 48칸에서 **+0.0056R**로 **부호가
    뒤집혔는데**, 노이즈선(±0.005R)만 보는 옛 판정은 둘 다 「판정」으로 찍었다. 진짜
    효과라면 유니버스를 넓혔다고 부호가 뒤집히지 않는다.

    ⚠️ 두 팔은 **같은 후보 세계**를 공유해 독립이 아니므로(롱 다리가 겹친다) 독립 가정
    합성은 **보수적**이다 — 실제 불확실성은 이보다 작다. 그래도 이 자를 쓰는 이유는
    「부호를 못 정한다」를 **놓치지 않기 위해서**이지 정밀한 구간을 주장하기 위해서가 아니다.
    """
    sigma = (base.net_r_stderr**2 + other.net_r_stderr**2) ** 0.5
    return bool(abs(delta) > 2.0 * sigma)


def _verdict(rows: Sequence[ArmRow]) -> str:
    """판정 한 줄 — 착수 전에 못 박은 규칙대로 **코드가** 낸다(사람이 표를 보고 정하지 않는다).

    자는 주 구간(`oos_warm`)에서 보고, **두 관문을 다 넘어야** 판정을 찍는다:

    1. **부호가 정해졌는가** — 차가 두 팔 표준오차 합성의 2σ 밖인가(`_sign_is_decided`).
    2. 노이즈선(±0.005R) 밖인가.

    둘 다 넘으면 (가) 상쇄됨 / (나) 거울상, 아니면 **(다) 부호 미정**이다.
    """
    by_arm = {row.arm: row for row in rows if row.segment == PRIMARY_OOS}
    base, both = by_arm.get(ARM_LONG_ONLY.name), by_arm.get(ARM_BOTH.name)
    if base is None or both is None:
        return "⚠️ 판정 불가 — 주 구간(`oos_warm`) 행이 모자랍니다."
    delta = both.mean_net_r - base.mean_net_r
    sigma = (base.net_r_stderr**2 + both.net_r_stderr**2) ** 0.5
    decided = _sign_is_decided(delta, base, both)
    corr_drop = (
        abs(base.btc_return_corr) - abs(both.btc_return_corr)
        if base.btc_return_corr is not None and both.btc_return_corr is not None
        else None
    )
    corr_note = (
        f"BTC 상관 |{base.btc_return_corr:+.3f}| → |{both.btc_return_corr:+.3f}|"
        if corr_drop is not None
        else "BTC 상관 미정의"
    )
    if not decided:
        head = (
            f"**(다) 부호 미정 — 거래당 실력은 안 바뀌고 거래만 는다**(차가 2σ={2 * sigma:.4f}R 안)"
        )
    elif delta > NOISE_R and (corr_drop is None or corr_drop > 0):
        head = "**(가) 상쇄됨**"
    elif delta < -NOISE_R:
        head = "**(나) 거울상 — 양쪽으로 지면서 거래만 는다**"
    else:
        head = "**(다) 무의 — 차가 노이즈선 안**"
    grew = both.num_trades / base.num_trades - 1 if base.num_trades else 0.0
    trades = f"거래 {base.num_trades:,} → {both.num_trades:,}({grew:+.0%})"
    return (
        f"{head}: `both` − `long_only` = **{delta:+.4f}R** ± {sigma:.4f} "
        f"(노이즈선 ±{NOISE_R:g}R · {corr_note} · {trades})"
    )


def build_summary_markdown(
    grid: Sequence[ArmRow],
    regime_rows: Sequence[RegimeRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    scopes: Sequence[ScopeRow] = (),
    cells: Sequence[CellAttributionRow] = (),
) -> str:
    lines: list[str] = [
        "# WAN-412 — 숏을 더하면 BTC 하락일 손실이 상쇄되는가 (오늘 엔진 · 채택 북)",
        "",
        "채택 좌표(12종목 × 15m·1h·2h·4h **한 지갑** · 못 박은 6년 · 인과 취소 · 존폭 필터 끔 ·",
        "재진입 band · cap_only 5배 · 익절 1.5R) 위에서 **방향만** 흔든 표다. **핀 없음** ·",
        "판단은 북에서(WAN-341) · 전부 `baseline`(낙관) 렌즈 위 값.",
        "",
        "🚨 **측정 전용** — `ConfluenceParams()`의 `short_enabled=False`를 **안 바꾼다**.",
        "숏 재활성화는 **재-베이스라인 = 사용자 결정**이다(WAN-287이 2026-08-14에 취소된 자리).",
        "",
        "## 판정",
        "",
        _verdict(grid),
        "",
    ]

    primary = [r for r in grid if r.segment == PRIMARY_OOS]
    if primary:
        lines += [
            "## §2 팔 셋 (주 구간 `oos_warm`)",
            "",
            "| 팔 | 거래 | 롱 | 숏 | 승률 | 거래당 netR | ± | netR 합(롱/숏) "
            "| gross | 비용 | MDD | 지갑 |",
            "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | -- |",
        ]
        for row in primary:
            lines.append(
                f"| `{row.arm}` | {row.num_trades:,} | {row.long_trades:,} "
                f"| {row.short_trades:,} | {row.win_rate * 100:.2f}% "
                f"| **{row.mean_net_r:+.4f}** | ±{row.net_r_stderr:.4f} | "
                f"{row.long_net_r_sum:+,.0f} / {row.short_net_r_sum:+,.0f} | "
                f"{row.gross_r:+.4f} | {row.cost_r:.4f} | "
                f"{'—' if not wallet_defined(row) else _pct(row.max_drawdown)} | "
                f"{'정의' if wallet_defined(row) else '**정의 상실**'} |"
            )
        lines += [
            "",
            "🚨 **지갑 층 열은 이 좌표에서 뜻을 잃을 수 있다** — 거래당 기대값이 음수라 복리를",
            "꺼도 잔고가 0을 뚫는다(WAN-386/388 `wallet_defined` 규약). 「정의 상실」로 찍힌 행의",
            "MDD·수익/MDD·청산은 **읽지 않는다**. 판정 자는 처음부터 거래당 net R이다.",
            "",
        ]

    lines += [
        "## §2-1 네 구간",
        "",
        "| 팔 | 구간 | 거래 | 승률 | 거래당 net R | net R 합 |",
        "| -- | -- | --: | --: | --: | --: |",
    ]
    for row in grid:
        total = row.long_net_r_sum + row.short_net_r_sum
        lines.append(
            f"| `{row.arm}` | {row.segment} | {row.num_trades:,} | {row.win_rate * 100:.2f}% | "
            f"{row.mean_net_r:+.4f} | {total:+,.0f} |"
        )
    lines.append("")

    if regime_rows:
        lines += [
            "## §1 🚨 BTC 일간 등락 5분위 × 방향 (주 구간 · **이 표가 답이다**)",
            "",
            "하루 = **KST 달력일** · 등락 = 그날 첫 1h `open` → 마지막 1h `close`(저장 봉).",
            "🚨 분위 축은 **팔 셋이 공유한다**(1 = 가장 많이 내린 날) — 팔마다 자기가 거래한",
            "날로 자르면 축이 팔마다 달라져 *「BTC 내린 날 숏이 몇 건 하는가」*에 답할 수 없다.",
            "`일수`는 그 버킷의 **달력 날 수**(공유)이고 `매매일`은 그 팔이 실제로 거래한 날 수다.",
            "",
            "| 팔 | 분위 | 일수 | BTC 등락 | 롱 거래 | 롱 netR "
            "| 숏 거래 | 숏 netR | 합 거래 | 합 netR | 매매일 |",
            "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
        ]
        indexed = {(r.arm, r.quantile, r.direction): r for r in regime_rows}
        for arm in ARMS:
            for q in range(1, NUM_QUANTILES + 1):
                cell = indexed.get((arm.name, q, "all"))
                if cell is None:
                    continue
                lo = indexed.get((arm.name, q, "long"))
                sh = indexed.get((arm.name, q, "short"))
                lines.append(
                    f"| `{arm.name}` | {q} | {cell.days} | {cell.btc_ret_mean * 100:+.2f}% | "
                    f"{lo.trades if lo else 0:,} | {_fmt(lo.mean_net_r if lo else None)} | "
                    f"{sh.trades if sh else 0:,} | {_fmt(sh.mean_net_r if sh else None)} | "
                    f"{cell.trades:,} | **{_fmt(cell.mean_net_r)}** | {cell.traded_days} |"
                )
        lines += [
            "",
            "🚨 **읽는 법** — *「BTC 내린 날(분위 1~2) 숏이 몇 건 하는가」*가 롱 거래 수에 비해",
            "**작으면 상쇄가 구조적으로 불가능하다**(반대편 다리가 정작 필요한 날에 안 서 있다).",
            "⚠️ **분위는 그날이 끝나야 안다** — 이 축은 **설명 변수이지 진입 조건이 아니다**",
            "(그것으로 거르는 것은 인과적으로 불가능하다 — WAN-408이 건 함정).",
            "",
        ]

    if scopes:
        lines += [
            "## §3-B TF별 스코프 — 그 TF만으로 **지갑을 다시 배치**한 판 (WAN-301/316)",
            "",
            "🚨 **아래 §3-A를 TF로 합친 값과 같지 않다. 같으면 그게 버그다** — 여기서는 다른",
            "TF가 자본을 안 먹으므로 그 12칸이 더 많이 들어간다. **다른 질문에 답하는 두 표**다.",
            "",
            "| TF | 팔 | 구간 | 거래 | 롱 | 숏 | 승률 | 거래당 netR | BTC 상관 |",
            "| -- | -- | -- | --: | --: | --: | --: | --: | --: |",
        ]
        for row in scopes:
            lines.append(
                f"| {row.scope} | `{row.arm}` | {row.segment} | {row.num_trades:,} "
                f"| {row.long_trades:,} | {row.short_trades:,} | {row.win_rate * 100:.2f}% "
                f"| **{row.mean_net_r:+.4f}** | {_fmt(row.btc_return_corr, 3)} |"
            )
        lines.append("")

    if cells:
        lines += [
            "## §3-A 종목 × TF 귀속 — **채택 북 한 지갑**이 실제로 한 거래 (주 구간)",
            "",
            "🚨 **재배치가 아니다** — 48칸이 자본을 나눠 쓴 그 지갑의 거래를 칸으로 쪼갠 것이라",
            "「그 칸이 자리를 못 잡아 못 들어간 거래」도 반영돼 있다.",
            "",
            "| 팔 | 칸 | 롱 거래 | 롱 netR | 숏 거래 | 숏 netR | 합 netR |",
            "| -- | -- | --: | --: | --: | --: | --: |",
        ]
        cell_idx: dict[tuple[str, str, str, str], CellAttributionRow] = {
            (c.arm, c.symbol, c.timeframe, c.direction): c
            for c in cells
            if c.segment == PRIMARY_OOS
        }
        keys = sorted({(c.arm, c.symbol, c.timeframe) for c in cells if c.segment == PRIMARY_OOS})
        for arm_name, sym, tf in keys:
            whole = cell_idx.get((arm_name, sym, tf, "all"))
            lng = cell_idx.get((arm_name, sym, tf, "long"))
            srt = cell_idx.get((arm_name, sym, tf, "short"))
            if whole is None:
                continue
            lines.append(
                f"| `{arm_name}` | {sym} {tf} | {lng.trades if lng else 0:,} "
                f"| {_fmt(lng.mean_net_r if lng else None)} | {srt.trades if srt else 0:,} "
                f"| {_fmt(srt.mean_net_r if srt else None)} | **{_fmt(whole.mean_net_r)}** |"
            )
        lines.append("")

    corr_rows = [r for r in grid if r.segment == PRIMARY_OOS]
    if corr_rows:
        lines += [
            "## §5 BTC 등락과 일별 성적의 상관 (주 구간)",
            "",
            "**일별 평균 net R ↔ 그날 BTC 등락**의 피어슨 상관. 📌 0에 가까워지면 그게",
            "**「상쇄됐다」의 정의**다. 🚨 거래 단위가 아니라 **날 단위**로 잰다 — 거래 단위로",
            "재면 거래가 많은 날(= 내린 날)이 상관을 통째로 지배한다.",
            "",
            "| 팔 | 상관 | 날 수 |",
            "| -- | --: | --: |",
        ]
        for row in corr_rows:
            lines.append(f"| `{row.arm}` | {_fmt(row.btc_return_corr, 3)} | {row.btc_days} |")
        lines.append("")

    if loo:
        lines += [
            "## §4 leave-one-out (지갑 **재배치** · WAN-316)",
            "",
            "| 팔 | 제외 | 구간 | 거래 | 거래당 net R |",
            "| -- | -- | -- | --: | --: |",
        ]
        for row in loo:
            lines.append(
                f"| `{row.arm}` | {row.exclude} | {row.segment} | {row.num_trades:,} | "
                f"{row.mean_net_r:+.4f} |"
            )
        lines.append("")

    if checks:
        worst = max(checks, key=lambda c: c.abs_diff)
        lines += [
            "## ✅ 검산",
            "",
            f"최대 절대차 **{worst.abs_diff:.2e}** ({worst.check} · {worst.metric})",
            "",
            "| 검산 | 구간 | 지표 | 좌 | 우 | 차 |",
            "| -- | -- | -- | --: | --: | --: |",
        ]
        for check in checks:
            lines.append(
                f"| {check.check} | {check.segment} | {check.metric} | {check.left:,.6g} | "
                f"{check.right:,.6g} | {check.abs_diff:.2e} |"
            )
        lines.append("")

    lines += [
        "## ⚠️ 경고",
        "",
        "* 전부 `baseline`(닿으면 체결) **낙관** 렌즈 위 값이고 체결 보수화(`pen_5bp`)는",
        "  **안 쟀다**",
        "  — 숏 축은 특히 안 쟀다(WAN-282~285가 그 렌즈에서 크게 깎였다).",
        "* 6년 MDD는 폭락 미포함 **바닥선**이다(창이 2020-09 시작이라 2018·2020-03 미포함).",
        "* 총수익 %는 복리 착시(WAN-169/213)이고 이 좌표에서는 **포화**한다 — 판정 자가 아니다.",
        "* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *반대편 다리를",
        "  달면 방향 노출이 줄어드는가*를 묻는다(**다른 질문**).",
        "* 🚨 **「이걸로 흑자」로 기대하지 말 것** — 롱 온리 gross가 +0.0019R(WAN-396)이라, 숏이",
        "  그 자체로 플러스가 아니면 상쇄는 **분산을 줄이지 기대값을 못 올린다**.",
        "* 🚨 **셀 비교 금지** — WAN-282/288/301/302의 숏 표는 **좌표가 다르다**(9종목 · 존폭 필터",
        "  켬 · 소급 취소 엔진 · 재진입 없는 스냅샷 북). **방향만** 대조한다(WAN-316 부류).",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-412 — 방향 팔 셋 × 채택 북")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--tf", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=None)
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="BTC 4h 한 칸으로 배선·비용을 확인한다(채택 좌표가 아니라 검산 (a)는 건너뛴다).",
    )
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 다시 낸다.")
    parser.add_argument("--no-loo", action="store_true", help="leave-one-out을 건너뛴다.")
    parser.add_argument(
        "--no-checksum", action="store_true", help="검산 (a)의 두 번째 후보 생성을 건너뛴다."
    )
    parser.add_argument("--no-cache", action="store_true", help="후보 payload 디스크 캐시를 끈다.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.from_csv:
        SUMMARY_PATH.write_text(
            build_summary_markdown(
                _read(GRID_CSV_PATH, ArmRow),
                _read(REGIME_CSV_PATH, RegimeRow),
                _read(LOO_CSV_PATH, LooRow),
                _read(CHECKSUM_CSV_PATH, ChecksumRow),
                _read(SCOPE_CSV_PATH, ScopeRow),
                _read(CELL_CSV_PATH, CellAttributionRow),
            ),
            encoding="utf-8",
        )
        print(f"[wan412] 요약만 재생성: {SUMMARY_PATH}")
        return

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.tf.split(",") if t.strip()]
    if args.pilot:
        symbols, timeframes = [symbols[0]], ["4h"]
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    print(
        f"[wan412] 병렬 설정: 워커 {jobs}개 · {len(symbols)}종목 × {len(timeframes)}TF "
        f"= {len(symbols) * len(timeframes)}칸 · {args.start}~{args.end}",
        flush=True,
    )
    cache = None if args.no_cache else PayloadCache(DEFAULT_CACHE_DIR)
    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)
    cfg = _cfg()
    regime = load_daily_regime()

    began = time.time()
    payloads = build_payloads(
        symbols,
        timeframes,
        short_enabled=True,
        start=args.start,
        end=args.end,
        jobs=jobs,
        cache=cache,
    )
    print(f"[wan412] 후보 생성 {time.time() - began:.0f}초 · {len(payloads)}칸", flush=True)

    num_symbols = len({p.symbol for p in payloads})
    grid: list[ArmRow] = []
    loo: list[LooRow] = []
    cells: list[CellAttributionRow] = []
    scopes: list[ScopeRow] = []
    primary: list[tuple[Arm, BookSegment]] = []
    for arm in ARMS:
        rows, placed = build_arm_rows(
            payloads,
            arm=arm,
            start_ms=start_ms,
            end_ms=end_ms,
            num_symbols=num_symbols,
            cfg=cfg,
            regime=regime,
        )
        grid.extend(rows)
        primary += [(arm, s) for s in placed if s.segment == PRIMARY_OOS]
        for segment in placed:
            if segment.segment in (harness.SEGMENT_FULL, PRIMARY_OOS):
                cells.extend(build_cell_attribution(segment, arm=arm))
        scopes.extend(
            build_scope_rows(
                payloads,
                arm=arm,
                start_ms=start_ms,
                end_ms=end_ms,
                cfg=cfg,
                regime=regime,
            )
        )
        if not args.no_loo:
            loo.extend(
                build_leave_one_out(
                    payloads,
                    arm=arm,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    cfg=cfg,
                    regime=regime,
                )
            )
        print(f"[wan412] {arm.name} 배치 완료 ({time.time() - began:.0f}초)", flush=True)

    buckets = regime_buckets(primary, regime)
    regime_rows = [
        row
        for arm, segment in primary
        for row in build_regime_rows(segment, arm=arm, buckets=buckets)
    ]

    checks = [*checksum_direction(grid), *checksum_short_geometry(payloads)]
    if not args.no_checksum and not args.pilot:
        checks = [
            *checksum_candidate_parity(
                payloads,
                symbols=symbols,
                timeframes=timeframes,
                start=args.start,
                end=args.end,
                jobs=jobs,
                cache=cache,
            ),
            *checks,
        ]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _frame(grid, ArmRow).to_csv(GRID_CSV_PATH, index=False)
    _frame(regime_rows, RegimeRow).to_csv(REGIME_CSV_PATH, index=False)
    _frame(loo, LooRow).to_csv(LOO_CSV_PATH, index=False)
    _frame(cells, CellAttributionRow).to_csv(CELL_CSV_PATH, index=False)
    _frame(scopes, ScopeRow).to_csv(SCOPE_CSV_PATH, index=False)
    _frame(checks, ChecksumRow).to_csv(CHECKSUM_CSV_PATH, index=False)
    SUMMARY_PATH.write_text(
        build_summary_markdown(grid, regime_rows, loo, checks, scopes, cells), encoding="utf-8"
    )
    print(
        f"[wan412] 완료 {time.time() - began:.0f}초 → {GRID_CSV_PATH} · {REGIME_CSV_PATH} · "
        f"{SUMMARY_PATH}",
        flush=True,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
