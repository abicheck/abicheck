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

"""CLI — abicheck dump | compare | compat (dump | check)."""
from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .checker import DiffResult, LibraryMetadata, Verdict, compare
from .compat.abicc_dump_import import import_abicc_perl_dump, looks_like_perl_dump
from .compat.cli import compat_group
from .dumper import dump
from .errors import AbicheckError
from .reporter import to_json
from .serialization import load_snapshot, snapshot_to_json

if TYPE_CHECKING:
    from .appcompat import AppRequirements
    from .bundle import BundleDiffResult
    from .checker_types import DiffResult
    from .debug_resolver import DebugArtifact
    from .policy_file import PolicyFile
    from .severity import SeverityConfig
    from .suppression import SuppressionList

from . import __version__ as _abicheck_version
from .model import AbiSnapshot

# Number of bytes to read when sniffing file format (covers ELF magic + JSON/Perl head)
_SNIFF_BYTES = 256

_logger = logging.getLogger("abicheck")

_HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp", ".inc"}


def _expand_header_inputs(inputs: list[Path]) -> list[Path]:
    """Expand header inputs where each item can be a file or a directory.

    Directories are scanned recursively for known header extensions.
    """
    out: list[Path] = []
    for p in inputs:
        if not p.exists():
            raise click.ClickException(f"Header file not found or not a file: {p}")
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found = [
                f for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in _HEADER_EXTS
            ]
            if not found:
                raise click.ClickException(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(sorted(found))
            continue
        raise click.ClickException(f"Header path is neither file nor directory: {p}")

    # Deduplicate while preserving deterministic order
    seen: set[str] = set()
    deduped: list[Path] = []
    for h in out:
        k = str(h.resolve())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(h)
    return deduped


def _setup_verbosity(verbose: bool) -> None:
    """Configure logging verbosity for native commands."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if verbose else logging.WARNING)


def _detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns 'elf', 'pe', 'macho', or None for non-binary / unknown.
    """
    from .binary_utils import detect_binary_format
    return detect_binary_format(path)


def _safe_write_output(output: Path, text: str) -> None:
    """Write *text* to *output*, creating parent directories as needed."""
    try:
        parent = output.parent
        if not parent.exists():
            click.echo(f"Creating output directory: {parent}", err=True)
            parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Cannot write to {output}: {exc}") from exc


def _stamp_provenance(
    snap: AbiSnapshot,
    *,
    git_tag: str | None,
    build_id: str | None,
    no_git: bool,
) -> None:
    """Fill provenance metadata on a snapshot (mutates in place)."""
    import datetime
    import subprocess

    snap.created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    snap.git_tag = git_tag
    snap.build_id = build_id

    if not no_git:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                snap.git_commit = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # git not available or not a repo — leave as None


def _write_snapshot_output(
    snap: AbiSnapshot,
    output: Path | None,
) -> None:
    """Serialize snapshot and write to file or stdout."""
    result = snapshot_to_json(snap)
    if output:
        _safe_write_output(output, result)
        click.echo(f"Snapshot written to {output}", err=True)
    else:
        click.echo(result)


def _sniff_text_format(path: Path) -> str:
    """Read a small header chunk and return 'json', 'perl', or 'unknown'."""
    try:
        with open(path, "rb") as f:
            raw = f.read(_SNIFF_BYTES)
        head = raw.decode("utf-8", errors="replace").lstrip()
    except OSError:
        return "unknown"
    # Check Perl dump BEFORE JSON — a Perl dump can start with $VAR1 = {
    # which would incorrectly match the JSON heuristic after the '{'
    if looks_like_perl_dump(head):
        return "perl"
    if head.startswith("{"):
        return "json"
    return "unknown"


def _dump_elf(
    path: Path, headers: list[Path], includes: list[Path],
    version: str, lang: str, *, dwarf_only: bool = False,
    debug_format: str | None = None,
) -> AbiSnapshot:
    """Dump ABI snapshot from an ELF binary."""
    resolved_headers = _expand_header_inputs(headers) if headers else []
    if not resolved_headers and not dwarf_only:
        click.echo(
            f"Warning: '{path}' — no headers provided. "
            "Will use DWARF debug info if available, else symbols-only mode.",
            err=True,
        )
    if resolved_headers and not dwarf_only:
        for inc in includes:
            if not inc.exists() or not inc.is_dir():
                raise click.ClickException(f"Include directory not found or not a directory: {inc}")
    elif includes and not dwarf_only:
        click.echo(
            "Warning: --include paths are ignored without headers.",
            err=True,
        )
    compiler = "cc" if lang == "c" else "c++"
    try:
        return dump(
            so_path=path,
            headers=resolved_headers,
            extra_includes=includes,
            version=version,
            compiler=compiler,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(f"Failed to dump '{path}': {exc}") from exc


def _dump_macho(path: Path, version: str) -> AbiSnapshot:
    """Dump ABI snapshot from a Mach-O binary."""
    from .macho_metadata import parse_macho_metadata
    try:
        macho_meta = parse_macho_metadata(path)
    except (RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(
            f"Failed to parse Mach-O '{path}': {exc}"
        ) from exc
    if not macho_meta.exports and not macho_meta.install_name and not macho_meta.dependent_libs:
        raise click.ClickException(
            f"Mach-O file '{path}' has no exports or load-command metadata. "
            "Verify the file is a valid dynamic library."
        )
    from .model import Function, Visibility
    funcs = [
        Function(
            name=exp.name, mangled=exp.name, return_type="?",
            visibility=Visibility.PUBLIC,
            is_extern_c=not exp.name.startswith("_Z"),
        )
        for exp in macho_meta.exports if exp.name
    ]
    return AbiSnapshot(
        library=path.name, version=version,
        functions=funcs, macho=macho_meta,
        platform="macho",
    )


def _dump_native_binary(
    path: Path, binary_fmt: str,
    headers: list[Path], includes: list[Path],
    version: str, lang: str,
    *,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
) -> AbiSnapshot:
    """Dump ABI snapshot from a native binary (ELF, PE, or Mach-O).

    For ELF, headers are required for full AST analysis unless dwarf_only
    is set or DWARF debug info is available (ADR-003 fallback chain).
    For PE/Mach-O, headers are optional — export tables provide the symbol surface.
    """
    if binary_fmt == "elf":
        return _dump_elf(path, headers, includes, version, lang,
                         dwarf_only=dwarf_only, debug_format=debug_format)

    if binary_fmt == "pe":
        from .service import _dump_pe
        try:
            return _dump_pe(path, version, pdb_path=pdb_path)
        except AbicheckError as exc:
            raise click.ClickException(str(exc)) from exc

    if binary_fmt == "macho":
        return _dump_macho(path, version)

    fmt_labels = {"elf": "ELF", "pe": "PE (Windows DLL)", "macho": "Mach-O (macOS dylib)"}
    raise click.ClickException(f"Unsupported binary format: {fmt_labels.get(binary_fmt, binary_fmt)}")


def _resolve_input(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    is_elf: bool | None = None,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
) -> AbiSnapshot:
    """Auto-detect input type and return an AbiSnapshot.

    Detection order:
    1. Native binary (ELF / PE / Mach-O, detected by magic bytes)
    2. ABICC Perl dump (``$VAR1`` prefix) → :func:`import_abicc_perl_dump`
    3. JSON snapshot (``{`` prefix) → :func:`load_snapshot`

    Args:
        path: Path to the input file.
        headers: Public header files (required for ELF inputs).
        includes: Extra include directories (used for ELF inputs).
        version: Version label to embed in the resulting snapshot.
        lang: Language mode for castxml (``c++`` or ``c``).
        is_elf: Pre-computed ELF detection result; if *None*, detection is
            performed here (avoids a second ``open()`` when the caller already
            knows the result).
        dwarf_only: If True, force DWARF-only mode (ADR-003).
        debug_format: Force debug format ("dwarf", "btf", "ctf") or None for auto.
    """
    # Fast path: caller already knows it's ELF
    if is_elf is True:
        return _dump_native_binary(
            path, "elf", headers, includes, version, lang,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )

    # Detect binary format from magic bytes
    binary_fmt = _detect_binary_format(path) if is_elf is None else None
    if binary_fmt is not None:
        return _dump_native_binary(
            path, binary_fmt, headers, includes, version, lang,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )

    # Text-based formats: detect by sniffing only a small header chunk
    fmt = _sniff_text_format(path)

    if fmt == "perl":
        try:
            return import_abicc_perl_dump(path)
        except (ValueError, KeyError, UnicodeDecodeError, OSError, AbicheckError) as exc:
            raise click.ClickException(
                f"Failed to import ABICC Perl dump '{path}': {exc}"
            ) from exc

    if fmt == "json":
        try:
            return load_snapshot(path)
        except (ValueError, KeyError, UnicodeDecodeError, OSError) as exc:
            raise click.ClickException(
                f"Failed to load JSON snapshot '{path}': {exc}"
            ) from exc

    raise click.UsageError(
        f"Cannot detect format of '{path}'. "
        "Expected: ELF (.so), PE (.dll), Mach-O (.dylib), JSON snapshot, or ABICC Perl dump."
    )


def _collect_metadata(path: Path) -> LibraryMetadata | None:
    """Compute SHA-256 and file size for a library artifact.

    Returns *None* when *path* is a text-based snapshot (JSON or Perl dump)
    so that reports don't display misleading metadata for the serialised file.
    """
    text_fmt = _sniff_text_format(path)
    if text_fmt in ("json", "perl"):
        return None

    import hashlib

    data = path.read_bytes()
    return LibraryMetadata(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


@click.group()
@click.version_option(
    version=_abicheck_version,
    prog_name="abicheck",
    message="%(prog)s %(version)s (napetrov/abicheck)",
)
def main() -> None:
    """abicheck — ABI compatibility checker for C/C++ shared libraries."""


def _populate_dependency_info(
    snap: AbiSnapshot, so_path: Path,
    search_paths: list[Path], sysroot: Path | None, ld_library_path: str,
) -> None:
    """Resolve transitive deps and store DependencyInfo in the snapshot."""
    from .binder import BindingStatus, compute_bindings
    from .model import DependencyInfo
    from .resolver import resolve_dependencies

    graph = resolve_dependencies(
        so_path,
        search_paths=search_paths or None,
        sysroot=sysroot,
        ld_library_path=ld_library_path,
    )
    bindings = compute_bindings(graph)

    summary: dict[str, int] = {}
    for b in bindings:
        summary[b.status.value] = summary.get(b.status.value, 0) + 1

    missing = [
        {"consumer": b.consumer, "symbol": b.symbol, "version": b.version}
        for b in bindings if b.status == BindingStatus.MISSING
    ]

    snap.dependency_info = DependencyInfo(
        nodes=[
            {
                "path": str(node.path),
                "soname": node.soname,
                "needed": node.needed,
                "depth": node.depth,
                "resolution_reason": node.resolution_reason,
            }
            for node in sorted(graph.nodes.values(), key=lambda n: (n.depth, n.soname))
        ],
        edges=[
            {"consumer": consumer, "provider": provider}
            for consumer, provider in graph.edges
        ],
        unresolved=[
            {"consumer": consumer, "soname": soname}
            for consumer, soname in graph.unresolved
        ],
        bindings_summary=summary,
        missing_symbols=missing,
    )


@main.command("dump")
@click.argument("so_path", type=click.Path(exists=True, path_type=Path))
@click.option("-H", "--header", "headers", multiple=True, type=click.Path(exists=True, path_type=Path),
              help="Public header file or directory (repeat for multiple).")
@click.option("-I", "--include", "includes", multiple=True, type=click.Path(path_type=Path),
              help="Extra include directory for castxml.")
@click.option("--version", "version", default="unknown", show_default=True,
              help="Library version string to embed in snapshot.")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for castxml.")
@click.option("-o", "--output", "output", type=click.Path(path_type=Path), default=None,
              help="Output JSON file. Defaults to stdout.")
# ── Cross-compilation flags ───────────────────────────────────────────────────
@click.option("--gcc-path", default=None,
              help="Path to GCC/G++ cross-compiler binary.")
@click.option("--gcc-prefix", default=None,
              help="Cross-toolchain prefix (e.g. aarch64-linux-gnu-).")
@click.option("--gcc-options", default=None,
              help="Extra compiler flags passed through to castxml.")
@click.option("--sysroot", type=click.Path(path_type=Path), default=None,
              help="Alternative system root directory.")
@click.option("--nostdinc", is_flag=True, default=False,
              help="Do not search standard system include paths.")
@click.option("--pdb-path", "pdb_path", type=click.Path(path_type=Path), default=None,
              help="Explicit path to PDB file for Windows PE debug info. "
                   "Overrides automatic PDB discovery from the PE debug directory.")
@click.option("--follow-deps", is_flag=True, default=False,
              help="Resolve transitive DT_NEEDED dependencies and include the full "
                   "dependency graph and symbol binding status in the snapshot. "
                   "ELF only.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries (with --follow-deps).")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (with --follow-deps).")
@click.option("--dwarf-only", is_flag=True, default=False,
              help="Force DWARF-only mode: use DWARF debug info as the primary "
                   "data source even when headers are available. Enables 24/30 "
                   "detectors without requiring castxml.")
@click.option("--show-data-sources", is_flag=True, default=False,
              help="Print which data layers (L0/L1/L2) are available for the "
                   "binary and exit.")
@click.option("--btf", "debug_format", flag_value="btf", default=None,
              help="Force BTF debug format (ELF only).")
@click.option("--ctf", "debug_format", flag_value="ctf",
              help="Force CTF debug format (ELF only).")
@click.option("--dwarf", "debug_format", flag_value="dwarf",
              help="Force DWARF debug format (ELF only).")
# ── Build context capture (ADR-020) ──────────────────────────────────────────
@click.option("-p", "--build-dir", "compile_db_path", type=click.Path(path_type=Path), default=None,
              help="Build directory containing compile_commands.json, or path to the "
                   "file itself. Enables deterministic header parsing with exact build "
                   "flags. Requires -H/--header.")
@click.option("--compile-db", "compile_db_path_alt", type=click.Path(path_type=Path), default=None,
              help="Explicit path to compile_commands.json (alias for -p).")
@click.option("--compile-db-filter", "compile_db_filter", default=None,
              help="Glob pattern to filter compile_commands.json entries by source file "
                   "(e.g. 'src/libfoo/**'). Useful for large databases.")
# ── Debug artifact resolution (ADR-021) ──────────────────────────────────────
@click.option("--debug-root", "debug_roots", multiple=True, type=click.Path(path_type=Path),
              help="Directory containing separate debug files (build-id trees, "
                   "path-mirror debug files, or dSYM bundles). Can be repeated.")
@click.option("--debuginfod", is_flag=True, default=False,
              help="Enable debuginfod network resolution for debug info (opt-in). "
                   "Uses DEBUGINFOD_URLS environment variable or --debuginfod-url.")
@click.option("--debuginfod-url", "debuginfod_url", default=None,
              help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Enable verbose/debug output.")
# ── Provenance metadata ──────────────────────────────────────────────────────
@click.option("--git-tag", "git_tag", default=None,
              help="Git tag to embed in the snapshot (e.g. v2.0.0).")
@click.option("--build-id", "build_id", default=None,
              help="Opaque build identifier (CI run ID, build number, etc.).")
@click.option("--no-git", "no_git", is_flag=True, default=False,
              help="Do not auto-detect git commit SHA.")
def dump_cmd(so_path: Path, headers: tuple[Path, ...], includes: tuple[Path, ...],
             version: str, lang: str, output: Path | None,
             gcc_path: str | None, gcc_prefix: str | None, gcc_options: str | None,
             sysroot: Path | None, nostdinc: bool, pdb_path: Path | None,
             follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
             dwarf_only: bool, show_data_sources: bool,
             debug_format: str | None,
             compile_db_path: Path | None, compile_db_path_alt: Path | None,
             compile_db_filter: str | None,
             debug_roots: tuple[Path, ...],
             debuginfod: bool, debuginfod_url: str | None,
             verbose: bool,
             git_tag: str | None, build_id: str | None, no_git: bool) -> None:
    """Dump ABI snapshot of a shared library to JSON.

    \b
    Example:
      abicheck dump libfoo.so.1 -H include/foo.h --version 1.2.3 -o snap.json
      abicheck dump libfoo.so.1 -H include/foo.h --lang c -o snap.json
      abicheck dump libfoo.so.1 -H include/foo.h --gcc-prefix aarch64-linux-gnu-
      abicheck dump libfoo.so.1 --follow-deps -o snap.json
      abicheck dump libfoo.so.1 --dwarf-only -o snap.json
      abicheck dump libfoo.so.1 --show-data-sources
      abicheck dump libfoo.so.1 -H include/ -p build/  # build context from compile_commands.json
      abicheck dump libfoo.so.1 --debug-root /usr/lib/debug  # separate debug files
    """
    _setup_verbosity(verbose)

    # Resolve -p / --compile-db aliases
    effective_compile_db = compile_db_path or compile_db_path_alt
    if effective_compile_db and not headers:
        raise click.UsageError(
            "Compilation database (-p / --compile-db) requires -H/--header. "
            "Without headers, CastXML has nothing to parse."
        )

    # --show-data-sources: diagnostic output and exit
    if show_data_sources:
        _print_data_sources(so_path, bool(headers))
        return

    # Auto-detect binary format — PE/Mach-O skip the ELF/castxml path
    binary_fmt = _detect_binary_format(so_path)
    if debug_format is not None and binary_fmt in ("pe", "macho"):
        raise click.BadParameter(
            f"--{debug_format} is only supported for ELF binaries, not {binary_fmt.upper()}."
        )
    if binary_fmt in ("pe", "macho"):
        _handle_non_elf_dump(
            so_path,
            binary_fmt,
            headers,
            includes,
            version,
            lang,
            pdb_path,
            follow_deps,
            git_tag,
            build_id,
            no_git,
            output,
        )
        return

    build_context_flags = _resolve_build_context_flags(
        effective_compile_db, headers, compile_db_filter,
    )
    effective_gcc_options = _merge_gcc_options(build_context_flags, gcc_options)

    # Debug artifact resolution (ADR-021): resolve before dump
    if debug_roots or debuginfod:
        artifact = _resolve_debug_artifact(
            so_path, debug_roots, debuginfod, debuginfod_url,
        )
        if artifact:
            click.echo(f"Debug info: {artifact.source}", err=True)

    compiler = "cc" if lang == "c" else "c++"
    resolved_headers = _expand_header_inputs(list(headers)) if headers else []
    try:
        snap = dump(
            so_path=so_path,
            headers=resolved_headers,
            extra_includes=list(includes),
            version=version,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=effective_gcc_options,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if follow_deps:
        _populate_dependency_info(snap, so_path, list(search_paths), sysroot, ld_library_path)

    _stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    _write_snapshot_output(snap, output)


def _handle_non_elf_dump(
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
) -> None:
    """Handle PE/Mach-O native dump path and output writing."""
    if follow_deps:
        click.echo("Warning: --follow-deps is only supported for ELF binaries.", err=True)
    try:
        snap = _dump_native_binary(
            so_path, binary_fmt, list(headers), list(includes), version, lang,
            pdb_path=pdb_path,
        )
    except click.ClickException:
        raise
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _stamp_provenance(snap, git_tag=git_tag, build_id=build_id, no_git=no_git)
    _write_snapshot_output(snap, output)


def _resolve_build_context_flags(
    effective_compile_db: Path | None,
    headers: tuple[Path, ...],
    compile_db_filter: str | None,
) -> list[str]:
    """Resolve compile database into castxml flags for dump."""
    if not effective_compile_db:
        return []
    try:
        from .build_context import (
            build_context_for_header,
            build_context_union_fallback,
            load_compile_db,
        )
        db_entries = load_compile_db(effective_compile_db)
        resolved_hdrs = _expand_header_inputs(list(headers)) if headers else []
        if resolved_hdrs:
            ctx = build_context_for_header(
                db_entries, resolved_hdrs[0], source_filter=compile_db_filter,
            )
        else:
            ctx = build_context_union_fallback(db_entries, source_filter=compile_db_filter)
        flags = ctx.to_castxml_flags()
        if flags:
            click.echo(
                f"Build context: {len(db_entries)} entries from "
                f"{effective_compile_db}, {len(flags)} flags derived",
                err=True,
            )
            if ctx.has_conflicts:
                click.echo(
                    "Warning: conflicting flags detected in compile database; "
                    "using first-match values. See --verbose for details.",
                    err=True,
                )
        return flags
    except (AbicheckError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


def _merge_gcc_options(build_context_flags: list[str], gcc_options: str | None) -> str | None:
    """Merge compile-db derived flags with explicit gcc options."""
    if not build_context_flags:
        return gcc_options
    merged = " ".join(build_context_flags)
    return f"{merged} {gcc_options}" if gcc_options else merged


def _resolve_debug_artifact(
    so_path: Path,
    debug_roots: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
) -> DebugArtifact | None:
    """Resolve optional separate debug artifacts for dump."""
    from .debug_resolver import resolve_debug_info

    return resolve_debug_info(
        so_path,
        debug_roots=list(debug_roots) or None,
        enable_debuginfod=debuginfod,
        debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
    )


def _print_data_sources(so_path: Path, has_headers: bool) -> None:
    """Print data source diagnostic information for a binary."""
    from .dwarf_snapshot import show_data_sources

    binary_fmt = _detect_binary_format(so_path)
    elf_meta = None
    dwarf_meta = None

    if binary_fmt == "elf":
        from .dwarf_unified import parse_dwarf
        from .elf_metadata import parse_elf_metadata
        elf_meta = parse_elf_metadata(so_path)
        dwarf_meta, _ = parse_dwarf(so_path)

    click.echo(show_data_sources(so_path, elf_meta, dwarf_meta, has_headers))


def _resolve_per_side_options(
    headers: tuple[Path, ...], includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Resolve per-side headers/includes: --old-header overrides -H, etc."""
    old_h = list(old_headers_only) if old_headers_only else list(headers)
    new_h = list(new_headers_only) if new_headers_only else list(headers)
    old_inc = list(old_includes_only) if old_includes_only else list(includes)
    new_inc = list(new_includes_only) if new_includes_only else list(includes)
    return old_h, new_h, old_inc, new_inc


def _warn_ignored_flags(
    old_is_binary: bool, new_is_binary: bool,
    headers: tuple[Path, ...], includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
) -> None:
    """Warn if dump-only options are provided but not used (both inputs are snapshots)."""
    if old_is_binary or new_is_binary:
        return
    flag_pairs: list[tuple[tuple[Path, ...], str]] = [
        (headers, "-H/--header"),
        (old_headers_only, "--old-header"),
        (new_headers_only, "--new-header"),
        (includes, "-I/--include"),
        (old_includes_only, "--old-include"),
        (new_includes_only, "--new-include"),
    ]
    ignored_flags = [label for value, label in flag_pairs if value]
    if ignored_flags:
        click.echo(
            f"Warning: {', '.join(ignored_flags)} ignored when both inputs are snapshots.",
            err=True,
        )


def _load_suppression_and_policy(
    suppress: Path | None, policy: str, policy_file_path: Path | None,
    *,
    strict_suppressions: bool = False,
    require_justification: bool = False,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from CLI arguments."""
    from .policy_file import PolicyFile
    from .suppression import SuppressionList

    suppression: SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = SuppressionList.load(
                suppress, require_justification=require_justification,
            )
        except OSError as e:
            raise click.BadParameter(str(e), param_hint="--suppress") from e
        except ValueError as e:
            msg = str(e)
            if "no 'reason' field" in msg:
                raise click.ClickException(msg) from e
            raise click.BadParameter(msg, param_hint="--suppress") from e
        if strict_suppressions:
            expired = suppression.check_expired_strict()
            if expired:
                parts = [
                    f"ERROR: {len(expired)} expired suppression rule(s) "
                    f"found in {suppress}:"
                ]
                for idx, rule in expired:
                    target = (
                        rule.symbol_pattern and f'symbol_pattern="{rule.symbol_pattern}"'
                        or rule.symbol and f'symbol="{rule.symbol}"'
                        or rule.type_pattern and f'type_pattern="{rule.type_pattern}"'
                        or rule.source_location and f'source_location="{rule.source_location}"'
                        or "?"
                    )
                    parts.append(
                        f"  Rule {idx + 1}: {target} expired on {rule.expires}"
                    )
                parts.append(
                    "Remove or renew expired rules before proceeding."
                )
                raise click.ClickException("\n".join(parts))

    pf: PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise click.ClickException(str(e)) from e
        except (ValueError, OSError) as e:
            raise click.BadParameter(str(e), param_hint="--policy-file") from e
        if policy != "strict_abi":
            click.echo(
                f"Warning: --policy={policy!r} is ignored when --policy-file is given. "
                "Set base_policy in the YAML file to override the base policy.",
                err=True,
            )
    return suppression, pf


def _validate_show_only(
    ctx: click.Context, param: click.Parameter, value: str | None,
) -> str | None:
    """Eagerly validate --show-only tokens so invalid ones surface early."""
    if value is None:
        return None
    from .reporter import ShowOnlyFilter
    try:
        ShowOnlyFilter.parse(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    return value


def _render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Render comparison result in the requested output format."""
    from .service import render_output
    return render_output(
        fmt, result, old, new,
        follow_deps=follow_deps, show_only=show_only,
        report_mode=report_mode, show_impact=show_impact,
        stat=stat, severity_config=severity_config,
    )


def _collect_additions(result: DiffResult) -> list[object]:
    """Collect additive changes in a policy-independent way."""
    from .checker_policy import COMPATIBLE_KINDS
    addition_kinds = {k for k in COMPATIBLE_KINDS if k.value.endswith("_added")}
    return [c for c in result.changes if c.kind in addition_kinds]


def _run_compare_pair(
    old_input: Path,
    new_input: Path,
    old_headers: list[Path],
    new_headers: list[Path],
    old_includes: list[Path],
    new_includes: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    old_pdb_path: Path | None,
    new_pdb_path: Path | None,
) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """Run compare for one old/new pair and return result + resolved snapshots."""
    old_fmt = _detect_binary_format(old_input)
    new_fmt = _detect_binary_format(new_input)

    old = _resolve_input(
        old_input,
        old_headers,
        old_includes,
        old_version,
        lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=old_pdb_path,
    )
    new = _resolve_input(
        new_input,
        new_headers,
        new_includes,
        new_version,
        lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=new_pdb_path,
    )

    suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
    result = compare(old, new, suppression=suppression, policy=policy, policy_file=pf)
    result.old_metadata = _collect_metadata(old_input)
    result.new_metadata = _collect_metadata(new_input)
    return result, old, new


def _canonical_library_key(path: Path) -> str:
    """Canonical key used to match libraries across releases.

    For ELF versioned names, canonicalize to ``*.so`` (e.g. ``libfoo.so.1.2`` → ``libfoo.so``).
    """
    lower = path.name.lower()
    m = re.search(r"\.so(?:\.|$)", lower)
    if m:
        return lower[: m.start() + 3]
    return lower


def _version_sort_key(path: Path, canonical_key: str) -> tuple[list[tuple[int, int | str]], str]:
    """Build a version-aware sort key for ambiguous library candidates."""
    lower = path.name.lower()
    remainder = lower
    if canonical_key.endswith(".so") and canonical_key in lower:
        remainder = lower[lower.find(canonical_key) + len(canonical_key):]
    # strip known wrapper extensions for snapshots/dumps
    for suffix in (".json", ".pl", ".pm"):
        if remainder.endswith(suffix):
            remainder = remainder[: -len(suffix)]
            break
    remainder = remainder.lstrip("._-")
    tokens = re.findall(r"\d+|[a-z]+", remainder)
    parsed: list[tuple[int, int | str]] = []
    for tok in tokens:
        if tok.isdigit():
            parsed.append((1, int(tok)))
        else:
            parsed.append((0, tok))
    return parsed, lower


def _is_supported_compare_input(path: Path) -> bool:
    """Return True for files accepted by compare-release directory scanning.

    Delegates to :func:`abicheck.classify.is_supported_compare_input` which
    runs a composable classifier pipeline (binary extensions → magic bytes →
    ABI JSON fingerprint → Perl dump → fallback sniff).

    To add support for a new ABI snapshot format, edit ``abicheck/classify.py``
    rather than this function.
    """
    from .classify import is_supported_compare_input
    return is_supported_compare_input(path)


def _collect_release_inputs(path: Path) -> list[Path]:
    """Collect compare-able inputs from a file or directory."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise click.ClickException(f"Input path is neither file nor directory: {path}")
    files = [p for p in sorted(path.rglob("*")) if _is_supported_compare_input(p)]
    if not files:
        raise click.ClickException(f"No supported ABI inputs found in directory: {path}")
    return files


def _build_match_map(paths: list[Path]) -> tuple[dict[str, Path], list[str]]:
    """Build key->path map with version-aware duplicate resolution."""
    buckets: dict[str, list[Path]] = {}
    for p in paths:
        buckets.setdefault(_canonical_library_key(p), []).append(p)

    mapping: dict[str, Path] = {}
    warnings: list[str] = []
    for key, vals in buckets.items():
        ordered = sorted(vals, key=lambda x: _version_sort_key(x, key))
        selected = ordered[-1]
        mapping[key] = selected
        if len(ordered) > 1:
            warnings.append(
                f"Ambiguous match for '{key}': {[v.name for v in ordered]}; using '{selected.name}'"
            )
    return mapping, warnings


# ---------------------------------------------------------------------------
# Shared helpers for CLI commands
# ---------------------------------------------------------------------------

def _resolve_severity(
    preset: str | None,
    abi_breaking: str | None,
    potential_breaking: str | None,
    quality_issues: str | None,
    addition: str | None,
) -> tuple[SeverityConfig, bool]:
    """Resolve severity configuration and return (config, explicitly_set)."""
    from .severity import resolve_severity_config
    explicitly_set = any(v is not None for v in (
        preset, abi_breaking, potential_breaking, quality_issues, addition,
    ))
    config = resolve_severity_config(
        preset=preset,
        abi_breaking=abi_breaking,
        potential_breaking=potential_breaking,
        quality_issues=quality_issues,
        addition=addition,
    )
    return config, explicitly_set



def _merge_redundant_changes(result: DiffResult) -> None:
    """Re-merge redundant changes back into the main change list."""
    for c in result.changes:
        if c.caused_count > 0:
            c.caused_count = 0
    for c in result.redundant_changes:
        c.caused_by_type = None
    result.changes = result.changes + result.redundant_changes
    result.redundant_changes = []
    result.redundant_count = 0


def _warn_all_suppressed(result: DiffResult) -> None:
    """Warn if a suppression file swallowed all changes."""
    total_changes = len(result.changes) + result.suppressed_count
    if result.suppression_file_provided and total_changes > 0 and len(result.changes) == 0:
        click.echo(
            "Warning: all ABI changes were suppressed by the suppression file. "
            "Verify your suppression rules are not too broad.",
            err=True,
        )


def _maybe_emit_annotations(
    result: DiffResult,
    *,
    annotate: bool,
    annotate_additions: bool,
    write_step_summary: bool = True,
) -> None:
    """Emit GitHub annotations to stderr if --annotate is set and running in CI."""
    if not annotate:
        return

    from .annotations import (
        collect_annotations,
        emit_github_step_summary,
        format_annotations,
        is_github_actions,
    )

    if not is_github_actions():
        return

    annotations = collect_annotations(result, annotate_additions=annotate_additions)
    text = format_annotations(annotations)
    if text:
        click.echo(text, err=True)

    if write_step_summary:
        emit_github_step_summary(result)


def _write_release_step_summary(text: str, fmt: str) -> None:
    """Write a single step summary for compare-release when running in CI."""
    import os as _os

    summary_path = _os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    from .annotations import is_github_actions

    if not is_github_actions():
        return

    # For markdown output, write the summary directly.
    # For JSON, wrap it in a code block.
    if fmt == "json":
        content = f"```json\n{text}\n```\n"
    else:
        content = text + "\n"

    with open(summary_path, "a", encoding="utf-8") as f:
        f.write(content)


def _write_or_echo(output: Path | None, text: str) -> None:
    """Write text to file or echo to stdout."""
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)


def _exit_with_severity_or_verdict(
    result: DiffResult, sev_config: SeverityConfig | None, severity_explicitly_set: bool,
) -> None:
    """Exit with appropriate code based on severity config or legacy verdict."""
    from .severity import compute_exit_code
    if severity_explicitly_set:
        assert sev_config is not None
        eff_sets = result._effective_kind_sets()
        exit_code = compute_exit_code(result.changes, sev_config, kind_sets=eff_sets)
        if exit_code != 0:
            sys.exit(exit_code)
    else:
        if result.verdict.value == "BREAKING":
            sys.exit(4)
        elif result.verdict.value == "API_BREAK":
            sys.exit(2)


@main.command("compare")
@click.argument("old_input", type=click.Path(exists=True, path_type=Path))
@click.argument("new_input", type=click.Path(exists=True, path_type=Path))
# ── Dump options (used when input is an ELF binary) ──────────────────────────
@click.option("-H", "--header", "headers", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header file or directory applied to both sides (repeat for multiple). "
                   "Recommended for full ELF ABI analysis; without headers, ELF falls back to symbols-only mode. "
                   "Validated when input is ELF; ignored for snapshots.")
@click.option("-I", "--include", "includes", multiple=True,
              type=click.Path(path_type=Path),
              help="Extra include directory for castxml (applied to both sides).")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for castxml.")
@click.option("--old-header", "old_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for old side only (overrides -H for old). "
                   "Validated when input is ELF; ignored for snapshots.")
@click.option("--new-header", "new_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for new side only (overrides -H for new). "
                   "Validated when input is ELF; ignored for snapshots.")
@click.option("--old-include", "old_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for old side only (overrides -I for old).")
@click.option("--new-include", "new_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for new side only (overrides -I for new).")
@click.option("--old-version", "old_version", default="old", show_default=True,
              help="Version label for old side (used when input is a .so file).")
@click.option("--new-version", "new_version", default="new", show_default=True,
              help="Version label for new side (used when input is a .so file).")
# ── Compare options (unchanged) ──────────────────────────────────────────────
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "sarif", "html", "junit"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("--suppress", type=click.Path(exists=True, path_type=Path), default=None,
              help="Suppression file (YAML) to filter known/intentional changes.")
@click.option("--strict-suppressions", is_flag=True, default=False,
              help="Fail with exit code 1 if any suppression rule has expired.")
@click.option("--require-justification", is_flag=True, default=False,
              help="Require every suppression rule to have a non-empty 'reason' field.")
@click.option("--policy", "policy",
              type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
              default="strict_abi", show_default=True,
              help="Built-in policy profile for verdict classification. Ignored when --policy-file is given.")
@click.option("--policy-file", "policy_file_path",
              type=click.Path(exists=True, path_type=Path), default=None,
              help="YAML policy file with per-kind verdict overrides. Overrides --policy.")
@click.option("--pdb-path", "pdb_path", type=click.Path(path_type=Path), default=None,
              help="Explicit PDB file path for Windows PE debug info (applied to both sides). "
                   "Overrides automatic PDB discovery.")
@click.option("--old-pdb-path", "old_pdb_path", type=click.Path(path_type=Path), default=None,
              help="PDB file path for old side only (overrides --pdb-path for old).")
@click.option("--new-pdb-path", "new_pdb_path", type=click.Path(path_type=Path), default=None,
              help="PDB file path for new side only (overrides --pdb-path for new).")
@click.option("--dwarf-only", is_flag=True, default=False,
              help="Force DWARF-only mode for both sides: use DWARF debug info "
                   "as primary data source even when headers are available.")
@click.option("--severity-preset", "severity_preset",
              type=click.Choice(["default", "strict", "info-only"], case_sensitive=True),
              default=None,
              help="Severity preset: 'default', 'strict', or 'info-only'. "
                   "Controls exit codes and report labels. Per-category "
                   "--severity-* options override the chosen preset.")
@click.option("--severity-abi-breaking", "severity_abi_breaking",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for clear ABI/API incompatibilities (overrides preset).")
@click.option("--severity-potential-breaking", "severity_potential_breaking",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for potential incompatibilities needing review (overrides preset).")
@click.option("--severity-quality-issues", "severity_quality_issues",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for problematic behaviors like std symbol leaks (overrides preset).")
@click.option("--severity-addition", "severity_addition",
              type=click.Choice(["error", "warning", "info"], case_sensitive=True),
              default=None,
              help="Severity for new public API additions (overrides preset).")
@click.option("--follow-deps", is_flag=True, default=False,
              help="Resolve transitive dependencies for both old and new, compute symbol "
                   "bindings, and include a dependency-change section in the report. ELF only.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries (with --follow-deps).")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (with --follow-deps).")
@click.option("--show-redundant", is_flag=True, default=False,
              help="Disable redundancy filtering and show all changes including those "
                   "derived from root type changes.")
@click.option("--show-only", "show_only", default=None,
              callback=_validate_show_only, expose_value=True, is_eager=False,
              help="Comma-separated filter tokens to limit displayed changes. "
                   "Severity: breaking, api-break, risk, compatible. "
                   "Element: functions, variables, types, enums, elf. "
                   "Action: added, removed, changed. "
                   "AND across dimensions, OR within. Does not affect exit codes.")
@click.option("--stat", is_flag=True, default=False,
              help="One-line summary output for CI gates. "
                   "With --format json, emits only the summary object.")
@click.option("--report-mode", "report_mode",
              type=click.Choice(["full", "leaf"], case_sensitive=True),
              default="full", show_default=True,
              help="Report mode: 'full' lists all changes individually (default), "
                   "'leaf' groups by root type changes with impact lists.")
@click.option("--show-impact", is_flag=True, default=False,
              help="Append an impact summary table showing root changes and affected interfaces.")
@click.option("--btf", "debug_format", flag_value="btf", default=None,
              help="Force BTF debug format for both sides (ELF only).")
@click.option("--ctf", "debug_format", flag_value="ctf",
              help="Force CTF debug format for both sides (ELF only).")
@click.option("--dwarf", "debug_format", flag_value="dwarf",
              help="Force DWARF debug format for both sides (ELF only).")
@click.option("--annotate", is_flag=True, default=False,
              help="Emit GitHub Actions workflow command annotations to stderr. "
                   "Annotations appear as inline comments on PR diffs. "
                   "Only effective when GITHUB_ACTIONS=true.")
@click.option("--annotate-additions", is_flag=True, default=False,
              help="Include additions/compatible changes as ::notice annotations "
                   "(requires --annotate).")
# ── Debug artifact resolution (ADR-021) ──────────────────────────────────────
@click.option("--debug-root", "debug_roots", multiple=True, type=click.Path(path_type=Path),
              help="Directory containing separate debug files (build-id trees, "
                   "path-mirror, dSYM bundles). Applied to both sides. Can be repeated.")
@click.option("--debug-root1", "debug_roots_old", multiple=True, type=click.Path(path_type=Path),
              help="Debug root for old side only (overrides --debug-root for old).")
@click.option("--debug-root2", "debug_roots_new", multiple=True, type=click.Path(path_type=Path),
              help="Debug root for new side only (overrides --debug-root for new).")
@click.option("--debuginfod", is_flag=True, default=False,
              help="Enable debuginfod network resolution for debug info (opt-in).")
@click.option("--debuginfod-url", "debuginfod_url", default=None,
              help="debuginfod server URL (overrides DEBUGINFOD_URLS env var).")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="Enable verbose/debug output.")
def compare_cmd(
    old_input: Path, new_input: Path,
    headers: tuple[Path, ...], includes: tuple[Path, ...], lang: str,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
    old_version: str, new_version: str,
    fmt: str, output: Path | None,
    suppress: Path | None, strict_suppressions: bool, require_justification: bool,
    policy: str, policy_file_path: Path | None,
    pdb_path: Path | None, old_pdb_path: Path | None, new_pdb_path: Path | None,
    dwarf_only: bool,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
    follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
    show_redundant: bool, show_only: str | None, stat: bool,
    report_mode: str, show_impact: bool,
    debug_format: str | None,
    annotate: bool,
    annotate_additions: bool,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
    verbose: bool,
) -> None:
    """Compare two ABI surfaces and report changes.

    Each input (OLD, NEW) can be a .so shared library, a JSON snapshot from
    'abicheck dump', or an ABICC Perl dump file. The format is auto-detected.

    When a .so file is given, headers (-H) are recommended for full ABI
    extraction. If headers are absent for ELF, abicheck falls back to
    DWARF-only mode (if DWARF available) or symbols-only analysis.

    \b
    Exit codes (legacy, without --severity-* flags):
      0  NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK — no binary ABI break
         (COMPATIBLE_WITH_RISK: deployment risk present; check the report)
      2  API_BREAK — source-level API break — recompilation required
      4  BREAKING — binary ABI break detected
    \b
    Exit codes (severity-aware, with any --severity-* flag):
      0  No error-level findings
      1  Error-level findings in addition or quality_issues only
      2  Error-level findings in potential_breaking (but not abi_breaking)
      4  Error-level findings in abi_breaking

    \b
    Examples:
    \b
      # One-liner: each version has its own header (primary flow)
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --old-header include/v1/foo.h --new-header include/v2/foo.h
    \b
      # Shorthand: -H when the same header applies to both versions
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h
    \b
      # With version labels and SARIF output
      abicheck compare libfoo.so.1 libfoo.so.2 \\
        --old-header v1/foo.h --new-header v2/foo.h \\
        --old-version 1.0 --new-version 2.0 --format sarif -o abi.sarif
    \b
      # Compare saved snapshot vs current build (mixed mode)
      abicheck compare baseline.json ./build/libfoo.so --new-header include/foo.h
    \b
      # Compare two pre-dumped snapshots (existing workflow)
      abicheck compare libfoo-1.0.json libfoo-2.0.json
    \b
      # Policy and suppression
      abicheck compare libfoo.so.1 libfoo.so.2 -H include/foo.h --policy sdk_vendor
      abicheck compare old.json new.json --suppress suppressions.yaml
    """
    _setup_verbosity(verbose)

    if annotate_additions and not annotate:
        raise click.UsageError("--annotate-additions requires --annotate")

    sev_config, severity_explicitly_set = _resolve_severity(
        severity_preset, severity_abi_breaking,
        severity_potential_breaking, severity_quality_issues, severity_addition,
    )

    old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
        headers, includes, old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    old_fmt = _detect_binary_format(old_input)
    new_fmt = _detect_binary_format(new_input)
    _warn_ignored_flags(
        old_fmt is not None, new_fmt is not None,
        headers, includes,
        old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    resolved_old_pdb = old_pdb_path if old_pdb_path else pdb_path
    resolved_new_pdb = new_pdb_path if new_pdb_path else pdb_path

    # Resolve per-side debug roots: --debug-root1 overrides --debug-root for old, etc.
    resolved_old_debug = list(debug_roots_old) if debug_roots_old else list(debug_roots)
    resolved_new_debug = list(debug_roots_new) if debug_roots_new else list(debug_roots)

    # Log debug resolution if roots are specified
    if resolved_old_debug or resolved_new_debug or debuginfod:
        from .debug_resolver import resolve_debug_info
        for label, binary, droots in [
            ("old", old_input, resolved_old_debug),
            ("new", new_input, resolved_new_debug),
        ]:
            if _detect_binary_format(binary) is not None and (droots or debuginfod):
                artifact = resolve_debug_info(
                    binary,
                    debug_roots=droots or None,
                    enable_debuginfod=debuginfod,
                    debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
                )
                if artifact:
                    click.echo(
                        f"Debug info ({label}): {artifact.source}",
                        err=True,
                    )

    old = _resolve_input(
        old_input, old_h, old_inc, old_version, lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=resolved_old_pdb,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )
    new = _resolve_input(
        new_input, new_h, new_inc, new_version, lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=resolved_new_pdb,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )

    suppression, pf = _load_suppression_and_policy(
        suppress, policy, policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )

    if follow_deps:
        if old_fmt == "elf":
            _populate_dependency_info(old, old_input, list(search_paths), None, ld_library_path)
        if new_fmt == "elf":
            _populate_dependency_info(new, new_input, list(search_paths), None, ld_library_path)

    result = compare(old, new, suppression=suppression, policy=policy, policy_file=pf)

    result.old_metadata = _collect_metadata(old_input)
    result.new_metadata = _collect_metadata(new_input)

    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)

    _warn_all_suppressed(result)

    _maybe_emit_annotations(result, annotate=annotate, annotate_additions=annotate_additions)

    text = _render_output(
        fmt, result, old, new,
        follow_deps=follow_deps,
        show_only=show_only, report_mode=report_mode,
        show_impact=show_impact, stat=stat,
        severity_config=sev_config if severity_explicitly_set else None,
    )

    _write_or_echo(output, text)

    _exit_with_severity_or_verdict(result, sev_config, severity_explicitly_set)


def _validate_appcompat_args(
    weak_mode: bool,
    old_lib: Path | None, new_lib: Path | None,
    list_symbols: bool,
    old_headers_only: tuple[Path, ...], new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...], new_includes_only: tuple[Path, ...],
) -> None:
    """Validate appcompat CLI argument combinations."""
    if weak_mode and (old_lib is not None or new_lib is not None):
        raise click.UsageError(
            "--check-against cannot be used with positional OLD_LIB/NEW_LIB arguments."
        )
    if not weak_mode and (old_lib is None or new_lib is None):
        raise click.UsageError(
            "Provide OLD_LIB and NEW_LIB arguments, or use --check-against for weak mode."
        )
    if weak_mode or list_symbols:
        _rejected: list[str] = []
        if old_headers_only:
            _rejected.append("--old-header")
        if new_headers_only:
            _rejected.append("--new-header")
        if old_includes_only:
            _rejected.append("--old-include")
        if new_includes_only:
            _rejected.append("--new-include")
        if _rejected:
            mode_label = "--check-against" if weak_mode else "--list-required-symbols"
            raise click.UsageError(
                f"{', '.join(_rejected)} cannot be used with {mode_label}. "
                f"Per-side header/include flags are only supported in full "
                f"comparison mode (OLD_LIB NEW_LIB)."
            )


def _handle_list_required_symbols(
    app_path: Path,
    check_against_lib: Path | None,
    old_lib: Path | None, new_lib: Path | None,
    weak_mode: bool, fmt: str,
    _get_lib_soname: Callable[[Path], str], parse_app_requirements: Callable[..., AppRequirements],
) -> None:
    """Handle the --list-required-symbols flow."""
    target_lib = check_against_lib if weak_mode else (old_lib or new_lib)
    if target_lib is None:
        raise click.UsageError(
            "--list-required-symbols requires a library path "
            "(via positional args or --check-against)."
        )
    lib_name = _get_lib_soname(target_lib)
    reqs = parse_app_requirements(app_path, lib_name)
    if fmt == "json":
        import json as _json
        click.echo(_json.dumps({
            "application": str(app_path),
            "library": lib_name,
            "needed_libs": reqs.needed_libs,
            "required_symbols": sorted(reqs.undefined_symbols),
            "required_versions": reqs.required_versions,
        }, indent=2))
    else:
        click.echo(f"Application: {app_path}")
        click.echo(f"Library filter: {lib_name}")
        click.echo(f"Needed libraries: {', '.join(reqs.needed_libs) or '(none)'}")
        click.echo(f"Required symbols ({len(reqs.undefined_symbols)}):")
        for sym in sorted(reqs.undefined_symbols):
            click.echo(f"  {sym}")
        if reqs.required_versions:
            click.echo(f"Required versions ({len(reqs.required_versions)}):")
            for ver, lib in sorted(reqs.required_versions.items()):
                click.echo(f"  {ver} (from {lib})")


@main.command("appcompat")
@click.argument("app_path", type=click.Path(exists=True, path_type=Path))
@click.argument("old_lib", type=click.Path(exists=True, path_type=Path), required=False, default=None)
@click.argument("new_lib", type=click.Path(exists=True, path_type=Path), required=False, default=None)
# ── Weak mode ─────────────────────────────────────────────────────────────────
@click.option("--check-against", "check_against_lib",
              type=click.Path(exists=True, path_type=Path), default=None,
              help="Weak mode: check if a library provides everything the app needs "
                   "(no old library required).")
# ── Dump options ──────────────────────────────────────────────────────────────
@click.option("-H", "--header", "headers", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header file or directory for library ABI extraction "
                   "(applied to both sides).")
@click.option("-I", "--include", "includes", multiple=True,
              type=click.Path(path_type=Path),
              help="Extra include directory for castxml (applied to both sides).")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for castxml.")
@click.option("--old-header", "old_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for old library only (overrides -H for old).")
@click.option("--new-header", "new_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for new library only (overrides -H for new).")
@click.option("--old-include", "old_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for old library only (overrides -I for old).")
@click.option("--new-include", "new_includes_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Include dir for new library only (overrides -I for new).")
@click.option("--old-version", "old_version", default="old", show_default=True)
@click.option("--new-version", "new_version", default="new", show_default=True)
# ── Output options ────────────────────────────────────────────────────────────
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("--show-irrelevant", is_flag=True, default=False,
              help="Include library changes that don't affect the application.")
@click.option("--list-required-symbols", "list_symbols", is_flag=True, default=False,
              help="List symbols the application requires and exit.")
# ── Suppression + policy ─────────────────────────────────────────────────────
@click.option("--suppress", type=click.Path(exists=True, path_type=Path), default=None,
              help="Suppression file (YAML).")
@click.option("--policy", "policy",
              type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
              default="strict_abi", show_default=True)
@click.option("--policy-file", "policy_file_path",
              type=click.Path(exists=True, path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def appcompat_cmd(
    app_path: Path,
    old_lib: Path | None,
    new_lib: Path | None,
    check_against_lib: Path | None,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    lang: str,
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    fmt: str,
    output: Path | None,
    show_irrelevant: bool,
    list_symbols: bool,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    verbose: bool,
) -> None:
    """Check if an application is compatible with a library update.

    Answers: "Will my application still work with the new library version?"
    by intersecting the app's required symbols with the library diff.

    \b
    Full check (with old and new library):
      abicheck appcompat myapp libfoo.so.1 libfoo.so.2
      abicheck appcompat myapp libfoo.so.1 libfoo.so.2 -H include/foo.h

    \b
    Weak mode (only new library — symbol availability check):
      abicheck appcompat myapp --check-against libfoo.so.2

    \b
    List required symbols:
      abicheck appcompat myapp --list-required-symbols --check-against libfoo.so.2

    \b
    Exit codes:
      0  COMPATIBLE — application is safe with the new library
      2  API_BREAK — source-level break affecting app's symbols
      4  BREAKING — binary ABI break or missing symbols
    """
    _setup_verbosity(verbose)

    from .appcompat import _get_lib_soname, check_appcompat, parse_app_requirements
    from .appcompat import check_against as _check_against
    from .reporter import appcompat_to_json, appcompat_to_markdown

    weak_mode = check_against_lib is not None
    _validate_appcompat_args(
        weak_mode, old_lib, new_lib, list_symbols,
        old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    if list_symbols:
        _handle_list_required_symbols(
            app_path, check_against_lib, old_lib, new_lib,
            weak_mode, fmt,
            _get_lib_soname, parse_app_requirements,
        )
        return

    if weak_mode:
        assert check_against_lib is not None
        result = _check_against(app_path, check_against_lib)
    else:
        assert old_lib is not None and new_lib is not None
        suppression, pf = _load_suppression_and_policy(suppress, policy, policy_file_path)
        old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
            headers, includes,
            old_headers_only, new_headers_only,
            old_includes_only, new_includes_only,
        )
        resolved_old_h = _expand_header_inputs(old_h) if old_h else []
        resolved_new_h = _expand_header_inputs(new_h) if new_h else []
        result = check_appcompat(
            app_path, old_lib, new_lib,
            old_headers=resolved_old_h,
            new_headers=resolved_new_h,
            old_includes=old_inc,
            new_includes=new_inc,
            old_version=old_version,
            new_version=new_version,
            lang=lang,
            suppression=suppression,
            policy=policy,
            policy_file=pf,
        )

    if fmt == "json":
        text = appcompat_to_json(result)
    elif fmt == "html":
        from .appcompat_html import appcompat_to_html
        text = appcompat_to_html(result)
    else:
        text = appcompat_to_markdown(result, show_irrelevant=show_irrelevant)

    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if result.verdict == Verdict.BREAKING:
        sys.exit(4)
    elif result.verdict == Verdict.API_BREAK:
        sys.exit(2)


# ---------------------------------------------------------------------------
# compare-release helpers
# ---------------------------------------------------------------------------

_RELEASE_VERDICT_ORDER: dict[str, int] = {
    "NO_CHANGE": 0, "COMPATIBLE": 1, "COMPATIBLE_WITH_RISK": 2,
    "API_BREAK": 3, "BREAKING": 4, "ERROR": 5,
}


_CompareReleaseCommonArgs = tuple[
    dict[str, Path], dict[str, Path],
    Path | None, Path | None,
    Callable[[Path, Path], Path | None],
    list[Path], list[Path],
    list[Path], list[Path],
    str, str,
    str, Path | None,
    str, Path | None,
    Path | None,
]


def _discover_files(
    input_dir: Path, lib_dir: Path,
    include_private: bool,
    discover_shared_libraries: Callable[..., list[Path]], is_package: Callable[[Path], bool],
) -> list[Path]:
    """Discover library files from a directory or extracted package."""
    if is_package(input_dir):
        files = discover_shared_libraries(lib_dir, include_private=include_private)
        if not files:
            files = _collect_release_inputs(lib_dir)
    else:
        files = _collect_release_inputs(lib_dir)
    return files


def _resolve_release_headers(
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_header_dir: Path | None,
    new_header_dir: Path | None,
) -> tuple[list[Path], list[Path]]:
    """Resolve per-side headers for compare-release."""
    old_h: list[Path] = list(old_headers_only) if old_headers_only else list(headers)
    new_h: list[Path] = list(new_headers_only) if new_headers_only else list(headers)
    if old_header_dir and not old_headers_only:
        old_h = [old_header_dir]
    if new_header_dir and not new_headers_only:
        new_h = [new_header_dir]
    return old_h, new_h


def _match_release_keys(
    old_dir: Path, new_dir: Path,
    old_map: dict[str, Path], new_map: dict[str, Path],
    old_files: list[Path], new_files: list[Path],
    is_package: Callable[[Path], bool],
) -> tuple[list[str], list[str], list[str], dict[str, Path], dict[str, Path]]:
    """Match library keys between old and new, handling direct file pairs."""
    direct_file_pair = (
        old_dir.is_file() and new_dir.is_file()
        and not is_package(old_dir) and not is_package(new_dir)
    )
    if direct_file_pair:
        matched_keys = ["__direct_pair__"]
        old_map = {"__direct_pair__": old_files[0]}
        new_map = {"__direct_pair__": new_files[0]}
        return matched_keys, [], [], old_map, new_map

    matched_keys = sorted(set(old_map) & set(new_map))
    removed_keys = sorted(set(old_map) - set(new_map))
    added_keys = sorted(set(new_map) - set(old_map))
    return matched_keys, removed_keys, added_keys, old_map, new_map


def _collect_release_warnings(
    warning_msgs: list[str],
    matched_keys: list[str], removed_keys: list[str], added_keys: list[str],
    old_map: dict[str, Path], new_map: dict[str, Path],
) -> None:
    """Collect warning messages for unmatched libraries."""
    for k in removed_keys:
        warning_msgs.append(f"Warning: library removed: {old_map[k].name}")
    for k in added_keys:
        warning_msgs.append(f"Info: library added: {new_map[k].name}")
    if not matched_keys:
        warning_msgs.append(
            "Warning: no matching library pairs found between OLD and NEW inputs."
        )


def _compare_one_library(
    key: str,
    old_map: dict[str, Path], new_map: dict[str, Path],
    old_debug_dir: Path | None, new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path], new_h: list[Path],
    old_inc: list[Path], new_inc: list[Path],
    old_version: str, new_version: str,
    lang: str, suppress: Path | None,
    policy: str, policy_file_path: Path | None,
    output_dir: Path | None,
) -> dict[str, object]:
    """Compare one library pair — suitable for parallel dispatch.

    The entire per-library flow (debug info resolution, comparison, output
    writing) is wrapped so that *any* exception yields an ERROR entry
    instead of aborting the whole release comparison.
    """
    old_path = old_map[key]
    new_path = new_map[key]
    try:
        old_dbg = resolve_debug_info(old_path, old_debug_dir) if old_debug_dir else None
        new_dbg = resolve_debug_info(new_path, new_debug_dir) if new_debug_dir else None
        result, _, _ = _run_compare_pair(
            old_path, new_path,
            old_h, new_h, old_inc, new_inc,
            old_version, new_version,
            lang, suppress, policy, policy_file_path,
            old_pdb_path=old_dbg, new_pdb_path=new_dbg,
        )
        v = result.verdict.value
        entry: dict[str, object] = {
            "library": old_path.name, "verdict": v,
            "breaking": len(result.breaking),
            "source_breaks": len(result.source_breaks),
            "risk_changes": len(result.risk),
            "compatible_additions": len(result.compatible),
        }
        if output_dir:
            lib_report_path = output_dir / f"{old_path.stem}.json"
            _safe_write_output(lib_report_path, to_json(result))
        return entry
    except (click.ClickException, click.UsageError) as exc:
        return {"library": old_path.name, "verdict": "ERROR", "error": exc.format_message()}
    except Exception as exc:
        return {"library": old_path.name, "verdict": "ERROR", "error": str(exc)}


def _compare_release_libraries(
    matched_keys: list[str],
    old_map: dict[str, Path], new_map: dict[str, Path],
    old_debug_dir: Path | None, new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path], new_h: list[Path],
    old_inc: list[Path], new_inc: list[Path],
    old_version: str, new_version: str,
    lang: str, suppress: Path | None,
    policy: str, policy_file_path: Path | None,
    output_dir: Path | None,
    collect_diff_results: bool = False,
    *,
    annotate: bool = False,
    annotate_additions: bool = False,
    jobs: int = 1,
) -> tuple[list[dict[str, object]], str, list[tuple[DiffResult, AbiSnapshot]]]:
    """Compare each matched library pair and collect results.

    When *collect_diff_results* is True, ``(DiffResult, old_snapshot)``
    pairs are collected and returned as the third element of the tuple
    (used by the JUnit output format).

    When *jobs* > 1, comparisons are dispatched in parallel via
    :func:`_compare_one_library` using a :class:`ProcessPoolExecutor`.
    """
    import os as _os

    effective_jobs = jobs if jobs > 0 else (_os.cpu_count() or 1)
    library_results: list[dict[str, object]] = []
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] = []
    worst_verdict = "NO_CHANGE"
    all_annotations: list[tuple[int, str]] = []

    common_args = (
        old_map, new_map, old_debug_dir, new_debug_dir, resolve_debug_info,
        old_h, new_h, old_inc, new_inc,
        old_version, new_version,
        lang, suppress, policy, policy_file_path, output_dir,
    )

    if effective_jobs > 1 and len(matched_keys) > 1:
        library_results.extend(
            _compare_release_parallel(matched_keys, common_args, old_map, effective_jobs),
        )
    else:
        library_results.extend(
            _compare_release_sequential(matched_keys, common_args),
        )

    # Post-process all results: compute worst verdict, collect annotations,
    # and optionally collect diff_pairs (for JUnit).
    for entry in library_results:
        v = str(entry["verdict"])
        if v == "ERROR":
            if "error" in entry:
                click.echo(f"Error comparing {entry['library']}: {entry['error']}", err=True)
        if _RELEASE_VERDICT_ORDER.get(v, 0) > _RELEASE_VERDICT_ORDER.get(worst_verdict, 0):
            worst_verdict = v

    # collect_diff_results and annotate require re-running comparison for
    # affected libraries (only used for JUnit / GitHub annotations which
    # are sequential-only features)
    if collect_diff_results or annotate:
        extra_pairs, extra_annotations = _collect_release_extras(
            matched_keys, old_map, new_map,
            old_debug_dir, new_debug_dir, resolve_debug_info,
            old_h, new_h, old_inc, new_inc,
            old_version, new_version, lang,
            suppress, policy, policy_file_path,
            annotate_additions=annotate_additions,
            collect_diff_results=collect_diff_results,
            annotate=annotate,
        )
        diff_pairs.extend(extra_pairs)
        all_annotations.extend(extra_annotations)

    # Emit annotations once: sort globally across all libraries by severity,
    # then truncate to the cap.  This ensures the most important annotations
    # (errors) are always visible regardless of which library they came from.
    if all_annotations:
        from .annotations import format_annotations

        text = format_annotations(all_annotations)
        if text:
            click.echo(text, err=True)

    return library_results, worst_verdict, diff_pairs


def _compare_release_parallel(
    matched_keys: list[str],
    common_args: _CompareReleaseCommonArgs,
    old_map: dict[str, Path],
    max_workers: int,
) -> list[dict[str, object]]:
    """Run per-library release comparisons in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_compare_one_library, key, *common_args): key
            for key in matched_keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                click.echo(f"Error comparing {old_map[key].name}: {exc}", err=True)
                results.append(
                    {"library": old_map[key].name, "verdict": "ERROR", "error": str(exc)},
                )
    return results


def _compare_release_sequential(
    matched_keys: list[str],
    common_args: _CompareReleaseCommonArgs,
) -> list[dict[str, object]]:
    """Run per-library release comparisons sequentially."""
    return [_compare_one_library(key, *common_args) for key in matched_keys]


def _collect_release_extras(
    matched_keys: list[str],
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    old_debug_dir: Path | None,
    new_debug_dir: Path | None,
    resolve_debug_info: Callable[[Path, Path], Path | None],
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    suppress: Path | None,
    policy: str,
    policy_file_path: Path | None,
    *,
    annotate_additions: bool,
    collect_diff_results: bool,
    annotate: bool,
) -> tuple[list[tuple[DiffResult, AbiSnapshot]], list[tuple[int, str]]]:
    """Collect optional re-run artifacts for JUnit and annotations."""
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] = []
    annotations: list[tuple[int, str]] = []
    for key in matched_keys:
        old_path = old_map[key]
        new_path = new_map[key]
        old_dbg = resolve_debug_info(old_path, old_debug_dir) if old_debug_dir else None
        new_dbg = resolve_debug_info(new_path, new_debug_dir) if new_debug_dir else None
        try:
            result, old_snap, _ = _run_compare_pair(
                old_path, new_path,
                old_h, new_h, old_inc, new_inc,
                old_version, new_version,
                lang, suppress, policy, policy_file_path,
                old_pdb_path=old_dbg, new_pdb_path=new_dbg,
            )
        except Exception as exc:
            click.echo(
                f"Warning: failed to re-run comparison for {old_path.name}: {exc}",
                err=True,
            )
            continue
        if collect_diff_results:
            diff_pairs.append((result, old_snap))
        if annotate:
            from .annotations import collect_annotations, is_github_actions

            if is_github_actions():
                annotations.extend(
                    collect_annotations(result, annotate_additions=annotate_additions),
                )
    return diff_pairs, annotations


def _run_bundle_analysis(
    old_map: dict[str, Path],
    new_map: dict[str, Path],
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]],
    *,
    manifest_path: Path | None,
    bundle_system_providers: str,
) -> BundleDiffResult | None:
    """Run bundle-level (ADR-023) analysis on a compare-release run.

    Returns None when there is nothing to analyze (e.g. all libraries
    failed to dump). Errors during analysis are caught and reported as a
    warning rather than aborting; bundle analysis is additive.
    """
    from .bundle import (
        BundleDiffResult,
        build_bundle_snapshot,
        compare_bundle,
        load_manifest,
    )

    if not old_map and not new_map:
        return None
    try:
        old_snap = build_bundle_snapshot(dict(old_map))
        new_snap = build_bundle_snapshot(dict(new_map))
    except Exception as exc:
        click.echo(f"Warning: bundle analysis skipped: {exc}", err=True)
        return None

    manifest = None
    if manifest_path is not None:
        try:
            manifest = load_manifest(manifest_path)
        except Exception as exc:
            click.echo(f"Warning: failed to load manifest {manifest_path}: {exc}", err=True)
            return None

    system_extra: list[str] = [
        s.strip() for s in bundle_system_providers.split(",") if s.strip()
    ]
    per_lib_results = [pair[0] for pair in diff_pairs]
    try:
        return compare_bundle(
            old_snap, new_snap, per_lib_results,
            manifest=manifest,
            system_providers=system_extra or None,
        )
    except Exception as exc:
        click.echo(f"Warning: bundle analysis raised: {exc}", err=True)
        return BundleDiffResult(old_root=old_snap.root, new_root=new_snap.root)


def _format_release_summary(
    fmt: str, worst_verdict: str,
    old_dir: Path, new_dir: Path,
    library_results: list[dict[str, object]],
    removed_keys: list[str], added_keys: list[str],
    old_map: dict[str, Path], new_map: dict[str, Path],
    warning_msgs: list[str],
    diff_pairs: list[tuple[DiffResult, AbiSnapshot]] | None = None,
    bundle_result: BundleDiffResult | None = None,
) -> str:
    """Format the release comparison summary as JSON, markdown, or JUnit XML."""
    if fmt == "junit":
        from .junit_report import to_junit_xml_multi
        pairs: list[tuple[DiffResult, AbiSnapshot | None]] = list(diff_pairs or [])
        error_libs = [
            entry for entry in library_results
            if entry.get("verdict") == "ERROR"
        ]
        return to_junit_xml_multi(
            pairs, error_libraries=error_libs if error_libs else None,
        )

    if fmt == "json":
        summary: dict[str, object] = {
            "verdict": worst_verdict,
            "old_dir": str(old_dir),
            "new_dir": str(new_dir),
            "libraries": library_results,
            "unmatched_old": [old_map[k].name for k in removed_keys],
            "unmatched_new": [new_map[k].name for k in added_keys],
            "warnings": warning_msgs,
        }
        if bundle_result is not None:
            summary["bundle_verdict"] = bundle_result.bundle_verdict.value
            summary["bundle_findings"] = [
                {
                    "kind": f.kind.value,
                    "symbol": f.symbol,
                    "consumer_library": f.consumer_library,
                    "provider_library": f.provider_library,
                    "description": f.description,
                    "old_value": f.old_value,
                    "new_value": f.new_value,
                    "affected_libraries": list(f.affected_libraries),
                }
                for f in bundle_result.bundle_findings
            ]
        return json.dumps(summary, indent=2)

    _VERDICT_EMOJI = {
        "NO_CHANGE": "✅", "COMPATIBLE": "✅", "COMPATIBLE_WITH_RISK": "⚠️",
        "API_BREAK": "⚠️", "BREAKING": "❌", "ERROR": "💥",
    }
    lines: list[str] = [
        "# ABI Release Comparison", "",
        "| | |", "|---|---|",
        f"| **Old** | `{old_dir}` |",
        f"| **New** | `{new_dir}` |",
        f"| **Verdict** | {_VERDICT_EMOJI.get(worst_verdict, '?')} `{worst_verdict}` |",
    ]
    bundle_count = len(bundle_result.bundle_findings) if bundle_result else 0
    if bundle_result is not None:
        bundle_em = _VERDICT_EMOJI.get(bundle_result.bundle_verdict.value, "?")
        lines.append(
            f"| **Bundle** | {bundle_em} `{bundle_result.bundle_verdict.value}` "
            f"({bundle_count} cross-library finding{'s' if bundle_count != 1 else ''}) |",
        )
    lines += [
        "", "## Libraries", "",
        "| Library | Verdict | Breaking | Source | Risk | Additions |",
        "|---|---|---|---|---|---|",
    ]
    for lib in library_results:
        em = _VERDICT_EMOJI.get(str(lib["verdict"]), "?")
        lines.append(
            f"| `{lib['library']}` | {em} `{lib['verdict']}` "
            f"| {lib.get('breaking', '—')} | {lib.get('source_breaks', '—')} "
            f"| {lib.get('risk_changes', '—')} | {lib.get('compatible_additions', '—')} |"
        )
    if removed_keys:
        lines += ["", "## ⚠️ Removed Libraries", ""]
        for k in removed_keys:
            lines.append(f"- `{old_map[k].name}`")
    if added_keys:
        lines += ["", "## ℹ️ Added Libraries", ""]
        for k in added_keys:
            lines.append(f"- `{new_map[k].name}`")
    if bundle_result is not None and bundle_result.bundle_findings:
        lines += ["", "## 🔗 Bundle (Cross-Library) Findings", ""]
        for f in bundle_result.bundle_findings:
            lines.append(
                f"- **{f.kind.value}** — `{f.symbol}`"
                + (f" (consumer: `{f.consumer_library}`)" if f.consumer_library else "")
                + (f" (provider: `{f.provider_library}`)" if f.provider_library else ""),
            )
            lines.append(f"  - {f.description}")
    return "\n".join(lines)


def _write_release_summary_file(
    output_dir: Path, worst_verdict: str,
    library_results: list[dict[str, object]],
    removed_keys: list[str], added_keys: list[str],
    old_map: dict[str, Path], new_map: dict[str, Path],
) -> None:
    """Write per-library summary JSON to output directory."""
    summary_data: dict[str, object] = {
        "verdict": worst_verdict,
        "libraries": library_results,
        "unmatched_old": [old_map[k].name for k in removed_keys],
        "unmatched_new": [new_map[k].name for k in added_keys],
    }
    summary_path = output_dir / "summary.json"
    _safe_write_output(summary_path, json.dumps(summary_data, indent=2))
    click.echo(f"Per-library reports written to {output_dir}/", err=True)


@main.command("compare-release")
@click.argument("old_dir", type=click.Path(exists=True, path_type=Path))
@click.argument("new_dir", type=click.Path(exists=True, path_type=Path))
@click.option("-H", "--header", "headers", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header file or directory applied to both sides.")
@click.option("-I", "--include", "includes", multiple=True,
              type=click.Path(path_type=Path),
              help="Extra include directory for castxml.")
@click.option("--old-header", "old_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Header for old side only (overrides -H for old).")
@click.option("--new-header", "new_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Header for new side only (overrides -H for new).")
@click.option("--old-version", "old_version", default="old", show_default=True,
              help="Version label for old side.")
@click.option("--new-version", "new_version", default="new", show_default=True,
              help="Version label for new side.")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False))
@click.option("--format", "fmt",
              type=click.Choice(["json", "markdown", "junit"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Output file for summary report (default: stdout).")
@click.option("--output-dir", "output_dir", type=click.Path(path_type=Path), default=None,
              help="Directory to write per-library reports.")
@click.option("--suppress", type=click.Path(exists=True, path_type=Path), default=None,
              help="Suppression file (YAML).")
@click.option("--strict-suppressions", is_flag=True, default=False,
              help="Fail with exit code 1 if any suppression rule has expired.")
@click.option("--require-justification", is_flag=True, default=False,
              help="Require every suppression rule to have a non-empty 'reason' field.")
@click.option("--policy", "policy",
              type=click.Choice(["strict_abi", "sdk_vendor", "plugin_abi"], case_sensitive=True),
              default="strict_abi", show_default=True)
@click.option("--policy-file", "policy_file_path",
              type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--fail-on-removed-library/--no-fail-on-removed-library",
              "fail_on_removed", default=False,
              help="Exit 8 when a library present in old_dir is absent in new_dir.")
@click.option("--debug-info1", type=click.Path(exists=True, path_type=Path), default=None,
              help="Debug info package for old side (RPM/Deb/tar).")
@click.option("--debug-info2", type=click.Path(exists=True, path_type=Path), default=None,
              help="Debug info package for new side (RPM/Deb/tar).")
@click.option("--devel-pkg1", type=click.Path(exists=True, path_type=Path), default=None,
              help="Development package with headers for old side.")
@click.option("--devel-pkg2", type=click.Path(exists=True, path_type=Path), default=None,
              help="Development package with headers for new side.")
@click.option("--dso-only", is_flag=True, default=False,
              help="Only compare shared objects, skip executables.")
@click.option("--include-private-dso", is_flag=True, default=False,
              help="Include private (non-public) shared objects from non-standard paths.")
@click.option("--keep-extracted", is_flag=True, default=False,
              help="Keep extracted temporary files for debugging.")
@click.option("--annotate", is_flag=True, default=False,
              help="Emit GitHub Actions workflow command annotations to stdout. "
                   "Only effective when GITHUB_ACTIONS=true.")
@click.option("--annotate-additions", is_flag=True, default=False,
              help="Include additions/compatible changes as ::notice annotations "
                   "(requires --annotate).")
@click.option("-v", "--verbose", is_flag=True, default=False)
@click.option("-j", "--jobs", "jobs", type=int, default=1, show_default=True,
              help="Number of parallel library comparisons. "
                   "Use 0 for auto-detect (CPU count).")
@click.option("--manifest", "manifest_path", type=click.Path(exists=True, path_type=Path),
              default=None,
              help="ABI instantiation manifest (YAML/JSON) listing symbols the "
                   "release publicly promises. See ADR-023.")
@click.option("--bundle-system-providers", "bundle_system_providers", default="",
              help="Comma-separated extra sonames to treat as system-provided "
                   "(extends the built-in libc/libstdc++/libgcc/libtbb allow-list).")
@click.option("--no-bundle-analysis", "no_bundle_analysis", is_flag=True, default=False,
              help="Skip bundle-level cross-library analysis (debug/parity escape hatch). "
                   "Bundle findings catch intra-bundle symbol removals, signature drift "
                   "across DSO boundaries, type drift across siblings, provider "
                   "migration, and manifest mismatches.")
def compare_release_cmd(
    old_dir: Path,
    new_dir: Path,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_version: str,
    new_version: str,
    lang: str,
    fmt: str,
    output: Path | None,
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
    annotate: bool,
    annotate_additions: bool,
    verbose: bool,
    jobs: int,
    manifest_path: Path | None,
    bundle_system_providers: str,
    no_bundle_analysis: bool,
) -> None:
    """Compare all libraries in two release directories or packages.

    OLD_DIR and NEW_DIR may each be a file, directory, or package
    (RPM, Deb, tar, conda, wheel). Package format is auto-detected.
    When directories are given, libraries are matched by filename stem.

    \b
    Exit codes:
      0  All libraries: NO_CHANGE, COMPATIBLE, or COMPATIBLE_WITH_RISK
      2  At least one library: API_BREAK
      4  At least one library: BREAKING
      8  Library removed (only when --fail-on-removed-library)

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
    import tempfile

    from .package import (
        _is_elf_shared_object,
        detect_extractor,
        discover_shared_libraries,
        is_package,
        resolve_debug_info,
    )

    _setup_verbosity(verbose)

    if annotate_additions and not annotate:
        raise click.UsageError("--annotate-additions requires --annotate")

    # Track temporary directory paths for cleanup
    _temp_dir_paths: list[str] = []

    def _make_temp_dir(prefix: str) -> Path:
        """Create a temporary directory, tracking it for later cleanup."""
        path = tempfile.mkdtemp(prefix=prefix)
        _temp_dir_paths.append(path)
        return Path(path)

    def _extract_if_package(
        input_path: Path,
        debug_pkg: Path | None,
        devel_pkg: Path | None,
    ) -> tuple[Path, Path | None, Path | None]:
        """Extract package to tempdir if needed, return (lib_dir, debug_dir, header_dir)."""
        if not is_package(input_path):
            return input_path, None, None

        extractor = detect_extractor(input_path)
        if extractor is None:
            raise click.ClickException(f"Unrecognized package format: {input_path}")

        target = _make_temp_dir("abicheck_pkg_")

        result = extractor.extract(input_path, target)
        lib_dir = result.lib_dir
        debug_dir = result.debug_dir
        header_dir = result.header_dir

        # Extract debug info package if provided
        if debug_pkg is not None:
            dbg_ext = detect_extractor(debug_pkg)
            if dbg_ext is None:
                raise click.ClickException(f"Unrecognized debug package format: {debug_pkg}")
            dbg_target = _make_temp_dir("abicheck_dbg_")
            dbg_result = dbg_ext.extract(debug_pkg, dbg_target)
            debug_dir = dbg_result.lib_dir

        # Extract devel package if provided
        if devel_pkg is not None:
            dev_ext = detect_extractor(devel_pkg)
            if dev_ext is None:
                raise click.ClickException(f"Unrecognized devel package format: {devel_pkg}")
            dev_target = _make_temp_dir("abicheck_dev_")
            dev_result = dev_ext.extract(devel_pkg, dev_target)
            header_dir = dev_result.lib_dir

        return lib_dir, debug_dir, header_dir

    # Validate suppression file early (before per-library loop)
    if suppress is not None and (strict_suppressions or require_justification):
        _load_suppression_and_policy(
            suppress, policy, policy_file_path,
            strict_suppressions=strict_suppressions,
            require_justification=require_justification,
        )

    try:
        prep = _prepare_compare_release_inputs(
            old_dir, new_dir,
            debug_info1, debug_info2, devel_pkg1, devel_pkg2,
            include_private_dso, dso_only,
            headers, old_headers_only, new_headers_only, includes,
            _extract_if_package, discover_shared_libraries, is_package, _is_elf_shared_object,
        )
        (
            old_debug_dir, new_debug_dir,
            old_h, new_h, old_inc, new_inc,
            old_map, new_map, warning_msgs,
            matched_keys, removed_keys, added_keys,
        ) = prep

        if fmt != "json":
            for msg in warning_msgs:
                click.echo(msg, err=True)

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        bundle_enabled = not no_bundle_analysis
        library_results, worst_verdict, diff_pairs = _compare_release_libraries(
            matched_keys, old_map, new_map,
            old_debug_dir, new_debug_dir, resolve_debug_info,
            old_h, new_h, old_inc, new_inc,
            old_version, new_version,
            lang, suppress, policy, policy_file_path,
            output_dir,
            collect_diff_results=(fmt == "junit") or bundle_enabled,
            annotate=annotate,
            annotate_additions=annotate_additions,
            jobs=jobs,
        )

        bundle_result = None
        if bundle_enabled:
            bundle_result = _run_bundle_analysis(
                old_map, new_map, diff_pairs,
                manifest_path=manifest_path,
                bundle_system_providers=bundle_system_providers,
            )
            if bundle_result is not None:
                bv = bundle_result.bundle_verdict.value
                if _RELEASE_VERDICT_ORDER.get(bv, 0) > _RELEASE_VERDICT_ORDER.get(worst_verdict, 0):
                    worst_verdict = bv

        if removed_keys and _RELEASE_VERDICT_ORDER.get(worst_verdict, 0) < _RELEASE_VERDICT_ORDER.get("COMPATIBLE_WITH_RISK", 0):
            worst_verdict = "COMPATIBLE_WITH_RISK"

        text = _format_release_summary(
            fmt, worst_verdict, old_dir, new_dir,
            library_results, removed_keys, added_keys,
            old_map, new_map, warning_msgs,
            diff_pairs=diff_pairs if fmt == "junit" else None,
            bundle_result=bundle_result,
        )
        _write_or_echo(output, text)

        # Write a single step summary for the entire release comparison.
        if annotate:
            _write_release_step_summary(text, fmt)

        if output_dir:
            _write_release_summary_file(
                output_dir, worst_verdict, library_results,
                removed_keys, added_keys, old_map, new_map,
            )

        _exit_compare_release(worst_verdict, fail_on_removed, removed_keys)
    finally:
        import shutil as _shutil
        if not keep_extracted:
            for td_path in _temp_dir_paths:
                _shutil.rmtree(td_path, ignore_errors=True)
        elif _temp_dir_paths:
            kept_paths = ", ".join(_temp_dir_paths)
            click.echo(
                f"Extracted files kept in: {kept_paths}",
                err=True,
            )


def _prepare_compare_release_inputs(
    old_dir: Path,
    new_dir: Path,
    debug_info1: Path | None,
    debug_info2: Path | None,
    devel_pkg1: Path | None,
    devel_pkg2: Path | None,
    include_private_dso: bool,
    dso_only: bool,
    headers: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    includes: tuple[Path, ...],
    extract_if_package: Callable[[Path, Path | None, Path | None], tuple[Path, Path | None, Path | None]],
    discover_shared_libraries: Callable[..., list[Path]],
    is_package: Callable[[Path], bool],
    is_elf_shared_object: Callable[[Path], bool],
) -> tuple[
    Path | None, Path | None,
    list[Path], list[Path], list[Path], list[Path],
    dict[str, Path], dict[str, Path], list[str],
    list[str], list[str], list[str],
]:
    """Prepare inputs/maps/keys for compare-release command."""
    old_lib_dir, old_debug_dir, old_header_dir = extract_if_package(
        old_dir, debug_info1, devel_pkg1,
    )
    new_lib_dir, new_debug_dir, new_header_dir = extract_if_package(
        new_dir, debug_info2, devel_pkg2,
    )
    old_files = _discover_files(
        old_dir, old_lib_dir, include_private_dso, discover_shared_libraries, is_package,
    )
    new_files = _discover_files(
        new_dir, new_lib_dir, include_private_dso, discover_shared_libraries, is_package,
    )
    if dso_only:
        old_files = [f for f in old_files if is_elf_shared_object(f)]
        new_files = [f for f in new_files if is_elf_shared_object(f)]
    old_map, old_warns = _build_match_map(old_files)
    new_map, new_warns = _build_match_map(new_files)
    warning_msgs: list[str] = [f"Warning: {warning}" for warning in (old_warns + new_warns)]
    old_h, new_h = _resolve_release_headers(
        headers, old_headers_only, new_headers_only, old_header_dir, new_header_dir,
    )
    old_inc = list(includes)
    new_inc = list(includes)
    matched_keys, removed_keys, added_keys, old_map, new_map = _match_release_keys(
        old_dir, new_dir, old_map, new_map, old_files, new_files, is_package,
    )
    _collect_release_warnings(
        warning_msgs, matched_keys, removed_keys, added_keys, old_map, new_map,
    )
    return (
        old_debug_dir, new_debug_dir,
        old_h, new_h, old_inc, new_inc,
        old_map, new_map, warning_msgs,
        matched_keys, removed_keys, added_keys,
    )


def _exit_compare_release(
    worst_verdict: str,
    fail_on_removed: bool,
    removed_keys: list[str],
) -> None:
    """Exit compare-release with ABI-compatible status code mapping."""
    if worst_verdict in ("BREAKING", "ERROR"):
        sys.exit(4)
    if worst_verdict == "API_BREAK":
        sys.exit(2)
    if fail_on_removed and removed_keys:
        sys.exit(8)


# ── Suggest suppressions command ──────────────────────────────────────────────

@main.command("suggest-suppressions")
@click.argument("diff_json", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None,
              help="Output file for candidate suppressions (default: stdout).")
@click.option("--expiry-days", type=click.IntRange(min=0), default=180, show_default=True,
              help="Number of days from today for the expires field.")
def suggest_suppressions_cmd(
    diff_json: Path,
    output: Path | None,
    expiry_days: int,
) -> None:
    """Generate candidate suppression rules from a JSON diff result.

    DIFF_JSON is a JSON file produced by 'abicheck compare --format json'.

    \b
    Example:
      abicheck compare old.so new.so -H include/ --format json -o diff.json
      abicheck suggest-suppressions diff.json -o candidates.yml
    """
    import json

    from .suppression import suggest_suppressions

    try:
        text = diff_json.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        raise click.ClickException(f"Cannot read JSON diff: {e}") from e

    if not isinstance(data, dict):
        raise click.ClickException(
            "JSON diff must be an object with a 'changes' key"
        )
    if "changes" not in data:
        raise click.ClickException(
            "JSON diff is missing required 'changes' key"
        )
    changes = data["changes"]
    if not isinstance(changes, list):
        raise click.ClickException("'changes' must be an array")
    for i, entry in enumerate(changes):
        if not isinstance(entry, dict):
            raise click.ClickException(
                f"changes[{i}] must be an object, got {type(entry).__name__}"
            )

    yaml_text = suggest_suppressions(changes, expiry_days=expiry_days)
    _write_or_echo(output, yaml_text)


# ── Full-stack dependency commands ────────────────────────────────────────────

@main.command("deps")
@click.argument("binary", type=click.Path(exists=True, path_type=Path))
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries.")
@click.option("--sysroot", type=click.Path(exists=True, path_type=Path), default=None,
              help="Sysroot prefix for cross/container analysis.")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (colon-separated).")
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def deps_cmd(
    binary: Path, search_paths: tuple[Path, ...],
    sysroot: Path | None, ld_library_path: str,
    fmt: str, output: Path | None, verbose: bool,
) -> None:
    """Show the resolved dependency tree and symbol binding status.

    Resolves the transitive closure of DT_NEEDED dependencies for BINARY
    using loader-accurate search order (RPATH/RUNPATH, LD_LIBRARY_PATH,
    default dirs) and reports symbol binding status.

    \b
    Exit codes:
      0  All dependencies resolved, all required symbols bound
      1  Missing dependencies or symbols (load would fail)

    \b
    Examples:
      abicheck deps ./build/libfoo.so
      abicheck deps /usr/bin/myapp --format json -o deps.json
      abicheck deps ./app --sysroot /path/to/container/rootfs
    """
    _setup_verbosity(verbose)

    fmt_detected = _detect_binary_format(binary)
    if fmt_detected != "elf":
        raise click.ClickException(
            f"deps requires an ELF binary; got {fmt_detected or 'unknown format'}: {binary}"
        )

    from .stack_checker import check_single_env
    from .stack_report import stack_to_json, stack_to_markdown

    result = check_single_env(
        binary,
        search_paths=list(search_paths) or None,
        sysroot=sysroot,
        ld_library_path=ld_library_path,
    )

    if fmt == "json":
        text = stack_to_json(result)
    elif fmt == "html":
        from .stack_html import stack_to_html
        text = stack_to_html(result)
    else:
        text = stack_to_markdown(result)
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if result.loadability.value == "fail":
        sys.exit(1)


@main.command("stack-check")
@click.argument("binary", type=click.Path(path_type=Path))
@click.option("--baseline", type=click.Path(exists=True, path_type=Path),
              default=Path("/"), show_default=True,
              help="Sysroot for the baseline environment.")
@click.option("--candidate", type=click.Path(exists=True, path_type=Path),
              default=Path("/"), show_default=True,
              help="Sysroot for the candidate environment.")
@click.option("--search-path", "search_paths", multiple=True,
              type=click.Path(exists=True, path_type=Path),
              help="Additional directory to search for shared libraries.")
@click.option("--ld-library-path", "ld_library_path", default="",
              help="Simulated LD_LIBRARY_PATH (colon-separated).")
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "html"]),
              default="markdown", show_default=True)
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("-v", "--verbose", is_flag=True, default=False)
def stack_check_cmd(
    binary: Path, baseline: Path, candidate: Path,
    search_paths: tuple[Path, ...], ld_library_path: str,
    fmt: str, output: Path | None, verbose: bool,
) -> None:
    """Compare a binary's full dependency stack across two environments.

    Resolves all transitive dependencies in both BASELINE and CANDIDATE sysroots,
    computes symbol bindings, detects changed DSOs, runs per-library ABI diffs,
    and produces a stack-level compatibility verdict.

    BINARY is the path relative to the sysroot (e.g. usr/bin/myapp).

    \b
    Exit codes:
      0  PASS — binary loads and no harmful ABI changes
      1  WARN — loads but ABI risk detected
      4  FAIL — load failure or binary ABI break

    \b
    Examples:
      abicheck stack-check usr/bin/myapp --baseline /old-root --candidate /new-root
      abicheck stack-check usr/lib/libfoo.so.1 \\
        --baseline ./image-v1 --candidate ./image-v2 --format json
    """
    _setup_verbosity(verbose)

    # Guard against accidental no-op comparisons.
    if baseline == candidate:
        raise click.UsageError(
            "--baseline and --candidate resolve to the same sysroot; "
            "provide two different roots for stack comparison."
        )

    # Validate that every existing binary is ELF in both sysroots
    for label, root in [("baseline", baseline), ("candidate", candidate)]:
        resolved = root / binary
        if resolved.exists():
            fmt_detected = _detect_binary_format(resolved)
            if fmt_detected != "elf":
                raise click.ClickException(
                    f"stack-check requires an ELF binary; got "
                    f"{fmt_detected or 'unknown format'}: {resolved}"
                )

    from .stack_checker import check_stack
    from .stack_report import stack_to_json, stack_to_markdown

    result = check_stack(
        binary,
        baseline_root=baseline,
        candidate_root=candidate,
        ld_library_path=ld_library_path,
        search_paths=list(search_paths) or None,
    )

    if fmt == "json":
        text = stack_to_json(result)
    elif fmt == "html":
        from .stack_html import stack_to_html
        text = stack_to_html(result)
    else:
        text = stack_to_markdown(result)
    if output:
        _safe_write_output(output, text)
        click.echo(f"Report written to {output}", err=True)
    else:
        click.echo(text)

    if result.loadability.value == "fail" or result.abi_risk.value == "fail":
        sys.exit(4)
    elif result.abi_risk.value == "warn" or result.loadability.value == "warn":
        sys.exit(1)


# ── ABICC compat subcommands (implementation in abicheck.compat) ─────────────
# NOTE: eagerly loads abicheck.compat.cli at import time — intentional so all
# consumers get compat commands registered. Private helpers re-exported for
# backward compatibility with code importing from abicheck.cli directly.
from .compat.cli import (  # noqa: E402,F401
    _API_BREAK_KINDS,
    _BINARY_ONLY_KINDS,
    _NEW_SYMBOL_KINDS,
    _P2_STUB_FLAGS,
    _apply_strict,
    _apply_warn_newsym,
    _build_internal_suppression,
    _build_skip_suppression,
    _build_whitelist_suppression,
    _classify_compat_error_exit_code,
    _compat_fail,
    _detect_compiler_version,
    _do_echo,
    _filter_binary_only,
    _filter_source_only,
    _limit_affected_changes,
    _load_descriptor_or_dump,
    _load_skip_headers,
    _merge_suppression,
    _resolve_headers_from_list,
    _safe_path,
    _setup_logging,
    _warn_stub_flags,
    _write_affected_list,
)

# fmt: on

main.add_command(compat_group)


# ── debian-symbols command group ──────────────────────────────────────────────

@click.group("debian-symbols")
def debian_symbols_group() -> None:
    """Generate, validate, and diff Debian symbols files.

    Integrates abicheck with Debian/Ubuntu packaging workflows where
    dpkg-gensymbols and dpkg-shlibdeps use symbols files for fine-grained
    dependency tracking.
    """


@debian_symbols_group.command("generate")
@click.argument("so_path", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", "output_path", type=click.Path(path_type=Path), default=None,
              help="Output file path. Prints to stdout if not specified.")
@click.option("--package", default="", help="Debian package name (derived from SONAME if empty).")
@click.option("--version", "version", default="#MINVER#", show_default=True,
              help="Minimum version string for symbols.")
@click.option("--no-cpp", "no_cpp", is_flag=True, default=False,
              help="Do not emit C++ demangled (c++) form; use mangled names only.")
def debian_symbols_generate(
    so_path: Path,
    output_path: Path | None,
    package: str,
    version: str,
    no_cpp: bool,
) -> None:
    """Generate a Debian symbols file from a shared library.

    \b
    Example:
      abicheck debian-symbols generate libfoo.so -o debian/libfoo1.symbols
    """
    from .debian_symbols import generate_from_binary

    symbols_file = generate_from_binary(
        so_path,
        package=package,
        version=version,
        use_cpp=not no_cpp,
    )

    text = symbols_file.format()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
        click.echo(f"Symbols file written to {output_path}")
    else:
        click.echo(text, nl=False)


@debian_symbols_group.command("validate")
@click.argument("so_path", type=click.Path(exists=True, path_type=Path))
@click.argument("symbols_path", type=click.Path(exists=True, path_type=Path))
def debian_symbols_validate(so_path: Path, symbols_path: Path) -> None:
    """Validate a Debian symbols file against a shared library binary.

    \b
    Exit codes:
      0  symbols file matches the binary
      2  mismatch (missing symbols)

    \b
    Example:
      abicheck debian-symbols validate libfoo.so debian/libfoo1.symbols
    """
    from .debian_symbols import format_validation_report, validate_from_binary

    result = validate_from_binary(so_path, symbols_path)
    click.echo(format_validation_report(result), nl=False)

    if not result.passed:
        sys.exit(2)


@debian_symbols_group.command("diff")
@click.argument("old_symbols", type=click.Path(exists=True, path_type=Path))
@click.argument("new_symbols", type=click.Path(exists=True, path_type=Path))
def debian_symbols_diff(old_symbols: Path, new_symbols: Path) -> None:
    """Diff two Debian symbols files.

    \b
    Example:
      abicheck debian-symbols diff old/libfoo1.symbols new/libfoo1.symbols
    """
    from .debian_symbols import (
        diff_symbols_files,
        format_diff_report,
        load_symbols_file,
    )

    old = load_symbols_file(old_symbols)
    new = load_symbols_file(new_symbols)
    diff = diff_symbols_files(old, new)

    click.echo(format_diff_report(diff, str(old_symbols), str(new_symbols)), nl=False)


main.add_command(debian_symbols_group)


# ---------------------------------------------------------------------------
# Baseline registry commands (ADR-022)
# ---------------------------------------------------------------------------

@main.group("baseline")
def baseline_group() -> None:
    """Manage ABI baseline snapshots.

    Push, pull, list, and delete baseline snapshots from a registry.
    Default backend: filesystem (--registry file:///path/to/baselines).
    """


@baseline_group.command("push")
@click.argument("library", type=str)
@click.option("--version", "version", required=True,
              help="Version or branch name for the baseline.")
@click.option("--platform", "platform", required=False,
              help="Target platform (e.g. 'linux-x86_64'). Use --auto-platform to detect.")
@click.option("--variant", default="",
              help="Build variant (e.g. 'debug', 'ssl-enabled').")
@click.option("--snapshot", "snapshot_path", required=True,
              type=click.Path(exists=True, path_type=Path),
              help="Path to the ABI snapshot JSON file.")
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory. "
                   "Defaults to .abicheck/baselines in the current directory.")
@click.option("--auto-platform", is_flag=True, default=False,
              help="Auto-detect platform from the binary in the snapshot.")
@click.option("--git-commit", default=None,
              help="Source commit SHA to record in baseline metadata.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_push(
    library: str, version: str, platform: str, variant: str,
    snapshot_path: Path, registry_path: Path | None,
    auto_platform: bool, git_commit: str | None, verbose: bool,
) -> None:
    """Push an ABI baseline snapshot to the registry.

    \b
    Example:
      abicheck baseline push libfoo --version 1.0.0 --platform linux-x86_64 \\
        --snapshot build/abi-snapshot.json
    """
    _setup_verbosity(verbose)
    from .baseline import BaselineKey, BaselineMetadata, FilesystemRegistry
    from .serialization import load_snapshot as _load

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    from .serialization import snapshot_to_json

    snapshot = _load(snapshot_path)
    # Compute checksum from canonical serialization (same form registry.push stores)
    canonical_json = snapshot_to_json(snapshot)
    meta = BaselineMetadata.create(canonical_json, git_commit=git_commit)

    effective_platform: str | None = platform
    if auto_platform and not platform:
        # Detect platform from the library path embedded in the snapshot
        if snapshot.library:
            lib_path = Path(snapshot.library)
            if lib_path.exists():
                from .baseline import detect_platform_from_binary
                effective_platform = detect_platform_from_binary(lib_path)
                if effective_platform is None:
                    raise click.UsageError(
                        "--auto-platform: failed to detect binary architecture. "
                        "Use --platform to specify the platform explicitly."
                    )
                click.echo(f"Auto-detected platform: {effective_platform}", err=True)
            else:
                raise click.UsageError(
                    f"--auto-platform: binary '{snapshot.library}' not found on disk. "
                    "Use --platform to specify the platform explicitly."
                )
        else:
            raise click.UsageError(
                "--auto-platform: snapshot has no library path. "
                "Use --platform to specify the platform explicitly."
            )

    if effective_platform is None:
        raise click.UsageError(
            "Platform is required. Use --platform or --auto-platform with a detectable binary."
        )

    try:
        key = BaselineKey(library=library, version=version, platform=effective_platform, variant=variant)
    except (ValueError, AbicheckError) as exc:
        raise click.ClickException(str(exc)) from exc
    ref = registry.push(key, snapshot, meta)
    click.echo(f"Baseline pushed: {ref}", err=True)


@baseline_group.command("pull")
@click.argument("spec", type=str)
@click.option("-o", "--output", type=click.Path(path_type=Path), required=True,
              help="Output path for the snapshot JSON file.")
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_pull(spec: str, output: Path, registry_path: Path | None, verbose: bool) -> None:
    """Pull an ABI baseline snapshot from the registry.

    SPEC is a colon-separated key: library:version:platform[:variant]

    \b
    Example:
      abicheck baseline pull libfoo:1.0.0:linux-x86_64 -o baseline.json
    """
    _setup_verbosity(verbose)
    from .baseline import BaselineKey, FilesystemRegistry

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    try:
        key = BaselineKey.from_spec(spec)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    result = registry.pull(key)
    if result is None:
        raise click.ClickException(f"Baseline not found: {key.path}")

    snapshot, meta = result
    snap_json = snapshot_to_json(snapshot)
    _safe_write_output(output, snap_json)
    click.echo(
        f"Baseline pulled: {key.path} (abicheck {meta.abicheck_version}, "
        f"created {meta.created_at})",
        err=True,
    )


@baseline_group.command("list")
@click.argument("prefix", required=False, default=None)
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory.")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]),
              default="text", show_default=True)
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_list(prefix: str | None, registry_path: Path | None, fmt: str, verbose: bool) -> None:
    """List available ABI baselines in the registry.

    \b
    Example:
      abicheck baseline list
      abicheck baseline list libfoo
      abicheck baseline list --format json
    """
    _setup_verbosity(verbose)
    from .baseline import FilesystemRegistry

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    keys = registry.list(prefix=prefix)
    if not keys:
        click.echo("No baselines found.", err=True)
        return

    if fmt == "json":
        click.echo(json.dumps([
            {"library": k.library, "version": k.version,
             "platform": k.platform, "variant": k.variant, "path": k.path}
            for k in keys
        ], indent=2))
    else:
        for k in keys:
            click.echo(k.path)


@baseline_group.command("delete")
@click.argument("spec", type=str)
@click.option("--registry", "registry_path", type=click.Path(path_type=Path),
              default=None,
              help="Path to the baseline registry directory.")
@click.option("-v", "--verbose", is_flag=True, default=False)
def baseline_delete(spec: str, registry_path: Path | None, verbose: bool) -> None:
    """Delete an ABI baseline from the registry.

    SPEC is a colon-separated key: library:version:platform[:variant]

    \b
    Example:
      abicheck baseline delete libfoo:0.9.0:linux-x86_64
    """
    _setup_verbosity(verbose)
    from .baseline import BaselineKey, FilesystemRegistry

    reg_path = registry_path or Path(".abicheck/baselines")
    registry = FilesystemRegistry(reg_path)

    try:
        key = BaselineKey.from_spec(spec)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if registry.delete(key):
        click.echo(f"Baseline deleted: {key.path}", err=True)
    else:
        raise click.ClickException(f"Baseline not found: {key.path}")


if __name__ == "__main__":
    main()
