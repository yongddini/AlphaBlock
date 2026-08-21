# 페이퍼 수집기·러너 리눅스 서버 이전 런북 (WAN-174)

> 🔄 **2026-07-24 정정 — 아래 「ASTx가 막는다」 전제는 폐기됐다. 차단은 없었다.**
> 원인은 수집기가 **옛 엔드포인트 경로**를 쓴 것이고(`/market` 접두사 누락), 그 경로는
> 핸드셰이크만 성공시키고 데이터를 한 건도 안 보낸다. 고친 뒤 **로컬 맥에서 실시간 수신이
> 확인됐다**(ASTx 상주 그대로). 근거·실측: [`docs/decisions/wan174.md`](../decisions/wan174.md).
> **따라서 「데이터를 받으려면 서버로 가야 한다」는 이 문서의 동기는 더 이상 성립하지 않는다.**
> 서버 이전을 계속할 이유는 **상시 가동**(맥은 잠자고 재부팅된다) 하나이며, 그 판단은
> 사용자 몫이다. 아래 절차 자체는 상시 가동을 택할 때 **그대로 유효**하다(집 미니PC 포함).

로컬 맥은 ASTx(AhnLab Safe Transaction)가 바이낸스 **선물 웹소켓**(`wss://fstream.binance.com`)을
막아 실시간 수집이 불가하다(REST `fapi`는 정상 — WAN-174 진단). 리눅스 서버에는 ASTx가
없으므로 **수집기 + 페이퍼 러너 + 대시보드**를 서버로 옮겨 이 문제를 회피한다.

- **서버(사용자 확정)**: 오라클 클라우드 무료 티어 · **춘천 리전** ·
  `VM.Standard.E2.1.Micro`(1 OCPU · 1GB RAM · x86). 지금 단계는 **페이퍼/테스트 전용**이고,
  실매매 전환 시 더 좋은 유료 서버로 재이전한다(사용자 결정 2026-07-23).
- **안전**: 페이퍼 한정. `ALPHABLOCK_LIVE_TRADING=false` 불변 · 실주문 없음 · 바이낸스 API 키
  불필요(시세는 공개 데이터, 페이퍼는 `PaperBroker`).
- **1GB 박스 제약(PM 실측)**: 서버는 **실시간 수집 + 페이퍼 러너 + 대시보드 전용**이다.
  몇 년치 1분봉을 pandas로 올리는 **백테스트 격자는 서버에서 돌리지 않는다**(OOM) — 맥에서
  돌린다. 스왑 2GB는 필수(셋업 스크립트가 만든다).

## 0. 사전 조건

- 오라클 VM 프로비저닝 완료(Ubuntu 22.04+ 또는 동급, systemd 필수) + SSH 키 접속 가능.
- 인바운드 포트는 **SSH(22)만** 열면 된다. 텔레그램 알림은 아웃바운드 HTTPS라 개방 불필요,
  대시보드는 SSH 터널로만 접속한다(공개 노출 금지).

이하 `<서버>`는 `ubuntu@<공인IP>` 형태의 SSH 대상이라고 표기한다.

## 1. 서버 1회 셋업

```bash
ssh <서버>
git clone https://github.com/yongddini/AlphaBlock.git && cd AlphaBlock
./scripts/setup-server.sh   # 스왑 2GB + uv + uv sync (멱등)
```

## 2. .env 배치 (로컬 맥에서)

`.env`는 커밋 금지 파일이라 손으로 옮긴다(텔레그램 토큰 등):

```bash
scp .env <서버>:~/AlphaBlock/.env
```

새 설정을 추가할 일이 있으면 `.env.example`에 예시를 함께 올린다(저장소 규칙).

## 3. DB 이전 (로컬 맥에서)

3년치 재백필보다 복구된 `data/ohlcv.db`를 복사하는 쪽이 훨씬 빠르다.

⚠️ **복사 전 반드시 로컬 수집기를 정지**한다 — SQLite WAL 저널이 열린 채 복사하면
깨진 사본이 나온다(`data/ohlcv.db.corrupt.bak`이 그 흉터다).

```bash
# ① 로컬 수집기·러너 정지 (launchd 데몬을 쓰고 있었다면)
./scripts/uninstall-daemons.sh collector
./scripts/uninstall-daemons.sh live

# ② WAL 체크포인트로 -wal/-shm 을 본 파일에 흡수
sqlite3 data/ohlcv.db "PRAGMA wal_checkpoint(TRUNCATE);"

# ③ 복사 (수 GB — 수 분 걸린다)
scp data/ohlcv.db <서버>:~/AlphaBlock/data/ohlcv.db
```

무결성 확인(서버에서): `sqlite3 ~/AlphaBlock/data/ohlcv.db "PRAGMA integrity_check;"` → `ok`.

## 4. 상시 구동 등록 (서버에서)

```bash
cd ~/AlphaBlock
./scripts/install-systemd.sh            # 수집기 + 러너 + 대시보드 + DB 점검 타이머 두 쌍 + 상태 워치
# 또는 개별: ./scripts/install-systemd.sh collector|live|dashboard|doctor|watch
```

systemd 시스템 서비스로 등록되어 **부팅 시 자동 시작 + 크래시 시 10초 후 자동 재시작**된다
(launchd 판 WAN-31/48과 대칭). 로그는 `~/AlphaBlock/logs/{collector,live,dashboard}.log`.

`doctor` 는 서비스가 아니라 **타이머 두 쌍**으로 등록된다(WAN-185 · 분리 = WAN-318 §2).
이상이면 종료 코드 1로 systemd 에 실패를 남기고(`systemctl --failed`) 텔레그램 경고를 보낸다
(`ALPHABLOCK_TELEGRAM_*` 설정 시). 07-22 손상처럼 "봉은 오는데 DB가 조용히 깨지는" 상태를
사람이 화면을 안 봐도 잡는다.

| 타이머 | 도는 것 | 기본 주기 | 환경변수 |
| -- | -- | -- | -- |
| `alphablock-doctor.timer` | 전수 — `PRAGMA quick_check` 포함 | **1d** | `ALPHABLOCK_DOCTOR_INTERVAL` |
| `alphablock-doctor-light.timer` | 싼 점검만 — `--skip-quick-check` | **1h** | `ALPHABLOCK_DOCTOR_LIGHT_INTERVAL` |

🚨 **주기를 실행 시간보다 짧게 잡지 말 것.** 옛 기본값은 15min 이었는데 서버 실측(2026-08-17)
전수 1회가 **18분+**(4.0GB · CPU 37초 · 나머지는 전부 디스크 I/O 대기 · 실효 3.7MB/s)라
**끝나기 전에 다음 차례가 와 DB 풀스캔이 상시 걸려 있었다.** 수집기·러너가 그 스캔과 디스크를
두고 싸웠고, 하필 WAN-195 가 규명한 07-22 손상의 최유력 벡터가 **동시 접근**이다 — 무결성을
지키려는 점검이 압력을 상시로 만들고 있었던 셈이다. 두 유닛 모두 `Nice=19` ·
`IOSchedulingClass=idle` 로 수집·러너에 디스크를 양보한다.

⚠️ **점검 항목을 줄인 게 아니다** — 같은 `alphablock doctor` 를 두 주기로 나눠 돌릴 뿐이다
(무엇을 검사하는가는 WAN-194 소관). 나눈 이유: 전 페이지를 읽는 것은 `quick_check` 하나이고,
「장부만 비었는데 러너는 계속 쓰는」(WAN-194 원형) 상태는 싼 점검으로 잡히므로 하루를 기다릴
이유가 없다. 단 싼 판도 공짜는 아니다 — 인구조사가 6,500만 행 테이블을 세느라 로컬 7.3GB
실측 **90초**였다(전수는 174초). 그래서 1h 이지 15min 이 아니다.

### 4-1. 상태 워치 — 러너가 죽은 걸 아는 유일한 장치 (WAN-32, 등록 = WAN-344)

`watch` 도 타이머 한 쌍(`alphablock-watch.timer` + `.service`)으로 등록되고 기본 **10분**
마다 `alphablock watch --once --require-delivery` 를 돌린다(`ALPHABLOCK_WATCH_INTERVAL`).

🚨 **doctor 로는 러너 정지를 못 잡는다.** 수집기와 러너는 **별개 프로세스**라 러너만 죽으면
봉은 계속 신선하고(→ `stale_series` 백업 경보도 안 울림) 대시보드도 정상으로 보인다. doctor 는
DB 무결성·인구조사를 보지 **러너 생존을 안 본다**. 실제로 2026-08-20 서버 실측에서 워치가
크론에도 타이머에도 **아무 데도 등록돼 있지 않았고**, 그 기간 11분·34분·41분 정지 동안 아무
경보도 가지 않았다(WAN-344).

🚨 **등록만 하고 끝내지 말 것 — 경보가 실제로 도착하는지 1회 보라:**

```bash
uv run -- alphablock watch --test-message   # 폰에 오면 성공, 안 오면 종료 코드 1(전송 실패)·2(미설정)
systemctl list-timers alphablock-watch.timer --no-pager
tail -20 logs/watch.log
```

`--require-delivery` 덕분에 텔레그램이 미설정이거나 전송이 실패하면 유닛이 `failed` 로 남는다
(`systemctl --failed`). 이게 없으면 「감시는 도는데 경보는 아무 데도 안 가는」 상태가 성공으로
보인다 — WAN-321 이 고친 실패 부류와 같은 자리다.

⚠️ 쿨다운(기본 1시간)·복구 알림 상태는 `data/health_watch_state.json` 에 저장되므로 타이머가
매번 새 프로세스로 돌아도 중복 경고가 나지 않는다. 그 파일을 지우면 발효 중인 경고가 한 번 더 온다.

간격 조정: `ALPHABLOCK_DOCTOR_INTERVAL=12h ./scripts/install-systemd.sh doctor` ·
`ALPHABLOCK_WATCH_INTERVAL=5min ./scripts/install-systemd.sh watch`.
설치 후 **서버에서 1회 실측을 확인**하고(아래) 주기가 그보다 충분히 긴지 본다:

```bash
systemctl list-timers 'alphablock-*' --no-pager           # doctor 두 쌍 + 워치의 다음/마지막 실행
systemctl show alphablock-doctor -p ExecMainStartTimestamp -p ExecMainExitTimestamp   # 1회 소요
tail -20 logs/doctor.log                                  # 판정 출력(전수·싼 점검 공용)
```

## 4a. 재배포 — 코드 갱신 시 (서버에서, WAN-185)

설치 후 새 커밋을 서버에 반영할 때는 `deploy.sh` 한 줄로 한다. 서버가 main 을 깔끔히
추적하지 못하거나 프로세스가 오래 떠 있으면 **디스크 소스는 새것인데 돌고 있는 프로세스나
`__pycache__` 바이트코드가 옛 모듈을 붙들어** "코드는 고쳤는데 화면은 옛것"(ImportError·옛
화면)이 재발한다(PM 운영 메모 2026-07-25, WAN-190 사건). 브라우저 새로고침으로는 안 고쳐진다.

```bash
cd ~/AlphaBlock
./scripts/deploy.sh                 # git pull → __pycache__ 정리 → 셋 다 재시작 + 상태
# 또는 개별: ./scripts/deploy.sh dashboard|collector|live
# git 동기화 없이 캐시 정리 + 재시작만: ./scripts/deploy.sh --no-pull
# 실행할 명령만 미리보기(리눅스 밖에서도): ./scripts/deploy.sh --dry-run
```

세 단계(fetch + fast-forward pull → `__pycache__`/`*.pyc` 정리 → `systemctl restart` + 상태)를
항상 한 세트로 묶으므로 세 단계 중 하나를 빠뜨려 생기는 어긋남이 없다. working tree 가
깨끗하지 않으면(손으로 얹힌 변경) 덮어쓰기 전에 멈추고 알린다 — 먼저 `git stash` 또는
`git checkout` 하라. `.env`·DB 는 건드리지 않으므로 `ALPHABLOCK_LIVE_TRADING`(기본 false)은
이 스크립트로 바뀌지 않는다(페이퍼 전용).

## 4b. 유닛 템플릿이 바뀌었을 때 — 재설치 절차 (서버에서, WAN-318)

`deploy.sh` 는 **코드만** 새로 하고 `/etc/systemd/system/*.service` 는 **그대로 둔다**.
`scripts/systemd/*.template` 이 바뀐 배포(예: WAN-318 의 doctor 주기·I/O 우선순위·
`SuccessExitStatus=143`)는 **설치 스크립트를 다시 돌려야** 반영된다 — 안 돌리면 소스만 새것이고
돌고 있는 유닛은 옛 설정이다(같은 부류의 「배포했다고 믿는데 안 바뀐 것」).

```bash
cd ~/AlphaBlock && git pull

# ① 서비스 유닛(collector·live·dashboard) 재설치 — 재시작까지 함께 된다
./scripts/install-systemd.sh collector
./scripts/install-systemd.sh live
./scripts/install-systemd.sh dashboard

# ② doctor 타이머 두 쌍 재설치(옛 15min 타이머를 새 주기로 덮어쓴다)
./scripts/install-systemd.sh doctor

# ③ 상태 워치 타이머(WAN-344) — 옛 서버에는 **아예 없다**. 처음 등록하는 셈이다.
./scripts/install-systemd.sh watch
uv run -- alphablock watch --test-message      # 🚨 폰에 도착하는지 1회 확인

# ④ 확인
systemctl cat alphablock-live | grep SuccessExitStatus        # 143 이 보여야 한다
systemctl cat alphablock-doctor | grep -E 'Nice|IOScheduling' # idle · 19
systemctl list-timers 'alphablock-*' --no-pager               # doctor 1d / 1h · watch 10min
```

⚠️ **옛 설치본이 남아 있을 수 있다** — 15min 타이머가 이미 돌던 서버라면 ②가 그 파일을
덮어쓰지만, `install-systemd.sh` 는 `daemon-reload` 까지 하므로 추가 조치는 없다. 확인은
`grep OnUnitActiveSec /etc/systemd/system/alphablock-doctor.timer`.

📌 **§3 확인법**: `sudo systemctl stop alphablock-live` 후 `systemctl status alphablock-live`
가 `failed` 가 아니라 **`inactive (dead)`** 여야 한다(옛 유닛은 SIGTERM 종료 143 을 실패로
찍어 정상 정지와 크래시가 구분되지 않았다). doctor 는 **일부러 예외**다 — 이상 시 종료 코드
1 = `systemctl --failed` 감시가 설계라 `SuccessExitStatus` 를 넣지 않았다.

## 4c. DB 백업 — 검증된 백업만 남긴다 (WAN-318 §4)

```bash
cd ~/AlphaBlock
# 타이머만 멈추면 이미 돌고 있는 점검은 계속 돈다 — 서비스까지 함께 세운다.
sudo systemctl stop alphablock-live alphablock-collector \
    alphablock-doctor.timer alphablock-doctor-light.timer \
    alphablock-doctor.service alphablock-doctor-light.service

./scripts/db-backup.sh                       # data/ohlcv.db → data/ohlcv.db.bak-<타임스탬프>

sudo systemctl start alphablock-live alphablock-collector \
    alphablock-doctor.timer alphablock-doctor-light.timer
```

🚨 **`cp`·맨손 `sqlite3 .backup` 을 쓰지 말 것.** 2026-08-17 에 실제로, doctor 가 DB 를 붙잡은
상태에서 돈 `.backup` 이 중간에 끊겼는데 **잘린 1.5GB 파일이 4.0GB 백업과 똑같은 이름으로
남았다**(옆에 `-journal` 이 중단 흔적으로 함께). 그걸 복구본으로 쓰면 DB 의 3분의 2 가 조용히
사라진다. `db-backup.sh` 는 임시 이름으로 받아 **검증(헤더 페이지 수 × 페이지 크기 = 실제 크기
· 스키마 읽힘 · 저널 없음)에 성공해야만** 최종 이름을 붙이고, 실패하면 `.FAILED` 로 남기며
종료 코드 1 을 낸다.

- 이미 갖고 있는 백업이 성한지: `./scripts/db-backup.sh --verify-only <파일>`
- 더 깊게 보려면 `--quick-check`(수 GB 면 수 분~수십 분).
- 러너·수집기·doctor 가 돌고 있으면 **거부**한다(경합이 사고 원인이었다) — 정말 필요하면
  `--allow-running`.

## 5. 검증 — WAN-174 완료 기준

1. **웹소켓 수신(핵심)**: 로컬에서 막히던 그 스트림이 서버에서 뚫리는지.

   ```bash
   uv run -- alphablock status        # 초록불 + 시리즈 신선도
   tail -f logs/collector.log         # 1분봉이 실시간으로 붙는지
   ```

2. **러너**: `logs/live.log`에 시그널 평가 루프가 돌고, 페이퍼 체결/알림이 기록되는지.
3. **자동 복구**: `sudo reboot` 후 셋 다 자동으로 올라오는지 —
   `systemctl status alphablock-collector alphablock-live alphablock-dashboard`.
4. **가동 커버리지**: 며칠 가동 후 `alphablock status`의 신선도/갭으로 "구멍 없이 돈다" 확인.
   갭이 보이면 `uv run -- alphablock backfill`로 1회 복구(WAN-35). 조용히 멈춘 스트림의
   자동 재접속은 WAN-173(워치독)이 담당한다.
5. **러너 정지 감시(WAN-344)**: `systemctl list-timers alphablock-watch.timer` 가 다음 실행을
   보여야 하고, `alphablock watch --test-message` 가 폰에 도착해야 한다. 이 둘이 아니면
   **러너가 죽어도 아무도 모른다**(수집기는 별개 프로세스라 봉은 계속 신선하다).
6. **DB 무결성(WAN-185)**: 무결성 타이머가 도는지 `systemctl list-timers alphablock-doctor.timer`,
   최근 실행 결과는 `systemctl status alphablock-doctor.service`(또는 `logs/doctor.log`).
   한 번 손으로 돌려 초록불 확인: `uv run -- alphablock doctor`(종료 코드 0). ⚠️ `data/`가
   FUSE/네트워크 마운트 위면 07-22 손상이 재발하므로 **로컬 디스크인지 먼저 확인**(WAN-195):
   `df -T data`가 ext4/xfs 등 로컬 파일시스템이어야 한다.

## 6. 대시보드 접속 (로컬 맥에서)

대시보드는 서버의 `127.0.0.1`에만 바인딩된다. SSH 터널로 접속:

```bash
ssh -N -L 8501:127.0.0.1:8501 <서버>
# 브라우저에서 http://localhost:8501
```

## 7. DB 배치 설계 — 수집=서버 / 백테스트=로컬

PM 제약 4번("DB를 어디 두고 동기화할지")에 대한 채택 설계:

- **서버 DB = 운영 정본**. 수집기가 쌓고 러너·대시보드가 읽는다. 백테스트는 여기서 안 돌린다.
- **로컬 맥 DB = 백테스트용 사본이며, 동기화가 필요 없다.** ASTx가 막는 것은 **웹소켓뿐**이고
  REST(`fapi`) 백필은 로컬에서 정상 동작한다(WAN-174 진단). 백테스트 전에 로컬에서
  `uv run -- alphablock backfill`(또는 `history`)로 REST 백필해 최신화하면 된다.
- 두 DB가 완전히 같아야 할 일(예: 서버 수집분과 로컬 백필분의 패리티 감사)이 생기면 그때만
  스냅숏을 내린다: 서버에서 §3과 같은 체크포인트 후 `scp` **역방향** 복사. 상시 동기화
  파이프라인은 만들지 않는다(1GB 박스에 부담 + 필요 근거 없음).

## 8. 역할 요약

| 위치 | 역할 | 근거 |
| --- | --- | --- |
| 리눅스 서버 (오라클 무료) | 실시간 수집 · 페이퍼 러너 · 대시보드 (상시) | ASTx 없음 → 웹소켓 정상 |
| 로컬 맥 | 백테스트 격자 · 개발 · REST 백필 | RAM 여유 · REST는 로컬에서도 정상 |

## 범위 밖

- ASTx를 로컬에서 제거·우회하는 방안(서버로 회피가 이 이슈의 결정).
- 실매매 전환·유료 서버 재이전(별도 결정), KST 표시 통일(WAN-172), 스트림 워치독(WAN-173).
