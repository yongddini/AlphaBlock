"""`.env.example` ↔ `config.settings.Settings` 정합성 가드 (WAN-40).

`.env.example` 는 `.gitattributes` 의 `merge=union` 으로 append 충돌을 자동 해소한다.
union 은 편리하지만 같은 키를 양쪽에서 다르게 *수정*하면 두 줄이 모두 남아 **중복 키**가
생긴다. 또 새 설정 필드를 추가하면서 `.env.example` 문서화를 빠뜨리기 쉽다.

이 테스트는 그 두 가지를 CI에서 잡는다:

1. `.env.example` 안에 같은 `ALPHABLOCK_*` 키가 두 번 이상 나오지 않는다(union 중복 방지).
2. `.env.example` 의 모든 키가 실제 `Settings` 필드(중첩 필드 포함)와 대응한다(오타·삭제 감지).
3. 중첩이 아닌 모든 `Settings` 필드가 `.env.example` 에 문서화돼 있다(누락 방지).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings

from config.settings import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

# `ALPHABLOCK_FOO=` 또는 `# ALPHABLOCK_FOO__BAR=...` 형태에서 키(`ALPHABLOCK_...`)를 뽑는다.
# 주석(`#`) 처리된 예시 라인도 union 이 복제할 수 있으므로 함께 검사한다.
_KEY_RE = re.compile(r"^#?\s*(ALPHABLOCK_[A-Z0-9_]+)\s*=")


def _env_prefix() -> str:
    prefix = Settings.model_config.get("env_prefix", "")
    return str(prefix)


def _delimiter() -> str:
    delim = Settings.model_config.get("env_nested_delimiter", "__")
    return str(delim) if delim else "__"


def _valid_env_keys() -> tuple[set[str], set[str]]:
    """(전체 유효 키, 중첩이 아닌 단순 필드 키) 를 `Settings` 정의로부터 계산한다.

    단순 필드는 `PREFIX + FIELD`, 중첩 BaseModel 필드는 각 하위 필드마다
    `PREFIX + FIELD + DELIM + SUBFIELD` 로 환경변수명이 만들어진다(대문자, 케이스 무시).
    """
    prefix = _env_prefix().upper()
    delim = _delimiter()
    valid: set[str] = set()
    simple: set[str] = set()
    for name, field in Settings.model_fields.items():
        ann = field.annotation
        env_name = f"{prefix}{name.upper()}"
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            # 중첩 파라미터 모델: 하위 필드가 각각 환경변수 키가 된다.
            for sub in ann.model_fields:
                valid.add(f"{env_name}{delim}{sub.upper()}")
        else:
            valid.add(env_name)
            simple.add(env_name)
    return valid, simple


def _extract_keys() -> list[str]:
    lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    keys: list[str] = []
    for line in lines:
        match = _KEY_RE.match(line)
        if match:
            keys.append(match.group(1))
    return keys


def test_env_example_has_no_duplicate_keys() -> None:
    """union 머지가 만든 중복 키를 잡는다. 각 `ALPHABLOCK_*` 키는 정확히 한 번만."""
    keys = _extract_keys()
    seen: set[str] = set()
    duplicates: list[str] = []
    for key in keys:
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    assert not duplicates, f".env.example 에 중복 키가 있다(union 머지 정리 필요): {duplicates}"


def test_env_example_keys_map_to_settings_fields() -> None:
    """`.env.example` 의 모든 키가 실제 Settings 필드(중첩 포함)와 대응해야 한다."""
    valid, _ = _valid_env_keys()
    unknown = sorted(set(_extract_keys()) - valid)
    assert not unknown, f".env.example 에 Settings 필드에 없는 키가 있다: {unknown}"


def test_all_simple_settings_fields_are_documented() -> None:
    """중첩이 아닌 모든 Settings 필드는 `.env.example` 에 문서화돼 있어야 한다."""
    _, simple = _valid_env_keys()
    documented = set(_extract_keys())
    missing = sorted(simple - documented)
    assert not missing, f".env.example 에 문서화되지 않은 Settings 필드가 있다: {missing}"


def test_settings_uses_expected_env_conventions() -> None:
    """이 가드가 의존하는 prefix/delimiter 규약이 유지되는지 확인한다."""
    assert issubclass(Settings, BaseSettings)
    assert _env_prefix() == "ALPHABLOCK_"
    assert _delimiter() == "__"
