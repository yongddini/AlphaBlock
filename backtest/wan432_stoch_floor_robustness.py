"""WAN-432: 스토캐스틱 팔의 **손절폭 하한**이 뒷구간으로 넘어가는가 — 6점 스윕(9TF).

## 왜 이 모듈이 있나

이 저장소에서 매칭 널을 **앞뒤 구간 모두** 통과한 것은 스토캐스틱 팔의 **시각 축** 하나뿐이다
(WAN-424 §2 `B_시각`: 앞구간 p=0.000 → 뒷구간 p=0.000). 선별 축은 두 번 부정됐다(같은 표의
`A_선별` p=0.140 · WAN-428 §3 캡 널 4/4 실패). 그래서 **남은 후보는 그 팔 하나**다.

그런데 그 팔은 자유 파라미터가 넷이고 전부 표를 보고 고른 값이다 — **손절폭 하한**(3%/4%) ·
**%K 문턱**(15/20/25) · **보유 봉**(4~24) · **TF 집합**(1h~1w). 이 모듈은 그중 **앞구간에서
고른 티가 가장 진한 하한**을 흔들어 뒷구간으로 넘어가는지 본다.

🚨 **15m에서는 이미 깨지는 것을 봤다**(WAN-430 §2): 앞구간은 하한을 올릴수록 **단조로** 좋아져
+0.50R까지 가는데 뒷구간에서는 그 개선이 하나도 안 남는다(표본이 있는 0.5%는 음수, 위쪽은
미결정). 9TF에서 같은 일이 일어나면 **이 팔의 하한은 앞구간에서 고른 값**이고, 그러면 그 위에
쌓을 작업(사이징·페이퍼 배선)이 전부 의미를 잃는다.

## 축은 하나다

흔드는 것은 **하한 하나**이고(WAN-394), `%K` 문턱·보유 봉·TF 집합은 WAN-424 §1의 값을
**그대로 병기**한다(새로 늘리지 않는다). 하한 점도 WAN-430 §2와 **같은 여섯 점**이라 자를 새로
쓰지 않는다.

## 왜 싼가 — 한 번 낮게 만들어 두고 올려 거른다

팔 후보를 **하한 0.5%로 한 번만** 만들어 두면(`ARM_FLOOR`) 그보다 높은 하한은
`filtered_payloads`가 **후보를 빼는 것**으로 표현된다 — 점을 늘리는 것이 배치 비용(조합당 초
단위)뿐이다. WAN-430 §2가 쓴 방법이고, 그 등식이 성립하는 이유는 `time_exit`이 후보마다
독립이라 풀이 커져도 **남는 후보의 청산이 안 바뀌기** 때문이다. 그 성질은 주장이 아니라
**검산**이다 — 하한 4% 행이 WAN-424 §1 공개 CSV와 **비트 일치**해야 한다(`checksum_wan424`).

## `_cell_arms`를 복제한 이유 (기록) — 그리고 남은 정리

이 표를 돌릴 당시 WAN-424의 워커 `_cell_arms`는 팔 풀을 `STOP_WIDTH_LOG_FLOOR`(= 3%)로
**하드코딩**해 만들었다. 이 이슈는 0.5%가 필요한데 그 모듈에 `min_width` 인자를 다는 변경을
**WAN-430이 같은 파일에서 이미 하고 있어**(그때는 미머지) 같은 리팩터를 두 브랜치에서 하면
머지 충돌이었다. 그래서 이 모듈은 `wan424`의 **순수 함수를 그대로 빌려** 자기 워커를 둔다.
복제는 갈라질 위험이 있으므로 `tests/test_wan432_stoch_floor_robustness.py`가 **같은 하한에서
두 워커의 산출이 같은 객체**임을 실데이터로 고정한다(라벨이 아니라 동작으로).

📌 **WAN-430은 그 뒤 머지됐다**(PR #340) — 지금 `wan424.build_arm_cells(min_width=)`가 존재하고
이 워커는 그쪽으로 **갈아끼울 수 있다**. 이 PR에서 하지 않은 이유는 그 교체가 표를 만든 계산
경로를 바꾸는데 **재검증에 3시간짜리 격자 재실행이 필요**하기 때문이다(공개 CSV는 그 경로의
산출물이다). 위 복제 가드가 머지된 상판과도 **그대로 통과**하므로 갈라지지는 않는다 —
교체는 **별건**이다.

## 판정은 코드가 낸다

사람이 표를 보고 정하지 않는다 — `floor_argmax`가 구간별 최적 하한을 뽑고 `flip_census`가
**앞구간 argmax와 뒷구간 argmax가 같은지**를 (문턱 × 보유) 조합마다 센다. ⚠️ **뒷구간
argmax는 채택 근거가 아니다**(WAN-161) — 뒤집힘을 세는 데만 쓴다.

## 재현

```
uv run python -m backtest.wan432_stoch_floor_robustness --jobs 3   # 팔 후보(무겁다) + 표
uv run python -m backtest.wan432_stoch_floor_robustness --from-csv # 표만 다시
uv run python -m backtest.wan432_stoch_floor_robustness --from-csv --elapsed 10635  # 공개 요약
```

⚠️ CSV에는 시간이 없다 — `--from-csv`만 주면 실측 비용 줄을 **지어내지 않고 뺀다**. 공개
요약은 완주 실행이 찍은 값(**10,635초 = 177분**)을 `--elapsed`로 되살려 만든 것이다.

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 핀 없음 WAN-305) · 이 팔은 **채택 좌표가 아니다**(채택 북
−0.12R과 나란히 놓지 말 것) · 전부 `pen_5bp` × 같은 분 익절 금지 위의 값 · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments, net_r
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS, SEGMENT_OOS_WARM
from backtest.leverage_book import LeverageBookParams
from backtest.run import parse_date_ms
from backtest.substep import build_substeps
from backtest.wan169_leverage_book import IS_SEGMENT, OOS_SEGMENT, CellPayload
from backtest.wan370_cost_decomposition import decompose_trade

# 🚨 아래 다섯은 **의도적 재노출**이다 — 이 이슈가 흔드는 축은 하한 하나이고 나머지 축(문턱·
# 보유 봉·TF·종목·구간)은 WAN-424 §1의 값을 그대로 물려받는다. `as` 형태가 「여기서 다시
# 정의하지 않았다」를 타입 수준에서 못 박는다 — 값을 베껴 쓰면 두 표의 자가 조용히 갈라진다.
from backtest.wan424_stoch_ob_arm import HOLD_BARS as HOLD_BARS
from backtest.wan424_stoch_ob_arm import SEGMENTS as SEGMENTS
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS
from backtest.wan424_stoch_ob_arm import THRESHOLDS as THRESHOLDS
from backtest.wan424_stoch_ob_arm import TIMEFRAMES as TIMEFRAMES
from backtest.wan424_stoch_ob_arm import (
    ArmCell,
    _equity_path,
    arm_pool,
    build_base_payloads,
    derive_hold_arms,
    filtered_payloads,
    stoch_series,
)
from backtest.zone_limit_backtest import _Candidate
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

REPORT_DIR = Path(__file__).resolve().parent / "reports"
CSV_PATH = REPORT_DIR / "wan432_stoch_floor_robustness.csv"
SUMMARY_PATH = REPORT_DIR / "wan432_stoch_floor_robustness_summary.md"
DEFAULT_PAYLOAD_DIR = Path(__file__).resolve().parent / "cache" / "wan424_payloads"
WAN424_CSV = REPORT_DIR / "wan424_stoch_ob_arm.csv"

#: 흔드는 유일한 축 — WAN-430 §2와 **같은 여섯 점**(자를 새로 쓰지 않는다).
FLOORS: tuple[float, ...] = (0.005, 0.01, 0.015, 0.02, 0.03, 0.04)

#: 팔 후보를 만들 때 쓰는 하한 — 가장 낮은 점. 위쪽 점은 `filtered_payloads`가 빼서 만든다.
ARM_FLOOR = min(FLOORS)

#: WAN-424 §1이 채택값으로 쓴 하한 — 완료기준 3(뒷구간 몇 등인가)의 대상.
ADOPTED_FLOOR = 0.04

#: 부호 판정 자 — `|평균| > 2σ`면 「결정」(WAN-381/412 관문). 새로 쓰는 자가 아니다.
SIGN_SIGMA = 2.0

#: 「0과 구분되지 않는다」 선 — WAN-366/370 규약 그대로.
NOISE_R = 0.005

#: 검산 (4)의 합격선 — CSV 텍스트 왕복 끝자리(부동소수 repr)까지만 허용한다.
#:
#: 🚨 **보고 마크와 종료 코드가 이 상수 하나를 함께 읽는다.** 둘이 갈라지면 같은 실행이
#: 표에서는 「≈ 통과」인데 셸에서는 **실패**로 보인다 — 이 저장소가 반복해 잡아 온
#: 「실패가 성공과 같은 모양」(WAN-194/318 §3/321)의 **거울상**이고, 실제로 이 모듈의
#: 첫 완주(하한 4% × 45행 최대 절대차 `1.78e-15`)가 그렇게 종료 코드 1을 냈다.
CHECKSUM_TOL = 1e-9


# ---------------------------------------------------------------------------
# 팔 후보 — WAN-424의 순수 함수를 그대로 쓰되 하한만 인자로 받는다
# ---------------------------------------------------------------------------


def cell_arms_at(task: tuple[CellPayload, float]) -> ArmCell:
    """워커: 한 칸의 창을 다시 읽어 구간별 서브스텝으로 시간 청산을 푼다(하한 `min_width`).

    🚨 `wan424._cell_arms`와 **같은 계산**이고 다른 것은 풀 하한 하나다 — 그 동일성을
    회귀 테스트가 실데이터로 고정한다(모듈 독스트링 「복제한 이유」).
    """
    payload, min_width = task
    start_ms = parse_date_ms(harness.DEFAULT_START)
    end_ms = parse_date_ms(harness.DEFAULT_END)
    market = harness.load_market_data(
        payload.symbol, payload.timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True
    )
    htf_ms = timeframe_to_ms(payload.timeframe)
    windows = {
        SEGMENT_FULL: market,
        SEGMENT_IS: harness.slice_market(market, IS_SEGMENT),
        SEGMENT_OOS: harness.slice_market(market, OOS_SEGMENT),
    }
    arms: dict[int, dict[str, tuple[_Candidate, ...]]] = {h: {} for h in HOLD_BARS}
    for segment, window in windows.items():
        pool = arm_pool(payload.candidates.get(segment, ()), min_width=min_width)
        substeps = build_substeps(window.df_1m, htf_ms) if pool else []
        derived = derive_hold_arms(pool, substeps=substeps) if pool else {h: [] for h in HOLD_BARS}
        for h, cands in derived.items():
            arms[h][segment] = tuple(cands)
    times, k = stoch_series(OhlcvStore(harness.DB_PATH), payload.symbol, payload.timeframe)
    return ArmCell(payload.symbol, payload.timeframe, arms, tuple(times), tuple(k))


def build_arm_cells(
    payloads: Sequence[CellPayload], *, jobs: int, min_width: float = ARM_FLOOR
) -> list[ArmCell]:
    tasks = [(p, min_width) for p in payloads]
    if jobs <= 1:
        return [cell_arms_at(t) for t in tasks]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(cell_arms_at, tasks))


# ---------------------------------------------------------------------------
# 한 조합의 배치 — net R과 비용 분해를 **같은 배치에서** 낸다
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FloorRow:
    """(보유 × 하한 × 문턱 × 구간) 한 칸."""

    hold: int
    floor: float
    threshold: float | None
    segment: str
    num_trades: int
    win_rate: float
    mean_net_r: float
    se_net_r: float
    gross_r: float
    """수수료·슬리피지·펀딩 전 가격 손익(거래당 R) — 「시장에서 얻은 것」(WAN-370 자)."""
    cost_r: float
    """거래당 총비용 R(≥0). `gross_r − cost_r ≈ mean_net_r`이 닫혀야 한다."""
    identity_max_abs: float
    """검산 — 거래별 `gross − 비용 − net`의 최대 절댓값(R). 0이 아니면 분해가 틀렸다."""
    median_stop_width: float
    fixed_return: float
    fixed_mdd: float
    compound_return: float
    compound_mdd: float
    compound_ruined: bool

    @property
    def sign_is_decided(self) -> bool:
        """`|평균| > 2σ`인가 — 이 저장소의 부호 관문(WAN-381/412)."""
        if self.num_trades < 2 or math.isnan(self.se_net_r):
            return False
        return abs(self.mean_net_r) > SIGN_SIGMA * self.se_net_r


def book_segments(payloads: Sequence[CellPayload]) -> list[BookSegment]:
    """🚨 이 팔의 북 인자가 사는 **유일한 자리** — `wan424.place`와 같은 값이어야 한다.

    두 벌로 갈라지면 「같은 팔로 쟀다」가 거짓이 되므로(WAN-95/112/123) 회귀 테스트가
    `wan424.place`와의 동일성을 실데이터로 건다.
    """
    return iter_book_segments(
        payloads,
        book=LeverageBookParams(),
        segments=SEGMENTS,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        include_reentry=False,
        compound_sizing=False,
        min_stop_distance_fraction=0.0,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def place(
    payloads: Sequence[CellPayload], *, hold: int, floor: float, threshold: float | None
) -> list[FloorRow]:
    """한 조합을 채택 북(한 지갑 · 복리 끔 · 가드 끔)에 배치해 구간별 행을 낸다."""
    rows: list[FloorRow] = []
    for seg in book_segments(payloads):
        cfg = seg.outcome.effective_config
        pairs = sorted(
            ((t.exit_time, t, p) for t, p in seg.trades_with_placements() if p.risk_amount > 0.0),
            key=lambda x: x[0],
        )
        rs = [net_r(t, p) for _, t, p in pairs]
        n = len(rs)
        mean = sum(rs) / n if n else math.nan
        se = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1) / n) if n > 1 else math.nan
        gross = cost = worst = 0.0
        wins = 0
        widths: list[float] = []
        for _, trade, placement in pairs:
            risk = placement.risk_amount
            parts = decompose_trade(trade, cfg)
            gross += parts.gross / risk
            cost += parts.total_cost / risk
            worst = max(worst, abs(parts.residual) / risk)
            wins += 1 if trade.realized_pnl > 0 else 0
            if trade.entry_price > 0.0:
                widths.append(abs(trade.entry_price - placement.stop_price) / trade.entry_price)
        f_ret, f_mdd, _ = _equity_path(rs, compound=False)
        c_ret, c_mdd, ruined = _equity_path(rs, compound=True)
        rows.append(
            FloorRow(
                hold=hold,
                floor=floor,
                threshold=threshold,
                segment=seg.segment,
                num_trades=n,
                win_rate=wins / n if n else math.nan,
                mean_net_r=mean,
                se_net_r=se,
                gross_r=gross / n if n else math.nan,
                cost_r=cost / n if n else math.nan,
                identity_max_abs=worst,
                median_stop_width=float(pd.Series(widths).median()) if widths else math.nan,
                fixed_return=f_ret,
                fixed_mdd=f_mdd,
                compound_return=c_ret,
                compound_mdd=c_mdd,
                compound_ruined=ruined,
            )
        )
    return rows


def run_grid(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    *,
    floors: Sequence[float] = FLOORS,
    thresholds: Sequence[float] = THRESHOLDS,
    holds: Sequence[int] = HOLD_BARS,
    progress: bool = False,
) -> list[FloorRow]:
    rows: list[FloorRow] = []
    total = len(floors) * len(thresholds) * len(holds)
    done = 0
    for floor in floors:
        for thr in thresholds:
            for hold in holds:
                rows.extend(
                    place(
                        filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=thr),
                        hold=hold,
                        floor=floor,
                        threshold=thr,
                    )
                )
                done += 1
                if progress:
                    print(
                        f"  배치 {done}/{total} (하한{floor:.1%} K<{thr:.0f} ts{hold})", flush=True
                    )
    return rows


# ---------------------------------------------------------------------------
# 판정 — 사람이 표를 보고 정하지 않는다
# ---------------------------------------------------------------------------


def floor_argmax(
    rows: Sequence[FloorRow], *, threshold: float, hold: int, segment: str
) -> FloorRow | None:
    """그 (문턱 × 보유 × 구간)에서 거래당 net R이 가장 큰 하한의 행.

    ⚠️ 표본이 얇아도 뺀 것이 아니다 — 고른 칸의 거래 수·부호 결정 여부를 **함께** 찍는다
    (WAN-430 §2의 「20거래짜리 4%가 1등」 함정을 감추지 않기 위해서다).
    """
    cands = [
        r
        for r in rows
        if r.threshold == threshold and r.hold == hold and r.segment == segment and r.num_trades > 0
    ]
    return max(cands, key=lambda r: r.mean_net_r) if cands else None


def floor_rank(
    rows: Sequence[FloorRow], *, threshold: float, hold: int, segment: str, floor: float
) -> int | None:
    """그 조합에서 `floor`가 몇 등인가(1 = 최선). 행이 없으면 `None`."""
    cands = sorted(
        (r for r in rows if r.threshold == threshold and r.hold == hold and r.segment == segment),
        key=lambda r: r.mean_net_r,
        reverse=True,
    )
    for i, r in enumerate(cands, start=1):
        if r.floor == floor:
            return i
    return None


@dataclass(frozen=True)
class FlipRow:
    """한 (문턱 × 보유) 조합의 앞구간 argmax ↔ 뒷구간 argmax."""

    threshold: float
    hold: int
    is_floor: float | None
    is_net_r: float
    is_trades: int
    oos_floor: float | None
    oos_net_r: float
    oos_trades: int
    oos_sign_decided: bool
    adopted_oos_rank: int | None

    @property
    def flipped(self) -> bool:
        return self.is_floor != self.oos_floor


def flip_census(
    rows: Sequence[FloorRow],
    *,
    thresholds: Sequence[float] = THRESHOLDS,
    holds: Sequence[int] = HOLD_BARS,
) -> list[FlipRow]:
    out: list[FlipRow] = []
    for thr in thresholds:
        for hold in holds:
            best_is = floor_argmax(rows, threshold=thr, hold=hold, segment="is")
            best_oos = floor_argmax(rows, threshold=thr, hold=hold, segment="oos_warm")
            out.append(
                FlipRow(
                    threshold=thr,
                    hold=hold,
                    is_floor=best_is.floor if best_is else None,
                    is_net_r=best_is.mean_net_r if best_is else math.nan,
                    is_trades=best_is.num_trades if best_is else 0,
                    oos_floor=best_oos.floor if best_oos else None,
                    oos_net_r=best_oos.mean_net_r if best_oos else math.nan,
                    oos_trades=best_oos.num_trades if best_oos else 0,
                    oos_sign_decided=bool(best_oos and best_oos.sign_is_decided),
                    adopted_oos_rank=floor_rank(
                        rows, threshold=thr, hold=hold, segment="oos_warm", floor=ADOPTED_FLOOR
                    ),
                )
            )
    return out


def verdict(flips: Sequence[FlipRow]) -> str:
    """헤드라인 — 하한이 구간을 넘어가는가. 코드가 낸다(사람이 표를 보고 정하지 않는다).

    🚨 **뒤집힘 「수」만 세면 방향을 놓친다.** 「앞구간에서 고른 값이다」가 성립하려면 뒤집힘이
    *앞구간이 고른 쪽으로* 유리해야 하는데, 뒤집힘이 전부 **반대쪽**(앞구간은 더 느슨한 하한을
    고르고 뒷구간은 더 조인 하한을 고른다)이면 그 문장은 **데이터와 반대**가 된다. 헤드라인은
    인용되는 줄이라 그대로 두면 이 저장소가 반복해 잡아 온 「라벨과 동작이 어긋남」
    (WAN-91/95/112/123/159/393)이 된다. 그래서 개수와 **방향**을 함께 본다.

    ⚠️ 가르는 선은 **다수결과 「내린 뒤집힘이 하나도 없다」** 둘뿐이다 — 표를 보고 고른 문턱이
    아니라 구조적인 선이다(WAN-161: 결과를 보고 선을 옮기지 않는다). ⚠️ 어느 갈래도
    **채택 근거가 아니다** — 뒷구간 argmax는 뒤집힘을 세는 데만 쓴다.
    """
    if not flips:
        return "⚠️ 판정 불가 — 조합이 없다."
    total = len(flips)
    flipped = [f for f in flips if f.flipped]
    n = len(flipped)
    if n == 0:
        return f"**넘어간다** — {total}조합 전부 앞구간 argmax = 뒷구간 argmax."

    raised = sum(
        1
        for f in flipped
        if f.oos_floor is not None and f.is_floor is not None and f.oos_floor > f.is_floor
    )
    lowered = sum(
        1
        for f in flipped
        if f.oos_floor is not None and f.is_floor is not None and f.oos_floor < f.is_floor
    )
    toward_adopted = sum(1 for f in flips if f.oos_floor == ADOPTED_FLOOR)

    # 뒤집히되 **전부 조이는 쪽**이고 뒷구간 argmax가 **채택값**인 조합이 다수면,
    # 「앞구간에서 고른 값」이라는 읽기는 성립하지 않는다 — 앞구간은 채택값을 고르지 않았다.
    if lowered == 0 and raised == n and toward_adopted * 2 > total:
        return (
            f"🚨 **뒤집히지만 방향이 반대다** — {total}조합 중 {n}개에서 앞구간 argmax ≠ 뒷구간 "
            f"argmax인데, 그 **{n}개 전부 뒷구간이 더 조인 하한을 고르고**(내려간 뒤집힘 0개) "
            f"뒷구간 argmax가 {toward_adopted}/{total}조합에서 **채택값"
            f"({ADOPTED_FLOOR:.0%}) 그 자체**다. 즉 앞구간은 더 **느슨한** 하한을 고르므로 "
            "「하한이 앞구간에서 고른 값」은 이 표에서 성립하지 않는다. "
            "⚠️ **그렇다고 채택값이 검증된 것도 아니다** — 뒷구간 argmax는 채택 근거가 아니고"
            "(WAN-161) 하필 그 칸이 **가장 얇다**(하한이 높을수록 거래가 적다)."
        )
    if n == total:
        return (
            f"🚨 **앞구간에서 고른 값이다** — {total}조합 **전부** 뒤집힌다"
            "(앞구간 최적 하한이 뒷구간에서 최적이 아니다)."
        )
    return (
        f"🚨 **대체로 앞구간에서 고른 값이다** — {total}조합 중 **{n}개**가 뒤집힌다"
        f"({total - n}개만 유지 · 올린 뒤집힘 {raised} · 내린 뒤집힘 {lowered})."
    )


@dataclass(frozen=True)
class SplitRow:
    """한 구간에서 「하한을 바닥에서 채택값까지 올리면」 좋아진 몫의 출처."""

    segment: str
    delta_net: float
    """거래당 net R의 변화(채택 하한 − 바닥 하한). 조합 평균."""
    delta_gross: float
    """그중 **시장 몫** — gross R(수수료·슬리피지·펀딩 전)의 변화."""
    delta_cost: float
    """그중 **비용 몫** — 거래당 비용 R의 **감소**(손절폭이 넓어져 같은 수수료의 R 비중이 준다)."""
    combos: int

    @property
    def gross_share(self) -> float:
        """시장 몫의 비율. `delta_net`이 0 근처면 뜻을 잃는다(`share_is_readable`)."""
        return self.delta_gross / self.delta_net if self.delta_net else math.nan

    @property
    def share_is_readable(self) -> bool:
        """🚨 비율이 **읽으면 틀리는** 경우에는 내지 않는다(WAN-115/395 부호 함정 관행).

        둘을 막는다 — 분모(`delta_net`)가 잡음선 안이거나, **분자와 분모의 부호가 갈릴 때**다.
        후자에서 나오는 「시장 몫 −32%」는 비율이 아니라 *시장 몫이 반대로 갔다*는 뜻인데
        퍼센트로 찍으면 크기가 있는 양처럼 읽힌다. 그때는 `—`로 두고 옆의 원값
        (`Δgross`)이 말하게 한다.
        """
        if abs(self.delta_net) <= NOISE_R:
            return False
        return (self.delta_gross >= 0) == (self.delta_net >= 0)


def cost_vs_market(
    rows: Sequence[FloorRow],
    *,
    low: float = ARM_FLOOR,
    high: float = ADOPTED_FLOOR,
    segments: Sequence[str] = SEGMENTS,
) -> list[SplitRow]:
    """하한을 올려 좋아지는 것이 **비용을 덜 내는 것인지 시장 몫인지** 가른다(이슈 사양).

    🚨 **이 갈래가 필요한 이유**: 하한을 올리면 손절폭이 넓어져 **같은 수수료의 R 비중이
    기계적으로 준다**(WAN-370: 손절폭 5분위로 비용 R이 4.5배). 그러면 net R은 시장에서 아무것도
    얻지 못해도 올라간다 — 표에 gross R을 나란히 두는 것만으로는 그 구분이 **문장으로 안 나온다**.
    """
    index = {(r.hold, r.floor, r.threshold, r.segment): r for r in rows}
    holds = sorted({r.hold for r in rows})
    thresholds = sorted({r.threshold for r in rows if r.threshold is not None})
    out: list[SplitRow] = []
    for seg in segments:
        dn = dg = dc = 0.0
        k = 0
        for thr in thresholds:
            for hold in holds:
                lo = index.get((hold, low, thr, seg))
                hi = index.get((hold, high, thr, seg))
                if lo is None or hi is None or lo.num_trades == 0 or hi.num_trades == 0:
                    continue
                dn += hi.mean_net_r - lo.mean_net_r
                dg += hi.gross_r - lo.gross_r
                dc += lo.cost_r - hi.cost_r
                k += 1
        if k:
            out.append(
                SplitRow(
                    segment=seg, delta_net=dn / k, delta_gross=dg / k, delta_cost=dc / k, combos=k
                )
            )
    return out


def split_reading(splits: Sequence[SplitRow]) -> str:
    """§비용인가 시장인가의 한 줄 — **표에서 파생한다**(문장에 숫자를 손으로 박지 않는다).

    🚨 손으로 박으면 데이터가 움직였을 때 **문장만 낡는다** — 이 저장소가 반복해 잡아 온
    「라벨과 동작이 어긋남」(WAN-91/95/112/123/159)의 산문 축이다.
    """
    by = {sp.segment: sp for sp in splits}
    # 🚨 뒷구간은 **따뜻한** `oos_warm`이다(WAN-166 정본) — `harness.SEGMENT_OOS`(= 차가운
    # `"oos"`)를 쓰면 키가 안 맞아 조용히 「판정하지 않는다」로 접힌다. 실제로 한 번 그랬다.
    is_row, oos_row = by.get(SEGMENT_IS), by.get(SEGMENT_OOS_WARM)
    if is_row is None or oos_row is None:
        return "⚠️ 앞뒤 구간이 다 있어야 이 갈래를 읽는다 — 한쪽이 없어 판정하지 않는다."
    cost_carries = abs(oos_row.delta_cost - is_row.delta_cost) <= NOISE_R * 2
    market_carries = (is_row.delta_gross > NOISE_R) and (oos_row.delta_gross > NOISE_R)
    if market_carries:
        return (
            f"📌 **시장 몫도 구간을 넘어간다** — Δgross가 앞구간 {is_row.delta_gross:+.3f} · "
            f"뒷구간 {oos_row.delta_gross:+.3f}로 둘 다 잡음선 밖이다."
        )
    return (
        f"🚨 **답은 「아니다」 — 구간을 넘어가는 것은 기계적인 비용 절감뿐이다.** 비용 몫은 앞뒤가 "
        f"{'거의 같은데' if cost_carries else '같은 방향인데'}(앞 {is_row.delta_cost:+.3f} ↔ "
        f"뒤 {oos_row.delta_cost:+.3f}) **시장 몫은 뒷구간에만 있다**"
        f"(앞 {is_row.delta_gross:+.3f} ↔ 뒤 {oos_row.delta_gross:+.3f}). "
        "⚠️ 즉 뒷구간의 큰 개선을 「하한이 좋은 자리를 고른다」로 읽을 근거가 이 표에 없다 — "
        "같은 자를 앞구간에 대면 그 몫이 **사라진다**."
    )


# ---------------------------------------------------------------------------
# 검산 — 하한 4% 행이 WAN-424 §1 공개 CSV와 비트 일치하는가
# ---------------------------------------------------------------------------

#: 두 표가 공유하는 열 — WAN-424 §1 `ArmRow`에 있는 것만 본다(비용 열은 그쪽에 없다).
SHARED_COLUMNS: tuple[str, ...] = (
    "num_trades",
    "mean_net_r",
    "se_net_r",
    "fixed_return",
    "fixed_mdd",
    "compound_return",
    "compound_mdd",
)


def on_adopted_coordinates(
    *, symbols: Sequence[str], timeframes: Sequence[str], holds: Sequence[int]
) -> bool:
    """이 실행이 WAN-424 §1 좌표를 그대로 도는가 — 검산 (4)가 성립하는 유일한 조건.

    🚨 좁혀 돈 파일럿을 공개 CSV와 대조하면 **좌표 차이가 배선 오류처럼 보인다**(WAN-381이
    실제로 겪었다). 그때는 대조하지 않고 「건너뜀」으로 찍는다.
    """
    return (
        tuple(symbols) == SYMBOLS and tuple(timeframes) == TIMEFRAMES and tuple(holds) == HOLD_BARS
    )


def checksum_wan424(
    rows: Sequence[FloorRow], *, reference_csv: Path = WAN424_CSV, adopted: bool = True
) -> tuple[list[str], float]:
    """하한 4% 행 ↔ WAN-424 §1 공개 CSV. (보고 줄, 최대 절대차)를 낸다.

    🚨 이 등식이 이 이슈의 자격 증명이다 — 「하한을 낮춰 풀을 키운 뒤 다시 걸러 올린 것」이
    「처음부터 그 하한으로 만든 것」과 같아야 스윕이 기존 칸을 안 움직인 것이다.

    `adopted=False`(좁혀 돈 판)면 **대조하지 않고 건너뛴다** — `on_adopted_coordinates` 참고.
    """
    if not adopted:
        return ["⏭️ 건너뜀 — 좁혀 돈 좌표라 공개 CSV와의 대조가 성립하지 않는다(WAN-381)."], math.nan
    if not reference_csv.exists():
        return [f"⚠️ 기준 CSV 없음: {reference_csv}"], math.nan
    ref = pd.read_csv(reference_csv)
    ref = ref[ref["floor"].round(6) == round(ADOPTED_FLOOR, 6)]
    index = {
        (int(r["hold"]), float(r["threshold"]), str(r["segment"])): r for _, r in ref.iterrows()
    }
    lines: list[str] = []
    worst = 0.0
    compared = 0
    for row in rows:
        if round(row.floor, 6) != round(ADOPTED_FLOOR, 6) or row.threshold is None:
            continue
        key = (row.hold, float(row.threshold), row.segment)
        want = index.get(key)
        if want is None:
            lines.append(f"❌ 기준 행 없음: ts{row.hold} K<{row.threshold:.0f} {row.segment}")
            continue
        compared += 1
        for col in SHARED_COLUMNS:
            got = float(getattr(row, col))
            exp = float(want[col])
            if math.isnan(got) and math.isnan(exp):
                continue
            worst = max(worst, abs(got - exp))
    if compared == 0:
        lines.append("❌ 대조한 행이 하나도 없다 — 좌표가 다르면 이 검산은 성립하지 않는다.")
    else:
        mark = "✅" if worst == 0.0 else ("≈" if worst < CHECKSUM_TOL else "❌")
        lines.append(
            f"{mark} 하한 {ADOPTED_FLOOR:.0%} × {compared}행 × {len(SHARED_COLUMNS)}열 — "
            f"최대 절대차 {worst:.2e}"
        )
    return lines, worst


def checksum_passes(worst: float) -> bool:
    """검산 (4)가 합격인가 — **보고 마크와 종료 코드가 같은 자를 쓰게 하는 한 곳**.

    🚨 `worst == 0.0`을 요구하면 **CSV 텍스트 왕복 끝자리**(`1.78e-15`)에 걸려, 표에는
    「≈ 통과」라 찍고 셸에는 실패를 내는 실행이 나온다. `math.nan`(대조 건너뜀)은 합격이다 —
    좁혀 돈 파일럿은 애초에 이 등식을 물을 좌표가 아니다(`on_adopted_coordinates`).
    """
    return math.isnan(worst) or worst < CHECKSUM_TOL


# ---------------------------------------------------------------------------
# 표
# ---------------------------------------------------------------------------


def rows_to_frame(rows: Sequence[FloorRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_rows(frame: pd.DataFrame) -> list[FloorRow]:
    fields = {f.name for f in dataclasses.fields(FloorRow)}
    out: list[FloorRow] = []
    for rec in frame.to_dict("records"):
        rec = {k: v for k, v in rec.items() if k in fields}
        thr = rec["threshold"]
        rec["threshold"] = (
            None if thr is None or (isinstance(thr, float) and math.isnan(thr)) else float(thr)
        )
        rec["compound_ruined"] = bool(rec["compound_ruined"])
        rec["hold"] = int(rec["hold"])
        rec["num_trades"] = int(rec["num_trades"])
        out.append(FloorRow(**rec))
    return out


def _fmt_r(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:+.3f}"


def _cell(row: FloorRow | None) -> str:
    if row is None or row.num_trades == 0:
        return "—"
    mark = (
        "＋"
        if row.sign_is_decided and row.mean_net_r > 0
        else ("−" if row.sign_is_decided and row.mean_net_r < 0 else "")
    )
    return f"{row.mean_net_r:+.3f}{mark} ±{2 * row.se_net_r:.3f} ({row.num_trades})"


def render_summary(
    rows: Sequence[FloorRow],
    *,
    elapsed: float | None = None,
    floors: Sequence[float] = FLOORS,
    adopted: bool = True,
) -> str:
    index = {(r.hold, r.floor, r.threshold, r.segment): r for r in rows}
    flips = flip_census(
        rows,
        thresholds=tuple(sorted({r.threshold for r in rows if r.threshold is not None})),
        holds=tuple(sorted({r.hold for r in rows})),
    )
    check_lines, _worst = checksum_wan424(rows, adopted=adopted)
    lines = [
        "# WAN-432 — 스토캐스틱 팔의 손절폭 하한이 뒷구간으로 넘어가는가 (9TF)",
        "",
        "롱 · 31종목 × 9TF(1h~1w) · 못 박은 6년 · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 "
        "익절 금지 · 채택 북 · 복리 끔 · 가드 끔(손절폭 하한이 대신) · **핀 없음**.",
        "판정 자 = **거래당 net R ± 2σ**(`＋`/`−` = 부호 결정 · 표기 없으면 미결정).",
        "",
        f"## 판정 — {verdict(flips)}",
        "",
        "🚨 **뒷구간 argmax는 채택 근거가 아니다**(WAN-161) — 뒤집힘을 세는 데만 쓴다.",
        "",
        "## 앞구간 argmax ↔ 뒷구간 argmax (완료기준 2)",
        "",
        "| %K | 보유 | 앞구간 최적 하한 | 앞 net R (거래) | 뒷구간 최적 하한 | 뒤 net R (거래) "
        "| 뒤집힘 | 채택값(4%) 뒷구간 등수 |",
        "| -- | -- | --: | --: | --: | --: | :--: | --: |",
    ]
    for f in flips:
        lines.append(
            f"| <{f.threshold:.0f} | ts{f.hold} "
            f"| {'—' if f.is_floor is None else f'{f.is_floor:.1%}'} "
            f"| {_fmt_r(f.is_net_r)} ({f.is_trades}) "
            f"| {'—' if f.oos_floor is None else f'{f.oos_floor:.1%}'} "
            f"| {_fmt_r(f.oos_net_r)}{'＋' if f.oos_sign_decided and f.oos_net_r > 0 else ''} "
            f"({f.oos_trades}) "
            f"| {'🚨 예' if f.flipped else '아니오'} "
            f"| {'—' if f.adopted_oos_rank is None else f'{f.adopted_oos_rank}/{len(floors)}'} |"
        )
    ranks = [f.adopted_oos_rank for f in flips if f.adopted_oos_rank is not None]
    if ranks:
        lines += [
            "",
            f"📌 **완료기준 3 — 채택값(하한 {ADOPTED_FLOOR:.0%})의 뒷구간 등수**: "
            f"중앙값 **{pd.Series(ranks).median():.1f}/{len(floors)}** · "
            f"1등 {sum(1 for r in ranks if r == 1)}/{len(ranks)}조합 · "
            f"꼴찌 {sum(1 for r in ranks if r == len(floors))}/{len(ranks)}조합.",
        ]
    splits = cost_vs_market(rows)
    if splits:
        lines += [
            "",
            f"## 비용인가 시장인가 — 하한 {ARM_FLOOR:.1%} → {ADOPTED_FLOOR:.0%}의 몫 가르기",
            "",
            "| 구간 | Δ거래당 net R | 그중 시장(Δgross) | 그중 비용(Δ비용 감소) | 시장 몫 | 조합 |",
            "| -- | --: | --: | --: | --: | --: |",
        ]
        for sp in splits:
            share = f"{sp.gross_share:.0%}" if sp.share_is_readable else "—"
            lines.append(
                f"| {sp.segment} | {sp.delta_net:+.3f} | {sp.delta_gross:+.3f} "
                f"| {sp.delta_cost:+.3f} | {share} | {sp.combos} |"
            )
        lines += [
            "",
            "🚨 **비용 몫은 기계적이다** — 하한을 올리면 손절폭이 넓어져 같은 수수료의 R 비중이 "
            "그냥 준다(WAN-370). 그래서 읽을 것은 **시장 몫이 구간을 넘어가는가**다.",
            "",
            split_reading(splits),
            "",
            "⚠️ 시장 몫 비율은 분모(Δnet)가 잡음선(±0.005R) 밖이고 **분자와 부호가 같을 때만** "
            "낸다 — 아니면 `—`로 두고 원값이 말하게 한다(WAN-115/395 부호 함정).",
        ]
    lines += [
        "",
        "## 하한 스윕 (거래당 net R ± 2σ · 거래 수)",
        "",
    ]
    thresholds = sorted({r.threshold for r in rows if r.threshold is not None})
    holds = sorted({r.hold for r in rows})
    for thr in thresholds:
        lines += [
            f"### %K < {thr:.0f}",
            "",
            "| 하한 | 보유 | full | is | oos_warm | oos gross R | oos 비용 R | oos 손절폭 중앙 |",
            "| --: | -- | --: | --: | --: | --: | --: | --: |",
        ]
        for floor in floors:
            for hold in holds:
                cells = [_cell(index.get((hold, floor, thr, s))) for s in SEGMENTS]
                oos = index.get((hold, floor, thr, "oos_warm"))
                gross = "—" if oos is None or oos.num_trades == 0 else f"{oos.gross_r:+.3f}"
                cost = "—" if oos is None or oos.num_trades == 0 else f"{oos.cost_r:.3f}"
                width = (
                    "—"
                    if oos is None or oos.num_trades == 0 or math.isnan(oos.median_stop_width)
                    else f"{oos.median_stop_width:.2%}"
                )
                lines.append(
                    f"| {floor:.1%} | ts{hold} | "
                    + " | ".join(cells)
                    + f" | {gross} | {cost} | {width} |"
                )
        lines.append("")
    lines += [
        "## 검산 — 하한 4% 행 ≡ WAN-424 §1 공개 CSV (완료기준 4)",
        "",
        *[f"- {line}" for line in check_lines],
        "",
        "비용 분해 항등식(`gross − 비용 − net`)의 최대 절댓값: "
        f"{max((r.identity_max_abs for r in rows), default=math.nan):.2e} R.",
        "",
        "⚠️ **표에서 최선 칸을 고르지 말 것**(WAN-161) — 읽을 것은 **모양**이다. "
        "전부 `pen_5bp`(체결 보수화) 위의 값이고 **채택 좌표가 아니다** · "
        "**「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변**(이 표는 *그 팔의 하한이 "
        "구간을 넘어가나*를 묻는다 — **다른 질문**).",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초({elapsed / 60:.0f}분).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--from-csv", action="store_true", help="CSV에서 요약만 다시 만든다.")
    parser.add_argument(
        "--elapsed",
        type=float,
        default=None,
        help=(
            "`--from-csv`가 실측 비용 줄을 되살릴 때 쓰는 초(완료기준 5). "
            "CSV에는 시간이 없으므로, 안 주면 그 줄을 **지어내지 않고 뺀다**."
        ),
    )
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--symbols", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument("--timeframes", default=None, help="파일럿용 좁히기(콤마).")
    parser.add_argument(
        "--holds", default=None, help="파일럿용 좁히기(콤마) — 기본은 WAN-424 §1 다섯 점."
    )
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = frame_to_rows(pd.read_csv(args.csv))
        summary = render_summary(rows, elapsed=args.elapsed)
        args.summary.write_text(summary, encoding="utf-8")
        print(summary)
        return 0

    started = time.monotonic()
    symbols = tuple(args.symbols.split(",")) if args.symbols else SYMBOLS
    timeframes = tuple(args.timeframes.split(",")) if args.timeframes else TIMEFRAMES
    holds = tuple(int(h) for h in args.holds.split(",")) if args.holds else HOLD_BARS
    payloads = build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, symbols=symbols, timeframes=timeframes
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    cells = build_arm_cells(payloads, jobs=args.jobs)
    print(f"팔 후보(하한 {ARM_FLOOR:.1%}) {time.monotonic() - started:.0f}s", flush=True)
    rows = run_grid(payloads, cells, holds=holds, progress=True)
    elapsed = time.monotonic() - started
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(args.csv, index=False)
    adopted = on_adopted_coordinates(symbols=symbols, timeframes=timeframes, holds=holds)
    summary = render_summary(rows, elapsed=elapsed, adopted=adopted)
    args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    _lines, worst = checksum_wan424(rows, adopted=adopted)
    return 0 if (not adopted) or checksum_passes(worst) else 1


if __name__ == "__main__":
    raise SystemExit(main())
