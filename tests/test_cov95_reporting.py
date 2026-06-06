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

"""Coverage-focused unit tests for the reporting / post-processing surface.

Targets uncovered branches in:

* ``abicheck.reporter``
* ``abicheck.post_processing``
* ``abicheck.internal_leak``
* ``abicheck.stack_report``

All tests are pure-Python (no compiler / external tools), deterministic, and
drive the rendering / serialization helpers directly with synthetic model
objects.
"""

from __future__ import annotations

import json
from pathlib import Path

from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.checker import Verdict
from abicheck.checker_policy import ChangeKind, Confidence, EvidenceTier
from abicheck.checker_types import Change, DiffResult, LibraryMetadata
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_checker import StackChange, StackCheckResult, StackVerdict

# ===========================================================================
# Shared builders
# ===========================================================================


def _snap(
    *,
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions or []),
        variables=list(variables or []),
        types=list(types or []),
    )


def _public_fn(
    name: str, ret: str = "void", params: list[tuple[str, str]] | None = None
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=n, type=t) for n, t in (params or [])],
        visibility=Visibility.PUBLIC,
    )


def _record(name: str, kind: str = "class", **kw: object) -> RecordType:
    kw.setdefault("fields", [])
    kw.setdefault("bases", [])
    kw.setdefault("virtual_bases", [])
    kw.setdefault("vtable", [])
    return RecordType(name=name, kind=kind, **kw)  # type: ignore[arg-type]


def _change(
    kind: ChangeKind, symbol: str, description: str = "desc", **kw: object
) -> Change:
    return Change(kind=kind, symbol=symbol, description=description, **kw)  # type: ignore[arg-type]


def _diff_result(changes: list[Change], **kw: object) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=list(changes),
        **kw,  # type: ignore[arg-type]
    )


class _AllSuppression:
    """Duck-typed suppression: suppresses every change."""

    def is_suppressed(self, change: Change) -> bool:
        return True


# ===========================================================================
# reporter.py
# ===========================================================================


class TestReporterShowOnly:
    def test_check_element_exact_match(self) -> None:
        # Line 171: an exact element token (not a prefix) matches.
        from abicheck.reporter import ShowOnlyFilter

        filt = ShowOnlyFilter.parse("functions")
        # ``anon_field_changed`` is in the exact-match list for "functions".
        assert filt._check_element("anon_field_changed") is True
        # A kind that is neither a prefix nor an exact match returns False.
        assert filt._check_element("soname_changed") is False

    def test_parse_skips_empty_tokens(self) -> None:
        # Line 105: empty token after split is skipped.
        from abicheck.reporter import ShowOnlyFilter

        filt = ShowOnlyFilter.parse("breaking,, ,functions")
        assert "breaking" in filt.severities
        assert "functions" in filt.elements


class TestReporterStat:
    def test_to_stat_includes_source_and_risk(self) -> None:
        # Lines 223, 225: source-level breaks + risk parts in to_stat.
        from abicheck.reporter import to_stat

        changes = [
            _change(ChangeKind.FUNC_REMOVED, "rm"),
            _change(ChangeKind.METHOD_ACCESS_CHANGED, "ns::C::m"),
            _change(ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL, "sym"),
            _change(ChangeKind.FUNC_ADDED, "added"),
        ]
        result = _diff_result(changes, verdict=Verdict.BREAKING)
        text = to_stat(result)
        assert "source-level breaks" in text
        assert "risk" in text
        assert "compatible" in text

    def test_to_stat_no_changes(self) -> None:
        from abicheck.reporter import to_stat

        text = to_stat(_diff_result([], verdict=Verdict.NO_CHANGE))
        assert "no changes" in text


class TestReporterChangeToDict:
    def test_change_to_dict_without_kind_sets_uses_policy(self) -> None:
        # Lines 721-722 + _kind_to_severity 48-57: no kind_sets path.
        from abicheck.reporter import _change_to_dict

        c = _change(
            ChangeKind.FUNC_REMOVED,
            "rm",
            caused_by_type="root::Type",
            caused_count=3,
        )
        c.source_location = "header.h:42"
        c.affected_symbols = ["a", "b"]
        d = _change_to_dict(c, policy="strict_abi")
        assert d["severity"] == "breaking"
        assert d["caused_by_type"] == "root::Type"
        assert d["caused_count"] == 3
        assert d["source_location"] == "header.h:42"
        assert d["affected_symbols"] == ["a", "b"]

    def test_change_to_dict_severities_all_branches(self) -> None:
        # _kind_to_severity api_break / risk / compatible branches (51-57).
        from abicheck.reporter import _change_to_dict

        sev = {
            ChangeKind.METHOD_ACCESS_CHANGED: "api_break",
            ChangeKind.SYMBOL_SIZE_CHANGED_INTERNAL: "risk",
            ChangeKind.FUNC_ADDED: "compatible",
        }
        for kind, expected in sev.items():
            d = _change_to_dict(_change(kind, "s"), policy="strict_abi")
            assert d["severity"] == expected

    def test_change_to_dict_no_kind(self) -> None:
        # Line 724: object without a kind attribute → "unknown".
        from abicheck.reporter import _change_to_dict

        class _Bare:
            symbol = "x"
            description = "y"

        d = _change_to_dict(_Bare())
        assert d["severity"] == "unknown"
        assert d["kind"] == ""


class TestReporterJsonLeaf:
    def test_leaf_json_severity_from_sets(self) -> None:
        # Lines 471-481: _severity_from_sets covers breaking/api_break/risk/compatible.
        from abicheck.reporter import to_json

        # All four are *root type* change kinds → rendered via _severity_from_sets.
        changes = [
            _change(ChangeKind.TYPE_SIZE_CHANGED, "ns::T1"),  # breaking
            _change(ChangeKind.ENUM_MEMBER_RENAMED, "ns::E1"),  # api_break
            _change(ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED, "ns::E2"),  # risk
            _change(ChangeKind.ENUM_MEMBER_ADDED, "ns::E3"),  # compatible
        ]
        result = _diff_result(changes, verdict=Verdict.BREAKING)
        out = json.loads(to_json(result, report_mode="leaf"))
        assert out["verdict"] == "BREAKING"
        severities = {lc["kind"]: lc["severity"] for lc in out["leaf_changes"]}
        assert severities["type_size_changed"] == "breaking"
        assert severities["enum_member_renamed"] == "api_break"
        assert severities["enum_last_member_value_changed"] == "risk"
        assert severities["enum_member_added"] == "compatible"

    def test_leaf_json_unknown_severity_with_override(self) -> None:
        # Line 481: a kind moved out of all sets via policy override → "unknown".
        from abicheck.policy_file import PolicyFile
        from abicheck.reporter import to_json

        # Override TYPE_SIZE_CHANGED to NO_CHANGE so it leaves all four sets.
        pf = PolicyFile(overrides={ChangeKind.TYPE_SIZE_CHANGED: Verdict.NO_CHANGE})
        result = _diff_result(
            [_change(ChangeKind.TYPE_SIZE_CHANGED, "ns::T")],
            verdict=Verdict.COMPATIBLE,
            policy_file=pf,
        )
        out = json.loads(to_json(result, report_mode="leaf"))
        assert out["leaf_changes"][0]["severity"] == "unknown"


class TestReporterMarkdown:
    def test_markdown_no_changes(self) -> None:
        # Line 411: "_No ABI changes detected._" path in full markdown.
        from abicheck.reporter import to_markdown

        md = to_markdown(_diff_result([], verdict=Verdict.NO_CHANGE))
        assert "_No ABI changes detected._" in md

    def test_markdown_with_changes_renders(self) -> None:
        from abicheck.reporter import to_markdown

        changes = [
            _change(ChangeKind.TYPE_SIZE_CHANGED, "ns::T"),
            _change(ChangeKind.FUNC_REMOVED, "rm"),
        ]
        md = to_markdown(_diff_result(changes, verdict=Verdict.BREAKING))
        assert "ABI Report" in md

    def test_markdown_leaf_no_changes(self) -> None:
        # Line 411: leaf-mode markdown "_No ABI changes detected._".
        from abicheck.reporter import to_markdown

        md = to_markdown(
            _diff_result([], verdict=Verdict.NO_CHANGE), report_mode="leaf"
        )
        assert "_No ABI changes detected._" in md
        assert "leaf-change view" in md

    def test_markdown_leaf_with_type_changes(self) -> None:
        from abicheck.reporter import to_markdown

        changes = [
            _change(ChangeKind.TYPE_SIZE_CHANGED, "ns::T"),
            _change(ChangeKind.FUNC_REMOVED, "rm"),
        ]
        md = to_markdown(
            _diff_result(changes, verdict=Verdict.BREAKING), report_mode="leaf"
        )
        assert "leaf-change view" in md


class TestReporterSectionSeverityLabel:
    def test_section_severity_label_none_level(self) -> None:
        # Line 816: severity_config present but the category attr is None.
        from abicheck.reporter import _section_severity_label

        class _Cfg:
            some_category = None

        assert _section_severity_label(_Cfg(), "some_category") == ""

    def test_section_severity_label_with_level(self) -> None:
        from abicheck.reporter import _section_severity_label

        class _Level:
            value = "error"

        class _Cfg:
            cat = _Level()

        label = _section_severity_label(_Cfg(), "cat")
        assert "ERROR" in label

    def test_section_severity_label_no_config(self) -> None:
        from abicheck.reporter import _section_severity_label

        assert _section_severity_label(None, "cat") == ""


class TestReporterConfidenceSection:
    def test_append_confidence_section_returns_early_when_missing(self) -> None:
        # Line 1229: result without a confidence attr → early return.
        from abicheck.reporter import _append_confidence_section

        class _NoConf:
            confidence = None

        lines: list[str] = []
        _append_confidence_section(lines, _NoConf())  # type: ignore[arg-type]
        assert lines == []

    def test_append_confidence_section_renders(self) -> None:
        from abicheck.reporter import _append_confidence_section

        result = _diff_result(
            [],
            confidence=Confidence.HIGH,
            evidence_tiers=["elf", "dwarf"],
            coverage_warnings=["dwarf stripped"],
            evidence_tier=EvidenceTier.DWARF_AWARE,
        )
        lines: list[str] = []
        _append_confidence_section(lines, result)
        joined = "\n".join(lines)
        assert "Analysis Confidence" in joined
        assert "dwarf stripped" in joined


# ===========================================================================
# post_processing.py
# ===========================================================================


class TestPostProcessingHelpers:
    def test_safe_index_success_and_failure(self) -> None:
        # Lines 98-102: index OK True, exception → False.
        from abicheck.post_processing import _safe_index

        assert _safe_index(_snap()) is True

        class _Boom:
            def index(self) -> None:
                raise RuntimeError("partial snapshot")

        assert _safe_index(_Boom()) is False  # type: ignore[arg-type]

    def test_matches_suppression_key(self) -> None:
        # Lines 128-134: exact, empty, short-ambiguous, structured substring.
        from abicheck.post_processing import _matches_suppression_key

        assert _matches_suppression_key("foo", "foo") is True
        assert _matches_suppression_key("foo", "") is False
        # Short, unstructured key cannot substring-match.
        assert _matches_suppression_key("precompute", "comp") is False
        # Structured key (underscore) substring-matches.
        assert (
            _matches_suppression_key("kmeans_compute_avx512", "compute_avx512") is True
        )

    def test_change_matches_symbols(self) -> None:
        # Lines 229-234: empty symbol, exact, trailing-segment match.
        from abicheck.post_processing import _change_matches_symbols

        assert (
            _change_matches_symbols(_change(ChangeKind.FUNC_REMOVED, ""), {"foo"})
            is False
        )
        assert (
            _change_matches_symbols(_change(ChangeKind.FUNC_REMOVED, "foo"), {"foo"})
            is True
        )
        assert (
            _change_matches_symbols(
                _change(ChangeKind.FUNC_REMOVED, "ns::foo"), {"foo"}
            )
            is True
        )
        assert (
            _change_matches_symbols(
                _change(ChangeKind.FUNC_REMOVED, "ns::bar"), {"foo"}
            )
            is False
        )


class TestDetectInternalLeaksStep:
    def _leak_snaps(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        old = _snap(
            functions=[_public_fn("get", "ns::detail::Impl*", [])],
            types=[
                _record(
                    "ns::detail::Impl",
                    "struct",
                    size_bits=64,
                    fields=[TypeField(name="x", type="int")],
                )
            ],
        )
        new = _snap(
            functions=[_public_fn("get", "ns::detail::Impl*", [])],
            types=[
                _record(
                    "ns::detail::Impl",
                    "struct",
                    size_bits=128,
                    fields=[TypeField(name="x", type="int")],
                )
            ],
        )
        return old, new

    def test_leak_finding_added(self) -> None:
        from abicheck.post_processing import DetectInternalLeaks, PipelineContext

        old, new = self._leak_snaps()
        changes = [_change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Impl", "size")]
        ctx = PipelineContext(old=old, new=new)
        out = DetectInternalLeaks().run(list(changes), ctx)
        assert any(c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API for c in out)

    def test_leak_finding_suppressed(self) -> None:
        # Lines 613-614: synthetic leak finding respects suppression.
        from abicheck.post_processing import DetectInternalLeaks, PipelineContext

        old, new = self._leak_snaps()
        changes = [_change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Impl", "size")]
        ctx = PipelineContext(old=old, new=new, suppression=_AllSuppression())  # type: ignore[arg-type]
        out = DetectInternalLeaks().run(list(changes), ctx)
        assert not any(
            c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API for c in out
        )
        assert any(
            c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
            for c in ctx.suppressed
        )

    def test_no_leak_returns_unchanged(self) -> None:
        from abicheck.post_processing import DetectInternalLeaks, PipelineContext

        old = _snap(functions=[_public_fn("foo", "int", [])])
        new = _snap(functions=[_public_fn("foo", "int", [])])
        changes = [_change(ChangeKind.FUNC_ADDED, "bar")]
        ctx = PipelineContext(old=old, new=new)
        out = DetectInternalLeaks().run(list(changes), ctx)
        assert [c.kind for c in out] == [ChangeKind.FUNC_ADDED]

    def test_leak_finding_dedup_when_already_present(self) -> None:
        # Branch 615->611: a pre-existing leak finding is not duplicated.
        from abicheck.post_processing import DetectInternalLeaks, PipelineContext

        old, new = self._leak_snaps()
        changes = [
            _change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Impl", "size"),
            _change(
                ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
                "ns::detail::Impl",
                "pre-existing leak",
            ),
        ]
        ctx = PipelineContext(old=old, new=new)
        out = DetectInternalLeaks().run(list(changes), ctx)
        leak_count = sum(
            1 for c in out if c.kind == ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API
        )
        assert leak_count == 1


class TestDetectNamespacePatternsStep:
    def _ns_snaps(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        old = _snap(
            functions=[
                Function(
                    name="ns::v1::foo",
                    mangled="_Z9foov1",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ]
        )
        new = _snap(
            functions=[
                Function(
                    name="ns::v2::foo",
                    mangled="_Z9foov2",
                    return_type="void",
                    params=[],
                    visibility=Visibility.PUBLIC,
                ),
            ]
        )
        return old, new

    def test_namespace_finding_added(self) -> None:
        from abicheck.post_processing import DetectNamespacePatterns, PipelineContext

        old, new = self._ns_snaps()
        ctx = PipelineContext(old=old, new=new)
        out = DetectNamespacePatterns().run([], ctx)
        assert any(c.kind == ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED for c in out)

    def test_namespace_finding_suppressed(self) -> None:
        # Lines 570-571: namespace finding respects suppression.
        from abicheck.post_processing import DetectNamespacePatterns, PipelineContext

        old, new = self._ns_snaps()
        ctx = PipelineContext(old=old, new=new, suppression=_AllSuppression())  # type: ignore[arg-type]
        out = DetectNamespacePatterns().run([], ctx)
        assert not any(
            c.kind == ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED for c in out
        )
        assert ctx.suppressed

    def test_namespace_finding_deduped(self) -> None:
        # Lines 573-574: a finding whose (kind, symbol) already exists is dropped.
        from abicheck.post_processing import DetectNamespacePatterns, PipelineContext

        old, new = self._ns_snaps()
        ctx = PipelineContext(old=old, new=new)
        # First run to learn the emitted symbol, then seed it as pre-existing.
        first = DetectNamespacePatterns().run([], PipelineContext(old=old, new=new))
        ns_finding = next(
            c for c in first if c.kind == ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED
        )
        seed = _change(ns_finding.kind, ns_finding.symbol, "pre-existing")
        out = DetectNamespacePatterns().run([seed], ctx)
        count = sum(
            1 for c in out if c.kind == ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED
        )
        assert count == 1


class TestDemoteUnreachableInternalChurn:
    def test_demotes_unreachable_internal_change(self) -> None:
        from abicheck.post_processing import (
            DemoteUnreachableInternalChurn,
            PipelineContext,
        )

        old, new = _snap(), _snap()
        c = _change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Private", "size")
        ctx = PipelineContext(old=old, new=new)
        out = DemoteUnreachableInternalChurn().run([c], ctx)
        assert out == []
        assert ctx.out_of_surface == [c]

    def test_frozen_internal_namespace_kept(self) -> None:
        from abicheck.post_processing import (
            DemoteUnreachableInternalChurn,
            PipelineContext,
        )

        old, new = _snap(), _snap()
        c = _change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Frozen", "size")
        ctx = PipelineContext(
            old=old,
            new=new,
            frozen_namespaces=["**::detail::*"],
        )
        out = DemoteUnreachableInternalChurn().run([c], ctx)
        # Frozen namespace declaration keeps the finding in-surface.
        assert out == [c]
        assert ctx.out_of_surface == []


class TestEscalateFrozenNamespaceViolations:
    def test_tags_frozen_violation(self) -> None:
        from abicheck.post_processing import (
            EscalateFrozenNamespaceViolations,
            PipelineContext,
        )

        old, new = _snap(), _snap()
        c = _change(ChangeKind.FUNC_REMOVED, "ns::detail::r1::foo")
        ctx = PipelineContext(
            old=old,
            new=new,
            frozen_namespaces=["**::detail::r1::*"],
        )
        EscalateFrozenNamespaceViolations().run([c], ctx)
        assert c.frozen_namespace_violation == "**::detail::r1::*"
        assert c.description.startswith("[frozen-namespace violation")

    def test_no_frozen_namespaces_returns_early(self) -> None:
        from abicheck.post_processing import (
            EscalateFrozenNamespaceViolations,
            PipelineContext,
        )

        old, new = _snap(), _snap()
        c = _change(ChangeKind.FUNC_REMOVED, "ns::detail::r1::foo")
        ctx = PipelineContext(old=old, new=new)
        out = EscalateFrozenNamespaceViolations().run([c], ctx)
        assert out == [c]
        assert c.frozen_namespace_violation is None

    def test_redundant_findings_also_tagged(self) -> None:
        from abicheck.post_processing import (
            EscalateFrozenNamespaceViolations,
            PipelineContext,
        )

        old, new = _snap(), _snap()
        kept = _change(ChangeKind.FUNC_REMOVED, "ns::frozen::a")
        redundant = _change(ChangeKind.FUNC_REMOVED, "ns::frozen::b")
        ctx = PipelineContext(old=old, new=new, frozen_namespaces=["ns::frozen::*"])
        ctx.redundant.append(redundant)
        EscalateFrozenNamespaceViolations().run([kept], ctx)
        assert kept.frozen_namespace_violation == "ns::frozen::*"
        assert redundant.frozen_namespace_violation == "ns::frozen::*"

    def test_already_tagged_change_not_reprefixed(self) -> None:
        from abicheck.post_processing import (
            EscalateFrozenNamespaceViolations,
            PipelineContext,
        )

        old, new = _snap(), _snap()
        c = _change(ChangeKind.FUNC_REMOVED, "ns::frozen::x")
        c.frozen_namespace_violation = "preset"
        ctx = PipelineContext(old=old, new=new, frozen_namespaces=["ns::frozen::*"])
        EscalateFrozenNamespaceViolations().run([c], ctx)
        # Pre-existing tag is preserved, description untouched.
        assert c.frozen_namespace_violation == "preset"
        assert not c.description.startswith("[frozen-namespace violation")


class TestPipelineFallbackKept:
    def test_pipeline_sets_kept_when_filter_redundant_absent(self) -> None:
        # Line 868: ctx.kept fallback when FilterRedundant didn't run.
        from abicheck.post_processing import (
            EnrichSourceLocations,
            PostProcessingPipeline,
        )

        old = _snap(functions=[_public_fn("foo")])
        new = _snap(functions=[_public_fn("foo")])
        changes = [_change(ChangeKind.FUNC_ADDED, "bar")]
        pipeline = PostProcessingPipeline([EnrichSourceLocations()])
        ctx = pipeline.run(list(changes), old, new)
        assert [c.symbol for c in ctx.kept] == ["bar"]
        assert pipeline.step_names == ["enrich_source_locations"]


# ===========================================================================
# internal_leak.py
# ===========================================================================


class TestInternalLeakReachability:
    def test_seed_from_function_skips_empty_types(self) -> None:
        # Line 312: empty return/param types are skipped in seeding.
        from abicheck.internal_leak import compute_leak_paths

        snap = _snap(functions=[_public_fn("foo", "", [("a", "")])])
        # No internal types reachable, but the empty-type skip branch runs.
        assert compute_leak_paths(snap) == {}

    def test_seed_from_variables(self) -> None:
        # Lines 325-327: variable-typed seeding reaches an internal type.
        from abicheck.internal_leak import compute_leak_paths

        snap = _snap(
            variables=[Variable(name="g", mangled="g", type="ns::detail::Cfg")],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::Cfg" in paths

    def test_virtual_base_path(self) -> None:
        # Lines 375-376: virtual bases are enqueued and reached.
        from abicheck.internal_leak import compute_leak_paths

        snap = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                _record("Public", "class", virtual_bases=["ns::detail::VBase"]),
                _record(
                    "ns::detail::VBase",
                    "class",
                    fields=[TypeField(name="f", type="int")],
                ),
            ],
        )
        paths = compute_leak_paths(snap)
        assert "ns::detail::VBase" in paths

    def test_value_embedded_field_detected_in_leak_description(self) -> None:
        # Lines 504-513 + 536: value-embedding heuristic in detect_internal_leaks.
        from abicheck.internal_leak import detect_internal_leaks

        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                _record(
                    "Public",
                    "class",
                    fields=[TypeField(name="impl_", type="ns::detail::Impl")],
                ),
                _record("ns::detail::Impl", "struct", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                _record(
                    "Public",
                    "class",
                    fields=[TypeField(name="impl_", type="ns::detail::Impl")],
                ),
                _record("ns::detail::Impl", "struct", size_bits=128),
            ],
        )
        changes = [_change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Impl", "size")]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        # Value-embedded path → layout-propagation severity hint.
        assert "embedded-by-value" in leaks[0].description

    def test_bfs_skips_empty_typename(self) -> None:
        # Line 394: an empty typename in the queue is skipped.
        import collections

        from abicheck.internal_leak import _bfs_collect_paths

        queue: collections.deque[tuple[str, list[str]]] = collections.deque()
        queue.append(("", []))
        queue.append(("ns::detail::X", ["fn:foo"]))
        paths = _bfs_collect_paths(queue, {}, {"detail"})
        assert "ns::detail::X" in paths

    def test_record_field_value_embedded_helper(self) -> None:
        # Lines 510-513: field found by-value (True), indirect (False), missing (None).
        from abicheck.internal_leak import _record_field_is_value_embedded

        rec = _record(
            "C",
            "class",
            fields=[
                TypeField(name="byval", type="ns::detail::Impl"),
                TypeField(name="ptr", type="ns::detail::Impl*"),
            ],
        )
        assert _record_field_is_value_embedded(rec, "byval") is True
        assert _record_field_is_value_embedded(rec, "ptr") is False
        assert _record_field_is_value_embedded(rec, "missing") is None

    def test_path_value_embedding_missing_record(self) -> None:
        # Line 536: containing type not in the type map → continue, returns False.
        from abicheck.internal_leak import _path_describes_value_embedding

        snap = _snap()  # empty type map
        path = ["Public", "field:impl_", "ns::detail::Impl"]
        assert _path_describes_value_embedding(path, snap) is False

    def test_indirect_field_severity_hint(self) -> None:
        from abicheck.internal_leak import detect_internal_leaks

        old = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                _record(
                    "Public",
                    "class",
                    fields=[TypeField(name="impl_", type="ns::detail::Impl*")],
                ),
                _record("ns::detail::Impl", "struct", size_bits=64),
            ],
        )
        new = _snap(
            functions=[_public_fn("make", "Public*", [])],
            types=[
                _record(
                    "Public",
                    "class",
                    fields=[TypeField(name="impl_", type="ns::detail::Impl*")],
                ),
                _record("ns::detail::Impl", "struct", size_bits=128),
            ],
        )
        changes = [_change(ChangeKind.TYPE_SIZE_CHANGED, "ns::detail::Impl", "size")]
        leaks = detect_internal_leaks(changes, old, new)
        assert len(leaks) == 1
        assert "pointer / template" in leaks[0].description


# ===========================================================================
# stack_report.py
# ===========================================================================


def _graph() -> DependencyGraph:
    g = DependencyGraph(root="/app")
    g.nodes["/app"] = ResolvedDSO(
        path=Path("/app"),
        soname="app",
        needed=[],
        rpath="",
        runpath="",
        resolution_reason="root",
        depth=0,
    )
    g.nodes["/lib/libfoo.so"] = ResolvedDSO(
        path=Path("/lib/libfoo.so"),
        soname="libfoo.so",
        needed=[],
        rpath="",
        runpath="",
        resolution_reason="default",
        depth=1,
    )
    g.edges = [("/app", "/lib/libfoo.so")]
    return g


def _binding() -> SymbolBinding:
    return SymbolBinding(
        consumer="/app",
        symbol="sym",
        version="",
        provider="/lib/libfoo.so",
        status=BindingStatus.RESOLVED_OK,
        explanation="ok",
    )


def _content_changed_stack_change(verdict: Verdict) -> StackChange:
    diff = _diff_result(
        [_change(ChangeKind.FUNC_REMOVED, "gone", "removed")],
        verdict=verdict,
        confidence=Confidence.MEDIUM,
        evidence_tiers=["elf", "dwarf"],
        coverage_warnings=["dwarf partially stripped"],
        old_metadata=LibraryMetadata(
            path="/base/libfoo.so", sha256="aa" * 32, size_bytes=100
        ),
        new_metadata=LibraryMetadata(
            path="/cand/libfoo.so", sha256="bb" * 32, size_bytes=200
        ),
    )
    return StackChange(
        library="libfoo.so", change_type="content_changed", abi_diff=diff
    )


def _stack_result(stack_changes: list[StackChange]) -> StackCheckResult:
    g = _graph()
    return StackCheckResult(
        root_binary="/app",
        baseline_env="/base",
        candidate_env="/cand",
        loadability=StackVerdict.PASS,
        abi_risk=StackVerdict.WARN,
        baseline_graph=g,
        candidate_graph=g,
        bindings_baseline=[_binding()],
        bindings_candidate=[_binding()],
        missing_symbols=[],
        stack_changes=stack_changes,
        risk_score="medium",
    )


class TestStackReportContentChanged:
    def test_json_carries_confidence_and_metadata(self) -> None:
        # Lines 85-109: confidence / evidence_tiers / coverage_warnings /
        # old_metadata / new_metadata serialization for content_changed.
        from abicheck.stack_report import stack_to_json

        result = _stack_result([_content_changed_stack_change(Verdict.BREAKING)])
        data = json.loads(stack_to_json(result))
        sc = data["stack_changes"][0]
        assert sc["change_type"] == "content_changed"
        assert sc["abi_verdict"] == "BREAKING"
        assert sc["abi_breaking"] == 1
        assert sc["confidence"] == "medium"
        assert sc["evidence_tiers"] == ["elf", "dwarf"]
        assert sc["coverage_warnings"] == ["dwarf partially stripped"]
        assert sc["old_file"]["path"] == "/base/libfoo.so"
        assert sc["new_file"]["size_bytes"] == 200

    def test_markdown_breaking_content_change(self) -> None:
        # Lines 159-164: confidence + evidence + breaking change listing.
        from abicheck.stack_report import stack_to_markdown

        result = _stack_result([_content_changed_stack_change(Verdict.BREAKING)])
        md = stack_to_markdown(result)
        assert "content changed (ABI: `BREAKING`)" in md
        assert "Confidence: **MEDIUM**" in md
        assert "func_removed" in md

    def test_markdown_evidence_only_no_confidence(self) -> None:
        # Lines 160-161: evidence tiers shown when confidence is absent.
        from abicheck.stack_report import stack_to_markdown

        diff = _diff_result(
            [],
            verdict=Verdict.COMPATIBLE,
            evidence_tiers=["elf"],
        )
        # Force confidence attribute to None so the elif branch is taken.
        diff.confidence = None  # type: ignore[assignment]
        sc = StackChange(
            library="libfoo.so", change_type="content_changed", abi_diff=diff
        )
        md = stack_to_markdown(_stack_result([sc]))
        assert "Evidence: `elf`" in md

    def test_markdown_warn_verdict_emoji(self) -> None:
        from abicheck.stack_report import stack_to_markdown

        result = _stack_result([_content_changed_stack_change(Verdict.API_BREAK)])
        md = stack_to_markdown(result)
        assert "content changed (ABI: `API_BREAK`)" in md
