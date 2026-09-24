# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``conftest._keep_mutmut_stats_out_of_child_processes``: this process keeps
mutmut's ``stats`` phase, a real child process does not inherit it."""

from __future__ import annotations

import subprocess
import sys

import conftest
import pytest

trampoline = pytest.importorskip("mutmut.mutation.trampoline")


@pytest.fixture
def _restore(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    saved = trampoline._mutant_under_test
    yield monkeypatch
    trampoline._mutant_under_test = saved


@pytest.mark.parametrize("phase", ["stats", "some.module.x_f__mutmut_1", "fail", ""])
def test_only_the_stats_phase_is_withheld_from_children(_restore, phase: str) -> None:  # type: ignore[no-untyped-def]
    monkeypatch = _restore
    trampoline._mutant_under_test = None
    if phase:
        monkeypatch.setenv("MUTANT_UNDER_TEST", phase)
    else:
        monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    conftest._keep_mutmut_stats_out_of_child_processes()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('MUTANT_UNDER_TEST', ''))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert trampoline.get_mutant_under_test() == phase  # this process: unchanged
    assert child == ("" if phase == "stats" else phase)  # only stats withheld
