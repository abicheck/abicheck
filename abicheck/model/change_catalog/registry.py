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

"""Core types for the single-declaration ChangeKind registry (ADR-061 D9).

Leaf module holding the verdict enum, the per-kind metadata dataclass, and
the registry container that derives the classification sets, plus the
catalog-validation logic D9 assigns to the assembled registry. This is the
target owner ADR-061 names for this logic — it used to live in the legacy
flat ``abicheck/change_registry_types.py``, which now re-exports every name
here unchanged (``from abicheck.change_registry_types import Verdict`` and
the transitive ``from abicheck.change_registry import Verdict`` are both
still valid). The 397-entry data table itself (``change_registry.py`` and
its ``change_registry_<topic>.py`` siblings) has not yet been repartitioned
into this package — see the ADR's Phase 5 section for the remaining scope.

This module has zero internal imports (a true leaf, per ADR-061's ``model``
layer contract of ``may_import: []``), which is what lets both
``checker_policy.py`` (imports ``REGISTRY`` from ``change_registry.py``,
which imports the legacy shim, which imports this module) and
``diff_helpers.py`` (same shape) import ``VALID_BASE_POLICIES``/
``TEMPLATE_VOCAB`` from here without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_RISK = "COMPATIBLE_WITH_RISK"
    API_BREAK = "API_BREAK"
    BREAKING = "BREAKING"


#: Canonical set of valid built-in policy names. Import from here — do not
#: redefine. ``ChangeKindRegistry`` below validates every
#: ``ChangeKindMeta.policy_overrides`` key against this set at construction
#: time (ADR-061 Phase 5, D9's "valid references" catalog validation).
#: ``checker_policy.py``/``change_registry_types.py`` re-export this name
#: unchanged, so every existing
#: ``from abicheck.checker_policy import VALID_BASE_POLICIES`` caller is
#: unaffected.
VALID_BASE_POLICIES: frozenset[str] = frozenset(
    {"strict_abi", "sdk_vendor", "plugin_abi"}
)

#: Policies whose ``checker_policy.policy_kind_sets()`` implementation
#: classifies every kind carrying a ``policy_overrides`` entry for that
#: policy as ``Verdict.COMPATIBLE`` unconditionally — the declared override
#: *value* is never consulted, only its key's presence
#: (``_policy_override_kinds()`` gathers ``policy_overrides_for(policy)``'s
#: keys, not its values). ``ChangeKindRegistry`` below rejects a declared
#: override for one of these policies that isn't ``Verdict.COMPATIBLE``,
#: since anything else would silently disagree with actual runtime behavior.
#: Keep this in sync with ``policy_kind_sets()`` — if a future policy's
#: implementation genuinely consumes the declared verdict, remove it here.
_VERDICT_BLIND_POLICIES: frozenset[str] = frozenset({"sdk_vendor", "plugin_abi"})

#: Fixed placeholder vocabulary a ``ChangeKindMeta.description_template`` may
#: use. Import from here — do not redefine. ``diff_helpers.make_change``
#: formats a kind's template from exactly these structured fields
#: (``{symbol} {name} {old} {new} {detail}``) — see that module for the
#: per-field meaning. ``diff_helpers.py``/``change_registry_types.py``
#: re-export this name unchanged, so every existing
#: ``from abicheck.diff_helpers import TEMPLATE_VOCAB`` caller is unaffected.
TEMPLATE_VOCAB = frozenset({"symbol", "name", "old", "new", "detail"})


@dataclass(frozen=True)
class ChangeKindMeta:
    """All metadata for a single ChangeKind, declared in one place."""

    kind: str  # ChangeKind enum value (e.g. "func_removed")
    default_verdict: Verdict
    impact: str = ""
    is_addition: bool = False
    policy_overrides: dict[str, Verdict] = field(default_factory=dict)
    # Optional ``str.format``-style template for a finding's per-change
    # ``description`` (C6). Detectors build their Change via
    # ``diff_helpers.make_change`` and pass structured fields rather than
    # hand-rolling an f-string, so the wording for a kind lives in one place.
    # Placeholders are drawn from the fixed vocabulary
    # ``{symbol} {name} {old} {new} {detail}`` (``make_change`` validates this).
    # ``None`` means the kind keeps a *bespoke* per-call-site description — used
    # when the text embeds computed offsets, demangled signatures, vtable slot
    # indices, counts, etc. that no fixed template can express.
    description_template: str | None = None


#: Representative ``str.format(**...)`` kwarg sets used to *actually execute*
#: a ``description_template`` at registry-construction time (see
#: ``_check_template_formats`` below), rather than re-implementing Python's
#: own replacement-field grammar by hand. Two sets, not one: real callers
#: (``diff_helpers.make_change()``) always pass a real ``str`` for
#: ``symbol``, but ``name``/``old``/``new``/``detail`` are all
#: ``str | None`` and frequently ``None`` in practice — and a format spec
#: that works for a ``str`` value can still raise ``TypeError`` for ``None``
#: (``format(None, ">10")`` raises; ``format(None, "")`` — i.e. a bare
#: ``{old}`` — does not), so probing only with strings would miss that
#: failure mode.
_TEMPLATE_PROBE_VALUE_SETS: tuple[dict[str, str | None], ...] = (
    {
        "symbol": "probe",
        "name": "probe",
        "old": "probe",
        "new": "probe",
        "detail": "probe",
    },
    {"symbol": "probe", "name": None, "old": None, "new": None, "detail": None},
)


def _check_template_formats(template: str) -> None:
    """Raise ``ValueError`` if ``template`` cannot be formatted by ``make_change()``.

    Actually executes ``template.format(**probe)`` for each of
    ``_TEMPLATE_PROBE_VALUE_SETS`` — the exact operation
    ``diff_helpers.make_change()`` performs at finding-emission time — rather
    than hand-parsing the template's replacement-field grammar. An earlier
    version of this check used ``string.Formatter().parse()`` to inspect only
    each replacement field's outer field name, which missed a field nested
    inside a format spec (``{name:{bogus}}``), an illegal ``!conversion``
    (``{name!x}`` — only ``r``/``s``/``a``/none are legal), and an outright
    invalid format *code* (``{name:q}`` — ``q`` is not a real presentation
    type, raising ``ValueError: Unknown format code 'q'`` only at format
    time). Executing the real call catches all of these — and anything else
    ``str.format`` can raise — by construction, since it does not depend on
    this function correctly re-deriving Python's own formatting grammar
    (Codex review, PR #882, two rounds: nested fields/conversions, then
    format codes).
    """
    for probe in _TEMPLATE_PROBE_VALUE_SETS:
        try:
            template.format(**probe)
        except Exception as exc:  # noqa: BLE001 - re-raised with kind context below
            raise ValueError(
                f"description_template {template!r} fails to format with "
                f"representative values {probe!r}: {exc}"
            ) from exc


def _validate_references_and_defaults(e: ChangeKindMeta) -> None:
    """Enforce D9's "valid references" and "non-contradictory defaults".

    Two of the four catalog-validation properties ADR-061 D9 assigns to the
    assembled registry (global uniqueness and enum-membership completeness
    are enforced elsewhere — the constructor's duplicate check and
    ``tests/test_architecture_refactor.py``'s membership tests respectively;
    "complete metadata" is not yet enforced, since ``impact``/
    ``description_template`` are deliberately optional and a real content
    gap — see the ADR's Phase 5 section). Raises ``ValueError`` with the
    offending kind named, matching the constructor's existing duplicate-key
    failure mode, so a bad entry fails at import time rather than silently
    reaching a comparison.
    """
    for policy, override in e.policy_overrides.items():
        if policy not in VALID_BASE_POLICIES:
            raise ValueError(
                f"{e.kind!r}: policy_overrides names unknown policy {policy!r}; "
                f"valid policies are {sorted(VALID_BASE_POLICIES)}"
            )
        if policy == "strict_abi":
            # strict_abi IS the base policy default_verdict already encodes —
            # an override under this key would be a second, competing source
            # of truth for the same policy rather than a real override.
            raise ValueError(
                f"{e.kind!r}: policy_overrides may not target 'strict_abi' "
                f"(that policy's verdict is default_verdict itself)"
            )
        if override == e.default_verdict:
            # A policy_overrides entry that restates default_verdict verbatim
            # is not an override at all — either the entry is stale after a
            # default_verdict edit, or it never needed to be declared.
            raise ValueError(
                f"{e.kind!r}: policy_overrides[{policy!r}] == default_verdict "
                f"({override!r}); a redundant override contradicts the point "
                f"of declaring one — remove it or pick a genuinely different verdict"
            )
        if policy in _VERDICT_BLIND_POLICIES and override != Verdict.COMPATIBLE:
            # checker_policy.policy_kind_sets() classifies every kind with a
            # 'sdk_vendor'/'plugin_abi' override as Verdict.COMPATIBLE
            # unconditionally (via _policy_override_kinds(), which gathers
            # only policy_overrides_for(policy)'s KEYS — the declared verdict
            # is never consulted at runtime). A declared override value other
            # than Verdict.COMPATIBLE would therefore pass the redundancy
            # check above while silently behaving as COMPATIBLE anyway — a
            # real metadata/runtime-behavior mismatch, not a redundant-override
            # duplicate (Codex review, PR #882). If a future policy's
            # implementation in policy_kind_sets() genuinely honors the
            # declared verdict, remove it from _VERDICT_BLIND_POLICIES rather
            # than special-casing around this check.
            raise ValueError(
                f"{e.kind!r}: policy_overrides[{policy!r}] declares {override!r}, "
                f"but checker_policy.policy_kind_sets() classifies every "
                f"{policy!r}-keyed kind as Verdict.COMPATIBLE unconditionally, "
                f"discarding the declared verdict — only Verdict.COMPATIBLE "
                f"matches this policy's actual runtime behavior today"
            )
    if e.is_addition and e.default_verdict != Verdict.COMPATIBLE:
        # addition_kinds() is documented as "a subset of COMPATIBLE" — an
        # is_addition entry whose own default_verdict disagrees with that
        # invariant is self-contradictory.
        raise ValueError(
            f"{e.kind!r}: is_addition=True requires default_verdict == "
            f"Verdict.COMPATIBLE (addition_kinds() is a subset of "
            f"COMPATIBLE_KINDS), got {e.default_verdict!r}"
        )
    if e.description_template is not None:
        # diff_helpers.make_change() formats description_template via
        # ``template.format(symbol=..., name=..., old=..., new=...,
        # detail=...)`` — a keyword-only call, so any field name outside
        # TEMPLATE_VOCAB, a positional `{}`/`{0}` (which that call shape can
        # never satisfy), an illegal conversion, a bad field nested inside a
        # format spec, or an invalid format code, all raise at format time —
        # but only the first time a finding of this kind is actually
        # formatted, not at registry construction. That is D9's "valid
        # references" property for this field, the same shape as the
        # policy_overrides checks above (Codex review, PR #882).
        try:
            _check_template_formats(e.description_template)
        except ValueError as exc:
            raise ValueError(f"{e.kind!r}: {exc}") from exc


class ChangeKindRegistry:
    """Registry of ChangeKindMeta entries, deriving classification sets.

    Usage::

        registry = ChangeKindRegistry(entries)
        breaking = registry.kinds_for_verdict(Verdict.BREAKING)
        impact = registry.impact_for("func_removed")
    """

    def __init__(self, entries: list[ChangeKindMeta]) -> None:
        self._entries: dict[str, ChangeKindMeta] = {}
        for e in entries:
            if e.kind in self._entries:
                raise ValueError(f"Duplicate registry entry for {e.kind!r}")
            _validate_references_and_defaults(e)
            self._entries[e.kind] = e

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, kind_value: str) -> bool:
        return kind_value in self._entries

    def get(self, kind_value: str) -> ChangeKindMeta | None:
        return self._entries.get(kind_value)

    def kinds_for_verdict(self, verdict: Verdict) -> frozenset[str]:
        """Return all kind values whose default_verdict matches."""
        return frozenset(
            e.kind for e in self._entries.values() if e.default_verdict == verdict
        )

    def addition_kinds(self) -> frozenset[str]:
        """Return kind values flagged as additions (subset of COMPATIBLE)."""
        return frozenset(e.kind for e in self._entries.values() if e.is_addition)

    def policy_overrides_for(self, policy: str) -> dict[str, Verdict]:
        """Return {kind_value: overridden_verdict} for a given policy name."""
        return {
            e.kind: e.policy_overrides[policy]
            for e in self._entries.values()
            if policy in e.policy_overrides
        }

    def impact_text(self) -> dict[str, str]:
        """Return {kind_value: impact} for all entries with non-empty impact."""
        return {e.kind: e.impact for e in self._entries.values() if e.impact}

    def description_template_for(self, kind_value: str) -> str | None:
        """Return the description template for a kind, or None if bespoke/unknown."""
        e = self._entries.get(kind_value)
        return e.description_template if e is not None else None

    def templated_kinds(self) -> frozenset[str]:
        """Return kind values that own a description template (C6 migration set)."""
        return frozenset(
            e.kind for e in self._entries.values() if e.description_template is not None
        )

    @property
    def entries(self) -> dict[str, ChangeKindMeta]:
        return dict(self._entries)
