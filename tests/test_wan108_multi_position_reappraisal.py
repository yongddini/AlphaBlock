"""WAN-108 재판정 리포트의 조립 로직 테스트 (실데이터 없이 합성 후보로).

리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 사이징 축이 실제로 `cfg`에
실리는지(안 실리면 "1안을 5배로 돌린 결과에 2안 라벨"이 붙는다), 2안에서 청산이 실제로
잡히는지(1안의 「청산 0」이 사이징 덕이지 레버리지 덕이 아님을 이 대조가 보인다),
TF 축이 사용자 확정 범위와 맞는지.
"""

from __future__ import annotations

import pytest

from backtest.models import BacktestConfig, ExitReason, PositionSide
from backtest.portfolio import PortfolioParams, sequence_portfolio
from backtest.sweep import default_backtest_config
from backtest.wan108_multi_position_reappraisal import (
    ALL_TIMEFRAMES,
    SCENARIO_PEAK,
    SERIES_TIMEFRAMES,
    SIZING_VARIANTS,
    UNIVERSES,
    SizingVariant,
    _scenarios,
)
from backtest.zone_limit_backtest import _Candidate, _to_trade
from execution.sizing import PositionSizingParams

_MIN = 60_000


def _variant(name: str) -> SizingVariant:
    return next(v for v in SIZING_VARIANTS if v.name == name)


def _cands(count: int, *, stop: float = 96.0) -> list[_Candidate]:
    """겹치도록(같은 시각에 전부 열려 있도록) 깔린 서로 다른 존의 후보들."""
    return [
        _Candidate(
            side=PositionSide.LONG,
            entry_time=i * _MIN,
            entry_price=100.0,
            exit_time=1000 * _MIN,
            exit_price=101.0,
            reason=ExitReason.TAKE_PROFIT,
            stop_price=stop,
            zone_key=frozenset({i}),
        )
        for i in range(count)
    ]


# --------------------------------------------------------------------------- #
# 사이징 축이 실제로 실리는가 (라벨 ≠ 실행 방지)
# --------------------------------------------------------------------------- #


def test_configure_puts_sizing_mode_on_the_config() -> None:
    """`SizingVariant`를 통과한 cfg가 실제로 2안 사이징을 들고 있어야 한다.

    이게 깨지면 리포트는 **1안을 5배로 돌린 결과에 「2안」 라벨**을 붙인다 — WAN-95가
    `entry_mode`에서, WAN-91이 펀딩 배선에서 각각 한 번씩 겪은 조용한 실패다.
    """
    cfg = default_backtest_config("1h")
    configured = _variant("fixed_f1_lev5").configure(cfg)
    assert configured.risk_sizing is not None
    assert configured.risk_sizing.sizing_mode == "fixed_notional"
    assert configured.risk_sizing.notional_fraction == 1.0
    # 원본 불변 — 셀 cfg를 여러 사이징이 공유하므로 오염되면 뒤 축이 앞 축을 물려받는다.
    assert cfg.risk_sizing is not None
    assert cfg.risk_sizing.sizing_mode == "risk_pct"


def test_risk_pct_variant_leaves_the_adopted_sizing_untouched() -> None:
    """1안은 채택 경로 그 자체 — configure가 아무것도 바꾸지 않아야 한다."""
    cfg = default_backtest_config("1h")
    configured = _variant("risk_pct").configure(cfg)
    assert configured.risk_sizing == cfg.risk_sizing


def test_configure_without_risk_sizing_is_noop() -> None:
    cfg = BacktestConfig(risk_sizing=None)
    assert _variant("fixed_f1_lev5").configure(cfg) is cfg


# --------------------------------------------------------------------------- #
# 2안의 청산 (사용자 요구: 「청산 0」이 사이징 덕임을 정면으로 검증)
# --------------------------------------------------------------------------- #


def test_fixed_notional_liquidates_where_risk_pct_does_not() -> None:
    """같은 셋업·같은 레버리지에서 **1안은 청산 0, 2안은 청산 발생**.

    `wan103.md` §5의 경고("레버리지는 이 엔진에서 위험 배수가 아니라 명목 천장 — 사용자의
    1/N 시드 × N배 모델과 다른 물건")를 숫자로 고정한다. 2안은 자리당 손실이 손절 거리에
    비례해 상한이 없으므로, 손절이 충분히 멀면 최악 가정 자본이 유지증거금 아래로 내려간다.

    ⚠️ 손절 거리를 25%로 잡은 건 임의가 아니다 — 최악 가정 청산은 `Σ(손절 거리 %) × f`가
    자본을 먹어야 트리거되므로, f=1·5자리에서 **자리당 평균 20%** 를 넘겨야 한다. 즉 2안
    에서도 청산은 "레버리지가 5배라서"가 아니라 **손절이 멀어서** 난다. 실제 오더블록
    손절 거리가 그만큼 먼지는 리포트가 답한다.
    """
    cands = _cands(5, stop=75.0)  # 손절 거리 25% → 2안 자리당 최악 손실 = 자본의 25%.
    base = BacktestConfig(initial_capital=10_000.0, risk_sizing=PositionSizingParams(leverage=5.0))
    portfolio = PortfolioParams(leverage=5.0)

    risk_cfg = _variant("risk_pct").configure(base)
    _paired, risk_stats = sequence_portfolio(cands, risk_cfg, _to_trade, portfolio=portfolio)
    assert not risk_stats.liquidated

    fixed_cfg = _variant("fixed_f1_lev5").configure(base)
    _paired2, fixed_stats = sequence_portfolio(cands, fixed_cfg, _to_trade, portfolio=portfolio)
    assert fixed_stats.liquidated


def test_fixed_notional_concurrent_risk_far_exceeds_one_percent_per_slot() -> None:
    """1안의 "동시 k개면 최악 k%"가 2안에서는 성립하지 않는다.

    이 비대칭이 WAN-108 사이징 축의 존재 이유다: 1안에서 「청산 0」이었던 건 레버리지가
    안전해서가 아니라 손절이 자리당 손실을 1%로 묶었기 때문이고, 2안은 그 묶음을 푼다.
    """
    cands = _cands(3, stop=96.0)
    base = BacktestConfig(initial_capital=10_000.0, risk_sizing=PositionSizingParams(leverage=5.0))
    portfolio = PortfolioParams(leverage=5.0)

    _p1, risk_stats = sequence_portfolio(
        cands, _variant("risk_pct").configure(base), _to_trade, portfolio=portfolio
    )
    _p2, fixed_stats = sequence_portfolio(
        cands, _variant("fixed_f1_lev5").configure(base), _to_trade, portfolio=portfolio
    )
    # 1안: 자리당 1% → 3자리 ≈ 3%. 2안: 자리당 4%(손절 거리) → 3자리 ≈ 12%.
    assert risk_stats.max_concurrent_risk_ratio == pytest.approx(0.03, abs=5e-3)
    assert fixed_stats.max_concurrent_risk_ratio > 0.10


def test_fixed_notional_fills_five_slots_then_skips_on_the_cap() -> None:
    """f=1 · 천장 5배 = 자리 5개 뒤 스킵(사용자 확정: "6번째 존이 닿으면 마진 없어 스킵")."""
    cands = _cands(7, stop=90.0)
    cfg = _variant("fixed_f1_lev5").configure(
        BacktestConfig(initial_capital=10_000.0, risk_sizing=PositionSizingParams(leverage=5.0))
    )
    paired, stats = sequence_portfolio(
        cands, cfg, _to_trade, portfolio=PortfolioParams(leverage=5.0)
    )
    assert stats.peak_concurrency == 5
    assert len(paired) == 5
    assert stats.skipped_notional == 2


def test_fixed_notional_full_slots_are_not_counted_as_clamped() -> None:
    """`_unclamped_notional`이 2안 식을 쓰는지 — 안 쓰면 온전한 자리가 축소로 오탐된다.

    2안의 명목은 손절 거리와 무관한데 진단이 1안 식(리스크/손절거리)을 쓰면, 손절이 먼
    자리마다 "원했던 명목"이 실제보다 작게 나와 축소 진입 카운트가 엉킨다.
    """
    cands = _cands(3, stop=50.0)  # 손절이 아주 멀다 → 1안 식이면 명목이 훨씬 작아진다.
    cfg = _variant("fixed_f1_lev5").configure(
        BacktestConfig(initial_capital=10_000.0, risk_sizing=PositionSizingParams(leverage=5.0))
    )
    _paired, stats = sequence_portfolio(
        cands, cfg, _to_trade, portfolio=PortfolioParams(leverage=5.0)
    )
    assert stats.clamped_entries == 0


# --------------------------------------------------------------------------- #
# 격자 축 (사용자 확정 범위)
# --------------------------------------------------------------------------- #


def test_series_layer_judges_15m_and_1h_and_excludes_1d() -> None:
    """층 1 본판정 = 15m·1h(WAN-107 공동 작업 TF), 4h 대조, 1d 제외."""
    assert "15m" in SERIES_TIMEFRAMES
    assert "1h" in SERIES_TIMEFRAMES
    assert "4h" in SERIES_TIMEFRAMES
    assert "1d" not in SERIES_TIMEFRAMES


def test_multi_tf_book_includes_4h_and_1d_zones() -> None:
    """층 2 통합 책은 4h·1d 존을 **진입 소스로** 받는다.

    사용자 지적: "15분봉에 진입한 상태에서 4시간봉 존에 닿을 수 있다." 표본 부족은
    4h·1d를 **단독 결론**으로 쓸 때의 제약이지, 통합 책에 존을 얹는 것과는 별개다.
    """
    assert UNIVERSES["multi_tf"] == ("15m", "1h", "4h", "1d")
    assert set(ALL_TIMEFRAMES) >= set(UNIVERSES["multi_tf"])


def test_peak_scenario_only_on_the_risk_pct_axis() -> None:
    """N배는 1안 축의 질문이다(WAN-103 §3). 2안은 천장이 5배로 확정돼 있다."""
    assert _variant("risk_pct").peak_scenario
    assert not _variant("fixed_f1_lev5").peak_scenario


def test_peak_scenario_not_duplicated_when_it_equals_a_fixed_leverage() -> None:
    """N이 고정 축과 겹치면 같은 숫자를 두 줄로 내지 않는다."""
    variant = _variant("risk_pct")
    names = [name for name, _ in _scenarios(variant, peak=3)]
    assert SCENARIO_PEAK not in names
    assert [name for name, _ in _scenarios(variant, peak=6)][-1] == SCENARIO_PEAK


def test_fixed_variants_run_only_their_declared_ceiling() -> None:
    assert _scenarios(_variant("fixed_f1_lev5"), peak=9) == [("lev_5", 5.0)]


def test_series_scope_has_no_peak_scenario() -> None:
    """series에는 `peak_N`이 없다 — 셀에서 잰 N을 그 셀에 쓰면 look-ahead이고,
    심볼마다 N이 달라 평균이 부분집합끼리 섞인다(`series_rows` docstring)."""
    assert [name for name, _ in _scenarios(_variant("risk_pct"), peak=0)] == [
        "lev_1",
        "lev_2",
        "lev_3",
    ]
