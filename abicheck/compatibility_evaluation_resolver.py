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

"""ADR-049 Phase 1 slice 2: the field-level precedence resolver.

Implements the per-field precedence rule from ADR-049 D7::

    explicit CLI or explicit API request for the field/manifest
    > legacy CLI alias (for the field it aliases)
    > selected run recipe
    > selected run profile (execution fields only)
    > project config (including manifests referenced there)
    > built-in default

This module is pure resolution logic over already-collected candidate
values -- it does not read ``argv``, environment variables, or files. No
front end (CLI, ``.abicheck.yml``, service/API) constructs
:class:`FieldCandidate` lists from real input yet; wiring a concrete field
(e.g. ``contract.mode`` from ``cli_options.py``) through this resolver is
Phase 1's remaining slice, tracked in
``docs/contribute/plans/public-contract-default.md``.

Two ADR-049 D7 usage-error rules are enforced here, both raising a
:class:`FieldResolutionError` subclass rather than exiting the process --
mapping that to a concrete exit code (64 for the CLI) is a front end's job,
not this leaf module's:

- "Contradictory values at the same selector layer... are usage errors" --
  :class:`ConflictingFieldValuesError`.
- "a legacy alias that disagrees with an explicit new option" is a usage
  error, **except** the documented ``--policy`` / ``--policy-file``
  compatibility rule, where the explicit option keeps winning --
  :class:`LegacyAliasConflictError`, suppressed by passing
  ``require_legacy_alias_agreement=False``. That exception's provenance
  retains the shadowed legacy input (D7: "provenance records the
  file-selected effective base and the shadowed ``--policy`` input") via
  :attr:`~abicheck.compatibility_evaluation_config.ValueProvenance.shadowed_legacy`.

"Equivalent duplicate values are accepted" (D7) falls out naturally:
multiple candidates at the same precedence tier with the *same* value do
not conflict.

D7 also scopes ``RUN_PROFILE`` precedence to "execution fields only"
(depth, format, budget, workflow) -- it does not apply to semantic fields
like ``contract.mode`` or ``policy.base``. Since this module resolves one
field at a time with no built-in notion of which fields are execution
fields, the caller declares that per call via ``allow_run_profile``
(default ``False``): a ``RUN_PROFILE`` candidate for a field that doesn't
opt in is a caller bug, not a legitimate input, so it raises loudly rather
than silently taking part in precedence.

:func:`detect_pack_conflicts` implements ADR-049 D8's separate pack-level
usage error: "Two selected packs that assign incompatible values to the
same field or ChangeKind are a usage error until an explicit final override
resolves the conflict. Pack order never decides semantics." Like
``resolve_field``, it is pure logic over already-resolved input -- callers
supply each selected pack's identity paired with its own resolved
field-name -> value mapping (a policy pack's ChangeKind-slug -> Verdict
overrides, or a contract/gate pack's own field assignments -- this function
doesn't distinguish the two, since D8's rule is identical for both); how
that content is loaded (a future pack-manifest format) is a front end's job
this module doesn't own.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .compatibility_evaluation_config import ImmutableIdentity, ValueProvenance
from .contract_relevance_types import SelectorLayer

# D7: "explicit CLI or explicit API request for the field/manifest" are the
# same top tier -- a single resolution only ever has one or the other, since
# they come from different front ends, but neither outranks the other.
_PRECEDENCE_TIERS: tuple[frozenset[SelectorLayer], ...] = (
    frozenset({SelectorLayer.EXPLICIT_CLI, SelectorLayer.API_REQUEST}),
    frozenset({SelectorLayer.LEGACY_ALIAS}),
    frozenset({SelectorLayer.RUN_RECIPE}),
    frozenset({SelectorLayer.RUN_PROFILE}),
    frozenset({SelectorLayer.PROJECT_CONFIG}),
    frozenset({SelectorLayer.BUILT_IN_DEFAULT}),
)

_EXPLICIT_LAYERS = frozenset({SelectorLayer.EXPLICIT_CLI, SelectorLayer.API_REQUEST})

# SelectorLayer is documented as extensible (contract_relevance_types.py) --
# a new member added there without updating _PRECEDENCE_TIERS above must not
# silently vanish from resolution (BUILT_IN_DEFAULT always matches, so the
# candidate would just be dropped with no error). resolve_field() checks
# every candidate's layer against this set up front.
_KNOWN_LAYERS: frozenset[SelectorLayer] = frozenset().union(*_PRECEDENCE_TIERS)


@dataclass(frozen=True)
class FieldCandidate:
    """One selector layer's contribution to a field's effective value.

    ``value`` must be hashable (str/int/bool/enum/tuple/frozen dataclass --
    everything ADR-049 D7 fields actually resolve to) since candidates at
    the same tier are deduplicated by equality.
    """

    provenance: ValueProvenance
    value: Hashable

    def __post_init__(self) -> None:
        # An untyped manifest/API adapter passing decoded provenance
        # directly (e.g. FieldCandidate(provenance={}, value="public")) was
        # previously accepted outright -- resolve_field() would then crash
        # with AttributeError reaching `.layer` instead of the deliberate
        # configuration error the effective-config types already produce
        # for this same class of gap (Codex review).
        if not isinstance(self.provenance, ValueProvenance):
            raise TypeError(
                "FieldCandidate.provenance must be a ValueProvenance, not "
                f"{self.provenance!r}."
            )
        # The `value: Hashable` annotation isn't runtime-enforced -- an
        # untyped adapter passing a decoded list/dict was previously
        # accepted outright, and resolve_field() would later crash inside
        # its set comprehensions with "TypeError: unhashable type" even
        # with only one candidate, instead of the deliberate configuration
        # error this constructor exists to produce (Codex review).
        #
        # isinstance(..., Hashable) only checks that the *type* defines
        # __hash__, not that every instance actually hashes -- a tuple
        # nesting an unhashable element (e.g. `([],)`) passes that check but
        # still raises "TypeError: unhashable type: 'list'" from `hash()`
        # itself. Call hash() directly and translate the failure to the same
        # boundary error (Codex review, fresh evidence).
        if not isinstance(self.value, Hashable):
            raise TypeError(
                f"FieldCandidate.value must be hashable, not {self.value!r}."
            )
        try:
            hash(self.value)
        except TypeError as exc:
            raise TypeError(
                f"FieldCandidate.value must be hashable, not {self.value!r} "
                f"(contains an unhashable element: {exc})."
            ) from exc

    @property
    def layer(self) -> SelectorLayer:
        return self.provenance.layer


class FieldResolutionError(ValueError):
    """Base for field-resolution usage errors (ADR-049 D7).

    A front end should catch this and map it to its own usage-error exit
    (CLI exit 64, ADR-037) -- this module has no notion of exit codes.
    """

    field_name: str


class ConflictingFieldValuesError(FieldResolutionError):
    """Two candidates at the same precedence tier disagree.

    ADR-049 D7: "Contradictory values at the same selector layer... are
    usage errors."
    """

    def __init__(self, field_name: str, candidates: Sequence[FieldCandidate]) -> None:
        self.field_name = field_name
        self.candidates = tuple(candidates)
        layers = ", ".join(sorted({c.layer.value for c in self.candidates}))
        super().__init__(
            f"{field_name}: conflicting values supplied at the same "
            f"precedence tier ({layers}) (ADR-049 D7)"
        )


class LegacyAliasConflictError(FieldResolutionError):
    """A legacy alias disagrees with an explicit new option for the same
    field.

    ADR-049 D7: "a legacy alias that disagrees with an explicit new option"
    is a usage error, unless the caller passes
    ``require_legacy_alias_agreement=False`` for the documented
    ``--policy``/``--policy-file`` style compatibility exception.
    """

    def __init__(
        self, field_name: str, explicit: FieldCandidate, legacy: FieldCandidate
    ) -> None:
        self.field_name = field_name
        self.explicit = explicit
        self.legacy = legacy
        super().__init__(
            f"{field_name}: legacy alias value disagrees with an explicit "
            f"CLI/API value for the same field (ADR-049 D7)"
        )


def resolve_field(
    field_name: str,
    candidates: Sequence[FieldCandidate],
    *,
    default: FieldCandidate,
    require_legacy_alias_agreement: bool = True,
    allow_run_profile: bool = False,
) -> tuple[Hashable, ValueProvenance]:
    """Resolve one field's effective value and provenance.

    ``candidates`` holds every non-default layer that contributed a value;
    ``default`` is always the ``BUILT_IN_DEFAULT`` fallback candidate. When
    multiple candidates land in the winning tier with equal values, the
    first one in ``candidates`` order is the provenance of record -- an
    arbitrary but deterministic choice among genuinely equivalent inputs
    (D7 "equivalent duplicates... report the winning selected-by chain").

    ``allow_run_profile`` must be ``True`` for execution fields (depth,
    format, budget, workflow) -- D7 scopes ``RUN_PROFILE`` precedence to
    those only. It defaults to ``False``; a ``RUN_PROFILE`` candidate
    supplied for a field that hasn't opted in raises a plain ``ValueError``.

    Raises :class:`ConflictingFieldValuesError` if two candidates *at the
    same precedence tier* disagree -- checked for every populated tier, not
    only the winning one, since a shadowed layer's contradiction must not
    surface later merely because a higher-precedence override is removed --
    and :class:`LegacyAliasConflictError` if an explicit CLI/API value
    disagrees with a legacy-alias value for the same field, unless
    ``require_legacy_alias_agreement=False`` (the documented
    ``--policy``/``--policy-file`` exception, D7): in that case no error is
    raised, and the winning provenance's ``shadowed_legacy`` records the
    suppressed legacy candidate's provenance for audit/replay. Also raises a
    plain ``ValueError`` if any candidate's layer is not covered by
    ``_PRECEDENCE_TIERS`` -- a resolver/enum mismatch, not a D7 usage error,
    but one that must fail loudly rather than silently dropping the
    candidate (``BUILT_IN_DEFAULT`` always matches some tier, so an unknown
    layer would otherwise resolve to a default or another candidate's value
    with no indication the caller's selection was ignored).
    """
    if default.layer is not SelectorLayer.BUILT_IN_DEFAULT:
        raise ValueError(
            f"{field_name}: default candidate must use "
            f"SelectorLayer.BUILT_IN_DEFAULT, got {default.layer}"
        )

    all_candidates = (*candidates, default)

    for candidate in all_candidates:
        if candidate.layer not in _KNOWN_LAYERS:
            # `.name` assumes a real (or duck-typed enum-shaped) layer --
            # ValueProvenance.layer's `SelectorLayer` annotation isn't
            # runtime-enforced, so an untyped API/manifest adapter could
            # otherwise pass a plain string (e.g. a typo'd layer name) and
            # crash with AttributeError here instead of the documented
            # ValueError (Codex review). getattr falls back to repr() for
            # anything without a `.name` attribute.
            layer_name = getattr(candidate.layer, "name", None)
            layer_desc = (
                f"SelectorLayer.{layer_name}" if layer_name else repr(candidate.layer)
            )
            raise ValueError(
                f"{field_name}: candidate uses {layer_desc}, "
                "which is not represented in any _PRECEDENCE_TIERS entry -- "
                "compatibility_evaluation_resolver.py must be updated when "
                "SelectorLayer gains a new member, or this candidate would "
                "be silently dropped from resolution"
            )
        if candidate.layer is SelectorLayer.RUN_PROFILE and not allow_run_profile:
            raise ValueError(
                f"{field_name}: RUN_PROFILE candidates are only valid for "
                "execution fields -- ADR-049 D7 scopes run-profile precedence "
                "to 'execution fields only' (depth, format, budget, workflow); "
                "pass allow_run_profile=True when resolving one of those"
            )

    explicit_candidates = [c for c in all_candidates if c.layer in _EXPLICIT_LAYERS]
    legacy_candidates = [
        c for c in all_candidates if c.layer is SelectorLayer.LEGACY_ALIAS
    ]
    shadowed_legacy: FieldCandidate | None = None
    if explicit_candidates and legacy_candidates:
        explicit_values = {c.value for c in explicit_candidates}
        legacy_values = {c.value for c in legacy_candidates}
        if (
            len(explicit_values) == 1
            and len(legacy_values) == 1
            and explicit_values != legacy_values
        ):
            if require_legacy_alias_agreement:
                raise LegacyAliasConflictError(
                    field_name, explicit_candidates[0], legacy_candidates[0]
                )
            shadowed_legacy = legacy_candidates[0]

    winner: FieldCandidate | None = None
    for tier in _PRECEDENCE_TIERS:
        tier_candidates = [c for c in all_candidates if c.layer in tier]
        if not tier_candidates:
            continue
        # ADR-049 D7's "contradictory values at the same selector layer are
        # usage errors" is not scoped to only the winning tier: a shadowed
        # layer's internal conflict must still be caught now, not silently
        # exposed later if the higher-precedence override is ever removed.
        if len({c.value for c in tier_candidates}) > 1:
            raise ConflictingFieldValuesError(field_name, tier_candidates)
        if winner is None:
            winner = tier_candidates[0]

    if winner is not None:
        provenance = winner.provenance
        if shadowed_legacy is not None:
            provenance = dataclasses.replace(
                provenance, shadowed_legacy=shadowed_legacy.provenance
            )
        return winner.value, provenance

    raise AssertionError(  # pragma: no cover - default always matches a tier
        f"{field_name}: no candidate matched any precedence tier"
    )


class PackConflictError(ValueError):
    """Two selected packs assign different values to the same field.

    ADR-049 D8: "Two selected packs that assign incompatible values to the
    same field or ChangeKind are a usage error until an explicit final
    override resolves the conflict."
    """

    def __init__(
        self,
        field_name: str,
        contributors: Sequence[tuple[ImmutableIdentity, Hashable]],
    ) -> None:
        self.field_name = field_name
        self.contributors = tuple(contributors)
        packs = ", ".join(
            f"{identity.id}@{identity.version} -> {value!r}"
            for identity, value in sorted(
                self.contributors, key=lambda c: (c[0].id, c[0].version, c[0].sha256)
            )
        )
        super().__init__(
            f"{field_name!r}: conflicting values from selected packs "
            f"({packs}) (ADR-049 D8) -- add an explicit override entry for "
            f"{field_name!r} to resolve the conflict"
        )


def detect_pack_conflicts(
    pack_assignments: Sequence[tuple[ImmutableIdentity, Mapping[str, Hashable]]],
    *,
    explicit_overrides: Mapping[str, Hashable] = MappingProxyType({}),
) -> None:
    """Raise :class:`PackConflictError` if two selected packs disagree.

    ``pack_assignments`` pairs each selected pack's :class:`ImmutableIdentity`
    with its own resolved field-name -> value mapping. D8's rule covers "the
    same field or ChangeKind" -- a policy pack's ``ChangeKind`` slug ->
    :class:`~abicheck.change_registry_types.Verdict` overrides and a
    contract/gate pack's own field assignments are both just string-keyed
    mappings to this function, since the conflict rule is identical either
    way; it doesn't need to know which namespace it's checking.
    ``explicit_overrides`` is the effective config's own explicit override
    for that same namespace (e.g. ``policy.overrides`` for policy packs,
    ADR-049 D8 composition order: "explicit ... override > selected packs >
    base policy") -- a field already covered there is exempt from conflict
    detection, since the explicit override already resolves any
    pack-vs-pack disagreement on it.

    Two packs that assign the *same* value to the same field never conflict
    (equivalent duplicates are fine, mirroring D7's rule for field-level
    candidates). Iteration and error ordering are both key-sorted, so which
    conflict is detected first never depends on ``pack_assignments`` input
    order ("pack order never decides semantics").
    """
    # The `Mapping[str, Hashable]` annotation isn't runtime-enforced -- an
    # untyped pack adapter passing a plain collection (e.g.
    # explicit_overrides=["exit_code_scheme"]) was previously accepted
    # outright, since `field_name in explicit_overrides` below is a valid
    # membership check on a list too. That silently treated *any* field
    # named in the collection as override-covered and suppressed a genuine
    # pack-vs-pack conflict on it, even though no override value was
    # actually supplied (Codex review).
    if not isinstance(explicit_overrides, Mapping):
        raise TypeError(
            "detect_pack_conflicts: explicit_overrides must be a "
            f"Mapping[str, Hashable], not {explicit_overrides!r}."
        )
    by_field: dict[str, list[tuple[ImmutableIdentity, Hashable]]] = {}
    for identity, assignments in pack_assignments:
        # A future untyped pack-manifest adapter supplying a bare/decoded
        # identity (e.g. a plain str) instead of ImmutableIdentity was
        # previously accepted outright at ingestion -- a non-conflicting
        # assignment would silently pass, while an actual conflict crashed
        # inside PackConflictError's `identity.id`/`identity.version`
        # access with an uncontextualized AttributeError instead of the
        # promised pack-conflict usage error (Codex review).
        if not isinstance(identity, ImmutableIdentity):
            raise TypeError(
                "detect_pack_conflicts: every pack identity must be an "
                f"ImmutableIdentity, not {identity!r}."
            )
        # An untyped pack-manifest adapter supplying a decoded non-mapping
        # assignment block (e.g. (identity, ["contract.mode"])) previously
        # reached `.items()` unvalidated, crashing with "AttributeError:
        # 'list' object has no attribute 'items'" instead of the deliberate
        # configuration error this function exists to produce -- the same
        # gap already closed for explicit_overrides above (Codex review).
        if not isinstance(assignments, Mapping):
            raise TypeError(
                "detect_pack_conflicts: every pack's assignments must be a "
                f"Mapping[str, Hashable], not {assignments!r} (from pack "
                f"{identity!r})."
            )
        for field_name, value in assignments.items():
            # A non-str field/ChangeKind-slug key (e.g. an untyped pack
            # adapter supplying {2: "legacy"}) previously reached
            # sorted(by_field) unvalidated below, crashing with "TypeError:
            # '<' not supported between instances of 'int' and 'str'"
            # instead of the deliberate PackConflictError this function
            # exists to raise (Codex review).
            if not isinstance(field_name, str):
                raise TypeError(
                    "detect_pack_conflicts: every assignment key must be a "
                    f"str, not {field_name!r} (from pack {identity!r})."
                )
            # An untyped pack adapter supplying a decoded collection-valued
            # assignment (e.g. {"contract.overlays": ["api"]}) previously
            # reached the `{value for _, value in contributors}` set
            # comprehension below unvalidated, crashing with an
            # uncontextualized "TypeError: unhashable type: 'list'" instead
            # of a message identifying which pack/field caused it --
            # FieldCandidate.__post_init__ guards this same
            # annotation-isn't-runtime-enforced gap for individual
            # candidates, but this separate pack-conflict path wasn't
            # covered (Codex review).
            try:
                hash(value)
            except TypeError as exc:
                raise TypeError(
                    "detect_pack_conflicts: assignment value for field "
                    f"{field_name!r} from pack {identity!r} must be "
                    f"hashable, not {value!r} ({exc})."
                ) from exc
            by_field.setdefault(field_name, []).append((identity, value))

    for field_name in sorted(by_field):
        if field_name in explicit_overrides:
            continue
        contributors = by_field[field_name]
        if len({value for _, value in contributors}) > 1:
            raise PackConflictError(field_name, contributors)
