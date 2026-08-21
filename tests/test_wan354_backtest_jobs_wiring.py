"""백테 병렬 워커 수 배선 (WAN-354).

이 저장소가 반복해 겪은 실패는 **「설정했다고 믿으면서 기본값으로 도는」** 것이다
(WAN-91/95/112/123/159). WAN-294가 `ALPHABLOCK_BACKTEST_JOBS`라는 덮어쓰기 자리를
만들었지만, 이 이슈에서 그 자리가 두 겹으로 새고 있음이 드러났다:

1. `.env`가 **CWD 기준 상대 경로**라 저장소 밖에서 실행하면 조용히 무시됐다.
2. `alphablock` CLI의 백테 하위 명령(`trades`·`compare`·`stop-width`·`parity`)이
   `--jobs` 기본값을 **리터럴 1로 박아** 두어 설정이 그 경로에는 **닿지도 않았다** —
   즉 야간 크론은 오버서브가 아니라 **직렬**로 돌았고, 서버 `.env`에 값을 넣어도
   아무 일도 일어나지 않았을 것이다.

그래서 여기 테스트는 전부 **라벨이 아니라 동작**을 건다: 파서 기본값이 무엇인지,
실제로 푼 값이 무엇인지, 그리고 워커 프로세스가 정말 그 수만큼 뜨는지.

⚠️ `--jobs`는 결과를 안 바꾸는 순수 성능 노브다(WAN-121: 직렬 = 병렬 비트 동일) —
이 배선은 측정값·재현성·캐시 지문을 하나도 건드리지 않는다.
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from backtest import harness
from cli.main import build_parser, resolve_jobs_arg
from config.settings import Settings

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 백테를 부르는 `alphablock` 하위 명령 — 넷 다 같은 설정을 읽어야 한다. 하나만 리터럴로
#: 박혀 있어도 「고쳤다고 믿으면서 그 경로만 다른 값으로 도는」 상태가 된다.
_BACKTEST_SUBCOMMANDS = {
    "trades": ["trades", "--day", "2026-08-17"],
    "compare": ["compare", "--day", "2026-08-17"],
    "stop-width": ["stop-width", "--day", "2026-08-17"],
    "parity": ["parity"],
}


# --------------------------------------------------------------------------- #
# §1 `.env` 가 실제로 읽히는 자리
# --------------------------------------------------------------------------- #


def _probe_source() -> str:
    """자식 프로세스에서 `Settings().backtest_jobs`를 찍는 스크립트."""
    return textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        from config.settings import Settings
        print(Settings().backtest_jobs)
        """
    )


def _resolved_jobs_in(cwd: Path, env: dict[str, str] | None = None) -> int:
    """`cwd`에서 새 프로세스로 설정을 푼 값.

    `Settings()`의 `.env` 탐색은 **프로세스 CWD**에 달려 있으므로 같은 프로세스 안에서
    `monkeypatch.chdir`로 흉내 내면 캐시·임포트 시점에 오염된다 — 실제로 새 프로세스를
    띄워 잰다(재는 것이 「그 자리에서 정말 읽히나」이므로 이 비용은 측정의 일부다).
    """
    child_env = dict(os.environ)
    # 부모 환경변수가 새어 들어가면 `.env`가 아니라 그것을 재게 된다(실제 환경변수가 이긴다).
    child_env.pop("ALPHABLOCK_BACKTEST_JOBS", None)
    child_env.update(env or {})
    out = subprocess.run(
        [sys.executable, "-c", _probe_source()],
        cwd=cwd,
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip().splitlines()[-1])


class _RepoEnvFile:
    """저장소 루트 `.env`를 테스트 동안만 쓰는 손잡이."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, text: str) -> None:
        self._path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo_env_file() -> Iterator[_RepoEnvFile]:
    """저장소 루트 `.env`를 테스트 동안만 둔다 — 있던 파일은 건드리지 않는다.

    개발용 `.env`가 이미 있으면 **건너뛴다**: 남의 파일을 덮어썼다가 되돌리는 것보다
    안 재는 쪽이 낫다(CI에는 없으므로 거기서는 항상 돈다).
    """
    path = _REPO_ROOT / ".env"
    if path.exists():
        pytest.skip("저장소 루트에 개발용 .env가 이미 있어 이 테스트는 건너뜁니다.")
    try:
        yield _RepoEnvFile(path)
    finally:
        path.unlink(missing_ok=True)


def test_env_file_is_read_from_repo_root_even_outside_the_repo(
    repo_env_file: _RepoEnvFile, tmp_path: Path
) -> None:
    """저장소 **밖** CWD에서도 저장소 루트 `.env`가 먹는다 (WAN-354 §1).

    옛 코드는 `env_file=".env"`(CWD 상대) 하나만 봐서, 저장소 밖에서 돌리면 값이 조용히
    무시되고 코드 기본값으로 돌았다. 서버는 크론이 `cd <저장소>`를 하고 systemd가
    `WorkingDirectory`를 두어 **우연히** 맞고 있었을 뿐이다.
    """
    repo_env_file.write("ALPHABLOCK_BACKTEST_JOBS=3\n")
    assert _resolved_jobs_in(tmp_path) == 3


def test_cwd_env_file_still_wins_over_repo_root(
    repo_env_file: _RepoEnvFile, tmp_path: Path
) -> None:
    """기존 동작 보존 — CWD의 `.env`가 여전히 최우선이다(순수 추가지 교체가 아니다)."""
    repo_env_file.write("ALPHABLOCK_BACKTEST_JOBS=3\n")
    (tmp_path / ".env").write_text("ALPHABLOCK_BACKTEST_JOBS=2\n", encoding="utf-8")
    assert _resolved_jobs_in(tmp_path) == 2


def test_real_env_var_beats_every_env_file(repo_env_file: _RepoEnvFile, tmp_path: Path) -> None:
    """실제 환경변수는 어느 `.env`보다도 이긴다 — 크론 줄에 직접 주입해도 먹는다."""
    repo_env_file.write("ALPHABLOCK_BACKTEST_JOBS=3\n")
    (tmp_path / ".env").write_text("ALPHABLOCK_BACKTEST_JOBS=2\n", encoding="utf-8")
    assert _resolved_jobs_in(tmp_path, {"ALPHABLOCK_BACKTEST_JOBS": "7"}) == 7


def test_code_default_stays_four(repo_env_file: _RepoEnvFile, tmp_path: Path) -> None:
    """완료 기준 4: 코드 기본값은 **안 바꾼다**(M1 성능 코어 수). 서버는 설정으로 덮는다."""
    assert Settings.model_validate({}).backtest_jobs == 4
    assert _resolved_jobs_in(tmp_path) == 4  # .env 가 하나도 없으면 코드 기본값


# --------------------------------------------------------------------------- #
# §2 CLI 가 그 설정을 실제로 읽는가
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(_BACKTEST_SUBCOMMANDS))
def test_cli_jobs_default_is_unset_not_a_literal(name: str) -> None:
    """네 하위 명령 전부 `--jobs` 기본값이 **미지정**이어야 한다 (WAN-354 §2).

    리터럴(옛 `default=1`)이면 설정이 그 경로에 **닿지 않는다** — 값을 아무리 덮어도
    바뀌지 않는 「자리만 있고 배선은 없는」 상태다.
    """
    args = build_parser().parse_args(_BACKTEST_SUBCOMMANDS[name])
    assert args.jobs is None


def test_resolve_jobs_arg_follows_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """미지정이면 설정 기본값으로 풀고, **출처를 함께** 돌려준다."""
    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=2))
    workers, origin = resolve_jobs_arg(None)
    assert workers == 2
    assert "ALPHABLOCK_BACKTEST_JOBS=2" in origin


def test_resolve_jobs_arg_explicit_beats_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """명시적 `--jobs N`은 설정과 무관하게 이긴다(`backtest.run`과 같은 규약)."""
    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=2))
    workers, origin = resolve_jobs_arg(6)
    assert workers == 6
    assert "명시" in origin


def test_cli_main_resolves_jobs_and_reports_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main()`이 **한 곳에서** 풀어 하위 명령에 정수를 넘기고, 쓴 값을 stderr에 남긴다.

    하위 명령마다 각자 풀면 「하나는 설정을 읽고 하나는 리터럴」인 지금 상태가 다시
    만들어진다. 로그 줄은 서버에서 「설정이 진짜 먹었나」를 사후에 확인하는 근거다
    (완료 기준 2) — stdout은 표/CSV 전용이라 stderr로 간다.
    """
    cli_main = importlib.import_module("cli.main")  # `from cli import main`은 함수를 집는다

    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=2))
    seen: list[int] = []

    def _fake(args: argparse.Namespace, settings: object) -> int:
        seen.append(args.jobs)
        return 0

    parser = build_parser()
    real_parse = parser.parse_args

    def _build() -> object:
        namespace = real_parse(["trades", "--day", "2026-08-17"])
        namespace.func = _fake

        class _P:
            def parse_args(self, argv: object = None) -> object:
                return namespace

        return _P()

    monkeypatch.setattr(cli_main, "build_parser", _build)
    assert cli_main.main([]) == 0
    assert seen == [2]
    assert "병렬 설정: 워커 2개" in capsys.readouterr().err


def test_cli_main_leaves_explicit_jobs_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """명시적으로 준 값은 `main()`이 덮어쓰지 않는다."""
    cli_main = importlib.import_module("cli.main")  # `from cli import main`은 함수를 집는다

    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=2))
    seen: list[int] = []

    def _record(args: argparse.Namespace, settings: object) -> int:
        seen.append(args.jobs)
        return 0

    namespace = build_parser().parse_args(["trades", "--day", "2026-08-17", "--jobs", "5"])
    namespace.func = _record

    class _P:
        def parse_args(self, argv: object = None) -> object:
            return namespace

    monkeypatch.setattr(cli_main, "build_parser", lambda: _P())
    assert cli_main.main([]) == 0
    assert seen == [5]


def test_commands_without_jobs_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--jobs`가 없는 하위 명령(`status` 등)에는 아무 속성도 만들지 않는다."""
    cli_main = importlib.import_module("cli.main")  # `from cli import main`은 함수를 집는다

    namespace = build_parser().parse_args(["status"])
    namespace.func = lambda args, settings: 0

    class _P:
        def parse_args(self, argv: object = None) -> object:
            return namespace

    monkeypatch.setattr(cli_main, "build_parser", lambda: _P())
    assert cli_main.main([]) == 0
    assert not hasattr(namespace, "jobs")


# --------------------------------------------------------------------------- #
# §3 동작 확인 — 워커가 정말 그 수만큼 뜨는가
# --------------------------------------------------------------------------- #


def test_probe_counts_the_workers_that_actually_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """`scripts.wan354_jobs_probe`가 **실제로 뜬 프로세스 수**를 센다 (완료 기준 2).

    설정 파일에 줄이 있는지가 아니라 프로세스가 그 수만큼 생기는지를 본다 — 서버에서
    사람이 돌릴 확인이 여기서 동작으로 고정된다.
    """
    from scripts import wan354_jobs_probe

    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=2))
    requested, workers, spawned = wan354_jobs_probe.count_workers(cells=48)
    assert (requested, workers) == (2, 2)
    assert spawned == 2


def test_probe_serial_path_makes_no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """워커 1개는 풀을 만들지 않는다 — 부모가 곧 일꾼이다(`_iter_outcomes`와 같은 규약)."""
    from scripts import wan354_jobs_probe

    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=1))
    assert wan354_jobs_probe.count_workers(cells=48) == (1, 1, 1)


def test_probe_is_capped_by_cell_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """셀보다 워커가 많으면 셀 수로 캡된다 — 「요청값」이 아니라 「실제 쓴 값」을 본다."""
    from scripts import wan354_jobs_probe

    monkeypatch.setattr(harness, "get_settings", lambda: Settings(backtest_jobs=4))
    requested, workers, spawned = wan354_jobs_probe.count_workers(cells=2)
    assert (requested, workers, spawned) == (4, 2, 2)
