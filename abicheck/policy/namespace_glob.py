# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Namespace-glob matching -- the string primitive behind suppression selectors.

ADR-061 D5: ``suppression.py`` sat seven lines under the 2000-line hard cap.
This is the one self-contained cluster inside it -- compiling a
``foo::bar::*`` namespace pattern into a matcher and running a symbol against
it. It is a general string-matching primitive: it knows nothing about
suppressions, findings, or policy, and `suppression.py`'s own
``Suppression``/``SuppressionList`` are its only callers.

The pieces are one mechanism and move together: ``_translate_namespace_glob``
compiles, ``_SegmentGlobMatcher`` runs the compiled form segment by segment,
``_WildcardRunMatcher``/``_match_run`` handle a ``**``-style run, and the four
small helpers below them do the character-level work
(``_fnmatch_segment_regex``, ``_bracket_class_end``, ``_has_wildcard_char``,
``_split_namespace_segments``, ``_collapsed_namespace_segments``). Splitting
them apart would leave a caller reaching across modules for every step.

``suppression.py`` re-exports every name unchanged, which
``tests/test_suppression_edge_cases.py`` relies on -- it imports
``_SEGMENT_RE_WRAPPER`` and ``_fnmatch_segment_regex`` from there directly.
"""

from __future__ import annotations

import fnmatch
import re

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
