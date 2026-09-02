# Copyright 2026 Nikolay Petrov
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

"""Validate that `compare` JSON output conforms to the published schema.

The schema (`abicheck/schemas/compare_report.schema.json`) is the stable
machine-readable contract documented in docs/use/output-formats.md.
These tests pin three things:

1. The schema file itself is well-formed JSON Schema and ships in the package.
2. Real `to_json` output validates against it (full / show-only / severity).
3. The emitted ``report_schema_version`` matches the package constant and is
   present in every projection (full / ``--stat`` / ``--report-mode leaf``).

`jsonschema` is an optional dependency; structural validation tests skip
cleanly when it is absent, while the non-jsonschema invariants always run.
"""

from __future__ import annotations

import json

import pytest

from abicheck import reporter
from abicheck.checker import compare
from abicheck.contract_pipeline import ContractEvaluationStage
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.schemas import (
    COMPARE_REPORT_SCHEMA_PATH,
    REPORT_SCHEMA_VERSION,
    load_compare_report_schema,
)
from abicheck.severity import SeverityConfig

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

_requires_jsonschema = pytest.mark.skipif(
    jsonschema is None, reason="jsonschema not installed"
)


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A pair that yields a mix of breaking, addition, and type changes."""
    old = AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_b", "_Z5api_bv")],
        types=[
            RecordType(
                name="Cfg",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="x", type="int", offset_bits=0)],
            )
        ],
        enums=[EnumType(name="Color", members=[EnumMember(name="RED", value=0)])],
    )
    new = AbiSnapshot(
        library="libfoo.so.1",
        version="2.0",
        functions=[_fn("api_a", "_Z5api_av"), _fn("api_c", "_Z5api_cv")],
        types=[
            RecordType(
                name="Cfg",
                kind="struct",
                size_bits=64,
                fields=[
                    TypeField(name="x", type="int", offset_bits=0),
                    TypeField(name="y", type="int", offset_bits=32),
                ],
            )
        ],
        enums=[
            EnumType(
                name="Color",
                members=[
                    EnumMember(name="RED", value=0),
                    EnumMember(name="BLUE", value=1),
                ],
            )
        ],
    )
    return old, new


class TestSchemaFile:
    def test_schema_file_ships_in_package(self):
        assert COMPARE_REPORT_SCHEMA_PATH.is_file()

    @_requires_jsonschema
    def test_schema_is_valid_jsonschema(self):
        schema = load_compare_report_schema()
        # Raises SchemaError if the schema itself is malformed.
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_schema_declares_version(self):
        schema = load_compare_report_schema()
        assert "report_schema_version" in schema["required"]

    def test_docs_mirror_matches_packaged_schema(self):
        # Regression guard (code review, PR #611): the docs/reference/schemas/v1
        # copy previously drifted from the packaged schema (PR #595) without any
        # test catching it, since jsonschema.validate's additionalProperties
        # tolerance doesn't flag a stale docs copy. scripts/publish_schemas.py
        # keeps the two byte-identical; assert that invariant directly.
        docs_copy = (
            COMPARE_REPORT_SCHEMA_PATH.parent.parent.parent
            / "docs"
            / "reference"
            / "schemas"
            / "v1"
            / "compare_report.schema.json"
        )
        assert docs_copy.is_file()
        assert docs_copy.read_text() == COMPARE_REPORT_SCHEMA_PATH.read_text()


@_requires_jsonschema
class TestReportValidatesAgainstSchema:
    def _validate(self, payload: dict) -> None:
        schema = load_compare_report_schema()
        jsonschema.validate(instance=payload, schema=schema)

    def test_no_change_report_validates(self):
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        self._validate(payload)

    def test_no_change_report_carries_analysis_assurance(self):
        """P0.4 (schema 2.37): analysis_assurance is unconditionally present
        on every real compare-report JSON (a verdict from the five Verdict
        enum values), not just for a run that requested deeper evidence."""
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        assert "analysis_assurance" in payload
        self._validate(payload)

    def test_report_missing_analysis_assurance_is_rejected(self):
        """P2 review (finding 3): analysis_assurance was documented and
        emitted as unconditional for schema 2.37, but was never added to the
        top-level `required` array, so a report missing it silently
        validated. A real, well-formed report with the key stripped must now
        fail schema validation."""
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        assert "analysis_assurance" in payload
        del payload["analysis_assurance"]
        with pytest.raises(jsonschema.ValidationError):
            self._validate(payload)

    def test_nonzero_crosscheck_promotion_contribution_is_rejected(self):
        """CodeRabbit review: crosscheck_promotion_contribution has no
        meaning outside scan --against's own maintainer-promoted
        --crosscheck KEY=error finding, and this schema validates only
        native compare reports (scan's own report shape is never validated
        against this file). A malformed compare report claiming a nonzero
        contribution must fail schema validation, not silently pass under
        a mere non-negativity floor."""
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        assert payload["exit"]["crosscheck_promotion_contribution"] == 0
        self._validate(payload)
        payload["exit"]["crosscheck_promotion_contribution"] = 1
        with pytest.raises(jsonschema.ValidationError):
            self._validate(payload)

    def test_breaking_report_validates(self):
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new)))
        self._validate(payload)
        assert payload["verdict"] in {
            "NO_CHANGE",
            "COMPATIBLE",
            "COMPATIBLE_WITH_RISK",
            "API_BREAK",
            "BREAKING",
        }

    def test_show_only_report_validates(self):
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), show_only="breaking"))
        self._validate(payload)

    def test_severity_report_validates(self):
        old, new = _breaking_pair()
        payload = json.loads(
            reporter.to_json(compare(old, new), severity_config=SeverityConfig())
        )
        self._validate(payload)

    def test_addition_reviewer_action_validates_against_packaged_schema(self):
        # Regression guard (Codex review, PR #595): reviewer_action was added
        # to reporter.py and the *docs* schema copy, but the packaged schema
        # abicheck/schemas/compare_report.schema.json -- what
        # load_compare_report_schema() actually reads at runtime -- was left
        # stale. jsonschema.validate would still pass a stale schema (its
        # "change" object has additionalProperties: true), so this doesn't
        # just check "no error" -- it asserts the field the real payload
        # carries is the one actually declared in the packaged schema's enum.
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new)))
        self._validate(payload)
        additions = [
            c
            for c in payload["changes"]
            if c.get("recommended_action") == "no_action_required"
        ]
        assert additions, "fixture must produce at least one addition finding"
        schema = load_compare_report_schema()
        declared_enum = schema["$defs"]["change"]["properties"]["reviewer_action"][
            "enum"
        ]
        for c in additions:
            assert c["reviewer_action"] in declared_enum

    def test_layout_unverifiable_correlation_validates_against_packaged_schema(self):
        # Schema 2.28: correlated_change_kind gained a second producer
        # (LAYOUT_UNVERIFIABLE, via
        # post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged)
        # beyond ADR-041's original public_api_internal_dependency_added --
        # validate a real payload carrying that second producer's value
        # against the packaged schema, the same "don't just check no error"
        # discipline the reviewer_action regression guard above uses.
        old = AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            types=[RecordType(name="Foo", kind="class", vtable=[], size_bits=None)],
        )
        new = AbiSnapshot(
            library="libfoo.so.1",
            version="2.0",
            types=[
                RecordType(
                    name="Foo",
                    kind="class",
                    vtable=["_ZN3Foo1fEv"],
                    size_bits=None,
                    base_offsets={"Base": 0},
                )
            ],
        )
        payload = json.loads(reporter.to_json(compare(old, new)))
        self._validate(payload)
        layout_findings = [
            c for c in payload["changes"] if c["kind"] == "layout_unverifiable"
        ]
        assert layout_findings, "fixture must produce a layout_unverifiable finding"
        assert layout_findings[0]["correlated_change_kind"] == "type_vtable_changed"

    def test_contract_evaluation_report_validates(self):
        # ADR-049 Phase 3 (schema 2.23): compare(..., contract_evaluation=True)
        # stamps three new optional per-finding keys -- validate a real
        # payload against the packaged schema (not just the docs mirror,
        # same "don't just check no error" reasoning as the reviewer_action
        # regression guard above) and pin the enum casing
        # ContractRelevance/ContractAssurance actually serialize with.
        old, new = _breaking_pair()
        payload = json.loads(
            reporter.to_json(compare(old, new, contract_evaluation=True))
        )
        self._validate(payload)
        stamped = [c for c in payload["changes"] if "contract_relevance" in c]
        assert stamped, "fixture must produce at least one shadow-evaluated finding"
        for c in stamped:
            assert c["contract_relevance"] in {
                "IN_CONTRACT",
                "UNKNOWN_UNRESOLVED",
                "UNKNOWN_UNPROVEN",
                "PROVEN_OUT_OF_CONTRACT",
                "NOT_APPLICABLE",
            }
            assert isinstance(c["contract_reason_code"], str)
            assert c["contract_assurance"] in {"complete", "partial", "unavailable"}

    def test_contract_evaluation_omitted_by_default(self):
        # The flag defaults to False -- an ordinary report carries none of
        # the three keys, matching every existing caller's unchanged output.
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new)))
        self._validate(payload)
        for c in payload["changes"]:
            assert "contract_relevance" not in c
            assert "contract_reason_code" not in c
            assert "contract_assurance" not in c

    def test_contract_evaluation_renders_in_markdown_report(self):
        # Regression (Codex review, PR #658, fresh evidence): --contract-
        # evaluation's own help text promises every finding is stamped, but
        # only the JSON report (reporter._add_contract_evaluation_fields)
        # ever rendered the decision -- an ordinary `compare
        # --contract` run (default markdown format) was
        # byte-for-byte identical to one without the flag.
        old, new = _breaking_pair()
        md_with = reporter.to_markdown(compare(old, new, contract_evaluation=True))
        md_without = reporter.to_markdown(compare(old, new))
        assert md_with != md_without
        assert "Contract:" in md_with
        assert "Contract:" not in md_without

    def test_contract_evaluation_renders_in_leaf_type_findings(self):
        # Regression (Codex review, PR #658, fresh evidence): --report-mode
        # leaf routes root TYPE_* changes through
        # reporter_markdown._format_leaf_type_change, a separate code path
        # from _format_change_md -- the already-stamped contract decision
        # was silently dropped for this one finding shape/report-mode
        # combination, unlike full and root-cause mode (which both use
        # _format_change_md for every finding, type or not).
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[RecordType(name="Result", kind="struct", size_bits=64)],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[RecordType(name="Result", kind="struct", size_bits=128)],
        )
        result = compare(old, new, contract_evaluation=True)
        type_changes = [c for c in result.changes if c.symbol == "Result"]
        assert type_changes, "fixture must produce a Result type-size finding"
        assert any(c.contract_relevance is not None for c in type_changes)

        md = reporter.to_markdown(result, report_mode="leaf")
        assert "Result" in md
        assert "Contract:" in md

    def test_contract_evaluation_stamps_suppressed_changes(self):
        # Regression (Codex review, PR #658, fresh evidence): a suppressed
        # finding stays visible in the ADR-013 audit trail
        # (suppression.suppressed_changes), but _apply_contract_evaluation_shadow
        # only ever evaluated kept + out_of_surface + redundant -- suppression
        # silently erased the contract decision even though the finding
        # itself is still rendered.
        from abicheck.suppression import Suppression, SuppressionList

        old, new = _breaking_pair()
        suppression = SuppressionList([Suppression(symbol="_Z5api_bv")])
        result = compare(old, new, suppression=suppression, contract_evaluation=True)
        assert result.suppressed_changes, "fixture must produce a suppressed finding"
        for c in result.suppressed_changes:
            assert c.contract_relevance is not None

        payload = json.loads(reporter.to_json(result))
        self._validate(payload)
        suppressed_entries = payload["suppression"]["suppressed_changes"]
        assert suppressed_entries
        for e in suppressed_entries:
            assert "contract_relevance" in e
            assert "contract_reason_code" in e

    def test_contract_evaluation_renders_suppressed_changes_in_markdown(self):
        # Regression (Codex review, PR #658, fresh evidence): the JSON audit
        # trail fix above stamps suppression.suppressed_changes, but
        # reporter_markdown._append_suppression_note's own bullet line
        # (rendered in every markdown report shape) never read the stamped
        # fields -- the suppression-note line for a stamped finding carried
        # no [contract: ...] tag even though the JSON sibling did.
        from abicheck.suppression import Suppression, SuppressionList

        old, new = _breaking_pair()
        suppression = SuppressionList([Suppression(symbol="_Z5api_bv")])
        result = compare(old, new, suppression=suppression, contract_evaluation=True)
        assert result.suppressed_changes, "fixture must produce a suppressed finding"

        md = reporter.to_markdown(result)
        assert "suppressed via suppression file" in md
        assert "[contract:" in md

    def test_contract_evaluation_all_mode_report_validates(self):
        # scope_to_public_surface=False means FilterNonPublicSurface never
        # runs its header-scoping path, so PipelineContext.surf_old/surf_new
        # stay None -- _apply_contract_evaluation_shadow must fall back to
        # computing them independently rather than leave shadow evaluation
        # unresolvable for this run shape. mode is contract=all (the
        # --no-scope-public-headers alias), so every finding normalizes to
        # IN_CONTRACT/all_mode_normalized_entity.
        old, new = _breaking_pair()
        payload = json.loads(
            reporter.to_json(
                compare(
                    old, new, contract_evaluation=True, scope_to_public_surface=False
                )
            )
        )
        self._validate(payload)
        stamped = [c for c in payload["changes"] if "contract_relevance" in c]
        assert stamped, "fixture must produce at least one shadow-evaluated finding"
        for c in stamped:
            assert c["contract_relevance"] == "IN_CONTRACT"
            assert c["contract_reason_code"] == "all_mode_normalized_entity"
            assert c["contract_assurance"] == "complete"

    def test_contract_evaluation_stamps_demoted_out_of_surface_findings(self):
        # Regression (Codex review, fresh evidence): _apply_contract_evaluation_shadow
        # previously evaluated only `kept`, so a finding public-surface
        # scoping already demoted to out_of_surface never got a shadow
        # decision at all -- exactly the false-positive-reduction case
        # Phase 3 exists to measure. An internal (unreferenced) type's
        # layout change is demoted to out_of_surface by scope_to_public_surface,
        # and must now resolve to PROVEN_OUT_OF_CONTRACT via its own
        # surface_exclusion_reason ("non-public-type").
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=64),
            ],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=128),
            ],
        )
        result = compare(
            old, new, scope_to_public_surface=True, contract_evaluation=True
        )
        assert result.out_of_surface_count >= 1
        demoted = [
            c for c in result.out_of_surface_changes if c.symbol == "InternalCache"
        ]
        assert demoted, "fixture must produce a demoted InternalCache finding"
        for c in demoted:
            assert c.contract_relevance is not None
            assert c.contract_relevance.value == "PROVEN_OUT_OF_CONTRACT"
            assert c.contract_reason_code == "terminal_authoritative_exclusion"

        payload = json.loads(reporter.to_json(result))
        entries = payload["surface_scope"]["out_of_surface_changes"]
        stamped_entries = [e for e in entries if e["symbol"] == "InternalCache"]
        assert stamped_entries
        for e in stamped_entries:
            assert e["contract_relevance"] == "PROVEN_OUT_OF_CONTRACT"
            assert e["contract_reason_code"] == "terminal_authoritative_exclusion"
            assert e["contract_assurance"] == "complete"

        # Regression (Codex review, fresh evidence): the identical
        # out_of_surface_changes Change objects are also serialized a
        # second, independent time as scope.filtered_internal_changes
        # (reporter._scope_dict) -- that ledger's own entries never read
        # the already-stamped fields, so a consumer of it (rather than
        # surface_scope.out_of_surface_changes) missed the decision.
        scope_entries = payload["scope"]["filtered_internal_changes"]
        scope_stamped = [e for e in scope_entries if e["symbol"] == "InternalCache"]
        assert scope_stamped
        for e in scope_stamped:
            assert e["contract_relevance"] == "PROVEN_OUT_OF_CONTRACT"
            assert e["contract_reason_code"] == "terminal_authoritative_exclusion"
            assert e["contract_assurance"] == "complete"

    @staticmethod
    def _stage_for(old: AbiSnapshot, new: AbiSnapshot) -> ContractEvaluationStage:
        """The contract-evaluation stage `checker.compare` builds internally."""
        from abicheck.contract_pipeline import build_contract_stage
        from abicheck.post_processing import PipelineContext

        return build_contract_stage(
            old,
            new,
            scope_to_public_surface=True,
            force_public_symbols=None,
            pp_ctx=PipelineContext(old=old, new=new),
        )

    def test_contract_evaluation_stamps_redundant_bucket(self):
        # Regression (Codex review, PR #658, fresh evidence): a redundant
        # (display-dedup) finding is only restored into the rendered report
        # when the caller passes --show-redundant/scope.show_redundant --
        # entirely in the CLI layer, via cli_helpers_compare.
        # _merge_redundant_changes, long after the contract-evaluation stage
        # already ran over `kept` + out_of_surface only. Without stamping the
        # redundant bucket too, a restored finding rendered with none of the
        # promised contract fields regardless of contract_evaluation=True.
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        old = AbiSnapshot(library="lib", version="1")
        new = AbiSnapshot(library="lib", version="2")
        kept = [
            Change(
                ChangeKind.TYPE_SIZE_CHANGED,
                "Cfg",
                "size changed from 64 to 72 bytes",
            )
        ]
        redundant = [
            Change(
                ChangeKind.FUNC_PARAMS_CHANGED,
                "config_init",
                "parameter type changed in config_init(Cfg*)",
                old_value="Cfg (64 bytes)",
                new_value="Cfg (72 bytes)",
            )
        ]
        assert redundant[0].contract_relevance is None
        self._stage_for(old, new).classify(kept + redundant)
        assert redundant[0].contract_relevance is not None
        assert redundant[0].contract_reason_code is not None
        # ADR-049 D1: the status travels with the relevance, so a restored
        # redundant finding also states whether policy scored it.
        assert redundant[0].compatibility_evaluation_status is not None

    def test_contract_evaluation_stamps_reconciled_bucket(self):
        # Regression (Codex review, PR #658, fresh evidence): a finding
        # --reconcile-build-context clears from `kept` (a context-free
        # header-parse phantom the build's active defines prove never
        # happened) stays visible in its own audit ledger
        # (reporter._add_reconciled, build_context_reconciled.changes) --
        # but reconciliation runs *before* contract classification
        # (checker.compare's own pipeline order), so the cleared finding
        # never reached `kept` and its ledger entry silently lost the
        # contract decision it would otherwise carry.
        from abicheck.checker_policy import ChangeKind
        from abicheck.checker_types import Change

        old = AbiSnapshot(library="lib", version="1")
        new = AbiSnapshot(library="lib", version="2")
        kept = [
            Change(ChangeKind.FUNC_REMOVED, "api", "removed"),
        ]
        reconciled = [
            Change(
                ChangeKind.TYPE_FIELD_ADDED,
                "Cfg::flag",
                "conditional field phantom add cleared by build context",
            )
        ]
        assert reconciled[0].contract_relevance is None
        self._stage_for(old, new).classify(kept + reconciled)
        assert reconciled[0].contract_relevance is not None
        assert reconciled[0].contract_reason_code is not None
        assert reconciled[0].compatibility_evaluation_status is not None

    def test_contract_evaluation_stamps_reconciled_bucket_end_to_end(self):
        # Same fix, exercised through a real --reconcile-build-context
        # false-positive clear (test_diff_reconcile.py's own canonical
        # fixture) rather than a hand-built Change, and through the real
        # JSON renderer (reporter._add_reconciled) end to end.
        from tests.test_diff_reconcile import _fp_pair

        old, new = _fp_pair()
        result = compare(
            old,
            new,
            scope_to_public_surface=True,
            reconcile_build_context=True,
            contract_evaluation=True,
        )
        assert result.reconciled_count == 1
        assert result.reconciled_changes[0].contract_relevance is not None

        payload = json.loads(reporter.to_json(result))
        self._validate(payload)
        reconciled_entries = payload["build_context_reconciled"]["changes"]
        assert reconciled_entries
        for e in reconciled_entries:
            assert "contract_relevance" in e
            assert "contract_reason_code" in e

    def test_out_of_surface_findings_omit_contract_fields_by_default(self):
        # Without contract_evaluation=True, out_of_surface_changes entries
        # must stay exactly as before -- no new keys at all.
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=64),
            ],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[_fn("public_api", "_Z10public_apiv", ret="Result *")],
            types=[
                RecordType(name="Result", kind="struct", size_bits=64),
                RecordType(name="InternalCache", kind="struct", size_bits=128),
            ],
        )
        result = compare(old, new, scope_to_public_surface=True)
        payload = json.loads(reporter.to_json(result))
        entries = payload["surface_scope"]["out_of_surface_changes"]
        assert entries
        for e in entries:
            assert "contract_relevance" not in e
            assert "contract_reason_code" not in e
            assert "contract_assurance" not in e

    def test_contract_evaluation_honors_post_manifest_commitment(self):
        # Regression (Codex review, fresh evidence): _apply_contract_evaluation_shadow
        # forwarded only header surfaces and force_public_symbols, not
        # public_surface_allowlist (the --post-manifest committed-export
        # set) -- so a symbol POST-manifest committed despite private-header
        # provenance was stamped PROVEN_OUT_OF_CONTRACT by a fresh
        # classify_change_surface recomputation that had no knowledge of the
        # commitment, inverting the pipeline's own kept (not demoted)
        # decision for that exact finding.
        old = AbiSnapshot(
            library="lib",
            version="1",
            functions=[
                Function(
                    name="pp_foo",
                    mangled="pp_foo",
                    return_type="int",
                    visibility=Visibility.ELF_ONLY,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                )
            ],
        )
        new = AbiSnapshot(
            library="lib",
            version="2",
            functions=[
                Function(
                    name="pp_foo",
                    mangled="pp_foo",
                    return_type="double",
                    visibility=Visibility.ELF_ONLY,
                    origin=ScopeOrigin.PRIVATE_HEADER,
                )
            ],
        )
        result = compare(
            old,
            new,
            public_surface_allowlist={"pp_foo"},
            contract_evaluation=True,
        )
        committed = [c for c in result.changes if c.symbol == "pp_foo"]
        assert committed, "fixture must produce a kept, committed pp_foo finding"
        assert not any(c.symbol == "pp_foo" for c in result.out_of_surface_changes)
        for c in committed:
            assert c.contract_relevance is not None
            assert c.contract_relevance.value == "IN_CONTRACT"
            assert c.contract_reason_code == "public_root_membership"

        payload = json.loads(reporter.to_json(result))
        entries = [c for c in payload["changes"] if c["symbol"] == "pp_foo"]
        assert entries
        for e in entries:
            assert e["contract_relevance"] == "IN_CONTRACT"
            assert e["contract_reason_code"] == "public_root_membership"


@_requires_jsonschema
class TestNotComparableReportSchema:
    """Schema 2.17 (ADR-050 D2): the `verdict: null` / `reason` shape a
    not_comparable compare report uses, distinct from the ordinary
    five-verdict shape TestReportValidatesAgainstSchema exercises above."""

    def _validate(self, payload: dict) -> None:
        schema = load_compare_report_schema()
        jsonschema.validate(instance=payload, schema=schema)

    def test_not_comparable_report_with_reason_validates(self):
        payload = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "library": "libfoo.so.1",
            "old_version": "1.0",
            "new_version": "2.0",
            "verdict": None,
            "reason": {"kind": "scope_mismatch", "message": "scope drift"},
        }
        self._validate(payload)

    def test_not_comparable_report_without_reason_is_rejected(self):
        # CodeRabbit review, PR #631: `reason` was documented as always
        # present when verdict is null, but only declaring the property
        # (not requiring it) let a null-verdict, reason-less payload validate
        # anyway. The allOf conditional added in response must actually
        # enforce it.
        payload = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "library": "libfoo.so.1",
            "old_version": "1.0",
            "new_version": "2.0",
            "verdict": None,
        }
        with pytest.raises(jsonschema.ValidationError):
            self._validate(payload)


@_requires_jsonschema
class TestCheckTargetSyntheticReportsValidateAgainstSchema:
    """`actions/check-target`'s synthesized-from-scratch report shapes
    (`check_report.build_operational_error_report`/`build_bootstrap_report`/
    `build_new_target_report`) -- distinct from `TestReportValidatesAgainstSchema`
    above, which only exercises reports built from a real `checker.compare()`
    result. Regression coverage for the finding that the packaged schema's
    `verdict`/`check_evidence_coverage.state` enums lagged the wire vocabulary
    these builders actually emit (Codex review)."""

    def _validate(self, payload: dict) -> None:
        schema = load_compare_report_schema()
        jsonschema.validate(instance=payload, schema=schema)

    def test_operational_error_report_validates(self):
        from abicheck.buildsource.check_report import build_operational_error_report

        report = build_operational_error_report(
            name="libpvxs",
            profile_id="linux-x86_64-gcc13-release",
            baseline_channel="accepted-main",
            requested_depth="headers",
            resolve_outcome="ambiguous",
            resolve_message="target 'libpvxs' is not in this baseline-set's manifest.",
        )
        self._validate(report)

    def test_bootstrap_report_validates(self):
        from abicheck.buildsource.check_report import build_bootstrap_report

        report = build_bootstrap_report(
            name="libpvxs",
            profile_id="linux-x86_64-gcc13-release",
            baseline_channel="release-contract",
            requested_depth="headers",
            resolve_message="no baseline set exists yet.",
        )
        self._validate(report)

    def test_new_target_report_validates(self):
        from abicheck.buildsource.check_report import build_new_target_report

        report = build_new_target_report(
            name="libnew",
            profile_id="linux-x86_64-gcc13-release",
            baseline_channel="release-contract",
            requested_depth="source",
            resolve_message="target 'libnew' is not in this baseline-set's manifest.",
        )
        self._validate(report)


class TestEvidenceDepthValidator:
    """Direct unit coverage for checker_types.validate_evidence_depth/
    EVIDENCE_DEPTH_VALUES -- the shared depth-spelling guard both
    reporter._add_check_identity (compare) and ScanOutcome.to_dict (scan)
    delegate to."""

    def test_accepts_every_public_depth(self):
        from abicheck.checker_types import (
            EVIDENCE_DEPTH_VALUES,
            validate_evidence_depth,
        )

        for depth in EVIDENCE_DEPTH_VALUES:
            validate_evidence_depth("requested_depth", depth)  # must not raise

    def test_rejects_unknown_depth_with_field_name_in_message(self):
        from abicheck.checker_types import validate_evidence_depth

        with pytest.raises(ValueError, match="effective_depth"):
            validate_evidence_depth("effective_depth", "bogus")


@_requires_jsonschema
class TestReportIdentityEnvelope:
    """ADR-047 §7 report-identity envelope subset (G30 P0.3): check_id,
    profile_id, requested_depth, effective_depth, baseline_channel are
    additive, optional fields nothing populates yet -- this only pins the
    schema/round-trip contract so G30 P1's primitives have somewhere to
    write."""

    def test_unset_by_default(self):
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        for key in (
            "check_id",
            "profile_id",
            "requested_depth",
            "effective_depth",
            "baseline_channel",
        ):
            assert key not in payload

    def test_set_fields_round_trip_and_validate(self):
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo@linux-x86_64-gcc13#accepted-main@source"
        result.profile_id = "linux-x86_64-gcc13"
        result.requested_depth = "source"
        result.effective_depth = "headers"
        result.baseline_channel = "accepted-main"
        payload = json.loads(reporter.to_json(result))
        jsonschema.validate(instance=payload, schema=load_compare_report_schema())
        assert payload["check_id"] == "libfoo@linux-x86_64-gcc13#accepted-main@source"
        assert payload["profile_id"] == "linux-x86_64-gcc13"
        assert payload["requested_depth"] == "source"
        assert payload["effective_depth"] == "headers"
        assert payload["baseline_channel"] == "accepted-main"

    def test_malformed_check_id_fails_schema_validation(self):
        # ADR-047 §7's delimiter-joined check_id form
        # ("target@profile#baseline_channel@requested_depth") is only
        # unambiguous if each component avoids '@'/'#' itself -- a value
        # missing the depth suffix, or with the wrong delimiter order,
        # must fail the schema's pattern. Code review on PR #611 also added
        # eager validation at the point check_id is set (matching
        # requested_depth/effective_depth's validate_evidence_depth), so
        # to_json now raises ValueError immediately rather than only
        # failing the opt-in JSON Schema check.
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo-missing-the-rest-of-the-shape"
        with pytest.raises(ValueError, match="check_id"):
            reporter.to_json(result)

    def test_check_id_with_a_trailing_newline_is_rejected(self):
        """Codex review: CHECK_ID_PATTERN was anchored with a trailing
        '$', which (without re.MULTILINE) also matches just before a
        trailing '\\n' -- a check_id carrying one would otherwise pass
        validate_check_id() and corrupt GITHUB_OUTPUT downstream."""
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo@profile#channel@source\n"
        with pytest.raises(ValueError, match="check_id"):
            reporter.to_json(result)

    def test_explicit_id_qualified_check_id_round_trips_and_validates(self):
        """G42 'Explicit check identifiers': the full construct
        (build_check_id) -> validate-against-schema -> parse
        (contracts.parse_check_id) chain for a ~<explicit_id>-qualified
        check_id -- not just the parser in isolation, since either half of
        the three-layer extension (checker_types/check_report vs. the
        schema vs. contracts.py) being missed fails at a different point."""
        from abicheck.buildsource.check_report import build_check_id
        from abicheck.workflows.aggregate.contracts import parse_check_id

        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = build_check_id(
            "libfoo",
            "linux-x86_64-gcc13",
            "accepted-main",
            "source",
            explicit_id="l4-plugin-rhel8",
        )
        payload = json.loads(reporter.to_json(result))
        jsonschema.validate(instance=payload, schema=load_compare_report_schema())
        assert (
            payload["check_id"]
            == "libfoo@linux-x86_64-gcc13#accepted-main@source~l4-plugin-rhel8"
        )
        parsed = parse_check_id(payload["check_id"])
        assert parsed is not None
        assert parsed.target == "libfoo"
        assert parsed.profile == "linux-x86_64-gcc13"
        assert parsed.baseline_channel == "accepted-main"
        assert parsed.requested_depth == "source"
        assert parsed.explicit_id == "l4-plugin-rhel8"
        assert parsed.environment_id is None

    def test_stat_mode_carries_identity_fields_too(self):
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo@profile#channel@binary"
        payload = json.loads(reporter.to_json(result, stat=True))
        assert payload["check_id"] == "libfoo@profile#channel@binary"

    def test_leaf_mode_carries_identity_fields_too(self):
        # Regression guard: --report-mode leaf builds its own top-level dict
        # independently of _build_json_base/to_stat_json (a separate code
        # path, _to_json_leaf), so it needs its own _add_check_identity call
        # -- code review on PR #611 caught this gap between the PR's own
        # description ("full/--stat/leaf JSON via a shared helper") and what
        # _to_json_leaf actually did (nothing, until this fix).
        old, new = _breaking_pair()
        result = compare(old, new)
        result.check_id = "libfoo@profile#channel@source"
        result.profile_id = "linux-x86_64-gcc13"
        result.requested_depth = "source"
        result.effective_depth = "build"
        result.baseline_channel = "accepted-main"
        payload = json.loads(reporter.to_json(result, report_mode="leaf"))
        assert payload["check_id"] == "libfoo@profile#channel@source"
        assert payload["profile_id"] == "linux-x86_64-gcc13"
        assert payload["requested_depth"] == "source"
        assert payload["effective_depth"] == "build"
        assert payload["baseline_channel"] == "accepted-main"

    def test_leaf_mode_unset_by_default(self):
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), report_mode="leaf"))
        for key in (
            "check_id",
            "profile_id",
            "requested_depth",
            "effective_depth",
            "baseline_channel",
        ):
            assert key not in payload

    def test_invalid_requested_depth_rejected_before_json_is_built(self):
        # reporter.to_json validates requested_depth/effective_depth itself
        # (matching mcp_server._validate_public_depth's same check) rather
        # than relying solely on JSON Schema validation, which production
        # code never runs -- only opt-in tests do. Catches a bad value at
        # the point it's set, not only when a test happens to schema-check
        # the output.
        old, new = _breaking_pair()
        result = compare(old, new)
        result.requested_depth = "not-a-real-depth"
        with pytest.raises(ValueError, match="requested_depth"):
            reporter.to_json(result)

    def test_invalid_effective_depth_rejected_before_json_is_built(self):
        old, new = _breaking_pair()
        result = compare(old, new)
        result.effective_depth = "not-a-real-depth"
        with pytest.raises(ValueError, match="effective_depth"):
            reporter.to_json(result)


class TestScanReportIdentityEnvelope:
    """Same ADR-047 §7 fields (G30 P0.3), mirrored on the scan side --
    ScanOutcome.to_dict() rather than a compare_report.schema.json (scan's
    JSON output has no packaged JSON Schema to validate against)."""

    def _outcome(self, **identity: str) -> object:
        from abicheck.buildsource.risk import RiskScore
        from abicheck.scan_engine import ScanOutcome

        return ScanOutcome(
            mode="scan",
            resolved_method="auto",
            depth="headers",
            collect_mode="off",
            risk=RiskScore(total=0),
            auto=True,
            changed_path_count=0,
            changed_path_source="none",
            **identity,
        )

    def test_unset_by_default(self):
        payload = self._outcome().to_dict()
        for key in (
            "check_id",
            "profile_id",
            "requested_depth",
            "effective_depth",
            "baseline_channel",
        ):
            assert key not in payload

    def test_set_fields_round_trip(self):
        payload = self._outcome(
            check_id="libfoo@profile#channel@source",
            profile_id="linux-x86_64-gcc13",
            requested_depth="source",
            effective_depth="build",
            baseline_channel="accepted-main",
        ).to_dict()
        assert payload["check_id"] == "libfoo@profile#channel@source"
        assert payload["profile_id"] == "linux-x86_64-gcc13"
        assert payload["requested_depth"] == "source"
        assert payload["effective_depth"] == "build"
        assert payload["baseline_channel"] == "accepted-main"

    def test_scan_schema_version_bumped_for_the_new_fields(self):
        from abicheck.schemas import SCAN_SCHEMA_VERSION

        payload = self._outcome().to_dict()
        assert payload["scan_schema_version"] == SCAN_SCHEMA_VERSION
        assert SCAN_SCHEMA_VERSION != "1.0"

    def test_invalid_requested_depth_rejected_before_dict_is_built(self):
        with pytest.raises(ValueError, match="requested_depth"):
            self._outcome(requested_depth="not-a-real-depth").to_dict()

    def test_invalid_effective_depth_rejected_before_dict_is_built(self):
        with pytest.raises(ValueError, match="effective_depth"):
            self._outcome(effective_depth="not-a-real-depth").to_dict()


class TestSchemaVersion:
    def test_emitted_version_matches_constant(self):
        f = _fn("api", "_Z3apiv")
        snap = AbiSnapshot(library="libfoo.so.1", version="1.0", functions=[f])
        payload = json.loads(reporter.to_json(compare(snap, snap)))
        assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION

    def test_version_is_major_minor(self):
        parts = REPORT_SCHEMA_VERSION.split(".")
        assert len(parts) == 2
        assert all(p.isdigit() for p in parts)

    def test_stat_mode_carries_version(self):
        """--stat JSON is a different shape but must still carry the version marker."""
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), stat=True))
        assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION

    def test_leaf_mode_carries_version(self):
        """--report-mode leaf JSON must still carry the version marker."""
        old, new = _breaking_pair()
        payload = json.loads(reporter.to_json(compare(old, new), report_mode="leaf"))
        assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION
