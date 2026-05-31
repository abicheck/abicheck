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

"""Service layer — shared orchestration for CLI and MCP server.

Provides framework-agnostic functions for the core abicheck operations:

- :func:`resolve_input` — Load an ABI snapshot from any supported input format
- :func:`run_dump` — Extract ABI snapshot from a binary + optional headers
- :func:`run_compare` — Compare two ABI snapshots and return classified changes
- :func:`render_output` — Render a DiffResult to the specified output format
"""
from __future__ import annotations

import hashlib
import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from .checker import compare
from .checker_types import DiffResult, LibraryMetadata
from .errors import AbicheckError, SnapshotError, ValidationError
from .model import AbiSnapshot, EnumType, Function, RecordType, Visibility
from .reporter import to_json, to_markdown, to_stat, to_stat_json
from .serialization import load_snapshot

if TYPE_CHECKING:
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .policy_file import PolicyFile
    from .severity import SeverityConfig
    from .suppression import SuppressionList

_logger = logging.getLogger(__name__)

# Magic-byte length for format detection
_SNIFF_BYTES = 256

# Header file extensions recognised during directory expansion
_HEADER_EXTS = frozenset({
    ".h", ".hh", ".hpp", ".hxx", ".h++",
    ".ipp", ".tpp", ".inc",
})


# ── Input resolution ────────────────────────────────────────────────────────


def detect_binary_format(path: Path) -> str | None:
    """Detect binary format from magic bytes.

    Returns ``'elf'``, ``'pe'``, ``'macho'``, or *None* for non-binary / unknown.
    """
    from .binary_utils import detect_binary_format as _detect
    return _detect(path)


def sniff_text_format(path: Path) -> str:
    """Read a small header chunk and return ``'json'``, ``'perl'``, or ``'unknown'``."""
    from .compat.abicc_dump_import import looks_like_perl_dump

    try:
        with open(path, "rb") as f:
            raw = f.read(_SNIFF_BYTES)
        head = raw.decode("utf-8", errors="replace").lstrip()
    except OSError:
        return "unknown"
    if looks_like_perl_dump(head):
        return "perl"
    if head.startswith("{"):
        return "json"
    return "unknown"


def expand_header_inputs(inputs: list[Path]) -> list[Path]:
    """Expand header inputs where each item can be a file or a directory.

    Directories are scanned recursively for known header extensions.

    Raises:
        ValidationError: If a path does not exist or a header directory is empty.
    """
    out: list[Path] = []
    for p in inputs:
        if not p.exists():
            raise ValidationError(f"Header file not found or not a file: {p}")
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found = [
                f for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in _HEADER_EXTS
            ]
            if not found:
                raise ValidationError(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(sorted(found))
            continue
        raise ValidationError(f"Header path is neither file nor directory: {p}")

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


def resolve_input(
    path: Path,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    version: str = "",
    lang: str = "c++",
    *,
    is_elf: bool | None = None,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
) -> AbiSnapshot:
    """Auto-detect input type and return an ABI snapshot.

    Detection order:

    1. Native binary (ELF / PE / Mach-O, detected by magic bytes)
    2. ABICC Perl dump (``$VAR1`` prefix) → :func:`import_abicc_perl_dump`
    3. JSON snapshot (``{`` prefix) → :func:`load_snapshot`

    Raises:
        SnapshotError: If the snapshot cannot be loaded from the input.
        ValidationError: If the input format cannot be detected.
    """
    _headers = headers or []
    _includes = includes or []

    # Fast path: caller already knows it's ELF
    if is_elf is True:
        return run_dump(
            path, "elf", _headers, _includes, version, lang,
            dwarf_only=dwarf_only,
            debug_roots=debug_roots, enable_debuginfod=enable_debuginfod,
        )

    # Detect binary format from magic bytes
    binary_fmt = detect_binary_format(path) if is_elf is None else None
    if binary_fmt is not None:
        return run_dump(
            path, binary_fmt, _headers, _includes, version, lang,
            pdb_path=pdb_path, dwarf_only=dwarf_only,
            debug_roots=debug_roots, enable_debuginfod=enable_debuginfod,
        )

    # Text-based formats
    fmt = sniff_text_format(path)

    if fmt == "perl":
        from .compat.abicc_dump_import import import_abicc_perl_dump
        try:
            return import_abicc_perl_dump(path)
        except (ValueError, KeyError, UnicodeDecodeError, OSError, AbicheckError) as exc:
            raise SnapshotError(f"Failed to import ABICC Perl dump '{path}': {exc}") from exc

    if fmt == "json":
        try:
            return load_snapshot(path)
        except (ValueError, KeyError, UnicodeDecodeError, OSError) as exc:
            raise SnapshotError(f"Failed to load JSON snapshot '{path}': {exc}") from exc

    raise ValidationError(
        f"Cannot detect format of '{path}'. "
        "Expected: ELF (.so), PE (.dll), Mach-O (.dylib), JSON snapshot, or ABICC Perl dump."
    )


# ── Binary dumping ──────────────────────────────────────────────────────────


def run_dump(
    path: Path,
    binary_fmt: str,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    version: str = "",
    lang: str = "c++",
    *,
    pdb_path: Path | None = None,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
) -> AbiSnapshot:
    """Extract an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    Raises:
        SnapshotError: If the binary cannot be parsed.
        ValidationError: For invalid arguments (missing exports, bad include dirs).
    """
    _headers = headers or []
    _includes = includes or []

    if binary_fmt == "elf":
        snap = _dump_elf(
            path, _headers, _includes, version, lang,
            dwarf_only=dwarf_only,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
        )
        _try_attach_sycl_metadata(snap, path)
        return snap
    if binary_fmt == "pe":
        return _dump_pe(
            path, version,
            headers=_headers, includes=_includes, lang=lang,
            pdb_path=pdb_path,
        )
    if binary_fmt == "macho":
        return _dump_macho(
            path, version,
            headers=_headers, includes=_includes, lang=lang,
        )
    raise ValidationError(f"Unsupported binary format: {binary_fmt}")


def _try_attach_sycl_metadata(snap: AbiSnapshot, lib_path: Path) -> None:
    """Auto-detect SYCL distribution and attach plugin metadata.

    Runs only when ``lib_path`` lives in a directory that looks like a
    SYCL runtime distribution (contains ``libsycl.so`` or ``libacpp-rt.so``).
    Cost for non-SYCL libraries: one ``_detect_sycl_implementation()`` call
    which is a few ``Path.exists()`` checks — effectively zero overhead.
    """
    from .sycl_metadata import parse_sycl_metadata

    lib_dir = lib_path.resolve().parent
    try:
        sycl = parse_sycl_metadata(lib_dir)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("SYCL metadata extraction skipped: %s", exc)
        return
    if sycl is not None:
        snap.sycl = sycl
        _logger.info(
            "SYCL metadata attached: implementation=%s, %d plugin(s)",
            sycl.implementation,
            len(sycl.plugins),
        )


def _dump_elf(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    dwarf_only: bool = False,
    debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
) -> AbiSnapshot:
    """Dump an ELF binary to an ABI snapshot."""
    from .dumper import dump

    resolved_headers = expand_header_inputs(headers) if headers else []
    if not resolved_headers and not dwarf_only:
        _logger.warning(
            "'%s' — no headers provided. "
            "Will use DWARF debug info if available, else symbols-only mode.",
            path,
        )
    if resolved_headers and not dwarf_only:
        for inc in includes:
            if not inc.exists() or not inc.is_dir():
                raise ValidationError(f"Include directory not found or not a directory: {inc}")
    elif includes and not dwarf_only:
        _logger.warning("Include paths are ignored without headers.")

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
        )
    except (AbicheckError, RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to dump '{path}': {exc}") from exc


def _has_matched_public_surface(snap: AbiSnapshot) -> bool:
    """True if header parsing matched at least one exported symbol.

    ``dumper._dump_pe`` / ``dumper._dump_macho`` mark a declaration ``PUBLIC``
    only when its (mangled) name is present in the binary's export table.  When
    no declaration matches — e.g. an MSVC-mangled C++ DLL parsed with a
    Clang/GCC toolchain that emits Itanium names — every symbol collapses to
    ``HIDDEN`` and header scoping has had no effect.
    """
    return any(f.visibility == Visibility.PUBLIC for f in snap.functions) or any(
        v.visibility == Visibility.PUBLIC for v in snap.variables
    )


def _try_header_scoped_dump(
    fmt: str,
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
) -> tuple[AbiSnapshot | None, str | None]:
    """Attempt a castxml header-scoped dump for a PE/Mach-O binary.

    Returns ``(snapshot, None)`` when castxml is available *and* at least one
    declared symbol matched the export table.  Returns ``(None, reason)`` (after
    emitting a ``UserWarning``) when scoping is unavailable or had no effect, so
    the caller can fall back to export-table mode and record the structured
    confidence signal (ADR-024 §D5.3).  ``reason`` is one of
    ``"castxml-unavailable"`` / ``"mangling-fallback"``.  This mirrors the
    public-API scoping that ``abidw --headers-dir`` / abi-dumper apply for ELF.
    """
    from .dumper import _dump_macho as _dumper_macho
    from .dumper import _dump_pe as _dumper_pe

    # Expand header directories into individual files (same as the ELF path),
    # so `--header <dir>` scopes correctly instead of feeding a directory to
    # castxml's `#include`. Done *outside* the broad except below so a genuinely
    # bad/empty header path raises a clear ValidationError rather than silently
    # falling back to the full export table.
    resolved_headers = expand_header_inputs(headers)

    compiler = "cc" if lang.lower() == "c" else "c++"
    lang_arg = lang if lang.lower() == "c" else None
    try:
        if fmt == "pe":
            snap = _dumper_pe(path, resolved_headers, includes, version, compiler, lang=lang_arg)
        else:
            snap = _dumper_macho(path, resolved_headers, includes, version, compiler, lang=lang_arg)
    except Exception as exc:  # noqa: BLE001 — castxml missing / parse failure → fall back
        warnings.warn(
            f"Header-based ABI scoping unavailable for '{path.name}' "
            f"({fmt.upper()}): {exc}. Falling back to export-table mode — "
            f"--header/--include were ignored.",
            UserWarning,
            stacklevel=2,
        )
        return None, "castxml-unavailable"

    if not _has_matched_public_surface(snap):
        warnings.warn(
            f"None of the provided headers matched exported symbols in "
            f"'{path.name}'. This commonly happens when a C++ {fmt.upper()} binary "
            f"uses a name-mangling scheme (e.g. MSVC) different from the compiler "
            f"used to parse the headers. Falling back to export-table mode — "
            f"header-based scoping had no effect.",
            UserWarning,
            stacklevel=2,
        )
        return None, "mangling-fallback"
    return snap, None


def _extract_pdb_debug(
    path: Path, pdb_path: Path | None
) -> tuple[DwarfMetadata | None, AdvancedDwarfMetadata | None]:
    """Locate and parse a PDB for *path*.

    Returns ``(dwarf_meta, dwarf_adv)`` or ``(None, None)`` when no PDB is found
    or parsing fails.  PDB extraction is best-effort and never fatal.
    """
    try:
        from .pdb_metadata import parse_pdb_debug_info
        from .pdb_utils import locate_pdb

        pdb_file = locate_pdb(path, pdb_path_override=pdb_path, allow_network=False)
        if pdb_file is not None:
            meta, adv = parse_pdb_debug_info(pdb_file)
            _logger.info("PDB debug info loaded from %s", pdb_file)
            return meta, adv
        _logger.debug("No PDB file found for %s", path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("PDB parsing failed for %s: %s", path, exc)
    return None, None


def _dump_pe(
    path: Path,
    version: str,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
    pdb_path: Path | None = None,
) -> AbiSnapshot:
    """Dump a PE binary (Windows DLL) to an ABI snapshot.

    When *headers* are supplied the ABI surface is scoped to declarations in
    those public headers via castxml (mirroring ``abidw --headers-dir``).  If
    castxml is unavailable or no header declaration matches an exported symbol,
    scoping is skipped (with a warning) and the full export table is used.
    """
    from .pe_metadata import parse_pe_metadata

    try:
        pe_meta = parse_pe_metadata(path)
    except ImportError as exc:
        raise SnapshotError(str(exc)) from exc
    except (RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to parse PE '{path}': {exc}") from exc

    if not pe_meta.machine:
        raise SnapshotError(
            f"Failed to extract PE metadata from '{path}'. "
            "The file may be corrupt or not a valid PE binary."
        )
    if not pe_meta.exports:
        raise ValidationError(
            f"PE file '{path}' has no exports (named or ordinal). "
            "Verify the file is a valid DLL."
        )

    dwarf_meta, dwarf_adv = _extract_pdb_debug(path, pdb_path)

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = _try_header_scoped_dump(
            "pe", path, headers, includes or [], version, lang,
        )
        if scoped is not None:
            # Preserve any PDB debug info alongside the header-scoped surface.
            if dwarf_meta is not None:
                scoped.dwarf = dwarf_meta
                scoped.dwarf_advanced = dwarf_adv
            return scoped

    funcs = [
        Function(
            name=(exp.name or f"ordinal:{exp.ordinal}"),
            mangled=(exp.name or f"ordinal:{exp.ordinal}"),
            return_type="?",
            visibility=Visibility.PUBLIC,
            is_extern_c=not (exp.name or "").startswith("?"),
        )
        for exp in pe_meta.exports
    ]

    # ADR-024 Phase 1 (PDB provenance): when header scoping was requested but
    # castxml could not resolve a surface (commonly the MSVC C++-mangling gap),
    # recover declared types — *with their defining source header* — from the
    # PDB debug info so that --public-header scoping still has a provenance
    # signal to classify against. Bounded to this fallback branch so default
    # PE diffs (no --header) are unaffected.
    pdb_types: list[RecordType] = []
    pdb_enums: list[EnumType] = []
    if headers and dwarf_meta is not None:
        from .pdb_model import model_types_from_dwarf_metadata
        pdb_types, pdb_enums = model_types_from_dwarf_metadata(dwarf_meta)

    return AbiSnapshot(
        library=path.name, version=version,
        functions=funcs, types=pdb_types, enums=pdb_enums, pe=pe_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        platform="pe",
        scope_fallback=scope_fallback,
    )


def _dump_macho(
    path: Path,
    version: str,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
) -> AbiSnapshot:
    """Dump a Mach-O binary (macOS dylib) to an ABI snapshot.

    When *headers* are supplied the ABI surface is scoped to declarations in
    those public headers via castxml; otherwise the full export table is used.
    """
    from .macho_metadata import parse_macho_metadata

    try:
        macho_meta = parse_macho_metadata(path)
    except (RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to parse Mach-O '{path}': {exc}") from exc

    if not macho_meta.exports and not macho_meta.install_name and not macho_meta.dependent_libs:
        raise SnapshotError(
            f"Mach-O file '{path}' has no exports or load-command metadata. "
            "Verify the file is a valid dynamic library."
        )

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = _try_header_scoped_dump(
            "macho", path, headers, includes or [], version, lang,
        )
        if scoped is not None:
            return scoped

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
        scope_fallback=scope_fallback,
    )


# ── Comparison ──────────────────────────────────────────────────────────────


def collect_metadata(path: Path) -> LibraryMetadata | None:
    """Compute SHA-256 and file size for a library artifact.

    Returns *None* when *path* is a text-based snapshot (JSON or Perl dump)
    so that reports don't display misleading metadata for the serialised file.
    """
    text_fmt = sniff_text_format(path)
    if text_fmt in ("json", "perl"):
        return None

    data = path.read_bytes()
    return LibraryMetadata(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def load_suppression_and_policy(
    suppress: Path | None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from paths.

    Raises:
        ValidationError: If the suppression or policy file is invalid.
    """
    from .policy_file import PolicyFile as _PolicyFile
    from .suppression import SuppressionList as _SuppressionList

    suppression: _SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = _SuppressionList.load(suppress)
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid suppression file: {e}") from e

    pf: _PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = _PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise ValidationError(str(e)) from e
        except (ValueError, OSError) as e:
            raise ValidationError(f"Invalid policy file: {e}") from e
        if policy != "strict_abi":
            _logger.warning(
                "--policy=%r is ignored when --policy-file is given. "
                "Set base_policy in the YAML file to override the base policy.",
                policy,
            )
    return suppression, pf


def run_compare(
    old_input: Path,
    new_input: Path,
    old_headers: list[Path] | None = None,
    new_headers: list[Path] | None = None,
    old_includes: list[Path] | None = None,
    new_includes: list[Path] | None = None,
    old_version: str = "",
    new_version: str = "",
    lang: str = "c++",
    suppress: Path | None = None,
    policy: str = "strict_abi",
    policy_file_path: Path | None = None,
    old_pdb_path: Path | None = None,
    new_pdb_path: Path | None = None,
    old_debug_roots: list[Path] | None = None,
    new_debug_roots: list[Path] | None = None,
    enable_debuginfod: bool = False,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """Compare two ABI inputs and return the classified diff result.

    This is the main entry point for programmatic comparison. It handles:
    - Input format detection and snapshot loading
    - Suppression and policy file loading
    - Running the comparison
    - Collecting library metadata

    Returns:
        A tuple of (DiffResult, old_snapshot, new_snapshot).

    Raises:
        SnapshotError: If either input cannot be loaded.
        ValidationError: If inputs have unrecognised formats.
    """
    _old_headers = old_headers or []
    _new_headers = new_headers or []
    _old_includes = old_includes or []
    _new_includes = new_includes or []

    old_fmt = detect_binary_format(old_input)
    new_fmt = detect_binary_format(new_input)

    old = resolve_input(
        old_input, _old_headers, _old_includes, old_version, lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=old_pdb_path,
        debug_roots=old_debug_roots,
        enable_debuginfod=enable_debuginfod,
    )
    new = resolve_input(
        new_input, _new_headers, _new_includes, new_version, lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=new_pdb_path,
        debug_roots=new_debug_roots,
        enable_debuginfod=enable_debuginfod,
    )

    suppression, pf = load_suppression_and_policy(suppress, policy, policy_file_path)
    result = compare(
        old, new, suppression=suppression, policy=policy, policy_file=pf,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
    )
    result.old_metadata = collect_metadata(old_input)
    result.new_metadata = collect_metadata(new_input)
    return result, old, new


# ── Output rendering ────────────────────────────────────────────────────────


def render_output(
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
) -> str:
    """Render comparison result in the requested output format.

    Supported formats: ``'json'``, ``'markdown'``, ``'sarif'``, ``'html'``,
    ``'junit'``.

    Raises:
        ValidationError: For unrecognised output format.
    """
    if stat and fmt != "junit":
        if fmt == "json":
            return to_stat_json(result)
        return to_stat(result)

    if fmt == "json":
        return _render_json_output(
            result, old, new, follow_deps=follow_deps,
            show_only=show_only, report_mode=report_mode,
            show_impact=show_impact, severity_config=severity_config,
        )

    if fmt == "sarif":
        from .sarif import to_sarif_str
        return to_sarif_str(result, show_only=show_only)

    if fmt == "html":
        from .html_report import generate_html_report
        return generate_html_report(
            result,
            lib_name=old.library,
            old_version=old.version,
            new_version=new.version if new else "new",
            old_symbol_count=result.old_symbol_count,
            show_only=show_only,
            show_impact=show_impact,
        )

    if fmt == "junit":
        from .junit_report import to_junit_xml
        return to_junit_xml(
            result, old,
            show_only=show_only, severity_config=severity_config,
        )

    if fmt == "review":
        from .reporter import to_review_digest
        return to_review_digest(result)

    _SUPPORTED_FORMATS = {"json", "sarif", "html", "junit", "markdown", "md", "review"}
    if fmt not in _SUPPORTED_FORMATS:
        raise ValidationError(f"Unsupported output format: {fmt!r} (expected one of {sorted(_SUPPORTED_FORMATS)})")

    # Default: markdown
    md = to_markdown(
        result, show_only=show_only, report_mode=report_mode,
        show_impact=show_impact, severity_config=severity_config,
        show_recommendation=show_recommendation,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        md += _render_deps_section_md(old, new)
    return md


def _render_json_output(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None,
    *,
    follow_deps: bool,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    severity_config: SeverityConfig | None,
) -> str:
    """Render comparison result as JSON, optionally including dependency info."""
    base = to_json(
        result, show_only=show_only, report_mode=report_mode,
        show_impact=show_impact, severity_config=severity_config,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        import json
        from dataclasses import asdict
        d = json.loads(base)
        if old.dependency_info:
            d["old_dependency_info"] = asdict(old.dependency_info)
        if new and new.dependency_info:
            d["new_dependency_info"] = asdict(new.dependency_info)
        return json.dumps(d, indent=2)
    return base


def _render_deps_section_md(old: AbiSnapshot, new: AbiSnapshot | None) -> str:
    """Append dependency summary section to markdown output."""
    lines: list[str] = ["", "## Dependency Analysis", ""]

    for label, snap in [("Old", old), ("New", new)]:
        if snap is None or snap.dependency_info is None:
            continue
        info = snap.dependency_info
        lines.append(f"### {label} version (`{snap.version}`)")
        lines.append("")

        if info.nodes:
            lines.append(f"**Dependencies**: {len(info.nodes)} resolved DSOs")
            for node in info.nodes:
                raw_depth = node.get("depth", 0)
                depth = raw_depth if isinstance(raw_depth, int) else 0
                indent = "  " * depth
                reason = node.get("resolution_reason", "")
                lines.append(f"  {indent}- `{node.get('soname', '?')}` ({reason})")
            lines.append("")

        if info.bindings_summary:
            lines.append("**Bindings**:")
            for status, count in sorted(info.bindings_summary.items()):
                lines.append(f"  - `{status}`: {count}")
            lines.append("")

        if info.unresolved:
            lines.append("**Unresolved libraries**:")
            for u in info.unresolved:
                lines.append(f"  - `{u.get('soname', '?')}` needed by `{u.get('consumer', '?')}`")
            lines.append("")

        if info.missing_symbols:
            lines.append(f"**Missing symbols**: {len(info.missing_symbols)}")
            for ms in info.missing_symbols[:10]:
                ver = f"@{ms['version']}" if ms.get('version') else ""
                lines.append(f"  - `{ms['symbol']}{ver}`")
            if len(info.missing_symbols) > 10:
                lines.append(f"  - ... +{len(info.missing_symbols) - 10} more")
            lines.append("")

    return "\n".join(lines)
