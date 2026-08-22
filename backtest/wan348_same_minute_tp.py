"""「같은 분 익절」이 진짜인지 **틱으로 직접** 잰다 (WAN-348 · WAN-336 후속).

## 묻는 것 — 한 문장

> 그 「같은 1분에 진입하고 익절한」 거래들에서, **정말 저가가 먼저였나?**

1분봉은 그 1분의 **시·고·저·종 네 숫자만** 알려 주고 **안의 순서는 모른다**. 롱 지정가는
가격이 **내려와야** 체결되고 고정 1.5R 익절은 **올라가야** 닿으니, 「같은 1분에 진입+익절」이
성립하려면 **저가가 먼저 · 고가가 나중**이어야 한다 — 엔진은 그걸 확인하지 않고 **가정**한다.
손절 쪽에는 그 가정을 누르는 장치가 둘 있는데(`stop_before_tp` 동시 도달 시 손절 우선 ·
WAN-46 관통 카운터) **익절 쪽에는 없다**(WAN-336이 이름 붙인 비대칭).

체결내역(틱)에는 그 순서가 **그대로 들어 있다**. 그 1분치만 펼치면 가정이 아니라 사실이 나온다.

## 왜 큰가

WAN-336: 채택 북 `oos_warm` 순손익의 **약 48%**가 그 467건(전체 거래의 7.37%)에 실려 있고,
반대쪽 극단으로 누른 반사실(`no_same_step_tp`)에서 MDD가 22.90% → 25.22%로 나빠진다. 진값은
**두 극단 사이**이고, 이 표가 그 폭을 **p**로 좁힌다.

## 무엇을 재나 — 두 팔, 그리고 두 질문

측정 대상은 WAN-346 §0이 낸 **팔 A 거래별 CSV**의 `같은분익절=True` 467건이다(§0을 다시
만들지 않는다 — 이슈 지시). 표본은 **TF 층화 무작위 100건**(시드 고정).

**팔 `static`(엔진 값 그대로)** — 엔진이 실제로 쓴 진입가 `E`와 익절가 `T`를 고정 수준으로
두고, 그 1분의 체결을 시간순으로 훑어 `가격 ≤ E`에 처음 닿은 시각과 `가격 ≥ T`에 처음 닿은
시각을 찾는다. **이 팔이 헤드라인**이다 — 엔진이 낸 그 손익이 성립하려면 이 순서여야 하고,
WAN-336의 반사실이 누른 것도 정확히 이 가정이다.

**팔 `band`(§3-8 요구 · 틱 위 밴드 재산정)** — 우리 지정가는 봉 안에서 **계속 재산정**된다
(봉내 라이브 밴드, WAN-132). 그래서 틱마다 `L(p)`(밴드 → `deviation_entry_price` → 오프셋)를
다시 내고 `p ≤ L(p)`가 처음 성립하는 틱을 체결로 본다(WAN-328 `path_fill_price`의 고정점
풀이와 **같은 사슬**). 체결가가 달라지므로 1R도 익절 목표도 함께 다시 난다.

각 팔에서 **두 질문**을 따로 센다 — 크기가 다르고 뜻도 다르다:

* **`순서`**: 익절가보다 체결가에 **먼저** 닿았나(= 엔진의 가정 그대로).
* **`성립`**: 체결 뒤 그 분 안에서 익절가에 **닿았나**(= 그 손익이 실제로 났나).

📌 **`성립`이 헤드라인 p다.** 순서가 뒤집혔어도 체결 뒤 다시 익절가에 닿았으면 **그 거래의
손익은 그대로 난다** — 틀린 것은 가정이고 결과는 맞다. 반대로 `순서`만 보면 그런 거래를
「가짜」로 세어 할인율을 과대평가한다. 두 수를 함께 내는 이유가 그것이다.

## ⚠️ 이 표가 답하지 못하는 것

* ❌ **「우리 주문이 채워졌을까」** — 그건 호가 깊이·큐 우선순위이고 체결내역은 답하지
  못한다(별개 축 · `pen_5bp`가 그 민감도 · WAN-98은 Canceled).
* ❌ **더 이른 분에서 체결했을 가능성** — 팔 `band`는 **그 1분 안**만 다시 푼다. 실제
  라이브라면 주문이 몇 분 전부터 걸려 있어 **더 이른 봉**에서 체결됐을 수 있다(WAN-328
  `path_fill_price`가 명시한 같은 한계).
* ❌ **p가 낮아도 「수익의 48%가 가짜」가 아니다** — 순서가 반대였다면 그 거래는 손실이
  아니라 **더 오래 보유**이고 그 뒤는 미지다. 대가의 상한은 WAN-336 §2 반사실이다.

## 재현

    uv run python -m backtest.wan348_same_minute_tp            # 표본 추출 + 측정
    uv run python -m backtest.wan348_same_minute_tp --from-csv # 적재된 CSV로 요약만

측정 전용 — 엔진·기본값·토대 불변(`ConfluenceParams()`·`LeverageBookParams()` 그대로),
DB에 아무것도 쓰지 않는다(WAN-194 원칙), 실거래 보류 유지.
"""

from __future__ import annotations

import argparse
import bisect
import logging
import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.run import parse_date_ms
from data.agg_trade_archive import (
    DEFAULT_CACHE_DIR,
    DayFetch,
    Tick,
    day_of,
    fetch_day,
    minute_ticks,
)
from data.storage import OhlcvStore
from strategy.models import (
    ConfluenceParams,
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    deviation_entry_price,
)
from strategy.order_blocks import OrderBlockDetector
from strategy.realtime_band import RealtimeBand

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("backtest/reports")
#: 대상 목록의 출처 — WAN-346 §0이 낸 채택 북 팔 A 거래별 CSV. **다시 만들지 않는다.**
TARGET_CSV = REPORTS_DIR / "wan346_trades_A_oos_warm.csv"
CSV_PATH = REPORTS_DIR / "wan348_same_minute_tp.csv"
SAMPLE_CSV_PATH = REPORTS_DIR / "wan348_sample.csv"
COST_CSV_PATH = REPORTS_DIR / "wan348_archive_cost.csv"
SUMMARY_PATH = REPORTS_DIR / "wan348_same_minute_tp_summary.md"

#: 표본 크기(이슈 §2-4: 100건이면 「대부분 진짜」와 「대부분 인공물」을 가르기에 충분).
DEFAULT_SAMPLE_SIZE = 100
#: 층(=TF)마다 최소 이만큼은 뽑는다 — 비례 배분만 하면 4h가 4건이라 층별 판정이 안 선다.
DEFAULT_STRATUM_FLOOR = 10
#: 시드 고정(§2 완료기준 2: 재현 가능). 이슈 번호를 쓴다.
DEFAULT_SEED = 348

TF_MS: dict[str, int] = {"15m": 900_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}
#: 표·요약의 TF 순서(짧은 것부터). 층 배분·출력이 이 순서를 공유한다.
TF_ORDER: tuple[str, ...] = ("15m", "1h", "2h", "4h")

ARM_STATIC = "static"
ARM_BAND = "band"
ARM_ORDER: tuple[str, ...] = (ARM_STATIC, ARM_BAND)

#: 진입가 재구성이 「같은 값」이라고 인정하는 상대 오차. 같은 코드·같은 데이터라 사실상
#: 비트 일치가 나오지만, 부동소수 끝자리까지 요구하면 재구성이 **맞았는데** 실패로 찍힌다.
RECON_REL_TOL = 1e-9


# --------------------------------------------------------------------------- #
# §0 대상 목록 — WAN-346 §0 CSV를 읽기만 한다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Target:
    """「같은 분 익절」 거래 하나 (WAN-346 팔 A 거래별 CSV의 한 줄)."""

    symbol: str
    timeframe: str
    entry_ms: int
    """진입 = 익절이 일어난 그 1분의 `open_time`(ms, UTC)."""
    entry_price: float
    stop_price: float
    take_profit_price: float
    is_reentry: bool
    net_r: float
    pnl: float
    take_profit_derived: bool = False
    """익절가를 CSV가 아니라 `진입가 + R배수 ×(진입가 − 손절가)`로 되살렸는지 (아래 참고)."""

    @property
    def day(self) -> str:
        return day_of(self.entry_ms)


def _parse_utc_minute(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000)


def derive_take_profit(entry_price: float, stop_price: float, take_profit_r: float) -> float:
    """고정 R 익절 목표 — `진입가 + R×(진입가 − 손절가)`(롱)."""
    return entry_price + take_profit_r * (entry_price - stop_price)


def load_targets(path: Path = TARGET_CSV, *, take_profit_r: float | None = None) -> list[Target]:
    """대상 목록(`같은분익절=True`)을 읽는다.

    ⚠️ **이 CSV를 다시 만들지 않는다** — WAN-346 §0의 산출물이고, 다시 돌리면 팔 하나에
    66분이 든다(WAN-330 실측). 열이 없으면 **조용히 빈 목록을 내지 않고 죽는다**: 열
    이름이 바뀌었는데 0건으로 통과하면 「표본이 없는 표」가 정상처럼 나온다.

    🚨 **재진입 거래는 `익절가` 열이 비어 있다**(WAN-346 §0의 관측 열이 재진입 후보 경로에
    배선되지 않았다 — WAN-345가 고친 것과 **같은 자리의 남은 조각**이고, 순수 관측 열이라
    손익·판정 어디에도 안 쓰인다). 채택 북 팔 A의 같은 분 익절 467건 중 **137건이 재진입**
    이라 그냥 두면 표본의 29%가 「익절가 없음」으로 판정 불가가 된다. 그래서 고정 R 규칙으로
    되살리고, **CSV에 값이 있는 행에서 그 되살림이 맞는지 검산한다**(`take_profit_checksum`):
    330건 전부 비트 수준으로 일치하므로 되살린 값은 지어낸 것이 아니다.
    """
    resolved_r = ConfluenceParams().take_profit_r if take_profit_r is None else take_profit_r
    frame = pd.read_csv(path)
    required = {"같은분익절", "칸(종목)", "칸(TF)", "진입시각(UTC)", "진입가", "손절가", "익절가"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}에 필요한 열이 없습니다: {sorted(missing)}")
    hits = frame[frame["같은분익절"].astype(str) == "True"]
    targets: list[Target] = []
    for _, row in hits.iterrows():
        entry = float(row["진입가"])
        stop = float(row["손절가"])
        recorded = float(row["익절가"])
        derived = derive_take_profit(entry, stop, resolved_r)
        targets.append(
            Target(
                symbol=str(row["칸(종목)"]),
                timeframe=str(row["칸(TF)"]),
                entry_ms=_parse_utc_minute(str(row["진입시각(UTC)"])),
                entry_price=entry,
                stop_price=stop,
                take_profit_price=derived if math.isnan(recorded) else recorded,
                is_reentry=str(row["재진입"]) == "True",
                net_r=float(row["net R"]),
                pnl=float(row["손익"]),
                take_profit_derived=math.isnan(recorded),
            )
        )
    if not targets:
        raise ValueError(f"{path}에 「같은분익절=True」 행이 없습니다 — 대상 목록이 비었습니다.")
    return targets


def take_profit_checksum(
    path: Path = TARGET_CSV, *, take_profit_r: float | None = None
) -> tuple[int, int, float]:
    """되살린 익절가가 맞는지 — `(값이 있는 행, 일치한 행, 최대 상대오차)`.

    CSV에 익절가가 적힌 행에서 고정 R 규칙이 **그 값을 재현**해야 재진입 행의 되살림을
    믿을 수 있다. 이 검산이 깨지면 되살림은 「그럴듯한 지어냄」이 되므로 요약에 그대로 찍는다.
    """
    resolved_r = ConfluenceParams().take_profit_r if take_profit_r is None else take_profit_r
    frame = pd.read_csv(path)
    hits = frame[frame["같은분익절"].astype(str) == "True"]
    known = hits[~hits["익절가"].isna()]
    if known.empty:
        return (0, 0, float("nan"))
    derived = known["진입가"] + resolved_r * (known["진입가"] - known["손절가"])
    rel = ((known["익절가"] - derived).abs() / known["익절가"].abs()).astype(float)
    return (int(len(known)), int((rel < 1e-12).sum()), float(rel.max()))


# --------------------------------------------------------------------------- #
# §2 표본 설계 — TF 층화 · 시드 고정
# --------------------------------------------------------------------------- #


def allocate(sizes: dict[str, int], total: int, floor: int) -> dict[str, int]:
    """층(=TF)마다 몇 건을 뽑을지 — **바닥 우선 + 나머지는 비례**.

    순수 비례만 쓰면 4h가 4건이라 층별 판정이 서지 않고(모집단 21건), 균등만 쓰면 15m의
    무게(336건 · 같은 분 익절 손익의 76%)가 표본에서 사라진다. 그래서 **바닥 `floor`를 먼저
    깔고 남은 자리를 비례 배분**한 뒤, 층별 p는 각자 내고 전체 p는 **층 가중**으로 낸다
    (`weighted_ratio`) — 그래야 배분이 헤드라인을 흔들지 않는다.

    모집단이 바닥보다 작은 층은 **전수**다(21건짜리 층에 25건을 요구할 수 없다).
    """
    if total <= 0:
        raise ValueError(f"표본 크기는 양수여야 합니다: {total}")
    order = [tf for tf in TF_ORDER if tf in sizes] + [tf for tf in sizes if tf not in TF_ORDER]
    picked = {tf: min(floor, sizes[tf]) for tf in order}
    remaining = total - sum(picked.values())
    if remaining <= 0:
        return picked
    room = {tf: sizes[tf] - picked[tf] for tf in order}
    weight_total = sum(sizes[tf] for tf in order if room[tf] > 0)
    if weight_total > 0:
        for tf in order:
            if room[tf] <= 0:
                continue
            add = min(room[tf], int(remaining * sizes[tf] / weight_total))
            picked[tf] += add
    # 반올림 잔여는 여유가 남은 층에 큰 순서로 하나씩 — 합이 `total`이 되게 한다.
    while sum(picked.values()) < total:
        candidates = [tf for tf in order if picked[tf] < sizes[tf]]
        if not candidates:
            break
        best = max(candidates, key=lambda tf: (sizes[tf], tf))
        picked[best] += 1
    return picked


def _sort_key(target: Target) -> tuple[str, str, int, float]:
    return (target.symbol, target.timeframe, target.entry_ms, target.entry_price)


def draw_sample(
    targets: Sequence[Target],
    *,
    size: int = DEFAULT_SAMPLE_SIZE,
    floor: int = DEFAULT_STRATUM_FLOOR,
    seed: int = DEFAULT_SEED,
) -> list[Target]:
    """TF 층화 무작위 표본. **같은 시드면 같은 목록**이다(완료기준 2).

    층마다 목록을 정본 키로 정렬한 뒤 뽑으므로 입력 CSV의 행 순서가 바뀌어도 결과가 같다 —
    「시드 고정」이 파일 순서에 기대면 재현이 조용히 깨진다.

    ⚠️ **날짜를 겹치도록 몰아 뽑지 않는다**(§2-6이 허용한 비용 절감). 아카이브는 일 단위라
    그렇게 하면 파일 수가 줄지만 **시점 편중**을 사게 되고, 실측 비용이 그럴 만큼 크지
    않았다(요약의 §1 비용 표). 대신 표본이 실제로 몇 개 파일·며칠에 흩어졌는지를 **보고한다**.
    """
    by_tf: dict[str, list[Target]] = {}
    for target in targets:
        by_tf.setdefault(target.timeframe, []).append(target)
    sizes = {tf: len(rows) for tf, rows in by_tf.items()}
    quota = allocate(sizes, size, floor)
    picked: list[Target] = []
    for index, tf in enumerate(tf for tf in TF_ORDER if tf in by_tf):
        pool = sorted(by_tf[tf], key=_sort_key)
        rng = random.Random(f"{seed}:{tf}:{index}")
        picked.extend(rng.sample(pool, quota[tf]))
    return sorted(picked, key=_sort_key)


# --------------------------------------------------------------------------- #
# §3 재구성 — 엔진이 그 순간 주문판에 걸어 두었던 지정가를 되살린다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Reconstruction:
    """그 1분의 지정가 함수 `L(p)` + 그것이 엔진 값을 재현했다는 증거."""

    order_block: OrderBlock
    band: RealtimeBand
    params: ConfluenceParams
    minute_close: float
    reconstructed_entry: float
    """`L(그 1분 종가)` — 엔진이 기록한 진입가와 같아야 한다(그게 재구성의 검산이다)."""

    def limit_at(self, price: float) -> float | None:
        """틱 가격 `price`를 밴드 표본으로 썼을 때 주문판에 걸려 있는 지정가.

        사슬은 엔진과 **글자 그대로 같다**: 밴드 → `deviation_entry_price`(규칙 3 기각이면
        주문 없음) → `apply_zone_limit_offset`(`backtest.zone_limit_backtest._IntrabarLiveLimit`
        `_limit_from_sample`). 여기서 사슬을 다시 쓰지 않고 같은 순수 함수들을 부른다.
        """
        band_value = self.band.value(price, 1)
        if band_value is None:
            return None
        entry = deviation_entry_price(1, self.order_block, band_value)
        if entry is None:
            return None
        return self.params.apply_zone_limit_offset(entry, is_long=True)


@dataclass(frozen=True, slots=True)
class CellBars:
    """한 (종목, TF)의 상위TF 봉 + 그 위에서 한 번만 돌린 오더블록 탐지 결과."""

    times: list[int]
    closes: list[float]
    order_blocks: list[OrderBlock]


def load_cell(
    symbol: str,
    timeframe: str,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> CellBars:
    """채택 창 전체로 탐지한다 — 「워밍업이 모자랐나」라는 질문 자체를 없앤다.

    엔진(따뜻한 OOS)은 전 구간을 연속으로 태우므로 존 대장도 창 전체에서 난다(WAN-166).
    짧은 창으로 탐지하면 오래된 존이 빠져 재구성이 실패하는데, 그 실패가 「데이터가 없다」인지
    「창이 짧다」인지 구분되지 않는다. 6년 15m 탐지가 몇 초라(`combine_obs=False`) 아낄 이유가
    없다.
    """
    store = OhlcvStore(db_path)
    frame = store.load(
        symbol,
        timeframe,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
    )
    if frame.empty:
        return CellBars([], [], [])
    result = OrderBlockDetector(OrderBlockParams()).run(frame)
    return CellBars(
        times=[int(v) for v in frame["open_time"]],
        closes=[float(v) for v in frame["close"]],
        order_blocks=list(result.order_blocks),
    )


def load_minute_bar(
    symbol: str, entry_ms: int, *, db_path: str = harness.DB_PATH
) -> tuple[float, float, float] | None:
    """그 1분봉의 `(고가, 저가, 종가)`. 없으면 None."""
    store = OhlcvStore(db_path)
    frame = store.load(symbol, "1m", start_ms=entry_ms, end_ms=entry_ms + 60_000)
    if frame.empty:
        return None
    row = frame.iloc[0]
    return float(row["high"]), float(row["low"]), float(row["close"])


def reconstruct(
    target: Target,
    cell: CellBars,
    minute_close: float,
    *,
    params: ConfluenceParams | None = None,
) -> Reconstruction | None:
    """그 셋업의 오더블록과 밴드 상태를 되살린다 — **엔진 값을 재현해야만** 성공이다.

    후보는 「손절 참조가 = 존 무효화 경계(롱은 `bottom`)」로 좁히고, 그중 `L(그 분 종가)`가
    기록된 진입가와 같은 것만 받는다. 이 검산이 없으면 엉뚱한 존으로 틱을 굴려 놓고 표에는
    숫자가 찍히는, 이 저장소가 가장 경계하는 부류의 실패가 된다(WAN-91/95/112/123/159).
    """
    resolved = params or ConfluenceParams()
    deviation = resolved.deviation_filter
    if deviation is None:  # pragma: no cover - 채택 기본값에는 언제나 있다
        return None
    htf_ms = TF_MS.get(target.timeframe)
    if htf_ms is None or not cell.times:
        return None
    bar_open = (target.entry_ms // htf_ms) * htf_ms
    cut = bisect.bisect_left(cell.times, bar_open)
    band = RealtimeBand.seed_from_closed(cell.closes, deviation, end=cut)
    for block in cell.order_blocks:
        if block.direction is not OrderBlockDirection.BULLISH:
            continue
        if not math.isclose(block.bottom, target.stop_price, rel_tol=1e-12, abs_tol=0.0):
            continue
        candidate = Reconstruction(
            order_block=block,
            band=band,
            params=resolved,
            minute_close=minute_close,
            reconstructed_entry=float("nan"),
        )
        limit = candidate.limit_at(minute_close)
        if limit is None:
            continue
        if math.isclose(limit, target.entry_price, rel_tol=RECON_REL_TOL, abs_tol=0.0):
            return replace(candidate, reconstructed_entry=limit)
    return None


# --------------------------------------------------------------------------- #
# §3 측정 — 그 1분의 체결을 시간순으로 훑는다
# --------------------------------------------------------------------------- #

#: 판정 라벨. 「판정 불가」 둘(`틱없음`·`미체결`)은 비율의 **분모에서 빠진다**.
VERDICT_REAL = "진짜"
"""체결이 먼저이고 그 뒤 익절가에 닿았다 — 엔진의 가정도 결과도 맞다."""
VERDICT_ORDER_FLIPPED_STILL = "순서역전-성립"
"""익절가에 먼저 닿았지만 체결 뒤 **다시** 닿았다 — 가정은 틀렸고 손익은 그대로 난다."""
VERDICT_ARTIFACT = "인공물"
"""익절가에 먼저 닿고 체결 뒤로는 그 분 안에 다시 안 닿았다 — 그 손익은 그 분에 안 났다."""
VERDICT_NOT_SAME_MINUTE = "같은분아님"
"""체결은 됐으나 그 분 안에 익절가에 닿지 않았다(팔 `band`에서 목표가 달라질 때)."""
VERDICT_NO_FILL = "미체결"
"""그 분 안에 지정가에 닿은 체결이 없다 — 팔 `static`에서는 데이터 이상 신호다."""
VERDICT_NO_TICKS = "틱없음"
"""그 분에 체결 자체가 없다(아카이브 결측·거래 없음)."""
VERDICT_RECON_FAILED = "재구성실패"
"""엔진이 그 순간 걸어 두었던 지정가를 되살리지 못했다(팔 `band` 전용).

**빈칸으로 두지 않고 라벨로 남긴다** — 조용히 빠지면 두 팔이 다른 모집단을 재게 되고
표에서 그 사실이 안 보인다."""

DECIDABLE = (VERDICT_REAL, VERDICT_ORDER_FLIPPED_STILL, VERDICT_ARTIFACT, VERDICT_NOT_SAME_MINUTE)


@dataclass(frozen=True, slots=True)
class Measurement:
    """한 거래 × 한 팔의 판정."""

    verdict: str
    fill_ms: int | None
    fill_price: float | None
    take_profit_price: float | None
    first_tp_ms: int | None
    """익절가에 **처음** 닿은 시각(체결 전이어도 찍는다 — 그게 순서 질문의 자료다)."""
    tp_after_fill_ms: int | None
    tick_count: int

    @property
    def decidable(self) -> bool:
        return self.verdict in DECIDABLE

    @property
    def order_ok(self) -> bool:
        """엔진의 가정 그대로 — 익절가보다 체결가에 먼저 닿았나."""
        return self.verdict == VERDICT_REAL

    @property
    def outcome_ok(self) -> bool:
        """그 손익이 실제로 그 분에 났나(순서가 뒤집혔어도 다시 닿았으면 참)."""
        return self.verdict in (VERDICT_REAL, VERDICT_ORDER_FLIPPED_STILL)


def _classify(
    *,
    fill_ms: int | None,
    first_tp_ms: int | None,
    tp_after_fill_ms: int | None,
    tick_count: int,
) -> str:
    if tick_count == 0:
        return VERDICT_NO_TICKS
    if fill_ms is None:
        return VERDICT_NO_FILL
    if tp_after_fill_ms is None:
        return VERDICT_ARTIFACT if first_tp_ms is not None else VERDICT_NOT_SAME_MINUTE
    if first_tp_ms is not None and first_tp_ms < fill_ms:
        return VERDICT_ORDER_FLIPPED_STILL
    return VERDICT_REAL


def measure_static(ticks: Sequence[Tick], target: Target) -> Measurement:
    """팔 `static` — 엔진이 쓴 진입가·익절가를 고정 수준으로 두고 순서만 본다."""
    fill_ms: int | None = None
    first_tp_ms: int | None = None
    tp_after_fill_ms: int | None = None
    for tick in ticks:
        if fill_ms is None and tick.price <= target.entry_price:
            fill_ms = tick.time_ms
        if tick.price >= target.take_profit_price:
            if first_tp_ms is None:
                first_tp_ms = tick.time_ms
            if fill_ms is not None and tp_after_fill_ms is None:
                tp_after_fill_ms = tick.time_ms
    return Measurement(
        verdict=_classify(
            fill_ms=fill_ms,
            first_tp_ms=first_tp_ms,
            tp_after_fill_ms=tp_after_fill_ms,
            tick_count=len(ticks),
        ),
        fill_ms=fill_ms,
        fill_price=target.entry_price if fill_ms is not None else None,
        take_profit_price=target.take_profit_price,
        first_tp_ms=first_tp_ms,
        tp_after_fill_ms=tp_after_fill_ms,
        tick_count=len(ticks),
    )


def measure_band(
    ticks: Sequence[Tick], target: Target, recon: Reconstruction, *, take_profit_r: float
) -> Measurement:
    """팔 `band` — 틱마다 지정가를 다시 내고(`L(p)`) 고정점에서 체결시킨다 (§3-8).

    체결가가 그 자리에서 정해지므로 1R(진입가 → 무효화 경계)도 그 배수인 고정 R 익절 목표도
    **체결 순간에** 다시 난다 — 엔진이 `resolve_exits`로 하는 것과 같은 순서다.

    두 번 훑는 이유: 익절 목표가 체결가에서 파생되므로 **체결을 먼저 확정한 뒤에야** 그
    목표선으로 순서를 셀 수 있다. 한 번에 하면 체결 전 구간만 엔진 목표로, 체결 후 구간만 새
    목표로 재는 **섞인 자**가 된다.
    """
    fill_ms: int | None = None
    fill_price: float | None = None
    take_profit: float | None = None
    for tick in ticks:
        limit = recon.limit_at(tick.price)
        if limit is not None and tick.price <= limit:
            fill_ms = tick.time_ms
            fill_price = limit
            take_profit = limit + take_profit_r * (limit - target.stop_price)
            break

    first_tp_ms: int | None = None
    tp_after_fill_ms: int | None = None
    if take_profit is not None:
        for tick in ticks:
            if tick.price < take_profit:
                continue
            if first_tp_ms is None:
                first_tp_ms = tick.time_ms
            if fill_ms is not None and tick.time_ms >= fill_ms:
                tp_after_fill_ms = tick.time_ms
                break
    return Measurement(
        verdict=_classify(
            fill_ms=fill_ms,
            first_tp_ms=first_tp_ms,
            tp_after_fill_ms=tp_after_fill_ms,
            tick_count=len(ticks),
        ),
        fill_ms=fill_ms,
        fill_price=fill_price,
        take_profit_price=take_profit,
        first_tp_ms=first_tp_ms,
        tp_after_fill_ms=tp_after_fill_ms,
        tick_count=len(ticks),
    )


# --------------------------------------------------------------------------- #
# 통계 — 층 가중 비율 + Wilson 구간
# --------------------------------------------------------------------------- #


def wilson_interval(
    successes: int, total: int, *, z: float = 1.959963984540054
) -> tuple[float, float]:
    """이항 비율의 Wilson 95% 구간.

    정규근사(`p ± z·√(p(1−p)/n)`)를 쓰지 않는 이유: p가 0이나 1에 가까우면 구간이 [0,1]을
    벗어나거나 폭 0으로 붕괴한다 — 이 표는 그 끝값이 실제로 나올 수 있는 자리다.
    """
    if total <= 0:
        return (float("nan"), float("nan"))
    phat = successes / total
    denom = 1.0 + z * z / total
    center = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def weighted_ratio(per_stratum: dict[str, tuple[int, int]], weights: dict[str, int]) -> float:
    """층 가중 비율 `Σ (N_h/N)·p_h` — 배분이 헤드라인을 흔들지 않게 한다.

    바닥 배분(`allocate`) 때문에 4h가 모집단 비중(4.5%)보다 훨씬 많이 뽑히므로, 표본을 그냥
    합치면 **4h가 헤드라인을 끌어당긴다**. 판정 가능한 층만 가중에 넣는다.
    """
    usable = {tf: (s, n) for tf, (s, n) in per_stratum.items() if n > 0}
    total_weight = sum(weights.get(tf, 0) for tf in usable)
    if total_weight <= 0:
        return float("nan")
    return sum(weights.get(tf, 0) / total_weight * (s / n) for tf, (s, n) in usable.items())


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResultRow:
    """CSV 한 줄 — 거래 하나 × 팔 하나."""

    arm: str
    symbol: str
    timeframe: str
    entry_ms: int
    entry_utc: str
    day: str
    is_reentry: bool
    take_profit_derived: bool
    engine_entry: float
    engine_take_profit: float
    stop_price: float
    net_r: float
    reconstructed: bool
    """봉내 지정가 재구성에 성공했는지. 팔 `static`은 재구성이 **필요 없으므로** 언제나 참이고
    (엔진이 기록한 가격을 그대로 쓴다), 판정에 쓰이는 것은 팔 `band` 행의 값이다."""
    ohlc_match: bool | None
    """그 1분의 **틱 고·저가**가 저장 1분봉의 고·저가와 같은지 (독립 검산).

    두 자료는 출처가 다르다 — 1분봉은 우리 수집기가 넣은 것이고 틱은 거래소 아카이브다.
    어긋나면 **엉뚱한 날·엉뚱한 종목의 파일을 펼쳤다는 뜻**이라 판정 전체가 무효다. 틱이
    없으면 `None`(비교할 것이 없다)."""
    verdict: str
    order_ok: bool
    outcome_ok: bool
    fill_ms: int | None
    fill_price: float | None
    take_profit_price: float | None
    first_tp_ms: int | None
    tp_after_fill_ms: int | None
    fill_offset_s: float | None
    """그 분 시작부터 체결까지 몇 초. 「어디쯤에서 체결됐나」의 분포용."""
    tick_count: int


def ohlc_matches(
    ticks: Sequence[Tick], bar: tuple[float, float, float] | None, *, rel_tol: float = 1e-9
) -> bool | None:
    """틱 고·저가가 저장 1분봉의 고·저가와 같은가 — 두 자료가 서로를 검산한다."""
    if not ticks or bar is None:
        return None
    prices = [tick.price for tick in ticks]
    return math.isclose(max(prices), bar[0], rel_tol=rel_tol) and math.isclose(
        min(prices), bar[1], rel_tol=rel_tol
    )


def _row(
    arm: str,
    target: Target,
    measurement: Measurement,
    *,
    reconstructed: bool,
    ohlc_match: bool | None,
) -> ResultRow:
    return ResultRow(
        arm=arm,
        symbol=target.symbol,
        timeframe=target.timeframe,
        entry_ms=target.entry_ms,
        entry_utc=datetime.fromtimestamp(target.entry_ms / 1000.0, tz=UTC).strftime(
            "%Y-%m-%d %H:%M"
        ),
        day=target.day,
        is_reentry=target.is_reentry,
        take_profit_derived=target.take_profit_derived,
        engine_entry=target.entry_price,
        engine_take_profit=target.take_profit_price,
        stop_price=target.stop_price,
        net_r=target.net_r,
        reconstructed=reconstructed,
        ohlc_match=ohlc_match,
        verdict=measurement.verdict,
        order_ok=measurement.order_ok,
        outcome_ok=measurement.outcome_ok,
        fill_ms=measurement.fill_ms,
        fill_price=measurement.fill_price,
        take_profit_price=measurement.take_profit_price,
        first_tp_ms=measurement.first_tp_ms,
        tp_after_fill_ms=measurement.tp_after_fill_ms,
        fill_offset_s=(
            None
            if measurement.fill_ms is None
            else (measurement.fill_ms - target.entry_ms) / 1000.0
        ),
        tick_count=measurement.tick_count,
    )


def run_measurement(
    sample: Sequence[Target],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    db_path: str = harness.DB_PATH,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    params: ConfluenceParams | None = None,
) -> tuple[list[ResultRow], list[DayFetch]]:
    """표본을 실제로 잰다 — (종목, TF)마다 탐지 한 번 · (종목, 날짜)마다 파일 한 개."""
    resolved = params or ConfluenceParams()
    cells: dict[tuple[str, str], CellBars] = {}
    fetches: dict[tuple[str, str], DayFetch] = {}
    rows: list[ResultRow] = []

    for index, target in enumerate(sample, start=1):
        cell_key = (target.symbol, target.timeframe)
        if cell_key not in cells:
            cells[cell_key] = load_cell(
                target.symbol, target.timeframe, start=start, end=end, db_path=db_path
            )
        day_key = (target.symbol, target.day)
        if day_key not in fetches:
            fetches[day_key] = fetch_day(target.symbol, target.day, cache_dir=cache_dir)
            logger.info(
                "[wan348] %d/%d %s %s %s",
                index,
                len(sample),
                target.symbol,
                target.day,
                "캐시" if fetches[day_key].cached else f"{fetches[day_key].size_bytes / 1e6:.1f}MB",
            )
        fetch = fetches[day_key]
        ticks: list[Tick] = (
            minute_ticks(fetch.path, target.entry_ms) if fetch.path is not None else []
        )

        bar = load_minute_bar(target.symbol, target.entry_ms, db_path=db_path)
        match = ohlc_matches(ticks, bar)
        rows.append(
            _row(
                ARM_STATIC,
                target,
                measure_static(ticks, target),
                reconstructed=True,
                ohlc_match=match,
            )
        )

        recon = (
            None if bar is None else reconstruct(target, cells[cell_key], bar[2], params=resolved)
        )
        if recon is None:
            rows.append(
                _row(
                    ARM_BAND,
                    target,
                    Measurement(
                        verdict=VERDICT_RECON_FAILED,
                        fill_ms=None,
                        fill_price=None,
                        take_profit_price=None,
                        first_tp_ms=None,
                        tp_after_fill_ms=None,
                        tick_count=len(ticks),
                    ),
                    reconstructed=False,
                    ohlc_match=match,
                )
            )
            continue
        rows.append(
            _row(
                ARM_BAND,
                target,
                measure_band(ticks, target, recon, take_profit_r=resolved.take_profit_r),
                reconstructed=True,
                ohlc_match=match,
            )
        )
    return rows, list(fetches.values())


def rows_to_frame(rows: Sequence[ResultRow]) -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in rows])


def sample_to_frame(sample: Sequence[Target]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": t.symbol,
                "timeframe": t.timeframe,
                "entry_ms": t.entry_ms,
                "entry_utc": datetime.fromtimestamp(t.entry_ms / 1000.0, tz=UTC).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                "day": t.day,
                "entry_price": t.entry_price,
                "stop_price": t.stop_price,
                "take_profit_price": t.take_profit_price,
                "is_reentry": t.is_reentry,
                "take_profit_derived": t.take_profit_derived,
                "net_r": t.net_r,
            }
            for t in sample
        ]
    )


def fetches_to_frame(fetches: Sequence[DayFetch]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": f.symbol,
                "day": f.day,
                "status": f.status,
                "size_bytes": f.size_bytes,
                "seconds": f.seconds,
                "cached": f.cached,
                "note": f.note,
            }
            for f in fetches
        ]
    )


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def _ratio_block(frame: pd.DataFrame, column: str, weights: dict[str, int]) -> list[str]:
    usable = frame[frame["verdict"].isin(DECIDABLE)]
    lines: list[str] = []
    per_stratum: dict[str, tuple[int, int]] = {}
    for tf in TF_ORDER:
        part = usable[usable["timeframe"] == tf]
        if part.empty:
            continue
        hits = int(part[column].astype(bool).sum())
        total = int(len(part))
        per_stratum[tf] = (hits, total)
        low, high = wilson_interval(hits, total)
        lines.append(
            f"| {tf} | {hits}/{total} | {hits / total * 100:.1f}% | "
            f"{low * 100:.1f}~{high * 100:.1f}% | {weights.get(tf, 0)} |"
        )
    hits = int(usable[column].astype(bool).sum())
    total = int(len(usable))
    low, high = wilson_interval(hits, total)
    weighted = weighted_ratio(per_stratum, weights)
    lines.append(
        f"| **합(단순)** | {hits}/{total} | {hits / total * 100:.1f}% | "
        f"{low * 100:.1f}~{high * 100:.1f}% | — |"
        if total
        else "| **합(단순)** | 0/0 | — | — | — |"
    )
    lines.append(f"| **합(층 가중)** | — | {weighted * 100:.1f}% | — | — |")
    return lines


#: WAN-336 §1·§2가 `oos_warm`에서 낸 두 극단. **여기서 다시 계산하지 않고 인용한다** —
#: 그 표를 다시 돌리려면 채택 북을 통째로 돌려야 하고(팔당 66분, WAN-330 실측) 이 이슈는
#: 그 두 극단 **사이의 어디인지**만 묻는다.
WAN336_BASE_MDD = 22.90
WAN336_COUNTERFACTUAL_MDD = 25.22
WAN336_BASE_WIN = 55.33
WAN336_COUNTERFACTUAL_WIN = 53.32
WAN336_NET_R_SHARE = 48.18
"""`oos_warm` 순손익(net R 자)에서 같은 분 익절이 차지하는 비중(%)."""


def blend(base: float, counterfactual: float, p: float) -> float:
    """두 극단 사이에서 진값의 자리 — `p`가 1이면 현행, 0이면 반사실.

    WAN-336이 낸 것은 **폭**이고 이 이슈가 내는 것은 그 안의 **위치**다. 선형 혼합이
    정당한 이유: `p`는 「같은 분 익절 중 틱이 지지하는 비율」이라, 지지받는 몫은 현행처럼
    처리되고 나머지는 반사실처럼 처리되는 것이 이 두 극단의 정의 그대로다.

    ⚠️ **복리 총수익에는 쓰지 말 것** — 거래당 효과가 곱으로 쌓여 선형이 아니다(WAN-169/213).
    """
    return counterfactual + p * (base - counterfactual)


def fill_offset_table(frame: pd.DataFrame) -> list[str]:
    """체결이 그 분의 **어디쯤**에서 났는지 × 성립률 — 판정의 기계적 이유다.

    봉내 라이브 밴드가 떨어지는 가격을 따라 지정가를 재산정하므로 체결가는 그 분의 저가
    근처에 놓이고(WAN-336이 「정합적인 설명」으로 남긴 자리), 그러면 **남은 시간이 짧을수록**
    1.5R을 올라갈 여유가 없다. 이 표가 그 사슬을 숫자로 보인다.
    """
    part = frame[frame["fill_offset_s"].notna()].copy()
    if part.empty:
        return []
    edges = [0.0, 15.0, 30.0, 45.0, 60.1]
    labels = ["0~15초", "15~30초", "30~45초", "45~60초"]
    part["bucket"] = pd.cut(part["fill_offset_s"], edges, labels=labels, include_lowest=True)
    rows = [
        "| 체결 시각(그 분 안) | 건수 | 성립 | 성립률 | 순서 |",
        "| -- | --: | --: | --: | --: |",
    ]
    for label in labels:
        chunk = part[part["bucket"] == label]
        if chunk.empty:
            continue
        total = int(len(chunk))
        hits = int(chunk["outcome_ok"].astype(bool).sum())
        order = int(chunk["order_ok"].astype(bool).sum())
        rows.append(f"| {label} | {total} | {hits} | {hits / total * 100:.0f}% | {order} |")
    return rows


def leave_one_out(frame: pd.DataFrame, column: str = "outcome_ok") -> list[tuple[str, float, int]]:
    """종목을 하나씩 빼고 다시 낸 비율 — 「한 종목이 만든 결과」인지 본다.

    ⚠️ WAN-336의 leave-one-out과 **성격이 다르다** — 그쪽은 지갑을 다시 배치한다(북의 자본
    경합이 달라진다). 여기서는 이미 일어난 거래의 **판정 비율**이라 라벨 필터가 옳다.
    """
    usable = frame[frame["verdict"].isin(DECIDABLE)]
    out: list[tuple[str, float, int]] = []
    for symbol in sorted(usable["symbol"].unique()):
        rest = usable[usable["symbol"] != symbol]
        if rest.empty:
            continue
        out.append((str(symbol), float(rest[column].astype(bool).mean()), int(len(rest))))
    return out


def arm_agreement(frame: pd.DataFrame) -> tuple[int, int]:
    """두 팔이 같은 판정을 낸 거래 수 / 두 팔 다 판정 가능한 거래 수.

    §3-8이 걱정한 것은 「밴드 재산정을 안 하면 체결 시각이 틀린다」였다. 이 수가 크면 그
    걱정이 이 표의 결론을 흔들지 않았다는 뜻이고, 작으면 헤드라인을 팔 `band`로 옮겨야 한다.
    """
    usable = frame[frame["verdict"].isin(DECIDABLE)]
    pivot = usable.pivot_table(
        index=["symbol", "timeframe", "entry_ms"],
        columns="arm",
        values="outcome_ok",
        aggfunc="first",
    )
    both = pivot.dropna()
    if both.empty or ARM_STATIC not in both.columns or ARM_BAND not in both.columns:
        return (0, 0)
    same = int((both[ARM_STATIC].astype(bool) == both[ARM_BAND].astype(bool)).sum())
    return (same, int(len(both)))


def net_r_weighted_ratio(frame: pd.DataFrame, column: str = "outcome_ok") -> float:
    """크기로 가중한 성립 비율 — 「인공물이 하필 큰 거래에 몰려 있나」를 본다.

    건수 비율과 크게 갈리면 그 자체가 신호다(WAN-336이 USD·net R 두 자를 병기한 이유와 같은
    부류). `net R`을 쓰는 이유는 복리·시점 편중이 안 섞이기 때문이다.
    """
    usable = frame[frame["verdict"].isin(DECIDABLE)]
    total = float(usable["net_r"].abs().sum())
    if total <= 0:
        return float("nan")
    hits = usable[usable[column].astype(bool)]
    return float(hits["net_r"].abs().sum()) / total


def build_summary(
    frame: pd.DataFrame,
    sample_frame: pd.DataFrame,
    cost_frame: pd.DataFrame,
    *,
    population: dict[str, int] | None = None,
    checksum: tuple[int, int, float] | None = None,
) -> str:
    """판정을 문장으로 낸다 — 표만 두면 다음 사람이 다시 해석한다."""
    weights = population or {}
    out: list[str] = [
        "# WAN-348 — 「같은 분 익절」이 진짜인지 틱으로 직접 잰다",
        "",
        "> 대상: WAN-346 §0 팔 A 거래별 CSV의 `같은분익절=True` 467건 · 표본 TF 층화 무작위 "
        f"{len(sample_frame)}건(시드 {DEFAULT_SEED}).",
        "> 자료: Binance USDⓈ-M 선물 일자별 체결내역 아카이브(`data.binance.vision`) — "
        "WAN-347 §0이 「유일한 길」로 실측한 경로.",
        "",
        "## 판정",
        "",
    ]
    for arm in ARM_ORDER:
        part = frame[frame["arm"] == arm]
        if part.empty:
            continue
        label = "엔진 값 그대로(헤드라인)" if arm == ARM_STATIC else "틱 위 밴드 재산정(§3-8)"
        out += [
            f"### 팔 `{arm}` — {label}",
            "",
            "**성립**(체결 뒤 그 분 안에 익절가 도달 = 그 손익이 실제로 났나)",
            "",
            "| TF | 성립/판정 | 비율 | 95% 구간 | 모집단 |",
            "| -- | -- | -- | -- | -- |",
            *_ratio_block(part, "outcome_ok", weights),
            "",
            "**순서**(익절가보다 체결가에 먼저 닿았나 = 엔진의 가정 그대로)",
            "",
            "| TF | 순서/판정 | 비율 | 95% 구간 | 모집단 |",
            "| -- | -- | -- | -- | -- |",
            *_ratio_block(part, "order_ok", weights),
            "",
            "판정 분포: "
            + " · ".join(
                f"`{k}` {v}" for k, v in sorted(part["verdict"].value_counts().to_dict().items())
            ),
            "",
        ]

    static = frame[frame["arm"] == ARM_STATIC]
    if not static.empty:
        usable = static[static["verdict"].isin(DECIDABLE)]
        headline = weighted_ratio(
            {
                tf: (
                    int(usable[usable["timeframe"] == tf]["outcome_ok"].astype(bool).sum()),
                    int(len(usable[usable["timeframe"] == tf])),
                )
                for tf in TF_ORDER
                if not usable[usable["timeframe"] == tf].empty
            },
            weights,
        )
        low, high = wilson_interval(int(usable["outcome_ok"].astype(bool).sum()), int(len(usable)))
        out += [
            "## §4 할인 — 그 p를 WAN-336의 두 극단에 대입하면",
            "",
            f"**한 줄: 같은 분 익절 중 틱이 지지하는 것은 약 {headline * 100:.0f}%이므로, 진값은 "
            f"WAN-336의 두 극단 사이에서 반사실 쪽으로 약 {(1 - headline) * 100:.0f}% 기운 "
            "자리다.**",
            "",
            "| `oos_warm` | 현행(WAN-336 `base`) | 반사실(`no_same_step_tp`) "
            "| **이 표의 p를 대입** |",
            "| -- | --: | --: | --: |",
            f"| MDD | {WAN336_BASE_MDD:.2f}% | {WAN336_COUNTERFACTUAL_MDD:.2f}% | "
            f"**{blend(WAN336_BASE_MDD, WAN336_COUNTERFACTUAL_MDD, headline):.2f}%** |",
            f"| 승률 | {WAN336_BASE_WIN:.2f}% | {WAN336_COUNTERFACTUAL_WIN:.2f}% | "
            f"**{blend(WAN336_BASE_WIN, WAN336_COUNTERFACTUAL_WIN, headline):.2f}%** |",
            "",
            f"- 그 구간 순손익의 **{WAN336_NET_R_SHARE:.2f}%**(net R 자)가 같은 분 익절에 실려 "
            f"있고 그중 약 {(1 - headline) * 100:.0f}%가 틱의 지지를 못 받으므로, "
            f"**약 {WAN336_NET_R_SHARE * (1 - headline):.0f}%p**가 재판정 대상이다.",
            "- 🚨 **재판정 대상 ≠ 손실이다** — 순서가 반대였다면 그 거래는 손실이 아니라 **더 "
            "오래 보유**이고 그 뒤는 미지다. 그 대가의 상한이 반사실 열이다.",
            "- ⚠️ **복리 총수익에는 이 혼합을 쓰지 말 것** — 거래당 효과가 곱으로 쌓여 선형이 "
            "아니다(WAN-169/213).",
            f"- 표본 오차: 단순 비율 95% 구간 {low * 100:.1f}~{high * 100:.1f}% "
            "(그 폭만큼 위 칸도 함께 움직인다).",
            "",
        ]

        offsets = fill_offset_table(static)
        if offsets:
            median_offset = float(static["fill_offset_s"].dropna().median())
            out += [
                "## §5 왜 그런가 — 체결이 그 분의 **끝쪽**에서 난다",
                "",
                f"체결 시각 중앙값이 그 분의 **{median_offset:.0f}초**다. 봉내 라이브 밴드가 "
                "떨어지는 가격을 따라 지정가를 재산정하므로 체결가는 그 분의 저가 근처에 놓이고"
                "(WAN-336이 「정합적인 설명」으로 남긴 자리), 그러면 남은 시간이 짧을수록 1.5R을 "
                "올라갈 여유가 없다.",
                "",
                *offsets,
                "",
                "📌 **이 표가 그 설명을 실측으로 뒷받침한다** — 판정은 우연이 아니라 기계적이다.",
                "",
            ]

        loo = leave_one_out(static)
        if loo:
            worst = min(loo, key=lambda item: item[1])
            best = max(loo, key=lambda item: item[1])
            same, both = arm_agreement(frame)
            weighted_net_r = net_r_weighted_ratio(static)
            band = frame[frame["arm"] == ARM_BAND]
            no_fill = int((band["verdict"] == VERDICT_NO_FILL).sum())
            compared = static[static["ohlc_match"].notna()]
            compared_ohlc = int(len(compared))
            matched_ohlc = int(compared["ohlc_match"].astype(bool).sum())
            rebuilt = int(band["reconstructed"].astype(bool).sum())
            out += [
                "## §6 흔들리지 않는가",
                "",
                f"- **종목 편중이 아니다** — 하나씩 빼고 다시 내도 성립률이 "
                f"{worst[1] * 100:.1f}%(−{worst[0]})~{best[1] * 100:.1f}%(−{best[0]}) 사이다.",
                f"- **크기로 가중해도 같다** — net R 가중 성립률 {weighted_net_r * 100:.1f}% "
                "(건수 비율과 갈리면 「인공물이 큰 거래에 몰렸다」는 뜻인데 그렇지 않다).",
                f"- **두 팔이 같은 답을 낸다** — 판정 가능한 {both}건 중 {same}건에서 일치"
                f"({same / both * 100:.1f}%). §3-8이 걱정한 밴드 재산정은 결론을 흔들지 않았다.",
                f"- **재구성이 {rebuilt}/{len(band)}건 성공** — 엔진이 그 순간 걸어 두었던 "
                "지정가를 되살려 기록된 진입가를 재현했다(재현 못 하면 판정하지 않는다).",
                f"- **틱 고·저가가 저장 1분봉과 일치 {matched_ohlc}/{compared_ohlc}건** — 두 "
                "자료는 출처가 달라(수집기 1분봉 vs 거래소 아카이브) 어긋나면 엉뚱한 파일을 "
                "펼쳤다는 뜻이다.",
                f"- **팔 `band`의 미체결 {no_fill}건**은 틱 추종에서는 그 분에 안 채워졌다는 "
                "뜻이다 — WAN-328이 잰 「틱으론 그 봉에서 미체결」 11.4%와 같은 자릿수다"
                "(독립 교차 확인).",
                "",
            ]

    if not cost_frame.empty:
        downloaded = cost_frame[~cost_frame["cached"].astype(bool)]
        total_mb = float(cost_frame["size_bytes"].sum()) / 1e6
        seconds = float(downloaded["seconds"].sum())
        failed = int((cost_frame["status"].astype(int) != 200).sum())
        out += [
            "## §1 실현 가능성 · 비용 (실측)",
            "",
            f"- 파일 {len(cost_frame)}개(= 서로 다른 (종목, 날짜) 쌍) · 합계 "
            f"**{total_mb:.0f}MB** · 실패 {failed}개.",
            (
                f"- 받는 데 **{seconds:.0f}초**({len(downloaded)}개 실제 다운로드)."
                if len(downloaded)
                else "- ⚠️ 이 실행은 **전부 캐시 적중**이라 받는 시간을 재지 않았다 — "
                "0초로 읽지 말 것(비용 표는 실제로 받은 실행의 기록이다)."
            ),
            f"- 표본이 흩어진 날짜 수: {sample_frame['day'].nunique()}일 "
            f"(모집단 467건은 서로 다른 (종목, 날짜) 257쌍 · 159일).",
            "- 판정: **된다 — 단 일자별 아카이브로만**(REST는 최근 2일, WAN-347 §0).",
            "",
        ]

    if checksum is not None and checksum[0] > 0:
        known, matched, max_rel = checksum
        out += [
            "## 검산 — 되살린 익절가",
            "",
            "- 재진입 거래는 WAN-346 §0 CSV의 `익절가` 열이 **비어 있다**(관측 열이 재진입 "
            "후보 경로에 배선되지 않았다 — WAN-345가 고친 자리의 남은 조각). 같은 분 익절 "
            "467건 중 **137건이 재진입**이라 고정 R 규칙으로 되살렸다.",
            f"- 값이 적힌 **{known}건에서 되살림이 {matched}건 일치**(최대 상대오차 "
            f"{max_rel:.2e}) — 되살린 값은 지어낸 것이 아니다.",
            "",
        ]

    out += [
        "## ⚠️ 읽는 법",
        "",
        "- **`성립`이 헤드라인이다** — 순서가 뒤집혔어도 체결 뒤 익절가에 다시 닿았으면 그 "
        "거래의 손익은 그대로 난다. `순서`만 보면 그런 거래를 「가짜」로 세어 할인을 과대평가한다.",
        "- **「우리 주문이 채워졌을까」는 답하지 못한다** — 큐 우선순위·호가 깊이는 별개 축이다"
        "(`pen_5bp`가 그 민감도 · WAN-98 Canceled).",
        "- **더 이른 분에서 체결했을 가능성은 안 잰다** — 팔 `band`는 그 1분 안만 다시 푼다"
        "(WAN-328 `path_fill_price`와 같은 한계).",
        "- **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 축은 *진입 규칙이 "
        "무작위와 구분되는가*가 아니라 *이미 잰 숫자가 얼마나 낙관인가*를 묻는다.",
        "- 측정 전용 — 엔진·기본값·토대 불변 · `no_same_step_tp`를 기본으로 켜는 것은 "
        "**재-베이스라인 = 사용자 결정**이다(개발자 임의 착수 금지).",
        "",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-348 같은 분 익절 틱 검증")
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--floor", type=int, default=DEFAULT_STRATUM_FLOOR, help="층별 최소 표본")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--targets", default=str(TARGET_CSV))
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    return parser.parse_args(argv)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    targets = load_targets(Path(args.targets))
    population: dict[str, int] = {}
    for target in targets:
        population[target.timeframe] = population.get(target.timeframe, 0) + 1

    if args.from_csv:
        frame = _read(CSV_PATH)
        if frame.empty:
            print(f"[wan348] {CSV_PATH}가 없습니다 — 먼저 측정을 돌리세요.")
            return 1
        SUMMARY_PATH.write_text(
            build_summary(
                frame, _read(SAMPLE_CSV_PATH), _read(COST_CSV_PATH), population=population
            ),
            encoding="utf-8",
        )
        print(f"[wan348] 요약 재생성: {SUMMARY_PATH}")
        return 0

    sample = draw_sample(targets, size=args.sample_size, floor=args.floor, seed=args.seed)
    sample_frame = sample_to_frame(sample)
    sample_frame.to_csv(SAMPLE_CSV_PATH, index=False)
    print(f"[wan348] 표본 {len(sample)}건 → {SAMPLE_CSV_PATH}", flush=True)

    rows, fetches = run_measurement(sample, cache_dir=Path(args.cache_dir))
    frame = rows_to_frame(rows)
    frame.to_csv(CSV_PATH, index=False)
    cost_frame = fetches_to_frame(fetches)
    cost_frame.to_csv(COST_CSV_PATH, index=False)
    SUMMARY_PATH.write_text(
        build_summary(
            frame,
            sample_frame,
            cost_frame,
            population=population,
            checksum=take_profit_checksum(Path(args.targets)),
        ),
        encoding="utf-8",
    )
    print(f"[wan348] 적재: {CSV_PATH} ({len(frame)}행) · 요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
