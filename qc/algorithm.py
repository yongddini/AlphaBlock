"""QC 클라우드 업로드용 `QCAlgorithm` 셸 (WAN-181 파일럿 — ⚠️ 로컬 미검증 층).

이 파일은 QuantConnect 프로젝트에 저장소를 통째로 올렸을 때의 **진입점**이다. QC SDK
(`AlgorithmImports`)는 로컬에 존재하지 않으므로 이 모듈은 **로컬에서 실행·검증되지
않는다** — 로컬에서 검증되는 것은 이 셸이 위임하는 `qc.event_engine`까지다
(`backtest.wan181_qc_pilot` 감사). 이 분리가 파일럿의 핵심 설계다.

## 단계 설계 (한 축씩 격리 — WAN-74 감사와 같은 원칙)

1. **QC 백테스트 × 데이터 공급자(이 파일의 현재 형태)**: QC는 바이낸스 선물 분봉을
   공급하는 역할만 하고, 봉이 다 모이면 로컬에서 감사를 마친 드라이버
   (`qc.event_engine.run_event_backtest`)를 **그대로** 돌린다. 그 결과와 우리 로컬
   실행(`backtest/reports/wan181_qc_pilot_*.csv`)의 차이는 로직이 동일하므로
   **순수하게 데이터 차이**(QC CoinAPI vs 우리 수집)다.
2. **QC 스트리밍 × QC 지정가 주문(후속)**: `OnData` 스트리밍으로 탭을 실시간 판정하고
   QC `LimitOrder` · QC 체결 모델(1분 스냅샷)을 받는다 — 1단과의 차이가 **집행 모델의
   몫**이다. ⚠️ 이 단계에는 파일럿이 확인한 실시간 간극 둘이 있다: (a) 탭 시그널은
   상위TF 봉이 닫혀야 확정되는데 정본 셋업은 탭 봉 **슬롯 시작부터** 체결을 허용한다,
   (b) 동시 1포지션 아래 "실주문 하나만 걸기"는 정본의 가상 체결 스킵과 의미가 다르다.
   wan181.md §스트리밍-간극 참고 — 이 간극의 처리(가상 추적 + 미러링 vs 실주문 수용)는
   **사용자 결정**이다.
3. **QC 페이퍼(최종 목표)**: 2단이 우리 숫자를 재현할 때만 진행한다(이슈 명세:
   "QC 페이퍼는 포팅의 하류지 지름길이 아니다").

## 업로드 방법 (사용자 QC 계정 필요 — 개발자는 계정을 만들지 않는다)

QC 프로젝트에 이 저장소의 `qc/`·`strategy/`·`backtest/`·`execution/`·`common/`·
`data/`·`config/` 디렉터리를 올리고 이 파일을 메인으로 지정한다. QC 클라우드 파이썬
환경에 `pandas`·`pydantic`이 있어야 한다(2026-07 기준 QC 지원 패키지 목록에 포함 —
막히면 wan181.md §의존성 참고).
"""

from __future__ import annotations

from typing import Any

try:  # QC 클라우드에서만 존재한다. 로컬 임포트는 조용히 실패하지 않고 명시적으로 막는다.
    from AlgorithmImports import QCAlgorithm, Resolution  # type: ignore[import-not-found]

    _QC_AVAILABLE = True
except ImportError:  # pragma: no cover - 로컬에는 QC SDK가 없다.
    QCAlgorithm = object  # type: ignore[assignment, misc]
    Resolution = None  # type: ignore[assignment]
    _QC_AVAILABLE = False


class AlphaBlockPilotAlgorithm(QCAlgorithm):  # type: ignore[misc]
    """BTC 1h 채택 기본값 셀의 QC 재생 — 1단(데이터 축 격리) 형태.

    `OnData`는 분봉을 **모으기만** 하고, `OnEndOfAlgorithm`이 로컬에서 감사를 마친
    `run_event_backtest`를 그 봉들로 돌린다. 규칙 코드는 전부 저장소 모듈에 있다 —
    여기서 규칙을 다시 쓰면 두 벌이 갈라진다(WAN-100의 교훈).
    """

    #: 못 박은 채택 창(WAN-182)·감사 셀 좌표 — `backtest.wan181_qc_pilot`과 같은 값.
    PILOT_SYMBOL = "BTCUSDT"
    PILOT_TIMEFRAME = "1h"
    PILOT_START = (2020, 9, 15)
    PILOT_END = (2026, 7, 22)

    def Initialize(self) -> None:  # noqa: N802 - QC 명명 규약.
        if not _QC_AVAILABLE:
            raise RuntimeError(
                "AlgorithmImports가 없습니다 — 이 파일은 QC 클라우드 전용입니다. "
                "로컬 감사는 backtest.wan181_qc_pilot을 쓰세요."
            )
        self.SetStartDate(*self.PILOT_START)
        self.SetEndDate(*self.PILOT_END)
        self.SetCash(10_000)
        # 바이낸스 무기한선물 분봉 — 우리 1분 서브스텝과 같은 해상도.
        future = self.AddCryptoFuture(self.PILOT_SYMBOL, Resolution.Minute)
        self._qc_symbol = future.Symbol
        #: (open_time_ms, open, high, low, close, volume) 분봉 행 버퍼.
        self._minute_rows: list[tuple[int, float, float, float, float, float]] = []

    def OnData(self, data: Any) -> None:  # noqa: N802 - QC 명명 규약.
        bar = data.Bars.get(self._qc_symbol) if hasattr(data, "Bars") else None
        if bar is None:
            return
        open_time_ms = int(bar.Time.timestamp() * 1000)  # QC TradeBar.Time = 봉 시작.
        self._minute_rows.append(
            (
                open_time_ms,
                float(bar.Open),
                float(bar.High),
                float(bar.Low),
                float(bar.Close),
                float(bar.Volume),
            )
        )

    def OnEndOfAlgorithm(self) -> None:  # noqa: N802 - QC 명명 규약.
        """모인 분봉으로 로컬 검증 드라이버를 재생하고 거래 로그를 ObjectStore에 남긴다."""
        import pandas as pd

        from backtest.harness import build_config, build_params
        from backtest.sweep import timeframe_to_ms
        from qc.event_engine import run_event_backtest
        from strategy.order_blocks import OrderBlockDetector

        df_1m = pd.DataFrame(
            self._minute_rows,
            columns=["open_time", "open", "high", "low", "close", "volume"],
        )
        df_1m["closed"] = True

        # 상위TF 집계 — 우리 저장소(WAN-175)가 1분봉에서 상위TF를 만드는 규칙과 동일
        # (open=첫, high=최대, low=최소, close=끝, volume=합).
        htf_ms = timeframe_to_ms(self.PILOT_TIMEFRAME)
        slots = (df_1m["open_time"] // htf_ms) * htf_ms
        grouped = df_1m.groupby(slots)
        htf_df = pd.DataFrame(
            {
                "open_time": grouped["open_time"].first().index.astype("int64"),
                "open": grouped["open"].first().to_numpy(),
                "high": grouped["high"].max().to_numpy(),
                "low": grouped["low"].min().to_numpy(),
                "close": grouped["close"].last().to_numpy(),
                "volume": grouped["volume"].sum().to_numpy(),
            }
        ).reset_index(drop=True)
        htf_df["closed"] = True

        params = build_params()  # 채택 기본값(ConfluenceParams()) 그대로.
        cfg = build_config(self.PILOT_TIMEFRAME)
        # ⚠️ 펀딩: 1단은 QC에서 펀딩 시계열을 따로 얻지 않으므로 펀딩 없이 돌고, 로컬
        # 대조(`--no-funding` 실행)도 같은 조건으로 맞춘다 — 축을 하나만 움직인다.
        cfg = cfg.model_copy(update={"funding_enabled": False})

        ob_result = OrderBlockDetector().run(htf_df)
        outcome = run_event_backtest(
            htf_df,
            df_1m,
            self.PILOT_TIMEFRAME,
            params=params,
            cfg=cfg,
            order_block_result=ob_result,
            funding_rates=None,
        )

        lines = ["entry_time,entry_price,exit_time,exit_price,reason,quantity,realized_pnl"]
        for trade in outcome.trades:
            exit_fill = trade.exits[-1]
            lines.append(
                f"{trade.entry_time},{trade.entry_price},{exit_fill.time},{exit_fill.price},"
                f"{exit_fill.reason.value},{trade.quantity},{trade.realized_pnl}"
            )
        self.ObjectStore.Save("wan181_qc_trades.csv", "\n".join(lines))
        self.Log(
            f"WAN-181 pilot: trades={len(outcome.trades)} eligible={outcome.stats.eligible} "
            f"filled={outcome.stats.filled} ties={outcome.same_minute_fill_ties}"
        )
