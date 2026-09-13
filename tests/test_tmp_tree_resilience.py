# SPDX-License-Identifier: Apache-2.0
"""The contract of ``tests/_tmp_tree_resilience.run_writing_env_file``.

Stated over *which part of the tree was removed* and *how often*, not against
the one observed deletion -- the same reason
``TestTheAllocatorSurvivesItsDirectoryTreeVanishing`` states the sibling
contract that way: the deleter is not the bug, and a test pinned to one of
them would leave the others open.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from _tmp_tree_resilience import (
    ATTEMPTS,
    describe_tree,
    run_writing_env_file,
    run_writing_env_files,
)

#: How far up the tree a deletion reached. The distinction is the whole
#: contract, not a taxonomy: only "the file alone" leaves the caller's own
#: fixtures (a stub on $PATH, input libraries, a seeded workspace) in place, so
#: only it may be retried. The two deeper ones must fail loudly instead of
#: re-running the step against a directory this module would have fabricated.
RECOVERABLE = "the file alone"
UNFAITHFUL = ("its directory", "the whole tree above it")
REMOVALS = (RECOVERABLE, *UNFAITHFUL)


def _workspace(tmp_path: Path) -> Path:
    """The caller-owned directory an environment file lives in.

    Created by the *caller*, never by the helper under test: a real harness's
    fixtures live here, and fabricating it is exactly what
    `TestARetryThatCouldNotBeFaithfulIsRefused` forbids.
    """
    workspace = tmp_path / "tree" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _remove(env_file: Path, how: str) -> None:
    if how == "the file alone":
        env_file.unlink()
    elif how == "its directory":
        shutil.rmtree(env_file.parent)
    else:
        shutil.rmtree(env_file.parent.parent)


class TestAStepWhoseOutputVanishedIsRunAgain:
    @pytest.mark.parametrize("how", [RECOVERABLE])
    def test_a_single_loss_is_recovered_when_the_fixtures_survived(
        self, tmp_path: Path, how: str
    ) -> None:
        env_file = _workspace(tmp_path) / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            if len(calls) == 1:
                _remove(env_file, how)
                return "lost"
            env_file.write_bytes(b"answer=second\n")
            return "kept"

        result, payload = run_writing_env_file(env_file, run, retry=True)

        assert result == "kept", (
            "the surviving attempt's own result is the one returned"
        )
        assert payload == b"answer=second\n"
        assert len(calls) == 2, "the step must actually have been run again"

    @pytest.mark.parametrize("how", [RECOVERABLE])
    def test_a_step_that_loses_it_every_time_fails_with_the_tree_state(
        self, tmp_path: Path, how: str
    ) -> None:
        env_file = _workspace(tmp_path) / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            _remove(env_file, how)
            return "lost"

        with pytest.raises(AssertionError) as excinfo:
            run_writing_env_file(env_file, run, retry=True)

        assert len(calls) == ATTEMPTS, "the budget is spent, and only once"
        message = str(excinfo.value)
        assert str(env_file) in message, "the diagnostic must name the file"
        assert message.count("MISSING") >= ATTEMPTS, (
            "each attempt's tree state must be attached, or the next occurrence "
            f"is as undiagnosable as the bare FileNotFoundError was: {message}"
        )

    def test_a_step_that_never_loses_it_runs_exactly_once(self, tmp_path: Path) -> None:
        """Vacuity guard: recovery must not become "run everything twice"."""

        env_file = _workspace(tmp_path) / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            env_file.write_bytes(b"answer=first\n")
            return "kept"

        result, payload = run_writing_env_file(env_file, run)
        assert (result, payload, calls) == ("kept", b"answer=first\n", [1])

    def test_each_attempt_starts_from_an_empty_file(self, tmp_path: Path) -> None:
        """A retry must not let the lost attempt's leftovers into the answer."""

        env_file = _workspace(tmp_path) / "env_file"
        seen: list[bytes] = []

        def run() -> str:
            seen.append(env_file.read_bytes())
            env_file.write_bytes(b"answer=%d\n" % len(seen))
            if len(seen) == 1:
                env_file.unlink()
            return "done"

        _, payload = run_writing_env_file(env_file, run, retry=True)
        assert seen == [b"", b""], "the step is always handed a fresh, empty file"
        assert payload == b"answer=2\n"


class TestTheDiagnosticDistinguishesTheDeleters:
    """ "the file alone is gone" and "its directory is gone" are different bugs.

    The first points at the step under test, the second at the environment --
    and a bare ``FileNotFoundError`` says neither, which is why the CI
    occurrences this module exists for could not be attributed at all.
    """

    def test_a_missing_file_under_a_live_directory_reads_that_way(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / "workspace" / "env_file"
        env_file.parent.mkdir()
        described = describe_tree(env_file)
        assert described.endswith(f"{env_file}: MISSING")
        assert f"{env_file.parent}: present" in described

    def test_a_missing_directory_is_reported_above_the_file(
        self, tmp_path: Path
    ) -> None:
        env_file = tmp_path / "workspace" / "env_file"
        described = describe_tree(env_file)
        assert f"{env_file.parent}: MISSING" in described
        assert f"{tmp_path}: present" in described

    def test_a_fully_present_tree_reports_nothing_missing(self, tmp_path: Path) -> None:
        env_file = tmp_path / "workspace" / "env_file"
        env_file.parent.mkdir()
        env_file.write_bytes(b"")
        assert "MISSING" not in describe_tree(env_file)


class TestEveryRequiredFileIsGuarded:
    """Recovery is owed to *each* file the caller reads back, not the first one.

    The reported shape of this (CodeRabbit, PR #1292) was the shell harness
    guarding `$GITHUB_OUTPUT` while reading `$GITHUB_STEP_SUMMARY` afterwards:
    the summary's own `read_text` then sat outside the retry boundary, which is
    the original bug moved one file to the right. So the invariant is stated
    over *which* of the required files went missing -- parametrized by index,
    not pinned to the reported second file -- and over each one's payload
    arriving intact, since a helper that recovered but returned the files in
    the wrong order would satisfy a bare "it did not raise".
    """

    @pytest.mark.parametrize("victim", range(3))
    @pytest.mark.parametrize("how", [RECOVERABLE])
    def test_losing_any_one_of_them_re_runs_the_step(
        self, tmp_path: Path, victim: int, how: str
    ) -> None:
        files = [_workspace(tmp_path) / f"file{i}" for i in range(3)]
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            for index, path in enumerate(files):
                path.write_bytes(b"payload-%d\n" % index)
            if len(calls) == 1:
                _remove(files[victim], how)
            return "kept"

        result, payloads = run_writing_env_files(files, run, retry=True)

        assert len(calls) == 2, f"losing file{victim} must re-run the step"
        assert result == "kept"
        assert payloads == [b"payload-0\n", b"payload-1\n", b"payload-2\n"], (
            "each requested file's own bytes, in the order they were requested"
        )

    @pytest.mark.parametrize("victim", range(3))
    def test_the_diagnostic_names_the_file_that_went_missing(
        self, tmp_path: Path, victim: int
    ) -> None:
        """Not merely the first requested one, which is sitting right there."""

        files = [_workspace(tmp_path) / f"file{i}" for i in range(3)]

        def run() -> str:
            files[victim].unlink()
            return "lost"

        with pytest.raises(AssertionError) as excinfo:
            run_writing_env_files(files, run, retry=True)

        message = str(excinfo.value)
        assert f"{files[victim]}: MISSING" in message, message
        for survivor in (f for i, f in enumerate(files) if i != victim):
            assert f"{survivor}: MISSING" not in message, message

    def test_the_single_file_wrapper_is_the_one_file_case(self, tmp_path: Path) -> None:
        """Vacuity guard: the wrapper must delegate, not keep a second copy."""

        env_file = _workspace(tmp_path) / "env_file"

        def run() -> str:
            env_file.write_bytes(b"answer=only\n")
            return "kept"

        assert run_writing_env_file(env_file, run, retry=True) == (
            "kept",
            b"answer=only\n",
        )


class TestARetryThatCouldNotBeFaithfulIsRefused:
    """A deletion that reached the fixtures must fail, never silently re-run.

    Reported by Codex on PR #1292 and the sharper half of this module's
    contract. The caller's own fixtures -- the stub `abicheck` on `$PATH`, the
    input libraries, the payload blobs, a workspace's seeded files -- live in
    the same tree as the environment file. This module can recreate the file;
    it cannot recreate any of those. Re-running into a directory it had just
    fabricated would replace a crash with a confident *wrong* answer (an Action
    `ERROR`/127 from a stub that is no longer on disk), which is worse than the
    flake the retry exists to fix.

    Stated over both deeper removal depths and asserted on the *observable*
    consequence -- the step is not called a second time -- rather than on the
    message alone, since a helper that retried and then happened to fail would
    still produce a plausible-looking error.
    """

    @pytest.mark.parametrize("how", UNFAITHFUL)
    def test_the_step_is_not_run_again(self, tmp_path: Path, how: str) -> None:
        env_file = _workspace(tmp_path) / "env_file"
        fixture = env_file.parent / "stub-on-PATH"
        calls: list[bool] = []

        def run() -> str:
            calls.append(fixture.exists())
            _remove(env_file, how)
            return "lost"

        fixture.write_bytes(b"#!/bin/sh\n")

        with pytest.raises(AssertionError) as excinfo:
            run_writing_env_file(env_file, run, retry=True)

        assert calls == [True], (
            "the step ran once, with its fixtures; it must not be run a second "
            f"time against a fabricated empty directory (calls: {calls})"
        )
        assert not fixture.exists(), "the fixture really did go with the tree"
        message = str(excinfo.value)
        assert str(env_file) in message and "not retried" in message, message

    @pytest.mark.parametrize("how", UNFAITHFUL)
    def test_a_tree_already_gone_before_the_step_is_refused_too(
        self, tmp_path: Path, how: str
    ) -> None:
        """The same judgement on entry, not only after an attempt.

        A caller can reach this helper with its tree already reaped -- the loss
        does not have to happen during the step to have taken the fixtures.
        """

        env_file = _workspace(tmp_path) / "env_file"
        env_file.write_bytes(b"")
        _remove(env_file, how)
        calls: list[int] = []

        with pytest.raises(AssertionError, match="not retried"):
            run_writing_env_file(env_file, lambda: calls.append(1), retry=True)

        assert calls == [], "the step must never run without its fixtures"


class TestRetryingIsOptedIntoPerCaller:
    """Re-running a step is a claim about the step, so the caller must make it.

    `run_step` cannot: a caller may hand the workflow body its own
    `$GITHUB_STEP_SUMMARY` through `env` and read it back itself, and the body
    *appends* to it -- so a recovered `$GITHUB_OUTPUT` loss would return a
    summary holding that entry twice, an effect no single run produces (Codex
    review, PR #1292). The default is therefore "detect and report", never
    "run it again and hope the sinks did not notice".
    """

    def test_the_default_reports_the_loss_instead_of_re_running(
        self, tmp_path: Path
    ) -> None:
        env_file = _workspace(tmp_path) / "env_file"
        appended = _workspace(tmp_path) / "caller_owned_sink"
        appended.write_bytes(b"")
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            with appended.open("ab") as handle:  # the step appends, as they do
                handle.write(b"entry\n")
            env_file.unlink()
            return "lost"

        with pytest.raises(AssertionError) as excinfo:
            run_writing_env_file(env_file, run)

        assert calls == [1], "the step must run exactly once without an opt-in"
        assert appended.read_bytes() == b"entry\n", (
            "a second run would have doubled the caller's own sink, which is "
            "the fabricated result this default exists to refuse"
        )
        assert "not retried" in str(excinfo.value), excinfo.value

    def test_opting_in_is_what_enables_the_second_attempt(self, tmp_path: Path) -> None:
        """Vacuity guard: the flag must be the thing that changes the behaviour."""

        env_file = _workspace(tmp_path) / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            if len(calls) == 1:
                env_file.unlink()
            else:
                env_file.write_bytes(b"ok\n")
            return "kept"

        assert run_writing_env_file(env_file, run, retry=True) == ("kept", b"ok\n")
        assert calls == [1, 1]
