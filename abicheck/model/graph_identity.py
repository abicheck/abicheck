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

"""L5 decl/type node-id normalization and construction (ADR-031/ADR-048).

Split out of ``graph_facts.py`` (moved to `abicheck/model/` as part of
ADR-061 Phase 5 item 2's follow-up, keeping this leaf-normalization half
separate to stay under the new-file 800-line production cap). Every caller
in ``graph_facts.py`` that normalizes a node/edge id or attrs value —
``GraphNode``/``GraphEdge.from_dict``, ``ensure_facts_and_resolve`` —
imports these functions from here; ``buildsource/graph_facts.py`` (the flat
compat facade) re-exports them transitively.
"""

from __future__ import annotations

import re
from typing import Any

from ..name_classification import (
    _declaring_header_discriminator,
    _quoted_spans,
    strip_anonymous_type_location,
)

# ── decl/type identity normalization (ADR-031/ADR-048) ─────────────────────
#
# Split out here (rather than kept in source_graph.py, where the choke-point
# functions that consume this live) purely to stay under source_graph.py's
# AI-readiness line-count cap -- see this module's own docstring for the
# established precedent of moving content here for exactly that reason.

#: Same location-shaped text :func:`strip_anonymous_type_location` strips,
#: but *without* the leading ``(`` that function anchors on. clang's own
#: declaration-name spelling for a lambda closure's implicit record (as
#: opposed to the *type* printer's ``"(lambda at ...)"`` qualType spelling
#: `strip_anonymous_type_location` targets) has been observed reaching the
#: L5 source-graph pipeline bare -- e.g. a ``SourceEntity.identity()``/
#: ``qualified_name`` of exactly ``"lambda at /a/foo.hpp:4:37"``, no
#: wrapping parens at all -- so a real fix here needs both shapes covered,
#: not just the parenthesized one `strip_anonymous_type_location` already
#: handles. Anchored the same way (``\b`` word boundary before the marker)
#: to avoid rewriting unrelated text that merely contains the substring
#: "at" followed by something colon-shaped.
#:
#: The path group is a negative-lookahead-guarded ``.*`` -- greedy, but
#: refusing to consume across a *later* marker's own ``lambda at``/``unnamed
#: <kind> at`` trigger -- rather than plain non-greedy ``.*?`` (Codex review,
#: fresh evidence): a non-greedy path group stops at the FIRST ``:\d+:\d+``
#: it can find, which is wrong the moment the checkout path itself contains
#: a colon-digit-colon-digit-shaped segment (a timestamped build directory,
#: ``/tmp/build-2026T12:34:56/src/foo.hpp:4:37``) -- it silently keeps the
#: real, checkout-dependent tail (``/src/foo.hpp:4:37``) unmodified past the
#: truncated match. A plain greedy ``.*`` fixes that (it finds the
#: *rightmost* valid ``:\d+:\d+``) but then over-matches a string embedding
#: *two* markers (a quoted lookalike alongside a real one, or two real
#: markers), swallowing both into one match. The lookahead guard gets both
#: right: greedy within one marker's own text, but bounded at the next
#: marker's trigger.
#:
#: The path group also excludes the ``"`` character outright, not just via
#: the "inside quotes" guard in :func:`_strip_bare_anonymous_type_location`
#: (Codex review, fresh evidence): that guard only checks where a *match*
#: **starts**, not whether its greedy tail wanders *into* a later quoted
#: span it never started inside of. A real, unquoted marker followed later
#: in the same string by an unrelated quoted coordinate-shaped literal --
#: e.g. ``Wrapper<lambda at /a/foo.hpp:4:37, Tag<"/literal/path:9:9">>`` --
#: has no second ``lambda at``/``unnamed <kind> at`` trigger for the
#: negative lookahead above to bound against, so the greedy path group would
#: otherwise consume straight through the real marker's own terminator and
#: into the quoted literal, matching the quoted text's own ``:9:9`` instead
#: of the real ``:4:37``. Excluding ``"`` from the path characters bounds
#: the greedy match at the opening quote it would otherwise cross into --
#: the real terminator (a valid ``:\d+:\d+\b`` immediately before that
#: point) is still found correctly by the same backtracking the timestamped-
#: directory case above already relies on, since nothing between the
#: marker's own trigger and the first ``"`` can accidentally look like a
#: *second*, later ``:\d+:\d+`` occurrence unless the checkout path itself
#: contains a literal double-quote character -- an accepted, documented
#: edge case matching this whole heuristic's existing "approximate,
#: degrade rather than risk a wrong merge" discipline (see this module's
#: own docstring).
#: **Accepted, deliberately-unfixed limitation** (Codex review, fresh
#: evidence, fourth round on this same regex): the negative lookahead exists
#: to bound a marker's greedy path group at a *later, real* marker's own
#: trigger -- but it cannot distinguish that from a checkout path whose own
#: directory name is, by pure textual coincidence, spelled exactly like a
#: trigger (e.g. a directory literally named ``lambda at``, as in
#: ``lambda at /tmp/lambda at build/foo.hpp:4:37``). For that shape the
#: lookahead treats the coincidental path segment as a second marker
#: boundary, the outer match fails to find any valid ``:\d+:\d+`` within its
#: bounded region, and ``re.sub`` falls through to matching the *inner*
#: "lambda at build/..." text instead -- leaving the real, checkout-
#: dependent ``/tmp/`` prefix completely untouched. Unlike the two prior
#: rounds this regex has already been hardened against (a timestamped build
#: directory, ADR-031/ADR-048's own real-world CI convention; a quoted C++20
#: fixed-string NTTP literal, real language syntax), a directory component
#: that spells the literal English words "lambda at"/"unnamed <kind> at" is
#: not a realistic checkout-root or build-directory naming convention any
#: real toolchain produces -- it requires the marker text itself to be
#: reproduced verbatim as a path segment, which is adversarial construction,
#: not a plausible input this heuristic needs to defend against. Closing it
#: soundly would need the lookahead to distinguish "this is the START of a
#: declaration-shaped marker" from "this text merely spells the same words"
#: -- i.e. real declaration-syntax awareness a regex over free-form path
#: text cannot express, the same fundamental ceiling this whole heuristic
#: already operates under (see this module's own docstring: "approximate,
#: degrade rather than risk a wrong merge"). Left as a documented residual
#: rather than attempted a fourth time under continued review pressure, per
#: this repository's own "known gaps over risky reactive patches"
#: convention (root ``AGENTS.md``) -- pinned by
#: ``test_bare_marker_normalization_known_limitation_marker_text_in_path``
#: rather than silently left unrecorded.
#:
#: ``anonymous\s+\w+`` (fresh evidence, real corpus): a real L5 graph node
#: was observed spelled bare as ``"anonymous union at /abs/.../x.h:649:9"``
#: -- a legitimate anonymous-tag vocabulary this regex's original
#: ``lambda``/``unnamed <kind>`` alternation didn't cover, so the whole
#: match failed and the checkout-dependent absolute path survived straight
#: into the node id: two builds of the identical, unedited declaration
#: produced two different ids, read downstream as a spurious
#: ``declaration_renamed``.
_BARE_ANON_TYPE_LOCATION_RE = re.compile(
    r"\b(lambda|unnamed\s+\w+|anonymous\s+\w+)\s+at\s+"
    r"((?:(?!(?:lambda|unnamed\s+\w+|anonymous\s+\w+)\s+at\s)[^\"])*):(\d+):(\d+)\b"
)


def _strip_bare_anonymous_type_location(name: str) -> str:
    """Strip the checkout-dependent directory out of a *bare* (unparenthesized)
    ``lambda at <path>:<line>:<col>``/``unnamed <kind> at <path>:<line>:<col>``
    spelling, mirroring :func:`strip_anonymous_type_location`'s contract
    (keep the declaring header's own basename + ``:<line>:<col>`` as a
    discriminator, drop only the checkout-dependent directory) for the shape
    that function's own paren-anchored regex does not match. See
    :func:`_normalize_graph_identity`.

    A match that falls inside a ``"..."`` quoted literal is left completely
    untouched, mirroring `strip_anonymous_type_location`'s own protection
    (Codex review, fresh evidence): a real anonymous/lambda marker is never
    itself quoted, so a match starting inside quotes can only be ordinary
    string-literal *content* that happens to spell location-shaped text --
    e.g. a C++20 fixed-string NTTP argument like ``Tag<"lambda at
    /a/foo.hpp:1:2">``. Without this guard, two distinct specializations
    quoting *different* paths (``Tag<"lambda at /a/foo.hpp:1:2">`` vs.
    ``Tag<"lambda at /b/foo.hpp:1:2">``) would collapse onto the identical
    normalized identity, fabricating a same-identity collision between two
    genuinely different declarations.
    """
    quoted_spans = _quoted_spans(name)

    def _inside_quotes(pos: int) -> bool:
        return any(start <= pos < end for start, end in quoted_spans)

    def _replace(match: re.Match[str]) -> str:
        if _inside_quotes(match.start()):
            return match.group(0)
        marker, path, line, col = match.groups()
        return f"{marker}:{_declaring_header_discriminator(path)}:{line}:{col}"

    return _BARE_ANON_TYPE_LOCATION_RE.sub(_replace, name)


def _normalize_graph_identity(identity: str) -> str:
    """Strip a checkout-dependent directory out of *identity* before it
    becomes (part of) an L5 decl/type node id (ADR-031/ADR-048).

    A ``SourceEntity``'s ``identity()``/``qualified_name`` falls back to the
    raw declaration spelling clang/castxml emit for an anonymous-tag or
    lambda-closure type -- ``"(unnamed struct at /a/foo.h:56:5)"``,
    ``"raii_guard<(lambda at /a/foo.h:4:37)>"``, or (observed directly in a
    real L5 graph, with no wrapping parens at all) a bare ``"lambda at
    /a/foo.h:4:37"`` -- which embeds an *absolute* path. ``dumper_castxml.py``'s
    L2 header-AST backend already strips the parenthesized form at
    extraction time (see :func:`strip_anonymous_type_location`'s own
    docstring), but nothing under ``abicheck/buildsource/`` did: two builds
    of the identical, unedited declaration under different checkout roots
    produced two different L5 node ids for it, which
    ``graph_reconcile``/``diff_source_graph`` then read as a real rename
    (``declaration_renamed``) purely from directory taint. ``source_graph.
    _decl_node_id``/``_type_node_id`` are the one choke point every producer
    in ``abicheck/buildsource/`` (``type_graph.py``, ``call_graph.py``,
    ``override_graph.py``, ``callback_graph.py``, ``template_graph.py``,
    ``header_graph.py``, ``macro_graph.py``, ``source_graph.py`` itself)
    routes a decl/type identity through -- for both a node's own id and
    every edge endpoint naming it -- so normalizing here closes the whole
    class at once rather than one producer at a time. Both the
    parenthesized (L2-style) and bare shapes are stripped, since only
    real-world evidence -- not either producer's own documented output
    contract -- tells us which one a given decl/type identity actually
    carries by the time it reaches this package.

    **Accepted, pre-existing residual (Codex review, fresh evidence,
    thirteenth round)**: the marker's own discriminator (basename +
    ``:line:col``, via ``name_classification._declaring_header_discriminator``
    -- see that function's own docstring) cannot distinguish two DIFFERENT
    headers that share both a basename and the identical coordinates (e.g.
    ``include/v1/config.hpp:4:37`` vs. ``include/v2/config.hpp:4:37``). For
    L5 this is a materially sharper consequence than for the pre-existing L2
    caller: ``SourceGraphSummary.add_node`` dedups strictly by id, so a
    genuine collision here merges two structurally distinct declarations
    into one node/edge set, not merely a display-string coincidence. Not
    fixed here: closing it needs real, checkout-relative path information
    (a known project/source root to relativize against) that a pure
    string-pattern normalizer has no way to obtain -- the identical
    structural ceiling ``_declaring_header_discriminator``'s own docstring
    already accepts for its L2 caller, and this module reuses that same
    primitive rather than inventing a second, differently-limited one. A
    fix would be a real, cross-cutting redesign of a primitive shared with
    the L2 backend, not a scoped change to this L5-specific caller.

    Fast-pathed on a plain ``"at" in identity`` check (Codex review,
    fresh evidence -- a real header-graph attach-cost CI regression,
    ~31% at 400 declarations/castxml, traced to this function running on
    every id/label/attrs pair for every decl/type node several times
    over, across the several rounds this whole mechanism accreted through
    -- not a hypothetical). Both underlying regexes require the literal
    substring ``"at"`` (``strip_anonymous_type_location``'s own
    ``\\bat\\s+...``, this module's own ``\\s+at\\s+...`` bare-marker
    pattern) immediately before the location text, so a string that
    doesn't contain it at all cannot match either one -- a cheap,
    substring-only necessary condition with no regex engine involved,
    correct for every input (never skips a string that could actually
    match), not just the realistic ones. Ordinary identifiers containing
    "at" as a substring (``"Category"``, ``"static_cast"``) still pay
    the full regex cost -- this only eliminates the (typically dominant)
    fraction of identities with no "at" substring anywhere.
    """
    if "at" not in identity:
        return identity
    return _strip_bare_anonymous_type_location(strip_anonymous_type_location(identity))


#: ``attrs`` keys carrying a raw declaration/qualified-name spelling that can
#: embed the identical checkout-dependent anonymous/lambda location marker a
#: decl/type node's own ``label``/``id`` already get normalized against
#: (Codex review, fresh evidence). ``entity_identity.resolve_identity_for_node``
#: (ADR-048's canonical-identity resolver) prefers ``attrs["name"]``/
#: ``attrs["qualified_name"]`` over ``node.label`` when both are present, so
#: normalizing only the label left the richer ADR-048 identity computed from
#: these attrs still checkout-tainted -- two nodes with an identical,
#: normalized id/label could still resolve to two different canonical ids
#: purely from directory taint surviving in ``attrs``, and reloading a pack
#: (``SourceGraphSummary.from_dict``'s ``resolve_entities()`` rebuild) would
#: silently reintroduce the taint into a freshly-computed ``EntityResolver``
#: even after ``EntityResolver.from_dict``'s own ``remap_node_ids`` had
#: already cleaned the *persisted* aliases.
_IDENTITY_ATTR_KEYS = ("name", "qualified_name")


def _normalize_identity_attrs(attrs: dict[str, Any]) -> None:
    """Normalize every :data:`_IDENTITY_ATTR_KEYS` string value in *attrs* in
    place, the same way :func:`_normalize_graph_identity` normalizes a
    decl/type node's own ``label``/``id``. A no-op for any other key, and for
    a value that isn't a non-empty string (an absent/None/non-str attr is
    left exactly as a producer supplied it -- never fabricated).
    """
    for key in _IDENTITY_ATTR_KEYS:
        value = attrs.get(key)
        if isinstance(value, str) and value:
            attrs[key] = _normalize_graph_identity(value)


#: ``_normalize_graph_identity`` is deliberately blind to *why* it might
#: match -- it only ever touches an embedded anonymous/lambda location
#: marker, which is safe for a real decl/type identity. But applied
#: unconditionally to *every* graph node id/label regardless of kind (Codex
#: review, fresh evidence, sixth round), a non-declaration id that happens
#: to spell marker-shaped text -- e.g. a ``source://`` node whose path
#: literally contains ``"lambda at ...:1:2"`` -- would be silently rewritten
#: too, changing a node's id/label/``graph_id`` with no directory taint
#: involved at all. Every caller that applies the normalization to an
#: arbitrary node/edge string (as opposed to a value already known to be a
#: decl/type identity, like ``_decl_node_id``'s own argument) gates on this
#: first, restricting the effect to the id-space this whole mechanism exists
#: for: every node kind whose own id is genuinely built from a declaration/
#: type spelling that can embed the marker, not merely a filesystem path or
#: an opaque symbol/flag name. ``template_decl://``/``template_instantiation://``
#: joined ``decl://``/``type://`` here (Codex review, twelfth round, fresh
#: evidence): a class template instantiated with a lambda-closure-typed
#: argument has clang print that argument's spelling as the identical
#: ``"(lambda at <path>:<line>:<col>)"`` marker
#: :func:`~abicheck.buildsource.template_graph._instantiation_label` then
#: folds straight into ``template_instantiation://``'s own node id --
#: confirmed against real clang output, the same false rename this whole
#: mechanism exists to close, just reached from a template-instantiation
#: node instead of a bare decl/type one.
_DECL_OR_TYPE_ID_PREFIXES = (
    "decl://",
    "type://",
    "template_decl://",
    "template_instantiation://",
    "vtable://",
)


def _is_decl_or_type_node_id(node_id: str) -> bool:
    return node_id.startswith(_DECL_OR_TYPE_ID_PREFIXES)


def _normalize_if_decl_or_type(node_id: str) -> str:
    """``_normalize_graph_identity(node_id)``, gated by
    :func:`_is_decl_or_type_node_id` -- the shared shape every load-time
    migration call site (``GraphNode``/``GraphEdge.from_dict``) uses so a
    ``source://``/``header://``/... id can never be mistaken for a decl/type
    one just because it happens to spell marker-shaped text.
    """
    return (
        _normalize_graph_identity(node_id)
        if _is_decl_or_type_node_id(node_id)
        else node_id
    )


def _decl_node_id(identity: str) -> str:
    return f"decl://{_normalize_graph_identity(identity)}"


def _type_node_id(identity: str) -> str:
    return f"type://{_normalize_graph_identity(identity)}"


#: Matches the ``<marker>:<basename>:<line>:<col>`` shape
#: :func:`_normalize_graph_identity` already produces (both the
#: parenthesized and bare closure/anonymous-tag spellings converge on this
#: one form once normalized -- see its own docstring), capturing just the
#: marker so a substitution can drop the trailing basename/coordinates
#: entirely rather than merely relocating them. Deliberately runs on
#: *already-normalized* text rather than duplicating the quote/timestamp-
#: aware machinery `_normalize_graph_identity`'s own regexes already carry.
_NORMALIZED_CLOSURE_DISCRIMINATOR_RE = re.compile(
    r"\b(lambda|unnamed\s+\w+|anonymous\s+\w+):[^():\"]*:\d+:\d+\b"
)


def closure_location_free_identity(identity: str) -> str:
    """*identity*, with every closure/anonymous-tag marker's basename and
    ``:<line>:<col>`` discriminator dropped entirely, leaving just the bare
    marker (``"(lambda at /a/foo.h:4:37)"`` /
    ``"lambda at /a/foo.h:4:37"`` -> ``"(lambda)"`` / ``"lambda"``).

    :func:`_normalize_graph_identity` (used for a node's own id/label, and
    for identity ATTRS a rename/move candidate is matched *against*)
    deliberately *keeps* the basename+``:line:col`` discriminator --
    dropping it there would let two unrelated lambdas declared in the same
    header collide onto one node id, silently merging two distinct
    declarations (see that function's own docstring, and
    ``strip_anonymous_type_location``'s: "there is no location-based
    discriminator that is simultaneously stable under unrelated line
    movement AND distinguishing between two declarations in one header").

    This function is for a *different*, narrower job where that collision
    risk does not apply: :func:`~abicheck.buildsource.graph_reconcile.
    _classify_outcome` decides whether an *already-matched* pair (matched
    by :mod:`~abicheck.buildsource.graph_reconcile`'s canonical-id/alias/
    structural-context tiers -- none of which use this function, so no new
    merge is ever made on its evidence) should be labeled a genuine
    "rename" or not. A pure coordinate shift on an otherwise-identical
    closure/anonymous-tag spelling -- the common case when an unrelated
    edit elsewhere in the same header moves an unchanged lambda to a new
    line -- is not, in any user-meaningful sense, a "declaration renamed":
    it is source churn the closure's own identity has no way to be stable
    against (a lambda has no user-given name to begin with). Reporting it
    as ``declaration_renamed`` anyway is misleading noise, observed at real
    scale in a template/lambda-heavy corpus (oneTBB) where an unrelated
    header edit shifts dozens of otherwise-unchanged closures at once.

    Dropping the basename here too (not just ``:line:col``) is deliberate
    and still safe: a lambda that genuinely moved to a different declaring
    FILE is separately caught by :func:`~abicheck.buildsource.
    graph_reconcile._classify_outcome`'s own file-based ``moved`` check
    (real declaring-file evidence, never derived from this marker text) --
    so collapsing the basename here only changes such a pair's outcome from
    the misleading ``declaration_renamed`` to the more accurate
    ``declaration_moved``, never hides the file change itself.

    A no-op for any identity carrying no closure/anonymous-tag marker at
    all (ordinary qualified names are returned unchanged), and idempotent
    (re-applying to an already-stripped identity is a no-op).

    A match inside a ``"..."`` quoted literal is left untouched, mirroring
    :func:`_strip_bare_anonymous_type_location`'s own guard (CodeRabbit
    review): ``_normalize_graph_identity`` already protects quoted spans, so
    marker-shaped *content* -- a C++20 fixed-string NTTP argument like
    ``Tag<"lambda:foo.h:1:2">`` -- can reach here verbatim. Without this
    guard, two distinct specializations quoting different literal text would
    collapse onto the same coordinate-free identity, silently reconciling a
    genuine ``declaration_renamed`` as unchanged.
    """
    if "at" not in identity and ":" not in identity:
        return identity
    normalized = _normalize_graph_identity(identity)
    if ":" not in normalized:
        return normalized
    quoted_spans = _quoted_spans(normalized)

    def _replace(match: re.Match[str]) -> str:
        if any(start <= match.start() < end for start, end in quoted_spans):
            return match.group(0)
        return match.group(1)

    return _NORMALIZED_CLOSURE_DISCRIMINATOR_RE.sub(_replace, normalized)


def closure_marker_files(identity: str) -> tuple[str, ...]:
    """The declaring-file *basename* of every closure/anonymous-tag marker
    in *identity*, **in source order** (``"w<(lambda at /a/foo.h:4:37)>"``
    -> ``("foo.h",)``); empty for an identity carrying no marker.

    The complement of :func:`closure_location_free_identity`: that function
    drops the basename along with the ``:line:col`` discriminator, and its
    own docstring justifies dropping the basename on the grounds that a
    declaration which genuinely moved to a different declaring FILE is
    "separately caught by ``_classify_outcome``'s own file-based ``moved``
    check". That argument holds only while such file evidence exists --
    real header-graph nodes frequently carry none (PR #1228), and then a
    marker spelling ``old.h`` on one side and ``new.h`` on the other is
    *positive* evidence of a cross-file move that collapses to nothing.
    This function recovers exactly that evidence, so the move check can
    read it from the same place the coordinate shift itself is read from.

    Normalizes ONCE through :func:`_normalize_graph_identity` and then
    scans the single normalized marker form, rather than running one scan
    per accepted spelling and concatenating (Codex review, PR #1229): two
    independent scans return their matches grouped by which regex found
    them, not in source order, so a mixed-spelling identity
    (``Pair<(lambda at a.h:1:2),(lambda:b.h:3:4)>``) compared unequal
    against the same two markers written in the other order -- and the raw
    form's own path group could run greedily through a following
    normalized marker, capturing ``a.h:1:2),(lambda:b.h`` as a "basename".
    Order matters because the comparison is positional: marker *i* on the
    old side is marker *i* on the new side.

    Basenames, not paths: two independently-rooted checkouts spell the same
    unmoved header differently (``/old/checkout/foo.h`` vs
    ``/new/checkout/foo.h``), and comparing those would manufacture a move
    out of a checkout root. The cost is the documented residual that a move
    keeping the same basename across directories stays invisible here --
    the same limitation ``graph_reconcile_outcome.coordinate_evidence``
    already records.
    """
    normalized = _normalize_graph_identity(identity)
    # Skip a match inside a ``"..."`` quoted literal, exactly as
    # :func:`closure_location_free_identity` does (Codex review, PR #1229):
    # a quoted C++ fixed-string NTTP spelling marker-shaped text is source
    # *content*, not a declaring location. Without this, that function
    # rightly reads `Tag<"lambda:a.h:1:2">` -> `Tag<"lambda:b.h:3:4">` as a
    # genuine rename while this one called it a move, and the pair reported
    # the combined outcome for a location change that never happened. The
    # two functions are complements over the same markers, so they must
    # agree on which text IS a marker.
    quoted_spans = _quoted_spans(normalized)
    return tuple(
        _basename(match.group(0).rsplit(":", 3)[-3])
        for match in _NORMALIZED_CLOSURE_DISCRIMINATOR_RE.finditer(normalized)
        if not any(start <= match.start() < end for start, end in quoted_spans)
    )


def _basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()
