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


def checksum_adopted(
    long_rows: Sequence[ArmRow],
    *,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    start: str,
    end: str,
    start_ms: int,
    end_ms: int,
    jobs: int,
    cfg: BacktestConfig,
    cache: PayloadCache | None,
    log: bool = True,
) -> list[ChecksumRow]:
    """검산 (a) — `long_only` 팔 ≡ **엔진이 직접 낸 롱 전용 북**.

    🚨 이 검산이 이 모듈의 자격 증명이다. *「숏 게이트는 롱 후보를 건드리지 않는다」*는
    WAN-282가 **주장**으로 남긴 성질이고, 이 표의 팔 셋이 전부 그 위에 서 있다 —
    틀리면 `long_only`가 채택 북이 아니게 되고 `both`−`long_only` 차도 방향의 몫이 아니다.
    그래서 같은 실행에서 `short_enabled=False` 후보를 한 번 더 만들어 대조한다.

    📌 **차가운 절단(`is`/`oos`)은 안 만든다**(`cold_segments=False`) — 그 구간의 후보를
    또 탐지하면 이 검산 하나가 격자만큼 비싸진다. `full`·`oos_warm`은 **같은 전체 창
    후보**에서 나오므로(경계 필터일 뿐) 이 노브에 영향받지 않고, 대조하려는 성질
    (*「숏 게이트가 롱 후보를 안 건드린다」*)은 **구간과 무관**하다. 대조는 양쪽에 다
    있는 구간에서만 하고 요약이 어느 구간을 봤는지 그대로 적는다.
    """
    if log:
        print("[wan412] 검산(a): short_enabled=False 후보 생성 중…", flush=True)
    plain = build_payloads(
        symbols,
        timeframes,
        short_enabled=False,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=False,
        cache=cache,
    )
    baseline = {
        row.segment: row
        for row in [
            ArmRow(
                **_arm_fields(ARM_LONG_ONLY),
                **_row_kwargs(
                    segment,
                    cfg,
                    num_symbols=len({p.symbol for p in plain}),
                    entry_position=entry_in_zone(plain, segment.segment, include_reentry=True),
                ),
                **_extra_kwargs(segment, None),
            )
            for segment in place(
                plain,
                start_ms=start_ms,
                end_ms=end_ms,
                segments=[harness.SEGMENT_FULL, PRIMARY_OOS],
            )
        ]
    }
    out: list[ChecksumRow] = []
    for row in long_rows:
        other = baseline.get(row.segment)
        if other is None:
            continue
        for metric in _ADOPTED_METRICS:
            left = float(getattr(row, metric) or 0.0)
            right = float(getattr(other, metric) or 0.0)
            out.append(
                ChecksumRow(
                    check="a_long_only_is_adopted_book",
                    arm=ARM_LONG_ONLY.name,
                    segment=row.segment,
                    metric=metric,
                    left=left,
                    right=right,
                    abs_diff=abs(left - right),
                )
            )
    return out


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


def _verdict(rows: Sequence[ArmRow]) -> str:
    """판정 한 줄 — 착수 전에 못 박은 규칙대로 **코드가** 낸다(사람이 표를 보고 정하지 않는다).

    자는 셋이고 전부 주 구간(`oos_warm`)에서 본다:

    * **(가) 상쇄됨** — `both`의 거래당 net R이 `long_only`보다 **노이즈선(±0.005R) 밖으로
      좋고**, BTC 상관의 절댓값이 `long_only`보다 작다.
    * **(나) 거울상** — `both`가 `long_only`보다 노이즈선 밖으로 **나쁘다**.
    * **(다) 무의** — 둘 다 아니다(차가 노이즈선 안).
    """
    by_arm = {row.arm: row for row in rows if row.segment == PRIMARY_OOS}
    base, both = by_arm.get(ARM_LONG_ONLY.name), by_arm.get(ARM_BOTH.name)
    if base is None or both is None:
        return "⚠️ 판정 불가 — 주 구간(`oos_warm`) 행이 모자랍니다."
    delta = both.mean_net_r - base.mean_net_r
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
    if delta > NOISE_R and (corr_drop is None or corr_drop > 0):
        head = "**(가) 상쇄됨**"
    elif delta < -NOISE_R:
        head = "**(나) 거울상 — 양쪽으로 지면서 거래만 는다**"
    else:
        head = "**(다) 무의 — 차가 노이즈선 안**"
    return (
        f"{head}: `both` − `long_only` = **{delta:+.4f}R** (노이즈선 ±{NOISE_R:g}R · {corr_note})"
    )


def build_summary_markdown(
    grid: Sequence[ArmRow],
    regime_rows: Sequence[RegimeRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
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
            *checksum_adopted(
                [r for r in grid if r.arm == ARM_LONG_ONLY.name],
                symbols=symbols,
                timeframes=timeframes,
                start=args.start,
                end=args.end,
                start_ms=start_ms,
                end_ms=end_ms,
                jobs=jobs,
                cfg=cfg,
                cache=cache,
            ),
            *checks,
        ]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _frame(grid, ArmRow).to_csv(GRID_CSV_PATH, index=False)
    _frame(regime_rows, RegimeRow).to_csv(REGIME_CSV_PATH, index=False)
    _frame(loo, LooRow).to_csv(LOO_CSV_PATH, index=False)
    _frame(checks, ChecksumRow).to_csv(CHECKSUM_CSV_PATH, index=False)
    SUMMARY_PATH.write_text(
        build_summary_markdown(grid, regime_rows, loo, checks), encoding="utf-8"
    )
    print(
        f"[wan412] 완료 {time.time() - began:.0f}초 → {GRID_CSV_PATH} · {REGIME_CSV_PATH} · "
        f"{SUMMARY_PATH}",
        flush=True,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
