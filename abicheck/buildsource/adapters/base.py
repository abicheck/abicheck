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

"""Shared adapter contract and helpers (ADR-029).

The :class:`BuildAdapter` protocol is the minimal contract every build-system
adapter implements; the free functions are the normalization helpers shared
across adapters (language detection, compile-unit identity, ABI-flag
extraction). Keeping these here avoids each adapter re-deriving them.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from ..build_evidence import BuildEvidence, BuildOption, CompileUnit

# Source-file extension → normalized language token.
_LANG_BY_EXT: dict[str, str] = {
    ".c": "C",
    ".i": "C",
    ".cc": "CXX",
    ".cpp": "CXX",
    ".cxx": "CXX",
    ".c++": "CXX",
    ".cp": "CXX",
    ".ii": "CXX",
    ".hpp": "CXX",
    ".hh": "CXX",
    ".hxx": "CXX",
    ".m": "OBJC",
    ".mm": "OBJCXX",
    ".cu": "CUDA",
}

#: ABI/API-affecting compiler-flag prefixes (ADR-029 D9). Drift in any of these
#: is treated as a risk signal by the build-evidence diff, not mere noise.
#:
#: A flag here is captured per-TU into ``CompileUnit.abi_relevant_flags`` and
#: projected into a global ``BuildOption`` by :func:`derive_build_options`; the
#: build-evidence diff (``build_diff._diff_options``) then surfaces any drift as
#: ``ABI_RELEVANT_BUILD_FLAG_CHANGED`` (unless the flag is a runtime-mode or
#: toolchain flag with a dedicated finding). Per ADR-028 D3 these are risk/
#: source-level signals — the artifact diff proves any concrete break.
ABI_RELEVANT_FLAG_PREFIXES: tuple[str, ...] = (
    "-std=",
    "/std:",
    "-stdlib=",
    "--target=",
    "-target",
    "-mabi=",
    "/arch:",
    "-m32",
    "-m64",
    # Microarchitecture / float ABI — can change struct alignment (vector ABI)
    # or the hard/soft-float calling convention (ARM), so a flip is a risk.
    "-march=",
    "-mtune=",
    "-mfloat-abi=",
    "-mfpmath=",
    "--sysroot",
    "-isysroot",
    "-fvisibility",
    "-fvisibility-inlines-hidden",
    "-fpack-struct",
    "/Zp",
    "-fshort-enums",
    "-fno-short-enums",
    "-fshort-wchar",
    "-fsigned-char",
    "-funsigned-char",
    "-fabi-version",
    "-fno-rtti",
    "-frtti",
    "-fno-exceptions",
    "-fexceptions",
    "-flto",
    "-fno-lto",
    "-fwhole-program-vtables",
    "-fno-whole-program-vtables",
    "-ftls-model",
    "-fextern-tls-init",
    "-fno-extern-tls-init",
    "-fno-threadsafe-statics",
    "-fthreadsafe-statics",
    "-freg-struct-return",
    "-fpcc-struct-return",
    # Sanitizers change object layout/instrumentation and the runtime contract.
    "-fsanitize=",
    "-fno-sanitize=",
    # Position-independence / TLS access model and frame-pointer presence.
    "-fPIC",
    "-fpic",
    "-fPIE",
    "-fpie",
    "-fno-pic",
    "-fno-pie",
    "-fno-PIC",
    "-fno-PIE",
    "-fomit-frame-pointer",
    "-fno-omit-frame-pointer",
    # SYCL language mode (icpx/dpcpp -fsycl/-fsycl-*): enables an entirely
    # different parse (implicit SYCL builtins/attributes, kernel-lambda
    # codegen), so its presence/absence is exactly the kind of parse-affecting
    # signal this list already tracks for -std=/-fvisibility/etc. Without
    # this, a resolved --gcc-path override that now actually invokes icpx/
    # dpcpp for L4 replay (previously always a bare "clang") would replay a
    # SYCL TU as plain C++, silently missing built-in SYCL state (Codex
    # review). Only the flag's *presence*, carried via replay_extra_flags(),
    # not a from-scratch reconstruction of the real build's host/device
    # multi-pass invocation -- see the "Known gaps" entry in AGENTS.md.
    # "-fno-sycl" is a separate, non-prefix-matching spelling (it does not
    # start with "-fsycl") that explicitly disables SYCL mode again -- an
    # "icpx -fsycl -fno-sycl" (last-flag-wins) command recorded only
    # "-fsycl" without it, so replay reconstructed the wrong (SYCL-enabled)
    # AST for what the real build actually compiled as plain C++ (Codex
    # review, second round). Extraction is argv-order-preserving, so
    # capturing both spellings lets the same last-flag-wins semantics reach
    # replay unchanged.
    "-fsycl",
    "-fno-sycl",
)

#: Runtime-model flags normalized to a canonical (key, value) so a mode flip is
#: diffable regardless of spelling/order, and absence (= compiler default) never
#: reads as a change. The build-evidence diff routes these canonical keys to the
#: dedicated EXCEPTIONS/RTTI/TLS/THREADSAFE_STATICS_MODE_CHANGED findings.
#: Keys here are intentionally distinct from the raw-flag keys other options use.
_RUNTIME_MODE_FLAGS: dict[str, tuple[str, str]] = {
    "-fexceptions": ("exceptions", "on"),
    "-fno-exceptions": ("exceptions", "off"),
    "-frtti": ("rtti", "on"),
    "-fno-rtti": ("rtti", "off"),
    "-fextern-tls-init": ("tls_init", "extern"),
    "-fno-extern-tls-init": ("tls_init", "local"),
    "-fthreadsafe-statics": ("threadsafe_statics", "on"),
    "-fno-threadsafe-statics": ("threadsafe_statics", "off"),
    # Language-agnostic layout/codegen mode flips. Their compiler default is
    # known and language-independent (int-sized enums / natural packing / no
    # LTO), so the build-evidence diff can compare an explicit value against an
    # omitted flag; char signedness is target-dependent and handled with a
    # both-sides-explicit rule in build_diff. Keys are intentionally bare (not
    # in _LANG_QUALIFIED_MODE_KEYS) so they are recorded unqualified.
    "-fshort-enums": ("enum_size", "short"),
    "-fno-short-enums": ("enum_size", "int"),
    "-fsigned-char": ("char_signedness", "signed"),
    "-funsigned-char": ("char_signedness", "unsigned"),
    "-flto": ("lto", "on"),
    "-fno-lto": ("lto", "off"),
    # Whole-program devirtualization: enabling it lets the linker elide/rewrite
    # vtable slots and typeinfo across TUs, so mixing a -fwhole-program-vtables
    # build with one without it is ABI-risky. Default off (known).
    "-fwhole-program-vtables": ("whole_program_vtables", "on"),
    "-fno-whole-program-vtables": ("whole_program_vtables", "off"),
}

#: Runtime-mode keys whose compiler default depends on the source language
#: (C++ vs C), so the option is recorded as ``<key>:<lang>`` (like ``std:<lang>``)
#: and the build-evidence diff infers the per-language default for an omitted
#: flag. TLS keys are language-agnostic and are not qualified.
_LANG_QUALIFIED_MODE_KEYS: frozenset[str] = frozenset(
    {"exceptions", "rtti", "threadsafe_statics"}
)
# Known limitation: a TU that omits a runtime-mode flag entirely contributes no
# option (the compiler default is implicit), so a *mixed* build where some TUs
# are default-on and others carry an explicit ``-fno-*`` records only the
# explicit value. A partial flip within such a heterogeneous library can be
# under-reported here; the artifact diff still proves any concrete break, and
# building a whole library half-and-half is rare. The common all-or-nothing
# mode flip across versions is detected.

#: ``-flto*`` spellings that only *tune* an existing LTO build (backend
#: parallelism / partitioning / compression) rather than enabling LTO. They are
#: not ABI-relevant on their own, but they share the ``-flto`` prefix, so they
#: must be explicitly excluded from extraction or the generic option path would
#: report a standalone tuning flag as ABI_RELEVANT_BUILD_FLAG_CHANGED (Codex).
_NON_ABI_LTO_TUNING_PREFIXES: tuple[str, ...] = (
    "-flto-partition=",
    "-flto-jobs=",
    "-flto-compression-level=",
    "-flto-incremental=",
)

#: ABI-relevant flags whose value is a *separate*, following argv token
#: rather than a combined ``=``/immediate-suffix spelling -- confirmed
#: against a real ``clang -cc1 --help``: ``-target-abi <value>``,
#: ``-target-cpu <value>``, ``-target-feature <value>``,
#: ``-target-linker-version <value>`` are all two-token forms (unlike
#: ``-target-sdk-version=<value>``, already combined). Each shares the
#: ``-target`` prefix already matched by ``ABI_RELEVANT_FLAG_PREFIXES``, so
#: without this the naive prefix match below captured only the flag token
#: itself and silently discarded its operand -- a real, information-losing
#: bug (P2 review, ``discussion_r3787772666``): two compile units disagreeing
#: only on e.g. ``-target-abi aapcs`` vs. ``-target-abi aapcs16`` read as
#: identical once the operand was dropped, and any caller replaying the
#: bare, valueless survivor got a syntactically incomplete command.
#:
#: Deliberately **not** extended to the bare ``-target``/``--sysroot``/
#: ``-isysroot`` split forms those same three prefixes also match: unlike
#: the four flags here, each of those already has its own dedicated
#: structured ``CompileUnit`` field (``target_triple``/``sysroot``) derived
#: by a separate, correct parse of the same argv (``build_context.py``), so
#: their raw split-form survivor carries no additional information -- adding
#: it here would only introduce a new value-bearing list entry
#: (``derive_build_options``/``source_graph.py`` iterate this list treating
#: each entry as one independent flag) with nothing to gain.
#:
#: Normalized to one internal ``<flag>=<value>`` token, not two separate
#: list entries -- ``-target-abi=<value>`` is not real clang syntax (unlike
#: ``-D<KEY>=<VALUE>``, which genuinely is both split- and combined-form
#: valid, the reason the ``-D``/``-U`` handling below concatenates the raw
#: tokens directly), it is a purely-internal, round-trippable encoding.
#: Every other reader of ``CompileUnit.abi_relevant_flags``
#: (``derive_build_options``, ``crosscheck_coherence.py``,
#: ``source_graph.py``, ``call_graph.py``'s safe-replay allowlist, which does
#: not include any of these four prefixes at all) already treats "one list
#: entry = one flag" -- keeping that invariant means none of them need any
#: change to stay correct in the presence of this fix, and a genuinely
#: differing value still makes two units' entries compare unequal for
#: ambiguity grouping. Only :func:`~abicheck.buildsource.header_compile_
#: context._split_operand_survivor` (the one consumer that replays this into
#: a real command) splits the encoding back into the two literal argv tokens
#: a real compiler invocation needs.
#:
#: **Every one of these four is a cc1-only flag, and a normal Clang** *driver*
#: **invocation never passes one bare -- each is individually wrapped in its
#: own ``-Xclang``, not just written after a single leading ``-Xclang``**
#: (P2 review, ``discussion_r3788073752``, fresh evidence): confirmed with the
#: installed CLI that ``clang -cc1 --help`` documents ``-target-abi <value>``,
#: while ``clang -target-abi aapcs`` is rejected outright ("unknown argument
#: '-target-abi'; did you mean '-Xclang -target-abi'"), and the real,
#: supported spelling is ``-Xclang -target-abi -Xclang aapcs`` -- each token
#: individually prefixed. :func:`extract_abi_relevant_flags` recognizes this
#: ``-Xclang <flag> -Xclang <value>`` wrapping explicitly (checked *before*
#: the bare two-token branch below, since a bare-form match would otherwise
#: consume the second ``-Xclang`` token itself as the operand and silently
#: drop the real value one token later) and normalizes it into a second,
#: distinct internal encoding, ``-Xclang <flag>=<value>`` (a literal space
#: after ``-Xclang``, never colliding with a real flag spelling since nothing
#: in this codebase ever emits a bare ``-Xclang`` token from this function on
#: its own) -- so :func:`~abicheck.buildsource.header_compile_context.
#: _split_operand_survivor` can tell which of the two real argv shapes
#: (``["-target-abi", "<value>"]`` vs. ``["-Xclang", "-target-abi", "-Xclang",
#: "<value>"]``) to reconstruct at replay time.
_SPLIT_OPERAND_ABI_FLAGS = frozenset(
    {
        "-target-abi",
        "-target-cpu",
        "-target-feature",
        "-target-linker-version",
    }
)

#: Macro defines whose value is ABI-relevant even though they're plain -D flags.
_ABI_RELEVANT_DEFINES: tuple[str, ...] = (
    "_GLIBCXX_USE_CXX11_ABI",
    "_ITERATOR_DEBUG_LEVEL",
    "_LIBCPP_ABI_VERSION",
    # libstdc++ debug/hardening modes change std:: container layout & size, so a
    # consumer built without them is ABI-incompatible with a library built with
    # them (routed to STDLIB_DEBUG_MODE_CHANGED by build_diff).
    "_GLIBCXX_DEBUG",
    "_GLIBCXX_ASSERTIONS",
)


@runtime_checkable
class BuildAdapter(Protocol):
    """Contract for a build-system evidence adapter (ADR-028 D6, ADR-032).

    ``name`` is the stable extractor identifier recorded in the manifest.
    ``collect`` returns normalized :class:`BuildEvidence`; it must never run
    build commands or execute project code by default — it only reads existing
    build outputs and pre-captured query output.
    """

    name: str

    def collect(self) -> BuildEvidence: ...


def detect_language(source: str) -> str:
    """Return the normalized language token for a source path ("C"/"CXX"/...)."""
    lower = source.lower()
    for ext, lang in _LANG_BY_EXT.items():
        if lower.endswith(ext):
            return lang
    return ""


#: GNU ``-x <language>`` operand → normalized language. ``none`` cancels an
#: earlier ``-x`` and reverts to extension-based detection. Unknown languages
#: (assembler, ``cuda``, …) leave the forced language unchanged.
_X_LANG_TO_NORMALIZED: dict[str, str] = {
    "c": "C",
    "c-header": "C",
    "cpp-output": "C",
    "objective-c": "OBJC",
    "objective-c-header": "OBJC",
    "objc-cpp-output": "OBJC",
    "c++": "CXX",
    "c++-header": "CXX",
    "c++-cpp-output": "CXX",
    "objective-c++": "OBJCXX",
    "objective-c++-header": "OBJCXX",
    "objc++-cpp-output": "OBJCXX",
}


def effective_language(argv: list[str], source: str) -> str:
    """Normalized language honoring a forced ``-x <lang>`` / MSVC ``/Tp``/``/Tc``.

    The command line overrides the source extension: ``g++ -x c++ -c foo.c``
    compiles C++ even though ``foo.c`` reads as C, and MSVC ``/TP`` / ``/Tp<f>``
    (force C++) / ``/TC`` / ``/Tc<f>`` (force C) do the same. The last forcing
    token wins for a single-source TU. Falls back to :func:`detect_language` on
    the source path when nothing on the command line forces the language.

    The scan is option-parser aware: operands consumed by another value-taking
    flag (for example ``-MF -xc``) are skipped so filenames that begin with
    language-option spellings cannot masquerade as real language overrides.
    """
    forced = ""
    msvc = _is_msvc_command(argv)
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "-x" and i + 1 < len(argv):
            tok = argv[i + 1].lower()
            forced = "" if tok == "none" else _X_LANG_TO_NORMALIZED.get(tok, forced)
            i += 2
            continue
        if arg in SOURCE_OPERAND_FLAGS:
            i += 2  # skip the flag and the operand it consumes
            continue
        if arg.startswith("-x") and len(arg) > 2:
            tok = arg[2:].lower()
            forced = "" if tok == "none" else _X_LANG_TO_NORMALIZED.get(tok, forced)
        elif msvc and (arg == "/TP" or arg[:3] == "/Tp"):
            forced = "CXX"
        elif msvc and (arg == "/TC" or arg[:3] == "/Tc"):
            forced = "C"
        i += 1
    return forced or detect_language(source)


#: Value-taking compiler flags whose *operand* is not the translation unit even
#: when it looks source-like — e.g. ``-include config.hpp`` (GNU forced header),
#: ``-x c++``, or the MSVC/clang-cl ``/FI`` / ``/FU`` forced-include/using flags.
#: Skipping the operand keeps :func:`source_from_argv` from mistaking a forced or
#: precompiled header for the real source. Combined MSVC forms like
#: ``/FIconfig.hpp`` are handled by the ``/``-prefix guard.
SOURCE_OPERAND_FLAGS: frozenset[str] = frozenset(
    {
        "-include",
        "-imacros",
        "-include-pch",
        "-Xclang",
        "-x",
        "-o",
        "-MF",
        "-MT",
        "-MQ",
        "-MJ",
        "-I",
        "-isystem",
        "-iquote",
        "-idirafter",
        "-D",
        "-U",
        "/FI",
        "/FU",
    }
)


def _is_bare_input(arg: str, msvc: bool) -> bool:
    """Like :func:`_is_source_token` but without the known-extension requirement.

    Used only in forced-language mode to accept an extensionless/generated input
    (``-x c++ … generated``). ``-``-prefixed tokens stay options, and MSVC option
    tokens are excluded; value-flag operands (``-o``/``-x``/…) never reach here
    because the caller skips them via :data:`SOURCE_OPERAND_FLAGS`.
    """
    if not arg or arg.startswith("-"):
        return False
    if arg.startswith("/") and msvc and _is_msvc_option_token(arg):
        return False
    return True


def sources_from_argv(
    argv: list[str], *, accept_forced_language: bool = False
) -> list[str]:
    """Return **every** argv token that names a compiled translation unit, in order.

    A single compiler invocation may build several TUs (``gcc -c a.c b.c``), so a
    caller that wants all of them (e.g. the ``abicheck-cc`` wrapper) must not stop
    at the first. Same dialect-aware operand recognition as
    :func:`source_from_argv`, which is now the first element of this list.

    With ``accept_forced_language`` (opt-in), a bare input that follows a forced
    ``-x <lang>`` (other than ``-x none``) is accepted even when its filename has
    no known source extension — ``clang++ -x c++ -c generated`` compiles a real
    TU that extension-only discovery would otherwise miss. Off by default so
    compile-DB parsing keeps its strict extension-based recognition.
    """
    msvc = _is_msvc_command(argv)
    out: list[str] = []
    forced_active = False  # a non-"none" -x language applies to following inputs
    i = 0
    while i < len(argv):
        arg = argv[i]
        # Track -x state before the value-flag skip so a forced language is known
        # for the inputs that follow it.
        if arg == "-x" and i + 1 < len(argv):
            forced_active = argv[i + 1].lower() != "none"
            i += 2
            continue
        if arg.startswith("-x") and len(arg) > 2:
            forced_active = arg[2:].lower() != "none"
            i += 1
            continue
        if arg in SOURCE_OPERAND_FLAGS:
            i += 2  # skip the flag and the operand it consumes
            continue
        # MSVC/clang-cl name the TU explicitly with /Tp<file> (C++) / /Tc<file>
        # (C), or the space-separated `/Tp <file>` form.
        if arg in ("/Tp", "/Tc"):
            if i + 1 < len(argv) and detect_language(argv[i + 1]):
                out.append(argv[i + 1])
            i += 2
            continue
        if arg[:3] in ("/Tp", "/Tc") and detect_language(arg[3:]):
            out.append(arg[3:])
            i += 1
            continue
        if _is_source_token(arg, msvc):
            out.append(arg)
        elif accept_forced_language and forced_active and _is_bare_input(arg, msvc):
            out.append(arg)
        i += 1
    return out


def source_from_argv(argv: list[str]) -> str:
    """Return the first argv token that names the compiled translation unit.

    Operands of value-taking flags (e.g. ``-include foo.hpp`` / ``/FI foo.hpp``)
    are skipped so a forced/precompiled header is never mistaken for the source
    TU. The compiler at ``argv[0]`` carries no source extension, so scanning
    from the start is safe (and handles ``cd dir && cc …`` recipes).

    Source recognition is dialect-aware: an MSVC/clang-cl command (``/c`` present
    or a ``cl``/``clang-cl`` driver) treats known ``/``-prefixed compiler flags
    as options — including combined value-taking forms like ``/FIsrc/config.hpp``
    — while still allowing POSIX absolute paths such as ``/work/src/foo.cc``.
    A GNU command treats ``/abs/path.cc`` as a Unix absolute source path.

    Convenience wrapper over :func:`sources_from_argv` for the common single-TU
    callers; returns ``""`` when no source operand is present.
    """
    sources = sources_from_argv(argv)
    return sources[0] if sources else ""


#: Driver basenames that mark a command as MSVC-dialect (``/`` introduces an
#: option, not a path). ``clang-cl`` mimics ``cl`` exactly.
_MSVC_DRIVERS: frozenset[str] = frozenset({"cl", "cl.exe", "clang-cl", "clang-cl.exe"})

_MSVC_COMBINED_OPTION_PREFIXES: tuple[str, ...] = (
    "/fi",
    "/fu",
    "/fo",
    "/fe",
    "/fd",
    "/fp",
    "/yu",
    "/yc",
    "/std:",
    "/external:",
)


def _msvc_driver_scan(argv: list[str]) -> tuple[bool, str | None]:
    """Shared scan behind :func:`_is_msvc_command` and :func:`msvc_driver_token`.

    Returns ``(is_msvc, driver_token)``: *is_msvc* is the same True/False
    :func:`_is_msvc_command` has always returned; *driver_token* is the
    literal argv token whose basename matched :data:`_MSVC_DRIVERS`
    (``cl``/``cl.exe``/``clang-cl``/``clang-cl.exe``, possibly a full path),
    or ``None`` when MSVC dialect was detected some other way (a bare ``/c``
    marker with no such token anywhere in argv, or an explicit
    ``--driver-mode=cl`` naming no ``cl``/``clang-cl``-basename token) --
    both of which leave the caller with no more specific token to prefer
    over ``argv[0]``.

    Split out (P2 review, ``discussion_r3788073756``, fresh evidence) so a
    caller needing the actual driver *token* -- not merely whether the
    command is MSVC-dialect -- doesn't have to assume ``argv[0]`` is it: a
    compiler-cache/launcher wrapper (``sccache``, ``ccache``, ``distcc``, ...)
    commonly precedes the real driver, e.g. ``sccache clang-cl /std:c++20
    ...``, where ``argv[0]`` is the launcher, not the compiler.
    """
    is_msvc = "/c" in argv
    driver_token: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("&&", ";"):
            break
        if arg in SOURCE_OPERAND_FLAGS:
            i += 2
            continue
        if arg == "--driver-mode" and i + 1 < len(argv):
            if argv[i + 1].lower() == "cl":
                is_msvc = True
            i += 2
            continue
        if arg.lower() == "--driver-mode=cl":
            is_msvc = True
            i += 1
            continue
        base = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in _MSVC_DRIVERS:
            is_msvc = True
            if driver_token is None:
                driver_token = arg
        i += 1
    return is_msvc, driver_token


def _is_msvc_command(argv: list[str]) -> bool:
    """True if *argv* uses MSVC/clang-cl option syntax (``/opt`` not paths).

    Detected either by the ``/c`` compile marker (GNU uses ``-c``) or by a
    ``cl``/``clang-cl`` driver basename anywhere in the leading tokens (the
    driver may be a full path, e.g. ``C:\\VS\\bin\\cl.exe``), or by clang's
    explicit ``--driver-mode=cl`` spelling.
    """
    return _msvc_driver_scan(argv)[0]


def msvc_driver_token(argv: list[str]) -> str | None:
    """The literal argv token identified as the MSVC/clang-cl driver, if any.

    ``None`` when *argv* is not MSVC-dialect at all, or when it is (a bare
    ``/c``/``--driver-mode=cl`` with no ``cl``/``clang-cl``-basename token
    present) but no single token can be pointed to as "the driver" more
    specifically than ``argv[0]`` -- see :func:`_msvc_driver_scan`'s own
    docstring. A caller wanting "the compiler to invoke, if this is an
    MSVC-dialect command, falling back to ``argv[0]`` when nothing more
    specific was found" should check :func:`_is_msvc_command` separately,
    the same pattern :func:`~abicheck.buildsource.header_compile_context.
    _derived_gcc_path` uses.
    """
    return _msvc_driver_scan(argv)[1]


def _is_source_token(arg: str, msvc: bool) -> bool:
    """True if *arg* is a translation-unit source path, not a compiler option.

    ``-``-prefixed tokens are always options. In an MSVC/clang-cl command known
    ``/``-prefixed compiler flags (``/c``, ``/FIsrc/config.hpp``,
    ``/Fofoo.obj``) are options, but POSIX-hosted clang-cl invocations may still
    pass Unix absolute source paths (e.g. ``/work/src/foo.cc``), which are kept.
    """
    if not arg or arg.startswith("-"):
        return False
    if arg.startswith("/") and msvc and _is_msvc_option_token(arg):
        return False  # MSVC/clang-cl option (handled before us for /Tp,/Tc)
    return bool(detect_language(arg))


def _is_msvc_option_token(arg: str) -> bool:
    lower = arg.lower()
    if lower in {"/c", "/tp", "/tc", "/nologo", "/showincludes", "/wx", "/gr", "/gs"}:
        return True
    if arg.startswith(("/D", "/I")):
        return True
    if any(lower.startswith(prefix) for prefix in _MSVC_COMBINED_OPTION_PREFIXES):
        return True
    if lower.startswith("/w"):
        suffix = lower[2:]
        return (
            suffix.isdigit()
            or suffix in {"all", "x"}
            or (
                len(suffix) > 1
                and suffix[0] in {"d", "e", "o"}
                and suffix[1:].isdigit()
            )
        )
    if lower.startswith("/o"):
        suffix = lower[2:]
        return bool(suffix) and all(ch in "012bdfgitxys-" for ch in suffix)
    return False


def compile_unit_id(source: str, argv: list[str], output: str = "") -> str:
    """Derive a stable compile-unit id from source + normalized argv + output.

    The argv hash lets the same source compiled under two configurations
    produce two distinct units (ADR-029 D3), while staying stable across runs.
    """
    h = hashlib.sha256()
    h.update(source.encode("utf-8"))
    h.update(b"\0")
    h.update("\0".join(argv).encode("utf-8"))
    h.update(b"\0")
    h.update(output.encode("utf-8"))
    return f"cu://{source}#cfg:{h.hexdigest()[:12]}"


def extract_abi_relevant_flags(argv: list[str]) -> list[str]:
    """Return the subset of *argv* that is ABI/API-affecting (ADR-029 D9).

    Handles both the combined ``-DKEY=VALUE`` define form and the split
    ``['-D', 'KEY=VALUE']`` form; split ABI macros are normalized to the
    combined token so downstream option derivation parses them uniformly.
    A genuinely split-operand flag in :data:`_SPLIT_OPERAND_ABI_FLAGS`
    (``-target-abi``/``-target-cpu``/``-target-feature``/
    ``-target-linker-version``) is normalized the same way, into one
    internal ``<flag>=<value>`` token -- see that set's own docstring for
    why this differs from the ``-D`` combined-form concatenation just below.

    Each of those four is also individually recognized in its real-world
    ``-Xclang <flag> -Xclang <value>``-wrapped spelling (P2 review,
    ``discussion_r3788073752`` -- see :data:`_SPLIT_OPERAND_ABI_FLAGS`'s own
    docstring for why a normal Clang driver invocation always wraps a
    cc1-only flag like this rather than passing it bare), normalized into a
    distinct ``-Xclang <flag>=<value>`` internal token so the two real argv
    shapes stay distinguishable for replay. Checked before the bare
    two-token branch, which would otherwise consume the second ``-Xclang``
    token as the operand and silently drop the real value one token later.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith(_NON_ABI_LTO_TUNING_PREFIXES):
            # LTO backend-tuning flag (not enabling): not ABI-relevant, though it
            # shares the -flto prefix. Skip so it never reaches option derivation.
            pass
        elif (
            arg == "-Xclang"
            and i + 3 < len(argv)
            and argv[i + 1] in _SPLIT_OPERAND_ABI_FLAGS
            and argv[i + 2] == "-Xclang"
        ):
            # -Xclang-wrapped split two-token cc1 flag pair (`-Xclang
            # -target-abi -Xclang aapcs`): this branch matches on the
            # leading `-Xclang` token, one position before the flag itself,
            # so the bare `arg in _SPLIT_OPERAND_ABI_FLAGS` branch below
            # never gets a chance to misfire on `-target-abi` here -- without
            # this branch, the bare one would fire on the *next* iteration
            # and treat the second `-Xclang` as the operand, dropping the
            # real value one token later (the bug this branch exists to
            # fix).
            out.append(f"-Xclang {argv[i + 1]}={argv[i + 3]}")
            i += 4
            continue
        elif arg in _SPLIT_OPERAND_ABI_FLAGS and i + 1 < len(argv):
            # Split two-token form (`-target-abi <value>`): must be checked
            # *before* the generic ABI_RELEVANT_FLAG_PREFIXES prefix match
            # below, since every one of these flags shares the "-target"
            # prefix that match already recognizes -- reaching that branch
            # first would append the bare flag alone and silently drop the
            # operand (the bug this set exists to fix).
            out.append(f"{arg}={argv[i + 1]}")
            i += 2
            continue
        elif arg.startswith(ABI_RELEVANT_FLAG_PREFIXES):
            out.append(arg)
        elif arg in ("-D", "/D") and i + 1 < len(argv):
            # Split form: -D KEY[=VALUE]. Normalize to the combined token.
            nxt = argv[i + 1]
            if nxt.split("=", 1)[0] in _ABI_RELEVANT_DEFINES:
                out.append(arg + nxt)
            i += 2
            continue
        elif arg.startswith(("-D", "/D")):
            if arg[2:].split("=", 1)[0] in _ABI_RELEVANT_DEFINES:
                out.append(arg)
        i += 1
    return out


def _add_build_option(
    out: list[BuildOption],
    seen: set[tuple[str, str]],
    key: str,
    value: str,
    *,
    raw: str,
) -> None:
    """Append a BuildOption to *out* unless its (key, value) was already recorded."""
    sig = (key, value)
    if sig in seen:
        return
    seen.add(sig)
    out.append(BuildOption(key=key, value=value, abi_relevant=True, raw=raw))


def _structured_unit_options(cu: CompileUnit) -> list[tuple[str, str, str]]:
    """(key, value, raw) options from a unit's structured standard/target/sysroot fields."""
    opts: list[tuple[str, str, str]] = []
    if cu.standard:
        # Per-language key so a mixed C/C++ project keeps std:C and std:CXX
        # distinct (otherwise one masks the other in the diff).
        std_key = f"std:{cu.language}" if cu.language else "std"
        opts.append((std_key, cu.standard, f"-std={cu.standard}"))
    if cu.target_triple:
        opts.append(("target", cu.target_triple, cu.target_triple))
    if cu.sysroot:
        opts.append(("sysroot", cu.sysroot, cu.sysroot))
    return opts


def _mode_flag_option(flag: str, cu: CompileUnit) -> tuple[str, str] | None:
    """Classify *flag* as a last-one-wins runtime-mode option: (key, value), or None."""
    if flag in _RUNTIME_MODE_FLAGS:
        base_key, value = _RUNTIME_MODE_FLAGS[flag]
        # exceptions/rtti/threadsafe-statics have language-dependent
        # compiler defaults (on for C++, off / N-A for C), so qualify the
        # key per language (mirrors the ``std:<lang>`` option) — the
        # build-evidence diff then infers the right default for an
        # omitted flag. TLS keys are language-agnostic and stay bare.
        key = (
            f"{base_key}:{cu.language}"
            if base_key in _LANG_QUALIFIED_MODE_KEYS and cu.language
            else base_key
        )
        return key, value
    if flag.startswith("-ftls-model"):
        # -ftls-model=<model>: canonical key so a model switch diffs as a
        # single option regardless of which model string is on each side.
        return "tls_model", flag.split("=", 1)[1] if "=" in flag else ""
    if flag.startswith("/Zp"):
        # MSVC struct-packing width (/Zp[N]). Its default is
        # target-dependent (/Zp8 on x86/ARM, /Zp16 on x64), so it is keyed
        # separately from the GNU flag and diffed only when both sides are
        # explicit (build_diff), avoiding a false flip when a VS project
        # merely records its platform default against an omitted side.
        return "struct_packing_msvc", flag[len("/Zp") :] or "1"
    if flag.startswith("-fpack-struct"):
        # GNU/Clang struct-packing width (-fpack-struct[=N]). The default
        # is known (disabled → natural packing), so a one-sided add/remove
        # is a real, reportable layout change. Bare flag packs to 1.
        return "struct_packing", flag.split("=", 1)[1] if "=" in flag else "1"
    if flag.startswith("-flto="):
        # -flto=<thin|auto|N>: an *enabling* spelling (bare -flto is caught
        # by the _RUNTIME_MODE_FLAGS map above; -fno-lto too). Match only
        # "-flto=" so LTO *tuning* flags that do not enable LTO
        # (-flto-partition=, -flto-jobs=) fall through to the generic
        # option path instead of falsely reporting lto off->on (Codex).
        return "lto", "on"
    if flag.startswith("-fsanitize="):
        # -fsanitize=<list>: sanitizers change object layout (asan
        # redzones), instrumentation and the runtime contract. Canonical
        # key so a flip diffs as one option; default is "none" (known).
        return "sanitizer", flag.split("=", 1)[1]
    if flag.startswith("-fno-sanitize="):
        # -fno-sanitize=<list> disables sanitizers. On its own it just
        # spells the default (no sanitizer), so normalize to "none" rather
        # than let it fall through to the generic ABI-flag finding — with
        # last-one-wins it also correctly cancels an earlier -fsanitize=
        # for the same set (Codex review).
        return "sanitizer", "none"
    if flag.startswith("-mfloat-abi="):
        # -mfloat-abi=<soft|softfp|hard>: the float calling convention on
        # ARM. Its default is target-dependent, so build_diff requires
        # both sides explicit before reporting a flip.
        return "float_abi", flag.split("=", 1)[1]
    return None


def _add_generic_flag_option(
    flag: str,
    cu: CompileUnit,
    out: list[BuildOption],
    seen: set[tuple[str, str]],
) -> None:
    """Record a non-mode ABI-relevant flag (define/std/generic) as a BuildOption."""
    if flag.startswith(("-D", "/D")):
        key, _, value = flag[2:].partition("=")
        _add_build_option(out, seen, f"define:{key}", value, raw=flag)
    elif flag.startswith(_STD_FLAG_PREFIXES):
        # Language standard. GCC ``-std=`` is already captured via
        # cu.standard above; MSVC ``/std:`` is not parsed into
        # cu.standard, so normalize it into the same std:<lang> option
        # here (only when the structured field didn't already set it).
        if not cu.standard:
            sep = "=" if "=" in flag else ":"
            std_val = flag.split(sep, 1)[1] if sep in flag else flag
            _add_build_option(
                out,
                seen,
                f"std:{cu.language}" if cu.language else "std",
                std_val,
                raw=flag,
            )
    elif flag.startswith(_TOOLCHAIN_PATH_FLAG_PREFIXES):
        # sysroot/target are already emitted from the normalized
        # structured fields above. Re-adding the raw flag would
        # double-count and make split (``--sysroot /sdk``) vs combined
        # (``--sysroot=/sdk``) spelling look like a change.
        return
    else:
        _add_build_option(out, seen, flag.split("=", 1)[0], flag, raw=flag)


def derive_build_options(compile_units: list[CompileUnit]) -> list[BuildOption]:
    """Project each compile unit's ABI-relevant flags into global BuildOptions.

    Shared by every adapter so a pack always carries the ``build_options`` the
    build-evidence diff (ADR-029 D9) reads — without this, a Ninja-only pack
    would record per-unit ``abi_relevant_flags`` but no diffable options. The
    unit fields are already redacted, so no further redaction happens here.
    De-duplicated across units so a flag shared by 100 TUs records once.
    """
    out: list[BuildOption] = []
    seen: set[tuple[str, str]] = set()

    for cu in compile_units:
        for key, value, raw in _structured_unit_options(cu):
            _add_build_option(out, seen, key, value, raw=raw)
        # Runtime-mode flags are resolved per TU with last-one-wins semantics
        # (GCC: "if conflicting, the last such option is effective"), so a TU
        # that carries e.g. ``-fno-exceptions -fexceptions`` records only the
        # effective ``on`` rather than both values. Collected here and emitted
        # after the flag loop so a later flag can override an earlier one.
        mode_values: dict[str, tuple[str, str]] = {}  # key -> (value, raw)
        for flag in cu.abi_relevant_flags:
            mode = _mode_flag_option(flag, cu)
            if mode is not None:
                mode_values[mode[0]] = (mode[1], flag)
            else:
                _add_generic_flag_option(flag, cu, out, seen)
        for key, (value, raw) in mode_values.items():
            _add_build_option(out, seen, key, value, raw=raw)

    return out


#: Language-standard flag prefixes (GCC ``-std=`` / MSVC ``/std:``).
_STD_FLAG_PREFIXES: tuple[str, ...] = ("-std=", "/std:")

#: Value-taking toolchain flags already captured as structured target/sysroot
#: options, so the raw flag must not also become an option (split vs combined
#: spelling would otherwise read as a change).
_TOOLCHAIN_PATH_FLAG_PREFIXES: tuple[str, ...] = (
    "--sysroot",
    "-isysroot",
    "--target",
    "-target",
)
