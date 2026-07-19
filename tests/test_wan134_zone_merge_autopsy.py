"""WAN-134 존 병합 부검 — 집계·검정·판정·직렬화 로직 테스트.

격자 실행(DB·수분)이 아니라 **대조·폭 통제 순열·거래량 오염·심볼 편중·ablation 델타·판정**을
손으로 만든 라벨/행으로 고정한다. 존폭 통제가 병합의 순수 효과를 폭에서 갈라내는지, 오염
게이트가 거래 수 차이를 올바로 플래그하는지, CSV 왕복이 손실 없는지를 못 박는다.
"""

from __future__ import annotations

import random

import pandas as pd

from backtest.wan134_zone_merge_autopsy import (
    _ARM_COMBINE,
    AblationRow,
    MergeTrade,
    _annotate_volume_pctl,
    _corr,
    _labeled_from_frame,
    _labeled_to_frame,
    ablation_delta,
    ablation_symbol_mean,
    contrast_row,
    leave_one_out,
    stratified_permutation_p,
    verdict_for_tf,
    volume_row,
    width_control_row,
)
from strategy.models import OrderBlock, OrderBlockDirection, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector, _make_merged_group


def _mt(
    *,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    segment: str = "oos",
    broke: bool,
    combined: bool,
    zone_width_atr: float | None = 1.0,
    volume_pctl: float | None = 0.5,
    num_component_obs: int = 1,
    ob_volume: float | None = 100.0,
    trigger_time: int = 0,
) -> MergeTrade:
    return MergeTrade(
        symbol=symbol,
        timeframe=timeframe,
        segment=segment,
        trigger_time=trigger_time,
        broke=broke,
        r_multiple=-1.0 if broke else 1.5,
        combined=combined,
        num_component_obs=num_component_obs,
        zone_width_atr=zone_width_atr,
        volume_pctl=volume_pctl,
        ob_volume=ob_volume,
    )


# --------------------------------------------------------------------------- #
# 엔진 필드 — num_component_obs
# --------------------------------------------------------------------------- #


def _ob(direction: OrderBlockDirection, top: float, bottom: float, t: int) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        top=top,
        bottom=bottom,
        start_time=t,
        confirmed_time=t,
        ob_volume=10.0,
        ob_low_volume=4.0,
        ob_high_volume=6.0,
    )


def test_single_zone_component_count_is_one() -> None:
    ob = _ob(OrderBlockDirection.BULLISH, 100.0, 90.0, 0)
    group = _make_merged_group(OrderBlockDirection.BULLISH, [(0, ob)])
    assert group.merged_ob.num_component_obs == 1
    assert group.merged_ob.combined is False


def test_merged_zone_counts_components() -> None:
    a = _ob(OrderBlockDirection.BULLISH, 100.0, 90.0, 0)
    b = _ob(OrderBlockDirection.BULLISH, 95.0, 85.0, 1)
    c = _ob(OrderBlockDirection.BULLISH, 92.0, 80.0, 2)
    group = _make_merged_group(OrderBlockDirection.BULLISH, [(0, a), (1, b), (2, c)])
    assert group.merged_ob.combined is True
    assert group.merged_ob.num_component_obs == 3
    # 합집합 경계 + 손절선은 최외곽(최저 bottom) 존.
    assert group.merged_ob.bottom == 80.0
    assert group.merged_ob.top == 100.0


def test_orderblock_default_component_count() -> None:
    ob = _ob(OrderBlockDirection.BEARISH, 50.0, 45.0, 0)
    assert ob.num_component_obs == 1


# --------------------------------------------------------------------------- #
# Ablation 팔 — combine_obs는 탐지 시점 파라미터다 (WAN-134 핵심 버그 가드)
# --------------------------------------------------------------------------- #


def test_arm_combine_mapping() -> None:
    # on=병합 · off=병합 끔. 두 팔이 다른 combine_obs를 요구해야 ablation이 성립한다.
    assert _ARM_COMBINE == {"on": True, "off": False}


def test_combine_obs_changes_detection_output() -> None:
    """`combine_obs`는 `OrderBlockDetector.run`에서 `retap_signals`를 다르게 낸다.

    이것이 WAN-134가 처음에 걸린 함정의 축이다 — 한 번 탐지해 두 팔이 공유하면 병합
    시그널이 같아 결과가 비트 단위로 같아진다. 각 팔이 자기 `combine_obs`로 따로 탐지해야
    한다. 합성 데이터가 실제로 병합을 만들고, 그 병합이 off 팔에서 사라짐을 못박는다.
    """
    from backtest.synthetic import make_synthetic_ohlcv

    df = make_synthetic_ohlcv(bars=1500, seed=7)
    on = OrderBlockDetector(OrderBlockParams(combine_obs=True)).run(df)
    off = OrderBlockDetector(OrderBlockParams(combine_obs=False)).run(df)

    # off 팔은 정의상 병합 존이 없다.
    assert all(not s.order_block.combined for s in off.retap_signals)
    # on 팔은 실제로 병합 존을 낸다(합성 데이터가 겹치는 동방향 존을 만든다).
    assert any(s.order_block.combined for s in on.retap_signals)
    # 따라서 두 탐지 결과의 시그널 집합이 다르다(팔이 갈린다).
    assert len(on.retap_signals) != len(off.retap_signals) or any(
        s.order_block.combined for s in on.retap_signals
    )


# --------------------------------------------------------------------------- #
# 점이연 상관
# --------------------------------------------------------------------------- #


def test_corr_perfect_positive() -> None:
    # combined=1 → 항상 뚫림, combined=0 → 항상 버팀.
    values = [1.0, 1.0, 0.0, 0.0]
    labels = [1.0, 1.0, 0.0, 0.0]
    corr = _corr(values, labels)
    assert corr is not None and corr > 0.99


def test_corr_zero_variance_is_none() -> None:
    assert _corr([1.0, 1.0, 1.0], [1.0, 0.0, 1.0]) is None


# --------------------------------------------------------------------------- #
# 병합/단일 대조
# --------------------------------------------------------------------------- #


def test_contrast_merged_breaks_more() -> None:
    labeled: list[MergeTrade] = []
    # 병합존 15개 중 13개 뚫림(~87%), 단일존 15개 중 2개 뚫림(~13%). n≥20이라 순열 실행.
    for i in range(15):
        labeled.append(_mt(broke=i < 13, combined=True, trigger_time=i))
    for i in range(15):
        labeled.append(_mt(broke=i < 2, combined=False, trigger_time=100 + i))
    row = contrast_row(labeled, timeframe="1h", segment="oos", permutations=300)
    assert row.n_merged == 15
    assert row.n_single == 15
    assert row.broke_merged is not None and row.broke_merged > 0.8
    assert row.broke_single is not None and row.broke_single < 0.2
    assert row.broke_diff is not None and row.broke_diff > 0.5
    assert row.correlation is not None and row.correlation > 0
    assert row.p_value is not None and row.p_value < 0.05


def test_contrast_small_group_no_permutation() -> None:
    labeled = [_mt(broke=True, combined=True, trigger_time=i) for i in range(3)]
    labeled += [_mt(broke=False, combined=False, trigger_time=100 + i) for i in range(30)]
    row = contrast_row(labeled, timeframe="1h", segment="oos", permutations=200)
    # 병합 표본이 _MIN_GROUP 미만 → 순열 미실행.
    assert row.p_value is None
    assert row.permutations == 0


# --------------------------------------------------------------------------- #
# 존폭 통제 — 병합이 폭의 대리변수일 때 통제 후 사라진다
# --------------------------------------------------------------------------- #


def test_width_control_removes_proxy_effect() -> None:
    # 병합·뚫림 모두 존폭(연속)이 몰고 간다: 넓을수록 병합이 되기 쉽고 넓을수록 뚫린다.
    # 단, 폭이 주어지면 병합과 뚫림은 서로 독립(별개 추첨)이다 → 병합은 폭의 대리변수.
    labeled: list[MergeTrade] = []
    rng = random.Random(3)
    for i in range(400):
        w = rng.uniform(1.0, 6.0)
        p_wide = (w - 1.0) / 5.0  # 폭이 넓을수록 커진다.
        combined = rng.random() < p_wide
        broke = rng.random() < p_wide  # 폭이 주어지면 combined와 독립.
        labeled.append(
            _mt(
                broke=broke,
                combined=combined,
                zone_width_atr=w,
                trigger_time=i,
                symbol=rng.choice(["BTC/USDT:USDT", "ETH/USDT:USDT"]),
            )
        )
    raw = contrast_row(labeled, timeframe="1h", segment="oos", permutations=400)
    width = width_control_row(labeled, timeframe="1h", segment="oos", permutations=400)
    # 원 대조는 폭 매개로 유의(둘 다 폭이 몰고 감)해야 의미가 있다.
    assert raw.correlation is not None and raw.correlation > 0
    # 폭을 분위로 통제하면 병합↔뚫림 연관이 사라져 유의성을 잃는다("폭의 대리변수").
    assert width.p_value is None or width.p_value > 0.05


def test_width_control_survives_when_independent() -> None:
    # 폭과 무관하게 병합이 더 뚫린다: 각 폭 대에 병합/단일 둘 다 있고 병합이 더 뚫림.
    labeled: list[MergeTrade] = []
    for i in range(120):
        wide = i % 2 == 0
        combined = i % 4 < 2  # 폭과 독립.
        width = 5.0 if wide else 1.0
        # 병합이면 70% 뚫림, 단일이면 30% — 폭과 무관.
        broke = (i % 10) < (7 if combined else 3)
        labeled.append(
            _mt(
                broke=broke,
                combined=combined,
                zone_width_atr=width,
                trigger_time=i,
                symbol="BTC/USDT:USDT",
            )
        )
    w = width_control_row(labeled, timeframe="1h", segment="oos", permutations=500)
    assert w.correlation is not None and w.correlation > 0
    assert w.p_value is not None and w.p_value < 0.05


# --------------------------------------------------------------------------- #
# 거래량 오염
# --------------------------------------------------------------------------- #


def test_volume_contamination_detected() -> None:
    labeled: list[MergeTrade] = []
    for i in range(15):
        labeled.append(_mt(broke=False, combined=True, volume_pctl=0.9, trigger_time=i))
    for i in range(15):
        labeled.append(_mt(broke=False, combined=False, volume_pctl=0.2, trigger_time=100 + i))
    row = volume_row(labeled, timeframe="1h", segment="oos")
    assert row.vol_merged is not None and row.vol_merged > 0.8
    assert row.vol_single is not None and row.vol_single < 0.3
    assert row.correlation is not None and row.correlation > 0


def test_annotate_volume_pctl_ranks_within_cell() -> None:
    labeled = [
        _mt(broke=False, combined=False, ob_volume=10.0, volume_pctl=None, trigger_time=0),
        _mt(broke=False, combined=False, ob_volume=20.0, volume_pctl=None, trigger_time=1),
        _mt(broke=False, combined=False, ob_volume=30.0, volume_pctl=None, trigger_time=2),
    ]
    out = _annotate_volume_pctl(labeled)
    pctls = sorted(t.volume_pctl for t in out if t.volume_pctl is not None)
    assert pctls == [0.0, 0.5, 1.0]


# --------------------------------------------------------------------------- #
# 심볼 편중
# --------------------------------------------------------------------------- #


def test_leave_one_out_reports_per_symbol() -> None:
    labeled: list[MergeTrade] = []
    for sym in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
        for i in range(6):
            labeled.append(_mt(broke=i < 5, combined=True, symbol=sym, trigger_time=i))
        for i in range(6):
            labeled.append(_mt(broke=i < 1, combined=False, symbol=sym, trigger_time=100 + i))
    loo = leave_one_out(labeled, timeframe="1h", segment="oos")
    assert set(loo) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
    # 대칭이라 어느 심볼을 빼도 큰 양의 손절차가 유지된다.
    for v in loo.values():
        assert v is not None and v > 0.5


# --------------------------------------------------------------------------- #
# Ablation 델타 + 오염 게이트
# --------------------------------------------------------------------------- #


def _ar(*, arm: str, seg: str, ret: float, trades: int, sym: str = "BTC/USDT:USDT") -> AblationRow:
    return AblationRow(
        symbol=sym,
        timeframe="1h",
        segment=seg,
        arm=arm,
        num_trades=trades,
        win_rate=0.5,
        total_return=ret,
        max_drawdown=0.1,
        fill_rate=0.8,
        mean_r=0.2,
    )


def test_ablation_delta_flags_trade_contamination() -> None:
    rows = [
        _ar(arm="on", seg="oos", ret=0.10, trades=100),
        _ar(arm="off", seg="oos", ret=0.15, trades=130),  # 30% 더 많음 → 오염.
    ]
    mean_frame = ablation_symbol_mean(rows)
    delta = ablation_delta(mean_frame)
    assert len(delta) == 1
    r = delta.iloc[0]
    assert abs(r["ret_delta"] - 0.05) < 1e-9
    assert bool(r["contaminated"]) is True


def test_ablation_delta_clean_when_trades_close() -> None:
    rows = [
        _ar(arm="on", seg="oos", ret=0.10, trades=100),
        _ar(arm="off", seg="oos", ret=0.11, trades=102),  # 2% → 오염 아님.
    ]
    delta = ablation_delta(ablation_symbol_mean(rows))
    assert bool(delta.iloc[0]["contaminated"]) is False


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


def _make_labeled(
    *, merged_break: float, single_break: float, n: int, seg: str, width_couples: bool
) -> list[MergeTrade]:
    """병합/단일 각 n개, 지정 손절률. width_couples면 병합=넓음(대리변수)."""
    labeled: list[MergeTrade] = []
    for i in range(n):
        labeled.append(
            _mt(
                broke=(i / n) < merged_break,
                combined=True,
                zone_width_atr=5.0 if width_couples else (5.0 if i % 2 else 1.0),
                segment=seg,
                trigger_time=i,
                symbol="BTC/USDT:USDT" if i % 2 else "ETH/USDT:USDT",
            )
        )
    for i in range(n):
        labeled.append(
            _mt(
                broke=(i / n) < single_break,
                combined=False,
                zone_width_atr=1.0 if width_couples else (5.0 if i % 2 else 1.0),
                segment=seg,
                trigger_time=1000 + i,
                symbol="BTC/USDT:USDT" if i % 2 else "ETH/USDT:USDT",
            )
        )
    return labeled


def test_verdict_c_when_no_difference() -> None:
    labeled = _make_labeled(
        merged_break=0.5, single_break=0.5, n=40, seg="oos", width_couples=False
    )
    labeled += _make_labeled(
        merged_break=0.5, single_break=0.5, n=40, seg="is", width_couples=False
    )
    contrast = [
        contrast_row(labeled, timeframe="1h", segment=s, permutations=200) for s in ("is", "oos")
    ]
    width = [
        width_control_row(labeled, timeframe="1h", segment=s, permutations=200)
        for s in ("is", "oos")
    ]
    verdict = verdict_for_tf(contrast, width, timeframe="1h")
    assert "(c)" in verdict


# --------------------------------------------------------------------------- #
# CSV 왕복
# --------------------------------------------------------------------------- #


def test_labeled_csv_roundtrip() -> None:
    labeled = [
        _mt(broke=True, combined=True, zone_width_atr=3.2, volume_pctl=0.7, num_component_obs=4),
        _mt(broke=False, combined=False, zone_width_atr=None, volume_pctl=None, ob_volume=None),
    ]
    frame = _labeled_to_frame(labeled)
    restored = _labeled_from_frame(frame)
    assert len(restored) == 2
    assert restored[0].combined is True
    assert restored[0].num_component_obs == 4
    assert abs((restored[0].zone_width_atr or 0) - 3.2) < 1e-9
    assert restored[1].zone_width_atr is None
    assert restored[1].ob_volume is None


def test_stratified_permutation_below_min_trades() -> None:
    values = [1.0, 0.0, 1.0]
    broke = [True, False, True]
    strata: list[object] = ["BTC", "BTC", "ETH"]
    corr, p = stratified_permutation_p(values, broke, strata, permutations=50)
    assert corr is None and p is None


def test_ablation_symbol_mean_averages_symbols() -> None:
    rows = [
        _ar(arm="on", seg="oos", ret=0.10, trades=100, sym="BTC/USDT:USDT"),
        _ar(arm="on", seg="oos", ret=0.20, trades=100, sym="ETH/USDT:USDT"),
    ]
    frame = ablation_symbol_mean(rows)
    assert len(frame) == 1
    assert abs(float(frame.iloc[0]["total_return"]) - 0.15) < 1e-9
    assert int(frame.iloc[0]["n_symbols"]) == 2


def test_empty_ablation_delta() -> None:
    assert ablation_delta(pd.DataFrame()).empty
