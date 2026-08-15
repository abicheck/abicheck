# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""AST cache-key, language detection, and CastXML/Clang command helpers.

``_build_castxml_command`` and ``_build_clang_header_command`` are deliberate
neighbours: the two backends must parse the same TU under the same context, so
their flag handling (includes, sysroot, ``-nostdinc``, pass-through options,
C-vs-C++ mode, the C++20 bump) is meant to be read side by side. The clang
builder previously lived in ``dumper.py``, which sits at the AI-readiness
2000-line hard cap; it is pure argv construction with no dependency on the
dumper pipeline, so it lives here and ``dumper`` re-exports it (several callers
and tests import ``abicheck.dumper._build_clang_header_command``).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence
from pathlib import Path

from ._compiler_options import has_explicit_std, split_gcc_options
from .dumper_clang import _needs_sycl_host_only
from .header_utils import iter_cache_header_files


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
    frontend_identity: str = "",
    compiler_identity: str = "",
    force_cpp20: bool = False,
    frontend_context: str = "host",
) -> str:
    h = hashlib.sha256()
    h.update(f"backend={backend}".encode())
    # A "host" vs "device" request against the identical inputs resolves to
    # a genuinely different AST (ADR-050 D5, G32 Phase D) -- must not share
    # a cache entry. Harmless for castxml/plain-clang callers, which only
    # ever pass the default "host".
    h.update(f"frontend_context={frontend_context}".encode())
    h.update(f"frontend_identity={frontend_identity}".encode())
    h.update(f"compiler_identity={compiler_identity}".encode())
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
    # The *resolved* C++20 dialect decision (-std=gnu++20 or not), not just the
    # explicit --lang the caller passed (Codex review): _detect_cpp20_headers is
    # a heuristic that can itself change across an abicheck upgrade (a bug fix
    # to the detector, like a false-positive/negative correction) without any
    # header content or toolchain identity changing. Without this, upgrading
    # abicheck to fix such a detector bug would silently keep reusing a stale
    # AST parsed under the *old*, wrong dialect decision until the on-disk
    # cache was manually cleared — the cache key must depend on everything
    # that changes the frontend command, and this heuristic decision does.
    h.update(f"force_cpp20={force_cpp20}".encode())
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
_CPP_ONLY_PATTERNS = (
    re.compile(rb"^\s*class\s+\w+\s*[:{]"),  # class Foo { / class Foo :
    re.compile(rb"^\s*namespace\s+\w+"),  # namespace ns
    re.compile(rb"^\s*template\s*<"),  # template<...>
    re.compile(rb"^\s*using\s+\w+\s*="),  # using alias = ...
    re.compile(rb"^\s*public\s*:"),  # public:
    re.compile(rb"^\s*private\s*:"),  # private:
    re.compile(rb"^\s*protected\s*:"),  # protected:
    # C++ keywords that can appear anywhere in a line (not just at start)
    re.compile(rb"\bvirtual\s+"),  # virtual member functions
    re.compile(rb"(?<!\w)~\w+\s*\("),  # destructor ~Foo()
    re.compile(rb":\s*public\s+\w+"),  # struct Derived : public Base
    re.compile(rb":\s*private\s+\w+"),  # : private Base
    re.compile(rb":\s*protected\s+\w+"),  # : protected Base
    re.compile(rb"\bclass\s+\w+\s*[{;]"),  # class anywhere (forward decl or def)
    re.compile(rb"\bconst\s+\w[\w:]*\s*&"),  # const Type& reference (C++ idiom)
    re.compile(rb"\bstatic_cast\b"),  # C++ cast
    re.compile(rb"\bconstexpr\b"),  # C++ constexpr
    re.compile(rb"\bnullptr\b"),  # C++ nullptr
    re.compile(rb"\bnoexcept\b"),  # C++ noexcept
    re.compile(rb"\boverride\b"),  # C++ override specifier
)

# Full set used for auto language-mode detection (lang unspecified) and the
# failure hint: here ``extern "C"`` *does* count, because castxml always parses in
# a C++-ish mode, so an aggregate including an extern "C" header is built as .hpp.
# Both sets are tuples: they are read-only, and one of them is a default argument.
_CPP_PATTERNS = (_EXTERN_C_PATTERN, *_CPP_ONLY_PATTERNS)


def _detect_cpp_headers(
    header_paths: Sequence[Path],
    patterns: Sequence[re.Pattern[bytes]] = _CPP_PATTERNS,
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
    _cc_map = {
        "c++": "g++",
        "cc": "gcc",
        "g++": "g++",
        "gcc": "gcc",
        "clang++": "clang++",
        "clang": "clang",
    }

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
    cc_bin: str,
    cc_id: str,
    extra_includes: list[Path],
    out_xml: Path,
    agg_path: Path,
    *,
    sysroot: Path | None = None,
    nostdinc: bool = False,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
    force_cpp: bool = False,
    force_cpp20: bool = False,
    castxml_bin: str = "castxml",
) -> list[str]:
    """Build the castxml command line."""
    # CastXML needs its language-specific compiler-emulation id in C mode.
    # ``gnu`` + ``-x c`` can inject C++ _Float* approximations into C;
    # ``gnu-c`` avoids that. Parentheses preserve an explicit g++ path/prefix.
    castxml_cc_id = "gnu-c" if not force_cpp and cc_id == "gnu" else cc_id
    compiler_command = (
        ["(", cc_bin, "-x", "c", ")"] if castxml_cc_id == "gnu-c" else [cc_bin]
    )
    cmd = [
        castxml_bin,
        "--castxml-output=1",
        f"--castxml-cc-{castxml_cc_id}",
        *compiler_command,
    ]
    for inc in extra_includes:
        cmd += ["-I", str(inc)]

    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += split_gcc_options(gcc_options)
    # Repeatable --gcc-option: each value is one literal compiler argument,
    # appended verbatim (no shlex split) so a flag whose value contains
    # whitespace survives intact and identically on POSIX and Windows.
    cmd += list(gcc_option_tokens)

    explicit_std = has_explicit_std(gcc_options, gcc_option_tokens)
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
    dpcpp_multi_context: bool = False,
) -> list[str]:
    """Build the ``clang -ast-dump=json`` command for the aggregate header.

    Mirrors :func:`_build_castxml_command`'s flag handling (includes, sysroot,
    ``-nostdinc``, pass-through options, C-vs-C++ language mode and the C++20
    bump) so the clang backend parses the same TU under the same context.
    ``-fsyntax-only``/``-ferror-limit=0`` keeps parsing past recoverable
    errors so a single bad decl does not blank the whole dump.

    ``system_includes`` are host-compiler-probed system dirs (see
    :func:`_probe_gnu_system_includes`) injected as ``-isystem`` so clang
    finds the same libstdc++/libc headers castxml gets via
    ``--castxml-cc-gnu``. Emitted **last** (after the user's ``-I`` and
    pass-through ``--gcc-options``/``--gcc-option``) so a user-supplied
    ``-isystem`` for a cross/hermetic SDK wins. Skipped under ``-nostdinc``.

    On an Intel oneAPI driver, ``-fsycl-host-only`` is appended per
    :func:`_needs_sycl_host_only` (PR #643) -- skipped when
    ``dpcpp_multi_context`` is set, since that request wants both passes.

    ``dpcpp_multi_context`` (ADR-050 D5, G32 Phase D) adds ``-fsycl -v``
    when *cc_bin* is DPC++-capable (:func:`_is_dpcpp_family_binary`) --
    ``-fsycl`` splits the driver into a host + one-or-more-device
    compilation passes, and ``-v`` emits the ``-cc1 ... -triple <T> ...
    -fsycl-is-(host|device)`` stderr lines :mod:`abicheck.sycl_context`
    needs to correlate each stdout document back to a host/device ``kind``.
    """
    cmd = [cc_bin]
    for inc in extra_includes:
        cmd += ["-I", str(inc)]
    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += split_gcc_options(gcc_options)
    # Repeatable --gcc-option: one literal argument each (no shlex split).
    cmd += list(gcc_option_tokens)
    if not dpcpp_multi_context and _needs_sycl_host_only(cc_bin, cmd):
        cmd.append("-fsycl-host-only")
    # Auto-probed host system dirs go *after* the user's pass-through flags, so a
    # user-supplied -isystem (cross/hermetic SDK) keeps higher priority (Codex review).
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
    if dpcpp_multi_context:
        cmd += ["-fsycl", "-v"]
    cmd += [
        "-fsyntax-only",
        "-ferror-limit=0",
        "-Xclang",
        "-ast-dump=json",
        str(agg_path),
    ]
    return cmd
