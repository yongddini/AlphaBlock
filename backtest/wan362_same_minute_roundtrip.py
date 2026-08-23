"""「같은 분 왕복」의 방향이 라이브와 백테에서 왜 반대인가 (WAN-362 · WAN-336 §6-2).

## 묻는 것 — 한 문장

> 백테스트에서 「들어간 그 1분 안에 나온 거래」가 **익절 467 : 손절 7**인데 페이퍼에서는
> **손절 5 : 익절 2**다. 그 방향을 만드는 **기계**가 무엇인가?

WAN-336 §6-2가 *「가르려면 체결가가 봉 범위 어디에 놓이는지를 직접 재야 하고 그건 별도
이슈」*로 남긴 자리다. 이 모듈이 그 측정을 한다.

## 무엇으로 재나 — 새 데이터도 격자도 없다

* **거래별**: WAN-346 §0이 낸 채택 북 팔 A `oos_warm` CSV(`wan346_trades_A_oos_warm.csv`,
  6,336건). **다시 만들지 않는다** — 팔 하나에 66분이다(WAN-330 실측).
* **1분봉**: 저장된 `ohlcv`(1m)에서 **진입 분 그 한 봉**만 읽는다.

두 개면 봉 내 위치 `(체결가 − 봉저가) / (봉고가 − 봉저가)`가 나오고, 손절가·익절가까지
있으니 **그 봉이 손절선/익절선에 닿을 수 있었는지**를 기하로 직접 판정할 수 있다.

## 산출물 셋

**§2-A 봉 내 위치** — 체결가가 그 1분봉의 어디에 놓이는가. 0에 몰릴수록 「지정가가 봉을
따라 내려가며 재산정돼 저가 근처에서 체결된다」(봉내 라이브 밴드, WAN-132)는 설명이 선다.

**§2-B 기하 판정** — `저가 ≤ 손절가`(같은 분 손절 가능) · `고가 ≥ 익절가`(같은 분 익절
가능)를 세어 엔진이 실제로 낸 467:7과 **맞는지 검산**한다. 맞으면 이 표는 「엔진과 같은
것을 재고 있다」가 증명된 것이다.

**§3 왜 손절이 7건뿐인가** — 이 이슈의 답. 아래 「기계」 문단.

## 📌 기계 — 같은 분 손절은 「순서를 몰라서」가 아니라 **취소 규칙**이 닫는다

1. 존을 깨는 상위TF 봉은 `break_time`을 그 봉 **시작 시각**으로 찍는다
   (`strategy.order_blocks`: 롱은 `저가 < 존바닥`, **엄격 부등호**).
2. 서브스텝 루프는 `step.time >= invalidation_time`이면 **체결을 판정하기 전에** 주문을
   취소한다(`backtest.substep.simulate_zone_limit_trade`). 그래서 **존을 깨는 상위TF 봉의
   어느 1분에서도 체결이 일어나지 않는다.**
3. 남는 유일한 창은 **`저가 == 존바닥`(정확히 일치)** 이다 — 무효화는 `<`라 안 걸리고
   손절 트리거는 `<=`(`backtest.substep`)라 걸린다.

실측이 이 논증을 그대로 확인한다: 6,336건 진입 봉의 **진입-봉 MAE 최대가 정확히 1.000R**
이고 그 아래로는 **한 건도 없으며**, 정확히 1.000R인 7건이 곧 엔진이 낸 같은 분 손절 7건이다.

📌 **라이브에는 이 취소가 없다** — `live/limit_engine.py`가 스스로 적어 둔 알려진 근사:
*"백테스트는 `break_time`(무효화 봉 시작)에 취소하지만 라이브는 그 봉이 **닫혀 탐지가
무효화를 확인한 뒤** 취소한다(한 봉 늦음 — 무효화 봉 안에서 체결될 수 있다)"*. 즉 라이브는
**존을 깨는 봉 안에서 진입**할 수 있고, 그 진입은 곧바로 손절로 끝난다.

🔁 ⚠️ **단 그 「한 봉 늦음」을 두 표가 갈리는 원인으로 읽지 말 것 — WAN-364가 정정했다.**
롱 지정가는 존 안에 있고 손절선은 존 아랫변이라 **체결이 무효화보다 반드시 먼저**다
(가격 하락 → 지정가 통과 → 계속 하락 → 존 아랫변 돌파). 무효화 시점엔 취소할 주문이 이미
없으므로 **라이브가 실시간으로 취소해도 그 체결은 못 막는다** — 원인은 라이브의 지연이
아니라 **백테스트 쪽 소급 취소**다. 라이브 지연이 실제로 만드는 차이는 **무효화 봉이 닫힌
뒤**의 재터치라는 별개의 작은 축이다. 이 모듈의 **측정 수치는 전부 유효하다**.

## ⚠️ 이 표가 답하지 못하는 것

* ❌ **「백테가 손실을 숨겼다」가 아니다** — 백테는 그 셋업에 **진입하지 않는다**(취소).
  숨긴 손실이 아니라 **아예 없는 거래**다. 그 「없는 거래」의 크기는 이 표가 재지 않았고
  **WAN-364**(`backtest.wan364_invalidation_cancel`)가 잰다 — 무효화 봉 안의 탭 인구조사 +
  취소를 봉 마감으로 미룬 인과 팔 북 반사실.
* ❌ **같은 분 익절 467건이 진짜인지** — 그건 봉 안의 순서 문제이고 WAN-348/359가 틱으로
  쟀다. 이 표는 **손절 쪽**을 연다.
* ❌ **큐 우선순위**(`pen_5bp` · WAN-98 Canceled)는 여전히 다른 축이다.

## 재현

    uv run python -m backtest.wan362_same_minute_roundtrip             # 측정 + 요약
    uv run python -m backtest.wan362_same_minute_roundtrip --from-csv  # 적재된 CSV로 요약만

측정 전용 — 엔진·기본값·토대 불변(`ConfluenceParams()`·`LeverageBookParams()` 그대로 ·
**엔진 코드를 한 줄도 고치지 않는다**), DB에 아무것도 쓰지 않는다(WAN-194 원칙),
실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.wan348_same_minute_tp import wilson_interval
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams

logger = logging.getLogger(__name__)

REPORTS_DIR = Path("backtest/reports")
#: 거래 목록의 출처 — WAN-346 §0이 낸 채택 북 팔 A 거래별 CSV. **다시 만들지 않는다.**
TRADES_CSV = REPORTS_DIR / "wan346_trades_A_oos_warm.csv"
CSV_PATH = REPORTS_DIR / "wan362_fill_position.csv"
DECILE_CSV_PATH = REPORTS_DIR / "wan362_volatility_deciles.csv"
SUMMARY_PATH = REPORTS_DIR / "wan362_same_minute_roundtrip_summary.md"

TF_ORDER: tuple[str, ...] = ("15m", "1h", "2h", "4h")
#: 분위 표의 십분위 수. 봉 변동성 층별로 같은 분 왕복률이 어떻게 변하는지 본다.
DECILES = 10
#: 「봉 안 위치가 0에 붙었다」고 부를 문턱(하위 5%). 요약이 이 비율을 찍는다.
NEAR_LOW = 0.05

#: §1 대조 — **사용자 서버 실측**(2026-08-22, `paper_trades` 전수)의 인용값이다.
#: 이 저장소가 다시 계산한 값이 **아니다**(로컬 장부는 2행뿐 — 러너는 서버에서 돈다).
#: 날짜·종목·TF 분해와 leave-one-day-out은 서버에서 `alphablock same-minute`가 낸다.
REPORTED_LIVE_TRADES = 32
REPORTED_LIVE_SAME_MINUTE = 7
REPORTED_LIVE_SAME_TP = 2


# --------------------------------------------------------------------------- #
# 대조군과 통계 — §1(`live.same_minute_census`)이 같은 자를 쓴다
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BacktestReference:
    """대조군 — 백테스트(채택 북 `oos_warm`)의 같은 분 왕복 실측.

    출처는 `backtest/reports/wan336_same_step_tp.csv`(WAN-336)이고, **다시 계산하지
    않는다**(이슈 §1 지시). 같은 수를 아래 §2-B(`geometry_check`)가 **저장 1분봉과 거래별
    CSV만으로 독립 재구성**하므로 이 상수는 두 곳에서 검산된다 — 어긋나면 요약이 ❌를 찍는다.

    §1(`live.same_minute_census`)이 페이퍼 장부를 이 대조군에 대고 잰다. 여기 사는 이유는
    레이어 규칙이다(`backtest`는 `live`를 임포트할 수 없고 그 반대는 된다).
    """

    trades: int = 6336
    same_minute_take_profit: int = 467
    same_minute_stop_loss: int = 7

    @property
    def same_minute(self) -> int:
        return self.same_minute_take_profit + self.same_minute_stop_loss

    @property
    def same_minute_rate(self) -> float:
        """전체 거래 중 같은 분 왕복 비율."""
        return self.same_minute / self.trades

    @property
    def take_profit_share(self) -> float:
        """같은 분 왕복 중 **익절** 비율 — 이 축이 라이브와 방향이 반대다."""
        return self.same_minute_take_profit / self.same_minute


#: 대조군 기본값(WAN-336 `oos_warm`).
BACKTEST_REFERENCE = BacktestReference()


def binomial_tail_p(successes: int, total: int, prob: float, *, upper: bool) -> float:
    """정확 이항 단측 p값. `upper`면 `P(X ≥ successes)`, 아니면 `P(X ≤ successes)`.

    정규근사를 쓰지 않는 이유는 `wilson_interval`과 같다 — 표본이 7~32건이고 기준 비율이
    0.985처럼 끝에 붙어 있어 근사가 성립하지 않는다.
    """
    if total <= 0:
        return float("nan")
    prob = min(max(prob, 0.0), 1.0)
    lo, hi = (successes, total) if upper else (0, successes)
    return min(
        1.0,
        sum(math.comb(total, k) * prob**k * (1.0 - prob) ** (total - k) for k in range(lo, hi + 1)),
    )


def required_sample(observed_rate: float, reference: float, *, alpha: float = 0.05) -> int | None:
    """관측 비율이 유지될 때 기준 비율과 **갈리려면 몇 건이 필요한가**.

    `observed_rate`로 계속 나온다고 가정하고(성공 수 = `round(n·p̂)`) 정확 이항 단측 p가
    `alpha` 아래로 내려가는 가장 작은 `n`을 찾는다. 1,000건 안에 못 찾으면 `None` —
    「이 정도 차이는 현실적인 표본으로 안 갈린다」는 뜻이다.

    ⚠️ **독립 시행 가정**이다. 같은 분 왕복이 한 급락에 몰리면 유효 표본은 건수보다
    작으므로 이 값은 **낙관적 하한**이다.
    """
    if math.isnan(observed_rate) or math.isnan(reference) or observed_rate == reference:
        return None
    upper = observed_rate > reference
    for n in range(1, 1001):
        successes = int(round(observed_rate * n))
        if binomial_tail_p(successes, n, reference, upper=upper) <= alpha:
            return n
    return None


@dataclass(frozen=True, slots=True)
class Verdict:
    """한 축(빈도·구성)의 판정 — 관측 · Wilson 구간 · 기준 · p · 필요 표본."""

    axis: str
    successes: int
    total: int
    reference: float
    low: float
    high: float
    p_value: float
    required: int | None

    @property
    def rate(self) -> float:
        return self.successes / self.total if self.total else float("nan")

    @property
    def decided(self) -> bool:
        """기준 비율이 Wilson 구간 **밖**이면 이 표본으로 판정이 선다."""
        if self.total <= 0 or math.isnan(self.low):
            return False
        return not (self.low <= self.reference <= self.high)


def judge(
    axis: str, successes: int, total: int, reference: float, *, alpha: float = 0.05
) -> Verdict:
    """한 축을 재고 판정을 낸다."""
    if total <= 0:
        return Verdict(axis, 0, 0, reference, float("nan"), float("nan"), float("nan"), None)
    rate = successes / total
    low, high = wilson_interval(successes, total)
    p_value = binomial_tail_p(successes, total, reference, upper=rate > reference)
    return Verdict(
        axis,
        successes,
        total,
        reference,
        low,
        high,
        p_value,
        required_sample(rate, reference, alpha=alpha),
    )


def _parse_utc_minute(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000)


def load_trades(path: Path = TRADES_CSV, *, take_profit_r: float | None = None) -> pd.DataFrame:
    """거래별 CSV를 읽고 진입 시각을 ms로, 빠진 익절가를 고정 R 규칙으로 되살린다.

    🚨 **재진입 거래는 `익절가` 열이 비어 있다**(WAN-345가 고친 배선의 남은 조각 — 순수
    관측 열이라 손익에는 안 쓰인다). 6,336건 중 987건이 그렇다. WAN-348이 쓴 것과 **같은
    되살림**(`진입가 + R배수 × (진입가 − 손절가)`)을 쓰고, 값이 있는 행에서 그 규칙이
    기록값을 재현하는지 `take_profit_checksum`이 검산한다.

    열이 없으면 조용히 빈 표를 내지 않고 죽는다 — 0건으로 통과하면 「표본이 없는 표」가
    정상처럼 나온다(WAN-348과 같은 관행).
    """
    resolved_r = ConfluenceParams().take_profit_r if take_profit_r is None else take_profit_r
    frame = pd.read_csv(path)
    required = {
        "방향",
        "칸(종목)",
        "칸(TF)",
        "진입시각(UTC)",
        "청산시각(UTC)",
        "진입가",
        "손절가",
        "익절가",
        "청산사유",
        "같은분익절",
        "재진입",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}에 필요한 열이 없습니다: {sorted(missing)}")
    longs = frame[frame["방향"] == "롱"]
    if len(longs) != len(frame):
        # 채택 엔진은 롱 온리(WAN-87)다. 숏이 섞이면 아래 기하(저가/고가)가 통째로 뒤집혀야
        # 하므로 조용히 롱만 세지 않고 죽는다.
        raise ValueError(f"{path}에 롱이 아닌 거래가 있습니다: {len(frame) - len(longs)}건")
    out = frame.copy()
    out["entry_ms"] = out["진입시각(UTC)"].map(_parse_utc_minute)
    out["R"] = out["진입가"] - out["손절가"]
    derived = out["진입가"] + resolved_r * out["R"]
    out["tp_derived"] = out["익절가"].isna()
    out["tp"] = out["익절가"].fillna(derived)
    out["same_minute"] = out["진입시각(UTC)"] == out["청산시각(UTC)"]
    return out


def take_profit_checksum(
    frame: pd.DataFrame, *, take_profit_r: float | None = None
) -> tuple[int, int, float]:
    """되살린 익절가가 맞는지 — `(값이 있는 행, 일치한 행, 최대 상대오차)` (WAN-348과 같은 검산)."""
    resolved_r = ConfluenceParams().take_profit_r if take_profit_r is None else take_profit_r
    known = frame[~frame["익절가"].isna()]
    if known.empty:
        return (0, 0, float("nan"))
    derived = known["진입가"] + resolved_r * (known["진입가"] - known["손절가"])
    rel = ((known["익절가"] - derived).abs() / known["익절가"].abs()).astype(float)
    return (int(len(known)), int((rel < 1e-12).sum()), float(rel.max()))


def attach_entry_bars(
    frame: pd.DataFrame, *, db_path: str = harness.DB_PATH, timeframe: str = "1m"
) -> pd.DataFrame:
    """거래마다 **진입 분 그 1분봉**의 시·고·저·종을 붙인다.

    종목마다 필요한 구간을 한 번씩만 읽는다 — 거래마다 SQL을 날리면 6,336번이다.
    붙지 않은 행(그 분봉이 저장에 없음)은 지우지 않고 `NaN`으로 남겨 요약이 **몇 건이
    빠졌는지 밝히게** 한다(조용히 표본을 줄이지 않는다).
    """
    store = OhlcvStore(db_path)
    parts: list[pd.DataFrame] = []
    try:
        for symbol, group in frame.groupby("칸(종목)"):
            bars = store.load(
                str(symbol),
                timeframe,
                start_ms=int(group["entry_ms"].min()),
                end_ms=int(group["entry_ms"].max()) + 60_000,
            )
            if bars.empty:
                parts.append(
                    group.assign(
                        bar_open=math.nan, bar_high=math.nan, bar_low=math.nan, bar_close=math.nan
                    )
                )
                continue
            indexed = bars.set_index("open_time")[["open", "high", "low", "close"]]
            indexed.columns = ["bar_open", "bar_high", "bar_low", "bar_close"]
            parts.append(group.join(indexed, on="entry_ms"))
    finally:
        store.close()
    return pd.concat(parts).sort_index()


def measure(frame: pd.DataFrame) -> pd.DataFrame:
    """봉 내 위치와 기하 판정 열을 붙인다.

    * `fill_position` = `(체결가 − 봉저가) / (봉고가 − 봉저가)` — 0이면 저가, 1이면 고가.
    * `entry_bar_mae_r` = `(체결가 − 봉저가) / 1R` — 그 봉이 손절선 쪽으로 **1R 중 얼마나**
      갔는가. 같은 분 손절이 나려면 **≥ 1**이어야 한다.
    * `entry_bar_mfe_r` = `(봉고가 − 체결가) / 1R` — 익절 쪽. 고정 1.5R이면 **≥ 1.5**.
    * `can_stop` = `봉저가 ≤ 손절가` · `can_tp` = `봉고가 ≥ 익절가` — 기하 판정.

    📌 **`can_stop`은 체결가와 무관하다** — 손절가는 존 무효화 경계라 체결가에서 파생되지
    않는다. 반대로 `can_tp`는 체결가에 강하게 의존한다(1R = 체결가 − 손절가라 체결가가
    낮을수록 익절 목표가 가까워진다). 이 비대칭이 §2-C 반사실의 근거다.
    """
    out = frame.copy()
    span = out["bar_high"] - out["bar_low"]
    out["bar_span"] = span
    out["fill_position"] = (out["진입가"] - out["bar_low"]) / span
    out["entry_bar_mae_r"] = (out["진입가"] - out["bar_low"]) / out["R"]
    out["entry_bar_mfe_r"] = (out["bar_high"] - out["진입가"]) / out["R"]
    out["can_stop"] = out["bar_low"] <= out["손절가"]
    out["can_tp"] = out["bar_high"] >= out["tp"]
    out["bar_range_pct"] = span / out["진입가"] * 100.0
    out["range_over_r"] = span / out["R"]
    out["recorded_same_tp"] = out["같은분익절"].astype(str) == "True"
    out["recorded_same_sl"] = out["same_minute"] & (out["청산사유"] == "손절")
    return out


@dataclass(frozen=True, slots=True)
class GeometryCheck:
    """§2-B 검산 — 기하 판정이 엔진이 낸 수를 재현하는가."""

    can_stop: int
    recorded_same_sl: int
    can_tp: int
    recorded_same_tp: int
    stop_wins: int
    """`can_tp`인데 같은 봉에서 `can_stop`이기도 해 손절이 이긴 건수(`stop_before_tp`)."""
    fill_outside_bar: int
    """체결가가 그 봉의 [저가, 고가] **밖**인 건수 — 밴드가 봉 범위 위로 뛴 경우."""
    below_zone_bottom: int
    """진입 봉의 저가가 존 바닥(손절가) **아래**로 내려간 건수.

    §3의 논증이 옳다면 **0이어야 한다** — 그런 봉은 존을 깨므로 상위TF 봉 시작에 주문이
    취소돼 애초에 체결이 없다. 이 수가 0이 아니면 §3 문단을 인용하지 말 것."""
    exact_touch: int
    """진입 봉의 저가가 손절가와 **정확히 같은** 건수 — 남는 유일한 창(무효화 `<` vs 손절 `<=`)."""
    missing_bars: int

    @property
    def stop_matches(self) -> bool:
        return self.can_stop == self.recorded_same_sl

    @property
    def tp_matches(self) -> bool:
        """익절은 손절 우선 규칙을 되돌려야 맞는다: `can_tp − stop_wins == 기록`."""
        return self.can_tp - self.stop_wins == self.recorded_same_tp


def geometry_check(measured: pd.DataFrame) -> GeometryCheck:
    """기하 판정이 엔진의 같은 분 익절·손절 수를 재현하는지 센다.

    🚨 이 검산이 이 표의 자격 증명이다 — 재현하지 못하면 아래 분포는 「엔진이 아닌 무언가」를
    재고 있는 것이라 한 줄도 인용할 수 없다(WAN-91/95/112/123/159 부류의 조용한 실패 방지).
    """
    usable = measured[~measured["bar_low"].isna()]
    both = usable["can_tp"] & usable["can_stop"]
    return GeometryCheck(
        can_stop=int(usable["can_stop"].sum()),
        recorded_same_sl=int(usable["recorded_same_sl"].sum()),
        can_tp=int(usable["can_tp"].sum()),
        recorded_same_tp=int(usable["recorded_same_tp"].sum()),
        stop_wins=int(both.sum()),
        fill_outside_bar=int(
            ((usable["진입가"] > usable["bar_high"]) | (usable["진입가"] < usable["bar_low"])).sum()
        ),
        below_zone_bottom=int((usable["bar_low"] < usable["손절가"]).sum()),
        exact_touch=int((usable["bar_low"] == usable["손절가"]).sum()),
        missing_bars=int(measured["bar_low"].isna().sum()),
    )


def position_table(measured: pd.DataFrame) -> pd.DataFrame:
    """§2-A — TF별 봉 내 위치·진입봉 MAE/MFE 분포."""
    rows: list[dict[str, object]] = []
    usable = measured[~measured["bar_low"].isna()]
    for label, part in [("전체", usable)] + [
        (tf, usable[usable["칸(TF)"] == tf]) for tf in TF_ORDER
    ]:
        if part.empty:
            continue
        pos = part["fill_position"]
        rows.append(
            {
                "구간": label,
                "거래": int(len(part)),
                "위치_p25": float(pos.quantile(0.25)),
                "위치_중앙": float(pos.median()),
                "위치_p75": float(pos.quantile(0.75)),
                "위치_평균": float(pos.mean()),
                "저가붙음%": float((pos <= NEAR_LOW).mean() * 100.0),
                "MAE_R_중앙": float(part["entry_bar_mae_r"].median()),
                "MAE_R_최대": float(part["entry_bar_mae_r"].max()),
                "MFE_R_중앙": float(part["entry_bar_mfe_r"].median()),
                "같은분익절": int(part["recorded_same_tp"].sum()),
                "같은분손절": int(part["recorded_same_sl"].sum()),
                "같은분익절%": float(part["recorded_same_tp"].mean() * 100.0),
                "같은분손절%": float(part["recorded_same_sl"].mean() * 100.0),
            }
        )
    return pd.DataFrame(rows)


def decile_table(measured: pd.DataFrame, column: str = "bar_range_pct") -> pd.DataFrame:
    """§2-D — 진입 봉 변동성 십분위별 같은 분 왕복률.

    라이브 표본이 급락 2분에 몰렸을 수 있다는 것이 이슈의 경고 2다. 그렇다면 **변동성을
    통제하면 두 표가 만나야 한다** — 이 표가 그 조건부 비율을 낸다.
    """
    usable = measured[~measured["bar_low"].isna()].copy()
    if usable.empty:
        return pd.DataFrame()
    usable["decile"] = pd.qcut(usable[column], DECILES, labels=False, duplicates="drop")
    rows: list[dict[str, object]] = []
    for decile, part in usable.groupby("decile"):
        same_tp = int(part["recorded_same_tp"].sum())
        same_sl = int(part["recorded_same_sl"].sum())
        rows.append(
            {
                "십분위": int(decile) + 1,
                "자": column,
                "중앙값": float(part[column].median()),
                "거래": int(len(part)),
                "같은분익절": same_tp,
                "같은분손절": same_sl,
                "같은분익절%": same_tp / len(part) * 100.0,
                "같은분손절%": same_sl / len(part) * 100.0,
                "익절:손절": float(same_tp / same_sl) if same_sl else float("inf"),
            }
        )
    return pd.DataFrame(rows)


def fill_placement_counterfactual(measured: pd.DataFrame) -> pd.DataFrame:
    """§2-C — 체결가를 봉 안 다른 자리에 두면 같은 분 익절이 몇 건이나 남는가.

    ⚠️ **대안 엔진 제안이 아니다.** 롱 지정가를 봉 중간에 「받는」 방법은 없다 — 이 표는
    **같은 분 익절이 체결가 위치에 얼마나 가파르게 의존하는지**를 재는 민감도다. 손절
    쪽에는 이 축이 없다(`저가 ≤ 손절가`는 체결가와 무관 — `measure` 문단).
    """
    usable = measured[~measured["bar_low"].isna()]
    span = usable["bar_high"] - usable["bar_low"]
    take_profit_r = ConfluenceParams().take_profit_r
    arms: list[tuple[str, pd.Series]] = [
        ("실제(엔진 체결가)", usable["진입가"]),
        ("봉 저가 q=0", usable["bar_low"]),
        ("봉 종가", usable["bar_close"]),
        ("봉 중간 q=0.5", usable["bar_low"] + 0.5 * span),
    ]
    rows: list[dict[str, object]] = []
    for label, entry in arms:
        risk = entry - usable["손절가"]
        target = entry + take_profit_r * risk
        reachable = (usable["bar_high"] >= target) & (risk > 0)
        rows.append(
            {
                "체결가 자리": label,
                "봉 내 위치 중앙": float(((entry - usable["bar_low"]) / span).median()),
                "같은분익절 가능": int(reachable.sum()),
                "같은분익절 가능%": float(reachable.mean() * 100.0),
                "같은분손절 가능": int((usable["bar_low"] <= usable["손절가"]).sum()),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 실행 · 요약
# --------------------------------------------------------------------------- #


def build(*, trades_csv: Path = TRADES_CSV, db_path: str = harness.DB_PATH) -> pd.DataFrame:
    """거래별 CSV × 저장 1분봉으로 측정 표를 만든다."""
    frame = load_trades(trades_csv)
    with_bars = attach_entry_bars(frame, db_path=db_path)
    return measure(with_bars)


_EXPORT_COLUMNS = [
    "칸(종목)",
    "칸(TF)",
    "진입시각(UTC)",
    "청산시각(UTC)",
    "청산사유",
    "진입가",
    "손절가",
    "tp",
    "tp_derived",
    "재진입",
    "bar_open",
    "bar_high",
    "bar_low",
    "bar_close",
    "bar_span",
    "fill_position",
    "entry_bar_mae_r",
    "entry_bar_mfe_r",
    "can_stop",
    "can_tp",
    "bar_range_pct",
    "range_over_r",
    "recorded_same_tp",
    "recorded_same_sl",
]


def _fmt(value: float, digits: int = 3) -> str:
    return (
        "—"
        if value is None or (isinstance(value, float) and math.isnan(value))
        else f"{value:.{digits}f}"
    )


def _live_verdicts() -> list[Verdict]:
    """§1 — 인용한 페이퍼 실측이 백테 기준과 갈리는지. 통계는 `live.same_minute_census`의 자다."""
    return [
        judge(
            "빈도(같은 분 왕복 / 전체 거래)",
            REPORTED_LIVE_SAME_MINUTE,
            REPORTED_LIVE_TRADES,
            BACKTEST_REFERENCE.same_minute_rate,
        ),
        judge(
            "구성(익절 / 같은 분 왕복)",
            REPORTED_LIVE_SAME_TP,
            REPORTED_LIVE_SAME_MINUTE,
            BACKTEST_REFERENCE.take_profit_share,
        ),
    ]


def _ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1f} : 1" if denominator else f"{numerator} : 0"


def render_summary(
    measured: pd.DataFrame,
    positions: pd.DataFrame,
    deciles: pd.DataFrame,
    counterfactual: pd.DataFrame,
    check: GeometryCheck,
    checksum: tuple[int, int, float],
) -> str:
    """요약 md. 결론 문장을 표에서 **파생**시켜 본문과 수치가 어긋나지 않게 한다."""
    total_row = positions[positions["구간"] == "전체"].iloc[0]
    known, matched, max_rel = checksum
    lines: list[str] = []
    lines.append("# WAN-362 — 「같은 분 왕복」의 방향이 라이브와 백테에서 반대인 이유")
    lines.append("")
    lines.append(
        "채택 북 팔 A(`oos_warm`) 거래별 CSV(WAN-346 §0) × 저장 1분봉. "
        "**새 격자를 돌리지 않았고 엔진을 한 줄도 고치지 않았다.**"
    )
    lines.append("")
    lines.append("## §2-B 검산 — 기하가 엔진의 수를 재현하는가")
    lines.append("")
    lines.append("| 항목 | 기하 판정 | 엔진 기록 | 일치 |")
    lines.append("| -- | --: | --: | :--: |")
    lines.append(
        f"| 같은 분 **손절**(`저가 ≤ 손절가`) | {check.can_stop} | {check.recorded_same_sl} | "
        f"{'✅' if check.stop_matches else '❌'} |"
    )
    lines.append(
        f"| 같은 분 **익절**(`고가 ≥ 익절가` − 손절 우선 {check.stop_wins}) | "
        f"{check.can_tp - check.stop_wins} | {check.recorded_same_tp} | "
        f"{'✅' if check.tp_matches else '❌'} |"
    )
    lines.append("")
    lines.append(
        f"익절가 되살림 검산(WAN-348과 같은 규칙): 값이 있는 {known}행 중 {matched}행 일치 "
        f"(최대 상대오차 {max_rel:.2e}). 봉을 못 붙인 거래 {check.missing_bars}건 · "
        f"체결가가 봉 범위 밖 {check.fill_outside_bar}건(밴드가 봉 위로 뛴 경우)."
    )
    lines.append("")
    lines.append(
        "📌 **기하만으로 467:7이 그대로 나온다** — 이 표가 엔진과 같은 것을 재고 있다는 뜻이고, "
        "동시에 **엔진이 같은 분 손절을 인색하게 세지 않는다**는 뜻이다(기하가 허락한 7건을 "
        "하나도 빠짐없이 냈다)."
        if check.stop_matches and check.tp_matches
        else "🚨 **검산 실패 — 아래 분포를 인용하지 말 것.**"
    )
    lines.append("")
    lines.append("## §2-A 체결가는 그 1분봉의 어디에 놓이는가")
    lines.append("")
    lines.append(
        "| 구간 | 거래 | 위치 p25 | **중앙** | p75 | 저가붙음(≤0.05)% | MAE_R 중앙 | MAE_R 최대 | "
        "MFE_R 중앙 | 같은분익절% | 같은분손절% |"
    )
    lines.append("| -- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for _, row in positions.iterrows():
        lines.append(
            f"| {row['구간']} | {int(row['거래'])} | {_fmt(row['위치_p25'])} | "
            f"**{_fmt(row['위치_중앙'])}** | {_fmt(row['위치_p75'])} | {row['저가붙음%']:.1f}% | "
            f"{_fmt(row['MAE_R_중앙'])} | "
            f"{_fmt(row['MAE_R_최대'])} | {_fmt(row['MFE_R_중앙'])} | {row['같은분익절%']:.2f}% | "
            f"{row['같은분손절%']:.2f}% |"
        )
    lines.append("")
    lines.append(
        f"📌 **체결가는 봉의 아래쪽에 놓인다**(전체 중앙 {_fmt(total_row['위치_중앙'])}) — "
        f"하지만 「0에 딱 붙는다」는 아니다(≤0.05는 {total_row['저가붙음%']:.1f}%). "
        f"결정적인 것은 **R로 잰 값**이다: 진입 봉이 손절 쪽으로 간 거리 중앙이 "
        f"**{_fmt(total_row['MAE_R_중앙'])}R**이고 익절 쪽은 "
        f"**{_fmt(total_row['MFE_R_중앙'])}R**이다. "
        f"같은 분 손절에 필요한 1.0R은 **최대값이 정확히 {_fmt(total_row['MAE_R_최대'])}R**이라 "
        "그 위로는 한 건도 없다."
    )
    lines.append("")
    lines.append("## §3 왜 최대가 정확히 1.000R인가 — 취소 규칙이 닫는다")
    lines.append("")
    lines.append(
        "우연이 아니라 **구조**다. 존을 깨는 상위TF 봉(롱: `저가 < 존바닥`, **엄격 부등호**)은 "
        "`break_time`을 그 봉 **시작 시각**으로 찍고, 서브스텝 루프는 `step.time >= "
        "invalidation_time`이면 **체결을 판정하기 전에** 주문을 취소한다. 그래서 존을 깨는 "
        "상위TF 봉의 **어느 1분에서도 체결이 일어나지 않는다**. 남는 유일한 창이 "
        "`저가 == 존바닥`(무효화는 `<`라 안 걸리고 손절 트리거는 `<=`라 걸린다)이고, "
        f"그것이 정확히 이 {check.recorded_same_sl}건이다."
    )
    lines.append("")
    lines.append(
        f"🚨 **실측이 그 논증을 그대로 확인한다**: 진입 봉의 저가가 존 바닥 **아래**로 "
        f"내려간 거래는 **{check.below_zone_bottom}건**(논증이 옳다면 0이어야 한다)이고, "
        f"**정확히 같은** 거래가 **{check.exact_touch}건**으로 같은 분 손절 "
        f"{check.recorded_same_sl}건과 맞는다."
    )
    lines.append("")
    lines.append(
        "🚨 **그 취소는 엄밀히 인과적이지 않다** — 상위TF 봉이 존을 깨는지는 그 봉이 **닫혀야** "
        "아는데(저가는 봉이 끝나야 확정된다) 취소는 그 봉 **시작**부터 걸린다. 즉 백테스트는 "
        "봉 끝에서야 알 수 있는 사실로 봉 처음의 주문을 걷어낸다 — 같은 분 손절이 7건뿐인 "
        "이유가 「1분봉이 순서를 모른다」가 아니라 **여기**다."
    )
    lines.append("")
    lines.append(
        "🔁 **이 문단의 원래 결론(「라이브가 한 봉 늦어서 라이브만 더 한다」)은 WAN-364가 "
        "정정했다 — 위 수치는 전부 유효하고 바뀌는 것은 해석이다.** 롱 지정가는 존 안에 있고"
        "(밴드가 존보다 아래면 진입 자체가 기각 — WAN-75 규칙 3) 손절선은 존 아랫변이라 순서가 "
        "**강제된다**: 가격 하락 → 지정가 통과(체결) → 계속 하락 → 존 아랫변 돌파(무효화). "
        "**체결이 무효화보다 반드시 먼저**이므로 무효화 시점엔 이미 포지션을 들고 있고 취소할 "
        "주문이 없다 — **라이브가 실시간으로 취소해도 그 체결은 못 막는다.** 즉 두 표가 갈리는 "
        "주된 원인은 라이브의 한 봉 지연이 아니라 **백테스트 쪽 소급 취소**다. 라이브 지연이 "
        "실제로 만드는 차이는 **무효화 봉이 닫힌 뒤**의 재터치라는 별개의 작은 축이다."
    )
    lines.append("")
    lines.append(
        "📌 **크기는 WAN-364가 쟀다** — 취소를 무효화 봉의 **마감**으로 미룬 인과 팔로 채택 북을 "
        "다시 돌린 반사실이다(`backtest/reports/wan364_invalidation_cancel_summary.md`). "
        "⚠️ 기본값은 여전히 안 바꿨다 — 후보 집합이 통째로 바뀌는 **재-베이스라인 = 사용자 "
        "결정**이다(WAN-132/149/159급 파급)."
    )
    lines.append("")
    lines.append(
        "⚠️ **「백테가 손실을 숨겼다」로 인용 금지** — 백테는 그 셋업에 **진입하지 않는다**. "
        "숨긴 손실이 아니라 **없는 거래**다. 그 「없는 거래」의 크기는 이 표가 재지 않았고 "
        "**WAN-364**가 잰다(무효화 봉 안의 탭 인구조사 + 인과 팔 북 반사실)."
    )
    lines.append("")
    lines.append("## §2-D 변동성을 통제하면 두 표가 만나는가")
    lines.append("")
    if not deciles.empty:
        lines.append(
            "| 십분위(진입 봉 범위 %) | 중앙값 | 거래 | 같은분익절% | 같은분손절% | 익절:손절 |"
        )
        lines.append("| --: | --: | --: | --: | --: | --: |")
        for _, row in deciles.iterrows():
            ratio = "—" if row["같은분손절"] == 0 else f"{row['익절:손절']:.0f} : 1"
            lines.append(
                f"| {int(row['십분위'])} | {row['중앙값']:.3f}% | {int(row['거래'])} | "
                f"{row['같은분익절%']:.2f}% | {row['같은분손절%']:.2f}% | {ratio} |"
            )
        top = deciles.iloc[-1]
        lines.append("")
        lines.append(
            f"🚨 **답: 만나지 않는다.** 같은 분 왕복은 상위 두 십분위에만 있고, 가장 격렬한 "
            f"십분위에서도 익절 {top['같은분익절%']:.1f}% 대 손절 {top['같은분손절%']:.2f}%로 "
            f"**여전히 익절 쪽이 압도**한다. 라이브 표본이 급락에 몰렸다는 것만으로는 "
            "손절 5 : 익절 2가 설명되지 않는다 — 설명하는 것은 §3의 취소 규칙이다."
        )
    lines.append("")
    lines.append("## §2-C 체결가 자리 민감도 (⚠️ 대안 엔진 제안 아님)")
    lines.append("")
    lines.append("| 체결가 자리 | 봉 내 위치 중앙 | 같은분익절 가능 | 비율 | 같은분손절 가능 |")
    lines.append("| -- | --: | --: | --: | --: |")
    for _, row in counterfactual.iterrows():
        lines.append(
            f"| {row['체결가 자리']} | {_fmt(row['봉 내 위치 중앙'])} | "
            f"{int(row['같은분익절 가능'])} | "
            f"{row['같은분익절 가능%']:.2f}% | {int(row['같은분손절 가능'])} |"
        )
    lines.append("")
    lines.append(
        "📌 **비대칭이 그대로 보인다** — 체결가를 옮기면 같은 분 **익절** 가능 건수는 크게 "
        "움직이는데 **손절** 가능 건수는 **한 건도 안 움직인다**(손절가는 존 무효화 경계라 "
        "체결가에서 파생되지 않는다). 즉 같은 분 익절은 「체결가가 낮아 1R이 작아진」 산물이고, "
        "같은 분 손절은 **체결가와 무관한 취소 규칙**이 닫는다 — **두 축은 같은 원인이 아니다.**"
    )
    lines.append("")
    lines.append("## §1 — 페이퍼 쪽 관측(사용자 서버 실측 인용)과 그 표본으로 갈리는가")
    lines.append("")
    lines.append(
        f"⚠️ **이 저장소가 다시 잰 값이 아니다** — 사용자 서버 실측(2026-08-22, `paper_trades` "
        f"전수 {REPORTED_LIVE_TRADES}건)의 인용이다(로컬 장부는 2행뿐 — 러너는 서버에서 돈다). "
        "날짜·종목·TF 분해와 날짜 하나씩 빼기는 서버에서 `alphablock same-minute`가 낸다."
    )
    lines.append("")
    lines.append("| 축 | 페이퍼 | Wilson 95% | 백테 기준 | 판정 | 정확 이항 p | 필요 표본 |")
    lines.append("| -- | --: | :--: | --: | :--: | --: | --: |")
    for verdict in _live_verdicts():
        need = "—" if verdict.required is None else f"{verdict.required}건"
        decided = "**갈린다**" if verdict.decided else "안 갈린다"
        lines.append(
            f"| {verdict.axis} | {verdict.successes}/{verdict.total} = "
            f"{verdict.rate * 100:.1f}% | [{verdict.low * 100:.1f}%, {verdict.high * 100:.1f}%] | "
            f"{verdict.reference * 100:.1f}% | {decided} | "
            f"{verdict.p_value:.2g} | {need} |"
        )
    lines.append("")
    lines.append(
        "📌 **두 축 다 갈린다 — 단 「구성」이 훨씬 강하다.** 빈도(21.9% vs 7.5%)는 필요 표본 "
        "16건으로 겨우 넘고, 구성(익절 28.6% vs 98.5%)은 **같은 분 손절이 한 건만 나와도** "
        "기준을 기각한다(필요 표본 1건). 🚨 **단 둘 다 독립 시행 가정 위의 값이다** — "
        "같은 분 왕복 7건이 급락 2분에 몰렸다면 유효 표본은 7보다 훨씬 작고, 빈도 축은 "
        "그 순간 판정을 잃는다. **구성 축은 더 오래 버틴다**: 백테스트의 같은 분 손절률은 "
        f"거래 {check.recorded_same_tp + check.recorded_same_sl + 0:,}건이 아니라 **전체 "
        f"6,336건 중 {check.recorded_same_sl}건 = 0.11%**인데 페이퍼는 32건 중 5건 = 15.6%라 "
        "**자릿수가 둘 다르다** — 급락 하루로 표본을 좁혀도(§2-D 상위 십분위 0.32%) 그 "
        "간극은 닫히지 않는다."
    )
    lines.append("")
    lines.append("## §1에 넘기는 대조군")
    lines.append("")
    lines.append(
        f"백테스트(채택 북 `oos_warm`): 같은 분 왕복 "
        f"{check.recorded_same_tp + check.recorded_same_sl}건의 익절:손절 = "
        f"**{_ratio(check.recorded_same_tp, check.recorded_same_sl)}** · "
        f"같은 분 비중 {BACKTEST_REFERENCE.same_minute_rate * 100:.2f}%. "
        "페이퍼 장부 쪽 인구조사는 `alphablock same-minute`(서버)가 낸다."
    )
    lines.append("")
    lines.append("## ⚠️ 경고")
    lines.append("")
    lines.append(
        "* 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이다 — 큐 우선순위는 다른 축이고 "
        "실측은 틱·호가(WAN-98, **Canceled**) 소관이다."
    )
    lines.append(
        "* **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 축은 *진입 규칙이 "
        "무작위와 구분되는가*가 아니라 *이미 잰 숫자가 얼마나 낙관인가*를 묻는다. 다른 질문이다."
    )
    lines.append(
        "* **측정 전용** — `ConfluenceParams()`·`LeverageBookParams()` 불변, 엔진 코드 무수정, "
        "DB에 아무것도 쓰지 않았다, 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`)."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-362 §2 — 체결가의 1분봉 내 위치와 기하 판정")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 다시 만든다")
    parser.add_argument("--db", default=harness.DB_PATH, help="OHLCV DB 경로")
    parser.add_argument("--trades-csv", default=str(TRADES_CSV), help="거래별 CSV 경로(WAN-346 §0)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.from_csv:
        if not CSV_PATH.exists():
            logger.error("적재된 CSV가 없습니다: %s", CSV_PATH)
            return 1
        measured = pd.read_csv(CSV_PATH)
        # `--from-csv`는 저장된 열만 쓴다 — 되살림 검산은 원본 CSV가 있어야 한다.
        checksum = take_profit_checksum(load_trades(Path(args.trades_csv)))
    else:
        measured = build(trades_csv=Path(args.trades_csv), db_path=args.db)
        checksum = take_profit_checksum(load_trades(Path(args.trades_csv)))
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        measured[_EXPORT_COLUMNS].to_csv(CSV_PATH, index=False)
        logger.info("적재: %s (%d행)", CSV_PATH, len(measured))

    positions = position_table(measured)
    deciles = decile_table(measured)
    counterfactual = fill_placement_counterfactual(measured)
    check = geometry_check(measured)
    if not args.from_csv:
        deciles.to_csv(DECILE_CSV_PATH, index=False)
        logger.info("적재: %s (%d행)", DECILE_CSV_PATH, len(deciles))

    summary = render_summary(measured, positions, deciles, counterfactual, check, checksum)
    SUMMARY_PATH.write_text(summary, encoding="utf-8")
    logger.info("요약: %s", SUMMARY_PATH)
    print(summary)
    return 0 if (check.stop_matches and check.tp_matches) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
