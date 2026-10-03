"""Forward-reference linking in the PDB type database (dead-code plan,
Stage D): one rule, the first complete definition of a name wins, matching
the layout ``pdb_metadata`` records, and every name/size lookup follows it."""

from __future__ import annotations

import pytest
from test_pdb_parser import (
    _build_tpi_stream,
    _make_lf_enum,
    _make_lf_enumerate,
    _make_lf_fieldlist,
    _make_lf_structure,
)

from abicheck.pdb_parser import (
    LF_ENUM,
    LF_FIELDLIST,
    LF_STRUCTURE,
    TypeDatabase,
    parse_tpi_stream,
)


class TestForwardRefLinkingFirstDefinitionWins:
    """A forward ref resolves to the *first* complete definition of its name,
    the same ODR rule ``pdb_metadata`` uses for a record's canonical layout,
    and every name/size lookup goes through that one link. Enumerated over
    every placement of one forward ref among one to three same-named
    definitions of different sizes; the oracle is "lowest type index among
    the definitions", stated directly."""

    @staticmethod
    def _db(records: list[tuple[int, bytes]]) -> TypeDatabase:
        db = TypeDatabase(parse_tpi_stream(_build_tpi_stream(records)))
        db.parse_all()
        return db

    @pytest.mark.parametrize(
        ("n_defs", "fwd_at"),
        [(n, at) for n in (1, 2, 3) for at in range(n + 1)],
    )
    def test_struct_forward_ref(self, n_defs: int, fwd_at: int) -> None:
        sizes = [4 * (i + 1) for i in range(n_defs)]
        records = [
            (LF_STRUCTURE, _make_lf_structure(0, 0, 0, sz, "Foo")) for sz in sizes
        ]
        records.insert(
            fwd_at, (LF_STRUCTURE, _make_lf_structure(0, 0x0080, 0, 0, "Foo"))
        )
        db = self._db(records)
        fwd_ti = 0x1000 + fwd_at
        assert db.type_size(fwd_ti) == sizes[0]
        resolved = db.resolve_struct(fwd_ti)
        assert resolved is not None and resolved.byte_size == sizes[0]
        assert db.type_name(fwd_ti) == "Foo"

    def test_a_struct_and_an_enum_sharing_a_name_never_cross_link(self) -> None:
        fl = _make_lf_fieldlist([_make_lf_enumerate(0, 0, "A")])
        db = self._db(
            [
                (LF_STRUCTURE, _make_lf_structure(0, 0x0080, 0, 0, "Dual")),  # 0x1000
                (LF_STRUCTURE, _make_lf_structure(0, 0, 0, 16, "Dual")),  # 0x1001
                (LF_FIELDLIST, fl),  # 0x1002
                (LF_ENUM, _make_lf_enum(1, 0x0080, 0x74, 0, "Dual")),  # 0x1003
                (LF_ENUM, _make_lf_enum(1, 0, 0x13, 0x1002, "Dual")),  # 0x1004
            ]
        )
        assert db.type_size(0x1000) == 16
        assert db.type_size(0x1003) == 8
        assert db.resolve_enum(0x1000) is None
        assert db.resolve_struct(0x1003) is None

    def test_an_enum_forward_ref_takes_the_definitions_underlying_size(self) -> None:
        fl = _make_lf_fieldlist([_make_lf_enumerate(0, 0, "A")])
        db = self._db(
            [
                (LF_ENUM, _make_lf_enum(0, 0x0080, 0, 0, "E")),  # fwd, no underlying
                (LF_FIELDLIST, fl),
                (LF_ENUM, _make_lf_enum(1, 0, 0x13, 0x1001, "E")),  # int64
            ]
        )
        assert db.type_size(0x1000) == 8
        assert db.type_name(0x1000) == "enum E"
