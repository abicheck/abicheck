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
)
from .checker_types import Change

# Pre-build valid change_kind values for fast validation
_VALID_CHANGE_KINDS: frozenset[str] = frozenset(ck.value for ck in ChangeKind)

# Keys allowed in a suppression entry — unknown keys are rejected
_KNOWN_ENTRY_KEYS: frozenset[str] = frozenset({
    "symbol", "symbol_pattern", "type_pattern", "member_name",
    "change_kind", "reason", "label", "source_location", "expires",
    "namespace", "entity_namespace", "cause_namespace",
    "reachability", "allow_public_break", "allow_unknown_reachability",
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


def _has_wildcard_char(segment: str) -> bool:
    """Whether *segment* itself contains an fnmatch wildcard character."""
    return "*" in segment or "?" in segment


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

    A ``**`` segment immediately adjacent (via a literal ``::``) to a
    segment that itself contains its *own* wildcard is a special case the
    hand-rolled optional-``::`` absorption above cannot express correctly:
    that neighboring segment's own unconstrained wildcard can silently
    swallow what should be a mandatory ``::`` boundary, since the whole
    absorbing group is optional and a greedy ``.*`` never needs it —
    ``foo**::**`` compiled (before this fix) to ``foo.*(?:::.*)?``, which
    matches bare ``foobar`` even though no ``::`` appears anywhere in it
    (Codex review, fresh evidence: the original, pre-rewrite fnmatch-style
    pattern required that literal ``::`` to be present — verified against
    real ``fnmatch.translate("foo**::**")``, which already handles this
    exact adjacency correctly via an atomic group). Rather than re-derive
    that same guarantee by hand, :func:`_fnmatch_segment_regex` is asked to
    translate the adjacent wildcard segment *together with* its bordering
    ``**`` (and the ``::`` between them) as one combined fnmatch string,
    reusing CPython's own correct handling instead of reinventing it.
    """
    # A run of adjacent "**" segments means the same thing as one — collapse
    # "a::**::**::b" to "a::**::b". Done on the already-split segment list,
    # not the raw string (Codex review): a substring-level regex here would
    # also fire inside a non-globstar segment ending in "**" (e.g.
    # "foo**::**" contains the literal substring "**::**" starting mid-
    # segment), silently rewriting it to "foo**" and dropping the "::"
    # boundary the fnmatch-style pattern still required.
    segments: list[str] = []
    for seg in pattern.split("::"):
        if seg == "**" and segments and segments[-1] == "**":
            continue
        segments.append(seg)
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
            prev_has_wildcard = i > 0 and _has_wildcard_char(segments[i - 1])
            next_has_wildcard = i + 1 < n and _has_wildcard_char(segments[i + 1])
            if prev_has_wildcard and next_has_wildcard:
                combined = _fnmatch_segment_regex(
                    segments[i - 1] + "::**::" + segments[i + 1]
                )
                parts[-1] = combined
                i += 2
                continue
            if prev_has_wildcard:
                # `parts[-1]` is still exactly the previous segment's own
                # regex fragment (appended on the prior loop iteration) —
                # replace it in place with the combined translation; any
                # earlier "::" joiner sits in its own separate `parts`
                # entry and is untouched.
                combined = _fnmatch_segment_regex(segments[i - 1] + "::**")
                parts[-1] = combined
                i += 1
                continue
            if next_has_wildcard:
                combined = _fnmatch_segment_regex("**::" + segments[i + 1])
                if i > 0:
                    parts.append("::")
                parts.append(combined)
                i += 2
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


def _compile_namespace_glob(glob: str | None, field_name: str) -> re.Pattern[str] | None:
    """Compile a namespace-selector glob (``namespace``/``entity_namespace``/
    ``cause_namespace``) to a regex, using :func:`_translate_namespace_glob`'s
    globstar semantics rather than plain ``fnmatch``. Raises
    :class:`ValueError` on a malformed pattern."""
    if glob is None:
        return None
    try:
        return re.compile(_translate_namespace_glob(glob))
    except re.error as e:
        raise ValueError(f"Invalid {field_name} {glob!r}: {e}") from e


def _validate_selectors(
    has_symbol: bool,
    has_sym_pattern: bool,
    has_type_pattern: bool,
    has_member_name: bool,
    has_source_location: bool,
    has_namespace: bool,
) -> None:
    """Raise :class:`ValueError` if the selector combination is invalid."""
    selector_count = sum([has_symbol, has_sym_pattern, has_type_pattern])
    if selector_count == 0 and not has_source_location and not has_member_name and not has_namespace:
        raise ValueError(
            "Suppression must have at least one of: "
            "'symbol', 'symbol_pattern', 'type_pattern', "
            "'member_name', 'source_location', or 'namespace'"
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


def _ns_match(pat: re.Pattern[str], name: str | None) -> bool:
    """Return True if *name* (or any of its namespace ancestors) matches *pat*.

    Handles Itanium-mangled symbols by also trying the demangled form.
    Template arguments are stripped before walking the ancestor chain.
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
    for form in forms:
        candidate = _strip_template_args(form)
        while True:
            if pat.match(candidate):
                return True
            if "::" not in candidate:
                break
            candidate = candidate.rsplit("::", 1)[0]
    return False


def _matches_source_location(compiled: re.Pattern[str], change: Change) -> bool:
    """Return False if *change*'s source path does not match *compiled*."""
    src = change.source_location or ""
    src_path = re.sub(r":\d+(?::\d+)?$", "", src)
    return bool(compiled.match(src_path))


def _matches_member_name(compiled: re.Pattern[str], change: Change) -> bool:
    """Return True if the last ``::``-segment of ``change.symbol`` matches *compiled*."""
    member = change.symbol.rsplit("::", 1)[-1] if change.symbol else ""
    return bool(compiled.fullmatch(member))


def _matches_entity_namespace(compiled: re.Pattern[str], change: Change) -> bool:
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


def _matches_cause_namespace(compiled: re.Pattern[str], change: Change) -> bool:
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
    _compiled_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_type_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_member_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_source_pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _compiled_entity_namespace_pattern: re.Pattern[str] | None = field(
        default=None, init=False, repr=False
    )
    _compiled_cause_namespace_pattern: re.Pattern[str] | None = field(
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
        effective_entity_ns = self.entity_namespace if self.entity_namespace is not None else self.namespace
        _validate_selectors(
            has_symbol=self.symbol is not None,
            has_sym_pattern=self.symbol_pattern is not None,
            has_type_pattern=self.type_pattern is not None,
            has_member_name=self.member_name is not None,
            has_source_location=self.source_location is not None,
            has_namespace=effective_entity_ns is not None or self.cause_namespace is not None,
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
                    "when --require-justification is set."
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
    ) -> SuppressionAudit:
        """Audit suppression rules against a set of changes.

        Returns a :class:`SuppressionAudit` with:
        - ``stale_rules``: suppressions that matched zero changes (misconfigured?)
        - ``high_risk_matches``: suppressions that matched a change breaking
          under *breaking_kinds*
        - ``expired_rules``: rules past their expiry date
        - ``near_expiry_rules``: rules expiring within *near_expiry_days*
        - ``match_counts``: per-rule match count

        *breaking_kinds* defaults to the built-in :data:`BREAKING_KINDS`, but
        a caller running under an active ``--policy-file`` should pass its
        effective, override-applied breaking set (e.g.
        ``result._effective_kind_sets()[0]``) instead -- a policy can promote
        a normally-API_BREAK kind to BREAKING or demote a normally-BREAKING
        kind away from it, and this audit's "high risk" classification should
        match the verdict the user's own comparison actually produced, not
        the static default.
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
            for i, s in enumerate(self._suppressions):
                if s.matches(c, today=today):
                    match_counts[i] += 1
                    if c.kind in effective_breaking_kinds:
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
