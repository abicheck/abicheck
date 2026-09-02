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

"""The shared selector-matching primitive behind suppression and
reclassification (ADR-063 D10, implementation plan Phase 9).

``suppression.py``'s ``Suppression`` and ``reclassify.py``'s
``ReclassifyRule`` each need to answer the identical question — "does this
selector combination (``symbol``/``symbol_pattern``/``type_pattern``/
``member_name``/``namespace``/``entity_namespace``/``cause_namespace``/
``source_location``/``change_kind``/``binding``/``finding_id``/``expires``)
match this ``Change``?" — but only *what to do* once it matches differs
(delete the finding vs. reclassify its verdict). Before this phase, both
classes carried a full, independent copy of the fnmatch/regex/namespace-glob
matching machinery, kept in sync by hand — ``reclassify.py`` in practice
reused ``Suppression`` at runtime via a lazy ``importlib.import_module`` call
specifically to dodge the import cycle a *static* import would have closed
(``policy_file -> reclassify -> suppression -> checker_types ->
policy_file``). :class:`SelectorSet` is the one grammar both classes now
build on: it is what ``Suppression.__post_init__``/``ReclassifyRule.
__post_init__`` each construct internally to validate and match selectors,
and it is what let ``reclassify.py`` drop the ``importlib`` workaround for a
**static** import of this module — the cycle it worked around no longer
exists, because neither ``suppression.py`` nor ``reclassify.py`` needs to
import the *other* module any more; both import this leaf instead.

**Leaf module — zero dependency on ``checker_types.py``, ``suppression.py``,
``reclassify.py``, ``policy_file.py``, or ``finding_identity.py``.** This is
not a style preference: it is the only way a *static* import from
``reclassify.py`` can exist without recreating the cycle above.
``finding_identity.py`` is named explicitly, not merely assumed covered by
the other four, because it is comparison-layer logic (imports
``checker_types``/model entities to compute
``report_canonical_finding_id()``) — a first draft of this phase's
``finding_id`` matcher called into it directly from here, which is exactly
the upward-dependency mistake Phase 2 already caught and corrected for
``model/identity.py``, recreated in this leaf. The fix is the same shape:
:func:`_matches_finding_id` never computes a canonical finding id itself —
it only compares an already-computed string. The **caller**
(``Suppression.selector_matches()``, comparison-layer code that already
imports ``finding_identity.py`` today) computes
``report_canonical_finding_id(change)`` once and passes the resulting string
in via :meth:`SelectorSet.matches_selectors`'s ``canonical_finding_id``
parameter. ``scripts/check_architecture.py``'s import-direction gate
enforces this module's own import list directly (not just the general
layer-level ``policy -> compare`` edge, which *would* otherwise permit an
import of ``finding_identity.py`` — that module is classified into the
``compare`` layer, and ``policy`` may import ``compare`` in general; this
leaf's own contract is strictly narrower than what the layer graph alone
would allow) — a future change reintroducing any of these five imports
here fails that check closed, not just this docstring's own promise.

``SelectorSet`` covers the *union* of both classes' selector fields, not
their intersection — ``binding`` is shared by both (ELF symbol-linkage
matching); ``finding_id`` is ``Suppression``-only (an exact match on the
canonical finding identity, standalone-sufficient, so it stays part of the
shared grammar every matcher must still evaluate even though
``ReclassifyRule`` never sets it — a consumer that doesn't use a given field
simply never sets it, per this phase's own design note). ``Suppression``'s
own ``reachability``/``allow_public_break``/``allow_unknown_reachability``
gates, and ``ReclassifyRule``'s own ``to``/``to_verdict``, are deliberately
**not** part of this shared grammar — those are the two classes' genuinely
different *outcomes* (delete vs. reclassify), which D10 does not
consolidate, only the matching grammar that decides *whether* either
outcome applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from ..checker_policy import ChangeKind
from ..model.elf_facts import SymbolBinding
from .selectors_namespace_glob import (
    _compile_glob,
    _compile_namespace_glob,
    _compile_pattern,
    _SegmentGlobMatcher,
)

# Valid values for a `binding` selector — the same value strings
# `Change.symbol_binding` is stamped with (`SymbolBinding.value`). Imported
# from `model.elf_facts` rather than duplicated: that module is itself a
# leaf (`may_import: []` under the `model` layer), so it carries no cycle
# risk (mirrors `suppression.py`'s own pre-existing identical reasoning for
# this same import, before this phase moved it here).
_VALID_BINDING: frozenset[str] = frozenset(b.value for b in SymbolBinding)

# Pre-built valid `change_kind` values for fast validation.
_VALID_CHANGE_KINDS: frozenset[str] = frozenset(ck.value for ck in ChangeKind)

# ChangeKind values that represent type-level changes (matched by type_pattern).
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


class SelectorMatchable(Protocol):
    """Structural protocol for the ``Change``-shaped value every matcher in
    this module reads from — deliberately duck-typed rather than importing
    the real ``abicheck.checker_types.Change`` (see the module docstring's
    zero-dependency contract). A real ``Change`` instance satisfies this
    structurally; nothing needs to declare the relationship."""

    symbol: str
    qualified_name: str | None
    caused_by_type: str | None
    source_location: str | None
    symbol_binding: str | None
    kind: ChangeKind


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



# Matches abicheck/internal_leak.py's own `_TEMPLATE_ARG_RE`/
# `_strip_template_args` exactly, but is not imported from there:
# internal_leak.py pulls in `checker_types.py`/`buildsource/*` transitively,
# and this module's own zero-dependency leaf contract (ADR-063 D10 — see the
# module docstring) forbids that edge. Ten lines of self-contained regex
# logic is a cheaper, more honest duplication than an import that would
# make `abicheck/policy/selectors.py` a "migrated" (physically-located-under
# `abicheck/policy/`) source importing an unclassified module —
# `scripts/check_architecture.py`'s existing dependency-direction gate
# already forbids exactly that shape for any file under this package.
_TEMPLATE_ARG_RE = re.compile(r"<[^<>]*>")


def _strip_template_args(name: str) -> str:
    """Collapse balanced ``<...>`` template arg lists out of *name*.

    Handles one level of nesting iteratively. Used only for splitting the
    name into ``::``-separated segments, not for canonicalisation.
    """
    prev = None
    cur = name
    while cur != prev:
        prev = cur
        cur = _TEMPLATE_ARG_RE.sub("", cur)
    return cur


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
    from ..demangle import demangle as _dm

    forms: list[str] = [name]
    if name.startswith("_Z"):
        dm = _dm(name)
        if dm:
            forms.append(dm)
    return any(pat.matches_any_ancestor(_strip_template_args(form)) for form in forms)


def _matches_source_location(compiled: re.Pattern[str], change: SelectorMatchable) -> bool:
    """Return False if *change*'s source path does not match *compiled*."""
    src = change.source_location or ""
    src_path = re.sub(r":\d+(?::\d+)?$", "", src)
    return bool(compiled.match(src_path))


def _matches_member_name(compiled: re.Pattern[str], change: SelectorMatchable) -> bool:
    """Return True if the last ``::``-segment of ``change.symbol`` matches *compiled*."""
    member = change.symbol.rsplit("::", 1)[-1] if change.symbol else ""
    return bool(compiled.fullmatch(member))


def _matches_binding(binding: str, change: SelectorMatchable) -> bool:
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


def _matches_finding_id(finding_id: str, canonical_finding_id: str | None) -> bool:
    """Return True if *canonical_finding_id* equals *finding_id* — see
    :attr:`~abicheck.suppression.Suppression.finding_id`.

    A plain string comparison, deliberately: this leaf module has zero
    dependency on :mod:`abicheck.finding_identity` (ADR-063 D10 — see this
    module's own docstring), so it cannot compute
    ``report_canonical_finding_id(change)`` itself. The caller (comparison-
    layer code that already imports ``finding_identity.py`` today, e.g.
    :meth:`~abicheck.suppression.Suppression.selector_matches`) computes
    that value once and passes it in here; ``None`` (no value was computed
    for this change, or the caller never bothers because its own selector
    grammar has no ``finding_id`` field, as :class:`~abicheck.reclassify.
    ReclassifyRule` doesn't) never matches.
    """
    return canonical_finding_id is not None and canonical_finding_id == finding_id


def _matches_entity_namespace(compiled: _SegmentGlobMatcher, change: SelectorMatchable) -> bool:
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


def _matches_cause_namespace(compiled: _SegmentGlobMatcher, change: SelectorMatchable) -> bool:
    """Return True if the change's ``caused_by_type`` lies in the namespace.

    ADR-044 D3: the counterpart to :func:`_matches_entity_namespace` — matches
    only the *cause* of the change, not its own subject.
    """
    return _ns_match(compiled, change.caused_by_type)


def _matches_type_pattern(
    compiled: re.Pattern[str],
    change_kind_filter: str | None,
    change: SelectorMatchable,
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
    change: SelectorMatchable,
) -> bool:
    """Return True if *change.symbol* satisfies the symbol/symbol_pattern selector."""
    if symbol is not None:
        return change.symbol == symbol
    if compiled_pattern is not None:
        return bool(compiled_pattern.fullmatch(change.symbol))
    return True


@dataclass
class SelectorSet:
    """The shared selector grammar :class:`~abicheck.suppression.Suppression`
    and :class:`~abicheck.reclassify.ReclassifyRule` each validate and match
    through — see the module docstring for why this exists and what it
    deliberately excludes (``reachability``/``allow_public_break``/
    ``allow_unknown_reachability``, ``to``/``to_verdict``, ``reason``,
    ``label``: outcome-level fields specific to one caller or the other, not
    part of the shared matching grammar).

    A caller constructs one of these in its own ``__post_init__`` from its
    own selector fields (mirroring the identically-named/-typed fields on
    :class:`Suppression`/:class:`ReclassifyRule`), then delegates matching
    to :meth:`matches_selectors`/:meth:`is_expired` on every ``matches()``/
    ``selector_matches()`` call. Validation (mutual exclusivity, unknown
    ``change_kind``, malformed glob/regex, "at least one selector",
    malformed ``binding``) happens once, here, at construction time — a
    ``ValueError`` raised here propagates unchanged to the caller's own
    constructor, so neither ``Suppression`` nor ``ReclassifyRule`` can ever
    exist with an invalid selector combination.
    """

    symbol: str | None = None
    symbol_pattern: str | None = None
    type_pattern: str | None = None
    member_name: str | None = None
    source_location: str | None = None
    namespace: str | None = None
    entity_namespace: str | None = None
    cause_namespace: str | None = None
    change_kind: str | None = None
    binding: str | None = None
    finding_id: str | None = None
    expires: date | None = None
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

    def __post_init__(self) -> None:
        if self.namespace is not None and self.entity_namespace is not None:
            raise ValueError(
                "Selector fields 'namespace' and 'entity_namespace' are "
                "aliases for the same selector — specify only one"
            )
        # A datetime is itself a date subclass -- normalize the same way
        # Suppression._parse_expires/ReclassifyRule.__post_init__ each
        # already normalized their own YAML/direct-construction inputs
        # before this shared grammar existed, so a caller that skips its own
        # normalization (there is currently none that does) still can't
        # crash a later `date.today() > self.expires` comparison.
        if isinstance(self.expires, datetime):
            self.expires = self.expires.date()
        effective_entity_ns = (
            self.entity_namespace if self.entity_namespace is not None else self.namespace
        )
        _validate_selectors(
            has_symbol=self.symbol is not None,
            has_sym_pattern=self.symbol_pattern is not None,
            has_type_pattern=self.type_pattern is not None,
            has_member_name=self.member_name is not None,
            has_source_location=self.source_location is not None,
            has_namespace=effective_entity_ns is not None or self.cause_namespace is not None,
            has_finding_id=self.finding_id is not None,
        )
        # Compile regex eagerly — malformed patterns fail at construction
        # time, not match time. Fullmatch semantics throughout.
        self._compiled_pattern = _compile_pattern(self.symbol_pattern, "symbol_pattern")
        self._compiled_type_pattern = _compile_pattern(self.type_pattern, "type_pattern")
        self._compiled_member_pattern = _compile_pattern(self.member_name, "member_name")
        self._compiled_source_pattern = _compile_glob(self.source_location, "source_location")
        self._compiled_entity_namespace_pattern = _compile_namespace_glob(
            effective_entity_ns, "namespace"
        )
        self._compiled_cause_namespace_pattern = _compile_namespace_glob(
            self.cause_namespace, "cause_namespace"
        )
        if self.change_kind is not None and self.change_kind not in _VALID_CHANGE_KINDS:
            valid = ", ".join(sorted(_VALID_CHANGE_KINDS))
            raise ValueError(
                f"Unknown change_kind {self.change_kind!r}. "
                f"Valid values: {valid}"
            )
        if self.binding is not None and (
            # isinstance check first: a YAML value neither field's own
            # `str | None` annotation enforces at runtime (a list, a
            # mapping) is unhashable, so `not in` on the frozenset below
            # would raise TypeError instead of this constructor's
            # documented ValueError contract — see Suppression's own
            # pre-existing identical guard, moved here unchanged.
            not isinstance(self.binding, str)
            or self.binding not in _VALID_BINDING
        ):
            raise ValueError(
                f"Invalid binding {self.binding!r}. "
                f"Valid values: {sorted(_VALID_BINDING)}"
            )

    def is_expired(self, today: date | None = None) -> bool:
        """Return True if this selector set has passed its ``expires`` date."""
        if self.expires is None:
            return False
        check_date = today or date.today()
        return check_date > self.expires

    def matches_selectors(
        self,
        change: SelectorMatchable,
        *,
        today: date | None = None,
        canonical_finding_id: str | None = None,
    ) -> bool:
        """Return True if this selector combination matches *change*.

        Mirrors ``Suppression._selector_match``'s pre-Phase-9 field order
        exactly (this *is* that logic, moved rather than rewritten) — an
        expired selector set never matches; every non-``type_pattern``/
        non-``symbol``/``symbol_pattern`` field applies conjunctively (AND
        semantics); ``type_pattern`` is a primary selector that returns
        early; ``symbol``/``symbol_pattern`` and ``change_kind`` are checked
        last.

        *canonical_finding_id* is the caller's own already-computed
        ``report_canonical_finding_id(change)`` — see the module docstring
        for why this leaf never computes that value itself. Pass ``None``
        when the caller's own selector grammar has no ``finding_id`` field
        (:class:`~abicheck.reclassify.ReclassifyRule`) or didn't bother
        computing it; a selector set with ``finding_id`` set never matches
        in that case.
        """
        if self.is_expired(today):
            return False
        if self._compiled_source_pattern is not None:
            if not _matches_source_location(self._compiled_source_pattern, change):
                return False
        if self._compiled_member_pattern is not None:
            if not _matches_member_name(self._compiled_member_pattern, change):
                return False
        if self.binding is not None:
            if not _matches_binding(self.binding, change):
                return False
        if self.finding_id is not None:
            if not _matches_finding_id(self.finding_id, canonical_finding_id):
                return False
        if self._compiled_entity_namespace_pattern is not None:
            if not _matches_entity_namespace(self._compiled_entity_namespace_pattern, change):
                return False
        if self._compiled_cause_namespace_pattern is not None:
            if not _matches_cause_namespace(self._compiled_cause_namespace_pattern, change):
                return False
        if self._compiled_type_pattern is not None:
            return _matches_type_pattern(self._compiled_type_pattern, self.change_kind, change)
        if not _matches_symbol(self.symbol, self._compiled_pattern, change):
            return False
        if self.change_kind is not None and change.kind.value != self.change_kind:
            return False
        return True
