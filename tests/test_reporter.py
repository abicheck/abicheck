"""Tests for abi_check.reporter — JSON and Markdown output."""
import json

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.reporter import to_json, to_markdown, to_review_digest


class TestReviewDigest:
    def test_breaking_digest_has_verdict_and_recommendation(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        out = to_review_digest(_result(Verdict.BREAKING, changes=[c]))
        assert "ABI review" in out
        assert "`BREAKING`" in out
        assert "Release recommendation:" in out
        assert "_Z3foov" in out  # top impacted symbol

    def test_manual_review_banner_on_scope_fallback(self):
        r = _result(Verdict.BREAKING, changes=[
            Change(ChangeKind.FUNC_REMOVED, "x", "removed"),
        ])
        r.scope_to_public_surface = True
        r.scope_resolved = False
        out = to_review_digest(r)
        assert "Manual review required" in out
        assert "unconfirmed" in out.lower()

    def test_no_banner_when_scope_resolved(self):
        r = _result(Verdict.COMPATIBLE)
        r.scope_to_public_surface = True
        r.scope_resolved = True
        out = to_review_digest(r)
        assert "Manual review required" not in out
        # Scoped reports label additions as public and show the filtered row.
        assert "Public additions" in out
        assert "Filtered (internal/private)" in out

    def test_top_impacted_symbols_truncated(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, f"sym{i}", f"removed sym{i}")
            for i in range(13)
        ]
        out = to_review_digest(_result(Verdict.BREAKING, changes=changes))
        # Only the first 10 are listed, with a "… and N more" line.
        assert "and 3 more" in out
        assert "`sym0`" in out
        assert "`sym12`" not in out


class TestReviewDigestSeverityAware:
    """Without severity_config, the merge-effect phrase is inferred purely
    from the compatibility verdict — which can misreport the actual CI gate
    once severity configuration is in play (compatibility and "blocks CI" are
    independent decisions). These guard the fix."""

    def test_compatible_addition_configured_as_error_is_not_safe_to_merge(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = _result(Verdict.COMPATIBLE, changes=[c])

        # Legacy (no severity_config): the old, misleading claim.
        legacy = to_review_digest(result)
        assert "safe to merge" in legacy

        cfg = resolve_severity_config("default", addition="error")
        out = to_review_digest(result, severity_config=cfg)
        assert "safe to merge" not in out
        assert "blocked by severity policy" in out

    def test_breaking_demoted_to_non_error_is_not_reported_as_blocking(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        result = _result(Verdict.BREAKING, changes=[c])

        # Legacy (no severity_config): claims it blocks merge under a strict gate.
        legacy = to_review_digest(result)
        assert "blocks merge" in legacy

        cfg = resolve_severity_config("default", abi_breaking="info")
        out = to_review_digest(result, severity_config=cfg)
        assert "blocks merge" not in out
        assert "safe to merge" in out

    def test_severity_config_confirms_a_real_block(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        result = _result(Verdict.BREAKING, changes=[c])
        cfg = resolve_severity_config("default")  # default: abi_breaking=error
        out = to_review_digest(result, severity_config=cfg)
        assert "blocked by severity policy" in out


def _result(verdict: Verdict, changes=None) -> DiffResult:
    return DiffResult(
        old_version="1.0", new_version="2.0",
        library="libtest.so.1",
        changes=changes or [],
        verdict=verdict,
    )


class TestJsonReporter:
    def test_no_change_json(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r))
        assert d["verdict"] == "NO_CHANGE"
        assert d["summary"]["total_changes"] == 0

    def test_breaking_json_has_changes(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r))
        assert d["verdict"] == "BREAKING"
        assert d["summary"]["breaking"] == 1
        assert d["changes"][0]["kind"] == "func_removed"

    def test_stat_forwards_severity_config(self):
        """Codex review: to_json(stat=True, severity_config=...) returned
        before forwarding severity_config to to_stat_json, so a caller going
        through to_json directly (not service.render_output) silently lost
        the severity block/exit code in stat JSON output."""
        from abicheck.severity import PRESET_DEFAULT

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r, stat=True, severity_config=PRESET_DEFAULT))
        assert "severity" in d


class TestEvidenceStatusInJson:
    """The per-finding `evidence_status` field (schema 2.2): the epistemic
    label a finding's verdict implies — see checker_policy.EvidenceStatus."""

    def test_breaking_change_is_artifact_proven(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r))
        assert d["changes"][0]["evidence_status"] == "artifact_proven"

    def test_api_break_change_is_source_contract(self):
        c = Change(ChangeKind.FIELD_RENAMED, "s", "field renamed")
        r = _result(Verdict.API_BREAK, changes=[c])
        d = json.loads(to_json(r))
        assert d["changes"][0]["evidence_status"] == "source_contract"

    def test_risk_change_is_contextual_risk(self):
        c = Change(
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
            "libc.so.6",
            "New GLIBC_2.34 version requirement added",
        )
        r = _result(Verdict.COMPATIBLE_WITH_RISK, changes=[c])
        d = json.loads(to_json(r))
        assert d["changes"][0]["evidence_status"] == "contextual_risk"

    def test_compatible_change_has_no_evidence_status(self):
        c = Change(ChangeKind.FUNC_ADDED, "s", "function added")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r))
        assert "evidence_status" not in d["changes"][0]

    def test_evidence_required_missing_is_not_checkable(self):
        c = Change(ChangeKind.EVIDENCE_REQUIRED_MISSING, "s", "required evidence missing")
        r = _result(Verdict.API_BREAK, changes=[c])
        d = json.loads(to_json(r))
        assert d["changes"][0]["evidence_status"] == "not_checkable"

    def test_report_schema_version_matches_constant(self):
        from abicheck.schemas import REPORT_SCHEMA_VERSION

        d = json.loads(to_json(_result(Verdict.NO_CHANGE)))
        assert d["report_schema_version"] == REPORT_SCHEMA_VERSION

    def test_change_operation_field(self):
        added = Change(ChangeKind.FUNC_ADDED, "s1", "added")
        removed = Change(ChangeKind.FUNC_REMOVED, "s2", "removed")
        modified = Change(ChangeKind.FUNC_PARAMS_CHANGED, "s3", "params changed")
        r = _result(Verdict.BREAKING, changes=[added, removed, modified])
        d = json.loads(to_json(r))
        by_symbol = {c["symbol"]: c["operation"] for c in d["changes"]}
        assert by_symbol == {"s1": "added", "s2": "removed", "s3": "modified"}

    def test_change_operation_field_experimental_graduated(self):
        """Codex review on #557: experimental_graduated (ADDITION_KINDS) was
        misclassified as operation="modified" since its kind name contains
        no "_added" suffix."""
        c = Change(ChangeKind.EXPERIMENTAL_GRADUATED, "lib::sort", "graduated to stable")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r))
        assert d["changes"][0]["operation"] == "added"

    def test_change_recommended_action_field(self):
        breaking = Change(ChangeKind.FUNC_REMOVED, "s1", "removed")
        api_break = Change(ChangeKind.FIELD_RENAMED, "s2", "renamed")
        risk = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "s3", "version req added")
        quality = Change(ChangeKind.VISIBILITY_LEAK, "s4", "visibility leak")
        addition = Change(ChangeKind.FUNC_ADDED, "s5", "added")
        r = _result(
            Verdict.BREAKING,
            changes=[breaking, api_break, risk, quality, addition],
        )
        d = json.loads(to_json(r))
        by_symbol = {c["symbol"]: c["recommended_action"] for c in d["changes"]}
        assert by_symbol == {
            "s1": "recompile_and_relink_required",
            "s2": "recompile_required",
            "s3": "verify_deployment_compatibility",
            "s4": "review_recommended",
            "s5": "no_action_required",
        }

    def test_recommended_action_honours_policy_file_override(self):
        """recommended_action must reflect the *effective* verdict (honouring
        a PolicyFile override), not the kind's raw default verdict — same
        resolver `severity`/`operation`/`finding_id` already use."""
        from abicheck.policy_file import PolicyFile

        c = Change(ChangeKind.FUNC_REMOVED, "s", "removed")
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
        r = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest.so",
            changes=[c], verdict=Verdict.COMPATIBLE, policy_file=pf,
        )
        d = json.loads(to_json(r))
        # func_removed is not itself an addition kind -> quality issue, not
        # "no action required".
        assert d["changes"][0]["recommended_action"] == "review_recommended"

    def test_reviewer_action_present_only_for_additions(self):
        # reviewer_action refines the ambiguous "no_action_required" bucket
        # with what a *reviewer* (not the old binary consumer) should check;
        # every other verdict already has reviewer-actionable guidance via
        # recommended_action itself, so the key is omitted there.
        breaking = Change(ChangeKind.FUNC_REMOVED, "s1", "removed")
        api_break = Change(ChangeKind.FIELD_RENAMED, "s2", "renamed")
        risk = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "s3", "version req added")
        quality = Change(ChangeKind.VISIBILITY_LEAK, "s4", "visibility leak")
        addition = Change(ChangeKind.FUNC_ADDED, "s5", "added")
        r = _result(
            Verdict.BREAKING,
            changes=[breaking, api_break, risk, quality, addition],
        )
        d = json.loads(to_json(r))
        by_symbol = {c["symbol"]: c for c in d["changes"]}
        for sym in ("s1", "s2", "s3", "s4"):
            assert "reviewer_action" not in by_symbol[sym]
        assert by_symbol["s5"]["reviewer_action"] == "confirm_public_api_intent"

    def test_reviewer_action_per_kind_overrides(self):
        enum_member = Change(ChangeKind.ENUM_MEMBER_ADDED, "E::X", "added")
        graduated = Change(ChangeKind.EXPERIMENTAL_GRADUATED, "foo_v2", "graduated")
        r = _result(Verdict.COMPATIBLE, changes=[enum_member, graduated])
        d = json.loads(to_json(r))
        by_symbol = {c["symbol"]: c["reviewer_action"] for c in d["changes"]}
        assert by_symbol == {
            "E::X": "review_exhaustive_switches",
            "foo_v2": "document_stable_replacement",
        }

    def test_reviewer_action_honours_policy_file_override(self):
        """A kind demoted to COMPATIBLE by a policy override, and classified
        as an addition, must still get reviewer_action — same effective-
        verdict/category resolver recommended_action uses."""
        from abicheck.policy_file import PolicyFile

        c = Change(ChangeKind.FUNC_ADDED, "s", "added")
        pf = PolicyFile(overrides={ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE})
        r = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest.so",
            changes=[c], verdict=Verdict.COMPATIBLE, policy_file=pf,
        )
        d = json.loads(to_json(r))
        assert d["changes"][0]["reviewer_action"] == "confirm_public_api_intent"

    def test_finding_id_is_stable_and_deterministic(self):
        """Same underlying finding -> same finding_id across independent runs."""
        c1 = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        c2 = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        r1 = _result(Verdict.BREAKING, changes=[c1])
        r2 = _result(Verdict.BREAKING, changes=[c2])
        d1 = json.loads(to_json(r1))
        d2 = json.loads(to_json(r2))
        fid1 = d1["changes"][0]["finding_id"]
        fid2 = d2["changes"][0]["finding_id"]
        assert fid1 == fid2
        assert isinstance(fid1, str) and len(fid1) == 16

    def test_finding_id_differs_for_different_findings(self):
        c1 = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        c2 = Change(ChangeKind.FUNC_REMOVED, "_Z3barv", "removed: bar")
        r = _result(Verdict.BREAKING, changes=[c1, c2])
        d = json.loads(to_json(r))
        assert d["changes"][0]["finding_id"] != d["changes"][1]["finding_id"]

    def test_finding_id_unaffected_by_policy(self):
        """finding_id excludes policy-derived fields — the same underlying
        finding must hash identically regardless of --policy."""
        from abicheck.policy_file import PolicyFile

        c1 = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        c2 = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
        r1 = _result(Verdict.BREAKING, changes=[c1])
        r2 = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest.so",
            changes=[c2], verdict=Verdict.COMPATIBLE, policy_file=pf,
        )
        d1 = json.loads(to_json(r1))
        d2 = json.loads(to_json(r2))
        assert d1["changes"][0]["finding_id"] == d2["changes"][0]["finding_id"]
        # Confirm the policy override really did take effect (different
        # severity), so this is a meaningful same-ID-despite-different-
        # policy check, not a vacuous one.
        assert d1["changes"][0]["severity"] != d2["changes"][0]["severity"]

    def test_finding_id_differs_for_same_kind_symbol_and_values(self):
        """Two findings on the same symbol, same kind, same old/new value,
        and no distinct source location (e.g. the same pointer-depth
        transition on two different parameters of one function) must not
        collide on finding_id — description carries the per-finding detail
        (parameter name/index here) that disambiguates them (Codex review,
        PR #557)."""
        c1 = Change(
            ChangeKind.PARAM_POINTER_LEVEL_CHANGED, "_Z3foov",
            "Parameter 'x' pointer level changed from 1 to 2",
            old_value="1", new_value="2",
        )
        c2 = Change(
            ChangeKind.PARAM_POINTER_LEVEL_CHANGED, "_Z3foov",
            "Parameter 'y' pointer level changed from 1 to 2",
            old_value="1", new_value="2",
        )
        r = _result(Verdict.BREAKING, changes=[c1, c2])
        d = json.loads(to_json(r))
        assert d["changes"][0]["finding_id"] != d["changes"][1]["finding_id"]

    def test_severity_blocking_fields_present_when_configured(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "s", "added")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        cfg = resolve_severity_config("default", addition="error")
        d = json.loads(to_json(r, severity_config=cfg))
        assert d["severity"]["blocking"] is True
        assert d["severity"]["blocking_categories"] == ["addition"]

    def test_severity_blocking_false_when_no_error_level_findings(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "s", "added")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        cfg = resolve_severity_config("default")
        d = json.loads(to_json(r, severity_config=cfg))
        assert d["severity"]["blocking"] is False
        assert d["severity"]["blocking_categories"] == []

    def test_blocking_categories_survives_show_only_hiding_the_blocker(self):
        """Verified defect (Codex review on #557): blocking_categories was
        derived from the *display* changes (post-show_only), while exit_code
        already correctly used the unfiltered set. Hiding the one finding
        that's actually blocking the build via --show-only must not make
        blocking_categories silently empty out from under a still-nonzero
        exit_code/blocking=true."""
        from abicheck.severity import resolve_severity_config

        addition = Change(ChangeKind.FUNC_ADDED, "s1", "added")
        breaking = Change(ChangeKind.FUNC_REMOVED, "s2", "removed")
        r = _result(Verdict.BREAKING, changes=[addition, breaking])
        cfg = resolve_severity_config("default", addition="error")
        # --show-only=breaking hides the ADDITION finding from `changes[]`,
        # but it must not hide it from the gate summary: the addition is
        # still what's configured to fail the build.
        d = json.loads(to_json(r, show_only="breaking", severity_config=cfg))
        assert d["severity"]["blocking"] is True
        assert set(d["severity"]["blocking_categories"]) == {"abi_breaking", "addition"}
        # The addition finding itself is correctly hidden from the display
        # `changes[]` by --show-only — only the gate summary must see it.
        assert len(d["changes"]) == 1

    def test_leaf_mode_root_type_change_carries_evidence_status(self):
        # Regression (Codex review): --report-mode leaf serializes root type
        # changes via a separate _leaf_entry() path, not _change_to_dict() —
        # evidence_status must be populated there too.
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Cfg", "struct Cfg grew")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r, report_mode="leaf"))
        assert d["leaf_changes"][0]["evidence_status"] == "artifact_proven"
        # And the top-level `changes` union (leaf_changes + non_type_changes,
        # kept for backward-compat consumers) carries it too.
        assert d["changes"][0]["evidence_status"] == "artifact_proven"

    def test_leaf_mode_root_type_change_carries_schema_2_3_fields(self):
        """Codex review on #557: _leaf_entry() builds its own dict rather
        than routing through _change_to_dict(), so root type changes in
        leaf_changes[]/changes[] were missing the schema 2.3 `operation`/
        `finding_id` fields present on non-type leaf entries and full-mode
        entries — breaking finding_id correlation for leaf-mode reports."""
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Cfg", "struct Cfg grew")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r, report_mode="leaf"))
        assert d["leaf_changes"][0]["operation"] == "modified"
        assert isinstance(d["leaf_changes"][0]["finding_id"], str)
        assert len(d["leaf_changes"][0]["finding_id"]) == 16
        assert d["changes"][0]["operation"] == "modified"
        assert d["changes"][0]["finding_id"] == d["leaf_changes"][0]["finding_id"]

        # Same finding_id as full (non-leaf) mode for the identical change —
        # the fingerprint must not depend on which report mode built it.
        full_d = json.loads(to_json(_result(Verdict.BREAKING, changes=[c])))
        assert full_d["changes"][0]["finding_id"] == d["leaf_changes"][0]["finding_id"]

    def test_leaf_mode_root_type_change_carries_recommended_action(self):
        c = Change(ChangeKind.TYPE_SIZE_CHANGED, "Cfg", "struct Cfg grew")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r, report_mode="leaf"))
        assert d["leaf_changes"][0]["recommended_action"] == "recompile_and_relink_required"
        assert d["changes"][0]["recommended_action"] == "recompile_and_relink_required"

    def test_leaf_mode_root_type_change_carries_reviewer_action(self):
        # enum_member_added is both a root-type-change kind (routed through
        # _leaf_entry, which builds its own dict rather than reusing
        # _change_to_dict) and an addition -- must carry reviewer_action in
        # both leaf_changes[] and changes[], matching full-mode entries.
        c = Change(ChangeKind.ENUM_MEMBER_ADDED, "E::X", "added")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r, report_mode="leaf"))
        assert d["leaf_changes"][0]["reviewer_action"] == "review_exhaustive_switches"
        assert d["changes"][0]["reviewer_action"] == "review_exhaustive_switches"

    def test_leaf_mode_root_type_change_honours_frozen_namespace_floor(self):
        """Codex review on #549: a policy-file override that demotes a root
        type kind (type_size_changed) to COMPATIBLE must not silently drop a
        frozen_namespace_violation-tagged finding below its raw severity in
        leaf_changes — the top-level `severity` block already honours
        policy_file (via _build_severity_json), so leaf_changes reading
        "compatible" for the same finding would be a direct contradiction."""
        from abicheck.policy_file import PolicyFile
        from abicheck.severity import PRESET_DEFAULT

        c = Change(
            ChangeKind.TYPE_SIZE_CHANGED, "Cfg", "struct Cfg grew",
            frozen_namespace_violation="**::detail::r1::*",
        )
        pf = PolicyFile(overrides={ChangeKind.TYPE_SIZE_CHANGED: Verdict.COMPATIBLE})
        r = _result(Verdict.BREAKING, changes=[c])
        r.policy_file = pf
        d = json.loads(to_json(r, report_mode="leaf", severity_config=PRESET_DEFAULT))
        assert d["leaf_changes"][0]["severity"] == "breaking"
        assert d["severity"]["exit_code"] == 4

    def test_leaf_mode_non_type_change_honours_frozen_namespace_floor(self):
        """Codex review on #549 (follow-on to the root-type leaf-entry fix):
        the adjacent non_type_changes path in the same leaf-mode function
        called _change_to_dict without policy_file, so a non-root-type kind
        (func_removed) demoted by a policy override, but tagged
        frozen_namespace_violation, read "compatible" in non_type_changes
        (and the backward-compat changes union) while the top-level severity
        block correctly reported exit_code=4 for the same finding."""
        from abicheck.policy_file import PolicyFile
        from abicheck.severity import PRESET_DEFAULT

        c = Change(
            ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo",
            frozen_namespace_violation="**::detail::r1::*",
        )
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
        r = _result(Verdict.BREAKING, changes=[c])
        r.policy_file = pf
        d = json.loads(to_json(r, report_mode="leaf", severity_config=PRESET_DEFAULT))
        assert d["non_type_changes"][0]["severity"] == "breaking"
        assert d["changes"][0]["severity"] == "breaking"
        assert d["severity"]["exit_code"] == 4

    def test_leaf_mode_carries_severity_block(self):
        """report_mode="leaf" returned before the severity block was ever
        built, so a caller passing severity_config silently got no severity
        information at all — unlike full-mode JSON."""
        from abicheck.severity import PRESET_DEFAULT

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r, report_mode="leaf", severity_config=PRESET_DEFAULT))
        assert "severity" in d
        assert d["severity"]["categories"]["addition"]["count"] == 1

    def test_leaf_mode_without_severity_config_has_no_severity_block(self):
        r = _result(Verdict.COMPATIBLE)
        d = json.loads(to_json(r, report_mode="leaf"))
        assert "severity" not in d


class TestRootCauseReporter:
    """G29 Phase 3 slice 3 (ADR-051): --report-mode root-cause groups
    findings sharing a Change.caused_by_type under one entry -- a first,
    JSON-only slice reusing the existing caused_by_type field rather than
    requiring the full G29 Phase 6 RootCauseCorrelator."""

    def test_groups_findings_sharing_caused_by_type(self):
        root = Change(
            ChangeKind.FUNC_REMOVED, "ns::internal::helper", "helper removed",
        )
        overlay = Change(
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API, "pub_entry",
            "required", caused_by_type="ns::internal::helper",
        )
        r = _result(Verdict.BREAKING, changes=[root, overlay])
        d = json.loads(to_json(r, report_mode="root-cause"))
        assert d["root_cause_count"] == 1
        group = d["root_causes"][0]
        assert group["root"] == "ns::internal::helper"
        assert group["finding_count"] == 2
        assert {f["symbol"] for f in group["findings"]} == {
            "ns::internal::helper", "pub_entry",
        }

    def test_ungrouped_finding_is_its_own_singleton(self):
        c = Change(ChangeKind.FUNC_ADDED, "ns::pub_new", "added")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r, report_mode="root-cause"))
        assert d["root_cause_count"] == 1
        assert d["root_causes"][0]["root"] == "ns::pub_new"

    def test_independent_findings_sharing_a_symbol_stay_separate(self):
        """Codex review: two independent findings on the same symbol with no
        caused_by_type correlation (e.g. a return-type change and a parameter
        change, both on "foo") must NOT collapse into one root cause just
        because they share a symbol -- only caused_by_type correlates
        findings in this slice's contract."""
        a = Change(ChangeKind.FUNC_RETURN_CHANGED, "foo", "return type changed")
        b = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "parameter changed")
        r = _result(Verdict.BREAKING, changes=[a, b])
        d = json.loads(to_json(r, report_mode="root-cause"))
        assert d["root_cause_count"] == 2
        for group in d["root_causes"]:
            assert group["root"] == "foo"
            assert group["finding_count"] == 1
        ids = {group["root_cause_id"] for group in d["root_causes"]}
        assert len(ids) == 2

    def test_anonymous_findings_with_no_symbol_stay_separate(self):
        """Codex review: SOURCE_FACT_COVERAGE_INCOMPLETE/
        SOURCE_BINARY_PROVENANCE_MISMATCH (source_diff.py) are both
        constructed with symbol="" and no caused_by_type -- a bare-symbol
        grouping fallback would collapse every such aggregate finding into
        one fake shared root cause ("" == ""), even though none of them
        actually correlate. Each must stay its own singleton group."""
        a = Change(
            ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE,
            "",
            "L4 source-fact evidence incomplete",
        )
        b = Change(
            ChangeKind.SOURCE_BINARY_PROVENANCE_MISMATCH,
            "",
            "source tree does not match binary",
        )
        r = _result(Verdict.COMPATIBLE_WITH_RISK, changes=[a, b])
        d = json.loads(to_json(r, report_mode="root-cause"))
        assert d["root_cause_count"] == 2
        kinds = {group["findings"][0]["kind"] for group in d["root_causes"]}
        assert kinds == {
            "source_fact_coverage_incomplete",
            "source_binary_provenance_mismatch",
        }
        ids = {group["root_cause_id"] for group in d["root_causes"]}
        assert len(ids) == 2
        assert d["root_causes"][0]["finding_count"] == 1

    def test_root_cause_id_is_stable_for_the_same_root(self):
        c1 = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        c2 = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        d1 = json.loads(
            to_json(_result(Verdict.BREAKING, changes=[c1]), report_mode="root-cause")
        )
        d2 = json.loads(
            to_json(_result(Verdict.BREAKING, changes=[c2]), report_mode="root-cause")
        )
        assert (
            d1["root_causes"][0]["root_cause_id"]
            == d2["root_causes"][0]["root_cause_id"]
        )

    def test_changes_still_present_for_backward_compat(self):
        """Every other report mode always provides a flat `changes` array
        (leaf mode included, via its own backward-compat union) -- root-cause
        mode must too, or a consumer relying on that contract breaks."""
        c = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r, report_mode="root-cause"))
        assert len(d["changes"]) == 1
        assert d["changes"][0]["symbol"] == "ns::internal::helper"

    def test_carries_severity_block(self):
        from abicheck.severity import PRESET_DEFAULT

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(
            to_json(r, report_mode="root-cause", severity_config=PRESET_DEFAULT)
        )
        assert "severity" in d
        assert d["severity"]["categories"]["addition"]["count"] == 1

    def test_show_only_filters_root_cause_groups(self):
        breaking = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        addition = Change(ChangeKind.FUNC_ADDED, "ns::pub_new", "added")
        r = _result(Verdict.BREAKING, changes=[breaking, addition])
        d = json.loads(to_json(r, report_mode="root-cause", show_only="breaking"))
        roots = {group["root"] for group in d["root_causes"]}
        assert roots == {"ns::internal::helper"}

    def test_carries_scope_block_when_public_headers_scoped(self):
        """Codex review: full/leaf JSON both emit the machine-readable
        `scope` block (resolved/fell_back/manual_review_required) when
        --scope-public-headers was requested; root-cause mode dropped it,
        hiding the fallback/manual-review warning for scoped root-cause
        runs."""
        c = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        r = _result(Verdict.BREAKING, changes=[c])
        r.scope_to_public_surface = True
        r.scope_resolved = False
        d = json.loads(to_json(r, report_mode="root-cause"))
        assert d["scope"]["public_headers_applied"] is True
        assert d["scope"]["manual_review_required"] is True


class TestRootCauseMarkdown:
    """G29 Phase 3 slice 4 (ADR-051): --report-mode root-cause markdown/text
    rendering, sharing reporter._group_changes_by_root_cause with the JSON
    renderer so the two formats can never disagree about grouping."""

    def test_groups_findings_sharing_caused_by_type(self):
        root = Change(
            ChangeKind.FUNC_REMOVED, "ns::internal::helper", "helper removed",
        )
        overlay = Change(
            ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API, "pub_entry",
            "required", caused_by_type="ns::internal::helper",
        )
        r = _result(Verdict.BREAKING, changes=[root, overlay])
        md = to_markdown(r, report_mode="root-cause")
        assert "## Root Causes (1)" in md
        assert "### `ns::internal::helper` (2 findings)" in md
        assert "func_removed" in md
        assert "internal_symbol_required_by_public_api" in md

    def test_independent_findings_sharing_a_symbol_stay_separate(self):
        a = Change(ChangeKind.FUNC_RETURN_CHANGED, "foo", "return type changed")
        b = Change(ChangeKind.FUNC_PARAMS_CHANGED, "foo", "parameter changed")
        r = _result(Verdict.BREAKING, changes=[a, b])
        md = to_markdown(r, report_mode="root-cause")
        assert "## Root Causes (2)" in md
        assert md.count("### `foo` (1 finding)") == 2

    def test_show_only_filters_root_cause_groups(self):
        breaking = Change(ChangeKind.FUNC_REMOVED, "ns::internal::helper", "removed")
        addition = Change(ChangeKind.FUNC_ADDED, "ns::pub_new", "added")
        r = _result(Verdict.BREAKING, changes=[breaking, addition])
        md = to_markdown(r, report_mode="root-cause", show_only="breaking")
        assert "ns::internal::helper" in md
        assert "ns::pub_new" not in md
        assert "Filtered by: `--show-only breaking`" in md

    def test_no_changes_reports_no_abi_changes(self):
        md = to_markdown(_result(Verdict.NO_CHANGE), report_mode="root-cause")
        assert "No ABI changes detected" in md
        assert "Root Causes" not in md

    def test_carries_severity_summary(self):
        from abicheck.severity import PRESET_DEFAULT

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        md = to_markdown(r, report_mode="root-cause", severity_config=PRESET_DEFAULT)
        assert "## Severity Configuration" in md


class TestMarkdownReporter:
    def test_no_change_contains_no_change(self):
        md = to_markdown(_result(Verdict.NO_CHANGE))
        assert "NO_CHANGE" in md
        assert "No ABI changes" in md

    def test_breaking_contains_section(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo",
                   old_value="foo")
        md = to_markdown(_result(Verdict.BREAKING, [c]))
        assert "❌ Breaking Changes" in md
        assert "func_removed" in md

    def test_compatible_section(self):
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api",
                   new_value="new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]))
        assert "COMPATIBLE" in md
        assert "Additions" in md

    def test_noexcept_added_in_quality_section(self):
        c = Change(ChangeKind.FUNC_NOEXCEPT_ADDED, "_Z4swapv",
                   "noexcept specifier added: swap")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]))
        assert "COMPATIBLE" in md
        assert "Quality Issues" in md

    def test_demangle_rewrites_mangled_names_when_enabled(self, monkeypatch):
        import abicheck.demangle as dm
        monkeypatch.setattr(dm, "demangle_batch", lambda syms: {"_Z3foov": "foo()"})
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: _Z3foov")
        result = _result(Verdict.BREAKING, [c])
        md_on = to_markdown(result, demangle=True)
        assert "foo()" in md_on
        assert "_Z3foov" not in md_on
        # Default leaves mangled names untouched (machine-stable).
        md_off = to_markdown(result, demangle=False)
        assert "_Z3foov" in md_off

    def test_service_review_format_honors_demangle(self, monkeypatch):
        import abicheck.demangle as dm
        from abicheck.model import AbiSnapshot
        from abicheck.service import render_output
        monkeypatch.setattr(dm, "demangle_batch", lambda syms: {"_Z3foov": "foo()"})
        result = _result(Verdict.BREAKING,
                         [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed _Z3foov")])
        old = AbiSnapshot(library="libtest.so.1", version="1.0")
        out_on = render_output("review", result, old, demangle=True)
        assert "foo()" in out_on and "_Z3foov" not in out_on
        out_off = render_output("review", result, old, demangle=False)
        assert "_Z3foov" in out_off

    def test_legend_always_present(self):
        md = to_markdown(_result(Verdict.NO_CHANGE))
        assert "Legend" in md

    def test_risk_changes_in_json(self):
        """JSON summary must include risk_changes field with correct count."""
        c = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                   "New GLIBC_2.34 version requirement added")
        r = _result(Verdict.COMPATIBLE_WITH_RISK, changes=[c])
        d = json.loads(to_json(r))
        assert d["verdict"] == "COMPATIBLE_WITH_RISK"
        assert "risk_changes" in d["summary"], "JSON summary must contain 'risk_changes' key"
        assert d["summary"]["risk_changes"] == 1
        assert d["summary"]["breaking"] == 0

    def test_risk_section_in_markdown(self):
        """Markdown must include Deployment Risk Changes section when risk > 0."""
        c = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                   "New GLIBC_2.34 version requirement added")
        md = to_markdown(_result(Verdict.COMPATIBLE_WITH_RISK, [c]))
        assert "COMPATIBLE_WITH_RISK" in md
        assert "⚠️ Deployment Risk Changes" in md
        assert "binary-compatible" in md
        assert "symbol_version_required_added" in md

    def test_compatible_with_risk_emoji_in_markdown(self):
        """COMPATIBLE_WITH_RISK verdict uses ⚠️ emoji in header table."""
        c = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                   "New GLIBC_2.34 version requirement added")
        md = to_markdown(_result(Verdict.COMPATIBLE_WITH_RISK, [c]))
        assert "⚠️ `COMPATIBLE_WITH_RISK`" in md


# ---------------------------------------------------------------------------
# Severity-aware reporter output
# ---------------------------------------------------------------------------

class TestSeverityMarkdown:
    """Tests for to_markdown with severity_config parameter."""

    def test_severity_badges_shown_when_config_provided(self):
        """Section header for breaking changes includes ERROR badge."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo",
                   old_value="foo")
        md = to_markdown(_result(Verdict.BREAKING, [c]), severity_config=PRESET_DEFAULT)
        # Exact section header produced by the reporter
        assert "## \u274c Breaking Changes \u274c `ERROR`" in md

    def test_severity_badges_absent_without_config(self):
        """Section headers do NOT include severity badges without severity_config."""
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo",
                   old_value="foo")
        md = to_markdown(_result(Verdict.BREAKING, [c]))
        # Without severity_config, the header has no badge suffix
        assert "## \u274c Breaking Changes\n" in md
        assert "`ERROR`" not in md
        assert "`WARNING`" not in md
        assert "`INFO`" not in md

    def test_severity_summary_table_in_markdown(self):
        """Markdown includes a severity configuration table when config is provided."""
        from abicheck.severity import PRESET_STRICT
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]), severity_config=PRESET_STRICT)
        assert "## Severity Configuration" in md
        # Exact table rows
        assert "| ABI/API Incompatibilities |" in md
        assert "| Additions |" in md

    def test_severity_summary_absent_without_config(self):
        """Markdown does NOT include severity table without config."""
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]))
        assert "Severity Configuration" not in md

    def test_quality_section_with_severity_label(self):
        """Quality section header includes WARNING badge."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_NOEXCEPT_ADDED, "_Z4swapv",
                   "noexcept specifier added: swap")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]), severity_config=PRESET_DEFAULT)
        # Exact section header
        assert "## \U0001f50d Quality Issues \u26a0\ufe0f `WARNING`" in md

    def test_additions_section_with_severity_label(self):
        """Additions section header includes INFO badge."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]), severity_config=PRESET_DEFAULT)
        # Exact section header
        assert "## \u2705 Additions \u2139\ufe0f `INFO`" in md


class TestSeverityJson:
    """Tests for to_json with severity_config parameter."""

    def test_severity_section_in_json(self):
        """JSON output includes severity section when config is provided."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r, severity_config=PRESET_DEFAULT))
        assert "severity" in d
        sev = d["severity"]
        assert "config" in sev
        assert sev["config"]["abi_breaking"] == "error"
        assert sev["config"]["addition"] == "info"
        assert "categories" in sev
        assert sev["categories"]["abi_breaking"]["count"] == 1

    def test_severity_absent_in_json_without_config(self):
        """JSON output does NOT include severity section without config."""
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r))
        assert "severity" not in d

    def test_severity_exit_code_in_json(self):
        """JSON severity section includes computed exit_code."""
        from abicheck.severity import PRESET_STRICT
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r, severity_config=PRESET_STRICT))
        assert d["severity"]["exit_code"] == 1

    def test_severity_category_counts(self):
        """JSON severity categories have correct counts for mixed changes."""
        from abicheck.severity import PRESET_DEFAULT
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo"),
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added: bar"),
            Change(ChangeKind.VISIBILITY_LEAK, "std::string", "std symbol exposed"),
        ]
        r = _result(Verdict.BREAKING, changes=changes)
        d = json.loads(to_json(r, severity_config=PRESET_DEFAULT))
        cats = d["severity"]["categories"]
        assert cats["abi_breaking"]["count"] == 1
        assert cats["addition"]["count"] == 1
        assert cats["quality_issues"]["count"] == 1
        assert cats["potential_breaking"]["count"] == 0


# ---------------------------------------------------------------------------
# Confidence, evidence tiers, coverage warnings, and policy in reports
# ---------------------------------------------------------------------------

class TestConfidenceInJson:
    """JSON report must include confidence, evidence_tiers, and coverage_warnings."""

    def test_default_confidence_high(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r))
        assert d["confidence"] == "high"
        assert d["evidence_tiers"] == []
        assert "coverage_warnings" not in d  # omitted when empty

    def test_confidence_with_tiers(self):
        from abicheck.checker_policy import Confidence
        r = _result(Verdict.BREAKING, [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
        ])
        r.confidence = Confidence.MEDIUM
        r.evidence_tiers = ["elf", "header"]
        r.coverage_warnings = ["DWARF debug info not available"]
        d = json.loads(to_json(r))
        assert d["confidence"] == "medium"
        assert d["evidence_tiers"] == ["elf", "header"]
        assert d["coverage_warnings"] == ["DWARF debug info not available"]

    def test_policy_overrides_in_json(self):
        from abicheck.policy_file import PolicyFile
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        r = _result(Verdict.COMPATIBLE)
        r.policy_file = pf
        d = json.loads(to_json(r))
        assert d["policy_overrides"] == {"func_removed": "COMPATIBLE"}

    def test_policy_overrides_absent_without_file(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r))
        assert "policy_overrides" not in d


class TestConfidenceInMarkdown:
    """Markdown report must include Analysis Confidence section."""

    def test_confidence_section_present(self):
        from abicheck.checker_policy import Confidence
        r = _result(Verdict.COMPATIBLE, [
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added: bar"),
        ])
        r.confidence = Confidence.LOW
        r.evidence_tiers = ["elf"]
        r.coverage_warnings = ["DWARF stripped"]
        md = to_markdown(r)
        assert "## Analysis Confidence" in md
        assert "LOW" in md
        assert "`elf`" in md
        assert "DWARF stripped" in md

    def test_policy_shown_in_markdown(self):
        r = _result(Verdict.NO_CHANGE)
        md = to_markdown(r)
        assert "**Policy**: `strict_abi`" in md

    def test_policy_overrides_shown(self):
        from abicheck.policy_file import PolicyFile
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        r = _result(Verdict.NO_CHANGE)
        r.policy_file = pf
        md = to_markdown(r)
        assert "**Policy overrides**" in md
        assert "`func_removed`" in md


# ---------------------------------------------------------------------------
# AppCompat report traceability (file metadata + confidence)
# ---------------------------------------------------------------------------

class TestAppCompatTraceability:
    """AppCompat JSON/Markdown include file metadata and confidence when available."""

    def _appcompat_result(self):
        from types import SimpleNamespace

        from abicheck.checker_policy import Confidence
        diff = _result(Verdict.COMPATIBLE)
        diff.old_metadata = SimpleNamespace(path="/old/lib.so", sha256="aabb" * 8, size_bytes=4096)
        diff.new_metadata = SimpleNamespace(path="/new/lib.so", sha256="ccdd" * 8, size_bytes=8192)
        diff.confidence = Confidence.MEDIUM
        diff.evidence_tiers = ["elf", "header"]
        diff.coverage_warnings = []
        return SimpleNamespace(
            app_path="/bin/app",
            old_lib_path="/old/lib.so",
            new_lib_path="/new/lib.so",
            verdict=Verdict.COMPATIBLE,
            symbol_coverage=100.0,
            required_symbol_count=10,
            missing_symbols=[],
            missing_versions=[],
            breaking_for_app=[],
            irrelevant_for_app=[],
            full_diff=diff,
        )

    def test_appcompat_json_includes_file_metadata(self):
        from abicheck.reporter import appcompat_to_json
        r = self._appcompat_result()
        d = json.loads(appcompat_to_json(r))
        assert d["old_file"]["path"] == "/old/lib.so"
        assert d["new_file"]["path"] == "/new/lib.so"
        assert d["old_file"]["size_bytes"] == 4096
        assert d["confidence"] == "medium"
        assert d["evidence_tiers"] == ["elf", "header"]

    def test_appcompat_markdown_includes_file_metadata(self):
        from abicheck.reporter import appcompat_to_markdown
        r = self._appcompat_result()
        md = appcompat_to_markdown(r)
        assert "Library Files" in md
        assert "/old/lib.so" in md
        assert "**Confidence**" in md

    def test_appcompat_markdown_includes_policy(self):
        from abicheck.reporter import appcompat_to_markdown
        r = self._appcompat_result()
        md = appcompat_to_markdown(r)
        assert "**Policy**" in md
        assert "`strict_abi`" in md


class TestStatJsonConfidence:
    """Stat JSON must include confidence and evidence_tiers."""

    def test_stat_json_default_confidence(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r, stat=True))
        assert d["confidence"] == "high"
        assert d["evidence_tiers"] == []
        assert "coverage_warnings" not in d

    def test_stat_json_with_confidence(self):
        from abicheck.checker_policy import Confidence
        r = _result(Verdict.BREAKING, [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
        ])
        r.confidence = Confidence.LOW
        r.evidence_tiers = ["elf"]
        r.coverage_warnings = ["DWARF stripped"]
        d = json.loads(to_json(r, stat=True))
        assert d["confidence"] == "low"
        assert d["evidence_tiers"] == ["elf"]
        assert d["coverage_warnings"] == ["DWARF stripped"]
