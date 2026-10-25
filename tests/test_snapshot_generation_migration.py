"""Extractor-generation stamping, drift notice and spelling migration for
single-file snapshots (ADR-062 D2).

Bug class ``storage.cross_generation_spelling_drift``: a snapshot dumped by an
older extractor generation, compared against a fresh dump of the *same*
source, must report no change -- the oracle is "the source is identical",
independent of the diff code -- and must carry a visible generation notice.
The field report was castxml's undeduced ``AutoType`` (generation < 6) versus
today's ``auto``; the tests below put that spelling at every position a type
spelling is stored, not only the reported return type.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _field_shapes import materialize

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
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.generation_migrations import (
    FACT_GENERATIONS_KEY,
    SPELLING_MIGRATIONS,
)
from abicheck.storage.versioning import (
    EXTRACTOR_GENERATION,
    GENERATION_DRIFT_NOTICE_MARKER,
)

_SPELLING_INTRODUCED_AT = 6  # castxml AutoType -> auto


def _func(name: str, ret: str = "int", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type=ret,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _fresh(position: str, spelling: str) -> AbiSnapshot:
    """A snapshot as today's extractor writes it, with *spelling* stored at
    *position*."""
    functions = [_func("anchor")]
    types: list[RecordType] = []
    variables: list[Variable] = []
    typedefs: dict[str, str] = {}
    if position == "return":
        functions.append(_func("set_x", ret=spelling))
    elif position == "param":
        functions.append(_func("take", params=(spelling,)))
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
    elif position == "typedef":
        typedefs["alias_t"] = spelling
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=functions,
        variables=variables,
        types=types,
        typedefs=typedefs,
    )


def _as_old_generation(doc: dict, stamp: dict | None) -> dict:
    """*doc* rewritten to the pre-generation-6 spelling, stamped *stamp*
    (``None``: the key absent, as every pre-stamp snapshot)."""
    old = json.loads(re.sub(r"\bauto\b", "AutoType", json.dumps(doc)))
    old.pop(FACT_GENERATIONS_KEY, None)
    if stamp is not None:
        old[FACT_GENERATIONS_KEY] = stamp
    return old


_POSITIONS = ("return", "param", "field", "variable", "typedef")
_SPELLINGS = ("auto", "auto&", "const auto*", "Box<auto>", "Box<Box<auto&> >")
_OLD_STAMPS = (
    pytest.param(None, id="unstated"),
    pytest.param({"extractor": 5, "resolver": 2, "source": 0}, id="gen5"),
    pytest.param({"extractor": 1, "resolver": 1, "source": 0}, id="gen1"),
)


@pytest.mark.parametrize("stamp", _OLD_STAMPS)
@pytest.mark.parametrize("spelling", _SPELLINGS)
@pytest.mark.parametrize("position", _POSITIONS)
def test_old_generation_spelling_compares_clean_with_notice(
    position: str, spelling: str, stamp: dict | None
) -> None:
    fresh = _fresh(position, spelling)
    old = snapshot_from_dict(_as_old_generation(snapshot_to_dict(fresh), stamp))
    reloaded_fresh = snapshot_from_dict(snapshot_to_dict(fresh))

    result = compare(old, reloaded_fresh)

    assert result.changes == [], [c.kind for c in result.changes]
    assert any(GENERATION_DRIFT_NOTICE_MARKER in w for w in result.coverage_warnings)
    assert any("'AutoType' -> 'auto'" in n for n in old.load_notices)


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_current_generation_spelling_is_not_migrated(spelling: str) -> None:
    """The migration is keyed by generation: a document stamped at or after
    the generation that changed the spelling keeps what it says. This is the
    control showing the oracle above would catch a missing migration."""
    fresh = _fresh("return", spelling)
    stamp = {"extractor": EXTRACTOR_GENERATION, "resolver": 2, "source": 0}
    old = snapshot_from_dict(_as_old_generation(snapshot_to_dict(fresh), stamp))
    result = compare(old, snapshot_from_dict(snapshot_to_dict(fresh)))
    assert result.changes, "an unmigrated AutoType spelling must still diff"
    assert not old.load_notices


def test_same_generation_snapshot_has_no_drift_notice() -> None:
    fresh = _fresh("return", "auto&")
    doc = snapshot_to_dict(fresh)
    assert doc[FACT_GENERATIONS_KEY]["extractor"] == EXTRACTOR_GENERATION
    loaded = snapshot_from_dict(doc)
    assert loaded.load_notices == ()
    result = compare(loaded, snapshot_from_dict(doc))
    assert not any(
        GENERATION_DRIFT_NOTICE_MARKER in w for w in result.coverage_warnings
    )


@pytest.mark.parametrize("stamp", _OLD_STAMPS)
def test_resave_never_redates_the_stamp(stamp: dict | None) -> None:
    doc = _as_old_generation(snapshot_to_dict(_fresh("return", "auto")), stamp)
    resaved = snapshot_to_dict(snapshot_from_dict(doc))
    expected = stamp["extractor"] if stamp else 0
    assert resaved[FACT_GENERATIONS_KEY]["extractor"] == expected


def test_user_type_named_like_old_placeholder_is_not_rewritten() -> None:
    snap = AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=[_func("make", ret="AutoType")],
        types=[RecordType(name="AutoType", kind="struct", size_bits=8)],
    )
    doc = snapshot_to_dict(snap)
    doc.pop(FACT_GENERATIONS_KEY)
    loaded = snapshot_from_dict(doc)
    assert any(f.return_type == "AutoType" for f in loaded.declarations.functions)
    assert any("skipped" in n for n in loaded.load_notices)


def test_every_extractor_generation_has_a_spelling_record() -> None:
    """Bumping EXTRACTOR_GENERATION must add a SPELLING_MIGRATIONS row: either
    the rewrites the bump introduced or the reason it introduced none."""
    assert set(SPELLING_MIGRATIONS) == set(range(1, EXTRACTOR_GENERATION + 1))
    for generation, change in SPELLING_MIGRATIONS.items():
        assert change.reason.strip(), generation
        for old, new in change.rewrites:
            assert old and new and old != new, generation
    assert ("AutoType", "auto") in SPELLING_MIGRATIONS[_SPELLING_INTRODUCED_AT].rewrites


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
@pytest.mark.parametrize("stamp", _OLD_STAMPS)
def test_real_castxml_dump_old_generation_compares_clean(
    tmp_path: Path, stamp: dict | None
) -> None:
    """The field-report repro: a real castxml dump of ``auto& set_x();``
    rewritten to the generation-5 spelling, against a fresh dump."""
    from abicheck.dumper import dump

    # Sources: the field-shapes corpus copy of this repro (one owner).
    materialize("auto_return_setters", "v1", tmp_path)
    so = tmp_path / "libx.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(tmp_path / "api.cpp"),
        ],
        check=True,
    )
    fresh_doc = snapshot_to_dict(dump(so, [tmp_path / "api.h"]))
    assert "auto&" in json.dumps(fresh_doc)
    old = snapshot_from_dict(_as_old_generation(fresh_doc, stamp))
    result = compare(old, snapshot_from_dict(fresh_doc))
    kinds = {str(c.kind.value) for c in result.changes}
    assert "func_return_changed" not in kinds
    assert "unspecified_return_now_named" not in kinds
    assert result.changes == [], kinds
    assert any(GENERATION_DRIFT_NOTICE_MARKER in w for w in result.coverage_warnings)


@pytest.mark.parametrize("stamp", _OLD_STAMPS)
def test_sectioned_on_disk_round_trip_keeps_the_stamp(stamp: dict | None) -> None:
    from abicheck.serialization import snapshot_to_json

    doc = _as_old_generation(snapshot_to_dict(_fresh("return", "auto")), stamp)
    reloaded = snapshot_from_dict(json.loads(snapshot_to_json(snapshot_from_dict(doc))))
    expected = stamp["extractor"] if stamp else 0
    assert reloaded.fact_generations is not None
    assert reloaded.fact_generations[0] == expected
