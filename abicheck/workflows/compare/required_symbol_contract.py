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

"""``consumers.required_symbols`` on the typed compare route (ADR-043).

The CLI reads the key from ``.abicheck.yml`` or ``compare --set``; a typed
caller states it the same way, as ``CompareRequest.config_overrides
["consumers.required_symbols"]``. Both routes go through the same owners:

* :func:`~abicheck.workflows.compare_configuration.load_required_symbols`
  expands the entries (at most one ``@FILE``) and refuses an unreadable,
  missing, directory or non-UTF-8 ``@FILE`` as a typed
  :class:`~abicheck.workflows.compare_configuration.CompareSettingsError`;
* :func:`~abicheck.workflows.compare.configuration.resolve_required_symbol_policy`
  makes ``plugin_abi`` the policy unless the caller chose one;
* :func:`~abicheck.workflows.consumer_scope_gate.apply_scoped_gating` records
  the contract summary on the result, beside (never instead of) the
  full-library verdict.

There is no ``CompareRequest`` field of its own: the override is the typed
spelling of the config key, as for every other ``consumers``/``scope`` key
that reaches a comparison.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ...policy_file import PolicyFile
    from ..service_requests import CompareRequest, CompareResult

__all__ = [
    "REQUIRED_SYMBOLS_OVERRIDE_PATH",
    "RequiredSymbolContract",
    "apply_required_symbol_contract",
    "required_symbol_contract",
    "with_required_symbol_policy",
]

#: The config key a typed caller sets in ``config_overrides``.
REQUIRED_SYMBOLS_OVERRIDE_PATH = "consumers.required_symbols"


@dataclass(frozen=True)
class RequiredSymbolContract:
    """One run's resolved required-symbol contract."""

    #: Every required symbol, literal entries and ``@FILE`` lines combined.
    symbols: tuple[str, ...]
    #: The symbols the ``@FILE`` entry contributed (empty when none).
    from_file: tuple[str, ...]
    #: The ``@FILE``'s path and SHA-256, ``None`` when no file was given.
    path: str | None
    sha256: str | None


def required_symbol_contract(
    config_overrides: Mapping[str, object],
    entries: tuple[str, ...],
) -> RequiredSymbolContract | None:
    """The contract *config_overrides* states, or ``None`` when it states none.

    *entries* is the key's schema-validated value (the resolved project
    config's ``required_symbols``). Read errors propagate as
    ``CompareSettingsError`` from the one loader the CLI calls.
    """
    if REQUIRED_SYMBOLS_OVERRIDE_PATH not in config_overrides or not entries:
        return None
    from ..compare_configuration import load_required_symbols

    symbols, from_file, sha256, path = load_required_symbols(tuple(entries))
    if not symbols:
        return None
    return RequiredSymbolContract(
        symbols=tuple(symbols),
        from_file=tuple(from_file),
        path=path,
        sha256=sha256,
    )


def with_required_symbol_policy(
    request: CompareRequest, contract: RequiredSymbolContract | None
) -> CompareRequest:
    """*request* under the policy the contract selects.

    The CLI keeps a ``--policy`` the user typed; the typed route keeps a
    ``policy`` the caller set away from its default or named in
    ``stated_settings`` -- the rule ``config_overrides`` already follows for
    every field it fills.
    """
    if contract is None:
        return request
    from ..service_requests import CompareRequest
    from .configuration import resolve_required_symbol_policy

    default = next(
        f.default for f in dataclasses.fields(CompareRequest) if f.name == "policy"
    )
    stated = request.stated_settings
    policy_chosen = request.policy != default or (
        stated is not None and "policy" in stated
    )
    policy, *_ = resolve_required_symbol_policy(
        str(request.policy),
        required_symbols=contract.symbols,
        policy_from_commandline=policy_chosen,
        required_symbols_from_file=contract.from_file,
        required_symbols_path=contract.path,
        required_symbols_sha=contract.sha256,
    )
    if policy == request.policy:
        return request
    return dataclasses.replace(request, policy=policy)


def apply_required_symbol_contract(
    result: CompareResult,
    request: CompareRequest,
    contract: RequiredSymbolContract | None,
) -> None:
    """Record the contract's scoped summary on *result*'s ``DiffResult``.

    The same call the CLI makes after classification
    (``frontends.cli.compare_result``), with the policy, policy file,
    severity and suppression list this run was scored under.
    """
    if contract is None:
        return
    from pathlib import Path

    from ..consumer_scope_gate import apply_scoped_gating

    # The two operand paths are read only by a `--used-by` scope, never by a
    # required-symbol one; a typed request built from snapshots has none.
    apply_scoped_gating(
        result.diff,
        result.old_snapshot,
        result.new_snapshot,
        request.policy_profile,
        # The PolicyFile this run loaded and classified under.
        cast("PolicyFile | None", result.diff.policy_file),
        used_by_apps=(),
        required_symbols=contract.symbols,
        used_by_old_input=Path(request.old.path or "<old>"),
        used_by_new_input=Path(request.new.path or "<new>"),
        severity=result.severity_config,
        suppression=result.suppression,
    )
    # The CLI's next step (`frontends.cli.compare_result`): the contract
    # evaluator always runs (ADR-049 Phase 7), so the findings the scope made
    # relevant carry its explicit-scope IN_CONTRACT decision on both routes.
    from ...compare.finding_identity import report_finding_id
    from ...contract_scoped_promotion import stamp_scoped_result_findings

    stamp_scoped_result_findings(result.diff, finding_id=report_finding_id)
