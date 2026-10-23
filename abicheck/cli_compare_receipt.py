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

"""ADR-049 Phase 5: the ``compare`` CLI's own resolved configuration.

``checker.compare`` builds a persisted contract context from the arguments it
was handed, so it can honestly claim no more than ``API_REQUEST`` for any of
them (see :mod:`abicheck.contract_context`). This module is where the
``compare`` command hands over what it -- and only it -- knows: which flags
the user really typed, which ``.abicheck.yml`` supplied a value, and the
exit-code scheme and severity levels the run was actually scored with.

Those inputs go through Phase 1's canonical resolver
(:func:`~abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`),
and the resulting object *replaces* the core verb's narrower reconstruction.
That is the whole point of the phase: one resolver decides D7 precedence for
every front end, instead of each one patching the fields it happens to know
about after the fact.

**Values and provenance come from different places for the gate, on purpose.**
The scheme and the four severity levels are resolved by
``workflows.compare_configuration.resolve_compare_config`` *before* this runs, and that
resolution is what the run's verdict and exit code were computed from -- so
those are the values a receipt must report, and they are written through
:func:`~abicheck.contract_context.with_resolved_gate` unchanged. The
canonical resolver re-derives the same two from the same inputs and is used
for their *provenance*. The two agreeing is a real, checkable claim rather
than an assumption: ``tests/test_cli_compare_config_receipt.py`` asserts it
across the input matrix.

Split out of :mod:`abicheck.cli_compare_helpers` when that file reached the
2000-line hard cap -- a cohesive unit (one concern, one caller), not an
arbitrary cut. Deliberately a **leaf**: it imports nothing from its caller,
so no cycle forms; answers arrive here as data (*typed*), and
``resolved_cfg``/``project_cfg`` stay ``Any`` since typing them would
import the very module this one is split out of.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .frontends.cli.options.params import DEFAULT_POLICY_PROFILE

if TYPE_CHECKING:
    from .pack_application import PackApplication
    from .workflows.gate import SeverityConfig


def resolve_cli_config(params: Mapping[str, Any], **kwargs: Any) -> Any:
    """Resolve one :class:`CompatibilityEvaluationConfig` for this invocation.

    :func:`cli_configuration_inputs` through the canonical resolver. Raises
    whatever that resolver raises (a D7 same-tier conflict, a D8 pack
    conflict, a malformed pack manifest); mapping those onto an exit code is
    the caller's job, as that module documents.
    """
    from .compatibility_evaluation_frontend import (
        FrontEnd,
        resolve_compatibility_evaluation_config,
    )
    from .workflows.compare_input_adapters import cli_configuration_inputs

    explicit, project = cli_configuration_inputs(params, **kwargs)
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.CLI, explicit=explicit, project=project
    )


def dry_run_scheme_label(resolved_cfg: Any, pack_paths: Collection[Any]) -> str:
    """How ``compare --dry-run`` should describe the exit-code scheme.

    The renderer was previously handed the *raw* ``--exit-code-scheme`` value,
    so it printed "legacy (0/2/4)" whenever the flag was absent -- including
    when ``.abicheck.yml`` configured severity and the real run therefore used
    the severity scheme. That predates ``--pack`` and is wrong for the plain
    config case too, so this reports the *resolved* scheme instead (Codex
    review, reproduced against a config-only run with no pack involved).

    A selected pack is called out rather than resolved: a gate pack can move
    the scheme, but resolving one here is not safe. The configuration cannot
    be resolved before the ``--policy-file`` this command loads much later,
    and a *partial* resolution would run D8 conflict detection against
    different pins than the real one -- which can reject a pack pair the real
    run accepts. Saying the scheme may still move is honest; asserting one
    computed under different precedence would not be.
    """
    scheme = getattr(resolved_cfg, "exit_code_scheme", None)
    label = "legacy (0/2/4)" if scheme == "legacy" else (scheme or "legacy (0/2/4)")
    return f"{label}; a selected --pack may adjust it" if pack_paths else label


def resolve_release_pack_application_from_ctx(
    ctx: Any,
    *,
    contract_mode: str | None,
    policy: str,
    policy_file_path: Path | None,
    suppress: Path | tuple[Path, ...] | None,
    require_justification: bool,
    severity_preset: str | None,
    pack_paths: tuple[Path, ...],
    contract_evaluation: bool,
    project_cfg: Any,
    project_path: Path | None,
    project_sha256: str | None,
    policy_option: str | None,
    policy_path: Path | None,
    policy_sha256: str | None,
    headers: tuple[Path, ...] = (),
) -> Any:
    """``resolve_release_pack_application``, but reading "was this typed?"
    (and a best-effort ``--policy-file`` pre-read) off the real Click *ctx*
    itself, the way ``_resolve_evaluation_config`` does for the single-pair
    path -- split out so the caller stays a single call rather than this
    whole resolution inlined at the call site.

    Resolved even when *pack_paths* is empty (finding 1, round 4) -- see
    ``resolve_release_pack_application``'s own docstring. Raises
    ``click.UsageError`` rather than the raw resolver exceptions, so the
    caller needs no except.
    """
    import click
    import yaml

    from .errors import PackManifestError
    from .workflows.compare_input_adapters import typed_parameter_names
    from .workflows.pack_resolution import (
        preread_policy_file,
        resolve_release_pack_application,
    )

    # The pre-read itself (and why a broken file is not reported here) lives
    # with the resolution it feeds: `workflows.pack_resolution`.
    loaded_policy_file = preread_policy_file(policy_file_path)
    typed = {
        name
        for name in typed_parameter_names()
        if ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
    }
    try:
        return resolve_release_pack_application(
            {
                "contract_mode": contract_mode,
                "policy": policy,
                "policy_file_path": policy_file_path,
                "suppress": suppress,
                "require_justification": require_justification,
                "severity_preset": severity_preset,
                "pack_paths": pack_paths,
                "headers": headers,
            },
            contract_evaluation=contract_evaluation,
            typed=typed,
            project_cfg=project_cfg,
            project_path=project_path,
            policy_file=loaded_policy_file,
            suppress_path=suppress,
            policy_option=policy_option,
            policy_path=policy_path,
            policy_sha256=policy_sha256,
            project_sha256=project_sha256,
            symbols_list=None,
        )
    except (
        # A D7 same-tier / D8 pack conflict is a `ValueError` subclass of the
        # resolver's own, covered by the `ValueError` arm below.
        PackManifestError,
        # `resolve_release_pack_application`'s own `resolve_cli_config` call
        # loads `--policy`'s document a *second* time (for D7 provenance,
        # via `compatibility_evaluation_frontend`), independently of the
        # already-guarded pre-read a few lines up -- a genuinely malformed
        # policy document (an unknown ChangeKind slug, a non-mapping
        # `overrides:`, an unreadable file, or a plain YAML syntax error)
        # raises `PolicyError`/`OSError`/`ImportError`/`yaml.YAMLError` there,
        # uncaught, unlike the single-pair path (whose own
        # `_load_suppression_and_policy` call already converts the identical
        # failure to a clean error *before* ever reaching this resolver, so
        # it never hits this second load at all). Caught here instead of left
        # to propagate as a raw traceback (Codex review, found while adding
        # direct test coverage for this function; `yaml.YAMLError` added in a
        # second round once real syntax-error input was tried, not just a
        # semantically-invalid-but-well-formed document). `yaml` is safe to
        # import unconditionally here: if PyYAML itself were missing,
        # `PolicyFile.load`'s own `ImportError` would already have fired at
        # the guarded pre-read above, before this call ever runs.
        ValueError,
        OSError,
        ImportError,
        yaml.YAMLError,
    ) as exc:
        raise click.UsageError(str(exc)) from exc


def record_resolved_config(
    result: Any,
    resolved_cfg: Any,
    config: Any,
    *,
    project_config_path: Any = None,
    project_config_sha256: str | None = None,
    require_complete_analysis_stated: bool = False,
) -> None:
    """Install this front end's resolved configuration onto the context.

    *project_config_path*/*project_config_sha256*/*require_complete_
    analysis_stated* are forwarded, not re-derived, to :func:`~abicheck.
    contract_context.with_resolved_gate` -- see its own docstring.

    A no-op unless ``--contract`` produced a context (and unless the caller
    resolved a *config* at all). Runs before any report is rendered, so
    every output path sees one configuration resolved by the canonical
    resolver rather than the core verb's argument-shaped reconstruction,
    and sees the gate the run was actually scored with rather than
    :class:`GateConfig`'s built-in defaults.

    *config* arrives already resolved rather than being resolved here: since
    ADR-049's ``--pack`` landed, the same object also *configures* the run
    (``pack_application``), and it has to exist before the comparison for
    that. Resolving a second time here would re-read every pack manifest --
    the "one read per resolution" rule ``resolve_compatibility_evaluation_config``
    keeps internally, for the same reason -- and would be handed the
    already-pack-folded policy file, so the receipt would report a pack's
    contribution as an explicitly stated ``--policy-file`` override.
    """
    if config is None:
        return
    # CLI cleanup phase two, PR B (Codex review, PR #803): stamped
    # unconditionally, *before* the contract_context-only branch below --
    # a `--pack`-only run (no `--contract`) never builds a
    # `PersistedContractContext` at all, so `effective_config_digest`'s
    # rich tier would otherwise be silently unreachable for it even though
    # `config` (this same object) is a real, fully-resolved
    # `CompatibilityEvaluationConfig` with real pack identities.
    result.evaluation_config = config

    from .contract_evidence import PersistedContractContext

    ctx = getattr(result, "contract_context", None)
    if not isinstance(ctx, PersistedContractContext):
        return
    from .contract_context import with_resolved_config, with_resolved_gate
    from .workflows.evaluation_identities import SEVERITY_CATEGORY_FIELDS

    ctx = with_resolved_config(ctx, config)
    # The values the run was scored with, with the canonical resolver's
    # provenance -- see this module's docstring for why the two halves come
    # from different resolutions, and which test holds them to agreeing.
    result.contract_context = with_resolved_gate(
        ctx,
        exit_code_scheme=resolved_cfg.exit_code_scheme,
        severity=resolved_cfg.severity,
        severity_provenance={
            category: config.provenance[SEVERITY_CATEGORY_FIELDS[category]]
            for category in SEVERITY_CATEGORY_FIELDS
        },
        require_complete_analysis=resolved_cfg.require_complete_analysis,
        require_complete_analysis_stated=require_complete_analysis_stated,
        project_config_path=str(project_config_path) if project_config_path else None,
        project_config_sha256=project_config_sha256,
    )


def _release_summary_effective_config_block(
    severity_config: SeverityConfig | None,
    *,
    policy: str = DEFAULT_POLICY_PROFILE,
    policy_file_path: Path | None = None,
    suppress: Path | tuple[Path, ...] | None = None,
    pack_application: PackApplication | None = None,
    scope_public_headers: bool = True,
    on_incomplete_scope: str = "",
    fail_on_removed_library: bool | None = None,
    require_complete_analysis: bool = False,
    env_matrix_source_sha256: str | None = None,
    excluded_header_patterns: str = "",
) -> tuple[str, dict[str, str]]:
    """The ``(digest, fields)`` pair for a release-level *summary* document
    (the primary release JSON and a per-component export's ``summary.json``
    alike) -- narrower than a per-library sidecar's own digest: this always
    resolves the *baseline* tier's field *shape* (see
    ``effective_config_digest.py``'s docstring for the two tiers), even
    though ``pack_application.resolved_config`` is a real config since round
    4's fix -- ``policy.overrides`` reads straight from it (round 6 finding
    1) rather than switching the whole receipt to the rich tier, which would
    move every other field and the pinned ``_tier`` for no reason this needs.

    P1 (CLI-audit): this used to compute the baseline tier from a bare,
    empty ``SimpleNamespace()`` -- carrying only *severity_config* -- so
    ``policy.base``/``policy.reclassify``/``policy.overrides``/
    ``suppressions`` all read empty regardless of the real
    ``--policy``/``--policy-file``/``--suppress`` every library was
    actually compared under. Every library shares one such input, so
    resolving it once more here the same way
    (:func:`~abicheck.frontends.cli.options.params._load_suppression_and_policy`,
    folding *pack_application* like
    :func:`~abicheck.workflows.release_compare.collect_matrix_result`
    does) reproduces what any one library's own report shows for these
    fields; reloaded rather than threaded down since no per-library
    ``PolicyFile`` is retained at this scope (the reload runs inside the
    same ``dedup_validate_overrides_warnings()`` scope ``compare_release_cmd``
    opens, so it doesn't duplicate a per-library warning).

    Called from ``frontends.cli.release_report``/``cli_compare_release_matrix``/
    ``frontends.cli.release_summary`` (all three at their own ``no_growth``
    cap, hence living here).

    *scope_public_headers* (PR #1016): same bug shape as ``policy`` above,
    for a second field -- ``effective_config_fields_from_diff_result`` reads
    ``result.scope_to_public_surface``/``.scope_to_public_surface_requested``
    off whatever it's given, so a bare unset ``SimpleNamespace`` would fall
    back to ``False``/``True`` regardless of what ``scope.public``
    actually resolved to. The release fan-out has no ``--post-manifest``/
    forced-public-symbols concept of its own (unlike single-pair ``compare``,
    where the two can diverge), so both fields are simply the resolved
    value here.

    *excluded_header_patterns* is the release's canonical ``--exclude-header``
    identity (``model.header_exclusion_record.canonical_exclusion_identity``
    of the resolved rules), computed once at release scope for the same
    reason *env_matrix_source_sha256* below is.

    *env_matrix_source_sha256* (Codex review, P2 follow-up): the release-
    wide deployment-floor digest, computed once by the caller at release
    scope from the resolved ``EnvironmentMatrix`` -- not read off any
    per-library ``DiffResult`` (none exists at this scope), so a release
    with zero matched/completed pairs still reports a real ``policy.
    env_matrix`` rather than leaving it empty.
    """
    from types import SimpleNamespace

    from .contract_context import suppression_config_for
    from .effective_config_digest import (
        effective_config_digest,
        release_summary_fields,
    )
    from .frontends.cli.options.params import _load_suppression_and_policy
    from .workflows.gate import EffectiveGate

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    if pack_application is not None:
        from dataclasses import replace as _replace

        from .pack_application import policy_file_with_packs
        from .workflows.policy_file import PolicyFile

        pf = policy_file_with_packs(pf, pack_application, base_policy=policy)
        # Round 6 finding 1: fold above carries only the pack's own overrides -- resolved_config.policy.overrides is the one canonical D7 merge (file+pack+project); read it directly.
        rc = getattr(pack_application.resolved_config, "policy", None)
        rc_overrides = getattr(rc, "overrides", None)
        if rc_overrides is not None:
            pf = (
                _replace(pf, overrides=dict(rc_overrides))
                if pf
                else PolicyFile(base_policy=policy, overrides=dict(rc_overrides))
            )
    suppression_config = suppression_config_for(suppression)
    # `pf.base_policy`, not the raw `policy` argument, when a `--policy-file` resolved one (Codex review, PR #1016): `checker.compare`'s own `effective_policy = policy_file.base_policy if policy_file is not None else policy` is what a real per-library report's `policy.base` field reflects, so a policy document naming a non-default `base_policy:` (e.g. `sdk_vendor`) produced a release-summary digest still reading the CLI default (`strict_abi`) while every per-library report agreed on the real base -- this stand-in must resolve the identical way.
    effective_policy = pf.base_policy if pf is not None else policy
    ec_result = SimpleNamespace(
        policy=effective_policy,
        policy_file=pf,
        suppression_source_sha256=(
            suppression_config.sha256 if suppression_config is not None else None
        ),
        scope_to_public_surface=scope_public_headers,
        scope_to_public_surface_requested=scope_public_headers,
        # Codex review, P2 follow-up: feeds `policy.env_matrix` below.
        env_matrix_source_sha256=env_matrix_source_sha256,
        # Feeds `surface.exclude_headers`; release-wide for the same reason
        # `env_matrix_source_sha256` above is (no per-library DiffResult
        # survives to this scope, and a release that matched nothing must
        # still report the rules it would have applied).
        excluded_header_patterns=excluded_header_patterns,
        # ADR-068 D4/Phase 5 + §4.1's AUTO rows: modulation, surface metrics and ADR-039 reconciliation are unconditional now (forced on at the Tier-2 chokepoint every library here routes through), so this stand-in must agree rather than default to the old "off".
        pattern_verdicts_enabled=True,
        surface_metrics_enabled=True,
        reconcile_build_context_enabled=True,
    )
    # No result/scope at this release-summary scope, but require_complete_analysis/on_incomplete_scope/fail_on_removed_library ARE (Codex review, PR #1192 fourth round for the latter two; PR #1238 for the first, once `assurance.require_complete` started applying to a release at all) -- read from `gate.*` below, not the now-removed `ec_result.on_incomplete_scope` this used to set instead.
    gate = EffectiveGate.from_severity(
        severity_config,
        require_complete_analysis=require_complete_analysis,
        on_incomplete_scope=on_incomplete_scope or None,
        fail_on_removed_library=fail_on_removed_library,
    )
    resolved = getattr(pack_application, "resolved_config", None)
    ec_fields = release_summary_fields(resolved, ec_result, policy_file=pf, gate=gate)
    return effective_config_digest(ec_fields), ec_fields
