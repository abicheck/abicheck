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
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path

from ..._compiler_options import (
    has_explicit_std,
    split_gcc_options,
)
from ...dumper_ast_config_cpp20 import _preprocessed_header_content
from ...header_utils import drop_include_tokens_duplicating_paths
from ...storage.tu_parse_store import content_key, digest_file
from ..cache_header_scan import iter_cache_header_files
from ..castxml_compiler_emulation import (
    castxml_parser_arguments,
    emulated_compiler_command,
)
from ..castxml_header_compat import (
    CASTXML_HEADER_PREAMBLE,
    PREAMBLE_FILENAME,
    castxml_aggregate_text,
)
from ..header_ast_cache_producers import (
    castxml_ast_output_fingerprint,
    clang_ast_output_fingerprint,
)
from .clang_header_argv import HeaderCompileContext, render_clang_header_argv

#: Bumped once (Codex review, fresh evidence, P2): a pre-existing on-disk
#: entry an OLDER binary wrote for a self-healed C-to-C++ dump was stored
#: under the pre-retry (C-mode) key -- exactly the key this hash still
#: computes for the identical input -- so it would otherwise stay silently
#: reachable and reintroduce the stale `resolved_force_cpp=False` bug the
#: write-side key fix (see `extract.headers.clang.backend.clang_header_dump`) closes only for
#: entries written from here on. Bump again if the clang cache format ever
#: changes in some other incompatible way.
#: Bumped to 4 when every clang AST pass started receiving
#: `CLANG_GCC_HEADER_COMPAT_DEFINES`: an entry written before could hold an
#: AST clang only partially recovered from GCC's `omp.h`.
_CLANG_CACHE_SCHEMA_VERSION = 4

#: Salts every castxml header-parse cache key. Bumped to 2 when castxml's
#: emulated compiler started receiving the run's standard/sysroot/target
#: flags (`extract.castxml_compiler_emulation`): an entry written before that
#: was parsed under the host compiler's default-standard macros, with the
#: same key inputs, so it must not be served afterwards.
#: Bumped to 3 when every aggregate started including
#: `extract.castxml_header_compat.CASTXML_HEADER_PREAMBLE`: on a target
#: whose `long double` is binary128 the same inputs now parse to a different
#: (complete) AST.
#: Bumped to 4 when the cached document started carrying the compiler-resolved
#: calling-convention macro table (`extract.headers.castxml.macro_table`).
#: Bumped to 5 when that table was replaced by the preprocessed text of each
#: convention-bearing declaration (`extract.headers.castxml.preprocessed_text`).
#: Bumped to 6 when vector types joined the clang AST: a castxml entry written
#: before this carries no vector spellings, so it must miss and re-run.
#: Bumped to 7 when vector sidecars keyed C++ overloads by mangled name and
#: resolved inline vector returns: a sidecar from before this is keyed wrongly.
#: Bumped to 8 when the cached document started carrying the preprocessor-
#: excluded declaration scan (`extract.headers.preprocessor_branches`).
#: Bumped to 9 when the clang join also spells complex and bit-precise integer
#: types (castxml's ``Complex``/``BitInt`` ``Unimplemented``): an entry written
#: before this left them as the one literal ``Unimplemented`` spelling.
_CASTXML_CACHE_SCHEMA_VERSION = 9

#: Which module constant salts each backend's header-parse cache key. Named,
#: not copied, so the key always reads the constant's current value.
_CACHE_SCHEMA_VERSION_NAMES: dict[str, str] = {
    "clang": "_CLANG_CACHE_SCHEMA_VERSION",
    "castxml": "_CASTXML_CACHE_SCHEMA_VERSION",
}


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
    force_cpp: bool | None = None,
    force_cpp20: bool = False,
    frontend_context: str = "host",
    invocation_tool: tuple[str, ...] = (),
) -> str:
    if backend == "castxml":
        return _castxml_store_key(
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
            system_includes=system_includes,
            extra_hash_dirs=extra_hash_dirs,
            frontend_identity=frontend_identity,
            compiler_identity=compiler_identity,
            force_cpp=force_cpp,
            force_cpp20=force_cpp20,
            invocation_tool=invocation_tool,
        )
    h = hashlib.sha256()
    h.update(f"backend={backend}".encode())
    schema_name = _CACHE_SCHEMA_VERSION_NAMES.get(backend)
    if schema_name is not None:
        schema = globals()[schema_name]
        h.update(f"{backend}_cache_schema={schema}".encode())
    # `force_cpp` is `None` only for a handful of call sites (e.g.
    # `_ast_compile_provenance`'s own probing helpers) that never resolve a
    # real language-mode decision at all; every real dump-producing call site
    # passes the bool it actually resolved. Folded in explicitly rather than
    # relying on `compiler_identity`/`system_includes` to differ incidentally
    # when it flips (they usually do -- castxml's gcc/g++ remap, clang's C-vs-
    # C++ system-include probe -- but not when an explicit `--gcc-path`/
    # `--gcc-prefix` cross-compiler pins one binary for both modes, and not
    # in every future caller of this cache key). Without this, a header
    # previously dumped in C mode and later re-resolved to C++ (e.g. by
    # `_exported_symbols_indicate_cpp`'s export-evidence fallback, once a
    # real binary is dumped after a header-only probe) could otherwise reuse
    # the stale C-mode AST under a pinned cross-compiler (CodeRabbit review).
    h.update(f"force_cpp={force_cpp}".encode())
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
    #
    # An entry naming a *file* rather than a directory is hashed directly, the
    # same way ``headers`` are above (Codex review, PR D). The directory walk
    # below is deliberately suffix-filtered (#454), which is right for "catch
    # transitive includes under a search root" but wrong for a file the caller
    # named explicitly because it is *itself* part of the parse: a forced
    # pre-include (``-include generated/config``, ``-imacros settings.def``)
    # routinely carries a suffix ``CACHE_HEADER_SUFFIXES`` does not list, so
    # hashing only its parent directory would let an edit to it reuse a stale
    # AST while the unchanged option token kept the key identical. Before this
    # branch a non-directory entry contributed only its path string, so this is
    # a strict widening — no previously-hashed input stops being hashed.
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
        else:
            try:
                h.update(str(inc_path.stat().st_mtime).encode())
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
    # That rule, by construction rather than per field: the exact aggregate
    # header and command line this key's run will hand the frontend, and for
    # clang the code that shapes the stored entry
    # (`extract.header_ast_cache_producers`). Empty *invocation_tool* (a probe
    # that never runs the frontend) folds nothing.
    if invocation_tool:
        h.update(
            "invocation={}".format(
                _invocation_digest(
                    backend,
                    invocation_tool,
                    headers,
                    extra_includes,
                    sysroot=sysroot,
                    nostdinc=nostdinc,
                    gcc_options=gcc_options,
                    gcc_option_tokens=gcc_option_tokens,
                    force_cpp=bool(force_cpp),
                    force_cpp20=force_cpp20,
                    system_includes=system_includes,
                )
            ).encode()
        )
    if backend == "clang":
        h.update(f"output_code={clang_ast_output_fingerprint()}".encode())
    return h.hexdigest()


def _content_digest(path: Path) -> str:
    """Content digest of a file, or of every header-like file under a directory.

    A missing path digests to a fixed marker, so an absent root and an empty
    directory cannot share a key with a present one that happens to hash alike.
    A file that cannot be read (a permission error, a dangling symlink, a
    directory named like a header) digests to its own marker: the key still
    computes, and it changes when the file becomes readable.
    """

    def _digest(file: Path) -> str:
        try:
            return digest_file(file)
        except OSError:
            return "unreadable"

    if path.is_file():
        return _digest(path)
    if path.is_dir():
        h = hashlib.sha256()
        for child in iter_cache_header_files(path):
            h.update(str(child).encode())
            h.update(_digest(child).encode())
        return h.hexdigest()
    return "missing"


def _castxml_store_key(
    headers: list[Path],
    extra_includes: list[Path],
    compiler: str,
    *,
    gcc_path: str | None,
    gcc_prefix: str | None,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    sysroot: Path | None,
    nostdinc: bool,
    lang: str | None,
    system_includes: tuple[str, ...],
    extra_hash_dirs: tuple[Path, ...],
    frontend_identity: str,
    compiler_identity: str,
    force_cpp: bool | None,
    force_cpp20: bool,
    invocation_tool: tuple[str, ...],
) -> str:
    """The castxml entry key: a content-addressed parse-store key (Phase 6).

    The inputs are the ones the old mtime key folded, with two changes: every
    header and include root is keyed by its content digest rather than its
    mtime, and the whole key is one :func:`~..storage.tu_parse_store.content_key`
    over ordered parts. ``frontend_context`` is not folded: castxml only ever runs
    in the host context, so the value was constant.

    The stored document is castxml's XML plus the preprocessed text and the
    preprocessor-excluded scan abicheck splices into it, so the key also folds
    :func:`castxml_ast_output_fingerprint` -- the source of the modules that
    compute those payloads -- rather than trusting the hand-bumped schema
    constant alone.
    """
    parts = [
        f"castxml_cache_schema={_CASTXML_CACHE_SCHEMA_VERSION}",
        f"castxml_output_code={castxml_ast_output_fingerprint()}",
        f"frontend_identity={frontend_identity}",
        f"compiler_identity={compiler_identity}",
        f"compiler={compiler}",
        f"gcc_path={gcc_path or ''}",
        f"gcc_prefix={gcc_prefix or ''}",
        f"gcc_options={gcc_options or ''}",
        f"gcc_option_tokens={json.dumps(list(gcc_option_tokens))}",
        f"sysroot={sysroot or ''}",
        f"nostdinc={nostdinc}",
        f"lang={lang or ''}",
        f"system_includes={json.dumps(list(system_includes))}",
        f"force_cpp={force_cpp}",
        f"force_cpp20={force_cpp20}",
    ]
    for header in sorted(str(p.resolve()) for p in headers):
        parts.append(f"header:{header}:{_content_digest(Path(header))}")
    for root in sorted(str(p) for p in (*extra_includes, *extra_hash_dirs)):
        parts.append(f"root:{root}:{_content_digest(Path(root))}")
    if invocation_tool:
        parts.append(
            "invocation="
            + _invocation_digest(
                "castxml",
                invocation_tool,
                headers,
                extra_includes,
                sysroot=sysroot,
                nostdinc=nostdinc,
                gcc_options=gcc_options,
                gcc_option_tokens=gcc_option_tokens,
                force_cpp=bool(force_cpp),
                force_cpp20=force_cpp20,
                system_includes=system_includes,
            )
        )
    return content_key(parts)


def clang_aggregate_text(headers: Sequence[Path]) -> str:
    """The clang aggregate header's text: one ``#include`` per header.

    Shared by ``extract.headers.clang.backend.clang_header_dump`` and the cache key, so a change to
    what clang is fed is a key change.
    """
    return "".join(f'#include "{h.resolve()}"\n' for h in headers)


def _invocation_digest(
    backend: str,
    tool: tuple[str, ...],
    headers: list[Path],
    extra_includes: list[Path],
    *,
    sysroot: Path | None,
    nostdinc: bool,
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    force_cpp: bool,
    force_cpp20: bool,
    system_includes: tuple[str, ...],
) -> str:
    """Hash the aggregate header and command line a run would use.

    Built with the same builders the run calls, with fixed placeholders for
    the per-run temporary paths. *tool* is ``(cc_bin, cc_id, castxml_bin)``
    for castxml and ``(clang_bin, cc_id, dpcpp_multi, dpcpp_host)`` for clang.
    """
    agg = Path("<abicheck-aggregate>" + (".hpp" if force_cpp else ".h"))
    if backend == "clang":
        clang_bin, cc_id, multi, host = tool
        text = clang_aggregate_text(headers)
        argv = _build_clang_header_command(
            clang_bin,
            cc_id,
            list(extra_includes),
            agg,
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=force_cpp,
            force_cpp20=force_cpp20,
            system_includes=system_includes,
            dpcpp_multi_context=multi == "True",
            dpcpp_host_context=host == "True",
        )
    else:
        cc_bin, cc_id, castxml_bin = tool
        preamble = Path("<abicheck-preamble>") / PREAMBLE_FILENAME
        text = castxml_aggregate_text(headers, preamble) + CASTXML_HEADER_PREAMBLE
        argv = _build_castxml_command(
            cc_bin,
            cc_id,
            list(extra_includes),
            Path("<abicheck-out.xml>"),
            agg,
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=force_cpp,
            force_cpp20=force_cpp20,
            castxml_bin=castxml_bin,
        )
    h = hashlib.sha256(text.encode())
    h.update("\0".join(argv).encode())
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


#: C/C++ keywords excluded from the identifier candidate set
#: :func:`_header_declared_identifiers` collects -- a keyword can never
#: itself be a mangled function/variable's own source-level name, so
#: including it would only add correlation-check noise (and, in principle,
#: risk on odd coincidental substring matches) for no benefit. Not
#: exhaustive -- just the common ones plausible in a public header -- since
#: an omitted keyword is merely a slightly wider (still correct, since a
#: keyword is never itself a declared name) candidate, not a correctness bug.
_C_CXX_KEYWORDS = frozenset(
    {
        "int", "char", "void", "float", "double", "long", "short", "signed",
        "unsigned", "const", "volatile", "static", "extern", "struct",
        "union", "enum", "class", "namespace", "typedef", "sizeof", "return",
        "if", "else", "for", "while", "do", "switch", "case", "default",
        "break", "continue", "goto", "define", "include", "ifndef", "ifdef",
        "endif", "pragma", "undef", "elif", "template", "typename", "public",
        "private", "protected", "virtual", "inline", "friend", "operator",
        "new", "delete", "this", "true", "false", "nullptr", "bool",
        "using", "explicit", "constexpr", "noexcept", "override", "final",
    }
)  # fmt: skip

_IDENTIFIER_RE = re.compile(rb"\b[A-Za-z_]\w*\b")


def _header_declared_identifiers(headers: Sequence[Path]) -> frozenset[str]:
    """Every plausible declared-name token appearing in *headers*' own
    ACTIVE, non-comment, non-literal text.

    A cheap, pre-parse (regex) substitute for "what does this header
    declare" -- at the point :func:`_resolve_force_cpp` runs, the header
    hasn't been parsed yet (that's what this function's language-mode
    decision feeds into), so there is no real declaration list to check
    against. Deliberately broad (every identifier-shaped token in the
    scanned text, keywords excluded) rather than trying to regex-parse real
    declaration syntax: the caller only ever uses this to *correlate* a
    candidate export against something this header's own text actually
    mentions, so a wider candidate set only widens what can be confirmed,
    never what can be wrongly promoted -- correctness comes from the
    correlation step requiring an exact Itanium length-prefixed substring
    match, not from this set being minimal.

    "Actually mentions", though, means real source text -- not a comment, a
    string/char literal, or a permanently-unreachable ``#if 0``/``#if
    false`` arm (CodeRabbit review, fresh evidence: an incidental name match
    in `// TODO: compute this differently` or `"compute"` must not
    correlate against a real, unrelated exported ``compute`` symbol from
    elsewhere in the binary -- the same whole-binary-over-correlation bug
    this function's own correlation step was written to close, one level
    less obvious). Reuses :func:`dumper_ast_config_cpp20.
    _preprocessed_header_content`'s existing comment/string/raw-string/
    inactive-``#if 0``-block stripping (already exercised by the C++20
    detector on this exact file set) rather than a second, independent
    stripper. **Known residual limitation, matching this repo's own
    documented-gap convention:** a general preprocessor/macro-expansion
    evaluator is out of scope here (as it is for the C++20 detector this
    reuses) -- an identifier appearing only as a macro parameter name or
    inside an ``#ifdef``/``#ifndef`` branch this heuristic can't evaluate
    (anything other than a literal ``0``/``1``/``false``/``true`` guard)
    still counts as a candidate. That is the conservative direction: it can
    only widen what the correlation step *might* confirm against a real
    export, never fabricate one, since correlation still requires the
    exact Itanium length-prefixed substring match.
    """
    names: set[str] = set()
    for p in headers:
        prepared = _preprocessed_header_content(p, for_language_mode_decision=True)
        if prepared is None:
            continue
        scan_content, _shadow_content = prepared
        for m in _IDENTIFIER_RE.finditer(scan_content):
            name = m.group().decode("ascii", errors="ignore")
            if name and name not in _C_CXX_KEYWORDS:
                names.add(name)
    return frozenset(names)


def _exported_symbols_indicate_cpp(
    exported_symbols: frozenset[str], declared_identifiers: frozenset[str]
) -> bool:
    """True if *exported_symbols* contains a real C++/MSVC mangled name that
    correlates with one of *this header's own* ``declared_identifiers``.

    A header with no structural C++ syntax at all (a plain top-level function
    declaration, no ``class``/``namespace``/``template``/``extern "C"`` — the
    common shape of a small, focused public header) gives
    :func:`_detect_cpp_headers` nothing to key on and auto-detection silently
    settles on C, even when the header is unambiguously part of a C++
    library and was compiled as such. Unlike header *syntax*, the binary's
    own already-computed export table is direct, unambiguous evidence: a
    real Itanium (``_Z...``), Mach-O Itanium (``__Z...``), or MSVC (``?...``)
    mangled name can only come from a C++ (or C++-linkage) compile — plain C
    has no name mangling at all, so a C compile can never produce one. This
    generalizes the identical, narrower per-declaration reasoning
    :func:`abicheck.extract.headers.castxml.functions.parse_function_element`
    already applies (its "case141" override, which corrects one already-
    mis-parsed declaration's *symbol* after the fact) to the *whole-TU
    language-mode decision* itself — the earlier, root-cause point where a
    C-mode parse also produces wrong ``is_extern_c``/visibility for every
    declaration in the header, not merely a wrong mangled name for one
    function (the actual defect behind ``exported_not_public``/
    ``public_not_exported`` firing as a contradictory pair for a perfectly
    ordinary, unnamespaced C++ function with no C++-specific syntax in its
    own declaration).

    Checked against the *whole* export table (dynamic and static symbols
    alike, exactly like the per-declaration override's own
    ``exported_dynamic | exported_static`` union) -- a multi-TU binary's
    export table is not partitioned by which header declared which symbol,
    so there is no narrower *table* to check against -- but **correlated**
    against *declared_identifiers* (this header's own text, see
    :func:`_header_declared_identifiers`) before counting as evidence: an
    Itanium mangled name embeds its own source-level identifier(s) as
    length-prefixed substrings (``plain_func`` in ``_Z10plain_funci`` as the
    literal substring ``"10plain_func"``), and an MSVC mangled name embeds
    its own leaf identifier immediately after the leading ``?`` (``main_op``
    in ``?main_op@@YAHH@Z`` as the literal substring ``"?main_op@"``), so
    requiring that embedding confirms the *export* actually names something
    *this header* plausibly declares, not merely some unrelated C++ symbol
    compiled from a completely different TU into the same binary (CodeRabbit
    review, fresh evidence: a genuinely plain-C header declaring
    ``struct options { int new; };`` -- valid C, ``new`` a reserved word
    only in C++ -- must not be forced into C++ mode merely because some
    *other* header in the same library happens to export real C++ symbols;
    the whole-binary-export check the fix originally used had exactly that
    failure mode). No demangling is performed -- the length-prefixed/``?``-
    anchored substring tests are specific/false-positive-resistant enough on
    their own (matching a real mangled name's own internal encoding, not
    just the bare name), and avoid adding a ``c++filt``/demangler dependency
    to a hot, whole-TU decision path.
    """
    if not declared_identifiers:
        return False
    itanium_candidates = tuple(f"{len(name)}{name}" for name in declared_identifiers)
    msvc_candidates = tuple(f"?{name}@" for name in declared_identifiers)
    for sym in exported_symbols:
        if sym.startswith("_Z") or sym.startswith("__Z"):
            if any(token in sym for token in itanium_candidates):
                return True
        elif sym.startswith("?"):
            if any(token in sym for token in msvc_candidates):
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
    # Built as the arguments castxml's own parser sees; the emulated compiler
    # gets the subset that changes its predefined macros or system search
    # path (`extract.castxml_compiler_emulation`), so the two agree on the
    # language standard, sysroot and target. For C this always includes
    # `-x c`, which is what the `gnu-c` group has always carried.
    cmd: list[str] = []
    for inc in extra_includes:
        cmd += ["-I", str(inc)]

    if sysroot:
        cmd += [f"--sysroot={sysroot.as_posix()}"]
    if nostdinc:
        cmd += ["-nostdinc"]
    if gcc_options:
        cmd += split_gcc_options(gcc_options)
    # Repeatable --compiler-option: each value is one literal compiler argument,
    # appended verbatim (no shlex split) so a flag whose value contains
    # whitespace survives intact and identically on POSIX and Windows.
    # Dropped when an entry duplicates a directory already emitted above via
    # `extra_includes` — see `drop_include_tokens_duplicating_paths`'s own
    # docstring for why this can otherwise happen and what it broke.
    cmd += drop_include_tokens_duplicating_paths(gcc_option_tokens, cmd)

    explicit_std = has_explicit_std(gcc_options, gcc_option_tokens)
    # Workaround: castxml with --castxml-cc-gnu gcc auto-injects -std=gnu++17
    # which is rejected when parsing a .h file in C mode. Force C mode, but only
    # impose gnu11 when the user did not request a C standard via --compiler-option
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

    cmd = castxml_parser_arguments(cmd)
    return [
        castxml_bin,
        "--castxml-output=1",
        f"--castxml-cc-{castxml_cc_id}",
        *emulated_compiler_command(cc_bin, castxml_cc_id, cmd),
        *cmd,
        "-o",
        str(out_xml),
        str(agg_path),
    ]


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
    dpcpp_host_context: bool = False,
) -> list[str]:
    """Build the ``clang -ast-dump=json`` command for the aggregate header.

    The argv is rendered by :func:`.clang_header_argv.render_clang_header_argv`.
    ``cc_id`` is kept for the shared builder signature; the clang argv does not depend on it.
    """
    return render_clang_header_argv(
        HeaderCompileContext(
            cc_bin=cc_bin,
            extra_includes=tuple(extra_includes),
            sysroot=sysroot,
            nostdinc=nostdinc,
            gcc_options=gcc_options,
            gcc_option_tokens=gcc_option_tokens,
            force_cpp=force_cpp,
            force_cpp20=force_cpp20,
            system_includes=system_includes,
            dpcpp_multi_context=dpcpp_multi_context,
            dpcpp_host_context=dpcpp_host_context,
        ),
        agg_path,
    )
