# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""JSON ``evidence_status`` tests for a BREAKING_KINDS finding on an
``"elf"``-tiered comparison. Split out of ``tests/test_reporter.py``
(architecture debt-no-growth baseline) rather than grown in place -- see
that file's ``TestEvidenceStatusInJson`` for the sibling
no-binary-evidence-at-all case this one complements.
"""

from __future__ import annotations

import json

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.reporter import to_json


def _result(verdict: Verdict, changes: list[Change]) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=changes,
        verdict=verdict,
    )


def test_breaking_change_with_binary_evidence_stays_artifact_proven():
    # A real ELF-observed removal stamps symbol_binding from the snapshot's
    # own symbol table (diff_symbols._check_removed_function).
    c = Change(
        ChangeKind.FUNC_REMOVED,
        "_Z3foov",
        "Public function removed: foo",
        symbol_binding="global",
    )
    r = _result(Verdict.BREAKING, changes=[c])
    r.evidence_tiers = ["header", "elf"]
    d = json.loads(to_json(r))
    assert d["changes"][0]["evidence_status"] == "artifact_proven"


def test_breaking_change_with_elf_tier_but_no_symbol_binding_is_unattributed():
    # ADR-068 finding C: an "elf"-tiered comparison can still produce a
    # BREAKING_KINDS finding synthesized from header-only evidence with no
    # corresponding export (e.g. a header-only overload-set member never
    # matched against a mangled ELF symbol) -- symbol_binding stays unset
    # for exactly that case, which is the positive signal this specific
    # finding was never actually observed in the symbol table, independent
    # of what the comparison as a whole examined.
    c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
    r = _result(Verdict.BREAKING, changes=[c])
    r.evidence_tiers = ["header", "elf"]
    d = json.loads(to_json(r))
    assert d["changes"][0]["evidence_status"] == "unattributed"
