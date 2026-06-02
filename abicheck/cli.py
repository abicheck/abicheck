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

"""CLI — abicheck dump | compare | compat (dump | check)."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .checker import DiffResult, LibraryMetadata, compare
from .compat.abicc_dump_import import import_abicc_perl_dump, looks_like_perl_dump
from .compat.cli import compat_group
from .dumper import dump
from .errors import AbicheckError
from .serialization import load_snapshot, snapshot_to_json

if TYPE_CHECKING:
    from .checker_types import Change, DiffResult
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
    """Fill provenance metadata on a snapshot (mutates in place).

    ``created_at`` honours ``SOURCE_DATE_EPOCH`` (the reproducible-builds
    standard): when set to a Unix timestamp, that fixed time is used instead of
    the wall clock, so two dumps of an identical library are byte-identical —
    enabling content-addressable caching and reproducible-build verification.
    An unset or malformed value falls back to the current time.
    """
    import os
    import subprocess

    snap.created_at = _provenance_timestamp(os.environ.get("SOURCE_DATE_EPOCH"))
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


def _provenance_timestamp(source_date_epoch: str | None) -> str:
    """ISO-8601 UTC timestamp, honouring ``SOURCE_DATE_EPOCH`` when valid."""
    import datetime

    if source_date_epoch:
        try:
            epoch = int(source_date_epoch.strip())
            return datetime.datetime.fromtimestamp(
                epoch, tz=datetime.timezone.utc
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            # Non-numeric or out-of-range epoch — fall back to wall clock
            # rather than aborting the dump.
            pass
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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


# GNU ld linker-script directives. The conventional ``libfoo.so`` development
# symlink is frequently a tiny ASCII script such as ``INPUT(libfoo.so.1)`` or
# ``GROUP ( /usr/lib/libfoo.so.1 AS_NEEDED ( ... ) )`` rather than an ELF file.
_LD_SCRIPT_RE = re.compile(r"\b(?:INPUT|GROUP|OUTPUT_FORMAT)\s*\(")
# Keywords that may appear as bare tokens inside INPUT()/GROUP() — never files.
_LD_KEYWORDS = frozenset({"AS_NEEDED", "INPUT", "GROUP", "OUTPUT_FORMAT"})


def _resolve_linker_script(path: Path) -> tuple[Path | None, bool]:
    """Resolve a GNU ld linker script to the shared library it points at.

    Returns ``(resolved_path, is_linker_script)``. ``is_linker_script`` is True
    when *path* looks like a GNU ld script (so callers can emit a targeted hint
    even when no target file could be located); ``resolved_path`` is the first
    ``INPUT()``/``GROUP()`` member that exists next to the script, or *None*.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return None, False
    # Strip C-style comments (real scripts start with ``/* GNU ld script */``).
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    if not _LD_SCRIPT_RE.search(text):
        return None, False
    # Collect candidate file tokens from inside INPUT()/GROUP() groups.
    for group in re.findall(r"(?:INPUT|GROUP)\s*\(([^)]*)\)", text):
        for tok in group.replace(",", " ").split():
            if tok in _LD_KEYWORDS or tok.startswith(("-l", "-L", "(")):
                continue
            # Only consider tokens that name a library file.
            if ".so" not in tok and not tok.endswith(".a"):
                continue
            candidate = Path(tok)
            for cand in (candidate, path.parent / tok, path.parent / candidate.name):
                if cand.is_file():
                    return cand, True
    return None, True


def _maybe_follow_linker_script(path: Path) -> Path:
    """Return the linker-script target if *path* is a resolvable GNU ld script.

    Emits a one-line note when it follows a script; otherwise returns *path*
    unchanged. Used by entry points that dispatch on binary format directly
    (e.g. ``dump``) rather than through :func:`_resolve_input`.
    """
    target, is_ld = _resolve_linker_script(path)
    if is_ld and target is not None and target.resolve() != path.resolve():
        click.echo(
            f"Note: '{path}' is a GNU ld linker script; following its "
            f"INPUT()/GROUP() directive to '{target}'.",
            err=True,
        )
        return target
    return path


def _normalize_binary_input(path: Path) -> tuple[Path, str | None]:
    """Detect a binary input's format, following GNU ld linker scripts.

    Returns ``(resolved_path, format)``. When *path* is a linker script that
    resolves to a real shared library, the resolved path and *its* format are
    returned so downstream metadata collection and dependency analysis operate
    on the actual DSO rather than the text script.
    """
    fmt = _detect_binary_format(path)
    if fmt is None:
        resolved = _maybe_follow_linker_script(path)
        if resolved != path:
            return resolved, _detect_binary_format(resolved)
    return path, fmt



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


def _apply_native_provenance(
    snap: AbiSnapshot,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
) -> AbiSnapshot:
    """Tag declaration provenance on a PE/Mach-O snapshot (ADR-024 Phase 1).

    Mirrors the ELF path (``dumper.create_snapshot``), which always runs
    ``apply_provenance``. A no-op when no public-header set is supplied —
    every origin stays ``UNKNOWN`` and behaviour is unchanged.
    """
    from .provenance import apply_provenance
    return apply_provenance(snap, public_headers, public_header_dirs)


def _dump_native_binary(
    path: Path, binary_fmt: str,
    headers: list[Path], includes: list[Path],
    version: str, lang: str,
    *,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Dump ABI snapshot from a native binary (ELF, PE, or Mach-O).

    For ELF, headers are required for full AST analysis unless dwarf_only
    is set or DWARF debug info is available (ADR-003 fallback chain).
    For PE/Mach-O, headers are optional: when supplied they scope the ABI
    surface to declarations in those public headers (best-effort, via castxml),
    otherwise the export table provides the symbol surface.

    ``public_headers`` / ``public_header_dirs`` classify declaration provenance
    (ADR-024 Phase 1). For PE they also let the PDB-derived types carry a
    ``ScopeOrigin``; an empty set keeps every origin ``UNKNOWN`` (no-op).
    """
    if binary_fmt == "elf":
        return _dump_elf(path, headers, includes, version, lang,
                         dwarf_only=dwarf_only, debug_format=debug_format)

    if binary_fmt == "pe":
        from .service import _dump_pe
        try:
            snap = _dump_pe(
                path, version,
                headers=headers, includes=includes, lang=lang,
                pdb_path=pdb_path,
            )
        except AbicheckError as exc:
            raise click.ClickException(str(exc)) from exc
        return _apply_native_provenance(snap, public_headers, public_header_dirs)

    if binary_fmt == "macho":
        from .service import _dump_macho
        try:
            snap = _dump_macho(
                path, version,
                headers=headers, includes=includes, lang=lang,
            )
        except AbicheckError as exc:
            raise click.ClickException(str(exc)) from exc
        return _apply_native_provenance(snap, public_headers, public_header_dirs)

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

    # GNU ld linker script (e.g. the ``libfoo.so`` dev symlink is the text
    # ``INPUT(libfoo.so.1)``): follow it to the real shared library.
    target, is_ld_script = _resolve_linker_script(path)
    if is_ld_script:
        if target is not None and target.resolve() != path.resolve():
            click.echo(
                f"Note: '{path}' is a GNU ld linker script; following its "
                f"INPUT()/GROUP() directive to '{target}'.",
                err=True,
            )
            return _resolve_input(
                target, headers, includes, version, lang,
                dwarf_only=dwarf_only, debug_format=debug_format,
            )
        raise click.UsageError(
            f"'{path}' is a GNU ld linker script (INPUT/GROUP), not a binary, "
            "and its target could not be located next to it. Pass the actual "
            "shared library named in its INPUT(...) directive directly."
        )

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


# Exit code for an invalid invocation (bad arguments, unknown option, invalid
# option value, unreadable/unrecognised input path). Chosen as sysexits.h
# ``EX_USAGE`` so it sits *outside* the compare/compat result space
# {0, 1, 2, 4} — a CI script can therefore tell "you called me wrong" apart
# from a real ABI verdict. Click defaults ``UsageError`` to exit 2, which
# collides with ``compare``'s documented "2 = source break"; this remaps it.
_EXIT_USAGE_ERROR = 64


class _AbicheckGroup(click.Group):
    """Root group that maps Click *usage* errors to a dedicated exit code.

    Click exits 2 for ``UsageError`` / ``BadParameter`` (bad arguments, unknown
    options, invalid option values, missing/unreadable input paths), which
    collides with ``compare``'s documented ``2 = source break`` result. Remap
    just that code to ``_EXIT_USAGE_ERROR`` so an invalid invocation is never
    mistaken for an ABI verdict. Other ``ClickException``s (exit 1, used for
    operational failures such as malformed input or an expired strict waiver),
    verdict exits (``SystemExit`` 2/4), and the ``compat`` error scheme (3–11)
    are deliberately left untouched.
    """

    def main(self, *args: Any, standalone_mode: bool = True, **kwargs: Any) -> Any:  # type: ignore[override]
        if not standalone_mode:
            return super().main(*args, standalone_mode=False, **kwargs)  # type: ignore[call-overload]
        try:
            super().main(*args, standalone_mode=False, **kwargs)  # type: ignore[call-overload]
        except click.exceptions.Abort:
            click.echo("Aborted!", err=True)
            sys.exit(1)
        except click.exceptions.ClickException as exc:
            exc.show()
            # Only Click's usage-error code (2) collides with a compare verdict.
            sys.exit(_EXIT_USAGE_ERROR if exc.exit_code == 2 else exc.exit_code)
        else:
            sys.exit(0)


@click.group(cls=_AbicheckGroup)
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
# ── Declaration provenance (ADR-015) ─────────────────────────────────────────
@click.option("--public-header", "public_headers", multiple=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Header treated as public for provenance classification (repeat for "
                   "multiple). Declarations are tagged public/private/system in the snapshot. "
                   "Opt-in: omitting this leaves every origin UNKNOWN.")
@click.option("--public-header-dir", "public_header_dirs", multiple=True,
              type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Directory whose headers are treated as public for provenance "
                   "classification (repeat for multiple).")
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
             public_headers: tuple[Path, ...], public_header_dirs: tuple[Path, ...],
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

    # Auto-detect binary format — PE/Mach-O skip the ELF/castxml path. The
    # conventional ``libfoo.so`` dev symlink is often a GNU ld linker script;
    # follow it to the real shared library before dispatching.
    so_path, binary_fmt = _normalize_binary_input(so_path)
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
            public_headers,
            public_header_dirs,
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
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
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
    public_headers: tuple[Path, ...] = (),
    public_header_dirs: tuple[Path, ...] = (),
) -> None:
    """Handle PE/Mach-O native dump path and output writing."""
    if follow_deps:
        click.echo("Warning: --follow-deps is only supported for ELF binaries.", err=True)
    try:
        snap = _dump_native_binary(
            so_path, binary_fmt, list(headers), list(includes), version, lang,
            pdb_path=pdb_path,
            public_headers=list(public_headers),
            public_header_dirs=list(public_header_dirs),
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


def _collect_force_public_symbols(
    public_symbols: tuple[str, ...], symbols_list: Path | None,
) -> set[str]:
    """Merge --public-symbol values with a --public-symbols-list file.

    The list file is one symbol per line; blank lines and ``#`` comments are
    ignored (à la abi-compliance-checker -symbols-list). Inline trailing
    comments are not stripped — a ``#`` must start the line to be a comment.
    """
    out: set[str] = {s.strip() for s in public_symbols if s.strip()}
    if symbols_list is not None:
        for raw in symbols_list.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                out.add(line)
    return out


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
    show_recommendation: bool = False,
    demangle: bool = False,
) -> str:
    """Render comparison result in the requested output format."""
    from .service import render_output
    return render_output(
        fmt, result, old, new,
        follow_deps=follow_deps, show_only=show_only,
        report_mode=report_mode, show_impact=show_impact,
        stat=stat, severity_config=severity_config,
        show_recommendation=show_recommendation,
        demangle=demangle,
    )


def _collect_additions(result: DiffResult) -> list[object]:
    """Collect additive changes in a policy-independent way."""
    from .checker_policy import COMPATIBLE_KINDS
    addition_kinds = {k for k in COMPATIBLE_KINDS if k.value.endswith("_added")}
    return [c for c in result.changes if c.kind in addition_kinds]


def _load_probe_matrix_changes(
    probe_matrix_old: Path | None, probe_matrix_new: Path | None,
) -> list[Change] | None:
    """Load build-config matrix snapshots and return diff_matrix() findings.

    These findings (CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV,
    BEHAVIOURAL_DEFAULT_CHANGED) need multi-configuration inputs the plain
    compare() does not have, so they are computed here and merged in (G2).
    """
    if probe_matrix_old is None and probe_matrix_new is None:
        return None
    if probe_matrix_old is None or probe_matrix_new is None:
        raise click.UsageError(
            "--probe-matrix-old and --probe-matrix-new must be given together."
        )
    from .diff_build_config import diff_matrix
    from .probe_harness import load_matrix_snapshot

    old_matrix = load_matrix_snapshot(probe_matrix_old)
    new_matrix = load_matrix_snapshot(probe_matrix_new)
    return list(diff_matrix(old_matrix, new_matrix))


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
    scope_to_public_surface: bool = False,
) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """Run compare for one old/new pair and return result + resolved snapshots."""
    # Follow GNU ld linker scripts up front so metadata/dependency analysis use
    # the resolved DSO, not the text script.
    old_input, old_fmt = _normalize_binary_input(old_input)
    new_input, new_fmt = _normalize_binary_input(new_input)

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
    result = compare(
        old, new, suppression=suppression, policy=policy, policy_file=pf,
        scope_to_public_surface=scope_to_public_surface,
    )
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


def _echo_filtered_surface(result: DiffResult) -> None:
    """Print the public-surface audit ledger (ADR-024 §D5 traceability)."""
    n = result.out_of_surface_count
    click.echo(
        f"\nFiltered as non-public ABI surface ({n} "
        f"{'finding' if n == 1 else 'findings'}, --scope-public-headers):",
        err=True,
    )
    for c in result.out_of_surface_changes:
        loc = f" [{c.source_location}]" if c.source_location else ""
        reason = f" ({c.surface_exclusion_reason})" if c.surface_exclusion_reason else ""
        click.echo(f"  - {c.kind.value}: {c.symbol}{loc}{reason}", err=True)


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


def _log_one_side_debug(
    label: str, binary: Path, droots: list[Path],
    *,
    debuginfod: bool, debuginfod_url: str | None,
) -> None:
    """Resolve and log debug info for a single binary side, if applicable."""
    if _detect_binary_format(binary) is None or not (droots or debuginfod):
        return
    from .debug_resolver import resolve_debug_info

    artifact = resolve_debug_info(
        binary,
        debug_roots=droots or None,
        enable_debuginfod=debuginfod,
        debuginfod_urls=[debuginfod_url] if debuginfod_url else None,
    )
    if artifact:
        click.echo(f"Debug info ({label}): {artifact.source}", err=True)


def _log_debug_resolution(
    old_input: Path, new_input: Path,
    resolved_old_debug: list[Path], resolved_new_debug: list[Path],
    *,
    debuginfod: bool, debuginfod_url: str | None,
) -> None:
    """Resolve and log per-side debug info (debug roots / debuginfod), if any."""
    if not (resolved_old_debug or resolved_new_debug or debuginfod):
        return
    _log_one_side_debug(
        "old", old_input, resolved_old_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )
    _log_one_side_debug(
        "new", new_input, resolved_new_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )


def _resolve_compare_snapshots(
    old_input: Path, new_input: Path,
    old_fmt: str | None, new_fmt: str | None,
    old_h: list[Path], new_h: list[Path],
    old_inc: list[Path], new_inc: list[Path],
    old_version: str, new_version: str, lang: str,
    pdb_path: Path | None, old_pdb_path: Path | None, new_pdb_path: Path | None,
    dwarf_only: bool, debug_format: str | None,
    follow_deps: bool, search_paths: tuple[Path, ...], ld_library_path: str,
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Load both ABI snapshots and (optionally) populate ELF dependency info."""
    old = _resolve_input(
        old_input, old_h, old_inc, old_version, lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=old_pdb_path if old_pdb_path else pdb_path,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )
    new = _resolve_input(
        new_input, new_h, new_inc, new_version, lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=new_pdb_path if new_pdb_path else pdb_path,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )
    if follow_deps:
        if old_fmt == "elf":
            _populate_dependency_info(old, old_input, list(search_paths), None, ld_library_path)
        if new_fmt == "elf":
            _populate_dependency_info(new, new_input, list(search_paths), None, ld_library_path)
    return old, new


def _finalize_compare_result(
    result: DiffResult, old_input: Path, new_input: Path,
    *,
    show_redundant: bool, show_filtered: bool,
    annotate: bool, annotate_additions: bool,
) -> None:
    """Attach metadata and emit redundancy/filter/suppression/annotation output."""
    result.old_metadata = _collect_metadata(old_input)
    result.new_metadata = _collect_metadata(new_input)

    if show_redundant and result.redundant_changes:
        _merge_redundant_changes(result)
    if show_filtered and result.out_of_surface_changes:
        _echo_filtered_surface(result)

    # The scoping fallback warning goes to stderr so it never corrupts the
    # machine-readable payload on stdout (which carries scope_resolved /
    # manual_review_required for programmatic consumers).
    if result.scope_to_public_surface and not result.scope_resolved:
        click.echo(
            "Warning: --scope-public-headers could not resolve the public "
            "surface (no header-derived public symbols); fell back to the full "
            "export table. Compatibility is UNCONFIRMED — treat this result as "
            "manual-review-required, not a clean public surface.",
            err=True,
        )

    _warn_all_suppressed(result)
    _maybe_emit_annotations(
        result, annotate=annotate, annotate_additions=annotate_additions
    )


@main.command("compare")
@click.argument("old_input", type=click.Path(exists=True, path_type=Path))
@click.argument("new_input", type=click.Path(exists=True, path_type=Path))
# ── Dump options (used when input is an ELF binary) ──────────────────────────
@click.option("-H", "--header", "headers", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header file or directory applied to both sides (repeat for multiple). "
                   "Recommended for full ABI analysis; without headers, native binaries fall back to symbols-only mode. "
                   "Scopes the ABI surface to declarations in these headers for ELF; on PE/Mach-O scoping is "
                   "best-effort and falls back to the export table when castxml is unavailable or names don't match "
                   "(e.g. MSVC C++ mangling). Validated for native binaries; ignored for snapshots.")
@click.option("-I", "--include", "includes", multiple=True,
              type=click.Path(path_type=Path),
              help="Extra include directory for castxml (applied to both sides).")
@click.option("--lang", default="c++", show_default=True,
              type=click.Choice(["c++", "c"], case_sensitive=False),
              help="Language mode for castxml.")
@click.option("--old-header", "old_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for old side only (overrides -H for old). "
                   "Validated for native binaries; ignored for snapshots.")
@click.option("--new-header", "new_headers_only", multiple=True,
              type=click.Path(path_type=Path),
              help="Public header for new side only (overrides -H for new). "
                   "Validated for native binaries; ignored for snapshots.")
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
@click.option("--format", "fmt", type=click.Choice(["json", "markdown", "sarif", "html", "junit", "review"]),
              default="markdown", show_default=True,
              help="Output format. 'review' emits a compact GitHub-facing digest "
                   "(verdict + counts + release recommendation + manual-review banner) "
                   "suitable for a job summary or PR comment.")
@click.option("--demangle/--no-demangle", default=False, show_default=True,
              help="Demangle C++ symbol names in human-facing output (markdown, "
                   "review). Recommended for C++ libraries; machine formats "
                   "(json/sarif/junit) always keep raw mangled symbols.")
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
@click.option("--scope-public-headers/--no-scope-public-headers", "scope_public_headers",
              default=True, show_default=True,
              help="Restrict findings to the public-header ABI surface (ADR-024): "
                   "changes to symbols/types not reachable from public-header-declared "
                   "exported API are recorded as filtered, not reported. Internal-type "
                   "leaks are never hidden. On by default; use --no-scope-public-headers "
                   "to report every finding regardless of surface.")
@click.option("--show-filtered", "show_filtered", is_flag=True, default=False,
              help="List findings excluded by --scope-public-headers (audit trail).")
@click.option("--public-symbol", "public_symbols", multiple=True,
              help="Widening overlay (ADR-024 §D6): force a symbol (mangled or demangled "
                   "name) into the public surface even when header provenance can't see it "
                   "(asm stubs, .def exports, extern \"C\" shims, MSVC-mangling gaps). "
                   "Repeatable. Only meaningful with --scope-public-headers.")
@click.option("--public-symbols-list", "public_symbols_list",
              type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="File of symbols to force public (one per line; '#' comments and blank "
                   "lines ignored), à la abi-compliance-checker -symbols-list. "
                   "Merged with --public-symbol.")
@click.option("--probe-matrix-old", "probe_matrix_old", type=click.Path(exists=True, path_type=Path),
              default=None,
              help="Old build-configuration matrix snapshot (from 'abicheck probe run'). "
                   "When given with --probe-matrix-new, build-config findings "
                   "(CXX_STANDARD_FLOOR_RAISED, API_DEPENDS_ON_CONSUMER_ENV, "
                   "BEHAVIOURAL_DEFAULT_CHANGED) are folded into this comparison's "
                   "verdict and report (G2: probe -> compare).")
@click.option("--probe-matrix-new", "probe_matrix_new", type=click.Path(exists=True, path_type=Path),
              default=None,
              help="New build-configuration matrix snapshot (pairs with --probe-matrix-old).")
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
@click.option("--recommend", is_flag=True, default=False,
              help="Append a release recommendation (semver bump + SONAME action) to the "
                   "report. Always present in --format json under 'release_recommendation'.")
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
    fmt: str, demangle: bool, output: Path | None,
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
    scope_public_headers: bool, show_filtered: bool,
    public_symbols: tuple[str, ...], public_symbols_list: Path | None,
    report_mode: str, show_impact: bool,
    recommend: bool,
    debug_format: str | None,
    annotate: bool,
    annotate_additions: bool,
    debug_roots: tuple[Path, ...],
    debug_roots_old: tuple[Path, ...],
    debug_roots_new: tuple[Path, ...],
    debuginfod: bool,
    debuginfod_url: str | None,
    verbose: bool,
    probe_matrix_old: Path | None = None,
    probe_matrix_new: Path | None = None,
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
    Invalid invocation (bad arguments/options, unreadable or unrecognised
    input) exits 64, outside the result space above, so it is never mistaken
    for an ABI verdict.

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

    # Follow GNU ld linker scripts up front so the resolved DSO (not the text
    # script) drives format detection, metadata, and dependency analysis.
    old_input, old_fmt = _normalize_binary_input(old_input)
    new_input, new_fmt = _normalize_binary_input(new_input)
    _warn_ignored_flags(
        old_fmt is not None, new_fmt is not None,
        headers, includes,
        old_headers_only, new_headers_only,
        old_includes_only, new_includes_only,
    )

    # Resolve per-side debug roots: --debug-root1 overrides --debug-root for old, etc.
    resolved_old_debug = list(debug_roots_old) if debug_roots_old else list(debug_roots)
    resolved_new_debug = list(debug_roots_new) if debug_roots_new else list(debug_roots)
    _log_debug_resolution(
        old_input, new_input,
        resolved_old_debug, resolved_new_debug,
        debuginfod=debuginfod, debuginfod_url=debuginfod_url,
    )

    old, new = _resolve_compare_snapshots(
        old_input, new_input, old_fmt, new_fmt,
        old_h, new_h, old_inc, new_inc,
        old_version, new_version, lang,
        pdb_path, old_pdb_path, new_pdb_path,
        dwarf_only, debug_format,
        follow_deps, search_paths, ld_library_path,
    )

    suppression, pf = _load_suppression_and_policy(
        suppress, policy, policy_file_path,
        strict_suppressions=strict_suppressions,
        require_justification=require_justification,
    )

    force_public = _collect_force_public_symbols(public_symbols, public_symbols_list)
    if force_public and not scope_public_headers:
        click.echo(
            "Warning: --public-symbol/--public-symbols-list only take effect with "
            "--scope-public-headers; ignoring the widening overlay.",
            err=True,
        )

    extra_changes = _load_probe_matrix_changes(probe_matrix_old, probe_matrix_new)

    result = compare(
        old, new, suppression=suppression, policy=policy, policy_file=pf,
        scope_to_public_surface=scope_public_headers,
        force_public_symbols=force_public,
        extra_changes=extra_changes,
    )

    _finalize_compare_result(
        result, old_input, new_input,
        show_redundant=show_redundant, show_filtered=show_filtered,
        annotate=annotate, annotate_additions=annotate_additions,
    )

    text = _render_output(
        fmt, result, old, new,
        follow_deps=follow_deps,
        show_only=show_only, report_mode=report_mode,
        show_impact=show_impact, stat=stat,
        severity_config=sev_config if severity_explicitly_set else None,
        show_recommendation=recommend,
        demangle=demangle,
    )

    _write_or_echo(output, text)

    _exit_with_severity_or_verdict(result, sev_config, severity_explicitly_set)



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






# ---------------------------------------------------------------------------
# Sub-command modules. Imported for side-effect so their @main.command(...)
# decorators register the commands on the Click group above. They sit in
# sibling files to keep this module under the AI-readiness file-size limit.
# ---------------------------------------------------------------------------
from . import (  # noqa: E402  — must run after `main` and helpers are defined
    cli_appcompat,  # noqa: F401  — registers appcompat
    cli_baseline,  # noqa: F401  — registers baseline
    cli_compare_release,  # noqa: F401  — registers compare-release
    cli_debian_symbols,  # noqa: F401  — registers debian-symbols
    cli_probe,  # noqa: F401  — registers probe (run, compare)
    cli_stack,  # noqa: F401  — registers deps, stack-check
    cli_suggest,  # noqa: F401  — registers suggest-suppressions
)

if __name__ == "__main__":
    main()
