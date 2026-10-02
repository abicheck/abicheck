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

**Inventory** (``_family_f4_inventory``): an AST scan of the decision
modules (``compare/``, ``policy/``, ``diff_*``, ``detectors*``,
``checker*``, ``classify*`` and the flat-root ``internal_leak*``) for regex,
affix, fnmatch and name-vocabulary sites, keyed
``module::qualname::kind:pattern``. Every key must have exactly one row in
``_family_f4_registry.HEURISTICS``: an unlisted site fails, and so does a
stale row.

**Oracle** (``_family_f4_harness``): per covered heuristic, FP cells (name
matches, structure vetoes -> no heuristic finding), FN cells (structure
positive, near-miss name -> the real finding survives) and a control cell
(the heuristic is alive), stated as raw ``(kind, symbol)`` pairs and a
verdict band; the #1231 cells add a metamorphic check against a structurally
identical twin in a neutral namespace.

**Real findings** (``KNOWN_VIOLATIONS``, each a strict xfail):

* ``sentinel.fp.mid_list_max_name`` -- the end-sentinel rule is name-only:
  ``E_MAX`` declared mid-list, neither last nor the maximum value, has its
  value change demoted to ``enum_last_member_value_changed``
  (COMPATIBLE_WITH_RISK) instead of a breaking ``enum_member_value_changed``.
* ``internal_ns.fp.reached_by_pointer_{detail,impl}`` -- a record reached
  from an exported function's signature by pointer is dropped as
  out-of-contract (NO_CHANGE) when its namespace is spelled ``detail``/
  ``impl``, while the structurally identical ``ns::priv::Cfg`` is BREAKING:
  ``internal_leak.is_internal_type`` decides contract membership by name.
"""

from __future__ import annotations

import collections

import pytest
from _family_f4_harness import CELLS, _scope_path_for_mutant, oracle_violations
from _family_f4_inventory import heuristic_site_inventory, scanned_files
from _family_f4_registry import (
    CONVENTION_STRUCTURE,
    COVERED,
    HEURISTICS,
    UNCOVERED_REASONS,
)

import abicheck.diff_namespaces as diff_namespaces_mod
import abicheck.diff_serialization as diff_serialization_mod
import abicheck.diff_types as diff_types_mod
import abicheck.internal_leak as internal_leak_mod

# --------------------------------------------------------------------------
# Registry contract
# --------------------------------------------------------------------------

#: Shrink-only ceilings on the UNCOVERED rows, per category. Lower a number
#: when a row is covered or deleted; never raise one to admit a new site --
#: register the new heuristic with its structural fact and cells instead.
UNCOVERED_CEILINGS: dict[str, int] = {
    "spelling": 60,
    "grammar": 36,
    "convention": 34,
    "own_format": 30,
    "platform": 17,
    "user_rule": 8,
    "sniff": 5,
}

KNOWN_VIOLATIONS: dict[str, frozenset[str]] = {}
_KNOWN_BUG_REASON = (
    "F4 real finding: a name-shape heuristic decides this finding with no "
    "structural confirmation (see module docstring)"
)


@pytest.mark.repo_scan
def test_inventory_is_derived_and_nontrivial() -> None:
    inventory = heuristic_site_inventory()
    assert len(scanned_files()) > 100
    assert len(inventory) > 150
    # The scan finds the #1411 heuristics by itself (not via this table).
    for needle in (
        "abicheck.compare.enum_sentinel::<module>::vocab:_SENTINEL_TAIL_TOKENS",
        "abicheck.diff_serialization::<module>::vocab:_TAG_SUFFIX_PATTERNS",
        "abicheck.diff_namespaces::<module>::vocab:DEFAULT_EXPERIMENTAL_NAMESPACES",
        "abicheck.internal_leak::<module>::vocab:DEFAULT_INTERNAL_NAMESPACES",
    ):
        assert needle in inventory


@pytest.mark.repo_scan
def test_every_site_is_registered() -> None:
    unlisted = heuristic_site_inventory() - set(HEURISTICS)
    assert not unlisted, (
        "new name/spelling heuristic site(s) with no row in "
        "tests/_family_f4_registry.HEURISTICS -- register the structural fact "
        f"that confirms/vetoes each, with FP/FN cells or an UNCOVERED reason: {sorted(unlisted)}"
    )


@pytest.mark.repo_scan
def test_registry_has_no_stale_rows() -> None:
    stale = set(HEURISTICS) - heuristic_site_inventory()
    assert not stale, f"HEURISTICS rows for sites that no longer exist: {sorted(stale)}"


def test_registry_rows_are_well_formed() -> None:
    bad = [
        (k, v)
        for k, v in HEURISTICS.items()
        if not (v.startswith("C:") and v[2:] in COVERED) and v not in UNCOVERED_REASONS
    ]
    assert not bad, f"rows naming no covered heuristic or UNCOVERED reason: {bad}"
    assert all(r.strip() for r in UNCOVERED_REASONS.values())
    used = {v[2:] for v in HEURISTICS.values() if v.startswith("C:")}
    assert used == set(COVERED), (
        f"COVERED heuristics with no site: {set(COVERED) - used}"
    )


def test_covered_heuristics_name_structure_and_cells() -> None:
    referenced: set[str] = set()
    for name, h in COVERED.items():
        assert h.structure.strip() and h.bugs, name
        assert h.fp_cells and h.fn_cells and h.control_cells, name
        cells = (*h.fp_cells, *h.fn_cells, *h.control_cells)
        missing = [c for c in cells if c not in CELLS]
        assert not missing, f"{name}: unknown cells {missing}"
        referenced.update(cells)
    assert referenced == set(CELLS), f"orphan cells: {set(CELLS) - referenced}"


def test_convention_sites_name_their_structural_fact() -> None:
    modules = {k.split("::", 1)[0] for k, v in HEURISTICS.items() if v == "convention"}
    assert modules - set(CONVENTION_STRUCTURE) == set(), (
        "convention sites whose module states no structural fact"
    )
    assert set(CONVENTION_STRUCTURE) - modules == set(), "stale CONVENTION_STRUCTURE"


def test_uncovered_is_shrink_only() -> None:
    counts = collections.Counter(
        v for v in HEURISTICS.values() if not v.startswith("C:")
    )
    over = {c: n for c, n in counts.items() if n > UNCOVERED_CEILINGS.get(c, 0)}
    assert not over, f"UNCOVERED grew past its ceiling: {over}"
    slack = {c: UNCOVERED_CEILINGS[c] - counts.get(c, 0) for c in UNCOVERED_CEILINGS}
    assert not any(slack.values()), f"lower UNCOVERED_CEILINGS to match: {slack}"


# --------------------------------------------------------------------------
# Oracle cells
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cell_id", sorted(set(CELLS) - set(KNOWN_VIOLATIONS)))
def test_heuristic_cell_oracle(cell_id: str) -> None:
    assert oracle_violations(cell_id, CELLS[cell_id]) == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_KNOWN_BUG_REASON)
@pytest.mark.parametrize("cell_id", sorted(KNOWN_VIOLATIONS))
def test_known_violation(cell_id: str) -> None:
    assert oracle_violations(cell_id, CELLS[cell_id]) == []


@pytest.mark.parametrize("cell_id", sorted(KNOWN_VIOLATIONS))
def test_known_violations_still_reproduce_exactly(cell_id: str) -> None:
    # Pins the diagnosis, so the xfail cannot pass for an unrelated reason.
    assert (
        frozenset(oracle_violations(cell_id, CELLS[cell_id]))
        == KNOWN_VIOLATIONS[cell_id]
    )


# --------------------------------------------------------------------------
# Seeded mutants: reintroduce a historical heuristic bug, the oracle reports it
# --------------------------------------------------------------------------


def _violations(prefix: str) -> list[str]:
    return [
        v
        for cid, cell in CELLS.items()
        if cid.startswith(prefix) and cid not in KNOWN_VIOLATIONS
        for v in oracle_violations(cid, cell)
    ]


def test_seeded_mutant_raw_substring_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-token matching: ``max``/``end``/``last`` anywhere in the name."""
    monkeypatch.setattr(
        diff_types_mod,
        "is_sentinel_enum_member",
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
