"""신호 이벤트 모델 (`SignalEvent`).

한 시리즈(심볼·TF)에서 발생한 진입/청산 신호를 감싸는 값 객체다. 러너 런타임
상태 기록(`live.runtime_state`)이 이 타입을 참조한다.

## 배경 — 옛 A안(종가 시그널) 알림기 제거 (WAN-208)

과거에는 이 모듈에 컨플루언스 종가 결과를 텔레그램 메시지로 만들어 보내는 A안
알림기(`Notifier`·`collect_events`·`format_entry`/`format_exit` 등)가 있었다.
WAN-95 이후 채택 진입이 지정가로 바뀌며 그 경로는 도달 불가였고, 지정가 러너는
자체 알림기(`live.zone_limit_notifier.ZoneLimitNotifier` + `live.message_format`)를
쓴다. WAN-198 → WAN-200 → WAN-208의 A안 폐기 정리로 그 알림기는 삭제됐고,
공유 타입인 `SignalEvent`만 남는다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from strategy.confluence import ConfluenceSignal, SignalKind


class SignalEvent(BaseModel):
    """한 시리즈(심볼·TF)에서 발생한 진입/청산 신호 이벤트."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    signal: ConfluenceSignal

    @property
    def time(self) -> int:
        """신호 봉의 `open_time`(ms)."""
        return self.signal.time

    @property
    def is_entry(self) -> bool:
        return self.signal.kind is SignalKind.ENTRY

    @property
    def signal_id(self) -> str:
        """중복 판정을 위한 안정적 식별자.

        같은 봉·같은 종류의 신호는 전략을 재평가해도 동일한 id를 갖는다.
        """
        sig = self.signal
        reason = sig.exit_reason.value if sig.exit_reason is not None else "-"
        return (
            f"{self.symbol}|{self.timeframe}|{sig.kind.value}|"
            f"{sig.direction.value}|{sig.time}|{reason}"
        )
