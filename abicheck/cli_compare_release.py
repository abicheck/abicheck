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

"""CLI — compare-release command and its helpers.

Split out of :mod:`abicheck.cli` to keep that module under the
AI-readiness file-size limit. Imported for side-effect at the bottom
of :mod:`abicheck.cli` so the ``@main.command("compare-release")``
decorator runs.

The per-pair/per-library comparison engine and the matrix-result/output/
gating engine this command calls live in the sibling modules
:mod:`abicheck.cli_compare_release_pairwise` and
:mod:`abicheck.cli_compare_release_matrix`, split out purely to stay under
the AI-readiness 2000-line hard cap -- see either module's own docstring.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .bundle import BundleDiffResult
from .cli import _setup_verbosity, _write_or_echo
from .cli_compare_release_helpers import (  # noqa: F401
    _RELEASE_VERDICT_ORDER,
    _cleanup_temp_dirs,
    _collect_bundle_result,
    _collect_release_warnings,
    _compute_release_severity_exit_code,
    _debian_symbols_warning,
    _discover_include_roots,
    _exit_compare_release,
    _extract_if_package,
    _fold_release_global_severity,
    _format_release_json,
    _format_release_junit,
    _format_release_markdown,
    _format_release_summary,
    _match_release_keys,
    _release_json_scope,
    _release_md_bundle_findings,
    _release_md_changed_libraries,
    _release_md_libraries_table,
    _release_md_matrix_findings,
    _resolve_release_headers,
    _resolve_release_severity_config,
    _run_bundle_analysis,
    apply_release_gate_pack,
    reject_bundle_facts_out_collision,
    reject_bundle_facts_out_dir_collision,
    write_bundle_facts_out,
)
from .cli_compare_release_matrix import (
    _collect_matrix_result,
    _finalize_release_output,
    _prepare_compare_release_inputs,
    _release_finding_dicts as _release_finding_dicts,
    _release_gating_buckets as _release_gating_buckets,
    _strip_diff_results_and_adjust_verdict,
    _validate_suppression_early,
    _write_release_summary_file as _write_release_summary_file,
)
from .cli_compare_release_pairwise import (
    _compare_one_library as _compare_one_library,
    _compare_release_libraries,
    _compare_release_parallel as _compare_release_parallel,
    _compare_release_sequential as _compare_release_sequential,
    _run_compare_pair as _run_compare_pair,
    _suppress_lockstep_soname_findings as _suppress_lockstep_soname_findings,
)
from .cli_options import (
    include_dependencies_option,
    lang_option,
    output_options,
    policy_options,
    release_input_options,
    scope_options,
    severity_options,
    verbose_option,
)
from .frontends.cli.options import (
    reject_incoherent_secondary_output,
    secondary_output_options,
)
from .model import AbiSnapshot
from .pack_application import resolve_bundle_policy_file

if TYPE_CHECKING:
    from .compile_context import CompileContext
    from .pack_application import PackApplication


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
@output_options(
    ["json", "markdown", "junit"],
    output_help="Output file for summary report (default: stdout).",
)
@secondary_output_options(["json", "markdown", "junit"])
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory to write per-library reports.",
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
    "--fail-on-removed-library/--no-fail-on-removed-library",
    "fail_on_removed",
    default=False,
    help="Exit 8 when a library present in old_dir is absent in new_dir.",
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
@click.option(
    "--keep-extracted",
    is_flag=True,
    default=False,
    help="Keep extracted temporary files for debugging.",
)
@verbose_option
@click.option(
    "-j",
    "--jobs",
    "jobs",
    type=int,
    default=0,
    show_default=True,
    help="Number of parallel library comparisons (0 = auto-detect CPU count, "
    "clamped to fit available memory -- see ABICHECK_RELEASE_JOB_MEM_GIB -- "
    "the default). An explicit positive value is never memory-clamped.",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="ABI instantiation manifest (YAML/JSON) listing symbols the "
    "release publicly promises. See ADR-023.",
)
@click.option(
    "--bundle-system-providers",
    "bundle_system_providers",
    default="",
    help="Comma-separated extra sonames to treat as system-provided "
    "(extends the built-in libc/libstdc++/libgcc/libtbb allow-list). "
    "Matched against the real DT_NEEDED soname either exactly or by its "
    "version-suffix-stripped stem (e.g. 'libmkl_core' matches a real "
    "'libmkl_core.so.2' DT_NEEDED entry); matching is case-sensitive.",
)
@click.option(
    "--bundle-cohort",
    "bundle_cohorts",
    multiple=True,
    metavar="PREFIX",
    help="Declare a co-versioned library cohort by name prefix (e.g. "
    "'libfoo_'). Repeatable. Enables the BUNDLE_SONAME_SKEW check, "
    "which flags when some members of the cohort bump their major SONAME "
    "while siblings lag.",
)
@click.option(
    "--no-bundle-analysis",
    "no_bundle_analysis",
    is_flag=True,
    default=False,
    help="Skip bundle-level cross-library analysis (debug/parity escape hatch). "
    "Bundle findings catch intra-bundle symbol removals, signature drift "
    "across DSO boundaries, type drift across siblings, provider "
    "migration, and manifest mismatches.",
)
@click.option(
    "--bundle-facts-out",
    "bundle_facts_out",
    type=click.Path(path_type=Path),
    default=None,
    help="Persist this run's OLD-side bundle facts (per-library snapshots "
    "plus the instantiation manifest, if any) to PATH (G38 Phase 2, "
    "ADR-023 amendment). A later comparison can load this file and pass "
    "it to abicheck.bundle_facts.compare_bundle_from_facts() (Python API; "
    "no CLI consumer yet) to get a bundle-level verdict from this stored "
    "baseline without reopening the old .so files. This is an additive "
    "output alongside the ordinary live-vs-live comparison this "
    "invocation already performs -- it does not change any finding or "
    "exit code. No-op combined with --no-bundle-analysis.",
)
@scope_options  # --scope-public-headers/--no- (ADR-037 D3)
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
    secondary_fmt: str | None,
    secondary_output: Path | None,
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
    keep_extracted: bool,
    verbose: bool,
    jobs: int,
    manifest_path: Path | None,
    bundle_system_providers: str,
    bundle_cohorts: tuple[str, ...],
    no_bundle_analysis: bool,
    bundle_facts_out: Path | None,
    scope_public_headers: bool,
    include_dependencies: bool,
    probe_matrix_old: Path | None,
    probe_matrix_new: Path | None,
    severity_preset: str | None,
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
    release_exit_code_scheme: str | None = None,
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
    # D1: `compare`'s directory/package fan-out forwards the one `--depth`
    # value it can actually honour on this path -- `"binary"` (an explicit
    # assertion that clears header/build/source evidence, matching a
    # single-pair `compare --depth binary`) -- resolved ahead of dispatch by
    # `cli_resolve._reject_depth_for_set_inputs`, which rejects every other
    # rung outright. `None` (the default) is a true no-op, matching every
    # pre-existing caller.
    depth: str | None = None,
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

    from .workflows.extraction import (
        _is_elf_shared_object,
        detect_extractor,
        discover_shared_libraries,
        is_package,
        resolve_package_debug_info as resolve_debug_info,
    )

    _setup_verbosity(verbose)

    # CLI cleanup phase two, PR E: shared with `compare` so it can't drift.
    reject_incoherent_secondary_output(
        dry_run=False,
        output=output,
        secondary_fmt=secondary_fmt,
        secondary_output=secondary_output,
    )
    reject_bundle_facts_out_collision(bundle_facts_out, output, secondary_output)

    # Track temporary directory paths for cleanup
    _temp_dir_paths: list[str] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = tempfile.mkdtemp(prefix=prefix)
        _temp_dir_paths.append(path)
        return Path(path)

    def _do_extract(
        input_path: Path, debug_pkg: Path | None, devel_pkg: Path | None
    ) -> tuple[Path, Path | None, Path | None, Path | None]:
        return _extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            _make_temp_dir,
            is_package,
            detect_extractor,
        )

    # dedup_validate_overrides_warnings(): this whole release run reloads
    # the same --policy-file several times over -- the early strict-
    # suppression validation just below, the per-library fan-out (including
    # its default `--jobs 0` ThreadPoolExecutor parallel path -- see
    # _compare_release_parallel's own docstring for how the dedup scope
    # reaches those worker threads), and (when a probe matrix is given) the
    # matrix-result path all load it independently, so without this a
    # single risky override would log its validate_overrides() warning
    # once per load instead of once for the whole run (Codex review).
    from .workflows.policy_file import dedup_validate_overrides_warnings

    with dedup_validate_overrides_warnings():
        # Validate suppression file early (before per-library loop)
        _validate_suppression_early(
            suppress,
            policy,
            policy_file_path,
            strict_suppressions,
            require_justification,
        )

        try:
            (
                old_debug_dir,
                new_debug_dir,
                old_h,
                new_h,
                old_inc,
                new_inc,
                old_map,
                new_map,
                warning_msgs,
                matched_keys,
                removed_keys,
                added_keys,
            ) = _prepare_compare_release_inputs(
                old_dir,
                new_dir,
                debug_info1,
                debug_info2,
                devel_pkg1,
                devel_pkg2,
                include_private_dso,
                dso_only,
                headers,
                old_headers_only,
                new_headers_only,
                includes,
                old_includes_only,
                new_includes_only,
                config_includes,
                _do_extract,
                discover_shared_libraries,
                is_package,
                _is_elf_shared_object,
            )

            if fmt != "json":
                for msg in warning_msgs:
                    click.echo(msg, err=True)

            reject_bundle_facts_out_dir_collision(bundle_facts_out, output_dir, old_map)
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)

            # CLI cleanup phase two, "PR B" slice 2: fold a selected `kind:
            # gate` pack's gate.exit_code_scheme/gate.severity.<category>
            # into these same raw inputs, once, before every downstream
            # consumer below (this function's own severity_config, the
            # per-library JSON write inside _compare_release_libraries,
            # _compute_release_severity_exit_code,
            # _fold_release_global_severity) reads them — mirroring the
            # `.abicheck.yml`-only `severity_preset = "default"` reassignment
            # a few lines below, which the same downstream consumers already
            # rely on seeing applied exactly once. A no-op without --pack or
            # without a selected gate pack.
            (
                release_exit_code_scheme,
                severity_preset,
                severity_abi_breaking,
                severity_potential_breaking,
                severity_quality_issues,
                severity_addition,
            ) = apply_release_gate_pack(
                pack_application,
                release_exit_code_scheme=release_exit_code_scheme,
                severity_preset=severity_preset,
                severity_abi_breaking=severity_abi_breaking,
                severity_potential_breaking=severity_potential_breaking,
                severity_quality_issues=severity_quality_issues,
                severity_addition=severity_addition,
            )

            # Resolved before the compare pass (its inputs are plain CLI values, no
            # dependency on compare results) so persisted per-library annotations
            # (schema 2.43/2.44, computed inside _compare_release_libraries's
            # primary pass and read by the Action, not the CLI) reflect the same
            # severity-aware gate as the exit code below, instead of the legacy
            # kind-set mapping. Returns None when no severity setting was in
            # effect, or when compare's resolved config pins the legacy scheme
            # for set inputs.
            severity_config = _resolve_release_severity_config(
                severity_preset,
                severity_abi_breaking,
                severity_potential_breaking,
                severity_quality_issues,
                severity_addition,
            )
            if release_exit_code_scheme == "severity" and severity_config is None:
                # The resolved scheme is "severity" (e.g. .abicheck.yml's
                # exit_code_scheme: severity with no severity: block at all) but
                # no severity setting was ever in effect, so the raw-args resolution
                # above returned None. The single-file compare path never hits
                # this: its resolved_cfg.severity is unconditionally populated
                # (defaulting to PRESET_DEFAULT) and only *gated* by scheme, not
                # re-derived from raw flags. Mirror that here — including
                # reassigning severity_preset so the two downstream helpers below
                # that independently re-resolve from these same raw args
                # (_compute_release_severity_exit_code, _fold_release_global_severity)
                # agree with it, instead of also silently resolving None and
                # falling back to the legacy verdict-based exit (Codex review on
                # #549).
                from .workflows.gate import PRESET_DEFAULT

                severity_config = PRESET_DEFAULT
                severity_preset = "default"
            if release_exit_code_scheme == "legacy":
                severity_config = None

            # JUnit, bundle analysis, and annotations all reuse the
            # _diff_result (and, for JUnit, _old_snapshot) stashed in each
            # library entry from this single primary pass -- no independent
            # re-run.
            #
            # This fan-out reloads the same --policy-file once per library;
            # the enclosing dedup_validate_overrides_warnings() scope above
            # is what keeps a single risky override from logging its
            # validate_overrides() warning once per library (Codex review).
            library_results, worst_verdict, diff_pairs = _compare_release_libraries(
                matched_keys,
                old_map,
                new_map,
                old_debug_dir,
                new_debug_dir,
                resolve_debug_info,
                old_h,
                new_h,
                old_inc,
                new_inc,
                old_version,
                new_version,
                lang,
                suppress,
                policy,
                policy_file_path,
                output_dir,
                collect_diff_results=(
                    fmt == "junit"
                    or secondary_fmt == "junit"
                    or bundle_facts_out is not None
                    # G38 Phase 4 (_compare_one_library's docstring):
                    or not no_bundle_analysis
                ),
                # Phase 9: only JUnit/--bundle-facts-out need AbiSnapshot.
                need_full_snapshots=(
                    fmt == "junit"
                    or secondary_fmt == "junit"
                    or bundle_facts_out is not None
                ),
                jobs=jobs,
                scope_to_public_surface=scope_public_headers,
                include_dependencies=include_dependencies,
                severity_config=severity_config,
                contract_evaluation=contract_evaluation,
                contract_mode=contract_mode,
                pack_application=pack_application,
                compile_context=compile_context,
                depth=depth,
            )

            if bundle_facts_out is not None and not no_bundle_analysis:
                # Resolved here, not in the leaf write_bundle_facts_out() (see its docstring).
                #
                # ADR-063 D1's second named exception: this used to call
                # `cli_resolve._resolve_input()` directly -- the same Tier-2
                # resolution `resolve_dump_request`/`execute_dump_request`
                # wrap, but reached independently, with its own hand-rolled
                # `depth=binary` header-clearing special-case (duplicating
                # what `service_compare_evidence`'s shared evidence
                # resolution already does for every matched pair) and no
                # `AnalysisPlan` pre-flight check at all. Migrated onto the
                # same `DumpRequest -> resolve_dump_request ->
                # execute_dump_request` pipeline `dump`/`scan` already
                # converge on: this stranded library is exactly one dump-
                # shaped input, so building a real `DumpRequest` for it and
                # running the shared pipeline both gets it the
                # `AnalysisPlanner.resolve()` pre-flight check ADR-063 Phase 4
                # already provides and drops the manual `is_binary_depth`
                # special-case -- `DumpRequest.depth` feeds the identical
                # evidence-resolution machinery a matched pair's own
                # `CompareRequest` side does, so headers are cleared
                # consistently by the one shared mechanism instead of a
                # second, hand-copied rule that could drift from it.
                #
                # The "deliberately-degrading stranded-library fallback"
                # ADR-063's own text names as the reason this isn't "the same
                # shape of check" as an ordinary comparison is preserved
                # exactly: a `PlanningError`/`ValidationError`/`SnapshotError`
                # from either resolve or execute step still degrades to an
                # ELF-only entry with a warning, not a hard failure -- the
                # migration changes *how* the input resolves, not this
                # function's own degrade-rather-than-abort contract.
                def _resolve_stranded_library(old_path: Path) -> AbiSnapshot:
                    from .api_types import DumpRequest, InputSpec
                    from .service_dump_pipeline import (
                        execute_dump_request,
                        resolve_dump_request,
                    )
                    from .workflows import extraction

                    old_dbg = (
                        resolve_debug_info(old_path, old_debug_dir)
                        if old_debug_dir
                        else None
                    )
                    try:
                        dump_request = DumpRequest(
                            input=InputSpec.of(
                                old_path,
                                headers=old_h,
                                includes=old_inc,
                                version=old_version,
                                pdb=old_dbg,
                                compile=compile_context,
                                include_dependencies=include_dependencies,
                            ),
                            lang=lang,
                            depth=depth,
                        )
                        resolved = resolve_dump_request(dump_request)
                        return execute_dump_request(resolved).snapshot
                    except Exception as exc:
                        # Degrade rather than abort, but warn: lossy entry (Codex review).
                        click.echo(f"{old_path.name}: ELF-only ({exc})", err=True)
                        return AbiSnapshot(
                            library=old_path.name,
                            version="",
                            elf=extraction.parse_elf_metadata(old_path),
                        )

                write_bundle_facts_out(
                    bundle_facts_out,
                    diff_pairs,
                    manifest_path,
                    old_map,
                    resolve_stranded_library=_resolve_stranded_library,
                )

            # ADR-049 Phase 7's orthogonal contract-coverage floor, aggregated
            # across every library with max() -- one library's incomplete
            # evidence must still raise the release's exit code, the same rule
            # contract_coverage_exit.fold_coverage_exit applies to a single pair.
            # `0` (the default fold value) when --contract was never
            # given, or every library's own selected domain closed cleanly.
            contract_coverage_exit_contribution = max(
                (
                    contribution
                    for entry in library_results
                    if isinstance(entry, dict)
                    and isinstance(
                        contribution := entry.get(
                            "contract_coverage_exit_contribution", 0
                        ),
                        int,
                    )
                ),
                default=0,
            )
            # Summed, not max()'d: unlike the exit-code floor above (a 0/1
            # gate, where max is the right fold), this is a plain count of
            # how many failures exist across the whole release -- real even
            # when `contract.unresolved: warn` zeroed every library's own
            # exit contribution. Stays 0 (not omitted) for a run that never
            # passed --contract, same as the count field above.
            contract_coverage_failure_count = sum(
                count
                for entry in library_results
                if isinstance(entry, dict)
                and isinstance(
                    count := entry.get("contract_coverage_failure_count", 0), int
                )
            )

            # Compute the severity-aware exit code while per-library DiffResults
            # are still stashed (before _strip_diff_results_and_adjust_verdict).
            severity_exit_code = (
                None
                if release_exit_code_scheme == "legacy"
                else _compute_release_severity_exit_code(
                    library_results,
                    severity_preset,
                    severity_abi_breaking,
                    severity_potential_breaking,
                    severity_quality_issues,
                    severity_addition,
                )
            )

            bundle_result: BundleDiffResult | None = None
            if not no_bundle_analysis:
                bundle_result, worst_verdict = _collect_bundle_result(
                    library_results,
                    old_map,
                    new_map,
                    worst_verdict,
                    manifest_path=manifest_path,
                    bundle_system_providers=bundle_system_providers,
                    bundle_cohorts=bundle_cohorts,
                    policy=policy,
                    policy_file=resolve_bundle_policy_file(
                        suppress, policy, policy_file_path, pack_application
                    ),
                )

            # Strip _diff_result from entries and bump verdict for removed libraries.
            worst_verdict = _strip_diff_results_and_adjust_verdict(
                library_results,
                removed_keys,
                worst_verdict,
                severity_config,
                needs_annotations=(fmt == "json" or secondary_fmt == "json"),
            )

            # Build-configuration matrix findings (G2: probe -> compare-release).
            # These are release-global, not per-library, so they fold into the
            # worst-of verdict and surface as their own report section.
            matrix_result, worst_verdict = _collect_matrix_result(
                probe_matrix_old,
                probe_matrix_new,
                policy,
                worst_verdict,
                suppress=suppress,
                policy_file_path=policy_file_path,
                old_version=old_version,
                new_version=new_version,
                pack_application=pack_application,
            )

            # Fold release-global bundle/matrix findings into the severity exit so a
            # clean-per-library release with a bundle/matrix break is not masked.
            if severity_exit_code is not None:
                severity_exit_code = _fold_release_global_severity(
                    severity_exit_code,
                    bundle_result,
                    matrix_result,
                    severity_preset,
                    severity_abi_breaking,
                    severity_potential_breaking,
                    severity_quality_issues,
                    severity_addition,
                )

            if secondary_output is not None:
                # CLI cleanup phase two, PR E: --write, now supported for a
                # directory/package (release) compare. Renders the second
                # format from the exact same already-computed
                # library_results/diff_pairs/bundle_result/matrix_result --
                # no second per-library comparison pass, mirroring how
                # single-pair `compare`'s own --write reuses its one already-
                # computed DiffResult (see run_compare's own secondary
                # _write_or_echo call).
                assert secondary_fmt is not None  # guaranteed by Click's callback
                secondary_text = _format_release_summary(
                    secondary_fmt,
                    worst_verdict,
                    old_dir,
                    new_dir,
                    library_results,
                    removed_keys,
                    added_keys,
                    old_map,
                    new_map,
                    warning_msgs,
                    diff_pairs=diff_pairs if secondary_fmt == "junit" else None,
                    bundle_result=bundle_result,
                    matrix_result=matrix_result,
                    severity_config=severity_config,
                    severity_exit_code=severity_exit_code,
                    contract_coverage_exit_contribution=contract_coverage_exit_contribution,
                    contract_coverage_failure_count=contract_coverage_failure_count,
                    fail_on_removed=fail_on_removed,
                    policy=policy,
                    policy_file_path=policy_file_path,
                    suppress=suppress,
                    pack_application=pack_application,
                    scope_public_headers=scope_public_headers,
                )
                _write_or_echo(secondary_output, secondary_text)

            _finalize_release_output(
                fmt,
                worst_verdict,
                old_dir,
                new_dir,
                library_results,
                removed_keys,
                added_keys,
                old_map,
                new_map,
                warning_msgs,
                diff_pairs,
                bundle_result,
                output,
                output_dir,
                fail_on_removed,
                matrix_result=matrix_result,
                severity_exit_code=severity_exit_code,
                severity_config=severity_config,
                contract_coverage_exit_contribution=contract_coverage_exit_contribution,
                contract_coverage_failure_count=contract_coverage_failure_count,
                policy=policy,
                policy_file_path=policy_file_path,
                suppress=suppress,
                pack_application=pack_application,
                scope_public_headers=scope_public_headers,
            )
        finally:
            _cleanup_temp_dirs(_temp_dir_paths, keep_extracted)
