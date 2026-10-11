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

"""A single-pair compare's stated configuration, resolved once for every front end.

Two steps decide what a comparison is scored under beyond its loaded
``--policy``/``--suppress`` files:

* :func:`resolve_compare_configuration` -- ADR-049 D7/D8: resolve the
  effective configuration, reject a pack this build or invocation cannot
  apply, and fold the selected packs' ``policy.overrides``/
  ``surface.internal_namespaces`` into the policy file. The returned
  :class:`~abicheck.pack_application.PackApplication` also carries a gate
  pack's ``gate.severity.*`` levels, which each front end folds into its own
  gate (the CLI's ``ResolvedCompareConfig``, the typed API's ``GateOptions``)
  through the one shared ``policy.gate_pack_fold``.
* :func:`project_policy_overrides` / :func:`fold_project_policy_overrides` --
  ADR-068 §3 #23: ``.abicheck.yml``'s ``policy.overrides`` at the weakest
  (``PROJECT_CONFIG``) tier, folded strictly after the packs.

Before this module, the native ``compare`` CLI ran both inside
``cli_compare_receipt.resolve_and_apply`` and ``cli_compare_helpers``, so a
``CompareRequest`` could name no pack and a gate pack could move a severity
level only on the CLI. Both front ends now call these functions; each keeps
only its own input adapter (``compare_cli_inputs`` vs
``compare_request_inputs``) and maps :class:`CompareConfigurationError` onto
its own error surface (exit 64 for the CLI, the exception itself for a typed
caller).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import PackManifestError, PolicyError, ValidationError

if TYPE_CHECKING:
    from ..change_registry_types import Verdict
    from ..compatibility_evaluation_frontend import (
        ExplicitCompatibilityInputs,
        FrontEnd,
        ProjectCompatibilityInputs,
    )
    from ..model.change_catalog.kinds import ChangeKind
    from ..pack_application import PackApplication
    from ..policy_file import PolicyFile

__all__ = [
    "CompareConfiguration",
    "CompareConfigurationError",
    "ConfigurationConflictError",
    "ProjectPolicyFold",
    "ProjectPolicyOverridesError",
    "fold_project_policy_overrides",
    "project_policy_overrides",
    "resolve_compare_configuration",
]


class CompareConfigurationError(ValidationError):
    """The stated configuration of a compare cannot be resolved."""


class ConfigurationConflictError(CompareConfigurationError):
    """A D7 same-tier conflict, a D8 pack conflict, or an inapplicable pack."""


class ProjectPolicyOverridesError(CompareConfigurationError):
    """A malformed ``.abicheck.yml`` ``policy.overrides`` entry."""


@dataclass(frozen=True)
class CompareConfiguration:
    """What :func:`resolve_compare_configuration` decided.

    ``evaluation_config`` is ``None`` exactly when nothing would read one (no
    contract evaluation and no pack, unless the caller asked to resolve
    anyway). ``policy_file`` is the loaded policy with the packs folded in,
    before the project-config fold. ``pack_application`` is ``None`` when no
    pack was selected.
    """

    evaluation_config: Any
    policy_file: PolicyFile | None
    pack_application: PackApplication | None = None


def resolve_compare_configuration(
    explicit: ExplicitCompatibilityInputs,
    *,
    front_end: FrontEnd,
    project: ProjectCompatibilityInputs | None,
    policy_file: PolicyFile | None,
    base_policy: str,
    contract_evaluation: bool,
    always_resolve: bool = False,
) -> CompareConfiguration:
    """Resolve *explicit* + *project*, then let the selected packs configure the run.

    The packs are ``explicit.pack_paths`` and nothing else, so the receipt
    and the application cannot name different packs. The configuration is
    resolved from the *explicitly given* *policy_file* and only then are the
    packs folded into a new one: folding first would present a pack's
    override to the resolver as an explicitly stated value.

    With no pack and no contract evaluation nothing reads a resolution, so
    none is computed unless *always_resolve* (the typed API, which always
    records one): resolving anyway would make an ordinary run newly able to
    fail on a D7 conflict it never computed before.

    Raises :class:`ConfigurationConflictError` or
    :class:`ProjectPolicyOverridesError`.
    """
    from ..compatibility_evaluation_frontend import (
        resolve_compatibility_evaluation_config,
    )
    from ..compatibility_evaluation_resolver import (
        FieldResolutionError,
        PackConflictError,
    )
    from ..pack_application import (
        check_resolved_config_applies_packs,
        pack_application,
        policy_file_with_packs,
    )

    if not (contract_evaluation or explicit.pack_paths or always_resolve):
        return CompareConfiguration(None, policy_file)
    try:
        config = resolve_compatibility_evaluation_config(
            front_end=front_end, explicit=explicit, project=project
        )
        if not explicit.pack_paths:
            return CompareConfiguration(config, policy_file)
        # Asked of the resolution, not of a second read of the files: the
        # resolver already loaded the revision that configures the run.
        check_resolved_config_applies_packs(
            config, contract_evaluation=contract_evaluation
        )
    except (FieldResolutionError, PackConflictError, PackManifestError) as exc:
        raise ConfigurationConflictError(str(exc)) from exc
    except PolicyError as exc:
        raise ProjectPolicyOverridesError(str(exc)) from exc
    application = pack_application(config, policy_file=policy_file)
    return CompareConfiguration(
        config,
        policy_file_with_packs(policy_file, application, base_policy=base_policy),
        application,
    )


def project_policy_overrides(
    project_cfg: Any, project_path: Path | None
) -> dict[ChangeKind, Verdict]:
    """``.abicheck.yml``'s ``policy.overrides`` as ``ChangeKind -> Verdict``.

    Empty when the config states none (or there is no config). Raises
    :class:`ProjectPolicyOverridesError` for an unknown kind or severity.
    """
    from ..policy.policy_file_project_overrides import (
        resolve_project_config_policy_overrides,
    )

    try:
        return resolve_project_config_policy_overrides(project_cfg, project_path)
    except PolicyError as exc:
        raise ProjectPolicyOverridesError(str(exc)) from exc


@dataclass(frozen=True)
class ProjectPolicyFold:
    """The policy file after the project-config fold, and its diagnostics."""

    policy_file: PolicyFile | None
    #: ``HIGH RISK`` downgrade warnings for exactly the kinds the fold added.
    warnings: tuple[str, ...] = ()


def fold_project_policy_overrides(
    policy_file: PolicyFile | None,
    overrides: Mapping[ChangeKind, Verdict] | None,
    *,
    base_policy: str,
    project_path: Path | None = None,
) -> ProjectPolicyFold:
    """Fold *overrides* into *policy_file* at the ``PROJECT_CONFIG`` tier.

    Call after :func:`resolve_compare_configuration`: a kind the explicit
    policy file or a pack already states always wins. A no-op (the identical
    object back) when *overrides* is empty.
    """
    if not overrides:
        return ProjectPolicyFold(policy_file)
    from ..policy.policy_file_project_overrides import (
        apply_lower_precedence_overrides,
        project_config_policy_downgrade_warnings,
    )

    folded = apply_lower_precedence_overrides(
        policy_file, dict(overrides), base_policy=base_policy
    )
    return ProjectPolicyFold(
        folded,
        tuple(
            project_config_policy_downgrade_warnings(
                policy_file, folded, project_path=project_path
            )
        ),
    )
