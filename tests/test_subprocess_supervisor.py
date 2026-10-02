"""One subprocess supervisor (design-hardening plan, Phase 6).

``abicheck.deadline.supervised_popen`` is the only place that starts a child
in its own process group and tears that group down. Two halves:

* a structural gate: no other ``start_new_session``/``killpg`` pair, and no
  direct ``subprocess`` call in the extraction trees, so a new site cannot
  quietly re-implement (or skip) the group/registration/teardown logic;
* the contract the gate routes everyone onto, stated over several exit
  paths rather than one: whatever leaves the ``with`` block by exception,
  the whole group is gone and the SIGTERM-cleanup registry holds nothing.
"""

from __future__ import annotations

import ast
import os
import subprocess
import time
from pathlib import Path

import pytest

from abicheck import deadline

REPO = Path(__file__).resolve().parent.parent

#: The supervisor itself.
SUPERVISOR = "abicheck/deadline.py"

#: Reviewed exceptions, each with the reason it cannot use the supervisor.
ALLOWED_GROUP_MANAGERS: dict[str, str] = {
    # Stdlib-only harness that measures an *installed* abicheck, possibly a
    # base revision without supervised_popen; importing the product would
    # also make it measure itself. See the module's own docstring.
    "scripts/perf_receipt.py": "stdlib-only perf harness measuring another product revision",
}

#: Every child process under ``abicheck/`` goes through deadline.run_bounded /
#: supervised_popen; these are the reviewed exceptions, by file.
#: Each entry pins the exact number of direct calls the file may make, so a
#: new direct call in an allowlisted file still fails the gate.
DIRECT_CALL_ALLOWLIST: dict[str, tuple[int, str]] = {
    # abicheck-cc runs the user's own compiler command as a transparent
    # wrapper: it must inherit the terminal, signals and process group, and
    # must never be time-bounded.
    "abicheck/cc_wrapper.py": (
        1,
        "transparent compiler passthrough",
    ),
    # Streaming zstd / rpm2cpio|cpio pipelines: each owns a size-bounded
    # reader and its own wall-clock deadline, and no child spawns
    # grandchildren; moving them onto supervised_popen is a separate slice.
    "abicheck/package.py": (
        3,
        "streaming decompression pipelines with their own deadline",
    ),
    # ADR-061 migrated layers may not import the unclassified root
    # ``deadline`` module (architecture/dispositions.yaml); both are short
    # git queries with their own timeout and no grandchildren.
    "abicheck/frontends/cli/runtime.py": (
        1,
        "frontends cannot import unclassified deadline yet",
    ),
    "abicheck/workflows/changed_paths.py": (
        1,
        "workflows cannot import unclassified deadline yet",
    ),
}

_DIRECT_CALLS = {"run", "Popen", "call", "check_call", "check_output"}


def _py_files(*roots: str) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        p = REPO / root
        out.extend([p] if p.is_file() else sorted(p.rglob("*.py")))
    return out


def _group_management_sites(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "start_new_session":
            lines.append(node.value.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in {
            "killpg",
            "setsid",
            "setpgrp",
        }:
            lines.append(node.lineno)
    return lines


def _direct_subprocess_calls(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _DIRECT_CALLS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]


@pytest.mark.repo_scan
def test_only_the_supervisor_manages_process_groups() -> None:
    offenders = []
    for path in _py_files("abicheck", "scripts", "action"):
        rel = path.relative_to(REPO).as_posix()
        if rel == SUPERVISOR or rel in ALLOWED_GROUP_MANAGERS:
            continue
        for line in _group_management_sites(
            ast.parse(path.read_text(encoding="utf-8"))
        ):
            offenders.append(f"{rel}:{line}")
    assert offenders == [], (
        "start a child via abicheck.deadline.run_bounded/supervised_popen "
        f"instead of managing its process group by hand: {offenders}"
    )


@pytest.mark.repo_scan
def test_allowlist_entries_still_need_their_exception() -> None:
    for rel in ALLOWED_GROUP_MANAGERS:
        path = REPO / rel
        assert path.is_file(), f"stale allowlist entry: {rel}"
        assert _group_management_sites(ast.parse(path.read_text(encoding="utf-8"))), (
            f"{rel} no longer manages a process group; drop its allowlist entry"
        )


@pytest.mark.repo_scan
def test_no_direct_subprocess_calls_outside_the_supervisor() -> None:
    offenders = []
    for path in _py_files("abicheck"):
        rel = path.relative_to(REPO).as_posix()
        if rel == SUPERVISOR:
            continue
        lines = _direct_subprocess_calls(ast.parse(path.read_text(encoding="utf-8")))
        allowed = DIRECT_CALL_ALLOWLIST.get(rel, (0, ""))[0]
        if len(lines) != allowed:
            offenders.append(
                f"{rel}: {len(lines)} direct call(s) at {sorted(lines)}, allowlist pins {allowed}"
            )
    assert offenders == [], (
        "use abicheck.deadline.run_bounded, or update the pinned count with a "
        f"reviewed reason: {offenders}"
    )
    for rel in DIRECT_CALL_ALLOWLIST:
        assert (REPO / rel).is_file(), f"stale allowlist entry: {rel}"


def test_gate_detectors_see_each_spelling() -> None:
    # Oracle for the two scanners: each known spelling is caught, a
    # look-alike is not.
    flagged = [
        "subprocess.Popen(c, start_new_session=True)",
        "os.killpg(p, 9)",
        "os.setsid()",
    ]
    for src in flagged:
        assert _group_management_sites(ast.parse(src)), src
    assert not _group_management_sites(ast.parse("start_new_session = True"))
    for call in sorted(_DIRECT_CALLS):
        assert _direct_subprocess_calls(ast.parse(f"subprocess.{call}(['x'])")), call
    assert not _direct_subprocess_calls(ast.parse("run_bounded(['x'], timeout=1)"))


# -- the supervisor's own contract ------------------------------------------

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); it is reaped by init shortly.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _wait_dead(pid: int, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


class _Boom(Exception):
    pass


@posix_only
@pytest.mark.parametrize(
    "exc",
    [_Boom("reader failed"), KeyboardInterrupt(), subprocess.TimeoutExpired("x", 1)],
    ids=["error", "interrupt", "timeout"],
)
def test_exception_tears_down_the_whole_group(
    tmp_path: Path, exc: BaseException
) -> None:
    pidfile = tmp_path / "grandchild.pid"
    # The direct child backgrounds a grandchild that ignores SIGTERM, then
    # waits: only a group-wide SIGKILL reaches the grandchild.
    script = f"(trap '' TERM; exec sleep 300) & echo $! > {pidfile}; wait"
    with pytest.raises(type(exc)):
        with deadline.supervised_popen(["sh", "-c", script]) as proc:
            end = time.monotonic() + 10
            while not (pidfile.exists() and pidfile.read_text().strip()):
                assert time.monotonic() < end, "grandchild never started"
                time.sleep(0.02)
            assert proc.pid in deadline._active_pgroups
            raise exc
    grandchild = int(pidfile.read_text())
    assert _wait_dead(grandchild), "grandchild survived the supervisor"
    assert proc.returncode is not None
    assert proc.pid not in deadline._active_pgroups


@posix_only
def test_normal_exit_unregisters_and_leaves_the_result(tmp_path: Path) -> None:
    with deadline.supervised_popen(
        ["sh", "-c", "echo hi"], stdout=subprocess.PIPE, text=True
    ) as proc:
        assert proc.pid in deadline._active_pgroups
        out, _ = proc.communicate(timeout=10)
    assert out == "hi\n"
    assert proc.returncode == 0
    assert proc.pid not in deadline._active_pgroups


@posix_only
def test_run_bounded_passes_env(tmp_path: Path) -> None:
    result = deadline.run_bounded(
        ["sh", "-c", 'printf %s "$ABICHECK_SUPERVISOR_PROBE"'],
        timeout=10,
        capture_output=True,
        text=True,
        env={"ABICHECK_SUPERVISOR_PROBE": "seen", "PATH": os.environ.get("PATH", "")},
    )
    assert result.stdout == "seen"
