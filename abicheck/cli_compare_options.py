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

"""What ``compare`` rejects or normalizes before any snapshot is read.

Flag bookkeeping (which spelling did the user actually type, which typed
parameters are inert for the operands given) and the usage errors that
follow from it: an evidence/compile-context flag passed alongside a
pre-extracted set input, a ``--debug-format`` that only means something for
ELF, a ``--demangle``/``--no-demangle`` pair, the debug-root list.

Split out of :mod:`abicheck.cli_compare_helpers`, which sat one line under
the 2000-line hard cap. The seam is not "small helpers" but "the part that
needs no engine at all" -- everything here answers a question about the
argv, so it imports ``click`` and nothing from ``abicheck``. That matters
structurally, not just aesthetically: ``cli_compare_helpers`` is inside the
baselined CLI-registration import cycle (``IMPORT_CYCLE_ALLOWLIST``), and a
module carved out of it that pulled in ``cli``/``cli_resolve``/
``cli_dump_helpers`` would join that cycle -- which the
``import-cycle-growth`` gate rejects, and which CLAUDE.md says needs an ADR
rather than a wider allowlist. The option helpers that *do* reach those
modules deliberately stayed behind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import click


def _cli_flag(name: str, value: bool) -> bool | None:
    """Return *value* only when *name* actually came from the command line.

    So a flag default (e.g. ``--scope-public-headers``'s True) doesn't mask config.
    """
    src = click.get_current_context().get_parameter_source(name)
    return value if src == click.core.ParameterSource.COMMANDLINE else None


def _param_from_cli(name: str) -> bool:
    """True when parameter *name*'s value came from the command line (not default)."""
    src = click.get_current_context().get_parameter_source(name)
    return bool(src == click.core.ParameterSource.COMMANDLINE)


def _reject_set_input_flags(
    env_matrix_path: Path | None,
    used_by_apps: tuple[Any, ...] = (),
    required_symbols: tuple[str, ...] = (),
    use_cases_manifest: Path | None = None,
    diagnostic_comparison: bool = False,
    audit_suppressions: bool = False,
    suppress: Path | None = None,
    include_labels: dict[Path, str] | None = None,
    require_complete_analysis: bool = False,
    budget: str | None = None,
    pdb_path: Path | None = None,
) -> None:
    """Reject single-pair-only flags on a directory/package (release) compare.

    The per-library fan-out has no public CLI support for these, so reject them
    loudly rather than silently ignore them (ADR-037 D12).

    ``--pack`` is not one of these -- its own, separate resolution (CLI
    cleanup phase two, "PR B" slice 1) decides what to accept or reject.
    ``--write`` (``secondary_fmt``/``secondary_output``) is not one of these
    either, as of CLI cleanup phase two, PR E: the release engine now
    supports it directly (``compare_release_cmd``'s own
    ``secondary_output_options``/``reject_incoherent_secondary_output``
    call), so there is nothing left for this function to reject.
    ``--exit-code-scheme`` is not one of these either any more -- CLI
    cleanup phase two PR G2 deleted the flag entirely, so there is nothing
    left to reject it against on a release comparison either.
    """
    # ``--reconcile-build-context`` is not one of these any more either
    # (one-comparison-product.md §4.1's AUTO row): the flag is gone and the
    # ADR-039 reconciliation runs unconditionally inside the Tier-2
    # ``compare_snapshots`` chokepoint every per-library fan-out already
    # routes through, so the release path now *gets* the behavior this
    # branch used to reject a request for.
    if env_matrix_path is not None:
        raise click.UsageError(
            "--env-matrix is not supported for directory/package (release) "
            "comparisons yet; it applies to single-file / snapshot inputs. "
            "Compare the libraries individually to use it."
        )
    if pdb_path is not None:
        # Codex review, PR #1180, fresh evidence ("Reject PDB config for
        # release fan-outs"): compare_pdb_config's own PE-liveness check
        # never fires here -- it sees the raw directory/package path, not
        # its PE members, so it never rejects. The release dispatch below
        # has no PDB parameter of its own at all, so a configured
        # debug.pdb_path would otherwise be silently dropped, every member
        # falling back to auto-discovery with no PDB.
        raise click.UsageError(
            "debug.pdb_path is not supported for directory/package "
            "(release) comparisons: the per-library fan-out has no "
            "per-member PDB parameter, so the configured value would be "
            "silently ignored while every member fell back to auto-"
            "discovery (an embedded PDB path, or a same-named .pdb next "
            "to the DLL). Compare the specific library individually to "
            "use it."
        )
    if used_by_apps:
        raise click.UsageError(
            "--used-by/--used-by-manifest is not supported for "
            "directory/package (release) comparisons: the per-library "
            "fan-out has no per-app scoping. Compare the specific library "
            "individually with --used-by/--used-by-manifest."
        )
    if required_symbols:
        raise click.UsageError(
            "--required-symbol is not supported for "
            "directory/package (release) comparisons: the per-library "
            "fan-out has no plugin-host-contract scoping. Compare the "
            "specific library individually with --required-symbol."
        )
    if use_cases_manifest is not None:
        raise click.UsageError(
            "--use-cases is not supported for directory/package (release) "
            "comparisons: attribution walks one pair's own call graphs, and "
            "the per-library fan-out never builds them, so the manifest "
            "would be accepted and attribute nothing. Compare the specific "
            "library individually with --use-cases."
        )
    if diagnostic_comparison:
        raise click.UsageError(
            "--diagnostic-comparison is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out does not "
            "wire the ADR-050 D2 comparability gate's diagnostic escape "
            "hatch (a mismatch there still raises unhandled). Compare the "
            "specific library individually to use it."
        )
    # --contract is deliberately NOT rejected here
    # (CLI-audit P1, release/package contract parity): the per-library
    # fan-out now threads it straight into each pair's own
    # service.run_compare(contract_evaluation=..., contract_mode=...) call
    # (compare_release_cmd), the exact same Tier-2 chokepoint a single-pair
    # `compare` uses -- so a library compared through the fan-out gets the
    # identical contract decision it would from comparing it individually.
    # --pack is the same story since CLI cleanup phase two "PR B" slice 1:
    # its own resolution (resolve_release_pack_application, called by the
    # caller right after this function) applies a pack's policy/contract-
    # surface contributions to every library uniformly, through
    # CompareRequest.pack_policy_overrides/pack_internal_namespaces -- the
    # same Tier-2 chokepoint (service_compare_pipeline.classify_compare_pair)
    # a single-pair `compare` folds its own packs through. Only a `kind:
    # gate` pack (gate.exit_code_scheme/gate.severity.*) is still rejected,
    # by that resolution itself, since the release fan-out has no resolved
    # gate-options wiring to apply one to yet.
    # ADR-068 D4/Phase 5, CodeRabbit/Codex review on PR #1154: the scalar
    # `compare` path's own preflight (``_preflight_manifests_and_audit``)
    # made ``--audit-suppressions`` with no ``--suppress`` a no-op rather
    # than a hard rejection -- there is genuinely nothing to audit without a
    # suppression file, so the release fan-out must not reject that same
    # harmless combination just because the operand is a directory/package.
    # A real conflict remains: ``--suppress`` *with* the audit *rendering*
    # request asks for a genuine per-finding audit section, and the
    # per-library fan-out still has no single audit result to attach across
    # N libraries -- that combination is still rejected. The flag itself is
    # gone (Phase 5); ``--view suppressions`` is its only spelling now, so
    # the message names that instead, the same way
    # ``reject_release_incompatible_view_mode`` already names ``--view``.
    if audit_suppressions and suppress is not None:
        raise click.UsageError(
            "--view suppressions is not supported together with --suppress "
            "for directory/package (release) comparisons yet: the "
            "per-library fan-out has no single suppression-audit result to "
            "attach. Compare the specific library individually to use it."
        )
    if include_labels:
        raise click.UsageError(
            "A labeled --include (old:LABEL=PATH/new:LABEL=PATH/"
            "both:LABEL=PATH) is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out does not "
            "thread ADR-050 D1's project_include_labels into its per-library "
            "dumps, so the label would be silently dropped. Compare the "
            "specific library individually to use it."
        )
    if require_complete_analysis:
        raise click.UsageError(
            "assurance.require_complete is not supported for directory/"
            "package (release) comparisons yet (P0.4): the per-library "
            "fan-out has no single analysis_assurance result to gate on. "
            "Compare the specific library individually to use it, or see "
            "P0.6 (run-plan-aware aggregation) for the tracked follow-up."
        )
    if budget is not None:
        raise click.UsageError(
            "--budget is not supported for directory/package (release) "
            "comparisons yet (ADR-068 §3 #19, Codex review): the per-library "
            "fan-out dispatches each member through its own process/thread, "
            "so a single ambient deadline set here would not reach any of "
            "them, silently ignoring the budget instead of enforcing it. "
            "Compare a specific library individually to use --budget."
        )


def _resolve_depth_for_set_inputs(ctx: click.Context) -> str | None:
    """Resolve an explicit ``--depth`` for a directory/package compare.

    Returns the requested depth verbatim (``None`` when the user did not
    type one) for the caller to forward to the release fan-out. It rejects
    nothing: every rung of the public ladder is forwarded, exactly as a
    single-pair ``compare`` forwards it.

    **Why this used to reject three of the four rungs, and why it no longer
    does.** This guard began as one wholesale "``--depth`` is not supported
    for directory/package comparisons" usage error, later split (D1) into a
    per-rung allow-list: ``binary`` accepted, ``headers`` rejected for
    lacking per-library *floor* enforcement, ``build``/``source`` rejected
    for needing inline ``--sources``/``--build-info`` the fan-out cannot
    collect. Both surviving rejections rested on beliefs about the fan-out
    that stopped being true once every member pair started routing through
    :func:`abicheck.service.run_compare` like any other comparison:

    * **Floor enforcement does have a home.**
      ``service_compare_pipeline.resolve_compare_request`` calls
      ``workflows.artifact.execute.enforce_requested_depth`` for *every*
      pair it resolves, the fan-out's members included, and
      ``classify_compare_pair`` applies the matching ceiling
      (``policy.depth_projection.project_pair_to_depth``). A member that
      falls short of the requested rung therefore already fails -- as that
      member's own ``ERROR`` result on the release's acquisition record
      (``operational: extraction_error``, ``scope: incomplete``), which is
      the *release-shaped* answer, strictly more informative than one
      whole-run usage error that names no member at all.
    * **Reachability is a property of the members, not of the operand's
      cardinality.** A directory member may itself be a pre-dumped JSON
      snapshot carrying embedded L3/L4/L5 evidence (``dump --sources``/
      ``--build-info``), which satisfies ``build``/``source`` with no inline
      collection at all -- so rejecting those rungs for every set input
      denied a genuinely reachable configuration. Conversely a bare ``.so``
      member cannot reach them, and now says so per member rather than
      being pre-judged for its neighbours.

    So the whole rung allow-list was a static restatement of a check that
    already runs downstream over real evidence, wrong in one direction and
    redundant in the other. Deleting it is what makes
    ``compare OLD_DIR NEW_DIR --depth X`` mean, per member, exactly what
    ``compare old.so new.so --depth X`` means -- AGENTS.md's "One model, any
    cardinality" product rule.

    Unchanged by any of that, and still worth reading before touching this
    path: ``--depth`` is a *floor* for live extraction and, for a member
    that is already a pre-built snapshot, the ceiling is applied by
    ``policy.depth_projection`` rather than by refusing the operand -- see
    ``docs/contribute/known-gaps.md``'s "``--depth`` is a floor for live
    extraction, not a ceiling for a pre-built snapshot" entry, which this
    function has cross-referenced since it accepted its first rung.

    The *flags* that genuinely have no per-library home on this path
    (``--sources``/``--build-info``/``--dump-manifest``) are still rejected,
    by this function's caller
    (``cli_resolve._reject_evidence_flags_for_set_inputs``) -- that guard is
    about an input the fan-out would silently drop, not about a rung. The
    guidance the removed rung errors used to carry (compare libraries
    individually, or pre-dump snapshots with ``dump --sources``) is carried
    by the release's own evidence-contract notice
    (``cli_compare_release_matrix``), which is where a run that pinned a
    rung its members could not reach now reports it.

    Lives here (not next to its caller in ``cli_resolve.py``) purely because
    that module has no line-count budget left (``architecture/debt.yaml``'s
    ``no_growth`` baseline) -- this module's own ``click``-only,
    no-abicheck-import leaf contract (see the module docstring) fits it
    exactly as well.
    """
    if ctx.get_parameter_source("depth") != click.core.ParameterSource.COMMANDLINE:
        return None
    depth: str | None = ctx.params.get("depth")
    return depth.lower() if depth is not None else None


#: ADR-068 Phase 2c/2d flags that only a *single-pair* compare implements
#: (param dest -> flag). Both are per-run inputs to one library's own
#: analysis: the changed-path seed scopes that library's L4/L5 replay, and
#: the abi3 audit judges one candidate artifact. The directory/package
#: fan-out neither collects inline source evidence nor audits members as
#: extension modules, so accepting either there would be a flag that
#: silently does nothing -- exactly the failure `_reject_evidence_flags_
#: for_set_inputs` above exists to prevent.
_SINGLE_PAIR_ONLY_SET_INPUT_FLAGS: dict[str, str] = {
    "since": "--since",
    "changed_paths_opt": "--changed-path",
    "abi3": "--abi3",
}


def _reject_single_pair_flags_for_set_inputs(ctx: click.Context) -> None:
    """Reject the Phase 2c/2d single-pair-only flags on a release compare."""
    used = sorted(
        flag
        for dest, flag in _SINGLE_PAIR_ONLY_SET_INPUT_FLAGS.items()
        if ctx.get_parameter_source(dest) == click.core.ParameterSource.COMMANDLINE
    )
    if not used:
        return
    raise click.UsageError(
        ", ".join(used)
        + " "
        + ("is" if len(used) == 1 else "are")
        + " not supported for directory/package (release) comparisons: "
        "--since/--changed-path scope one library's own source-evidence "
        "replay, and --abi3 audits one candidate extension module. Compare "
        "the library (or the extension module) individually to use them."
    )


def _reject_bundle_facts_out_for_single_pair(bundle_facts_out: Path | None) -> None:
    """Reject ``--bundle-facts-out`` on a single-file/snapshot comparison.

    The mirror-image case of :func:`_reject_set_input_flags` above: this is
    a directory/package-only flag reaching a single-pair compare, rather
    than the other way around. Rejected outright rather than merged into
    ``cli._warn_unused_set_flags``'s warn-and-ignore set (G38 Phase 2,
    Codex review, fresh evidence): it promises to persist an OLD-side
    baseline artifact, and a single-pair compare has no library map to
    build one from -- silently accepting it would report success while
    leaving automation believing a baseline was written when none was,
    unlike ``--dso-only``/``--output-dir``, which are merely
    inert conveniences here.
    """
    if bundle_facts_out is not None:
        raise click.UsageError(
            "--bundle-facts-out is only supported for directory/package "
            "(release) comparisons; a single-file/snapshot compare has no "
            "OLD-side library map to persist. Compare a directory or "
            "package pair to use it."
        )


class _NormalizedCompareOptions(NamedTuple):
    collect_mode: str
    headers: tuple[Path, ...]
    old_headers_only: tuple[Path, ...]
    new_headers_only: tuple[Path, ...]
    effective_debug_format: str | None
    demangle: bool
    report_mode: str
    show_impact: bool


def _resolve_demangle(fmt: str, demangle: bool | None) -> bool:
    """Resolve the tri-state ``--demangle`` flag against a specific format.

    Default ON for the human-facing formats (markdown/review/html), OFF for
    machine formats (json/sarif/junit) whose consumers match on the raw
    mangled symbol. HTML demangles safely because ``report.render_html.
    abbr_symbol_text``/``render_changes_table`` always run ``demangle_text``
    BEFORE ``html.escape`` — never the reverse — so a demangled signature's
    own ``<``/``>``/``&`` are escaped like any other text, not injected raw
    (this was previously assumed unsafe and HTML defaulted OFF; abicheck
    code-review report item 8). An explicit flag always wins over the
    per-format default.

    Shared by the primary render (:func:`_normalize_compare_options`) and
    the ``--write`` render in :func:`run_compare`, each resolved
    against its own format — a machine primary format paired with a text
    secondary format (or vice versa) must not inherit the other's default.
    """
    return fmt in {"markdown", "review", "html"} if demangle is None else demangle


def _reject_debug_format_for_non_elf(
    effective_debug_format: str | None,
    old_fmt: str | None,
    new_fmt: str | None,
) -> None:
    """Reject a forced ELF debug format for PE/Mach-O inputs.

    They force an ELF debug format and are silently ignored by the PE/Mach-O dump
    paths, so reject them up front (mirrors dump_cmd). JSON-snapshot / dump inputs
    have ``*_fmt == None`` and are unaffected.

    ADR-068 D5 / Phase 7a: ``compare`` no longer has a ``--debug-format`` CLI
    flag (only ``.abicheck.yml``'s ``debug.format`` key), so the message
    names the config key rather than a flag that no longer exists.
    """
    if effective_debug_format is None:
        return
    for side, bfmt in (("old", old_fmt), ("new", new_fmt)):
        if bfmt in ("pe", "macho"):
            raise click.BadParameter(
                f"debug.format: {effective_debug_format} (.abicheck.yml) is "
                f"only supported for ELF binaries, but the {side} input is "
                f"{bfmt.upper()}."
            )


def _resolve_debug_roots(
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
) -> tuple[list[Path], list[Path]]:
    """Per-side debug roots: --debug-root old=/new= override the both-sides value."""
    resolved_old = list(debug_roots_old) if debug_roots_old else list(debug_roots)
    resolved_new = list(debug_roots_new) if debug_roots_new else list(debug_roots)
    return resolved_old, resolved_new


def _warn_force_public_ignored(
    force_public: object,
    scope_public_headers: bool,
) -> None:
    """Warn a ``scope.public_symbols`` overlay needs ``--scope-public-headers``.

    Names the config key rather than the removed ``--public-symbol``/
    ``--public-symbols-list`` flags it used to: they were hidden duplicates of
    that key and are gone, so the overlay this warns about can only have come
    from ``.abicheck.yml`` (Codex review).
    """
    if force_public and not scope_public_headers:
        click.echo(
            "Warning: .abicheck.yml's scope.public_symbols overlay only takes "
            "effect with --scope-public-headers; ignoring the widening overlay.",
            err=True,
        )


def echo_coverage_warnings(warnings: list[str]) -> None:
    """Echo each of *warnings* to stderr, prefixed "Warning: " -- the one-line format's own rendered summary is a fixed, machine-parseable string with no room for a coverage_warnings entry, unlike every other format, which already surfaces them inline (Codex review, fresh evidence)."""
    for w in warnings:
        click.echo(f"Warning: {w}", err=True)
