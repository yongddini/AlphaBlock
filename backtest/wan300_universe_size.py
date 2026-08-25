"""WAN-300 — 레버리지 북 종목 수 최적화: cap_only 5배에서 유니버스(9→12→15→19) 스윕.

사용자 질문(2026-08-13): *"5배 레버리지·9종목에서, 수익률이 비슷하다는 가정하에 종목을
더 늘리면 수익을 최대화하는 종목 수가 몇 개가 적당한가?"* — cap_only 5배 북은 한 지갑을
여러 칸(종목·TF)이 공유하고 총 명목이 자본의 5배를 못 넘으므로, 종목이 적으면 자본이
놀고(한도 미달) 많으면 새 셋업이 밀린다(cap binds). 이 표가 그 교차점을 잰다.

🚨 **"수익률이 비슷하다"는 가정은 역사적으로 깨졌다**(WAN-111: 3→6종목에서 OOS 심볼평균
+19% → +3.9%). 이 측정의 본질은 「종목 늘림의 용량 이득」이 「종목 희석 손실」을 이기는
구간이 있는가다 — 늘리면 좋다를 전제하지 않는다.

## 후보 종목 (§1 프로브 · 2026-08-14 실측)

이슈가 지목한 후보 11종목 중 **EOS는 바이낸스 USDT 퍼프 마켓이 없어(상폐) 제외**, 나머지
10종목은 전부 상장일이 창 하한(2020-09-15) 이전이고 창 상한(2026-07-22)까지 데이터가
있다(같은 날 1m 6년 백필 + `data.aggregate` native TF 빌드 + WAN-292 패턴 펀딩 백필 완료).
사다리 합류 순서는 **못 박은 창의 일중 달러 거래대금 중앙값(ADV) 내림차순**으로 고정한다
(`CANDIDATE_SYMBOLS` — 얇은 종목일수록 "닿으면 체결" 낙관이 안 맞으므로 유동성 순).
측정·동결 근거는 요약 md의 ADV 표(재계산: `rank_candidates_by_adv`).

## 기계 재사용 (새 파이프라인 금지 · WAN-180/301 패턴)

* **셀은 한 번만 계산하고 북을 조립한다** — `wan169.run_cells`(cold_segments=False ·
  engine_check는 기준 렌즈만, WAN-301 컴퓨트 노브)로 19종목 × TF 셀을 렌즈당 한 번 내고,
  유니버스 크기는 그 payload의 **부분집합 선택**이다(크기마다 재계산 금지).
* **북 = 채택 스냅샷**(cap_only 5배 · 재진입 없음 — wan288/293/301과 같은 구성·같은 이유:
  재진입은 별도 축). `LeverageBookParams(leverage_multiple=5, leverage_mode="cap_only")`
  명시 핀(WAN-213 — 기본값이 또 이동해도 이 CSV가 조용히 다른 북으로 돌지 않게).
* **정본 OOS = oos_warm**(WAN-166) + full 병기. 차가운 절단(is/oos)은 생성조차 안 한다.
* **렌즈 = baseline + pen_5bp**(완료기준 3 — 용량 이득이 낙관 렌즈 산물인지).

## 밀림율(포화 지표) 정의

`skip_notional_rate = skipped_notional / (num_trades + skipped_notional)` — 칸이 비어
배치될 수 있었던 셋업 중 **북 명목 한도(자본×5)에 막혀 밀린 비율**. `skipped_cell_busy`
(칸당 1포지션 규칙에 막힌 것)는 유니버스 크기와 무관한 다른 기제라 분모에서 뺀다.

## 재현

```
uv run python -m backtest.wan300_universe_size --jobs 4            # §2: 1h·4h × 2렌즈
uv run python -m backtest.wan300_universe_size --from-csv          # 요약만 재생성
# §3(후속): 최적 근처 유니버스에서 15m 확인 — all 스코프 재계산에 3TF 한 실행 필요
uv run python -m backtest.wan300_universe_size --tf 15m,1h,4h --append --jobs 4
```
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest import harness
from backtest.harness import SEGMENT_FULL, SEGMENT_OOS_WARM
from backtest.leverage_book import LeverageBookParams, run_leverage_book
from backtest.sweep import timeframe_to_ms
from backtest.wan169_leverage_book import (
    BOOK_ANNUALIZATION_TF,
    CellPayload,
    CellRow,
    _segment_cells,
    _short,
    run_cells,
    verify_cells,
)
from backtest.wan176_nine_symbol_rebaseline import DEFAULT_END, DEFAULT_START, NINE_SYMBOLS
from backtest.wan180_leverage_book_nine import _mean_confirmed_rate, _reprice_with_funding
from backtest.zone_limit_backtest import build_result_from_trades
from data.models import FundingRate
from data.storage import OhlcvStore

REPORTS_DIR = Path("backtest/reports")
DEFAULT_CELLS_CSV = REPORTS_DIR / "wan300_universe_cells.csv"
DEFAULT_GRID_CSV = REPORTS_DIR / "wan300_universe_grid.csv"
DEFAULT_SUMMARY = REPORTS_DIR / "wan300_universe_size_summary.md"

#: 렌즈 — baseline(공식) + pen_5bp(체결 보수화, 완료기준 3). 탈락 렌즈는 이 표의 질문
#: (용량 vs 희석)과 직교라 안 돈다.
DEFAULT_LENSES: tuple[str, ...] = ("baseline", "pen_5bp")
REF_LENS = "baseline"

#: 기본 TF = 1h·4h(이슈 §2 — 방향 먼저). 15m은 셀당 무거우니(~37분, WAN-203) §3에서
#: 최적 근처 유니버스만 `--append`로 잇는다(WAN-301→302 패턴).
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("1h", "4h")

#: 측정 구간 — oos_warm(정본, WAN-166) + full. 차가운 절단(is/oos)은 이 표가 안 쓰므로
#: 생성조차 안 한다(`run_cells(cold_segments=False)`, WAN-301 컴퓨트 노브).
MEASURED_SEGMENTS: tuple[str, ...] = (SEGMENT_OOS_WARM, SEGMENT_FULL)

#: 채택 북 명시 핀(WAN-213) — 클래스 기본값에 맡기지 않는다.
ADOPTED_MULTIPLE = 5.0
ADOPTED_MODE = "cap_only"

#: §1 프로브(2026-08-14)에서 자격 미달로 제외된 후보와 사유 — 요약에 그대로 적는다.
EXCLUDED_CANDIDATES: dict[str, str] = {
    "EOSUSDT": "바이낸스 USDT 퍼프 마켓 없음(상폐) — 2026-08-14 프로브",
}

#: 자격 통과 후보 10종목 — **못 박은 창(2020-09-15~2026-07-22) 일중 달러 거래대금 중앙값
#: 내림차순으로 동결**(측정: `rank_candidates_by_adv`, 값은 요약 md의 ADV 표).
#: 사다리 합류는 이 순서다: 12종목 = 9 + 앞 3, 15종목 = + 다음 3, 19종목 = + 나머지 4.
#: ⚠️ 자유 파라미터가 아니라 유동성 규칙이다(얇은 종목일수록 baseline 낙관이 안 맞는다,
#: 이슈 후보 선정 원칙과 같은 자) — 데이터가 바뀌어도 이 상수를 조용히 따라 움직이지
#: 않는다(재동결은 명시적 재측정).
CANDIDATE_SYMBOLS: tuple[str, ...] = (
    # 실측 ADV(2026-08-14, 백만$): ADA 329 · DOT 168 · BCH 145 · ETC 119 · ATOM 75 ·
    # XLM 56 · ALGO 39 · XMR 32.3 · THETA 32.2 · VET 27.
    "ADAUSDT",
    "DOTUSDT",
    "BCHUSDT",
    "ETCUSDT",
    "ATOMUSDT",
    "XLMUSDT",
    "ALGOUSDT",
    "XMRUSDT",
    "THETAUSDT",
    "VETUSDT",
)

#: 유니버스 사다리 — 9(채택) → 12 → 15 → 19(자격 후보 전부). 이슈의 20은 EOS 제외로 19다.
UNIVERSE_SIZES: tuple[int, ...] = (9, 12, 15, 19)

ALL_SYMBOLS: tuple[str, ...] = NINE_SYMBOLS + CANDIDATE_SYMBOLS

#: 잔존/배율 계산에서 0으로 나누지 않기 위한 하한(WAN-115 부호 함정 가드).
_RATIO_EPS = 1e-9


def universe_symbols(size: int) -> tuple[str, ...]:
    """크기 `size` 유니버스의 종목(축약형). 9는 채택 9종목, 그 위는 ADV 순 합류."""
    if size < len(NINE_SYMBOLS) or size > len(ALL_SYMBOLS):
        raise ValueError(f"유니버스 크기 {size}는 9~{len(ALL_SYMBOLS)} 밖입니다.")
    return NINE_SYMBOLS + CANDIDATE_SYMBOLS[: size - len(NINE_SYMBOLS)]


# --------------------------------------------------------------------------- #
# ADV 랭킹 (후보 순서 동결 근거 — 요약에 표로 남긴다)
# --------------------------------------------------------------------------- #


def median_daily_dollar_volume(
    store: OhlcvStore, symbol: str, *, start: str = DEFAULT_START, end: str = DEFAULT_END
) -> float:
    """못 박은 창의 일중 달러 거래대금(Σ close×volume per day) 중앙값. 데이터 없으면 0."""
    from backtest.run import parse_date_ms

    df = store.load(
        harness.normalize_symbol(symbol),
        "1h",
        start_ms=parse_date_ms(start),
        end_ms=parse_date_ms(end),
    )
    if df.empty:
        return 0.0
    dollar = df["close"].astype(float) * df["volume"].astype(float)
    day = (df["open_time"].astype("int64") // 86_400_000).astype("int64")
    daily = dollar.groupby(day).sum()
    return float(daily.median())


def rank_candidates_by_adv(
    store: OhlcvStore,
    symbols: Sequence[str] = CANDIDATE_SYMBOLS,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
) -> list[tuple[str, float]]:
    """후보를 창 내 ADV 내림차순으로 정렬해 (심볼, ADV) 목록을 낸다 — 동결 상수의 자."""
    ranked = [(s, median_daily_dollar_volume(store, s, start=start, end=end)) for s in symbols]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


# --------------------------------------------------------------------------- #
# 행 모델
# --------------------------------------------------------------------------- #


class UniverseCellRow(BaseModel):
    """렌즈 축이 붙은 셀 격리 성과 행(원자료 CSV용 — wan169 `CellRow` + lens)."""

    model_config = ConfigDict(frozen=True)

    lens: str
    symbol: str
    timeframe: str
    segment: str
    num_candidates: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    engine_total_return: float | None = None
    engine_num_trades: int | None = None

    @classmethod
    def from_cell_row(cls, row: CellRow, lens: str) -> UniverseCellRow:
        return cls(lens=lens, **row.model_dump())


class UniverseRow(BaseModel):
    """(렌즈 × 유니버스 크기 × 스코프 × 구간 [× 제외 종목])의 채택 북 성과 한 행.

    ⚠️ `total_return`은 5배 북 복리 착시(WAN-213) — 방향·`return_over_mdd` 비교용으로만
    싣는다. 판정 저울은 MDD·밀림율·위험조정이다(이슈 완료기준 2).
    """

    model_config = ConfigDict(frozen=True)

    lens: str
    universe: int
    scope: str
    """`all`(이 실행이 커버한 전 TF 합친 북) 또는 개별 TF."""
    segment: str
    exclude_symbol: str = ""
    """leave-one-out 행(비어 있으면 전체 북)."""
    num_cells: int
    num_trades: int
    win_rate: float
    total_return: float
    max_drawdown: float
    peak_concurrency: int
    max_concurrent_risk: float
    max_open_notional_ratio: float
    liquidation_events: int
    clamped_entries: int
    skipped_cell_busy: int
    skipped_notional: int

    @property
    def return_over_mdd(self) -> float | None:
        """수익/MDD(위험조정 판정 지표). MDD가 0이면 정의하지 않는다."""
        if self.max_drawdown <= 0.0:
            return None
        return self.total_return / self.max_drawdown

    @property
    def skip_notional_rate(self) -> float | None:
        """밀림율 — 배치 가능했던 셋업 중 북 명목 한도에 막힌 비율(모듈 docstring 정의)."""
        denom = self.num_trades + self.skipped_notional
        if denom <= 0:
            return None
        return self.skipped_notional / denom


# --------------------------------------------------------------------------- #
# 펀딩 대리 (일반화 — WAN-180 규칙을 임의 유니버스로)
# --------------------------------------------------------------------------- #


def apply_funding_proxy_any(
    payloads: Sequence[CellPayload],
) -> tuple[list[CellPayload], str]:
    """펀딩 0행 칸을 **확정 펀딩 평균 최고**(롱에게 가장 비싼) 종목의 시계열로 대체한다.

    WAN-180 대리 규칙의 일반화 — 도너 후보를 「기존 6종목」이 아니라 「자기 펀딩이 있는
    전 종목」으로 넓혔다(이슈 §1: WAN-292 백필이 정상이면 발동하지 않아야 하고, 발동하면
    요약에 그대로 적는다). 자기 데이터가 있는 칸은 손대지 않는다(비트 불변).
    """
    donors = [p for p in payloads if _mean_confirmed_rate(p.funding[SEGMENT_FULL]) > float("-inf")]
    gapped = [p for p in payloads if not p.funding[SEGMENT_FULL]]
    if not gapped or not donors:
        return list(payloads), ""

    rate_by_symbol: dict[str, float] = {}
    for p in donors:
        short = _short(p.symbol)
        rate = _mean_confirmed_rate(p.funding[SEGMENT_FULL])
        rate_by_symbol[short] = max(rate_by_symbol.get(short, float("-inf")), rate)
    donor_short = max(rate_by_symbol, key=lambda s: rate_by_symbol[s])
    donor_by_tf: dict[str, dict[str, tuple[FundingRate, ...]]] = {
        p.timeframe: dict(p.funding) for p in donors if _short(p.symbol) == donor_short
    }

    out: list[CellPayload] = []
    replaced: list[str] = []
    for p in payloads:
        if p.funding[SEGMENT_FULL] or p.timeframe not in donor_by_tf:
            out.append(p)
            continue
        out.append(_reprice_with_funding(p, donor_by_tf[p.timeframe]))
        replaced.append(f"{_short(p.symbol)} {p.timeframe}")
    if not replaced:
        return list(payloads), ""
    note = f"도너 `{donor_short}`(확정 펀딩 평균 최고 = 보수적) → 대체 칸: {', '.join(replaced)}"
    return out, note


# --------------------------------------------------------------------------- #
# 북 실행 — 한 (렌즈 × 유니버스 × 스코프 × 구간 [× 제외])
# --------------------------------------------------------------------------- #


def _book_row(
    payloads: Sequence[CellPayload],
    *,
    lens: str,
    universe: int,
    scope: str,
    segment: str,
    exclude: str = "",
    include_reentry: bool = False,
) -> UniverseRow:
    """이 유니버스·스코프·구간의 채택(cap_only 5배) 북을 돌려 성과 행을 낸다.

    `include_reentry`(WAN-304, 옵트인)를 켜면 payload에 실린 「익절 후 존 내 재진입」
    후보(WAN-261/269)를 base와 합쳐 북에 넣는다 — 기본(False)이면 예전과 비트 단위로
    같다(wan300 CSV 재현).
    """
    seg_cells = _segment_cells(list(payloads), segment, exclude, include_reentry=include_reentry)
    base_cfg = harness.legacy_build_config(BOOK_ANNUALIZATION_TF)
    outcome = run_leverage_book(
        seg_cells,
        base_cfg,
        LeverageBookParams(leverage_multiple=ADOPTED_MULTIPLE, leverage_mode=ADOPTED_MODE),
    )
    result = build_result_from_trades(
        outcome.trades, outcome.effective_config, BOOK_ANNUALIZATION_TF
    )
    m = result.metrics
    s = outcome.stats
    return UniverseRow(
        lens=lens,
        universe=universe,
        scope=scope,
        segment=segment,
        exclude_symbol=exclude,
        num_cells=len(seg_cells),
        num_trades=m.num_trades,
        win_rate=m.win_rate,
        total_return=m.total_return,
        max_drawdown=m.max_drawdown,
        peak_concurrency=s.peak_concurrency,
        max_concurrent_risk=s.max_concurrent_risk_ratio,
        max_open_notional_ratio=s.max_open_notional_ratio,
        liquidation_events=len(s.liquidations),
        clamped_entries=s.clamped_entries,
        skipped_cell_busy=s.skipped_cell_busy,
        skipped_notional=s.skipped_notional,
    )


def _universe_payloads(payloads: Sequence[CellPayload], size: int, scope: str) -> list[CellPayload]:
    """유니버스 크기·스코프로 payload를 고른다 — 셀 재계산 없는 부분집합 선택.

    ⚠️ 비교는 반드시 `_short` 축으로 양쪽을 맞춘다 — 상수는 `BTCUSDT` 축약형이고
    payload.symbol은 `BTC/USDT:USDT` 정규형이라 한쪽만 `_short`를 태우면 교집합이
    조용히 비어 **빈 북**이 나온다(회귀 테스트가 동작으로 고정).
    """
    members = {_short(s) for s in universe_symbols(size)}
    picked = [p for p in payloads if _short(p.symbol) in members]
    if scope != "all":
        picked = [p for p in picked if p.timeframe == scope]
    return picked


def build_rows_for_cells(
    payloads: Sequence[CellPayload],
    *,
    lens: str,
    sizes: Sequence[int] = UNIVERSE_SIZES,
    include_reentry: bool = False,
) -> list[UniverseRow]:
    """한 렌즈의 셀에서 (유니버스 × 스코프 × 구간) 북 행 전부 + LOO 행(기준 렌즈·all)을 낸다.

    `include_reentry`(WAN-304, 옵트인) 규약은 `_book_row`와 같다 — 기본(False)이면 예전과
    비트 단위로 같다(payload에 재진입 후보가 실려 있어도 base만 북에 들어간다).
    """
    have = {_short(p.symbol) for p in payloads}
    timeframes = sorted({p.timeframe for p in payloads}, key=timeframe_to_ms)
    scopes: list[str] = ["all", *timeframes] if len(timeframes) > 1 else list(timeframes)
    rows: list[UniverseRow] = []
    for size in sizes:
        members = universe_symbols(size)
        missing = [s for s in members if _short(s) not in have]
        if missing:
            raise ValueError(f"유니버스 {size}의 셀이 없습니다: {missing} (부분 실행 금지)")
        for scope in scopes:
            picked = _universe_payloads(payloads, size, scope)
            for segment in MEASURED_SEGMENTS:
                rows.append(
                    _book_row(
                        picked,
                        lens=lens,
                        universe=size,
                        scope=scope,
                        segment=segment,
                        include_reentry=include_reentry,
                    )
                )
                if lens == REF_LENS and scope == "all":
                    # leave-one-out(완료기준 3) — 전 종목 축은 기준 렌즈·all 스코프만
                    # (북 시퀀싱은 싸지만 행 폭발은 읽기를 해친다).
                    for member in members:
                        short = _short(member)
                        rows.append(
                            _book_row(
                                picked,
                                lens=lens,
                                universe=size,
                                scope=scope,
                                segment=segment,
                                exclude=short,
                                include_reentry=include_reentry,
                            )
                        )
    return rows


# --------------------------------------------------------------------------- #
# 검산 — wan180 셀 CSV 교차 대조 (완료기준 4)
# --------------------------------------------------------------------------- #

WAN180_CELLS_CSV = REPORTS_DIR / "wan180_leverage_book_cells.csv"


def cross_check_wan180(
    cell_rows: Sequence[UniverseCellRow], path: Path = WAN180_CELLS_CSV
) -> list[str]:
    """기준 렌즈 1h 셀(9종목)을 wan180 셀 CSV와 대조한다 — 같은 좌표·같은 기계의 재현.

    거래 집합(후보·거래 수)은 펀딩과 무관하므로 **비트 일치를 요구**하고(불일치 = 배선
    오류 = RuntimeError), `total_return`은 WAN-292가 신규 3종목(DOGE·LINK·LTC) 펀딩을
    실데이터로 채운 뒤라 그 종목만 대리 시절과 달라진다(WAN-291 선례 — 의도된 발산).
    """
    if not path.exists():
        return [f"⚠️ wan180 셀 CSV가 없어 교차 대조 생략(`{path}`)."]
    ref = pd.read_csv(path)
    ref_keyed = {
        (str(r["symbol"]), str(r["timeframe"]), str(r["segment"])): r
        for r in ref.to_dict("records")
    }
    checked = 0
    bit_match: list[str] = []
    diverged: list[str] = []
    for row in cell_rows:
        if row.lens != REF_LENS or row.timeframe != "1h":
            continue
        key = (row.symbol, row.timeframe, row.segment)
        if key not in ref_keyed:
            continue
        r = ref_keyed[key]
        checked += 1
        if int(r["num_candidates"]) != row.num_candidates or int(r["num_trades"]) != (
            row.num_trades
        ):
            raise RuntimeError(
                f"🚨 wan180 교차 대조 불일치 — {key} 후보/거래 수가 다릅니다"
                f"({r['num_candidates']}/{r['num_trades']} vs "
                f"{row.num_candidates}/{row.num_trades}). 배선 오류다."
            )
        if abs(float(r["total_return"]) - row.total_return) < _RATIO_EPS:
            bit_match.append(_short(row.symbol))
        else:
            diverged.append(_short(row.symbol))
    if checked == 0:
        return ["⚠️ wan180 셀 CSV와 겹치는 (심볼·TF·구간)이 없어 교차 대조 생략."]
    lines = [
        f"✅ wan180 셀 교차 대조 — 1h {checked}행의 **후보·거래 수 전부 비트 일치**"
        "(거래 집합은 펀딩과 무관)."
    ]
    if bit_match:
        lines.append(f"`total_return`까지 비트 일치: {', '.join(sorted(set(bit_match)))}.")
    if diverged:
        lines.append(
            f"`total_return` 발산(의도됨 — WAN-292 실펀딩 vs wan180 대리/공백): "
            f"{', '.join(sorted(set(diverged)))} (WAN-291 선례)."
        )
    return lines


# --------------------------------------------------------------------------- #
# 격자 조립 — 렌즈 축
# --------------------------------------------------------------------------- #


def build_lens_rows(
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    lenses: Sequence[str] = DEFAULT_LENSES,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    jobs: int = 1,
    funding_proxy: bool = True,
    sizes: Sequence[int] = UNIVERSE_SIZES,
) -> tuple[list[UniverseRow], list[UniverseCellRow], dict[str, str], list[str], list[str]]:
    """렌즈별 (북 행, 셀 행) + 펀딩 대리 노트 + 펀딩 공백 종목 + 검산 문장들."""
    grid: list[UniverseRow] = []
    cells_out: list[UniverseCellRow] = []
    notes: dict[str, str] = {}
    gapped: set[str] = set()
    verify_lines: list[str] = []
    for lens in lenses:
        fill = None if lens == REF_LENS else harness.fill_preset(lens)
        engine_check = lens == REF_LENS
        cells = run_cells(
            symbols,
            timeframes,
            start=start,
            end=end,
            jobs=jobs,
            fill=fill,
            cold_segments=False,
            engine_check=engine_check,
            # WAN-305 명시 핀: wan300/303 CSV는 재진입 꺼진 북의 동결 기록이다(켠 판은 WAN-304
            # = wan304_universe_reentry가 별도 모듈로 낸다).
            reentry=False,
            invalidation_cancel=harness.LEGACY_INVALIDATION_CANCEL,
        )
        gapped.update(_short(c.symbol) for c in cells if not c.funding[SEGMENT_FULL])
        if funding_proxy:
            cells, note = apply_funding_proxy_any(cells)
            if note:
                notes[lens] = note
        lens_cell_rows = [UniverseCellRow.from_cell_row(row, lens) for c in cells for row in c.rows]
        cells_out.extend(lens_cell_rows)
        if engine_check:
            # 검산(WAN-164 패턴): 격리 행 ↔ 표준 경로 비트 대조 — 불일치면 죽는다.
            line, worst = verify_cells([row for c in cells for row in c.rows])
            print(f"[wan300] 검산({lens}): {line}", flush=True)
            if "🚨" in line:
                raise RuntimeError(f"엔진 검산 실패(worst={worst}): {line}")
            verify_lines.append(f"`{lens}` 렌즈: {line}")
            verify_lines.extend(cross_check_wan180(lens_cell_rows))
        rows = build_rows_for_cells(cells, lens=lens, sizes=sizes)
        grid.extend(rows)
        print(f"[wan300] {lens}: 북 {len(rows)}행", flush=True)
    return grid, cells_out, notes, sorted(gapped), verify_lines


# --------------------------------------------------------------------------- #
# 프레임 · CSV 왕복 · append 병합
# --------------------------------------------------------------------------- #


def grid_to_frame(rows: Sequence[UniverseRow]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows], columns=list(UniverseRow.model_fields))


def grid_from_csv(path: Path) -> list[UniverseRow]:
    frame = pd.read_csv(path, keep_default_na=False)
    return [UniverseRow.model_validate(rec) for rec in frame.to_dict("records")]


def cells_to_frame(rows: Sequence[UniverseCellRow]) -> pd.DataFrame:
    frame = pd.DataFrame([r.model_dump() for r in rows], columns=list(UniverseCellRow.model_fields))
    return frame


def cells_from_csv(path: Path) -> list[UniverseCellRow]:
    frame = pd.read_csv(path)
    records = []
    for rec in frame.to_dict("records"):
        for key in ("engine_total_return", "engine_num_trades"):
            if pd.isna(rec.get(key)):
                rec[key] = None
        records.append(UniverseCellRow.model_validate(rec))
    return records


def merge_grid(existing: Sequence[UniverseRow], new: Sequence[UniverseRow]) -> list[UniverseRow]:
    """--append: (렌즈, 유니버스, 스코프, 구간, 제외) 키로 덮어쓴다.

    ⚠️ `all` 스코프는 그 실행이 커버한 TF 집합의 집계라 단일 TF만 append하면 재계산
    불가다 — 새로 온 스코프만 덮어쓰고 기존 `all`은 남긴다(wan301 `merge_risk` 규약).
    §3의 `all` 3TF 재산출은 append 병합이 아니라 3TF 한 실행으로 한다(WAN-302 교훈).
    """
    keyed = {(r.lens, r.universe, r.scope, r.segment, r.exclude_symbol): r for r in existing}
    for r in new:
        keyed[(r.lens, r.universe, r.scope, r.segment, r.exclude_symbol)] = r
    return [keyed[k] for k in sorted(keyed, key=str)]


def merge_cells(
    existing: Sequence[UniverseCellRow], new: Sequence[UniverseCellRow]
) -> list[UniverseCellRow]:
    keyed = {(r.lens, r.symbol, r.timeframe, r.segment): r for r in existing}
    for r in new:
        keyed[(r.lens, r.symbol, r.timeframe, r.segment)] = r
    return [keyed[k] for k in sorted(keyed, key=str)]


# --------------------------------------------------------------------------- #
# 분석 (요약용 · 테스트 대상)
# --------------------------------------------------------------------------- #


def find_row(
    rows: Sequence[UniverseRow],
    lens: str,
    universe: int,
    scope: str,
    segment: str,
    exclude: str = "",
) -> UniverseRow | None:
    for r in rows:
        if (
            r.lens == lens
            and r.universe == universe
            and r.scope == scope
            and r.segment == segment
            and r.exclude_symbol == exclude
        ):
            return r
    return None


def _present_scopes(rows: Sequence[UniverseRow]) -> list[str]:
    seen = {r.scope for r in rows}
    tf = sorted((s for s in seen if s != "all"), key=timeframe_to_ms)
    return (["all"] if "all" in seen else []) + tf


def _agg_scope(rows: Sequence[UniverseRow]) -> str:
    scopes = _present_scopes(rows)
    return "all" if "all" in scopes else (scopes[0] if scopes else "")


def verdict(rows: Sequence[UniverseRow]) -> str:
    """완료기준 2 — (a/b/c) + 포화점. 기준 렌즈 · oos_warm(정본) · all 스코프에서 낸다."""
    agg = _agg_scope(rows)
    if not agg:
        return "**판정**: 북 행이 없어 판정 표본이 없다."
    seg = SEGMENT_OOS_WARM
    sizes = sorted({r.universe for r in rows})
    base = find_row(rows, REF_LENS, sizes[0], agg, seg)
    if base is None or base.return_over_mdd is None:
        return "**판정**: 기준 유니버스 행이 없어 판정 표본이 없다."
    better_both: list[int] = []
    better_return_only: list[int] = []
    for size in sizes[1:]:
        r = find_row(rows, REF_LENS, size, agg, seg)
        if r is None or r.return_over_mdd is None:
            continue
        if r.total_return > base.total_return and r.return_over_mdd >= base.return_over_mdd:
            better_both.append(size)
        elif r.total_return > base.total_return:
            better_return_only.append(size)
    # 포화 신호: 밀림율이 유니버스를 따라 실제로 커지는가.
    rates: dict[int, float | None] = {}
    for size in sizes:
        row = find_row(rows, REF_LENS, size, agg, seg)
        if row is not None:
            rates[size] = row.skip_notional_rate
    rate_parts: list[str] = []
    for size in sizes:
        rate = rates.get(size)
        rate_parts.append(f"{size}: —" if rate is None else f"{size}: {rate * 100:.2f}%")
    rate_txt = " → ".join(rate_parts)
    max_rate = max((v for v in rates.values() if v is not None), default=0.0)
    if max_rate < 0.05:
        saturation = (
            f"**5배 한도는 {sizes[-1]}종목에서도 포화 근처가 아니다**(밀림율 {rate_txt} — "
            "전부 5% 미만). 포화점은 이 사다리 밖(더 큰 유니버스)이다."
        )
    else:
        first = next((size for size in sizes if (rates.get(size) or 0.0) >= 0.05), sizes[-1])
        saturation = f"밀림율이 {first}종목부터 5%를 넘는다({rate_txt}) — 포화점 근처."
    if better_both:
        letter = "(a) 용량 이득이 희석을 이기는 구간이 있다"
        detail = f"유니버스 {better_both}가 총수익·수익/MDD 모두 {sizes[0]}종목을 이긴다"
    elif better_return_only:
        letter = "(c) 총수익은 늘지만 위험조정은 아니다"
        detail = (
            f"유니버스 {better_return_only}가 총수익만 이기고 수익/MDD는 밀린다"
            " — 늘어난 수익이 늘어난 낙폭의 값을 못 한다"
        )
    else:
        letter = "(b) 종목 늘림이 값을 더하지 않는다"
        detail = f"어느 유니버스도 {sizes[0]}종목의 총수익을 못 이긴다(희석 우세)"
    # 낙폭 사다리 — (a)를 「크면 클수록 좋다」로 읽지 못하게 판정 문장에 함께 싣는다
    # (수익·수익/MDD의 사다리 상승은 거래 수 증가의 복리 항이 크다, WAN-213).
    mdd_parts: list[str] = []
    for size in sizes:
        row = find_row(rows, REF_LENS, size, agg, seg)
        if row is not None:
            mdd_parts.append(f"{size}: {row.max_drawdown * 100:.2f}%")
    mdd_txt = " → ".join(mdd_parts)
    return (
        f"**판정 {letter}** — {detail} (기준 렌즈 `baseline` · `{agg}` 스코프 · oos_warm"
        f" 정본). {saturation} ⚠️ **MDD 사다리: {mdd_txt}** — 수익·수익/MDD의 사다리 "
        "상승에는 거래 수 증가의 복리 항이 섞여 있으므로(WAN-213) 「클수록 좋다」가 아니라 "
        "**낙폭 예산 안에서 고르는 문제**다."
    )


# --------------------------------------------------------------------------- #
# 요약 마크다운
# --------------------------------------------------------------------------- #


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _fmt_rr(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _fmt_rate(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _ladder_table(rows: Sequence[UniverseRow], lens: str, scope: str, segment: str) -> str:
    lines = [
        "| 종목 수 | 칸 | 거래수 | 총수익 | MDD | 수익/MDD | 밀림(건) | 밀림율 | 칸바쁨 "
        "| 동시 최대 칸 | 최대 동시 리스크 | 청산 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for size in sorted({r.universe for r in rows}):
        r = find_row(rows, lens, size, scope, segment)
        if r is None:
            continue
        lines.append(
            f"| **{size}** | {r.num_cells} | {r.num_trades} | {_fmt_pct(r.total_return)} | "
            f"**{_fmt_pct(r.max_drawdown)}** | {_fmt_rr(r.return_over_mdd)} | "
            f"{r.skipped_notional} | {_fmt_rate(r.skip_notional_rate)} | "
            f"{r.skipped_cell_busy} | {r.peak_concurrency} | "
            f"{_fmt_pct(r.max_concurrent_risk)} | {r.liquidation_events} |"
        )
    return "\n".join(lines)


def _loo_table(rows: Sequence[UniverseRow], segment: str) -> str:
    """유니버스별 leave-one-out — 수익/MDD 최악·최선 종목과 ETH 제외를 병기(완료기준 3)."""
    agg = _agg_scope(rows)
    lines = [
        "| 종목 수 | 전체 수익/MDD | ETH 제외 | 최악 제외(종목) | 최선 제외(종목) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for size in sorted({r.universe for r in rows}):
        full = find_row(rows, REF_LENS, size, agg, segment)
        if full is None:
            continue
        loo = [
            r
            for r in rows
            if r.lens == REF_LENS
            and r.universe == size
            and r.scope == agg
            and r.segment == segment
            and r.exclude_symbol
        ]
        if not loo:
            continue
        eth = next((r for r in loo if r.exclude_symbol == "ETH"), None)
        worst = min(loo, key=lambda r: r.return_over_mdd or float("inf"))
        best = max(loo, key=lambda r: r.return_over_mdd or float("-inf"))
        lines.append(
            f"| {size} | {_fmt_rr(full.return_over_mdd)} | "
            f"{_fmt_rr(eth.return_over_mdd) if eth else '—'} | "
            f"{_fmt_rr(worst.return_over_mdd)} ({worst.exclude_symbol}) | "
            f"{_fmt_rr(best.return_over_mdd)} ({best.exclude_symbol}) |"
        )
    return "\n".join(lines)


def _dilution_table(cell_rows: Sequence[UniverseCellRow], timeframes: Sequence[str]) -> str:
    """신규 후보의 격리 oos_warm 성과 — 희석의 직접 증거(WAN-111 선례 확인용)."""
    nine_short = {_short(s) for s in NINE_SYMBOLS}
    lines = ["| 종목 | " + " | ".join(f"{tf} 격리 oos_warm" for tf in timeframes) + " |"]
    lines.append("| --- | " + " | ".join(["---"] * len(timeframes)) + " |")
    keyed = {
        (_short(r.symbol), r.timeframe): r.total_return
        for r in cell_rows
        if r.lens == REF_LENS and r.segment == SEGMENT_OOS_WARM
    }
    nine_means = []
    for tf in timeframes:
        vals = [v for (s, t), v in keyed.items() if t == tf and s in nine_short]
        nine_means.append(sum(vals) / len(vals) if vals else 0.0)
    lines.append(
        "| **채택 9종목 평균** | " + " | ".join(f"**{_fmt_pct(v)}**" for v in nine_means) + " |"
    )
    for sym in CANDIDATE_SYMBOLS:
        short = _short(sym)
        cells = [keyed.get((short, tf)) for tf in timeframes]
        if all(c is None for c in cells):
            continue
        lines.append(
            f"| {short} | " + " | ".join("—" if c is None else _fmt_pct(c) for c in cells) + " |"
        )
    return "\n".join(lines)


def build_summary_markdown(
    rows: Sequence[UniverseRow],
    cell_rows: Sequence[UniverseCellRow],
    *,
    grid_csv: Path = DEFAULT_GRID_CSV,
    cells_csv: Path = DEFAULT_CELLS_CSV,
    funding_notes: dict[str, str] | None = None,
    gapped_symbols: Sequence[str] = (),
    verify_lines: Sequence[str] = (),
    adv_table: Sequence[tuple[str, float]] = (),
) -> str:
    timeframes = sorted({r.scope for r in rows if r.scope != "all"}, key=timeframe_to_ms)
    parts: list[str] = []
    parts.append("# WAN-300 — 레버리지 북 종목 수 최적화 (cap_only 5배 · 유니버스 스윕)\n")
    parts.append(
        "> 사용자 질문(2026-08-13): 종목을 더 늘리면 수익을 최대화하는 종목 수는? — 채택 북\n"
        "> (cap_only 5배 · 재진입 없음 스냅샷)에서 유니버스 9→12→15→19를 같은 셀 위에서\n"
        "> 조립해 잰다. 측정 전용 · oos_warm 정본(WAN-166) · 렌즈 baseline+pen_5bp.\n"
    )
    excluded = " · ".join(f"{k}({v})" for k, v in EXCLUDED_CANDIDATES.items())
    parts.append(
        f"후보 자격(§1 프로브 2026-08-14): 이슈 후보 11종목 중 **제외 {excluded}**, 나머지 "
        "10종목은 상장일 < 2020-09-15 · 창 상한까지 데이터 있음(1m 6년 백필 + native TF "
        "빌드 + 펀딩 백필 완료). 합류 순서 = 창 내 ADV 내림차순(아래 표).\n"
    )
    if adv_table:
        parts.append("## 후보 ADV (합류 순서의 자 — 일중 달러 거래대금 중앙값, 못 박은 창)\n")
        parts.append("| 순위 | 종목 | ADV(백만$) |")
        parts.append("| --- | --- | --- |")
        for i, (sym, adv) in enumerate(adv_table, start=1):
            parts.append(f"| {i} | {sym} | {adv / 1e6:,.1f} |")
        parts.append("")
    if gapped_symbols:
        parts.append(
            f"🚨 **펀딩 공백 종목**: {', '.join(gapped_symbols)} — 자기 펀딩 0행"
            "(대리 규칙 적용 여부는 아래 노트).\n"
        )
    else:
        parts.append("펀딩: 전 종목 실데이터(자기 펀딩 0행 종목 없음 — 대리 미발동).\n")
    if funding_notes:
        for lens, note in sorted(funding_notes.items()):
            parts.append(f"펀딩 대리 발동(`{lens}`): {note}\n")
    scope_note = "+".join(timeframes) if timeframes else "(TF 없음)"
    parts.append(
        f"`all` 스코프의 TF 구성: **{scope_note}** (단일 TF append는 `all`을 재계산하지 "
        "못한다 — wan301 규약. §3 15m은 3TF 한 실행으로 잇는다).\n"
    )

    parts.append("## 판정\n")
    parts.append(verdict(rows) + "\n")

    lenses = [lens for lens in DEFAULT_LENSES if any(r.lens == lens for r in rows)]
    for segment in MEASURED_SEGMENTS:
        seg_label = "oos_warm (정본)" if segment == SEGMENT_OOS_WARM else segment
        parts.append(f"## 구간 `{seg_label}`\n")
        smallest = min({r.universe for r in rows}, default=0)
        for lens in lenses:
            for scope in _present_scopes(rows):
                if find_row(rows, lens, smallest, scope, segment) is None:
                    continue
                parts.append(f"### 렌즈 `{lens}` · 스코프 `{scope}`\n")
                parts.append(_ladder_table(rows, lens, scope, segment) + "\n")

    parts.append("## leave-one-out (기준 렌즈 · all 스코프 · oos_warm)\n")
    parts.append(_loo_table(rows, SEGMENT_OOS_WARM) + "\n")

    if cell_rows:
        parts.append("## 신규 후보 격리 성과 (희석의 직접 증거 — WAN-111 선례 확인)\n")
        parts.append(_dilution_table(cell_rows, timeframes) + "\n")

    parts.append("## ⚠️ 경고 (인용 시 함께 옮길 것)\n")
    parts.append(
        "- **「종목 늘리면 돈 더 번다」로 인용 금지** — WAN-111 희석 선례(3→6종목에서 OOS "
        "심볼평균 +19% → +3.9%)가 반대를 가리켰다. 이 표는 그 교차점을 잰 것이지 확대를 "
        "전제·권고하지 않는다.\n"
        "- `total_return` %는 5배 북 복리 착시(WAN-213) — 판단은 **MDD·밀림율·수익/MDD**로.\n"
        "- 전부 `baseline`(닿으면 체결) 위 값이고 pen_5bp까지가 모델 밴드 — 신규 후보일수록 "
        "유동성이 얇아 낙관이 더 크다(그래서 합류 순서가 ADV 순이다). 진짜 큐 우선순위·부분 "
        "체결은 틱·호가(WAN-98, Canceled) 소관.\n"
        "- **6년 MDD는 천장이 아니라 바닥선** — 창이 2020-09 시작이라 2018·2020-03 폭락 "
        "미포함.\n"
        "- **「엣지 없음」(WAN-84/88/111/114/124/151/201) 불변** — 유니버스 크기는 알파가 "
        "아니라 용량·위험의 모양을 바꾼다(WAN-90 계열).\n"
        "- 측정 전용 — 유니버스 확대 채택은 **재-베이스라인 = 사용자 결정**(개발자 임의 착수 "
        "금지). `DEFAULT_SYMBOLS`·`ConfluenceParams()`·`LeverageBookParams()` 불변 · 실거래 "
        "보류(`ALPHABLOCK_LIVE_TRADING=false`) 유지.\n"
    )

    parts.append("## 검산\n")
    if verify_lines:
        for line in verify_lines:
            parts.append(f"- {line}")
        parts.append("")
    else:
        parts.append(
            "- 검산 문장이 없다 — 셀 CSV가 없어 재계산하지 못했다(측정 실행 로그와 회귀 "
            "테스트가 정본).\n"
        )

    parts.append("## 재현\n")
    parts.append(
        "```\n"
        "uv run python -m backtest.wan300_universe_size --jobs 4\n"
        "uv run python -m backtest.wan300_universe_size --from-csv   # 요약만 재생성\n"
        "# §3(후속): 최적 근처 유니버스 15m 확인 — all 재산출은 3TF 한 실행\n"
        "uv run python -m backtest.wan300_universe_size --tf 15m,1h,4h --append --jobs 4\n"
        "```\n"
        f"원자료: `{grid_csv}` · `{cells_csv}`. cap_only={ADOPTED_MODE} 배수="
        f"{ADOPTED_MULTIPLE} · 창 못 박음({DEFAULT_START}~{DEFAULT_END}).\n"
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-300 레버리지 북 종목 수 최적화 스윕")
    parser.add_argument("--symbols", type=str, default=",".join(ALL_SYMBOLS))
    parser.add_argument("--tf", type=str, default=",".join(DEFAULT_TIMEFRAMES))
    parser.add_argument("--fill", type=str, default=",".join(DEFAULT_LENSES))
    parser.add_argument("--start", type=str, default=DEFAULT_START)
    parser.add_argument("--end", type=str, default=DEFAULT_END)
    parser.add_argument(
        "--jobs",
        type=int,
        default=harness.default_jobs(),
        help="(심볼, TF) 단위 병렬 워커 수(미지정이면 ALPHABLOCK_BACKTEST_JOBS, WAN-294)",
    )
    parser.add_argument("--no-funding-proxy", action="store_true", help="펀딩 대리 끔.")
    parser.add_argument("--from-csv", action="store_true", help="저장된 CSV에서 요약만 재생성.")
    parser.add_argument("--append", action="store_true", help="새 TF/렌즈를 기존 CSV에 병합.")
    parser.add_argument("--grid-csv", type=Path, default=DEFAULT_GRID_CSV)
    parser.add_argument("--cells-csv", type=Path, default=DEFAULT_CELLS_CSV)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args(argv)

    grid_csv: Path = args.grid_csv
    cells_csv: Path = args.cells_csv
    out_md: Path = args.summary
    funding_notes: dict[str, str] = {}
    gapped: list[str] = []
    verify_lines: list[str] = []

    if args.from_csv:
        rows = grid_from_csv(grid_csv)
        cell_rows = cells_from_csv(cells_csv) if cells_csv.exists() else []
        print(f"[wan300] CSV 로드 — 북 {len(rows)}행 · 셀 {len(cell_rows)}행 (재실행 없음)")
        if cell_rows:
            # 검산은 셀 CSV가 재료를 다 갖고 있어(엔진 열 포함) 재생성 경로에서도 다시
            # 계산한다 — 요약이 실행 이력에 기대지 않고 자가 치유된다(wan291 관행).
            line, worst = verify_cells(
                [
                    CellRow(**{k: v for k, v in r.model_dump().items() if k != "lens"})
                    for r in cell_rows
                    if r.lens == REF_LENS
                ]
            )
            if "🚨" in line:
                raise RuntimeError(f"엔진 검산 실패(worst={worst}): {line}")
            verify_lines.append(f"`{REF_LENS}` 렌즈: {line}")
            verify_lines.extend(cross_check_wan180(cell_rows))
    else:
        symbols = tuple(s.strip() for s in str(args.symbols).split(",") if s.strip())
        timeframes = tuple(t.strip() for t in str(args.tf).split(",") if t.strip())
        lenses = tuple(s.strip() for s in str(args.fill).split(",") if s.strip())
        for lens in lenses:
            if lens != REF_LENS:
                harness.fill_preset(lens)  # 지원하지 않는 렌즈는 여기서 거부(WAN-95 교훈).
        rows, cell_rows, funding_notes, gapped, verify_lines = build_lens_rows(
            symbols,
            timeframes,
            lenses=lenses,
            start=args.start,
            end=args.end,
            jobs=args.jobs,
            funding_proxy=not args.no_funding_proxy,
        )
        if args.append and grid_csv.exists():
            rows = merge_grid(grid_from_csv(grid_csv), rows)
        if args.append and cells_csv.exists():
            cell_rows = merge_cells(cells_from_csv(cells_csv), cell_rows)
        grid_csv.parent.mkdir(parents=True, exist_ok=True)
        grid_to_frame(rows).to_csv(grid_csv, index=False)
        cells_to_frame(cell_rows).to_csv(cells_csv, index=False)
        print(f"[wan300] 북 {len(rows)}행 → {grid_csv} · 셀 {len(cell_rows)}행 → {cells_csv}")

    # ADV 표(합류 순서 동결 근거) — 저장소가 있으면 재계산해 요약에 싣는다.
    adv_table: list[tuple[str, float]] = []
    try:
        from config.settings import get_settings

        with OhlcvStore(get_settings().db_path) as store:
            adv_table = rank_candidates_by_adv(store, start=args.start, end=args.end)
    except Exception as exc:  # noqa: BLE001 — 요약 재생성은 DB 없이도 가능해야 한다.
        print(f"[wan300] ADV 표 생략(데이터 없음): {exc}")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(
        build_summary_markdown(
            rows,
            cell_rows,
            grid_csv=grid_csv,
            cells_csv=cells_csv,
            funding_notes=funding_notes,
            gapped_symbols=gapped,
            verify_lines=verify_lines,
            adv_table=adv_table,
        ),
        encoding="utf-8",
    )
    print(f"[wan300] 요약 → {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
