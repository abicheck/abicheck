# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The N-library audit's workflow, report and consumer contracts
(one-comparison-product F-23).

* **The acquisition record** (``workflows.no_baseline_set.
  build_no_baseline_set_record``) is checked over generated inputs against
  an oracle written here from ADR-065's stated precedence -- not by calling
  the release builders it is a sibling of -- plus the invariants no input
  may break: every member exactly once, OLD never present, no member ever
  ``not_supplied``, and therefore no proven removal or addition, *whatever*
  the inventory proves.
* **The per-member loop's error taxonomy** is checked over generated
  outcome sequences with an injected audit callable.
* **The document** validates against its published schema, and every
  member's findings flatten into the root with their member tag.
* **Every consumer** of audit documents reads the ``audit_set`` envelope
  as an audit: the Action's ``report_query``, ``aggregate``, and the PR
  comment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model.release_selection import ReleaseSelection
from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    SideInventory,
)
from abicheck.workflows.no_baseline_compare import NoBaselineAuditInputs
from abicheck.workflows.no_baseline_set import (
    MemberAudit,
    MemberAuditStatus,
    NoBaselineSetPlan,
    build_no_baseline_set_record,
    run_no_baseline_set,
)
from abicheck.workflows.release_inputs import ReleaseSide

_KEYS = ("liba.so", "libb.so", "libc.so", "libd.so", "libe.so")


def _plan(
    discovered: set[str],
    *,
    failed: dict[str, str] | None = None,
    unproduced: dict[str, str] | None = None,
    selection: ReleaseSelection | None = None,
    proven: bool = False,
) -> NoBaselineSetPlan:
    side = ReleaseSide(
        side="new",
        stored=False,
        members={k: Path("/candidate") / k for k in sorted(discovered)},
        files=[],
        lib_dir=Path("/candidate"),
        debug_dir=None,
        header_dir=None,
        symbols_file=None,
        headers=[],
        includes=[],
    )
    return NoBaselineSetPlan(
        operand=Path("/candidate"),
        operand_kind="package" if proven else "directory",
        side=side,
        inventory=SideInventory(
            InventoryCompleteness.PROVEN if proven else InventoryCompleteness.UNPROVEN,
            "test",
        ),
        selection=selection,
        failed=dict(failed or {}),
        unproduced=dict(unproduced or {}),
    )


def _oracle_state(
    key: str,
    *,
    discovered: set[str],
    failed: dict[str, str],
    unproduced: dict[str, str],
    declared: dict[str, bool] | None,
    outcome: dict[str, MemberAuditStatus],
) -> str:
    """ADR-065's precedence, restated: selection first (an undeclared member
    is never this run's concern), then absence, then pre-audit failure, then
    the audit's own outcome."""
    if declared is not None and key not in declared:
        return "out_of_scope"
    if key not in discovered and key not in failed and key not in unproduced:
        return "expected_not_produced"
    if key in unproduced:
        return "expected_not_produced"
    if key in failed:
        return "failed"
    return {
        MemberAuditStatus.COMPLETED: "declared_absent",
        MemberAuditStatus.UNSUPPORTED: "unsupported",
        MemberAuditStatus.FAILED: "failed",
    }[outcome[key]]


_subsets = st.sets(st.sampled_from(_KEYS))


@settings(max_examples=150, deadline=None)
@given(
    discovered=_subsets,
    failed_keys=_subsets,
    unproduced_keys=_subsets,
    declared=st.none() | st.dictionaries(st.sampled_from(_KEYS), st.booleans()),
    outcomes=st.lists(st.sampled_from(list(MemberAuditStatus)), min_size=5, max_size=5),
    proven=st.booleans(),
)
def test_the_record_follows_the_stated_precedence_and_never_reads_absence_as_removal(
    discovered, failed_keys, unproduced_keys, declared, outcomes, proven
) -> None:
    # A failed (unclassified/degraded) member is one the operand did ship;
    # an unproduced one is declared by the inventory but was never selected.
    failed = {k: f"{k} boom" for k in failed_keys & discovered}
    unproduced = {k: f"{k} dangling" for k in unproduced_keys - discovered}
    selection = (
        ReleaseSelection.from_lists(
            required=[k for k, req in declared.items() if req],
            optional=[k for k, req in declared.items() if not req],
        )
        if declared
        else None
    )
    declared_map = dict(selection.members) if selection is not None else None
    plan = _plan(
        discovered,
        failed=failed,
        unproduced=unproduced,
        selection=selection,
        proven=proven,
    )
    outcome = dict(zip(_KEYS, outcomes, strict=True))
    audits = [
        MemberAudit(member=k, path=plan.side.members[k], status=outcome[k], reason="x")
        for k in plan.audit_keys
    ]
    record = build_no_baseline_set_record(plan, audits)

    expected_keys = set(discovered) | set(unproduced) | set(declared_map or {})
    assert [m.member for m in record.members] == sorted(expected_keys)
    for m in record.members:
        assert m.state.value == _oracle_state(
            m.member,
            discovered=discovered,
            failed=failed,
            unproduced=unproduced,
            declared=declared_map,
            outcome=outcome,
        ), m
        assert m.old_present is False
        assert m.state is not AcquisitionState.NOT_SUPPLIED
        expected_required = (
            True
            if m.state is AcquisitionState.OUT_OF_SCOPE or declared_map is None
            else declared_map[m.member]
        )
        assert m.required is expected_required
    # The deletion gate cannot open on a declared-absent OLD, whatever NEW's
    # inventory proves.
    assert record.proven_removed_members == ()
    assert record.proven_added_members == ()
    assert record.old_inventory.completeness is InventoryCompleteness.UNPROVEN
    completed = {k for k, s in outcome.items() if s is MemberAuditStatus.COMPLETED}
    assert record.no_comparison_completed is not (set(plan.audit_keys) & completed)


@settings(max_examples=60, deadline=None)
@given(
    outcomes=st.lists(
        st.sampled_from(["ok", "unsupported", "snapshot", "unexpected"]),
        min_size=1,
        max_size=5,
    )
)
def test_the_loop_classifies_every_failure_and_drops_no_member(outcomes) -> None:
    from abicheck.errors import SnapshotError, UnsupportedArtifactError

    keys = _KEYS[: len(outcomes)]
    behaviour = dict(zip(keys, outcomes, strict=True))
    sentinel = object()

    def audit(path: Path, inputs: NoBaselineAuditInputs):
        kind = behaviour[path.name]
        if kind == "unsupported":
            raise UnsupportedArtifactError("no backend")
        if kind == "snapshot":
            raise SnapshotError("unreadable")
        if kind == "unexpected":
            raise KeyError("internal")
        return sentinel

    result = run_no_baseline_set(_plan(set(keys)), NoBaselineAuditInputs(), audit=audit)
    assert [a.member for a in result.audits] == list(keys)
    expected = {
        "ok": MemberAuditStatus.COMPLETED,
        "unsupported": MemberAuditStatus.UNSUPPORTED,
        "snapshot": MemberAuditStatus.FAILED,
        "unexpected": MemberAuditStatus.FAILED,
    }
    for a in result.audits:
        assert a.status is expected[behaviour[a.member]]
        assert (a.result is sentinel) is (a.status is MemberAuditStatus.COMPLETED)
        assert a.status is MemberAuditStatus.COMPLETED or a.reason


def test_a_member_validation_error_fails_that_member_only() -> None:
    """A ValidationError raised while auditing one member is about that
    member's content (an undetectable format, say) -- the invocation was
    validated before the loop. It is recorded against the member, as the
    release fan-out records it, and every other member is still audited."""
    from abicheck.errors import ValidationError

    sentinel = object()

    def audit(path: Path, inputs: NoBaselineAuditInputs):
        if path.name == "libbad.so":
            raise ValidationError("Cannot detect format of 'libbad.so'")
        return sentinel

    result = run_no_baseline_set(
        _plan({"liba.so", "libbad.so", "libc.so"}), NoBaselineAuditInputs(), audit=audit
    )
    status = {a.member: a.status for a in result.audits}
    assert status == {
        "liba.so": MemberAuditStatus.COMPLETED,
        "libbad.so": MemberAuditStatus.FAILED,
        "libc.so": MemberAuditStatus.COMPLETED,
    }
    bad = next(a for a in result.audits if a.member == "libbad.so")
    assert "Cannot detect format" in bad.reason


def test_package_roots_are_added_to_not_replacing_the_shared_inputs() -> None:
    from dataclasses import replace

    from abicheck.workflows.no_baseline_set import member_audit_inputs

    plan = _plan({"liba.so"})
    shared = NoBaselineAuditInputs(
        headers=(Path("inc"),), includes=(Path("i"),), debug_roots=(Path("d"),)
    )
    assert member_audit_inputs(plan, shared) is shared
    pkg = replace(
        plan,
        side=replace(
            plan.side, header_dir=Path("/x/usr/include"), debug_dir=Path("/x/dbg")
        ),
    )
    merged = member_audit_inputs(pkg, shared)
    assert merged.headers == (Path("inc"), Path("/x/usr/include"))
    assert merged.public_header_dirs == (Path("/x/usr/include"),)
    assert merged.debug_roots == (Path("d"), Path("/x/dbg"))
    assert merged.includes[0] == Path("i")


def test_the_one_sided_resolver_finds_what_the_two_sided_release_pairs(
    tmp_path: Path,
) -> None:
    """``resolve_release_side`` is the per-side half ``prepare_release_inputs``
    now calls twice: the member map one side resolves alone must be exactly
    the map that side contributes to a two-sided release."""
    from test_compare_no_baseline_set import _populate

    from abicheck.workflows.no_baseline_set import resolve_no_baseline_set_plan
    from abicheck.workflows.release_request import (
        ReleaseCompareRequest,
        resolve_release_compare_plan,
    )

    old, new = tmp_path / "old", tmp_path / "new"
    _populate(old, ("liba.abi.json", "libb.abi.json"))
    _populate(new, ("liba.abi.json", "libc.abi.json"))
    two_sided = resolve_release_compare_plan(
        ReleaseCompareRequest(old_dir=old, new_dir=new)
    )
    one_sided = resolve_no_baseline_set_plan(new, operand_kind="directory")
    assert dict(one_sided.side.members) == dict(two_sided.scope.new_map)
    assert one_sided.inventory == two_sided.scope.evidence.new


# ---------------------------------------------------------------------------
# The document, its schema, and its consumers -- over one real run.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def audit_set_doc(tmp_path_factory: pytest.TempPathFactory) -> dict:
    from click.testing import CliRunner
    from test_compare_no_baseline_set import _populate

    from abicheck.cli import main

    root = tmp_path_factory.mktemp("set") / "release"
    _populate(
        root, ("liba.abi.json", "libb.abi.json", "libc.abi.json", "libd.abi.json")
    )
    (root / "libbad.abi.json").write_text('{"library": "libbad.so", "functions": 7}')
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--no-baseline",
            str(root),
            "--select-required",
            "liba.abi.json",
            "--select-required",
            "libb.abi.json",
            "--select-required",
            "libc.abi.json",
            "--select-required",
            "libbad.abi.json",
            "--select-required",
            "libgone.so",
            "--contract",
            "public",
            "-o",
            "json=-",
        ],
    )
    return json.loads(result.stdout)


def test_the_document_validates_against_its_published_schema(
    audit_set_doc: dict,
) -> None:
    pytest.importorskip("jsonschema")
    from schema_validation import validate_instance

    from abicheck.report.no_baseline_set import AUDIT_SET_REPORT_SCHEMA_VERSION
    from abicheck.schemas import (
        AUDIT_SET_REPORT_SCHEMA_PATH,
        load_audit_set_report_schema,
    )

    schema = load_audit_set_report_schema()
    validate_instance(audit_set_doc, schema)
    assert (
        audit_set_doc["audit_set_report_schema_version"]
        == AUDIT_SET_REPORT_SCHEMA_VERSION
    )
    assert (
        f'currently "{AUDIT_SET_REPORT_SCHEMA_VERSION}"'
        in schema["properties"]["audit_set_report_schema_version"]["description"]
    )
    published = (
        Path(__file__).resolve().parent.parent
        / "docs/reference/schemas/v1/audit_set_report.schema.json"
    )
    assert published.read_text() == AUDIT_SET_REPORT_SCHEMA_PATH.read_text()
    # Every embedded member report is itself a valid single-build audit.
    from abicheck.schemas import load_audit_report_schema

    for member in audit_set_doc["members"]:
        if member["report"] is not None:
            validate_instance(member["report"], load_audit_report_schema())


def test_findings_flatten_with_their_member_and_nothing_evolves(
    audit_set_doc: dict,
) -> None:
    per_member = [
        (m["member"], f)
        for m in audit_set_doc["members"]
        if m["report"] is not None
        for f in m["report"]["findings"]
    ]
    assert [
        (f["member"], {k: v for k, v in f.items() if k != "member"})
        for f in audit_set_doc["findings"]
    ] == per_member
    assert per_member, "the fixture must exercise at least one finding"
    assert audit_set_doc["changes"] == [] and audit_set_doc["verdict"] is None
    for _member, finding in per_member:
        assert finding.get("evolution") not in {"introduced", "resolved"}
    scope = audit_set_doc["comparison_scope"]
    assert scope["proven_removed"] == scope["proven_added"] == []
    for member in audit_set_doc["members"]:
        report = member["report"]
        if report is not None:
            assert report["changes"] == [] and report["verdict"] is None
            assert report["old_acquisition_state"] == "declared_absent"


def test_the_actions_report_reader_reads_it_as_an_audit(audit_set_doc: dict) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _action_report_reader import rq

    assert rq._carries_a_result(audit_set_doc)  # noqa: SLF001
    assert rq.answer(audit_set_doc, "no_baseline_audit") == "findings"
    assert rq.answer(audit_set_doc, "compat_verdict") == ""
    assert rq.answer(audit_set_doc, "assurance_axis") == "not_gated"
    # libgone.so and libbad were unchecked; under the default warn policy the
    # scope axis records it without gating.
    assert rq.answer(audit_set_doc, "scope_incomplete") == "1"
    assert rq.answer(audit_set_doc, "scope_contribution") == "0"
    assert "libgone.so" in rq.answer(audit_set_doc, "scope_where")
    assert rq.answer(audit_set_doc, "coverage_contribution") == str(
        audit_set_doc["exit_axes"]["contract_coverage"]
    )


def test_aggregate_folds_it_as_a_completed_audit_with_its_findings(
    audit_set_doc: dict, tmp_path: Path
) -> None:
    from abicheck.workflows.aggregate import ExpectedTargets, aggregate_reports_dir

    (tmp_path / "abi-report-linux-x86_64.json").write_text(json.dumps(audit_set_doc))
    r = aggregate_reports_dir(
        tmp_path, expected=ExpectedTargets.from_lists(["linux-x86_64"], [])
    )
    (target,) = r.targets
    assert target.compatibility_verdict is None
    assert target.findings is not None and target.findings.complete
    assert len(target.findings.findings) == len(audit_set_doc["findings"])
    # A member's audit failed, so the set did not complete operationally.
    assert audit_set_doc["run_outcome"]["operational"] == "extraction_error"
    assert target.analyzed is False
    assert r.exit_code() != 0


def test_the_pr_comment_renders_it_as_an_audit_with_its_scope_notice(
    audit_set_doc: dict,
) -> None:
    from abicheck.pr_comment import build_model, render_comment

    model = build_model(audit_set_doc)
    assert model.no_baseline_audit is True
    assert model.no_baseline_audit_blocking is (audit_set_doc["exit_code"] > 0)
    # Every member's findings, plus each contract-coverage failure the
    # comment itemizes as an incomplete-evidence row.
    assert model.total_changes == len(audit_set_doc["findings"]) + len(
        audit_set_doc["contract_coverage_failures"]
    )
    assert model.scope_notice is not None and "libgone.so" in model.scope_notice
    body = render_comment(model, sha="abc")
    assert "no baseline" in body
