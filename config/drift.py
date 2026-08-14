"""채택 좌표 ↔ 환경변수 드리프트 점검 (WAN-309).

pydantic-settings는 환경변수(`.env` 포함)가 코드 기본값을 이긴다 — 그래서 재-베이스라인이
코드 기본값을 옮겨도(예: WAN-307 유니버스 9→12종목) 낡은 `.env`가 남아 있으면 **그 결정은
실제 프로세스에 도달하지 않는다**. 실제 사고: 2026-07-22자 `.env`가 9종목·BTC/1h 단독을
박아 두어 WAN-191(감시 확대)·WAN-252(2h 승격)·WAN-307(12종목)이 전부 무효화될 뻔했다.

이 모듈은 그 어긋남을 **보이게만** 한다 — 값을 자동으로 고치거나 덮어쓰지 않는다(조용히
코드 기본값을 강제하면 "왜 내 설정이 안 먹지"라는 반대 방향 사고가 된다). 차이가 의도된
것일 수 있으므로(좁혀서 테스트 중 등) 경고이지 에러가 아니다.

⚠️ 비밀값(API 키·토큰)은 여기서 다루지 않는다 — 드리프트 점검은 **좌표 필드의 값**과
**키 이름**만 본다. `.env.example` 키 목록 대조(§2)도 키 이름만 출력한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from config.settings import Settings

# 채택 좌표를 실어 나르는 설정 필드들. "코드 기본값 = 채택 좌표"라는 저장소 규약
# (WAN-182/252/307: 재-베이스라인이 곧 기본값 이동) 위에서, 실효 설정값이 코드 기본값과
# 다르면 어딘가(환경변수/.env)가 덮어쓴 것이다. 기본값을 여기 복붙하지 않고 pydantic
# 필드에서 읽으므로 다음 좌표 변경 때 이 목록을 같이 고칠 필요가 없다.
_COORDINATE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("symbols", "ALPHABLOCK_SYMBOLS", "수집 유니버스"),
    ("timeframes", "ALPHABLOCK_TIMEFRAMES", "수집 타임프레임"),
    ("live_signal_symbols", "ALPHABLOCK_LIVE_SIGNAL_SYMBOLS", "페이퍼 감시 심볼"),
    ("live_signal_timeframes", "ALPHABLOCK_LIVE_SIGNAL_TIMEFRAMES", "페이퍼 감시 타임프레임"),
)


@dataclass(frozen=True)
class CoordinateDrift:
    """한 좌표 필드의 실효값 ↔ 채택 기본값 어긋남."""

    field: str
    env_key: str
    label: str
    actual: tuple[str, ...]
    adopted: tuple[str, ...]

    @property
    def missing(self) -> tuple[str, ...]:
        """채택 좌표에는 있는데 실효 설정에는 없는 값(낡은 목록의 서명)."""
        actual = set(self.actual)
        return tuple(v for v in self.adopted if v not in actual)

    @property
    def extra(self) -> tuple[str, ...]:
        """실효 설정에는 있는데 채택 좌표에는 없는 값."""
        adopted = set(self.adopted)
        return tuple(v for v in self.actual if v not in adopted)


def adopted_default(field: str) -> tuple[str, ...]:
    """설정 필드의 코드 기본값(= 채택 좌표)을 pydantic 필드 정의에서 읽는다."""
    value = Settings.model_fields[field].get_default(call_default_factory=True)
    return tuple(str(v) for v in value)


def check_coordinate_drift(settings: Settings) -> list[CoordinateDrift]:
    """실효 설정이 채택 좌표와 다른 필드를 모은다. 전부 일치하면 빈 리스트.

    순서만 다르고 집합이 같으면 드리프트가 아니다(좌표 필드는 순서에 의미가 없다).
    """
    drifts: list[CoordinateDrift] = []
    for field, env_key, label in _COORDINATE_FIELDS:
        actual = tuple(str(v) for v in getattr(settings, field))
        adopted = adopted_default(field)
        if set(actual) == set(adopted):
            continue
        drifts.append(
            CoordinateDrift(
                field=field, env_key=env_key, label=label, actual=actual, adopted=adopted
            )
        )
    return drifts


def render_drift_lines(drifts: list[CoordinateDrift]) -> list[str]:
    """드리프트를 사람이 읽는 경고 줄로. 드리프트가 없으면 빈 리스트(아무것도 안 찍는다)."""
    lines: list[str] = []
    for d in drifts:
        parts: list[str] = []
        if d.missing:
            parts.append("누락: " + ", ".join(d.missing))
        if d.extra:
            parts.append("추가: " + ", ".join(d.extra))
        detail = " · ".join(parts)
        lines.append(
            f"⚠️ {d.env_key}가 채택 좌표({d.label} {len(d.adopted)}개)와 다릅니다"
            f" — 현재 {len(d.actual)}개 · {detail}"
        )
    if lines:
        lines.append(
            "   (환경변수/.env가 코드 기본값을 덮어쓰고 있습니다 — 의도한 축소가 아니라면"
            " `.env`를 갱신하고 프로세스를 재시작하세요. 코드는 값을 자동으로 고치지 않습니다.)"
        )
    return lines


# `KEY=` 할당 줄(선택적 export 접두, 주석 제외). 값은 읽지 않는다 — 키 이름만.
_ENV_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def parse_env_keys(path: Path) -> set[str] | None:
    """env 파일의 미주석 `KEY=` 키 이름 집합. 파일이 없으면 None."""
    if not path.exists():
        return None
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_KEY_RE.match(line)
        if match is not None:
            keys.add(match.group(1))
    return keys


def env_example_only_keys(
    example_path: Path = Path(".env.example"), env_path: Path = Path(".env")
) -> list[str] | None:
    """`.env.example`에는 있는데 `.env`에는 없는 키 이름 목록(정렬).

    없는 키는 코드 기본값을 따르므로 대부분 문제가 아니다 — 다만 "의도적으로 비움"과
    "몰라서 빠짐"을 구분할 수 있게 목록을 보이게 한다(WAN-309 §2). 어느 한쪽 파일이
    없으면 None(대조 불가 — 예: `.env` 없이 코드 기본값으로 도는 기계).
    """
    example_keys = parse_env_keys(example_path)
    env_keys = parse_env_keys(env_path)
    if example_keys is None or env_keys is None:
        return None
    return sorted(example_keys - env_keys)
