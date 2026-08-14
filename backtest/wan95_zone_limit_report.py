"""WAN-95: 지정가(zone_limit, B안) 채택 성과 재산출.

배경은 이슈 WAN-95 본문 참고. 사용자의 실매매는 **오더블록 존에 지정가를 걸어두고
가격이 닿는 순간 체결**하는 방식이다(채택 기본값 `ConfluenceParams()` = 지정가 + 실시간
RSI + 롱 온리). 이 모듈이 그 채택 기본값을 채택 좌표 — 12종목(WAN-307) × 작업 TF
15m·1h·2h·4h(WAN-252) × 못 박은 6년 창(WAN-182) — 에서 재산출한다.

⚠️ **A안(종가 시장가, `entry_mode="close"`) 비교팔은 WAN-200 §A로 제거됐다** — 이 리포트는
이제 채택된 B안 단독 성적표다(종가 대조표·델타표 없음). B안 수치는 제거로 바뀌지 않는다
(두 진입 엔진이 독립). 옛 종가 대조가 필요하면 git 이력·`docs/decisions/wan200.md` 참고.

## 방법론

B안(지정가)은 1분봉이 커버하는 구간의 셋업만 평가한다(`zone_limit_backtest` 참고). 상위TF
히스토리 전체는 오더블록 탐지·지표 워밍업에 쓰고, 성과 집계만 그 창으로 한정한다.

## 비용

- **지정가 진입 = 메이커**: 메이커 수수료(2bp), 슬리피지 없음.
- **청산은 테이커**: 손절·익절 도달은 시장가 성격(테이커 4bp + 슬리피지 5bp).

왕복 비용은 0.11%(2+0+4+5 bp)다. WAN-91이 "15m 채택 제외"를 **비용** 근거로 권고했는데,
그 전제(테이커 종가 진입, 왕복 0.18%)가 사용자의 실매매와 달랐다 — 이 리포트가 메이커
비용으로 다시 돌린 재산출이다(옛 손익표 `wan87_long_only_summary.md`·WAN-91 펀딩 리포트는
종가 진입 = **사용자가 하지 않는 매매**의 성과였다).

## 펀딩비

`funding_enabled=True`만으로는 손익이 안 바뀐다. `data.FundingRateStore.get_rates`로
조회한 펀딩비를 엔진에 **명시적으로 전달**하고, 커버리지를 행에 실어 "얼마나 반영됐는지"가
리포트에서 보이게 한다(WAN-63/91이 두 번 잡은 조용한 실패의 재발 방지).

## 체결률(fill rate)

지정가는 **닿지 않으면 체결되지 않는다**. `eligible`(1분봉이 커버한 활성 셋업) 대비
`filled`(지정가 체결) 비율이 이 전환의 핵심 트레이드오프(비용↓ vs 기회↓)를 드러낸다.

## 재현

```
python -m backtest.wan95_zone_limit_report             # 실데이터 재산출(백테스트)
python -m backtest.wan95_zone_limit_report --from-csv   # 커밋 CSV에서 요약만 재생성(백테스트 없음)
```
"""

from __future__ import annotations

import argparse
import gc
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    CACHE_DIR as _CACHE_DIR,
)
from backtest.harness import (
    DB_PATH as _DB_PATH,
)
from backtest.harness import (
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
)
from backtest.models import BacktestConfig
from backtest.run import parse_date_ms
from backtest.sweep import default_backtest_config
from backtest.zone_limit_backtest import run_zone_limit_backtest_verbose
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from strategy.models import ConfluenceParams, OrderBlockResult
from strategy.order_blocks import OrderBlockDetector

#: 유니버스 확장분 = 기존 6종목(WAN-111) 이후 합류한 종목 전부(WAN-182의 DOGE·LINK·LTC +
#: WAN-307의 ADA·DOT·BCH). 확장분이 펀딩 데이터 없이 합류하면 펀딩비 0으로 성과가
#: 부풀려지므로 아래 `apply_funding_proxy`가 보정한다 — 단 **대리의 도너 풀은 기존 6종목**
#: 이다(WAN-180 규칙: 이 목록에 든 종목은 도너가 되지 않는다). 2026-08-14 실측으로는
#: 6종목 전부 자기 확정 펀딩이 채택 창을 덮어(DOGE·LINK·LTC는 WAN-292 백필, ADA·DOT·BCH는
#: 상장 초기부터 수집분) 대리는 **무동작**이다 — 안전망으로만 남는다.
NEW_SYMBOLS: tuple[str, ...] = (
    "DOGE/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
    "ADA/USDT:USDT",
    "DOT/USDT:USDT",
    "BCH/USDT:USDT",
)

#: 채택 기본값(WAN-95) — 지정가 + 실시간 RSI + 롱 온리. `ConfluenceParams()` 그 자체다.
ZONE_LIMIT_PARAMS = ConfluenceParams()


class ZoneLimitRow(BaseModel):
    """한 (심볼, TF, 진입 방식) 셀의 재산출 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    entry_mode: str
    """`zone_limit`(채택). A안(`close`) 비교팔은 WAN-200 §A로 제거됐다."""
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    profit_factor: float | None
    """거래가 없거나 손실이 0이면 None(정의 불가)."""
    sharpe: float | None
    """거래가 없으면 None."""
    total_funding_cost: float
    funding_coverage: float | None
    """펀딩 데이터 커버리지(0~1). None이면 펀딩 미사용."""
    eligible_setups: int | None = None
    """1분봉이 커버해 시뮬레이션에 들어간 활성 셋업 수(지정가만)."""
    num_filled: int | None = None
    """지정가가 실제 체결된 셋업 수(지정가만)."""
    fill_rate: float | None = None
    """`num_filled / eligible_setups`. 지정가 전환의 기회비용 축(지정가만)."""
    num_penetrations: int | None = None
    """진입과 손절이 같은 1분 스텝에서 일어난 관통 건수(낙관 편향 감사, 지정가만)."""


def run_symbol_timeframe(
    htf_df: pd.DataFrame,
    df_1m: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    funding_rates: list[FundingRate],
    order_block_result: OrderBlockResult,
    backtest_config: BacktestConfig | None = None,
) -> list[ZoneLimitRow]:
    """한 (심볼, TF)에서 지정가(B안, 채택)를 재산출한다."""
    cfg = backtest_config or default_backtest_config(timeframe)

    # 지정가(B안, 채택): 1분봉 서브스텝으로 존 근단 지정가 체결을 시뮬레이션한다.
    zl_result, zl_stats = run_zone_limit_backtest_verbose(
        htf_df,
        df_1m,
        timeframe,
        confluence_params=ZONE_LIMIT_PARAMS,
        backtest_config=cfg,
        order_block_result=order_block_result,
        funding_rates=funding_rates,
    )

    return [
        ZoneLimitRow(
            symbol=symbol,
            timeframe=timeframe,
            entry_mode="zone_limit",
            num_trades=zl_result.metrics.num_trades,
            win_rate=zl_result.metrics.win_rate,
            total_return=zl_result.metrics.total_return,
            max_drawdown=zl_result.metrics.max_drawdown,
            profit_factor=zl_result.metrics.profit_factor,
            sharpe=zl_result.metrics.sharpe,
            total_funding_cost=zl_result.metrics.total_funding_cost,
            funding_coverage=zl_result.metrics.funding_coverage,
            eligible_setups=zl_stats.eligible,
            num_filled=zl_stats.filled,
            fill_rate=zl_stats.fill_rate,
            num_penetrations=zl_stats.penetrations,
        ),
    ]


def apply_funding_proxy(
    funding_by_symbol: dict[str, list[FundingRate]],
) -> tuple[dict[str, list[FundingRate]], str]:
    """신규 3종목의 빈 펀딩을 기존 종목 중 **최고 펀딩** 종목의 시계열로 대체한다.

    WAN-180과 같은 규칙(사용자 지시 2026-07-23): 대리 종목 = 신규가 아닌 종목 중 이 창의
    확정 펀딩 **평균이 가장 높은**(= 롱에게 가장 비싼) 종목. 보수적 대체다 — 실제 신규 종목
    펀딩이 이보다 쌌다면 성과는 여기 값보다 좋았을 것이다. 신규 종목이라도 **자기 데이터가
    있으면 덮지 않는다**(대리는 「데이터가 없어 0으로 계상되는」 문제의 교정이지 데이터
    교체가 아니다) — WAN-178 백필이 끝나면 이 함수는 저절로 아무것도 하지 않는다.
    """
    olds = {s: r for s, r in funding_by_symbol.items() if s not in NEW_SYMBOLS and r}
    targets = [s for s in funding_by_symbol if s in NEW_SYMBOLS and not funding_by_symbol[s]]
    if not olds or not targets:
        return dict(funding_by_symbol), ""

    def mean_confirmed(rates: list[FundingRate]) -> float:
        vals = [r.rate for r in rates if not r.is_predicted]
        return sum(vals) / len(vals) if vals else float("-inf")

    rate_by_symbol = {s: mean_confirmed(r) for s, r in olds.items()}
    proxy_symbol = max(rate_by_symbol, key=lambda s: rate_by_symbol[s])
    if rate_by_symbol[proxy_symbol] == float("-inf"):
        return dict(funding_by_symbol), ""

    out = dict(funding_by_symbol)
    for symbol in targets:
        out[symbol] = list(funding_by_symbol[proxy_symbol])
    note = (
        f"신규 종목 펀딩 대리(WAN-182 · WAN-180과 같은 규칙): "
        f"{', '.join(s.split('/')[0] for s in targets)}의 펀딩을 "
        f"**{proxy_symbol.split('/')[0]}**(기존 종목 중 확정 펀딩 평균 최고 "
        f"{rate_by_symbol[proxy_symbol] * 100:.4f}%/정산)의 시계열로 대체했다 — 신규 종목은 "
        "이 창에서 펀딩 데이터가 없어(WAN-178 백필 전) 그대로 두면 펀딩비 0으로 성과가 "
        "부풀려진다. 보수적(롱에게 가장 비싼) 대체이며, 백필이 끝나 자기 데이터가 생기면 "
        "대리는 자동으로 적용되지 않는다."
    )
    return out, note


def collect_rows(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = _DB_PATH,
    cache_dir: str = _CACHE_DIR,
) -> tuple[list[ZoneLimitRow], str]:
    """전 심볼×TF를 채택 창(못 박은 6년, WAN-182)에서 재산출한다.

    1분봉이 없는 심볼은 건너뛴다(지정가 평가 불가). 반환은 (행들, 펀딩 대리 각주) —
    각주가 비어 있지 않으면 신규 종목 펀딩이 대리 시계열로 계산된 것이다.

    ⚠️ 옛 판(WAN-159까지)은 `--years 3`(마지막 봉 기준 미끄러지는 창) × 3심볼 × 4TF였다 —
    WAN-182가 좌표를 채택 창으로 옮겼다. 창이 못 박혀 있으므로 새 봉이 쌓여도 이 표는
    비트 단위로 재현된다(데이터 드리프트 각주의 원인 하나가 사라진다).
    """
    store = OhlcvStore(db_path, cache_dir=cache_dir)
    funding_store = FundingRateStore(db_path)
    start_ms = parse_date_ms(start)
    end_ms = parse_date_ms(end)

    # 펀딩 조회는 요청 심볼 + **대리 도너 후보**(채택 유니버스의 비-신규 종목)를 함께 본다 —
    # 신규 종목만 부분 실행해도(`--symbols DOGE/...`) 대리가 같은 도너에서 같은 시계열을
    # 받게 하기 위해서다. 조회만 넓히는 것이고 행은 요청 심볼만 만든다(펀딩 테이블은 작아
    # 비용이 무시할 수준이다).
    donor_candidates = tuple(s for s in DEFAULT_SYMBOLS if s not in NEW_SYMBOLS)
    query_symbols = list(dict.fromkeys([*donor_candidates, *symbols]))
    funding_by_symbol = {
        symbol: funding_store.get_rates(
            symbol, start_ms=start_ms, end_ms=end_ms, include_predicted=True
        )
        for symbol in query_symbols
    }
    funding_by_symbol, proxy_note = apply_funding_proxy(funding_by_symbol)

    rows: list[ZoneLimitRow] = []
    for symbol in symbols:
        # 직전 심볼의 1분봉을 확실히 놓고 나서 다음 것을 읽는다 — 6년 1분봉(심볼당 ~315만 행)
        # 은 두 심볼 몫이 겹치는 순간이 메모리 피크다. 9종목 직렬 실행이 이 지점에서 조용히
        # 죽은 적이 있다(WAN-182 재산출 1차 시도 — 트레이스백 없이 프로세스 소멸).
        gc.collect()
        df_1m = store.load(symbol, "1m", start_ms=start_ms, end_ms=end_ms).reset_index(drop=True)
        if df_1m.empty:
            print(f"[wan95] {symbol}: 1분봉 없음 — 지정가 평가 불가, 건너뜀")
            continue
        funding_rates = funding_by_symbol[symbol]
        for timeframe in timeframes:
            htf_df = store.load(symbol, timeframe, start_ms=start_ms, end_ms=end_ms).reset_index(
                drop=True
            )
            if htf_df.empty:
                continue
            window_start = int(htf_df["open_time"].iloc[0])
            # 상위TF 창으로 1분봉을 잘라 메모리·시간을 아낀다(워밍업은 상위TF가 담당).
            window_1m = df_1m[df_1m["open_time"] >= window_start]
            if window_1m.empty:
                continue
            ob_result = OrderBlockDetector().run(htf_df)
            rows.extend(
                run_symbol_timeframe(
                    htf_df,
                    window_1m,
                    symbol=symbol,
                    timeframe=timeframe,
                    funding_rates=funding_rates,
                    order_block_result=ob_result,
                )
            )
            print(
                f"[wan95] {symbol} {timeframe}: {len(htf_df)}봉, "
                f"1분봉 {len(window_1m)}행, 펀딩 {len(funding_rates)}건",
                flush=True,
            )
            del window_1m, htf_df, ob_result
        # 이름을 놓아야 다음 심볼 로드 때 두 심볼 몫이 겹치지 않는다(위 gc.collect() 주석).
        del df_1m
    return rows, proxy_note


_TF_ORDER = {"15m": 0, "1h": 1, "2h": 2, "4h": 3, "1d": 4}


def rows_to_frame(rows: list[ZoneLimitRow]) -> pd.DataFrame:
    """행들을 심볼×TF×진입방식 순으로 정렬한 DataFrame으로."""
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if frame.empty:
        return frame
    frame["_tf"] = frame["timeframe"].map(_TF_ORDER)
    frame = frame.sort_values(["symbol", "_tf", "entry_mode"]).drop(columns=["_tf"])
    return frame.reset_index(drop=True)


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def build_tf_verdict_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """TF별 채택 판단 근거표 — 진입 방식별 평균 수익률·플러스 심볼 수·평균 체결률.

    WAN-91이 "15m 채택 제외"를 **비용** 근거로 권고했으므로, 메이커 비용으로 다시 돌린
    이 표가 그 권고를 확정하거나 뒤집는 근거가 된다.
    """
    if frame.empty:
        return frame
    grouped = frame.groupby(["timeframe", "entry_mode"], as_index=False).agg(
        mean_return=("total_return", "mean"),
        positive_symbols=("total_return", lambda s: int((s > 0).sum())),
        num_symbols=("total_return", "size"),
        mean_mdd=("max_drawdown", "mean"),
        mean_fill_rate=("fill_rate", "mean"),
    )
    grouped["_tf"] = grouped["timeframe"].map(_TF_ORDER)
    return grouped.sort_values(["_tf", "entry_mode"]).drop(columns=["_tf"]).reset_index(drop=True)


def _tf_mean(frame: pd.DataFrame, timeframe: str, entry_mode: str, column: str) -> float | None:
    """한 (TF, 진입 방식)의 열 평균. 해당 셀이 없으면 None.

    한계 서술의 숫자를 본문에 **하드코딩하지 않기 위한** 헬퍼다(WAN-100). 예전엔
    "체결률 약 28%"·"승률 55% → 49%"가 문장에 박혀 있었는데, WAN-100이 엔진을 고쳐
    체결률이 50%대로 바뀌자 표는 갱신되고 그 옆 문장만 옛 숫자를 주장하는 상태가 됐다
    — 이 리포트가 고발하는 "문서와 실제가 갈라진다"를 리포트 자신이 저지른 셈이다.
    """
    cell = frame[(frame["timeframe"] == timeframe) & (frame["entry_mode"] == entry_mode)]
    if cell.empty:
        return None
    value = cell[column].mean()
    return None if pd.isna(value) else float(value)


def _verdict_lines(frame: pd.DataFrame) -> list[str]:
    """TF 채택 판단 섹션. 수치는 표에서 읽고, 결론·한계는 명시적으로 적는다."""
    verdict = build_tf_verdict_frame(frame)
    zl_fill = _tf_mean(frame, "15m", "zone_limit", "fill_rate")
    zl_win = _tf_mean(frame, "15m", "zone_limit", "win_rate")
    lines = [
        "## TF 채택 판단 (메이커 비용 기준 재산출)",
        "",
        "| TF | 진입 | 평균 return | 플러스 심볼 | 평균 MDD | 평균 체결률 |",
        "| -- | -- | --: | --: | --: | --: |",
    ]
    for _, row in verdict.iterrows():
        fill = _fmt_pct(row["mean_fill_rate"]) if pd.notna(row["mean_fill_rate"]) else "—"
        lines.append(
            f"| {row['timeframe']} | {row['entry_mode']} | {_fmt_pct(row['mean_return'])} | "
            f"{int(row['positive_symbols'])}/{int(row['num_symbols'])} | "
            f"{_fmt_pct(row['mean_mdd'])} | {fill} |"
        )
    lines += [
        "",
        "### 15m: WAN-91의 '채택 제외' 권고는 **유지되지 않는다**",
        "",
        "WAN-91은 15m을 채택 대상에서 제외할 것을 권고했고, 그 근거는 과최적화가 아니라 "
        "**비용**이었다 — 거래 수가 많아 왕복 0.18%(테이커)가 신호를 잠식한다는 것. "
        "그 권고의 전제(테이커 진입)가 사용자의 실매매와 달랐으므로, 메이커 진입으로 "
        "다시 돌린 이 표가 답이다: 당시 판(3심볼 × 3년)에서 **15m은 3심볼 전부 마이너스에서 "
        "3심볼 전부 플러스로 뒤집혔고**, 채택 좌표(9종목 × 6년, WAN-182)에서도 방향은 "
        "같다. 즉 15m의 문제는 신호가 아니라 "
        "비용이었다는 WAN-91의 진단 자체는 옳았고, 비용을 실제 매매 방식에 맞추자 결론이 "
        "반대로 나온다(A안 종가 대비 대조는 WAN-200 §A로 제거 — 옛 판은 git 이력·"
        "`docs/decisions/wan200.md` 참고).",
        "",
        "### 그러나 이 결과를 채택 근거로 쓰기 전 반드시 볼 한계",
        "",
        "1. **체결 가정이 낙관적이다.** 시뮬레이터는 가격이 지정가에 **닿으면 체결**로 "
        "본다(`backtest/substep.py`). 실제 지정가는 큐 우선순위가 있어, 가격이 스치기만 "
        f"하면 체결되지 않을 수 있다. 즉 실제 체결률은 이 표(15m 기준 {_fmt_pct(zl_fill)})"
        "보다 낮고, **체결된 거래만 골라 담는 편향**이 남는다. WAN-96이 이 가정을 보수화해 "
        "재검정했다.",
        f"2. **수익 개선을 승률만으로 설명할 수 없다**(15m 지정가 승률 {_fmt_pct(zl_win)}). "
        "개선의 몸통은 진입가·비용이다 — 지정가는 더 유리한 가격에 "
        "들어가 1R이 줄고 익절 목표가 가까워진다(고정 1:1.5R 규칙과의 상호작용). 옛 판"
        "(3심볼 × 3년)에서는 승률이 오히려 떨어지면서 수익이 늘어 이 성질이 극명했다.",
        "3. **4h는 표본이 작다**(체결률이 높아 기회 손실은 작지만 심볼당 거래가 적어 "
        "수익률 추정의 신뢰구간이 넓다). 1d는 WAN-182(작업 TF 15m·1h·4h 확정)가 표본 미달로 "
        "제외해 이 표에 없다 — 옛 판(3심볼 × 4TF)의 1d 행은 당시 기록이다.",
        "4. **1분봉 근사 잔존**: 채택 밴드(`intrabar_live`, WAN-132)는 미래를 보지 않지만 "
        "1분봉 근사라 **잔여 1분**이 남는다 — WAN-120이 그 잔여를 0으로 만든 감사에서 "
        "판정 불변(증분 88.5% 잔존)을 확인했다. 옛 판의 「탭 봉 SMA20 룩어헤드」 서술은 "
        "WAN-132 전환으로 채택 경로에서 사라졌다.",
        "",
        "따라서 이 리포트는 **'15m 제외 권고를 확정할 수 없다'**까지가 결론이다. "
        "15m 채택 여부는 체결 가정을 보수화한 재검정(체결률 하향·큐 모델) 후 판단할 것을 "
        "권고한다. 엣지 판정 자체는 WAN-88(매칭 널 재검정)이 이 엔진으로 다시 내야 한다.",
        "",
    ]
    return lines


def build_markdown(frame: pd.DataFrame, proxy_note: str = "") -> str:
    """리포트 마크다운. 수치 해석은 사람이 하되, 재현 커맨드·방법론을 함께 남긴다."""
    lines = [
        "# WAN-95: 지정가(zone_limit) 채택 성과",
        "",
        "**재현**: `python -m backtest.wan95_zone_limit_report`",
        "",
        "> 🧹 **A안(종가 진입) 비교팔은 WAN-200 §A로 제거됐다.** 이 리포트는 이제 채택된 "
        "**B안(존 지정가) 단독** 성적표다 — 종가 대조표·델타표는 걷어냈다. 종가 대비 대조가 "
        "필요하면 git 이력(제거 전 커밋) · "
        "[`docs/decisions/wan200.md`](../../docs/decisions/wan200.md) 참고. B안 수치는 제거로 "
        "바뀌지 않았다(두 엔진이 독립).",
        "",
        "> 🔁 **WAN-182(채택 좌표 재-베이스라인)로 이 표가 전면 재산출됐다 — 이번에 움직인 "
        "것은 엔진이 아니라 좌표다.** 유니버스 3심볼 → **9종목**(WAN-111의 6종목 + DOGE·LINK·"
        "LTC), 창 `--years 3`(마지막 봉 기준 미끄러지는 창) → **못 박은 6년 창 "
        f"{DEFAULT_START} ~ {DEFAULT_END}**(실효 5.85년, SOL 상장 하한 · 9종목 균일 창), "
        "TF 15m/1h/4h/1d → **작업 TF 15m·1h·4h**(WAN-179가 4h를 승격, 1d는 표본 미달로 제외 "
        "유지). `ConfluenceParams()`는 그대로이므로 **이 표의 옛 판과의 차이를 엔진 변화로 "
        "읽지 말 것** — 창·유니버스가 통째로 다르다(옛 판과 셀 비교 금지). 창이 못 박혀 "
        "있으므로 이제 이 표는 새 봉이 쌓여도 비트 단위로 재현된다. ⚠️ 신규 3종목(DOGE·LINK·"
        "LTC)은 이 창에서 **펀딩 데이터가 0행**이라(WAN-178 백필 전) 기존 종목 중 확정 펀딩 "
        "평균 최고(= 롱에게 가장 비싼) 종목의 시계열로 **대리 계산**한다(WAN-180과 같은 "
        "규칙, 아래 각주). 근거·파급은 "
        "[`docs/decisions/wan182.md`](../../docs/decisions/wan182.md).",
        "",
        "> 🔁 **WAN-307(유니버스 9→12종목)로 이 표가 다시 전면 재산출됐다 — 좌표 "
        "재-베이스라인**(사용자 결정 2026-08-14, WAN-182/252와 같은 패턴). 채택 유니버스에 "
        "**ADV 상위 3종목(ADA·DOT·BCH)**이 합류해 12종목 × 4TF가 됐다(합류 순서는 못 박은 "
        "창의 일중 달러 거래대금 내림차순으로 **동결된 유동성 규칙** — 자유 파라미터가 "
        "아니다). 근거는 WAN-304 사다리(12종목은 렌즈에 MDD가 +0.1%p로 거의 안 흔들리는데 "
        "15종목은 +4.3%p 튄다 · 밀림율 4.24% < 5% 포화선)이고 `ConfluenceParams()`는 "
        "그대로다. ⚠️ **9종목 판과 셀 비교 금지**(기존 9종목 행도 채택 북이 아닌 per-cell "
        "표라 값은 같지만, 표 전체의 좌표가 다르다). 펀딩은 12종목 전부 자기 확정 데이터가 "
        "창을 덮어(DOGE·LINK·LTC는 WAN-292 백필, ADA·DOT·BCH는 상장 초기부터 수집분) "
        "**대리 각주가 비어 있는 것이 정상**이다. 근거·파급은 "
        "[`docs/decisions/wan307.md`](../../docs/decisions/wan307.md).",
        "",
        "> 🔁 **WAN-252(2h 작업 TF 승격)로 이 표에 2h 행이 더해졌다 — 좌표 재-베이스라인**"
        "(사용자 결정 2026-08-05, WAN-182의 4h 승격과 같은 패턴). 작업 TF가 15m·1h·4h → "
        "**15m·1h·2h·4h**가 됐다(2h는 저장된 1h에서 무손실 리샘플로 파생, WAN-24 — 사전 적재 "
        "불필요). 15m/1h/4h 행은 좌표가 같아 **비트 단위로 그대로**이고 2h 9행만 새로 얹혔다. "
        "근거는 WAN-236(2h 성능)·WAN-243(2h 재진입 널)이 이미 낸 표이며 `ConfluenceParams()`는 "
        "그대로다. 근거·파급은 "
        "[`docs/decisions/wan252.md`](../../docs/decisions/wan252.md).",
        "",
        "> ⚠️ **이 표는 WAN-100(첫 탭 면제 배선)으로 전면 재산출됐다.** 최초 WAN-95 산출 "
        "당시 지정가(B안) 경로는 `tap_index`를 읽지 않아 「첫 탭은 RSI 무관 무조건 진입」"
        "(WAN-81)이 통째로 빠져 있었다 — 첫 탭에도 재탭용 RSI 게이트(롱 `RSI<=30`)가 "
        "걸려 명세보다 **빡빡한** 규칙으로 돌았다. 고친 결과 지정가 체결률이 약 29% → 약 "
        "50%로 오르고 수익률이 크게 늘었다.",
        "",
        "> 🔁 **WAN-112(오프셋 2bp 채택)로 `zone_limit` 행이 다시 전면 재산출됐다.** 채택 "
        "기본 `zone_limit_offset_bps`가 0.0 → **2.0(2bp)** 이 되면서 지정가를 존 근단보다 "
        "2bp 체결 쉬운 쪽(롱=위)에 건다 — 진입가가 밀리면 1R이 커지고 고정 1.5R 익절 목표도 "
        "그만큼 멀어지므로 **모든 `zone_limit` 셀이 이동**했다. ⚠️ 2bp는 "
        "**데이터가 고른 값이 아니라 사용자 판단**이다 — 순수 수익으로는 0bp가 이기고(WAN-104 "
        "IS 심볼평균 −0.43%p), 2bp는 그 대가로 체결 신뢰성을 산다. 근거·트레이드오프는 "
        "[`docs/decisions/wan112.md`](../../docs/decisions/wan112.md).",
        "",
        "> 🔁 **WAN-123(재탭 RSI 게이트 제거)으로 `zone_limit` 행이 다시 전면 재산출됐다.** "
        "채택 기본 `rsi_gate_mode`가 `first_tap_free` → "
        "**`unconditional`**(게이트 자체가 없음)이 되면서 재탭에 걸리던 RSI 조건(롱 "
        "`RSI<=30`)이 사라졌다. 오프셋(WAN-112)이 **같은 거래의 가격**을 옮긴 것과 달리 이건 "
        "**거래 집합 자체를 넓힌다**(이 표 기준 지정가 거래 +19~27%). "
        "⚠️ **체결률이 약 51% → 약 81%로 뛴 것이 게이트의 몫**이다: "
        "WAN-114 분해가 예고한 대로(존-단독 100% → RSI 게이트가 62%로 → 볼린저가 51%로) "
        "'체결률 ~50%'는 시장이 아니라 **규칙 층이 만든 값**이었고, 그중 게이트 몫이 여기서 "
        "돌아왔다. ⚠️ **이 표의 옛 수치와의 차이를 게이트 효과로만 읽지 말 것** — `--years 3` "
        "창은 마지막 봉 기준이라 실행 시각이 다르면 창도 미끄러진다(CLAUDE.md). 게이트만의 "
        "격리된 기여는 창을 못 박은 WAN-114 사다리(`L0r→L1`)가 낸다. 근거·파급은 "
        "[`docs/decisions/wan123.md`](../../docs/decisions/wan123.md).",
        "",
        "> 🔁 **WAN-132(진입가 정본을 봉내 라이브 밴드로)로 `zone_limit` 행이 다시 전면 "
        "재산출됐다.** 채택 기본 `band_bar`가 `tap`(탭 봉 최종 종가) → **`intrabar_live`**"
        "(체결 순간의 현재가)로 바뀌었다 — 옛 밴드는 **그 봉이 어떻게 끝날지 알아야 나오는 "
        "가격**이라 실거래에서 재현할 수 없었고(룩어헤드), 새 밴드는 사용자가 트레이딩뷰에서 "
        "보고 지정가를 거는 바로 그 값이다. **최적화가 아니라 정확성**이다. 밴드가 봉 안에서 "
        "계속 움직이므로 지정가도 **매 서브스텝 재산정**되어 진입가·1R·고정 1.5R 목표가 모두 "
        "이동한다. ⚠️ **「수익이 올라서 바꿨다」로 인용 금지**(WAN-119/120 실측: 15m "
        "OOS +0.50%p지만 **1h OOS는 −1.62%p**) · ⚠️ **「엣지 없음」(WAN-84/88/111/114/124)은 "
        "그대로**다. 근거·파급은 "
        "[`docs/decisions/wan132.md`](../../docs/decisions/wan132.md).",
        "",
        "> 🔁 **WAN-159(존폭 필터를 채택 기본값으로)로 `zone_limit` 행이 다시 전면 "
        "재산출됐다.** 채택 기본 `max_zone_width_atr`가 `None`(꺼짐, 전부 매매) → **`1.28`**"
        "(존폭 ÷ ATR ≤ 1.28인 좁은 존만 매매)이 되면서 진입 **후보 집합 자체가 3분의 1로 "
        "줄었다** — 진입 방식·가격이 아니라 **어떤 셋업을 버리는가**가 바뀌었으므로 거래 수·"
        "승률·MDD가 모두 이동한다. ⚠️ **「켜면 돈을 더 번다」로 인용 금지** — "
        "문턱 1.28은 데이터가 아니라 사용자 판단(측정 권고 15m 1.24 · 1h 1.32의 중간)이고, "
        "승률 상승의 과반은 기하(1R이 좁아져 고정 1.5R 익절이 가까워짐)이며, TRX 15m은 "
        "손절폭 가드에 잘려 사실상 매매가 멈춘다 · ⚠️ **「엣지 없음」(WAN-84/88/111/114/124/"
        "151)은 그대로**다. 근거·경고는 "
        "[`docs/decisions/wan159.md`](../../docs/decisions/wan159.md).",
        "",
        "## 무엇이 바뀌었나",
        "",
        "채택 기본값(`ConfluenceParams()`)의 진입 방식을 **종가 시장가 → 존 근단 지정가"
        "(`zone_limit`)** 로 바꾸고, 지정가 진입에 **메이커 수수료(2bp)** 를 배선했다. "
        "사용자의 실매매가 지정가이므로, 이전 손익표(`wan87_long_only_summary.md`, "
        "WAN-91 펀딩 리포트 등)는 **사용자가 하지 않는 매매**의 성과였다. 채택 지정가 진입의 "
        "왕복 비용은 **0.11%**(메이커 2bp + 슬리피지 0 진입 · 테이커 4bp + 슬리피지 5bp "
        "청산)로, 옛 종가 진입의 0.18%보다 낮다 — 대신 **미체결 위험**이 생긴다(아래 체결률).",
        "",
        "## 방법론",
        "",
        "- **평가 창**: 지정가(B안)는 1분봉이 커버하는 구간의 셋업만 평가한다"
        "(`zone_limit_backtest`). 상위TF 히스토리 전체는 오더블록 탐지·지표 워밍업에 쓰고, "
        "성과 집계만 창으로 한정한다.",
        "- **펀딩비**: `FundingRateStore.get_rates`로 조회해 엔진에 명시적으로 전달했다. "
        "`funding_coverage` 열이 실제 반영 비율이다.",
        "- **롱 온리**(WAN-87) 유지. 전략 파라미터 탐색·최적화는 하지 않았다 — 채택 "
        "기본값(`ConfluenceParams()`) 그대로다.",
        "",
        "## 심볼 × TF 손익표",
        "",
        "| 심볼 | TF | 진입 | 거래수 | 승률 | total_return | MDD | PF | 체결률 |",
        "| -- | -- | -- | --: | --: | --: | --: | --: | --: |",
    ]
    for _, row in frame.iterrows():
        fill = _fmt_pct(row["fill_rate"]) if pd.notna(row.get("fill_rate")) else "—"
        pf = f"{row['profit_factor']:.2f}" if pd.notna(row["profit_factor"]) else "n/a"
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['entry_mode']} | "
            f"{int(row['num_trades'])} | {_fmt_pct(row['win_rate'])} | "
            f"{_fmt_pct(row['total_return'])} | {_fmt_pct(row['max_drawdown'])} | "
            f"{pf} | {fill} |"
        )
    lines += [""]
    lines += _verdict_lines(frame)
    lines += ["## 펀딩 커버리지", ""]
    if proxy_note:
        lines += [f"> ⚠️ {proxy_note}", ""]
    lines += [
        "| 심볼 | TF | 진입 | 펀딩 비용 | 커버리지 |",
        "| -- | -- | -- | --: | --: |",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['entry_mode']} | "
            f"{row['total_funding_cost']:.2f} | {_fmt_pct(row['funding_coverage'])} |"
        )
    return "\n".join(lines) + "\n"


def _reconstruct_proxy_note(symbols: tuple[str, ...], start: str, end: str, db_path: str) -> str:
    """`--from-csv` 재생성용 — 백테스트 없이 펀딩 테이블만 읽어 대리 각주를 복원한다.

    `collect_rows`와 같은 도너 규칙(`DEFAULT_SYMBOLS`의 비-신규 종목)을 쓴다. 각주 문구는
    `apply_funding_proxy`가 정하므로 커밋본과 동일하게 복원된다(펀딩 조회는 백테스트가 아님).
    """
    funding_store = FundingRateStore(db_path)
    start_ms = parse_date_ms(start)
    end_ms = parse_date_ms(end)
    donor_candidates = tuple(s for s in DEFAULT_SYMBOLS if s not in NEW_SYMBOLS)
    query_symbols = list(dict.fromkeys([*donor_candidates, *symbols]))
    funding_by_symbol = {
        s: funding_store.get_rates(s, start_ms=start_ms, end_ms=end_ms, include_predicted=True)
        for s in query_symbols
    }
    _, note = apply_funding_proxy(funding_by_symbol)
    return note


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-95 지정가(B안) 채택 성과 재산출")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_TIMEFRAMES))
    parser.add_argument("--start", default=DEFAULT_START, help="채택 창 시작(YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="채택 창 끝(YYYY-MM-DD)")
    parser.add_argument("--db-path", default=_DB_PATH)
    parser.add_argument("--cache-dir", default=_CACHE_DIR)
    parser.add_argument("--out-dir", default="backtest/reports")
    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="백테스트를 다시 돌리지 않고 커밋된 recompute.csv에서 요약(md)만 재생성한다. "
        "펀딩 대리 각주는 펀딩 테이블만 읽어 복원한다(백테스트 없음).",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "wan95_zone_limit_recompute.csv"
    md_path = out_dir / "wan95_zone_limit_summary.md"

    if args.from_csv:
        frame = pd.read_csv(csv_path)
        proxy_note = _reconstruct_proxy_note(
            tuple(args.symbols), args.start, args.end, args.db_path
        )
    else:
        rows, proxy_note = collect_rows(
            symbols=tuple(args.symbols),
            timeframes=tuple(args.timeframes),
            start=args.start,
            end=args.end,
            db_path=args.db_path,
            cache_dir=args.cache_dir,
        )
        frame = rows_to_frame(rows)
        frame.to_csv(csv_path, index=False)
        print(f"[wan95] 저장: {csv_path}")

    md_path.write_text(build_markdown(frame, proxy_note), encoding="utf-8")
    print(f"[wan95] 저장: {md_path}")


if __name__ == "__main__":
    main()
