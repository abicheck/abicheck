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

"""Helper functions for the ``dump`` CLI command (split from cli.py).

Everything here is presentation, provenance and validation around a dump --
debug-format resolution, the ``--depth`` floor, ``--dry-run`` rendering, the
snapshot write step, the L2 compile-context resolution. The *execution* half
this module used to own (``perform_elf_dump``, and its PE/Mach-O sibling
``handle_non_elf_dump`` in the former ``cli_dump_non_elf.py``) is gone:
ADR-063 Phase 1 routed ``dump_cmd``'s real run for either binary format
through the shared typed executor
(``frontends.cli.dump_execute.execute_and_write_dump_cli_run`` ->
:func:`abicheck.service_dump_pipeline.execute_dump_request`), leaving both
functions with no production caller and only their own unit tests keeping
them alive. Track 1 of ``docs/contribute/plans/
duplication-and-convergence-assessment.md`` retired all three modules'
worth of that (the two functions plus the ``Protocol`` types in
``cli_dump_protocols.py`` that existed solely to describe the callables
``dump_cmd`` passed into them); the behaviours whose only home was one of
them are now pinned in ``tests/test_dump_cli_execution_behaviors.py``
against the path that actually runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

# `resolve_dump_depth`/`resolve_dump_collect_context` moved to
# `cli_dump_depth.py`, purely to stay under the AI-readiness 2000-line hard
# cap (see that module's own docstring). Re-exported here (the `as`-aliased
# form ruff/mypy both recognize as an explicit re-export, not an unused
# import) so every pre-existing `from .cli_dump_helpers import
# resolve_dump_depth`/`resolve_dump_collect_context` -- `cli_compare_helpers.
# py` calls the former directly -- keeps resolving unchanged. A real, eager
# import rather than the lazy `__getattr__` shim below: a dynamic
# module-level `__getattr__` is opaque to mypy at a *call* site, unlike the
# four names in that shim, which nothing outside this module calls as a
# function. No cycle risk: `cli_dump_depth.py` imports only
# `.buildsource.scan_levels`/`click`.
from .cli_dump_depth import (
    resolve_dump_collect_context as resolve_dump_collect_context,
    resolve_dump_depth as resolve_dump_depth,
)
from .evidence_depth import (
    DEPTH_RANK,
    depth_label_for,
    gated_source_label,
    l4_source_abi_was_attempted,
)
from .workflows.extraction import (
    _manifest_declared_includes,
    detect_binary_format,
    normalize_binary_input,
    show_data_sources,
)

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .model import AbiSnapshot
    from .service_dump_pipeline import ResolvedDumpRequest
    from .service_scan import CompileContext


# ── Back-compat re-export shim (lazy, per AGENTS.md's moved-helper
#    convention) ────────────────────────────────────────────────────────────
# All four of these live in `header_conditionals.py` (see that module for the
# ADR-039 collection logic and why it moved -- PR C, CLI cleanup phase two) and
# nothing in this module calls any of them itself. `attach_build_context`/
# `user_define_flags` joined this shim in PR 3A: `perform_elf_dump` used to
# call both directly (a structurally required static edge), but now reaches
# them only through `attach_build_context_for_parsed_headers` -- the one shared
# ADR-039 gate this call site, the typed pipeline's
# `_resolve_side_snapshot_impl`, and `scan_engine._build_new_snapshot` all use.
# They stay reachable under their original private names purely so
# `from .cli_dump_helpers import ...` (cli.py) and existing tests keep working
# unchanged. A static re-export would be an eager dependency edge this module
# has no other reason to carry for these names, so this module-level
# `__getattr__` (PEP 562) resolves them lazily via `importlib.import_module`
# instead -- mirroring the identical shim at the tail of `cli_buildsource.py`
# (Codex review, PR #809).
_HEADER_CONDITIONALS_REEXPORTS: dict[str, str] = {
    "compile_db_from_build_info": "compile_db_from_build_info",
    "compile_db_for_filter_scope_check": "compile_db_for_filter_scope_check",
    "compile_db_filter_scope_error": "compile_db_filter_scope_error",
    "_attach_build_context": "attach_build_context",
    "_user_define_flags": "user_define_flags",
}


def __getattr__(name: str) -> Any:
    if (target := _HEADER_CONDITIONALS_REEXPORTS.get(name)) is not None:
        import importlib

        return getattr(importlib.import_module("abicheck.header_conditionals"), target)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


def check_dump_debug_format_error(
    effective_debug_format: str | None,
    binary_fmt: str | None,
) -> str | None:
    """Pure predicate for the --debug-format/PE-Mach-O incompatibility.

    Mirrors the ``click.BadParameter`` cli.py's real dump path raises after
    resolving the binary format, as a plain string instead of raising --
    shared with ``dump --dry-run``, which previously never ran this check at
    all (it only existed in the real path, after the dry-run branch), so a
    ``--dwarf``/``--btf``/``--ctf``/``--debug-format`` dump of a PE/Mach-O
    binary reported dry-run success on an invocation the real run would
    immediately reject.
    """
    if effective_debug_format is not None and binary_fmt in ("pe", "macho"):
        return (
            f"--{effective_debug_format} is only supported for ELF binaries, "
            f"not {binary_fmt.upper()}."
        )
    return None


def evidence_depth_label(
    snap: AbiSnapshot,
    build_source: BuildSourcePack | None = None,
) -> str:
    """Report which evidence depth a snapshot *actually* carries (CLI-audit P2).

    The embedded-only defaulting wrapper over
    :func:`abicheck.evidence_depth.depth_label_for`, which owns the rule and
    documents it. *build_source*, when given, overrides ``snap.build_source``
    -- ``compare`` can resolve an out-of-band ``--old/new-sources`` pack that
    is never attached back to the snapshot object (``_resolve_side_pack``
    returns it standalone), and without this override a compare run using only
    out-of-band packs would report the depth of the *unrelated* embedded (or
    absent) snapshot payload (Codex review). The default is what the plain
    single-artifact ``dump -o`` case wants; the leaf itself deliberately takes
    the pack explicitly so no other caller can acquire the default by accident.
    """
    return depth_label_for(snap, snap.build_source if build_source is None else build_source)


#: Compatibility alias. The ladder is owned by ``evidence_depth.DEPTH_RANK``,
#: which derives it from ``buildsource.scan_levels.USER_DEPTHS`` so the
#: ordering has one definition rather than a copy per consumer.
_DEPTH_RANK = DEPTH_RANK


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
    from .cli_buildsource_helpers import _is_inputs_pack_dir
    from .workflows.extraction import is_pack_dir

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
    """Compatibility alias for :func:`abicheck.evidence_depth.l4_source_abi_was_attempted`."""
    return l4_source_abi_was_attempted(build_source)


def _gated_source_label(build_source: BuildSourcePack | None, snap: AbiSnapshot) -> str:
    """Compatibility alias for :func:`abicheck.evidence_depth.gated_source_label`."""
    return gated_source_label(build_source, snap)


def check_requested_depth_satisfied(
    depth: str | None,
    snap: AbiSnapshot,
    build_source: BuildSourcePack | None = None,
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


def fold_dump_provenance_into_dict(
    payload: dict[str, Any],
    depth: str | None,
    snap: AbiSnapshot,
) -> tuple[dict[str, Any], str]:
    """Dict-level counterpart of :func:`fold_dump_provenance_into_json`.

    Same computation, but operating on an already-decoded payload dict
    in-place instead of a serialized JSON string — used by the normal
    `dump` write path (ADR-059) so a 100+ MB snapshot is never
    ``json.loads()``-ed and ``json.dumps()``-ed a second time just to
    attach this one key. ``fold_dump_provenance_into_json`` below is now a
    thin backward-compatible wrapper for callers (and tests) that still
    want the string-in/string-out shape.

    Returns ``(payload, effective_depth)`` — see
    ``fold_dump_provenance_into_json``'s docstring for the full reasoning
    behind every field and the ``effective``/``degraded`` semantics.
    """
    effective = _gated_source_label(snap.build_source, snap)
    frontend = (
        (_l4_source_abi_frontend(snap) or snap.ast_producer)
        if effective == "source"
        else (snap.ast_producer or _l4_source_abi_frontend(snap))
    )
    source_abi = snap.build_source.source_abi if snap.build_source is not None else None
    source_scope = (
        source_abi.coverage.get("replay_scope") if source_abi is not None else None
    )
    payload["dump_provenance"] = {
        "requested_depth": depth,
        "effective_depth": effective,
        "degraded": (
            depth is not None
            and _DEPTH_RANK.get(effective, 0) < _DEPTH_RANK.get(depth, 0)
        ),
        "frontend": frontend,
        "ast_toolchain": snap.ast_toolchain or None,
        "ast_fallback_reason": snap.ast_fallback_reason,
        "source_scope": source_scope,
    }
    return payload, effective


def write_snapshot_payload(
    payload: dict[str, Any],
    path: Path,
    *,
    compression: Any,
) -> Any:
    """One JSON encode + one canonical, atomic, possibly-compressed write
    (ADR-059) for the already-decoded ``dump`` payload dict — the write
    chokepoint the real (non-dry) `dump` write path routes through so a
    100+ MB snapshot is serialized exactly once. Writes the single-file
    sectioned shape (``storage.sectioned_document``). *compression* is a
    :class:`~abicheck.snapshot_io.SnapshotCompression`; returns a
    :class:`~abicheck.snapshot_io.SnapshotWriteResult`.
    """
    import json

    from .serialization import SCHEMA_VERSION
    from .workflows.storage import to_sectioned_document, write_snapshot_text

    payload = to_sectioned_document(payload, max_known_schema_version=SCHEMA_VERSION)
    text = json.dumps(payload, indent=2)
    return write_snapshot_text(text, path, compression=compression)


def reject_snapshot_compression_conflict(
    output: Path | None, snapshot_compression: str
) -> None:
    """Validate ``--compression``/``-o`` suffix agreement up front (Codex
    review): :func:`~abicheck.snapshot_io.resolve_write_compression` used to
    run only at the very end of the write path, after a potentially
    expensive extraction had already completed -- so ``--compression zstd
    -o snapshot.json.gz`` wasted a full dump before failing. Calling it here
    too, before any extraction work starts, makes the same conflict a fast
    usage error (exit 64) like every other invalid-argument case, and keeps
    ``--dry-run``'s own reporting of the same conflict consistent with the
    real run's exit code (both go through this same resolver now)."""
    if output is None:
        return
    from .errors import SnapshotError
    from .workflows.storage import SnapshotCompression, resolve_write_compression

    try:
        resolve_write_compression(output, SnapshotCompression(snapshot_compression))
    except SnapshotError as exc:
        raise click.UsageError(str(exc)) from exc


def write_snapshot_and_report(
    payload: dict[str, Any],
    output: Path,
    snapshot_compression: str,
    resolved_depth_label: str,
) -> None:
    """Write *payload* to *output* under *snapshot_compression* (ADR-059) and
    echo the "written to"/storage-summary/evidence-depth stderr lines the
    real (non-dry) ``dump`` write path always emits — split out of
    ``cli.dump_cmd`` to keep that function's line count under the AI-
    readiness file-size gate.
    """
    from .errors import SnapshotError
    from .workflows.storage import SnapshotCompression

    try:
        write_result = write_snapshot_payload(
            payload, output, compression=SnapshotCompression(snapshot_compression)
        )
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc
    except OSError as exc:
        # Codex review: the canonical atomic writer can raise a plain OSError
        # (unwritable destination, disk full, a chmod/os.replace failure --
        # see snapshot_io._atomic_write_bytes) that isn't a SnapshotError.
        # The old _safe_write_output() path translated exactly this class of
        # failure into a ClickException instead of a raw traceback; match
        # that here too.
        raise click.ClickException(f"Cannot write to {output}: {exc}") from exc
    click.echo(f"Snapshot written to {output}", err=True)
    click.echo(
        "Storage: "
        f"{write_result.compression.value}, "
        f"{write_result.decoded_size_bytes:,} -> {write_result.stored_size_bytes:,} bytes "
        f"({write_result.ratio:.1%})",
        err=True,
    )
    # Self-describing output (CLI-audit P2): report the evidence depth this
    # snapshot actually reached -- computed from what it carries, not the
    # requested --depth, so an explicit --depth source that collected
    # nothing usable is never silently reported as if it had succeeded.
    # Reuses fold_dump_provenance_into_dict's own returned label (the strict
    # _gated_source_label, not the plain evidence_depth_label) so this line
    # can never disagree with the JSON's effective_depth for the same dump
    # -- they previously could, on the documented zero-match-source-only
    # case (external review).
    click.echo(f"Resolved evidence depth: {resolved_depth_label}", err=True)


def fold_dump_provenance_into_json(
    text: str,
    depth: str | None,
    snap: AbiSnapshot,
) -> tuple[str, str]:
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

    ``effective_depth`` uses ``_gated_source_label``, the same strict
    recompute ``check_requested_depth_satisfied`` gates on -- not the plain
    ``evidence_depth_label``. They disagree on exactly the case
    ``_gated_source_label``'s docstring documents: a zero-match source-only
    dump (L4 replay genuinely ran but linked nothing, no binary to link
    against) reports "source" satisfied by the strict gate, but
    ``evidence_depth_label`` still reports "build" (its own L4-or-L5
    payload-emptiness check sees both empty). Using the plain label here
    would serialize ``effective_depth: "build", degraded: true`` for a
    ``--depth source`` dump the strict gate had just accepted moments
    earlier in the same call -- self-contradictory (Codex review).

    Returns ``(json_text, effective_depth)`` -- the caller's own "Resolved
    evidence depth: ..." stderr echo must reuse this exact label rather than
    recomputing via the plain ``evidence_depth_label``, or the two can
    disagree on the identical zero-match-source-only case this docstring
    just described: JSON says "source", stderr says "build", for the same
    dump (external review).
    """
    import json

    effective = _gated_source_label(snap.build_source, snap)
    try:
        payload = json.loads(text)
    except ValueError:
        return text, effective
    payload, effective = fold_dump_provenance_into_dict(payload, depth, snap)
    return json.dumps(payload, indent=2), effective


def _l4_source_abi_frontend(snap: AbiSnapshot) -> str | None:
    """Best-effort L4 source-ABI extractor identity for ``dump_provenance.frontend``.

    ``snap.ast_producer`` is only ever stamped by the L2 header-AST pipeline
    (``dumper.py``'s castxml/clang parser); a symbol-only ELF dump with no
    ``-H`` headers (the common shape for a plain ``--sources``/``--build-info
    --depth build|source`` run) returns *before* that pipeline ever runs, so
    ``ast_producer`` stays ``None`` even though ``embed_build_source`` went on
    to run a real L4 ``source_abi:<extractor>`` replay over the source tree --
    losing the actual frontend identity from provenance (Codex review).

    Falls back to the build-source pack's extractor ledger
    (``buildsource.inline._run_inline_source_abi`` always records its result,
    success or not, as ``source_abi:<extractor>``): the most recent
    non-skipped/non-failed entry names the frontend that genuinely produced
    the linked ``SourceAbiSurface``. ``None`` when no such record exists
    (e.g. a headers-only or symbol-only dump with no L4 replay at all).
    """
    pack = snap.build_source
    if pack is None:
        return None
    for record in reversed(pack.manifest.extractors):
        if not record.name.startswith("source_abi:"):
            continue
        if record.status in ("skipped", "failed"):
            continue
        return record.name.removeprefix("source_abi:")
    return None


def _add_dump_manifest_section(result: Any, dump_manifest: Any) -> None:
    """Report a ``--dump-manifest`` document's TUs and ``scope_fingerprint``.

    Everything here is derived from the manifest document alone -- no compiler
    invocation -- which is what lets a dry run report the same
    ``scope_fingerprint`` (ADR-050 D1) the real extraction computes. It never
    computes a ``profile_fingerprint``: that fingerprint's ``-I`` component is a
    ``-MD``-depfile digest that only exists once a real L2 extraction runs.
    """
    from .comparability import compute_extraction_contract, manifest_tu_scope_field

    contract = compute_extraction_contract(
        declared_headers=list(dump_manifest.roots),
        # Purely manifest-document-derived (no compiler invocation),
        # exactly like manifest_tu_scope below -- the real (non-dry) path
        # folds both into scope_fingerprint via this same helper (Codex
        # review: the legacy field set either omission would otherwise
        # fall back to omits every TU's own includes/structure), so
        # without them the dry-run's printed fingerprint could never
        # match what the real extraction actually computes.
        declared_includes=_manifest_declared_includes(dump_manifest),
        public_header_paths=list(dump_manifest.public_header_paths),
        public_header_dirs=list(dump_manifest.public_header_dirs),
        l2_frontend_ran=False,
        manifest_tu_scope=manifest_tu_scope_field(dump_manifest),
    )
    scope_fingerprint = contract.scope_fingerprint if contract is not None else None
    manifest_lines = [
        f"compiler: {dump_manifest.compiler}",
        f"target: {dump_manifest.target or '(none)'}",
        f"frontend_context: {dump_manifest.frontend_context}",
        f"roots: {', '.join(str(p) for p in dump_manifest.roots)}",
    ]
    if dump_manifest.public_header_paths:
        manifest_lines.append(
            "public_header_paths: "
            + ", ".join(str(p) for p in dump_manifest.public_header_paths)
        )
    if dump_manifest.public_header_dirs:
        manifest_lines.append(
            "public_header_dirs: "
            + ", ".join(str(p) for p in dump_manifest.public_header_dirs)
        )
    for tu in dump_manifest.translation_units:
        manifest_lines.append(
            f"  tu: {tu.name} (required={tu.required}, "
            f"contributes_to_abi={tu.contributes_to_abi})"
        )
        if tu.forced_includes:
            manifest_lines.append(
                "      forced_includes: "
                + ", ".join(str(p) for p in tu.forced_includes)
            )
        if tu.includes:
            inc_repr = ", ".join(
                f"{inc.path}{' [project_owned]' if inc.project_owned else ''}"
                for inc in tu.includes
            )
            manifest_lines.append(f"      includes: {inc_repr}")
    manifest_lines.append(f"scope_fingerprint: {scope_fingerprint or '(none)'}")
    manifest_lines.append(
        "profile_fingerprint: (not computed -- requires a real L2 "
        "extraction; run without --dry-run)"
    )
    result.add("Multi-TU manifest (--dump-manifest)", *manifest_lines)


def _add_dump_data_layers(
    result: Any, so_path: Path, headers: tuple[Path, ...]
) -> None:
    """Report which L0-L2 data layers the artifact actually carries.

    Best-effort: a diagnostic section, so any parse failure is downgraded to a
    warning rather than failing the dry run.
    """
    try:
        normalized_path, binary_fmt = normalize_binary_input(so_path)
        if binary_fmt is None:
            binary_fmt = detect_binary_format(normalized_path)
        elf_meta = None
        dwarf_meta = None
        if binary_fmt == "elf":
            from .workflows.extraction import parse_dwarf, parse_elf_metadata

            elf_meta = parse_elf_metadata(normalized_path)
            dwarf_meta, _ = parse_dwarf(normalized_path)
        report = show_data_sources(
            normalized_path, elf_meta, dwarf_meta, bool(headers), None
        )
        result.add("Available data layers", *report.splitlines())
    except Exception as exc:  # pragma: no cover - best-effort diagnostic
        result.warn(f"could not inspect available data layers: {exc}")


def _add_dump_depth_feasibility(
    result: Any,
    *,
    so_path: Path | None,
    sources: Path | None,
    build_info: Path | None,
    depth: str | None,
    has_compile_db: bool,
    compile_db_matched: bool | None,
    build_info_is_pack: bool,
    dump_manifest: Any | None,
) -> None:
    """Decide whether the requested ``--depth`` is reachable from these inputs.

    Emits the blockers and warnings that predict the real run's strict
    ``check_requested_depth_satisfied`` gate. A blocker means the real run would
    certainly exit 1; a warning means "possibly satisfiable" -- the distinction
    is load-bearing, and the reasoning behind each case is kept with the case
    itself below.
    """
    if so_path is None and sources is None and build_info is None:
        if dump_manifest is not None:
            # A --dump-manifest-only dry run is a legitimate narrower
            # preflight (ADR-050 D3/D1): validate the manifest and compute
            # its scope_fingerprint before a real artifact even exists (the
            # former standalone `plan --dump-manifest` command's whole
            # purpose, ADR-054). It does not promise a same-flags real `dump`
            # would succeed -- that still needs SO_PATH, since
            # --dump-manifest extraction is only wired for real ELF dumps.
            result.warn(
                "no artifact (SO_PATH): the manifest above was parsed and "
                "fingerprinted, but a real extraction additionally needs "
                "SO_PATH (--dump-manifest is only wired for ELF binaries)."
            )
        else:
            result.block(
                "no artifact (SO_PATH) and no --sources/--build-info: dump has "
                "nothing to analyze."
            )
    if (
        depth is not None
        and depth != "binary"
        and sources is None
        # A compile database is now read off --build-info rather than named by
        # its own flag, so "no --sources/--build-info" already excludes every
        # case a database could supply L3 "build" evidence for; the precise
        # block/no-signal handling below covers those instead.
        and build_info is None
    ):
        result.warn(
            f"--depth {depth} was requested but no --sources/--build-info was given; "
            "the snapshot would carry only L0-L2 data."
        )
    # Cheaply, deterministically known to fail the real run's strict
    # check_requested_depth_satisfied gate -- computed independently of the
    # warn above (not nested under its both-absent condition), since
    # --depth source has its own, narrower requirement: L4 source-ABI
    # replay only ever runs over a source tree
    # (buildsource.inline._run_inline_source_abi returns ``(None, [])``
    # whenever ``sources`` is None) -- a raw --build-info/-p compile
    # database supplies L3 "build" context only, never L4, even when
    # --sources is absent. A --depth source dry-run with --build-info but
    # no --sources previously fell through this whole block (the warn's
    # "both absent" guard was false) and reported success even though the
    # real dump would hard-fail (Codex review). --depth build has no path
    # at all once --sources/--build-info AND -p are all absent. A
    # pack-shaped --build-info is a "possibly satisfiable" case for
    # --depth source: _combine_packs falls back to its own source_abi (L4)
    # when no --sources pack is given, so it is left as a warning, not a
    # blocker (Codex review, second finding on this signal) -- loading the
    # pack to verify it actually carries L4 evidence is out of scope here.
    if depth == "source" and sources is None and not build_info_is_pack:
        result.block(
            "--depth source was requested but no --sources was given -- a "
            "raw --build-info compile database or build directory supplies "
            "L3 build context only, never L4 source-ABI replay -- the resolved "
            "evidence depth check_requested_depth_satisfied would raise "
            "on this: the real run would exit 1."
        )
    elif depth == "source" and sources is None and build_info_is_pack:
        # CodeRabbit review: pack *shape* alone (is_pack_dir/
        # _is_inputs_pack_dir -- a manifest-shape stat + small JSON read,
        # not a full pack load per this function's own docstring) does not
        # prove the pack's manifest actually carries usable L4 source_abi
        # facts -- a manifest-only/empty pack is exactly as unsatisfiable
        # as a raw compile database, but previously fell through with no
        # signal at all here (neither the warn() above, which requires
        # build_info to also be None, nor this block). Loading the pack to
        # verify is real I/O a dry run must not perform, so -- mirroring
        # the sibling "--depth build backed by some compile database"
        # warning below -- this is only "possibly satisfiable", not
        # "definitely satisfiable".
        result.warn(
            "--depth source was requested with no --sources; it depends "
            "entirely on --build-info's own source_abi facts (a prebuilt "
            "BuildSourcePack/abicheck_inputs pack). A dry run does not "
            "load the pack to verify it actually carries L4 evidence -- "
            "if it doesn't, the real run's evidence depth check would "
            "reject it."
        )
    elif depth == "build" and sources is None and build_info is None:
        result.block(
            "--depth build was requested but no --sources/--build-info "
            "was given -- the resolved evidence depth "
            "check_requested_depth_satisfied would raise on this: the "
            "real run would exit 1."
        )
    elif (
        depth == "build"
        and sources is None
        and has_compile_db
        and compile_db_matched is False
    ):
        # External review: unlike the "nothing at all" case above, a
        # compile database was given -- loading it and checking whether it
        # matches the resolved headers is cheap, deterministic, read-only
        # resolution (the same JSON load + path match the real run performs
        # before castxml even runs), not the "real work" an earlier version
        # of this function assumed it was. compile_db_matched=False (empty,
        # entirely filtered-out, or malformed) is therefore now a definite
        # verdict, not a guess: check_requested_depth_satisfied would
        # certainly reject this in the real run (parsed_with_build_context
        # never gets stamped), so this is a blocker, not a soft warning.
        result.block(
            "--depth build was requested and --build-info resolved to a "
            "compilation database, but it has no entry matching the resolved "
            "headers (empty, entirely filtered-out, or malformed) -- the "
            "resolved evidence depth check_requested_depth_satisfied would "
            "raise on this: the real run would exit 1."
        )


def dry_run_build_context_preview(
    compile_db_path: Path | None,
    headers: tuple[Path, ...],
    compile_db_filter: str | None,
) -> tuple[list[str], bool] | None:
    """Silent, non-raising sibling of
    ``cli_helpers_compare._resolve_build_context_flags`` that also returns
    the derived castxml flags themselves -- ``dump --dry-run``'s
    :class:`~abicheck.service_dump_pipeline.DumpExecutionOptions` preview
    (ADR-063 Track T4, "Dump request contract").

    Lives here rather than alongside its sibling in ``cli_helpers_compare.py``
    (only its own ``_matched_build_context`` core import crosses that
    boundary -- the same pre-existing ``cli_dump_helpers -> cli_helpers_
    compare`` edge ``discover_project_config`` already uses below, not a new
    one) purely to keep that file's own `no_growth` debt entry
    (``architecture/debt.yaml``) from tripping -- this function's own home
    is ``render_dump_dry_run``'s module regardless, since it exists only to
    feed that renderer's own "Execution options" section.

    Same relationship to ``_resolve_build_context_flags`` that
    ``cli_helpers_compare.dry_run_compile_db_matched`` already has (loading
    and matching a compile database is cheap, deterministic, read-only
    resolution, so a dry run may perform it) -- extended to return ``flags``
    too, since a dry run reporting ``legacy_compile_db_tokens`` needs the
    actual list, not just the match verdict. Never echoes to stderr and
    never raises: an unreadable/malformed compile database folds to
    ``([], False)`` rather than the ``click.ClickException`` the real run
    would raise -- the same accepted imprecision
    ``dry_run_compile_db_matched`` already documents for the identical
    failure shape.

    Returns ``None`` when no compile database was given at all (the ``dump``
    CLI's dry-run preview reads this as "no legacy compile-db flags to
    show", the same as an execution that never threads any).
    """
    if not compile_db_path:
        return None
    from .cli_helpers_compare import _matched_build_context
    from .errors import AbicheckError

    try:
        ctx, _entry_count = _matched_build_context(
            compile_db_path, headers, compile_db_filter
        )
        return ctx.to_castxml_flags(), ctx.compile_db_path is not None
    except (AbicheckError, OSError, ValueError, click.ClickException):
        return [], False


def render_dump_dry_run(
    resolved: ResolvedDumpRequest,
    *,
    output: Path | None,
    build_config: Path | None = None,
    snapshot_compression: str = "auto",
    has_compile_db: bool = False,
    compile_db_matched: bool | None = None,
    build_info_is_pack: bool = False,
) -> Any:
    """Build the ``dump --dry-run`` report (ADR-043 D4): resolve, never execute.

    Cheap, read-only resolution only: classifies the inputs, discovers config,
    shows the resolved depth/collect-mode and available data layers, and
    checks tool availability on PATH. Never runs castxml/clang, a build
    query, or I/O beyond stat()/PATH lookups.

    ``has_compile_db`` (Codex review): whether ``-p``/``--compile-db`` was
    given at all -- a bare presence flag, kept for the "nothing was given at
    all" case below, distinct from whether it actually matches.

    ``compile_db_matched`` (external review): the *real* result of loading
    ``-p``/``--compile-db`` and checking it against the resolved headers --
    ``cli.dump_cmd`` computes this the same way the real run does
    (``cli_helpers_compare._resolve_build_context_flags``, the same JSON
    load + path match the real run performs before castxml even runs) and
    passes the outcome in, rather than this function loading it itself.
    Loading and matching a compile database is cheap, deterministic,
    read-only resolution (no compiler/frontend invocation) -- an earlier
    version of this function treated it as "real work out of scope for a
    dry run" and only checked bare presence, so a ``--depth build`` backed
    by an empty/non-matching compile database dry-ran as merely a soft
    warning even though the real run's strict depth gate would definitely
    reject it. ``None`` when no compile database was given at all (the
    "nothing was given" case is handled separately, below); ``True``/
    ``False`` is now a definite verdict, not a "possibly satisfiable" guess.

    ``build_info_is_pack`` (Codex review): whether ``--build-info`` names a
    prebuilt ``BuildSourcePack`` *or* a Flow-2 ``abicheck_inputs/`` directory
    rather than a raw compile DB / build dir -- the caller ORs
    ``buildsource.inline.is_pack_dir`` and ``cli_buildsource_helpers.
    _is_inputs_pack_dir`` (each a manifest-shape stat + small JSON read, not
    a full pack load), the same two directory kinds ``cli_buildsource.
    embed_build_source`` itself recognizes as pack-shaped (``bi_is_pack``/
    ``bi_is_inputs``). Either one's ``_combine_packs`` fallback lets a
    pack-shaped ``build_info``'s own ``source_abi`` (L4) satisfy ``--depth
    source`` with no ``--sources`` pack given, unlike a raw compile
    database, which never carries L4 facts of its own. Checking only
    ``is_pack_dir`` missed the ``abicheck_inputs/`` case (Codex review,
    second finding on this signal): that combination was wrongly treated as
    "--depth source has no path without --sources" and blocked even though
    the real (non-dry) run writes a valid source-depth snapshot from it.

    ``dump_manifest`` (ADR-050 D3, folded from the former standalone ``plan
    --dump-manifest`` diagnostic, ADR-054): a parsed
    :class:`~abicheck.dump_manifest.DumpManifest` when ``--dump-manifest``
    was given (mutually exclusive with ``headers``, enforced by the caller
    before this function is reached). When present, this function reports
    the manifest's translation units and its ``scope_fingerprint`` (ADR-050
    D1) — computed from the manifest document alone, no compiler invocation
    — the same information ``dump --dump-manifest FILE --dry-run`` now
    covers standalone, without running castxml/clang. It never computes a
    ``profile_fingerprint``: that fingerprint's ``-I`` component is a
    ``-MD``-depfile digest that only exists once a real L2 extraction
    actually runs. Text-only, like every other dry-run (ADR-043 D9,
    ``--format`` is inert against ``--dry-run`` everywhere, not just here) —
    the former standalone command's ``--format json``/``-o`` machine-readable
    output has no CLI replacement; see ADR-054's D4 for the accepted
    trade-off and the equivalent two-line Python snippet (calling
    ``dump_manifest.load_manifest`` + ``comparability.compute_extraction_contract``
    directly, the same two functions this branch itself calls below).

    A ``-p`` compile DB with no ``-H``, or a ``--debug-format``/``--dwarf``/
    ``--btf``/``--ctf`` selection against a PE/Mach-O binary, are genuine
    usage errors in the real run (``click.UsageError``/``BadParameter``,
    exit 64) -- ``cli.dump_cmd`` checks both, via
    :func:`check_dump_debug_format_error`,
    and raises directly *before* branching on ``dry_run`` at all, so this
    function is never even reached for that input on either path. Do not
    re-encode either as a :meth:`DryRunResult.block` (exit 1) here -- that
    would silently downgrade a usage error into an evidence blocker and
    disagree with the real run's actual exit code for the identical input
    (CodeRabbit review).
    """
    from .cli_helpers_compare import discover_project_config
    from .dry_run import DryRunResult, tool_status

    side = resolved.request.input
    so_path, sources, build_info = side.path, side.sources, side.build_info
    headers = resolved.headers
    depth = resolved.requested_depth
    collect_mode = resolved.collect_mode
    dump_manifest = side.dump_manifest  # raw; evidence's clears at --depth binary

    result = DryRunResult(command="dump")
    result.add(
        "Inputs",
        f"artifact: {so_path}" if so_path else "artifact: (none -- source-only dump)",
        f"headers: {', '.join(str(h) for h in headers)}" if headers else None,
    )
    if dump_manifest is not None:
        _add_dump_manifest_section(result, dump_manifest)
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
        f"ast-frontend: {resolved.effective_header_backend}",
    )
    result.add(
        "Build/source inputs",
        f"--sources: {sources}" if sources else None,
        f"--build-info: {build_info}" if build_info else None,
        # AC-007: --build-info *is* the L3 build source now -- a compile
        # database is read off it rather than promoted into it -- so there is
        # no separate "reused as L3" state to report alongside it.
        "L3 build source: --build-info resolved to a compilation database"
        if has_compile_db
        else None,
        "no --sources/--build-info given -- L0-L2 only"
        if sources is None and build_info is None and collect_mode != "off"
        else None,
    )
    result.add("Tools and frontends", *tool_status("castxml", "clang", "gcc", "g++"))
    if so_path is not None:
        _add_dump_data_layers(result, so_path, headers)
    cfg_path = build_config or discover_project_config(sources)
    result.add(
        "Configuration and value origins",
        f".abicheck.yml: {cfg_path if cfg_path else '(none found)'}",
    )
    # ADR-063 Track T4 ("Dump request contract"): render the nine execution
    # kwargs `execute_dump_request` would receive, when the caller resolved
    # them (`resolved.execution_options`, a preview for `--dry-run` -- see
    # `frontends.cli.commands.dump.dump_cmd`'s own attach step). `None`
    # (never resolved) renders nothing, same as every other optional section
    # here -- an older caller that built a `ResolvedDumpRequest` without this
    # field keeps seeing the unchanged report.
    opts = resolved.execution_options
    if opts is not None:
        result.add(
            "Execution options",
            f"build config: {opts.build_config}" if opts.build_config else None,
            f"allow build query: {opts.allow_build_query}",
            f"legacy compile-db flags: {len(opts.legacy_compile_db_tokens)} "
            f"derived ({'matched' if opts.legacy_compile_db_matched else 'no match'})"
            if opts.legacy_compile_db_tokens or opts.legacy_compile_db_matched
            else None,
            f"seed collect mode: {opts.seed_collect_mode}"
            if opts.seed_collect_mode
            else None,
            "source frontend from folded context: "
            f"{opts.source_frontend_from_folded_context}",
        )
    if output is not None:
        from .errors import SnapshotError
        from .workflows.storage import SnapshotCompression, resolve_write_compression

        try:
            resolved_compression = resolve_write_compression(
                output, SnapshotCompression(snapshot_compression)
            )
        except SnapshotError as exc:
            raise click.UsageError(str(exc)) from exc
        compression_line = (
            f"compression: {resolved_compression.value} (file will not be created)"
        )
    else:
        compression_line = "compression: (n/a -- stdout is always plain JSON)"
    result.add(
        "Output and exit-code behavior",
        f"output: {output if output else 'stdout'}",
        compression_line,
        "exit codes: 0 valid, 1 requested depth not satisfiable, 64 usage error",
    )
    _add_dump_depth_feasibility(
        result,
        so_path=so_path,
        sources=sources,
        build_info=build_info,
        depth=depth,
        has_compile_db=has_compile_db,
        compile_db_matched=compile_db_matched,
        build_info_is_pack=build_info_is_pack,
        dump_manifest=dump_manifest,
    )
    return result


# `compile_db_from_build_info` moved to `header_conditionals.py` (PR C, CLI
# cleanup phase two) alongside `attach_build_context`/`user_define_flags` --
# imported above under its original name; every existing caller/test is
# unchanged. See that module for the derivation logic.


def resolve_dump_compile_context(
    resolved_compile_context: CompileContext | None,
    *,
    gcc_options: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    header_backend: str,
    includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None,
    frontend_context: str = "host",
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
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
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=includes,
        build_config=build_config,
        sources=sources,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
    )
