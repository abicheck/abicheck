# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""G31 Phase D — the L2 header-backend capability matrix, kept honest.

Mirrors ``tests/test_platform_matrix.py``'s generator/`--check` contract, and
adds the checks that matter for *this* matrix specifically: a published claim
about "which backend populates which fact" is worthless if nothing re-derives
it from the parsers. So this file asserts three things:

1. **Coverage** — every field of every declaration dataclass the header
   backends build has exactly one row. A new model field fails here rather
   than silently going undocumented.
2. **Truth** — each row's per-backend claim matches what
   ``dumper_castxml.py``/``dumper_clang.py`` actually do, read back with
   :mod:`ast`. A backend that starts (or stops) populating a fact fails here.
3. **Derivation** — the hybrid column is computed from
   ``dumper_hybrid.py``'s real backfill lists, not hand-typed.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from abicheck import model

REPO_DIR = Path(__file__).resolve().parent.parent


def _load(name: str):
    """Import a `scripts/` module by path (they are not an installed package).

    Registers the module in ``sys.modules`` BEFORE executing it, unlike the
    shorter helper in ``test_platform_matrix.py``: ``@dataclass`` resolves a
    string annotation (these modules use ``from __future__ import
    annotations``) by looking its own module up in ``sys.modules``, and an
    unregistered module makes that lookup return ``None`` — a bare
    ``AttributeError`` from inside ``dataclasses`` rather than anything that
    points at the cause.
    """
    path = REPO_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def caps():
    return _load("backend_capabilities")


@pytest.fixture(scope="module")
def gen():
    return _load("gen_backend_capability_matrix")


# ── 1. Coverage ─────────────────────────────────────────────────────────────


def test_every_model_field_has_exactly_one_row(caps) -> None:
    rows: dict[tuple[str, str], object] = {}
    for row in caps.FACT_ROWS:
        key = (row.owner, row.field)
        assert key not in rows, f"duplicate row for {key}"
        rows[key] = row

    for class_name in caps.COVERED_MODEL_CLASSES:
        cls = getattr(model, class_name)
        real = {f.name for f in dataclasses.fields(cls)}
        documented = {field for (owner, field) in rows if owner == class_name}
        assert real == documented, (
            f"{class_name}: matrix and model disagree. Missing rows: "
            f"{sorted(real - documented)}; rows for fields that no longer "
            f"exist: {sorted(documented - real)}. Update "
            "scripts/backend_capabilities.py's FACT_ROWS and regenerate."
        )


def test_covered_classes_are_real_model_types(caps) -> None:
    for class_name in caps.COVERED_MODEL_CLASSES:
        assert dataclasses.is_dataclass(getattr(model, class_name))


# ── 2. Truth: the claim vs. what the parsers do ─────────────────────────────


def _mismatches(caps, rows) -> list[str]:
    """Rows whose published claim disagrees with the parser source."""
    evidence = {
        "castxml": caps.castxml_evidence(),
        "clang": caps.clang_evidence(),
    }
    extracting = (caps.Capability.FULL, caps.Capability.PARTIAL)
    out: list[str] = []
    for row in rows:
        for backend, capability in (
            ("castxml", row.castxml),
            ("clang", row.clang),
        ):
            observed = evidence[backend][row.owner].get(row.field, caps.Evidence.ABSENT)
            claims_extraction = capability in extracting
            really_extracts = observed is caps.Evidence.EXTRACTED
            if claims_extraction != really_extracts:
                out.append(
                    f"{row.owner}.{row.field} [{backend}]: matrix says "
                    f"{capability.value!r}, source says {observed.value!r}"
                )
    return out


def test_matrix_claims_match_parser_source(caps) -> None:
    """The whole point of generating this matrix rather than writing it.

    ``FULL``/``PARTIAL`` mean "this backend's own parse populates the fact",
    so the backend must pass that keyword with a real expression. Every other
    capability means it does not, so the keyword must be absent or a hardcoded
    placeholder (``size_bits=None``, ``is_opaque=False``).
    """
    assert _mismatches(caps, caps.FACT_ROWS) == []


def test_scanner_distinguishes_a_placeholder_from_an_extraction(caps) -> None:
    """Guards the check above from silently degrading into "keyword present".

    ``dumper_clang.py`` passes ``size_bits=None`` and ``is_opaque=False`` —
    keywords that are present but extract nothing. If the scanner ever counted
    those as extraction, `test_matrix_claims_match_parser_source` would pass
    against a matrix claiming clang computes record layout.
    """
    clang = caps.clang_evidence()
    assert clang["RecordType"]["size_bits"] is caps.Evidence.LITERAL
    assert clang["RecordType"]["is_opaque"] is caps.Evidence.LITERAL
    # ...and a genuinely computed one on the same class is not mistaken for it.
    assert clang["RecordType"]["is_union"] is caps.Evidence.EXTRACTED


def test_scanner_reads_multiline_values(caps) -> None:
    """Regression guard for a real bug in an earlier, line-oriented scanner.

    castxml builds ``bases``/``virtual_bases`` as ``[] if is_opaque else
    [...]`` — a value whose FIRST line is a bare ``[]``. A line-oriented match
    called both hardcoded-empty and would have forced two wrong rows into the
    published matrix.
    """
    castxml = caps.castxml_evidence()
    assert castxml["RecordType"]["bases"] is caps.Evidence.EXTRACTED
    assert castxml["RecordType"]["virtual_bases"] is caps.Evidence.EXTRACTED


def test_the_fact_this_phase_added_is_present_on_both_backends(caps) -> None:
    """G31 Phase C's own change, pinned: ``Param.is_restrict`` was castxml-only
    (which made every cross-backend comparison report a false
    ``param_restrict_changed``) until the clang backend learned it."""
    row = next(
        r for r in caps.FACT_ROWS if (r.owner, r.field) == ("Param", "is_restrict")
    )
    assert row.castxml is caps.Capability.FULL
    assert row.clang is caps.Capability.FULL


# ── 3. Derivation: the hybrid column ────────────────────────────────────────


def test_hybrid_backfilled_rows_match_the_merge_layout_list(caps) -> None:
    """``_LAYOUT_SCALAR_ATTRS`` is imported, so it cannot drift silently."""
    from abicheck.dumper_hybrid import _LAYOUT_SCALAR_ATTRS

    backfilled = {r.field for r in caps.rows_for("RecordType") if r.hybrid_backfilled}
    assert set(_LAYOUT_SCALAR_ATTRS) <= backfilled, (
        "dumper_hybrid backfills a RecordType layout attr the matrix does not "
        f"mark: {sorted(set(_LAYOUT_SCALAR_ATTRS) - backfilled)}"
    )


def test_every_merge_backfilled_attr_is_declared(caps) -> None:
    """The other direction: a NEW fact added to one of ``dumper_hybrid``'s
    ``for attr in (...)`` backfill loops must be declared in the matrix, or
    the hybrid column silently understates what a merge recovers."""
    import ast as ast_mod

    source = (REPO_DIR / "abicheck" / "dumper_hybrid.py").read_text()
    looped: set[str] = set()
    for node in ast_mod.walk(ast_mod.parse(source)):
        if (
            isinstance(node, ast_mod.For)
            and isinstance(node.target, ast_mod.Name)
            and node.target.id == "attr"
            and isinstance(node.iter, (ast_mod.Tuple, ast_mod.List))
        ):
            for elt in node.iter.elts:
                if isinstance(elt, ast_mod.Constant) and isinstance(elt.value, str):
                    looped.add(elt.value)

    assert looped, "found no backfill loops in dumper_hybrid.py — did it move?"
    declared = {r.field for r in caps.FACT_ROWS if r.hybrid_backfilled}
    assert looped <= declared, (
        "dumper_hybrid backfills these facts, but the matrix does not mark "
        f"them: {sorted(looped - declared)}"
    )


def test_hybrid_is_castxml_for_a_fact_the_merge_does_not_backfill(caps) -> None:
    """The consequence the doc's Known gaps section spells out: a hybrid
    snapshot is castxml-based, so a clang-only fact nothing backfills does NOT
    reach a declaration both backends saw.

    ``EnumType.underlying_type`` used to be this test's fixture, before
    castxml itself started extracting a real value (Phase C fact-completeness
    pass) — it is no longer a fact only clang populates, so it can't
    demonstrate a dropped-on-merge clang-only fact any more.
    ``RecordType.is_template_pattern`` has the same castxml-NONE/clang-FULL/
    not-backfilled shape today.
    """
    row = next(
        r
        for r in caps.FACT_ROWS
        if (r.owner, r.field) == ("RecordType", "is_template_pattern")
    )
    assert not row.hybrid_backfilled
    assert row.clang is caps.Capability.FULL
    assert caps.hybrid_capability(row) is caps.Capability.NONE


def test_hybrid_takes_clang_for_a_backfilled_castxml_gap(caps) -> None:
    row = next(
        r
        for r in caps.FACT_ROWS
        if (r.owner, r.field) == ("RecordType", "is_standard_layout")
    )
    assert row.hybrid_backfilled
    assert row.castxml is caps.Capability.NONE
    assert caps.hybrid_capability(row) is caps.Capability.FULL


def test_non_header_facts_stay_non_header_in_the_hybrid_column(caps) -> None:
    for row in caps.FACT_ROWS:
        if not row.populated_by_any_backend:
            assert caps.hybrid_capability(row) is caps.Capability.OTHER_LAYER


# ── Row invariants ──────────────────────────────────────────────────────────


def test_divergent_rows_explain_themselves(caps) -> None:
    """A cell a reader would question must carry its reason — enforced in
    ``FactRow.__post_init__``, asserted here so the rule is visible."""
    for row in caps.FACT_ROWS:
        if row.castxml is not row.clang:
            assert row.note, f"{row.owner}.{row.field} differs with no note"


def test_row_construction_rejects_a_half_declared_non_header_fact(caps) -> None:
    with pytest.raises(ValueError, match="OTHER_LAYER"):
        caps.FactRow(
            "Function",
            "name",
            caps.Capability.OTHER_LAYER,
            caps.Capability.FULL,
            note="mismatched",
        )


def test_row_construction_rejects_an_unknown_owner(caps) -> None:
    with pytest.raises(ValueError, match="unknown owner"):
        caps.FactRow("Nonexistent", "x", caps.Capability.FULL, caps.Capability.FULL)


# ── Generated doc ───────────────────────────────────────────────────────────


def test_generated_matrix_is_in_sync(gen) -> None:
    current = gen.OUT_PATH.read_text(encoding="utf-8")
    expected = gen._splice(current, gen.render())
    assert current == expected, (
        "docs/reference/header-backend-capabilities.md's matrix section is "
        "stale -- regenerate with "
        "`python scripts/gen_backend_capability_matrix.py`"
    )


def test_generated_section_has_marker_comment(gen) -> None:
    text = gen.OUT_PATH.read_text(encoding="utf-8")
    assert "generated by scripts/gen_backend_capability_matrix.py" in text.lower()


def test_check_mode_passes_on_the_committed_file(gen) -> None:
    assert gen.main(["--check"]) == 0


def test_every_row_is_rendered(gen, caps) -> None:
    text = gen.OUT_PATH.read_text(encoding="utf-8")
    for row in caps.FACT_ROWS:
        assert f"| `{row.field}` |" in text, f"{row.owner}.{row.field} not rendered"
