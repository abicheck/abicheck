# SPDX-License-Identifier: Apache-2.0
"""Hidden findings are explained by their real reason, and the report's two
non-gating counts reconcile.

Bug class (oneCCL 2021.14 -> 2021.15): (a) every renderer said the hidden
redundant changes were "derived from root type changes", false for the ten
``exported_not_public`` findings folded into their existence finding; (b)
``result_counts`` said 49 non-gating and ``disposition_audit`` said 59 with
nothing linking them. Oracles here are independent of the code under test:
(a) the change *kind* each producer emits, (b) a recount over the ledger's
own per-change records.
"""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import DiffResult
from abicheck.model.change import Change
from abicheck.policy.disposition_close import ledger_for
from abicheck.policy.disposition_types import Disposition
from abicheck.report.diff_report import to_json
from abicheck.report.disposition_audit import DispositionAudit
from abicheck.report.redundancy_reasons import (
    redundancy_reason_counts,
    redundancy_reason_text,
)
from abicheck.report.result_counts import held_out_non_gating
from abicheck.report.section_notes import compute_redundancy_note
from tests.test_export_existence_reconciliation import _run

_EXISTENCE = "folded into their symbol's existence finding"
_ROOT = "derived from root type changes"
_RENAME = "folded into a rename"


@pytest.mark.parametrize("seed", range(40))
def test_hidden_reasons_match_what_was_folded(tmp_path: Path, seed: int) -> None:
    _presence, result = _run(tmp_path, seed)
    hidden = result.redundant_changes[: result.redundant_count]
    counts = dict(redundancy_reason_counts(result))
    # Conservation: every hidden change has exactly one reason.
    assert sum(counts.values()) == result.redundant_count
    # Oracle by kind: the existence fold is the only producer that hides an
    # `exported_not_public` finding.
    folded = sum(c.kind == ChangeKind.EXPORTED_NOT_PUBLIC for c in hidden)
    assert counts.get(_EXISTENCE, 0) == folded
    note = compute_redundancy_note(result)
    if folded and folded == result.redundant_count:
        assert note is not None and note.reasons == _EXISTENCE
        assert _ROOT not in note.reasons


def _hidden(cause: str | None) -> Change:
    return Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="s",
        description="d",
        caused_by_type=cause,
    )


@pytest.mark.parametrize("seed", range(30))
def test_reason_counts_conserve_any_mix(seed: int) -> None:
    rng = random.Random(seed)
    spellings = {
        _EXISTENCE: lambda i: f"export_existence:func_added_elf_only:_Z{i}f",
        _RENAME: lambda i: f"rename:old{i}→new{i}",
        _ROOT: lambda i: f"ns::Type{i}",
        "reason not recorded": lambda i: None,
    }
    want = {r: rng.randint(0, 4) for r in spellings}
    changes = [_hidden(spellings[r](i)) for r, n in want.items() for i in range(n)]
    rng.shuffle(changes)
    tail = [_hidden(None) for _ in range(rng.randint(0, 3))]  # opaque half
    result = DiffResult(
        old_version="1",
        new_version="2",
        library="l",
        redundant_changes=changes + tail,
        redundant_count=len(changes),
    )
    got = dict(redundancy_reason_counts(result))
    assert got == {r: n for r, n in want.items() if n}
    text = redundancy_reason_text(result)
    for reason, n in got.items():
        assert reason in text
        if len(got) > 1:
            assert f"{n} {reason}" in text


def test_every_redundant_producer_has_a_reason() -> None:
    """Adding a fourth producer must add its reason, not inherit "root type"."""
    known = {"finding_redundancy.py", "export_existence_fold.py", "post_processing.py"}
    producers = set()
    for path in Path("abicheck").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend"}
                and ast.unparse(node.func.value).split(".")[-1] == "redundant"
            ):
                producers.add(path.name)
    assert producers == known, producers


@pytest.mark.parametrize("seed", range(40))
def test_non_gating_counts_reconcile_through_the_ledger(tmp_path, seed) -> None:
    _presence, result = _run(tmp_path, seed)
    doc = json.loads(to_json(result))
    audit = DispositionAudit.from_dict(doc["disposition_audit"])
    retained = {id(c) for c in result.changes}
    ledger = ledger_for(result)
    held_out = sum(
        1
        for bucket in (
            result.redundant_changes,
            result.out_of_surface_changes,
            result.reconciled_changes,
        )
        for c in bucket
        if id(c) not in retained
        and (rec := ledger.record_for(c)) is not None
        and rec.disposition == Disposition.NON_GATING
    )
    assert held_out_non_gating(doc["result_counts"], audit) == held_out
    counts = doc["result_counts"]
    assert counts["non_gating"] + held_out == dict(audit.counts)["non_gating"]


def test_held_out_note_absent_without_audit_or_difference() -> None:
    from abicheck.report.result_counts import held_out_note

    audit = DispositionAudit(
        detected_total=3,
        effective_total=0,
        counts=(("non_gating", 3),),
        rules=(),
        not_evaluated_detectors=(),
    )
    assert held_out_note({"non_gating": 3}, None) == ""
    assert held_out_note({"non_gating": 3}, audit) == ""
    assert "2 non-gating held out" in held_out_note({"non_gating": 1}, audit)
