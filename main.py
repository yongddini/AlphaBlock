"""AlphaBlock 엔트리포인트.

현재는 셋업 검증용. 로드된 설정 요약을 출력한다.
데이터 수집·전략·실행 로직은 이후 이슈에서 추가된다.
"""

from __future__ import annotations

from config import get_settings


def main() -> None:
    settings = get_settings()
    print("AlphaBlock 설정 로드됨:")
    print(f"  market_type   = {settings.market_type}")
    print(f"  symbol        = {settings.symbol}")
    print(f"  timeframe     = {settings.timeframe}")
    print(f"  live_trading  = {settings.live_trading}")
    print(f"  credentials   = {'설정됨' if settings.has_credentials else '없음'}")


if __name__ == "__main__":
    main()
