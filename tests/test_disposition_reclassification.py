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
``change_json.reclassified_by_for_change``/``reclassify.reclassify_rule_for_
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

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.detectors import DetectorResult
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.model.change import Change
from abicheck.policy.disposition_close import (
    ledger_for,
    reclassifications,
    reclassified_total,
)
from abicheck.policy_file import PolicyFile
from abicheck.report.disposition_audit import compute_disposition_audit
from abicheck.serialization import snapshot_to_json


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
    # P1: one row per configured rule, keyed by the rule itself. Its display
    # label stays the reason it was written with; its id is the canonical
    # identity, not that prose.
    (row,) = audit.reclassifications
    assert row.reason == "COMDAT-inline demotions"
    assert row.label is None
    assert row.matched_count == 1
    assert row.selector_matched_count == 1
    assert row.shadowed_count == 0
    assert row.status == "active"
    assert "symbol='foo'" in row.rule_id
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
    from abicheck.policy.suppression.rules import Suppression, SuppressionList

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


def test_an_unmatched_reclassify_rule_still_has_a_zero_row(tmp_path: Path) -> None:
    """P1 (ADR-067 D3): a configured rule that matched nothing is still a row.

    The rule below is configured and does not match the finding; the audit
    must say so (zero matches, status ``unused``) rather than leave the rule
    out, which is what a record-derived tally did.
    """
    pf = _policy_file(tmp_path, symbol="bar", reason="unrelated")
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        policy_file=pf,
        # The rule names func_removed, which the ``functions`` detector produces.
        # It ran, so the zero-match rule is unused (ADR-067 P1 addendum). Without
        # a detector record nothing is known to have run, and the rule reads
        # not_evaluated instead.
        detector_results=[DetectorResult(name="functions", changes_count=1)],
    )
    audit = compute_disposition_audit(diff)
    assert audit.reclassified_total == 0
    (row,) = audit.reclassifications
    assert row.matched_count == 0
    assert row.selector_matched_count == 0
    assert row.status == "unused"
    assert row.reason == "unrelated"


@pytest.mark.parametrize("verdict", [Verdict.COMPATIBLE_WITH_RISK, Verdict.COMPATIBLE])
def test_reclassification_is_read_not_recomputed_by_the_ledger(
    tmp_path: Path, verdict: Verdict
) -> None:
    """`resolve_reclassifications` reads `reclassify.reclassify_rule_for_
    change` -- the same resolver `change_json.reclassified_by_for_change`
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
        (ChangeKind.FUNC_REMOVED, "_Z6inlinev", "ignore"),
        (ChangeKind.VAR_REMOVED, "_Z3barv", "risk"),
    ],
)
def test_every_finding_dict_builder_agrees_on_reclassified_by(
    tmp_path: Path, kind: ChangeKind, symbol: str, to: str
) -> None:
    """The oneDAL-release bug: the release fan-out never stamped
    ``reclassified_by`` on its findings, although the disposition ledger counted
    the same reclassification. The invariant is parity across the public
    outputs: a reclassified change names its deciding reason in the single-pair
    JSON, the release JSON and the release Markdown, for more than one kind.
    """
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        f"reclassify:\n  - kind: {kind.value}\n    symbol: {symbol}\n"
        f'    to: {to}\n    reason: "inlines-hidden-demotion"\n',
        encoding="utf-8",
    )
    expected = "inlines-hidden-demotion"

    # Precondition, asserted rather than assumed: the ledger must see one real
    # reclassification, or the parity checks below pass vacuously.
    pf = PolicyFile.load(policy)
    change = Change(kind=kind, symbol=symbol, description="x")
    diff = DiffResult(
        changes=[change], old_version="1", new_version="2", library="l", policy_file=pf
    )
    ledger = ledger_for(diff)
    assert reclassified_total(ledger) == 1
    assert reclassifications(ledger) == ((expected, 1),)

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    _write_one_library_pair(old_dir, new_dir, kind, symbol)
    policy_args = ["--policy", str(policy)]

    single = _run_json(
        "compare",
        str(old_dir / "libfoo.json"),
        str(new_dir / "libfoo.json"),
        *policy_args,
        "-o",
        "json=-",
    )
    (single_change,) = [c for c in single["changes"] if c["kind"] == kind.value]
    assert single_change["reclassified_by"] == expected

    release = _run_json(
        "compare", str(old_dir), str(new_dir), *policy_args, "-o", "json=-"
    )
    (finding,) = [
        f for f in release["libraries"][0]["findings"] if f["kind"] == kind.value
    ]
    assert finding["reclassified_by"] == expected

    markdown = (
        CliRunner()
        .invoke(
            main,
            ["compare", str(old_dir), str(new_dir), *policy_args, "-o", "markdown=-"],
        )
        .stdout
    )
    assert f"_Reclassified by:_ {expected}" in markdown


def _write_one_library_pair(
    old_dir: Path, new_dir: Path, kind: ChangeKind, symbol: str
) -> None:
    """One library, one removed symbol of *kind*, as a directory-compare pair."""
    if kind is ChangeKind.FUNC_REMOVED:
        old = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                Function(
                    name=symbol,
                    mangled=symbol,
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
            from_headers=True,
        )
    else:
        old = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            variables=[Variable(name=symbol, mangled=symbol, type="int")],
            from_headers=True,
        )
    new = AbiSnapshot(library="libfoo.so", version="2.0", from_headers=True)
    (old_dir / "libfoo.json").write_text(snapshot_to_json(old), encoding="utf-8")
    (new_dir / "libfoo.json").write_text(snapshot_to_json(new), encoding="utf-8")


def _run_json(*args: str) -> dict:
    """Run the CLI and parse stdout (a breaking verdict exits non-zero but still
    writes its JSON, so the exit code is not asserted here)."""
    result = CliRunner().invoke(main, list(args))
    return json.loads(result.stdout)
