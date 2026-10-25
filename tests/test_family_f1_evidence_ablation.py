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

"""Harness H1 -- evidence ablation for defect family F1 ("Unknown != value").

Plan: ``docs/contribute/plans/defect-family-harnesses.md`` section H1. The
family: an evidence input that was unread, failed, or never produced is
consumed as a *definite* value. September 2026's chain -- #1384 (export
fact ``present(False)`` whenever a binary was supplied), #1385 (unread OLD
export table suppressing real exits), #1388/#1398 (unread release member),
#1389 (L5 producers), #1033 -> #1075 (``Fact`` collapse fabricating
vtable/bases/va_list findings), #1200 (producer-capability gap compared
raw) -- was fixed one instance at a time. This module states the class once.

**Inventory (mechanical).** Every ``Fact[...]``-annotated field of every
dataclass reachable from ``AbiSnapshot`` (``fact_site_inventory``), plus
every optional evidence container on ``AbiSnapshot`` itself
(``container_site_inventory``). Both are derived from annotations, so a new
``Fact`` field or container joins the inventory without anyone listing it,
and ``test_inventory_is_complete`` then fails until the site is either
exercised by the corpus or named in ``UNCOVERED`` with a reason. ``UNCOVERED``
is shrink-only: an entry naming a site that no longer exists, or one the
corpus now exercises, fails too.

**Ablations.** For each corpus pair (identical, compatible, and real breaks
across functions, variables, records, bases/vtables, enums) and each side
(OLD, NEW, both): every ``Fact`` site set to ``NOT_COLLECTED`` /
``UNSUPPORTED`` / ``FAILED`` on every instance (rebuilt with
``dataclasses.replace`` so each owner's legacy/``Fact`` bridge runs as it
would for a real producer), and each covered container dropped / emptied /
read-failed (the last one running the builders' real
``finish_binary_snapshot`` tail).

**Oracles** (``_family_f1_harness.oracle_violations``), written against raw
``DiffResult`` fields, never the predicates the pipeline decides with:

(a) a BREAKING/API_BREAK run never becomes NO_CHANGE/COMPATIBLE/
    COMPATIBLE_WITH_RISK unless the report *states a gap*: lower overall
    confidence, a new coverage warning, a changed analysis-assurance record,
    or a higher ADR-049 contract-coverage exit floor;
(b) BREAKING-kind findings under ablation are a subset of the full run's;
(c) the identical pair never becomes BREAKING/API_BREAK.

Adjustments to the plan's oracles, and why:

* The plan's "``assurance(ablated) <= assurance(full)``" is not checked as a
  separate ordering: ``AnalysisAssurance`` has no total order to compare on.
  (a) instead requires that *some* gap marker moved whenever the verdict got
  cleaner, which is the user-visible half of the claim.
* The plan's "subset, plus findings explicitly marked ``evidence_gap``" --
  ``Change`` carries no such marker today, so (b) is a subset modulo one
  rule: a DWARF-layout kind for a record whose header-layer equivalent the
  full run reported (``_SAME_OBSERVATION``) is the same observation from the
  other layer -- what happens when an unknown ``CanonicalEntity.size_bits``
  makes the header detector stand down. None of
  the covered ablations needs the escape hatch.
* The plan's "truncated / short decode" ablation is modelled only as an
  *unread* export table, not a silently half-read one: ``ElfMetadata.symbols``
  carries no completeness marker, so a table holding half its entries is
  indistinguishable from a release that removed the other half, and reporting
  those removals is correct. F1 concerns signalled unknowns.
* "Raises" is not an ablation here: ``compare()`` consumes snapshots, and a
  raising extractor surfaces before a snapshot exists. Its snapshot-level
  outcome is the ``FAILED`` status, which is ablated.

**Seeded mutants** (vacuity guard): the pre-#1033 bridge that collapsed an
unknown ``Fact`` into a confirmed default, and the pre-#1384 reading of an
unknown export fact as confirmed-absent, are patched back in; the oracles
must report them.

**Real finding, fixed** (formerly ``KNOWN_VIOLATIONS``; now the regular
tests over ``HEADER_ORIGIN_SEED_CELLS``):
the public-surface closure seeds header-origin enums and records from the
legacy ``source_header`` string (``policy/public_surface_closure.py``,
``en.source_header`` / ``_record_is_confirmed_public_seed``, which also
requires ``rec.qualified_name``). When ``source_header_fact`` -- or, for a
record, ``qualified_name_fact`` -- is unknown on both sides the legacy value
is ``None``,
the type is judged ``non-public-type`` and a real break is scoped out --
NO_CHANGE with no coverage warning, no confidence drop and no assurance note.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from _family_f1_harness import (
    CONFIGS,
    CONTAINER_ABLATIONS,
    CORPUS,
    SIDES,
    UNKNOWN_FACTS,
    Outcome,
    ablate_fact,
    apply_sided,
    container_site_inventory,
    fact_site_inventory,
    gap_stated,
    oracle_violations,
    outcome,
    present_fact_count,
    present_fact_sites,
)

import abicheck.model.declarations as declarations_mod
import abicheck.model.entities as entities_mod
import abicheck.model.fact as fact_mod
import abicheck.model.surface_facts as surface_facts_mod
from abicheck.model import Fact, FactStatus
from abicheck.model.snapshot import AbiSnapshot

# --------------------------------------------------------------------------
# Inventory bookkeeping
# --------------------------------------------------------------------------

#: Sites the harness cannot ablate yet. Shrink-only: see
#: ``test_uncovered_is_shrink_only``.
_DECLARATION_PROJECTION = (
    "comparison-time projection of the paired Function/Variable "
    "(compare/function_signature.py, compare/variables.py re-project the "
    "declaration over its occurrence), so the evidence is the declaration's "
    "own field, which H1 ablates; no producer persists this fact any more"
)

UNCOVERED: dict[str, str] = {
    "MachoMetadata.weak_dependent_libs_fact": (
        "read only by the release dependency graph's Mach-O facts "
        "(workflows/release_dependency_facts.py), whose unit and CLI tests "
        "(test_release_dependency_facts.py, test_release_html_pe_macho_deps.py) "
        "cover recorded, legacy-incomplete and absent inputs; no corpus Mach-O "
        "pair carries LC_LOAD_WEAK_DYLIB yet"
    ),
    "AbiSnapshot.header_declaration_ids": (
        "runtime-only L2 ids a header-depth projection keeps when it clears "
        "surface_graph; never stored, so no stored-corpus ablation reaches it. "
        "Covered by test_template_instance_absence_shapes.py at both depths"
    ),
    "AbiSnapshot.projection_unavailable": (
        "runtime-only record of the facts a depth projection cleared; never "
        "stored. Covered by test_depth_detector_input_contract.py at every "
        "rung"
    ),
    "RecordType.template_param_defaults_fact": (
        "read only by compare/template_param_defaults, whose unit tests "
        "(test_template_param_defaults.py) cover both recorded, UNKNOWN and "
        "one-side-unrecorded inputs; no corpus pair records defaults on both "
        "sides yet"
    ),
    "Function.template_param_defaults_fact": (
        "read only by compare/template_param_defaults' function-template "
        "detector, whose unit tests (test_function_template_param_defaults.py) "
        "cover changed, added, removed, unchanged and overloaded patterns; no "
        "corpus pair records function-template defaults on both sides yet"
    ),
    "RecordType.specialization_declared_fact": (
        "read only by compare/template_instance_absence, whose own unit test "
        "(test_class_template_specialization_absence_unit.py) ablates every "
        "status: only PRESENT(False) changes a finding"
    ),
    "CanonicalEntity.access": _DECLARATION_PROJECTION,
    "CanonicalEntity.contract_attributes": _DECLARATION_PROJECTION,
    "CanonicalEntity.declared_alignment_bits": _DECLARATION_PROJECTION,
    "CanonicalEntity.deleted_from_dwarf": _DECLARATION_PROJECTION,
    "CanonicalEntity.deprecated": _DECLARATION_PROJECTION,
    "CanonicalEntity.exception_spec": _DECLARATION_PROJECTION,
    "CanonicalEntity.hidden_friend_owner": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_deleted": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_explicit": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_extern_c": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_hidden_friend": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_inline": _DECLARATION_PROJECTION,
    "Function.is_inline_fact": (
        "persisted Fact[bool] sibling added in schema v60; the H1 corpus has no "
        "inline-linkage pair that differs across its operands, so the ablation "
        "cannot reach it yet. The inline-transition detector's own tests cover it"
    ),
    "CanonicalEntity.is_noexcept": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_override": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_variadic": _DECLARATION_PROJECTION,
    "CanonicalEntity.is_virtual": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_defaults": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_kinds": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_names": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_pointer_depths": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_restrict": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_type_spellings": _DECLARATION_PROJECTION,
    "CanonicalEntity.parameter_va_list": _DECLARATION_PROJECTION,
    "CanonicalEntity.ref_qualifier": _DECLARATION_PROJECTION,
    "CanonicalEntity.return_pointer_depth": _DECLARATION_PROJECTION,
    "CanonicalEntity.return_type_spelling": _DECLARATION_PROJECTION,
    "CanonicalEntity.vtable_index": _DECLARATION_PROJECTION,
}

#: The real current violations this harness found (see module docstring).
#: ``(case, site, side)``. Empty since the header-origin seeding fix; the
#: cells it used to hold are pinned as regular tests by
#: ``HEADER_ORIGIN_SEED_CELLS`` below.
KNOWN_VIOLATIONS: frozenset[tuple[str, str, str]] = frozenset()

#: Formerly-known violations: public_surface_closure seeded header-origin
#: enums/records from the legacy source_header / qualified_name strings, so an
#: unknown source_header_fact (or, for records, qualified_name_fact) on both
#: sides demoted the type to non-public-type and scoped a real break out to
#: NO_CHANGE with no stated gap.
HEADER_ORIGIN_SEED_CELLS: tuple[tuple[str, str, str], ...] = (
    ("enum_value_changed", "EnumType.source_header_fact", "both"),
    ("header_record_changed", "RecordType.qualified_name_fact", "both"),
    ("header_record_changed", "RecordType.source_header_fact", "both"),
)

_FACT_SITES = fact_site_inventory()
_STATUSES = tuple(UNKNOWN_FACTS)


def _exercised_fact_sites() -> set[str]:
    present: set[tuple[type, str]] = set()
    for old, new in CORPUS.values():
        present |= present_fact_sites(old) | present_fact_sites(new)
    return {name for name, site in _FACT_SITES.items() if site in present}


_EXERCISED_FACTS = _exercised_fact_sites()


def test_one_walk_site_census_matches_the_per_site_count() -> None:
    """``present_fact_sites`` (one walk per snapshot) decides collection-time
    parametrization, so it must answer exactly what the per-site walk
    ``present_fact_count`` answers, for every corpus snapshot and site."""
    checked = 0
    for old, new in CORPUS.values():
        for snap in (old, new):
            present = present_fact_sites(snap)
            for site in _FACT_SITES.values():
                assert (site in present) == bool(present_fact_count(snap, site)), site
                checked += 1
    assert checked == 2 * len(CORPUS) * len(_FACT_SITES)
    assert _EXERCISED_FACTS, "census found no exercised site; the oracle is vacuous"


_EXERCISED_CONTAINERS = set(CONTAINER_ABLATIONS)
_FULL: dict[tuple[str, str], Outcome] = {}


def _full(case: str, config: str = "default") -> Outcome:
    key = (case, config)
    if key not in _FULL:
        _FULL[key] = outcome(*CORPUS[case], config)
    return _FULL[key]


def _ablated_pairs(
    case: str, site: str, status: str
) -> dict[str, tuple[AbiSnapshot, AbiSnapshot]]:
    """The (OLD, NEW) pair each side ablation of one (case, site, status)
    compares. An ablated snapshot depends only on the side snapshot, the
    site and the status, so OLD and NEW are each ablated once and shared by
    the ``old``/``new``/``both`` pairs -- two rebuilds instead of four.
    Sharing is sound because ``compare()`` does not mutate its inputs, which
    this harness already relies on: the unablated side of every cell is the
    same ``CORPUS`` object throughout."""
    old, new = CORPUS[case]
    fact = UNKNOWN_FACTS[status]()
    a_old = ablate_fact(old, _FACT_SITES[site], fact)[0]
    a_new = ablate_fact(new, _FACT_SITES[site], fact)[0]
    return {"old": (a_old, new), "new": (old, a_new), "both": (a_old, a_new)}


def _fact_cell(
    case: str, site: str, status: str, side: str, config: str = "default"
) -> list[str]:
    fact = UNKNOWN_FACTS[status]()
    old, new = apply_sided(
        *CORPUS[case], side, lambda s: ablate_fact(s, _FACT_SITES[site], fact)[0]
    )
    return oracle_violations(case, _full(case, config), outcome(old, new, config))


# --------------------------------------------------------------------------
# Inventory completeness
# --------------------------------------------------------------------------


def test_inventory_is_derived_and_nontrivial() -> None:
    # The inventory must see the historical F1 sites, or it is inventorying
    # something else.
    for site in (
        "Function.binary_exported_fact",
        "RecordType.bases_fact",
        "RecordType.vtable_fact",
        "Param.is_va_list_fact",
    ):
        assert site in _FACT_SITES
    assert "AbiSnapshot.elf" in container_site_inventory()
    assert len(_FACT_SITES) >= 50


def test_inventory_is_complete() -> None:
    inventory = set(_FACT_SITES) | set(container_site_inventory())
    unaccounted = inventory - _EXERCISED_FACTS - _EXERCISED_CONTAINERS - set(UNCOVERED)
    assert not unaccounted, (
        f"new evidence site(s) neither exercised by H1's corpus nor listed in UNCOVERED: {sorted(unaccounted)}"
    )


def test_uncovered_is_shrink_only() -> None:
    inventory = set(_FACT_SITES) | set(container_site_inventory())
    stale = set(UNCOVERED) - inventory
    assert not stale, f"UNCOVERED names sites that no longer exist: {sorted(stale)}"
    now_covered = set(UNCOVERED) & (_EXERCISED_FACTS | _EXERCISED_CONTAINERS)
    assert not now_covered, (
        f"UNCOVERED sites the corpus now exercises -- remove them: {sorted(now_covered)}"
    )
    assert all(reason.strip() for reason in UNCOVERED.values())


def test_corpus_has_breaks_and_identical_pair() -> None:
    verdicts = {case: _full(case).verdict.value for case in CORPUS}
    assert verdicts["identical"] == "NO_CHANGE"
    assert sum(v == "BREAKING" for v in verdicts.values()) >= 8
    # every breaking case has at least one BREAKING-kind finding to lose
    for case, verdict in verdicts.items():
        if verdict == "BREAKING":
            assert _full(case).breaking, case


# --------------------------------------------------------------------------
# Fact ablations
# --------------------------------------------------------------------------


@pytest.mark.parametrize("site", sorted(_EXERCISED_FACTS))
def test_fact_ablation_oracles(site: str) -> None:
    """Default lane: every exercised site x case x side, status rotated per
    (site, case) so all three statuses are reached across the sweep; the
    ``slow`` sweep below runs the full status product."""
    idx = sorted(_EXERCISED_FACTS).index(site)
    violations = {}
    for j, case in enumerate(CORPUS):
        status = _STATUSES[(idx + j) % len(_STATUSES)]
        pairs = _ablated_pairs(case, site, status)
        for side in SIDES:
            if (case, site, side) in KNOWN_VIOLATIONS:
                continue
            v = oracle_violations(case, _full(case), outcome(*pairs[side]))
            if v:
                violations[(case, status, side)] = v
    assert not violations, violations


#: What each configuration's full status product must find, per case. Under
#: ``contract_exports`` both known cells are out of the export domain in the
#: full run already (NO_CHANGE), so nothing is left to silence; under
#: ``contract_public`` -- the domain that reads the header-identifier index
#: and every captured ``*.type_identities_fact`` -- an unknown fact must never
#: let a break through as clean without a stated gap.
_FULL_PRODUCT_EXPECTED: dict[str, Any] = {
    "default": lambda case: {cell for cell in KNOWN_VIOLATIONS if cell[0] == case},
    "contract_exports": lambda case: set(),
    "contract_public": lambda case: set(),
}


def _full_product_cells(case: str, sites: list[str] | None = None) -> Any:
    """Yield ``(config, site, status, side, Outcome)`` for every cell of *case*.

    The same cells ``_fact_cell`` defines; the ablated pair of each
    (site, status) is built once (``_ablated_pairs``) and shared by all
    three configurations too -- 396 rebuilds per case instead of 2,376.
    """
    for site in sorted(_EXERCISED_FACTS) if sites is None else sites:
        for status in _STATUSES:
            pairs = _ablated_pairs(case, site, status)
            for config in CONFIGS:
                for side in SIDES:
                    yield config, site, status, side, outcome(*pairs[side], config)


def _full_product_sweep(case: str) -> dict[str, set[tuple[str, str, str]]]:
    found: dict[str, set[tuple[str, str, str]]] = {c: set() for c in CONFIGS}
    for config, site, _status, side, result in _full_product_cells(case):
        if oracle_violations(case, _full(case, config), result):
            found[config].add((case, site, side))
    return found


# One test per corpus case, all three configurations inside it. As three
# whole-corpus tests these took ~180-210s each and pinned one xdist worker
# while the others idled; per case the 27 units spread across workers, and
# keeping the configurations together is what lets them share ablations.
# The per-case assertion is that case's slice of the original set equality,
# so the union over cases is exactly the original claim.
@pytest.mark.slow
@pytest.mark.parametrize("case", list(CORPUS))
def test_fact_ablation_full_product(case: str) -> None:
    assert set(_FULL_PRODUCT_EXPECTED) == set(CONFIGS)
    found = _full_product_sweep(case)
    expected = {c: _FULL_PRODUCT_EXPECTED[c](case) for c in CONFIGS}
    assert found == expected


def test_shared_ablations_match_independently_built_cells() -> None:
    """Every shared-ablation cell's ``Outcome`` equals the one built the
    unshared way (``apply_sided`` + a fresh ``ablate_fact`` per cell).
    Compared on outcomes, not on oracle violations: with no known violation
    left, violation sets are empty either way and would prove nothing. A
    slice chosen so that a mis-paired side or configuration changes the
    outcome."""
    case = "header_record_changed"
    old, new = CORPUS[case]
    # Sites whose ablation gives the three sides different outcomes, on a
    # case whose configurations already disagree -- so a swapped side or a
    # swapped configuration changes the compared Outcome.
    sites = ["RecordType.qualified_name_fact", "RecordType.source_header_fact"]
    assert set(sites) <= _EXERCISED_FACTS
    cells = list(_full_product_cells(case, sites))
    assert len(cells) == len(sites) * len(_STATUSES) * len(SIDES) * len(CONFIGS)
    by_config: dict[str, set[Outcome]] = {c: set() for c in CONFIGS}
    by_side: dict[tuple[str, str, str], set[Outcome]] = {}
    for config, site, status, side, shared in cells:
        fact = UNKNOWN_FACTS[status]()
        o, n = apply_sided(
            old, new, side, lambda snap: ablate_fact(snap, _FACT_SITES[site], fact)[0]
        )
        assert shared == outcome(o, n, config), (config, site, status, side)
        by_config[config].add(shared)
        by_side.setdefault((config, site, status), set()).add(shared)
    # Non-vacuity: sides and configurations really produce different
    # outcomes on this slice, so mis-pairing either would be caught above.
    assert any(len(v) > 1 for v in by_side.values())
    assert len({frozenset(v) for v in by_config.values()}) > 1


@pytest.mark.parametrize(("case", "site", "side"), HEADER_ORIGIN_SEED_CELLS)
@pytest.mark.parametrize("status", _STATUSES)
def test_header_origin_seed_unknown_fact_keeps_break(
    case: str, site: str, side: str, status: str
) -> None:
    assert _fact_cell(case, site, status, side) == []


def test_no_known_violations_remain() -> None:
    assert KNOWN_VIOLATIONS == frozenset()


# --------------------------------------------------------------------------
# Container ablations
# --------------------------------------------------------------------------


_CONTAINER_CELLS = [
    (site, name) for site, ops in CONTAINER_ABLATIONS.items() for name in ops
]


@pytest.mark.parametrize(("site", "ablation"), _CONTAINER_CELLS)
@pytest.mark.parametrize("config", sorted(CONFIGS))
def test_container_ablation_oracles(site: str, ablation: str, config: str) -> None:
    op = CONTAINER_ABLATIONS[site][ablation]
    applied = 0
    violations = {}
    for case, (old, new) in CORPUS.items():
        for side in SIDES:
            o, n = apply_sided(old, new, side, lambda s: op(s) or s)
            applied += (o is not old) + (n is not new)
            v = oracle_violations(case, _full(case, config), outcome(o, n, config))
            if v:
                violations[(case, side)] = v
    assert applied, "ablation never applied: the corpus lacks this container"
    assert not violations, violations


# --------------------------------------------------------------------------
# Oracle self-checks and seeded mutants (vacuity guard)
# --------------------------------------------------------------------------


def _mk(
    verdict: str, breaking: frozenset[tuple[str, str]] = frozenset(), **kw: Any
) -> Outcome:
    from abicheck.checker import Verdict

    base = {
        "confidence": 3,
        "warnings": frozenset(),
        "assurance": (),
        "coverage_floor": 0,
    }
    base.update(kw)
    return Outcome(verdict=Verdict(verdict), breaking=breaking, **base)


@pytest.mark.parametrize(
    ("full", "ablated", "expect"),
    [
        (_mk("BREAKING", frozenset({("k", "s")})), _mk("NO_CHANGE"), ["(a)"]),
        (_mk("BREAKING", frozenset({("k", "s")})), _mk("NO_CHANGE", confidence=2), []),
        (
            _mk("BREAKING", frozenset({("k", "s")})),
            _mk("COMPATIBLE", warnings=frozenset({"w"})),
            [],
        ),
        (
            _mk("BREAKING", frozenset({("k", "s")})),
            _mk("COMPATIBLE", coverage_floor=1),
            [],
        ),
        (
            _mk("BREAKING", frozenset({("k", "s")})),
            _mk("COMPATIBLE", assurance=("partial",)),
            [],
        ),
        (_mk("COMPATIBLE"), _mk("BREAKING", frozenset({("k", "s")})), ["(b)"]),
        # The DWARF layer reporting the size change the header layer reported
        # for the same record is the same observation, not a fabrication...
        (
            _mk("BREAKING", frozenset({("type_size_changed", "S")})),
            _mk("BREAKING", frozenset({("struct_size_changed", "S")})),
            [],
        ),
        # ...but only for that record, and only for that pairing.
        (
            _mk("BREAKING", frozenset({("type_size_changed", "S")})),
            _mk("BREAKING", frozenset({("struct_size_changed", "T")})),
            ["(b)"],
        ),
        (
            _mk("BREAKING", frozenset({("type_field_removed", "S")})),
            _mk("BREAKING", frozenset({("struct_size_changed", "S")})),
            ["(b)"],
        ),
        (_mk("NO_CHANGE"), _mk("API_BREAK"), []),
    ],
)
def test_oracle_decision_table(
    full: Outcome, ablated: Outcome, expect: list[str]
) -> None:
    got = [v[:3] for v in oracle_violations("other", full, ablated)]
    assert got == expect
    assert gap_stated(full, ablated) is (
        expect != ["(a)"]
        and full.verdict.value == "BREAKING"
        and ablated.verdict.value != "BREAKING"
    )


def test_identical_oracle_c() -> None:
    assert any(
        v.startswith("(c)")
        for v in oracle_violations("identical", _mk("NO_CHANGE"), _mk("API_BREAK"))
    )


def _any_violation(
    sites: list[str], cases: list[str]
) -> list[tuple[str, str, str, str]]:
    hits = []
    for case, site, side in itertools.product(cases, sites, SIDES):
        if (case, site, side) in KNOWN_VIOLATIONS:
            continue
        if _fact_cell(case, site, "not_collected", side):
            hits.append((case, site, side, "not_collected"))
    return hits


def test_seeded_mutant_pre_1033_fact_collapse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-#1033: an unknown ``Fact`` bridged to a *confirmed* default (e.g.
    ``bases`` not collected read as ``present([])``), fabricating base/vtable
    findings. The harness must catch it."""
    original = fact_mod.bridge_legacy_and_fact

    def collapsing(legacy: Any, explicit: Any, omitted: Any, default: Any) -> Any:
        if explicit is not None and explicit.status is not FactStatus.PRESENT:
            return default, Fact.present(default)
        return original(legacy, explicit, omitted, default)

    for mod in (declarations_mod, entities_mod):
        monkeypatch.setattr(mod, "bridge_legacy_and_fact", collapsing)
    _FULL.clear()
    try:
        hits = _any_violation(
            ["RecordType.bases_fact", "RecordType.vtable_fact"],
            ["identical", "struct_field_type"],
        )
    finally:
        _FULL.clear()
    assert hits, "H1 failed to catch the seeded pre-#1033 Fact-collapse mutant"


def test_seeded_mutant_pre_1384_unknown_export_as_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-#1384/#1385: an unknown export fact read as confirmed-absent
    (``present(False)``). The harness must catch it."""
    original = surface_facts_mod.binary_exported

    def collapsing(decl: Any) -> Fact[bool]:
        fact = original(decl)
        if fact.status in (FactStatus.PRESENT, FactStatus.PARTIAL):
            return fact
        return Fact.present(False)

    monkeypatch.setattr(surface_facts_mod, "binary_exported", collapsing)
    _FULL.clear()
    try:
        hits = _any_violation(
            ["Function.binary_exported_fact", "Variable.binary_exported_fact"],
            ["identical", "func_removed"],
        )
    finally:
        _FULL.clear()
    assert hits, "H1 failed to catch the seeded pre-#1384 export-collapse mutant"


def test_unmutated_pipeline_passes_the_mutant_cells() -> None:
    # The two mutant probes above are only evidence if the same cells are
    # clean without the mutant.
    assert not _any_violation(
        ["RecordType.bases_fact", "RecordType.vtable_fact"],
        ["identical", "struct_field_type"],
    )
    assert not _any_violation(
        ["Function.binary_exported_fact", "Variable.binary_exported_fact"],
        ["identical", "func_removed"],
    )


# ``ElfSymbol.version_binding`` (bug class
# ``evidence.distinct_encoded_states_collapsed_to_one_value``): an unknown
# binding -- what a pre-v58 snapshot loads as -- is ablated evidence too. It
# must never add an ``unversioned_exported_symbol`` finding, and never let
# the check read as evaluated-clean.
@pytest.mark.parametrize("side", ["old", "new", "both"])
@pytest.mark.parametrize("binding", ["unversioned", "base", "named"])
def test_unknown_version_binding_never_adds_a_finding(side: str, binding: str) -> None:
    from abicheck.buildsource.cross_source_checks import (
        CHECK_UNVERSIONED_EXPORTED_SYMBOL,
        run_crosschecks,
    )
    from abicheck.checker import compare
    from abicheck.model import AbiSnapshot
    from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, ElfVersionBinding

    def snap(ablate: bool) -> AbiSnapshot:
        state = ElfVersionBinding.UNKNOWN if ablate else ElfVersionBinding(binding)
        version = "V1" if binding == "named" else ""
        elf = ElfMetadata(
            symbols=[
                ElfSymbol("api", version="V1", version_binding=ElfVersionBinding.NAMED),
                ElfSymbol("legacy", version=version, version_binding=state),
            ],
            versions_defined=["V1"],
        )
        return AbiSnapshot(library="libx.so", version="1", elf=elf)

    def flagged(old: AbiSnapshot, new: AbiSnapshot) -> set[str]:
        return {
            c.symbol
            for c in compare(old, new).changes
            if c.kind.value == "unversioned_exported_symbol"
        }

    full = flagged(snap(False), snap(False))
    old, new = snap(side in ("old", "both")), snap(side in ("new", "both"))
    assert flagged(old, new) <= full
    if binding != "named":  # an empty version left nothing to evaluate
        ablated = new if side != "old" else old
        row = next(
            r
            for r in run_crosschecks(ablated).coverage
            if r["layer"] == f"crosscheck:{CHECK_UNVERSIONED_EXPORTED_SYMBOL}"
        )
        assert row["status"] != "present", row


# ``Change.symbol_binding`` on template findings (bug class
# ``detector.selector_axis_unstamped``): an uncaptured ELF binding on any
# removed instantiation is ablated evidence. It must never let a
# ``binding: weak`` rule demote the finding -- the aggregate fails closed to
# ``None`` -- so ablation can only keep the finding at its full severity.
@pytest.mark.parametrize("size", [1, 2, 3])
@pytest.mark.parametrize("kind", ["leak", "missing"])
def test_unknown_template_binding_never_demotes(kind: str, size: int) -> None:
    from abicheck.checker_policy import Verdict
    from abicheck.diff_templates import (
        detect_internal_template_leaks,
        detect_missing_instantiations,
    )
    from abicheck.model import AbiSnapshot
    from abicheck.model.declarations import Function, Visibility
    from abicheck.model.elf_facts import SymbolBinding
    from abicheck.policy.reclassify import ReclassifyRule

    stem = "lib::__detail::walk" if kind == "leak" else "descriptor"
    suffix = "" if kind == "leak" else "::get"
    args = ["long", "double", "char"][:size]

    def fn(arg: str, binding: SymbolBinding | None) -> Function:
        name = f"{stem}<{arg}>{suffix}"
        return Function(
            name=name,
            mangled=f"sym:{name}",
            return_type="void",
            visibility=Visibility.PUBLIC,
            elf_binding=binding,
        )

    def findings(ablated: int | None) -> list:
        removed = [
            fn(a, None if i == ablated else SymbolBinding.WEAK)
            for i, a in enumerate(args)
        ]
        survivor = fn("int", SymbolBinding.GLOBAL)
        old = AbiSnapshot(
            library="libx.so", version="1", functions=[survivor, *removed]
        )
        new = AbiSnapshot(
            library="libx.so", version="2", functions=[fn("int", SymbolBinding.GLOBAL)]
        )
        detect = (
            detect_internal_template_leaks
            if kind == "leak"
            else detect_missing_instantiations
        )
        return detect(old, new)

    rule = ReclassifyRule(
        to_verdict=Verdict.COMPATIBLE_WITH_RISK,
        to="risk",
        change_kind=(
            "internal_template_leaks_via_public_api"
            if kind == "leak"
            else "instantiation_missing_from_binary"
        ),
        symbol_pattern=".*",
        binding="weak",
    )
    # Full evidence: every member is WEAK, so the rule demotes everything.
    full = findings(None)
    assert full and all(rule.matches(f) for f in full)
    for ablated in range(size):
        cut = findings(ablated)
        # Same findings observed -- ablation never removes one ...
        assert {f.symbol for f in cut} == {f.symbol for f in full}
        # ... and never lets the unknown member be demoted as WEAK.
        assert any(not rule.matches(f) for f in cut)


# -- DWARF member location: malformed evidence is unknown, never a value ----
#
# An expression with no exact offset (underflowing, truncated, unsupported op)
# is evidence that failed to read. It must reach the layout as "unknown"
# (``None``), never as a plausible offset, the same way an unread Fact does.


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param([0x22], id="raw-plus-underflow"),
        pytest.param([(0x22, 0)], id="tuple-plus-underflow"),
        pytest.param([0x31, 0x22, 0x22], id="plus-underflow-after-valid-plus"),
        pytest.param([0x23, 0x80], id="truncated-uleb128"),
        pytest.param([(0x99, 0)], id="unsupported-opcode"),
    ],
)
def test_malformed_member_location_is_unknown_not_a_value(expr: list[object]) -> None:
    from abicheck.dwarf_utils import decode_member_location

    assert decode_member_location(expr) is None


#: Runtime-owned subjects, written by hand from the libstdc++ ABI (not read
#: back from the classifier): typeinfo, a destructor, a function-local static.
_RUNTIME_SUBJECTS = (
    "_ZTISt9exception",
    "_ZNSt18bad_variant_accessD1Ev",
    "_ZZNSt8__detail18__to_chars_10_implIjEEvPcjT_E8__digits",
)


@pytest.mark.parametrize("table", ["lists_symbol", "omits_symbol", "no_elf"])
@pytest.mark.parametrize("subject", _RUNTIME_SUBJECTS)
def test_ablated_elf_table_never_makes_a_runtime_finding_gate(
    subject: str, table: str
) -> None:
    """Dropping a runtime symbol from the ELF table (or the table itself) is
    evidence removed, so it may not turn an excluded finding into an
    ``UNKNOWN_UNRESOLVED`` one that raises the contract-coverage exit (the
    SVS field report: 8 leaked-dependency findings did exactly that)."""
    from abicheck.buildsource.export_accounting import (
        toolchain_runtime_name_predicate,
        toolchain_runtime_symbol_names,
    )
    from abicheck.checker_policy import ChangeKind
    from abicheck.contract_evaluation import evaluate_change_contract_relevance
    from abicheck.contract_relevance_types import ContractMode, ContractRelevance
    from abicheck.elf_metadata import ElfMetadata, ElfSymbol
    from abicheck.model import AbiSnapshot
    from abicheck.model.change import Change
    from abicheck.policy.contract_toolchain_origin import (
        exclude_toolchain_runtime_findings,
    )
    from abicheck.policy.public_surface_index import PublicSurface

    names = {"lists_symbol": [subject, "mylib_init"], "omits_symbol": ["mylib_init"]}
    elf = (
        ElfMetadata(
            soname="libmylib.so.1",
            needed=["libstdc++.so.6"],
            symbols=[ElfSymbol(name=n) for n in names[table]],
        )
        if table in names
        else None
    )
    snap = AbiSnapshot(library="libmylib.so.1", version="1", elf=elf)
    surface = PublicSurface(
        resolvable=True, public_symbols={"mylib_init"}, all_symbols={"mylib_init"}
    )
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol=subject, description="")
    decision = evaluate_change_contract_relevance(
        change, surface, surface, mode=ContractMode.PUBLIC
    )
    (out,) = exclude_toolchain_runtime_findings(
        [change],
        [decision],
        toolchain_runtime_symbol_names(snap),
        is_runtime_name=toolchain_runtime_name_predicate([snap]),
    )
    assert out.relevance is ContractRelevance.PROVEN_OUT_OF_CONTRACT


# Header declaration coverage (bug class
# ``detector.undeclared_export_shrink_silent``): how many of the lost exports
# a public header declares is evidence about *contract*, not about *loss*.
# Ablating the declarations (none, some, all declared) must never change the
# set of lost exports reported as removals, and the run stays BREAKING.
@pytest.mark.parametrize("sym_type", ["func", "object"])
@pytest.mark.parametrize("lost", [1, 5, 40])
def test_header_declaration_ablation_never_hides_an_export_removal(
    sym_type: str, lost: int
) -> None:
    from abicheck.checker import compare
    from abicheck.checker_policy import Verdict
    from abicheck.model import AbiSnapshot, Function, Variable
    from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolType

    st = SymbolType.FUNC if sym_type == "func" else SymbolType.OBJECT
    names = [f"_ZN2ns4lossE{i}" if i % 2 else f"loss_{i}" for i in range(lost)]

    def snap(version: str, exports: list[str], declared: list[str]) -> AbiSnapshot:
        decls = [n for n in declared if n in exports]
        funcs = [Function(name="api", mangled="api", return_type="int")]
        variables: list[Variable] = []
        if st is SymbolType.FUNC:
            funcs += [Function(name=n, mangled=n, return_type="int") for n in decls]
        else:
            variables = [Variable(name=n, mangled=n, type="int") for n in decls]
        return AbiSnapshot(
            library="libx.so",
            version=version,
            from_headers=True,
            functions=funcs,
            variables=variables,
            elf=ElfMetadata(
                machine="x86-64",
                symbols=[ElfSymbol("api")]
                + [ElfSymbol(n, sym_type=st) for n in exports],
            ),
        )

    removal_kinds = {
        "func_removed",
        "func_removed_elf_only",
        "var_removed",
        "var_removed_elf_only",
    }
    seen = []
    for declared in ([], names[::2], names):
        result = compare(snap("1", names, declared), snap("2", [], declared))
        seen.append({c.symbol for c in result.changes if c.kind.value in removal_kinds})
        assert result.verdict is Verdict.BREAKING
    assert seen[0] == seen[1] == seen[2] == set(names)
