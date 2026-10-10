# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""The target platform a header parse was actually run for (ADR-050 D1).

``comparability.compute_extraction_contract`` fingerprints ``target_triple``/
``pointer_width``/``endianness``, but nothing recorded them, so ``-m32`` or
``--target=`` on one side only never reached the contract. The facts come
from the parsing frontend itself:

* **pointer width / endianness** -- the ``__SIZEOF_POINTER__`` and
  ``__BYTE_ORDER__`` the compiler predefines under the dump's own flags
  (for castxml: the emulated compiler with the arguments castxml forwards to
  it, so the macros the parse saw);
* **triple** -- an explicit ``--target=``/``-target`` wins; otherwise the
  frontend's resolved triple (clang's ``-print-target-triple`` honours
  ``-m32``), or the compiler's ``-dumpmachine`` -- which ignores ``-m32`` --
  moved to the architecture sibling of the probed pointer width.

The values are stored on ``AbiSnapshot.ast_toolchain`` (free-form provenance)
under the keys below; a snapshot without them is *unrecorded*, never "empty
platform" -- the comparability gate treats an unrecorded side as unknown.
This module is pure: the compiler run lives with the other toolchain probes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

TARGET_TRIPLE_KEY = "target_triple_effective"
POINTER_WIDTH_KEY = "target_pointer_width"
ENDIANNESS_KEY = "target_endianness"

_BYTE_ORDER = {
    "__ORDER_LITTLE_ENDIAN__": "little",
    "__ORDER_BIG_ENDIAN__": "big",
}

#: Architecture spelled for the other word size, by ``-dumpmachine`` arch.
#: Only families whose 32/64-bit spellings are unambiguous; an arch outside
#: this table keeps its spelling (pointer_width still records the switch).
_ARCH_BY_WIDTH: dict[str, dict[int, str]] = {
    arch: pair
    for pair in (
        {32: "i686", 64: "x86_64"},
        {32: "powerpc", 64: "powerpc64"},
        {32: "powerpcle", 64: "powerpc64le"},
        {32: "s390", 64: "s390x"},
        {32: "sparc", 64: "sparc64"},
        {32: "mips", 64: "mips64"},
        {32: "mipsel", 64: "mips64el"},
        {32: "riscv32", 64: "riscv64"},
    )
    for arch in pair.values()
} | {"i386": {32: "i386", 64: "x86_64"}, "i586": {32: "i586", 64: "x86_64"}}


def platform_from_macros(text: str) -> tuple[int | None, str | None]:
    """``(pointer_width_bits, endianness)`` from ``-E -dM`` output.

    Either is ``None`` when the compiler does not define the macro (an MSVC
    or exotic frontend): absent evidence, never a guess.
    """
    defs: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0] == "#define":
            defs[parts[1]] = parts[2].strip()
    width: int | None = None
    size = defs.get("__SIZEOF_POINTER__", "")
    if size.isdigit() and int(size) > 0:
        width = int(size) * 8
    return width, _BYTE_ORDER.get(defs.get("__BYTE_ORDER__", ""))


def explicit_target(args: Sequence[str]) -> str | None:
    """The last ``--target=X``/``-target X``/``--target X`` in *args*."""
    found: str | None = None
    tokens = list(args)
    for i, tok in enumerate(tokens):
        if tok.startswith("--target="):
            found = tok.split("=", 1)[1] or found
        elif tok in ("-target", "--target") and i + 1 < len(tokens):
            found = tokens[i + 1]
    return found


def _last_x86_abi_flag(args: Sequence[str]) -> str | None:
    """The last of ``-m32``/``-m64``/``-mx32`` (the one GCC/Clang honour)."""
    found: str | None = None
    for tok in args:
        if tok in ("-m32", "-m64", "-mx32"):
            found = tok
    return found


def effective_triple(
    resolved: str | None, args: Sequence[str], pointer_width: int | None
) -> str | None:
    """The triple the parse targeted.

    *resolved* is the frontend's own answer (clang's flag-aware triple, or a
    ``-dumpmachine`` that ignores ``-m32``). An explicit target flag wins;
    otherwise *resolved*'s architecture is re-spelled for *pointer_width*
    when the table knows the family's sibling.
    """
    explicit = explicit_target(args)
    if explicit:
        return explicit
    if not resolved:
        return None
    arch, sep, rest = resolved.partition("-")
    x86 = _ARCH_BY_WIDTH.get("x86_64", {}).values()
    if arch in x86 and _last_x86_abi_flag(args) == "-mx32":
        # x32 is 32-bit pointers on the x86_64 ISA: keep the arch, mark the
        # ABI in the environment component (clang: ``...-gnux32``).
        env_rest = (
            rest if rest.endswith("x32") or not rest.endswith("gnu") else f"{rest}x32"
        )
        return f"x86_64{sep}{env_rest}"
    if arch == "x86_64" and rest.endswith("gnux32") and (pointer_width or 0) == 32:
        return resolved
    sibling = _ARCH_BY_WIDTH.get(arch, {}).get(pointer_width or 0)
    return f"{sibling}{sep}{rest}" if sibling else resolved


def target_platform_metadata(
    triple: str | None, pointer_width: int | None, endianness: str | None
) -> dict[str, str]:
    """The ``ast_toolchain`` entries for what was established (only those)."""
    out: dict[str, str] = {}
    if triple:
        out[TARGET_TRIPLE_KEY] = triple
    if pointer_width:
        out[POINTER_WIDTH_KEY] = str(pointer_width)
    if endianness:
        out[ENDIANNESS_KEY] = endianness
    return out


def contract_platform_fields(
    ast_toolchain: Mapping[str, str] | None,
) -> tuple[str | None, int | None, str | None]:
    """``(target_triple, pointer_width, endianness)`` for
    ``compute_extraction_contract`` -- ``None`` for each unrecorded one."""
    tc = ast_toolchain or {}
    width = tc.get(POINTER_WIDTH_KEY, "")
    return (
        tc.get(TARGET_TRIPLE_KEY) or None,
        int(width) if width.isdigit() else None,
        tc.get(ENDIANNESS_KEY) or None,
    )
