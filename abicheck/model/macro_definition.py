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

"""ADR-074: the logical preprocessor macro definition behind ``-D/--define``.

A leaf module (stdlib only) so every layer that needs it -- the Click option
(``cli_options``), the one canonical fold (``cli_options.merge_compile_config``),
and the frontend argv translation -- shares ONE parser, ONE merge rule and ONE
emitter instead of re-deriving ``NAME``/``VALUE`` from a raw string at each
site.

Why a typed value object rather than a raw ``-DNAME=VALUE`` string: the merge
rule ADR-074 D3 specifies is *by macro name*, so every layer that folds two
sources of definitions together has to know where the name ends. Splitting
that out repeatedly is exactly how ``NAME=A=B`` becomes three fields; here it
is parsed once, at the CLI boundary, and never re-parsed.

**Why this cannot inject an unrelated compiler option** (ADR-074 D4): a
``MacroDefinition`` renders to exactly ONE argv token, always prefixed with
the frontend's define switch (``-D``/``/D``). A user-supplied string is never
placed in argv on its own, and :func:`parse_macro_definition` additionally
rejects any spelling whose *name* is not a bare C identifier -- which is what
turns ``--define=-Xclang``, ``--define=@resp.txt`` and ``--define=-DFOO`` into
loud usage errors rather than a second flag. Whitespace is rejected outright
(see :func:`parse_macro_definition`), so one token can never be re-split by a
downstream ``shlex``-style consumer either.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "MacroDefinition",
    "MacroDefinitionError",
    "macro_definition_tokens",
    "merge_macro_definitions",
    "parse_macro_definition",
    "parse_macro_definitions",
]

#: A C/C++ macro identifier. Deliberately ASCII-only: an extended-identifier
#: macro name (``-Dété``) is accepted by neither MSVC's ``/D`` nor
#: castxml's own argv handling portably, so abicheck rejects it at the CLI
#: boundary rather than shipping a spelling that means four different things
#: on four frontends (ADR-074 D2).
_MACRO_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: A function-like macro definition (``F(x)=x+1``). Detected separately from a
#: plain bad-identifier rejection so the error can say *why* it is unsupported
#: rather than "not a valid macro name" (ADR-074 D2 documents the limitation).
_FUNCTION_LIKE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(")


class MacroDefinitionError(ValueError):
    """An invalid ``-D/--define`` operand. Raised with a message that names
    the supported spelling, so the CLI can surface it verbatim as a usage
    error without re-wording it."""


@dataclass(frozen=True, order=True)
class MacroDefinition:
    """One logical preprocessor definition: ``name`` plus an optional
    replacement ``value``.

    ``value is None`` is ``-DNAME`` (the compiler's own implicit ``1``);
    ``value == ""`` is ``-DNAME=`` (defined to an empty token sequence).
    These are genuinely different preprocessor states -- ``#if NAME`` is an
    error for the second and ``1`` for the first -- so they are kept
    distinct rather than normalized together.
    """

    name: str
    value: str | None = None

    @property
    def spelling(self) -> str:
        """The canonical operand spelling (``NAME`` / ``NAME=VALUE``) -- what
        a ``.abicheck.yml`` ``compile.defines`` entry would hold, and what
        the resolved-request/dry-run receipt reports."""
        return self.name if self.value is None else f"{self.name}={self.value}"

    def token(self, style: str = "gnu") -> str:
        """This definition as ONE frontend argv token.

        *style* is ``"gnu"`` (``-D``; GCC, Clang, and castxml in **either**
        ``--castxml-cc-gnu`` or ``--castxml-cc-msvc`` emulation mode -- see
        ADR-074's compatibility matrix and the identical unconditional
        ``-D`` in ``buildsource/source_extractors/castxml.py``) or ``"cl"``
        (``/D``; a driver actually invoked in MSVC/clang-cl mode, which
        today is only ``buildsource/source_extractors/clang.py``'s
        ``--driver-mode=cl`` L4 replay).
        """
        if style not in ("gnu", "cl"):
            raise ValueError(f"unknown define token style {style!r}")
        return ("-D" if style == "gnu" else "/D") + self.spelling


def parse_macro_definition(text: str) -> MacroDefinition:
    """Parse ONE ``-D/--define`` operand into a :class:`MacroDefinition`.

    Accepted: ``NAME`` and ``NAME=VALUE``. The split is on the **first**
    ``=`` only, so ``NAME=A=B`` is the macro ``NAME`` with the replacement
    list ``A=B`` -- never three fields. An empty ``VALUE`` (``NAME=``) is
    valid and preserved.

    Rejected, each with its own message:

    * an empty operand;
    * whitespace anywhere (a replacement list containing a space cannot be
      spelled identically across GNU-style and ``cl``-style command lines,
      and would additionally be re-splittable by any downstream
      ``shlex``-style consumer -- use ``compile.options`` in
      ``.abicheck.yml`` when a build genuinely needs one);
    * a function-like definition (``F(x)=...``) -- same portability reason;
    * anything whose name is not a bare ASCII C identifier, which is what
      catches an operand the user accidentally re-prefixed
      (``--define=-DFOO``), a ``-U`` undefine, an ``@response-file``, and
      every other attempt to make one definition become a second compiler
      option.
    """
    if not text:
        raise MacroDefinitionError(
            "--define requires a macro definition, got an empty value "
            "(expected NAME or NAME=VALUE)"
        )
    name, sep, value = text.partition("=")
    # Checked BEFORE the whitespace rule: a parameter list with a space in it
    # (`F(x, y)=x`) trips both, and "this is a function-like macro" is the
    # message that actually tells the user what to do (a test asserts it).
    if _FUNCTION_LIKE_RE.match(name if sep else text):
        raise MacroDefinitionError(
            f"--define {text!r} is a function-like macro definition, which "
            "abicheck does not support (its portability across CastXML, Clang "
            "and MSVC command lines is not established). Define an object-like "
            "macro, or use .abicheck.yml's compile.options."
        )
    if any(ch.isspace() for ch in text):
        raise MacroDefinitionError(
            f"--define {text!r} contains whitespace, which abicheck cannot spell "
            "identically across GNU-style and MSVC-style compiler command lines. "
            "Put a whitespace-bearing replacement list in .abicheck.yml under "
            "compile.options instead."
        )
    if not _MACRO_NAME_RE.match(name):
        hint = ""
        if name.startswith(("-D", "/D")):
            hint = (
                " -- pass the macro name alone: the -D is the option itself, so "
                f"write -D{name[2:] or 'NAME'}, not -D{name}"
            )
        elif name.startswith(("-U", "/U")):
            hint = (
                " -- abicheck has no --undefine counterpart; remove the macro "
                "from .abicheck.yml's compile.defines instead"
            )
        elif name.startswith(("-", "/", "@")):
            hint = (
                " -- --define takes a macro definition, not a compiler option; "
                "general compiler flags belong in .abicheck.yml under "
                "compile.options"
            )
        raise MacroDefinitionError(
            f"--define {text!r} is not a valid macro definition: {name!r} is not "
            f"a C identifier (expected NAME or NAME=VALUE){hint}"
        )
    return MacroDefinition(name, value if sep else None)


def parse_macro_definitions(values: Iterable[str]) -> tuple[MacroDefinition, ...]:
    """:func:`parse_macro_definition` over a repeated option's values, in the
    order given. No merging happens here -- a later duplicate of the same
    name is preserved for :func:`merge_macro_definitions` to resolve, so the
    parse step stays a pure, order-preserving validation."""
    return tuple(parse_macro_definition(v) for v in values)


def merge_macro_definitions(
    lower: Sequence[MacroDefinition], higher: Sequence[MacroDefinition]
) -> tuple[MacroDefinition, ...]:
    """Merge two precedence tiers **by macro name** (ADR-074 D3).

    * A name defined in *higher* replaces every *lower* definition of that
      name, and takes *higher*'s own position (which is what makes the
      resulting token order last-wins-correct against a raw ``-DNAME``
      smuggled through a lower tier).
    * A name defined only in *lower* keeps its relative order and value.
    * Within one tier, a repeated name resolves last-wins, so the result
      holds exactly one definition per name.

    Deterministic and independent of any dict iteration order: the result is
    ordered by first appearance among the surviving definitions, lower tier
    first. Order is preserved rather than sorted because ``-D`` order is not
    semantically free in general (an unrelated lower-tier ``-U`` in
    ``compile.options`` is positioned against it).
    """
    overridden = {d.name for d in higher}
    out: list[MacroDefinition] = []
    seen: dict[str, int] = {}
    for definition in (*(d for d in lower if d.name not in overridden), *higher):
        if definition.name in seen:
            out[seen[definition.name]] = definition
            continue
        seen[definition.name] = len(out)
        out.append(definition)
    return tuple(out)


def macro_definition_tokens(
    definitions: Iterable[MacroDefinition], style: str = "gnu"
) -> list[str]:
    """One argv token per definition, in order. See
    :meth:`MacroDefinition.token` for what *style* selects."""
    return [d.token(style) for d in definitions]


def define_spellings_from_tokens(tokens: Iterable[str]) -> tuple[str, ...]:
    """The *effective* macro spellings an already-rendered frontend argv tail
    defines, in the order a compiler would end up with them.

    The inverse of :func:`macro_definition_tokens`, over the effective
    ``CompileContext.gcc_option_tokens`` -- i.e. after
    ``cli_options.merge_compile_config`` folded config and CLI together. The
    ``--dry-run`` receipt reports this rather than the raw ``-D/--define``
    values so it states what the frontend will actually be given, config
    contributions included, and so it cannot drift from the real run.

    Two rules make the answer match the compiler rather than the raw token
    list (CodeRabbit review):

    * **A token is a define only if it really is one.** Every candidate is
      run through :func:`parse_macro_definition`, so a token that merely
      *starts* with ``-D``/``/D`` but carries no valid macro name is
      skipped rather than reported as a macro -- ``/Deps/include`` (a path
      operand following a ``/I`` in ``compile.options``) is not the macro
      ``eps/include``. The separated two-token form (``-D`` then ``NAME``)
      is recognized too, since ``compile.options`` can legitimately spell
      it that way.
    * **One entry per macro, last wins.** A lower-precedence ``-DA=9`` from
      ``compile.options`` followed by the winning ``-DA=2`` is reported as
      ``A=2`` alone. Reporting both would name a value the compiler
      discards, which is the opposite of an "effective" receipt.

    Deliberately tolerant rather than strict: this reads a rendered argv
    that may contain anything ``compile.options`` allowed, so an
    unparseable candidate is skipped, never raised on -- a dry-run receipt
    must not be the thing that fails a run.
    """
    found: list[MacroDefinition] = []
    items = list(tokens)
    index = 0
    while index < len(items):
        token = items[index]
        operand: str | None = None
        if token in ("-D", "/D") and index + 1 < len(items):
            operand = items[index + 1]
            index += 1
        elif token.startswith(("-D", "/D")) and len(token) > 2:
            operand = token[2:]
        index += 1
        if operand is None:
            continue
        try:
            found.append(parse_macro_definition(operand))
        except MacroDefinitionError:
            continue  # not a macro definition; some other flag's operand
    return tuple(d.spelling for d in merge_macro_definitions((), found))


def defines_receipt_line(tokens: Iterable[str]) -> str | None:
    """The ``defines: ...`` line a ``--dry-run`` receipt prints for an
    already-resolved frontend argv tail, or ``None`` when the run defines
    nothing (so the receipt stays exactly as it read before ADR-074).

    Lives here, next to the spelling rules it renders, so ``dump``'s and
    ``compare``'s receipts cannot format the same fact two ways.
    """
    spellings = define_spellings_from_tokens(tokens)
    return f"defines: {', '.join(spellings)}" if spellings else None
