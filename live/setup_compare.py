"""페이퍼(라이브) ↔ 백테스트를 **셋업 단위로 1:1 조인**하는 대조 계층 (WAN-295).

WAN-234/290의 타임라인은 라이브·백테 행을 시각순으로 **병기**할 뿐, 같은 셋업을 한 줄로
묶지 않았다 — 그래서 "백테는 진입했는데 라이브는 끊겼나"를 눈으로 대조해야 했다. 이 모듈은
그 조인을 한다: 같은 셋업의 페이퍼·백테를 한 줄로 묶어 좌(페이퍼) | 가운데(차이) | 우(백테)로
낸다(사용자 승인 목업 `docs/mockups/wan290_timeline_compare_mockup.html`).

## 조인 키 — 체결 시각이 아니라 존 정체성 (WAN-234 노트가 지적한 불안정성 회피)

라이브 체결 시각(틱)과 백테 체결 시각(1분봉)은 **원래 갈린다**(WAN-256). 그 시각으로 짝지으면
같은 셋업이 안 맞는다. 대신 **존 정체성 + 탭 순번**으로 짝짓는다:

    (심볼, TF, 방향, 존 시작시각, 존 확정시각, 탭 순번)

라이브는 주문 장부(`live_limit_orders`의 `zone_start_time`·`zone_confirmed_time`·`tap_index`),
백테는 셋업 진단(`SetupDiagnostic`의 동명 필드, WAN-295)에서 온다 — 둘 다 같은
`entry_candidate_signals`가 같은 탐지(`OrderBlockParams()`)에서 매긴 값이라 같은 존의 같은 탭을
가리킨다. 존은 형성되고 나면 시작·확정 시각이 불변이라 체결 시각보다 훨씬 안정적이다.

## 세 신호 = 서로 다른 시각 언어 (목업 정본)

* **가격 차이(정상)** — 두 쪽 다 진입했고 손익만 다르다. 가운데 Δ 막대만 조용히(빨강/주황 없음).
* **판정 갈림(🔴 빨강)** — 한쪽만 진입했다(`verdict_differs`). 이 도구의 핵심 신호. ⚠️ 가격이
  몇 bp 다른 건 불일치로 안 친다 — 빨강은 오직 **진입 여부**가 갈릴 때만.
* **가격 벗어남(🟠 주황)** — 두 쪽 다 진입했는데 진입가 차이가 틱 오차를 넘는다(`price_off`).
  임계값은 지어내지 않고 `live.tick_parity`의 측정 분포(그날 매칭 체결의 가격차 bp)에서 낸다.

## 성격

순수 조인·집계다(화면 없이 테스트된다). 엔진·전략·기본값·토대 불변,
`ALPHABLOCK_LIVE_TRADING=false` 유지. 페이퍼↔백테 차이는 **설계상 알려진 것**(틱 vs 1분봉
WAN-256 · 신규 3종목 펀딩 대리 · 큐 우선순위 미모델 WAN-98)이고, 이 화면은 그 괴리를
**보이게** 하는 도구이지 없애는 게 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from live.tick_parity import FillDivergence, summarize_divergences
from live.trade_timeline import (
    SOURCE_BACKTEST,
    SOURCE_LIVE,
    STATUS_BACKTEST_CLOSED,
    TimelineRow,
)

__all__ = [
    "TICK_DIVERGENCE_K",
    "SetupCompareResult",
    "SetupCompareSummary",
    "SetupComparison",
    "SetupKey",
    "build_setup_comparisons",
    "filter_comparisons",
    "price_off_threshold_bps",
    "setup_key",
]

#: 페이퍼↔백테 셋업 조인 키: (심볼, TF, 롱?, 존 시작, 존 확정, 탭 순번).
SetupKey = tuple[str, str, bool, int, int, int]

#: 「가격 벗어남」 임계값 = 그날 매칭 체결 가격차의 측정 평균 × 이 계수. 하드코딩된 bp 값이
#: 아니라 `tick_parity`가 잰 분포에서 파생된다(완료 기준 4) — 평균의 K배를 넘는 진입가차만
#: "틱 오차 초과"로 본다(3σ 식 이상치 판정). 값이 클수록 보수적(덜 표시).
TICK_DIVERGENCE_K = 3.0

#: 라이브에서 「진입했다」로 보는 상태(진입 후 보유 중이거나 청산 완료).
_LIVE_ENTERED_STATUSES = ("진입", "청산")


def setup_key(row: TimelineRow) -> SetupKey | None:
    """행의 셋업 조인 키. 존 정체성(시작·확정)·탭 순번 중 하나라도 없으면 None(조인 불가).

    📌 **재진입 행(`is_reentry=True`)도 None이다(WAN-305)** — 재진입의 탭 순번은 안정적이지
    않다(라이브 = 재무장 시점 탭 카운트 · 백테 = 0)라 이 키로 짝지으면 같은 존의 base 탭과
    충돌하거나 영원히 안 맞는다. 재진입은 존 정체성 구제 조인(`_rescue_join_reentries`)이
    짝짓는다.
    """
    if row.is_reentry:
        return None
    if row.zone_start_time is None or row.zone_confirmed_time is None or row.tap_index is None:
        return None
    return (
        row.symbol,
        row.timeframe,
        row.is_long,
        row.zone_start_time,
        row.zone_confirmed_time,
        row.tap_index,
    )


def _live_entered(row: TimelineRow | None) -> bool:
    return row is not None and row.status in _LIVE_ENTERED_STATUSES


def _backtest_entered(row: TimelineRow | None) -> bool:
    # 백테 셀은 데이터 끝까지 보유하다 강제 청산하므로 진입 = 청산 행이다(`cell_setup_timeline`).
    return row is not None and row.status == STATUS_BACKTEST_CLOSED


def _entry_delta_bps(live: TimelineRow | None, backtest: TimelineRow | None) -> float | None:
    """진입가 차이(bp) = |라이브 체결가 − 백테 체결가| / 백테 체결가 × 10000. 없으면 None."""
    if live is None or backtest is None:
        return None
    if live.fill_price is None or backtest.fill_price is None or backtest.fill_price == 0.0:
        return None
    return abs(live.fill_price - backtest.fill_price) / backtest.fill_price * 10_000.0


@dataclass(frozen=True)
class SetupComparison:
    """한 셋업의 페이퍼↔백테 대조 한 줄 — 좌(페이퍼) | 가운데(차이) | 우(백테)."""

    key: SetupKey
    symbol: str
    timeframe: str
    is_long: bool
    live: TimelineRow | None
    backtest: TimelineRow | None
    live_entered: bool
    backtest_entered: bool
    pnl_delta_pct: float | None
    """Δ = 페이퍼 손익 − 백테 손익(%p). 두 쪽 다 손익이 있을 때만(둘 다 청산)."""
    entry_delta_bps: float | None
    """진입가 차이(bp). 두 쪽 다 체결됐을 때만."""
    verdict_differs: bool
    """한쪽만 진입 — 🔴 「판정 갈림」(이 도구의 핵심 신호)."""
    price_off: bool
    """두 쪽 다 진입했는데 진입가 차이가 틱 오차 임계를 넘음 — 🟠 「가격 벗어남」."""

    @property
    def diverges(self) -> bool:
        """불일치(핵심 신호) 여부 = 판정 갈림. 필터 칩 「불일치만」이 이 값을 본다."""
        return self.verdict_differs

    @property
    def focus_ms(self) -> int | None:
        """정렬·차트 점프 기준 시각 — 페이퍼(주인공) 먼저, 없으면 백테."""
        if self.live is not None and self.live.focus_ms is not None:
            return self.live.focus_ms
        return self.backtest.focus_ms if self.backtest is not None else None


@dataclass(frozen=True)
class SetupCompareSummary:
    """상단 요약 카드 값(오늘 셋업·일치·불일치 분해)."""

    total: int
    matched: int
    """두 쪽 판정(진입 여부)이 같은 셋업 수."""
    diverged: int
    """판정이 갈린(한쪽만 진입) 셋업 수 = 핵심 신호."""
    backtest_only_entered: int
    """백테만 진입(라이브 끊김) — 불일치의 주요 유형."""
    live_only_entered: int
    """라이브만 진입(백테는 미진입) — 반대 유형."""
    price_off: int
    """진입가가 틱 오차를 넘어 벗어난 셋업 수(일치 중에서도 확인 대상)."""


@dataclass(frozen=True)
class SetupCompareResult:
    """셋업 대조 한 판 — 행 + 요약 + 이번 판에 쓴 가격차 임계값."""

    comparisons: tuple[SetupComparison, ...]
    summary: SetupCompareSummary
    price_delta_threshold_bps: float


def price_off_threshold_bps(deltas_bps: Sequence[float], *, k: float = TICK_DIVERGENCE_K) -> float:
    """「가격 벗어남」 임계값을 매칭 체결의 가격차 분포에서 낸다(하드코딩 금지, 완료 기준 4).

    `deltas_bps`는 그날 두 쪽 다 체결된 셋업들의 진입가 차이(bp) — 라이브=틱, 백테=1분봉이라
    이것이 곧 `tick_parity`가 재는 그 갈림의 실측 표본이다(WAN-256). `tick_parity`의 집계
    (`summarize_divergences`)로 평균을 얻어 그 **K배**를 임계로 삼는다: 평균적 틱 오차의 K배를
    넘는 진입가차만 "틱 오차 초과"로 본다. 표본이 없으면 0.0(임계 없음 — 벗어남으로 안 친다).
    """
    # `summarize_divergences`가 bp 분포를 그대로 집계하도록, 각 delta를 두 체결가 차이로 싣는다:
    # 기준가 10000에 delta bp만큼 벌리면 `price_delta_bps`가 정확히 그 delta를 되돌려준다.
    divergences = [
        FillDivergence(
            bar_filled=True,
            tick_filled=True,
            bar_price=10_000.0,
            tick_price=10_000.0 + d,
        )
        for d in deltas_bps
    ]
    summary = summarize_divergences(divergences)
    return k * summary.mean_price_delta_bps


def build_setup_comparisons(
    live_rows: Sequence[TimelineRow],
    backtest_rows: Sequence[TimelineRow],
    *,
    k: float = TICK_DIVERGENCE_K,
) -> SetupCompareResult:
    """라이브·백테 셋업 행을 조인 키로 1:1 묶어 대조 결과를 만든다 (WAN-295).

    같은 키의 라이브·백테를 한 `SetupComparison`으로 합치고, 어느 한쪽만 있는 셋업도 그 한
    줄로 남긴다(키가 없는 행 — 존 정체성 미상 — 은 각자 유일 키로 두어 강제 조인하지 않는다).
    가격차 임계값은 두 쪽 다 체결된 셋업들의 진입가차 분포에서 낸다(`price_off_threshold_bps`).

    📌 **재진입 구제 조인(WAN-305)**: 재진입 행은 탭 순번이 안정적이지 않아 정확 키로
    짝지어지지 않는다 — 정확 조인이 끝난 뒤, 한쪽만 남은 행들을 **존 정체성**(심볼·TF·방향·
    존 시작·확정)으로 묶어 어느 한쪽이 재진입인 짝을 시간순으로 잇는다. 이것이 없으면
    페이퍼가 채택 규칙(WAN-273/274)대로 한 재진입 매매가 전부 🔴 「판정 갈림」으로 찍힌다
    (2026-08-13 LTC 1h 실사례 — 이 이슈의 동기).
    """
    live_by_key: dict[SetupKey, TimelineRow] = {}
    bt_by_key: dict[SetupKey, TimelineRow] = {}
    live_orphans: list[TimelineRow] = []
    bt_orphans: list[TimelineRow] = []

    for row in live_rows:
        if row.source != SOURCE_LIVE:
            continue
        key = setup_key(row)
        if key is None:
            live_orphans.append(row)
        else:
            # 같은 키가 여러 라이브 행이면 첫 진입/청산을 우선(가장 정보가 많은 줄).
            existing = live_by_key.get(key)
            if existing is None or (not _live_entered(existing) and _live_entered(row)):
                live_by_key[key] = row

    for row in backtest_rows:
        if row.source != SOURCE_BACKTEST:
            continue
        key = setup_key(row)
        if key is None:
            bt_orphans.append(row)
        else:
            existing = bt_by_key.get(key)
            if existing is None or (not _backtest_entered(existing) and _backtest_entered(row)):
                bt_by_key[key] = row

    keys = sorted(set(live_by_key) | set(bt_by_key))
    both_sided = [k for k in keys if k in live_by_key and k in bt_by_key]
    one_sided = [k for k in keys if k not in live_by_key or k not in bt_by_key]

    # 재진입 구제 조인 — 한쪽만 남은 행(키 없는 orphan + 키 있는 one-sided)을 존 정체성으로.
    rescued, consumed_keys, live_orphans, bt_orphans = _rescue_join_reentries(
        one_sided, live_by_key, bt_by_key, live_orphans, bt_orphans
    )

    # 1단계: 두 쪽 다 체결된 셋업(정확 조인 + 구제 조인)의 진입가차로 임계값 산출.
    paired_deltas: list[float] = []
    for key in both_sided:
        delta = _entry_delta_bps(live_by_key.get(key), bt_by_key.get(key))
        if delta is not None:
            paired_deltas.append(delta)
    for live, backtest in rescued:
        delta = _entry_delta_bps(live, backtest)
        if delta is not None:
            paired_deltas.append(delta)
    threshold = price_off_threshold_bps(paired_deltas, k=k)

    # 2단계: 행 조립.
    comparisons: list[SetupComparison] = []
    for key in both_sided:
        comparisons.append(
            _make_comparison(key, live_by_key.get(key), bt_by_key.get(key), threshold)
        )
    for key in one_sided:
        if key in consumed_keys:
            continue  # 구제 조인이 짝지었다.
        comparisons.append(
            _make_comparison(key, live_by_key.get(key), bt_by_key.get(key), threshold)
        )
    orphan_seq = 0
    for live, backtest in rescued:
        ref = live if live is not None else backtest
        assert ref is not None
        comparisons.append(
            _make_comparison(_synthetic_key(ref, orphan_seq), live, backtest, threshold)
        )
        orphan_seq += 1
    for row in live_orphans:
        comparisons.append(_make_comparison(_synthetic_key(row, orphan_seq), row, None, threshold))
        orphan_seq += 1
    for row in bt_orphans:
        comparisons.append(_make_comparison(_synthetic_key(row, orphan_seq), None, row, threshold))
        orphan_seq += 1

    comparisons.sort(key=_comparison_sort_key)
    summary = _summarize(comparisons)
    return SetupCompareResult(
        comparisons=tuple(comparisons),
        summary=summary,
        price_delta_threshold_bps=threshold,
    )


#: 존 정체성(구제 조인의 묶음 키): (심볼, TF, 롱?, 존 시작, 존 확정) — 탭 순번 없음.
_ZoneIdentity = tuple[str, str, bool, int, int]


def _zone_identity(row: TimelineRow) -> _ZoneIdentity | None:
    if row.zone_start_time is None or row.zone_confirmed_time is None:
        return None
    return (row.symbol, row.timeframe, row.is_long, row.zone_start_time, row.zone_confirmed_time)


def _row_time(row: TimelineRow) -> int:
    return row.focus_ms if row.focus_ms is not None else 0


def _rescue_join_reentries(
    one_sided: Sequence[SetupKey],
    live_by_key: dict[SetupKey, TimelineRow],
    bt_by_key: dict[SetupKey, TimelineRow],
    live_orphans: list[TimelineRow],
    bt_orphans: list[TimelineRow],
) -> tuple[
    list[tuple[TimelineRow | None, TimelineRow | None]],
    set[SetupKey],
    list[TimelineRow],
    list[TimelineRow],
]:
    """한쪽만 남은 행들을 존 정체성으로 묶어 **어느 한쪽이 재진입인** 짝만 시간순으로 잇는다.

    재진입의 두 실측 모양을 모두 덮는다(WAN-305 §2): (1) 라이브 재무장 주문(`origin=
    "reentry"`) ↔ 백테 재진입 행 — 양쪽 다 재진입 표기. (2) 재시작으로 재무장이 폐기된 뒤
    새 탭으로 다시 들어간 라이브 행(옛 장부라 origin NULL·tap 키) ↔ 백테 재진입 행 —
    백테 쪽 표기만으로 잇는다. **둘 다 재진입이 아닌 짝은 잇지 않는다**(그건 진짜 판정
    갈림이다 — 조용히 초록불로 만들지 않는다).

    반환: (구제된 짝들, 소비된 one-sided 키, 남은 라이브 orphan, 남은 백테 orphan).
    """
    live_pool: dict[_ZoneIdentity, list[tuple[SetupKey | None, TimelineRow]]] = {}
    bt_pool: dict[_ZoneIdentity, list[tuple[SetupKey | None, TimelineRow]]] = {}

    for key in one_sided:
        row = live_by_key.get(key) or bt_by_key.get(key)
        assert row is not None
        zone = _zone_identity(row)
        if zone is None:
            continue
        pool = live_pool if key in live_by_key else bt_pool
        pool.setdefault(zone, []).append((key, row))
    for row in live_orphans:
        zone = _zone_identity(row)
        if zone is not None:
            live_pool.setdefault(zone, []).append((None, row))
    for row in bt_orphans:
        zone = _zone_identity(row)
        if zone is not None:
            bt_pool.setdefault(zone, []).append((None, row))

    rescued: list[tuple[TimelineRow | None, TimelineRow | None]] = []
    consumed_keys: set[SetupKey] = set()
    consumed_rows: set[int] = set()
    for zone in sorted(set(live_pool) & set(bt_pool)):
        lives = sorted(live_pool[zone], key=lambda kr: _row_time(kr[1]))
        bts = sorted(bt_pool[zone], key=lambda kr: _row_time(kr[1]))
        li = bi = 0
        while li < len(lives) and bi < len(bts):
            l_key, l_row = lives[li]
            b_key, b_row = bts[bi]
            if not (l_row.is_reentry or b_row.is_reentry):
                # 둘 다 재진입이 아니면 강제 조인하지 않는다 — 시간순으로 앞선 쪽을 넘긴다.
                if _row_time(l_row) <= _row_time(b_row):
                    li += 1
                else:
                    bi += 1
                continue
            rescued.append((l_row, b_row))
            if l_key is not None:
                consumed_keys.add(l_key)
            else:
                consumed_rows.add(id(l_row))
            if b_key is not None:
                consumed_keys.add(b_key)
            else:
                consumed_rows.add(id(b_row))
            li += 1
            bi += 1

    remaining_live = [r for r in live_orphans if id(r) not in consumed_rows]
    remaining_bt = [r for r in bt_orphans if id(r) not in consumed_rows]
    return rescued, consumed_keys, remaining_live, remaining_bt


def _synthetic_key(row: TimelineRow, seq: int) -> SetupKey:
    """조인 불가(존 정체성 미상) 행에 유일 키를 준다 — 강제 조인 방지."""
    return (row.symbol, row.timeframe, row.is_long, -1, -1, -(seq + 1))


def _make_comparison(
    key: SetupKey,
    live: TimelineRow | None,
    backtest: TimelineRow | None,
    threshold: float,
) -> SetupComparison:
    ref = live if live is not None else backtest
    assert ref is not None  # 최소 한쪽은 있다(키가 그 행에서 나왔다).
    live_entered = _live_entered(live)
    backtest_entered = _backtest_entered(backtest)
    verdict_differs = live_entered != backtest_entered
    entry_delta = _entry_delta_bps(live, backtest)
    pnl_delta: float | None = None
    if (
        live is not None
        and backtest is not None
        and live.pnl_pct is not None
        and backtest.pnl_pct is not None
    ):
        pnl_delta = live.pnl_pct - backtest.pnl_pct
    # 가격 벗어남은 두 쪽 다 진입(판정 일치)했는데 진입가차가 임계 초과일 때만 — 판정이
    # 갈린 줄은 빨강이 이미 말하므로 주황을 얹지 않는다(신호가 겹치지 않게).
    price_off = (
        not verdict_differs
        and live_entered
        and backtest_entered
        and entry_delta is not None
        and threshold > 0.0
        and entry_delta > threshold
    )
    return SetupComparison(
        key=key,
        symbol=ref.symbol,
        timeframe=ref.timeframe,
        is_long=ref.is_long,
        live=live,
        backtest=backtest,
        live_entered=live_entered,
        backtest_entered=backtest_entered,
        pnl_delta_pct=pnl_delta,
        entry_delta_bps=entry_delta,
        verdict_differs=verdict_differs,
        price_off=price_off,
    )


def _comparison_sort_key(comp: SetupComparison) -> tuple[int, str, str, int]:
    focus = comp.focus_ms if comp.focus_ms is not None else 0
    return (focus, comp.symbol, comp.timeframe, comp.key[5])


def _summarize(comparisons: Sequence[SetupComparison]) -> SetupCompareSummary:
    total = len(comparisons)
    diverged = sum(1 for c in comparisons if c.verdict_differs)
    bt_only = sum(1 for c in comparisons if c.backtest_entered and not c.live_entered)
    live_only = sum(1 for c in comparisons if c.live_entered and not c.backtest_entered)
    price_off = sum(1 for c in comparisons if c.price_off)
    return SetupCompareSummary(
        total=total,
        matched=total - diverged,
        diverged=diverged,
        backtest_only_entered=bt_only,
        live_only_entered=live_only,
        price_off=price_off,
    )


def filter_comparisons(comparisons: Sequence[SetupComparison], mode: str) -> list[SetupComparison]:
    """필터 칩(전체/불일치만/일치)으로 행을 거른다. 알 수 없는 모드는 전체."""
    if mode == "diverge":
        return [c for c in comparisons if c.verdict_differs]
    if mode == "match":
        return [c for c in comparisons if not c.verdict_differs]
    return list(comparisons)
