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
  ``require_legacy_alias_agreement=False``.

"Equivalent duplicate values are accepted" (D7) falls out naturally:
multiple candidates at the same precedence tier with the *same* value do
not conflict.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

from .compatibility_evaluation_config import ValueProvenance
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


@dataclass(frozen=True)
class FieldCandidate:
    """One selector layer's contribution to a field's effective value.

    ``value`` must be hashable (str/int/bool/enum/tuple/frozen dataclass --
    everything ADR-049 D7 fields actually resolve to) since candidates at
    the same tier are deduplicated by equality.
    """

    provenance: ValueProvenance
    value: Hashable

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
) -> tuple[Hashable, ValueProvenance]:
    """Resolve one field's effective value and provenance.

    ``candidates`` holds every non-default layer that contributed a value;
    ``default`` is always the ``BUILT_IN_DEFAULT`` fallback candidate. When
    multiple candidates land in the winning tier with equal values, the
    first one in ``candidates`` order is the provenance of record -- an
    arbitrary but deterministic choice among genuinely equivalent inputs
    (D7 "equivalent duplicates... report the winning selected-by chain").

    Raises :class:`ConflictingFieldValuesError` if two candidates *at the
    same precedence tier* disagree -- checked for every populated tier, not
    only the winning one, since a shadowed layer's contradiction must not
    surface later merely because a higher-precedence override is removed --
    and :class:`LegacyAliasConflictError` if an explicit CLI/API value
    disagrees with a legacy-alias value for the same field, unless
    ``require_legacy_alias_agreement=False``.
    """
    if default.layer is not SelectorLayer.BUILT_IN_DEFAULT:
        raise ValueError(
            f"{field_name}: default candidate must use "
            f"SelectorLayer.BUILT_IN_DEFAULT, got {default.layer}"
        )

    all_candidates = (*candidates, default)

    if require_legacy_alias_agreement:
        explicit = [c for c in all_candidates if c.layer in _EXPLICIT_LAYERS]
        legacy = [c for c in all_candidates if c.layer is SelectorLayer.LEGACY_ALIAS]
        if explicit and legacy:
            explicit_values = {c.value for c in explicit}
            legacy_values = {c.value for c in legacy}
            if (
                len(explicit_values) == 1
                and len(legacy_values) == 1
                and explicit_values != legacy_values
            ):
                raise LegacyAliasConflictError(field_name, explicit[0], legacy[0])

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
        return winner.value, winner.provenance

    raise AssertionError(  # pragma: no cover - default always matches a tier
        f"{field_name}: no candidate matched any precedence tier"
    )
