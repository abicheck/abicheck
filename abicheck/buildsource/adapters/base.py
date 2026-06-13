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
    ".cc": "CXX", ".cpp": "CXX", ".cxx": "CXX", ".c++": "CXX", ".cp": "CXX",
    ".ii": "CXX", ".hpp": "CXX", ".hh": "CXX", ".hxx": "CXX",
    ".m": "OBJC", ".mm": "OBJCXX",
    ".cu": "CUDA",
}

#: ABI/API-affecting compiler-flag prefixes (ADR-029 D9). Drift in any of these
#: is treated as a risk signal by the build-evidence diff, not mere noise.
ABI_RELEVANT_FLAG_PREFIXES: tuple[str, ...] = (
    "-std=", "/std:",
    "--target=", "-target", "-mabi=", "/arch:", "-m32", "-m64",
    "--sysroot", "-isysroot",
    "-fvisibility", "-fvisibility-inlines-hidden",
    "-fpack-struct", "/Zp", "-fshort-enums", "-fshort-wchar",
    "-fabi-version", "-fno-rtti", "-frtti", "-fno-exceptions", "-fexceptions",
    "-flto", "-fno-lto", "-fwhole-program-vtables",
    "-ftls-model", "-fextern-tls-init", "-fno-extern-tls-init",
    "-fno-threadsafe-statics", "-fthreadsafe-statics",
    "-freg-struct-return", "-fpcc-struct-return",
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

#: Macro defines whose value is ABI-relevant even though they're plain -D flags.
_ABI_RELEVANT_DEFINES: tuple[str, ...] = (
    "_GLIBCXX_USE_CXX11_ABI",
    "_ITERATOR_DEBUG_LEVEL",
    "_LIBCPP_ABI_VERSION",
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

    def collect(self) -> BuildEvidence:
        ...


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
    "c": "C", "c-header": "C", "cpp-output": "C",
    "objective-c": "OBJC", "objective-c-header": "OBJC", "objc-cpp-output": "OBJC",
    "c++": "CXX", "c++-header": "CXX", "c++-cpp-output": "CXX",
    "objective-c++": "OBJCXX", "objective-c++-header": "OBJCXX", "objc++-cpp-output": "OBJCXX",
}


def effective_language(argv: list[str], source: str) -> str:
    """Normalized language honoring a forced ``-x <lang>`` / MSVC ``/Tp``/``/Tc``.

    The command line overrides the source extension: ``g++ -x c++ -c foo.c``
    compiles C++ even though ``foo.c`` reads as C, and MSVC ``/TP`` / ``/Tp<f>``
    (force C++) / ``/TC`` / ``/Tc<f>`` (force C) do the same. The last forcing
    token wins for a single-source TU. Falls back to :func:`detect_language` on
    the source path when nothing on the command line forces the language.
    """
    forced = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "-x" and i + 1 < len(argv):
            tok = argv[i + 1].lower()
            forced = "" if tok == "none" else _X_LANG_TO_NORMALIZED.get(tok, forced)
            i += 2
            continue
        if arg.startswith("-x") and len(arg) > 2:
            tok = arg[2:].lower()
            forced = "" if tok == "none" else _X_LANG_TO_NORMALIZED.get(tok, forced)
        elif arg == "/TP" or arg[:3] == "/Tp":
            forced = "CXX"
        elif arg == "/TC" or arg[:3] == "/Tc":
            forced = "C"
        i += 1
    return forced or detect_language(source)


#: Value-taking compiler flags whose *operand* is not the translation unit even
#: when it looks source-like — e.g. ``-include config.hpp`` (GNU forced header),
#: ``-x c++``, or the MSVC/clang-cl ``/FI`` / ``/FU`` forced-include/using flags.
#: Skipping the operand keeps :func:`source_from_argv` from mistaking a forced or
#: precompiled header for the real source. Combined MSVC forms like
#: ``/FIconfig.hpp`` are handled by the ``/``-prefix guard.
SOURCE_OPERAND_FLAGS: frozenset[str] = frozenset({
    "-include", "-imacros", "-include-pch", "-Xclang", "-x",
    "-o", "-MF", "-MT", "-MQ", "-MJ",
    "-I", "-isystem", "-iquote", "-idirafter", "-D", "-U",
    "/FI", "/FU",
})


def source_from_argv(argv: list[str]) -> str:
    """Return the first argv token that names the compiled translation unit.

    Operands of value-taking flags (e.g. ``-include foo.hpp`` / ``/FI foo.hpp``)
    are skipped so a forced/precompiled header is never mistaken for the source
    TU. The compiler at ``argv[0]`` carries no source extension, so scanning
    from the start is safe (and handles ``cd dir && cc …`` recipes).

    Source recognition is dialect-aware: an MSVC/clang-cl command (``/c`` present
    or a ``cl``/``clang-cl`` driver) treats every ``/``-prefixed token as an
    option — including combined value-taking forms like ``/FIsrc/config.hpp`` —
    so only bare, ``C:\\``-rooted, or ``/Tp``/``/Tc``-named tokens are sources.
    A GNU command treats ``/abs/path.cc`` as a Unix absolute source path.
    """
    msvc = _is_msvc_command(argv)
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in SOURCE_OPERAND_FLAGS:
            i += 2  # skip the flag and the operand it consumes
            continue
        # MSVC/clang-cl name the TU explicitly with /Tp<file> (C++) / /Tc<file>
        # (C), or the space-separated `/Tp <file>` form. Return the bare path.
        if arg in ("/Tp", "/Tc"):
            if i + 1 < len(argv) and detect_language(argv[i + 1]):
                return argv[i + 1]
            i += 2
            continue
        if arg[:3] in ("/Tp", "/Tc") and detect_language(arg[3:]):
            return arg[3:]
        if _is_source_token(arg, msvc):
            return arg
        i += 1
    return ""


#: Driver basenames that mark a command as MSVC-dialect (``/`` introduces an
#: option, not a path). ``clang-cl`` mimics ``cl`` exactly.
_MSVC_DRIVERS: frozenset[str] = frozenset({"cl", "cl.exe", "clang-cl", "clang-cl.exe"})


def _is_msvc_command(argv: list[str]) -> bool:
    """True if *argv* uses MSVC/clang-cl option syntax (``/opt`` not paths).

    Detected either by the ``/c`` compile marker (GNU uses ``-c``) or by a
    ``cl``/``clang-cl`` driver basename anywhere in the leading tokens (the
    driver may be a full path, e.g. ``C:\\VS\\bin\\cl.exe``).
    """
    if "/c" in argv:
        return True
    for arg in argv:
        if arg in ("&&", ";"):
            break
        base = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base in _MSVC_DRIVERS:
            return True
    return False


def _is_source_token(arg: str, msvc: bool) -> bool:
    """True if *arg* is a translation-unit source path, not a compiler option.

    ``-``-prefixed tokens are always options. In an MSVC/clang-cl command every
    ``/``-prefixed token is an option (``/c``, ``/FIsrc/config.hpp``,
    ``/Fofoo.obj``) regardless of an embedded ``/``. In a GNU command a
    ``/``-prefixed token with a source extension is a Unix absolute source path
    (e.g. ``/work/src/foo.cc``) and is kept.
    """
    if not arg or arg.startswith("-"):
        return False
    if arg.startswith("/") and msvc:
        return False  # MSVC/clang-cl option (handled before us for /Tp,/Tc)
    return bool(detect_language(arg))


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
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith(ABI_RELEVANT_FLAG_PREFIXES):
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

    def add(key: str, value: str, *, raw: str) -> None:
        sig = (key, value)
        if sig in seen:
            return
        seen.add(sig)
        out.append(BuildOption(key=key, value=value, abi_relevant=True, raw=raw))

    for cu in compile_units:
        if cu.standard:
            # Per-language key so a mixed C/C++ project keeps std:C and std:CXX
            # distinct (otherwise one masks the other in the diff).
            std_key = f"std:{cu.language}" if cu.language else "std"
            add(std_key, cu.standard, raw=f"-std={cu.standard}")
        if cu.target_triple:
            add("target", cu.target_triple, raw=cu.target_triple)
        if cu.sysroot:
            add("sysroot", cu.sysroot, raw=cu.sysroot)
        # Runtime-mode flags are resolved per TU with last-one-wins semantics
        # (GCC: "if conflicting, the last such option is effective"), so a TU
        # that carries e.g. ``-fno-exceptions -fexceptions`` records only the
        # effective ``on`` rather than both values. Collected here and emitted
        # after the flag loop so a later flag can override an earlier one.
        mode_values: dict[str, tuple[str, str]] = {}  # key -> (value, raw)
        for flag in cu.abi_relevant_flags:
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
                mode_values[key] = (value, flag)
            elif flag.startswith("-ftls-model"):
                # -ftls-model=<model>: canonical key so a model switch diffs as a
                # single option regardless of which model string is on each side.
                model = flag.split("=", 1)[1] if "=" in flag else ""
                mode_values["tls_model"] = (model, flag)
            elif flag.startswith(("-D", "/D")):
                key, _, value = flag[2:].partition("=")
                add(f"define:{key}", value, raw=flag)
            elif flag.startswith(_STD_FLAG_PREFIXES):
                # Language standard. GCC ``-std=`` is already captured via
                # cu.standard above; MSVC ``/std:`` is not parsed into
                # cu.standard, so normalize it into the same std:<lang> option
                # here (only when the structured field didn't already set it).
                if not cu.standard:
                    sep = "=" if "=" in flag else ":"
                    std_val = flag.split(sep, 1)[1] if sep in flag else flag
                    add(f"std:{cu.language}" if cu.language else "std", std_val, raw=flag)
            elif flag.startswith(_TOOLCHAIN_PATH_FLAG_PREFIXES):
                # sysroot/target are already emitted from the normalized
                # structured fields above. Re-adding the raw flag would
                # double-count and make split (``--sysroot /sdk``) vs combined
                # (``--sysroot=/sdk``) spelling look like a change.
                continue
            else:
                add(flag.split("=", 1)[0], flag, raw=flag)
        for key, (value, raw) in mode_values.items():
            add(key, value, raw=raw)

    return out


#: Language-standard flag prefixes (GCC ``-std=`` / MSVC ``/std:``).
_STD_FLAG_PREFIXES: tuple[str, ...] = ("-std=", "/std:")

#: Value-taking toolchain flags already captured as structured target/sysroot
#: options, so the raw flag must not also become an option (split vs combined
#: spelling would otherwise read as a change).
_TOOLCHAIN_PATH_FLAG_PREFIXES: tuple[str, ...] = (
    "--sysroot", "-isysroot", "--target", "-target",
)
