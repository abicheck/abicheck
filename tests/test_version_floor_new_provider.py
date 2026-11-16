"""A version requirement is a floor: what a newly needed provider raises it to.

The oracle is the verneed table written out in each test (``versions_required``
per provider), never the implementation. For each shape the expected kinds are
stated from that table:

- a newly needed provider whose node exceeds the binary-wide floor raises it
  (``SYMBOL_VERSION_REQUIRED_ADDED`` and ``RUNTIME_FLOOR_RAISED`` from no
  requirement, or from the existing maximum);
- a newly needed provider whose nodes stay at or below the existing floor does
  not (``SYMBOL_VERSION_REQUIRED_ADDED_COMPAT``, no floor finding; the TBB shape);
- a newly needed provider with no versioned nodes adds nothing version-related;
- an existing provider whose floor rises reports the raise with both tags.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from tests.test_diff_platform_deep import _snap


def _kinds(
    old_vers: dict[str, list[str]], new_vers: dict[str, list[str]]
) -> set[ChangeKind]:
    r = compare(
        _snap(elf=ElfMetadata(versions_required=old_vers)),
        _snap(elf=ElfMetadata(versions_required=new_vers)),
    )
    return {c.kind for c in r.changes}


@pytest.mark.parametrize(
    ("old", "new", "added", "floor"),
    [
        # No requirement before; GLIBC_2.25 is a new floor.
        ({}, {"libc.so.6": ["GLIBC_2.25"]}, True, True),
        # libc needed GLIBC_2.5; a new provider needing GLIBC_2.25 raises the binary floor.
        (
            {"libc.so.6": ["GLIBC_2.5"]},
            {"libc.so.6": ["GLIBC_2.5"], "libm.so.6": ["GLIBC_2.25"]},
            True,
            True,
        ),
        # TBB shape: libc already needs GLIBC_2.34; a new libdl needing GLIBC_2.2.5 is no floor.
        (
            {"libc.so.6": ["GLIBC_2.34"]},
            {"libc.so.6": ["GLIBC_2.34"], "libdl.so.2": ["GLIBC_2.2.5"]},
            False,
            False,
        ),
    ],
    ids=["from-nothing", "raises-existing", "stays-at-floor"],
)
def test_a_new_provider_raises_the_floor_only_above_the_binary_floor(
    old: dict[str, list[str]],
    new: dict[str, list[str]],
    added: bool,
    floor: bool,
) -> None:
    kinds = _kinds(old, new)
    assert (ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds) is added
    assert (ChangeKind.RUNTIME_FLOOR_RAISED in kinds) is floor


def test_a_new_provider_without_versioned_nodes_adds_no_version_finding() -> None:
    kinds = _kinds({}, {"libfoo.so.1": []})
    assert not (
        kinds
        & {
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT,
            ChangeKind.RUNTIME_FLOOR_RAISED,
        }
    )


def test_an_existing_provider_whose_floor_rises_reports_both_tags() -> None:
    r = compare(
        _snap(elf=ElfMetadata(versions_required={"libc.so.6": ["GLIBC_2.17"]})),
        _snap(
            elf=ElfMetadata(
                versions_required={"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]}
            )
        ),
    )
    floors = [c for c in r.changes if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED]
    assert len(floors) == 1
    assert floors[0].old_value == "GLIBC_2.17"
    assert floors[0].new_value == "GLIBC_2.34"


def test_an_existing_provider_whose_floor_stays_reports_no_floor() -> None:
    kinds = _kinds(
        {"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]},
        {"libc.so.6": ["GLIBC_2.17", "GLIBC_2.34"]},
    )
    assert ChangeKind.RUNTIME_FLOOR_RAISED not in kinds


def _kinds_on(
    machine: str, old: dict[str, list[str]], new: dict[str, list[str]]
) -> dict[ChangeKind, Change]:
    r = compare(
        _snap(elf=ElfMetadata(machine=machine, versions_required=old)),
        _snap(elf=ElfMetadata(machine=machine, versions_required=new)),
    )
    return {c.kind: c for c in r.changes}


@pytest.mark.parametrize(
    ("machine", "baseline"),
    [("EM_X86_64", "GLIBC_2.2.5"), ("EM_386", "GLIBC_2.0")],
    ids=["x86-64", "i386"],
)
def test_a_node_at_the_arch_baseline_is_no_floor(machine: str, baseline: str) -> None:
    kinds = _kinds_on(machine, {}, {"libc.so.6": [baseline]})
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT in kinds
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED not in kinds
    assert ChangeKind.RUNTIME_FLOOR_RAISED not in kinds


@pytest.mark.parametrize(
    ("machine", "baseline"),
    [("EM_X86_64", "GLIBC_2.2.5"), ("EM_386", "GLIBC_2.0")],
    ids=["x86-64", "i386"],
)
def test_a_node_above_the_arch_baseline_raises_from_the_baseline(
    machine: str, baseline: str
) -> None:
    kinds = _kinds_on(machine, {}, {"libc.so.6": ["GLIBC_2.25"]})
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds
    floor = kinds[ChangeKind.RUNTIME_FLOOR_RAISED]
    assert floor.old_value.startswith(baseline)
    assert "architecture baseline" in floor.old_value
    assert floor.new_value == "GLIBC_2.25"


def test_an_unmeasured_arch_gets_no_exemption() -> None:
    # aarch64 is not in the table: GLIBC_2.2.5 is judged as an ordinary node
    # from no requirement, so it is a floor (the conservative direction).
    kinds = _kinds_on("EM_AARCH64", {}, {"libc.so.6": ["GLIBC_2.2.5"]})
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds
    assert ChangeKind.RUNTIME_FLOOR_RAISED in kinds
