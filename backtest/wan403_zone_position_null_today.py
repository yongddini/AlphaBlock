"""WAN-403: 「오더블록 자리가 아무 자리보다 나은가」를 **오늘 엔진·오늘 좌표**에서 다시.

## 이 표의 용도가 다르다 — 「무엇을 채택할까」가 아니라 「이 접근을 계속할 것인가」

WAN-248(2026-08-04)이 정확히 이 질문을 물었고 판정은 **(c)**였다 — 앞구간 13/27은 강한데
뒷구간 3/25로 대부분 사라진다. 그런데 그 표 뒤에 좌표가 통째로 바뀌었다:

| 무엇 | 그때(WAN-248) | 지금(이 표) |
| -- | -- | -- |
| 무효화 취소 | 소급 취소(룩어헤드) | **인과**(WAN-365) |
| 존폭 필터 | 1.28 켬 | **꺼짐**(WAN-384) |
| 유니버스 | 9종목 | **12종목**(WAN-307) |
| 작업 TF | 15m·1h·4h | **15m·1h·2h·4h**(WAN-252) |
| 익절 비용 | 테이커 | **메이커**(WAN-370) |

🚨 하필 그 좌표가 버그의 영향을 가장 크게 받는 자리였다 — WAN-366 §0이 소급 취소로 지워진
탭이 **좁은 존에 4배 몰려 있었다**고 냈는데(좁은 존 25.86% vs 넓은 존 6.75%), WAN-248은
**존폭 필터 1.28을 켠 채**(= 좁은 존만 매매하던 판) 돌았다.

⚠️ 다만 **방향이 뒤집힐 종류는 아니다** — 가짜 존이 실제 존과 폭을 매칭하므로 그 버그의
보호도 양쪽이 같이 받는다(엣지 판정 계열의 대칭 원칙). 그 대칭이 실제로 성립하는지는 §4가
**무효화 봉 탭 비율을 두 팔에서 나란히** 내어 확인한다.

## 자를 바꾸지 않았다 — 기계는 WAN-248을 그대로 import한다

검정(매칭 널 · `pool_k` · 심볼 층화 순열 · 유효 셀 기준 · 유의 기준 · 부트스트랩 반복·시드)은
`backtest.wan248_zone_position_null`에서 **상수와 함수를 그대로 가져온다**. 이 모듈이 바꾸는
것은 **좌표 셋**뿐이다:

1. 심볼 9 → **12**(`harness.DEFAULT_SYMBOLS`)
2. 존폭 필터 핀 **제거**(`harness.UNSET` = 핀 없음 = 채택 기본값을 따라간다, WAN-305)
3. TF는 `harness.DEFAULT_TIMEFRAMES`라 **2h가 자동으로** 들어온다

무효화 취소 시점은 **핀이 애초에 없어** 자동으로 인과(`bar_close`, WAN-365)다.
익절 배수도 핀이 없어 자동으로 채택 기본값 **1.5R**이다.

⚠️ **이 표는 1.5R에서 쟀다 — 익절 배수를 바꾸는 결정이 나면 재확인이 필요하다**(사용자 결정
2026-09-02 「A로 하고」). 배수는 실제·가짜 양쪽에 똑같이 걸리므로 방향이 뒤집힐 구조는
아니지만, 익절이 가까울수록(0.4R) 아무 자리에 사도 닿아 **위치 정보가 묻힌다**(WAN-395:
0.4R에서 거래의 40%가 「산 그 1분 안에 익절」). 그래서 먼 배수가 이 질문에 더 예민한 자다.

⚠️ **옛 CSV는 덮지 않는다** — 새 파일(`wan403_*`)로 내고 WAN-248 표는 「그때는 그랬다」로
보존한다(WAN-194/297/325 관행). 그리고 **셀을 직접 비교하지 않는다**(좌표가 통째로 다르다) —
§3은 **유의 비율과 방향만** 대조한다.

## 판정 갈래 — 착수 전에 못 박았고 **코드가 고른다**

`branch_verdict()`가 아래 기준으로 (가)/(나)/(다) 중 하나를 문장으로 찍는다. 사람이 표를
보고 정하지 않는다(완료기준 2).

* **(가) 씨앗이 커졌다** — 뒷구간 유의 비율이 옛 판(3/25 = 12.0%)의 **2배 이상**.
* **(다) 씨앗이 사라졌다** — 앞구간 유의 비율이 **우연 수준의 2배 이하**(= 2α = 10%).
  널 아래에서 「p≤0.05 & 실제>무작위평균」은 약 α의 비율로 저절로 나온다.
* **(나) 그대로 (c)** — 그 사이. 앞구간엔 있는데 뒷구간에서 못 넘어간다.

## 재현

```
uv run python -m backtest.wan403_zone_position_null_today --part null --tf 4h --jobs 2
uv run python -m backtest.wan403_zone_position_null_today --part null --tf 2h --jobs 2 --append
uv run python -m backtest.wan403_zone_position_null_today --part null --tf 1h --jobs 2 --append
uv run python -m backtest.wan403_zone_position_null_today --part null --tf 15m --jobs 2 --append
uv run python -m backtest.wan403_zone_position_null_today --part summary
```

🚨 **램이 병목이다** — 위치 널은 존마다 가짜 존을 `pool_k`개 만들어 매번 풀 시뮬레이션한다.
16GB에서 워커가 많으면 1분봉 6년 사본이 램을 넘겨 스왑으로 붕괴한다. `--jobs 2` 권고.

**측정 전용** — 기본값·토대를 바꾸지 않고 핀도 하나 없다. 무엇이 나오든 채택·폐지는
**재-베이스라인 = 사용자 결정**이다(`ALPHABLOCK_LIVE_TRADING=false` 유지).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.wan248_zone_position_null import (
    _ADOPTED_OB,
    ALPHA,
    BASELINE_LENS,
    BOOTSTRAP_ITERATIONS,
    DEFAULT_LENSES,
    DEFAULT_SEGMENTS,
    LONG_ARM,
    MIN_TRADES_FOR_VERDICT,
    PEN_LENS,
    SEGMENT_LABELS,
    PositionNullRow,
    _md_table,
    _mean,
    _round_frame,
    _short,
    arm_of,
    cell_table,
    eligible_rows,
    is_significant,
    rows_from_csv,
    rows_to_frame,
    run_null,
    significance_counts,
)

REPORTS_DIR = Path("backtest/reports")
NULL_CSV = REPORTS_DIR / "wan403_zone_position_null_today.csv"
SUMMARY_MD = REPORTS_DIR / "wan403_zone_position_null_today_summary.md"

#: 오늘 좌표 — **핀 없이** `harness` 기본값에서 읽는다(WAN-305). 재-베이스라인이 오면 이 표도
#: 따라간다. WAN-248이 `LEGACY_NINE_SYMBOLS`로 얼려 둔 것과 정확히 반대의 선택이다.
SYMBOLS: tuple[str, ...] = harness.DEFAULT_SYMBOLS
TIMEFRAMES: tuple[str, ...] = harness.DEFAULT_TIMEFRAMES
DEFAULT_START: str = harness.DEFAULT_START
DEFAULT_END: str = harness.DEFAULT_END

#: 존당 무작위 위치 복제 수 — **WAN-248이 실제로 낸 표의 값(4)** 이다(모듈 기본값 8이 아니라
#: 공개 CSV의 `pool_k` 열이 정본). 자를 바꾸면 「판정이 바뀐 것」과 「자가 바뀐 것」이 안
#: 갈리므로 여기서 명시로 못 박고 회귀 테스트가 잠근다.
POOL_K: int = 4

#: 존폭 필터 핀 — `UNSET` = **핀을 안 건다**. 명시적 `None`(끄기)과 다르다(WAN-159 센티넬
#: 규약): 오늘은 값이 같아 보이지만 필터가 다시 채택되면 이 표는 따라가고 `None`은 안 간다.
ZONE_WIDTH_PIN: harness.ZoneWidthArg = harness.UNSET

#: 옛 판(WAN-248) 유의 셀 — **대조용 비율만** 쓴다. ⚠️ 셀 직접 비교 금지(좌표가 통째로 다름).
OLD_IS_SIGNIFICANT: tuple[int, int] = (13, 27)
OLD_OOS_SIGNIFICANT: tuple[int, int] = (3, 25)

#: 갈래 기준 — 착수 전에 못 박은 값이고 회귀 테스트가 잠근다(결과를 보고 선을 옮기지 못하게).
#: 널 아래에서 「p≤α & 실제>무작위평균」은 약 α의 비율로 저절로 나오므로, 그 **2배**를
#: 「우연과 구분 안 됨」의 선으로 쓴다.
CHANCE_RATIO: float = 2.0 * ALPHA
#: 「뚜렷이 늘었다」의 선 = 옛 뒷구간 비율(3/25 = 12.0%)의 2배.
GROWN_OOS_RATIO: float = 2.0 * (OLD_OOS_SIGNIFICANT[0] / OLD_OOS_SIGNIFICANT[1])

#: 대칭 확인(완료기준 5)의 선 — 실제·가짜 팔의 무효화 봉 탭 비율 차이가 이 폭 안이면
#: 「같은 정도로 걸렸다」로 읽는다. WAN-364 §2가 잰 전체 비율(15.59%)의 3분의 1 수준.
SYMMETRY_TOLERANCE_PP: float = 5.0

IS_SEGMENT = harness.SEGMENT_IS
OOS_SEGMENT = harness.SEGMENT_OOS_WARM


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def describe_engine() -> str:
    """검정한 엔진의 지문 — **핀 없이** 채택 파라미터를 그대로 읽는다."""
    p = arm_of(LONG_ARM).params()
    band = p.deviation_filter.band_bar if p.deviation_filter else None
    return (
        f"entry_mode={p.entry_mode}, rsi_gate_mode={p.rsi_gate_mode}, "
        f"retap_mode={p.retap_mode}, zone_limit_offset_bps={p.zone_limit_offset_bps}, "
        f"take_profit_r={p.take_profit_r}, band_bar={band}, "
        f"max_zone_width_atr={p.max_zone_width_atr}, "
        f"invalidation_cancel={p.invalidation_cancel}, combine_obs={_ADOPTED_OB.combine_obs}"
    )


def run_today_null(
    *,
    symbols: Sequence[str] = SYMBOLS,
    timeframes: Sequence[str] = TIMEFRAMES,
    segments: Sequence[str] = DEFAULT_SEGMENTS,
    lenses: Sequence[str] = DEFAULT_LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    pool_k: int = POOL_K,
    iterations: int = BOOTSTRAP_ITERATIONS,
    jobs: int = 1,
    log: bool = True,
) -> list[PositionNullRow]:
    """WAN-248 기계를 **오늘 좌표**로 돌린다 — 바꾸는 것은 심볼·TF·존폭 핀뿐이다."""
    return run_null(
        symbols=symbols,
        timeframes=timeframes,
        segments=segments,
        lenses=lenses,
        start=start,
        end=end,
        pool_k=pool_k,
        iterations=iterations,
        zone_width_pin=ZONE_WIDTH_PIN,
        jobs=jobs,
        log=log,
    )


# --------------------------------------------------------------------------- #
# 갈래 판정 — 코드가 고른다 (완료기준 2)
# --------------------------------------------------------------------------- #


def _segment_ratio(rows: Sequence[PositionNullRow], segment: str) -> tuple[int, int, float | None]:
    sig, total = significance_counts([r for r in rows if r.segment == segment])
    return sig, total, (sig / total if total else None)


def branch_verdict(rows: Sequence[PositionNullRow], *, lens: str = BASELINE_LENS) -> str:
    """(가)/(나)/(다) 중 하나를 문장으로 낸다 — 기본은 공식 렌즈(`baseline`).

    선은 모듈 상수(`GROWN_OOS_RATIO`·`CHANCE_RATIO`)이고 회귀 테스트가 잠근다. 표를 보고
    선을 옮기는 것을 막기 위해 판정을 사람이 아니라 여기서 내린다(이슈 완료기준 2).

    🚨 `lens`는 **인자로 받는다** — 렌즈를 이 함수 안에 못 박으면 `pen_5bp` 절이 자기 행을
    하나도 못 보고 조용히 「판정 불가」를 찍는다(라벨과 동작이 어긋나는 그 자리).
    """
    scoped = [r for r in rows if r.lens == lens]
    is_sig, is_total, is_ratio = _segment_ratio(scoped, IS_SEGMENT)
    oos_sig, oos_total, oos_ratio = _segment_ratio(scoped, OOS_SEGMENT)
    if is_total == 0 or oos_total == 0:
        return (
            f"**⚠️ 판정 불가** — 거래 {MIN_TRADES_FOR_VERDICT}건 이상인 유효 셀이 "
            f"앞구간 {is_total}개 · 뒷구간 {oos_total}개다(표본 부족)."
        )
    assert is_ratio is not None and oos_ratio is not None
    head = (
        f"앞구간(`{IS_SEGMENT}`) 유의 {is_sig}/{is_total}({is_ratio:.1%}) · "
        f"뒷구간(`{OOS_SEGMENT}`) 유의 {oos_sig}/{oos_total}({oos_ratio:.1%}) → "
    )
    if oos_ratio >= GROWN_OOS_RATIO:
        body = (
            f"**(가) 씨앗이 커졌다** — 뒷구간 유의 비율이 옛 판 "
            f"{OLD_OOS_SIGNIFICANT[0]}/{OLD_OOS_SIGNIFICANT[1]}"
            f"({OLD_OOS_SIGNIFICANT[0] / OLD_OOS_SIGNIFICANT[1]:.1%})의 2배 선"
            f"({GROWN_OOS_RATIO:.1%})을 넘는다. 버그가 위치 정보를 가리고 있었다는 뜻이고, "
            "다음은 WAN-402(선별·조기청산)다."
        )
    elif is_ratio <= CHANCE_RATIO:
        body = (
            f"**(다) 씨앗이 사라졌다** — 앞구간 유의 비율이 우연 수준의 2배 선"
            f"({CHANCE_RATIO:.1%}) 이하다. 옛 판의 앞구간 우위가 버그의 산물이었다는 뜻이고, "
            "**접는 결정 이슈**가 다음이다(개발자 임의 착수 금지 · 사용자 결정)."
        )
    else:
        body = (
            "**(나) 그대로 (c)** — 앞구간엔 있는데 뒷구간에서 사라진다. 버그와 무관하게 "
            "못 넘어간다는 뜻이고, WAN-402를 하되 **기대치를 낮춘다**. "
            "「무엇이 못 넘기게 하는가」(과최적화·레짐·표본)가 별도 질문으로 남는다."
        )
    return head + body


def old_comparison_line(rows: Sequence[PositionNullRow], *, lens: str = BASELINE_LENS) -> str:
    """완료기준 3 — 옛 판과의 대조 **한 줄**. ⚠️ 비율과 방향만, 셀 직접 비교 금지."""
    scoped = [r for r in rows if r.lens == lens]
    lines: list[str] = []
    for label, segment, old in (
        ("앞구간", IS_SEGMENT, OLD_IS_SIGNIFICANT),
        ("뒷구간", OOS_SEGMENT, OLD_OOS_SIGNIFICANT),
    ):
        sig, total, ratio = _segment_ratio(scoped, segment)
        old_ratio = old[0] / old[1]
        if ratio is None:
            lines.append(f"{label}: 유효 셀 없음(옛 판 {old[0]}/{old[1]} = {old_ratio:.1%})")
            continue
        direction = (
            "↑ 늘었다" if ratio > old_ratio else ("↓ 줄었다" if ratio < old_ratio else "→ 같다")
        )
        lines.append(
            f"{label} {old[0]}/{old[1]}({old_ratio:.1%}) → "
            f"**{sig}/{total}({ratio:.1%})** {direction}"
        )
    return " · ".join(lines)


# --------------------------------------------------------------------------- #
# 표 · 진단
# --------------------------------------------------------------------------- #


def summary_table(rows: Sequence[PositionNullRow], *, lens: str) -> pd.DataFrame:
    """(TF × 구간) 심볼평균 — 총수익 %(옛 판 대조용)와 **거래당 net R**을 함께 낸다.

    🚨 총수익 %로 판정하지 않는다(이 좌표에서 복리 착시, WAN-169/213). 옛 판이 그 열로 냈기에
    대조를 위해 병기할 뿐이고, 판정 열은 `real_net_r`/`random_net_r`과 유의 셀 수다.
    """
    records: list[dict[str, object]] = []
    scoped = [r for r in rows if r.lens == lens]
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    groups: dict[tuple[str, str], list[PositionNullRow]] = defaultdict(list)
    for r in scoped:
        groups[(r.timeframe, r.segment)].append(r)
    for (timeframe, segment), cells in sorted(
        groups.items(), key=lambda kv: (tf_order.get(kv[0][0], 9), seg_order.get(kv[0][1], 9))
    ):
        values = [c.real_total_return for c in cells]
        eligible = eligible_rows(cells)
        records.append(
            {
                "timeframe": timeframe,
                "segment": segment,
                "lens": lens,
                "real_mean": _mean(values),
                "random_mean": _mean(
                    [c.random_mean_return for c in cells if c.random_mean_return is not None]
                ),
                "real_net_r": _mean(
                    [c.real_mean_net_r for c in cells if c.real_mean_net_r is not None]
                ),
                "random_net_r": _mean(
                    [c.random_mean_net_r for c in cells if c.random_mean_net_r is not None]
                ),
                "positive": sum(1 for v in values if v > 0),
                "symbols": len(values),
                "eligible": len(eligible),
                "significant": sum(1 for c in eligible if is_significant(c)),
                "trades": _mean([float(c.real_num_trades) for c in cells]),
                "real_zones": _mean([float(c.real_zones) for c in cells]),
                "fake_zones": _mean([float(c.fake_zones) for c in cells]),
            }
        )
    return pd.DataFrame(records)


def _round_today(frame: pd.DataFrame) -> pd.DataFrame:
    out = _round_frame(frame)
    for col in ("real_net_r", "random_net_r"):
        if col in frame.columns:
            values = frame[col].astype(float).round(4)
            out[col] = values.astype(object).where(frame[col].notna(), "—")
    return out


def leave_one_out_lines(rows: Sequence[PositionNullRow], *, lens: str) -> list[str]:
    """완료기준 4 — 종목 **하나씩 전부** 빼 보고 심볼평균 부호가 유지되는가.

    ⚠️ WAN-248은 ETH·SOL·DOGE 셋만 뺐다 — 12종목 좌표에서는 전부 돈다(빼는 종목 목록이
    좌표를 따라가지 않으면 새 종목의 편중이 보이지 않는다). per-cell 널이라 이 LOO는
    **재집계**이지 북처럼 지갑을 다시 배치하는 것이 아니다.
    """
    lines: list[str] = []
    scoped = [r for r in rows if r.lens == lens]
    for tf in TIMEFRAMES:
        for seg in (IS_SEGMENT, OOS_SEGMENT):
            cells = [r for r in scoped if r.timeframe == tf and r.segment == seg]
            if len(cells) < 2:
                continue
            mean_all = _mean([c.real_total_return for c in cells])
            if mean_all is None:
                continue
            worst_label = ""
            worst_value: float | None = None
            flips: list[str] = []
            for sym in sorted({_short(c.symbol) for c in cells}):
                kept = [c for c in cells if _short(c.symbol) != sym]
                if not kept:
                    continue
                mean_ex = _mean([c.real_total_return for c in kept])
                if mean_ex is None:
                    continue
                if (mean_all > 0) != (mean_ex > 0):
                    flips.append(f"−{sym} {mean_ex * 100:+.2f}%")
                if worst_value is None or mean_ex < worst_value:
                    worst_value = mean_ex
                    worst_label = sym
            kept_n = len({_short(c.symbol) for c in cells})
            verdict = (
                f"**부호 뒤집힘**: {' · '.join(flips)}"
                if flips
                else (
                    f"{kept_n}판 전부 **부호 유지**"
                    f"(최악 −{worst_label} {(worst_value or 0.0) * 100:+.2f}%)"
                )
            )
            lines.append(f"- **{tf} {seg}**: 심볼평균 {mean_all * 100:+.2f}% → {verdict}")
    return lines or ["- (해당 행 없음)"]


def symmetry_table(rows: Sequence[PositionNullRow]) -> pd.DataFrame:
    """완료기준 5 — 실제 팔·가짜 팔의 **무효화 봉 탭 비율**을 나란히.

    WAN-364가 이름 붙인 소급 취소 룩어헤드가 지우던 탭이 정확히 이 부분집합이다. 두 팔의
    비율이 붙어 있으면 그 결함이 **양쪽에 같은 정도로** 걸렸었다는 뜻이고, 그것이 이 계열의
    「대칭이라 방향은 안 뒤집힌다」는 주장의 실측 근거다.
    """
    records: list[dict[str, object]] = []
    scoped = [r for r in rows if r.lens == BASELINE_LENS]
    tf_order = {tf: i for i, tf in enumerate(TIMEFRAMES)}
    seg_order = {s: i for i, s in enumerate(SEGMENT_LABELS)}
    groups: dict[tuple[str, str], list[PositionNullRow]] = defaultdict(list)
    for r in scoped:
        groups[(r.timeframe, r.segment)].append(r)
    for (timeframe, segment), cells in sorted(
        groups.items(), key=lambda kv: (tf_order.get(kv[0][0], 9), seg_order.get(kv[0][1], 9))
    ):
        real_taps = sum(c.real_taps or 0 for c in cells)
        real_inval = sum(c.real_invalidation_taps or 0 for c in cells)
        fake_taps = sum(c.fake_taps or 0 for c in cells)
        fake_inval = sum(c.fake_invalidation_taps or 0 for c in cells)
        if real_taps == 0 or fake_taps == 0:
            continue
        real_ratio = real_inval / real_taps
        fake_ratio = fake_inval / fake_taps
        records.append(
            {
                "timeframe": timeframe,
                "segment": segment,
                "real_taps": real_taps,
                "real_inval_pct": round(real_ratio * 100, 2),
                "fake_taps": fake_taps,
                "fake_inval_pct": round(fake_ratio * 100, 2),
                "delta_pp": round((real_ratio - fake_ratio) * 100, 2),
            }
        )
    return pd.DataFrame(records)


def symmetry_line(rows: Sequence[PositionNullRow]) -> str:
    """완료기준 5의 **한 줄** — 대칭이 성립하는가."""
    frame = symmetry_table(rows)
    if frame.empty:
        return "탭 인구조사 행이 없어 대칭을 확인할 수 없다."
    deltas = [abs(float(v)) for v in frame["delta_pp"].tolist()]
    worst = max(deltas)
    real_mean = float(pd.Series(frame["real_inval_pct"].tolist()).mean())
    fake_mean = float(pd.Series(frame["fake_inval_pct"].tolist()).mean())
    ok = worst <= SYMMETRY_TOLERANCE_PP
    head = "**대칭 성립**" if ok else "🚨 **대칭이 흔들린다**"
    return (
        f"{head} — 무효화 봉 탭 비율이 실제 팔 평균 {real_mean:.2f}% vs 가짜 팔 평균 "
        f"{fake_mean:.2f}%이고 (TF×구간) 최대 격차가 **{worst:.2f}%p**"
        f"({'≤' if ok else '>'} 허용 폭 {SYMMETRY_TOLERANCE_PP:.1f}%p)다. "
        + (
            "소급 취소(WAN-364)가 두 팔에 같은 정도로 걸렸었다는 뜻이라, 옛 판의 **방향**은 "
            "그 버그로 뒤집힐 종류가 아니었다(절대 수준은 얼어붙는다)."
            if ok
            else "두 팔이 그 버그의 보호를 다르게 받았을 수 있다 — 옛 판과의 방향 대조를 "
            "그만큼 약하게 읽어야 한다."
        )
    )


# --------------------------------------------------------------------------- #
# 요약 렌더
# --------------------------------------------------------------------------- #


def build_summary_markdown(rows: Sequence[PositionNullRow]) -> str:
    baseline = [r for r in rows if r.lens == BASELINE_LENS]
    pen = [r for r in rows if r.lens == PEN_LENS]
    symbols = sorted({_short(r.symbol) for r in rows})
    timeframes = [tf for tf in TIMEFRAMES if any(r.timeframe == tf for r in rows)]
    lines: list[str] = [
        "# WAN-403 — 「오더블록 자리가 아무 자리보다 나은가」 오늘 엔진·오늘 좌표 재측정",
        "",
        f"창 **{DEFAULT_START} ~ {DEFAULT_END}** · **{len(symbols)}종목**"
        f"({', '.join(symbols)}) × TF({' · '.join(timeframes)}) × 구간(IS · oos_warm) · "
        "롱 축 단독. 대조군 = **같은 방향·존폭·빈도·무효화 규칙을 매칭하고 위치만 무작위**로 "
        "뿌린 가짜 존(WAN-248 설계 그대로).",
        "",
        "🚨 **이 표의 용도가 다르다** — 「무엇을 채택할까」가 아니라 **「이 접근을 계속할 "
        "것인가」**를 묻는다.",
        "",
        f"검정한 엔진: `{describe_engine()}`.",
        "",
        "**자는 WAN-248 그대로다** — 매칭 널 · `pool_k` · 심볼 층화 순열 · 유효 셀 기준"
        f"(거래 ≥{MIN_TRADES_FOR_VERDICT}) · 유의 기준(p≤{ALPHA} **이면서** 실제>무작위평균) · "
        f"부트스트랩 {BOOTSTRAP_ITERATIONS}회를 **상수·함수로 import**한다. 바꾼 것은 좌표 "
        "셋(심볼 12 · 존폭 필터 핀 제거 · TF 자동)뿐이다.",
        "",
        "## §1 갈래 판정 (공식 렌즈 `baseline`)",
        "",
        branch_verdict(baseline),
        "",
        f"> 선은 착수 전에 못 박았다 — (가) 뒷구간 ≥ {GROWN_OOS_RATIO:.1%} · "
        f"(다) 앞구간 ≤ {CHANCE_RATIO:.1%}(= 2α) · 그 사이는 (나). 회귀 테스트가 이 값을 "
        "잠근다(결과를 보고 선을 옮기지 못하게).",
        "",
        "### TF × 구간 요약 (`baseline`)",
        "",
        _md_table(_round_today(summary_table(baseline, lens=BASELINE_LENS))),
        "",
        "🚨 **총수익 %(`real_mean`·`random_mean`)로 판정하지 말 것** — 이 좌표에서 복리 "
        "착시가 있다(WAN-169/213). 옛 판이 그 열로 냈기에 대조용으로 병기할 뿐이고, 판정 열은 "
        "**유의 셀 수**와 **거래당 net R**(`real_net_r`·`random_net_r`)이다.",
        "",
        "## §2 옛 판(WAN-248)과의 대조 — 비율과 방향만",
        "",
        old_comparison_line(baseline),
        "",
        "⚠️ **셀을 직접 비교하지 말 것** — 좌표가 통째로 다르다(엔진: 소급 취소 → 인과 · "
        "존폭 필터 1.28 → 꺼짐 · 9종목 → 12종목 · 3TF → 4TF · 익절 테이커 → 메이커). "
        "존폭 필터를 끄면 매매 대상 자체가 넓어져 거래가 크게 는다(WAN-384 실측 ＋56%).",
        "",
        "## §3 체결 보수화 병기 (`pen_5bp`)",
        "",
        (branch_verdict(pen, lens=PEN_LENS) if pen else "(pen_5bp 미측정)"),
        "",
        (old_comparison_line(pen, lens=PEN_LENS) if pen else ""),
        "",
        (_md_table(_round_today(summary_table(pen, lens=PEN_LENS))) if pen else ""),
        "",
        "## §4 대칭 확인 — 소급 취소는 두 팔에 같은 정도로 걸렸었나",
        "",
        symmetry_line(rows),
        "",
        _md_table(symmetry_table(rows)),
        "",
        "## §5 편중 — leave-one-out (종목 전부)",
        "",
        *leave_one_out_lines(baseline, lens=BASELINE_LENS),
        "",
        "## §6 셀별 결과 (`baseline`)",
        "",
        cell_table(baseline, lens=BASELINE_LENS),
        "",
        "## 결론 · 인용 금지",
        "",
        "- **측정 전용** — 기본값·토대 불변(`ConfluenceParams()`·`OrderBlockParams()`·"
        "`LeverageBookParams()`) · **핀 하나도 없음**(WAN-305) · DB 불변(WAN-194) · "
        "실거래 보류(`ALPHABLOCK_LIVE_TRADING=false`) 유지.",
        "- ⚠️ **이 표는 익절 1.5R에서 쟀다** — 익절 배수를 바꾸는 결정이 나면 **재확인이 "
        "필요하다**(사용자 결정 2026-09-02). 배수는 양쪽에 똑같이 걸려 방향이 뒤집힐 구조는 "
        "아니지만, 가까운 배수는 「아무 자리에 사도 닿아」 위치 정보를 묻는 둔한 자다"
        "(WAN-395: 0.4R에서 거래의 40%가 같은 분 익절).",
        "- ⚠️ **「위치에 정보가 있다」를 「엣지가 있다」로 인용 금지** — 옛 판이 앞구간 우위를 "
        "보이고도 뒷구간에서 못 넘어갔다. **앞구간 우위는 채택 근거가 아니다.**",
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386)을 뒤집거나 확정하지 "
        "않는다** — 그쪽은 *「존 안에서 타이밍」*을 묻고 이 표는 *「존이라는 자리」*를 묻는다. "
        "**다른 질문이다.**",
        "- ⚠️ 전부 `baseline`·`pen_5bp` 렌즈 위 값이고 **틱·호가는 여전히 미측정**"
        "(WAN-98 Canceled).",
        "- 🚨 **결과가 (다)여도 실패가 아니라 결론이다** — 계산 결함(소급 취소·허수 슬리피지·"
        "페이퍼 비용 누락)을 걷어낸 끝에 나온 답이고, **실거래를 켜기 전에 나온 것이 훨씬 "
        "낫다.**",
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

PARTS: tuple[str, ...] = ("null", "summary", "all")


def _write(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def merge_shards(shards: Sequence[Path], out: Path = NULL_CSV) -> int:
    """TF별로 따로 낸 조각 CSV를 하나로 합친다.

    🚨 `--append`는 한 파일을 읽고 다시 쓰므로 **동시에 도는 두 실행이 서로를 덮는다**.
    TF를 병렬로 돌리려면 각자 `--out`으로 자기 조각에 쓰고 여기서 합쳐야 한다(잃어버린
    갱신을 조용히 만들지 않는다).
    """
    frames = [pd.read_csv(path) for path in shards if path.exists()]
    if not frames:
        return 0
    frame = pd.concat(frames, ignore_index=True)
    _write(frame, out)
    return len(frame)


def _run_summary() -> None:
    if not NULL_CSV.exists():
        print(f"[wan403] {NULL_CSV} 없음 — 먼저 --part null을 돌리세요.")
        return
    rows = rows_from_csv(NULL_CSV)
    SUMMARY_MD.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_MD.write_text(build_summary_markdown(rows), encoding="utf-8")
    print(f"[wan403] summary → {SUMMARY_MD}")


def _csv_list(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WAN-403 오더블록 존 위치 매칭 널 — 오늘 엔진·오늘 좌표"
    )
    parser.add_argument("--part", type=str, default="all", choices=PARTS)
    parser.add_argument("--tf", type=str, default=None, help="TF 목록(콤마). 미지정=15m,1h,2h,4h")
    parser.add_argument("--symbols", type=str, default=None, help="심볼 목록(콤마). 미지정=12종목")
    parser.add_argument(
        "--seg", type=str, default=None, help="구간 목록(콤마: is,oos_warm,oos). 미지정=is,oos_warm"
    )
    parser.add_argument(
        "--lenses", type=str, default=None, help="렌즈(콤마). 미지정=baseline,pen_5bp"
    )
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument("--pool-k", type=int, default=POOL_K)
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--jobs", type=int, default=1, help="🚨 램이 병목 — 2 권고")
    parser.add_argument("--append", action="store_true", help="CSV에 덧붙인다(TF·심볼 분할 실행).")
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="이 실행의 산출 CSV 경로(TF 병렬 실행용 조각). 미지정=정본 CSV.",
    )
    parser.add_argument(
        "--merge",
        type=str,
        default=None,
        help="조각 CSV들(콤마)을 정본 CSV로 합친다. `--part summary`와 함께 쓴다.",
    )
    args = parser.parse_args(argv)

    part = str(args.part)
    out_csv = Path(str(args.out)) if args.out else NULL_CSV
    if args.merge:
        merged = merge_shards([Path(p) for p in str(args.merge).split(",") if p.strip()])
        print(f"[wan403] merge {merged}행 → {NULL_CSV}")
    if part in ("null", "all"):
        rows = run_today_null(
            symbols=_csv_list(args.symbols, SYMBOLS),
            timeframes=_csv_list(args.tf, TIMEFRAMES),
            segments=_csv_list(args.seg, DEFAULT_SEGMENTS),
            lenses=_csv_list(args.lenses, DEFAULT_LENSES),
            start=str(args.start),
            end=str(args.end),
            pool_k=int(args.pool_k),
            iterations=int(args.iterations),
            jobs=int(args.jobs),
        )
        frame = rows_to_frame(rows)
        if args.append and out_csv.exists():
            frame = pd.concat([pd.read_csv(out_csv), frame], ignore_index=True)
        _write(frame, out_csv)
        print(f"[wan403] null {len(frame)}행 → {out_csv}")

    if part in ("summary", "all"):
        _run_summary()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
