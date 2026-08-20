"""짝 없는 셋업의 귀속 테스트 (WAN-337 §1).

`live.unpaired_setups`의 순수 계층을 화면 없이 고정한다. 이 표가 답해야 하는 것은 하나다 —
**조인 키의 어느 조각이 갈려서 짝이 안 지어졌나**:

- `(0) 키 없음` / `(a) 존 없음` / `(b) 확정 시각` / `(c) 탭 순번`이 실제로 갈린다(완료 기준 1).
- 🚨 **재진입 행이 `(b)`로 오분류되지 않는다** — `setup_key()`는 재진입에 일부러 `None`을
  내므로(WAN-305) 그 부류를 안 빼면 재진입이 탐지 로직 결함으로 둔갑한다.
- 🚨 **짝지어진 셋업은 이 표에 안 들어온다** — 기본 표(§1 손절폭 짝)는 그대로다(WAN-333).
- 판정 한 줄이 과반 부류를 따라간다(완료 기준 2).
- 편향 점검이 짝 있는/없는 라이브 셋업의 손절폭을 나란히 낸다(완료 기준 3).
"""

from __future__ import annotations

from live.setup_compare import build_setup_comparisons
from live.trade_timeline import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    STATUS_BACKTEST_CLOSED,
    TimelineRow,
)
from live.unpaired_setups import (
    BUCKET_CONFIRM_DIFFERS,
    BUCKET_NO_KEY,
    BUCKET_TAP_DIFFERS,
    BUCKET_ZONE_MISSING,
    UnpairedReport,
    attribute_unpaired,
    render_unpaired,
)

_SYMBOL = "BTC/USDT:USDT"
_TF = "1h"
_TF_MS = 3_600_000


def _row(
    source: str,
    *,
    status: str,
    zone_start: int | None = 1_700_000_000_000,
    zone_confirmed: int | None = 1_700_003_600_000,
    tap_index: int | None = 0,
    is_reentry: bool | None = False,
    fill_price: float | None = 100.0,
    stop_price: float | None = 99.0,
    fill_ms: int | None = 1_700_007_200_000,
) -> TimelineRow:
    return TimelineRow(
        source=source,
        symbol=_SYMBOL,
        timeframe=_TF,
        is_long=True,
        status=status,
        reserve_ms=1_700_005_000_000,
        limit_price=100.0,
        fill_ms=fill_ms,
        fill_price=fill_price,
        stop_price=stop_price,
        take_profit_price=110.0,
        exit_ms=None,
        exit_price=None,
        exit_reason=None,
        pnl_pct=None,
        pnl_amount=None,
        zone_start_time=zone_start,
        zone_confirmed_time=zone_confirmed,
        tap_index=tap_index,
        is_reentry=is_reentry,
    )


def _live(
    *,
    zone_start: int | None = 1_700_000_000_000,
    zone_confirmed: int | None = 1_700_003_600_000,
    tap_index: int | None = 0,
    is_reentry: bool | None = False,
    fill_price: float | None = 100.0,
    stop_price: float | None = 99.0,
    fill_ms: int | None = 1_700_007_200_000,
) -> TimelineRow:
    return _row(
        SOURCE_LIVE,
        status="진입",
        zone_start=zone_start,
        zone_confirmed=zone_confirmed,
        tap_index=tap_index,
        is_reentry=is_reentry,
        fill_price=fill_price,
        stop_price=stop_price,
        fill_ms=fill_ms,
    )


def _bt(
    *,
    zone_start: int | None = 1_700_000_000_000,
    zone_confirmed: int | None = 1_700_003_600_000,
    tap_index: int | None = 0,
    is_reentry: bool | None = False,
    fill_price: float | None = 100.0,
    stop_price: float | None = 99.0,
    fill_ms: int | None = 1_700_007_200_000,
) -> TimelineRow:
    return _row(
        SOURCE_BACKTEST,
        status=STATUS_BACKTEST_CLOSED,
        zone_start=zone_start,
        zone_confirmed=zone_confirmed,
        tap_index=tap_index,
        is_reentry=is_reentry,
        fill_price=fill_price,
        stop_price=stop_price,
        fill_ms=fill_ms,
    )


def _attribute(
    live_rows: list[TimelineRow], bt_rows: list[TimelineRow]
) -> tuple[list[str], UnpairedReport]:
    result = build_setup_comparisons(live_rows, bt_rows)
    report = attribute_unpaired(live_rows, bt_rows, result.comparisons)
    return [one.bucket for one in report.setups], report


def test_paired_setups_never_enter_the_diagnostic() -> None:
    """짝지어진 셋업은 이 표에 안 들어온다 — 기본 표는 그대로다(WAN-333 규약)."""
    buckets, report = _attribute([_live()], [_bt()])
    assert buckets == []
    assert report.setups == ()
    assert "짝 없는 셋업이 없습니다" in render_unpaired(report)


def test_tap_index_only_difference_is_bucket_c() -> None:
    """존·확정이 같고 탭 순번만 다르면 (c) — 틱 대 1분봉 해상도(알려진 비대칭)."""
    buckets, report = _attribute([_live(tap_index=2)], [_bt(tap_index=1)])
    assert buckets == [BUCKET_TAP_DIFFERS, BUCKET_TAP_DIFFERS]  # 양쪽 다 짝이 없다
    assert {one.side for one in report.setups} == {"라이브", "백테"}
    assert all(one.tap_delta == 1 for one in report.setups)


def test_confirmed_time_difference_is_bucket_b() -> None:
    """존 시작은 같은데 확정 시각이 다르면 (b) — 탐지 로직·봉 경계."""
    buckets, report = _attribute([_live()], [_bt(zone_confirmed=1_700_003_600_000 + _TF_MS)])
    assert buckets == [BUCKET_CONFIRM_DIFFERS, BUCKET_CONFIRM_DIFFERS]
    assert all(one.confirm_delta_ms == _TF_MS for one in report.setups)


def test_missing_zone_is_bucket_a_and_carries_the_nearest_distance() -> None:
    """존 시작이 아예 다르면 (a) — 다만 **얼마나 먼지**를 함께 싣는다(버킷은 안 흐린다)."""
    buckets, report = _attribute(
        [_live(zone_start=1_700_000_000_000)],
        [_bt(zone_start=1_700_000_000_000 - 5 * _TF_MS, zone_confirmed=1_700_003_600_000)],
    )
    assert buckets == [BUCKET_ZONE_MISSING, BUCKET_ZONE_MISSING]
    live_one = next(o for o in report.setups if o.side == "라이브")
    assert live_one.nearest_zone_delta_ms == 5 * _TF_MS
    assert live_one.near_miss_bars == 5.0
    assert not live_one.near_miss  # 5봉은 근접이 아니다


def test_one_bar_off_detection_is_flagged_as_a_near_miss() -> None:
    """🚨 (a)인데 최근접 백테 존이 한 봉 옆이면 원인은 (b)에 가깝다 — 증거를 붙여 센다."""
    buckets, report = _attribute(
        [_live(zone_start=1_700_000_000_000)],
        [_bt(zone_start=1_700_000_000_000 - _TF_MS, zone_confirmed=1_700_003_600_000)],
    )
    assert buckets == [BUCKET_ZONE_MISSING, BUCKET_ZONE_MISSING]
    assert report.near_misses == 2
    assert "원인은 (b)에 가깝습니다" in render_unpaired(report)


def test_backtest_never_looked_at_the_cell_is_distinguishable() -> None:
    """같은 칸에 반대편 행이 하나도 없으면 「그 칸을 아예 안 봤다」로 남는다(거리 없음)."""
    buckets, report = _attribute([_live()], [])
    assert buckets == [BUCKET_ZONE_MISSING]
    assert report.setups[0].nearest_zone_delta_ms is None
    assert "반대편이 그 칸을 아예 안 봄" in render_unpaired(report)


def test_reentry_row_is_bucket_zero_not_a_detection_defect() -> None:
    """🚨 핵심 회귀 — 재진입 행은 (0)이지 (b)가 아니다.

    `setup_key()`는 재진입에 **일부러** `None`을 낸다(WAN-305 — 재진입의 탭 순번이
    라이브(재무장 시점 카운트)와 백테(0)에서 다르다). 그 부류를 안 빼면 페이퍼가 채택
    규칙(WAN-273/274)대로 한 재진입 매매가 「탐지 로직 결함」으로 둔갑한다.
    """
    # 구제 조인이 짝지을 반대편이 없는 재진입 행 하나(백테는 그 칸을 안 봤다).
    buckets, report = _attribute([_live(is_reentry=True, tap_index=3)], [])
    assert buckets == [BUCKET_NO_KEY]
    assert BUCKET_CONFIRM_DIFFERS not in buckets
    assert "재진입 행(설계상 무키)" in render_unpaired(report)


def test_missing_zone_identity_is_also_bucket_zero() -> None:
    """존 정체성이 아예 없는 행(옛 장부)도 (0) — 조인이 성립하지 않는다."""
    buckets, _ = _attribute([_live(zone_start=None, zone_confirmed=None)], [])
    assert buckets == [BUCKET_NO_KEY]


def test_rescued_reentry_pairs_do_not_reach_the_diagnostic() -> None:
    """구제 조인(WAN-305)이 짝지은 재진입은 짝이 **있으므로** 이 표에 안 들어온다."""
    buckets, _ = _attribute(
        [_live(is_reentry=True, tap_index=3)], [_bt(is_reentry=True, tap_index=0)]
    )
    assert buckets == []


def test_opposite_pool_is_all_rows_not_only_the_unpaired_ones() -> None:
    """🚨 반대편 풀은 **전부**여야 한다 — 짝 없는 것만 보면 (c)가 (a)로 잘못 떨어진다.

    라이브 탭 0·1이 있고 백테는 탭 0만 아는 상황: 백테 탭 0은 라이브 탭 0과 **짝지어져**
    소비되므로, 짝 없는 라이브 탭 1은 「짝 없는 반대편」만 보면 비교 대상이 없어 (a)가 된다.
    실제로는 백테가 같은 존을 알고 있으니 (c)다.
    """
    buckets, report = _attribute(
        [_live(tap_index=0), _live(tap_index=1, fill_ms=1_700_010_800_000)],
        [_bt(tap_index=0)],
    )
    assert buckets == [BUCKET_TAP_DIFFERS]
    assert report.setups[0].tap_delta == 1


def test_direction_is_part_of_the_cell() -> None:
    """존은 방향이 있다 — 롱/숏이 다르면 「같은 칸의 반대편」이 아니다."""
    short_bt = TimelineRow(**{**_bt().__dict__, "is_long": False})
    buckets, report = _attribute([_live()], [short_bt])
    assert buckets == [BUCKET_ZONE_MISSING, BUCKET_ZONE_MISSING]
    assert report.setups[0].nearest_zone_delta_ms is None


def test_verdict_follows_the_majority_bucket() -> None:
    """판정 한 줄이 과반 부류를 따라가고, 부류마다 후속이 다르다(완료 기준 2)."""
    _, tap_report = _attribute([_live(tap_index=2)], [_bt(tap_index=1)])
    assert "알려진 비대칭" in tap_report.verdict

    _, zone_report = _attribute([_live()], [])
    assert "별도 이슈로 뺍니다" in zone_report.verdict

    _, reentry_report = _attribute([_live(is_reentry=True, tap_index=3)], [])
    assert "구제 조인" in reentry_report.verdict


def test_bias_check_contrasts_paired_and_unpaired_stop_widths() -> None:
    """편향 점검 — 짝 없는 셋업이 유독 얇으면 WAN-334의 10건 표본이 편향됐다는 뜻이다."""
    live_rows = [
        _live(fill_price=100.0, stop_price=99.0),  # 짝 있음 → 손절폭 1.0%
        _live(
            zone_start=1_700_100_000_000,
            zone_confirmed=1_700_103_600_000,
            fill_price=100.0,
            stop_price=99.8,  # 짝 없음 → 손절폭 0.2%(얇다)
            fill_ms=1_700_110_800_000,
        ),
    ]
    _, report = _attribute(live_rows, [_bt()])
    bias = report.bias
    assert bias.paired_widths == (1.0,)
    assert bias.unpaired_widths[0] < 1.0
    assert bias.median_delta_pp is not None and bias.median_delta_pp < 0.0
    assert "편향 점검" in render_unpaired(report)


def test_counts_split_by_side_and_sum_to_total() -> None:
    """부류별 집계가 쪽별로 갈리고 합계가 맞는다(완료 기준 1 — 「각 몇 건인지 표로」)."""
    _, report = _attribute(
        [_live(tap_index=2), _live(zone_start=None, zone_confirmed=None)],
        [_bt(tap_index=1)],
    )
    live_counts = report.counts("라이브")
    bt_counts = report.counts("백테")
    total = report.counts()
    assert live_counts[BUCKET_TAP_DIFFERS] == 1
    assert live_counts[BUCKET_NO_KEY] == 1
    assert bt_counts[BUCKET_TAP_DIFFERS] == 1
    assert sum(total.values()) == len(report.setups)


def test_verdict_refuses_to_close_on_a_tie_or_without_a_majority() -> None:
    """🚨 과반이 없거나 동률이면 **한 부류로 닫지 않는다**.

    최다 부류를 골라 그 후속을 찍으면 동률에서도 **표시 순서로 이긴** 부류의 결론이
    단정적으로 나온다 — argmax만 보고 결론 내기(WAN-161 §곡선 폭)의 판정 축 변종이다.
    부류마다 원인·후속이 완전히 다르므로 그런 판은 따로 읽어야 한다.
    """
    # 백테가 아무 행도 없어 라이브 두 건이 각각 (a)·(0)로 갈린다 = 1대1 동률.
    _, report = _attribute(
        [
            _live(),  # (a) 존 없음
            _live(
                is_reentry=True,
                tap_index=3,
                zone_start=1_700_100_000_000,
                zone_confirmed=1_700_103_600_000,
                fill_ms=1_700_110_800_000,
            ),  # (0) 키 없음
        ],
        [],
    )
    assert report.counts()[BUCKET_ZONE_MISSING] == 1
    assert report.counts()[BUCKET_NO_KEY] == 1

    verdict = report.verdict
    assert "과반 부류 없음" in verdict
    assert "동률" in verdict
    # 어느 부류의 후속도 단정적으로 찍히지 않는다.
    assert "별도 이슈로 뺍니다" not in verdict
    assert "크기만 기록하고 닫습니다" not in verdict
    # 대신 분해를 보여 준다.
    assert BUCKET_ZONE_MISSING in verdict and BUCKET_NO_KEY in verdict
