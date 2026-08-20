"""짝 없는 셋업의 귀속 — 「라이브가 보는 존을 백테는 왜 모르는가」 (WAN-337 §1).

## 왜 이 계층이 없었나

`live.setup_compare`의 조인은 **존 정체성 + 탭 순번**으로 짝을 짓고(WAN-295/333), 짝이 안
지어진 셋업은 `live.stop_width_parity`가 표에서 **뺀다** — *"존이 다르다는 뜻이라 손절폭
비교가 성립하지 않는다"*. **빼는 것 자체는 옳다**(다른 존의 손절폭을 빼면 무의미한 Δ가
나온다, WAN-333). 그런데 그래서 **「왜 다른 존을 보는가」를 아무도 재고 있지 않았다.**

실측이 그 자리를 가리켰다(2026-08-19 서버, WAN-337 본문): 08-17 라이브 60 · 백테 49 ·
짝지어짐 46 · **짝 없음 라이브 14** / 백테 3. 08-18도 같은 모양(라이브 14건)이다.
📌 **부호가 단서다 — 라이브가 항상 더 많이 본다.** 백테가 더 많으면 「라이브가 놓쳤다」인데
반대이므로 **라이브가 백테는 모르는 존을 알고 있다.**

🚨 **가장 유력했던 「워밍업 존 재고」 가설은 실측으로 기각됐다** — 백테 대조는 120일만
데우는데 라이브 러너는 계속 살아 있고 재시작해도 존 기억을 유지하므로(WAN-306) 「120일보다
전에 생긴 존은 라이브만 안다」가 자연스러운 설명이었지만, 그날 60건 중 워밍업 창 밖은
**0건**이고 최고령 존도 17일 전이다. 즉 이 차이는 **「알려진 근사」가 아니라 설명되지 않은
차이**다. 데이터 가설((a)의 원인 후보)도 `alphablock partial-bars --timeframes 15m` 실측이
배제했다 — 판정 사흘의 15m 손상이 0/0/1봉이고 그 1봉도 0.1bp다(WAN-327 도구).

## 네 부류 — 조인 키의 어느 조각이 갈리는가

조인 키는 **(심볼, TF, 방향, 존 시작, 존 확정, 탭 순번)** 이고 **하나만 어긋나도 짝이
깨진다.** 어느 것인지에 따라 원인이 완전히 다르므로 섞으면 안 된다:

| 부류 | 뜻 | 후속 |
| -- | -- | -- |
| `(0) 키 없음` | 존 정체성이 아예 없다 — 조인이 **성립 불가** | (b)/(c)와 섞지 말 것 |
| `(a) 존 없음` | 같은 칸의 백테에 그 존 시작이 없다 = 탐지 **입력**이 다르다 | 데이터 축 |
| `(b) 확정 시각` | 존은 있는데 확정 시각이 다르다 = 탐지 **로직**·봉 경계 | 엔진 파리티 결함 |
| `(c) 탭 순번` | 존·확정 같고 탭 순번만 다르다 = 틱 대 1분봉 해상도 | 알려진 비대칭(WAN-256) |

🚨 **`(0)`을 따로 세는 것이 이 표의 첫 안전장치다.** `setup_key()`는 **재진입 행에 일부러
`None`을 낸다**(WAN-305 — 재진입의 탭 순번이 라이브(재무장 시점 카운트)와 백테(0)에서 다르기
때문이다). 그 부류를 안 빼면 **재진입 때문에 키가 없는 행이 「탐지 로직 결함 (b)」로
오분류된다.** 재진입은 `_rescue_join_reentries`가 존 정체성으로 구제하므로, 여기까지 짝 없이
내려온 무키 행은 「구제도 못 한 행」이라는 뜻이다.

📌 **(c)가 가장 그럴듯하되 확신하지 말 것** — WAN-328이 「틱으론 그 봉에서 미체결이 15m
체결의 11.4%」를 실측했으니 해상도 차이는 실재한다. 다만 그건 **체결** 축 관찰이고, **탭**은
1분봉 저가가 봉내 저점을 이미 담으므로 같은 논리가 그대로 적용되지 않는다. 재서 판정한다.

## (a)의 「근접」 열 — 버킷을 흐리지 않으면서 판단을 살린다

버킷은 조인 키가 정하므로 (a)는 「존 시작이 정확히 일치하는 백테 존이 없다」이지 「가까운
존도 없다」가 아니다. 탐지가 한 봉 밀리면 그것도 (a)로 떨어지는데, 원인은 (b)에 가깝다.
그래서 (a) 행마다 **같은 칸에서 가장 가까운 백테 존 시작까지의 거리를 봉 단위로** 싣고,
요약이 「(a) 중 ±1봉 이내」를 따로 센다 — **분류를 느슨하게 하는 대신 증거를 붙인다.**

## 편향 점검 (완료 기준 3)

WAN-334는 손절폭 축을 「동일 = 잔차 없음」으로 닫았지만 그건 **짝지어진 10건**의 답이다.
짝 없는 셋업이 체계적으로 다른 부류(예: 유독 얇은 존)라면 그 표본이 편향됐을 수 있으므로,
짝 있는 라이브 셋업과 짝 없는 라이브 셋업의 **손절폭 분포를 나란히** 낸다.

## 성격

**순수 조인·집계다**(화면 없이 테스트된다). DB에 아무것도 쓰지 않고(WAN-194 원칙) 엔진·
전략·기본값·토대를 건드리지 않는다 — 손절폭 가드(0.3%)는 WAN-76/79, 존폭 필터(1.28)는
WAN-159 소관이고 재-베이스라인 = 사용자 결정이다. 전부 페이퍼이고
`ALPHABLOCK_LIVE_TRADING=false` 유지.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from common.timefmt import KST_LABEL, format_kst
from data.models import timeframe_to_ms
from live.setup_compare import SetupComparison, setup_key
from live.trade_timeline import SOURCE_BACKTEST, TimelineRow

__all__ = [
    "BUCKET_CONFIRM_DIFFERS",
    "BUCKET_NO_KEY",
    "BUCKET_TAP_DIFFERS",
    "BUCKET_ZONE_MISSING",
    "BUCKETS",
    "NEAR_MISS_BARS",
    "StopWidthBias",
    "UnpairedReport",
    "UnpairedSetup",
    "attribute_unpaired",
    "render_unpaired",
]

#: 조인 키가 아예 없어 **조인이 성립하지 않는** 부류. 재진입 행이 여기 오는 것은 설계다
#: (WAN-305) — (b)/(c)와 섞으면 재진입이 탐지 결함으로 오분류된다.
BUCKET_NO_KEY = "(0) 키 없음"
#: 같은 칸의 백테에 그 **존 시작**이 없다 — 탐지 입력(데이터)이 다르다.
BUCKET_ZONE_MISSING = "(a) 존 없음"
#: 존 시작은 같은데 **확정 시각**이 다르다 — 탐지 로직·봉 경계.
BUCKET_CONFIRM_DIFFERS = "(b) 확정 시각"
#: 존·확정이 같고 **탭 순번**만 다르다 — 틱 대 1분봉 해상도(WAN-256, 알려진 비대칭).
BUCKET_TAP_DIFFERS = "(c) 탭 순번"

#: 표시 순서(요약 표의 행 순서). 새 부류를 넣으면 여기에도 넣어야 요약에 나온다.
BUCKETS: tuple[str, ...] = (
    BUCKET_NO_KEY,
    BUCKET_ZONE_MISSING,
    BUCKET_CONFIRM_DIFFERS,
    BUCKET_TAP_DIFFERS,
)

#: (a) 행 중 「가장 가까운 반대편 존이 이 봉 수 이내」면 근접(near miss)으로 따로 센다.
#: 1봉 = 탐지가 한 봉 밀린 모양이라 원인이 (b)에 가깝다 — 버킷은 안 바꾸고 증거만 붙인다.
NEAR_MISS_BARS = 1

_SIDE_LIVE = "라이브"
_SIDE_BACKTEST = "백테"

#: 셀 = (심볼, TF, 롱?). 존은 방향이 있으므로 방향까지 같아야 「같은 칸」이다.
_Cell = tuple[str, str, bool]


def _cell(row: TimelineRow) -> _Cell:
    return (row.symbol, row.timeframe, row.is_long)


@dataclass(frozen=True, slots=True)
class UnpairedSetup:
    """짝을 못 지은 셋업 하나와 **어느 조각이 갈렸는지**."""

    side: str
    """`라이브` 또는 `백테` — 어느 쪽에만 있었나."""
    symbol: str
    timeframe: str
    is_long: bool
    focus_ms: int | None
    status: str
    """그 행의 상태 라벨(진입·미체결·무효화…). 짝이 없는 이유와는 다른 축이지만, 부류가
    특정 상태에 몰려 있으면 그 자체가 단서다."""
    zone_start_time: int | None
    zone_confirmed_time: int | None
    tap_index: int | None
    is_reentry: bool | None
    bucket: str
    confirm_delta_ms: int | None = None
    """(b) 전용 — 같은 존 시작을 가진 반대편 행 중 확정 시각 차이의 최솟값(절대값, ms)."""
    tap_delta: int | None = None
    """(c) 전용 — 같은 존을 가진 반대편 행 중 탭 순번 차이의 최솟값(절대값)."""
    nearest_zone_delta_ms: int | None = None
    """(a) 전용 — 같은 칸에서 가장 가까운 반대편 **존 시작**까지의 거리(절대값, ms).
    같은 칸에 반대편 행이 하나도 없으면 `None`(= 그 칸을 아예 안 봤다)."""
    stop_width_pct: float | None = None
    """이 셋업의 손절폭(%). 편향 점검(완료 기준 3)에 쓴다. 가격이 없으면 `None`."""

    @property
    def near_miss_bars(self) -> float | None:
        """(a) 행의 근접 거리를 **봉 수**로. 값이 없으면 `None`."""
        if self.nearest_zone_delta_ms is None:
            return None
        return self.nearest_zone_delta_ms / timeframe_to_ms(self.timeframe)

    @property
    def near_miss(self) -> bool:
        """(a)인데 가장 가까운 반대편 존이 `NEAR_MISS_BARS`봉 이내인가 — 원인이 (b)에 가깝다."""
        bars = self.near_miss_bars
        return self.bucket == BUCKET_ZONE_MISSING and bars is not None and bars <= NEAR_MISS_BARS


@dataclass(frozen=True, slots=True)
class StopWidthBias:
    """짝 있는/없는 라이브 셋업의 손절폭 분포 대조 (완료 기준 3).

    WAN-334의 「동일 = 잔차 없음」은 **짝지어진 10건**의 답이다. 짝 없는 셋업이 유독 얇은
    존이면 그 표본이 편향됐다는 뜻이므로 두 분포를 나란히 낸다. ⚠️ 이 표는 **기술 통계**이지
    검정이 아니다 — 표본이 십수 건이라 「같다/다르다」를 선언하지 않고 값만 보인다.
    """

    paired_widths: tuple[float, ...]
    unpaired_widths: tuple[float, ...]

    @staticmethod
    def _median(values: Sequence[float]) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    @property
    def paired_median(self) -> float | None:
        return self._median(self.paired_widths)

    @property
    def unpaired_median(self) -> float | None:
        return self._median(self.unpaired_widths)

    @property
    def median_delta_pp(self) -> float | None:
        """짝 없음 − 짝 있음(%p). 음수면 짝 없는 셋업이 **더 얇다**(가드에 더 잘 걸린다)."""
        a, b = self.unpaired_median, self.paired_median
        return None if a is None or b is None else a - b


@dataclass(frozen=True, slots=True)
class UnpairedReport:
    """한 판의 짝 없는 셋업 귀속 — 행 + 부류별 집계 + 편향 점검."""

    setups: tuple[UnpairedSetup, ...]
    bias: StopWidthBias

    def counts(self, side: str | None = None) -> dict[str, int]:
        """부류별 건수. `side`를 주면 그 쪽만(`라이브`/`백테`)."""
        out = dict.fromkeys(BUCKETS, 0)
        for one in self.setups:
            if side is not None and one.side != side:
                continue
            out[one.bucket] = out.get(one.bucket, 0) + 1
        return out

    @property
    def near_misses(self) -> int:
        """(a) 중 가장 가까운 반대편 존이 `NEAR_MISS_BARS`봉 이내인 건수."""
        return sum(1 for one in self.setups if one.near_miss)

    #: 부류별 후속 — 「이 부류가 과반이면 무엇을 하는가」. 판정 문장이 여기서만 나온다.
    _FOLLOW_UP: ClassVar[dict[str, str]] = {
        BUCKET_TAP_DIFFERS: (
            "틱 대 1분봉 해상도 = **알려진 비대칭**(WAN-256)입니다 — 크기만 기록하고 닫습니다."
        ),
        BUCKET_NO_KEY: (
            "조인이 **성립하지 않는** 행입니다 — 파리티 결함이 아니라 재진입 구제 조인"
            "(WAN-305) 쪽을 봐야 합니다."
        ),
        BUCKET_ZONE_MISSING: (
            "탐지 **입력**이 갈립니다 — 알려진 근사가 아니라 데이터 파리티 결함이라"
            " **별도 이슈로 뺍니다**."
        ),
        BUCKET_CONFIRM_DIFFERS: (
            "탐지 **로직**·봉 경계가 갈립니다 — 알려진 근사가 아니라 엔진 파리티 결함이라"
            " **별도 이슈로 뺍니다**."
        ),
    }

    @property
    def verdict(self) -> str:
        """「알려진 근사인가 결함인가」 한 줄 판정 (완료 기준 2).

        (c)가 과반이면 알려진 비대칭(WAN-256)이라 **크기만 기록하고 닫는다**. (a)·(b)가
        과반이면 데이터·엔진 파리티 결함이라 **별도 이슈로 뺀다**. (0)이 과반이면 그건
        파리티 결함이 아니라 **조인이 성립하지 않는 행**이라 구제 조인 쪽을 본다.

        🚨 **과반이 없거나 동률이면 한 부류로 닫지 않는다.** 최다 부류를 골라 그 후속을
        찍으면 「(a) 2건 · (b) 2건 · (c) 2건」에서도 사전 순으로 이긴 부류의 결론이
        단정적으로 나온다 — 이 저장소가 반복해 경계한 **argmax만 보고 결론 내기**
        (WAN-161 §곡선 폭)의 판정 축 변종이다. 그런 판에서는 **부류마다 따로 읽으라**고
        말한다.
        """
        if not self.setups:
            return "짝 없는 셋업이 없습니다 — 이 판에서는 판정할 것이 없습니다."
        counts = self.counts()
        total = len(self.setups)
        best = max(counts.values())
        leaders = [bucket for bucket in BUCKETS if counts[bucket] == best]
        if best == 0:
            return "부류가 하나도 안 잡혔습니다 — 분류기 배선을 의심하세요."
        if len(leaders) > 1 or best * 2 <= total:
            spread = " · ".join(f"{b} {counts[b]}건" for b in BUCKETS if counts[b])
            reason = "동률" if len(leaders) > 1 else f"최다도 {best}/{total}건뿐"
            return (
                f"**과반 부류 없음({reason})** — {spread}. 한 부류로 닫지 말고 각 부류의"
                " 후속을 따로 보세요(부류마다 원인·후속이 완전히 다릅니다)."
            )
        top = leaders[0]
        share = best / total * 100.0
        return f"과반 부류 = {top}({best}/{total}건 · {share:.1f}%) — {self._FOLLOW_UP[top]}"


def _stop_width_pct(row: TimelineRow) -> float | None:
    """손절폭(%) = `|진입가 − 손절가| / 진입가 × 100`.

    미체결 셋업은 체결가가 없으므로 걸어 둔 지정가를 쓴다(`live.stop_width_parity._fill_price`와
    같은 규약) — 값이 아예 없으면 `None`이고 **지어내지 않는다**(WAN-194).
    """
    entry = row.fill_price if row.fill_price is not None else row.limit_price
    stop = row.stop_price
    if entry is None or stop is None or entry <= 0.0:
        return None
    return abs(entry - stop) / entry * 100.0


def _attribute_one(row: TimelineRow, opposite: Sequence[TimelineRow]) -> UnpairedSetup:
    """한 행을 **같은 칸의 반대편 행 목록**과 대조해 부류를 매긴다 (순수 함수).

    `opposite`는 반대편의 **전부**여야 한다(짝 없는 것만이 아니라) — 라이브 탭 2가 짝을 못
    지은 이유가 「백테는 그 존의 탭 1만 안다」인데 그 탭 1은 이미 라이브 탭 1과 짝지어져
    있을 수 있다. 짝 없는 것만 넘기면 그 셋업이 (c)가 아니라 (a)로 잘못 떨어진다.
    """
    bucket = BUCKET_NO_KEY
    confirm_delta: int | None = None
    tap_delta: int | None = None
    nearest_zone_delta: int | None = None

    start_ms, confirmed_ms, tap = row.zone_start_time, row.zone_confirmed_time, row.tap_index
    if setup_key(row) is not None:
        # 키가 있으면 세 조각이 다 있다(`setup_key` 계약) — 좁혀 가며 어디까지 같은지 본다.
        assert start_ms is not None and confirmed_ms is not None and tap is not None
        same_zone = [
            o
            for o in opposite
            if o.zone_start_time == start_ms and o.zone_confirmed_time == confirmed_ms
        ]
        same_start = [o for o in opposite if o.zone_start_time == start_ms]
        if same_zone:
            taps = [abs(o.tap_index - tap) for o in same_zone if o.tap_index is not None]
            bucket = BUCKET_TAP_DIFFERS
            tap_delta = min(taps) if taps else None
        elif same_start:
            confirms = [
                abs(o.zone_confirmed_time - confirmed_ms)
                for o in same_start
                if o.zone_confirmed_time is not None
            ]
            bucket = BUCKET_CONFIRM_DIFFERS
            confirm_delta = min(confirms) if confirms else None
        else:
            starts = [
                abs(o.zone_start_time - start_ms) for o in opposite if o.zone_start_time is not None
            ]
            bucket = BUCKET_ZONE_MISSING
            nearest_zone_delta = min(starts) if starts else None

    return UnpairedSetup(
        side=_SIDE_BACKTEST if row.source == SOURCE_BACKTEST else _SIDE_LIVE,
        symbol=row.symbol,
        timeframe=row.timeframe,
        is_long=row.is_long,
        focus_ms=row.focus_ms,
        status=row.status,
        zone_start_time=start_ms,
        zone_confirmed_time=confirmed_ms,
        tap_index=tap,
        is_reentry=row.is_reentry,
        bucket=bucket,
        confirm_delta_ms=confirm_delta,
        tap_delta=tap_delta,
        nearest_zone_delta_ms=nearest_zone_delta,
        stop_width_pct=_stop_width_pct(row),
    )


def attribute_unpaired(
    live_rows: Sequence[TimelineRow],
    backtest_rows: Sequence[TimelineRow],
    comparisons: Sequence[SetupComparison],
) -> UnpairedReport:
    """짝 없는 셋업을 (0)/(a)/(b)/(c)로 귀속시킨다 (순수 함수, WAN-337 §1).

    조인 자체는 `live.setup_compare`가 이미 한 것을 그대로 받는다 — **다시 짝짓지 않는다**
    (두 벌로 갈라지면 같은 셋업이 두 화면에서 다른 짝을 얻는다, WAN-333/335 규약). 이 함수가
    더하는 것은 「짝이 없는 행이 반대편의 무엇과 어디까지 같은가」 하나다.

    `live_rows`·`backtest_rows`는 **조인에 실제로 들어간 그 행들**이어야 한다(좁혔으면 좁힌
    뒤의 것) — 아니면 「반대편에 있다/없다」의 분모가 화면과 어긋난다.
    """
    live_by_cell: dict[_Cell, list[TimelineRow]] = {}
    bt_by_cell: dict[_Cell, list[TimelineRow]] = {}
    for row in live_rows:
        live_by_cell.setdefault(_cell(row), []).append(row)
    for row in backtest_rows:
        bt_by_cell.setdefault(_cell(row), []).append(row)

    setups: list[UnpairedSetup] = []
    paired_widths: list[float] = []
    unpaired_widths: list[float] = []
    for comp in comparisons:
        if comp.paired:
            if comp.live is not None:
                width = _stop_width_pct(comp.live)
                if width is not None:
                    paired_widths.append(width)
            continue
        lone = comp.live if comp.live is not None else comp.backtest
        assert lone is not None  # 짝이 없어도 최소 한쪽은 있다.
        opposite = (
            bt_by_cell.get(_cell(lone), [])
            if comp.live is not None
            else live_by_cell.get(_cell(lone), [])
        )
        one = _attribute_one(lone, opposite)
        setups.append(one)
        if one.side == _SIDE_LIVE and one.stop_width_pct is not None:
            unpaired_widths.append(one.stop_width_pct)

    setups.sort(key=lambda s: (s.focus_ms or 0, s.symbol, s.timeframe))
    return UnpairedReport(
        setups=tuple(setups),
        bias=StopWidthBias(
            paired_widths=tuple(paired_widths),
            unpaired_widths=tuple(unpaired_widths),
        ),
    )


def _fmt_bars(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}봉"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _evidence(one: UnpairedSetup) -> str:
    """그 행이 반대편의 **무엇과 어디까지 같았는지** 한 조각."""
    if one.bucket == BUCKET_NO_KEY:
        if one.is_reentry:
            return "재진입 행(설계상 무키) · 구제 조인도 못 지음"
        return "존 정체성 미상"
    if one.bucket == BUCKET_TAP_DIFFERS:
        return "—" if one.tap_delta is None else f"탭 차이 {one.tap_delta}"
    if one.bucket == BUCKET_CONFIRM_DIFFERS:
        if one.confirm_delta_ms is None:
            return "—"
        bars = one.confirm_delta_ms / timeframe_to_ms(one.timeframe)
        return f"확정 차이 {_fmt_bars(bars)}"
    if one.nearest_zone_delta_ms is None:
        return "반대편이 그 칸을 아예 안 봄"
    return f"최근접 존 {_fmt_bars(one.near_miss_bars)}"


def render_unpaired(report: UnpairedReport) -> str:
    """사람이 읽는 진단 표. 시각은 KST 고정(WAN-172).

    📌 **기본 표(§1 손절폭 짝)는 건드리지 않는다** — 짝 없는 셋업을 그 표에 넣으면 다른 존의
    손절폭을 빼는 무의미한 Δ가 나온다(WAN-333이 고친 부류). 이것은 **별도 진단 블록**이고,
    절 번호도 새로 매기지 않는다(이 리포트의 §1·§3은 WAN-328이 매긴 것이고 그 이슈의 §2는
    다른 것이다 — 「§1 부록」으로 붙인다).
    """
    lines: list[str] = [
        "",
        # 🚨 절 번호를 새로 매기지 않는다 — 이 리포트의 §1·§3은 WAN-328이 매긴 것이고
        # 그 이슈의 §2는 **다른 것**(틱 재산정 팔)이다. 「§1 부록」으로 붙인다.
        f"§1 부록 · 짝 없는 셋업 귀속 — 조인 키의 어느 조각이 갈리는가 ({KST_LABEL})",
        "-" * 72,
    ]
    if not report.setups:
        lines.append("  짝 없는 셋업이 없습니다.")
        return "\n".join(lines)

    lines.append(f"  {'쪽':<5}{'심볼':<14}{'TF':<5}{'시각':<18}{'부류':<12}{'손절폭':>8}  근거")
    for one in report.setups:
        stamp = format_kst(one.focus_ms) if one.focus_ms is not None else "—"
        flag = " ⚠️근접" if one.near_miss else ""
        side = one.side.ljust(3)  # 「백테」(2자)를 「라이브」(3자)와 같은 표시 폭으로.
        lines.append(
            f"  {side}  {one.symbol:<14}{one.timeframe:<5}{stamp:<18}{one.bucket:<12}"
            f"{_fmt_pct(one.stop_width_pct):>8}  {_evidence(one)}{flag}"
        )

    lines += [
        "",
        "  부류별 집계",
        "  " + "-" * 68,
        f"  {'부류':<14}{'라이브':>8}{'백테':>8}{'합계':>8}",
    ]
    live_counts = report.counts(_SIDE_LIVE)
    bt_counts = report.counts(_SIDE_BACKTEST)
    total_counts = report.counts()
    for bucket in BUCKETS:
        lines.append(
            f"  {bucket:<14}{live_counts[bucket]:>8}{bt_counts[bucket]:>8}{total_counts[bucket]:>8}"
        )
    lines.append(
        f"  {'합계':<14}{sum(live_counts.values()):>8}{sum(bt_counts.values()):>8}"
        f"{len(report.setups):>8}"
    )
    if report.near_misses:
        lines.append(
            f"  📌 (a) 중 최근접 백테 존이 {NEAR_MISS_BARS}봉 이내: {report.near_misses}건 —"
            " 버킷은 (a)지만 원인은 (b)에 가깝습니다(탐지가 한 봉 밀린 모양)."
        )
    lines += ["", f"  판정: {report.verdict}"]

    bias = report.bias
    lines += [
        "",
        "  편향 점검 — 짝 있는/없는 라이브 셋업의 손절폭(완료 기준 3)",
        "  " + "-" * 68,
        f"  짝 있음 {len(bias.paired_widths):>3}건 중앙값 {_fmt_pct(bias.paired_median)}%"
        f" · 짝 없음 {len(bias.unpaired_widths):>3}건 중앙값 {_fmt_pct(bias.unpaired_median)}%"
        f" · Δ {_fmt_pct(bias.median_delta_pp)}%p",
        "  ⚠️ 기술 통계이지 검정이 아닙니다 — 표본이 십수 건이라 「같다/다르다」를 선언하지",
        "     않습니다. Δ가 크게 음수면 WAN-334의 손절폭 판정(「동일」)이 유독 두꺼운 존만",
        "     본 값일 수 있다는 뜻입니다.",
        "",
        "  ⚠️ 부류 (0)은 **조인이 성립하지 않는 행**이라 (b)/(c)와 섞지 마세요 — 재진입 행은",
        "     설계상 무키입니다(WAN-305). 여기 남았다는 것은 구제 조인도 못 지었다는 뜻입니다.",
    ]
    return "\n".join(lines)
