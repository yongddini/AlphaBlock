"""WAN-110 리포트의 조립 로직 테스트 (실데이터 없이 합성 후보로).

리포트가 **라벨과 실제 실행이 갈라질** 자리들을 고정한다: 렌즈 축이 WAN-96/CLI와 같은
뜻인지(다르면 이 표를 WAN-97/107 표와 나란히 못 놓는다), 탈락 없는 렌즈에 시드 5개를
돌려 같은 숫자 5줄을 내지 않는지, 판정 문장이 표의 숫자에서 실제로 계산되는지
(사람이 손으로 적으면 재실행 때 문장과 숫자가 갈라진다 — WAN-95가 겪은 사고).

`build_cell`의 새 인자 두 개(`params`·`order_blocks`)가 **기본 실행을 바꾸지 않는다**는
것도 함께 고정한다 — 바뀌면 WAN-103/108 CSV가 조용히 재현되지 않는다.
"""

from __future__ import annotations

import inspect

import pandas as pd
import pytest

from backtest.harness import FILL_PRESETS_BY_NAME, build_params, fill_preset, pin_band_bar
from backtest.models import ExitReason, PositionSide
from backtest.sweep import default_backtest_config
from backtest.wan103_portfolio_leverage_report import PARAMS, _Cell, build_cell
from backtest.wan110_multi_position_fill_conservatism import (
    HEADLINE_SCENARIO,
    LENS_NAMES,
    MULTI_LEVERAGES,
    PINNED_BAND_BAR,
    PINNED_OFFSET_BPS,
    PINNED_RSI_GATE_MODE,
    SCENARIO_SINGLE,
    _lens_seeds,
    aggregate,
    asymmetry,
    cell_rows,
    rows_to_frame,
    verdict,
)
from backtest.zone_limit_backtest import ZoneLimitStats, _Candidate
from strategy.models import ConfluenceParams

_MIN = 60_000


def _cands(count: int) -> list[_Candidate]:
    """겹치도록(같은 시각에 전부 열려 있도록) 깔린 서로 다른 존의 후보들."""
    return [
        _Candidate(
            side=PositionSide.LONG,
            entry_time=i * _MIN,
            entry_price=100.0,
            exit_time=1000 * _MIN,
            exit_price=101.0,
            reason=ExitReason.TAKE_PROFIT,
            stop_price=96.0,
            zone_key=frozenset({i}),
        )
        for i in range(count)
    ]


def _cell(*, eligible: int = 10, filled: int = 4) -> _Cell:
    return _Cell(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        segment="oos",
        candidates=_cands(3),
        rates=(),
        cfg=default_backtest_config("1h"),
        stats=ZoneLimitStats(eligible=eligible, filled=filled),
    )


# --------------------------------------------------------------------------- #
# 렌즈 축이 WAN-96/CLI와 같은 뜻인가
# --------------------------------------------------------------------------- #


def test_lenses_come_from_the_shared_harness_presets() -> None:
    """렌즈 이름이 `harness` 프리셋에 실재해야 한다.

    이름을 이 모듈이 따로 정의하면 `pen_5bp`가 여기선 5bp, CLI에선 다른 값이 되는
    사고가 열린다 — 그 순간 이 표를 WAN-97/107 표와 나란히 놓고 읽을 수 없다.
    """
    for name in LENS_NAMES:
        assert name in FILL_PRESETS_BY_NAME


def test_lens_axis_is_official_sensitivity_stress() -> None:
    """공식(`baseline`) · 민감도(`pen_5bp`) · 스트레스(`pen_5bp_drop_50`) 세 렌즈.

    CLAUDE.md 토대 2가 정한 병기 규칙이다 — 공식 렌즈만 내면 WAN-95의 "15m 반전" 착시를
    렌즈만 바꿔 재연하게 된다.
    """
    assert LENS_NAMES == ("baseline", "pen_5bp", "pen_5bp_drop_50")
    assert fill_preset("baseline").penetration_bps == 0.0
    assert fill_preset("baseline").dropout_rate == 0.0
    assert fill_preset("pen_5bp").penetration_bps == 5.0
    assert fill_preset("pen_5bp_drop_50").dropout_rate == 0.5


def test_baseline_lens_is_the_pinned_wan103_engine() -> None:
    """`baseline` 렌즈 = WAN-103/108이 돌린 엔진 그 자체여야 WAN-108 행이 재현된다.

    이 리포트의 `baseline` 열은 WAN-108 표와 **같은 숫자**여야 대조가 성립한다.
    보수화 노브를 뺀 나머지가 하나라도 다르면 그 대조가 깨진다.

    ⚠️ 채택 기본값이 두 번(WAN-112 오프셋 2bp · WAN-123 게이트 제거) 움직였지만 이 재검은
    **WAN-103과 같은 셋업 풀**을 봐야 한다("같은 풀에 렌즈만 조이면 다중 우위가 남는가"가
    질문이므로). 그래서 둘 다 WAN-103 엔진에서 가져와 고정한다. 게이트는 특히 중요하다 —
    오프셋과 달리 **풀 자체를 넓혀** 질문의 전제를 깬다.
    """
    assert (
        build_params(
            fill=fill_preset("baseline"),
            seed=0,
            offset_bps=PINNED_OFFSET_BPS,
            base=pin_band_bar(
                ConfluenceParams(rsi_gate_mode=PINNED_RSI_GATE_MODE, max_zone_width_atr=None),
                PINNED_BAND_BAR,
            ),
        )
        == PARAMS
    )
    assert PINNED_OFFSET_BPS == 0.0
    assert PINNED_RSI_GATE_MODE == "first_tap_free" != ConfluenceParams().rsi_gate_mode
    # WAN-132(밴드 정본 전환)로 고정 필드가 셋이 됐다.
    default_band = ConfluenceParams().deviation_filter
    assert default_band is not None
    assert PINNED_BAND_BAR == "tap" != default_band.band_bar


# --------------------------------------------------------------------------- #
# 시드 축 — 안 뽑는 난수를 5번 뽑은 척하지 않는가
# --------------------------------------------------------------------------- #


def test_non_dropout_lenses_run_a_single_seed() -> None:
    """탈락이 없으면 난수를 뽑지 않으므로 시드가 결과에 영향이 없다.

    5개를 돌면 **같은 숫자 5줄**이 나와 표가 "5번 검증했다"로 오독된다.
    """
    assert _lens_seeds(fill_preset("baseline")) == (0,)
    assert _lens_seeds(fill_preset("pen_5bp")) == (0,)


def test_dropout_lens_runs_every_preset_seed() -> None:
    """탈락이 있으면 프리셋의 시드 전부를 돈다 — 단일 시드의 운을 배제하기 위해."""
    assert _lens_seeds(fill_preset("pen_5bp_drop_50")) == (0, 1, 2, 3, 4)
    assert len(_lens_seeds(fill_preset("pen_5bp_drop_50"))) > 1


# --------------------------------------------------------------------------- #
# `build_cell`의 새 인자가 기본 실행을 바꾸지 않는가 (WAN-103/108 재현)
# --------------------------------------------------------------------------- #


def test_build_cell_params_default_to_the_adopted_params() -> None:
    """`params`를 안 주면 채택 기본값 — 안 그러면 WAN-103/108이 다른 엔진을 탄다."""
    signature = inspect.signature(build_cell)
    assert signature.parameters["params"].default is None
    assert signature.parameters["order_blocks"].default is None


def test_build_cell_lens_params_differ_from_default() -> None:
    """렌즈 파라미터는 기준선과 달라야 한다(같으면 축이 아무 일도 안 한 것)."""
    lens = build_params(
        fill=fill_preset("pen_5bp"),
        seed=0,
        offset_bps=PINNED_OFFSET_BPS,
        base=pin_band_bar(
            ConfluenceParams(rsi_gate_mode=PINNED_RSI_GATE_MODE, max_zone_width_atr=None),
            PINNED_BAND_BAR,
        ),
    )
    assert lens != PARAMS
    assert lens.fill_penetration_bps == 5.0
    # 보수화 노브 **말고는** 고정 엔진과 같아야 한다.
    assert lens.model_copy(update={"fill_penetration_bps": 0.0}) == PARAMS


# --------------------------------------------------------------------------- #
# 행 조립 — 안 잰 값을 0으로 적지 않는가
# --------------------------------------------------------------------------- #


def test_single_row_leaves_portfolio_diagnostics_unmeasured() -> None:
    """`single`은 포트폴리오 시퀀서를 안 타므로 진단이 None이어야 한다.

    0으로 채우면 "겹친 리스크가 0이었다"는 사실 주장이 되는데, 그건 재지 않은 값이다
    (단일 포지션의 실제 동시 리스크는 1%다) — WAN-103/108과 같은 규칙.
    """
    rows = cell_rows(_cell(), "baseline", 0)
    single = next(r for r in rows if r.scenario == SCENARIO_SINGLE)
    assert single.max_concurrent_risk_ratio is None
    assert single.liquidations is None
    assert single.peak_concurrency == 1


def test_multi_rows_measure_portfolio_diagnostics() -> None:
    """다중 행은 시퀀서를 타므로 진단이 실제로 채워진다."""
    rows = cell_rows(_cell(), "baseline", 0)
    multi = [r for r in rows if r.scenario != SCENARIO_SINGLE]
    assert len(multi) == len(MULTI_LEVERAGES)
    assert all(r.max_concurrent_risk_ratio is not None for r in multi)
    assert all(r.liquidations is not None for r in multi)


def test_fill_rate_is_a_property_of_the_candidate_pool() -> None:
    """체결률은 셀의 성질이라 같은 셀을 공유하는 모든 시나리오에서 같다."""
    rows = cell_rows(_cell(eligible=10, filled=4), "baseline", 0)
    assert {r.fill_rate for r in rows} == {0.4}


def test_rows_carry_their_lens_and_seed_labels() -> None:
    """좌표가 행에 실려야 렌즈를 가로질러 CSV를 다시 가를 수 있다."""
    rows = cell_rows(_cell(), "pen_5bp_drop_50", 3)
    assert {r.lens for r in rows} == {"pen_5bp_drop_50"}
    assert {r.seed for r in rows} == {3}


# --------------------------------------------------------------------------- #
# 판정·진단이 표의 숫자에서 계산되는가
# --------------------------------------------------------------------------- #


def _agg_frame(single: float, multi: float, lens: str = "baseline") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "lens": lens,
                "timeframe": "15m",
                "segment": "oos",
                "scenario": SCENARIO_SINGLE,
                "total_return": single,
                "num_trades": 100.0,
            },
            {
                "lens": lens,
                "timeframe": "15m",
                "segment": "oos",
                "scenario": HEADLINE_SCENARIO,
                "total_return": multi,
                "num_trades": 110.0,
            },
        ]
    )


def test_verdict_calls_multi_when_multi_wins() -> None:
    line = verdict(_agg_frame(single=0.2326, multi=0.3881))[0]
    assert "다중 우위" in line
    assert "단일 우위" not in line


def test_verdict_calls_single_when_single_wins() -> None:
    """부호가 뒤집힌 표에서 판정도 뒤집혀야 한다 — 이게 이 이슈의 질문 그 자체다."""
    line = verdict(_agg_frame(single=0.2326, multi=0.1000))[0]
    assert "단일 우위" in line


def test_verdict_reports_every_lens() -> None:
    frame = pd.concat([_agg_frame(0.1, 0.2, lens=name) for name in LENS_NAMES])
    assert len(verdict(frame)) == len(LENS_NAMES)


def test_asymmetry_measures_kept_ratios_against_baseline() -> None:
    """WAN-96 진단: 거래는 90% 남았는데 수익은 20%만 남았다 = 비대칭."""
    frame = pd.DataFrame(
        [
            {
                "lens": "baseline",
                "timeframe": "15m",
                "segment": "oos",
                "scenario": SCENARIO_SINGLE,
                "total_return": 0.5,
                "num_trades": 100.0,
            },
            {
                "lens": "pen_5bp",
                "timeframe": "15m",
                "segment": "oos",
                "scenario": SCENARIO_SINGLE,
                "total_return": 0.1,
                "num_trades": 90.0,
            },
        ]
    )
    row = asymmetry(frame).iloc[0]
    assert row["return_kept"] == pytest.approx(0.2)
    assert row["trades_kept"] == pytest.approx(0.9)


def test_asymmetry_excludes_the_baseline_lens_itself() -> None:
    """`baseline`은 기준선이라 자기 자신과의 비교 행을 내지 않는다."""
    frame = _agg_frame(0.1, 0.2)
    assert asymmetry(frame).empty


def test_aggregate_counts_positive_symbols() -> None:
    """심볼별 부호가 갈리는 일이 잦아 평균 옆에 플러스 심볼 수를 함께 낸다."""
    rows = cell_rows(_cell(), "baseline", 0)
    agg = aggregate(rows)
    assert "symbols_positive" in agg.columns
    assert "return_min" in agg.columns
    assert "return_max" in agg.columns


def test_rows_to_frame_keeps_column_order() -> None:
    frame = rows_to_frame(cell_rows(_cell(), "baseline", 0))
    assert list(frame.columns)[:4] == ["lens", "seed", "symbol", "timeframe"]


def test_default_params_untouched_by_this_report() -> None:
    """이 리포트는 채택 기본값을 바꾸지 않는다(기본값 불변 — 이슈 완료기준).

    ⚠️ 이 리포트의 엔진은 **세 필드**(오프셋 · RSI 게이트 · 밴드 표본)에서 채택 기본값과
    **의도적으로** 갈라져 있다(WAN-112 · WAN-123 · WAN-132). 그건 이 리포트가 기본값을
    바꿔서가 아니라 **WAN-103의 셋업 풀에 고정**돼 있어서다 — 체결 가정 노브는 여전히
    기본값을 건드리지 않는다.
    """
    assert ConfluenceParams().fill_penetration_bps == 0.0
    assert ConfluenceParams().fill_dropout_rate == 0.0
    assert (
        pin_band_bar(
            ConfluenceParams(
                zone_limit_offset_bps=PINNED_OFFSET_BPS,
                rsi_gate_mode=PINNED_RSI_GATE_MODE,
                max_zone_width_atr=None,
            ),
            PINNED_BAND_BAR,
        )
        == PARAMS
    )
