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
                source_location=SourceLocation(
                    path="include/lib.h", line=7, origin="PUBLIC_HEADER"
                ),
                visibility="public_header",
                api_relevant=True,
            )
        ]
    )


def test_diff_source_abi_reports_concept_tightened() -> None:
    # Structured (conjunctive) values: the new requirement set strictly
    # contains the old one, so tightening is proven.
    changes = diff_source_abi(
        _surface("conj:sha256:req-a"),
        _surface("conj:sha256:req-a,sha256:req-b"),
    )

    concept_changes = [c for c in changes if c.kind == ChangeKind.CONCEPT_TIGHTENED]

    assert len(concept_changes) == 1
    assert concept_changes[0].symbol == "lib::Accepts"
    assert "L4_SOURCE_ABI" in concept_changes[0].source_location


def test_opaque_constraint_change_does_not_claim_tightening() -> None:
    # Two opaque fingerprints differ in both directions alike: a change, not
    # a proven tightening (case105's reverse edit used to read API_BREAK).
    for old, new in (
        (
            "requires sizeof(T) >= 1",
            "requires sizeof(T) >= 1 && requires { typename T::iterator; }",
        ),
        (
            "requires sizeof(T) >= 1 && requires { typename T::iterator; }",
            "requires sizeof(T) >= 1",
        ),
    ):
        kinds = [
            c.kind
            for c in diff_source_abi(_surface(old), _surface(new))
            if "concept" in c.kind.value
        ]
        assert kinds == [ChangeKind.CONCEPT_CONSTRAINT_CHANGED]
