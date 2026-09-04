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

"""L5 source-graph completeness rollup for ``analysis_assurance.py``, split
out to keep that module under the architecture debt-no-growth ceiling
(ADR-061).

``_graph_completeness`` and the extractor-family vocabulary it reads
(``_L5_GRAPH_EXTRACTOR_FAMILIES``/``_is_l5_graph_extractor_record``/
``_CONDITIONALLY_APPLICABLE_FAMILIES``) are used nowhere else in
``analysis_assurance.py`` and take only ``BuildSourcePack | None`` in, so
this is a self-contained chunk with no dependency on the rest of that
module. ``analysis_assurance.py`` imports ``_graph_completeness`` back via
an explicit ``X as X`` re-export (the same convention ``ctf_metadata.py``'s
and ``dwarf_advanced.py``'s own splits, and ``checker_policy.py``'s
``ChangeKind``, already use), since a few tests import it directly from
``analysis_assurance`` for an isolated, unit-level check.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .model.source_graph import SourceGraphSummary

#: The real L5 source-graph extractor families
#: (``buildsource/inline_graph_fold.py``'s ``fold_*`` functions each record
#: one ``ExtractorRecord`` named ``"<family>:<producer>"``, e.g.
#: ``"call_graph:clang"``, ``"type_graph:clang"``, ``"include_graph:clang"``,
#: ``"override_graph:clang"``, ``"template_graph:clang"``,
#: ``"macro_graph:clang"``, ``"callback_graph:clang"``,
#: ``"archive_graph:ar_index"``. Finding (review, P1): the previous version
#: of ``_graph_completeness`` matched on a ``"source_graph"`` prefix, which
#: is not how any real extractor is actually named, so a genuinely
#: partial/failed L5 graph extractor status was never recognized and could
#: read as ``"complete"``.
_L5_GRAPH_EXTRACTOR_FAMILIES: frozenset[str] = frozenset(
    {
        "archive_graph",
        "call_graph",
        "callback_graph",
        "include_graph",
        "macro_graph",
        "override_graph",
        "template_graph",
        "type_graph",
    }
)


def _is_l5_graph_extractor_record(name: str) -> bool:
    """Whether *name* (an ``ExtractorRecord.name``) belongs to one of the L5
    source-graph extractor families above -- the part before the first
    ``":"``."""
    family = name.split(":", 1)[0]
    return family in _L5_GRAPH_EXTRACTOR_FAMILIES


#: P0.4 (P2 review): ``fold_archive_graph`` correctly records nothing on a side with no ``static_library`` node.
_CONDITIONALLY_APPLICABLE_FAMILIES: dict[str, Callable[[SourceGraphSummary], bool]] = {
    "archive_graph": lambda sg: any(n.kind == "static_library" for n in sg.nodes),
}


def _graph_completeness(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    """Graph-completeness rollup over both sides' L5 ``SourceGraphSummary``.

    Finding (review, P1): the previous implementation only ever looked at
    ``degraded_passes`` and defaulted to ``"complete"`` for every other
    state, silently treating three genuinely-incomplete states as complete:

    - a pass that ran only over ``narrowed_passes``/``narrowed_scope`` -- a
      real, distinct field from ``degraded_passes``: the pass completed
      cleanly, but only over a subset of the project, so it does not
      establish full-project coverage;
    - a graph carrying no ``extractor_passes``/``narrowed_passes`` at all
      (a hand-built graph, or one from before that bookkeeping existed) --
      unknown pass coverage, not "everything ran";
    - old/new graph asymmetry -- one side carries an L5 graph and the other
      does not, so whatever the present side's graph says, the comparison
      itself never examined the missing side's implementation at all.

    Finding (review, P1): even after the fix above, this function only ever
    consulted ``SourceGraphSummary.degraded_passes``/``narrowed_passes``/
    ``extractor_passes`` -- boolean, per-pass-name coverage flags. It never
    consulted ``BuildSourcePack.manifest.extractors``, the reproducibility
    ledger where each real L5 graph extractor (``call_graph:clang``,
    ``type_graph:clang``, ``include_graph:clang``, ...) records its own
    ``status`` (``ok``/``partial``/``failed``/``skipped``). The two are not
    redundant: ``fold_call_graph`` (``inline_graph_fold.py``) only sets
    ``degraded_passes``/``narrowed_passes`` when the run was unnarrowed with
    diagnostics, or confirmed narrowed -- a pass that ran unnarrowed, hit no
    per-TU diagnostics, but still found zero edges records
    ``status="partial"`` on its own ``ExtractorRecord`` without setting any
    of those three booleans, so it was silently invisible here and could
    read as ``"complete"``. Fixed by also scanning each side's
    ``manifest.extractors`` for a record in one of the recognized L5 graph
    families (see :data:`_L5_GRAPH_EXTRACTOR_FAMILIES`) whose own ``status``
    is not ``"ok"``, folding it into the same ``degraded`` bucket used for
    the pre-existing signals -- a partial/failed/skipped extractor is
    exactly the kind of incomplete evidence ``degraded`` already means.

    Finding (review, P2): that fix over-corrected. A confirmed, fully-run,
    genuinely edge-free pass (e.g. a project with no call/override/template
    edges to find) also records ``status="partial"`` on its own
    ``ExtractorRecord`` -- the real producers key ``status`` off "did this
    pass add any edges", not "did this pass examine everything it was asked
    to". Folding *every* non-``"ok"`` record into ``degraded`` therefore
    treated that legitimate, fully-covered zero-edge case identically to a
    genuine shortfall (a crashed/unavailable extractor), which could fail
    ``--require-complete-analysis`` on a simple, cleanly-examined project.
    Fixed below by exempting a record whose own extractor family is
    separately confirmed complete (``SourceGraphSummary.extractor_passes``)
    or confirmed narrowed (``SourceGraphSummary.narrowed_passes``) on the
    same side -- both are the pass's own stronger "this genuinely ran"
    signal, set unconditionally on success regardless of edge count. A
    record with neither confirmation (a real "failed"/crash, or any other
    unconfirmed case) still folds into ``degraded``, unchanged.
    """
    notes: list[str] = []
    old_graph = getattr(old_pack, "source_graph", None)
    new_graph = getattr(new_pack, "source_graph", None)
    if old_graph is None and new_graph is None:
        return "not_collected", notes
    if old_graph is None or new_graph is None:
        missing = "old" if old_graph is None else "new"
        notes.append(
            f"graph completeness unknown: the {missing} side carries no L5 "
            "source graph -- the comparison never examined that side's "
            "implementation at all"
        )
        return "unknown", notes

    degraded = False
    narrowed = False
    unknown_pass_coverage = False
    for side, sg in (("old", old_graph), ("new", new_graph)):
        for pass_name, is_degraded in (sg.degraded_passes or {}).items():
            if is_degraded:
                degraded = True
                notes.append(
                    f"source-graph pass {pass_name!r} ran degraded on the {side} side"
                )
        for pass_name, is_narrowed in (sg.narrowed_passes or {}).items():
            if is_narrowed:
                narrowed = True
                notes.append(
                    f"source-graph pass {pass_name!r} ran narrowed-scope on "
                    f"the {side} side -- it did not examine the whole project"
                )
        if not sg.extractor_passes and not sg.narrowed_passes:
            unknown_pass_coverage = True
            notes.append(
                f"the {side} side's source graph records no "
                "extractor_passes/narrowed_passes -- which parts of the "
                "project it actually examined is unknown"
            )

    for side, pack, sg in (
        ("old", old_pack, old_graph),
        ("new", new_pack, new_graph),
    ):
        manifest = getattr(pack, "manifest", None)
        records = getattr(manifest, "extractors", None) or []
        for record in records:
            if not _is_l5_graph_extractor_record(record.name):
                continue
            if record.status == "ok":
                continue
            family = record.name.split(":", 1)[0]
            # Finding (review, P2): a real producer (``inline_graph_fold.py``'s
            # ``fold_call_graph``/``fold_type_graph``/... family) stamps
            # ``status="ok" if added else "partial"`` on its own
            # ExtractorRecord -- so a pass that ran over the *whole* project,
            # hit no per-TU diagnostics, and simply found zero edges to add
            # (a legitimately edge-free project: no calls/overrides/templates/
            # etc. to discover) still records "partial" here, even though the
            # SAME call also set ``extractor_passes[family] = True``
            # (confirmed full coverage) or, for a confirmed-narrowed run,
            # ``narrowed_passes[family] = True`` -- both are the pass's own,
            # stronger "this genuinely ran to completion" signal. Treating
            # every non-"ok" record as a shortfall overrides that signal and
            # would fail a simple, fully-examined project under
            # --require-complete-analysis for finding nothing to report. A
            # record whose family was NOT separately confirmed this way (a
            # real crash/missing-tool "failed", or any other case the two
            # confirmation dicts don't cover) still folds into ``degraded``
            # below -- only the confirmed-complete-or-confirmed-narrowed,
            # zero-edge shape is exempted.
            confirmed = sg is not None and (
                sg.extractor_passes.get(family) is True
                or sg.narrowed_passes.get(family) is True
            )
            if confirmed:
                continue
            degraded = True
            detail = f": {record.detail}" if record.detail else ""
            notes.append(
                f"L5 graph extractor {record.name!r} recorded status "
                f"{record.status!r} on the {side} side{detail}"
            )

    # Finding (review, P1, round 8): everything above checks per-side
    # confirmation in isolation -- it never asks whether old and new agree on
    # WHICH pass families they confirmed. A run comparing an old header-only
    # graph (``header_call_graph``/``header_type_graph`` confirmed complete)
    # against a new build-integrated graph (``call_graph``/``type_graph``
    # confirmed complete) has both sides individually "confirmed complete"
    # under every check above, so this function returned ``"complete"`` --
    # but the two sides confirmed *entirely different* family names. The
    # actual cross-snapshot graph diff only ever compares edges within a
    # shared family (``post_processing_reachability._call_graph_fully_trusted``
    # looks up the literal keys ``"call_graph"``/``"type_graph"`` on *each*
    # side independently, and ``source_diff``'s own family-scoped comparisons
    # are the same shape) -- when the confirmed family sets don't overlap at
    # all, there is no family left for that diff to actually compare on both
    # sides, so every edge on both sides goes unexamined against its
    # counterpart despite each side individually looking complete. Detect
    # this by comparing the two sides' own confirmed-family sets (the same
    # ``extractor_passes``/``narrowed_passes`` dicts already read above) and
    # report a total mismatch as ``"unknown"`` rather than ``"complete"`` --
    # folding into ``status="partial"`` under
    # ``--require-complete-analysis`` the same way the other coverage gaps in
    # this function already do, since neither side's individually-confirmed
    # coverage translates into anything the graph diff could actually use.
    #
    # Finding (review, P1, round 9): the fix above only fired on a
    # *disjoint* pair -- ``isdisjoint()`` is true only when the two sets
    # share NO member at all. It missed the partially-overlapping case: old
    # confirmed ``{"call_graph", "type_graph"}``, new confirmed only
    # ``{"call_graph"}``. The two sets are not disjoint (they share
    # ``call_graph``), so the old check left ``graph_completeness`` at
    # whatever the per-side loop above computed (``"complete"`` when nothing
    # else tripped) -- but ``buildsource/source_graph_findings.py`` only
    # ever trusts a family when it is confirmed on BOTH sides (see e.g.
    # ``_family_confirmed``/``_common_dependency_edge_kinds`` in that
    # module), so ``type_graph`` -- confirmed on old, never even attempted
    # on new -- is silently skipped for this comparison exactly the same way
    # a fully-disjoint pair is, just for one family instead of all of them.
    # A subset relationship is still a real coverage gap: whatever family is
    # in one side's confirmed set but not the other's was never examined on
    # both sides, and the cross-snapshot diff cannot compare it. Fixed by
    # comparing the two sets for *any* inequality (``!=``) rather than only
    # a total disjunction -- a subset, superset, or partial-overlap pair are
    # all "the two sides disagree on what they confirmed" just as much as a
    # fully disjoint pair is, and each reports which family/families are
    # confirmed on only one side.
    old_confirmed_families = {
        name for name, ok in (old_graph.extractor_passes or {}).items() if ok
    } | {name for name, ok in (old_graph.narrowed_passes or {}).items() if ok}
    new_confirmed_families = {
        name for name, ok in (new_graph.extractor_passes or {}).items() if ok
    } | {name for name, ok in (new_graph.narrowed_passes or {}).items() if ok}
    asymmetric_family_coverage = False
    if (
        old_confirmed_families or new_confirmed_families
    ) and old_confirmed_families != new_confirmed_families:
        # Finding (P2 review): exclude a family genuinely inapplicable on the side lacking confirmation.
        _cap = _CONDITIONALLY_APPLICABLE_FAMILIES

        def _gap(a: set[str], b: set[str], other: SourceGraphSummary) -> set[str]:
            return {f for f in a - b if _cap.get(f) is None or _cap[f](other)}

        only_old = _gap(old_confirmed_families, new_confirmed_families, new_graph)
        only_new = _gap(new_confirmed_families, old_confirmed_families, old_graph)
        one_sided = sorted(only_old | only_new)
        asymmetric_family_coverage = bool(one_sided)
        if one_sided:
            notes.append(
                f"graph completeness unknown: {one_sided!r} family/families are "
                f"confirmed on only one side (old={sorted(old_confirmed_families)!r}, "
                f"new={sorted(new_confirmed_families)!r}) -- only a family both sides "
                "cover is trusted (buildsource/source_graph_findings.py)"
            )

    if degraded:
        return "degraded", notes
    if asymmetric_family_coverage:
        return "unknown", notes
    if narrowed:
        return "narrowed", notes
    if unknown_pass_coverage:
        return "unknown", notes
    return "complete", notes
