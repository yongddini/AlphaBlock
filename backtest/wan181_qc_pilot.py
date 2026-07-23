"""WAN-181: QC 포팅 파일럿 불일치 감사 — 정본 엔진 vs 이벤트 재배열 (BTC 1h).

사용자 결정(2026-07-23)으로 페이퍼 실행 경로가 **길 A(QuantConnect 클라우드)** 로
정해졌고, 그 선행 필수가 이 감사다: 채택 엔진을 QC의 이벤트 구동 실행 모델로 옮겼을 때
(`qc.event_engine`) **같은 데이터에서 같은 거래가 나오는가**. 데이터·로직 두 축을 한
번에 움직이면 차이의 출처를 못 가르므로(WAN-74의 교훈), 이 감사는 **데이터를 저장소로
고정하고 로직 축만** 움직인다 — 남는 데이터 축은 QC 클라우드에서 `qc.algorithm`으로
잰다(사용자 QC 계정 필요, wan181.md §남은-축).

## 대조 축 3개

1. **후보(셋업) 축** — 정본 `build_zone_limit_candidates` vs 이벤트 엔진의 가상 후보를
   `(trigger_time, tap_index, zone_key)`로 조인해 진입·청산 필드를 대조한다. 밴드 재산정·
   체결 판정·청산 산출의 재배열이 여기서 검증된다.
2. **시퀀싱 축** — 정본 시퀀서(청산 시각 정렬 = 미래 정보)를 이벤트 엔진의 후보에 적용한
   결과 vs 이벤트 엔진의 **온라인 북**(등록 순서 타이브레이크) 거래를 대조한다. 같은 분
   체결 타이 횟수가 이 축의 위험 지표다(0이면 동치).
3. **헤드라인** — 두 팔의 최종 거래·성과 지표(거래 수·승률·수익·MDD) 대조.

재현:
    uv run python -m backtest.wan181_qc_pilot            # 전체 실행(무거움 — 6년 1분봉)
    uv run python -m backtest.wan181_qc_pilot --from-csv # 저장된 CSV로 요약만 재생성
    uv run python -m backtest.wan181_qc_pilot --no-funding  # QC 1단 대조용(펀딩 끔)

출력: `backtest/reports/wan181_qc_pilot.csv`(후보 조인) ·
`wan181_qc_pilot_trades_event.csv`(이벤트 북 거래 — QC ObjectStore CSV와 같은 열) ·
`wan181_qc_pilot_aggregate.csv`(집계) · `wan181_qc_pilot_summary.md`.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest.models import BacktestConfig, Trade
from backtest.run import parse_date_ms
from backtest.zone_limit_backtest import (
    _Candidate,
    build_result_from_trades,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from qc.event_engine import EventBacktestOutcome, run_event_backtest
from strategy.models import ConfluenceParams

REPORTS_DIR = Path("backtest/reports")

PILOT_SYMBOL = "BTC/USDT:USDT"
PILOT_TIMEFRAME = "1h"
#: 못 박은 채택 창(WAN-182). `qc.algorithm.AlphaBlockPilotAlgorithm`과 같은 좌표다.
PILOT_START = harness.DEFAULT_START
PILOT_END = harness.DEFAULT_END

JOIN_CSV = REPORTS_DIR / "wan181_qc_pilot.csv"
EVENT_TRADES_CSV = REPORTS_DIR / "wan181_qc_pilot_trades_event.csv"
AGGREGATE_CSV = REPORTS_DIR / "wan181_qc_pilot_aggregate.csv"
SUMMARY_MD = REPORTS_DIR / "wan181_qc_pilot_summary.md"

#: 후보 축에서 대조하는 필드 — `_Candidate`의 체결·청산 경제성 전부.
COMPARE_FIELDS = (
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "reason",
    "stop_price",
    "penetration",
)


def _zone_key_str(cand: _Candidate) -> str:
    if cand.zone_key is None:
        return ""
    return "|".join(str(v) for v in sorted(cand.zone_key))


def _cand_key(cand: _Candidate) -> tuple[int, int, str]:
    return (cand.trigger_time, cand.tap_index, _zone_key_str(cand))


def build_join_frame(canon: list[_Candidate], event: list[_Candidate]) -> pd.DataFrame:
    """두 팔의 후보를 셋업 키로 아우터 조인한 행 프레임."""
    canon_map = {_cand_key(c): c for c in canon}
    event_map = {_cand_key(c): c for c in event}
    if len(canon_map) != len(canon) or len(event_map) != len(event):
        raise ValueError(
            "셋업 키(trigger_time, tap_index, zone_key)가 유일하지 않습니다 — "
            "조인 축이 무너지므로 감사를 진행하지 않습니다."
        )
    rows: list[dict[str, object]] = []
    for key in sorted(set(canon_map) | set(event_map)):
        c = canon_map.get(key)
        e = event_map.get(key)
        row: dict[str, object] = {
            "trigger_time": key[0],
            "tap_index": key[1],
            "zone_key": key[2],
            "in_canonical": c is not None,
            "in_event": e is not None,
        }
        for field_name in COMPARE_FIELDS:
            cv = getattr(c, field_name) if c is not None else None
            ev = getattr(e, field_name) if e is not None else None
            if field_name == "reason":
                cv = cv.value if cv is not None else None
                ev = ev.value if ev is not None else None
            row[f"canon_{field_name}"] = cv
            row[f"event_{field_name}"] = ev
        row["fields_match"] = bool(
            c is not None
            and e is not None
            and all(row[f"canon_{f}"] == row[f"event_{f}"] for f in COMPARE_FIELDS)
        )
        rows.append(row)
    columns = [
        "trigger_time",
        "tap_index",
        "zone_key",
        "in_canonical",
        "in_event",
        *(f"{arm}_{f}" for f in COMPARE_FIELDS for arm in ("canon", "event")),
        "fields_match",
    ]
    # 후보가 0이어도 스키마는 유지한다 — 빈 프레임에 컬럼이 없으면 집계·요약이 무너진다.
    return pd.DataFrame(rows, columns=columns)


def trades_frame(trades: list[Trade]) -> pd.DataFrame:
    """거래 목록 → QC ObjectStore CSV(`qc.algorithm`)와 같은 열의 프레임."""
    rows = [
        {
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "exit_time": t.exits[-1].time,
            "exit_price": t.exits[-1].price,
            "reason": t.exits[-1].reason.value,
            "quantity": t.quantity,
            "realized_pnl": t.realized_pnl,
        }
        for t in trades
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "entry_time",
            "entry_price",
            "exit_time",
            "exit_price",
            "reason",
            "quantity",
            "realized_pnl",
        ],
    )


def _trades_equal(a: list[Trade], b: list[Trade]) -> tuple[int, float]:
    """두 거래 열의 (불일치 행 수, 실현손익 총합 절대차)."""
    fa, fb = trades_frame(a), trades_frame(b)
    if len(fa) != len(fb):
        return abs(len(fa) - len(fb)) + max(len(fa), len(fb)), float("nan")
    if len(fa) == 0:
        return 0, 0.0
    mismatched = int((fa != fb).any(axis=1).sum())
    pnl_gap = abs(float(fa["realized_pnl"].sum()) - float(fb["realized_pnl"].sum()))
    return mismatched, pnl_gap


def _metrics_row(
    arm: str, trades: list[Trade], cfg: BacktestConfig, timeframe: str
) -> dict[str, object]:
    metrics = build_result_from_trades(trades, cfg, timeframe).metrics
    return {
        "arm": arm,
        "num_trades": metrics.num_trades,
        "win_rate": metrics.win_rate,
        "total_return": metrics.total_return,
        "max_drawdown": metrics.max_drawdown,
        "final_equity": metrics.final_equity,
        "total_funding_cost": metrics.total_funding_cost,
    }


def run_audit(*, funding: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """감사 본체 — (후보 조인, 이벤트 거래, 집계) 프레임을 낸다."""
    params: ConfluenceParams = harness.build_params()  # 채택 기본값 그대로.
    cfg = harness.build_config(PILOT_TIMEFRAME, funding_enabled=funding)
    market = harness.load_market_data(
        PILOT_SYMBOL,
        PILOT_TIMEFRAME,
        start_ms=parse_date_ms(PILOT_START),
        end_ms=parse_date_ms(PILOT_END),
    )
    if market.empty or market.df_1m.empty:
        raise SystemExit(f"{PILOT_SYMBOL} {PILOT_TIMEFRAME}: 데이터가 없습니다.")
    funding_rates = market.funding_rates if funding else None
    ob_result = harness.detect_order_blocks(market)

    # 팔 1 — 정본(셋업별 배치 + 일괄 시퀀싱).
    canon_cands, canon_stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        PILOT_TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
    )
    canon_trades = [t for _, t in sequence_with_candidates(canon_cands, cfg, funding_rates)]

    # 팔 2 — 이벤트 재배열(QC 포팅 골격, 단일 패스 + 온라인 북).
    outcome: EventBacktestOutcome = run_event_backtest(
        market.htf_df,
        market.df_1m,
        PILOT_TIMEFRAME,
        params=params,
        cfg=cfg,
        order_block_result=ob_result,
        funding_rates=funding_rates,
    )

    join = build_join_frame(canon_cands, outcome.candidates)

    # 시퀀싱 축 — 정본 시퀀서를 이벤트 후보에 적용해 온라인 북과 대조한다.
    reseq_trades = [t for _, t in sequence_with_candidates(outcome.candidates, cfg, funding_rates)]
    seq_mismatch, seq_pnl_gap = _trades_equal(reseq_trades, outcome.trades)
    final_mismatch, final_pnl_gap = _trades_equal(canon_trades, outcome.trades)

    aggregate_rows: list[dict[str, object]] = [
        _metrics_row("canonical", canon_trades, cfg, PILOT_TIMEFRAME),
        _metrics_row("event", outcome.trades, cfg, PILOT_TIMEFRAME),
    ]
    counters: dict[str, object] = {
        "arm": "counters",
        "num_trades": None,
        "win_rate": None,
        "total_return": None,
        "max_drawdown": None,
        "final_equity": None,
        "total_funding_cost": None,
        "canon_eligible": canon_stats.eligible,
        "canon_filled": canon_stats.filled,
        "canon_penetrations": canon_stats.penetrations,
        "event_eligible": outcome.stats.eligible,
        "event_filled": outcome.stats.filled,
        "event_penetrations": outcome.stats.penetrations,
        "same_minute_fill_ties": outcome.same_minute_fill_ties,
        "event_skipped_fills": outcome.skipped_fills,
        "seq_axis_mismatched_trades": seq_mismatch,
        "seq_axis_pnl_gap": seq_pnl_gap,
        "final_mismatched_trades": final_mismatch,
        "final_pnl_gap": final_pnl_gap,
        "funding_enabled": funding,
    }
    aggregate = pd.DataFrame(aggregate_rows + [counters])
    return join, trades_frame(outcome.trades), aggregate


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def render_summary(join: pd.DataFrame, aggregate: pd.DataFrame) -> str:
    """감사 요약 md — 세 축의 대조와 판정."""
    counters = aggregate[aggregate["arm"] == "counters"].iloc[0]
    arms = {str(r["arm"]): r for _, r in aggregate.iterrows() if r["arm"] != "counters"}
    canon, event = arms["canonical"], arms["event"]

    both = join[join["in_canonical"] & join["in_event"]]
    only_canon = int((join["in_canonical"] & ~join["in_event"]).sum())
    only_event = int((~join["in_canonical"] & join["in_event"]).sum())
    mismatched = int((~both["fields_match"]).sum()) if len(both) else 0
    ties = int(counters["same_minute_fill_ties"])
    seq_mismatch = int(counters["seq_axis_mismatched_trades"])
    final_mismatch = int(counters["final_mismatched_trades"])

    candidate_ok = only_canon == 0 and only_event == 0 and mismatched == 0
    sequencing_ok = seq_mismatch == 0
    final_ok = final_mismatch == 0
    if candidate_ok and sequencing_ok and final_ok:
        verdict = (
            "✅ **로직 축 동치** — 후보·시퀀싱·최종 거래가 전부 일치한다. 이벤트 재배열"
            "(QC 포팅 골격)은 저장소 데이터 위에서 정본 엔진과 같은 거래를 낸다. "
            "남은 불일치 가능성은 **데이터 축**(QC CoinAPI vs 우리 수집)과 **집행 축**"
            "(QC 체결 모델)뿐이며, 둘 다 QC 클라우드 실행(사용자 계정 필요)에서 잰다."
        )
    else:
        verdict = (
            f"⚠️ **불일치 있음** — 후보 축(한쪽에만 {only_canon + only_event} · 필드 불일치 "
            f"{mismatched}) · 시퀀싱 축(불일치 거래 {seq_mismatch} · 동일 분 타이 {ties}) · "
            f"최종({final_mismatch}). 아래 표와 `wan181_qc_pilot.csv`에서 출처를 가른다."
        )

    lines = [
        "# WAN-181 — QC 포팅 파일럿 불일치 감사 (정본 vs 이벤트 재배열)",
        "",
        f"- 셀: `{PILOT_SYMBOL}` × `{PILOT_TIMEFRAME}` × 못 박은 채택 창 "
        f"`{PILOT_START}` ~ `{PILOT_END}` (WAN-182) × 채택 기본값(`ConfluenceParams()`)",
        f"- 펀딩 반영: `{bool(counters['funding_enabled'])}` — QC 1단 대조는 "
        "`--no-funding` 실행과 맞춘다.",
        "- 움직인 축은 **실행 모델 하나**다(셋업별 배치 → 이벤트 단일 패스). 데이터·탐지·"
        "파라미터·비용은 두 팔이 같은 객체를 공유한다.",
        "- 재현: `uv run python -m backtest.wan181_qc_pilot` (요약만: `--from-csv`)",
        "",
        "## 판정",
        "",
        verdict,
        "",
        "## §1 후보(셋업) 축 — 밴드 재산정·체결·청산의 재배열 검증",
        "",
        "| 항목 | 정본 | 이벤트 | 일치 |",
        "| -- | --: | --: | -- |",
        f"| eligible 셋업 | {int(counters['canon_eligible'])} | {int(counters['event_eligible'])} "
        f"| {'✅' if counters['canon_eligible'] == counters['event_eligible'] else '❌'} |",
        f"| 체결(filled) | {int(counters['canon_filled'])} | {int(counters['event_filled'])} "
        f"| {'✅' if counters['canon_filled'] == counters['event_filled'] else '❌'} |",
        f"| 관통(penetrations) | {int(counters['canon_penetrations'])} "
        f"| {int(counters['event_penetrations'])} "
        f"| {'✅' if counters['canon_penetrations'] == counters['event_penetrations'] else '❌'} |",
        f"| 후보 수(조인 {len(join)}행) | {int(join['in_canonical'].sum())} "
        f"| {int(join['in_event'].sum())} | 한쪽에만: {only_canon + only_event} |",
        f"| 진입·청산 7필드 전부 일치 | — | — | 불일치 {mismatched}행 |",
        "",
        "## §2 시퀀싱 축 — 온라인 북 vs 정본 시퀀서(미래 정보 타이브레이크)",
        "",
        f"- 같은 분 체결 경합(타이): **{ties}회** — 0이면 온라인 북이 정본 정렬과 다른 선택을 "
        "할 여지 자체가 없다.",
        f"- 북 점유로 스킵된 가상 체결: {int(counters['event_skipped_fills'])}건(정본 시퀀서의 "
        "겹침 스킵에 대응).",
        f"- 정본 시퀀서를 이벤트 후보에 적용 vs 온라인 북: 불일치 거래 **{seq_mismatch}건** "
        f"(손익 총합 절대차 {float(counters['seq_axis_pnl_gap']):.6g}).",
        "",
        "## §3 헤드라인 — 최종 거래·성과",
        "",
        "| 지표 | 정본 | 이벤트 |",
        "| -- | --: | --: |",
        f"| 거래 수 | {int(canon['num_trades'])} | {int(event['num_trades'])} |",
        f"| 승률 | {float(canon['win_rate']) * 100:.2f}% | {float(event['win_rate']) * 100:.2f}% |",
        f"| 총수익률 | {_fmt_pct(float(canon['total_return']))} "
        f"| {_fmt_pct(float(event['total_return']))} |",
        f"| MDD | {float(canon['max_drawdown']) * 100:.2f}% "
        f"| {float(event['max_drawdown']) * 100:.2f}% |",
        f"| 최종 자본 | {float(canon['final_equity']):.2f} | {float(event['final_equity']):.2f} |",
        f"| 누적 펀딩비 | {float(canon['total_funding_cost']):.2f} "
        f"| {float(event['total_funding_cost']):.2f} |",
        f"- 최종 거래 대조: 불일치 **{final_mismatch}건** "
        f"(손익 총합 절대차 {float(counters['final_pnl_gap']):.6g}).",
        "",
        "## §4 남은 축 — 이 감사가 재지 **않은** 것",
        "",
        "- **데이터 축**: QC CoinAPI 바이낸스 선물 봉이 우리 수집 봉과 같은가 — "
        "`qc/algorithm.py`(1단: QC = 데이터 공급자, 로직은 이 감사를 통과한 드라이버 재생)를 "
        "QC 클라우드 백테스트로 돌려 ObjectStore CSV를 `wan181_qc_pilot_trades_event.csv`"
        "(`--no-funding` 실행 산출)와 diff 한다. **사용자 QC 계정 필요.**",
        "- **집행 축**: QC `LimitOrder` + 1분 스냅샷 체결 모델(2단) — 1단이 붙은 뒤에만 "
        "의미가 있다.",
        "- **스트리밍 간극**(2단 설계 입력): (a) 탭 시그널은 상위TF 봉 마감에야 확정되는데 "
        "정본 셋업은 탭 봉 슬롯 시작부터 체결을 허용한다 · (b) 동시 1포지션 아래 실주문 "
        "하나만 걸기는 정본의 가상 체결 스킵과 의미가 다르다. 처리 방안은 **사용자 결정**"
        "(wan181.md).",
        "",
        "*채택 수치가 아니라 포팅 감사다 — 기본값·토대·엔진 불변, 실거래 아님.*",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-181 QC 포팅 파일럿 불일치 감사")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV로 요약만 재생성")
    parser.add_argument(
        "--no-funding",
        action="store_true",
        help="펀딩비 끄기 — QC 1단(펀딩 시계열 없음) 대조용 트레이드 CSV를 만들 때 쓴다",
    )
    args = parser.parse_args()

    if args.from_csv:
        join = pd.read_csv(JOIN_CSV)
        aggregate = pd.read_csv(AGGREGATE_CSV)
    else:
        join, event_trades, aggregate = run_audit(funding=not args.no_funding)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        join.to_csv(JOIN_CSV, index=False)
        event_trades.to_csv(EVENT_TRADES_CSV, index=False)
        aggregate.to_csv(AGGREGATE_CSV, index=False)

    summary = render_summary(join, aggregate)
    SUMMARY_MD.write_text(summary, encoding="utf-8")
    print(summary)
    counters = aggregate[aggregate["arm"] == "counters"].iloc[0]
    if (
        not math.isnan(float(counters["final_pnl_gap"]))
        and int(counters["final_mismatched_trades"]) == 0
    ):
        print("[wan181] 로직 축 동치 — 남은 축(데이터·집행)은 QC 클라우드에서.")


if __name__ == "__main__":
    main()
