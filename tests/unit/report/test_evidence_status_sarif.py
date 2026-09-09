# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""SARIF ``evidenceStatus`` counterpart of ``test_evidence_status_json.py``
(same directory). Split out of ``tests/test_sarif.py`` (architecture
debt-no-growth baseline) rather than grown in place.
"""

from __future__ import annotations

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.sarif import to_sarif


def _make_result(
    changes: list[Change], verdict: Verdict = Verdict.BREAKING
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so.1",
        changes=changes,
        verdict=verdict,
    )


def test_result_evidence_status_artifact_proven_with_binary_evidence() -> None:
    # A real ELF-observed removal stamps symbol_binding from the snapshot's
    # own symbol table -- the per-finding half of the ADR-068 finding C fix
    # (checker_policy.evidence_status_for_result) needs that stamp on an
    # "elf"-tiered comparison to keep claiming artifact_proven for this
    # specific finding.
    change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_Z3foov",
        description="Function foo() removed",
        symbol_binding="global",
    )
    result = _make_result([change], verdict=Verdict.BREAKING)
    result.evidence_tiers = ["header", "elf"]
    doc = to_sarif(result)
    props = doc["runs"][0]["results"][0]["properties"]
    assert props["evidenceStatus"] == "artifact_proven"


def test_result_evidence_status_unattributed_despite_elf_tier_without_symbol_binding() -> (
    None
):
    # ADR-068 finding C: an "elf"-tiered comparison can still produce a
    # BREAKING_KINDS finding synthesized from header-only evidence with no
    # corresponding export -- symbol_binding stays unset for exactly that
    # case, the positive signal this specific finding was never actually
    # observed in the symbol table.
    change = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_Z3foov",
        description="Function foo() removed",
    )
    result = _make_result([change], verdict=Verdict.BREAKING)
    result.evidence_tiers = ["header", "elf"]
    doc = to_sarif(result)
    props = doc["runs"][0]["results"][0]["properties"]
    assert props["evidenceStatus"] == "unattributed"
