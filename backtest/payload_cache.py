"""WAN-394 §0 — 북 후보 payload의 디스크 캐시.

## 왜

격자 비용의 대부분이 **같은 후보를 다시 만드는 것**이다(실측: WAN-386 4시간 50분 중 후보
생성 4시간 40분 · WAN-389 2시간 48분 중 배치는 **6초**). 후보 집합은 좌표(종목·TF·창)와
탐지·진입 규칙만 정하고 **가드·재진입 배치·복리·렌즈-사후 축과는 무관**한데, 격자를 돌 때마다
그 무거운 패스를 처음부터 다시 돌고 있었다.

## 🚨 급소 — 옛 후보를 조용히 재사용하면 이 저장소 최악의 사고다

엔진이 바뀌었는데 캐시가 히트하면 **「고쳤다고 믿으면서 옛 엔진 결과를 인용」**하게 된다
(WAN-364 소급 취소가 6년치 표를 통째로 얼린 그 부류이고, 캐시는 그것을 **자동화**한다).
그래서 키를 **손으로 나열하지 않는다**:

📌 **키는 `_Task` 그 자체다.** `wan169_leverage_book._Task`는 한 칸의 후보 생성을 **완전히**
기술하는 frozen 데이터클래스라, *「payload를 바꾸는 것은 전부 `_Task`에 있다」*가 구조적으로
참이다. 뒤집으면 **`_Task`에 없는 축은 키에 들어갈 수가 없다** — 손절폭 가드
(`iter_book_segments(min_stop_distance_fraction=)`) · 재진입 **배치**
(`include_reentry=`) · 복리(`compound_sizing=`)는 배치 인자라 `_Task`에 아예 없다. 즉
완료기준 3(「가드·재진입을 바꿔도 히트한다」)은 규칙이 아니라 **타입의 성질**이다.

여기에 **소스 지문 두 겹**을 얹는다:

* `trade_store.engine_source_revision()` — 엔진 정의 파일(`strategy/`·`backtest/substep.py`
  등)의 내용 해시. 엔진이 한 글자라도 바뀌면 값이 달라진다(WAN-253).
* `RUNNER_SOURCE_FILES` — 그 목록에 **없으면서** 후보를 만드는 러너
  (`wan169_leverage_book.py`의 `_Task`→`run_cell` 배선 · `wan228_reentry_census.py`의 재무장
  파생). 이 둘이 빠지면 「재진입 파생을 고쳤는데 캐시가 히트하는」 구멍이 남는다.

## 익절 배수만 예외 — 부분집합이면 히트한다

`confirmation_multiples`(WAN-386 §0)는 `_Task`에 있지만 **키에서 뺀다.** 대신 파일이 어떤
배수를 담고 있는지 적어 두고 **요청 ⊆ 저장**일 때만 히트시킨 뒤 요청한 키만 남겨 돌려준다.
근거는 `derive_arm_candidates`가 배수마다 **독립으로** 청산을 내는 성질이고
(`tests/test_payload_cache.py`가 그 독립성을 동작으로 건다), 그래서 배수 하나를 더 재려고
4시간을 다시 쓰지 않아도 된다. 저장은 **합집합**이라 캐시가 쌓인다.

## 저장 위치·정리

`backtest/cache/payloads/<revision>/<key>.pkl.gz`(gitignore). 🚨 **DB에 넣지 않는다**
(WAN-194: 6년치 시세와 같은 파일에 두지 않는다). 🚨 **자동 삭제가 없다**(WAN-194/297 원칙) —
정리는 명시 플래그다::

    uv run python -m backtest.payload_cache --stats            # 리비전별 개수·크기
    uv run python -m backtest.payload_cache --prune-stale      # 세기만 (기본)
    uv run python -m backtest.payload_cache --prune-stale --apply   # 실제 삭제

`--prune-stale`은 **지금 리비전이 아닌** 디렉터리만 본다. 기준이 하나도 없으면 거부한다.

## 안 하는 것

- ❌ 기본으로 켜지 않는다 — `run_cells(payload_cache=…)`를 **명시**해야 돈다(안 주면 예전과
  비트 단위로 같다).
- ❌ 미스를 조용히 메우지 않는다 — 몇 칸이 미스인지 **실행 시작에 찍는다**(WAN-335 관행).
- ❌ 배치 산출물(북 집계·거래)은 담지 않는다. 담는 것은 **후보**뿐이다.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pickle
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backtest.trade_store import UNKNOWN_REVISION, engine_source_revision

if TYPE_CHECKING:  # pragma: no cover - 순환 임포트 회피(런타임엔 필요 없다)
    from backtest.wan169_leverage_book import CellPayload, _Task

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "DEFAULT_CACHE_DIR",
    "PayloadCache",
    "PayloadFingerprint",
    "RUNNER_SOURCE_FILES",
    "fingerprint",
    "payload_source_revision",
]

#: 캐시에 담기는 **것의 의미**가 바뀌면 올린다(WAN-297/335 규약). 올리면 옛 적재분은
#: 자동 미스가 되고 **지워지지는 않는다**.
CACHE_SCHEMA_VERSION = "wan394.1"

DEFAULT_CACHE_DIR = Path("backtest/cache/payloads")

#: `ENGINE_SOURCE_FILES`(WAN-253)에 **없으면서** 후보를 만드는 러너 소스.
#:
#: 🚨 이 목록이 이 캐시의 유일한 손-유지 부분이다. 엔진 목록은 「엔진 정의」를 덮지만
#: `_Task`→`run_cell` 배선과 재무장 파생은 그 밖에 산다 — 빠지면 「재진입 파생을 고쳤는데
#: 캐시가 히트하는」 구멍이 된다. `tests/test_payload_cache.py`가 두 파일을 건드리면 리비전이
#: 실제로 달라지는지 **동작으로** 확인한다.
RUNNER_SOURCE_FILES: tuple[str, ...] = (
    "backtest/wan169_leverage_book.py",
    "backtest/wan228_reentry_census.py",
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: 키에서 빼는 `_Task` 필드 — **부분집합 매칭**으로 따로 다룬다(위 독스트링).
_SUBSET_FIELDS: frozenset[str] = frozenset({"confirmation_multiples"})


def payload_source_revision(root: str | Path | None = None) -> str:
    """엔진 + 러너 소스의 내용 해시. 둘 중 하나라도 바뀌면 캐시가 **통째로 미스**다."""
    base = Path(root) if root is not None else _REPO_ROOT
    engine = engine_source_revision(base)
    if engine == UNKNOWN_REVISION:
        return UNKNOWN_REVISION
    digest = hashlib.sha256(engine.encode("utf-8"))
    try:
        for rel in RUNNER_SOURCE_FILES:
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update((base / rel).read_bytes())
            digest.update(b"\0")
    except OSError:  # pragma: no cover - 삭제/비-레포 환경 방어
        return UNKNOWN_REVISION
    return f"pay:{digest.hexdigest()[:12]}"


def _stable(value: Any) -> Any:
    """해시에 넣을 수 있는 **안정적인** 표현.

    🚨 `str()`로 뭉개지 않는다 — `harness.UNSET`(Enum)과 `None`이 같은 문자로 접히면
    「필터 끔」과 「채택 기본값」이 한 키를 공유한다(WAN-159가 못 박은 그 구분이 캐시에서
    무너진다). 타입 이름을 함께 실어 두 값을 반드시 가른다.
    """
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, frozenset | set):
        return ["set", sorted(_stable(v) for v in value)]
    if isinstance(value, tuple | list):
        return [_stable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return repr(value)
    return f"{type(value).__name__}:{value!r}"  # pragma: no cover - 방어


def task_spec(task: _Task) -> dict[str, Any]:
    """키에 들어가는 `_Task` 필드 전부(배수 제외). **손으로 나열하지 않는다** — 필드가
    늘면 자동으로 키에 들어간다(빠뜨려서 조용히 틀리는 경로를 없앤다)."""
    return {
        field.name: _stable(getattr(task, field.name))
        for field in fields(task)
        if field.name not in _SUBSET_FIELDS
    }


@dataclass(frozen=True)
class PayloadFingerprint:
    """한 칸 payload의 지문 — `(리비전, 키)`가 파일을 정하고 `multiples`는 부분집합 매칭용."""

    revision: str
    key: str
    multiples: tuple[float, ...]
    schema_version: str = CACHE_SCHEMA_VERSION

    @property
    def rel_path(self) -> Path:
        return Path(self.revision) / f"{self.key}.pkl.gz"


def fingerprint(task: _Task, *, revision: str | None = None) -> PayloadFingerprint:
    """이 칸의 지문. `revision`을 주면 그 값을 쓴다(테스트·감사용)."""
    rev = revision if revision is not None else payload_source_revision()
    spec = {
        "schema": CACHE_SCHEMA_VERSION,
        "revision": rev,
        "task": task_spec(task),
    }
    blob = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return PayloadFingerprint(
        revision=rev,
        key=hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32],
        multiples=tuple(task.confirmation_multiples),
    )


def _multiple_of(arm_key_str: str) -> float:
    return float(arm_key_str.rsplit("|", 1)[1])


class PayloadCache:
    """칸 payload의 디스크 캐시 — 명시적으로 넘겨야만 도는 옵트인 계층.

    🚨 **읽기 실패는 미스이지 예외가 아니다**(끊긴 파일·옛 파이썬 피클) — 캐시는 성능
    노브이지 결과 축이 아니므로, 못 읽으면 그냥 계산한다. 반대로 **쓰기 실패는 시끄럽다**
    (디스크가 찼는데 「캐시했다」고 믿으면 다음 실행이 또 4시간을 쓴다).
    """

    def __init__(
        self,
        directory: str | Path = DEFAULT_CACHE_DIR,
        *,
        revision: str | None = None,
        read: bool = True,
        write: bool = True,
    ) -> None:
        self.directory = Path(directory)
        self.revision = revision if revision is not None else payload_source_revision()
        self.read = read
        self.write = write
        self.hits = 0
        self.misses = 0
        self.stores = 0

    # ------------------------------------------------------------------ #
    # 조회 · 적재
    # ------------------------------------------------------------------ #

    def path_for(self, task: _Task) -> Path:
        return self.directory / fingerprint(task, revision=self.revision).rel_path

    def load(self, task: _Task) -> CellPayload | None:
        """히트면 payload, 미스면 `None`. 카운터는 호출자가 아니라 여기서 센다."""
        if not self.read:
            self.misses += 1
            return None
        fp = fingerprint(task, revision=self.revision)
        blob = self._read(self.directory / fp.rel_path)
        if blob is None:
            self.misses += 1
            return None
        stored = tuple(blob.get("multiples", ()))
        want = fp.multiples
        if not set(want).issubset(stored):
            # 배수가 모자라면 미스다 — 부분만 채워 돌려주면 격자에 **구멍이 뚫린 채**
            # 「캐시 히트」로 보고된다(WAN-335가 이름 붙인 조용한 실패).
            self.misses += 1
            return None
        payload: CellPayload = blob["payload"]
        if want != stored:
            payload = replace(
                payload,
                arm_candidates={
                    k: v for k, v in payload.arm_candidates.items() if _multiple_of(k) in want
                },
            )
        self.hits += 1
        return payload

    def store(self, task: _Task, payload: CellPayload) -> None:
        """이 칸을 적재한다. 같은 키가 이미 있으면 **배수를 합집합으로 병합**한다.

        병합이 안전한 이유: 같은 키 = 배수를 뺀 나머지 스펙이 글자 그대로 같다는 뜻이고,
        배수는 `arm_candidates`만 바꾼다(`_Task.confirmation_arms` 독스트링: *base 후보·재진입
        후보·격리 성과 행은 불변*). 그래서 새 payload에 옛 팔 후보만 얹는다.
        """
        if not self.write:
            return
        fp = fingerprint(task, revision=self.revision)
        path = self.directory / fp.rel_path
        multiples = set(fp.multiples)
        arm_candidates = dict(payload.arm_candidates)
        existing = self._read(path)
        if existing is not None:
            multiples |= set(existing.get("multiples", ()))
            for key, value in existing["payload"].arm_candidates.items():
                arm_candidates.setdefault(key, value)
        merged = replace(payload, arm_candidates=arm_candidates)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        with gzip.open(tmp, "wb") as handle:
            pickle.dump(
                {
                    "schema": CACHE_SCHEMA_VERSION,
                    "revision": fp.revision,
                    "multiples": sorted(multiples),
                    "payload": merged,
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
        tmp.replace(path)
        self.stores += 1

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rb") as handle:
                blob = pickle.load(handle)
        except Exception:  # noqa: BLE001 - 끊긴 파일·옛 피클은 미스로 접는다
            return None
        if not isinstance(blob, dict) or blob.get("schema") != CACHE_SCHEMA_VERSION:
            return None
        return blob

    # ------------------------------------------------------------------ #
    # 인구조사
    # ------------------------------------------------------------------ #

    def census(self, tasks: Sequence[_Task]) -> tuple[int, int]:
        """(히트, 미스) — **계산을 시작하기 전에** 찍는다(WAN-335 관행).

        조회만 하고 카운터를 건드리지 않는다 — 실제 실행에서 다시 세기 때문이다.
        """
        if not self.read:
            return 0, len(tasks)
        hit = 0
        for task in tasks:
            fp = fingerprint(task, revision=self.revision)
            blob = self._read(self.directory / fp.rel_path)
            if blob is not None and set(fp.multiples).issubset(set(blob.get("multiples", ()))):
                hit += 1
        return hit, len(tasks) - hit

    def summary(self) -> str:
        return (
            f"캐시 {self.directory} ({self.revision}): "
            f"히트 {self.hits} · 미스 {self.misses} · 적재 {self.stores}"
        )


# --------------------------------------------------------------------------- #
# 정리 (명시 플래그로만 — 자동 삭제 없음, WAN-194/297)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RevisionStat:
    revision: str
    files: int
    bytes: int
    current: bool


def revision_stats(
    directory: str | Path = DEFAULT_CACHE_DIR, *, revision: str | None = None
) -> list[RevisionStat]:
    root = Path(directory)
    current = revision if revision is not None else payload_source_revision()
    out: list[RevisionStat] = []
    if not root.exists():
        return out
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        files = list(child.glob("*.pkl.gz"))
        out.append(
            RevisionStat(
                revision=child.name,
                files=len(files),
                bytes=sum(f.stat().st_size for f in files),
                current=child.name == current,
            )
        )
    return out


def prune_stale(
    directory: str | Path = DEFAULT_CACHE_DIR,
    *,
    revision: str | None = None,
    apply: bool = False,
) -> list[RevisionStat]:
    """**지금 리비전이 아닌** 디렉터리를 센다(`apply=True`면 지운다).

    🚨 기본이 「세기」다 — 무엇을 지웠는지 모르는 상태를 저장소가 스스로 만들지 않는다
    (WAN-194 원칙 · WAN-297 `--prune-apply`와 같은 규약).
    """
    stale = [s for s in revision_stats(directory, revision=revision) if not s.current]
    if apply:
        for stat in stale:
            shutil.rmtree(Path(directory) / stat.revision, ignore_errors=True)
    return stale


def _fmt_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"  # pragma: no cover - 위 루프가 GB에서 반환한다


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WAN-394 §0 후보 payload 캐시 관리")
    parser.add_argument("--dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--stats", action="store_true", help="리비전별 개수·크기")
    parser.add_argument(
        "--prune-stale", action="store_true", help="지금 리비전이 아닌 적재분을 센다"
    )
    parser.add_argument("--apply", action="store_true", help="정리를 **실제로** 수행한다")
    args = parser.parse_args(argv)

    if not args.stats and not args.prune_stale:
        parser.error("--stats 또는 --prune-stale 중 하나를 주세요(기준 없는 삭제는 거부합니다).")
    if args.apply and not args.prune_stale:
        parser.error("--apply는 --prune-stale과 함께 씁니다.")

    current = payload_source_revision()
    print(f"현재 리비전: {current}")
    stats = revision_stats(args.dir)
    if not stats:
        print(f"적재분 없음 ({args.dir})")
        return 0
    for stat in stats:
        mark = " ← 현재" if stat.current else ""
        print(f"  {stat.revision}: {stat.files}칸 {_fmt_bytes(stat.bytes)}{mark}")
    if args.prune_stale:
        stale = prune_stale(args.dir, apply=args.apply)
        total = sum(s.bytes for s in stale)
        verb = "삭제함" if args.apply else "삭제 대상(세기만 — 지우려면 --apply)"
        print(f"{verb}: {len(stale)}개 리비전 · {_fmt_bytes(total)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
