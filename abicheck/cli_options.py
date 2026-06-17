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

"""Reusable Click option groups.

Stacked-decorator helpers that bundle related ``compare`` options so the large
``cli.py`` stays under the AI-readiness file-size cap. Imported at the top of
``cli.py`` and applied to ``compare_cmd``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

import click

from .cli_params import DEPTH_PARAM, POLICY_FILE_PARAM

F = TypeVar("F", bound=Callable[..., object])


# ── ADR-037 D3: shared option families ───────────────────────────────────────
#
# Every option family that more than one verdict-emitting command needs is
# declared **once** here as a decorator; commands compose the decorators instead
# of re-declaring the family inline. The ``cli-contract`` AI-readiness gate
# (ADR-037 D10.2/D10.4) and ``tests/test_cli_contract.py`` key on the tables at
# the bottom of this module (``FAMILY_FLAGS`` / ``VERDICT_EMITTING_COMMANDS`` /
# ``INTENTIONAL_SUBSET``), so keep those in sync when a family changes.
#
# Decorators apply bottom-up (Click reverses ``__click_params__``), so each
# helper lists its options in reverse of their displayed order — matching the
# existing ``build_source_*`` helpers below.


def two_sided_input_options(func: F) -> F:
    """Headers / includes / version labels, shared (`-H/-I` + per-side + version).

    Identical across ``compare`` / ``compare-release`` / ``appcompat`` /
    ``deep-compare``: a both-sides input plus an old-only / new-only override and
    a per-side version label. (``--lang`` and the L2 ``--header-backend`` family
    stay inline — the latter becomes ``--ast-frontend`` in G22 Phase 6.)
    """
    func = click.option(
        "--new-version", "new_version", default="new", show_default=True,
        help="Version label for new side (used when input is a .so file).",
    )(func)
    func = click.option(
        "--old-version", "old_version", default="old", show_default=True,
        help="Version label for old side (used when input is a .so file).",
    )(func)
    func = click.option(
        "--new-include", "new_includes_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for new side only (overrides -I for new).",
    )(func)
    func = click.option(
        "--old-include", "old_includes_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Include dir for old side only (overrides -I for old).",
    )(func)
    func = click.option(
        "--new-header", "new_headers_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for new side only (overrides -H for new). "
             "Validated for native binaries; ignored for snapshots.",
    )(func)
    func = click.option(
        "--old-header", "old_headers_only", multiple=True,
        type=click.Path(path_type=Path),
        help="Public header for old side only (overrides -H for old). "
             "Validated for native binaries; ignored for snapshots.",
    )(func)
    func = click.option(
        "-I", "--include", "includes", multiple=True,
        type=click.Path(path_type=Path),
        help="Extra include directory for castxml (applied to both sides).",
    )(func)
    func = click.option(
        "-H", "--header", "headers", multiple=True,
        type=click.Path(path_type=Path),
        help="Public header file or directory applied to both sides (repeat for multiple). "
             "Recommended for full ABI analysis; without headers, native binaries fall back to symbols-only mode. "
             "Scopes the ABI surface to declarations in these headers for ELF; on PE/Mach-O scoping is "
             "best-effort and falls back to the export table when castxml is unavailable or names don't match "
             "(e.g. MSVC C++ mangling). Validated for native binaries; ignored for snapshots.",
    )(func)
    return func


def policy_options(func: F) -> F:
    """Verdict-classification policy + suppression file (`--policy`/`--policy-file`/`--suppress`).

    Shared verbatim by every verdict-emitting command. (``--policy`` accepting a
    *path* directly, folding ``--policy-file`` in, is a later-phase D4 change.)
    """
    func = click.option(
        "--suppress", type=click.Path(exists=True, path_type=Path), default=None,
        help="Suppression file (YAML) to filter known/intentional changes.",
    )(func)
    func = click.option(
        "--policy-file", "policy_file_path", type=POLICY_FILE_PARAM, default=None,
        help="YAML policy file with per-kind verdict overrides, or a built-in name "
             "(e.g. 'security'). Overrides --policy.",
    )(func)
    func = click.option(
        "--policy", "policy",
        type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
        default="strict_abi", show_default=True,
        help="Built-in policy profile for verdict classification. Ignored when "
             "--policy-file is given.",
    )(func)
    return func


def severity_options(func: F) -> F:
    """The severity preset + the four per-category overrides.

    ADR-037 D4 will demote the per-category flags into ``.abicheck.yml`` (Phase
    5), leaving only ``--severity-preset`` on the CLI; until then they are a
    genuine shared family across ``compare`` / ``compare-release`` / ``appcompat``
    and live here once instead of being copy-pasted three times.
    """
    func = click.option(
        "--severity-addition", "severity_addition",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        help="Severity for new public API additions (overrides preset).",
    )(func)
    func = click.option(
        "--severity-quality-issues", "severity_quality_issues",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        help="Severity for problematic behaviors like std symbol leaks (overrides preset).",
    )(func)
    func = click.option(
        "--severity-potential-breaking", "severity_potential_breaking",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        help="Severity for potential incompatibilities needing review (overrides preset).",
    )(func)
    func = click.option(
        "--severity-abi-breaking", "severity_abi_breaking",
        type=click.Choice(["error", "warning", "info"], case_sensitive=True),
        default=None,
        help="Severity for clear ABI/API incompatibilities (overrides preset).",
    )(func)
    func = click.option(
        "--severity-preset", "severity_preset",
        type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
        default=None,
        help="Severity preset: 'default', 'strict', or 'info-only'. "
             "Controls exit codes and report labels. Per-category "
             "--severity-* options override the chosen preset.",
    )(func)
    return func


def scope_options(func: F) -> F:
    """Public-surface scoping (`--scope-public-headers/--no-`).

    The universally-shared toggle. ``--show-filtered`` (a ``compare``-only audit
    view) stays inline on ``compare`` rather than being forced onto commands that
    have no filtered-findings report to dump.
    """
    func = click.option(
        "--scope-public-headers/--no-scope-public-headers", "scope_public_headers",
        default=True, show_default=True,
        help="Restrict findings to the public-header ABI surface (ADR-024): "
             "changes to symbols/types not reachable from public-header-declared "
             "exported API are recorded as filtered, not reported. Internal-type "
             "leaks are never hidden. On by default; use --no-scope-public-headers "
             "to report every finding regardless of surface.",
    )(func)
    return func


def output_options(
    formats: Sequence[str],
    *,
    default: str = "markdown",
    format_help: str = "Output format.",
    output_help: str | None = None,
) -> Callable[[F], F]:
    """Factory for the ``--format`` / ``-o/--output`` pair.

    A factory rather than a bare decorator because the *set* of producible
    formats legitimately differs per command (``appcompat`` cannot emit
    sarif/junit, ``compare-release`` cannot emit html/review) — but the option
    *structure*, the ``-o/--output`` flag, and the contract live here once.
    """
    # ``help=None`` renders no help line in Click, so a single call covers both
    # the with-help and without-help cases without a ``**dict[str, object]``
    # unpack (which mypy can't reconcile with ``click.option``'s overloads).
    def deco(func: F) -> F:
        func = click.option(
            "-o", "--output", "output",
            type=click.Path(path_type=Path), default=None, help=output_help,
        )(func)
        func = click.option(
            "--format", "fmt", type=click.Choice(list(formats)),
            default=default, show_default=True, help=format_help,
        )(func)
        return func

    return deco


def set_input_options(func: F) -> F:
    """Set-input fan-out knobs: ``-j/--jobs`` / ``--dso-only`` / ``--output-dir``.

    ADR-037 D7 folds ``compare-release`` into ``compare`` via input-type
    dispatch: when ``compare``'s operands are directories or packages it fans out
    to a per-library comparison, and these three flags tune that fan-out (parallel
    jobs, executable filtering, per-library report directory). On single-file
    inputs they are a no-op and ``compare`` warns. Declared once here so the
    dispatch and the deprecated ``compare-release`` alias share one surface.
    Applied bottom-up, so listed in reverse of displayed order.
    """
    func = click.option(
        "--output-dir", "output_dir", type=click.Path(path_type=Path), default=None,
        help="Directory to write per-library reports (directory/package inputs only).",
    )(func)
    func = click.option(
        "--dso-only", "dso_only", is_flag=True, default=False,
        help="Only compare shared objects, skip executables (directory/package inputs only).",
    )(func)
    func = click.option(
        "-j", "--jobs", "jobs", type=int, default=0, show_default=True,
        help="Parallel library comparisons for directory/package inputs "
             "(0 = auto-detect CPU count, the default).",
    )(func)
    return func


def debug_resolution_options(func: F) -> F:
    """Separate-debug-file resolution (ADR-021a): roots + debuginfod + format.

    Currently a ``compare``-only family — it resolves *local* ELF debug
    artifacts, which the package-oriented (``compare-release``) and
    snapshot-oriented (``deep-compare``/``appcompat``) commands do not take. It
    lives here so the moment a second command needs it there is one definition to
    compose, not a copy to drift (ADR-037 D3).
    """
    func = click.option(
        "--dwarf", "debug_format", flag_value="dwarf", hidden=True,
        help="Force DWARF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--ctf", "debug_format", flag_value="ctf", hidden=True,
        help="Force CTF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--btf", "debug_format", flag_value="btf", default=None, hidden=True,
        help="Force BTF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--debug-format", "debug_format_opt",
        type=click.Choice(["auto", "dwarf", "btf", "ctf"], case_sensitive=False),
        default=None,
        help="Force the ELF debug format for both sides (auto=pick best available). "
             "Supersedes the individual --btf/--ctf/--dwarf flags.",
    )(func)
    func = click.option(
        "--debuginfod-url", "debuginfod_url", default=None,
        help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).",
    )(func)
    func = click.option(
        "--debuginfod", is_flag=True, default=False,
        help="Enable debuginfod network resolution for debug info (opt-in).",
    )(func)
    func = click.option(
        "--debug-root2", "debug_roots_new", multiple=True, type=click.Path(path_type=Path),
        help="Debug root for new side only (overrides --debug-root for new).",
    )(func)
    func = click.option(
        "--debug-root1", "debug_roots_old", multiple=True, type=click.Path(path_type=Path),
        help="Debug root for old side only (overrides --debug-root for old).",
    )(func)
    func = click.option(
        "--debug-root", "debug_roots", multiple=True, type=click.Path(path_type=Path),
        help="Directory containing separate debug files (build-id trees, "
             "path-mirror, dSYM bundles). Applied to both sides. Can be repeated.",
    )(func)
    func = click.option(
        "--dwarf-only", is_flag=True, default=False,
        help="Force DWARF-only mode for both sides: use DWARF debug info "
             "as primary data source even when headers are available.",
    )(func)
    return func


def adr027_compare_options(func: F) -> F:
    """Add the ADR-027 API-surface-intelligence options to ``compare``.

    ``--pattern-verdicts`` / ``--explain-patterns`` (A4 modulation) and
    ``--surface-metrics`` (A1/D1.2 metric drift). Decorators apply bottom-up, so
    they are listed here in reverse of their displayed order.
    """
    func = click.option(
        "--surface-metrics",
        "surface_metrics",
        is_flag=True,
        default=False,
        help="Emit aggregate public-surface metric drift (ADR-027): "
        "public_surface_grew/shrank, undocumented_export_ratio_increased. "
        "Informational (COMPATIBLE).",
    )(func)
    func = click.option(
        "--explain-patterns",
        "explain_patterns",
        is_flag=True,
        default=False,
        help="Print idiom evidence behind each modulation (implies "
        "--pattern-verdicts).",
    )(func)
    func = click.option(
        "--pattern-verdicts/--no-pattern-verdicts",
        "pattern_verdicts",
        default=False,
        help="Modulate verdicts with idiom/anti-pattern evidence (ADR-027): "
        "demote opaque-pointer/PIMPL-hidden layout changes (header-aware only) "
        "and raise breaks when an opacity/handle guarantee is lost. Disclosed in "
        "the pattern_modulations ledger; reversible.",
    )(func)
    return func


def build_source_dump_options(func: F) -> F:
    """Add the ``--build-info`` / ``--sources`` embed options to ``dump``.

    Source-tree-centric inputs (ADR-028..033 amendment): ``--sources`` is a
    source checkout — L4 source ABI replay and the L5 graph are run inline and
    embedded; ``--build-info`` is an optional build dir / ``compile_commands.json``
    / pre-captured pack supplying L3 (auto-discovered inside the source tree when
    omitted). A path that is itself a pack directory from ``abicheck collect``
    is loaded as that pack instead. Embedding makes the ``.abi.json``
    self-contained, so a later ``compare old.json new.json`` carries the facts
    with no out-of-band directories. Applied bottom-up, so listed in reverse of
    display.
    """
    from pathlib import Path

    func = click.option(
        "--collect-mode", "collect_mode",
        type=click.Choice(["off", "build", "graph-build", "source-changed", "source-target", "graph-summary", "graph-full"]),
        default="source-target", show_default=False, hidden=True,
        help="DEPRECATED (ADR-037 D5): internal ADR-033 D2 evidence mode. Prefer "
        "the unified --depth dial; kept as a hidden alias for one release.",
    )(func)
    func = click.option(
        "--depth", "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Unified evidence-depth dial (ADR-037 D5; same vocabulary as "
        "`compare`/`scan --depth`): symbols=L0/L1 only, headers=+L2 AST (default), "
        "build=+L3 build context, source=+L4 replay & the L5 graph, full=deepest. "
        "--max == --depth full.",
    )(func)
    func = click.option(
        "--max", "max_depth", is_flag=True, default=False,
        help="Shorthand for --depth full (collect the deepest evidence available).",
    )(func)
    func = click.option(
        "--allow-build-query", "allow_build_query", is_flag=True, default=False,
        help="Permit running `build.query` from an explicit trusted "
        "--build-config to emit a compile DB / exports (ADR-032 D5 "
        "query_build_system). Off by default, and ignored for auto-discovered "
        "source-tree configs: only existing build outputs are inspected — "
        "a full project build is never run.",
    )(func)
    func = click.option(
        "--build-config", "build_config",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Path to a trusted `.abicheck.yml` build config (build system, "
        "query command, compile-DB location). Defaults to `.abicheck.yml` "
        "at the --sources tree root for non-executing settings; build.query "
        "runs only from an explicit --build-config.",
    )(func)
    func = click.option(
        "--build-compile-db", "build_compile_db", default=None, metavar="GLOB",
        help="Where a build/query lands its compile_commands.json, relative to "
        "--sources (e.g. 'build/compile_commands.json'). CLI equivalent of "
        "`.abicheck.yml` build.compile_db; overrides it when both are given.",
    )(func)
    func = click.option(
        "--build-query", "build_query", default=None, metavar="CMD",
        help="Build-system query command that emits a compile DB without a full "
        "build (e.g. 'cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON'). "
        "CLI equivalent of `.abicheck.yml` build.query — no config file needed. "
        "Only runs with --allow-build-query.",
    )(func)
    func = click.option(
        "--sources", "sources",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Source checkout to run L4 source ABI replay + the L5 graph over "
        "and embed inline. (A pack directory from `abicheck collect` is loaded "
        "as that pack instead.)",
    )(func)
    func = click.option(
        "--build-info", "build_info",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Optional L3 build context: a build dir, a compile_commands.json, "
        "or a pre-captured pack. Auto-discovered inside the --sources tree when "
        "omitted.",
    )(func)
    return func


def build_source_compare_options(func: F) -> F:
    """Add the build-info / sources compare options.

    By default ``compare old.json new.json`` reads build-info + source facts
    **embedded** in each snapshot (single-artifact UX). The optional
    ``--old-build-info`` / ``--new-build-info`` and ``--old-sources`` /
    ``--new-sources`` point at out-of-band pack directories to supply or
    override those facts per side; ``--collect-mode`` selects the inline
    collection mode (ADR-033 D2). All folded into the verdict as ordinary
    findings, never overriding artifact-backed ABI verdicts (ADR-028 D3).
    Applied bottom-up, so listed in reverse of displayed order.
    """
    from pathlib import Path

    pack_dir = click.Path(exists=True, file_okay=False, path_type=Path)
    func = click.option(
        "--collect-mode", "collect_mode",
        type=click.Choice(["off", "build", "graph-build", "source-changed", "source-target", "graph-summary", "graph-full"]),
        default="off", show_default=False, hidden=True,
        help="DEPRECATED (ADR-037 D5): internal ADR-033 D2 evidence mode. Prefer "
        "the unified --depth dial; kept as a hidden alias for one release.",
    )(func)
    func = click.option(
        "--max", "max_depth", is_flag=True, default=False,
        help="Shorthand for --depth full (collect the deepest evidence available).",
    )(func)
    func = click.option(
        "--depth", "depth", type=DEPTH_PARAM, default=None,
        help="Unified evidence-depth dial (ADR-037 D5): symbols=L0/L1 only, "
        "headers=+L2 AST (default), build=+L3, source=+L4 replay & the L5 graph, "
        "full=deepest. --max == --depth full. Deeper-than-headers needs "
        "--old/new-sources or --old/new-build-info.",
    )(func)
    func = click.option(
        "--new-sources", "new_sources", type=pack_dir, default=None,
        help="Out-of-band L4/L5 source pack for the new side (overrides embedded).",
    )(func)
    func = click.option(
        "--old-sources", "old_sources", type=pack_dir, default=None,
        help="Out-of-band L4/L5 source pack for the old side (overrides embedded).",
    )(func)
    func = click.option(
        "--new-build-info", "new_build_info", type=pack_dir, default=None,
        help="Out-of-band L3 build-info pack for the new side (overrides embedded).",
    )(func)
    func = click.option(
        "--old-build-info", "old_build_info", type=pack_dir, default=None,
        help="Out-of-band L3 build-info pack for the old side (overrides embedded).",
    )(func)
    return func


# ── ADR-037 D10: contract metadata (single source of truth for the gate) ──────
#
# The ``cli-contract`` AI-readiness gate (D10.2 decorator coverage, D10.4
# one-default-per-flag) and its test mirror key on these tables. Keeping them
# beside the decorators means adding/renaming a family is a one-place edit.

#: Family name → the long ``--flag`` names that family contributes. The gate
#: checks a verdict-emitting command carries the *whole* family (composed via the
#: matching decorator) or is allowlisted in ``INTENTIONAL_SUBSET``.
FAMILY_FLAGS: dict[str, frozenset[str]] = {
    "two_sided_input": frozenset({
        "--header", "--include", "--old-header", "--new-header",
        "--old-include", "--new-include", "--old-version", "--new-version",
    }),
    "policy": frozenset({"--policy", "--policy-file", "--suppress"}),
    "severity": frozenset({
        "--severity-preset", "--severity-abi-breaking",
        "--severity-potential-breaking", "--severity-quality-issues",
        "--severity-addition",
    }),
    "scope": frozenset({"--scope-public-headers"}),
    "output": frozenset({"--format", "--output"}),
    # Documented for completeness; deliberately *not* in REQUIRED_FAMILIES (it
    # resolves local ELF debug artifacts, which the package/snapshot-oriented
    # commands do not take).
    "debug_resolution": frozenset({
        "--dwarf-only", "--debug-root", "--debug-root1", "--debug-root2",
        "--debuginfod", "--debuginfod-url", "--debug-format",
        "--btf", "--ctf", "--dwarf",
    }),
}

#: Family name → the decorator callable that supplies it (used by the gate's
#: AST coverage check, which keys on the decorator applied to a command).
FAMILY_DECORATOR: dict[str, str] = {
    "two_sided_input": "two_sided_input_options",
    "policy": "policy_options",
    "severity": "severity_options",
    "scope": "scope_options",
    "output": "output_options",
}

#: Families every verdict-emitting command must compose (unless allowlisted).
#: ``debug_resolution`` is deliberately *not* required — it resolves local ELF
#: debug artifacts that the package/snapshot-oriented commands do not take.
REQUIRED_FAMILIES: frozenset[str] = frozenset(FAMILY_DECORATOR)

#: command name → module basename, for the gate to locate each command's source.
VERDICT_EMITTING_COMMANDS: dict[str, str] = {
    "compare": "cli.py",
    "compare-release": "cli_compare_release.py",
    "appcompat": "cli_appcompat.py",
    "deep-compare": "cli_max.py",
}

#: (command, family) → reason. A deliberate, reviewed omission of a shared
#: family from a verdict-emitting command (ADR-037 D3: opt out *explicitly*).
INTENTIONAL_SUBSET: dict[tuple[str, str], str] = {
    ("deep-compare", "severity"): (
        "deep-compare is a one-shot convenience wrapper that exposes only the "
        "coarse --severity-preset; the per-category overrides are config-bound "
        "(ADR-037 D4) and not surfaced on this command."
    ),
}

#: Flag names knowingly carrying two defaults across decorators, deferred to a
#: later phase rather than hidden. ``--collect-mode`` differs between the dump
#: embed default (source-target) and the compare read default (off); it is now a
#: hidden deprecated alias behind the unified ``--depth`` dial (G22 Phase 3) and
#: its two-default-ness rides out the deprecation window in ``DEPRECATED_FLAGS``.
DEFERRED_MULTI_DEFAULT: frozenset[str] = frozenset({"--collect-mode"})


# ── ADR-037 D5 / §Backward-compat: deprecated-flag registry (G22 Phase 3) ─────
#
# Single source of truth for renamed/removed CLI surface. Each entry records the
# replacement and a one-line note; the deprecation *resolver* + window-enforcing
# test land in Phase 7 (kept advisory until 1.0). Today the live deprecations
# already warn at their option sites (``--collect-mode`` in the command bodies,
# ``--depth graph`` in ``cli_params.DepthParam``); this table documents them in
# one place so nothing is removed silently.
DEPRECATED_FLAGS: dict[str, tuple[str, str]] = {
    "--collect-mode": (
        "--depth",
        "internal ADR-033 evidence mode; use the unified --depth dial (ADR-037 D5).",
    ),
    "--depth=graph": (
        "--depth=source",
        "the L5 graph is built internally at --depth source (ADR-037 D6).",
    ),
}
