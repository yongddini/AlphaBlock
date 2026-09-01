"""WAN-396: 허수 진입 슬리피지를 공개 CSV에서 걷어낸다 — 열만 되계산한다.

## 한 줄

비용 분해(WAN-370)가 `BacktestConfig.entry_liquidity`(기본 **테이커**)를 읽어 붙지도 않은
진입 슬리피지 5bp를 계상했다 — 엔진은 후보의 값(기본 **메이커**)을 쓴다. 판정은 **(가)
분해만 틀렸다**: `gross_r`와 `slippage_r`가 **같은 크기로 부풀어 상쇄되므로 `net_r`은 맞다.**

## 왜 격자를 다시 안 돌리는가 — 보정이 근사가 아니라 항등식이다

옛 분해는 진입 참조가를 `entry_price / (1 + slip)`으로 되돌렸다(롱). 그러면 허수 슬리피지가

```
phantom_i = entry_notional_i × slip/(1+slip)
          = entry_fee_i × K,      K = (slip/(1+slip)) / maker_rate
```

이고 `entry_fee_i = entry_notional_i × maker_rate`라 **K가 거래에 무관한 상수**다. R로 나누고
평균내도 상수는 그대로 나오므로

```
보정 gross_r    = gross_r    − K × entry_fee_r
보정 slippage_r = slippage_r − K × entry_fee_r
보정 cost_r     = cost_r     − K × entry_fee_r
net_r           = 그대로 (판정 (가))
```

가 **거래 단위 산출물 없이도 정확**하다. 채택 요율(메이커 2bp · 슬리피지 5bp)에서
`K = 2.4987506…`이고, 그 항등식은 `tests/test_wan396_entry_liquidity.py`가 실제 거래로 고정한다.

⚠️ **전제 하나**: 그 CSV의 모든 거래가 **메이커 진입**이어야 한다(채택 B안 좌표는 전부 그렇다).
확인 진입 팔(WAN-383/386 · 테이커 진입)은 이 분해를 쓰지 않는다. 이 모듈은 그 전제를
**검산 (a)로 확인한다** — 안 맞으면 실행이 죽는다.

## 검산

* **(a) 슬리피지 항등식** — `slippage_r == K×entry_fee_r + Kx×(테이커 청산 수수료)`.
  `Kx = (slip/(1−slip)) / taker_rate`. 이 식이 기계 정밀도로 닫혀야 「진입 몫이 정확히
  `K×entry_fee_r`」라는 주장이 선다. 안 닫히면 그 행은 다른 요율·다른 유동성이라 보정할 수 없다.
* **(b) 독립 자 대조** — `gross_r − slippage_r == mean_gross_r_after_slippage`(WAN-381/386의 자,
  `realized_pnl + 수수료`로만 만들어 이 버그에 **안 걸린다**). 보정이 두 열에서 같은 값을 빼므로
  이 차는 **불변**이어야 한다 = 보정이 신뢰할 수 있는 양을 안 건드렸다는 직접 증거.
* **(c) `net_r` 불변** — 보정 전후로 한 글자도 안 바뀐다(판정 (가)의 정의).

## 공개 CSV는 덮어쓰지 않는다

여섯 CSV는 **그때 그 코드가 실제로 낸 것**이라 기록으로 보존한다(WAN-194/297/325 관행).
이 모듈은 **보정 전후 대조표 하나**(`wan396_entry_slippage_correction.csv`)를 낸다.

재현:

```
uv run python -m backtest.wan396_entry_slippage_correction
```
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

from backtest import harness
from common.costs import Liquidity

__all__ = [
    "CORRECTED_COLUMNS",
    "SOURCE_CSVS",
    "CorrectedRow",
    "correction_constant",
    "correct_row",
    "main",
    "taker_exit_constant",
]

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
OUTPUT_CSV = REPORTS_DIR / "wan396_entry_slippage_correction.csv"
OUTPUT_MD = REPORTS_DIR / "wan396_entry_slippage_correction.md"

#: 같은 분해(`wan370_cost_decomposition.decompose_trade`)를 쓴 표 여섯.
SOURCE_CSVS: tuple[str, ...] = (
    "wan370_cost_decomposition.csv",
    "wan388_merge_x_retap_grid.csv",
    "wan388_leave_one_out.csv",
    "wan389_retap_attribution_grid.csv",
    "wan394_retap_reentry_tp_grid.csv",
    "wan394_leave_one_out.csv",
)

#: 보정되는 열 셋. `net_r`·`mean_net_r`은 **여기 없다**(판정 (가)).
CORRECTED_COLUMNS: tuple[str, ...] = ("gross_r", "slippage_r", "cost_r")

#: 익절까지 테이커로 돌린 팔(WAN-370 「전」 팔) — 검산 (a)의 청산 쪽 항이 하나 더 붙는다.
_TAKER_TP_ARMS: frozenset[str] = frozenset({"taker_tp"})

#: 검산 (a) 허용 오차. 기계 정밀도라 실측은 1e-13 수준이다.
IDENTITY_TOL = 1e-9


def correction_constant(*, slippage: float, maker_rate: float) -> float:
    """`K` — 허수 진입 슬리피지를 `entry_fee`에서 되찾는 상수."""
    return (slippage / (1.0 + slippage)) / maker_rate


def taker_exit_constant(*, slippage: float, taker_rate: float) -> float:
    """`Kx` — 테이커 청산 수수료에서 그 청산의 슬리피지를 되찾는 상수(검산 (a) 전용)."""
    return (slippage / (1.0 - slippage)) / taker_rate


@dataclass(frozen=True)
class CorrectedRow:
    """한 행의 보정 전후. `net_r`은 보정 대상이 아니라 **불변 확인용**으로 싣는다."""

    source: str
    label: str
    segment: str
    entry_fee_r: float
    phantom_r: float
    """걷어낸 허수 진입 슬리피지 = `K × entry_fee_r`."""
    before: dict[str, float]
    after: dict[str, float]
    net_r: float
    identity_abs: float
    """검산 (a) 잔차."""
    independent_gross_delta: float | None
    """검산 (b) 잔차. 그 열이 없는 표는 `None`."""
    is_headline: bool
    """요약 표에 오르는 행인가 — 각 표의 **채택 좌표 전체 집계** 행 하나씩.

    `wan370`은 `axis == "overall"`, 나머지는 그 표가 스스로 찍어 둔 `adopted_arm`
    (WAN-394는 `adopted_point`)이다. 여기서 새로 정하지 않고 **그 표의 라벨을 그대로 쓴다** —
    다시 정하면 요약이 원본과 다른 행을 「채택」이라 부르게 된다."""


def _float(row: dict[str, str], key: str) -> float:
    raw = row.get(key, "")
    return float(raw) if raw not in ("", "None") else 0.0


def _label(row: dict[str, str]) -> str:
    """행을 사람이 알아볼 이름 하나로 — 표마다 라벨 열이 다르다."""
    parts = [row.get("label") or row.get("arm") or "?"]
    for key in ("axis", "bucket", "multiple", "reentry", "exclude"):
        value = row.get(key)
        if value not in (None, "", "overall", "all", "전체"):
            parts.append(f"{key}={value}")
    return " · ".join(parts)


def correct_row(source: str, row: dict[str, str], *, k: float, kx: float) -> CorrectedRow:
    """한 행에서 허수 진입 슬리피지를 걷어내고 검산 둘을 함께 낸다."""
    entry_fee_r = _float(row, "entry_fee_r")
    phantom = k * entry_fee_r

    before = {name: _float(row, name) for name in CORRECTED_COLUMNS}
    after = {name: value - phantom for name, value in before.items()}

    taker_exit_fee = _float(row, "stop_fee_r") + _float(row, "other_fee_r")
    if row.get("arm") in _TAKER_TP_ARMS:
        taker_exit_fee += _float(row, "take_profit_fee_r")
    identity_abs = abs(before["slippage_r"] - (phantom + kx * taker_exit_fee))

    independent = row.get("mean_gross_r_after_slippage")
    independent_delta = (
        None
        if independent in (None, "")
        else abs(after["gross_r"] - after["slippage_r"] - float(independent))
    )

    net_key = "net_r" if "net_r" in row else "mean_net_r"
    if "axis" in row:
        headline = row.get("axis") == "overall"
    else:
        flag = row.get("adopted_point", row.get("adopted_arm", ""))
        headline = flag == "True" and not row.get("exclude")
    return CorrectedRow(
        source=source,
        label=_label(row),
        segment=row.get("segment", "?"),
        entry_fee_r=entry_fee_r,
        phantom_r=phantom,
        before=before,
        after=after,
        net_r=_float(row, net_key),
        identity_abs=identity_abs,
        independent_gross_delta=independent_delta,
        is_headline=headline,
    )


def load_rows(reports_dir: Path = REPORTS_DIR) -> list[CorrectedRow]:
    """여섯 CSV를 읽어 보정한다. 검산 (a)가 깨진 행이 있으면 **죽는다**."""
    cfg = harness.build_config("1h")
    k = correction_constant(
        slippage=cfg.slippage, maker_rate=cfg.cost_model.fee_rate(Liquidity.MAKER)
    )
    kx = taker_exit_constant(
        slippage=cfg.slippage, taker_rate=cfg.cost_model.fee_rate(Liquidity.TAKER)
    )

    out: list[CorrectedRow] = []
    for name in SOURCE_CSVS:
        path = reports_dir / name
        if not path.exists():
            raise FileNotFoundError(f"보정할 CSV가 없습니다: {path}")
        with path.open(encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                out.append(correct_row(name, raw, k=k, kx=kx))

    broken = [r for r in out if r.identity_abs > IDENTITY_TOL]
    if broken:
        worst = max(broken, key=lambda r: r.identity_abs)
        raise ValueError(
            "검산 (a) 실패 — 그 행의 슬리피지가 「메이커 진입 + 테이커 청산」으로 설명되지 "
            f"않습니다(보정 불가): {worst.source} · {worst.label} · 잔차 {worst.identity_abs:.3e}"
        )
    return out


def write_csv(rows: list[CorrectedRow], path: Path = OUTPUT_CSV) -> None:
    header = [
        "source",
        "label",
        "segment",
        "entry_fee_r",
        "phantom_entry_slippage_r",
        *[f"{name}_before" for name in CORRECTED_COLUMNS],
        *[f"{name}_after" for name in CORRECTED_COLUMNS],
        "net_r_unchanged",
        "identity_abs",
        "independent_gross_delta",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(
                [
                    row.source,
                    row.label,
                    row.segment,
                    f"{row.entry_fee_r:.6f}",
                    f"{row.phantom_r:.6f}",
                    *[f"{row.before[name]:.6f}" for name in CORRECTED_COLUMNS],
                    *[f"{row.after[name]:.6f}" for name in CORRECTED_COLUMNS],
                    f"{row.net_r:.6f}",
                    f"{row.identity_abs:.3e}",
                    ""
                    if row.independent_gross_delta is None
                    else f"{row.independent_gross_delta:.3e}",
                ]
            )


def verdict(gross_r: float, *, noise: float = 0.005) -> str:
    """보정 후 gross의 부호 판정 — WAN-370 §1-3과 **같은 자**(±0.005R은 0과 구분 못 한다)."""
    if gross_r > noise:
        return "(나) 시장에선 이겼는데 비용이 먹었다"
    if gross_r < -noise:
        return "(가) 시장에서 졌다"
    return "(0 근처) 시장에서도 본전"


def render_summary(rows: list[CorrectedRow]) -> str:
    """결론 문장 + 헤드라인 표. 판정은 코드가 낸다(사람이 표를 보고 정하지 않는다)."""
    lines: list[str] = [
        "# WAN-396 — 허수 진입 슬리피지 보정 전후",
        "",
        "**판정 (가) 분해만 틀렸다.** 엔진은 후보의 `entry_liquidity`(기본 **메이커**)로 진입을 "
        "체결하므로 진입 슬리피지가 **0**인데, 비용 분해는 `BacktestConfig.entry_liquidity`"
        "(기본 **테이커**)를 읽어 5bp가 붙었다고 역산했다. `gross_r`와 `slippage_r`가 **같은 "
        "크기로 부풀어 상쇄되므로 `net_r`은 처음부터 맞았다** — 손익이 아니라 **진단**이 틀렸다.",
        "",
        "근거(코드): 엔진 `backtest/zone_limit_backtest.py::_to_trade` ↔ 분해 "
        "`backtest/wan370_cost_decomposition.py::decompose_trade`.",
        "",
        f"보정: `gross_r`·`slippage_r`·`cost_r`에서 각각 `K × entry_fee_r`을 뺀다"
        f"(`K = {correction_constant(slippage=0.0005, maker_rate=0.0002):.7f}`). `net_r`은 불변.",
        "",
        "## 헤드라인 — `oos_warm` 채택 좌표",
        "",
        "| 표 | 행 | gross 전 | **gross 후** | slippage 전 | **후** | cost 전 | **후** "
        "| net(불변) | 판정 |",
        "| -- | -- | --: | --: | --: | --: | --: | --: | --: | -- |",
    ]
    for row in rows:
        if row.segment != "oos_warm" or not row.is_headline:
            continue
        lines.append(
            f"| {row.source.replace('.csv', '')} | {row.label} | {row.before['gross_r']:+.4f} | "
            f"**{row.after['gross_r']:+.4f}** | {row.before['slippage_r']:.4f} | "
            f"**{row.after['slippage_r']:.4f}** | {row.before['cost_r']:.4f} | "
            f"**{row.after['cost_r']:.4f}** | {row.net_r:+.4f} | {verdict(row.after['gross_r'])} |"
        )

    worst_identity = max((r.identity_abs for r in rows), default=0.0)
    deltas = [r.independent_gross_delta for r in rows if r.independent_gross_delta is not None]
    lines += [
        "",
        "## 검산",
        "",
        f"- **(a) 슬리피지 항등식** 최대 잔차 `{worst_identity:.2e}` — 진입 몫이 정확히 "
        "`K × entry_fee_r`이라는 주장의 근거.",
        f"- **(b) 독립 자 대조** 최대 잔차 "
        f"`{(max(deltas) if deltas else 0.0):.2e}` — `gross_r − slippage_r`가 "
        "`mean_gross_r_after_slippage`(이 버그에 안 걸리는 자)와 같고, 보정이 그 차를 "
        "**안 건드린다**.",
        "- **(c) `net_r` 불변** — 보정 대상 열에 없다(판정 (가)의 정의).",
        "",
        f"보정한 행 **{len(rows)}개** / 표 {len(SOURCE_CSVS)}개. 원본 CSV는 그때의 기록으로 "
        "**보존한다**(덮어쓰지 않는다).",
        "",
        "🚨 **「gross가 사실 0이었다」를 「엣지가 없다」의 새 근거로 인용하지 말 것** — 이 표가 "
        "고친 것은 **회계 한 줄**이고, 「엣지 없음」(WAN-84/88/111/114/124/151/201/248/386)은 "
        "*진입 규칙이 무작위와 구분되는가*라는 **다른 질문**이다.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-396 허수 진입 슬리피지 보정 전후 대조표")
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    args = parser.parse_args(argv)

    rows = load_rows(args.reports_dir)
    write_csv(rows, args.reports_dir / OUTPUT_CSV.name)
    summary = render_summary(rows)
    (args.reports_dir / OUTPUT_MD.name).write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
