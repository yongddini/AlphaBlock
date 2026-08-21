"""WAN-336: 「진입한 그 1분 안에서 익절」의 크기 — 관측(§1) + 보수적 반사실(§2).

## 무엇이 비어 있었나

백테스트는 **1분봉**으로 체결을 판정하는데, 1분봉은 그 1분의 **시·고·저·종 네 숫자만**
알려 주고 **그 안의 순서는 모른다**. 롱 지정가 진입은 가격이 **내려와야** 체결되고 고정
1.5R 익절은 **올라가야** 닿으므로, 「같은 1분에 진입도 하고 익절도 했다」가 성립하려면
**저가가 먼저 · 고가가 나중**이어야 한다. 엔진은 그렇다고 **가정**한다.

🚨 **손절 쪽에는 이 가정을 누르는 보수성이 있는데 익절 쪽에는 없었다.** 같은 스텝에서
손절·익절이 함께 닿으면 `stop_before_tp`가 손절을 이기게 하고, 진입과 손절이 같은 1분인
건수는 WAN-46 감사(`ZoneLimitStats.penetrations`)가 센다. **같은 조건의 익절은 세지도
않았다** — 이 저장소는 그 낙관을 관측조차 한 적이 없다.

🚨 **체결 보수화(`pen_5bp`)로는 이 축이 안 잡힌다.** 그쪽은 *「주문이 채워지느냐」*(큐
우선순위)를 묻고 이건 *「채워진 뒤 그 1분 안의 순서」*를 묻는다 — 다른 질문이라 이
저장소의 **모든 체결 보수화 관문이 이 낙관을 통과시켜 왔다**.

## 축 — 팔 2 × 스코프 1 × 구간 4

* **`base`**: 인자 없는 채택 북 그대로(현행). §1 관측의 원자료다.
* **`no_same_step_tp`**: 진입 스텝에서 익절을 판정하지 않는 **반대쪽 극단**(WAN-336 §2).

⚠️ **두 팔 다 진값이 아니다.** 순서가 실제로 반대였다면 그 거래는 「손실」이 아니라 **더
오래 보유**이고 결과는 미지다 — 반사실 팔은 그 미지를 「그 스텝엔 익절 없음」으로 눌러 본
것뿐이다. **두 극단의 폭**이 이 리포트의 산출물이고, 좁히는 것은 틱·호가(WAN-98,
Canceled) 소관이다.

## 좌표 (WAN-305 — 핀 하나도 없다)

12종목(`harness.DEFAULT_SYMBOLS`) · 4TF 한 지갑 · 못 박은 6년 창 · 재진입 ON(band) ·
cap_only 5배 · 존폭 필터 1.28 · 오프셋 2bp · 손절폭 가드 0.3% · 유동성 한도 채택값.
구간은 `oos_warm`(주, WAN-166) + `oos`(스트레스) + `full`·`is` 병기.

## 판정 열 — 총수익 %가 아니다

§1의 헤드라인은 **「같은 분 익절이 채택 북 순손익의 몇 %를 만드는가」**이고, §2는
**MDD · 수익/MDD · 승률 · 거래 수**로 읽는다. `total_return` %는 수천 거래 복리 착시라
실현 수익이 아니다(WAN-169/213).

## 귀속과 leave-one-out

북은 한 지갑이라 `Trade`에 심볼·TF가 없다 — `book_cli.BookSegment.trades_with_cells()`가
시퀀서의 배치 기록과 짝지어 칸을 되붙인다(길이·손익 이중 대조로 계약을 지킨다).

📌 **leave-one-out은 라벨 필터가 아니라 지갑 재배치다** — 종목을 뺀 **칸 집합**으로 북을
다시 돌린다(WAN-316 `both_no15m`과 같은 스코프 패턴). 후보 생성이 비용의 전부이고 배치는
싸므로(`book_cli` 설계) 12종목 LOO가 사실상 공짜다. 라벨만 걸러 내면 「그 종목이 안 썼을
자본을 다른 칸이 쓴다」는 북의 본질이 빠져 per-cell 표가 된다.

## 검산

* **(a) `base` ≡ 인자 없는 채택 북** — 같은 payload를 `book_cli.run_book`의 마지막 두 단계
  (`apply_funding_proxy` → `build_book_rows`)에 그대로 넣어 낸 행과 대조한다.
* **(b) 두 층이 같은 것을 센다** — 후보 층 카운터(`ZoneLimitStats.same_step_take_profits`,
  격리)와 북 거래 층 귀속이 같은 술어(`is_same_step_take_profit`)를 쓴다. 북 쪽이 더 작은
  것이 정상이다(시퀀싱이 후보를 떨어뜨린다) — 리포트가 두 수를 나란히 싣는다.

재현:

```
uv run python -m backtest.wan336_same_step_tp --jobs 4
uv run python -m backtest.wan336_same_step_tp --from-csv      # 요약만
```
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.book_cli import (
    ADOPTED_REENTRY_ENTRY_RULE,
    BookSegment,
    build_book_rows,
    iter_book_segments,
)
from backtest.leverage_book import LeverageBookParams, PlacedSetup
from backtest.models import ExitReason, Trade
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, _segment_cells, run_cells
from backtest.wan180_leverage_book_nine import apply_funding_proxy
from backtest.wan323_partial_tp_ladder import PRIMARY_OOS, SEGMENT_ORDER
from backtest.zone_limit_backtest import is_same_step_take_profit

REPORTS_DIR = Path("backtest/reports")
CSV_PATH = REPORTS_DIR / "wan336_same_step_tp.csv"
LOO_CSV_PATH = REPORTS_DIR / "wan336_same_step_tp_loo.csv"
SUMMARY_PATH = REPORTS_DIR / "wan336_same_step_tp_summary.md"

#: 팔 이름 — `base`가 현행 채택 북(§1 관측의 원자료), 반사실이 §2다.
BASE_ARM = "base"
COUNTERFACTUAL_ARM = "no_same_step_tp"
ARM_ORDER: tuple[str, ...] = (BASE_ARM, COUNTERFACTUAL_ARM)

#: `run_cells`에 넘기는 **채택 좌표 인자** — `book_cli.run_book`이 쓰는 것과 같아야 한다.
#: 회귀 테스트가 두 호출의 인자를 실제로 캡처해 대조하므로, 여기를 바꾸면 테스트가 깨진다.
ADOPTED_CELL_KWARGS: dict[str, object] = {
    "adv_fraction": harness.UNSET,
    "reentry": True,
    "reentry_entry_rule": ADOPTED_REENTRY_ENTRY_RULE,
}


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class SameStepRow(BaseModel):
    """한 (팔, 구간)의 북 집계 + 「같은 분 청산」 귀속 — 북은 한 지갑이라 심볼 열이 없다."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    return_over_mdd: float | None
    peak_concurrency: int
    max_concurrent_risk: float
    liquidation_events: int

    same_step_tp_trades: int
    """진입과 익절이 **같은 1분**인 거래 수(북에 실제로 배치된 것)."""
    same_step_tp_trade_share: float
    """그 건수 / 전체 거래 수."""
    same_step_tp_pnl: float
    """그 거래들의 순손익 합(USD)."""
    net_pnl: float
    """전체 거래의 순손익 합(USD). ⚠️ 복리 자본곡선의 총수익과 다른 자다 — 이 열은 거래
    단위 귀속을 위한 **단순 합**이고, 「몇 %를 만드는가」의 분모다."""
    same_step_tp_pnl_share: float | None
    """§1 헤드라인(USD 자) — `same_step_tp_pnl / net_pnl`. 분모가 0 언저리·음수면 None.

    ⚠️ **복리 지갑의 USD 합이라 뒤쪽 거래가 표를 지배한다**(WAN-169/213) — 아래 net R 자와
    나란히 읽는다. 둘이 크게 갈리면 그 자체가 **시점 편중의 신호**다."""
    same_step_tp_net_r: float
    """같은 분 익절 거래의 실현 net R 합(크기 정규화, WAN-154 `mean_net_r`와 같은 자)."""
    net_r: float
    """전체 거래의 실현 net R 합."""
    same_step_tp_net_r_share: float | None
    """§1 헤드라인(크기 정규화 자) — `same_step_tp_net_r / net_r`. 분모가 0 언저리·음수면 None."""
    same_step_stop_trades: int
    """그 거울 — 진입과 **손절**이 같은 1분인 거래 수(WAN-46 `penetrations`의 북 층 판)."""
    same_step_stop_pnl: float
    candidate_same_step_tps: int
    """후보 층(격리) 카운터 합 — 시퀀싱 전 원(raw) 수. 북 층보다 크면 정상이다."""


class SameStepLooRow(BaseModel):
    """종목 하나를 뺀 **지갑 재배치** 결과 (WAN-336 §1-3)."""

    model_config = ConfigDict(frozen=True)

    arm: str
    segment: str
    excluded: str
    """빼낸 종목(`"-"`이면 전 종목 = 기준 행)."""
    num_trades: int
    max_drawdown: float
    same_step_tp_trades: int
    same_step_tp_pnl_share: float | None
    same_step_tp_net_r_share: float | None
    net_pnl: float


CSV_KEYS: tuple[str, ...] = ("arm", "segment")
LOO_CSV_KEYS: tuple[str, ...] = ("arm", "segment", "excluded")


# --------------------------------------------------------------------------- #
# 귀속 — 북 거래를 「같은 분 청산」으로 가른다
# --------------------------------------------------------------------------- #


def _final_reason(trade: Trade) -> ExitReason:
    return trade.exits[-1].reason


def _net_r(trade: Trade, placement: PlacedSetup) -> float:
    """거래당 실현 net R = 실현손익 ÷ **그 거래의** 리스크 금액(WAN-154 `mean_net_r`와 같은 자).

    리스크가 0이면(사이징이 그런 거래를 내지 않지만 방어적으로) 0으로 본다.
    """
    return trade.realized_pnl / placement.risk_amount if placement.risk_amount > 0 else 0.0


def classify_trades(pairs: Sequence[tuple[Trade, PlacedSetup]]) -> dict[str, float]:
    """거래·배치 짝에서 「같은 분 익절/손절」 건수·USD 손익·net R을 센다.

    술어는 `zone_limit_backtest.is_same_step_take_profit` **하나**를 쓴다 — 후보 층
    카운터와 같은 정의여야 두 수가 같은 것을 센다(WAN-336 검산 (b)).

    📌 **두 자를 함께 낸다.** USD 합은 그 지갑이 실제로 겪은 몫이지만 **복리라 뒤쪽 거래가
    표를 지배**한다(WAN-169/213). net R 합은 크기를 정규화해 「6년 중 어느 시점이든 한 거래는
    한 거래」로 세므로, 두 비중이 크게 갈리면 그 자체가 **시점 편중의 신호**다. 어느 한쪽만
    싣고 「몇 %」를 말하면 읽는 사람이 다른 쪽을 상상하게 된다.
    """
    tp_trades = tp_pnl = tp_net_r = 0.0
    stop_trades = stop_pnl = 0.0
    net_pnl = net_r = 0.0
    for trade, placement in pairs:
        reason = _final_reason(trade)
        r = _net_r(trade, placement)
        net_pnl += trade.realized_pnl
        net_r += r
        if is_same_step_take_profit(trade.entry_time, trade.exit_time, reason):
            tp_trades += 1
            tp_pnl += trade.realized_pnl
            tp_net_r += r
        elif reason is ExitReason.STOP_LOSS and trade.exit_time == trade.entry_time:
            stop_trades += 1
            stop_pnl += trade.realized_pnl
    return {
        "tp_trades": tp_trades,
        "tp_pnl": tp_pnl,
        "tp_net_r": tp_net_r,
        "stop_trades": stop_trades,
        "stop_pnl": stop_pnl,
        "net_pnl": net_pnl,
        "net_r": net_r,
    }


#: 「몇 %를 만드는가」를 낼 수 있는 최소 분모(USD). 순손익 합이 이보다 작으면(또는 음수면)
#: 비율이 뜻을 잃으므로 내지 않는다 — WAN-115가 문서화한 「기준이 0 언저리면 잔존율은 함정」
#: 과 같은 가드다.
_PNL_FLOOR = 1.0

#: net R 자의 같은 가드 — R은 USD보다 훨씬 작은 눈금이라 문턱도 작다(1R = 거래 하나의 리스크).
_R_FLOOR = 0.5


def pnl_share(part: float, whole: float, *, floor: float = _PNL_FLOOR) -> float | None:
    """부분/전체. 전체가 0 언저리이거나 음수면 **비율을 내지 않는다**(부호만 읽는다)."""
    return part / whole if whole > floor else None


def _pairs(segment: BookSegment) -> list[tuple[Trade, PlacedSetup]]:
    return segment.trades_with_placements()


def _candidate_same_step_tps(payloads: Sequence[CellPayload], segment: str) -> int:
    """후보 층(시퀀싱 전) 카운터 합 — 검산 (b)의 한쪽.

    북이 실제로 받는 **그 후보 집합**에서 센다(`_segment_cells` 재사용) — 그래야 두 수가
    같은 모집단의 부분·전체가 된다. `oos_warm`의 경계 필터와 재진입 후보 합류가 여기에
    그대로 걸리므로 따로 규칙을 복제하지 않는다(복제하면 갈라진다).
    """
    return sum(
        1
        for cell in _segment_cells(payloads, segment, "", include_reentry=True)
        for cand in cell.candidates
        if cand.same_step_take_profit
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def book_segments_for_payloads(
    payloads: Sequence[CellPayload],
    *,
    start_ms: int,
    end_ms: int,
    segments: Sequence[str] = SEGMENT_ORDER,
) -> list[BookSegment]:
    """채택 북 배치 — `book_cli.run_book`의 마지막 두 단계와 **같은 함수·같은 인자**다.

    `apply_funding_proxy`를 여기서 거치는 것이 요점이다(WAN-305: 기본이 채택 규칙). 12종목이
    전부 자기 확정 펀딩을 갖는 오늘 좌표에서는 **무동작**이고, 검산 (a)가 그 사실을 숫자로 남긴다.
    """
    proxied, _note = apply_funding_proxy(payloads)
    return iter_book_segments(
        proxied,
        book=LeverageBookParams(),
        segments=segments,
        start_ms=start_ms,
        end_ms=end_ms,
        include_reentry=True,
    )


def verify_adopted_identity(
    payloads: Sequence[CellPayload], *, start_ms: int, end_ms: int
) -> float:
    """검산 (a) — 펀딩 대리가 이 좌표에서 **무동작**인가(= 원 payload 행 ≡ 채택 경로 행).

    돌려주는 값은 최대 절대차. `0.0`이면 12종목이 전부 자기 펀딩을 갖는다는 뜻이다.

    ⚠️ 이 함수가 못 잡는 고리 하나 — `run_cells`에 넘긴 인자가 채택 경로와 같은가. 그건
    회귀 테스트가 실제 호출 인자를 캡처해 동작으로 고정한다(모듈 상수 대조가 아니다).
    """
    raw = {
        r.segment: r
        for r in build_book_rows(
            payloads,
            book=LeverageBookParams(),
            segments=SEGMENT_ORDER,
            start_ms=start_ms,
            end_ms=end_ms,
            include_reentry=True,
        )
    }
    worst = 0.0
    for seg in book_segments_for_payloads(payloads, start_ms=start_ms, end_ms=end_ms):
        other = raw[seg.segment]
        worst = max(
            worst,
            abs(seg.row.total_return - other.total_return),
            abs(seg.row.max_drawdown - other.max_drawdown),
            float(abs(seg.row.num_trades - other.num_trades)),
        )
    return worst


def _to_row(*, arm: str, segment: BookSegment, payloads: Sequence[CellPayload]) -> SameStepRow:
    row = segment.row
    counts = classify_trades(_pairs(segment))
    return SameStepRow(
        arm=arm,
        segment=segment.segment,
        num_cells=row.num_cells,
        num_trades=row.num_trades,
        win_rate=row.win_rate,
        total_return=row.total_return,
        max_drawdown=row.max_drawdown,
        return_over_mdd=row.return_over_mdd,
        peak_concurrency=row.peak_concurrency,
        max_concurrent_risk=row.max_concurrent_risk,
        liquidation_events=row.liquidation_events,
        same_step_tp_trades=int(counts["tp_trades"]),
        same_step_tp_trade_share=(counts["tp_trades"] / row.num_trades if row.num_trades else 0.0),
        same_step_tp_pnl=counts["tp_pnl"],
        net_pnl=counts["net_pnl"],
        same_step_tp_pnl_share=pnl_share(counts["tp_pnl"], counts["net_pnl"]),
        same_step_tp_net_r=counts["tp_net_r"],
        net_r=counts["net_r"],
        same_step_tp_net_r_share=pnl_share(counts["tp_net_r"], counts["net_r"], floor=_R_FLOOR),
        same_step_stop_trades=int(counts["stop_trades"]),
        same_step_stop_pnl=counts["stop_pnl"],
        candidate_same_step_tps=_candidate_same_step_tps(payloads, segment.segment),
    )


def _loo_rows(
    *,
    arm: str,
    payloads: Sequence[CellPayload],
    symbols: Sequence[str],
    start_ms: int,
    end_ms: int,
) -> list[SameStepLooRow]:
    """종목을 하나씩 뺀 **지갑 재배치** — 배치는 싸므로 12종목 LOO가 사실상 공짜다."""
    # 🚨 조용한 실패 방지 — **북을 돌리기 전에** 표기를 맞춰 본다. 심볼 표기가 어긋나면
    # 아무것도 안 빠져 **모든 LOO 행이 기준 행과 같아지고**, 그러면 「한 종목이 만드는 결과가
    # 아니다」라는 결론이 근거 없이 만들어진다(라벨은 멀쩡한 채 표만 거짓이 되는 부류).
    present = {p.symbol for p in payloads}
    unmatched = [s for s in symbols if s not in present]
    if present and unmatched:
        raise AssertionError(
            f"leave-one-out이 아무 칸도 빼지 못했습니다: {unmatched} — 심볼 표기가 "
            f"payload({sorted(present)[0]!r} 형식)와 어긋납니다."
        )

    rows: list[SameStepLooRow] = []
    for excluded in ("-", *symbols):
        scoped = [p for p in payloads if p.symbol != excluded]
        if not scoped:
            continue
        for seg in book_segments_for_payloads(
            scoped, start_ms=start_ms, end_ms=end_ms, segments=(PRIMARY_OOS,)
        ):
            counts = classify_trades(_pairs(seg))
            rows.append(
                SameStepLooRow(
                    arm=arm,
                    segment=seg.segment,
                    excluded=excluded,
                    num_trades=seg.row.num_trades,
                    max_drawdown=seg.row.max_drawdown,
                    same_step_tp_trades=int(counts["tp_trades"]),
                    same_step_tp_pnl_share=pnl_share(counts["tp_pnl"], counts["net_pnl"]),
                    same_step_tp_net_r_share=pnl_share(
                        counts["tp_net_r"], counts["net_r"], floor=_R_FLOOR
                    ),
                    net_pnl=counts["net_pnl"],
                )
            )
    return rows


def tf_attribution(segment: BookSegment) -> pd.DataFrame:
    """TF별 귀속 — 15m은 1분봉 대비 봉이 15개뿐이라 상대 빈도가 다를 수 있다(§1-2)."""
    records: list[dict[str, object]] = []
    by_tf: dict[str, list[tuple[Trade, PlacedSetup]]] = {}
    for trade, placement in _pairs(segment):
        by_tf.setdefault(placement.cell[1], []).append((trade, placement))
    for timeframe, pairs in sorted(by_tf.items()):
        counts = classify_trades(pairs)
        records.append(
            {
                "timeframe": timeframe,
                "num_trades": len(pairs),
                "same_step_tp_trades": int(counts["tp_trades"]),
                "same_step_tp_trade_share": counts["tp_trades"] / len(pairs),
                "same_step_tp_pnl": counts["tp_pnl"],
                "net_pnl": counts["net_pnl"],
                "same_step_tp_pnl_share": pnl_share(counts["tp_pnl"], counts["net_pnl"]),
                "net_r": counts["net_r"],
                "same_step_tp_net_r_share": pnl_share(
                    counts["tp_net_r"], counts["net_r"], floor=_R_FLOOR
                ),
            }
        )
    return pd.DataFrame(records)


def run_arm(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    arm: str,
    *,
    start: str,
    end: str,
    jobs: int,
    segments: Sequence[str] = SEGMENT_ORDER,
    log: bool = True,
) -> tuple[list[SameStepRow], list[SameStepLooRow], pd.DataFrame, float | None]:
    """한 팔의 후보를 한 번 만들고 지갑 · TF 귀속 · 종목 LOO를 낸다."""
    start_ms, end_ms = parse_date_ms(start), parse_date_ms(end)
    is_base = arm == BASE_ARM
    payloads = run_cells(
        symbols,
        timeframes,
        start=start,
        end=end,
        jobs=jobs,
        # ⚠️ 반사실 팔은 `engine_check`를 끈다 — 그 검산은 격리 성과가 `harness.run_once`
        # (반사실이 없는 per-cell)와 비트 일치하는지 보는 것이라 팔을 켠 쪽에서는 **당연히**
        # 어긋난다. 기준선 팔에서만 켜서 채택 경로 배선을 지킨다.
        engine_check=is_base,
        no_same_step_tp=not is_base,
        **ADOPTED_CELL_KWARGS,  # type: ignore[arg-type]
    )
    identity: float | None = None
    if is_base:
        identity = verify_adopted_identity(payloads, start_ms=start_ms, end_ms=end_ms)
        if log:
            print(f"[wan336] 검산(a) 펀딩 대리 무동작 최대차: {identity:.2e}", flush=True)

    book = book_segments_for_payloads(payloads, start_ms=start_ms, end_ms=end_ms, segments=segments)
    rows = [_to_row(arm=arm, segment=seg, payloads=payloads) for seg in book]
    primary = next((s for s in book if s.segment == PRIMARY_OOS), None)
    tf_frame = tf_attribution(primary) if primary is not None else pd.DataFrame()
    if not tf_frame.empty:
        tf_frame.insert(0, "arm", arm)
        tf_frame.insert(1, "segment", PRIMARY_OOS)
    loo = _loo_rows(
        arm=arm,
        payloads=payloads,
        symbols=[harness.normalize_symbol(s) for s in symbols],
        start_ms=start_ms,
        end_ms=end_ms,
    )
    return rows, loo, tf_frame, identity


def run_report(
    symbols: Sequence[str] = harness.DEFAULT_SYMBOLS,
    timeframes: Sequence[str] = harness.DEFAULT_TIMEFRAMES,
    *,
    arms: Sequence[str] = ARM_ORDER,
    start: str = harness.DEFAULT_START,
    end: str = harness.DEFAULT_END,
    jobs: int = 1,
    segments: Sequence[str] = SEGMENT_ORDER,
    on_arm: Callable[[list[SameStepRow], list[SameStepLooRow], pd.DataFrame], None] | None = None,
    log: bool = True,
) -> tuple[list[SameStepRow], list[SameStepLooRow], pd.DataFrame]:
    """팔마다 4TF 지갑을 한 실행으로 돈다.

    📌 팔마다 즉시 적재한다(`on_arm`) — 한 팔이 12종목 × 4TF라 한 시간 안팎이고, 팔은 각자
    독립 지갑이라 중간에 끊겨도 끝난 팔은 보존된다. **끊길 수 없는 것은 한 팔 안의 4TF뿐이다**
    (북은 이어붙일 수 없다 — WAN-316).
    """
    rows: list[SameStepRow] = []
    loo: list[SameStepLooRow] = []
    frames: list[pd.DataFrame] = []
    for arm in arms:
        t0 = time.time()
        arm_rows, arm_loo, arm_tf, _identity = run_arm(
            symbols,
            timeframes,
            arm,
            start=start,
            end=end,
            jobs=jobs,
            segments=segments,
            log=log,
        )
        rows.extend(arm_rows)
        loo.extend(arm_loo)
        if not arm_tf.empty:
            frames.append(arm_tf)
        if on_arm is not None:
            on_arm(arm_rows, arm_loo, arm_tf)
        if log:
            print(
                f"[wan336] {arm}: {len(arm_rows)}행 ({time.time() - t0:.0f}s)",
                flush=True,
            )
    tf_frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return rows, loo, tf_frame


# --------------------------------------------------------------------------- #
# 요약
# --------------------------------------------------------------------------- #


def rows_to_frame(rows: Sequence[SameStepRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def loo_to_frame(rows: Sequence[SameStepLooRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


def _missing(value: float | None) -> bool:
    """`None`과 **NaN을 함께** 결측으로 본다.

    CSV 왕복이 `None`을 NaN으로 바꾸므로 `is None`만 보면 표에 `nan%`가 찍힌다 — 비율을
    일부러 내지 않은 셀(분모가 0 언저리)이 「측정값 nan」으로 보이면 정반대로 읽힌다.
    """
    return value is None or pd.isna(value)


def _pct(value: float | None) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:.2f}%"  # type: ignore[arg-type]


def _pp(value: float | None) -> str:
    return "—" if _missing(value) else f"{float(value) * 100:+.2f}%p"  # type: ignore[arg-type]


def _num(value: float | None) -> str:
    return "—" if _missing(value) else f"{float(value):.2f}"  # type: ignore[arg-type]


def _pick(frame: pd.DataFrame, arm: str, segment: str) -> pd.Series | None:
    hit = frame[(frame["arm"] == arm) & (frame["segment"] == segment)]
    return None if hit.empty else hit.iloc[0]


def _verdict_sentence(frame: pd.DataFrame, tf_frame: pd.DataFrame, loo: pd.DataFrame) -> str:
    """완료기준 2 — 한 문장 판정(TF별·종목 편중 확인까지)."""
    base = _pick(frame, BASE_ARM, PRIMARY_OOS)
    if base is None:
        return "⚠️ 기준선 팔의 주 구간 행이 없어 판정을 낼 수 없다."
    share = base["same_step_tp_pnl_share"]
    if pd.isna(share):
        return (
            f"⚠️ **판정 불가(비율 무의미)** — `{PRIMARY_OOS}` 순손익 합이 "
            f"{base['net_pnl']:,.0f} USD로 0 언저리이거나 음수라 「몇 %」가 뜻을 잃는다. "
            f"건수만 읽는다: 같은 분 익절 {int(base['same_step_tp_trades'])}건 / "
            f"{int(base['num_trades'])}건."
        )
    tf_bits: list[str] = []
    if not tf_frame.empty:
        rows = tf_frame[(tf_frame["arm"] == BASE_ARM) & (tf_frame["segment"] == PRIMARY_OOS)]
        for _, row in rows.iterrows():
            tf_bits.append(
                f"{row['timeframe']} {_pct(row['same_step_tp_pnl_share'])}"
                f"/{_pct(row['same_step_tp_net_r_share'])}"
            )
    loo_bit = ""
    if not loo.empty:
        cut = loo[(loo["arm"] == BASE_ARM) & (loo["excluded"] != "-")].dropna(
            subset=["same_step_tp_net_r_share"]
        )
        if not cut.empty:
            worst = cut.loc[cut["same_step_tp_net_r_share"].idxmax()]
            mildest = cut.loc[cut["same_step_tp_net_r_share"].idxmin()]
            loo_bit = (
                f" 종목을 하나씩 빼고 **지갑을 다시 배치해도** net R 비중은 "
                f"{_pct(mildest['same_step_tp_net_r_share'])}"
                f"(−{mildest['excluded']})~{_pct(worst['same_step_tp_net_r_share'])}"
                f"(−{worst['excluded']}) 사이라"
                + (
                    " 한 종목이 만드는 결과가 아니다."
                    if float(mildest["same_step_tp_net_r_share"]) > 0.0
                    else " 종목에 갈린다."
                )
            )
    return (
        f"📌 **채택 북 `{PRIMARY_OOS}`에서 「진입과 익절이 같은 1분」인 거래는 "
        f"{int(base['same_step_tp_trades'])}건({_pct(base['same_step_tp_trade_share'])})인데 "
        f"거래 순손익의 {_pct(share)}(USD 자) · "
        f"{_pct(base['same_step_tp_net_r_share'])}(크기 정규화 net R 자)를 만든다**"
        + (f" (TF별 USD/R: {' · '.join(tf_bits)})" if tf_bits else "")
        + "."
        + loo_bit
    )


def _asymmetry_note(frame: pd.DataFrame) -> str:
    """같은 분 **익절**과 같은 분 **손절**의 비대칭 — 이 표에서 가장 말이 많은 숫자다.

    익절은 1R의 `take_profit_r`배(1.5) 거리, 손절은 1배 거리다. 순진하게 보면 **같은 분
    손절이 더 흔해야** 하는데 실측은 정반대다. 그 배수를 숫자로 남긴다.
    """
    row = _pick(frame, BASE_ARM, PRIMARY_OOS)
    if row is None:
        return ""
    tps, stops = int(row["same_step_tp_trades"]), int(row["same_step_stop_trades"])
    ratio = f"**{tps / stops:.0f}배**" if stops else "**(같은 분 손절이 0건이라 배수 없음)**"
    return (
        f"🚨 **비대칭이 이 표에서 가장 말이 많은 숫자다 — 같은 분 익절 {tps}건 대 같은 분 "
        f"손절 {stops}건({ratio}).** 익절은 1R의 1.5배 거리이고 손절은 1배 거리라 **순진하게는 "
        "같은 분 손절이 더 흔해야 한다.** 정반대인 데는 기계적 이유가 보인다 — 봉내 라이브 "
        "밴드(WAN-132)가 봉 **안에서** 가격을 따라 내려가며 지정가를 재산정하므로 체결가가 그 "
        "봉의 **저가 근처**에 놓인다. 그러면 `고가 − 진입`은 봉 범위에 가깝고 `진입 − 저가`는 "
        "거의 0이라, 같은 봉 익절은 쉽고 같은 봉 손절은 사실상 불가능해진다(존폭 필터 1.28이 "
        "좁은 존만 남겨 1R을 작게 만드는 것이 이를 더 키운다). ⚠️ **이것은 이 표가 증명한 게 "
        "아니라 이 표와 정합적인 설명**이다 — 가르려면 체결가가 봉 범위 어디에 놓이는지를 직접 "
        "재야 하고, 그건 별도 이슈다(WAN-328 `path_fill_price`가 인접한 자)."
    )


def _arm_did_something(frame: pd.DataFrame) -> str:
    """검산 (d) — 반사실 팔의 후보 층 카운터는 **정의상 0이어야 한다**.

    팔이 「진입 스텝에서는 익절을 판정하지 않는다」이므로 「진입과 익절이 같은 1분」인 후보가
    하나라도 남아 있으면 팔이 그 자리에서 동작하지 않은 것이다. 라벨만 붙고 기본 엔진이
    도는 것이 이 저장소가 반복해 겪은 실패라(WAN-91/95/112/123/159), 그 부재를 **숫자로**
    확인한다.
    """
    arm = frame[frame["arm"] == COUNTERFACTUAL_ARM]
    if arm.empty:
        return "⚠️ 검산 (d): 반사실 팔 행이 없어 확인하지 못했다."
    leftover = int(arm["candidate_same_step_tps"].sum())
    base = frame[frame["arm"] == BASE_ARM]
    removed = int(base["candidate_same_step_tps"].sum()) if not base.empty else 0
    if leftover:
        return (
            f"🚨 **검산 (d) 실패**: 반사실 팔에 「같은 분 익절」 후보가 {leftover}건 남아 있다 "
            "— 팔이 라벨만 붙고 동작하지 않았을 수 있다."
        )
    return (
        f"📌 **검산 (d) 통과**: 반사실 팔의 후보 층 카운터가 전 구간 **0**이다(기준선 팔은 "
        f"{removed}건). 팔이 라벨이 아니라 실제로 그 자리에서 익절을 미뤘다는 직접 증거다."
    )


def build_summary(frame: pd.DataFrame, loo: pd.DataFrame, tf_frame: pd.DataFrame) -> str:
    """md 요약 — §1 관측 · §2 반사실 · 경고를 한 문서로."""
    lines: list[str] = [
        "# WAN-336: 진입한 그 1분 안의 익절 — 관측(§1) + 보수적 반사실(§2)",
        "",
        "1분봉은 그 1분의 **시·고·저·종 네 숫자만** 알려 주고 **그 안의 순서는 모른다**. "
        "롱 지정가 진입은 가격이 **내려와야** 체결되고 고정 1.5R 익절은 **올라가야** 닿으니, "
        "「같은 1분에 진입 + 익절」이 성립하려면 **저가가 먼저 · 고가가 나중**이어야 한다 — "
        "엔진은 그렇다고 **가정**한다. 손절 쪽에는 `stop_before_tp`(동시 도달 시 손절 우선)와 "
        "WAN-46 관통 카운터가 있는데 **익절 쪽에는 아무 장치도 없었다**.",
        "",
        "좌표: 12종목 × 4TF 한 지갑 · 못 박은 6년 창 · 재진입 ON(band) · cap_only 5배 · "
        "**핀 하나도 없음**(WAN-305). 주 구간은 `oos_warm`(WAN-166).",
        "",
        "## 판정",
        "",
        _verdict_sentence(frame, tf_frame, loo),
        "",
        "## §1 관측 — 같은 분 청산의 크기 (팔 `base` = 인자 없는 채택 북)",
        "",
        "| 구간 | 거래 | 같은 분 익절 | 건수 비중 | **순손익 비중(USD)** "
        "| **순손익 비중(net R)** | 같은 분 손절 | 후보 층(시퀀싱 전) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for segment in SEGMENT_ORDER:
        row = _pick(frame, BASE_ARM, segment)
        if row is None:
            continue
        lines.append(
            f"| `{segment}` | {int(row['num_trades'])} | {int(row['same_step_tp_trades'])} "
            f"| {_pct(row['same_step_tp_trade_share'])} "
            f"| **{_pct(row['same_step_tp_pnl_share'])}** "
            f"| **{_pct(row['same_step_tp_net_r_share'])}** "
            f"| {int(row['same_step_stop_trades'])} "
            f"| {int(row['candidate_same_step_tps'])} |"
        )
    lines += [
        "",
        "⚠️ **「그만큼이 부풀려진 수익」이라는 뜻이 아니다** — 순서가 반대였다면 그 거래는 "
        "손실이 아니라 **더 오래 보유**이고 결과는 미지다. 이 표는 **노출된 표본의 크기**이지 "
        "손익 보정이 아니다.",
        "",
        "📌 **자를 둘 병기한다.** `USD`는 거래 단위 실현손익의 단순 합이라 그 지갑이 실제로 "
        "겪은 몫이지만 **복리라 뒤쪽 거래가 표를 지배한다**(WAN-169/213). `net R`은 거래마다 "
        "실현손익 ÷ 그 거래의 리스크 금액이라(WAN-154 `mean_net_r`와 같은 자) 6년 중 어느 "
        "시점이든 한 거래를 한 거래로 센다. **두 비중이 크게 갈리면 그 자체가 시점 편중의 "
        "신호**이므로 어느 한쪽만 인용하지 말 것.",
        "",
        "📌 **후보 층 수가 북 층보다 큰 것이 정상이다** — 칸당 1포지션·명목 상한이 후보를 "
        "떨어뜨린다(검산 (b): 두 층이 같은 술어 `is_same_step_take_profit`를 쓴다).",
        "",
        _asymmetry_note(frame),
        "",
        _arm_did_something(frame),
        "",
        f"### TF별 귀속 (`{PRIMARY_OOS}` · 팔 `base`)",
        "",
        "| TF | 거래 | 같은 분 익절 | 건수 비중 | 순손익 비중(USD) | 순손익 비중(net R) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not tf_frame.empty:
        rows = tf_frame[(tf_frame["arm"] == BASE_ARM) & (tf_frame["segment"] == PRIMARY_OOS)]
        for _, row in rows.iterrows():
            lines.append(
                f"| {row['timeframe']} | {int(row['num_trades'])} "
                f"| {int(row['same_step_tp_trades'])} "
                f"| {_pct(row['same_step_tp_trade_share'])} "
                f"| {_pct(row['same_step_tp_pnl_share'])} "
                f"| {_pct(row['same_step_tp_net_r_share'])} |"
            )
    lines += [
        "",
        "### 종목 leave-one-out — 라벨 필터가 아니라 **지갑 재배치**",
        "",
        "그 종목의 칸을 빼고 북을 **다시 돌린 값**이다(WAN-316 스코프 패턴). 라벨만 걸러 내면 "
        "「그 종목이 안 썼을 자본을 다른 칸이 쓴다」는 북의 본질이 빠져 per-cell 표가 된다.",
        "",
        "| 제외 | 거래 | MDD | 같은 분 익절 | 순손익 비중(USD) | 순손익 비중(net R) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not loo.empty:
        rows = loo[(loo["arm"] == BASE_ARM) & (loo["segment"] == PRIMARY_OOS)]
        for _, row in rows.iterrows():
            label = "전 종목" if row["excluded"] == "-" else f"−{row['excluded']}"
            lines.append(
                f"| {label} | {int(row['num_trades'])} | {_pct(row['max_drawdown'])} "
                f"| {int(row['same_step_tp_trades'])} "
                f"| {_pct(row['same_step_tp_pnl_share'])} "
                f"| {_pct(row['same_step_tp_net_r_share'])} |"
            )
    lines += [
        "",
        "## §2 반사실 — 「진입 스텝에서는 익절을 판정하지 않는다」 (팔 `no_same_step_tp`)",
        "",
        "⚠️ **이것도 진값이 아니라 반대쪽 극단이다.** 진값은 두 극단 사이에 있고 **그 폭**이 "
        "이 리포트의 산출물이다 — 좁히는 것은 틱·호가(WAN-98, Canceled) 소관이다.",
        "",
        "🚨 **수익/MDD는 배율(×)로만 싣는다** — 분자가 6년 복리 총수익이라 절댓값이 천문학적이고 "
        "그 배율도 대부분 총수익이 만든다(WAN-169/213). **판정은 MDD·승률·거래 수로 낸다.**",
        "",
        "| 구간 | 거래 (Δ) | 승률 (Δ) | **MDD (Δ)** | 최대 동시 리스크 (Δ) | 청산 "
        "| 수익/MDD 배율 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for segment in SEGMENT_ORDER:
        base, arm = _pick(frame, BASE_ARM, segment), _pick(frame, COUNTERFACTUAL_ARM, segment)
        if base is None or arm is None:
            continue
        trade_delta = int(arm["num_trades"]) - int(base["num_trades"])
        rom_base, rom_arm = base["return_over_mdd"], arm["return_over_mdd"]
        # 🚨 수익/MDD의 **절댓값**은 여기서 읽을 수 없다 — 분자가 6년 복리 총수익이라 자릿수가
        # 천문학적이다(WAN-169/213). 배율(×)로만 싣고 판정은 MDD·승률로 낸다.
        rom = (
            "—"
            if _missing(rom_base) or _missing(rom_arm) or float(rom_base) == 0.0
            else f"×{float(rom_arm) / float(rom_base):.3f}"
        )
        lines.append(
            f"| `{segment}` | {int(arm['num_trades'])} ({trade_delta:+}) "
            f"| {_pct(arm['win_rate'])} ({_pp(arm['win_rate'] - base['win_rate'])}) "
            f"| **{_pct(arm['max_drawdown'])} "
            f"({_pp(arm['max_drawdown'] - base['max_drawdown'])})** "
            f"| {_pct(arm['max_concurrent_risk'])} "
            f"({_pp(arm['max_concurrent_risk'] - base['max_concurrent_risk'])}) "
            f"| {int(arm['liquidation_events'])} | {rom} |"
        )
    lines += [
        "",
        "## 읽지 말아야 할 것",
        "",
        "* 🚨 **체결 보수화(`pen_5bp`)가 이 축을 대신하지 않는다** — 그쪽은 *「주문이 "
        "채워지느냐」*(큐 우선순위), 이건 *「채워진 뒤 그 1분 안의 순서」*다. 다른 질문이라 "
        "이 저장소의 모든 체결 보수화 관문이 이 낙관을 통과시켜 왔다.",
        "* ❌ **기본값 전환 제안이 아니다** — `no_same_step_tp`를 기본으로 켜는 것은 이 "
        "저장소의 **모든 지정가 백테스트 수치**를 움직이는 재-베이스라인이고 **사용자 "
        "결정**이다(WAN-132/149/159급 파급). 개발자 임의 착수 금지.",
        "* 🚨 **「엣지 없음」(WAN-84/88/111/114/124/151/201/248) 불변** — 이 축은 *진입 규칙이 "
        "무작위와 구분되는가*가 아니라 *이미 잰 숫자가 얼마나 낙관인가*를 묻는다. **다른 "
        "질문이다.**",
        "* ⚠️ 전부 `baseline`(닿으면 체결) 위 값이고 6년 MDD는 폭락 미포함 **바닥선**이다.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WAN-336 같은 분 익절 관측 + 반사실")
    parser.add_argument("--symbols", default=None, help="쉼표 구분(기본: 채택 12종목)")
    parser.add_argument("--tf", default=None, help="쉼표 구분(기본: 채택 4TF)")
    parser.add_argument("--start", default=harness.DEFAULT_START)
    parser.add_argument("--end", default=harness.DEFAULT_END)
    parser.add_argument("--arms", default=None, help=f"쉼표 구분(기본: {','.join(ARM_ORDER)})")
    parser.add_argument("--jobs", type=int, default=harness.default_jobs())
    parser.add_argument("--append", action="store_true", help="기존 CSV에 이어 쓴다")
    parser.add_argument("--from-csv", action="store_true", help="적재된 CSV로 요약만 재생성")
    return parser.parse_args(argv)


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    if existing.empty:
        return fresh
    merged = pd.concat([existing, fresh], ignore_index=True)
    return merged.drop_duplicates(subset=list(keys), keep="last").reset_index(drop=True)


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    tf_csv = REPORTS_DIR / "wan336_same_step_tp_by_tf.csv"

    if args.from_csv:
        frame, loo, tf_frame = _read(CSV_PATH), _read(LOO_CSV_PATH), _read(tf_csv)
        if frame.empty:
            print(f"[wan336] {CSV_PATH}가 없습니다 — 먼저 격자를 돌리세요.")
            return 1
        SUMMARY_PATH.write_text(build_summary(frame, loo, tf_frame), encoding="utf-8")
        print(f"[wan336] 요약 재생성: {SUMMARY_PATH}")
        return 0

    symbols = (
        [s.strip() for s in args.symbols.split(",")] if args.symbols else harness.DEFAULT_SYMBOLS
    )
    timeframes = [t.strip() for t in args.tf.split(",")] if args.tf else harness.DEFAULT_TIMEFRAMES
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else list(ARM_ORDER)
    unknown = [a for a in arms if a not in ARM_ORDER]
    if unknown:
        print(f"[wan336] 모르는 팔: {unknown} (가능: {', '.join(ARM_ORDER)})")
        return 2

    base_rows = _read(CSV_PATH) if args.append else pd.DataFrame()
    base_loo = _read(LOO_CSV_PATH) if args.append else pd.DataFrame()
    base_tf = _read(tf_csv) if args.append else pd.DataFrame()

    def persist(rows: list[SameStepRow], loo: list[SameStepLooRow], tf_frame: pd.DataFrame) -> None:
        nonlocal base_rows, base_loo, base_tf
        base_rows = _merge(base_rows, rows_to_frame(rows), CSV_KEYS)
        base_loo = _merge(base_loo, loo_to_frame(loo), LOO_CSV_KEYS)
        if not tf_frame.empty:
            base_tf = _merge(base_tf, tf_frame, ("arm", "segment", "timeframe"))
        base_rows.to_csv(CSV_PATH, index=False)
        base_loo.to_csv(LOO_CSV_PATH, index=False)
        base_tf.to_csv(tf_csv, index=False)
        print(f"[wan336] 적재: {CSV_PATH} ({len(base_rows)}행)", flush=True)

    run_report(
        symbols,
        timeframes,
        arms=arms,
        start=args.start,
        end=args.end,
        jobs=args.jobs,
        on_arm=persist,
    )
    SUMMARY_PATH.write_text(build_summary(base_rows, base_loo, base_tf), encoding="utf-8")
    print(f"[wan336] 요약: {SUMMARY_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
