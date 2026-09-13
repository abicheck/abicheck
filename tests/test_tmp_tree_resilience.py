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
from _tmp_tree_resilience import ATTEMPTS, describe_tree, run_writing_env_file

#: Every part of the tree above (and including) the environment file whose
#: removal has to be survivable. Named by how far up the deletion reached,
#: because that is exactly what distinguishes the plausible deleters.
REMOVALS = ("the file alone", "its directory", "the whole tree above it")


def _remove(env_file: Path, how: str) -> None:
    if how == "the file alone":
        env_file.unlink()
    elif how == "its directory":
        shutil.rmtree(env_file.parent)
    else:
        shutil.rmtree(env_file.parent.parent)


class TestAStepWhoseOutputVanishedIsRunAgain:
    @pytest.mark.parametrize("how", REMOVALS)
    def test_a_single_loss_is_recovered_whatever_was_removed(
        self, tmp_path: Path, how: str
    ) -> None:
        env_file = tmp_path / "tree" / "workspace" / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            if len(calls) == 1:
                _remove(env_file, how)
                return "lost"
            env_file.write_bytes(b"answer=second\n")
            return "kept"

        result, payload = run_writing_env_file(env_file, run)

        assert result == "kept", (
            "the surviving attempt's own result is the one returned"
        )
        assert payload == b"answer=second\n"
        assert len(calls) == 2, "the step must actually have been run again"

    @pytest.mark.parametrize("how", REMOVALS)
    def test_a_step_that_loses_it_every_time_fails_with_the_tree_state(
        self, tmp_path: Path, how: str
    ) -> None:
        env_file = tmp_path / "tree" / "workspace" / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            _remove(env_file, how)
            return "lost"

        with pytest.raises(AssertionError) as excinfo:
            run_writing_env_file(env_file, run)

        assert len(calls) == ATTEMPTS, "the budget is spent, and only once"
        message = str(excinfo.value)
        assert str(env_file) in message, "the diagnostic must name the file"
        assert message.count("MISSING") >= ATTEMPTS, (
            "each attempt's tree state must be attached, or the next occurrence "
            f"is as undiagnosable as the bare FileNotFoundError was: {message}"
        )

    def test_a_step_that_never_loses_it_runs_exactly_once(self, tmp_path: Path) -> None:
        """Vacuity guard: recovery must not become "run everything twice"."""

        env_file = tmp_path / "tree" / "workspace" / "env_file"
        calls: list[int] = []

        def run() -> str:
            calls.append(1)
            env_file.write_bytes(b"answer=first\n")
            return "kept"

        result, payload = run_writing_env_file(env_file, run)
        assert (result, payload, calls) == ("kept", b"answer=first\n", [1])

    def test_each_attempt_starts_from_an_empty_file(self, tmp_path: Path) -> None:
        """A retry must not let the lost attempt's leftovers into the answer."""

        env_file = tmp_path / "tree" / "workspace" / "env_file"
        seen: list[bytes] = []

        def run() -> str:
            seen.append(env_file.read_bytes())
            env_file.write_bytes(b"answer=%d\n" % len(seen))
            if len(seen) == 1:
                env_file.unlink()
            return "done"

        _, payload = run_writing_env_file(env_file, run)
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
