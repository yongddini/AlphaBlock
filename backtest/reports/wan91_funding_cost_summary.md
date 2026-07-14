# WAN-91 펀딩비 배선 재산출 — 펀딩 on/off 델타 + TF별 그로스/넷 분해

3심볼(BTC/ETH/SOL) × 4TF(15m/1h/4h/1d) × IS/OOS(봉 2:1 분할), 로컬 `data/ohlcv.db` 실데이터 3년. 재현: `python -m backtest.wan91_funding_cost_report`. 원자료: `backtest/reports/wan91_funding_cost.csv`.

> **방법론 caveat**: 이 리포트는 `entry_mode="close"`(A안, 현재 채택 엔진 기본값과 동일 실행 경로 — `backtest.wan87_long_only_report`와 동일 파이프라인)로 산출했다. WAN-84는 `entry_mode="zone_limit"`(B안) 매칭 널 방법론이라 이 리포트와 절대값을 직접 비교할 수 없다 — 델타(펀딩 on−off, 넷−그로스)와 부호 변화만 비교 가능하다.

## 1. 펀딩 on/off 델타 (넷 비용 기준)

| 심볼 | TF | 구간 | 엔진 | 펀딩off | 펀딩on | delta |
| -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | long_only | -0.119 | -0.134 | -0.0146 |
| BTC/USDT:USDT | 1d | IS | long_only | 0.015 | 0.014 | -0.0011 |
| BTC/USDT:USDT | 1h | IS | long_only | -0.124 | -0.133 | -0.0092 |
| BTC/USDT:USDT | 4h | IS | long_only | 0.126 | 0.122 | -0.0050 |
| ETH/USDT:USDT | 15m | IS | long_only | -0.365 | -0.376 | -0.0114 |
| ETH/USDT:USDT | 1d | IS | long_only | 0.051 | 0.050 | -0.0014 |
| ETH/USDT:USDT | 1h | IS | long_only | 0.021 | 0.011 | -0.0102 |
| ETH/USDT:USDT | 4h | IS | long_only | 0.130 | 0.126 | -0.0037 |
| SOL/USDT:USDT | 15m | IS | long_only | 0.074 | 0.064 | -0.0101 |
| SOL/USDT:USDT | 1d | IS | long_only | -0.002 | -0.002 | -0.0001 |
| SOL/USDT:USDT | 1h | IS | long_only | 0.205 | 0.198 | -0.0071 |
| SOL/USDT:USDT | 4h | IS | long_only | -0.044 | -0.045 | -0.0010 |
| BTC/USDT:USDT | 15m | OOS | long_only | -0.110 | -0.111 | -0.0011 |
| BTC/USDT:USDT | 1d | OOS | long_only | 0.082 | 0.082 | -0.0002 |
| BTC/USDT:USDT | 1h | OOS | long_only | 0.095 | 0.091 | -0.0039 |
| BTC/USDT:USDT | 4h | OOS | long_only | -0.035 | -0.037 | -0.0026 |
| ETH/USDT:USDT | 15m | OOS | long_only | 0.147 | 0.142 | -0.0056 |
| ETH/USDT:USDT | 1d | OOS | long_only | -0.035 | -0.035 | -0.0005 |
| ETH/USDT:USDT | 1h | OOS | long_only | 0.146 | 0.144 | -0.0020 |
| ETH/USDT:USDT | 4h | OOS | long_only | 0.012 | 0.012 | -0.0005 |
| SOL/USDT:USDT | 15m | OOS | long_only | -0.155 | -0.151 | +0.0048 |
| SOL/USDT:USDT | 1d | OOS | long_only | -0.009 | -0.008 | +0.0005 |
| SOL/USDT:USDT | 1h | OOS | long_only | 0.146 | 0.147 | +0.0012 |
| SOL/USDT:USDT | 4h | OOS | long_only | 0.159 | 0.160 | +0.0012 |
| BTC/USDT:USDT | 15m | IS | short_enabled | -0.602 | -0.602 | +0.0007 |
| BTC/USDT:USDT | 1d | IS | short_enabled | -0.015 | -0.015 | -0.0001 |
| BTC/USDT:USDT | 1h | IS | short_enabled | -0.139 | -0.139 | -0.0001 |
| BTC/USDT:USDT | 4h | IS | short_enabled | 0.096 | 0.098 | +0.0021 |
| ETH/USDT:USDT | 15m | IS | short_enabled | -0.685 | -0.686 | -0.0003 |
| ETH/USDT:USDT | 1d | IS | short_enabled | 0.056 | 0.055 | -0.0010 |
| ETH/USDT:USDT | 1h | IS | short_enabled | 0.074 | 0.077 | +0.0023 |
| ETH/USDT:USDT | 4h | IS | short_enabled | 0.098 | 0.099 | +0.0004 |
| SOL/USDT:USDT | 15m | IS | short_enabled | -0.229 | -0.229 | +0.0006 |
| SOL/USDT:USDT | 1d | IS | short_enabled | 0.017 | 0.017 | +0.0005 |
| SOL/USDT:USDT | 1h | IS | short_enabled | 0.210 | 0.207 | -0.0031 |
| SOL/USDT:USDT | 4h | IS | short_enabled | 0.081 | 0.083 | +0.0017 |
| BTC/USDT:USDT | 15m | OOS | short_enabled | -0.015 | -0.015 | -0.0002 |
| BTC/USDT:USDT | 1d | OOS | short_enabled | 0.069 | 0.069 | +0.0004 |
| BTC/USDT:USDT | 1h | OOS | short_enabled | 0.208 | 0.210 | +0.0019 |
| BTC/USDT:USDT | 4h | OOS | short_enabled | -0.051 | -0.051 | -0.0002 |
| ETH/USDT:USDT | 15m | OOS | short_enabled | 0.114 | 0.114 | -0.0004 |
| ETH/USDT:USDT | 1d | OOS | short_enabled | -0.049 | -0.049 | +0.0000 |
| ETH/USDT:USDT | 1h | OOS | short_enabled | 0.342 | 0.346 | +0.0042 |
| ETH/USDT:USDT | 4h | OOS | short_enabled | 0.071 | 0.072 | +0.0013 |
| SOL/USDT:USDT | 15m | OOS | short_enabled | -0.202 | -0.199 | +0.0028 |
| SOL/USDT:USDT | 1d | OOS | short_enabled | -0.031 | -0.030 | +0.0011 |
| SOL/USDT:USDT | 1h | OOS | short_enabled | 0.146 | 0.151 | +0.0044 |
| SOL/USDT:USDT | 4h | OOS | short_enabled | 0.090 | 0.092 | +0.0023 |

## 2. TF별 그로스/넷 분해 (펀딩 off 기준 — 순수 수수료·슬리피지 효과)

| 심볼 | TF | 구간 | 엔진 | 그로스 | 넷 | 판정 |
| -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | long_only | 0.9590 | -0.1195 | 비용이 신호를 잠식 |
| BTC/USDT:USDT | 1d | IS | long_only | 0.0199 | 0.0149 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 1h | IS | long_only | 0.0815 | -0.1237 | 비용이 신호를 잠식 |
| BTC/USDT:USDT | 4h | IS | long_only | 0.1669 | 0.1265 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 15m | IS | long_only | 0.4963 | -0.3647 | 비용이 신호를 잠식 |
| ETH/USDT:USDT | 1d | IS | long_only | 0.0558 | 0.0509 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 1h | IS | long_only | 0.2676 | 0.0212 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 4h | IS | long_only | 0.1754 | 0.1298 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 15m | IS | long_only | 1.7385 | 0.0740 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 1d | IS | long_only | -0.0004 | -0.0015 | 그로스도 마이너스 |
| SOL/USDT:USDT | 1h | IS | long_only | 0.4517 | 0.2048 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 4h | IS | long_only | -0.0203 | -0.0437 | 그로스도 마이너스 |
| BTC/USDT:USDT | 15m | OOS | long_only | 0.6523 | -0.1101 | 비용이 신호를 잠식 |
| BTC/USDT:USDT | 1d | OOS | long_only | 0.0898 | 0.0823 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 1h | OOS | long_only | 0.2768 | 0.0950 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 4h | OOS | long_only | -0.0147 | -0.0348 | 그로스도 마이너스 |
| ETH/USDT:USDT | 15m | OOS | long_only | 1.5321 | 0.1473 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 1d | OOS | long_only | -0.0330 | -0.0347 | 그로스도 마이너스 |
| ETH/USDT:USDT | 1h | OOS | long_only | 0.3789 | 0.1455 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 4h | OOS | long_only | 0.0334 | 0.0122 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 15m | OOS | long_only | 1.0340 | -0.1555 | 비용이 신호를 잠식 |
| SOL/USDT:USDT | 1d | OOS | long_only | -0.0069 | -0.0088 | 그로스도 마이너스 |
| SOL/USDT:USDT | 1h | OOS | long_only | 0.3611 | 0.1461 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 4h | OOS | long_only | 0.1848 | 0.1585 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 15m | IS | short_enabled | 0.5878 | -0.6025 | 비용이 신호를 잠식 |
| BTC/USDT:USDT | 1d | IS | short_enabled | -0.0083 | -0.0150 | 그로스도 마이너스 |
| BTC/USDT:USDT | 1h | IS | short_enabled | 0.2645 | -0.1388 | 비용이 신호를 잠식 |
| BTC/USDT:USDT | 4h | IS | short_enabled | 0.1807 | 0.0959 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 15m | IS | short_enabled | 0.5318 | -0.6853 | 비용이 신호를 잠식 |
| ETH/USDT:USDT | 1d | IS | short_enabled | 0.0637 | 0.0558 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 1h | IS | short_enabled | 0.5801 | 0.0744 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 4h | IS | short_enabled | 0.1768 | 0.0983 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 15m | IS | short_enabled | 3.9254 | -0.2293 | 비용이 신호를 잠식 |
| SOL/USDT:USDT | 1d | IS | short_enabled | 0.0199 | 0.0166 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 1h | IS | short_enabled | 0.7845 | 0.2099 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 4h | IS | short_enabled | 0.1438 | 0.0808 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 15m | OOS | short_enabled | 1.4776 | -0.0146 | 비용이 신호를 잠식 |
| BTC/USDT:USDT | 1d | OOS | short_enabled | 0.0766 | 0.0689 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 1h | OOS | short_enabled | 0.5982 | 0.2078 | 비용 이후에도 플러스 |
| BTC/USDT:USDT | 4h | OOS | short_enabled | -0.0090 | -0.0511 | 그로스도 마이너스 |
| ETH/USDT:USDT | 15m | OOS | short_enabled | 3.1755 | 0.1143 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 1d | OOS | short_enabled | -0.0455 | -0.0493 | 그로스도 마이너스 |
| ETH/USDT:USDT | 1h | OOS | short_enabled | 1.0749 | 0.3420 | 비용 이후에도 플러스 |
| ETH/USDT:USDT | 4h | OOS | short_enabled | 0.1236 | 0.0709 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 15m | OOS | short_enabled | 3.4014 | -0.2019 | 비용이 신호를 잠식 |
| SOL/USDT:USDT | 1d | OOS | short_enabled | -0.0280 | -0.0306 | 그로스도 마이너스 |
| SOL/USDT:USDT | 1h | OOS | short_enabled | 0.6590 | 0.1463 | 비용 이후에도 플러스 |
| SOL/USDT:USDT | 4h | OOS | short_enabled | 0.1367 | 0.0901 | 비용 이후에도 플러스 |

## 3. 펀딩 데이터 커버리지

| 심볼 | TF | 구간 | 펀딩 커버리지 |
| -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | IS | 1.000 |
| BTC/USDT:USDT | 1d | IS | 1.000 |
| BTC/USDT:USDT | 1h | IS | 1.000 |
| BTC/USDT:USDT | 4h | IS | 1.000 |
| BTC/USDT:USDT | 15m | OOS | 1.000 |
| BTC/USDT:USDT | 1d | OOS | 1.000 |
| BTC/USDT:USDT | 1h | OOS | 1.000 |
| BTC/USDT:USDT | 4h | OOS | 1.000 |
| ETH/USDT:USDT | 15m | IS | 1.000 |
| ETH/USDT:USDT | 1d | IS | 1.000 |
| ETH/USDT:USDT | 1h | IS | 1.000 |
| ETH/USDT:USDT | 4h | IS | 1.000 |
| ETH/USDT:USDT | 15m | OOS | 1.000 |
| ETH/USDT:USDT | 1d | OOS | 1.000 |
| ETH/USDT:USDT | 1h | OOS | 1.000 |
| ETH/USDT:USDT | 4h | OOS | 1.000 |
| SOL/USDT:USDT | 15m | IS | 1.000 |
| SOL/USDT:USDT | 1d | IS | 1.000 |
| SOL/USDT:USDT | 1h | IS | 1.000 |
| SOL/USDT:USDT | 4h | IS | 1.000 |
| SOL/USDT:USDT | 15m | OOS | 1.000 |
| SOL/USDT:USDT | 1d | OOS | 1.000 |
| SOL/USDT:USDT | 1h | OOS | 1.000 |
| SOL/USDT:USDT | 4h | OOS | 1.000 |

## 4. 숏 펀딩 수취가 WAN-84 결론을 뒤집는가

OOS 숏(`short_enabled`) 평균 total_return: 펀딩off 0.0577 → 펀딩on 0.0592. 부호는 유지된다 — 펀딩 수취만으로 결론이 뒤집히지는 않는다(단, 이 리포트는 A안 방법론이라 WAN-84의 B안 수치와 절대값 비교는 불가하다).

## 5. TF별 채택/제외 권고 (그로스/넷 근거)

| TF | 그로스+ 셀 수 | 비용 잠식(넷-) 셀 수 | 잠식 비율 | 권고 |
| -- | -- | -- | -- | -- |
| 15m | 12 | 9 | 75% | 제외 — 신호는 살아있으나 비용이 과반 셀에서 잠식 |
| 1h | 12 | 2 | 17% | 유지(주의) — 일부 셀에서 비용 민감 |
| 4h | 9 | 0 | 0% | 유지 — 비용 이후에도 대체로 견고 |
| 1d | 6 | 0 | 0% | 유지 — 비용 이후에도 대체로 견고 |

15m은 신호(그로스)가 살아있는 셀 대부분에서 비용이 넷을 마이너스로 뒤집는다 — "손해나니까 뺀다"는 과최적화가 아니라, 그로스/넷 분해로 뒷받침되는 원칙적 제외 근거다(WAN-91 배경 질문에 대한 답). 1h는 셀마다 갈린다(IS는 침식되는 경우가 있고 OOS는 대체로 견고) — 배경에서 지적한 대로 비용 가정에 민감하므로 제외하지 않되 모니터링 대상으로 남긴다. 4h/1d는 비용 이후에도 대체로 견고하다.

## 6. 비용 가정 현실성 검토

바이낸스 USDⓈ-M 무기한선물 일반(레귤러/VIP0) 계정 기준 실제 수수료(2026-07 조회,
`binance.com/en/fee/futureFee` 계열 공개 자료)와 저장소 가정을 대조한다.

| 항목 | 저장소 가정 | 바이낸스 실제(레귤러) | 평가 |
| -- | -- | -- | -- |
| 테이커 수수료 | 0.04%(4bp) | **0.05%(5bp)** | **과소 계상** — 왕복 2bp 저평가(0.08%→0.10%) |
| 메이커 수수료 | None→테이커와 동일(4bp) | **0.02%(2bp)** | `maker_fee_rate` 미지정 시 B안(WAN-41) 비용을 실제의 2배로 과대 계상 — B안 성과가 실제보다 나빠 보일 수 있다 |
| 슬리피지 | 0.05%(5bp) 고정 | (공식 수치 없음, 정성 평가) | BTC/ETH는 대체로 보수적(안전)이지만 **SOL·15m처럼 빈도·변동성이 큰 구간에서는 낙관적일 수 있다** — §2의 그로스↔넷 격차가 15m·SOL에서 가장 큰 것과 일관된 정황 |

**권고(이 이슈에서는 제안만, 기본값 변경은 하지 않음)**:

1. `fee_rate` 기본값을 0.0004→**0.0005**로 올려 테이커 비용 저평가를 바로잡는다.
2. `maker_fee_rate=0.0002`를 명시하는 시나리오를 B안(zone_limit) 평가에 항상 동반한다 — None 방치는 메이커 경로의 실제 비용 이점을 리포트에서 숨긴다.
3. 슬리피지를 TF/심볼 공용 상수 대신 변동성 연동(예: ATR 비례)으로 바꾸는 방안을 별도 이슈로 검토한다 — §2에서 15m·SOL이 비용에 가장 취약한 것이 그 우선순위 근거다.

## 7. 제한사항 및 후속 작업

- 이 리포트는 A안(`entry_mode="close"`) 파이프라인만 재산출했다. §5의 메이커 경로 검토(WAN-91 작업범위 5번, 선택)는 B안(`zone_limit`) 백테스트 전체 재실행이 필요해 이 실행 범위 밖에 남긴다 — 특히 15m처럼 그로스가 살아있는 TF에서 메이커 진입(§6 권고 2, `maker_fee_rate=0.0002`)이 넷을 플러스로 되돌리는지가 다음으로 확인할 질문이다.
- 숏 펀딩 수취가 WAN-84(B안 매칭 널, OOS 숏 −3.82%) 결론을 뒤집는지는 이 리포트의 A안 수치로 직접 답할 수 없다 — 같은 질문을 B안 파이프라인(`backtest.wan84_new_engine_validation` 계열)에 펀딩을 배선해 재실행해야 한다.
- 커버리지는 전 셀 100%였다 — 이 리포트가 다룬 3년 창에서는 펀딩 데이터 결측이 성과에 영향을 주지 않았다.

