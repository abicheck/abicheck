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

"""Shared qualified-name segmentation and versioned inline-namespace helpers.

Leaf module (no imports from the rest of ``abicheck``) so it can be shared
between ``diff_namespaces.py`` (which owns the segment-splitting logic this
module was extracted from) and any other detector that needs to recognize a
versioned inline-namespace segment (``v1``, ``_V2``, ``__1``, ...) without
re-implementing the same regex.

A versioned inline namespace makes the *same* declaration reachable under
two qualified spellings: the full path (``detail::v1::x``) and the
version-elided path (``detail::x``) that unqualified lookup from the
enclosing scope also resolves to. When a header-AST producer surfaces both
spellings as separate top-level declarations, a name-keyed detector that
doesn't canonicalize away the version segment double-reports the identical
change once per spelling.

``diff_namespaces.py``'s function/type detectors use :func:`segments`/
:func:`version_strip_segments` directly, gated on real extraction-data
identity (a shared mangled name, a shared source location) before ever
merging two spellings -- see its own module docstring. There is
deliberately **no** merge helper here for a plain name-keyed mapping like
``AbiSnapshot.constants``: a header constant carries no identity beyond its
own value, and value-equality alone was tried and repeatedly shown
(Codex review, P1, three rounds) to be indistinguishable from coincidence
in both directions -- it can hide a real value divergence between two
unrelated declarations that happen to start equal, and it can also merge
two unrelated declarations that never even coexist in the same snapshot,
each present on only one side. ``diff_symbols._diff_constants`` therefore
compares ``AbiSnapshot.constants`` unmodified; double-reporting a constant
value change once per versioned-namespace spelling is an accepted, documented
limitation (see that function's docstring) rather than a heuristic that
cannot be made sound with the data available today.
"""

from __future__ import annotations

import contextlib as _contextlib
import dataclasses as _dataclasses
import re as _re
import threading as _threading
from collections.abc import (
    Callable as _Callable,
    Iterable as _Iterable,
    Iterator as _Iterator,
    Mapping as _Mapping,
)
from typing import NamedTuple as _NamedTuple, TypeVar as _TypeVar

_SnapshotT = _TypeVar("_SnapshotT")

# Matches segment-name shapes commonly used as a versioned inline
# namespace: ``_V1``, ``__v2``, ``v3``, ``__1``. Anchored to whole
# segment match (caller passes a single segment string). Captures the
# integer suffix for ordering checks.
_VERSION_NS_RE = _re.compile(r"^_{0,2}[Vv]?(\d+)$")


def segments(qualified: str) -> list[str]:
    """Split a qualified C++ name into namespace segments.

    Template arguments are stripped before splitting so that
    ``ns::experimental::sort<int>`` -> ``["ns", "experimental", "sort"]``.
    Operator names containing ``::`` (extremely rare in declared form) are
    not handled specially; this is acceptable because callers only care
    about segment ordering for namespace identification.
    """
    if not qualified:
        return []
    # Fast path: a name with neither a ``::`` separator nor a template ``<``
    # is its own single segment -- the overwhelmingly common case. See
    # diff_namespaces.py's history for why this matters for perf on
    # versioned-symbol libraries with thousands of plain-name findings.
    if "::" not in qualified and "<" not in qualified:
        return [qualified]
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    i = 0
    n = len(qualified)
    while i < n:
        ch = qualified[i]
        if ch == "<":
            depth += 1
            i += 1
            continue
        if ch == ">":
            if depth > 0:
                depth -= 1
            i += 1
            continue
        if depth == 0 and ch == ":" and i + 1 < n and qualified[i + 1] == ":":
            if buf:
                out.append("".join(buf).strip())
                buf = []
            i += 2
            continue
        if depth == 0:
            buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]


def version_suffix(segment: str) -> int | None:
    """Return the integer suffix if *segment* looks like a versioned
    inline-namespace tag (``_V1``, ``__1``, ``v2``, ...); else ``None``.
    """
    m = _VERSION_NS_RE.match(segment)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def version_strip_segments(segs: list[str]) -> tuple[tuple[str, ...], int | None]:
    """Strip any one versioned-*namespace* segment and return
    ``(stripped_segments, version_int)``.

    Returns ``(tuple(segs), None)`` unchanged when no versioned segment is
    present. Only the first matching segment is stripped -- nested
    versioned namespaces are vanishingly rare in practice and the simple
    rule keeps the matching key stable.

    Only scans segments *before* the last one (CodeRabbit review: a
    version-shaped segment can legitimately be the declaration's own leaf
    name, not a namespace -- a constant or type literally named ``v1`` or
    ``v2``). The last segment is always the leaf, never a namespace, so it
    is never a candidate: scanning it could strip the entity's own name
    (corrupting the key to just its enclosing scope) or make two genuinely
    different leaves (``ns::v1``, ``ns::v2``) collide on the same stripped
    key.
    """
    for i, s in enumerate(segs[:-1]):
        v = version_suffix(s)
        if v is not None:
            return tuple(segs[:i] + segs[i + 1 :]), v
    return tuple(segs), None


#: Toolchain *ABI-tag* inline namespaces that are not version-number-shaped
#: and therefore invisible to :func:`version_suffix`: libstdc++'s dual-ABI
#: ``std::__cxx11`` and the Android NDK's ``std::__ndk1``. Both are inline
#: namespaces — transparent for name lookup — so a declaration gaining or
#: losing one is the *same* entity spelled two ways, exactly like the
#: ``v1``/``__1`` family ``version_suffix`` already recognizes.
_ABI_TAG_NS_RE = _re.compile(r"^__(?:cxx|ndk)\d+$")


def is_inline_abi_namespace_segment(segment: str) -> bool:
    """True when *segment* names an inline namespace used as an ABI tag.

    Union of the version-number family (``v1``, ``__1``, ``_V2`` — see
    :func:`version_suffix`) and the named toolchain tags (``__cxx11``,
    ``__ndk1``). Deliberately not a general "looks like an implementation
    detail" test: a segment such as ``detail``, ``impl`` or oneTBB's ``d1``
    is an *ordinary* namespace whose rename is a real move of the
    declarations inside it, so it must not be stripped.
    """
    return version_suffix(segment) is not None or bool(_ABI_TAG_NS_RE.match(segment))


def strip_inline_abi_namespaces(qualified: str) -> tuple[str, ...]:
    """Return *qualified*'s segments with every inline ABI-tag namespace removed.

    Two spellings that reduce to the same tuple name the same entity through
    a transparent inline namespace (``std::basic_string`` vs.
    ``std::__cxx11::basic_string``); two that do not are genuinely different
    scopes (``tbb::detail::d1::graph`` vs. ``tbb::detail::d2::graph``).

    The leaf segment is never stripped, for the same reason
    :func:`version_strip_segments` never scans it: a declaration may
    legitimately *be* named ``v1``, and stripping its own name would collapse
    it onto its enclosing scope.

    Splitting keeps each segment's template arguments, unlike
    :func:`segments`: two spellings that differ only inside an *enclosing*
    segment's argument list (``ns::Outer<int>::Inner`` vs.
    ``ns::Outer<float>::Inner``) name different entities, and dropping the
    arguments would reduce both to the same tuple.
    """
    segs = raw_segments(qualified)
    if len(segs) < 2:
        return tuple(segs)
    leaf = len(segs) - 1
    return tuple(
        s
        for i, s in enumerate(segs)
        if i == leaf or not is_inline_abi_namespace_segment(s)
    )


def raw_segments(qualified: str) -> list[str]:
    """:func:`segments`, but keeping each segment's template arguments.

    Splits on ``::`` at template-nesting depth zero only, so
    ``ns::Map<std::pair<int, int>>::iterator`` yields three segments and the
    ``::`` inside the argument list is not a separator.
    """
    if not qualified:
        return []
    if "::" not in qualified and "<" not in qualified:
        return [qualified]
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(qualified)
    while i < n:
        ch = qualified[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            if depth > 0:
                depth -= 1
        elif depth == 0 and ch == ":" and i + 1 < n and qualified[i + 1] == ":":
            if buf:
                out.append("".join(buf).strip())
                buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append("".join(buf).strip())
    return [s for s in out if s]


# ---------------------------------------------------------------------------
# Anonymous/lambda-closure ordinal identity.
#
# castxml/clang spell a closure type as ``(lambda at <path>:<line>:<col>)``.
# ``name_classification.strip_anonymous_type_location`` already reduces the
# checkout-dependent *path* to just the declaring header's basename, keeping
# ``:<line>:<col>`` as the only discriminator between two distinct lambdas in
# one header -- e.g. ``"raii_guard<(lambda:task_group.h:522:26)>"``. An
# unrelated edit *anywhere earlier* in that header shifts every lambda below
# it to a new line, so an otherwise-unchanged closure then compares as
# removed-plus-added between old/new snapshots -- reported against real
# oneTBB binaries as a spurious ``type_removed``/``type_added`` pair, a
# paired ``func_removed``/``func_added`` on every ctor/dtor of the
# instantiation (castxml's synthetic ctor/dtor keys embed the same owner
# spelling), and a ``declaration_renamed`` RISK finding whose entire content
# is the line-number text.
#
# The functions below replace that ``:<line>:<col>`` discriminator with a
# stable ordinal -- "the Nth lambda of this marker kind declared in this
# header" -- computed once per snapshot, mirroring GCC/DWARF's own per-scope
# ``{lambda(...)#1}`` numbering. As long as an edit doesn't reorder or
# add/remove same-header, same-kind lambdas relative to each other, both
# sides of a comparison assign the identical ordinal to the identical
# closure. Kept in this leaf module (not ``name_classification.py``, which
# already owns ``strip_anonymous_type_location``) purely to stay within
# ADR-061's no-growth debt baseline for that already-frozen file -- see
# ``architecture/debt.yaml``.
# ---------------------------------------------------------------------------

#: Matches the marker prefix :func:`strip_anonymous_type_location` already
#: produces (``"(lambda:"``, ``"(unnamed struct:"``) -- NOT the raw
#: ``at <path>:<line>:<col>`` form that function itself consumes. Only the
#: fixed prefix is a regex; the variable-length basename that follows is
#: scanned manually by :func:`_scan_anon_type_marker` below, since a single
#: regex alternation (``\([^()]*\)``) can only ever balance one level of
#: nesting and fails on a basename with two, e.g. ``foo(a(b)).hpp``
#: (Codex review, fresh evidence).
_ANON_TYPE_MARKER_PREFIX_RE = _re.compile(r"\((lambda|unnamed\s+\w+):")

#: Matches a marker's trailing ``:<line>:<col>`` right before the closing
#: paren :func:`_scan_anon_type_marker` already found -- applied to the text
#: between the prefix and that paren, anchored at the end so a basename that
#: itself contains a colon (legal on POSIX, e.g. ``weird:name.h`` -- the same
#: "known, accepted limitation" shape documented on
#: :func:`collect_anonymous_type_ordinals`) still yields the *rightmost*
#: ``:digits:digits`` as the real discriminator, matching this module's
#: previous (regex-only) behavior for that case.
_ANON_TYPE_TRAILING_LINE_COL_RE = _re.compile(r":(\d+):(\d+)\s*$")


class _AnonTypeMatch(_NamedTuple):
    """One marker occurrence -- a drop-in replacement for the ``re.Match``
    shape this module used before switching to manual scanning, carrying
    only what :func:`collect_anonymous_type_ordinals`/
    :func:`apply_anonymous_type_ordinals` actually read."""

    start: int
    end: int
    marker: str
    header: str
    line: int
    col: int


def _anon_type_match_from_close_paren(
    name: str, prefix_match: _re.Match[str], close_index: int
) -> _AnonTypeMatch:
    body = name[prefix_match.end() : close_index]
    tail = _ANON_TYPE_TRAILING_LINE_COL_RE.search(body)
    assert tail is not None  # caller only invokes this once it has matched
    return _AnonTypeMatch(
        start=prefix_match.start(),
        end=close_index,
        marker=f"({prefix_match.group(1)}",
        header=body[: tail.start()],
        line=int(tail.group(1)),
        col=int(tail.group(2)),
    )


def _scan_anon_type_marker(
    name: str, prefix_match: _re.Match[str]
) -> _AnonTypeMatch | None:
    """Scan forward from *prefix_match* for its balanced-paren-aware basename
    and trailing ``:<line>:<col>)``.

    Tracks paren depth through the basename so any number of nested,
    genuinely-balanced parenthesized groups are correctly matched. A
    depth-0 ``)`` is only a CANDIDATE terminator when the text immediately
    before it ends in ``:<digits>:<digits>`` -- a real compiler-emitted
    basename can also contain an *unmatched* ``)`` of its own (e.g.
    ``foo)bar.hpp``, legal on POSIX), which would otherwise be mistaken for
    the terminator before the real coordinates are ever reached (Codex
    review, fresh evidence). A depth-0 ``)`` that fails that check is
    treated as ordinary basename text and scanning continues.

    The LAST such candidate found while scanning to the end of the string
    is the one returned, not the first (Codex review, fresh evidence): a
    basename can legally contain coordinate-shaped text of its own before
    the real terminator (``foo:1:2)bar.hpp:10:2)``), and stopping at the
    first depth-0 match would then corrupt the marker at ``foo:1:2)``
    instead of assigning an ordinal to the real trailing coordinates at
    the end. Preferring the last candidate is safe against ever running
    past a genuinely separate, later marker: any such marker's own prefix
    always starts with ``(``, which bumps depth before its own closing
    paren is reached, keeping it ineligible as a candidate for THIS scan.

    A real basename can just as legally contain an *unmatched* ``(`` of its
    own (``foo(bar.hpp``, Codex review, fresh evidence) -- there, depth
    never returns to 0 by the time the marker's real closing paren is
    reached, so the depth-tracking pass above finds no candidate at all.
    When that happens, a second, depth-blind pass looks for the FIRST ``)``
    whose immediately preceding text ends in ``:<digits>:<digits>``,
    treating every ``(``/``)`` in between as ordinary basename text rather
    than a nesting delimiter -- correct precisely because depth tracking
    already had its chance and failed, meaning the string's parens don't
    balance within this marker to begin with; unlike the primary pass, this
    fallback has no depth signal to tell a later, separate marker apart
    from more of this one's own basename, so it must stop at the first
    candidate to avoid swallowing that later marker.
    """
    depth = 0
    i = prefix_match.end()
    length = len(name)
    last_candidate: int | None = None
    while i < length:
        ch = name[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                body = name[prefix_match.end() : i]
                if _ANON_TYPE_TRAILING_LINE_COL_RE.search(body) is not None:
                    last_candidate = i
            else:
                depth -= 1
        i += 1
    if last_candidate is not None:
        return _anon_type_match_from_close_paren(name, prefix_match, last_candidate)

    for i in range(prefix_match.end(), length):
        if name[i] == ")":
            body = name[prefix_match.end() : i]
            if _ANON_TYPE_TRAILING_LINE_COL_RE.search(body) is not None:
                return _anon_type_match_from_close_paren(name, prefix_match, i)
    return None


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """``[start, end)`` ranges of every ``"..."`` quoted literal in *text*,
    respecting backslash-escaped quotes.

    A small, local copy of ``name_classification._quoted_spans`` (this
    module imports nothing from the rest of ``abicheck`` by design -- see
    the module docstring) so a C++20 fixed-string NTTP argument that merely
    *looks* like our marker shape (``Tag<"(lambda:a.h:1:2)">``) is never
    mistaken for a real one.
    """
    spans: list[tuple[int, int]] = []
    start: int | None = None
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == "\\" and start is not None:
            i += 2
            continue
        if ch == '"':
            if start is None:
                start = i
            else:
                spans.append((start, i + 1))
                start = None
        i += 1
    return spans


def _anon_type_ordinal_matches(name: str) -> list[_AnonTypeMatch]:
    """Every anonymous/lambda-closure marker in *name*, excluding one that
    falls inside a quoted literal.
    """
    if "(" not in name:
        return []
    quoted_spans = _quoted_spans(name)
    matches: list[_AnonTypeMatch] = []
    consumed_until = -1
    for prefix in _ANON_TYPE_MARKER_PREFIX_RE.finditer(name):
        if prefix.start() < consumed_until:
            # This prefix is itself inside a marker basename an earlier,
            # outer match already claimed (e.g. the nested
            # "(lambda:a.h:1:2)" inside "(lambda:(lambda:a.h:1:2).hpp:10:2)")
            # -- treat it as ordinary basename text rather than a second,
            # overlapping marker, so apply_anonymous_type_ordinals never
            # rewrites two overlapping ranges.
            continue
        if any(start <= prefix.start() < end for start, end in quoted_spans):
            continue
        match = _scan_anon_type_marker(name, prefix)
        if match is not None:
            matches.append(match)
            consumed_until = match.end
    return matches


def collect_anonymous_type_ordinals(
    names: _Iterable[str],
) -> dict[tuple[str, str, int, int], int]:
    """Assign a stable ordinal to every distinct anonymous/lambda-closure
    declaration referenced across *names* (typically every string field of
    one ``AbiSnapshot``), grouped by marker kind (``"(lambda"``,
    ``"(unnamed struct"``, ...) and declaring header basename, ordered by
    source position (``:line:col``).

    Computed fresh per snapshot (never shared across old/new): as long as
    the count and relative order of same-header, same-kind lambdas is
    unchanged between the two sides -- true for pure line drift -- both
    snapshots assign the identical ordinal to the identical closure.

    Known, accepted limitations (see this module's own docstring for the
    general shape of the same tradeoff on ``version_strip_segments``):
    (1) a lambda genuinely inserted/removed earlier in the same header still
    shifts every later ordinal, the same way it would shift every later
    ``#N`` in a real compiler's own numbering; (2) the group key is
    ``(marker, header basename)`` -- the same checkout-independent basename
    ``strip_anonymous_type_location`` already reduces a full path to -- so
    two genuinely different files sharing a basename (e.g. two vendored
    dependencies each shipping their own ``config.h``) share one ordinal
    sequence, and an edit in one can reorder an unrelated lambda's ordinal
    in the other. Closing either needs real scope/per-file identity no
    longer available once castxml/clang have flattened the closure into a
    type-name string -- not attempted here.

    Returns ``(marker, header_basename, line, col) -> 1-based ordinal``,
    ready for :func:`apply_anonymous_type_ordinals`.
    """
    coordinates: dict[tuple[str, str], set[tuple[int, int]]] = {}
    for name in names:
        for match in _anon_type_ordinal_matches(name):
            key = (match.marker, match.header)
            coordinates.setdefault(key, set()).add((match.line, match.col))

    ordinals: dict[tuple[str, str, int, int], int] = {}
    for (marker, header), coords in coordinates.items():
        for ordinal, (line, col) in enumerate(sorted(coords), start=1):
            ordinals[(marker, header, line, col)] = ordinal
    return ordinals


def apply_anonymous_type_ordinals(
    name: str, ordinals: _Mapping[tuple[str, str, int, int], int]
) -> str:
    """Rewrite every marker in *name* from its ``:<line>:<col>``
    discriminator to the stable ``#<ordinal>`` computed by
    :func:`collect_anonymous_type_ordinals` -- e.g.
    ``"raii_guard<(lambda:task_group.h:522:26)>"`` becomes
    ``"raii_guard<(lambda:task_group.h#3)>"``.

    A marker absent from *ordinals* is left completely untouched rather
    than fabricated.
    """
    if "(" not in name:
        return name
    matches = _anon_type_ordinal_matches(name)
    if not matches:
        return name
    pieces: list[str] = []
    cursor = 0
    for match in matches:
        ordinal = ordinals.get((match.marker, match.header, match.line, match.col))
        pieces.append(name[cursor : match.start])
        if ordinal is None:
            pieces.append(name[match.start : match.end])
        else:
            pieces.append(f"{match.marker}:{match.header}#{ordinal}")
        cursor = match.end
    pieces.append(name[cursor:])
    return "".join(pieces)


#: Dataclass field names that carry free-text/expression payload, never a
#: type/name spelling -- so a coincidental substring matching the closure
#: marker syntax must not be collected as (fabricated) identity evidence or
#: rewritten as if it were one (Codex review: a ``RecordType.deprecated``
#: message like ``"avoid (lambda:x.h:10:2)"`` was silently corrupted to
#: ``"avoid (lambda:x.h#1)"``). Shared across every declaration dataclass in
#: ``model.py`` that has a field of this name (``Function``/``Variable``/
#: ``TypeField``/``RecordType``/``EnumType``/``EnumMember`` all document
#: ``deprecated`` as "see Function.deprecated for the message-string
#: convention"; ``Param.default``/``TypeField.default`` are documented
#: "verbatim, value not preserved"), matched by name alone rather than
#: per-dataclass, since the walk in ``_collect_strings``/
#: ``_walk_rewrite_strings`` is itself dataclass-agnostic. ``Variable.value``
#: (its compile-time constant initializer, "if known", model.py's own
#: docstring) is the identical payload shape -- added after the same
#: reachable-corruption pattern was found on it too (Codex review, fresh
#: evidence). ``source_location``/``source_header`` (ADR-015 provenance --
#: a filesystem path, optionally with ``:line:col`` appended, never a C++
#: type/name spelling) are the same shape again: a legal path containing
#: marker-shaped text of its own (``/tmp/(lambda:a.h:1:2)``) was rewritten
#: even for a snapshot with no real closure at all, corrupting persisted
#: declaration provenance and, transitively, any later header-origin/
#: dependency-scoping decision that reads it (Codex review, fresh evidence).
_PAYLOAD_FIELD_EXCLUSIONS: frozenset[str] = frozenset(
    {"deprecated", "default", "value", "source_location", "source_header"}
)


def _collect_strings(value: object, out: list[str]) -> None:
    """Append every ``str`` reachable from *value* to *out*, recursing
    through dataclasses, lists/tuples, and dicts (keys and values) --
    except a field named in :data:`_PAYLOAD_FIELD_EXCLUSIONS`.
    """
    if isinstance(value, str):
        out.append(value)
    elif _dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in _dataclasses.fields(value):
            if f.name in _PAYLOAD_FIELD_EXCLUSIONS:
                continue
            _collect_strings(getattr(value, f.name), out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_strings(item, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                out.append(k)
            _collect_strings(v, out)


def _walk_rewrite_strings(value: object, rewrite: _Callable[[str], str]) -> object:
    """Rewrite every ``str`` reachable from *value* via ``rewrite(s)``,
    mutating dataclasses/lists/dicts in place where possible -- except a
    field named in :data:`_PAYLOAD_FIELD_EXCLUSIONS`. Returns the (possibly
    new) value -- a bare ``str`` can't be mutated in place.

    A **frozen** dataclass is rebuilt via ``dataclasses.replace`` rather
    than mutated: ``setattr`` on one raises ``FrozenInstanceError``
    outright, so this walk would crash the whole dump the moment any
    reachable model field held one. That is not hypothetical -- ADR-063
    Phase 2's ``entity_id`` carrier (a frozen ``model.identity.EntityId``,
    itself holding a tuple of frozen scope segments) is reachable from
    ``functions``/``variables``/``types``/``enums``, all four of which
    :data:`_LAMBDA_IDENTITY_FIELDS` walks. Rebuilding is also the right
    *behaviour*, not merely a way to avoid the exception: a closure marker
    that survives unrewritten inside an identity carrier would leave that
    carrier keyed on the raw ``:line:col`` spelling this whole function
    exists to remove, i.e. path/line-tainted identity next to a normalized
    one. Only ``init=True`` fields can be handed to ``replace``; a changed
    ``init=False`` field on a frozen dataclass is instead applied via
    ``object.__setattr__`` -- the same escape hatch a frozen dataclass's
    own ``__post_init__`` uses to set a derived field, and the established
    convention elsewhere in this codebase for the identical need (see
    ``compatibility_evaluation_config.py``) -- applied AFTER ``replace``
    rebuilds the ``init=True`` fields, onto the freshly-rebuilt object
    rather than the original, so a rewrite touching both kinds of field in
    one dataclass lands on the object this function actually returns
    (Codex review, PR #943): a reachable ``init=False`` field can itself
    hold a closure marker (e.g. one populated from a rewritten ``init=True``
    field inside ``__post_init__``), and silently discarding its rewrite
    would leave that field pointing at stale, path/line-tainted content
    even though the dataclass it belongs to was otherwise correctly
    rebuilt.

    A ``str``-subclass instance that is not *exactly* ``str`` (a closed-
    vocabulary ``class Visibility(str, Enum)`` member, say) is left
    untouched rather than handed to ``rewrite``: such a value is never a
    free-text type/name spelling a closure marker could appear in, and
    ``rewrite`` returning a plain ``str`` -- even one equal in content, as
    every stdlib string-transform helper does -- would silently demote the
    field from its enum member to a bare string one indistinguishable
    ``==``/``in`` check later couldn't tell apart from the real thing (a
    stored snapshot loaded through
    ``storage.snapshot_load_normalization.normalize_anonymous_type_spellings_on_load``
    then crashed every ``diff_symbols`` lookup of ``Function.visibility.value``
    with ``AttributeError: 'str' object has no attribute 'value'`` on any
    snapshot with a lambda/anonymous-type marker anywhere in it -- the walk
    reaches every ``Function``/``Variable`` in ``functions``/``variables``,
    ``visibility`` included, not just the marker-bearing field that
    triggered the walk).
    """
    if isinstance(value, str):
        if type(value) is not str:
            return value
        return rewrite(value)
    if _dataclasses.is_dataclass(value) and not isinstance(value, type):
        params = getattr(value, "__dataclass_params__", None)
        is_frozen = bool(getattr(params, "frozen", False))
        replacements: dict[str, object] = {}
        frozen_field_updates: dict[str, object] = {}
        for f in _dataclasses.fields(value):
            if f.name in _PAYLOAD_FIELD_EXCLUSIONS:
                continue
            old = getattr(value, f.name)
            new = _walk_rewrite_strings(old, rewrite)
            if new is old:
                continue
            if not is_frozen:
                setattr(value, f.name, new)
            elif f.init:
                replacements[f.name] = new
            else:
                frozen_field_updates[f.name] = new
        if replacements or frozen_field_updates:
            value = _dataclasses.replace(value, **replacements)
        for name, new in frozen_field_updates.items():
            object.__setattr__(value, name, new)
        return value
    if isinstance(value, list):
        for i, item in enumerate(value):
            new_item = _walk_rewrite_strings(item, rewrite)
            if new_item is not item:
                value[i] = new_item
        return value
    if isinstance(value, tuple):
        return tuple(_walk_rewrite_strings(item, rewrite) for item in value)
    if isinstance(value, dict):
        rewritten: dict[object, object] = {}
        changed = False
        for k, v in value.items():
            new_k = rewrite(k) if isinstance(k, str) else k
            new_v = _walk_rewrite_strings(v, rewrite)
            rewritten[new_k] = new_v
            if new_k != k or new_v is not v:
                changed = True
        if changed:
            value.clear()
            value.update(rewritten)
        return value
    return value


#: Fields whose string content can embed a castxml/clang closure marker --
#: scoped to the header-derived ABI surface rather than the whole snapshot,
#: since ELF/PE/Mach-O/DWARF metadata never carry a demangled C++ type
#: spelling. Keeping this scoped is what keeps
#: :func:`renumber_anonymous_closure_identities` cheap even for a snapshot
#: with a large exported symbol table.
#:
#: ``fact_provenance`` is included even though it carries no closure
#: markers of its own to *collect* ordinals from: its keys are composite
#: strings built by ``fact_provenance.type_fact_key``/``field_fact_key``
#: (e.g. ``"type:Foo<(lambda:x.h:10:2)>:field:y:size"``) that embed the
#: exact same type-name spelling ``types``/``functions``/etc. carry. Left
#: out, a hybrid-merged snapshot's provenance keys would still name the
#: pre-renumber ``:line:col`` spelling after every other field was
#: renumbered to ``#<ordinal>``, so ``fact_provenance.fact_producer()``
#: would silently miss on every closure-parameterized declaration (Codex
#: review on PR #868, fresh evidence). Rewriting it is safe: the ordinal
#: map is keyed on (marker, header, line, col), computed once from the
#: ABI-surface fields above, and a key or value here that doesn't match
#: any of those tuples is left untouched by
#: :func:`apply_anonymous_type_ordinals`.
#: ``constants`` (``#define``/``constexpr`` name -> value string) is
#: deliberately excluded, unlike every field above: its values are payload
#: literals, never a type-name spelling a closure marker could legitimately
#: appear in, and the generic dict walk below cannot tell a payload dict's
#: values apart from an identity-bearing one's -- rewriting them risked the
#: same corruption ``_PAYLOAD_FIELD_EXCLUSIONS`` already guards for
#: ``deprecated``/``default`` (Codex review, fresh evidence: a constant
#: literally spelled ``"text (lambda:x.h:1:1)"`` was rewritten to ordinal
#: form and could even consume an ordinal a real closure should have
#: gotten).
_LAMBDA_IDENTITY_FIELDS: tuple[str, ...] = (
    "functions",
    "variables",
    "types",
    "enums",
    "typedefs",
    "typedefs_qualified",
    "fact_provenance",
)


_defer_renumber = _threading.local()


@_contextlib.contextmanager
def defer_closure_identity_renumbering() -> _Iterator[None]:
    """Suppress :func:`renumber_anonymous_closure_identities` for the
    duration of this context (re-entrant; restores the previous state on
    exit, so a nested caller can't un-suppress an outer one).

    For a caller that produces MULTIPLE independent snapshots of the SAME
    headers and then merges them by identity key --
    ``dumper_hybrid.run_hybrid_dump`` is the one caller today -- and needs
    the renumbering applied exactly once, on the merged result, rather
    than once per input. Per-input renumbering before such a merge can
    silently desynchronize: two backends that see a header's lambdas in a
    different count or order (one omits an earlier same-header lambda the
    other captures, say) independently assign the SAME later closure a
    DIFFERENT ordinal, and the merge's identity-keyed matching (a
    qualified type name, a mangled function name) then either fails to
    join the two backends' facts for that closure, or -- worse -- joins
    it against a different, unrelated closure that happens to land on the
    same ordinal (Codex review, fresh evidence). ``threading.local`` scopes
    this per-thread, not process-global, in case a future caller ever runs
    two such merges concurrently on different threads.
    """
    previous = getattr(_defer_renumber, "active", False)
    _defer_renumber.active = True
    try:
        yield
    finally:
        _defer_renumber.active = previous


def _lambda_identity_containers_and_strings(
    snapshot: object,
) -> tuple[list[object], list[str]] | None:
    """Collect :data:`_LAMBDA_IDENTITY_FIELDS`' containers and strings, or
    ``None`` once none mentions ``"(lambda"``/``"(unnamed "`` -- shared with
    ``storage.snapshot_load_normalization``, an importer of this helper."""
    containers = [getattr(snapshot, name) for name in _LAMBDA_IDENTITY_FIELDS]
    strings: list[str] = []
    for container in containers:
        _collect_strings(container, strings)
    if not any("(lambda" in s or "(unnamed " in s for s in strings):
        return None
    return containers, strings


def renumber_anonymous_closure_identities(snapshot: _SnapshotT) -> _SnapshotT:
    """Replace each castxml/clang closure marker's ``:<line>:<col>``
    discriminator with a stable ordinal among same-header, same-kind
    closures in *snapshot* alone, mutating it in place. Returns *snapshot*
    unchanged (for chaining at a call site's own ``return`` statement).

    Call this once, right after an ``AbiSnapshot``'s
    functions/variables/types/enums/typedefs/constants are fully populated
    (a fresh dump) or loaded (``snapshot_from_dict``), and BEFORE
    ``AbiSnapshot.index()`` builds any name-keyed map from them -- a
    renamed ``RecordType.name`` must be what gets indexed. Idempotent
    (a snapshot already in ordinal form has nothing left to renumber) and a
    cheap no-op when nothing in *snapshot* embeds an anonymous/lambda
    marker at all, which is the common case. Duck-typed on
    ``getattr(snapshot, field)`` rather than importing ``AbiSnapshot``, to
    keep this module import-free -- see its own docstring.

    The marker regex requires the stripped spelling, never a raw
    ``(lambda at <path>:<line>:<col>)`` one --
    ``storage.snapshot_load_normalization`` strips it first on load.

    Known, accepted residual (Codex review, fresh evidence): a reader
    *older* than this function that loads a snapshot written by a *newer*
    abicheck (already in ordinal form) has no code path renumbering it
    back -- ``serialization.SCHEMA_VERSION`` was not bumped for this
    representation change, so that older reader has no signal the identity
    format differs from what its own dumper would produce. The reverse
    direction (an older-format baseline, current reader) is closed by the
    strip-then-renumber load path above; closing this one too needs a real
    schema bump, rippling into ``docs/`` and every fixture pinning the
    schema version -- separate, not folded into this fix.

    A no-op, returning *snapshot* untouched, inside
    :func:`defer_closure_identity_renumbering`'s context.
    """
    if getattr(_defer_renumber, "active", False):
        return snapshot
    collected = _lambda_identity_containers_and_strings(snapshot)
    if collected is None:
        return snapshot
    containers, strings = collected
    ordinals = collect_anonymous_type_ordinals(strings)
    if not ordinals:
        return snapshot

    def _rewrite(text: str) -> str:
        return apply_anonymous_type_ordinals(text, ordinals)

    for field_name, container in zip(_LAMBDA_IDENTITY_FIELDS, containers):
        new_container = _walk_rewrite_strings(container, _rewrite)
        if new_container is not container:
            setattr(snapshot, field_name, new_container)
    return snapshot
