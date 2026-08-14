"""손절폭 가드(0.3%)를 **존폭 필터 1.28이 켜진 오늘 엔진**에서 켜고/끈 수익률 재측정
(WAN-197 — 사용자 질문 "필터 키고 새로 재야 할 것 같은데?", 2026-07-26).

## 이 모듈이 재는 것 — WAN-154 §3-B와 무엇이 다른가

WAN-154 §3-B가 최소 손절폭 가드(`min_stop_distance_fraction=0.003`)를 켜고/끈 수익률을
**오늘 엔진**에서 쟀지만, 그때 **존폭 필터를 끈 채**(`max_zone_width_atr=LEGACY`=None)
측정했다 — WAN-154는 필터를 후보 리스트로 직접 만들어 매칭 대조군과 겨루는 모듈이라
엔진 필터를 켜면 이중 필터가 되기 때문이다. 그 뒤 WAN-159가 **필터 1.28을 채택
기본값으로** 올렸으므로, **지금 실제로 매매하는 엔진(필터 켜짐)에서 가드만 켜고 끈 깨끗한
비교가 없었다.**

가드와 필터가 정면충돌한다 — 둘 다 "좁은 것"을 향한다(필터는 좁은 존만, 가드는 짧은 손절을
거부). WAN-154 §3-B가 이미 관찰했다: `zone` 장벽 TRX 15m은 필터 후보의 92.6%를 가드가
잘라 16거래로 붕괴했다(20건 유효 기준 미달 = 판정 불가). 그래서 **필터가 켜진 오늘 엔진**의
가드 효과는 WAN-154(필터 끈 판)의 「15m +3.3~+5.9%p · 1h 무영향」과 다를 수 있다.

## WAN-154와의 관계 — 파이프라인 재사용, 축은 하나만

`trade_stats`·`per_trade_records`·`apply_guard`·`symbol_mean` 골격은 전부
`backtest.wan152_selection_vs_geometry`의 것이다(새 파이프라인 금지 — WAN-154 사양 계승).
이 모듈은 **필터를 후보로 만들지 않는다** — 엔진의 채택 기본값(`max_zone_width_atr=1.28`)이
후보를 이미 걸러 냈고, 그 `default` 팔(= 필터 켜진 채택 엔진 = 인자 없는 `backtest.run`)
위에서 가드만 스윕한다. 그래서 WAN-152/154의 매칭 대조군·세 장벽은 여기 없다 — 축은
**가드 하나**다. 가드는 시퀀싱(`position_size`)에서만 걸리므로 후보를 한 번만 빌드하고
값마다 재시퀀싱만 한다.

🚨 **핀 미사용** — `LEGACY_*`·`pin_band_bar`를 쓰지 않는다(오늘의 채택 기본값 그대로).
회귀 테스트가 후보 집합·구간 분할로 고정한다(WAN-152 패턴).

## 좌표 (WAN-182 채택 창)

9종목 · 못 박은 6년 창(2020-09-15~2026-07-22) · 15m·1h·4h(4h는 표본 게이트로 대조군,
1d 제외). **신규 3종목(DOGE·LINK·LTC)은 펀딩 대리 규칙**(WAN-180/182 — 기존 종목 중 확정
펀딩 평균 최고 종목의 시계열)을 얹는다. 안 얹으면 그 종목 수익률이 펀딩비 0으로 부풀려진다.

## WAN-205 확장 (사용자 질문 2026-07-28) — 촘촘 스윕 + `pen_5bp`

WAN-197은 가드를 거친 5값(0/0.22/0.30/0.33/0.55%) × `baseline`으로만 쟀다. WAN-205가 두
사각을 메운다: (1) **0.15/0.20/0.25%를 끼운 촘촘 스윕**(`FINE_GUARDS` 8값) — 순수 수익
정점이 0.22% 근처라 그 사이를 봐야 0.3%가 최적 근처인지 확정된다. (2) **`pen_5bp` 렌즈**
— 좁은 손절 = 마진 체결이라 관통 요구에 특히 취약할 수 있다. 렌즈는 체결 집합을 바꾸므로
셀을 렌즈당 다시 빌드하고(WAN-154 §4 패턴), 가드는 그 위에서 재시퀀싱만 한다. **기존 5값 ×
`baseline`은 `wan197_guard_grid.csv`를 비트 재현한다**(가드는 후보와 무관한 재시퀀싱 축).

재현: `python -m backtest.wan197_guard_with_filter --timeframes 1h` →
`--timeframes 15m --append` → `--timeframes 4h --append`(요약만 재생성: `--from-csv`).
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import (
    IS_FRACTION,
    SEGMENT_IS,
    SEGMENT_OOS,
    MarketData,
)
from backtest.run import parse_date_ms
from backtest.wan95_zone_limit_report import NEW_SYMBOLS, apply_funding_proxy
from backtest.wan133_geometry_vs_selection import (
    MIN_TRADES_FOR_PNL,
    REPORTS_DIR,
    STOP_GUARD_FRACTION,
    _bare,
    _write_csv,
)
from backtest.wan152_selection_vs_geometry import _val, trade_stats
from backtest.wan154_stop_width_audit import GUARD_VALUES, ROUND_TRIP_COST
from backtest.zone_limit_backtest import _Candidate, build_zone_limit_candidates
from data.funding import FundingRateStore
from data.models import FundingRate
from strategy.models import ConfluenceParams

# --------------------------------------------------------------------------- #
# 상수
# --------------------------------------------------------------------------- #

LENS_PRIMARY = "baseline"
"""공식 렌즈(WAN-104/128) — 판정의 주 수치."""

LENS_PEN = "pen_5bp"
"""체결 보수화 렌즈(WAN-205 = 사용자 질문 2026-07-28). 주문가를 5bp **관통**해야 체결.
체결 집합을 바꾸므로 셀을 이 렌즈 파라미터로 **다시 빌드**한 뒤 이름을 넘긴다(WAN-154 §4 패턴).
좁은 손절 = 마진 체결이라 관통 요구에 특히 취약할 수 있다는 것이 이 축의 질문이다."""

LENSES: tuple[str, ...] = (LENS_PRIMARY, LENS_PEN)

#: WAN-205 촘촘 스윕 — WAN-197 거친 5값(0/0.22/0.30/0.33/0.55%)에 0.15/0.20/0.25%를 끼운다.
#: 순수 수익 정점이 0.22% 근처라(WAN-197) 0.3%가 최적 근처인지 확정하려면 그 사이를 촘촘히
#: 봐야 한다. 기존 5값은 그대로 있어 `baseline` 재시퀀싱이 `wan197_guard_grid.csv`를 비트
#: 재현한다(가드는 후보와 무관한 재시퀀싱 축 — 값을 추가해도 기존 값의 행은 안 움직인다).
FINE_GUARDS: tuple[float, ...] = tuple(sorted({*GUARD_VALUES, 0.0015, 0.0020, 0.0025}))

RET_EPS = 0.001
"""수익률 델타 ±0.1%p 미만은 「효과 없음」으로 읽는다 — 0을 어느 한쪽 부호로 세면
「무영향 + 이득」이 「TF에 갈린다」로 둔갑한다(WAN-115/120이 겪은 부호 함정)."""


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class GuardRow(BaseModel):
    """한 (심볼, TF, 구간, 가드)의 손익 — `default` 팔(필터 켜진 채택 엔진) 단독.

    후보 집합은 가드와 무관하다(가드는 시퀀싱에서만 걸린다) — `num_candidates`는 다섯 가드
    값이 같고, `num_trades`·수익·가드 탈락률만 가드를 따라 움직인다.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    guard: float
    lens: str = LENS_PRIMARY
    num_candidates: float
    num_trades: float
    total_return: float
    max_drawdown: float
    win_rate: float
    mean_net_r: float | None = None
    cost_r_median: float | None = None
    profit_factor: float | None = None
    cap_hit_rate: float | None = None
    effective_risk_mean: float | None = None
    guard_reject_rate: float | None = None
    """후보 중 가드에 걸리는(|진입−손절| < guard·진입가) 비율 — **후보 단위**의 결정적 값
    (시퀀싱 순서 무관). 필터 × 가드 정면 충돌(WAN-154 §3-B)의 크기."""


# --------------------------------------------------------------------------- #
# 펀딩 대리 · 후보 빌드
# --------------------------------------------------------------------------- #


def load_proxied_funding(
    symbols: Sequence[str], *, start_ms: int, end_ms: int, db_path: str
) -> tuple[dict[str, list[FundingRate]], str]:
    """요청 심볼 + 대리 도너 후보의 펀딩을 로드하고 신규 3종목에 대리를 얹는다.

    도너 후보(채택 유니버스의 비-신규 종목)를 함께 조회해, 신규 종목만 부분 실행해도
    같은 도너에서 같은 시계열을 받게 한다(wan95 `collect_rows`와 같은 규칙). 조회만 넓히고
    행은 요청 심볼만 만든다(펀딩 테이블은 작다).
    """
    store = FundingRateStore(db_path)
    donor_candidates = tuple(s for s in harness.LEGACY_NINE_SYMBOLS if s not in NEW_SYMBOLS)
    query_symbols = list(dict.fromkeys([*donor_candidates, *symbols]))
    funding_by_symbol = {
        s: store.get_rates(s, start_ms=start_ms, end_ms=end_ms, include_predicted=True)
        for s in query_symbols
    }
    return apply_funding_proxy(funding_by_symbol)


def production_candidates(market: MarketData, params: ConfluenceParams) -> list[_Candidate]:
    """필터 1.28이 켜진 채택 엔진의 지정가 후보(존 무효화 손절 = 채택 장벽).

    `stop_loss_override=None`이라 손절은 채택 기본값(오더블록 무효화 경계)이고, `params`가
    `max_zone_width_atr=1.28`을 들고 있어 엔진이 넓은 존을 후보 단계에서 걸러 낸다 — 이것이
    「필터 켜진 오늘 엔진」이고, 인자 없는 `backtest.run`이 시퀀싱하는 바로 그 집합이다.
    """
    obr = harness.detect_order_blocks(market)
    cfg = harness.build_config(market.timeframe)
    cands, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=obr,
        stop_loss_override=None,
    )
    return cands


def is_boundary_ms(htf_df: pd.DataFrame) -> int:
    """IS/OOS 시간 경계(전체창 IS_FRACTION 지점) — wan152 `build_cell`과 같은 식."""
    times = htf_df["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    return start + int((end - start) * IS_FRACTION)


def guard_reject_rate(cands: Sequence[_Candidate], guard: float) -> float | None:
    """후보 단위 가드 탈락률(결정적 — 시퀀싱 순서 무관)."""
    if not cands:
        return None
    below = sum(1 for c in cands if abs(c.entry_price - c.stop_price) < guard * c.entry_price)
    return below / len(cands)


def guard_rows_for_cell(
    market: MarketData,
    cands: Sequence[_Candidate],
    is_boundary: int,
    guards: Sequence[float],
    *,
    lens: str = LENS_PRIMARY,
) -> list[GuardRow]:
    """한 (심볼, TF, 렌즈)의 구간 × 가드 손익 행. 후보는 한 번 빌드하고 값마다 재시퀀싱한다.

    `lens`는 좌표 라벨일 뿐이다(WAN-152 규약) — 렌즈(`pen_5bp`)는 체결 집합을 바꾸므로
    호출부가 그 렌즈 파라미터로 빌드한 `cands`를 넘겨야 한다. 가드는 시퀀싱에서만 걸리므로
    그 위에서 값마다 재시퀀싱만 한다.
    """
    rows: list[GuardRow] = []
    for segment in (SEGMENT_IS, SEGMENT_OOS):
        seg = [c for c in cands if (c.trigger_time < is_boundary) == (segment == SEGMENT_IS)]
        for guard in guards:
            s = trade_stats(list(seg), market, market.timeframe, guard=guard)
            rows.append(
                GuardRow(
                    symbol=market.symbol,
                    timeframe=market.timeframe,
                    segment=segment,
                    guard=guard,
                    lens=lens,
                    num_candidates=float(len(seg)),
                    num_trades=float(s.num_trades),
                    total_return=s.total_return,
                    max_drawdown=s.max_drawdown,
                    win_rate=s.win_rate,
                    mean_net_r=s.mean_net_r,
                    cost_r_median=s.cost_r_median,
                    profit_factor=s.profit_factor,
                    cap_hit_rate=s.cap_hit_rate,
                    effective_risk_mean=s.effective_risk_mean,
                    guard_reject_rate=guard_reject_rate(seg, guard),
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass
class AuditResult:
    rows: list[GuardRow] = field(default_factory=list)
    pool_notes: list[str] = field(default_factory=list)
    funding_note: str = ""


def run_audit(
    *,
    symbols: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS,
    timeframes: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    guards: Sequence[float] = FINE_GUARDS,
    lenses: Sequence[str] = LENSES,
    db_path: str = harness.DB_PATH,
) -> AuditResult:
    """9심볼 × TF × 렌즈 2 × 가드 8값 — 필터 1.28 켠 채택 엔진.

    후보는 (심볼, TF, 렌즈)당 한 번 빌드한다 — 렌즈(`pen_5bp`)는 체결 집합을 바꾸므로 셀을
    다시 빌드하고(WAN-154 §4), 가드는 그 위에서 재시퀀싱만 한다. 두 렌즈 모두 존폭 필터
    1.28을 켠 채다(끄지 않는다 — 이 이슈는 「필터 켠 오늘 엔진」의 가드를 잰다).
    """
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    result = AuditResult()
    funding_by_symbol, funding_note = load_proxied_funding(
        symbols, start_ms=start_ms, end_ms=end_ms, db_path=db_path
    )
    result.funding_note = funding_note
    if funding_note:
        print(f"[wan197] {funding_note}", flush=True)
    # 렌즈별 채택 파라미터 — 필터 1.28은 UNSET 규약으로 그대로 물려받는다(끄지 않는다).
    params_by_lens = {
        LENS_PRIMARY: harness.build_params(),
        LENS_PEN: harness.build_params(fill=harness.fill_preset(LENS_PEN)),
    }
    for timeframe in timeframes:
        for symbol in symbols:
            # 6년 1분봉(심볼당 ~315만 행)은 두 심볼 몫이 겹치는 순간이 메모리 피크다 —
            # 9종목 직렬 실행이 그 지점에서 트레이스백 없이 죽은 적이 있다(wan95 주석).
            gc.collect()
            norm = harness.normalize_symbol(symbol)
            market = harness.load_market_data(
                norm,
                timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                need_1m=True,
                funding=False,
                db_path=db_path,
            )
            if market.empty or market.df_1m.empty:
                print(f"[wan197] {_bare(norm)} {timeframe}: 데이터 없음 — 건너뜀", flush=True)
                continue
            market = dataclasses.replace(
                market, funding_rates=funding_by_symbol.get(norm, market.funding_rates)
            )
            is_boundary = is_boundary_ms(market.htf_df)
            for lens in lenses:
                cands = production_candidates(market, params_by_lens[lens])
                result.rows.extend(
                    guard_rows_for_cell(market, cands, is_boundary, guards, lens=lens)
                )
                note = f"{_bare(norm)} {timeframe} [{lens}]: 필터 켠 후보 {len(cands)}개"
                result.pool_notes.append(note)
                print(f"[wan197] {note}", flush=True)
    return result


# --------------------------------------------------------------------------- #
# 집계
# --------------------------------------------------------------------------- #


def _rows(
    rows: Sequence[GuardRow],
    *,
    timeframe: str,
    segment: str,
    guard: float,
    lens: str = LENS_PRIMARY,
) -> list[GuardRow]:
    return [
        r
        for r in rows
        if r.timeframe == timeframe
        and r.segment == segment
        and r.lens == lens
        and abs(r.guard - guard) < 1e-12
    ]


def symbol_mean(
    rows: Sequence[GuardRow],
    *,
    timeframe: str,
    segment: str,
    guard: float,
    lens: str = LENS_PRIMARY,
) -> dict[str, float | None]:
    """심볼평균(수익·MDD·승률은 단순평균, 거래·후보는 합). 거래 20건 미만 셀은 제외한다."""
    sub_all = _rows(rows, timeframe=timeframe, segment=segment, guard=guard, lens=lens)
    excluded = [r for r in sub_all if r.num_trades < MIN_TRADES_FOR_PNL]
    sub = [r for r in sub_all if r.num_trades >= MIN_TRADES_FOR_PNL]
    if not sub:
        return {"n_symbols": 0.0, "n_excluded": float(len(excluded))}
    n = len(sub)

    def _mean(attr: str) -> float | None:
        vals = [v for v in (_val(getattr(r, attr)) for r in sub) if v is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "total_return": sum(r.total_return for r in sub) / n,
        "max_drawdown": sum(r.max_drawdown for r in sub) / n,
        "win_rate": sum(r.win_rate for r in sub) / n,
        "num_trades": sum(r.num_trades for r in sub),
        "num_candidates": sum(r.num_candidates for r in sub),
        "mean_net_r": _mean("mean_net_r"),
        "cost_r_median": _mean("cost_r_median"),
        "profit_factor": _mean("profit_factor"),
        "cap_hit_rate": _mean("cap_hit_rate"),
        "effective_risk_mean": _mean("effective_risk_mean"),
        "guard_reject_rate": _mean("guard_reject_rate"),
        "n_symbols": float(n),
        "n_excluded": float(len(excluded)),
    }


def leave_one_out(
    rows: Sequence[GuardRow],
    *,
    timeframe: str,
    guard: float,
    segment: str = SEGMENT_OOS,
    lens: str = LENS_PRIMARY,
) -> str:
    """심볼 하나씩 빼고 본 total_return 심볼평균 — 편중 확인."""
    sub = [
        r
        for r in _rows(rows, timeframe=timeframe, segment=segment, guard=guard, lens=lens)
        if r.num_trades >= MIN_TRADES_FOR_PNL
    ]
    if len(sub) < 2:
        return "표본 부족"
    parts: list[str] = []
    for drop in sub:
        rest = [r.total_return for r in sub if r.symbol != drop.symbol]
        parts.append(f"−{_bare(drop.symbol)} {sum(rest) / len(rest) * 100:+.2f}%")
    return " · ".join(parts)


def collapsed_cells(
    rows: Sequence[GuardRow], *, guard: float, lens: str = LENS_PRIMARY
) -> list[str]:
    """가드 `guard`에서 거래 20건 미만으로 무너진 (TF, 구간, 심볼) 셀."""
    return [
        f"`{r.timeframe}` {r.segment} {_bare(r.symbol)}({r.num_trades:.0f}거래)"
        for r in rows
        if r.lens == lens and abs(r.guard - guard) < 1e-12 and r.num_trades < MIN_TRADES_FOR_PNL
    ]


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


class GuardKind(StrEnum):
    """가드(0.3%) 효과 — **문장이 아니라 이 값이 정본이다**(WAN-142가 열거형으로 고친 사고)."""

    BENEFIT = "benefit"  # (a) 이득
    HARM = "harm"  # (b) 손해
    NEUTRAL = "neutral"  # (c) 무영향 또는 TF·종목에 갈림
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class Judgement:
    kind: GuardKind
    text: str

    def __str__(self) -> str:
        return self.text


def _ra(m: dict[str, float | None]) -> float | None:
    ret, mdd = m.get("total_return"), m.get("max_drawdown")
    if ret is None or mdd is None:
        return None
    return ret / mdd if mdd > 0 else 0.0


def guard_verdict(
    rows: Sequence[GuardRow], *, timeframe: str, lens: str = LENS_PRIMARY
) -> Judgement:
    """한 TF에서 가드(0.3%)가 (a) 이득 / (b) 손해 / (c) 중립·갈림인가. OOS `default` 팔.

    분모는 가드 0%(끔). `total_return`만으로 내지 않는다 — 수익/MDD(위험조정)를 함께 본다
    (가드는 수익 장치가 아니라 신뢰성 장치라는 WAN-76 취지).
    """
    on = symbol_mean(
        rows, timeframe=timeframe, segment=SEGMENT_OOS, guard=STOP_GUARD_FRACTION, lens=lens
    )
    off = symbol_mean(rows, timeframe=timeframe, segment=SEGMENT_OOS, guard=0.0, lens=lens)
    if (on.get("n_symbols") or 0.0) < 3 or (off.get("n_symbols") or 0.0) < 3:
        return Judgement(
            GuardKind.INDETERMINATE,
            f"**{timeframe}**: ⚠️ 판정 불가(대조군) — 유효 심볼이 가드 0.3% "
            f"{on.get('n_symbols', 0.0):.0f}개 · 끔 {off.get('n_symbols', 0.0):.0f}개뿐이다"
            f"(거래 {MIN_TRADES_FOR_PNL}건 미달 제외).",
        )
    ret_on, ret_off = on["total_return"], off["total_return"]
    ra_on, ra_off = _ra(on), _ra(off)
    assert ret_on is not None and ret_off is not None
    assert ra_on is not None and ra_off is not None
    d_ret = ret_on - ret_off
    detail = (
        f"수익 {ret_off * 100:+.2f}%(끔) → {ret_on * 100:+.2f}%(0.3%) Δ{d_ret * 100:+.2f}%p · "
        f"수익/MDD {ra_off:.2f} → {ra_on:.2f} · 유효 심볼 {on['n_symbols']:.0f}"
    )
    if abs(d_ret) < RET_EPS and abs(ra_on - ra_off) < 0.02:
        return Judgement(
            GuardKind.NEUTRAL,
            f"**{timeframe}**: **(c) 중립(무영향)** — 가드를 끄나 켜나 사실상 같다({detail}). "
            "필터가 이미 좁은 존만 남겨 가드에 걸리는 거래가 적다는 뜻이다.",
        )
    if d_ret > RET_EPS and ra_on >= ra_off - 1e-9:
        return Judgement(
            GuardKind.BENEFIT,
            f"**{timeframe}**: **(a) 가드가 이득** — 수익도 위험조정도 내려가지 않는다({detail}).",
        )
    if d_ret < -RET_EPS and ra_on <= ra_off + 1e-9:
        return Judgement(
            GuardKind.HARM,
            f"**{timeframe}**: **(b) 가드가 손해** — 수익도 위험조정도 오르지 않는다({detail}).",
        )
    return Judgement(
        GuardKind.NEUTRAL,
        f"**{timeframe}**: **(c) 중립(방향 갈림)** — 수익과 위험조정의 방향이 다르다({detail}). "
        "가드는 수익 장치가 아니라 신뢰성 장치라는 WAN-76 취지 그대로다.",
    )


def best_guard(
    rows: Sequence[GuardRow],
    *,
    timeframe: str,
    segment: str,
    guards: Sequence[float],
    metric: str,
    lens: str = LENS_PRIMARY,
) -> tuple[float, float] | None:
    """`metric`("return" = 순수 수익 · "risk_adjusted" = 수익/MDD)을 최대화하는 가드와 그 값.

    유효 심볼 3개 미만인 가드 값은 후보에서 뺀다(표본 게이트). 두 값이 같으면 **작은 가드**를
    고른다(신뢰성 장치를 최소로 — 동률에서 굳이 더 조이지 않는다). 없으면 `None`.
    """
    best: tuple[float, float] | None = None
    for guard in guards:
        m = symbol_mean(rows, timeframe=timeframe, segment=segment, guard=guard, lens=lens)
        if (m.get("n_symbols") or 0.0) < 3:
            continue
        score = _ra(m) if metric == "risk_adjusted" else m.get("total_return")
        if score is None:
            continue
        if best is None or score > best[1] + 1e-12:
            best = (guard, score)
    return best


def optimal_guard_note(
    rows: Sequence[GuardRow],
    *,
    timeframe: str,
    guards: Sequence[float],
    lens: str = LENS_PRIMARY,
    segment: str = SEGMENT_OOS,
) -> str:
    """한 (TF, 렌즈, 구간)의 순수 수익 정점 가드 · 위험조정 정점 가드 + 현행 0.3%가 고원인지."""
    by_ret = best_guard(
        rows, timeframe=timeframe, segment=segment, guards=guards, metric="return", lens=lens
    )
    by_ra = best_guard(
        rows,
        timeframe=timeframe,
        segment=segment,
        guards=guards,
        metric="risk_adjusted",
        lens=lens,
    )
    cur = symbol_mean(
        rows, timeframe=timeframe, segment=segment, guard=STOP_GUARD_FRACTION, lens=lens
    )
    if by_ret is None or by_ra is None or not cur.get("n_symbols"):
        return f"{timeframe} `{lens}` {segment}: ⚠️ 판정 불가(유효 표본 부족)"
    cur_ra = _ra(cur)
    ra_best = by_ra[1]
    plateau = (
        cur_ra is not None
        and ra_best is not None
        and ra_best > 0
        and (ra_best - cur_ra) / ra_best <= 0.05
    )
    plateau_txt = "0.3%는 위험조정 고원 안" if plateau else "0.3%는 위험조정 고원 **밖**"
    return (
        f"{timeframe} `{lens}` {segment}: 순수 수익 정점 **{by_ret[0] * 100:.2f}%**"
        f"({by_ret[1] * 100:+.2f}%) · 위험조정 정점 **{by_ra[0] * 100:.2f}%**({ra_best:.2f}) · "
        f"현행 0.3% 위험조정 {_ra_txt(cur)} → {plateau_txt}"
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _pct(value: float | None, *, signed: bool = False, digits: int = 2) -> str:
    v = _val(value)
    if v is None:
        return "—"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def _num(value: float | None, *, digits: int = 3, signed: bool = True) -> str:
    v = _val(value)
    if v is None:
        return "—"
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def _ra_txt(m: dict[str, float | None]) -> str:
    ra = _ra(m)
    return "—" if ra is None else f"{ra:.2f}"


def build_summary_markdown(result: AuditResult, *, timeframes: Sequence[str]) -> str:
    rows = result.rows
    symbols = sorted({_bare(r.symbol) for r in rows}) or ["—"]
    lines: list[str] = []
    lines.append(
        "# WAN-205 손절폭 가드 촘촘 스윕 + `pen_5bp` — 존폭 필터 1.28 켠 오늘 엔진 (WAN-197 확장)\n"
    )
    lines.append(
        f"{len(symbols)}심볼({'/'.join(symbols)}) × {'·'.join(timeframes)}, 못 박은 창 "
        f"**{harness.DEFAULT_START} ~ {harness.DEFAULT_END}**(WAN-182), **오늘의 채택 기본값**"
        "(`ConfluenceParams()` — 오프셋 2bp · `intrabar_live` 밴드 · `unconditional` 게이트 · "
        "고정 1.5R · 롱 온리 · `combine_obs=False` · **`max_zone_width_atr=1.28`(필터 켜짐)**).\n"
    )
    guard_txt = " · ".join(f"{g * 100:.2f}%" for g in FINE_GUARDS)
    lines.append(
        f"가드 축({len(FINE_GUARDS)}값): {guard_txt} — 0%는 판정의 분모(끔), **0.3%가 현행"
        f"(WAN-79)**. WAN-197의 거친 5값(0/0.22/0.30/0.33/0.55%, 왕복 비용 "
        f"{ROUND_TRIP_COST:.2%}의 배수)에 **0.15/0.20/0.25%를 끼운 촘촘 스윕**이다(WAN-205 — "
        "순수 수익 정점이 0.22% 근처라 그 사이를 봐야 0.3%가 최적 근처인지 확정된다). "
        "가드는 시퀀싱(`position_size`)에서만 걸리므로 후보를 렌즈당 한 번만 빌드하고 값마다 "
        "재시퀀싱만 한다.\n"
    )
    lines.append(
        "렌즈 축(2): 공식 `baseline`(닿으면 체결, 주 수치) + **`pen_5bp`**(주문가 5bp 관통 "
        "요구, WAN-205). `pen_5bp`는 체결 집합을 바꾸므로 셀을 다시 빌드한다 — 좁은 손절 = "
        "마진 체결이라 관통 요구에 특히 취약할 수 있다는 것이 이 축의 질문이다(WAN-128 이후 "
        "옵트인 민감도 병기).\n"
    )
    lines.append(
        "🚨 **WAN-154 §3-B와의 차이**: 그 표는 필터를 **끈 채**(엔진 필터 off + 존폭 필터를 "
        "후보로 직접 구성) 잰 것이고, 이 표는 **엔진 필터 1.28을 켠** 채택 엔진의 `default` "
        "팔에서 가드만 스윕한다 — WAN-159가 필터를 채택 기본값으로 올린 뒤의 실제 매매 엔진이다. "
        "매칭 대조군·세 장벽은 이 이슈 범위 밖이라 없다(축은 가드 하나).\n"
    )
    if result.funding_note:
        lines.append(f"📌 **{result.funding_note}**\n")
    lines.append(
        "재현: `python -m backtest.wan197_guard_with_filter --timeframes 1h` → "
        "`--timeframes 15m --append` → `--timeframes 4h --append`.\n"
    )

    lines.append("## 판정 — 필터 켠 상태에서 가드(0.3%)가 이득인가 (OOS · `default` 팔)\n")
    present_lenses = [lens for lens in LENSES if any(r.lens == lens for r in rows)]
    for lens in present_lenses:
        lines.append(f"**렌즈 `{lens}`:**\n")
        for timeframe in timeframes:
            lines.append(f"* {guard_verdict(rows, timeframe=timeframe, lens=lens).text}")
        lines.append("")

    lines.append("## 촘촘 스윕 판정 — 위험조정 최적 가드 · 순수 수익 정점 (OOS)\n")
    lines.append(
        "「위험조정 정점」 = 수익/MDD 최대 가드, 「순수 수익 정점」 = total_return 최대 가드"
        "(유효 심볼 3개 미만 가드 값은 제외). 현행 0.3%가 위험조정 고원(정점 대비 5% 이내) "
        "안인지 병기한다.\n"
    )
    for lens in present_lenses:
        for timeframe in timeframes:
            lines.append(
                f"* {optimal_guard_note(rows, timeframe=timeframe, guards=FINE_GUARDS, lens=lens)}"
            )
    lines.append("")

    lines.append(_pool_section(result))
    lines.append(_metrics_section(result, timeframes))
    lines.append(_symbol_section(result, timeframes))
    lines.append("## 결론\n")
    lines.append(_conclusion(result, timeframes))
    return "\n".join(lines)


def _pool_section(result: AuditResult) -> str:
    lines = ["## 필터 켠 후보 수 (심볼 × TF)\n"]
    for note in result.pool_notes:
        lines.append(f"* {note}")
    lines.append("")
    return "\n".join(lines)


def _metrics_section(result: AuditResult, timeframes: Sequence[str]) -> str:
    present_lenses = [lens for lens in LENSES if any(r.lens == lens for r in result.rows)]
    lines = ["## 가드 × 구간 × 렌즈 심볼평균 (`default` 팔 · 필터 1.28)\n"]
    lines.append(
        f"⚠️ 심볼평균은 거래 {MIN_TRADES_FOR_PNL}건 미만 셀을 제외한다(유효/제외 심볼 병기). "
        "`mean_net_r` = 거래당 (실현 손익 ÷ 리스크 금액), 수수료·슬리피지·펀딩 반영 후. "
        "가드 탈락률 = 후보 중 |진입−손절| < guard·진입가인 비율.\n"
    )
    lines.append(
        "| TF | 구간 | 렌즈 | 가드 | 유효(제외) | 거래 | total_return | MDD | 수익/MDD | 승률 | "
        "mean_net_r | cost_r | PF | 가드 탈락률 |\n" + "| -- " * 14 + "|"
    )
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for lens in present_lenses:
                for guard in FINE_GUARDS:
                    m = symbol_mean(
                        result.rows, timeframe=timeframe, segment=segment, guard=guard, lens=lens
                    )
                    if not m.get("n_symbols"):
                        continue
                    mark = " ←현행" if abs(guard - STOP_GUARD_FRACTION) < 1e-12 else ""
                    nt = m.get("num_trades")
                    lines.append(
                        f"| {timeframe} | {segment} | `{lens}` | {guard * 100:.2f}%{mark} | "
                        f"{m['n_symbols']:.0f}({m['n_excluded']:.0f}) | "
                        f"{'—' if nt is None else f'{nt:.0f}'} | "
                        f"{_pct(m.get('total_return'), signed=True)} | "
                        f"{_pct(m.get('max_drawdown'))} | "
                        f"{_ra_txt(m)} | {_pct(m.get('win_rate'))} | {_num(m.get('mean_net_r'))} | "
                        f"{_num(m.get('cost_r_median'), signed=False)} | "
                        f"{_num(m.get('profit_factor'), signed=False, digits=2)} | "
                        f"{_pct(m.get('guard_reject_rate'), digits=1)} |"
                    )
    lines.append("")
    lines.append("**심볼 편중(OOS `default` 팔 leave-one-out · 가드 0.3% vs 끔):**\n")
    for lens in present_lenses:
        for timeframe in timeframes:
            on = leave_one_out(
                result.rows, timeframe=timeframe, guard=STOP_GUARD_FRACTION, lens=lens
            )
            off = leave_one_out(result.rows, timeframe=timeframe, guard=0.0, lens=lens)
            lines.append(f"* {timeframe} `{lens}` 가드 0.3%: {on}")
            lines.append(f"* {timeframe} `{lens}` 가드 끔: {off}")
    lines.append("")
    for lens in present_lenses:
        collapse = collapsed_cells(result.rows, guard=STOP_GUARD_FRACTION, lens=lens)
        lines.append(
            f"**`{lens}` 가드 0.3%에서 유효 표본(거래 {MIN_TRADES_FOR_PNL}건) 붕괴 셀:** "
            + (" · ".join(collapse) if collapse else "없음")
            + " (WAN-154/155/161의 TRX 충돌 재현 여부)"
        )
    lines.append("")
    return "\n".join(lines)


def _symbol_section(result: AuditResult, timeframes: Sequence[str]) -> str:
    lines = ["## 종목별 가드 켜고/끈 수익 (OOS · `default` 팔 · `baseline` 렌즈)\n"]
    lines.append(
        "「종목에 갈리는가」를 보기 위한 종목 단위 대조다(공식 렌즈 `baseline`). 거래 수는 "
        "가드 0.3% 기준(끔은 후보 전부 시퀀싱). 🚨 = 가드 0.3%에서 거래 20건 미달.\n"
    )
    lines.append(
        "| TF | 심볼 | 후보 | 거래(끔→0.3%) | 수익(끔) | 수익(0.3%) | Δ수익 | "
        "MDD(끔→0.3%) | 가드 탈락률 |\n" + "| -- " * 9 + "|"
    )
    for timeframe in timeframes:
        symbols = sorted(
            {
                r.symbol
                for r in result.rows
                if r.timeframe == timeframe and r.segment == SEGMENT_OOS and r.lens == LENS_PRIMARY
            }
        )
        for symbol in symbols:
            off = next(
                (
                    r
                    for r in result.rows
                    if r.symbol == symbol
                    and r.timeframe == timeframe
                    and r.segment == SEGMENT_OOS
                    and r.lens == LENS_PRIMARY
                    and abs(r.guard - 0.0) < 1e-12
                ),
                None,
            )
            on = next(
                (
                    r
                    for r in result.rows
                    if r.symbol == symbol
                    and r.timeframe == timeframe
                    and r.segment == SEGMENT_OOS
                    and r.lens == LENS_PRIMARY
                    and abs(r.guard - STOP_GUARD_FRACTION) < 1e-12
                ),
                None,
            )
            if off is None or on is None:
                continue
            mark = " 🚨" if on.num_trades < MIN_TRADES_FOR_PNL else ""
            d = on.total_return - off.total_return
            lines.append(
                f"| {timeframe} | {_bare(symbol)} | {on.num_candidates:.0f} | "
                f"{off.num_trades:.0f}→{on.num_trades:.0f}{mark} | "
                f"{_pct(off.total_return, signed=True)} | {_pct(on.total_return, signed=True)} | "
                f"{d * 100:+.2f}%p | {_pct(off.max_drawdown)}→{_pct(on.max_drawdown)} | "
                f"{_pct(on.guard_reject_rate, digits=1)} |"
            )
    lines.append("")
    return "\n".join(lines)


def _conclusion(result: AuditResult, timeframes: Sequence[str]) -> str:
    verdicts = [guard_verdict(result.rows, timeframe=tf, lens=LENS_PRIMARY) for tf in timeframes]
    kinds = {v.kind for v in verdicts if v.kind != GuardKind.INDETERMINATE}
    if kinds == {GuardKind.BENEFIT}:
        head = "**필터 켠 오늘 엔진에서도 가드(0.3%)는 (a) 이득이다 — 판정 TF 전부(`baseline`).**"
    elif kinds == {GuardKind.HARM}:
        head = "**필터 켠 오늘 엔진에서 가드(0.3%)는 (b) 손해다 — 판정 TF 전부(`baseline`).**"
    elif GuardKind.BENEFIT in kinds and (GuardKind.HARM in kinds or GuardKind.NEUTRAL in kinds):
        head = "**(c) TF에 갈린다 — 가드 효과가 TF마다 다르다(`baseline` · 판정 모음이 정본).**"
    elif kinds == {GuardKind.NEUTRAL}:
        head = "**(c) 중립 — 필터가 이미 좁은 존만 남겨 가드에 걸리는 거래가 적다(`baseline`).**"
    else:
        head = "**판정 모음이 정본이다(일부 TF는 표본 게이트로 판정 불가).**"

    has_pen = any(r.lens == LENS_PEN for r in result.rows)
    pen_txt = ""
    if has_pen:
        base_v = {
            tf: guard_verdict(result.rows, timeframe=tf, lens=LENS_PRIMARY).kind
            for tf in timeframes
        }
        pen_v = {
            tf: guard_verdict(result.rows, timeframe=tf, lens=LENS_PEN).kind for tf in timeframes
        }
        # (b)손해 < (c)중립 < (a)이득 — pen 아래서 가드 판정이 좋아졌는지/나빠졌는지로
        # 방향을 읽는다(판정 종류가 바뀐 방향을 잘못 해석하지 않도록).
        rank = {GuardKind.HARM: 0, GuardKind.NEUTRAL: 1, GuardKind.BENEFIT: 2}
        labels = {
            GuardKind.HARM: "(b)손해",
            GuardKind.NEUTRAL: "(c)중립",
            GuardKind.BENEFIT: "(a)이득",
        }
        shifted = [
            tf
            for tf in timeframes
            if base_v[tf] != pen_v[tf] and base_v[tf] in rank and pen_v[tf] in rank
        ]
        if shifted:
            parts = [f"{tf} ({labels[base_v[tf]]}→{labels[pen_v[tf]]})" for tf in shifted]
            worsened = any(rank[pen_v[tf]] < rank[base_v[tf]] for tf in shifted)
            improved = any(rank[pen_v[tf]] > rank[base_v[tf]] for tf in shifted)
            if worsened and not improved:
                read = "가드 우위가 관통 요구에 무너진다는 신호(좁은 손절이 마진 체결이라 취약)"
            elif improved and not worsened:
                read = (
                    "가드가 관통 요구 아래서 **오히려 더 유용해진다** — 가드가 걷어내는 좁은 "
                    "손절이 곧 관통에 가장 취약한 마진 체결이라, 그걸 미리 버린 팔이 덜 다친다"
                    "(무너지는 반대 방향이 아니다)"
                )
            else:
                read = "TF마다 방향이 갈린다"
            pen_txt = (
                f" 📌 **`pen_5bp`에서 판정 종류가 바뀌는 TF**: {', '.join(parts)} — {read}"
                "(위 「판정」의 `pen_5bp` 블록이 정본)."
            )
        else:
            pen_txt = (
                " 📌 **`pen_5bp`에서도 TF별 판정 종류는 그대로다** — 두 팔이 같이 깎이지 "
                "가드 우위의 부호가 뒤집히지 않는다(위 「촘촘 스윕 판정」의 렌즈별 정점 병기가 "
                "정본)."
            )
    return (
        head + pen_txt + " 각 TF 판정 문장은 위 「판정」이 정본이다.\n\n"
        "🚨 **「엣지 찾았다」로 인용 금지** — `baseline`은 닿으면 체결(낙관)이고 `pen_5bp`는 "
        "옵트인 민감도 병기다. 「엣지 없음」(WAN-84/88/111/114/124/151)은 다른 질문이라 "
        "뒤집히지 않는다. ⚠️ **측정 전용 · 기본값 변경 아님** — 가드 기본값(0.3%) 전환은 "
        "WAN-76/79 소관이고 재-베이스라인 = 사용자 결정이다(`ConfluenceParams()` 불변, "
        "`min_stop_distance_fraction=0.003` 불변, 실거래 보류 `ALPHABLOCK_LIVE_TRADING=false` "
        "유지). ⚠️ 순수 수익 argmax만 보고 「0.3%는 틀렸다」로 인용 금지 — 위험조정은 고원이라 "
        "0.3%가 나쁜 선택이 아니다(WAN-90/161 계열 IS→OOS 뒤집힘 주의). ⚠️ 4h는 표본 게이트로 "
        "대조군이다. ⚠️ WAN-76/79 원 감사 수치는 옛 엔진이라 이 표와 섞지 말 것."
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _load_rows(path: Path) -> list[GuardRow]:
    if not path.exists():
        return []
    return [GuardRow.model_validate(rec) for rec in pd.read_csv(path).to_dict("records")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-197 손절폭 가드 × 존폭 필터 재측정")
    parser.add_argument("--symbols", type=str, default=",".join(harness.LEGACY_NINE_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=harness.DEFAULT_START)
    parser.add_argument("--end", type=str, default=harness.DEFAULT_END)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--rows-out", type=Path, default=REPORTS_DIR / "wan197_guard_grid.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan197_summary.md")
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 붙인다(TF 분할).")
    parser.add_argument(
        "--from-csv", action="store_true", help="격자를 다시 돌리지 않고 CSV로 요약만 재생성."
    )
    args = parser.parse_args(argv)

    funding_note = ""
    if args.from_csv:
        result = AuditResult(rows=_load_rows(args.rows_out))
    else:
        result = run_audit(
            symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
            timeframes=tuple(t.strip() for t in args.timeframes.split(",") if t.strip()),
            start=args.start,
            end=args.end,
            db_path=args.db,
        )
        funding_note = result.funding_note
        frame = pd.DataFrame([r.model_dump() for r in result.rows])
        if args.append and args.rows_out.exists():
            frame = pd.concat([pd.read_csv(args.rows_out), frame], ignore_index=True)
        _write_csv(frame, args.rows_out)
        print(f"[wan197] rows → {args.rows_out}")
        # 이어 붙였으면 요약은 합친 표 위에서 낸다(풀 메모·펀딩 각주는 이번 실행 몫만 남는다).
        result = AuditResult(
            rows=_load_rows(args.rows_out),
            pool_notes=result.pool_notes,
            funding_note=funding_note,
        )

    timeframes = tuple(dict.fromkeys(r.timeframe for r in result.rows))
    summary = build_summary_markdown(result, timeframes=timeframes)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan197] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
