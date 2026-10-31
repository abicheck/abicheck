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

The settings a ``compare`` run takes from its flags and its project file are
resolved here too, so no front end needs a CLI module to read them:

* :func:`resolve_compare_config` / :class:`ResolvedCompareConfig` -- ADR-037
  D4: CLI flag over ``.abicheck.yml`` over built-in default, per key. An
  Action-synthesized config overlay reaches this as an ordinary explicit
  ``--config`` file, so it needs no rung of its own.
  :func:`load_and_resolve_compare_config` reads the file first.
* :func:`resolve_compare_collect_mode` -- the source/build collect mode:
  ``--depth`` over ``sources.method`` over the evidence inputs given.
* :func:`load_required_symbols` / :func:`resolve_force_public_scope` -- the
  ``--required-symbol`` contract and the forced-public symbol set, each read
  once so a receipt names the content that scored the run.

They raise :class:`CompareSettingsError`, which the CLI reports as a usage
error (exit 64) with the same message. Project-config *discovery* stays in
``frontends.cli.project_config``: walking up from the working directory is
the CLI's convention, not a fact about the comparison.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..dry_run_estimate import pair_wide_cxx20_std_override
from ..errors import PackManifestError, PolicyError, ValidationError
from .extraction import has_explicit_std

if TYPE_CHECKING:
    from ..change_registry_types import Verdict
    from ..compatibility_evaluation_frontend import (
        ExplicitCompatibilityInputs,
        FrontEnd,
        ProjectCompatibilityInputs,
    )
    from ..dry_run_estimate import CompileContext
    from ..environment_matrix import EnvironmentMatrix
    from ..model.change_catalog.kinds import ChangeKind
    from ..pack_application import PackApplication
    from ..policy_file import PolicyFile
    from .compare_input_sources import PublicSymbolsList
    from .extraction import BuildConfig
    from .gate import SeverityConfig

__all__ = [
    "CompareConfiguration",
    "CompareConfigurationError",
    "CompareSettingsError",
    "ConfigurationConflictError",
    "ProjectPolicyFold",
    "ProjectPolicyOverridesError",
    "ResolvedCompareConfig",
    "fold_project_policy_overrides",
    "load_and_resolve_compare_config",
    "load_required_symbols",
    "project_policy_overrides",
    "resolve_compare_collect_mode",
    "resolve_compare_config",
    "resolve_compare_configuration",
    "resolve_force_public_scope",
    "resolve_release_pack_application",
]


class CompareConfigurationError(ValidationError):
    """The stated configuration of a compare cannot be resolved."""


class ConfigurationConflictError(CompareConfigurationError):
    """A D7 same-tier conflict, a D8 pack conflict, or an inapplicable pack."""


class ProjectPolicyOverridesError(CompareConfigurationError):
    """A malformed ``.abicheck.yml`` ``policy.overrides`` entry."""


class CompareSettingsError(CompareConfigurationError):
    """A compare setting from a flag or the project file is unusable.

    An unreadable or invalid ``.abicheck.yml``, an invalid ``sources.method``,
    or a ``--required-symbol @FILE`` that is missing or given twice. The CLI
    reports it as a usage error (exit 64) with this message.
    """


#: ``config_overrides`` keys a typed comparison applies (ADR-091 D4): each row
#: is (config path, ``CompareRequest`` field, ``ResolvedCompareConfig`` attribute).
#: A validated key outside this table is refused by the typed API rather than
#: dropped, so an override never silently changes nothing.

#: Further ``--set`` keys the CLI's ``compare`` applies from the resolved config
#: or the run's own settings, which a typed comparison has no field for.
CLI_ONLY_OVERRIDE_PATHS: frozenset[str] = frozenset(
    {
        "scope.show_redundant",
        "sources.method",
        "performance.profile",
        "output.view",
        "consumers.manifests",
        "consumers.required_symbols",
    }
)


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


def resolve_release_pack_application(
    explicit: ExplicitCompatibilityInputs,
    *,
    front_end: FrontEnd,
    project: ProjectCompatibilityInputs | None,
    policy_file: PolicyFile | None,
    base_policy: str,
    contract_evaluation: bool,
) -> PackApplication:
    """A release's resolved configuration, as the one ``PackApplication``
    every member and release-level stage reads.

    Always resolved (a release records it on every member under contract
    evaluation or a pack), through :func:`resolve_compare_configuration` --
    the scalar compare's own resolution. With no pack selected it is an
    *inert* application carrying only ``resolved_config``, so a project's
    ``policy.overrides`` and the stated gate still reach each member's
    receipt. Each front end supplies only its *explicit*/*project* inputs:
    the CLI's from the flags the user typed
    (``workflows.pack_resolution.resolve_release_pack_application``), a typed
    caller's from its ``ReleaseCompareOptions`` (``release_compare``).

    Raises :class:`ConfigurationConflictError` or
    :class:`ProjectPolicyOverridesError`.
    """
    from ..pack_application import PackApplication

    configuration = resolve_compare_configuration(
        explicit,
        front_end=front_end,
        project=project,
        policy_file=policy_file,
        base_policy=base_policy,
        contract_evaluation=contract_evaluation,
        always_resolve=True,
    )
    if configuration.pack_application is not None:
        return configuration.pack_application
    return PackApplication(
        policy_overrides={}, resolved_config=configuration.evaluation_config
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


# ── ADR-037 D4: flags over project config over built-in defaults ─────────────


@dataclass(frozen=True)
class ResolvedCompareConfig:
    """The settings ``compare`` runs with, after merging CLI flags over config.

    Precedence per key is **CLI > config > built-in default** (ADR-037 D4). A
    CLI value of ``None`` means "the user did not pass the flag", so the config
    value (or the default) wins; an explicit CLI value always wins.
    """

    severity: SeverityConfig
    #: True when severity was set anywhere (CLI flag or config) — drives the
    #: ``auto`` exit-code scheme.
    severity_active: bool
    scope_public: bool
    collapse_versioned_symbols: bool
    public_symbols: tuple[str, ...]
    strict_suppressions: bool
    require_justification: bool
    source_method: str | None
    #: ADR-040 Lever 2: debug-resolution knobs demoted to the ``debug:`` config
    #: block (CLI flags still override). ``debug_format`` is ``None`` when unset.
    debug_format: str | None = None
    dwarf_only: bool = False
    debuginfod: bool = False
    debuginfod_url: str | None = None
    #: Phase 7: ``--pdb-path`` demoted to ``debug.pdb_path``.
    pdb_path: str | None = None
    #: Phase 7 (one-comparison-product.md §4.1): ``--lang`` demoted to
    #: ``compile.lang`` with no surviving CLI override. ``None`` when
    #: unset -- the caller (``run_compare``) applies ``LANG_DEFAULT``.
    compile_lang: str | None = None
    #: ADR-040 Lever 2: ``--show-redundant`` demoted to ``scope.show_redundant``.
    show_redundant: bool = False
    #: The CLI-or-config severity values (``None`` when neither set), kept raw so
    #: the directory/package fan-out can forward them to ``compare-release``
    #: without forcing severity-aware mode when nothing is configured.
    merged_severity_preset: str | None = None
    merged_severity_abi_breaking: str | None = None
    merged_severity_potential_breaking: str | None = None
    merged_severity_quality_issues: str | None = None
    merged_severity_addition: str | None = None
    #: CLI cleanup phase two, PR J: ``bundle.system_providers``/
    #: ``bundle.cohorts`` demoted off the CLI entirely (no
    #: ``--bundle-system-providers``/``--bundle-cohort`` flags any more) —
    #: ``.abicheck.yml`` is these two fields' only source, same shape as
    #: ``show_redundant`` above.
    bundle_system_providers: tuple[str, ...] = ()
    bundle_cohorts: tuple[str, ...] = ()
    #: Phase 7d (one-comparison-product.md §4.1): the release/bundle
    #: topology knobs demoted off the CLI entirely -- ``.abicheck.yml`` is
    #: their only source now, same shape as ``bundle_system_providers``/
    #: ``bundle_cohorts`` above. ``on_incomplete_scope`` keeps its CLI
    #: default (``"warn"``) as the config default too, so an unconfigured
    #: project's behavior is unchanged.
    on_incomplete_scope: str = "warn"
    fail_on_removed_library: bool = False
    release_dso_only: bool = False
    release_include_private_dso: bool = False
    #: Phase 7: ``release.support_promise`` (``off``/``declared``).
    release_support_promise: str = "off"
    resource_limits_max_bundle_facts_decode_nodes: int | None = None
    require_complete_analysis: bool = False  # former --require-complete-analysis
    #: ADR-020b §4.1 / ADR-068 D5: the former ``compare --env-matrix FILE``,
    #: now the ``deployment:`` config key -- no CLI override. ``None`` when
    #: unset.
    deployment: EnvironmentMatrix | None = None

    @property
    def exit_code_scheme(self) -> str:
        """The one automatic gate algorithm (ADR-064/CLI cleanup phase two PR
        G2): ``"severity"`` when a severity setting is in effect anywhere
        (``severity_active``), else ``"legacy"``.

        A **derived property, not a field** (duplication-and-convergence-
        assessment T6). There is no user-facing selector for this any more
        (the removed ``--exit-code-scheme``/``.abicheck.yml``'s
        ``exit_code_scheme:`` key used to let a caller force either value
        regardless of ``severity_active``), so a settable field beside
        ``severity_active`` only let the model express a disagreement no
        resolution can produce -- and ``pack_application.
        apply_to_compare_config`` was correspondingly obliged to set both,
        in agreement, by hand. It stays a *named*, read-only member because a
        large amount of downstream reporting/dry-run code reads it as "which
        algorithm did this run use", which is still a fact worth stating even
        though it is no longer configurable.

        Derived through the one shared
        :func:`~abicheck.policy.gate_pack_fold.gate_exit_code_scheme` rule
        that ``policy.release_gate_options.GateOptions`` also states its
        scheme with, so single-pair ``compare`` and the release fan-out
        cannot drift. Reached through ``workflows.gate``, the gate surface
        this layer re-exports for its front ends.
        """
        from .gate import gate_exit_code_scheme

        return gate_exit_code_scheme(self.severity_active)


def resolve_compare_config(
    cfg: BuildConfig | None,
    *,
    cli_severity_preset: str | None,
) -> ResolvedCompareConfig:
    """Merge CLI flags over ``.abicheck.yml`` config with built-in defaults.

    Pure (no Click/IO) so the precedence contract is unit-testable per key
    (``test_config_precedence``). Each ``cli_*`` argument is ``None`` when the
    user did not pass the corresponding flag.

    Only the keys that still have a CLI flag take a ``cli_*`` argument. The
    per-category severity levels, the suppression strict/justification pair,
    the public-symbol overlay, ``collapse_versioned_symbols``,
    ``show_redundant``, and (Phase 7) the four ``debug:`` knobs were hidden
    CLI duplicates of a config key and have been removed from the CLI
    entirely (no surviving override, ADR-068 D5 guard #2), so
    ``.abicheck.yml`` is now their only source, read straight off *cfg*.
    ``scope.public`` joined them in one-comparison-product Phase 9b: the
    ``--scope-public-headers``/``--no-`` pair is gone (``--contract all``
    replaces the opt-out), so the key and its built-in ``True`` decide.
    """
    from .gate import resolve_severity_config

    def _pick(cli: object, conf: object, default: object) -> object:
        if cli is not None:
            return cli
        if conf is not None:
            return conf
        return default

    # Severity: preset from CLI → config; per-category from config only.
    c_preset = cfg.severity_preset if cfg else None
    c_abi = cfg.severity_abi_breaking if cfg else None
    c_pot = cfg.severity_potential_breaking if cfg else None
    c_qual = cfg.severity_quality_issues if cfg else None
    c_add = cfg.severity_addition if cfg else None

    eff_preset = cli_severity_preset if cli_severity_preset is not None else c_preset
    eff_abi, eff_pot, eff_qual, eff_add = c_abi, c_pot, c_qual, c_add

    severity_active = any(
        v is not None for v in (eff_preset, eff_abi, eff_pot, eff_qual, eff_add)
    )
    severity = resolve_severity_config(
        preset=eff_preset,
        abi_breaking=eff_abi,
        potential_breaking=eff_pot,
        quality_issues=eff_qual,
        addition=eff_add,
    )

    scope_public = bool(_pick(None, cfg.scope_public if cfg else None, True))
    collapse = bool(cfg.collapse_versioned_symbols) if cfg else False
    merged_public: list[str] = list(cfg.public_symbols) if cfg else []

    strict = bool(cfg.suppression_strict) if cfg else False
    require_just = bool(cfg.suppression_require_justification) if cfg else False

    source_method = cfg.source_method if cfg else None

    # Phase 7 (ADR-068 D5): debug-resolution knobs are config-only now -- no
    # CLI flag survives to override them (ADR-040 Lever 2 demoted them to a
    # config-key-with-override first; Phase 7 removed the override itself).
    debug_format = cfg.debug_format if cfg else None
    dwarf_only = bool(cfg.debug_dwarf_only) if cfg else False
    debuginfod = bool(cfg.debug_debuginfod) if cfg else False
    debuginfod_url = cfg.debug_debuginfod_url if cfg else None
    pdb_path_cfg = cfg.debug_pdb_path if cfg else None
    compile_lang = cfg.compile_lang if cfg else None
    show_redundant = bool(cfg.scope_show_redundant) if cfg else False
    bundle_system_providers = tuple(cfg.bundle_system_providers) if cfg else ()
    bundle_cohorts = tuple(cfg.bundle_cohorts) if cfg else ()
    # Phase 7d: no surviving CLI override for any of these four -- resolved
    # straight off cfg, same shape as the debug-resolution/bundle knobs above.
    on_incomplete_scope = (cfg.scope_on_incomplete if cfg else None) or "warn"
    fail_on_removed_library = bool(cfg.gate_fail_on_removed_library) if cfg else False
    release_dso_only = bool(cfg.release_dso_only) if cfg else False
    release_support_promise = (cfg.release_support_promise if cfg else None) or "off"
    release_include_private_dso = (
        bool(cfg.release_include_private_dso) if cfg else False
    )
    res_limit_nodes = cfg.resource_limits_max_bundle_facts_decode_nodes if cfg else None
    require_complete_analysis = bool(cfg.assurance_require_complete) if cfg else False
    deployment = cfg.deployment if cfg else None

    return ResolvedCompareConfig(
        severity=severity,
        severity_active=severity_active,
        scope_public=scope_public,
        collapse_versioned_symbols=collapse,
        public_symbols=tuple(merged_public),
        strict_suppressions=strict,
        require_justification=require_just,
        source_method=source_method,
        debug_format=debug_format if isinstance(debug_format, str) else None,
        dwarf_only=dwarf_only,
        debuginfod=debuginfod,
        debuginfod_url=debuginfod_url if isinstance(debuginfod_url, str) else None,
        pdb_path=pdb_path_cfg if isinstance(pdb_path_cfg, str) else None,
        compile_lang=compile_lang,
        show_redundant=show_redundant,
        merged_severity_preset=eff_preset,
        merged_severity_abi_breaking=eff_abi,
        merged_severity_potential_breaking=eff_pot,
        merged_severity_quality_issues=eff_qual,
        merged_severity_addition=eff_add,
        bundle_system_providers=bundle_system_providers,
        bundle_cohorts=bundle_cohorts,
        on_incomplete_scope=on_incomplete_scope,
        fail_on_removed_library=fail_on_removed_library,
        release_dso_only=release_dso_only,
        release_support_promise=release_support_promise,
        release_include_private_dso=release_include_private_dso,
        resource_limits_max_bundle_facts_decode_nodes=res_limit_nodes,
        require_complete_analysis=require_complete_analysis,
        deployment=deployment,
    )


def load_and_resolve_compare_config(
    cfg_path: Path | None,
    *,
    severity_preset: str | None,
    config_overrides: Mapping[str, object] | None = None,
) -> tuple[BuildConfig | None, ResolvedCompareConfig, str | None]:
    """Load the project config at *cfg_path* and resolve the run's settings.

    Returns ``(project_cfg, resolved, sha256)``. ``sha256`` is the digest of
    the bytes the config was parsed from, captured by the same read, so an
    ADR-049 receipt can prove *which revision* of the file supplied a value
    rather than only naming its path. Both are ``None`` when there is neither
    a file (*cfg_path*) nor a *config_overrides* mapping to build a config from.

    Raises :class:`CompareSettingsError` when the file cannot be parsed.
    """
    from .extraction import load_build_config_with_digest

    project_cfg: BuildConfig | None = None
    cfg_sha: str | None = None
    if cfg_path is not None or config_overrides:
        try:
            project_cfg, cfg_sha = load_build_config_with_digest(
                cfg_path, config_overrides=config_overrides
            )
        except ValueError as exc:
            raise CompareSettingsError(str(exc)) from exc
    resolved = resolve_compare_config(project_cfg, cli_severity_preset=severity_preset)
    return project_cfg, resolved, cfg_sha


def load_required_symbols(
    symbols: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], str | None, str | None]:
    """Combine ``--required-symbol`` values, expanding any ``@FILE`` entry
    (ADR-068 D5 / plan Phase 7h merged ``--required-symbols FILE`` into this
    form: one symbol per line, ``#`` comments ignored, ADR-043; at most one
    ``@FILE`` per invocation, matching the singular flag it replaces).

    Returns ``(combined, from_file, digest, path)`` -- the last three
    ``None``/empty when no ``@FILE`` was given, so a receipt can tell "no
    file" from "a file that parsed to nothing" (Codex review).
    """
    import hashlib

    literal = [v for v in symbols if not v.startswith("@")]
    files = [v[1:] for v in symbols if v.startswith("@")]
    if len(files) > 1:
        raise CompareSettingsError(
            f"consumers.required_symbols accepts at most one '@FILE' value; got {files}. "
            "Combine both files, or pass the extras as plain values."
        )
    from_file: list[str] = []
    digest = path = None
    if files:
        candidate = Path(files[0])
        if candidate.is_dir():
            raise CompareSettingsError(
                f"consumers.required_symbols @{candidate}: is a directory, not a file."
            )
        if not candidate.is_file():
            raise CompareSettingsError(
                f"consumers.required_symbols @{candidate}: no such file."
            )
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            # An existing but unreadable file (permissions, EIO, ...) is an input
            # error like a missing one, not an operational failure: it must reach
            # the CLI as the typed refusal (exit 64), never as a traceback.
            raise CompareSettingsError(
                f"consumers.required_symbols @{candidate}: cannot read file "
                f"({exc.strerror or exc})."
            ) from None
        digest, path = hashlib.sha256(data).hexdigest(), str(candidate)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CompareSettingsError(
                f"consumers.required_symbols @{candidate}: not UTF-8 text ({exc.reason} "
                f"at byte {exc.start})."
            ) from None
        from_file = [
            s
            for line in text.splitlines()
            if (s := line.strip()) and not s.startswith("#")
        ]
    # De-duplicate while preserving first-seen order.
    return tuple(dict.fromkeys([*literal, *from_file])), tuple(from_file), digest, path


def resolve_force_public_scope(
    public_symbols: tuple[str, ...], symbols_list_path: str | Path | None
) -> tuple[set[str], PublicSymbolsList | None]:
    """The forced-public set, and the list file it was read from -- one read.

    ``--public-symbols-list`` feeds two consumers: the live comparison's
    forced-public overlay, and the ADR-049 receipt that names the file with
    its digest. Reading it once for both is what keeps the receipt honest --
    a second read could pair the persisted digest with content that did not
    score the run, and a file deleted mid-run would fail an otherwise
    finished comparison during receipt generation (Codex review, fresh
    evidence). Returns ``None`` for the list when no file was given, which
    is a different fact from a file that named no symbols.
    """
    from .compare_input_sources import (
        PublicSymbolsList as _List,
        collect_force_public_symbols,
    )

    listed = (
        _List.from_file(symbols_list_path) if symbols_list_path is not None else None
    )
    forced = collect_force_public_symbols(public_symbols, None, already_read=listed)
    return forced, listed


def resolve_compare_collect_mode(
    depth: str | None,
    source_method: str | None,
    old_sources: Path | None,
    new_sources: Path | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
) -> tuple[str, str]:
    """Resolve compare's source/build collect mode, plus a human label for it.

    Precedence (ADR-037 D4/D5, extended by the P1 CLI-contract fix below):
    explicit ``--depth`` > ``.abicheck.yml`` ``sources.method`` > inferred from
    raw ``--old/new-sources``/``--old/new-build-info`` given with neither of
    the above > off.

    The inferred rung closes a gap where passing ``--sources``/``--build-info``
    with no ``--depth`` (and no ``sources.method`` in config) silently resolved
    to "off" and the inputs were ignored with a warning: an explicit
    source/build-info input is itself a request to use it, so omitted depth
    should not default to discarding it. This mirrors ``scan``'s own
    "auto" depth, which is likewise input-driven rather than a fixed default.
    The label is shown verbatim in ``compare --dry-run``'s "Resolved depth and
    source scope" section so a dry run reports the *effective* depth, not just
    the raw ``--depth`` string the user passed (or omitted).
    """
    from ..model.evidence_depth_levels import collect_mode_for_depth

    if depth is not None:
        return collect_mode_for_depth(depth), f"--depth {depth}"
    if source_method:
        from ..model.evidence_depth_levels import SourceMethod, method_to_collect_mode

        try:
            mode = method_to_collect_mode(SourceMethod(source_method))
        except ValueError:
            raise CompareSettingsError(
                f"sources.method in .abicheck.yml is invalid: "
                f"{source_method!r} (expected s0..s6 or auto)."
            ) from None
        return mode, f"sources.method={source_method} (.abicheck.yml)"
    if old_sources is not None or new_sources is not None:
        return (
            collect_mode_for_depth("source"),
            "source (inferred: --sources old=/new= given, no --depth)",
        )
    if old_build_info is not None or new_build_info is not None:
        return (
            collect_mode_for_depth("build"),
            "build (inferred: --build-info old=/new= given, no --depth)",
        )
    return "off", "off -- no L3-L5 source/build evidence (no --depth, no --sources/--build-info, no source.method)"  # fmt: skip


def _pair_wide_dialect_override(
    lang: str,
    old_h: list[Path],
    new_h: list[Path],
    compile_context: CompileContext,
    side_compile_context: CompileContext,
) -> tuple[CompileContext, CompileContext]:
    """Pin ``-std=gnu++20`` for BOTH compare sides at once, or neither (P0 fix).

    Thin wrapper around the shared core
    (:func:`~abicheck.dry_run_estimate.pair_wide_cxx20_std_override`, also used by
    ``service.run_compare_request``'s Python-API/MCP path, so the policy can't
    drift between the two front-ends) that applies the decision to this CLI
    path's two ``CompileContext`` objects: ``compile_context`` (used by the
    inline-source-embed path) and ``side_compile_context`` (used by
    ``_resolve_compare_snapshots``) — the sole call site derives the latter
    from the former via ``dataclasses.replace(compile_context, frontend="auto")``,
    so today they always carry identical ``gcc_options``/``gcc_option_tokens``;
    the explicit-std guard below checks both anyway rather than relying on
    that invariant holding for every future caller.

    An explicit ``-std=``/``--std=``/``/std:`` from the user always wins and
    this is then a no-op — auto-detection never overrides an explicit choice.
    """
    if has_explicit_std(
        compile_context.gcc_options, compile_context.gcc_option_tokens
    ) or has_explicit_std(
        side_compile_context.gcc_options, side_compile_context.gcc_option_tokens
    ):
        return compile_context, side_compile_context
    override = pair_wide_cxx20_std_override(
        lang,
        old_h,
        new_h,
        compile_context.gcc_options,
        compile_context.gcc_option_tokens,
    )
    if override is None:
        return compile_context, side_compile_context
    compile_context = dataclasses.replace(
        compile_context,
        gcc_option_tokens=(*compile_context.gcc_option_tokens, *override),
    )
    side_compile_context = dataclasses.replace(
        side_compile_context,
        gcc_option_tokens=(*side_compile_context.gcc_option_tokens, *override),
    )
    return compile_context, side_compile_context
