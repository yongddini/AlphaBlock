"""펀딩 신선도가 화면에 보이고, 파리티 경고가 거짓말을 하지 않는다 (WAN-407).

이 저장소가 반복해 데인 실패는 **「실패가 성공과 같은 모양」**(WAN-194/318 §3/321)이다.
WAN-407은 그 실패의 펀딩 축 두 겹을 고친다:

* **§1** — `alphablock parity`가 **펀딩이 100% 채워진 창에서도** 「커버리지 0.0%」를 경고했다.
  대조 셀을 `funding=False`로 로드하면서 cfg는 채택 기본값(`funding_enabled=True`)이라,
  엔진이 「펀딩을 쓴다」고 믿고 **빈 시계열**의 커버리지를 잰 탓이다. 거짓 경보가 실제로
  「펀딩 수집이 멈췄다」는 의심의 유일한 근거였다.
* **§2** — 진짜 정지는 반대로 **아무 데도 안 보였다**. `HealthView.funding`은 이미 계산돼
  종합 배지에까지 들어가는데 CLI가 OHLCV만 그려서, 펀딩이 멈추면 **배지만 노랗게 변하고
  이유는 어디에도 없었다**.

⚠️ §1 테스트는 `alphablock parity`가 옛 규칙 페이퍼와 함께 은퇴하며 지웠다(WAN-451 ②).

테스트는 **라벨이 아니라 동작으로** 건다 — 로그가 실제로 나오는지/안 나오는지, 화면 문자열에
그 심볼이 실제로 뜨는지. 돌연변이 확인(옛 코드로 되돌리면 실패)도 함께 건다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from cli.main import _funding_census_lines, format_status
from dashboard.health import FundingFreshness, HealthLevel
from dashboard.health_data import HealthView, build_health_view
from data.funding import FundingRateStore
from data.models import FundingRate

_NOW = 1_700_000_000_000
_HOUR_MS = 3_600_000
_SYMBOL = "BTC/USDT:USDT"


# ---------------------------------------------------------------------------
# §2 — 펀딩 신선도가 화면에 뜬다
# ---------------------------------------------------------------------------


def _view_with_funding(rows: list[FundingFreshness]) -> HealthView:
    from dashboard.health import CollectorStatus, OverallBadge, RunnerStatus

    return HealthView(
        now_ms=_NOW,
        overall=OverallBadge(level=HealthLevel.OK, label="정상"),
        freshness=[],
        funding=rows,
        collector=CollectorStatus(
            ran=False, last_beat_ms=None, lag_ms=None, level=HealthLevel.UNKNOWN
        ),
        runner=RunnerStatus(
            ran=False,
            last_poll_ms=None,
            last_notification_ms=None,
            lag_ms=None,
            level=HealthLevel.UNKNOWN,
        ),
        positions=[],
        recent_events=[],
    )


def _funding_row(*, symbol: str, funding_time: int | None, level: HealthLevel) -> FundingFreshness:
    return FundingFreshness(
        symbol=symbol,
        rate=0.0001,
        funding_time=funding_time,
        next_funding_time=None,
        is_predicted=False,
        lag_ms=None if funding_time is None else _NOW - funding_time,
        level=level,
    )


def test_status_shows_stale_funding_with_a_warning() -> None:
    """펀딩이 멈추면 `status`가 그 심볼과 경고를 **글자로** 낸다(WAN-407 §2)."""
    view = _view_with_funding(
        [
            _funding_row(
                symbol=_SYMBOL, funding_time=_NOW - 7 * 24 * _HOUR_MS, level=HealthLevel.STALE
            )
        ]
    )
    text = format_status(view)
    assert "펀딩 신선도:" in text
    assert _SYMBOL in text.split("펀딩 신선도:", 1)[1]
    assert "0으로 계산" in text, "롱 온리에 유리한 방향이라는 경고가 빠졌다"


def test_status_is_quiet_when_funding_is_fresh() -> None:
    """정상 상태를 빨간불로 만들지 않는다(WAN-321이 고친 실패 부류)."""
    view = _view_with_funding(
        [_funding_row(symbol=_SYMBOL, funding_time=_NOW - _HOUR_MS, level=HealthLevel.OK)]
    )
    section = format_status(view).split("펀딩 신선도:", 1)[1]
    assert "0으로 계산" not in section
    assert "[STALE]" not in section


def test_status_names_symbols_with_no_funding_at_all() -> None:
    """행이 하나도 없는 종목은 「없음」으로 **보인다** — 조용히 0으로 세지 않는다."""
    view = _view_with_funding(
        [_funding_row(symbol=_SYMBOL, funding_time=None, level=HealthLevel.UNKNOWN)]
    )
    assert "저장된 펀딩 없음" in format_status(view)


def test_status_funding_follows_the_configured_universe(tmp_path: Path) -> None:
    """판정 대상은 **설정 유니버스**다 — 옛 유니버스 잔재로 상시 빨간불을 만들지 않는다.

    저장된 펀딩에는 「수집 대상이 아닌」 심볼이 섞여 있을 수 있다(유니버스가 바뀌면 옛 심볼의
    시계열이 그대로 남는다). 그걸 판정하면 영원히 STALE이라 진짜 정지와 구분되지 않는다.
    """
    db_path = str(tmp_path / "ohlcv.db")
    with FundingRateStore(db_path) as store:
        store.upsert_rates(
            [
                FundingRate(
                    symbol=sym,
                    funding_time=_NOW - _HOUR_MS,
                    rate=0.0001,
                    mark_price=100.0,
                    next_funding_time=_NOW + 7 * _HOUR_MS,
                    is_predicted=False,
                )
                for sym in (_SYMBOL, "LEGACY/USDT:USDT")
            ]
        )
    view = build_health_view(
        db_path,
        runtime_state_path=str(tmp_path / "missing.json"),
        poll_interval_seconds=60,
        stale_multiplier=2.5,
        funding_symbols=[_SYMBOL],
        now_ms=_NOW,
    )
    symbols = {row.symbol for row in view.funding}
    assert symbols == {_SYMBOL}, "설정에 없는 옛 심볼이 판정 대상에 섞였다"


def test_doctor_funding_census_is_quiet_when_fresh_and_loud_when_stale(tmp_path: Path) -> None:
    """doctor의 펀딩 절도 같은 자를 쓴다 — 그리고 **종료 코드는 안 건드린다**."""
    from config.settings import Settings

    db_path = str(tmp_path / "ohlcv.db")
    settings = Settings(db_path=db_path, symbols=[_SYMBOL])

    with FundingRateStore(db_path) as store:
        store.upsert_rates(
            [
                FundingRate(
                    symbol=_SYMBOL,
                    funding_time=int(pd.Timestamp.now("UTC").timestamp() * 1000) - _HOUR_MS,
                    rate=0.0001,
                    mark_price=100.0,
                    next_funding_time=None,
                    is_predicted=False,
                )
            ]
        )
    fresh = _funding_census_lines(db_path, settings)
    assert fresh and "전부 최신" in fresh[0]

    stale_db = str(tmp_path / "stale.db")
    with FundingRateStore(stale_db) as store:
        store.upsert_rates(
            [
                FundingRate(
                    symbol=_SYMBOL,
                    funding_time=int(pd.Timestamp.now("UTC").timestamp() * 1000)
                    - 30 * 24 * _HOUR_MS,
                    rate=0.0001,
                    mark_price=100.0,
                    next_funding_time=None,
                    is_predicted=False,
                )
            ]
        )
    stale = _funding_census_lines(stale_db, settings)
    assert any(_SYMBOL in line for line in stale)
    assert any("종료 코드에는 반영하지 않습니다" in line for line in stale)
