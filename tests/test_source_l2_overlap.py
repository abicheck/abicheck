# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Gate for the L4 / L2 ChangeKind overlap table (``scripts/source_l2_overlap.py``).

The table is the Phase 8 assessment of ``docs/contribute/plans/
l4-l2-extraction-convergence.md``. These tests fail when a kind lands on either
tier without a classification, when a row goes stale, or when a label
contradicts the derivation. The L4 and L2 oracles are direct AST scans of the
emitting modules, independent of the closure the script uses.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "source_l2_overlap.py"
    spec = importlib.util.spec_from_file_location("source_l2_overlap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["source_l2_overlap"] = module
    spec.loader.exec_module(module)
    return module


overlap = _load_script()


def _direct_refs(paths: list[Path]) -> set[str]:
    """``ChangeKind.<NAME>`` attribute references in *paths*, read straight from the AST."""
    names: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "ChangeKind"
            ):
                names.add(node.attr)
    return names


def _kind_values(names: set[str]) -> set[str]:
    from abicheck.model.change_catalog.kinds import ChangeKind

    return {ChangeKind[name].value for name in names}


def test_every_kind_either_tier_emits_is_classified():
    l4, l2 = overlap.derive_sides()
    assert overlap.problems(l4, l2) == []


def test_classification_has_no_stale_rows():
    l4, l2 = overlap.derive_sides()
    assert overlap.CLASSIFICATION.keys() - (l4 | l2) == set()


def test_l4_closure_equals_direct_source_diff_references():
    l4, _ = overlap.derive_sides()
    # ``diff_source_abi`` calls helpers in sibling modules (``_diff_provenance``
    # lives in ``source_provenance_diff``), so the oracle scans every L4 emitter.
    buildsource = ROOT / "abicheck" / "buildsource"
    direct = _direct_refs(
        [buildsource / "source_diff.py", buildsource / "source_provenance_diff.py"]
    )
    assert set(l4) == direct


def test_l2_closure_covers_every_direct_detector_reference():
    _, l2 = overlap.derive_sides()
    modules = sorted((ROOT / "abicheck").glob("diff_types*.py")) + sorted(
        (ROOT / "abicheck").glob("diff_symbols*.py")
    )
    direct = _direct_refs(modules)
    assert direct - set(l2) == set(), "a detector emits a kind the closure misses"


def test_new_kind_on_either_tier_fails_until_classified():
    l4, l2 = overlap.derive_sides()
    new = "BRAND_NEW_TEST_ONLY_KIND"
    assert any(
        line == f"unclassified: {new} (L4)" for line in overlap.problems(l4 | {new}, l2)
    )
    assert any(
        line == f"unclassified: {new} (L2)" for line in overlap.problems(l4, l2 | {new})
    )


def test_label_must_match_the_derivation():
    l4, l2 = overlap.derive_sides()
    # CONCEPT_TIGHTENED is L4-only; claiming it is L2-only must be reported.
    wrong = dict(overlap.CLASSIFICATION)
    wrong["CONCEPT_TIGHTENED"] = overlap.Row(overlap.L2_ONLY)
    original = overlap.CLASSIFICATION
    overlap.CLASSIFICATION = wrong
    try:
        found = overlap.problems(l4, l2)
    finally:
        overlap.CLASSIFICATION = original
    assert any(
        "CONCEPT_TIGHTENED: L2_ONLY but derivation says L4" in line for line in found
    )


def test_inline_removal_is_the_only_shared_kind_by_name():
    l4, l2 = overlap.derive_sides()
    assert (l4 & l2) == {"INLINE_FUNCTION_REMOVED"}
    assert _kind_values({"INLINE_FUNCTION_REMOVED"}) == {"inline_function_removed"}
