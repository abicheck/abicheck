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

"""Front-end adapters for compatibility-evaluation configuration.

Each adapter normalizes one front end's stated inputs into
:class:`~abicheck.compatibility_evaluation_frontend.ExplicitCompatibilityInputs`:
:func:`compare_cli_inputs` for the ``compare`` command's kwargs, and
:func:`compare_request_inputs` for a typed
:class:`~abicheck.workflows.service_requests.CompareRequest`.

This lives under ``workflows/`` rather than ``frontends/`` because workflow
callers (``compare_configuration``, ``compare_gate_receipt``,
``release_compare``) use the adapters, and ``workflows`` may not import
``frontends`` under ADR-061's dependency direction.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..compatibility_evaluation_frontend import (
    ExplicitCompatibilityInputs,
    FrontEnd,
    ProjectCompatibilityInputs,
    resolve_compatibility_evaluation_config,
)
from .compare_input_sources import PublicSymbolsList, SuppressionSource
from .evaluation_identities import stated_policy_base
from .ownership_contract_inputs import header_dir_spellings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..compatibility_evaluation_config import CompatibilityEvaluationConfig
    from ..policy_file import PolicyFile
    from .service_requests import CompareRequest


def _load_policy_file(path: str | Path) -> PolicyFile:
    """Load a ``--policy-file`` document from a path an invocation named."""
    from ..policy_file import PolicyFile as _PolicyFile

    return _PolicyFile.load(Path(path))


def compare_cli_inputs(
    kwargs: Mapping[str, Any],
    *,
    explicit_parameters: Collection[str] = (),
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
    policy_base_option: str | None = None,
    policy_base_path: str | None = None,
    policy_base_sha256: str | None = None,
    public_symbols_list: PublicSymbolsList | None = None,
) -> ExplicitCompatibilityInputs:
    """Normalize the ``compare`` command's real kwargs into resolver inputs.

    *kwargs* is the command's own parameter mapping (``cli.py``'s option
    destinations, verbatim). *explicit_parameters* names the parameters the
    user actually typed; it only matters for
    :data:`DEFAULTED_COMPARE_PARAMETERS`, whose click defaults are
    indistinguishable from a stated value. Every other option already uses
    ``None``/``()`` for "not given", so it is read directly.

    *policy_file* and *suppression* let a caller pass what it already loaded
    (the CLI loads both long before configuration would be resolved). Left
    unset, they are loaded from the ``policy_file_path``/``suppress`` kwargs
    the command itself carries -- silently ignoring a path the invocation
    really named would make the resolved configuration misrepresent the run.

    *policy_base_option* names the option that selected ``policy`` when it was
    not ``--policy``: ``compare`` switches an untouched ``--policy`` to
    ``plugin_abi`` for a ``--required-symbol``
    contract, and that value is read as stated regardless of
    *explicit_parameters* (it was not typed, but it was chosen -- see
    :attr:`ExplicitCompatibilityInputs.policy_base_option`).
    *policy_base_path*/*policy_base_sha256* identify the list file when that
    option is the file form.

    *public_symbols_list* is the same "pass what you already read" affordance
    (re-reading could pair the receipt's digest with content that did not
    score the run). It stays its own :class:`PublicSymbolsList`, so the
    receipt names the file; :func:`_explicit_scope` forms the union the live
    CLI's :func:`collect_force_public_symbols` produces.
    """
    typed = set(explicit_parameters)

    def _defaulted(name: str) -> Any:
        return kwargs.get(name) if name in typed else None

    symbols_list_path = kwargs.get("public_symbols_list")
    if public_symbols_list is None and symbols_list_path is not None:
        public_symbols_list = PublicSymbolsList.from_file(symbols_list_path)
    symbols_list = public_symbols_list
    if policy_file is None and kwargs.get("policy_file_path") is not None:
        policy_file = _load_policy_file(kwargs["policy_file_path"])
    if suppression is None and kwargs.get("suppress") is not None:
        suppression = SuppressionSource.from_file(
            kwargs["suppress"],
            require_justification=bool(kwargs.get("require_justification")),
        )
    return ExplicitCompatibilityInputs(
        contract_mode=kwargs.get("contract_mode"),
        # No CLI spelling since one-comparison-product Phase 9b: the
        # project tier (`scope.public`) is the CLI's only legacy-alias input.
        policy_base=(
            kwargs.get("policy") if policy_base_option else _defaulted("policy")
        ),
        policy_base_option=policy_base_option,
        policy_base_path=policy_base_path,
        policy_base_sha256=policy_base_sha256,
        policy_file=policy_file,
        public_symbols=tuple(kwargs.get("public_symbols") or ()),
        public_symbols_list=symbols_list,
        suppression=suppression,
        severity_preset=kwargs.get("severity_preset"),
        severity_abi_breaking=kwargs.get("severity_abi_breaking"),
        severity_potential_breaking=kwargs.get("severity_potential_breaking"),
        severity_quality_issues=kwargs.get("severity_quality_issues"),
        severity_addition=kwargs.get("severity_addition"),
        pack_paths=tuple(str(p) for p in kwargs.get("pack_paths") or ()),
        header_dirs=header_dir_spellings(kwargs.get("headers") or ()),
    )


def compare_request_inputs(
    request: CompareRequest,
    *,
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
) -> ExplicitCompatibilityInputs:
    """Normalize a typed :class:`~abicheck.workflows.service_requests.CompareRequest`.

    Every field is read as *stated* unless ``request.stated_settings`` names
    the stated ones (then an unnamed ``policy`` is the built-in default, as
    for the CLI); unlike the CLI, the typed request has no
    "unset" representation for ``policy`` (defaults to ``"strict_abi"``) or
    ``scope_public`` (defaults to ``True``) -- a caller constructing the
    request chose those values, whether deliberately or by accepting the
    dataclass default. ``checker.compare`` treats the same two the same way
    and for the same reason (see ``_apply_contract_evaluation_shadow``'s
    ``scope_public_headers_is_explicit=True``).

    ``severity_preset`` forwards too, else this receipt's ``gate.*`` block
    could disagree with ``CompareResult.exit_decision``; with no
    per-category or pack field it resolves like a CLI run stating none.
    ``policy_file_path``/``suppress`` are loaded from the request unless the
    caller passes them already loaded. ``policy_base`` goes through
    :func:`stated_policy_base` (an unknown name beside a valid file is
    accepted; the file wins). ADR-075 D7: the new side's ``-H`` directories
    and ``InputSpec.ownership`` are the request's stated ownership inputs.
    """
    if policy_file is None and request.policy_file_path is not None:
        policy_file = _load_policy_file(request.policy_file_path)
    if suppression is None and request.suppress is not None:
        suppression = SuppressionSource.from_file(request.suppress)
    return ExplicitCompatibilityInputs(
        contract_mode=request.contract_mode,
        # `scope_public=True` is the field's own default and, since ADR-049
        # Phase 7, states no domain: the evidence-adaptive built-in default
        # decides. Only `False` (the legacy alias for `all`) is a statement.
        scope_public_headers=None if request.scope_public else False,
        policy_base=(
            stated_policy_base(request.policy, policy_file)
            if request.stated_settings is None or "policy" in request.stated_settings
            else None
        ),
        policy_file=policy_file,
        public_symbols=tuple(sorted(request.force_public_symbols or ())),
        suppression=suppression,
        severity_preset=request.severity_preset,
        pack_paths=tuple(str(p) for p in request.pack_paths),
        header_dirs=header_dir_spellings(
            (*request.new.headers, *request.new.public_header_dirs)
        ),
        ownership=getattr(request.new.ownership, "rules", None),
    )


def compatibility_config_from_compare_request(
    request: CompareRequest,
    *,
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
    project: ProjectCompatibilityInputs | None = None,
) -> CompatibilityEvaluationConfig:
    """One-call adapter: typed request -> resolved effective configuration."""
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.API,
        explicit=compare_request_inputs(
            request, policy_file=policy_file, suppression=suppression
        ),
        project=project,
    )


#: The ``compare`` kwargs that feed the compatibility configuration. Named
#: here so the caller forwards exactly the mapping
#: ``compare_cli_inputs`` reads, and an option renamed on one side fails
#: loudly on the other rather than resolving to "not stated".
COMPARE_CONFIG_PARAMS: tuple[str, ...] = (
    "contract_mode",
    "policy",
    "policy_file_path",
    "suppress",
    "require_justification",
    "severity_preset",
    "pack_paths",
    # ADR-075 D7: the -H roots the snapshots were classified under.
    "headers",
)


def typed_parameter_names() -> tuple[str, ...]:
    """The parameters the caller must answer "was this typed?" for.

    Read off the resolver's own
    :data:`~abicheck.compatibility_evaluation_frontend.DEFAULTED_COMPARE_PARAMETERS`
    rather than restated here: those are exactly the options whose click
    default (``--policy strict_abi``) is
    indistinguishable from a value the user chose, and a second copy of that
    list is a second thing to keep in sync. Every other option already
    spells "not given" as ``None``/``()``.
    """
    from ..compatibility_evaluation_frontend import DEFAULTED_COMPARE_PARAMETERS

    return tuple(sorted(DEFAULTED_COMPARE_PARAMETERS))


def _suppression_source(suppression: Any, path: Any) -> Any:
    """The already-loaded ``--suppress`` list, as a resolver input.

    Built from the list the run really used rather than re-reading *path*
    (a second read could pair one content's digest with another's rules,
    the trap :meth:`SuppressionSource.from_file` documents). A list with no
    ``source_sha256`` still selected a source, so it is reported as one --
    the same absent-vs-empty rule ``contract_context.
    suppression_config_for`` follows."""
    if suppression is None:
        return None
    from ..policy.suppression.merge import suppress_label

    return SuppressionSource(
        path=suppress_label(path) if path is not None else None,
        sha256=getattr(suppression, "source_sha256", None) or "",
        rules=tuple(suppression.rule_identities()),
    )


def cli_configuration_inputs(
    params: Mapping[str, Any],
    *,
    typed: Collection[str],
    project_cfg: Any,
    project_path: Path | None,
    policy_file: Any = None,
    suppression: Any = None,
    suppress_path: Path | tuple[Path, ...] | None = None,
    policy_option: str | None = None,
    policy_path: Path | None = None,
    policy_sha256: str | None = None,
    project_sha256: str | None = None,
    symbols_list: Any = None,
    project_overlays_applied: bool = False,
) -> tuple[Any, Any]:
    """This invocation's ``(explicit, project)`` resolver inputs.

    *typed* names the parameters the user really typed (the CLI answers that
    from Click's ``ParameterSource`` at its own call site and hands it over as
    data -- the same stated-settings set a typed caller puts in
    ``CompareRequest.stated_settings``).
    *policy_option* names the flag that selected ``policy`` when it was not
    ``--policy`` -- ``--required-symbol``, whose contract switches an
    untouched ``--policy`` to ``plugin_abi``, with *policy_path*/
    *policy_sha256* identifying the ``@FILE`` form's list file when used.
    *project_sha256* is the digest of the ``.abicheck.yml`` bytes
    *project_cfg* was parsed from, naming a revision, not only a path.
    *project_overlays_applied*: this route applies the config's
    ``contract.overlays`` (a single-pair compare under an explicit
    ``--config``), so the receipt states them at ``project_config`` tier.
    """
    # The declared contract, enforced rather than only documented: every one
    # of these keys is read below, and a caller that renamed or dropped one
    # would have it resolve silently to "not stated" -- indistinguishable
    # from a user who did not pass the flag (CodeRabbit review).
    missing = [name for name in COMPARE_CONFIG_PARAMS if name not in params]
    if missing:
        raise KeyError(
            f"compare config params missing {missing}; every name in "
            "COMPARE_CONFIG_PARAMS must be forwarded, since an absent one "
            "resolves as unstated rather than failing"
        )
    explicit = compare_cli_inputs(
        params,
        explicit_parameters=typed,
        policy_file=policy_file,
        suppression=_suppression_source(suppression, suppress_path),
        policy_base_option=policy_option,
        policy_base_path=str(policy_path) if policy_path is not None else None,
        policy_base_sha256=policy_sha256,
        public_symbols_list=symbols_list,
    )
    project = ProjectCompatibilityInputs.from_build_config(
        project_cfg,
        path=project_path,
        sha256=project_sha256,
        overlays_applied=project_overlays_applied,
    )
    return explicit, project
