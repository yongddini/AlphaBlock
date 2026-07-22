"""WAN-137 Phase 2: 저항 오더블록 익절 **손익 격자** — 도달률이 아니라 손익으로 판정한다.

Phase 1(`wan137_resistance_distance`)은 "가장 가까운 저항이 가격의 도달 범위(MFE) 너머다"
까지만 봤다(게이트 (b)). PM 변경요청(2026-07-19): **도달률은 손익 판정을 대신하지 못한다** —
자기-TF 저항 중앙거리가 3.49R(15m)인데 현행 익절은 1.5R이라, 저항-OB 팔은 **도달률이 낮은
대신 닿았을 때 두 배 이상을 챙긴다**. 승률↓ · 크기↑ 교환에서 어느 쪽이 이기는지는 **재봐야
알 수 있고 Phase 1은 재지 않았다**. 이 모듈이 그 손익을 잰다.

## 팔 (arm)

* **`fixed_1.5r`** (대조) — 현행 채택 기본값(고정 1.5R 익절). `ConfluenceParams()` 그대로.
* **`resistance_self`** (정본) — 익절 목표 = **자기 진입 TF**의 가장 가까운 상방 저항 OB
  근단(WAN-137 무게 규칙 **B**, 파라미터 0). Phase 1이 분포에서 도출한 정본 팔이다.
* **`resistance_combined`** (참고) — 익절 목표 = **사다리 전 TF**(15m→5m→1m 등)의 최근접
  저항. ⚠️ **참고 팔이다** — Phase 1에서 최근접의 67%가 1m에서 나와(퇴화) "얼마나 낮은
  데까지 볼 것인가"라는 자유 파라미터를 되살린다. 손익이 좋아 보여도 채택 근거로 약하다.

## 익절만 바꾼다 — 진입 집합은 세 팔이 같다 (거래 수 검산의 근거)

익절은 **청산만** 바꾸고 진입 결정·체결 판정에는 전혀 안 쓰인다. 그래서 세 팔의 **후보(체결
셋업) 집합은 비트 단위로 같다** — `build_zone_limit_candidates(take_profit_override=...)`가
같은 셋업 루프를 돌며 목표만 갈아끼운다(WAN-137 엔진 훅). 이 모듈은 그 불변을 **동작으로
검산**한다(팔별 후보 수·진입시각이 어긋나면 배선 버그). ⚠️ 단 **시퀀싱된 거래 수는 다를 수
있다** — 저항 팔은 목표가 멀어(3.5R) 포지션을 더 오래 들고 있으므로 동시 1포지션 슬롯이
더 오래 잠겨, 뒤따르는 셋업이 더 많이 스킵된다. 즉 `filled`(후보)는 같고 `num_trades`
(시퀀싱 후)는 갈린다 — 그 갈림 자체가 이 이슈의 관찰이다.

## 폴백 (저항 없음)

위쪽에 유효 저항이 없으면(Phase 1: ~1%) **고정 1.5R로 폴백**한다 — 현행 익절을 그대로
쓰는 것이라 그 셋업만은 대조 팔과 같은 목표를 갖는다. 저항 없음이 드물어(과반 아님) 폴백
규칙이 결과를 좌우하지 않는다(게이트 (c) 불성립, Phase 1). `frac_no_resistance`로 병기한다.

## 룩어헤드 가드 (① 뚫린 저항 · ② 확정 시점)

저항 질의는 **WAN-126 클리핑 provider**(`indexed_zone_provider`)로만 한다 — 진입 시각까지
확정된 존만 보이고(②), `nearest_resistance`가 클리핑된 `breaker`를 뺀다(①). Phase 1 회귀
테스트가 순수 함수 수준에서 고정하고, 이 모듈 테스트가 **손익 팔에서도** ①②가 동작으로
지켜지는지 고정한다.

## 오프셋 · 병합

* **익절 오프셋**: 저항 목표에 2bp를 **얹지 않는다**(정본). 진입과 대칭으로 얹으면 목표가
  2bp 가까워지지만, 자기-TF 3.5R 간극 앞에서 2bp는 무의미하다(§③ 결정 기록).
* **병합**: `combine=False`(정본). Phase 1과 동일 — 1분봉 병합은 O(활성²)라 격자에서 뺐고,
  병합이 목표를 당기는 방향은 WAN-134가 이미 쟀다(폭 ~1.8배).

## 구간 (IS/OOS)

WAN-99/harness 규칙대로 **구간마다 독립 백테스트**(초기자본에서 새로 시작)로 낸다 —
거래를 사후 분할하면 OOS 사이징 자본이 IS에서 굴러온 상태라 오염된다. 하위TF 아카이브는
못 박은 **전체 창**에서 한 번 만들어(WAN-126 디스크 캐시 재사용) 구간마다 트리거 시각으로
클리핑해 재사용한다 — 구간마다 1분봉을 재탐지(심볼당 8분+)하지 않기 위해서다.

재현:

```
uv run python -m backtest.wan137_phase2_pnl --tf 1h            # 1h 먼저(가벼움)
uv run python -m backtest.wan137_phase2_pnl --tf 15m --append  # 15m 뒤에 붙임
uv run python -m backtest.wan137_phase2_pnl --from-csv         # 요약만 재생성
uv run python -m backtest.wan137_phase2_pnl --tf 1h --no-combined  # 참고 팔 생략(빠름)
```
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_IS, SEGMENT_OOS, MarketData, Segment, segments_for
from backtest.models import BacktestConfig, BacktestResult, ExitReason, PositionSide, Trade
from backtest.multi_tf_overlap import ZoneProvider, indexed_zone_provider
from backtest.run import parse_date_ms
from backtest.wan126_multi_tf_overlap import DEFAULT_CACHE_DIR, detect_ltf_archives
from backtest.wan137_resistance_distance import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    RESISTANCE_TFS,
    nearest_resistance,
)
from backtest.zone_limit_backtest import (
    TakeProfitContext,
    TakeProfitOverride,
    _Candidate,
    _resolve_take_profit,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams, OrderBlockDirection, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

# --------------------------------------------------------------------------- #
# 상수 — Phase 1과 같은 못 박은 창·유니버스
# --------------------------------------------------------------------------- #

DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")
REPORTS_DIR = Path("backtest/reports")

ARM_FIXED = "fixed_1.5r"
ARM_SELF = "resistance_self"
ARM_COMBINED = "resistance_combined"
ARMS: tuple[str, ...] = (ARM_FIXED, ARM_SELF, ARM_COMBINED)

#: 하위TF 사다리(저항 질의용). 진입 TF 자신은 후보 생성에 쓴 결과를 재사용하므로 여기선 뺀다.
_LOWER_TFS: dict[str, tuple[str, ...]] = {
    tf: tuple(t for t in ladder if t != tf) for tf, ladder in RESISTANCE_TFS.items()
}

SEGMENT_ORDER: tuple[str, ...] = ("full", SEGMENT_IS, SEGMENT_OOS)


# --------------------------------------------------------------------------- #
# 저항-OB 익절 오버라이드 (정적 지정가 경로 훅)
# --------------------------------------------------------------------------- #


@dataclass
class _OverrideStats:
    """오버라이드가 셋업마다 남긴 진단 — 저항 발견/폴백 수와 거리 분포."""

    resistance: int = 0
    fallback: int = 0
    distances: list[float] = field(default_factory=list)

    @property
    def frac_no_resistance(self) -> float | None:
        total = self.resistance + self.fallback
        return self.fallback / total if total else None

    @property
    def dist_median(self) -> float | None:
        return statistics.median(self.distances) if self.distances else None


def make_resistance_override(
    provider: ZoneProvider,
    ladder: Sequence[str],
    params: ConfluenceParams,
    stats: _OverrideStats,
) -> TakeProfitOverride:
    """저항-OB 익절 목표를 내는 오버라이드를 만든다(폴백 = 고정 1.5R).

    `ladder`가 진입 TF 하나면 무게 규칙 **B**(자기-TF), 사다리 전체면 참고 팔(전TF 최근접).
    저항이 없으면(폴백) 현행 `_resolve_take_profit`(고정 1.5R)을 그대로 쓴다 — 그 셋업만
    대조 팔과 같은 목표라 폴백이 결과를 왜곡하지 않는다. `stats`에 진단을 남긴다.
    """

    def resolve(ctx: TakeProfitContext) -> float | None:
        risk = ctx.entry_price - ctx.stop_price if ctx.is_long else ctx.stop_price - ctx.entry_price
        best_dist: float | None = None
        best_target: float | None = None
        for tf in ladder:
            zones = provider(tf, ctx.trigger_time, OrderBlockDirection.BEARISH)
            hit = nearest_resistance(zones, entry_price=ctx.entry_price, risk=risk, timeframe=tf)
            if hit is None:
                continue
            if best_dist is None or hit.distance_r < best_dist:
                best_dist, best_target = hit.distance_r, hit.target
        if best_target is None:
            stats.fallback += 1
            # 폴백: 저항 없음 → 현행 고정 1.5R 익절.
            return _resolve_take_profit(params, ctx.is_long, ctx.entry_price, ctx.stop_price, [])
        stats.resistance += 1
        assert best_dist is not None
        stats.distances.append(best_dist)
        return best_target

    return resolve


# --------------------------------------------------------------------------- #
# 한 팔 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArmOutcome:
    result: BacktestResult
    candidates: list[_Candidate]
    paired: list[tuple[_Candidate, Trade]]
    eligible: int
    filled: int
    fill_rate: float | None
    override_stats: _OverrideStats | None


def _gross_r(cand: _Candidate) -> float | None:
    """비용 반영 전 실현 R = 부호(청산가 − 진입가) / 1R. 1R을 못 재면 None."""
    risk = (
        cand.entry_price - cand.stop_price
        if cand.side is PositionSide.LONG
        else cand.stop_price - cand.entry_price
    )
    if risk <= 0:
        return None
    return cand.side.sign * (cand.exit_price - cand.entry_price) / risk


def run_arm(
    market: MarketData,
    obr: OrderBlockResult,
    cfg: BacktestConfig,
    params: ConfluenceParams,
    *,
    arm: str,
    self_provider: ZoneProvider,
    combined_provider: ZoneProvider | None,
) -> ArmOutcome:
    """한 (구간, 팔)의 후보 생성 → 동시 1포지션 시퀀싱 → 결과."""
    override = None
    ostats: _OverrideStats | None = None
    if arm != ARM_FIXED:
        provider: ZoneProvider
        ladder: tuple[str, ...]
        if arm == ARM_SELF:
            provider, ladder = self_provider, (market.timeframe,)
        else:
            assert combined_provider is not None
            provider, ladder = combined_provider, RESISTANCE_TFS[market.timeframe]
        ostats = _OverrideStats()
        override = make_resistance_override(provider, ladder, params, ostats)

    candidates, stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=obr,
        take_profit_override=override,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)
    trades = [t for _, t in paired]
    result = build_result_from_trades(trades, cfg, market.timeframe)
    return ArmOutcome(
        result=result,
        candidates=candidates,
        paired=paired,
        eligible=stats.eligible,
        filled=stats.filled,
        fill_rate=stats.fill_rate,
        override_stats=ostats,
    )


# --------------------------------------------------------------------------- #
# 결과 행
# --------------------------------------------------------------------------- #


class PnlRow(BaseModel):
    """한 (심볼, 진입TF, 구간, 팔) 셀의 손익 + 청산 사유 분포."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    entry_tf: str
    segment: str
    arm: str
    eligible: int
    filled: int
    """체결 셋업 수(시퀀싱 이전). 세 팔이 같아야 한다(익절이 진입을 안 바꾸므로)."""
    num_trades: int
    """동시 1포지션 시퀀싱 후 거래 수. 팔마다 다를 수 있다(청산 시각이 슬롯 점유를 바꾼다)."""
    fill_rate: float | None
    total_return: float
    max_drawdown: float
    win_rate: float
    sharpe: float | None
    mean_gross_r: float | None
    n_take_profit: int
    n_stop_loss: int
    n_end_of_data: int
    frac_no_resistance: float | None
    dist_median_r: float | None


def _reason_counts(paired: list[tuple[_Candidate, Trade]]) -> Counter[ExitReason]:
    return Counter(cand.reason for cand, _ in paired)


def build_row(symbol: str, entry_tf: str, segment: str, arm: str, outcome: ArmOutcome) -> PnlRow:
    m = outcome.result.metrics
    reasons = _reason_counts(outcome.paired)
    grs = [g for g in (_gross_r(cand) for cand, _ in outcome.paired) if g is not None]
    ostats = outcome.override_stats
    return PnlRow(
        symbol=symbol,
        entry_tf=entry_tf,
        segment=segment,
        arm=arm,
        eligible=outcome.eligible,
        filled=outcome.filled,
        num_trades=m.num_trades,
        fill_rate=outcome.fill_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        win_rate=m.win_rate,
        sharpe=m.sharpe,
        mean_gross_r=statistics.fmean(grs) if grs else None,
        n_take_profit=reasons.get(ExitReason.TAKE_PROFIT, 0),
        n_stop_loss=reasons.get(ExitReason.STOP_LOSS, 0),
        n_end_of_data=reasons.get(ExitReason.END_OF_DATA, 0),
        frac_no_resistance=ostats.frac_no_resistance if ostats else None,
        dist_median_r=ostats.dist_median if ostats else None,
    )


# --------------------------------------------------------------------------- #
# 격자
# --------------------------------------------------------------------------- #


def _detect(market: MarketData) -> OrderBlockResult:
    return OrderBlockDetector(harness.LEGACY_OB_PARAMS).run(market.htf_df)


def run_cell(
    market: MarketData,
    segment: Segment,
    *,
    ltf_archives: dict[str, OrderBlockResult] | None,
    with_combined: bool,
) -> list[PnlRow]:
    """한 (심볼, 진입TF, 구간)의 세 팔을 돌려 행을 낸다 + 후보 수 일치 검산.

    저항 provider는 구간에서 탐지한 진입 TF 존(`obr`)에 전체-창 하위TF 아카이브를 더해
    만든다 — 클리핑이 트리거 시각으로 자르므로 전체-창 아카이브를 구간마다 재사용해도
    룩어헤드가 새지 않는다(질의 시각이 구간 안이다).
    """
    seg_market = harness.slice_market(market, segment)
    if seg_market.empty or seg_market.df_1m.empty:
        return []
    obr = _detect(seg_market)
    # ⚠️ 밴드는 WAN-132 이전 값(`tap`)으로 고정한다 — 숫자 보존이자 실행 가능성 문제다:
    # 봉내 라이브 밴드는 `take_profit_override`(이 리포트의 저항 팔)를 지원하지 않는다.
    params = harness.pin_band_bar(
        harness.build_params(entry_mode="zone_limit", max_zone_width_atr=None)
    )
    cfg = harness.build_config(seg_market.timeframe)

    self_provider = indexed_zone_provider({seg_market.timeframe: obr}, combine=False)
    combined_provider: ZoneProvider | None = None
    if with_combined and ltf_archives is not None:
        archives = {seg_market.timeframe: obr, **ltf_archives}
        combined_provider = indexed_zone_provider(archives, combine=False)

    arms = (ARM_FIXED, ARM_SELF) + ((ARM_COMBINED,) if combined_provider is not None else ())
    rows: list[PnlRow] = []
    entry_sets: dict[str, list[int]] = {}
    for arm in arms:
        outcome = run_arm(
            seg_market,
            obr,
            cfg,
            params,
            arm=arm,
            self_provider=self_provider,
            combined_provider=combined_provider,
        )
        entry_sets[arm] = sorted(c.entry_time for c in outcome.candidates)
        rows.append(build_row(seg_market.symbol, seg_market.timeframe, segment.name, arm, outcome))

    # 거래 수 검산: 익절만 바꿨으므로 후보(체결 셋업) 집합은 팔마다 **완전히 같아야** 한다.
    baseline = entry_sets[ARM_FIXED]
    for arm, entries in entry_sets.items():
        if entries != baseline:
            raise AssertionError(
                f"후보 집합 불일치 — {seg_market.symbol} {seg_market.timeframe} "
                f"{segment.name} {arm}: {len(entries)} vs fixed {len(baseline)}. "
                "익절이 진입을 바꾸는 배선 버그다(WAN-137 Phase 2 §거래 수 검산)."
            )
    return rows


def run_report(
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = ("1h",),
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    cache_dir: str | None = DEFAULT_CACHE_DIR,
    with_combined: bool = True,
    log: bool = True,
) -> list[PnlRow]:
    """격자: 심볼 × 진입TF × 구간 × 팔."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    segments = segments_for(oos=True)
    rows: list[PnlRow] = []
    for timeframe in timeframes:
        for symbol in symbols:
            sym = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                sym, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, funding=True
            )
            if market.empty or market.df_1m.empty:
                if log:
                    print(f"[wan137p2] skip {sym} {timeframe}: 데이터 없음", flush=True)
                continue
            ltf_archives: dict[str, OrderBlockResult] | None = None
            if with_combined:
                ltf_archives = detect_ltf_archives(
                    sym,
                    _LOWER_TFS[timeframe],
                    start_ms=start_ms,
                    end_ms=end_ms,
                    cache_dir=cache_dir,
                    log=log,
                )
            for segment in segments:
                t0 = time.time()
                cell = run_cell(
                    market, segment, ltf_archives=ltf_archives, with_combined=with_combined
                )
                rows.extend(cell)
                if log:
                    print(
                        f"[wan137p2] {sym} {timeframe} {segment.name}: "
                        f"{len(cell)}행 ({time.time() - t0:.0f}s)",
                        flush=True,
                    )
    return rows


# --------------------------------------------------------------------------- #
# 집계 · 판정
# --------------------------------------------------------------------------- #


def _bare(symbol: str) -> str:
    return symbol.split("/")[0]


def pooled(frame: pd.DataFrame, entry_tf: str, segment: str, arm: str) -> dict[str, float | None]:
    """6심볼 심볼평균 풀 — total_return 등은 단순평균, 청산 사유는 합."""
    sub = frame[
        (frame["entry_tf"] == entry_tf) & (frame["segment"] == segment) & (frame["arm"] == arm)
    ]
    if sub.empty:
        return {}

    def avg(col: str) -> float | None:
        vals = sub[col].astype(float).dropna()
        return float(vals.mean()) if len(vals) else None

    tp = int(sub["n_take_profit"].sum())
    sl = int(sub["n_stop_loss"].sum())
    eod = int(sub["n_end_of_data"].sum())
    closed = tp + sl
    return {
        "n_symbols": float(len(sub)),
        "total_return": avg("total_return"),
        "max_drawdown": avg("max_drawdown"),
        "win_rate": avg("win_rate"),
        "mean_gross_r": avg("mean_gross_r"),
        "num_trades": float(sub["num_trades"].sum()),
        "filled": float(sub["filled"].sum()),
        "n_take_profit": float(tp),
        "n_stop_loss": float(sl),
        "n_end_of_data": float(eod),
        "tp_share": tp / (tp + sl + eod) if (tp + sl + eod) else None,
        "eod_share": eod / (tp + sl + eod) if (tp + sl + eod) else None,
        "win_of_closed": tp / closed if closed else None,
        "frac_no_resistance": avg("frac_no_resistance"),
        "dist_median_r": avg("dist_median_r"),
    }


def leave_one_out(frame: pd.DataFrame, entry_tf: str, arm: str, segment: str = SEGMENT_OOS) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인."""
    sub = frame[
        (frame["entry_tf"] == entry_tf) & (frame["segment"] == segment) & (frame["arm"] == arm)
    ]
    if sub.empty:
        return "—"
    parts: list[str] = []
    for _, drop in sub.iterrows():
        rest = sub[sub["symbol"] != drop["symbol"]]["total_return"].astype(float)
        if len(rest):
            parts.append(f"−{_bare(str(drop['symbol']))} {rest.mean() * 100:+.2f}%")
    return " · ".join(parts)


def verdict(frame: pd.DataFrame, entry_tf: str) -> str:
    """공식 OOS 심볼평균으로 fixed vs resistance_self를 가른다(정본 팔)."""
    fx = pooled(frame, entry_tf, SEGMENT_OOS, ARM_FIXED)
    rs = pooled(frame, entry_tf, SEGMENT_OOS, ARM_SELF)
    if not fx or not rs:
        return "판정 불가(OOS 데이터 없음)."
    f_ret = fx.get("total_return")
    r_ret = rs.get("total_return")
    if f_ret is None or r_ret is None:
        return "판정 불가(수익 없음)."
    delta = r_ret - f_ret
    tag = "(a) 저항-OB가 이긴다" if delta > 0 else "(b) 저항-OB가 못 이긴다 → 1.5R 재확인"
    return (
        f"{tag} — OOS 심볼평균 total_return: fixed {f_ret * 100:+.2f}% vs "
        f"resistance_self {r_ret * 100:+.2f}% (Δ{delta * 100:+.2f}%p)."
    )


def tradeoff_sentence(frame: pd.DataFrame, entry_tf: str) -> str:
    """승률↓·평균R↑ 교환비를 명시적 문장으로(이슈 완료 기준)."""
    fx = pooled(frame, entry_tf, SEGMENT_OOS, ARM_FIXED)
    rs = pooled(frame, entry_tf, SEGMENT_OOS, ARM_SELF)
    if not fx or not rs:
        return "—"

    def d(col: str, scale: float = 1.0) -> str:
        a, b = fx.get(col), rs.get(col)
        if a is None or b is None:
            return "—"
        return f"{(b - a) * scale:+.2f}"

    wr_f, wr_r = fx.get("win_rate"), rs.get("win_rate")
    r_f, r_r = fx.get("mean_gross_r"), rs.get("mean_gross_r")
    wr = (
        f"승률 {wr_f * 100:.1f}%→{wr_r * 100:.1f}% ({d('win_rate', 100)}%p)"
        if wr_f is not None and wr_r is not None
        else "승률 —"
    )
    rr = (
        f"평균R {r_f:+.3f}→{r_r:+.3f} ({d('mean_gross_r')})"
        if r_f is not None and r_r is not None
        else "평균R —"
    )
    return f"{wr} · {rr} → 곱이 total_return을 어느 쪽으로 기울이는지는 위 판정이 답한다."


# --------------------------------------------------------------------------- #
# 렌더
# --------------------------------------------------------------------------- #


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}"


def _num(v: float | None, fmt: str = ".2f") -> str:
    return "—" if v is None else format(v, fmt)


def _pooled_table(frame: pd.DataFrame, entry_tf: str) -> str:
    headers = [
        "segment",
        "arm",
        "return%",
        "mdd%",
        "win%",
        "meanR",
        "trades",
        "TP",
        "SL",
        "EOD",
        "TP%",
        "distR",
    ]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for segment in SEGMENT_ORDER:
        for arm in ARMS:
            c = pooled(frame, entry_tf, segment, arm)
            if not c:
                continue
            tp_share = c.get("tp_share")
            lines.append(
                "| "
                + " | ".join(
                    [
                        segment,
                        arm,
                        _pct(c.get("total_return")),
                        _pct(c.get("max_drawdown")),
                        _pct(c.get("win_rate")),
                        _num(c.get("mean_gross_r"), ".3f"),
                        _num(c.get("num_trades"), ".0f"),
                        _num(c.get("n_take_profit"), ".0f"),
                        _num(c.get("n_stop_loss"), ".0f"),
                        _num(c.get("n_end_of_data"), ".0f"),
                        _num(None if tp_share is None else tp_share * 100, ".0f"),
                        _num(c.get("dist_median_r"), ".2f"),
                    ]
                )
                + " |"
            )
    return "\n".join(lines)


def _symbol_table(frame: pd.DataFrame, entry_tf: str, segment: str) -> str:
    sub = frame[(frame["entry_tf"] == entry_tf) & (frame["segment"] == segment)].copy()
    if sub.empty:
        return "(없음)"
    headers = ["symbol", "arm", "return%", "mdd%", "win%", "meanR", "trades", "TP", "SL", "EOD"]
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("--" for _ in headers) + " |"]
    for _, r in sub.sort_values(["symbol", "arm"]).iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    _bare(str(r["symbol"])),
                    str(r["arm"]),
                    _pct(float(r["total_return"])),
                    _pct(float(r["max_drawdown"])),
                    _pct(float(r["win_rate"])),
                    _num(float(r["mean_gross_r"]) if pd.notna(r["mean_gross_r"]) else None, ".3f"),
                    str(int(r["num_trades"])),
                    str(int(r["n_take_profit"])),
                    str(int(r["n_stop_loss"])),
                    str(int(r["n_end_of_data"])),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_summary(frame: pd.DataFrame, path: Path) -> None:
    entry_tfs = sorted(set(frame["entry_tf"]))
    lines = [
        "# WAN-137 Phase 2: 저항 오더블록 익절 손익 격자",
        "",
        "재현: `uv run python -m backtest.wan137_phase2_pnl`",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · 6심볼 · 롱 온리 · 공식 렌즈 `baseline`"
        "(WAN-128) · 채택 기본값(존 지정가 + 오프셋 2bp) 고정. 구간마다 독립 백테스트"
        "(초기자본 재시작, WAN-99). 팔: `fixed_1.5r`(대조) · `resistance_self`(정본, 무게 "
        "규칙 B) · `resistance_combined`(참고, 전TF 최근접 — 퇴화 경고).",
        "",
        "> ⚠️ `baseline`은 낙관 렌즈(닿으면 체결) — 손익·체결률은 **상한**이다.",
        "> ⚠️ **참고 팔(`resistance_combined`)은 채택 근거로 약하다** — Phase 1: 최근접의 "
        "67%가 1m에서 나와 「얼마나 낮은 데까지」라는 자유 파라미터를 되살린다.",
        "> ⚠️ 「엣지 없음」(WAN-84/88/111/114/124)은 불변 — 익절 구조는 위험의 모양만 바꾼다"
        "(WAN-90).",
        "> ⚠️ 익절 목표에 2bp 오프셋을 얹지 않았다(정본, §③). 병합 `combine=False`(정본).",
        "",
    ]
    for entry_tf in entry_tfs:
        lines += [
            f"## 진입 {entry_tf}",
            "",
            f"**판정**: {verdict(frame, entry_tf)}",
            "",
            f"**교환비(OOS)**: {tradeoff_sentence(frame, entry_tf)}",
            "",
            f"**Leave-one-out(OOS `resistance_self`)**: {leave_one_out(frame, entry_tf, ARM_SELF)}",
            "",
            f"**Leave-one-out(OOS `fixed_1.5r`)**: {leave_one_out(frame, entry_tf, ARM_FIXED)}",
            "",
            "### 6심볼 풀 (심볼평균; TP/SL/EOD·distR은 청산 사유 합·거리 중앙값)",
            "",
            _pooled_table(frame, entry_tf),
            "",
            "### 심볼별 (OOS)",
            "",
            _symbol_table(frame, entry_tf, SEGMENT_OOS),
            "",
        ]
    # 거래 수 검산은 격자에서 raise로 강제되므로 여기서는 한 줄로만 남긴다.
    lines += [
        "## 거래 수 검산",
        "",
        "익절만 바꾸므로 **체결 셋업(`filled`) 집합은 세 팔이 비트 단위로 같다** — 격자가 팔별 "
        "진입시각 집합을 대조해 어긋나면 `AssertionError`로 멈춘다(§거래 수 검산). 위 표의 "
        "`trades`(시퀀싱 후)가 팔마다 다른 것은 **버그가 아니라 관찰**이다: 저항 팔은 목표가 "
        "멀어 포지션을 더 오래 들고 있어 동시 1포지션 슬롯이 더 오래 잠기고, 뒤따르는 셋업이 "
        "더 많이 스킵된다.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[PnlRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def rows_from_csv(path: Path) -> list[PnlRow]:
    frame = pd.read_csv(path)
    return [PnlRow.model_validate(rec) for rec in frame.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tf", type=str, default="1h", help="콤마로 여러 개(예: 1h,15m)")
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--out-csv", type=str, default=str(REPORTS_DIR / "wan137_phase2_pnl.csv"))
    parser.add_argument(
        "--out-md", type=str, default=str(REPORTS_DIR / "wan137_phase2_pnl_summary.md")
    )
    parser.add_argument("--append", action="store_true", help="기존 CSV에 새 TF 행을 덧붙인다")
    parser.add_argument("--from-csv", action="store_true", help="격자 재실행 없이 요약만 재생성")
    parser.add_argument("--cache-dir", type=str, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--no-cache", action="store_true", help="LTF 아카이브 캐시를 쓰지 않는다")
    parser.add_argument("--no-combined", action="store_true", help="참고 팔(전TF 최근접) 생략")
    args = parser.parse_args()

    out_csv = Path(args.out_csv)
    if args.from_csv:
        rows = rows_from_csv(out_csv)
        print(f"[wan137p2] {out_csv}에서 {len(rows)}행 로드 — 격자 재실행 없음")
    else:
        symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
        timeframes = tuple(t.strip() for t in args.tf.split(",") if t.strip())
        cache_dir = None if args.no_cache else args.cache_dir
        rows = run_report(
            symbols,
            timeframes,
            start=args.start,
            end=args.end,
            cache_dir=cache_dir,
            with_combined=not args.no_combined,
        )
        if args.append and out_csv.exists():
            existing = rows_from_csv(out_csv)
            keep = [r for r in existing if r.entry_tf not in set(timeframes)]
            rows = keep + list(rows)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(out_csv, index=False)
    write_summary(rows_to_frame(rows), Path(args.out_md))
    print(f"[wan137p2] 저장: {out_csv}, {args.out_md}")


if __name__ == "__main__":
    main()
