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

"""ADR-048 D2's reconciliation *outcome* vocabulary and its classifier.

One responsibility, lifted out of ``graph_reconcile.py`` (which owns the
*matching* -- which old node pairs with which new one -- and sat at its
``architecture/debt.yaml`` no-growth baseline): given a matched pair's two
:class:`CanonicalIdentity` values, which of the four outcomes does the
evidence actually support? The declaring-path normalization the answer
rests on (:func:`_project_relative_path`) comes along, since the classifier
is its only semantic consumer; ``graph_reconcile`` imports it back for its
own node-identity paths.

A leaf: nothing here reaches back into the matching module.
"""

from __future__ import annotations

from collections import Counter

from ..model.graph_identity import (
    closure_location_free_identity,
    closure_marker_locations,
    path_basename,
)
from .entity_identity import CanonicalIdentity

#: Which provider carried the *location* half of an
#: :data:`OUTCOME_COORDINATES_ONLY` classification. ``declaring_file`` is a
#: second, independent provider agreeing the declaring file did not change;
#: ``qualified_name`` means the coordinate shift was read out of the
#: qualified name itself and NO declaring-file evidence existed on either
#: side -- the weaker of the two, and the one a reader should see stated
#: rather than inferred (Codex review, PR #1228: a same-basename
#: cross-directory move is indistinguishable from a pure coordinate shift
#: on name evidence alone).
COORDINATE_EVIDENCE_DECLARING_FILE = "declaring_file"
#: Exactly one side recorded a declaring file. Distinct from
#: :data:`COORDINATE_EVIDENCE_QUALIFIED_NAME` because "neither side records
#: one" and "one side's extraction lost it" are different gaps, and the
#: finding's own text said the former for both until this was split out
#: (Codex review, PR #1228).
COORDINATE_EVIDENCE_PARTIAL_DECLARING_FILE = "partial_declaring_file"
COORDINATE_EVIDENCE_QUALIFIED_NAME = "qualified_name"


#: Reconciliation outcomes (ADR-048 D2) — distinct from plain
#: node-add/node-remove, so a consumer can tell "the same entity, under a
#: new name/location" from "an unrelated add and an unrelated remove that
#: happen to be in the same diff".
OUTCOME_RENAMED = "declaration_renamed"
OUTCOME_MOVED = "declaration_moved"
OUTCOME_RECONCILED = "declaration_identity_reconciled"
OUTCOME_COORDINATES_ONLY = "declaration_coordinates_shifted"  # neither predicate fired -- distinct from OUTCOME_RECONCILED, where both did

#: OUTCOME_COORDINATES_ONLY-eligible kinds -- excludes "source_decl": a
#: function's signature isn't exposed to resolve_identity_for_node in
#: production, so "nothing else changed" can't be proven for one.
_COORDINATE_ONLY_KINDS: frozenset[str] = frozenset(
    {"record_type", "enum_type", "typedef"}
)


def _path_segments(path: str) -> tuple[str, ...]:
    """Plain-path split into segments, ignoring the root/self markers.

    Normalizes ``\\`` to ``/`` first (same as
    ``source_graph_findings._path_segments``/``source_graph.py``'s own
    caller-file normalization): ``PurePosixPath`` treats a backslash as an
    ordinary filename character, not a separator, so a Windows-style
    declaring path (``C:\\old\\include\\api.h``) would never be split at
    all -- silently defeating the project-root-marker search in
    :func:`_project_relative_path` and comparing raw checkout roots
    (Codex review).
    """
    from pathlib import PurePosixPath

    posix = path.replace("\\", "/")
    return tuple(p for p in PurePosixPath(posix).parts if p not in ("/", ".", ""))


#: Conventional project-root directory names — a superset of
#: :data:`abicheck.header_utils._INCLUDE_ROOT_NAMES` (which only needs
#: ``include``/``inc`` for its narrower include-root-inference purpose;
#: this also covers ``src``/``source``/``sources`` layouts). Used here as an
#: anchor for stripping a checkout-root prefix from a single declaring-file
#: path with no sibling to derive a shared prefix from (Codex review — see
#: :func:`_project_relative_path`).
_CONVENTIONAL_ROOT_MARKERS: frozenset[str] = frozenset(
    {"include", "inc", "src", "source", "sources"}
)


def _project_relative_path(path: str) -> str:
    """Best-effort project-relative form of a declaring-file/header path.

    Two independently-rooted checkouts of the same tree (separate temp dirs
    in a benchmark harness, or two CI job workspaces) share no absolute
    root, so comparing raw absolute paths would misclassify an unmoved file
    as "moved" purely because of where its tree happened to be checked out.

    With more than one declaring file on a side, the shared checkout-root
    prefix could in principle be derived structurally (comparing multiple
    paths against each other) — but a single sample gives no such baseline,
    and blindly reserving "everything but the basename" as an assumed
    checkout root (an earlier version of this function did that) silently
    hides a real cross-directory move that happens to keep the same
    filename (Codex review: ``/tmp/old/src/foo.h`` -> ``/tmp/new/include/foo.h``
    must not read as unmoved). Anchoring on the last conventional root-marker
    segment instead (``include``/``inc``/``src``/``source``/``sources`` — the
    same vocabulary :data:`abicheck.header_utils._INCLUDE_ROOT_NAMES` already
    uses for a similar purpose) gets both cases right without needing a
    second sample: it strips the checkout-root prefix when a recognizable
    project-layout marker is present, and falls back to comparing the full
    path (never silently "unmoved") when it isn't.
    """
    if not path:
        return path
    segs = _path_segments(path)
    for i in range(len(segs) - 1, -1, -1):
        if segs[i].lower() in _CONVENTIONAL_ROOT_MARKERS:
            return "/".join(segs[i:])
    return "/".join(segs)


def _signature_tail(identity: CanonicalIdentity) -> str:
    # normalized_signature's kind/arity/param-types tail, qn field stripped
    # off (that field alone carries coordinate churn) -- comparable across
    # a coordinate shift. Format: "sig:" + qn + "\x1f" + kind + "\x1f" + ...
    return identity.normalized_signature.split("\x1f", 1)[-1]


def _declaration_marker_is_among_the_changed(
    declaring_file: str,
    own_files: list[tuple[str, str]],
    other_files: list[tuple[str, str]],
) -> bool:
    """Whether the marker a recorded *declaring_file* identifies is itself
    one of the markers that CHANGED between the two sides.

    Vacuously true when either the file or the markers are absent -- no
    recorded file means nothing contradicts the markers, and no marker
    means there is nothing to read.

    Otherwise the recorded file must appear in this side's own share of
    the marker difference (Codex review, PR #1229). Requiring merely that
    SOME marker name the recorded file is too weak once a declaration
    carries several: `Wrapper<(lambda at wrapper.h:1:2),(lambda at
    nested_old.h:3:4)>` declared in `include/wrapper.h` satisfies that
    through its unchanged `wrapper.h` marker, so an unrelated change to
    the nested one then read as the wrapper moving. The declaration's own
    marker is the only one that can say the DECLARATION moved; every other
    marker describes a nested template argument.
    """
    if not declaring_file or not own_files:
        return True
    base = path_basename(declaring_file)
    matching = [marker for marker in own_files if marker[1] == base]
    if len(matching) != 1:
        # Ambiguous rather than absent (Codex review, PR #1229): several
        # markers naming the recorded file give no way to tell WHICH one
        # is the declaration's, so one of them changing is equally
        # consistent with an unmoved declaration whose nested argument
        # moved. `W<(lambda at wrapper.h:1:2),(lambda at wrapper.h:3:4)>`
        # declared in `include/wrapper.h` is exactly that. Answering False
        # withholds the move claim; `markers_differ` stays true, so the
        # real difference is still reported.
        return False
    changed = Counter(own_files) - Counter(other_files)
    return any(marker_base == base for _kind, marker_base in changed.elements())


def _classify_outcome(
    old_identity: CanonicalIdentity,
    new_identity: CanonicalIdentity,
    *,
    old_declaring_file: str = "",
    new_declaring_file: str = "",
) -> str:
    old_qn = old_identity.qualified_name
    new_qn = new_identity.qualified_name
    # source_relative is file#scope#name; the file prefix says "did the
    # declaring file change" (falls back to _declaring_files' edge-derived
    # file when the node carries none of its own).
    old_file = (
        _project_relative_path(old_identity.source_relative.split("\x1f", 1)[0])
        or old_declaring_file
    )
    new_file = (
        _project_relative_path(new_identity.source_relative.split("\x1f", 1)[0])
        or new_declaring_file
    )
    old_key = closure_location_free_identity(old_qn)
    new_key = closure_location_free_identity(new_qn)
    renamed = bool(old_qn) and bool(new_qn) and old_key != new_key
    # The marker fallback closes the hole dropping the declaring-file gate
    # would otherwise open (Codex review, PR #1228, fresh evidence beyond
    # the same-basename ambiguity): closure_location_free_identity drops
    # each marker's basename as well as its :line:col, and its own
    # docstring justifies that by saying a genuine cross-FILE move "is
    # separately caught by _classify_outcome's own file-based moved check"
    # -- an argument that holds only while such evidence exists. With none
    # recorded, "(lambda at old.h:1:2)" vs "(lambda at new.h:9:9)"
    # collapses to nothing and a real move reads as a compatible
    # coordinate-only shift. Read from the same place the coordinate shift
    # is read from: differing marker basenames ARE the location evidence,
    # exactly as agreeing ones are. Marker counts always match here --
    # equal location-free keys imply it, since the key collapses every
    # marker to its bare form.
    #
    # Marker evidence is a FALLBACK, not an override (Codex review, PR
    # #1229): when both sides record a declaring file, that is the
    # stronger, two-sided evidence about where the DECLARATION lives, and
    # a marker inside the name may describe a nested template argument's
    # location rather than the declaration's own -- `Wrapper<(lambda at
    # old.h:1:2)>` and `Wrapper<(lambda at new.h:9:9)>` both declared in
    # `wrapper.h` did not move. So the marker only speaks when the
    # declaring-file comparison is unavailable.
    #
    # Deliberately NOT gated on `not renamed`: a declaration whose ordinary
    # name AND declaring file both changed is the catalog's combined case,
    # OUTCOME_RECONCILED -- disabling the only available move evidence
    # during a rename emitted it as a bare OUTCOME_RENAMED instead, losing
    # half of what the pair shows. Equal marker COUNTS are what makes the
    # positional comparison meaningful, so the claim is made only when the
    # two sides' markers can be aligned; under an ordinary coordinate shift
    # equal counts are implied by the equal location-free keys, and under a
    # rename they are checked.
    old_markers = closure_marker_locations(old_qn) if old_qn else ()
    new_markers = closure_marker_locations(new_qn) if new_qn else ()
    # A PERMUTATION is not a move (Codex review, PR #1229): reordered
    # template arguments leave every marker naming the file it already
    # named, so the multiset is what carries "some marker now names a
    # different file". The reordering is still a real difference the
    # location-free key collapses, so it disqualifies coordinate-only
    # below -- it is simply not evidence of a *move*.
    # A marker KIND change is not location evidence at all (CodeRabbit
    # review, PR #1229): `X<(lambda at a.h:1:2)>` -> `X<(unnamed struct at
    # a.h:1:2)>` names the same file throughout, and the location-free key
    # keeps the kind, so it is already a rename. Reading the differing
    # `(kind, basename)` pairs as a move on top of that asserted a file
    # change nothing shows. A move means some marker now names a different
    # FILE, which presupposes the markers still correspond -- so the kinds
    # must line up before their basenames mean anything.
    #
    # The two questions read DIFFERENT projections of the same marker
    # tuple, and that is what keeps them from answering each other's
    # (Codex review, PR #1229). A move is about the FILE, so it compares
    # `(kind, basename)` -- including `line`/`col` there would report
    # ordinary coordinate churn as a move. A permutation is about which
    # marker sits where, so it compares all four: two same-kind markers
    # from one header (`Pair<(lambda at same.h:1:2),(lambda at
    # same.h:3:4)>`) have identical `(kind, basename)` pairs, so a swap of
    # them was invisible and the pair claimed "no material identity
    # change" -- while it is the same template-argument reorder a
    # differing basename already reports. Pure coordinate churn stays
    # distinguishable because its discriminators do not merely reorder:
    # the sorted sequences differ too.
    old_files = [(kind, base) for kind, base, _, _ in old_markers]
    new_files = [(kind, base) for kind, base, _, _ in new_markers]
    marker_kinds_align = sorted(kind for kind, _ in old_files) == sorted(
        kind for kind, _ in new_files
    )
    markers_differ = (
        bool(old_markers)
        and len(old_markers) == len(new_markers)
        and marker_kinds_align
        and sorted(old_files) != sorted(new_files)
    )
    # A reorder shows up in EITHER projection, and both have to be asked
    # (Codex review, PR #1229). The full discriminator catches a swap of
    # two markers from one header, where the `(kind, basename)` pairs are
    # identical. The `(kind, basename)` sequence catches a swap that also
    # shifted coordinates -- `Pair<(lambda at a.h:1:2),(lambda at
    # b.h:3:4)>` -> `Pair<(lambda at b.h:7:8),(lambda at a.h:9:10)>` --
    # where the full tuples no longer form the same multiset at all, so
    # the stricter projection alone reported "no material identity
    # change". Pure coordinate churn matches neither: its files sequence
    # does not reorder, and its full multiset differs.
    markers_reordered = bool(old_markers) and (
        (old_files != new_files and sorted(old_files) == sorted(new_files))
        or (old_markers != new_markers and sorted(old_markers) == sorted(new_markers))
    )
    has_two_sided_files = bool(old_file) and bool(new_file)
    # A recorded declaring file that names NONE of its side's markers
    # disproves that those markers describe the declaration (Codex review,
    # PR #1229) -- `Wrapper<(lambda at nested_old.h:1:2)>` declared in
    # `include/wrapper.h` carries a marker for a nested template argument,
    # not for the wrapper. The two-sided case already defers to the files;
    # this is the same rule where only ONE side recorded a file, which is
    # still enough to disprove the fallback: the only known declaration
    # location is unrelated to the marker, so the marker cannot establish
    # that the declaration moved. `markers_differ` stays true, so the pair
    # keeps the evidence-conflict outcome rather than dropping into
    # coordinate-only's "no material identity change" -- the nested
    # argument's own file really did change.
    marker_fallback_usable = _declaration_marker_is_among_the_changed(
        old_file, old_files, new_files
    ) and _declaration_marker_is_among_the_changed(new_file, new_files, old_files)
    moved = (
        (old_file != new_file)
        if has_two_sided_files
        else (markers_differ and marker_fallback_usable)
    )
    if renamed and not moved:
        return OUTCOME_RENAMED
    if moved and not renamed:
        return OUTCOME_MOVED
    if renamed and moved:
        return OUTCOME_RECONCILED
    # Neither fired: coordinate-only needs the raw name differed (normalized
    # equal), an agreeing signature tail, AND a type-shaped kind -- a real
    # source_decl producer tracks no param_types/mangled_name (Codex
    # review), so a function's tail is vacuously equal and can't prove
    # nothing else changed; only a type has no such hidden dimension.
    same_sig = _signature_tail(old_identity) == _signature_tail(new_identity)
    # bool(old_qn)/bool(new_qn) mirror renamed's own guard above -- an
    # absent name is a name gain/loss, not coordinate-churn evidence.
    #
    # Deliberately NOT gated on bool(old_file)/bool(new_file). In this
    # population the coordinate shift is embedded in the qualified name
    # itself ("(lambda at global_control.h:172:22)"), and
    # closure_location_free_identity stripping it is precisely what proved
    # the non-coordinate part identical -- the name-embedded coordinate IS
    # the location evidence. Demanding a SOURCE_DECLARES-edge declaring
    # file on top of it asks for a second, independent provider for a fact
    # the first already carries, and real header-graph nodes frequently
    # have none (measured on a oneTBB header-graph comparison: 15 of 15
    # otherwise-eligible record_type pairs were blocked solely by
    # old_file == new_file == "", making this outcome unreachable in
    # production). Crucially the alternative here is not "make no claim":
    # falling through to OUTCOME_RECONCILED asserts the strictly STRONGER
    # "both name and location evidence changed", at severity risk. On
    # absent file evidence `moved` is already False for that same reason,
    # so declining the weak claim only to make the strong one inverts
    # AGENTS.md's "weaker evidence narrows conclusions".
    coordinate_only = (
        bool(old_qn)
        and bool(new_qn)
        and old_qn != new_qn
        and same_sig
        # An evidence conflict is not a coordinate-only shift: two-sided
        # declaring files saying "did not move" while the embedded markers
        # name different files means something beyond coordinates changed
        # (a nested template argument's own declaring file, or a
        # reordering of the arguments themselves), so the pair falls
        # through rather than claiming no material identity change
        # (Codex review, PR #1229).
        and not markers_differ
        and not markers_reordered
        and old_identity.kind in _COORDINATE_ONLY_KINDS
    )
    return OUTCOME_COORDINATES_ONLY if coordinate_only else OUTCOME_RECONCILED


def coordinate_evidence(
    old_identity: CanonicalIdentity,
    new_identity: CanonicalIdentity,
    *,
    old_declaring_file: str = "",
    new_declaring_file: str = "",
) -> str | None:
    """Which provider carried the location half of this pair's
    :data:`OUTCOME_COORDINATES_ONLY` classification, or ``None`` when the
    pair is not classified that way.

    Recorded rather than acted on. Declining to classify a pair as
    coordinate-only for want of a declaring file makes the strictly
    stronger :data:`OUTCOME_RECONCILED` claim instead (see
    :func:`_classify_outcome`), so the honest narrowing is to state which
    evidence the classification rests on and let a reader weigh it -- a
    :data:`COORDINATE_EVIDENCE_QUALIFIED_NAME` pair cannot rule out a
    same-basename cross-directory move, and says so in the emitted
    finding's own description. A *differing* marker basename is not this
    function's problem: :func:`_classify_outcome` already reads that as a
    move, so such a pair never reaches this outcome at all.
    """
    if (
        _classify_outcome(
            old_identity,
            new_identity,
            old_declaring_file=old_declaring_file,
            new_declaring_file=new_declaring_file,
        )
        != OUTCOME_COORDINATES_ONLY
    ):
        return None
    old_file = (
        _project_relative_path(old_identity.source_relative.split("\x1f", 1)[0])
        or old_declaring_file
    )
    new_file = (
        _project_relative_path(new_identity.source_relative.split("\x1f", 1)[0])
        or new_declaring_file
    )
    if old_file and new_file:
        return COORDINATE_EVIDENCE_DECLARING_FILE
    if old_file or new_file:
        return COORDINATE_EVIDENCE_PARTIAL_DECLARING_FILE
    return COORDINATE_EVIDENCE_QUALIFIED_NAME


#: Human-readable outcome descriptions, keyed by :data:`OUTCOME_RENAMED`
#: et al. — used by :func:`diff_graph_reconciliation_findings` below.
_OUTCOME_PROSE: dict[str, str] = {
    OUTCOME_RENAMED: "renamed",
    OUTCOME_MOVED: "moved to a different declaring file",
    OUTCOME_RECONCILED: "identity-reconciled (both name and location evidence changed)",
    OUTCOME_COORDINATES_ONLY: "no material identity change (coordinate-only shift)",
}
