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

"""Input resolution & native-dump dispatch for the CLI.

This is the leaf module of the ``compare`` / ``dump`` input pipeline: given a
path, it detects the format (ELF / PE / Mach-O / JSON snapshot / ABICC Perl
dump / GNU ld linker script), follows linker scripts, dispatches native dumps
to the per-format builders, and loads or builds the resulting
:class:`~abicheck.model.AbiSnapshot`.

It is imported (and re-exported) by :mod:`abicheck.cli`; it deliberately does
**not** import ``cli`` so the dependency runs one way (``cli`` → ``cli_resolve``)
and stays cycle-free. Errors surface as ``click`` exceptions because every
caller is a CLI entry point — the parallel, framework-free contract lives in
:func:`abicheck.service.resolve_input` (which raises ``SnapshotError`` /
``ValidationError`` instead).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from .compat.abicc_dump_import import import_abicc_perl_dump, looks_like_perl_dump
from .dumper import dump
from .errors import AbicheckError
from .serialization import load_snapshot

if TYPE_CHECKING:
    from pathlib import Path

    from .model import AbiSnapshot

_HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp", ".inc"}

# Number of bytes to read when sniffing file format (covers ELF magic + JSON/Perl head)
_SNIFF_BYTES = 256


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
                f
                for f in p.rglob("*")
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


def _detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns 'elf', 'pe', 'macho', or None for non-binary / unknown.
    """
    from .binary_utils import detect_binary_format

    return detect_binary_format(path)


def _resolve_linker_script(path: Path) -> tuple[Path | None, bool]:
    """Resolve a GNU ld linker script to the shared library it points at.

    Returns ``(resolved_path, is_linker_script)``. ``is_linker_script`` is True
    when *path* looks like a GNU ld script (so callers can emit a targeted hint
    even when no target file could be located); ``resolved_path`` is the first
    ``INPUT()``/``GROUP()`` member that exists next to the script, or *None*.
    """
    from .binary_utils import resolve_linker_script

    return resolve_linker_script(path)


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
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    dwarf_only: bool = False,
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
                raise click.ClickException(
                    f"Include directory not found or not a directory: {inc}"
                )
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
    path: Path,
    binary_fmt: str,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
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
        return _dump_elf(
            path,
            headers,
            includes,
            version,
            lang,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )

    if binary_fmt == "pe":
        from .service import _dump_pe

        try:
            snap = _dump_pe(
                path,
                version,
                headers=headers,
                includes=includes,
                lang=lang,
                pdb_path=pdb_path,
            )
        except AbicheckError as exc:
            raise click.ClickException(str(exc)) from exc
        return _apply_native_provenance(snap, public_headers, public_header_dirs)

    if binary_fmt == "macho":
        from .service import _dump_macho

        try:
            snap = _dump_macho(
                path,
                version,
                headers=headers,
                includes=includes,
                lang=lang,
            )
        except AbicheckError as exc:
            raise click.ClickException(str(exc)) from exc
        return _apply_native_provenance(snap, public_headers, public_header_dirs)

    fmt_labels = {
        "elf": "ELF",
        "pe": "PE (Windows DLL)",
        "macho": "Mach-O (macOS dylib)",
    }
    raise click.ClickException(
        f"Unsupported binary format: {fmt_labels.get(binary_fmt, binary_fmt)}"
    )


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
            path,
            "elf",
            headers,
            includes,
            version,
            lang,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )

    # Detect binary format from magic bytes
    binary_fmt = _detect_binary_format(path) if is_elf is None else None
    if binary_fmt is not None:
        return _dump_native_binary(
            path,
            binary_fmt,
            headers,
            includes,
            version,
            lang,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
        )

    # Raw kernel type-info blob (a bare BTF/CTF section, e.g. from
    # `bpftool btf dump file <elf> format raw`): parse directly.
    from .service import _resolve_raw_typeinfo

    raw_typeinfo = _resolve_raw_typeinfo(path, version)
    if raw_typeinfo is not None:
        return raw_typeinfo

    # Text-based formats: detect by sniffing only a small header chunk
    fmt = _sniff_text_format(path)

    if fmt == "perl":
        try:
            return import_abicc_perl_dump(path)
        except (
            ValueError,
            KeyError,
            UnicodeDecodeError,
            OSError,
            AbicheckError,
        ) as exc:
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
                target,
                headers,
                includes,
                version,
                lang,
                dwarf_only=dwarf_only,
                debug_format=debug_format,
            )
        raise click.UsageError(
            f"'{path}' is a GNU ld linker script (INPUT/GROUP), not a binary, "
            "and its target could not be located next to it. Pass the actual "
            "shared library named in its INPUT(...) directive directly."
        )

    # Static / import library archives (.a / .lib) are member containers, not a
    # single linkable image — a deliberate non-goal (see
    # docs/concepts/limitations.md). Reject with actionable guidance.
    from .binary_utils import detect_archive

    if detect_archive(path):
        raise click.UsageError(
            f"'{path}' is a static/import library archive (.a/.lib), which abicheck "
            "does not analyse — it compares single linkable images (shared libraries "
            "and objects). Extract the members (e.g. `ar x lib.a`) and compare the "
            "resulting object files or the shared library built from them instead."
        )

    raise click.UsageError(
        f"Cannot detect format of '{path}'. "
        "Expected: ELF (.so), PE (.dll), Mach-O (.dylib), JSON snapshot, or ABICC Perl dump."
    )


def _populate_dependency_info(
    snap: AbiSnapshot,
    so_path: Path,
    search_paths: list[Path],
    sysroot: Path | None,
    ld_library_path: str,
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
        for b in bindings
        if b.status == BindingStatus.MISSING
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


def _resolve_compare_snapshots(
    old_input: Path,
    new_input: Path,
    old_fmt: str | None,
    new_fmt: str | None,
    old_h: list[Path],
    new_h: list[Path],
    old_inc: list[Path],
    new_inc: list[Path],
    old_version: str,
    new_version: str,
    lang: str,
    pdb_path: Path | None,
    old_pdb_path: Path | None,
    new_pdb_path: Path | None,
    dwarf_only: bool,
    debug_format: str | None,
    follow_deps: bool,
    search_paths: tuple[Path, ...],
    ld_library_path: str,
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Load both ABI snapshots and (optionally) populate ELF dependency info."""
    old = _resolve_input(
        old_input,
        old_h,
        old_inc,
        old_version,
        lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=old_pdb_path if old_pdb_path else pdb_path,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )
    new = _resolve_input(
        new_input,
        new_h,
        new_inc,
        new_version,
        lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=new_pdb_path if new_pdb_path else pdb_path,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
    )
    if follow_deps:
        if old_fmt == "elf":
            _populate_dependency_info(
                old, old_input, list(search_paths), None, ld_library_path
            )
        if new_fmt == "elf":
            _populate_dependency_info(
                new, new_input, list(search_paths), None, ld_library_path
            )
    return old, new
