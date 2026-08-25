"""WAN-377: 봉 **안**에서 자르고 다시 돌려도 이미 끝난 거래가 비트 동일한가.

이 저장소의 미래 누수 자는 하나뿐이었다 — `test_harness.test_warm_oos_has_zero_future_leakage`
(WAN-166). 모양은 옳지만 절단 지점이 **상위TF 봉 경계**라 봉이 원자적으로 들어가거나
빠진다. 그런데 WAN-364는 **봉 *안*의 룩어헤드**였다:

    09:00  봉 시작 — 존에 지정가 대기
    09:03  지정가 체결
    09:11  존 무효화 → 옛 엔진이 09:00으로 소급 취소 → 09:03 체결이 없던 일

봉 끝(09:15)에서 자르면 봉이 통째로 들어가 차이가 없고, 봉 시작(09:00)에서 자르면 봉이
통째로 빠져 비교 대상이 없다. **그래서 그 테스트가 있었는데도 WAN-364는 안 걸렸다** —
거래당 실력이 +0.1985R → −0.1798R로 부호까지 뒤집힌 결함이었는데도.

여기서는 09:05처럼 **봉 안에서** 자른다: *"09:05까지만 세상이 존재한다"*. 상위TF 봉 N은
빼지 않고 **09:00~09:04의 1분봉으로 재구성해 넣는다**(설계 (가) · 사용자 결정). 봉을 아예
빼는 (나)는 성립하지 않는다 — 백테스트 엔진은 「상위TF 봉 → 그 안의 1분봉 서브스텝」으로
걸어가므로 봉 N이 없으면 09:03 체결이 **평가조차 되지 않아** 불일치가 룩어헤드가 아니라
「봉을 뺐기 때문」이 된다.

**이 자가 지키는 범위**(결정문 `docs/decisions/wan377.md` §범위가 정본):

* ✅ **셋업 층**(`build_zone_limit_candidates`) — 셋업 하나하나의 진입시각·진입가·
  청산시각·청산가. WAN-364의 소급 취소가 사는 자리다.
* ✅ **존 대장** — 재구성한 반쪽 봉이 전체 데이터에 없던 존을 만들어 내지 않는가.
* ⚠️ **시퀀서(포트폴리오 층)는 이 자의 범위가 아니다** — 정본 시퀀서는 같은 시각에 열린
  후보들 사이에서 **청산 시각(미래 정보)으로 동점을 가른다**(WAN-181이 이미 기록한 별개
  부류). 잘린 창에서는 미해결 후보의 청산 시각이 데이터 끝으로 당겨져 그 동점 처리가
  달라질 수 있다. 그래서 거래 층은 「차이가 나더라도 그 차이가 셋업 층까지 내려가지
  않는다」로만 건다(`test_trade_layer_differences_never_reach_the_setup_layer`).
* ❌ 데이터 준비 단계의 누수나 「뒷구간 보고 파라미터 고르기」는 다른 부류다 — 그건
  「앞구간에서 고르고 뒷구간으로 확인」 규약이 담당한다.

**테스트 전용이다** — 엔진 동작·기본값·토대를 하나도 안 바꾸고 숫자를 하나도 안 낸다.
기존 `test_warm_oos_has_zero_future_leakage`도 고치거나 지우지 않는다(따뜻한 OOS 경계를
지키는 다른 자다 — 나란히 둔다).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd
import pytest

from backtest import harness
from backtest.harness import build_config, build_params
from backtest.models import BacktestConfig, ExitReason, Trade
from backtest.sweep import timeframe_to_ms
from backtest.synthetic import make_synthetic_ohlcv
from backtest.zone_limit_backtest import (
    _Candidate,
    build_zone_limit_candidates,
    run_zone_limit_backtest_verbose,
)
from strategy.models import ConfluenceParams, OrderBlockParams
from strategy.order_blocks import OrderBlockDetector

_SYMBOL = "TEST/USDT:USDT"
_MIN_MS = 60_000

#: 비교 키 = 완료기준 1이 요구하는 네 값(+ 방향·청산 사유).
SetupKey = tuple[str, int, float, int, float, str]
#: 존의 **정체성**. `break_time`·`swept_time`은 미래를 보는 값이라 일부러 뺀다 —
#: 잘린 창에서 「아직 안 깨졌다」는 것은 결함이 아니라 이 테스트가 재려는 바로 그것이다.
ZoneKey = tuple[str, float, float, int, int]


# --------------------------------------------------------------------------- #
# 0. 절단 도구 — 「그 시점에 알 수 있던 것만」으로 세상을 다시 만든다
# --------------------------------------------------------------------------- #


def aggregate_1m(df_1m: pd.DataFrame, timeframe: str, *, allow_partial: bool) -> pd.DataFrame:
    """1분봉을 상위TF로 접는다. `allow_partial`이면 **형성 중인 마지막 봉**도 낸다.

    `data.resample.resample_ohlcv`는 구성 하위봉이 빠짐없이 모여야만 상위봉을 만든다
    (설계상 미래 누수 방지) — 그래서 형성 중인 봉을 표현하지 못한다. 여기서는 그 반쪽 봉이
    **테스트 대상**이므로 같은 집계 규칙을 쓰되 부분 버킷을 허용하는 판을 따로 둔다.

    ⚠️ 반쪽 봉도 `closed=True`로 낸다 — 엔진(`_prepare_htf`)이 확정봉만 걸어가므로
    미확정으로 표시하면 봉이 통째로 빠져 설계 (나)가 되어 버린다. 「형성 중」이라는 사실은
    그 봉이 **마지막 봉이고 1분봉이 T에서 끊긴다**는 것으로 표현된다.
    """
    htf_ms = timeframe_to_ms(timeframe)
    per_bar = htf_ms // _MIN_MS
    work = df_1m.sort_values("open_time")
    bucket = (work["open_time"].astype("int64") // htf_ms) * htf_ms
    rows: list[dict[str, object]] = []
    for start, group in work.groupby(bucket, sort=True):
        if len(group) != per_bar and not allow_partial:
            continue
        rows.append(
            {
                "symbol": _SYMBOL,
                "timeframe": timeframe,
                "open_time": int(start),
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                "volume": float(group["volume"].sum()),
                "closed": True,
            }
        )
    return pd.DataFrame(rows)


def cut_world_intrabar(
    df_1m: pd.DataFrame, timeframe: str, cut_ms: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`cut_ms`(분 경계) 이전만 존재하는 세상 — (상위TF, 1분봉).

    1분봉은 `open_time < cut_ms`만 남기고, 상위TF는 **그 1분봉에서 다시 접는다**. 그래서
    마지막 상위TF 봉은 T까지의 반쪽이 되고, 그 앞의 봉들은 전체 데이터와 같은 값이 된다
    (같은 집계 규칙 · 같은 입력).
    """
    times = df_1m["open_time"].astype("int64")
    kept = df_1m[times < cut_ms].reset_index(drop=True)
    return aggregate_1m(kept, timeframe, allow_partial=True), kept


# --------------------------------------------------------------------------- #
# 1. 비교 대상 — 「절단 이전에 끝난 일」만 본다
# --------------------------------------------------------------------------- #


def _setup_keys(candidates: Sequence[_Candidate], cut_ms: int) -> list[SetupKey]:
    """절단 이전에 **청산까지 끝난** 셋업의 비교 키.

    잘린 창에서 청산이 미확정인 셋업(`END_OF_DATA`)은 정의상 달라지므로 뺀다 —
    `test_warm_oos_has_zero_future_leakage`가 쓰는 규약 그대로다.
    """
    return sorted(
        (
            candidate.side.value,
            candidate.entry_time,
            candidate.entry_price,
            candidate.exit_time,
            candidate.exit_price,
            candidate.reason.value,
        )
        for candidate in candidates
        if candidate.exit_time < cut_ms and candidate.reason is not ExitReason.END_OF_DATA
    )


def _trade_keys(trades: Sequence[Trade], cut_ms: int) -> list[SetupKey]:
    """시퀀싱까지 마친 거래의 같은 비교 키."""
    keys: list[SetupKey] = []
    for trade in trades:
        exit_fill = trade.exits[-1]
        if exit_fill.time < cut_ms and exit_fill.reason is not ExitReason.END_OF_DATA:
            keys.append(
                (
                    trade.side.value,
                    trade.entry_time,
                    trade.entry_price,
                    exit_fill.time,
                    exit_fill.price,
                    exit_fill.reason.value,
                )
            )
    return sorted(keys)


def _zone_keys(htf_df: pd.DataFrame, ob_params: OrderBlockParams) -> set[ZoneKey]:
    result = OrderBlockDetector(ob_params).run(htf_df)
    return {
        (ob.direction.value, ob.top, ob.bottom, ob.start_time, ob.confirmed_time)
        for ob in result.order_blocks
    }


# --------------------------------------------------------------------------- #
# 2. 절단 불변 판정기
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CutReport:
    """절단 지점들을 훑은 결과. 판정은 `mismatches`가 비었는가로 낸다."""

    compared: int
    """비교한 (절단 지점 × 셋업) 쌍의 수 — 0이면 이 테스트는 아무것도 안 지킨 것이다."""
    compared_in_forming_bar: int
    """그중 **재구성한 반쪽 봉 안에서** 진입까지 한 셋업의 수(= 봉 안 절단이 실제로 문 자리)."""
    cuts: int
    intrabar_cuts: int
    mismatches: list[str] = field(default_factory=list)
    ghost_zone_cuts: list[str] = field(default_factory=list)


def intrabar_cut_report(
    df_1m: pd.DataFrame,
    timeframe: str,
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    ob_params: OrderBlockParams | None,
    cuts: Sequence[int],
) -> CutReport:
    """절단 지점마다 「잘라도 이미 끝난 일은 같은가」를 확인하고 결과를 모아 낸다.

    AssertionError로 즉사시키지 않고 리포트로 내는 것은 **돌연변이 확인**(완료기준 3)
    때문이다 — 옛 엔진에서 이 자가 실제로 무는지를 테스트가 값으로 읽어야 한다.
    """
    htf_ms = timeframe_to_ms(timeframe)

    def build(htf: pd.DataFrame, minutes: pd.DataFrame) -> list[_Candidate]:
        candidates, _stats = build_zone_limit_candidates(
            htf,
            minutes,
            timeframe,
            params=params,
            cfg=cfg,
            order_block_params=ob_params,
        )
        return candidates

    full_htf = aggregate_1m(df_1m, timeframe, allow_partial=False)
    full = build(full_htf, df_1m)
    full_zones = _zone_keys(full_htf, ob_params or OrderBlockParams())

    compared = forming = intrabar = 0
    mismatches: list[str] = []
    ghosts: list[str] = []
    for cut in sorted(set(cuts)):
        bar_open = (cut // htf_ms) * htf_ms
        if cut != bar_open:
            intrabar += 1
        cut_htf, cut_1m = cut_world_intrabar(df_1m, timeframe, cut)
        expected = _setup_keys(full, cut)
        actual = _setup_keys(build(cut_htf, cut_1m), cut)
        compared += len(expected)
        if cut != bar_open:
            forming += sum(1 for key in expected if key[1] >= bar_open)
        if expected != actual:
            only_full = sorted(set(expected) - set(actual))
            only_cut = sorted(set(actual) - set(expected))
            mismatches.append(f"T={cut}: 전체만 {only_full!r} · 절단판만 {only_cut!r}")
        extra_zones = _zone_keys(cut_htf, ob_params or OrderBlockParams()) - full_zones
        if extra_zones:
            ghosts.append(f"T={cut}: {sorted(extra_zones)!r}")
    return CutReport(
        compared=compared,
        compared_in_forming_bar=forming,
        cuts=len(set(cuts)),
        intrabar_cuts=intrabar,
        mismatches=mismatches,
        ghost_zone_cuts=ghosts,
    )


def intrabar_cuts_for(candidates: Sequence[_Candidate], timeframe: str) -> list[int]:
    """청산이 난 봉의 **매 분**을 절단 지점으로 삼는다(청산 직후 ~ 봉 마감).

    한 점만 자르면 그 봉에 우연히 문제가 없을 수 있다(완료기준 1의 「여러 시점」). 청산
    직후를 포함해야 「방금 끝난 거래」가 비교 대상에 들어가고, 봉 마감까지 훑어야 봉 경계
    절단(옛 자)과 봉 안 절단이 같은 표에서 대조된다.
    """
    htf_ms = timeframe_to_ms(timeframe)
    cuts: set[int] = set()
    for candidate in candidates:
        if candidate.reason is ExitReason.END_OF_DATA:
            continue
        bar_end = (candidate.exit_time // htf_ms) * htf_ms + htf_ms
        moment = candidate.exit_time + _MIN_MS
        while moment <= bar_end:
            cuts.add(moment)
            moment += _MIN_MS
    return sorted(cuts)


# --------------------------------------------------------------------------- #
# 3. 픽스처 — (a) 합성 시장, (b) 손으로 만든 무효화 봉
# --------------------------------------------------------------------------- #

_SYNTHETIC_TF = "5m"
#: 1분봉만 만들고 상위TF는 **거기서 접는다** — 두 시리즈를 따로 생성하면 상위TF 봉이
#: 자기 1분봉의 집계가 아니게 되어 「반쪽 봉」이 애초에 정의되지 않는다.
_SYNTHETIC_FIXTURES = ((17, 90), (13, 90))


def _synthetic_1m(seed: int, swing_period: int, *, htf_bars: int = 400) -> pd.DataFrame:
    htf_ms = timeframe_to_ms(_SYNTHETIC_TF)
    return make_synthetic_ohlcv(
        timeframe="1m",
        bars=htf_bars * (htf_ms // _MIN_MS),
        seed=seed,
        swing_period=swing_period,
        noise=0.012,
    ).assign(symbol=_SYMBOL, timeframe="1m")


def _engine_params() -> ConfluenceParams:
    """합성 데이터에서 거래가 나는 파라미터(`test_harness._warm_params` 관행).

    볼린저·존폭 필터는 이 작은 합성 셋의 후보를 전부 걸러 낸다 — 이 테스트가 보는 것은
    **절단 불변**이지 필터가 아니므로 꺼 둔다. 취소 시점(`invalidation_cancel`)은 손대지
    않으므로 채택 기본값(`bar_close`)이 그대로 온다.
    """
    base = ConfluenceParams(
        entry_mode="zone_limit", rsi_mode="realtime", short_enabled=True, deviation_filter=None
    )
    return build_params(max_zone_width_atr=None, base=base)


# ── (b) 손으로 만든 무효화 봉 ─────────────────────────────────────────────────
#
# 상위TF 골격은 `test_order_blocks._BULL_BARS`와 같은 시나리오다(손으로 추적한 강세 OB):
# idx 11의 종가가 스윙고를 돌파해 존 [98, 103]이 확정되고, **idx 12가 그 존을 깨면서
# 동시에 탭하는 봉**이다(그 파일의 `test_bullish_signal_cancelled_on_breaker_tap`이
# `status == "cancelled"`로 고정한 그 봉). 여기서는 그 봉 **안의 1분 경로**를 깔아
# 「체결 → 익절 → (절단) → 존 무효화」 순서를 만든다.
_CRAFT_TF = "15m"
_CRAFT_HTF_MS = timeframe_to_ms(_CRAFT_TF)
_CRAFT_BREAK_BAR = 12
_CRAFT_CUT = _CRAFT_BREAK_BAR * _CRAFT_HTF_MS + 8 * _MIN_MS

_CRAFT_HTF_BARS = [
    # open, high, low, close, volume
    (100.0, 102.0, 90.0, 95.0, 10.0),
    (95.0, 100.0, 93.0, 98.0, 10.0),
    (98.0, 101.0, 94.0, 99.0, 10.0),
    (99.0, 103.0, 95.0, 101.0, 10.0),
    (101.0, 110.0, 100.0, 108.0, 10.0),
    (108.0, 109.0, 104.0, 106.0, 15.0),
    (106.0, 107.0, 103.0, 105.0, 20.0),
    (105.0, 106.0, 102.0, 104.0, 25.0),
    (104.0, 105.0, 100.0, 102.0, 10.0),
    (102.0, 104.0, 99.0, 101.0, 10.0),
    (101.0, 103.0, 98.0, 100.0, 10.0),
    (100.0, 115.0, 99.0, 112.0, 30.0),  # ← 존 [98, 103] 확정
    (112.0, 113.0, 95.0, 97.0, 10.0),  # ← 무효화 봉이자 탭 봉
]

#: 무효화 봉의 1분 경로. 3분에 존 안으로 내려와 체결, 5분에 익절, **9분에 존 하단 이탈**.
#: 절단은 8분(`_CRAFT_CUT`) — 거래는 이미 끝났고 무효화는 아직 오지 않은 시점이다.
_CRAFT_BREAK_PATH = [
    (112.0, 113.0, 111.5, 112.0),
    (112.0, 112.0, 108.0, 108.5),
    (108.5, 108.5, 104.0, 104.5),
    (104.5, 104.5, 102.9, 103.2),  # 지정가 체결
    (103.2, 106.0, 103.1, 105.8),
    (105.8, 111.0, 105.5, 110.8),  # 고정 1.5R 익절
    (110.8, 111.0, 109.0, 109.5),
    (109.5, 110.0, 108.0, 108.5),
    # ── 절단 지점 T = 봉 시작 + 8분 ──
    (108.5, 108.6, 100.0, 100.5),
    (100.5, 101.0, 96.0, 96.5),  # 존 하단(98) 이탈 = 무효화
    (96.5, 98.0, 95.0, 96.0),
    (96.0, 97.5, 95.5, 97.0),
    (97.0, 97.5, 96.5, 97.0),
    (97.0, 97.5, 96.5, 97.0),
    (97.0, 97.5, 96.5, 97.0),
]


def _craft_1m() -> pd.DataFrame:
    """상위TF 골격을 그대로 집계해 내는 1분봉 — 무효화 봉만 손으로 깐 경로를 쓴다.

    나머지 봉은 첫 분이 봉의 전 범위를 담고 이후 분은 종가에 붙어 있게 둔다(집계하면
    open/high/low/close/volume이 골격과 정확히 같다). 이 테스트가 보는 것은 무효화 봉
    안의 **순서**이므로 앞선 봉의 봉내 경로는 자유도다.
    """
    per_bar = _CRAFT_HTF_MS // _MIN_MS
    rows: list[tuple[int, float, float, float, float, float]] = []
    for index, (bar_open, high, low, close, volume) in enumerate(_CRAFT_HTF_BARS):
        base = index * _CRAFT_HTF_MS
        path = _CRAFT_BREAK_PATH if index == _CRAFT_BREAK_BAR else None
        for minute in range(per_bar):
            stamp = base + minute * _MIN_MS
            if path is not None:
                m_open, m_high, m_low, m_close = path[minute]
                rows.append((stamp, m_open, m_high, m_low, m_close, volume / per_bar))
            elif minute == 0:
                rows.append((stamp, bar_open, high, low, close, volume))
            else:
                rows.append((stamp, close, close, close, close, 0.0))
    frame = pd.DataFrame(
        rows, columns=["open_time", "open", "high", "low", "close", "volume"]
    ).assign(symbol=_SYMBOL, timeframe="1m", closed=True)
    return frame


def _craft_ob_params() -> OrderBlockParams:
    """`test_order_blocks._bull_params`와 같은 값 — 손으로 추적한 그 시나리오를 재현한다."""
    return OrderBlockParams(
        swing_length=3, atr_length=3, max_atr_mult=100.0, combine_obs=False, zone_count="high"
    )


def _craft_params(*, legacy: bool) -> ConfluenceParams:
    params = _engine_params()
    return harness.pin_invalidation_cancel(params) if legacy else params


# --------------------------------------------------------------------------- #
# 4. 절단 도구가 거짓말을 하지 않는가
# --------------------------------------------------------------------------- #


def test_forming_bar_is_the_partial_aggregate_and_leaks_nothing() -> None:
    """반쪽 봉은 T 이전 1분봉의 집계 그 자체이고, 앞선 봉들은 전체 데이터와 같은 값이다.

    이게 깨지면 「잘랐다」가 거짓말이 되고, 그 위의 모든 판정이 뜻을 잃는다.
    """
    minutes = _craft_1m()
    full_htf = aggregate_1m(minutes, _CRAFT_TF, allow_partial=False)
    cut_htf, cut_1m = cut_world_intrabar(minutes, _CRAFT_TF, _CRAFT_CUT)

    # (1) T 이후는 1분봉에도 상위TF에도 남지 않는다.
    assert int(cut_1m["open_time"].max()) == _CRAFT_CUT - _MIN_MS
    last = cut_htf.iloc[-1]
    assert int(last["open_time"]) < _CRAFT_CUT <= int(last["open_time"]) + _CRAFT_HTF_MS

    # (2) 반쪽 봉 = T 이전 1분봉의 집계.
    tail = _CRAFT_BREAK_PATH[: (_CRAFT_CUT % _CRAFT_HTF_MS) // _MIN_MS]
    assert float(last["open"]) == tail[0][0]
    assert float(last["high"]) == max(step[1] for step in tail)
    assert float(last["low"]) == min(step[2] for step in tail)
    assert float(last["close"]) == tail[-1][3]

    # (3) 앞선 봉들은 전체 데이터와 **같은 값**이다(같은 집계 규칙 · 같은 입력).
    columns = ["open_time", "open", "high", "low", "close", "volume"]
    closed_part = cut_htf.iloc[:-1][columns].reset_index(drop=True)
    expected = full_htf.iloc[: len(closed_part)][columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(closed_part, expected)

    # (4) 그리고 그 앞선 봉들은 손으로 적은 상위TF 골격 그대로다.
    for index, (bar_open, high, low, close, volume) in enumerate(_CRAFT_HTF_BARS):
        row = full_htf.iloc[index]
        assert (float(row["open"]), float(row["high"])) == (bar_open, high)
        assert (float(row["low"]), float(row["close"])) == (low, close)
        assert float(row["volume"]) == pytest.approx(volume)


def test_cutting_at_a_bar_boundary_leaves_no_forming_bar() -> None:
    """봉 경계에서 자르면 반쪽 봉이 없다 — 옛 자(WAN-166)와 같은 상황이 특수 케이스로 들어온다."""
    minutes = _craft_1m()
    boundary = _CRAFT_BREAK_BAR * _CRAFT_HTF_MS
    cut_htf, _cut_1m = cut_world_intrabar(minutes, _CRAFT_TF, boundary)
    assert int(cut_htf["open_time"].max()) == boundary - _CRAFT_HTF_MS


# --------------------------------------------------------------------------- #
# 5. 픽스처가 실제로 그 상황을 만드는가 (완료기준 2·4)
# --------------------------------------------------------------------------- #


def test_break_bar_fixture_actually_creates_the_pathology() -> None:
    """무효화 봉 **안에서** 체결·청산까지 끝나고, 그 봉의 무효화는 절단 이후에 온다.

    이 조건이 없으면 아래 절단 불변 테스트는 **아무것도 안 하면서 초록불**이다.
    """
    minutes = _craft_1m()
    full_htf = aggregate_1m(minutes, _CRAFT_TF, allow_partial=False)
    ob_params = _craft_ob_params()
    detected = OrderBlockDetector(ob_params).run(full_htf)

    # (1) 존 [98, 103]이 idx 11에서 확정되고 idx 12에서 깨진다 — 탭도 그 봉이다.
    assert len(detected.order_blocks) == 1
    zone = detected.order_blocks[0]
    assert (zone.top, zone.bottom) == (103.0, 98.0)
    assert zone.break_time == _CRAFT_BREAK_BAR * _CRAFT_HTF_MS
    assert [signal.status for signal in detected.signals] == ["cancelled"]

    # (2) 무효화(존 하단 이탈)는 절단 이후에 일어난다.
    break_minute = next(
        index for index, step in enumerate(_CRAFT_BREAK_PATH) if step[2] < zone.bottom
    )
    assert _CRAFT_BREAK_BAR * _CRAFT_HTF_MS + break_minute * _MIN_MS > _CRAFT_CUT

    # (3) 채택 엔진은 그 봉 안에서 체결→익절까지 끝난 거래를 하나 낸다.
    cfg = build_config(_CRAFT_TF, funding_enabled=False)
    causal, _stats = build_zone_limit_candidates(
        full_htf,
        minutes,
        _CRAFT_TF,
        params=_craft_params(legacy=False),
        cfg=cfg,
        order_block_params=ob_params,
    )
    assert len(causal) == 1
    trade = causal[0]
    bar_open = _CRAFT_BREAK_BAR * _CRAFT_HTF_MS
    assert bar_open <= trade.entry_time < trade.exit_time < _CRAFT_CUT
    assert trade.reason is ExitReason.TAKE_PROFIT

    # (4) 옛 엔진은 그 거래를 **없던 일로 만든다** — 이게 WAN-364가 잡은 룩어헤드다.
    legacy, _legacy_stats = build_zone_limit_candidates(
        full_htf,
        minutes,
        _CRAFT_TF,
        params=_craft_params(legacy=True),
        cfg=cfg,
        order_block_params=ob_params,
    )
    assert legacy == []


# --------------------------------------------------------------------------- #
# 6. 본 판정 — 봉 안에서 잘라도 이미 끝난 일은 같은가 (완료기준 1·2)
# --------------------------------------------------------------------------- #


def test_intrabar_cut_keeps_finished_setups_bit_identical_on_the_break_bar_fixture() -> None:
    """무효화 봉 픽스처: 채택 엔진은 봉 안 절단에 흔들리지 않는다."""
    minutes = _craft_1m()
    report = intrabar_cut_report(
        minutes,
        _CRAFT_TF,
        params=_craft_params(legacy=False),
        cfg=build_config(_CRAFT_TF, funding_enabled=False),
        ob_params=_craft_ob_params(),
        cuts=[_CRAFT_CUT + offset * _MIN_MS for offset in range(-3, 4)],
    )
    assert report.compared, "픽스처가 절단 이전에 청산까지 끝난 셋업을 내지 못했다 — 테스트 무력"
    assert report.compared_in_forming_bar, "반쪽 봉 안에서 끝난 셋업이 하나도 비교되지 않았다"
    assert report.mismatches == []
    assert report.ghost_zone_cuts == []


@pytest.mark.parametrize(("seed", "swing_period"), _SYNTHETIC_FIXTURES)
def test_intrabar_cut_keeps_finished_setups_bit_identical_on_synthetic_markets(
    seed: int, swing_period: int
) -> None:
    """합성 시장을 청산이 난 봉의 **매 분**에서 잘라 본다(완료기준 1의 「여러 시점」)."""
    minutes = _synthetic_1m(seed, swing_period)
    params = _engine_params()
    cfg = build_config(_SYNTHETIC_TF, funding_enabled=False)
    full, _stats = build_zone_limit_candidates(
        aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False),
        minutes,
        _SYNTHETIC_TF,
        params=params,
        cfg=cfg,
    )
    cuts = intrabar_cuts_for(full, _SYNTHETIC_TF)
    report = intrabar_cut_report(
        minutes, _SYNTHETIC_TF, params=params, cfg=cfg, ob_params=None, cuts=cuts
    )
    assert report.intrabar_cuts, "봉 안 절단이 하나도 없다 — 봉 경계만 자르면 옛 자와 같다"
    assert report.compared, "절단 이전에 청산까지 끝난 셋업이 하나도 없다 — 테스트 무력"
    assert report.compared_in_forming_bar, "반쪽 봉 안에서 끝난 셋업이 하나도 비교되지 않았다"
    assert report.mismatches == [], "\n".join(report.mismatches)
    assert report.ghost_zone_cuts == [], "\n".join(report.ghost_zone_cuts)


# --------------------------------------------------------------------------- #
# 7. 돌연변이 확인 — 옛 엔진에서 **실제로 실패해야 한다** (완료기준 3)
# --------------------------------------------------------------------------- #


def test_intrabar_cut_invariance_fails_under_legacy_retroactive_cancel() -> None:
    """`invalidation_cancel="bar_open"`(옛 동작)에서 이 자가 **문다**.

    안 물면 그 테스트는 아무것도 안 지키는 것이고, 그건 봉 경계만 보던 옛 자와 똑같은
    상태다(이 이슈의 존재 이유). 전체 데이터에서는 소급 취소가 거래를 지우고, 절단판에서는
    무효화가 아직 오지 않아 그 거래가 살아 있다 — 그 격차가 곧 룩어헤드의 크기다.
    """
    minutes = _craft_1m()
    report = intrabar_cut_report(
        minutes,
        _CRAFT_TF,
        params=_craft_params(legacy=True),
        cfg=build_config(_CRAFT_TF, funding_enabled=False),
        ob_params=_craft_ob_params(),
        cuts=[_CRAFT_CUT],
    )
    assert report.mismatches, "옛 엔진에서 통과했다 — 이 테스트는 아무것도 지키지 않는다"
    assert "절단판만" in report.mismatches[0]
    # 소급 취소는 **지우는** 방향이라 전체 데이터 쪽이 비어 있다.
    assert report.compared == 0
    assert report.ghost_zone_cuts == [], "유령 존은 이 돌연변이와 무관해야 한다"


# --------------------------------------------------------------------------- #
# 8. 거래 층 — 차이가 나더라도 셋업 층까지 내려가지 않는다
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("seed", "swing_period"), _SYNTHETIC_FIXTURES)
def test_trade_layer_differences_never_reach_the_setup_layer(seed: int, swing_period: int) -> None:
    """시퀀서 층의 차이는 허용하되 **셋업 층까지 내려가면** 실패한다.

    정본 시퀀서는 같은 시각에 열린 후보들 사이에서 **청산 시각으로 동점을 가른다** —
    미래 정보이고, WAN-181이 이미 「어떤 온라인 집행과도 벌어지는 바닥 잡음」으로 기록한
    별개 부류다. 잘린 창에서는 미해결 후보의 청산 시각이 데이터 끝으로 당겨져 그 동점
    처리가 달라질 수 있다. 그래서 이 자는 거래 층 차이 자체를 금지하지 않고, **그 차이가
    셋업 층의 차이를 동반하지 않는다**는 것만 건다(그 동반이 곧 WAN-364 부류다).
    """
    minutes = _synthetic_1m(seed, swing_period)
    params = _engine_params()
    cfg = build_config(_SYNTHETIC_TF, funding_enabled=False)
    full_htf = aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False)
    full_candidates, _stats = build_zone_limit_candidates(
        full_htf, minutes, _SYNTHETIC_TF, params=params, cfg=cfg
    )
    full_result, _ = run_zone_limit_backtest_verbose(
        full_htf, minutes, _SYNTHETIC_TF, confluence_params=params, backtest_config=cfg
    )

    for cut in intrabar_cuts_for(full_candidates, _SYNTHETIC_TF):
        cut_htf, cut_1m = cut_world_intrabar(minutes, _SYNTHETIC_TF, cut)
        cut_result, _ = run_zone_limit_backtest_verbose(
            cut_htf, cut_1m, _SYNTHETIC_TF, confluence_params=params, backtest_config=cfg
        )
        if _trade_keys(full_result.trades, cut) == _trade_keys(cut_result.trades, cut):
            continue
        cut_candidates, _ = build_zone_limit_candidates(
            cut_htf, cut_1m, _SYNTHETIC_TF, params=params, cfg=cfg
        )
        assert _setup_keys(full_candidates, cut) == _setup_keys(cut_candidates, cut), (
            f"T={cut}: 거래 층 차이가 셋업 층까지 내려갔다 — 시퀀서 동점 처리가 아니라 "
            "엔진 결정의 룩어헤드다"
        )


def test_the_only_trade_layer_gap_is_the_sequencer_tie_break() -> None:
    """제외한 부류를 **구체적으로** 못 박는다 — 조용한 구멍으로 남기지 않는다.

    seed 13 합성 시장에는 같은 시각에 열리는 후보가 셋 있고, 잘린 창에서 그중 둘의 청산이
    미확정(`END_OF_DATA`)이 되면서 시퀀서가 다른 후보를 고른다 — 그래서 절단 이전에 끝난
    거래 하나가 거래 층에서 사라진다. **셋업 층은 그대로**이므로 WAN-364 부류가 아니다.

    🚨 시퀀서가 언젠가 미래를 안 보는 온라인 방식이 되면 이 테스트는 실패한다 — 그때는 이
    테스트를 지우고 `docs/decisions/wan377.md` §범위의 시퀀서 예외도 함께 걷어낼 것.
    """
    minutes = _synthetic_1m(13, 90)
    params = _engine_params()
    cfg = build_config(_SYNTHETIC_TF, funding_enabled=False)
    full_htf = aggregate_1m(minutes, _SYNTHETIC_TF, allow_partial=False)
    full_candidates, _stats = build_zone_limit_candidates(
        full_htf, minutes, _SYNTHETIC_TF, params=params, cfg=cfg
    )
    full_result, _ = run_zone_limit_backtest_verbose(
        full_htf, minutes, _SYNTHETIC_TF, confluence_params=params, backtest_config=cfg
    )

    explained = 0
    for cut in intrabar_cuts_for(full_candidates, _SYNTHETIC_TF):
        cut_htf, cut_1m = cut_world_intrabar(minutes, _SYNTHETIC_TF, cut)
        cut_result, _ = run_zone_limit_backtest_verbose(
            cut_htf, cut_1m, _SYNTHETIC_TF, confluence_params=params, backtest_config=cfg
        )
        lost = set(_trade_keys(full_result.trades, cut)) - set(_trade_keys(cut_result.trades, cut))
        if not lost:
            continue
        cut_candidates, _ = build_zone_limit_candidates(
            cut_htf, cut_1m, _SYNTHETIC_TF, params=params, cfg=cfg
        )
        assert _setup_keys(full_candidates, cut) == _setup_keys(cut_candidates, cut)
        for key in lost:
            entry_time = key[1]
            rivals = [
                candidate
                for candidate in cut_candidates
                if candidate.entry_time == entry_time and candidate.reason is ExitReason.END_OF_DATA
            ]
            assert rivals, (
                f"T={cut}: 사라진 거래 {key!r}를 설명할 미해결 경쟁 후보가 없다 — "
                "시퀀서 동점 처리로 설명되지 않는 차이다"
            )
        explained += 1
    assert explained, "시퀀서 동점 차이가 한 건도 재현되지 않았다 — 위 독스트링의 🚨를 읽을 것"
