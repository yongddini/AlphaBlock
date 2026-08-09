"""채택 레버리지 북 CLI 경로 테스트 (WAN-213).

두 층으로 고정한다:

* **인자 없는 데이터** — 채택 기본값(`LeverageBookParams()` = cap_only 5배)·인자 파싱
  (`_book_from_args`)·구간 매핑·거부 규칙. 라벨이 아니라 **동작**으로.
* **실데이터(있을 때만)** — CLI 북 경로가 측정 리포트(`wan180.build_rows`)의 aggregation과
  **비트 단위로 같은 수**를 낸다는 검산(WAN-213 완료기준의 「wan180 채택 셀과 검산 비트
  일치」). 창을 작게 잡아 CI에서도 몇 초 안에 돈다(실데이터 없으면 skip).
"""

from __future__ import annotations

import pytest

from backtest import book_cli
from backtest.harness import (
    SEGMENT_FULL,
    SEGMENT_IS,
    SEGMENT_OOS,
    SEGMENT_OOS_WARM,
    load_market_data,
    normalize_symbol,
)
from backtest.leverage_book import LeverageBookParams
from backtest.run import (
    ADOPTED_BOOK,
    _book_from_args,
    _book_segments,
    _resolve_reentry,
    build_parser,
    main,
)

# --------------------------------------------------------------------------- #
# 채택 기본값 = 라벨이 아니라 값
# --------------------------------------------------------------------------- #


def test_default_book_params_are_the_adopted_book() -> None:
    """`LeverageBookParams()` 기본값이 채택 북(cap_only 5배)이다 — `ConfluenceParams()`와 대칭."""
    assert LeverageBookParams() == LeverageBookParams(
        leverage_multiple=5.0, leverage_mode="cap_only"
    )
    assert ADOPTED_BOOK.leverage_mode == "cap_only"
    assert ADOPTED_BOOK.leverage_multiple == 5.0


def test_bare_positions_default_to_the_book() -> None:
    """인자 없는 실행은 북을 돈다(WAN-213) — per-cell 단일 포지션이 아니다."""
    args = build_parser().parse_args([])
    assert _book_from_args(args) == ADOPTED_BOOK


def test_positions_book_token_selects_the_book() -> None:
    args = build_parser().parse_args(["--positions", "book"])
    assert _book_from_args(args) == ADOPTED_BOOK


def test_positions_single_and_numbers_are_not_the_book() -> None:
    """`single`/숫자는 per-cell 경로(북 아님) — `grid_from_args`가 처리한다."""
    assert _book_from_args(build_parser().parse_args(["--positions", "single"])) is None
    assert _book_from_args(build_parser().parse_args(["--positions", "3"])) is None
    assert _book_from_args(build_parser().parse_args(["--positions", "single,3"])) is None


def test_coordinate_only_runs_are_still_the_book() -> None:
    """정본 리포트(`--oos-warm`)와 좌표만 준 실행은 북이다 — 전략 축이 없으므로."""
    assert _book_from_args(build_parser().parse_args(["--oos-warm"])) == ADOPTED_BOOK
    assert _book_from_args(build_parser().parse_args(["--oos"])) == ADOPTED_BOOK
    assert _book_from_args(build_parser().parse_args(["--symbol", "BTCUSDT", "--tf", "1h"])) == (
        ADOPTED_BOOK
    )


def test_per_cell_axes_without_positions_fall_back_to_single() -> None:
    """전략·비용·거래별 축을 주면(--positions 없이) per-cell 단일로 접힌다 — 북이 그 축을
    표현하지 못하므로, `--tp-r`·`--fill` 스윕은 매번 `--positions single`을 붙일 필요가 없다."""
    for argv in (
        ["--tp-r", "2.0"],
        ["--fill", "pen_5bp"],
        ["--max-zone-width-atr", "none"],
        ["--limit-valid-bars", "6"],
        ["--trades", "x.csv"],
        ["--walkforward", "3"],
        ["--no-funding"],
    ):
        assert _book_from_args(build_parser().parse_args(argv)) is None, argv


def test_positions_book_cannot_mix_with_per_cell() -> None:
    args = build_parser().parse_args(["--positions", "book,single"])
    with pytest.raises(ValueError, match="단독으로만"):
        _book_from_args(args)


def test_book_segments_map_oos_and_warm() -> None:
    assert _book_segments(oos=False, warm_oos=False) == (SEGMENT_FULL,)
    assert _book_segments(oos=True, warm_oos=False) == (SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS)
    assert _book_segments(oos=False, warm_oos=True) == (
        SEGMENT_FULL,
        SEGMENT_IS,
        SEGMENT_OOS_WARM,
        SEGMENT_OOS,
    )


# --------------------------------------------------------------------------- #
# 거부 규칙: 북 모드는 채택 기본값만 (라벨만 붙는 조용한 무시 방지 — WAN-95 교훈)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "argv",
    [
        ["--positions", "book", "--tp-r", "2.0"],
        ["--positions", "book", "--offset-bps", "5"],
        ["--positions", "book", "--fill", "pen_5bp"],
        ["--positions", "book", "--max-zone-width-atr", "none"],
        ["--positions", "book", "--limit-valid-bars", "6"],
        ["--positions", "book", "--walkforward", "3"],
        ["--positions", "book", "--years", "3"],
        ["--positions", "book", "--no-funding"],
        ["--positions", "book", "--persist"],
    ],
)
def test_book_mode_rejects_unwired_axes(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """전략·비용·거래별-출력 축은 북 모드에서 종료 코드 2로 거부된다(데이터 로딩 전)."""
    assert main(argv) == 2
    assert "북 모드" in capsys.readouterr().err


def test_book_mode_rejects_mixed_positions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--positions", "book,single"]) == 2
    assert "단독으로만" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 실데이터 검산 (있을 때만)
# --------------------------------------------------------------------------- #

_START = "2024-01-01"
_END = "2024-04-01"
_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
_TFS = ["15m", "1h"]


def _require_real_data() -> None:
    """대조 심볼의 봉이 실제로 있을 때만 돈다(없으면 skip — CI 기본).

    파일 존재가 아니라 **실제 데이터 유무**로 판정한다(빈 DB에서 0행 실패를 막는다).
    """
    market = load_market_data(
        normalize_symbol("BTCUSDT"),
        "1h",
        start_ms=None,
        end_ms=None,
        need_1m=True,
    )
    if market.empty:
        pytest.skip("BTCUSDT 1h 실데이터가 없어 북 검산을 건너뜁니다(CI 기본).")


def test_book_cli_matches_wan180_aggregation_bit_for_bit() -> None:
    """CLI 북 경로가 wan180 측정 aggregation과 비트 단위로 같다(WAN-213 완료기준).

    같은 payloads에서 `book_cli.build_book_rows`(채택 북)와 `wan180.build_rows`의
    `nine/both/cap_only/5.0/full` 셀을 대조한다. 둘 다 `run_cells`·`_segment_cells`·
    `run_leverage_book`을 **같은 인자로** 부르므로 구성상 같아야 한다 — 이 테스트가 그
    「구성상 같음」을 실데이터 위에서 못 박는다(따로 재현 로직을 짜면 갈라진다는 WAN-95 교훈).

    ⚠️ 이것은 wan180 CSV 셀의 **작은 창 대리**다. 6년·9종목·both 전체 셀의 비트 일치는
    `uv run python -m backtest.wan180_leverage_book_nine`을 돌려 확인한다(세션에서 돌리기엔
    무겁다 — docs/decisions/wan213.md §검산).
    """
    _require_real_data()
    from backtest.wan169_leverage_book import run_cells
    from backtest.wan180_leverage_book_nine import build_rows

    payloads = run_cells(_SYMBOLS, _TFS, start=_START, end=_END, jobs=1)
    cli_row = book_cli.build_book_rows(
        payloads, book=ADOPTED_BOOK, segments=[SEGMENT_FULL], start_ms=0, end_ms=1
    )[0]
    book_rows, _, _ = build_rows(payloads)
    wan = next(
        r
        for r in book_rows
        if r.universe == "nine"
        and r.scope == "both"
        and r.arm == "book"
        and r.leverage_mode == "cap_only"
        and r.multiple == 5.0
        and r.segment == "full"
        and r.exclude_symbol == ""
    )
    assert cli_row.total_return == wan.total_return
    assert cli_row.num_trades == wan.num_trades
    assert cli_row.max_drawdown == wan.max_drawdown
    assert cli_row.win_rate == wan.win_rate
    assert cli_row.peak_concurrency == wan.peak_concurrency
    assert cli_row.max_concurrent_risk == wan.max_concurrent_risk
    assert cli_row.skipped_cell_busy == wan.skipped_cell_busy
    assert cli_row.skipped_notional == wan.skipped_notional
    assert cli_row.liquidation_events == wan.liquidation_events


def test_bare_cli_default_runs_the_book_not_single_position() -> None:
    """인자 없는 CLI가 실제로 북을 돈다(동작으로 고정 — 라벨만 붙는 실패 방지, WAN-95).

    per-cell 경로는 심볼·TF마다 한 행을 내지만, 북은 요청 칸 전체를 한 지갑으로 묶어
    구간당 한 행을 낸다. BTC+ETH 1h 두 칸이 한 행으로 접히고 그 거래 수가 두 칸 거래의
    합이면(명목 상한 미발동 구간) 공유 자본 회계를 탄 것이다.
    """
    _require_real_data()
    rows = book_cli.run_book(
        _SYMBOLS,
        ["1h"],
        start=_START,
        end=_END,
        book=ADOPTED_BOOK,
        segments=[SEGMENT_FULL],
        jobs=1,
        log=False,
    )
    assert len(rows) == 1  # 두 칸이 한 지갑 → 한 행.
    row = rows[0]
    assert row.num_cells == 2
    assert row.num_symbols == 2
    assert row.leverage_mode == "cap_only"
    assert row.leverage_multiple == 5.0


def test_reentry_arg_resolution() -> None:
    """`--reentry`가 미지정과 off를 가른다(WAN-273 완료기준 2 · 라벨 아닌 값).

    미지정(None)·on = 채택(band 켬), off = 끔, freeze/zone/band = 그 규칙으로 켬.
    """
    assert _resolve_reentry(None) == (True, "band")  # 미지정 = 채택 기본값
    assert _resolve_reentry("on") == (True, "band")  # on = 채택 규칙(band) 별칭
    assert _resolve_reentry("off") == (False, "band")  # 끔(규칙은 무의미)
    assert _resolve_reentry("freeze") == (True, "freeze")
    assert _resolve_reentry("zone") == (True, "zone")
    assert _resolve_reentry("band") == (True, "band")


def test_reentry_requires_book_mode(capsys: pytest.CaptureFixture[str]) -> None:
    """명시 재진입(on/band 등)은 북 전용 — per-cell 축과 함께 주면 종료 코드 2(WAN-261/273).

    ⚠️ 미지정·off는 per-cell에서도 「재진입 없음」이라 거부 대상이 아니다(WAN-273).
    """
    assert main(["--positions", "single", "--reentry", "on"]) == 2
    assert "북 모드 전용" in capsys.readouterr().err
    assert main(["--positions", "single", "--reentry", "band"]) == 2
    assert "북 모드 전용" in capsys.readouterr().err


def test_reentry_flag_alone_is_still_the_book() -> None:
    """재진입 토큰만 주면 여전히 북이다 — 북 축이라 `_book_from_args` 거부 대상이 아니다."""
    assert _book_from_args(build_parser().parse_args(["--reentry", "off"])) == ADOPTED_BOOK
    assert _book_from_args(build_parser().parse_args(["--reentry", "band"])) == ADOPTED_BOOK


def test_default_book_runs_band_reentry() -> None:
    """인자 없는 run_book() = 채택 북(band 재진입 켬, WAN-273) — off보다 거래가 는다.

    미지정 기본값이 band 켬임을 값으로 고정하고(라벨 아닌 동작), off와 실제로 갈림을 후보
    수(거래 수)로 확인한다.
    """
    _require_real_data()
    kw = dict(
        start=_START,
        end=_END,
        book=ADOPTED_BOOK,
        segments=[SEGMENT_FULL],
        jobs=1,
        log=False,
    )
    default = book_cli.run_book(_SYMBOLS, _TFS, **kw)[0]  # type: ignore[arg-type]
    band = book_cli.run_book(
        _SYMBOLS,
        _TFS,
        reentry=True,
        reentry_entry_rule="band",
        **kw,  # type: ignore[arg-type]
    )[0]
    off = book_cli.run_book(_SYMBOLS, _TFS, reentry=False, **kw)[0]  # type: ignore[arg-type]
    assert default.model_dump() == band.model_dump()  # 미지정 = band 켬
    # 이 창엔 실제로 재진입이 생겨 후보가 늘어야 한다(동작 고정 — 아니면 배선이 죽은 것).
    assert default.num_trades > off.num_trades


def test_reentry_off_reproduces_pre_wan273_book() -> None:
    """재진입 off ≡ WAN-273 이전 재진입-off 북(옛 CSV 비트 재현, 완료기준 2).

    off 경로가 base 후보만으로 도는지 후보 집합으로 고정한다(라벨이 아니라 동작) —
    `run_cells`(reentry=False) + `build_book_rows`(include_reentry=False)로 만든 「재진입 없는
    북」과 `run_book(reentry=False)`가 비트 일치한다.
    """
    _require_real_data()
    from backtest.run import parse_date_ms
    from backtest.wan169_leverage_book import run_cells

    base = run_cells(_SYMBOLS, _TFS, start=_START, end=_END, jobs=1)  # reentry=False 기본
    ref = book_cli.build_book_rows(
        base,
        book=ADOPTED_BOOK,
        segments=[SEGMENT_FULL, SEGMENT_OOS_WARM],
        start_ms=parse_date_ms(_START),
        end_ms=parse_date_ms(_END),
        include_reentry=False,
    )
    off = book_cli.run_book(
        _SYMBOLS,
        _TFS,
        start=_START,
        end=_END,
        book=ADOPTED_BOOK,
        segments=[SEGMENT_FULL, SEGMENT_OOS_WARM],
        jobs=1,
        log=False,
        reentry=False,
    )
    for a, b in zip(ref, off, strict=True):
        assert a.model_dump() == b.model_dump()


def test_book_warm_and_cold_oos_parity() -> None:
    """`--oos-warm` 구간이 북 경로에서 나온다(WAN-213 §3 · WAN-166 정본).

    따뜻(oos_warm 주 수치)과 차가움(oos 스트레스)이 둘 다 나오고, IS 후보는 full의
    앞부분이라 거래 수가 full 이하다(경계 검산).
    """
    _require_real_data()
    rows = book_cli.run_book(
        _SYMBOLS,
        _TFS,
        start=_START,
        end="2024-07-01",
        book=ADOPTED_BOOK,
        segments=[SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS],
        jobs=1,
        log=False,
    )
    by_seg = {r.segment: r for r in rows}
    assert set(by_seg) == {SEGMENT_FULL, SEGMENT_IS, SEGMENT_OOS_WARM, SEGMENT_OOS}
    assert by_seg[SEGMENT_IS].num_trades <= by_seg[SEGMENT_FULL].num_trades
