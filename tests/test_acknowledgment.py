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

"""ADR-067 D5/D6 (plan workstream C-S3): change-intent acknowledgment records.

Covers the acknowledgment model/loader (bounded selectors, ambiguity is a
hard error), the ledger overlay (``acknowledged_by``, conservation), the
additions-review gate (D6's allow/warn/block, orthogonal to the verdict and
folded into the exit code only under ``block``), and the vision.md invariant
that a broad rule or a baseline refresh is never an acknowledgment of
everything it happens to cover.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.model import AbiSnapshot
from abicheck.model.declarations import Function
from abicheck.policy.acknowledgment import (
    AcknowledgmentList,
    AmbiguousAcknowledgmentError,
)
from abicheck.policy.acknowledgment_gate import (
    evaluate_unacknowledged_additions,
    fold_additions_review_exit,
)
from abicheck.policy.acknowledgment_policy import AcknowledgmentPolicy
from abicheck.policy.disposition_close import (
    acknowledged_total,
    acknowledgments as ledger_acknowledgments,
    ledger_for,
)
from abicheck.policy_file import PolicyFile
from abicheck.report.disposition_audit import compute_disposition_audit


def _snapshot(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        version=version,
        library="libfoo",
        functions=functions,
        variables=[],
        types=[],
        enums=[],
    )


def _func(name: str) -> Function:
    return Function(name=name, mangled=name, return_type="void", params=[])


# --- loading / bounded matching (D5) --------------------------------------


def test_load_requires_finding_id_or_symbol(tmp_path: Path) -> None:
    p = tmp_path / "ack.yml"
    p.write_text("version: 1\nacknowledgments:\n  - reason: 'no selector'\n")
    with pytest.raises(ValueError, match="finding_id.*symbol"):
        AcknowledgmentList.load(p)


def test_load_requires_a_reason(tmp_path: Path) -> None:
    p = tmp_path / "ack.yml"
    p.write_text("version: 1\nacknowledgments:\n  - symbol: 'foo'\n")
    with pytest.raises(ValueError, match="reason"):
        AcknowledgmentList.load(p)


@pytest.mark.parametrize(
    "key,value",
    [
        ("symbol_pattern", "foo.*"),
        ("type_pattern", "Foo.*"),
        ("namespace", "**::detail::**"),
        ("entity_namespace", "**::detail::**"),
        ("cause_namespace", "**::detail::**"),
        ("source_location", "*/internal/*"),
        ("member_name", "value_type"),
        ("binding", "weak"),
    ],
)
def test_load_rejects_every_broad_suppression_only_selector(
    tmp_path: Path, key: str, value: str
) -> None:
    """vision.md: 'a broad regex is a suppression, not an acknowledgment.'

    Exercised against every broad selector suppression's grammar offers, not
    just one representative — the bug class this generalizes from (#753) was
    exactly a registry that silently omitted members from an enumeration."""
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\n"
        "acknowledgments:\n"
        f"  - {key}: {value!r}\n"
        "    reason: 'attempted broad rule'\n"
    )
    with pytest.raises(ValueError, match="suppression-only broad selector"):
        AcknowledgmentList.load(p)


def test_load_rejects_unknown_keys(tmp_path: Path) -> None:
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\nacknowledgments:\n  - symbol: foo\n    reason: x\n    bogus: 1\n"
    )
    with pytest.raises(ValueError, match="unknown key"):
        AcknowledgmentList.load(p)


def test_bounded_symbol_match_scoped_by_component_and_release(tmp_path: Path) -> None:
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\n"
        "acknowledgments:\n"
        "  - symbol: bar\n"
        "    component: libfoo\n"
        "    candidate: '2.0.0'\n"
        "    reason: 'intentional new API'\n"
    )
    acks = AcknowledgmentList.load(p)
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")

    assert acks.evaluate(change, component="libfoo", release_label="2.0.0") is not None
    # A caller that cannot supply the declared scope never gets a match --
    # D5's fail-closed rule, never "resolve to the nearest".
    assert acks.evaluate(change, component="libfoo", release_label="3.0.0") is None
    assert acks.evaluate(change, component="other-lib", release_label="2.0.0") is None
    assert acks.evaluate(change) is None


def test_baseline_is_enforced_the_same_way_as_candidate(tmp_path: Path) -> None:
    """A record naming one release transition (baseline -> candidate) must
    not silently also cover a comparison from a different baseline to the
    same candidate (CodeRabbit review, PR #1137)."""
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\n"
        "acknowledgments:\n"
        "  - symbol: bar\n"
        "    baseline: '1.0.0'\n"
        "    candidate: '2.0.0'\n"
        "    reason: 'intentional new API'\n"
    )
    acks = AcknowledgmentList.load(p)
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")

    assert acks.evaluate(change, baseline="1.0.0", release_label="2.0.0") is not None
    assert acks.evaluate(change, baseline="0.9.0", release_label="2.0.0") is None
    assert acks.evaluate(change, release_label="2.0.0") is None


def test_ambiguous_match_is_a_hard_error_not_a_nearest_match(tmp_path: Path) -> None:
    """D5: 'an ambiguous or unknown identity requires review and is never
    resolved to the nearest old acknowledgment.'"""
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\n"
        "acknowledgments:\n"
        "  - symbol: bar\n"
        "    reason: 'first'\n"
        "  - symbol: bar\n"
        "    reason: 'second, conflicting'\n"
    )
    acks = AcknowledgmentList.load(p)
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    with pytest.raises(AmbiguousAcknowledgmentError):
        acks.evaluate(change)


def test_expired_acknowledgment_never_matches(tmp_path: Path) -> None:
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\n"
        "acknowledgments:\n"
        "  - symbol: bar\n"
        "    reason: 'temporary'\n"
        "    expires: '2000-01-01'\n"
    )
    acks = AcknowledgmentList.load(p)
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    assert acks.evaluate(change) is None


def test_record_id_shared_scheme_reads_component_baseline_candidate() -> None:
    from abicheck.policy.acknowledgment import Acknowledgment

    record = Acknowledgment(
        symbol="bar",
        component="libfoo",
        baseline="1.0.0",
        candidate="2.0.0",
        reason="x",
    )
    assert record.record_id() == "libfoo::1.0.0::2.0.0::bar"
    bare = Acknowledgment(symbol="bar", reason="x")
    assert bare.record_id() == "*::*::*::bar"


# --- ledger overlay (D2/D5) ------------------------------------------------


def test_acknowledged_finding_keeps_its_disposition_and_verdict_class(
    tmp_path: Path,
) -> None:
    """D5: an acknowledged change 'keeps its verdict class, stays in the
    report... and contributes to the gate according to policy' -- it is
    never suppressed and never reclassified by acknowledging it."""
    p = tmp_path / "ack.yml"
    p.write_text(
        "version: 1\nacknowledgments:\n  - symbol: foo\n    reason: 'known break'\n"
    )
    acks = AcknowledgmentList.load(p)
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        acknowledgments=acks,
    )

    ledger = ledger_for(diff)
    record = ledger.record_for(change)
    assert record is not None
    assert record.acknowledged_by == "*::*::*::foo"
    # The disposition is whatever the gate decided independent of
    # acknowledgment -- D2 forbids acknowledgment from being a terminal
    # disposition of its own.
    from abicheck.policy.disposition_ledger import Disposition

    assert record.disposition is Disposition.GATING
    assert acknowledged_total(ledger) == 1
    assert ledger_acknowledgments(ledger) == (("*::*::*::foo", 1),)


def test_conservation_holds_with_acknowledgments(tmp_path: Path) -> None:
    """D1: acknowledging a finding never changes the detected total or moves
    it out of the per-disposition counts -- it is a pure overlay."""
    p = tmp_path / "ack.yml"
    p.write_text("version: 1\nacknowledgments:\n  - symbol: foo\n    reason: 'x'\n")
    change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")
    diff_without = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
    )
    diff_with = DiffResult(
        changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="x")],
        old_version="1",
        new_version="2",
        library="l",
        acknowledgments=AcknowledgmentList.load(p),
    )
    audit_without = compute_disposition_audit(diff_without)
    audit_with = compute_disposition_audit(diff_with)
    assert audit_without.detected_total == audit_with.detected_total
    assert audit_without.counts == audit_with.counts
    assert audit_with.acknowledged_total == 1


# --- additions review gate (D6) -------------------------------------------


def test_additions_review_default_policy_is_allow_and_never_gates() -> None:
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    result = evaluate_unacknowledged_additions([change], None, None)
    assert result.policy == "allow"
    assert result.gate_contribution == 0
    assert len(result.unacknowledged) == 1


def test_additions_review_warn_reports_but_never_gates() -> None:
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    policy = AcknowledgmentPolicy(unacknowledged_additions="warn")
    result = evaluate_unacknowledged_additions([change], None, policy)
    assert result.gate_contribution == 0
    assert len(result.unacknowledged) == 1


def test_additions_review_block_gates_only_when_unacknowledged() -> None:
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    policy = AcknowledgmentPolicy(unacknowledged_additions="block")

    unblocked = evaluate_unacknowledged_additions([change], None, policy)
    assert unblocked.gate_contribution == 1

    p_yaml = "version: 1\nacknowledgments:\n  - symbol: bar\n    reason: 'reviewed'\n"
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "ack.yml"
        path.write_text(p_yaml)
        acks = AcknowledgmentList.load(path)
    blocked_but_acked = evaluate_unacknowledged_additions([change], acks, policy)
    assert blocked_but_acked.gate_contribution == 0
    assert blocked_but_acked.unacknowledged == ()


def test_additions_review_never_changes_the_verdict_class() -> None:
    """D6: 'This folds through the existing gate/exit precedence... as
    policy acceptance, never as a reclassification of the addition into a
    break.'"""
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    policy = AcknowledgmentPolicy(unacknowledged_additions="block")
    result = evaluate_unacknowledged_additions([change], None, policy)
    assert result.gate_contribution == 1
    # The gate contribution is orthogonal -- the change's own kind is
    # untouched by this evaluation.
    assert change.kind is ChangeKind.FUNC_ADDED


def test_fold_additions_review_exit_never_lowers_a_real_gate_code() -> None:
    diff = DiffResult(changes=[], old_version="1", new_version="2", library="l")
    diff.unacknowledged_additions_review = evaluate_unacknowledged_additions(
        [Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")],
        None,
        AcknowledgmentPolicy(unacknowledged_additions="block"),
    )
    # A compatibility gate that already exited 4 (ABI break) is never
    # lowered by a 0/1 orthogonal axis.
    assert fold_additions_review_exit(4, diff) == 4
    # A clean 0 is raised to the axis's own contribution.
    assert fold_additions_review_exit(0, diff) == 1


def test_fold_additions_review_exit_is_zero_when_never_evaluated() -> None:
    """No `acknowledgments=...` was ever supplied -- the axis contributes
    nothing, so no pre-existing invocation's exit code moves."""
    diff = DiffResult(changes=[], old_version="1", new_version="2", library="l")
    assert fold_additions_review_exit(0, diff) == 0
    assert fold_additions_review_exit(4, diff) == 4


# --- policy file wiring (D6 config) ---------------------------------------


def test_policy_file_parses_acknowledgment_block(tmp_path: Path) -> None:
    p = tmp_path / "policy.yml"
    p.write_text(
        "base_policy: strict_abi\nacknowledgment:\n  unacknowledged_additions: warn\n"
    )
    pf = PolicyFile.load(p)
    assert pf.acknowledgment_policy_stated is True
    assert pf.acknowledgment_policy.unacknowledged_additions == "warn"


def test_policy_file_default_acknowledgment_policy_is_allow(tmp_path: Path) -> None:
    p = tmp_path / "policy.yml"
    p.write_text("base_policy: strict_abi\n")
    pf = PolicyFile.load(p)
    assert pf.acknowledgment_policy_stated is False
    assert pf.acknowledgment_policy.unacknowledged_additions == "allow"


def test_policy_file_rejects_invalid_acknowledgment_action(tmp_path: Path) -> None:
    p = tmp_path / "policy.yml"
    p.write_text(
        "base_policy: strict_abi\nacknowledgment:\n  unacknowledged_additions: nope\n"
    )
    with pytest.raises(Exception, match="invalid value"):
        PolicyFile.load(p)


@pytest.mark.parametrize("bad_yaml", ["[]", "{}"])
def test_policy_file_rejects_non_string_acknowledgment_action(
    tmp_path: Path, bad_yaml: str
) -> None:
    """A YAML list/mapping is valid `safe_load` input but unhashable --
    membership-testing it directly against the valid-action set would raise
    `TypeError` instead of the documented `PolicyError` (CodeRabbit review,
    PR #1137)."""
    p = tmp_path / "policy.yml"
    p.write_text(
        f"base_policy: strict_abi\nacknowledgment:\n  unacknowledged_additions: {bad_yaml}\n"
    )
    with pytest.raises(Exception, match="invalid value"):
        PolicyFile.load(p)


def test_acknowledgment_policy_validates_direct_construction() -> None:
    """`Literal` is not enforced at runtime -- a caller constructing
    `AcknowledgmentPolicy` directly (bypassing the YAML loader) must still be
    rejected for an invalid action (CodeRabbit review, PR #1137)."""
    with pytest.raises(ValueError, match="unacknowledged_additions"):
        AcknowledgmentPolicy(unacknowledged_additions="typo")  # type: ignore[arg-type]


# --- end-to-end through checker.compare() ---------------------------------


def test_compare_wires_acknowledgment_overlay_and_additions_gate(
    tmp_path: Path,
) -> None:
    from abicheck import checker

    old = _snapshot("1.0.0", [_func("foo")])
    new = _snapshot("2.0.0", [_func("foo"), _func("bar")])

    ack_path = tmp_path / "ack.yml"
    ack_path.write_text(
        "version: 1\nacknowledgments:\n  - symbol: bar\n    reason: 'planned'\n"
    )
    acks = AcknowledgmentList.load(ack_path)

    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "base_policy: strict_abi\nacknowledgment:\n  unacknowledged_additions: block\n"
    )
    pf = PolicyFile.load(policy_path)

    result = checker.compare(old, new, policy_file=pf, acknowledgments=acks)

    assert result.verdict == Verdict.COMPATIBLE
    review = result.unacknowledged_additions_review
    assert review is not None
    assert review.gate_contribution == 0  # the addition was acknowledged
    ledger = result.disposition_ledger
    record = ledger.record_for(next(c for c in result.changes if c.symbol == "bar"))
    assert record.acknowledged_by is not None


def test_compare_blocks_on_a_real_unacknowledged_addition(tmp_path: Path) -> None:
    from abicheck import checker
    from abicheck.frontends.cli.runtime import _exit_with_severity_or_verdict

    old = _snapshot("1.0.0", [_func("foo")])
    new = _snapshot("2.0.0", [_func("foo"), _func("bar")])

    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "base_policy: strict_abi\nacknowledgment:\n  unacknowledged_additions: block\n"
    )
    pf = PolicyFile.load(policy_path)

    result = checker.compare(
        old, new, policy_file=pf, acknowledgments=AcknowledgmentList([])
    )
    assert result.verdict == Verdict.COMPATIBLE
    with pytest.raises(SystemExit) as exc_info:
        _exit_with_severity_or_verdict(result, None, "legacy")
    assert exc_info.value.code == 1


# --- vision.md invariant ---------------------------------------------------


def test_a_baseline_refresh_never_implicitly_acknowledges_anything(
    tmp_path: Path,
) -> None:
    """vision.md: 'A baseline refresh or a broad ignore rule is not an
    acknowledgment of everything it happens to cover.'

    Simulated here as: loading a *new* baseline snapshot (i.e. producing a
    fresh comparison with no acknowledgment document supplied at all) must
    never cause any finding to read as acknowledged -- there is no implicit
    "the baseline moved, so everything in it is accepted" path anywhere in
    this mechanism.
    """
    old = _snapshot("1.0.0", [_func("foo")])
    new = _snapshot("2.0.0", [])  # foo removed -- a real breaking baseline "refresh"

    from abicheck import checker

    # No acknowledgments supplied at all -- exactly what "refreshing a
    # baseline" looks like from compare()'s perspective.
    result = checker.compare(old, new)
    ledger = result.disposition_ledger
    assert acknowledged_total(ledger) == 0
    for record in ledger.records:
        assert record.acknowledged_by is None
    # The removal is still a real, gating BREAKING finding -- nothing about
    # the absence of an acknowledgment document silently accepted it.
    assert result.verdict == Verdict.BREAKING


def test_an_acknowledgment_never_matches_a_different_finding_by_proximity(
    tmp_path: Path,
) -> None:
    """The bounded-selector half of the same invariant: an acknowledgment
    naming one exact symbol must never "spill over" onto a sibling finding
    that merely looks similar -- there is no fuzzy/nearest matching path."""
    p = tmp_path / "ack.yml"
    p.write_text("version: 1\nacknowledgments:\n  - symbol: foo_v2\n    reason: 'x'\n")
    acks = AcknowledgmentList.load(p)
    sibling = Change(kind=ChangeKind.FUNC_ADDED, symbol="foo_v3", description="x")
    assert acks.evaluate(sibling) is None


# --- ledger_for() no-mutation contract + fold (CodeRabbit review, PR #1137) -


def test_ledger_for_never_mutates_a_hand_built_result_with_acknowledgments() -> None:
    """`ledger_for()`'s fallback path (a `DiffResult` with no persisted
    ledger) must not write `unacknowledged_additions_review` back onto
    *result* -- that violates its own documented no-mutation contract."""
    change = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar", description="x")
    diff = DiffResult(
        changes=[change],
        old_version="1",
        new_version="2",
        library="l",
        acknowledgments=AcknowledgmentList([]),
    )
    assert diff.unacknowledged_additions_review is None
    ledger_for(diff)
    assert diff.unacknowledged_additions_review is None


def test_fold_disposition_audits_preserves_unacknowledged_entries() -> None:
    from abicheck.report.disposition_audit import (
        DispositionAudit,
        fold_disposition_audits,
    )

    zero = DispositionAudit(
        detected_total=0,
        effective_total=0,
        counts=(),
        rules=(),
        not_evaluated_detectors=(),
    )
    member_a = DispositionAudit(
        detected_total=1,
        effective_total=0,
        counts=(),
        rules=(),
        not_evaluated_detectors=(),
        unacknowledged_additions_review={
            "policy": "warn",
            "unacknowledged": [
                {"kind": "func_added", "symbol": "a", "finding_id": "1"}
            ],
            "gate_contribution": 0,
        },
    )
    member_b = DispositionAudit(
        detected_total=1,
        effective_total=0,
        counts=(),
        rules=(),
        not_evaluated_detectors=(),
        unacknowledged_additions_review={
            "policy": "block",
            "unacknowledged": [
                {"kind": "func_added", "symbol": "b", "finding_id": "2"}
            ],
            "gate_contribution": 1,
        },
    )
    folded = fold_disposition_audits([zero, member_a, member_b])
    review = folded.unacknowledged_additions_review
    assert review is not None
    assert review["gate_contribution"] == 1
    assert review["policy"] == "mixed"
    symbols = {entry["symbol"] for entry in review["unacknowledged"]}
    assert symbols == {"a", "b"}


def test_fold_disposition_audits_reports_none_when_no_member_evaluated() -> None:
    from abicheck.report.disposition_audit import (
        DispositionAudit,
        fold_disposition_audits,
    )

    folded = fold_disposition_audits(
        [
            DispositionAudit(
                detected_total=0,
                effective_total=0,
                counts=(),
                rules=(),
                not_evaluated_detectors=(),
            )
        ]
    )
    assert folded.unacknowledged_additions_review is None
