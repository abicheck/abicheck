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

from .compat.abicc_dump_import import looks_like_perl_dump

if TYPE_CHECKING:
    from pathlib import Path

    from .model import AbiSnapshot


def _click_notify(message: str) -> None:
    """Emit a service-layer progress note to stderr via click.

    Passed as the ``notify`` callback to :func:`abicheck.service.resolve_input` /
    :func:`abicheck.service.run_dump` so their user-facing notes (linker-script
    following, "no headers provided", "--include ignored") reach the CLI's stderr
    exactly as they did when this logic lived in the CLI.
    """
    click.echo(message, err=True)

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
    header_backend: str = "auto",
) -> AbiSnapshot:
    """Dump an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    Thin CLI wrapper over :func:`abicheck.service.run_dump` — the single source
    of truth for native dumping. It supplies a ``click.echo`` notifier so the
    "no headers" / "--include ignored" notes still reach stderr, and translates
    the framework-free errors into the CLI's ``click`` exceptions, preserving
    exit codes: ``ValidationError`` (unusable input / bad arguments) →
    :class:`click.UsageError` (exit 64); ``SnapshotError`` (operational failure)
    → :class:`click.ClickException` (exit 1).

    ``public_headers`` / ``public_header_dirs`` classify declaration provenance
    (ADR-024 Phase 1) on PE/Mach-O snapshots; a no-op for ELF and when empty.
    """
    from . import service
    from .errors import SnapshotError, ValidationError

    try:
        return service.run_dump(
            path,
            binary_fmt,
            headers,
            includes,
            version,
            lang,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            header_backend=header_backend,
            notify=_click_notify,
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


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
    header_backend: str = "auto",
) -> AbiSnapshot:
    """Auto-detect input type and return an AbiSnapshot.

    Thin CLI wrapper over :func:`abicheck.service.resolve_input` — the single
    source of truth for format detection, linker-script following, and native
    dumping. It supplies a ``click.echo`` notifier so progress notes reach
    stderr unchanged, and maps the framework-free errors to ``click`` exceptions
    so exit codes are preserved: ``ValidationError`` (unrecognised / unusable
    input) → :class:`click.UsageError` (exit 64); ``SnapshotError`` (operational
    failure loading or building the snapshot) → :class:`click.ClickException`
    (exit 1).

    Args:
        path: Path to the input file.
        headers: Public header files (required for ELF inputs).
        includes: Extra include directories (used for ELF inputs).
        version: Version label to embed in the resulting snapshot.
        lang: Language mode for castxml (``c++`` or ``c``).
        is_elf: Pre-computed ELF detection result; if *None*, the service layer
            detects the format from magic bytes.
        dwarf_only: If True, force DWARF-only mode (ADR-003).
        debug_format: Force debug format ("dwarf", "btf", "ctf") or None for auto.
    """
    from . import service
    from .errors import SnapshotError, ValidationError

    try:
        return service.resolve_input(
            path,
            headers,
            includes,
            version,
            lang,
            is_elf=is_elf,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            header_backend=header_backend,
            notify=_click_notify,
        )
    except ValidationError as exc:
        raise click.UsageError(str(exc)) from exc
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


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


def _looks_like_application(path: Path) -> bool:
    """Positively identify an ELF *application* (executable), not a library.

    Returns True only when we are confident the file is an executable:
    ``ET_EXEC``, or a PIE (``ET_DYN`` with a ``PT_INTERP`` segment and a
    non-``.so`` filename). Anything inconclusive (unreadable, malformed program
    headers, a versioned ``.so`` name) returns False so the operand stays on the
    normal single-artifact path — we never *guess* a binary is an app (ADR-037
    D7: when the kind is genuinely ambiguous, the caller asks the user rather
    than mis-dispatching).
    """
    import struct

    from .package import (
        _ELF_MAGIC,
        _ET_DYN,
        _has_interp_segment,
        _has_shared_object_name,
    )

    _ET_EXEC = 2
    try:
        with open(path, "rb") as f:
            if f.read(4) != _ELF_MAGIC:
                return False
            ei_class_raw = f.read(1)
            ei_data_raw = f.read(1)
            if len(ei_class_raw) != 1 or len(ei_data_raw) != 1:
                return False
            ei_class = ei_class_raw[0]
            ei_data = ei_data_raw[0]
            # Unknown class/endianness ⇒ inconclusive: return False rather than
            # fall through to big-endian parsing and risk misreading e_type.
            if ei_class not in (1, 2) or ei_data not in (1, 2):
                return False
            f.seek(16)
            byte_order = "<" if ei_data == 1 else ">"
            e_type = struct.unpack(f"{byte_order}H", f.read(2))[0]
            if e_type == _ET_EXEC:
                return True
            if e_type == _ET_DYN:
                # PIE executable: ET_DYN + an interpreter + not a .so-style name.
                has_interp = _has_interp_segment(f, ei_class, byte_order)
                return has_interp is True and not _has_shared_object_name(path)
            return False
    except (OSError, struct.error, IndexError):
        return False


def classify_compare_operand(path: Path) -> str:
    """Classify a ``compare`` operand for ADR-037 D7 input-type dispatch.

    Returns one of:

    * ``"package"``   — a recognised archive/package (RPM/Deb/tar/conda/wheel);
      a *set* input that fans out to per-library comparison.
    * ``"directory"`` — a plain directory of libraries; also a set input.
    * ``"app"``       — an ELF application/executable (or ambiguous PIE) that
      ``compare`` cannot pair as a library (hint the user at ``appcompat``).
    * ``"file"``      — a single ``.so`` / JSON snapshot / Perl dump: the default
      single-pair path, unchanged.
    """
    from .package import is_package

    if path.is_dir():
        return "directory"
    if is_package(path):
        return "package"
    norm, fmt = _normalize_binary_input(path)
    if fmt == "elf" and _looks_like_application(norm):
        return "app"
    return "file"


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
    header_backend: str = "auto",
    old_header_backend: str | None = None,
    new_header_backend: str | None = None,
) -> tuple[AbiSnapshot, AbiSnapshot]:
    """Load both ABI snapshots and (optionally) populate ELF dependency info.

    ``header_backend`` is the both-sides default; ``old_header_backend`` /
    ``new_header_backend`` override it for one side only (``None`` = inherit).
    A per-side override lets a release whose new headers need the host
    toolchain parse on ``clang`` while the old release keeps the ``castxml``
    schema reference — the backend mirror of ``--old-header``/``--new-header``.
    """
    old_backend = old_header_backend or header_backend
    new_backend = new_header_backend or header_backend
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
        header_backend=old_backend,
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
        header_backend=new_backend,
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
