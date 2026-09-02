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
from typing import NamedTuple

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


def _merge_cli_debug_format(
    debug_format_opt: str | None,
    legacy_debug_format: str | None,
    *,
    legacy_from_cli: bool,
) -> str | None:
    """Effective *command-line* debug format across all CLI spellings (ADR-040 L2).

    ``--debug-format`` (``debug_format_opt``) is the primary selector; the hidden
    compatibility flags ``--btf``/``--ctf``/``--dwarf`` write the ``debug_format``
    dest. Either, when typed, must beat a ``.abicheck.yml`` ``debug.format`` — so
    fold a *command-line-sourced* legacy flag in here (the flag's own default is
    ``None``, so ``legacy_from_cli`` distinguishes "typed" from "unset"). Returns
    ``None`` when no format was given on the command line, letting config win.
    """
    if debug_format_opt is not None:
        return debug_format_opt
    if legacy_from_cli:
        return legacy_debug_format
    return None


def _reject_set_input_flags(
    exit_code_scheme: str | None,
    reconcile_build_context: bool,
    env_matrix_path: Path | None,
    used_by_apps: tuple[Path, ...] = (),
    required_symbols: tuple[str, ...] = (),
    use_cases_manifest: Path | None = None,
    diagnostic_comparison: bool = False,
    audit_suppressions: bool = False,
    include_labels: dict[Path, str] | None = None,
    require_complete_analysis: bool = False,
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
    """
    if exit_code_scheme is not None:
        raise click.UsageError(
            "--exit-code-scheme is not supported for directory/package "
            "(release) comparisons: the per-library fan-out uses the legacy "
            "verdict scheme, or severity-aware when severity is configured in "
            ".abicheck.yml. Compare libraries individually for explicit "
            "scheme control."
        )
    if reconcile_build_context:
        raise click.UsageError(
            "--reconcile-build-context is not supported for directory/package "
            "(release) comparisons; it applies to single-file / snapshot "
            "inputs. Compare the libraries individually to use it."
        )
    if env_matrix_path is not None:
        raise click.UsageError(
            "--env-matrix is not supported for directory/package (release) "
            "comparisons yet; it applies to single-file / snapshot inputs. "
            "Compare the libraries individually to use it."
        )
    if used_by_apps:
        raise click.UsageError(
            "--used-by is not supported for directory/package (release) "
            "comparisons: the per-library fan-out has no per-app scoping. "
            "Compare the specific library individually with --used-by."
        )
    if required_symbols:
        raise click.UsageError(
            "--required-symbol/--required-symbols is not supported for "
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
    if audit_suppressions:
        raise click.UsageError(
            "--audit-suppressions is not supported for directory/package "
            "(release) comparisons yet: the per-library fan-out has no "
            "single suppression-audit result to attach. Compare the "
            "specific library individually to use it."
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
            "--require-complete-analysis is not supported for directory/"
            "package (release) comparisons yet (P0.4): the per-library "
            "fan-out has no single analysis_assurance result to gate on. "
            "Compare the specific library individually to use it, or see "
            "P0.6 (run-plan-aware aggregation) for the tracked follow-up."
        )


def _reject_depth_for_set_inputs(ctx: click.Context) -> str | None:
    """Resolve (or reject) an explicit ``--depth`` for a directory/package compare.

    D1: ``--depth`` used to be rejected wholesale by
    ``cli_resolve._reject_evidence_flags_for_set_inputs``, lumped in with
    ``--sources``/``--build-info``/``--dump-manifest`` under one message
    whose own reasoning ("the per-library fan-out does not collect inline
    build/source evidence") never applied to every rung of the dial:

    * ``binary`` requests *less* evidence than the fan-out already collects
      by default (each pair is compared from its own binary plus whatever
      header/compile-context evidence the release's own
      ``-H``/``--include-dir`` already resolves) — there is nothing about an
      explicit ``--depth binary`` assertion the fan-out can't provide, so it
      is accepted here and forwarded to every pair (mirrors a single-pair
      ``compare --depth binary``, which just clears header/build/source
      evidence rather than requiring anything new).
    * ``headers`` is still rejected, but for a narrower, distinct reason than
      build/source: the fan-out *does* resolve per-pair header evidence
      (the same ``-H``/compile-context plumbing a plain directory `compare`
      already threads through), so it isn't blocked by "no inline
      build/source evidence". What's actually missing is depth's *floor*
      enforcement (``workflows.artifact.execute.enforce_requested_depth`` —
      failing the run when a pair didn't actually reach the requested rung)
      — that has no home in the per-library fan-out today. Lumping it into
      build/source's message would misstate why it's rejected, so it gets
      its own message instead (D1).
    * ``build``/``source`` are rejected for the original reason: they need
      inline build/source evidence the release fan-out has no per-library
      way to collect (the flags that would feed it — ``--sources``/
      ``--build-info`` — are exactly what the caller's own
      ``_EVIDENCE_SET_INPUT_FLAGS`` rejects).

    Returns the accepted depth value (currently always ``"binary"`` or
    ``None``) for the caller to forward to the fan-out; raises
    ``click.UsageError`` for anything else explicitly requested. Lives here
    (not next to its caller in ``cli_resolve.py``) purely because that
    module has no line-count budget left (`architecture/debt.yaml`'s
    ``no_growth`` baseline) — this module's own ``click``-only, no-abicheck-
    import leaf contract (see the module docstring) fits it exactly as
    well.
    """
    if ctx.get_parameter_source("depth") != click.core.ParameterSource.COMMANDLINE:
        return None
    depth: str | None = ctx.params.get("depth")
    if depth is None:
        return None
    value = depth.lower()
    if value == "binary":
        return value
    if value == "headers":
        raise click.UsageError(
            "--depth headers is not supported for directory/package (release) "
            "comparisons: the per-library fan-out does not enforce a "
            "per-library evidence floor (it already resolves per-pair header "
            "evidence via -H/--include-dir, it just can't yet fail the run "
            "when a pair falls short of the requested rung). Compare the "
            "libraries individually (where --depth headers is honoured) to "
            "require header-level evidence."
        )
    raise click.UsageError(
        f"--depth {depth} is not supported for directory/package (release) "
        "comparisons: the per-library fan-out does not collect inline "
        "build/source evidence. Compare the libraries individually (or "
        "pre-dump snapshots with `dump --sources/--build-info`) to collect "
        "L3-L5 evidence."
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
    unlike ``--jobs``/``--dso-only``/``--output-dir``, which are merely
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
    mangled symbol. HTML demangles safely because ``html_report.
    _symbol_cell``/``_changes_table`` always run ``demangle_text`` BEFORE
    ``html.escape`` — never the reverse — so a demangled signature's own
    ``<``/``>``/``&`` are escaped like any other text, not injected raw
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
    """Reject --debug-format / legacy --btf/--ctf/--dwarf for PE/Mach-O inputs.

    They force an ELF debug format and are silently ignored by the PE/Mach-O dump
    paths, so reject them up front (mirrors dump_cmd). JSON-snapshot / dump inputs
    have ``*_fmt == None`` and are unaffected.
    """
    if effective_debug_format is None:
        return
    for side, bfmt in (("old", old_fmt), ("new", new_fmt)):
        if bfmt in ("pe", "macho"):
            raise click.BadParameter(
                f"--debug-format {effective_debug_format} is only supported "
                f"for ELF binaries, but the {side} input is {bfmt.upper()}."
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
