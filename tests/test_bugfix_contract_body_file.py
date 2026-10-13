"""Bug-fix test contract: how an absent or empty PR body is judged."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._bugfix_contract_gate import (  # noqa: F401
    COMPLETE_BODY,
    _content_diff,
    _no_inherited_body_file,
    gate,
)


class TestEmptyPrBody:
    """An empty description must not be treated like a local run.

    CI always passes `--body-file`, so collapsing "flag absent" and "file
    empty" into one branch let a contributor clear the PR description and
    bypass every declared requirement while still passing (Codex review).
    """

    def test_an_empty_body_file_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            gate,
            "changed_files",
            lambda b, h: [("M", "abicheck/x.py"), ("A", "tests/test_x.py")],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(
            gate, "unified_diff", lambda b, h: _content_diff("tests/test_x.py")
        )
        empty = tmp_path / "body.md"
        empty.write_text("   \n\n", encoding="utf-8")
        rc = gate.main(["--base", "A", "--head", "B", "--body-file", str(empty)])
        assert rc == 1
        assert "PR description is empty" in capsys.readouterr().out

    def test_an_unresolvable_local_base_is_partial_not_a_pass(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No diff means not even the structural half ran.

        Returning 0 there let `verify.py` record the step as passed and the pr
        profile as complete — the same over-claim as a missing PR body, from
        an input missing for a different reason (Codex review).
        """

        def _boom(base: str, head: str) -> list[tuple[str, str]]:
            raise subprocess.CalledProcessError(128, ["git", "diff"])

        monkeypatch.setattr(gate, "changed_files", _boom)
        # A distinct code from the missing-body case: nothing ran here, so
        # reporting that reason would send a reader to the wrong fix.
        assert gate.main([]) == gate.EXIT_NO_DIFF
        assert "nothing was checked" in capsys.readouterr().out

    def test_an_unresolvable_ref_named_by_ci_is_still_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control: when CI names the refs, an unresolvable one is a
        real failure, not a partial run — a gate that cannot run in CI has
        nothing to be partial about."""

        def _boom(base: str, head: str) -> list[tuple[str, str]]:
            raise subprocess.CalledProcessError(128, ["git", "diff"])

        monkeypatch.setattr(gate, "changed_files", _boom)
        assert gate.main(["--base", "A", "--head", "B"]) == 1

    def test_a_structural_failure_outranks_the_partial_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A real finding must not be laundered into "partial".

        Without a body file the run is partial, but a fix that changes shipped
        code with no test is a genuine failure the structural half found on
        its own — it exits 1, which no caller maps to a skip.
        """
        monkeypatch.setattr(
            gate, "changed_files", lambda b, h: [("M", "abicheck/x.py")]
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(gate, "unified_diff", lambda b, h: "")
        assert gate.main(["--base", "A", "--head", "B"]) == 1
        assert "changes shipped code but no test" in capsys.readouterr().out

    def test_an_absent_body_file_is_structural_only(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Negative control: the local path must still work."""
        monkeypatch.setattr(
            gate,
            "changed_files",
            lambda b, h: [("M", "abicheck/x.py"), ("A", "tests/test_x.py")],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(
            gate, "unified_diff", lambda b, h: _content_diff("tests/test_x.py")
        )
        rc = gate.main(["--base", "A", "--head", "B"])
        # Exit 2, not 0: the structural half ran and passed, the declared half
        # could not run at all. `verify.py` maps this to a skip so a local
        # `--profile pr` cannot claim parity over half a gate (Codex review).
        assert rc == gate.EXIT_STRUCTURAL_ONLY
        out = capsys.readouterr().out
        assert "no --body-file given" in out
        assert "the declared half did not run" in out

    def test_a_populated_body_is_still_evaluated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            gate,
            "changed_files",
            lambda b, h: [("M", "abicheck/cli_deps.py"), ("A", "tests/test_x.py")],
        )
        monkeypatch.setattr(gate, "commit_subjects", lambda b, h: ["fix: thing"])
        monkeypatch.setattr(
            gate, "unified_diff", lambda b, h: _content_diff("tests/test_x.py")
        )
        body = tmp_path / "body.md"
        body.write_text(COMPLETE_BODY, encoding="utf-8")
        assert gate.main(["--base", "A", "--head", "B", "--body-file", str(body)]) == 0
