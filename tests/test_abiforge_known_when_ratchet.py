"""The ``when`` no-growth ratchet on ``abiforge/known.py`` (plan §7.2, WS-7).

``check_abiforge_known_no_growth`` keys each ``Known`` entry on its gap string,
so a widened ``when`` clause passes it silently. ``known_when_widening`` closes
that: it fails when an entry's ``when`` covers a probe-grid configuration the
committed record does not hold, and passes when the covered set is unchanged or
narrowed.

The oracle is hand counts over a small grid, written out in each test, not the
gate's own helper. The grid is two producer families (gcc 14 and clang 19), two
flag sets and two evidence levels, so each covered standard contributes four
configurations per producer.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from abiforge.known_coverage import (
    BASELINE_PATH,
    PROBE_TOOLCHAINS,
    known_record,
    load_baseline,
    widened_pairs,
)
from abiforge.model.config import STANDARD_BY_NAME, Flags

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ai_readiness.py"

GCC14 = next(t for t in PROBE_TOOLCHAINS if t.id == "gcc14")
CLANG19 = next(t for t in PROBE_TOOLCHAINS if t.id == "clang19")
STDS = (STANDARD_BY_NAME["c++17"], STANDARD_BY_NAME["c++20"])
FLAGS = (Flags(), Flags(opt="O2"))
EVIDENCE = ("binary", "headers")
GRID = {
    "stds": STDS,
    "toolchains": (GCC14, CLANG19),
    "flags": FLAGS,
    "evidence": EVIDENCE,
}


@dataclass(frozen=True)
class Synthetic:
    """A stand-in entry: the ratchet needs only a key and a ``when``."""

    name: str
    when: object


def _key(entry: Synthetic) -> str:
    return entry.name


def _record(when: object) -> dict[str, dict[str, str]]:
    return known_record([Synthetic("e", when)], _key, **GRID)


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("check_ai_readiness_when", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Covered on the grid: c++20 only (c++17 is not >= 2020). Each (producer, standard)
# pair holds four configurations: two flag sets x two evidence levels.
GCC_ONLY_C20 = lambda c: c.std.year >= 2020 and c.producer.family == "gcc"  # noqa: E731
ALL_C20 = lambda c: c.std.year >= 2020  # noqa: E731 -- widened: adds clang
GCC_C20_O2 = lambda c: (  # noqa: E731 -- narrowed: O2 only
    c.std.year >= 2020 and c.producer.family == "gcc" and c.flags.opt == "O2"
)


def test_unchanged_when_passes(gate):
    recorded = _record(GCC_ONLY_C20)
    current = _record(GCC_ONLY_C20)
    assert gate.known_when_widening(current, recorded) == []


def test_widened_when_fails(gate):
    recorded = _record(GCC_ONLY_C20)
    current = _record(ALL_C20)
    problems = gate.known_when_widening(current, recorded)
    assert len(problems) == 1
    assert "e#0" in problems[0]
    # The clang pairs are new: four configurations each (2 flags x 2 evidence).
    assert "c++20|clang19 +4" in problems[0]


def test_narrowed_when_passes(gate):
    recorded = _record(GCC_ONLY_C20)
    current = _record(GCC_C20_O2)
    assert gate.known_when_widening(current, recorded) == []


def test_widening_is_counted_per_configuration_not_per_pair(gate):
    # Recorded: O2 only (two evidence levels = two bits on gcc14). Widened: all flag
    # sets, so the O0 configurations are the two new bits on the same pair.
    recorded = _record(GCC_C20_O2)["e#0"]
    current = _record(GCC_ONLY_C20)["e#0"]
    assert widened_pairs(current, recorded) == {"c++20|gcc14": 2}
    (problem,) = gate.known_when_widening(_record(GCC_ONLY_C20), _record(GCC_C20_O2))
    assert "c++20|gcc14 +2" in problem


def test_entry_missing_from_the_record_is_all_widening(gate):
    (problem,) = gate.known_when_widening(_record(GCC_ONLY_C20), {})
    assert "e#0" in problem


def test_widened_pairs_reports_extra_configurations_by_hand(gate):
    # Hand count from the grid above: the two clang pairs each gain four bits.
    recorded = _record(GCC_ONLY_C20)["e#0"]
    current = _record(ALL_C20)["e#0"]
    assert widened_pairs(current, recorded) == {
        "c++20|clang19": 4,
    }


def test_real_record_matches_the_shipped_entries(gate):
    """The committed record is the one the shipped ``known.py`` computes.

    A changed ``when`` with no regenerated record is a widening the gate must
    report, so this test is the same comparison the gate makes.
    """
    from abiforge.known import KNOWN

    current = known_record(KNOWN, gate.known_entry_key)
    assert current == load_baseline(BASELINE_PATH)
    assert gate.known_when_widening(current, load_baseline(BASELINE_PATH)) == []


def test_widening_the_real_std_string_entry_is_caught(gate):
    """Dropping its gcc restriction makes the shipped std_string entry cover clang."""
    import dataclasses

    from abiforge.known import KNOWN

    entry = next(e for e in KNOWN if e.op == "std_string_abi_flipped")
    widened = dataclasses.replace(entry, when=lambda c: c.std.year >= 2020)
    current = known_record(
        [widened if e is entry else e for e in KNOWN], gate.known_entry_key
    )
    problems = gate.known_when_widening(current, load_baseline(BASELINE_PATH))
    assert len(problems) == 1
    assert "std_string_abi_flipped" in problems[0]
    assert "clang" in problems[0]
