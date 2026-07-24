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

import json
import logging
import os
import shlex
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
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .dwarf_unified import DwarfSession
    from .elf_metadata import ElfMetadata

from defusedxml import ElementTree as DefusedET

from . import deadline
from ._compiler_options import has_explicit_cpp_std, has_explicit_std
from .castxml_policy import evaluate_castxml_version
from .dumper_ast_config import (
    _CPP_ONLY_PATTERNS as _CPP_ONLY_PATTERNS,
    _build_castxml_command as _build_castxml_command,
    _cache_key as _cache_key,
    _detect_cpp20_headers as _detect_cpp20_headers,
    _detect_cpp_headers as _detect_cpp_headers,
    _resolve_compiler_binary as _resolve_compiler_binary,
)
from .dumper_cache import _atomic_write, _cache_path
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
    _resolve_clang_bin as _resolve_clang_bin,
)
from .dumper_clang_errors import (
    _is_direct_include_guard_failure,
    _is_missing_cpp_stdlib_header_error,
    _parse_clang_ast_result,
    retry_excluding_error_headers,
    run_clang_to_ast_file,
)
from .dumper_debug import (
    # DWARF/BTF/CTF format resolution + the kernel-binary heuristic live in the
    # sibling module (dumper.py is at the file-size cap); re-exported here so
    # ``dumper._is_kernel_binary`` / ``dumper._resolve_debug_metadata`` remain
    # valid bare-name calls in ``_dump_elf`` and test patch targets.
    _is_kernel_binary as _is_kernel_binary,
    _resolve_debug_metadata as _resolve_debug_metadata,
)
from .dumper_layout_backfill import backfill_dwarf_layout, dwarf_layout_types_or_empty
from .dumper_sysinc import (
    _auto_system_includes_enabled as _auto_system_includes_enabled,
    _parse_gnu_include_search_dirs as _parse_gnu_include_search_dirs,
    _probe_gnu_system_includes as _probe_gnu_system_includes,
    _resolve_clang_system_includes as _resolve_clang_system_includes,
    _resolve_probe_compiler as _resolve_probe_compiler,
)
from .dumper_toolchain import (
    _allow_unsupported_castxml_enabled as _allow_unsupported_castxml_enabled,
    _ast_fallback_enabled as _ast_fallback_enabled,
    _auto_ast_fallback_eligible as _auto_ast_fallback_eligible,
    _castxml_available as _castxml_available,
    _parser_ast_fallback_reason as _parser_ast_fallback_reason,
    _parser_ast_supported as _parser_ast_supported,
    _parser_ast_toolchain as _parser_ast_toolchain,
    _parser_ast_unsupported_reasons as _parser_ast_unsupported_reasons,
    _resolve_selected_tool as _resolve_selected_tool,
    _safe_mtime as _safe_mtime,
    _safe_size as _safe_size,
    _tool_identity as _tool_identity,
    _tool_identity_metadata as _tool_identity_metadata,
)
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .errors import SnapshotError, UnsupportedCastxmlVersionError, ValidationError
from .model import (
    AbiSnapshot,
    ElfVisibility,
    Function,
    RecordType,
    Variable,
    Visibility,
    is_cxx_runtime_library,
)

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


def _build_clang_header_command(
    cc_bin: str,
    cc_id: str,
    extra_includes: list[Path],
    agg_path: Path,
    *,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool = False,
    force_cpp20: bool = False,
    system_includes: tuple[str, ...] = (),
) -> list[str]:
    """Build the ``clang -ast-dump=json`` command for the aggregate header.

    Mirrors :func:`_build_castxml_command`'s flag handling (includes, sysroot,
    ``-nostdinc``, pass-through options, C-vs-C++ language mode and the C++20
    bump) so the clang backend parses the same TU under the same context — it is
    just a different frontend over the identical inputs. ``-fsyntax-only`` (no
    codegen) with ``-ferror-limit=0`` keeps parsing past recoverable errors so a
    single bad decl does not blank the whole dump.

    ``system_includes`` are host-compiler-probed system dirs (see
    :func:`_probe_gnu_system_includes`) injected as ``-isystem`` so clang finds
    the same libstdc++/libc headers castxml gets via ``--castxml-cc-gnu`` — the
    castxml↔clang capability-parity fix. They are emitted **last** (after the
    user's ``-I`` *and* the pass-through ``--gcc-options``/``--gcc-option``) so
    auto-detection stays a genuine fallback: a user-supplied ``-isystem`` for a
    cross/hermetic SDK is searched first and wins. Skipped under ``-nostdinc``.
    """
    cmd = [cc_bin]
    for inc in extra_includes:
        cmd += ["-I", str(inc)]
    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += shlex.split(gcc_options, posix=os.name != "nt")
    # Repeatable --gcc-option: one literal argument each (no shlex split).
    cmd += list(gcc_option_tokens)
    # Auto-probed host system dirs go *after* the user's pass-through flags, so a
    # user-supplied -isystem (cross/hermetic SDK) keeps higher search priority
    # (Codex review). Auto-detection is a fallback, never an override.
    for sysinc in system_includes:
        cmd += ["-isystem", sysinc]
    explicit_std = has_explicit_std(gcc_options, gcc_option_tokens)
    if not force_cpp:
        if not explicit_std:
            cmd += ["-x", "c", "-std=gnu11"]
    elif not explicit_std:
        # Select the C++ language explicitly (``-x c++``) rather than relying on
        # the aggregate file's extension: the C→C++ retry reuses a ``.h`` aggregate
        # that clang would otherwise parse as C. Only bump the standard to gnu++20
        # when C++20 syntax was detected; otherwise leave clang's default dialect.
        cmd += ["-x", "c++"]
        if force_cpp20:
            cmd += ["-std=gnu++20"]
    cmd += [
        "-fsyntax-only",
        "-ferror-limit=0",
        "-Xclang",
        "-ast-dump=json",
        str(agg_path),
    ]
    return cmd


def _resolve_force_cpp(
    lang: str | None,
    headers: list[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
) -> bool:
    """Decide whether the TU is C++ when no ``lang`` was explicitly given.

    An explicit ``--lang c++``/``cpp`` always wins. Otherwise, C++20
    concept/requires syntax (including an abbreviated constrained parameter
    like ``void f(std::integral auto x);``, which needs no
    class/namespace/template keyword at all) is on its own sufficient proof
    the header is C++ — without this, a header whose only C++ signal is such
    syntax stayed auto-detected as C (Codex review). Shared by both the clang
    and castxml frontends so the auto-detection rule cannot drift between
    them.

    ``for_language_mode_decision=True`` (Codex review): a
    ``#if __cplusplus``/``#ifdef __cplusplus``-guarded C++20 construct
    must not by itself promote an auto-detected header to C++ mode — in C
    mode ``__cplusplus`` is undefined, so that guard's content is not
    actually reachable there, and forcing C++ purely because it exists
    would then turn an *active*, unguarded use of the same word as an
    ordinary C identifier elsewhere in the header into a reserved-word
    parse error once C++20 mode is wrongly forced.
    """
    if lang:
        return bool(lang.upper() in ("C++", "CPP"))
    return (
        _detect_cpp_headers(headers)
        or _detect_cpp20_headers(headers, for_language_mode_decision=True)
        or has_explicit_cpp_std(gcc_options, gcc_option_tokens)
    )


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
) -> dict[str, Any]:
    """Run clang over *headers* and return the parsed ``-ast-dump=json`` root.

    The clang-frontend counterpart of :func:`_castxml_dump`: it aggregates the
    headers into one ``#include`` TU, runs ``clang -ast-dump=json``, and returns
    the JSON dict that :class:`abicheck.dumper_clang._ClangAstParser` consumes.
    Results are disk-cached (keyed on header mtimes + toolchain + backend) like
    the castxml path. Raises :class:`SnapshotError` when clang is missing, times
    out, or emits no usable AST.
    """
    clang_bin = _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
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
    )
    cached = _cache_path(key, backend="clang")
    if cached.exists():
        # A cache hit still costs time parsing a potentially huge AST (Codex review).
        deadline.check()
        try:
            _cached_result = cast(
                "dict[str, Any]", json.loads(cached.read_text(encoding="utf-8"))
            )
        except (ValueError, OSError):
            cached.unlink(missing_ok=True)
        else:
            # The load itself can consume the rest of the budget on a huge
            # cached AST; re-check before handing it to the AST walker
            # (Codex review, PR #591, round 3).
            deadline.check()
            return _cached_result

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
        return _parse_clang_ast_result(
            result, cached, _ast_paths[-1], cache_write=identities_stable
        )
    finally:
        agg_path.unlink(missing_ok=True)
        for _p in _ast_paths:
            _p.unlink(missing_ok=True)


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
) -> _CastxmlParser | _ClangAstParser:
    """Run the resolved L2 backend and return its CastXML/Clang parser.

    Both parser implementations expose the same format-builder interface.
    """
    resolved = _resolve_header_backend(backend)
    if resolved == "hybrid":
        # No single parser exists for "hybrid" — must be resolved by
        # dumper_hybrid.run_hybrid_dump, not silently treated as castxml.
        raise ValidationError(
            '"hybrid" AST frontend has no single parser here '
            "(see dumper_hybrid.run_hybrid_dump)."
        )

    def _stamp_parser(
        parser: _CastxmlParser | _ClangAstParser,
        *,
        producer: str,
        executable: str,
        fallback_reason: str | None = None,
    ) -> _CastxmlParser | _ClangAstParser:
        metadata = {"producer": producer, **_tool_identity_metadata(executable)}
        if producer == "clang":
            compiler_meta = _tool_identity_metadata(executable)
        else:
            try:
                host_cc, _ = _resolve_compiler_binary(compiler, gcc_path, gcc_prefix)
                compiler_meta = _tool_identity_metadata(host_cc)
            except SnapshotError as exc:
                metadata["compiler_error"] = str(exc)
                compiler_meta = {}
        metadata.update(
            {f"compiler_{key}": value for key, value in compiler_meta.items()}
        )
        setattr(parser, "_abicheck_ast_toolchain", metadata)
        setattr(parser, "_abicheck_ast_fallback_reason", fallback_reason)
        if producer == "castxml":
            check = evaluate_castxml_version(metadata.get("version", ""))
            setattr(parser, "_abicheck_ast_supported", check.supported)
            setattr(parser, "_abicheck_ast_unsupported_reasons", check.reasons)
        return parser

    def _run_clang(*, fallback_reason: str | None = None) -> _ClangAstParser:
        clang_bin = _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
        ast_root = _clang_header_dump(
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
        )
        parser = _ClangAstParser(
            ast_root,
            exported_dynamic,
            exported_static,
            public_header_paths=public_header_paths,
            public_dir_paths=public_dir_paths,
        )
        return cast(
            _ClangAstParser,
            _stamp_parser(
                parser,
                producer="clang",
                executable=clang_bin,
                fallback_reason=fallback_reason,
            ),
        )

    if resolved == "clang":
        return _run_clang()

    # Auto mode may use the explicit opt-in fallback for known toolchain or
    # direct-inclusion failures. Explicit CastXML remains fail-closed.
    auto_selected = _auto_ast_fallback_eligible(backend)
    selected_castxml: list[str] = []
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
        )
    except SnapshotError as exc:
        # Probe the driver _run_clang() would actually invoke (honors
        # --gcc-path/--gcc-prefix), not just a bare "clang" on PATH (Codex
        # review).
        def _clang_fallback_ready() -> bool:
            try:
                _resolve_clang_bin(compiler, gcc_path, gcc_prefix)
                return True
            except SnapshotError:
                return False

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
        if (
            auto_selected
            and _ast_fallback_enabled()
            and _clang_fallback_ready()
            and (
                is_version_gate_failure
                or _is_toolchain_version_failure(str(exc))
                or _is_direct_include_guard_failure(str(exc))
            )
        ):
            log.warning(
                "castxml could not parse the header(s) (toolchain mismatch, an "
                "unsupported castxml version, or a header that refuses direct "
                "inclusion); falling back to the clang header backend, which "
                "parses against the host toolchain and can exclude direct-include "
                "#error guard headers. Set --ast-frontend castxml to force castxml "
                "and see the original error."
            )
            fallback_reason = (
                "castxml-unsupported-version"
                if is_version_gate_failure
                else "castxml-toolchain-version-mismatch"
                if _is_toolchain_version_failure(str(exc))
                else "castxml-direct-include-guard"
            )
            return _run_clang(fallback_reason=fallback_reason)
        if auto_selected and (
            is_version_gate_failure
            or _is_toolchain_version_failure(str(exc))
            or _is_direct_include_guard_failure(str(exc))
        ):
            message = (
                f"{exc}\n\nAutomatic CastXML-to-Clang fallback is disabled because "
                "the two frontends can produce materially different findings. "
                "Install a compatible CastXML, select --ast-frontend clang "
                "explicitly, or opt in with --allow-ast-frontend-fallback "
                "(ABICHECK_ALLOW_AST_FALLBACK=1)."
            )
            raise type(exc)(message) from exc
        raise
    parser = _CastxmlParser(
        xml_root,
        exported_dynamic,
        exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
    )
    return cast(
        _CastxmlParser,
        _stamp_parser(
            parser,
            producer="castxml",
            executable=selected_castxml[0] if selected_castxml else "castxml",
        ),
    )


_HIDDEN_VIS = frozenset({"STV_HIDDEN", "STV_INTERNAL"})


def _is_abi_relevant_symbol(name: str) -> bool:
    """Return False for symbols that are NOT part of the library's public ABI.

    Filters out (in ELF-only mode):
    1. GCC/compiler internal symbols (``ix86_*``, ``_ZGV*``, ``__svml_*`` …)
       that leak into ``.dynsym`` through a statically-linked runtime.
    2. Transitive C++ stdlib symbols (``_ZNSt*``, ``_ZTI*`` …) that appear
       in ``.dynsym`` via weak linkage from libstdc++ / libc++.
    3. Private C symbols that use ``__`` as a namespace separator
       (e.g. ``H5C__flush``, ``MPI__send``).  These follow an internal
       naming convention and are *not* part of the public API, even though
       they may have global ELF visibility.
    """
    return is_abi_relevant_elf_symbol(name)


def _pyelftools_exported_symbols(so_path: Path) -> tuple[set[str], set[str]]:
    """Return (exported_dynamic, exported_static) sets of mangled symbol names.

    Uses pyelftools (pure Python) instead of shelling out to readelf.
    - exported_dynamic: symbols from .dynsym, truly exported via ELF
    - exported_static: symbols from .symtab (all symbols including static)
    """
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection

    def _extract_symbols(elf: Any, section_name: str) -> set[str]:
        syms: set[str] = set()
        section = elf.get_section_by_name(section_name)
        if section is None or not isinstance(section, SymbolTableSection):
            return syms
        for sym in section.iter_symbols():
            shndx = sym.entry.st_shndx
            if shndx in ("SHN_UNDEF", "SHN_ABS"):
                continue
            bind = sym.entry.st_info.bind
            vis = sym.entry.st_other.visibility
            if bind in ("STB_GLOBAL", "STB_WEAK") and vis not in _HIDDEN_VIS:
                name = sym.name
                if name and _is_abi_relevant_symbol(name):
                    syms.add(name)
        return syms

    try:
        with open(so_path, "rb") as f:
            elf: Any = ELFFile(f)  # type: ignore[no-untyped-call]
            exported_dynamic = _extract_symbols(elf, ".dynsym")
            try:
                exported_static = _extract_symbols(elf, ".symtab")
            except (ELFError, OSError):
                exported_static = set(exported_dynamic)
            return exported_dynamic, exported_static
    except (ELFError, OSError) as exc:
        raise SnapshotError(f"Failed to parse ELF file {so_path}: {exc}") from exc


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
    """
    try:
        castxml_bin = castxml_bin or _resolve_selected_tool("castxml")
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
    if _selected_tool_out is not None:
        _selected_tool_out.append(castxml_bin)

    # CastXML version gate (castxml_policy) — fail closed *before* any header
    # is parsed. An out-of-policy build (notably the legacy PyPI ``castxml``
    # distribution) is rejected unless the caller explicitly opted in via
    # ABICHECK_ALLOW_UNSUPPORTED_CASTXML. Skipped when the executable itself
    # could not even be resolved/probed (``"error"`` key) — that is a
    # different, pre-existing failure mode (missing/unreadable binary) that
    # the actual castxml invocation below already reports precisely; this
    # gate only judges a version it could actually observe.
    _castxml_meta = _tool_identity_metadata(castxml_bin)
    if "error" not in _castxml_meta:
        _version_check = evaluate_castxml_version(_castxml_meta.get("version", ""))
        if not _version_check.supported and not _allow_unsupported_castxml_enabled():
            raise UnsupportedCastxmlVersionError(
                _version_check.message(found_at=castxml_bin)
            )

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
        try:
            _cached_root = DefusedET.parse(str(cached)).getroot()
        except Exception:
            _cached_root = None
        if _cached_root is None:
            cached.unlink(missing_ok=True)
        else:
            # The parse itself can consume the rest of the budget on a huge
            # cached XML tree; re-check before handing it off (Codex review,
            # PR #591, round 3).
            deadline.check()
            return cast(Element, _cached_root)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        out_xml = Path(tmp.name)

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
            # G16/A3: an explicit ``--lang c`` on a header that actually requires
            # C++ (a stray class/namespace/template, or C++20 concept/requires
            # syntax — Codex review) should degrade to a C++ retry rather than
            # hard-fail. Skip the retry when we are already in C++ mode, when
            # the failure is a frontend-too-old signature (a mode switch won't
            # help), or when the header has no *genuinely C++-only* construct
            # (``_CPP_ONLY_PATTERNS`` excludes ``extern "C"``: a guarded
            # ``extern "C"`` header is valid C, so a C-mode failure there is real
            # and must NOT be masked by re-parsing as C++, which would skip the
            # ``#ifndef __cplusplus`` branches — Codex review).
            if (
                force_cpp
                or _is_toolchain_version_failure(str(primary))
                or not (
                    _detect_cpp_headers(headers, _CPP_ONLY_PATTERNS)
                    or _detect_cpp20_headers(headers)
                )
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
            except SnapshotError:
                # Both modes failed — surface the originally requested C-mode
                # error (and its hint), not the fallback's, so the diagnostic
                # matches what the user asked for.
                raise primary from None
        if (
            _tool_identity(castxml_bin) != frontend_identity
            or _tool_identity(cc_bin) != compiler_identity
        ):
            log.warning(
                "AST toolchain changed during CastXML execution; skipping cache write"
            )
        else:
            try:
                _atomic_write(cached, out_xml.read_bytes())
            except OSError as exc:
                log.warning("Could not write castxml AST cache %s: %s", cached, exc)
        # Re-reading the whole XML file (read_bytes) and writing the cache copy
        # can itself consume real time on a huge fresh tree; re-check before
        # handing the already-parsed root back to the caller, mirroring the
        # pre-cache-write check in _validate_castxml_output (Codex review,
        # PR #591, round 10).
        deadline.check()
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

    Returns:
        AbiSnapshot with functions, variables, and types populated.
    """
    if _resolve_header_backend(header_backend) == "hybrid":
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
        public_headers=public_headers,
        public_header_dirs=public_header_dirs,
        header_backend=header_backend,
        extra_hash_dirs=extra_hash_dirs,
        **extra,
    )

    # Note: from_headers (the HEADER_AWARE evidence-tier signal) is set by the
    # format-specific builders (_dump_elf / _dump_pe / _dump_macho) at the point
    # castxml actually parses headers, so every entry point — including the CLI
    # and service native-binary paths that call those builders directly (e.g.
    # service._try_header_scoped_dump), bypassing this function — records it
    # correctly. DWARF-only and symbols-only builds leave it False.

    # Tag declaration provenance (source_header + origin). Always derives
    # source_header from the parsed source location; origin is only
    # classified when a public-header set is supplied (ADR-015, D4).
    from .provenance import apply_provenance

    return apply_provenance(snapshot, public_headers, public_header_dirs)


_ELF_VIS_MAP: dict[str, ElfVisibility] = {
    "default": ElfVisibility.DEFAULT,
    "protected": ElfVisibility.PROTECTED,
    "hidden": ElfVisibility.HIDDEN,
    "internal": ElfVisibility.INTERNAL,
}


def _populate_elf_visibility(snap: AbiSnapshot) -> None:
    """Populate elf_visibility on Function/Variable from ELF metadata symbols."""
    if snap.elf is None:
        return
    sym_map = snap.elf.symbol_map
    for func in snap.functions:
        elf_sym = sym_map.get(func.mangled)
        if elf_sym is not None:
            func.elf_visibility = _ELF_VIS_MAP.get(elf_sym.visibility)
    for var in snap.variables:
        elf_sym = sym_map.get(var.mangled)
        if elf_sym is not None:
            var.elf_visibility = _ELF_VIS_MAP.get(elf_sym.visibility)


def _elf_classify_symbols(
    elf_meta: ElfMetadata,
    exported_dynamic: set[str],
    *,
    library_name: str | None = None,
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Split ELF metadata symbols into typed subsets for the no-header path.

    Returns ``(exported_dynamic, funcs, objects, tls)`` where *exported_dynamic*
    may be the original fallback set when *elf_meta* has no symbols.
    """
    from .elf_metadata import SymbolType

    exported_dynamic_funcs: set[str] = exported_dynamic  # fallback
    exported_dynamic_objects: set[str] = set()
    exported_dynamic_tls: set[str] = set()
    if elf_meta.symbols:
        runtime_name = elf_meta.soname or library_name
        filter_transitive_runtime_symbols = not is_cxx_runtime_library(runtime_name)
        # Apply the shared ABI-relevance filter here too: this no-header path
        # rebuilds the exported sets directly from ``elf_meta.symbols`` rather
        # than the already-filtered ``_pyelftools_exported_symbols`` result, so
        # lifecycle stubs (``_init``/``_fini``) and transitive runtime symbols
        # would otherwise re-enter the symbol-only ABI surface as ELF_ONLY
        # functions. Keeping it consistent with the DWARF-backed path.
        exported_dynamic_funcs = {
            sym.name
            for sym in elf_meta.symbols
            if sym.sym_type in (SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE)
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        exported_dynamic_objects = {
            sym.name
            for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.OBJECT
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        exported_dynamic_tls = {
            sym.name
            for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.TLS
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        # Full set for CastxmlParser: determines PUBLIC vs ELF_ONLY visibility
        exported_dynamic = (
            exported_dynamic_funcs | exported_dynamic_objects | exported_dynamic_tls
        )
    return (
        exported_dynamic,
        exported_dynamic_funcs,
        exported_dynamic_objects,
        exported_dynamic_tls,
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


def _try_dwarf_snapshot(
    so_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    version: str,
    profile_hint: str | None,
    headers: list[Path],
    dwarf_only: bool,
    session: DwarfSession | None = None,
) -> tuple[AbiSnapshot | None, list[RecordType]]:
    """Attempt to build a snapshot from DWARF debug info.

    Returns ``(snapshot, dwarf_only_types)``.  When the snapshot should be
    used directly, *snapshot* is non-None.  When DWARF produced no symbols
    (and *dwarf_only* is False), *snapshot* is None and *dwarf_only_types*
    carries the partial type list for the symbol-only fallback path.

    *session*, when provided, is the open :class:`DwarfSession` from the
    metadata parse; the snapshot DIE walk reuses it instead of re-opening
    ``so_path`` (F5b). The caller retains ownership and closes it.
    """
    from .dwarf_snapshot import build_snapshot_from_dwarf

    if dwarf_only and headers:
        warnings.warn(
            "--dwarf-only: ignoring provided headers; using DWARF as primary data source.",
            UserWarning,
            stacklevel=3,
        )

    snap = build_snapshot_from_dwarf(
        so_path,
        elf_meta,
        dwarf_meta,
        dwarf_adv,
        version=version,
        language_profile=profile_hint,
        session=session,
    )
    # If DWARF produced functions (or was explicitly forced), use it.
    if snap.functions or snap.variables or dwarf_only:
        if not headers and not dwarf_only:
            # Advisory, not a problem: header-less dump is a legitimate mode (a
            # stripped/binary-only library). Demoted from UserWarning to an
            # info log so it does not spam stderr on every run; visible under
            # `-v` (ADR-035 P6). The genuine "headers passed but unusable"
            # cases below stay UserWarnings.
            log.info(
                "No headers provided — using DWARF debug info as primary data source. "
                "#define constants and default parameter values will be unavailable."
            )
        _populate_elf_visibility(snap)
        return snap, []
    # DWARF snapshot had no symbols of its own (often the case when
    # the binary exports only constructors / extern "C" wrappers that
    # the DWARF subprogram filter rejected). Keep the *types* it
    # extracted — they include bases / vtable info that pure-DWARF
    # metadata (DwarfMetadata.structs) does not retain.
    return None, list(snap.types)


def _build_symbol_only_snapshot(
    so_path: Path,
    version: str,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    exported_dynamic_funcs: set[str],
    exported_dynamic_objects: set[str],
    exported_dynamic_tls: set[str],
    dwarf_only_types: list[RecordType],
    profile_hint: str | None,
) -> AbiSnapshot:
    """Build a symbol-only :class:`AbiSnapshot` when no headers are available.

    Issues the appropriate ``UserWarning`` based on whether DWARF-derived
    types are present, then assembles the snapshot from ELF-exported symbols.
    """
    # No headers → symbol-only fallback. When the DWARF snapshot
    # builder produced types but no functions, we still preserve
    # those types (see *dwarf_only_types*), so the warning is
    # narrowed to reflect what's actually missing.
    # Advisory (ADR-035 P6): a header-less dump is a legitimate mode, so this is
    # an info log (suppressed by default, shown under `-v`), not a stderr-spamming
    # UserWarning on every run.
    if dwarf_only_types:
        log.info(
            "No headers provided — using ELF-exported symbols for "
            "functions/variables; DWARF-derived type information "
            "preserved."
        )
    elif dwarf_meta.has_dwarf:
        log.info(
            "No headers provided — using ELF-exported symbols only; DWARF "
            "debug info is present but was not expanded into the ABI surface."
        )
    else:
        log.info(
            "No headers provided and no DWARF debug info — only ELF-exported "
            "symbols will be captured; type information will be missing."
        )
    _so_mtime, _so_mtime_epoch = _safe_mtime(so_path)
    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path.resolve()),
        source_mtime=_so_mtime,
        source_mtime_epoch=_so_mtime_epoch,
        source_size=_safe_size(so_path),
        functions=[
            Function(
                name=sym,
                mangled=sym,
                return_type="?",
                visibility=Visibility.ELF_ONLY,
                # Absence of Itanium _Z prefix is strong evidence of C linkage
                is_extern_c=not sym.startswith("_Z"),
            )
            for sym in sorted(exported_dynamic_funcs)
        ],
        variables=[
            Variable(
                name=sym,
                mangled=sym,
                type="?",
                visibility=Visibility.ELF_ONLY,
            )
            for sym in sorted(exported_dynamic_objects | exported_dynamic_tls)
        ],
        # Preserve DWARF-derived types (with bases / vtable) when the
        # symbol-only fallback is taken. Pure DwarfMetadata loses
        # inheritance info; retaining the partially-populated DWARF
        # snapshot's types lets downstream detectors (e.g. internal
        # leak detection) still see the relationships.
        types=dwarf_only_types,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        elf_only_mode=True,
        platform="elf",
        language_profile=profile_hint,
    )
    _populate_elf_visibility(snapshot)
    return snapshot


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
) -> AbiSnapshot:
    """ELF-specific dump: pyelftools + debug info (DWARF/BTF/CTF) + header AST."""
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
    # reuse the same DWARFInfo (and its warm DIE cache) rather than re-parsing
    # every DIE from a second open (F5b). Metadata resolution and the snapshot
    # attempt run inside the try; the finally closes any opened session on every
    # exit path (including an exception during resolution), so no descriptor
    # leaks. The built snapshot holds extracted model objects, not live DIE
    # references, so closing after it is returned is safe.
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
        if (
            not (symbols_only or debug_presence_only)
            and resolved_debug_format == "dwarf"
            and (dwarf_only or (not headers and dwarf_meta.has_dwarf))
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
        if symbols_only or not headers:
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
        # Built here (session still open): the "auto" frontend can fall back to clang internally (G16) even when _resolve_header_backend guesses castxml, so the actual parser type is the only reliable signal below (Codex review).
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
        )
        dwarf_layout_types = dwarf_layout_types_or_empty(
            so_path,
            elf_meta,
            dwarf_meta,
            dwarf_adv,
            isinstance(parser, _ClangAstParser),
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            debug_format=resolved_debug_format,
            version=version,
            language_profile=profile_hint,
            session=dwarf_session,
        )
    finally:
        for _sess in _dwarf_session_out:
            _sess.close()

    _so_mtime, _so_mtime_epoch = _safe_mtime(so_path)
    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path.resolve()),
        source_mtime=_so_mtime,
        source_mtime_epoch=_so_mtime_epoch,
        source_size=_safe_size(so_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=backfill_dwarf_layout(parser.parse_types(), dwarf_layout_types),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        constants=parser.parse_constants(),
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        # Reached only when headers were supplied and castxml ran (the no-header
        # and DWARF-only branches return earlier): this surface is header-parsed.
        from_headers=True,
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
        platform="elf",
        language_profile=profile_hint,
    )
    _populate_elf_visibility(snapshot)
    return snapshot


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
) -> AbiSnapshot:
    """Mach-O dump: export table from macholib + header-AST analysis."""
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
        return AbiSnapshot(
            library=dylib_path.name,
            version=version,
            source_path=str(dylib_path.resolve()),
            source_mtime=_dylib_mtime,
            source_mtime_epoch=_dylib_mtime_epoch,
            source_size=_safe_size(dylib_path),
            functions=[
                Function(
                    name=_normalize_macho_sym(exp.name),
                    mangled=_normalize_macho_sym(exp.name),
                    return_type="?",
                    # ELF_ONLY: marks symbols as export-table-only (no header
                    # confirmation). This lets the checker distinguish
                    # binary-only removals as FUNC_REMOVED_ELF_ONLY.
                    visibility=Visibility.ELF_ONLY,
                    is_extern_c=not _normalize_macho_sym(exp.name).startswith("_Z"),
                )
                for exp in sorted(macho_funcs, key=lambda e: e.name)
            ],
            variables=[
                Variable(
                    name=_normalize_macho_sym(exp.name),
                    mangled=_normalize_macho_sym(exp.name),
                    type="?",
                    visibility=Visibility.ELF_ONLY,
                )
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
    )

    _dylib_mtime, _dylib_mtime_epoch = _safe_mtime(dylib_path)
    return AbiSnapshot(
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
        constants=parser.parse_constants(),
        macho=macho_meta,
        # Reached only when headers were supplied and castxml ran (the no-header
        # branch returns earlier): this surface is header-parsed.
        from_headers=True,
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
        platform="macho",
        language_profile=profile_hint,
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
) -> AbiSnapshot:
    """PE dump: export table from pefile + header-AST analysis."""
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
            functions=[
                Function(
                    name=sym,
                    mangled=sym,
                    return_type="?",
                    visibility=Visibility.ELF_ONLY,
                    is_extern_c=not sym.startswith("?"),
                )
                for sym in sorted(exported_dynamic)
            ],
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
    )

    _dll_mtime, _dll_mtime_epoch = _safe_mtime(dll_path)
    return AbiSnapshot(
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
        constants=parser.parse_constants(),
        pe=pe_meta,
        # Reached only when headers were supplied and castxml ran (the no-header
        # branch returns earlier): this surface is header-parsed.
        from_headers=True,
        ast_producer="clang" if isinstance(parser, _ClangAstParser) else "castxml",
        ast_toolchain=_parser_ast_toolchain(parser),
        ast_fallback_reason=_parser_ast_fallback_reason(parser),
        ast_toolchain_supported=_parser_ast_supported(parser),
        ast_toolchain_unsupported_reasons=_parser_ast_unsupported_reasons(parser),
        platform="pe",
        language_profile=profile_hint,
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
