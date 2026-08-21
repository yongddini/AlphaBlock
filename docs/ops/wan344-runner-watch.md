# 러너 정지 감시 등록·확인 런북 (WAN-344)

**한 줄**: 러너가 죽은 걸 아는 장치는 `alphablock watch` 하나뿐인데 서버 어디에도 등록돼
있지 않았다. 이제 systemd 타이머(`alphablock-watch.timer`, 기본 10분)로 돈다 — **등록하고,
경보가 실제로 도착하는지 한 번 보라.**

## 왜 이게 조용한 위험인가

수집기(`alphablock-collector`)와 러너(`alphablock-live`)는 **별개 프로세스**다. 러너만 죽으면:

* 봉은 계속 쌓인다 → `data/repair.py` 의 `stale_series` 백업 경보도 **안 울린다**
* 대시보드도 정상으로 보인다
* doctor(1d·1h)는 DB 무결성·인구조사를 볼 뿐 **러너 생존을 안 본다**
* 결과: **러너만 조용히 죽어 있다**

서버 실측(2026-08-20) — `crontab -l` 에는 야간 타임라인 캐시(WAN-297) 한 줄뿐,
`systemctl list-timers` 에는 doctor 두 쌍뿐이었다. 그 기간 러너 정지 공백이 **11분·34분·41분**
있었고 아무 경보도 가지 않았다.

⚠️ 그 정지들 자체는 **크래시가 아니었다**(WAN-344 조사: 앱 로그·journald·커널 로그 세 축 전부
크래시 증거 0, 정지 로그는 `Deactivated successfully` = 정상 SIGTERM, 그 기간 배포 다수).
문제는 정지의 원인이 아니라 **정지를 아무도 몰랐다는 것**이다.

## 1) 등록 (서버에서)

```bash
cd ~/AlphaBlock && git pull
./scripts/install-systemd.sh watch        # 유닛 두 개 설치 + 타이머 enable --now
```

유닛 템플릿이 바뀌면 `deploy.sh` 로는 반영되지 않는다 — 위 스크립트를 다시 돌려야 한다
(WAN-318 §7, `docs/ops/server-migration.md` §4b).

간격을 바꾸려면: `ALPHABLOCK_WATCH_INTERVAL=5min ./scripts/install-systemd.sh watch`.
기본 10분은 설정 기본값 `health_watch_interval_seconds`(600초)와 같은 값이다.
🚨 **크게 잡으면 그만큼 늦게 안다** — 실측 공백이 11·34·41분이었다.

## 2) 🚨 경보가 실제로 도착하는지 1회 확인 (건너뛰지 말 것)

```bash
uv run -- alphablock watch --test-message; echo "종료 코드: $?"
```

| 종료 코드 | 뜻 | 할 일 |
| --: | -- | -- |
| `0` | 폰에 도착 | 끝 |
| `1` | 보낼 곳은 있는데 전송 실패 | 토큰·chat_id·네트워크 확인(`logs/watch.log`) |
| `2` | `ALPHABLOCK_TELEGRAM_*` 미설정 | `.env` 설정 후 재확인 |

**등록만 하고 도착을 안 보면 같은 자리다.** WAN-321 이 고친 것이 정확히 「경보가 안 가는데
그 사실도 경보되지 않는」 상황이었다(밑줄이 든 문자열을 레거시 Markdown 이 이탤릭으로 읽어
텔레그램이 400 으로 거부 → 진짜 이상이 나도 폰에 아무것도 안 감).

## 3) 도는지 확인

```bash
systemctl list-timers alphablock-watch.timer --no-pager   # 다음 실행·마지막 실행
systemctl status alphablock-watch.service --no-pager      # 마지막 1회 결과
tail -20 logs/watch.log                                    # 판정 출력(KST)
systemctl --failed                                         # 워치가 실패로 남았는가
```

📌 유닛은 `alphablock watch --once --require-delivery` 를 돌린다. `--require-delivery` 가
**핵심**이다 — 텔레그램이 미설정이거나 전송이 실패하면 종료 코드 2/1 을 내 systemd 가
`failed` 로 기록한다. 이게 없으면 「감시는 도는데 아무 데도 안 가는」 상태가 **성공으로**
보인다. 그래서 이 유닛에는 doctor 와 같은 이유로 `SuccessExitStatus` 를 넣지 않는다.

## 왜 상주 서비스가 아니라 타이머인가

* 쿨다운·복구 상태가 `data/health_watch_state.json` 으로 **영속화**돼 있어 매번 새 프로세스로
  돌아도 중복 경고가 나지 않는다(회귀 테스트가 동작으로 고정).
* 상주 프로세스는 **죽으면 그 사실을 알 장치가 또 필요하다**(감시의 감시). 타이머는 프로세스가
  죽어도 다음 주기에 그냥 다시 뜬다 — doctor 와 같은 패턴이다.

⚠️ 쿨다운 상태 파일을 지우면 발효 중인 경고가 한 번 더 온다(손상은 아니다).

## 부수 — journald 보존이 짧다

서버 실측에서 `journalctl -u alphablock-live` 의 가장 오래된 기록이 **3시간 전**이었다.
사고가 나도 몇 시간 뒤면 원인을 못 캔다 — 이번 조사도 그 때문에 막혔고, 유닛이
`StandardOutput=append:` 로 파일 로그를 남긴 덕분에만 과거를 봤다(워치 유닛도 같은 규약으로
`logs/watch.log` 에 남긴다).

조치 후보(이 이슈 범위 밖 · 별도 이슈): `/etc/systemd/journald.conf` 의 `Storage=persistent`
· `SystemMaxUse=` 확인, 또는 rsyslog 이중화.

```bash
grep -E '^\s*(Storage|SystemMaxUse|MaxRetentionSec)' /etc/systemd/journald.conf
journalctl --disk-usage
```

## 범위 밖

* ❌ 유닛의 `Restart`/`StartLimit*` 변경 — 원인이 크래시가 아님이 확인됐으므로 건드리지 않았다
  (모르는 채 늘리면 증상만 가린다).
* ❌ 전략·엔진·기본값·토대 무관. `ALPHABLOCK_LIVE_TRADING=false` 유지 · DB 에 아무것도 쓰지
  않는다(WAN-194).
