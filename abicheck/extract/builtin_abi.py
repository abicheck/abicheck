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

"""The ABI fact of a clang-spelled builtin type (schema v69).

Both header frontends need the same fact for a builtin: the clang AST (direct
frontend, and the castxml<-clang sidecar) names a builtin in its canonical
spelling (``unsigned int``, ``_Bool``), and gives no size or alignment. Those
come from the compiler itself: :func:`probe_builtin_abi` compiles and runs a
program that prints ``sizeof``/``_Alignof`` for each builtin on the target the
header parse used, and reads ``__CHAR_UNSIGNED__`` for a plain ``char``. The
encoding class is the closed set of builtin names below (the C type system's
own names, never a user spelling).

A spelling that is not a builtin, a vector, or an ``_Atomic`` of one has no
fact, so the caller keeps its name-based verdict.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..deadline import run_bounded
from ..model.abi_fundamental import AbiFundamental, FundamentalEncoding
from ..model.execution_cache import memoized
from ..model.fact import Fact

__all__ = [
    "BUILTIN_ENCODING",
    "BuiltinAbi",
    "builtin_type_fact",
    "plain_char_is_unsigned",
    "probe_builtin_abi",
]

_SIGNED = FundamentalEncoding.SIGNED
_UNSIGNED = FundamentalEncoding.UNSIGNED
_FLOAT = FundamentalEncoding.FLOAT

#: The closed set of C/C++ builtin spellings a compiler names a fundamental by,
#: with the encoding class each is. ``char`` is absent: its signedness is the
#: target's (see :func:`plain_char_is_unsigned`). ``wchar_t`` is absent: it is a
#: typedef in C, and its signedness is the target's.
BUILTIN_ENCODING: dict[str, FundamentalEncoding] = {
    "signed char": _SIGNED,
    "unsigned char": _UNSIGNED,
    "short int": _SIGNED,
    "short": _SIGNED,
    "int": _SIGNED,
    "signed int": _SIGNED,
    "long int": _SIGNED,
    "long": _SIGNED,
    "long long int": _SIGNED,
    "long long": _SIGNED,
    "__int128": _SIGNED,
    "short unsigned int": _UNSIGNED,
    "unsigned short": _UNSIGNED,
    "unsigned int": _UNSIGNED,
    "unsigned": _UNSIGNED,
    "long unsigned int": _UNSIGNED,
    "unsigned long": _UNSIGNED,
    "long long unsigned int": _UNSIGNED,
    "unsigned long long": _UNSIGNED,
    "unsigned __int128": _UNSIGNED,
    "char16_t": _UNSIGNED,
    "char32_t": _UNSIGNED,
    "char8_t": _UNSIGNED,
    "_Bool": FundamentalEncoding.BOOLEAN,
    "bool": FundamentalEncoding.BOOLEAN,
    "float": _FLOAT,
    "double": _FLOAT,
    "long double": _FLOAT,
    "_Float16": _FLOAT,
    "_Float32": _FLOAT,
    "_Float64": _FLOAT,
    "_Float128": _FLOAT,
    "_Float32x": _FLOAT,
    "_Float64x": _FLOAT,
    "__float128": _FLOAT,
}

_VECTOR_SPELLING = re.compile(
    r"^\s*(?P<element>.+?)\s*__attribute__\(\(vector_size\((?P<bytes>\d+)\)\)\)\s*$"
)
#: clang spells a vector attribute-first, with a byte count that may be an
#: expression (``__attribute__((__vector_size__(2 * sizeof(float)))) float``).
_CLANG_VECTOR_ATTRIBUTE = re.compile(
    r"__attribute__\(\(\s*(?:__)?vector_size__?\((?P<expr>.*)\)\s*\)\)", re.DOTALL
)
_SIZEOF_CALL = re.compile(r"sizeof\s*\(\s*(?P<type>[^()]+?)\s*\)")
_ATOMIC_SPELLING = re.compile(
    r"^\s*_Atomic\s*(?:\((?P<inner>.+)\)|\s+(?P<bare>.+?))\s*$"
)

#: The builtins the layout probe reports, in the spelling the clang AST uses.
_PROBED = (
    "char",
    "signed char",
    "unsigned char",
    "short",
    "unsigned short",
    "int",
    "unsigned int",
    "long",
    "unsigned long",
    "long long",
    "unsigned long long",
    "float",
    "double",
    "long double",
    "_Bool",
    "__int128",
    "unsigned __int128",
)

_PROBE_SOURCE = """\
#include <stdio.h>
#define P(T) printf("%s %zu %zu\\n", #T, sizeof(T), _Alignof(T));
int main(void) {
@BODY@
return 0;
}
"""


@dataclass(frozen=True)
class BuiltinAbi:
    """What the compiler decided about builtins on one target.

    ``layout`` maps each probed builtin spelling to ``(size, alignment)`` in
    bytes; ``plain_char_unsigned`` is the target's plain ``char`` signedness.
    """

    plain_char_unsigned: bool
    layout: Mapping[str, tuple[int, int]]


@memoized
def plain_char_is_unsigned(cc_bin: str, option_tokens: tuple[str, ...]) -> bool | None:
    """Whether a plain ``char`` is unsigned under *cc_bin* with *option_tokens*.

    The compiler's predefined macro ``__CHAR_UNSIGNED__``, defined by GCC and
    Clang exactly when plain ``char`` is unsigned. ``None`` when the compiler
    does not run.
    """
    proc = _run([cc_bin, *option_tokens, "-dM", "-E", "-x", "c", os.devnull])
    if proc is None or proc.returncode != 0:
        return None
    return "#define __CHAR_UNSIGNED__" in proc.stdout


@memoized
def probe_builtin_abi(cc_bin: str, option_tokens: tuple[str, ...]) -> BuiltinAbi | None:
    """The builtin layout and plain-char signedness of *cc_bin* under *option_tokens*.

    Compiles and runs a probe with the same compiler and options the header
    parse used, so the answer is the target's. ``None`` when the probe does not
    compile or run: then no builtin carries a fact.
    """
    plain_char = plain_char_is_unsigned(cc_bin, option_tokens)
    if plain_char is None:
        return None
    body = "\n".join(f"P({name})" for name in _PROBED)
    with tempfile.TemporaryDirectory(prefix="abicheck-builtin-abi-") as tmp:
        src = Path(tmp) / "probe.c"
        exe = Path(tmp) / "probe"
        src.write_text(_PROBE_SOURCE.replace("@BODY@", body), encoding="utf-8")
        built = _run([cc_bin, *option_tokens, "-x", "c", str(src), "-o", str(exe)])
        if built is None or built.returncode != 0:
            return None
        ran = _run([str(exe)])
        if ran is None or ran.returncode != 0:
            return None
    layout: dict[str, tuple[int, int]] = {}
    for line in ran.stdout.splitlines():
        parts = line.rsplit(" ", 2)
        if len(parts) != 3:
            return None
        name, size, align = parts
        if not (size.isdigit() and align.isdigit()):
            return None
        layout[name] = (int(size), int(align))
    return BuiltinAbi(plain_char_unsigned=plain_char, layout=layout)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str] | None:
    """*argv* run under the active deadline, or None when it cannot run."""
    try:
        return run_bounded(argv, timeout=60, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return None


def _encoding(name: str, abi: BuiltinAbi) -> FundamentalEncoding | None:
    if name == "char":
        return _UNSIGNED if abi.plain_char_unsigned else _SIGNED
    return BUILTIN_ENCODING.get(name)


def _scalar(name: str, abi: BuiltinAbi) -> AbiFundamental | None:
    encoding = _encoding(name, abi)
    layout = abi.layout.get(name)
    if encoding is None or layout is None:
        return None
    size, align = layout
    if size <= 0 or align <= 0:
        return None
    return AbiFundamental(encoding, size, align)


def _split_clang_vector(text: str) -> tuple[str, str] | None:
    """``(byte expression, element)`` of a clang vector spelling, or None."""
    if not text.startswith("__attribute__"):
        return None
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                attribute, rest = text[: index + 1], text[index + 1 :].strip()
                match = _CLANG_VECTOR_ATTRIBUTE.fullmatch(attribute)
                if match is None or not rest:
                    return None
                return match.group("expr").strip(), rest
    return None


def _vector_bytes(expr: str, abi: BuiltinAbi) -> int | None:
    """The byte count of a vector: an integer, or ``+``/``*`` of integers and
    ``sizeof(builtin)`` (each replaced by the probed size before evaluating)."""

    def probed_size(match: re.Match[str]) -> str:
        layout = abi.layout.get(match.group("type").strip())
        return str(layout[0]) if layout is not None else "?"

    text = _SIZEOF_CALL.sub(probed_size, expr)
    if not re.fullmatch(r"[\d\s()*+]+", text):
        return None
    try:
        return _arithmetic(ast.parse(text, mode="eval").body)
    except (ValueError, SyntaxError):
        return None


def _arithmetic(node: ast.expr) -> int:
    """The value of a constant ``+``/``*`` tree; ValueError on anything else."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        left, right = _arithmetic(node.left), _arithmetic(node.right)
        return left + right if isinstance(node.op, ast.Add) else left * right
    raise ValueError(type(node).__name__)


def _vector(spelling: str, abi: BuiltinAbi) -> AbiFundamental | None:
    clang = _split_clang_vector(spelling.strip())
    if clang is not None:
        total = _vector_bytes(clang[0], abi)
        element = clang[1]
    else:
        match = _VECTOR_SPELLING.match(spelling)
        if match is None:
            return None
        element = match.group("element").strip()
        total = int(match.group("bytes"))
    element_fact = _scalar(element, abi)
    if element_fact is None or total is None or total <= 0 or total % element_fact.size:
        return None
    # A GCC/Clang vector is aligned to its own size (as dwarf_abi_fact records).
    return AbiFundamental(
        element_fact.encoding, total, total, total // element_fact.size
    )


def builtin_type_fact(
    spelling: str, abi: BuiltinAbi | None
) -> Fact[AbiFundamental] | None:
    """The ABI fact of a clang-spelled type, or ``None`` (no fact).

    *spelling* is the desugared type (typedefs resolved). A builtin, a vector of
    one, or an ``_Atomic`` of a builtin with a fact; anything else has none.
    """
    if abi is None:
        return None
    text = spelling.strip()
    atomic = _ATOMIC_SPELLING.match(text)
    fact: AbiFundamental | None
    if atomic is not None:
        inner = (atomic.group("inner") or atomic.group("bare") or "").strip()
        inner_fact = _scalar(inner, abi) or _vector(inner, abi)
        if inner_fact is None or inner_fact.lanes:
            return None
        # An _Atomic scalar is naturally aligned: its alignment is its size.
        fact = AbiFundamental(inner_fact.encoding, inner_fact.size, inner_fact.size)
    elif "__attribute__" in text:
        fact = _vector(text, abi)
    else:
        fact = _scalar(text, abi)
    return None if fact is None else Fact.present(fact)
