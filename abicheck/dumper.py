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

"""Dump headers and binaries with recorded AST toolchain provenance."""

from __future__ import annotations

import logging
import shutil as shutil  # noqa: F401  # legacy test patch target
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .dump_manifest import DumpManifest
    from .dwarf_unified import DwarfSession


from .dumper_ast_config import (
    _CPP_ONLY_PATTERNS as _CPP_ONLY_PATTERNS,
    _build_castxml_command as _build_castxml_command,
    _build_clang_header_command as _build_clang_header_command,
    _cache_key as _cache_key,
    _detect_cpp_headers as _detect_cpp_headers,
    _resolve_compiler_binary as _resolve_compiler_binary,
)
from .dumper_ast_config_cpp20 import _detect_cpp20_headers as _detect_cpp20_headers
from .dumper_castxml import (
    _CastxmlParser as _CastxmlParser,
    _parse_vtable_index as _parse_vtable_index,
    _vt_sort_key as _vt_sort_key,
)
from .dumper_clang import (
    _clang_available as _clang_available,
    _ClangAstParser as _ClangAstParser,
    _is_cl_style_driver_name as _is_cl_style_driver_name,
    _is_default_clang_bin as _is_default_clang_bin,
    _is_dpcpp_family_binary as _is_dpcpp_family_binary,
    _needs_sycl_host_only as _needs_sycl_host_only,
    _resolve_clang_bin as _resolve_clang_bin,
)
from .dumper_clang_errors import (
    _is_direct_include_guard_failure,
)
from .dumper_contract import (
    # ADR-050 D1 extraction-contract attachment lives in the sibling module
    # (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._attach_extraction_contract`` remains a valid bare-name call
    _attach_extraction_contract as _attach_extraction_contract,
)
from .dumper_debug import (
    # DWARF/BTF/CTF format resolution + the kernel-binary heuristic live in the
    # sibling module (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._is_kernel_binary`` / ``dumper._resolve_debug_metadata`` remain
    # valid bare-name calls in ``_dump_elf`` and test patch targets.
    _is_kernel_binary as _is_kernel_binary,
    _resolve_debug_metadata as _resolve_debug_metadata,
)
from .dumper_elf_symbols import (
    # ELF visibility/symbol-classification helpers live in the sibling module
    # (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._elf_classify_symbols``/``dumper._populate_elf_visibility``/
    # ``dumper._pyelftools_exported_symbols`` remain valid bare-name calls in
    # ``_dump_elf``/``_try_dwarf_snapshot``/``_build_symbol_only_snapshot``
    # (and in the Mach-O/PE paths) and existing test patch targets. Because
    # every caller still lives in ``dumper``, a bare-name call resolves through
    # this module's namespace at call time, so ``monkeypatch.setattr(dumper,
    # "_pyelftools_exported_symbols", ...)`` keeps taking effect.
    _ELF_VIS_MAP as _ELF_VIS_MAP,
    _HIDDEN_VIS as _HIDDEN_VIS,
    _elf_classify_symbols as _elf_classify_symbols,
    _is_abi_relevant_symbol as _is_abi_relevant_symbol,
    _populate_elf_visibility as _populate_elf_visibility,
    _pyelftools_exported_symbols as _pyelftools_exported_symbols,
)
from .dumper_layout_backfill import (
    backfill_dwarf_layout,
    dwarf_layout_types_or_empty,
    resolve_snapshot_layout_coherence,
)
from .errors import (
    SnapshotError,
    UnsupportedCastxmlVersionError,
    ValidationError,
)
from .extract.export_symbol_identity import (
    itanium_export_function as _itanium_export_function,
    itanium_export_variable as _itanium_export_variable,
    msvc_export_function as _msvc_export_function,
)
from .extract.export_table_read import finish_binary_snapshot
from .extract.header_ast_backend import (
    HEADER_BACKENDS as HEADER_BACKENDS,
    _is_hybrid_request,
    _resolve_effective_ast_backend as _resolve_effective_ast_backend,
    _resolve_header_backend as _resolve_header_backend,
    _resolve_single_ast_backend as _resolve_single_ast_backend,
    lang_to_profile,
)
from .extract.header_ast_fields import parse_header_ast_fields
from .extract.headers.backend import HeaderAstBackend, HeaderParseRequest
from .extract.headers.castxml.backend import CastxmlBackend, CastxmlRunError
from .extract.headers.castxml.probe import (
    _castxml_cpp_retry_allowed as _castxml_cpp_retry_allowed,
    _castxml_failure_hint as _castxml_failure_hint,
    _castxml_version_note as _castxml_version_note,
    _is_toolchain_version_failure as _is_toolchain_version_failure,
    _parse_castxml_version as _parse_castxml_version,
    _validate_castxml_output as _validate_castxml_output,
)
from .extract.headers.clang.backend import ClangBackend
from .extract.headers.sysinc import (
    _auto_system_includes_enabled as _auto_system_includes_enabled,
    _parse_gnu_include_search_dirs as _parse_gnu_include_search_dirs,
    _probe_gnu_system_includes as _probe_gnu_system_includes,
    _resolve_clang_system_includes as _resolve_clang_system_includes,
    _resolve_probe_compiler as _resolve_probe_compiler,
)
from .extract.headers.toolchain import (
    _allow_unsupported_castxml_enabled as _allow_unsupported_castxml_enabled,
    _ast_compile_provenance as _ast_compile_provenance,
    _ast_fallback_enabled as _ast_fallback_enabled,
    _auto_ast_fallback_eligible as _auto_ast_fallback_eligible,
    _configured_target_triple as _configured_target_triple,
    _cplusplus_macro_for_standard as _cplusplus_macro_for_standard,
    _parser_ast_fallback_reason as _parser_ast_fallback_reason,
    _parser_ast_supported as _parser_ast_supported,
    _parser_ast_toolchain as _parser_ast_toolchain,
    _parser_ast_unsupported_reasons as _parser_ast_unsupported_reasons,
    _parser_frontend_context_kind as _parser_frontend_context_kind,
    _resolve_force_cpp as _resolve_force_cpp,
    _resolve_selected_tool as _resolve_selected_tool,
    _resolve_standard_provenance as _resolve_standard_provenance,
    _safe_mtime as _safe_mtime,
    _safe_size as _safe_size,
    _stamp_ast_parser as _stamp_ast_parser,
    _tool_identity as _tool_identity,
    _tool_identity_metadata as _tool_identity_metadata,
)
from .extract.path_aliases import absolutize_include_roots
from .extract.progress import timed
from .model import AbiSnapshot, RecordType
from .storage.ast_cache_location import reference_scratch_scoped
from .storage.header_ast_cache import (
    _cache_path as _cache_path,
)
from .workflows.dump.elf_fallback import (
    # DWARF/symbol-only fallback snapshot builders live in the sibling module
    # (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._try_dwarf_snapshot``/``dumper._build_symbol_only_snapshot``
    # remain valid bare-name calls in ``_dump_elf`` and existing test patch
    # targets (``patch.object(dumper, "_try_dwarf_snapshot", ...)``).
    _build_symbol_only_snapshot as _build_symbol_only_snapshot,
    _try_dwarf_snapshot as _try_dwarf_snapshot,
)
from .workflows.snapshot_factory import new_snapshot

log = logging.getLogger(__name__)

#: The header-AST backends ``_header_ast_parser`` dispatches through, by
#: name. Replace an entry to substitute a backend (a test's fake runner:
#: ``ClangBackend(runner=...)``) instead of patching a module name.
HEADER_AST_BACKENDS: dict[str, HeaderAstBackend] = {
    "castxml": CastxmlBackend(),
    "clang": ClangBackend(),
}


def _castxml_fallback_reason(
    exc: SnapshotError,
    *,
    auto_selected: bool,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
) -> str | None:
    """Decide whether a failed castxml dump may fall back to the clang backend.

    Returns the ``fallback_reason`` to stamp on the clang parser, or ``None``
    when the caller must re-raise *exc* unchanged. Raises an annotated copy of
    *exc* when the failure *is* fallback-eligible but the opt-in is off, so the
    user is told why the fallback did not happen rather than just seeing the raw
    castxml error. Split out of :func:`_header_ast_parser`; the reasoning behind
    each eligible signature is in the comments below.
    """
    # A proactive UnsupportedCastxmlVersionError (raised before castxml
    # even runs) is exactly the same "this castxml can't be trusted"
    # signal as the two string-matched stderr signatures below — it's
    # just detected earlier and more precisely (an exact version
    # comparison instead of a diagnostic-text guess). The opt-in
    # fallback's whole purpose is letting a user accept the
    # castxml/clang discrepancy risk to keep scanning on a host whose
    # castxml can't be trusted; excluding this one reason a castxml is
    # untrusted defeated that opt-in for exactly the case this PR's own
    # new gate creates (Codex review).
    is_version_gate_failure = isinstance(exc, UnsupportedCastxmlVersionError)
    eligible = auto_selected and (
        is_version_gate_failure
        or _is_toolchain_version_failure(str(exc))
        or _is_direct_include_guard_failure(str(exc))
    )
    if not eligible:
        return None

    # Probe the driver _run_clang() would actually invoke (honors
    # --compiler/--compiler-prefix), not just a bare "clang" on PATH (Codex
    # review).
    def _clang_fallback_ready() -> bool:
        try:
            _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
            return True
        except SnapshotError:
            return False

    if not _ast_fallback_enabled() or not _clang_fallback_ready():
        message = (
            f"{exc}\n\nAutomatic CastXML-to-Clang fallback is disabled because "
            "the two frontends can produce materially different findings. "
            "Install a compatible CastXML, select the clang backend explicitly "
            "(.abicheck.yml compile.frontend: clang, or ABICHECK_AST_FRONTEND=clang), "
            "or opt in to the fallback (compile.ast_frontend_fallback: true, or "
            "ABICHECK_ALLOW_AST_FALLBACK=1)."
        )
        raise type(exc)(message) from exc
    log.warning(
        "castxml could not parse the header(s) (toolchain mismatch, an "
        "unsupported castxml version, or a header that refuses direct "
        "inclusion); falling back to the clang header backend, which "
        "parses against the host toolchain and can exclude direct-include "
        "#error guard headers. Set compile.frontend: castxml in .abicheck.yml "
        "(or ABICHECK_AST_FRONTEND=castxml) to force castxml and see the "
        "original error."
    )
    if is_version_gate_failure:
        return "castxml-unsupported-version"
    if _is_toolchain_version_failure(str(exc)):
        return "castxml-toolchain-version-mismatch"
    return "castxml-direct-include-guard"


def _header_ast_parser(
    headers: list[Path],
    extra_includes: list[Path],
    *,
    backend: str,
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    exported_dynamic: set[str],
    exported_static: set[str],
    public_header_paths: list[str],
    public_dir_paths: list[str],
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
    pruning_header_roots: tuple[str, ...] | None = None,
    no_binary_evidence: bool = False,
) -> _CastxmlParser | _ClangAstParser:
    """Run the resolved L2 backend and return its CastXML/Clang parser.

    Both parser implementations expose the same format-builder interface.

    Frontend context selection and header-only evidence semantics are
    forwarded unchanged to the selected backend.
    """
    effective = _resolve_effective_ast_backend(backend, frontend_context)

    request = HeaderParseRequest(
        headers=headers,
        extra_includes=extra_includes,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
        extra_hash_dirs=extra_hash_dirs,
        frontend_context=frontend_context,
        pruning_header_roots=pruning_header_roots,
        no_binary_evidence=no_binary_evidence,
    )

    def _run_clang(*, fallback_reason: str | None = None) -> _ClangAstParser:
        return cast(
            _ClangAstParser,
            HEADER_AST_BACKENDS["clang"].parse(
                request, fallback_reason=fallback_reason
            ),
        )

    if effective == "clang":
        return _run_clang()

    auto_selected = _auto_ast_fallback_eligible(backend)
    try:
        return cast(
            _CastxmlParser,
            HEADER_AST_BACKENDS["castxml"].parse(request),
        )
    except CastxmlRunError as exc:
        fallback_reason = _castxml_fallback_reason(
            exc.original,
            auto_selected=auto_selected,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
        )
        if fallback_reason is None:
            raise exc.original from None
        return _run_clang(fallback_reason=fallback_reason)


# castxml parser + helpers moved to dumper_castxml (see top-of-file imports)


@dataclass(frozen=True)
class _FormatHandler:
    """One binary format: how to recognise it and how to dump it (C3).

    The registry collapses the per-format magic-byte knowledge and the
    ``dump()`` dispatch into a single declarative entry — adding a new binary
    format is a new ``_FormatHandler`` in ``_FORMAT_HANDLERS`` rather than edits
    scattered across ``_detect_format`` and ``dump``'s if/elif chain.

    ``accepts_dwarf_only`` / ``accepts_debug_format`` record which optional
    kwargs the format's builder takes, so ``dump()`` forwards exactly the same
    arguments each ``_dump_*`` accepted before (ELF: both; Mach-O: dwarf_only
    only; PE: neither).
    """

    name: str
    builder: Callable[..., AbiSnapshot]
    magics: tuple[bytes, ...] = ()
    magic_prefix: bytes | None = None
    accepts_dwarf_only: bool = False
    accepts_debug_format: bool = False

    def matches_magic(self, magic: bytes) -> bool:
        if magic in self.magics:
            return True
        if (
            self.magic_prefix is not None
            and magic[: len(self.magic_prefix)] == self.magic_prefix
        ):
            return True
        return False


def _detect_format(path: Path) -> str:
    """Detect binary format from magic bytes. Returns 'elf', 'macho', 'pe', or 'unknown'."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return "unknown"
    for handler in _FORMAT_HANDLERS:
        if handler.matches_magic(magic):
            return handler.name
    return "unknown"


@reference_scratch_scoped
def dump(
    so_path: Path,
    headers: list[Path],
    extra_includes: list[Path] | None = None,
    version: str = "unknown",
    compiler: str = "c++",
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    extra_hash_dirs: tuple[Path, ...] = (),
    debug_info_path: Path | None = None,
    extra_include_labels: dict[Path, str] | None = None,
    dump_manifest: DumpManifest | None = None,
    scope_header_dirs: list[Path] | None = None,
    frontend_context: str = "host",
    public_include_search_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Create an AbiSnapshot from a shared library + headers.

    Supports ELF (.so), Mach-O (.dylib), and PE (.dll) binaries.
    Binary format is auto-detected from magic bytes.  For all formats,
    castxml header analysis is performed when *headers* are provided.

    Args:
        so_path: Path to the shared library (.so / .dylib / .dll).
        headers: List of public header files to parse.
        extra_includes: Additional -I include directories for castxml.
        version: Version string for the snapshot (e.g. "1.2.3").
        compiler: Compiler frontend for castxml ("c++" or "cc").
        gcc_path: Explicit path to a GCC/G++ cross-compiler binary.
        gcc_prefix: Cross-toolchain prefix (e.g. "aarch64-linux-gnu-").
        gcc_options: Extra compiler flags passed through to castxml.
        sysroot: Alternative system root directory.
        nostdinc: If True, do not search standard system include paths.
        lang: Force language ("C" or "C++").
        dwarf_only: If True, force DWARF-only mode even when headers
            are available (ADR-003).
        debug_format: Force debug format for ELF inputs: "dwarf", "btf", or "ctf".
            None = auto-detect (DWARF preferred for userspace, BTF for kernel).
            Ignored for Mach-O and PE binaries.
        symbols_only: For ELF inputs, skip expensive DWARF type expansion and
            build the ABI surface from exported symbols only while still
            recording cheap debug-info presence. No production caller passes it
            since ``scan`` was removed; ``compare --depth binary`` reads DWARF.
        debug_presence_only: For ELF inputs, skip expensive DWARF type expansion
            while still allowing header parsing. Used by shallow scan depths that
            collect L2/L3 from headers/build evidence.
        debug_info_path: For ELF inputs, a resolved detached debug artifact
            (ADR-021a: a build-id-tree or path-mirror ``.debug`` file distinct
            from *so_path*) to read DWARF sections from instead of *so_path*
            itself — lets a stripped binary still get DWARF-aware comparison
            when its separate debug file was found via ``--debug-root``/
            ``--debuginfod`` (P1.1). ``None`` (the default) parses DWARF from
            *so_path*, unchanged. Ignored for non-ELF formats.
        public_headers: Explicit public-header files used only to classify
            declaration provenance (ADR-015). When empty, every declaration's
            origin stays UNKNOWN and behaviour is unchanged.
        public_header_dirs: Directories whose headers are treated as public
            for provenance classification.
        header_backend: "auto"/"castxml"/"clang"/"hybrid" (G28 Phase 3: runs
            both real backends and merges them via dumper_hybrid).
        extra_include_labels: Resolved ``path -> label`` map from a labeled
            ``--include old:LABEL=PATH``/``new:LABEL=PATH`` CLI entry
            (ADR-050 D1), consulted when building the ``IncludeDir`` list
            :func:`comparability.compute_extraction_contract` fingerprints.
            A path with no entry gets ``label=None``, unchanged.
        dump_manifest: A parsed ``--dump-manifest`` document (ADR-050 D3) for
            a real multi-TU dump; mutually exclusive with *headers*,
            *extra_includes*, *public_headers*/*public_header_dirs*. ELF only.
        scope_header_dirs: Directories folded into the extraction contract's
            ``public_header_dirs`` scope-fingerprint field (ADR-050 D1)
            *in addition to* ``public_header_dirs``, without affecting
            declaration-provenance tagging (ADR-015 stays driven by
            ``public_header_dirs`` alone, unchanged). ``compare``'s own
            ``--header <dir>`` already feeds its directory argument into the
            live side's scope contract this way (``cli_resolve.
            _resolve_compare_snapshots``); without an equivalent here, a
            snapshot `dump`-produced from a bare ``-H <dir>`` (with no
            the public-header set) always carries an empty
            ``public_header_dirs`` scope field, so comparing it against a
            live `compare`-side extraction of the identical header set
            spuriously raises ``ScopeMismatchError`` (found during the G30
            pilot validation).
            No typed request populates this field: ``dump``'s own ``-H``
            directory operands reach ``public_header_dirs`` instead, via
            ``service_dump_pipeline.resolve_dump_request``'s
            ``split_public_header_inputs`` call (the same split ``compare``
            applies to its own ``-H`` list). The retired
            ``cli_dump_helpers.perform_elf_dump`` was this parameter's one
            caller; see ``docs/contribute/known-gaps.md``'s entry on that
            channel change.
            Mutually exclusive with *dump_manifest*, same as *headers*.
        frontend_context: "host"/"device" (ADR-050 D5) DPC++/SYCL AST pass;
            "device" on a non-DPC++-capable frontend raises ``AstContextMissingError``.

    Returns:
        AbiSnapshot with functions, variables, and types populated.
    """
    if dump_manifest is not None:
        # Each has its own manifest-field equivalent (roots / per-TU includes /
        # public_header_paths+dirs); a flat value here would be silently
        # unused by the manifest-driven parse or ambiguous.
        _conflicts = {
            "headers": headers,
            "extra_includes": extra_includes,
            "public_headers": public_headers,
            "public_header_dirs": public_header_dirs,
            "scope_header_dirs": scope_header_dirs,
            # No per-TU equivalent for a multi-TU manifest (CodeRabbit review).
            "public_include_search_dirs": public_include_search_dirs,
        }
        if _given := [name for name, value in _conflicts.items() if value]:
            raise ValidationError(
                f"dump_manifest and {', '.join(_given)} are mutually exclusive "
                "-- declare the equivalent in the manifest itself."
            )

    if _is_hybrid_request(
        header_backend,
        dump_manifest_given=dump_manifest is not None,
        frontend_context=frontend_context,
    ):
        from .workflows.dump.hybrid import run_hybrid_dump

        return run_hybrid_dump(
            dump,
            so_path,
            headers,
            extra_includes=extra_includes,
            version=version,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            dwarf_only=dwarf_only,
            debug_format=debug_format,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            extra_hash_dirs=extra_hash_dirs,
            debug_info_path=debug_info_path,
            extra_include_labels=extra_include_labels,
            scope_header_dirs=scope_header_dirs,
            public_include_search_dirs=public_include_search_dirs,
        )

    fmt = _detect_format(so_path)
    handler = _HANDLERS_BY_NAME.get(fmt)
    if handler is None:
        from .binary_utils import detect_archive

        if detect_archive(so_path):
            raise ValidationError(
                f"'{so_path}' is a static/import library archive (.a/.lib); abicheck compares single linkable images "
                "(shared libraries and objects). Extract the members (e.g. "
                "`ar x lib.a`) and compare the resulting object files or the shared "
                "library built from them instead."
            )
        raise ValidationError(
            f"Unrecognised binary format for {so_path}: "
            f"expected ELF, Mach-O, or PE but detected {fmt!r}. "
            f"Ensure the file is a valid shared library."
        )

    extra: dict[str, Any] = {}
    if handler.accepts_dwarf_only:
        extra["dwarf_only"] = dwarf_only
    if handler.accepts_debug_format:
        extra["debug_format"] = debug_format
        extra["symbols_only"] = symbols_only
        extra["debug_presence_only"] = debug_presence_only
        extra["debug_info_path"] = debug_info_path
    # dump_manifest's own public_header_paths/public_header_dirs replace the
    # CLI-flag-derived ones for provenance/contract below (mutual exclusivity
    # already validated above).
    effective_public_headers = (
        list(dump_manifest.public_header_paths)
        if dump_manifest is not None
        else public_headers
    )
    effective_public_header_dirs = (
        list(dump_manifest.public_header_dirs)
        if dump_manifest is not None
        else public_header_dirs
    )
    snapshot = handler.builder(
        so_path,
        headers,
        extra_includes or [],
        version,
        compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        public_headers=effective_public_headers,
        public_header_dirs=effective_public_header_dirs,
        header_backend=header_backend,
        extra_hash_dirs=extra_hash_dirs,
        dump_manifest=dump_manifest,
        frontend_context=frontend_context,
        **extra,
    )

    # Note: from_headers (the HEADER_AWARE evidence-tier signal) is set by the
    # format-specific builders (_dump_elf / _dump_pe / _dump_macho) at the point
    # castxml actually parses headers, so every entry point — including the CLI
    # and service native-binary paths that call those builders directly (e.g.
    # service._try_header_scoped_dump), bypassing this function — records it
    # correctly. DWARF-only and symbols-only builds leave it False.
    #
    # scope_header_dirs is folded into the CONTRACT's public_header_dirs only
    # -- never into apply_provenance's call below -- so a bare `-H <dir>`
    # gains the same scope-comparability identity `compare`'s own `--header
    # <dir>` already has, without silently opting a `dump`-only invocation
    # into declaration-provenance tagging (ADR-015 stays opt-in via the
    # separate public-header inputs, unchanged).
    _attach_extraction_contract(
        snapshot,
        headers=list(dump_manifest.roots) if dump_manifest is not None else headers,
        extra_includes=extra_includes,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        lang=lang,
        public_headers=effective_public_headers,
        # Union, duplicates and all -- compute_extraction_contract's own
        # _normalize() already folds through a set before sorting, so
        # pre-deduping here would only be redundant work (code review).
        public_header_dirs=[
            *(effective_public_header_dirs or []),
            *(scope_header_dirs or []),
        ],
        extra_include_labels=extra_include_labels,
        dump_manifest=dump_manifest,
    )

    # Tag declaration provenance (source_header + origin). Always derives
    # source_header from the parsed source location; origin is only
    # classified when a public-header set is supplied (ADR-015, D4).
    #
    # include_search_dirs=public_include_search_dirs folds those directories
    # into the public-directory set once a real -H/--public-header-dir set
    # already opted classification in: a header-AST dump only ever parses
    # declarations reachable by #include from its own -H root(s), so a
    # header living elsewhere under the same include root that the umbrella
    # header pulled in transitively is not a private implementation detail
    # merely because it isn't the literal -H file (defect: every
    # transitively-included header classified private-header, silently
    # dropping real breaking findings out of the compared surface).
    #
    # Deliberately NOT `extra_includes` (Codex review, real regression found
    # via the example suite): `extra_includes` is the FULL compile include
    # path, which also carries directories this function (or a caller's own
    # P3 `resolve_inferred_header_roots` step) auto-derives purely so an
    # umbrella -H header's own relative #includes resolve -- typically the
    # umbrella header's own directory. That directory can just as easily
    # hold a genuinely *private* sibling header (case184_internal_enum_
    # churn_scoped's own v1_internal.h, next to the public v1.h) -- folding
    # it into the public-directory set defeated the entire private-header
    # scoping example that test exists to cover. `public_include_search_
    # dirs` is a separate, caller-supplied parameter carrying ONLY the
    # directories the caller can positively attest are a real, explicit
    # dependency-search declaration (a literal `-I`/`--include`), never an
    # internal #include-resolution auto-add.
    #
    # Explicit is still not the same as *owned*, though: `apply_provenance`
    # keeps only the roots this run's own declared public headers live
    # underneath. An `-I` a library passes purely so a dependency's
    # `#include <dep.h>` resolves stays compile context -- see
    # `extract.public_root_ownership` for the rule and the MKL/MPI case that forced it.
    from .workflows.snapshot_factory import finish_binary_dump

    return finish_binary_dump(
        snapshot,
        effective_public_headers,
        effective_public_header_dirs,
        include_search_dirs=public_include_search_dirs,
        read_dwarf=not (symbols_only or debug_presence_only),  # shallow: .comment only
    )


def _dump_elf(
    so_path: Path,
    headers: list[Path],
    extra_includes: list[Path],
    version: str,
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    extra_hash_dirs: tuple[Path, ...] = (),
    debug_info_path: Path | None = None,
    dump_manifest: DumpManifest | None = None,
    frontend_context: str = "host",
) -> AbiSnapshot:
    """ELF-specific dump: pyelftools + debug info (DWARF/BTF/CTF) + header AST.

    *dump_manifest* (ADR-050 D3, Phase B): a real multi-TU dump via
    :func:`abicheck.extract.headers.manifest.resolve_header_ast_result`, replacing
    the single flat *headers*/*extra_includes* parse. *headers* must be
    empty in this case (enforced by :func:`dump`). PE/Mach-O reject a
    non-``None`` value outright (not yet supported there).
    """
    extra_includes = absolutize_include_roots(extra_includes)  # see its docstring
    exported_dynamic, exported_static = _pyelftools_exported_symbols(so_path)
    from .elf_metadata import parse_elf_metadata

    elf_meta = parse_elf_metadata(so_path)
    (
        exported_dynamic,
        exported_dynamic_funcs,
        exported_dynamic_objects,
        exported_dynamic_tls,
    ) = _elf_classify_symbols(elf_meta, exported_dynamic, library_name=so_path.name)
    # A DWARF metadata parse that finds real debug info leaves its open
    # DwarfSession in ``_dwarf_session_out`` so the snapshot build below can
    # reuse the same DWARFInfo/DIE cache instead of re-parsing (F5b); the
    # finally below closes it on every exit path, including exceptions.
    _dwarf_session_out: list[DwarfSession] = []
    # Auto-detect can resolve to BTF/CTF with debug_format still None (Codex review).
    _dwarf_format_out: list[str | None] = []
    dwarf_only_types: list[RecordType] = []
    try:
        if symbols_only or debug_presence_only:
            from .dwarf_presence import cheap_debug_presence_metadata

            dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
                so_path, debug_format=debug_format
            )
        else:
            dwarf_meta, dwarf_adv = _resolve_debug_metadata(
                so_path,
                debug_format,
                _session_out=_dwarf_session_out,
                _format_out=_dwarf_format_out,
                dwarf_source=debug_info_path,
            )
        resolved_debug_format = (
            _dwarf_format_out[0] if _dwarf_format_out else debug_format
        )
        dwarf_session = _dwarf_session_out[0] if _dwarf_session_out else None
        profile_hint = lang_to_profile(lang)
        # ADR-003 fallback chain: --dwarf-only forces DWARF mode; no headers +
        # DWARF -> DWARF-only mode; no headers + no DWARF -> symbols-only. Both
        # legs gated on resolved_debug_format, not dwarf_meta.has_dwarf (which
        # mirrors BTF/CTF presence too, and --dwarf-only + --debug-format
        # btf/ctf resolves no real DWARF either — Codex review, twice).
        if dwarf_only and resolved_debug_format != "dwarf":
            warnings.warn(
                f"debug.dwarf_only requested but resolved debug format is {resolved_debug_format!r}; ignoring.",
                UserWarning,
                stacklevel=2,
            )
        no_headers = not headers and dump_manifest is None
        if (
            not (symbols_only or debug_presence_only)
            and resolved_debug_format == "dwarf"
            and (dwarf_only or (no_headers and dwarf_meta.has_dwarf))
        ):
            snap, dwarf_only_types = _try_dwarf_snapshot(
                so_path,
                elf_meta,
                dwarf_meta,
                dwarf_adv,
                version,
                profile_hint,
                headers,
                dwarf_only,
                session=dwarf_session,
            )
            if snap is not None:
                return snap
        if symbols_only or no_headers:
            return _build_symbol_only_snapshot(
                so_path,
                version,
                elf_meta,
                dwarf_meta,
                dwarf_adv,
                exported_dynamic_funcs,
                exported_dynamic_objects,
                exported_dynamic_tls,
                dwarf_only_types,
                profile_hint,
                # Presence-only probe never parsed structs/enums (Codex review, PR #1026).
                None
                if (symbols_only or debug_presence_only)
                else resolved_debug_format,
            )
        # Built here (session open): "auto" can fall back to clang (G16), so
        # ast_result.is_clang is the only reliable signal (Codex review).
        from .extract.headers.manifest import resolve_header_ast_result

        ast_result = timed("header AST parse")(resolve_header_ast_result)(
            dump_manifest=dump_manifest,
            headers=headers,
            extra_includes=extra_includes,
            header_ast_parser=_header_ast_parser,
            backend=header_backend,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            exported_dynamic=exported_dynamic,
            exported_static=exported_static,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            extra_hash_dirs=extra_hash_dirs,
            frontend_context=frontend_context,
        )
        # Host DWARF describes the host-compiled binary's own layout --
        # meaningless for a SYCL/DPC++ device-target AST pass (a different
        # architecture/ABI can have different sizes/offsets); backfilling
        # by name would attach unrelated host data (Codex review).
        _is_device_context = ast_result.frontend_context_kind == "device"
        dwarf_layout_types = (
            []
            if _is_device_context
            else dwarf_layout_types_or_empty(
                so_path,
                elf_meta,
                dwarf_meta,
                ast_result.is_clang,
                symbols_only=symbols_only,
                debug_presence_only=debug_presence_only,
                debug_format=resolved_debug_format,
                session=dwarf_session,
            )
        )
    finally:
        for _sess in _dwarf_session_out:
            _sess.close()

    _backfilled_types, _layout_coherence = backfill_dwarf_layout(
        list(ast_result.types), dwarf_layout_types, dwarf_meta.struct_odr_conflicts
    )
    _dwarf_layout_coherence, _dwarf_layout_coherence_mismatches = (
        resolve_snapshot_layout_coherence(
            is_clang_backend=ast_result.is_clang and not _is_device_context,
            coherence=_layout_coherence,
        )
    )

    _so_mtime, _so_mtime_epoch = _safe_mtime(so_path)
    snapshot = new_snapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path.resolve()),
        source_mtime=_so_mtime,
        source_mtime_epoch=_so_mtime_epoch,
        source_size=_safe_size(so_path),
        functions=list(ast_result.functions),
        variables=list(ast_result.variables),
        types=_backfilled_types,
        enums=list(ast_result.enums),
        typedefs=ast_result.typedefs,
        typedefs_qualified=ast_result.typedefs_qualified,
        constants=ast_result.constants,
        typedef_entity_ids=ast_result.typedef_entity_ids,
        constant_entity_ids=ast_result.constant_entity_ids,
        semantic_ir=ast_result.semantic_ir,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        # Reached only when headers were supplied and castxml ran (the no-header
        # and DWARF-only branches return earlier): this surface is header-parsed.
        from_headers=True,
        ast_producer=ast_result.ast_producer,
        ast_toolchain=ast_result.ast_toolchain,
        ast_fallback_reason=ast_result.ast_fallback_reason,
        ast_toolchain_supported=ast_result.ast_toolchain_supported,
        ast_toolchain_unsupported_reasons=list(
            ast_result.ast_toolchain_unsupported_reasons
        ),
        frontend_context_kind=ast_result.frontend_context_kind,
        platform="elf",
        language_profile=profile_hint,
        dwarf_layout_coherence=_dwarf_layout_coherence,
        dwarf_layout_coherence_mismatches=_dwarf_layout_coherence_mismatches,
        **_ast_compile_provenance(
            list(ast_result.provenance_headers),
            gcc_options,
            gcc_option_tokens,
            sysroot,
            ast_toolchain=ast_result.ast_toolchain,
            lang=lang,
        ),
    )
    _populate_elf_visibility(snapshot)
    return finish_binary_snapshot(snapshot)


def _dump_macho(
    dylib_path: Path,
    headers: list[Path],
    extra_includes: list[Path],
    version: str,
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    dwarf_only: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    extra_hash_dirs: tuple[Path, ...] = (),
    dump_manifest: DumpManifest | None = None,
    frontend_context: str = "host",
) -> AbiSnapshot:
    """Mach-O dump: export table from macholib + header-AST analysis.

    *dump_manifest* is not yet supported here (ADR-050 D3 is ELF-scoped) --
    rejected explicitly rather than silently ignored.
    """
    extra_includes = absolutize_include_roots(extra_includes)  # see its docstring
    if dump_manifest is not None:
        raise ValidationError(
            "--dump-manifest is not yet supported for Mach-O binaries"
            "; use a single-header dump for this format."
        )
    if dwarf_only:
        warnings.warn(
            "dwarf_only=True is not supported for Mach-O; "
            "falling back to normal extraction.",
            UserWarning,
            stacklevel=2,
        )
    from .macho_metadata import parse_macho_metadata

    macho_meta = parse_macho_metadata(dylib_path)
    # Build exported symbol set from Mach-O export table
    exported_dynamic: set[str] = {
        exp.name
        for exp in macho_meta.exports
        if exp.name and _is_abi_relevant_symbol(exp.name)
    }

    profile_hint = lang_to_profile(lang)

    if not headers:
        # Advisory only (ADR-035 P6): info log, not a per-run UserWarning.
        log.info(
            "No headers provided — only Mach-O exported symbols will be captured; "
            "type information will be missing."
        )

        # `macho_meta.exports` entries are already normalized -- see the
        # "already normalized" comment a few lines below, at the
        # `exported_dynamic` build above, for the full account of why a
        # second leading-underscore strip here corrupts every Itanium-
        # mangled C++ export (this branch had the identical double-strip
        # bug the with-headers path below used to have).
        # Split exports into functions (__TEXT) and variables (__DATA)
        # using section classification from Mach-O nlist entries.
        _relevant = [
            exp
            for exp in macho_meta.exports
            if exp.name and _is_abi_relevant_symbol(exp.name)
        ]
        macho_funcs = [exp for exp in _relevant if not exp.is_data]
        macho_vars = [exp for exp in _relevant if exp.is_data]

        _dylib_mtime, _dylib_mtime_epoch = _safe_mtime(dylib_path)
        # ADR-063 Phase 2: see extract.export_symbol_identity's own docstring.
        return new_snapshot(
            library=dylib_path.name,
            version=version,
            source_path=str(dylib_path.resolve()),
            source_mtime=_dylib_mtime,
            source_mtime_epoch=_dylib_mtime_epoch,
            source_size=_safe_size(dylib_path),
            functions=[
                _itanium_export_function(exp.name)
                for exp in sorted(macho_funcs, key=lambda e: e.name)
            ],
            variables=[
                _itanium_export_variable(exp.name)
                for exp in sorted(macho_vars, key=lambda e: e.name)
            ],
            macho=macho_meta,
            elf_only_mode=True,
            platform="macho",
            language_profile=profile_hint,
        )

    # `macho_meta.exports` entries are already normalized (macho_metadata.py
    # strips the Mach-O ABI's leading underscore itself while walking the
    # export trie/symtab — see its own "Strip leading underscore" step), so
    # `exported_dynamic` here already reads e.g. "_ZN4demo9configureE..." for
    # a C++ symbol or "foo" for a plain C one, matching the header-AST names
    # castxml computes verbatim. A second strip used to run here too, which
    # was harmless for C symbols but corrupted every Itanium-mangled C++ name
    # by eating the leading underscore of its own "_Z..." prefix — silently
    # guaranteeing zero header/export matches for any C++ Mach-O binary and
    # falling back to export-table-only mode (observed on macOS CI; the
    # equivalent ELF path never had this double-strip).
    parser = timed("header AST parse")(_header_ast_parser)(
        headers,
        extra_includes,
        backend=header_backend,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_dynamic,
        public_header_paths=[str(h) for h in headers]
        + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
        extra_hash_dirs=extra_hash_dirs,
        frontend_context=frontend_context,
    )

    _dylib_mtime, _dylib_mtime_epoch = _safe_mtime(dylib_path)
    _ast_producer = "clang" if isinstance(parser, _ClangAstParser) else "castxml"
    _ast = parse_header_ast_fields(parser, producer=_ast_producer)
    return finish_binary_snapshot(
        new_snapshot(
            library=dylib_path.name,
            version=version,
            source_path=str(dylib_path.resolve()),
            source_mtime=_dylib_mtime,
            source_mtime_epoch=_dylib_mtime_epoch,
            source_size=_safe_size(dylib_path),
            functions=list(_ast.functions),
            variables=list(_ast.variables),
            types=list(_ast.types),
            enums=list(_ast.enums),
            typedefs=_ast.typedefs,
            typedefs_qualified=_ast.typedefs_qualified,
            constants=_ast.constants,
            typedef_entity_ids=_ast.typedef_entity_ids,
            constant_entity_ids=_ast.constant_entity_ids,
            semantic_ir=_ast.semantic_ir,
            macho=macho_meta,
            # Reached only when headers were supplied and castxml ran (the no-header
            # branch returns earlier): this surface is header-parsed.
            from_headers=True,
            ast_producer=_ast_producer,
            ast_toolchain=_parser_ast_toolchain(parser),
            ast_fallback_reason=_parser_ast_fallback_reason(parser),
            ast_toolchain_supported=_parser_ast_supported(parser),
            ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
            frontend_context_kind=_parser_frontend_context_kind(parser),
            platform="macho",
            language_profile=profile_hint,
            **_ast_compile_provenance(
                headers,
                gcc_options,
                gcc_option_tokens,
                sysroot,
                ast_toolchain=_parser_ast_toolchain(parser),
                lang=lang,
            ),
        )
    )


def _dump_pe(
    dll_path: Path,
    headers: list[Path],
    extra_includes: list[Path],
    version: str,
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    extra_hash_dirs: tuple[Path, ...] = (),
    dump_manifest: DumpManifest | None = None,
    frontend_context: str = "host",
) -> AbiSnapshot:
    """PE dump: export table from pefile + header-AST analysis.

    *dump_manifest* is not yet supported here (ADR-050 D3 is ELF-scoped) --
    rejected explicitly rather than silently ignored.
    """
    extra_includes = absolutize_include_roots(extra_includes)  # see its docstring
    if dump_manifest is not None:
        raise ValidationError(
            "--dump-manifest is not yet supported for PE binaries"
            "; use a single-header dump for this format."
        )
    from .pe_metadata import parse_pe_metadata

    pe_meta = parse_pe_metadata(dll_path)
    exported_dynamic: set[str] = {
        (exp.name or f"ordinal:{exp.ordinal}") for exp in pe_meta.exports
    }
    exported_static: set[str] = set(exported_dynamic)

    profile_hint = lang_to_profile(lang)

    if not headers:
        # Advisory only (ADR-035 P6): info log, not a per-run UserWarning.
        log.info(
            "No headers provided — only PE exported symbols will be captured; "
            "type information will be missing."
        )
        _dll_mtime, _dll_mtime_epoch = _safe_mtime(dll_path)
        # 32-bit x86 is the only PE machine type with __stdcall/__fastcall/
        # __cdecl export decoration -- see export_symbol_identity.py's own
        # comment for why every other machine type must NOT strip a
        # leading underscore.
        _is_x86_32 = pe_meta.machine == "IMAGE_FILE_MACHINE_I386"
        return new_snapshot(
            library=dll_path.name,
            version=version,
            source_path=str(dll_path.resolve()),
            source_mtime=_dll_mtime,
            source_mtime_epoch=_dll_mtime_epoch,
            source_size=_safe_size(dll_path),
            # ADR-063 Phase 2: see extract.export_symbol_identity's own docstring.
            functions=[
                _msvc_export_function(sym, is_x86_32=_is_x86_32)
                for sym in sorted(exported_dynamic)
            ],
            pe=pe_meta,
            elf_only_mode=True,
            platform="pe",
            language_profile=profile_hint,
        )

    parser = timed("header AST parse")(_header_ast_parser)(
        headers,
        extra_includes,
        backend=header_backend,
        compiler=compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        exported_dynamic=exported_dynamic,
        exported_static=exported_static,
        public_header_paths=[str(h) for h in headers]
        + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
        extra_hash_dirs=extra_hash_dirs,
        frontend_context=frontend_context,
    )

    _dll_mtime, _dll_mtime_epoch = _safe_mtime(dll_path)
    _ast_producer = "clang" if isinstance(parser, _ClangAstParser) else "castxml"
    _ast = parse_header_ast_fields(parser, producer=_ast_producer)
    return finish_binary_snapshot(
        new_snapshot(
            library=dll_path.name,
            version=version,
            source_path=str(dll_path.resolve()),
            source_mtime=_dll_mtime,
            source_mtime_epoch=_dll_mtime_epoch,
            source_size=_safe_size(dll_path),
            functions=list(_ast.functions),
            variables=list(_ast.variables),
            types=list(_ast.types),
            enums=list(_ast.enums),
            typedefs=_ast.typedefs,
            typedefs_qualified=_ast.typedefs_qualified,
            constants=_ast.constants,
            typedef_entity_ids=_ast.typedef_entity_ids,
            constant_entity_ids=_ast.constant_entity_ids,
            semantic_ir=_ast.semantic_ir,
            pe=pe_meta,
            # Reached only when headers were supplied and castxml ran (the no-header
            # branch returns earlier): this surface is header-parsed.
            from_headers=True,
            ast_producer=_ast_producer,
            ast_toolchain=_parser_ast_toolchain(parser),
            ast_fallback_reason=_parser_ast_fallback_reason(parser),
            ast_toolchain_supported=_parser_ast_supported(parser),
            ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
            frontend_context_kind=_parser_frontend_context_kind(parser),
            platform="pe",
            language_profile=profile_hint,
            **_ast_compile_provenance(
                headers,
                gcc_options,
                gcc_option_tokens,
                sysroot,
                ast_toolchain=_parser_ast_toolchain(parser),
                lang=lang,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Binary-format handler registry (C3). Single source of truth for magic-byte
# recognition (drives _detect_format) and dump() dispatch. Defined after the
# _dump_* builders it references; resolved at call time. Add a format by adding
# an entry here — no edits to _detect_format or dump().
# ---------------------------------------------------------------------------

_FORMAT_HANDLERS: tuple[_FormatHandler, ...] = (
    _FormatHandler(
        name="elf",
        builder=_dump_elf,
        magics=(b"\x7fELF",),
        accepts_dwarf_only=True,
        accepts_debug_format=True,
    ),
    _FormatHandler(
        name="macho",
        builder=_dump_macho,
        magics=(
            b"\xfe\xed\xfa\xce",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
            b"\xca\xfe\xba\xbf",
            b"\xbf\xba\xfe\xca",
        ),
        accepts_dwarf_only=True,
    ),
    _FormatHandler(
        name="pe",
        builder=_dump_pe,
        magic_prefix=b"MZ",
    ),
)

_HANDLERS_BY_NAME: dict[str, _FormatHandler] = {h.name: h for h in _FORMAT_HANDLERS}
