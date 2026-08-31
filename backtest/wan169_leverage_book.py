"""WAN-169 — 타임프레임·종목 가로지르는 레버리지 북의 손익·위험 측정.

사용자 정의(2026-07-22): 진입 단위 = **(종목, TF) 칸**, 칸 안에서는 청산 전 1포지션,
여러 칸이 **한 지갑(공유 자본)** 을 나눠 쓰며, 레버리지 N배 = **매 거래 사이징 N배**
(리스크 1% → N%, 원문: *"한번의 진입이 원래 1%였다면 3배일때는 3% 이런식으로"*).
엔진은 `backtest.leverage_book`(§1·§2)이고, 이 모듈은 그 위의 측정 격자(§3)다.

## 격자

* **팔**: 격리(현행 — 각 칸 독립 자본, 채택 단일 포지션 엔진 그대로) vs **공유 자본 북**.
* **사이징**: `risk_pct`(현행, 리스크 1%×N) vs `fixed_notional`(시드 분할 — 명목 =
  자본 × N/칸수, WAN-108 2안의 오늘 엔진 판. ⚠️ 옛 WAN-108의 "2안이 진다"는 옛 엔진
  값이라 결론으로 재인용 금지 — 이 표가 처음 잰다).
* **배수**: 1 · 2 · 3 · 5 (1배 = 채택 사이징 그대로에 자본 공유만 얹은 기준점).
* **스코프**: 15m(6칸) · 1h(6칸) · both(12칸 — 사용자 정의의 실제 북).
* **구간**: full · is · **oos_warm(주 수치)** · oos(스트레스) — WAN-166 정본 규약.
  straddle 회계 = **(b) 배치 안 함**(사용자 결정, `docs/decisions/wan169.md`).
* leave-one-out(종목 편중) · 20건 게이트 · 렌즈 `baseline` 단독 · 못 박은 창.

## 판정 열 (사용자 지시 2026-07-22)

원수익 단독이 아니라 **위험조정**으로 판정한다: `total_return` · MDD · **수익/MDD(주)** ·
**통합 최대 동시 리스크**(전 포지션 동시 손절 시 공유 자본 대비 % — WAN-108이 1안 12%
vs 2안 55.7%로 가른 지표의 오늘 엔진 초측정) · **청산 트리거 건수**(최악 가정, WAN-103
결정 4 — 이 모델에선 필수 열).

## 재현

```
uv run python -m backtest.wan169_leverage_book --jobs 6
uv run python -m backtest.wan169_leverage_book --from-csv   # 요약만 재생성
```
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, field_validator

from backtest import harness
from backtest.confirmation_arm import ARM_BASE, ARM_C_OFFSET, derive_arm_candidates
from backtest.harness import (
    IS_FRACTION,
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    WARM_OOS_SEGMENT,
    Segment,
)
from backtest.leverage_book import BookCell, LeverageBookParams, run_leverage_book
from backtest.models import BacktestConfig, ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.substep import SubStep, build_substeps
from backtest.sweep import timeframe_to_ms
from backtest.wan167_position_census import ALL_SYMBOLS, MAIN_TIMEFRAMES
from backtest.wan228_reentry_census import ReentryEntryRule
from backtest.wan228_reentry_census import reentry_candidates as _reentry_candidates_for_cand
from backtest.zone_limit_backtest import (
    _Candidate,
    _prepare_htf,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from common.costs import Liquidity
from data.models import FundingRate
from strategy.models import ConfluenceParams, InvalidationCancel, OrderBlockParams, ZoneLimitRef

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan169_leverage_book_cells.csv"
DEFAULT_GRID_CSV = REPORTS_DIR / "wan169_leverage_book_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan169_leverage_book_summary.md"

#: 못 박은 창 — WAN-111/114/145/164/167과 동일(`--years N`은 미끄러진다).
DEFAULT_START = "2023-07-14"
DEFAULT_END = "2026-07-15"

#: 스윕 배수(사용자 확정 2026-07-22). 1배가 기준점이다.
MULTIPLES: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0)

SIZING_MODES: tuple[str, ...] = ("risk_pct", "fixed_notional")

SEGMENTS: tuple[str, ...] = (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS)

#: 판정 표본 게이트(WAN-84 유효 기준). 미달 셀은 판정에서 뺀다 — WAN-143 게이트와 같은 이유.
MIN_TRADES = 20

IS_SEGMENT = Segment(name=SEGMENT_IS, window=0, start_fraction=0.0, end_fraction=IS_FRACTION)
OOS_SEGMENT = Segment(name=SEGMENT_OOS, window=0, start_fraction=IS_FRACTION, end_fraction=1.0)

#: 북 자본곡선 지표의 연율화 앵커. 북은 거래 단위 곡선이라 Sharpe를 판정에 쓰지 않으며
#: (판정 열은 수익/MDD), 이 값은 `build_result_from_trades` 인자를 채우는 용도다.
BOOK_ANNUALIZATION_TF = "1h"


def _short(symbol: str) -> str:
    return symbol.split("/")[0].replace("USDT", "")


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class CellRow(BaseModel):
    """칸 하나 × 구간 하나의 격리(현행) 성과 — 북 대조의 원자료이자 검산 대상.

    `engine_*` 열(full 구간만)은 같은 입력을 표준 경로(`harness.run_once`)로 다시 돌린
    값이다 — 이 모듈의 후보 생성·시퀀싱 배선이 채택 엔진과 같은 숫자를 내는지의 검산
    (WAN-164 패턴). 나머지 구간은 그 검산된 배선을 재사용하므로 따로 재지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    engine_total_return: float | None = None
    engine_num_trades: int | None = None

    @field_validator("engine_total_return", "engine_num_trades", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """CSV 왕복의 빈 칸(`""`/NaN)을 `None`으로 — `RunRow`와 같은 함정 방지(WAN-130)."""
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value


class BookRow(BaseModel):
    """격자 한 셀 — (스코프 × 팔 × 사이징 × 배수 × 구간 × 제외 종목)의 성과·위험."""

    model_config = ConfigDict(frozen=True)

    scope: str
    """칸 집합: `15m`(6칸) · `1h`(6칸) · `both`(12칸 = 사용자 정의의 실제 북)."""
    arm: str
    """`isolated`(격리 현행 — 칸 평균) · `book`(공유 자본 북)."""
    sizing_mode: str
    multiple: float
    segment: str
    exclude_symbol: str = ""
    """leave-one-out 축 — 빈 문자열이면 전 종목."""
    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: int | None = None
    max_concurrent_risk: float | None = None
    max_open_notional_ratio: float | None = None
    liquidation_events: int | None = None
    clamped_entries: int | None = None
    skipped_cell_busy: int | None = None
    skipped_notional: int | None = None

    @field_validator(
        "peak_concurrency",
        "max_concurrent_risk",
        "max_open_notional_ratio",
        "liquidation_events",
        "clamped_entries",
        "skipped_cell_busy",
        "skipped_notional",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """CSV 왕복의 빈 칸(`""`/NaN)을 `None`으로 — `RunRow`와 같은 함정 방지(WAN-130)."""
        if value == "" or (isinstance(value, float) and math.isnan(value)):
            return None
        return value

    @property
    def return_over_mdd(self) -> float | None:
        """수익/MDD(주 판정 지표). MDD가 0이면 정의하지 않는다."""
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown

    @property
    def sample_ok(self) -> bool:
        return self.num_trades >= MIN_TRADES


# --------------------------------------------------------------------------- #
# 칸 실행 (무거운 fan-out 단위 — 워커가 자기 데이터를 자기가 로드한다)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Task:
    symbol: str
    timeframe: str
    start_ms: int
    end_ms: int
    take_profit_liquidity: Liquidity = harness.LEGACY_TAKE_PROFIT_LIQUIDITY
    """익절 청산 유동성(WAN-370): 후보 cfg의 `take_profit_liquidity`를 이 값으로 **명시 고정**
    한다 — `adv_fraction`과 **글자 그대로 같은 중앙화**다. 기본이 옛 값(`taker`)이라 이 함수를
    공유하는 북 측정 모듈 전부가 한 곳에서 옛 비용 회계로 보존되고, 채택 북
    (`book_cli.run_book`)과 재산출 대상(wan366·wan370)만 `ADOPTED_TAKE_PROFIT_LIQUIDITY`를
    **명시로** 넘겨 옵트인한다. 🚨 새 측정 모듈은 그 명시를 잊으면 옛 회계로 돈다 — 새 모듈은
    반드시 채택 값을 넘길 것(WAN-305).

    ⚠️ 이 cfg는 **후보 생성과 per-cell 격리 행**에만 쓰인다. 북 배치 회계의 비용은
    `book_cli.iter_book_segments`의 base_cfg가 정한다(같은 값을 넘겨야 한 표가 한 회계다).
    """
    adv_fraction: harness.AdvCapArg = harness.LEGACY_MAX_NOTIONAL_ADV_FRACTION
    """유동성 한도(WAN-244/279): 후보 생성 cfg의 `max_notional_adv_fraction`을 이 값으로 **명시
    고정**한다(`run_cell`이 `build_config(max_notional_adv_fraction=...)`으로 항상 얹는다).

    `None`(기본, 측정 모듈)이면 상한을 **끄고** ADV를 계산조차 하지 않아 후보 집합·격리 성과가
    옛 북 CSV와 비트 단위로 같다(WAN-279가 채택 기본값을 0.005로 올린 뒤에도). `UNSET`이면
    채택 기본값(= 0.005 = 유동성 한도 켜짐)을 물려받아 각 후보에 룩어헤드-안전 `adv_usd`를
    싣는다 — 채택 북(`book_cli.run_book`)이 이 경로로 옵트인한다. `float`이면 그 프랙션으로 켠다
    (wan244 측정)."""
    combine_obs: bool = False
    """존 병합(WAN-149 · WAN-388 옵트인). `False`(기본) = 채택 기본값(원본 존 단위 분리)이라
    `OrderBlockParams()`와 같은 객체가 나가 예전과 **비트 단위로 같다**. `True`면 겹치는
    오더블록을 하나로 접어 탐지하므로 `signals`/`retap_signals`가 통째로 달라진다 —
    ⚠️ **탐지 파라미터라 값마다 오더블록을 다시 탐지해야 한다**(탐지 결과 공유 금지,
    `harness.detect_order_blocks` docstring). 그래서 이 축은 팔마다 별도 `run_cells` 실행이다.
    """
    retap_mode: str | None = None
    """재탭 정책(WAN-138 · WAN-388 옵트인). `None`(기본)이면 `build_params`가 손대지 않아
    채택 기본값 `"every_tap"`이고 예전과 **비트 단위로 같다**. `"once"`면 존(병합 시 클러스터)
    당 **첫 탭만** 진입 후보가 된다.

    🚨 **`reentry`(WAN-273)와 다른 축이다** — 재진입은 *「익절로 나온 뒤 같은 존에 재무장」*
    이고 재탭은 *「무효화 전까지 그 존을 다시 건드릴 때마다 새로 진입」*이다. `"once"`로 재탭을
    꺼도 재진입은 그대로 돈다(WAN-388은 네 팔 전부 재진입을 채택값으로 켠다).
    """
    reentry: bool = True
    """WAN-261에서 옵트인으로 태어나 **WAN-305가 기본 켬으로 승격**(채택 규칙 = 페이퍼와 같은
    선상). 켜면 각 구간의 base 후보에서 「익절 후 존 내 재진입」 후보(WAN-228 재무장 로직)를
    추가로 만들어 `CellPayload.reentry_candidates`에 싣는다. base 후보·격리 성과 행은
    **불변**이라(재진입은 별도 dict에 담긴다) `False`로 끄면 WAN-273 이전 북과 비트 단위로
    같다 — 옛 CSV를 결론에 박아 둔 모듈은 `reentry=False` **명시 핀**으로 고정한다(WAN-305)."""
    take_profit_r: float | None = None
    """전량 익절 R 배수(WAN-323 B 계열 · 옵트인). `None`이면 채택 기본값 1.5라 예전과
    **비트 단위로 같다**."""
    partial_take_profit_r: float | None = None
    """반익절 래더 분할 지점(진입 시점 1R의 배수, WAN-323 · 옵트인). `None`이면 전량 익절이라
    후보가 예전과 **비트 단위로 같다**. base 후보와 재진입 후보 **양쪽에** 같은 규칙이 걸린다 —
    한쪽만 걸면 "재진입만 전량 익절"인 잡종 엔진을 재게 된다."""
    partial_take_profit_fraction: float = 0.5
    breakeven_after_partial: bool = False
    """첫 부분 청산 뒤 손절을 진입가로(WAN-323 · 옵트인). 래더 없이 켜면 엔진이 거부한다."""
    no_same_step_tp: bool = False
    """WAN-336(옵트인): 진입한 **그 1분 스텝에서는 익절을 판정하지 않는다**. base 후보와
    재진입 후보 **양쪽에** 같은 규칙이 걸린다. 1분봉은 봉 안의 순서를 모르는데 「같은 1분에
    진입 + 익절」은 롱 기준 「저가 먼저 · 고가 나중」을 가정한 것이라, 이 팔이 그 가정을
    반대쪽 극단으로 눌러 본 반사실이다. `False`(기본)면 예전과 **비트 단위로 같다**."""
    observe_zone_width_atr: bool = False
    """WAN-376(옵트인 관측): 후보에 **존폭 ÷ ATR14**(엔진이 필터에 쓰는 그 값)를 싣는다.
    순수 관측이라 켜도 후보 집합·손익이 하나도 안 움직인다. `False`(기본)면 필드가 전부
    `None`이라 예전과 **비트 단위로 같다**."""
    observe_confirmation: bool = False
    """WAN-383 §0(옵트인 관측): 체결된 셋업마다 탭부터 존 무효화까지 한 번 더 훑어 **확인 진입
    세 팔의 트리거 시각**을 후보에 싣는다. base 후보와 재진입 후보 **양쪽에** 같은 규칙이
    걸린다(WAN-345 부류 방지). 순수 관측이라 켜도 후보 집합·손익이 하나도 안 움직이고,
    `False`(기본)면 필드가 전부 `None`이라 **비트 단위로 같다**."""
    observe_macd: bool = False
    """WAN-372(옵트인 관측): 후보에 **체결 순간의 MACD 히스토그램**(봉내 라이브 · 직전 확정봉)을
    싣는다. base 후보와 재진입 후보 **양쪽에** 같은 규칙이 걸린다 — 한쪽만 달면 색 분포표가
    채택 북 거래의 상당 부분을 놓친다(WAN-345 부류). 순수 관측이라 켜도 후보 집합·손익이
    하나도 안 움직이고, `False`(기본)면 필드가 전부 `None`이라 **비트 단위로 같다**."""
    post_filter_zone_width: float | None = None
    """WAN-376 §1a **지름길 팔**(옵트인): 엔진 필터를 **끈 채** 후보를 만들고, 그 뒤
    `zone_width_atr`가 이 문턱보다 넓은(또는 판정 불가인) 후보를 **빼고** 나머지로 재진입
    파생·격리 성과·북 입력을 만든다.

    🚨 **재진입 파생이 이 팔의 급소다** — 재진입 후보는 base 후보에서 나오므로(WAN-261),
    컷을 재진입 **뒤에** 걸면 「빠진 셋업의 재진입이 살아남는」 잡종이 된다. 그래서 컷은
    반드시 base 후보 직후 · 재진입 파생 **앞**이다.

    ⚠️ 엔진 필터(`max_zone_width_atr`)와 **동시에 못 쓴다** — 이중 필터가 되어 라벨이
    거짓이 된다(WAN-159 `none` vs 미지정 규약과 같은 부류). `None`(기본)이면 이 경로를
    아예 타지 않아 예전과 **비트 단위로 같다**."""
    no_same_step_tp_minutes: frozenset[int] = frozenset()
    """WAN-359(옵트인): **이 칸의** 「틱이 지지하지 않는」 1분 `open_time` 집합.

    비어 있으면(기본) 시뮬레이터에 `None`으로 내려가 **비트 단위로 예전과 같다**. 위
    `no_same_step_tp`(전부 끔)와 같은 축의 두 값이라 함께 켜면 엔진이 거부한다 — 「전부」와
    「이것만」이 섞이면 어느 쪽이 이겼는지 결과만 보고는 알 수 없다."""
    invalidation_cancel: InvalidationCancel | None = None
    """WAN-364 · 기본값 WAN-365: 미체결 지정가를 「존이 깨졌다」로 취소하는 **시점**.

    `None`(기본)이면 채택 기본값(`ConfluenceParams().invalidation_cancel` = `"bar_close"` =
    인과)으로 돈다. `"bar_open"`은 WAN-365 전의 옛 동작(소급 취소)이라 **옛 북 CSV를 비트
    재현**한다 — 옛 결론 모듈이 `harness.LEGACY_INVALIDATION_CANCEL`로 명시 고정하는 값이다.
    base 후보와 재진입 후보 **양쪽에** 같은 규칙이 걸린다 — 한쪽만 걸면 잡종 엔진이다
    (WAN-345 선례)."""
    bollinger: bool = True
    """WAN-366(옵트인): 볼린저 진입가 재산정(`deviation_filter`)을 켜 둘지.

    `True`(기본)면 채택 기본값 그대로라 **비트 단위로 예전과 같다**. `False`면 후보 생성
    파라미터의 `deviation_filter`를 `None`으로 덮어써 진입가가 **존 근단**에 남는다 —
    WAN-114/145/151 사다리의 `L0`/`L1` 단과 같은 축이고, 그 모듈들이 per-cell에서 하던 것을
    **북 후보 생성**에 옮긴 것이다. 규칙 3(밴드가 존보다 불리하면 기각)도 함께 꺼진다."""
    zone_limit_ref: ZoneLimitRef | None = None
    """WAN-378(옵트인): 지정가를 걸 **존 내 기준선**(`ConfluenceParams.zone_limit_ref`).

    `None`(기본)이면 채택 기본값(`"proximal"` = 존 근단)을 물려받아 **비트 단위로 예전과
    같다**. `"mid"`(존 중앙)는 사용자 제안 *"OB Zone의 딱 반 되는 가격"*의 팔이다 — 진입가를
    내려 **손절폭을 절반으로** 만들므로 손절폭 가드 축과 **독립이 아니다**(WAN-376 §0).

    ⚠️ 볼린저(`bollinger=True`)와 함께 쓰면 밴드가 진입가를 다시 덮어쓰므로(WAN-95 채택 규칙:
    둘 다 켜면 볼린저가 이긴다) 이 축은 **볼린저를 끈 팔에서만** 뜻이 있다. 그 조합을 막지는
    않는다 — 엔진의 실제 우선순위를 그대로 재는 것이 이 축의 목적이기 때문이다."""
    max_zone_width_atr: harness.ZoneWidthArg = harness.UNSET
    """WAN-366(옵트인): 존폭 필터 문턱. `harness.build_params`와 **같은 센티넬 규약**이다 —
    `UNSET`(기본)이면 채택 기본값(`1.28`)을 물려받아 비트 단위로 예전과 같고, 명시적 `None`이면
    **끈다**(사다리의 필터-끔 단). 안 가르면 「필터 끔」 라벨을 단 채 조용히 1.28로 도는 이중
    필터가 된다(WAN-159가 못 박은 규약)."""
    reentry_entry_rule: ReentryEntryRule = "band"
    """재진입 후보의 재무장 지정가 규칙 — 기본 `"band"`(봉내 라이브 밴드 재산정) = 채택 규칙
    (WAN-273, WAN-305가 기본값으로 승격). `"freeze"`(첫 체결가 고정)는 옵트인으로 존치 —
    **wan261/262 북 CSV는 freeze 명시 핀**으로 재현한다. `reentry=False`면 이 값은 무의미하다."""
    fill: harness.FillPreset | None = None
    """WAN-264(옵트인): 체결 렌즈. `None`(기본)이면 `harness.build_params()`가 채택 기본값
    (`baseline`, 관통 0bp)을 써 예전과 **비트 단위로 같다**. `pen_5bp` 등을 주면 후보 생성의
    `fill_penetration_bps`가 바뀌어 「스치듯 닿은 체결」이 후보 집합에서 빠진다(WAN-96/124).
    렌즈는 **후보 집합**을 바꾸므로 렌즈마다 후보 생성을 다시 해야 한다 — 비용(cost)은 반대로
    후보에 무관하고 시퀀싱에서만 적용되므로(BookCell = 「비용 미반영 원가 셋업」) 렌즈당 한 번
    생성한 후보를 여러 비용에 재사용할 수 있다(WAN-264 컴퓨트 최적화)."""
    stop_slippage_alpha: float = 0.0
    """WAN-276(옵트인): 시장가 손절 슬리피지 α. 0(기본)이면 손절가 그대로라 **비트 단위로
    같다**. α 스윕은 후보의 손절 청산가만 바꾸므로(진입·체결 집합 불변) 후보를 한 번 생성해
    `apply_stop_slippage`로 사후 변환하는 편이 싸다 — 이 인자는 그 변환의 검산용 직접 경로다."""
    limit_stop_nonfill: bool = False
    """WAN-276(옵트인): 지정가 손절 미체결(갭 관통 봉을 미체결 처리). False(기본)이면 예전과
    **비트 단위로 같다**. 손절 청산의 시각/홀드를 바꾸므로 후보 집합이 α 사후 변환처럼
    싸게 파생되지 않아 별도 생성이 필요하다."""
    short_enabled: bool = False
    """WAN-282(옵트인): 켜면 후보 생성이 베어리시 OB **숏**(기존 숏 경로, WAN-89/145/164)을
    같이 낸다 — 롱 모델의 좌우 반전 거울(근단 지정가 매도 · 존 위 무효화 손절 · 1.5R 익절 ·
    band 재진입 대칭). False(기본)이면 `params`에 `short_enabled`를 얹지 않아 예전과 **비트
    단위로 같다**(`ConfluenceParams()` 기본값 `short_enabled=False`). 측정용 숏이지 재활성화가
    아니다 — 판정은 롱-온리 북 vs 롱+숏 북(WAN-282)이 낸다."""
    seed: int = 0
    """WAN-293(옵트인): 체결 **탈락** 렌즈의 추첨 시드. `fill.dropout_rate > 0`인 렌즈
    (`drop_25`·`drop_50`·`pen_5bp_drop_50`)만 후보 생성의 `random.Random(seed)`에 흘러 들어가
    같은 렌즈를 여러 시드로 돌려 단일 시드의 운을 배제한다(WAN-96 관행 = 시드 5개 평균). 탈락이
    없는 렌즈(`baseline`·`pen_1bp`·`pen_5bp`)는 RNG를 만들지 않으므로 이 값과 무관하고, 기본
    `0`은 지금까지 `run_cell`이 쓰던 값이라 **비트 단위로 같다**(WAN-264 이하 CSV 무영향)."""
    cold_segments: bool = True
    """WAN-301(옵트인 컴퓨트 노브): `False`면 차가운 절단 구간(`is`/`oos`)의 탐지·후보 생성을
    **건너뛴다** — `full`·`oos_warm`만 쓰는 리포트(wan288/293/301 부류)에서 셀 비용의 큰 몫이
    쓰지도 않는 차가운 창 재탐지였다. 건너뛴 구간은 payload의 `candidates`/`funding`/`rows`에
    **키 자체가 없다**(빈 값으로 위장하지 않는다 — 요청하면 KeyError로 시끄럽게 죽는다,
    WAN-95 교훈). `True`(기본)면 예전과 **비트 단위로 같다**. `full`·`oos_warm` 산출은 이
    노브와 무관하다(같은 전체 창 후보의 경계 필터)."""
    repair_partial_bars: bool = False
    """WAN-327(옵트인, **비파괴**): 켜면 저장 상위TF 봉 중 **손상된 봉만** 그 구간 1분봉 합으로
    갈아끼운 사본으로 후보를 만든다(`harness.load_market_data(repair_htf_from_1m=True)`).
    「고치기 전후로 같은 좌표를 돌려 본다」(WAN-327 완료기준 2)를 위한 반사실이고 **DB는
    쓰지 않는다**. `False`(기본)면 저장 봉 그대로라 예전과 **비트 단위로 같다**."""
    confirmation_arms: tuple[str, ...] = ()
    """WAN-386 §0(옵트인): 「확인 진입」 팔 라벨들(`backtest.confirmation_arm.ARM_ORDER`).

    비어 있으면(기본) 이 경로를 아예 타지 않아 예전과 **비트 단위로 같다**. 값을 주면
    `observe_confirmation`이 자동으로 켜지고(트리거 관측 없이는 팔을 만들 수 없다), 각 팔 ×
    익절 배수마다 **진입 시각·진입가·주문 종류·청산만** 갈아끼운 후보를
    `CellPayload.arm_candidates`에 싣는다. **base 후보·재진입 후보·격리 성과 행은 불변**이다
    (팔은 별도 dict) — 그래서 켜도 채택 북 행이 비트 재현된다."""
    confirmation_multiples: tuple[float, ...] = ()
    """WAN-386 §0: 확인 팔의 익절 배수 점들. `confirmation_arms`와 짝이라 한쪽만 주면 거부한다."""
    confirmation_offset: float = ARM_C_OFFSET
    """WAN-386 §0: 팔 `C`의 고정 오프셋(기본 = WAN-383 §1 실측 1.026%)."""
    engine_check: bool = True
    """WAN-301(옵트인 컴퓨트 노브): `False`면 full 구간의 표준 경로 검산(`harness.run_once`
    재실행 — 후보 생성과 맞먹는 비용)을 생략한다. 검산은 배선이 같은 한 렌즈·시드 축에서
    반복할 필요가 없으므로, 렌즈 격자(WAN-301)는 기준(baseline) 팔만 켜고 나머지는 끈다.
    끄면 `CellRow.engine_*`이 `None`이라 `verify_cells`가 그 행을 건너뛴다(조용한 통과가
    아니라 검산 대상 축소 — 요약에 어느 팔이 검산됐는지 적는다). `True`(기본)면 예전과
    **비트 단위로 같다**."""


@dataclass(frozen=True)
class CellPayload:
    """칸 하나의 산출물 — 구간별 후보(북의 입력) + 격리 성과 행 + 따뜻한 경계."""

    symbol: str
    timeframe: str
    boundary_ms: int
    """따뜻한 평가 경계(WAN-166 `eval_boundary_ms` — 이 칸의 전체 창 앵커 기준).

    칸마다 마지막 봉 시각이 달라 경계가 칸 사이 1봉 미만으로 어긋날 수 있다 — 북은
    칸별 경계로 후보를 거른다(각 칸의 따뜻한 평가 창 = 그 칸의 차가운 OOS 창, WAN-166
    보장을 칸 단위로 보존)."""
    candidates: dict[str, tuple[_Candidate, ...]]
    """구간(`full`/`is`/`oos`) → 후보. `oos_warm`은 `full`을 경계로 걸러 만든다."""
    funding: dict[str, tuple[FundingRate, ...]]
    rows: tuple[CellRow, ...]
    reentry_candidates: dict[str, tuple[_Candidate, ...]] = field(default_factory=dict)
    """구간 → 「익절 후 존 내 재진입」 후보(WAN-261, 옵트인). 빈 dict(기본)이면 예전과
    비트 단위로 같다 — `_segment_cells(include_reentry=True)`에서만 base 후보와 합쳐 북에
    들어간다. `oos_warm`은 base와 같은 규약으로 `full`을 칸별 경계로 걸러 만든다."""
    arm_candidates: dict[str, dict[str, tuple[_Candidate, ...]]] = field(default_factory=dict)
    """WAN-386 §0(옵트인): `팔|배수` → 구간 → 후보. 빈 dict(기본)이면 예전과 비트 동일.

    base 후보와 재진입 후보를 **이미 합친** 목록이다(재무장 일정은 기준 팔의 것을 쓴다 —
    `backtest.confirmation_arm` 독스트링의 알려진 한계). `oos_warm`은 base와 **같은 규약**으로
    `full`을 칸별 경계로 걸러 만든다(`trigger_time`은 팔 사이에서 불변이라 경계가 같다)."""


def _isolated_metrics(
    candidates: Sequence[_Candidate],
    cfg: BacktestConfig,
    timeframe: str,
    rates: Sequence[FundingRate],
) -> tuple[int, float, float, float]:
    """격리(단일 포지션) 성과 — (거래수, 승률, 수익률, MDD)."""
    trades = [t for _, t in sequence_with_candidates(list(candidates), cfg, rates)]
    result = build_result_from_trades(trades, cfg, timeframe)
    m = result.metrics
    return m.num_trades, m.win_rate, m.total_return, m.max_drawdown


@dataclass(frozen=True)
class ReentryWindowContext:
    """재진입 파생이 쓰는 **창 단위 전처리** — 후보 집합과 무관한 것만 담는다 (WAN-378).

    존폭 문턱을 여러 점 돌 때(`run_cell_variants`) 재진입 파생을 문턱마다 다시 해야 하는데
    (부모 집합이 바뀌면 슬롯 점유·청산 시각이 바뀐다 — WAN-376 §1a), 그 안에서 **서브스텝
    빌드와 상위TF 배열 준비는 문턱과 무관**하다. 6년 1분봉이면 그 준비만 셀당 5초대이고
    9점 × 3구간이면 그냥 버리는 시간이 된다.

    ⚠️ **캐시가 아니라 인자다** — 창이 다르면(차가운 `is`/`oos`) 반드시 다른 컨텍스트여야
    하므로 호출부가 창마다 만들어 넘긴다. 안 넘기면 예전처럼 함수가 직접 만든다(비트 동일).
    """

    htf_ms: int
    htf_times: list[int]
    htf_closes: list[float]
    substeps: list[SubStep]
    substep_times: list[int]


def reentry_window_context(window: harness.MarketData, timeframe: str) -> ReentryWindowContext:
    """한 창의 재진입 전처리를 한 번만 만든다 — `reentry_candidates_for_window`와 같은 식."""
    htf_ms = timeframe_to_ms(timeframe)
    frame = _prepare_htf(window.htf_df)
    substeps = build_substeps(window.df_1m, htf_ms)
    return ReentryWindowContext(
        htf_ms=htf_ms,
        htf_times=[int(t) for t in frame["open_time"].astype("int64").tolist()],
        htf_closes=[float(v) for v in frame["close"].astype(float).tolist()],
        substeps=substeps,
        substep_times=[s.time for s in substeps],
    )


def reentry_candidates_for_window(
    window: harness.MarketData,
    candidates: Sequence[_Candidate],
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    timeframe: str,
    entry_rule: ReentryEntryRule = "band",
    partial_take_profit_r: float | None = None,
    partial_take_profit_fraction: float = 0.5,
    breakeven_after_partial: bool = False,
    no_same_step_tp: bool = False,
    no_same_step_tp_minutes: frozenset[int] | None = None,
    invalidation_cancel: InvalidationCancel | None = None,
    observe_macd: bool = False,
    observe_confirmation: bool = False,
    context: ReentryWindowContext | None = None,
) -> list[_Candidate]:
    """이 창의 base 후보에서 「익절 후 존 내 재진입」 후보를 만든다(WAN-261, 옵트인).

    ⚠️ `entry_rule` 기본값은 채택 규칙 `"band"`다(WAN-273 = WAN-305 기본 승격) — freeze
    시절 CSV 재현은 호출부가 명시 핀한다(현재 호출부는 전부 명시적으로 넘긴다).

    재진입은 base 후보 빌더가 만들지 않는 동작이라(존은 익절 후 소비) base 후보로는 표현되지
    않는다 — 그래서 base를 **단일 포지션으로 시퀀싱**해 실제 익절 거래를 얻은 뒤(WAN-228
    census와 같은 규약), 익절로 닫힌 존마다 지정가를 재무장해 재진입 후보를 낸다
    (`reentry_candidates`, WAN-228 로직 공유). 낸 후보는 청산이 확정돼 있어 북이 재시뮬 없이
    배치한다. base 후보·격리 성과는 건드리지 않는다(별도 반환).

    `entry_rule`은 `reentry_candidates`로 그대로 흐른다 — `"freeze"`면 첫 체결가를 얼려
    **기존 wan261/262 북 CSV가 비트 재현**되고(그 모듈들이 명시 핀), `"band"`(기본 = 채택)면
    재무장 순간의 봉내 라이브 밴드로 지정가를 재산정한다(WAN-267 리더 팔 = WAN-273 채택).
    base 후보 생성은 이 인자와 무관하므로 팔 사이에서 base는 불변이다."""
    if not candidates:
        return []
    paired = sequence_with_candidates(list(candidates), cfg, window.funding_rates)
    # WAN-378: 창 전처리는 후보 집합과 무관하다 — 문턱을 여러 점 도는 호출부가 한 번 만들어
    # 넘긴다(`context`). 안 넘기면 예전처럼 여기서 만들므로 **비트 단위로 같다**.
    ctx = context if context is not None else reentry_window_context(window, timeframe)
    htf_ms = ctx.htf_ms
    htf_times = ctx.htf_times
    htf_closes = ctx.htf_closes
    substeps = ctx.substeps
    substep_times = ctx.substep_times
    out: list[_Candidate] = []
    for cand, trade in paired:
        # WAN-323: 익절뿐 아니라 **본절 청산**도 재무장 대상이다 — 본절은 존 무효화 경계를
        # 건드리지 않았으므로 그 오더블록이 아직 살아 있다(사용자 지적 2026-08-18). 래더를
        # 안 켜면 `exit_at_breakeven`이 언제나 거짓이라 기존 북 CSV가 비트 재현된다.
        zone_alive = cand.reason is ExitReason.TAKE_PROFIT or cand.exit_at_breakeven
        if not zone_alive or cand.order_block is None:
            continue
        out.extend(
            _reentry_candidates_for_cand(
                cand,
                parent_exit_time=trade.exit_time,
                substeps=substeps,
                substep_times=substep_times,
                htf_times=htf_times,
                htf_closes=htf_closes,
                params=params,
                cfg=cfg,
                funding_rates=window.funding_rates,
                entry_rule=entry_rule,
                partial_take_profit_r=partial_take_profit_r,
                partial_take_profit_fraction=partial_take_profit_fraction,
                breakeven_after_partial=breakeven_after_partial,
                no_same_step_tp=no_same_step_tp,
                no_same_step_tp_minutes=no_same_step_tp_minutes,
                invalidation_cancel=invalidation_cancel,
                htf_ms=htf_ms,
                # WAN-372 관측 전용 — base 후보와 같은 규칙으로 재진입 거래에도 색을 단다.
                observe_macd=observe_macd,
                # WAN-383 §0 관측 전용 — 같은 이유로 재진입 거래에도 트리거를 잰다.
                observe_confirmation=observe_confirmation,
            )
        )
    return out


def arm_key(arm: str, multiple: float) -> str:
    """`팔|배수` 라벨 — 두 축을 한 dict 키로 (WAN-386). 배수는 소수 둘로 고정해 `1.5`/`1.50`이
    갈리지 않게 한다(`zone_width_label`이 문턱에서 쓴 규약과 같다)."""
    return f"{arm}|{multiple:.2f}"


def zone_width_label(threshold: float | None) -> str:
    """존폭 문턱을 표/딕셔너리 키로 — `None`(끔)과 숫자를 **문자로 가른다**.

    WAN-159가 못 박은 「끄기(`None`)와 미지정은 다르다」 규약을 라벨 층까지 끌고 온 것이다
    (숫자를 `float`로 키에 쓰면 `0.90`과 `0.9`가 갈리고 `None`은 키가 될 수 없다).
    """
    return "off" if threshold is None else f"{threshold:.2f}"


def run_cell(task: _Task, *, log: bool = True) -> CellPayload:
    """한 칸의 구간별 후보·격리 성과·검산을 낸다 — 채택 기본값 그대로(옛 핀 없음).

    후보 생성이 이 리포트의 유일한 무거운 연산이다. `full` 후보는 따뜻한 구간이
    재사용하고(경계 필터만), 차가운 `is`/`oos`는 잘린 창에서 탐지부터 다시 한다
    (존 재고 0에서 시작 — `harness.slice_market` 규약 그대로).

    📌 **속은 `run_cell_variants`다**(WAN-378) — 문턱 하나짜리 호출이라 예전과 **비트 단위로
    같다**. 두 경로로 갈라 두면 한쪽만 고쳐져 조용히 다른 숫자를 내므로(WAN-95/112/123 부류)
    단수는 복수의 특수 경우로만 존재한다.
    """
    thresholds = (task.post_filter_zone_width,)
    return run_cell_variants(task, thresholds, log=log)[zone_width_label(thresholds[0])]


def run_cell_variants(
    task: _Task, thresholds: Sequence[float | None], *, log: bool = True
) -> dict[str, CellPayload]:
    """한 칸을 **한 번만** 무겁게 돌고 존폭 문턱 여러 점의 payload를 함께 낸다 (WAN-378).

    WAN-376 §1a가 「필터 끔으로 만들고 밖에서 컷」이 성립함을 실데이터로 못 박았지만,
    `run_cells(post_filter_zone_width=)`는 문턱 **하나**만 받으므로 9점 격자를 돌리면 후보
    생성이 9번 반복된다(§1b가 피하려던 바로 그 3N패스). 이 함수가 그 지름길을 실제 절약으로
    바꾼다 — 무거운 것(1분봉 로딩 · 탐지 · 서브스텝 시뮬)은 **문턱과 무관**하므로 한 번 하고,
    문턱마다 다시 하는 것은 **컷 ＋ 재진입 파생 ＋ 격리 시퀀싱**뿐이다.

    🚨 **재진입 파생은 문턱마다 다시 해야 한다** — 재진입 후보는 base 후보의 per-cell
    시퀀싱에서 나오므로(WAN-261), 컷이 부모 집합을 바꾸면 슬롯 점유가 바뀌어 청산 시각이
    달라지고 재무장 시점도 달라진다. 컷을 재진입 **뒤에** 걸면 「빠진 셋업의 재진입이
    살아남는」 잡종이 된다(WAN-376 §1a가 급소로 지목한 자리). 그래서 이 루프의 순서는
    반드시 **컷 → 파생**이다.

    `thresholds`의 `None`은 「필터 끔」(컷 없음)이고 `run_cell`의 `post_filter_zone_width=None`
    과 **같은 뜻**이다. 같은 값을 두 번 주면 라벨이 겹치므로 거부한다.
    """
    if bool(task.confirmation_arms) != bool(task.confirmation_multiples):
        # 한쪽만 주면 팔이 조용히 0개가 되거나 배수가 무시된다 — 라벨만 남는 실패라 거부한다.
        raise ValueError(
            "confirmation_arms와 confirmation_multiples는 짝입니다(WAN-386) — "
            f"arms={task.confirmation_arms} multiples={task.confirmation_multiples}"
        )
    labels = [zone_width_label(t) for t in thresholds]
    if len(set(labels)) != len(labels):
        raise ValueError(f"존폭 문턱이 중복입니다: {labels} — 라벨이 겹치면 payload가 덮인다.")
    if task.engine_check and len(thresholds) > 1:
        # 검산은 `harness.run_once`를 **엔진 필터 설정 하나**로 다시 도는 것이라, 밖에서 컷한
        # 여러 문턱 중 어느 것과 대조할지가 정의되지 않는다. 조용히 아무 라벨에나 붙이면
        # 「검산했다」는 라벨만 남는다(WAN-194/318/321 부류) — 그래서 시끄럽게 죽는다.
        raise ValueError(
            "engine_check는 문턱 하나짜리 실행에서만 정의됩니다 "
            f"(문턱 {len(thresholds)}점) — 다중 문턱 실행은 engine_check=False로 부르세요."
        )

    market = harness.load_market_data(
        task.symbol,
        task.timeframe,
        start_ms=task.start_ms,
        end_ms=task.end_ms,
        need_1m=True,
        repair_htf_from_1m=task.repair_partial_bars,
    )
    if market.empty or market.df_1m.empty:
        raise ValueError(f"{task.symbol} {task.timeframe}: 데이터가 없습니다(창 확인).")
    # 인자 없음 = 채택 기본값(옛 핀 물려받기 금지 — 완료기준). `fill`(WAN-264, 옵트인)을 주면
    # 체결 렌즈만 갈아끼운다 — `None`이면 `build_params(fill=BASELINE_FILL)`과 같아 비트 재현.
    # `retap_mode`(WAN-388, 옵트인)는 `None`이면 `build_params`가 손대지 않아 채택
    # 기본값(`"every_tap"`)이고 비트 재현된다.
    params = (
        harness.build_params(
            take_profit_r=task.take_profit_r,
            max_zone_width_atr=task.max_zone_width_atr,
            retap_mode=task.retap_mode,
        )
        if task.fill is None
        else harness.build_params(
            fill=task.fill,
            seed=task.seed,
            take_profit_r=task.take_profit_r,
            max_zone_width_atr=task.max_zone_width_atr,
            retap_mode=task.retap_mode,
        )
    )
    if not task.bollinger:
        # WAN-366(옵트인): 볼린저를 끄면 진입가가 존 근단에 남는다(사다리 `L0`/`L1`).
        # 켜 두면(기본) 이 model_copy를 아예 타지 않아 예전과 비트 단위로 같다.
        params = params.model_copy(update={"deviation_filter": None})
    if task.zone_limit_ref is not None:
        # WAN-378(옵트인): 지정가 기준선(`proximal`/`mid`/`distal`). `None`(기본)이면 이
        # model_copy를 아예 타지 않아 예전과 비트 단위로 같다.
        params = params.model_copy(update={"zone_limit_ref": task.zone_limit_ref})
    # WAN-365: 취소 시점을 **파라미터에 실어** 재진입·엔진 본 진입이 같은 값을 읽게 한다.
    # `None`(기본)이면 채택 기본값 그대로라 인자를 안 준 실행이 채택 북이다.
    if task.invalidation_cancel is not None:
        params = params.model_copy(update={"invalidation_cancel": task.invalidation_cancel})
    if task.short_enabled:
        # WAN-282(옵트인): 베어리시 OB 숏을 후보에 같이 낸다. 끄면(기본) 이 model_copy를
        # 아예 타지 않아 예전과 비트 단위로 같다 — 롱 후보는 short_enabled와 무관하게 같은
        # 시그널에서 나온다(숏 게이트는 롱을 건드리지 않는다, zone_limit_backtest.py L883).
        params = params.model_copy(update={"short_enabled": True})
    # 유동성 한도(WAN-244/279): `task.adv_fraction`으로 후보 cfg의 상한을 **항상 명시 고정**한다.
    # 기본 `None`(측정 모듈)이면 상한을 끄고 — WAN-279가 채택 기본값을 0.005로 올린 뒤라 pin
    # 없이 build_config에 맡기면 조용히 켜진다(WAN-91/95/112 부류) — 옛 북 CSV가 비트 재현된다.
    # 이 저장소의 북 후보 생성은 전부 이 함수를 지나므로(wan180/261/264/269/271이 run_cells를
    # 공유) 여기 한 곳의 고정이 그 모듈들을 한꺼번에 상한-끔으로 보존한다. 채택 북
    # (`book_cli.run_book`)만 `UNSET`을 넘겨 채택 0.005를 물려받는다. wan244는 0.005를 넘겨 켠다.
    cfg = harness.build_config(
        task.timeframe,
        max_notional_adv_fraction=task.adv_fraction,
        # WAN-370: 익절 청산 유동성도 같은 이유로 **항상 명시 고정**한다(기본 = 옛 taker).
        take_profit_liquidity=task.take_profit_liquidity,
    )

    candidates: dict[str, dict[str, tuple[_Candidate, ...]]] = {ell: {} for ell in labels}
    reentry: dict[str, dict[str, tuple[_Candidate, ...]]] = {ell: {} for ell in labels}
    arms: dict[str, dict[str, dict[str, tuple[_Candidate, ...]]]] = {ell: {} for ell in labels}
    rows: dict[str, list[CellRow]] = {ell: [] for ell in labels}
    funding: dict[str, tuple[FundingRate, ...]] = {}

    boundary = harness.eval_boundary_ms(market, WARM_OOS_SEGMENT)
    assert boundary is not None  # WARM_OOS_SEGMENT는 평가 경계를 항상 가진다.

    segment_specs: list[tuple[str, Segment | None]] = [(SEGMENT_FULL, None)]
    if task.cold_segments:
        segment_specs.extend([(SEGMENT_IS, IS_SEGMENT), (SEGMENT_OOS, OOS_SEGMENT)])
    for segment_name, segment in segment_specs:
        window = market if segment is None else harness.slice_market(market, segment)
        # WAN-388(옵트인): 존 병합은 **탐지** 파라미터라 팔마다 다시 탐지해야 한다.
        # `combine_obs=False`(기본)면 `OrderBlockParams()`와 같은 객체라 비트 동일.
        ob_result = harness.detect_order_blocks(
            window, OrderBlockParams(combine_obs=task.combine_obs)
        )
        generated, _stats = build_zone_limit_candidates(
            window.htf_df,
            window.df_1m,
            task.timeframe,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
            stop_slippage_alpha=task.stop_slippage_alpha,
            limit_stop_nonfill=task.limit_stop_nonfill,
            partial_take_profit_r=task.partial_take_profit_r,
            partial_take_profit_fraction=task.partial_take_profit_fraction,
            breakeven_after_partial=task.breakeven_after_partial,
            no_same_step_tp=task.no_same_step_tp,
            no_same_step_tp_minutes=task.no_same_step_tp_minutes or None,
            invalidation_cancel=task.invalidation_cancel,
            observe_zone_width_atr=(
                task.observe_zone_width_atr or any(t is not None for t in thresholds)
            ),
            observe_macd=task.observe_macd,
            # WAN-386: **확인** 팔을 요청하면 관측이 자동으로 켜진다 — 트리거 없이는 그 팔을
            # 만들 수 없는데 인자를 잊으면 팔이 조용히 0개가 된다(WAN-345 부류).
            #
            # 🚨 **기준 팔(`ARM_BASE`)은 예외다** — `derive_arm_candidates`가 그 팔에서
            # `cand.confirmation`을 **아예 읽지 않는다**(트리거 판독은 `taker` 팔 전용이고
            # 기준 팔은 후보 자기 체결을 그대로 쓴다). 그런데 옛 조건은 `bool(arms)`라
            # 기준 팔만 요청해도 관측을 켰고, 그 관측은 체결 셋업마다 「탭 → 존 무효화」를
            # 한 번 더 훑는 **가장 비싼 패스 중 하나**다(WAN-383 §0). 배수 축만 쓰는 격자
            # (WAN-381 · WAN-394 §1)가 쓰지도 않는 값을 위해 그 비용을 통째로 물고 있었다.
            #
            # ⚠️ 끄는 것이 **수치를 바꾸지 않는다**: `confirmation`은 순수 관측 필드라
            # 후보의 진입·청산·손절·목표 어디에도 안 들어가고, 배치는 그것을 `PlacedSetup`에
            # 옮겨 싣기만 한다. 실데이터로 비트 대조해 확인한다(회귀 테스트가 고정).
            observe_confirmation=task.observe_confirmation
            or any(arm != ARM_BASE for arm in task.confirmation_arms),
        )
        funding[segment_name] = tuple(window.funding_rates)
        engine_return: float | None = None
        engine_trades: int | None = None
        if segment is None and task.engine_check:
            # 검산(WAN-164 패턴): 같은 창을 표준 경로로 다시 돌려 배선 실수를 비트로 잡는다.
            outcome = harness.run_once(window, params=params, cfg=cfg, order_block_result=ob_result)
            engine_return = outcome.result.metrics.total_return
            engine_trades = outcome.result.metrics.num_trades

        # WAN-378: 문턱마다 재진입을 다시 파생해야 하지만(부모 집합이 바뀐다) 창 전처리는
        # 문턱과 무관하다 — 한 번 만들어 돌려 쓴다. 문턱이 하나뿐이거나 재진입이 꺼져 있으면
        # 만들지 않아 예전과 같은 호출 수다(비트 동일).
        reentry_ctx = (
            reentry_window_context(window, task.timeframe)
            if (task.reentry and len(thresholds) > 1) or task.confirmation_arms
            else None
        )
        for threshold, label in zip(thresholds, labels, strict=True):
            if threshold is None:
                cands = list(generated)
            else:
                # WAN-376 §1a: 「필터 끔으로 만들고 밖에서 컷」. 판정 불가(`None`)는 엔진이
                # 기각하는 부류라 여기서도 버린다 — 그래야 두 팔이 같은 집합을 겨눈다.
                cands = [
                    c
                    for c in generated
                    if c.zone_width_atr is not None and c.zone_width_atr <= threshold
                ]
            candidates[label][segment_name] = tuple(cands)
            if task.reentry:
                # WAN-261(옵트인): base 후보에서 「익절 후 존 내 재진입」 후보를 별도로 만든다.
                # base 후보·격리 성과 행은 건드리지 않으므로 끄면 예전과 비트 단위로 같다.
                reentry[label][segment_name] = tuple(
                    reentry_candidates_for_window(
                        window,
                        cands,
                        params=params,
                        cfg=cfg,
                        timeframe=task.timeframe,
                        entry_rule=task.reentry_entry_rule,
                        partial_take_profit_r=task.partial_take_profit_r,
                        partial_take_profit_fraction=task.partial_take_profit_fraction,
                        breakeven_after_partial=task.breakeven_after_partial,
                        no_same_step_tp=task.no_same_step_tp,
                        no_same_step_tp_minutes=task.no_same_step_tp_minutes or None,
                        invalidation_cancel=task.invalidation_cancel,
                        observe_macd=task.observe_macd,
                        observe_confirmation=task.observe_confirmation,
                        context=reentry_ctx,
                    )
                )

            if task.confirmation_arms:
                # WAN-386 §0: 팔 변환은 **후보를 새로 만들지 않는다** — base + 재진입을 합친
                # 그 목록의 진입·청산만 갈아끼운다(셋업은 팔 사이에서 불변).
                assert reentry_ctx is not None
                merged = [*cands, *reentry[label].get(segment_name, ())]
                for arm in task.confirmation_arms:
                    derived = derive_arm_candidates(
                        merged,
                        arm=arm,
                        multiples=task.confirmation_multiples,
                        substeps=reentry_ctx.substeps,
                        substep_times=reentry_ctx.substep_times,
                        offset=task.confirmation_offset,
                    )
                    for multiple, arm_cands in derived.items():
                        arms[label].setdefault(arm_key(arm, multiple), {})[segment_name] = tuple(
                            arm_cands
                        )

            num_trades, win_rate, total_return, mdd = _isolated_metrics(
                cands, cfg, task.timeframe, window.funding_rates
            )
            rows[label].append(
                CellRow(
                    symbol=task.symbol,
                    timeframe=task.timeframe,
                    segment=segment_name,
                    num_candidates=len(cands),
                    num_trades=num_trades,
                    win_rate=win_rate,
                    total_return=total_return,
                    max_drawdown=mdd,
                    engine_total_return=engine_return,
                    engine_num_trades=engine_trades,
                )
            )

    payloads: dict[str, CellPayload] = {}
    for label in labels:
        # 따뜻한 구간(oos_warm): 전 창 후보를 경계로 걸러(straddle (b) — 워밍업 셋업은 배치조차
        # 안 함) 신선한 초기자본으로 격리 시퀀싱 — `run_zone_limit_backtest_verbose(eval_from_ms=)`
        # 와 같은 규약이다.
        warm_cands = tuple(c for c in candidates[label][SEGMENT_FULL] if c.trigger_time >= boundary)
        num_trades, win_rate, total_return, mdd = _isolated_metrics(
            warm_cands, cfg, task.timeframe, funding[SEGMENT_FULL]
        )
        rows[label].append(
            CellRow(
                symbol=task.symbol,
                timeframe=task.timeframe,
                segment=SEGMENT_OOS_WARM,
                num_candidates=len(warm_cands),
                num_trades=num_trades,
                win_rate=win_rate,
                total_return=total_return,
                max_drawdown=mdd,
            )
        )
        if log:
            full_row = rows[label][0]
            suffix = "" if len(labels) == 1 else f" [문턱 {label}]"
            print(
                f"[wan169] {task.symbol} {task.timeframe}{suffix}: "
                f"full 후보 {full_row.num_candidates} · 거래 {full_row.num_trades} · "
                f"수익 {full_row.total_return * 100:.2f}%",
                flush=True,
            )
        payloads[label] = CellPayload(
            symbol=task.symbol,
            timeframe=task.timeframe,
            boundary_ms=boundary,
            candidates=candidates[label],
            funding=funding,
            rows=tuple(rows[label]),
            reentry_candidates=reentry[label],
            arm_candidates=arms[label],
        )
    return payloads


def _run_task_logged(task: _Task) -> CellPayload:
    return run_cell(task, log=True)


@dataclass(frozen=True)
class _MultiTask:
    """워커에 보내는 「한 칸 × 문턱 여러 점」 단위 — 피클 가능해야 하므로 최상위 타입이다."""

    task: _Task
    thresholds: tuple[float | None, ...]


def _run_variants_logged(multi: _MultiTask) -> dict[str, CellPayload]:
    return run_cell_variants(multi.task, multi.thresholds, log=True)


def run_cells(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    start: str,
    end: str,
    jobs: int = 1,
    adv_fraction: harness.AdvCapArg = harness.LEGACY_MAX_NOTIONAL_ADV_FRACTION,
    take_profit_liquidity: Liquidity = harness.LEGACY_TAKE_PROFIT_LIQUIDITY,
    combine_obs: bool = False,
    retap_mode: str | None = None,
    reentry: bool = True,
    reentry_entry_rule: ReentryEntryRule = "band",
    fill: harness.FillPreset | None = None,
    stop_slippage_alpha: float = 0.0,
    limit_stop_nonfill: bool = False,
    short_enabled: bool = False,
    bollinger: bool = True,
    zone_limit_ref: ZoneLimitRef | None = None,
    max_zone_width_atr: harness.ZoneWidthArg = harness.UNSET,
    seed: int = 0,
    cold_segments: bool = True,
    engine_check: bool = True,
    take_profit_r: float | None = None,
    partial_take_profit_r: float | None = None,
    partial_take_profit_fraction: float = 0.5,
    breakeven_after_partial: bool = False,
    repair_partial_bars: bool = False,
    no_same_step_tp: bool = False,
    no_same_step_tp_minutes: Mapping[tuple[str, str], frozenset[int]] | None = None,
    invalidation_cancel: InvalidationCancel | None = None,
    observe_zone_width_atr: bool = False,
    observe_macd: bool = False,
    observe_confirmation: bool = False,
    confirmation_arms: Sequence[str] = (),
    confirmation_multiples: Sequence[float] = (),
    confirmation_offset: float = ARM_C_OFFSET,
    post_filter_zone_width: float | None = None,
    payload_cache: PayloadCache | None = None,
) -> list[CellPayload]:
    """전 칸을 돈다. `jobs`는 성능 노브이지 결과 축이 아니다(WAN-121).

    `adv_fraction`(유동성 한도, WAN-244/279)은 후보 cfg의 상한을 **명시 고정**한다 —
    `None`(기본, 측정 모듈)이면 상한을 끄고 ADV를 계산조차 하지 않아 옛 북 CSV와 비트 단위로
    같다(wan180/261/264/269/271 무영향). `UNSET`이면 채택 기본값(0.005)을 물려받아 후보에
    룩어헤드-안전 `adv_usd`를 싣는다(채택 북 `book_cli.run_book`의 옵트인 경로). `float`이면 그
    프랙션으로 켠다(wan244 측정).

    `take_profit_liquidity`(익절 청산 유동성, WAN-370)도 같은 중앙화다 — `taker`(기본)면 옛
    비용 회계라 옛 북 CSV가 비트 재현되고, 채택 값(`maker`)은 채택 북·재산출 대상만 명시로
    넘긴다. 비용은 후보 집합을 안 바꾸므로 이 축은 per-cell 격리 행과 손익에만 나타난다.

    ⚠️ **`reentry` 기본값은 켬(band)이다(WAN-305)** — 채택 규칙(WAN-273 재진입 · 페이퍼
    러너 WAN-274)과 같은 선상이 「아무것도 안 하면」 나오게 한다. 각 칸의 payload에 「익절 후
    존 내 재진입」 후보를 함께 싣는다 — base 후보·격리 성과 행은 불변이라(재진입은 별도
    dict) `reentry=False`(명시 핀)면 WAN-273 이전 북과 비트 단위로 같다. 옛 CSV를 결론에
    박아 둔 리포트 모듈은 반드시 `reentry=False`로 핀한다(WAN-305 §1 — wan169/180/244/276/
    288/293/300/301 부류).

    `reentry_entry_rule`은 재진입 후보의 재무장 지정가 규칙 — 기본 `"band"`(봉내 라이브 밴드
    재산정, WAN-273 채택 = WAN-305 기본 승격). `"freeze"`(첫 체결가 고정)는 옵트인 존치 —
    **wan261/262 CSV는 freeze 명시 핀**으로 재현한다. base 후보는 이 값과 무관해 팔 사이에서
    불변이다.

    `fill`(WAN-264, 옵트인)을 주면 후보 생성의 체결 렌즈를 바꾼다 — None(기본)이면 채택
    기본값(`baseline`)이라 비트 단위로 같다. 렌즈는 후보 집합을 바꾸므로 렌즈마다 다시
    생성해야 하지만, 비용은 후보에 무관하니 렌즈당 한 번 생성해 여러 비용에 재사용한다.

    `stop_slippage_alpha`·`limit_stop_nonfill`(WAN-276, 옵트인)은 손절 체결 모델을 보수화한다
    — 둘 다 기본(0 · False)이면 예전과 비트 단위로 같다(WAN-276 손절 갭-체결 민감도 측정용).

    `short_enabled`(WAN-282, 옵트인)를 켜면 후보 생성이 베어리시 OB 숏을 같이 낸다(롱 모델의
    거울) — 끄면(기본) `params`에 얹지 않아 예전과 비트 단위로 같다. 롱+숏 북 측정용이다.

    `zone_limit_ref`(WAN-378, 옵트인)은 지정가를 걸 **존 내 기준선**이다 — `None`(기본)이면
    채택 기본값(`"proximal"`)이라 비트 단위로 같고, `"mid"`는 존 중앙 진입(손절폭 절반)이다.
    볼린저가 켜져 있으면 밴드가 진입가를 덮어쓰므로(WAN-95) 이 축은 볼린저를 끈 팔의 것이다.

    `bollinger`·`max_zone_width_atr`(WAN-366, 옵트인)은 **후보 집합 사다리**의 두 축이다 —
    전자는 볼린저 진입가 재산정을, 후자는 존폭 필터를 끈다. 기본값(`True` · `UNSET`)이면
    채택 기본값 그대로라 비트 단위로 같다. ⚠️ 손절폭 가드는 여기 없다 — 그건 **사이징**
    축이라 후보를 안 바꾸고 `build_book_rows(min_stop_distance_fraction=)`가 배치에서 건다.

    `seed`(WAN-293, 옵트인)는 체결 **탈락** 렌즈의 추첨 시드다 — `fill.dropout_rate > 0`인
    렌즈만 후보 생성 RNG에 흘러 든다. 기본 `0`은 예전 값이라 비트 단위로 같고, 탈락 없는
    렌즈에서는 이 값이 무관하다(같은 렌즈를 시드 5개로 돌려 평균하는 WAN-96 관행에 쓴다).

    `cold_segments`·`engine_check`(WAN-301, 옵트인 컴퓨트 노브)는 각각 차가운 절단 구간
    (`is`/`oos`) 생성과 full 표준 경로 검산을 끈다 — 렌즈 × 시드 격자처럼 `full`/`oos_warm`만
    쓰는 실행에서 셀 비용을 절반 아래로 줄인다. 둘 다 기본 `True`면 예전과 비트 단위로 같다
    (자세한 규약은 `_Task` 필드 docstring).

    `no_same_step_tp`(WAN-336, 옵트인)를 켜면 base·재진입 후보 **양쪽**이 「진입 스텝 익절
    금지」 반사실로 생성된다 — 끄면(기본) 예전과 비트 단위로 같다. ⚠️ 다른 옵트인 훅과 달리
    **후보 집합 자체가 바뀔 수 있다**(익절이 미뤄지면 그 셋업이 다른 청산을 타고, 북에서는
    슬롯 점유 시간이 달라져 뒤따르는 후보까지 갈린다) — 그래서 이건 오버라이드가 아니라 **팔**
    이고, 기준선 팔과 나란히 놓고 **차이의 폭**으로만 읽는다.

    `observe_confirmation`(WAN-383 §0, 옵트인)은 체결된 셋업마다 **확인 진입 세 팔의 트리거
    시각**을 후보에 실어 준다(base·재진입 양쪽). 순수 관측이라 켜도 후보·손익이 안 움직인다.

    `confirmation_arms`·`confirmation_multiples`(WAN-386 §0, 옵트인)를 주면 그 관측 위에서
    **실제로 늦게 진입하는 팔**을 만들어 `CellPayload.arm_candidates`에 싣는다(`팔|배수` →
    구간 → 후보). base 후보·재진입 후보·격리 성과 행은 **불변**이라(팔은 별도 dict) 켜도 채택
    북 행이 비트 재현된다. 팔은 후보를 **새로 만들지 않고** 진입 시각·진입가·주문 종류(테이커)·
    청산만 갈아끼운다 — 셋업이 팔 사이에서 같아야 「진입 시점의 값어치」가 격리된다. 둘은
    짝이라 한쪽만 주면 거부한다.

    `observe_macd`(WAN-372, 옵트인)는 후보에 **체결 순간의 MACD 히스토그램**을 실어 준다 —
    base 후보와 재진입 후보 양쪽에 같은 규칙이 걸리고, 순수 관측이라 켜도 후보·손익이 하나도
    안 움직인다(끄면 비트 재현).

    `observe_zone_width_atr`·`post_filter_zone_width`(WAN-376, 옵트인)는 존폭 축의 **관측**과
    **지름길 팔**이다. 전자는 후보에 「존폭 ÷ ATR14」를 실을 뿐이라 켜도 아무 수치가 안 움직이고,
    후자는 엔진 필터를 **끈 채** 만든 후보에서 그 비율로 컷해 재진입 파생·격리 성과·북 입력을
    다시 만든다(컷은 재진입 파생 **앞**이다 — 뒤에 걸면 「빠진 셋업의 재진입이 살아남는」
    잡종이다). 둘 다 기본값이면 예전과 **비트 단위로 같다**. ⚠️ 후자는 `max_zone_width_atr`과
    동시에 못 주고(이중 필터), **탈락 렌즈**(`fill.dropout_rate > 0`)와도 못 쓴다 — 그쪽은
    추첨 순서가 후보 집합에 의존해 지름길이 **원리적으로** 깨진다.

    `repair_partial_bars`(WAN-327, 옵트인 · **비파괴**)를 켜면 저장 상위TF 손상 봉을 1분봉
    합으로 갈아끼운 사본에서 후보를 만든다 — 부분 봉의 백테 영향 크기를 재는 반사실이다.
    끄면(기본) 저장 봉 그대로라 예전과 비트 단위로 같다. **DB는 쓰지 않는다.**

    ⚠️ **`invalidation_cancel` 기본값은 채택(인과 `"bar_close"`)이다**(WAN-365 = WAN-305
    원칙) — 「아무것도 안 하면」 페이퍼와 같은 선상이 나온다. 옛 북 CSV를 결론에 박아 둔
    모듈은 `harness.LEGACY_INVALIDATION_CANCEL`(= `"bar_open"` = 소급 취소)을 **명시로**
    넘겨 그 시절 엔진에 고정한다. base 후보와 재진입 후보 **양쪽에** 걸린다 — 한쪽만 걸면
    잡종 엔진이다(WAN-345 선례).

    `combine_obs`·`retap_mode`(WAN-388, 옵트인)는 **후보 집합을 바꾸는 두 축**이다 — 전자는
    겹치는 오더블록을 하나로 접고(탐지 층), 후자는 `"once"`로 존당 첫 탭만 남긴다(시그널 층).
    둘 다 기본값(`False` · `None`)이면 채택 기본값이라 **비트 단위로 같다**. 🚨 전자는 **탐지**
    파라미터라 값마다 오더블록을 다시 탐지한다(WAN-149) — 팔끼리 payload를 공유하지 말 것.
    🚨 후자는 `reentry`(WAN-273)와 **다른 축이다**: 재탭을 꺼도 「익절 후 재무장」은 그대로
    돈다. 둘을 섞어 「존에 한 번만」으로 읽지 말 것.

    `no_same_step_tp_minutes`(WAN-359, 옵트인)는 그 반사실을 **전부가 아니라 「틱이 지지하지
    않는 그 분들」에만** 거는 표적 팔이다 — 칸 `(정규화 심볼, TF)`마다 1분 `open_time` 집합을
    준다. `None`(기본)이면 비트 단위로 예전과 같다. 🚨 **아무 칸과도 안 맞는 키가 있으면
    거부한다** — 심볼 표기가 어긋나면 아무것도 안 걸린 채 「표적 팔」 라벨만 붙어 기준선과
    같은 수가 나오고, 그러면 「보간이 맞았다」가 근거 없이 만들어진다(WAN-91/95/112/123/159가
    반복해 경계한 자리 · `_loo_rows`의 같은 가드와 같은 부류).

    `payload_cache`(WAN-394 §0, 옵트인)를 주면 이 함수가 **미스 칸만** 계산하고 나머지는
    디스크에서 읽는다(`backtest.payload_cache`). 캐시 키는 **`_Task` 그 자체 + 엔진·러너 소스
    지문**이라 *「payload를 바꾸는 것은 전부 키에 있다」*가 구조적으로 참이고, 반대로 손절폭
    가드·재진입 **배치**·복리는 `_Task`에 아예 없어 **바꿔도 히트한다**(그것이 캐시의 존재
    이유다). `None`(기본)이면 이 경로를 아예 타지 않아 예전과 **비트 단위로 같다**.
    """
    targeted: Mapping[tuple[str, str], frozenset[int]] = no_same_step_tp_minutes or {}
    if no_same_step_tp and targeted:
        raise ValueError(
            "no_same_step_tp(전부)와 no_same_step_tp_minutes(표적)는 같은 축의 두 값이라 "
            "함께 줄 수 없습니다(WAN-359)."
        )
    if post_filter_zone_width is not None and fill is not None and fill.dropout_rate > 0:
        # WAN-376 §1a: 탈락 렌즈에서는 지름길이 **원리적으로** 깨진다 — 추첨 순서가 「어느
        # 셋업이 체결됐나」에 달려 있어, 넓은 셋업을 안 만들면 뒤 셋업의 난수가 통째로
        # 밀린다(`baseline`은 `dropout_rate=0`이라 난수를 뽑지도 않는다). 조용히 돌면
        # 「지름길이 성립한다」는 표가 거짓이 된다.
        raise ValueError(
            f"post_filter_zone_width는 탈락 렌즈({fill.name!r}, dropout_rate="
            f"{fill.dropout_rate})와 함께 쓸 수 없습니다 — 추첨 순서가 후보 집합에 "
            "의존해 지름길이 원리적으로 깨집니다(WAN-376)."
        )
    if post_filter_zone_width is not None and max_zone_width_atr is not None:
        # WAN-376 §1a: 지름길 팔은 **필터를 끈 채** 만들어야 한다. 켠 채로 또 컷하면
        # 이중 필터라 「지름길이 성립한다」는 판정이 거짓이 된다(WAN-159 규약과 같은 부류).
        raise ValueError(
            "post_filter_zone_width는 엔진 필터를 끈 채(max_zone_width_atr=None) 씁니다 — "
            f"지금 max_zone_width_atr={max_zone_width_atr!r}이라 이중 필터가 됩니다(WAN-376)."
        )
    cells = {(harness.normalize_symbol(s), tf) for s in symbols for tf in timeframes}
    unmatched = sorted(key for key in targeted if key not in cells)
    if unmatched:
        raise AssertionError(
            f"no_same_step_tp_minutes에 이 실행의 칸과 안 맞는 키가 있습니다: {unmatched} — "
            "심볼 표기(정규화)나 TF를 확인하세요(안 걸린 채 라벨만 붙는 것을 막습니다)."
        )
    tasks = [
        _Task(
            symbol=harness.normalize_symbol(symbol),
            timeframe=timeframe,
            start_ms=parse_date_ms(start),
            end_ms=parse_date_ms(end),
            adv_fraction=adv_fraction,
            take_profit_liquidity=take_profit_liquidity,
            combine_obs=combine_obs,
            retap_mode=retap_mode,
            reentry=reentry,
            reentry_entry_rule=reentry_entry_rule,
            fill=fill,
            stop_slippage_alpha=stop_slippage_alpha,
            limit_stop_nonfill=limit_stop_nonfill,
            short_enabled=short_enabled,
            bollinger=bollinger,
            zone_limit_ref=zone_limit_ref,
            max_zone_width_atr=max_zone_width_atr,
            seed=seed,
            cold_segments=cold_segments,
            engine_check=engine_check,
            take_profit_r=take_profit_r,
            partial_take_profit_r=partial_take_profit_r,
            partial_take_profit_fraction=partial_take_profit_fraction,
            breakeven_after_partial=breakeven_after_partial,
            repair_partial_bars=repair_partial_bars,
            no_same_step_tp=no_same_step_tp,
            no_same_step_tp_minutes=targeted.get(
                (harness.normalize_symbol(symbol), timeframe), frozenset()
            ),
            invalidation_cancel=invalidation_cancel,
            observe_zone_width_atr=observe_zone_width_atr,
            observe_macd=observe_macd,
            observe_confirmation=observe_confirmation,
            confirmation_arms=tuple(confirmation_arms),
            confirmation_multiples=tuple(confirmation_multiples),
            confirmation_offset=confirmation_offset,
            post_filter_zone_width=post_filter_zone_width,
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    return _run_tasks(tasks, jobs=jobs, cache=payload_cache)


def _run_tasks(
    tasks: Sequence[_Task], *, jobs: int, cache: PayloadCache | None = None
) -> list[CellPayload]:
    """칸들을 돈다 — 캐시가 있으면 **미스만** 돌고 히트는 디스크에서 온다(WAN-394 §0).

    🚨 캐시는 `run_cell` **바깥**에 있다 — 워커에 들어가는 것은 예전과 같은 `_Task`뿐이고
    무거운 함수는 한 글자도 안 바뀐다. 그래서 「캐시를 끄면 비트 재현」이 배선의 성질이지
    지켜야 할 규칙이 아니다(캐시가 있어도 미스 칸은 **글자 그대로 같은 경로**를 돈다).

    🚨 **미스를 조용히 메우지 않는다** — 몇 칸이 히트/미스인지 **계산 전에** 찍는다
    (WAN-335 관행: 조용히 느려지면 「왜 안 끝나지」가 반복된다).
    """
    if cache is None:
        if jobs <= 1 or len(tasks) <= 1:
            return [run_cell(task) for task in tasks]
        with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as executor:
            return list(executor.map(_run_task_logged, tasks))

    hit_count, miss_count = cache.census(tasks)
    print(
        f"[payload-cache] {cache.directory} ({cache.revision}): "
        f"히트 {hit_count}칸 · 미스 {miss_count}칸 — 미스만 계산합니다.",
        flush=True,
    )
    out: list[CellPayload | None] = [cache.load(task) for task in tasks]
    pending = [
        (i, task) for i, (task, got) in enumerate(zip(tasks, out, strict=True)) if got is None
    ]
    if pending:
        missing = [task for _i, task in pending]
        if jobs <= 1 or len(missing) <= 1:
            _drain(pending, (run_cell(task) for task in missing), out, cache)
        else:
            with ProcessPoolExecutor(max_workers=min(jobs, len(missing))) as executor:
                _drain(pending, executor.map(_run_task_logged, missing), out, cache)
    print(f"[payload-cache] {cache.summary()}", flush=True)
    return [payload for payload in out if payload is not None]


def _drain(
    pending: Sequence[tuple[int, _Task]],
    computed: Iterable[CellPayload],
    out: list[CellPayload | None],
    cache: PayloadCache,
) -> None:
    """🚨 **칸이 끝나는 대로 적재한다** — 다 모아서 저장하면 안 된다.

    `executor.map`은 제출 순서대로 결과를 내는 **지연 이터레이터**다. 그걸 `list()`로 먼저
    비우면 47칸 중 46칸을 돌고 마지막에 죽었을 때 **아무것도 안 남는다** — 4시간짜리 실행에서
    그건 캐시가 있으나 마나라는 뜻이다. 하나씩 받아 바로 적재하면 죽은 자리까지가 남고 다음
    실행이 그만큼 건너뛴다(WAN-394 §0).
    """
    for (index, task), payload in zip(pending, computed, strict=True):
        cache.store(task, payload)
        out[index] = payload


def run_cells_multi(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    thresholds: Sequence[float | None],
    start: str,
    end: str,
    jobs: int = 1,
    adv_fraction: harness.AdvCapArg = harness.LEGACY_MAX_NOTIONAL_ADV_FRACTION,
    take_profit_liquidity: Liquidity = harness.LEGACY_TAKE_PROFIT_LIQUIDITY,
    reentry: bool = True,
    reentry_entry_rule: ReentryEntryRule = "band",
    bollinger: bool = True,
    zone_limit_ref: ZoneLimitRef | None = None,
    cold_segments: bool = True,
) -> dict[str, list[CellPayload]]:
    """존폭 문턱 **여러 점**의 칸 payload를 한 번의 무거운 패스로 낸다 (WAN-378 §1b).

    `run_cells`가 문턱 하나짜리 진입점이라면 이쪽은 격자용이다 — 엔진 필터를 **끈 채**
    후보를 한 번 만들고(WAN-376 §1a가 성립을 못 박은 지름길) 문턱마다 컷 ＋ 재진입 파생만
    다시 한다. 반환은 `문턱 라벨 → 칸 payload 목록`이고 라벨은 `zone_width_label`이다.

    🚨 **엔진 필터는 항상 꺼진다**(`max_zone_width_atr=None`) — 켜 두고 밖에서 또 컷하면
    이중 필터라 라벨이 거짓이 된다(WAN-159 규약). 그래서 이 함수에는 그 인자가 아예 없다.

    🚨 **검산(`engine_check`)도 없다** — `harness.run_once`는 엔진 필터 설정 하나로 도는데
    이 함수는 여러 문턱을 동시에 내므로 어느 라벨과 대조할지가 정의되지 않는다. 배선 검산은
    호출부가 채택 좌표 팔을 **표준 경로로 따로 돌려** 하는 것이 옳다(WAN-378 완료기준 6).

    ⚠️ `run_cells`와 달리 체결 렌즈·손절 보수화·래더 같은 축은 열지 않았다 — 이 격자가 안
    쓰는 축을 열면 「지름길이 원리적으로 깨지는 조합」(탈락 렌즈 등)을 여기서 다시 막아야
    한다. 필요해지면 그때 열되 그 검사와 함께 연다.
    """
    labels = [zone_width_label(t) for t in thresholds]
    if len(set(labels)) != len(labels):
        raise ValueError(f"존폭 문턱이 중복입니다: {labels}")
    multi = [
        _MultiTask(
            task=_Task(
                symbol=harness.normalize_symbol(symbol),
                timeframe=timeframe,
                start_ms=parse_date_ms(start),
                end_ms=parse_date_ms(end),
                adv_fraction=adv_fraction,
                take_profit_liquidity=take_profit_liquidity,
                reentry=reentry,
                reentry_entry_rule=reentry_entry_rule,
                bollinger=bollinger,
                zone_limit_ref=zone_limit_ref,
                # 엔진 필터는 끈다 — 컷은 밖에서 문턱마다 건다(위 독스트링).
                max_zone_width_atr=None,
                cold_segments=cold_segments,
                engine_check=False,
                observe_zone_width_atr=True,
            ),
            thresholds=tuple(thresholds),
        )
        for symbol in symbols
        for timeframe in timeframes
    ]
    if jobs <= 1 or len(multi) <= 1:
        results = [run_cell_variants(m.task, m.thresholds) for m in multi]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(multi))) as executor:
            results = list(executor.map(_run_variants_logged, multi))
    return {label: [r[label] for r in results] for label in labels}


# --------------------------------------------------------------------------- #
# 북 격자 (가벼운 시퀀싱 — 후보 재사용)
# --------------------------------------------------------------------------- #


def _reentry_for_segment(payload: CellPayload, segment: str) -> list[_Candidate]:
    """이 구간에 얹을 재진입 후보(WAN-261) — base와 같은 규약으로 버킷한다.

    `oos_warm`은 base처럼 full 재진입 후보를 칸별 경계로 거른다(재진입의 `trigger_time`은
    진입 시각이라 base와 같은 straddle (b) 경계식을 탄다). payload에 재진입이 없으면 빈
    리스트라 include_reentry=True여도 base만 남는다(비트 재현).
    """
    if not payload.reentry_candidates:
        return []
    if segment == SEGMENT_OOS_WARM:
        return [
            c
            for c in payload.reentry_candidates.get(SEGMENT_FULL, ())
            if c.trigger_time >= payload.boundary_ms
        ]
    return list(payload.reentry_candidates.get(segment, ()))


def _segment_cells(
    payloads: Sequence[CellPayload],
    segment: str,
    exclude_symbol: str,
    *,
    include_reentry: bool = True,
) -> list[BookCell]:
    """이 구간의 북 입력 칸들. `oos_warm`은 full 후보를 칸별 경계로 거른다(straddle (b)).

    ⚠️ **`include_reentry` 기본값은 켬이다(WAN-305)** — 채택 북(WAN-273 재진입)이 「아무것도
    안 하면」 나오게 한다. 켜면 각 칸의 재진입 후보(payload에 실려 있을 때만)를 base 후보와
    **합쳐** 북에 넣는다 — 북 시퀀서가 칸당 1포지션·공유 자본·명목 상한으로 재탭과 재진입을
    한 지갑에서 함께 배치한다. payload에 재진입이 없으면(핀된 `run_cells(reentry=False)`)
    켜져 있어도 base만 남아 비트 재현된다. `False`는 옛 CSV 재현용 **명시 핀**이다(WAN-305).
    """
    cells: list[BookCell] = []
    for payload in payloads:
        if exclude_symbol and _short(payload.symbol) == exclude_symbol:
            continue
        if segment == SEGMENT_OOS_WARM:
            cands: list[_Candidate] = [
                c for c in payload.candidates[SEGMENT_FULL] if c.trigger_time >= payload.boundary_ms
            ]
            rates = payload.funding[SEGMENT_FULL]
        else:
            cands = list(payload.candidates[segment])
            rates = payload.funding[segment]
        if include_reentry:
            cands = [*cands, *_reentry_for_segment(payload, segment)]
        cells.append(
            BookCell(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                candidates=cands,
                funding_rates=rates,
            )
        )
    return cells


def _book_config(base_cfg: BacktestConfig, sizing_mode: str, scope_cells: int) -> BacktestConfig:
    """팔의 사이징 모드를 실은 설정. 배수는 북(`apply_book_leverage`)이 얹는다.

    `fixed_notional`의 분수는 **시드 분할** = `1/스코프 칸수`다(사용자의 「1/N 시드」를
    칸 축으로 옮긴 것) — 12칸이 전부 열리면 총 명목이 자본 × N이 되어 `risk_pct` 팔과
    같은 천장을 쓴다. leave-one-out 행도 이 분수를 **바꾸지 않는다**(같은 전략에서
    종목의 거래만 빼는 것이지 사이징을 다시 고르는 게 아니다).
    """
    if sizing_mode == "risk_pct":
        return base_cfg
    assert base_cfg.risk_sizing is not None
    sizing = base_cfg.risk_sizing.model_copy(
        update={"sizing_mode": "fixed_notional", "notional_fraction": 1.0 / scope_cells}
    )
    return base_cfg.model_copy(update={"risk_sizing": sizing})


def _scope_payloads(payloads: Sequence[CellPayload], scope: str) -> list[CellPayload]:
    if scope == "both":
        return list(payloads)
    return [p for p in payloads if p.timeframe == scope]


def build_book_rows(
    payloads: Sequence[CellPayload],
    *,
    take_profit_liquidity: Liquidity = harness.LEGACY_TAKE_PROFIT_LIQUIDITY,
) -> list[BookRow]:
    """격자 전체의 북 행 + 격리(현행) 대조 행.

    `take_profit_liquidity`(WAN-370)는 배치 회계의 익절 청산 유동성이다 — 기본이 옛 값
    (`taker`)이라 이 함수를 쓰는 북 측정 모듈의 CSV가 비트 재현되고, 재산출 대상만 채택 값을
    명시로 넘긴다. ⚠️ `run_cells`에 넘긴 값과 **같아야** 한 표가 한 회계다.
    """
    scopes = [*MAIN_TIMEFRAMES, "both"]
    symbols = sorted({_short(p.symbol) for p in payloads})
    cell_rows_by_key = {
        (row.symbol, row.timeframe, row.segment): row for p in payloads for row in p.rows
    }
    rows: list[BookRow] = []
    base_cfg = harness.build_config(
        BOOK_ANNUALIZATION_TF, take_profit_liquidity=take_profit_liquidity
    )

    for scope in scopes:
        scoped = _scope_payloads(payloads, scope)
        if not scoped:
            continue  # 이 TF의 칸이 없다(부분 실행) — 빈 스코프는 행을 내지 않는다.
        if scope == "both" and len({p.timeframe for p in scoped}) < 2:
            continue  # TF가 하나뿐이면 both는 그 TF 스코프의 복제라 내지 않는다.
        scope_cells = len(scoped)
        for segment in SEGMENTS:
            for exclude in ["", *symbols]:
                kept = [p for p in scoped if not exclude or _short(p.symbol) != exclude]
                # 격리(현행) 대조 행 — 칸 평균(이 저장소의 심볼평균 관행을 칸 축으로).
                iso = [cell_rows_by_key[(p.symbol, p.timeframe, segment)] for p in kept]
                rows.append(
                    BookRow(
                        scope=scope,
                        arm="isolated",
                        sizing_mode="risk_pct",
                        multiple=1.0,
                        segment=segment,
                        exclude_symbol=exclude,
                        num_cells=len(iso),
                        num_trades=sum(r.num_trades for r in iso),
                        win_rate=_mean([r.win_rate for r in iso]),
                        total_return=_mean([r.total_return for r in iso]),
                        max_drawdown=_mean([r.max_drawdown for r in iso]),
                    )
                )
                for sizing_mode in SIZING_MODES:
                    cfg = _book_config(base_cfg, sizing_mode, scope_cells)
                    # WAN-305 명시 핀: wan169 격자 CSV는 재진입 이전(WAN-261 옵트인 도입
                    # 전) 북의 동결 스냅샷이다 — 기본값이 켬으로 바뀐 뒤에도 비트 재현.
                    cells = _segment_cells(scoped, segment, exclude, include_reentry=False)
                    for multiple in MULTIPLES:
                        outcome = run_leverage_book(
                            # WAN-213 명시 핀: 이 리포트는 결합(combined)만 측정했다 —
                            # 클래스 기본값이 채택 북(cap_only 5배)으로 옮겨간 뒤 CSV가
                            # 조용히 그 값으로 다시 돌지 않게 combined를 못 박는다.
                            cells,
                            cfg,
                            LeverageBookParams(
                                leverage_multiple=multiple, leverage_mode="combined"
                            ),
                        )
                        result = build_result_from_trades(
                            outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
                        )
                        m = result.metrics
                        stats = outcome.stats
                        rows.append(
                            BookRow(
                                scope=scope,
                                arm="book",
                                sizing_mode=sizing_mode,
                                multiple=multiple,
                                segment=segment,
                                exclude_symbol=exclude,
                                num_cells=len(cells),
                                num_trades=m.num_trades,
                                win_rate=m.win_rate,
                                total_return=m.total_return,
                                max_drawdown=m.max_drawdown,
                                peak_concurrency=stats.peak_concurrency,
                                max_concurrent_risk=stats.max_concurrent_risk_ratio,
                                max_open_notional_ratio=stats.max_open_notional_ratio,
                                liquidation_events=len(stats.liquidations),
                                clamped_entries=stats.clamped_entries,
                                skipped_cell_busy=stats.skipped_cell_busy,
                                skipped_notional=stats.skipped_notional,
                            )
                        )
    return rows


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #


def verify_cells(rows: Sequence[CellRow]) -> tuple[str, float]:
    """full 구간 격리 행 ↔ 표준 경로(`harness.run_once`) 대조 — (문장, 최대 절대차).

    코드가 일치·잡음·불일치를 다르게 찍는다(WAN-151/161 패턴 — 조용한 통과 금지).
    """
    diffs: list[float] = []
    for row in rows:
        if row.segment != SEGMENT_FULL or row.engine_total_return is None:
            continue
        diffs.append(abs(row.total_return - row.engine_total_return))
        if row.engine_num_trades is not None and row.engine_num_trades != row.num_trades:
            return (
                f"🚨 **불일치** — {row.symbol} {row.timeframe} full 거래 수가 표준 경로와 "
                f"다릅니다({row.num_trades} vs {row.engine_num_trades}). 배선 오류다.",
                float("inf"),
            )
    if not diffs:
        return ("🚨 **검산 불가** — full 구간 엔진 대조 값이 없습니다.", float("inf"))
    worst = max(diffs)
    if worst == 0.0:
        return (
            f"✅ **일치** — full 격리 {len(diffs)}칸의 `total_return`이 표준 경로"
            "(`harness.run_once`)와 **비트 단위로 같다**(최대 절대차 0.00e+00).",
            worst,
        )
    if worst < 1e-12:
        return (
            f"✅ 일치(부동소수 끝자리) — 최대 절대차 {worst:.2e} (< 1e-12), "
            f"{len(diffs)}칸 전부 표준 경로와 같은 수다.",
            worst,
        )
    return (
        f"🚨 **불일치** — full 격리 성과가 표준 경로와 최대 {worst:.2e} 차이. 배선 오류다.",
        worst,
    )


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def pick(
    rows: Sequence[BookRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    sizing_mode: str = "risk_pct",
    multiple: float | None = None,
    exclude: str = "",
) -> list[BookRow]:
    out = [
        r
        for r in rows
        if r.scope == scope
        and r.arm == arm
        and r.segment == segment
        and r.sizing_mode == sizing_mode
        and r.exclude_symbol == exclude
        and (multiple is None or r.multiple == multiple)
    ]
    return sorted(out, key=lambda r: r.multiple)


def _one(
    rows: Sequence[BookRow],
    *,
    scope: str,
    arm: str,
    segment: str,
    sizing_mode: str = "risk_pct",
    multiple: float | None = None,
    exclude: str = "",
) -> BookRow:
    found = pick(
        rows,
        scope=scope,
        arm=arm,
        segment=segment,
        sizing_mode=sizing_mode,
        multiple=multiple,
        exclude=exclude,
    )
    if len(found) != 1:
        key = (scope, arm, segment, sizing_mode, multiple, exclude)
        raise ValueError(f"행이 정확히 1개여야 합니다: {key} → {len(found)}개")
    return found[0]


def verdict(rows: Sequence[BookRow]) -> str:
    """완료기준의 판정 문장 — 배수 N이 위험조정으로 「할 만한가」(사용자 지시 서식).

    자: 주 수치(`oos_warm`)와 스트레스(`oos`)에서, 각 스코프의 북(`risk_pct`)이 배수
    N>1로 **수익/MDD를 1배보다 올리는가**(청산 트리거 0 조건). 숫자는 전부 행에서
    계산한다 — 문장에 박으면 재실행 뒤 리포트가 거짓말을 한다(WAN-164 패턴).
    """
    present = {r.scope for r in rows}
    sub: list[tuple[str, str, bool, bool]] = []  # (scope, segment, improved, raw_up)
    for scope in [s for s in [*MAIN_TIMEFRAMES, "both"] if s in present]:
        for segment in (SEGMENT_OOS_WARM, SEGMENT_OOS):
            base = _one(rows, scope=scope, arm="book", segment=segment, multiple=1.0)
            others = [
                r
                for r in pick(rows, scope=scope, arm="book", segment=segment)
                if r.multiple > 1.0 and r.sample_ok
            ]
            if not others or not base.sample_ok:
                continue
            base_rr = base.return_over_mdd or 0.0
            improved = any(
                (r.return_over_mdd or 0.0) > base_rr and (r.liquidation_events or 0) == 0
                for r in others
            )
            raw_up = max(r.total_return for r in others) > base.total_return
            sub.append((scope, segment, improved, raw_up))
    if not sub:
        return "**판정 불가** — 표본 게이트(20건)를 넘는 셀이 없다."

    coord_scope = "both" if "both" in present else next(iter(sorted(present)))
    both_warm = _one(rows, scope=coord_scope, arm="book", segment=SEGMENT_OOS_WARM, multiple=1.0)
    best = max(
        (
            r
            for r in pick(rows, scope=coord_scope, arm="book", segment=SEGMENT_OOS_WARM)
            if r.multiple > 1.0
        ),
        key=lambda r: r.return_over_mdd or float("-inf"),
    )
    coords = (
        f"{coord_scope}·oos_warm 기준 배수 {best.multiple:g}에서 수익 "
        f"{(best.total_return - both_warm.total_return) * 100:+.2f}%p"
        f"({both_warm.total_return * 100:.2f}% → {best.total_return * 100:.2f}%), "
        f"최대 동시 리스크 {(best.max_concurrent_risk or 0.0) * 100:.2f}%, 수익/MDD "
        f"{both_warm.return_over_mdd or 0.0:.2f} → {best.return_over_mdd or 0.0:.2f}, "
        f"청산 트리거 {best.liquidation_events}건"
    )
    if all(improved for _, _, improved, _ in sub):
        return (
            f"**(a) 위험조정 개선 — 배수가 수익/MDD를 올린다.** {coords}. 스코프·구간 "
            "전부에서 어떤 배수 N>1이 1배의 수익/MDD를 청산 트리거 없이 이겼다."
        )
    if all(not improved for _, _, improved, _ in sub):
        raw = all(raw_up for _, _, _, raw_up in sub)
        tail = "원수익은 배수대로 커지지만" if raw else "원수익 우위조차 구간에 갈리고"
        return (
            f"**(b) 원수익만 개선 — 위험조정 우위는 없다.** {coords}. {tail} 수익/MDD는 "
            "어느 스코프·구간에서도 1배를 넘지 못했다 — 배수는 위험의 모양만 키운다."
        )
    split = " · ".join(
        f"{scope}/{segment}={'개선' if improved else '아님'}" for scope, segment, improved, _ in sub
    )
    return (
        f"**(c) 스코프·구간에 갈린다.** {coords}. 세부: {split}. 하나의 배수로 전부를 "
        "좋게 할 수 없다 — 채택은 이 갈림을 알고 내리는 사용자 결정이다."
    )


# --------------------------------------------------------------------------- #
# 프레임 왕복
# --------------------------------------------------------------------------- #


def cells_to_frame(rows: Sequence[CellRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(CellRow.model_fields))


def grid_to_frame(rows: Sequence[BookRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(BookRow.model_fields))


def cells_from_csv(path: Path) -> list[CellRow]:
    # `keep_default_na=False`: `exclude_symbol=""` 같은 빈 문자열 축이 NaN으로 둔갑하지
    # 않게 한다. 선택 숫자 열의 빈 칸은 검증기가 `""` → None으로 되돌린다.
    frame = pd.read_csv(path, keep_default_na=False)
    return [CellRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def grid_from_csv(path: Path) -> list[BookRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [BookRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _book_table(rows: Sequence[BookRow], scope: str, segment: str, sizing_mode: str) -> list[str]:
    lines = [
        "| 팔 | 배수 | 수익률 | MDD | 수익/MDD | 최대동시리스크 | 최대명목/자본 "
        "| 청산 | 거래 | 최대칸 |",
        "| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    iso = _one(rows, scope=scope, arm="isolated", segment=segment)
    lines.append(
        f"| 격리(현행) | — | {_pct(iso.total_return)} | {_pct(iso.max_drawdown)} | "
        f"{_rr(iso.return_over_mdd)} | — | — | — | {iso.num_trades} | — |"
    )
    for row in pick(rows, scope=scope, arm="book", segment=segment, sizing_mode=sizing_mode):
        gate = "" if row.sample_ok else " ⚠️"
        lines.append(
            f"| 북 | {row.multiple:g} | {_pct(row.total_return)} | {_pct(row.max_drawdown)} | "
            f"{_rr(row.return_over_mdd)} | {_pct(row.max_concurrent_risk or 0.0)} | "
            f"{(row.max_open_notional_ratio or 0.0):.2f} | {row.liquidation_events} | "
            f"{row.num_trades}{gate} | {row.peak_concurrency} |"
        )
    return lines


def _loo_lines(rows: Sequence[BookRow], scope: str, segment: str) -> list[str]:
    symbols = sorted({r.exclude_symbol for r in rows if r.exclude_symbol})
    lines = [
        "| 배수 | 전체 | " + " | ".join(f"−{s}" for s in symbols) + " |",
        "| --: | --: | " + " | ".join("--:" for _ in symbols) + " |",
    ]
    for multiple in MULTIPLES:
        base = _one(rows, scope=scope, arm="book", segment=segment, multiple=multiple)
        cells = [
            _one(
                rows,
                scope=scope,
                arm="book",
                segment=segment,
                multiple=multiple,
                exclude=s,
            ).total_return
            for s in symbols
        ]
        lines.append(
            f"| {multiple:g} | {_pct(base.total_return)} | "
            + " | ".join(_pct(v) for v in cells)
            + " |"
        )
    return lines


def _compounding_caveat(rows: Sequence[BookRow], scope: str) -> str:
    """수익/MDD가 배수에 대해 기계적으로 커지는 성질의 경고 — 숫자는 행에서 계산한다.

    거래당 기대값이 양(+)이면 총수익은 배수 N에 대해 지수적으로 커지는데 MDD는 100%로
    유계다 — 그래서 이 자(총수익/MDD)는 복리 구간이 길수록 N과 함께 **산수적으로** 오르는
    경향이 있다. (a)를 「높은 배수가 안전하다」로 읽으면 안 되는 이유를 판정 옆에 붙인다.
    """
    base = _one(rows, scope=scope, arm="book", segment=SEGMENT_OOS_WARM, multiple=MULTIPLES[0])
    top = _one(rows, scope=scope, arm="book", segment=SEGMENT_OOS_WARM, multiple=MULTIPLES[-1])
    top_cold = _one(rows, scope=scope, arm="book", segment=SEGMENT_OOS, multiple=MULTIPLES[-1])
    return (
        "🚨 **(a)를 「높은 배수가 안전하다」로 읽지 말 것 — 수익/MDD의 N-단조 상승은 상당"
        "부분 복리 산수다.** 거래당 기대값이 양(+)인 백테스트를 수백 번 복리로 돌리면 "
        "총수익은 N에 대해 지수적으로 커지고 MDD는 100%로 유계라, 총수익/MDD는 배수를 "
        "올릴수록 기계적으로 커지는 경향이 있다(그 자를 주 판정으로 정한 것은 사용자 지시고, "
        "이 경고는 그 자의 성질 기록이다). 결정에 실질적인 질문은 낙폭 그 자체다 — "
        f"{scope}·oos_warm에서 MDD가 1배 {_pct(base.max_drawdown)} → "
        f"{MULTIPLES[-1]:g}배 {_pct(top.max_drawdown)}"
        f"(차가운 oos는 {_pct(top_cold.max_drawdown)})로, **그 낙폭을 견딜 수 있는가**가 "
        "배수 선택의 실제 내용이다."
    )


def build_summary_markdown(
    cell_rows: Sequence[CellRow],
    book_rows: Sequence[BookRow],
    *,
    cells_csv: Path,
    grid_csv: Path,
) -> str:
    verify_line, _ = verify_cells(cell_rows)
    present = {r.scope for r in book_rows}
    main_scope = "both" if "both" in present else next(iter(sorted(present)))
    lines = [
        "# WAN-169 — 타임프레임·종목 가로지르는 레버리지 북: 손익·위험 측정",
        "",
        "**성격** 측정 전용(옵트인 엔진 `backtest.leverage_book` 위의 격자). 진입 단위 = "
        "**(종목, TF) 칸**, 칸 안 1포지션 · 칸 간 동시 허용 · 한 지갑 공유(사용자 정의 "
        "2026-07-22). **레버리지 N배 = 매 거래 사이징 N배**(리스크 1% → N% — cap-only가 "
        "아니다, 사용자 확정). 렌즈 `baseline` 단독(WAN-128) · 못 박은 창"
        f"({DEFAULT_START}~{DEFAULT_END}) · 채택 기본값 그대로(옛 핀 없음) · **기본값·토대·"
        "사이징 기본값 불변**(`ALPHABLOCK_LIVE_TRADING=false` 유지).",
        "",
        "**구간** `oos_warm`(따뜻한 연속 OOS, **주 수치**) + `oos`(차가운 절단, 과최적화 "
        "스트레스) 병기 — WAN-166 정본 규약. **straddle 회계 = (b) 배치 안 함**(사용자 결정): "
        "워밍업에 탭이 나 평가 경계를 넘어 사는 포지션은 평가 초입의 칸·자본·레버리지 자리를 "
        "점유하지 않는다(`docs/decisions/wan169.md`).",
        "",
        f"재현: `uv run python -m backtest.wan169_leverage_book --jobs 6` (요약만: `--from-csv`). "
        f"원자료: `{cells_csv}`(칸별 격리 성과·검산) · `{grid_csv}`(북 격자).",
        "",
        "## 0. 검산 — 이 모듈의 배선이 채택 엔진과 같은 수를 내는가",
        "",
        verify_line,
        "",
        "추가로 칸 하나짜리 북 ≡ 채택 단일 포지션 시퀀서의 비트 일치는 "
        "`tests/test_leverage_book.py`가 동작으로 고정한다.",
        "",
        f"## 1. 본 판정 — {main_scope}"
        + ("(15m+1h 12칸 = 사용자 정의의 실제 북)" if main_scope == "both" else "(부분 실행)")
        + " × `risk_pct`(현행 사이징 × N)",
        "",
        "### oos_warm (주 수치)",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS_WARM, "risk_pct"),
        "",
        "### oos (차가운 스트레스)",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS, "risk_pct"),
        "",
        "### full · is (맥락)",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_FULL, "risk_pct"),
        "",
        *_book_table(book_rows, main_scope, SEGMENT_IS, "risk_pct"),
        "",
        "⚠️ **격리(현행) 행과 북 행은 자가 다르다** — 격리는 칸마다 독립 자본을 준 수익률의 "
        "**칸 평균**(이 저장소의 심볼평균 관행)이고, 북은 한 지갑의 단일 자본곡선이다. 격리 "
        "12칸의 자본 합은 북의 12배이므로 두 행을 「같은 돈의 두 성적」으로 읽지 말 것 — "
        "격리 행은 「칸들이 각자였다면」의 기준선일 뿐이다. 북 1배가 격리 평균보다 훨씬 큰 "
        "주된 이유도 배수가 아니라 **거래 빈도**다: 한 지갑이 전 칸의 셋업을 순차로 다 받아 "
        "복리 횟수가 칸 하나의 몇 배가 된다.",
        "",
        "🚨 **북의 수익률은 수백~수천 거래의 복리 값이다 — 달성 가능 성과로 인용 금지.** "
        "full·is에서 조 단위 %까지 커지는 것은 복리 산수이지 새 정보가 아니며, 거래당 "
        "기대값의 작은 낙관(체결 가정·비용)이 그 횟수만큼 지수적으로 증폭된 값이다. 이 표에서 "
        "결정에 실질적인 열은 수익률의 절대 크기가 아니라 **MDD · 최대 동시 리스크 · 청산 "
        "트리거**다.",
        "",
    ]
    for tf in [t for t in MAIN_TIMEFRAMES if t in present and t != main_scope]:
        lines += [
            f"## 2. TF 단면 — {tf}(6칸) × `risk_pct`",
            "",
            "### oos_warm (주)",
            "",
            *_book_table(book_rows, tf, SEGMENT_OOS_WARM, "risk_pct"),
            "",
            "### oos (스트레스)",
            "",
            *_book_table(book_rows, tf, SEGMENT_OOS, "risk_pct"),
            "",
        ]
    lines += [
        "## 3. 사이징 축 — `fixed_notional`(시드 분할: 명목 = 자본 × N/칸수)",
        "",
        "WAN-108 2안의 오늘 엔진 판이다. ⚠️ 옛 WAN-108의 「2안이 진다」는 **옛 엔진** 값이라 "
        "결론으로 재인용 금지 — 아래가 첫 측정이다.",
        "",
        f"### {main_scope} · oos_warm",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS_WARM, "fixed_notional"),
        "",
        f"### {main_scope} · oos",
        "",
        *_book_table(book_rows, main_scope, SEGMENT_OOS, "fixed_notional"),
        "",
        "## 4. leave-one-out — 종목 편중 (`risk_pct` · 북)",
        "",
        f"### {main_scope} · oos_warm",
        "",
        *_loo_lines(book_rows, main_scope, SEGMENT_OOS_WARM),
        "",
        f"### {main_scope} · oos",
        "",
        *_loo_lines(book_rows, main_scope, SEGMENT_OOS),
        "",
        "## 판정 — 리스크가 배수만큼 오른다는 가정 아래, 그럼에도 할 만한가",
        "",
        verdict(book_rows),
        "",
        f"판정 자: 각 스코프(15m·1h·both) × 구간(oos_warm·oos)에서 북(`risk_pct`)의 어떤 배수 "
        "N>1이 1배의 수익/MDD를 **청산 트리거 0으로** 이기는가. 전부 그렇다 → (a) · 전부 "
        f"아니다 → (b) · 갈린다 → (c). 표본 게이트 {MIN_TRADES}건(WAN-84) 미달 셀은 판정에서 "
        "뺀다.",
        "",
        "⚠️ **청산 트리거는 최악 가정 검사다**(WAN-103 결정 4 — 열린 포지션 전부 동시 손절 시 "
        "유지증거금 미달) — 0건이 「그 배수는 안전하다」가 아니라 「이 보수적 상한 검사로는 "
        "마진콜 사거리 밖」이라는 뜻이다. 순차 손실의 복리 낙폭은 MDD 열이 담당한다.",
        "",
        _compounding_caveat(book_rows, main_scope),
        "",
        "⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151)은 이 표로 뒤집히지 않는다** — 레버리지는 "
        "위험의 모양만 바꾸지 알파를 만들지 않는다(WAN-90 계열). 배수·사이징을 기본값으로 "
        "올리는 것은 재-베이스라인 = 사용자 결정이다.",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-169 레버리지 북 측정")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(MAIN_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=1, help="(심볼, TF) 칸 단위 병렬 워커 수")
    parser.add_argument("--out-cells", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--out-grid", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 저장된 CSV에서 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    out_cells = Path(args.out_cells)
    out_grid = Path(args.out_grid)
    out_md = Path(args.out_md)

    if args.from_csv:
        cell_rows = cells_from_csv(out_cells)
        book_rows = grid_from_csv(out_grid)
        print(f"[wan169] CSV 로드 — 칸 {len(cell_rows)}행 · 격자 {len(book_rows)}행 (재실행 없음)")
    else:
        payloads = run_cells(
            tuple(s.strip() for s in str(args.symbols).split(",") if s.strip()),
            tuple(t.strip() for t in str(args.tf).split(",") if t.strip()),
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            # WAN-305 명시 핀: wan169 리포트 CSV는 재진입 이전 북의 동결 스냅샷이다.
            reentry=False,
            # WAN-365 명시 핀: 그 CSV는 **소급 취소** 시절의 기록이기도 하다.
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
            # WAN-384 명시 핀: 그 CSV는 **존폭 필터 1.28이 켜진** 판이기도 하다(2026-07-22
            # 산출 = WAN-159 채택 뒤). 이 인자의 기본값은 `UNSET`(= 채택 기본값 물려받기)이라
            # 안 주면 필터 꺼진 오늘 엔진으로 조용히 다시 돌아 본문과 어긋난다.
            max_zone_width_atr=harness.LEGACY_ZONE_WIDTH_FILTER_ON,
        )
        cell_rows = [row for p in payloads for row in p.rows]
        book_rows = build_book_rows(payloads)
        out_cells.parent.mkdir(parents=True, exist_ok=True)
        cells_to_frame(cell_rows).to_csv(out_cells, index=False)
        grid_to_frame(book_rows).to_csv(out_grid, index=False)
        print(f"[wan169] 칸 {len(cell_rows)}행 → {out_cells}")
        print(f"[wan169] 격자 {len(book_rows)}행 → {out_grid}")

    verify_line, worst = verify_cells(cell_rows)
    print(f"[wan169] 검산: {verify_line}")
    if not math.isfinite(worst) or worst >= 1e-12:
        print("[wan169] 🚨 검산 실패 — 요약을 내기 전에 배선을 확인하세요.")
        return 1

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(cell_rows, book_rows, cells_csv=out_cells, grid_csv=out_grid),
        encoding="utf-8",
    )
    print(f"[wan169] summary → {out_md}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
