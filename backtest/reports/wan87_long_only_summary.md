# WAN-87 롱 온리 기본값 재산출 — 숏 활성화(WAN-81/84) 대비 비교

WAN-86 결정 1로 `ConfluenceParams.short_enabled` 기본값이 `True`(WAN-81)에서 `False`(WAN-87, 롱 온리)로 되돌아갔다. 아래 `long_only`가 현재 기본값, `short_enabled`가 WAN-81/WAN-84 검증 당시의 이전 기본값이다. 두 프리셋을 같은 실행에서 돌려 데이터 창(최근 3년)을 맞췄다.

## 심볼 × TF × 프리셋별 성과

| symbol | timeframe | engine | num_trades | long_trades | short_trades | win_rate | total_return | max_drawdown |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | long_only | 740 | 740 | 0 | 0.5446 | -0.2296 | 0.4293 |
| BTC/USDT:USDT | 15m | short_enabled | 1278 | 684 | 594 | 0.5321 | -0.6174 | 0.6843 |
| BTC/USDT:USDT | 1h | long_only | 233 | 233 | 0 | 0.5536 | -0.0287 | 0.1827 |
| BTC/USDT:USDT | 1h | short_enabled | 405 | 203 | 202 | 0.5753 | 0.0690 | 0.2261 |
| BTC/USDT:USDT | 4h | long_only | 62 | 62 | 0 | 0.5484 | 0.0916 | 0.0987 |
| BTC/USDT:USDT | 4h | short_enabled | 115 | 61 | 54 | 0.5217 | 0.0448 | 0.1535 |
| BTC/USDT:USDT | 1d | long_only | 11 | 11 | 0 | 0.8182 | 0.0972 | 0.0719 |
| BTC/USDT:USDT | 1d | short_enabled | 17 | 11 | 6 | 0.5882 | 0.0539 | 0.0907 |
| ETH/USDT:USDT | 15m | long_only | 874 | 874 | 0 | 0.5584 | -0.2174 | 0.4510 |
| ETH/USDT:USDT | 15m | short_enabled | 1517 | 802 | 715 | 0.5399 | -0.5660 | 0.7136 |
| ETH/USDT:USDT | 1h | long_only | 260 | 260 | 0 | 0.5654 | 0.1668 | 0.1681 |
| ETH/USDT:USDT | 1h | short_enabled | 465 | 242 | 223 | 0.5742 | 0.4164 | 0.1654 |
| ETH/USDT:USDT | 4h | long_only | 63 | 63 | 0 | 0.6508 | 0.1420 | 0.0685 |
| ETH/USDT:USDT | 4h | short_enabled | 116 | 61 | 55 | 0.5862 | 0.1692 | 0.0781 |
| ETH/USDT:USDT | 1d | long_only | 13 | 13 | 0 | 0.5385 | 0.0170 | 0.0839 |
| ETH/USDT:USDT | 1d | short_enabled | 22 | 13 | 9 | 0.5000 | 0.0073 | 0.1109 |
| SOL/USDT:USDT | 15m | long_only | 939 | 939 | 0 | 0.5527 | -0.0815 | 0.2909 |
| SOL/USDT:USDT | 15m | short_enabled | 1775 | 892 | 883 | 0.5358 | -0.4294 | 0.5750 |
| SOL/USDT:USDT | 1h | long_only | 253 | 253 | 0 | 0.5968 | 0.3509 | 0.2153 |
| SOL/USDT:USDT | 1h | short_enabled | 490 | 241 | 249 | 0.5694 | 0.3562 | 0.2153 |
| SOL/USDT:USDT | 4h | long_only | 65 | 65 | 0 | 0.5385 | 0.1148 | 0.1434 |
| SOL/USDT:USDT | 4h | short_enabled | 120 | 62 | 58 | 0.5417 | 0.1709 | 0.1403 |
| SOL/USDT:USDT | 1d | long_only | 8 | 8 | 0 | 0.3750 | -0.0103 | 0.0386 |
| SOL/USDT:USDT | 1d | short_enabled | 18 | 8 | 10 | 0.4444 | -0.0140 | 0.0533 |

## 롱 온리 전환에 따른 total_return 변화(long_only − short_enabled)

| symbol | timeframe | long_only | short_enabled | delta |
| -- | -- | -- | -- | -- |
| BTC/USDT:USDT | 15m | -0.2296 | -0.6174 | +0.3878 |
| BTC/USDT:USDT | 1d | 0.0972 | 0.0539 | +0.0432 |
| BTC/USDT:USDT | 1h | -0.0287 | 0.0690 | -0.0976 |
| BTC/USDT:USDT | 4h | 0.0916 | 0.0448 | +0.0469 |
| ETH/USDT:USDT | 15m | -0.2174 | -0.5660 | +0.3486 |
| ETH/USDT:USDT | 1d | 0.0170 | 0.0073 | +0.0097 |
| ETH/USDT:USDT | 1h | 0.1668 | 0.4164 | -0.2496 |
| ETH/USDT:USDT | 4h | 0.1420 | 0.1692 | -0.0273 |
| SOL/USDT:USDT | 15m | -0.0815 | -0.4294 | +0.3478 |
| SOL/USDT:USDT | 1d | -0.0103 | -0.0140 | +0.0037 |
| SOL/USDT:USDT | 1h | 0.3509 | 0.3562 | -0.0053 |
| SOL/USDT:USDT | 4h | 0.1148 | 0.1709 | -0.0561 |

- 평균 total_return: 롱 온리 0.0344 vs 숏 활성화 -0.0283
- 롱 온리가 우월한 셀: 7/12

## 관련 기록

- 결정 근거: [`docs/decisions/wan86.md`](../../docs/decisions/wan86.md)(WAN-86 결정 1 — WAN-84 OOS 롱/숏 분해에서 롱 +3.07% vs 숏 −3.82%).
- 숏 활성화 시절 원본 검증: `backtest/reports/wan81_summary.md`, `backtest/reports/wan84_summary.md`(둘 다 이제는 롱 온리 기본값과 다른 설정 기준이므로 현재 기본값 성과로는 무효, 숏 활성화 자체의 검증 기록으로만 유효).
- 이 리포트 이후 채택 기본값(`ConfluenceParams()`) 성과는 위 `long_only` 행 기준으로 삼는다.
