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
from typing import TYPE_CHECKING, Any

from .api_types import CompareRequest, InputSpec
from .checker import compare
from .checker_types import DiffResult, LibraryMetadata
from .errors import AbicheckError, SnapshotError, ValidationError
from .header_utils import deferred_token_dirs, resolve_inferred_header_roots
from .model import AbiSnapshot, EnumType, Function, RecordType, Visibility
from .reporter import to_json, to_markdown, to_stat, to_stat_json
from .serialization import load_snapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from .checker_types import Change
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .environment_matrix import EnvironmentMatrix
    from .policy_file import PolicyFile
    from .severity import SeverityConfig
    from .suppression import SuppressionList

_logger = logging.getLogger(__name__)

# Magic-byte length for format detection
_SNIFF_BYTES = 256


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


def _resolve_symvers(path: Path, version: str) -> AbiSnapshot | None:
    """Parse a Linux kernel ``Module.symvers`` manifest into a snapshot, or None.

    Recognized by filename (``Module.symvers`` / ``*.symvers``) or, for a
    generically-named file, by content (a hex-CRC + ``EXPORT_SYMBOL`` record).
    """
    from .symvers_metadata import looks_like_symvers, parse_symvers

    name = path.name.lower()
    by_name = name == "module.symvers" or name.endswith(".symvers")
    if not by_name:
        # Cheap bounded content sniff before committing to a full decode, so a
        # generically-named non-symvers input (a large JSON snapshot, an archive)
        # on the hot `compare old new` path isn't read+decoded in full here only
        # to be rejected — the caller re-reads it for its real format anyway.
        try:
            with open(path, "rb") as f:
                head = f.read(_SNIFF_BYTES).decode("utf-8", "replace")
        except OSError:
            return None
        if not looks_like_symvers(head):
            return None
    try:
        text = path.read_text("utf-8", "replace")
    except OSError:
        return None
    kabi = parse_symvers(text)
    if not kabi.entries:
        return None
    return AbiSnapshot(library=path.name, version=version, kabi=kabi)


def _typeinfo_functions(func_protos: dict[str, Any]) -> list[Function]:
    """Convert BTF/CTF function prototypes into snapshot Function records.

    BTF/CTF names are C-linkage; the plain name doubles as the symbol key
    (matching the C-function convention used by the header dumpers).
    """
    from .model import Param

    return [
        Function(
            name=name,
            mangled=name,
            return_type=proto.return_type,
            params=[Param(name=p_name, type=p_type) for p_name, p_type in proto.params],
            is_extern_c=True,
        )
        for name, proto in sorted(func_protos.items())
    ]


def _resolve_raw_typeinfo(path: Path, version: str) -> AbiSnapshot | None:
    """Parse a bare BTF or CTF blob into a snapshot, or return None.

    BTF blobs start with magic ``0xEB9F`` and CTF with ``0xCFF1`` (either byte
    order). The parsed type layout is converted to the checker's DWARF-shaped
    metadata so the same struct/enum layout detectors apply.
    """
    from .btf_metadata import BTF_MAGIC, parse_btf_from_bytes
    from .ctf_metadata import CTF_MAGIC, parse_ctf_from_bytes

    try:
        with open(path, "rb") as f:
            head = f.read(2)
    except OSError:
        return None
    if len(head) < 2:
        return None

    # Only detect the little-endian byte order that parse_btf_from_bytes /
    # parse_ctf_from_bytes actually support: a big-endian-target blob (first
    # bytes EB 9F / CF F1) would otherwise enter the branch but parse to empty
    # metadata, silently dropping all type changes. Falling through to the
    # "cannot detect format" error is the honest outcome for those.
    magic_le = int.from_bytes(head, "little")
    data = path.read_bytes()
    try:
        if magic_le == BTF_MAGIC:
            btf = parse_btf_from_bytes(data)
            # Require actual type records, not just a valid header. A
            # truncated/unsupported blob parses to empty metadata, and a
            # header-only blob (valid header, type_len=0) sets has_btf=True with
            # type_count==0; either way, accepting it would yield a silent empty
            # baseline that hides all layout changes. Fall through to the
            # "cannot detect format" error instead.
            if not btf.has_btf or btf.type_count <= 0:
                _logger.warning("raw BTF blob %s has no type records; ignoring", path)
                return None
            return AbiSnapshot(
                library=path.name,
                version=version,
                dwarf=btf.to_dwarf_metadata(),
                # Bridge the full BTF surface: prototypes feed the function
                # detectors (FUNC_PARAMS_CHANGED, ...) and typedef targets feed
                # TYPEDEF_BASE_CHANGED — previously dropped at this boundary.
                functions=_typeinfo_functions(btf.func_protos),
                typedefs=dict(btf.typedefs),
            )
        if magic_le == CTF_MAGIC:
            ctf = parse_ctf_from_bytes(data)
            if not ctf.has_ctf or ctf.type_count <= 0:
                _logger.warning("raw CTF blob %s has no type records; ignoring", path)
                return None
            return AbiSnapshot(
                library=path.name,
                version=version,
                dwarf=ctf.to_dwarf_metadata(),
                functions=_typeinfo_functions(ctf.func_protos),
                typedefs=dict(ctf.typedefs),
            )
    except (ValueError, OSError) as exc:
        _logger.warning("failed to parse raw type-info blob %s: %s", path, exc)
        return None
    return None


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
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    follow_linker_scripts: bool = True,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Auto-detect input type and return an ABI snapshot.

    This is the single source of truth for turning a path into an
    :class:`AbiSnapshot`; the CLI (:func:`abicheck.cli_resolve._resolve_input`)
    and the MCP server are thin wrappers that translate the framework-free
    errors raised here into their own contracts.

    Detection order:

    1. Native binary (ELF / PE / Mach-O, detected by magic bytes)
    2. Raw BTF/CTF type-info blob
    3. ABICC Perl dump (``$VAR1`` prefix) → :func:`import_abicc_perl_dump`
    4. JSON snapshot (``{`` prefix) → :func:`load_snapshot`
    5. GNU ld linker script (``INPUT()``/``GROUP()``) → follow to its target

    Args:
        debug_format: Force the ELF debug format ("dwarf", "btf", "ctf") or
            *None* for auto-detection.
        public_headers / public_header_dirs: Public-header sets used to tag
            declaration provenance on PE/Mach-O snapshots (ADR-024 Phase 1).
        follow_linker_scripts: When True (default), a GNU ld linker script is
            followed to the shared library named in its ``INPUT()``/``GROUP()``
            directive.
        notify: Optional callback for user-facing progress notes (e.g. "following
            a linker script", "no headers provided"). When *None*, such notes go
            to the module logger. The CLI passes a ``click.echo(..., err=True)``
            wrapper so its stderr output is unchanged.

    Raises:
        SnapshotError: If the snapshot cannot be loaded from the input.
        ValidationError: If the input format cannot be detected.
    """
    _headers = headers or []
    _includes = includes or []

    # Fast path: caller already knows it's ELF
    if is_elf is True:
        return run_dump(
            path,
            "elf",
            _headers,
            _includes,
            version,
            lang,
            dwarf_only=dwarf_only,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            header_backend=header_backend,
            compile=compile,
            notify=notify,
        )

    # Detect binary format from magic bytes
    binary_fmt = detect_binary_format(path) if is_elf is None else None
    if binary_fmt is not None:
        return run_dump(
            path,
            binary_fmt,
            _headers,
            _includes,
            version,
            lang,
            pdb_path=pdb_path,
            dwarf_only=dwarf_only,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            header_backend=header_backend,
            compile=compile,
            notify=notify,
        )

    # Raw kernel type-info blobs (a bare `.BTF` / CTF section extracted with
    # `bpftool btf dump ... format raw` or `objcopy -O binary --only-section`).
    # A real kernel carries BTF inside an ELF `.BTF` section, but the bare blob
    # is a convenient, toolchain-free comparison input.
    raw_typeinfo = _resolve_raw_typeinfo(path, version)
    if raw_typeinfo is not None:
        return raw_typeinfo

    # Linux kernel Module.symvers (kABI manifest) — a tab-separated text file,
    # recognized by filename or content (G23-D1).
    kabi_snap = _resolve_symvers(path, version)
    if kabi_snap is not None:
        return kabi_snap

    # Text-based formats
    fmt = sniff_text_format(path)

    if fmt == "perl":
        from .compat.abicc_dump_import import import_abicc_perl_dump

        try:
            return import_abicc_perl_dump(path)
        except (
            ValueError,
            KeyError,
            UnicodeDecodeError,
            OSError,
            AbicheckError,
        ) as exc:
            raise SnapshotError(
                f"Failed to import ABICC Perl dump '{path}': {exc}"
            ) from exc

    if fmt == "json":
        try:
            return load_snapshot(path)
        except (ValueError, KeyError, UnicodeDecodeError, OSError) as exc:
            raise SnapshotError(
                f"Failed to load JSON snapshot '{path}': {exc}"
            ) from exc

    # GNU ld linker script (e.g. the ``libfoo.so`` dev symlink is the text
    # ``INPUT(libfoo.so.1)``): follow it to the real shared library.
    if follow_linker_scripts:
        from .binary_utils import resolve_linker_script

        target, is_ld_script = resolve_linker_script(path)
        if is_ld_script:
            if target is not None and target.resolve() != path.resolve():
                _emit(
                    notify,
                    f"Note: '{path}' is a GNU ld linker script; following its "
                    f"INPUT()/GROUP() directive to '{target}'.",
                )
                return resolve_input(
                    target,
                    _headers,
                    _includes,
                    version,
                    lang,
                    dwarf_only=dwarf_only,
                    debug_roots=debug_roots,
                    enable_debuginfod=enable_debuginfod,
                    debug_format=debug_format,
                    symbols_only=symbols_only,
                    debug_presence_only=debug_presence_only,
                    public_headers=public_headers,
                    public_header_dirs=public_header_dirs,
                    follow_linker_scripts=follow_linker_scripts,
                    header_backend=header_backend,
                    compile=compile,
                    notify=notify,
                )
            raise ValidationError(
                f"'{path}' is a GNU ld linker script (INPUT/GROUP), not a binary, "
                "and its target could not be located next to it. Pass the actual "
                "shared library named in its INPUT(...) directive directly."
            )

    # Static / import libraries (`.a`, `.lib`) are member archives, not single
    # linkable images. abicheck does not analyse archives (by design — see
    # docs/concepts/limitations.md); fail with actionable guidance rather than a
    # generic "unknown format" error.
    from .binary_utils import detect_archive

    if detect_archive(path):
        raise ValidationError(
            f"'{path}' is a static/import library archive (.a/.lib), which abicheck "
            "does not analyse — it compares single linkable images (shared libraries "
            "and objects). Extract the members (e.g. `ar x lib.a`) and compare the "
            "resulting object files or the shared library built from them instead."
        )

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
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Extract an ABI snapshot from a native binary (ELF, PE, or Mach-O).

    ``public_headers`` / ``public_header_dirs`` tag declaration provenance
    (ADR-024 Phase 1) on all three formats: ELF threads them into
    :func:`dumper.dump` (which runs ``apply_provenance``), PE/Mach-O apply them
    via :func:`_apply_native_provenance`. A no-op when no header set is supplied.
    ``debug_format`` forces the ELF debug format. ``notify`` receives
    user-facing progress notes (see :func:`resolve_input`).

    Raises:
        SnapshotError: If the binary cannot be parsed.
        ValidationError: For invalid arguments (missing exports, bad include dirs).
    """
    _headers = headers or []
    _includes = includes or []
    # An explicit --ast-frontend on the compile context wins over the bare
    # header_backend arg (the latter is the compare-path default carrier).
    eff_backend = (
        compile.frontend
        if (compile is not None and compile.frontend != "auto")
        else header_backend
    )

    if binary_fmt == "elf":
        snap = _dump_elf(
            path,
            _headers,
            _includes,
            version,
            lang,
            dwarf_only=dwarf_only,
            debug_roots=debug_roots,
            enable_debuginfod=enable_debuginfod,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            header_backend=eff_backend,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            notify=notify,
        )
        _try_attach_sycl_metadata(snap, path)
        _try_attach_python_ext_metadata(snap)
        _try_attach_python_api_surface(snap)
        return snap
    if binary_fmt == "pe":
        snap = _dump_pe(
            path,
            version,
            headers=_headers,
            includes=_includes,
            lang=lang,
            pdb_path=pdb_path,
            header_backend=eff_backend,
            compile=compile,
        )
        snap = _apply_native_provenance(snap, public_headers, public_header_dirs)
        _try_attach_python_ext_metadata(snap)
        _try_attach_python_api_surface(snap)
        return snap
    if binary_fmt == "macho":
        snap = _dump_macho(
            path,
            version,
            headers=_headers,
            includes=_includes,
            header_backend=eff_backend,
            lang=lang,
            compile=compile,
        )
        snap = _apply_native_provenance(snap, public_headers, public_header_dirs)
        _try_attach_python_ext_metadata(snap)
        _try_attach_python_api_surface(snap)
        return snap
    raise ValidationError(f"Unsupported binary format: {binary_fmt}")


def _apply_native_provenance(
    snap: AbiSnapshot,
    public_headers: list[Path] | None,
    public_header_dirs: list[Path] | None,
) -> AbiSnapshot:
    """Tag declaration provenance on a PE/Mach-O snapshot (ADR-024 Phase 1).

    Mirrors the ELF path (``dumper.create_snapshot``), which always runs
    ``apply_provenance``. A no-op when no public-header set is supplied — every
    origin stays ``UNKNOWN`` and behaviour is unchanged.
    """
    from .provenance import apply_provenance

    return apply_provenance(snap, public_headers, public_header_dirs)


def _emit(notify: Callable[[str], None] | None, message: str) -> None:
    """Send a user-facing progress note to *notify*, or the logger if unset."""
    if notify is not None:
        notify(message)
    else:
        _logger.warning(message)


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


def _try_attach_python_ext_metadata(snap: AbiSnapshot) -> None:
    """Recognise a CPython extension module and attach its metadata (G14).

    Cheap and side-effect-free: inspects the snapshot's already-parsed export
    and import tables (plus the filename SOABI tag) for a ``PyInit_*`` export or
    ``Py*`` imports. A plain C/C++ library has neither, so ``python_ext`` stays
    ``None`` and nothing downstream changes.
    """
    from .python_ext import detect_python_extension

    try:
        python_ext = detect_python_extension(snap)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Python extension detection skipped: %s", exc)
        return
    if python_ext is not None:
        snap.python_ext = python_ext
        _logger.info(
            "CPython extension detected: module=%s, abi3=%s, %d CPython import(s)",
            python_ext.module_name,
            python_ext.limited_api,
            len(python_ext.cpython_imports),
        )


def _try_attach_python_api_surface(snap: AbiSnapshot) -> None:
    """Recover an extension module's Python-visible API surface (G23).

    Looks for a ``.pyi`` type stub alongside the snapshot's ``source_path`` and,
    if found, statically parses the top-level functions/classes/methods and
    their signatures into ``python_api``. Never imports or executes the module.
    A no-op (leaves ``python_api`` as ``None``) when no stub is present — the
    common case for a plain C/C++ library or a stubless extension.
    """
    from .python_api import detect_python_api

    try:
        python_api = detect_python_api(snap)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Python API surface recovery skipped: %s", exc)
        return
    if python_api is not None:
        snap.python_api = python_api
        _logger.info(
            "Python API surface recovered: module=%s, %d function(s), %d class(es)",
            python_api.module_name,
            len(python_api.functions),
            len(python_api.classes),
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
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Dump an ELF binary to an ABI snapshot.

    ``public_headers`` / ``public_header_dirs`` classify declaration provenance
    (ADR-024). They are threaded into :func:`dumper.dump`, which runs
    ``apply_provenance`` over the parsed surface — the same call the ``dump`` CLI
    makes (``cli_dump_helpers._run_elf_dump``). Without this thread-through the
    ELF service path leaves every origin ``UNKNOWN``, silently disabling the
    provenance-gated cross-checks on the ``scan`` entry point.
    """
    from .dumper import dump

    cc = compile if compile is not None else CompileContext()
    resolved_headers = expand_header_inputs(headers) if headers else []
    if not resolved_headers and symbols_only:
        _emit(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Using exported symbols only for binary-depth scan.",
        )
    elif not resolved_headers and not dwarf_only:
        _emit(
            notify,
            f"Warning: '{path}' — no headers provided. "
            "Will use DWARF debug info if available, else symbols-only mode.",
        )
    if resolved_headers and not dwarf_only:
        for inc in includes:
            if not inc.exists() or not inc.is_dir():
                raise ValidationError(
                    f"Include directory not found or not a directory: {inc}"
                )
    elif includes and not dwarf_only:
        _emit(notify, "Warning: --include paths are ignored without headers.")

    # P3: auto-add the public-header roots to the search path. Same bucket
    # selection as the dump CLI path (resolve_inferred_header_roots): plain -I
    # when this request carries no compile-context includes, or -isystem (below
    # the build-context dirs, above the standard system dirs) when the caller's
    # CompileContext supplies its own includes via gcc_options/tokens (e.g.
    # -isystem build/generated) — so a real build context keeps search priority
    # without dropping the inferred root below system headers (Codex review).
    eff_includes = list(includes)
    eff_tokens: tuple[str, ...] = cc.gcc_option_tokens
    deferred_dirs: tuple[Path, ...] = ()
    if resolved_headers and not dwarf_only:
        inc_extra, deferred = resolve_inferred_header_roots(
            headers,
            list(includes),
            gcc_options=cc.gcc_options,
            gcc_option_tokens=cc.gcc_option_tokens,
        )
        eff_includes += inc_extra
        eff_tokens = cc.gcc_option_tokens + tuple(deferred)
        # Deferred roots ride in gcc_option_tokens (-isystem), not extra_includes,
        # so hash their contents into the AST cache key explicitly (Codex review).
        deferred_dirs = tuple(deferred_token_dirs(deferred))

    compiler = "cc" if lang == "c" else "c++"
    try:
        return dump(
            so_path=path,
            headers=resolved_headers,
            extra_includes=eff_includes,
            version=version,
            compiler=compiler,
            gcc_path=cc.gcc_path,
            gcc_prefix=cc.gcc_prefix,
            gcc_options=cc.gcc_options,
            gcc_option_tokens=eff_tokens,
            sysroot=cc.sysroot,
            nostdinc=cc.nostdinc,
            lang=lang if lang == "c" else None,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            header_backend=header_backend,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            extra_hash_dirs=deferred_dirs,
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
    header_backend: str = "auto",
    compile: CompileContext | None = None,
) -> tuple[AbiSnapshot | None, str | None]:
    """Attempt a header-scoped dump for a PE/Mach-O binary.

    Returns ``(snapshot, None)`` when the selected header backend is available
    *and* at least one declared symbol matched the export table.  Returns
    ``(None, reason)`` (after emitting a ``UserWarning``) when scoping is
    unavailable or had no effect, so the caller can fall back to export-table
    mode and record the structured confidence signal (ADR-024 §D5.3).
    ``reason`` is one of ``"header-backend-unavailable"`` /
    ``"mangling-fallback"``.  This mirrors the public-API scoping that
    ``abidw --headers-dir`` / abi-dumper apply for ELF.
    """
    from .dumper import _dump_macho as _dumper_macho, _dump_pe as _dumper_pe

    # Expand header directories into individual files (same as the ELF path),
    # so `--header <dir>` scopes correctly instead of feeding a directory to
    # castxml's `#include`. Done *outside* the broad except below so a genuinely
    # bad/empty header path raises a clear ValidationError rather than silently
    # falling back to the full export table.
    resolved_headers = expand_header_inputs(headers)

    compiler = "cc" if lang.lower() == "c" else "c++"
    lang_arg = lang if lang.lower() == "c" else None
    cc = compile if compile is not None else CompileContext()
    # P3 parity with the ELF path: auto-add the inferred public-header roots so a
    # -H umbrella resolves its own relative includes without a separate -I on
    # PE/Mach-O too (else header parsing fails and we drop to export-table mode,
    # losing the L2/type surface). Same bucket selection — plain -I with no build
    # context, deferred -isystem otherwise — and the deferred dirs are hashed
    # into the AST cache key (Codex review).
    eff_includes = list(includes)
    eff_tokens = cc.gcc_option_tokens
    deferred_dirs: tuple[Path, ...] = ()
    if resolved_headers:
        inc_extra, deferred = resolve_inferred_header_roots(
            headers,
            list(includes),
            gcc_options=cc.gcc_options,
            gcc_option_tokens=cc.gcc_option_tokens,
        )
        eff_includes += inc_extra
        eff_tokens = cc.gcc_option_tokens + tuple(deferred)
        deferred_dirs = tuple(deferred_token_dirs(deferred))
    try:
        if fmt == "pe":
            snap = _dumper_pe(
                path,
                resolved_headers,
                eff_includes,
                version,
                compiler,
                gcc_path=cc.gcc_path,
                gcc_prefix=cc.gcc_prefix,
                gcc_options=cc.gcc_options,
                gcc_option_tokens=eff_tokens,
                sysroot=cc.sysroot,
                nostdinc=cc.nostdinc,
                lang=lang_arg,
                header_backend=header_backend,
                extra_hash_dirs=deferred_dirs,
            )
        else:
            snap = _dumper_macho(
                path,
                resolved_headers,
                eff_includes,
                version,
                compiler,
                gcc_path=cc.gcc_path,
                gcc_prefix=cc.gcc_prefix,
                gcc_options=cc.gcc_options,
                gcc_option_tokens=eff_tokens,
                sysroot=cc.sysroot,
                nostdinc=cc.nostdinc,
                lang=lang_arg,
                header_backend=header_backend,
                extra_hash_dirs=deferred_dirs,
            )
    except Exception as exc:  # noqa: BLE001 — header backend/parse failure → fall back
        warnings.warn(
            f"Header-based ABI scoping unavailable for '{path.name}' "
            f"({fmt.upper()}): {exc}. Falling back to export-table mode — "
            f"--header/--include were ignored.",
            UserWarning,
            stacklevel=2,
        )
        return None, "header-backend-unavailable"

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
    header_backend: str = "auto",
    compile: CompileContext | None = None,
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
            "pe",
            path,
            headers,
            includes or [],
            version,
            lang,
            header_backend=header_backend,
            compile=compile,
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
        library=path.name,
        version=version,
        functions=funcs,
        types=pdb_types,
        enums=pdb_enums,
        pe=pe_meta,
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
    header_backend: str = "auto",
    compile: CompileContext | None = None,
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

    if (
        not macho_meta.exports
        and not macho_meta.install_name
        and not macho_meta.dependent_libs
    ):
        raise SnapshotError(
            f"Mach-O file '{path}' has no exports or load-command metadata. "
            "Verify the file is a valid dynamic library."
        )

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = _try_header_scoped_dump(
            "macho",
            path,
            headers,
            includes or [],
            version,
            lang,
            header_backend=header_backend,
            compile=compile,
        )
        if scoped is not None:
            return scoped

    funcs = [
        Function(
            name=exp.name,
            mangled=exp.name,
            return_type="?",
            visibility=Visibility.PUBLIC,
            is_extern_c=not exp.name.startswith("_Z"),
        )
        for exp in macho_meta.exports
        if exp.name
    ]
    return AbiSnapshot(
        library=path.name,
        version=version,
        functions=funcs,
        macho=macho_meta,
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


def load_env_matrix(path: Path | None) -> EnvironmentMatrix | None:
    """Load an ADR-020b environment-matrix YAML, or None when *path* is None.

    Tier-2 loader (mirrors :func:`load_suppression_and_policy`): parse/shape
    errors surface as :class:`ValidationError` with identical text across
    front-ends.
    """
    if path is None:
        return None
    from .environment_matrix import EnvironmentMatrix

    try:
        # from_yaml converts malformed YAML to ValueError, so no yaml import
        # is needed here (abicheck.service has no import-untyped override).
        return EnvironmentMatrix.from_yaml(Path(path))
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid environment matrix {path}: {e}") from e
    except OSError as e:
        raise ValidationError(f"Cannot read environment matrix {path}: {e}") from e


def compare_snapshots(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None = None,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    extra_changes: list[Change] | None = None,
    pattern_verdicts: bool = False,
    surface_metrics: bool = False,
    collapse_versioned_symbols: bool = False,
    public_surface_allowlist: set[str] | None = None,
    reconcile_build_context: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
) -> DiffResult:
    """Classify two already-resolved snapshots — the Tier-2 snapshot verb.

    Thin wrapper over the Tier-1 core (:func:`abicheck.checker.compare`) so that
    *front-ends never call the core directly* (ADR-037 D1/D10.1). Front-ends
    that have already resolved their own snapshots (the native ``compare``
    command with embedded build-source evidence, ``scan``, ``appcompat``) route
    through here instead of importing ``checker.compare``; the kwargs mirror the
    core verb exactly so no capability is lost.
    """
    # Centralized POST removed-wrapper recovery: when a committed-surface
    # allowlist is supplied, union the wrappers present in *old* but gone from
    # *new* (contract_scope_allowlist's snapshot half) so a dropped/hidden/
    # non-default-demoted committed wrapper — absent from a *new* manifest — stays
    # in-surface. Every scope caller (CLI, run_compare_request, direct API) routes
    # through here, so recovery happens once and uniformly; it is a no-op when the
    # allowlist/binaries carry no `pp_*` removals (safe for scan/appcompat, which
    # never set the allowlist). Idempotent if the caller already unioned it.
    if public_surface_allowlist is not None:
        from .post_manifest import removed_contract_symbols

        public_surface_allowlist = (
            set(public_surface_allowlist) | removed_contract_symbols(old, new)
        )
    return compare(
        old,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        extra_changes=extra_changes,
        pattern_verdicts=pattern_verdicts,
        surface_metrics=surface_metrics,
        collapse_versioned_symbols=collapse_versioned_symbols,
        public_surface_allowlist=public_surface_allowlist,
        reconcile_build_context=reconcile_build_context,
        env_matrix=env_matrix,
    )


def run_compare_request(
    request: CompareRequest,
) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """Compare two ABI inputs described by a :class:`CompareRequest`.

    The single classification chokepoint (ADR-037 D1/D2): every front-end builds
    a ``CompareRequest`` and calls this, so defaults cannot diverge between
    invocation paths. The legacy keyword-argument :func:`run_compare` is a thin
    shim that builds the request and delegates here.

    Returns:
        A tuple of (DiffResult, old_snapshot, new_snapshot).

    Raises:
        ValidationError: If the request fails :meth:`CompareRequest.validate`.
        SnapshotError: If either input cannot be loaded.
    """
    request.validate()
    # ``validate()`` accepts the language case-insensitively, but the ELF dump
    # path does case-sensitive ``lang == "c"`` checks — normalise so an accepted
    # ``"C"`` is not silently treated as C++.
    lang = request.lang.lower()
    # The artifact resolve path uses the header-AST frontend; an ``android``
    # selection (source-ABI only, gated to has_sources by validate) has no
    # header-AST path, so fall back to ``auto`` for the binary dump.
    from .api_types import HEADER_AST_FRONTENDS

    header_backend = (
        request.frontend if request.frontend.lower() in HEADER_AST_FRONTENDS else "auto"
    )

    old_fmt = detect_binary_format(request.old.path)
    new_fmt = detect_binary_format(request.new.path)

    old = resolve_input(
        request.old.path,
        list(request.old.headers),
        list(request.old.includes),
        request.old.version,
        lang,
        is_elf=True if old_fmt == "elf" else None,
        pdb_path=request.old.pdb,
        debug_roots=list(request.old.debug_roots) or None,
        enable_debuginfod=request.enable_debuginfod,
        header_backend=header_backend,
    )
    new = resolve_input(
        request.new.path,
        list(request.new.headers),
        list(request.new.includes),
        request.new.version,
        lang,
        is_elf=True if new_fmt == "elf" else None,
        pdb_path=request.new.pdb,
        debug_roots=list(request.new.debug_roots) or None,
        enable_debuginfod=request.enable_debuginfod,
        header_backend=header_backend,
    )

    suppression, pf = load_suppression_and_policy(
        request.suppress, request.policy, request.policy_file_path
    )
    result = compare_snapshots(
        old,
        new,
        suppression=suppression,
        policy=request.policy,
        policy_file=pf,
        scope_to_public_surface=request.scope_public,
        force_public_symbols=(
            set(request.force_public_symbols) if request.force_public_symbols else None
        ),
        public_surface_allowlist=(
            set(request.public_surface_allowlist)
            if request.public_surface_allowlist is not None
            else None
        ),
        pattern_verdicts=request.pattern_verdicts,
        reconcile_build_context=request.reconcile_build_context,
        env_matrix=load_env_matrix(request.env_matrix_path),
    )
    result.old_metadata = collect_metadata(request.old.path)
    result.new_metadata = collect_metadata(request.new.path)
    return result, old, new


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
    frontend: str = "auto",
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
    pattern_verdicts: bool = False,
    public_surface_allowlist: set[str] | None = None,
) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """Compare two ABI inputs and return the classified diff result.

    Keyword-argument shim over :func:`run_compare_request`: it assembles a
    :class:`CompareRequest` from loose arguments and delegates, so existing
    callers keep working while the typed request is the real chokepoint
    (ADR-037 D2). New callers should build a ``CompareRequest`` directly.

    Returns:
        A tuple of (DiffResult, old_snapshot, new_snapshot).

    Raises:
        SnapshotError: If either input cannot be loaded.
        ValidationError: If inputs have unrecognised formats.
    """
    request = CompareRequest(
        old=InputSpec(
            path=old_input,
            headers=tuple(old_headers or ()),
            includes=tuple(old_includes or ()),
            version=old_version,
            pdb=old_pdb_path,
            debug_roots=tuple(old_debug_roots or ()),
        ),
        new=InputSpec(
            path=new_input,
            headers=tuple(new_headers or ()),
            includes=tuple(new_includes or ()),
            version=new_version,
            pdb=new_pdb_path,
            debug_roots=tuple(new_debug_roots or ()),
        ),
        lang=lang,
        frontend=frontend,
        policy=policy,
        policy_file_path=policy_file_path,
        suppress=suppress,
        scope_public=scope_to_public_surface,
        force_public_symbols=(
            frozenset(force_public_symbols) if force_public_symbols else None
        ),
        public_surface_allowlist=(
            frozenset(public_surface_allowlist)
            if public_surface_allowlist is not None
            else None
        ),
        pattern_verdicts=pattern_verdicts,
        enable_debuginfod=enable_debuginfod,
    )
    return run_compare_request(request)


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
    demangle: bool = False,
) -> str:
    """Render comparison result in the requested output format.

    Supported formats: ``'json'``, ``'markdown'``, ``'sarif'``, ``'html'``,
    ``'junit'``.

    ``demangle`` only affects human-facing formats (markdown, review); machine
    formats (json/sarif/junit) always keep raw mangled symbols so downstream
    tooling can match on them.

    Raises:
        ValidationError: For unrecognised output format.
    """
    if stat and fmt != "junit":
        if fmt == "json":
            return to_stat_json(result)
        return to_stat(result)

    if fmt == "json":
        return _render_json_output(
            result,
            old,
            new,
            follow_deps=follow_deps,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
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
            result,
            old,
            show_only=show_only,
            severity_config=severity_config,
        )

    if fmt == "review":
        from .reporter import to_review_digest

        txt = to_review_digest(result)
        if demangle:
            from .demangle import demangle_text

            txt = demangle_text(txt)
        return txt

    _SUPPORTED_FORMATS = {"json", "sarif", "html", "junit", "markdown", "md", "review"}
    if fmt not in _SUPPORTED_FORMATS:
        raise ValidationError(
            f"Unsupported output format: {fmt!r} (expected one of {sorted(_SUPPORTED_FORMATS)})"
        )

    # Default: markdown
    md = to_markdown(
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
        show_recommendation=show_recommendation,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        md += _render_deps_section_md(old, new)
    if demangle:
        from .demangle import demangle_text

        md = demangle_text(md)
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
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
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


# ── Scan service (extracted to a leaf module) ────────────────────────────────
#
# The ADR-035 D10 typed scan engine (ScanRequest → ScanResult / [CostEstimate])
# lives in ``service_scan`` so this module stays under the AI-readiness size cap.
# Re-exported here verbatim so the public Python API — ``from abicheck.service
# import ScanRequest`` etc. — is unchanged. ``service_scan`` is a leaf: it does
# not import this module at load time.
from .service_scan import (  # noqa: E402,F401
    _HEADER_EXTS,
    Budget,
    CompileContext,
    CostEstimate,
    LayerResult,
    ScanRequest,
    ScanResult,
    _count_compile_db_tus,
    _count_pack_tus,
    _count_source_tus,
    _discover_compile_db,
    _is_header_path,
    _is_source_tu_path,
    _kill_process_tree,
    _layers_from_coverage,
    _scan_imports,
    _scan_subprocess_worker,
    estimate_scan,
    expand_header_inputs,
    run_audit,
    run_scan,
    run_scan_subprocess,
)

# Explicit re-export (mypy strict / no_implicit_reexport): the scan engine moved
# to the leaf module ``service_scan`` but its public names must still resolve as
# ``from abicheck.service import ...``.
__all__ = [
    "Budget",
    "CompareRequest",
    "CompileContext",
    "CostEstimate",
    "InputSpec",
    "LayerResult",
    "ScanRequest",
    "ScanResult",
    "collect_metadata",
    "compare_snapshots",
    "detect_binary_format",
    "estimate_scan",
    "expand_header_inputs",
    "load_suppression_and_policy",
    "render_output",
    "resolve_input",
    "run_audit",
    "run_compare",
    "run_compare_request",
    "run_dump",
    "run_scan",
    "run_scan_subprocess",
    "sniff_text_format",
]


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
                lines.append(
                    f"  - `{u.get('soname', '?')}` needed by `{u.get('consumer', '?')}`"
                )
            lines.append("")

        if info.missing_symbols:
            lines.append(f"**Missing symbols**: {len(info.missing_symbols)}")
            for ms in info.missing_symbols[:10]:
                ver = f"@{ms['version']}" if ms.get("version") else ""
                lines.append(f"  - `{ms['symbol']}{ver}`")
            if len(info.missing_symbols) > 10:
                lines.append(f"  - ... +{len(info.missing_symbols) - 10} more")
            lines.append("")

    return "\n".join(lines)
