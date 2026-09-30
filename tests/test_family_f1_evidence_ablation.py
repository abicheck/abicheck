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
  ``Change`` carries no such marker today, so (b) is a plain subset. None of
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

**Real finding** (``KNOWN_VIOLATIONS``, each also an ``xfail(strict=True)``):
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
)

import abicheck.model.declarations as declarations_mod
import abicheck.model.entities as entities_mod
import abicheck.model.fact as fact_mod
import abicheck.model.surface_facts as surface_facts_mod
from abicheck.model import Fact, FactStatus

# --------------------------------------------------------------------------
# Inventory bookkeeping
# --------------------------------------------------------------------------

#: Sites the harness cannot ablate yet. Shrink-only: see
#: ``test_uncovered_is_shrink_only``.
UNCOVERED: dict[str, str] = {
    "EnumType.ownership_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.contract_attributes_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.exception_spec_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.hidden_friend_owner_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.is_compiler_generated_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.is_hidden_friend_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.is_override_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Function.ownership_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "RecordType.ownership_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Variable.alignment_bits_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "Variable.ownership_fact": "baseline corpus never populates this fact present, so ablating it would swap one unknown for another; needs a corpus value",
    "CanonicalEntity.canonical_spelling": "SemanticIR occurrences are not built by the in-process corpus; needs a header-AST normalizer fixture",
    "CanonicalEntity.cv_qualification": "SemanticIR occurrences are not built by the in-process corpus; needs a header-AST normalizer fixture",
    "CanonicalEntity.template_arguments": "SemanticIR occurrences are not built by the in-process corpus; needs a header-AST normalizer fixture",
    "MachoMetadata.rpaths_fact": "corpus is ELF-only; needs a Mach-O pair",
    "PeMetadata.delay_imports_fact": "corpus is ELF-only; needs a PE pair",
    "AbiSnapshot.pe": "corpus is ELF-only; needs a PE pair",
    "AbiSnapshot.macho": "corpus is ELF-only; needs a Mach-O pair",
    "AbiSnapshot.dwarf_advanced": "no AdvancedDwarfMetadata fixture in the corpus yet",
    "AbiSnapshot.sycl": "SYCL plugin-interface evidence has no corpus pair",
    "AbiSnapshot.python_ext": "CPython extension evidence has no corpus pair",
    "AbiSnapshot.kabi": "kABI (Module.symvers) evidence has no corpus pair",
    "AbiSnapshot.python_api": "Python API surface evidence has no corpus pair",
    "AbiSnapshot.numpy_capi": "NumPy C-API evidence has no corpus pair",
    "AbiSnapshot.extraction_scope": "scope record, not a producer output; ablating it is H2's (route parity) territory",
    "AbiSnapshot.dependency_info": "dependency walk evidence has no corpus pair",
    "AbiSnapshot.build_mode": "L3 build-mode evidence has no corpus pair",
    "AbiSnapshot.build_source_pack": "L3-L5 build-source reference has no corpus pair (#1389 lives here)",
    "AbiSnapshot.build_source": "L3-L5 build-source pack has no corpus pair (#1389 lives here)",
    "AbiSnapshot.surface_graph": "persisted surface graph is recomputed, not trusted, by compare (PR #979)",
    "AbiSnapshot.contract": "extraction contract record has no corpus pair",
    "AbiSnapshot.semantic_ir": "SemanticIR is not built by the in-process corpus",
}

#: The real current violations this harness found (see module docstring).
#: ``(case, site, side)``; every status in ``UNKNOWN_FACTS`` reproduces them.
KNOWN_VIOLATIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("enum_value_changed", "EnumType.source_header_fact", "both"),
        ("header_record_changed", "RecordType.source_header_fact", "both"),
        ("header_record_changed", "RecordType.qualified_name_fact", "both"),
    }
)
_KNOWN_BUG_REASON = (
    "F1 real bug: public_surface_closure seeds header-origin enums/records from the "
    "legacy source_header / qualified_name strings; an unknown source_header_fact "
    "(or, for records, qualified_name_fact) on both sides reads as 'not from a "
    "header' / 'unnamed', the type becomes non-public-type and a real break is "
    "scoped out to NO_CHANGE with no stated gap"
)

_FACT_SITES = fact_site_inventory()
_STATUSES = tuple(UNKNOWN_FACTS)


def _exercised_fact_sites() -> set[str]:
    hit = set()
    for old, new in CORPUS.values():
        for name, site in _FACT_SITES.items():
            if present_fact_count(old, site) or present_fact_count(new, site):
                hit.add(name)
    return hit


_EXERCISED_FACTS = _exercised_fact_sites()
_EXERCISED_CONTAINERS = set(CONTAINER_ABLATIONS)
_FULL: dict[tuple[str, str], Outcome] = {}


def _full(case: str, config: str = "default") -> Outcome:
    key = (case, config)
    if key not in _FULL:
        _FULL[key] = outcome(*CORPUS[case], config)
    return _FULL[key]


def _fact_cell(
    case: str, site: str, status: str, side: str, config: str = "default"
) -> list[str]:
    fact = UNKNOWN_FACTS[status]()
    old, new = apply_sided(
        *CORPUS[case], side, lambda s: ablate_fact(s, _FACT_SITES[site], fact)[0]
    )
    return oracle_violations(case, _full(case, config), outcome(old, new, config))


def _sweep(
    sites: list[str], statuses_for: Any, config: str
) -> set[tuple[str, str, str]]:
    found = set()
    for case in CORPUS:
        for i, site in enumerate(sites):
            for status in statuses_for(i, case):
                for side in SIDES:
                    if _fact_cell(case, site, status, side, config):
                        found.add((case, site, side))
    return found


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
        for side in SIDES:
            if (case, site, side) in KNOWN_VIOLATIONS:
                continue
            v = _fact_cell(case, site, status, side)
            if v:
                violations[(case, status, side)] = v
    assert not violations, violations


@pytest.mark.slow
def test_fact_ablation_full_product_default_config() -> None:
    found = _sweep(sorted(_EXERCISED_FACTS), lambda i, c: _STATUSES, "default")
    assert found == set(KNOWN_VIOLATIONS)


@pytest.mark.slow
def test_fact_ablation_contract_exports_config() -> None:
    found = _sweep(sorted(_EXERCISED_FACTS), lambda i, c: _STATUSES, "contract_exports")
    # Under contract=exports both known cells are out of the export domain in
    # the full run already (NO_CHANGE), so nothing is left to silence.
    assert found == set()


@pytest.mark.parametrize(("case", "site", "side"), sorted(KNOWN_VIOLATIONS))
@pytest.mark.parametrize("status", _STATUSES)
@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_KNOWN_BUG_REASON)
def test_known_violation_header_origin_seed(
    case: str, site: str, side: str, status: str
) -> None:
    assert _fact_cell(case, site, status, side) == []


@pytest.mark.parametrize(("case", "site", "side"), sorted(KNOWN_VIOLATIONS))
def test_known_violations_still_reproduce_exactly(
    case: str, site: str, side: str
) -> None:
    # Pins the diagnosis, so the xfail cannot pass for an unrelated reason.
    for status in _STATUSES:
        assert _fact_cell(case, site, status, side) == [
            "(a) silent clean: BREAKING -> NO_CHANGE with no stated gap"
        ]


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
