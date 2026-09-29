"""WAN-440: 스토캐스틱 팔 종목 확대 — 종목을 고르지 않는 규칙으로 넓혀 8.6년 · MDD 35% 자로 잰다.

## 왜 이 모듈이 있나

WAN-438 탐색의 종목 수 곡선(무작위 부분집합 · 채택 근거 아님)이 31종목에서도 꺾이지 않았다.
이 모듈은 **종목을 손으로 고르지 않는 규칙**으로 유니버스를 넓혀 한 번 잰다(사용자 지시 —
과최적화 금지).

## 무엇을 고정했나 (착수 전 · 코드 상수)

* **대상 규칙**: 바이낸스 USDT 무기한선물(아카이브 `data/futures/um`) 중 **첫 1분봉이
  2020-09-15 00:00 UTC 이전**인 종목 전부 — 이후 상폐된 종목 포함(생존 편향 방지). 목록은
  아카이브 목록에서 기계적으로 뽑고 제외 사유를 표로 남긴다(`--part list`). ⚠️ 지금 31종목 중
  UNI(09-18) · AVAX(09-23)는 이 규칙 밖이다 — 31종목이 손으로 고른 목록이었다는 증거다.
* **토대 그대로**: WAN-436/438 페이퍼 좌표 + 층 B(WAN-439) — `wan439_crash_size_layer`의
  경로를 그대로 쓴다. 새 파라미터 없음. 🚨 몰림 규칙의 숫자(직전 24h 신호 1~10 건너뜀 · 11+
  몰림 · 예산 10)는 절대 개수라 종목이 늘면 사실상 다르게 동작한다 — **숫자를 바꾸지 않고**
  잰다(이슈 §알려진 함정).
* **변형 둘**: `9TF`(탐색 곡선과 같은 좌표) · `+15m`(WAN-439 §8 · 페이퍼에 올리기로 한 규칙).
* **데이터**: 새 종목의 선물 1분봉 · 상위TF · 펀딩(실데이터, 아카이브) → **별도 루트**
  (`data/cache/wan440-roots/futures`). 2018~2020 현물은 있는 종목만 → `…/spot`. 운영 DB
  불변(WAN-194). 3h는 1분봉에서 집계(`data.aggregate`, 선물 DB와 같은 규약) · 2h는 로더가
  1h에서 파생. 폭락 국면 판정은 시장별 BTC(선물은 운영 DB 선물 BTC · 현물은 WAN-438 현물 BTC).
* **자**: 8.6년 이어 붙인 경로 · 크기는 ① 미끄러짐 포함(손절 = 그 1분 저가) 평가손 MDD
  35%(정본) · ③ 손절가 체결 MDD 35%로 맞춘다. 사용자가 고른 **거래당 3% 고정**도 적는다.
* **판정**(`verdict`): 규칙 유니버스의 8.6년 연환산(①, 자기 크기)이 31종목보다 높다
  **그리고** 31종목의 맞춤 크기에서 창 밖 · 6년 앞 · 6년 뒤 수익/MDD가 31종목보다 나쁘지 않다.

**측정 전용 · 기본값·토대 불변** · 페이퍼 반영은 WAN-441(사용자 결정) · 실거래 보류 유지
(`ALPHABLOCK_LIVE_TRADING=false`).
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import re
import time
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import harness
from backtest import wan436_stoch_crowd_rules as w
from backtest import wan438_spot_stress as m438
from backtest import wan439_crash_size_layer as c
from backtest.payload_cache import PayloadCache
from backtest.run import parse_date_ms
from backtest.wan169_leverage_book import CellPayload, run_cells
from backtest.wan424_stoch_ob_arm import SYMBOLS as SYMBOLS_31
from backtest.wan424_stoch_ob_arm import base_cell_kwargs, build_base_payloads
from data import spot_klines as sk
from data.aggregate import build_symbol
from data.funding import FundingRateStore
from data.models import FundingRate
from data.storage import OhlcvStore
from data.tick_probe import VISION_BASE, urllib_transport

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "backtest" / "reports"
UNIVERSE_PATH = REPORT_DIR / "wan440_universe.csv"
LOAD_PATH = REPORT_DIR / "wan440_load_check.csv"
CSV_PATH = REPORT_DIR / "wan440_universe_grid.csv"
SUMMARY_PATH = REPORT_DIR / "wan440_universe_summary.md"
ROOTS = REPO_ROOT / "data" / "cache" / "wan440-roots"
FUT_ROOT = ROOTS / "futures"
SPOT_ROOT = ROOTS / "spot"
ZIP_CACHE = REPO_ROOT / "data" / "cache" / "wan440-zips"

# --- 착수 전 고정 ---------------------------------------------------------------------------
LISTED_BEFORE = "2020-09-15"
"""첫 선물 1분봉이 이 시각(UTC 00:00) **이전**이면 대상."""
QUOTE = "USDT"
FUT_FIRST_MONTH = "2020-01"
FUT_LAST_MONTH = "2026-07"
FUT_NATIVE_TFS: tuple[str, ...] = ("1m", "15m", "1h", "4h", "6h", "8h", "12h", "1d", "1w")
AGG_TFS: tuple[str, ...] = ("3h",)
SPOT_NATIVE_TFS: tuple[str, ...] = ("1m", "15m", "1h", "4h", "6h", "8h", "12h", "1d", "1w")
BTC = "BTCUSDT"
TF15 = "15m"
VARIANTS: tuple[str, ...] = ("9TF", "+15m")
U31 = "31종목(현재)"
URULE = "규칙 유니버스"
#: (라벨, 맞춤 체결, MDD 목표 · None이면 고정 크기)
RULERS: tuple[tuple[str, str, float | None, float | None], ...] = (
    ("① 미끄러짐 · MDD 35%", "bar_low", 0.35, None),
    ("③ 손절가 · MDD 35%", "stop", 0.35, None),
    ("고정 3%", "bar_low", None, 0.03),
)
PRIMARY_RULER = RULERS[0][0]
CURVE_SIZES: tuple[int, ...] = (35, 40, 45, 50)
CURVE_DRAWS = 8
CURVE_SEED = 440
"""종목 수 곡선(사용자 요청 2026-09-30) — 규칙 유니버스 안에서 무작위 부분집합 · 착수 전 고정."""
CURVE_PATH = REPORT_DIR / "wan440_curve.csv"

_PREFIX_RE = re.compile(r"<CommonPrefixes><Prefix>([^<]+)</Prefix></CommonPrefixes>")
_MARKER_RE = re.compile(r"<NextMarker>([^<]+)</NextMarker>")


def ms(date: str) -> int:
    return parse_date_ms(date)


# ---------------------------------------------------------------------------
# §1 목록 — 규칙으로 기계적으로 뽑는다
# ---------------------------------------------------------------------------


def archive_symbols(market_path: str = "futures/um") -> list[str]:
    """아카이브에 월별 캔들이 있는 모든 심볼(상폐 포함)."""
    transport = sk.retrying(urllib_transport)
    prefix = f"data/{market_path}/monthly/klines/"
    out: list[str] = []
    marker: str | None = None
    while True:
        url = f"{sk.LISTING_BASE}?delimiter=/&prefix={prefix}" + (
            f"&marker={marker}" if marker else ""
        )
        text = transport(url).body.decode("utf-8")
        out += [p.rstrip("/").rsplit("/", 1)[-1] for p in _PREFIX_RE.findall(text)]
        found = _MARKER_RE.search(text)
        if "<IsTruncated>true</IsTruncated>" not in text or not found:
            break
        marker = found.group(1)
    return out


def is_usdt_perp(symbol: str) -> bool:
    """`…USDT`이고 만기물(`_YYMMDD`)이 아닌 것. 비ASCII 이름은 2020년 이후 상장이라 대상 밖이다."""
    return symbol.isascii() and symbol.endswith(QUOTE) and "_" not in symbol


def first_open_ms(symbol: str, month: str) -> int:
    """그 달 선물 1분봉 파일의 첫 `open_time`(상장 시각)."""
    transport = sk.retrying(urllib_transport)
    body = transport(sk.month_url(symbol, "1m", month, "um")).body
    frame = sk.read_zip_bytes(body)
    return int(frame["open_time"].min())


@dataclass(frozen=True)
class UniverseRow:
    symbol: str
    first_month: str
    last_month: str
    first_open_utc: str
    included: bool
    reason: str
    in_31: bool


def build_universe(jobs: int = 16) -> list[UniverseRow]:
    syms = [s for s in archive_symbols() if is_usdt_perp(s)]
    with ThreadPoolExecutor(jobs) as pool:
        listed = pool.map(lambda s: sk.list_months(s, "1m", market="um"), syms)
        months = dict(zip(syms, listed, strict=True))
    cut = ms(LISTED_BEFORE)
    have = {s.split("/")[0] + QUOTE for s in SYMBOLS_31}
    rows: list[UniverseRow] = []
    boundary = LISTED_BEFORE[:7]
    for s in sorted(syms):
        files = months[s]
        if not files or files[0].month > boundary:
            continue  # 명백히 이후 상장 — 표에 싣지 않는다(수백 개)
        first = files[0].month
        opened = first_open_ms(s, first) if first == boundary else ms(f"{first}-01")
        ok = opened < cut
        rows.append(
            UniverseRow(
                symbol=s,
                first_month=first,
                last_month=files[-1].month,
                first_open_utc=m438._utc(opened) if first == boundary else f"{first}(월)",
                included=ok,
                reason="" if ok else f"상장 {m438._utc(opened)} ≥ {LISTED_BEFORE}",
                in_31=s in have,
            )
        )
    return rows


def rule_symbols() -> list[str]:
    frame = pd.read_csv(UNIVERSE_PATH)
    return [str(s) for s in frame[frame.included].symbol]


def store_symbol(bare: str) -> str:
    return sk.to_store_symbol(bare)


# ---------------------------------------------------------------------------
# §2 받기 · 적재
# ---------------------------------------------------------------------------


def _files(
    symbols: Sequence[str], tfs: Sequence[str], first: str, last: str, market: str
) -> list[sk.MonthFile]:
    pairs = [(s, tf) for s in symbols for tf in tfs]
    with ThreadPoolExecutor(16) as pool:
        lists = pool.map(lambda p: sk.list_months(p[0], p[1], market=market), pairs)
    return [f for fs in lists for f in sk.select_months(fs, first, last)]


def funding_rates(symbol: str, first: str, last: str) -> list[FundingRate]:
    """아카이브 월별 펀딩(`calc_time · funding_interval_hours · last_funding_rate`) → 확정 펀딩."""
    transport = sk.retrying(urllib_transport)
    out: list[FundingRate] = []
    y, mo = (int(x) for x in first.split("-"))
    while f"{y:04d}-{mo:02d}" <= last:
        month = f"{y:04d}-{mo:02d}"
        url = (
            f"{VISION_BASE}/data/futures/um/monthly/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-{month}.zip"
        )
        r = transport(url)
        if r.ok:
            with zipfile.ZipFile(io.BytesIO(r.body)) as z:
                raw = z.read(next(n for n in z.namelist() if n.endswith(".csv")))
            frame = pd.read_csv(io.BytesIO(raw))
            for t, rate in zip(frame.iloc[:, 0], frame.iloc[:, -1], strict=True):
                t = int(t) // 1000 if int(t) > 10**14 else int(t)
                out.append(FundingRate(store_symbol(symbol), t, float(rate)))
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def load_root(
    root: Path,
    symbols: Sequence[str],
    tfs: Sequence[str],
    first: str,
    last: str,
    *,
    market: str,
    end_ms: int,
    extra_1m: Sequence[str] = (),
) -> tuple[list[m438.LoadCheckRow], float, float]:
    """받기 → 검증 → 적재. 반환: (검사 행, 받은 MB, 초)."""
    t0 = time.monotonic()
    files = _files(symbols, tfs, first, last, market)
    files += _files(extra_1m, ("1m",), first, last, market)
    cache = ZIP_CACHE / market
    res = sk.fetch_months(files, cache_dir=cache, jobs=8)
    bad = [r for r in res if not r.ok]
    if bad:
        raise RuntimeError(
            f"받기 실패 {len(bad)}건: {[(r.file.symbol, r.file.month, r.note) for r in bad[:5]]}"
        )
    mb = sum(f.size_bytes for f in files) / 1e6
    rows = m438.build_root(root.name, root, files, zip_cache=cache, start_ms=None, end_ms=end_ms)
    return rows, mb, time.monotonic() - t0


def aggregate_3h(root: Path, symbols: Sequence[str]) -> int:
    store = OhlcvStore(sk.root_db_path(root))
    n = 0
    for s in symbols:
        for r in build_symbol(store, store_symbol(s), tuple(AGG_TFS)):
            n += r.inserted
    return n


# ---------------------------------------------------------------------------
# §3 측정
# ---------------------------------------------------------------------------


def root_payloads(
    root: Path, symbols: Sequence[str], tfs: Sequence[str], start: str, end: str, jobs: int
) -> list[CellPayload]:
    """별도 루트에서 base 후보 — 인자는 `wan424.base_cell_kwargs` 그대로(차가운 절단만 끈다)."""
    with m438.working_root(root):
        payloads = run_cells(
            [store_symbol(s) for s in symbols],
            tuple(tfs),
            start=start,
            end=end,
            jobs=jobs,
            cold_segments=False,
            payload_cache=PayloadCache(root / "payloads"),
            **base_cell_kwargs(),
        )
    blank = {harness.SEGMENT_IS: (), harness.SEGMENT_OOS: ()}
    return [dataclasses.replace(p, funding={**blank, **p.funding}) for p in payloads]


def market_from(
    name: str,
    parts: Sequence[tuple[list[CellPayload], OhlcvStore]],
    *,
    start_ms: int,
    end_ms: int,
    entry_from_ms: int,
    entry_to_ms: int,
    btc_store: OhlcvStore,
    btc_from_ms: int,
) -> c.Market:
    """여러 루트의 payload를 한 시장으로 — 팔 후보는 루트마다 그 루트 1분봉으로, 폭락 판정은 그
    시장의 BTC 하나로(두 벌 금지)."""
    payloads: list[CellPayload] = []
    entries: list[w.ArmEntry] = []
    for pls, store in parts:
        es = w.build_entries(
            pls, store=store, start_ms=start_ms, end_ms=end_ms, btc_store=btc_store
        )
        entries += [e for e in es if entry_from_ms <= int(e.cand.entry_time) < entry_to_ms]
        payloads += pls
    counts = w.signal_counts([int(e.cand.entry_time) for e in entries])
    b24, vr = c.btc_features(btc_store, entries, btc_from_ms, end_ms)
    return c.Market(name, payloads, entries, counts, b24, vr)


def restrict(mk: c.Market, symbols: set[str]) -> c.Market:
    keep = [i for i, e in enumerate(mk.entries) if e.symbol in symbols]
    entries = [mk.entries[i] for i in keep]
    return c.Market(
        mk.name,
        [p for p in mk.payloads if p.symbol in symbols],
        entries,
        w.signal_counts([int(e.cand.entry_time) for e in entries]),
        [mk.btc_24h[i] for i in keep],
        [mk.vol_ratio[i] for i in keep],
    )


def label(universe: str, variant: str) -> str:
    return f"{universe} {variant}"


def verdict(rows: Sequence[c.Row], ruler: str, variant: str) -> tuple[bool, list[str]]:
    def get(u: str, basis: str, seg: str) -> c.Row:
        hits = [
            r
            for r in rows
            if (r.arm, r.size_basis, r.segment, r.fill)
            == (label(u, variant), basis, seg, "bar_low")
        ]
        if len(hits) != 1:
            raise KeyError((u, basis, seg, len(hits)))
        return hits[0]

    own = f"{ruler} · 자기 맞춤"
    base = f"{ruler} · {U31} 맞춤 크기"
    b, x = get(U31, own, c.SEG_CHAIN), get(URULE, own, c.SEG_CHAIN)
    ok = x.cagr > b.cagr
    lines = [
        f"(1) 8.6년 연환산: 규칙 {x.cagr:+.2%} ({x.risk:.2%}) vs 31종목 {b.cagr:+.2%} "
        f"({b.risk:.2%}) → " + ("✅" if ok else "❌")
    ]
    for seg in c.VERDICT_SEGMENTS:
        rb, rx = get(U31, base, seg), get(URULE, base, seg)
        good = rx.ratio >= rb.ratio
        ok = ok and good
        lines.append(
            f"(2) 31종목 크기 {rb.risk:.2%} · {seg}: 수익/MDD 규칙 {rx.ratio:+.3f} "
            f"({rx.total_return:+.1%}/{rx.mdd_low:.1%}) vs 31종목 {rb.ratio:+.3f} "
            f"({rb.total_return:+.1%}/{rb.mdd_low:.1%}) → " + ("✅" if good else "❌")
        )
    return ok, lines


def run(jobs: int) -> tuple[list[c.Row], list[str]]:
    c.assert_adopted_take_profit_liquidity()
    t0 = time.monotonic()
    notes: list[str] = []
    rule = rule_symbols()
    rule_store = {store_symbol(s) for s in rule}
    main31 = set(SYMBOLS_31)
    new_fut = sorted(s for s in rule if store_symbol(s) not in main31)
    tfs = (*c.FUT_TIMEFRAMES, TF15)
    main_pl = build_base_payloads(
        jobs=jobs, payload_dir=w.DEFAULT_PAYLOAD_DIR, symbols=SYMBOLS_31, timeframes=tfs
    )
    new_pl = root_payloads(FUT_ROOT, new_fut, tfs, harness.DEFAULT_START, harness.DEFAULT_END, jobs)
    # 앞/뒤 경계는 칸의 **마지막 봉** 기준(WAN-166)이라 상폐 종목(LEND 2020-11 등)은 경계가 앞당겨져
    # 그 거래가 「6년 뒤」로 잘못 분류된다 — 새 종목 칸의 경계를 같은 TF의 BTC 칸 경계로 맞춘다
    # (달력 한 점). 경계는 거래를 앞/뒤로 나누는 데만 쓰이고 후보·배치는 안 바뀐다.
    btc_bound = {p.timeframe: p.boundary_ms for p in main_pl if p.symbol == "BTC/USDT:USDT"}
    new_pl = [dataclasses.replace(p, boundary_ms=btc_bound[p.timeframe]) for p in new_pl]
    notes.append(
        f"선물 후보 — 31종목 {len(main_pl)}칸 · 새 종목 {len(new_pl)}칸 "
        f"({time.monotonic() - t0:.0f}초)"
    )
    main_store = OhlcvStore(harness.DB_PATH)
    fut = market_from(
        "선물",
        [(main_pl, main_store), (new_pl, OhlcvStore(sk.root_db_path(FUT_ROOT)))],
        start_ms=ms(harness.DEFAULT_START),
        end_ms=ms(harness.DEFAULT_END),
        entry_from_ms=0,
        entry_to_ms=1 << 62,
        btc_store=main_store,
        btc_from_ms=ms(harness.DEFAULT_START) - (c.VOL_BASE_MIN + 60) * c.MINUTE_MS,
    )
    stress = m438.DEFAULT_ROOTS / "stress"
    old_spot_pl = m438.spot_payloads(
        stress,
        start=m438.STRESS_START,
        end=m438.STRESS_END,
        jobs=jobs,
        timeframes=(*m438.TIMEFRAMES, TF15),
    )
    old_spot_syms = {p.symbol for p in old_spot_pl}
    spot_new = sorted(
        s
        for s in rule
        if store_symbol(s) not in old_spot_syms and store_symbol(s) in _root_symbols(SPOT_ROOT)
    )
    new_spot_pl = root_payloads(
        SPOT_ROOT, spot_new, (*m438.TIMEFRAMES, TF15), m438.STRESS_START, m438.STRESS_END, jobs
    )
    spot_stress_store = OhlcvStore(sk.root_db_path(stress))
    spot = market_from(
        "현물",
        [(old_spot_pl, spot_stress_store), (new_spot_pl, OhlcvStore(sk.root_db_path(SPOT_ROOT)))],
        start_ms=ms(m438.STRESS_START),
        end_ms=ms(m438.STRESS_END),
        entry_from_ms=ms(c.WINDOW_START),
        entry_to_ms=ms(m438.STRESS_END),
        btc_store=spot_stress_store,
        btc_from_ms=ms(c.STRESS_BTC_FROM),
    )
    notes.append(
        f"현물 후보 — 기존 루트 {len(old_spot_pl)}칸 · 새 종목 {len(spot_new)}개 "
        f"{len(new_spot_pl)}칸"
        f" ({time.monotonic() - t0:.0f}초)"
    )
    bounds = sorted(p.boundary_ms for p in fut.payloads)
    if bounds[-1] - bounds[0] > 7 * 86_400_000:
        raise AssertionError(f"앞/뒤 경계가 일주일 넘게 벌어졌다: {bounds[0]}~{bounds[-1]}")
    boundary_ms = bounds[len(bounds) // 2]

    # 현물 창 밖은 두 유니버스 모두 「그 종목에 현물 데이터가 있으면 쓴다」로 맞춘다 — WAN-438
    # 루트는 손으로 고른 21종목뿐이라(SOL·DOT·COMP·CRV·SNX·YFI 없음) 그대로 두면 31종목과 규칙
    # 유니버스가 현물 구간에서 다른 기준으로 비교된다. WAN-439 대조 검산은 옛 21종목으로 따로 한다.
    all_spot = {p.symbol for p in spot.payloads}
    universes = {
        U31: (set(SYMBOLS_31), set(SYMBOLS_31) & all_spot),
        URULE: (rule_store, rule_store & all_spot),
    }
    markets: dict[str, tuple[c.Market, c.Market]] = {}
    for u, (fs, ss) in universes.items():
        for v in VARIANTS:
            s_mk, f_mk = restrict(spot, ss), restrict(fut, fs)
            if v == "9TF":
                s_mk, f_mk = s_mk.without(TF15), f_mk.without(TF15)
            markets[label(u, v)] = (s_mk, f_mk)
            notes.append(
                f"{label(u, v)}: 선물 {len({e.symbol for e in f_mk.entries})}종목 · 후보 "
                f"{len(f_mk.entries)} / 현물 {len({e.symbol for e in s_mk.entries})}종목 · 후보 "
                f"{len(s_mk.entries)}"
            )
    placed: dict[tuple[str, str], tuple[list[w.PlacedTrade], list[w.PlacedTrade]]] = {}
    for key, (s_mk, f_mk) in markets.items():
        for fill in c.FILLS:
            placed[(key, fill)] = (
                c.place_arm(s_mk, c.ARM_B, fill, in_book=True),
                c.place_arm(f_mk, c.ARM_B, fill, in_book=True),
            )
    ck_s = restrict(spot, old_spot_syms)
    ck_f = restrict(fut, set(SYMBOLS_31))
    notes += checksum_31(
        {
            (label(U31, "+15m"), "bar_low"): (
                c.place_arm(ck_s, c.ARM_B, "bar_low", in_book=True),
                c.place_arm(ck_f, c.ARM_B, "bar_low", in_book=True),
            )
        },
        rows_ref=c.CSV_PATH,
    )

    rows: list[c.Row] = []
    for ruler, fit_fill, target, fixed in RULERS:
        sizes: dict[str, float] = {}
        for key in markets:
            if fixed is not None:
                sizes[key] = fixed
                continue
            s_fit, f_fit = placed[(key, fit_fill)]

            def mdd_at(
                r: float, s: list[w.PlacedTrade] = s_fit, f: list[w.PlacedTrade] = f_fit
            ) -> float:
                return c.chain(s, f, r)[1]

            assert target is not None
            sizes[key] = c.fit_risk(mdd_at, target=target)
        for key, (s_mk, f_mk) in markets.items():
            layered = {
                (e.symbol, e.timeframe, int(e.cand.entry_time))
                for mk in (s_mk, f_mk)
                for e, mult in zip(mk.entries, mk.layer(c.ARM_B), strict=True)
                if mult != 1.0
            }
            u31_key = label(U31, key.split(" ")[-1])
            bases = {
                f"{ruler} · 자기 맞춤": sizes[key],
                f"{ruler} · {U31} 맞춤 크기": sizes[u31_key],
            }
            for basis, risk in bases.items():
                for fill in c.FILLS:
                    s, f = placed[(key, fill)]
                    rows += c.segment_rows(key, True, fill, basis, risk, s, f, boundary_ms, layered)
    curve = curve_rows(spot, fut, rule_store, boundary_ms)
    pd.DataFrame(curve).to_csv(CURVE_PATH, index=False)
    notes.append(f"곡선 {len(curve)}점 ({time.monotonic() - t0:.0f}초)")
    notes.append(f"총 {time.monotonic() - t0:.0f}초")
    return rows, notes


def curve_rows(
    spot: c.Market, fut: c.Market, rule_store: set[str], boundary_ms: int
) -> list[dict[str, object]]:
    """규칙 유니버스에서 N종목을 무작위로 뽑아(시드 고정) ① 맞춤 연환산을 잰다 — 두 변형 모두.

    현물 창 밖은 뽑힌 종목 중 현물이 있는 것만 쓴다(몰림 신호 수는 부분집합의 후보로 다시 센다).
    """
    import random

    del boundary_ms
    rng = random.Random(CURVE_SEED)
    pool = sorted(rule_store)
    draws = [
        (n, d, frozenset(rng.sample(pool, n))) for n in CURVE_SIZES for d in range(CURVE_DRAWS)
    ]
    draws.append((len(pool), 0, frozenset(pool)))
    years = c._years(ms(c.WINDOW_START), ms(harness.DEFAULT_END))
    out: list[dict[str, object]] = []
    for v in VARIANTS:
        for n, d, chosen in draws:
            s_mk, f_mk = restrict(spot, set(chosen)), restrict(fut, set(chosen))
            if v == "9TF":
                s_mk, f_mk = s_mk.without(TF15), f_mk.without(TF15)
            s = c.place_arm(s_mk, c.ARM_B, "bar_low", in_book=True)
            f = c.place_arm(f_mk, c.ARM_B, "bar_low", in_book=True)

            def mdd_at(r: float, s: list[w.PlacedTrade] = s, f: list[w.PlacedTrade] = f) -> float:
                return c.chain(s, f, r)[1]

            risk = c.fit_risk(mdd_at, target=0.35)
            total, _ = c.chain(s, f, risk)
            out.append(
                {
                    "variant": v,
                    "n": n,
                    "draw": d,
                    "trades": len(s) + len(f),
                    "risk": risk,
                    "total_return": total,
                    "cagr": c.cagr(total, years),
                    "symbols": " ".join(sorted(x.split("/")[0] for x in chosen)),
                }
            )
    return out


def _root_symbols(root: Path) -> set[str]:
    db = sk.root_db_path(root)
    if not db.exists():
        return set()
    import sqlite3

    with sqlite3.connect(db) as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM ohlcv")}


def checksum_31(
    placed: dict[tuple[str, str], tuple[list[w.PlacedTrade], list[w.PlacedTrade]]],
    rows_ref: Path,
) -> list[str]:
    """31종목 · +15m · B · ① 맞춤 크기 = WAN-439 §8 B+15m(같은 입력이면 같은 수)."""
    s, f = placed[(label(U31, "+15m"), "bar_low")]
    risk = c.fit_risk(lambda r: c.chain(s, f, r)[1], target=0.35)
    ref = 0.02915  # WAN-439 §8 표기(소수 넷째 자리) — 아래는 정확한 값을 CSV에서 읽는다
    b15 = REPORT_DIR / "wan439_b15m.csv"
    if b15.exists():
        frame = pd.read_csv(b15)
        hit = frame[
            (frame.arm == "B +15m")
            & (frame.size_basis == "① 미끄러짐 · MDD 35% · 자기 맞춤")
            & (frame.segment == c.SEG_CHAIN)
            & (frame.fill == "bar_low")
        ]
        if len(hit) == 1:
            ref = float(hit.risk.iloc[0])
            if abs(ref - risk) > 1e-12:
                raise AssertionError(f"31종목 +15m B가 WAN-439 §8과 다르다: {risk} vs {ref}")
            return [f"31종목 +15m · B ① 크기 {risk:.4%} ≡ WAN-439 §8 (차 {abs(ref - risk):.2e})"]
    del rows_ref
    return [f"31종목 +15m · B ① 크기 {risk:.4%} (WAN-439 §8 CSV 없음 — 대조 건너뜀)"]


# ---------------------------------------------------------------------------
# 요약
# ---------------------------------------------------------------------------


def render(rows: Sequence[c.Row], notes: Sequence[str], universe: pd.DataFrame) -> str:
    inc = universe[universe.included]
    out = [
        "# WAN-440 — 스토캐스틱 팔 종목 확대(규칙 유니버스)",
        "",
        "> 자동 생성(`uv run python -m backtest.wan440_universe --part run`). "
        "결정문 `docs/decisions/wan440.md`.",
        "> 🚨 채택 근거 아님 · 기본값·토대·페이퍼 규칙 불변.",
        "",
        f"대상 규칙: 바이낸스 USDT 무기한 · 첫 1분봉 < {LISTED_BEFORE} UTC · 상폐 포함 → "
        f"**{len(inc)}종목**"
        f"(31종목 중 {int(inc.in_31.sum())}개 포함 · 새 종목 {int((~inc.in_31).sum())}개). "
        f"목록 `backtest/reports/wan440_universe.csv`.",
        "",
    ]
    for ruler, fit_fill, _t, _f in RULERS:
        out += [f"## {ruler}", ""]
        out += [
            "| 유니버스 | 체결 | 크기 | " + " | ".join(c.SEGMENTS) + " |",
            "|" + "---|" * (3 + len(c.SEGMENTS)),
        ]
        for v in VARIANTS:
            for u in (U31, URULE):
                for fill in (fit_fill, *[x for x in c.FILLS if x != fit_fill]):
                    seg = {
                        r.segment: r
                        for r in rows
                        if (r.arm, r.size_basis, r.fill)
                        == (label(u, v), f"{ruler} · 자기 맞춤", fill)
                    }
                    if not seg:
                        continue
                    risk = next(iter(seg.values())).risk
                    cells = " | ".join(c._cell(seg[s]) for s in c.SEGMENTS)
                    out.append(f"| {label(u, v)} | {w._FILL_LABEL[fill]} | {risk:.2%} | {cells} |")
        out.append("")
        if _t is not None:
            for v in VARIANTS:
                ok, lines = verdict(rows, ruler, v)
                out.append(f"**{v}: 규칙 유니버스 vs 31종목 — {'통과' if ok else '불통과'}**")
                out.append("")
                out += [f"* {line}" for line in lines]
                out.append("")
    if CURVE_PATH.exists():
        cv = pd.read_csv(CURVE_PATH)
        out += [
            "## 종목 수 곡선 — ① 미끄러짐 · MDD 35% 맞춤 연환산 (참고 · 판정 아님)",
            "",
            f"규칙 유니버스 안에서 무작위 부분집합(시드 {CURVE_SEED} · N마다 {CURVE_DRAWS}회) · "
            "53은 전체 한 번.",
            "",
            "| 변형 | N | 거래 중앙 | 연환산 중앙 | 범위 |",
            "|---|--:|--:|--:|---|",
        ]
        for (v, n), g in cv.groupby(["variant", "n"], sort=False):
            out.append(
                f"| {v} | {n} | {int(g.trades.median()):,} | {g.cagr.median():+.1%} | "
                f"{g.cagr.min():+.1%} ~ {g.cagr.max():+.1%} |"
            )
        out.append("")
    out += ["## 실행 기록", ""] + [f"* {n}" for n in notes] + [""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--part", choices=("list", "load", "run", "summary"), required=True)
    ap.add_argument("--jobs", type=int, default=None)
    args = ap.parse_args(argv)
    jobs = args.jobs if args.jobs is not None else harness.default_jobs()
    if args.part == "list":
        urows = build_universe()
        pd.DataFrame([dataclasses.asdict(r) for r in urows]).to_csv(UNIVERSE_PATH, index=False)
        inc = [r for r in urows if r.included]
        print(
            f"대상 {len(inc)}종목 · 31종목 중 {sum(r.in_31 for r in inc)}개 · "
            f"제외 {len(urows) - len(inc)}개"
        )
        return 0
    if args.part == "load":
        rule = rule_symbols()
        new_fut = sorted(s for s in rule if store_symbol(s) not in set(SYMBOLS_31))
        end_fut = ms(harness.DEFAULT_END)
        rows_f, mb_f, sec_f = load_root(
            FUT_ROOT,
            new_fut,
            FUT_NATIVE_TFS,
            FUT_FIRST_MONTH,
            FUT_LAST_MONTH,
            market="um",
            end_ms=end_fut,
            extra_1m=(BTC,),
        )
        n3 = aggregate_3h(FUT_ROOT, new_fut)
        with FundingRateStore(sk.root_db_path(FUT_ROOT)) as fstore:
            nf = sum(
                fstore.upsert_rates(funding_rates(s, FUT_FIRST_MONTH, FUT_LAST_MONTH))
                for s in new_fut
            )
        stress_syms = _root_symbols(m438.DEFAULT_ROOTS / "stress")
        spot_cands = [s for s in rule if store_symbol(s) not in stress_syms]
        have_spot = [
            s for s in spot_cands if sk.select_months(sk.list_months(s, "1m"), "2017-08", "2020-08")
        ]
        rows_s, mb_s, sec_s = load_root(
            SPOT_ROOT,
            have_spot,
            SPOT_NATIVE_TFS,
            "2017-08",
            "2020-08",
            market="spot",
            end_ms=ms(m438.STRESS_END),
            extra_1m=(BTC,),
        )
        q = sk.quarantine_impossible_ticks(SPOT_ROOT)
        pd.DataFrame([dataclasses.asdict(r) for r in (*rows_f, *rows_s)]).to_csv(
            LOAD_PATH, index=False
        )
        info = {
            "futures_new": new_fut,
            "futures_mb": round(mb_f, 1),
            "futures_sec": round(sec_f),
            "agg_3h": n3,
            "funding_rows": nf,
            "spot_new": have_spot,
            "spot_mb": round(mb_s, 1),
            "spot_sec": round(sec_s),
            "quarantined": len(q),
        }
        (REPORT_DIR / "wan440_load_info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=1)
        )
        print(json.dumps(info, ensure_ascii=False))
        return 0
    universe = pd.read_csv(UNIVERSE_PATH)
    if args.part == "summary":
        rows = c.rows_from_frame(pd.read_csv(CSV_PATH))
        SUMMARY_PATH.write_text(render(rows, [], universe), "utf-8")
        return 0
    rows, notes = run(jobs)
    c.rows_frame(rows).to_csv(CSV_PATH, index=False)
    SUMMARY_PATH.write_text(render(rows, notes, universe), "utf-8")
    print(SUMMARY_PATH.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
