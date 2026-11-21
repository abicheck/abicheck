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
    "platforms": (("linux", "elf"),),
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
    assert problems[0].startswith("widening, needs review")
    assert "e#0" in problems[0]
    # The clang pairs are new: four configurations each (2 flags x 2 evidence).
    assert "c++20|clang19 +4" in problems[0]


def test_narrowed_entry_with_stale_record_fails_with_regenerate_message(gate):
    recorded = _record(GCC_ONLY_C20)
    current = _record(GCC_C20_O2)
    (problem,) = gate.known_when_widening(current, recorded)
    assert problem.startswith("narrowed entry e#0")
    assert "c++20|gcc14 -2" in problem
    assert "regenerate the record with --write-known-when in this PR" in problem


def test_narrowed_entry_with_regenerated_record_passes(gate):
    current = _record(GCC_C20_O2)
    assert gate.known_when_widening(current, current) == []


def test_record_entry_removed_from_known_fails(gate):
    recorded = _record(GCC_ONLY_C20)
    (problem,) = gate.known_when_widening({}, recorded)
    assert "e#0" in problem
    assert "regenerate the record with --write-known-when" in problem


def test_widening_is_counted_per_configuration_not_per_pair(gate):
    # Recorded: O2 only (two evidence levels = two bits on gcc14). Widened: all flag
    # sets, so the O0 configurations are the two new bits on the same pair.
    recorded = _record(GCC_C20_O2)["e#0"]
    current = _record(GCC_ONLY_C20)["e#0"]
    assert widened_pairs(current, recorded) == {"c++20|gcc14": 2}
    (problem,) = gate.known_when_widening(_record(GCC_ONLY_C20), _record(GCC_C20_O2))
    assert problem.startswith("widening, needs review")
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


def test_widening_a_real_gcc_only_entry_is_caught(gate):
    """Dropping its gcc restriction makes a shipped gcc-only entry cover clang."""
    import dataclasses

    from abiforge.known import KNOWN

    entry = next(e for e in KNOWN if e.op == "gnu_unique_static_added")
    widened = dataclasses.replace(
        entry, when=lambda c: c.flags.opt == "O2" and c.evidence == "dwarf"
    )
    current = known_record(
        [widened if e is entry else e for e in KNOWN], gate.known_entry_key
    )
    problems = gate.known_when_widening(current, load_baseline(BASELINE_PATH))
    assert len(problems) == 1
    assert problems[0].startswith("widening, needs review")
    assert "gnu_unique_static_added" in problems[0]
    assert "clang" in problems[0]


@pytest.mark.parametrize("host", ["linux", "darwin", "win32"])
def test_record_is_identical_on_every_host(gate, monkeypatch, host):
    """The record must not depend on the runner. A ``when`` reads ``objfmt``, which
    follows the host, so the probe grid pins every platform explicitly; faking
    ``sys.platform`` as each host must give the committed record unchanged."""
    import sys

    from abiforge.known import KNOWN
    from abiforge.known_coverage import known_record

    reference = known_record(KNOWN, gate.known_entry_key)
    monkeypatch.setattr(sys, "platform", host)
    assert known_record(KNOWN, gate.known_entry_key) == reference
    assert reference == load_baseline(BASELINE_PATH)


def test_every_probe_config_pins_its_platform(gate, monkeypatch):
    """Spy on the grid's ``Config`` construction: each probe configuration names its
    platform, so none inherits the runner's (a host-free record needs no default)."""
    from abiforge import known_coverage
    from abiforge.model.config import Config

    seen: list[bool] = []

    def spy(**kwargs):
        seen.append("platform" in kwargs)
        return Config(**kwargs)

    monkeypatch.setattr(known_coverage, "Config", spy)
    known_coverage.covered_masks(lambda c: True)
    assert seen and all(seen)


def test_computing_the_record_runs_no_toolchain_probe(gate, monkeypatch):
    """A ``when`` that probes the installed toolchain (``uses_libstdcxx`` runs the
    driver) makes the record depend on the runner. Computing it must start no
    process and look up no executable."""
    import shutil
    import subprocess

    from abiforge.known import KNOWN
    from abiforge.known_coverage import known_record

    def forbidden(*args, **kwargs):
        raise AssertionError(f"record computation probed the host: {args!r}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(shutil, "which", forbidden)
    assert known_record(KNOWN, gate.known_entry_key)


def test_dropping_the_producer_from_a_pack1_entry_is_widening_on_the_real_grid(gate):
    """The probe grid covers -fpack-struct=1: a pack-1 entry records real cells,
    and lifting its gcc restriction (keeping pack 1) is caught as widening."""
    gcc_pack1 = lambda c: c.producer.family == "gcc" and c.flags.pack == 1  # noqa: E731
    any_pack1 = lambda c: c.flags.pack == 1  # noqa: E731
    recorded = known_record([Synthetic("e", gcc_pack1)], _key)
    assert recorded["e#0"], "the probe grid never builds -fpack-struct=1"
    current = known_record([Synthetic("e", any_pack1)], _key)
    (problem,) = gate.known_when_widening(current, recorded)
    assert problem.startswith("widening, needs review")


def test_a_stripped_pack1_entry_records_cells_and_its_widening_is_caught(gate):
    """Stripped {B,H} is its own lane of the grid: an entry gated on it records
    real cells, and dropping the stripped condition is caught as widening."""
    stripped_pack1 = lambda c: c.stripped and c.flags.pack == 1  # noqa: E731
    any_headers_pack1 = lambda c: c.evidence == "headers" and c.flags.pack == 1  # noqa: E731
    recorded = known_record([Synthetic("e", stripped_pack1)], _key)
    assert recorded["e#0"], "the probe grid never builds a stripped {B,H} cell"
    current = known_record([Synthetic("e", any_headers_pack1)], _key)
    (problem,) = gate.known_when_widening(current, recorded)
    assert problem.startswith("widening, needs review")


def test_a_header_backend_entry_records_cells(gate):
    clang_headers = lambda c: c.evidence == "headers" and c.header_backend == "clang"  # noqa: E731
    assert known_record([Synthetic("e", clang_headers)], _key)["e#0"]
