# SPDX-License-Identifier: Apache-2.0
"""The expected-gap registry — the migration's own definition of done.

``docs/contribute/adr/068-one-comparison-product-and-scan-retirement.md``
identifies the capabilities ``compare`` cannot reach today: the eleven
cross-source checks (``abicheck/buildsource/crosscheck.py``), the lexical
pattern pre-scan (``pattern_scan.py``), the preprocessor scan
(``preprocessor_scan.py``), changed-path localization, and the ``abi3``
audit. Every one of those is registered here, each with the plan phase
that is expected to close it (``docs/contribute/plans/one-comparison-product.md``
§6) — never a bare ``xfail``, so a reader always has a name and a phase to
check rather than a silent skip.

Phases 2c and 2d are **closed**: ``compare`` now carries ``--since``/
``--changed-path`` (changed-path localization, ADR-043 D7's POI scoping rule)
and ``--abi3`` (the candidate-side stable-ABI audit, ADR-068 D3), so their two
entries were deleted from this registry in the same PR that landed them --
which is exactly what the registry is for.

**All eleven cross-source checks are closed.** ``unversioned_exported_
symbol`` and ``private_header_leak`` landed first; ``exported_not_public``,
``public_not_exported``, ``rtti_for_internal_type``, and
``public_to_internal_dependency`` followed (plan §3 rows 3-5); and a later
PR closed the remaining five -- ``header_build_context_mismatch``,
``odr_type_variant``, ``identity_collision_detected``,
``compile_context_conflict``, and ``source_surface_dso_mismatch`` -- all now
running automatically inside ``checker.compare()`` (``cross_source_checks``,
default ``True`` -- see ``workflows/cross_source_evolution.py``), reached by
every front end through ``compare()``'s ordinary path with no opt-in flag
(ADR-068 D4/D5). No cross-source check remains in this registry.

**Phase 2b is closed too (this PR): ``pattern_scan``/``preprocessor_scan``.**
Both migrated capabilities (plan §3 rows 6/8) now run automatically inside
``compare()``, per side, via ``workflows/lexical_prescan.py``'s
``fold_lexical_prescan`` -- one shared fold point both the native ``compare``
CLI and the typed API call, mirroring ``abi3_audit.fold``'s own shape.
Deliberately **not** folded through the ``CrossSourceEvolution`` axis the
eleven cross-source checks use: neither pre-scan has ever produced a
``ChangeKind``/``Change`` (they emit advisory ``PatternFact``/
``EscalationTrigger``/``MacroDivergence``/``HeaderLeak`` records, per ADR-028
D3/ADR-035 D1's authority rule -- never authoritative for a verdict), and
``scan`` itself never computed either pre-scan on a baseline, only ever on
the candidate -- so a two-sided evolution axis over these facts would invent
new product behavior rather than preserve one. Instead each side's result
attaches to the report as an evidence/coverage block
(``DiffResult.pattern_prescan``/``.preprocessor_prescan``, report schema
3.12), the same "coverage row, not a Change" shape L3/L4/L5 evidence already
gets. See ``workflows/lexical_prescan.py``'s module docstring for the full
design-decision writeup (the two options weighed, and why this one is
correct) and ``docs/contribute/plans/one-comparison-product.md``'s §3 rows
6/8 for the plan-level record of the same decision.

**This registry is the red set the migration must turn empty.** A test in
this package asserts, for each registered key, that the capability is
present under ``scan`` and absent under ``compare`` today. When a Phase 2
PR gives ``compare`` the capability, the corresponding parity test starts
asserting a contradiction (``scan`` and ``compare`` now agree) and fails
loudly until the entry below is deleted in that same PR — see
``test_gap_registry_contract.py``.

Do not add an entry here for a loss that isn't one of the originally listed
fifteen scan-only capabilities (eleven checks + pattern scan + preprocessor
scan + changed-path localization + abi3 audit; all fifteen are now closed) --
an *unexplained* loss anywhere else is a real regression the harness must
fail on, not something to file away quietly. ``EXPECTED_GAPS`` (this dict,
now empty) is what ``runner.py``'s scan-vs-compare diff checks against;
``ALL_EXPECTED_GAPS`` below folds in ``NOT_YET_IMPLEMENTED_ANYWHERE`` too
(currently empty — ADR-068 Phase 1 item 2 landed the ``finding_evolution``
vocabulary it used to hold), purely for ``test_gap_registry_contract.py``'s
own completeness bookkeeping — never to decide whether a scan finding's
*absence* from `compare` is expected.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectedGap:
    """One capability ``compare`` cannot reach yet."""

    #: Why the gap exists today (mirrors ADR-068 §1's call-site finding).
    reason: str
    #: The plan phase (docs/contribute/plans/one-comparison-product.md §6)
    #: expected to close it. Not a promise of *when* — just *which* PR.
    plan_phase: str
    #: The table row in the plan's §3 capability map, for cross-reference.
    plan_row: str


#: The eleven cross-source checks (buildsource/crosscheck.py) all closed as
#: of an earlier PR -- kept only as the historical reason text
#: ``test_gap_registry_contract.py`` and this module's own docstring
#: reference; no cross-source-check key remains in EXPECTED_GAPS below.
_CROSSCHECK_REASON = (
    "cross-source check (abicheck/buildsource/crosscheck.py); its only "
    "production caller under abicheck/ is scan_engine.py (ADR-068 §1)"
)

#: ``pattern_scan``/``preprocessor_scan`` (plan §3 rows 6/8) are also closed
#: now (this PR) -- kept only as the historical reason text, same as
#: ``_CROSSCHECK_REASON`` above; no key for either remains in EXPECTED_GAPS.
_LEXICAL_PRESCAN_REASON = (
    "lexical/preprocessor pre-scan (abicheck/buildsource/pattern_scan.py, "
    "abicheck/buildsource/preprocessor_scan.py); their only production "
    "caller under abicheck/ used to be scan_engine.py (ADR-068 §1)"
)

#: Every scan-only capability from the original fifteen-item list is now
#: closed on ``compare`` (all eleven cross-source checks, both pre-scans,
#: changed-path localization, and the abi3 audit) -- this dict is the red
#: set the migration exists to empty, and it is empty.
EXPECTED_GAPS: dict[str, ExpectedGap] = {}

#: F-8/F-9 (plan §7) used to be tracked here as `finding_evolution`: the
#: `not_evaluated`/`resolved` evolution axis existed under neither tool.
#: ADR-068 Phase 1 item 2 landed the generic vocabulary (`checker_policy.
#: FindingEvolution`, `Change.evolution`/`DiffResult.resolved_findings`,
#: `policy.finding_evolution`'s correspondence primitive) -- see
#: `test_evolution_state_gap.py` for the now-real F-8/F-9 demonstration --
#: so this registry is empty until a genuinely new "neither tool has this at
#: all" gap appears. Kept as its own dict (rather than deleted outright)
#: because it is a structurally different kind of gap from EXPECTED_GAPS
#: (which is specifically "scan has it, compare doesn't") and a future gap
#: of this second kind should have an obvious place to register, per this
#: module's own docstring.
NOT_YET_IMPLEMENTED_ANYWHERE: dict[str, ExpectedGap] = {}


#: The complete *tracked* set: every scan-only capability, plus whatever
#: NOT_YET_IMPLEMENTED_ANYWHERE holds (currently nothing -- see its own
#: comment). `test_gap_registry_contract.py` pins this against `EXPECTED_GAPS`
#: alone while it's empty; `EXPECTED_GAPS` itself is now empty -- every one
#: of the fifteen originally-listed scan-only capabilities is closed on
#: `compare`. Used only for registry-completeness bookkeeping --
#: `runner.py`'s scan-vs-compare diff checks against `EXPECTED_GAPS` alone
#: (see this module's docstring).
ALL_EXPECTED_GAPS: dict[str, ExpectedGap] = {
    **EXPECTED_GAPS,
    **NOT_YET_IMPLEMENTED_ANYWHERE,
}
