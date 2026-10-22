"""`storage.decode_memo`: the snapshot decoder's memo tables are pure
speedups -- a memoized decode equals a fresh one, malformed input still
raises, and a stored snapshot round-trips to identical content and digest.

The oracle is constructed independently of the decoder (``Fact(...)`` built
field by field, ``Enum(...)`` called directly), never the decoder's own
build helper.
"""

from __future__ import annotations

import itertools
import json

import pytest

from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    ElfVisibility,
    Fact,
    FactStatus,
    Function,
    Param,
    ParamKind,
    ScopeOrigin,
    SymbolBinding,
    Variable,
    Visibility,
)
from abicheck.serialization import load_snapshot, snapshot_to_dict, write_snapshot
from abicheck.storage.canonical import semantic_digest
from abicheck.storage.decode_memo import enum_decoder
from abicheck.storage.fact_codec import decode_fact
from abicheck.storage.semantic_ir_codec import _fact_from_dict

_VALUES = [None, True, False, 0, 1, 2, "", "x", "1"]
_DIAGNOSTICS = [None, [], ["one"], ["one", "two"]]
_PRODUCERS = [None, "castxml", "dwarf"]


def _raw(status: str, value: object, diagnostics: object, producer: object) -> dict:
    raw: dict = {"status": status, "value": value}
    if diagnostics is not None:
        raw["diagnostics"] = diagnostics
    if producer is not None:
        raw["producer"] = producer
    return raw


@pytest.mark.parametrize("decode_twice", [1, 2])
def test_decode_fact_matches_independent_oracle_over_small_domain(
    decode_twice: int,
) -> None:
    """Every status x scalar value x diagnostics x producer: the decoded fact
    equals one built field by field, keeps the value's exact type
    (``True``/``1`` never merge), and a repeat decode answers the same."""
    for status, value, diags, producer in itertools.product(
        [s.value for s in FactStatus], _VALUES, _DIAGNOSTICS, _PRODUCERS
    ):
        expected = Fact(
            status=FactStatus(status),
            value=value,
            diagnostics=tuple(diags or ()),
            producer=producer,
        )
        for _ in range(decode_twice):
            got = decode_fact(_raw(status, value, diags, producer), 1_000_000)
            assert got == expected
            assert type(got.value) is type(value)


def test_list_valued_fact_is_never_shared() -> None:
    first = decode_fact({"status": "present", "value": []}, 1_000_000)
    second = decode_fact({"status": "present", "value": []}, 1_000_000)
    assert first == second
    assert first.value is not second.value


def test_malformed_fact_still_raises_after_valid_shapes_are_memoized() -> None:
    decode_fact({"status": "present", "value": True}, 1_000_000)
    for _ in range(2):
        with pytest.raises(ValueError):
            decode_fact({"status": "no-such-status", "value": True}, 1_000_000)
        with pytest.raises(TypeError, match="fact producer must be a string"):
            decode_fact({"status": "present", "value": True, "producer": 7}, 1_000_000)


def test_semantic_fact_missing_required_key_raises_even_when_complete_shape_cached() -> (
    None
):
    complete = {"status": "present", "value": "int", "diagnostics": []}
    assert _fact_from_dict(complete, as_tuple=False, field_name="spelling") == Fact(
        status=FactStatus.PRESENT, value="int", diagnostics=(), producer=None
    )
    for broken in (
        {"status": "present", "value": "int"},
        {"status": "present", "diagnostics": []},
    ):
        with pytest.raises(ValueError):
            _fact_from_dict(broken, as_tuple=False, field_name="spelling")
    # An integer-valued field rejects a string the plain field accepted.
    with pytest.raises(ValueError):
        _fact_from_dict(complete, as_tuple=False, as_int=True, field_name="size_bits")


@pytest.mark.parametrize(
    "enum_cls",
    [ParamKind, Visibility, AccessLevel, ElfVisibility, SymbolBinding, ScopeOrigin],
)
def test_enum_decoder_matches_enum_call(enum_cls: type) -> None:
    decode = enum_decoder(enum_cls)
    for member in enum_cls:
        assert decode(member.value) is enum_cls(member.value)
    for bad in ("no-such-member", "", 3, None):
        with pytest.raises(ValueError):
            enum_cls(bad)
        with pytest.raises(ValueError):
            decode(bad)


def _large_snapshot(n: int) -> AbiSnapshot:
    snap = AbiSnapshot(library="libmemo.so", version="1.0")
    snap.declarations.functions = [
        Function(
            name=f"f{i}",
            mangled=f"_Z2f{i}i",
            return_type="int" if i % 3 else "void",
            params=[
                Param(name="a", type="int", kind=ParamKind.VALUE),
                Param(name="p", type="char", kind=ParamKind.POINTER, pointer_depth=1),
            ],
            visibility=Visibility.PUBLIC if i % 2 else Visibility.HIDDEN,
            is_inline=bool(i % 5 == 0),
            access=AccessLevel.PROTECTED if i % 7 == 0 else AccessLevel.PUBLIC,
        )
        for i in range(n)
    ]
    snap.declarations.variables = [
        Variable(name=f"g{i}", mangled=f"g{i}", type="int", is_const=bool(i % 2))
        for i in range(n // 4)
    ]
    return snap


def test_stored_snapshot_round_trips_to_identical_content_and_digest(tmp_path) -> None:
    """Store -> load -> store: the decoded document (and its digest) equals
    the original's, whichever facts the memo answered."""
    snap = _large_snapshot(300)
    original = json.loads(json.dumps(snapshot_to_dict(snap)))
    path = tmp_path / "entry.json.zst"
    write_snapshot(snap, path, compression="zstd")
    loaded = load_snapshot(path)
    again = json.loads(json.dumps(snapshot_to_dict(loaded)))
    assert again == original
    assert semantic_digest(again) == semantic_digest(original)
    assert loaded.declarations.functions == snap.declarations.functions
    assert loaded.declarations.variables == snap.declarations.variables
