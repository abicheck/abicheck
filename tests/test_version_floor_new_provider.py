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
from abicheck.model.change_catalog.registry import Verdict
from tests.test_diff_platform_deep import _snap


def _kinds(
    old_vers: dict[str, list[str]], new_vers: dict[str, list[str]]
) -> set[ChangeKind]:
    # x86-64 has a measured baseline (GLIBC_2.2.5), so these shapes judge floors.
    meta = {"machine": "EM_X86_64", "elf_class": 64, "ei_data": "LSB"}
    r = compare(
        _snap(elf=ElfMetadata(versions_required=old_vers, **meta)),
        _snap(elf=ElfMetadata(versions_required=new_vers, **meta)),
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
    machine: str,
    old: dict[str, list[str]],
    new: dict[str, list[str]],
    elf_class: int = 64,
    ei_data: str = "LSB",
) -> dict[ChangeKind, Change]:
    r = compare(
        _snap(
            elf=ElfMetadata(
                machine=machine,
                elf_class=elf_class,
                ei_data=ei_data,
                versions_required=old,
            )
        ),
        _snap(
            elf=ElfMetadata(
                machine=machine,
                elf_class=elf_class,
                ei_data=ei_data,
                versions_required=new,
            )
        ),
    )
    return {c.kind: c for c in r.changes}


@pytest.mark.parametrize(
    ("machine", "baseline"),
    [("EM_X86_64", "GLIBC_2.2.5"), ("EM_386", "GLIBC_2.0")],
    ids=["x86-64", "i386"],
)
def test_a_node_at_the_arch_baseline_is_no_floor(machine: str, baseline: str) -> None:
    width = 32 if machine == "EM_386" else 64
    kinds = _kinds_on(machine, {}, {"libc.so.6": [baseline]}, elf_class=width)
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
    width = 32 if machine == "EM_386" else 64
    kinds = _kinds_on(machine, {}, {"libc.so.6": ["GLIBC_2.25"]}, elf_class=width)
    assert ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED in kinds
    floor = kinds[ChangeKind.RUNTIME_FLOOR_RAISED]
    assert floor.old_value.startswith(baseline)
    assert "architecture baseline" in floor.old_value
    assert floor.new_value == "GLIBC_2.25"


def test_an_unmeasured_arch_reports_the_node_evidence_limited_and_no_floor() -> None:
    # EM_MIPS has no measured baseline: the node is reported at reduced
    # assurance with the reason, and no runtime floor is claimed.
    kinds = _kinds_on("EM_MIPS", {}, {"libc.so.6": ["GLIBC_2.25"]}, elf_class=32)
    assert ChangeKind.RUNTIME_FLOOR_RAISED not in kinds
    node = kinds[ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT]
    assert node.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
    assert node.modulation_rule == "glibc_baseline_unknown"
    assert "EM_MIPS" in node.modulation_reason
    assert "not established" in node.modulation_reason


def test_an_unmeasured_arch_with_an_established_floor_still_raises_it() -> None:
    # The old binary's own GLIBC node is evidence: a raise above it is established
    # even without a measured baseline.
    kinds = _kinds_on(
        "EM_MIPS",
        {"libc.so.6": ["GLIBC_2.5"]},
        {"libc.so.6": ["GLIBC_2.5", "GLIBC_2.25"]},
        elf_class=32,
    )
    assert ChangeKind.RUNTIME_FLOOR_RAISED in kinds


def test_an_aarch64_node_above_its_baseline_raises_the_floor() -> None:
    kinds = _kinds_on("EM_AARCH64", {}, {"libc.so.6": ["GLIBC_2.18"]})
    floor = kinds[ChangeKind.RUNTIME_FLOOR_RAISED]
    assert floor.old_value.startswith("GLIBC_2.17")
    assert floor.new_value == "GLIBC_2.18"


# Every measured row of model/glibc_baseline.py, with the byte order the reader
# records and the class. The oracle is the expected version written here.
_ROWS = [
    ("EM_X86_64", 64, "LSB", (2, 2, 5)),
    ("EM_386", 32, "LSB", (2, 0)),
    ("EM_AARCH64", 64, "LSB", (2, 17)),
    ("EM_AARCH64", 64, "MSB", (2, 17)),
    ("EM_ARM", 32, "LSB", (2, 4)),
    ("EM_PPC64", 64, "LSB", (2, 17)),
    ("EM_S390", 64, "MSB", (2, 2)),
    ("EM_RISCV", 64, "LSB", (2, 27)),
    ("EM_RISCV", 32, "LSB", (2, 33)),
    ("EM_LOONGARCH", 64, "LSB", (2, 36)),
]


@pytest.mark.parametrize(("machine", "width", "order", "baseline"), _ROWS)
def test_every_measured_row_baseline_and_its_next_node(
    machine: str, width: int, order: str, baseline: tuple[int, ...]
) -> None:
    from abicheck.model.glibc_baseline import arch_baseline

    entry = arch_baseline(machine, width, order)
    assert entry is not None
    assert entry.version == baseline
    at = ".".join(str(n) for n in baseline)
    above = ".".join(str(n) for n in (*baseline[:-1], baseline[-1] + 1))
    kinds_at = _kinds_on(machine, {}, {"libc.so.6": [f"GLIBC_{at}"]}, width, order)
    assert ChangeKind.RUNTIME_FLOOR_RAISED not in kinds_at
    kinds_above = _kinds_on(
        machine, {}, {"libc.so.6": [f"GLIBC_{above}"]}, width, order
    )
    assert ChangeKind.RUNTIME_FLOOR_RAISED in kinds_above


def test_ppc64_big_endian_is_unmeasured_and_evidence_limited() -> None:
    # The baseline depends on the ELF ABI (v1 or v2), which the reader does not
    # record for big-endian: no answer, so no floor is claimed.
    kinds = _kinds_on("EM_PPC64", {}, {"libc.so.6": ["GLIBC_2.25"]}, 64, "MSB")
    assert ChangeKind.RUNTIME_FLOOR_RAISED not in kinds
    assert kinds[ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT].effective_verdict is (
        Verdict.COMPATIBLE_WITH_RISK
    )


def _floor_changes(
    old: dict[str, list[str]],
    new: dict[str, list[str]],
    imports: list[object] | None = None,
) -> list[Change]:
    meta = {"machine": "EM_X86_64", "elf_class": 64, "ei_data": "LSB"}
    r = compare(
        _snap(elf=ElfMetadata(versions_required=old, **meta)),
        _snap(elf=ElfMetadata(versions_required=new, imports=imports or [], **meta)),
    )
    return [c for c in r.changes if c.kind is ChangeKind.RUNTIME_FLOOR_RAISED]


def test_a_new_libstdcxx_prefix_raises_from_nothing_without_a_baseline() -> None:
    # Only GLIBC_ prefixes take the architecture baseline: a new GLIBCXX node
    # is judged against the binary-wide GLIBCXX floor (nothing here).
    floors = _floor_changes({}, {"libstdc++.so.6": ["GLIBCXX_3.4.30"]})
    assert len(floors) == 1
    assert floors[0].old_value == ""
    assert floors[0].new_value == "GLIBCXX_3.4.30"


def test_imports_from_another_provider_or_unversioned_are_not_floor_evidence() -> None:
    from abicheck.elf_metadata import ElfImport

    imports = [
        ElfImport(name="cxa", version="GLIBC_2.30", version_soname="libm.so.6"),
        ElfImport(name="plain"),
        ElfImport(name="memcpy", version="GLIBC_2.34", version_soname="libc.so.6"),
    ]
    floors = _floor_changes(
        {"libc.so.6": ["GLIBC_2.2.5"]},
        {"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.34"]},
        imports,
    )
    assert len(floors) == 1
    assert "memcpy@GLIBC_2.34" in floors[0].description
    assert "cxa" not in floors[0].description


def test_more_than_five_floor_evidence_symbols_are_summarised() -> None:
    from abicheck.elf_metadata import ElfImport

    imports = [
        ElfImport(name=f"f{i}", version="GLIBC_2.34", version_soname="libc.so.6")
        for i in range(7)
    ]
    floors = _floor_changes(
        {"libc.so.6": ["GLIBC_2.2.5"]},
        {"libc.so.6": ["GLIBC_2.2.5", "GLIBC_2.34"]},
        imports,
    )
    assert "(+2 more)" in floors[0].description


def test_no_machine_means_no_baseline_answer() -> None:
    from abicheck.model.glibc_baseline import arch_baseline

    assert arch_baseline(None, 64, "LSB") is None
    assert arch_baseline("EM_X86_64", None, "LSB") is None


def test_a_new_provider_floor_summarises_more_than_five_evidence_symbols() -> None:
    from abicheck.elf_metadata import ElfImport

    imports = [
        ElfImport(name=f"g{i}", version="GLIBC_2.34", version_soname="libc.so.6")
        for i in range(7)
    ]
    floors = _floor_changes({}, {"libc.so.6": ["GLIBC_2.34"]}, imports)
    assert len(floors) == 1
    assert "(+2 more)" in floors[0].description
    assert floors[0].old_value.endswith("(architecture baseline)")
