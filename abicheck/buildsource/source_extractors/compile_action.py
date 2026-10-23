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
"""Per-source operands and the compile action of one GNU-driver invocation.

A compile-database entry is one driver command, but it can name several
sources (``gcc -c a.c b.c``), switch the forced language between them
(``g++ -x c++ a.h -x none b.c``), and link objects and archives instead of
compiling (``gcc d.c -o d.so -shared c.o lib.a``). :func:`parse_gnu_operands`
reads the driver's argv tail once and returns each source as a
:class:`SourceOperand` with its own language, action and output, plus the
non-source inputs a link consumes.

This module is a leaf: it imports nothing from the compile-invocation model.
:func:`~.compile_invocation.parse_compile_invocation` calls it and stores the
result on :class:`~.compile_invocation.CompilerInvocation`.

Scope: the GNU driver vocabulary (gcc, g++, clang, clang++). CL-mode driver
operands (``/c``, ``/Fo``, ``/Tc``/``/Tp``) are a separate slice, and the
caller leaves them unset for the MSVC driver. Response files (``@file``) are
not expanded; the token is reported as a link input so the gap stays visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class CompileAction(StrEnum):
    """What the driver does with a source: the last of ``-E``/``-S``/``-c`` wins.

    ``-E`` overrides ``-S``, which overrides ``-c``. A command with none of the
    three runs the full compile-and-link pipeline, which is :attr:`LINK`.
    """

    PREPROCESS = "preprocess"  # -E: stop after the preprocessor
    ASSEMBLE = "assemble"  # -S: stop after code generation, emit assembly
    COMPILE = "compile"  # -c: stop after the assembler, emit an object
    LINK = "link"  # no stop option: compile each source, then link


@dataclass(frozen=True)
class SourceOperand:
    """One source the driver compiles, with the facts its own argv position decides."""

    #: The operand exactly as it is spelled in argv.
    source: str
    #: The gcc ``-x`` language that applies to this operand. The ``-x`` in
    #: effect at its position wins over the file extension; ``""`` when neither
    #: decides it (an extensionless file with no ``-x``).
    language: str
    action: CompileAction
    #: The file this operand's step produces: the ``-o`` value when it is
    #: unambiguous, otherwise the default name the driver picks. ``""`` for
    #: preprocessing to stdout. For :attr:`CompileAction.LINK` every operand
    #: shares the link output, because the intermediate objects are temporary.
    effective_output: str


@dataclass(frozen=True)
class GnuOperands:
    """The parsed argv tail of a GNU-driver invocation."""

    action: CompileAction
    operands: tuple[SourceOperand, ...]
    #: Non-source inputs a :attr:`CompileAction.LINK` consumes, in argv order.
    #: Empty for every other action, which does not invoke the linker.
    link_inputs: tuple[str, ...]


#: Options whose operand is the following token (``-o out``), not a joined
#: spelling. A joined form (``-Idir``, ``-DX=1``) is one token and is skipped
#: as a whole. Lists the GNU/Clang options whose argument is separate.
_SEPARATE_ARG_OPTIONS = frozenset(
    {
        "-o",
        "-x",
        "-I",
        "-D",
        "-U",
        "-include",
        "-imacros",
        "-isystem",
        "-iquote",
        "-idirafter",
        "-iprefix",
        "-iwithprefix",
        "-iwithprefixbefore",
        "-isysroot",
        "-MF",
        "-MT",
        "-MQ",
        "-L",
        "-Xlinker",
        "-Xassembler",
        "-Xpreprocessor",
        "-aux-info",
        "-T",
        "-u",
        "-z",
        "-target",
        "-arch",
        "-framework",
        "-install_name",
        "-compatibility_version",
        "-current_version",
    }
)

#: The gcc language name a file extension selects when no ``-x`` applies.
#: Case-sensitive: ``.C`` is C++ and ``.c`` is C, as gcc treats them.
_EXTENSION_LANGUAGE = {
    ".c": "c",
    ".i": "cpp-output",
    ".h": "c-header",
    ".cc": "c++",
    ".cp": "c++",
    ".cpp": "c++",
    ".cxx": "c++",
    ".c++": "c++",
    ".C": "c++",
    ".ii": "c++-cpp-output",
    ".hh": "c++-header",
    ".hpp": "c++-header",
    ".hxx": "c++-header",
    ".m": "objective-c",
    ".mm": "objective-c++",
    ".M": "objective-c++",
    ".s": "assembler",
    ".S": "assembler-with-cpp",
}

#: The object-or-listing suffix each action's default output name carries.
_DEFAULT_SUFFIX = {CompileAction.COMPILE: ".o", CompileAction.ASSEMBLE: ".s"}

#: The link output when a LINK command names no ``-o``.
_DEFAULT_LINK_OUTPUT = "a.out"


def _normalize_forced_language(value: str) -> str:
    """Map a ``-x`` value to the language it selects; ``none`` resets to extension."""
    return "" if value == "none" else value


def _scan(tail: list[str]) -> tuple[str | None, list[tuple[str, str]], set[str]]:
    """Return ``(-o value, [(positional, forced -x language)], stop flags)``.

    The forced language is ``""`` when no ``-x`` applies at that position.
    ``-x`` is the only option whose value changes how later positionals are
    read, so it is tracked here; every other option only consumes its operand.
    """
    output: str | None = None
    forced = ""
    positionals: list[tuple[str, str]] = []
    flags: set[str] = set()
    i = 0
    while i < len(tail):
        tok = tail[i]
        if tok == "-o" and i + 1 < len(tail):
            output = tail[i + 1]
            i += 2
            continue
        if tok == "-x" and i + 1 < len(tail):
            forced = _normalize_forced_language(tail[i + 1])
            i += 2
            continue
        if tok.startswith("-x") and len(tok) > 2:
            forced = _normalize_forced_language(tok[2:])
            i += 1
            continue
        if tok in _SEPARATE_ARG_OPTIONS:
            i += 2
            continue
        if tok in ("-c", "-S", "-E"):
            flags.add(tok)
        elif not tok.startswith("-"):
            positionals.append((tok, forced))
        i += 1
    return output, positionals, flags


def _action(flags: set[str]) -> CompileAction:
    if "-E" in flags:
        return CompileAction.PREPROCESS
    if "-S" in flags:
        return CompileAction.ASSEMBLE
    if "-c" in flags:
        return CompileAction.COMPILE
    return CompileAction.LINK


def _language_for(token: str, forced: str) -> str | None:
    """The language *token* compiles as, or ``None`` when it is a link input.

    A ``-x`` in effect makes the token a source of that language. Otherwise the
    extension decides, and a file with no known source extension is passed to
    the linker, which is what gcc does with it.
    """
    if forced:
        return forced
    return _EXTENSION_LANGUAGE.get(PurePosixPath(token).suffix)


def _default_output(action: CompileAction, source: str) -> str:
    """The name the driver writes for *source* when ``-o`` does not apply.

    The driver places the object or listing in the working directory, named
    after the source's basename. Preprocessing writes to stdout.
    """
    if action is CompileAction.PREPROCESS:
        return ""
    return PurePosixPath(source).stem + _DEFAULT_SUFFIX[action]


def _effective_output(
    action: CompileAction, source: str, output: str | None, single: bool
) -> str:
    if action is CompileAction.LINK:
        return output or _DEFAULT_LINK_OUTPUT
    # ``-o`` names one file, so it applies only when a single source produces
    # one output. gcc rejects ``-o`` with several ``-c``/``-S``/``-E`` sources;
    # the default keeps each operand's output well defined regardless.
    if output is not None and single:
        return output
    return _default_output(action, source)


def parse_gnu_operands(tail: list[str]) -> GnuOperands:
    """Parse the argv tail of one GNU-driver invocation (the driver token removed).

    Every source operand gets its own language, action and effective output.
    Under :attr:`CompileAction.LINK` the non-source positionals are the link
    inputs; under every other action the linker is not run, so there are none.
    """
    output, positionals, flags = _scan(tail)
    action = _action(flags)
    sources: list[tuple[str, str]] = []
    link_inputs: list[str] = []
    for token, forced in positionals:
        language = _language_for(token, forced)
        if language is None:
            link_inputs.append(token)
        else:
            sources.append((token, language))
    single = len(sources) == 1
    operands = tuple(
        SourceOperand(
            source=token,
            language=language,
            action=action,
            effective_output=_effective_output(action, token, output, single),
        )
        for token, language in sources
    )
    return GnuOperands(
        action=action,
        operands=operands,
        link_inputs=tuple(link_inputs) if action is CompileAction.LINK else (),
    )
