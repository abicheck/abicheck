# SPDX-License-Identifier: Apache-2.0
"""CodeView procedure types spelled as the header readers spell a function type.

An LF_PROCEDURE carries a return type, a CV_call_e calling convention and an
argument list (an LF_ARGLIST whose last entry is T_NOTYPE for a variadic
tail). Spelled as ``fn(...)`` every procedure looked alike, so a callback
whose signature or stdcall/fastcall convention changed compared equal. This
module spells it through :func:`~abicheck.model.cc_attributes.function_type_text`,
the one shape castxml, clang and DWARF also use.

The calling-convention names are the CV_call_e table ``pdb_parser`` has always
carried; it moved here so the parser module stays under its size baseline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..model.cc_attributes import function_type_text

#: T_NOTYPE: the argument-list entry CodeView uses to mark a variadic tail.
T_NOTYPE = 0x0000

#: CV_call_e values whose convention is spelled on a function type. The default
#: (cdecl, and x64's single convention) is omitted, as a header spelling omits it.
_CV_CALL_CONVENTION_ATTRIBUTES: dict[int, str] = {
    0x04: "fastcall",
    0x07: "stdcall",
    0x0B: "thiscall",
    0x18: "vectorcall",
}

_CC_NAMES: dict[int, str] = {
    0x00: "cdecl",
    0x01: "far_cdecl",
    0x02: "pascal",
    0x03: "far_pascal",
    0x04: "fastcall",
    0x05: "far_fastcall",
    0x07: "stdcall",
    0x08: "far_stdcall",
    0x09: "syscall",
    0x0A: "far_syscall",
    0x0B: "thiscall",
    0x0D: "generic",
    0x11: "armcall",
    0x16: "clrcall",
    0x17: "inline",
    0x18: "vectorcall",
}


def procedure_spelling(
    return_name: str,
    calltype: int,
    arglist: list[int] | None,
    name_of: Callable[[int], str],
    *,
    pointer: bool,
) -> str:
    """Spell an LF_PROCEDURE from its resolved return name and argument list.

    *arglist* is the argument type indices (``None`` or empty for ``(void)``);
    *name_of* resolves one index to its spelling.
    """
    args = list(arglist or [])
    varargs = bool(args) and args[-1] == T_NOTYPE
    fixed = args[:-1] if varargs else args
    return function_type_text(
        return_name,
        [name_of(arg) for arg in fixed],
        varargs=varargs,
        pointer=pointer,
        convention=_CV_CALL_CONVENTION_ATTRIBUTES.get(calltype),
    )


def calling_convention_text(cc: int, table: Mapping[int, str] = _CC_NAMES) -> str:
    """The CV_call_e name for *cc*, or ``cc_<hex>`` when unnamed."""
    return table.get(cc, f"cc_{cc:#x}")
