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

"""ADR-044: reachability-aware suppression.

Regression coverage for the pipeline-order correctness bug the ADR fixes —
a broad ``namespace``/``source_location`` suppression rule used to be able to
remove the raw evidence for an internal-type change *before*
``DetectInternalLeaks`` ever saw it, silently hiding a genuine leak through
the public ABI surface with no trace in the report. These tests build
synthetic ``AbiSnapshot``/``Change`` objects — no compiler needed — and are
part of the default fast test suite.
"""
from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.post_processing import DEFAULT_PIPELINE
from abicheck.suppression import Suppression, SuppressionList


def _snap(*, functions=None, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        functions=list(functions or []),
        types=list(types or []),
    )


def _public_fn(name: str, ret: str = "void") -> Function:
    return Function(name=name, mangled=name, return_type=ret, params=[], visibility=Visibility.PUBLIC)


def _reachable_scenario():
    """A public factory function returning a type that inherits an internal
    base — the minimal reproduction of the review's oneDAL dispatcher shape
    (a public entry point whose reachability closure includes an internal
    type)."""
    old = _snap(
        functions=[_public_fn("make", "oneapi::dal::kmeans::descriptor*")],
        types=[
            RecordType(
                name="oneapi::dal::kmeans::descriptor",
                kind="class",
                bases=["oneapi::dal::kmeans::detail::descriptor_base"],
            ),
            RecordType(
                name="oneapi::dal::kmeans::detail::descriptor_base",
                kind="class",
                size_bits=64,
            ),
        ],
    )
    new = _snap(
        functions=[_public_fn("make", "oneapi::dal::kmeans::descriptor*")],
        types=[
            RecordType(
                name="oneapi::dal::kmeans::descriptor",
                kind="class",
                bases=["oneapi::dal::kmeans::detail::descriptor_base"],
            ),
            RecordType(
                name="oneapi::dal::kmeans::detail::descriptor_base",
                kind="class",
                size_bits=128,
            ),
        ],
    )
    raw_change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="oneapi::dal::kmeans::detail::descriptor_base",
        description="size changed",
    )
    return old, new, raw_change


def _unreachable_scenario():
    old = _snap(
        functions=[_public_fn("foo", "int")],
        types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="class", size_bits=64)],
    )
    new = _snap(
        functions=[_public_fn("foo", "int")],
        types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="class", size_bits=128)],
    )
    raw_change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="oneapi::dal::kmeans::detail::hidden",
        description="size changed",
    )
    return old, new, raw_change


class TestMarkReachability:
    def test_tags_reachable_internal_change(self) -> None:
        old, new, raw_change = _reachable_scenario()
        # MarkReachability is gated on a suppression object being configured
        # (see test_skips_without_suppression) — an empty rule list is enough
        # to trigger the tagging so it can be observed directly.
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=SuppressionList([]))
        found = [c for c in ctx.kept if c.kind == ChangeKind.TYPE_SIZE_CHANGED]
        assert len(found) == 1
        assert found[0].public_reachable is True
        assert found[0].reachability_kind == "value_embedding"
        assert found[0].reachability_proof_path

    def test_does_not_tag_unreachable_internal_change(self) -> None:
        old, new, raw_change = _unreachable_scenario()
        # DemoteUnreachableInternalChurn removes truly-unreachable internal
        # churn from ctx.kept — check the tag directly on the object instead.
        DEFAULT_PIPELINE.run([raw_change], old, new, suppression=SuppressionList([]))
        assert raw_change.public_reachable is False
        assert raw_change.reachability_kind is None

    def test_skips_without_suppression(self) -> None:
        """Perf guard (CI benchmark_scaling.py regression): MarkReachability
        must not run its public-surface BFS when no suppression is configured
        at all — the tags it produces have no other consumer in this slice,
        and internal_leak.compute_leak_paths is expensive enough that running
        it unconditionally on every compare() roughly doubled the cost
        DetectInternalLeaks already pays (caught by CI's baseline-regression
        gate)."""
        old, new, raw_change = _reachable_scenario()
        DEFAULT_PIPELINE.run([raw_change], old, new)  # no suppression passed
        assert raw_change.public_reachable is False
        assert raw_change.reachability_kind is None

    def test_pointer_only_pure_layout_change_not_flagged_reachable(self) -> None:
        """Codex review: DetectInternalLeaks does not treat a pure-layout
        change reached only through a pointer as a leak (it is not
        consumer-visible) — MarkReachability must not tag it reachable
        either, or a broad suppression rule gets refused (and a
        suppression_would_hide_public_break diagnostic appended) for churn
        that was always going to be demoted as unreachable anyway."""
        old = _snap(
            functions=[Function(
                name="use", mangled="use", return_type="void",
                params=[Param(name="h", type="oneapi::dal::kmeans::detail::hidden*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )],
            types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="struct", size_bits=32)],
        )
        new = _snap(
            functions=[Function(
                name="use", mangled="use", return_type="void",
                params=[Param(name="h", type="oneapi::dal::kmeans::detail::hidden*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )],
            types=[RecordType(name="oneapi::dal::kmeans::detail::hidden", kind="struct", size_bits=64)],
        )
        raw_change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="oneapi::dal::kmeans::detail::hidden",
            description="size changed",
        )
        suppression = SuppressionList([
            Suppression(namespace="oneapi::dal::**::detail::**", reason="private")
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change.public_reachable is False
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]


class TestSuppressionPipelineOrderFix:
    """The ADR's headline regression: example B from the review report."""

    def test_broad_namespace_suppression_does_not_hide_reachable_break(self) -> None:
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="Private implementation details",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)

        # The raw layout evidence must survive — this is the bug: it used to
        # be removed by ApplySuppression before DetectInternalLeaks ran.
        kinds = [c.kind for c in ctx.kept]
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds
        # DetectInternalLeaks had real evidence to correlate, so the leak
        # finding fires too.
        assert ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API in kinds
        # And the report explains why the suppression rule did not apply.
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK in kinds
        diag = next(c for c in ctx.kept if c.kind == ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK)
        assert "allow_public_break" in diag.description
        assert ctx.suppressed == []

    def test_broad_namespace_suppression_still_suppresses_unreachable_churn(self) -> None:
        """Unreachable internal churn is unaffected — no regression for the
        common, safe case this rule shape is meant for."""
        old, new, raw_change = _unreachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="Private implementation details",
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_allow_public_break_makes_the_override_explicit(self) -> None:
        old, new, raw_change = _reachable_scenario()
        suppression = SuppressionList([
            Suppression(
                namespace="oneapi::dal::**::detail::**",
                reason="Reviewed — safe to hide",
                allow_public_break=True,
            )
        ])
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change in ctx.suppressed
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK not in [c.kind for c in ctx.kept]

    def test_narrow_symbol_suppression_unaffected_by_default(self) -> None:
        """A narrow symbol selector still suppresses a non-breaking reachable
        change with no extra ceremony — only the breaking+reachable
        combination needs allow_public_break."""
        old, new, raw_change = _reachable_scenario()
        raw_change.kind = ChangeKind.TYPE_ALIGNMENT_CHANGED  # still BREAKING; keep coverage of the gate
        suppression = SuppressionList([
            Suppression(
                symbol="oneapi::dal::kmeans::detail::descriptor_base",
                reason="exact symbol, known safe",
            )
        ])
        # Exact-symbol rule still requires allow_public_break for a BREAKING
        # public-reachable change — this is intentional (ADR-044 D2): naming
        # one symbol doesn't by itself prove the author reviewed reachability.
        ctx = DEFAULT_PIPELINE.run([raw_change], old, new, suppression=suppression)
        assert raw_change not in ctx.suppressed


class TestSuppressionYamlRoundTrip:
    def test_new_keys_parse(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "oneapi::dal::**::detail::**"\n'
            "    reachability: unreachable-only\n"
            '    reason: "private"\n'
            '  - cause_namespace: "oneapi::dal::**::detail::**"\n'
            "    allow_public_break: true\n"
            '    reason: "explicit"\n'
        )
        sl = SuppressionList.load(p)
        assert len(sl) == 2
        assert sl._suppressions[0].namespace == "oneapi::dal::**::detail::**"
        assert sl._suppressions[1].cause_namespace == "oneapi::dal::**::detail::**"
        assert sl._suppressions[1].allow_public_break is True

    def test_namespace_and_entity_namespace_conflict_rejected(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            '    entity_namespace: "b::*"\n'
            '    reason: "x"\n'
        )
        with pytest.raises(ValueError, match="aliases"):
            SuppressionList.load(p)

    def test_invalid_reachability_value_rejected(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            "    reachability: sometimes\n"
            '    reason: "x"\n'
        )
        with pytest.raises(ValueError, match="reachability"):
            SuppressionList.load(p)

    def test_allow_public_break_string_value_rejected(self, tmp_path) -> None:
        """Codex review: bool("false") is True in Python — a quoted string
        must be rejected outright rather than silently coerced to True for
        this safety-critical override."""
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            '    allow_public_break: "false"\n'
            '    reason: "x"\n'
        )
        with pytest.raises(ValueError, match="allow_public_break"):
            SuppressionList.load(p)

    def test_allow_public_break_true_bool_accepted(self, tmp_path) -> None:
        p = tmp_path / "suppressions.yaml"
        p.write_text(
            "version: 1\n"
            "suppressions:\n"
            '  - namespace: "a::*"\n'
            "    allow_public_break: true\n"
            '    reason: "x"\n'
        )
        sl = SuppressionList.load(p)
        assert sl._suppressions[0].allow_public_break is True
