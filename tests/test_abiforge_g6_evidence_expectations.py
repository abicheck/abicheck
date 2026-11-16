"""Evidence-scoped expectations for three corpus operators (G6).

Each operator's ground truth is written here by hand, from the catalog's own
impact text and from what the evidence operand carries, never from abicheck's
answer:

- ``tag_type_renamed`` at binary depth: the catalog says the binary break is
  "reported by the removal findings ... (func_removed /
  instantiation_missing_from_binary), not by this one". Headers still expect
  the tag rename itself.
- ``nonpod_field_added_in_tail_padding``: headers expect
  ``TAIL_PADDING_REUSE_CHANGED``. DWARF has no data size, so its expectation
  stays the tail-padding kind; the DWARF cells stay red until the WS-1
  ``observable_at`` batch makes that kind unobservable there. No DWARF rule.
- ``secondary_base_became_polymorphic`` at binary depth: a stripped binary has
  no inheritance graph, so the secondary group shows as the vtable growing a
  slot (``VTABLE_SLOT_COUNT_CHANGED``). DWARF and headers still expect
  ``SECONDARY_VTABLE_GROUP_CHANGED``.

The shapes vary the language standard and the evidence level; the headers
baseline is pinned so a rule can never leak into the primary configuration.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import STANDARD_BY_NAME, Toolchain, default_config
from abiforge.operators import OP_BY_NAME

PRODUCER = Toolchain("gcc", 13, "gcc-13", "g++-13")
CXX_STANDARDS = ("c++11", "c++17", "c++23")

_TAG_REMOVAL_KINDS = frozenset(
    {"FUNC_REMOVED", "FUNC_REMOVED_ELF_ONLY", "INSTANTIATION_MISSING_FROM_BINARY"}
)


#: The advisory a BREAKING verdict owes (``EditOp.with_derived``), not the edit.
_ADVISORY = "SONAME_BUMP_RECOMMENDED"


def _edit_kinds(exp) -> frozenset[str]:
    return exp.expected - {_ADVISORY}


def _cfg(std: str, evidence: str):
    base = default_config("c++", PRODUCER)
    return base.with_(
        std=STANDARD_BY_NAME[std],
        evidence=evidence,
        stripped=evidence == "headers",
    )


@pytest.mark.parametrize("std", CXX_STANDARDS)
def test_tag_type_renamed_binary_expects_the_removal_not_the_tag(std: str) -> None:
    exp = OP_BY_NAME["tag_type_renamed"].expect(_cfg(std, "binary"))
    assert "TAG_TYPE_RENAMED" not in _edit_kinds(exp)
    named = set().union(*exp.any_of) if exp.any_of else set()
    assert named & _TAG_REMOVAL_KINDS, (std, exp)


@pytest.mark.parametrize("std", CXX_STANDARDS)
def test_tag_type_renamed_headers_still_expects_the_tag_rename(std: str) -> None:
    exp = OP_BY_NAME["tag_type_renamed"].expect(_cfg(std, "headers"))
    assert _edit_kinds(exp) == frozenset({"TAG_TYPE_RENAMED"}), (std, exp)


@pytest.mark.parametrize("std", ("c++17", "c++23"))
def test_nonpod_tail_padding_headers_still_expects_the_reuse(std: str) -> None:
    exp = OP_BY_NAME["nonpod_field_added_in_tail_padding"].expect(_cfg(std, "headers"))
    assert _edit_kinds(exp) == frozenset({"TAIL_PADDING_REUSE_CHANGED"}), exp


@pytest.mark.parametrize("std", CXX_STANDARDS)
def test_secondary_base_binary_expects_the_vtable_slot_growth(std: str) -> None:
    exp = OP_BY_NAME["secondary_base_became_polymorphic"].expect(_cfg(std, "binary"))
    assert _edit_kinds(exp) == frozenset({"VTABLE_SLOT_COUNT_CHANGED"}), (std, exp)


@pytest.mark.parametrize("evidence", ("dwarf", "headers"))
@pytest.mark.parametrize("std", CXX_STANDARDS)
def test_secondary_base_dwarf_and_headers_keep_the_secondary_group(
    std: str, evidence: str
) -> None:
    exp = OP_BY_NAME["secondary_base_became_polymorphic"].expect(_cfg(std, evidence))
    assert _edit_kinds(exp) == frozenset({"SECONDARY_VTABLE_GROUP_CHANGED"}), (
        std,
        evidence,
        exp,
    )
