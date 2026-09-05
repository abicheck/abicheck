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

"""Suppression — load and apply suppression rules to ABI changes.

The selector grammar itself (``symbol``/``symbol_pattern``/``type_pattern``/
``member_name``/``namespace``/``entity_namespace``/``cause_namespace``/
``source_location``/``change_kind``/``binding``/``finding_id``/``expires``)
lives in :mod:`abicheck.policy.selectors` (ADR-063 D10, implementation plan
Phase 9) — a dependency-free leaf module shared with
:mod:`abicheck.reclassify`'s ``ReclassifyRule``, so the two selector-scoped
rule forms can no longer drift out of sync the way two independent copies of
the same fnmatch/regex/namespace-glob machinery once could. ``Suppression``
constructs one :class:`~abicheck.policy.selectors.SelectorSet` from its own
selector fields in :meth:`Suppression.__post_init__` and delegates all
selector validation/matching to it; this module retains only what's
genuinely specific to *suppression* — the reachability/``allow_public_break``
gates (ADR-044 D2) that decide whether a selector match actually suppresses
the finding, YAML loading, audit/reporting, and suggestion generation.
"""
from __future__ import annotations

import dataclasses
import hashlib
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
from .policy.selectors import _TYPE_CHANGE_KINDS, SelectorSet
from .suppression_yaml import parse_finding_id, raw_finding_ids_by_index

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


@dataclass
class Suppression:
    """One suppression rule. See individual field docstrings below for the
    selector grammar and :meth:`matches`/:meth:`selector_matches` for how
    they're evaluated.

    **Selector fields are read once, at construction time, into an internal
    :class:`~abicheck.policy.selectors.SelectorSet` (ADR-063 D10) — mutating
    a field after construction does not change what :meth:`matches`/
    :meth:`selector_matches` matches.** This was already true for every
    *compiled* selector (``symbol_pattern``/``type_pattern``/``member_name``/
    ``source_location``/``namespace``/``entity_namespace``/``cause_namespace``)
    before this refactor -- each was compiled once in ``__post_init__`` and
    never recompiled on assignment; this phase only extended the same rule to
    the two selectors that used to be a live re-read each call
    (``symbol``, ``expires``), rather than leaving those as the sole
    exceptions to an otherwise already-construct-once contract. Construct a
    new ``Suppression`` instead of mutating one in place if a rule's
    selectors need to change. (Some *non-selector* fields, e.g. ``reason``/
    ``label``, remain safely mutable, since nothing reads them through
    ``_selector`` -- see :mod:`abicheck.reporter_contract_blocks`'s
    ``suppression_rule_label``, which deliberately reads via ``getattr``
    rather than through this class's own matching path.)
    """

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
    #: Constructed in :meth:`__post_init__` from this class's own selector
    #: fields and delegated to for all selector validation/matching (ADR-063
    #: D10 — see the module docstring). Typed ``SelectorSet`` directly (not
    #: ``Any``): unlike ``reclassify.py``, this module already sits one
    #: layer below ``policy_file.py`` and ``policy/selectors.py`` is a leaf
    #: with no edge back to this module, so a static import here creates no
    #: cycle.
    _selector: SelectorSet = field(init=False, repr=False)
    _resolved_reachability: str = field(default="any", init=False, repr=False)
    _is_broad_selector: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        # Normalize + validate here too, not just in SuppressionList.load's
        # YAML path -- constructing Suppression(finding_id=...) directly
        # bypassed this entirely, silently creating a rule that can never
        # match anything real (Codex review).
        self.finding_id = parse_finding_id(self.finding_id)
        effective_entity_ns = self.entity_namespace if self.entity_namespace is not None else self.namespace
        # Every selector-grammar validation (namespace/entity_namespace
        # mutual exclusivity, "at least one selector", malformed glob/
        # regex, unknown change_kind, malformed binding) happens inside
        # SelectorSet.__post_init__ -- a ValueError raised there propagates
        # unchanged, so this constructor's documented error contract is
        # identical to before the selector grammar moved out.
        self._selector = SelectorSet(
            symbol=self.symbol,
            symbol_pattern=self.symbol_pattern,
            type_pattern=self.type_pattern,
            member_name=self.member_name,
            source_location=self.source_location,
            namespace=self.namespace,
            entity_namespace=self.entity_namespace,
            cause_namespace=self.cause_namespace,
            change_kind=self.change_kind,
            binding=self.binding,
            finding_id=self.finding_id,
            expires=self.expires,
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

        Delegates to :meth:`~abicheck.policy.selectors.SelectorSet.
        matches_selectors` (ADR-063 D10) — the shared grammar this class and
        :class:`~abicheck.reclassify.ReclassifyRule` both build on. This
        method's own job is narrow: compute
        ``finding_identity.report_canonical_finding_id(change)`` when (and
        only when) this rule actually has a ``finding_id`` selector to check
        it against — the one piece of matching that leaf module cannot do
        itself (it has zero dependency on ``finding_identity.py`` — see that
        module's own docstring), so the caller that already imports it
        computes the value instead.
        """
        canonical_finding_id: str | None = None
        if self.finding_id is not None:
            from .finding_identity import report_canonical_finding_id

            canonical_finding_id = report_canonical_finding_id(change)
        return self._selector.matches_selectors(
            change, today=today, canonical_finding_id=canonical_finding_id
        )

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
        source_path: str | None = None,
    ) -> None:
        self._suppressions = suppressions
        #: sha256 of the exact raw bytes :meth:`load` read, when these rules
        #: came from a file. Captured during that one read so a consumer
        #: never has to re-read the path to digest it: the file could have
        #: changed in between, and the digest would then authenticate content
        #: that did not produce these rules (Codex review, ADR-049 D6 replay).
        self.source_sha256 = source_sha256
        #: Path :meth:`load` read these rules from, when they came from a
        #: file. ADR-067 D3/D4 want the suppression *source* in the audit, not
        #: only its content hash: "which rule hid this finding" is only
        #: actionable when the reader also knows which document to open. Set
        #: on the same one read as ``source_sha256`` above, and ``None`` for a
        #: programmatically-built or merged list.
        self.source_path = source_path

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
            return cls([], source_sha256=digest, source_path=str(path))
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

        return cls(suppressions, source_sha256=digest, source_path=str(path))

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
