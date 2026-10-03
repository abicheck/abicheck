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

"""Harness H4: the heuristic registry for defect family F4, "Structure over
spelling" (``docs/contribute/plans/defect-family-harnesses.md``).

**Invariant.** No finding's severity rests only on a name, suffix or
directory heuristic: every heuristic has a named structural fact that
confirms or vetoes it (#1411, #1231, #1316, #1308, #1344, #1218).

**Runtime registry** (design-hardening Phase 5): every spelling-based
classifier registers with ``abicheck.model.name_heuristics`` -- a lowering
or review-routing effect, or, to raise severity, a named structural fact --
and ``abicheck.policy.name_heuristics`` catalogues the complete set. This
harness stays the oracle.

**Inventory** (``_family_f4_inventory``): an AST scan of the decision
modules (``compare/``, ``policy/``, ``diff_*``, ``detectors*``,
``checker*``, ``classify*`` and the flat-root ``internal_leak*``) for regex,
affix, fnmatch and name-vocabulary sites, keyed
``module::qualname::kind:pattern``. Each key must resolve to a registered
heuristic (``_family_f4_resolve``) or carry an exemption row in
``_family_f4_registry.HEURISTICS``; the uncategorized remainder must stay at
or below ``UNCATEGORIZED_CEILING`` (0).

**Oracle** (``_family_f4_harness``): per covered heuristic, FP cells (name
matches, structure vetoes -> no heuristic finding), FN cells (structure
positive, near-miss name -> the real finding survives) and a control cell
(the heuristic is alive), stated as raw ``(kind, symbol)`` pairs and a
verdict band; the #1231 cells add a metamorphic check against a structurally
identical twin in a neutral namespace. Every severity-raising heuristic has
cells. There is no strict xfail: the #1411 sentinel and #1231
``detail``/``impl`` findings were fixed in Phase 0.
"""

from __future__ import annotations

import collections
import re

import pytest
from _family_f4_harness import CELLS, _scope_path_for_mutant, oracle_violations
from _family_f4_inventory import PKG, heuristic_site_inventory, scanned_files
from _family_f4_raise_scan import raise_violations, raising_declarations_reached
from _family_f4_registry import COVERED, EXEMPTION_REASONS, HEURISTICS
from _family_f4_resolve import resolve_site

import abicheck.diff_namespaces as diff_namespaces_mod
import abicheck.diff_serialization as diff_serialization_mod
import abicheck.internal_leak as internal_leak_mod
from abicheck.compare.enum_sentinel import ENUM_SENTINEL
from abicheck.model.name_heuristics import (
    LazyFactInput,
    NameHeuristicEffect,
    StructuralFact,
    heuristic_callables,
    register_name_heuristic,
    register_severity_raising_heuristic,
)
from abicheck.policy.name_heuristics import (
    name_heuristic_registry,
    registry_problems,
    severity_raising_heuristics,
)

# --------------------------------------------------------------------------
# Registry contract
# --------------------------------------------------------------------------

#: The agreed ceiling on *uncategorized* sites -- a site that is neither a
#: registered heuristic nor a stated non-heuristic exemption. Phase 5 drove it
#: from 34 (the old ``convention`` backlog) to 0 and records 0 as the ceiling.
UNCATEGORIZED_CEILING = 0

#: Shrink-only ceilings on the exemption rows, per category. Lower a number
#: when a row is deleted or registered; never raise one to admit a new
#: naming-convention site -- register it at runtime instead.
#: Raised once, in the same PR that widened the scan to post_processing*,
#: surface.py and export_surface*.py: the 16 sites it found are all
#: non-heuristic (ChangeKind-value prefixes, frozen-namespace user globs,
#: mangling, C++ keywords, ``std::``).
EXEMPTION_CEILINGS: dict[str, int] = {
    "spelling": 65,
    # 44: the merge with main brought model/symbol_ownership.py into the scan
    # (main made it the vocabulary owner) and main's shared
    # match_frozen_namespace -- both one pre-existing `_Z` mangling check.
    # 41: minus three sites the Phase 3 decoration codecs deleted
    # (export_join._macho_shifted, the rename parser's ctor/dtor scan).
    "grammar": 41,
    # 43: minus outcome_release.unclassified_release_contribution_fields,
    # moved into its test (dead-code plan, Stage D).
    "own_format": 43,
    "platform": 21,
    # 9: main replaced the two fnmatch frozen-namespace matchers with one
    # policy.frozen_namespace helper.
    "user_rule": 9,
    "sniff": 5,
}


def _resolved() -> dict[str, str]:
    reg = name_heuristic_registry()
    out: dict[str, str] = {}
    for site in heuristic_site_inventory():
        hid = resolve_site(site, reg)
        if hid is not None:
            out[site] = hid
    return out


@pytest.mark.repo_scan
def test_inventory_is_derived_and_nontrivial() -> None:
    inventory = heuristic_site_inventory()
    assert len(scanned_files()) > 100
    assert len(inventory) > 150
    # The scan finds the #1411/#1231 heuristics by itself (not via a table).
    for needle in (
        "abicheck.compare.enum_sentinel::<module>::vocab:_SENTINEL_TAIL_TOKENS",
        "abicheck.diff_serialization::<module>::vocab:_TAG_SUFFIX_PATTERNS",
        "abicheck.diff_namespaces::<module>::vocab:DEFAULT_EXPERIMENTAL_NAMESPACES",
        "abicheck.model.symbol_ownership::<module>::vocab:DEFAULT_INTERNAL_NAMESPACES",
    ):
        assert needle in inventory


@pytest.mark.repo_scan
def test_every_site_is_registered_or_exempt() -> None:
    """The gate: an unregistered spelling check in compare/, policy/ or a
    diff_* module fails here until it is registered at runtime (with its
    effect, and a structural fact if it can raise severity) or given an
    exemption row that states why it is not a naming heuristic."""
    resolved = _resolved()
    uncategorized = heuristic_site_inventory() - set(HEURISTICS) - set(resolved)
    assert len(uncategorized) <= UNCATEGORIZED_CEILING, (
        "unregistered name/spelling heuristic site(s): register each through "
        "abicheck.model.name_heuristics (and list its module in "
        "policy.name_heuristics.HEURISTIC_OWNER_MODULES), or add an exemption "
        f"row to tests/_family_f4_registry.HEURISTICS: {sorted(uncategorized)}"
    )


@pytest.mark.repo_scan
def test_registry_has_no_stale_rows() -> None:
    inventory = heuristic_site_inventory()
    stale = set(HEURISTICS) - inventory
    assert not stale, f"HEURISTICS rows for sites that no longer exist: {sorted(stale)}"
    registered = set(HEURISTICS) & set(_resolved())
    assert not registered, (
        f"exemption rows for sites a registered heuristic owns: {sorted(registered)}"
    )


@pytest.mark.repo_scan
def test_every_registered_heuristic_owns_a_site() -> None:
    registry = name_heuristic_registry()
    owned = set(_resolved().values())
    owning_callables = {
        f
        for hid in owned
        if hid in registry
        for f in heuristic_callables(registry[hid])
    }
    #: Spelling checks the AST inventory cannot see (no regex/affix/vocab).
    uninventoried = {
        "bundle_soname_cohort": "the cohort stem is name.split('.', 1)[0]",
    }
    phantom = {
        hid
        for hid, h in registry.items()
        if hid not in owned
        and not set(heuristic_callables(h)) & owning_callables
        and hid not in uninventoried
    }
    assert not phantom, f"registered heuristics that own no inventoried site: {phantom}"
    assert set(uninventoried) <= set(registry)


@pytest.mark.repo_scan
def test_exemption_rows_are_well_formed() -> None:
    bad = {k: v for k, v in HEURISTICS.items() if v not in EXEMPTION_REASONS}
    assert not bad, f"rows naming no exemption category: {bad}"
    assert all(r.strip() for r in EXEMPTION_REASONS.values())


def test_exemptions_are_shrink_only() -> None:
    counts = collections.Counter(HEURISTICS.values())
    over = {c: n for c, n in counts.items() if n > EXEMPTION_CEILINGS.get(c, 0)}
    assert not over, f"exemptions grew past their ceiling: {over}"
    slack = {c: EXEMPTION_CEILINGS[c] - counts.get(c, 0) for c in EXEMPTION_CEILINGS}
    assert not any(slack.values()), f"lower EXEMPTION_CEILINGS to match: {slack}"


def test_runtime_registry_is_well_formed() -> None:
    assert registry_problems() == []


def test_every_severity_raising_heuristic_has_cells() -> None:
    raising = severity_raising_heuristics()
    assert raising, "no severity-raising heuristic registered"
    assert set(raising) <= set(COVERED), set(raising) - set(COVERED)
    for hid, h in raising.items():
        assert h.fact.id.count(".") >= 2, hid
        assert not hasattr(h, "matches"), f"{hid} exposes a name-only query"


def test_covered_heuristics_are_registered_and_name_cells() -> None:
    registry = name_heuristic_registry()
    referenced: set[str] = set()
    for name, h in COVERED.items():
        assert name in registry, f"COVERED names an unregistered heuristic {name}"
        assert h.fp_cells and h.fn_cells and h.control_cells, name
        cells = (*h.fp_cells, *h.fn_cells, *h.control_cells)
        missing = [c for c in cells if c not in CELLS]
        assert not missing, f"{name}: unknown cells {missing}"
        referenced.update(cells)
    assert referenced == set(CELLS), f"orphan cells: {set(CELLS) - referenced}"


# --------------------------------------------------------------------------
# No name reaches a breaking finding except through a declared path
# --------------------------------------------------------------------------


@pytest.mark.repo_scan
def test_no_name_heuristic_reaches_an_undeclared_breaking_kind() -> None:
    """Every BREAKING/API_BREAK kind reachable from a function that consults
    a registered handle is either one a lowering handle there declares it
    demotes (``lowers_from``), or one a severity-raising handle confirmed in
    the same reach declares it gates (``raises``)."""
    violations = raise_violations(dict(name_heuristic_registry()))
    assert not violations, (
        "a name heuristic can reach a breaking finding with no structural "
        "fact: register it through register_severity_raising_heuristic "
        "(raises=...) and gate the emission on .confirmed(), or declare the "
        f"kind in lowers_from if the name only demotes it: {violations}"
    )


@pytest.mark.repo_scan
def test_scan_reports_a_lowering_handle_that_stops_declaring_its_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Negative control: without its declaration, reserved_field's
    # neighbouring FIELD_RENAMED emission is a violation.
    registry = dict(name_heuristic_registry())
    monkeypatch.setattr(registry["reserved_field"], "lowers_from", ())
    assert any("FIELD_RENAMED" in v for v in raise_violations(registry))


@pytest.mark.repo_scan
def test_raising_declarations_are_live() -> None:
    reached = raising_declarations_reached()
    stale = {
        hid: sorted(set(h.raises) - reached.get(hid, set()))
        for hid, h in severity_raising_heuristics().items()
        if set(h.raises) - reached.get(hid, set())
    }
    assert not stale, f"raises= kinds no .confirmed() call can reach: {stale}"


@pytest.mark.repo_scan
def test_detectors_never_touch_heuristic_internals() -> None:
    """The name-only check behind a handle is reachable only from the
    registry's own modules and this harness."""
    allowed = {
        PKG / "model" / "name_heuristics.py",
        PKG / "policy" / "name_heuristics.py",
    }
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        if path in allowed or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\._matcher\b|\bheuristic_callables\b", text):
            offenders.append(str(path.relative_to(PKG.parent)))
    assert not offenders, offenders


# --------------------------------------------------------------------------
# The registration API cannot express "raise severity" without a fact
# --------------------------------------------------------------------------


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe registrations must not leak into the process-wide registry."""
    import abicheck.model.name_heuristics as api

    name_heuristic_registry()  # load every real owner first
    monkeypatch.setattr(api, "_REGISTRY", dict(api._REGISTRY))


def test_effect_vocabulary_has_no_raise() -> None:
    assert {e.value for e in NameHeuristicEffect} == {
        "lower_confidence",
        "route_to_review",
    }


@pytest.mark.parametrize("effect", ["raise_severity", "lower_confidence", None])
@pytest.mark.usefixtures("isolated_registry")
def test_lowering_registration_rejects_a_non_enum_effect(effect: object) -> None:
    with pytest.raises(TypeError, match="effect must be a NameHeuristicEffect"):
        register_name_heuristic(
            "probe_effect",
            owner="abicheck.probe",
            effect=effect,  # type: ignore[arg-type]
            description="probe",
            matcher=bool,
        )


@pytest.mark.parametrize("fact", [None, bool, "compare.x.y"])
@pytest.mark.usefixtures("isolated_registry")
def test_raising_registration_requires_a_structural_fact(fact: object) -> None:
    with pytest.raises(TypeError, match="must name a StructuralFact"):
        register_severity_raising_heuristic(
            "probe_raise",
            owner="abicheck.probe",
            fact=fact,  # type: ignore[arg-type]
            raises=("FUNC_REMOVED",),
            description="probe",
            matcher=bool,
        )


@pytest.mark.parametrize("bad", ["holds", "Compare.x.y", "compare.x", ""])
def test_structural_fact_needs_a_dotted_name(bad: str) -> None:
    with pytest.raises(ValueError):
        StructuralFact(bad, bool)


@pytest.mark.usefixtures("isolated_registry")
def test_raising_handle_is_name_and_fact() -> None:
    h = register_severity_raising_heuristic(
        "probe_truth_table",
        owner="abicheck.probe",
        fact=StructuralFact("compare.probe.holds", bool),
        raises=("FUNC_REMOVED",),
        description="probe",
        matcher=lambda name: name.startswith("x"),
    )
    # Exhaustive truth table: only name AND fact confirms.
    for name in ("x1", "y1"):
        for fact in (True, False):
            assert h.confirmed(name, fact) is (name == "x1" and fact)


@pytest.mark.usefixtures("isolated_registry")
def test_lazy_fact_input_is_built_only_after_the_name_nominates() -> None:
    built: list[str] = []
    h = register_severity_raising_heuristic(
        "probe_lazy",
        owner="abicheck.probe",
        fact=StructuralFact("compare.probe.holds", bool),
        raises=("FUNC_REMOVED",),
        description="probe",
        matcher=lambda name: name.startswith("x"),
    )
    for name in ("y1", "x1"):
        lazy = LazyFactInput(lambda name=name: built.append(name) or True)
        assert h.confirmed(name, lazy) is (name == "x1")
    assert built == ["x1"]


@pytest.mark.usefixtures("isolated_registry")
def test_owner_collision_is_rejected() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_name_heuristic(
            "enum_sentinel",
            owner="abicheck.someone_else",
            effect=NameHeuristicEffect.LOWER_CONFIDENCE,
            description="probe",
            matcher=bool,
        )


def test_moving_a_spelling_check_out_of_its_matcher_unresolves_it() -> None:
    # The resolver keys on the registered matcher/helpers, so a copy of the
    # same check in an unregistered function is not covered by it.
    site = (
        "abicheck.compare.naming_conventions::_unregistered_copy::affix.startswith:'x'"
    )
    assert resolve_site(site) is None
    vocab = (
        "abicheck.compare.naming_conventions::<module>::vocab:_INTERNAL_NAME_PATTERNS"
    )
    assert resolve_site(vocab) == "elf_internal_symbol_name"


# --------------------------------------------------------------------------
# Oracle cells
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cell_id", sorted(CELLS))
def test_heuristic_cell_oracle(cell_id: str) -> None:
    assert oracle_violations(cell_id, CELLS[cell_id]) == []


# --------------------------------------------------------------------------
# Seeded mutants: reintroduce a historical heuristic bug, the oracle reports it
# --------------------------------------------------------------------------


def _violations(prefix: str) -> list[str]:
    return [
        v
        for cid, cell in CELLS.items()
        if cid.startswith(prefix)
        for v in oracle_violations(cid, cell)
    ]


def test_seeded_mutant_raw_substring_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-token matching: ``max``/``end``/``last`` anywhere in the name."""
    monkeypatch.setattr(
        ENUM_SENTINEL,
        "_matcher",
        lambda n: any(t in n.lower() for t in ("max", "last", "count", "end", "num")),
    )
    found = _violations("sentinel.fn.")
    assert len({v.split(":", 1)[0] for v in found}) == 3, found


def test_seeded_mutant_1411_bare_tag_type_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-#1411: any enum type spelled ``*_tag`` was a serialization registry."""
    monkeypatch.setattr(
        diff_serialization_mod,
        "_enum_type_is_tag_registry",
        lambda n: n.rsplit("::", 1)[-1].lower().removesuffix("_t").endswith("tag"),
    )
    assert any(
        "unexpected serialization_tag_changed" in v for v in _violations("tag.fp.")
    )


def test_seeded_mutant_1411_promotion_without_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``preview::f(int)`` "promoted" to ``v1::f(double)`` by leaf name alone."""

    def leaf_only(item, removed, root):  # type: ignore[no-untyped-def]
        if item.leaf != removed.leaf:
            return None
        path = _scope_path_for_mutant(item)
        return path if path and path[0] == root else None

    monkeypatch.setattr(
        diff_namespaces_mod, "_same_leaf_and_signature_under_root", leaf_only
    )
    found = _violations("experimental.fp.")
    assert any("experimental_removed_without_replacement" in v for v in found), found


def test_seeded_mutant_1231_substring_internal_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal-namespace convention matched as a substring, not a segment."""
    monkeypatch.setattr(
        internal_leak_mod,
        "is_internal_type",
        lambda name, *a, **k: any(s in name for s in ("detail", "impl", "internal")),
    )
    assert _violations("internal_ns.fn."), "oracle missed a substring-matched namespace"


def test_unmutated_pipeline_passes_the_mutant_cells() -> None:
    for prefix in ("sentinel.fn.", "tag.fp.", "experimental.fp.", "internal_ns.fn."):
        assert _violations(prefix) == [], prefix
