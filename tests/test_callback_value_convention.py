# SPDX-License-Identifier: Apache-2.0
"""Record call-trait evidence at direct and table callback boundaries."""

import pytest

from abicheck.compare.callback_value_convention import callback_value_changes
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.declarations import Function, Param
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.fact import Fact
from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.vocabulary import AccessLevel


def snapshot(trait, signature, *, table=False, size=64, private=False, base=False):
    scalar = AbiFundamental(FundamentalEncoding.SIGNED, 8, 8)
    value = RecordType(
        "V",
        "struct",
        size_bits=size,
        trivial_for_calls=trait,
        fields=[TypeField("x", "long", 0, type_abi_fact=Fact.present(scalar))],
        bases=["Base"] if base else [],
    )
    records = [value]
    function = Function("run", "run", "void", params=[Param("fn", "Slot")])
    if table:
        records.append(
            RecordType(
                "Table",
                "struct",
                fields=[
                    TypeField(
                        "visit",
                        "Slot",
                        access=AccessLevel.PRIVATE if private else AccessLevel.PUBLIC,
                    )
                ],
            )
        )
        function = Function(
            "run", "run", "Table*", return_type_identities_fact=Fact.present(("Table",))
        )
    return AbiSnapshot(
        library="test.so",
        version="1",
        functions=[function],
        types=records,
        typedefs_qualified={"Slot": signature},
        elf=ElfMetadata(machine="EM_X86_64", elf_class=64, symbols=[ElfSymbol("run")]),
    )


@pytest.mark.parametrize(
    "signature,table,size,private,base,expected",
    [
        ("int (*)(V)", False, 64, False, False, "p0.callback.p0"),
        ("V (*)()", True, 64, False, False, "return.visit.callback.return"),
        ("int (*)(V)", True, 64, False, False, "return.visit.callback.p0"),
        ("V (*)()", False, 192, False, False, None),
        ("V (*)()", False, 64, False, True, None),
        ("V* (*)()", True, 64, False, False, None),
        ("int (*)(const V&)", False, 64, False, False, None),
        ("int (*)(V)", True, 64, True, False, None),
    ],
)
def test_compiler_transition_requires_a_matching_public_slot(
    signature, table, size, private, base, expected
):
    old, new = (
        snapshot(t, signature, table=table, size=size, private=private, base=base)
        for t in (True, False)
    )
    rows = callback_value_changes(old, new, old.function_map, new.function_map)
    assert [(r.symbol, r.old_value, r.new_value) for r in rows] == (
        [("run", f"{expected}:trivial", f"{expected}:nontrivial")] if expected else []
    )


def test_changed_callback_prototype_never_joins_different_slots():
    old = snapshot(True, "int (*)(V)")
    new = snapshot(False, "int (*)(long, V)")
    assert callback_value_changes(old, new, old.function_map, new.function_map) == []
    new.function_map["run"].params.clear()
    assert callback_value_changes(old, new, old.function_map, new.function_map) == []
