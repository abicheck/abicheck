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

"""ADR-067 C-S2: reclassification recorded through the disposition ledger.

Before this slice, ``DispositionRecord.reclassified_by`` was populated by
``getattr(change, "reclassified_by", None)`` -- and ``Change`` carries no
such attribute (the audit value is computed on demand by
``reporter._reclassified_by_for_change``/``reclassify.reclassify_rule_for_
change``), so every record's ``reclassified_by`` was always ``None``.
``DispositionLedger.resolve_reclassifications`` closes that gap the same way
``resolve_verdict_classes`` already resolves a suppressed record's verdict
class on demand.

Registered bug class: ``policy.disposition_conservation``
(``tests/regressions/manifest.py``) -- reclassification is an *overlay*
attribute (D2), never a terminal disposition of its own, so recording it
must never move a finding's ``counts()`` bucket or ``detected_total``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.policy.disposition_close import (
    ledger_for,
    reclassifications,
    reclassified_total,
)
from abicheck.policy_file import PolicyFile
from abicheck.report.disposition_audit import compute_disposition_audit


def _policy_file(tmp_path: Path, *, symbol: str, reason: str) -> PolicyFile:
    p = tmp_path / "policy.yaml"
    p.write_text(
        "reclassify:\n"
        "  - kind: func_removed\n"
        f"    symbol: {symbol}\n"
        "    to: ignore\n"
        f'    reason: "{reason}"\n',
        encoding="utf-8",
    )
    return PolicyFile.load(p)


def test_a_reclassified_finding_is_recorded_without_moving_the_counts(
    tmp_path: Path,
) -> None:
    pf = _policy_file(tmp_path, symbol="foo", reason="COMDAT-inline demotions")
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        policy_file=pf,
    )

    ledger = ledger_for(diff)
    audit = compute_disposition_audit(diff)

    # D2: the overlay is recorded independently of the terminal disposition.
    assert reclassified_total(ledger) == 1
    assert reclassifications(ledger) == (("COMDAT-inline demotions", 1),)
    assert audit.reclassified_total == 1
    assert audit.reclassifications == (("COMDAT-inline demotions", 1),)
    # Never added into `counts`/`detected_total` -- conservation still holds
    # over the *disposition* axis alone.
    assert audit.detected_total == 1
    assert sum(dict(audit.counts).values()) == audit.detected_total


def test_reclassification_and_suppression_compose(tmp_path: Path) -> None:
    """A reclassified-and-suppressed finding is still `suppressed` (D2):
    reclassification never overrides a terminal disposition, it only
    annotates whichever one the finding actually received."""
    from abicheck.checker import compare
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.suppression import Suppression, SuppressionList

    pf = _policy_file(tmp_path, symbol="foo", reason="COMDAT-inline demotions")
    old = AbiSnapshot(
        library="libfoo.so",
        version="1",
        functions=[
            Function(
                name="foo",
                mangled="foo",
                return_type="void",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    new = AbiSnapshot(library="libfoo.so", version="2", functions=[])
    rules = SuppressionList([Suppression(symbol="foo", reason="waived")])

    result = compare(old, new, rules, policy_file=pf)
    audit = compute_disposition_audit(result)

    assert audit.reclassified_total == 1
    assert dict(audit.counts)["suppressed"] == 1
    assert dict(audit.counts)["gating"] == 0


def test_no_reclassify_rule_leaves_the_overlay_absent(tmp_path: Path) -> None:
    pf = _policy_file(tmp_path, symbol="bar", reason="unrelated")
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        policy_file=pf,
    )
    audit = compute_disposition_audit(diff)
    assert audit.reclassified_total == 0
    assert audit.reclassifications == ()


@pytest.mark.parametrize("verdict", [Verdict.COMPATIBLE_WITH_RISK, Verdict.COMPATIBLE])
def test_reclassification_is_read_not_recomputed_by_the_ledger(
    tmp_path: Path, verdict: Verdict
) -> None:
    """`resolve_reclassifications` reads `reclassify.reclassify_rule_for_
    change` -- the same resolver `reporter._reclassified_by_for_change`
    uses -- so it cannot invent a rule the policy file never declared."""
    to = "risk" if verdict is Verdict.COMPATIBLE_WITH_RISK else "ignore"
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"reclassify:\n  - kind: func_removed\n    symbol: foo\n    to: {to}\n"
        '    reason: "r"\n',
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        policy_file=pf,
    )
    audit = compute_disposition_audit(diff)
    assert audit.reclassified_total == 1
