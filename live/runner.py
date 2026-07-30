"""실시간(폴링) 페이퍼 러너 진입점 (WAN-25 / WAN-45).

채택 기본값(`entry_mode="zone_limit"`)에서 사용자의 실매매는 **존-지정가**다. 이
모듈은 CLI/`python -m live.runner` 공용 진입점(`run_signal_runner`)을 유지하되,
실제 폴링은 존-지정가 페이퍼 러너(`live.zone_limit_runner.run_zone_limit_runner`,
WAN-45)에 위임한다.

## 배경 — 옛 A안(종가 시그널) 러너 제거 (WAN-208)

과거에는 이 모듈에 봉 마감 종가 시그널을 내는 A안 러너(`SignalRunner`·
`WatermarkStore`·`Notifier` 배선)가 있었다. WAN-95 이후 채택 진입이 지정가로
바뀌면서 그 경로는 **도달 불가**(dead)였고 — 기본값에서 `run_signal_runner`가
항상 B안으로 위임했다 — WAN-198 → WAN-200 → WAN-208의 A안 폐기 정리로 삭제됐다.
진입점 이름(`run_signal_runner`)과 `python -m live.runner` 프로덕션 명령은 그대로
유지된다: B안으로 위임할 뿐이다(테스트가 위임을 동작으로 고정한다).
"""

from __future__ import annotations

import argparse
import logging

from common.telegram import build_telegram_client
from common.timefmt import kst_log_format, use_kst_logging
from config.settings import Settings, get_settings

_logger = logging.getLogger(__name__)

#: (symbol, timeframe) 시리즈 키.
Series = tuple[str, str]


def build_series(settings: Settings) -> list[Series]:
    """설정의 심볼×타임프레임 조합을 시리즈 목록으로 만든다.

    존-지정가 러너(`live.zone_limit_runner.build_series`)와 **같은 결과**를 내야
    한다(WAN-191 — 두 진입점이 같은 조합을 봐야 한다). 테스트가 동작으로 고정한다.
    """
    return [
        (symbol, timeframe)
        for symbol in settings.live_signal_symbols
        for timeframe in settings.live_signal_timeframes
    ]


def run_signal_runner(
    settings: Settings | None = None,
    *,
    once: bool = False,
    dry_run: bool = False,
    test_message: bool = False,
) -> None:
    """페이퍼 러너를 실행한다(`live` CLI/`python -m live.runner` 공용 진입).

    - `test_message=True`: 텔레그램 연결 확인용 메시지 1건만 보내고 종료.
    - 그 외에는 존-지정가 페이퍼 러너(WAN-45)에 위임한다(`once`면 1회 폴링).

    📌 채택 기본값이 지정가(`entry_mode="zone_limit"`)라 실제 폴링은 항상 B안이다
    (옛 A안 종가 시그널 경로는 WAN-208에서 제거됨). `dry_run`은 `test_message`
    경로에서만 의미가 있다 — 위임 러너는 `settings.paper_trade_notify_enabled`와
    텔레그램 설정 여부로 드라이런을 스스로 판단한다.
    """
    settings = settings or get_settings()

    if test_message:
        telegram = None if dry_run else build_telegram_client(settings)
        if telegram is None:
            _logger.error("텔레그램이 설정되지 않았습니다(ALPHABLOCK_TELEGRAM_*).")
            return
        ok = telegram.send_message("✅ AlphaBlock 시그널 러너 테스트 메시지 (WAN-25)")
        _logger.info("테스트 메시지 전송 %s", "성공" if ok else "실패")
        return

    from live.zone_limit_runner import run_zone_limit_runner

    run_zone_limit_runner(settings, once=once)


def main() -> None:
    """CLI 엔트리포인트: `python -m live.runner`."""
    parser = argparse.ArgumentParser(description="WAN-25 실시간 페이퍼 러너 (존-지정가 위임)")
    parser.add_argument("--once", action="store_true", help="한 번만 폴링하고 종료")
    parser.add_argument(
        "--test-message",
        action="store_true",
        help="테스트 메시지를 한 번 보내고 종료(텔레그램 연결 확인)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="텔레그램 전송 없이 로그로만 출력(--test-message 경로에만 적용)",
    )
    args = parser.parse_args()

    use_kst_logging()  # 로그 시각도 KST(WAN-172)
    logging.basicConfig(level=logging.INFO, format=kst_log_format())
    run_signal_runner(
        once=args.once,
        dry_run=args.dry_run,
        test_message=args.test_message,
    )


if __name__ == "__main__":
    main()
