# SPDX-License-Identifier: Apache-2.0
"""Binary purity evidence for a header-built snapshot (WS-3, ``vision`` A6).

A binary-only dump proves a member pure from its debug declaration or, for a
GCC producer that never marks one, from the class vtable slot bound to
``__cxa_pure_virtual`` (:mod:`.elf_pure_virtual_slots`). A header-built
snapshot has no debug member DIEs, so its purity is castxml's or clang's
header attribute -- which ``--depth binary`` correctly discards. This module
reads the *same* binary fact for the header declarations, so the projection
can keep it at binary depth instead of dropping it.

Mapping a header virtual method to its vtable slot needs the slot order.
The header backends already build that order as ``RecordType.vtable`` (the
Itanium declaration order, an override reusing its base's slot). It is trusted
only where it is unambiguous:

* the record and every ancestor have at most one base and no virtual base
  (a single primary chain, so the primary vtable is the only one);
* the library emits that record's vtable (a ``_ZTV`` symbol demangling to
  ``vtable for <record>``);
* the emitted vtable holds exactly ``len(RecordType.vtable)`` slots
  (``(symbol size / 8) - 2``: offset-to-top and typeinfo come first).

Anywhere else the record gets **no claim**, with the reason recorded, and
no purity is inferred. A claim is a bool; "unknown" is never recorded as
"not pure".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .elf_pure_virtual_slots import read_pure_virtual_slots

if TYPE_CHECKING:
    from ..model.elf_facts import ElfMetadata
    from ..model.entities import RecordType

__all__ = ["BinaryPurityClaims", "binary_purity_claims", "binary_purity_extension"]

#: ``e_ident[EI_CLASS]`` of a 32-bit ELF file.
_ELFCLASS32 = 1
#: Offset-to-top and typeinfo pointer precede the first virtual slot.
_HEADER_WORDS = 2
_VTABLE_PREFIX = "vtable for "


@dataclass
class BinaryPurityClaims:
    """What the emitted vtables prove about a header-built snapshot.

    ``functions``: mangled name -> whether that virtual is pure in the
    binary's vtable slot. ``records``: qualified record name -> whether its
    vtable proves it abstract. ``unobserved``: qualified record name -> the
    reason no claim was made (absent from both maps, never a negative).
    """

    functions: dict[str, bool] = field(default_factory=dict)
    records: dict[str, bool] = field(default_factory=dict)
    unobserved: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.functions or self.records or self.unobserved)


def _vtable_symbols(elf_meta: ElfMetadata) -> dict[str, tuple[str, int]]:
    """``{qualified class name: (_ZTV symbol, size)}`` for the vtables the
    library defines, read from the symbol table's own spelling (demangled)."""
    from ..demangle import demangle_batch

    defined = [s for s in elf_meta.symbols if s.name.startswith("_ZTV")]
    if not defined:
        return {}
    demangled = demangle_batch([s.name for s in defined])
    out: dict[str, tuple[str, int]] = {}
    for sym in defined:
        text = demangled.get(sym.name) or ""
        if text.startswith(_VTABLE_PREFIX):
            out.setdefault(text[len(_VTABLE_PREFIX) :], (sym.name, sym.size))
    return out


def _word_size(elf_path: Path) -> int:
    """Bytes per vtable slot (a pointer) of *elf_path*: 4 for ELF32, 8 for ELF64."""
    with open(elf_path, "rb") as f:
        ident = f.read(5)
    return 4 if len(ident) == 5 and ident[4] == _ELFCLASS32 else 8


#: An Itanium destructor's name ends in its complete (``D1``), base (``D2``) or
#: deleting (``D0``) form; a digit before ``D`` makes it a method named ``D…``.
_DESTRUCTOR_SPELLING = re.compile(r"(?<![0-9])D[012]Ev$")


def _claim_key(record: RecordType) -> str:
    """The key a record's claim is stored under: its qualified name, so two
    same-named classes in different namespaces never share one claim."""
    return record.qualified_name or record.name


def _known_list(fact: Any) -> list[str] | None:
    """The value of a ``Fact[list[str]]`` sibling, or ``None`` when it is not
    collected -- unknown is never read as an empty list (ADR-063 Phase 0:
    the ``*_fact`` sibling is the reader, never the legacy field)."""
    if fact is None or not fact.is_present or fact.value is None:
        return None
    return list(fact.value)


def _single_primary_chain(
    record: RecordType, by_name: Mapping[str, RecordType]
) -> str | None:
    """``None`` when *record* and its ancestors form one unambiguous chain;
    otherwise the reason it does not."""
    seen: set[str] = set()
    current: RecordType | None = record
    while current is not None:
        if current.name in seen:
            return "base chain is cyclic"
        seen.add(current.name)
        bases = _known_list(current.bases_fact)
        virtual = _known_list(current.virtual_bases_fact)
        if bases is None or virtual is None:
            return f"the base list of {current.name} is not known"
        if virtual:
            return f"{current.name} has a virtual base"
        if len(bases) > 1:
            return f"{current.name} has multiple bases"
        if not bases:
            return None
        base_name = bases[0]
        current = by_name.get(base_name)
        if current is None:
            return f"base {base_name} is not in the header model"
    return None


def _slot_entries(vtable: list[str]) -> list[str | None]:
    """One entry per vtable slot: the mangled name of the virtual function, or
    ``None`` for a destructor's slot. The header model lists a virtual
    destructor once (``~X``), but the Itanium ABI gives it two slots (the
    complete-object and deleting destructors), so it expands to two."""
    entries: list[str | None] = []
    for name in vtable:
        if name.startswith("~") or (
            name.startswith("_Z") and _DESTRUCTOR_SPELLING.search(name)
        ):
            entries.extend((None, None))
        else:
            entries.append(name)
    return entries


def binary_purity_claims(
    elf_path: Path,
    elf_meta: ElfMetadata,
    records: Iterable[RecordType],
) -> BinaryPurityClaims:
    """The purity claims for the header-built *records* of the library at
    *elf_path* (see the module docstring for the rule).

    A library that imports no ``__cxa_pure_virtual`` has no pure slot, so
    "not pure" is claimed for its emitted vtables. A file that cannot be read
    claims nothing: every record is then unobserved.
    """
    claims = BinaryPurityClaims()
    word = _word_size(elf_path)
    all_records = list(records)
    by_name = {r.name: r for r in all_records}
    emitted = _vtable_symbols(elf_meta)
    pure_slots = read_pure_virtual_slots(elf_path, (i.name for i in elf_meta.imports))

    for record in all_records:
        if record.is_union or record.is_opaque:
            continue
        slots = _known_list(record.vtable_fact)
        if not slots:
            continue
        if pure_slots is None:
            claims.unobserved[_claim_key(record)] = "the vtable slots could not be read"
            continue
        reason = _single_primary_chain(record, by_name)
        if reason is not None:
            claims.unobserved[_claim_key(record)] = reason
            continue
        symbol = emitted.get(_claim_key(record))
        if symbol is None:
            claims.unobserved[_claim_key(record)] = (
                "the library does not emit its vtable"
            )
            continue
        vtable_symbol, size = symbol
        entries = _slot_entries(slots)
        slot_count = size // word - _HEADER_WORDS
        if size % word or slot_count != len(entries):
            claims.unobserved[_claim_key(record)] = (
                f"emitted vtable has {slot_count} slots, the header model "
                f"{len(entries)}"
            )
            continue
        pure = pure_slots.get(vtable_symbol, frozenset())
        for index, mangled in enumerate(entries):
            if mangled is not None:
                claims.functions[mangled] = index in pure
        claims.records[_claim_key(record)] = bool(pure)
    return claims


def binary_purity_extension(claims: BinaryPurityClaims) -> dict[str, Any] | None:
    """The snapshot value (``AbiSnapshot.binary_purity``) for *claims*, or
    ``None`` when nothing was read -- absent reads as "no binary evidence"."""
    if not claims:
        return None
    return {
        "functions": dict(sorted(claims.functions.items())),
        "records": dict(sorted(claims.records.items())),
        "unobserved": dict(sorted(claims.unobserved.items())),
    }
