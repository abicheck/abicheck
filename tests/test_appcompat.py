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

"""Tests for the public ``abicheck.appcompat`` facade (ADR-005).

End-to-end/public-API tests only: the facade's exports, the standalone
``check_appcompat`` orchestrator (patched at the dump/compare boundary), and
the ADR-057 consumer-impact enrichment helpers. Layered tests live next to
their owners:

* ``tests/unit/extract/test_consumer_imports.py`` -- application import parsing
* ``tests/unit/extract/test_library_export_facts.py`` -- library export facts
* ``tests/unit/policy/test_consumer_requirements*.py`` -- pure evaluation
* ``tests/unit/workflows/test_consumer_scope.py`` -- scoping workflow
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import abicheck.appcompat as appcompat
from abicheck.appcompat import (
    AppCompatResult,
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
    check_against,
    check_appcompat,
    parse_app_requirements,
)
from abicheck.checker import Change, DiffResult
from abicheck.checker_policy import ChangeKind, ReachabilityState, Verdict
from abicheck.workflows.consumer_scope import scope_diff_to_consumer_facts

# ---------------------------------------------------------------------------
# Public data structures and facade surface
# ---------------------------------------------------------------------------


class TestDataStructures:
    def test_app_requirements_defaults(self):
        reqs = AppRequirements()
        assert reqs.needed_libs == []
        assert reqs.undefined_symbols == set()
        assert reqs.required_versions == {}

    def test_app_requirements_populated(self):
        reqs = AppRequirements(
            needed_libs=["libfoo.so.1", "libc.so.6"],
            undefined_symbols={"foo_init", "foo_process"},
            required_versions={"FOO_1.0": "libfoo.so.1"},
        )
        assert len(reqs.needed_libs) == 2
        assert "foo_init" in reqs.undefined_symbols
        assert reqs.required_versions["FOO_1.0"] == "libfoo.so.1"

    def test_app_compat_result_defaults(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp", old_lib_path="old.so", new_lib_path="new.so"
        )
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.missing_symbols == []
        assert result.breaking_for_app == []
        assert result.unreadable is False

    @pytest.mark.parametrize(
        "kwargs, verdict",
        [
            ({"missing_symbols": ["foo_init"]}, Verdict.BREAKING),
            ({"missing_versions": ["FOO_1.0"]}, Verdict.BREAKING),
            ({"required_symbol_count": 5}, Verdict.COMPATIBLE),
        ],
    )
    def test_result_carries_verdict(self, kwargs, verdict):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            verdict=verdict,
            **kwargs,
        )
        assert result.verdict == verdict


class TestFacadeSurface:
    def test_all_names_resolve_and_no_private_names(self):
        assert set(appcompat.__all__) == {
            "AppCompatResult",
            "AppRequirements",
            "ConsumerImportFacts",
            "LibraryExportFacts",
            "PluginHostContractResult",
            "check_against",
            "check_appcompat",
            "check_plugin_host_contract",
            "parse_app_requirements",
            "scope_diff_to_app",
            "scope_diff_to_required_symbols",
            "uncovered_missing_symbols",
        }
        for name in appcompat.__all__:
            assert getattr(appcompat, name) is not None
        assert not [n for n in vars(appcompat) if n.startswith("_") and n[1] != "_"]


# ---------------------------------------------------------------------------
# parse_app_requirements / check_against on real files (public API)
# ---------------------------------------------------------------------------


class TestParseAppRequirements:
    def test_unknown_format_raises(self, tmp_path):
        f = tmp_path / "unknown.bin"
        f.write_bytes(b"\x00\x00\x00\x00")
        with pytest.raises(ValueError, match="Cannot detect binary format"):
            parse_app_requirements(f, "libfoo.so")

    def test_corrupt_elf_returns_empty_requirements(self, tmp_path):
        """Recognised format, unreadable import table: no requirements are
        invented (the FAILED status is on ``read_consumer_imports``' fact)."""
        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert parse_app_requirements(f, "libfoo.so") == AppRequirements()

    def test_check_against_unknown_app_raises(self, tmp_path):
        app = tmp_path / "app"
        app.write_bytes(b"\x00\x00\x00\x00")
        with pytest.raises(ValueError, match="Cannot detect binary format"):
            check_against(app, tmp_path / "new.so")


# ---------------------------------------------------------------------------
# check_appcompat: standalone orchestrator, patched at dump/compare boundary
# ---------------------------------------------------------------------------

_STANDALONE = "abicheck.workflows.consumer_scope_standalone"


def _boundary(diff, scoped=None, fmt="elf"):
    """Patches for the dump/compare boundary plus the delegated scoping."""
    return (
        patch(
            "abicheck.workflows.input_resolution.detect_binary_format", return_value=fmt
        ),
        patch("abicheck.service_dump_native.run_dump", return_value=MagicMock()),
        patch("abicheck.workflows.compare_policy.compare_snapshots", return_value=diff),
        patch(
            f"{_STANDALONE}.scope_diff_to_app",
            return_value=scoped
            or AppCompatResult(app_path="app", old_lib_path="o", new_lib_path="n"),
        ),
    )


def _paths(tmp_path):
    return tmp_path / "app", tmp_path / "old.so", tmp_path / "new.so"


def _run_dump_calls(tmp_path, **kwargs):
    diff = DiffResult(old_version="1", new_version="2", library="libfoo")
    detect, run_dump, compare, scope = _boundary(diff)
    with detect, run_dump as mock_run_dump, compare, scope:
        check_appcompat(*_paths(tmp_path), **kwargs)
    assert mock_run_dump.call_count == 2
    return mock_run_dump.call_args_list


class TestCheckAppcompat:
    def test_unrecognised_binary_format_raises(self, tmp_path):
        """check_appcompat resolves each side's format itself (T5) ahead of
        run_dump -- an unresolvable format must raise, not reach run_dump
        with binary_fmt=None."""
        from abicheck.errors import ValidationError

        with (
            patch(
                "abicheck.workflows.input_resolution.detect_binary_format",
                return_value=None,
            ),
            pytest.raises(ValidationError, match="Unrecognised binary format"),
        ):
            check_appcompat(*_paths(tmp_path))

    def test_headers_passed_as_public_headers(self, tmp_path):
        """-H/--header is a public header list: the per-side paths must reach
        run_dump() as public_headers."""
        old_hdr, new_hdr = tmp_path / "old.h", tmp_path / "new.h"
        old_call, new_call = _run_dump_calls(
            tmp_path, old_headers=[old_hdr], new_headers=[new_hdr]
        )
        assert old_call.kwargs["public_headers"] == [old_hdr]
        assert new_call.kwargs["public_headers"] == [new_hdr]

    def test_both_dumps_leave_include_dependencies_at_default(self, tmp_path):
        """run_dump's wrapper defaults include_dependencies to True (Codex
        review, PR #840) -- check_appcompat must not override it."""
        for call in _run_dump_calls(tmp_path):
            assert "include_dependencies" not in call.kwargs

    def test_includes_passed_as_public_include_search_dirs(self, tmp_path):
        """Explicit per-side -I lists reach run_dump()'s
        public_include_search_dirs (Codex review, PR #839 round 7)."""
        old_inc, new_inc = tmp_path / "old_include", tmp_path / "new_include"
        old_call, new_call = _run_dump_calls(
            tmp_path,
            old_headers=[tmp_path / "old.h"],
            new_headers=[tmp_path / "new.h"],
            old_includes=[old_inc],
            new_includes=[new_inc],
        )
        assert old_call.kwargs["public_include_search_dirs"] == [old_inc]
        assert new_call.kwargs["public_include_search_dirs"] == [new_inc]

    def test_shared_headers_and_includes_fall_back_to_both_sides(self, tmp_path):
        hdr, inc = tmp_path / "foo.h", tmp_path / "include"
        for call in _run_dump_calls(tmp_path, headers=[hdr], includes=[inc]):
            assert call.kwargs["public_headers"] == [hdr]
            assert call.kwargs["public_include_search_dirs"] == [inc]

    def test_lang_c(self, tmp_path):
        """lang='c' reaches run_dump()'s positional `lang` slot unchanged."""
        for call in _run_dump_calls(tmp_path, lang="c"):
            assert call.args[5] == "c"

    @pytest.mark.parametrize(
        "required, exports, changes, verdict, missing",
        [
            (
                {"foo_init", "foo_process"},
                {"foo_init", "foo_process"},
                [],
                Verdict.COMPATIBLE,
                [],
            ),
            (
                {"foo_init", "foo_gone"},
                {"foo_init"},
                [],
                Verdict.BREAKING,
                ["foo_gone"],
            ),
            (
                {"foo_init"},
                {"foo_init"},
                [ChangeKind.FUNC_REMOVED],
                Verdict.BREAKING,
                [],
            ),
            (
                {"foo_init"},
                {"foo_init", "bar_new"},
                [ChangeKind.FUNC_ADDED],
                Verdict.COMPATIBLE,
                [],
            ),
            (set(), {"foo_init"}, [], Verdict.NO_CHANGE, []),
        ],
    )
    def test_delegates_compare_diff_to_scoping_and_closes_ledger(
        self, tmp_path, required, exports, changes, verdict, missing
    ):
        """The diff check_appcompat computed is the one it scopes (with the
        dumped OLD snapshot for the ADR-057 join), and the scoped verdict is
        returned unchanged after the single closing ledger call."""
        app, old_lib, new_lib = _paths(tmp_path)
        symbol = {ChangeKind.FUNC_REMOVED: "foo_init", ChangeKind.FUNC_ADDED: "bar_new"}
        diff = DiffResult(
            old_version="1",
            new_version="2",
            library="libfoo",
            changes=[
                Change(kind=k, symbol=symbol[k], description="d") for k in changes
            ],
        )
        consumer = ConsumerImportFacts(
            path=app,
            binary_format="elf",
            target_library="libfoo.so.1",
            requirements=AppRequirements(undefined_symbols=set(required)),
        )
        lib = LibraryExportFacts(
            label="lib",
            binary_format="elf",
            soname="libfoo.so.1",
            export_names=frozenset(),
        )
        new = LibraryExportFacts(
            label="new",
            binary_format="elf",
            soname="libfoo.so.1",
            export_names=frozenset(exports),
        )
        seen = {}

        def fake_scope(d, app_path, old, new_path, **kw):
            seen.update(diff=d, app=app_path, old=old, new=new_path, **kw)
            return scope_diff_to_consumer_facts(
                d, consumer, lib, new, policy=kw["policy"]
            )

        detect, run_dump, compare, _ = _boundary(diff)
        with (
            detect,
            run_dump,
            compare,
            patch(f"{_STANDALONE}.scope_diff_to_app", side_effect=fake_scope),
            patch(f"{_STANDALONE}.close_consumer_scope") as close,
        ):
            result = check_appcompat(app, old_lib, new_lib)

        assert seen["diff"] is diff
        assert (seen["app"], seen["old"], seen["new"]) == (app, old_lib, new_lib)
        assert seen["old_snapshot"] is not None
        assert result.verdict == verdict
        assert result.missing_symbols == missing
        close.assert_called_once()
        assert close.call_args.kwargs["gating"] == result.breaking_for_app

    def test_policy_file_reaches_scoping(self, tmp_path):
        pf = MagicMock()
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        detect, run_dump, compare, scope = _boundary(diff)
        with detect, run_dump, compare as mock_compare, scope as mock_scope:
            check_appcompat(*_paths(tmp_path), policy_file=pf, policy="sdk_vendor")
        assert mock_compare.call_args.kwargs["policy_file"] is pf
        assert mock_scope.call_args.kwargs["policy_file"] is pf
        assert mock_scope.call_args.kwargs["policy"] == "sdk_vendor"


class TestConsumerOverlayImpactAssessment:
    def test_consumer_required_symbol_removed_carries_impact_assessment(self):
        """ADR-052 D2 follow-up (G29 Phase 3): the consumer overlay caches its
        own ImpactAssessment at construction time, and
        impact.engine.assess_change() must reuse it unchanged."""
        from abicheck.impact.engine import assess_change

        consumer = ConsumerImportFacts(
            path=Path("myapp"),
            binary_format="elf",
            target_library="libfoo.so.1",
            requirements=AppRequirements(undefined_symbols={"foo_process"}),
        )

        def lib(label, exports):
            return LibraryExportFacts(
                label=label,
                binary_format="elf",
                soname="libfoo.so.1",
                export_names=frozenset(exports),
            )

        diff = DiffResult(old_version="1.0", new_version="2.0", library="libfoo.so.1")
        result = scope_diff_to_consumer_facts(
            diff,
            consumer,
            lib("old", {"foo_init", "foo_process"}),
            lib("new", {"foo_init"}),
        )
        (overlay,) = [
            c
            for c in result.breaking_for_app
            if c.kind == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
        ]
        assert overlay.impact_assessment is not None
        assert overlay.impact_assessment.public_reachable is True
        assert (
            overlay.impact_assessment.reachability_state
            == ReachabilityState.PROVEN_REACHABLE
        )
        assert overlay.impact_assessment.reachability_kind == "consumer_proven"
        # Mutating the flat fields after caching must not change what
        # assess_change() serves -- proves the cache is actually used.
        overlay.public_reachable = False
        overlay.reachability_state = ReachabilityState.UNKNOWN
        reassessed = assess_change(overlay)
        assert reassessed.public_reachable is True
        assert reassessed.reachability_state == ReachabilityState.PROVEN_REACHABLE
        assert reassessed.reachability_kind == "consumer_proven"


# ---------------------------------------------------------------------------
# ADR-057 consumer-impact enrichment (abicheck.appcompat_consumer_impact)
# ---------------------------------------------------------------------------


class TestHasImpactEvidence:
    """``_has_impact_evidence`` must key off a *meaningful* proof path, not
    merely "an ``impact_assessment`` object exists on the change" (Codex
    review, fresh evidence). Since ADR-052 Slice 10,
    ``post_processing.MarkReachability`` caches an ``ImpactAssessment`` on
    *every* change it tags — including a change it leaves ``UNKNOWN`` with
    no proof path and one it tags ``PROVEN_REACHABLE`` via a direct-symbol/
    public-source-ABI match with no walked path either. Treating either as
    "evidence of its own" would wrongly block
    :func:`abicheck.appcompat_consumer_impact.enrich_covered_changes` from attaching the
    consumer-graph explanation for the ordinary, common case this join
    exists to serve."""

    def _change(self, **kwargs):
        return Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z9dispatchv",
            description="Function removed: _Z9dispatchv",
            **kwargs,
        )

    def test_no_evidence_at_all_is_false(self):
        from abicheck.appcompat_consumer_impact import _has_impact_evidence

        assert _has_impact_evidence(self._change()) is False

    def test_a_cached_assessment_with_no_proof_path_is_false(self):
        """The exact regression shape: MarkReachability's blanket cache on
        an UNKNOWN, unproven change must not read as evidence."""
        from abicheck.appcompat_consumer_impact import _has_impact_evidence
        from abicheck.impact.engine import assess_change

        change = self._change(reachability_state=ReachabilityState.UNKNOWN)
        change.impact_assessment = assess_change(change)
        assert change.impact_assessment.proof_path is None
        assert _has_impact_evidence(change) is False

    def test_a_cached_assessment_tagged_reachable_with_no_path_is_false(self):
        """The direct-symbol/public-source-ABI-surface tag branches set
        ``public_reachable``/``reachability_state`` but no proof path —
        still not "evidence of its own" for this check's purpose."""
        from abicheck.appcompat_consumer_impact import _has_impact_evidence
        from abicheck.impact.engine import assess_change

        change = self._change(
            public_reachable=True,
            reachability_kind="direct_public_symbol",
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
        )
        change.impact_assessment = assess_change(change)
        assert change.impact_assessment.proof_path is None
        assert _has_impact_evidence(change) is False

    def test_a_cached_assessment_with_a_real_proof_path_is_true(self):
        from abicheck.appcompat_consumer_impact import _has_impact_evidence
        from abicheck.impact.engine import assess_change

        change = self._change(
            public_reachable=True,
            reachability_kind="value_embedding",
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
            reachability_proof_path="Foo -> Bar -> _Z9dispatchv",
        )
        change.impact_assessment = assess_change(change)
        assert change.impact_assessment.proof_path is not None
        assert _has_impact_evidence(change) is True

    def test_a_flat_reachability_proof_path_with_no_cached_assessment_is_true(self):
        from abicheck.appcompat_consumer_impact import _has_impact_evidence

        change = self._change(reachability_proof_path="Foo -> Bar -> _Z9dispatchv")
        assert change.impact_assessment is None
        assert _has_impact_evidence(change) is True


class TestEnrichCoveredChangesRefreshesCache:
    """:func:`abicheck.appcompat_consumer_impact.enrich_covered_changes` must refresh
    ``change.impact_assessment`` after attaching consumer evidence, not just
    the flat proof-path fields (Codex review, fresh evidence): a change
    reaching this function with a cached-but-pathless ``ImpactAssessment``
    (``MarkReachability``'s blanket Slice 10 cache) still has that stale
    object sitting on ``change.impact_assessment`` after enrichment --
    ``impact.engine.assess_change`` prefers any non-``None`` cached
    assessment, so a later JSON/SARIF render would keep returning
    ``proof_path=None`` and silently drop the newly attached explanation."""

    def test_stale_pathless_cache_is_refreshed_after_enrichment(self):
        from abicheck.appcompat_consumer_impact import (
            enrich_covered_changes as _enrich_covered_changes,
        )
        from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.impact.engine import assess_change

        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z9dispatchv",
            description="Function removed: _Z9dispatchv",
            reachability_state=ReachabilityState.UNKNOWN,
        )
        # Simulate MarkReachability's Slice 10 blanket cache: a real
        # ImpactAssessment object with no proof path.
        change.impact_assessment = assess_change(change)
        assert change.impact_assessment.proof_path is None

        graph = SourceGraphSummary()
        graph.add_node(GraphNode(id="decl://run", kind="source_decl", label="run"))
        explained = ConsumerImpactPath(
            consumer="training-service",
            symbol="_Z9dispatchv",
            declarations=("_Z9dispatchv",),
            public_entries=("run",),
        )
        assert explained.is_direct()

        _enrich_covered_changes([change], {"_Z9dispatchv": explained}, graph)

        assert change.affected_public_roots == ["run"]
        refreshed = change.impact_assessment
        assert refreshed.proof_path is not None
        # The refreshed cache must agree with a genuinely uncached
        # derivation -- assess_change() prefers change.impact_assessment
        # when it's non-None, so clear it first or this comparison would
        # just read the very cache under test.
        change.impact_assessment = None
        fresh = assess_change(change)
        assert refreshed.proof_path == fresh.proof_path


class TestAttachConsumerImpactStampsReachability:
    """:func:`abicheck.appcompat_consumer_impact.attach_consumer_impact` must stamp
    ``public_reachable``/``reachability_kind``/``reachability_state`` on the
    change it enriches, not just the proof-path fields (Codex review, fresh
    evidence): a shared ``Change`` reaching ``_enrich_covered_changes`` with
    no reachability-aware suppression configured at all never had
    ``MarkReachability`` run on it, so it still carries the dataclass
    defaults (``UNKNOWN``/``False``) -- attaching a concrete consumer-proven
    proof path without also raising those fields left an internally
    contradictory finding (a real proof path, but 'unknown'/'unreachable'
    status)."""

    def test_enrichment_marks_the_change_consumer_proven(self):
        from abicheck.appcompat_consumer_impact import (
            enrich_covered_changes as _enrich_covered_changes,
        )
        from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
        from abicheck.impact.consumer_graph import ConsumerImpactPath

        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="_Z9dispatchv",
            description="Function removed: _Z9dispatchv",
            # Left at the dataclass defaults, same as a real Change that
            # never went through MarkReachability (no suppression file, or
            # one whose rules never need reachability evidence).
        )
        assert change.reachability_state == ReachabilityState.UNKNOWN
        assert change.public_reachable is False

        graph = SourceGraphSummary()
        graph.add_node(GraphNode(id="decl://run", kind="source_decl", label="run"))
        explained = ConsumerImpactPath(
            consumer="training-service",
            symbol="_Z9dispatchv",
            declarations=("_Z9dispatchv",),
            public_entries=("run",),
        )

        _enrich_covered_changes([change], {"_Z9dispatchv": explained}, graph)

        assert change.public_reachable is True
        assert change.reachability_kind == "consumer_proven"
        assert change.reachability_state == ReachabilityState.PROVEN_REACHABLE
        # The cached assessment must agree -- assess_change() derives these
        # fields straight from the flat ones this stamps.
        assert change.impact_assessment.public_reachable is True
        assert change.impact_assessment.reachability_state == (
            ReachabilityState.PROVEN_REACHABLE
        )


class TestMergeConsumerImpactPaths:
    """:func:`abicheck.appcompat_consumer_impact._merge_consumer_impact_paths` must combine
    every matching symbol-level explanation for a shared Change, not keep
    only the first and silently discard the rest (Codex review, fresh
    evidence): a single Change can cover more than one missing export via
    ``affected_symbols`` (e.g. one type-size change breaking several
    removed functions), and _change_covers_symbol treats every one of them
    as covered."""

    def test_single_match_with_no_alternatives_is_equivalent(self):
        """A lone match with nothing to filter comes back with the same
        content -- but not necessarily the same object (Codex review, fresh
        evidence): the single-match case is no longer a special-cased
        early return, since a lone match can itself carry a
        differently-rooted alternative that needs the same filtering as
        the multi-match case (see the class below)."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath

        only = ConsumerImpactPath(consumer="app", symbol="foo", public_entries=("run",))
        merged = _merge_consumer_impact_paths([only])
        assert merged.consumer == only.consumer
        assert merged.symbol == only.symbol
        assert merged.public_entries == only.public_entries
        assert merged.entry_path == only.entry_path
        assert merged.alternative_entry_paths == only.alternative_entry_paths

    def test_single_matchs_own_differently_rooted_alternative_is_excluded(self):
        """The exact bug Codex flagged: explain_required_symbols can return
        one ConsumerImpactPath (the ordinary single-symbol case) whose own
        alternative_entry_paths already mixes candidates from more than one
        consumer-compiled entry -- not just multi-match merges. Without
        routing this through the same root filter, impact.engine's single
        affected_public_roots[0] would mislabel a differently-rooted
        alternative as though it started at the primary's own root."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        primary_edge = GraphEdge(src="entry_A", dst="foo", kind="DECL_CALLS_DECL")
        same_root_alt = GraphEdge(src="entry_A", dst="foo2", kind="DECL_CALLS_DECL")
        other_root_alt = GraphEdge(src="entry_C", dst="foo3", kind="DECL_CALLS_DECL")
        only = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            public_entries=("entry_A",),
            entry_path=[primary_edge],
            alternative_entry_paths=[[same_root_alt], [other_root_alt]],
        )
        merged = _merge_consumer_impact_paths([only])
        assert merged.entry_path == [primary_edge]
        assert [same_root_alt] in merged.alternative_entry_paths
        assert [other_root_alt] not in merged.alternative_entry_paths

    def test_multiple_matches_union_public_entries_and_declarations(self):
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath

        first = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            declarations=("foo",),
            public_entries=("run",),
        )
        second = ConsumerImpactPath(
            consumer="app",
            symbol="bar",
            declarations=("bar",),
            public_entries=("train",),
        )
        # A duplicate entry across matches must not appear twice.
        third = ConsumerImpactPath(
            consumer="app",
            symbol="baz",
            declarations=("bar",),
            public_entries=("run",),
        )
        merged = _merge_consumer_impact_paths([first, second, third])
        assert merged.consumer == "app"
        assert merged.symbol == "foo"  # display-only, first match's value
        assert merged.public_entries == ("run", "train")
        assert merged.declarations == ("foo", "bar")

    def test_non_primary_entry_paths_become_alternatives(self):
        """Two matches that share the SAME root: the non-primary's own
        path is a valid alternative for that root, so it is folded in.

        Both edges start at the SAME node id ("a") -- not just the same
        display label (Codex review, fresh evidence: an earlier version of
        this test used two DIFFERENT node ids ("a"/"b") sharing one label
        to model "same root", which is exactly the label-vs-node-identity
        conflation _merge_consumer_impact_paths must not make, since
        distinct nodes commonly share a label for C++ overloads)."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        edge_a = GraphEdge(src="a", dst="foo", kind="DECL_CALLS_DECL")
        edge_b = GraphEdge(src="a", dst="bar", kind="DECL_CALLS_DECL")
        first = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            public_entries=("run",),
            entry_path=[edge_a],
        )
        second = ConsumerImpactPath(
            consumer="app",
            symbol="bar",
            public_entries=("run",),
            entry_path=[edge_b],
        )
        merged = _merge_consumer_impact_paths([first, second])
        assert merged.entry_path == [edge_a]
        assert [edge_b] in merged.alternative_entry_paths

    def test_differently_rooted_paths_are_not_folded_into_alternatives(self):
        """A non-primary match whose own root differs from the primary's
        must NOT be folded into alternative_entry_paths (Codex review,
        fresh evidence): impact.engine._build_alternative_path stamps
        every merged alternative with the SAME single primary root, so
        including a differently-rooted path would serialize it in
        JSON/SARIF as though it started at the primary's own entry, when
        it actually starts at (and explains) an entirely different one."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        edge_a = GraphEdge(src="a", dst="foo", kind="DECL_CALLS_DECL")
        edge_b = GraphEdge(src="b", dst="bar", kind="DECL_CALLS_DECL")
        first = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            public_entries=("run",),
            entry_path=[edge_a],
        )
        second = ConsumerImpactPath(
            consumer="app",
            symbol="bar",
            public_entries=("train",),  # a different root than "run"
            entry_path=[edge_b],
        )
        merged = _merge_consumer_impact_paths([first, second])
        assert merged.entry_path == [edge_a]
        assert [edge_b] not in merged.alternative_entry_paths
        # The differently-rooted entry is still reported in public_entries
        # (the union), just not smuggled in as a same-rooted alternative.
        assert "train" in merged.public_entries

    def test_primarys_own_differently_rooted_alternative_is_excluded_too(self):
        """The identical guard applies to the PRIMARY match's own
        alternative_entry_paths, not just non-primary matches (Codex
        review, fresh evidence): explain_required_symbols can attach an
        alternative that started at a different consumer-compiled entry
        than the one select_preferred_graph_path chose as primary -- that
        alternative must not be folded in either, since
        assess_change()/GraphProofPath has no per-alternative root field
        and would serialize it as though it shared the primary's own
        start."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        primary_edge = GraphEdge(src="entry_A", dst="foo", kind="DECL_CALLS_DECL")
        same_root_alt = GraphEdge(src="entry_A", dst="foo2", kind="DECL_CALLS_DECL")
        other_root_alt = GraphEdge(src="entry_C", dst="foo3", kind="DECL_CALLS_DECL")
        first = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            public_entries=("entry_A",),
            entry_path=[primary_edge],
            alternative_entry_paths=[[same_root_alt], [other_root_alt]],
        )
        second = ConsumerImpactPath(
            consumer="app", symbol="bar", public_entries=("entry_A",)
        )
        merged = _merge_consumer_impact_paths([first, second])
        assert merged.entry_path == [primary_edge]
        assert [same_root_alt] in merged.alternative_entry_paths
        assert [other_root_alt] not in merged.alternative_entry_paths

    def test_non_primary_root_comparison_uses_node_id_not_label(self):
        """Codex review, fresh evidence: distinct public-entry nodes can
        share one display label -- C++ overloads are the common case -- so
        comparing m.public_entries[0] (a label) against the primary's own
        label is not proof m's path starts at the SAME graph node. Two
        matches here share the label "run" but their entry_path edges
        start at genuinely different node ids (entry_A vs entry_B); the
        non-primary match must NOT be folded in as a same-rooted
        alternative just because the labels collide."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        primary_edge = GraphEdge(src="entry_A", dst="foo", kind="DECL_CALLS_DECL")
        overload_edge = GraphEdge(src="entry_B", dst="bar", kind="DECL_CALLS_DECL")
        primary = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            public_entries=("run",),
            entry_path=[primary_edge],
        )
        # Same label "run", but a DIFFERENT graph node (entry_B, an
        # overload of "run" sharing its display name with entry_A).
        overload = ConsumerImpactPath(
            consumer="app",
            symbol="bar",
            public_entries=("run",),
            entry_path=[overload_edge],
        )
        merged = _merge_consumer_impact_paths([primary, overload])
        assert merged.entry_path == [primary_edge]
        assert [overload_edge] not in merged.alternative_entry_paths

    def test_non_primarys_own_differently_rooted_alternative_is_excluded(self):
        """Codex review, fresh evidence: a non-primary match's OWN
        alternative_entry_paths must be filtered per-alt too, not bulk
        folded in once the match's PREFERRED entry_path passes the
        same-root check. explain_required_symbols can attach a non-primary
        match's own alternative that started at a THIRD entry -- neither
        the primary's root nor the non-primary match's own preferred
        entry -- and that third-rooted path must not be smuggled in as
        though it shared the primary's root."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        primary_edge = GraphEdge(src="entry_A", dst="foo", kind="DECL_CALLS_DECL")
        # second's own preferred path shares the primary's root (entry_A).
        second_preferred = GraphEdge(src="entry_A", dst="bar2", kind="DECL_CALLS_DECL")
        # But one of second's OWN alternatives starts at a THIRD entry.
        second_own_alt_same_root = GraphEdge(
            src="entry_A", dst="bar3", kind="DECL_CALLS_DECL"
        )
        second_own_alt_third_root = GraphEdge(
            src="entry_C", dst="bar4", kind="DECL_CALLS_DECL"
        )
        primary = ConsumerImpactPath(
            consumer="app",
            symbol="foo",
            public_entries=("entry_A",),
            entry_path=[primary_edge],
        )
        second = ConsumerImpactPath(
            consumer="app",
            symbol="bar",
            public_entries=("entry_A",),
            entry_path=[second_preferred],
            alternative_entry_paths=[
                [second_own_alt_same_root],
                [second_own_alt_third_root],
            ],
        )
        merged = _merge_consumer_impact_paths([primary, second])
        assert merged.entry_path == [primary_edge]
        assert [second_preferred] in merged.alternative_entry_paths
        assert [second_own_alt_same_root] in merged.alternative_entry_paths
        assert [second_own_alt_third_root] not in merged.alternative_entry_paths

    def test_symbol_entry_and_path_come_from_the_same_match(self):
        """The exact contradiction Codex flagged: the first match is
        direct (entry_path=[]) and the second is indirect. symbol,
        entry_path, and the primary public entry must all come from the
        SAME match (the indirect one, since only it has a real path to
        show) -- not symbol/entries from the first match glued onto a
        path from a different one, which would read as 'A is reachable
        from entry A' followed by a chain that actually starts at and
        explains B."""
        from abicheck.appcompat_consumer_impact import _merge_consumer_impact_paths
        from abicheck.impact.consumer_graph import ConsumerImpactPath
        from abicheck.model.graph_facts import GraphEdge

        direct_first = ConsumerImpactPath(
            consumer="app",
            symbol="A",
            public_entries=("entry_A",),
            entry_path=[],  # direct: declaration IS the public entry
        )
        indirect_second = ConsumerImpactPath(
            consumer="app",
            symbol="B",
            public_entries=("entry_B",),
            entry_path=[GraphEdge(src="entry_B", dst="B", kind="DECL_CALLS_DECL")],
        )
        merged = _merge_consumer_impact_paths([direct_first, indirect_second])
        # entry_path came from indirect_second -- symbol and the primary
        # public entry must agree with it, not with direct_first.
        assert merged.entry_path == indirect_second.entry_path
        assert merged.symbol == "B"
        assert merged.public_entries[0] == "entry_B"
        # The direct match's own entry is still reported, just not first.
        assert "entry_A" in merged.public_entries

    def test_enrichment_uses_the_merged_explanation_end_to_end(self):
        """_enrich_covered_changes' own next(...)-vs-merge regression: a
        single shared Change whose affected_symbols names two missing
        exports must report both public roots, not just the first
        explanation found."""
        from abicheck.appcompat_consumer_impact import (
            enrich_covered_changes as _enrich_covered_changes,
        )
        from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
        from abicheck.impact.consumer_graph import ConsumerImpactPath

        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Config",
            description="Type size changed: Config",
            affected_symbols=["_Z3foov", "_Z3barv"],
        )
        graph = SourceGraphSummary()
        graph.add_node(GraphNode(id="decl://run", kind="source_decl", label="run"))
        graph.add_node(GraphNode(id="decl://train", kind="source_decl", label="train"))
        explanations = {
            "_Z3foov": ConsumerImpactPath(
                consumer="app", symbol="_Z3foov", public_entries=("run",)
            ),
            "_Z3barv": ConsumerImpactPath(
                consumer="app", symbol="_Z3barv", public_entries=("train",)
            ),
        }

        _enrich_covered_changes([change], explanations, graph)

        # Both symbols' public entries must be represented, not just the
        # first one found in dict iteration order.
        assert set(change.affected_public_roots) == {"run", "train"}
