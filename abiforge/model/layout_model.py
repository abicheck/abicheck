"""An independent layout model for the union and bit-field fixtures of ``c_layout``.

The expected rows of ``operators/fragments`` come from this model, never from
a checker or from the compiler run under test. It covers exactly the fixture
fragment: C11 records on SysV x86-64 (LP64, little-endian) built by gcc or
clang, members of the scalar types in ``SCALARS``, char arrays, one embedded
union, ``unsigned`` bit-fields of width 1..32 and unnamed ``unsigned : 0``
barriers, under default packing or ``-fpack-struct=1`` (``Flags.pack``).
Anything else raises ``Unsupported``, so an operator can report the cell
unsupported instead of guessing.

Rules (all quantities in bits):

- An ordinary member goes at the next multiple of its alignment, capped at
  the pack value. A record's alignment is the largest capped member
  alignment (a named bit-field counts its declared type's alignment, an
  unnamed one does not); its size is the end of its last member rounded up
  to that alignment. A union places every member at 0.
- A named bit-field of width *w* goes at the current bit offset unless it
  would straddle a boundary of its declared type's storage unit (32 bits),
  in which case it starts at the next unit. Under ``-fpack-struct=1`` there
  is no straddle rule: the field goes at the current bit.
- An unnamed ``: 0`` barrier rounds the offset up to the next unit boundary.
  Under ``-fpack-struct=1`` gcc caps the barrier's alignment at the pack
  value (it rounds to the next *byte*), while clang still rounds to the
  32-bit unit; under ``#pragma pack(1)`` both round to the unit. The pragma
  result is what the research campaign measured, so it does not transfer to
  the generator's flag: ``producer`` is part of the model's input for
  exactly this case.

``tests/test_abiforge_layout_model.py`` checks the model against compiler
probes (byte images of each field set to all-ones; ``sizeof``/``_Alignof``)
and against raw DWARF, at both packings and on both producers.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SCALARS",
    "Layout",
    "Member",
    "Record",
    "Unsupported",
    "layout",
]


class Unsupported(ValueError):
    """The record or configuration lies outside the modelled fragment."""


#: (size, alignment) in bytes of the scalar types the fixtures use.
SCALARS: dict[str, tuple[int, int]] = {
    "char": (1, 1),
    "unsigned char": (1, 1),
    "unsigned": (4, 4),
    "uint32_t": (4, 4),
    "uint64_t": (8, 8),
    "double": (8, 8),
}
_UNIT = 32  # the storage unit (and alignment) of ``unsigned``, in bits


@dataclass(frozen=True)
class Member:
    """One declared member.

    ``ctype``: a ``SCALARS`` key or ``union <Name>`` of an already-modelled
    union. ``extent``: an array of that many elements. ``width``: a bit-field
    (``ctype`` must be ``unsigned``); ``name=None`` with ``width=0`` is the
    unnamed barrier.
    """

    name: str | None
    ctype: str
    extent: int | None = None
    width: int | None = None

    def render(self) -> str:
        if self.width is not None:
            return f"{self.ctype} {self.name or ''} : {self.width};".replace("  ", " ")
        dim = f"[{self.extent}]" if self.extent is not None else ""
        return f"{self.ctype} {self.name}{dim};"


@dataclass(frozen=True)
class Record:
    tag: str  # "struct" | "union"
    name: str
    members: tuple[Member, ...]

    def render(self) -> str:
        body = " ".join(m.render() for m in self.members)
        return f"{self.tag} {self.name} {{ {body} }};"

    @property
    def spelling(self) -> str:
        return f"{self.tag} {self.name}"


@dataclass(frozen=True)
class Layout:
    """A record's layout in bits: size, alignment, and per named member its
    bit offset and (for a bit-field) width."""

    size: int
    align: int
    offsets: dict[str, int]
    widths: dict[str, int]


def _check_config(pack: int | None, producer: str) -> None:
    if pack not in (None, 1):
        raise Unsupported(f"pack {pack} is outside the model (default or 1)")
    if producer not in ("gcc", "clang"):
        raise Unsupported(f"producer {producer!r} is outside the model")


def _round(n: int, a: int) -> int:
    return -(-n // a) * a


def layout(
    record: Record,
    *,
    pack: int | None,
    producer: str,
    known: dict[str, Layout] | None = None,
) -> Layout:
    """*record*'s layout built with ``-fpack-struct=pack`` by *producer*'s family.

    *known* holds the layouts of unions a member embeds, by spelling.
    """
    _check_config(pack, producer)
    known = known or {}
    cap = 8 * (pack if pack is not None else 1 << 30)
    offset = 0
    end = 0
    align = 8
    offsets: dict[str, int] = {}
    widths: dict[str, int] = {}
    for m in record.members:
        if m.width is not None:
            if m.ctype != "unsigned" or not 0 <= m.width <= _UNIT:
                raise Unsupported(f"bit-field {m.render()} is outside the model")
            if record.tag == "union":
                raise Unsupported("bit-fields in a union are outside the model")
            if m.name is None:
                if m.width != 0:
                    raise Unsupported("only unnamed zero-width barriers are modelled")
                # clang aligns to the declared type's unit whatever the
                # packing; gcc caps the barrier's alignment at the pack value.
                barrier = (
                    _UNIT if pack is None or producer == "clang" else min(_UNIT, cap)
                )
                offset = _round(offset, barrier)
                continue
            if m.width == 0:
                raise Unsupported("a named zero-width bit-field is ill-formed")
            if pack is None and offset // _UNIT != (offset + m.width - 1) // _UNIT:
                offset = _round(offset, _UNIT)
            offsets[m.name] = offset
            widths[m.name] = m.width
            offset += m.width
            align = max(align, min(_UNIT, cap))
            end = max(end, offset)
            continue
        if m.name is None:
            raise Unsupported("unnamed ordinary members are outside the model")
        if m.ctype in SCALARS:
            size_b, align_b = SCALARS[m.ctype]
            size, malign = 8 * size_b, 8 * align_b
        elif m.ctype in known:
            size, malign = known[m.ctype].size, known[m.ctype].align
        else:
            raise Unsupported(f"member type {m.ctype!r} is outside the model")
        if m.extent is not None:
            if m.extent < 1:
                raise Unsupported(
                    "zero-length and flexible arrays are outside the model"
                )
            size *= m.extent
        malign = min(malign, cap)
        align = max(align, malign)
        if record.tag == "union":
            offsets[m.name] = 0
            end = max(end, size)
            continue
        offset = _round(offset, malign)
        offsets[m.name] = offset
        offset += size
        end = max(end, offset)
    return Layout(_round(end, align), align, offsets, widths)
