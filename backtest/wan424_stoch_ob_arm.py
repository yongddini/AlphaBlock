"""WAN-424 §1: 스토캐스틱 × 오더블록 팔을 **저장소로 복원**하고 WAN-423 §6·§7 표를 재현한다.

## 왜 이 모듈이 있나

WAN-423 §6·§7이 이 저장소 처음으로 **뒷구간 양수**를 냈는데(롱 · ts4 · 손절폭 하한 4% ·
%K<25 → full +0.120 · is +0.106 · **oos_warm +0.165 · 679거래**), 그 측정 스크립트는 세션
스크래치에서 돌았고 저장소에 실리지 않았다 — 표는 **명령 한 줄로 재현되지 않았다**.
WAN-424는 그 팔에 매칭 대조군을 거는 이슈이고, 사용자 결정(2026-09-22)으로 **단계를 끊는다**:

* **§1(이 모듈)** — 팔을 복원하고 옛 숫자를 재현한다. 안 맞으면 거기서 멈춘다.
* §2 — 파일럿 견적 → 매칭 널(사용자 결정 뒤).

## 팔의 정의 (WAN-423 결정문 §6·§7 · 스크래치 `diag_patch.py` 그대로)

* **후보** — 채택 엔진의 base 후보(볼린저 지정가 · 인과 취소 · 존폭 필터 끔) 중
  **롱 · 첫 탭**(`retap_mode="once"`) · **재진입 없음** · 보수 엔진(`pen_5bp` × 같은 분 익절
  금지). 31종목 × 9TF(1h·2h·3h·4h·6h·8h·12h·1d·1w) · 못 박은 채택 창.
* **`tsN` 청산** — 익절선을 **없애고** 손절선(존 무효화 경계)은 그대로 둔 채, 진입 봉을
  포함해 상위TF 봉 **N개**를 채운 뒤 그 마지막 1분봉 종가에 나간다. 그 사이에 손절선에
  닿으면 손절(`STOP_LOSS`)이고 아니면 시간 청산(`END_OF_DATA` = 테이커). 진입 시각·진입가·
  손절가는 base 후보 그대로다(`time_exit`).
* **손절폭 하한** — `(진입가 − 손절가) / 진입가 ≥ 하한`. 손절폭 가드는 끈다(하한이 대신한다).
* **%K 필터** — 트레이딩뷰 스토캐스틱(20,10,10)의 **%K 선**(= SMA(raw, 10)) `< 문턱`.
  🚨 **직전 확정봉**의 %K를 읽는다 — 진입 봉은 형성 중이라 그 봉의 %K는 룩어헤드다
  (WAN-119/132 규약). `stoch_k_line` · `k_before`가 정의이고 회귀 테스트가 동작으로 고정한다.
* **회계** — 채택 북(`LeverageBookParams()` = cap_only 5배 · 한 지갑) · 복리 끔 · 익절 메이커.
  판정 자는 거래당 net R(`book_cli.net_r`)이다.

## 엔진을 건드리지 않았다 — 후처리다

스크래치는 `confirmation_arm.derive_arm_candidates`를 **몽키패치**해 `run_cells` 안에서 팔을
만들었다. 그 경로를 저장소에 싣으려면 엔진 소스(`confirmation_arm.py` · `wan169`)를 고쳐야
하고, 그러면 **모든** 후보 캐시·야간 타임라인 캐시의 리비전이 갈린다(WAN-253/394). 이 팔은
base 후보의 진입·손절을 그대로 쓰고 **청산만** 바꾸므로, base 후보를 만든 뒤 같은 1분봉
서브스텝(`build_substeps` — `reentry_window_context`와 같은 식)으로 청산을 다시 푸는 것과
**같은 계산**이다. 그 등식은 주장이 아니라 검산이다(결정문 §1 — 스크래치 캐시의 팔 후보와
후보 단위 대조).

## 3h 주기

3h는 거래소 주기가 아니라(바이낸스 봉 목록에 없다) WAN-423이 1h에서 접어 저장한 것이다.
`data.models`·`backtest.sweep`의 주기 표에 **이 프로세스에서만** 등록한다(`register_3h`) —
운영 코드(수집기·러너)가 3h를 지원한다고 선언하지 않기 위해서다. 3h는 하루를 정확히 나누므로
`floor(t / 3h)` 정렬이 저장 봉과 맞는다.

⚠️ **1w의 서브스텝 버킷은 epoch 주(목요일 시작)다** — `build_substeps`가 `floor(t / 1w)`로
정렬하므로 시간 청산의 「봉 N개」가 저장 주봉(월요일 시작)과 어긋난다. 스크래치도 같은 식이라
재현에는 영향이 없고, 9TF 중 1w 한 칸의 보유 길이만 며칠 밀린다(기록).

## 재현

```
uv run python -m backtest.wan424_stoch_ob_arm --jobs 4          # 후보 생성(무겁다) + 표
uv run python -m backtest.wan424_stoch_ob_arm --from-csv        # 표만 다시
```

**측정 전용 · 기본값·토대 불변**(`ConfluenceParams()`·`OrderBlockParams()`·
`LeverageBookParams()` 그대로 · 핀 없음 WAN-305) · 이 팔은 **채택 좌표가 아니다**(채택 북
−0.12R과 나란히 놓지 말 것) · 실거래 보류 유지(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import math
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.book_cli import iter_book_segments, net_r
from backtest.harness import SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS
from backtest.leverage_book import LeverageBookParams
from backtest.models import ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.substep import SubStep, build_substeps
from backtest.wan169_leverage_book import IS_SEGMENT, OOS_SEGMENT, CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.zone_limit_backtest import _Candidate
from common.costs import Liquidity
from data.models import timeframe_to_ms
from data.storage import OhlcvStore

REPORT_DIR = Path(__file__).resolve().parent / "reports"
CSV_PATH = REPORT_DIR / "wan424_stoch_ob_arm.csv"
SUMMARY_PATH = REPORT_DIR / "wan424_stoch_ob_arm_summary.md"
DEFAULT_PAYLOAD_DIR = Path(__file__).resolve().parent / "cache" / "wan424_payloads"

#: WAN-423 스크래치(`diag_cross.py`)의 31종목 — 채택 12종목 + 신규 7 + 신규 12. 순서까지 그대로.
NEW7: tuple[str, ...] = (
    "ALGO/USDT:USDT",
    "ATOM/USDT:USDT",
    "ETC/USDT:USDT",
    "THETA/USDT:USDT",
    "VET/USDT:USDT",
    "XLM/USDT:USDT",
    "XMR/USDT:USDT",
)
NEW12: tuple[str, ...] = tuple(
    f"{c}/USDT:USDT"
    for c in (
        "AVAX",
        "UNI",
        "SUSHI",
        "CRV",
        "SNX",
        "COMP",
        "YFI",
        "XTZ",
        "ZEC",
        "DASH",
        "NEO",
        "EGLD",
    )
)
SYMBOLS: tuple[str, ...] = tuple(harness.DEFAULT_SYMBOLS) + NEW7 + NEW12
TIMEFRAMES: tuple[str, ...] = ("1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d", "1w")
SEGMENTS: tuple[str, ...] = ("full", "is", "oos_warm")

#: 트레이딩뷰 스토캐스틱(20,10,10) — %K 선 = SMA(raw %K, 10). 결정문이 못 박은 정의다.
K_LENGTH = 20
K_SMOOTH = 10

HOLD_BARS: tuple[int, ...] = (4, 8, 12, 16, 24)
FLOORS: tuple[float, ...] = (0.03, 0.04)
THRESHOLDS: tuple[float, ...] = (15.0, 20.0, 25.0)
STOP_WIDTH_LOG_FLOOR = min(FLOORS)
"""팔 후보를 만들 때 먼저 거르는 가장 낮은 하한 — 이보다 좁은 후보는 어느 칸에도 안 들어간다."""

#: WAN-423 §7(스크래치 `hold_ls.log`)이 낸 롱 표 — 재현 검산의 기준값.
#: (보유 봉, 하한, 문턱) → {구간: (거래당 net R, 거래 수)}. 로그가 소수 셋째 자리로 찍었다.
WAN423_REFERENCE: dict[tuple[int, float, float], dict[str, tuple[float, int]]] = {
    (4, 0.04, 25.0): {"full": (0.120, 2710), "is": (0.106, 2031), "oos_warm": (0.165, 679)},
    (4, 0.04, 15.0): {"full": (0.154, 861), "is": (0.135, 627), "oos_warm": (0.203, 234)},
}

_THREE_HOURS_MS = 3 * 3_600_000


def register_3h() -> None:
    """이 프로세스(와 spawn 워커 — 모듈 임포트 시 실행)에서만 3h 주기를 인식시킨다."""
    import backtest.sweep as sweep_module
    import data.models as models_module

    models_module._TIMEFRAME_MS.setdefault("3h", _THREE_HOURS_MS)
    sweep_module._TIMEFRAME_MINUTES.setdefault("3h", 180)


register_3h()


# ---------------------------------------------------------------------------
# 스토캐스틱 %K — 순수 함수
# ---------------------------------------------------------------------------


def stoch_k_line(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    *,
    length: int = K_LENGTH,
    smooth: int = K_SMOOTH,
) -> list[float | None]:
    """봉 i의 %K 선(= SMA(raw %K, smooth)) — 봉 i가 **닫혀야** 아는 값이다.

    raw %K = 100·(종가 − 최근 `length`봉 최저) / (최고 − 최저). 폭이 0이면 50(스크래치 규약).
    워밍업(raw가 `length`봉, 평활이 `smooth`개 모자란 자리)은 `None`이다.
    """
    n = len(close)
    if not (len(high) == len(low) == n):
        raise ValueError("high/low/close 길이가 다릅니다.")
    raw: list[float | None] = []
    for i in range(n):
        if i + 1 < length:
            raw.append(None)
            continue
        hi = max(high[i + 1 - length : i + 1])
        lo = min(low[i + 1 - length : i + 1])
        raw.append(50.0 if hi <= lo else 100.0 * (close[i] - lo) / (hi - lo))
    out: list[float | None] = []
    for i in range(n):
        if i + 1 < smooth:
            out.append(None)
            continue
        window = raw[i + 1 - smooth : i + 1]
        vals = [v for v in window if v is not None]
        out.append(sum(vals) / smooth if len(vals) == smooth else None)
    return out


def k_before(
    times: Sequence[int], k: Sequence[float | None], entry_ms: int, tf_ms: int
) -> float | None:
    """진입 시각이 속한 봉의 **직전 확정봉** %K — 인과적으로 알 수 있는 가장 최신 값.

    스크래치(`hold_ls.py` `k_at`) 규약을 그대로 옮긴다: 진입 봉 `b = floor(t/tf)·tf`가
    저장돼 있으면 그 앞 봉의 값, 없으면(데이터 구멍) `b` 이하 마지막 봉의 **한 칸 앞** 값.
    두 경우 모두 진입 시점 이전에 닫힌 봉이다(뒤쪽은 한 봉 더 보수적이다).
    """
    if not times:
        return None
    bar = (entry_ms // tf_ms) * tf_ms
    i = bisect.bisect_left(times, bar)
    if i >= len(times) or times[i] != bar:
        i = bisect.bisect_right(times, bar) - 1
        if i < 0:
            return None
    return k[i - 1] if i - 1 >= 0 else None


def stoch_series(
    store: OhlcvStore, symbol: str, timeframe: str
) -> tuple[list[int], list[float | None]]:
    """저장 상위TF 전 이력의 (봉 open_time, %K 선). 창 앞 이력도 쓴다(워밍업 — 인과적이다)."""
    frame = store.load(symbol, timeframe, start_ms=0, end_ms=10**14)
    if frame.empty:
        return [], []
    times = [int(v) for v in frame["open_time"]]
    k = stoch_k_line(
        [float(v) for v in frame["high"]],
        [float(v) for v in frame["low"]],
        [float(v) for v in frame["close"]],
    )
    return times, k


# ---------------------------------------------------------------------------
# 시간 청산 팔 — 순수 함수
# ---------------------------------------------------------------------------


def stop_width(cand: _Candidate) -> float:
    """진입가 대비 손절폭(비율). 롱·숏 공용이고 0 이하면 0."""
    entry, stop = float(cand.entry_price), float(cand.stop_price)
    if entry <= 0:
        return 0.0
    raw = (entry - stop) if cand.side.sign > 0 else (stop - entry)
    return max(raw / entry, 0.0)


def time_exit(
    cand: _Candidate, *, index: int, bars: int, substeps: Sequence[SubStep]
) -> _Candidate:
    """`substeps[index]`에서 진입해 상위TF 봉 `bars`개를 채우고 나간다(손절 우선).

    진입 봉이 첫 봉이다 — 새 상위TF 봉이 시작되는 순간 이미 `bars`개를 봤으면 멈추고, 그 직전
    1분봉 종가가 청산가다. 그 전에 손절선에 닿으면(진입 스텝 포함) 손절가에 나간다. 스크래치
    `diag_patch._time_exit`(손절 있는 `ts` 팔)와 같은 식이다.
    """
    stop_price = float(cand.stop_price)
    is_long = cand.side.sign > 0
    seen = {substeps[index].htf_bar_time}
    last = substeps[index]
    hit_stop = False
    for step in substeps[index:]:
        if step.htf_bar_time not in seen:
            if len(seen) >= bars:
                break
            seen.add(step.htf_bar_time)
        last = step
        if (step.low <= stop_price) if is_long else (step.high >= stop_price):
            hit_stop = True
            break
    return dataclasses.replace(
        cand,
        entry_time=substeps[index].time,
        exit_time=last.time,
        exit_price=stop_price if hit_stop else last.close,
        reason=ExitReason.STOP_LOSS if hit_stop else ExitReason.END_OF_DATA,
        take_profit_price=None,
        entry_liquidity=Liquidity.MAKER,
        mfe_r=None,
        mae_r=None,
    )


def arm_pool(candidates: Iterable[_Candidate], *, min_width: float) -> list[_Candidate]:
    """팔이 쓰는 base 후보 — 롱 · 첫 탭 · 재진입 아님 · 손절폭 ≥ `min_width`."""
    return [
        c
        for c in candidates
        if c.side.sign > 0
        and not c.is_reentry
        and c.tap_index == 0
        and c.entry_price > 0
        and stop_width(c) >= min_width
    ]


def derive_hold_arms(
    candidates: Sequence[_Candidate],
    *,
    substeps: Sequence[SubStep],
    holds: Sequence[int] = HOLD_BARS,
) -> dict[int, list[_Candidate]]:
    """보유 봉 수마다 시간 청산 후보. 진입 스텝이 서브스텝에 없으면 뺀다(스크래치 규약)."""
    times = [s.time for s in substeps]
    out: dict[int, list[_Candidate]] = {h: [] for h in holds}
    for cand in candidates:
        index = bisect.bisect_left(times, cand.entry_time)
        if index >= len(times) or times[index] != cand.entry_time:
            continue
        for h in holds:
            out[h].append(time_exit(cand, index=index, bars=h, substeps=substeps))
    return out


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmCell:
    """한 칸의 팔 후보 — 보유 봉 → 구간(`full`/`is`/`oos`) → 시간 청산 후보."""

    symbol: str
    timeframe: str
    arms: dict[int, dict[str, tuple[_Candidate, ...]]]
    k_times: tuple[int, ...]
    k_values: tuple[float | None, ...]


def _cell_arms(payload: CellPayload) -> ArmCell:
    """워커: 한 칸의 창을 다시 읽어 구간별 서브스텝으로 시간 청산을 푼다."""
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
        pool = arm_pool(payload.candidates.get(segment, ()), min_width=STOP_WIDTH_LOG_FLOOR)
        substeps = build_substeps(window.df_1m, htf_ms) if pool else []
        derived = derive_hold_arms(pool, substeps=substeps) if pool else {h: [] for h in HOLD_BARS}
        for h, cands in derived.items():
            arms[h][segment] = tuple(cands)
    times, k = stoch_series(OhlcvStore(harness.DB_PATH), payload.symbol, payload.timeframe)
    return ArmCell(payload.symbol, payload.timeframe, arms, tuple(times), tuple(k))


def build_base_payloads(
    *,
    jobs: int,
    payload_dir: Path,
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[str] = TIMEFRAMES,
) -> list[CellPayload]:
    """base 후보 — 롱 온리 · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 익절 금지 · 익절 메이커."""
    payloads, _donor = apply_funding_proxy(
        run_cells(
            symbols,
            timeframes,
            start=harness.DEFAULT_START,
            end=harness.DEFAULT_END,
            jobs=jobs,
            take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
            retap_mode="once",
            reentry=False,
            engine_check=False,
            fill=harness.fill_preset("pen_5bp"),
            no_same_step_tp=True,
            payload_cache=PayloadCache(payload_dir),
        )
    )
    return payloads


def build_arm_cells(payloads: Sequence[CellPayload], *, jobs: int) -> list[ArmCell]:
    if jobs <= 1:
        return [_cell_arms(p) for p in payloads]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_cell_arms, payloads))


def filtered_payloads(
    payloads: Sequence[CellPayload],
    cells: Sequence[ArmCell],
    *,
    hold: int,
    floor: float,
    threshold: float | None,
    drop_symbol: str | None = None,
) -> list[CellPayload]:
    """팔 후보를 하한·%K로 걸러 base 후보 자리에 넣은 payload(재진입 비움)."""
    by_cell = {(c.symbol, c.timeframe): c for c in cells}
    out: list[CellPayload] = []
    for p in payloads:
        if drop_symbol is not None and p.symbol == drop_symbol:
            continue
        cell = by_cell[(p.symbol, p.timeframe)]
        tf_ms = timeframe_to_ms(p.timeframe)
        kept: dict[str, tuple[_Candidate, ...]] = {}
        for segment, cands in cell.arms[hold].items():
            rows = []
            for c in cands:
                if stop_width(c) < floor:
                    continue
                if threshold is not None:
                    kv = k_before(cell.k_times, cell.k_values, int(c.entry_time), tf_ms)
                    if kv is None or kv >= threshold:
                        continue
                rows.append(c)
            kept[segment] = tuple(rows)
        out.append(
            dataclasses.replace(p, candidates=kept, reentry_candidates={}, arm_candidates={})
        )
    return out


@dataclass(frozen=True)
class ArmRow:
    hold: int
    floor: float
    threshold: float | None
    segment: str
    num_trades: int
    mean_net_r: float
    se_net_r: float
    fixed_return: float
    fixed_mdd: float
    compound_return: float
    compound_mdd: float
    compound_ruined: bool


def _equity_path(rs: Sequence[float], *, compound: bool) -> tuple[float, float, bool]:
    """거래당 1% 리스크로 net R을 순서대로 쌓은 (수익, MDD, 파산) — 스크래치와 같은 식."""
    eq = peak = 1.0
    mdd = 0.0
    for r in rs:
        eq += 0.01 * (eq if compound else 1.0) * r
        if eq <= 0:
            return eq - 1.0, 1.0, True
        peak = max(peak, eq)
        mdd = max(mdd, 1.0 - eq / peak)
    return eq - 1.0, mdd, False


def place(
    payloads: Sequence[CellPayload], *, hold: int, floor: float, threshold: float | None
) -> list[ArmRow]:
    """한 칸 조합을 채택 북(한 지갑 · 복리 끔 · 가드 끔)에 배치해 구간별 행을 낸다."""
    rows: list[ArmRow] = []
    for seg in iter_book_segments(
        payloads,
        book=LeverageBookParams(),
        segments=SEGMENTS,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        include_reentry=False,
        compound_sizing=False,
        min_stop_distance_fraction=0.0,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    ):
        pairs = sorted(
            ((t.exit_time, net_r(t, p)) for t, p in seg.trades_with_placements()),
            key=lambda x: x[0],
        )
        rs = [r for _, r in pairs]
        n = len(rs)
        mean = sum(rs) / n if n else math.nan
        se = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1) / n) if n > 1 else math.nan
        f_ret, f_mdd, _ = _equity_path(rs, compound=False)
        c_ret, c_mdd, ruined = _equity_path(rs, compound=True)
        rows.append(
            ArmRow(
                hold,
                floor,
                threshold,
                seg.segment,
                n,
                mean,
                se,
                f_ret,
                f_mdd,
                c_ret,
                c_mdd,
                ruined,
            )
        )
    return rows


def reference_check(rows: Sequence[ArmRow]) -> list[str]:
    """WAN-423 §7 기준값과 대조 — 거래 수는 정확히, net R은 소수 셋째 자리(로그 정밀도)로."""
    index = {(r.hold, r.floor, r.threshold, r.segment): r for r in rows}
    lines: list[str] = []
    for (hold, floor, thr), segs in WAN423_REFERENCE.items():
        for segment, (ref_r, ref_n) in segs.items():
            row = index.get((hold, floor, thr, segment))
            if row is None:
                lines.append(f"❌ ts{hold} 하한{floor:.0%} K<{thr:.0f} {segment}: 행 없음")
                continue
            ok = row.num_trades == ref_n and round(row.mean_net_r, 3) == ref_r
            mark = "✅" if ok else "❌"
            lines.append(
                f"{mark} ts{hold} 하한{floor:.0%} K<{thr:.0f} {segment}: "
                f"{row.mean_net_r:+.4f}R · {row.num_trades}거래 (기준 {ref_r:+.3f}R · {ref_n}거래)"
            )
    return lines


def rows_to_frame(rows: Sequence[ArmRow]) -> pd.DataFrame:
    return pd.DataFrame([dataclasses.asdict(r) for r in rows])


def frame_to_rows(frame: pd.DataFrame) -> list[ArmRow]:
    out = []
    for rec in frame.to_dict("records"):
        thr = rec["threshold"]
        missing = thr is None or (isinstance(thr, float) and math.isnan(thr))
        rec["threshold"] = None if missing else thr
        rec["compound_ruined"] = bool(rec["compound_ruined"])
        out.append(ArmRow(**rec))
    return out


def render_summary(rows: Sequence[ArmRow], *, elapsed: float | None = None) -> str:
    lines = [
        "# WAN-424 §1 — 스토캐스틱 × 오더블록 팔 복원 · WAN-423 재현 검산",
        "",
        "롱 · 31종목 × 9TF · 첫 탭 · 재진입 없음 · `pen_5bp` × 같은 분 익절 금지 · 채택 북 · "
        "복리 끔 · 가드 끔(손절폭 하한이 대신) · 판정 자 = 거래당 net R.",
        "",
        "## 재현 검산 (WAN-423 §7 `hold_ls.log` 기준값)",
        "",
        *[f"- {line}" for line in reference_check(rows)],
        "",
        "## 롱 표 (보유 × 하한 × %K 문턱)",
        "",
        "| 팔 | full | is | oos_warm | full 복리 수익/MDD | oos 복리 수익/MDD |",
        "| -- | --: | --: | --: | --: | --: |",
    ]
    index = {(r.hold, r.floor, r.threshold, r.segment): r for r in rows}
    combos = sorted(
        {(r.floor, r.threshold, r.hold) for r in rows}, key=lambda x: (x[0], x[1] or 0, x[2])
    )
    for floor, thr, hold in combos:
        cells = []
        for s in SEGMENTS:
            r = index.get((hold, floor, thr, s))
            cells.append("—" if r is None else f"{r.mean_net_r:+.3f} ({r.num_trades})")
        full = index.get((hold, floor, thr, "full"))
        oos = index.get((hold, floor, thr, "oos_warm"))

        def comp(r: ArmRow | None) -> str:
            if r is None:
                return "—"
            ret = "파산" if r.compound_ruined else f"{r.compound_return * 100:+.0f}%"
            return f"{ret} / {r.compound_mdd * 100:.0f}%"

        k = "끔" if thr is None else f"<{thr:.0f}"
        lines.append(
            f"| ts{hold} · 하한{floor:.0%} · %K{k} | "
            + " | ".join(cells)
            + f" | {comp(full)} | {comp(oos)} |"
        )
    lines += [
        "",
        "⚠️ 표에서 최선 칸을 고르지 말 것(WAN-161) — 읽을 것은 모양이다. "
        "매칭 대조군은 §2(사용자 결정 뒤).",
    ]
    if elapsed is not None:
        lines.append(f"\n실측 {elapsed:.0f}초.")
    return "\n".join(lines) + "\n"


def run_grid(payloads: Sequence[CellPayload], cells: Sequence[ArmCell]) -> list[ArmRow]:
    rows: list[ArmRow] = []
    for floor in FLOORS:
        for thr in THRESHOLDS:
            for hold in HOLD_BARS:
                rows.extend(
                    place(
                        filtered_payloads(payloads, cells, hold=hold, floor=floor, threshold=thr),
                        hold=hold,
                        floor=floor,
                        threshold=thr,
                    )
                )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--payload-dir", type=Path, default=DEFAULT_PAYLOAD_DIR)
    parser.add_argument("--from-csv", action="store_true", help="CSV에서 요약만 다시 만든다.")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument(
        "--symbols", default=None, help="파일럿용 좁히기(콤마). 기준값 대조는 전 좌표에서만."
    )
    parser.add_argument("--timeframes", default=None, help="파일럿용 좁히기(콤마).")
    args = parser.parse_args(argv)

    if args.from_csv:
        rows = frame_to_rows(pd.read_csv(args.csv))
        args.summary.write_text(render_summary(rows), encoding="utf-8")
        print(render_summary(rows))
        return 0

    started = time.monotonic()
    symbols = tuple(args.symbols.split(",")) if args.symbols else SYMBOLS
    timeframes = tuple(args.timeframes.split(",")) if args.timeframes else TIMEFRAMES
    payloads = build_base_payloads(
        jobs=args.jobs, payload_dir=args.payload_dir, symbols=symbols, timeframes=timeframes
    )
    print(f"base 후보 {time.monotonic() - started:.0f}s · 칸 {len(payloads)}", flush=True)
    cells = build_arm_cells(payloads, jobs=args.jobs)
    print(f"팔 후보 {time.monotonic() - started:.0f}s", flush=True)
    rows = run_grid(payloads, cells)
    elapsed = time.monotonic() - started
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    rows_to_frame(rows).to_csv(args.csv, index=False)
    summary = render_summary(rows, elapsed=elapsed)
    args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    return 0 if all(line.startswith("✅") for line in reference_check(rows)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
