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

"""ADR-049 Phase 1's real front-end wirings: real CLI/config inputs ->
resolved ``CompatibilityEvaluationConfig`` fields.

Every other ADR-049 module built so far (``contract_relevance_types.py``,
``compatibility_evaluation_config.py``, ``compatibility_evaluation_resolver.py``)
is pure vocabulary/shape/resolution logic. This module is where real input
first reaches :func:`~abicheck.compatibility_evaluation_resolver.resolve_field`:

- :func:`resolve_legacy_contract_mode` takes the *actual*
  ``--scope-public-headers``/``--no-scope-public-headers`` CLI flag
  (``cli_options.py``'s ``scope_options``, the only scope-shaped option that
  exists in the CLI today) and produces a real ``contract.mode`` resolution,
  via the D2 alias table in
  :data:`~abicheck.contract_relevance_types.LEGACY_SCOPE_FLAG_CONTRACT_MODE`.
- :func:`resolve_internal_namespaces` takes the *actual* ``--policy-file``
  YAML's ``internal_namespaces`` list (``policy_file.py``'s ``PolicyFile``,
  the only front end that can set this field today -- there is no CLI flag
  for it) and produces a real ``surface.internal_namespaces`` resolution.

Neither function is called from any live command yet. ADR-049's own rollout
plan puts wiring a resolved value into an authoritative code path behind a
later phase (Phase 3's shadow contract evaluator validates resolution
against real traffic before it can affect a gate decision, and the default
flip is Phase 7) -- see
``docs/contribute/plans/public-contract-default.md``. Landing each wiring
function itself, fully tested against its real input's semantics, is what
Phase 1's gate asks for: "every front end resolves equivalent semantic
input to an equal ``CompatibilityEvaluationConfig`` and provenance receipt."
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from .compatibility_evaluation_config import SelectedByEntry, ValueProvenance
from .compatibility_evaluation_resolver import FieldCandidate, resolve_field
from .contract_relevance_types import (
    LEGACY_SCOPE_FLAG_CONTRACT_MODE,
    ContractMode,
    LegacyScopeFlag,
    SelectorLayer,
)

if TYPE_CHECKING:
    from .policy_file import PolicyFile

_CONTRACT_MODE_FIELD = "contract.mode"

#: What ``contract.mode`` resolves to when the legacy flag was never
#: mentioned at all -- deliberately equal to ``--scope-public-headers``'s
#: own CLI default (``scope_public_headers=True`` in ``cli_options.py``),
#: so accepting ADR-049 does not, by itself, change today's real default
#: behavior. The actual default *flip* (if any) is Phase 7's decision.
_BUILT_IN_DEFAULT_MODE = LEGACY_SCOPE_FLAG_CONTRACT_MODE[
    LegacyScopeFlag.SCOPE_PUBLIC_HEADERS
]


def resolve_legacy_contract_mode(
    *,
    scope_public_headers: bool,
    scope_public_headers_is_explicit: bool,
) -> tuple[ContractMode, ValueProvenance]:
    """Resolve ``contract.mode`` from the real legacy scope flag.

    ``scope_public_headers`` is the flag's boolean value (``cli_options.py``
    ``scope_options``'s ``scope_public_headers`` destination).
    ``scope_public_headers_is_explicit`` distinguishes the user actually
    typing ``--scope-public-headers``/``--no-scope-public-headers`` (a real
    ``legacy_alias``-layer input, e.g. via Click's
    ``ctx.get_parameter_source(...) is ParameterSource.COMMANDLINE``) from
    the flag merely carrying its own click default -- an untouched flag
    contributes no candidate at all and falls through to
    :data:`_BUILT_IN_DEFAULT_MODE`, matching ADR-049 D7's precedence model
    (a selector layer only participates when it actually selected
    something).

    Returns the resolved :class:`~abicheck.contract_relevance_types.ContractMode`
    and its :class:`~abicheck.compatibility_evaluation_config.ValueProvenance`.
    This performs no I/O and touches no CLI/Click objects directly, so it is
    testable with plain booleans.
    """
    default = FieldCandidate(
        provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
        value=_BUILT_IN_DEFAULT_MODE,
    )

    candidates: list[FieldCandidate] = []
    if scope_public_headers_is_explicit:
        flag = (
            LegacyScopeFlag.SCOPE_PUBLIC_HEADERS
            if scope_public_headers
            else LegacyScopeFlag.NO_SCOPE_PUBLIC_HEADERS
        )
        option = (
            "--scope-public-headers"
            if scope_public_headers
            else "--no-scope-public-headers"
        )
        candidates.append(
            FieldCandidate(
                provenance=ValueProvenance(
                    layer=SelectorLayer.LEGACY_ALIAS,
                    source_kind="legacy_scope_flag",
                    reference=flag.value,
                    selected_by=(
                        SelectedByEntry(
                            layer=SelectorLayer.LEGACY_ALIAS, option=option
                        ),
                    ),
                ),
                value=LEGACY_SCOPE_FLAG_CONTRACT_MODE[flag],
            )
        )

    value, provenance = resolve_field(_CONTRACT_MODE_FIELD, candidates, default=default)
    return cast(ContractMode, value), provenance


_INTERNAL_NAMESPACES_FIELD = "surface.internal_namespaces"

#: What ``surface.internal_namespaces`` resolves to when no ``--policy-file``
#: set it -- equal to ``SurfaceConfig.internal_namespaces``'s own default
#: (empty tuple), so accepting ADR-049 does not, by itself, change today's
#: real default behavior (each consuming step falls back to its own
#: ``DEFAULT_INTERNAL_NAMESPACES``, exactly as it does today with no policy
#: file at all).
_BUILT_IN_DEFAULT_INTERNAL_NAMESPACES: tuple[str, ...] = ()


def resolve_internal_namespaces(
    *, policy_file: PolicyFile | None
) -> tuple[tuple[str, ...], ValueProvenance]:
    """Resolve ``surface.internal_namespaces`` from a real ``--policy-file``.

    ``internal_namespaces`` has no CLI flag of its own -- ``policy_file.py``'s
    ``PolicyFile.internal_namespaces`` (populated only when a real
    ``--policy-file`` YAML sets the key) is the only front end that can set
    it today. ``policy_file is None`` (no ``--policy-file`` given at all) or
    an empty ``internal_namespaces`` list (indistinguishable, once parsed,
    from the key never being set) both contribute no candidate at all and
    fall through to :data:`_BUILT_IN_DEFAULT_INTERNAL_NAMESPACES`, the same
    "a selector layer only participates when it actually selected something"
    principle :func:`resolve_legacy_contract_mode`'s untouched-flag case
    already applies (ADR-049 D7).

    Sorts and dedupes the list before building the candidate, mirroring
    ``SurfaceConfig.__post_init__``'s own canonicalization and
    ``compatibility_evaluation_packs.py``'s
    ``_canonicalize_order_insensitive_field`` (both already treat
    ``surface.internal_namespaces`` as an order-insensitive set, not an
    ordered sequence) -- two policy files listing the same namespaces in a
    different order must resolve to the same value, per D7's "equivalent
    semantic inputs must resolve to an equivalent object".

    Returns the resolved ``tuple[str, ...]`` and its
    :class:`~abicheck.compatibility_evaluation_config.ValueProvenance`. This
    performs no I/O -- *policy_file*, if given, must already be loaded by the
    caller (e.g. via ``PolicyFile.load``).
    """
    default = FieldCandidate(
        provenance=ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT),
        value=_BUILT_IN_DEFAULT_INTERNAL_NAMESPACES,
    )

    candidates: list[FieldCandidate] = []
    if policy_file is not None and policy_file.internal_namespaces:
        canonical = tuple(dict.fromkeys(sorted(policy_file.internal_namespaces)))
        source_path = str(policy_file.source_path) if policy_file.source_path else None
        candidates.append(
            FieldCandidate(
                provenance=ValueProvenance(
                    layer=SelectorLayer.PROJECT_CONFIG,
                    source_kind="policy_file",
                    path=source_path,
                    selected_by=(
                        SelectedByEntry(
                            layer=SelectorLayer.PROJECT_CONFIG,
                            option="--policy-file",
                            path=source_path,
                        ),
                    ),
                ),
                value=canonical,
            )
        )

    value, provenance = resolve_field(
        _INTERNAL_NAMESPACES_FIELD, candidates, default=default
    )
    return cast("tuple[str, ...]", value), provenance
