# SPDX-License-Identifier: Apache-2.0
"""Storage plan A2.2: the snapshot write encodes one section at a time.

Oracle: the whole-document encoder the streamed write replaced
(``tests/_whole_document_encoder.py``), serialized by ``json.dumps`` itself
-- not by this module's own ``json_stream`` encoders. Checked byte for byte,
indented and compact, over Hypothesis-generated snapshots carrying D14/D15
carriers (record and header extensions, unknown and must-understand-blocked
sections, failed availability, document-level names), over every snapshot in
the fixture corpus, and for memory: the streamed write's peak follows the
largest section, not the document.
"""

from __future__ import annotations

import copy
import gc
import json
import tracemalloc
import warnings
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import load_snapshot, snapshot_from_dict, snapshot_to_json
from abicheck.storage.json_stream import (
    iter_json_compact,
    iter_json_indented,
    join_json_indented,
)
from abicheck.storage.section_states import record_section_failure
from abicheck.storage.snapshot_encode import (
    sectioned_document_for_write,
    streamed_sectioned_document,
)
from tests._whole_document_encoder import whole_sectioned_document

_FIXTURES = Path(__file__).parent / "fixtures"


def _assert_identical(snap: AbiSnapshot) -> None:
    oracle = whole_sectioned_document(copy.deepcopy(snap))
    assert join_json_indented(streamed_sectioned_document(snap), indent=2) == (
        json.dumps(oracle, indent=2)
    )
    assert "".join(iter_json_compact(streamed_sectioned_document(snap))) == (
        json.dumps(oracle, separators=(",", ":"))
    )
    assert snapshot_to_json(snap) == json.dumps(oracle, indent=2)
    # The eager form bundle members embed: same members, same order.
    eager = sectioned_document_for_write(snap)
    assert json.dumps(eager, indent=2) == json.dumps(oracle, indent=2)


# -- generated snapshots -------------------------------------------------------

_NAME = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True)


@st.composite
def _snapshots(draw: st.DrawFn) -> AbiSnapshot:
    names = draw(st.lists(_NAME, max_size=6, unique=True))
    functions = [
        Function(
            name=n,
            mangled=f"_Z{len(n)}{n}v",
            return_type=draw(st.sampled_from(["int", "void", "T*"])),
            params=[
                Param(name=p, type="int") for p in draw(st.lists(_NAME, max_size=3))
            ],
        )
        for n in names
    ]
    types = [
        RecordType(
            name=n.upper(),
            kind=draw(st.sampled_from(["struct", "class", "union"])),
            size_bits=draw(st.none() | st.integers(0, 512)),
            fields=[
                TypeField(name=f, type="int", offset_bits=i * 32)
                for i, f in enumerate(draw(st.lists(_NAME, max_size=3, unique=True)))
            ],
        )
        for n in draw(st.lists(_NAME, max_size=4, unique=True))
    ]
    enums = [
        EnumType(name=f"E{n}", members=[EnumMember(name=f"{n}0", value=0)])
        for n in draw(st.lists(_NAME, max_size=2, unique=True))
    ]
    variables = [
        Variable(name=n, mangled=n, type="int")
        for n in draw(st.lists(_NAME, max_size=3, unique=True))
    ]
    elf = None
    if draw(st.booleans()):
        elf = ElfMetadata(
            soname="libgen.so.1",
            symbols=[
                ElfSymbol(
                    name=f.mangled,
                    version=draw(st.sampled_from(["", "GEN_1.0"])),
                )
                for f in functions
            ],
        )
    return AbiSnapshot(
        library="libgen.so.1",
        version=draw(st.sampled_from(["1.0", "2.0-rc"])),
        functions=functions,
        variables=variables,
        types=types,
        enums=enums,
        elf=elf,
    )


_RECORD_LISTS = [
    ("declarations", "functions"),
    ("declarations", "variables"),
    ("declarations", "enums"),
    ("types", "types"),
]
_BLOCKABLE = ["binary", "debug", "declarations", "layout", "types"]
_JSONISH = st.recursive(
    st.none() | st.booleans() | st.integers(-5, 5) | _NAME,
    lambda c: st.lists(c, max_size=2) | st.dictionaries(_NAME, c, max_size=2),
    max_leaves=4,
)


def _carry(document: dict[str, Any], data: st.DataObject) -> dict[str, Any]:
    """Write D14/D15 carriers into a written document, as a newer writer
    would, so the load keeps them for the re-save under test."""
    sections = document["sections"]
    for section, field in _RECORD_LISTS:
        records = sections.get(section, {}).get("payload", {}).get(field) or []
        if records and data.draw(st.booleans(), label=f"extra {field}"):
            index = data.draw(st.integers(0, len(records) - 1))
            records[index]["x_" + data.draw(_NAME)] = data.draw(_JSONISH)
    present = sorted(set(sections) & set(_BLOCKABLE))
    if present and data.draw(st.booleans(), label="header extensions"):
        kind = data.draw(st.sampled_from(present))
        sections[kind]["extensions"] = {"vendor": data.draw(_JSONISH)}
    if data.draw(st.booleans(), label="unknown section"):
        sections["x_future"] = {
            "section_kind": "x_future",
            "section_schema_version": 3,
            "payload": data.draw(st.dictionaries(_NAME, _JSONISH, max_size=2)),
        }
        document["section_schema_versions"]["x_future"] = 3
    present = sorted(set(sections) & set(_BLOCKABLE))
    if present and data.draw(st.booleans(), label="blocked section"):
        kind = data.draw(st.sampled_from(present))
        sections[kind]["must_understand"] = ["x_layout_v2"]
    present = sorted(
        set(sections)
        & set(_BLOCKABLE) - {k for k in sections if "must_understand" in sections[k]}
    )
    if present and data.draw(st.booleans(), label="failed section"):
        kind = data.draw(st.sampled_from(present))
        sections.pop(kind)
        document["section_availability"][kind] = {
            "diagnostics": ["truncated"],
            "status": "failed",
        }
    if data.draw(st.booleans(), label="document must_understand"):
        # Only an understood name loads; an empty list is the falsy case.
        document["must_understand"] = []
    return document


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(snap=_snapshots(), data=st.data())
def test_streamed_write_is_byte_identical_over_generated_snapshots(
    snap: AbiSnapshot, data: st.DataObject
) -> None:
    _assert_identical(snap)
    document = _carry(json.loads(snapshot_to_json(snap)), data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        loaded = snapshot_from_dict(document)
    _assert_identical(loaded)


@pytest.mark.parametrize(
    "section", ["binary", "build", "debug", "declarations", "layout", "types"]
)
def test_producer_recorded_failure_is_byte_identical(section: str) -> None:
    snap = AbiSnapshot(
        library="libf.so",
        version="1",
        functions=[Function(name="f", mangled="f", return_type="int")],
    )
    record_section_failure(snap, section, ["unreadable"])
    _assert_identical(snap)


# -- the fixture corpus ---------------------------------------------------------


def _corpus() -> list[Path]:
    paths = []
    for path in sorted(_FIXTURES.rglob("*.json*")):
        if path.suffix not in (".json", ".gz"):
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                load_snapshot(path)
        except Exception:  # not a snapshot (reports, manifests, ...)
            continue
        paths.append(path)
    return paths


_CORPUS = _corpus()


def test_corpus_is_not_empty() -> None:
    assert len(_CORPUS) >= 20


@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: str(p.relative_to(_FIXTURES)))
def test_streamed_write_is_byte_identical_over_the_fixture_corpus(path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snap = load_snapshot(path)
    _assert_identical(snap)


# -- memory ---------------------------------------------------------------------


def _wide(n_functions: int, n_types: int) -> AbiSnapshot:
    return AbiSnapshot(
        library="libwide.so",
        version="1",
        functions=[
            Function(
                name=f"function_{i}",
                mangled=f"_Z10function_{i}i",
                return_type="int",
                params=[Param(name="a", type="int"), Param(name="b", type="long")],
            )
            for i in range(n_functions)
        ],
        types=[
            RecordType(
                name=f"Record{i}",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="x", type="int", offset_bits=0)],
            )
            for i in range(n_types)
        ],
    )


def _peak(build: Any) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        for _ in build():
            pass
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_streamed_write_peak_follows_the_largest_section() -> None:
    """Two equal halves (declarations, types): the whole-document encoder
    holds both trees, the streamed one only the larger. The streamed peak
    must stay well under the whole one and near a one-section snapshot's."""
    both = _wide(3000, 3000)
    one = _wide(3000, 0)

    def whole(s: AbiSnapshot) -> Any:
        return lambda: [whole_sectioned_document(s)]

    # The fragment stream, as the file write consumes it (never joined).
    def streamed_fragments(s: AbiSnapshot) -> Any:
        return lambda: iter_json_indented(streamed_sectioned_document(s), indent=2)

    peak_whole = _peak(whole(both))
    peak_streamed = _peak(streamed_fragments(both))
    peak_one_section = _peak(streamed_fragments(one))
    assert peak_streamed < 0.75 * peak_whole, (peak_streamed, peak_whole)
    assert peak_streamed < 1.5 * peak_one_section, (peak_streamed, peak_one_section)
