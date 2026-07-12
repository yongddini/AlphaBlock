# 패키지 레이어 규칙 (import 방향)

AlphaBlock 은 아래 레이어 순서로 의존한다. **위 레이어는 아래 레이어를 임포트할 수 있고, 아래 레이어는 위 레이어를 임포트하지 않는다.** 이 규칙을 어기면 패키지 `__init__` 의 eager re-export 와 맞물려 **순환 임포트**(예: WAN-42)가 난다.

```
낮음(기반)
  common      # 레이어 독립 유틸(하트비트, 텔레그램 전송/팩토리). 표준 라이브러리 + config 까지만 의존.
  config      # 설정 로딩(pydantic-settings)
  data        # 시세 수집·저장 (OHLCV, 펀딩) — WAN-6
  strategy    # 오더블록/컨플루언스 시그널·지표 — WAN-7/23
  execution   # 주문 실행·리스크·사이징 — WAN-9
  backtest    # 백테스팅 엔진 — WAN-8
  paper       # 페이퍼 거래 영속·성과·패리티 — WAN-33
  live        # 실시간 러너·알림·헬스워치 — WAN-25/31/32
  dashboard   # 운영/성과 대시보드(Streamlit) — WAN-30
  cli         # 진입점(alphablock)
높음(응용)
```

## 규칙 요약

- `common` 은 **어떤 프로젝트 패키지도** 임포트하지 않는다(`config` 제외). 여러 레이어가 공유하는 가벼운 도구(예: `HeartbeatStore`, `TelegramClient`, `build_telegram_client`)만 둔다.
- `data` / `strategy` / `execution` / `backtest` 는 `live` · `paper` 를 **절대 임포트하지 않는다.** 하위 레이어가 상위의 유틸이 필요하면, 그 유틸을 `common`(또는 더 낮은 레이어)으로 내리거나 **콜백/의존성 주입**으로 넘겨받는다.
- `live` 와 `paper` 는 사실상 최상위 형제 레이어다. `live` 는 `paper.store` 로 체결을 영속화하고, `paper.store` 는 `live.paper` 의 도메인 모델(`ClosedTrade`)을 참조한다. 이 형제 결합은 허용하되, **`live/__init__.py` 는 PEP 562(`__getattr__`) 지연 로딩**으로 공개 심볼을 노출해, `from live.paper import ClosedTrade` 한 줄이 러너·페이퍼 스토어 전체를 끌어와 순환을 만들지 않게 한다.

## WAN-42 에서 한 일

1. `live/heartbeat.py`, `live/telegram.py`(+`build_telegram_client`)를 `common/` 으로 이동. → `data` 가 `live` 를 임포트하던 두 지점(`data/collector.py`, `data/repair.py`)이 `common` 을 임포트하도록 바뀌어 `data → live` 역방향 의존이 사라졌다.
2. `live/__init__.py` 를 지연 re-export 로 전환해 `paper.store ↔ live.paper` 형제 순환을 끊었다.

## 강제(enforcement)

- `tests/test_import_isolation.py`
  - 각 최상위 패키지를 **독립 하위 프로세스**에서 단독 임포트해 성공하는지 검증(우연한 임포트 순서로 순환을 놓치지 않도록).
  - 하위 레이어 소스가 상위 레이어를 임포트하지 않는지 **AST 정적 검사**(함수 내부 지연 임포트 포함).
- 더 엄격히 강제하려면 [`import-linter`](https://import-linter.readthedocs.io/) 의 layered/forbidden contract 로 CI 에 추가할 수 있다(선택). 현재는 위 pytest 로 충분히 회귀를 막는다.
