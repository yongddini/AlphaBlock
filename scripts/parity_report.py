"""오더블록 패리티 리포트 CLI (WAN-13).

fixture(TV 정답 데이터셋)를 로드해 우리 탐지기를 돌리고, 캔들+오더블록
오버레이 이미지를 저장한 뒤 패리티 리포트를 표준출력에 출력한다.

사용법::

    uv run python scripts/parity_report.py \\
        strategy/parity/fixtures/btcusdt_1h_bullish_sample.json \\
        --out-dir out/parity
"""

from __future__ import annotations

import argparse
from pathlib import Path

from strategy.order_blocks import detect_order_blocks
from strategy.parity.chart import render_order_block_chart
from strategy.parity.fixtures import load_fixture
from strategy.parity.report import compare_to_fixture


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="TV fixture JSON 경로")
    parser.add_argument(
        "--out-dir", type=Path, default=Path("out/parity"), help="이미지 저장 디렉터리"
    )
    parser.add_argument(
        "--price-tolerance-pct", type=float, default=0.05, help="top/bottom 허용 오차(%)"
    )
    parser.add_argument("--time-tolerance-ms", type=int, default=0, help="start_time 허용 오차(ms)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    fixture = load_fixture(args.fixture)
    df = fixture.to_dataframe()
    result = detect_order_blocks(df, fixture.params)

    image_path = render_order_block_chart(
        df,
        result.order_blocks,
        args.out_dir / f"{fixture.symbol}_{fixture.timeframe}.png",
        title=f"{fixture.symbol} {fixture.timeframe} 오더블록 탐지 결과",
    )
    print(f"차트 저장: {image_path}")

    report = compare_to_fixture(
        result,
        fixture,
        price_tolerance_pct=args.price_tolerance_pct,
        time_tolerance_ms=args.time_tolerance_ms,
    )
    print()
    print(report.to_table())


if __name__ == "__main__":
    main()
