"""Generation-15 castxml array-count migration (bug class
``storage.cross_generation_spelling_drift``).

Before generation 15 the castxml backend spelled ``T a[N]`` as ``T[N-1]``
(castxml's ``max`` is the last index). A castxml header baseline dumped
earlier, compared against a fresh dump of the *same* source, must report no
change. The oracle is the source itself: each case states the declared
element counts and the old spelling castxml wrote for them, by hand, at every
position a type spelling is stored.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.policy.generation_drift_demotion import GENERATION_DRIFT_RULE
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.generation_migrations import FACT_GENERATIONS_KEY

# (fresh spelling of the declared type, what castxml wrote before generation 15)
_CASES = (
    pytest.param("double[2]", "double[1]", id="one-dim"),
    pytest.param("int[1]", "int[0]", id="single-element"),
    pytest.param("char[10][3]", "char[9][2]", id="two-dim"),
    pytest.param("const float*[8]", "const float*[7]", id="pointer-elements"),
    pytest.param("Box<int[3]>[4]", "Box<int[3]>[3]", id="template-arg-kept"),
    pytest.param("Box<int[3]>", "Box<int[3]>", id="only-template-arg"),
    pytest.param("unsigned char[]", "unsigned char[]", id="unbounded"),
)
_POSITIONS = ("return", "param", "field", "variable", "typedef")


def _snapshot(position: str, spelling: str, producer: str | None) -> AbiSnapshot:
    functions = [
        Function(
            name="anchor",
            mangled="_Z6anchorv",
            return_type="int",
            visibility=Visibility.PUBLIC,
        )
    ]
    types: list[RecordType] = []
    variables: list[Variable] = []
    typedefs: dict[str, str] = {}
    if position == "return":
        functions.append(
            Function(
                name="get",
                mangled="_Z3getv",
                return_type=spelling,
                visibility=Visibility.PUBLIC,
            )
        )
    elif position == "param":
        functions.append(
            Function(
                name="take",
                mangled="_Z4takev",
                return_type="void",
                params=[Param(name="p", type=spelling)],
                visibility=Visibility.PUBLIC,
            )
        )
    elif position == "field":
        types.append(
            RecordType(
                name="S",
                kind="struct",
                size_bits=64,
                fields=[TypeField("f", spelling, offset_bits=0)],
            )
        )
    elif position == "variable":
        variables.append(
            Variable(name="g", mangled="g", type=spelling, visibility=Visibility.PUBLIC)
        )
    else:
        typedefs["alias_t"] = spelling
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=functions,
        variables=variables,
        types=types,
        typedefs=typedefs,
        from_headers=producer is not None,
        ast_producer=producer,
    )


def _stored(
    position: str, spelling: str, producer: str | None, extractor: int
) -> AbiSnapshot:
    doc = snapshot_to_dict(_snapshot(position, spelling, producer))
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 2, "source": 0}
    return snapshot_from_dict(doc)


@pytest.mark.parametrize("extractor", [1, 13, 14])
@pytest.mark.parametrize(("fresh", "old"), _CASES)
@pytest.mark.parametrize("position", _POSITIONS)
def test_old_castxml_array_spelling_compares_clean(
    position: str, fresh: str, old: str, extractor: int
) -> None:
    baseline = _stored(position, old, "castxml", extractor)
    current = snapshot_from_dict(
        snapshot_to_dict(_snapshot(position, fresh, "castxml"))
    )

    result = compare(baseline, current)

    assert result.changes == [], [
        (c.kind.value, c.old_value, c.new_value) for c in result.changes
    ]
    migrated = fresh != old
    assert any("castxml array spelling" in n for n in baseline.load_notices) is migrated


@pytest.mark.parametrize("position", ["return", "param", "variable"])
def test_real_array_resize_still_reported(position: str) -> None:
    """Control: the migration reads the old count as N+1, so a genuine
    double[2] -> double[3] resize (old stored as double[1]) still diffs. Only
    the positions this synthetic snapshot reports at all (an unexported
    record or typedef is outside the compared surface)."""
    baseline = _stored(position, "double[1]", "castxml", 14)
    current = snapshot_from_dict(
        snapshot_to_dict(_snapshot(position, "double[3]", "castxml"))
    )
    changes = compare(baseline, current).changes
    assert changes
    # The load-time rewrite consumed generation 15's extent shift, so the
    # comparison's drift demotion must not lower this real resize.
    assert all(c.modulation_rule != GENERATION_DRIFT_RULE for c in changes)


@pytest.mark.parametrize("position", ["return", "param", "variable"])
@pytest.mark.parametrize("producer", ["hybrid", None])
def test_unmigrated_extent_shift_still_demoted(
    position: str, producer: str | None
) -> None:
    """Fallback: a snapshot the load rewrite does not touch (no castxml
    provenance) keeps the comparison's extent-shift demotion."""
    baseline = _stored(position, "double[1]", producer, 14)
    current = snapshot_from_dict(
        snapshot_to_dict(_snapshot(position, "double[2]", producer))
    )
    changes = compare(baseline, current).changes
    assert changes
    assert all(c.modulation_rule == GENERATION_DRIFT_RULE for c in changes)


@pytest.mark.parametrize(
    ("producer", "extractor"),
    [
        pytest.param("castxml", 15, id="castxml-current"),
        pytest.param("clang", 14, id="clang-spelled-counts"),
        pytest.param("hybrid", 14, id="hybrid-per-fact"),
        pytest.param(None, 14, id="dwarf-or-symbols"),
    ],
)
@pytest.mark.parametrize("position", _POSITIONS)
def test_spelling_kept_outside_old_castxml(
    position: str, producer: str | None, extractor: int
) -> None:
    stored = _stored(position, "double[2]", producer, extractor)
    assert (
        compare(
            stored,
            snapshot_from_dict(
                snapshot_to_dict(_snapshot(position, "double[2]", producer))
            ),
        ).changes
        == []
    )
    assert not any("castxml array spelling" in n for n in stored.load_notices)


@pytest.mark.parametrize("resaves", [1, 2, 3])
@pytest.mark.parametrize(("fresh", "old"), _CASES)
def test_resaved_migrated_baseline_is_not_shifted_again(
    fresh: str, old: str, resaves: int, tmp_path
) -> None:
    """Re-saving keeps the old generation stamp (it is never re-dated), so the
    stamp alone would migrate the counts again on every load; the oracle is
    the declared count, unchanged however often the baseline is re-saved."""
    from abicheck.serialization import load_snapshot, save_snapshot

    snap = _stored("field", old, "castxml", 13)
    for index in range(resaves):
        path = tmp_path / f"resave{index}.json"
        save_snapshot(snap, path)
        snap = load_snapshot(path)
    assert snap.fact_generations is not None and snap.fact_generations[0] == 13
    assert [f.type for t in snap.declarations.types for f in t.fields] == [fresh]
    # The notice stays on every load: the document still holds migrated data.
    assert any("castxml array spelling" in n for n in snap.load_notices) is (
        fresh != old
    )


def test_malformed_entries_pass_through_unchanged() -> None:
    """A hand-edited or partial document: non-mapping entries, missing lists,
    non-string spellings and a non-mapping typedef table are kept verbatim;
    only the well-formed spellings are respelled and counted."""
    from abicheck.storage.castxml_array_migration import migrate_castxml_array_counts

    document = {
        "ast_producer": "castxml",
        "functions": [
            "junk",
            {"name": "f", "return_type": 3},
            {"name": "g", "params": None},
        ],
        "variables": [None, {"name": "v", "type": "int[3]"}],
        "types": [
            "junk",
            {"name": "R"},
            {"name": "S", "fields": [{"name": "x", "type": None}]},
        ],
        "typedefs": {"a_t": 7, "b_t": "char[0]"},
        "typedefs_qualified": ["not", "a", "mapping"],
    }
    migrated, notices = migrate_castxml_array_counts(document, 14)

    assert migrated["functions"] is document["functions"]
    assert migrated["types"] is document["types"]
    assert migrated["variables"] == [None, {"name": "v", "type": "int[4]"}]
    assert migrated["typedefs"] == {"a_t": 7, "b_t": "char[1]"}
    assert migrated["typedefs_qualified"] == ["not", "a", "mapping"]
    assert document["variables"][1]["type"] == "int[3]"  # never mutated in place
    assert len(notices) == 1


def test_nothing_to_migrate_returns_the_same_document() -> None:
    from abicheck.storage.castxml_array_migration import migrate_castxml_array_counts

    document = {
        "ast_producer": "castxml",
        "functions": [{"name": "f", "return_type": "int"}],
    }
    assert migrate_castxml_array_counts(document, 14) == (document, [])
    assert migrate_castxml_array_counts(document, 14)[0] is document
