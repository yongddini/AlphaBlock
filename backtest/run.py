"""범용 백테스트 CLI (WAN-101) — `python -m backtest.run`.

실험 하나마다 전용 스크립트를 새로 짜서 PR로 올리던 구조를 끝낸다. "익절 1.5R 말고
2R이면?" 같은 질문에 티켓 → 개발 → PR 사이클 대신 **한 줄**로 답한다:

```
uv run python -m backtest.run --tp-r 1.0,1.5,2.0,3.0
```

값에 콤마를 주면 데카르트 곱으로 격자를 돌고 조합별 1행을 낸다. 축은 심볼 · TF ·
진입 방식 · 익절 R · 지정가 오프셋 · 재탭 정책 · **포지션 정책** · 체결 가정 · 시드다.

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

# 6. 병렬 실행 — 6심볼 격자를 코어에 나눠 돌린다(결과는 직렬과 동일)
python -m backtest.run --symbol BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,TRXUSDT --jobs auto

# 7. 단일 대 동시 다중 포지션 — 같은 셋업 풀에서 회계만 바꿔 부호를 본다(WAN-103/108)
python -m backtest.run --tf 15m,1h --positions single,3 --oos

# 8. 거래별 내역 — 언제 샀고 어디서 손절났고 시드가 어떻게 바뀌었나(WAN-106)
python -m backtest.run --symbol BTCUSDT --tf 15m --trades out.csv --equity seed.csv

# 9. DB 적재 — 한 번 계산해 넣고 대시보드에서 계산 없이 조회한다(WAN-106)
python -m backtest.run --symbol BTCUSDT --tf 15m --persist
```

## 거래별 내역 (`--trades`/`--equity`/`--persist`, WAN-106)

요약 1행은 "언제 샀나 · 어디서 손절났나"에 답하지 못한다. 세 플래그가 **같은 실행의**
거래 단위 산출물을 낸다 — 컬럼은 대시보드와 **같은 함수**
(`backtest.report.trades_to_display_frame`)가 만들어 화면·CSV·DB가 같은 숫자를 낸다.

* `--trades`/`--equity`는 **단일 조합 전용**이다. 격자면 거부한다 — 조용히 마지막 조합만
  내보낸 파일이 나중에 "채택 엔진의 거래"로 인용되는 것이 WAN-95의 교훈이다.
* `--persist`는 격자에서도 쓸 수 있다. 조합마다 **실행 지문**(파라미터 전부 + 엔진
  버전 + 코드 리비전)이 붙어 섞이지 않고, 같은 지문의 재적재는 기본이 거부다
  (`--persist-replace`가 명시적 덮어쓰기). 상세는 `backtest/trade_store.py`.

## 포지션 정책 (`--positions`, WAN-130)

`single`(기본)은 채택 기본값인 **동시 1포지션** 경로 그대로이고, 숫자는 **동시 다중
포지션**(WAN-103)의 명목 상한 배수다(`열린 명목 합 ≤ 자본 × leverage`). 두 팔은 셋업
탐색·체결 시뮬레이션이 **완전히 같고** 후보를 배치하는 회계만 다르므로, 한 표의 두 줄이
같은 풀에서 나온다. 인자를 안 주면 예전과 비트 단위로 같은 행이 나온다.

## 병렬 실행 (`--jobs`, WAN-121)

기본 `1`은 직렬이고, `N>1`/`auto`는 **(심볼, TF) 단위**로 `ProcessPoolExecutor`에
fan-out한다. 병렬화는 일을 나눠 맡길 뿐 계산 로직을 건드리지 않으므로 **행의 내용도
순서도 `--jobs` 값과 무관하게 같다** — 셀은 서로 상태를 공유하지 않고, 결과·로그는
제출 순서로 모은다. 그래서 채택 수치가 `--jobs`에 따라 흔들리지 않는다
(`tests/test_run_cli.py`·`tests/test_run_regression_real_data.py`가 그 동일성을 고정한다).

## 진입 경로

`--entry-mode close`는 A안(`backtest.sweep.evaluate` → `BacktestEngine`),
`zone_limit`(기본)은 B안(`run_zone_limit_backtest_verbose`)을 탄다. `entry_mode`는
라벨이 아니라 **경로 스위치**이며(WAN-95), 불일치는 엔진이 `ValueError`로 거부한다.
두 방식을 한 표에 같이 올리면 종가 성과를 1분봉 커버 창으로 한정해 기간을 맞춘다
(`--fair-window`, 기본 자동).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
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
    RETAP_MODES,
    UNSET,
    FillPreset,
    RunRow,
    ZoneWidthArg,
    build_config,
    build_params,
    build_row,
    detect_order_blocks,
    fill_preset,
    iter_seeds,
    load_market_data,
    normalize_symbol,
    pin_band_bar,
    render,
    run_once,
    segments_for,
    slice_market,
    write_output,
)
from backtest.models import BacktestConfig, BacktestResult
from backtest.portfolio import PortfolioParams
from backtest.report import (
    equity_to_display_frame,
    trades_to_display_frame,
)
from backtest.trade_store import (
    UNKNOWN_REVISION,
    BacktestRunStore,
    DuplicateRunError,
    RunFingerprint,
    engine_revision,
)
from backtest.zone_limit_backtest import SetupDiagnostic, ZoneLimitStats
from strategy.models import (
    BandBar,
    ConfluenceParams,
    OrderBlockParams,
    OrderBlockResult,
    RsiGateMode,
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


JOBS_AUTO = 0
"""`--jobs auto`의 내부 표현. `resolve_jobs`가 `os.cpu_count()`로 푼다."""


def parse_jobs(text: str) -> int:
    """`--jobs` 값을 정수로. `auto` → `JOBS_AUTO`(0).

    음수를 조용히 1로 접지 않고 거부한다 — `--jobs -1`을 "코어 하나 빼고"로 기대한
    사용자에게 아무 말 없이 직렬 실행을 돌려주면, 느린 이유를 영영 모른다.
    """
    if text.strip().lower() == "auto":
        return JOBS_AUTO
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"--jobs는 정수 또는 auto여야 합니다: {text!r}") from exc
    if value < 0:
        raise ValueError(f"--jobs는 0(=auto) 이상이어야 합니다: {value}")
    return value


def resolve_jobs(jobs: int, task_count: int) -> int:
    """요청한 `--jobs`와 실제 작업 수로 워커 수를 정한다.

    작업 수보다 많은 워커는 프로세스를 띄우고 놀 뿐이라 `task_count`로 자른다(기존
    wan70/71/88/104 fan-out과 같은 `min(jobs, len(tasks))` 규칙).
    """
    requested = (os.cpu_count() or 1) if jobs == JOBS_AUTO else jobs
    return max(1, min(requested, task_count))


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
    retap_modes: tuple[str, ...] = ("every_tap",)
    """재탭 정책 축(WAN-138). 기본은 채택 기본값(`every_tap`) 하나 — `every_tap,once`로
    두 팔을 한 표에서 비교한다. 다른 축과 달리 이건 **진짜 축이지 핀이 아니다**(사용자가
    두 팔을 나란히 보려는 것이 이슈의 목적이라 CLI 플래그로 연다)."""
    seeds: tuple[int, ...] | None = None
    """탈락 시드 오버라이드. None이면 프리셋의 시드를 쓴다."""
    short_enabled: bool | None = None
    rsi_gate_mode: RsiGateMode | None = None
    """RSI 게이트 **고정**. None이면 채택 기본값(WAN-123: `unconditional` = 게이트 없음).

    격자 **축이 아니라 핀**이다(`short_enabled`와 같은 자리) — CLI 플래그로 열지 않으므로
    기본 실행은 언제나 채택 기본값을 돈다. WAN-123이 게이트를 뺀 뒤, **게이트가 켜진
    거래 집합에서 낸 수치를 결론에 박아 둔 리포트**(wan111 등)가 자기 엔진을 고정하는
    용도다(`harness.LEGACY_RSI_GATE_MODE`). 없으면 그런 리포트는 `run_grid`를 통과하는
    순간 새 게이트로 조용히 다시 돈다.
    """
    portfolio_leverages: tuple[float | None, ...] = (None,)
    """포지션 정책 축(WAN-130). `None` = 채택 기본값(동시 1포지션), 숫자 = 동시 다중
    포지션(WAN-103)의 명목 상한 배수.

    **진짜 축이다**(`retap_mode`와 같은 자리, `band_bar` 같은 핀이 아니다) — 단일 대 다중은
    한 표에 나란히 놓고 부호를 봐야 하는 비교라 CLI로 연다. 기본값 `(None,)`이라 인자를
    안 주면 예전과 **비트 단위로 같은 행**이 나온다.
    """
    combine_obs: tuple[bool | None, ...] = (None,)
    """존 병합 축(WAN-149). `None` = 채택 기본값(WAN-149: 분리), `True`/`False` = 명시.

    **진짜 축이다**(`portfolio_leverages`와 같은 자리, `band_bar` 같은 핀이 아니다) —
    병합 폐지 전후를 한 표에 나란히 놓고 봐야 하는 비교라 CLI로 연다. 기본값 `(None,)`
    이라 인자를 안 주면 예전과 **비트 단위로 같은 행**이 나온다.

    ⚠️ **이 축은 탐지 파라미터라 다른 축과 성격이 다르다** — `OrderBlockParams`에 있어서
    조합마다 오더블록을 **다시 탐지**해야 한다(다른 축은 탐지를 공유한다). 그래서
    `_run_cell`이 이 값별로 탐지 결과를 따로 캐시한다. 옛 수치를 결론에 박아 둔
    리포트는 `(harness.LEGACY_COMBINE_OBS,)`로 고정한다.
    """
    max_zone_widths_atr: tuple[ZoneWidthArg, ...] = (UNSET,)
    """존폭 필터 축(WAN-158 옵트인 → WAN-159 채택 기본값). `UNSET` = 채택 기본값(WAN-159:
    `1.28`, 좁은 존만), `None` = **끄기**(전부 매매), 숫자 = 존폭 ÷ ATR가 그 값 이하만 진입.

    **진짜 축이다**(`combine_obs`와 같은 자리, `band_bar` 같은 핀이 아니다) — 필터 on/off를
    한 표에 나란히 놓고 보라고 여는 축이다. 기본값 `(UNSET,)`이라 인자를 안 주면 채택
    기본값(1.28)으로 예전과 **비트 단위로 같은 행**이 나온다.

    ⚠️ **끄기는 `None`(= CLI `none`)이고 미지정(`UNSET`)과 다르다**(WAN-159) — 채택 기본값이
    `1.28`이 된 뒤로 둘이 갈라진다. `offset_bps`의 `None`("손대지 않는다")과 규약이 반대라
    센티넬로 미지정을 따로 표현한다(`harness.UNSET`).

    ⚠️ 단위는 **ATR 배수**지 퍼센트가 아니다(권고 문턱 15m 1.24 · 1h 1.32, 채택 `1.28`).
    """
    band_bar: BandBar | None = None
    """이격 밴드 표본 **고정**. None이면 채택 기본값(WAN-132: `intrabar_live`).

    `rsi_gate_mode`와 같은 자리의 핀이다(축이 아니다). WAN-132가 밴드 정본을 옮긴 뒤,
    **탭 봉 종가 밴드에서 낸 수치를 결론에 박아 둔 리포트**(wan111 등)가 자기 엔진을
    고정하는 용도다(`harness.LEGACY_BAND_BAR`).
    """

    def __post_init__(self) -> None:
        for mode in self.entry_modes:
            if mode not in ENTRY_MODES:
                raise ValueError(f"알 수 없는 진입 방식: {mode!r} (지원: {', '.join(ENTRY_MODES)})")
        for retap in self.retap_modes:
            if retap not in RETAP_MODES:
                supported = ", ".join(RETAP_MODES)
                raise ValueError(f"알 수 없는 재탭 정책: {retap!r} (지원: {supported})")
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
            if self.portfolio_leverages != (None,):
                raise ValueError(
                    "--entry-mode close와 --positions(다중 포지션)를 같이 줄 수 없습니다. "
                    "동시 다중 포지션 회계는 지정가(B안) 경로에만 있습니다(WAN-103)."
                )
            # 끄기(`none`/`None`)·미지정(`UNSET`)은 A안에 무해하다(필터가 안 걸린다).
            # 양수 문턱만 거부한다 — 그것만이 "A안이 조용히 무시하는 필터를 켰다"는 오해다.
            if any(isinstance(z, float) for z in self.max_zone_widths_atr):
                raise ValueError(
                    "--entry-mode close와 --max-zone-width-atr(존폭 문턱)를 같이 줄 수 "
                    "없습니다. 필터는 지정가 후보를 거르는 B안 경로에만 배선돼 "
                    "있습니다(WAN-158). 끄기(none)는 무해하므로 허용됩니다."
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
    retap_mode: str
    portfolio_leverage: float | None
    combine_obs: bool | None
    max_zone_width_atr: ZoneWidthArg
    fill: FillPreset
    seed: int

    @property
    def order_block(self) -> OrderBlockParams:
        """이 조합의 탐지 파라미터. `combine_obs`가 `None`이면 채택 기본값 그대로다.

        `None`을 여기서 채택 기본값으로 **푸는 것**이 중요하다 — 격자 밖에서
        `OrderBlockParams()`를 지어내면 기본값이 움직여도 그 경로만 옛 값을 물고 도는
        조용한 갈라짐이 생긴다(`_pinned_base`가 `None`을 그대로 넘기는 것과 같은 원칙).
        """
        if self.combine_obs is None:
            return OrderBlockParams()
        return OrderBlockParams(combine_obs=self.combine_obs)

    @property
    def portfolio(self) -> PortfolioParams | None:
        """이 조합의 포트폴리오 파라미터. 단일 포지션이면 `None`.

        `max_concurrent`·`one_per_zone`은 기본값 그대로다 — WAN-103/108이 발표 수치를 낸
        설정이 그것이라, 확인 격자가 다른 값을 쓰면 비교 대상이 달라진다.
        """
        if self.portfolio_leverage is None:
            return None
        return PortfolioParams(leverage=self.portfolio_leverage)


def iter_combos(grid: Grid) -> list[Combo]:
    """격자의 모든 조합을 결정적 순서로 열거한다."""
    combos: list[Combo] = []
    for entry_mode in grid.entry_modes:
        for retap_mode in grid.retap_modes:
            for take_profit_r in grid.take_profit_rs:
                for offset_bps in grid.offsets_bps:
                    for leverage in grid.portfolio_leverages:
                        for combine_obs in grid.combine_obs:
                            for zone_width in grid.max_zone_widths_atr:
                                for fill in grid.fills:
                                    for seed in iter_seeds(fill, grid.seeds):
                                        combos.append(
                                            Combo(
                                                entry_mode=entry_mode,
                                                take_profit_r=take_profit_r,
                                                offset_bps=offset_bps,
                                                retap_mode=retap_mode,
                                                portfolio_leverage=leverage,
                                                combine_obs=combine_obs,
                                                max_zone_width_atr=zone_width,
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

    collect_artifacts: bool = False
    """거래별 산출물(`RunArtifact`)을 모을지 (WAN-106 — `--trades`/`--equity`/`--persist`).

    기본은 `False`다. 요약 1행만 필요한 격자에서 결과·셋업 진단을 통째로 들고 다니면
    (특히 병렬 실행에서 프로세스 경계를 넘겨야 하므로) 메모리·직렬화 비용만 든다.
    """
    revision: str = UNKNOWN_REVISION
    """실행 지문에 실을 코드 리비전(WAN-106). 부모가 한 번 구해 워커에 실어 보낸다 —
    워커마다 `git`을 부르면 느릴 뿐 아니라, 실행 도중 워킹트리가 바뀌면 같은 격자의
    행들이 서로 다른 지문을 받는다."""
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


@dataclass(frozen=True)
class _CellTask:
    """fan-out 한 단위 = (심볼, TF).

    격자 축(`grid`)·실행 설정(`options`)을 통째로 들고 다니는 이유는 **워커가 자기
    데이터를 자기가 로드**하게 하기 위해서다(wan104 `CellSpec`과 같은 패턴). 부모가
    로드해서 넘기면 심볼당 수백 MB의 1분봉을 프로세스 경계로 pickle해야 하는데
    (wan70이 주석으로 남긴 그 비용), 경계를 (심볼, TF)로 잡으면 워커는 **자기 심볼만**
    읽으므로 그 직렬화가 통째로 사라진다. 대가는 같은 심볼을 TF 수만큼 중복 로드하는
    것인데, 그건 원래 직렬 실행도 하던 일이라 새로 생기는 손해가 아니다.

    이 경계는 parquet 캐시와도 맞물린다: `OhlcvStore`의 캐시 키가 (심볼, TF)이고
    (`_cache_paths`) 작업 단위가 그 키와 같으므로 **두 워커가 같은 캐시 파일에 쓰는 일이
    없다**. 조합(익절 R 등)으로 잘랐다면 같은 (심볼, TF)를 맡은 워커 여러 개가 한 파일에
    동시에 써 캐시가 깨진다 — 경계를 여기 두는 이유 중 하나다.
    """

    symbol: str
    timeframe: str
    grid: Grid
    options: RunOptions
    fair_window: bool


@dataclass(frozen=True)
class RunArtifact:
    """한 조합·구간의 **거래 단위** 산출물 (WAN-106).

    요약 1행(`RunRow`)이 답하지 못하는 "언제 샀나 · 어디서 손절났나 · 못 산 자리는
    어디였나"를 담는다. `fingerprint`는 이 거래들이 **어느 엔진의 것인지**를 실행
    단위로 못 박는 지문이고, 적재·내보내기 양쪽이 같은 것을 쓴다.
    """

    row: RunRow
    fingerprint: RunFingerprint
    result: BacktestResult
    stats: ZoneLimitStats | None
    setups: tuple[SetupDiagnostic, ...]


@dataclass(frozen=True)
class _CellOutcome:
    """한 셀의 산출물. 로그를 **값으로** 들고 나오는 게 핵심이다.

    워커가 stderr에 직접 쓰면 여러 프로세스의 줄이 섞여 순서가 실행마다 달라진다.
    부모가 제출 순서대로 받아 찍으면 stderr도 직렬 실행과 같은 순서가 된다.
    """

    rows: list[RunRow]
    logs: tuple[str, ...]
    artifacts: tuple[RunArtifact, ...] = ()
    """`options.collect_artifacts`일 때만 채워진다(WAN-106). 적재는 **부모가** 한다 —
    워커가 각자 SQLite에 쓰면 같은 파일에 동시 쓰기가 되고, 그 순간 `--jobs`가 결과를
    바꾸는 축이 된다(이 CLI가 지키기로 한 성질을 깬다)."""


def _pinned_base(grid: Grid) -> ConfluenceParams | None:
    """격자가 **고정**한 전략 필드만 얹은 베이스 파라미터. 고정이 없으면 `None`.

    `None`을 그대로 돌려주는 것이 중요하다 — `build_params(base=None)`가 "채택 기본값에
    맡긴다"는 뜻이고, 여기서 `ConfluenceParams()`를 지어내면 나중에 기본값이 움직여도
    이 경로만 옛 값을 물고 도는 조용한 갈라짐이 생긴다(`build_params` 독스트링).
    """
    if grid.rsi_gate_mode is None and grid.band_bar is None:
        return None
    base = ConfluenceParams()
    if grid.rsi_gate_mode is not None:
        base = base.model_copy(update={"rsi_gate_mode": grid.rsi_gate_mode})
    if grid.band_bar is not None:
        base = pin_band_bar(base, grid.band_bar)
    return base


def _run_cell(task: _CellTask) -> _CellOutcome:
    """(심볼, TF) 하나를 돌아 그 셀의 모든 조합·구간 행을 낸다.

    구간마다 오더블록을 한 번만 탐지해 그 구간의 조합들이 공유한다 — 탐지는 컨플루언스
    파라미터와 무관하므로 결과는 같고 실행 시간만 줄어든다.

    직렬·병렬이 **같은 이 함수**를 부른다. 두 경로에 각자의 루프를 두면 언젠가 한쪽만
    고쳐져 `--jobs`가 숫자를 바꾸는 축이 된다 — 도구 개선이 채택 수치를 흔드는 그 사고를
    막으려고 경로를 하나로 유지한다.
    """
    grid, options = task.grid, task.options
    symbol, timeframe = task.symbol, task.timeframe
    combos = iter_combos(grid)
    segments = segments_for(oos=options.oos, walkforward=options.walkforward)

    market = load_market_data(
        symbol,
        timeframe,
        years=options.years,
        start_ms=options.start_ms,
        end_ms=options.end_ms,
        need_1m=grid.needs_1m or task.fair_window,
        funding=options.funding,
        db_path=options.db_path,
        cache_dir=options.cache_dir,
    )
    if market.empty:
        return _CellOutcome([], (f"[run] {symbol} {timeframe}: 상위TF 데이터 없음 — 건너뜀",))
    if grid.needs_1m and market.df_1m.empty:
        return _CellOutcome(
            [], (f"[run] {symbol} {timeframe}: 1분봉 없음 — 지정가 평가 불가, 건너뜀",)
        )

    cfg = build_config(
        timeframe,
        fee_rate=options.fee_rate,
        maker_fee_rate=options.maker_fee_rate,
        slippage=options.slippage,
        funding_enabled=options.funding,
    )
    rows: list[RunRow] = []
    artifacts: list[RunArtifact] = []
    for segment in segments:
        window = slice_market(market, segment)
        if window.empty:
            continue
        # 존 병합 축(WAN-149)은 **탐지** 파라미터라 값마다 다시 탐지해야 한다. 그 밖의
        # 축은 탐지와 무관하므로 같은 결과를 공유한다 — 그래서 캐시 키가 이 값 하나다.
        ob_cache: dict[bool | None, OrderBlockResult] = {}
        for combo in combos:
            if combo.entry_mode == "zone_limit" and window.df_1m.empty:
                continue
            ob_params = combo.order_block
            if combo.combine_obs not in ob_cache:
                ob_cache[combo.combine_obs] = detect_order_blocks(window, ob_params)
            ob_result = ob_cache[combo.combine_obs]
            params = build_params(
                entry_mode=combo.entry_mode,
                take_profit_r=combo.take_profit_r,
                offset_bps=combo.offset_bps,
                fill=combo.fill,
                seed=combo.seed,
                retap_mode=combo.retap_mode,
                short_enabled=grid.short_enabled,
                max_zone_width_atr=combo.max_zone_width_atr,
                base=_pinned_base(grid),
            )
            portfolio = combo.portfolio
            wants_setups = (
                options.collect_artifacts and combo.entry_mode == "zone_limit" and portfolio is None
            )
            setup_sink: list[SetupDiagnostic] | None = [] if wants_setups else None
            outcome = run_once(
                window,
                params=params,
                cfg=cfg,
                order_block_result=ob_result,
                fair_window=task.fair_window,
                portfolio=portfolio,
                setup_sink=setup_sink,
            )
            row = build_row(
                outcome,
                window,
                segment=segment,
                params=params,
                fill_name=combo.fill.name,
                portfolio=portfolio,
                order_block=ob_params,
            )
            rows.append(row)
            if options.collect_artifacts:
                artifacts.append(
                    RunArtifact(
                        row=row,
                        fingerprint=fingerprint_for(
                            row,
                            params=params,
                            cfg=cfg,
                            revision=options.revision,
                            order_block=ob_params,
                        ),
                        result=outcome.result,
                        stats=outcome.stats,
                        setups=tuple(setup_sink or ()),
                    )
                )
    return _CellOutcome(
        rows,
        (
            f"[run] {symbol} {timeframe}: {len(market.htf_df)}봉, "
            f"1분봉 {len(market.df_1m)}행, 펀딩 {len(market.funding_rates)}건 "
            f"→ {len(combos) * len(segments)}조합",
        ),
        tuple(artifacts),
    )


def fingerprint_for(
    row: RunRow,
    *,
    params: ConfluenceParams,
    cfg: BacktestConfig,
    revision: str,
    order_block: OrderBlockParams | None = None,
) -> RunFingerprint:
    """실행 지문을 만든다 (WAN-106).

    좌표(`RunRow`)와 **실제로 엔진에 넘어간 객체**(`params`/`cfg`)에서 만든다 —
    라벨을 따로 조립하면 "다중으로 돌고 single 라벨이 붙는" WAN-95 부류의 거짓말이
    가능해진다(`build_row`가 `portfolio`를 그대로 받는 것과 같은 이유).
    """
    return RunFingerprint(
        symbol=row.symbol,
        timeframe=row.timeframe,
        segment=row.segment,
        window=row.window,
        start_time=row.start_time,
        end_time=row.end_time,
        entry_mode=params.entry_mode,
        fill=row.fill,
        seed=row.seed,
        position_mode=row.position_mode,
        portfolio_leverage=row.portfolio_leverage,
        confluence_json=params.model_dump_json(),
        order_block_json=(order_block or OrderBlockParams()).model_dump_json(),
        config_json=cfg.model_dump_json(),
        revision=revision,
    )


def _iter_outcomes(tasks: Sequence[_CellTask], workers: int) -> Iterator[_CellOutcome]:
    """셀 결과를 **제출 순서대로** 흘린다(워커 수와 무관).

    `Executor.map`은 완료 순서가 아니라 제출 순서로 내주므로, 워커 수가 몇이든 부모가
    보는 순서는 직렬 실행과 같다(wan70/71/84가 `--jobs` 무관 결과 동일성에 기대는 그
    성질). 지연 반복이라 직렬 경로의 로그는 지금처럼 셀이 끝날 때마다 흐른다.
    """
    if workers <= 1:
        yield from map(_run_cell, tasks)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(_run_cell, tasks)


def run_grid_full(
    grid: Grid,
    options: RunOptions,
    *,
    log: bool = True,
    jobs: int = 1,
) -> tuple[list[RunRow], list[RunArtifact]]:
    """`run_grid`와 같되 거래 단위 산출물도 함께 낸다 (WAN-106).

    `options.collect_artifacts`가 꺼져 있으면 두 번째 값은 항상 빈 목록이다 —
    `run_grid`(요약 전용)와 **같은 이 함수**를 타므로 두 경로가 갈라지지 않는다.

    (심볼, TF)마다 데이터를 한 번만 로드하고, 구간마다 오더블록을 한 번만 탐지해 그
    구간의 조합들이 공유한다 — 탐지는 컨플루언스 파라미터와 무관하므로 결과는 같고
    실행 시간만 줄어든다.

    `jobs>1`(또는 `JOBS_AUTO`)이면 그 (심볼, TF) 단위로 `ProcessPoolExecutor`에 fan-out
    한다(WAN-121). 병렬화는 **일을 나눠 맡길 뿐 계산을 바꾸지 않으므로** 행의 내용도
    순서도 직렬과 같다 — 셀끼리 상태를 공유하지 않고(각자 자기 데이터를 로드한다),
    결과는 제출 순서로 모은다.
    """
    fair_window = grid.mixes_entry_modes if options.fair_window is None else options.fair_window
    tasks = [
        _CellTask(
            symbol=symbol,
            timeframe=timeframe,
            grid=grid,
            options=options,
            fair_window=fair_window,
        )
        for symbol in grid.symbols
        for timeframe in grid.timeframes
    ]
    rows: list[RunRow] = []
    artifacts: list[RunArtifact] = []
    for outcome in _iter_outcomes(tasks, resolve_jobs(jobs, len(tasks))):
        rows.extend(outcome.rows)
        artifacts.extend(outcome.artifacts)
        for message in outcome.logs:
            _log(log, message)
    return rows, artifacts


def run_grid(
    grid: Grid,
    options: RunOptions,
    *,
    log: bool = True,
    jobs: int = 1,
) -> list[RunRow]:
    """격자를 돌아 조합별 1행을 낸다(요약 전용 — 거래 단위 산출물은 버린다)."""
    rows, _ = run_grid_full(grid, options, log=log, jobs=jobs)
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
        "--retap-mode",
        help=(
            f"재탭 정책(콤마 복수 = 격자). 지원: {', '.join(RETAP_MODES)}. "
            "기본: 채택 기본값 every_tap"
        ),
    )
    strategy.add_argument(
        "--positions",
        help=(
            "포지션 정책 축(콤마 복수 = 격자). single(기본, 동시 1포지션 = 채택 기본값) "
            "또는 숫자 = 동시 다중 포지션의 명목 상한 배수(WAN-103). "
            "예: --positions single,3 → 단일 대 3배 다중을 한 표에서 비교"
        ),
    )
    strategy.add_argument(
        "--fill",
        help=f"체결 가정(기본 baseline). 지원: {', '.join(p.name for p in FILL_PRESETS)}",
    )
    strategy.add_argument(
        "--fill-penetration-bps", type=float, help="--fill 대신 관통 요구를 직접 지정"
    )
    strategy.add_argument(
        "--combine-obs",
        help=(
            "존 병합 축(WAN-149). true/false 콤마 복수. "
            "안 주면 채택 기본값(false = 원본 존 단위 분리)"
        ),
    )
    strategy.add_argument(
        "--max-zone-width-atr",
        help=(
            "존폭 필터 축(WAN-159 채택 기본값 1.28). 존폭÷ATR가 이 값 이하인 셋업만 진입. "
            "콤마 복수 = 격자이며 none = 필터 끄기(전부 매매). 안 주면 채택 기본값(1.28). "
            "단위는 ATR 배수지 퍼센트가 아니다(권고: 15m 1.24 · 1h 1.32, 채택 1.28). "
            "예: --max-zone-width-atr none,1.28"
        ),
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

    execution = parser.add_argument_group("실행")
    execution.add_argument(
        "--jobs",
        default="1",
        metavar="N",
        help=(
            "(심볼, TF) 단위 병렬 워커 수(기본 1 = 직렬). auto 또는 0이면 CPU 코어 수. "
            "결과는 --jobs 값과 무관하게 동일하다"
        ),
    )

    output = parser.add_argument_group("출력")
    output.add_argument("--format", default="table", choices=list(FORMATS))
    output.add_argument("--out", help="결과를 파일로 저장(경로)")
    output.add_argument("--quiet", action="store_true", help="진행 로그(stderr) 끄기")

    detail = parser.add_argument_group("거래별 내역 (WAN-106)")
    detail.add_argument(
        "--trades",
        metavar="PATH",
        help="거래별 CSV 저장(KST·UTC 병기, 시드 변화 포함). 단일 조합일 때만",
    )
    detail.add_argument("--equity", metavar="PATH", help="시드곡선 CSV 저장. 단일 조합일 때만")
    detail.add_argument(
        "--persist",
        action="store_true",
        help="거래·미체결 셋업·시드곡선을 DB에 적재한다(격자 전체 가능 — 조합마다 실행 지문)",
    )
    detail.add_argument(
        "--persist-db",
        default=DB_PATH,
        help=f"적재할 DB 경로(기본 {DB_PATH} — OHLCV와 같은 파일, 테이블만 다름)",
    )
    detail.add_argument(
        "--persist-replace",
        action="store_true",
        help="같은 실행 지문이 이미 있으면 덮어쓴다(기본은 거부)",
    )
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
    entry_modes = split_list(args.entry_mode)
    return Grid(
        symbols=tuple(normalize_symbol(s) for s in split_list(args.symbol)),
        timeframes=split_list(args.tf),
        entry_modes=entry_modes,
        take_profit_rs=(
            split_floats(args.tp_r, label="--tp-r") if args.tp_r else (_default_tp_r(),)
        ),
        offsets_bps=(
            split_floats(args.offset_bps, label="--offset-bps")
            if args.offset_bps
            else _default_offsets_bps(entry_modes)
        ),
        retap_modes=(split_list(args.retap_mode) if args.retap_mode else _default_retap_modes()),
        fills=_fills_from_args(args),
        portfolio_leverages=_positions_from_args(args),
        combine_obs=_combine_obs_from_args(args),
        max_zone_widths_atr=_zone_widths_from_args(args),
        seeds=split_ints(args.seeds, label="--seeds") if args.seeds else None,
        short_enabled=short_enabled,
    )


#: `--positions`에서 "동시 1포지션(채택 기본값)"을 가리키는 토큰. 숫자로 표현하지 않는
#: 이유는 단일 포지션 경로에 레버리지 축이 없기 때문이다 — `1`을 단일의 뜻으로 쓰면
#: "다중 1배"와 구분되지 않는데, 그 둘은 실제로 다른 경로다(WAN-108 대조군 설계).
SINGLE_POSITION_TOKEN = "single"

#: `--combine-obs`가 받는 토큰 → 불리언.
_BOOL_TOKENS: dict[str, bool] = {"true": True, "false": False, "on": True, "off": False}


def _combine_obs_from_args(args: argparse.Namespace) -> tuple[bool | None, ...]:
    """`--combine-obs true,false` → `(True, False)`. 안 주면 `(None,)`(채택 기본값).

    `None`을 여기서 `False`로 풀지 않는 이유는 `_default_offsets_bps`와 같다 — 채택
    기본값을 CLI가 복사하면 기본값이 움직일 때 이 경로만 옛 값을 물고 돈다.
    """
    if not args.combine_obs:
        return (None,)
    values: list[bool | None] = []
    for token in split_list(args.combine_obs):
        try:
            value = _BOOL_TOKENS[token.lower()]
        except KeyError as exc:
            supported = ", ".join(_BOOL_TOKENS)
            raise ValueError(
                f"--combine-obs에 알 수 없는 값이 있습니다: {token!r} (지원: {supported})"
            ) from exc
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("--combine-obs가 비어 있습니다.")
    return tuple(values)


#: `--max-zone-width-atr`에서 "필터 끄기"를 가리키는 토큰. 0으로 표현하지 않는 이유는
#: `--positions single`과 같다 — 0은 "존폭 0 이하만" 이라는 다른 뜻이 되고, 필드가 `gt=0`
#: 이라 값으로도 못 쓴다. ⚠️ WAN-159 이후 `none`(끄기)은 인자 미지정(채택 기본값 1.28)과
#: 다르다.
NO_ZONE_WIDTH_TOKEN = "none"


def _zone_widths_from_args(args: argparse.Namespace) -> tuple[ZoneWidthArg, ...]:
    """`--max-zone-width-atr none,1.24` → `(None, 1.24)`. 안 주면 `(UNSET,)`(채택 기본값).

    ⚠️ **`none`은 끄기(`None`)이고, 인자를 안 준 것(`UNSET` = 채택 기본값 1.28)과 다르다**
    (WAN-159) — 채택 기본값이 켜진 뒤로 둘이 갈라진다. `none`을 "채택 기본값"으로 착각하면
    「필터 끔」이라 믿으며 1.28로 도는 이중 필터가 된다.

    ⚠️ 값은 **ATR 배수**다. 퍼센트로 착각한 값(0.01 등)도 문법상 유효하므로 여기서
    막을 수는 없다 — `--help`와 문서가 단위를 못 박는다(WAN-112 단위 함정).
    """
    if not args.max_zone_width_atr:
        return (UNSET,)
    values: list[ZoneWidthArg] = []
    for token in split_list(args.max_zone_width_atr):
        if token.lower() == NO_ZONE_WIDTH_TOKEN:
            value: float | None = None
        else:
            try:
                value = float(token)
            except ValueError as exc:
                raise ValueError(
                    f"--max-zone-width-atr에 알 수 없는 값이 있습니다: {token!r} "
                    f"({NO_ZONE_WIDTH_TOKEN} 또는 ATR 배수 숫자)"
                ) from exc
            if value <= 0:
                raise ValueError(f"--max-zone-width-atr의 문턱은 0보다 커야 합니다: {token!r}")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("--max-zone-width-atr가 비어 있습니다.")
    return tuple(values)


def _positions_from_args(args: argparse.Namespace) -> tuple[float | None, ...]:
    """`--positions single,3` → `(None, 3.0)`. 안 주면 `(None,)`(채택 기본값)."""
    if not args.positions:
        return (None,)
    values: list[float | None] = []
    for token in split_list(args.positions):
        if token == SINGLE_POSITION_TOKEN:
            value: float | None = None
        else:
            try:
                value = float(token)
            except ValueError as exc:
                raise ValueError(
                    f"--positions에 알 수 없는 값이 있습니다: {token!r} "
                    f"({SINGLE_POSITION_TOKEN} 또는 레버리지 숫자)"
                ) from exc
            if value <= 0:
                raise ValueError(f"--positions의 레버리지는 0보다 커야 합니다: {token!r}")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("--positions가 비어 있습니다.")
    return tuple(values)


def _default_tp_r() -> float:
    """익절 R을 안 주면 채택 기본값 그대로."""
    return build_params().take_profit_r


def _default_retap_modes() -> tuple[str, ...]:
    """재탭 정책을 안 주면 채택 기본값 하나만(`every_tap`).

    `_default_offsets_bps`와 같은 이유로 `("every_tap",)`를 하드코딩하지 않고 채택
    기본값에서 읽는다 — 기본값이 바뀌면 CLI 기본 실행도 따라가야 조용한 갈라짐이 없다.
    """
    return (build_params().retap_mode,)


def _default_offsets_bps(entry_modes: tuple[str, ...]) -> tuple[float, ...]:
    """오프셋을 안 주면 **채택 기본값 그대로**(WAN-112: 2bp).

    여기에 `(0.0,)`을 하드코딩하면 CLI가 `ConfluenceParams`의 기본 오프셋을 말없이
    덮어써서, 인자 없는 실행만 혼자 옛 엔진(0bp)을 도는 조용한 갈라짐이 생긴다 —
    "인자 없이 돌리면 채택 기본값 그대로"라는 이 CLI의 약속(WAN-101)이 깨진다.

    단 **종가 진입이 격자에 섞이면 0bp로 내린다**: A안은 오프셋을 읽지 않으므로
    (`apply_zone_limit_offset` 호출부가 B안뿐) 종가 팔에 2bp를 얹을 방법이 없다. 그
    상태로 지정가 팔만 2bp를 물리면 두 팔이 **진입 방식 말고도 오프셋까지 달라져**,
    진입 방식을 격리하려던 대조표가 두 변수를 섞어 버린다(WAN-95 `CLOSE_ENTRY_PARAMS`가
    같은 이유로 다른 필드를 전부 맞춘다). 오프셋을 명시로 주면 `Grid`가 이 조합을 거부한다.
    """
    if "close" in entry_modes:
        return (0.0,)
    return (build_params().zone_limit_offset_bps,)


def wants_detail(args: argparse.Namespace) -> bool:
    """거래 단위 산출물이 필요한 실행인가 (`--trades`/`--equity`/`--persist`)."""
    return bool(args.trades or args.equity or args.persist)


def options_from_args(args: argparse.Namespace) -> RunOptions:
    detail = wants_detail(args)
    return RunOptions(
        collect_artifacts=detail,
        revision=engine_revision() if detail else UNKNOWN_REVISION,
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
        jobs = parse_jobs(args.jobs)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    if args.oos and args.walkforward:
        print("오류: --oos와 --walkforward는 함께 쓸 수 없습니다.", file=sys.stderr)
        return 2

    rows, artifacts = run_grid_full(grid, options, log=not args.quiet, jobs=jobs)
    try:
        _write_detail(args, artifacts, log=not args.quiet)
    except (ValueError, DuplicateRunError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    text = render(rows, args.format)
    if args.out:
        path = write_output(text, args.out)
        _log(not args.quiet, f"[run] 저장: {path}")
    else:
        print(text)
    return 0


def _single_artifact(artifacts: Sequence[RunArtifact], flag: str) -> RunArtifact:
    """거래별 출력은 **단일 조합**일 때만 의미가 있다 — 격자면 거부한다.

    조용히 마지막 조합만 내보내면 파일 이름과 내용이 어긋난 채로 남고, 그 파일이
    나중에 "채택 엔진의 거래"로 인용된다(WAN-95의 교훈). DB 적재(`--persist`)는 조합마다
    실행 지문이 붙어 섞이지 않으므로 격자에서도 허용한다 — 그쪽이 이 방어의 대안이다.
    """
    if not artifacts:
        raise ValueError(f"{flag}: 내보낼 실행 결과가 없습니다(데이터 없음 또는 빈 격자).")
    if len(artifacts) > 1:
        raise ValueError(
            f"{flag}는 단일 조합에만 쓸 수 있는데 {len(artifacts)}개 조합이 나왔습니다. "
            "축을 하나로 좁히거나(--symbol/--tf 하나, --oos 없이), 격자 전체를 남기려면 "
            "--persist로 DB에 적재하세요(조합마다 실행 지문이 붙습니다)."
        )
    return artifacts[0]


def _write_detail(args: argparse.Namespace, artifacts: Sequence[RunArtifact], *, log: bool) -> None:
    """`--trades`/`--equity`/`--persist` 출력 (WAN-106)."""
    if args.trades:
        artifact = _single_artifact(artifacts, "--trades")
        frame = trades_to_display_frame(artifact.result, include_utc=True)
        path = write_output(str(frame.to_csv(index=False)), args.trades)
        _log(log, f"[run] 거래별 CSV 저장: {path} ({len(frame)}건)")
    if args.equity:
        artifact = _single_artifact(artifacts, "--equity")
        frame = equity_to_display_frame(artifact.result, include_utc=True)
        path = write_output(str(frame.to_csv(index=False)), args.equity)
        _log(log, f"[run] 시드곡선 CSV 저장: {path} ({len(frame)}점)")
    if args.persist:
        persist_artifacts(
            artifacts,
            db_path=args.persist_db,
            replace=args.persist_replace,
            log=log,
        )


def persist_artifacts(
    artifacts: Sequence[RunArtifact],
    *,
    db_path: str,
    replace: bool = False,
    log: bool = True,
) -> list[str]:
    """산출물을 DB에 적재하고 `run_id` 목록을 반환한다 (WAN-106).

    **부모 프로세스에서 한 번에** 쓴다 — 워커가 각자 쓰면 같은 SQLite 파일에 동시
    쓰기가 되어 `--jobs`가 결과를 바꾸는 축이 된다.
    """
    if not artifacts:
        raise ValueError("--persist: 적재할 실행 결과가 없습니다(데이터 없음 또는 빈 격자).")
    run_ids: list[str] = []
    created_at = int(datetime.now(tz=UTC).timestamp() * 1000)
    with BacktestRunStore(db_path) as store:
        for artifact in artifacts:
            run_id = store.save_run(
                artifact.fingerprint,
                artifact.result,
                stats=artifact.stats,
                setups=artifact.setups,
                replace=replace,
                created_at=created_at,
            )
            run_ids.append(run_id)
            _log(
                log,
                f"[run] 적재: {run_id} — {artifact.fingerprint.label()} "
                f"(거래 {len(artifact.result.trades)}건 · 셋업 {len(artifact.setups)}건)",
            )
    return run_ids


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
