# SPDX-License-Identifier: Apache-2.0
"""Surviving the *test temp tree* vanishing in the middle of a shelled-out step.

Every harness here follows the same three-beat shape: create an environment
file under the test's own ``tmp_path``, run a real ``bash`` step with
``$GITHUB_OUTPUT`` (or a sibling) pointing at it, then read back what the step
wrote.  Between beat one and beat three the file can simply cease to exist:

    tests/_action_run_sh_harness.py:163: in _run_action
        for line in out.read_text(encoding="utf-8").splitlines():
    E   FileNotFoundError: [Errno 2] No such file or directory:
        '/tmp/pytest-of-runner/pytest-0/popen-gw0/test_the_diagnostic_names_the_1/github_output'

That is not the step misbehaving.  ``action/run.sh`` only ever *appends* to
``$GITHUB_OUTPUT`` (``{ ... } >> "$GITHUB_OUTPUT"``), so a step that reached
its output block recreates the file rather than removing it, and every ``rm``
in it names a path it created itself under its own ``mktemp``.  What is left is
the environment removing part of pytest's temp tree while the step ran -- the
same class ``tests/conftest.py``'s ``_snapshot_cache_bucket`` already heals for
its own bucket, and for the same reason it is stated there over *what was
removed* rather than over one deleter: pytest's retention policy, a tmp reaper,
a sandbox cleanup and a stray ``rmtree`` all arrive here identically.

Recovery, not tolerance, and only as far as this module can honestly reach.
A vanished environment *file* means the step produced no answer, so there is
nothing to weaken by recreating that file and running the step again.  A
vanished *directory* is a different event and must not be treated as the same
one: the caller's own fixtures live in that tree too -- the stub ``abicheck``
on ``$PATH``, the input libraries, the payload blobs, a workflow workspace's
seeded files -- and this module cannot rebuild any of them.  Re-running into a
freshly-created empty directory would hand the caller a confident *wrong*
answer (an Action ``ERROR``/127 from a stub that is no longer there) in place
of a crash, which is worse than the flake it set out to fix (Codex review,
PR #1292).

So the rule is: retry while the loss is confined to what this module owns, and
fail loudly the moment it is not.  Either way the failure carries the state of
the whole tree, so the next occurrence arrives diagnosed rather than as one
more bare ``FileNotFoundError``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

#: How many times a step whose output file vanished is run again. One retry is
#: the whole budget on purpose: a second loss is no longer plausibly a passing
#: reaper, and turning this into "keep trying" would convert a real, repeatable
#: deletion by the step under test into an infinite quiet loop.
ATTEMPTS = 2


def describe_tree(env_file: Path) -> str:
    """Which part of the tree above *env_file* is missing, outermost first.

    The answer is the diagnostic: "the file alone is gone" points at the step,
    while "its directory (or the worker basetemp) is gone too" points at the
    environment -- and only the first is recoverable here. A bare
    ``FileNotFoundError`` cannot tell those apart, which is why the observed
    failures could not be attributed from CI logs at all.
    """
    levels = [*reversed(env_file.parents), env_file]
    return ", ".join(
        f"{level}: {'present' if level.exists() else 'MISSING'}" for level in levels
    )


def _fixtures_intact(env_files: Sequence[Path]) -> bool:
    """Whether every directory the caller populated is still there.

    Deliberately *not* "can I make it exist again": `mkdir` would succeed and
    produce an empty directory, which is exactly the unfaithful retry this
    guard exists to refuse.
    """
    return all(env_file.parent.is_dir() for env_file in env_files)


def _lost(env_files: Sequence[Path], missing: Path) -> AssertionError:
    return AssertionError(
        f"{missing} is gone after running the step that writes it, and its "
        "directory went with it -- so the fixtures the caller put in that same "
        "tree (the stub on $PATH, input libraries, seeded workspace files) are "
        "gone too. Re-running the step now would exercise an empty directory "
        "and report a confident wrong answer, so it is not retried. Requested "
        f"files: {', '.join(str(f) for f in env_files)}. Tree state, outermost "
        f"path first:\n  {describe_tree(missing)}"
    )


def run_writing_env_files(
    env_files: Sequence[Path], run: Callable[[], T], *, retry: bool = False
) -> tuple[T, list[bytes]]:
    """Run *run* with every path in *env_files* freshly created; return their bytes.

    Stated over *all* the files the caller reads back, not just the one this was
    reported against (CodeRabbit, PR #1292): the shell harness reads
    ``$GITHUB_OUTPUT`` **and** ``$GITHUB_STEP_SUMMARY``, and guarding only the
    first left the second's own read outside the retry boundary -- the original
    bug, moved one file to the right. Whatever removed one of these removed the
    others beside it, so they are recovered together or not at all.

    ``retry`` is the caller's assertion that running *run* twice is
    indistinguishable from running it once -- that every sink the step writes
    is one of *env_files* (reset here between attempts) or is rewritten rather
    than appended to. It defaults to ``False`` because that is *not* generally
    true and the helper cannot check it: `run_step`'s callers may pass their
    own `$GITHUB_STEP_SUMMARY` through the step's env and read it back
    themselves, and the workflow steps append to it -- so a recovered
    `$GITHUB_OUTPUT` loss would hand that caller a summary containing its entry
    twice, an effect no single run of the step produces (Codex review,
    PR #1292, against `tests/test_mutation_workflow_execution.py`). Without
    ``retry`` the loss is still detected and attributed; it is simply reported
    rather than papered over with a second run whose result would be a
    fabrication.
    """
    losses: list[str] = []
    for _ in range(ATTEMPTS if retry else 1):
        if not _fixtures_intact(env_files):
            raise _lost(env_files, next(f for f in env_files if not f.parent.is_dir()))
        for env_file in env_files:
            env_file.write_bytes(b"")
        result = run()
        try:
            return result, [env_file.read_bytes() for env_file in env_files]
        except FileNotFoundError as exc:
            # The tree of the file that actually went missing, not of the first
            # one: naming a path that is sitting right there would send the
            # next reader after the wrong deleter.
            missing = Path(exc.filename or env_files[0])
            if not missing.parent.is_dir():
                raise _lost(env_files, missing) from exc
            losses.append(describe_tree(missing))
    attempts = (
        f"any of {ATTEMPTS} attempts at running the step that writes them"
        if retry
        else "running the step that writes them (not retried: this caller has "
        "not declared the step idempotent)"
    )
    raise AssertionError(
        f"{', '.join(str(f) for f in env_files)} did not all survive "
        f"{attempts}, so the step's own output was never observable. Tree "
        "state after each attempt, outermost path first:\n  " + "\n  ".join(losses)
    )


def run_writing_env_file(
    env_file: Path, run: Callable[[], T], *, retry: bool = False
) -> tuple[T, bytes]:
    """The single-file case of `run_writing_env_files`."""
    result, payloads = run_writing_env_files([env_file], run, retry=retry)
    return result, payloads[0]
