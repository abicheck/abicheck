"""The bug-fix contract tests judge their own inputs, not the caller's PR.

``verify.py``'s ``bugfix-test-contract`` step and CI export the PR's title,
range and body file; the unit tests of that gate run in the same environment
(``unit-pr``). Before ``_no_inherited_gate_env`` cleared all four, a
``feat:`` title or an exported base/head made 13 of them judge the real PR.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._bugfix_contract_gate import (  # noqa: F401
    GATE_ENV_VARS,
    _no_inherited_gate_env,
)

_REPO = Path(__file__).resolve().parent.parent

# Written from scripts/check_bugfix_test_contract.py's argparse defaults, not
# from GATE_ENV_VARS' own scan.
_READ_BY_THE_GATE = {
    "BUGFIX_CONTRACT_BASE",
    "BUGFIX_CONTRACT_HEAD",
    "BUGFIX_CONTRACT_TITLE",
    "BUGFIX_CONTRACT_BODY_FILE",
}


def test_every_variable_the_gate_reads_is_cleared() -> None:
    assert set(GATE_ENV_VARS) == _READ_BY_THE_GATE
    assert not [name for name in GATE_ENV_VARS if name in os.environ]


@pytest.mark.parametrize(
    "exported",
    [
        {"BUGFIX_CONTRACT_TITLE": "feat: not a fix"},
        {"BUGFIX_CONTRACT_BASE": "HEAD", "BUGFIX_CONTRACT_HEAD": "HEAD"},
        {"BUGFIX_CONTRACT_BODY_FILE": os.devnull},
    ],
    ids=["title", "range", "body-file"],
)
def test_the_contract_tests_pass_under_a_callers_exported_pr(
    exported: dict[str, str],
) -> None:
    env = {**os.environ, **exported, "PYTHONPATH": str(_REPO)}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_bugfix_contract_body_file.py",
            "tests/test_bugfix_test_contract.py",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-n",
            "0",
        ],
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-3000:]
