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

"""A variable's alignment is *declared* only where the evidence states it.

Two extractors recorded a value as declared that their evidence did not
state: castxml (which writes no ``align`` on a ``Variable``) recorded the
type's natural alignment, and the DWARF reader took an absent
``DW_AT_alignment`` as unknown where DWARF 5 states the default. Each test
below enumerates its extractor's small input domain against a table written
from the evidence's own rules, not from the implementation.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.extract.dwarf_variable_alignment import dwarf_variable_alignment_fact
from abicheck.extract.headers.castxml.context import CastxmlParserContext
from abicheck.extract.headers.castxml.preprocessed_text import (
    PreprocessedText,
    alignment_files,
    attach_preprocessed_text,
    read_preprocessed_text,
)
from abicheck.extract.headers.castxml.variable_alignment import (
    variable_alignment_fact,
)
from abicheck.model.availability import FactStatus

# ---------------------------------------------------------------------------
# castxml: which preprocessed files spell an alignment specifier
# ---------------------------------------------------------------------------

_SPELLS = [
    "extern int v __attribute__((aligned(64)));",
    "extern int v __attribute__((__aligned__(16)));",
    "extern _Alignas(16) int v;",
    "extern alignas(32) int v;",
    "typedef int al16 __attribute__ ((aligned (16)));",
    "extern __declspec(align(16)) int v;",
    "extern __declspec( align (8)) int v;",
]
_SPELLS_NOT = [
    "extern int v;",
    "int posix_memalign(void **p, unsigned long a, unsigned long n);",
    "typedef int unaligned_t; int alignment(void);",
    "int my_aligned_alloc(void);",
    # A macro definition is a directive, not code: only its expansion counts.
    "#define ALIGN16 __attribute__((aligned(16)))",
]


@pytest.mark.parametrize("line", _SPELLS)
def test_a_file_whose_code_spells_a_specifier_is_recorded(line: str) -> None:
    assert alignment_files({"a.h": {1: line}}) == frozenset({"a.h"})


@pytest.mark.parametrize("line", _SPELLS_NOT)
def test_a_file_whose_code_spells_none_is_not_recorded(line: str) -> None:
    assert alignment_files({"a.h": {1: line}}) == frozenset()


@pytest.mark.parametrize("recorded", [None, frozenset(), frozenset({"/x/a.h"})])
def test_the_alignment_file_set_survives_the_document_round_trip(recorded) -> None:
    root = Element("CastXML")
    attach_preprocessed_text(root, PreprocessedText({}, "cdecl", recorded))
    table = read_preprocessed_text(root)
    assert table is not None
    assert table.alignment_files == recorded
    # Not recorded means no file is known to be free of a specifier.
    assert table.spells_alignment("/x/b.h") is (recorded is None)
    assert table.spells_alignment("/x/a.h") is (
        recorded is None or "/x/a.h" in recorded
    )


# ---------------------------------------------------------------------------
# castxml: the variable alignment fact, exhaustively
# ---------------------------------------------------------------------------

_VAR_FILE, _TD_FILE = "/x/api.h", "/x/types.h"


def _castxml_ctx(
    *, via_typedef: bool, var_spells: bool, td_spells: bool, recorded: bool, explicit
) -> tuple[CastxmlParserContext, Element]:
    root = Element("CastXML")
    SubElement(root, "File", id="f1", name=_VAR_FILE)
    SubElement(root, "File", id="f2", name=_TD_FILE)
    SubElement(root, "FundamentalType", id="_1", name="int", size="32", align="32")
    SubElement(root, "Typedef", id="_2", name="myint", type="_1", file="f2")
    SubElement(root, "CvQualifiedType", id="_3", type="_2", const="1")
    var = SubElement(
        root, "Variable", id="_4", name="v", type="_3" if via_typedef else "_1"
    )
    var.set("file", "f1")
    if explicit is not None:
        var.set("align", str(explicit))
    files = {k for k, on in ((_VAR_FILE, var_spells), (_TD_FILE, td_spells)) if on}
    table = PreprocessedText({}, "", frozenset(files) if recorded else None)
    attach_preprocessed_text(root, table)
    ctx = CastxmlParserContext(root, set(), set())
    ctx.build_id_map()
    return ctx, var


_CASTXML_CELLS = list(itertools.product([False, True], repeat=4))


@pytest.mark.parametrize(
    ("via_typedef", "var_spells", "td_spells", "recorded"), _CASTXML_CELLS
)
@pytest.mark.parametrize("explicit", [None, 512])
def test_castxml_natural_alignment_is_declared_only_where_no_specifier_can_apply(
    via_typedef, var_spells, td_spells, recorded, explicit
) -> None:
    ctx, var = _castxml_ctx(
        via_typedef=via_typedef,
        var_spells=var_spells,
        td_spells=td_spells,
        recorded=recorded,
        explicit=explicit,
    )
    fact = variable_alignment_fact(ctx, var, 32)
    # Oracle: an align castxml wrote is declared. Otherwise a specifier can
    # apply when the run did not record the files, or the variable's file
    # spells one, or (only through the typedef) the typedef's file does.
    can_apply = not recorded or var_spells or (via_typedef and td_spells)
    if explicit is not None:
        assert (fact.status, fact.value) == (FactStatus.PRESENT, explicit)
    elif can_apply:
        assert (fact.status, fact.value) == (FactStatus.PARTIAL, 32)
    else:
        assert (fact.status, fact.value) == (FactStatus.PRESENT, 32)


def test_castxml_unknown_natural_alignment_stays_unknown() -> None:
    ctx, var = _castxml_ctx(
        via_typedef=False,
        var_spells=True,
        td_spells=False,
        recorded=True,
        explicit=None,
    )
    fact = variable_alignment_fact(ctx, var, None)
    assert (fact.status, fact.value) == (FactStatus.PRESENT, None)


# ---------------------------------------------------------------------------
# DWARF: an absent DW_AT_alignment in a DWARF 5 unit states the default
# ---------------------------------------------------------------------------

_DW_ATE_FLOAT, _DW_ATE_SIGNED = 4, 5


class _Die:
    def __init__(self, cu: _Cu, offset: int, tag: str, **attrs) -> None:
        self.tag, self.offset = tag, offset
        self.attributes = {
            k: SimpleNamespace(
                value=v, form="DW_FORM_ref4" if k == "DW_AT_type" else ""
            )
            for k, v in attrs.items()
        }
        cu.dies[offset] = self


class _Cu:
    def __init__(self, version: int, address_size: int) -> None:
        self.header = {"version": version, "address_size": address_size}
        self.cu_offset = 0
        self.dies: dict[int, _Die] = {}

    def get_DIE_from_refaddr(self, offset: int) -> _Die:  # noqa: N802 - pyelftools
        return self.dies[offset]


def _type_chain(cu: _Cu, shape: str) -> int:
    """Build the type *shape* names; return its DIE offset."""
    _Die(cu, 1, "DW_TAG_base_type", DW_AT_encoding=_DW_ATE_SIGNED, DW_AT_byte_size=4)
    _Die(cu, 2, "DW_TAG_base_type", DW_AT_encoding=_DW_ATE_FLOAT, DW_AT_byte_size=8)
    _Die(cu, 3, "DW_TAG_typedef", DW_AT_type=1, DW_AT_alignment=16)
    _Die(cu, 4, "DW_TAG_const_type", DW_AT_type=1)
    _Die(cu, 5, "DW_TAG_array_type", DW_AT_type=1)
    _Die(cu, 6, "DW_TAG_structure_type", DW_AT_byte_size=4)
    _Die(cu, 7, "DW_TAG_typedef", DW_AT_type=1)
    return {
        "int": 1,
        "double": 2,
        "aligned_typedef": 3,
        "const_int": 4,
        "int_array": 5,
        "struct": 6,
        "plain_typedef": 7,
    }[shape]


#: The type's default alignment in bytes as DWARF states it: a scalar's is
#: its size up to the unit's address size (i386 aligns an 8-byte double to
#: 4 while placing a global at 8, so beyond it DWARF states no single
#: number); a typedef's own DW_AT_alignment wins; qualifiers, plain typedefs
#: and arrays pass through; a record's natural alignment is not stated.
_DEFAULT_BYTES = {
    ("int", 8): 4,
    ("int", 4): 4,
    ("double", 8): 8,
    ("double", 4): None,
    ("aligned_typedef", 8): 16,
    ("aligned_typedef", 4): 16,
    ("const_int", 8): 4,
    ("const_int", 4): 4,
    ("int_array", 8): 4,
    ("int_array", 4): 4,
    ("struct", 8): None,
    ("struct", 4): None,
    ("plain_typedef", 8): 4,
    ("plain_typedef", 4): 4,
}

_DIE_FORMS = ["standalone", "completes_declaration", "declaration"]


@pytest.mark.parametrize(("shape", "address_size"), sorted(_DEFAULT_BYTES))
@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("own", [None, 64])
@pytest.mark.parametrize("form", _DIE_FORMS)
def test_dwarf_variable_alignment_reads_what_the_unit_states(
    shape, address_size, version, own, form
) -> None:
    cu = _Cu(version, address_size)
    attrs: dict[str, int] = {"DW_AT_type": _type_chain(cu, shape)}
    if own is not None:
        attrs["DW_AT_alignment"] = own
    if form == "completes_declaration":
        attrs["DW_AT_specification"] = 99
    elif form == "declaration":
        attrs["DW_AT_declaration"] = 1
    die = _Die(cu, 50, "DW_TAG_variable", **attrs)
    fact = dwarf_variable_alignment_fact(die, cu)

    if own is not None:
        # The unit's own statement, in any DWARF version.
        assert (fact.status, fact.value) == (FactStatus.PRESENT, own * 8)
        return
    default = _DEFAULT_BYTES[(shape, address_size)] if version >= 5 else None
    if default is None:
        assert (fact.status, fact.value) == (FactStatus.PRESENT, None)
    elif form == "standalone":
        assert (fact.status, fact.value) == (FactStatus.PRESENT, default * 8)
    else:
        # GCC writes a definition-only specifier on neither DIE.
        assert (fact.status, fact.value) == (FactStatus.PARTIAL, default * 8)


# --- comparing a lower bound with an exact value ----------------------------------

_ALIGN_FACTS = [
    (status, value) for status in ("present", "partial") for value in (32, 128, 512)
]


#: Every power-of-two alignment (bits) a variable can have, for the oracle.
_ALIGNMENTS = tuple(8 << k for k in range(13))
#: An exported object's address alignment (bits), or unknown.
_PLACEMENTS = (None, 64, 256, 1024)


def _admitted(status: str, value: int, placement: int | None) -> set[int]:
    """The alignments a recorded fact admits, enumerated.

    An exact record admits its value. A lower bound admits every alignment
    at or above it that the object's address still allows (an object aligned
    to A sits at a multiple of A); an address that contradicts the bound
    says nothing.
    """
    if status == "present":
        return {value}
    above = {a for a in _ALIGNMENTS if a >= value}
    placed = {a for a in above if placement is None or a <= placement}
    return placed or above


@pytest.mark.parametrize(
    "old,new", list(itertools.product(_ALIGN_FACTS, repeat=2)), ids=str
)
@pytest.mark.parametrize(
    "placement", list(itertools.product(_PLACEMENTS, repeat=2)), ids=str
)
def test_alignment_change_is_reported_only_when_provable(
    old: tuple[str, int],
    new: tuple[str, int],
    placement: tuple[int | None, int | None],
) -> None:
    from abicheck.compare.declaration_facts import alignment_changes
    from abicheck.model.fact import Fact

    def entity(status: str, value: int) -> SimpleNamespace:
        fact = Fact.present(value) if status == "present" else Fact.partial(value)
        return SimpleNamespace(declared_alignment_bits=fact)

    got = alignment_changes(
        "v",
        "v",
        entity(*old),
        entity(*new),
        entity_id=None,  # type: ignore[arg-type]
        placement=placement,
    )
    disjoint = not (_admitted(*old, placement[0]) & _admitted(*new, placement[1]))
    assert bool(got) is disjoint, (old, new, placement, got)
