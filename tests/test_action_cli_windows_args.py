"""The Action CLI receives its arguments verbatim on Windows.

On Windows, Click globs every argument and expands ``~`` and ``$VAR`` in it
unless the entry point passes ``windows_expand_args=False``. The Action's
scripts call ``python -m abicheck.frontends.action.cli`` with values taken
verbatim from workflow inputs, so ``--paths include/**`` reached the command
as a list of files and ``base-equivalent-paths`` failed on windows-latest
("Got unexpected extra argument (include\\f0.txt)").

The child interpreter imports the CLI, then sets ``os.name = "nt"`` (the
check Click makes at call time) and runs the module's real ``__main__``
block. The negative control calls the group with Click's default and must see
the expansion, so the harness is known to reproduce the Windows behaviour.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_RUN_MAIN = textwrap.dedent(
    """
    import os, runpy, sys, warnings
    warnings.simplefilter("ignore")
    import abicheck.frontends.action.cli as cli
    os.name = "nt"
    sys.argv = ["cli", *sys.argv[1:]]
    if os.environ.get("CLICK_DEFAULT") == "1":
        cli.action_cli()
    else:
        runpy.run_module("abicheck.frontends.action.cli", run_name="__main__")
    """
)


def _pathspecs(
    tmp_path: Path, value: str, *, click_default: bool = False
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "include").mkdir(exist_ok=True)
    for name in ("include/f0.h", "include/f1.h", "a.h", "b.h"):
        (tmp_path / name).write_text("", encoding="utf-8")
    # Inherit the environment: a Windows child without SYSTEMROOT cannot even
    # initialise Winsock (asyncio's import fails with WinError 10106).
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(_REPO)}
    env.pop("CLICK_DEFAULT", None)
    if click_default:
        env["CLICK_DEFAULT"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _RUN_MAIN, "equivalent-pathspecs", "--paths", value],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("value", ["include/**", "*.h", "include/*.h"])
def test_a_glob_pattern_reaches_the_command_verbatim(
    tmp_path: Path, value: str
) -> None:
    res = _pathspecs(tmp_path, value)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split("\0")[:-1] == [f":(glob){value}"]


@pytest.mark.parametrize("value", ["include/**", "*.h"])
def test_control_click_default_expands_the_pattern(tmp_path: Path, value: str) -> None:
    res = _pathspecs(tmp_path, value, click_default=True)
    assert res.returncode == 2
    assert "unexpected extra argument" in res.stderr
