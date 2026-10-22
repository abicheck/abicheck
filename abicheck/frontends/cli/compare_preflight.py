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

"""Click-side preflight and evaluation-config adapters for ``compare``.

Both functions turn the Click-parsed option values into the typed inputs the
workflow layer expects, and render the workflow's refusals as the usage errors
``compare`` prints. The resolution itself lives in ``workflows.compare``.
Split out of :mod:`abicheck.cli_compare_helpers` (ADR-061 frontends routing);
no behavior change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ...cli_compare_options import _param_from_cli

if TYPE_CHECKING:
    from ...workflows.compare_configuration import ResolvedCompareConfig  # noqa: F401
    from ...workflows.extraction import DumpManifest
    from ...workflows.policy_file import PolicyFile


def _preflight_manifests_and_audit(
    *,
    old_dump_manifest: Path | None,
    new_dump_manifest: Path | None,
    pack_paths: Any,
    policy_file_path: Path | None,
    contract_evaluation: bool,
) -> tuple[DumpManifest | None, DumpManifest | None]:
    """Load the dump manifests and reject what a dry run must not report "ok".

    The loading and validation are in ``workflows.compare.manifests``; this
    renders their typed refusals as the usage errors ``compare`` prints.
    """
    from ...errors import ManifestValidationError, PackManifestError
    from ...workflows.compare.manifests import (
        load_compare_dump_manifests,
        validate_compare_pack_manifests,
    )

    try:
        manifests = load_compare_dump_manifests(old_dump_manifest, new_dump_manifest)
    except ManifestValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    try:
        validate_compare_pack_manifests(
            pack_paths,
            policy_file_path=policy_file_path,
            contract_evaluation=contract_evaluation,
        )
    except PackManifestError as exc:
        raise click.UsageError(str(exc)) from exc
    return manifests


def _resolve_evaluation_config(
    ctx: click.Context,
    *,
    headers: tuple[Path, ...],
    resolved_cfg: Any,
    project_cfg: Any,
    cfg_path: Path | None,
    cfg_sha: str | None,
    config_explicit: bool,
    policy: str,
    policy_file_path: Path | None,
    policy_file: PolicyFile | None,
    suppression: Any,
    suppress: Path | None,
    symbols_list: Any,
    contract_mode: str | None,
    contract_evaluation: bool,
    require_justification: bool,
    severity_preset: str | None,
    pack_paths: tuple[Path, ...],
    policy_selected_by: str | None,
    policy_selected_path: Path | None,
    policy_selected_sha: str | None,
) -> tuple[Any, PolicyFile | None, Any]:
    """Adapt the CLI inputs to ``workflows.compare.configuration`` and render refusals.

    The raw flag values (not merged locals, which would look CLI-stated) and
    the Click-typed set are the CLI's; the fold itself is in the workflow.
    """
    from ...workflows import compare_input_adapters as _adapters
    from ...workflows.compare.configuration import resolve_evaluation_configuration
    from ...workflows.compare_configuration import (
        ConfigurationConflictError,
        ProjectPolicyOverridesError,
    )

    try:
        return resolve_evaluation_configuration(
            {
                "contract_mode": contract_mode,
                "policy": policy,
                "policy_file_path": policy_file_path,
                "suppress": suppress,
                "require_justification": require_justification,
                "severity_preset": severity_preset,
                "pack_paths": pack_paths,
                # The roots each side was classified under (ADR-075 D7).
                "headers": headers,
            },
            # The stated settings: only this module holds the Click context.
            typed={n for n in _adapters.typed_parameter_names() if _param_from_cli(n)},
            resolved_cfg=resolved_cfg,
            project_cfg=project_cfg,
            cfg_path=cfg_path,
            cfg_sha=cfg_sha,
            config_explicit=config_explicit,
            policy=policy,
            policy_file=policy_file,
            suppression=suppression,
            suppress=suppress,
            symbols_list=symbols_list,
            contract_evaluation=contract_evaluation,
            policy_selected_by=policy_selected_by,
            policy_selected_path=policy_selected_path,
            policy_selected_sha=policy_selected_sha,
        )
    except ConfigurationConflictError as exc:
        raise click.UsageError(str(exc)) from exc
    except ProjectPolicyOverridesError as exc:
        raise click.BadParameter(str(exc), param_hint="--policy") from exc
