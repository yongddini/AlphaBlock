"""CLI ↔ 기존 리포트 회귀 검증 (WAN-101 완료기준).

`python -m backtest.run`의 **기본값 실행**이 WAN-95/99 리포트의 해당 셀과 숫자까지
일치하는지 실데이터로 확인한다 — "엔진이 조용히 달라지지 않았음"의 직접 증거다.
`tests/test_harness.py`는 같은 보장을 파라미터·호출 경로 수준에서 CI 안전하게 고정하고,
이 파일은 그 위에 **실제 산출 숫자**를 얹는다.

실데이터(`data/ohlcv.db`, 약 580MB)는 저장소에 없으므로 CI에서는 통째로 skip된다.
로컬에 데이터가 있으면 자동으로 돈다. 비용을 감당 가능하게 두려고 심볼 1개 × TF 1개로
좁혔다 — 전 격자 대조는 아래 재현 커맨드로 수행한다:

```
python -m backtest.run --symbol BTCUSDT,ETHUSDT,SOLUSDT --tf 1h --fill pen_5bp --format csv
```

⚠️ skip 판정은 **파일 존재가 아니라 실제 데이터 유무**로 한다. `data/ohlcv.db` 파일이
있다고 봉이 들어 있는 건 아니다 — `OhlcvStore.__init__`이 `sqlite3.connect`로 빈 DB를
스키마만 만들어 놓고, 실제로 `dashboard.app` 임포트(= `tests/test_dashboard_app.py`
수집)만으로도 그 빈 파일이 생긴다. 그 모듈이 이 모듈보다 알파벳순으로 앞이라, 파일
존재로 판정하면 CI에서 skip이 안 걸리고 "0행" 실패가 난다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from backtest.harness import (
    LEGACY_BAND_BAR,
    LEGACY_COMBINE_OBS,
    LEGACY_RSI_GATE_MODE,
    LEGACY_TAKE_PROFIT_LIQUIDITY,
    RunRow,
    build_config,
    load_market_data,
)
from backtest.models import BacktestConfig
from backtest.run import (
    JOBS_AUTO,
    RunOptions,
    build_parser,
    grid_from_args,
    parse_date_ms,
    run_grid,
)
from backtest.zone_limit_backtest import build_zone_limit_candidates
from common.costs import Liquidity
from strategy.models import BandBar, ConfluenceParams, DeviationFilterParams, RsiGateMode
from strategy.realtime_band import RealtimeBand

_WAN99_CSV = Path("backtest/reports/wan99_zone_limit_offset.csv")

#: 대조 심볼·TF. 1분봉 로딩이 실행 시간을 지배하므로 1셀로 좁힌다.
_SYMBOL = "BTC/USDT:USDT"
_TIMEFRAME = "1h"
_YEARS = 3.0

#: **못 박은 대조 창(WAN-162)**. `--years 3`은 마지막 봉 기준으로 창을 자르므로 데이터가
#: 쌓이면 창이 미끄러지고(`harness.load_recent`), 옛 리포트 셀과의 대조가 날짜에 따라
#: 어긋난다 — 실제로 WAN-99 셀의 `eligible_setups` 분모가 창 경계에서 598→595로 밀려
#: 실패했다(나머지 지표는 전부 비트 단위로 일치 = **순수 창 미끄러짐, 엔진 무변**). 저장소
#: 표준 창(2023-07-15~2026-07-15, WAN-111 이래)으로 못 박으면 두 경계 모두 과거라 새 봉이
#: 들어와도 움직이지 않고, WAN-99 **동결** CSV가 비트 단위로 재현된다. 창을 바꾸면 옛 셀과
#: 안 맞으니 `--start`/`--end`로 못 박은 것이지 임의 값이 아니다.
_START = "2023-07-15"
_END = "2026-07-15"
_START_MS = parse_date_ms(_START)
_END_MS = parse_date_ms(_END)

#: `--jobs` 대조용(WAN-121). fan-out 단위가 (심볼, TF)라 **심볼이 2개는 돼야** 워커가
#: 실제로 둘로 갈린다(1셀이면 `resolve_jobs`가 1로 접어 직렬과 같은 경로를 탄다 —
#: 그러면 "병렬이 같다"를 증명한 게 아니라 병렬을 안 돈 것이다).
_JOBS_SYMBOLS = "BTCUSDT,ETHUSDT"
#: 병렬 대조는 리포트 셀과 맞출 필요가 없으므로 창을 좁혀 비용을 줄인다(실데이터 유지).
_JOBS_YEARS = 0.5


@pytest.fixture(autouse=True)
def _require_real_data() -> None:
    """대조 심볼의 봉이 실제로 있을 때만 돈다(없으면 skip).

    1분봉은 읽지 않는다 — 존재 확인에 수백 MB를 읽을 이유가 없다.
    """
    market = load_market_data(
        _SYMBOL, _TIMEFRAME, start_ms=_START_MS, end_ms=_END_MS, need_1m=False, funding=False
    )
    if market.empty:
        pytest.skip(f"{_SYMBOL} {_TIMEFRAME} 실데이터가 없어 회귀 대조를 건너뜁니다(CI 기본).")


def _run(
    argv: list[str],
    *,
    rsi_gate_mode: RsiGateMode | None = None,
    band_bar: BandBar | None = None,
    combine_obs: bool | None = None,
    take_profit_liquidity: Liquidity | None = None,
) -> RunRow:
    """CLI 인자로 한 셀을 돌려 그 행을 낸다.

    데이터 유무는 픽스처가 이미 확인했으므로, 여기서 0행이 나오면 그건 진짜 배선 버그다.

    `rsi_gate_mode`(WAN-123)·`band_bar`(WAN-132)·`take_profit_liquidity`(WAN-370)는 CLI 축이
    아니라 핀이라 인자로 못 준다 — 옛 리포트 셀과 대조할 때만 여기서 되돌려 요청한다.
    """
    grid = grid_from_args(build_parser().parse_args(argv))
    if rsi_gate_mode is not None:
        grid = replace(grid, rsi_gate_mode=rsi_gate_mode)
    if band_bar is not None:
        grid = replace(grid, band_bar=band_bar)
    if take_profit_liquidity is not None:
        grid = replace(grid, take_profit_liquidity=take_profit_liquidity)
    if combine_obs is not None:
        grid = replace(grid, combine_obs=(combine_obs,))
    # 창을 못 박는다(WAN-162) — `start_ms`/`end_ms`가 있으면 `load_market_data`가 `_YEARS`
    # 대신 이 창을 쓴다. 날짜가 지나도 대조가 흔들리지 않게 하는 핵심이다.
    rows = run_grid(grid, RunOptions(years=_YEARS, start_ms=_START_MS, end_ms=_END_MS), log=False)
    assert len(rows) == 1, f"대조는 한 셀이어야 합니다: {len(rows)}행"
    return rows[0]


def _report_cell(csv: Path, **filters: object) -> pd.Series:
    frame = pd.read_csv(csv)
    for column, value in filters.items():
        frame = frame[frame[column] == value]
    assert len(frame) == 1, f"{csv.name}에서 셀을 특정하지 못했습니다: {filters} → {len(frame)}행"
    return frame.iloc[0]


def _assert_matches(row: RunRow, cell: pd.Series, columns: list[str]) -> None:
    for column in columns:
        actual = getattr(row, column)
        assert actual == pytest.approx(float(cell[column]), abs=1e-9), (
            f"{column}: CLI {actual} != 리포트 {cell[column]}"
        )


#: 현행 채택 기본값(지정가 + 롱 온리 + 1.5R + 오프셋 2bp + intrabar_live + unconditional +
#: combine_obs=False + max_zone_width_atr=1.28) 셀을 **못 박은 창에서** 산출한 기준값.
#:
#: ⚠️ 여기만 CSV 셀이 아니라 상수를 쓰는 이유: `wan95_zone_limit_recompute.csv`는 재-베이스
#: 라인마다 `--years 3`으로 재산출되는 **살아있는** 문서라(WAN-159가 마지막) 그 창이 "지금"을
#: 따라다녀 **날짜 독립 참조가 못 된다**(오늘 창에서 117거래, 못 박은 창에서 118거래로 경계
#: 1거래가 갈린다). 그래서 CSV 대신 못 박은 창의 값을 참조로 고정한다.
#:
#: 🚨 이 상수는 "실제값을 기대값으로 덮어쓴" 것이 아니다 — 원인이 **창 미끄러짐**임을 아래
#: WAN-99 테스트가 **같은 창에서 동결 CSV를 비트 단위로 재현**해 증명한다(= 엔진 무변). 값을
#: 다시 낼 때는 그 WAN-99 재현이 먼저 통과하는지 확인할 것(엔진이 실제로 바뀌었다면 WAN-99도
#: 같이 깨진다).
#: 🔁 **WAN-365(소급 취소 → 인과)로 값이 통째로 움직였다 — 그게 맞는 숫자다.** 무효화 봉에서
#: 난 탭이 후보로 살아나므로 거래가 **118 → 182(+54%)**, 승률 **57.63% → 43.41%**,
#: total_return **+21.26% → −13.43%**, MDD **5.09% → 17.49%**, 체결률 **67.98% → 83.28%**.
#: 되살아난 거래는 대부분 손절이다(WAN-364 §4-2: 88.3%).
#:
#: 🚨 **이 값을 「실제값으로 기대값을 덮어쓴 것」으로 읽지 말 것** — 같은 실행에서 아래 WAN-99
#: 테스트가 **동결 CSV를 비트 단위로 그대로 재현**한다(`--invalidation-cancel bar_open`으로 옛
#: 엔진을 요청해서). 즉 엔진은 **이 축 말고는 조용히 달라지지 않았다**. 그 대조가 이 상수를
#: 다시 낸 근거다(위 문단의 규약 그대로: WAN-99가 먼저 통과해야 이 값을 갱신한다).
#: 🔁 **WAN-370(익절 테이커 → 메이커)로 손익 열만 움직였다 — 그게 맞는 숫자다.** 익절 청산이
#: 메이커 2bp·슬리피지 0으로 값매김되면서 total_return **−13.43% → −8.58%**, MDD **17.49% →
#: 14.78%**. 거래 수(182)·승률(43.41%)·체결률(83.28%)은 **비트 그대로**다 — 비용은 후보
#: 집합도 승패도 안 바꾸고 이긴 거래의 크기만 키운다(같은 실행에서 WAN-99 테스트가
#: `take_profit_liquidity=LEGACY_TAKE_PROFIT_LIQUIDITY` 핀으로 동결 CSV를 비트 재현 — 엔진은
#: 이 축 말고는 조용히 달라지지 않았다).
#: 🔁 **WAN-384(존폭 필터 폐지)로 값이 통째로 움직였다 — 그게 맞는 숫자다.** 좁은 존만
#: 매매하던 필터(1.28)를 끄면서 후보가 넓어져 거래 **182 → 357(+96%)**, 체결률 **83.28% →
#: 83.54%**, 승률 **43.41% → 41.46%**, total_return **−8.58% → −20.75%**, MDD **14.78% →
#: 27.87%**. 필터를 켜 두면 거래당 손실은 조금 작지만(WAN-378: −0.1309R vs −0.1207R) **거래
#: 수를 36% 줄여 복리 손실 총액이 작아 보이던 것**이고, 판정은 거래당 net R로 낸다(WAN-341).
#:
#: 🚨 **이 값도 「실제값으로 기대값을 덮어쓴 것」이 아니다** — 같은 실행에서 아래 WAN-99
#: 테스트가 **동결 CSV를 비트 단위로 재현**한다(그쪽은 이 축을 `max_zone_width_atr=None`으로
#: 이미 명시했고, WAN-159 이전 엔진이라 이 전환에 무영향이다). 즉 엔진은 이 축 말고는 조용히
#: 달라지지 않았다.
_WAN95_PINNED = {
    "total_return": -0.2075424224059141,
    "win_rate": 0.41456582633053224,
    "max_drawdown": 0.2786699692055053,
    "num_trades": 357,
    "fill_rate": 0.8354114713216958,
}


def test_cli_defaults_reproduce_wan95_zone_limit_cell() -> None:
    """채택 **단일 포지션** 엔진(per-cell) == 못 박은 창의 WAN-95 기준값.

    이 테스트가 채택 per-cell 엔진을 실데이터로 도는 유일한 축이다 — 아래 WAN-99 테스트는
    옛 엔진을 핀(오프셋 0 · tap 밴드 · first_tap_free · 병합 존)으로 되돌려 돌기 때문이다.

    ⚠️ **WAN-213부터 인자 없는 `backtest.run`(main)은 레버리지 북을 돈다** — 이 셀의 단일
    포지션 손익은 이제 `--positions single` 경로다. `_run`이 `run_grid`(per-cell)를 직접
    부르므로 이 대조는 그 단일 포지션 엔진을 그대로 고정한다(북 기본값 전환에 영향받지 않는다).
    북이 채택 경로를 실제로 타는지는 `tests/test_book_cli.py`가 동작으로 고정한다.
    """
    row = _run(["--symbol", "BTCUSDT", "--tf", _TIMEFRAME, "--positions", "single"])
    for column, expected in _WAN95_PINNED.items():
        actual = getattr(row, column)
        assert actual == pytest.approx(float(expected), abs=1e-9), (
            f"{column}: CLI {actual} != 못 박은 창 기준 {expected}"
        )


@pytest.mark.skipif(not _WAN99_CSV.exists(), reason="WAN-99 리포트 CSV 없음")
def test_cli_reproduces_wan99_pen_5bp_cell() -> None:
    """`--fill pen_5bp --offset-bps 0` == WAN-99의 (full, 오프셋 0, pen_5bp) 셀.

    `mean_r`·`fill_rate`까지 맞아야 한다 — 거래 수만 같고 손익이 다르면 비용·사이징
    배선이 갈린 것이고, 그건 표를 나란히 읽는 순간 드러나지 않는 종류의 오차다.

    ⚠️ **못 박은 창에서 돈다**(WAN-162, `_run`이 `_START`/`_END`로 고정): WAN-99 CSV는
    다시 산출되지 않는 **동결** 아티팩트라, 저장소 표준 창으로 못 박으면 `fill_rate`
    분모(`eligible_setups`)까지 비트 단위로 재현된다. `--years 3`으로 두면 데이터가 쌓일
    때마다 그 분모가 창 경계에서 밀려 이 셀만 어긋난다(그게 WAN-162가 고친 실패다).
    이 테스트가 통과한다는 것은 곧 "엔진이 조용히 달라지지 않았다"의 직접 증거이므로,
    위 WAN-95 상수(같은 창)의 신뢰 근거이기도 하다.

    ⚠️ `--offset-bps 0`을 **명시**해야 한다(WAN-112): 채택 기본 오프셋이 2bp가 되면서
    CLI 기본 실행은 더 이상 WAN-99의 오프셋 0 셀이 아니다. 이 인자가 "옛 셀과 대조하려고
    옛 엔진을 요청한다"는 사실을 드러낸다 — 빼면 다른 엔진의 숫자를 같은 셀로 착각한다.

    ⚠️ 같은 이유로 **RSI 게이트도 옛 값으로 되돌려 요청한다**(WAN-123): 채택 기본값이
    `unconditional`(게이트 제거)이 되면서 CLI 기본 실행의 거래 집합이 13~14% 넓어졌다.
    게이트는 격자 축이 아니라 핀이므로 CLI 플래그가 없다 — `Grid.rsi_gate_mode`로 직접
    요청한다(그쪽 필드가 존재하는 이유가 이것이다).

    ⚠️ **밴드 표본도 마찬가지다**(WAN-132): 채택 기본값이 `intrabar_live`(봉내 라이브)가
    되면서 진입가가 서브스텝마다 재산정된다. WAN-99 격자는 탭 봉 종가 밴드에서 나왔으므로
    `Grid.band_bar`로 되돌려 요청한다.

    ⚠️ **존 병합도 되돌린다**(WAN-149): 채택 기본값이 `combine_obs=False`(원본 존 단위
    분리)가 되면서 존 집합 자체가 달라졌다 — 겹치는 존이 접히지 않으니 진입 후보가 늘고
    손절 거리(1R)가 좁아진다. WAN-99 격자는 병합 엔진에서 나왔으므로 되돌려 요청한다.
    이쪽은 **축이라 CLI 플래그(`--combine-obs`)가 있지만**, 이 대조는 "옛 엔진을 요청한다"는
    사실이 코드에 드러나야 하므로 다른 두 핀과 같은 자리에서 명시한다.

    ⚠️ **무효화 취소 시점도 되돌린다**(WAN-365): 채택 기본값이 `bar_close`(인과)가 되면서
    **무효화 봉에서 난 탭이 후보로 살아난다**. WAN-99 격자는 소급 취소 엔진에서 나왔으므로
    `--invalidation-cancel bar_open`으로 명시 요청한다(축이라 CLI 플래그가 있다).
    """
    # ⚠️ **존폭 필터도 되돌린다**(WAN-159): 채택 기본값이 `max_zone_width_atr=1.28`(좁은 존만
    # 매매)이 되면서 후보 집합이 3분의 1로 줄었다. WAN-99 격자는 필터 꺼진 엔진에서 나왔으므로
    # `--max-zone-width-atr none`으로 끄기를 명시 요청한다(축이라 CLI 플래그가 있다).
    row = _run(
        [
            "--symbol",
            "BTCUSDT",
            "--tf",
            _TIMEFRAME,
            "--fill",
            "pen_5bp",
            "--offset-bps",
            "0",
            "--max-zone-width-atr",
            "none",
            "--invalidation-cancel",
            "bar_open",
        ],
        rsi_gate_mode=LEGACY_RSI_GATE_MODE,
        band_bar=LEGACY_BAND_BAR,
        combine_obs=LEGACY_COMBINE_OBS,
        # ⚠️ **익절 비용도 되돌린다**(WAN-370): 채택 기본값이 「익절 = 지정가 메이커 2bp」가
        # 되면서 같은 거래의 손익이 달라진다. WAN-99 격자는 익절도 테이커이던 엔진에서
        # 나왔으므로 되돌려 요청한다(축이 아니라 핀이라 CLI 플래그가 없다).
        take_profit_liquidity=LEGACY_TAKE_PROFIT_LIQUIDITY,
    )
    cell = _report_cell(
        _WAN99_CSV,
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        segment="full",
        offset_bps=0.0,
        assumption="pen_5bp",
    )
    _assert_matches(
        row,
        cell,
        [
            "total_return",
            "win_rate",
            "max_drawdown",
            "num_trades",
            "fill_rate",
            "mean_r",
            "sharpe",
        ],
    )


def _jobs_rows(jobs: int) -> list[RunRow]:
    grid = grid_from_args(
        build_parser().parse_args(["--symbol", _JOBS_SYMBOLS, "--tf", _TIMEFRAME])
    )
    return run_grid(grid, RunOptions(years=_JOBS_YEARS), log=False, jobs=jobs)


def test_jobs_does_not_change_real_data_numbers() -> None:
    """완료기준(WAN-121): `--jobs`가 **실데이터 숫자**를 바꾸지 않는다.

    합성 데이터 대조(`tests/test_run_cli.py`)는 배선·순서·pickle을 잡지만, 지정가 팔이
    0거래라 손익까지는 못 잰다(볼린저 기본 필터가 합성 후보를 전부 거른다). 실데이터는
    체결·손익·펀딩이 전부 흐르는 유일한 축이라, 병렬이 숫자를 흔드는지는 **여기서만**
    진짜로 증명된다. `--jobs`는 성능 노브이지 결과 축이 아니다.
    """
    serial = _jobs_rows(1)
    assert len(serial) == 2, f"2심볼 대조가 아니다: {len(serial)}행"
    assert any(row.num_trades > 0 for row in serial), "실데이터가 거래를 내지 않았다"

    for jobs in (2, JOBS_AUTO):
        parallel = _jobs_rows(jobs)
        assert [r.model_dump() for r in parallel] == [r.model_dump() for r in serial], (
            f"--jobs {jobs}의 결과가 직렬과 다르다"
        )


def _naive_band_seed_from_closed(
    cls: type[RealtimeBand],
    closes: Sequence[float],
    filter_params: DeviationFilterParams,
    *,
    end: int | None = None,
) -> RealtimeBand:
    """WAN-204 최적화 **이전**의 밴드 시딩 — `closes[:end]` 전체를 커밋한다(O(N×M))."""
    state = cls(filter_params=filter_params)
    seq = closes if end is None else closes[:end]
    for close in seq:
        state.commit(float(close))
    return state


def test_intrabar_live_seeding_preserves_real_filled_trades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """완료기준(WAN-204): 밴드 시딩 O(N×M)→O(1)가 **실데이터 체결 거래**를 안 바꾼다.

    합성 데이터는 볼린저 필터가 후보를 전부 걸러 `filled=0`이라(위 병렬 테스트 각주와
    같은 한계) 시딩 등가성을 체결 손익까지 증명하지 못한다. 실데이터는 채택 기본값
    (`intrabar_live` 밴드)이 실제로 수십 건을 체결하므로, 최적화판과 전체 이력 커밋
    원본이 **같은 진입/청산/사유**를 내는지를 여기서만 진짜로 못 박는다.
    """
    market = load_market_data(_SYMBOL, _TIMEFRAME, start_ms=_START_MS, end_ms=_END_MS)
    assert market.df_1m is not None and not market.df_1m.empty
    params = ConfluenceParams()  # 채택 기본값 = intrabar_live 밴드
    cfg = BacktestConfig()

    def snapshot() -> tuple[list[tuple[object, ...]], tuple[int, int, int, int]]:
        cands, stats = build_zone_limit_candidates(
            market.htf_df, market.df_1m, _TIMEFRAME, params=params, cfg=cfg
        )
        rows: list[tuple[object, ...]] = [
            (
                c.trigger_time,
                c.entry_time,
                c.entry_price,
                c.stop_price,
                c.exit_time,
                c.exit_price,
                c.reason,
                c.mfe_r,
                c.mae_r,
            )
            for c in cands
        ]
        return rows, (stats.eligible, stats.filled, stats.penetrations, stats.dropped)

    opt_rows, opt_stats = snapshot()
    assert opt_stats[1] > 0, "실데이터가 체결 거래를 내지 않았다(시딩 등가성 검증 무의미)"

    monkeypatch.setattr(RealtimeBand, "seed_from_closed", classmethod(_naive_band_seed_from_closed))
    ref_rows, ref_stats = snapshot()

    assert opt_stats == ref_stats
    assert opt_rows == ref_rows


def test_adv_cap_metadata_does_not_change_candidate_set() -> None:
    """WAN-244 완료기준 — 유동성 한도를 켜서 `adv_usd`를 계산해 실어도 **후보 집합은 그대로**다.

    상한은 사이징 시점에만 걸리므로(체결·청산 로직 무관), 상한을 켠 cfg로 후보를 지어도
    `adv_usd`만 채워지고 나머지 필드는 비트 단위로 같아야 한다 — 「상한 끔 = 옛 북 셀
    비트 재현」의 후보 생성 단위 보증(실데이터로 실제 체결 후보 위에서 확인).

    ⚠️ WAN-279가 채택 기본값을 0.005로 올린 뒤라 상한 끔 팔은 **명시적 `None`으로 고정**한다
    (build_config에 맡기면 0.005로 켜진다)."""
    market = load_market_data(_SYMBOL, _TIMEFRAME, start_ms=_START_MS, end_ms=_END_MS)
    assert market.df_1m is not None and not market.df_1m.empty
    params = ConfluenceParams()  # 채택 기본값.

    cfg_off = build_config(_TIMEFRAME, max_notional_adv_fraction=None)
    cfg_on = build_config(_TIMEFRAME, max_notional_adv_fraction=0.005)
    assert cfg_off.risk_sizing is not None and cfg_on.risk_sizing is not None

    cands_off, stats_off = build_zone_limit_candidates(
        market.htf_df, market.df_1m, _TIMEFRAME, params=params, cfg=cfg_off
    )
    cands_on, stats_on = build_zone_limit_candidates(
        market.htf_df, market.df_1m, _TIMEFRAME, params=params, cfg=cfg_on
    )
    assert stats_off.filled > 0, "실데이터가 체결 후보를 내지 않았다(검증 무의미)"
    # 상한 끔: adv_usd 전부 None(계산조차 안 함). 상한 켬: 값이 채워진다.
    assert all(c.adv_usd is None for c in cands_off)
    assert any(c.adv_usd is not None for c in cands_on)
    assert all(c.adv_usd is None or c.adv_usd > 0.0 for c in cands_on)
    # adv_usd만 다르고 나머지 필드는 전부 같다 = 후보 집합 불변.
    assert (stats_off.eligible, stats_off.filled) == (stats_on.eligible, stats_on.filled)
    assert [replace(c, adv_usd=None) for c in cands_on] == cands_off
