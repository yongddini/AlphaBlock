"""WAN-389 — 재탭 차단의 +0.0152R은 「재탭을 뺀 몫」인가 「재진입이 그 자리를 채운 몫」인가.

**묻는 것**: WAN-388이 잰 재탭 차단 효과에 **채널이 하나 섞여 있다**. 그 표의 같은 줄에
이 숫자가 나란히 있었다(`oos_warm`):

| 팔 | 거래 | 재탭 거래 | **재진입 거래** |
| -- | --: | --: | --: |
| `분리·매탭`(채택 북) | 14,825 | 4,824 | **1,591** |
| `분리·첫탭만` | 12,945 | 0 | **4,142 (2.6배)** |

재탭 후보가 슬롯을 안 잡으니 **그 빈자리를 재진입이 채웠다** — 후보는 −57%인데 북 거래는
−12.7%만 줄었다. 즉 「재탭 차단」 팔은 사실 **두 가지를 동시에** 했다. 📌 **북에서만 생기는
채널이다**(칸이 한 지갑을 공유하므로 한쪽이 자리를 비우면 다른 쪽이 쓴다 — WAN-323이 반익절
래더에서 겪은 것과 같은 부류이고 per-cell로는 원리적으로 안 보인다, WAN-341).

격자 — **2×2**(재탭 × 재진입), 존은 넷 다 **분리**(원본 존):

| 팔 | 재탭 | 재진입 | 왜 |
| -- | -- | -- | -- |
| `split_every` (분리·매탭) | 켬 | 켬 | ← **오늘 채택 북**(검산 기준) |
| `split_once` (분리·첫탭만) | **끔** | 켬 | ← WAN-388 실측 +0.0152R의 한쪽 |
| `split_every_no_reentry` | 켬 | **끔** | 새로 |
| `split_once_no_reentry` | **끔** | **끔** | 새로 |

**판정 줄**은 재탭 차단 효과를 **두 번** 낸다::

    재탭 차단(재진입 켬) = split_once            − split_every            ← WAN-388 실측
    재탭 차단(재진입 끔) = split_once_no_reentry − split_every_no_reentry ← 이 이슈가 낼 값
    재진입이 채운 몫     = 위 − 아래

📌 **컴퓨트는 팔 넷이 아니라 후보 생성 둘이다** — `reentry`는 **후보 생성**이 아니라
**배치** 축이다(`_segment_cells(include_reentry=)`가 payload에 이미 실린 재진입 후보를 base와
합칠지만 고른다 · base 후보는 불변이다, `wan169.run_cells` 독스트링). 그래서 재탭 모드마다
payload를 **한 번만** 만들고 배치를 두 번 한다 — 그리고 그 성질 자체가 검산 (b)다.

**판정 자**: 거래당 **net R**(+ 거래 수 **항상 병기** — 재진입을 끄면 거래가 크게 줄므로
「덜 매매해서 좋아 보이는 것」과 구분해야 한다, WAN-378). 판정선은 코드 상수 `NOISE_R`
(0.005R, WAN-366/370)이고 **착수 전에 못 박았다**.

**좌표**: 오늘 채택 그대로 — 12종목 × 4TF · 못 박은 6년 · 존폭 필터 끔(WAN-384) · 인과
취소(WAN-365) · 익절 메이커(WAN-370) · cap_only 5배(WAN-213) · **핀 없음**(WAN-305).

**검산**
* (a) `split_every` ≡ `wan388_merge_x_retap_grid.csv`의 같은 팔 — **팔을 더해도 기존 행이
  안 움직였다**는 증거이자 이 모듈의 배선이 WAN-388과 같다는 독립 재계산이다.
* (b) 재진입 끈 팔의 **재진입 거래가 전 구간 0건** — 라벨이 아니라 **동작**으로 축이 실제로
  걸렸음을 증명한다(WAN-91/95/112/123/159 부류 방지).
* (c) 첫탭만 팔의 배치 거래에 **재탭(`tap_index>=1`)이 하나도 없다**(WAN-388 검산 (c) 계승).
* (d) 네 팔의 칸 수·심볼 수가 같다 — 축이 후보/배치를 바꾸지 **실행 좌표**를 안 바꾼다.

⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` 기본값을
하나도 안 바꾼다. ❌ **재진입을 끄자는 제안이 아니다** — `reentry=True`(band)는 WAN-273
**사용자 결정**이고 불변이다. 재진입 끈 팔은 **귀속용 반사실**이지 후보가 아니다.
⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값 · 체결 보수화(`pen_5bp`)는 범위 밖 · 총수익
%는 복리 착시라 판정 자가 아니다(WAN-346) · **위험의 모양은 이 좌표에서 못 잰다**(거래당
기대값이 음수라 지갑 층 지표가 「정의 상실」 — WAN-388 §2) · **「엣지 없음」(WAN-84/88/111/
114/124/151/201/248/386) 불변**(이 표는 *같은 셋업을 몇 번에 나눠 잡나*를 묻는다).

재현::

    uv run python -m backtest.wan389_retap_attribution --jobs 4
    uv run python -m backtest.wan389_retap_attribution --retaps every_tap   # 후보 생성 하나만
    uv run python -m backtest.wan389_retap_attribution --from-csv           # 요약만
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import ConfigDict

from backtest import harness
from backtest.book_cli import BookSegment, iter_book_segments
from backtest.leverage_book import LeverageBookParams
from backtest.models import BacktestConfig
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS
from backtest.wan336_same_step_tp import ADOPTED_CELL_KWARGS
from backtest.wan376_zone_thickness import ADOPTED_STOP_GUARD
from backtest.wan388_merge_retap_census import ADOPTED_COMBINE_OBS, ADOPTED_RETAP_MODE
from backtest.wan388_merge_x_retap import (
    ADOPTED_ARM as WAN388_ADOPTED_ARM,
)
from backtest.wan388_merge_x_retap import (
    GRID_CSV_PATH as WAN388_GRID_CSV_PATH,
)
from backtest.wan388_merge_x_retap import (
    NOISE_R,
    ChecksumRow,
    GridRow,
    _cfg,
    _row_kwargs,
    _short,
    wallet_defined,
)

REPORTS_DIR = Path("backtest/reports")
GRID_CSV_PATH = REPORTS_DIR / "wan389_retap_attribution_grid.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan389_leave_one_out.csv"
CHECKSUM_CSV_PATH = REPORTS_DIR / "wan389_checksum.csv"
SUMMARY_PATH = REPORTS_DIR / "wan389_retap_attribution_summary.md"

#: 이 격자의 구간 — `full`(6년 전체)과 주 수치 `oos_warm`. 차가운 `is`/`oos`는 컴퓨트를
#: 두 배로 만드는데 이슈의 질문(귀속)에 답을 더하지 않아 기본으로 끈다(`--cold-segments`).
DEFAULT_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

#: leave-one-out 구간 — 종목 편중 확인용.
LOO_SEGMENTS: tuple[str, ...] = ("full", PRIMARY_OOS)

#: 채택 좌표의 신규 3종목(WAN-182) — 묶어 빼 보는 leave-one-out 라벨.
NEW_THREE: tuple[str, ...] = ("DOGE", "LINK", "LTC")

#: 채택 북과 대조하는 WAN-388 팔 이름. 이 모듈의 `split_every`가 그 팔과 같아야 한다.
WAN388_REFERENCE_ARM = WAN388_ADOPTED_ARM


# --------------------------------------------------------------------------- #
# 팔
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    """격자의 한 팔.

    🚨 **두 축의 성격이 다르다** — `retap_mode`는 소비하는 시그널 목록을 바꾸므로 **후보를
    다시 만들어야** 하고(팔마다 별도 생성), `reentry`는 **배치 축**이라 같은 payload를 두
    번 배치하면 된다(`_segment_cells(include_reentry=)`). 이 비대칭이 컴퓨트를 절반으로
    만들고, 동시에 검산 (b)가 성립하는 이유다.
    """

    name: str
    label: str
    retap_mode: str
    reentry: bool

    #: 존은 넷 다 채택값(분리) — 병합 축은 WAN-388이 ≈0으로 닫았다(격자만 두 배가 된다).
    combine_obs: bool = ADOPTED_COMBINE_OBS

    @property
    def is_adopted(self) -> bool:
        """오늘 채택 북과 같은 팔인가 — 검산 (a)의 기준이다."""
        return self.retap_mode == ADOPTED_RETAP_MODE and self.reentry


ARMS: tuple[Arm, ...] = (
    Arm("split_every", "분리·매탭·재진입 켬", retap_mode="every_tap", reentry=True),
    Arm("split_once", "분리·첫탭만·재진입 켬", retap_mode="once", reentry=True),
    Arm("split_every_no_reentry", "분리·매탭·재진입 끔", retap_mode="every_tap", reentry=False),
    Arm("split_once_no_reentry", "분리·첫탭만·재진입 끔", retap_mode="once", reentry=False),
)
ARMS_BY_NAME: dict[str, Arm] = {arm.name: arm for arm in ARMS}
ADOPTED_ARM = "split_every"

#: 후보 생성 단위 — 재탭 모드 하나가 팔 둘을 먹인다.
RETAP_MODES: tuple[str, ...] = ("every_tap", "once")


def arms_for_retap(retap_mode: str) -> tuple[Arm, ...]:
    return tuple(arm for arm in ARMS if arm.retap_mode == retap_mode)


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class AttributionRow(GridRow):
    """WAN-388 행에 **재진입 축**을 더한 것 — 열은 그대로라 두 표를 나란히 읽을 수 있다."""

    model_config = ConfigDict(frozen=True)

    reentry: bool


class LooRow(AttributionRow):
    """종목 하나(또는 신규 3종목)를 빼고 **지갑을 다시 배치**한 행 (WAN-316 스코프 패턴)."""

    exclude: str


# --------------------------------------------------------------------------- #
# 후보 생성 · 배치
# --------------------------------------------------------------------------- #


def _cell_kwargs() -> dict[str, object]:
    """채택 좌표 그대로 — 🚨 **익절 청산 유동성을 명시**한다(WAN-370/373, 잊으면 옛 회계).

    `ADOPTED_CELL_KWARGS`가 `reentry=True`를 이미 담고 있다(WAN-305). **여기서는 항상 켠
    채로 만든다** — 재진입 후보를 payload에 실어 두고 배치에서 고르기 때문이고, 그래야 두
    재진입 팔이 **글자 그대로 같은 base 후보**를 쓴다(검산 (b)의 전제).
    """
    return {
        **ADOPTED_CELL_KWARGS,
        "take_profit_liquidity": harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    }


def build_payloads(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    retap_mode: str,
    start: str,
    end: str,
    jobs: int,
    cold_segments: bool = False,
) -> list[CellPayload]:
    """이 재탭 모드의 후보를 만든다 — **팔이 아니라 재탭 모드가 단위다**.

    🚨 `retap_mode`는 소비하는 시그널 목록 자체를 바꾸므로 모드마다 별도 생성이다. 반면
    `reentry`는 배치 축이라 이 payload 하나가 재진입 켠 팔과 끈 팔을 **둘 다** 먹인다.
    """
    return run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        cold_segments=cold_segments,
        engine_check=False,
        combine_obs=ADOPTED_COMBINE_OBS,
        retap_mode=retap_mode,
        observe_zone_width_atr=False,
        **_cell_kwargs(),  # type: ignore[arg-type]
    )


def place(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str],
    include_reentry: bool,
    compound: bool = False,
) -> list[BookSegment]:
    """채택 북 배치 — 🚨 **여기에도** 익절 청산 유동성을 명시한다(한 표가 한 회계).

    `include_reentry`가 이 이슈의 축이다: `True`가 채택 규칙(WAN-273/305)이고 `False`는
    **귀속용 반사실**이다(채택 제안이 아니다). `compound=False`(기본)가 이 격자의 판이다
    (WAN-346 §2: 복리 총수익은 판정 자가 아니다).
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=list(segments),
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=include_reentry,
        min_stop_distance_fraction=ADOPTED_STOP_GUARD,
        compound_sizing=compound,
        take_profit_liquidity=harness.ADOPTED_TAKE_PROFIT_LIQUIDITY,
    )


def entry_in_zone(payloads: Sequence[CellPayload], segment: str, *, include_reentry: bool) -> float:
    """진입가의 **존 근단으로부터의 깊이** 중앙값 (0 = 근단 · 1 = 원단 = 무효화 경계).

    WAN-388의 같은 열이되 **재진입 후보를 셀지 고를 수 있다** — 재진입 끈 팔에서 그 후보를
    같이 세면 배치되지도 않은 주문이 열을 움직여 라벨이 거짓이 된다.

    🚨 `oos_warm`은 payload에 **없는 키**다 — 배치가 `full` 후보를 평가 경계로 걸러 만든다
    (`_segment_cells`). 그대로 `get("oos_warm")`을 하면 조용히 빈 목록이 돌아와 이 열이 전부
    0이 된다(주 수치 구간에서 하필).
    """
    positions: list[float] = []
    for payload in payloads:
        source = harness.SEGMENT_FULL if segment == PRIMARY_OOS else segment
        cands = list(payload.candidates.get(source, ()))
        if include_reentry:
            cands += list(payload.reentry_candidates.get(source, ()))
        if segment == PRIMARY_OOS:
            cands = [c for c in cands if c.trigger_time >= payload.boundary_ms]
        for cand in cands:
            ob = cand.order_block
            if ob is None:
                continue
            height = ob.top - ob.bottom
            if height <= 0:
                continue
            if cand.side.sign > 0:
                positions.append((ob.top - cand.entry_price) / height)
            else:
                positions.append((cand.entry_price - ob.bottom) / height)
    return statistics.median(positions) if positions else 0.0


# --------------------------------------------------------------------------- #
# 행 만들기
# --------------------------------------------------------------------------- #


def _arm_fields(arm: Arm) -> dict[str, object]:
    return {
        "arm": arm.name,
        "label": arm.label,
        "combine_obs": arm.combine_obs,
        "retap_mode": arm.retap_mode,
        "reentry": arm.reentry,
        "adopted_arm": arm.is_adopted,
    }


def build_arm_rows(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    num_symbols: int,
    segments: Sequence[str] = DEFAULT_SEGMENTS,
    cfg: BacktestConfig | None = None,
) -> list[AttributionRow]:
    config = cfg if cfg is not None else _cfg()
    rows: list[AttributionRow] = []
    for segment in place(
        payloads,
        start_ms=start_ms,
        end_ms=end_ms,
        segments=segments,
        include_reentry=arm.reentry,
    ):
        rows.append(
            AttributionRow(
                **_arm_fields(arm),
                **_row_kwargs(
                    segment,
                    config,
                    num_symbols=num_symbols,
                    entry_position=entry_in_zone(
                        payloads, segment.segment, include_reentry=arm.reentry
                    ),
                ),
            )
        )
    return rows


def build_leave_one_out(
    payloads: Sequence[CellPayload],
    *,
    arm: Arm,
    start_ms: int,
    end_ms: int,
    log: bool = True,
) -> list[LooRow]:
    """종목 하나씩 빼고 **지갑을 다시 배치**한다 — 라벨 필터가 아니다(WAN-316)."""
    cfg = _cfg()
    rows: list[LooRow] = []
    all_symbols = sorted({_short(p.symbol) for p in payloads})
    drops: list[tuple[str, tuple[str, ...]]] = [(f"-{s}", (s,)) for s in all_symbols]
    present_new = tuple(s for s in NEW_THREE if s in all_symbols)
    if len(present_new) > 1:
        drops.append(("-new3", present_new))
    for drop_label, dropped in drops:
        drop = {s.upper() for s in dropped}
        kept = [p for p in payloads if _short(p.symbol) not in drop]
        if not kept:
            continue
        for segment in place(
            kept,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=LOO_SEGMENTS,
            include_reentry=arm.reentry,
        ):
            rows.append(
                LooRow(
                    **_arm_fields(arm),
                    exclude=drop_label,
                    **_row_kwargs(
                        segment,
                        cfg,
                        num_symbols=len({p.symbol for p in kept}),
                        entry_position=entry_in_zone(
                            kept, segment.segment, include_reentry=arm.reentry
                        ),
                    ),
                )
            )
    if log:
        print(f"[wan389] {arm.name}: leave-one-out {len(drops)}판 완료", flush=True)
    return rows


# --------------------------------------------------------------------------- #
# 검산
# --------------------------------------------------------------------------- #

#: 검산 (a)가 대조하는 열 — WAN-388 CSV와 이 모듈의 같은 팔이 **비트 일치**해야 한다.
_WAN388_CHECK_METRICS: tuple[str, ...] = (
    "num_trades",
    "win_rate",
    "mean_net_r",
    "gross_r",
    "cost_r",
    "stop_width_p50",
    "retap_trades",
    "reentry_trades",
    "total_return_flat",
    "max_drawdown",
)


def check_against_wan388(
    rows: Sequence[AttributionRow], *, path: Path = WAN388_GRID_CSV_PATH
) -> list[ChecksumRow]:
    """검산 (a) — 이 모듈의 `split_every`가 WAN-388 CSV의 같은 팔과 비트 일치하는가.

    🚨 **이것이 「팔을 더해도 기존 행이 안 움직였다」의 증거다.** 두 모듈이 후보를 각각
    만들어(다른 실행 · 다른 날) 같은 숫자를 내야 한다 — 어긋나면 배선이 갈렸다는 뜻이고
    아래 판정 줄 전체가 WAN-388 값과 비교 불가가 된다.

    🚨 **좌표가 다르면 아예 내지 않는다** — `--pilot`(1종목 × 4h)처럼 다른 좌표를 48칸 판과
    대조하면 「최대 절대차 4.9e+04」 같은 수가 검산 표에 앉아 **배선 오류처럼 읽힌다**. 검산은
    같은 질문에 답한 두 계산을 대조하는 것이지 아무 두 수를 빼는 게 아니다.
    """
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    ref = frame[frame["arm"] == WAN388_REFERENCE_ARM]
    by_segment = {str(rec["segment"]): rec for rec in ref.to_dict("records")}
    out: list[ChecksumRow] = []
    for row in rows:
        if row.arm != ADOPTED_ARM:
            continue
        other = by_segment.get(row.segment)
        if other is None:
            continue
        if (int(other["num_cells"]), int(other["num_symbols"])) != (
            row.num_cells,
            row.num_symbols,
        ):
            continue
        for metric in _WAN388_CHECK_METRICS:
            lhs = float(getattr(row, metric))
            rhs = float(other[metric])
            out.append(
                ChecksumRow(
                    check="a WAN-388 같은 팔",
                    arm=row.arm,
                    segment=row.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


def check_reentry_axis(rows: Sequence[AttributionRow]) -> list[ChecksumRow]:
    """검산 (b) — 재진입 끈 팔의 **재진입 거래가 전 구간 0건**.

    🚨 **라벨이 아니라 동작으로** 축이 걸렸음을 증명한다. `reentry=False`인데 재진입 거래가
    남아 있으면 그 팔은 이름만 「재진입 끔」이고 조용히 채택 팔로 돈 것이다(WAN-91/95/112/
    123/159 부류). 같은 payload를 두 번 배치하는 구조라 **이 검산이 특히 중요하다**.
    """
    return [
        ChecksumRow(
            check="b 재진입 축이 실제로 걸렸나",
            arm=row.arm,
            segment=row.segment,
            metric="reentry_trades",
            left=float(row.reentry_trades),
            right=0.0,
            abs_diff=float(row.reentry_trades),
        )
        for row in rows
        if not row.reentry
    ]


def check_retap_axis(rows: Sequence[AttributionRow]) -> list[ChecksumRow]:
    """검산 (c) — 첫탭만 팔의 배치 거래에 재탭이 하나도 없다 (WAN-388 계승)."""
    return [
        ChecksumRow(
            check="c 재탭 축이 실제로 걸렸나",
            arm=row.arm,
            segment=row.segment,
            metric="retap_trades",
            left=float(row.retap_trades),
            right=0.0,
            abs_diff=float(row.retap_trades),
        )
        for row in rows
        if row.retap_mode == "once"
    ]


def check_arm_invariants(rows: Sequence[AttributionRow]) -> list[ChecksumRow]:
    """검산 (d) — 팔이 후보·배치를 바꾸지 **실행 좌표**를 안 바꾼다(칸 수·심볼 수 불변)."""
    out: list[ChecksumRow] = []
    base = {row.segment: row for row in rows if row.arm == ADOPTED_ARM}
    for row in rows:
        ref = base.get(row.segment)
        if ref is None or row.arm == ADOPTED_ARM:
            continue
        for metric in ("num_cells", "num_symbols"):
            lhs = float(getattr(row, metric))
            rhs = float(getattr(ref, metric))
            out.append(
                ChecksumRow(
                    check="d 팔 사이 좌표 불변",
                    arm=row.arm,
                    segment=row.segment,
                    metric=metric,
                    left=lhs,
                    right=rhs,
                    abs_diff=abs(lhs - rhs),
                )
            )
    return out


# --------------------------------------------------------------------------- #
# 판정
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Verdict:
    """§2 판정 줄 — 재탭 차단 효과를 재진입 켠 판과 끈 판에서 각각 낸다."""

    segment: str
    retap_effect_reentry_on: float | None
    """`split_once − split_every` — WAN-388이 낸 값(재진입이 빈자리를 채운 채)."""
    retap_effect_reentry_off: float | None
    """`split_once_no_reentry − split_every_no_reentry` — 이 이슈가 낸 값."""
    reentry_fill: float | None
    """둘의 차 = **재진입이 채운 몫**. 양수면 재진입이 효과를 부풀렸다는 뜻이다."""
    trades_on_every: int | None
    trades_on_once: int | None
    trades_off_every: int | None
    trades_off_once: int | None
    reentry_trades_on_every: int | None
    reentry_trades_on_once: int | None

    @property
    def label(self) -> str:
        """한 문장 결론의 라벨 — 🚨 **규칙은 착수 전에 못 박았다**(사후에 고르지 않는다).

        판정선은 `NOISE_R`(0.005R)이고 다음 넷 중 하나다:

        * **판정 불가** — 필요한 팔이 없다.
        * **재진입이 채운 몫** — 재진입을 끄면 효과가 노이즈선 아래로 내려간다.
        * **재탭을 뺀 몫** — 재진입을 꺼도 효과가 남고, 두 값의 차가 노이즈선 안이다.
        * **둘 다** — 재진입을 꺼도 효과가 남지만 차도 노이즈선을 넘는다.
        """
        on, off, fill = (
            self.retap_effect_reentry_on,
            self.retap_effect_reentry_off,
            self.reentry_fill,
        )
        if on is None or off is None or fill is None:
            return "판정 불가"
        if abs(off) < NOISE_R:
            return "재진입이 채운 몫"
        if abs(fill) < NOISE_R:
            return "재탭을 뺀 몫"
        return "둘 다"

    @property
    def residual_share(self) -> float | None:
        """재진입을 꺼도 남는 비율 — 기준(재진입 켠 효과)이 노이즈선 안이면 **내지 않는다**.

        🚨 WAN-115가 문서화한 부호·크기 함정 — 기준이 0 언저리면 비율이 뜻을 잃는다(작은
        분모가 100%를 훌쩍 넘는 수를 만들어 「유지」로 읽힌다).
        """
        on, off = self.retap_effect_reentry_on, self.retap_effect_reentry_off
        if on is None or off is None or abs(on) < NOISE_R:
            return None
        return off / on

    @property
    def eaten_share(self) -> float | None:
        """재진입이 **깎아 먹은** 비율 = `|채운 몫| ÷ 재진입 끈 효과`.

        🚨 **「채운 몫」이 음수일 때만 뜻이 있다** — 그때 재진입은 효과를 부풀린 게 아니라
        빈 슬롯을 **더 나쁜 거래로** 채워 잠재 이득을 깎은 것이고, 이 값이 그 몫이다.
        분모(재진입 끈 효과)가 노이즈선 안이면 비율이 뜻을 잃으므로 **내지 않는다**
        (`residual_share`와 같은 WAN-115 함정 회피).
        """
        fill, off = self.reentry_fill, self.retap_effect_reentry_off
        if fill is None or off is None or fill >= 0 or abs(off) < NOISE_R:
            return None
        return abs(fill) / abs(off)


def _row_of(rows: Sequence[AttributionRow], arm: str, segment: str) -> AttributionRow | None:
    return next((r for r in rows if r.arm == arm and r.segment == segment), None)


def verdict_for(rows: Sequence[AttributionRow], segment: str) -> Verdict:
    on_every = _row_of(rows, "split_every", segment)
    on_once = _row_of(rows, "split_once", segment)
    off_every = _row_of(rows, "split_every_no_reentry", segment)
    off_once = _row_of(rows, "split_once_no_reentry", segment)

    def diff(a: AttributionRow | None, b: AttributionRow | None) -> float | None:
        return None if a is None or b is None else a.mean_net_r - b.mean_net_r

    on = diff(on_once, on_every)
    off = diff(off_once, off_every)
    return Verdict(
        segment=segment,
        retap_effect_reentry_on=on,
        retap_effect_reentry_off=off,
        reentry_fill=None if on is None or off is None else on - off,
        trades_on_every=None if on_every is None else on_every.num_trades,
        trades_on_once=None if on_once is None else on_once.num_trades,
        trades_off_every=None if off_every is None else off_every.num_trades,
        trades_off_once=None if off_once is None else off_once.num_trades,
        reentry_trades_on_every=None if on_every is None else on_every.reentry_trades,
        reentry_trades_on_once=None if on_once is None else on_once.reentry_trades,
    )


# --------------------------------------------------------------------------- #
# 입출력
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[GridRow]) -> pd.DataFrame:
    return pd.DataFrame([row.model_dump() for row in rows])


def grid_from_csv(path: Path = GRID_CSV_PATH) -> list[AttributionRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [AttributionRow.model_validate(rec) for rec in frame.to_dict("records")]


def loo_from_csv(path: Path = LOO_CSV_PATH) -> list[LooRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [LooRow.model_validate(rec) for rec in frame.to_dict("records")]


def checksum_from_csv(path: Path = CHECKSUM_CSV_PATH) -> list[ChecksumRow]:
    if not path.exists():
        return []
    frame = pd.read_csv(path)
    return [ChecksumRow.model_validate(rec) for rec in frame.to_dict("records")]


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


#: 부동소수 끝자리 잡음의 상한 — 이 아래는 **불일치가 아니다**.
#:
#: 🚨 검산 (a)는 CSV를 **텍스트로 왕복**시켜 대조하므로(`.6g`가 아니라 전체 정밀도라도
#: 십진 ↔ 이진 변환이 끝자리를 흔든다) 완전히 같은 계산도 1e-17 언저리가 남는다. 그것을
#: 「⚠️ 불일치」로 찍으면 **성공이 실패와 같은 모양**이 되고(WAN-194/318/321이 반복해 경계한
#: 자리의 거울상), 진짜 배선 오류가 그 소음에 묻힌다. WAN-151/161이 세운 관행대로
#: **일치 · 잡음 · 불일치를 다르게 찍는다**.
CHECKSUM_NOISE = 1e-9


def checksum_grade(abs_diff: float) -> str:
    """검산 절대차를 **일치 · 잡음 · 불일치** 셋으로 읽는다 (WAN-151/161 관행)."""
    if abs_diff == 0.0:
        return "비트 일치"
    if abs_diff < CHECKSUM_NOISE:
        return "부동소수 끝자리 잡음 — 같은 계산이다"
    return "⚠️ 불일치 — 배선을 확인할 것"


def _fmt(value: float | None, *, digits: int = 4) -> str:
    if value is None:
        return "—"
    mark = " (≈0)" if abs(value) < NOISE_R else ""
    return f"{value:+.{digits}f}R{mark}"


def _segments_present(rows: Sequence[AttributionRow]) -> list[str]:
    seen = {row.segment for row in rows}
    ordered = [s for s in ("full", "is", PRIMARY_OOS, "oos") if s in seen]
    return ordered + sorted(seen - set(ordered))


def build_summary_markdown(
    rows: Sequence[AttributionRow],
    loo: Sequence[LooRow],
    checks: Sequence[ChecksumRow],
    *,
    elapsed: float | None = None,
) -> str:
    out: list[str] = []
    out.append("# WAN-389 — 재탭 차단의 몫인가, 재진입이 채운 몫인가 (채택 북)")
    out.append("")
    out.append(
        "⚠️ **측정 전용** — `ConfluenceParams()`·`OrderBlockParams()`·`LeverageBookParams()` "
        "기본값 **불변**. ❌ **재진입을 끄자는 제안이 아니다** — `reentry=True`(band)는 "
        "WAN-273 **사용자 결정**이고 재진입 끈 팔은 **귀속용 반사실**이다."
    )
    out.append("")
    if not rows:
        out.append("🚨 격자 행이 없다 — 판정하지 않는다(빈 표에서 결론을 지어내지 않는다).")
        out.append("")
        return "\n".join(out)

    present = [arm for arm in ARMS if any(r.arm == arm.name for r in rows)]
    segments = _segments_present(rows)
    if len(present) < len(ARMS):
        out.append(
            "🚨 **2×2가 아직 안 찼다** — 지금 있는 팔: "
            + ", ".join(f"`{a.name}`" for a in present)
            + ". 재탭 차단 효과를 **두 번** 못 내면 귀속이 성립하지 않는다."
        )
        out.append("")

    out.append(f"## 1. 격자 (주 수치 `{PRIMARY_OOS}`)")
    out.append("")
    out.append(
        "| 팔 | 재탭 | 재진입 | 구간 | 거래 | 거래당 net R | gross R | 비용 R | 승률 | "
        "손절폭 p50 | 재탭 거래 | 재진입 거래 | MDD |"
    )
    out.append("| -- | -- | -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | --: |")
    for arm in present:
        for segment in segments:
            row = _row_of(rows, arm.name, segment)
            if row is None:
                continue
            mdd = f"{row.max_drawdown:.2%}" if wallet_defined(row) else "—"
            out.append(
                f"| {arm.label} | {'켬' if arm.retap_mode == 'every_tap' else '끔'} | "
                f"{'켬' if arm.reentry else '끔'} | {segment} | {row.num_trades:,} | "
                f"{row.mean_net_r:+.4f} | {row.gross_r:+.4f} | {row.cost_r:.4f} | "
                f"{row.win_rate:.2%} | {row.stop_width_p50:.3%} | {row.retap_trades:,} | "
                f"{row.reentry_trades:,} | {mdd} |"
            )
    out.append("")
    out.append(
        "- 🚨 **거래 수를 net R 옆에서 같이 읽는다** — 재진입을 끄면 거래가 크게 줄므로 "
        "「덜 매매해서 좋아 보이는 것」과 구분해야 한다(WAN-378이 실측한 함정)."
    )
    if any(not wallet_defined(r) for r in rows):
        out.append(
            "- 🚨 **MDD 열이 `—`인 행은 「정의 상실」이다 — 값이 없는 게 아니라 읽을 수 "
            "없다.** 거래당 기대값이 음수인 좌표라 지갑 층 위험 지표가 팔을 못 가른다"
            "(WAN-388 §2 실측). **이 격자는 위험의 모양을 재지 않았다.**"
        )
    out.append("")

    out.append("## 2. 판정 줄 — 재탭 차단 효과를 두 번 낸다")
    out.append("")
    out.append(
        "| 구간 | 재탭 차단(재진입 켬) | 재탭 차단(재진입 끔) | **재진입이 채운 몫** | "
        "재진입 끄고도 남는 비율 | 판정 |"
    )
    out.append("| -- | --: | --: | --: | --: | -- |")
    for segment in segments:
        v = verdict_for(rows, segment)
        share = "—" if v.residual_share is None else f"{v.residual_share:.0%}"
        out.append(
            f"| {segment} | {_fmt(v.retap_effect_reentry_on)} | "
            f"{_fmt(v.retap_effect_reentry_off)} | {_fmt(v.reentry_fill)} | {share} | "
            f"{v.label} |"
        )
    out.append("")
    out.append(
        "- **재진입이 채운 몫** = `재탭 차단(재진입 켬) − 재탭 차단(재진입 끔)`. 양수면 "
        "재진입이 빈 슬롯을 채워 효과를 **부풀렸다**는 뜻이다."
    )
    out.append(
        f"- `(≈0)`는 |값| < {NOISE_R}R(WAN-366/370 노이즈선)이라 0과 구분되지 않는다는 표시. "
        "🚨 **기준(재진입 켠 효과)이 노이즈선 안이면 비율을 내지 않는다** — 작은 분모는 뜻을 "
        "잃은 백분율을 만든다(WAN-115 함정)."
    )
    out.append("")

    out.append("## 3. 결론")
    out.append("")
    v = verdict_for(rows, PRIMARY_OOS)
    if v.label == "판정 불가":
        out.append(f"- 🚨 주 구간(`{PRIMARY_OOS}`)에 필요한 팔이 없어 **판정하지 않는다**.")
    elif v.label == "재진입이 채운 몫":
        out.append(
            f"- **재진입이 채운 몫이다** — 재진입을 끄면 재탭 차단 효과가 "
            f"{_fmt(v.retap_effect_reentry_on)} → {_fmt(v.retap_effect_reentry_off)}로 "
            f"노이즈선(±{NOISE_R}R) 안으로 내려앉는다. 즉 값은 「재탭을 빼서」가 아니라 "
            "**「재진입을 더 해서」** 나온 것이고, 재진입은 **이미 채택값으로 켜져 있다**"
            "(WAN-273). 🚨 그러면 권고는 `retap_mode`가 아니라 **「슬롯 배분을 어떻게 할 "
            "것인가」**로 옮겨간다 — 완전히 다른 축이다."
        )
    elif v.label == "재탭을 뺀 몫":
        out.append(
            f"- **재탭을 뺀 몫이다** — 재진입을 꺼도 효과가 "
            f"{_fmt(v.retap_effect_reentry_off)}로 남고 두 값의 차"
            f"({_fmt(v.reentry_fill)})가 노이즈선 안이다. 「재탭 거래가 첫탭 거래보다 "
            "나쁘다」는 **선별 사실**로 읽을 수 있다. ⚠️ **그래도 채택은 사용자 결정이다** — "
            "재-베이스라인이고 개발자 임의 착수 금지."
        )
    elif v.reentry_fill is not None and v.reentry_fill < 0:
        # 🚨 **이슈의 가설과 부호가 반대인 경우다** — 분류 규칙(`Verdict.label`)은 착수 전에
        # 못 박은 그대로 두고(사후에 고르지 않는다) **일어난 일을 부호대로 적는다**. 「채운
        # 몫」이 음수라는 것은 재진입이 효과를 **부풀린 게 아니라 깎았다**는 뜻이다.
        eaten = (
            f" 잠재 이득의 **{v.eaten_share:.0%}**가 관측값에 도달하기 전에 사라진다."
            if v.eaten_share is not None
            else ""
        )
        out.append(
            f"- **재탭을 뺀 몫이고, 재진입은 그 몫을 오히려 깎는다** — 이슈가 세운 가설과 "
            f"**부호가 반대다**. 재진입을 끄면 재탭 차단 효과가 "
            f"{_fmt(v.retap_effect_reentry_on)} → {_fmt(v.retap_effect_reentry_off)}로 "
            f"**커진다**(차 {_fmt(v.reentry_fill)}). 즉 재탭이 비운 슬롯을 재진입이 채우는 "
            "것은 맞지만, 그렇게 채워 넣은 거래가 **빼낸 재탭 거래보다 나빠서**" + eaten
        )
    else:
        out.append(
            f"- **둘 다 섞여 있다** — 재진입을 꺼도 효과가 "
            f"{_fmt(v.retap_effect_reentry_off)}로 남지만(선별 몫) 재진입이 채운 몫"
            f"({_fmt(v.reentry_fill)})도 노이즈선을 넘는다. **한 축만 떼어 채택하면 안 "
            "된다** — WAN-388 헤드라인은 두 채널의 합이다."
        )
    # 🚨 **넷이 다 있어야 낸다** — 하나라도 비면 「덜 매매해서 좋아 보이는 것」과의 구분이
    # 반쪽이라 아예 안 적는다(빈칸을 0으로 지어내지 않는다).
    counts = (v.trades_on_every, v.trades_on_once, v.trades_off_every, v.trades_off_once)
    if all(n is not None for n in counts):
        on_e, on_o, off_e, off_o = (int(n) for n in counts if n is not None)
        out.append(
            f"- 거래 수 병기(`{PRIMARY_OOS}`): 재진입 켬 {on_e:,} → {on_o:,} · "
            f"재진입 끔 {off_e:,} → {off_o:,}."
        )
    if v.reentry_trades_on_every is not None and v.reentry_trades_on_once is not None:
        base_n, once_n = v.reentry_trades_on_every, v.reentry_trades_on_once
        ratio = f"{once_n / base_n:.2f}배" if base_n else "—"
        out.append(
            f"- 이슈가 지목한 채널의 크기: 재진입 거래가 {base_n:,} → {once_n:,}({ratio})로 "
            "늘어난다 — **재탭이 비운 슬롯을 재진입이 실제로 채운다**(관측)."
        )
    out.append("")

    if loo:
        out.append("## 4. 종목 leave-one-out (지갑 재배치)")
        out.append("")
        out.append("| 팔 | 구간 | 최악 제외 | 최악 net R | 최선 제외 | 최선 net R | 기준 |")
        out.append("| -- | -- | -- | --: | -- | --: | --: |")
        for arm in present:
            for segment in LOO_SEGMENTS:
                sub = [r for r in loo if r.arm == arm.name and r.segment == segment]
                if not sub:
                    continue
                worst = min(sub, key=lambda r: r.mean_net_r)
                best = max(sub, key=lambda r: r.mean_net_r)
                ref = _row_of(rows, arm.name, segment)
                base = "—" if ref is None else f"{ref.mean_net_r:+.4f}"
                out.append(
                    f"| {arm.label} | {segment} | {worst.exclude} | {worst.mean_net_r:+.4f} | "
                    f"{best.exclude} | {best.mean_net_r:+.4f} | {base} |"
                )
        out.append("")
        out.append(
            "- 🚨 **라벨 필터가 아니라 지갑 재배치다**(WAN-316) — 종목을 빼면 자본 경합이 "
            "달라져 남은 칸의 거래 자체가 바뀐다."
        )
        out.append("")

    out.append("## 5. 검산")
    out.append("")
    if not checks:
        out.append("- (이번 실행에서는 검산을 돌리지 않았다.)")
    else:
        out.append("| 검산 | 팔 | 구간 | 지표 | 왼쪽 | 오른쪽 | 절대차 |")
        out.append("| -- | -- | -- | -- | --: | --: | --: |")
        for check in checks:
            out.append(
                f"| {check.check} | {check.arm} | {check.segment} | {check.metric} "
                f"| {check.left:.6g} | {check.right:.6g} | {check.abs_diff:.2e} |"
            )
        worst_diff = max(c.abs_diff for c in checks)
        out.append("")
        out.append(f"- 최대 절대차 **{worst_diff:.2e}** ({checksum_grade(worst_diff)}).")
        out.append(
            "- (a)는 **다른 모듈·다른 실행**이 같은 숫자를 냈다는 뜻이고 = 「팔을 더해도 "
            "기존 행이 안 움직였다」의 증거다. (b)는 재진입 축이, (c)는 재탭 축이 **라벨이 "
            "아니라 동작으로** 걸렸다는 증거다."
        )
        if not any(c.check.startswith("a ") for c in checks):
            out.append(
                "- 🚨 **(a)가 없다** — 이 실행의 좌표가 WAN-388 CSV(48칸 · 12종목)와 달라 "
                "대조를 **내지 않았다**(좌표가 다른 두 수를 빼면 배선 오류처럼 읽힌다). "
                "이 표는 그만큼만 읽는다."
            )
    out.append("")

    out.append("## 6. 경고")
    out.append("")
    out.append(
        "- ⚠️ 전부 `baseline`(닿으면 체결) 낙관 렌즈 위 값이고 체결 보수화(`pen_5bp`)는 범위 밖이다."
    )
    out.append(
        "- ⚠️ **총수익 %는 복리 착시라 판정 자가 아니다**(WAN-346) · 이 표의 총수익은 "
        "**복리를 끈** 판이라 채택 북 보고값과 비교 불가다."
    )
    out.append(
        "- 🚨 **「흑자」로 기대하지 말 것** — 재탭 차단을 다 인정해도 주 구간은 여전히 "
        "깊은 마이너스다. WAN-370: 비용을 0으로 만들어도 천장이 +0.09R이다."
    )
    out.append(
        "- ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386) 불변** — 이 표는 *같은 "
        "셋업을 몇 번에 나눠 잡나*를 묻지 *진입 규칙이 무작위와 구분되는가*를 묻지 않는다."
    )
    out.append(
        "- ⚠️ 판단은 북에서(WAN-341) · 핀 없이(WAN-305) · **이 채널은 per-cell로는 원리적으로 "
        "안 보인다**(칸이 한 지갑을 공유해야 생긴다)."
    )
    if elapsed is not None:
        out.append("")
        out.append(f"실측 소요: {elapsed / 3600:.2f}시간")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-389 재탭 차단 × 재진입 2×2 귀속 격자")
    parser.add_argument("--symbols", default=",".join(harness.DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(harness.DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument(
        "--retaps",
        default=",".join(RETAP_MODES),
        help=(
            "돌릴 재탭 모드(쉼표). 🚨 **팔이 아니라 재탭 모드가 컴퓨트 단위다** — 모드 "
            "하나가 재진입 켠 팔과 끈 팔을 둘 다 먹인다."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="기존 CSV에 이어 붙인다(모드를 나눠 돌릴 때). 같은 팔은 새 행이 이긴다.",
    )
    parser.add_argument("--loo", action="store_true", help="leave-one-out을 함께 돌린다")
    parser.add_argument("--no-checksum", action="store_true")
    parser.add_argument(
        "--cold-segments",
        action="store_true",
        help="차가운 `is`/`oos`까지 생성한다(컴퓨트 두 배 · 주 수치 oos_warm은 그대로)",
    )
    parser.add_argument("--from-csv", action="store_true", help="요약만 다시 만든다")
    parser.add_argument("--pilot", action="store_true", help="1종목 × 4h — 견적용")
    args = parser.parse_args(argv)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.from_csv:
        SUMMARY_PATH.write_text(
            build_summary_markdown(grid_from_csv(), loo_from_csv(), checksum_from_csv()),
            encoding="utf-8",
        )
        print(f"요약: {SUMMARY_PATH}")
        return 0

    symbols = [s for s in args.symbols.split(",") if s]
    timeframes = [t for t in args.timeframes.split(",") if t]
    if args.pilot:
        symbols, timeframes = symbols[:1], ["4h"]
    unknown = [m for m in args.retaps.split(",") if m and m not in RETAP_MODES]
    if unknown:
        parser.error(f"알 수 없는 재탭 모드: {unknown} (지원: {', '.join(RETAP_MODES)})")
    retaps = [m for m in args.retaps.split(",") if m]

    cold = args.cold_segments
    segments = ("full", "is", PRIMARY_OOS, "oos") if cold else DEFAULT_SEGMENTS
    start_ms, end_ms = parse_date_ms(args.start), parse_date_ms(args.end)

    started = time.monotonic()
    cfg = _cfg()
    rows: list[AttributionRow] = []
    loo: list[LooRow] = []
    for retap_mode in retaps:
        mode_started = time.monotonic()
        payloads = build_payloads(
            symbols,
            timeframes,
            retap_mode=retap_mode,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            cold_segments=cold,
        )
        print(
            f"[wan389] {retap_mode}: 후보 생성 {(time.monotonic() - mode_started) / 60:.1f}분",
            flush=True,
        )
        for arm in arms_for_retap(retap_mode):
            rows += build_arm_rows(
                payloads,
                arm=arm,
                start_ms=start_ms,
                end_ms=end_ms,
                num_symbols=len(symbols),
                segments=segments,
                cfg=cfg,
            )
            if args.loo:
                loo += build_leave_one_out(payloads, arm=arm, start_ms=start_ms, end_ms=end_ms)
            print(f"[wan389] {arm.name}: 배치 완료", flush=True)
        print(
            f"[wan389] {retap_mode}: 완료 {(time.monotonic() - mode_started) / 60:.1f}분",
            flush=True,
        )

    if args.append:
        names = {arm.name for m in retaps for arm in arms_for_retap(m)}
        rows = [r for r in grid_from_csv() if r.arm not in names] + rows
        loo = [r for r in loo_from_csv() if r.arm not in names] + loo

    checks: list[ChecksumRow] = []
    if not args.no_checksum:
        checks += check_against_wan388(rows)
        checks += check_reentry_axis(rows)
        checks += check_retap_axis(rows)
        checks += check_arm_invariants(rows)

    grid_to_frame(rows).to_csv(GRID_CSV_PATH, index=False)
    if loo:
        grid_to_frame(loo).to_csv(LOO_CSV_PATH, index=False)
    if checks:
        pd.DataFrame([c.model_dump() for c in checks]).to_csv(CHECKSUM_CSV_PATH, index=False)
    SUMMARY_PATH.write_text(
        build_summary_markdown(rows, loo, checks, elapsed=time.monotonic() - started),
        encoding="utf-8",
    )
    print(f"\n격자: {GRID_CSV_PATH}\n요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
