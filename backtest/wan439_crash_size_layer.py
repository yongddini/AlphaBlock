"""WAN-439: 스토캐스틱 팔 위 얹는 층 「폭락 국면(BTC 24h ≤ −5%) 진입 크기 ×0.5」 — 정식 측정.

## 왜 이 모듈이 있나

WAN-438 탐색 스크래치(`docs/decisions/wan438_scratch/`, 시험한 설정 100개+ · 채택 근거 아님)가 토대
(진입·청산 엔진)를 건드리지 않고 **크기만** 바꾸는 층 하나를 가장 믿을 만한 개선으로 냈다. 이 모듈은
그 층을 **착수 전에 숫자를 못 박고 한 번** 잰다(결과를 보고 옮기지 않는다 — WAN-161).

## 무엇을 고정했나 (착수 전 · 코드 상수)

* 토대 = WAN-436/438 페이퍼 좌표 그대로 — 롱 · 첫 탭 · 재진입 없음 · 손절폭 ≥ 4% · 직전 확정봉
  %K<25 · ts4 · 손절 2배 · 초반 건너뛰기 1~10 · 몰림 중 BTC 4h ≤ −2.5% · 24h 예산 10 · 채택 북
  (`wan438.GATED`를 그대로 읽는다 — 두 벌 금지).
* 층 B: 진입 **직전 확정 1분봉** 기준 BTC 24h 수익률 ≤ `CRASH_THRESHOLD`(−5%)면 크기 × `CRASH_MULT`
  (0.5). 그 외 파라미터 없음.
* 대조 팔 C(**대조 전용 · 채택 후보 아님**): B + BTC 직전 1h 거래대금 ≥ 7일 시간당 평균 ×
  `CAPIT_VOL_RATIO`(8)면 예외(배율 1).
* 자: 현물 창 밖(2018-01-01~2020-09-01, WAN-438 현물 루트 · 불가능 틱 격리 포함)과 선물 6년
  (2020-09-15~2026-07-22)을 **이어 붙인 8.6년 경로**. 크기는 **미끄러짐 포함**(손절 = 그 1분 저가)
  평가손 MDD `MDD_TARGET`(35%)에 맞춘다(로그 이분 · 탐색과 같은 범위·횟수). 같은 크기의
  손절가 체결을 병기한다.
* 통과(`verdict`): (1) B의 8.6년 연환산(미끄러짐, 자기 크기)이 기준 A보다 높다 **그리고** (2) **같은
  크기 = A의 맞춤 크기**에서 창 밖 · 6년 앞 · 6년 뒤 세 구간 모두 B의 수익/MDD가 A보다 나쁘지 않다.
  🔁 **처음 판(첫 실행)은 「B의 맞춤 크기에서도」를 조건에 더했다가 철회했다** — A를 B 크기(2.89%)로
  돌리면 창 밖 낙폭이 57%라 **한도(35%)를 넘는 쓸 수 없는 설정**이고, 그 A와의 비율 비교는 판정 자가
  아니다(사용자 지적 2026-09-29). 그 비교는 표에 **참고로만** 남긴다(`reference_lines`).

## 탐색과 다른 점

* 층의 배율을 **채택 북 배치**(`run_leverage_book(risk_scale=)`)에도 넣는다(WAN-341) — 작아진 진입이
  명목 자리를 덜 차지해 다른 칸의 자리가 바뀐다. 탐색처럼 **복리 층에서만** 바꾼 판
  (`in_book=False`)을 같은 표에 대조로 싣고, 그 차이가 판정을 바꾸는지 코드가 적는다.
* 폭락 국면 판정은 진입 분 **이전에 확정된** 1분봉만 본다 — `btc_lookback_return`·
  `btc_volume_ratio`가 순수 함수이고 테스트가 「진입 분 이후를 바꿔도 값이 불변」을 동작으로
  고정한다.
* 시장별 BTC — 선물 구간은 **선물** BTC 1분봉(운영 DB), 현물 창 밖은 **현물** BTC 1분봉(현물 루트
  DB)이다. 두 시장을 섞지 않는다.

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()`
그대로 · 새 엔진 인자는 옵트인이고 안 주면 비트 동일 · 핀 없음 WAN-305) · 이 팔은 **채택 좌표가
아니다** · 페이퍼 규칙(WAN-435) 변경은 **사용자 결정** · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan438_spot_stress as m
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload
from backtest.wan424_stoch_ob_arm import TIMEFRAMES as FUT_TIMEFRAMES
from backtest.wan424_stoch_ob_arm import base_cell_kwargs, build_base_payloads
from data import spot_klines
from data.storage import OhlcvStore

REPORT_DIR = Path(__file__).resolve().parent / "reports"
CSV_PATH = REPORT_DIR / "wan439_crash_size_layer.csv"
SUMMARY_PATH = REPORT_DIR / "wan439_crash_size_layer_summary.md"

# --- 착수 전 고정 ---------------------------------------------------------------------------
CRASH_THRESHOLD = -0.05
"""진입 직전 BTC 24h 수익률이 이 값 이하면 폭락 국면."""
CRASH_MULT = 0.5
"""폭락 국면 진입의 크기 배율."""
CAPIT_VOL_RATIO = 8.0
"""C(대조 전용): BTC 직전 1h 거래대금 ÷ 직전 7일 시간당 평균이 이 값 이상이면 층 예외."""
CRASH_LOOKBACK_MIN = 1440
VOL_RECENT_MIN = 60
VOL_BASE_MIN = 7 * 1440
MDD_TARGET = 0.35
"""미끄러짐 포함(손절 = 그 1분 저가) 8.6년 평가손 MDD 목표."""
FIT_LO, FIT_HI, FIT_ITERATIONS = 0.002, 0.08, 16
"""크기 맞춤 로그 이분 — 탐색(`search.fit`)과 같은 범위·횟수."""
RULES = m.GATED
__all__ = ["FUT_TIMEFRAMES"]  # wan439_b15m이 읽는 이름(나머지는 모듈 속성으로 접근)
"""페이퍼 좌표(WAN-436/438) — 손절 2배 · 초반 건너뛰기 · 24h 예산 10 · 몰림 중 BTC 4h ≤ −2.5%."""

WINDOW_START = "2018-01-01"
"""창 밖 구간의 진입 시작(그 전은 워밍업 — WAN-438)."""
STRESS_BTC_FROM = "2017-12-01"
FUT_START = harness.DEFAULT_START
FUT_END = harness.DEFAULT_END

ARM_A = "A 기준(층 없음)"
ARM_B = "B 폭락 ×0.5"
ARM_C = "C B+투매 예외(대조 전용)"
ARMS: tuple[str, ...] = (ARM_A, ARM_B, ARM_C)
FILLS: tuple[str, ...] = ("bar_low", "stop")
FIT_FILL = "bar_low"

SEG_CHAIN = "8.6년"
SEG_SPOT = "창 밖"
SEG_FRONT = "6년 앞"
SEG_BACK = "6년 뒤"
SEG_FUT = "선물 6년(참고)"
SEGMENTS: tuple[str, ...] = (SEG_CHAIN, SEG_SPOT, SEG_FRONT, SEG_BACK, SEG_FUT)
VERDICT_SEGMENTS: tuple[str, ...] = (SEG_SPOT, SEG_FRONT, SEG_BACK)

SIZE_OWN = "자기 맞춤"


def combo_label(arm: str, in_book: bool) -> str:
    """팔 × 층 반영 방식 라벨(A는 층이 없어 언제나 「북」)."""
    return f"{arm.split()[0]}·{'북' if in_book else '복리층만'}"


def size_label(arm: str, in_book: bool) -> str:
    return f"{combo_label(arm, in_book)} 맞춤 크기"


SIZE_A = size_label(ARM_A, True)

CHECKSUM_TOL = 1e-9
#: WAN-438 공개 CSV — 현물 · %K<25 · 게이트 −2.5% · 손절가 체결 · 창 밖 전체 · 거래당 2%.
ANCHOR_SPOT = {"trades": 228, "total_return": -0.24984889557218726, "mdd_low": 0.3600039267387166}
#: WAN-436 공개 CSV — ≤ −2.5% · 손절가 체결 · 거래당 2% · 6년.
ANCHOR_FUT = {"trades": 1075, "total_return": 6.785146607822272, "mdd_low": 0.31999426500913464}
ANCHOR_RISK = 0.02

MINUTE_MS = 60_000


# ---------------------------------------------------------------------------
# 폭락 국면 판정 — 순수 함수 · 진입 분 이전에 확정된 1분봉만
# ---------------------------------------------------------------------------


def btc_lookback_return(
    times: np.ndarray, closes: np.ndarray, entry_ms: int, minutes: int = CRASH_LOOKBACK_MIN
) -> float:
    """진입 **직전 확정 1분봉** 종가 대비 그보다 `minutes`분 앞 종가의 수익률.

    `wan436.btc_return`과 같은 식(창만 다르다). 진입 분(`open_time == entry_ms`)은 보지 않는다 — 그
    봉은 진입 순간에 아직 안 닫혔다. 비는 분은 직전 값으로 본다.
    """
    j = int(np.searchsorted(times, entry_ms)) - 1
    if j < 0:
        return math.nan
    i = int(np.searchsorted(times, int(times[j]) - minutes * MINUTE_MS, side="right")) - 1
    if i < 0:
        return math.nan
    return float(closes[j] / closes[i] - 1.0)


def btc_volume_ratio(times: np.ndarray, quote_cum: np.ndarray, entry_ms: int) -> float:
    """직전 1h 거래대금 ÷ 직전 7일 시간당 평균(분 `[t − 60m, t)` · `[t − 7d, t)`).

    `quote_cum`은 `[0, cumsum(volume × close)]`(길이 len(times)+1). 탐색(`shape.py`)과 같은 식이다.
    """
    j = int(np.searchsorted(times, entry_ms))
    if j < 2:
        return math.nan
    i1 = int(np.searchsorted(times, entry_ms - VOL_RECENT_MIN * MINUTE_MS))
    i7 = int(np.searchsorted(times, entry_ms - VOL_BASE_MIN * MINUTE_MS))
    recent = float(quote_cum[j] - quote_cum[i1])
    base = float(quote_cum[j] - quote_cum[i7]) / max((j - i7) / 60.0, 1.0)
    return recent / base if base > 0.0 else math.nan


def layer_multiplier(arm: str, btc_24h: float, vol_ratio: float) -> float:
    """층 배율. NaN(판정 불가)은 폭락 국면으로 보지 않는다 — 지어내지 않는다."""
    if arm == ARM_A:
        return 1.0
    if arm not in (ARM_B, ARM_C):
        raise ValueError(f"모르는 팔: {arm!r}")
    if not btc_24h <= CRASH_THRESHOLD:  # NaN도 여기서 걸러진다
        return 1.0
    if arm == ARM_C and vol_ratio >= CAPIT_VOL_RATIO:
        return 1.0
    return CRASH_MULT


# ---------------------------------------------------------------------------
# 시장 하나 = payload + 팔 후보 + 진입 순간 BTC 특징
# ---------------------------------------------------------------------------


@dataclass
class Market:
    name: str
    payloads: list[CellPayload]
    entries: list[w.ArmEntry]
    counts: list[int]
    btc_24h: list[float]
    vol_ratio: list[float]

    def without(self, timeframe: str) -> Market:
        """그 TF의 칸·후보를 뺀 시장 — 신호 수는 남은 후보로 **다시** 센다(WAN-438 탐색과 같다)."""
        keep = [i for i, e in enumerate(self.entries) if e.timeframe != timeframe]
        entries = [self.entries[i] for i in keep]
        return Market(
            self.name,
            [p for p in self.payloads if p.timeframe != timeframe],
            entries,
            w.signal_counts([int(e.cand.entry_time) for e in entries]),
            [self.btc_24h[i] for i in keep],
            [self.vol_ratio[i] for i in keep],
        )

    def layer(self, arm: str) -> list[float]:
        return [
            layer_multiplier(arm, b, v) for b, v in zip(self.btc_24h, self.vol_ratio, strict=True)
        ]


def btc_features(
    store: OhlcvStore, entries: Sequence[w.ArmEntry], start_ms: int, end_ms: int
) -> tuple[list[float], list[float]]:
    """그 시장의 BTC 1분봉으로 진입마다 (24h 수익률, 1h 거래대금 비)."""
    frame = w._minute_frame(store, w.BTC_SYMBOL, start_ms, end_ms)
    t = frame["open_time"].to_numpy(np.int64)
    c = frame["close"].to_numpy(float)
    q = np.concatenate([[0.0], np.cumsum((frame["volume"] * frame["close"]).to_numpy(float))])
    b24 = [btc_lookback_return(t, c, int(e.cand.entry_time)) for e in entries]
    vr = [btc_volume_ratio(t, q, int(e.cand.entry_time)) for e in entries]
    return b24, vr


def futures_market(
    jobs: int, payload_dir: Path, timeframes: Sequence[str] = FUT_TIMEFRAMES
) -> Market:
    payloads = build_base_payloads(jobs=jobs, payload_dir=payload_dir, timeframes=timeframes)
    store = OhlcvStore(harness.DB_PATH)
    entries = w.build_entries(payloads, store=store)
    counts = w.signal_counts([int(e.cand.entry_time) for e in entries])
    lo = parse_date_ms(FUT_START) - (VOL_BASE_MIN + 60) * MINUTE_MS
    b24, vr = btc_features(store, entries, lo, parse_date_ms(FUT_END))
    return Market("선물", payloads, entries, counts, b24, vr)


def spot_market(jobs: int, root: Path, timeframes: Sequence[str] = m.TIMEFRAMES) -> Market:
    payloads = m.spot_payloads(
        root, start=m.STRESS_START, end=m.STRESS_END, jobs=jobs, timeframes=timeframes
    )
    store = OhlcvStore(spot_klines.root_db_path(root))
    entries = m.entries_for(
        payloads,
        store,
        start_ms=m.ms(m.STRESS_START),
        end_ms=m.ms(m.STRESS_END),
        k_threshold=m.PRIMARY_K,
        entry_from_ms=m.ms(WINDOW_START),
        entry_to_ms=m.ms(m.STRESS_END),
    )
    counts = w.signal_counts([int(e.cand.entry_time) for e in entries])
    b24, vr = btc_features(store, entries, m.ms(STRESS_BTC_FROM), m.ms(m.STRESS_END))
    return Market("현물", payloads, entries, counts, b24, vr)


def place_arm(mk: Market, arm: str, fill: str, *, in_book: bool) -> list[w.PlacedTrade]:
    layer = None if arm == ARM_A else mk.layer(arm)
    return w.place(
        mk.payloads,
        mk.entries,
        RULES,
        counts=mk.counts,
        stop_fill=fill,
        size_layer=layer,
        layer_in_book=in_book,
    )


# ---------------------------------------------------------------------------
# 8.6년 경로 · 크기 맞춤
# ---------------------------------------------------------------------------


def _mdd(low: np.ndarray) -> float:
    peak = np.maximum.accumulate(np.maximum(low, 1.0))
    return float((1.0 - low / peak).max())


def chain(
    spot: Sequence[w.PlacedTrade], fut: Sequence[w.PlacedTrade], risk: float
) -> tuple[float, float]:
    """창 밖 다음 선물 6년을 이어 붙인 (총수익, 평가손 MDD).

    뒤 구간은 앞 구간 마지막 종가 평가액에서 시작한다(탐색 `rsim.chain`과 같은 식)."""
    a = w.mtm_path(spot, risk=risk)
    b = w.mtm_path(fut, risk=risk)
    scale = float(a.mtm_close[-1])
    low = np.concatenate([a.mtm_low, b.mtm_low * scale])
    return float(b.mtm_close[-1]) * scale - 1.0, _mdd(low)


def chain_realized(
    spot: Sequence[w.PlacedTrade], fut: Sequence[w.PlacedTrade], risk: float
) -> tuple[float, float, float]:
    """`chain`과 같은 이음 · (총수익, **실현 손익 기준** MDD, 평가손 기준 MDD).

    실현 기준은 청산된 손익만 본다 — 열린 포지션의 평가손은 안 센다(사용자 질문 2026-09-29).
    """
    a = w.mtm_path(spot, risk=risk)
    b = w.mtm_path(fut, risk=risk)
    assert a.realized is not None and b.realized is not None
    scale = float(a.mtm_close[-1])
    realized = np.concatenate([a.realized, b.realized * scale])
    low = np.concatenate([a.mtm_low, b.mtm_low * scale])
    return float(b.mtm_close[-1]) * scale - 1.0, _mdd(realized), _mdd(low)


def fit_risk(fn: Callable[[float], float], target: float = MDD_TARGET) -> float:
    """`fn(risk) → MDD`가 target 이하인 가장 큰 크기(로그 이분)."""
    lo, hi, best = FIT_LO, FIT_HI, FIT_LO
    for _ in range(FIT_ITERATIONS):
        mid = math.sqrt(lo * hi)
        if fn(mid) <= target:
            best = lo = mid
        else:
            hi = mid
    return best


def _count(layered: set[tuple[str, str, int]], trades: Sequence[w.PlacedTrade]) -> int:
    return sum((t.symbol, t.timeframe, t.entry_time) in layered for t in trades)


def _years(a: int, b: int) -> float:
    return (b - a) / 86_400_000 / 365.25


def cagr(total: float, years: float) -> float:
    return float(max(1.0 + total, 1e-12) ** (1.0 / years) - 1.0)


@dataclass(frozen=True)
class Row:
    arm: str
    in_book: bool
    fill: str
    size_basis: str
    risk: float
    segment: str
    trades: int
    layered: int
    total_return: float
    cagr: float
    mdd_low: float

    @property
    def ratio(self) -> float:
        return self.total_return / self.mdd_low if self.mdd_low > 0 else math.nan


def segment_rows(
    arm: str,
    in_book: bool,
    fill: str,
    size_basis: str,
    risk: float,
    spot: Sequence[w.PlacedTrade],
    fut: Sequence[w.PlacedTrade],
    boundary_ms: int,
    layered: set[tuple[str, str, int]],
) -> list[Row]:
    w0, w1 = parse_date_ms(WINDOW_START), m.ms(m.STRESS_END)
    f0, f1 = parse_date_ms(FUT_START), parse_date_ms(FUT_END)
    front = [t for t in fut if not t.back]
    back = [t for t in fut if t.back]
    total, dd = chain(spot, fut, risk)
    out = [
        Row(
            arm,
            in_book,
            fill,
            size_basis,
            risk,
            SEG_CHAIN,
            len(spot) + len(fut),
            _count(layered, (*spot, *fut)),
            total,
            cagr(total, _years(w0, f1)),
            dd,
        )
    ]
    for seg, trades, years in (
        (SEG_SPOT, spot, _years(w0, w1)),
        (SEG_FRONT, front, _years(f0, boundary_ms)),
        (SEG_BACK, back, _years(boundary_ms, f1)),
        (SEG_FUT, fut, _years(f0, f1)),
    ):
        sim = w.simulate(trades, risk=risk)
        out.append(
            Row(
                arm,
                in_book,
                fill,
                size_basis,
                risk,
                seg,
                sim.trades,
                _count(layered, trades),
                sim.total_return,
                cagr(sim.total_return, years),
                sim.mdd_low,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 판정 — 착수 전 고정
# ---------------------------------------------------------------------------


def verdict(rows: Sequence[Row], arm: str, *, in_book: bool = True) -> tuple[bool, list[str]]:
    """`arm`이 A를 이기는가 — (통과 여부, 근거 줄)."""

    def get(a: str, ib: bool, basis: str, seg: str, fill: str = FIT_FILL) -> Row:
        a_ib = True if a == ARM_A else ib
        hits = [
            r
            for r in rows
            if r.arm == a
            and r.in_book == a_ib
            and r.size_basis == basis
            and r.segment == seg
            and r.fill == fill
        ]
        if len(hits) != 1:
            raise KeyError((a, a_ib, basis, seg, fill, len(hits)))
        return hits[0]

    lines: list[str] = []
    a_own = get(ARM_A, True, SIZE_OWN, SEG_CHAIN)
    x_own = get(arm, in_book, SIZE_OWN, SEG_CHAIN)
    cond1 = x_own.cagr > a_own.cagr
    lines.append(
        f"(1) 8.6년 연환산(미끄러짐 · 자기 크기): {arm} {x_own.cagr:+.2%} vs A {a_own.cagr:+.2%} → "
        + ("✅" if cond1 else "❌")
    )
    cond2 = True
    for seg in VERDICT_SEGMENTS:
        ok, line = _compare(get(ARM_A, True, SIZE_A, seg), get(arm, in_book, SIZE_A, seg), arm)
        cond2 = cond2 and ok
        lines.append(f"(2) {SIZE_A} · {seg}: {line}")
    return cond1 and cond2, lines


def _compare(ra: Row, rx: Row, arm: str) -> tuple[bool, str]:
    ok = rx.ratio >= ra.ratio
    return ok, (
        f"수익/MDD {arm[0]} {rx.ratio:+.3f} ({rx.total_return:+.1%}/{rx.mdd_low:.1%}) vs A "
        f"{ra.ratio:+.3f} ({ra.total_return:+.1%}/{ra.mdd_low:.1%}) → " + ("✅" if ok else "❌")
    )


def reference_lines(rows: Sequence[Row], arm: str, *, in_book: bool = True) -> list[str]:
    """판정 밖 참고 — A를 `arm`의 맞춤 크기로 돌린 비교(그 A는 한도를 넘는 설정일 수 있다)."""
    basis = size_label(arm, in_book)
    out: list[str] = []
    a_ib = True
    for seg in (SEG_CHAIN, *VERDICT_SEGMENTS):
        ra = [
            r
            for r in rows
            if (r.arm, r.in_book, r.size_basis, r.segment, r.fill)
            == (ARM_A, a_ib, basis, seg, FIT_FILL)
        ]
        rx = [
            r
            for r in rows
            if (r.arm, r.in_book, r.size_basis, r.segment, r.fill)
            == (arm, in_book, basis, seg, FIT_FILL)
        ]
        if len(ra) != 1 or len(rx) != 1:
            continue
        _, line = _compare(ra[0], rx[0], arm)
        out.append(f"{basis} {rx[0].risk:.2%} · {seg}: {line} · A 낙폭 {ra[0].mdd_low:.1%}")
    return out


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def checksum(spot: Sequence[w.PlacedTrade], fut: Sequence[w.PlacedTrade]) -> list[str]:
    """A 팔(층 없음) · 손절가 체결 · 거래당 2% = WAN-438/436 공개 CSV와 같아야 한다.

    공개 CSV는 WAN-443 이전 정렬(보유 0분 거래의 손익이 뒤 거래 크기에서 안 빠짐)로 났다 — 검산은
    배선(후보·배치)이 같은지를 보는 것이라 **그 옛 정렬로** 대조하고, 지금 정렬의 값을 옆에 적는다.
    """
    lines: list[str] = []
    for name, trades, anchor in (("현물 창 밖", spot, ANCHOR_SPOT), ("선물 6년", fut, ANCHOR_FUT)):
        sim = w.simulate(trades, risk=ANCHOR_RISK, legacy_zero_duration_order=True)
        got = {"trades": sim.trades, "total_return": sim.total_return, "mdd_low": sim.mdd_low}
        diff = max(abs(float(got[k]) - float(anchor[k])) for k in anchor)
        if diff > CHECKSUM_TOL:
            raise AssertionError(f"검산 실패 — {name}: {got} vs 공개 {anchor} (차 {diff:.2e})")
        now = w.simulate(trades, risk=ANCHOR_RISK)
        zero = sum(t.exit_time <= t.entry_time for t in trades)
        lines.append(
            f"{name}: 거래 {sim.trades} · 수익 {sim.total_return:+.4%} · MDD "
            f"{sim.mdd_low:.4%} — 공개 CSV와 최대 차 {diff:.2e}(WAN-443 이전 정렬) · "
            f"보유 0분 {zero}건 · 지금 정렬 수익 {now.total_return:+.4%} · MDD {now.mdd_low:.4%}"
        )
    return lines


def assert_adopted_take_profit_liquidity() -> None:
    """익절 회계는 채택 값(메이커, WAN-370)이어야 WAN-436/438과 같은 팔이다 — 후보·배치 인자가
    사는 `wan424.base_cell_kwargs`·`place_segments`가 그 값을 쓰는지 여기서 확인한다."""
    kwargs = base_cell_kwargs()
    if kwargs["take_profit_liquidity"] is not harness.ADOPTED_TAKE_PROFIT_LIQUIDITY:
        raise AssertionError("base_cell_kwargs의 take_profit_liquidity가 채택 값이 아닙니다")


def run(jobs: int, payload_dir: Path, root: Path) -> tuple[list[Row], list[str]]:
    assert_adopted_take_profit_liquidity()
    t0 = time.monotonic()
    notes: list[str] = []
    fut_mk = futures_market(jobs, payload_dir)
    notes.append(f"선물 후보 {len(fut_mk.entries)} ({time.monotonic() - t0:.0f}초)")
    spot_mk = spot_market(jobs, root)
    notes.append(f"현물 후보 {len(spot_mk.entries)} ({time.monotonic() - t0:.0f}초)")
    # 앞/뒤 분할은 거래마다 **칸별** 경계로 이미 갈렸다(`PlacedTrade.back`). 여기서 쓰는 대표
    # 경계는 연환산의 햇수에만 들어간다 — 칸마다 마지막 봉이 달라 며칠 어긋나므로(WAN-166)
    # 중앙값을 쓰고, 벌어짐이 일주일을 넘으면 뭔가 잘못 붙은 것이라 멈춘다.
    bounds = sorted(p.boundary_ms for p in fut_mk.payloads)
    if bounds[-1] - bounds[0] > 7 * 86_400_000:
        raise AssertionError(
            f"선물 칸의 앞/뒤 경계가 일주일 넘게 벌어졌다: {bounds[0]}~{bounds[-1]}"
        )
    boundary_ms = bounds[len(bounds) // 2]
    notes.append(
        f"앞/뒤 경계(칸별 · 거래 분할에 쓰임) {m._utc(bounds[0])}~{m._utc(bounds[-1])} · "
        f"연환산 햇수는 중앙값 {m._utc(boundary_ms)}"
    )
    for mk in (spot_mk, fut_mk):
        crash = sum(b <= CRASH_THRESHOLD for b in mk.btc_24h)
        nan = sum(math.isnan(b) for b in mk.btc_24h)
        notes.append(
            f"{mk.name}: 폭락 국면 후보 {crash}/{len(mk.entries)} · BTC 24h 판정 불가 {nan}"
        )

    placed: dict[tuple[str, bool, str], tuple[list[w.PlacedTrade], list[w.PlacedTrade]]] = {}
    combos = [(ARM_A, True)] + [(a, ib) for a in (ARM_B, ARM_C) for ib in (True, False)]
    for arm, ib in combos:
        for fill in FILLS:
            placed[(arm, ib, fill)] = (
                place_arm(spot_mk, arm, fill, in_book=ib),
                place_arm(fut_mk, arm, fill, in_book=ib),
            )
    notes += checksum(*placed[(ARM_A, True, "stop")])
    # 층을 북에 넣으면 배치가 달라지는가(거래 집합 비교)
    for arm in (ARM_B, ARM_C):
        for fill in FILLS:
            keys = []
            for ib in (True, False):
                s, f = placed[(arm, ib, fill)]
                keys.append({(t.symbol, t.timeframe, t.entry_time) for t in (*s, *f)})
            notes.append(
                f"{arm} · {fill}: 북 반영 {len(keys[0])}건 · 복리 층만 {len(keys[1])}건 · "
                f"한쪽에만 있는 거래 {len(keys[0] ^ keys[1])}건"
            )

    fitted: dict[tuple[str, bool], float] = {}
    for arm, ib in combos:
        s, f = placed[(arm, ib, FIT_FILL)]

        def fit_mdd(r: float, s: list[w.PlacedTrade] = s, f: list[w.PlacedTrade] = f) -> float:
            return chain(s, f, r)[1]

        fitted[(arm, ib)] = fit_risk(fit_mdd)
    notes.append(f"맞춤·표 산출 {time.monotonic() - t0:.0f}초")

    layered: dict[str, set[tuple[str, str, int]]] = {}
    for arm in ARMS:
        layered[arm] = {
            (e.symbol, e.timeframe, int(e.cand.entry_time))
            for mk in (spot_mk, fut_mk)
            for e, mult in zip(mk.entries, mk.layer(arm), strict=True)
            if mult != 1.0
        }
    rows: list[Row] = []
    for arm, ib in combos:
        bases = {SIZE_OWN: fitted[(arm, ib)]}
        bases.update({size_label(a, b): r for (a, b), r in fitted.items()})
        for basis, risk in bases.items():
            for fill in FILLS:
                s, f = placed[(arm, ib, fill)]
                rows += segment_rows(arm, ib, fill, basis, risk, s, f, boundary_ms, layered[arm])
    notes.append(f"총 {time.monotonic() - t0:.0f}초")
    return rows, notes


def rows_frame(rows: Sequence[Row]) -> pd.DataFrame:
    return pd.DataFrame([{**dataclasses.asdict(r), "ratio": r.ratio} for r in rows])


def rows_from_frame(frame: pd.DataFrame) -> list[Row]:
    return [
        Row(
            arm=str(rec["arm"]),
            in_book=bool(rec["in_book"]),
            fill=str(rec["fill"]),
            size_basis=str(rec["size_basis"]),
            risk=float(rec["risk"]),
            segment=str(rec["segment"]),
            trades=int(rec["trades"]),
            layered=int(rec["layered"]),
            total_return=float(rec["total_return"]),
            cagr=float(rec["cagr"]),
            mdd_low=float(rec["mdd_low"]),
        )
        for rec in frame.to_dict("records")
    ]


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def _cell(r: Row) -> str:
    c = "—" if math.isnan(r.cagr) else f"{r.cagr:+.1%}"
    return f"{r.total_return:+.0%} · 연 {c} / {r.mdd_low:.1%}"


def render(rows: Sequence[Row], notes: Sequence[str], elapsed: str) -> str:
    out = [
        "# WAN-439 — 스토캐스틱 팔 위 「폭락 국면(BTC 24h ≤ −5%) 크기 ×0.5」 정식 측정",
        "",
        "> 자동 생성(`uv run python -m backtest.wan439_crash_size_layer`). 결정문:"
        " `docs/decisions/wan439.md`. 🚨 채택 근거 아님 · 기본값·토대·페이퍼 규칙 불변.",
        "",
        f"좌표: 페이퍼 좌표({RULES.label}) · 현물 창 밖 {WINDOW_START}~{m.STRESS_END} + 선물 "
        f"{FUT_START}~{FUT_END} · 크기 = 미끄러짐 포함 8.6년 평가손 MDD {MDD_TARGET:.0%} 맞춤.",
        "",
        "## 판정 (착수 전 고정 · 층을 채택 북에 반영한 판이 정본)",
        "",
    ]
    for arm in (ARM_B, ARM_C):
        for ib in (True, False):
            try:
                ok, lines = verdict(rows, arm, in_book=ib)
            except KeyError:
                continue
            tag = "북 반영(정본)" if ib else "복리 층만(탐색 방식 · 대조)"
            out.append(f"### {arm} — {tag}: **{'통과' if ok else '불통과'}**")
            out.append("")
            out += [f"* {line}" for line in lines]
            ref = reference_lines(rows, arm, in_book=ib)
            if ref:
                out.append("")
                out.append(
                    f"참고(판정 밖) — A를 {arm[0]}의 맞춤 크기로 돌린 비교. 그 A는 8.6년 낙폭이 "
                    "한도를 넘는 설정이라 판정 자가 아니다:"
                )
                out += [f"* {line}" for line in ref]
            out.append("")
    out += ["## 8.6년 표 — 각 팔 자기 맞춤 크기 (칸: 총수익 · 연환산 / 평가손 MDD)", ""]
    header = "| 팔 | 배치 | 체결 | 크기 | " + " | ".join(SEGMENTS) + " |"
    out += [header, "|" + "---|" * (4 + len(SEGMENTS))]
    for arm in ARMS:
        for ib in (True, False):
            for fill in FILLS:
                seg_rows = {
                    r.segment: r
                    for r in rows
                    if r.arm == arm
                    and r.in_book == ib
                    and r.fill == fill
                    and r.size_basis == SIZE_OWN
                }
                if not seg_rows:
                    continue
                risk = next(iter(seg_rows.values())).risk
                tag = "북" if ib else "복리 층만"
                cells = " | ".join(_cell(seg_rows[s]) for s in SEGMENTS)
                out.append(f"| {arm} | {tag} | {w._FILL_LABEL[fill]} | {risk:.2%} | {cells} |")
    out += ["", "## 같은 크기 비교 (미끄러짐 · 칸: 총수익 / MDD · 수익/MDD)", ""]
    out += ["| 크기 | 팔 | " + " | ".join(VERDICT_SEGMENTS) + " |", "|---|---|---|---|---|"]
    for basis in (SIZE_A, size_label(ARM_B, True), size_label(ARM_C, True)):
        for arm in ARMS:
            seg_rows = {
                r.segment: r
                for r in rows
                if r.arm == arm and r.in_book and r.fill == FIT_FILL and r.size_basis == basis
            }
            if not seg_rows:
                continue
            risk = next(iter(seg_rows.values())).risk
            cells = " | ".join(
                f"{seg_rows[s].total_return:+.0%} / {seg_rows[s].mdd_low:.1%} · "
                f"{seg_rows[s].ratio:+.2f}"
                for s in VERDICT_SEGMENTS
            )
            out.append(f"| {basis} {risk:.2%} | {arm} | {cells} |")
    out += ["", "## 실행 기록", ""] + [f"* {n}" for n in notes] + [f"* {elapsed}", ""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--payload-dir", type=Path, default=w.DEFAULT_PAYLOAD_DIR)
    ap.add_argument("--root", type=Path, default=m.DEFAULT_ROOTS / "stress")
    ap.add_argument("--from-csv", action="store_true", help="CSV에서 요약만 다시 쓴다")
    args = ap.parse_args(argv)
    if args.from_csv:
        rows = rows_from_frame(pd.read_csv(CSV_PATH))
        old = SUMMARY_PATH.read_text(encoding="utf-8") if SUMMARY_PATH.exists() else ""
        notes = [
            line[2:]
            for line in old.split("## 실행 기록", 1)[-1].splitlines()
            if line.startswith("* ")
        ]
        SUMMARY_PATH.write_text(render(rows, notes[:-1], notes[-1] if notes else ""), "utf-8")
        return 0
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    t0 = time.monotonic()
    rows, notes = run(jobs, args.payload_dir, args.root)
    rows_frame(rows).to_csv(CSV_PATH, index=False)
    elapsed = f"실측 {time.monotonic() - t0:.0f}초 · `--jobs {jobs}`"
    SUMMARY_PATH.write_text(render(rows, notes, elapsed), encoding="utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
