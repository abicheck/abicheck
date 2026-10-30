"""``action/run.sh`` forwards the ``set:`` input as repeated ``--set`` (ADR-091 D4).

The Action does not validate ``set:`` keys: each non-blank, non-comment line
must reach the CLI verbatim and in order, and the CLI answers. A line that is
blank or a ``#`` comment names no override and must forward nothing. Dump and
other modes have no ``--set`` yet, so an effective ``set:`` there must fail the
step (``validate-inputs.sh``) instead of being dropped silently.

Driven through the real scripts against a fake ``abicheck`` on ``$PATH`` that
records its own argv one argument per line, so the assertions are about the
command line that actually runs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
RUN_SH = ACTION_DIR / "run.sh"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"


def _stub(tmp_path: Path) -> tuple[Path, Path]:
    """A fake ``abicheck`` that appends each argv token on its own line.

    Returns ``(fake_bin, captured)``. A ``---`` line closes each invocation.
    The stub writes a JSON report to every ``json=`` destination, as the
    compare path expects a real CLI to.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    captured = tmp_path / "captured_argv.txt"
    stub = fake_bin / "abicheck"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'for _a in "$@"; do printf \'%s\\n\' "$_a" >> "{captured}"; done\n'
        f"printf '%s\\n' --- >> \"{captured}\"\n"
        'for _a in "$@"; do\n'
        '  case "$_a" in\n'
        "    json=-) : ;;\n"
        '    json=*) printf \'%s\' \'{"verdict": "COMPATIBLE"}\' > "${_a#json=}" ;;\n'
        "  esac\n"
        "done\n"
        'echo \'{"verdict":"COMPATIBLE"}\'\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return fake_bin, captured


def _env(tmp_path: Path, fake_bin: Path, **inputs: str) -> dict[str, str]:
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    return {
        **base_env,
        "PATH": f"{fake_bin}{os.pathsep}{base_env.get('PATH', '')}",
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": str(old),
        "INPUT_NEW_LIBRARY": str(new),
        "INPUT_FORMAT": "markdown",
        "INPUT_ADD_JOB_SUMMARY": "false",
        "INPUT_PR_COMMENT": "false",
        "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
        **inputs,
    }


def _run_compare(tmp_path: Path, set_value: str) -> list[str]:
    """Run compare mode with ``set:`` = *set_value*; return the stub's argv."""
    require_bash()
    fake_bin, captured = _stub(tmp_path)
    env = _env(tmp_path, fake_bin, INPUT_SET=set_value)
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").splitlines()


def _set_pairs(argv: list[str]) -> list[str]:
    """The values that follow each ``--set`` token, in command-line order."""
    return [argv[i + 1] for i, tok in enumerate(argv[:-1]) if tok == "--set"]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestSetLinesForwardToTheCli:
    def test_each_line_becomes_its_own_set_in_order(self, tmp_path: Path) -> None:
        argv = _run_compare(
            tmp_path,
            "scope.show_redundant=true\nseverity.addition=error\nsuppression.strict=false\n",
        )
        assert _set_pairs(argv) == [
            "scope.show_redundant=true",
            "severity.addition=error",
            "suppression.strict=false",
        ]

    def test_value_is_forwarded_verbatim_including_spaces_and_flow_yaml(
        self, tmp_path: Path
    ) -> None:
        # A value is YAML for the CLI to read, so the Action must not split,
        # quote or reinterpret it: a flow list and a spaced value arrive whole.
        argv = _run_compare(tmp_path, "deployment=[a, b]\n")
        assert _set_pairs(argv) == ["deployment=[a, b]"]

    def test_comments_and_blank_lines_are_skipped_between_real_lines(
        self, tmp_path: Path
    ) -> None:
        argv = _run_compare(
            tmp_path,
            "# why the scope is widened\n\nscope.show_redundant=true\n   \n# next\n"
            "severity.addition=error\n",
        )
        assert _set_pairs(argv) == [
            "scope.show_redundant=true",
            "severity.addition=error",
        ]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBlankAndCommentOnlySetForwardsNothing:
    @pytest.mark.parametrize(
        "set_value",
        ["", "\n", "   \n\t\n", "# only a comment\n", "# one\n\n   # indented\n"],
    )
    def test_no_set_argument_is_passed(self, tmp_path: Path, set_value: str) -> None:
        argv = _run_compare(tmp_path, set_value)
        assert "--set" not in argv


@pytest.mark.skipif(not VALIDATE_SH.is_file(), reason="validate-inputs.sh not found")
class TestSetOnAModeWithoutSetFailsTheStep:
    def _validate(
        self, tmp_path: Path, mode: str, set_value: str
    ) -> subprocess.CompletedProcess[str]:
        require_bash()
        lib = tmp_path / "lib.so"
        lib.write_bytes(b"\x7fELF")
        env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
        env.update(
            {
                "INPUT_MODE": mode,
                "INPUT_NEW_LIBRARY": str(lib),
                "INPUT_SET": set_value,
                "GITHUB_OUTPUT": str(tmp_path / "gh_output"),
                "GITHUB_STEP_SUMMARY": str(tmp_path / "gh_summary"),
            }
        )
        if mode == "compare":
            env["INPUT_OLD_LIBRARY"] = str(lib)
        return subprocess.run(
            [bash_executable(), str(VALIDATE_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            check=False,
        )

    def test_dump_with_an_effective_set_line_fails_and_names_the_input(
        self, tmp_path: Path
    ) -> None:
        result = self._validate(tmp_path, "dump", "scope.show_redundant=true\n")
        assert result.returncode != 0
        assert "set: applies to mode: compare only" in result.stdout

    def test_dump_with_comment_only_set_is_accepted(self, tmp_path: Path) -> None:
        result = self._validate(tmp_path, "dump", "# nothing to apply\n\n")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "set: applies to mode" not in result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestRunShRefusesSetOnAnotherMode:
    def test_direct_run_sh_dump_with_effective_set_fails_before_forwarding(
        self, tmp_path: Path
    ) -> None:
        require_bash()
        fake_bin, captured = _stub(tmp_path)
        env = _env(
            tmp_path,
            fake_bin,
            INPUT_MODE="dump",
            INPUT_SET="scope.show_redundant=true\n",
        )
        result = subprocess.run(
            [bash_executable(), str(RUN_SH)],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            check=False,
        )
        assert result.returncode != 0
        assert "set: applies to mode: compare only" in result.stdout
        assert not captured.exists(), "the CLI must not run when set: is refused"
