"""Generation-38 castxml base-offset migration (bug class
``storage.cross_generation_spelling_drift``).

Before generation 38 the castxml backend stored castxml's ``<Base offset>``
verbatim -- a byte offset -- in ``RecordType.base_offsets``, which every
other producer (DWARF, the clang layout pass) fills in bits. A castxml
baseline dumped earlier, compared against a fresh dump of the *same* source,
must report no change. The oracle is the source itself: each case states the
declared bases' offsets, worked out by hand from the class definition, in
the bytes castxml wrote and the bits a fresh dump stores.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
)
from abicheck.storage.generation_migrations import FACT_GENERATIONS_KEY

# (class shape, stored castxml bytes, fresh bits)
_CASES = (
    # struct A { int a, b; }; struct B { double d; }; struct D : A, B {};
    pytest.param({"A": 0, "B": 8}, {"A": 0, "B": 64}, id="second-base-after-8-bytes"),
    # struct E {}; struct P { int x; }; struct W : E, P {};  (EBO: both at 0)
    pytest.param({"E": 0, "P": 0}, {"E": 0, "P": 0}, id="empty-base-all-zero"),
    # struct L { void* v; }; struct M { char c; }; struct R { long q; };
    # struct X : L, M, R {};
    pytest.param(
        {"L": 0, "M": 8, "R": 16}, {"L": 0, "M": 64, "R": 128}, id="three-bases"
    ),
)


def _snapshot(base_offsets: dict[str, int], producer: str | None) -> AbiSnapshot:
    record = RecordType(
        name="D",
        kind="struct",
        size_bits=256,
        alignment_bits=64,
        bases=list(base_offsets),
        base_offsets=dict(base_offsets),
    )
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=[
            Function(
                name="take",
                mangled="_Z4takeP1D",
                return_type="void",
                params=[Param(name="d", type="D *")],
                visibility=Visibility.PUBLIC,
            )
        ],
        types=[record],
        from_headers=producer is not None,
        ast_producer=producer,
    )


def _stored(
    base_offsets: dict[str, int], producer: str | None, extractor: int
) -> AbiSnapshot:
    doc = snapshot_to_dict(_snapshot(base_offsets, producer))
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 3, "source": 0}
    return snapshot_from_dict(doc)


def _fresh(base_offsets: dict[str, int], producer: str | None) -> AbiSnapshot:
    return snapshot_from_dict(snapshot_to_dict(_snapshot(base_offsets, producer)))


def _offsets(snap: AbiSnapshot) -> dict[str, int]:
    return next(t for t in snap.declarations.types if t.name == "D").base_offsets


@pytest.mark.parametrize("extractor", [1, 36, 37])
@pytest.mark.parametrize(("old_bytes", "bits"), _CASES)
def test_old_castxml_base_offsets_load_as_bits_and_compare_clean(
    old_bytes: dict[str, int], bits: dict[str, int], extractor: int
) -> None:
    baseline = _stored(old_bytes, "castxml", extractor)

    assert _offsets(baseline) == bits
    assert compare(baseline, _fresh(bits, "castxml")).changes == []
    migrated = old_bytes != bits
    assert (
        any("castxml base-class offset" in n for n in baseline.load_notices) is migrated
    )


def test_real_base_move_still_reported() -> None:
    """Control: B moving from byte 8 to byte 16 (old stored as 8 bytes, new as
    128 bits) is a real move, and still diffs after the rewrite."""
    baseline = _stored({"A": 0, "B": 8}, "castxml", 37)
    changes = compare(baseline, _fresh({"A": 0, "B": 128}, "castxml")).changes
    moved = [c for c in changes if c.kind is ChangeKind.BASE_CLASS_OFFSET_CHANGED]
    assert [(c.old_value, c.new_value) for c in moved] == [("64", "128")]


@pytest.mark.parametrize(
    ("producer", "extractor"),
    [
        pytest.param("castxml", 38, id="castxml-current"),
        pytest.param("clang", 37, id="clang-always-bits"),
        pytest.param("hybrid", 37, id="hybrid-mixed-units"),
        pytest.param(None, 37, id="dwarf-or-symbols"),
    ],
)
def test_offsets_kept_outside_old_castxml(producer: str | None, extractor: int) -> None:
    stored = _stored({"A": 0, "B": 64}, producer, extractor)
    assert _offsets(stored) == {"A": 0, "B": 64}
    assert not any("castxml base-class offset" in n for n in stored.load_notices)


@pytest.mark.parametrize("resaves", [1, 2, 3])
def test_resaved_migrated_baseline_is_not_scaled_again(resaves: int, tmp_path) -> None:
    """Re-saving keeps the old generation stamp, so the stamp alone would scale
    the offsets again on every load; the oracle is the declared offset,
    unchanged however often the baseline is re-saved."""
    snap = _stored({"A": 0, "B": 8}, "castxml", 37)
    for index in range(resaves):
        path = tmp_path / f"resave{index}.json"
        save_snapshot(snap, path)
        snap = load_snapshot(path)
    assert snap.fact_generations is not None and snap.fact_generations[0] == 37
    assert _offsets(snap) == {"A": 0, "B": 64}
    assert any("castxml base-class offset" in n for n in snap.load_notices)


def test_malformed_entries_pass_through_unchanged() -> None:
    from abicheck.storage.castxml_base_offset_migration import (
        migrate_castxml_base_offsets,
    )

    document = {
        "ast_producer": "castxml",
        "types": [
            "junk",
            {"name": "R"},
            {"name": "S", "base_offsets": None},
            {"name": "T", "base_offsets": {"A": "x", "B": True, "C": 2}},
        ],
    }
    migrated, notices = migrate_castxml_base_offsets(document, 37)

    assert migrated["types"][:3] == document["types"][:3]
    assert migrated["types"][3]["base_offsets"] == {"A": "x", "B": True, "C": 16}
    assert document["types"][3]["base_offsets"]["C"] == 2  # never mutated
    assert len(notices) == 1
    no_types = {"ast_producer": "castxml", "types": None}
    assert migrate_castxml_base_offsets(no_types, 37) == (no_types, [])
