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

"""Dumper — headers + .so → AbiSnapshot via a pluggable L2 header backend.

The header AST (L2) is produced by one of two interchangeable frontends behind
``_header_ast_parser``: **castxml** (the default / schema reference, parsed by
``dumper_castxml._CastxmlParser``) or **clang** (``clang -ast-dump=json``, parsed
by ``dumper_clang._ClangAstParser``) when explicitly requested. Select
with ``header_backend=`` (``auto``/``castxml``/``clang``; CLI ``--ast-frontend``)
or the ``ABICHECK_AST_FRONTEND`` env var. ``auto`` resolves to castxml and never
silently falls back to clang on castxml-less hosts (clang's JSON AST lacks
computed record layout, so an implicit fallback could miss layout-only breaks).
The one exception is a runtime castxml *toolchain-version* failure (bundled
Clang too old for the host libstdc++/GCC), which falls back to the clang backend
automatically (G16); an explicit selection is honored verbatim. See ADR-003.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
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
    from .elf_metadata import ElfMetadata

from defusedxml import ElementTree as DefusedET

from .dumper_cache import _cache_path
from .dumper_castxml import (
    _CastxmlParser as _CastxmlParser,
    _parse_vtable_index as _parse_vtable_index,
    _vt_sort_key as _vt_sort_key,
)
from .dumper_clang import _ClangAstParser as _ClangAstParser
from .dumper_clang_errors import (
    _is_direct_include_guard_failure,
    _is_missing_cpp_stdlib_header_error,
    _parse_clang_ast_result,
    diagnose_header_compile_failure,
    retry_excluding_error_headers,
)
from .dumper_sysinc import (
    _auto_system_includes_enabled as _auto_system_includes_enabled,
    _parse_gnu_include_search_dirs as _parse_gnu_include_search_dirs,
    _probe_gnu_system_includes as _probe_gnu_system_includes,
    _resolve_clang_system_includes as _resolve_clang_system_includes,
    _resolve_probe_compiler as _resolve_probe_compiler,
)
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .errors import SnapshotError, ValidationError
from .header_utils import iter_cache_header_files
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


def _castxml_available() -> bool:
    return shutil.which("castxml") is not None


def _clang_available(clang_bin: str = "clang") -> bool:
    return shutil.which(clang_bin) is not None


#: Header-AST backend identifiers (the L2 producers). castxml is the default and
#: the schema reference; clang is the alternative for hosts where castxml is
#: absent or its bundled frontend chokes (ADR-003, "clang as an alternative L2
#: frontend"). ``auto`` resolves to castxml unless the environment explicitly
#: selects clang; the clang backend lacks computed record layout evidence.
HEADER_BACKENDS = ("auto", "castxml", "clang")


def _resolve_header_backend(backend: str | None) -> str:
    """Resolve an L2 header-AST frontend request to a concrete ``castxml``/``clang``.

    Precedence: an explicit ``castxml``/``clang`` is honored verbatim (and the
    caller gets a clear error later if that tool is missing). ``auto``/``None``
    consults the ``ABICHECK_AST_FRONTEND`` env var first, then resolves to
    castxml (the schema reference). It deliberately does not auto-fallback to
    clang: clang JSON AST snapshots do not carry computed record size, alignment,
    field offsets, or vtable layout, so implicit fallback could silently miss
    layout-only ABI breaks on castxml-less hosts. Users who accept that evidence
    tier may still request ``clang`` explicitly (or via the environment).
    """
    choice = (backend or "auto").lower()
    if choice in ("castxml", "clang"):
        return choice
    if choice != "auto":
        raise ValidationError(
            f"Unknown AST frontend {backend!r}; expected one of {HEADER_BACKENDS}."
        )
    env = os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower()
    if env in ("castxml", "clang"):
        return env
    return "castxml"


def _has_explicit_std(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...] = ()
) -> bool:
    """True if the user supplied an explicit C/C++ standard.

    Checks both ``--gcc-options`` (whitespace-split string) and any repeatable
    ``--gcc-option`` token (e.g. ``-std=gnu++23`` / ``/std:c++latest``), so the
    automatic C++20 bump never appends a standard *after* — and thus override —
    a dialect the user requested through either flag.
    """
    if gcc_options and ("-std=" in gcc_options or "/std:" in gcc_options):
        return True
    return any(("-std=" in t or "/std:" in t) for t in gcc_option_tokens)


def _build_clang_header_command(
    cc_bin: str, cc_id: str,
    extra_includes: list[Path], agg_path: Path,
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
    explicit_std = _has_explicit_std(gcc_options, gcc_option_tokens)
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


def _resolve_clang_bin(
    compiler: str, gcc_path: str | None, gcc_prefix: str | None,
) -> str:
    """Resolve the clang executable to run, raising if it is not on ``PATH``.

    ``--gcc-path`` is honored only when it points at a clang (castxml emulates a
    GCC/G++ binary, which can't take clang-only flags); ``--gcc-prefix`` maps to
    the prefixed clang driver.
    """
    clang_bin: str | None = None
    if gcc_path and "clang" in Path(gcc_path).name.lower():
        clang_bin = gcc_path
    elif gcc_prefix:
        clang_bin = (
            f"{gcc_prefix}clang++"
            if compiler in ("c++", "g++", "clang++")
            else f"{gcc_prefix}clang"
        )
    if not clang_bin:
        clang_bin = "clang++" if compiler in ("c++", "g++", "clang++") else "clang"
    if not _clang_available(clang_bin):
        raise SnapshotError(
            f"{clang_bin} not found in PATH. The clang header backend needs clang/clang++ "
            "installed (apt install clang, brew install llvm, or conda install -c conda-forge "
            "clang). Or use the castxml frontend (--ast-frontend castxml)."
        )
    return clang_bin


def _resolve_clang_langmode(
    lang: str | None, headers: list[Path], clang_bin: str,
) -> tuple[bool, bool, bool, str]:
    """Return ``(force_cpp, force_cpp20, explicit_c_request, cc_id)`` for the TU.

    ``explicit_c_request`` records whether C was *explicitly* requested
    (``--lang c``) vs auto-detected — both leave ``force_cpp`` False, but the
    C→C++ self-heal treats them differently (warning vs debug; Codex review).
    """
    force_cpp = bool(lang and lang.upper() in ("C++", "CPP"))
    if not lang:
        force_cpp = _detect_cpp_headers(headers)
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
        lang, headers, clang_bin,
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

    key = _cache_key(
        headers, extra_includes, clang_bin,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang, backend="clang",
        # Both include sets feed the key: whichever the retry settles on, a
        # toolchain change to either invalidates the cached AST. Equal when
        # already in C++ mode — pass once so existing C++ cache keys are stable.
        system_includes=system_includes
        if force_cpp
        else (*system_includes, *cpp_system_includes),
        extra_hash_dirs=extra_hash_dirs,
    )
    cached = _cache_path(key, backend="clang")
    if cached.exists():
        try:
            return cast("dict[str, Any]", json.loads(cached.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            cached.unlink(missing_ok=True)

    agg_ext = ".hpp" if force_cpp else ".h"
    with tempfile.NamedTemporaryFile(suffix=agg_ext, mode="w", delete=False) as agg:
        agg_path = Path(agg.name)
    active_headers = list(headers)

    def _write_agg(hdrs: list[Path]) -> None:
        agg_path.write_text(
            "".join(f'#include "{h.resolve()}"\n' for h in hdrs), encoding="utf-8"
        )

    _write_agg(active_headers)

    def _run_clang(fcpp: bool, fcpp20: bool, sysinc: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        cmd = _build_clang_header_command(
            clang_bin, cc_id, extra_includes, agg_path,
            sysroot=sysroot, nostdinc=nostdinc, gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=fcpp, force_cpp20=fcpp20,
            system_includes=sysinc,
        )
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SnapshotError(
                "clang timed out after 120 seconds parsing the header(s). The header "
                "may contain syntax that causes the frontend to hang."
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
                True, _detect_cpp20_headers(headers), cpp_system_includes,
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
        return _parse_clang_ast_result(result, cached)
    finally:
        agg_path.unlink(missing_ok=True)


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
    """Run the resolved L2 backend and return its parser (castxml or clang).

    Both parsers expose the identical ``parse_functions``/``parse_variables``/
    ``parse_types``/``parse_enums``/``parse_typedefs``/``parse_constants``
    surface, so the format-specific ``_dump_*`` builders consume either one
    uniformly — the only difference is which frontend produced the AST.
    """
    resolved = _resolve_header_backend(backend)

    def _run_clang() -> _ClangAstParser:
        ast_root = _clang_header_dump(
            headers, extra_includes, compiler=compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
            extra_hash_dirs=extra_hash_dirs,
        )
        return _ClangAstParser(
            ast_root, exported_dynamic, exported_static,
            public_header_paths=public_header_paths,
            public_dir_paths=public_dir_paths,
        )

    if resolved == "clang":
        return _run_clang()

    # G16: when the frontend was selected automatically (no explicit --ast-frontend
    # and no ABICHECK_AST_FRONTEND pin), two castxml failures are recoverable by
    # falling back to the clang backend rather than aborting: a *toolchain-version*
    # failure (bundled Clang too old for the host libstdc++/GCC — clang parses
    # against the host toolchain directly), and a *direct-inclusion #error guard*
    # (a `-H <include-dir>` swept in a preview/internal header that #errors on
    # direct inclusion — only the clang path can granularly exclude the offending
    # headers via retry_excluding_error_headers, so the headline include-dir scan
    # works on the default frontend, not just --ast-frontend clang). An explicit
    # castxml request is honored verbatim (the error surfaces unchanged).
    choice = (backend or "auto").lower()
    env_pin = os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower()
    auto_selected = choice == "auto" and env_pin not in ("castxml", "clang")
    try:
        xml_root = _castxml_dump(
            headers, extra_includes, compiler=compiler,
            gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            sysroot=sysroot, nostdinc=nostdinc, lang=lang,
            extra_hash_dirs=extra_hash_dirs,
        )
    except SnapshotError as exc:
        if (
            auto_selected
            and _clang_available()
            and (
                _is_toolchain_version_failure(str(exc))
                or _is_direct_include_guard_failure(str(exc))
            )
        ):
            log.warning(
                "castxml could not parse the header(s) (toolchain mismatch or a "
                "header that refuses direct inclusion); falling back to the clang "
                "header backend, which parses against the host toolchain and can "
                "exclude direct-include #error guard headers. Set --ast-frontend "
                "castxml to force castxml and see the original error."
            )
            return _run_clang()
        raise
    return _CastxmlParser(
        xml_root, exported_dynamic, exported_static,
        public_header_paths=public_header_paths,
        public_dir_paths=public_dir_paths,
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


def _cache_key(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str,
    *,
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    sysroot: Path | None = None,
    nostdinc: bool = False,
    lang: str | None = None,
    backend: str = "castxml",
    system_includes: tuple[str, ...] = (),
    extra_hash_dirs: tuple[Path, ...] = (),
) -> str:
    h = hashlib.sha256()
    # The header-AST backend is part of the key: a castxml-XML cache entry and a
    # clang-JSON one are different artifacts that must never collide.
    h.update(f"backend={backend}".encode())
    for p in sorted(str(x.resolve()) for x in headers):
        h.update(p.encode())
        try:
            h.update(str(os.path.getmtime(p)).encode())
        except OSError:
            pass
    # Also hash mtimes of files in the include dirs (catches most transitive
    # changes). extra_hash_dirs are dirs searched via *deferred* -isystem tokens
    # (the inferred -H roots when a build context is present) rather than -I, so
    # their contents must be folded in here too — otherwise an edit to a header
    # transitively included from such a root would reuse a stale AST (Codex).
    for inc_dir in sorted(str(x) for x in (*extra_includes, *extra_hash_dirs)):
        inc_path = Path(inc_dir)
        h.update(inc_dir.encode())
        if inc_path.is_dir():
            # Hash every header-like file (incl. .inl/.tcc template bodies, not
            # just .h/.hpp) so any transitive include edit busts the key (#454).
            for f in iter_cache_header_files(inc_path):
                try:
                    h.update(str(f).encode())
                    h.update(str(f.stat().st_mtime).encode())
                except OSError:
                    pass
    h.update(compiler.encode())
    # Include toolchain parameters so different cross-compilation configs
    # produce distinct cache entries
    h.update(f"gcc_path={gcc_path or ''}".encode())
    h.update(f"gcc_prefix={gcc_prefix or ''}".encode())
    h.update(f"gcc_options={gcc_options or ''}".encode())
    h.update(f"gcc_option_tokens={chr(0).join(gcc_option_tokens)}".encode())
    h.update(f"sysroot={sysroot or ''}".encode())
    h.update(f"nostdinc={nostdinc}".encode())
    h.update(f"lang={lang or ''}".encode())
    # Auto-probed system include dirs (castxml↔clang parity): a host-toolchain
    # change must invalidate a cached clang dump (the resolved libstdc++ moved).
    h.update(f"system_includes={chr(0).join(system_includes)}".encode())
    return h.hexdigest()



# C++ file extensions that unambiguously indicate C++ content.
_CPP_EXTENSIONS = frozenset({".hpp", ".hxx", ".hh", ".h++", ".tpp"})

# ``extern "C"`` is special: it appears in *valid C* headers (guarded by
# ``#ifdef __cplusplus``), so its presence means "castxml parses in C++ mode" but
# does NOT mean the header *requires* C++. It is kept out of _CPP_ONLY_PATTERNS so
# the C→C++ retry (G16/A3) is never triggered by it — a guarded ``extern "C"``
# header that fails in C mode failed for a real reason, and retrying as C++ would
# skip the ``#ifndef __cplusplus`` branches and mask that error (Codex review).
_EXTERN_C_PATTERN = re.compile(rb'^\s*extern\s+"C"')

# Genuinely C++-only constructs: a *valid C* header cannot contain these, so they
# are a reliable signal that ``--lang c`` was mis-specified and a C++ retry is the
# right degrade. Match actual declarations, not keywords in comments (applied
# line-by-line to non-comment lines).
_CPP_ONLY_PATTERNS = [
    re.compile(rb"^\s*class\s+\w+\s*[:{]"),          # class Foo { / class Foo :
    re.compile(rb"^\s*namespace\s+\w+"),               # namespace ns
    re.compile(rb"^\s*template\s*<"),                  # template<...>
    re.compile(rb"^\s*using\s+\w+\s*="),               # using alias = ...
    re.compile(rb"^\s*public\s*:"),                     # public:
    re.compile(rb"^\s*private\s*:"),                    # private:
    re.compile(rb"^\s*protected\s*:"),                  # protected:
    # C++ keywords that can appear anywhere in a line (not just at start)
    re.compile(rb"\bvirtual\s+"),                       # virtual member functions
    re.compile(rb"(?<!\w)~\w+\s*\("),                     # destructor ~Foo()
    re.compile(rb":\s*public\s+\w+"),                   # struct Derived : public Base
    re.compile(rb":\s*private\s+\w+"),                  # : private Base
    re.compile(rb":\s*protected\s+\w+"),                # : protected Base
    re.compile(rb"\bclass\s+\w+\s*[{;]"),              # class anywhere (forward decl or def)
    re.compile(rb"\bconst\s+\w[\w:]*\s*&"),               # const Type& reference (C++ idiom)
    re.compile(rb"\bstatic_cast\b"),                    # C++ cast
    re.compile(rb"\bconstexpr\b"),                      # C++ constexpr
    re.compile(rb"\bnullptr\b"),                        # C++ nullptr
    re.compile(rb"\bnoexcept\b"),                       # C++ noexcept
    re.compile(rb"\boverride\b"),                           # C++ override specifier
]

# Full set used for auto language-mode detection (lang unspecified) and the
# failure hint: here ``extern "C"`` *does* count, because castxml always parses in
# a C++-ish mode, so an aggregate including an extern "C" header is built as .hpp.
_CPP_PATTERNS = [_EXTERN_C_PATTERN, *_CPP_ONLY_PATTERNS]


# Structural C++20 patterns — concepts and requires-expressions. When any
# of these appears in a header, castxml must be invoked with a C++20-aware
# `-std=` flag or it will fail to parse the file. The patterns target the
# definition site (`concept X = ...`, `requires(...) {`, `template <Foo T>`-
# style constrained template parameters) rather than uses, so we don't
# over-trigger.
_CPP20_PATTERNS = [
    re.compile(rb"^\s*concept\s+\w+\s*="),          # concept Addable = ...
    re.compile(rb"\brequires\s*\("),                # requires(T a, T b) { ... }
    re.compile(rb"\brequires\s+\w"),                # template<T> requires Foo<T>
]


def _detect_cpp20_headers(header_paths: list[Path]) -> bool:
    """Return True if any header contains C++20-only syntax (concept/requires).

    Used to decide whether to pass ``-std=gnu++20`` to castxml. castxml's
    default standard is whatever the underlying compiler defaults to
    (usually C++17 on modern gcc), which does not accept ``concept``
    declarations. This detection is conservative: only definition-site
    syntax counts, not the keyword in arbitrary text.
    """
    for p in header_paths:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        content = re.sub(rb"/\*.*?\*/", b"", content, flags=re.DOTALL)
        for line in content.split(b"\n"):
            stripped = line.split(b"//")[0]
            if any(pat.search(stripped) for pat in _CPP20_PATTERNS):
                return True
    return False


def _detect_cpp_headers(
    header_paths: list[Path], patterns: list[re.Pattern[bytes]] = _CPP_PATTERNS
) -> bool:
    """Auto-detect whether headers require C++ compilation mode (FIX-A).

    Returns True if any header has a C++ extension or contains structural
    C++ syntax (class/namespace/template declarations on non-comment lines).

    With the default *patterns* (``_CPP_PATTERNS``) ``extern "C"`` counts as a
    C++ indicator, because castxml always parses in a C++-ish mode and the
    aggregate header must then be built as ``.hpp``. Pass ``_CPP_ONLY_PATTERNS``
    to require a *genuinely C++-only* construct (excluding ``extern "C"``) — used
    by the C→C++ retry so a valid C header is never re-parsed as C++ and have its
    real C-mode error masked (Codex review).
    """
    for p in header_paths:
        if p.suffix.lower() in _CPP_EXTENSIONS:
            return True
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Strip C-style block comments to reduce false positives
        content = re.sub(rb"/\*.*?\*/", b"", content, flags=re.DOTALL)
        for line in content.split(b"\n"):
            # Skip C++ line comments
            stripped = line.split(b"//")[0]
            if any(pat.search(stripped) for pat in patterns):
                return True
    return False


def _resolve_compiler_binary(
    compiler: str,
    gcc_path: str | None,
    gcc_prefix: str | None,
) -> tuple[str, str]:
    """Resolve the compiler binary and dialect (gnu/msvc) for castxml.

    Returns (cc_bin, cc_id) where cc_id is "gnu" or "msvc".
    """
    _cc_map = {"c++": "g++", "cc": "gcc", "g++": "g++", "gcc": "gcc",
               "clang++": "clang++", "clang": "clang"}

    if gcc_path:
        cc_bin = gcc_path
    elif gcc_prefix:
        suffix = "g++" if compiler in ("c++", "g++", "clang++") else "gcc"
        cc_bin = f"{gcc_prefix}{suffix}"
    else:
        cc_bin = _cc_map.get(compiler, compiler)

    exe_name = Path(cc_bin).name.lower()
    cc_id = "msvc" if exe_name in ("cl", "cl.exe") else "gnu"
    return cc_bin, cc_id


def _build_castxml_command(
    cc_bin: str, cc_id: str,
    extra_includes: list[Path],
    out_xml: Path, agg_path: Path,
    *,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool = False,
    force_cpp20: bool = False,
) -> list[str]:
    """Build the castxml command line."""
    cmd = ["castxml", "--castxml-output=1",
           f"--castxml-cc-{cc_id}", cc_bin]
    for inc in extra_includes:
        cmd += ["-I", str(inc)]

    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += shlex.split(gcc_options, posix=os.name != "nt")
    # Repeatable --gcc-option: each value is one literal compiler argument,
    # appended verbatim (no shlex split) so a flag whose value contains
    # whitespace survives intact and identically on POSIX and Windows.
    cmd += list(gcc_option_tokens)

    explicit_std = _has_explicit_std(gcc_options, gcc_option_tokens)
    # Workaround: castxml with --castxml-cc-gnu gcc auto-injects -std=gnu++17
    # which is rejected when parsing a .h file in C mode. Force C mode, but only
    # impose gnu11 when the user did not request a C standard via --gcc-option(s)
    # — otherwise their -std=gnu17/c99 would be overridden by a later flag.
    if not force_cpp and cc_id == "gnu":
        cmd += ["-x", "c"]
        if not explicit_std:
            cmd += ["-std=gnu11"]
    elif force_cpp20 and not explicit_std:
        # Headers contain C++20-only syntax (concept / requires-expression).
        # Castxml's default standard is whatever the host compiler picks
        # (usually C++17 on modern gcc / MSVC), which rejects concepts.
        # Force C++20 unless the caller already supplied an explicit -std=.
        # MSVC uses /std:c++20; gcc/clang use -std=gnu++20.
        if cc_id == "msvc":
            cmd += ["/std:c++20"]
        else:
            cmd += ["-x", "c++", "-std=gnu++20"]

    cmd += ["-o", str(out_xml), str(agg_path)]
    return cmd


# clang's diagnostic for an unrecognised sized-float keyword, e.g.
#   error: unknown type name '_Float32'
_SIZED_FLOAT_RE = re.compile(r"_Float(?:16|32|64|128)(?:x)?\b")

# castxml drives an internal Clang frontend; it must be new enough to parse
# modern host headers. _Float32/_Float64/_Float128 land in Clang 16, and the
# [[assume]] / __assume__ attribute (GCC 13+ libstdc++) in Clang 18. We
# recommend a bundled Clang >= this so both are covered. This is the durable
# fix for the header-scoped toolchain aborts (plan G16) — abicheck cannot
# reliably work around a frontend that is simply older than the host headers,
# so it detects the version and tells the user to upgrade.
_RECOMMENDED_CLANG_MAJOR = 18

_CASTXML_VERSION_RE = re.compile(r"castxml version\s+(\S+)", re.IGNORECASE)
# `castxml --version` does not always print the bundled frontend version, and
# when it does the spelling varies ("clang version 18.1.8", "LLVM version 18.1.8").
# Accept either so the precise floor comparison can actually fire.
_CLANG_VERSION_RE = re.compile(
    r"(?:clang|LLVM) version\s+(\d+)(?:\.(\d+))?", re.IGNORECASE
)


def _is_toolchain_version_failure(stderr: str) -> bool:
    """True when a castxml failure is a bundled-Clang-too-old signature
    (sized-float keywords or the GCC ``__assume__`` attribute) — the only
    failures for which the ``castxml --version`` upgrade note is relevant."""
    return bool(stderr) and (
        bool(_SIZED_FLOAT_RE.search(stderr)) or "__assume__" in stderr
    )


def _parse_castxml_version(output: str) -> tuple[str | None, tuple[int, int] | None]:
    """Parse ``castxml --version`` text into (castxml_version, clang_major_minor).

    Either element is ``None`` when not found. Pure/string-only so it is fully
    unit-testable without castxml installed.
    """
    cx = _CASTXML_VERSION_RE.search(output or "")
    cl = _CLANG_VERSION_RE.search(output or "")
    cx_ver = cx.group(1) if cx else None
    clang = (int(cl.group(1)), int(cl.group(2) or 0)) if cl else None
    return cx_ver, clang


def _castxml_version_note() -> str:
    """Probe ``castxml --version`` and, when its bundled Clang predates the
    recommended floor, return a one-line upgrade note (else "").

    Best-effort: any probe failure yields "" so the base diagnostic still
    stands. Only called on an actual parse failure, so the extra process is
    incurred rarely.
    """
    try:
        proc = subprocess.run(
            ["castxml", "--version"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    raw, clang = _parse_castxml_version(f"{proc.stdout}\n{proc.stderr}")
    if clang is not None and clang[0] < _RECOMMENDED_CLANG_MAJOR:
        detected = (
            f"castxml {raw} (clang {clang[0]}.{clang[1]})"
            if raw else f"clang {clang[0]}.{clang[1]}"
        )
        return (
            f" Detected {detected}; these host headers need clang "
            f">= {_RECOMMENDED_CLANG_MAJOR} — upgrade castxml to a build with a "
            f"newer bundled Clang."
        )
    if raw and clang is None:
        return (
            f" Detected castxml {raw}; upgrade it if its bundled Clang predates "
            f"the host gcc (clang >= {_RECOMMENDED_CLANG_MAJOR} recommended)."
        )
    return ""


def _castxml_failure_hint(
    stderr: str,
    *,
    force_cpp: bool,
    headers: list[Path],
    version_note: str = "",
) -> str:
    """Map a known castxml/host-toolchain failure to an actionable remediation.

    Returns the empty string when no known signature matches. These three
    signatures account for the header-scoped scan aborts seen across the
    real-world scan campaign (see plan G16); each previously surfaced only as an
    opaque clang stderr dump. The durable fix for the first two is a castxml
    built against a newer Clang (or the libclang extractor, G4) — abicheck cannot
    reliably work around a frontend that is simply older than the host headers,
    so it diagnoses precisely (optionally with the detected version via
    ``version_note``) instead of guessing.
    """
    # 1) glibc sized-float types (the dominant case): _Float32/64/128 keywords
    #    the bundled clang frontend rejects while emulating a newer host GCC.
    if stderr and _SIZED_FLOAT_RE.search(stderr):
        return (
            "\n\nHint: the host glibc declares sized-float types "
            "(_Float32/_Float64/_Float128) that this castxml/clang frontend "
            "cannot parse — the bundled clang is older than the host gcc/glibc. "
            "Install a newer castxml (newer bundled Clang), or point abicheck at "
            f"a clang-parsable toolchain via --gcc-path / --sysroot.{version_note}"
        )
    # 2) GCC 13+ libstdc++ uses the [[__assume__]] / __attribute__((__assume__))
    #    spelling the bundled clang frontend doesn't know.
    if "__assume__" in stderr:
        return (
            "\n\nHint: the host libstdc++ uses the GCC '__assume__' attribute "
            "that this castxml/clang frontend rejects. Install a newer castxml "
            "matching the host GCC, or scan against an older/clang-parsable "
            f"libstdc++ via --gcc-path / --sysroot.{version_note}"
        )
    # 3) Explicit --lang c on headers that need C++ (classes/namespaces) or that
    #    guard extern "C" with #ifdef __cplusplus — castxml always parses in a
    #    C++-ish mode, so forcing C rejects valid headers.
    if not force_cpp and _detect_cpp_headers(headers):
        return (
            "\n\nHint: The header files appear to contain C++ syntax "
            "(class, namespace, template) but --lang c was specified. "
            "Try removing --lang or using --lang c++."
        )
    # 4) Generic remediable signatures (missing dependency header, required
    #    config macro, undeclared type from a missing umbrella) — frontend-
    #    agnostic, so the castxml path benefits from the same guidance as clang.
    return diagnose_header_compile_failure(stderr) or ""


def _validate_castxml_output(
    result: subprocess.CompletedProcess[str],
    out_xml: Path,
    headers: list[Path],
    force_cpp: bool,
) -> Element:
    """Validate castxml output and return parsed XML root."""
    if result.returncode != 0:
        # Only probe `castxml --version` when the failure is a frontend-too-old
        # signature — otherwise the upgrade note is irrelevant (and unused).
        version_note = (
            _castxml_version_note()
            if _is_toolchain_version_failure(result.stderr) else ""
        )
        hint = _castxml_failure_hint(
            result.stderr, force_cpp=force_cpp, headers=headers,
            version_note=version_note,
        )
        raise SnapshotError(
            f"castxml failed (exit {result.returncode}):\n{result.stderr[:2000]}{hint}"
        )
    if not out_xml.exists() or out_xml.stat().st_size == 0:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml exited 0 but produced no output file (or empty file).{detail}"
        )
    try:
        root = cast(Element, DefusedET.parse(str(out_xml)).getroot())
    except Exception as xml_exc:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml produced invalid XML: {xml_exc}{detail}"
        ) from xml_exc
    if len(root) == 0:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml produced an empty XML document (no declarations found). "
            f"Check that the header paths are correct and the compiler can "
            f"parse them.{detail}"
        )
    return root


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
    if not _castxml_available():
        raise SnapshotError(
            "castxml not found in PATH. Install with: apt install castxml, "
            "brew install castxml, conda install -c conda-forge castxml, "
            "or choco install castxml (Windows); then ensure castxml is in PATH. "
            "On a clang-only host, run with --ast-frontend clang (or "
            "ABICHECK_AST_FRONTEND=clang) to use the clang JSON-AST backend "
            "instead — note it does not carry record size/alignment/offset "
            "layout, so layout-only breaks need castxml or debug info (L1)."
        )

    # Check disk cache
    key = _cache_key(
        headers, extra_includes, compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        extra_hash_dirs=extra_hash_dirs,
    )
    cached = _cache_path(key)
    if cached.exists():
        try:
            _cached_root = DefusedET.parse(str(cached)).getroot()
        except Exception:
            _cached_root = None
        if _cached_root is None:
            cached.unlink(missing_ok=True)
        else:
            return cast(Element, _cached_root)

    cc_bin, cc_id = _resolve_compiler_binary(compiler, gcc_path, gcc_prefix)

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        out_xml = Path(tmp.name)

    # Determine language mode: .h / C parse for C-only, .hpp / C++ for C++ (FIX-A).
    force_cpp = bool(lang and lang.upper() in ("C++", "CPP"))
    if not lang:
        force_cpp = _detect_cpp_headers(headers)

    try:
        try:
            root = _run_castxml_attempt(
                cc_bin, cc_id, headers, extra_includes, out_xml,
                sysroot=sysroot, nostdinc=nostdinc, gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
                force_cpp=force_cpp,
            )
        except SnapshotError as primary:
            # G16/A3: an explicit ``--lang c`` on a header that actually requires
            # C++ (a stray class/namespace/template) should degrade to a C++ retry
            # rather than hard-fail. Skip the retry when we are already in C++
            # mode, when the failure is a frontend-too-old signature (a mode switch
            # won't help), or when the header has no *genuinely C++-only* construct
            # (``_CPP_ONLY_PATTERNS`` excludes ``extern "C"``: a guarded
            # ``extern "C"`` header is valid C, so a C-mode failure there is real
            # and must NOT be masked by re-parsing as C++, which would skip the
            # ``#ifndef __cplusplus`` branches — Codex review).
            if (
                force_cpp
                or _is_toolchain_version_failure(str(primary))
                or not _detect_cpp_headers(headers, _CPP_ONLY_PATTERNS)
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
                    cc_bin, cc_id, headers, extra_includes, out_xml,
                    sysroot=sysroot, nostdinc=nostdinc, gcc_options=gcc_options,
                    gcc_option_tokens=gcc_option_tokens,
                    force_cpp=True,
                )
            except SnapshotError:
                # Both modes failed — surface the originally requested C-mode
                # error (and its hint), not the fallback's, so the diagnostic
                # matches what the user asked for.
                raise primary from None
        try:
            shutil.copy2(str(out_xml), str(cached))
        except OSError as exc:
            log.warning("Could not write castxml AST cache %s: %s", cached, exc)
        return root
    finally:
        out_xml.unlink(missing_ok=True)


def _run_castxml_attempt(
    cc_bin: str, cc_id: str,
    headers: list[Path],
    extra_includes: list[Path],
    out_xml: Path,
    *,
    sysroot: Path | None,
    nostdinc: bool,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool,
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
        cc_bin, cc_id, extra_includes, out_xml, agg_path,
        sysroot=sysroot, nostdinc=nostdinc, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        force_cpp=force_cpp,
        force_cpp20=force_cpp20,
    )

    try:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired as exc:
            stderr_snippet = ""
            if exc.stderr:
                text = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")
                stderr_snippet = f"\nPartial stderr: {text[:1000].strip()}"
            raise SnapshotError(
                f"castxml timed out after 120 seconds. The header file may contain "
                f"syntax that causes the compiler to hang. Check that the header "
                f"is valid and can be compiled with gcc/g++.{stderr_snippet}"
            ) from exc
        return _validate_castxml_output(result, out_xml, headers, force_cpp)
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
        if self.magic_prefix is not None and magic[: len(self.magic_prefix)] == self.magic_prefix:
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
        public_headers: Explicit public-header files used only to classify
            declaration provenance (ADR-015). When empty, every declaration's
            origin stays UNKNOWN and behaviour is unchanged.
        public_header_dirs: Directories whose headers are treated as public
            for provenance classification.

    Returns:
        AbiSnapshot with functions, variables, and types populated.
    """
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
    snapshot = handler.builder(
        so_path, headers, extra_includes or [], version, compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        public_headers=public_headers, public_header_dirs=public_header_dirs,
        header_backend=header_backend, extra_hash_dirs=extra_hash_dirs,
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


def _is_kernel_binary(path: Path) -> bool:
    """Heuristic: is this a kernel binary (vmlinux, *.ko, *.ko.xz, *.ko.zst)?"""
    name = path.name
    if name == "vmlinux":
        return True
    suffixes = path.suffixes  # e.g. ['.ko', '.xz']
    suffix_str = "".join(suffixes)
    if suffix_str in (".ko", ".ko.xz", ".ko.zst", ".ko.gz"):
        return True
    # Check for .modinfo section (kernel module indicator)
    try:
        from elftools.elf.elffile import ELFFile
        with open(path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            return elf.get_section_by_name(".modinfo") is not None  # type: ignore[no-untyped-call]
    except Exception:  # noqa: BLE001
        return False


def _resolve_debug_metadata(
    so_path: Path,
    debug_format: str | None,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Resolve debug metadata using the specified or auto-detected format.

    Returns (dwarf_meta, dwarf_adv) — the same types as parse_dwarf().
    BTF/CTF data is converted to DwarfMetadata for checker compatibility.
    """
    from .dwarf_advanced import AdvancedDwarfMetadata

    if debug_format == "btf":
        from .btf_metadata import parse_btf_metadata
        btf = parse_btf_metadata(so_path)
        if not btf.has_btf:
            log.warning("BTF requested but no .BTF section in %s", so_path)
        return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    if debug_format == "ctf":
        from .ctf_metadata import parse_ctf_metadata
        ctf = parse_ctf_metadata(so_path)
        if not ctf.has_ctf:
            log.warning("CTF requested but no .ctf section in %s", so_path)
        return ctf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    if debug_format == "dwarf":
        from .dwarf_unified import parse_dwarf
        return parse_dwarf(so_path)

    if debug_format is not None:
        raise ValueError(
            f"Invalid debug_format {debug_format!r}; expected 'dwarf', 'btf', or 'ctf'."
        )

    # Auto-detect: kernel binaries prefer BTF, userspace prefers DWARF
    from .btf_metadata import has_btf_section, parse_btf_metadata
    from .ctf_metadata import has_ctf_section, parse_ctf_metadata
    from .dwarf_unified import parse_dwarf

    is_kernel = _is_kernel_binary(so_path)

    if is_kernel:
        # BTF > DWARF > CTF for kernel binaries
        if has_btf_section(so_path):
            btf = parse_btf_metadata(so_path)
            if btf.has_btf:
                log.info("Using BTF debug info from %s (kernel binary)", so_path)
                return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # DWARF > BTF > CTF for userspace (or kernel fallback)
    dwarf_meta, dwarf_adv = parse_dwarf(so_path)
    if dwarf_meta.has_dwarf:
        return dwarf_meta, dwarf_adv

    # Fallback to BTF if DWARF not available
    if has_btf_section(so_path):
        btf = parse_btf_metadata(so_path)
        if btf.has_btf:
            log.info("No DWARF, falling back to BTF in %s", so_path)
            return btf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # Fallback to CTF
    if has_ctf_section(so_path):
        ctf = parse_ctf_metadata(so_path)
        if ctf.has_ctf:
            log.info("No DWARF/BTF, falling back to CTF in %s", so_path)
            return ctf.to_dwarf_metadata(), AdvancedDwarfMetadata()

    # No debug info at all — return empty DWARF metadata
    return dwarf_meta, dwarf_adv


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
            sym.name for sym in elf_meta.symbols
            if sym.sym_type in (SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE)
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        exported_dynamic_objects = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.OBJECT
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        exported_dynamic_tls = {
            sym.name for sym in elf_meta.symbols
            if sym.sym_type == SymbolType.TLS
            and is_abi_relevant_elf_symbol(
                sym.name,
                filter_transitive_runtime_symbols=filter_transitive_runtime_symbols,
            )
        }
        # Full set for CastxmlParser: determines PUBLIC vs ELF_ONLY visibility
        exported_dynamic = exported_dynamic_funcs | exported_dynamic_objects | exported_dynamic_tls
    return exported_dynamic, exported_dynamic_funcs, exported_dynamic_objects, exported_dynamic_tls


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
) -> tuple[AbiSnapshot | None, list[RecordType]]:
    """Attempt to build a snapshot from DWARF debug info.

    Returns ``(snapshot, dwarf_only_types)``.  When the snapshot should be
    used directly, *snapshot* is non-None.  When DWARF produced no symbols
    (and *dwarf_only* is False), *snapshot* is None and *dwarf_only_types*
    carries the partial type list for the symbol-only fallback path.
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
    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path),
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
) -> AbiSnapshot:
    """ELF-specific dump: pyelftools + debug info (DWARF/BTF/CTF) + header AST."""
    exported_dynamic, exported_static = _pyelftools_exported_symbols(so_path)
    from .elf_metadata import parse_elf_metadata
    elf_meta = parse_elf_metadata(so_path)
    exported_dynamic, exported_dynamic_funcs, exported_dynamic_objects, exported_dynamic_tls = (
        _elf_classify_symbols(elf_meta, exported_dynamic, library_name=so_path.name)
    )
    if symbols_only or debug_presence_only:
        from .dwarf_presence import cheap_debug_presence_metadata
        dwarf_meta, dwarf_adv = cheap_debug_presence_metadata(
            so_path,
            debug_format=debug_format,
        )
    else:
        dwarf_meta, dwarf_adv = _resolve_debug_metadata(so_path, debug_format)
    profile_hint = _lang_to_profile(lang)
    # ADR-003: Updated fallback chain
    # --dwarf-only → force DWARF mode regardless of headers
    # no headers + DWARF available -> DWARF-only mode with type-aware checks
    # no headers + no DWARF -> symbols-only mode
    dwarf_only_types: list[RecordType] = []
    if not (symbols_only or debug_presence_only) and (
        dwarf_only or (not headers and dwarf_meta.has_dwarf)
    ):
        snap, dwarf_only_types = _try_dwarf_snapshot(
            so_path, elf_meta, dwarf_meta, dwarf_adv,
            version, profile_hint, headers, dwarf_only,
        )
        if snap is not None:
            return snap
    if symbols_only or not headers:
        return _build_symbol_only_snapshot(
            so_path, version, elf_meta, dwarf_meta, dwarf_adv,
            exported_dynamic_funcs, exported_dynamic_objects, exported_dynamic_tls,
            dwarf_only_types, profile_hint,
        )

    parser = _header_ast_parser(
        headers, extra_includes, backend=header_backend, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        exported_dynamic=exported_dynamic, exported_static=exported_static,
        public_header_paths=[str(h) for h in headers] + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
        extra_hash_dirs=extra_hash_dirs,
    )

    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path),
        functions=parser.parse_functions(),
        variables=parser.parse_variables(),
        types=parser.parse_types(),
        enums=parser.parse_enums(),
        typedefs=parser.parse_typedefs(),
        constants=parser.parse_constants(),
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        # Reached only when headers were supplied and castxml ran (the no-header
        # and DWARF-only branches return earlier): this surface is header-parsed.
        from_headers=True,
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
        exp.name for exp in macho_meta.exports
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
            exp for exp in macho_meta.exports
            if exp.name and _is_abi_relevant_symbol(exp.name)
        ]
        macho_funcs = [exp for exp in _relevant if not exp.is_data]
        macho_vars = [exp for exp in _relevant if exp.is_data]

        return AbiSnapshot(
            library=dylib_path.name,
            version=version,
            source_path=str(dylib_path),
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

    # On macOS, C symbols have a leading underscore in the export table
    # (Mach-O convention). Strip it for matching against the header-AST names.
    exported_no_underscore: set[str] = set()
    for sym in exported_dynamic:
        if sym.startswith("_"):
            exported_no_underscore.add(sym[1:])
        else:
            exported_no_underscore.add(sym)
    parser = _header_ast_parser(
        headers, extra_includes, backend=header_backend, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        exported_dynamic=exported_no_underscore, exported_static=exported_no_underscore,
        public_header_paths=[str(h) for h in headers] + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
        extra_hash_dirs=extra_hash_dirs,
    )

    return AbiSnapshot(
        library=dylib_path.name,
        version=version,
        source_path=str(dylib_path),
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
        (exp.name or f"ordinal:{exp.ordinal}")
        for exp in pe_meta.exports
    }
    exported_static: set[str] = set(exported_dynamic)

    profile_hint = _lang_to_profile(lang)

    if not headers:
        # Advisory only (ADR-035 P6): info log, not a per-run UserWarning.
        log.info(
            "No headers provided — only PE exported symbols will be captured; "
            "type information will be missing."
        )
        return AbiSnapshot(
            library=dll_path.name,
            version=version,
            source_path=str(dll_path),
            functions=[
                Function(
                    name=sym, mangled=sym, return_type="?",
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
        headers, extra_includes, backend=header_backend, compiler=compiler,
        gcc_path=gcc_path, gcc_prefix=gcc_prefix, gcc_options=gcc_options,
        gcc_option_tokens=gcc_option_tokens,
        sysroot=sysroot, nostdinc=nostdinc, lang=lang,
        exported_dynamic=exported_dynamic, exported_static=exported_static,
        public_header_paths=[str(h) for h in headers] + [str(h) for h in (public_headers or [])],
        public_dir_paths=[str(d) for d in (public_header_dirs or [])],
        extra_hash_dirs=extra_hash_dirs,
    )

    return AbiSnapshot(
        library=dll_path.name,
        version=version,
        source_path=str(dll_path),
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
            b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
            b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
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
