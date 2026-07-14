"""범용 백테스트 CLI (WAN-101) — `python -m backtest.run`.

실험 하나마다 전용 스크립트를 새로 짜서 PR로 올리던 구조를 끝낸다. "익절 1.5R 말고
2R이면?" 같은 질문에 티켓 → 개발 → PR 사이클 대신 **한 줄**로 답한다:

```
uv run python -m backtest.run --tp-r 1.0,1.5,2.0,3.0
```

값에 콤마를 주면 데카르트 곱으로 격자를 돌고 조합별 1행을 낸다. 축은 심볼 · TF ·
진입 방식 · 익절 R · 지정가 오프셋 · 체결 가정 · 시드다.

## 기본값 = 채택 기본값

인자를 아무것도 주지 않으면 `ConfluenceParams()`(WAN-95/87 채택 기본값: 지정가 진입 +
실시간 RSI + 롱 온리 + 볼린저 + 고정 1.5R) 그대로 돈다. CLI가 자기만의 기본값을 갖지
않는 이유는 그것이 조용히 갈라지기 때문이다 — `tests/test_run_cli.py`가 CLI 기본
파라미터 == WAN-95/96/99 리포트의 기준선 파라미터임을 고정한다.

## 사용 예시

```bash
# 1. 익절 R 스윕 — 1.5R이 맞는 값인가
python -m backtest.run --tp-r 1.0,1.5,2.0,3.0

# 2. 체결 가정 비교 — "닿으면 체결"이 얼마나 낙관인가
python -m backtest.run --tf 15m --fill baseline,pen_5bp,pen_5bp_drop_50

# 3. TF 비교
python -m backtest.run --tf 15m,1h,4h,1d

# 4. 심볼 비교 + CSV 저장
python -m backtest.run --symbol BTCUSDT,ETHUSDT,SOLUSDT --format csv --out /tmp/x.csv

# 5. OOS 검증 — IS에서 좋았던 게 OOS로 넘어오는가
python -m backtest.run --tp-r 1.0,1.5,2.0 --oos
```

## 진입 경로

`--entry-mode close`는 A안(`backtest.sweep.evaluate` → `BacktestEngine`),
`zone_limit`(기본)은 B안(`run_zone_limit_backtest_verbose`)을 탄다. `entry_mode`는
라벨이 아니라 **경로 스위치**이며(WAN-95), 불일치는 엔진이 `ValueError`로 거부한다.
두 방식을 한 표에 같이 올리면 종가 성과를 1분봉 커버 창으로 한정해 기간을 맞춘다
(`--fair-window`, 기본 자동).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from backtest.harness import (
    BASELINE_FILL,
    CACHE_DIR,
    DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    DEFAULT_YEARS,
    ENTRY_MODES,
    FILL_PRESETS,
    FORMATS,
    FillPreset,
    RunRow,
    build_config,
    build_params,
    build_row,
    detect_order_blocks,
    fill_preset,
    iter_seeds,
    load_market_data,
    normalize_symbol,
    render,
    run_once,
    segments_for,
    slice_market,
    write_output,
)

# --------------------------------------------------------------------------- #
# 인자 파싱 헬퍼
# --------------------------------------------------------------------------- #


def split_list(text: str) -> tuple[str, ...]:
    """`"a,b , c"` → `("a", "b", "c")`. 빈 조각은 버린다."""
    return tuple(part.strip() for part in text.split(",") if part.strip())


def split_floats(text: str, *, label: str) -> tuple[float, ...]:
    """콤마 구분 실수 목록. 격자 축은 중복을 접고 순서를 유지한다."""
    values: list[float] = []
    for part in split_list(text):
        try:
            value = float(part)
        except ValueError as exc:
            raise ValueError(f"{label}에 숫자가 아닌 값이 있습니다: {part!r}") from exc
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError(f"{label}이 비어 있습니다.")
    return tuple(values)


def split_ints(text: str, *, label: str) -> tuple[int, ...]:
    values: list[int] = []
    for part in split_list(text):
        try:
            value = int(part)
        except ValueError as exc:
            raise ValueError(f"{label}에 정수가 아닌 값이 있습니다: {part!r}") from exc
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError(f"{label}이 비어 있습니다.")
    return tuple(values)


def parse_date_ms(text: str) -> int:
    """`YYYY-MM-DD`(UTC) → epoch ms."""
    try:
        return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    except ValueError as exc:
        raise ValueError(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {text!r}") from exc


# --------------------------------------------------------------------------- #
# 격자
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Grid:
    """격자 축 정의. 콤마로 준 값들의 데카르트 곱이 실행 단위다."""

    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    entry_modes: tuple[str, ...]
    take_profit_rs: tuple[float, ...]
    offsets_bps: tuple[float, ...]
    fills: tuple[FillPreset, ...]
    seeds: tuple[int, ...] | None = None
    """탈락 시드 오버라이드. None이면 프리셋의 시드를 쓴다."""
    short_enabled: bool | None = None

    def __post_init__(self) -> None:
        for mode in self.entry_modes:
            if mode not in ENTRY_MODES:
                raise ValueError(f"알 수 없는 진입 방식: {mode!r} (지원: {', '.join(ENTRY_MODES)})")
        # 종가 진입은 지정가 노브를 쓰지 않는다. 격자에 섞여 있으면 조용히 무시하는
        # 대신 여기서 막는다 — 무시하면 `--offset-bps 5`가 아무 일도 하지 않은 표에
        # `off_bp=5` 라벨만 붙는다(WAN-95가 고친 바로 그 거짓말).
        if "close" in self.entry_modes:
            if self.offsets_bps != (0.0,):
                raise ValueError(
                    "--entry-mode close와 --offset-bps를 같이 줄 수 없습니다. "
                    "오프셋은 지정가 주문에만 얹힙니다."
                )
            if tuple(f.name for f in self.fills) != (BASELINE_FILL.name,):
                raise ValueError(
                    "--entry-mode close와 --fill을 같이 줄 수 없습니다. "
                    "종가 진입은 탭이 곧 진입이라 미체결 개념이 없습니다."
                )

    @property
    def needs_1m(self) -> bool:
        return "zone_limit" in self.entry_modes

    @property
    def mixes_entry_modes(self) -> bool:
        return len(set(self.entry_modes)) > 1


@dataclass(frozen=True)
class Combo:
    """격자의 한 셀(심볼·TF를 뺀 파라미터 조합)."""

    entry_mode: str
    take_profit_r: float
    offset_bps: float
    fill: FillPreset
    seed: int


def iter_combos(grid: Grid) -> list[Combo]:
    """격자의 모든 조합을 결정적 순서로 열거한다."""
    combos: list[Combo] = []
    for entry_mode in grid.entry_modes:
        for take_profit_r in grid.take_profit_rs:
            for offset_bps in grid.offsets_bps:
                for fill in grid.fills:
                    for seed in iter_seeds(fill, grid.seeds):
                        combos.append(
                            Combo(
                                entry_mode=entry_mode,
                                take_profit_r=take_profit_r,
                                offset_bps=offset_bps,
                                fill=fill,
                                seed=seed,
                            )
                        )
    return combos


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunOptions:
    """격자 밖의 실행 설정(데이터 창·비용·구간)."""

    years: float = DEFAULT_YEARS
    start_ms: int | None = None
    end_ms: int | None = None
    funding: bool = True
    fee_rate: float | None = None
    maker_fee_rate: float | None = None
    slippage: float | None = None
    oos: bool = False
    walkforward: int = 0
    fair_window: bool | None = None
    """None이면 자동 — 한 표에 종가·지정가가 같이 있을 때만 켠다."""
    db_path: str = DB_PATH
    cache_dir: str = CACHE_DIR


def run_grid(
    grid: Grid,
    options: RunOptions,
    *,
    log: bool = True,
) -> list[RunRow]:
    """격자를 돌아 조합별 1행을 낸다.

    (심볼, TF)마다 데이터를 한 번만 로드하고, 구간마다 오더블록을 한 번만 탐지해 그
    구간의 조합들이 공유한다 — 탐지는 컨플루언스 파라미터와 무관하므로 결과는 같고
    실행 시간만 줄어든다.
    """
    combos = iter_combos(grid)
    segments = segments_for(oos=options.oos, walkforward=options.walkforward)
    fair_window = grid.mixes_entry_modes if options.fair_window is None else options.fair_window
    rows: list[RunRow] = []

    for symbol in grid.symbols:
        for timeframe in grid.timeframes:
            market = load_market_data(
                symbol,
                timeframe,
                years=options.years,
                start_ms=options.start_ms,
                end_ms=options.end_ms,
                need_1m=grid.needs_1m or fair_window,
                funding=options.funding,
                db_path=options.db_path,
                cache_dir=options.cache_dir,
            )
            if market.empty:
                _log(log, f"[run] {symbol} {timeframe}: 상위TF 데이터 없음 — 건너뜀")
                continue
            if grid.needs_1m and market.df_1m.empty:
                _log(
                    log,
                    f"[run] {symbol} {timeframe}: 1분봉 없음 — 지정가 평가 불가, 건너뜀",
                )
                continue
            cfg = build_config(
                timeframe,
                fee_rate=options.fee_rate,
                maker_fee_rate=options.maker_fee_rate,
                slippage=options.slippage,
                funding_enabled=options.funding,
            )
            for segment in segments:
                window = slice_market(market, segment)
                if window.empty:
                    continue
                ob_result = detect_order_blocks(window)
                for combo in combos:
                    if combo.entry_mode == "zone_limit" and window.df_1m.empty:
                        continue
                    params = build_params(
                        entry_mode=combo.entry_mode,
                        take_profit_r=combo.take_profit_r,
                        offset_bps=combo.offset_bps,
                        fill=combo.fill,
                        seed=combo.seed,
                        short_enabled=grid.short_enabled,
                    )
                    outcome = run_once(
                        window,
                        params=params,
                        cfg=cfg,
                        order_block_result=ob_result,
                        fair_window=fair_window,
                    )
                    rows.append(
                        build_row(
                            outcome,
                            window,
                            segment=segment,
                            params=params,
                            fill_name=combo.fill.name,
                        )
                    )
            _log(
                log,
                f"[run] {symbol} {timeframe}: {len(market.htf_df)}봉, "
                f"1분봉 {len(market.df_1m)}행, 펀딩 {len(market.funding_rates)}건 "
                f"→ {len(combos) * len(segments)}조합",
            )
    return rows


def _log(enabled: bool, message: str) -> None:
    """진행 로그는 stderr로 — stdout은 표/CSV/JSON 전용이라 파이프를 오염시키면 안 된다."""
    if enabled:
        print(message, file=sys.stderr)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backtest.run",
        description="범용 백테스트 실행기 — 값에 콤마를 주면 격자 스윕(WAN-101).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  python -m backtest.run --tp-r 1.0,1.5,2.0,3.0\n"
            "  python -m backtest.run --tf 15m --fill baseline,pen_5bp,pen_5bp_drop_50\n"
            "  python -m backtest.run --symbol BTCUSDT,ETHUSDT --oos --format csv --out x.csv\n"
        ),
    )
    data = parser.add_argument_group("데이터")
    data.add_argument(
        "--symbol",
        default=",".join(DEFAULT_SYMBOLS),
        help=f"심볼(콤마 복수). 축약형(BTCUSDT) 허용. 기본 {','.join(DEFAULT_SYMBOLS)}",
    )
    data.add_argument(
        "--tf",
        default=",".join(DEFAULT_TIMEFRAMES),
        help=f"타임프레임(콤마 복수). 기본 {','.join(DEFAULT_TIMEFRAMES)}",
    )
    data.add_argument("--years", type=float, default=DEFAULT_YEARS, help="최근 N년 (기본 3)")
    data.add_argument("--start", help="시작일 YYYY-MM-DD (--years 대신)")
    data.add_argument("--end", help="종료일 YYYY-MM-DD (--years 대신)")
    data.add_argument("--db-path", default=DB_PATH)
    data.add_argument("--cache-dir", default=CACHE_DIR)

    strategy = parser.add_argument_group("전략 축 (콤마 복수 = 격자)")
    strategy.add_argument(
        "--entry-mode",
        default="zone_limit",
        help="close(A안) / zone_limit(B안, 기본). 콤마로 둘 다 주면 한 표에서 비교",
    )
    strategy.add_argument("--tp-r", help="고정 R 익절 배수(기본: 채택 기본값 1.5)")
    strategy.add_argument("--offset-bps", help="지정가 오프셋 bp(기본 0). 지정가 전용")
    strategy.add_argument(
        "--fill",
        help=f"체결 가정(기본 baseline). 지원: {', '.join(p.name for p in FILL_PRESETS)}",
    )
    strategy.add_argument(
        "--fill-penetration-bps", type=float, help="--fill 대신 관통 요구를 직접 지정"
    )
    strategy.add_argument("--fill-dropout-rate", type=float, help="--fill 대신 탈락률을 직접 지정")
    strategy.add_argument("--seeds", help="탈락 추첨 시드(콤마 복수). 기본은 프리셋 시드")

    side = strategy.add_mutually_exclusive_group()
    side.add_argument("--long-only", action="store_true", help="숏 비활성화(채택 기본값, WAN-87)")
    side.add_argument("--short-enabled", action="store_true", help="숏 활성화")

    costs = parser.add_argument_group("비용")
    costs.add_argument(
        "--funding",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="펀딩비 반영 (기본 반영)",
    )
    costs.add_argument("--fee", type=float, help="테이커 수수료율(기본 0.0004)")
    costs.add_argument("--maker-fee", type=float, help="메이커 수수료율(기본 0.0002)")
    costs.add_argument("--slippage", type=float, help="테이커 슬리피지(기본 0.0005)")

    validation = parser.add_argument_group("과최적화 방어")
    validation.add_argument("--oos", action="store_true", help="IS(앞 2/3)/OOS(뒤 1/3) 분할 실행")
    validation.add_argument(
        "--walkforward", type=int, default=0, metavar="N", help="N개 롤링 창으로 IS/OOS 반복"
    )
    validation.add_argument(
        "--fair-window",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="종가 성과를 1분봉 커버 창으로 한정(기본: 진입 방식이 섞일 때만 자동)",
    )

    output = parser.add_argument_group("출력")
    output.add_argument("--format", default="table", choices=list(FORMATS))
    output.add_argument("--out", help="결과를 파일로 저장(경로)")
    output.add_argument("--quiet", action="store_true", help="진행 로그(stderr) 끄기")
    return parser


def _fills_from_args(args: argparse.Namespace) -> tuple[FillPreset, ...]:
    """`--fill` 프리셋 또는 `--fill-penetration-bps`/`--fill-dropout-rate` 직접 지정."""
    custom = args.fill_penetration_bps is not None or args.fill_dropout_rate is not None
    if custom and args.fill:
        raise ValueError(
            "--fill과 --fill-penetration-bps/--fill-dropout-rate는 함께 쓸 수 없습니다."
        )
    if custom:
        return (
            FillPreset(
                name="custom",
                penetration_bps=args.fill_penetration_bps or 0.0,
                dropout_rate=args.fill_dropout_rate or 0.0,
                seeds=(0,),
                note="CLI 직접 지정",
            ),
        )
    if not args.fill:
        return (BASELINE_FILL,)
    return tuple(fill_preset(name) for name in split_list(args.fill))


def grid_from_args(args: argparse.Namespace) -> Grid:
    """파싱한 인자를 격자로. 잘못된 조합은 여기서 `ValueError`로 걸러진다."""
    short_enabled: bool | None = None
    if args.short_enabled:
        short_enabled = True
    elif args.long_only:
        short_enabled = False
    return Grid(
        symbols=tuple(normalize_symbol(s) for s in split_list(args.symbol)),
        timeframes=split_list(args.tf),
        entry_modes=split_list(args.entry_mode),
        take_profit_rs=(
            split_floats(args.tp_r, label="--tp-r") if args.tp_r else (_default_tp_r(),)
        ),
        offsets_bps=(
            split_floats(args.offset_bps, label="--offset-bps") if args.offset_bps else (0.0,)
        ),
        fills=_fills_from_args(args),
        seeds=split_ints(args.seeds, label="--seeds") if args.seeds else None,
        short_enabled=short_enabled,
    )


def _default_tp_r() -> float:
    """익절 R을 안 주면 채택 기본값 그대로."""
    return build_params().take_profit_r


def options_from_args(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        years=args.years,
        start_ms=parse_date_ms(args.start) if args.start else None,
        end_ms=parse_date_ms(args.end) if args.end else None,
        funding=args.funding,
        fee_rate=args.fee,
        maker_fee_rate=args.maker_fee,
        slippage=args.slippage,
        oos=args.oos,
        walkforward=args.walkforward,
        fair_window=args.fair_window,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        grid = grid_from_args(args)
        options = options_from_args(args)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    if args.oos and args.walkforward:
        print("오류: --oos와 --walkforward는 함께 쓸 수 없습니다.", file=sys.stderr)
        return 2

    rows = run_grid(grid, options, log=not args.quiet)
    text = render(rows, args.format)
    if args.out:
        path = write_output(text, args.out)
        _log(not args.quiet, f"[run] 저장: {path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
