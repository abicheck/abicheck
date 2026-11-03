"""A changed concept constraint is ``concept_tightened`` only when tightening
is proven by conjunctive-requirement inclusion; a fingerprint inequality
alone (the reverse edit, a rewrite, an opaque side) is a RISK
(case105: the old rule reported the reverse edit as API_BREAK too)."""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess

import pytest

from abicheck.buildsource.concept_diff import diff_concepts
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.buildsource.source_extractors.concept_constraints import (
    CONJUNCTIVE_PREFIX,
    concept_constraint_value,
    conjunctive_atoms,
)
from abicheck.checker_policy import API_BREAK_KINDS, RISK_KINDS
from abicheck.model.change_catalog.kinds import ChangeKind

ATOMS = ("a", "b", "c")


def _surface(value: str, body: str) -> SourceAbiSurface:
    ent = SourceEntity(
        id="x", kind="concept", qualified_name="ns::C", body_hash=body, value=value
    )
    return SourceAbiSurface(reachable_declarations=[ent])


def _conj(atoms: frozenset[str]) -> str:
    return CONJUNCTIVE_PREFIX + ",".join(sorted(atoms))


SUBSETS = [frozenset(c) for r in range(1, 4) for c in itertools.combinations(ATOMS, r)]


@pytest.mark.parametrize(("old", "new"), list(itertools.product(SUBSETS, SUBSETS)))
def test_direction_is_set_inclusion_over_every_small_pair(old, new):
    # Oracle: plain set algebra, independent of the detector's code path.
    changes = diff_concepts(
        _surface(_conj(old), "h-" + _conj(old)), _surface(_conj(new), "h-" + _conj(new))
    )
    kinds = [c.kind for c in changes]
    if old == new:
        assert kinds == []
    elif old < new:
        assert kinds == [ChangeKind.CONCEPT_TIGHTENED]
    else:
        assert kinds == [ChangeKind.CONCEPT_CONSTRAINT_CHANGED]


def test_opaque_legacy_value_never_claims_direction():
    changes = diff_concepts(
        _surface("sha256:old", "h1"), _surface(_conj(frozenset("ab")), "h2")
    )
    assert [c.kind for c in changes] == [ChangeKind.CONCEPT_CONSTRAINT_CHANGED]


def test_same_body_hash_is_no_change_even_across_value_encodings():
    changes = diff_concepts(
        _surface("sha256:old", "same"), _surface(_conj(frozenset("a")), "same")
    )
    assert changes == []


def test_kind_severities():
    assert ChangeKind.CONCEPT_TIGHTENED in API_BREAK_KINDS
    assert ChangeKind.CONCEPT_CONSTRAINT_CHANGED in RISK_KINDS


def test_conjunctive_atoms_parsing():
    assert conjunctive_atoms("sha256:abc") is None
    assert conjunctive_atoms("") is None
    assert conjunctive_atoms("conj:x,y") == frozenset({"x", "y"})


CONCEPTS = {
    "base": "requires(T a, T b) { a + b; }",
    "tight": "requires(T a, T b) { a + b; T(); }",
    "tight_renamed_reordered": "requires(T x, T y) { T(); x + y; }",
    "tight_parenthesized": "(requires(T a, T b) { a + b; T(); })",
    "mixed": "requires(T a, T b) { a - b; T(); }",
    "disjunction": "requires(T a, T b) { a + b; } || requires { T(); }",
}


@pytest.fixture(scope="module")
def concept_values(tmp_path_factory):
    clang = shutil.which("clang++")
    if clang is None:
        pytest.skip("clang++ not available")
    d = tmp_path_factory.mktemp("concepts")
    out = {}
    for name, body in CONCEPTS.items():
        src = d / f"{name}.hpp"
        src.write_text(f"template <typename T> concept C = {body};\n")
        ast = json.loads(
            subprocess.run(
                [
                    clang,
                    "-std=c++20",
                    "-fsyntax-only",
                    "-Xclang",
                    "-ast-dump=json",
                    str(src),
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        )
        (decl,) = [n for n in ast["inner"] if n.get("kind") == "ConceptDecl"]
        out[name] = conjunctive_atoms(concept_constraint_value(decl))
    return out


@pytest.mark.integration
def test_real_clang_constraints_decompose(concept_values):
    v = concept_values
    assert len(v["base"]) == 1 and len(v["tight"]) == 2
    assert v["base"] < v["tight"]
    # Renaming the requires-parameters, reordering requirements and
    # parenthesizing are not changes.
    assert v["tight_renamed_reordered"] == v["tight"]
    assert v["tight_parenthesized"] == v["tight"]
    # A changed requirement is neither a superset nor a subset.
    assert not (v["base"] <= v["mixed"]) and not (v["mixed"] <= v["base"])
    # A disjunction is one opaque atom, never decomposed.
    assert len(v["disjunction"]) == 1 and not (v["base"] <= v["disjunction"])
