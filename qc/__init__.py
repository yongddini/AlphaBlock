"""QuantConnect(QC) 포팅 층 (WAN-181, 길 A 파일럿).

사용자 결정(2026-07-23)으로 페이퍼 실행 경로가 **QC 클라우드**로 정해졌다. 이 패키지는
그 포팅의 첫 단 — 채택 엔진(BTC 1h 셀)을 QC의 **이벤트 구동 실행 모델**로 옮기고,
로컬에서 우리 정본 엔진과의 불일치를 감사하는 층이다.

## 설계 원칙 (WAN-171의 「같은 회계 함수 공유」를 QC 길에 적용)

정본 엔진의 **규칙 원천**(밴드 `RealtimeBand`·가격 사슬 `IntrabarLiveLimit`·사이징
`position_size`·비용 `CostModel`·펀딩 `cumulative_funding_cost`)은 **재구현하지 않고
그대로 import** 한다 — 두 벌을 만들면 어느 쪽이 맞는지 알 수 없게 된다(WAN-100의 교훈).
새로 쓰는 것은 QC가 강제하는 **실행 모델의 재배열**뿐이다: 정본은 셋업별 배치
시뮬레이션(`simulate_zone_limit_trade`) 후 일괄 시퀀싱(`sequence_with_candidates`)이지만,
QC `OnData`는 시간 순서 단일 패스이므로 모든 셋업 상태를 **한 번의 분봉 스위프**에서
증분으로 굴려야 한다. 그 재배열이 의미를 보존하는지가 `backtest.wan181_qc_pilot` 감사의
검증 대상이다.

## 구성

- `qc.event_engine` — 이벤트 구동 엔진(로컬 검증 완료 층). QC `OnData` 루프가 해야 할
  일을 QC 없이 실행·검증할 수 있는 형태로 담는다.
- `qc.algorithm` — QC 클라우드에 올릴 `QCAlgorithm` 셸(로컬 미검증 층 — QC SDK가
  로컬에 없다). 데이터 이벤트를 `event_engine`에 공급하고 거래 로그를 남긴다.

⚠️ 이 패키지는 **측정·검증 도구다**. 기본값·토대·엔진 불변, 실거래 아님
(`ALPHABLOCK_LIVE_TRADING=false` 유지). 배경: `docs/decisions/wan181.md`.
"""
