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

"""Helper functions for the ``dump`` CLI command (split from cli.py)."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import click

from .dumper import dump
from .errors import AbicheckError

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .model import AbiSnapshot
    from .service_scan import CompileContext


class _ExpandHeaderInputs(Protocol):
    def __call__(self, inputs: list[Path]) -> list[Path]: ...


class _PopulateDependencyInfo(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        so_path: Path,
        search_paths: list[Path],
        sysroot: Path | None,
        ld_library_path: str,
    ) -> None: ...


class _StampProvenance(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        *,
        git_tag: str | None,
        build_id: str | None,
        no_git: bool,
    ) -> None: ...


class _WriteSnapshotOutput(Protocol):
    def __call__(
        self,
        snap: AbiSnapshot,
        output: Path | None,
        build_info: Path | None,
        sources: Path | None,
        build_config: Path | None,
        allow_build_query: bool,
        collect_mode: str,
        build_query: str | None = ...,
        build_compile_db: str | None = ...,
        extractor: str = ...,
        inputs_pack: Path | None = ...,
        depth: str | None = ...,
    ) -> None: ...


def _user_define_flags(
    gcc_option_tokens: tuple[str, ...], user_gcc_options: str | None
) -> list[str]:
    """The user's *global* define-affecting flags for the ADR-039 collector.

    Combines the ``-D``/``-U`` in the ``--gcc-options`` string with the repeatable
    ``--gcc-option`` tokens, **in the same order the real dump applies them** —
    ``dumper._castxml_cmd`` appends ``gcc_options`` first, then
    ``gcc_option_tokens`` (see ``dumper.py``), so the collector must too (Codex
    review #498). Order is significant because ``defines_from_flags`` honours
    ``-D``/``-U`` sequence: ``--gcc-options=-DKEEP --gcc-option=-UKEEP`` must leave
    ``KEEP`` *inactive* on both the parse and the harvest, else the reconciler
    would add back a field the real parse pruned. These flags are applied on top
    of the compile-DB intersection, so a user ``-UKEEP`` also overrides a database
    ``-DKEEP``. The auto-derived first-header build context is deliberately
    excluded (it must not be unioned snapshot-wide).

    A malformed ``--gcc-options`` (e.g. an unbalanced quote) must not abort the
    dump — ``shlex.split`` errors are swallowed and only the tokens are used
    (CodeRabbit review)."""
    flags: list[str] = []
    if user_gcc_options:
        try:
            flags += shlex.split(user_gcc_options)
        except ValueError:
            pass  # bad optional define flags are skipped, not fatal
    flags += list(gcc_option_tokens)
    return flags


def _attach_build_context(
    snap: AbiSnapshot,
    compile_db: str | Path,
    headers: list[Path],
    extra_flags: list[str],
    source_filter: str | None = None,
) -> None:
    """ADR-039 collection layer: harvest the build's active ``-D`` set and scan the
    public headers for ``#ifdef``-guarded record fields, attaching both to *snap*.

    Best-effort and additive — a plain context-free dump (no compile DB) never
    reaches here, and an empty harvest leaves the snapshot's defaults untouched, so
    the pass is a safe no-op unless real build evidence is found. *source_filter*
    (``--compile-db-filter``) selects the same compile-DB entries the header parse
    used."""
    from .header_conditionals import collect_build_context

    bc_defines, bc_conditional = collect_build_context(
        headers, compile_db, extra_flags=extra_flags, source_filter=source_filter
    )
    if bc_defines:
        snap.build_context_defines = bc_defines
    if bc_conditional:
        snap.conditional_fields = bc_conditional


def resolve_dump_debug_format(
    debug_format_opt: str | None,
    debug_format: str | None,
) -> str | None:
    """Reconcile --debug-format selector with legacy --btf/--ctf/--dwarf flags.

    The selector supersedes the legacy flags whenever it is given: an explicit
    "auto" returns to auto-detection (None) even if a legacy flag is also
    present; only when the selector is absent do the legacy flags apply.
    """
    if debug_format_opt is not None:
        return None if debug_format_opt.lower() == "auto" else debug_format_opt
    return debug_format


def resolve_dump_depth(
    depth: str | None,
    default_mode: str,
) -> str:
    """Resolve the ``--depth`` dial into the internal collect-mode value.

    ``--depth`` is the friendly evidence-depth dial (same vocabulary as
    ``scan --depth``: binary/headers/build/source); it expands to the
    underlying ADR-033 collect mode via the shared ``scan_levels`` mapping so the
    commands stay consistent. When no depth preset is supplied, the command's
    *default_mode* is returned (``dump`` embeds at ``source-target``;
    ``compare`` reads at ``off``).
    """
    from .buildsource.scan_levels import (
        EvidenceDepth,
        SourceScope,
        depth_to_method,
        level_to_collect_mode,
    )

    if depth is None:
        return default_mode
    evidence_depth = EvidenceDepth(depth)
    method = depth_to_method(evidence_depth)
    if method is None:
        # headers/binary depth reaches no source method (L2 is intrinsic) --
        # collect nothing.
        return "off"
    # dump/compare always resolve --depth source at target scope (ADR-043 D3):
    # the fix for the zero-TU defect where an explicit deep depth without a
    # change seed silently selected no translation units.
    return level_to_collect_mode(method, evidence_depth, source_scope=SourceScope.TARGET)


def evidence_depth_label(
    snap: AbiSnapshot, build_source: BuildSourcePack | None = None,
) -> str:
    """Report which evidence depth a snapshot *actually* carries (CLI-audit P2).

    Computed purely from what was actually resolved -- ``binary``/
    ``headers``/``build``/``source`` -- rather than echoing back the
    requested ``--depth``: an explicit ``--depth source`` with no usable
    source facts still produces a snapshot that only reaches ``headers`` (or
    ``binary``), and this makes that honest instead of silently overstating
    what was collected.

    *build_source*, when given, overrides ``snap.build_source`` -- ``compare``
    can resolve an out-of-band ``--old/new-sources``/``--old/new-build-info``
    pack that is never attached back to the snapshot object itself
    (``_resolve_side_pack`` returns it standalone); without this override, a
    compare run using only out-of-band packs would report the depth of the
    *unrelated* embedded (or absent) snapshot payload instead of the pack
    that was actually used (Codex review). Defaults to ``snap.build_source``
    for the plain single-artifact (embedded-only) case ``dump -o`` uses.

    Uses the same payload-emptiness checks as ``_write_snapshot_output``'s own
    fail-loud warning (``cli._layer_payload_empty``): a coverage row / field
    can be non-``None`` while the embedded payload carries no real facts (e.g.
    ``_run_inline_source_abi`` returns an empty ``SourceAbiSurface()`` when
    clang is unavailable after L3 was found) -- checking presence alone would
    overstate ``source``/``build`` for a layer that ran but linked nothing
    (CodeRabbit review).

    ``snap.parsed_with_build_context`` (ADR-020a/039: ``-p``/``--compile-db``,
    a much older, narrower build-context mechanism than the ``BuildSourcePack``
    machinery above -- it harvests the active ``-D`` set and ``#ifdef``-guarded
    fields for the L2 header parse, with no ``BuildEvidence``/compile-unit
    model of its own) also reaches "build": without this, a
    ``dump lib.so -H api.h -p build/`` run has no ``snap.build_source`` at all
    and this function would report "headers", even though the error message
    this feeds (``check_requested_depth_satisfied``) already documents "build
    via --build-info/a compile database" as a valid way to satisfy
    ``--depth build`` (Codex review).
    """
    from .cli import _layer_payload_empty

    if build_source is None:
        build_source = snap.build_source
    if build_source is not None and (
        not _layer_payload_empty(build_source, "L4")
        or not _layer_payload_empty(build_source, "L5")
    ):
        return "source"
    if build_source is not None and not _layer_payload_empty(build_source, "L3"):
        return "build"
    if snap.parsed_with_build_context:
        return "build"
    if snap.from_headers:
        return "headers"
    return "binary"


# Same ordering as buildsource.scan_levels.USER_DEPTHS: each rung is a
# strict superset of the facts below it.
_DEPTH_RANK: dict[str, int] = {"binary": 0, "headers": 1, "build": 2, "source": 3}


def _dump_will_attempt_hybrid_l4_extraction(sources: Path | None) -> bool:
    """True iff ``collect_inline_pack`` would actually run L4 extraction with
    ``extractor="hybrid"`` for the ``--sources`` input.

    The ``--depth source`` + ``--ast-frontend hybrid`` rejection exists to
    stop a real, unsupported hybrid L4 extraction attempt from silently
    degrading — it must fire *only* when one would actually happen, and stay
    quiet otherwise so the more accurate error (or none at all) surfaces
    instead. Only ``--sources`` gates whether L4 replay runs at all:
    ``cli_buildsource.embed_build_source`` passes ``raw_sources`` (derived
    solely from the ``sources`` CLI argument, never from ``build_info``) as
    the one ``sources`` argument ``collect_inline_pack`` forwards to
    ``_run_inline_source_abi``, which returns immediately (``None, []``, no
    extraction) when that is ``None`` — ``--build-info`` only ever feeds L3
    compile-DB resolution and is irrelevant to whether an L4 extractor runs
    (Codex review, fourth finding: a raw ``--build-info`` tree alongside a
    *prebuilt* ``--sources`` pack must not trigger this rejection, since no
    extractor — hybrid or otherwise — ever runs for that combination). Two
    cases where it must NOT fire, each found by review:

    - **Prebuilt --sources pack input** (Codex review): ``embed_build_source``
      treats a ``BuildSourcePack`` directory (``is_pack_dir``) or a
      build-emitted ``abicheck_inputs/`` directory (``_is_inputs_pack_dir``)
      as data to load and filter, not a tree to extract from — ``raw_sources``
      is forced to ``None`` for that shape, so ``_run_inline_source_abi``
      never runs regardless of ``extractor``.
    - **No --sources at all** (Codex review, third finding): a bare
      ``dump lib.so -H api.h --depth source --ast-frontend hybrid`` never
      calls ``collect_inline_pack`` in the first place — L4 was never going
      to run regardless of frontend. Rejecting here would tell the user to
      switch frontends when that would not fix anything; the real problem
      (no source/build evidence at all) is better reported by
      ``check_requested_depth_satisfied``'s own "reached 'headers'/'binary'"
      message, which fires downstream regardless.
    """
    from .buildsource.inline import is_pack_dir
    from .cli_buildsource_helpers import _is_inputs_pack_dir

    return sources is not None and not (
        is_pack_dir(sources) or _is_inputs_pack_dir(sources)
    )


class DumpDepthNotSatisfiedError(click.ClickException):
    """Raised when an explicit ``--depth`` was requested but not reached.

    Exit code 1 (the default for ``ClickException``) — the "requested depth
    not satisfiable" code already documented by ``render_dump_dry_run``'s
    "Output and exit-code behavior" section.
    """


def _l4_source_abi_was_attempted(build_source: BuildSourcePack) -> bool:
    """True when L4 source-ABI extraction genuinely parsed source, regardless
    of whether it linked any declarations to a binary.

    Coverage *status* alone (``PRESENT``/``PARTIAL`` vs ``NOT_COLLECTED``) is
    not enough: ``buildsource.inline._run_inline_source_abi`` stamps L4
    ``PARTIAL`` (never ``NOT_COLLECTED``) both for the *expected*, warn-only
    "ran but 0/N symbols matched" outcome of a source-only ``dump --sources``
    (no binary to link declarations against; see ``_write_snapshot_output``'s
    G21.7 "collected but linked no facts" warning) **and** for a genuinely
    *failed* attempt — the selected extractor missing from ``PATH``, or every
    selected TU failing to parse — which returns the same empty
    ``SourceAbiSurface()`` shape with the same ``PARTIAL`` status (Codex
    review, fifth finding: a missing/failing extractor must not satisfy an
    explicit ``--depth source``, matching representation notwithstanding).

    The reliable signal is the presence of
    ``SourceAbiSurface.coverage["compile_units_parsed"]`` specifically — set
    unconditionally by ``source_replay.run_source_replay`` whenever replay
    actually executes, independent of whether anything downstream matched
    against binary exports (parsing happens before, and regardless of,
    linking). The *key* (not just a non-empty ``coverage`` dict) is what
    matters: it is absent for the tool-unavailable short-circuit, which
    returns a bare ``SourceAbiSurface()`` before replay ever runs, but a
    non-empty ``coverage`` dict populated by a *different* stage —
    ``link_source_abi``'s own ``reachable_declarations``/``matched_symbols``
    stats, stamped on a Flow-2 ``inputs_pack.ingest_inputs_pack()`` pack that
    never went through ``run_source_replay`` at all (pure per-TU-facts
    parsing, no frontend re-run) — must not be mistaken for "replay ran"
    just because it happens to be truthy. ``NOT_COLLECTED`` still covers the
    "no extraction attempted at all" cases (no ``--sources``, no L3 to replay
    against, or ``--ast-frontend hybrid``, which ``_run_inline_source_abi``
    records as ``"skipped"``).

    Falls back to the payload-based ``_layer_payload_empty`` check whenever
    the ``compile_units_parsed`` key is absent — covering both the ingested
    Flow-2 pack above and a hand-built pack (a test fixture, or an
    out-of-band ``--old/new-sources`` pack assembled without going through
    ``inline.py``'s replay) with genuine ``source_abi`` facts but no replay
    coverage stats, so neither is mistaken for "never attempted".
    """
    from .buildsource.model import CoverageStatus, DataLayer
    from .cli import _layer_payload_empty

    cov = build_source.manifest.coverage_for(DataLayer.L4_SOURCE_ABI)
    if cov is not None and cov.status == CoverageStatus.NOT_COLLECTED:
        return False
    surface = build_source.source_abi
    if surface is not None and "compile_units_parsed" in surface.coverage:
        return int(surface.coverage.get("compile_units_parsed", 0) or 0) > 0
    return not _layer_payload_empty(build_source, "L4")


def _gated_source_label(build_source: BuildSourcePack | None, snap: AbiSnapshot) -> str:
    """Recompute the "source" evidence label for the *strict* depth gate.

    ``evidence_depth_label`` honestly reports "source" whenever L4 or L5
    carries facts — correct for its own honesty contract, since genuine
    source-tier collection can legitimately populate L5 (``source_graph``)
    without L4 (``source_abi``): ``source_graph.build_source_graph`` folds
    ``BuildEvidence`` structure into a graph even when the L4 surface found
    nothing. But that L4-or-L5 rule is too permissive for a *gate*: a
    non-empty L5 can also come from a header-only (L2) declaration graph
    that never ran any L4/L5 source-tier replay at all —
    ``service._attach_header_graph`` (``--header-graph`` with no
    ``--sources``/``--build-info``) attaches one directly, and
    ``cli_buildsource.embed_build_source``'s backfill step (see its own
    comment: "a genuine --sources L5 collection in merged always wins; the
    header-only graph fills the gap only when merged carries none") can
    graft that same header-only graph onto an otherwise-real, L3-only
    ``--build-info`` pack — so "L3 present" does not rule out a
    header-only-graph L5 either (Codex review, second finding).

    The reliable signal is whether L4 extraction was genuinely *attempted*
    (``_l4_source_abi_was_attempted``) — a coverage-status check, not a
    payload-emptiness one: a source-only dump legitimately links zero
    declarations (no binary to link against) yet must still satisfy an
    explicit ``--depth source`` the same way it already only warns (not
    errors) about that case; only a *never-attempted* L4 (the header-graph
    cases above) is downgraded here, to "build" (real L3, or a ``-p``/
    ``--compile-db`` build context, per ``evidence_depth_label``) or
    ``headers``/``binary`` (nothing).
    """
    from .cli import _layer_payload_empty

    if build_source is not None and _l4_source_abi_was_attempted(build_source):
        return "source"
    if build_source is not None and not _layer_payload_empty(build_source, "L3"):
        return "build"
    # ADR-020a/039 build-context capture (-p/--compile-db) has no BuildSourcePack
    # of its own to check above, but is still a legitimate "build" evidence
    # source -- see evidence_depth_label's docstring (Codex review).
    if snap.parsed_with_build_context:
        return "build"
    return "headers" if snap.from_headers else "binary"


def check_requested_depth_satisfied(
    depth: str | None, snap: AbiSnapshot, build_source: BuildSourcePack | None = None,
) -> None:
    """Hard-fail when an *explicitly* requested ``--depth`` was not reached.

    Depth-contract (CLAUDE.md / CLI-audit P1): when ``--depth`` is left
    unspecified, degrading to whatever evidence is actually available is
    fine as long as ``evidence_depth_label`` honestly reports it. But once
    the user explicitly asks for ``headers``/``build``/``source``, silently
    writing a weaker snapshot is a lie a downstream baseline/CI consumer has
    no way to detect short of re-deriving the depth themselves — so this
    raises instead of warning. ``--depth binary`` is always satisfied
    (rank 0, the floor). A ``depth`` outside ``_DEPTH_RANK`` (defensively,
    should never happen past CLI parsing) is treated as unconstrained.
    """
    if depth is None:
        return
    requested_rank = _DEPTH_RANK.get(depth)
    if requested_rank is None:
        return
    effective_pack = build_source if build_source is not None else snap.build_source
    # _gated_source_label is called unconditionally, not just when
    # evidence_depth_label already says "source" -- evidence_depth_label's
    # own payload-emptiness check requires *either* L4 *or* L5 to be
    # non-empty, but a zero-match source-only dump (replay parsed TUs, linked
    # nothing because there is no binary to link against, and folded no L5
    # graph either) leaves both empty, so evidence_depth_label reports "build"
    # directly -- which would skip the gated recompute entirely and wrongly
    # reject that valid zero-match dump (CodeRabbit review). _gated_source_label
    # is self-contained (it recomputes straight from build_source, not from
    # evidence_depth_label's verdict) and never returns a *higher* rung than
    # is justified -- for every other case (evidence_depth_label already
    # "source", or genuinely no build_source at all) it reproduces the same
    # result the old evidence_depth_label-first path did; see its own
    # docstring for why evidence_depth_label's L4-or-L5 rule is not
    # trustworthy enough for a hard gate on its own.
    effective = _gated_source_label(effective_pack, snap)
    if _DEPTH_RANK.get(effective, 0) < requested_rank:
        raise DumpDepthNotSatisfiedError(
            f"--depth {depth} was requested but the snapshot only reached "
            f"'{effective}' evidence depth. Supply the evidence this rung "
            "needs (headers via -H/--header, build via --build-info/a "
            "compile database, source via --sources with linkable "
            "declarations) or lower --depth to match what is actually "
            "available."
        )


def fold_dump_provenance_into_json(text: str, depth: str | None, snap: AbiSnapshot) -> str:
    """Record the depth contract this dump actually satisfied (audit finding:
    "depth/scope provenance incomplete" -- a persisted ``.abi.json``/baseline
    manifest didn't record ``requested_depth``/``effective_depth``/``frontend``,
    so a downstream reader had no way to tell how deep it really goes short of
    re-deriving it themselves, the same problem ``check_requested_depth_
    satisfied`` already hard-fails on at dump time).

    JSON-only augmentation -- mirrors ``cli_compare_helpers.
    _fold_evidence_depth_into_json``'s pattern, not a new ``AbiSnapshot``
    field: informational provenance about *this dump run*, not part of the
    versioned snapshot schema, so it needs no ``SCHEMA_VERSION`` bump and
    is silently dropped by ``snapshot_from_dict``'s defensive ``.get()``
    parsing on any later object round-trip (load → re-save), same as
    ``compare``'s own JSON-only ``full_verdict``/``old_evidence_depth``
    folds. ``degraded`` is always ``False`` when *depth* is non-``None``, by
    construction: ``check_requested_depth_satisfied`` (called before this,
    in ``_write_snapshot_output``) already raised if the rank had come up
    short, so reaching this point means it did not -- recorded anyway so a
    reader sees a positive "no, this is not weaker than requested" signal
    rather than an absent field.
    """
    import json

    try:
        payload = json.loads(text)
    except ValueError:
        return text
    effective = evidence_depth_label(snap)
    payload["dump_provenance"] = {
        "requested_depth": depth,
        "effective_depth": effective,
        "degraded": (
            depth is not None
            and _DEPTH_RANK.get(effective, 0) < _DEPTH_RANK.get(depth, 0)
        ),
        "frontend": snap.ast_producer,
        # dump always analyzes the resolved library target -- unlike `scan`,
        # there is no --since/--changed-path narrowing concept here.
        "source_scope": "target",
    }
    return json.dumps(payload, indent=2)


def render_dump_dry_run(
    *,
    so_path: Path | None,
    headers: tuple[Path, ...],
    sources: Path | None,
    build_info: Path | None,
    build_config: Path | None,
    depth: str | None,
    collect_mode: str,
    header_backend: str,
    output: Path | None,
    has_compile_db: bool = False,
) -> Any:
    """Build the ``dump --dry-run`` report (ADR-043 D4): resolve, never execute.

    Cheap, read-only resolution only: classifies the inputs, discovers config,
    shows the resolved depth/collect-mode and available data layers, and
    checks tool availability on PATH. Never runs castxml/clang, a build query,
    or any I/O beyond stat()/PATH lookups.

    ``has_compile_db`` (Codex review): whether ``-p``/``--compile-db`` was
    given, threaded in as a bare presence flag (no DB load, no header
    matching -- that is real work, out of scope for a dry run) so the "would
    definitely fail" check below can tell a ``--depth build`` request backed
    by *some* compile database (which might satisfy it -- unknown without
    actually loading it) from one backed by nothing at all (which never
    can).
    """
    from .cli_helpers_compare import discover_project_config
    from .dry_run import DryRunResult, tool_status

    result = DryRunResult(command="dump")
    result.add(
        "Inputs",
        f"artifact: {so_path}" if so_path else "artifact: (none -- source-only dump)",
        f"headers: {', '.join(str(h) for h in headers)}" if headers else None,
    )
    result.add(
        "Resolved depth and source scope",
        f"requested depth: {depth or '(auto)'}",
        f"effective collect mode: {collect_mode}",
        "source scope: target (dump always analyzes the resolved library target)"
        if collect_mode in ("source-target", "source-changed", "graph-full")
        else None,
    )
    result.add(
        "Headers and compile context",
        f"ast-frontend: {header_backend}",
    )
    result.add(
        "Build/source inputs",
        f"--sources: {sources}" if sources else None,
        f"--build-info: {build_info}" if build_info else None,
        "no --sources/--build-info given -- L0-L2 only"
        if sources is None and build_info is None and collect_mode != "off"
        else None,
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    if so_path is not None:
        try:
            from .binary_utils import detect_binary_format, normalize_binary_input
            from .dwarf_snapshot import show_data_sources

            normalized_path, binary_fmt = normalize_binary_input(so_path)
            if binary_fmt is None:
                binary_fmt = detect_binary_format(normalized_path)
            elf_meta = None
            dwarf_meta = None
            if binary_fmt == "elf":
                from .dwarf_unified import parse_dwarf
                from .elf_metadata import parse_elf_metadata

                elf_meta = parse_elf_metadata(normalized_path)
                dwarf_meta, _ = parse_dwarf(normalized_path)
            report = show_data_sources(
                normalized_path, elf_meta, dwarf_meta, bool(headers), None
            )
            result.add("Available data layers", *report.splitlines())
        except Exception as exc:  # pragma: no cover - best-effort diagnostic
            result.warn(f"could not inspect available data layers: {exc}")
    cfg_path = build_config or discover_project_config(sources)
    result.add(
        "Configuration and value origins",
        f".abicheck.yml: {cfg_path if cfg_path else '(none found)'}",
    )
    result.add(
        "Output and exit-code behavior",
        f"output: {output if output else 'stdout'}",
        "exit codes: 0 valid, 1 requested depth not satisfiable, 64 usage error",
    )
    if so_path is None and sources is None and build_info is None:
        result.block(
            "no artifact (SO_PATH) and no --sources/--build-info: dump has "
            "nothing to analyze."
        )
    if depth is not None and depth != "binary" and sources is None and build_info is None:
        result.warn(
            f"--depth {depth} was requested but no --sources/--build-info was given; "
            "the snapshot would carry only L0-L2 data."
        )
        # Cheaply, deterministically known to fail the real run's strict
        # check_requested_depth_satisfied gate -- not merely degraded, since
        # neither input given here could ever reach it: --depth source has
        # no path but --sources/--build-info (a -p/--compile-db supplies L3
        # "build" context only, never L4 source-ABI replay), and --depth
        # build has no path at all once --sources/--build-info AND -p are
        # both absent. A --depth build backed by *some* compile database is
        # deliberately left as the softer warning above, not a blocker --
        # whether that database actually matches these headers is real work
        # (load + header-inclusion scan) a dry run must not perform, so it
        # is only "possibly satisfiable", not "definitely satisfiable"
        # (Codex review).
        if depth == "source" or (depth == "build" and not has_compile_db):
            result.block(
                f"--depth {depth} was requested but the resolved evidence "
                "depth check_requested_depth_satisfied would raise on: "
                f"nothing here can reach '{depth}' -- the real run would "
                "exit 1."
            )
    return result


def resolve_dump_compile_db(
    compile_db_path: Path | None,
    compile_db_path_alt: Path | None,
    headers: tuple[Path, ...],
) -> Path | None:
    """Resolve -p / --compile-db aliases and validate header requirement.

    Raises :class:`click.UsageError` if a compile DB is given but no headers.
    Returns the effective compile DB path (or *None*).
    """
    effective_compile_db = compile_db_path or compile_db_path_alt
    if effective_compile_db and not headers:
        raise click.UsageError(
            "Compilation database (-p / --compile-db) requires -H/--header. "
            "Without headers, CastXML has nothing to parse."
        )
    return effective_compile_db


def handle_non_elf_dump(
    so_path: Path,
    binary_fmt: str,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    version: str,
    lang: str,
    pdb_path: Path | None,
    follow_deps: bool,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    output: Path | None,
    dump_native_binary: Callable[..., AbiSnapshot],
    stamp_provenance: _StampProvenance,
    write_snapshot_output: _WriteSnapshotOutput,
    public_headers: tuple[Path, ...] = (),
    public_header_dirs: tuple[Path, ...] = (),
    build_info: Path | None = None,
    sources: Path | None = None,
    build_config: Path | None = None,
    allow_build_query: bool = False,
    collect_mode: str = "source-target",
    build_query: str | None = None,
    build_compile_db: str | None = None,
    header_backend: str = "auto",
    compile_context: Any = None,
    inputs_pack: Path | None = None,
    header_graph: bool = False,
    header_graph_includes: bool = False,
    depth: str | None = None,
) -> None:
    """Handle the PE/Mach-O native dump path and output writing (split from cli.py).

    ``dump_native_binary``/``stamp_provenance``/``write_snapshot_output`` are all
    passed in from cli.py rather than imported, mirroring ``perform_elf_dump`` —
    the AST-based import-cycle gate counts *any* import (including a lazy
    function-body ``from .cli_resolve import …`` and a ``TYPE_CHECKING`` import),
    so importing them here would close a ``cli → cli_dump_helpers → … → cli``
    cycle. ``compile_context`` is typed ``Any`` for the same reason (its concrete
    ``CompileContext`` lives in ``service_scan``).

    ``header_graph``/``header_graph_includes`` (ADR-041 addendum) forward
    straight into ``dump_native_binary`` (``_dump_native_binary`` →
    ``service.run_dump``), which attaches the header-only graph uniformly
    across ELF/PE/Mach-O — previously only the ELF ``perform_elf_dump`` path
    forwarded these, so ``dump --header-graph`` silently no-opped on PE/Mach-O
    input (Codex review).
    """
    if follow_deps:
        click.echo("Warning: --follow-deps is only supported for ELF binaries.", err=True)
    # L2 include fallback (parity with the ELF dump path): when -H headers are given
    # with --sources/--build-info but no explicit -I, seed the build's include dirs so
    # a PE/Mach-O header scope can resolve dependency headers instead of failing or
    # falling back to export-table mode (Codex review). collect_mode "off"
    # (--depth headers/binary) gates the executing inferred build query. dump has no
    # defer_cleanup channel, so temp-build-dir cleanups come back pending and run in
    # the finally, after the header parse has consumed the dirs.
    from .buildsource.inline import _run_cleanups
    from .buildsource.l2_seed import seed_l2_includes

    eff_includes, _l2_pending_cleanups = seed_l2_includes(
        headers=headers,
        includes=includes,
        sources=sources,
        build_info=build_info,
        build_config=build_config,
        defer_cleanup=None,
        build_query=build_query,
        build_compile_db=build_compile_db,
        gcc_options=getattr(compile_context, "gcc_options", None),
        gcc_option_tokens=getattr(compile_context, "gcc_option_tokens", ()),
        allow_inferred_build_query=collect_mode != "off",
    )
    try:
        snap = dump_native_binary(
            so_path, binary_fmt, list(headers), list(eff_includes), version, lang,
            pdb_path=pdb_path,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
            header_backend=header_backend,
            compile=compile_context,
            header_graph=header_graph,
            header_graph_includes=header_graph_includes,
        )
    except click.ClickException:
        raise
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        if _l2_pending_cleanups:
            _run_cleanups(_l2_pending_cleanups)
    stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    write_snapshot_output(
        snap, output, build_info, sources, build_config, allow_build_query,
        collect_mode, build_query=build_query, build_compile_db=build_compile_db,
        extractor=header_backend, inputs_pack=inputs_pack, depth=depth,
    )


def resolve_dump_collect_context(
    depth: str | None,
    resolved_collect_mode: str | None,
    sources: Path | None,
    build_info: Path | None,
    headers: tuple[Path, ...],
    compile_db_path: Path | None,
    compile_db_path_alt: Path | None,
    inputs_pack: Path | None = None,
) -> tuple[str, tuple[Path, ...], Path | None, Path | None]:
    """Resolve the --depth preset into the internal collect mode for a dump.

    Returns the ``(collect_mode, headers, compile_db_path, compile_db_path_alt)``
    tuple the caller should proceed with — ``--depth binary`` suppresses the L2
    header AST and its compile DB, and an explicitly-requested deep depth without
    a source tree / build context warns loudly (G21.7-style fail-loud).
    """
    # Resolve the --depth preset into the internal collect mode before any dump
    # path runs, so every branch (source-only / PE-Mach-O / ELF) embeds the same
    # evidence depth (G21.1). With no preset, dump embeds at "source-target".
    # ``compare``'s inline source-tree embed already resolved the mode and hands
    # it over via the private _resolved_collect_mode hook so we don't re-derive a
    # different default here (Codex review).
    if resolved_collect_mode is not None:  # pragma: no cover - only via compare's inline embed (integration)
        collect_mode = resolved_collect_mode
    else:
        collect_mode = resolve_dump_depth(depth, "source-target")
    # --depth binary suppresses the L2 header AST (symbols-only dump, ADR-037 D5).
    # A compile DB only feeds the header parse, so discard it with the headers --
    # otherwise resolve_dump_compile_db would reject the now-headerless invocation
    # even though the user did supply headers, blocking the switch to the fast
    # binary rung (Codex review).
    if depth == "binary":
        headers = ()
        compile_db_path = None
        compile_db_path_alt = None

    # An *explicitly* requested deep evidence depth (--depth) collects nothing
    # without a source tree / build context: _write_snapshot_output only embeds
    # when --sources/--build-info is given. Warn loudly rather than silently
    # writing an L0-L2 snapshot for an explicitly-requested deep depth (Codex
    # review). The bare default (collect_mode "source-target" with no flag) stays
    # silent -- embedding is a no-op there by design. G21.7-style fail-loud (a
    # warning, not an error).
    depth_requested = depth is not None
    if (
        depth_requested
        and collect_mode != "off"
        and sources is None and build_info is None
        and inputs_pack is None
    ):
        click.echo(
            f"Warning: evidence depth '{collect_mode}' was requested but no "
            "--sources/--build-info/--inputs was given; the snapshot will carry "
            "only L0-L2 data (no build/source/graph facts). Pass --sources, "
            "--build-info, or --inputs, or use --depth headers for an L2-only dump.",
            err=True,
        )
    return collect_mode, headers, compile_db_path, compile_db_path_alt


def resolve_dump_compile_context(
    resolved_compile_context: CompileContext | None,
    *,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    header_backend: str,
    includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Resolve the L2 compile context for a dump, folding the config compile: block.

    Returns ``(compile_context, includes)``. When the caller (compare's inline
    source-tree embed) already resolved the context it is used verbatim; do NOT
    re-discover/re-merge the tree's .abicheck.yml here.
    """
    if resolved_compile_context is not None:
        # Caller (compare's inline source-tree embed) already resolved the compile
        # context with CLI-over-config explicitness honored; use it verbatim and do
        # NOT re-discover/re-merge the tree's .abicheck.yml here — re-running the
        # resolver under ctx.invoke would lose that explicitness (the kwargs are not
        # COMMANDLINE param-sources), clobbering e.g. --no-nostdinc / --ast-frontend
        # auto on the source-tree path only (Codex review).
        return resolved_compile_context, includes
    from .cli_options import resolve_compile_context

    return resolve_compile_context(
        click.get_current_context(),
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens, sysroot=sysroot, nostdinc=nostdinc,
        header_backend=header_backend, includes=includes,
        build_config=build_config, sources=sources,
    )


def perform_elf_dump(
    so_path: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    version: str,
    lang: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    effective_gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    dwarf_only: bool,
    effective_debug_format: str | None,
    public_headers: tuple[Path, ...],
    public_header_dirs: tuple[Path, ...],
    effective_compile_db: Path | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
    output: Path | None,
    build_info: Path | None,
    sources: Path | None,
    build_config: Path | None,
    allow_build_query: bool,
    collect_mode: str,
    expand_header_inputs: _ExpandHeaderInputs,
    populate_dependency_info: _PopulateDependencyInfo,
    stamp_provenance: _StampProvenance,
    write_snapshot_output: _WriteSnapshotOutput,
    build_query: str | None = None,
    build_compile_db: str | None = None,
    header_backend: str = "auto",
    user_gcc_options: str | None = None,
    compile_db_filter: str | None = None,
    inputs_pack: Path | None = None,
    debug_info_path: Path | None = None,
    header_graph: bool = False,
    header_graph_includes: bool = False,
    compile_context: CompileContext | None = None,
    depth: str | None = None,
    compile_db_context_matched: bool = False,
) -> None:
    """Run the ELF dump pipeline and write output.

    ``debug_info_path`` (P1.1, ADR-021a): a resolved detached debug artifact
    (``--debug-root``/``--debuginfod``) to read DWARF sections from instead of
    ``so_path`` itself — threaded straight into :func:`dumper.dump`.

    ``header_graph``/``header_graph_includes`` (ADR-041 addendum): builds and
    embeds the header-only (L2) semantic graph via
    :func:`~abicheck.service._attach_header_graph` — the same post-processing
    step ``service.run_dump`` already applies for `compare`'s implicit-dump
    path, reused here rather than duplicated (``dumper.py`` sits at its
    2000-line hard cap, so this stays a wrapper around the already-built
    snapshot instead of a new parameter on :func:`dumper.dump` itself). A
    no-op when ``header_graph`` was not requested or no headers were parsed.

    All helper callables (expand_header_inputs, populate_dependency_info,
    stamp_provenance, write_snapshot_output) are passed in from cli.py to avoid
    an import cycle — cli_dump_helpers must not import from cli.

    ``compile_db_context_matched`` (Codex review): the second element of
    cli.py's ``_resolve_build_context_flags(effective_compile_db, headers,
    compile_db_filter)`` -- whether a compile-DB entry genuinely backed the
    resolved ``BuildContext`` (its ``compile_db_path`` is set) — computed
    there (before this function is even called) since that is the one place
    both the compile-DB load and the header-context derivation already
    happen. Distinct from both ``effective_compile_db`` merely being
    non-``None`` (a syntactically valid but empty, or entirely filtered-out,
    ``compile_commands.json`` still sets that, but matches nothing) *and*
    from whether any castxml flags were derived (a genuinely matched TU with
    no ABI-relevant flags to forward is still real build-context evidence,
    not an absent one — Codex review, second finding on this signal).
    """
    compiler = "cc" if lang == "c" else "c++"
    resolved_headers = expand_header_inputs(list(headers)) if headers else []
    # P3: auto-add the public-header roots so a -H umbrella resolves its own
    # relative includes without a separate -I. resolve_inferred_header_roots
    # picks the search bucket: plain -I (high priority, so an umbrella that pulls
    # a system-colliding name like <endian.h> still finds the package header)
    # when there is no build context, or -isystem (below the build-context dirs
    # so generated/shim headers from -p/--gcc-options keep priority, but still
    # above the standard system dirs) when the compile context supplies its own
    # includes — see its docstring.
    from .header_utils import deferred_token_dirs, resolve_inferred_header_roots

    inc_extra, deferred = (
        resolve_inferred_header_roots(
            list(headers),
            list(includes),
            gcc_options=effective_gcc_options,
            gcc_option_tokens=tuple(gcc_option_tokens),
        )
        if resolved_headers
        else ([], [])
    )
    # Deferred roots ride in gcc_option_tokens (as -isystem), not extra_includes,
    # so their contents must be hashed into the AST cache key explicitly (Codex).
    deferred_dirs = tuple(deferred_token_dirs(deferred))
    # L2 include fallback (parity with `scan`): when -H headers are given but no
    # explicit -I, seed the build's include dirs so `dump --sources` parses public
    # headers that reach into a dependency SDK (the pvxs/EPICS case). dump has no
    # defer_cleanup channel, so any inferred temp-build-dir cleanups come back as
    # pending and are run below, after the header parse has consumed the dirs.
    from .buildsource.inline import _run_cleanups
    from .buildsource.l2_seed import seed_l2_includes

    eff_includes, _l2_pending_cleanups = seed_l2_includes(
        headers=headers,
        includes=includes,
        sources=sources,
        build_info=build_info,
        build_config=build_config,
        defer_cleanup=None,
        build_query=build_query,
        build_compile_db=build_compile_db,
        # Include dirs supplied via --gcc-options/--gcc-option are as explicit as
        # -I and must suppress the seed so the user's search precedence is kept.
        gcc_options=effective_gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        # An L2-only dump (--depth headers → collect_mode "off") requested no build/
        # source evidence, so don't let the include-dir seed run a build system;
        # only the zero-config inferred query is gated, passive discovery stays
        # (Codex review).
        allow_inferred_build_query=collect_mode != "off",
    )
    try:
        snap = dump(
            so_path=so_path,
            headers=resolved_headers,
            extra_includes=eff_includes + inc_extra,
            version=version,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=effective_gcc_options,
            gcc_option_tokens=tuple(gcc_option_tokens) + tuple(deferred),
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=effective_debug_format,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
            header_backend=header_backend,
            extra_hash_dirs=deferred_dirs,
            debug_info_path=debug_info_path,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        # The header parse itself failed -- nothing downstream (including a
        # requested --header-graph pass) will run, so the seeded temp build
        # dir is no longer needed by anything; release it now rather than
        # leaking it.
        if _l2_pending_cleanups:
            _run_cleanups(_l2_pending_cleanups)
        raise click.ClickException(str(exc)) from exc

    try:
        # Record that the header AST was parsed with the real build context (ADR-029).
        # Gated on compile_db_context_matched (whether the DB actually yielded usable
        # castxml flags for these headers), not just effective_compile_db's presence --
        # a syntactically valid but empty/non-matching compile_commands.json sets
        # effective_compile_db without deriving anything, which must not silently
        # satisfy the --depth build strict gate (evidence_depth_label reads this flag
        # as "build" evidence; Codex review).
        if effective_compile_db and resolved_headers and compile_db_context_matched:
            snap.parsed_with_build_context = True

        # ADR-039 collection layer — when a compile DB is available, harvest the
        # build's active ``-D`` set and scan the public headers for ``#ifdef``-guarded
        # record fields, so the reconciler can clear a context-free header-parse false
        # positive (a guarded field the context-free castxml parse pruned). Best-effort
        # and additive: absent/empty on a plain context-free dump.
        if effective_compile_db and resolved_headers:
            # Augment the sound per-command compile-DB intersection with the user's
            # *global* flags only: the repeatable ``--gcc-option`` tokens and the
            # ``-D``/``-U`` in the ``--gcc-options`` string (``user_gcc_options``).
            # A user ``--gcc-options=-UKEEP`` must override a DB ``-DKEEP`` (Codex
            # review #498). We deliberately do NOT feed ``effective_gcc_options``,
            # which also carries the *first* resolved header's auto-derived build
            # context — unioning that snapshot-wide would mark one TU's ``-DKEEP``
            # active for every scanned header.
            _attach_build_context(
                snap,
                effective_compile_db,
                resolved_headers,
                _user_define_flags(gcc_option_tokens, user_gcc_options),
                source_filter=compile_db_filter,
            )

        # G14: recognise a CPython extension module and attach its metadata so the
        # written snapshot carries the abi3 / imported-C-API surface. The ELF `dump`
        # CLI reaches `dumper.dump` directly (not `service.run_dump`), so this is the
        # attach point for that path; `detect_python_extension` is a leaf import (no
        # cycle) and a no-op for ordinary libraries. `compare` also derives it on
        # load as a backstop for snapshots written without it.
        if snap.python_ext is None:
            from .python_ext import detect_python_extension

            snap.python_ext = detect_python_extension(snap)

        # G23: recover the Python-visible API surface from a sibling `.pyi` stub, so
        # the snapshot also carries the function/class/method signatures a consumer
        # `import`s — the surface the C-ABI export view cannot see. A no-op when no
        # stub is found alongside the binary.
        if snap.python_api is None:
            from .python_api import detect_python_api

            snap.python_api = detect_python_api(snap)

        # G26: attach NumPy C-API consumption evidence for the same reason as
        # G14/G23 above — this ELF `dump` CLI path reaches `dumper.dump` directly,
        # not `service.run_dump` (whose `_try_attach_numpy_capi_surface` only
        # covers the in-process compare path), so without this a snapshot written
        # via `abicheck dump` never carries `numpy_capi` and every G26 delta in a
        # later `compare` on the written JSON stays silently disabled (Codex
        # review).
        if snap.numpy_capi is None:
            from .numpy_capi import extract_numpy_capi_surface

            snap.numpy_capi = extract_numpy_capi_surface(so_path)

        # ADR-041 addendum: same "this ELF dump CLI path reaches dumper.dump
        # directly, not service.run_dump" attach point as G14/G23/G26 above —
        # service._attach_header_graph is the exact wrapper service.run_dump uses
        # for `compare`'s implicit-dump path, reused verbatim so a written
        # snapshot's embedded graph is identical either way. A no-op unless
        # --header-graph was passed and headers were parsed. Pass eff_includes
        # (seed_l2_includes' output), not the raw includes argument: when
        # --sources/--build-info seeded build-derived include dirs above (no
        # explicit -I given) the main dump() call already sees them via
        # `eff_includes + inc_extra`, but this second, independent clang pass
        # would otherwise resolve headers with only the user's explicit -I,
        # silently degrading to a declaration-only graph (no type/call edges)
        # even though the main snapshot parsed cleanly (Codex review).
        # effective_gcc_options folds in the -p/--compile-db-derived -D/-I/
        # -std flags (_merge_gcc_options, above the main dump() call) that
        # `compile_context` itself does not carry -- it was resolved earlier,
        # from the plain --gcc-options CLI value only. Without this, a header
        # that only parses successfully with those compile-DB flags would
        # produce a valid main snapshot while a second clang pass parses it
        # without them and silently degrades (declaration-only graph / no
        # layout enrichment) even though the main snapshot parsed cleanly
        # (Codex review). Shared by BOTH post-processing steps below, since
        # each runs its own independent second clang pass over the same
        # headers and needs the identical fix for the identical reason.
        effective_compile_context = compile_context
        if effective_gcc_options != (
            compile_context.gcc_options if compile_context is not None else None
        ):
            import dataclasses

            if compile_context is not None:
                effective_compile_context = dataclasses.replace(
                    compile_context, gcc_options=effective_gcc_options
                )
            else:
                from .service_scan import CompileContext

                effective_compile_context = CompileContext(
                    gcc_options=effective_gcc_options
                )

        if header_graph:
            from .service import _attach_header_graph

            snap = _attach_header_graph(
                snap,
                header_graph,
                header_graph_includes,
                list(headers),
                list(eff_includes),
                lang,
                effective_compile_context,
                list(public_headers),
                list(public_header_dirs),
            )

        # G28 Phase 4: same "this ELF dump CLI path reaches dumper.dump
        # directly, not service.run_dump" attach point as header_graph above.
        # attach_clang_layout is a no-op unless the snapshot's L2 backend was
        # actually "clang" AND the optional companion tool is resolvable
        # (ABICHECK_CLANG_LAYOUT_TOOL / a bare abicheck-clang-layout-tool on
        # PATH), so no extra CLI flag gates this call. Without it, a saved
        # JSON baseline written via `abicheck dump --ast-frontend clang`
        # never carried the tool's size/offset/base/vptr facts even though
        # `compare`'s implicit-dump path (service.run_dump) already got them
        # (Codex review).
        from .clang_layout_tool import attach_clang_layout

        snap = attach_clang_layout(
            snap,
            list(headers),
            list(eff_includes),
            lang=lang,
            compile=effective_compile_context,
        )
    finally:
        # The header-graph pass above (when requested) reuses the same seeded
        # include dirs the main dump() parse used, so cleanup must wait until
        # it (and everything else in this post-dump pipeline that could raise
        # first) has run — releasing right after dump() alone would leak the
        # temp dir on an exception from build-context/Python/NumPy enrichment
        # before the header-graph pass is ever reached (Codex review). One
        # try/finally around the whole pipeline drains the cleanup exactly
        # once, on every exit path.
        if _l2_pending_cleanups:
            _run_cleanups(_l2_pending_cleanups)

    if follow_deps:
        populate_dependency_info(
            snap, so_path, list(search_paths), sysroot, ld_library_path
        )

    stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    write_snapshot_output(
        snap,
        output,
        build_info,
        sources,
        build_config,
        allow_build_query,
        collect_mode,
        build_query=build_query,
        build_compile_db=build_compile_db,
        extractor=header_backend,
        inputs_pack=inputs_pack,
        depth=depth,
    )
