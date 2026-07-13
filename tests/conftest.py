"""pytest 전역 설정.

`Settings` 로딩이 로컬 `.env`/환경변수에 오염되지 않도록 격리한다(WAN-43).
기본값을 검증하는 테스트는 로컬에 `.env`가 있든 없든 동일하게 동작해야 한다.
환경변수 오버라이드 자체를 검증하는 테스트는 `monkeypatch.setenv`로 명시적으로
값을 주입하므로 이 격리와 무관하게 정상 동작한다.
"""

from __future__ import annotations

import os

import pytest

from config.settings import Settings


@pytest.fixture(autouse=True)
def _isolate_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    env_prefix = Settings.model_config.get("env_prefix", "")
    for key in list(os.environ):
        if key.startswith(env_prefix):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
