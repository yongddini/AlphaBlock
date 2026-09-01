"""WAN-400 §0 — 존 병합 규칙이 원본 파인스크립트와 갈리는 세 자리의 **크기**.

이 파트는 **탐지만** 하므로 1분봉도 서브스텝 시뮬도 필요 없다(WAN-366 §0 · WAN-388 §1
선례). 채택 좌표(12종목 × 4TF × 못 박은 6년)에서 세 차이를 각각 **반사실 팔**로 세우고
기준 팔과의 탭 차이를 센다.

🚨 **판정은 세 자리가 성격이 완전히 다르다** — 한 표에 있다고 같은 종류가 아니다:

* **A(소멸 존 제외)** — **원본과 이미 같다**(§1). 원본은 되쓸린 존을 `box.delete()`(렌더)가
  아니라 `bullishOrderBlocksList.remove(i)`(`.pine:267` · 약세 `:308`)로 **데이터 리스트**
  에서 빼고, `handleOrderBlocksFinal`이 **그 리스트에서만** 병합 입력(`allOrderBlocksList`)
  을 만든다. 즉 원본에서도 소멸 존은 겹침 판정에 참여하지 않는다. 여기서 재는 것은
  「이슈가 가정한 대로 고쳤다면 얼마나 움직였을까」= **안 고치기로 한 결정의 크기**다.
* **B(`break_time` 산정)** — **진짜로 갈린다.** 우리 `distal` vs 원본 `pine_max`.
  ★ **사용자 결정이고 개발자 임의 선택 금지**(이슈 §2). 이 표는 그 결정의 입력이다.
* **C(탭 상태 키)** — **원본에 대응물이 없다**(원본은 탭을 세지 않고 박스만 그린다).
  「원본과 동일하게」가 정의되지 않는 자리라 **우리가 설계한다**(이슈 §3).

⚠️ **채택 경로는 이 표와 무관하다** — 채택 기본값이 `combine_obs=False`(WAN-149)라
병합 경로 자체가 안 돌고, 지금 돌고 있는 백테스트·페이퍼 숫자는 **하나도 안 움직인다**.
세 팔 전부 옵트인이고 기본값에서 기존 CSV는 비트 단위로 재현된다.

⚠️ **탐지 층의 수다** — 「탭 +N%」가 「북 거래 +N%」를 뜻하지 않는다(상당수 탭은 볼린저·
체결·슬롯에서 이미 버려진다). 손익 판정은 북에서만 낸다(WAN-341).

팔 넷(전부 `combine_obs=True`):

===========  =========================  ==================================================
팔           설정                       뜻
===========  =========================  ==================================================
``base``     distal · cluster           현행 병합 경로(= 옛 병합 리포트가 돈 엔진)
``arm_a``    아카이브 `swept_time` 제거  소멸 존이 병합 후보에서 **안 빠지는** 반사실
``arm_b``    pine_max · cluster         원본 `breakTime` 접기(`.pine:391-392`)
``arm_c``    distal · zone              탭 상태를 **아카이브 존 인덱스**마다 (§3 새 설계)
===========  =========================  ==================================================

🚨 **A 팔은 15m에서 안 잰다**(기본값 `--a-arm-timeframes 1h,2h,4h`) — 그 팔은 **아무것도
안 죽는** 반사실이라 활성 집합이 창 끝까지 계속 커져 비용이 **초선형**이다(BTC 실측 1h
57.9초 vs B 4.2초 · C 6.1초 — 15m은 그 15~20배로 추정). **틀린 것으로 판명된 전제의 크기**를
재는 팔에 그 비용을 치를 값이 없고, **A 판정은 이 팔이 아니라 원본 소스 대조에서 나온다**
(§1). 안 잰 칸은 지어내지 않고 `None`으로 남긴다.

📌 **팔마다 「모든 탭」만 돌리고 첫 탭은 거기서 걸러 낸다** — `signals`가
`retap_signals` 중 `tap_index == 0`인 것과 **정확히 같다**(두 경로가 `entered`·상태 갱신을
똑같이 하고 재탭 분기만 다르다). 회귀 테스트가 이 등식을 동작으로 고정한다. 팔마다 replay를
두 번 돌던 것을 한 번으로 줄인다.

재현::

    uv run python -m backtest.wan400_merge_parity_census
    uv run python -m backtest.wan400_merge_parity_census --from-csv   # 요약만
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.run import parse_date_ms
from strategy.models import (
    OrderBlock,
    OrderBlockDirection,
    OrderBlockParams,
    OrderBlockSignal,
)
from strategy.order_blocks import merged_signals_for_archive

REPORTS_DIR = Path("backtest/reports")
CENSUS_CSV_PATH = REPORTS_DIR / "wan400_merge_parity_census.csv"
SUMMARY_PATH = REPORTS_DIR / "wan400_merge_parity_census_summary.md"

#: 이 표가 흔드는 축 말고는 전부 채택값이다(WAN-149 `combine_obs=False`는 **축**이라 켠다).
ADOPTED_BREAK_TIME_RULE = "distal"
ADOPTED_TAP_STATE = "cluster"

#: A 팔을 도는 TF. 15m은 비용이 초선형이라 뺀다(위 독스트링) — 안 잰 칸은 `None`이다.
A_ARM_TIMEFRAMES = ("1h", "2h", "4h")


class CensusRow(BaseModel):
    """칸 하나(심볼 × TF)의 탐지 층 인구조사."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    num_bars: int
    num_zones: int

    # 기준 팔(현행 병합 경로)
    base_first_taps: int
    base_all_taps: int

    # A — 소멸 존을 병합 후보에서 빼는 것의 크기(반사실: 안 뺐다면)
    swept_zones: int
    """소멸(`swept_time`)한 존 수 = 병합 후보 집합에서 빠진 존 수."""
    swept_share: float
    """소멸 존 / 전체 존."""
    a_first_taps: int | None = None
    """🚨 A 팔을 안 돈 칸은 `None`이다(15m — 위 모듈 독스트링). 0과 다르다."""
    a_all_taps: int | None = None
    a_first_tap_change: float | None = None
    a_all_tap_change: float | None = None
    a_removed_taps: int | None = None
    """기준 팔에 있고 A 팔에 없는 탭(모든 탭 기준)."""
    a_added_taps: int | None = None
    """A 팔에만 있는 탭. 🚨 순변화(`a_all_tap_change`)는 이 둘이 상쇄된 값이라 하한이다."""

    # B — `break_time` 산정이 갈리는 정도
    b_diff_taps: int
    """기준 팔 탭 중 **두 규칙의 `break_time`이 다른** 클러스터에서 난 탭 수."""
    b_diff_share: float
    b_multi_taps: int
    """기준 팔 탭 중 구성 존 2개 이상 클러스터에서 난 탭 수(= B가 물 수 있는 모집단)."""
    b_diff_share_of_multi: float
    """`b_diff_taps / b_multi_taps` — 병합이 무는 탭 안에서의 비율."""
    b_first_taps: int
    b_all_taps: int
    b_first_tap_change: float
    b_all_tap_change: float
    b_removed_taps: int
    b_added_taps: int

    # C — 탭 상태 키를 존 단위로 바꾸면
    c_first_taps: int
    c_all_taps: int
    c_first_tap_change: float
    c_all_tap_change: float
    c_removed_taps: int
    c_added_taps: int


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _change(new: int, old: int) -> float:
    return (new - old) / old if old else 0.0


def pine_max_break_time(members: Sequence[OrderBlock]) -> int | None:
    """원본 `combineOBsFunc`의 `breakTime` 접기 — 엔진 구현과 **같은 식**을 다시 쓴다.

    ⚠️ 여기서 다시 쓰는 이유는 이 표가 **팔을 안 돌리고도** B가 갈리는 클러스터를 셀 수
    있어야 하기 때문이다(기준 팔 시그널의 `zone_key`만으로 판정한다). 두 구현이 같은
    답을 낸다는 것은 회귀 테스트가 동작으로 고정한다.
    """
    dead = [ob.break_time for ob in members if ob.break_time is not None]
    return max(dead) if dead else None


def distal_break_time(members: Sequence[OrderBlock]) -> int | None:
    """현행 규칙 — 합집합 경계를 정의하는 구성 존의 `break_time`."""
    if not members:
        return None
    is_bullish = members[0].direction is OrderBlockDirection.BULLISH
    distal = (
        min(members, key=lambda ob: ob.bottom)
        if is_bullish
        else max(members, key=lambda ob: ob.top)
    )
    return distal.break_time


def _first_taps(all_taps: Sequence[OrderBlockSignal]) -> list[OrderBlockSignal]:
    """모든 탭에서 첫 탭만 걸러 낸다 — `include_retaps=False` 실행과 **같은 목록**이다.

    두 경로는 `entered`·`inside_state` 갱신이 완전히 같고 **재탭 분기만** 다르므로,
    `tap_index == 0`인 시그널은 양쪽에서 동일하다. 팔마다 replay를 한 번만 돌기 위한
    등식이고, 회귀 테스트가 실제 목록 동등성으로 고정한다.
    """
    return [sig for sig in all_taps if sig.tap_index == 0]


def _tap_diff(base: Sequence[OrderBlockSignal], arm: Sequence[OrderBlockSignal]) -> tuple[int, int]:
    """(없어진 탭, 새로 생긴 탭). 순변화가 상쇄로 가리는 크기를 그대로 낸다.

    탭의 정체는 `(시각, 클러스터 구성 집합, 방향)`이다 — 같은 봉에 서로 다른 클러스터가
    각각 탭할 수 있으므로 다중집합(`Counter`)으로 센다.
    """

    def key(sig: OrderBlockSignal) -> tuple[int, frozenset[int], str]:
        return (sig.trigger_time, sig.zone_key or frozenset(), sig.direction.value)

    base_c, arm_c = Counter(map(key, base)), Counter(map(key, arm))
    return sum((base_c - arm_c).values()), sum((arm_c - base_c).values())


def _members_of(signal: OrderBlockSignal, archive: Sequence[OrderBlock]) -> list[OrderBlock]:
    """시그널이 실린 클러스터의 구성 존들.

    `zone_key`는 탐지 아카이브 인덱스 집합이다(WAN-83) — 병합 존 값 객체는 재계산마다
    새로 만들어져 객체 동일성으로 추적할 수 없어서 이 필드가 있다.
    """
    if not signal.zone_key:
        return []
    return [archive[i] for i in sorted(signal.zone_key) if 0 <= i < len(archive)]


def census_for_cell(
    symbol: str, timeframe: str, *, start: str, end: str, with_a_arm: bool = True
) -> CensusRow:
    """한 칸의 인구조사. 탐지만 하므로 1분봉·펀딩을 안 읽는다."""
    market = harness.load_market_data(
        symbol,
        timeframe,
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
        need_1m=False,
        funding=False,
    )
    if market.empty:
        raise ValueError(f"{symbol} {timeframe}: 데이터가 없습니다(창 확인).")
    df = market.htf_df

    base = harness.detect_order_blocks(market, OrderBlockParams(combine_obs=True))
    archive = base.order_blocks

    # ── B 팔: 원본 `breakTime` 접기.
    b_all = merged_signals_for_archive(archive, df, include_retaps=True, break_time_rule="pine_max")
    b_first = _first_taps(b_all)

    # ── C 팔: 탭 상태를 아카이브 존 인덱스마다.
    c_all = merged_signals_for_archive(archive, df, include_retaps=True, tap_state="zone")
    c_first = _first_taps(c_all)

    # ── A 팔: 아카이브의 `swept_time`을 지운 반사실(엔진 노브 아님 · 데이터 층 조작).
    #    소멸 존이 활성 집합에서 안 빠지므로 「원본이 소멸 존을 안 뺀다면」이 된다.
    #    🚨 §1에서 그 전제는 **거짓**으로 판명됐다 — 이 팔은 「안 고치기로 한 결정」의 크기다.
    #    비용이 초선형이라 15m에서는 안 돈다(모듈 독스트링) — 안 잰 칸은 `None`이다.
    a_all: list[OrderBlockSignal] | None = None
    if with_a_arm:
        unswept = [ob.model_copy(update={"swept_time": None}) for ob in archive]
        a_all = merged_signals_for_archive(unswept, df, include_retaps=True)

    # B가 「무는」 정도는 팔을 안 돌려도 기준 팔 탭에서 바로 센다 — 그 시점 클러스터에
    # 두 규칙을 각각 적용해 답이 다른지 보면 된다.
    b_diff = 0
    b_multi = 0
    for sig in base.retap_signals:
        members = _members_of(sig, archive)
        if len(members) <= 1:
            continue  # 단일 존이면 두 규칙이 정의상 같다.
        b_multi += 1
        if distal_break_time(members) != pine_max_break_time(members):
            b_diff += 1

    b_removed, b_added = _tap_diff(base.retap_signals, b_all)
    c_removed, c_added = _tap_diff(base.retap_signals, c_all)

    swept = sum(1 for ob in archive if ob.swept_time is not None)
    base_first, base_all = len(base.signals), len(base.retap_signals)
    a_fields: dict[str, int | float | None] = dict.fromkeys(
        (
            "a_first_taps",
            "a_all_taps",
            "a_first_tap_change",
            "a_all_tap_change",
            "a_removed_taps",
            "a_added_taps",
        )
    )
    if a_all is not None:
        a_removed, a_added = _tap_diff(base.retap_signals, a_all)
        a_fields = {
            "a_first_taps": len(_first_taps(a_all)),
            "a_all_taps": len(a_all),
            "a_first_tap_change": _change(len(_first_taps(a_all)), base_first),
            "a_all_tap_change": _change(len(a_all), base_all),
            "a_removed_taps": a_removed,
            "a_added_taps": a_added,
        }

    return CensusRow(
        symbol=harness.normalize_symbol(symbol),
        timeframe=timeframe,
        num_bars=len(df),
        num_zones=len(archive),
        base_first_taps=base_first,
        base_all_taps=base_all,
        swept_zones=swept,
        swept_share=_share(swept, len(archive)),
        **a_fields,
        b_diff_taps=b_diff,
        b_diff_share=_share(b_diff, base_all),
        b_multi_taps=b_multi,
        b_diff_share_of_multi=_share(b_diff, b_multi),
        b_first_taps=len(b_first),
        b_all_taps=len(b_all),
        b_first_tap_change=_change(len(b_first), base_first),
        b_all_tap_change=_change(len(b_all), base_all),
        b_removed_taps=b_removed,
        b_added_taps=b_added,
        c_first_taps=len(c_first),
        c_all_taps=len(c_all),
        c_first_tap_change=_change(len(c_first), base_first),
        c_all_tap_change=_change(len(c_all), base_all),
        c_removed_taps=c_removed,
        c_added_taps=c_added,
    )


def run_census(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    a_arm_timeframes: Sequence[str] = A_ARM_TIMEFRAMES,
    log: bool = True,
) -> list[CensusRow]:
    rows: list[CensusRow] = []
    cells = [(s, tf) for s in symbols for tf in timeframes]
    for idx, (symbol, timeframe) in enumerate(cells, start=1):
        with_a = timeframe in a_arm_timeframes
        if log:
            print(
                f"[{idx}/{len(cells)}] {symbol} {timeframe}{'' if with_a else ' (A 팔 생략)'}",
                flush=True,
            )
        rows.append(census_for_cell(symbol, timeframe, start=start, end=end, with_a_arm=with_a))
    return rows


def rows_to_frame(rows: Sequence[CensusRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def rows_from_csv(path: Path = CENSUS_CSV_PATH) -> list[CensusRow]:
    frame = pd.read_csv(path)
    return [CensusRow.model_validate(record) for record in frame.to_dict("records")]


def _measured(rows: Sequence[CensusRow], field: str) -> list[CensusRow]:
    """그 열을 **실제로 잰** 행만. 안 잰 칸(`None`)을 0으로 접으면 없는 수를 지어낸다."""
    return [row for row in rows if getattr(row, field) is not None]


def _weighted(rows: Sequence[CensusRow], part: str, whole: str) -> float | None:
    """탭 가중 비율 — 칸마다의 비율을 단순 평균하면 얇은 칸이 과대 대표된다.

    `part`를 안 잰 행은 **분모에서도** 뺀다(안 그러면 비율이 조용히 희석된다). 잰 행이
    하나도 없으면 `None`이다 — 0.0으로 접으면 「쟀는데 0」과 구분되지 않는다.
    """
    subset = _measured(rows, part)
    if not subset:
        return None
    num = sum(int(getattr(row, part)) for row in subset)
    den = sum(int(getattr(row, whole)) for row in subset)
    return _share(num, den)


def _total(rows: Sequence[CensusRow], field: str) -> int | None:
    subset = _measured(rows, field)
    if not subset:
        return None
    return sum(int(getattr(row, field)) for row in subset)


def _pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def _num(value: int | None) -> str:
    return "—" if value is None else f"{value:,}"


def _delta(rows: Sequence[CensusRow], part: str, whole: str) -> float | None:
    """`part / whole - 1` (안 잰 행은 빠진다)."""
    ratio = _weighted(rows, part, whole)
    return None if ratio is None else ratio - 1.0


def build_summary_markdown(rows: Sequence[CensusRow], *, elapsed: float | None = None) -> str:
    """요약. 🚨 세 자리의 **성격 차이**를 표보다 먼저 적는다."""
    out: list[str] = []
    out.append("# WAN-400 §0 — 존 병합 파리티 인구조사 (탐지 층)")
    out.append("")
    if not rows:
        out.append("표본이 없습니다.")
        return "\n".join(out) + "\n"

    cells = len(rows)
    symbols = sorted({row.symbol for row in rows})
    tfs = sorted({row.timeframe for row in rows})
    base_all = _total(rows, "base_all_taps")
    base_first = _total(rows, "base_first_taps")

    out.append(
        f"좌표: {len(symbols)}종목 × {len(tfs)}TF = {cells}칸 · "
        f"기준 팔 모든 탭 {base_all:,}건(첫 탭 {base_first:,}건)."
    )
    out.append("")
    out.append("🚨 **세 자리는 성격이 다르다 — 한 표에 있다고 같은 종류가 아니다.**")
    out.append("")
    out.append(
        "* **A** — 원본과 **이미 같다**(`.pine:267`의 `remove(i)`가 데이터 리스트에서 뺀다). "
        "아래 수는 「이슈 전제대로 고쳤다면 얼마나 움직였을까」다."
    )
    out.append("* **B** — 진짜로 갈린다. ★ **사용자 결정**이고 이 표가 그 입력이다.")
    out.append("* **C** — 원본에 대응물이 없다. 우리가 설계하는 자리다.")
    out.append("")

    a_rows = _measured(rows, "a_all_taps")
    a_tfs = sorted({row.timeframe for row in a_rows})
    swept = _weighted(rows, "swept_zones", "num_zones")

    out.append("## 유니버스 합계 (탭 가중)")
    out.append("")
    out.append("| 자리 | 첫 탭 | 모든 탭 | 부연 |")
    out.append("| -- | --: | --: | -- |")
    out.append(
        f"| **A** 소멸 존을 안 뺀다면 | "
        f"{_pct(_delta(rows, 'a_first_taps', 'base_first_taps'), signed=True)} | "
        f"{_pct(_delta(rows, 'a_all_taps', 'base_all_taps'), signed=True)} | "
        f"소멸 존 {_num(_total(rows, 'swept_zones'))}개({_pct(swept)}) · "
        f"{len(a_rows)}칸({', '.join(a_tfs) or '없음'})만 잼 |"
    )
    out.append(
        f"| **B** 원본 `pine_max` | "
        f"{_pct(_delta(rows, 'b_first_taps', 'base_first_taps'), signed=True)} | "
        f"{_pct(_delta(rows, 'b_all_taps', 'base_all_taps'), signed=True)} | "
        f"규칙이 갈리는 탭 {_num(_total(rows, 'b_diff_taps'))}건 "
        f"= 전체의 {_pct(_weighted(rows, 'b_diff_taps', 'base_all_taps'))} · "
        f"병합이 문 탭의 {_pct(_weighted(rows, 'b_diff_taps', 'b_multi_taps'))} |"
    )
    out.append(
        f"| **C** 탭 상태 존 단위 | "
        f"{_pct(_delta(rows, 'c_first_taps', 'base_first_taps'), signed=True)} | "
        f"{_pct(_delta(rows, 'c_all_taps', 'base_all_taps'), signed=True)} | "
        f"리셋 제거의 순효과 |"
    )
    out.append("")
    out.append(
        "🚨 **A 팔은 15m에서 안 쟀다** — 아무것도 안 죽는 반사실이라 활성 집합이 계속 커져 "
        "비용이 초선형이다(BTC 1h 실측 57.9초 vs B 4.2초·C 6.1초). **틀린 것으로 판명된 "
        "전제의 크기**를 재는 팔이고 **A 판정은 원본 소스 대조에서 나온다**(§1). "
        "안 잰 칸은 지어내지 않고 `—`다 — 그 행은 분자·분모 양쪽에서 빠진다."
    )
    out.append("")
    out.append(
        "⚠️ **「탭 변화」는 순변화다** — 없어진 탭과 새로 생긴 탭이 상쇄된 값이라 "
        "「몇 건이 달라졌나」의 **하한**이다. 그 상쇄를 푼 것이 다음 표다."
    )
    out.append("")
    out.append("## 상쇄를 푼 탭 차이 (모든 탭 · 건수)")
    out.append("")
    out.append("| 자리 | 없어진 탭 | 새로 생긴 탭 | 합 | 기준 팔 대비 |")
    out.append("| -- | --: | --: | --: | --: |")
    for label, prefix in (("A", "a"), ("B", "b"), ("C", "c")):
        removed = _total(rows, f"{prefix}_removed_taps")
        added = _total(rows, f"{prefix}_added_taps")
        if removed is None or added is None:
            out.append(f"| **{label}** | — | — | — | — |")
            continue
        # 분모는 그 팔을 **실제로 잰** 칸의 기준 탭이다(안 잰 칸을 섞으면 비율이 희석된다).
        base_sub = sum(row.base_all_taps for row in _measured(rows, f"{prefix}_removed_taps"))
        out.append(
            f"| **{label}** | {removed:,} | {added:,} | {removed + added:,} | "
            f"{_share(removed + added, base_sub):.2%} |"
        )
    out.append("")

    out.append("## TF별")
    out.append("")
    out.append("| TF | 칸 | 모든 탭 | A | B | B 갈림(병합 탭 중) | C |")
    out.append("| -- | --: | --: | --: | --: | --: | --: |")
    for tf in harness.DEFAULT_TIMEFRAMES:
        sub = [row for row in rows if row.timeframe == tf]
        if not sub:
            continue
        out.append(
            f"| {tf} | {len(sub)} | {_num(_total(sub, 'base_all_taps'))} | "
            f"{_pct(_delta(sub, 'a_all_taps', 'base_all_taps'), signed=True)} | "
            f"{_pct(_delta(sub, 'b_all_taps', 'base_all_taps'), signed=True)} | "
            f"{_pct(_weighted(sub, 'b_diff_taps', 'b_multi_taps'))} | "
            f"{_pct(_delta(sub, 'c_all_taps', 'base_all_taps'), signed=True)} |"
        )
    out.append("")

    out.append("## 칸별")
    out.append("")
    out.append("| 종목 | TF | 봉 | 존 | 모든 탭 | A | B | B 갈림 | C |")
    out.append("| -- | -- | --: | --: | --: | --: | --: | --: | --: |")
    for row in sorted(rows, key=lambda r: (r.timeframe, r.symbol)):
        out.append(
            f"| {row.symbol} | {row.timeframe} | {row.num_bars:,} | {row.num_zones:,} | "
            f"{row.base_all_taps:,} | {_pct(row.a_all_tap_change, signed=True)} | "
            f"{row.b_all_tap_change:+.2%} | {row.b_diff_share_of_multi:.2%} | "
            f"{row.c_all_tap_change:+.2%} |"
        )
    out.append("")
    out.append("## 읽는 법 · 범위")
    out.append("")
    out.append(
        "* ⚠️ **채택 경로는 이 표와 무관하다** — `combine_obs=False`(WAN-149)라 병합 경로가 "
        "안 돌고, 지금 돌고 있는 백테스트·페이퍼 숫자는 하나도 안 움직인다."
    )
    out.append(
        "* ⚠️ **탐지 층의 수다** — 「탭 ±N%」가 「북 거래 ±N%」가 아니다(볼린저·체결·슬롯이 "
        "뒤에서 또 깎는다). 손익 판정은 북에서만 낸다(WAN-341)."
    )
    out.append(
        "* 🚨 **B 채택은 재-베이스라인 = 사용자 결정**이고 개발자 임의 착수 금지다. "
        "`break_time`은 `obs_touch`의 오른쪽 변이라 **겹침 판정 결과까지** 달라진다."
    )
    out.append(
        "* ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 "
        "*존을 어떻게 뭉치나*를 묻지 *진입 규칙이 무작위와 구분되나*를 묻지 않는다."
    )
    if elapsed is not None:
        out.append("")
        out.append(f"실측 비용: {elapsed / 60:.1f}분 ({cells}칸 · 직렬 · 탐지만).")
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-400 §0 존 병합 파리티 인구조사")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument(
        "--a-arm-timeframes",
        default=",".join(A_ARM_TIMEFRAMES),
        help="A 팔을 돌 TF(빈 문자열이면 전부 생략). 기본값에서 15m은 뺀다 — 비용 초선형.",
    )
    args = parser.parse_args(argv)

    elapsed: float | None = None
    if args.from_csv:
        rows = rows_from_csv()
    else:
        started = time.monotonic()
        rows = run_census(
            [s.strip() for s in args.symbols.split(",") if s.strip()],
            [t.strip() for t in args.timeframes.split(",") if t.strip()],
            start=args.start,
            end=args.end,
            a_arm_timeframes=[t.strip() for t in args.a_arm_timeframes.split(",") if t.strip()],
        )
        elapsed = time.monotonic() - started
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        rows_to_frame(rows).to_csv(CENSUS_CSV_PATH, index=False)
        print(f"적재: {CENSUS_CSV_PATH}", flush=True)

    summary = build_summary_markdown(rows, elapsed=elapsed)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(summary, encoding="utf-8")
    print(summary)
    print(f"요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
