# SPDX-License-Identifier: Apache-2.0
"""GCC representations compare only with GCC; stored extraction gaps persist."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.model import AbiSnapshot, Fact, RecordType, TypeField
from abicheck.policy.analysis_assurance import compute_analysis_assurance
from abicheck.serialization import load_snapshot, save_snapshot


def snapshot(producer: str, value: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="a.so",
        version="1",
        from_headers=True,
        ast_producer=producer,
        types=[
            RecordType(
                name="S",
                kind="struct",
                fields=[
                    TypeField(name="x", type="int", default_fact=Fact.present(value))
                ],
            )
        ],
    )


@pytest.mark.parametrize("other", ["gcc-experimental", "clang", "castxml", "unknown"])
def test_gcc_initializer_representation_is_same_producer_only(other: str) -> None:
    result = compare(snapshot("gcc-experimental", "1"), snapshot(other, "2"))
    kinds = {c.kind.value for c in result.changes}
    assert ("field_default_initializer_changed" in kinds) == (
        other == "gcc-experimental"
    )


@pytest.mark.parametrize("side", ["old", "new"])
def test_stored_extractor_limitations_lower_assurance(
    tmp_path: Path, side: str
) -> None:
    old, new = snapshot("gcc-experimental", "1"), snapshot("gcc-experimental", "1")
    chosen = old if side == "old" else new
    chosen.dump_provenance = {
        "extraction_limitations": ["macros are not modelled", "macro scope is partial"]
    }
    filename = tmp_path / "snapshot.json"
    save_snapshot(chosen, filename)
    if side == "old":
        old = load_snapshot(filename)
    else:
        new = load_snapshot(filename)
    result = compare(old, new)
    assurance = compute_analysis_assurance(result, old, new)
    assert not result.changes
    assert assurance.status == "partial"
    assert any(
        f"{side} extraction limitation: macros are not modelled" in note
        for note in assurance.notes
    )
    assert any(
        f"{side} extraction limitation: macro scope is partial" in note
        for note in assurance.notes
    )


@pytest.mark.parametrize("raw", ["bad", {"bad": "value"}, [5]])
def test_malformed_limitations_cannot_claim_complete_assurance(raw: object) -> None:
    old, new = snapshot("gcc-experimental", "1"), snapshot("gcc-experimental", "1")
    new.dump_provenance = {"extraction_limitations": raw}
    assurance = compute_analysis_assurance(compare(old, new), old, new)
    assert assurance.status == "partial"
    assert any("invalid extraction_limitations metadata" in n for n in assurance.notes)
