"""WAN-104: 오프셋 증분 거래 분해 — "존을 안 찍고 반등한 진입"은 좋은 거래인가.

## 왜 격자(`python -m backtest.run --offset-bps ...`)로는 답이 안 나오는가

헤드라인 격자는 오프셋별 `total_return`을 낸다. 그 수치는 **두 개의 서로 다른 효과가
상계된 합**이다:

1. **증분 거래**(+): 오프셋이 새로 잡는 체결. 가격이 존 근단을 **안 찍고** 그 위에서
   반등해버려 오프셋 0은 놓치는 진입이다.
2. **기존 거래에 붙는 세금**(−): 오프셋 0에서도 어차피 체결됐을 거래의 진입가가 전부
   나빠진다(롱=더 높게). 손절 참조가(존 원단)는 그대로라 1R이 늘고, 1R에서 파생되는
   고정 1.5R 익절 목표도 함께 멀어진다.

`total_return`만 보면 0bp가 이겨도 그게 "증분 거래가 나빠서"인지 "증분 거래는 좋은데
세금이 더 커서"인지 구분되지 않는다. 두 경우의 처방은 정반대다 — 전자면 오프셋을 버리는
게 맞고, 후자면 **세금을 안 내는 방법**(예: 존 근단 지정가는 그대로 두고 증분 밴드에만
별도 주문)을 찾을 이유가 생긴다. 이 리포트는 그 둘을 갈라 놓는다.

## 무엇을 재는가

**증분 셋업**(offset=X): `X`에서는 체결되지만 `0`에서는 체결되지 않는 셋업. 정의상
가격이 **존 근단(볼린저 재산정 후 지정가)에 닿지 않고** 그 위 밴드 `(근단, 근단+X]`
안에서만 저점을 찍고 돌아선 경우다 — 사용자가 오프셋을 원하는 바로 그 케이스다.

세 부류로 가른다(기준은 항상 `offset=0` 실행):

| 부류 | 정의 | 뜻 |
| -- | -- | -- |
| `base` | 0에서도 X에서도 체결 | 오프셋이 **세금만** 매긴 진입 |
| `incremental` | X에서만 체결 | 오프셋이 **새로 산** 진입 |
| `lost` | 0에서만 체결 | 오프셋이 **놓친** 진입 |

`lost`가 0이 아닐 수 있다는 점이 중요하다. 오프셋은 가격 조건만 보면 단조롭지만(롱은
지정가가 높을수록 닿기 쉽다) **체결 시각이 앞당겨지면 그 순간의 실시간 RSI가 달라진다** —
재탭 게이트(`RSI<=30`)를 그 시점엔 아직 통과하지 못해 체결이 사라질 수 있다. 그래서
체결 여부는 오프셋에 대해 단조가 아니고, 이 리포트는 그 비단조성을 감추지 않고 센다.

## 판정

증분 진입의 **승률·평균 R이 base보다 높으면** → 존을 안 찍고 반등할 만큼 강한 존이
좋은 롱이라는 뜻이고, `total_return` 평균이 0bp를 가리켜도 작은 오프셋에 값어치가 있다.
**낮으면** → 그 진입은 함정(채우고 존까지 뚫려 손절)이므로 0bp를 확정한다.

품질(승률·평균 R)은 **셋업 수준**(시퀀싱 전)에서, 손익은 **거래 수준**(시퀀싱 후)에서
잰다. 동시 1포지션 제약은 "그때 이미 포지션이 있었다"는 자본 사정이지 셋업의 좋고
나쁨이 아닌데, 증분 셋업은 수가 적어 그 제약에 표본이 통째로 깎여 나간다 — 품질까지
거래 수준에서 재면 판정이 한두 건에 좌우된다.

## 반등 갭 분포

셋업마다 **처음 체결되는 오프셋**(격자 해상도)을 구해, 오프셋 0이 놓친 셋업들이 존 위
몇 bp에서 반등했는지의 분포를 낸다. 대부분 1~3bp에 몰려 있으면 2bp로 충분하고, 8bp
너머에 흩어져 있으면 그걸 잡으려다 전 거래에 세금을 매기는 셈이라 감당할 수 없다.
이 분포가 "왜 2bp인가 / 왜 10bp가 아닌가"의 근거다.

## 체결 렌즈

이 분해는 **`baseline`(닿으면 체결)에서만** 의미가 있다. `pen_5bp`는 지정가를 5bp
관통해야 체결로 치는 벌점인데, 오프셋은 **주문 가격을 옮겨 관통을 미리 만족시키는**
수단이라 둘을 겹치면 같은 마찰을 두 번 친다. 헤드라인 격자
(`wan104_offset_oos_grid.csv`)가 세 가정을 모두 병기하므로 민감도는 그쪽에서 본다.

## 재현

```
python -m backtest.wan104_offset_incremental_report
```
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict

from backtest.harness import (
    BASELINE_FILL,
    CACHE_DIR,
    DB_PATH,
    DEFAULT_SYMBOLS,
    DEFAULT_YEARS,
    LEGACY_RSI_GATE_MODE,
    MarketData,
    build_config,
    build_params,
    detect_order_blocks,
    load_market_data,
    normalize_symbol,
    segments_for,
    slice_market,
)
from backtest.models import BacktestConfig, ExitReason, Trade
from backtest.zone_limit_backtest import (
    SetupDiagnostic,
    _Candidate,
    build_zone_limit_candidates,
    sequence_with_candidates,
)
from strategy.models import ConfluenceParams

#: 판정 TF. WAN-97이 정한 작업 TF 하나만 본다 — 15m은 WAN-97에서 제외됐다.
DEFAULT_TIMEFRAME = "1h"

#: 분해 격자. 헤드라인 격자(0/2/5/10)와 같은 축이되, 반등 갭 분포의 해상도를 위해
#: 1·3·8bp를 끼워 넣는다. "1~3bp에 몰렸나, 8bp 너머로 흩어졌나"가 오프셋 크기 결정의
#: 근거인데 0/2/5/10만으로는 그 질문에 눈금이 모자란다.
DEFAULT_OFFSETS_BPS: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0)

#: 판정에 쓰는 오프셋(헤드라인 격자와 동일). 갭 분포용 눈금(1·3·8)은 여기 넣지 않는다.
DECISION_OFFSETS_BPS: tuple[float, ...] = (2.0, 5.0, 10.0)

CLASS_BASE = "base"
CLASS_INCREMENTAL = "incremental"
CLASS_LOST = "lost"


# --------------------------------------------------------------------------- #
# 한 오프셋 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OffsetRun:
    """오프셋 하나의 실행 결과 — 셋업 진단 + 실제로 거래가 된 셋업×손익."""

    offset_bps: float
    setups: dict[int, SetupDiagnostic]
    """`trigger_time` → 진단. 오프셋은 시그널 생성에 관여하지 않으므로 키 집합은
    오프셋 간 동일하고, 그래서 `trigger_time`이 셋업의 안정적 식별자가 된다.

    `(zone_key, tap_index)`로 잇지 않는 이유: 병합 존은 새로 편입된 구성 존이 같은
    클러스터 안에서 다시 `tap_index=0`을 받을 수 있어(WAN-81 §5) 그 조합이 유일하지
    않다 — dict 키로 쓰면 두 셋업이 조용히 하나로 합쳐진다."""
    candidates: dict[int, _Candidate]
    """`trigger_time` → 체결·청산이 확정된 셋업(**시퀀싱 전**).

    거래 품질(승률·평균 R)은 여기서 잰다. 시퀀싱은 "이미 포지션이 있어서 못 들어갔다"는
    자본 제약이지 셋업의 성질이 아닌데, 증분 셋업은 수가 적어 그 제약에 표본이 통째로
    깎여 나간다(스모크 실행: 증분 셋업 3건 중 거래로 남은 건 1건). n=1로 "증분이 좋은
    거래인가"를 판정할 수는 없다.
    """
    trades: dict[int, tuple[_Candidate, Trade]]
    """`trigger_time` → (셋업, 거래). 시퀀싱(동시 1포지션)에서 살아남은 것만.
    손익 기여는 여기서 잰다 — 실제로 자본이 굴러간 것은 이쪽이기 때문이다."""

    @property
    def total_pnl(self) -> float:
        return sum(t.realized_pnl for _, t in self.trades.values())


def run_offset(
    market: MarketData,
    *,
    offset_bps: float,
    cfg: BacktestConfig,
    order_block_result: object = None,
) -> OffsetRun:
    """한 오프셋으로 셋업을 돌려 진단·거래를 모은다(`baseline` 체결 가정 고정).

    엔진 호출은 채택 경로(B안) 그대로다 — `build_zone_limit_candidates` +
    `sequence_with_candidates`는 `run_zone_limit_backtest_verbose`가 내부에서 쓰는 바로
    그 두 단계이고, 여기서는 중간 산출물(셋업↔거래 링크)이 필요해 나눠 부를 뿐이다.

    ⚠️ RSI 게이트는 **명시 고정**한다(WAN-123이 기본값을 `unconditional`로 옮겼다). 이
    리포트의 증분 분해(「2bp가 사는 셋업은 13건인데 세금은 917건 전부에 매겨진다」)는
    게이트가 켜진 셋업 풀에서 센 값이라, 기본값을 따라가면 그 건수가 재현되지 않는다.
    """
    params = build_params(
        offset_bps=offset_bps,
        fill=BASELINE_FILL,
        base=ConfluenceParams(rsi_gate_mode=LEGACY_RSI_GATE_MODE),
    )
    setup_sink: list[SetupDiagnostic] = []
    candidates, _stats = build_zone_limit_candidates(
        market.htf_df,
        market.df_1m,
        market.timeframe,
        params=params,
        cfg=cfg,
        order_block_result=order_block_result,  # type: ignore[arg-type]
        setup_sink=setup_sink,
    )
    paired = sequence_with_candidates(candidates, cfg, market.funding_rates)
    return OffsetRun(
        offset_bps=offset_bps,
        setups={s.trigger_time: s for s in setup_sink},
        candidates={c.trigger_time: c for c in candidates},
        trades={c.trigger_time: (c, t) for c, t in paired},
    )


# --------------------------------------------------------------------------- #
# 분해
# --------------------------------------------------------------------------- #


class ClassRow(BaseModel):
    """한 (심볼, 구간, 오프셋, 부류)의 거래 품질."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    offset_bps: float
    trade_class: str
    num_setups: int
    """그 부류에 속한 체결 셋업 수(시퀀싱 전). **판정의 표본이 이것이다.**"""
    num_resolved: int
    """그중 승패가 확정된 수(익절·손절). 데이터 끝까지 미청산은 R이 안 매겨져 빠진다."""
    num_wins: int
    win_rate: float | None
    """셋업 수준 승률 = 익절 / (익절 + 손절). 시퀀싱 전이므로 자본 제약이 안 섞인다."""
    mean_r: float | None
    """셋업 수준 평균 R. 승/패 구성만 본다(1R 확대는 R로 정규화하면 사라진다 — WAN-99)."""
    num_trades: int
    """시퀀싱(동시 1포지션)에서 살아남아 실제 거래가 된 수. 손익의 표본."""
    pnl: float
    """실현손익 합(그 오프셋 실행 안에서). base + incremental = 그 실행의 총손익."""
    pnl_share: float | None
    """그 실행 총손익 대비 비중. 총손익이 0에 가까우면 None."""


def _reason_r(reason: ExitReason | None, take_profit_r: float) -> float | None:
    """청산 사유로 매긴 R(비용 반영 전). `harness.mean_r`과 같은 규칙."""
    if reason is ExitReason.STOP_LOSS:
        return -1.0
    if reason is ExitReason.TAKE_PROFIT:
        return take_profit_r
    return None


@dataclass(frozen=True)
class Quality:
    """셋업 묶음의 승/패 구성(시퀀싱 전)."""

    num_resolved: int
    """익절·손절로 승패가 확정된 수. 데이터 끝까지 미청산은 R이 없어 빠진다."""
    num_wins: int
    mean_r: float | None

    @property
    def win_rate(self) -> float | None:
        return self.num_wins / self.num_resolved if self.num_resolved else None


def measure_quality(candidates: list[_Candidate], take_profit_r: float) -> Quality:
    """체결된 셋업들의 승/패 구성을 잰다 — **시퀀싱 전**이라 자본 제약이 섞이지 않는다."""
    r_values = [r for c in candidates if (r := _reason_r(c.reason, take_profit_r)) is not None]
    wins = sum(1 for c in candidates if c.reason is ExitReason.TAKE_PROFIT)
    return Quality(
        num_resolved=len(r_values),
        num_wins=wins,
        mean_r=sum(r_values) / len(r_values) if r_values else None,
    )


def classify_setups(base: OffsetRun, other: OffsetRun) -> dict[int, str]:
    """`offset=0` 실행을 기준으로 셋업을 `base`/`incremental`/`lost`로 가른다.

    두 실행의 셋업 키 집합은 같아야 한다 — 오프셋은 지정가 **가격**만 바꾸고 시그널
    생성(존 탐지·탭 순번·게이트 모드)에는 관여하지 않기 때문이다. 어긋나면 분해의 전제가
    깨진 것이므로 조용히 넘기지 않고 예외로 드러낸다.
    """
    if base.setups.keys() != other.setups.keys():
        only_base = len(base.setups.keys() - other.setups.keys())
        only_other = len(other.setups.keys() - base.setups.keys())
        raise ValueError(
            f"오프셋 {base.offset_bps}bp와 {other.offset_bps}bp의 셋업 집합이 다릅니다"
            f"(0bp에만 {only_base}건, {other.offset_bps}bp에만 {only_other}건). "
            "오프셋은 시그널 생성에 관여하지 않아야 하므로 이는 엔진 쪽 회귀입니다."
        )
    classes: dict[int, str] = {}
    for key, base_setup in base.setups.items():
        other_setup = other.setups[key]
        if other_setup.filled and base_setup.filled:
            classes[key] = CLASS_BASE
        elif other_setup.filled and not base_setup.filled:
            classes[key] = CLASS_INCREMENTAL
        elif base_setup.filled and not other_setup.filled:
            classes[key] = CLASS_LOST
    return classes


def build_class_rows(
    runs: dict[float, OffsetRun],
    *,
    symbol: str,
    timeframe: str,
    segment: str,
    take_profit_r: float,
    offsets: tuple[float, ...] = DECISION_OFFSETS_BPS,
) -> list[ClassRow]:
    """오프셋별로 base/incremental/lost 부류의 거래 품질과 손익 기여를 낸다.

    품질(승률·평균 R)은 **셋업 수준**(시퀀싱 전), 손익은 **거래 수준**(시퀀싱 후)에서
    잰다. 둘을 같은 표본으로 맞추지 않는 이유: 시퀀싱은 "그때 이미 다른 포지션을 들고
    있었다"는 자본 제약이라 셋업의 좋고 나쁨과 무관한데, 증분 셋업은 수가 적어 그 제약에
    표본이 통째로 깎인다. 반대로 손익은 자본이 실제로 굴러간 거래에서만 나온다.
    """
    base_run = runs[0.0]
    rows: list[ClassRow] = []
    for offset in offsets:
        run = runs.get(offset)
        if run is None:
            continue
        classes = classify_setups(base_run, run)
        # 부류별로 셋업(품질)과 거래(손익)를 모은다. `lost`만 0bp 실행을 본다 — 정의상
        # X에서는 체결되지 않았으므로 X의 목록에 아예 없다.
        source_of = {CLASS_BASE: run, CLASS_INCREMENTAL: run, CLASS_LOST: base_run}
        total_pnl = run.total_pnl
        for label, source in source_of.items():
            keys = {k for k, v in classes.items() if v == label}
            candidates = [c for k, c in source.candidates.items() if k in keys]
            trades = [t for k, (_, t) in source.trades.items() if k in keys]
            quality = measure_quality(candidates, take_profit_r)
            pnl = sum(t.realized_pnl for t in trades)
            rows.append(
                ClassRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    segment=segment,
                    offset_bps=offset,
                    trade_class=label,
                    num_setups=len(candidates),
                    num_resolved=quality.num_resolved,
                    num_wins=quality.num_wins,
                    win_rate=quality.win_rate,
                    mean_r=quality.mean_r,
                    num_trades=len(trades),
                    pnl=pnl,
                    # `lost`는 X 실행에 없는 손익이라 X의 총손익으로 나누면 뜻이 없다.
                    pnl_share=(
                        pnl / total_pnl if label != CLASS_LOST and abs(total_pnl) > 1e-9 else None
                    ),
                )
            )
    return rows


class GapRow(BaseModel):
    """반등 갭 분포 한 칸 — "이 밴드에서 반등한 셋업이 몇 건이고 그 질은 어떤가"."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    segment: str
    band: str
    """`(0, 1]` 처럼 처음 체결된 오프셋 구간. `never`는 격자 끝까지 미체결."""
    band_upper_bps: float | None
    num_setups: int
    num_resolved: int
    num_wins: int
    win_rate: float | None
    mean_r: float | None


def build_gap_rows(
    runs: dict[float, OffsetRun],
    *,
    symbol: str,
    timeframe: str,
    segment: str,
    take_profit_r: float,
) -> list[GapRow]:
    """오프셋 0이 놓친 셋업이 **존 위 몇 bp에서 반등했는지**의 분포.

    셋업마다 "처음 체결되는 오프셋"을 격자에서 찾는다. 그 값이 곧 그 셋업을 잡는 데
    필요했던 최소 오프셋이고, 가격이 존 근단 위 몇 bp에서 돌아섰는지의 대리 지표다
    (격자 해상도까지). 체결의 질은 그 셋업이 처음 체결된 오프셋 실행의 **셋업 수준**
    결과로 잰다 — 실제로 그 오프셋을 걸었을 때 받았을 진입이 그것이기 때문이다.
    """
    base_run = runs[0.0]
    ladder = sorted(o for o in runs if o > 0.0)
    rows: list[GapRow] = []

    #: 셋업 키 → (처음 체결된 오프셋, 그 실행). 0에서 이미 체결된 셋업은 대상이 아니다.
    first_fill: dict[int, float] = {}
    for key, setup in base_run.setups.items():
        if setup.filled:
            continue
        for offset in ladder:
            if runs[offset].setups[key].filled:
                first_fill[key] = offset
                break

    bands: list[tuple[str, float | None]] = []
    lower = 0.0
    for offset in ladder:
        bands.append((f"({lower:g}, {offset:g}]", offset))
        lower = offset
    bands.append(("never", None))

    for band, upper in bands:
        if upper is None:
            keys = {k for k, s in base_run.setups.items() if not s.filled and k not in first_fill}
            rows.append(
                GapRow(
                    symbol=symbol,
                    timeframe=timeframe,
                    segment=segment,
                    band=band,
                    band_upper_bps=None,
                    num_setups=len(keys),
                    num_resolved=0,
                    num_wins=0,
                    win_rate=None,
                    mean_r=None,
                )
            )
            continue
        keys = {k for k, v in first_fill.items() if v == upper}
        run = runs[upper]
        quality = measure_quality(
            [c for k, c in run.candidates.items() if k in keys], take_profit_r
        )
        rows.append(
            GapRow(
                symbol=symbol,
                timeframe=timeframe,
                segment=segment,
                band=band,
                band_upper_bps=upper,
                num_setups=len(keys),
                num_resolved=quality.num_resolved,
                num_wins=quality.num_wins,
                win_rate=quality.win_rate,
                mean_r=quality.mean_r,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# 셀 실행
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CellSpec:
    """한 심볼 실행 단위. 프로세스 경계를 넘으므로 데이터가 아니라 좌표만 담는다."""

    symbol: str
    timeframe: str
    years: float
    db_path: str
    cache_dir: str
    offsets_bps: tuple[float, ...]


@dataclass(frozen=True)
class CellResult:
    classes: list[ClassRow]
    gaps: list[GapRow]


def run_cell(spec: CellSpec) -> CellResult:
    """한 심볼에서 구간(full/IS/OOS) × 오프셋을 돌아 분해 행을 낸다."""
    market = load_market_data(
        spec.symbol,
        spec.timeframe,
        years=spec.years,
        db_path=spec.db_path,
        cache_dir=spec.cache_dir,
    )
    if market.empty or market.df_1m.empty:
        print(f"[wan104] {spec.symbol} {spec.timeframe}: 데이터 없음 — 건너뜀")
        return CellResult([], [])
    cfg = build_config(spec.timeframe)
    take_profit_r = ConfluenceParams().take_profit_r
    classes: list[ClassRow] = []
    gaps: list[GapRow] = []
    for segment in segments_for(oos=True):
        window = slice_market(market, segment)
        if window.empty or window.df_1m.empty:
            continue
        runs = _runs_for_window(window, spec.offsets_bps, cfg)
        classes += build_class_rows(
            runs,
            symbol=spec.symbol,
            timeframe=spec.timeframe,
            segment=segment.name,
            take_profit_r=take_profit_r,
        )
        gaps += build_gap_rows(
            runs,
            symbol=spec.symbol,
            timeframe=spec.timeframe,
            segment=segment.name,
            take_profit_r=take_profit_r,
        )
    print(f"[wan104] {spec.symbol} {spec.timeframe}: 분해 완료")
    return CellResult(classes, gaps)


def _runs_for_window(
    window: MarketData, offsets: tuple[float, ...], cfg: BacktestConfig
) -> dict[float, OffsetRun]:
    """구간 하나에서 오프셋들을 돈다. 오더블록은 한 번만 탐지해 오프셋들이 공유한다 —
    탐지는 오프셋과 무관하므로 결과가 바뀌지 않고, 오프셋 간 비교가 **동일한 존** 위에서
    이뤄져야 분해가 성립한다."""
    ob_result = detect_order_blocks(window)
    return {
        offset: run_offset(window, offset_bps=offset, cfg=cfg, order_block_result=ob_result)
        for offset in offsets
    }


def collect(
    symbols: tuple[str, ...],
    timeframe: str,
    *,
    years: float,
    db_path: str,
    cache_dir: str,
    offsets_bps: tuple[float, ...],
    jobs: int,
) -> CellResult:
    specs = [
        CellSpec(
            symbol=symbol,
            timeframe=timeframe,
            years=years,
            db_path=db_path,
            cache_dir=cache_dir,
            offsets_bps=offsets_bps,
        )
        for symbol in symbols
    ]
    if jobs <= 1 or len(specs) <= 1:
        results = [run_cell(spec) for spec in specs]
    else:
        with ProcessPoolExecutor(max_workers=min(jobs, len(specs))) as pool:
            results = list(pool.map(run_cell, specs))
    return CellResult(
        classes=[r for res in results for r in res.classes],
        gaps=[r for res in results for r in res.gaps],
    )


# --------------------------------------------------------------------------- #
# 집계 · 렌더
# --------------------------------------------------------------------------- #

_SEGMENT_ORDER = {"full": 0, "is": 1, "oos": 2}
_CLASS_ORDER = {CLASS_BASE: 0, CLASS_INCREMENTAL: 1, CLASS_LOST: 2}


def classes_to_frame(rows: list[ClassRow]) -> pd.DataFrame:
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if frame.empty:
        return frame
    frame["_sg"] = frame["segment"].map(_SEGMENT_ORDER)
    frame["_cl"] = frame["trade_class"].map(_CLASS_ORDER)
    return (
        frame.sort_values(["symbol", "_sg", "offset_bps", "_cl"])
        .drop(columns=["_sg", "_cl"])
        .reset_index(drop=True)
    )


def gaps_to_frame(rows: list[GapRow]) -> pd.DataFrame:
    frame = pd.DataFrame([r.model_dump() for r in rows])
    if frame.empty:
        return frame
    frame["_sg"] = frame["segment"].map(_SEGMENT_ORDER)
    return (
        frame.sort_values(["symbol", "_sg", "band_upper_bps"], na_position="last")
        .drop(columns=["_sg"])
        .reset_index(drop=True)
    )


def _pool_quality(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """심볼별 행을 합쳐 승률·평균 R을 **다시 계산**한다(거래를 통째로 모은 것과 동일).

    심볼별 승률을 단순 평균하면 셋업 3건짜리 증분 부류가 200건짜리 base와 같은 무게를
    갖는다. 승/패 **개수**를 합친 뒤 나눠야 표본 크기가 반영된다.
    """
    pooled = frame.groupby(keys, dropna=False, as_index=False).agg(
        num_setups=("num_setups", "sum"),
        num_resolved=("num_resolved", "sum"),
        num_wins=("num_wins", "sum"),
        _r_sum=("_r_sum", "sum"),
    )
    pooled["win_rate"] = pooled.apply(
        lambda r: r["num_wins"] / r["num_resolved"] if r["num_resolved"] else None, axis=1
    )
    pooled["mean_r"] = pooled.apply(
        lambda r: r["_r_sum"] / r["num_resolved"] if r["num_resolved"] else None, axis=1
    )
    return pooled.drop(columns=["_r_sum"])


def aggregate_classes(frame: pd.DataFrame) -> pd.DataFrame:
    """심볼을 합쳐 (구간, 오프셋, 부류)별로 집계한다."""
    if frame.empty:
        return frame
    keys = ["segment", "offset_bps", "trade_class"]
    # 평균 R의 가중치는 **승패가 확정된 수**다. `mean_r`이 그 표본에서만 정의되므로
    # 거래 수로 가중하면 미청산 거래가 분모에 섞여 값이 희석된다.
    with_sum = frame.assign(_r_sum=(frame["mean_r"].fillna(0.0) * frame["num_resolved"]))
    grouped = _pool_quality(with_sum, keys)
    totals = frame.groupby(keys, as_index=False).agg(
        num_trades=("num_trades", "sum"), pnl=("pnl", "sum")
    )
    grouped = grouped.merge(totals, on=keys)
    grouped["_sg"] = grouped["segment"].map(_SEGMENT_ORDER)
    grouped["_cl"] = grouped["trade_class"].map(_CLASS_ORDER)
    return (
        grouped.sort_values(["_sg", "offset_bps", "_cl"])
        .drop(columns=["_sg", "_cl"])
        .reset_index(drop=True)
    )


def aggregate_gaps(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    keys = ["segment", "band", "band_upper_bps"]
    with_sum = frame.assign(_r_sum=(frame["mean_r"].fillna(0.0) * frame["num_resolved"]))
    grouped = _pool_quality(with_sum, keys)
    grouped["_sg"] = grouped["segment"].map(_SEGMENT_ORDER)
    return (
        grouped.sort_values(["_sg", "band_upper_bps"], na_position="last")
        .drop(columns=["_sg"])
        .reset_index(drop=True)
    )


def _pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.2f}%"


def _r(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:+.3f}R"


#: 증분 부류의 승/패 표본이 이보다 적으면 승률 비교를 판정 근거로 쓰지 않는다.
#: 사전에 고정한 값이며, 근거는 이 저장소가 이미 쓰는 기준선이다 — WAN-84가 심볼당
#: 20거래를 "판정 가능" 하한으로 잡았고(4h가 15.7거래로 미달해 보류됐다), 같은 잣대를
#: 표본이 훨씬 작은 이 분해에 그대로 댄다.
MIN_RESOLVED_FOR_VERDICT = 20


def _verdict_lines(agg: pd.DataFrame) -> list[str]:
    """증분 진입의 질로 판정한다 — 이 문장이 이 이슈의 실제 결론이다."""
    lines = ["## 판정: 증분 진입은 base보다 좋은가", ""]
    if agg.empty:
        return lines + ["분해 결과가 없다.", ""]
    for segment in ("full", "is", "oos"):
        rows = agg[agg["segment"] == segment]
        if rows.empty:
            continue
        lines += [f"### 구간 `{segment}`", ""]
        for offset in sorted(rows["offset_bps"].unique()):
            cell = rows[rows["offset_bps"] == offset]
            base = cell[cell["trade_class"] == CLASS_BASE]
            inc = cell[cell["trade_class"] == CLASS_INCREMENTAL]
            if base.empty or inc.empty:
                continue
            base_row, inc_row = base.iloc[0], inc.iloc[0]
            resolved = int(inc_row["num_resolved"])
            if resolved == 0:
                lines.append(
                    f"- **{offset:.0f}bp**: 증분 진입 중 승패가 확정된 것이 **0건**이다 — "
                    "판정할 표본이 없다. 살 것이 없으면 세금만 남는다."
                )
                continue
            better_win = (inc_row["win_rate"] or 0) > (base_row["win_rate"] or 0)
            better_r = (inc_row["mean_r"] or 0) > (base_row["mean_r"] or 0)
            if better_win and better_r:
                judgement = (
                    "증분이 base보다 **승률·평균 R 모두 높다** — 존을 안 찍고 반등할 만큼 "
                    "강한 존이 실제로 좋은 롱이었다는 쪽."
                )
            elif better_win or better_r:
                judgement = "증분이 base보다 **한 지표만** 낫다 — 우위가 견고하지 않다."
            else:
                judgement = (
                    "증분이 base보다 **승률·평균 R 모두 낮다** — 그 진입은 함정 쪽에 "
                    "가깝다(채우고 존까지 뚫려 손절)."
                )
            if resolved < MIN_RESOLVED_FOR_VERDICT:
                judgement += (
                    f" ⚠️ 다만 승패 확정 **{resolved}건**은 판정 하한"
                    f"({MIN_RESOLVED_FOR_VERDICT}건, WAN-84 기준)에 못 미쳐 이 비교 자체가 "
                    "잡음일 수 있다 — 방향만 참고한다."
                )
            lines.append(
                f"- **{offset:.0f}bp**: 증분 셋업 {int(inc_row['num_setups'])}건"
                f"(승패 확정 {resolved}건, 승률 {_pct(inc_row['win_rate'])}, "
                f"평균 {_r(inc_row['mean_r'])}) vs base {int(base_row['num_setups'])}건"
                f"(승률 {_pct(base_row['win_rate'])}, 평균 {_r(base_row['mean_r'])}). "
                f"{judgement}"
            )
        lines.append("")
    return lines


def build_markdown(
    agg_classes: pd.DataFrame,
    agg_gaps: pd.DataFrame,
    *,
    offsets: tuple[float, ...],
) -> str:
    lines = [
        "# WAN-104: 오프셋 증분 거래 분해",
        "",
        "**재현**: `python -m backtest.wan104_offset_incremental_report`",
        "",
        "## 무엇을 묻는가",
        "",
        "헤드라인 격자(`wan104_offset_oos_grid.csv`)의 `total_return`은 **증분 거래의 이득**과 "
        "**기존 거래에 붙는 세금**이 상계된 합이라, 0bp가 이겨도 그게 증분 거래가 나빠서인지 "
        "세금이 더 커서인지 알 수 없다. 이 리포트는 둘을 갈라 놓는다.",
        "",
        "**증분 셋업** = 오프셋 X에서는 체결되는데 0에서는 체결되지 않는 셋업. 정의상 가격이 "
        "**존 근단에 닿지 않고** 그 위 밴드 `(근단, 근단+X]` 안에서만 저점을 찍고 반등한 "
        "경우다 — 오프셋을 원하는 실제 동기가 이 케이스다.",
        "",
        "| 축 | 값 |",
        "| -- | -- |",
        f"| 오프셋(`zone_limit_offset_bps`) | {', '.join(f'{v:g}bp' for v in offsets)} |",
        "| 체결 가정 | `baseline`(닿으면 체결) 고정 |",
        f"| 심볼 | {', '.join(DEFAULT_SYMBOLS)} |",
        f"| TF | {DEFAULT_TIMEFRAME} (WAN-97 작업 TF) |",
        f"| 구간 | 최근 {DEFAULT_YEARS:.0f}년 (IS = 앞 2/3, OOS = 뒤 1/3) |",
        "",
        "`baseline`으로 고정한 이유: `pen_5bp`는 지정가를 5bp 관통해야 체결로 치는 벌점인데, "
        "오프셋은 **주문 가격을 옮겨 관통을 미리 만족시키는** 수단이라 둘을 겹치면 같은 "
        "마찰을 두 번 친다. 체결 가정 민감도는 헤드라인 격자에서 병기한다.",
        "",
    ]
    lines += _verdict_lines(agg_classes)

    lines += [
        "## 부류별 진입 품질 (3심볼 합산)",
        "",
        "`base` = 0에서도 X에서도 체결(오프셋이 **세금만** 매긴 진입) · "
        "`incremental` = X에서만 체결(오프셋이 **새로 산** 진입) · "
        "`lost` = 0에서만 체결(오프셋이 **놓친** 진입).",
        "",
        "**승률·평균 R은 셋업 수준**(시퀀싱 전), **손익은 거래 수준**(시퀀싱 후)이다. "
        "동시 1포지션 제약은 '그때 이미 포지션이 있었다'는 자본 사정이지 셋업의 좋고 "
        "나쁨이 아닌데, 증분 셋업은 수가 적어 그 제약에 표본이 통째로 깎인다 — 품질을 "
        "거래 수준에서 재면 판정이 한두 건에 좌우된다. 그래서 두 질문을 각자에게 맞는 "
        "표본에서 잰다: **좋은 진입인가**(셋업) / **얼마를 벌었나**(거래).",
        "",
        "`lost`가 0이 아닌 것은 버그가 아니다 — 오프셋이 체결 시각을 앞당기면 그 순간의 "
        "실시간 RSI가 달라져 재탭 게이트를 통과하지 못할 수 있다. 즉 체결 여부는 오프셋에 "
        "대해 단조가 아니다. `lost`의 손익은 X 실행에 없는 값이라 비중을 매기지 않는다.",
        "",
        "| 구간 | 오프셋 | 부류 | 셋업 | 승패확정 | 승률 | 평균 R | 거래 | 손익 |",
        "| -- | --: | -- | --: | --: | --: | --: | --: | --: |",
    ]
    for _, row in agg_classes.iterrows():
        lines.append(
            f"| {row['segment']} | {row['offset_bps']:.0f}bp | `{row['trade_class']}` | "
            f"{int(row['num_setups'])} | {int(row['num_resolved'])} | {_pct(row['win_rate'])} | "
            f"{_r(row['mean_r'])} | {int(row['num_trades'])} | {row['pnl']:,.0f} |"
        )
    lines += [""]

    lines += [
        "## 반등 갭 분포 — 존 위 몇 bp에서 돌아섰나",
        "",
        "오프셋 0이 놓친 셋업마다 **처음 체결되는 오프셋**을 격자에서 찾는다. 그 값이 그 "
        "셋업을 잡는 데 필요했던 최소 오프셋이고, 가격이 존 근단 위 몇 bp에서 반등했는지의 "
        "대리 지표다(격자 해상도까지). 대부분 1~3bp에 몰려 있으면 2bp로 충분하고, 8bp "
        "너머로 흩어져 있으면 그걸 잡으려다 전 거래에 세금을 매기는 셈이라 감당할 수 없다.",
        "",
        "`never`는 격자 끝(가장 큰 오프셋)까지도 체결되지 않은 셋업이다 — 가격이 존 근처에 "
        "오지도 않았거나 RSI 게이트에 막힌 경우이므로 오프셋으로 살 수 있는 대상이 아니다.",
        "",
        "| 구간 | 밴드 | 셋업 | 승패확정 | 승률 | 평균 R |",
        "| -- | -- | --: | --: | --: | --: |",
    ]
    for _, row in agg_gaps.iterrows():
        lines.append(
            f"| {row['segment']} | `{row['band']}` | {int(row['num_setups'])} | "
            f"{int(row['num_resolved'])} | {_pct(row['win_rate'])} | {_r(row['mean_r'])} |"
        )
    lines += [""]

    lines += [
        "## 한계",
        "",
        "1. **갭은 격자 해상도까지만 안다.** 실제 반등 지점이 아니라 '격자에서 처음 체결된 "
        "오프셋'이다. `(0, 1]` 밴드의 셋업이 0.1bp에서 돌아섰는지 0.9bp였는지는 구분하지 "
        "못한다.",
        "2. **비단조성이 밴드를 흐린다.** 체결 시각이 앞당겨지면 실시간 RSI가 달라져 더 큰 "
        "오프셋에서 오히려 체결이 사라질 수 있다(`lost`). 그런 셋업은 '처음 체결된 오프셋'이 "
        "반등 지점을 정확히 대리하지 못한다.",
        "3. **손익 기여는 시퀀싱에 얽혀 있다.** 동시 1포지션 제약 때문에 증분 거래 하나가 "
        "들어오면 뒤따르던 base 거래가 밀려날 수 있다. 따라서 부류별 손익 합은 '그 실행 안의 "
        "구성'이지, '증분 거래를 빼면 total_return이 그만큼 준다'는 뜻이 아니다.",
        "4. **`baseline`은 낙관이다.** 큐 우선순위를 모델링하지 않으므로 '닿으면 체결'이 "
        "실거래보다 후하다(WAN-96의 한계 그대로 — 틱·호가 데이터가 필요하다).",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="WAN-104 오프셋 증분 거래 분해")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--years", type=float, default=DEFAULT_YEARS)
    parser.add_argument("--offsets-bps", nargs="+", type=float, default=list(DEFAULT_OFFSETS_BPS))
    parser.add_argument("--db-path", default=DB_PATH)
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    parser.add_argument("--out-dir", default="backtest/reports")
    parser.add_argument("--jobs", type=int, default=min(3, os.cpu_count() or 1))
    args = parser.parse_args()

    offsets = tuple(sorted({0.0, *args.offsets_bps}))
    result = collect(
        tuple(normalize_symbol(s) for s in args.symbols),
        args.timeframe,
        years=args.years,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
        offsets_bps=offsets,
        jobs=args.jobs,
    )
    classes = classes_to_frame(result.classes)
    gaps = gaps_to_frame(result.gaps)
    agg_classes = aggregate_classes(classes)
    agg_gaps = aggregate_gaps(gaps)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        out_dir / "wan104_offset_incremental.csv": classes.to_csv(index=False),
        out_dir / "wan104_offset_gap_distribution.csv": gaps.to_csv(index=False),
        out_dir / "wan104_offset_incremental_summary.md": build_markdown(
            agg_classes, agg_gaps, offsets=offsets
        ),
    }
    for path, text in paths.items():
        path.write_text(text, encoding="utf-8")
        print(f"[wan104] 저장: {path}")


if __name__ == "__main__":
    main()
