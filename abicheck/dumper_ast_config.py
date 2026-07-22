# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""AST cache-key, language detection, and CastXML command helpers."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ._compiler_options import has_explicit_std
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
) -> str:
    h = hashlib.sha256()
    h.update(f"backend={backend}".encode())
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
# over-trigger. Matching is applied only to *code* text (see
# ``_find_cpp20_requirements``): preprocessor directive lines, string/char
# literal contents, and comments are stripped first, so "requires" appearing
# in a `#error`/`#define` message or a string literal is never mistaken for
# the C++20 keyword (Codex/false-positive report).
_CPP20_CONCEPT_PATTERN = re.compile(rb"\bconcept\s+\w+\s*=")  # concept Addable = ...
_CPP20_REQUIRES_EXPR_PATTERN = re.compile(
    rb"\brequires\s*\("
)  # requires(T a, T b) { ... }
_CPP20_REQUIRES_CLAUSE_PATTERN = re.compile(
    rb"\brequires\s+\w"
)  # template<T> requires Foo<T>

_STRING_LITERAL_PATTERN = re.compile(rb'"(?:\\.|[^"\\\n])*"')
_CHAR_LITERAL_PATTERN = re.compile(rb"'(?:\\.|[^'\\\n])*'")


def _strip_literals(line: bytes) -> bytes:
    """Blank out string/char literal contents.

    Prevents a keyword that only appears *inside* a string (e.g. an error
    message like ``"Foo requires Base"``) from being mistaken for C++
    structural syntax.
    """
    line = _STRING_LITERAL_PATTERN.sub(b'""', line)
    line = _CHAR_LITERAL_PATTERN.sub(b"''", line)
    return line


def _iter_logical_lines(content: bytes) -> list[tuple[int, bytes]]:
    """Split *content* into ``(1-based start line, logical line)`` pairs.

    Backslash-newline continuations are joined into a single logical line so
    a ``#define``/``#error`` directive spanning multiple physical lines is
    classified as one directive rather than leaking its continuation lines
    into ordinary code scanning.
    """
    physical = content.split(b"\n")
    logical: list[tuple[int, bytes]] = []
    start_no = 1
    buf: list[bytes] = []
    for i, raw in enumerate(physical, start=1):
        line = raw.rstrip(b"\r")
        if not buf:
            start_no = i
        if line.endswith(b"\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        logical.append((start_no, b"\n".join(buf)))
        buf = []
    if buf:
        logical.append((start_no, b"\n".join(buf)))
    return logical


def _is_preprocessor_directive(line: bytes) -> bool:
    return re.match(rb"^\s*#", line) is not None


@dataclass(frozen=True)
class Cpp20Requirement:
    """A single structural C++20 construct found while scanning headers."""

    reason: str  # "concept-declaration" | "requires-expression" | "requires-clause"
    path: str
    line: int


def _find_cpp20_requirements(header_paths: list[Path]) -> list[Cpp20Requirement]:
    """Scan *header_paths* for structural C++20 syntax, with reasons/locations.

    Conservative and directive/literal/comment-aware: only definition-site
    syntax in actual code counts, never the same keywords appearing inside a
    preprocessor diagnostic message, a string/char literal, or a comment.
    """
    found: list[Cpp20Requirement] = []
    for p in header_paths:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        # Blank string/char literals first so a literal containing comment-like
        # text ("/* not a comment */") is never mistaken for a real comment.
        content = _strip_literals(content)
        # Strip real block comments, but preserve the embedded newline count so
        # later-reported line numbers stay accurate for code following a
        # multi-line comment (CodeRabbit review).
        content = re.sub(
            rb"/\*.*?\*/",
            lambda m: b"\n" * m.group(0).count(b"\n"),
            content,
            flags=re.DOTALL,
        )
        for start_no, logical in _iter_logical_lines(content):
            if _is_preprocessor_directive(logical):
                continue
            code = _strip_literals(logical)
            code = code.split(b"//")[0]
            if _CPP20_CONCEPT_PATTERN.search(code):
                found.append(Cpp20Requirement("concept-declaration", str(p), start_no))
            elif _CPP20_REQUIRES_EXPR_PATTERN.search(code):
                found.append(Cpp20Requirement("requires-expression", str(p), start_no))
            elif _CPP20_REQUIRES_CLAUSE_PATTERN.search(code):
                found.append(Cpp20Requirement("requires-clause", str(p), start_no))
    return found


def _detect_cpp20_headers(header_paths: list[Path]) -> bool:
    """Return True if any header contains C++20-only syntax (concept/requires).

    Used to decide whether to pass ``-std=gnu++20`` to castxml. castxml's
    default standard is whatever the underlying compiler defaults to
    (usually C++17 on modern gcc), which does not accept ``concept``
    declarations. This detection is conservative: only definition-site
    syntax counts, not the keyword in arbitrary text — see
    ``_find_cpp20_requirements`` for the directive/literal/comment-aware scan.
    """
    return bool(_find_cpp20_requirements(header_paths))


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
        cmd += shlex.split(gcc_options, posix=os.name != "nt")
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
