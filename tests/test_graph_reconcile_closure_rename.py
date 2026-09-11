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

"""A closure/anonymous-tag's coordinate shift must not read as a genuine
``declaration_renamed`` (G31 Phase B, ADR-048).

Regression for a real-world report: comparing real oneTBB releases (a
template/lambda-heavy corpus) produced 139 ``declaration_renamed`` L5
findings against oneDNN's 3 for a comparable corpus size. Manually tracing a
representative sample (see ``docs/contribute/known-gaps.md``'s "Lambda-closure
churn survives at the *function* level" entry, and this file's own cases)
showed the graph-reconciliation matcher was correctly PAIRING an
unrelated-edit-shifted closure with its own unchanged predecessor (via its
structural-context tier -- unaffected by this fix, see
``test_graph_reconcile.py``), but then mislabeling that pair
``declaration_renamed`` purely because the closure's own qualified-name
spelling embeds its source ``:line:col`` (``"(lambda:task_group.h:522:26)"``)
-- an unrelated edit elsewhere in the same header shifts dozens of otherwise
-unchanged closures' coordinates at once, and every one of them read as a
"rename" though nothing about the declaration itself changed.

Split out of ``test_graph_reconcile.py`` (own file, not folded into the
larger suite) since this covers both the ``buildsource.graph_reconcile``
outcome-classification call site and the general-purpose primitive behind
it, ``model.graph_identity.closure_location_free_identity`` -- per this
repo's AGENTS.md "Primitive-level property tests" guidance, a reusable
identity-comparison helper gets its own contract-as-invariants property
tests, not only example tests pinned to the oneTBB shape that motivated it.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.buildsource.graph_reconcile import (
    OUTCOME_COORDINATES_ONLY,
    OUTCOME_MOVED,
    OUTCOME_RECONCILED,
    OUTCOME_RENAMED,
    reconcile_added_removed,
)
from abicheck.buildsource.graph_reconcile_outcome import (
    COORDINATE_EVIDENCE_DECLARING_FILE,
    COORDINATE_EVIDENCE_PARTIAL_DECLARING_FILE,
    COORDINATE_EVIDENCE_QUALIFIED_NAME,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.model.graph_identity import closure_location_free_identity


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g.finalize()


def _reconcile_one_pair(
    old_node: GraphNode, new_node: GraphNode, *, edge_kind: str = "TYPE_HAS_FIELD_TYPE"
):
    """Reconcile a single old/new node pair, wired to a shared parent so the
    structural-context tier (the tier every closure-coordinate-only rename
    actually matches through -- its qualified name differs, so canonical-id
    and alias both miss) has a unique position to match on, mirroring
    ``test_graph_reconcile.py``'s own ``test_rename_reconciles_via_
    structural_context``.
    """
    parent = GraphNode(id="type://demo::Owner", kind="record_type", label="demo::Owner")
    old_edge = GraphEdge(
        src=parent.id, dst=old_node.id, kind=edge_kind, attrs={"role": "field"}
    )
    new_edge = GraphEdge(
        src=parent.id, dst=new_node.id, kind=edge_kind, attrs={"role": "field"}
    )
    old_g = _graph([parent, old_node], [old_edge])
    new_g = _graph([parent, new_node], [new_edge])
    result = reconcile_added_removed([old_node], [new_node], old_g, new_g)
    assert len(result.reconciled) == 1, (
        f"expected exactly one reconciled pair, got {result.to_dict()}"
    )
    return result.reconciled[0]


class TestClosureCoordinateShiftIsNotARename:
    """Regression: the oneTBB-shaped case, through the real reconciliation
    entry point (``reconcile_added_removed`` -> ``_classify_outcome``), not
    just the underlying primitive in isolation.
    """

    def test_parenthesized_lambda_coordinate_shift_reconciles_not_renamed(self) -> None:
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="raii_guard<(lambda:task_group.h:522:26)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:task_group.h:522:26)>",
                "def_file": "task_group.h",
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="raii_guard<(lambda:task_group.h:525:12)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:task_group.h:525:12)>",
                "def_file": "task_group.h",
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.match_kind == "structural_context"
        assert pair.outcome == OUTCOME_COORDINATES_ONLY, (
            "a pure coordinate shift on an otherwise-identical closure must "
            "not read as declaration_renamed, nor as declaration_identity_"
            "reconciled (that outcome's own prose asserts both name and "
            "location evidence changed, which is false here -- neither did)"
        )

    def test_coordinate_shift_without_any_declaring_file_evidence(self) -> None:
        """The measured oneTBB shape, through the real reconciliation entry
        point: a header-graph node carrying NO ``def_file`` attr and no
        ``SOURCE_DECLARES`` edge (15 of 15 otherwise-eligible pairs in that
        comparison). The coordinate shift is embedded in the qualified name
        itself, and ``closure_location_free_identity`` stripping it is what
        proved the rest identical -- so the absent declaring file must not
        push the pair into ``OUTCOME_RECONCILED``'s strictly stronger "both
        name and location evidence changed" claim."""
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="__type_identity<(lambda:global_control.h:172:22)>",
            attrs={
                "qualified_name": "__type_identity<(lambda:global_control.h:172:22)>"
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="__type_identity<(lambda:global_control.h:181:22)>",
            attrs={
                "qualified_name": "__type_identity<(lambda:global_control.h:181:22)>"
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_COORDINATES_ONLY

    def test_bare_anonymous_marker_coordinate_shift_reconciles_not_renamed(
        self,
    ) -> None:
        # The bare (unparenthesized) "unnamed <kind> at ..." spelling that
        # model.graph_identity also normalizes (see
        # test_source_graph_directory_taint.py's sibling coverage).
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="unnamed struct at foo.h:56:5",
            attrs={
                "qualified_name": "unnamed struct at /old/checkout/foo.h:56:5",
                "def_file": "foo.h",
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="unnamed struct at foo.h:61:5",
            attrs={
                "qualified_name": "unnamed struct at /new/checkout/foo.h:61:5",
                "def_file": "foo.h",
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_COORDINATES_ONLY

    def test_coordinate_shift_with_simultaneous_signature_change_stays_reconciled(
        self,
    ) -> None:
        # Codex review, fresh evidence: a real signature change (not just a
        # coordinate shift) alongside the coordinate shift must NOT read as
        # COORDINATES_ONLY -- old_qn != new_qn alone is not sufficient
        # evidence that coordinates are the *only* difference. Only the
        # kind/arity/param-types tail of normalized_signature differs here
        # (int -> long), independent of the coordinate embedded in the name.
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="raii_guard<(lambda:task_group.h:522:26)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:task_group.h:522:26)>",
                "def_file": "task_group.h",
                "param_types": ["int"],
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="raii_guard<(lambda:task_group.h:525:12)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:task_group.h:525:12)>",
                "def_file": "task_group.h",
                "param_types": ["long"],
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_RECONCILED, (
            "a coordinate shift accompanying a real signature change must "
            "not be reported as a no-op coordinate-only shift"
        )

    def test_closure_owning_template_genuinely_renamed_still_detected(self) -> None:
        # A REAL rename (the owning template's own name changed, not just
        # the closure's coordinates) alongside a coordinate shift must still
        # be reported as OUTCOME_RENAMED -- this fix must not blind the
        # matcher to an actual rename that happens to also touch a closure.
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="raii_guard<(lambda:task_group.h:522:26)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:task_group.h:522:26)>",
                "def_file": "task_group.h",
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="scoped_guard<(lambda:task_group.h:525:12)>",
            attrs={
                "qualified_name": "scoped_guard<(lambda:task_group.h:525:12)>",
                "def_file": "task_group.h",
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_RENAMED

    def test_closure_moved_to_different_file_reports_moved_not_renamed(self) -> None:
        # A real cross-file move must still be OUTCOME_MOVED -- dropping the
        # basename from the rename-vs-not comparison must not swallow a real
        # file change, since that is caught separately via def_file/
        # source_relative evidence, never via this marker text.
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="raii_guard<(lambda:old.h:522:26)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:old.h:522:26)>",
                "def_file": "old.h",
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="raii_guard<(lambda:new.h:8:2)>",
            attrs={
                "qualified_name": "raii_guard<(lambda:new.h:8:2)>",
                "def_file": "new.h",
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_MOVED

    def test_ordinary_non_closure_rename_still_detected(self) -> None:
        # Sanity: an ordinary (non-closure) rename is completely unaffected
        # by this fix -- mirrors test_graph_reconcile.py's own
        # test_rename_reconciles_via_structural_context.
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="demo::detail::RawConfig",
            attrs={"qualified_name": "demo::detail::RawConfig", "def_file": "detail.h"},
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="demo::detail::RawConfigV2",
            attrs={
                "qualified_name": "demo::detail::RawConfigV2",
                "def_file": "detail.h",
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_RENAMED

    def test_quoted_marker_shaped_literal_is_not_stripped(self) -> None:
        # Regression (CodeRabbit review): a C++20 fixed-string NTTP argument
        # can quote text that merely *looks* like a normalized closure
        # marker (e.g. a literal spelled "lambda:foo.h:1:2"). That text is
        # not a real CastXML anonymous/lambda marker -- it must be left
        # alone, so two specializations quoting genuinely different literal
        # arguments must still classify as a real rename, not silently
        # reconcile onto the same coordinate-free identity.
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label='Tag<"lambda:foo.h:1:2">',
            attrs={
                "qualified_name": 'Tag<"lambda:foo.h:1:2">',
                "def_file": "detail.h",
            },
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label='Tag<"lambda:foo.h:9:9">',
            attrs={
                "qualified_name": 'Tag<"lambda:foo.h:9:9">',
                "def_file": "detail.h",
            },
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_RENAMED, (
            "distinct quoted literal template arguments must not be "
            "silently reconciled as an incidental coordinate shift"
        )


# ── closure_location_free_identity: primitive-level property tests ────────
#
# Per AGENTS.md's "Primitive-level property tests" guidance: this is a
# reusable, general-purpose identity-comparison helper (not tied to any one
# caller's domain logic), so it gets its own contract stated as invariants,
# generated adversarially rather than only checked against the one oneTBB-
# shaped example above.

_HEADER_BASENAMES = st.sampled_from(["foo.h", "task_group.h", "detail.hpp", "a_b.h"])
_MARKERS = st.sampled_from(
    ["lambda", "unnamed struct", "unnamed union", "anonymous enum"]
)
_COORDS = st.tuples(
    st.integers(min_value=1, max_value=99999), st.integers(min_value=1, max_value=999)
)
_CHECKOUT_ROOTS = st.sampled_from(["/old/checkout", "/mnt/ci/build-42", "", "C:/src"])


def _parenthesized_marker(
    marker: str, root: str, basename: str, line: int, col: int
) -> str:
    sep = "/" if root else ""
    return f"({marker} at {root}{sep}{basename}:{line}:{col})"


def _bare_marker(marker: str, root: str, basename: str, line: int, col: int) -> str:
    sep = "/" if root else ""
    return f"{marker} at {root}{sep}{basename}:{line}:{col}"


class TestClosureLocationFreeIdentityProperties:
    @given(
        marker=_MARKERS,
        basename=_HEADER_BASENAMES,
        root1=_CHECKOUT_ROOTS,
        root2=_CHECKOUT_ROOTS,
        coords1=_COORDS,
        coords2=_COORDS,
    )
    def test_location_invariant_for_same_marker_and_basename(
        self, marker, basename, root1, root2, coords1, coords2
    ) -> None:
        """The whole point of the primitive: two spellings of the SAME
        marker + basename differing only in checkout root and/or
        line:col must always reduce to the identical identity, regardless
        of which coordinates or roots were used."""
        a = _parenthesized_marker(marker, root1, basename, *coords1)
        b = _parenthesized_marker(marker, root2, basename, *coords2)
        assert closure_location_free_identity(a) == closure_location_free_identity(b)

    @given(
        marker=_MARKERS,
        basename1=_HEADER_BASENAMES,
        basename2=_HEADER_BASENAMES,
        root=_CHECKOUT_ROOTS,
        coords1=_COORDS,
        coords2=_COORDS,
    )
    def test_basename_invariant_for_same_marker(
        self, marker, basename1, basename2, root, coords1, coords2
    ) -> None:
        """Deliberately basename-insensitive too (unlike the sibling
        node-id normalizer in the same module): a real cross-file move is
        caught separately via declaring-file evidence in
        ``graph_reconcile._classify_outcome``, never via this marker text,
        so collapsing the basename here only changes the outcome label from
        the misleading ``renamed`` to the correct ``moved`` -- it never
        hides the file difference itself (see
        ``TestClosureCoordinateShiftIsNotARename.
        test_closure_moved_to_different_file_reports_moved_not_renamed``)."""
        a = _parenthesized_marker(marker, root, basename1, *coords1)
        b = _parenthesized_marker(marker, root, basename2, *coords2)
        assert closure_location_free_identity(a) == closure_location_free_identity(b)

    @given(
        marker=_MARKERS,
        basename=_HEADER_BASENAMES,
        root=_CHECKOUT_ROOTS,
        coords=_COORDS,
    )
    def test_parenthesized_and_bare_forms_agree(
        self, marker, basename, root, coords
    ) -> None:
        """Both spellings clang/castxml are known to produce for the exact
        same underlying marker (see model.graph_identity's own docstring)
        must reduce to an identity naming the same marker -- differing only
        in the surrounding parens, never in substance."""
        paren = closure_location_free_identity(
            _parenthesized_marker(marker, root, basename, *coords)
        )
        bare = closure_location_free_identity(
            _bare_marker(marker, root, basename, *coords)
        )
        assert paren == f"({bare})"

    @given(
        marker1=_MARKERS, marker2=_MARKERS, basename=_HEADER_BASENAMES, coords=_COORDS
    )
    def test_distinct_marker_kinds_never_collapse(
        self, marker1, marker2, basename, coords
    ) -> None:
        """A lambda and an unnamed struct/union/enum are never the same
        entity -- collapsing across marker kinds would be a real
        over-merge, not the noise this primitive exists to remove."""
        a = closure_location_free_identity(
            _parenthesized_marker(marker1, "", basename, *coords)
        )
        b = closure_location_free_identity(
            _parenthesized_marker(marker2, "", basename, *coords)
        )
        if marker1 == marker2:
            assert a == b
        else:
            assert a != b

    @given(st.text(min_size=0, max_size=80))
    def test_idempotent(self, text) -> None:
        once = closure_location_free_identity(text)
        twice = closure_location_free_identity(once)
        assert once == twice

    @given(
        st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=0,
            max_size=200,
        )
    )
    def test_never_raises_on_arbitrary_text(self, text) -> None:
        # Must degrade gracefully on adversarial/malformed input -- the
        # primitive is applied to producer-supplied qualified-name text,
        # not just the well-formed shapes this test file otherwise builds.
        closure_location_free_identity(text)

    def test_plain_qualified_name_with_no_marker_is_unchanged(self) -> None:
        # No closure/anonymous-tag marker at all -> a no-op, never mutates
        # ordinary ABI-surface identifiers.
        name = "ns::detail::Widget<int, std::vector<double>>"
        assert closure_location_free_identity(name) == name

    def test_no_op_when_at_present_but_no_colon_survives_normalization(self) -> None:
        # Exercises the second fast-path guard directly: an identity can
        # contain the substring "at" (failing the cheap first check) while
        # still carrying no ":" anywhere, including after normalization --
        # e.g. an ordinary spelling like "static_cast" embeds "at" but is
        # not location-shaped at all, so no marker regex ever matches.
        name = "static_cast<Foo>"
        assert "at" in name
        assert ":" not in name
        assert closure_location_free_identity(name) == name

    def test_quoted_marker_shaped_text_is_never_stripped(self) -> None:
        # Regression (CodeRabbit review): marker-shaped text inside a
        # `"..."` quoted literal (a C++20 fixed-string NTTP argument, say)
        # is source *content*, not a real anonymous/lambda marker -- it
        # must pass through completely unchanged, mirroring
        # `_strip_bare_anonymous_type_location`'s own quoted-span guard.
        # Without it, two distinct quoted literals would collapse onto the
        # same coordinate-free identity.
        a = 'Tag<"lambda:foo.h:1:2">'
        b = 'Tag<"lambda:foo.h:9:9">'
        assert closure_location_free_identity(a) == a
        assert closure_location_free_identity(b) == b
        assert closure_location_free_identity(a) != closure_location_free_identity(b)

    def test_no_op_on_text_with_neither_at_nor_colon(self) -> None:
        # Exercises the cheap fast-path guard directly.
        assert closure_location_free_identity("PlainName") == "PlainName"


class TestCoordinateEvidenceIsStated:
    """Codex review (PR #1228, P2): a coordinate-only pair with no
    declaring-file evidence on either side read its coordinate shift out of
    the qualified name alone, which cannot rule out a same-basename
    cross-directory move. The classification stays -- declining it asserts
    OUTCOME_RECONCILED's strictly stronger "both name and location evidence
    changed" on that same absent evidence -- so the narrowing is to record
    WHICH provider carried it and say so in the finding a reader sees.
    """

    def test_fileless_pair_records_qualified_name_evidence(self) -> None:
        old_node = GraphNode(
            id="type://old",
            kind="record_type",
            label="w<(lambda:g.h:172:22)>",
            attrs={"qualified_name": "w<(lambda:g.h:172:22)>"},
        )
        new_node = GraphNode(
            id="type://new",
            kind="record_type",
            label="w<(lambda:g.h:181:22)>",
            attrs={"qualified_name": "w<(lambda:g.h:181:22)>"},
        )
        pair = _reconcile_one_pair(old_node, new_node)
        assert pair.outcome == OUTCOME_COORDINATES_ONLY
        assert pair.coordinate_evidence == COORDINATE_EVIDENCE_QUALIFIED_NAME
        assert pair.to_dict()["coordinate_evidence"] == (
            COORDINATE_EVIDENCE_QUALIFIED_NAME
        )

    def test_two_sided_file_evidence_records_the_stronger_provider(self) -> None:
        attrs_old = {"qualified_name": "w<(lambda:g.h:172:22)>", "def_file": "g.h"}
        attrs_new = {"qualified_name": "w<(lambda:g.h:181:22)>", "def_file": "g.h"}
        pair = _reconcile_one_pair(
            GraphNode(
                id="type://old",
                kind="record_type",
                label="w<(lambda:g.h:172:22)>",
                attrs=attrs_old,
            ),
            GraphNode(
                id="type://new",
                kind="record_type",
                label="w<(lambda:g.h:181:22)>",
                attrs=attrs_new,
            ),
        )
        assert pair.outcome == OUTCOME_COORDINATES_ONLY
        assert pair.coordinate_evidence == COORDINATE_EVIDENCE_DECLARING_FILE

    def test_non_coordinate_outcomes_record_no_evidence(self) -> None:
        """The field is about one outcome only: a rename or a move must not
        acquire a coordinate-evidence label it says nothing about."""
        pair = _reconcile_one_pair(
            GraphNode(
                id="type://old",
                kind="record_type",
                label="ns::Widget",
                attrs={"qualified_name": "ns::Widget", "def_file": "a.h"},
            ),
            GraphNode(
                id="type://new",
                kind="record_type",
                label="ns::WidgetV2",
                attrs={"qualified_name": "ns::WidgetV2", "def_file": "a.h"},
            ),
        )
        assert pair.outcome == OUTCOME_RENAMED
        assert pair.coordinate_evidence is None
        assert "coordinate_evidence" not in pair.to_dict()

    def test_one_sided_file_evidence_is_reported_as_its_own_gap(self) -> None:
        """Codex review (PR #1228): with a declaring file on one side only,
        the evidence value said `qualified_name` and the finding's text
        said "no declaring file was recorded on either side" -- false, and
        it hid a one-sided extraction gap. Both orientations, since the
        gap is the same gap whichever side lost the path."""
        for label, (old_file, new_file) in {
            "old_only": ("g.h", None),
            "new_only": (None, "g.h"),
        }.items():
            attrs_old = {"qualified_name": "w<(lambda:g.h:172:22)>"}
            attrs_new = {"qualified_name": "w<(lambda:g.h:181:22)>"}
            if old_file:
                attrs_old["def_file"] = old_file
            if new_file:
                attrs_new["def_file"] = new_file
            pair = _reconcile_one_pair(
                GraphNode(
                    id="type://old",
                    kind="record_type",
                    label="w<(lambda:g.h:172:22)>",
                    attrs=attrs_old,
                ),
                GraphNode(
                    id="type://new",
                    kind="record_type",
                    label="w<(lambda:g.h:181:22)>",
                    attrs=attrs_new,
                ),
            )
            assert pair.outcome == OUTCOME_COORDINATES_ONLY, label
            assert pair.coordinate_evidence == (
                COORDINATE_EVIDENCE_PARTIAL_DECLARING_FILE
            ), label

    def test_one_sided_finding_text_does_not_claim_neither_side_had_one(
        self,
    ) -> None:
        """The consumer half of the case above: the description must not
        assert the stronger, false "on either side" claim."""
        from abicheck.buildsource.graph_reconcile import (
            GraphReconciliation,
            diff_graph_reconciliation_findings,
        )

        pair = _reconcile_one_pair(
            GraphNode(
                id="type://old",
                kind="record_type",
                label="w<(lambda:g.h:1:2)>",
                attrs={"qualified_name": "w<(lambda:g.h:1:2)>", "def_file": "g.h"},
            ),
            GraphNode(
                id="type://new",
                kind="record_type",
                label="w<(lambda:g.h:9:9)>",
                attrs={"qualified_name": "w<(lambda:g.h:9:9)>"},
            ),
        )
        findings = diff_graph_reconciliation_findings(
            GraphReconciliation(reconciled=[pair])
        )
        assert len(findings) == 1, findings
        text = findings[0].description
        assert "on either side" not in text, text
        assert "on one side but not the other" in text, text

    def test_finding_description_discloses_the_weaker_evidence(self) -> None:
        """The field has a real consumer: the emitted finding's own text.
        A `qualified_name`-evidence pair must say a same-basename move
        would be indistinguishable; a `declaring_file` pair must not."""
        from abicheck.buildsource.graph_reconcile import (
            GraphReconciliation,
            diff_graph_reconciliation_findings,
        )

        def _finding(attrs_old, attrs_new):
            old_node = GraphNode(
                id="type://old", kind="record_type", label="w<a>", attrs=attrs_old
            )
            new_node = GraphNode(
                id="type://new", kind="record_type", label="w<b>", attrs=attrs_new
            )
            pair = _reconcile_one_pair(old_node, new_node)
            rec = GraphReconciliation(reconciled=[pair])
            findings = diff_graph_reconciliation_findings(rec)
            assert len(findings) == 1, findings
            return findings[0]

        fileless = _finding(
            {"qualified_name": "w<(lambda:g.h:1:2)>"},
            {"qualified_name": "w<(lambda:g.h:9:9)>"},
        )
        assert "no declaring file was recorded on either side" in fileless.description
        assert "same file basename" in fileless.description

        filed = _finding(
            {"qualified_name": "w<(lambda:g.h:1:2)>", "def_file": "g.h"},
            {"qualified_name": "w<(lambda:g.h:9:9)>", "def_file": "g.h"},
        )
        assert "no declaring file was recorded" not in filed.description
