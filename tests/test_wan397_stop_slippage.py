"""WAN-397 — 슬리피지 실측의 회귀 테스트.

이 파일이 지키는 것은 **라벨이 아니라 동작**이다(WAN-91/95/112/123/159가 반복해 경계한
자리). 세 자리를 건다:

* **슬리피지 축이 실제로 걸리는가** — 0bp 팔의 슬리피지 비용이 정말 0이고 팔마다 손익이
  움직이는가(라벨만 바뀌면 이 표 전체가 무효다).
* **어느 청산이 슬리피지를 무는지 다시 정하지 않는가** — 그 분기의 단일 소스는
  `BacktestConfig.exit_liquidity`(WAN-370)이고, 목록을 손으로 적으면 익절 유동성을 바꾼 팔에서
  갈린다.
* **후보 생성 인자가 채택 북과 같은가** — 5시간짜리 실행을 기다리지 않고 잡으려고 호출
  인자를 **실제로 캡처해** 대조한다(WAN-330 스파이 패턴).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from backtest import harness
from backtest import wan397_stop_slippage as slip
from backtest.models import ExitReason
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from common.costs import Liquidity
from data.agg_trade_archive import Tick

_REAL_SYMBOL = "BTC/USDT:USDT"
_REAL_TF = "4h"
#: 게이트는 **좁은 창**으로 본다 — 6년 1분봉을 읽어 「데이터가 있나」를 확인하면 게이트가
#: 측정보다 비싸진다(이 파일의 실데이터 테스트 본체는 payload 캐시를 타 몇 초로 끝난다).
_GATE_START = "2024-01-01"
_GATE_END = "2024-01-08"


# --------------------------------------------------------------------------- #
# 자 — 봉 변동폭 · 손절가 아래 이탈
# --------------------------------------------------------------------------- #


def test_bar_range_bp_is_relative_to_the_midpoint() -> None:
    assert slip.bar_range_bp(101.0, 99.0) == pytest.approx(2.0 / 100.0 * 10_000.0)
    assert math.isnan(slip.bar_range_bp(0.0, 0.0))


def test_adverse_bp_is_directional_and_never_negative() -> None:
    """롱은 **저가**까지, 숏은 **고가**까지 — 방향을 뒤집으면 값이 달라져야 한다."""
    long_side = slip.adverse_bp(100.0, high=101.0, low=99.0, is_long=True)
    short_side = slip.adverse_bp(100.0, high=101.0, low=99.0, is_long=False)
    assert long_side == pytest.approx(100.0)
    assert short_side == pytest.approx(100.0)
    # 손절가가 그 분의 극값보다 이미 불리하면 0이다(음수로 새지 않는다).
    assert slip.adverse_bp(98.0, high=101.0, low=99.0, is_long=True) == 0.0
    assert slip.adverse_bp(102.0, high=101.0, low=99.0, is_long=False) == 0.0


def test_adverse_is_smaller_than_the_bar_range_when_the_stop_sits_inside() -> None:
    """손절가가 봉 안에 있으면 「아래로 더 간 거리」는 정의상 봉 폭보다 작다 — 두 자가
    같은 것을 재고 있지 않다는 최소한의 확인이다."""
    assert slip.adverse_bp(100.0, high=102.0, low=99.0, is_long=True) < slip.bar_range_bp(
        102.0, 99.0
    )


def test_percentile_of_reads_the_share_at_or_below() -> None:
    assert slip._percentile_of([1.0, 2.0, 3.0, 4.0], 2.0) == pytest.approx(0.5)
    assert math.isnan(slip._percentile_of([], 5.0))


# --------------------------------------------------------------------------- #
# 슬리피지를 무는 청산은 `exit_liquidity`가 정한다 (WAN-370 단일 소스)
# --------------------------------------------------------------------------- #


def test_taker_exit_reasons_follow_the_config_not_a_hand_written_list() -> None:
    """🚨 익절 유동성을 바꾸면 이 집합도 **따라 움직여야** 한다 — 안 움직이면 목록을 손으로
    적었다는 뜻이고, 그 순간 이 모듈과 엔진이 다른 비용을 말한다."""
    adopted = harness.build_config(
        _REAL_TF, take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    )
    legacy = harness.build_config(
        _REAL_TF, take_profit_liquidity=harness.LEGACY_TAKE_PROFIT_LIQUIDITY
    )
    assert ExitReason.STOP_LOSS in slip.taker_exit_reasons(adopted)
    assert ExitReason.END_OF_DATA in slip.taker_exit_reasons(adopted)
    # 채택 회계에서는 익절이 메이커라 슬리피지를 안 문다.
    assert ExitReason.TAKE_PROFIT not in slip.taker_exit_reasons(adopted)
    # 옛 회계(익절도 테이커)에서는 문다 — 집합이 설정을 따라간다는 직접 증거.
    assert ExitReason.TAKE_PROFIT in slip.taker_exit_reasons(legacy)
    assert adopted.exit_liquidity(ExitReason.TAKE_PROFIT) is Liquidity.MAKER


# --------------------------------------------------------------------------- #
# 선형 외삽 — 예측이지 측정이 아니다
# --------------------------------------------------------------------------- #


def test_linear_slippage_scales_with_the_rate() -> None:
    doubled = slip.linear_slippage_r(0.02, base=0.0005, target=0.0010)
    assert doubled == pytest.approx(0.04, rel=1e-3)
    assert slip.linear_slippage_r(0.02, base=0.0005, target=0.0005) == pytest.approx(0.02)


# --------------------------------------------------------------------------- #
# 체결내역 걷기 — 크기가 실제로 값에 들어가는가
# --------------------------------------------------------------------------- #


def _tape() -> list[Tick]:
    """손절가 100 아래로 내려가며 얇아지는 호가 — 크기가 커질수록 단가가 나빠져야 한다."""
    return [
        Tick(time_ms=0, price=101.0, qty=50.0),  # 아직 트리거 전
        Tick(time_ms=1, price=100.0, qty=1.0),
        Tick(time_ms=2, price=99.0, qty=1.0),
        Tick(time_ms=3, price=98.0, qty=1.0),
    ]


def test_walk_tape_triggers_only_after_the_stop_is_touched() -> None:
    fill, filled, first = slip.walk_tape(_tape(), stop_price=100.0, quantity=1.0, is_long=True)
    assert first == pytest.approx(100.0)
    assert filled == pytest.approx(1.0)
    assert fill == pytest.approx(100.0)


def test_walk_tape_gets_worse_as_the_order_gets_bigger() -> None:
    """🚨 **크기를 빼고 재면 「5bp가 맞다」는 답이 나온다** — 그건 우리가 낼 주문이 아니다."""
    small, _, _ = slip.walk_tape(_tape(), stop_price=100.0, quantity=1.0, is_long=True)
    big, filled, _ = slip.walk_tape(_tape(), stop_price=100.0, quantity=3.0, is_long=True)
    assert big < small
    assert filled == pytest.approx(3.0)
    assert big == pytest.approx((100.0 + 99.0 + 98.0) / 3.0)


def test_walk_tape_reports_a_short_fill_instead_of_pretending() -> None:
    fill, filled, _ = slip.walk_tape(_tape(), stop_price=100.0, quantity=10.0, is_long=True)
    assert filled == pytest.approx(3.0)
    assert fill == pytest.approx((100.0 + 99.0 + 98.0) / 3.0)


def test_walk_tape_is_direction_aware() -> None:
    """숏 청산은 **위로** 뚫릴 때 트리거된다 — 방향을 안 보면 영영 안 걸린다."""
    ticks = [Tick(time_ms=0, price=99.0, qty=5.0), Tick(time_ms=1, price=101.0, qty=5.0)]
    fill, filled, _ = slip.walk_tape(ticks, stop_price=100.0, quantity=1.0, is_long=False)
    assert filled == pytest.approx(1.0)
    assert fill == pytest.approx(101.0)


# --------------------------------------------------------------------------- #
# 표본 — 층화 · 시드 고정
# --------------------------------------------------------------------------- #


def _detail_frame(counts: dict[str, int]) -> pd.DataFrame:
    rows = []
    for timeframe, n in counts.items():
        for i in range(n):
            rows.append({"timeframe": timeframe, "symbol": "BTC/USDT:USDT", "exit_ms": i})
    return pd.DataFrame(rows)


def test_sample_is_stratified_and_deterministic() -> None:
    frame = _detail_frame({"15m": 600, "1h": 200, "2h": 100, "4h": 100})
    first = slip.sample_exits(frame, size=100, seed=slip.TICK_SEED)
    second = slip.sample_exits(frame, size=100, seed=slip.TICK_SEED)
    assert list(first.index) == list(second.index)
    assert len(first) == 100
    share = first["timeframe"].value_counts()
    # 비중에 비례한다 — 15m이 모집단의 60%면 표본에서도 그 언저리다.
    assert 55 <= int(share["15m"]) <= 65


def test_sample_never_asks_for_more_than_a_stratum_holds() -> None:
    frame = _detail_frame({"15m": 3, "1h": 2})
    assert len(slip.sample_exits(frame, size=100, seed=1)) == 5


# --------------------------------------------------------------------------- #
# 인구조사 — 조건부 판과 대조군이 **둘 다** 나오는가
# --------------------------------------------------------------------------- #


def test_census_emits_both_the_conditional_and_the_control() -> None:
    detail = pd.DataFrame(
        [
            {
                "segment": "oos_warm",
                "symbol": "BTC/USDT:USDT",
                "timeframe": "4h",
                "bar_range_bp": 20.0,
                "adverse_bp": 8.0,
            },
            {
                "segment": "oos_warm",
                "symbol": "BTC/USDT:USDT",
                "timeframe": "4h",
                "bar_range_bp": 40.0,
                "adverse_bp": 2.0,
            },
        ]
    )
    control = {
        ("oos_warm", "BTC/USDT:USDT"): {
            "p25": 1.0,
            "p50": 2.0,
            "p75": 3.0,
            "p90": 4.0,
            "p99": 5.0,
            "num_bars": 1000.0,
            "share_above_5bp": 0.1,
            "percentile_of_5bp": 0.9,
        }
    }
    rows = slip.census_rows(detail, control)
    conditional = [r for r in rows if r.conditional and r.axis == "symbol"]
    unconditional = [r for r in rows if not r.conditional]
    assert conditional and unconditional
    # 5bp가 봉 변동폭 분포의 0분위(둘 다 5bp보다 크다) · 손절가 아래 이탈에서는 50%.
    ranges = next(r for r in conditional if r.metric == "bar_range_bp")
    adverse = next(r for r in conditional if r.metric == "adverse_bp")
    assert ranges.percentile_of_5bp == pytest.approx(0.0)
    assert adverse.percentile_of_5bp == pytest.approx(0.5)
    assert unconditional[0].percentile_of_5bp == pytest.approx(0.9)


def test_segment_windows_come_from_the_placed_trades() -> None:
    detail = pd.DataFrame(
        [
            {"segment": "oos_warm", "exit_ms": 100},
            {"segment": "oos_warm", "exit_ms": 400},
            {"segment": "full", "exit_ms": 10},
        ]
    )
    windows = slip.segment_windows(detail)
    assert windows["oos_warm"] == (100, 400 + 60_000)
    assert windows["full"] == (10, 10 + 60_000)


# --------------------------------------------------------------------------- #
# 검산 — 좌표가 아니면 (a)를 건너뛴다
# --------------------------------------------------------------------------- #


def test_checksum_a_is_skipped_off_the_adopted_coordinates() -> None:
    """🚨 좁혀 돈 파일럿을 적재된 채택 좌표 행과 대조하면 좌표 차이가 **배선 오류처럼
    보인다**(WAN-381이 실측 `5.63e+04`로 겪은 자리)."""
    assert not slip.on_adopted_coordinates([_REAL_SYMBOL], [_REAL_TF], "2020-09-15", "2026-07-22")
    assert slip.on_adopted_coordinates(
        harness.DEFAULT_SYMBOLS,
        harness.DEFAULT_TIMEFRAMES,
        harness.DEFAULT_START,
        harness.DEFAULT_END,
    )
    rows = [
        slip.SensitivityRow(
            slippage_bp=5.0,
            segment=slip.PRIMARY_SEGMENT,
            adopted=True,
            num_cells=1,
            num_candidates=10,
            num_trades=3,
            win_rate=0.5,
            mean_net_r=-0.1,
            net_r_stderr=0.0,
            gross_r=0.0,
            cost_r=0.1,
            slippage_r=0.02,
            stop_fee_r=0.0,
            entry_fee_r=0.0,
            take_profit_fee_r=0.0,
            funding_r=0.0,
            breakeven_win_rate=0.44,
            linear_mean_net_r=None,
            linear_gap=None,
        )
    ]
    skipped = slip.checksums(rows, adopted_coordinates=False)
    assert any("건너뜀" in row.metric for row in skipped)


# --------------------------------------------------------------------------- #
# 후보 생성 인자 — 채택 북과 같은가 (WAN-330 스파이 패턴)
# --------------------------------------------------------------------------- #


def test_build_payloads_uses_the_adopted_cell_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """🚨 여기서 익절 유동성을 빠뜨리면 **옛 비용 회계**로 도는 표가 나온다(WAN-370/373).
    핀(존폭 필터·취소 시점)을 실수로 넣는 것도 여기서 잡는다 — 이 표는 **오늘 좌표**다."""
    captured: dict[str, Any] = {}

    def spy(*args: Any, **kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(slip, "run_cells", spy)
    slip.build_payloads(
        harness.DEFAULT_SYMBOLS,
        harness.DEFAULT_TIMEFRAMES,
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        jobs=1,
    )
    assert captured["take_profit_liquidity"] is harness.ADOPTED_TAKE_PROFIT_LIQUIDITY
    for key, value in ADOPTED_CELL_KWARGS.items():
        assert captured[key] == value
    # 핀은 하나도 없다(WAN-305) — 존폭 필터·취소 시점은 채택 기본값을 물려받는다.
    assert "max_zone_width_atr" not in captured
    assert "invalidation_cancel" not in captured
    # 캐시를 맞추려고 기준 팔만 요청한다 — 확인 트리거 **관측**은 켜지 않는다(WAN-394 §0).
    assert captured["confirmation_arms"] == ("기준",)
    assert captured.get("observe_confirmation") in (None, False)


# --------------------------------------------------------------------------- #
# 실데이터 — 슬리피지 축이 라벨이 아니라 동작인가
# --------------------------------------------------------------------------- #


def _skip_without_real_data() -> None:
    """🚨 게이트는 `run_cells` **호출 전에** 판정한다 — 안 그러면 CI의 빈 DB가 skip이 아니라
    실패로 끝난다(이 저장소가 이미 겪은 실패)."""
    market = harness.load_market_data(
        _REAL_SYMBOL,
        _REAL_TF,
        start_ms=parse_date_ms(_GATE_START),
        end_ms=parse_date_ms(_GATE_END),
    )
    if market.empty or market.df_1m.empty:
        pytest.skip(f"{_REAL_SYMBOL} {_REAL_TF} 실데이터가 없어 건너뜁니다(CI 기본).")


def test_slippage_arm_actually_bites_and_the_adopted_arm_is_the_default() -> None:
    """세 가지를 한 번에 건다.

    * **0bp 팔의 슬리피지 비용이 정확히 0** — 팔이 라벨이 아니라 실제로 걸렸다.
    * **팔이 올라갈수록 거래당 net R이 내려간다** — 축의 방향이 맞다.
    * **채택 요율(5bp) 팔 ≡ 슬리피지 인자를 안 준 배치** — `ADOPTED_SLIPPAGE`가 정말 엔진
      기본값이다(상수를 잘못 적으면 「현행」 열이 현행이 아니게 된다).
    """
    _skip_without_real_data()
    payloads = slip.build_payloads(
        [_REAL_SYMBOL],
        [_REAL_TF],
        start=harness.DEFAULT_START,
        end=harness.DEFAULT_END,
        jobs=1,
        cache=PayloadCache(),
    )
    rows, exits = slip.sensitivity_rows(
        payloads,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        segments=[slip.PRIMARY_SEGMENT],
        grid=(0.0, slip.ADOPTED_SLIPPAGE, 0.0020),
        log=False,
    )
    by_bp = {r.slippage_bp: r for r in rows}
    assert by_bp[0.0].slippage_r == 0.0
    assert by_bp[5.0].slippage_r > 0.0
    assert by_bp[0.0].mean_net_r > by_bp[5.0].mean_net_r > by_bp[20.0].mean_net_r
    # 진입은 지정가라 슬리피지를 안 문다(WAN-396) — 팔 사이에서 gross R이 안 움직인다.
    assert by_bp[0.0].gross_r == pytest.approx(by_bp[20.0].gross_r, abs=1e-12)

    default = slip.place(
        payloads,
        slippage=slip.ADOPTED_SLIPPAGE,
        start_ms=parse_date_ms(harness.DEFAULT_START),
        end_ms=parse_date_ms(harness.DEFAULT_END),
        segments=[slip.PRIMARY_SEGMENT],
    )
    engine_default = harness.build_config(_REAL_TF)
    assert engine_default.slippage == pytest.approx(slip.ADOPTED_SLIPPAGE)
    assert default[0].row.num_trades == by_bp[5.0].num_trades

    # 테이커 청산이 실제로 잡히고, 엔진이 물린 슬리피지가 정확히 채택 요율이다.
    taker = exits[slip.PRIMARY_SEGMENT]
    assert taker, "테이커 청산이 0건 — §1의 모집단이 비었다."
    for exit_row in taker[:20]:
        charged = abs(exit_row.exit_price - exit_row.stop_price) / exit_row.stop_price
        assert charged == pytest.approx(slip.ADOPTED_SLIPPAGE, rel=1e-6)
