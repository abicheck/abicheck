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

"""The ``compare`` command's two-operand adapter: preflight, then dispatch.

:func:`run_compare` turns ``compare``'s parsed options into a run: the
preflight a ``--dry-run`` shares (project config, required symbols, operand
classification, manifests), the ``--dry-run`` receipt, the directory/package
release fan-out, and -- for two single operands -- one
:class:`~abicheck.workflows.compare.session_request.PairSessionRequest`
handed to :func:`abicheck.workflows.compare.session.run_pair_session`, whose
outcome it renders (``frontends.cli.compare_result``). Everything from the
collect-mode fold to classification is the session's; this module only
translates its refusals back into Click (``frontends.cli.run_input_errors``)
and its two non-verdict outcomes (not comparable, ``--budget`` exceeded) into
their reports. Classification goes through the shared
``workflows.compare_classify`` core every scalar route runs, never a direct
``checker.compare`` call (cli-contract, ADR-037 D10.1).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .cli_compare_fold import _exit_on_budget_overflow, report_not_comparable_and_exit
from .cli_compare_options import (
    _reject_bundle_facts_out_for_single_pair,
    _resolve_debug_roots,
    stated_options,
)
from .cli_options import (
    resolve_compile_context,
    resolve_contract_domain,
    resolve_contract_evaluation,
)
from .cli_resolve import resolve_directory_compile_context
from .frontends.cli.compare_config_scope import (
    config_scope_inputs,
    consumer_manifest_specs,
)
from .frontends.cli.compare_input_validation import (
    classify_and_reject_operands,
    enter_budget_scope,
    parse_budget_option,
)
from .frontends.cli.compare_preflight import _preflight_manifests_and_audit
from .frontends.cli.compare_report import _reject_flags_unsupported_for_set_inputs
from .frontends.cli.compare_result import _report_compare_result
from .frontends.cli.compare_use_cases import reject_use_cases_without_carrying_output
from .frontends.cli.project_config import (
    compare_settings_usage_errors,
    config_exclude_headers,
    resolve_project_compare_config,
)
from .frontends.cli.run_input_errors import click_input_errors
from .frontends.cli.runtime import _setup_verbosity
from .report.report_modes import normalize_report_mode
from .serialization import run_scoped_digest_cache
from .workflows.compare.configuration import resolve_required_symbol_policy
from .workflows.compare.project_inputs import (
    note_unapplied_post_manifest,
    release_acknowledgment_inputs,
)
from .workflows.compare.use_case_attribution import (
    UseCaseAttributionError,
    read_use_case_manifest,
)
from .workflows.compare_configuration import (
    load_required_symbols,
    resolve_compare_collect_mode,
)
from .workflows.header_exclusion_audit import unmatched_exclusion_warning
from .workflows.ownership_request import (
    project_ownership_request,
    project_ownership_scope,
)
from .workflows.public_header_boundary import project_config_public_header_dirs

if TYPE_CHECKING:
    from .model.consumer_spec import ConsumerAppInput


# Plan slice 7m: ``_reject_incoherent_compare_flags`` is gone. Both rules it
# enforced -- a dry run asked to write a report, and two reports aimed at one
# file -- are now properties of the one export request, checked by
# ``frontends.cli.options.export`` while the operand is parsed: collision
# detection runs across *every* target rather than only between the primary
# and the repeats of one flag, and ``reject_dry_run_with_exports`` covers the
# dry run at ``compare``'s own callback boundary. Two flags with two grammars
# needed a coherence check between them; one grammar does not.


@run_scoped_digest_cache
def run_compare(
    ctx: click.Context,
    *,
    exclude_headers: tuple[str, ...] = (),
    config_overrides: Mapping[str, object] | None = None,
    old_input: Path,
    new_input: Path,
    output_dir: Path | None,
    select: tuple[str, ...] = (),
    select_required: tuple[str, ...] = (),
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    manifest_path: Path
    | None,  # bundle_system_providers/cohorts: PR J, see resolved_cfg
    bundle_facts_out: Path | None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    # Phase 7 (one-comparison-product.md §4.1, ADR-037 D8.1): `lang`/
    # `header_backend`/`sysroot`/`nostdinc`/`compiler_path`/
    # `compiler_prefix`/`compiler_option_tokens`/`old_header_backend`/
    # `new_header_backend` are no longer CLI-populated -- `--lang`/
    # `--ast-frontend`/`--compiler`/`--compiler-prefix`/`--compiler-option`/
    # `--sysroot`/`--nostdinc` are all gone from `compare`'s CLI (CONFIG
    # class; `.abicheck.yml`'s `compile:` block is their only source now).
    # `lang` is resolved from config below (no `CompileContext` field for
    # it); the rest reach the same `resolve_compile_context` call as
    # before with fixed "nothing explicit" inputs, which already treats an
    # unset/default value as "defer to config" the same way `compile.std`/
    # `compile.defines` already did.
    lang: str | None = None,
    header_backend: str = "auto",
    sysroot: Path | None = None,
    nostdinc: bool = False,
    # --gcc-options removed as a CLI flag (CLI audit PR 5/5); kept as an
    # internal-only, defaulted-None parameter -- see cli.py's dump_cmd for
    # why (never populated from the CLI anymore, only ever None here).
    gcc_options: str | None = None,
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
    # ADR-074's -D/--define: one both-sides macro set (never per-side -- two
    # sides parsed under different macro contexts are two different public
    # surfaces), folded by macro name against .abicheck.yml compile.defines
    # inside the shared resolve_compile_context below.
    defines: tuple[str, ...] = (),
    old_header_backend: str | None = None,
    new_header_backend: str | None = None,
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    fmt: str,
    output: Path | None,
    suppress: Path | tuple[Path, ...] | None,
    acknowledgments: Path | None = None,
    policy: str,
    policy_file_path: Path | None,
    severity_preset: str | None,
    config: Path | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    include_dependencies: bool,
    show_only: str | None,
    report_mode: str,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    # ADR-068 D4/Phase 5: --pattern-verdicts/--surface-metrics are gone --
    # both run unconditionally now (compare_snapshots() passes True for
    # both below). Plan slice 7o did the same to the three *disclosure*
    # tokens that used to gate rendering the ledgers those analyses produce
    # (the `patterns`/`filtered`/`suppressions` view tokens), so no parameter
    # corresponds to them here any more either.
    verbose: bool,
    use_cases_manifest: Path | None = None,
    old_build_info: Path | None = None,
    new_build_info: Path | None = None,
    old_sources: Path | None = None,
    new_sources: Path | None = None,
    depth: str | None = None,
    probe_matrix_old: Path | None = None,
    probe_matrix_new: Path | None = None,
    # ADR-068 D4/Phase 5: -o is repeatable (§4.1). Each entry is an
    # already-parsed, already PATH-uniqueness-checked (FORMAT, PATH) pair.
    secondary_writes: tuple[tuple[str, Path], ...] = (),
    dry_run: bool = False,
    used_by_apps: tuple[ConsumerAppInput, ...] = (),
    used_by_manifests: tuple[Path, ...] = (),
    required_symbols_opt: tuple[str, ...] = (),
    diagnostic_comparison: bool = False,
    contract_mode: str | None = None,
    pack_paths: tuple[Path, ...] = (),
    include_labels: dict[Path, str] | None = None,
    old_dump_manifest: Path | None = None,
    new_dump_manifest: Path | None = None,
    frontend_context: str = "host",
    since: str | None = None,  # ADR-068 Phase 2c: changed-path localization
    changed_paths_opt: tuple[str, ...] = (),
    abi3: str | None = None,  # ADR-068 Phase 2d: candidate-side abi3 audit
    budget: str | None = None,
) -> None:
    """Run the single-pair (or set fan-out) ``compare`` flow and exit accordingly."""
    from .dry_run import reject_dry_run_with_output
    from .frontends.cli.operand_diagnostics import _warn_unused_set_flags

    reject_dry_run_with_output(dry_run, output)
    budget_s = parse_budget_option(budget)
    enter_budget_scope(ctx, budget_s)  # before inline embedding too; see its docstring
    # --contract is the only way to ask for the ADR-049 evaluator on the CLI
    # (abicheck.cli_options.resolve_contract_evaluation) -- resolved here,
    # before contract_evaluation is used for anything else in this function,
    # so every downstream use (the typed CompareRequest included) sees the
    # already-resolved value and behaves exactly as if the caller had passed
    # contract_evaluation=True explicitly.
    contract_evaluation = resolve_contract_evaluation(contract_mode)
    contract_mode = resolve_contract_domain(contract_mode, ctx)
    _setup_verbosity(verbose)

    # G31 Phase C follow-up (AGENTS.md "dump --lang c++ is silently
    # discarded ..." known gap): --lang used to carry the same Click default
    # ("c++", indistinguishable from a genuine --lang c++) that dump_cmd's
    # own lang_explicit detection existed to resolve. Threaded through
    # _resolve_compare_snapshots -> CompareRequest.lang_explicit so a live
    # ELF/PE/Mach-O side's header-AST pass honors an explicit request on a
    # language-ambiguous header instead of silently auto-detecting past it.
    #
    # Phase 7 (one-comparison-product.md §4.1): `--lang` has no CLI spelling
    # left on `compare` at all, so `ctx.get_parameter_source("lang")` can
    # never report COMMANDLINE any more -- `compile.lang` in `.abicheck.yml`
    # is the only remaining source of an explicit request, resolved below
    # once `resolved_cfg` exists (Codex review: computing this before that
    # resolution silently discarded every config-set `compile.lang`).

    # ADR-037 D4: load the project config and merge CLI flags over it
    # (precedence CLI > config > built-in default) *before* dispatch, so both the
    # single-file and the directory/package fan-out paths share one resolution.
    # It comes first now: the consumer manifests, the required-symbol contract
    # and the rendering view are config keys, read from it below.
    cfg_path, project_cfg, resolved_cfg, cfg_sha = resolve_project_compare_config(
        config=config,
        severity_preset=severity_preset,
        config_overrides=config_overrides,
    )
    # Consumer scope and view come from .abicheck.yml (ADR-091 Phase 5).
    used_by_manifests, required_symbols_opt, config_view = config_scope_inputs(
        project_cfg, cfg_path, used_by_manifests, required_symbols_opt
    )
    report_mode, show_only = config_view or (report_mode, show_only)
    used_by_apps = (*used_by_apps, *consumer_manifest_specs(used_by_manifests))

    with compare_settings_usage_errors():
        (
            required_symbols,
            required_symbols_from_file,
            required_symbols_sha,
            required_symbols_path,
        ) = load_required_symbols(required_symbols_opt)
    if used_by_apps and required_symbols:
        raise click.UsageError(
            "--used-by and consumers.required_symbols are mutually "
            "exclusive: scope the comparison to either application imports or "
            "an explicit required-symbol contract, not both."
        )
    policy, policy_selected_by, policy_selected_path, policy_selected_sha = (
        resolve_required_symbol_policy(
            policy,
            required_symbols=required_symbols,
            policy_from_commandline=(
                ctx.get_parameter_source("policy")
                == click.core.ParameterSource.COMMANDLINE
            ),
            required_symbols_from_file=required_symbols_from_file,
            required_symbols_path=required_symbols_path,
            required_symbols_sha=required_symbols_sha,
        )
    )
    # ADR-037 D4: load the project config and merge CLI flags over it
    # (precedence CLI > config > built-in default) *before* dispatch, so both the
    # single-file and the directory/package fan-out paths share one resolution.
    exclude_headers = config_exclude_headers(exclude_headers, project_cfg)
    # One-comparison-product Phase 9b: no CLI flag any more -- scope.public
    # (or its built-in True) is the whole answer for a run with no --contract.
    scope_public_headers = resolved_cfg.scope_public
    collapse_versioned_symbols = resolved_cfg.collapse_versioned_symbols
    strict_suppressions = resolved_cfg.strict_suppressions
    require_justification = resolved_cfg.require_justification
    require_complete_analysis = (
        resolved_cfg.require_complete_analysis
    )  # former CLI flag
    # Was assurance.require_complete literally stated (vs. an omitted key
    # defaulting to the same resolved value)? See
    # contract_gate_require_complete_provenance.py's module docstring.
    require_complete_analysis_stated = (
        getattr(project_cfg, "assurance_require_complete", None) is not None
    )
    # ADR-068 D5 / Phase 7: `--lang` and the debug flags are config-only;
    # `config_run_settings` is the one reading of them, shared with
    # `--no-baseline` so the two `compare` shapes cannot differ. `lang` is
    # always None here (compare_cmd's kwargs carry no "lang" key; the
    # stored-bundle-facts path resolves its own upstream).
    from .frontends.cli.compare_config_settings import config_run_settings

    run_settings = config_run_settings(resolved_cfg)
    debug_format_opt = run_settings.debug_format
    dwarf_only = run_settings.dwarf_only
    debuginfod = run_settings.debuginfod
    debuginfod_url = run_settings.debuginfod_url
    # Phase 7 (§4.1 CONFIG row): --pdb-path is config-only; see compare_pdb_config's docstring.
    from .frontends.cli.compare_pdb_config import resolve_and_reject_shared_pdb_path

    pdb_path = resolve_and_reject_shared_pdb_path(
        run_settings.pdb_path, old_input=old_input, new_input=new_input
    )
    show_redundant = resolved_cfg.show_redundant
    if lang is None:
        lang = run_settings.lang
    lang_explicit = run_settings.lang_explicit
    # Phase 7: `--allow-ast-frontend-fallback`/`--allow-unsupported-castxml`
    # are gone from compare's CLI too; a `.abicheck.yml` `compile:` block
    # setting either to `true` has the identical effect the removed flag
    # had (both were already pure env-var togglers).
    from .buildsource.build_config import BuildConfig as _BuildConfig
    from .cli_options import apply_compile_config_env_toggles

    apply_compile_config_env_toggles(
        ctx, project_cfg if isinstance(project_cfg, _BuildConfig) else None
    )

    # P1.1 (Codex review): resolved ahead of the inline-embed block below so a
    # raw --old/new-sources tree's inline `dump` also gets per-side debug roots.
    resolved_old_debug, resolved_new_debug = _resolve_debug_roots(
        debug_roots, debug_roots_old, debug_roots_new
    )

    # ADR-037 D7: input-type dispatch. The resolved config (scope/suppression/
    # severity) is forwarded so a set-input compare classifies the same way a
    # single-pair one would (ADR-037 D4).
    old_kind, new_kind = classify_and_reject_operands(old_input, new_input)

    # ADR-068 D4/Phase 5: split out to _helpers_compare.reject_use_cases_
    # without_carrying_output (keeps this module under the 2000-line hard
    # cap) -- with -o repeatable, "one output carrying it" is now the
    # primary render OR any secondary write, not just one --write.
    reject_use_cases_without_carrying_output(
        fmt=fmt,
        secondary_fmts=[f for f, _ in secondary_writes],
        use_cases_manifest=use_cases_manifest,
    )

    # CLI cleanup phase two, PR B slice 1: None for a single-pair compare or a
    # release/directory one with no --pack; resolved just below (ahead of the
    # --dry-run emit) otherwise, so a dry run and the real run agree.
    release_pack_application = None
    release_depth: str | None = None
    if {old_kind, new_kind} & {"directory", "package"}:
        release_depth = _reject_flags_unsupported_for_set_inputs(
            ctx,
            used_by_apps=used_by_apps,
            required_symbols=required_symbols,
            diagnostic_comparison=diagnostic_comparison,
            include_labels=include_labels,
            use_cases_manifest=use_cases_manifest,
            budget=budget,
            pdb_path=pdb_path,
        )
        note_unapplied_post_manifest(
            project_cfg,
            route="a directory/package comparison",
            reason="the per-library fan-out has no contract-overlay channel",
            notify=_echo_err,
        )
        # Codex review, fresh evidence ("Validate release-only view
        # restrictions before dry-run exit"): output.view leaf/root-cause is
        # rejected for a directory/package operand inside
        # _dispatch_release_compare, but that check never ran for
        # --dry-run (emit_dry_run raises SystemExit before dispatch is ever
        # reached) -- so a dry run reported "ok" (exit 0) for exactly the
        # combination the identical non-dry-run invocation rejects (exit
        # 64). Validated here too, ahead of the --dry-run emit below, the
        # same way every other release-only flag conflict in this block
        # already is.
        from .frontends.cli.commands.compare_routing import (
            reject_release_incompatible_view_mode,
        )

        reject_release_incompatible_view_mode(report_mode)
        # Resolved unconditionally, not only under `--pack` (finding 1,
        # round 4): a no-pack run's own project-backed `.abicheck.yml`
        # `policy.overrides` still needs to reach a real config for
        # `record_release_resolved_config` -- see that resolver's docstring.
        from .cli_compare_receipt import resolve_release_pack_application_from_ctx

        release_pack_application = resolve_release_pack_application_from_ctx(
            ctx,
            contract_mode=contract_mode,
            policy=policy,
            policy_file_path=policy_file_path,
            suppress=suppress,
            require_justification=require_justification,
            severity_preset=severity_preset,
            pack_paths=pack_paths,
            contract_evaluation=contract_evaluation,
            project_cfg=project_cfg,
            project_path=cfg_path,
            project_sha256=cfg_sha,
            policy_option=policy_selected_by,
            policy_path=policy_selected_path,
            policy_sha256=policy_selected_sha,
            headers=(*headers, *old_headers_only, *new_headers_only),
        )

    # Parsed here, in the preflight, not only at the post-comparison
    # attribution call: --dry-run returns before that call, so a malformed
    # manifest passed a dry run as "validated" (exit 0) while the identical
    # real invocation rejected it (exit 64) -- the one thing --dry-run
    # promises not to do (Codex review). The parse is pure I/O + validation,
    # the same cheap read-only resolution the dry run already performs for
    # every other input; the result is discarded because attribution needs
    # both snapshots' graphs, which a dry run deliberately never builds.
    #
    # After the flag-combination rejections above, for the reason the
    # --dump-manifest parse below states for itself: when --use-cases was
    # never going to work here at all, "not supported here" is the useful
    # message, not "your manifest is malformed".
    #
    # The *other* --use-cases exit a dry run still cannot predict is "neither
    # side carries a source graph", and deliberately so: that is a property
    # of the resolved operands, not of the command line. It is knowable for a
    # snapshot operand and unknowable for a live binary, whose graph only
    # exists after a dump the dry run must not perform -- so checking it
    # would make the dry run's answer depend on which operand shape it was
    # given. Manifest validity has no such asymmetry.
    if use_cases_manifest is not None:
        try:
            read_use_case_manifest(use_cases_manifest)
        except UseCaseAttributionError as exc:
            raise click.UsageError(str(exc)) from exc

    # Parsed after the directory/package rejection above (not before, like an
    # earlier revision of this function did): a malformed --dump-manifest on
    # a directory/package compare must fail with that block's clear "not
    # supported for directory/package" message, not a confusing "invalid
    # YAML" one for a flag combination that was never going to work anyway
    # (Codex review).
    old_manifest_obj, new_manifest_obj = _preflight_manifests_and_audit(
        old_dump_manifest=old_dump_manifest,
        new_dump_manifest=new_dump_manifest,
        pack_paths=pack_paths,
        policy_file_path=policy_file_path,
        contract_evaluation=contract_evaluation,
    )
    from .cli_compare_receipt import dry_run_scheme_label

    # Round 5 finding 1: validate policy.overrides before --dry-run exits.
    from .pack_application import preflight_validate_project_policy_overrides

    preflight_validate_project_policy_overrides(project_cfg, cfg_path)
    # One warning per run for a rule that matched nothing -- asked here,
    # above the scalar-versus-release branch, so it is stated exactly once
    # whatever the operand is (see the owner's docstring for why once).
    _exclusion_warning = unmatched_exclusion_warning(
        [*headers, *old_headers_only, *new_headers_only], exclude_headers
    )
    if _exclusion_warning is not None:
        click.echo(_exclusion_warning, err=True)
    # The L2 compile-context inputs for the --dry-run receipt and the release
    # fan-out, the same ones the single-pair session states on its request
    # (tests/test_compare_session.py); `cfg_path` is `config` or the
    # auto-discovered .abicheck.yml (PR #1154).
    _compile_context_kwargs: dict[str, Any] = dict(
        ctx=ctx,
        config_overrides=config_overrides,
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=includes,
        build_config=cfg_path,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
        defines=defines,
        config_explicit=(config is not None),
    )
    if dry_run:
        from .dry_run import emit_dry_run
        from .frontends.cli.compare_dry_run import (
            build_compare_dry_run_result,
            effective_compare_defines,
        )

        # ADR-043 D4's dry-run report must reflect the *effective* depth, not
        # just echo `--depth` back; `frontends/cli/compare_dry_run.py`'s
        # docstring explains why it takes the resolved pair as parameters.
        # The parsed manifests say which operands parse headers without -H.
        with compare_settings_usage_errors():
            collect_mode_dr, effective_depth_label_dr = resolve_compare_collect_mode(
                depth,
                resolved_cfg.source_method,
                old_sources,
                new_sources,
                old_build_info,
                new_build_info,
            )
        emit_dry_run(
            build_compare_dry_run_result(
                # ADR-074: the effective macro set, resolved through the very
                # same call the real run makes below (see that helper).
                defines=effective_compare_defines(
                    resolve_compile_context, **_compile_context_kwargs
                ),
                compile_context=resolve_compile_context(**_compile_context_kwargs)[0],
                lang=lang,
                old_dump_manifest=old_manifest_obj,
                new_dump_manifest=new_manifest_obj,
                old_input=old_input,
                new_input=new_input,
                old_kind=old_kind,
                new_kind=new_kind,
                depth=depth,
                collect_mode=collect_mode_dr,
                effective_depth_label=effective_depth_label_dr,
                source_method=resolved_cfg.source_method,
                headers=headers,
                includes=includes,
                old_headers_only=old_headers_only,
                new_headers_only=new_headers_only,
                old_sources=old_sources,
                new_sources=new_sources,
                old_build_info=old_build_info,
                new_build_info=new_build_info,
                cfg_path=cfg_path,
                fmt=fmt,
                exit_code_scheme=dry_run_scheme_label(resolved_cfg, pack_paths),
                header_backend=header_backend,
                used_by_apps=used_by_apps,
                required_symbols=required_symbols,
                select=select,
                select_required=select_required,
                exclude_headers=tuple(exclude_headers or ()),
                since=since,
                changed_paths_opt=changed_paths_opt,
                config=config,
            )
        )

    if {old_kind, new_kind} & {"directory", "package"}:
        # Both-sides L2 compile context for the release fan-out -- see
        # resolve_directory_compile_context's own docstring.
        directory_compile_context, directory_includes = (
            # The identical resolution the single-pair path performs, from the
            # identical inputs (see the kwargs dict bound above the --dry-run
            # branch) -- including `config_explicit`, without which an
            # auto-discovered `compile.compiler` would bypass the untrusted-
            # executable-selection gate (Codex review, PR #1154), and ADR-074's
            # `defines`, without which a release fan-out would silently drop
            # every -D the single-pair path honors.
            resolve_directory_compile_context(**_compile_context_kwargs)
        )
        # Dirs the config appended past the CLI -I roots (mirrors the single-pair
        # `config_includes` split below): must survive a per-library-pair
        # `--old/new-include` override, which otherwise replaces `includes`.
        directory_config_includes = tuple(directory_includes[len(includes) :])
        # Off the owner, never via ``abicheck.cli`` (install_facade_guard);
        # ADR-068 §3 #23: also thread policy.overrides to the release fan-out.
        from .frontends.cli.commands.compare_routing import _dispatch_release_compare
        from .pack_application import resolve_release_project_policy_overrides
        from .workflows.release_member_request import ReleaseMemberRunInputs

        # ADR-075: every member dump and the release surface are classified
        # under the one project config (a run-scoped rule, like crosschecks).
        with project_ownership_scope(project_ownership_request(project_cfg, cfg_path)):
            project_policy_overrides = resolve_release_project_policy_overrides(
                project_cfg, cfg_path
            )
            with click_input_errors():
                acknowledgment_inputs = release_acknowledgment_inputs(
                    project_cfg,
                    cfg_path,
                    config_explicit=config is not None,
                    override_path=acknowledgments,
                    notify=_echo_err,
                )
            _dispatch_release_compare(
                ctx,
                old_dir=old_input,
                new_dir=new_input,
                project_policy_overrides=project_policy_overrides,
                **acknowledgment_inputs,
                headers=headers,
                includes=directory_includes,
                old_headers_only=old_headers_only,
                new_headers_only=new_headers_only,
                old_includes_only=old_includes_only,
                new_includes_only=new_includes_only,
                old_version=old_version,
                new_version=new_version,
                lang=lang,
                lang_explicit=lang_explicit,
                fmt=fmt,
                output=output,
                output_dir=output_dir,
                suppress=suppress,
                strict_suppressions=strict_suppressions,
                require_justification=require_justification,
                policy=policy,
                policy_file_path=policy_file_path,
                dso_only=resolved_cfg.release_dso_only,  # Phase 7d: config-only, no CLI kwarg
                fail_on_removed=resolved_cfg.fail_on_removed_library,
                on_incomplete_scope=resolved_cfg.on_incomplete_scope,
                # ADR-071: folded over every compared member's assurance (max).
                require_complete_analysis=require_complete_analysis,
                support_promise=resolved_cfg.release_support_promise,  # Phase 7: config-only, no CLI kwarg
                select=select,
                select_required=select_required,
                debug_info1=debug_info1,
                debug_info2=debug_info2,
                devel_pkg1=devel_pkg1,
                devel_pkg2=devel_pkg2,
                include_private_dso=resolved_cfg.release_include_private_dso,
                manifest_path=manifest_path,
                bundle_system_providers=resolved_cfg.bundle_system_providers,
                bundle_cohorts=resolved_cfg.bundle_cohorts,
                bundle_facts_out=bundle_facts_out,
                scope_public_headers=scope_public_headers,
                include_dependencies=include_dependencies,
                severity_preset=resolved_cfg.merged_severity_preset,
                severity_abi_breaking=resolved_cfg.merged_severity_abi_breaking,
                severity_potential_breaking=resolved_cfg.merged_severity_potential_breaking,
                severity_quality_issues=resolved_cfg.merged_severity_quality_issues,
                severity_addition=resolved_cfg.merged_severity_addition,
                probe_matrix_old=probe_matrix_old,
                probe_matrix_new=probe_matrix_new,
                verbose=verbose,
                contract_evaluation=contract_evaluation,
                contract_mode=contract_mode,
                pack_application=release_pack_application,
                secondary_writes=secondary_writes,
                compile_context=directory_compile_context,
                config_includes=directory_config_includes,
                depth=release_depth,
                public_header_dirs=project_config_public_header_dirs(project_cfg),
                collapse_versioned_symbols=collapse_versioned_symbols,
                env_matrix=resolved_cfg.deployment,  # ADR-020b: config-only, no CLI kwarg
                # output.view's derived values, raw; _dispatch_release_compare
                # resolves and validates them (PR #1154 follow-up).
                report_mode=report_mode,
                show_only=show_only,
                # Forwarded, never dropped: the scalar path's exclusion rules,
                # dependency walk and debug roots (each once accepted here and
                # silently ignored -- a false clean for every member).
                exclude_headers=tuple(exclude_headers or ()),
                run_inputs=ReleaseMemberRunInputs(
                    follow_deps=bool(follow_deps),
                    dependency_search_paths=tuple(search_paths),
                    ld_library_path=ld_library_path,
                    old_debug_roots=tuple(resolved_old_debug),
                    new_debug_roots=tuple(resolved_new_debug),
                ),
            )
        return
    # Single-file/snapshot inputs: the set-only fan-out flags do not apply.
    _reject_bundle_facts_out_for_single_pair(bundle_facts_out)
    _warn_unused_set_flags(
        dso_only=resolved_cfg.release_dso_only,
        output_dir=output_dir,
        select=select,
        select_required=select_required,
    )
    # --report-mode impact is sugar for a "full" report with the impact table
    # on; the fold is shared with every public Python rendering path (Codex
    # review, PR #1284).
    report_mode, show_impact = normalize_report_mode(report_mode)

    # The single-pair session (workflows/compare/session.py) owns everything
    # from the collect-mode fold to classification; this adapter states the
    # resolved request and renders the outcome.
    from .frontends.cli.commands.compare_generation import REQUIRE_SAME_GENERATION_META
    from .workflows.compare import session as _session
    from .workflows.compare.session_request import (
        PairCompileInputs,
        PairEvidenceInputs,
        PairHeaderInputs,
        PairOperands,
        PairPolicyInputs,
        PairSessionRequest,
        ProjectContext,
        SessionHost,
    )
    from .workflows.dump.project_config import DumpDebugConfig

    session_request = PairSessionRequest(
        operands=PairOperands(old_input, new_input, old_version, new_version),
        headers=PairHeaderInputs(
            headers=headers,
            old_headers=old_headers_only,
            new_headers=new_headers_only,
            includes=includes,
            old_includes=old_includes_only,
            new_includes=new_includes_only,
            exclude_headers=tuple(exclude_headers or ()),
            include_labels=include_labels,
            old_dump_manifest=old_manifest_obj,
            new_dump_manifest=new_manifest_obj,
        ),
        compile=PairCompileInputs(
            lang=lang,
            lang_explicit=lang_explicit,
            header_backend=header_backend,
            old_header_backend=old_header_backend,
            new_header_backend=new_header_backend,
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            compiler_path=compiler_path,
            compiler_prefix=compiler_prefix,
            compiler_option_tokens=compiler_option_tokens,
            defines=defines,
            frontend_context=frontend_context,
            config_overrides=config_overrides or {},
        ),
        evidence=PairEvidenceInputs(
            debug=DumpDebugConfig(
                format=debug_format_opt,
                dwarf_only=dwarf_only,
                debuginfod=debuginfod,
                debuginfod_url=debuginfod_url,
                pdb_path=pdb_path,
            ),
            old_debug_roots=tuple(resolved_old_debug),
            new_debug_roots=tuple(resolved_new_debug),
            depth=depth,
            old_sources=old_sources,
            new_sources=new_sources,
            old_build_info=old_build_info,
            new_build_info=new_build_info,
            probe_matrix_old=probe_matrix_old,
            probe_matrix_new=probe_matrix_new,
            since=since,
            changed_paths=changed_paths_opt,
            abi3=abi3,
            old_devel_package=devel_pkg1,
            new_devel_package=devel_pkg2,
            follow_deps=follow_deps,
            search_paths=search_paths,
            ld_library_path=ld_library_path,
            include_dependencies=include_dependencies,
        ),
        policy=PairPolicyInputs(
            policy=policy,
            policy_file_path=policy_file_path,
            suppress=suppress,
            severity_preset=severity_preset,
            pack_paths=pack_paths,
            policy_selected_by=policy_selected_by,
            policy_selected_path=policy_selected_path,
            policy_selected_sha=policy_selected_sha,
            contract_mode=contract_mode,
            contract_evaluation=contract_evaluation,
            acknowledgments=acknowledgments,
            diagnostic_comparison=diagnostic_comparison,
            require_same_generation=bool(ctx.meta.get(REQUIRE_SAME_GENERATION_META)),
        ),
        project=ProjectContext(config, cfg_path, project_cfg, resolved_cfg, cfg_sha),
        stated_options=stated_options(ctx),
    )
    host = SessionHost(notify=_echo_err, on_close=ctx.call_on_close)
    try:
        with click_input_errors():
            outcome = _session.run_pair_session(session_request, host)
    except _session.PairNotComparable as exc:
        report_not_comparable_and_exit(
            exc.cause,
            exc.old,
            exc.new,
            fmt=fmt,
            output=output,
            secondary_writes=secondary_writes,
        )
    except _session.PairBudgetExceeded as exc:
        _exit_on_budget_overflow(
            exc.cause,
            budget,
            exc.label,
            exc.library,
            exc.old_version,
            exc.new_version,
            fmt=fmt,
            output=output,
            secondary_writes=secondary_writes,
        )
    if outcome.abi3_failure is not None:
        # Said on stderr so it survives `-o json=...` on stdout; the shared
        # classification already recorded ADR-064's exit-7 axis.
        click.echo(f"error: {outcome.abi3_failure}", err=True)
    _report_compare_result(
        outcome.result,
        outcome.old,
        outcome.new,
        resolved_cfg=outcome.resolved_config,
        evaluation_config=outcome.evaluation_config,
        report_severity=outcome.report_severity,
        show_redundant=show_redundant,
        contract_evaluation=contract_evaluation,
        policy=policy,
        pf=outcome.policy_file,
        used_by_apps=used_by_apps,
        required_symbols=required_symbols,
        used_by_old_input=outcome.old_input,
        used_by_new_input=outcome.new_input,
        suppression=outcome.suppression,
        fmt=fmt,
        output=output,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        follow_deps=follow_deps,
        secondary_writes=secondary_writes,
        require_complete_analysis=require_complete_analysis,
        require_complete_analysis_stated=require_complete_analysis_stated,
        use_cases_manifest=use_cases_manifest,
        project_config_path=cfg_path,
        project_config_sha256=cfg_sha,
    )


def _echo_err(message: str) -> None:
    click.echo(message, err=True)
