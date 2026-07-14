# AlphaBlock — Claude Code 개발 가이드

오더블록(Order Block) 기반 암호화폐 자동매매 프로젝트. **이 저장소에서 Claude Code의 역할은 "개발자"다.** 완료 판단·상태 관리·다음 작업 제안 등 PM 업무는 별도 자동화(Cowork PM 러너)가 담당하므로 여기서 신경 쓰지 않는다.

## 역할 분담 (중요)

- **Claude Code (여기, 로컬)** = 개발. 아래 "개발 워크플로우"만 수행한다.
- **PM 러너 (Cowork, 매시간)** = 완료 판단(In Review→Done), 상태 관리, 다음 Todo 제안, 보고. 코드는 건드리지 않는다.
- 승인은 사용자가 이슈를 **Approved** 상태로 옮기는 것으로 표현된다.

## Linear 연결

이 워크스페이스는 Linear로 이슈를 관리한다. Linear MCP를 연결해 사용:

```
claude mcp add --transport http linear-server https://mcp.linear.app/mcp
```

세션에서 `/mcp`로 인증. 팀은 **Alphablock** (key: `WAN`).

## GitHub 연동

원격: `github.com/yongddini/AlphaBlock` (기본 브랜치 `main`). Linear ↔ GitHub 기본 연동을 사용해 **GitHub 동작이 Linear 상태를 자동 동기화**한다: 브랜치/커밋 → In Progress, PR 열림 → In Review, PR 머지 → Done. 따라서 개발자는 상태를 손으로 바꾸기보다 브랜치·PR·머지 행위로 상태가 흐르게 한다. 이슈 연결은 브랜치명 또는 PR 제목/본문의 이슈 식별자(`WAN-x`)로 이뤄진다.

`main`은 브랜치 보호로 **CI(WAN-10) green + PR** 없이는 머지되지 않는다(설정 스크립트: `scripts/setup-branch-protection.sh`, 적용은 저장소 소유자 1회 실행). 머지 버튼은 사람이 누른다 — 자세한 규칙은 README "브랜치 보호 · 머지 정책" 참고.

## 개발 워크플로우

1. **Approved 이슈만 개발한다.** Linear에서 team=Alphablock, state=`Approved` 이슈를 우선순위 순으로 가져온다. Backlog/Todo/Rejected 는 절대 개발하지 않는다.
2. **착수 = 브랜치 생성.** 이슈의 Linear 제안 브랜치명(이슈 식별자 포함, 예 `yu04038/wan-7-...`)으로 `main`에서 브랜치를 만든다. 브랜치·첫 커밋이 올라가면 Linear GitHub 연동이 이슈를 자동으로 **In Progress**로 옮긴다(안 되면 수동 전환).
3. 이슈의 완료 기준(Acceptance Criteria)을 충족하도록 이 저장소에서 개발한다.
4. 커밋 메시지 앞에 이슈 식별자를 붙인다. 예: `WAN-7: 오더블록 탐지 로직 추가`.
5. **완료 = 브랜치 push + PR 생성.** 브랜치를 `origin`에 push하고 `main`으로 향하는 GitHub **Pull Request**를 연다.
   - PR 제목/본문에 이슈 식별자(`WAN-7`)를 넣어 이슈에 자동 연결한다. PR 본문에는 변경 파일·완료기준 체크리스트·테스트/품질게이트 결과를 적는다.
   - PR이 열리면 Linear 연동이 이슈를 자동으로 **In Review**로 옮긴다(연동이 상태를 관리하므로 직접 Done으로 옮기지 않는다).
   - CI(WAN-10)가 PR에서 자동 실행된다. **CI 초록불이 아니면 리뷰 대상이 아니다** — 실패를 고쳐 다시 push한다.
6. PM 러너가 미흡하다고 판단해 **Approved로 되돌리고** 변경요청 코멘트를 달면, 그 피드백을 반영해 **같은 브랜치/PR에 추가 커밋**하고 다시 3~5를 수행한다.
7. **머지는 사람이 한다.** PM 검증 + CI green 확인 후, 사용자가 PR을 `main`에 머지한다. 머지되면 Linear 연동이 이슈를 자동으로 **Done**으로 옮긴다. **Claude Code는 PR을 자동 머지하지 않는다.**

## 품질 게이트 (커밋/리뷰요청 전 필수 통과)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy .            # strict
uv run pytest
```

처음이라면 `uv sync --dev` 로 의존성 설치, `uv run pre-commit install` 로 훅 설치.

## 안전 규칙

- **실거래 자동 활성화 금지.** `ALPHABLOCK_LIVE_TRADING` 기본값은 `false`. 실주문/자금 이동 코드는 사용자가 명시적으로 승인·수행하기 전까지 활성화하지 않는다.
- API 키·시크릿은 코드에 하드코딩하지 않는다. `.env`(커밋 금지, `.gitignore` 포함)나 환경변수로만 주입하고, 새 설정은 `.env.example`에 예시를 추가한다.
- 테스트가 실패하거나 완료기준을 못 채운 상태로 PR을 열지 않는다(= In Review로 넘기지 않는다).
- **PR을 자동으로 머지하지 않는다.** main 반영(머지)은 CI green + PM 검증 후 사용자가 수행한다.
- `.github/`(CI·릴리즈 워크플로우)는 승인된 이슈 범위 내에서만 수정한다.

## 프로젝트 구조

```
AlphaBlock/
├── data/        # 시세 수집·저장 (OHLCV, 웹소켓) — WAN-6
├── strategy/    # 오더블록 탐지·시그널 생성 — WAN-7
├── execution/   # 주문 실행·포지션·리스크 관리 — WAN-9
├── backtest/    # 백테스팅 엔진 — WAN-8
├── config/      # 설정 로딩 (pydantic-settings)
├── tests/       # 테스트
└── pyproject.toml
```

## 전략 규칙 요약 (WAN-81 메인 엔진)

컨플루언스 전략(`strategy/confluence.py`)의 확정 규칙. **WAN-23/WAN-66 규칙(EMA 60 +
VWMA 100 익절, 롱 온리, 존당 첫 탭 1회)은 WAN-81로 전면 교체됐다** — 아래가 현재
기본값(`ConfluenceParams()`)이다.

- **진입 방식** = **존 근단 지정가**(`entry_mode="zone_limit"`, `zone_limit_ref="proximal"`,
  WAN-95). 존에 지정가를 걸어두고 가격이 닿는 순간 체결한다 — 사용자의 실매매 그대로다.
  체결 판정은 1분봉 서브스텝(`backtest/substep.py`)으로 시뮬레이션하므로 **지정가
  백테스트는 1분봉이 필요하다**. RSI도 체결 순간 봉내 값으로 판정한다
  (`rsi_mode="realtime"`) — 지정가는 봉 중간에 체결되므로 확정봉 RSI를 쓰면 체결
  시점과 판정 시점이 어긋난다. 두 필드는 **한 세트**다.
- **진입가** = 활성(병합) 오더블록 탭 시 **볼린저밴드(SMA 20 ± 2σ)** 로 재산정
  (`deviation_filter` 기본 켜짐). 밴드가 존 위/겹침/아래에 있으면 각각 존 경계 진입/
  밴드가 진입/진입 없음(롱·숏 대칭, `deviation_entry_price` 참고).
- **진입 조건** = 존 확정 후 **첫 탭은 RSI 무관 무조건 진입**(워밍업 NaN이어도),
  **재탭부터 RSI 게이트**(롱 `RSI<=30`, 숏 `RSI>=70`) 적용 — 미충족이어도 존은
  소각되지 않고 다음 탭에서 재평가한다(`rsi_gate_mode="first_tap_free"`,
  `retap_mode="every_tap"`). 병합 존(`combine_obs=True`)은 구성 존이 개별적으로 자신의
  첫 탭을 셀 수 있다(WAN-81 §5, 구 WAN-82 버그 흡수 — 이미 진입한 클러스터에 새로
  편입된 존도 자기 몫의 첫 탭에서 진입한다).
- **숏 비활성화**(`short_enabled=False`, WAN-87 — [WAN-86 결정 1](docs/decisions/wan86.md)에
  따라 WAN-81의 숏 활성화를 사용자가 데이터에 근거해 다시 번복). WAN-84(PR #64) OOS
  롱/숏 분해에서 롱 +3.07%(9셀) vs 숏 −3.82%(7셀)로 숏이 롱 수익을 상쇄함을 확인했다.
  숏 경로 코드는 삭제되지 않았으므로 `short_enabled=True`로 재검증 여지를 남겨둔다.
- **익절** = **고정 1:1.5R**(`take_profit_mode="fixed_r"`, `take_profit_r=1.5`).
  1R = 진입가 → 진입 근거 오더블록 무효화 경계까지 거리. 볼린저로 진입가가
  유리해지면(롱=더 낮게) 1R이 줄어 익절 목표도 그만큼 가까워진다. **EMA·VWMA는
  익절 판정에서 완전히 빠졌다**(`use_line_take_profit=False`) — 옛 선 도달 익절은
  `take_profit_mode="line"`으로 켤 수 있지만 기본이 아니다.
- **손절** = 진입 근거 오더블록의 무효화(breaker). 이 규칙은 변경 없음.
- **재탭 경로도 병합 존 경계를 쓴다**(WAN-81 갭B) — `retap_mode="every_tap"`을 켜도
  `combine_obs`의 병합 존 기준이 유지된다(과거엔 재탭 경로가 병합을 무시하고 원본
  존 단위로 되돌아가는 버그가 있었다).

⚠️ **볼린저 진입가 × 지정가의 채택 규칙(WAN-95)**: 둘 다 켠 조합에서 **볼린저가
이긴다** — 지정가를 존 근단이 아니라 **볼린저가 재산정한 가격에 건다**. 코드 근거:
`backtest/zone_limit_backtest.py`의 `build_zone_limit_candidates`가
`limit_price = params.zone_limit_price(ob)`로 존 근단을 잡은 **직후**
`deviation_filter`가 켜져 있으면 `limit_price = deviation_entry_price(...)`로
덮어쓴다(밴드가 존보다 불리하면 그 셋업은 진입 자체를 건너뛴다 — WAN-75 규칙 3).
즉 "사전 지정가 vs 사후 재산정"의 모순은 없다: 밴드 값은 **탭 봉 시점에 확정**되고
그 가격으로 주문이 걸린 것으로 취급한다. 이 조합은 WAN-95 이전에도 기본값은 아니었지만
**미검증 경로는 아니다** — WAN-70/73/76/84 검정이 모두 `zone_limit + realtime` +
기본 `deviation_filter`로 돌았다.
그 대가로 남는 알려진 한계: 밴드는 탭 봉의 SMA20(= 그 봉 종가 포함)으로 계산되는데
체결은 그 봉 **내부**에서 일어날 수 있다(같은 봉의 종가를 알아야 계산되는 가격에
그 봉 도중 체결). 실무 영향은 SMA20 한 틱 차이 수준이라 작지만, 엄밀히는 룩어헤드다 —
WAN-95는 이 성질을 **바꾸지 않고 기록만 한다**(바꾸면 WAN-70/84 검정의 엔진 정의가
흔들리므로 별도 이슈에서 판단한다).

⚠️ **차트 표시선 ≠ 익절선**: 대시보드는 EMA 20/60/120/240/365를 그리지만
(`ConfluenceParams.display_ema_lengths`) `take_profit_mode="line"`으로 켤 때만 쓰는
익절 판정선은 EMA 60 + VWMA 100 뿐이다(`tp_ema_lengths=(60,)`, `tp_vwma_length=100`,
WAN-66). WAN-23 명세가 두 선 집합을 한 배열로 뒤섞어 적어 EMA 20에서 조기 익절하던
버그(WAN-66)를 낳았으니, 표시선과 익절선을 절대 다시 한 필드로 합치지 말 것.

⚠️ **기존 성과 수치 무효**: WAN-19/22/46/50/58/68/70/71/73/74/75/76 등의 백테스트
결과는 모두 WAN-81 이전 구 엔진 기준이다. WAN-81/WAN-84 리포트(숏 활성화 전제)도
WAN-87 이후에는 **채택 기본값과 다른 설정**이므로 현재 기본값 성과로는 무효다 —
숏 활성화 자체의 검증 기록으로만 유효하다.

**WAN-95가 여기에 한 겹을 더 얹는다**: `wan87_long_only_summary.md`와 WAN-91 펀딩
리포트도 **더 이상 채택 기본값 성과가 아니다** — 둘 다 `entry_mode="close"`(탭 봉 종가
시장가)로 산출됐는데, 사용자의 실매매이자 새 기본값은 **존 지정가**다. 즉 그 표들은
"사용자가 하지 않는 매매"의 손익이다. 두 리포트 모듈은 재현을 위해 당시 엔진을
명시적으로 고정해 뒀다(`SHORT_DISABLED_PARAMS`/`ENGINE_PRESETS`에
`entry_mode="close"` 고정). 채택 기본값(지정가 + 메이커 + 롱 온리 + 펀딩) 재산출과
종가 대비 대조표·체결률은 `backtest/reports/wan95_zone_limit_summary.md`(재현:
`python -m backtest.wan95_zone_limit_report`) 참고.

⚠️ **펀딩비 배선(WAN-91)**: `default_backtest_config`(`backtest/sweep.py`)가
`settings.backtest_funding_enabled`(기본 `True`)을 `BacktestConfig.funding_enabled`에
싣는다(`risk_sizing`과 동일 패턴). 이 플래그만으로는 손익이 바뀌지 않는다 — 호출부가
`data.FundingRateStore.get_rates(symbol, ...)`로 조회한 `funding_rates`를
`evaluate()`/`run_backtest()`에 **별도로** 넘겨야 실제 펀딩비가 반영된다. 위
WAN-19~87 리포트들은 전부 이 배선 이전(또는 배선 이후에도 `funding_rates`를 넘기지
않은 채로) 산출됐으므로 펀딩비 미반영 상태다 — 크립토 무기한선물은 보통 롱이 펀딩을
지불하므로 롱 온리 채택 기본값(WAN-87) 수익률은 실제보다 소폭 부풀려져 있을 수
있다. `backtest/reports/wan91_funding_cost_summary.md`(재현: `python -m
backtest.wan91_funding_cost_report`)의 펀딩 on/off 델타 재산출로는 그 영향이
대체로 total_return 기준 ±0.1~2%p 수준으로 작아, 위 문단의 무효 판정 자체를
뒤집지는 않는다 — 단, 15m은 이 리포트의 그로스/넷 분해로 "비용(수수료·슬리피지)이
신호를 잠식"함이 원칙적으로 확인돼 채택 대상에서 제외를 권고한다(과최적화가
아니라 비용 근거). 1h는 비용 가정에 민감하니 모니터링 대상으로 남긴다.
**단, 그 15m 제외 권고의 근거였던 비용은 종가 진입(테이커) 기준이라 WAN-95가
뒤집었다** — 지정가 채택으로 왕복 비용이 0.18%→0.11%로 줄자, 15m은 3심볼 전부
마이너스(평균 −18.9%, MDD 38.9%)에서 **3심볼 전부 플러스**(평균 +15.8%, MDD 14.5%)로
반전됐다(`wan95_zone_limit_summary.md`). "15m의 문제는 신호가 아니라 비용"이라는
WAN-91의 진단은 옳았고, 비용을 실매매(메이커)에 맞추자 결론이 반대가 된 것이다.
그렇다고 **15m 채택이 확정된 것은 아니다** — 서브스텝 시뮬레이터는 "지정가에 닿으면
체결"로 보므로(큐 우선순위 미모델링) 체결률(15m 약 28%)이 낙관적이고, 승률은 오히려
55%→49%로 떨어졌다. 15m 판단은 체결 가정을 보수화한 재검정 후에 내린다.
**→ WAN-96이 그 재검정을 수행했고, 결론은 15m 제외다**(아래).

⚠️ **체결 가정 보수화 결과 — 15m 제외 권고(WAN-96)**: `backtest/reports/
wan96_fill_conservatism_summary.md`(재현: `python -m backtest.wan96_fill_conservatism_report`).
WAN-95의 15m 반전은 **"닿으면 체결"이라는 낙관 가정에 의존**한다. 체결 규칙을 보수화하면
15m 평균 수익률이 +15.8%(3/3 플러스) → **−5.8%(0/3 플러스)**(관통 5bp + 50% 탈락)로
무너진다. 1h도 같은 최악 가정에서 +9.1% → −1.7%(0/3)다.

핵심 근거는 거래 수와 수익의 비대칭이다: **`pen_5bp`는 거래를 4.7%만 줄이는데
(461→439) 수익은 15.8%→1.3%로 사라진다**. 즉 수익이 **지정가를 스치듯 닿고 되돌아간
체결**(관통 없는 체결)에 몰려 있는데, 그 체결이야말로 실거래에서 큐 우선순위 때문에
**가장 안 될 가능성이 높은** 체결이다. 반면 체결 편향 진단은 체결군이 미체결군보다
**나쁘다**고 나온다(15m −0.18R) — 모순이 아니다. 그 진단은 공통 가상 진입가(탭 봉
종가)로 셋업의 방향성만 재므로 지정가의 가격 이점을 뺀 값이다. 둘을 합치면: 지정가
진입의 수익은 **셋업이 좋아서가 아니라 더 좋은 가격에 들어가서** 나오고, 그 가격은
체결 가정을 조금만 현실화하면 사라진다.

보수화 옵션은 **파라미터로만 존재하고 기본값은 바꾸지 않았다**
(`fill_penetration_bps=0.0`, `fill_dropout_rate=0.0`, `fill_dropout_seed=0`) — WAN-95
결과가 그대로 재현돼야 비교가 성립하기 때문이다. 탈락률 0이면 난수를 뽑지 않으므로
기본 실행은 WAN-95와 비트 단위로 동일하다. 이슈의 (b) 큐 모델(거래량 대비 체결 확률·
부분 체결)은 1분봉 거래량으로 큐 위치를 복원할 수 없어 **구현하지 않았다** — 틱·호가
데이터가 필요하다.

⚠️ **`entry_mode`는 라벨이 아니라 경로 스위치다(WAN-95)**: `close`는 A안
(`backtest.sweep.evaluate` → `BacktestEngine`), `zone_limit`은 B안
(`backtest.zone_limit_backtest.run_zone_limit_backtest`)에서만 유효하고, 두 진입점이
불일치를 `ValueError`로 거부한다. 예전엔 이 필드가 리포트에 찍히는 **라벨일 뿐**이라
`zone_limit`으로 두고 A안 엔진을 돌려도 아무 소리 없이 "종가 진입 결과에 zone_limit
라벨"이 붙었다. A안 전용 도구(스윕·워크포워드·대시보드·CLI 리포트)는
`backtest.sweep.CLOSE_ENTRY_DEFAULTS`로 **자기가 A안임을 선언**한다. 같은 이유로
리포트 헬퍼(`backtest/report.py`)는 파라미터를 못 받으면 진입 방식을 기본값으로
지어내지 않고 `"unknown"`으로 적는다.

⚠️ **비용 가정(WAN-95 기준)**: 테이커 4bp(`BacktestConfig.fee_rate`) / **메이커 2bp**
(`maker_fee_rate`, WAN-95에서 `None`→`0.0002`로 배선 — 예전엔 지정가 진입에도 테이커가
붙었다) / 슬리피지 5bp(테이커 체결에만). **지정가 진입=메이커(슬리피지 0), 청산=항상
테이커**(손절·익절 도달은 시장가 성격). 테이커 4bp→5bp 등 비용 가정 현실화는 WAN-92 범위.

⚠️ **라이브/페이퍼는 아직 지정가를 집행하지 못한다(WAN-45)**: `ConfluenceStrategy`는
`entry_mode`를 읽지 않는 **A안 시그널 생성기**라, 실시간 러너(`live.runner`)는 채택
기본값이 지정가로 바뀐 뒤에도 여전히 종가 기준 시그널·알림을 낸다. 대시보드도 상위TF만
로드하므로 종가 진입(A안)으로 명시 고정돼 있다(화면 배지가 "A안(봉 마감 종가)"로 표시).
즉 **백테스트(채택 규칙) ↔ 라이브(집행 가능 규칙)가 갈라져 있다** — WAN-45(러너 ↔
LimitOrderBook 배선)가 이 간극을 닫는다.

## 기술 스택

Python 3.11+ · uv · ccxt · pandas · asyncio · pydantic-settings
