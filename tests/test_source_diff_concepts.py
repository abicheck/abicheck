from __future__ import annotations

from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_diff import diff_source_abi
from abicheck.checker_policy import ChangeKind


def _surface(concept_value: str) -> SourceAbiSurface:
    return SourceAbiSurface(
        reachable_declarations=[
            SourceEntity(
                id="concept:lib::Accepts",
                kind="concept",
                qualified_name="lib::Accepts",
                value=concept_value,
                source_location=SourceLocation(path="include/lib.h", line=7, origin="PUBLIC_HEADER"),
                visibility="public_header",
                api_relevant=True,
            )
        ]
    )


def test_diff_source_abi_reports_concept_tightened() -> None:
    changes = diff_source_abi(
        _surface("requires sizeof(T) >= 1"),
        _surface("requires sizeof(T) >= 1 && requires { typename T::iterator; }"),
    )

    concept_changes = [c for c in changes if c.kind == ChangeKind.CONCEPT_TIGHTENED]

    assert len(concept_changes) == 1
    assert concept_changes[0].symbol == "lib::Accepts"
    assert "L4_SOURCE_ABI" in concept_changes[0].source_location
