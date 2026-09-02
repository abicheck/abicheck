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
import os
import shutil as shutil  # noqa: F401  # legacy test patch target
import subprocess
import tempfile
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

if TYPE_CHECKING:
    from .dump_manifest import DumpManifest
    from .dwarf_unified import DwarfSession

from defusedxml import ElementTree as DefusedET

from . import deadline, dumper_cache, qualified_name_segments
from .castxml_policy import evaluate_castxml_version
from .dumper_ast_config import (
    _CPP_ONLY_PATTERNS as _CPP_ONLY_PATTERNS,
    _build_castxml_command as _build_castxml_command,
    _build_clang_header_command as _build_clang_header_command,
    _cache_key as _cache_key,
    _detect_cpp_headers as _detect_cpp_headers,
    _resolve_compiler_binary as _resolve_compiler_binary,
)
from .dumper_ast_config_cpp20 import _detect_cpp20_headers as _detect_cpp20_headers
from .dumper_cache import _atomic_write as _atomic_write, _cache_path as _cache_path
from .dumper_castxml import (
    _CastxmlParser as _CastxmlParser,
    _parse_vtable_index as _parse_vtable_index,
    _vt_sort_key as _vt_sort_key,
)
from .dumper_castxml_probe import (
    _castxml_failure_hint as _castxml_failure_hint,
    _castxml_version_note as _castxml_version_note,
    _is_toolchain_version_failure as _is_toolchain_version_failure,
    _parse_castxml_version as _parse_castxml_version,
    _validate_castxml_output as _validate_castxml_output,
)
from .dumper_clang import (
    _clang_available as _clang_available,
    _ClangAstParser as _ClangAstParser,
    _is_dpcpp_family_binary as _is_dpcpp_family_binary,
    _needs_sycl_host_only as _needs_sycl_host_only,
    _resolve_clang_bin as _resolve_clang_bin,
    _resolve_dpcpp_multi_context,
)
from .dumper_clang_errors import (
    _is_direct_include_guard_failure,
    _is_missing_cpp_stdlib_header_error,
    _parse_clang_ast_result,
    retry_excluding_error_headers,
    run_clang_to_ast_file,
)
from .dumper_contract import (
    # ADR-050 D1 extraction-contract attachment lives in the sibling module
    # (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._attach_extraction_contract`` remains a valid bare-name call
    # in ``dump()`` and a valid import target for ``service.py``'s
    # PE/Mach-O header-scoped dump path.
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
from .dumper_elf_fallback import (
    # DWARF/symbol-only fallback snapshot builders live in the sibling module
    # (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._try_dwarf_snapshot``/``dumper._build_symbol_only_snapshot``
    # remain valid bare-name calls in ``_dump_elf`` and existing test patch
    # targets (``patch.object(dumper, "_try_dwarf_snapshot", ...)``).
    _build_symbol_only_snapshot as _build_symbol_only_snapshot,
    _try_dwarf_snapshot as _try_dwarf_snapshot,
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
from .dumper_sysinc import (
    _auto_system_includes_enabled as _auto_system_includes_enabled,
    _parse_gnu_include_search_dirs as _parse_gnu_include_search_dirs,
    _probe_gnu_system_includes as _probe_gnu_system_includes,
    _resolve_clang_system_includes as _resolve_clang_system_includes,
    _resolve_probe_compiler as _resolve_probe_compiler,
)
from .dumper_toolchain import (
    _allow_unsupported_castxml_enabled as _allow_unsupported_castxml_enabled,
    _ast_compile_provenance as _ast_compile_provenance,
    _ast_fallback_enabled as _ast_fallback_enabled,
    _auto_ast_fallback_eligible as _auto_ast_fallback_eligible,
    _castxml_available as _castxml_available,
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
from .errors import (
    AstContextMissingError,
    SnapshotError,
    UnsupportedCastxmlVersionError,
    ValidationError,
)
from .extract.export_symbol_identity import (
    itanium_export_function as _itanium_export_function,
    itanium_export_variable as _itanium_export_variable,
    msvc_export_function as _msvc_export_function,
)
from .model import AbiSnapshot, RecordType

log = logging.getLogger(__name__)


# L2 producers; hybrid is explicit because it runs both tools (~2x cost).
HEADER_BACKENDS = ("auto", "castxml", "clang", "hybrid")


def _resolve_header_backend(backend: str | None) -> str:
    """Resolve an L2 header-AST frontend request to a concrete ``castxml``/
    ``clang``/``hybrid``.

    Precedence: an explicit ``castxml``/``clang``/``hybrid`` is honored
    verbatim (and the caller gets a clear error later if a needed tool is
    missing). ``auto``/``None`` consults the ``ABICHECK_AST_FRONTEND`` env
    var first, then resolves to castxml (the schema reference). Never
    auto-falls-back to clang, and never auto-resolves to ``hybrid``: clang
    JSON AST snapshots lack computed layout, and running both backends
    unasked would silently double dump cost (see ``dumper_hybrid.py``).
    """
    choice = (backend or "auto").lower()
    if choice in ("castxml", "clang", "hybrid"):
        return choice
    if choice != "auto":
        raise ValidationError(
            f"Unknown AST frontend {backend!r}; expected one of {HEADER_BACKENDS}."
        )
    env = os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower()
    if env in ("castxml", "clang", "hybrid"):
        return env
    return "castxml"


def _resolve_clang_langmode(
    lang: str | None,
    headers: list[Path],
    clang_bin: str,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
) -> tuple[bool, bool, bool, str]:
    """Return ``(force_cpp, force_cpp20, explicit_c_request, cc_id)`` for the TU.

    ``explicit_c_request`` records whether C was *explicitly* requested
    (``--lang c``) vs auto-detected — both leave ``force_cpp`` False, but the
    C→C++ self-heal treats them differently (warning vs debug; Codex review).
    """
    force_cpp = _resolve_force_cpp(lang, headers, gcc_options, gcc_option_tokens)
    force_cpp20 = force_cpp and _detect_cpp20_headers(headers)
    explicit_c_request = bool(lang) and not force_cpp
    cc_id = "msvc" if Path(clang_bin).name.lower() in ("cl", "cl.exe") else "gnu"
    return force_cpp, force_cpp20, explicit_c_request, cc_id


def _log_c_to_cpp_selfheal(explicit_c_request: bool) -> None:
    """Log the C→C++ self-heal at the right level for how C was chosen."""
    if explicit_c_request:
        # Explicit --lang c that needs the C++ stdlib: keep the self-heal visible
        # — the result is C++ ABI evidence, not the C requested (Codex review).
        log.warning(
            "clang was asked for C (--lang c) but the header(s) require the "
            "C++ standard library; self-healing to C++ mode. The result is "
            "C++ ABI evidence — pass --lang c++ to make this explicit, or "
            "verify you intended a C library."
        )
    else:
        log.debug(
            "clang auto-detected C for a pure-#include umbrella header (no "
            "inline C++ syntax to key on), then self-healed to C++ after a "
            "missing C++ standard header — an unambiguous C++ signal. The "
            "result is unaffected; pass --lang c++ to skip the initial C probe."
        )


def _clang_header_dump(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str = "c++",
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    extra_hash_dirs: tuple[Path, ...] = (),
    frontend_context: str = "host",
    memoize: bool | None = None,
    pruning_header_roots: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], str | None, bool]:
    """Run clang over *headers* and return ``(root, resolved_kind, resolved_force_cpp)``.

    ``resolved_force_cpp`` is the mode that actually produced *root* -- the
    post-retry ``True`` when a C-mode parse self-healed into C++, else
    ``force_cpp`` (Codex review: the provenance probe must not re-derive a
    stale guess once this already resolved the real answer).

    Known, narrower residual (Codex review, fresh evidence, not fixed
    here): a cache/memo *hit* still returns the pre-retry ``force_cpp``
    rather than the mode that actually produced the *cached* AST, since
    neither the disk cache (a raw JSON/XML document, byte-identical to what
    a fresh parse writes) nor the in-process memo (``dumper_cache.
    store_cached_ast``, a bare ``(backend, key, root)`` tuple) persists this
    fact alongside the AST -- and the cache key deliberately does *not*
    distinguish a C-mode success from an initially-C-mode call's self-healed
    C++ success (see the key's own ``system_includes`` comment below). A
    correct fix needs a real cache-format change on both backends (a
    wrapper or sidecar carrying this one extra bit) verified against their
    existing cache-corruption/eviction paths -- a genuine, if narrow,
    cross-cutting change, not a follow-up to this fix. Matters only when a
    *second*, cache-hit call is made for the identical self-healed input
    (the common single-dump-per-process path never hits this, since the
    fresh call that actually self-healed already reports the correct
    value).

    The clang-frontend counterpart of :func:`_castxml_dump`: aggregates the
    headers into one ``#include`` TU, runs ``clang -ast-dump=json``, returns
    the JSON dict :class:`abicheck.dumper_clang._ClangAstParser` consumes.
    Disk-cached like the castxml path, and memoized in-process (G31 Phase C)
    for ``_attach_header_graph``'s reuse (``memoize=False``: final consumer;
    ``None`` defers to ``ast_memoize_active()``, set only in ``run_dump``'s
    primary-dump call). Raises :class:`SnapshotError` when clang is missing,
    times out, or emits no usable AST.

    ``frontend_context`` (ADR-050 D5, G32 Phase D) is ``"host"``/``"device"``.
    ``resolved_kind`` is *frontend_context* when the multi-pass SYCL decode
    path is engaged, else ``None``. A non-``"host"`` request fails immediately
    with :class:`abicheck.errors.AstContextMissingError` when *clang_bin*
    isn't DPC++-capable, or when its own ``gcc_options``/``gcc_option_tokens``
    explicitly disable SYCL (``-fno-sycl``) -- never silently resolved by
    re-enabling SYCL ourselves (Codex review, P2).
    """
    clang_bin = _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
    dpcpp_multi_context = _resolve_dpcpp_multi_context(
        clang_bin, frontend_context, gcc_options, gcc_option_tokens
    )
    force_cpp, force_cpp20, explicit_c_request, cc_id = _resolve_clang_langmode(
        lang,
        headers,
        clang_bin,
        gcc_options,
        gcc_option_tokens,
    )

    # castxml↔clang parity: probe the host GNU compiler for its ``-isystem`` dirs
    # so clang resolves libstdc++/libc the way castxml does via ``--castxml-cc-gnu``.
    # Folded into the cache key so a toolchain change invalidates a stale dump.
    def _resolve_sysinc(*, force_cpp: bool) -> tuple[str, ...]:
        return _resolve_clang_system_includes(
            compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            sysroot=sysroot,
            nostdinc=nostdinc,
            force_cpp=force_cpp,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
        )

    system_includes = _resolve_sysinc(force_cpp=force_cpp)
    # Pre-resolve the C++ system include set so it folds into the cache key (the
    # C-mode probe omits the versioned libstdc++ dirs, so without this a
    # libstdc++/GCC upgrade would not change the key and reuse a stale C++ AST —
    # Codex review) and is reused by the C→C++ retry without a second probe. Costs
    # a C-mode dump one extra ``g++ -E -v`` probe — the price of a retry-stable key.
    cpp_system_includes = (
        system_includes if force_cpp else _resolve_sysinc(force_cpp=True)
    )
    frontend_identity = _tool_identity(clang_bin)
    # Clang is both frontend and compiler here. A GNU driver is only an
    # optional include-path probe; clang-only hosts must not acquire a fake
    # hard dependency on g++ merely for cache identity/provenance.
    compiler_identity = frontend_identity

    key = _cache_key(
        headers,
        extra_includes,
        clang_bin,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        backend="clang",
        # Both include sets feed the key: whichever the retry settles on, a
        # toolchain change to either invalidates the cached AST. Equal when
        # already in C++ mode — pass once so existing C++ cache keys are stable.
        system_includes=system_includes
        if force_cpp
        else (*system_includes, *cpp_system_includes),
        extra_hash_dirs=extra_hash_dirs,
        frontend_identity=frontend_identity,
        compiler_identity=compiler_identity,
        force_cpp20=force_cpp20,
        frontend_context=frontend_context,
    )
    resolved_kind = frontend_context if dpcpp_multi_context else None
    cached = _cache_path(key, backend="clang")
    # A memo hit (G31 Phase C) skips the disk read/JSON re-parse entirely.
    _memoize = dumper_cache.ast_memoize_active() if memoize is None else memoize
    _cached_result = dumper_cache.load_cached_ast(
        key, "clang", cached, memoize=_memoize
    )
    if _cached_result is not None:
        return cast("dict[str, Any]", _cached_result), resolved_kind, force_cpp

    agg_ext = ".hpp" if force_cpp else ".h"
    with tempfile.NamedTemporaryFile(suffix=agg_ext, mode="w", delete=False) as agg:
        agg_path = Path(agg.name)
    active_headers = list(headers)

    def _write_agg(hdrs: list[Path]) -> None:
        agg_path.write_text(
            "".join(f'#include "{h.resolve()}"\n' for h in hdrs), encoding="utf-8"
        )

    _write_agg(active_headers)

    _ast_paths: list[Path] = []  # each attempt's AST, cleaned up in `finally` below

    def _run_clang(
        fcpp: bool, fcpp20: bool, sysinc: tuple[str, ...]
    ) -> subprocess.CompletedProcess[str]:
        cmd = _build_clang_header_command(
            clang_bin,
            cc_id,
            extra_includes,
            agg_path,
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=fcpp,
            force_cpp20=fcpp20,
            system_includes=sysinc,
            dpcpp_multi_context=dpcpp_multi_context,
        )
        # DeadlineExceeded propagates uncaught, mapped by run_scan_core to _BudgetOverflow.
        deadline.check()
        try:
            return run_clang_to_ast_file(cmd, timeout=120, on_created=_ast_paths.append)
        except subprocess.TimeoutExpired as exc:
            raise SnapshotError(
                "clang timed out after 120 seconds parsing the header(s). The header "
                "may contain syntax that causes the frontend to hang. The clang "
                "process (and any child processes) has been terminated."
            ) from exc

    try:
        result = _run_clang(force_cpp, force_cpp20, system_includes)
        # C→C++ self-heal: a pure-``#include`` umbrella header (e.g. oneTBB's
        # ``oneapi/tbb.h``) picks C mode, then ``#include <cstddef>`` fails — a
        # missing C++ *standard* header is an unambiguous "this is C++" signal, so
        # retry once in C++ mode with the pre-resolved C++ system includes. Skipped
        # when already C++ or the failure is anything but a missing C++ stdlib header.
        if (
            result.returncode != 0
            and not force_cpp
            and _is_missing_cpp_stdlib_header_error(result.stderr or "")
        ):
            _log_c_to_cpp_selfheal(explicit_c_request)
            cur_fcpp, cur_fcpp20, cur_sysinc = (
                True,
                _detect_cpp20_headers(headers),
                cpp_system_includes,
            )
            result = _run_clang(cur_fcpp, cur_fcpp20, cur_sysinc)
        else:
            cur_fcpp, cur_fcpp20, cur_sysinc = force_cpp, force_cpp20, system_includes
        # Graceful #error handling: when ``-H`` expands to a public include dir,
        # some headers are not meant to be included directly and raise a
        # preprocessor ``#error`` (preview / internal ``detail`` headers) that
        # would otherwise abort the whole aggregate compile. Drop the offending
        # headers and re-parse the rest (see dumper_clang_errors).
        result = retry_excluding_error_headers(
            result=result,
            run_clang=lambda: _run_clang(cur_fcpp, cur_fcpp20, cur_sysinc),
            write_agg=_write_agg,
            agg_path=agg_path,
            active_headers=active_headers,
        )
        identities_stable = _tool_identity(clang_bin) == frontend_identity
        if not identities_stable:
            log.warning(
                "AST toolchain changed during clang execution; skipping cache write"
            )
        root = _parse_clang_ast_result(
            result,
            cached,
            _ast_paths[-1],
            cache_write=identities_stable,
            dpcpp_capable=dpcpp_multi_context,
            frontend_context=frontend_context,
            header_roots=pruning_header_roots if pruning_header_roots is not None else tuple(str(h) for h in headers),
        )
        if identities_stable and _memoize:
            dumper_cache.store_cached_ast(key, "clang", root)
        return root, resolved_kind, cur_fcpp
    finally:
        agg_path.unlink(missing_ok=True)
        for _p in _ast_paths:
            _p.unlink(missing_ok=True)


def _resolve_single_ast_backend(backend: str, frontend_context: str) -> str:
    """Resolve *backend* to the one L2 frontend that will actually run.

    Rejects the two requests no single parser can satisfy: ``"hybrid"`` (which
    only :func:`abicheck.dumper_hybrid.run_hybrid_dump` can resolve) and a
    non-``"host"`` *frontend_context* under a deliberately-chosen castxml, which
    has no SYCL/DPC++ host/device context concept. Split out of
    :func:`_header_ast_parser` so that function reads as the three-way backend
    dispatch it is.
    """
    resolved = _resolve_header_backend(backend)
    if resolved == "hybrid":
        # No single parser exists for "hybrid" — must be resolved by
        # dumper_hybrid.run_hybrid_dump, not silently treated as castxml.
        raise ValidationError(
            '"hybrid" AST frontend has no single parser here '
            "(see dumper_hybrid.run_hybrid_dump)."
        )
    # `resolved` alone can't tell explicit/env-pinned/defaulted castxml apart; an env pin counts as explicit (environment.md: "honoured verbatim").
    _env_pinned_castxml = (backend or "auto").lower() == "auto" and (
        os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower() == "castxml"
    )
    if (
        resolved == "castxml"
        and frontend_context != "host"
        and ((backend or "auto").lower() == "castxml" or _env_pinned_castxml)
    ):
        raise AstContextMissingError(
            f"--frontend-context {frontend_context!r} requires the clang "
            "header backend (--ast-frontend clang); castxml has no SYCL/"
            "DPC++ host/device context concept."
        )
    return resolved


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
            "Install a compatible CastXML, select --ast-frontend clang "
            "explicitly, or opt in with --allow-ast-frontend-fallback "
            "(ABICHECK_ALLOW_AST_FALLBACK=1)."
        )
        raise type(exc)(message) from exc
    log.warning(
        "castxml could not parse the header(s) (toolchain mismatch, an "
        "unsupported castxml version, or a header that refuses direct "
        "inclusion); falling back to the clang header backend, which "
        "parses against the host toolchain and can exclude direct-include "
        "#error guard headers. Set --ast-frontend castxml to force castxml "
        "and see the original error."
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
) -> _CastxmlParser | _ClangAstParser:
    """Run the resolved L2 backend and return its CastXML/Clang parser.

    Both parser implementations expose the same format-builder interface.

    ``frontend_context`` (ADR-050 D5, G32 Phase D) is only satisfiable by the
    clang backend (:func:`abicheck.sycl_context`'s host/device selector).
    An explicit ``--ast-frontend castxml`` with a non-``"host"`` request
    fails immediately rather than silently returning an ordinary castxml
    dump; under ``"auto"`` a non-``"host"`` request skips castxml entirely.
    """
    resolved = _resolve_single_ast_backend(backend, frontend_context)

    def _stamp_parser(
        parser: _CastxmlParser | _ClangAstParser,
        *,
        producer: str,
        executable: str,
        fallback_reason: str | None = None,
        resolved_compiler: str | None = None,
        resolved_force_cpp: bool | None = None,
    ) -> _CastxmlParser | _ClangAstParser:
        return cast(
            "_CastxmlParser | _ClangAstParser",
            _stamp_ast_parser(
                parser,
                producer=producer,
                executable=executable,
                compiler=compiler,
                gcc_path=gcc_path,
                gcc_prefix=gcc_prefix,
                fallback_reason=fallback_reason,
                resolved_compiler=resolved_compiler,
                resolved_force_cpp=resolved_force_cpp,
                gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
            ),
        )

    def _run_clang(*, fallback_reason: str | None = None) -> _ClangAstParser:
        clang_bin = _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
        ast_root, resolved_kind, resolved_force_cpp = _clang_header_dump(
            headers,
            extra_includes,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            extra_hash_dirs=extra_hash_dirs,
            frontend_context=frontend_context,
            pruning_header_roots=pruning_header_roots if pruning_header_roots is not None else tuple(public_header_paths + public_dir_paths),
        )
        parser = _ClangAstParser(
            ast_root,
            exported_dynamic,
            exported_static,
            public_header_paths=public_header_paths,
            public_dir_paths=public_dir_paths,
            target_triple=_configured_target_triple(
                gcc_options, gcc_option_tokens, clang_bin
            ),
        )
        stamped = cast(
            _ClangAstParser,
            _stamp_parser(
                parser,
                producer="clang",
                executable=clang_bin,
                fallback_reason=fallback_reason,
                resolved_force_cpp=resolved_force_cpp,
            ),
        )
        # ADR-050 D5: resolved SYCL kind, None for a plain clang dump --
        # read by dumper_contract._attach_extraction_contract.
        setattr(stamped, "_abicheck_frontend_context_kind", resolved_kind)
        return stamped

    if resolved == "clang" or frontend_context != "host":
        return _run_clang()

    # Auto mode may use the explicit opt-in fallback for known toolchain or
    # direct-inclusion failures. Explicit CastXML remains fail-closed.
    auto_selected = _auto_ast_fallback_eligible(backend)
    selected_castxml: list[str] = []
    selected_meta: list[tuple[str, bool]] = []
    try:
        xml_root = _castxml_dump(
            headers,
            extra_includes,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot,
            nostdinc=nostdinc,
            lang=lang,
            extra_hash_dirs=extra_hash_dirs,
            _selected_tool_out=selected_castxml,
            _selected_meta_out=selected_meta,
        )
    except SnapshotError as exc:
        fallback_reason = _castxml_fallback_reason(
            exc,
            auto_selected=auto_selected,
            compiler=compiler,
            gcc_path=gcc_path,
            gcc_prefix=gcc_prefix,
        )
        if fallback_reason is None:
            raise
        return _run_clang(fallback_reason=fallback_reason)
    parser = _CastxmlParser(
        xml_root,
        exported_dynamic,
        exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
    )
    meta = selected_meta[0] if selected_meta else (None, None)
    return cast(
        _CastxmlParser,
        _stamp_parser(
            parser,
            producer="castxml",
            executable=selected_castxml[0] if selected_castxml else "castxml",
            resolved_compiler=meta[0],
            resolved_force_cpp=meta[1],
        ),
    )


def _resolve_gated_castxml_bin(castxml_bin: str | None) -> str:
    """Resolve the castxml executable and fail closed on an out-of-policy build.

    The version gate (``castxml_policy``) runs *before* any header is parsed. An
    out-of-policy build (notably the legacy PyPI ``castxml`` distribution) is
    rejected unless the caller explicitly opted in via
    ``ABICHECK_ALLOW_UNSUPPORTED_CASTXML``. Skipped when the executable itself
    could not even be resolved/probed (``"error"`` key) — that is a different,
    pre-existing failure mode (missing/unreadable binary) that the actual castxml
    invocation reports precisely; this gate only judges a version it could
    actually observe.
    """
    try:
        resolved = castxml_bin or _resolve_selected_tool("castxml")
    except OSError as exc:
        raise SnapshotError(
            "castxml not found in PATH. Install with: apt install castxml, "
            "brew install castxml, conda install -c conda-forge castxml, "
            "or choco install castxml (Windows); then ensure castxml is in PATH. "
            "On a clang-only host, run with --ast-frontend clang (or "
            "ABICHECK_AST_FRONTEND=clang) to use the clang JSON-AST backend "
            "instead — note it does not carry record size/alignment/offset "
            "layout, so layout-only breaks need castxml or debug info (L1)."
        ) from exc
    meta = _tool_identity_metadata(resolved)
    if "error" not in meta:
        check = evaluate_castxml_version(meta.get("version", ""))
        if not check.supported and not _allow_unsupported_castxml_enabled():
            raise UnsupportedCastxmlVersionError(check.message(found_at=resolved))
    return resolved


def _read_castxml_cache(cached: Path) -> Element | None:
    """Parse a cached castxml XML tree, discarding the entry if it is unusable.

    Returns ``None`` (having unlinked *cached*) when the file cannot be parsed,
    so the caller falls through to a fresh run rather than failing on a
    truncated or corrupt cache entry.
    """
    try:
        root = DefusedET.parse(str(cached)).getroot()
    except Exception:
        root = None
    if root is None:
        cached.unlink(missing_ok=True)
        return None
    return cast(Element, root)


def _castxml_cpp_retry_allowed(
    primary: SnapshotError, *, force_cpp: bool, headers: list[Path]
) -> bool:
    """Whether a failed C-mode castxml run may be retried in C++ mode (G16/A3).

    An explicit ``--lang c`` on a header that actually requires C++ (a stray
    class/namespace/template, or C++20 concept/requires syntax — Codex review)
    should degrade to a C++ retry rather than hard-fail. No retry when we are
    already in C++ mode, when the failure is a frontend-too-old signature (a mode
    switch won't help), or when the header has no *genuinely C++-only* construct
    (``_CPP_ONLY_PATTERNS`` excludes ``extern "C"``: a guarded ``extern "C"``
    header is valid C, so a C-mode failure there is real and must NOT be masked
    by re-parsing as C++, which would skip the ``#ifndef __cplusplus`` branches —
    Codex review).
    """
    if force_cpp or _is_toolchain_version_failure(str(primary)):
        return False
    return _detect_cpp_headers(headers, _CPP_ONLY_PATTERNS) or _detect_cpp20_headers(
        headers
    )


def _write_castxml_cache(
    cached: Path,
    out_xml: Path,
    *,
    castxml_bin: str,
    cc_bin: str,
    frontend_identity: str,
    compiler_identity: str,
) -> None:
    """Persist a fresh castxml XML dump, unless the toolchain moved underneath us.

    The cache key encodes the frontend/compiler identities observed *before* the
    run; if either changed while castxml was executing, the produced XML no
    longer describes that key, so the write is skipped rather than poisoning the
    cache. A cache write that fails on I/O is a warning, never an error — the
    dump itself already succeeded.
    """
    if (
        _tool_identity(castxml_bin) != frontend_identity
        or _tool_identity(cc_bin) != compiler_identity
    ):
        log.warning(
            "AST toolchain changed during CastXML execution; skipping cache write"
        )
        return
    try:
        _atomic_write(cached, out_xml.read_bytes())
    except OSError as exc:
        log.warning("Could not write castxml AST cache %s: %s", cached, exc)


def _castxml_dump(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str = "c++",
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    extra_hash_dirs: tuple[Path, ...] = (),
    castxml_bin: str | None = None,
    _selected_tool_out: list[str] | None = None,
    _selected_meta_out: list[tuple[str, bool]] | None = None,
) -> Element:
    """Run castxml on headers and return parsed XML root.

    Args:
        compiler: "c++" (maps to g++) or "cc" (maps to gcc).
        gcc_path: Explicit path to a GCC/G++ cross-compiler binary.
        gcc_prefix: Cross-toolchain prefix (e.g. "aarch64-linux-gnu-").
        gcc_options: Extra compiler flags passed through to castxml.
        sysroot: Alternative system root directory.
        nostdinc: If True, do not search standard system include paths.
        lang: Force language ("C" or "C++").  If "C", aggregated header uses .h extension.
        _selected_meta_out: when given, appended with
            ``(resolved_compiler, resolved_force_cpp)`` -- the force_cpp-aware
            compiler spelling (e.g. ``"cc"``, not the caller's original
            ``"c++"``) actually used to pick ``cc_bin``, and the real,
            post-retry language mode that produced *root* (mirrors the clang
            backend's ``resolved_force_cpp`` return value). So a caller
            stamping provenance records what castxml actually invoked, not a
            re-derivation from the unresolved request (Codex review: a
            C-mode dump under the default ``compiler="c++"`` otherwise
            recorded ``g++``'s identity while castxml ran ``gcc``, and a
            self-healed retry otherwise still probed the pre-retry C
            default).
    """
    castxml_bin = _resolve_gated_castxml_bin(castxml_bin)
    if _selected_tool_out is not None:
        _selected_tool_out.append(castxml_bin)

    # Determine language before selecting the emulated compiler: C mode uses
    # gcc/cc, not g++, and both cache identity and execution must describe the
    # same driver.
    force_cpp = _resolve_force_cpp(lang, headers, gcc_options, gcc_option_tokens)
    # Same expression _run_castxml_attempt uses for its (non-retry) call below —
    # folded into the cache key ahead of time so the resolved dialect decision,
    # not just the explicit --lang, invalidates a stale cache entry (Codex
    # review).
    force_cpp20 = force_cpp and _detect_cpp20_headers(headers)
    resolved_compiler = compiler
    if not force_cpp and not gcc_path and not gcc_prefix:
        resolved_compiler = {
            "c++": "cc",
            "g++": "gcc",
            "clang++": "clang",
        }.get(compiler, compiler)
    cc_bin, cc_id = _resolve_compiler_binary(resolved_compiler, gcc_path, gcc_prefix)
    # Freeze PATH selection for the actual CastXML invocation. Keep an explicit
    # unresolved path/name intact so CastXML can provide its native diagnostic.
    cc_bin = shutil.which(cc_bin) or cc_bin
    frontend_identity = _tool_identity(castxml_bin)
    compiler_identity = _tool_identity(cc_bin)

    # Check disk cache
    key = _cache_key(
        headers,
        extra_includes,
        compiler,
        gcc_path=gcc_path,
        gcc_prefix=gcc_prefix,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot,
        nostdinc=nostdinc,
        lang=lang,
        extra_hash_dirs=extra_hash_dirs,
        frontend_identity=frontend_identity,
        compiler_identity=compiler_identity,
        force_cpp20=force_cpp20,
    )
    cached = _cache_path(key)
    if cached.exists():
        # Same reasoning as the clang cache-hit path (_clang_header_dump, Codex review).
        deadline.check()
        _cached_root = _read_castxml_cache(cached)
        if _cached_root is not None:
            deadline.check()  # parsing a huge cached tree can eat the rest of the budget
            if _selected_meta_out is not None:
                _selected_meta_out.append((resolved_compiler, force_cpp))
            return _cached_root

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        out_xml = Path(tmp.name)

    final_force_cpp = force_cpp
    try:
        try:
            root = _run_castxml_attempt(
                cc_bin,
                cc_id,
                headers,
                extra_includes,
                out_xml,
                sysroot=sysroot,
                nostdinc=nostdinc,
                gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
                force_cpp=force_cpp,
                castxml_bin=castxml_bin,
            )
        except SnapshotError as primary:
            if not _castxml_cpp_retry_allowed(
                primary, force_cpp=force_cpp, headers=headers
            ):
                raise
            log.warning(
                "castxml failed to parse the header(s) under --lang c; the header "
                "contains C++-only constructs (class / namespace / template), so "
                "retrying in C++ mode. Pass --lang c++ to select this directly and "
                "silence this warning."
            )
            try:
                root = _run_castxml_attempt(
                    cc_bin,
                    cc_id,
                    headers,
                    extra_includes,
                    out_xml,
                    sysroot=sysroot,
                    nostdinc=nostdinc,
                    gcc_options=gcc_options,
                    gcc_option_tokens=gcc_option_tokens,
                    force_cpp=True,
                    castxml_bin=castxml_bin,
                )
                final_force_cpp = True
            except SnapshotError:
                # Both modes failed — surface the originally requested C-mode
                # error (and its hint), not the fallback's, so the diagnostic
                # matches what the user asked for.
                raise primary from None
        _write_castxml_cache(
            cached,
            out_xml,
            castxml_bin=castxml_bin,
            cc_bin=cc_bin,
            frontend_identity=frontend_identity,
            compiler_identity=compiler_identity,
        )
        # Re-reading/caching a huge fresh tree can itself consume real time;
        # re-check before returning (mirrors _validate_castxml_output's pre-cache-write check, Codex review).
        deadline.check()
        if _selected_meta_out is not None:
            _selected_meta_out.append((resolved_compiler, final_force_cpp))
        return root
    finally:
        out_xml.unlink(missing_ok=True)


def _run_castxml_attempt(
    cc_bin: str,
    cc_id: str,
    headers: list[Path],
    extra_includes: list[Path],
    out_xml: Path,
    *,
    sysroot: Path | None,
    nostdinc: bool,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool,
    castxml_bin: str = "castxml",
) -> Element:
    """Run one castxml invocation in a fixed language mode and parse its output.

    Writes the aggregate ``#include`` header (``.h`` for C, ``.hpp`` for C++),
    builds and runs the castxml command, and validates the result. Raises
    :class:`SnapshotError` on a non-zero exit, a timeout, or empty/invalid XML —
    leaving *out_xml* in place on success so the caller can cache it. The agg
    header is always cleaned up. Factored out of :func:`_castxml_dump` so the
    C→C++ fallback (G16/A3) can re-run with a different mode without duplicating
    the run/validate plumbing.
    """
    # Detect C++20 concept / requires syntax — castxml's default standard
    # (typically C++17) rejects these, so we override it. Only in C++ mode.
    force_cpp20 = force_cpp and _detect_cpp20_headers(headers)
    agg_ext = ".hpp" if force_cpp else ".h"

    with tempfile.NamedTemporaryFile(suffix=agg_ext, mode="w", delete=False) as agg:
        for h in headers:
            agg.write(f'#include "{h.resolve()}"\n')
        agg_path = Path(agg.name)

    cmd = _build_castxml_command(
        cc_bin,
        cc_id,
        extra_includes,
        out_xml,
        agg_path,
        sysroot=sysroot,
        nostdinc=nostdinc,
        gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        force_cpp=force_cpp,
        force_cpp20=force_cpp20,
        castxml_bin=castxml_bin,
    )

    try:
        deadline.check()  # propagates uncaught, like _clang_header_dump._run_clang
        try:
            result = deadline.run_bounded(
                cmd, capture_output=True, text=True, timeout=120
            )
        except subprocess.TimeoutExpired as exc:
            stderr_snippet = ""
            if exc.stderr:
                text = (
                    exc.stderr
                    if isinstance(exc.stderr, str)
                    else exc.stderr.decode("utf-8", errors="replace")
                )
                stderr_snippet = f"\nPartial stderr: {text[:1000].strip()}"
            raise SnapshotError(
                f"castxml timed out after 120 seconds. The header file may contain "
                f"syntax that causes the compiler to hang. Check that the header "
                f"is valid and can be compiled with gcc/g++. The castxml process "
                f"(and any child processes) has been terminated.{stderr_snippet}"
            ) from exc
        return _validate_castxml_output(
            result, out_xml, headers, force_cpp, castxml_bin=castxml_bin
        )
    finally:
        agg_path.unlink(missing_ok=True)


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
            recording cheap debug-info presence. Used by ``scan --depth binary``.
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
            The CLI's ``dump`` command passes its own raw ``-H``/``--header``
            directory arguments here (``cli_dump_helpers.perform_elf_dump``).
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

    if _resolve_header_backend(header_backend) == "hybrid":
        if dump_manifest is not None:
            raise ValidationError(
                "dump_manifest is not yet supported with the 'hybrid' AST "
                "frontend; pass an explicit --ast-frontend castxml/clang."
            )
        if frontend_context != "host":
            # Hybrid has no device concept (castxml+clang merge); reject
            # rather than silently defaulting both recursive calls to "host".
            raise AstContextMissingError(
                f"--frontend-context {frontend_context!r} requires the "
                "clang header backend (--ast-frontend clang); 'hybrid' "
                "merges castxml+clang and has no device-context semantics."
            )
        from .dumper_hybrid import run_hybrid_dump

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
    from .provenance import apply_provenance

    return apply_provenance(
        snapshot,
        effective_public_headers,
        effective_public_header_dirs,
        include_search_dirs=public_include_search_dirs,
    )


def _lang_to_profile(lang: str | None) -> str | None:
    """Convert a ``--lang`` flag value to an internal language-profile string.

    Shared by the ELF/PE/Mach-O snapshot builders (C3) — previously this logic
    was a helper for ELF but copy-pasted inline for the other two formats.
    """
    if lang is None:
        return None
    lu = lang.upper()
    if lu == "C":
        return "c"
    if lu in ("C++", "CPP"):
        return "cpp"
    return None


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
    :func:`abicheck.dumper_manifest.resolve_header_ast_result`, replacing
    the single flat *headers*/*extra_includes* parse. *headers* must be
    empty in this case (enforced by :func:`dump`). PE/Mach-O reject a
    non-``None`` value outright (not yet supported there).
    """
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
        profile_hint = _lang_to_profile(lang)
        # ADR-003 fallback chain: --dwarf-only forces DWARF mode; no headers +
        # DWARF -> DWARF-only mode; no headers + no DWARF -> symbols-only. Both
        # legs gated on resolved_debug_format, not dwarf_meta.has_dwarf (which
        # mirrors BTF/CTF presence too, and --dwarf-only + --debug-format
        # btf/ctf resolves no real DWARF either — Codex review, twice).
        if dwarf_only and resolved_debug_format != "dwarf":
            warnings.warn(
                f"--dwarf-only requested but resolved debug format is {resolved_debug_format!r}; ignoring.",
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
            )
        # Built here (session open): "auto" can fall back to clang (G16), so
        # ast_result.is_clang is the only reliable signal (Codex review).
        from .dumper_manifest import resolve_header_ast_result

        ast_result = resolve_header_ast_result(
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
                dwarf_adv,
                ast_result.is_clang,
                symbols_only=symbols_only,
                debug_presence_only=debug_presence_only,
                debug_format=resolved_debug_format,
                version=version,
                language_profile=profile_hint,
                session=dwarf_session,
            )
        )
    finally:
        for _sess in _dwarf_session_out:
            _sess.close()

    _backfilled_types, _layout_coherence = backfill_dwarf_layout(
        list(ast_result.types), dwarf_layout_types
    )
    _dwarf_layout_coherence, _dwarf_layout_coherence_mismatches = (
        resolve_snapshot_layout_coherence(
            is_clang_backend=ast_result.is_clang and not _is_device_context,
            coherence=_layout_coherence,
        )
    )

    _so_mtime, _so_mtime_epoch = _safe_mtime(so_path)
    snapshot = AbiSnapshot(
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
        **_ast_compile_provenance(list(ast_result.provenance_headers), gcc_options, gcc_option_tokens, sysroot, ast_toolchain=ast_result.ast_toolchain, lang=lang),
    )
    _populate_elf_visibility(snapshot)
    return qualified_name_segments.renumber_anonymous_closure_identities(snapshot)


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
    if dump_manifest is not None:
        raise ValidationError(
            "--dump-manifest is not yet supported for Mach-O binaries "
            "(ADR-050 D3); use a single-header dump for this format."
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

    profile_hint = _lang_to_profile(lang)

    if not headers:
        # Advisory only (ADR-035 P6): info log, not a per-run UserWarning.
        log.info(
            "No headers provided — only Mach-O exported symbols will be captured; "
            "type information will be missing."
        )

        # Normalize Mach-O leading underscore: _foo → foo, __Z... → _Z...
        def _normalize_macho_sym(s: str) -> str:
            if s.startswith("_"):
                return s[1:]
            return s

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
        return AbiSnapshot(
            library=dylib_path.name,
            version=version,
            source_path=str(dylib_path.resolve()),
            source_mtime=_dylib_mtime,
            source_mtime_epoch=_dylib_mtime_epoch,
            source_size=_safe_size(dylib_path),
            functions=[
                _itanium_export_function(_normalize_macho_sym(exp.name))
                for exp in sorted(macho_funcs, key=lambda e: e.name)
            ],
            variables=[
                _itanium_export_variable(_normalize_macho_sym(exp.name))
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
    parser = _header_ast_parser(
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
    return qualified_name_segments.renumber_anonymous_closure_identities(AbiSnapshot(
        library=dylib_path.name,
        version=version,
        source_path=str(dylib_path.resolve()),
        source_mtime=_dylib_mtime,
        source_mtime_epoch=_dylib_mtime_epoch,
        source_size=_safe_size(dylib_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        typedefs_qualified=parser.parse_typedefs_qualified(),
        constants=parser.parse_constants(),
        typedef_entity_ids=parser.parse_typedef_entity_ids(),
        constant_entity_ids=parser.parse_constant_entity_ids(),
        macho=macho_meta,
        # Reached only when headers were supplied and castxml ran (the no-header
        # branch returns earlier): this surface is header-parsed.
        from_headers=True,
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
        frontend_context_kind=_parser_frontend_context_kind(parser),
        platform="macho",
        language_profile=profile_hint,
        **_ast_compile_provenance(headers, gcc_options, gcc_option_tokens, sysroot, ast_toolchain=_parser_ast_toolchain(parser), lang=lang),
    ))


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
    if dump_manifest is not None:
        raise ValidationError(
            "--dump-manifest is not yet supported for PE binaries "
            "(ADR-050 D3); use a single-header dump for this format."
        )
    from .pe_metadata import parse_pe_metadata

    pe_meta = parse_pe_metadata(dll_path)
    exported_dynamic: set[str] = {
        (exp.name or f"ordinal:{exp.ordinal}") for exp in pe_meta.exports
    }
    exported_static: set[str] = set(exported_dynamic)

    profile_hint = _lang_to_profile(lang)

    if not headers:
        # Advisory only (ADR-035 P6): info log, not a per-run UserWarning.
        log.info(
            "No headers provided — only PE exported symbols will be captured; "
            "type information will be missing."
        )
        _dll_mtime, _dll_mtime_epoch = _safe_mtime(dll_path)
        return AbiSnapshot(
            library=dll_path.name,
            version=version,
            source_path=str(dll_path.resolve()),
            source_mtime=_dll_mtime,
            source_mtime_epoch=_dll_mtime_epoch,
            source_size=_safe_size(dll_path),
            # ADR-063 Phase 2: see extract.export_symbol_identity's own docstring.
            functions=[_msvc_export_function(sym) for sym in sorted(exported_dynamic)],
            pe=pe_meta,
            elf_only_mode=True,
            platform="pe",
            language_profile=profile_hint,
        )

    parser = _header_ast_parser(
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
    return qualified_name_segments.renumber_anonymous_closure_identities(AbiSnapshot(
        library=dll_path.name,
        version=version,
        source_path=str(dll_path.resolve()),
        source_mtime=_dll_mtime,
        source_mtime_epoch=_dll_mtime_epoch,
        source_size=_safe_size(dll_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        typedefs_qualified=parser.parse_typedefs_qualified(),
        constants=parser.parse_constants(),
        typedef_entity_ids=parser.parse_typedef_entity_ids(),
        constant_entity_ids=parser.parse_constant_entity_ids(),
        pe=pe_meta,
        # Reached only when headers were supplied and castxml ran (the no-header
        # branch returns earlier): this surface is header-parsed.
        from_headers=True,
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
        frontend_context_kind=_parser_frontend_context_kind(parser),
        platform="pe",
        language_profile=profile_hint,
        **_ast_compile_provenance(headers, gcc_options, gcc_option_tokens, sysroot, ast_toolchain=_parser_ast_toolchain(parser), lang=lang),
    ))


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
