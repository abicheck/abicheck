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

from ..model.graph_identity import closure_location_free_identity
from .entity_identity import CanonicalIdentity

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
    moved = bool(old_file) and bool(new_file) and old_file != new_file
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
        and old_identity.kind in _COORDINATE_ONLY_KINDS
    )
    return OUTCOME_COORDINATES_ONLY if coordinate_only else OUTCOME_RECONCILED


#: Human-readable outcome descriptions, keyed by :data:`OUTCOME_RENAMED`
#: et al. — used by :func:`diff_graph_reconciliation_findings` below.
_OUTCOME_PROSE: dict[str, str] = {
    OUTCOME_RENAMED: "renamed",
    OUTCOME_MOVED: "moved to a different declaring file",
    OUTCOME_RECONCILED: "identity-reconciled (both name and location evidence changed)",
    OUTCOME_COORDINATES_ONLY: "no material identity change (coordinate-only shift)",
}
