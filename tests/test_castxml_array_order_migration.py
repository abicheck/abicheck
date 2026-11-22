"""Generation-91 castxml multi-dimensional array order migration (bug class
``storage.cross_generation_spelling_drift``).

Before generation 91 the castxml backend spelled ``T m[A][B]`` as ``T[B][A]``
(outer bound appended after the inner ones). A castxml header baseline dumped
earlier, compared against a fresh dump of the same source, must report no
change, and a real reshape must still be reported. The oracle is the C
declarator: each case states the declared bounds and, by hand, the spelling
castxml wrote for them, at every position a type spelling is stored.
"""

from __future__ import annotations

import itertools
import re

import pytest

from abicheck.checker import compare
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.castxml_array_migration import (
    migrate_castxml_array_order,
    reverse_array_bounds,
)
from tests.test_castxml_array_migration import _POSITIONS, _snapshot, _stored

# (C spelling of the declared type, what castxml wrote at generations 15-90)
_CASES = (
    pytest.param("int[2][3]", "int[3][2]", id="two-dim"),
    pytest.param("int[4][3][2]", "int[2][3][4]", id="three-dim"),
    pytest.param("const char*[2][5]", "const char*[5][2]", id="pointer-elements"),
    pytest.param("row[4][2]", "row[2][4]", id="typedef-element"),
    pytest.param("int[3]*[2]", "int[3]*[2]", id="array-of-pointer-to-array"),
    pytest.param("Box<int[2][3]>[4]", "Box<int[2][3]>[4]", id="template-arg-kept"),
    pytest.param("char[][4]", "char[4][]", id="unbounded-outer"),
    pytest.param("double[6]", "double[6]", id="one-dim"),
)


def _c_bounds(dims: tuple[int, ...]) -> str:
    return "".join(f"[{d}]" for d in dims)


@pytest.mark.parametrize(
    "dims",
    [d for n in (1, 2, 3, 4) for d in itertools.product((1, 2, 5), repeat=n)],
)
def test_reverse_is_the_old_resolver_order(dims: tuple[int, ...]) -> None:
    """Exhaustive small domain: the old resolver appended each outer bound
    after the inner spelling, i.e. wrote the C bounds reversed; reversing
    recovers C order, and is its own inverse."""
    old = "int" + _c_bounds(tuple(reversed(dims)))
    assert reverse_array_bounds(old) == "int" + _c_bounds(dims)
    assert reverse_array_bounds(reverse_array_bounds(old)) == old


def _last_indices(spelling: str) -> str:
    """What a pre-generation-15 castxml wrote: each bound outside a template
    argument list as its last index."""
    head, sep, tail = spelling.rpartition(">")
    return head + sep + re.sub(r"\[(\d+)\]", lambda m: f"[{int(m[1]) - 1}]", tail)


@pytest.mark.parametrize("extractor", [14, 15, 90])
@pytest.mark.parametrize(("fresh", "old"), _CASES)
@pytest.mark.parametrize("position", _POSITIONS)
def test_old_castxml_array_order_compares_clean(
    position: str, fresh: str, old: str, extractor: int
) -> None:
    if extractor < 15:
        old = _last_indices(old)
    baseline = _stored(position, old, "castxml", extractor)
    current = snapshot_from_dict(
        snapshot_to_dict(_snapshot(position, fresh, "castxml"))
    )
    assert compare(baseline, current).changes == []


@pytest.mark.parametrize("position", ["return", "param", "variable"])
def test_real_reshape_still_reported(position: str) -> None:
    """Control: int[2][3] -> int[3][2] is a reshape. Stored at generation 90
    the old declaration reads int[3][2]; after migration it is int[2][3], so
    a fresh int[3][2] still differs."""
    baseline = _stored(position, "int[3][2]", "castxml", 90)
    current = snapshot_from_dict(
        snapshot_to_dict(_snapshot(position, "int[3][2]", "castxml"))
    )
    assert compare(baseline, current).changes


@pytest.mark.parametrize(
    ("producer", "extractor"),
    [("castxml", 91), ("clang", 90), ("hybrid", 90), (None, 90)],
)
def test_order_kept_outside_old_castxml(producer: str | None, extractor: int) -> None:
    document = {"ast_producer": producer, "variables": [{"type": "int[3][2]"}]}
    assert migrate_castxml_array_order(document, extractor) == (document, [])


def test_migration_never_mutates_and_says_so() -> None:
    document = {"ast_producer": "castxml", "variables": [{"type": "int[3][2]"}]}
    migrated, notices = migrate_castxml_array_order(document, 90)
    assert migrated["variables"] == [{"type": "int[2][3]"}]
    assert document["variables"] == [{"type": "int[3][2]"}]
    assert len(notices) == 1 and "multi-dimensional" in notices[0]
