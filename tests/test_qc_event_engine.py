"""WAN-181: 이벤트 구동 엔진(QC 포팅 골격)의 정본 동치·거부 계약 테스트.

파일럿 감사(`backtest.wan181_qc_pilot`)가 실데이터에서 재는 것을 합성 데이터로
동작 고정한다 — 후보(셋업)·통계·최종 거래가 정본 파이프라인과 **정확히** 같아야
하고, 지원 범위 밖 파라미터는 조용히 받지 않고 거부해야 한다(WAN-95 부류 방지).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import MarketData
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.wan181_qc_pilot import build_join_frame, trades_frame
from backtest.zone_limit_backtest import (
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from data.models import FundingRate
from qc.event_engine import run_event_backtest, validate_supported
from strategy.models import ConfluenceParams

_SYMBOL = "TEST/USDT:USDT"
_TIMEFRAME = "1h"


def _derive_1m(htf: pd.DataFrame, htf_ms: int) -> pd.DataFrame:
    """상위TF 봉 내부를 open→low→high→close 경로로 잇는 1분봉을 파생한다.

    독립 시드의 1m 합성(`test_harness._market` 패턴)은 상위TF와 가격대가 어긋나
    지정가가 한 번도 닿지 않을 수 있다 — 이 파생은 상위TF의 저가·고가를 1m 경로가
    실제로 지나가게 해 체결이 나는 패리티 표본을 만들고, 각 슬롯 마지막 1m 종가가
    상위TF 종가와 같아 밴드 커밋 값도 상위TF 확정 종가와 정확히 일치한다.
    """
    per_bar = htf_ms // 60_000
    rows: list[dict[str, float | int | bool]] = []
    for _, bar in htf.iterrows():
        o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        third = max(per_bar // 3, 1)
        anchors = [o, lo, h, c]
        spans = [third, third, per_bar - 2 * third]
        points: list[float] = []
        for seg, span in enumerate(spans):
            lo_v, hi_v = anchors[seg], anchors[seg + 1]
            points.extend(lo_v + (hi_v - lo_v) * (i + 1) / span for i in range(span))
        prev = o
        for i, point in enumerate(points):
            rows.append(
                {
                    "open_time": int(bar["open_time"]) + i * 60_000,
                    "open": prev,
                    "high": max(prev, point),
                    "low": min(prev, point),
                    "close": point,
                    "volume": float(bar["volume"]) / per_bar,
                    "closed": True,
                }
            )
            prev = point
    return pd.DataFrame(rows)


def _market(bars: int = 500) -> MarketData:
    """상위TF 전 구간을 파생 1분봉이 덮는 합성 시장 데이터.

    합성 파라미터(진폭 3% · 노이즈 0.3% · 주기 32)는 볼린저 폭이 존을 통째로 기각하지
    않아 **체결이 실제로 나는** 조합을 탐색으로 고른 것이다 — 기본 합성(진폭 7%)은 σ가
    커서 밴드가 존 아래로 빠져 지정가가 한 번도 안 걸리고, 그러면 패리티 테스트가 빈
    집합끼리의 가짜 통과가 된다.
    """
    htf = make_synthetic_ohlcv(
        timeframe=_TIMEFRAME,
        bars=bars,
        seed=5,
        swing_amplitude=0.03,
        noise=0.003,
        swing_period=32,
    )
    htf_ms = timeframe_to_ms(_TIMEFRAME)
    start = int(htf["open_time"].iloc[0])
    one_min = _derive_1m(htf, htf_ms)
    interval = 8 * 60 * 60_000
    rates = [
        FundingRate(symbol=_SYMBOL, funding_time=t, rate=0.0001)
        for t in range(start, int(htf["open_time"].iloc[-1]), interval)
    ]
    return MarketData(_SYMBOL, _TIMEFRAME, htf, one_min, rates)


@pytest.mark.parametrize("zone_filter_on", [True, False])
def test_event_engine_matches_canonical_pipeline(zone_filter_on: bool) -> None:
    """이벤트 재배열은 정본(셋업별 배치 + 일괄 시퀀싱)과 같은 후보·통계·거래를 낸다.

    존폭 필터 on/off 두 팔에서 잰다 — off 팔은 셋업이 더 많아 재배열의 병렬 상태
    관리(동시 대기 주문·북 스킵)를 더 세게 두드린다.
    """
    market = _market()
    params = harness.build_params(
        max_zone_width_atr=harness.UNSET if zone_filter_on else None,
        short_enabled=not zone_filter_on,  # off 팔은 숏까지 켜 표본을 키운다(패리티는 대칭).
    )
    cfg = harness.build_config(_TIMEFRAME, funding_enabled=True)
    ob_result = harness.detect_order_blocks(market)

    canon_cands, canon_stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
    )
    canon_trades = [t for _, t in sequence_with_candidates(canon_cands, cfg, market.funding_rates)]

    outcome = run_event_backtest(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
        funding_rates=market.funding_rates,
    )

    # 통계 축 — eligible/filled/penetrations가 정본 집계와 같아야 한다.
    assert outcome.stats == canon_stats

    # 후보 축 — 셋업 키 조인에서 한쪽에만 있는 행 0 · 필드 불일치 0.
    join = build_join_frame(canon_cands, outcome.candidates)
    assert bool(join["in_canonical"].all()) and bool(join["in_event"].all())
    assert bool(join["fields_match"].all())

    # 시퀀싱·회계 축 — 같은 분 타이가 없으면 온라인 북은 정본 시퀀서와 동치여야 한다.
    if outcome.same_minute_fill_ties == 0:
        assert trades_frame(outcome.trades).equals(trades_frame(canon_trades))

    # 빈 데이터로 통과하는 가짜 검증 방지 — 필터 off 팔은 실제 체결이 있어야 한다.
    if not zone_filter_on:
        assert canon_stats.filled > 0 and len(canon_trades) > 0


def test_event_engine_counts_book_skips_like_canonical_sequencer() -> None:
    """북 점유 스킵 수 = (가상 체결 수 − 배치 거래 수) — 정본 시퀀서의 겹침 스킵 대응.

    사이징 스킵(수량 0)은 북을 잠그지 않는 별도 경로라 등식을 흐리므로, 이 테스트는
    `risk_sizing=None`(고정 비율 사이징 — 수량이 0이 될 수 없는 경로)으로 그 축을 끈다.
    깨지면 온라인 북이 정본과 다른 스킵을 하고 있다는 뜻이다.
    """
    market = _market()
    params = harness.build_params(max_zone_width_atr=None, short_enabled=True)
    cfg = harness.build_config(_TIMEFRAME, funding_enabled=False).model_copy(
        update={"risk_sizing": None}
    )
    ob_result = harness.detect_order_blocks(market)
    outcome = run_event_backtest(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
    )
    assert outcome.skipped_fills == outcome.stats.filled - len(outcome.trades)


def test_validate_supported_rejects_out_of_scope_params() -> None:
    """파일럿 범위 밖 조합은 라벨만 붙이고 돌지 않는다 — 명시적 거부(WAN-95 부류 방지)."""
    with pytest.raises(ValueError, match="intrabar_live"):
        validate_supported(harness.pin_band_bar(ConfluenceParams()))  # band_bar="tap"
    with pytest.raises(ValueError, match="unconditional"):
        validate_supported(ConfluenceParams(rsi_gate_mode=harness.LEGACY_RSI_GATE_MODE))
    with pytest.raises(ValueError, match="fill_dropout_rate"):
        validate_supported(harness.build_params(fill=harness.fill_preset("pen_5bp_drop_50")))
    with pytest.raises(ValueError, match="B안"):
        validate_supported(harness.build_params(entry_mode="close"))


def test_event_engine_rejects_out_of_order_minutes() -> None:
    """분봉이 시간 역행하면 조용히 어긋난 상태로 계속 가지 않고 즉시 거부한다."""
    from backtest.substep import SubStep
    from qc.event_engine import ZoneLimitEventEngine

    engine = ZoneLimitEventEngine(
        params=harness.build_params(),
        cfg=harness.build_config(_TIMEFRAME),
        timeframe=_TIMEFRAME,
    )
    step = SubStep(time=120_000, high=1.0, low=1.0, close=1.0, htf_bar_time=0)
    engine.on_minute(step)
    with pytest.raises(ValueError, match="오름차순"):
        engine.on_minute(step)


def test_build_join_frame_rejects_duplicate_setup_keys() -> None:
    """조인 키가 겹치면 감사 축이 무너진다 — 진행하지 않고 거부한다."""
    market = _market(bars=200)
    params = harness.build_params(max_zone_width_atr=None)
    cfg = harness.build_config(_TIMEFRAME, funding_enabled=False)
    ob_result = harness.detect_order_blocks(market)
    cands, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        _TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
    )
    if not cands:
        pytest.skip("합성 데이터에 후보가 없어 중복 키 시나리오를 만들 수 없습니다.")
    with pytest.raises(ValueError, match="유일하지"):
        build_join_frame(cands + cands[:1], cands)
