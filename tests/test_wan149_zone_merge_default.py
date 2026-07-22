"""WAN-149: 존 병합 폐지(`combine_obs` 기본값 False)와 그 파급 고정.

이 파일이 지키는 것은 셋이다:

1. **§1 기본값 전환이 라벨이 아니라 동작으로 일어났는가** — `True` 옵트인 경로가 살아
   있고 두 값이 실제로 **다른 시그널**을 낸다.
2. **§2 파급 고정이 실제로 옛 엔진을 돌리는가** — `LEGACY_COMBINE_OBS` 핀이 걸린 격자가
   병합 존을 보고, 안 걸린 기본 실행이 분리 존을 본다. 이 저장소가 반복해 당한
   「바꿨다고 믿으면서 안 바뀐 것」(WAN-91/95/112/123)의 **거울상**을 막는다.
3. **§6 차트 버그가 이 전환으로 해소되는가** — 진입 화살표마다 근거 존 박스가 목록에
   있다(합쳐진 존은 원본 어느 것과도 `zone_key`가 안 맞아 어떤 필터로도 안 보였다).
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.harness import (
    BASELINE_FILL,
    LEGACY_COMBINE_OBS,
    LEGACY_OB_PARAMS,
    MarketData,
    build_row,
    detect_order_blocks,
    pin_combine_obs,
)
from backtest.run import Grid, build_parser, grid_from_args, iter_combos
from backtest.synthetic import make_synthetic_ohlcv
from dashboard.charts import ZoneCategory, entered_zone_keys, filter_zones, zone_key
from dashboard.pipeline import run_pipeline
from strategy.models import ConfluenceParams, OrderBlockParams

# --------------------------------------------------------------------------- #
# §1 기본값 전환
# --------------------------------------------------------------------------- #


def test_adopted_default_is_separated_zones() -> None:
    """채택 기본값 = 원본 존 단위 분리(WAN-149 사용자 결정)."""
    assert OrderBlockParams().combine_obs is False


def test_merged_path_survives_as_opt_in() -> None:
    """`True` 옵트인 경로는 **삭제하지 않는다** — 되돌릴 여지를 남긴다(이슈 §1).

    라벨만 남기고 경로가 죽으면 되돌릴 수 없으므로, 두 값이 실제로 **다른 시그널
    집합**을 내는지를 동작으로 본다.
    """
    df = make_synthetic_ohlcv(symbol="BTC/USDT:USDT", timeframe="1h", bars=800, seed=7)
    market = MarketData("BTC/USDT:USDT", "1h", df, pd.DataFrame(), [])

    separated = detect_order_blocks(market)
    merged = detect_order_blocks(market, LEGACY_OB_PARAMS)

    assert LEGACY_COMBINE_OBS is True
    # 아카이브는 두 경로 모두 원본 단위로 보존된다(`order_blocks.py:562`) — 갈라지는
    # 것은 시그널 쪽이다. 그 성질 자체가 §6 차트 버그의 원인이라 여기서 함께 고정한다.
    assert len(separated.order_blocks) == len(merged.order_blocks)
    assert [s.zone_key for s in separated.signals] != [s.zone_key for s in merged.signals]
    assert all(key is not None and len(key) == 1 for key in (s.zone_key for s in separated.signals))
    assert any(key is not None and len(key) > 1 for key in (s.zone_key for s in merged.signals))


# --------------------------------------------------------------------------- #
# §2 파급 고정
# --------------------------------------------------------------------------- #


def test_pin_combine_obs_touches_only_that_field() -> None:
    base = OrderBlockParams(swing_length=7, zone_count="high")
    pinned = pin_combine_obs(base)
    assert pinned.combine_obs is LEGACY_COMBINE_OBS
    assert pinned.model_dump(exclude={"combine_obs"}) == base.model_dump(exclude={"combine_obs"})


def test_legacy_ob_params_is_the_old_engine() -> None:
    assert LEGACY_OB_PARAMS.combine_obs is True
    assert OrderBlockParams(combine_obs=True) == LEGACY_OB_PARAMS


def test_grid_axis_defaults_to_adopted_engine() -> None:
    """인자를 안 주면 `(None,)` — 채택 기본값에 맡긴다.

    여기서 `(False,)`를 지어내면(= 기본값 복사) 기본값이 다시 움직일 때 이 경로만 옛
    값을 물고 도는 조용한 갈라짐이 생긴다(`_default_offsets_bps`의 교훈).
    """
    grid = grid_from_args(build_parser().parse_args(["--symbol", "BTCUSDT"]))
    assert grid.combine_obs == (None,)
    (combo,) = iter_combos(grid)
    assert combo.combine_obs is None
    assert combo.order_block == OrderBlockParams()


def test_cli_axis_parses_both_arms() -> None:
    grid = grid_from_args(
        build_parser().parse_args(["--symbol", "BTCUSDT", "--combine-obs", "true,false"])
    )
    assert grid.combine_obs == (True, False)
    assert [c.order_block.combine_obs for c in iter_combos(grid)] == [True, False]


def test_cli_axis_rejects_unknown_token() -> None:
    with pytest.raises(ValueError, match="combine-obs"):
        grid_from_args(build_parser().parse_args(["--symbol", "BTCUSDT", "--combine-obs", "maybe"]))


def test_row_carries_the_value_actually_detected() -> None:
    """행에 찍히는 값은 **탐지에 넘어간 객체**에서 온다 — 요청 라벨이 아니다.

    라벨을 따로 조립하면 "분리로 돌고 병합 라벨이 붙는" WAN-95 부류의 거짓말이 가능하다.
    """
    df = make_synthetic_ohlcv(symbol="BTC/USDT:USDT", timeframe="1h", bars=300, seed=3)
    market = MarketData("BTC/USDT:USDT", "1h", df, pd.DataFrame(), [])
    params = ConfluenceParams(entry_mode="close", rsi_mode="closed_bar", max_zone_width_atr=None)
    from backtest.harness import build_config, run_once, segments_for

    outcome = run_once(market, params=params, cfg=build_config("1h"))
    (segment,) = segments_for(oos=False, walkforward=0)

    merged_row = build_row(
        outcome,
        market,
        segment=segment,
        params=params,
        fill_name="baseline",
        order_block=LEGACY_OB_PARAMS,
    )
    default_row = build_row(outcome, market, segment=segment, params=params, fill_name="baseline")
    assert merged_row.combine_obs is True
    assert default_row.combine_obs is False


def test_each_arm_carries_its_own_detection_params() -> None:
    """병합 축은 **탐지** 파라미터라 조합마다 자기 `OrderBlockParams`를 들고 간다.

    두 팔이 같은 `OrderBlockResult`를 공유하면 라벨만 다른 같은 숫자가 나온다 —
    `_run_cell`의 탐지 캐시 키가 이 값 하나인 것이 그 방어이고, 여기서는 조합이 실제로
    서로 다른 탐지 파라미터를 내는지를 고정한다.
    """
    grid = Grid(
        symbols=("BTC/USDT:USDT",),
        timeframes=("1h",),
        entry_modes=("zone_limit",),
        take_profit_rs=(1.5,),
        offsets_bps=(2.0,),
        fills=(BASELINE_FILL,),
        combine_obs=(True, False),
    )
    combos = iter_combos(grid)
    assert [c.combine_obs for c in combos] == [True, False]
    assert [c.order_block for c in combos] == [
        OrderBlockParams(combine_obs=True),
        OrderBlockParams(combine_obs=False),
    ]


# --------------------------------------------------------------------------- #
# §6 차트 버그 — 진입 화살표마다 근거 존 박스가 있는가
# --------------------------------------------------------------------------- #


def test_every_entry_signal_has_a_zone_box_in_the_archive() -> None:
    """사용자 발견(2026-07-21): *"OB도 없는데 어떻게 진입한거야"*.

    병합 시절에는 시그널이 **합쳐진 존**을 싣는데(`top=max`·`bottom=min`) 차트가 그리는
    목록은 **원본 단위 아카이브**라, `zone_key`(상하단까지 일치)가 어느 원본과도 안 맞아
    진입 화살표의 근거 박스가 **어떤 필터로도 안 보였다**. 분리하면 시그널이 원본 존을
    그대로 싣게 되므로 맞아떨어진다 — 그 해소를 동작으로 고정한다.
    """
    df = make_synthetic_ohlcv(symbol="BTC/USDT:USDT", timeframe="1h", bars=800, seed=7)
    pipeline = run_pipeline(
        df,
        OrderBlockParams(),
        ConfluenceParams(entry_mode="close", rsi_mode="closed_bar", max_zone_width_atr=None),
    )
    entered = entered_zone_keys(pipeline.signals)
    assert entered, "합성 데이터에서 진입 시그널이 나와야 이 회귀 테스트가 의미가 있다"

    archive_keys = {zone_key(ob) for ob in pipeline.order_blocks}
    assert entered <= archive_keys

    drawn = filter_zones(pipeline.order_blocks, {ZoneCategory.ENTERED}, entered)
    assert {zone_key(ob) for ob in drawn} == entered


def test_merged_path_still_has_the_known_chart_limitation() -> None:
    """⚠️ **버그 자체가 고쳐진 게 아니다 — 옵트인 경로에는 그대로 남는다**(결정문서 §6).

    개발자 판단으로 근본 수정 대신 **문서화 + 이 테스트로 한계를 못 박는 쪽**을 골랐다:
    `zone_key`를 느슨하게 만들면 서로 다른 존이 같은 키를 갖게 되고, 그건 그리는 층의
    편의를 위해 존 식별을 흐리는 거래다(진입 근거를 화면에서 특정할 수 없게 된다).
    """
    df = make_synthetic_ohlcv(symbol="BTC/USDT:USDT", timeframe="1h", bars=800, seed=7)
    pipeline = run_pipeline(
        df,
        OrderBlockParams(combine_obs=True),
        ConfluenceParams(entry_mode="close", rsi_mode="closed_bar", max_zone_width_atr=None),
    )
    entered = entered_zone_keys(pipeline.signals)
    archive_keys = {zone_key(ob) for ob in pipeline.order_blocks}
    assert entered - archive_keys, (
        "병합 경로에서는 합쳐진 존의 키가 원본 아카이브에 없어야 한다 — 이 성질이 "
        "사라졌다면 §6 한계가 해소된 것이니 결정문서를 갱신할 것."
    )


# --------------------------------------------------------------------------- #
# §5 대시보드 — 새 기본값으로 돌고, 옛 적재분이 그 사실을 드러내는가
# --------------------------------------------------------------------------- #


def test_dashboard_badge_says_merge_off_on_the_adopted_default() -> None:
    """대시보드는 `OrderBlockParams()`를 그대로 쓰므로 배지가 새 기본값을 따라간다.

    라벨을 손으로 적어 두면 기본값이 움직여도 화면만 옛 값을 말한다 — 이 저장소가
    반복해 당한 그 사고라, 배지를 **기본값에서 읽는지**를 동작으로 고정한다.
    """
    from backtest.models import BacktestConfig
    from dashboard.app import _run_config_badge_text

    text = _run_config_badge_text(
        ConfluenceParams(entry_mode="close", rsi_mode="closed_bar", max_zone_width_atr=None),
        OrderBlockParams(),
        BacktestConfig(),
    )
    assert "병합: OFF" in text


def test_saved_run_badge_reveals_the_merge_setting() -> None:
    """저장된 거래 탭에서 **옛 실행을 고르면 병합 시절 거래가 보인다**(이슈 §5-3).

    `run_id`는 지문에 엔진 파라미터가 들어가 자동으로 갈리지만, 화면 배지가 말해 주지
    않으면 사용자는 그 표를 오늘의 엔진 성적으로 읽는다.
    """
    from backtest.models import BacktestConfig
    from backtest.trade_store import RunFingerprint

    def fingerprint(ob: OrderBlockParams) -> RunFingerprint:
        return RunFingerprint(
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            entry_mode="zone_limit",
            fill="baseline",
            confluence_json=ConfluenceParams().model_dump_json(),
            order_block_json=ob.model_dump_json(),
            config_json=BacktestConfig().model_dump_json(),
        )

    merged = fingerprint(LEGACY_OB_PARAMS)
    separated = fingerprint(OrderBlockParams())
    assert merged.combine_obs is True
    assert separated.combine_obs is False
    assert "병합 ON" in merged.label()
    assert "병합 OFF" in separated.label()
    assert merged.run_id != separated.run_id
