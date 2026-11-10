"""The bug-fix contract tests judge their own inputs, not the caller's PR.

``verify.py``'s ``bugfix-test-contract`` step and CI export the PR's title,
range and body file; the unit tests of that gate run in the same environment
(``unit-pr``). Before ``_no_inherited_gate_env`` cleared the whole
``BUGFIX_CONTRACT_*`` namespace, a ``feat:`` title or an exported base/head
made 13 of them judge the real PR.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._bugfix_contract_gate import (  # noqa: F401
    GATE_ENV_PREFIX,
    GATE_ENV_VARS,
    _no_inherited_gate_env,
    clear_gate_env,
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


def test_the_gate_reads_only_names_under_the_cleared_prefix() -> None:
    assert set(GATE_ENV_VARS) == _READ_BY_THE_GATE
    assert all(name.startswith(GATE_ENV_PREFIX) for name in _READ_BY_THE_GATE)


@pytest.mark.parametrize(
    "name",
    [*sorted(_READ_BY_THE_GATE), "BUGFIX_CONTRACT_FUTURE_KNOB"],
)
def test_every_name_in_the_namespace_is_cleared(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # A name the gate might read later (helper module, built dynamically) is
    # cleared too; an unrelated variable is left alone.
    monkeypatch.setenv(name, "inherited")
    monkeypatch.setenv("BUGFIX_CONTRACTX", "not in the namespace")
    clear_gate_env(monkeypatch)
    assert name not in os.environ
    assert os.environ["BUGFIX_CONTRACTX"] == "not in the namespace"


def test_the_contract_tests_pass_under_a_callers_exported_pr() -> None:
    # The public path: the two contract test modules, run the way unit-pr runs
    # them, with every variable a caller exports. One subprocess, without the
    # parent's coverage/pytest plumbing.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("COV_CORE_", "PYTEST_", GATE_ENV_PREFIX))
    }
    env.update(
        PYTHONPATH=str(_REPO),
        BUGFIX_CONTRACT_TITLE="feat: not a fix",
        BUGFIX_CONTRACT_BASE="HEAD",
        BUGFIX_CONTRACT_HEAD="HEAD",
        BUGFIX_CONTRACT_BODY_FILE=os.devnull,
    )
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
