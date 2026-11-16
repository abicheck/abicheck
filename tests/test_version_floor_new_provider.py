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
