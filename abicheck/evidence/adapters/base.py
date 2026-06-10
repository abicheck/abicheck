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
)

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
    """Return the subset of *argv* that is ABI/API-affecting (ADR-029 D9)."""
    out: list[str] = []
    for arg in argv:
        if arg.startswith(ABI_RELEVANT_FLAG_PREFIXES):
            out.append(arg)
        elif arg.startswith(("-D", "/D")):
            body = arg[2:]
            key = body.split("=", 1)[0]
            if key in _ABI_RELEVANT_DEFINES:
                out.append(arg)
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
        for flag in cu.abi_relevant_flags:
            if flag.startswith(("-D", "/D")):
                key, _, value = flag[2:].partition("=")
                add(f"define:{key}", value, raw=flag)
            elif not flag.startswith("-std="):
                add(flag.split("=", 1)[0], flag, raw=flag)
    return out
