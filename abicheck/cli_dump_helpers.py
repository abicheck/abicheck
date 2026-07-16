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
) -> Any:
    """Build the ``dump --dry-run`` report (ADR-043 D4): resolve, never execute.

    Cheap, read-only resolution only: classifies the inputs, discovers config,
    shows the resolved depth/collect-mode and available data layers, and
    checks tool availability on PATH. Never runs castxml/clang, a build query,
    or any I/O beyond stat()/PATH lookups.
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
) -> None:
    """Handle the PE/Mach-O native dump path and output writing (split from cli.py).

    ``dump_native_binary``/``stamp_provenance``/``write_snapshot_output`` are all
    passed in from cli.py rather than imported, mirroring ``perform_elf_dump`` —
    the AST-based import-cycle gate counts *any* import (including a lazy
    function-body ``from .cli_resolve import …`` and a ``TYPE_CHECKING`` import),
    so importing them here would close a ``cli → cli_dump_helpers → … → cli``
    cycle. ``compile_context`` is typed ``Any`` for the same reason (its concrete
    ``CompileContext`` lives in ``service_scan``).
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
        extractor=header_backend, inputs_pack=inputs_pack,
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
) -> None:
    """Run the ELF dump pipeline and write output.

    ``debug_info_path`` (P1.1, ADR-021a): a resolved detached debug artifact
    (``--debug-root``/``--debuginfod``) to read DWARF sections from instead of
    ``so_path`` itself — threaded straight into :func:`dumper.dump`.

    All helper callables (expand_header_inputs, populate_dependency_info,
    stamp_provenance, write_snapshot_output) are passed in from cli.py to avoid
    an import cycle — cli_dump_helpers must not import from cli.
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
        raise click.ClickException(str(exc)) from exc
    finally:
        # The header parse has consumed the build-seeded include dirs (success or
        # failure), so release any inferred-CMake temp build dir now.
        if _l2_pending_cleanups:
            _run_cleanups(_l2_pending_cleanups)

    # Record that the header AST was parsed with the real build context (ADR-029)
    if effective_compile_db and resolved_headers:
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
    )
