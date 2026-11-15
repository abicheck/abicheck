"""The no-growth ratchet on ``abiforge/known.py`` (plan §7.2, WS-7 PR 2).

``check_known_no_growth`` in ``scripts/check_ai_readiness.py`` compares the
multiset of ``Known`` entry keys in ``abiforge/known.py`` with the committed
``ABIFORGE_KNOWN_BASELINE``. The gate must fail when an entry is added, and when
an entry is removed but the baseline still records it; it passes only when the
two multisets are equal. The cases below state each outcome directly, so the
oracle is the rule in the brief, not the gate's own helper.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location(
        "check_ai_readiness_known_gate", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# A committed baseline of three keys, one of them recorded twice.
BASELINE = ["k1", "k2", "k2"]


def test_unchanged_known_passes(gate):
    assert gate.check_known_no_growth(["k1", "k2", "k2"], BASELINE) == []


def test_added_entry_fails(gate):
    problems = gate.check_known_no_growth(["k1", "k2", "k2", "k3"], BASELINE)
    assert len(problems) == 1
    assert "k3" in problems[0]
    assert "does not record" in problems[0]


def test_added_second_copy_of_a_recorded_key_fails(gate):
    # Multiplicity counts: a third ``k2`` is growth even though ``k2`` is recorded.
    problems = gate.check_known_no_growth(["k1", "k2", "k2", "k2"], BASELINE)
    assert len(problems) == 1
    assert "k2" in problems[0]


def test_removed_entry_without_shrinking_baseline_fails(gate):
    problems = gate.check_known_no_growth(["k1", "k2"], BASELINE)
    assert len(problems) == 1
    assert "k2" in problems[0]
    assert "remove it from the baseline" in problems[0]


def test_removed_entry_and_shrunk_baseline_passes(gate):
    assert gate.check_known_no_growth(["k1", "k2"], ["k1", "k2"]) == []


def test_removing_one_of_two_copies_needs_one_baseline_removal(gate):
    # Two recorded ``k2`` entries, one removed: exactly one baseline line goes.
    assert gate.check_known_no_growth(["k1", "k2"], ["k1", "k2"]) == []
    problems = gate.check_known_no_growth(["k1", "k2"], ["k1", "k2", "k2"])
    assert len(problems) == 1


def test_real_known_py_matches_the_committed_baseline(gate):
    """The shipped state passes, and the baseline records every entry exactly once.

    The count oracle is ``len(KNOWN)``: a baseline that dropped an entry without
    the entry being removed from ``known.py`` fails here as well as in the gate.
    """
    from abiforge.known import KNOWN

    assert len(gate.ABIFORGE_KNOWN_BASELINE) == len(KNOWN)
    current = [gate.known_entry_key(entry) for entry in KNOWN]
    assert gate.check_known_no_growth(current, gate.ABIFORGE_KNOWN_BASELINE) == []
