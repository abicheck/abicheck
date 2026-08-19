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

"""Suppression — load and apply suppression rules to ABI changes."""
from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from .checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    ChangeKind,
    ReachabilityState,
    Verdict,
)
from .checker_types import Change
from .elf_metadata import SymbolBinding
from .suppression_yaml import parse_finding_id, raw_finding_ids_by_index

# Valid values for Suppression.binding — the same value strings
# Change.symbol_binding is stamped with (SymbolBinding.value). Imported from
# elf_metadata rather than duplicated, and safe to import: elf_metadata.py
# has no local imports of its own, so this carries no cycle risk (mirrors
# model.py's identical reasoning for re-exporting the same enum).
_VALID_BINDING: frozenset[str] = frozenset(b.value for b in SymbolBinding)

# Pre-build valid change_kind values for fast validation
_VALID_CHANGE_KINDS: frozenset[str] = frozenset(ck.value for ck in ChangeKind)


# Keys allowed in a suppression entry — unknown keys are rejected
_KNOWN_ENTRY_KEYS: frozenset[str] = frozenset({
    "symbol", "symbol_pattern", "type_pattern", "member_name",
    "change_kind", "reason", "label", "source_location", "expires",
    "namespace", "entity_namespace", "cause_namespace", "binding",
    "reachability", "allow_public_break", "allow_unknown_reachability",
    "finding_id",
})

# ADR-044 D2: valid values for Suppression.reachability.
# "proven-unreachable-only" (impact-analysis-layer P0) is a stricter variant
# of "unreachable-only": it additionally refuses to match a change whose
# Change.reachability_state is UNKNOWN (graph coverage insufficient to prove
# unreachability), rather than treating UNKNOWN the same as proven-unreachable
# the way the original boolean-only "unreachable-only" gate does.
_VALID_REACHABILITY: frozenset[str] = frozenset({
    "unreachable-only", "any", "public-only", "proven-unreachable-only",
})

# ChangeKind values that represent type-level changes (matched by type_pattern)
_TYPE_CHANGE_KINDS: frozenset[str] = frozenset({
    "type_size_changed", "type_alignment_changed", "type_field_removed",
    "type_field_added", "type_field_offset_changed", "type_field_type_changed",
    "type_base_changed", "type_vtable_changed", "type_added", "type_removed",
    "type_field_added_compatible", "type_became_opaque", "type_visibility_changed",
    "enum_member_removed", "enum_member_added", "enum_member_value_changed",
    "enum_last_member_value_changed", "enum_member_renamed",
    "enum_underlying_size_changed",
    "typedef_removed", "typedef_base_changed",
    "struct_field_type_changed", "union_field_type_changed",
})


def _compile_pattern(pattern: str | None, field_name: str) -> re.Pattern[str] | None:
    """Compile *pattern* as a regex, raising :class:`ValueError` on failure."""
    if pattern is None:
        return None
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid {field_name} {pattern!r}: {e}") from e


def _compile_glob(glob: str | None, field_name: str) -> re.Pattern[str] | None:
    """Compile an fnmatch-style *glob* to a regex, raising :class:`ValueError` on failure."""
    if glob is None:
        return None
    try:
        return re.compile(fnmatch.translate(glob))
    except re.error as e:
        raise ValueError(f"Invalid {field_name} {glob!r}: {e}") from e


_SEGMENT_RE_WRAPPER = re.compile(r"^\(\?s:(.*)\)\\[Zz]$")


def _fnmatch_segment_regex(segment: str) -> str:
    """Translate one non-globstar namespace *segment* to a regex fragment.

    Uses the same fnmatch semantics a single ``*``/``?`` has always had —
    only :func:`_translate_namespace_glob`'s handling of a whole ``**``
    segment changes.

    ``fnmatch.translate``'s end-of-string anchor is not stable across
    Python versions — Python 3.14 emits ``\\z`` where earlier versions
    emit ``\\Z`` (Codex review, verified against 3.14.4: an unmatched
    wrapper made this function return the *whole* anchored translation
    unstripped, so composing it mid-pattern anchored that one segment to
    the end of the string instead of just contributing a fragment —
    ``namespace="ns::*"`` stopped matching any ``ns::...`` name at all).
    Accept either spelling rather than depend on one.
    """
    translated = fnmatch.translate(segment)
    m = _SEGMENT_RE_WRAPPER.match(translated)
    return m.group(1) if m else translated


def _bracket_class_end(text: str, open_index: int) -> int:
    """If *text* has a genuine fnmatch bracket character class opening at
    *open_index* (a ``[``), return the index of its closing ``]``;
    otherwise -1 — an unmatched ``[`` is a literal character in fnmatch's
    own grammar, not a class opener (mirrors ``fnmatch.translate``'s own
    fallback for an unclosed bracket).

    Mirrors fnmatch's bracket rules: an optional leading ``!`` negates the
    class, and a ``]`` immediately after ``[``/``[!`` is a literal class
    member rather than the closer (so ``[]]`` is a one-character class
    matching a literal ``]``, not an empty, immediately-closed class).
    """
    j = open_index + 1
    n = len(text)
    if j < n and text[j] == "!":
        j += 1
    if j < n and text[j] == "]":
        j += 1
    while j < n and text[j] != "]":
        j += 1
    return j if j < n else -1


def _has_wildcard_char(segment: str) -> bool:
    """Whether *segment* itself contains an fnmatch wildcard construct —
    ``*``, ``?``, or a genuine ``[...]`` bracket character class.

    A bracket class is just as unconstrained-vs-literal a distinction as
    ``*``/``?`` for the trailing-globstar delegation this feeds: a segment
    like ``foo[0-9]`` is a *bounded* fnmatch construct (matches exactly
    ``foo`` plus one digit), yet real ``fnmatch.translate("foo[0-9]::**")``
    still requires the literal ``::`` unconditionally when it borders a
    trailing globstar — there is no atomic-group "zero or more segments"
    absorption for this shape either, so treating a bracket class as
    "no wildcard here" would leave the same over-matching gap open (Codex
    review, fresh evidence: ``"foo[0-9]::**"`` matched bare ``"foo1"`` with
    no ``::`` anywhere before this fix).
    """
    if "*" in segment or "?" in segment:
        return True
    i = segment.find("[")
    while i != -1:
        if _bracket_class_end(segment, i) != -1:
            return True
        i = segment.find("[", i + 1)
    return False


def _split_namespace_segments(pattern: str) -> list[str]:
    """Split *pattern* on ``::`` namespace separators, treating a ``::``
    that appears *inside* a genuine fnmatch bracket class (``[...]``) as
    literal class content rather than a segment boundary.

    A plain ``pattern.split("::")`` corrupts a pre-existing, valid
    fnmatch-style selector whose character class happens to contain a
    literal ``::`` — ``"ns::[!::]*"`` (exclude a literal ``:`` or the
    segment separator character) split into ``["ns", "[!", "]*"]``,
    scattering the bracket class across two "segments" and translating
    each half as escaped literal text instead of one character class
    (Codex review, fresh evidence: this previously-matching selector
    stopped matching ``"ns::foo"`` entirely).
    """
    segments: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "[":
            end = _bracket_class_end(pattern, i)
            if end != -1:
                buf.append(pattern[i:end + 1])
                i = end + 1
                continue
        if pattern[i:i + 2] == "::":
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return segments


def _collapsed_namespace_segments(pattern: str) -> list[str]:
    """Split *pattern* into ``::``-separated segments (:func:`_split_namespace_segments`)
    and collapse any run of adjacent standalone ``"**"`` segments into one —
    ``"a::**::**::b"`` becomes ``["a", "**", "b"]``. Shared by
    :func:`_translate_namespace_glob` and :func:`_compile_namespace_glob`'s
    fast-path detection so both operate on an identical segment list."""
    segments: list[str] = []
    for seg in _split_namespace_segments(pattern):
        if seg == "**" and segments and segments[-1] == "**":
            continue
        segments.append(seg)
    return segments


class _SegmentGlobMatcher:
    """Non-backtracking namespace-glob matcher, used for every
    namespace/entity_namespace/cause_namespace selector.

    :func:`_translate_namespace_glob` compiles a namespace glob into one
    regex where each standalone ``"**"`` becomes an unconstrained,
    independently-backtracking ``.*``-based group. A pattern chaining
    several non-adjacent globstars against a repetitive-content candidate
    name (a real, if unusual, worst case for a deeply-templated/generated
    C++ symbol) is combinatorial for the ``re`` backtracking engine: e.g.
    ``"**::a::**::a::**::a::**::a::**::a::z"`` against a 121-segment
    non-matching name took over 8 seconds to reject (Codex review, real
    reproduction).

    **Design, after two false starts documented below.** Split the pattern
    on every standalone ``"**"`` into *runs* of consecutive non-globstar
    segments (a run may be empty, only at the very start/end — two
    globstars can never be directly adjacent, :func:`_collapsed_namespace_
    segments` already collapsed that). Each run is compiled *once*, as a
    single fnmatch regex over its own segments rejoined with literal
    ``"::"`` (:func:`_fnmatch_segment_regex` on ``"::".join(run)``) —
    exactly reproducing this module's pre-globstar-rewrite behavior for
    ordinary text, including a bare ``*``/``?`` legitimately spanning
    ``::`` within its own run. Matching walks the runs left to right,
    tracking the *set* of name-segment indices a valid match could
    currently be sitting at (``reachable``): a literal-only run shifts
    every index by its own fixed segment count (O(1) per index, since a
    run with no wildcard segment can only ever match one fixed-length
    span); a wildcarded run tries every candidate span starting at each
    reachable index (bounded, not exponential — see below); and a globstar
    between two runs collapses ``reachable`` to the contiguous range
    starting at its minimum, since "absorb zero-or-more segments" from any
    reachable point is a superset of absorbing zero-or-more from a smaller
    one. This is the same "zero or more segments" DP that closed the
    literal-only case, generalized to whole runs instead of whole segments
    — the ambiguity search only ever iterates over name length, never over
    the exponentially many ways to partition a repeating pattern, so total
    work is polynomial in name length (bounded by the number of runs ×
    name_length², since a wildcarded run's own regex costs O(span) and is
    tried over O(name_length) candidate spans per reachable start — still
    no backtracking blowup regardless of how many globstars the pattern
    chains).

    **First false start**: routing only an all-literal-plus-globstar
    pattern through this matcher and falling back to the old regex for any
    pattern with a per-segment wildcard was too narrow — a wildcarded
    segment sitting *beside* several other globstars (fresh Codex
    evidence: ``"**::a*::**::a::**::a::**::a::**::a::z"``) still took the
    old, still-exponential regex path (~3.7s to reject 61 repeated
    segments, worse for more).

    **Second false start**: generalizing to *every* pattern by requiring a
    wildcarded segment to match exactly one whole name segment (the same
    rule a literal segment follows) is a real, silent behavior change —
    fresh Codex evidence again: real, pre-existing ``fnmatch`` behavior
    (predating this whole globstar rewrite) lets a bare ``*`` span ``::``
    freely, so ``"oneapi::*::detail"`` legitimately matched
    ``"oneapi::x::y::detail"`` and silently stopped matching once
    per-segment matching was enforced everywhere. The run-based design
    above is what actually reconciles both properties: a wildcard's own
    run still spans ``::`` exactly like real ``fnmatch``, while only the
    *globstar* boundaries between runs get the backtracking-safe DP
    treatment — because only a *chain* of standalone globstars was ever
    the source of exponential blowup, not an ordinary wildcard on its own.

    **One boundary shape still needs special handling even in the run
    model**: a wildcarded run immediately followed by the pattern's own
    *trailing* globstar (nothing real after it — ``"foo**::**"`` and
    friends). There, letting the run try every candidate end position
    (as every other run does) lets the run's own trailing wildcard "cheat"
    past the globstar entirely — with nothing downstream to reject an
    over-greedy match, ``"foo**::**"`` matched bare ``"foobar"`` again
    (Codex-established precedent: real ``fnmatch.translate("foo**::**")``
    requires an actual ``::`` to exist, via its own atomic-group
    optimization — this shape is not expressible as "N segments of text,"
    it depends on the *raw string* containing a literal separator
    somewhere). A **non-trailing** occurrence of this same adjacency
    (``"a::foo*::**::z"``) does *not* have this problem: this module's
    own pre-existing, deliberately-tested convention already prefers
    "zero or more segments" over native fnmatch's atomic-group rejection
    there (see :func:`_translate_namespace_glob`'s docstring), and the run
    model reproduces that correctly on its own — a downstream literal
    (``"z"``) always rejects the over-greedy end position, leaving the
    tighter, correct one as the only survivor. So only the truly-trailing
    case is special-cased here, mirroring the delegation
    :func:`_translate_namespace_glob` already used for it: build one
    combined regex from the wildcarded run's own text plus its bordering
    ``"::**"``, and check it against the joined remainder for every
    position the rest of the pattern could have reached — bounded by name
    length, not exponential.

    **Fast path for the overwhelmingly common case.** All of the above
    only exists to defuse *multiple* standalone globstars interacting —
    with at most one ``"**"`` in the whole pattern there is only ever one
    "how much does it absorb" choice, which cannot multiply into
    combinatorial backtracking no matter what borders it (verified: even
    ``"a*::**::b*::c*::d*"`` against a 200-segment name resolves in under
    a millisecond via a single compiled regex). A real suppression rule
    almost always has zero or one globstar, so patterns of that shape
    reuse the plain, single compiled regex this module used before the
    DP rewrite (:func:`_translate_namespace_glob`) instead of paying the
    run/DP machinery's real, if bounded, per-call overhead — a profiled
    real-world-shaped benchmark (many short-name findings audited against
    a fixed ruleset including one ``"**::vendorN::*"`` rule per group)
    showed the general DP path costing routine namespace matching ~1.6×
    more per call than this fast path, entirely needless when there is no
    ambiguity to resolve in the first place (Codex review: a real +94%
    ``suppression_audit`` benchmark regression this fast path closes).
    """

    __slots__ = ("_simple", "_runs", "_tail")

    def __init__(self, pattern: str, segments: list[str]) -> None:
        if segments.count("**") <= 1:
            self._simple: re.Pattern[str] | None = re.compile(_translate_namespace_glob(pattern))
            self._runs: list[tuple[str, ...] | None | _WildcardRunMatcher] = []
            self._tail: re.Pattern[str] | None = None
            return
        self._simple = None
        tail: re.Pattern[str] | None = None
        if len(segments) >= 2 and segments[-1] == "**":
            # Walk back from just before the trailing "**" to find the run
            # immediately preceding it (there is always at least one — two
            # globstars can never be adjacent after collapsing).
            j = len(segments) - 2
            prefix_run: list[str] = []
            while j >= 0 and segments[j] != "**":
                prefix_run.insert(0, segments[j])
                j -= 1
            if prefix_run and _has_wildcard_char(prefix_run[-1]):
                combined = _fnmatch_segment_regex("::".join(prefix_run) + "::**")
                tail = re.compile("(?s:" + combined + ")\\Z")
                # The special-cased run + trailing globstar are handled by
                # `tail` alone — the run/globstar walk below only covers
                # whatever comes before them.
                segments = segments[: j + 1]
        self._tail = tail
        runs: list[tuple[str, ...] | None | _WildcardRunMatcher] = []
        current: list[str] = []
        for seg in segments:
            if seg == "**":
                runs.append(_compile_run(current))
                current = []
            else:
                current.append(seg)
        runs.append(_compile_run(current))
        self._runs = runs

    def match(self, name: str) -> bool:
        """Return True if *name* matches this matcher's pattern.

        Mirrors ``re.Pattern.match`` against a pattern anchored at both
        ends (the compiled-regex path always anchors with ``\\Z``) — the
        whole *name* must match, not just a prefix.
        """
        if self._simple is not None:
            return self._simple.match(name) is not None
        name_segments = name.split("::")
        n = len(name_segments)
        seg_start, seg_end = _segment_offsets(name_segments)
        reachable = self._run_glob_reachable(name, name_segments, seg_start, seg_end, n)
        if self._tail is None:
            return n in reachable
        return any(
            self._tail.fullmatch(name, seg_start[j0]) is not None for j0 in reachable
        )

    def matches_any_ancestor(self, name: str) -> bool:
        """Return True if *name*, or any ancestor obtained by repeatedly
        stripping the last ``"::"``-segment, matches this pattern.

        Used by :func:`_ns_match` instead of a Python-level loop calling
        :meth:`match` once per ancestor level. A naive loop is O(ancestor
        count) separate top-level matches — each independently paying this
        matcher's own worst-case cost, multiplying an already-polynomial
        per-call cost by name length again (Codex review, real
        reproduction: an ~11.6s single-symbol suppression check for a
        wildcarded-run-beside-several-globstars pattern against a 300-
        segment non-matching name, even though a single top-level
        :meth:`match` call on the same input takes well under a second —
        the multiplication, not any one call, was the real cost). Runs the
        run/globstar walk exactly *once* against the full name instead:

        - **No tail.** :meth:`_run_glob_reachable`'s result, for every ``j``
          it contains, already means "the whole pattern matches
          ``name_segments[0:j]``" — the DP never looks ahead of the
          position it is currently deciding, so this is true independent
          of what segments (if any) exist beyond ``j``. Checking whether
          any ``j >= 1`` is reachable therefore answers "does any ancestor
          match" directly, without recomputing per ancestor.
        - **With a tail.** The tail regex always ends in the unconstrained
          ``"::**"`` it was built from, so for a *fixed* start ``j0`` it is
          monotonic in its own end position: once it matches some length
          it matches every longer one too. By the contrapositive, if it
          fails against the *longest* available text (the full remaining
          name), it cannot have succeeded against any shorter one either —
          so checking only the full-length end (exactly what :meth:`match`
          already does) is equivalent to trying every ancestor length for
          that ``j0``. A tail-having pattern's own trailing ``"**"``
          already means "this prefix and everything deeper," which is why
          the ancestor walk buys it nothing extra to begin with.

        The fast (at-most-one-globstar) path below is not exponential —
        multiple globstars are what makes a naive ancestor loop costly to
        begin with — so it keeps the plain loop the DP path above
        replaces, matching pre-existing behavior exactly.
        """
        if self._simple is not None:
            candidate = name
            while True:
                if self._simple.match(candidate):
                    return True
                if "::" not in candidate:
                    return False
                candidate = candidate.rsplit("::", 1)[0]
        name_segments = name.split("::")
        n = len(name_segments)
        seg_start, seg_end = _segment_offsets(name_segments)
        reachable = self._run_glob_reachable(name, name_segments, seg_start, seg_end, n)
        if self._tail is None:
            return any(j >= 1 for j in reachable)
        return any(
            self._tail.fullmatch(name, seg_start[j0]) is not None for j0 in reachable
        )

    def _run_glob_reachable(
        self,
        name: str,
        name_segments: list[str],
        seg_start: list[int],
        seg_end: list[int],
        n: int,
    ) -> set[int]:
        """Walk this matcher's runs and their bordering globstars once
        against *name_segments*, returning the set of prefix lengths a
        valid match (of everything up to, but not including, any
        :attr:`_tail`) could end at."""
        reachable: set[int] = {0}
        last = len(self._runs) - 1
        for i, run in enumerate(self._runs):
            reachable = _match_run(
                run, name, name_segments, seg_start, seg_end, n, reachable
            )
            if not reachable:
                return reachable
            if i < last:
                # A globstar always follows a non-final run: it can absorb
                # zero or more further segments from *any* reachable point,
                # so the set of positions it can reach collapses to the
                # contiguous range starting at the smallest one — a larger
                # starting point's own reachable range is always a subset.
                reachable = set(range(min(reachable), n + 1))
        return reachable


class _WildcardRunMatcher:
    """A compiled regex for one wildcarded :class:`_SegmentGlobMatcher` run,
    plus two cheap pre-checks that let :func:`_match_run` avoid most of the
    O(n) candidate-span regex calls per start a naive exhaustive search
    would make:

    - ``monotonic``: True when a match at end position E implies a match
      at every end position > E too — exactly when the run's own source
      text (its segments rejoined with ``"::"``) ends in a bare,
      unconstrained ``*`` (fnmatch always translates a trailing ``*`` to
      an unanchored ``.*``, so appending more text can never turn a match
      into a non-match). Lets :func:`_match_run` stop at the *first*
      successful end position per start and record the whole remaining
      range at once, without further regex calls.
    - ``last_segment``: when the run's own *last declared segment* has no
      wildcard, this is that literal string — a necessary condition for
      *any* candidate span ending there: the span's own last name segment
      must equal it exactly, checkable with a plain ``==`` instead of a
      full regex call. For a run like ``["a*", "z"]`` this turns "try
      every end position with a regex" into "skip every end position
      whose last name segment isn't literally 'z', which for
      non-matching input is most of them, at a fraction of the cost."

    Both close the same real regression (Codex review, real reproduction:
    a chain of several wildcarded runs — e.g.
    ``"**::a*::**::a*::**::a*::z"`` — took ~4s for 600 segments and ~5s
    for 1200 even after the join-elimination fix below closed the
    *allocation* half of the cost, because the pattern's own *trailing*
    run (``["a*", "z"]``) is not monotonic — it requires an exact literal
    ``"z"`` at the end — so it fell all the way through to the exhaustive
    per-span regex search with no non-matching name ever containing a
    ``"z"`` segment to short-circuit on). Neither optimization changes
    what matches; both are strictly cheaper ways of reaching the same
    O(n)-candidate exhaustive search's answer when it cannot be avoided
    (no wildcard-run shape is provably monotonic *and* literal-anchored
    at once), and skip most of it when it can be.
    """

    __slots__ = ("pattern", "monotonic", "last_segment")

    def __init__(
        self, pattern: re.Pattern[str], monotonic: bool, last_segment: str | None
    ) -> None:
        self.pattern = pattern
        self.monotonic = monotonic
        self.last_segment = last_segment


def _compile_run(run: list[str]) -> tuple[str, ...] | None | _WildcardRunMatcher:
    """Compile one :class:`_SegmentGlobMatcher` run (a maximal sequence of
    consecutive non-globstar segments) to a matcher:

    - ``None`` for an empty run (only possible at the very start/end of the
      pattern) — matches zero segments, always.
    - A ``tuple`` of the run's own literal segments when *none* of them has
      an fnmatch wildcard — the overwhelmingly common case (CodeRabbit
      review: a multi-segment all-literal run, e.g. ``["a", "b"]`` between
      two globstars, previously fell through to the regex path below even
      though it has a fixed, known length and needs no backtracking-aware
      matching at all; a direct positional ``==`` comparison per segment is
      cheaper and needs no compiled pattern).
    - A :class:`_WildcardRunMatcher` otherwise, built from ``"::".join(run)``
      — one combined fnmatch translation so a wildcard anywhere in the run
      can still span the run's own internal ``::`` joiners exactly like
      plain ``fnmatch`` always could.
    """
    if not run:
        return None
    if not any(_has_wildcard_char(seg) for seg in run):
        return tuple(run)
    source = "::".join(run)
    pattern = re.compile("(?s:" + _fnmatch_segment_regex(source) + ")\\Z")
    last_segment = run[-1] if not _has_wildcard_char(run[-1]) else None
    return _WildcardRunMatcher(pattern, monotonic=source.endswith("*"), last_segment=last_segment)


def _segment_offsets(name_segments: list[str]) -> tuple[list[int], list[int]]:
    """Return, for a name already split on ``"::"``, each segment's own
    ``(start, end)`` character offsets within ``"::".join(name_segments)``
    (which is always exactly the original, un-split candidate string —
    ``"::".join(s.split("::")) == s`` for any string ``s``). Computed once
    per :meth:`_SegmentGlobMatcher.match`/``matches_any_ancestor`` call and
    reused by every candidate span :func:`_match_run` tries, instead of
    reconstructing ``"::".join(name_segments[s:e])`` — an O(e - s) string
    allocation — for each of the O(n) candidate spans a wildcarded run
    tries per start (Codex review, real reproduction: a chain of several
    wildcarded runs made this cubic overall, ~4s for 600 segments — the
    per-candidate string-building cost, not just the O(n²) candidate
    count, is what these offsets eliminate)."""
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for seg in name_segments:
        starts.append(pos)
        pos += len(seg)
        ends.append(pos)
        pos += 2  # the "::" separator (unused after the last segment)
    # One extra trailing entry so `seg_start[n]` (a start position exactly
    # at the end of the name — reachable, e.g., right after a globstar
    # collapses to `range(..., n + 1)`) is always valid to index, even
    # though no run can ever consume anything starting there.
    starts.append(pos)
    return starts, ends


def _match_run(
    run: tuple[str, ...] | None | _WildcardRunMatcher,
    name: str,
    name_segments: list[str],
    seg_start: list[int],
    seg_end: list[int],
    n: int,
    reachable: set[int],
) -> set[int]:
    """Return the set of name-segment indices reachable after matching
    *run* starting from each index in *reachable*."""
    if run is None:
        return reachable
    if isinstance(run, tuple):
        # Fixed-length literal run: O(len(run)) positional comparison per
        # start, no regex compile/match involved.
        length = len(run)
        return {
            s + length
            for s in reachable
            if s + length <= n and tuple(name_segments[s : s + length]) == run
        }
    result: set[int] = set()
    pattern = run.pattern
    last_segment = run.last_segment
    for s in reachable:
        # A non-empty run (>= 1 declared segment) always consumes at
        # least one whole name segment — start at s + 1, never s. A bare
        # "*" segment's own fnmatch regex also matches the empty string
        # (Codex review, real reproduction: "*::**::detail::**" matched
        # bare "detail", since fullmatch("") against a run of just "*"
        # succeeds) — only a genuinely *empty* run (``run is None``,
        # handled above) represents "zero segments"; that emptiness is a
        # property of the run's own declared segment list, never
        # something its regex's own leniency should decide.
        start = seg_start[s]
        for e in range(s + 1, n + 1):
            if last_segment is not None and name_segments[e - 1] != last_segment:
                # Cheap necessary-condition pre-filter (see
                # _WildcardRunMatcher): a run whose own last segment is a
                # literal can never match unless the candidate span's own
                # last name segment equals it exactly — skip the full
                # regex call entirely for every span that fails this.
                continue
            # `fullmatch(name, pos, endpos)` matches against name[pos:endpos]
            # without materializing that substring — the offsets above are
            # what make this possible instead of re-joining every candidate.
            if pattern.fullmatch(name, start, seg_end[e - 1]):
                if run.monotonic:
                    # Every longer span from the same start also matches
                    # (see _WildcardRunMatcher) — record the rest in one
                    # shot instead of re-matching each of them.
                    result.update(range(e, n + 1))
                    break
                result.add(e)
    return result


def _translate_namespace_glob(pattern: str) -> str:
    """Translate a ``namespace``/``entity_namespace``/``cause_namespace``
    glob to a regex pattern string, with pathspec/gitignore-style semantics
    for a ``**`` *segment*.

    A straight ``fnmatch.translate(pattern)`` treats every run of ``*``
    (single or doubled) as an ordinary ``.*`` wildcard glued to whatever
    literal text follows it — so two starred regions are still separated by
    a *mandatory* literal ``::``, and a pattern like
    ``oneapi::dal::**::detail::**`` can never match the zero-segment case
    ``oneapi::dal::detail::...`` (there is no room for a `::` that isn't
    there). That defeats the documented intent of ``**`` as "zero or more
    namespace segments" — every real-world use of this footgun so far has
    been a double-star meant to *also* match the "nothing in between" case.

    Here a ``**`` segment matches zero or more complete ``::``-separated
    segments, absorbing its own adjoining ``::`` separator so it can match
    nothing at all: ``a::**::b`` compiles to ``a(?:::.*)?::b``, which
    matches ``a::b`` as well as ``a::x::b`` and ``a::x::y::b``. A single
    ``*``/``?`` (never a full ``**`` segment on its own) keeps its original
    fnmatch behavior unchanged, via :func:`_fnmatch_segment_regex`.

    A **trailing** ``**`` segment (the last segment, not the whole-pattern
    standalone case above) immediately preceded by a segment that itself
    contains its *own* wildcard is a special case the hand-rolled
    optional-``::`` absorption above cannot express correctly: that
    preceding segment's own unconstrained wildcard sits directly next to a
    *fully optional* group with nothing after it to anchor the match, so a
    greedy ``.*`` never needs to try the ``::``-requiring alternative at
    all — ``foo**::**`` compiled (before this fix) to ``foo.*(?:::.*)?``,
    which matches bare ``foobar`` even though no ``::`` appears anywhere in
    it (Codex review, fresh evidence: the original, pre-rewrite fnmatch-
    style pattern required that literal ``::`` to be present — verified
    against real ``fnmatch.translate("foo**::**")``, which already handles
    this exact adjacency correctly via an atomic group). Rather than
    re-derive that same guarantee by hand, :func:`_fnmatch_segment_regex`
    is asked to translate the preceding wildcard segment *together with*
    its bordering trailing ``**`` (and the ``::`` between them) as one
    combined fnmatch string, reusing CPython's own correct handling instead
    of reinventing it.

    This delegation is deliberately scoped to *only* the trailing-globstar
    shape. A **leading** globstar followed by a wildcarded segment
    (``**::detail*``) has no such ambiguity — the globstar's own leading
    form (``(?:.*::)?``) is evaluated *first* and its own internal
    constraint ("must end in a literal ``::``" if non-empty) already fully
    resolves the choice before the unconstrained segment is ever reached,
    so the hand-rolled form already correctly matches the zero-segment case
    (``detail_private``) without needing delegation. Delegating there
    anyway was tried and reverted: native ``fnmatch.translate`` applies the
    *same* atomic-group optimization to a leading globstar too, which
    turned out to drop the zero-segment match entirely (Codex review, fresh
    evidence: ``fnmatch.translate("**::detail*")`` requires a literal
    ``::`` unconditionally, rejecting bare ``detail_private``). A **middle**
    globstar with a wildcarded neighbor on either side is likewise left
    hand-rolled: the segment *after* it always contributes its own mandatory
    ``::`` joiner (this function's existing logic below), which anchors the
    match regardless of what precedes — verified this hand-rolled form is
    not merely "close enough" but strictly more correct than delegating:
    native's atomic-group translation of ``a::foo*::**::b`` additionally
    (and incorrectly, per this module's own "zero or more segments"
    contract) rejects the legitimate zero-segment match ``a::foo::b``, a
    quirk of the same CPython glob-run optimization tuned for shell-style
    path matching, not this module's namespace semantics.
    """
    # A run of adjacent "**" segments means the same thing as one — collapse
    # "a::**::**::b" to "a::**::b". Done on the already-split segment list,
    # not the raw string (Codex review): a substring-level regex here would
    # also fire inside a non-globstar segment ending in "**" (e.g.
    # "foo**::**" contains the literal substring "**::**" starting mid-
    # segment), silently rewriting it to "foo**" and dropping the "::"
    # boundary the fnmatch-style pattern still required.
    segments = _collapsed_namespace_segments(pattern)
    parts: list[str] = []
    i = 0
    n = len(segments)
    while i < n:
        seg = segments[i]
        if seg == "**":
            if i == 0 and i == n - 1:
                # A standalone "**" (the whole pattern, after the collapse
                # above) is a catch-all — it must consume arbitrary
                # namespace text, not just "" or a "::"-terminated prefix
                # (Codex review: the two-way leading/trailing form below
                # left this case matching only the empty string or text
                # ending in "::", so "namespace: '**'" stopped matching an
                # ordinary name like "oneapi::dal::foo" entirely).
                parts.append(".*")
                i += 1
                continue
            is_trailing = i == n - 1
            prev_has_wildcard = i > 0 and _has_wildcard_char(segments[i - 1])
            if is_trailing and prev_has_wildcard:
                # `parts[-1]` is still exactly the previous segment's own
                # regex fragment (appended on the prior loop iteration) —
                # replace it in place with the combined translation; any
                # earlier "::" joiner sits in its own separate `parts`
                # entry and is untouched.
                combined = _fnmatch_segment_regex(segments[i - 1] + "::**")
                parts[-1] = combined
                i += 1
                continue
            parts.append("(?:.*::)?" if i == 0 else "(?:::.*)?")
            i += 1
            continue
        # A leading "**" already absorbs its own trailing "::" (or correctly
        # contributes none, for the zero-segment match) — every other
        # neighboring pair needs an explicit "::" joiner.
        prev_is_leading_globstar = i == 1 and segments[0] == "**"
        if i > 0 and not prev_is_leading_globstar:
            parts.append("::")
        parts.append(_fnmatch_segment_regex(seg))
        i += 1
    return "(?s:" + "".join(parts) + ")\\Z"


def _compile_namespace_glob(glob: str | None, field_name: str) -> _SegmentGlobMatcher | None:
    """Compile a namespace-selector glob (``namespace``/``entity_namespace``/
    ``cause_namespace``) to a :class:`_SegmentGlobMatcher`, using the same
    pathspec/gitignore-style globstar semantics :func:`_translate_namespace_glob`
    documents. Raises :class:`ValueError` on a malformed pattern.

    Routes through the non-backtracking DP matcher only when the pattern
    could actually be pathological (two or more standalone globstars) —
    immune to the catastrophic backtracking such a chain (with or without
    an embedded per-segment ``*``/``?``/``[...]`` wildcard) can otherwise
    trigger against a long, repetitive-content candidate name (Codex
    review, two P2 findings: a real ~8s stall for an all-literal chain,
    then a real ~3.7s-and-growing stall once one segment in the chain also
    carried a wildcard). A pattern with at most one globstar — the
    overwhelming majority of real suppression rules — keeps the plain,
    cheaper compiled regex instead (:class:`_SegmentGlobMatcher`'s own
    fast-path docstring). See that class's docstring for why the DP path
    is provably equivalent to the old regex path for every pattern shape,
    including the one case (a wildcarded segment immediately bordering a
    trailing globstar) that needs special handling to keep behaving
    exactly like real ``fnmatch.translate`` there.
    """
    if glob is None:
        return None
    try:
        segments = _collapsed_namespace_segments(glob)
        # _fnmatch_segment_regex (used for wildcarded segments, and for the
        # bordering-globstar tail) raises re.error on a malformed fnmatch
        # pattern the same way _translate_namespace_glob's callers expect.
        return _SegmentGlobMatcher(glob, segments)
    except re.error as e:
        raise ValueError(f"Invalid {field_name} {glob!r}: {e}") from e


def _validate_selectors(
    has_symbol: bool,
    has_sym_pattern: bool,
    has_type_pattern: bool,
    has_member_name: bool,
    has_source_location: bool,
    has_namespace: bool,
    has_finding_id: bool = False,
) -> None:
    """Raise :class:`ValueError` if the selector combination is invalid."""
    selector_count = sum([has_symbol, has_sym_pattern, has_type_pattern])
    if (
        selector_count == 0
        and not has_source_location
        and not has_member_name
        and not has_namespace
        and not has_finding_id
    ):
        raise ValueError(
            "Suppression must have at least one of: "
            "'symbol', 'symbol_pattern', 'type_pattern', "
            "'member_name', 'source_location', 'namespace', or 'finding_id'"
        )
    if selector_count > 1:
        raise ValueError(
            "Suppression fields 'symbol', 'symbol_pattern', and 'type_pattern' "
            "are mutually exclusive — specify exactly one"
        )
    if has_member_name and (has_symbol or has_sym_pattern):
        raise ValueError(
            "'member_name' cannot be combined with 'symbol' or 'symbol_pattern' "
            "(those already match the full symbol). Combine with 'type_pattern' "
            "and/or 'change_kind' instead."
        )


def _ns_match(pat: _SegmentGlobMatcher, name: str | None) -> bool:
    """Return True if *name* (or any of its namespace ancestors) matches *pat*.

    Handles Itanium-mangled symbols by also trying the demangled form.
    Template arguments are stripped before walking the ancestor chain.

    Delegates the ancestor walk to :meth:`_SegmentGlobMatcher.
    matches_any_ancestor` rather than looping over
    ``pat.match(candidate)`` for each progressively-shorter candidate
    here: a Python-level loop calls the matcher once per ancestor level,
    multiplying its own (already polynomial, not exponential) per-call
    cost by name length again — the matcher computes its run/globstar walk
    exactly once internally instead (see that method's docstring).
    """
    if not name:
        return False
    from .demangle import demangle as _dm
    from .internal_leak import _strip_template_args

    forms: list[str] = [name]
    if name.startswith("_Z"):
        dm = _dm(name)
        if dm:
            forms.append(dm)
    return any(pat.matches_any_ancestor(_strip_template_args(form)) for form in forms)


def _matches_source_location(compiled: re.Pattern[str], change: Change) -> bool:
    """Return False if *change*'s source path does not match *compiled*."""
    src = change.source_location or ""
    src_path = re.sub(r":\d+(?::\d+)?$", "", src)
    return bool(compiled.match(src_path))


def _matches_member_name(compiled: re.Pattern[str], change: Change) -> bool:
    """Return True if the last ``::``-segment of ``change.symbol`` matches *compiled*."""
    member = change.symbol.rsplit("::", 1)[-1] if change.symbol else ""
    return bool(compiled.fullmatch(member))


def _matches_binding(binding: str, change: Change) -> bool:
    """Return True if *change*'s ELF symbol linkage equals *binding*.

    ``change.symbol_binding`` is ``None`` for every change kind other than
    ``FUNC_REMOVED``/``FUNC_REMOVED_ELF_ONLY``/``VAR_REMOVED``/
    ``FUNC_DELETED_ELF_FALLBACK``/``FUNC_VISIBILITY_CHANGED`` (the only
    kinds any detector stamps it on), and also ``None`` on one of those whose binding
    was never captured (non-ELF platform, older snapshot, no matching
    ``.dynsym`` entry). A rule with an explicit ``binding:`` selector never
    matches either case — an unknown binding is not the same fact as a
    confirmed one, and silently treating it as a match would suppress a
    finding this rule was never actually audited against (the same
    fail-closed-on-unknown-evidence discipline ``reachability:
    proven-unreachable-only`` already uses for
    ``Change.reachability_state``).
    """
    return change.symbol_binding == binding


def _matches_finding_id(finding_id: str, change: Change) -> bool:
    """Return True if *change*'s canonical (backend-independent) identity
    equals *finding_id* — see :attr:`Suppression.finding_id`.

    Uses the same resolver the report's own ``canonical_finding_id`` field
    is computed from, so a value copied straight out of a report always
    matches the change it came from. Never raises: a change this resolver
    cannot compute an identity for (unreachable for a real ``Change`` —
    ``resolve_change_identity`` degrades through CANONICAL/NORMALIZED/
    REDUCED tiers rather than failing) simply fails to match.
    """
    from .finding_identity import report_canonical_finding_id

    return report_canonical_finding_id(change) == finding_id


def _matches_entity_namespace(compiled: _SegmentGlobMatcher, change: Change) -> bool:
    """Return True if the change's *own* symbol/qualified_name lies in the namespace.

    ADR-044 D3: deliberately does **not** consult ``change.caused_by_type`` —
    that field names the *cause* of the change (which may be a different,
    internal entity from the change's own public subject; see
    :func:`_matches_cause_namespace`), not the change's own identity. Matching
    it here would let a namespace rule aimed at an internal implementation
    detail silently suppress an unrelated finding on a *public* symbol merely
    because its documented cause happens to live in that namespace.
    """
    return _ns_match(compiled, change.symbol) or _ns_match(compiled, change.qualified_name)


def _matches_cause_namespace(compiled: _SegmentGlobMatcher, change: Change) -> bool:
    """Return True if the change's ``caused_by_type`` lies in the namespace.

    ADR-044 D3: the counterpart to :func:`_matches_entity_namespace` — matches
    only the *cause* of the change, not its own subject.
    """
    return _ns_match(compiled, change.caused_by_type)


def _matches_type_pattern(
    compiled: re.Pattern[str],
    change_kind_filter: str | None,
    change: Change,
) -> bool:
    """Return True if *change* is a type-level change matching *compiled*."""
    if change.kind.value not in _TYPE_CHANGE_KINDS:
        return False
    match_symbol = change.symbol.rsplit("::", 1)[0] if "::" in change.symbol else change.symbol
    if not compiled.fullmatch(match_symbol):
        return False
    if change_kind_filter is not None and change.kind.value != change_kind_filter:
        return False
    return True


def _matches_symbol(
    symbol: str | None,
    compiled_pattern: re.Pattern[str] | None,
    change: Change,
) -> bool:
    """Return True if *change.symbol* satisfies the symbol/symbol_pattern selector."""
    if symbol is not None:
        return change.symbol == symbol
    if compiled_pattern is not None:
        return bool(compiled_pattern.fullmatch(change.symbol))
    return True


@dataclass
class Suppression:
    symbol: str | None = None
    symbol_pattern: str | None = None
    type_pattern: str | None = None
    member_name: str | None = None
    """Regex (fullmatch) against the last ``::``-segment of ``change.symbol``.

    Useful for suppressing nested typedefs / fields by bare member name
    independent of the containing type — e.g. ``member_name: "value_type"``
    silences every ``typedef_removed`` whose alias is ``value_type``, no matter
    which allocator/container it came from. May be combined with
    ``type_pattern`` and/or ``change_kind`` for a conjunctive filter.
    """
    change_kind: str | None = None
    reason: str | None = None
    # --- Extended fields ---
    label: str | None = None
    """Optional tag/label for grouping suppressions (e.g. 'workaround', 'internal')."""
    source_location: str | None = None
    """Suppress all changes whose source file path matches this pattern (fnmatch-style).
    Example: ``source_location: "*/internal/*"`` suppresses changes from internal headers."""
    namespace: str | None = None
    """Alias for :attr:`entity_namespace` — specify only one of the two.

    Kept as the primary spelling for backward compatibility; matches only the
    change's *own* identity (``change.symbol`` / ``change.qualified_name``),
    never ``change.caused_by_type`` (ADR-044 D3 — see :attr:`cause_namespace`
    for that). Fnmatch-style glob; ``**`` matches any number of leading
    ``::``-separated segments. Template arguments are stripped before
    matching, so ``foo<int>::bar`` matches ``foo::bar``. Example:
    ``namespace: "**::detail::r1::*"`` suppresses every finding whose own
    subject lies inside a versioned frozen runtime namespace."""
    entity_namespace: str | None = None
    """Canonical spelling of :attr:`namespace` — specify only one of the two."""
    cause_namespace: str | None = None
    """Suppress a change whose ``caused_by_type`` (the root entity responsible
    for a derived/synthetic finding — e.g. the internal type a public leak
    finding names as its cause) lies in this namespace. Same glob semantics as
    :attr:`entity_namespace`. ADR-044 D3: deliberately separate from
    :attr:`entity_namespace` — a public symbol's finding whose *cause* happens
    to be internal must not be suppressible by a rule aimed at hiding
    internal-namespace churn on the *symbol itself*."""
    reachability: str | None = None
    """``"unreachable-only" | "any" | "public-only" | "proven-unreachable-only"``
    — gates whether this rule may match a change flagged
    ``Change.public_reachable``/``Change.reachability_state`` (ADR-044 D1,
    set by the ``MarkReachability`` pipeline step before suppression runs).

    Default depends on the selector shape: a rule using only broad selectors
    (:attr:`namespace`/:attr:`entity_namespace`/:attr:`cause_namespace`/
    :attr:`source_location`) defaults to ``"unreachable-only"`` — it will not
    match a change that turns out to be part of the effective public ABI. A
    rule using a narrow selector (:attr:`symbol`, :attr:`symbol_pattern`,
    :attr:`type_pattern`, :attr:`member_name`) defaults to ``"any"`` —
    unchanged behavior, since naming one exact symbol/type is already an
    audited decision. Set explicitly to override either default."""
    allow_public_break: bool = False
    """When True, permits this rule to suppress a change that is both
    ``Change.public_reachable`` and a member of ``BREAKING_KINDS``/
    ``API_BREAK_KINDS`` — normally refused regardless of :attr:`reachability`
    (ADR-044 D2). Makes an unsafe suppression explicit and reviewable rather
    than an accident of a broad glob."""
    allow_unknown_reachability: bool = False
    """When True, permits this rule — if :attr:`reachability` resolves to
    ``"proven-unreachable-only"`` — to also match a change whose
    ``Change.reachability_state`` is ``ReachabilityState.UNKNOWN`` (graph
    coverage was insufficient to positively prove the change unreachable).
    Has no effect under any other :attr:`reachability` value, since only
    ``"proven-unreachable-only"`` ever distinguishes UNKNOWN from
    proven-unreachable in the first place. Makes an audit-worthy
    absence-of-evidence suppression explicit rather than accidental
    (impact-analysis-layer P0 slice)."""
    expires: date | None = None
    """Optional expiry date (ISO 8601). After this date, the suppression is inactive
    and a warning is emitted. Format: ``expires: 2026-06-01``."""
    # Appended after every pre-existing positional field, and kw_only (Codex
    # review, fresh evidence): Suppression is constructible directly by a
    # programmatic caller (not just via SuppressionList.load's YAML path,
    # which always passes every field by keyword — see that call site), and
    # inserting a field earlier in the list would have silently reassigned
    # every positional argument from that point on for such a caller (e.g. a
    # value previously meant for `reachability` becoming `binding` instead).
    # Same "public-API dataclass, append-and-kw_only" convention as
    # Change.symbol_binding/Change.vtable_covers_unverifiable_layout_gap and
    # AbiSnapshot's own PR #582 fix — see those fields' docstrings.
    binding: str | None = field(default=None, kw_only=True)
    """``"global" | "weak" | "local" | "unique" | "other"`` — the removed
    (or visibility-hidden) symbol's ELF linkage (``Change.symbol_binding``,
    from ``Function.elf_binding``/``Variable.elf_binding``). Only ever set
    on a ``FUNC_REMOVED``/``FUNC_REMOVED_ELF_ONLY``/``VAR_REMOVED``/
    ``FUNC_DELETED_ELF_FALLBACK``/``FUNC_VISIBILITY_CHANGED`` finding; a
    rule combining this with any other ``change_kind`` never matches.
    Conjunctive with every other selector (AND semantics), like
    :attr:`member_name` — combine with :attr:`symbol_pattern` or
    :attr:`namespace` to scope it. Never matches a change whose binding was
    not captured (``Change.symbol_binding is None``) — see
    :func:`_matches_binding`.

    **Provider-side evidence only — not proof a removal is safe.** ``WEAK``
    linkage tells you the *library's own build* used vague/COMDAT linkage
    for this symbol; it does not by itself tell you every *consumer* already
    holds its own copy. AGENTS.md's "Linkage-blind removal" entry records
    the concrete counterexample this codebase already had to unlearn twice:
    a public header carrying ``extern template struct Box<int>;`` tells
    consumer TUs *not* to instantiate, while the library's own explicit
    instantiation still emits a ``WEAK``/COMDAT definition — so a consumer
    can hold an undefined reference to a symbol this repo's own model would
    still report as ``WEAK``. A ``binding: weak`` rule narrows a removal
    finding to the *common* WEAK-COMDAT-inline case (every consumer already
    emitted its own copy); it is not, on its own, sufficient justification
    for suppression — confirm the removed symbol is not also documented
    `extern template`/explicit-instantiation surface (or otherwise known to
    have real out-of-library callers relying on the library's definition)
    before relying on this selector alone. A separate, binding-less rule
    still catches (does not suppress) a ``GLOBAL``/STRONG removal on the
    same symbol set."""
    finding_id: str | None = field(default=None, kw_only=True)
    """Exact-match a change's ``canonical_finding_id`` (schema 2.36) — the
    producer-agnostic identity :func:`~abicheck.finding_identity.
    report_canonical_finding_id` computes. Unlike :attr:`symbol`/
    :attr:`type_pattern`/:attr:`member_name` (raw spellings that anonymous
    struct/union/enum naming can differ on between CastXML and Clang), this
    selector is stable across an ``--ast-frontend`` switch on the same
    underlying change. Get the value from either report shape's
    ``canonical_finding_id`` field.

    A **primary, standalone-sufficient selector**: alone satisfies
    :func:`_validate_selectors`'s "at least one selector" requirement, and
    counts as narrow for :attr:`reachability`'s broad/narrow default and
    :attr:`allow_public_break`'s gate, since it already names one specific
    finding. Combinable with other selectors (AND semantics), though
    redundant — the id already encodes kind and identity."""
    _compiled_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_type_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_member_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_source_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_entity_namespace_pattern: _SegmentGlobMatcher | None = field(
        default=None, init=False, repr=False
    )
    _compiled_cause_namespace_pattern: _SegmentGlobMatcher | None = field(
        default=None, init=False, repr=False
    )
    _resolved_reachability: str = field(default="any", init=False, repr=False)
    _is_broad_selector: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.namespace is not None and self.entity_namespace is not None:
            raise ValueError(
                "Suppression fields 'namespace' and 'entity_namespace' are "
                "aliases for the same selector — specify only one"
            )
        # Normalize + validate here too, not just in SuppressionList.load's
        # YAML path -- constructing Suppression(finding_id=...) directly
        # bypassed this entirely, silently creating a rule that can never
        # match anything real (Codex review).
        self.finding_id = parse_finding_id(self.finding_id)
        effective_entity_ns = self.entity_namespace if self.entity_namespace is not None else self.namespace
        _validate_selectors(
            has_symbol=self.symbol is not None,
            has_sym_pattern=self.symbol_pattern is not None,
            has_type_pattern=self.type_pattern is not None,
            has_member_name=self.member_name is not None,
            has_source_location=self.source_location is not None,
            has_namespace=effective_entity_ns is not None or self.cause_namespace is not None,
            has_finding_id=self.finding_id is not None,
        )
        # Compile regex eagerly — malformed patterns fail at load time, not match time.
        # Uses fullmatch semantics: the pattern must match the entire symbol name.
        # Use explicit '.*' anchors in the pattern if partial matching is intended.
        self._compiled_pattern = _compile_pattern(self.symbol_pattern, "symbol_pattern")
        self._compiled_type_pattern = _compile_pattern(self.type_pattern, "type_pattern")
        self._compiled_member_pattern = _compile_pattern(self.member_name, "member_name")
        self._compiled_source_pattern = _compile_glob(self.source_location, "source_location")
        self._compiled_entity_namespace_pattern = _compile_namespace_glob(effective_entity_ns, "namespace")
        self._compiled_cause_namespace_pattern = _compile_namespace_glob(
            self.cause_namespace, "cause_namespace"
        )
        # Validate change_kind against known enum values
        if self.change_kind is not None and self.change_kind not in _VALID_CHANGE_KINDS:
            valid = ", ".join(sorted(_VALID_CHANGE_KINDS))
            raise ValueError(
                f"Unknown change_kind {self.change_kind!r}. "
                f"Valid values: {valid}"
            )
        if self.reachability is not None and self.reachability not in _VALID_REACHABILITY:
            raise ValueError(
                f"Invalid reachability {self.reachability!r}. "
                f"Valid values: {sorted(_VALID_REACHABILITY)}"
            )
        if self.binding is not None and (
            # isinstance check first (Codex review, fresh evidence): a YAML
            # value the dataclass's own `str | None` annotation doesn't
            # enforce at runtime -- a list (`binding: [weak]`) or mapping --
            # is unhashable, so `not in` on the frozenset below would raise
            # TypeError instead of this constructor's documented ValueError
            # contract, escaping SuppressionList.load's/the CLI's/the
            # service layer's ValueError-only handling as an internal
            # exception rather than a normal invalid-config error.
            not isinstance(self.binding, str)
            or self.binding not in _VALID_BINDING
        ):
            raise ValueError(
                f"Invalid binding {self.binding!r}. "
                f"Valid values: {sorted(_VALID_BINDING)}"
            )
        # ADR-044 D2 (Codex review): SuppressionList.load already rejects a
        # non-bool allow_public_break via _parse_allow_public_break, but a
        # programmatic caller can construct Suppression directly — Python
        # does not enforce the dataclass field's `bool` annotation at
        # runtime, so e.g. allow_public_break="false" would otherwise pass
        # this safety-critical override's truthiness check as True.
        if not isinstance(self.allow_public_break, bool):
            raise ValueError(
                "'allow_public_break' must be a boolean (true/false), got "
                f"{self.allow_public_break!r}"
            )
        if not isinstance(self.allow_unknown_reachability, bool):
            raise ValueError(
                "'allow_unknown_reachability' must be a boolean (true/false), "
                f"got {self.allow_unknown_reachability!r}"
            )
        # ADR-044 D2: a rule with no explicit reachability defaults to
        # "unreachable-only" when it has a broad selector (namespace/
        # entity_namespace/cause_namespace/source_location) and no *primary*
        # narrow selector (symbol/symbol_pattern/type_pattern — the mutually
        # exclusive trio `_validate_selectors` already treats as the rule's
        # main selector). The same broad/narrow split also decides whether
        # allow_public_break is required at all (_passes_public_break_gate).
        #
        # A primary narrow selector present alongside a broad one exempts the
        # rule from "broad" (Codex review): `symbol="ns::detail::T",
        # source_location="*/internal/*"` already names the exact audited
        # entity — the source_location addition can only *narrow* which
        # changes on that one entity match (AND semantics), never introduce
        # an unaudited match the bare `symbol:` selector wouldn't already
        # have matched, so it must not lose the narrow-selector "unchanged
        # behavior" guarantee.
        #
        # member_name is deliberately NOT a primary selector for this
        # purpose, unlike symbol/symbol_pattern/type_pattern: by itself it
        # matches a bare trailing name across *any* containing type/
        # namespace (its own docstring: "independent of the containing
        # type"), so combined with a namespace/source_location filter, that
        # filter is still doing the real scoping work, not merely narrowing
        # an already-pinned-down match — `namespace: "**::detail::**",
        # member_name: "value_type"` stays broad.
        has_primary_narrow_selector = bool(
            self.symbol is not None
            or self.symbol_pattern is not None
            or self.type_pattern is not None
            or self.finding_id is not None
        )
        has_broad_shaped_selector = bool(
            effective_entity_ns is not None
            or self.cause_namespace is not None
            or self.source_location is not None
        )
        self._is_broad_selector = has_broad_shaped_selector and not has_primary_narrow_selector
        self._resolved_reachability = self.reachability or (
            "unreachable-only" if self._is_broad_selector else "any"
        )

    def is_expired(self, today: date | None = None) -> bool:
        """Return True if this suppression has passed its expiry date."""
        if self.expires is None:
            return False
        check_date = today or date.today()
        return check_date > self.expires

    def _selector_match(self, change: Change, today: date | None = None) -> bool:
        """Return True if this rule's selectors match *change*, ignoring the
        reachability/``allow_public_break`` gates (see :meth:`matches`).

        Expired suppressions (past ``expires`` date) never match.

        Pattern matching uses fullmatch — the pattern must cover the entire
        mangled symbol name. Use '.*foo.*' for substring matching.

        ``source_location`` uses fnmatch-style glob against
        ``change.source_location``.

        type_pattern only matches changes whose kind is a type-level change
        (TYPE_*, ENUM_*, TYPEDEF_*, etc.), preventing type whitelists from
        suppressing symbol-level changes.
        """
        # Expired suppressions are inactive
        if self.is_expired(today):
            return False

        # source_location: match against change.source_location if present.
        # Fall through to check remaining selectors conjunctively (AND logic).
        if self._compiled_source_pattern is not None:
            if not _matches_source_location(self._compiled_source_pattern, change):
                return False

        # member_name: fullmatch the last "::"-segment of change.symbol.
        # Applied conjunctively so it can combine with type_pattern / change_kind.
        if self._compiled_member_pattern is not None:
            if not _matches_member_name(self._compiled_member_pattern, change):
                return False

        # binding: ELF linkage of the removed symbol. Conjunctive, same
        # position as member_name — applies ahead of the type_pattern early
        # return so it also narrows a type_pattern-primary rule (in
        # practice, symbol_binding is only ever set on func/var removal
        # kinds, so it's a no-op there; kept general rather than special-cased).
        if self.binding is not None:
            if not _matches_binding(self.binding, change):
                return False

        # finding_id: canonical (backend-independent) identity, checked
        # early alongside the other conjunctive selectors above.
        if self.finding_id is not None:
            if not _matches_finding_id(self.finding_id, change):
                return False

        # entity_namespace / namespace: match the change's own identity only.
        if self._compiled_entity_namespace_pattern is not None:
            if not _matches_entity_namespace(self._compiled_entity_namespace_pattern, change):
                return False

        # cause_namespace: match the change's caused_by_type only.
        if self._compiled_cause_namespace_pattern is not None:
            if not _matches_cause_namespace(self._compiled_cause_namespace_pattern, change):
                return False

        # type_pattern: only matches type-level changes (TYPE_*, ENUM_*, TYPEDEF_*, …).
        # Returns early (True/False) because type_pattern is a primary selector.
        if self._compiled_type_pattern is not None:
            return _matches_type_pattern(self._compiled_type_pattern, self.change_kind, change)

        # Check symbol match
        if not _matches_symbol(self.symbol, self._compiled_pattern, change):
            return False

        # Check change_kind match (if specified)
        if self.change_kind is not None and change.kind.value != self.change_kind:
            return False

        return True

    def _passes_reachability_gate(self, change: Change) -> bool:
        """ADR-044 D2: gate on :attr:`reachability` (resolved default or explicit).

        ``allow_public_break: true`` is an explicit, narrowly-scoped override
        for exactly the public-reachable + breaking case: it must not be
        neutered by a broad rule's own ``reachability="unreachable-only"``
        default, or setting ``allow_public_break`` on a ``namespace`` rule
        would silently do nothing. A public-reachable but *non*-breaking
        change is unaffected — ``allow_public_break`` only concerns the
        failure mode this ADR exists to prevent.
        """
        if self._resolved_reachability == "any":
            return True
        if (
            change.public_reachable
            and self.allow_public_break
            and (change.kind in BREAKING_KINDS or change.kind in API_BREAK_KINDS)
        ):
            return True
        if self._resolved_reachability == "unreachable-only":
            return not change.public_reachable
        if self._resolved_reachability == "proven-unreachable-only":
            if change.reachability_state == ReachabilityState.PROVEN_UNREACHABLE:
                return True
            return (
                change.reachability_state == ReachabilityState.UNKNOWN
                and self.allow_unknown_reachability
            )
        return change.public_reachable  # "public-only"

    def _passes_public_break_gate(self, change: Change) -> bool:
        """ADR-044 D2: a *broad* rule (namespace/entity_namespace/
        cause_namespace/source_location) suppressing a public-reachable
        BREAKING/API_BREAK change needs ``allow_public_break: true``,
        regardless of its resolved :attr:`reachability`.

        A *narrow* rule (``symbol``/``symbol_pattern``/``type_pattern``/
        ``member_name`` — naming one exact symbol/type) is exempt from this
        gate entirely: it is already the deliberate, audited case suppression
        exists for, independent of whether that symbol happens to be public
        or an internal type that leaks — this is the ADR's own "unchanged
        behavior for narrow selectors" guarantee. The failure mode this gate
        exists to prevent is a *glob* over-matching something its author
        never reasoned about, not an author explicitly naming one symbol.
        """
        if not self._is_broad_selector:
            return True
        if self.allow_public_break:
            return True
        if not change.public_reachable:
            return True
        return change.kind not in BREAKING_KINDS and change.kind not in API_BREAK_KINDS

    def matches(self, change: Change, today: date | None = None) -> bool:
        """Return True if this suppression rule applies to *change*.

        A rule "applies" when its selectors match (:meth:`_selector_match`)
        **and** it clears the reachability / ``allow_public_break`` gates
        (ADR-044 D2). Use :meth:`would_withhold` to detect the "selectors
        matched but a gate withheld it" case for diagnostics.
        """
        if not self._selector_match(change, today):
            return False
        return self._passes_reachability_gate(change) and self._passes_public_break_gate(change)

    def selector_matches(self, change: Change, today: date | None = None) -> bool:
        """Return True if this rule's selectors alone match *change*.

        Public alias for :meth:`_selector_match`, deliberately skipping the
        reachability / ``allow_public_break`` gates :meth:`matches` applies
        on top. Those gates exist to guard against a suppression rule
        *hiding* a finding it never should have — a concern that doesn't
        apply to a consumer that keeps the finding visible and only changes
        its verdict (``abicheck/reclassify.py``'s ``ReclassifyRule``, which
        reuses this class purely for its selector grammar rather than
        re-implementing the glob/regex machinery a second time).
        """
        return self._selector_match(change, today)

    def would_withhold(self, change: Change, today: date | None = None) -> bool:
        """True if this rule's selectors match *change*, *change* is a
        public-reachable ``BREAKING``/``API_BREAK`` finding, and the
        ``allow_public_break`` gate is the reason this rule does not suppress
        it (ADR-044 D2/D4) — i.e. exactly the case the
        ``suppression_would_hide_public_break`` diagnostic describes.

        Deliberately narrower than "any gate failure" (Codex review): a rule
        correctly declining to match for an unrelated reachability-scoping
        reason — e.g. ``reachability: public-only`` correctly skipping a
        genuinely unreachable change, or the ``unreachable-only`` default
        correctly skipping a public-reachable but merely ``RISK``-classified
        change — is the rule intentionally not applying, not an overreach.
        The original, broader definition produced a diagnostic claiming "the
        symbol is public-reachable" and suggesting ``allow_public_break``
        even when the change was not public-reachable at all, or when
        ``allow_public_break`` would not have changed the outcome (it only
        ever bypasses the reachability gate for a ``BREAKING``/``API_BREAK``
        change — see :meth:`_passes_reachability_gate`).
        """
        if not self._selector_match(change, today):
            return False
        if not (change.public_reachable and (change.kind in BREAKING_KINDS or change.kind in API_BREAK_KINDS)):
            return False
        return not self._passes_public_break_gate(change)

    def would_withhold_unknown_reachability(
        self, change: Change, today: date | None = None
    ) -> bool:
        """True if this rule's selectors match *change*, its resolved
        :attr:`reachability` is ``"proven-unreachable-only"``, *change*'s
        ``Change.reachability_state`` is ``ReachabilityState.UNKNOWN``, and
        ``allow_unknown_reachability`` is not set — i.e. exactly the case the
        ``suppression_reachability_unknown`` diagnostic describes
        (impact-analysis-layer P0 slice).

        Only ``"proven-unreachable-only"`` ever distinguishes UNKNOWN from
        proven-unreachable at all — the original ``"unreachable-only"``
        default treats both identically (via the boolean
        ``Change.public_reachable``) for backward compatibility, so a rule
        using that default can never trigger this diagnostic.
        """
        if self._resolved_reachability != "proven-unreachable-only":
            return False
        if not self._selector_match(change, today):
            return False
        if change.reachability_state != ReachabilityState.UNKNOWN:
            return False
        return not self.allow_unknown_reachability


def _parse_expires(expires_raw: object, entry_index: int) -> date | None:
    """Parse and validate an ``expires`` value from a suppression entry.

    Returns a :class:`date` or *None*.  Raises :class:`ValueError` on
    invalid date formats.
    """
    if expires_raw is None:
        return None
    if isinstance(expires_raw, date):
        # datetime is a subclass of date; convert to date to avoid
        # TypeError when comparing datetime to date in is_expired()
        if isinstance(expires_raw, datetime):
            return expires_raw.date()
        return expires_raw
    try:
        return date.fromisoformat(str(expires_raw))
    except ValueError as e:
        raise ValueError(
            f"Suppression entry {entry_index}: invalid 'expires' date {expires_raw!r} "
            "(expected ISO 8601 format, e.g. 2026-06-01)"
        ) from e


def _parse_allow_public_break(raw: object, entry_index: int) -> bool:
    """Parse and validate ``allow_public_break`` from a suppression entry.

    ADR-044 D2 (Codex review): this is the explicit override for suppressing
    a public-reachable BREAKING/API_BREAK change, so it must not silently
    coerce a truthy-but-wrong value — ``bool("false")`` is ``True`` in Python,
    so a stray quoted string in a hand- or template-generated YAML file
    (``allow_public_break: "false"``) would otherwise silently enable the
    exact override this safety gate exists to require an explicit, reviewed
    ``true`` for. Only an actual YAML boolean (``true``/``false``, unquoted)
    or an absent key (default ``False``) is accepted.
    """
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    raise ValueError(
        f"Suppression entry {entry_index}: 'allow_public_break' must be a boolean "
        f"(true/false), got {raw!r}"
    )


def _parse_allow_unknown_reachability(raw: object, entry_index: int) -> bool:
    """Parse and validate ``allow_unknown_reachability`` from a suppression
    entry — same strict-boolean contract as :func:`_parse_allow_public_break`
    (impact-analysis-layer P0 slice)."""
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    raise ValueError(
        f"Suppression entry {entry_index}: 'allow_unknown_reachability' must be "
        f"a boolean (true/false), got {raw!r}"
    )


@dataclass
class SuppressionOutcome:
    """Result of :meth:`SuppressionList.evaluate` for one change (ADR-044 D4).

    ``withheld_unknown_rule`` (impact-analysis-layer P0 slice) is the
    ``"proven-unreachable-only"`` analogue of ``withheld_rule``: set when a
    rule's selectors matched but the change's ``reachability_state`` was
    ``UNKNOWN`` rather than proven-unreachable, distinct from the
    public-reachable-break case ``withheld_rule`` covers.

    ``matched_rule`` (G29 Phase 3 slice 2, ADR-052 follow-up) is the rule
    that actually suppressed the change when ``suppressed`` is True — before
    this, a successful match returned no record of *which* rule fired, so a
    caller moving the change into ``DiffResult.suppressed_changes`` had
    nothing to attribute the suppression to.
    """

    suppressed: bool
    withheld_rule: Suppression | None = None
    withheld_unknown_rule: Suppression | None = None
    matched_rule: Suppression | None = None

    def rule_label(self) -> str | None:
        """Display label for :attr:`matched_rule`: its ``label``, falling
        back to ``reason`` (both are optional/free-form on a ``Suppression``
        rule, so this can still be ``None``). ``None`` when nothing matched.
        Used by every call site that stamps ``Change.suppression_rule`` on a
        change moved into ``DiffResult.suppressed_changes``, so the
        label-vs-reason fallback logic lives in one place.
        """
        if self.matched_rule is None:
            return None
        return self.matched_rule.label or self.matched_rule.reason


class SuppressionList:
    def __init__(
        self,
        suppressions: list[Suppression],
        *,
        source_sha256: str | None = None,
    ) -> None:
        self._suppressions = suppressions
        #: sha256 of the exact raw bytes :meth:`load` read, when these rules
        #: came from a file. Captured during that one read so a consumer
        #: never has to re-read the path to digest it: the file could have
        #: changed in between, and the digest would then authenticate content
        #: that did not produce these rules (Codex review, ADR-049 D6 replay).
        self.source_sha256 = source_sha256

    @classmethod
    def merge(cls, a: SuppressionList, b: SuppressionList) -> SuppressionList:
        """Return a new SuppressionList combining rules from both lists."""
        return cls(suppressions=[*a._suppressions, *b._suppressions])

    @classmethod
    def load(cls, path: Path, *, require_justification: bool = False) -> SuppressionList:
        """Load suppression rules from a YAML file.

        If *require_justification* is True, every rule must have a non-empty
        ``reason`` field or a ``ValueError`` is raised.

        Raises ValueError on schema violations, unknown keys, bad regex,
        or invalid change_kind values.
        Raises OSError if the file cannot be read.
        """
        try:
            # Raw bytes, digested and decoded from one read: `read_text()`
            # would translate newlines before hashing, making a CRLF file
            # digest identically to its LF twin.
            raw_bytes = path.read_bytes()
        except OSError as e:
            raise OSError(f"Cannot read suppression file {path}: {e}") from e
        digest = hashlib.sha256(raw_bytes).hexdigest()
        text = raw_bytes.decode("utf-8")

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in suppression file: {e}") from e
        # Raw, unresolved finding_id scalar text per entry index -- see
        # raw_finding_ids_by_index's own docstring for why this can't be
        # recovered from `data` (the already-parsed structure) alone.
        raw_finding_ids = raw_finding_ids_by_index(text)

        if not isinstance(data, dict):
            raise ValueError("Suppression file must be a YAML mapping")

        version = data.get("version")
        if version != 1:
            raise ValueError(f"Unsupported suppression file version: {version!r} (expected 1)")

        raw_suppressions = data.get("suppressions")
        if raw_suppressions is None:
            # A file with no `suppressions:` key is a valid, empty rule set —
            # it still has content that can drift, so it keeps its digest
            # (ADR-049 D6) exactly like the populated return below.
            return cls([], source_sha256=digest)
        if not isinstance(raw_suppressions, list):
            raise ValueError("'suppressions' must be a list")

        suppressions: list[Suppression] = []
        for i, item in enumerate(raw_suppressions):
            if not isinstance(item, dict):
                raise ValueError(f"Suppression entry {i} must be a mapping")
            # Reject unknown keys — catches typos like 'symbl' or 'cahnge_kind'
            unknown = set(item.keys()) - _KNOWN_ENTRY_KEYS
            if unknown:
                raise ValueError(
                    f"Suppression entry {i} has unknown key(s): {sorted(unknown)}. "
                    f"Allowed keys: {sorted(_KNOWN_ENTRY_KEYS)}"
                )
            # Parse expires date
            expires = _parse_expires(item.get("expires"), i)
            allow_public_break = _parse_allow_public_break(item.get("allow_public_break"), i)
            allow_unknown_reachability = _parse_allow_unknown_reachability(
                item.get("allow_unknown_reachability"), i
            )
            try:
                sup = Suppression(
                    symbol=item.get("symbol"),
                    symbol_pattern=item.get("symbol_pattern"),
                    type_pattern=item.get("type_pattern"),
                    member_name=item.get("member_name"),
                    change_kind=item.get("change_kind"),
                    reason=item.get("reason"),
                    label=item.get("label"),
                    source_location=item.get("source_location"),
                    namespace=item.get("namespace"),
                    entity_namespace=item.get("entity_namespace"),
                    cause_namespace=item.get("cause_namespace"),
                    binding=item.get("binding"),
                    finding_id=parse_finding_id(
                        raw_finding_ids.get(i, item.get("finding_id"))
                    ),
                    reachability=item.get("reachability"),
                    allow_public_break=allow_public_break,
                    allow_unknown_reachability=allow_unknown_reachability,
                    expires=expires,
                )
            except ValueError as e:
                raise ValueError(f"Suppression entry {i}: {e}") from e
            if require_justification and not sup.reason:
                raise ValueError(
                    f"Suppression rule {i} has no 'reason' field. "
                    "All suppression rules must include a justification "
                    "when suppression.require_justification is set in "
                    ".abicheck.yml."
                )
            suppressions.append(sup)

        return cls(suppressions, source_sha256=digest)

    def is_suppressed(self, change: Change, today: date | None = None) -> bool:
        """Return True if any active (non-expired) suppression rule matches the given change."""
        return any(s.matches(change, today=today) for s in self._suppressions)

    def needs_reachability_evidence(self) -> bool:
        """ADR-044 D1 (Codex review): True if at least one rule could ever
        actually consult ``Change.public_reachable`` when matching.

        A rule that is narrow (not :attr:`Suppression._is_broad_selector`)
        with the default (or an explicit ``"any"``) :attr:`reachability
        <Suppression.reachability>` short-circuits both
        ``_passes_reachability_gate`` and ``_passes_public_break_gate``
        without ever reading the tag. A suppression file containing only
        such rules — the common case, e.g. a handful of exact ``symbol:``
        waivers — gains nothing from ``MarkReachability``'s public-surface
        walk; ``compute_leak_paths`` is expensive enough (the exact walk
        ``DetectInternalLeaks`` already performs) that running it for
        evidence nothing will ever consult is pure waste on every
        comparison. False only when *every* rule is provably indifferent to
        reachability.
        """
        return any(
            s._is_broad_selector or s._resolved_reachability != "any"
            for s in self._suppressions
        )

    def evaluate(self, change: Change, today: date | None = None) -> SuppressionOutcome:
        """Like :meth:`is_suppressed`, but also reports a withheld match.

        ADR-044 D4: when no rule suppresses *change* but at least one rule's
        selectors matched and was withheld by the reachability /
        ``allow_public_break`` gate (:meth:`Suppression.would_withhold`), the
        first such rule is returned so the caller can emit a
        ``SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK`` diagnostic explaining why.
        A rule that actually suppresses the change always wins outright (no
        diagnostic needed — the change is gone from the report either way).

        Independently (impact-analysis-layer P0 slice), the first rule
        withheld because its ``"proven-unreachable-only"`` gate could not
        prove *change* unreachable (:meth:`Suppression.would_withhold_unknown_reachability`)
        is returned as ``withheld_unknown_rule``, so the caller can emit a
        ``SUPPRESSION_REACHABILITY_UNKNOWN`` diagnostic.
        """
        withheld_rule: Suppression | None = None
        withheld_unknown_rule: Suppression | None = None
        for s in self._suppressions:
            if s.matches(change, today=today):
                return SuppressionOutcome(suppressed=True, matched_rule=s)
            if withheld_rule is None and s.would_withhold(change, today=today):
                withheld_rule = s
            if withheld_unknown_rule is None and s.would_withhold_unknown_reachability(
                change, today=today
            ):
                withheld_unknown_rule = s
        return SuppressionOutcome(
            suppressed=False,
            withheld_rule=withheld_rule,
            withheld_unknown_rule=withheld_unknown_rule,
        )

    def expired_rules(self, today: date | None = None) -> list[Suppression]:
        """Return all rules that have passed their expiry date."""
        return [s for s in self._suppressions if s.is_expired(today)]

    def rules_by_label(self, label: str) -> list[Suppression]:
        """Return all rules with the given label."""
        return [s for s in self._suppressions if s.label == label]

    def rule_identities(self) -> tuple[str, ...]:
        """One canonical, machine-facing identity string per loaded rule.

        ADR-049 D7's effective configuration carries the selected suppression
        source as ``{rules: [...], sha256: "..."}``
        (:class:`~abicheck.compatibility_evaluation_config.SuppressionConfig`);
        this is what fills ``rules``. The digest already covers the source file
        byte-for-byte, so these strings exist for the *receipt* — so a replayed
        decision can be read without re-opening the file — not as a second
        integrity check.

        Deliberately different from ``cli_compare_fold._suppression_rule_label``,
        which renders a rule for a human reading an audit report (preferring its
        ``label``/``reason`` prose and falling back to a file position):

        - every populated selector/gate field is included, so two rules that
          differ in any matching-relevant way get different identities;
        - ``reason`` is excluded — it is prose that changes what a reviewer
          reads, never what the rule matches, and the source digest already
          records that it changed;
        - fields are emitted in declaration order with no positional index, so
          the identity depends only on the rule's own content;
        - each value is rendered with ``repr()``, so a selector that itself
          contains the ``|`` separator or an ``=`` (routine in a regex
          selector — ``symbol_pattern: "a|change_kind=x"``) cannot render
          the same identity as a different rule with those as separate
          fields, and a ``date`` reads as a date rather than as a bare
          number triple.

        Derived generically from :class:`Suppression`'s own dataclass fields
        (skipping the compiled/resolved ``init=False`` internals), so a rule
        field added later is covered without touching this method.
        """
        identities: list[str] = []
        for rule in self._suppressions:
            parts = [
                f"{f.name}={getattr(rule, f.name)!r}"
                for f in dataclasses.fields(rule)
                if f.init
                and f.name != "reason"
                and getattr(rule, f.name) not in (None, False)
            ]
            identities.append("|".join(parts))
        return tuple(identities)

    def audit(
        self,
        changes: list[Change],
        today: date | None = None,
        *,
        near_expiry_days: int = 30,
        breaking_kinds: frozenset[ChangeKind] | None = None,
        policy_file: object | None = None,
    ) -> SuppressionAudit:
        """Audit suppression rules against a set of changes.

        Returns a :class:`SuppressionAudit` with:
        - ``stale_rules``: suppressions that matched zero changes (misconfigured?)
        - ``high_risk_matches``: suppressions that matched a change classified
          as ``BREAKING`` -- via *breaking_kinds* membership, or, when
          *policy_file* is given, via the same per-finding resolver
          (:func:`abicheck.severity.effective_verdict_for_change`) the
          comparison's own verdict/severity/exit-code already went through
          (see below)
        - ``expired_rules``: rules past their expiry date
        - ``near_expiry_rules``: rules expiring within *near_expiry_days*
        - ``match_counts``: per-rule match count

        *breaking_kinds* defaults to the built-in :data:`BREAKING_KINDS`; it's
        only consulted when *policy_file* is ``None`` (see below) -- a caller
        that has a real ``PolicyFile`` should pass it instead of just its
        derived breaking set.

        *policy_file* is optional and defaults to ``None`` (unchanged prior
        behavior for every existing caller that doesn't pass it, using
        *breaking_kinds* alone). When given, each change's "high risk"
        classification is instead decided by
        :func:`abicheck.severity.effective_verdict_for_change` -- the same
        resolver ``PolicyFile.compute_verdict``/``classify_effective_change``
        already use -- rather than *breaking_kinds* membership. A bare
        kind-wide set cannot express what that resolver's own precedence
        chain (a pipeline ``effective_verdict`` modulation, then a
        selector-scoped ``reclassify:`` rule, then a kind-global
        ``overrides:`` entry, then the base policy, each still subject to
        the frozen-namespace verdict floor) actually decided for one
        specific change (Codex review, two rounds: a `reclassify:` rule
        promoting one specific normally-compatible finding to ``break`` was
        invisible to a *breaking_kinds*-only check at all; a naive
        reclassify-only check that bypassed ``effective_verdict``
        precedence and the frozen-namespace floor was itself still wrong in
        the opposite direction).
        """
        if near_expiry_days < 0:
            raise ValueError("near_expiry_days must be non-negative")
        check_date = today or date.today()
        near_expiry_cutoff = check_date + timedelta(days=near_expiry_days)
        effective_breaking_kinds = (
            BREAKING_KINDS if breaking_kinds is None else breaking_kinds
        )

        match_counts: dict[int, int] = {i: 0 for i in range(len(self._suppressions))}
        high_risk: list[tuple[Suppression, Change]] = []

        for c in changes:
            if policy_file is not None:
                # Delegate to the shared per-finding resolver rather than
                # re-checking `reclassify:` alone (Codex review, fresh
                # evidence): a bare reclassify-only check bypasses both
                # `change.effective_verdict`'s own precedence (a pipeline
                # modulation must win outright, matching/overriding
                # `reclassify:` the same way effective_verdict_for_change's
                # own topmost branch does) and the frozen-namespace floor (a
                # `reclassify:`/override resolution below a frozen symbol's
                # base-policy verdict is rejected there, not honored). Using
                # the same resolver as PolicyFile.compute_verdict/
                # classify_effective_change means this audit's "high risk"
                # classification can never disagree with the verdict the
                # comparison itself actually produced.
                from .severity import effective_verdict_for_change

                is_breaking = (
                    effective_verdict_for_change(c, policy_file=policy_file, today=today)
                    == Verdict.BREAKING
                )
            else:
                is_breaking = c.kind in effective_breaking_kinds
            for i, s in enumerate(self._suppressions):
                if s.matches(c, today=today):
                    match_counts[i] += 1
                    if is_breaking:
                        high_risk.append((s, c))

        stale = [
            self._suppressions[i]
            for i, count in match_counts.items()
            if count == 0 and not self._suppressions[i].is_expired(today)
        ]

        expired = self.expired_rules(today)

        near_expiry = [
            s for s in self._suppressions
            if s.expires is not None
            and not s.is_expired(today)
            and s.expires <= near_expiry_cutoff
        ]

        return SuppressionAudit(
            stale_rules=stale,
            high_risk_matches=high_risk,
            expired_rules=expired,
            near_expiry_rules=near_expiry,
            match_counts={i: match_counts[i] for i in match_counts},
            total_rules=len(self._suppressions),
        )

    def check_expired_strict(self, today: date | None = None) -> list[tuple[int, Suppression]]:
        """Return ``(index, rule)`` pairs for all expired rules.

        Used by ``--strict-suppressions`` to enumerate expired rules with
        their 0-based index in the suppression file.
        """
        check_date = today or date.today()
        return [
            (i, s) for i, s in enumerate(self._suppressions)
            if s.is_expired(check_date)
        ]

    def __len__(self) -> int:
        return len(self._suppressions)

    def __repr__(self) -> str:
        return f"SuppressionList({len(self._suppressions)} rules)"


@dataclass
class SuppressionAudit:
    """Result of auditing suppression rules against detected changes."""
    stale_rules: list[Suppression]
    """Rules that matched zero changes (likely stale or misconfigured)."""
    high_risk_matches: list[tuple[Suppression, Change]]
    """Suppressions that matched BREAKING changes (high risk — should require reason)."""
    expired_rules: list[Suppression]
    """Rules past their expiry date."""
    near_expiry_rules: list[Suppression]
    """Rules expiring within the near-expiry window."""
    match_counts: dict[int, int]
    """Per-rule match count (rule index → number of matched changes)."""
    total_rules: int
    """Total number of suppression rules."""

    @property
    def has_issues(self) -> bool:
        """True if the audit found any issues worth reporting."""
        return bool(
            self.stale_rules
            or self.high_risk_matches
            or self.expired_rules
            or self.near_expiry_rules
        )

    def summary(self) -> str:
        """Human-readable audit summary."""
        lines = [f"Suppression audit: {self.total_rules} rules"]
        if self.stale_rules:
            # Codex review, fresh evidence: this used to also print up to 5
            # per-rule detail lines here, naming each stale rule by only its
            # first populated selector -- two rules sharing that first
            # selector (e.g. the same `symbol` but a different `change_kind`)
            # rendered identically, misdirecting a reader to the wrong rule.
            # A stable, fully-disambiguated identifier needs every matching
            # selector (see cli_compare_fold._suppression_rule_label), which
            # this module has no reason to duplicate -- callers that want
            # per-rule detail (e.g. compare --audit-suppressions's markdown/
            # JSON output) list stale_rules themselves; this summary only
            # reports the count, matching the expired/near-expiry buckets
            # below.
            lines.append(f"  ⚠ {len(self.stale_rules)} stale rule(s) matched nothing")
        if self.high_risk_matches:
            lines.append(f"  ⚠ {len(self.high_risk_matches)} suppression(s) matched BREAKING changes")
            for _sup, change in self.high_risk_matches[:5]:
                lines.append(f"    - {change.kind.value}: {change.symbol}")
        if self.expired_rules:
            lines.append(f"  ⚠ {len(self.expired_rules)} expired rule(s)")
        if self.near_expiry_rules:
            lines.append(f"  ℹ {len(self.near_expiry_rules)} rule(s) expiring soon")
        if not self.has_issues:
            lines.append("  ✓ No issues found")
        return "\n".join(lines)


def suggest_suppressions(
    changes: list[dict[str, object]],
    *,
    expiry_days: int = 180,
    today: date | None = None,
) -> str:
    """Generate candidate suppression rules as YAML from a list of change dicts.

    *changes* is a list of change dictionaries as found in the ``"changes"``
    key of a JSON diff result (each must have ``"kind"`` and ``"symbol"``).

    Returns a YAML string with ``# TODO`` comments for unreviewed rules.
    """
    check_date = today or date.today()
    expires_date = check_date + timedelta(days=expiry_days)
    expires_str = expires_date.isoformat()

    lines: list[str] = [
        "# Auto-generated suppression candidates from abicheck compare",
        "# Review each rule and add a justification before using",
        "version: 1",
        "suppressions:",
    ]

    for change in changes:
        raw_kind = change.get("kind")
        raw_symbol = change.get("symbol")
        if raw_kind is None or raw_symbol is None:
            continue
        kind = str(raw_kind)
        symbol = str(raw_symbol)
        if not kind or not symbol:
            continue

        # Use type_pattern for type-level changes, symbol for symbol-level
        if kind in _TYPE_CHANGE_KINDS:
            # Strip member suffix (e.g. "Color::GREEN" → "Color") so the
            # generated rule matches Suppression.matches() semantics.
            type_name = symbol.rsplit("::", 1)[0] if "::" in symbol else symbol
            lines.append(f"  - type_pattern: {_yaml_quote(type_name)}")
        else:
            lines.append(f"  - symbol: {_yaml_quote(symbol)}")
        lines.append(f"    change_kind: {_yaml_quote(kind)}")
        lines.append('    reason: ""  # TODO: add justification')
        lines.append(f"    expires: {_yaml_quote(expires_str)}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _yaml_quote(value: str) -> str:
    """Quote a string for safe YAML output, escaping special characters."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
