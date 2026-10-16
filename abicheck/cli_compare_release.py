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

"""CLI adapter for the directory/package release compare.

``compare_release_cmd`` is the unregistered engine command ``compare``'s
directory/package dispatch invokes. It parses and validates options,
resolves the release plan (:mod:`abicheck.workflows.release_request`), runs
it through the release compare owner
(:func:`abicheck.workflows.release_compare.execute_release_compare`) with
the CLI's reporter (:mod:`abicheck.frontends.cli.release_member_reporter`),
and renders the outcome. Pairing, member iteration, per-member comparison,
bundle/matrix analysis and every exit-axis fold live in the workflow, not
here.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .cli_compare_release_helpers import (
    _cleanup_temp_dirs,
    reject_bundle_facts_out_collision,
    reject_bundle_facts_out_dir_collision,
)
from .cli_compare_release_matrix import (
    _finalize_release_output,
    _strip_diff_results_and_adjust_verdict,
    _validate_suppression_early,
)
from .cli_options import (
    include_dependencies_option,
    lang_option,
    policy_options,
    release_input_options,
    severity_options,
    verbose_option,
)
from .errors import (
    ReleaseOperandContentError,
    ReleaseOperandUsageError,
    SnapshotError,
)
from .frontends.cli.release_member_reporter import CliReleaseReporter
from .frontends.cli.release_report import render_release_summary
from .frontends.cli.runtime import _setup_verbosity, _write_or_echo
from .model.release_selection import ReleaseSelection
from .report.comparison_scope import comparison_scope_terms
from .report.release_assurance_section import release_assurance_terms
from .report.release_public_surface_section import assemble_release_public_surface
from .workflows.release_compare import (
    ReleaseCompareOptions,
    ReleaseCompareOutcome,
    execute_release_compare,
)

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .environment_matrix import EnvironmentMatrix
    from .pack_application import PackApplication
    from .workflows.release_request import ReleaseComparePlan


#: The formats the release fan-out actually produces.
_ENGINE_FORMATS = ["json", "markdown", "junit", "oneline", "html"]


# NOTE: not registered on `main` — the user-facing `compare-release` command was
# removed (ADR-037 D7 clean removal). This stays a standalone Click command so
# `compare`'s directory/package dispatch can `ctx.invoke` it as the fan-out engine.
@click.command("compare-release")
@click.argument("old_dir", type=click.Path(exists=True, path_type=Path))
@click.argument("new_dir", type=click.Path(exists=True, path_type=Path))
# Per-side header/include/version for the internal (unregistered) release engine;
# the user-facing --header/--include collapse (ADR-040 L1) lives on `compare`.
@release_input_options
@lang_option
# Plan slice 7m: this engine is never registered on `main` and is only ever
# called through `compare`'s own already-parsed export set (see
# `frontends/cli/options/export.expand_export_kwargs`), so it declares its
# three destination parameters as plain internal Click options rather than
# reaching for a user-facing option factory. There is no `--format`/
# `-o`/`--output-dir` surface here to keep in sync with `compare`'s any
# more -- only the three dest names that set carries.
@click.option(
    "--format",
    "fmt",
    type=click.Choice(_ENGINE_FORMATS),
    default="markdown",
    hidden=True,
)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(path_type=Path),
    default=None,
    hidden=True,
)
@click.option("--write", "secondary_writes", multiple=True, hidden=True)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    hidden=True,
)
# Policy + suppression family (ADR-037 D3); strict/justification stay inline.
@policy_options
@click.option(
    "--strict-suppressions",
    is_flag=True,
    default=False,
    help="Fail with exit code 1 if any suppression rule has expired.",
)
@click.option(
    "--require-justification",
    is_flag=True,
    default=False,
    help="Require every suppression rule to have a non-empty 'reason' field.",
)
@click.option(
    "--support-promise",
    "support_promise",
    type=click.Choice(["off", "declared"]),
    default="off",
    show_default=True,
    help="Report a proven change to the release's declared component set as a finding: 'off' (the default) emits nothing; 'declared' emits support_promise_component_retired/_introduced for every member whose absence the *other* side's proven-complete inventory establishes -- a package archive unpacked in full, or a stored snapshot whose capture asserted inventory_complete. Never fires on an unmatched member under an unproven inventory, whatever the setting. (directory/package inputs only)",
)
@click.option(
    "--select",
    "select",
    multiple=True,
    metavar="KEY",
    help="Declare an expected release member by its canonical release-"
    "matching key (e.g. 'libfoo.so', never a raw filename stem). "
    "Repeatable. With any --select/--select-required given, only "
    "declared members are compared; a discovered member not named here is "
    "out of scope. Missing on one side is reported but does not, by "
    "itself, make the scope incomplete (that gate is --select-required).",
)
@click.option(
    "--select-required",
    "select_required",
    multiple=True,
    metavar="KEY",
    help="Like --select, but a missing declared member here contributes to "
    "--on-incomplete-scope's completeness gate. A key "
    "named in both --select and --select-required is required.",
)
@click.option(
    "--debug-info1",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Debug info package for old side (RPM/Deb/tar).",
)
@click.option(
    "--debug-info2",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Debug info package for new side (RPM/Deb/tar).",
)
@click.option(
    "--devel-pkg1",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Development package with headers for old side.",
)
@click.option(
    "--devel-pkg2",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Development package with headers for new side.",
)
@click.option(
    "--dso-only",
    is_flag=True,
    default=False,
    help="Only compare shared objects, skip executables.",
)
@click.option(
    "--include-private-dso",
    is_flag=True,
    default=False,
    help="Include private (non-public) shared objects from non-standard paths.",
)
@verbose_option
@click.option(
    "--instantiation-manifest",
    "manifest_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="ABI instantiation manifest (YAML/JSON) listing symbols the "
    "release publicly promises. Renamed from --manifest. Inert here, "
    "kept in sync with "
    "the real, user-facing spelling on frontends/cli/options/release.py "
    "purely for reader consistency.",
)
@click.option(
    "--bundle-facts-out",
    "bundle_facts_out",
    type=click.Path(path_type=Path),
    default=None,
    help="Persist this run's OLD-side bundle facts (per-library snapshots "
    "plus the instantiation manifest, if any) to PATH. "
    "A later comparison can load this file and pass "
    "it to abicheck.model.bundle_facts.compare_bundle_from_facts() (Python API; "
    "no CLI consumer yet) to get a bundle-level verdict from this stored "
    "baseline without reopening the old .so files. This is an additive "
    "output alongside the ordinary live-vs-live comparison this "
    "invocation already performs -- it does not change any finding or "
    "exit code.",
)
@include_dependencies_option
@click.option(
    "--probe-matrix-old",
    "probe_matrix_old",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Old build-configuration matrix snapshot. "
    "When given with --probe-matrix-new, build-config findings "
    "(CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV, "
    "BEHAVIOURAL_DEFAULT_CHANGED) are folded into this release's "
    "verdict and report (G2: probe -> compare-release).",
)
@click.option(
    "--probe-matrix-new",
    "probe_matrix_new",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="New build-configuration matrix snapshot (pairs with --probe-matrix-old).",
)
@click.option(
    "--old-variant",
    "old_variant",
    default=None,
    metavar="VARIANT_ID",
    help="Which build variant to compare when OLD_DIR is a stored "
    "ProjectSnapshot package declaring more than one. "
    "Defaults to the package's only variant when it declares exactly one; "
    "a usage error otherwise. No-op for a live directory/archive operand.",
)
@click.option(
    "--new-variant",
    "new_variant",
    default=None,
    metavar="VARIANT_ID",
    help="The --old-variant counterpart for NEW_DIR.",
)
# ── Severity (shared family, ADR-037 D3; mirrors `compare`) ───────────────────
@severity_options
def compare_release_cmd(
    old_dir: Path,
    new_dir: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    lang: str,
    fmt: str,
    output: Path | None,
    secondary_writes: tuple[tuple[str, Path], ...],
    output_dir: Path | None,
    suppress: Path | None,
    strict_suppressions: bool,
    require_justification: bool,
    policy: str,
    policy_file_path: Path | None,
    fail_on_removed: bool,
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    dso_only: bool,
    include_private_dso: bool,
    verbose: bool,
    manifest_path: Path | None,
    bundle_system_providers: tuple[str, ...],
    bundle_cohorts: tuple[str, ...],
    bundle_facts_out: Path | None,
    include_dependencies: bool,
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    severity_preset: str | None,
    # One-comparison-product Phase 9b: --scope-public-headers/--no- are no
    # longer CLI spellings, so this is a plain keyword parameter like
    # `on_incomplete_scope` below. `compare`'s fan-out passes the resolved
    # value (.abicheck.yml's scope.public over the built-in default); an
    # explicit `--contract all`/`exports` domain turns header-origin demotion
    # off inside checker.compare itself (Phase 9a), not through this value.
    scope_public_headers: bool = True,
    on_incomplete_scope: str = "warn",
    # ADR-071: `assurance.require_complete`, project-wide (never per-library,
    # D4) -- a plain keyword parameter for the same reason
    # `on_incomplete_scope` above is one: the setting is config-only, so the
    # fan-out states it here without re-exposing a user-facing flag on this
    # unregistered internal command.
    require_complete_analysis: bool = False,
    support_promise: str = "off",
    select: tuple[str, ...] = (),
    select_required: tuple[str, ...] = (),
    # Not Click options: `compare`'s directory/package fan-out `ctx.invoke`s
    # this engine with the *already-merged* per-category severity levels it
    # resolved from `.abicheck.yml` (the four `--severity-<category>` CLI
    # flags were removed -- see `cli_options.severity_options`). Plain
    # keyword parameters, so the fan-out can still state them without
    # re-exposing a user-facing flag on an unregistered internal command.
    severity_abi_breaking: str | None = None,
    severity_potential_breaking: str | None = None,
    severity_quality_issues: str | None = None,
    severity_addition: str | None = None,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    # CLI cleanup phase two, PR B slice 1: `compare`'s directory/package
    # fan-out resolves any `--pack` once, ahead of dispatch
    # (resolve_release_pack_application), and hands over the pack's
    # policy/contract-surface contribution here -- same internal-parameter
    # shape as severity_abi_breaking et al. above. `None` (the default) is a
    # true no-op: every library is compared exactly as it was before this
    # parameter existed.
    pack_application: PackApplication | None = None,
    # `compare`'s directory/package fan-out resolves the both-sides L2
    # header-AST compile context once, ahead of dispatch (the same
    # `cli_options.resolve_compile_context` call the single-pair path uses),
    # and hands it over here -- same internal-parameter shape as
    # pack_application above. `None` (the default) is a true no-op.
    compile_context: CompileContext | None = None,
    # The dirs the project's `.abicheck.yml` `compile.include_dirs` appended
    # past the caller's raw `-I` roots (the suffix of `resolve_compile_
    # context`'s merged-includes return past its own `includes` input) --
    # forwarded separately from `includes` itself so they survive a per-
    # library-pair `--old-include`/`--new-include` override, which
    # otherwise fully replaces `includes` for that side
    # (`_prepare_compare_release_inputs`). `()` (the default) is a true
    # no-op.
    config_includes: tuple[Path, ...] = (),
    # ADR-062 A1.7: which `VariantRef` to resolve on each side, when that
    # side is a stored `ProjectSnapshot` package directory declaring more
    # than one -- a true no-op (`None`) for every live directory/archive
    # operand and for a package declaring exactly one variant.
    old_variant: str | None = None,
    new_variant: str | None = None,
    # `compare`'s directory/package fan-out forwards the run's `--depth` pin
    # verbatim -- any rung of the public ladder, resolved ahead of dispatch
    # by `cli_compare_options._resolve_depth_for_set_inputs`. That function
    # used to reject everything but `"binary"`; it no longer rejects
    # anything, because each member pair is floor-checked
    # (`enforce_requested_depth`) and depth-projected
    # (`project_pair_to_depth`) by the same `service.run_compare` a
    # single-pair compare uses, so a member short of the requested rung
    # fails as that member's own `ERROR` result rather than as one whole-run
    # usage error. `None` (the default) is a true no-op, matching every
    # pre-existing caller.
    depth: str | None = None,
    # `compare`'s directory/package fan-out forwards --view's derived
    # show_only here; `report_mode` is not accepted, since
    # `_dispatch_release_compare` already rejects every value but "full"
    # before this is ever called, so there is nothing left for this engine
    # to branch on. `None` (the default) is a true no-op, matching every
    # pre-existing caller (this command has no `--view`/`--show-only` Click
    # option of its own). Demangling is not threaded at all any more (plan
    # slice 7o): this command's body resolves it from each destination's
    # own format, which is now the only input it has.
    show_only: str | None = None,
    # Codex review (PR #1154 follow-up): "Reject unsupported impact views
    # instead of silently dropping them" -- `compare --view impact`'s
    # aggregate counterpart. Unlike `report_mode`'s "leaf"/"root-cause"
    # (rejected outright above `_dispatch_release_compare`), the impact
    # summary is naturally per-library (each library's own DiffResult
    # already yields its own table), so it is threaded through rather than
    # dropped or rejected -- see `_strip_diff_results_and_adjust_verdict`'s
    # own docstring for how each library's table is computed. `False` (the
    # default) is a true no-op, matching every pre-existing caller (this
    # command has no `--view` option of its own; only `compare`'s
    # directory/package dispatch supplies a non-default value).
    show_impact: bool = False,
    # CodeRabbit review, PR #1138: a project's `.abicheck.yml`
    # `scope.public_header_dirs` (`workflows.public_header_boundary.
    # project_config_public_header_dirs`), resolved once by the caller
    # (`cli_compare_helpers.run_compare`, the same place that already
    # resolves `project_cfg` for the single-pair path) and forwarded here --
    # same internal-parameter shape as `compile_context`/`pack_application`
    # above. `None` (the default) is a true no-op: every library is compared
    # exactly as it was before this parameter existed.
    public_header_dirs: list[Path] | None = None,
    # Codex review, fresh evidence: resolved once by the caller
    # (`cli_compare_helpers.run_compare`'s already-extracted
    # `resolved_cfg.collapse_versioned_symbols`, the same place that
    # already resolves `project_cfg` for the single-pair path) and
    # forwarded here -- same internal-parameter shape as
    # `compile_context`/`public_header_dirs` above. `False` (the default)
    # is a true no-op: every library is compared exactly as it was before
    # this parameter existed.
    collapse_versioned_symbols: bool = False,
    # ADR-068 §3 #23 / ADR-049 D7 (second review round): `compare`'s
    # directory/package fan-out resolves `.abicheck.yml`'s `policy.overrides`
    # once, ahead of dispatch (the same place it already resolves
    # `project_cfg`/`pack_application` for this fan-out -- see
    # `resolve_project_config_policy_overrides`), and hands the resolved
    # `ChangeKind -> Verdict` mapping over here -- same internal-parameter
    # shape as `pack_application`/`compile_context` above. `None` (the
    # default) is a true no-op: every library is compared exactly as it was
    # before this parameter existed.
    project_policy_overrides: dict[Any, Any] | None = None,
    acknowledgments_path: Path | None = None,
    acknowledgment_unacknowledged_additions: str | None = None,
    # ADR-020b / ADR-068 D5: resolved once by the caller
    # (`cli_compare_helpers.run_compare`'s `resolved_cfg.deployment`, the
    # same place `collapse_versioned_symbols`/`public_header_dirs` above are
    # resolved) and forwarded here -- same internal-parameter shape as those
    # two. `None` (the default) is a true no-op: every library is compared
    # exactly as it was before this parameter existed. The former
    # `--env-matrix FILE` used to be rejected outright for a directory/
    # package compare; now that it is a project-wide config key
    # (`.abicheck.yml`'s `deployment:` block) it applies to every library in
    # the fan-out, the same way `gate.fail_on_removed_library`/`release.*`
    # already do.
    env_matrix: EnvironmentMatrix | None = None,
    # The run's `--exclude-header` patterns, resolved once by the caller
    # (`cli_compare_helpers.run_compare`) and forwarded here -- same
    # internal-parameter shape as `compile_context`/`public_header_dirs`
    # above, and the canonical rule set every member is narrowed by. It
    # applies to matched pairs and to one-sided/stranded snapshot capture
    # alike, so a header a release tree cannot parse alongside its siblings
    # is excluded everywhere this fan-out reads headers, not just where a
    # scalar `compare` does. `()` (the default) is a true no-op: every
    # library is compared exactly as it was before this parameter existed.
    exclude_headers: tuple[str, ...] = (),
    # Whether `lang` was stated (`compile.lang`), as `run_compare` resolved
    # it: the release surface forces a stated language and auto-detects an
    # unstated default, the same rule a scalar `compare` applies.
    lang_explicit: bool = False,
) -> None:
    """Compare all libraries in two release directories or packages.

    OLD_DIR and NEW_DIR may each be a file, directory, or package
    (RPM, Deb, tar, conda, wheel). Package format is auto-detected.
    When directories are given, libraries are matched by filename stem.

    \b
    Exit codes (verdict-based, the default):
      0  All libraries: NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK
      2  At least one library: API_BREAK
      4  At least one library: BREAKING
      8  Library removed (only when --fail-on-removed-library)

    \b
    With a severity setting in effect, exit codes follow the severity-aware scheme
    aggregated across all libraries (and bundle/matrix findings):
      0  no error-level findings
      1  error in quality/addition categories only
      2  error in potential_breaking
      4  error in abi_breaking
    A removed library (--fail-on-removed-library) still exits 8, and a per-library
    comparison ERROR still floors the exit at 4, regardless of severity settings.

    \b
    Under --contract, each library's own ADR-049 Phase 7 contract-
    coverage floor (0/1) folds into the release exit code with max() -- the
    same orthogonal axis a single-pair `compare` applies: it can raise a
    clean 0 to 1 but never lowers a real 2/4/8.

    \b
    Examples:
      abicheck compare-release release-1.0/ release-2.0/ -H include/
      abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm
      abicheck compare-release libfoo_1.0.deb libfoo_1.1.deb
      abicheck compare-release sdk-2.0.tar.gz sdk-2.1.tar.gz
      abicheck compare-release pkg-v1.conda pkg-v2.conda
      abicheck compare-release old.whl new.whl
      abicheck compare-release libfoo-1.0.rpm libfoo-1.1.rpm \\
          --debug-info1 libfoo-debuginfo-1.0.rpm \\
          --debug-info2 libfoo-debuginfo-1.1.rpm
    """

    # ADR-062 A1.7: --old-variant/--new-variant are declared on `compare`
    # itself (`cli_options.variant_options`, `expose_value=False` + a
    # `ctx.meta` stash) rather than on this unregistered internal command,
    # so a caller reaching this function via `.callback(**kwargs)` -- every
    # real caller -- never has them in `kwargs`; recovered here from the
    # still-current Click context instead.
    if old_variant is None and new_variant is None:
        from .cli_options import variant_kwargs_from_context

        ctx = click.get_current_context(silent=True)
        if ctx is not None:
            variant_kwargs = variant_kwargs_from_context(ctx)
            old_variant = variant_kwargs["old_variant"]
            new_variant = variant_kwargs["new_variant"]

    _setup_verbosity(verbose)

    # ADR-065 S1: an explicit, identity-keyed release member selection.
    # `None` (no --select/--select-required) compares every discovered member.
    release_selection: ReleaseSelection | None = None
    if select or select_required:
        try:
            release_selection = ReleaseSelection.from_lists(
                required=select_required, optional=select
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc

    # Every export destination came from one already-validated export set
    # (`frontends.cli.options.export`); `--bundle-facts-out` is not part of
    # it, so its own collision check stays.
    reject_bundle_facts_out_collision(
        bundle_facts_out, output, *(path for _, path in secondary_writes)
    )
    secondary_formats = {f for f, _ in secondary_writes}

    _temp_dir_paths: list[str] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = tempfile.mkdtemp(prefix=prefix)
        _temp_dir_paths.append(path)
        return Path(path)

    # One warning-dedup scope for the whole run (early validation, every
    # member, the release-level stages): a risky override warns once.
    from .workflows.policy_file import dedup_validate_overrides_warnings

    with dedup_validate_overrides_warnings():
        try:
            _validate_suppression_early(
                suppress,
                policy,
                policy_file_path,
                strict_suppressions,
                require_justification,
            )
            # The plan (`workflows.release_request`), through the CLI boundary
            # translating its typed errors; imported here so a test can patch
            # that boundary's own binding.
            from .frontends.cli.release_compare_request import (
                ReleaseCompareRequest,
                resolve_release_compare_plan,
            )

            request = ReleaseCompareRequest(
                old_dir=old_dir,
                new_dir=new_dir,
                debug_info1=debug_info1,
                debug_info2=debug_info2,
                devel_pkg1=devel_pkg1,
                devel_pkg2=devel_pkg2,
                include_private_dso=include_private_dso,
                dso_only=dso_only,
                headers=headers,
                old_headers_only=old_headers_only,
                new_headers_only=new_headers_only,
                includes=includes,
                old_includes_only=old_includes_only,
                new_includes_only=new_includes_only,
                config_includes=config_includes,
                old_variant=old_variant,
                new_variant=new_variant,
                release_selection=release_selection,
                pack_application=pack_application,
                severity_preset=severity_preset,
                severity_abi_breaking=severity_abi_breaking,
                severity_potential_breaking=severity_potential_breaking,
                severity_quality_issues=severity_quality_issues,
                severity_addition=severity_addition,
                on_incomplete_scope=on_incomplete_scope,
                fail_on_removed_library=fail_on_removed,
            )
            try:
                plan = resolve_release_compare_plan(
                    request, make_temp_dir=_make_temp_dir
                )
            except SnapshotError as exc:  # a damaged composition/marker (fail closed)
                raise click.UsageError(str(exc)) from exc

            if fmt != "json":
                for msg in plan.warnings:
                    click.echo(msg, err=True)
            reject_bundle_facts_out_dir_collision(
                bundle_facts_out, output_dir, dict(plan.scope.old_map)
            )
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)

            severity_config = plan.gate.severity
            outcome = _execute_release_compare(
                plan,
                ReleaseCompareOptions(
                    old_version=old_version,
                    new_version=new_version,
                    lang=lang,
                    lang_explicit=lang_explicit,
                    suppress=suppress,
                    policy=policy,
                    policy_file_path=policy_file_path,
                    scope_public_headers=scope_public_headers,
                    include_dependencies=include_dependencies,
                    contract_evaluation=contract_evaluation,
                    contract_mode=contract_mode,
                    require_complete_analysis=require_complete_analysis,
                    pack_application=pack_application,
                    compile_context=compile_context,
                    depth=depth,
                    public_header_dirs=public_header_dirs,
                    collapse_versioned_symbols=collapse_versioned_symbols,
                    project_policy_overrides=project_policy_overrides,
                    env_matrix=env_matrix,
                    exclude_headers=exclude_headers,
                    acknowledgments_path=acknowledgments_path,
                    acknowledgment_unacknowledged_additions=acknowledgment_unacknowledged_additions,
                    support_promise=support_promise,
                    manifest_path=manifest_path,
                    bundle_system_providers=bundle_system_providers,
                    bundle_cohorts=bundle_cohorts,
                    probe_matrix_old=probe_matrix_old,
                    probe_matrix_new=probe_matrix_new,
                    bundle_facts_out=bundle_facts_out,
                    junit_inventory=(fmt == "junit" or "junit" in secondary_formats),
                ),
                reporter=CliReleaseReporter(
                    severity_config=severity_config,
                    require_complete_analysis=require_complete_analysis,
                    contract_evaluation=contract_evaluation,
                    output_dir=output_dir,
                    echo_warnings=fmt != "json",
                ),
            )
            _render_release_outcome(
                outcome,
                fmt=fmt,
                output=output,
                output_dir=output_dir,
                secondary_writes=secondary_writes,
                fail_on_removed=fail_on_removed,
                policy=policy,
                policy_file_path=policy_file_path,
                suppress=suppress,
                pack_application=pack_application,
                scope_public_headers=scope_public_headers,
                show_only=show_only,
                show_impact=show_impact,
                require_complete_analysis=require_complete_analysis,
                old_dir=old_dir,
                new_dir=new_dir,
            )
        finally:
            _cleanup_temp_dirs(_temp_dir_paths)


def _execute_release_compare(
    plan: ReleaseComparePlan,
    options: ReleaseCompareOptions,
    *,
    reporter: CliReleaseReporter,
) -> ReleaseCompareOutcome:
    """:func:`abicheck.workflows.release_compare.execute_release_compare`,
    with its typed operand errors (a bad ``--manifest``, an undecodable
    embedded manifest, an unwritable ``--bundle-facts-out``, a one-sided
    probe matrix) turned into the ``click`` types -- and so the exit codes
    and messages -- this command always produced."""
    try:
        return execute_release_compare(plan, options, reporter=reporter)
    except ReleaseOperandUsageError as exc:
        raise click.UsageError(str(exc)) from exc
    except ReleaseOperandContentError as exc:
        raise click.ClickException(str(exc)) from exc


def _render_release_outcome(
    outcome: ReleaseCompareOutcome,
    *,
    fmt: str,
    output: Path | None,
    output_dir: Path | None,
    secondary_writes: tuple[tuple[str, Path], ...],
    fail_on_removed: bool,
    policy: str,
    policy_file_path: Path | None,
    suppress: Path | None,
    pack_application: PackApplication | None,
    scope_public_headers: bool,
    show_only: str | None,
    show_impact: bool,
    require_complete_analysis: bool,
    old_dir: Path,
    new_dir: Path,
) -> None:
    """Render *outcome*: the display projection and strip of every member
    entry, the release public-surface section, each ``-o`` export, then the
    primary format, its sidecars and the exit."""
    from .frontends.render import resolve_demangle_for_format

    secondary_formats = {f for f, _ in secondary_writes}
    library_results = outcome.library_results
    severity_config = outcome.severity_config
    scope_terms = comparison_scope_terms(outcome.scope_decision)
    assurance_terms = release_assurance_terms(outcome.assurance_decision)
    # The last point each member's live DiffResult exists: project the
    # findings every renderer reads, then strip every private key. The
    # verdict already carries the proven-removed floor, so this is a no-op
    # on it.
    worst_verdict = _strip_diff_results_and_adjust_verdict(
        library_results,
        outcome.removed_keys,
        outcome.worst_verdict,
        severity_config,
        needs_annotations=(fmt == "json" or "json" in secondary_formats),
        show_only=show_only,
        show_impact=show_impact,
    )
    # "One product fact, reported once": fold findings several members
    # reported identically out of the per-library tables and assemble the
    # release public-surface section every format reads.
    public_surface = assemble_release_public_surface(
        outcome.surface_stage,
        library_results,
        type_attribution=outcome.type_attribution,
    )
    common: dict[str, Any] = {
        "bundle_result": outcome.bundle_result,
        "matrix_result": outcome.matrix_result,
        "severity_config": severity_config,
        "severity_exit_code": outcome.severity_exit_code,
        "contract_coverage_exit_contribution": outcome.contract_coverage_exit_contribution,
        "contract_coverage_failure_count": outcome.contract_coverage_failure_count,
        "policy": policy,
        "policy_file_path": policy_file_path,
        "suppress": suppress,
        "pack_application": pack_application,
        "scope_public_headers": scope_public_headers,
        "scope_terms": scope_terms,
        "assurance_terms": assurance_terms,
        "show_only": show_only,
        "env_matrix_source_sha256": outcome.env_matrix_source_sha256,
        "require_complete_analysis": require_complete_analysis,
        "excluded_header_patterns": outcome.excluded_header_patterns,
        "public_surface": public_surface,
    }
    for secondary_fmt, secondary_output in secondary_writes:
        # `-o` is repeatable: every artifact renders the same computed
        # outcome -- one analysis, several artifacts -- and a display
        # selector means the same thing for every export, as in a
        # single-pair `compare`.
        secondary_text = render_release_summary(
            secondary_fmt,
            worst_verdict,
            old_dir,
            new_dir,
            library_results,
            outcome.removed_keys,
            outcome.added_keys,
            outcome.old_map,
            outcome.new_map,
            outcome.warnings,
            diff_pairs=outcome.junit_pairs if secondary_fmt == "junit" else None,
            fail_on_removed=fail_on_removed,
            demangle=resolve_demangle_for_format(secondary_fmt),
            **common,
        )
        _write_or_echo(secondary_output, secondary_text)

    _finalize_release_output(
        fmt,
        worst_verdict,
        old_dir,
        new_dir,
        library_results,
        outcome.removed_keys,
        outcome.added_keys,
        outcome.old_map,
        outcome.new_map,
        outcome.warnings,
        outcome.junit_pairs,
        outcome.bundle_result,
        output,
        output_dir,
        fail_on_removed,
        demangle=resolve_demangle_for_format(fmt),
        **{k: v for k, v in common.items() if k != "bundle_result"},
    )
