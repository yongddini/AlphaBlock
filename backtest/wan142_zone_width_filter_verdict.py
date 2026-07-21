"""존폭 필터 채택 판정 — 거래 수를 맞춘 손익 재검 + 체결 보수화 재검
(WAN-142, WAN-133 §후속 1·2 스핀아웃).

## 배경 — WAN-133은 (a) 선별을 냈지만 손익 표는 스스로 만든 게이트에 걸렸다

WAN-133(`wan133_geometry_vs_selection`)이 `zone_width_atr`↔뚫림 연관을 관문 2개(장벽 기하
제거 · ATR 분모 통제)로 통과시켜 **(a) 선별 축 확인**을 냈다. 그런데 그 판정으로 「존폭
필터를 채택한다」까지 갈 수 없다 — 결정문서가 두 구멍을 명시한다:

1. **거래 수 오염 게이트 초과** — 필터 팔이 기본 대비 −68.9%(15m) · −56.2%(1h)다.
   WAN-126이 만든 5% 문턱을 크게 넘으므로 손익 차이를 **순수 선별 효과로 인용할 수 없다**.
   MDD 개선이 **선별** 때문인지 그냥 **거래를 1/3로 줄여서**인지 갈리지 않는다.
2. **체결 보수화 미실시** — 전부 `baseline`(닿으면 체결) 위의 값이다.

## 🚨 고정 입력이 두 번 움직였다 — 이 표는 오늘의 채택 기본값 위에서 다시 낸다

WAN-133의 CSV는 **옛 밴드(`tap`) + 병합 존** 위의 기록이고 그 상태로 못 박혀 있다
(`harness.pin_band_bar` · `harness.LEGACY_OB_PARAMS`). 그 뒤 두 축이 바뀌었다:

* **WAN-132**: 진입가 정본이 `intrabar_live`로 옮겨 갔다 → 진입가가 움직이면 1R(진입가 →
  무효화 경계)이 움직이고, 존폭을 재는 자도 같이 움직인다.
* **WAN-149**: `combine_obs` 기본값이 `False`(원본 존 단위 분리)가 됐다 → **병합이 곧
  존폭을 만드는 장치였다**(WAN-134: 「병합은 존폭의 대리변수」). 병합을 없애면 박스가
  좁아지므로 **존폭 분포가 통째로 이동한다**.

그래서 이 모듈은 **핀을 하나도 쓰지 않는다** — `ConfluenceParams()`·`OrderBlockParams()`
기본값을 그대로 받아 오늘의 채택 기본값(오프셋 2bp × `intrabar_live` × `unconditional` ×
고정 1.5R × 롱 온리 × `combine_obs=False`)에서 잰다. 창·심볼·문턱 규칙·유효 표본 기준은
WAN-133 그대로라 **구조는 재사용하고 엔진만 갈아끼운 것**이다.

## §0 — 존폭 분포부터 다시 낸다

옛 문턱을 그대로 쓰면 전혀 다른 비율을 자르게 되므로, 분리 엔진의 존폭이 병합 시절과
얼마나 다른지를 **존 단위**로 먼저 보인다(같은 탐지기를 `combine_obs` 두 값으로 돌려
`zone_key`별 첫 탭 기준 폭/ATR을 비교). 문턱 자체는 §1이 매 셀 IS에서 다시 잡는다.

## §1 — 거래 수를 맞춘 팔 (이 이슈의 핵심)

세 팔을 같은 후보 풀 위에서 돌린다:

* `default` — 채택 기본값(전 후보).
* `filter_bot3` — `zone_width_atr` 하위 1/3만 진입(IS 문턱 → OOS 적용, 룩어헤드 없음).
* `matched_random` — **기본 팔에서 무작위로 필터와 같은 수만큼** 뽑은 매칭 대조군
  (시드 20개). 표본 크기·노출·복리가 필터 팔과 같고 **고르는 규칙만 없다**.

즉 `filter` vs `matched`가 **선별의 몫**이고, `default` vs `matched`가 **표본 축소의
몫**이다. WAN-126 오염 게이트가 막던 것이 정확히 이 분리이고, 매칭 대조군이 그 분모를
만들어 준다. 판정은 시드 분포 위의 순위 p값으로 낸다(시드 20개 → 최소 p = 1/21 ≈ 0.048).

⚠️ **매칭 단위는 「후보」다** — 필터가 자르는 대상이 후보이기 때문이다. 동시 1포지션
시퀀서가 그 뒤 한 번 더 깎으므로 최종 거래 수는 정확히 같지 않다(그 잔차도 표에 병기한다).

## §2 — 체결 보수화 재검

같은 세 팔을 `pen_5bp`(지정가 5bp 관통 요구)에서 다시 돌린다. WAN-128이 폐지한 것은
**병기 요구**이지 렌즈 자체가 아니므로 옵트인 민감도로 쓴다. 공식 수치는 `baseline`이다.

재현: `python -m backtest.wan142_zone_width_filter_verdict`.
"""

from __future__ import annotations

import argparse
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS, MarketData
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import (
    _ATR_LENGTH,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    _FeatureExtractor,
    harness_prepare,
)
from backtest.wan133_geometry_vs_selection import (
    ARM_DEFAULT,
    ARM_FILTER,
    FILTER_FRACTION,
    MIN_TRADES_FOR_PNL,
    REPORTS_DIR,
    CellResult,
    _bare,
    _is_threshold,
    _write_csv,
    _zwa,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import atr
from strategy.models import ConfluenceParams, OrderBlockParams, OrderBlockResult

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

LENS_PRIMARY = "baseline"
"""공식 렌즈(WAN-104/128). 판정은 이 값으로만 낸다."""

LENS_STRESS = "pen_5bp"
"""체결 보수화 재검용 **옵트인** 민감도(§2). WAN-128이 폐지한 건 병기 「요구」이지
렌즈 자체가 아니다 — 이 이슈 §작업 범위 2가 명시적으로 지시한 재검이다."""

LENS_NAMES: tuple[str, ...] = (LENS_PRIMARY, LENS_STRESS)

ARM_MATCHED = "matched_random"
"""기본 팔에서 필터와 같은 **후보 수**만큼 무작위로 뽑은 매칭 대조군(고르는 규칙 없음)."""

MATCH_SEEDS: tuple[int, ...] = tuple(range(20))
"""매칭 대조군 시드. 20개면 단측 순위 p의 하한이 1/21 ≈ 0.048로 α=0.05를 겨우 통과한다 —
시드를 더 줄이면 **어떤 결과가 나와도 유의할 수 없어** 판정 자체가 불가능해진다."""

SEED_AGGREGATE = -1
"""시드 평균 행의 `seed` 표식(CSV에서 개별 시드 행과 구분하기 위한 센티널)."""

ALPHA = 0.05

SAMPLE_SHARE_BAR = 0.7
"""표본 축소가 개선의 이만큼 이상을 설명하면 (b)로 읽는다.

`default → filter`의 MDD 개선분 중 `default → matched`(고르는 규칙 없이 표본만 줄인 팔)가
재현하는 비율이다. 0.7은 「대부분」의 조작적 정의이고, 판정의 주 근거는 이 비율이 아니라
아래 시드 순위 p값이다(비율은 크기, p값은 유의).
"""


# --------------------------------------------------------------------------- #
# §0 존폭 분포 (존 단위 · 탐지만 하면 되므로 서브스텝 불필요)
# --------------------------------------------------------------------------- #


class ZoneWidthRow(BaseModel):
    """한 (심볼, TF, `combine_obs`)의 존폭/ATR 분포 — 병합 폐지가 자를 자를 얼마나 옮겼나."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    combine_obs: bool
    n_zones: int
    p33: float | None
    median: float | None
    p67: float | None
    mean: float | None


def zone_width_samples(market: MarketData, *, combine_obs: bool) -> list[float]:
    """존 하나당 한 값인 존폭/ATR 표본.

    `retap_signals`를 `zone_key`(병합 클러스터의 안정 식별자, WAN-83)로 묶어 **첫 탭**만
    남긴다 — 탭 수가 많은 존이 분포를 여러 번 차지하면 「존폭 분포」가 아니라 「탭 분포」가
    된다. ATR은 후보 특징(`zone_width_atr`)과 같은 규칙으로 **탭 봉의 직전 확정봉**에서
    읽어 두 표가 같은 척도 위에 놓이게 한다.
    """
    if market.empty:
        return []
    frame = harness_prepare(market.htf_df)
    times = [int(t) for t in frame["open_time"].astype("int64").tolist()]
    atr14 = [float(v) for v in atr(frame, length=_ATR_LENGTH).tolist()]
    pos_of = {t: i for i, t in enumerate(times)}
    obr: OrderBlockResult = harness.detect_order_blocks(
        market, OrderBlockParams(combine_obs=combine_obs)
    )
    seen: set[object] = set()
    out: list[float] = []
    for sig in obr.retap_signals:
        # `zone_key`가 None인 시그널(병합을 안 쓰는 경로)은 존 객체 경계로 대신 식별한다.
        key: object = sig.zone_key or (sig.order_block.start_time, sig.order_block.confirmed_time)
        if key in seen:
            continue
        seen.add(key)
        pos = pos_of.get(sig.trigger_time)
        if pos is None or pos < 1:
            continue
        denom = atr14[pos - 1]
        if denom != denom or denom <= 0:  # NaN·비양수 제외(워밍업).
            continue
        out.append((sig.order_block.top - sig.order_block.bottom) / denom)
    return out


def zone_width_row(market: MarketData, *, combine_obs: bool) -> ZoneWidthRow:
    vals = zone_width_samples(market, combine_obs=combine_obs)
    if not vals:
        return ZoneWidthRow(
            symbol=market.symbol,
            timeframe=market.timeframe,
            combine_obs=combine_obs,
            n_zones=0,
            p33=None,
            median=None,
            p67=None,
            mean=None,
        )
    series = pd.Series(vals)
    return ZoneWidthRow(
        symbol=market.symbol,
        timeframe=market.timeframe,
        combine_obs=combine_obs,
        n_zones=len(vals),
        p33=float(series.quantile(FILTER_FRACTION)),
        median=float(series.median()),
        p67=float(series.quantile(1.0 - FILTER_FRACTION)),
        mean=float(series.mean()),
    )


# --------------------------------------------------------------------------- #
# §1·§2 손익 행
# --------------------------------------------------------------------------- #


class PnlRow(BaseModel):
    """한 (렌즈, 심볼, TF, 구간, 팔, 시드) 셀의 손익."""

    model_config = ConfigDict(frozen=True)

    lens: str
    symbol: str
    timeframe: str
    segment: str
    arm: str
    seed: int
    """`matched_random`의 추첨 시드. 다른 팔과 시드 평균 행은 `SEED_AGGREGATE`(−1)."""
    num_candidates: float
    num_trades: float
    total_return: float
    max_drawdown: float
    win_rate: float


def _metrics_for(
    cands: list[_Candidate], market: MarketData, timeframe: str
) -> tuple[float, float, float, int]:
    """후보 목록 → (total_return, MDD, 승률, 거래수). 시퀀싱·비용·펀딩은 프로덕션 경로 그대로."""
    cfg = harness.build_config(timeframe)
    trades = [t for _, t in sequence_with_candidates(cands, cfg, market.funding_rates)]
    m = build_result_from_trades(trades, cfg, timeframe).metrics
    return m.total_return, m.max_drawdown, m.win_rate, m.num_trades


def pnl_rows_for_cell(cell: CellResult, market: MarketData, *, lens: str) -> list[PnlRow]:
    """세 팔(기본·필터·매칭 대조군)의 구간별 손익.

    매칭 대조군은 **같은 구간의 기본 후보에서** 필터와 같은 개수를 비복원 추출한다 —
    구간을 넘나들면 IS 후보가 OOS 성과에 섞인다.
    """
    if not cell.default_candidates:
        return []
    threshold = _is_threshold(cell)
    pairs = list(zip(cell.default_candidates, cell.default_zwa, strict=True))
    rows: list[PnlRow] = []

    def _row(arm: str, seed: int, cands: list[_Candidate], segment: str) -> PnlRow:
        tr, mdd, wr, nt = _metrics_for(cands, market, cell.timeframe)
        return PnlRow(
            lens=lens,
            symbol=cell.symbol,
            timeframe=cell.timeframe,
            segment=segment,
            arm=arm,
            seed=seed,
            num_candidates=float(len(cands)),
            num_trades=float(nt),
            total_return=tr,
            max_drawdown=mdd,
            win_rate=wr,
        )

    for segment in (SEGMENT_IS, SEGMENT_OOS):
        in_seg = [
            (cand, z)
            for cand, z in pairs
            if (cand.trigger_time < cell.is_boundary) == (segment == SEGMENT_IS)
        ]
        base_cands = [cand for cand, _ in in_seg]
        if threshold is None:
            filter_cands: list[_Candidate] = []
        else:
            filter_cands = [cand for cand, z in in_seg if z is not None and z <= threshold]
        rows.append(_row(ARM_DEFAULT, SEED_AGGREGATE, base_cands, segment))
        rows.append(_row(ARM_FILTER, SEED_AGGREGATE, filter_cands, segment))

        k = min(len(filter_cands), len(base_cands))
        seed_rows: list[PnlRow] = []
        for seed in MATCH_SEEDS:
            drawn = random.Random(seed).sample(base_cands, k) if k else []
            seed_rows.append(_row(ARM_MATCHED, seed, drawn, segment))
        rows.extend(seed_rows)
        if seed_rows:
            rows.append(_mean_row(seed_rows))
    return rows


def _mean_row(seed_rows: Sequence[PnlRow]) -> PnlRow:
    """시드 평균 행(팔 간 대조표에 쓰는 대표값). 좌표는 시드만 빼고 전부 같다."""
    head = seed_rows[0]
    n = len(seed_rows)
    return head.model_copy(
        update={
            "seed": SEED_AGGREGATE,
            "num_candidates": sum(r.num_candidates for r in seed_rows) / n,
            "num_trades": sum(r.num_trades for r in seed_rows) / n,
            "total_return": sum(r.total_return for r in seed_rows) / n,
            "max_drawdown": sum(r.max_drawdown for r in seed_rows) / n,
            "win_rate": sum(r.win_rate for r in seed_rows) / n,
        }
    )


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def symbol_mean(
    rows: Sequence[PnlRow],
    *,
    lens: str,
    timeframe: str,
    segment: str,
    arm: str,
    seed: int = SEED_AGGREGATE,
    gated: bool = True,
) -> dict[str, float | None]:
    """6심볼 심볼평균(수익·MDD·승률은 단순평균, 거래수는 합).

    `gated=True`면 **거래 20건 미만 셀을 평균에서 뺀다**(`MIN_TRADES_FOR_PNL`, WAN-133과
    같은 기준) — 사이징 바닥(WAN-79)에 걸려 표본이 무너진 필터 셀이 6심볼 평균의 1/6을
    차지하는 것을 막는다. ⚠️ 게이트는 **필터 팔과 매칭 팔에 같은 규칙으로** 걸린다 —
    한쪽만 걸면 두 팔의 심볼 구성이 달라져 대조가 성립하지 않는다.
    """
    sub = [
        r
        for r in rows
        if r.lens == lens
        and r.timeframe == timeframe
        and r.segment == segment
        and r.arm == arm
        and r.seed == seed
    ]
    excluded = [r for r in sub if r.num_trades < MIN_TRADES_FOR_PNL]
    if gated:
        sub = [r for r in sub if r.num_trades >= MIN_TRADES_FOR_PNL]
    if not sub:
        return {
            "total_return": None,
            "max_drawdown": None,
            "win_rate": None,
            "num_trades": None,
            "num_candidates": None,
            "n_symbols": 0.0,
            "n_excluded": float(len(excluded)),
        }
    n = len(sub)
    return {
        "total_return": sum(r.total_return for r in sub) / n,
        "max_drawdown": sum(r.max_drawdown for r in sub) / n,
        "win_rate": sum(r.win_rate for r in sub) / n,
        "num_trades": sum(r.num_trades for r in sub),
        "num_candidates": sum(r.num_candidates for r in sub),
        "n_symbols": float(n),
        "n_excluded": float(len(excluded)),
    }


class MatchedTestRow(BaseModel):
    """필터 팔 vs 매칭 대조군 시드 분포 — 「선별인가 표본 축소인가」의 판정 통계량.

    ⚠️ **게이트를 켜면 두 팔의 심볼 구성이 갈릴 수 있으므로**(필터 셀만 20건 미만이 되는
    일이 잦다) 이 검정은 **필터 팔이 유효한 심볼 집합**으로 두 팔을 모두 잘라 낸 뒤 평균을
    낸다. 그래야 p값이 「선별의 몫」만 재고 심볼 구성 차이를 재지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    lens: str
    timeframe: str
    segment: str
    n_symbols: int
    n_seeds: int
    filter_return: float | None
    matched_return_mean: float | None
    p_return: float | None
    """단측 순위 p — 매칭 대조군이 필터 이상으로 벌 확률."""
    filter_mdd: float | None
    matched_mdd_mean: float | None
    p_mdd: float | None
    """단측 순위 p — 매칭 대조군의 MDD가 필터 이하일 확률(낮을수록 필터가 낫다)."""
    default_mdd: float | None
    sample_share: float | None
    """`default→filter` MDD 개선분 중 `default→matched`가 재현하는 비율(표본 축소의 몫)."""


def matched_test_row(
    rows: Sequence[PnlRow], *, lens: str, timeframe: str, segment: str
) -> MatchedTestRow:
    """필터 팔을 매칭 대조군 시드 분포 위에 놓고 단측 순위 p를 낸다."""
    empty = MatchedTestRow(
        lens=lens,
        timeframe=timeframe,
        segment=segment,
        n_symbols=0,
        n_seeds=0,
        filter_return=None,
        matched_return_mean=None,
        p_return=None,
        filter_mdd=None,
        matched_mdd_mean=None,
        p_mdd=None,
        default_mdd=None,
        sample_share=None,
    )

    def _pick(arm: str, seed: int) -> dict[str, PnlRow]:
        return {
            r.symbol: r
            for r in rows
            if r.lens == lens
            and r.timeframe == timeframe
            and r.segment == segment
            and r.arm == arm
            and r.seed == seed
        }

    filt = _pick(ARM_FILTER, SEED_AGGREGATE)
    base = _pick(ARM_DEFAULT, SEED_AGGREGATE)
    # 판정 심볼 집합 = 필터 셀이 유효 표본을 가진 심볼(두 팔에 같은 집합을 쓴다).
    symbols = sorted(s for s, r in filt.items() if r.num_trades >= MIN_TRADES_FOR_PNL)
    if not symbols:
        return empty

    def _avg(picked: dict[str, PnlRow], attr: str) -> float | None:
        vals = [getattr(picked[s], attr) for s in symbols if s in picked]
        return sum(vals) / len(vals) if len(vals) == len(symbols) else None

    f_ret, f_mdd = _avg(filt, "total_return"), _avg(filt, "max_drawdown")
    d_mdd = _avg(base, "max_drawdown")
    seed_ret: list[float] = []
    seed_mdd: list[float] = []
    for seed in MATCH_SEEDS:
        picked = _pick(ARM_MATCHED, seed)
        r, m = _avg(picked, "total_return"), _avg(picked, "max_drawdown")
        if r is None or m is None:
            continue
        seed_ret.append(r)
        seed_mdd.append(m)
    if not seed_ret or f_ret is None or f_mdd is None:
        return empty

    n = len(seed_ret)
    # 단측 순위 p(+1 보정) — 매칭 대조군이 필터를 이기거나 비기는 시드의 비율.
    p_ret = (sum(1 for v in seed_ret if v >= f_ret) + 1) / (n + 1)
    p_mdd = (sum(1 for v in seed_mdd if v <= f_mdd) + 1) / (n + 1)
    m_ret = sum(seed_ret) / n
    m_mdd = sum(seed_mdd) / n
    share: float | None = None
    if d_mdd is not None:
        gap = d_mdd - f_mdd
        # 개선이 없거나(≤0) 무시할 만큼 작으면 비율이 뜻을 잃는다(0으로 나누기·부호 함정).
        share = (d_mdd - m_mdd) / gap if gap > 1e-9 else None
    return MatchedTestRow(
        lens=lens,
        timeframe=timeframe,
        segment=segment,
        n_symbols=len(symbols),
        n_seeds=n,
        filter_return=f_ret,
        matched_return_mean=m_ret,
        p_return=p_ret,
        filter_mdd=f_mdd,
        matched_mdd_mean=m_mdd,
        p_mdd=p_mdd,
        default_mdd=d_mdd,
        sample_share=share,
    )


def leave_one_out(
    rows: Sequence[PnlRow], *, lens: str, timeframe: str, arm: str, segment: str = SEGMENT_OOS
) -> str:
    """심볼 하나씩 빼고 본 OOS total_return 심볼평균 — 편중 확인."""
    sub = [
        r
        for r in rows
        if r.lens == lens
        and r.timeframe == timeframe
        and r.segment == segment
        and r.arm == arm
        and r.seed == SEED_AGGREGATE
        and r.num_trades >= MIN_TRADES_FOR_PNL
    ]
    if len(sub) < 2:
        return "표본 부족"
    parts: list[str] = []
    for drop in sub:
        rest = [r.total_return for r in sub if r.symbol != drop.symbol]
        parts.append(f"−{_bare(drop.symbol)} {sum(rest) / len(rest) * 100:+.2f}%")
    return " · ".join(parts)


def contamination_note(rows: Sequence[PnlRow], *, lens: str, timeframe: str, segment: str) -> str:
    """거래 수 오염 게이트(WAN-126)를 **세 팔 모두**에 적용해 매칭이 실제로 됐는지 보인다."""
    base = symbol_mean(rows, lens=lens, timeframe=timeframe, segment=segment, arm=ARM_DEFAULT)
    filt = symbol_mean(rows, lens=lens, timeframe=timeframe, segment=segment, arm=ARM_FILTER)
    match = symbol_mean(rows, lens=lens, timeframe=timeframe, segment=segment, arm=ARM_MATCHED)
    b, f, m = base.get("num_trades"), filt.get("num_trades"), match.get("num_trades")
    if not b or f is None or m is None:
        return "판정 불가(표본 없음)"
    fp, mp = (f - b) / b * 100.0, (m - b) / b * 100.0
    gap = abs(f - m) / f * 100.0 if f else 0.0
    flag = "이내" if gap <= 5.0 else "**초과**"
    # 후보는 정확히 맞췄어도 동시 1포지션 시퀀서가 두 팔을 다르게 깎는다. 그 잔차의
    # **방향**이 판정에 유리한지 불리한지를 명시한다 — 크기만 적으면 읽는 사람이
    # "어느 쪽으로 기울었나"를 알 수 없다.
    if gap <= 5.0:
        bias = ""
    elif m > f:
        bias = " ⚠️ 매칭이 더 많이 거래한다 — 노출이 커 MDD가 부풀려지므로 **필터에 유리한 잔차**다"
    else:
        bias = " ⚠️ 필터가 더 많이 거래한다 — **필터에 불리한 잔차**다"
    return (
        f"기본 {int(b)}건 → 필터 {int(f)}건({fp:+.1f}%) · 매칭 {m:.0f}건({mp:+.1f}%) — "
        f"필터↔매칭 격차 {gap:.1f}%(5% {flag}).{bias}"
    )


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def verdict(tests: Sequence[MatchedTestRow], *, timeframe: str, lens: str = LENS_PRIMARY) -> str:
    """한 TF의 (a) 선별 / (b) 표본 축소 판정 — OOS 매칭 검정으로 낸다."""
    row = next(
        (
            t
            for t in tests
            if t.lens == lens and t.timeframe == timeframe and t.segment == SEGMENT_OOS
        ),
        None,
    )
    if row is None or row.filter_mdd is None or row.p_mdd is None or row.matched_mdd_mean is None:
        return f"**{timeframe}**: ⚠️ 판정 불가 — 유효 표본(거래 {MIN_TRADES_FOR_PNL}건) 셀이 없다."
    if row.n_symbols < 3:
        return (
            f"**{timeframe}**: ⚠️ 판정 불가(대조군) — 필터 팔의 유효 심볼이 "
            f"{row.n_symbols}개뿐이라 심볼평균이 의사결정 가치를 갖지 못한다."
        )
    share = "—" if row.sample_share is None else f"{row.sample_share * 100:.0f}%"
    p_ret = "—" if row.p_return is None else f"{row.p_return:.3f}"
    detail = (
        f"필터 MDD {_pct(row.filter_mdd)} vs 매칭 {_pct(row.matched_mdd_mean)}"
        f"(p={row.p_mdd:.3f}) · 수익 {_pct(row.filter_return, signed=True)} vs "
        f"{_pct(row.matched_return_mean, signed=True)}(p={p_ret}) · "
        f"표본 축소가 설명하는 MDD 개선 {share} · {row.n_symbols}심볼"
    )
    if row.p_mdd <= ALPHA:
        return (
            f"**{timeframe}**: **(a) 선별 근거 있음** — 같은 수의 거래를 무작위로 뽑은 "
            f"대조군보다 MDD가 유의하게 낮다({detail}). 즉 MDD 개선이 표본 축소만으로는 "
            "재현되지 않는다. 🚨 **그래도 채택이 아니다** — 재-베이스라인은 사용자 결정이고, "
            "아래 경고(심볼 편중 · 체결 보수화 · 사이징 바닥)를 함께 넘긴다."
        )
    mostly = row.sample_share is not None and row.sample_share >= SAMPLE_SHARE_BAR
    bar_note = (
        f" 표본 축소가 개선의 {SAMPLE_SHARE_BAR:.0%} 이상을 설명한다."
        if mostly
        else " (크기 지표인 「표본축소 몫」은 이 셀에서 그 기준 미만이거나 뜻을 잃었다 —"
        " 판정의 주 근거는 시드 순위 p값이다.)"
    )
    return (
        f"**{timeframe}**: **(b) 표본 축소 효과** — 같은 수의 거래를 **무작위로** 뽑아도 "
        f"MDD 개선이 재현된다({detail}).{bar_note} WAN-133의 「존폭 필터는 알파가 아니라 "
        "위험의 모양만 바꾼다」를 확정 기록한다 — 좁은 존을 고르는 규칙이 아니라 "
        "**덜 베팅하는 것**이 MDD를 줄인 것이다."
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass
class ExperimentResult:
    zone_widths: list[ZoneWidthRow] = field(default_factory=list)
    pnl_rows: list[PnlRow] = field(default_factory=list)
    matched_tests: list[MatchedTestRow] = field(default_factory=list)


def build_cell(market: MarketData, *, params: ConfluenceParams) -> CellResult:
    """한 (심볼, TF)의 기본 팔 후보 + 후보별 `zone_width_atr`.

    ⚠️ 오더블록 탐지는 **핀 없이** 채택 기본값(`combine_obs=False`, WAN-149)으로 한다 —
    WAN-133이 `LEGACY_OB_PARAMS`로 병합을 고정한 것과 정반대이고, 그게 이 이슈의 요점이다.
    """
    cell = CellResult(symbol=market.symbol, timeframe=market.timeframe)
    if market.empty or market.df_1m.empty:
        return cell
    frame = harness_prepare(market.htf_df)
    extractor = _FeatureExtractor.build(frame)
    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    cell.is_boundary = start + int((end - start) * IS_FRACTION)
    cands, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=harness.build_config(market.timeframe),
        order_block_result=harness.detect_order_blocks(market),
    )
    cell.default_candidates = cands
    cell.default_zwa = [_zwa(extractor, c) for c in cands]
    return cell


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    lenses: tuple[str, ...] = LENS_NAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = harness.DB_PATH,
) -> ExperimentResult:
    """6심볼 × {15m,1h} × {baseline, pen_5bp} — 존폭 분포 + 세 팔 손익 + 매칭 검정."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    result = ExperimentResult()
    for symbol in symbols:
        norm = harness.normalize_symbol(symbol)
        for timeframe in timeframes:
            market = harness.load_market_data(
                norm, timeframe, start_ms=start_ms, end_ms=end_ms, need_1m=True, db_path=db_path
            )
            # §0 — 존폭 분포(탐지만 하면 되므로 렌즈와 무관하게 한 번).
            for combine in (False, True):
                result.zone_widths.append(zone_width_row(market, combine_obs=combine))
            for lens in lenses:
                params = harness.build_params(fill=harness.fill_preset(lens))
                cell = build_cell(market, params=params)
                result.pnl_rows.extend(pnl_rows_for_cell(cell, market, lens=lens))
                print(
                    f"[wan142] {norm} {timeframe} {lens}: 후보={len(cell.default_candidates)}",
                    flush=True,
                )
    for lens in lenses:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                result.matched_tests.append(
                    matched_test_row(
                        result.pnl_rows, lens=lens, timeframe=timeframe, segment=segment
                    )
                )
    return result


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%" if signed else f"{value * 100:.2f}%"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def build_summary_markdown(result: ExperimentResult, *, timeframes: Sequence[str]) -> str:
    lines: list[str] = []
    lines.append("# WAN-142 존폭 필터 채택 판정 — 거래 수를 맞춘 손익 재검 + 체결 보수화 재검\n")
    # 심볼·TF는 실제로 돌린 것을 적는다 — 부분 실행(스모크)에 6심볼 라벨이 붙으면 그
    # 표가 나중에 "6심볼 격자"로 인용된다(WAN-95가 고친 라벨 거짓의 부류).
    symbols = sorted({_bare(r.symbol) for r in result.pnl_rows}) or ["—"]
    lines.append(
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)}, 못 박은 창 "
        f"**{DEFAULT_START} ~ {DEFAULT_END}**, **오늘의 채택 기본값**(`ConfluenceParams()` · "
        "`OrderBlockParams()` — 존 지정가 offset 2bp · `intrabar_live` 밴드(WAN-132) · "
        "`unconditional` 게이트(WAN-123) · 고정 1.5R · 롱 온리 · `combine_obs=False`"
        "(WAN-149)). 공식 렌즈 `baseline` 단독이고 `pen_5bp`는 §2 옵트인 민감도다.\n"
    )
    lines.append(
        "📌 **WAN-133과 엔진이 다르다** — 그쪽 CSV는 `harness.pin_band_bar`·"
        "`LEGACY_OB_PARAMS`로 **옛 밴드(`tap`) + 병합 존**에 못 박혀 있다(옛 리포트 재현이 "
        "그 고정의 목적이다). 이 표는 **핀을 하나도 쓰지 않는다** — 구조(문턱 규칙·유효 표본 "
        "기준·leave-one-out)만 재사용하고 엔진을 오늘 것으로 갈아끼웠다. **두 표의 셀을 직접 "
        "비교하지 말 것.**\n"
    )
    lines.append(
        f"재현: `python -m backtest.wan142_zone_width_filter_verdict`. "
        f"매칭 대조군 시드 {len(MATCH_SEEDS)}개.\n"
    )

    lines.append("## 판정\n")
    for timeframe in timeframes:
        lines.append(f"* {verdict(result.matched_tests, timeframe=timeframe)}")
    lines.append("")

    lines.append(_zone_width_section(result, timeframes))
    lines.append(_matched_section(result, timeframes))
    lines.append(_pnl_section(result, timeframes))
    lines.append(_stress_section(result, timeframes))
    lines.append("## 결론\n")
    lines.append(_conclusion(result, timeframes))
    return "\n".join(lines)


def _zone_width_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["## §0 존폭 분포 — 병합 폐지(WAN-149)가 자를 자를 얼마나 옮겼나\n"]
    lines.append(
        "존 하나당 한 값(`zone_key`별 **첫 탭** 기준 존폭/ATR, ATR은 탭 봉 직전 확정봉). "
        "`combine_obs=False`가 오늘의 채택 기본값이고 `True`가 WAN-133이 잰 세계다. "
        "**하위 1/3 문턱(p33)이 얼마나 내려가는지**가 이 표의 요점이다 — 옛 문턱을 그대로 "
        "쓰면 전혀 다른 비율을 자르게 된다(그래서 §1은 매 셀 IS에서 문턱을 다시 잡는다).\n"
    )
    lines.append(
        "| TF | 심볼 | 병합 | 존 수 | p33 | 중앙 | p67 |\n| -- | -- | -- | -- | -- | -- | -- |"
    )
    for timeframe in timeframes:
        for row in result.zone_widths:
            if row.timeframe != timeframe:
                continue
            lines.append(
                f"| {timeframe} | {_bare(row.symbol)} | {'ON' if row.combine_obs else 'OFF'} | "
                f"{row.n_zones} | {_num(row.p33)} | {_num(row.median)} | {_num(row.p67)} |"
            )
    lines.append("")
    for timeframe in timeframes:
        for combine in (False, True):
            sub = [
                r
                for r in result.zone_widths
                if r.timeframe == timeframe and r.combine_obs is combine and r.median is not None
            ]
            if not sub:
                continue
            med = sum(r.median for r in sub if r.median is not None) / len(sub)
            p33 = sum(r.p33 for r in sub if r.p33 is not None) / len(sub)
            zones = sum(r.n_zones for r in sub)
            lines.append(
                f"* **{timeframe} 병합 {'ON' if combine else 'OFF'}**: 존 {zones}개 · "
                f"심볼평균 p33 {p33:.2f} · 중앙 {med:.2f}"
            )
    lines.append("")
    return "\n".join(lines)


def _matched_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["## §1 거래 수를 맞춘 팔 — 선별인가 표본 축소인가 (공식 렌즈 `baseline`)\n"]
    lines.append(
        "`filter_bot3` = 존폭 하위 1/3만 진입(IS 문턱 → OOS 적용) · `matched_random` = **같은 "
        f"구간의 기본 후보에서 필터와 같은 개수를 무작위 추출**(시드 {len(MATCH_SEEDS)}개). "
        "두 팔은 표본 크기·노출·복리가 같고 **고르는 규칙만 다르다**. p는 단측 순위값"
        "(매칭 대조군이 필터를 이기거나 비긴 시드 비율, +1 보정)이라 하한이 "
        f"1/{len(MATCH_SEEDS) + 1} ≈ {1 / (len(MATCH_SEEDS) + 1):.3f}이다.\n"
    )
    lines.append(
        "⚠️ **검정 심볼 집합은 「필터 셀이 거래 "
        f"{MIN_TRADES_FOR_PNL}건 이상인 심볼」로 두 팔에 똑같이 적용한다** — 한쪽만 게이트를 "
        "걸면 심볼 구성 차이가 p값에 섞인다.\n"
    )
    lines.append(
        "| TF | 구간 | 심볼 | 필터 수익 | 매칭 수익 | p(수익) | 필터 MDD | 매칭 MDD | "
        "p(MDD) | 기본 MDD | 표본축소 몫 |\n" + "| -- " * 11 + "|"
    )
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            t = next(
                (
                    r
                    for r in result.matched_tests
                    if r.lens == LENS_PRIMARY and r.timeframe == timeframe and r.segment == segment
                ),
                None,
            )
            if t is None:
                continue
            share = "—" if t.sample_share is None else f"{t.sample_share * 100:.0f}%"
            lines.append(
                f"| {timeframe} | {segment} | {t.n_symbols} | "
                f"{_pct(t.filter_return, signed=True)} | "
                f"{_pct(t.matched_return_mean, signed=True)} | "
                f"{'—' if t.p_return is None else f'{t.p_return:.3f}'} | "
                f"{_pct(t.filter_mdd)} | {_pct(t.matched_mdd_mean)} | "
                f"{'—' if t.p_mdd is None else f'{t.p_mdd:.3f}'} | "
                f"{_pct(t.default_mdd)} | {share} |"
            )
    lines.append("")
    lines.append("**거래 수 정합 확인(WAN-126 게이트를 세 팔 모두에):**\n")
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            note = contamination_note(
                result.pnl_rows, lens=LENS_PRIMARY, timeframe=timeframe, segment=segment
            )
            lines.append(f"* {timeframe} {segment}: {note}")
    lines.append("")
    return "\n".join(lines)


def _pnl_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["### 세 팔 대조표 (6심볼 심볼평균, `baseline`)\n"]
    lines.append(
        f"⚠️ 심볼평균은 거래 {MIN_TRADES_FOR_PNL}건 미만 셀을 제외한 값이다(제외 셀 수 병기). "
        "`matched_random` 행은 시드 평균이다.\n"
    )
    lines.append(
        "| TF | 구간 | 팔 | 후보 | 거래 | total_return | MDD | 승률 | 심볼(제외) |\n"
        + "| -- " * 9
        + "|"
    )
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for arm in (ARM_DEFAULT, ARM_FILTER, ARM_MATCHED):
                m = symbol_mean(
                    result.pnl_rows,
                    lens=LENS_PRIMARY,
                    timeframe=timeframe,
                    segment=segment,
                    arm=arm,
                )
                nc, nt = m.get("num_candidates"), m.get("num_trades")
                lines.append(
                    f"| {timeframe} | {segment} | `{arm}` | "
                    f"{'—' if nc is None else f'{nc:.0f}'} | "
                    f"{'—' if nt is None else f'{nt:.0f}'} | "
                    f"{_pct(m.get('total_return'), signed=True)} | {_pct(m.get('max_drawdown'))} | "
                    f"{_pct(m.get('win_rate'))} | "
                    f"{m.get('n_symbols'):.0f}({m.get('n_excluded'):.0f}) |"
                )
    lines.append("")
    lines.append("**OOS total_return 심볼 편중(leave-one-out):**\n")
    for timeframe in timeframes:
        for arm in (ARM_FILTER, ARM_MATCHED):
            loo = leave_one_out(result.pnl_rows, lens=LENS_PRIMARY, timeframe=timeframe, arm=arm)
            lines.append(f"* {timeframe} `{arm}`: {loo}")
    lines.append("")
    lines.append("### 심볼별 분해 (OOS, `baseline`)\n")
    lines.append(
        "| TF | 심볼 | 기본 거래/수익/MDD | 필터 거래/수익/MDD | 매칭 거래/수익/MDD |\n"
        + "| -- " * 5
        + "|"
    )
    for timeframe in timeframes:
        for symbol in DEFAULT_SYMBOLS:
            picked = {
                r.arm: r
                for r in result.pnl_rows
                if r.lens == LENS_PRIMARY
                and r.timeframe == timeframe
                and r.segment == SEGMENT_OOS
                and r.symbol == harness.normalize_symbol(symbol)
                and r.seed == SEED_AGGREGATE
            }
            if len(picked) < 3:
                continue
            cells = []
            for arm in (ARM_DEFAULT, ARM_FILTER, ARM_MATCHED):
                r = picked[arm]
                warn = " ⚠️" if r.num_trades < MIN_TRADES_FOR_PNL else ""
                cells.append(
                    f"{r.num_trades:.0f} / {r.total_return * 100:+.2f}% / "
                    f"{r.max_drawdown * 100:.2f}%{warn}"
                )
            lines.append(f"| {timeframe} | {_bare(symbol)} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def _stress_section(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    lines = ["## §2 체결 보수화 재검 (`pen_5bp` — 옵트인 민감도)\n"]
    lines.append(
        "지정가를 5bp 관통해야 체결로 치는 렌즈에서 §1을 통째로 다시 돌린다. WAN-128이 "
        "폐지한 것은 **병기 요구**이지 렌즈 자체가 아니다(공식 수치는 위 `baseline`이다). "
        "질문은 하나다 — **판정이 바뀌는가.**\n"
    )
    lines.append(
        "| 렌즈 | TF | 구간 | 심볼 | 필터 수익 | 매칭 수익 | 필터 MDD | 매칭 MDD | p(MDD) |\n"
        + "| -- " * 9
        + "|"
    )
    for lens in LENS_NAMES:
        for timeframe in timeframes:
            for segment in (SEGMENT_IS, SEGMENT_OOS):
                t = next(
                    (
                        r
                        for r in result.matched_tests
                        if r.lens == lens and r.timeframe == timeframe and r.segment == segment
                    ),
                    None,
                )
                if t is None:
                    continue
                lines.append(
                    f"| `{lens}` | {timeframe} | {segment} | {t.n_symbols} | "
                    f"{_pct(t.filter_return, signed=True)} | "
                    f"{_pct(t.matched_return_mean, signed=True)} | "
                    f"{_pct(t.filter_mdd)} | {_pct(t.matched_mdd_mean)} | "
                    f"{'—' if t.p_mdd is None else f'{t.p_mdd:.3f}'} |"
                )
    lines.append("")
    lines.append("**렌즈가 판정을 바꾸는가:**\n")
    for timeframe in timeframes:
        lines.append(f"* {_lens_flip_note(result, timeframe=timeframe)}")
    lines.append("")
    return "\n".join(lines)


def _lens_flip_note(result: ExperimentResult, *, timeframe: str) -> str:
    """`baseline` OOS 판정과 `pen_5bp` OOS 판정이 갈리는지 한 문장."""

    def _row(lens: str) -> MatchedTestRow | None:
        return next(
            (
                r
                for r in result.matched_tests
                if r.lens == lens and r.timeframe == timeframe and r.segment == SEGMENT_OOS
            ),
            None,
        )

    base, stress = _row(LENS_PRIMARY), _row(LENS_STRESS)
    if base is None or stress is None or base.p_mdd is None or stress.p_mdd is None:
        return f"**{timeframe}**: 판정 불가 — 한쪽 렌즈의 유효 표본이 없다."
    same = (base.p_mdd <= ALPHA) == (stress.p_mdd <= ALPHA)
    label = "**바꾸지 않는다**" if same else "🚨 **바꾼다**"
    return (
        f"**{timeframe}**: {label} — `baseline` p(MDD)={base.p_mdd:.3f} · "
        f"`pen_5bp` p(MDD)={stress.p_mdd:.3f}(둘 다 α={ALPHA} 기준). 필터 수익은 "
        f"{_pct(base.filter_return, signed=True)} → {_pct(stress.filter_return, signed=True)}, "
        f"매칭 수익은 {_pct(base.matched_return_mean, signed=True)} → "
        f"{_pct(stress.matched_return_mean, signed=True)}로 움직인다."
    )


def _conclusion(result: ExperimentResult, timeframes: Sequence[str]) -> str:
    verdicts = {tf: verdict(result.matched_tests, timeframe=tf) for tf in timeframes}
    selective = [tf for tf, v in verdicts.items() if "(a) 선별" in v]
    reduced = [tf for tf, v in verdicts.items() if "(b) 표본" in v]
    tail = (
        "🚨 **어느 쪽이든 「엣지 찾았다」로 인용 금지** — 이 표는 `baseline`(닿으면 체결) 위의 "
        "값이고(§2가 `pen_5bp`로 재검한다), 이 저장소의 모든 플러스가 그렇듯 심볼 편중을 "
        "leave-one-out으로 함께 읽어야 한다. 또한 필터가 고르는 좁은 존은 사이징 바닥"
        "(`min_stop_distance_fraction=0.3%`, WAN-79)에 먼저 걸려 저변동성 심볼에서 표본이 "
        "무너진다(WAN-133 Part C) — 그 셀은 여기서도 판정에서 뺐다. **기본값·토대는 바꾸지 "
        "않았다**(측정 전용, 실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` 유지)."
    )
    if reduced and not selective:
        return (
            "**(b) 존폭 필터의 MDD 개선은 표본 축소의 산물이다 — 채택 권고 없음.** 같은 수의 "
            "거래를 **무작위로** 뽑은 매칭 대조군이 필터의 MDD 개선을 재현한다. 즉 MDD를 줄인 "
            "것은 「좁은 존을 고르는 규칙」이 아니라 **덜 베팅하는 것**이었다. WAN-133의 "
            "「존폭 필터는 알파가 아니라 위험의 모양만 바꾼다」를 확정 기록하고, 「엣지 없음」"
            "(WAN-84/88/111/114/124)은 그대로다. 📌 **세 번째 후보(c) 「그냥 병합의 산물」도 "
            "함께 배제된다** — 이 표는 병합을 폐지한 오늘의 엔진(`combine_obs=False`) 위에서 "
            "존폭 분포를 다시 잡고(§0) 그 위에서 잰 것이라, 병합이 만들던 넓은 존은 애초에 "
            "없다. " + tail
        )
    if selective and not reduced:
        return (
            f"**(a) 두 작업 TF({', '.join(selective)}) 모두 선별 근거가 남았다 — 다만 채택이 "
            "아니라 「재-베이스라인 결정 이슈」 제안이다.** 같은 수의 거래를 무작위로 뽑은 "
            "대조군보다 필터의 MDD가 유의하게 낮으므로 개선을 표본 축소로 환원할 수 없다. "
            "**개발자 임의 착수 금지** — 진입 규칙을 바꾸는 것은 사용자 결정이다(WAN-112/123/"
            "132/149와 같은 부류). " + tail
        )
    if not selective and not reduced:
        return (
            "**⚠️ 판정 불가 — 채택 권고 없음.** 어느 작업 TF에서도 매칭 검정이 유효 표본을 "
            "얻지 못했다(필터 팔이 사이징 바닥·표본 게이트에 걸린다). 이 상태에서 필터를 "
            "채택하거나 기각할 근거는 없다. " + tail
        )
    return (
        "**(c) TF에 갈린다 — 채택 권고 없음.** 선별 근거가 남은 "
        f"TF({', '.join(selective) or '없음'})와 표본 축소로 무너지는 "
        f"TF({', '.join(reduced) or '없음'})가 갈린다. 하나의 기본값으로 "
        "두 작업 TF를 다 좋게 할 수 없고, 「TF마다 다른 필터」는 IS에서 고르는 새 자유 "
        "파라미터다(WAN-108/143이 경계한 자리). " + tail
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _load_from_csv(pnl_path: Path, zone_path: Path) -> ExperimentResult:
    """CSV만으로 요약을 재생성한다(격자 재실행 없이 문장·표만 고칠 때)."""
    pnl = [PnlRow.model_validate(rec) for rec in pd.read_csv(pnl_path).to_dict("records")]
    zones = [ZoneWidthRow.model_validate(rec) for rec in pd.read_csv(zone_path).to_dict("records")]
    lenses = tuple(dict.fromkeys(r.lens for r in pnl))
    timeframes = tuple(dict.fromkeys(r.timeframe for r in pnl))
    tests = [
        matched_test_row(pnl, lens=lens, timeframe=tf, segment=seg)
        for lens in lenses
        for tf in timeframes
        for seg in (SEGMENT_IS, SEGMENT_OOS)
    ]
    return ExperimentResult(zone_widths=zones, pnl_rows=pnl, matched_tests=tests)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-142 존폭 필터 채택 판정")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--lenses", type=str, default=",".join(LENS_NAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--pnl-out", type=Path, default=REPORTS_DIR / "wan142_matched_pnl.csv")
    parser.add_argument("--zone-out", type=Path, default=REPORTS_DIR / "wan142_zone_width.csv")
    parser.add_argument("--test-out", type=Path, default=REPORTS_DIR / "wan142_matched_test.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan142_summary.md")
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="격자를 다시 돌리지 않고 기존 CSV로 요약만 재생성한다.",
    )
    args = parser.parse_args(argv)

    timeframes = tuple(t.strip() for t in args.timeframes.split(",") if t.strip())
    if args.from_csv:
        result = _load_from_csv(args.pnl_out, args.zone_out)
        timeframes = tuple(dict.fromkeys(r.timeframe for r in result.pnl_rows))
    else:
        result = run_experiment(
            symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=timeframes,
            lenses=tuple(x.strip() for x in args.lenses.split(",") if x.strip()),
            start=args.start,
            end=args.end,
            db_path=args.db,
        )
        _write_csv(_rows_to_frame(result.pnl_rows), args.pnl_out)
        _write_csv(_rows_to_frame(result.zone_widths), args.zone_out)
        print(f"[wan142] pnl → {args.pnl_out}")
        print(f"[wan142] zone_width → {args.zone_out}")
    _write_csv(_rows_to_frame(result.matched_tests), args.test_out)
    print(f"[wan142] matched_test → {args.test_out}")

    summary = build_summary_markdown(result, timeframes=timeframes)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan142] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
