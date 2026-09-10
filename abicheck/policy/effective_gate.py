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

"""``EffectiveGate`` -- the one gate-configuration runtime shape
(``docs/contribute/plans/duplication-and-convergence-assessment.md``'s P0
"Effective configuration and pack application" target), and the one
fold-time value type its two producers now share.

**Where this sits relative to T6.** ``gate_pack_fold.fold_gate_pack_severity``
already unified the *algorithm* both ``pack_application.
apply_to_compare_config`` (single-pair ``compare``) and ``release_gate_
options.apply_release_gate_pack`` (the directory/package release fan-out)
apply -- see that module's own docstring. What stayed unshared afterward,
named explicitly in this plan's own P0 section and pinned as a still-open
gap by ``tests/test_release_gate_pack_fold_parity.py``'s docstring, is the
*shape* each caller folds onto: an already-resolved
:class:`~abicheck.policy.severity.SeverityConfig` for ``compare``, four
independent raw optional strings for the release fan-out -- a real
difference (the release fan-out must keep "no severity setting is in
effect" distinguishable from "the default levels", which a resolved
``SeverityConfig`` cannot express), so unifying it needed a *third*,
provenance-preserving shape rather than forcing one caller onto the other's.

:class:`GateSeverityState` is that shape. Both callers now construct one from
their own native representation, fold a pack's contribution through the one
:meth:`GateSeverityState.folded` method (itself calling the identical
``fold_gate_pack_severity`` T6 already shared), and read the result back --
so "two different shapes" narrows to "two different *constructors* for one
shared fold-time type", the literal target this plan section names.

:class:`EffectiveGate` is the plan's own named target object -- the resolved
gate configuration a caller actually runs with, not merely the fold's
intermediate state. Both :class:`~abicheck.policy.release_gate_options.
GateOptions` (the release fan-out's resolved object) and
``abicheck.cli_helpers_compare.ResolvedCompareConfig`` (single-pair
``compare``'s) expose an ``effective_gate`` property built through
:meth:`EffectiveGate.from_severity`, so a caller holding either object can
ask for the same-shaped answer. This is deliberately narrower than the
plan's full ``EffectiveEvaluationConfig`` (which would also carry policy/
contract/assurance/surface/evidence/suppressions namespaces no release-fan-
out-facing object resolves today) -- see that section's own "Target" text;
extending this object to the other namespaces, and to a real digest over it,
is left to a future slice rather than attempted speculatively here.

A leaf: imports nothing from ``abicheck`` outside ``policy`` itself, so
either caller can depend on it without acquiring the other's dependencies --
the same reasoning ``gate_pack_fold.py``'s own docstring gives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .gate_pack_fold import (
    GATE_SEVERITY_CATEGORIES,
    fold_gate_pack_severity,
    gate_exit_code_scheme,
)
from .severity import SeverityConfig

__all__ = [
    "EffectiveGate",
    "GateSeverityState",
    "ScopedGateSelection",
]


@dataclass(frozen=True)
class GateSeverityState:
    """The one fold-time value both ``gate_pack_fold``'s two callers now
    construct, fold, and read back -- see this module's own docstring for
    why a third shape (rather than forcing one caller onto the other's) is
    the correct unification here.

    *levels* holds exactly :data:`~abicheck.policy.gate_pack_fold.
    GATE_SEVERITY_CATEGORIES` as keys, in whichever vocabulary the caller
    carries them (resolved ``SeverityLevel`` members for ``compare``; raw
    ``str | None`` CLI/config strings for the release fan-out) -- the same
    genericity ``fold_gate_pack_severity`` itself already documents.
    *active* is that caller's own "is a severity setting in effect" fact
    *before* this fold; :meth:`folded` never has to guess it, since a pack
    contribution can only ever turn ``active`` on, never off.
    """

    levels: Mapping[str, Any]
    active: bool

    def __post_init__(self) -> None:
        missing = set(GATE_SEVERITY_CATEGORIES) - set(self.levels)
        if missing:
            raise ValueError(
                f"GateSeverityState.levels is missing categories {sorted(missing)}; "
                f"it must carry exactly {list(GATE_SEVERITY_CATEGORIES)}"
            )

    def folded(self, pack_levels: Mapping[str, Any]) -> GateSeverityState:
        """*pack_levels* folded onto this state (:func:`~abicheck.policy.
        gate_pack_fold.fold_gate_pack_severity`), returning a fresh state.

        A no-op (returns ``self``, not merely an equal copy) when
        *pack_levels* is empty -- both existing callers relied on exactly
        this shortcut (the release fan-out to skip resolving a
        :class:`SeverityConfig` it would otherwise have to immediately
        discard; ``compare`` to avoid marking ``severity_active`` when no
        pack was selected at all), so preserving it here keeps this a
        behavior-neutral refactor of the *shape*, not a second change to the
        *fold* T6 already settled. Otherwise ``active`` becomes ``True``: a
        real pack contribution *is* a severity setting coming into effect,
        by both callers' own pre-existing behavior.
        """
        if not pack_levels:
            return self
        return GateSeverityState(
            levels=fold_gate_pack_severity(self.levels, pack_levels),
            active=True,
        )


@dataclass(frozen=True)
class ScopedGateSelection:
    """An ADR-043 scoped-gate selection (``--used-by``/``--required-symbol``)
    -- which consumer or entrypoint narrowed the gate, and to what. Mirrors
    ``effective_config_digest._gate_scope_str``'s own encoding (``kind`` is
    ``"used_by"``/``"required_symbol"``; ``targets`` the resolved consumer
    paths or required entrypoints), given a real typed home here rather than
    only a digest-string projection.
    """

    kind: str
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectiveGate:
    """The one resolved gate-configuration object
    (duplication-and-convergence-assessment plan's P0 target, narrowed to
    the gate namespace alone -- see this module's own docstring for why the
    full ``EffectiveEvaluationConfig`` is not attempted here).

    ``severity is None`` means "no severity setting is in effect for this
    run" -- the same single source of truth
    :class:`~abicheck.policy.release_gate_options.GateOptions` already
    documents for its own field of the same name. ``exit_code_scheme`` is
    always the one shared derivation
    (:func:`~abicheck.policy.gate_pack_fold.gate_exit_code_scheme`) applied
    to that fact, never an independently stated value -- the same structural
    invariant ``GateOptions.exit_code_scheme``/``ResolvedCompareConfig.
    exit_code_scheme`` already enforce by making it a derived property.
    ``require_complete_analysis``/``scope`` are carried alongside for the
    identical reason ``effective_config_digest.py``'s own field-key comment
    already gives for including them next to severity: each can change an
    otherwise-identical run's gate outcome on its own, so a caller comparing
    two ``EffectiveGate`` values for "would these gate the same way" must see
    all four fields, not severity alone.
    """

    exit_code_scheme: str
    severity: SeverityConfig | None
    require_complete_analysis: bool = False
    scope: ScopedGateSelection | None = None

    @classmethod
    def from_severity(
        cls,
        severity: SeverityConfig | None,
        *,
        require_complete_analysis: bool = False,
        scope: ScopedGateSelection | None = None,
    ) -> EffectiveGate:
        """Build the one derived-scheme ``EffectiveGate`` for *severity*
        (``None`` meaning "no severity setting is in effect") -- the single
        constructor both ``GateOptions.effective_gate`` and
        ``ResolvedCompareConfig.effective_gate`` call, so neither can
        independently mis-derive ``exit_code_scheme``.
        """
        return cls(
            exit_code_scheme=gate_exit_code_scheme(severity is not None),
            severity=severity,
            require_complete_analysis=require_complete_analysis,
            scope=scope,
        )
