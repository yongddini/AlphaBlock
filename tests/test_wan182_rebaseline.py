"""WAN-182 재-베이스라인(채택 유니버스 9종목 · 못 박은 6년 창 · 작업 TF 15m/1h/4h) 회귀.

라벨이 아니라 **동작**으로 고정한다(WAN-91/95/112 부류의 조용한 실패 방지):

- 새 기본값이 실제로 9종목 × 15m·1h·4h × 못 박은 채택 창을 돈다(CLI 파서 → 격자·옵션).
- `--years`를 명시하면 옛 미끄러지는 창이 그대로 나온다(채택 창이 덮지 않는다).
- harness 기본값을 직접 읽던 옛 리포트(wan104/wan108)는 `LEGACY_*` 좌표로 핀됐다.
- 채택 성과 리포트(wan95)는 새 좌표를 **따라간다**(핀 반대 방향 — "지금 채택된 것"을
  재는 리포트는 고정하지 않는다).
- 신규 3종목 펀딩 대리(WAN-180 규칙)는 **데이터가 없을 때만** 얹힌다 — 자기 데이터가
  생기면(WAN-178 백필) 저절로 아무것도 하지 않는다.
"""

from __future__ import annotations

import inspect

from backtest import harness
from backtest.run import build_parser, grid_from_args, options_from_args, parse_date_ms
from backtest.wan95_zone_limit_report import NEW_SYMBOLS, apply_funding_proxy, collect_rows
from config.settings import _default_live_signal_symbols, _default_symbols
from data.models import FundingRate

_NINE = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "TRX/USDT:USDT",
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
)


# ---------------------------------------------------------------- 채택 좌표


def test_adopted_coordinates_are_nine_symbols_three_tfs_pinned_window() -> None:
    """채택 좌표(WAN-179 결정): 9종목 · 15m/1h/4h · 2020-09-15~2026-07-22."""
    assert harness.DEFAULT_SYMBOLS == _NINE
    assert harness.DEFAULT_TIMEFRAMES == ("15m", "1h", "4h")
    assert harness.DEFAULT_START == "2020-09-15"
    assert harness.DEFAULT_END == "2026-07-22"


def test_bare_cli_actually_runs_adopted_coordinates() -> None:
    """인자 없는 `backtest.run`이 실제로 9종목 × 3TF × 못 박은 채택 창을 돈다.

    격자(심볼·TF)와 옵션(창)을 **파서 산출물에서** 확인한다 — 상수가 바뀌어도 CLI가
    옛 좌표를 물고 돌면(배선 누락) 여기서 걸린다.
    """
    args = build_parser().parse_args([])
    grid = grid_from_args(args)
    assert len(grid.symbols) == 9
    assert set(grid.symbols) == set(_NINE)
    assert grid.timeframes == ("15m", "1h", "4h")

    options = options_from_args(args)
    assert options.start_ms == parse_date_ms("2020-09-15")
    assert options.end_ms == parse_date_ms("2026-07-22")


def test_explicit_years_keeps_sliding_window() -> None:
    """`--years N`을 명시하면 옛 미끄러지는 창 그대로다 — 채택 창이 덮어쓰지 않는다."""
    args = build_parser().parse_args(["--years", "2"])
    options = options_from_args(args)
    assert options.years == 2.0
    assert options.start_ms is None
    assert options.end_ms is None


def test_explicit_start_end_wins_over_adopted_window() -> None:
    """`--start`/`--end` 명시가 채택 창을 대체한다(부분 명시는 부분만)."""
    args = build_parser().parse_args(["--start", "2024-01-01"])
    options = options_from_args(args)
    assert options.start_ms == parse_date_ms("2024-01-01")
    # 사용자가 끝을 명시하지 않았다 — 채택 창의 끝을 몰래 얹지 않는다(열린 끝 유지).
    assert options.end_ms is None


# ------------------------------------------------------- 수집 대상 · 실거래 불변


def test_collection_universe_is_nine_but_live_signal_stays_btc_only() -> None:
    """수집 유니버스는 9종목, 실시간 시그널 대상은 BTC 단독 그대로다(WAN-111 원칙).

    유니버스 확장은 측정·수집 대상이지 실거래 승인이 아니다.
    """
    assert tuple(_default_symbols()) == _NINE
    assert _default_live_signal_symbols() == ["BTC/USDT:USDT"]


# ------------------------------------------------------------ 옛 리포트 핀


def test_legacy_coordinates_pin_the_old_defaults() -> None:
    """`LEGACY_*` = WAN-181까지의 기본 좌표(3심볼 × 1h × 최근 3년) 스냅샷."""
    assert harness.LEGACY_SYMBOLS == ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
    assert harness.LEGACY_TIMEFRAMES == ("1h",)
    assert harness.LEGACY_YEARS == 3.0


def test_wan104_and_wan108_are_pinned_to_legacy_coordinates() -> None:
    """harness 기본값을 직접 읽던 두 모듈은 옛 좌표로 핀됐다(WAN-182 파급).

    핀이 없으면 이 모듈들은 다음 실행에서 조용히 9종목/6년으로 돌아 본문(3심볼/3년
    수치가 결론에 박혀 있다)과 어긋난다.
    """
    from backtest import wan104_offset_incremental_report as wan104
    from backtest import wan108_multi_position_reappraisal as wan108

    assert wan104.DEFAULT_SYMBOLS == harness.LEGACY_SYMBOLS
    assert wan104.DEFAULT_YEARS == harness.LEGACY_YEARS
    assert wan108.DEFAULT_YEARS == harness.LEGACY_YEARS
    # 다른 옛 리포트(wan70/81/84/88 등)는 좌표를 자기 모듈 상수로 들고 있어 이 전환의
    # 영향을 받지 않는다 — 대표로 wan81이 여전히 3심볼/3년임을 확인한다.
    from backtest import wan81_engine_replacement_report as wan81

    assert wan81.DEFAULT_SYMBOLS == harness.LEGACY_SYMBOLS
    assert wan81.DEFAULT_YEARS == harness.LEGACY_YEARS


def test_wan95_tracks_the_adopted_coordinates() -> None:
    """채택 성과 리포트(wan95)는 핀 반대 방향 — 새 좌표를 따라간다.

    "지금 채택된 것"을 재는 리포트는 고정하지 않는다(기본값이 움직이면 그 수치는 낡은
    것이 되어야 맞다). 기본 인자가 harness 채택 좌표와 같은지 **시그니처에서** 확인한다.
    """
    defaults = {name: p.default for name, p in inspect.signature(collect_rows).parameters.items()}
    assert defaults["symbols"] == harness.DEFAULT_SYMBOLS
    assert defaults["timeframes"] == harness.DEFAULT_TIMEFRAMES
    assert defaults["start"] == harness.DEFAULT_START
    assert defaults["end"] == harness.DEFAULT_END


# ------------------------------------------------------------ 펀딩 대리


def _rate(symbol: str, time_ms: int, rate: float, *, predicted: bool = False) -> FundingRate:
    return FundingRate(symbol=symbol, funding_time=time_ms, rate=rate, is_predicted=predicted)


def test_funding_proxy_fills_empty_new_symbols_with_highest_funding_series() -> None:
    """신규 종목의 **빈** 펀딩만 기존 종목 중 확정 펀딩 평균 최고의 시계열로 채운다."""
    btc = [_rate("BTC/USDT:USDT", t, 0.0001) for t in (0, 1, 2)]
    trx = [_rate("TRX/USDT:USDT", t, 0.0003) for t in (0, 1, 2)]
    link_own = [_rate("LINK/USDT:USDT", 0, 0.0002)]
    funding = {
        "BTC/USDT:USDT": btc,
        "TRX/USDT:USDT": trx,
        "DOGE/USDT:USDT": [],
        "LINK/USDT:USDT": link_own,
        "LTC/USDT:USDT": [],
    }
    out, note = apply_funding_proxy(funding)
    # DOGE·LTC(빈 것)만 TRX(평균 최고) 시계열을 받는다.
    assert out["DOGE/USDT:USDT"] == trx
    assert out["LTC/USDT:USDT"] == trx
    # 자기 데이터가 있는 신규 종목은 덮지 않는다(대리는 교정이지 데이터 교체가 아니다).
    assert out["LINK/USDT:USDT"] == link_own
    # 기존 종목은 손대지 않는다.
    assert out["BTC/USDT:USDT"] == btc
    assert "TRX" in note and "DOGE" in note and "LTC" in note and "LINK" not in note


def test_funding_proxy_is_noop_when_new_symbols_have_their_own_data() -> None:
    """WAN-178 백필이 끝나면(신규 종목에 자기 데이터) 대리는 저절로 꺼진다."""
    funding = {
        "BTC/USDT:USDT": [_rate("BTC/USDT:USDT", 0, 0.0001)],
        "DOGE/USDT:USDT": [_rate("DOGE/USDT:USDT", 0, 0.00005)],
    }
    out, note = apply_funding_proxy(funding)
    assert out == funding
    assert note == ""


def test_funding_proxy_requires_confirmed_rates_for_donor_selection() -> None:
    """예측값뿐인 종목은 대리 후보가 못 된다(확정 펀딩 평균이 자다)."""
    funding = {
        "BTC/USDT:USDT": [_rate("BTC/USDT:USDT", 0, 0.01, predicted=True)],
        "DOGE/USDT:USDT": [],
    }
    out, note = apply_funding_proxy(funding)
    assert out["DOGE/USDT:USDT"] == []
    assert note == ""


def test_new_symbols_constant_matches_universe_expansion() -> None:
    """`NEW_SYMBOLS` = 유니버스 확장분(9종목 − 기존 6종목)과 정확히 일치한다."""
    assert set(NEW_SYMBOLS) == set(harness.DEFAULT_SYMBOLS) - set(
        (
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "SOL/USDT:USDT",
            "BNB/USDT:USDT",
            "XRP/USDT:USDT",
            "TRX/USDT:USDT",
        )
    )
