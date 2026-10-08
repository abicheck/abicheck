# Copyright 2026 Nikolay Petrov
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

"""``abicheck compare`` -- command input translation (ADR-061 Phase 4 item 1).

Covers the single-pair comparison, the directory/package release fan-out it
dispatches to, and the inline build-source embedding a live-binary operand
needs before the pair can be resolved.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ....cli_helpers_compare import (  # noqa: F401  — re-exported to keep cli import sites stable
    _canonical_library_key as _canonical_library_key,
    _collect_force_public_symbols as _collect_force_public_symbols,
    _merge_redundant_changes as _merge_redundant_changes,
    _provenance_timestamp as _provenance_timestamp,
    _version_sort_key as _version_sort_key,
    _warn_ignored_flags as _warn_ignored_flags,
)
from ....cli_options import (
    LANG_DEFAULT,
    abi3_option,
    app_usage_scope_options,
    bundle_facts_manifest_options,
    changed_path_options,
    contract_options,
    debug_resolution_options,
    define_option,
    evidence_options,
    export_options,
    include_dependencies_option,
    normalize_sided_options,
    pack_option,
    policy_options,
    reject_bundle_facts_manifest_without_old_bundle_facts,
    release_options,
    set_input_options,
    severity_options,
    two_sided_input_options,
    verbose_option,
)
from ....frontends.cli import help as cli_help
from ..dump_debug_config import resolve_stored_bundle_lang
from ..options.evidence_roles import reject_unsupported_detached_debug
from ..options.operand_path import CompareOperandPath
from ..options.params import (
    _load_suppression_and_policy as _load_suppression_and_policy,  # noqa: F401  — re-exported to keep cli import sites (test suite) stable
)

if TYPE_CHECKING:
    pass


from ....cli import main
from ..runtime import (
    _validate_view,
)

#: `oneline` and `html` render the aggregate release document alone
#: (`report/release_oneline.py`, `report/render_release_html.py`); the two still
#: missing need a per-member `DiffResult` -- see `docs/contribute/known-gaps.md`.
from .compare_routing import _RELEASE_FORMATS


@main.command("compare")
@cli_help.compare_help_options  # curated --help + full --help-all (G21.8 collapse M2)
@click.argument("old_input", type=CompareOperandPath())
@click.argument("new_input", type=CompareOperandPath(), required=False)
@click.option(
    "--no-baseline",
    "no_baseline",
    is_flag=True,
    default=False,
    help="Declare that no prior surface exists for this candidate -- an "
    "audit, not a comparison. Takes exactly one operand (the "
    "candidate build) instead of OLD NEW; a directory/package of libraries "
    "is audited per member (--select/--select-required apply) into one "
    "report. The OLD side is recorded with "
    "the 'declared_absent' acquisition state. Replaces `scan`'s "
    "audit-only mode (no --against): reports candidate-side facts only -- "
    "never an addition, a removal, or a compatibility verdict. "
    "--severity-preset is the sole switch that arms this audit's own gate: "
    "any preset other than 'info-only' contributes exit 3 the first time a "
    "finding is BREAKING/API_BREAK-classified; "
    "omit it, or pass 'info-only', to opt out.",
)
# Set-input fan-out (ADR-037 D7): --dso-only, --output-dir only bite
# when the operands are directories/packages; a no-op-with-warning otherwise.
@set_input_options
# ── Release (directory/package) comparison knobs (ADR-037 D7) ────────────────
@release_options
# ── Stored bundle-facts OLD side (G38 Phase 13 follow-up) ────────────────────
# OLD_INPUT is automatically classified as a persisted BundleFacts document
# (produced by a prior `compare --bundle-facts-out`) rather than a live
# directory/package -- CLI cleanup phase two, PR I: the former
# `--old-bundle-facts` flag is gone; see
# workflows/bundle_compare_operand.py for the classifier and
# compare_bundle_facts.py for the dispatch it still routes to.
#
# Phase 7g (one-comparison-product.md §4.1/§3 #21): --max-json-object-nodes
# is gone from here too -- resource_limits.max_bundle_facts_decode_nodes in
# .abicheck.yml is its only source now (compare_bundle_facts.py's dispatch()
# reads it off the same BuildConfig already loaded there for
# bundle_system_providers/cohorts), calibrated against a real oneDAL-scale
# corpus (see bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES's own docstring).
@bundle_facts_manifest_options  # G38 Phase 17
# ── Dump options (used when input is an ELF binary) ──────────────────────────
# Two-sided header/include/version family (ADR-037 D3). Phase 7 (ADR-037
# D8.1): --ast-frontend/--compiler*/--sysroot/--nostdinc/--frontend-context/
# --lang are gone from `compare`'s CLI (compile: config only); `scan` keeps
# the unreduced compile-context decorator unchanged.
@two_sided_input_options
# ── Compare options (unchanged) ──────────────────────────────────────────────
@export_options(
    ["json", "markdown", "sarif", "html", "junit", "review", "terminal", "oneline"],
    default_format="terminal",
    supports_directory=True,
    directory_formats=["json"],
    help_extra=" 'review' emits a compact GitHub-facing digest (verdict + "
    "counts + release recommendation + manual-review banner) "
    "suitable for a job summary or PR comment; 'oneline' emits a "
    "single human-readable summary line -- the 'just tell me' "
    "flow. A directory/package (release) comparison renders "
    "json/markdown/junit/oneline/html only. Every export is rendered "
    "from the one completed comparison -- asking for more "
    "artifacts never re-runs the analysis and never changes the "
    "verdict or the exit code.",
)
@click.option(
    "--view",
    "view",
    multiple=True,
    callback=_validate_view,
    expose_value=True,
    metavar="TOKEN",
    help="Repeatable rendering selector: never changes the "
    "verdict, findings, or exit code. TOKEN: "
    "'full' (default)/'impact'/'root-cause' (report mode); "
    "'show=<tokens>' (display filter over severity "
    "[breaking/api-break/risk/compatible], element "
    "[functions/variables/types/enums/elf/build/source/analysis] and "
    "action [added/removed/changed/unchanged] -- AND across dimensions, OR within "
    "one, repeatable to OR whole groups together). Example: --view root-cause "
    "--view show=breaking,functions. Disclosure is not a token: the "
    "pattern-modulation ledger, the scope/reconciliation ledger and the "
    "--suppress audit are always reported, and C++ symbols are "
    "always demangled in human output with the exact mangled name kept "
    "beside them.",
)
# Policy + suppression family (ADR-037 D3). The strict/justification pair
# lives only in .abicheck.yml's suppression: block now (ADR-037 D4).
@policy_options
# Phase 7 (SS4.1's CONFIG row): --pdb-path is gone from `compare` too --
# `debug.pdb_path` is its only spelling, `dump`'s own key since Phase 7c
# (ADR-037 D8.1). A side needing its own PDB names the directory holding it
# with `--debug-root old=`/`new=`, which the resolver already searches.
# ── Scoped comparison (ADR-043): app-usage and required-symbol contracts ─────
@app_usage_scope_options
# Severity preset + per-category overrides (ADR-037 D3 / D4).
@severity_options
# ── Project config (ADR-037 D4) ────────────────────────────────────────────
# No manual --exit-code-scheme selector any more (CLI cleanup phase two PR
# G2, ADR-064): the one automatic gate algorithm is fully determined by
# whether a severity setting is in effect anywhere (--severity-preset,
# .abicheck.yml's severity: block, or a kind: gate pack's
# gate.severity.<category>) -- no gate/severity policy configured means the
# compatibility verdict decides 0/2/4; one in effect means the resolved
# GateDecision decides 0/1/2/4.
@click.option(
    "--config",
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to the project .abicheck.yml. Default: the "
    "nearest .abicheck.yml found from the current directory upward. "
    "Supplies stable project settings (severity map, scope/FP "
    "tuning, suppression policy); CLI flags override it.",
)
@click.option(
    "--follow-deps",
    is_flag=True,
    default=False,
    help="Resolve transitive dependencies for both old and new, compute symbol "
    "bindings, and include a dependency-change section in the report. ELF only.",
)
@include_dependencies_option
@define_option
@click.option(
    "--search-path",
    "search_paths",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Additional directory to search for shared libraries (with --follow-deps).",
)
@click.option(
    "--ld-library-path",
    "ld_library_path",
    default="",
    help="Simulated LD_LIBRARY_PATH (with --follow-deps).",
)
# One-comparison-product Phase 9b: --scope-public-headers/--no- are gone.
# Header-origin scoping stays on for a run with no --contract (built-in
# default, or .abicheck.yml's scope.public); `--contract all` is the
# measured replacement for --no-scope-public-headers (Phase 9a).
# ADR-068 D4 / Phase 5: --show-filtered is gone; the ledger it echoed has
# been unconditional since ADR-067 S1, so `--view filtered` is its spelling.
# One-comparison-product Phase 9d: --post-manifest is gone. A POST manifest
# is a stable project property: .abicheck.yml's contract.overlays.
# post_manifest (Phase 9c, frontends/cli/contract_overlays.py) is its only
# spelling.
# one-comparison-product.md Phase 7n: --probe-matrix is gone. A probe-matrix
# snapshot is build evidence, so it is one of --build-info's operands now,
# recognised from the document's own schema/required-key contract rather
# than a filename -- probe observations and compile context stay distinct
# internally and may be supplied together for one side.
# ── Debug artifact resolution (ADR-021a + ADR-037 D3) ─────────────────────────
# --debug-info: the whole separate-debug-info role (Phase 7n merged
# --debug-root into it -- directory, detached file, or debug package). The
# dwarf-only/debuginfod[-url]/debug-format hidden flags are gone (ADR-068 D5,
# Phase 7a) -- debug.* .abicheck.yml keys are their only spelling now.
@debug_resolution_options
@evidence_options  # --depth, --sources, --build-info
@changed_path_options  # ADR-068 Phase 2c: --since/--changed-path (scoping only)
@abi3_option  # ADR-068 Phase 2d: --abi3 candidate-side stable-ABI audit
# ADR-068 D4 / Phase 5: --surface-metrics is gone -- ADR-027's metric-drift
# findings are computed on every comparison and merged into result.changes,
# so nothing was left for the flag to select, not even a rendering choice.
# ADR-068 D5: --env-matrix is gone -- declared deployment constraints are
# now `.abicheck.yml`'s `deployment:` config key (runtime_floors contract).
# §4.1's AUTO row: ADR-039 build-context reconciliation is unconditional now
# and `--reconcile-build-context` is gone. It is strictly evidence-gated and
# can only ever move a phantom finding out of the verdict, never manufacture
# one, so an opt-in switch could only mean "leave a known false positive in
# because you forgot a flag". Forced on at the Tier-2 chokepoint
# (workflows/compare_policy.compare_snapshots): CLI, API and Action alike.
@click.option(
    "--budget",
    "budget",
    default=None,
    help="A wall-clock guard on this run's deadline-aware "
    "stages, so a CI job fails clearly (exit 5) instead of running "
    "unbounded. A duration like 15m/900s/1h; unset means no budget.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Resolve and validate the invocation -- classify inputs, resolve "
    "depth/scope, show tool/config resolution -- and print a report "
    "without running the diff. Writes nothing; incompatible with "
    "-o/--output.",
)
@click.option(
    "--diagnostic-comparison",
    "diagnostic_comparison",
    is_flag=True,
    default=False,
    help="A diagnostic escape hatch: when OLD and NEW were "
    "extracted under a genuinely incomparable profile/scope "
    "(ExtractionContract mismatch), downgrade the default hard "
    "failure (exit 16, no verdict) into a tentative diff instead, "
    'stamped assurance: "none" everywhere in the report so a '
    "reader knows not to trust it the way an ordinary comparable "
    "diff is trusted. Not needed, and does nothing, on a "
    "comparable pair.",
)
@contract_options  # ADR-049: --contract (--audit-suppressions is gone -- `--view suppressions`)
@pack_option  # ADR-049 D8: --pack
@click.option(
    "--use-cases",
    "use_cases_manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="An impact-use-cases.yaml manifest "
    "whose declared use cases this comparison's own "
    "findings are attributed to: for each use case, which changes "
    "its resolved entrypoints can be shown to reach. Needs a "
    "source graph on at least one side (dump --sources/"
    "--build-info, or the always-on header-only graph). Read-only "
    "-- an unattributed finding is an absence of proof, not proof "
    "the finding is harmless, so this never moves a verdict or an "
    "exit code. Validate a manifest on its own with "
    "`abicheck project validate`.",
)
@click.option(
    "--performance-profile",
    "performance_profile",
    type=click.Choice(["balanced", "low-memory"]),
    default=None,
    help="The memory/speed trade-off to run under; overrides .abicheck.yml's "
    "performance.profile. 'balanced' (default) is fastest; 'low-memory' "
    "resolves one side at a time, each in its own process on Linux, for "
    "the lowest peak memory. Never changes findings or the exit code.",
)
@verbose_option
@click.pass_context
def compare_cmd(ctx: click.Context, /, **kwargs: Any) -> None:
    """Compare two ABI surfaces and report changes.

    Each input (OLD, NEW) can be a .so shared library or a JSON snapshot from
    'abicheck dump'. The format is auto-detected.

    When a .so file is given, headers (-H) are recommended for full ABI
    extraction. If headers are absent for ELF, abicheck falls back to
    DWARF-only mode (if DWARF available) or symbols-only analysis.

    \b
    Exit codes (legacy, with no severity setting in effect):
      0  NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK — no binary ABI break
         (COMPATIBLE_WITH_RISK: deployment risk present; check the report)
      2  API_BREAK — source-level API break — recompilation required
      4  BREAKING — binary ABI break detected
    \b
    Exit codes (severity-aware, with --severity-preset or a config severity: block):
      0  No error-level findings
      1  Error-level findings in addition or quality_issues only
      2  Error-level findings in potential_breaking (but not abi_breaking)
      4  Error-level findings in abi_breaking
    \b
    Orthogonal to both tables: with --contract,
    incomplete contract coverage of the selected --contract domain
    contributes exit 1. It is folded with max, so it raises a clean 0 to 1
    and never lowers a 2/4 — under the legacy scheme, 1 can only mean this.
    Without --contract there is no domain to be short of evidence
    for and the tables above are exhaustive. Set contract.unresolved=warn
    (via a `kind: contract` --pack) to accept incomplete coverage.
    \b
    A second, independent orthogonal axis (P0.4): with a project's
    `.abicheck.yml` setting `assurance.require_complete: true` (config-only
    -- no CLI flag; the former --require-complete-analysis flag was
    demoted here entirely), an analysis_assurance.status other than
    "complete" (how complete/trustworthy the evidence itself was — depth,
    TU/export accounting, fact-set comparability, header-context drift,
    source-graph completeness — independent of what the verdict says)
    contributes exit 1 the same way, folded with the same max discipline.
    Without the setting, analysis_assurance is still always computed and
    reported in -o json=..., it just never affects the exit code. Single-
    pair compares only, not the directory/package release fan-out (see
    docs/reference/config-file.md's `assurance:` section).
    \b
    A third, independent orthogonal axis:
    under --no-baseline, this becomes an audit rather than a comparison, and
    --severity-preset (any value other than 'info-only') opts that audit
    into gating on its own findings, contributing exit 3
    (AUDIT_GATE_EXIT_CODE) the first time a finding's effective verdict is
    BREAKING or API_BREAK. Folded with the same max discipline as the axes
    above -- it raises a clean 0 to 3 and never emits, or is confused for,
    the compatibility family's own 2/4. Without --no-baseline, or with
    --severity-preset info-only or omitted, this axis never contributes.
    See docs/reference/exit-codes.md.
    \b
    Invalid invocation (bad arguments/options, unreadable or unrecognised
    input) exits 64, outside the result space above, so it is never mistaken
    for an ABI verdict.

    \b
    Examples:
    \b
      # One-liner: each version has its own header (primary flow)
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --header old=include/v1/foo.h --header new=include/v2/foo.h
    \b
      # Shorthand: -H when the same header applies to both versions
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
    \b
      # With version labels and SARIF output
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --header old=v1/foo.h --header new=v2/foo.h \\
        --version old=1.0 --version new=2.0 -o sarif=abi.sarif
    \b
      # Compare saved snapshot vs current build (mixed mode)
      abicheck compare baseline.json ./build/libfoo.so --header new=include/foo.h
    \b
      # Compare two pre-dumped snapshots (existing workflow)
      abicheck compare libfoo-1.0.json libfoo-2.0.json
    \b
      # Policy and suppression
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h --policy sdk_vendor
      abicheck compare old.json new.json --suppress suppressions.yaml
    """
    # Options are parsed by the click wrapper above; the full compare flow lives
    # in cli_compare_helpers.run_compare (size-split from cli.py to keep this
    # module under the AI-readiness file-size cap). Click collects every declared
    # option/argument into **kwargs, so forwarding it verbatim keeps behaviour —
    # and the exit-code matrix — identical while the single typed signature lives
    # only on run_compare (no duplicated 56-line parameter list; CodeFactor).
    from ....cli_compare_helpers import run_compare
    from ..project_config import enter_performance_profile

    enter_performance_profile(ctx, kwargs.pop("performance_profile", None))

    # Plan slice 7m: one repeatable ``-o FORMAT=DESTINATION`` request reaches
    # this callback as a single ``ExportSet``; expand it into the
    # ``fmt``/``output``/``secondary_writes``/``output_dir`` dest names every
    # downstream consumer (run_compare, the release fan-out, the abort
    # renderers, the exit fold) already threads, so the grammar change stops
    # at this boundary rather than rippling through the whole compare stack.
    from ..options.export import (
        ExportSet,
        expand_export_kwargs,
        reject_dry_run_with_exports,
    )

    # The compact default describes one completed pair. Release/package
    # fan-out and explicit alternate views retain the detailed Markdown
    # projection they already support. This fallback applies only when the
    # user supplied no export; an explicit `-o review=...` is still validated
    # normally and never silently rewritten.
    exports = kwargs["exports"]
    assert isinstance(exports, ExportSet)
    from .compare_bundle_facts_rejections import STORED_BUNDLE_FACTS_FORMATS
    from .compare_default_format import renderable_formats, resolve_export_set

    kwargs["exports"] = resolve_export_set(
        exports,
        renderable=renderable_formats(
            kwargs.get("old_input"),
            kwargs.get("new_input"),
            release_formats=_RELEASE_FORMATS,
            stored_formats=STORED_BUNDLE_FACTS_FORMATS,
        ),
        rewrite_all=(
            any(token != "full" for token in kwargs.get("view", ()))
            or bool(kwargs.get("no_baseline"))
        ),
    )

    reject_dry_run_with_exports(bool(kwargs.get("dry_run")), kwargs["exports"])
    expand_export_kwargs(kwargs)

    # ADR-040 Lever 1: translate the side-aware --header/--include/--sources/
    # --build-info tuples back into the per-side kwargs run_compare consumes.
    normalize_sided_options(kwargs)
    # Phase 7n: --debug-info's detached-file transport is DWARF-only; a named
    # PDB/DWP is refused here rather than resolved and then ignored.
    reject_unsupported_detached_debug(
        [
            *kwargs.get("debug_roots", ()),
            *kwargs.get("debug_roots_old", ()),
            *kwargs.get("debug_roots_new", ()),
        ]
    )

    # ADR-068 D4/Phase 5, rewritten by plan slice 7o: resolve --view
    # (frontends.cli.options.view) into the two dest names it still carries,
    # report_mode/show_only. No profile injects those dests.
    from ..options.view import parse_view_tokens

    kwargs.update(parse_view_tokens(kwargs.pop("view", ())))

    # ADR-068 D2 / plan §6 Phase 2e: `--no-baseline` is an explicit
    # declaration, never inferred from arity -- branch before the two-sided
    # machinery below, which a plain `compare OLD NEW` never reaches.
    from .compare_no_baseline import maybe_dispatch_no_baseline_compare

    if maybe_dispatch_no_baseline_compare(ctx, kwargs):
        return

    # CLI cleanup phase two, PR I: OLD_INPUT/NEW_INPUT are classified
    # automatically for bundle-facts routing, replacing the removed
    # --old-bundle-facts flag -- see compare_bundle_operand_dispatch.py's
    # own docstring (stored NEW_INPUT + live OLD_INPUT is rejected there;
    # OLD_INPUT classified as stored -- alone, or with NEW_INPUT stored
    # too -- short-circuits the ordinary live-binary/directory dispatch
    # entirely, never reaching run_compare/_dispatch_release_compare -- see
    # compare_bundle_facts.py's own module docstring for why that lives
    # here rather than as a branch inside cli_compare_helpers.run_compare).
    from .compare_bundle_operand_dispatch import resolve_bundle_compare_dispatch

    _bundle_operands = resolve_bundle_compare_dispatch(
        kwargs["old_input"], kwargs["new_input"]
    )
    if _bundle_operands.old_is_stored:
        from .compare_bundle_facts import (
            dispatch as dispatch_bundle_facts,
            resolve_dispatch_compile_context,
        )

        # Read before resolve_dispatch_compile_context below mutates kwargs["config"] -- see resolve_stored_bundle_lang.
        _cfg_explicit = (
            ctx.get_parameter_source("config") == click.core.ParameterSource.COMMANDLINE
        )
        _compile_context = resolve_dispatch_compile_context(
            ctx, kwargs, new_is_stored=_bundle_operands.new_is_stored
        )
        kwargs["lang"], kwargs["lang_explicit"] = resolve_stored_bundle_lang(
            kwargs,
            config_explicit=_cfg_explicit,
            new_is_stored=_bundle_operands.new_is_stored,
            lang_default=LANG_DEFAULT,
        )
        dispatch_bundle_facts(
            compile_context=_compile_context,
            new_is_stored=_bundle_operands.new_is_stored,
            config_explicit=_cfg_explicit,
            **kwargs,
        )
        return
    reject_bundle_facts_manifest_without_old_bundle_facts(kwargs)
    run_compare(ctx, **kwargs)
