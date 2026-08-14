"""즉사/손절 부검의 남은 세 축 — 거래량 · 상위TF 장세 · 볼린저 하단 기울기 (WAN-209).

WAN-150(즉사 부검)이 오늘 엔진에서 만든 3분류 라벨(즉사/애매/승자)에, WAN-117/150 검정 틀이
아직 안 본 **세 후보 축**을 얹어 "진입 시점에 즉사가 보이는가"를 더 넓게 재확인한다. **새
백테스트/새 파이프라인을 만들지 않는다**(WAN-101) — WAN-150의 라벨링 엔진(`build_zone_limit
_candidates` + 프로덕션 시퀀서)을 그대로 재사용하고 **특징 열만 얹는다**. 시퀀서가 낸 거래·
라벨·공유 특징(`zone_width_atr`·`stop_width_atr`·`volume_pctl`·…)은 WAN-150과 비트 동일해야
하며(회귀 테스트 + `--checksum`이 동작으로 고정), 새 축만 추가된다.

## 토대 (WAN-150과 동일 — 핀 하나도 안 건다)

분리 존(`combine_obs=False`) · `intrabar_live` 밴드 · 존폭 필터 1.28 · 오프셋 2bp ·
`unconditional` 게이트 · 고정 1.5R · 롱 온리 = **핀 없는 `ConfluenceParams()`·
`OrderBlockParams()`**. 9종목 · 못 박은 6년 창(2020-09-15~2026-07-22) · 15m·1h·4h · 렌즈
`baseline` 단독. **측정 전용 — 기본값·토대·실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`)는
건드리지 않는다.**

## §A — 거래량 축 (구 WAN-136)

형성 봉(`OrderBlock.start_time`)의 **상대거래량** `rvol_smaN = 형성봉 거래량 / SMA(거래량, N)`
(N=20·50)을 즉사와 대조한다. WAN-150 라벨에 이미 있는 `volume_pctl`(셀 내부 `ob_volume`
순위)은 삭제 없이 병기한다. 가설: **한산한 봉(낮은 RVOL)에서 생긴 존이 약해 즉사가 잦다**
(사용자 관찰). 문턱 스윕(0.6/0.8/1.0/1.2)은 IS에서 고르고 OOS로 검증(`threshold_sweep`).

## §B — 상위TF 장세 축 (구 WAN-139)

진입 시각에 **상위TF의 확정된 봉까지만** 보고(4h 진행 중 봉 종가 금지 — 룩어헤드) 4가지
장세 특징을 읽는다. 후보 4축을 **착수 전 확정**한다(IS 1등 뽑기 금지):

* `reg_{htf}_trend` = `close/ema200 − 1`(상위TF 확정봉) — 추세 방향(음수 = EMA 아래 = 하락).
* `reg_{htf}_ema_slope` = `(ema200[i]−ema200[i−K])/close`(K=10) — EMA200 기울기.
* `reg_{htf}_vol_pctl` = `atr/close` 셀 내부 순위 백분위 — 변동성 분위.
* `reg_{htf}_dev_pctl` = `|close/ema200 − 1|` 셀 내부 순위 백분위 — 이격(추세 이탈) 분위.

상위TF는 진입 TF보다 **엄격히 큰** {4h, 1d}만: 15m→4h·1d, 1h→4h·1d, 4h→1d. 진입 TF 이하인
칸은 그 특징이 None이다(예: 4h 셀의 `reg_4h_*`).

## §C — 볼린저밴드 하단 기울기 축 (사용자 요청 2026-07-30)

탭 직전 확정봉 기준 하단 밴드 `lower = SMA20(close) − 2·stdev20(close)`(닫힌 봉 → 룩어헤드
구조적으로 없음)의 기울기를 두 창(3·5봉) × 두 척도(÷ATR · 가격%)로 낸다:
`band_lower_slope_{k}_{atr|pct}`. 가설: **하단 밴드가 가파르게 내려갈 때(음의 기울기) 롱
진입하면 "떨어지는 칼 잡기"라 즉사가 잦다**(σ 확장 + 하락 모멘텀).

## 검정 틀 · 판정

WAN-117/150 골격 그대로: 심볼 층화 라벨 순열 2,000회 + 축별 Bonferroni + OOS 유의 & IS 동일
부호 + 유효 20건 + 실무 문턱 열(즉사 대 승자) + leave-one-out(심볼 편중). 축별로 판정 문장을
**따로** 낸다:

* (a) 보인다 → 후속 필터 이슈 제안(사용자 결정) / (b) 안 보인다 → 닫는다 /
  (c) 보이지만 기하 → WAN-133/152 계열.
* 🚨 **존폭/손절폭 통제(독립성) 관문** — 세 축 모두 `zone_width_atr`/`stop_width_atr`의
  대리변수일 공산이 크다(WAN-150이 그 기하 축 하나만 강건). **공선성 표**(corr(특징,
  존폭/손절폭))를 전 특징에 병기하고, 주 검정을 통과한 특징에 한해 **부분상관 통제 순열**
  (존폭·손절폭을 회귀로 잔차화한 뒤 즉사와의 잔차 상관을 순열 검정)을 돌려 (a)독립 대 (c)기하를
  가른다. 통과 특징이 없으면 통제 순열은 돌릴 대상이 없다(공선성 표만 참고로 남긴다).

⚠️ 어느 판정이든 「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지 —
다른 질문(*이미 진입한 손절 중 즉사를 진입 시점에 알아보는가*)이다. 전부 `baseline`(낙관) 렌즈
위의 값 · 존폭 축 체결 보수화(`pen_5bp`)는 안 쟀다. §C는 볼린저가 진입가를 만드는 도구
자체이므로(WAN-131: 기여의 84%가 선별 아닌 가격) "선별 축을 찾았다"로 인용 금지.

재현: `python -m backtest.wan209_death_autopsy_axes`(검산만: `--checksum`).
"""

from __future__ import annotations

import argparse
import bisect
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import IS_FRACTION, SEGMENT_IS, SEGMENT_OOS
from backtest.models import ExitReason, PositionSide
from backtest.run import parse_date_ms
from backtest.wan117_zone_failure_autopsy import (
    _isnan,
    _safe_div,
    bonferroni_alpha,
    harness_prepare,
)
from backtest.wan150_instant_death_autopsy import (
    _DEATH_VS_REST,
    _DEATH_VS_WINNER,
    Axis,
    Label,
    LabeledTrade,
    LabelStats,
    LeaveOneOutRow,
    PermutationRow,
    QuantileRow,
    _corr,
    _Wan150Extractor,
    classify,
    leave_one_out,
    permutation_test,
    quantile_rows,
)
from backtest.zone_limit_backtest import (
    _Candidate,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.indicators import atr, ema, sma, stdev
from strategy.models import ConfluenceParams, OrderBlockParams

# --------------------------------------------------------------------------- #
# 상수 — 오늘 엔진 좌표 (WAN-150과 동일)
# --------------------------------------------------------------------------- #

#: WAN-307이 기본 유니버스를 12종목으로 옮겼다 — 이 리포트의 결론·CSV는 9종목 좌표라
#: 당시 값으로 명시 고정한다(고정 원칙은 `harness.LEGACY_NINE_SYMBOLS` 문서 참고).
DEFAULT_SYMBOLS: tuple[str, ...] = harness.LEGACY_NINE_SYMBOLS
DEFAULT_TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

_MIN_TRADES_FOR_VERDICT = 20
_PERMUTATIONS = 2000
_ALPHA = 0.05
_PRACTICAL_ALPHA = 0.05
_SEED = 209

REPORTS_DIR = Path("backtest/reports")

# --- §A 거래량 --- #
_RVOL_MA_LENGTHS: tuple[int, ...] = (20, 50)
_RVOL_THRESHOLDS: tuple[float, ...] = (0.6, 0.8, 1.0, 1.2)
S_A_FEATURES: tuple[str, ...] = tuple(f"rvol_sma{n}" for n in _RVOL_MA_LENGTHS)

# --- §B 상위TF 장세 --- #
_REGIME_HTFS: tuple[str, ...] = ("4h", "1d")
_REGIME_EMA = 200
_REGIME_SLOPE_K = 10
_REGIME_ATR = 14
_TF_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
_REGIME_AXES: tuple[str, ...] = ("trend", "ema_slope", "vol_pctl", "dev_pctl")
S_B_FEATURES: tuple[str, ...] = tuple(
    f"reg_{htf}_{axis}" for htf in _REGIME_HTFS for axis in _REGIME_AXES
)
#: `_pctl` 계열은 셀 내부 순위로 채운다(raw 키 → 최종 키).
_S_B_PCTL_RAW: dict[str, str] = {
    f"_raw_reg_{htf}_{axis}": f"reg_{htf}_{axis}"
    for htf in _REGIME_HTFS
    for axis in ("vol_pctl", "dev_pctl")
}

# --- §C 볼린저 하단 기울기 --- #
_BAND_SMA = 20
_BAND_STD = 2.0
_BAND_SLOPE_WINDOWS: tuple[int, ...] = (3, 5)
S_C_FEATURES: tuple[str, ...] = tuple(
    f"band_lower_slope_{k}_{scale}" for k in _BAND_SLOPE_WINDOWS for scale in ("atr", "pct")
)

FEATURES: tuple[str, ...] = (*S_A_FEATURES, *S_B_FEATURES, *S_C_FEATURES)

#: corr(특징, 즉사)>0 = 특징이 클수록 더 즉사. 가설 부호(판정에 안 쓰이고 표기 전용).
HYPOTHESIS_SIGN: dict[str, int] = {}
for _n in _RVOL_MA_LENGTHS:
    HYPOTHESIS_SIGN[f"rvol_sma{_n}"] = -1  # 한산한 봉(낮은 RVOL)에서 생긴 존이 약하다.
for _htf in _REGIME_HTFS:
    HYPOTHESIS_SIGN[f"reg_{_htf}_trend"] = -1  # EMA 아래(하락 추세)일수록 즉사.
    HYPOTHESIS_SIGN[f"reg_{_htf}_ema_slope"] = -1  # 하락 기울기일수록 즉사.
    HYPOTHESIS_SIGN[f"reg_{_htf}_vol_pctl"] = +1  # 고변동 장세일수록 즉사.
    HYPOTHESIS_SIGN[f"reg_{_htf}_dev_pctl"] = +1  # 이격이 클수록(추세 이탈) 즉사.
for _k in _BAND_SLOPE_WINDOWS:
    HYPOTHESIS_SIGN[f"band_lower_slope_{_k}_atr"] = -1  # 가파른 하락(음의 기울기)일수록 즉사.
    HYPOTHESIS_SIGN[f"band_lower_slope_{_k}_pct"] = -1

#: 존폭/손절폭 통제(독립성) 관문의 통제 변수 — WAN-150이 강건 판정한 기하 축.
_CONTROL_FEATURES: tuple[str, ...] = ("zone_width_atr", "stop_width_atr")

# 축 이름 → 그 축의 특징들.
AXIS_FEATURES: dict[str, tuple[str, ...]] = {
    "A": S_A_FEATURES,
    "B": S_B_FEATURES,
    "C": S_C_FEATURES,
}


# --------------------------------------------------------------------------- #
# §B — 상위TF 장세 조회 테이블 (룩어헤드 없음)
# --------------------------------------------------------------------------- #


@dataclass
class _RegimeTable:
    """한 상위TF 프레임의 장세 지표를 진입 시각으로 조회한다(확정봉까지만).

    진입 시각 `tt`(진입 TF 탭 봉 `open_time`)에서 **닫힌** 상위TF 봉만 본다:
    상위TF 봉 `open_time=o`는 `o + htf_ms`에 닫히므로, `o + htf_ms <= tt` (= `o <= tt −
    htf_ms`)인 마지막 봉을 고른다. 진행 중인 봉(종가 미확정)은 구조적으로 제외된다.
    """

    htf: str
    htf_ms: int
    open_times: list[int]
    trend: list[float | None]
    ema_slope: list[float | None]
    vol_raw: list[float | None]
    dev_raw: list[float | None]

    @classmethod
    def build(cls, htf: str, frame: pd.DataFrame) -> _RegimeTable:
        prepared = harness_prepare(frame)
        times = [int(t) for t in prepared["open_time"].astype("int64").tolist()]
        closes = [float(v) for v in prepared["close"].astype(float).tolist()]
        ema200 = [float(v) for v in ema(prepared, length=_REGIME_EMA).tolist()]
        atr14 = [float(v) for v in atr(prepared, length=_REGIME_ATR).tolist()]
        n = len(times)
        trend: list[float | None] = []
        ema_slope: list[float | None] = []
        vol_raw: list[float | None] = []
        dev_raw: list[float | None] = []
        for i in range(n):
            e = ema200[i]
            c = closes[i]
            if _isnan(e) or _isnan(c) or e == 0:
                trend.append(None)
                dev_raw.append(None)
            else:
                trend.append(c / e - 1.0)
                dev_raw.append(abs(c / e - 1.0))
            if i - _REGIME_SLOPE_K >= 0 and not _isnan(ema200[i - _REGIME_SLOPE_K]) and c > 0:
                ema_slope.append((e - ema200[i - _REGIME_SLOPE_K]) / c)
            else:
                ema_slope.append(None)
            vol_raw.append(_safe_div(atr14[i], c))
        return cls(
            htf=htf,
            htf_ms=_TF_MS[htf],
            open_times=times,
            trend=trend,
            ema_slope=ema_slope,
            vol_raw=vol_raw,
            dev_raw=dev_raw,
        )

    def index_at(self, trigger_time: int) -> int | None:
        """진입 시각 `trigger_time`에 마지막으로 닫힌 상위TF 봉 인덱스(없으면 None)."""
        cutoff = trigger_time - self.htf_ms
        pos = bisect.bisect_right(self.open_times, cutoff) - 1
        return pos if pos >= 0 else None

    def features_at(self, trigger_time: int) -> dict[str, float | None]:
        idx = self.index_at(trigger_time)
        if idx is None:
            return _regime_null_features(self.htf)
        return {
            f"reg_{self.htf}_trend": self.trend[idx],
            f"reg_{self.htf}_ema_slope": self.ema_slope[idx],
            f"_raw_reg_{self.htf}_vol_pctl": self.vol_raw[idx],
            f"_raw_reg_{self.htf}_dev_pctl": self.dev_raw[idx],
        }


def _regime_null_features(htf: str) -> dict[str, float | None]:
    """한 상위TF의 네 장세 특징을 전부 None으로(자격 미달·조회 불가 시 명시적 None)."""
    return {
        f"reg_{htf}_trend": None,
        f"reg_{htf}_ema_slope": None,
        f"_raw_reg_{htf}_vol_pctl": None,
        f"_raw_reg_{htf}_dev_pctl": None,
    }


def build_symbol_regime(
    symbol: str,
    entry_timeframes: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    db_path: str,
) -> dict[str, _RegimeTable]:
    """한 심볼의 상위TF 장세 테이블을 필요한 만큼만 로드한다(진입 TF보다 큰 것만)."""
    needed: set[str] = set()
    for tf in entry_timeframes:
        tf_ms = _TF_MS.get(tf)
        if tf_ms is None:
            continue
        for htf in _REGIME_HTFS:
            if _TF_MS[htf] > tf_ms:
                needed.add(htf)
    tables: dict[str, _RegimeTable] = {}
    for htf in sorted(needed, key=lambda h: _TF_MS[h]):
        market = harness.load_market_data(
            symbol,
            htf,
            start_ms=start_ms,
            end_ms=end_ms,
            need_1m=False,
            funding=False,
            db_path=db_path,
        )
        if market.empty:
            continue
        tables[htf] = _RegimeTable.build(htf, market.htf_df)
    return tables


# --------------------------------------------------------------------------- #
# 특징 추출 (WAN-150 공유 특징 + §A/§B/§C)
# --------------------------------------------------------------------------- #


@dataclass
class _Wan209Extractor:
    """WAN-150 추출기를 감싸고 §A(RVOL)·§B(상위TF)·§C(밴드 기울기)를 얹는다.

    공유 특징은 `_Wan150Extractor`가 그대로 낸다 — 그래서 이 모듈의 라벨 CSV의 공유 열은
    `wan150_labeled.csv`와 비트 동일해야 한다(회귀 테스트 고정). 봉 파생 특징은 모두 **탭
    직전 확정봉(pos−1)** 까지만 본다.
    """

    base: _Wan150Extractor
    timeframe: str
    entry_tf_ms: int
    volume: list[float]
    sma_vol: dict[int, list[float]]
    band_lower: list[float]
    regime: dict[str, _RegimeTable]

    @classmethod
    def build(
        cls,
        frame: pd.DataFrame,
        *,
        timeframe: str,
        regime: dict[str, _RegimeTable],
    ) -> _Wan209Extractor:
        base = _Wan150Extractor.build(frame)
        volume = [float(v) for v in frame["volume"].astype(float).tolist()]
        sma_vol = {
            n: [float(v) for v in sma(frame, length=n, source="volume").tolist()]
            for n in _RVOL_MA_LENGTHS
        }
        band_mid = sma(frame, length=_BAND_SMA)
        band_width = stdev(frame, length=_BAND_SMA)
        lower = band_mid - _BAND_STD * band_width
        band_lower = [float(v) for v in lower.tolist()]
        return cls(
            base=base,
            timeframe=timeframe,
            entry_tf_ms=_TF_MS[timeframe],
            volume=volume,
            sma_vol=sma_vol,
            band_lower=band_lower,
            regime=regime,
        )

    def features_for(self, cand: _Candidate) -> dict[str, float | None] | None:
        feats = self.base.features_for(cand)
        if feats is None:
            return None
        pos = self.base.base.time_to_pos.get(cand.trigger_time)
        if pos is None or pos < 1:
            return None
        prev = pos - 1

        feats.update(self._rvol(cand))
        feats.update(self._band_lower_slope(prev))
        # 상위TF는 **진입 TF보다 엄격히 큰 것만** 본다(4h 진입에 reg_4h_* 금지 — 같은 TF는
        # 상위 장세가 아니다). 규제 테이블은 심볼 단위로 {4h,1d}를 다 들고 있으므로 여기서
        # 진입 TF별로 걸러야 한다(조용한 통과 금지 — 자격 없는 축은 명시적 None).
        for htf in _REGIME_HTFS:
            table = self.regime.get(htf)
            if table is not None and _TF_MS[htf] > self.entry_tf_ms:
                feats.update(table.features_at(cand.trigger_time))
            else:
                feats.update(_regime_null_features(htf))
        return feats

    def _rvol(self, cand: _Candidate) -> dict[str, float | None]:
        """형성 봉(`OrderBlock.start_time`)의 상대거래량 = 그 봉 거래량 / SMA(거래량, N)."""
        ob = cand.order_block
        out: dict[str, float | None] = {f"rvol_sma{n}": None for n in _RVOL_MA_LENGTHS}
        if ob is None:
            return out
        fpos = self.base.base.time_to_pos.get(ob.start_time)
        if fpos is None or fpos < 0:
            return out
        vol = self.volume[fpos]
        for n in _RVOL_MA_LENGTHS:
            out[f"rvol_sma{n}"] = _safe_div(vol, self.sma_vol[n][fpos])
        return out

    def _band_lower_slope(self, prev: int) -> dict[str, float | None]:
        """하단 밴드 기울기(닫힌 봉 prev/prev−k). ÷ATR·가격% 두 척도."""
        out: dict[str, float | None] = {f: None for f in S_C_FEATURES}
        atr_now = self.base.base.atr14[prev]
        close_now = self.base.base.closes[prev]
        for k in _BAND_SLOPE_WINDOWS:
            if prev - k < 0:
                continue
            now, past = self.band_lower[prev], self.band_lower[prev - k]
            if _isnan(now) or _isnan(past):
                continue
            slope = (now - past) / k
            if not _isnan(atr_now) and atr_now > 0:
                out[f"band_lower_slope_{k}_atr"] = slope / atr_now
            if not _isnan(close_now) and close_now > 0:
                out[f"band_lower_slope_{k}_pct"] = slope / close_now
        return out


def _annotate_percentiles(labeled: list[LabeledTrade], raw_to_final: dict[str, str]) -> None:
    """셀(심볼×TF) 내부 순위 백분위로 `raw_to_final`의 각 특징을 채우고 raw 키를 지운다."""
    for raw_key, final_key in raw_to_final.items():
        by_cell: dict[tuple[str, str], list[tuple[float, LabeledTrade]]] = defaultdict(list)
        for lt in labeled:
            v = lt.features.get(raw_key)
            if v is None:
                lt.features.setdefault(final_key, None)
                continue
            by_cell[(lt.symbol, lt.timeframe)].append((v, lt))
        for items in by_cell.values():
            order = sorted(range(len(items)), key=lambda i: items[i][0])
            n = len(items)
            for rank, idx in enumerate(order):
                _, lt = items[idx]
                lt.features[final_key] = rank / (n - 1) if n > 1 else 0.5
        for lt in labeled:
            lt.features.pop(raw_key, None)


# --------------------------------------------------------------------------- #
# 셀 라벨링 (WAN-150 엔진 재사용 — 특징만 얹는다)
# --------------------------------------------------------------------------- #


def label_cell(
    market: harness.MarketData,
    *,
    params: ConfluenceParams,
    order_block_params: OrderBlockParams,
    regime: dict[str, _RegimeTable],
    stats: LabelStats | None = None,
) -> list[LabeledTrade]:
    """한 (심볼, TF)의 오늘 엔진 거래를 3분류 + §A/§B/§C 특징으로 라벨링한다.

    WAN-150 `label_cell`과 시퀀싱·라벨·공유 특징은 동일하고 `_Wan209Extractor`가 새 축을
    얹는다. `_annotate_percentiles`(상위TF 분위)는 호출부(run_experiment)에서 셀 전체를 모은
    뒤 돌린다 — 셀 내부 순위이기 때문이다.
    """
    st = stats if stats is not None else LabelStats()
    if market.empty or market.df_1m.empty:
        return []
    cfg = harness.build_config(market.timeframe)
    candidates, _ = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_params=order_block_params,
    )
    if not candidates:
        return []
    frame = harness_prepare(market.htf_df)
    extractor = _Wan209Extractor.build(frame, timeframe=market.timeframe, regime=regime)

    times = frame["open_time"].astype("int64")
    start, end = int(times.iloc[0]), int(times.iloc[-1])
    is_boundary = start + int((end - start) * IS_FRACTION)

    labeled: list[LabeledTrade] = []
    for cand, _trade in sequence_with_candidates(candidates, cfg):
        st.sequenced += 1
        label = classify(cand.reason, cand.mfe_r)
        if label is None:
            if cand.reason is ExitReason.STOP_LOSS:
                st.mfe_missing += 1
            else:
                st.end_of_data += 1
            continue
        feats = extractor.features_for(cand)
        if feats is None:
            st.feature_missing += 1
            continue
        segment = SEGMENT_IS if cand.trigger_time < is_boundary else SEGMENT_OOS
        r_mult = params.take_profit_r if label is Label.WINNER else -1.0
        labeled.append(
            LabeledTrade(
                symbol=market.symbol,
                timeframe=market.timeframe,
                segment=segment,
                side="long" if cand.side is PositionSide.LONG else "short",
                trigger_time=cand.trigger_time,
                label=label,
                mfe_r=cand.mfe_r,
                r_multiple=r_mult,
                features=feats,
            )
        )
    st.labeled += len(labeled)
    return labeled


# --------------------------------------------------------------------------- #
# §A 문턱 스윕
# --------------------------------------------------------------------------- #


class ThresholdRow(BaseModel):
    """한 (TF, 구간, RVOL 특징, 문턱)의 저/고 즉사율 대조 (심볼 풀링)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    threshold: float
    n_low: int
    n_high: int
    death_rate_low: float
    death_rate_high: float
    death_diff: float
    """저(RVOL<문턱) 즉사율 − 고(RVOL≥문턱) 즉사율. 가설이면 양수(한산한 존이 더 즉사)."""


def threshold_sweep(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    thresholds: Sequence[float] = _RVOL_THRESHOLDS,
) -> list[ThresholdRow]:
    """RVOL 문턱별 저/고 그룹 즉사율 대조(즉사 = INSTANT_DEATH)."""
    pairs = [
        (v, lt)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and (v := lt.features.get(feature)) is not None
    ]
    rows: list[ThresholdRow] = []
    for thr in thresholds:
        low = [lt for v, lt in pairs if v < thr]
        high = [lt for v, lt in pairs if v >= thr]
        if not low or not high:
            continue
        dr_low = sum(1 for lt in low if lt.is_death) / len(low)
        dr_high = sum(1 for lt in high if lt.is_death) / len(high)
        rows.append(
            ThresholdRow(
                timeframe=timeframe,
                segment=segment,
                feature=feature,
                threshold=thr,
                n_low=len(low),
                n_high=len(high),
                death_rate_low=dr_low,
                death_rate_high=dr_high,
                death_diff=dr_low - dr_high,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 존폭/손절폭 통제(독립성) 관문 — 공선성 + 부분상관 순열
# --------------------------------------------------------------------------- #


class CollinearityRow(BaseModel):
    """한 특징의 통제 변수(존폭·손절폭)와의 상관 (심볼 풀링, 구간별)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    control: str
    n: int
    correlation: float | None


def collinearity(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    control: str,
) -> CollinearityRow:
    """corr(특징, 통제 변수) — 대리변수 위험을 직접 보여준다."""
    pairs = [
        (fv, cv)
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and (fv := lt.features.get(feature)) is not None
        and (cv := lt.features.get(control)) is not None
    ]
    if len(pairs) < _MIN_TRADES_FOR_VERDICT:
        return CollinearityRow(
            timeframe=timeframe,
            segment=segment,
            feature=feature,
            control=control,
            n=len(pairs),
            correlation=None,
        )
    fvals = [f for f, _ in pairs]
    cvals = [c for _, c in pairs]
    return CollinearityRow(
        timeframe=timeframe,
        segment=segment,
        feature=feature,
        control=control,
        n=len(pairs),
        correlation=_corr(fvals, cvals),
    )


def _residualize(values: list[float], controls: list[list[float]]) -> list[float] | None:
    """`values`를 `controls`(각 통제 변수 열)에 대해 OLS 잔차화한다(절편 포함).

    통제 변수가 여럿이면 순차적으로 직교화(Gram–Schmidt류)해 각 방향의 선형 성분을 뺀다.
    분산 0인 통제는 건너뛴다. 표본<3이면 None.
    """
    n = len(values)
    if n < 3:
        return None
    resid = list(values)
    for col in controls:
        mean_c = sum(col) / n
        mean_r = sum(resid) / n
        cov = sum((c - mean_c) * (r - mean_r) for c, r in zip(col, resid, strict=True))
        var_c = sum((c - mean_c) ** 2 for c in col)
        if var_c <= 0:
            continue
        beta = cov / var_c
        # 절편 포함 OLS 잔차: r − mean_r − beta·(c − mean_c). 상관은 평균 불변이라 검정 결과는
        # 절편을 빼든 안 빼든 같지만, 완전 공선일 때 잔차가 0으로 떨어져 검산이 깨끗하다.
        resid = [r - mean_r - beta * (c - mean_c) for r, c in zip(resid, col, strict=True)]
    return resid


def _req_float(value: float | None) -> float:
    """호출부가 이미 None을 배제했음을 타입으로 좁힌다(가드 유지)."""
    assert value is not None
    return value


class PartialCorrRow(BaseModel):
    """존폭·손절폭을 통제한 부분상관 순열 검정 (survivor 전용)."""

    model_config = ConfigDict(frozen=True)

    timeframe: str
    segment: str
    feature: str
    axis: str
    n: int
    raw_correlation: float | None
    partial_correlation: float | None
    p_value: float | None
    permutations: int


def partial_correlation_test(
    labeled: list[LabeledTrade],
    *,
    timeframe: str,
    segment: str,
    feature: str,
    axis: str,
    subset: Axis,
    positive: Axis,
    controls: Sequence[str] = _CONTROL_FEATURES,
    permutations: int = _PERMUTATIONS,
    seed: int = _SEED,
) -> PartialCorrRow:
    """즉사와 특징의 연관이 존폭/손절폭을 통제한 뒤에도 남는가(독립성 관문).

    특징·즉사(0/1)를 통제 변수에 각각 잔차화한 뒤 잔차끼리의 상관을 통계량으로 쓰고, 널은
    WAN-117 자와 같은 **심볼 층화 라벨 순열**(즉사 라벨만 섞어 잔차화 이후의 연관을 끊는다).
    """
    rows = [
        lt
        for lt in labeled
        if lt.timeframe == timeframe
        and lt.segment == segment
        and subset(lt)
        and lt.features.get(feature) is not None
        and all(lt.features.get(c) is not None for c in controls)
    ]
    n = len(rows)

    def _null() -> PartialCorrRow:
        return PartialCorrRow(
            timeframe=timeframe,
            segment=segment,
            feature=feature,
            axis=axis,
            n=n,
            raw_correlation=None,
            partial_correlation=None,
            p_value=None,
            permutations=0,
        )

    if n < _MIN_TRADES_FOR_VERDICT:
        return _null()
    # `rows`는 이미 특징·통제 변수가 모두 non-None인 거래만 남겼다(위 필터).
    fvals = [_req_float(lt.features[feature]) for lt in rows]
    ctrl_cols = [[_req_float(lt.features[c]) for lt in rows] for c in controls]
    target = [1.0 if positive(lt) else 0.0 for lt in rows]
    raw = _corr(fvals, target)
    resid_f = _residualize(fvals, ctrl_cols)
    if resid_f is None:
        return _null()

    def _partial(tgt: list[float]) -> float | None:
        resid_t = _residualize(tgt, ctrl_cols)
        if resid_t is None:
            return None
        return _corr(resid_f, resid_t)

    actual = _partial(target)
    if actual is None:
        return _null()

    strata: dict[str, list[int]] = defaultdict(list)
    for i, lt in enumerate(rows):
        strata[lt.symbol].append(i)

    rng = random.Random(seed)
    extreme = 0
    goal = abs(actual)
    for _ in range(permutations):
        shuffled = target.copy()
        for idxs in strata.values():
            pool = [target[i] for i in idxs]
            rng.shuffle(pool)
            for slot, i in enumerate(idxs):
                shuffled[i] = pool[slot]
        corr = _partial(shuffled)
        if corr is not None and abs(corr) >= goal - 1e-12:
            extreme += 1
    return PartialCorrRow(
        timeframe=timeframe,
        segment=segment,
        feature=feature,
        axis=axis,
        n=n,
        raw_correlation=raw,
        partial_correlation=actual,
        p_value=extreme / permutations,
        permutations=permutations,
    )


# --------------------------------------------------------------------------- #
# 판정 (축별 · WAN-150 s1 로직 일반화)
# --------------------------------------------------------------------------- #


def _rows_by_key(perm: list[PermutationRow]) -> dict[tuple[str, str, str, str], PermutationRow]:
    return {(r.timeframe, r.segment, r.feature, r.axis): r for r in perm}


def axis_survivors(
    perm: list[PermutationRow],
    *,
    features: Sequence[str],
    timeframe: str,
    alpha: float = _ALPHA,
) -> dict[str, list[str]]:
    """한 축(features) 즉사 검정 생존자 — WAN-150 `s1_survivors`를 축별 가족으로 일반화."""
    by = _rows_by_key(perm)
    tested = [
        f
        for f in features
        if (r := by.get((timeframe, SEGMENT_OOS, f, "death_vs_rest"))) is not None
        and r.p_value is not None
    ]
    alpha_adj = bonferroni_alpha(len(tested), alpha=alpha)

    def _passes(feature: str, axis: str, thresh: float) -> bool:
        o = by.get((timeframe, SEGMENT_OOS, feature, axis))
        i = by.get((timeframe, SEGMENT_IS, feature, axis))
        if o is None or o.p_value is None or o.correlation is None or o.p_value > thresh:
            return False
        if i is None or i.correlation is None:
            return False
        return (o.correlation > 0) == (i.correlation > 0)

    return {
        "death_vs_rest": [f for f in tested if _passes(f, "death_vs_rest", alpha_adj)],
        "death_vs_winner": [f for f in tested if _passes(f, "death_vs_winner", _PRACTICAL_ALPHA)],
    }


def axis_verdict(
    perm: list[PermutationRow],
    partial: list[PartialCorrRow],
    *,
    axis_name: str,
    features: Sequence[str],
    timeframe: str,
) -> tuple[str, str]:
    """한 축·TF 판정 `(code, 문장)`. code ∈ {"a", "b", "c"}.

    * 아무 특징도 즉사 축(주 검정/실무 문턱)을 못 넘으면 (b).
    * 넘되 존폭/손절폭 통제 부분상관이 무너지면(OOS p≥0.05) (c) 기하/공선.
    * 통제 뒤에도 남으면 (a) 독립 신호.
    """
    by = _rows_by_key(perm)
    tested = [
        f
        for f in features
        if (r := by.get((timeframe, SEGMENT_OOS, f, "death_vs_rest"))) is not None
        and r.p_value is not None
    ]
    if not tested:
        return (
            "b",
            f"**§{axis_name} {timeframe}**: 판정 불가 — 유효 특징(거래 "
            f"{_MIN_TRADES_FOR_VERDICT}건 이상) 없음.",
        )
    surv = axis_survivors(perm, features=features, timeframe=timeframe)
    winners = sorted(set(surv["death_vs_rest"]) | set(surv["death_vs_winner"]))
    if not winners:
        return (
            "b",
            f"**§{axis_name} {timeframe}**: **(b) 즉사가 안 보인다** — {len(tested)}개 특징 중 "
            "어느 것도 주 검정 Bonferroni 도 실무 문턱(OOS 순열 p<0.05 & IS 동일 부호)도 넘지 "
            "못한다.",
        )
    pby = {(r.timeframe, r.feature): r for r in partial if r.segment == SEGMENT_OOS}
    survive_control = [
        f
        for f in winners
        if (pr := pby.get((timeframe, f))) is not None
        and pr.p_value is not None
        and pr.p_value < _PRACTICAL_ALPHA
    ]
    surv_txt = ", ".join(f"`{f}`" for f in winners)
    if survive_control:
        keep = ", ".join(f"`{f}`" for f in survive_control)
        return (
            "a",
            f"**§{axis_name} {timeframe}**: **(a) 즉사 축을 넘고 존폭/손절폭 통제 뒤에도 남는 "
            f"특징 있음** — {keep}. ⚠️ 「선별」 대 「가격」은 미분리(후속 이슈·사용자 결정).",
        )
    return (
        "c",
        f"**§{axis_name} {timeframe}**: **(c) 즉사 축은 넘지만 기하(존폭/손절폭)의 대리변수** "
        f"— {surv_txt}가 무작위를 넘되 통제 부분상관이 OOS에서 무너진다(WAN-133/152 계열).",
    )


# --------------------------------------------------------------------------- #
# 실험 실행
# --------------------------------------------------------------------------- #


_AXES: tuple[tuple[str, Axis, Axis], ...] = (
    ("death_vs_rest", _DEATH_VS_REST[0], _DEATH_VS_REST[1]),
    ("death_vs_winner", _DEATH_VS_WINNER[0], _DEATH_VS_WINNER[1]),
)


@dataclass
class ExperimentResult:
    labeled: list[LabeledTrade] = field(default_factory=list)
    quantile: list[QuantileRow] = field(default_factory=list)
    permutation: list[PermutationRow] = field(default_factory=list)
    threshold: list[ThresholdRow] = field(default_factory=list)
    collinearity: list[CollinearityRow] = field(default_factory=list)
    partial: list[PartialCorrRow] = field(default_factory=list)
    leave_one_out: list[LeaveOneOutRow] = field(default_factory=list)
    stats: dict[tuple[str, str], LabelStats] = field(default_factory=dict)


def run_experiment(
    *,
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    permutations: int = _PERMUTATIONS,
    db_path: str = harness.DB_PATH,
) -> ExperimentResult:
    """오늘 엔진 라벨링(+§A/§B/§C) → 즉사 축 순열 → 문턱 스윕 → 공선성/부분상관 → LOO."""
    params = ConfluenceParams()
    ob_params = OrderBlockParams()
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    labeled: list[LabeledTrade] = []
    stats: dict[tuple[str, str], LabelStats] = {}
    for symbol in symbols:
        norm = harness.normalize_symbol(symbol)
        regime = build_symbol_regime(
            norm, timeframes, start_ms=start_ms, end_ms=end_ms, db_path=db_path
        )
        for timeframe in timeframes:
            market = harness.load_market_data(
                norm,
                timeframe,
                start_ms=start_ms,
                end_ms=end_ms,
                need_1m=True,
                funding=False,
                db_path=db_path,
            )
            st = LabelStats()
            cell = label_cell(
                market,
                params=params,
                order_block_params=ob_params,
                regime=regime,
                stats=st,
            )
            labeled.extend(cell)
            stats[(norm, timeframe)] = st
            deaths = sum(1 for lt in cell if lt.is_death)
            print(
                f"[wan209] {norm} {timeframe}: labeled={len(cell)} "
                f"(death={deaths} seq={st.sequenced} eod={st.end_of_data} "
                f"mfe_missing={st.mfe_missing})"
            )

    # 상위TF 분위(§B vol/dev)는 셀 전체를 모은 뒤 순위화한다.
    _annotate_percentiles(labeled, _S_B_PCTL_RAW)

    quantile: list[QuantileRow] = []
    permutation: list[PermutationRow] = []
    threshold: list[ThresholdRow] = []
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in FEATURES:
                quantile.extend(
                    quantile_rows(labeled, timeframe=timeframe, segment=segment, feature=feature)
                )
                for axis, subset, positive in _AXES:
                    permutation.append(
                        permutation_test(
                            labeled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            axis=axis,
                            subset=subset,
                            positive=positive,
                            permutations=permutations,
                            hypothesis_sign=HYPOTHESIS_SIGN.get(feature, 0),
                        )
                    )
            for feature in S_A_FEATURES:
                threshold.extend(
                    threshold_sweep(labeled, timeframe=timeframe, segment=segment, feature=feature)
                )

    # 공선성 표(전 특징) + 부분상관 통제(생존자만).
    collin: list[CollinearityRow] = []
    partial: list[PartialCorrRow] = []
    loo: list[LeaveOneOutRow] = []
    norm_symbols = tuple(harness.normalize_symbol(s) for s in symbols)
    for timeframe in timeframes:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in FEATURES:
                for control in _CONTROL_FEATURES:
                    collin.append(
                        collinearity(
                            labeled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            control=control,
                        )
                    )
        for feats in AXIS_FEATURES.values():
            surv = axis_survivors(permutation, features=feats, timeframe=timeframe)
            winners = sorted(set(surv["death_vs_rest"]) | set(surv["death_vs_winner"]))
            for feature in winners:
                # 부분상관 통제 순열(주 축) + leave-one-out — 둘 다 생존자 전용.
                for segment in (SEGMENT_IS, SEGMENT_OOS):
                    partial.append(
                        partial_correlation_test(
                            labeled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            axis="death_vs_rest",
                            subset=_DEATH_VS_REST[0],
                            positive=_DEATH_VS_REST[1],
                            permutations=permutations,
                        )
                    )
                    loo.extend(
                        leave_one_out(
                            labeled,
                            timeframe=timeframe,
                            segment=segment,
                            feature=feature,
                            axis="death_vs_rest",
                            subset=_DEATH_VS_REST[0],
                            positive=_DEATH_VS_REST[1],
                            symbols=norm_symbols,
                        )
                    )

    return ExperimentResult(
        labeled=labeled,
        quantile=quantile,
        permutation=permutation,
        threshold=threshold,
        collinearity=collin,
        partial=partial,
        leave_one_out=loo,
        stats=stats,
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _fmt_corr(r: PermutationRow | None) -> str:
    if r is None or r.correlation is None:
        return "—"
    return f"{r.correlation:+.3f}"


def _fmt_p(r: PermutationRow | None) -> str:
    if r is None or r.p_value is None:
        return "—"
    return f"{r.p_value:.4f}"


def _feature_table(
    by: dict[tuple[str, str, str, str], PermutationRow], features: Sequence[str]
) -> str:
    header = (
        "| TF | 구간 | 특징 | n | 즉사% | 주검정 corr | 주검정 p | 실무 corr | 실무 p | 가설 |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    rows = [header]
    for timeframe in DEFAULT_TIMEFRAMES:
        for segment in (SEGMENT_IS, SEGMENT_OOS):
            for feature in features:
                rest = by.get((timeframe, segment, feature, "death_vs_rest"))
                prac = by.get((timeframe, segment, feature, "death_vs_winner"))
                if rest is None:
                    continue
                arrow = (
                    "○" if rest.direction_matches else ("·" if rest.hypothesis_sign == 0 else "✗")
                )
                rows.append(
                    f"| {timeframe} | {segment} | `{feature}` | {rest.n} | "
                    f"{rest.positive_rate * 100:.1f}% | {_fmt_corr(rest)} | {_fmt_p(rest)} | "
                    f"{_fmt_corr(prac)} | {_fmt_p(prac)} | {arrow} |"
                )
    return "\n".join(rows)


def _collinearity_table(rows: list[CollinearityRow]) -> str:
    header = "| TF | 구간 | 특징 | 통제 | n | corr |\n| -- | -- | -- | -- | -- | -- |"
    out = [header]
    for r in rows:
        corr = "—" if r.correlation is None else f"{r.correlation:+.3f}"
        out.append(
            f"| {r.timeframe} | {r.segment} | `{r.feature}` | `{r.control}` | {r.n} | {corr} |"
        )
    return "\n".join(out)


def _threshold_table(rows: list[ThresholdRow]) -> str:
    header = (
        "| TF | 구간 | 특징 | 문턱 | n(저/고) | 즉사%(저) | 즉사%(고) | Δ(저−고) |\n"
        "| -- | -- | -- | -- | -- | -- | -- | -- |"
    )
    out = [header]
    for r in rows:
        out.append(
            f"| {r.timeframe} | {r.segment} | `{r.feature}` | {r.threshold:.1f} | "
            f"{r.n_low}/{r.n_high} | {r.death_rate_low * 100:.1f}% | "
            f"{r.death_rate_high * 100:.1f}% | {r.death_diff * 100:+.1f}%p |"
        )
    return "\n".join(out)


def build_summary_markdown(result: ExperimentResult, *, labeled_csv: Path) -> str:
    perm = result.permutation
    by = _rows_by_key(perm)
    lines: list[str] = []
    lines.append(
        "# WAN-209 즉사/손절 부검의 남은 세 축 — 거래량 · 상위TF 장세 · 볼린저 하단 기울기\n"
    )
    lines.append(
        f"9종목 × {', '.join(DEFAULT_TIMEFRAMES)}, 못 박은 6년 창 **{DEFAULT_START} ~ "
        f"{DEFAULT_END}**, 오늘의 채택 기본값(핀 없는 `ConfluenceParams()`·`OrderBlockParams()`) · "
        "렌즈 `baseline` 단독. WAN-150 라벨(즉사=손절 & MFE<0.5R · 애매=손절 & 0.5R≤MFE · "
        "승자=1.5R 익절)을 그대로 쓰고 §A(거래량)·§B(상위TF 장세)·§C(볼린저 하단 기울기) 특징만 "
        "얹었다. 재현: `python -m backtest.wan209_death_autopsy_axes`. "
        f"라벨 원자료: `{labeled_csv}`.\n"
    )

    total = len(result.labeled)
    n_death = sum(1 for lt in result.labeled if lt.label is Label.INSTANT_DEATH)
    n_amb = sum(1 for lt in result.labeled if lt.label is Label.AMBIGUOUS)
    n_win = sum(1 for lt in result.labeled if lt.label is Label.WINNER)
    seq_total = sum(s.sequenced for s in result.stats.values())
    lines.append("## §0 라벨 재생성 검산 (WAN-150 엔진 재사용)\n")
    lines.append(
        f"라벨링된 거래 **{total}건** = 즉사 {n_death} · 애매 {n_amb} · 승자 {n_win}. 시퀀서 거래 "
        f"{seq_total}건. **공유 특징(존폭·손절폭·`volume_pctl`·…)은 `wan150_labeled.csv`와 비트 "
        "동일해야 한다**(회귀 테스트 + `--checksum`).\n"
    )

    # 축별 판정.
    lines.append("## 축별 판정 — 즉사가 진입 시점에 보이는가\n")
    axis_titles = {"A": "거래량", "B": "상위TF 장세", "C": "볼린저 하단 기울기"}
    for axis_name, feats in AXIS_FEATURES.items():
        lines.append(f"### §{axis_name} — {axis_titles[axis_name]}\n")
        for timeframe in DEFAULT_TIMEFRAMES:
            _, sentence = axis_verdict(
                perm,
                result.partial,
                axis_name=axis_name,
                features=feats,
                timeframe=timeframe,
            )
            lines.append(f"* {sentence}")
        lines.append("")
        lines.append(_feature_table(by, feats))
        lines.append("")

    # §A 문턱 스윕.
    if result.threshold:
        lines.append("## §A 문턱 스윕 (RVOL < 문턱 대 ≥ 문턱 즉사율)\n")
        lines.append(
            "Δ>0 = 한산한 존(저 RVOL)이 더 자주 즉사(가설 방향). ⚠️ 문턱은 IS에서 고르고 OOS로 "
            "검증 — 단독 비율 차이는 채택 근거가 아니다.\n"
        )
        lines.append(_threshold_table(result.threshold))
        lines.append("")

    # 공선성.
    lines.append("## 존폭/손절폭 공선성 (대리변수 위험)\n")
    lines.append(
        "corr(특징, 통제 변수). |corr|이 크면 그 축은 `zone_width_atr`/`stop_width_atr`의 "
        "대리변수일 수 있다(WAN-150이 그 기하 축 하나만 강건 판정).\n"
    )
    lines.append(_collinearity_table(result.collinearity))
    lines.append("")

    # 부분상관 통제(생존자).
    if result.partial:
        lines.append("## 부분상관 통제 순열 (생존자 전용 — 독립성 관문)\n")
        lines.append(
            "주 검정을 넘은 특징을 존폭·손절폭에 잔차화한 뒤 즉사와의 잔차 상관을 순열 검정. "
            "OOS p≥0.05 = 기하의 대리변수(c), p<0.05 = 통제 뒤에도 남음(a).\n"
        )
        lines.append(
            "| TF | 구간 | 특징 | n | raw corr | partial corr | p |\n"
            "| -- | -- | -- | -- | -- | -- | -- |"
        )
        for r in result.partial:
            rc = "—" if r.raw_correlation is None else f"{r.raw_correlation:+.3f}"
            pc = "—" if r.partial_correlation is None else f"{r.partial_correlation:+.3f}"
            pv = "—" if r.p_value is None else f"{r.p_value:.4f}"
            lines.append(
                f"| {r.timeframe} | {r.segment} | `{r.feature}` | {r.n} | {rc} | {pc} | {pv} |"
            )
        lines.append("")

    # leave-one-out.
    if result.leave_one_out:
        lines.append("## leave-one-out (심볼 편중 진단 — 생존자만)\n")
        lines.append("| TF | 특징 | 구간 | 제외 | n | corr |\n| -- | -- | -- | -- | -- | -- |")
        for lr in result.leave_one_out:
            corr = "—" if lr.correlation is None else f"{lr.correlation:+.3f}"
            lines.append(
                f"| {lr.timeframe} | `{lr.feature}` | {lr.segment} | "
                f"{lr.excluded_symbol.split('/')[0]} | {lr.n} | {corr} |"
            )
        lines.append("")

    lines.append("## ⚠️ 인용 경고\n")
    lines.append(
        "* **「엣지 없음」(WAN-84/88/111/114/124/145/151)을 뒤집는 것으로 인용 금지** — 다른 "
        "질문(*이미 진입한 손절 중 즉사를 진입 시점에 알아보는가*)이다.\n"
        "* 전부 `baseline`(낙관) 렌즈 위의 값 · 존폭 축 체결 보수화(`pen_5bp`)는 안 쟀다.\n"
        "* §C 볼린저는 진입가를 만드는 도구 자체다(WAN-131: 기여의 84%가 선별 아닌 가격) — "
        '"선별 축을 찾았다"로 인용 금지.\n'
        "* 기본값·토대 불변 · `ALPHABLOCK_LIVE_TRADING=false` 유지(측정 전용).\n"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 검산 — 공유 특징이 WAN-150 라벨과 비트 동일
# --------------------------------------------------------------------------- #

_SHARED_CHECK_COLUMNS: tuple[str, ...] = (
    "label",
    "mfe_r",
    "zone_width_atr",
    "stop_width_atr",
    "volume_pctl",
    "rsi_ema_slope",
)


def checksum(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    wan150_csv: Path = REPORTS_DIR / "wan150_labeled.csv",
    db_path: str = harness.DB_PATH,
) -> tuple[bool, str]:
    """한 셀의 라벨·공유 특징이 `wan150_labeled.csv`와 비트 동일한지 확인한다.

    반환 `(matches, 메시지)`. WAN-209는 WAN-150 시퀀서를 그대로 재사용하므로 시퀀서 거래 수·
    라벨·공유 특징이 정확히 일치해야 한다(새 축만 추가). 라벨 CSV가 없으면 시퀀서 수 ≡ 채택
    엔진 num_trades 만 확인한다.
    """
    norm = harness.normalize_symbol(symbol)
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    regime = build_symbol_regime(
        norm, (timeframe,), start_ms=start_ms, end_ms=end_ms, db_path=db_path
    )
    market = harness.load_market_data(
        norm,
        timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        need_1m=True,
        funding=False,
        db_path=db_path,
    )
    st = LabelStats()
    cell = label_cell(
        market,
        params=ConfluenceParams(),
        order_block_params=OrderBlockParams(),
        regime=regime,
        stats=st,
    )
    _annotate_percentiles(cell, _S_B_PCTL_RAW)
    outcome = harness.run_once(
        market, params=ConfluenceParams(), cfg=harness.build_config(timeframe)
    )
    prod = outcome.result.metrics.num_trades
    if st.sequenced != prod:
        return False, f"sequenced={st.sequenced} != production num_trades={prod}"

    if not wan150_csv.exists():
        return (
            True,
            f"sequenced={st.sequenced} == production={prod} (wan150 CSV 없음 — 시퀀서만 확인)",
        )

    ref = pd.read_csv(wan150_csv)
    ref = ref[(ref["symbol"] == norm) & (ref["timeframe"] == timeframe)].reset_index(drop=True)
    mine = _labeled_to_frame(cell)
    if len(ref) != len(mine):
        return False, f"라벨 수 불일치: wan150={len(ref)} wan209={len(mine)}"
    ref = ref.sort_values("trigger_time").reset_index(drop=True)
    mine = mine.sort_values("trigger_time").reset_index(drop=True)
    for col in _SHARED_CHECK_COLUMNS:
        if col not in ref.columns or col not in mine.columns:
            continue
        a, b = ref[col], mine[col]
        if col == "label":
            if not (a.astype(str).to_numpy() == b.astype(str).to_numpy()).all():
                return False, f"공유 열 `{col}` 불일치"
            continue
        diff = (a.astype(float) - b.astype(float)).abs()
        if float(diff.fillna(0.0).max()) > 1e-9:
            return False, f"공유 열 `{col}` 최대차 {float(diff.max()):.3e} > 1e-9"
    return (
        True,
        f"sequenced={st.sequenced} · 공유 특징 {len(_SHARED_CHECK_COLUMNS)}열 비트 동일 "
        f"({len(mine)}행)",
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _rows_to_frame(rows: Sequence[BaseModel]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _labeled_to_frame(labeled: list[LabeledTrade]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for lt in labeled:
        record: dict[str, object] = {
            "symbol": lt.symbol,
            "timeframe": lt.timeframe,
            "segment": lt.segment,
            "side": lt.side,
            "trigger_time": lt.trigger_time,
            "label": lt.label.value,
            "mfe_r": lt.mfe_r,
            "r_multiple": lt.r_multiple,
        }
        # 공유 특징(존폭·손절폭·volume_pctl·rsi_ema_*)도 검산·투명성을 위해 함께 싣는다.
        for feature in (*_SHARED_CHECK_COLUMNS[2:], *FEATURES):
            record[feature] = lt.features.get(feature)
        records.append(record)
    columns = [
        "symbol",
        "timeframe",
        "segment",
        "side",
        "trigger_time",
        "label",
        "mfe_r",
        "r_multiple",
        *_SHARED_CHECK_COLUMNS[2:],
        *FEATURES,
    ]
    return pd.DataFrame(records, columns=columns)


def _write_csv(frame: pd.DataFrame, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, index=False)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-209 즉사 부검 남은 세 축")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--permutations", type=int, default=_PERMUTATIONS)
    parser.add_argument("--db", type=str, default=harness.DB_PATH)
    parser.add_argument("--checksum", action="store_true", help="검산만 돌리고 종료")
    parser.add_argument("--labeled-out", type=Path, default=REPORTS_DIR / "wan209_labeled.csv")
    parser.add_argument("--quantile-out", type=Path, default=REPORTS_DIR / "wan209_quantile.csv")
    parser.add_argument(
        "--permutation-out", type=Path, default=REPORTS_DIR / "wan209_permutation.csv"
    )
    parser.add_argument("--threshold-out", type=Path, default=REPORTS_DIR / "wan209_threshold.csv")
    parser.add_argument(
        "--collinearity-out", type=Path, default=REPORTS_DIR / "wan209_collinearity.csv"
    )
    parser.add_argument("--partial-out", type=Path, default=REPORTS_DIR / "wan209_partial.csv")
    parser.add_argument("--loo-out", type=Path, default=REPORTS_DIR / "wan209_leave_one_out.csv")
    parser.add_argument("--summary-out", type=Path, default=REPORTS_DIR / "wan209_summary.md")
    args = parser.parse_args(argv)

    if args.checksum:
        ok, msg = checksum(start=args.start, end=args.end, db_path=args.db)
        print(f"[wan209] checksum BTC 1h: {msg} match={ok}")
        return 0 if ok else 1

    result = run_experiment(
        symbols=tuple(s.strip() for s in args.symbols.split(",") if s.strip()),
        timeframes=tuple(t.strip() for t in args.timeframes.split(",") if t.strip()),
        start=args.start,
        end=args.end,
        permutations=args.permutations,
        db_path=args.db,
    )
    _write_csv(_labeled_to_frame(result.labeled), args.labeled_out)
    _write_csv(_rows_to_frame(result.quantile), args.quantile_out)
    _write_csv(_rows_to_frame(result.permutation), args.permutation_out)
    if result.threshold:
        _write_csv(_rows_to_frame(result.threshold), args.threshold_out)
    if result.collinearity:
        _write_csv(_rows_to_frame(result.collinearity), args.collinearity_out)
    if result.partial:
        _write_csv(_rows_to_frame(result.partial), args.partial_out)
    if result.leave_one_out:
        _write_csv(_rows_to_frame(result.leave_one_out), args.loo_out)
    print(f"[wan209] labeled rows={len(result.labeled)} → {args.labeled_out}")

    summary = build_summary_markdown(result, labeled_csv=args.labeled_out)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(summary, encoding="utf-8")
    print(f"[wan209] summary → {args.summary_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
