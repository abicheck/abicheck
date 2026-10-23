"""The TARGET_ISA_CHANGED detector and its consequence annotation.

Snapshots carry only what the detector reads (``build_mode`` provenance and
the ELF ISA-needed note), so each case states its inputs exactly.
"""

from __future__ import annotations

import pytest

from abicheck.build_mode import build_mode_from_signals
from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import BREAKING_KINDS, RISK_KINDS
from abicheck.compare.target_isa import (
    annotate_target_isa_consequences,
    target_isa_of,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.target_isa import TargetIsa


def _snap(
    producer: str | None = None, note: frozenset[str] = frozenset()
) -> AbiSnapshot:
    snap = AbiSnapshot(library="libxvec.so", version="1.0")
    if producer is not None:
        snap.build_mode = build_mode_from_signals(raw_producer=producer)
    snap.elf = ElfMetadata(machine="EM_X86_64", gnu_properties=note)
    return snap


def _isa_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    result = compare(old, new)
    return [c for c in result.changes if c.kind is ChangeKind.TARGET_ISA_CHANGED]


def test_avx2_to_avx512f_is_a_raised_target_with_the_new_feature_named():
    old = _snap("GNU C17 13.3.0 -mavx2 -mtune=generic -march=x86-64 -g")
    new = _snap("GNU C17 13.3.0 -mavx512f -mtune=generic -march=x86-64 -g")
    (change,) = _isa_changes(old, new)
    assert change.old_value == "x86-64 (+avx2)"
    assert change.new_value == "x86-64 (+avx512f)"
    assert "avx512f" in change.description
    assert change.symbol == "<target-isa>"


def test_v2_to_v3_level_bump_is_reported_with_the_level_names():
    old = _snap("GNU C17 13.3.0 -march=x86-64-v2 -mtune=generic -g")
    new = _snap("GNU C17 13.3.0 -march=x86-64-v3 -mtune=generic -g")
    (change,) = _isa_changes(old, new)
    assert change.old_value == "x86-64-v2"
    assert change.new_value == "x86-64-v3"


def test_note_only_v3_against_producer_baseline_is_raised():
    # The new image carries the linker's ISA-needed v3 note, the old one only a
    # producer with no ISA flag beyond the baseline: the note's requirement counts.
    old = _snap("GNU C17 13.3.0 -march=x86-64 -g")
    new = _snap("GNU C17 13.3.0 -march=x86-64 -g", note=frozenset({"x86-64-v3"}))
    (change,) = _isa_changes(old, new)
    # The producer's recorded arch stays x86-64; the note's v3 requirement shows
    # as the extension features beyond the baseline.
    assert change.new_value.startswith("x86-64 (+") and "avx2" in change.new_value


def test_downgrade_is_reported_as_lowered_requirement():
    old = _snap("GNU C17 13.3.0 -mavx512f -g")
    new = _snap("GNU C17 13.3.0 -mavx2 -g")
    (change,) = _isa_changes(old, new)
    assert change.old_value == "x86-64 (+avx512f)"
    assert change.new_value == "x86-64 (+avx2)"


def test_mixed_change_names_both_the_new_requirement_and_what_was_dropped():
    # avx2 -> fma: the new build needs fma the old one did not, and no longer
    # needs avx/avx2. Raised wins the direction; the drop is still stated.
    old = _snap("GNU C17 -mavx2 -g")
    new = _snap("GNU C17 -mfma -g")
    (change,) = _isa_changes(old, new)
    assert "raised: the new build requires fma" in change.description
    assert "also drops" in change.description
    assert "avx2" in change.description


def test_direction_is_stated_in_the_finding_text():
    old = _snap("GNU C17 -mavx512f -g")
    new = _snap("GNU C17 -mavx2 -g")
    (change,) = _isa_changes(old, new)
    assert "lowered: the new build no longer requires avx512f" in change.description


def test_same_target_is_silent():
    producer = "GNU C17 13.3.0 -mavx2 -mtune=generic -march=x86-64 -g"
    assert _isa_changes(_snap(producer), _snap(producer)) == []


def test_unknown_side_is_silent_not_a_baseline():
    # clang records no ISA; comparing it with a known target is missing evidence.
    old = _snap("clang version 18.1.3")
    new = _snap("GNU C17 13.3.0 -mavx512f -g")
    assert _isa_changes(old, new) == []


def test_arch_value_change_with_equal_features_is_reported_as_changed():
    old = _snap("GNU C17 -march=haswell -g")
    new = _snap("GNU C17 -march=skylake -g")
    (change,) = _isa_changes(old, new)
    assert change.old_value == "haswell"
    assert change.new_value == "skylake"


def test_target_isa_finding_is_risk_not_breaking():
    # Matches x86_isa_baseline_raised (the loader-note twin): RISK, not an ABI break.
    assert ChangeKind.TARGET_ISA_CHANGED in RISK_KINDS
    assert ChangeKind.TARGET_ISA_CHANGED not in BREAKING_KINDS


@pytest.mark.parametrize(
    ("producer", "note", "expected"),
    [
        (None, frozenset(), None),
        (
            "GNU C17 -march=x86-64-v3 -g",
            frozenset(),
            TargetIsa(
                "x86-64-v3",
                frozenset(
                    {
                        "sse2",
                        "sse3",
                        "ssse3",
                        "sse4.1",
                        "sse4.2",
                        "popcnt",
                        "cx16",
                        "avx",
                        "avx2",
                        "bmi",
                        "bmi2",
                        "f16c",
                        "fma",
                        "lzcnt",
                        "movbe",
                        "xsave",
                    }
                ),
            ),
        ),
    ],
)
def test_snapshot_target_reads_producer_and_note(producer, note, expected):
    assert target_isa_of(_snap(producer, note)) == expected


def _layout_change(kind: ChangeKind) -> Change:
    return Change(kind=kind, symbol="Vec8f", description=f"{kind.value} on Vec8f")


def test_consequences_are_annotated_and_none_removed():
    root = Change(
        kind=ChangeKind.TARGET_ISA_CHANGED,
        symbol="<target-isa>",
        description="compiled target ISA changed",
        old_value="x86-64 (+avx2)",
        new_value="x86-64 (+avx512f)",
    )
    size = _layout_change(ChangeKind.STRUCT_SIZE_CHANGED)
    unrelated = _layout_change(ChangeKind.VAR_REMOVED)
    changes = [root, size, unrelated]
    annotate_target_isa_consequences(changes)
    # Nothing removed or reordered; only the layout consequence gains the clause.
    assert changes == [root, size, unrelated]
    assert "x86-64 (+avx2) → x86-64 (+avx512f)" in size.description
    assert "compiled target ISA" not in unrelated.description


def test_annotation_is_idempotent():
    root = Change(
        kind=ChangeKind.TARGET_ISA_CHANGED,
        symbol="<target-isa>",
        description="r",
        old_value="a",
        new_value="b",
    )
    size = _layout_change(ChangeKind.TYPE_SIZE_CHANGED)
    changes = [root, size]
    annotate_target_isa_consequences(changes)
    once = size.description
    annotate_target_isa_consequences(changes)
    assert size.description == once


def test_annotation_without_a_root_changes_nothing():
    size = _layout_change(ChangeKind.STRUCT_SIZE_CHANGED)
    annotate_target_isa_consequences([size])
    assert size.description == "struct_size_changed on Vec8f"
