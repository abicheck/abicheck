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
``app_usage_scope_options``,
``build_source_dump_options``, and ``evidence_options`` (the ADR-037 D3
canonical name for the pre-existing ``build_source_compare_options`` alias,
kept here too) --
every stacked-decorator option group that adds no edge back into the
CLI-registration import cycle.

Deliberately a **leaf**, the same shape as this package's
``contract.py``/``secondary_output.py`` siblings: it restates the one-line
``F`` TypeVar rather than importing it back from its former home, so the
split adds no edge to the CLI-registration import cycle.
:mod:`abicheck.cli_options` re-exports every name here, so each existing
caller -- and each existing test importing them from there -- is
unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

import click

from .params import (
    DEPTH_PARAM,
    SIDED_BUILD_INFO_PARAM,
    SIDED_PATH_PARAM,
    SIDED_SOURCES_PARAM,
    SIDED_STR_PARAM,
)

F = TypeVar("F", bound=Callable[..., object])


def release_options(func: F) -> F:
    """Directory/package (release) comparison knobs, folded onto ``compare``.

    The release-only options the removed ``compare-release`` command exposed:
    the ADR-023 instantiation-manifest analysis and the release summary's
    finding cap (package extraction's own two inputs left this group in
    Phase 7n -- see the comment below). They bite only when ``compare``'s
    operands are directories or packages (the per-library fan-out); on
    single-file inputs they are inert. Declared once here so ``compare`` and
    the internal release engine share one surface (ADR-037 D7). Applied
    bottom-up, so listed in reverse of displayed order.

    CLI cleanup phase two, PR J: ``--bundle-system-providers``/
    ``--bundle-cohort`` are gone from this group -- topology, not a per-run
    input, per this plan's "belongs somewhere else" test. Sourced only from
    ``.abicheck.yml``'s ``bundle:`` block now
    (:data:`abicheck.buildsource.build_config.BuildConfig.bundle_system_providers`/
    ``bundle_cohorts``, resolved onto
    :class:`abicheck.cli_helpers_compare.ResolvedCompareConfig`).

    Phase 7d (one-comparison-product.md §4.1, ADR-068 D5): ``--fail-on-
    removed-library``/``--on-incomplete-scope``/``--include-private-dso``
    are gone from this group too -- ``gate.fail_on_removed_library``
    (prerequisite vision A-S4/ADR-065 S4 landed 2026-09-06),
    ``scope.on_incomplete``, and ``release.include_private_dso`` in
    ``.abicheck.yml`` are their only source now, no surviving CLI override,
    resolved the same way onto :class:`abicheck.cli_helpers_compare.
    ResolvedCompareConfig`. ``--dso-only`` (the fourth Phase 7d topology
    flag, ``release.dso_only``) lived in ``cli_options.set_input_options``
    instead, not here.

    Phase 7d remainder (one-comparison-product.md §4.1, ADR-068 D5, ruled on
    explicitly rather than left "pending"): ``--keep-extracted`` and
    ``--no-bundle-analysis`` are gone from this group too. Neither survives
    D5's three guards -- ``--keep-extracted`` is a local-debug retention
    knob with no per-run evidence content and no stable-property home
    either (guard 2, "no escape hatch": it doesn't disable a decision, it
    just leaves a tempdir on disk); ``--no-bundle-analysis`` is exactly the
    "escape hatch that disables real analysis" D4 rules out -- a bundle
    finding a user wants gone is a suppression-policy decision
    (``--suppress``/``.abicheck.yml`` overrides), not a flag that silently
    drops a whole analysis stage. Both are removed outright, with no config
    replacement -- extraction is now always cleaned up, and bundle analysis
    always runs. ``--instantiation-manifest``/``--bundle-facts-out``/
    ``--bundle-facts-library-manifest`` are ruled the other way -- see each
    option's own docstring below for the explicit per-run-operand-vs-
    config-property call and why.
    """
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
        "(directory/package inputs only)\n\n"
        "Phase 7d (one-comparison-product.md §4.1) classes this CONFIG ("
        '"a declared contract is a project property") with no stated '
        "prerequisite, but its natural home -- a project-config document "
        "-- already has an owner: ADR-049's CompatibilityEvaluationConfig "
        "resolves a whole `contract.*` namespace through its own D7 "
        "precedence tiers (compatibility_evaluation_frontend.py), separate "
        "from this plain BuildConfig/.abicheck.yml schema. Adding a second, "
        "uncoordinated top-level `contract:` block here -- with no D7 "
        "resolver, no receipt, no pack-conflict detection -- would be "
        "exactly the ad hoc config plumbing this workstream's own task "
        "instructions warn against inventing casually, not a mechanical "
        "rename. Kept as a CLI flag pending a real ADR-049-coordinated "
        "design for where a declared instantiation manifest belongs.",
    )(func)
    func = click.option(
        "--bundle-facts-out",
        "bundle_facts_out",
        type=click.Path(path_type=Path),
        default=None,
        help="Persist this run's OLD-side bundle facts (per-library snapshots "
        "plus the instantiation manifest, if any) to PATH (G38 Phase 2, "
        "ADR-023 amendment) for a later stored-baseline bundle comparison. "
        "Additive output alongside the ordinary live-vs-live comparison. "
        "(directory/package inputs only)\n\n"
        "Phase 7d (one-comparison-product.md §4.1) originally classed this "
        'REMOVE ("evidence capture belongs to dump (D2)"). Ruled '
        "explicitly, not left pending: `dump` has no directory/package "
        "fan-out at all today -- its operand is a single binary/header "
        "input, with no equivalent that walks a release tree and writes a "
        "multi-library BundleFacts document -- so this is not a duplicate "
        "spelling of a `dump` capability to collapse, it is the only way "
        "to produce a stored-baseline bundle-facts document for a later "
        "`compare OLD_FACTS NEW_INPUT` bundle comparison. Under D5's own "
        "test it is a per-run operand exactly like `-o/--output`: PATH "
        "names where *this invocation's* evidence capture lands, which is "
        "not a stable project property (it varies by run/CI job, e.g. by "
        "date or build id) and has no config vocabulary to merge into "
        "(guard 1) without inventing one purely to move a path string "
        '(guard 2\'s "no escape hatch" cuts the other way here -- a fixed '
        "config path would make the flag's own PATH argument the escape "
        "hatch). Stays a `compare` CLI flag; revisit only if `dump` grows "
        "a real release fan-out, at which point this becomes a duplicate "
        "spelling of that capability rather than the only one.",
    )(func)
    # one-comparison-product.md Phase 7n: ``--devel-pkg`` and this group's
    # own ``--debug-info`` are gone from *here*. Neither capability is: a
    # development package is a carrier of **header** evidence, so it is one
    # of ``-H/--header``'s transports now, and a debug package is one of
    # ``--debug-info``'s three (see ``debug_resolution_options`` below,
    # which owns that flag now that it covers the whole debug role). Both
    # still reach ``prepare_release_inputs``' ``devel_pkg1``/``devel_pkg2``/
    # ``debug_info1``/``debug_info2`` parameters unchanged -- the merge is
    # in the flags, not in the extraction path (``options/evidence_roles``).
    func = click.option(
        "--max-findings-per-library",
        "max_findings_per_library",
        type=click.IntRange(min=1),
        default=None,
        help="Directory/package inputs only: cap on findings embedded in "
        "each library's findings/findings_view lists in the release "
        "summary (default 10, or $ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY "
        "when set) -- mirrors `scan --max-findings`. Raising it never "
        "changes the verdict/exit code -- only how much of each library's "
        "diff the aggregate release summary itemizes; --output-dir remains "
        "the way to see every library's full, unfiltered report "
        "unconditionally. When truncated, "
        "findings_truncated_kinds/findings_view_truncated_kinds still "
        "report a kind -> count breakdown of what was cut. A genuine "
        "per-run report-size knob (D5 guard 2: it disables no analysis, it "
        "only bounds how much of an unchanged finding set one document "
        "itemizes), unlike this group's config-only topology flags above.",
    )(func)
    # one-comparison-product.md Phase 7: ``--support-promise`` is gone from
    # `compare`'s CLI. ADR-065 D1/D6 (and the flag's own help text) called it
    # "a contract-policy field" -- a project's declared support promise is
    # exactly D5 guard 1's stable project property, not a per-run operand, so
    # ``release.support_promise`` in ``.abicheck.yml`` is its only spelling
    # now, resolved onto ResolvedCompareConfig alongside the Phase 7d
    # ``release.dso_only``/``release.include_private_dso`` siblings. The
    # unregistered release engine (``cli_compare_release.compare_release_cmd``)
    # keeps its own internal parameter, which this fan-out feeds from the
    # resolved config.
    return func


def debug_resolution_options(func: F) -> F:
    """The whole separate-debug-info role, one input (ADR-021a + 7n).

    one-comparison-product.md Phase 7n: this family used to be "roots only"
    (``--debug-root``) while a debug *package* arrived through
    ``release_options``' own ``--debug-info``. A detached debug file, a
    directory of them, and a debug package are three transports of one
    evidence role, so they are one input -- ``--debug-info`` -- routed on
    each operand's content (``options/evidence_roles`` ->
    ``workflows.evidence_transport``). Each transport keeps exactly the
    destination and validation it had: a package still reaches
    ``prepare_release_inputs``' ``debug_info1``/``debug_info2`` extraction,
    a directory still reaches ``debug_resolver``'s build-id-tree/path-mirror
    search, and a named file is resolved by
    ``extract.detached_debug.DetachedDebugFileResolver``, which rejects a
    sidecar whose build-id contradicts the binary's rather than describing a
    different build.

    Currently a ``compare``-only family — the package-oriented
    (``compare-release``) and snapshot-oriented (``appcompat``) commands do
    not take it, and ``dump`` declares its own single-sided ``--debug-info``
    (no release fan-out, so no package transport there). It
    lives here so the moment a second command needs it there is one definition to
    compose, not a copy to drift (ADR-037 D3).

    ADR-068 D5 / one-comparison-product.md Phase 7a: this family used to also
    carry ``--debug-format``/``--debuginfod-url``/``--debuginfod``/
    ``--dwarf-only``, each already-hidden and already fully config-backed
    (``debug.format``/``debug.debuginfod_url``/``debug.debuginfod``/
    ``debug.dwarf_only`` in ``.abicheck.yml``) since their ADR-040 Lever 2
    demotion. Hidden-but-accepted options are still public surface (D5), so
    they were removed outright rather than left hidden — the config keys
    remain the only way to set them for ``compare``.
    """
    func = click.option(
        "--debug-info",
        "debug_info",
        multiple=True,
        type=SIDED_PATH_PARAM,
        help="Separate debug info for a side, in any of its three transports: "
        "a directory to search (build-id tree, path mirror, dSYM bundles), a "
        "detached DWARF debug file (a .debug sidecar), or a debug "
        "package (RPM/Deb/tar; directory/package inputs only). Which one an "
        "operand is comes from its content, not its name. Applies to both "
        "sides; scope to one with an 'old='/'new=' prefix, repeating the flag "
        "per side (e.g. --debug-info old=dbg1 --debug-info new=b-dbg.rpm). "
        "Repeatable (ADR-040).",
    )(func)
    return func


# ADR-068 D4 / one-comparison-product.md Phase 5: ``adr027_compare_options``
# used to live here, carrying ``--explain-patterns`` and then, after that
# moved into ``--view patterns``, only ``--surface-metrics``. Both are gone
# now. ADR-027's public-surface metric-drift findings
# (``public_surface_grew``/``public_surface_shrank``/
# ``undocumented_export_ratio_increased``) are computed on every comparison
# and merged into ``result.changes``, so the flag selected neither analysis
# nor rendering -- §4.1's AUTO classification, carried all the way through
# to the flag's deletion rather than left as an accepted no-op (D5: an
# accepted spelling is public surface).


def app_usage_scope_options(func: F) -> F:
    """Add the ADR-043 app-usage/required-symbol scoping options to ``compare``.

    ``--used-by`` and ``--required-symbol`` are mutually exclusive scoping
    mechanisms folding the former standalone ``appcompat``/``plugin-check``
    commands into ``compare``. Decorators apply bottom-up, so they are
    listed here in reverse of their displayed order.

    ``--used-by-manifest`` (Workstream D-S1) is a third way to name a
    consumer, additive to (never a replacement for) ``--used-by``: each
    manifest is a small JSON document naming one or more consumer binaries
    with optional digest/platform/profile/provider-baseline provenance and
    an advisory/required distinction for an unreadable consumer (see
    :mod:`abicheck.model.consumer_spec`). Manifest-named consumers are
    merged into the same ``--used-by`` pipeline -- they show up in the same
    ``used_by[]``/``consumer_scope`` report block, contribute to the same
    worst-wins scoped gate, and are still mutually exclusive with
    ``--required-symbol``.

    ADR-068 D5 / plan Phase 7h: ``--required-symbols FILE`` (the separate
    file-only flag) is gone. ``--required-symbol`` now accepts ``@FILE`` as
    one of its repeatable values -- two spellings of "name a required
    symbol" collapse into one flag, matching how the file form and the
    inline form always fed the identical contract (see
    :func:`~abicheck.cli_helpers_compare.load_required_symbols`).
    """
    func = click.option(
        "--used-by-manifest",
        "used_by_manifests",
        multiple=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="A JSON document naming one or more consumer binaries "
        "(repeatable), each with optional 'digest'/'platform'/'profile'/"
        "'provider_baseline' provenance and a 'requirement': "
        "'required' (default, an unreadable consumer aborts the run, "
        "same as --used-by) or 'advisory' (an unreadable consumer is "
        "skipped and reported, never aborts the run). Merged into the "
        "same scoping pipeline as --used-by; every listed consumer counts "
        "toward the reported 'N of M consumers affected' summary. "
        "Mutually exclusive with --required-symbol.",
    )(func)
    func = click.option(
        "--required-symbol",
        "required_symbols_opt",
        multiple=True,
        help="An exported linker symbol a plugin host resolves via dlopen/dlsym "
        "and requires (repeatable; folds `plugin-check`). '@FILE' reads "
        "required symbols from FILE, one per line (blank lines and '#' "
        "comments ignored) -- combinable with plain symbol values, but "
        "at most one '@FILE' per invocation. The full library comparison "
        "always determines this run's own verdict/exit code; this "
        "contract's own confirmed/potential/unresolved impact is reported "
        "alongside it (informational), never in place of it. Mutually "
        "exclusive with --used-by.",
    )(func)
    func = click.option(
        "--used-by",
        "used_by_apps",
        multiple=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        help="Application binary whose actual imports/required symbol versions "
        "scope the comparison (repeatable; folds `appcompat`). The full "
        "library comparison always determines this run's own verdict/exit "
        "code, exactly as it would without --used-by; the supplied "
        "application's own confirmed/potential/unresolved impact is "
        "reported alongside it (informational), never in place of it. "
        "OLD/NEW may be real library binaries or JSON snapshots carrying "
        "binary evidence (a `dump` of a real library, not headers-only). "
        "Mutually exclusive with --required-symbol.",
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
        "build's exact flags (scope it with build.compile_db_filter in "
        ".abicheck.yml).",
    )(func)
    return func


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
        help="Out-of-band build evidence: a build dir, a compile_commands.json "
        "or a pack (compile context, overriding embedded), or a probe-matrix "
        "snapshot (build-configuration observations). Which one an operand is "
        "comes from the document, not its name, and both kinds may be given "
        "for one side -- a matrix on both sides folds CXX_STANDARD_FLOOR_RAISED"
        "/API_DEPENDS_ON_CONSUMER_ENV/BEHAVIOURAL_DEFAULT_CHANGED into this "
        "comparison's verdict and report (G2: probe -> compare). Applies to "
        "both sides; scope to one "
        "with an 'old='/'new=' prefix, repeating the flag per side "
        "(e.g. --build-info old=b1 --build-info new=b2) (ADR-040).",
    )(func)
    return func


#: Back-compat alias for the pre-ADR-037-D3 name. ``evidence_options`` is the
#: canonical spelling (the D3 table); this keeps existing imports working.
build_source_compare_options = evidence_options


def _resolve_sided_variant(
    pairs: Sequence[tuple[str, str]],
) -> tuple[str | None, str | None]:
    """Resolve ``--variant``'s ``(side, variant_id)`` pairs to ``(old, new)``.

    The same "base + per-side override" model every other side-aware option
    on ``compare`` uses (``--header``, ``--include``, ``--version``,
    ``--sources``, ``--build-info``, ``--debug-info``, ``--devel-pkg``,
    ``--debug-root``, ``--probe-matrix``, ``--dump-manifest``): a bare or
    ``both=`` value applies to *both* sides, ``old=``/``new=`` override that
    one side, and the last value wins per bucket. Unset stays ``None`` --
    unlike ``--version``, a variant id has no per-side default to fall back
    on (the package's sole declared variant is the default, resolved much
    later by the release fan-out, and "no variant stated" is what selects
    it).

    A standalone function rather than an inline loop inside the callback
    because it is the reusable ordering primitive the behavior actually
    rests on: ``TestResolveSidedVariantProperties`` in
    ``tests/test_cli_compare_release_project_snapshot_package.py`` states
    its contract as invariants (last-wins per bucket, a later ``both=``
    re-basing both sides, a per-side override surviving an earlier base,
    and independence from how the two buckets interleave) rather than only
    exercising it through one ``compare`` invocation -- AGENTS.md's
    "Primitive-level property tests" rule for exactly this shape of helper.
    """
    old: str | None = None
    new: str | None = None
    for side, variant in pairs:
        if side in ("both", "old"):
            old = variant
        if side in ("both", "new"):
            new = variant
    return old, new


def _stash_variant_in_context(
    ctx: click.Context, param: click.Parameter, value: Sequence[tuple[str, str]]
) -> None:
    """``--variant``'s click ``callback=``: resolves *value*'s side-scoped
    pairs and stashes the per-side result on ``ctx.meta`` instead of exposing
    it to the decorated command's own ``**kwargs`` (``expose_value=False``).

    ``--variant`` means nothing to a single-pair `compare`/`run_compare`
    call -- only the directory/package release fan-out
    (`frontends.cli.commands.compare._dispatch_release_compare`) reads it
    back via `variant_kwargs_from_context`, off the identical `ctx` -- so
    routing it through `ctx.meta` instead of `**kwargs` means `run_compare`
    (whose own typed signature has no matching parameter) never has to see
    or strip it.

    An empty variant id (``--variant old=``, or a bare ``--variant ""``) is
    a usage error here rather than a silently-``None`` selection, matching
    how `dump --provenance` (Phase 7f) validates its own grammar eagerly in
    a callback: a package declaring several variants would otherwise fall
    back to "no variant stated" and fail much later with a message naming
    neither the empty value nor the flag that supplied it.
    """
    for side, variant in value:
        if not variant:
            raise click.UsageError(
                f"--variant: empty variant id in "
                f"{'' if side == 'both' else side + '='}"
                f"'' -- give a VARIANT_ID declared by the stored "
                f"ProjectSnapshot package (e.g. --variant old=v1).",
                ctx=ctx,
            )
    old, new = _resolve_sided_variant(value)
    ctx.meta["abicheck.variant.old_variant"] = old
    ctx.meta["abicheck.variant.new_variant"] = new


def variant_options(func: F) -> F:
    """``--variant`` (ADR-062 A1.7): which `VariantRef` to compare when a
    stored `ProjectSnapshot` package operand declares more than one --
    release-fanout-specific, same as this module's other option groups (a
    plain directory/package release comparison, ADR-054's own admission bar
    for what belongs here). Not applied via ``@variant_options`` on
    ``compare_cmd`` itself -- ``cli.py`` calls it directly on the already-
    registered ``compare`` command instead, once `frontends/cli/commands/
    compare.py` is fully loaded, so that already-at-cap module owes this
    flag family neither an import nor a decorator line. See
    `variant_kwargs_from_context`/`frontends/cli/commands/compare.py`'s own
    use for the full read-back contract.

    **ADR-068 D5 / plan Phase 7j:** this was the pair ``--old-variant``/
    ``--new-variant`` until that slice. Variant selection is genuinely
    per-run scope (ADR-065), so it stays on the CLI -- but it was the last
    two-sided input on ``compare`` still spelled as two flags instead of one
    ``old=``/``new=``-prefixed option, i.e. one concept represented twice.
    The pair is gone with no alias (exit 64), the same way ADR-040 Lever 1
    collapsed ``--old-header``/``--new-header`` and ``--old-version``/
    ``--new-version`` before it. The *unregistered* release engine
    (`cli_compare_release.py`) keeps its own per-side
    ``--old-variant``/``--new-variant``, exactly as it kept per-side
    ``--old-version``/``--new-version`` through that same lever.
    """
    func = click.option(
        "--variant",
        "variant",
        multiple=True,
        type=SIDED_STR_PARAM,
        expose_value=False,
        callback=_stash_variant_in_context,
        help="Which build variant to compare when an operand is a stored "
        "ProjectSnapshot package directory declaring more than one. Scope "
        "to one side with an 'old='/'new=' prefix, repeating the flag per "
        "side (e.g. --variant old=v1 --variant new=v2); a bare value "
        "applies to both. Defaults to the package's only variant when it "
        "declares exactly one; a usage error otherwise. No-op for a live "
        "directory/archive/single-file operand.",
    )(func)
    return func


def variant_kwargs_from_context(ctx: click.Context) -> dict[str, str | None]:
    """``--variant``'s resolved per-side values, stashed on *ctx* by
    `_stash_variant_in_context` -- what
    `frontends.cli.commands.compare._dispatch_release_compare` merges into
    its own kwargs before calling `compare_release_cmd.callback` (ADR-062
    A1.7), since `variant_options`' `expose_value=False` means the flag
    never reaches a decorated command's own `**kwargs`.

    The *keys* stay ``old_variant``/``new_variant``: the unregistered
    release engine's own per-side parameters are unchanged by Phase 7j's
    CLI-spelling merge, so only the user-facing flag collapsed.
    """
    return {
        "old_variant": ctx.meta.get("abicheck.variant.old_variant"),
        "new_variant": ctx.meta.get("abicheck.variant.new_variant"),
    }
