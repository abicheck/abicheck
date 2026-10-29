"""The initial value of a const integral global, read from the binary.

A header that only declares ``extern const int kv;`` (or a class's
``static const int k;``) carries no initializer, and the definition in a
``.cpp`` never reaches the header tier. The value is then only in the
binary: the object's bytes in its read-only section. This module reads those
bytes for the exact case that is decidable without DWARF -- a const variable
of a builtin or ``<stdint.h>`` integer type whose defining ELF symbol has the
same size as that type. Anything else keeps ``Variable.value`` as ``None``
(the honest "unknown"): a pointer, an enum, a class, a typedef abicheck cannot
resolve, a symbol that is undefined in this object, or a ``NOBITS`` section.

The value is written as a decimal integer, the same spelling a header
``init`` of a plain integer literal yields, so the two tiers compare equal
for the same number. Only ``Variable.value`` changes; the symbol facts and the
header facts are untouched.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Variable

log = logging.getLogger(__name__)

#: Integer spelling -> (size in bytes, signed). Only spellings whose size and
#: signedness are fixed by the language or by ``<stdint.h>``; ``int``-sized
#: ``long`` and friends are checked against the symbol's own size, so an LP32
#: or LP64 build reads the width the object really has.
_INTEGRAL_SHAPES: dict[str, tuple[int, bool]] = {
    "bool": (1, False),
    "char": (1, True),
    "signed char": (1, True),
    "unsigned char": (1, False),
    "short": (2, True),
    "short int": (2, True),
    "signed short": (2, True),
    "signed short int": (2, True),
    "unsigned short": (2, False),
    "unsigned short int": (2, False),
    "int": (4, True),
    "signed": (4, True),
    "signed int": (4, True),
    "unsigned": (4, False),
    "unsigned int": (4, False),
    "long": (8, True),
    "long int": (8, True),
    "signed long": (8, True),
    "signed long int": (8, True),
    "unsigned long": (8, False),
    "unsigned long int": (8, False),
    "long long": (8, True),
    "long long int": (8, True),
    "signed long long": (8, True),
    "signed long long int": (8, True),
    "unsigned long long": (8, False),
    "unsigned long long int": (8, False),
    "int8_t": (1, True),
    "uint8_t": (1, False),
    "int16_t": (2, True),
    "uint16_t": (2, False),
    "int32_t": (4, True),
    "uint32_t": (4, False),
    "int64_t": (8, True),
    "uint64_t": (8, False),
}

_CV_RE = re.compile(r"\b(?:const|volatile)\b")


def integral_shape(type_name: str) -> tuple[int, bool] | None:
    """``(size, signed)`` for a builtin or stdint integer *type_name*, else None.

    cv-qualifiers are ignored (``const int`` and ``int`` are one shape); a
    pointer, reference, array, or any other spelling is not an integer here.
    """
    spelling = " ".join(_CV_RE.sub(" ", type_name).split())
    return _INTEGRAL_SHAPES.get(spelling)


def populate_const_integral_values(snap: AbiSnapshot, so_path: Path) -> None:
    """Fill ``Variable.value`` for const integral globals the header left blank.

    A variable keeps any header-derived value. Only a ``const`` variable with
    no value whose defining symbol is an initialized object of the same size
    as its type is read. A binary that cannot be read is logged and leaves
    every value as it was: absent evidence, never a fabricated value.
    """
    candidates: dict[str, list[tuple[Variable, int, bool]]] = {}
    for var in snap.declarations.variables:
        if var.value is not None or not var.is_const or not var.mangled:
            continue
        shape = integral_shape(var.type)
        if shape is None:
            continue
        candidates.setdefault(var.mangled, []).append((var, shape[0], shape[1]))
    if not candidates:
        return
    try:
        with open(so_path, "rb") as fh:
            values = _read_symbol_values(fh, candidates)
    except (OSError, ELFError, ValueError) as exc:
        log.debug("const integral initializers not read from %s: %s", so_path, exc)
        return
    for name, entries in candidates.items():
        if name not in values:
            continue
        for var, _size, _signed in entries:
            var.value = str(values[name])


def _read_symbol_values(
    fh: IO[bytes],
    candidates: Mapping[str, list[tuple[Variable, int, bool]]],
) -> dict[str, int]:
    """The integer stored at each candidate's exported symbol, keyed by name.

    Only ``.dynsym`` is read: an exported object is the ABI surface. A
    local or hidden ``.symtab`` object (``static const int k = 1;``) has no
    binary-only value worth reporting, and the header carries it when it is
    public. The first initialized, defined ``STT_OBJECT`` of the candidate's
    name and size wins.
    """
    elf = ELFFile(fh)
    little = bool(elf.little_endian)
    values: dict[str, int] = {}
    for section in elf.iter_sections():
        if not isinstance(section, SymbolTableSection):
            continue
        if section["sh_type"] != "SHT_DYNSYM":
            continue
        for sym in section.iter_symbols():
            name = sym.name
            if name in values or name not in candidates:
                continue
            value = _symbol_integer(fh, elf, sym, candidates[name], little)
            if value is not None:
                values[name] = value
    return values


def _symbol_integer(
    fh: IO[bytes],
    elf: ELFFile,
    sym: Any,
    entries: list[tuple[Variable, int, bool]],
    little: bool,
) -> int | None:
    """The integer at *sym*'s address, or None if the symbol is not a readable
    initialized object of one of *entries*' sizes."""
    entry = sym.entry
    if entry["st_info"]["type"] != "STT_OBJECT":
        return None
    shndx = entry["st_shndx"]
    if not isinstance(shndx, int) or shndx == 0:  # SHN_UNDEF, or a special index
        return None
    size = int(entry["st_size"])
    shapes = {s for _var, s, _signed in entries}
    if size not in shapes:
        return None
    signed = next(sig for _var, s, sig in entries if s == size)
    section = elf.get_section(shndx)
    if section is None or section["sh_type"] == "SHT_NOBITS":
        return None
    offset_in_section = int(entry["st_value"]) - int(section["sh_addr"])
    if offset_in_section < 0 or offset_in_section + size > int(section["sh_size"]):
        return None
    fh.seek(int(section["sh_offset"]) + offset_in_section)
    raw = fh.read(size)
    if len(raw) != size:
        return None
    return int.from_bytes(raw, "little" if little else "big", signed=signed)
