"""The std-by-value check reads a slot's resolved identity, not only its spelling.

Bug class: a detector keyed on one backend's spelling of a type. castxml
spells a signature slot by its bare leaf (``string``, ``vector<int>``) and
records the qualified type it resolved (``std::basic_string<char>``) in
``type_identities_fact``; ``idioms._is_std_by_value`` looked for ``std::`` in
the spelling only, so a public function returning ``std::string`` by value
read no ``public_api_exposes_stl_by_value`` on castxml (found by the edit
corpus's ``stl_by_value_function_added``).

The oracle is the table: each slot is labelled by hand with whether it passes a
standard-library *class* by value, independent of how the check decides it.
"""

from __future__ import annotations

import pytest

from abicheck.model import ParamKind
from abicheck.model.fact import Fact
from abicheck.policy.std_by_value import is_std_by_value


def _ids(*names: str) -> Fact[tuple[str, ...]]:
    return Fact.present(tuple(names))


# (spelling, identities, pointer depth, kind, passes a std class by value)
CASES = [
    # castxml: bare leaf + resolved identity
    ("string", _ids("std::basic_string<char>"), 0, ParamKind.VALUE, True),
    ("vector<int>", _ids("std::vector<int>"), 0, ParamKind.VALUE, True),
    ("map<int, int>", _ids("std::map<int, int>"), 0, ParamKind.VALUE, True),
    ("shared_ptr<W>", _ids("std::shared_ptr<W>", "W"), 0, ParamKind.VALUE, True),
    ("const string", _ids("std::basic_string<char>"), 0, ParamKind.VALUE, True),
    # the same slots passed indirectly
    ("string *", _ids("std::basic_string<char>"), 1, ParamKind.POINTER, False),
    ("const string &", _ids("std::basic_string<char>"), 0, ParamKind.REFERENCE, False),
    ("vector<int> &&", _ids("std::vector<int>"), 0, ParamKind.RVALUE_REF, False),
    # a library's own record that merely shares the leaf
    ("string", _ids("mylib::string"), 0, ParamKind.VALUE, False),
    # a std enum that is a scalar (std::byte), a scalar alias, a builtin
    ("byte", _ids("std::byte"), 0, ParamKind.VALUE, False),
    ("size_t", _ids(), 0, ParamKind.VALUE, False),
    ("int", _ids(), 0, ParamKind.VALUE, False),
    # clang/gcc: the spelling is qualified, no identity is needed
    ("std::string", None, 0, ParamKind.VALUE, True),
    ("std::vector<int, std::allocator<int> >", None, 0, ParamKind.VALUE, True),
    ("std::size_t", None, 0, ParamKind.VALUE, False),
    ("const std::string &", None, 0, ParamKind.REFERENCE, False),
    # an identity that was not collected never makes a slot a std class
    ("string", Fact.not_collected("clang JSON"), 0, ParamKind.VALUE, False),
]


@pytest.mark.parametrize(("spelling", "ids", "depth", "kind", "expected"), CASES)
def test_a_slot_is_std_by_value_exactly_when_labelled(
    spelling: str,
    ids: Fact[tuple[str, ...]] | None,
    depth: int,
    kind: ParamKind,
    expected: bool,
) -> None:
    assert is_std_by_value(spelling, depth, kind, ids) is expected
