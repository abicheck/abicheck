"""``plain_deepcopy`` must be observationally a ``copy.deepcopy``.

Oracle: ``copy.deepcopy`` itself. Two invariants over generated plain-data
graphs: the copy is equal to deepcopy's, and no *mutable* object (list,
dict, set, non-frozen dataclass instance) is shared between input and
output -- which is the independence the report envelope depends on. Sharing
is allowed only for subtrees containing nothing mutable.
"""

from __future__ import annotations

import copy
import dataclasses
import enum
import random
from typing import Any

import pytest

from abicheck.model.plain_copy import plain_deepcopy


class Color(enum.Enum):
    RED = 1
    BLUE = 2


@dataclasses.dataclass(frozen=True)
class FrozenBox:
    a: Any
    b: Any = None


@dataclasses.dataclass(frozen=True, slots=True)
class SlotBox:
    a: Any


@dataclasses.dataclass
class MutBox:
    a: Any
    b: Any = None


def _gen(rng: random.Random, depth: int = 0) -> Any:
    atoms = ["s", "", 0, 7, 1.5, True, None, Color.RED, frozenset({1, 2}), b"x"]
    if depth > 3 or rng.random() < 0.35:
        return rng.choice(atoms)
    kind = rng.randrange(7)
    kids = [_gen(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    if kind == 0:
        return kids
    if kind == 1:
        return {f"k{i}": v for i, v in enumerate(kids)}
    if kind == 2:
        return tuple(kids)
    if kind == 3:
        return {x for x in kids if isinstance(x, (str, int, type(None)))}
    if kind == 4:
        return FrozenBox(kids, _gen(rng, depth + 1)) if kids else FrozenBox("leaf")
    if kind == 5:
        return SlotBox(tuple(kids))
    box = MutBox(kids, _gen(rng, depth + 1))
    if rng.random() < 0.3:
        box.extra = [1, 2]  # an undeclared attribute, as scoping passes attach
    return box


def _mutables(obj: Any, out: dict[int, Any]) -> dict[int, Any]:
    if isinstance(obj, (list, dict, set)) or (
        dataclasses.is_dataclass(obj) and not type(obj).__dataclass_params__.frozen  # type: ignore[attr-defined]
    ):
        out[id(obj)] = obj
    children: list[Any] = []
    if isinstance(obj, (list, tuple, set)):
        children = list(obj)
    elif isinstance(obj, dict):
        children = list(obj.values())
    elif dataclasses.is_dataclass(obj):
        children = [getattr(obj, f.name) for f in dataclasses.fields(obj)]
        children += list(getattr(obj, "__dict__", {}).values())
    for c in children:
        _mutables(c, out)
    return out


@pytest.mark.parametrize("seed", range(200))
def test_equals_deepcopy_and_shares_no_mutable(seed: int) -> None:
    rng = random.Random(seed)
    src = _gen(rng)
    ours = plain_deepcopy(src)
    assert ours == copy.deepcopy(src)
    shared = set(_mutables(src, {})) & set(_mutables(ours, {}))
    assert not shared
    if isinstance(src, MutBox) and hasattr(src, "extra"):
        assert ours.extra == src.extra and ours.extra is not src.extra


def test_immutable_subtrees_are_shared_not_copied() -> None:
    frozen = FrozenBox(("a", Color.BLUE), SlotBox((1, 2)))
    assert plain_deepcopy(frozen) is frozen
    holder = FrozenBox([1])  # frozen, but holds a mutable list
    copied = plain_deepcopy(holder)
    assert copied is not holder and copied.a is not holder.a and copied == holder


def test_real_findings_match_deepcopy() -> None:
    from pathlib import Path

    from abicheck.checker import compare
    from abicheck.serialization import load_snapshot

    root = Path(__file__).parent / "fixtures" / "header_graph"
    changes = []
    for v1 in sorted(root.glob("case*__v1.json")):
        v2 = v1.with_name(v1.name.replace("__v1", "__v2"))
        if v2.exists():
            changes += compare(load_snapshot(v1), load_snapshot(v2)).changes
    assert changes, "no real findings to check -- fixture set moved"
    for c in changes:
        ours = plain_deepcopy(c)
        assert ours == copy.deepcopy(c)
        assert not (set(_mutables(c, {})) & set(_mutables(ours, {})))
