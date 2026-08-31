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

"""Policy-parameterised comparison -- the ``service.py`` half that needed
``PolicyFile``/``SuppressionList``.

ADR-061 Phase 4's ``service.py`` thinning slice moved ``resolve_input`` and
friends into ``workflows/input_resolution.py`` early because that half had
zero ``PolicyFile`` dependency; ``compare_snapshots``/
``load_suppression_and_policy``/``_validate_contract_mode``/
``dedup_policy_override_warnings`` stayed behind because moving them into a
real, migrated ``workflows/`` package location would have made the
destination file ``migrated_source`` -- and at the time, ``policy_file.py``
was still unclassified, so an import of it from here would have tripped
``unclassified-import`` unconditionally (unlike ``service.py``'s own flat,
``legacy_paths``-classified copy of the identical import, which
``check_architecture.py`` never checks that way).

That blocker is closed: ``abicheck/policy_file.py`` is now classified
``policy`` in ``architecture/modules.yaml`` (the ``model``-owned
``PolicyFileProtocol``/``ReclassifyRuleProtocol`` pair in
``model/policy_file_protocol.py`` resolved the one real edge that
reclassification surfaced -- ``checker_types.py``'s own ``DiffResult.
policy_file`` field type -- see that module's docstring for the mechanism
and the ADR's Phase 4 section for the full investigation this closes). This
module is these four functions' real, physical home; ``service.py``
re-exports them via a plain static import (``workflows -> workflows``, not
the forbidden ``workflows -> frontends`` shape ``render.py`` needed an
``importlib`` bridge for -- nothing this module imports reaches back to
``service.py`` or the pre-existing CLI-registration SCC, so no cycle risk
forces that mechanism here).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..checker import compare
from ..checker_types import DiffResult
from ..errors import ValidationError
from ..model import AbiSnapshot
from ..policy.public_surface import PublicSurfaceQuery
from ..policy_file import (
    dedup_validate_overrides_warnings as _dedup_validate_overrides_warnings,
    pending_validate_overrides_warnings as _pending_validate_overrides_warnings,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..environment_matrix import EnvironmentMatrix
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList

_logger = logging.getLogger(__name__)

# Codex review, PR #730: re-exported so an existing `service.
# dedup_policy_override_warnings()` caller (and this module's own tests)
# keep working. The dedup state itself lives in `policy_file.py` -- the leaf
# module both this loader and `cli_params._load_suppression_and_policy` share
# -- specifically so *one* scope dedupes a warning repeated across both
# loaders, not just repeated calls to this one. See
# `policy_file.dedup_validate_overrides_warnings`'s own docstring for what
# this does and does not cover.
dedup_policy_override_warnings = _dedup_validate_overrides_warnings


def load_suppression_and_policy(
    suppress: Path | None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from paths.

    Raises:
        ValidationError: If the suppression or policy file is invalid.
    """
    from ..policy_file import PolicyFile as _PolicyFile
    from ..suppression import SuppressionList as _SuppressionList

    suppression: _SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = _SuppressionList.load(suppress)
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid suppression file: {e}") from e

    pf: _PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = _PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise ValidationError(str(e)) from e
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid policy file: {e}") from e
        if policy != "strict_abi":
            # Named as Tier-2 *parameters*, not CLI flags: the CLI merged
            # --policy/--policy-file into one --policy that routes an operand
            # to exactly one of these, so it can no longer set both and this
            # branch is now reachable only from a typed API caller, for whom
            # the flag spellings would name nothing.
            _logger.warning(
                "policy=%r is ignored when policy_file_path is given. "
                "Set base_policy in the YAML file to override the base policy.",
                policy,
            )
        # The Tier-2 chokepoint every non-CLI-validation consumer loads its
        # policy through, none of which reach `cli_params._load_suppression_
        # and_policy`'s own `click.echo` warning (Codex review). Routed
        # through `pending_validate_overrides_warnings` so a shared
        # `dedup_policy_override_warnings()` scope collapses repeats to one.
        for warning in _pending_validate_overrides_warnings(pf):
            _logger.warning("%s", warning)
    return suppression, pf


def _validate_contract_mode(
    contract_mode: str | None, contract_evaluation: bool
) -> None:
    """Apply ADR-049 Phase 6's two ``contract_mode`` rules at a Tier-2 entry.

    Same allowed values and same ``contract_evaluation`` dependency as
    ``CompareRequest.validation_errors`` and the CLI's ``--contract``, so the
    three front ends cannot disagree about what is accepted.
    """
    if contract_mode is None:
        return
    from ..contract_relevance_types import ContractMode

    allowed = {mode.value for mode in ContractMode}
    if contract_mode not in allowed:
        raise ValidationError(
            f"unsupported contract mode {contract_mode!r}: "
            f"choose from {', '.join(sorted(allowed))}"
        )
    if not contract_evaluation:
        raise ValidationError(
            "contract_mode requires contract_evaluation: it selects which "
            "evidence domain the shadow contract evaluator judges against, "
            "and without that flag no contract decision is computed at all"
        )


def compare_snapshots(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    extra_changes: list[Change] | None = None,
    pattern_verdicts: bool = False,
    surface_metrics: bool = False,
    collapse_versioned_symbols: bool = False,
    public_surface_allowlist: set[str] | None = None,
    reconcile_build_context: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
    diagnostic_comparison: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
) -> DiffResult:
    """Classify two already-resolved snapshots — the Tier-2 snapshot verb.

    Thin wrapper over the Tier-1 core (:func:`abicheck.checker.compare`) so that
    *front-ends never call the core directly* (ADR-037 D1/D10.1). Front-ends
    that have already resolved their own snapshots (the native ``compare``
    command with embedded build-source evidence, ``scan``, ``appcompat``) route
    through here instead of importing ``checker.compare``; the kwargs mirror the
    core verb exactly so no capability is lost.

    ADR-063 Phase 3 (D5): resolves each side's own public-surface
    ``EntityId`` set via ``PublicSurfaceQuery().resolve()`` and forwards the
    pair into :func:`~abicheck.checker.compare` as *old_public_entity_ids*/
    *new_public_entity_ids* -- ``compare()`` itself never resolves this
    (``policy/`` -> ``compare/`` stays a one-way edge), so this Tier-2
    chokepoint is where it happens for every caller that reaches ``compare()``
    through here, ``service_compare_pipeline.classify_compare_pair`` included.

    Raises:
        ValidationError: *contract_mode* is not one of ``public``/``exports``/
            ``all``, or is given without *contract_evaluation*. This is a
            documented Tier-2 entry point that direct Python callers reach
            without building a ``CompareRequest``, so it applies the same two
            rules that request object and the CLI do rather than silently
            accepting a no-op or failing later inside the core (Codex review).
    """
    _validate_contract_mode(contract_mode, contract_evaluation)
    # Centralized POST committed-wrapper recovery: when a committed-surface
    # allowlist is supplied, union the callable `pp_*` wrappers exported by
    # the old snapshot (contract_scope_allowlist's snapshot half) -- keeps
    # both dropped and still-exported-but-omitted wrappers in-surface, so a
    # scope against a new manifest can't hide an ABI break via omission.
    # Every scope caller routes through here (one place, no-op with no
    # `pp_*` wrappers, idempotent if already unioned).
    if public_surface_allowlist is not None:
        from ..post_manifest import _snapshot_contract_symbols

        public_surface_allowlist = set(
            public_surface_allowlist
        ) | _snapshot_contract_symbols(old)
    query = PublicSurfaceQuery()
    return compare(
        old,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        extra_changes=extra_changes,
        pattern_verdicts=pattern_verdicts,
        surface_metrics=surface_metrics,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
        reconcile_build_context=reconcile_build_context,
        env_matrix=env_matrix,
        diagnostic_comparison=diagnostic_comparison,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
        old_public_entity_ids=query.resolve(old),
        new_public_entity_ids=query.resolve(new),
    )


__all__ = [
    "compare_snapshots",
    "dedup_policy_override_warnings",
    "load_suppression_and_policy",
]
