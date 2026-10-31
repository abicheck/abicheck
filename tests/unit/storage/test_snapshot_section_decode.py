"""ADR-062 D18 decode split: each section decoder reads only its declared
sections, and the composed eager path is unchanged.

Oracles are independent of the step code under test: the section->field
partition of ``legacy_sections._SECTION_FIELDS`` (what a section *is*), an
unrestricted ``dict`` in place of every view (what the decode read before
the split), and the saved bytes/digests of a load (what users observe).
"""

from __future__ import annotations

import gzip
import json
import warnings
from pathlib import Path
from typing import Any

import pytest

from abicheck.serialization import load_snapshot, snapshot_from_dict, snapshot_to_json
from abicheck.storage import snapshot_codec
from abicheck.storage.evidence_digest import evidence_digest_of_document
from abicheck.storage.legacy_sections import (
    LEGACY_SECTION_KINDS,
    SCHEMA_VERSION_KEY,
    section_of_field,
)
from abicheck.storage.sectioned_document import (
    from_sectioned_document,
    is_sectioned_document,
)
from abicheck.storage.snapshot_encode import (
    sectioned_document_for_write,
    snapshot_content_digest,
    snapshot_to_dict,
)
from abicheck.storage.snapshot_section_decode import (
    DECODE_STEPS,
    SectionView,
    UndeclaredSectionRead,
    decode_binary_section,
    decode_build_section,
    decode_debug_section,
    decode_declarations_section,
    decode_layout_section,
    decode_provenance_section,
    decode_types_section,
)

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_CORPUS = sorted(
    [
        *_FIXTURES.glob("action/action_test_v*.json"),
        _FIXTURES / "contract_context_v1" / "old.json",
        _FIXTURES / "contract_context_v1" / "new.json",
        *_FIXTURES.glob("header_graph/*.json"),
        *_FIXTURES.glob("schema/v*.json"),
        *_FIXTURES.glob("toolchain_drift/*.json"),
        *_FIXTURES.glob("generations/gen*/corpus.json.gz"),
    ]
)

_SECTION_DECODERS = {
    "declarations": decode_declarations_section,
    "types": decode_types_section,
    "binary": decode_binary_section,
    "debug": decode_debug_section,
    "build": decode_build_section,
    "layout": decode_layout_section,
    "provenance": decode_provenance_section,
}


def _read(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    doc = json.loads(raw)
    assert isinstance(doc, dict)
    return doc


def _flat(doc: dict[str, Any]) -> dict[str, Any]:
    if is_sectioned_document(doc):
        return from_sectioned_document(doc)
    return doc


def _only(doc: dict[str, Any], section: str) -> dict[str, Any]:
    """*doc* with every other section's keys removed (oracle: the partition)."""
    return {
        k: v
        for k, v in doc.items()
        if k == SCHEMA_VERSION_KEY or section_of_field(k) == section
    }


def test_corpus_is_not_empty() -> None:
    assert len(_CORPUS) >= 20


def test_steps_are_ordered_by_their_dependencies() -> None:
    seen: set[str] = set()
    for step in DECODE_STEPS:
        assert set(step.after) <= seen, step
        seen.add(step.name)
    assert len(seen) == len(DECODE_STEPS)


def test_every_section_kind_is_read_by_some_step() -> None:
    read = set().union(*(step.reads for step in DECODE_STEPS))
    assert set(LEGACY_SECTION_KINDS) | {"semantic_ir"} <= read


def test_per_section_steps_are_the_decoders() -> None:
    single = {
        step.name for step in DECODE_STEPS if len(step.reads) == 1 and not step.after
    }
    assert set(_SECTION_DECODERS) <= single


@pytest.mark.parametrize("section", sorted(_SECTION_DECODERS))
@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: p.parent.name + "/" + p.name)
def test_section_decoder_needs_only_its_own_section(path: Path, section: str) -> None:
    doc = _flat(_read(path))
    sv = int(doc.get(SCHEMA_VERSION_KEY, 1))
    decode = _SECTION_DECODERS[section]
    full = decode(SectionView(doc, frozenset({section}), section), sv)
    alone = decode(SectionView(_only(doc, section), frozenset({section}), section), sv)
    assert alone == full


@pytest.mark.parametrize("section", sorted(_SECTION_DECODERS))
def test_reading_another_section_raises(section: str) -> None:
    other = next(s for s in LEGACY_SECTION_KINDS if s != section)
    key = next(
        k
        for k in (
            "functions",
            "types",
            "elf",
            "dwarf",
            "library",
            "contract",
            "build_source",
            "surface_graph",
        )
        if section_of_field(k) == other
    )
    view = SectionView({key: 1}, frozenset({section}), section)
    with pytest.raises(UndeclaredSectionRead):
        view.get(key)
    with pytest.raises(UndeclaredSectionRead):
        _ = key in view
    with pytest.raises(UndeclaredSectionRead):
        view[key]
    with pytest.raises(UndeclaredSectionRead):
        view.get("not_a_known_field")


def test_view_is_not_picklable() -> None:
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(SectionView({}, frozenset(), "x"))


def _observed(snap: Any) -> tuple[str, str, str]:
    return (
        snapshot_to_json(snap),
        snapshot_content_digest(snap),
        evidence_digest_of_document(snapshot_to_dict(snap)),
    )


@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: p.parent.name + "/" + p.name)
def test_split_decode_equals_unrestricted_decode(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Differential: the split path against the same steps fed the whole
    document (the pre-split decoder's input), flat and sectioned."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        split = load_snapshot(path)
        resaved = sectioned_document_for_write(split)
        split_sectioned = snapshot_from_dict(json.loads(json.dumps(resaved)))
        monkeypatch.setattr(snapshot_codec, "view_for", lambda d, _step: dict(d))
        whole = load_snapshot(path)
        whole_sectioned = snapshot_from_dict(json.loads(json.dumps(resaved)))
    assert split == whole
    assert split_sectioned == whole_sectioned
    assert _observed(split) == _observed(whole)
    assert _observed(split_sectioned) == _observed(whole_sectioned)
    # dump -> load -> save is a fixed point after the first save.
    assert snapshot_to_json(split_sectioned) == snapshot_to_json(split)
