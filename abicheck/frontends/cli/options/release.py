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

"""``compare``'s release-fanout/build-source/header-graph/evidence option
groups -- split out of :mod:`abicheck.cli_options` when that module reached
the 2000-line hard cap.

Bundles ``release_options`` (directory/package release-comparison knobs),
``debug_resolution_options`` (ADR-021a separate-debug-file resolution),
``adr027_compare_options``, ``app_usage_scope_options``,
``build_source_dump_options``, ``header_graph_options`` (+ its deprecated-flag
warning helper), and ``evidence_options`` (the ADR-037 D3 canonical name for
the pre-existing ``build_source_compare_options`` alias, kept here too) --
every stacked-decorator option group that adds no edge back into the
CLI-registration import cycle, mirroring why ``apply_compare_profile``/
``_profile_targets_set_input`` stayed behind in :mod:`abicheck.cli_options`
itself (they reach ``cli_resolve``, this module does not reach anything
beyond ``click``/``params``).

Deliberately a **leaf**, the same shape as this package's ``profiles.py``/
``contract.py``/``secondary_output.py`` siblings: it restates the one-line
``F`` TypeVar rather than importing it back from its former home, so the
split adds no edge to the CLI-registration import cycle.
:mod:`abicheck.cli_options` re-exports every name here, so each existing
caller -- and each existing test importing them from there -- is
unaffected.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import click

from .params import (
    DEPTH_PARAM,
    SIDED_BUILD_INFO_PARAM,
    SIDED_EXISTING_PATH_PARAM,
    SIDED_PATH_PARAM,
    SIDED_SOURCES_PARAM,
)

F = TypeVar("F", bound=Callable[..., object])


def release_options(func: F) -> F:
    """Directory/package (release) comparison knobs, folded onto ``compare``.

    The release-only options the removed ``compare-release`` command exposed:
    package extraction (``--debug-info*``/``--devel-pkg*``), DSO selection
    (``--include-private-dso``/``--keep-extracted``), the removed-library gate, and
    the ADR-023 instantiation-manifest analysis. They bite only when ``compare``'s
    operands are directories or packages (the per-library fan-out); on single-file
    inputs they are inert. Declared once here so ``compare`` and the internal
    release engine share one surface (ADR-037 D7). Applied bottom-up, so listed in
    reverse of displayed order.

    CLI cleanup phase two, PR J: ``--bundle-system-providers``/
    ``--bundle-cohort`` are gone from this group -- topology, not a per-run
    input, per this plan's "belongs somewhere else" test. Sourced only from
    ``.abicheck.yml``'s ``bundle:`` block now
    (:data:`abicheck.buildsource.build_config.BuildConfig.bundle_system_providers`/
    ``bundle_cohorts``, resolved onto
    :class:`abicheck.cli_helpers_compare.ResolvedCompareConfig`).
    """
    func = click.option(
        "--no-bundle-analysis",
        "no_bundle_analysis",
        is_flag=True,
        default=False,
        help="Skip bundle-level cross-library analysis (debug/parity escape hatch). "
        "Bundle findings catch intra-bundle symbol removals, signature drift "
        "across DSO boundaries, type drift across siblings, provider migration, "
        "and manifest mismatches. (directory/package inputs only)",
    )(func)
    func = click.option(
        "--instantiation-manifest",
        "manifest_path",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="ABI instantiation manifest (YAML/JSON) listing symbols the release "
        "publicly promises (ADR-023). Renamed from --manifest (CLI cleanup "
        "phase two, PR J): the bare spelling collided with aggregate's own "
        "--manifest and the product's several other manifest-shaped concepts "
        "(dump manifest, run plan, bundle facts, project config). "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--bundle-facts-out",
        "bundle_facts_out",
        type=click.Path(path_type=Path),
        default=None,
        help="Persist this run's OLD-side bundle facts (per-library snapshots "
        "plus the instantiation manifest, if any) to PATH (G38 Phase 2, "
        "ADR-023 amendment) for a later stored-baseline bundle comparison. "
        "Additive output alongside the ordinary live-vs-live comparison; "
        "no-op with --no-bundle-analysis. (directory/package inputs only)",
    )(func)
    func = click.option(
        "--keep-extracted",
        "keep_extracted",
        is_flag=True,
        default=False,
        help="Keep extracted temporary files for debugging. "
        "(directory/package inputs only)",
    )(func)
    func = click.option(
        "--include-private-dso",
        "include_private_dso",
        is_flag=True,
        default=False,
        help="Include private (non-public) shared objects from non-standard "
        "paths. (directory/package inputs only)",
    )(func)
    func = click.option(
        "--devel-pkg",
        "devel_pkg",
        multiple=True,
        type=SIDED_EXISTING_PATH_PARAM,
        help="Development package with headers, scoped per side with an "
        "'old='/'new=' prefix (e.g. --devel-pkg old=a-dev.rpm --devel-pkg "
        "new=b-dev.rpm). Directory/package inputs only (ADR-040).",
    )(func)
    func = click.option(
        "--debug-info",
        "debug_info",
        multiple=True,
        type=SIDED_EXISTING_PATH_PARAM,
        help="Debug info package (RPM/Deb/tar), scoped per side with an "
        "'old='/'new=' prefix (e.g. --debug-info old=a-dbg.rpm --debug-info "
        "new=b-dbg.rpm). Directory/package inputs only (ADR-040).",
    )(func)
    func = click.option(
        "--fail-on-removed-library/--no-fail-on-removed-library",
        "fail_on_removed",
        default=False,
        help="Exit 8 when a library present in old_dir is absent in new_dir. "
        "(directory/package inputs only)",
    )(func)
    return func


def debug_resolution_options(func: F) -> F:
    """Separate-debug-file resolution (ADR-021a): roots + debuginfod + format.

    Currently a ``compare``-only family — it resolves *local* ELF debug
    artifacts, which the package-oriented (``compare-release``) and
    snapshot-oriented (``appcompat``) commands do not take. It
    lives here so the moment a second command needs it there is one definition to
    compose, not a copy to drift (ADR-037 D3).
    """
    func = click.option(
        "--dwarf",
        "debug_format",
        flag_value="dwarf",
        hidden=True,
        help="Force DWARF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--ctf",
        "debug_format",
        flag_value="ctf",
        hidden=True,
        help="Force CTF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--btf",
        "debug_format",
        flag_value="btf",
        default=None,
        hidden=True,
        help="Force BTF debug format for both sides (ELF only).",
    )(func)
    func = click.option(
        "--debug-format",
        "debug_format_opt",
        type=click.Choice(["auto", "dwarf", "btf", "ctf"], case_sensitive=False),
        default=None,
        hidden=True,
        help="Force the ELF debug format for both sides (auto=pick best available). "
        "Supersedes the individual --btf/--ctf/--dwarf flags. Demoted to the "
        "debug.format config key (ADR-040 L2); this flag still overrides it.",
    )(func)
    func = click.option(
        "--debuginfod-url",
        "debuginfod_url",
        default=None,
        hidden=True,
        help="debuginfod server URL (overrides DEBUGINFOD_URLS env var). Demoted to "
        "the debug.debuginfod_url config key (ADR-040 L2); this flag still overrides it.",
    )(func)
    func = click.option(
        "--debuginfod/--no-debuginfod",
        "debuginfod",
        default=False,
        hidden=True,
        help="Enable debuginfod network resolution for debug info (opt-in). Demoted "
        "to the debug.debuginfod config key (ADR-040 L2); --debuginfod/--no-debuginfod "
        "still overrides it either way.",
    )(func)
    func = click.option(
        "--debug-root",
        "debug_root",
        multiple=True,
        type=SIDED_PATH_PARAM,
        help="Directory containing separate debug files (build-id trees, "
        "path-mirror, dSYM bundles). Applies to both sides; scope to one with an "
        "'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --debug-root old=dbg1 --debug-root new=dbg2). Repeatable (ADR-040).",
    )(func)
    func = click.option(
        "--dwarf-only/--no-dwarf-only",
        "dwarf_only",
        default=False,
        hidden=True,
        help="Force DWARF-only mode for both sides: use DWARF debug info "
        "as primary data source even when headers are available. Demoted to the "
        "debug.dwarf_only config key (ADR-040 L2); --dwarf-only/--no-dwarf-only "
        "still overrides it either way (e.g. --no-dwarf-only restores header parsing "
        "for a one-off run).",
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


def app_usage_scope_options(func: F) -> F:
    """Add the ADR-043 app-usage/required-symbol scoping options to ``compare``.

    ``--used-by`` and ``--required-symbol``/
    ``--required-symbols`` are mutually exclusive scoping mechanisms folding
    the former standalone ``appcompat``/``plugin-check`` commands into
    ``compare``. Decorators apply bottom-up, so they are listed here in
    reverse of their displayed order.
    """
    func = click.option(
        "--required-symbols",
        "required_symbols_file",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="File of required symbols, one per line (blank lines and '#' "
        "comments ignored). Combined with any --required-symbol values.",
    )(func)
    func = click.option(
        "--required-symbol",
        "required_symbols_opt",
        multiple=True,
        help="An exported linker symbol a plugin host resolves via dlopen/dlsym "
        "and requires (repeatable; folds `plugin-check`). Scopes the "
        "comparison to this explicit entrypoint contract instead of the "
        "full diff. Mutually exclusive with --used-by.",
    )(func)
    func = click.option(
        "--used-by",
        "used_by_apps",
        multiple=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Application binary whose actual imports/required symbol versions "
        "scope the comparison (repeatable; folds `appcompat`). The full "
        "library comparison still runs once; the worst app-scoped result "
        "becomes the primary verdict/exit code, with the full verdict and "
        "unrelated changes kept as informational context. OLD/NEW may be "
        "real library binaries or JSON snapshots carrying binary evidence "
        "(a `dump` of a real library, not headers-only). Mutually "
        "exclusive with --required-symbol/--required-symbols.",
    )(func)
    return func


def build_source_dump_options(func: F) -> F:
    """Add the ``--build-info`` / ``--sources`` embed options to ``dump``.

    Source-tree-centric inputs (ADR-028..033 amendment): ``--sources`` is a
    source checkout — L4 source ABI replay and the L5 graph are run inline and
    embedded; ``--build-info`` is an optional build dir / ``compile_commands.json``
    / pre-built pack supplying L3 (auto-discovered inside the source tree when
    omitted). Either flag also accepts, and auto-detects, a build-emitted
    ``abicheck_inputs/`` Flow-2 pack directory or a pre-built ``BuildSourcePack``
    directory (from an internal/producer-side collection step) — both are
    ingested and validated automatically, no separate ``inputs validate``/
    ``merge`` step needed (ADR-043 D1). Embedding makes the ``.abi.json``
    self-contained, so a later ``compare old.json new.json`` carries the facts
    with no out-of-band directories. Applied bottom-up, so listed in reverse of
    display.
    """
    func = click.option(
        "--depth",
        "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Evidence-depth dial (same vocabulary as `compare`/`scan --depth`): "
        "binary=symbols only, headers=+header AST (default), build=+build "
        "context, source=+source replay & call graph.",
    )(func)
    func = click.option(
        "--allow-build-query",
        "allow_build_query",
        is_flag=True,
        default=False,
        hidden=True,  # deprecated no-op (ADR-032 amended): build query is now automatic
        help="Deprecated and ignored. Build-system queries now run automatically "
        "when --sources is given (abicheck infers and runs cmake/make/bazel "
        "itself); no flag is needed. Kept as a no-op for backward compatibility.",
    )(func)
    func = click.option(
        "--config",
        "build_config",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Path to the project `.abicheck.yml` (ADR-037 D4): build system, "
        "query command, compile-DB location, plus the stable severity/scope/"
        "suppression/source settings. Defaults to `.abicheck.yml` at the "
        "--sources tree root for non-executing settings; build.query runs ONLY "
        "from an explicit --config -- an auto-discovered one never executes "
        "it, and no CLI flag can authorize it (ADR-032 D5).",
    )(func)
    func = click.option(
        "--build-target",
        "build_targets",
        multiple=True,
        metavar="TARGET",
        help="Explicit build-system root target(s) to scope L3 evidence "
        "collection to, instead of a workspace-wide query (P0.2; Bazel "
        "only so far, e.g. '//:math'). Repeatable — each root's transitive "
        "dependency closure is unioned. CLI equivalent of `.abicheck.yml` "
        "build.targets; overrides it when both are given. Without this, a "
        "multi-package workspace with fixture/test targets alongside the "
        "real library is collected in full, which can pollute L3 evidence "
        "with unrelated compile units.",
    )(func)
    func = click.option(
        "--sources",
        "sources",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Source checkout to run source-ABI replay and build the call "
        "graph over, embedding both inline. (An existing pack directory — e.g. "
        "from the abicheck-cc wrapper or Clang plugin — is auto-detected by "
        "its manifest.json and loaded as that pack instead.)",
    )(func)
    func = click.option(
        "--build-info",
        "build_info",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Optional build context: a build dir, a compile_commands.json, "
        "or a pre-captured pack. Auto-discovered inside the --sources tree when "
        "omitted. When it resolves to a compile database and -H/--header is "
        "given, that database also parameterizes the header parse with the "
        "build's exact flags (scope it with --compile-db-filter).",
    )(func)
    return func


def header_graph_options(func: F) -> F:
    """The shared, deprecated ``--header-graph``/``--header-graph-includes`` pair.

    G29 Phase A: the L2 header-only semantic graph
    (:func:`~abicheck.buildsource.header_graph.build_header_only_graph`) — and
    its include-file extension — is now always built whenever headers are
    available (``--depth headers`` or deeper), for both ``compare`` and
    ``dump``. These two flags are no longer opt-in toggles; they are kept as
    *hidden*, inert no-op shims (``hidden=True`` — absent from ``--help`` and
    from ``tests/test_cli_contract.py``'s ``_OPTION_SET_SNAPSHOT``) purely so
    an existing script/CI invocation that still passes ``--header-graph``
    doesn't hard-fail with "no such option". Passing either flag prints a
    one-line deprecation note to stderr and otherwise changes nothing — the
    graph is built identically whether or not the flag is given. Planned
    removal: two minor releases after this change ships (track in
    CHANGELOG.md). Shared by ``compare`` and ``dump`` so the two flags' spelling
    can never drift between them. Applied bottom-up, so listed in reverse of
    display.
    """
    func = click.option(
        "--header-graph-includes",
        "header_graph_includes_deprecated",
        is_flag=True,
        default=False,
        hidden=True,
        help="Deprecated, no-op: the include-file graph pass is now always run "
        "alongside --header-graph's replacement (always-on L2 header graph). "
        "Planned removal: two minor releases out.",
    )(func)
    func = click.option(
        "--header-graph",
        "header_graph_deprecated",
        is_flag=True,
        default=False,
        hidden=True,
        help="Deprecated, no-op: the L2 header-only semantic graph (ADR-041 "
        "addendum) is now always built for --depth headers and above. Planned "
        "removal: two minor releases out.",
    )(func)
    return func


def warn_deprecated_header_graph_flags(
    header_graph_deprecated: bool, header_graph_includes_deprecated: bool
) -> None:
    """Emit a deprecation note for the inert ``--header-graph``/``-includes`` shim.

    Called from ``compare``/``dump_cmd`` bodies (not the Click callback
    itself, so it runs after Click has finished parsing) whenever either
    flag was passed on the command line. Behavior is identical either way —
    this is purely a stderr note, per the "hidden shim must not control
    behavior" policy (AGENTS.md deprecation convention).
    """
    if header_graph_deprecated or header_graph_includes_deprecated:
        click.echo(
            "Note: --header-graph/--header-graph-includes are deprecated "
            "no-ops — the L2 header-only semantic graph is now always built "
            "for --depth headers and above. Planned removal: two minor "
            "releases out.",
            err=True,
        )


def evidence_options(func: F) -> F:
    """The shared two-sided evidence family (ADR-037 D3's ``@evidence_options``).

    The single source of truth for the depth/source/build-info surface a
    *two-sided* verdict command exposes: ``--depth`` plus the per-side
    ``--old/new-sources`` and ``--old/new-build-info`` packs. ``dump`` is
    single-sided (one artifact, plus the build-query knobs) so it composes the
    sibling :func:`build_source_dump_options` instead — they are deliberately not
    one decorator because their surfaces differ (per-side vs build-query), which
    is why ``evidence`` is a registered-but-not-required family (only commands
    that take source depth compose it).

    By default ``compare old.json new.json`` reads build-info + source facts
    **embedded** in each snapshot (single-artifact UX). The optional side-aware
    ``--build-info`` and ``--sources`` (ADR-040) point at out-of-band pack
    directories to supply or override those facts — for both sides, or per side
    with an ``old=``/``new=`` prefix; ``--depth`` selects how deep the inline
    collection runs (ADR-037 D5). All folded into the verdict as ordinary
    findings, never overriding artifact-backed ABI verdicts (ADR-028 D3).
    Applied bottom-up, so listed in reverse of displayed order.
    """
    func = click.option(
        "--depth",
        "depth",
        type=DEPTH_PARAM,
        default=None,
        help="Evidence-depth dial: binary=symbols only, headers=+header AST "
        "(default), build=+build context, source=+source replay & call graph. "
        "Deeper-than-headers needs --sources or --build-info.",
    )(func)
    func = click.option(
        "--sources",
        "sources",
        multiple=True,
        type=SIDED_SOURCES_PARAM,
        help="Source checkout for --depth build/source (collected inline, "
        "embedding build/source/graph facts) or a pre-built `collect` pack, "
        "overriding embedded. Applies to both sides; scope to one with an "
        "'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --sources old=src_v1 --sources new=src_v2) (ADR-040).",
    )(func)
    func = click.option(
        "--build-info",
        "build_info",
        multiple=True,
        type=SIDED_BUILD_INFO_PARAM,
        help="Out-of-band build context: a build dir, a compile_commands.json, "
        "or a pack, overriding embedded. Applies to both sides; scope to one "
        "with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --build-info old=b1 --build-info new=b2) (ADR-040).",
    )(func)
    return func


#: Back-compat alias for the pre-ADR-037-D3 name. ``evidence_options`` is the
#: canonical spelling (the D3 table); this keeps existing imports working.
build_source_compare_options = evidence_options


def _stash_variant_in_context(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> None:
    """``--old-variant``/``--new-variant``'s click ``callback=``: stashes
    *value* on ``ctx.meta`` under *param*'s own name instead of exposing it
    to the decorated command's own ``**kwargs`` (``expose_value=False``).

    Neither flag means anything to a single-pair `compare`/`run_compare`
    call -- only the directory/package release fan-out
    (`frontends.cli.commands.compare._dispatch_release_compare`) reads them
    back via `variant_kwargs_from_context`, off the identical `ctx` -- so
    routing them through `ctx.meta` instead of `**kwargs` means `run_compare`
    (whose own typed signature has no matching parameters) never has to see
    or strip them.
    """
    ctx.meta[f"abicheck.variant.{param.name}"] = value


def variant_options(func: F) -> F:
    """``--old-variant``/``--new-variant`` (ADR-062 A1.7): which `VariantRef`
    to compare when a stored `ProjectSnapshot` package operand declares more
    than one -- release-fanout-specific, same as this module's other option
    groups (a plain directory/package release comparison, ADR-054's own
    admission bar for what belongs here). Not applied via ``@variant_options``
    on ``compare_cmd`` itself -- ``cli.py`` calls it directly on the already-
    registered ``compare`` command instead, once `frontends/cli/commands/
    compare.py` is fully loaded, so that already-at-cap module owes this
    flag family neither an import nor a decorator line. See
    `variant_kwargs_from_context`/`frontends/cli/commands/compare.py`'s own
    use for the full read-back contract.
    """
    func = click.option(
        "--old-variant",
        "old_variant",
        default=None,
        metavar="VARIANT_ID",
        expose_value=False,
        callback=_stash_variant_in_context,
        help="Which build variant to compare when OLD is a stored "
        "ProjectSnapshot package directory declaring more than one. "
        "Defaults to the package's only variant when it declares exactly "
        "one; a usage error otherwise. No-op for a live directory/archive/"
        "single-file operand.",
    )(func)
    func = click.option(
        "--new-variant",
        "new_variant",
        default=None,
        metavar="VARIANT_ID",
        expose_value=False,
        callback=_stash_variant_in_context,
        help="The --old-variant counterpart for NEW.",
    )(func)
    return func


def variant_kwargs_from_context(ctx: click.Context) -> dict[str, str | None]:
    """``--old-variant``/``--new-variant``'s current values, stashed on
    *ctx* by `_stash_variant_in_context` -- what
    `frontends.cli.commands.compare._dispatch_release_compare` merges into
    its own kwargs before calling `compare_release_cmd.callback` (ADR-062
    A1.7), since `variant_options`' `expose_value=False` means neither flag
    ever reaches a decorated command's own `**kwargs`.
    """
    return {
        "old_variant": ctx.meta.get("abicheck.variant.old_variant"),
        "new_variant": ctx.meta.get("abicheck.variant.new_variant"),
    }
