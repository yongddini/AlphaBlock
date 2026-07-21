"""WAN-149 §4: 존 단위 생애 추적 — 생존 분석이 실제로 편향을 걷어내는가.

이 파일이 지키는 것은 **검열 처리**다. 창 끝까지 살아 있는 존을 "안 죽었다"로 세면
수명이 부풀려지고, 회차별 단순 비율은 이 측정이 고치려는 바로 그 편향을 재생산한다.
그래서 테스트는 손으로 만든 존 몇 개로 위험률·KM 곡선의 값을 **직접** 확인한다 —
"돌아가긴 한다"가 아니라 "숫자가 맞다"를 고정한다.

§3(격자)의 표본 게이트도 여기서 함께 본다: 4h·1d가 판정문 대신 「⚠️ 판정 불가(대조군)」를
받는 것이 주의문이 아니라 **코드**임을 확인한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtest.wan149_merge_grid import MERGE_OFF, MERGE_ON, arm_summary
from backtest.wan149_merge_grid import verdict as grid_verdict
from backtest.wan149_zone_lifetime import (
    MIN_ZONES,
    ZoneLife,
    _count_before,
    _zone_id,
    gap_frame,
    hazard_table,
    lives_to_frame,
    trend_test,
    verdict,
)


def _life(
    *,
    entries: int,
    taps: int | None = None,
    broken: bool,
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    zone_id: int = 0,
    fillable: int | None = None,
) -> ZoneLife:
    tap_count = entries if taps is None else taps
    fillable_count = entries if fillable is None else fillable
    return ZoneLife(
        symbol=symbol,
        timeframe=timeframe,
        zone_id=zone_id,
        direction="bull",
        confirmed_time=0,
        top=110.0,
        bottom=100.0,
        width_atr=1.0,
        ob_volume=1.0,
        tap_count=tap_count,
        fillable_count=fillable_count,
        entry_count=entries,
        broken=broken,
        break_time=1 if broken else None,
        death_after_entries=entries if broken else None,
        death_after_taps=tap_count if broken else None,
    )


# --------------------------------------------------------------------------- #
# 존 식별
# --------------------------------------------------------------------------- #


def test_zone_id_rejects_merged_keys() -> None:
    """병합 존(원소 여럿)은 존 식별이 **정의되지 않는다** — 조용히 첫 원소를 집지 않는다.

    집으면 클러스터 하나가 여러 존으로 쪼개져 위험률이 말없이 틀린다. 이 모듈이 §1의
    분리 전환 위에 서 있다는 사실이 여기서 코드로 드러난다.
    """
    assert _zone_id(frozenset({3})) == 3
    assert _zone_id(frozenset({3, 4})) is None
    assert _zone_id(None) is None


def test_count_before_excludes_events_after_invalidation() -> None:
    """존이 뚫린 **뒤**의 탭은 그 존의 생애가 아니다(breaker를 되쓸린 움직임이다).

    경계가 흐려지면 사망 회차가 뒤로 밀려 위험률이 낮게 나온다.
    """
    assert _count_before([1, 5, 9], 6) == 2
    assert _count_before([1, 5, 9], None) == 3
    assert _count_before([6], 6) == 0  # 무효화 봉 자체는 생애 밖.


# --------------------------------------------------------------------------- #
# 위험률 · Kaplan-Meier
# --------------------------------------------------------------------------- #


def test_hazard_table_treats_survivors_as_censored_not_as_zero_deaths() -> None:
    """검열 처리의 전부이자 핵심 — 생존자는 `at_risk`에만 들어가고 `deaths`에는 없다.

    존 4개: 진입 0회에 죽음 · 진입 1회에 죽음 · 진입 1회 후 창 끝까지 생존(검열) ·
    진입 2회에 죽음.
    """
    lives = [
        _life(entries=0, broken=True),
        _life(entries=1, broken=True),
        _life(entries=1, broken=False),
        _life(entries=2, broken=True),
    ]
    table = hazard_table(lives)
    assert [(r.level, r.at_risk, r.deaths) for r in table] == [(0, 4, 1), (1, 3, 1), (2, 1, 1)]
    assert table[0].hazard == pytest.approx(0.25)
    assert table[1].hazard == pytest.approx(1 / 3)
    assert table[2].hazard == pytest.approx(1.0)
    # KM = Π(1 − h) — 검열된 존은 분모에서 빠지되 죽음으로 세지지 않는다.
    assert table[1].survival == pytest.approx(0.75 * (2 / 3))
    assert table[2].survival == pytest.approx(0.0)


def test_naive_ratio_would_disagree_with_the_censored_hazard() -> None:
    """단순 비율표가 왜 미충족인지를 숫자로 남긴다.

    "1회 진입한 존 중 몇 %가 죽었나"를 소박하게 세면 검열된 존이 분모에 들어가 **낮게**
    나온다. 위험률은 그 존을 그 회차 이후로 넘겨 보내므로 값이 다르다.
    """
    lives = [
        _life(entries=1, broken=True),
        _life(entries=1, broken=False),
        _life(entries=3, broken=True),
    ]
    naive = sum(1 for x in lives if x.broken and x.entry_count == 1) / sum(
        1 for x in lives if x.entry_count == 1
    )
    hazard_at_1 = hazard_table(lives)[1].hazard
    assert naive == pytest.approx(0.5)
    assert hazard_at_1 == pytest.approx(1 / 3)
    assert naive != pytest.approx(hazard_at_1)


def test_tap_axis_is_available_but_separate() -> None:
    """탭 축은 보조다 — 진입 축과 **다른 곡선**이 나올 수 있어야 병기가 의미를 갖는다."""
    lives = [_life(entries=0, taps=3, broken=True), _life(entries=1, taps=4, broken=True)]
    entry_levels = [r.level for r in hazard_table(lives, axis="entry")]
    tap_levels = [r.level for r in hazard_table(lives, axis="tap")]
    assert entry_levels == [0, 1]
    assert tap_levels == [0, 1, 2, 3, 4]
    with pytest.raises(ValueError, match="알 수 없는 축"):
        hazard_table(lives, axis="fill")


# --------------------------------------------------------------------------- #
# 추세 검정
# --------------------------------------------------------------------------- #


def test_trend_test_detects_a_rising_hazard() -> None:
    """위험률이 회차와 함께 오르도록 만든 자료에서 기울기가 양수로 나와야 한다."""
    lives: list[ZoneLife] = []
    for i in range(200):
        # 회차가 오를수록 죽을 확률이 커지는 구조: 대부분 0~1회에서 살아남고 높은 회차는 죽는다.
        lives.append(_life(entries=i % 4, broken=(i % 4) >= 2, zone_id=i))
    result = trend_test(lives, iterations=200)
    assert result.slope is not None and result.slope > 0
    assert result.increasing


def test_trend_test_is_flat_when_hazard_is_constant() -> None:
    """**진짜로 평평한** 위험률에서는 구간이 0을 품어야 한다(= 가설 기각 쪽).

    회차마다 위험률 1/2인 기하 분포를 손으로 깐다 — 남은 존의 절반이 그 회차에서
    죽고 나머지가 다음 회차로 넘어간다. 이 모양에서 기울기가 유의하게 양수로 나오면
    검정이 잡음을 추세로 읽는 것이다.
    """
    lives: list[ZoneLife] = []
    remaining, zone_id = 256, 0
    level = 0
    while remaining > 1:
        deaths = remaining // 2
        for _ in range(deaths):
            lives.append(_life(entries=level, broken=True, zone_id=zone_id))
            zone_id += 1
        remaining -= deaths
        level += 1
    for _ in range(remaining):  # 마지막 생존자는 검열.
        lives.append(_life(entries=level, broken=False, zone_id=zone_id))
        zone_id += 1
    result = trend_test(lives, iterations=200)
    assert not result.increasing


def test_trend_test_degrades_gracefully_without_events() -> None:
    """전부 생존(또는 전부 사망)이면 기울기가 무한이라 `None`을 돌려준다.

    큰 유한값으로 적으면 부트스트랩 평균이 조용히 부풀려진다.
    """
    result = trend_test([_life(entries=1, broken=False) for _ in range(10)], iterations=10)
    assert result.slope is None
    assert result.p_value is None


# --------------------------------------------------------------------------- #
# 판정 게이트
# --------------------------------------------------------------------------- #


def test_lifetime_verdict_gates_control_timeframes() -> None:
    """4h·1d는 작업 TF가 아니므로 판정문 대신 「판정 불가(대조군)」를 받는다.

    게이트가 없으면 대조군 열이 판정문을 갖게 되고, 그 문장은 다음 이슈에서 근거로
    재인용된다 — 이 저장소가 여러 번 겪은 실패다.
    """
    lives = [
        _life(entries=i % 4, broken=(i % 3 == 0), timeframe="4h", zone_id=i) for i in range(300)
    ]
    assert "판정 불가(대조군)" in verdict(lives, "4h")


def test_lifetime_verdict_gates_small_zone_samples() -> None:
    lives = [_life(entries=i % 3, broken=(i % 2 == 0), zone_id=i) for i in range(MIN_ZONES - 1)]
    assert "판정 불가(대조군)" in verdict(lives, "1h")


def test_lifetime_verdict_judges_working_timeframes() -> None:
    lives = [_life(entries=i % 4, broken=(i % 4) >= 2, zone_id=i) for i in range(400)]
    text = verdict(lives, "1h")
    assert "판정 불가" not in text
    assert text.startswith("**(a)") or text.startswith("**(b)")


# --------------------------------------------------------------------------- #
# 간극 표 — 「한 번도 못 들어간 채 뚫린 존」을 반드시 센다
# --------------------------------------------------------------------------- #


def test_gap_frame_counts_zones_broken_without_a_single_entry() -> None:
    """손익표에는 안 잡히지만 **빼면 또 다른 생존 편향**이 되는 그 존들(코멘트 §2)."""
    lives = [
        _life(entries=0, taps=2, fillable=0, broken=True),
        _life(entries=0, taps=1, fillable=0, broken=False),
        _life(entries=2, taps=3, fillable=2, broken=True),
    ]
    row = gap_frame(lives).iloc[0]
    assert row["zones"] == 3
    assert row["broken"] == 2
    assert row["never_entered_broken"] == 1
    assert row["never_entered_broken%"] == pytest.approx(50.0)


def test_frame_roundtrip_keeps_every_column() -> None:
    frame = lives_to_frame([_life(entries=1, broken=True)])
    assert "death_after_entries" in frame.columns
    assert "entry_count" in frame.columns
    assert frame.iloc[0]["death_after_entries"] == 1


# --------------------------------------------------------------------------- #
# §3 격자의 표본 게이트
# --------------------------------------------------------------------------- #


def _grid_rows(timeframe: str, trades: int) -> pd.DataFrame:
    """(TF, 구간, 팔, 심볼) 요약을 직접 만들어 게이트만 본다."""
    records = []
    for segment in ("is", "oos"):
        for combine in (MERGE_ON, MERGE_OFF):
            for index in range(6):
                records.append(
                    {
                        "timeframe": timeframe,
                        "segment": segment,
                        "combine_obs": combine,
                        "symbol": f"S{index}",
                        "total_return": 0.1 if combine is MERGE_OFF else 0.05,
                        "win_rate": 0.5,
                        "max_drawdown": 0.1,
                        "num_trades": trades,
                        "fill_rate": 0.8,
                    }
                )
    return pd.DataFrame(records)


class _Rows:
    """`arm_summary`가 기대하는 최소 인터페이스(행 → dict)."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def model_dump(self) -> dict[str, object]:  # pragma: no cover - 사용되지 않음
        raise NotImplementedError


def test_grid_verdict_gates_control_timeframes(monkeypatch: pytest.MonkeyPatch) -> None:
    """4h가 OOS 심볼당 20거래에 못 미치면 코드가 문장을 바꾼다(WAN-143 게이트 재사용)."""
    import backtest.wan149_merge_grid as module

    monkeypatch.setattr(module, "per_symbol", lambda rows: rows)
    summary = arm_summary(_grid_rows("4h", trades=10))
    assert "판정 불가(대조군)" in grid_verdict(summary, "4h")


def test_grid_verdict_judges_when_sample_suffices(monkeypatch: pytest.MonkeyPatch) -> None:
    import backtest.wan149_merge_grid as module

    monkeypatch.setattr(module, "per_symbol", lambda rows: rows)
    summary = arm_summary(_grid_rows("1h", trades=50))
    text = grid_verdict(summary, "1h")
    assert "판정 불가" not in text
    assert "(a)" in text  # 두 구간 모두 분리 팔이 이기도록 만든 자료.


def test_trend_test_point_estimate_only_when_iterations_is_zero() -> None:
    """`iterations=0`은 **점추정만** 원한다는 뜻이다(leave-one-out이 그렇게 쓴다).

    가드를 `len(draws) < iterations // 2`만으로 두면 `0 < 0`이 거짓이라 빈 부트스트랩
    분포를 인덱싱해 `IndexError`로 죽는다 — 라벨링을 다 끝낸 뒤 요약 렌더에서 터진다.
    """
    lives = [_life(entries=i % 4, broken=(i % 4) >= 2, zone_id=i) for i in range(120)]
    result = trend_test(lives, iterations=0)
    assert result.slope is not None
    assert result.ci_low is None and result.ci_high is None and result.p_value is None
    assert not result.increasing  # 구간이 없으면 "오른다"고 주장하지 않는다.


def test_trend_ignores_the_level_zero_population_switch() -> None:
    """🚨 이 모듈에서 가장 중요한 방어 — `h(0)`은 **다른 모집단**이라 판정에서 뺀다.

    `h(0)`의 위험집합은 탐지된 **모든** 존이고 대부분은 **진입조차 못 한 존**이다.
    `h(1)` 이상은 실제로 진입한 존이다. 두 칸을 한 회귀에 넣으면 그 **모집단 교체가
    기울기로 둔갑**한다 — 실데이터 1h가 정확히 그랬다(전곡선 +0.138 p=0.001 → 회차 ≥1만
    보면 +0.009).

    여기서는 그 상황을 손으로 만든다: 회차 0에서만 위험률이 낮고 1 이상은 **완전히
    평평한** 자료. 기본 판정은 (b)여야 하고, `min_level=0`을 명시로 요청해야만 그
    가짜 상승이 보인다.
    """
    lives: list[ZoneLife] = []
    zone_id = 0
    # 회차 0에서 200개 중 20개만 죽는다(h(0)=10%) — 나머지는 진입으로 넘어간다.
    for _ in range(20):
        lives.append(_life(entries=0, broken=True, zone_id=zone_id))
        zone_id += 1
    # 회차 1~4는 매 회차 위험률 **정확히 1/2**로 일정하다(남은 절반이 그 회차에서 죽는다).
    remaining = 180
    for level in range(1, 5):
        deaths = remaining // 2
        for _ in range(deaths):
            lives.append(_life(entries=level, broken=True, zone_id=zone_id))
            zone_id += 1
        remaining -= deaths
    for _ in range(remaining):  # 남은 존은 검열.
        lives.append(_life(entries=5, broken=False, zone_id=zone_id))
        zone_id += 1

    judged = trend_test(lives, iterations=200)
    full = trend_test(lives, min_level=0, iterations=200)
    assert not judged.increasing, "회차 ≥1이 평평하면 가설은 기각이다"
    assert full.slope is not None and judged.slope is not None
    assert full.slope > judged.slope, "전곡선은 h(0) 한 칸 때문에 더 가파르게 보인다"
    assert "판정 불가" not in verdict(lives, "1h")
    assert "**(b)" in verdict(lives, "1h")
