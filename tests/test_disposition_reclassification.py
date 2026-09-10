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


@pytest.mark.parametrize(
    ("kind", "symbol", "to"),
    [
        (ChangeKind.FUNC_REMOVED, "inline_foo", "ignore"),
        (ChangeKind.VAR_REMOVED, "inline_bar", "risk"),
    ],
)
def test_every_finding_dict_builder_agrees_on_reclassified_by(
    tmp_path: Path, kind: ChangeKind, symbol: str, to: str
) -> None:
    """The oneDAL-release bug: the release fan-out's own finding projection
    (``cli_compare_release_matrix._release_finding_dicts``) never computed
    ``reclassified_by`` at all -- 0/10 emitted findings ever carried it, even
    though the aggregate disposition-audit ledger (the *same* policy file, the
    *same* change) tallied the reclassification correctly. The gap was a
    missing field in one entry-builder, not a wrong ``policy_file`` -- so the
    general invariant is *parity*: every per-finding entry-builder that
    exists to report a shared ``Change`` must resolve the same
    ``reclassified_by`` value for it, via the one canonical helper
    (``reporter._reclassified_by_for_change``), rather than some builders
    computing it and a sibling silently omitting it.

    Covers all three of this codebase's finding-dict builders (`compare`'s
    full/leaf JSON entries, `scan --against`'s baseline dicts, and the
    release fan-out's own capped dicts) against two different reclassify
    kinds/targets, not just the one input the original report reproduced.
    """
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"reclassify:\n  - kind: {kind.value}\n    symbol: {symbol}\n"
        f'    to: {to}\n    reason: "inlines-hidden-demotion"\n',
        encoding="utf-8",
    )
    pf = PolicyFile.load(p)
    change = Change(kind=kind, symbol=symbol, description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        policy_file=pf,
    )

    # Precondition, asserted rather than assumed (AGENTS.md's "assert your
    # fixture's preconditions" trap): this change really is in the failing
    # regime the bug report described -- the ledger must see a real
    # reclassification, or every assertion below would pass vacuously.
    ledger = ledger_for(diff)
    assert reclassified_total(ledger) == 1
    assert reclassifications(ledger) == (("inlines-hidden-demotion", 1),)

    from abicheck.reporter import _change_to_dict, _reclassified_by_for_change

    expected = _reclassified_by_for_change(change, pf)
    assert expected == "inlines-hidden-demotion"

    # 1. `compare`'s own full-mode JSON entry (`_change_to_dict`) -- needs
    # real `kind_sets` (as a live `compare()` run always supplies) for the
    # reclassify resolution branch to run at all.
    full_entry = _change_to_dict(
        change, policy_file=pf, kind_sets=diff._effective_kind_sets()
    )
    assert full_entry["reclassified_by"] == expected

    # 2. `scan --against`'s baseline finding dict.
    from abicheck.cli_scan_baseline import _baseline_finding_dicts

    scan_dicts = _baseline_finding_dicts([change], "compatible", policy_file=pf)
    assert scan_dicts[0]["reclassified_by"] == expected

    # 3. The release fan-out's own capped per-library finding dict -- the
    # one builder that regressed.
    from abicheck.cli_compare_release_matrix import _release_finding_dicts

    release_dicts, _cut_kinds = _release_finding_dicts(diff, None, None)
    assert release_dicts[0]["reclassified_by"] == expected

    # 4. The release fan-out's Markdown rendering of that same dict (Codex
    # review, PR #1176 follow-up): fixing the JSON dict alone left the
    # Markdown report discarding the field a second time.
    from abicheck.reporter import release_finding_detail_lines

    md_lines = release_finding_detail_lines(release_dicts[0])
    assert any(expected in line for line in md_lines)
