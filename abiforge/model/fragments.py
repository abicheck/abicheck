"""The union and bit-field fixtures of the ``c_layout`` seed, as modelled records.

Each fixture is one seed role (``lf.<id>``) holding the *old* state of a few
records: the declarations (``layout_model.Record``) are the single source of
the C text, of the consumer that reads them, and of the expected rows
(``operators/fragments``), so the three cannot drift apart.

A role's header declares the records plus two functions that reach the top
record through a pointer (``lf_<id>_fill``, ``lf_<id>_sum``): DWARF and the
header dump both see the type, and the consumer, compiled against the old
header, prints what the new library wrote. Bit-fields are only ever assigned
and read by value: no member address or ``offsetof`` is taken.
"""

from __future__ import annotations

from dataclasses import dataclass

from abiforge.model.layout_model import Member, Record

__all__ = [
    "BITFIELD_WIDTHS",
    "LAYOUT_FIXTURES",
    "Fixture",
    "bitfield_record",
    "consumer_text",
    "envelope",
    "payload",
    "role_text",
]

#: The bit-field widths of the research campaign: unit boundaries and their
#: neighbours (byte, half-unit and unit).
BITFIELD_WIDTHS = (1, 7, 8, 9, 15, 16, 17, 31, 32)


@dataclass(frozen=True)
class Fixture:
    """One role's old state: ``records`` in dependency order; the last is the
    *top* record the role's functions take."""

    id: str
    records: tuple[Record, ...]

    @property
    def role(self) -> str:
        return f"lf.{self.id}"

    @property
    def top(self) -> Record:
        return self.records[-1]


def _fn(fid: str) -> str:
    return f"lf_{fid}"


def role_text(fid: str, records: tuple[Record, ...]) -> tuple[str, str]:
    """(header, source) of role ``lf.<fid>`` declaring *records*."""
    top = records[-1]
    fn = _fn(fid)
    header = "\n".join(
        [
            *(r.render() for r in records),
            f"void {fn}_fill({top.spelling} *p);",
            f"unsigned long {fn}_sum(const {top.spelling} *p);",
        ]
    )
    fill: list[str] = ["memset(p, 0, sizeof *p);"]
    total: list[str] = []
    if top.tag == "union":
        fill = ["memset(p, 0x5A, sizeof *p);"]
        total = ["((const unsigned char *)p)[0]"]
    else:
        # Values are numbered over the *named* members, so inserting an
        # unnamed barrier never changes what is written, only where.
        named = [m for m in top.members if m.name is not None]
        for i, m in enumerate(named, start=1):
            if m.ctype.startswith("union "):
                fill.append(f"memset(&p->{m.name}, 0x5A, sizeof p->{m.name});")
                continue
            if m.extent is not None:
                continue
            value = i if m.width is None else i & ((1 << m.width) - 1)
            fill.append(f"p->{m.name} = {value};")
            total.append(f"(unsigned long)p->{m.name}")
    source = "\n".join(
        [
            f"void {fn}_fill({top.spelling} *p) {{ {' '.join(fill)} }}",
            f"unsigned long {fn}_sum(const {top.spelling} *p) {{ return "
            + (" + ".join(total) or "0")
            + "; }",
        ]
    )
    return header, source


def _printed(top: Record) -> list[str]:
    """The top record's members a consumer reads by value."""
    if top.tag == "union":
        return []
    return [
        m.name
        for m in top.members
        if m.name is not None and m.extent is None and not m.ctype.startswith("union ")
    ]


def consumer_text(fixtures: tuple[Fixture, ...]) -> str:
    """A consumer that fills every top record through the library and prints
    what it reads back with the old header's layout."""
    body: list[str] = []
    for fx in fixtures:
        fn = _fn(fx.id)
        names = _printed(fx.top)
        fmt = " ".join(f"{n}=%lu" for n in names)
        args = "".join(f", (unsigned long)v_{fx.id}.{n}" for n in names)
        body += [
            f"  {{ {fx.top.spelling} v_{fx.id}; memset(&v_{fx.id}, 0, sizeof v_{fx.id});",
            f"    {fn}_fill(&v_{fx.id});",
            f'    printf("{fx.id} {fmt} sum=%lu\\n"{args}, {fn}_sum(&v_{fx.id})); }}',
        ]
    return "\n".join(
        [
            "#include <stdio.h>",
            "#include <string.h>",
            '#include "edit_seed.h"',
            "int main(void) {",
            *body,
            "  return 0;",
            "}",
            "",
        ]
    )


# --- unions -----------------------------------------------------------------


def payload(fid: str, *members: Member) -> Record:
    return Record("union", f"Payload_{fid}", members)


def envelope(fid: str) -> Record:
    """A record embedding the union between a leading byte and a trailing word."""
    return Record(
        "struct",
        f"Envelope_{fid}",
        (
            Member("h", "char"),
            Member("p", f"union Payload_{fid}"),
            Member("t", "uint32_t"),
        ),
    )


def _char(n: int) -> Member:
    return Member("c", "char", extent=n)


_U64 = Member("bits", "uint64_t")
_DBL = Member("bits", "double")
_REAL = Member("real", "double")


def _union_fx(fid: str, *members: Member, embedded: bool = True) -> Fixture:
    u = payload(fid, *members)
    return Fixture(fid, (u, envelope(fid)) if embedded else (u,))


# --- bit-fields -------------------------------------------------------------


def bitfield_record(fid: str, width: int, barrier: str) -> Record:
    """``a : width``, ``b : 16``, ``c : 3`` and an ordinary ``tail``, with an
    unnamed ``: 0`` barrier ``before`` b, ``after`` b, or ``none``."""
    bar = Member(None, "unsigned", width=0)
    members = [Member("a", "unsigned", width=width)]
    if barrier == "before":
        members.append(bar)
    members.append(Member("b", "unsigned", width=16))
    if barrier == "after":
        members.append(bar)
    members += [Member("c", "unsigned", width=3), Member("tail", "unsigned char")]
    return Record("struct", f"Bits_{fid}", tuple(members))


def _bits_fx(fid: str, width: int, barrier: str = "none") -> Fixture:
    return Fixture(fid, (bitfield_record(fid, width, barrier),))


#: Every fixture, one per operator that needs its own old state.
LAYOUT_FIXTURES: tuple[Fixture, ...] = (
    _union_fx("uretype", _char(8), _U64),
    _union_fx("uretype_back", _char(8), _DBL),
    _union_fx("uadd", _char(8), _U64),
    _union_fx("uremove", _char(8), _U64, _REAL),
    *(_union_fx(f"uext{n}", _char(n), _U64) for n in (7, 8, 15, 16)),
    _union_fx("uext_char", _char(8)),
    _union_fx("upack", _char(9), _U64),
    _union_fx("ualign", _char(8), _U64, embedded=False),
    _union_fx("uctl", _char(9), _U64),
    *(_bits_fx(f"bw{w}", w) for w in BITFIELD_WIDTHS[:-1]),
    _bits_fx("bw17_shrink", 17),
    _bits_fx("bw16_before", 16, "before"),
    _bits_fx("bins7_before", 7),
    _bits_fx("bins17_before", 17),
    _bits_fx("bins17_after", 17),
    _bits_fx("brem17_after", 17, "after"),
    _bits_fx("brem7_before", 7, "before"),
    # The edits that close the row-shape classes the ones above miss
    # (``model/reduction.certify``): barrier removals whose size or absorption
    # differs, and width changes on either side of a barrier.
    _bits_fx("brem6_after", 6, "after"),
    _bits_fx("brem1_before", 1, "before"),
    _bits_fx("brem14_after", 14, "after"),
    _bits_fx("brem16_after", 16, "after"),
    _bits_fx("brem30_before", 30, "before"),
    _bits_fx("bw1_after", 1, "after"),
    _bits_fx("bw1_before", 1, "before"),
    Fixture(
        "balign",
        (
            Record(
                "struct",
                "Bits_balign",
                (Member("a", "unsigned", width=16), Member("b", "unsigned", width=16)),
            ),
        ),
    ),
    _bits_fx("bctl", 17),
)
