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

"""Config-fold decisions for the typed compare pipeline.

Pure functions over already-parsed values. The CLI reads what the user typed
(``click`` parameter sources) and passes the answer in as a plain ``bool``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from ..policy_file import PolicyFile


def resolve_required_symbol_policy(
    policy: str,
    *,
    required_symbols: tuple[str, ...],
    policy_from_commandline: bool,
    required_symbols_from_file: tuple[str, ...],
    required_symbols_path: str | None,
    required_symbols_sha: str | None,
) -> tuple[str, str | None, Path | None, str | None]:
    """Pick ``policy`` for a ``--required-symbol`` contract, and say what picked it.

    Required-symbol contracts default to the plugin-oriented policy unless the
    user explicitly picked one -- an explicit ``--policy`` always wins (ADR-043).
    Recorded, not just applied: the ADR-049 receipt has to name whatever really
    selected ``policy.base``, and this value was chosen by a typed
    ``--required-symbol`` rather than by ``--policy`` or a built-in default
    (Codex review, fresh evidence -- the receipt claimed ``strict_abi`` for a run
    that used ``plugin_abi``).

    Which spelling actually *contributed* decides what the receipt names -- not
    merely which was passed. ADR-068 D5 / plan Phase 7h folded the old, separate
    ``--required-symbols FILE`` flag into ``--required-symbol @FILE``, so both
    forms now share one option name for the receipt -- but a file that parsed
    to nothing still selected nothing (Codex review, two rounds, generalized):
    the ``@FILE`` form wins the receipt's path/digest only when it actually
    contributed a symbol.

    *policy_from_commandline* is whether ``--policy`` was typed on the command
    line (not defaulted); the caller reads it from its own argument parser.

    Returns ``(policy, selected_by, selected_path, selected_sha)``.
    """
    if not required_symbols or policy_from_commandline:
        return policy, None, None, None
    if required_symbols_from_file:
        return (
            "plugin_abi",
            "--required-symbol",
            Path(required_symbols_path) if required_symbols_path else None,
            required_symbols_sha,
        )
    return "plugin_abi", "--required-symbol", None, None


def fold_compare_collect_inputs(
    *,
    depth: str | None,
    source_method: str | None,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_sources: Path | None,
    new_sources: Path | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
) -> tuple[str, tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]]:
    """Fold ``--depth`` and the header roots into the dispatch-ready collect inputs.

    Returns ``(collect_mode, headers, old_headers_only, new_headers_only)``.
    ``--depth binary`` means no headers are parsed, so the root tuples are
    cleared. A :class:`~abicheck.workflows.compare_configuration.CompareSettingsError`
    propagates; the CLI renders it as a usage error.
    """
    from ..compare_configuration import resolve_compare_collect_mode

    collect_mode, _ = resolve_compare_collect_mode(
        depth,
        source_method,
        old_sources,
        new_sources,
        old_build_info,
        new_build_info,
    )
    if depth == "binary":
        headers, old_headers_only, new_headers_only = (), (), ()
    return collect_mode, headers, old_headers_only, new_headers_only


def split_compile_context_frontend(compile_context: Any) -> tuple[str, Any]:
    """Split the shared compile context into ``(header_backend, side_context)``.

    The merged frontend flows to both sides through the explicit header
    backend, so a per-side ``--ast-frontend old=``/``new=`` can still override
    it. The side context is neutralized to ``frontend="auto"`` so the config's
    ``compile.frontend`` does not outrank that per-side backend.
    """
    return compile_context.frontend, replace(compile_context, frontend="auto")


def resolve_evaluation_configuration(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    resolved_cfg: Any,
    project_cfg: Any,
    cfg_path: Path | None,
    cfg_sha: str | None,
    config_explicit: bool,
    policy: str,
    policy_file: PolicyFile | None,
    suppression: Any,
    suppress: Path | tuple[Path, ...] | None,
    symbols_list: Any,
    contract_evaluation: bool,
    policy_selected_by: str | None,
    policy_selected_path: Path | None,
    policy_selected_sha: str | None,
) -> tuple[Any, PolicyFile | None, Any]:
    """``(evaluation_config, policy_file, resolved_cfg)`` for one compare run.

    The CLI-specific part is the caller's job: *params* holds the raw values
    it forwards, and *typed* names the parameters the user really typed
    (Click's ``ParameterSource``, read at the CLI call site). Everything after
    that is the typed fold: the explicit/project inputs, the configuration
    resolution, and a gate pack's levels folded into *resolved_cfg*.

    Typed errors (:class:`ConfigurationConflictError`,
    :class:`ProjectPolicyOverridesError`) propagate; the CLI renders them as
    usage errors with the message unchanged.
    """
    from ...compatibility_evaluation_frontend import FrontEnd
    from ...pack_application import apply_to_compare_config
    from .. import compare_input_adapters as _adapters
    from ..compare_configuration import resolve_compare_configuration

    explicit, project = _adapters.cli_configuration_inputs(
        params,
        typed=typed,
        project_cfg=project_cfg,
        project_path=cfg_path,
        # The overlay applies only from an explicitly named --config.
        project_overlays_applied=config_explicit,
        policy_file=policy_file,  # already loaded: one read per digest
        suppression=suppression,
        suppress_path=suppress,
        policy_option=policy_selected_by,
        policy_path=policy_selected_path,
        policy_sha256=policy_selected_sha,
        project_sha256=cfg_sha,
        symbols_list=symbols_list,
    )
    configuration = resolve_compare_configuration(
        explicit,
        front_end=FrontEnd.CLI,
        project=project,
        policy_file=policy_file,
        base_policy=policy,
        contract_evaluation=contract_evaluation,
    )
    if (application := configuration.pack_application) is not None:
        resolved_cfg = apply_to_compare_config(resolved_cfg, application)
    return configuration.evaluation_config, configuration.policy_file, resolved_cfg
