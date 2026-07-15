# WAN-83: 포지션 충돌로 인한 첫 탭 RSI 면제권 소각 계측

WAN-112 이전 채택 기본값(롱 온리·지정가·`first_tap_free`·고정 1.5R·**오프셋 0bp**)에서, `tap_index=0`(무조건 진입)인데 포지션 보유로 스킵된 첫 탭이 존 무효화 전에 재탭됐는지, 재탭이 RSI 게이트를 통과했는지 분해한다. **이 이슈는 계측만 하며 진입 로직은 바꾸지 않는다.**

## series 범위 (현행 백테스트 — (심볼,TF) 시리즈 내부에서만 포지션 공유)

| symbol | timeframe | segment | tap0_filled | dropped_by_position | dropped_with_retap | dropped_retap_recovered | dropped_retap_blocked | dropped_no_retap |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | is | 603 | 34 | 9 | 4 | 5 | 25 |
| BTC/USDT:USDT | 1h | is | 129 | 6 | 1 | 0 | 1 | 5 |
| BTC/USDT:USDT | 4h | is | 30 | 0 | 0 | 0 | 0 | 0 |
| BTC/USDT:USDT | 1d | is | 1 | 0 | 0 | 0 | 0 | 0 |
| ETH/USDT:USDT | 15m | is | 615 | 28 | 9 | 2 | 7 | 19 |
| ETH/USDT:USDT | 1h | is | 137 | 7 | 3 | 1 | 2 | 4 |
| ETH/USDT:USDT | 4h | is | 26 | 0 | 0 | 0 | 0 | 0 |
| ETH/USDT:USDT | 1d | is | 5 | 0 | 0 | 0 | 0 | 0 |
| SOL/USDT:USDT | 15m | is | 613 | 13 | 6 | 2 | 4 | 7 |
| SOL/USDT:USDT | 1h | is | 147 | 10 | 4 | 2 | 2 | 6 |
| SOL/USDT:USDT | 4h | is | 33 | 0 | 0 | 0 | 0 | 0 |
| SOL/USDT:USDT | 1d | is | 5 | 0 | 0 | 0 | 0 | 0 |
| BTC/USDT:USDT | 15m | oos | 305 | 15 | 7 | 3 | 4 | 8 |
| BTC/USDT:USDT | 1h | oos | 70 | 3 | 1 | 0 | 1 | 2 |
| BTC/USDT:USDT | 4h | oos | 13 | 1 | 0 | 0 | 0 | 1 |
| BTC/USDT:USDT | 1d | oos | 3 | 0 | 0 | 0 | 0 | 0 |
| ETH/USDT:USDT | 15m | oos | 317 | 27 | 11 | 5 | 6 | 16 |
| ETH/USDT:USDT | 1h | oos | 69 | 4 | 1 | 1 | 0 | 3 |
| ETH/USDT:USDT | 4h | oos | 14 | 0 | 0 | 0 | 0 | 0 |
| ETH/USDT:USDT | 1d | oos | 4 | 0 | 0 | 0 | 0 | 0 |
| SOL/USDT:USDT | 15m | oos | 302 | 10 | 4 | 2 | 2 | 6 |
| SOL/USDT:USDT | 1h | oos | 56 | 4 | 0 | 0 | 0 | 4 |
| SOL/USDT:USDT | 4h | oos | 14 | 1 | 1 | 1 | 0 | 0 |
| SOL/USDT:USDT | 1d | oos | 0 | 0 | 0 | 0 | 0 | 0 |

series 합계: tap0_filled=3511, dropped_by_position=163, dropped_with_retap=57, dropped_retap_recovered=23, **dropped_retap_blocked=34**, dropped_no_retap=106

## global 범위 (라이브 조건 — 3심볼×4TF 전체가 포지션 1개를 공유)

| segment | tap0_filled | dropped_by_position | dropped_with_retap | dropped_retap_recovered | dropped_retap_blocked | dropped_no_retap |
| -- | -- | -- | -- | -- | -- | -- |
| is | 2344 | 1343 | 607 | 225 | 382 | 736 |
| oos | 1167 | 625 | 303 | 131 | 172 | 322 |

global 합계: tap0_filled=3511, dropped_by_position=1968, dropped_with_retap=910, dropped_retap_recovered=356, **dropped_retap_blocked=554**, dropped_no_retap=1058

