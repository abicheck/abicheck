"""The stored-snapshot decode fast path must be output-identical to the slow
path it replaced.

Three redundant steps were removed from reading a sectioned document:
content-hashing every section into a throwaway store, rebuilding an
already-current ``SectionDTO`` in ``migrate_section_dto``, and
re-canonicalizing a payload the ``SectionDTO`` had already canonicalized.
The oracle here reconstructs that slow path independently -- a real
``InMemoryObjectStore`` round trip, a forced rebuild in migration, and no
trust scope -- and every document in the corpus must decode identically.
"""

from __future__ import annotations

import contextlib
import json
import random
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from abicheck.serialization import SCHEMA_VERSION, load_snapshot, snapshot_to_dict
from abicheck.storage import dto as dto_mod
from abicheck.storage.canonical import (
    canonical_form,
    canonical_form_unless_trusted,
    canonical_input_trusted,
)
from abicheck.storage.dto import SectionDTO, migrate_section_dto
from abicheck.storage.graph_section_codec import GraphSection
from abicheck.storage.import_v1 import export_legacy_snapshot
from abicheck.storage.package import ArtifactRef, InMemoryObjectStore, ObjectRef
from abicheck.storage.sectioned_document import (
    SECTIONS_KEY,
    from_sectioned_document,
    to_sectioned_document,
)
from abicheck.storage.types_section_codec import TypesSection
from tests.test_property_based import snapshot_st

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_CORPUS = sorted((_FIXTURES / "schema").glob("v*.json")) + sorted(
    (_FIXTURES / "action").glob("action_test_*.json")
)


def _slow_path(sectioned: dict[str, Any]) -> dict[str, Any]:
    """The pre-change decode, rebuilt from primitives: every section hashed
    into a real store and fetched back, no trust, forced migration rebuild."""
    real_migrate = dto_mod.migrate_section_dto

    def rebuilding_migrate(dto: SectionDTO) -> SectionDTO:
        current = real_migrate(dto)
        return SectionDTO(
            section_kind=current.section_kind,
            section_schema_version=current.section_schema_version,
            payload=current.payload,
        )

    store = InMemoryObjectStore()
    refs = {
        kind: ObjectRef(kind=kind, digest=store.put(raw))
        for kind, raw in sectioned[SECTIONS_KEY].items()
    }
    artifact = ArtifactRef(
        artifact_id="a", variant_id="default", kind="elf", sections=refs
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dto_mod, "migrate_section_dto", rebuilding_migrate)
        mp.setattr(dto_mod, "canonical_input_trusted", contextlib.nullcontext)
        return export_legacy_snapshot(
            artifact, store=store, source_schema_version=sectioned["schema_version"]
        )


def _sectioned(path: Path) -> dict[str, Any]:
    # Normalized through the real reader first: an old-schema fixture is not
    # itself splittable, but the snapshot it loads as is.
    return _sectioned_doc(snapshot_to_dict(load_snapshot(path)))


def _sectioned_doc(doc: dict[str, Any]) -> dict[str, Any]:
    doc.setdefault("schema_version", 1)
    # Through JSON text, exactly as a reader receives it.
    return json.loads(
        json.dumps(to_sectioned_document(doc, max_known_schema_version=SCHEMA_VERSION))
    )


@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: p.name)
def test_fast_path_decodes_identically_to_slow_path(path: Path) -> None:
    # Exact equality, key order and container types included -- not merely
    # canonical equality, which would hide a list/tuple leak.
    _assert_same(_sectioned(path))


def _assert_same(sectioned: dict[str, Any]) -> None:
    fast = from_sectioned_document(sectioned)
    slow = _slow_path(sectioned)
    assert json.dumps(fast) == json.dumps(slow)
    assert fast == slow


def test_corpus_is_not_vacuous() -> None:
    assert len(_CORPUS) >= 6
    kinds = set()
    for path in _CORPUS:
        kinds |= set(_sectioned(path)[SECTIONS_KEY])
    assert len(kinds) >= 4, kinds


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(snap=snapshot_st())
def test_fast_path_matches_slow_path_on_generated_snapshots(snap: Any) -> None:
    _assert_same(_sectioned_doc(snapshot_to_dict(snap)))


@pytest.mark.integration
def test_fast_path_matches_slow_path_on_a_real_header_graph_dump(
    tmp_path: Path,
) -> None:
    """The section that dominates a real stored baseline is ``graph``; only a
    real ``--depth headers`` dump produces one."""
    import shutil
    import subprocess
    import sys

    lib, header = (
        Path("/usr/lib/x86_64-linux-gnu/libz.so.1"),
        Path("/usr/include/zlib.h"),
    )
    if not (lib.exists() and header.exists() and shutil.which("castxml")):
        pytest.skip("needs libz, zlib.h and castxml")
    out = tmp_path / "z.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "dump",
            str(lib),
            "-H",
            str(header),
            "--depth",
            "headers",
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert "graph" in stored[SECTIONS_KEY]
    _assert_same(stored)


def _random_tree(rng: random.Random, depth: int = 0) -> Any:
    roll = rng.random()
    if depth > 3 or roll < 0.3:
        return rng.choice([None, True, 0, -7, 1.5, "x", "café", ""])
    if roll < 0.65:
        keys = rng.sample(["b", "a", "zz", "k1", "_", "M"], rng.randint(0, 5))
        return {k: _random_tree(rng, depth + 1) for k in keys}
    return [_random_tree(rng, depth + 1) for _ in range(rng.randint(0, 4))]


def test_trusted_codecs_match_untrusted_on_canonical_input() -> None:
    """Inside the trust scope the codec's frozen state must equal what the
    full canonicalization would have produced, for any canonical input."""
    rng = random.Random(20260923)
    for _ in range(300):
        graph = {"g": _random_tree(rng)}
        canonical = canonical_form(graph)
        reference = GraphSection(surface_graph=graph)
        with canonical_input_trusted():
            trusted = GraphSection(surface_graph=json.loads(json.dumps(canonical)))
        assert trusted == reference
        assert trusted.to_document() == reference.to_document()
        types = [_random_tree(rng) for _ in range(rng.randint(0, 3))]
        ref_types = TypesSection(types=types)
        with canonical_input_trusted():
            tr_types = TypesSection(types=json.loads(json.dumps(canonical_form(types))))
        assert tr_types == ref_types


def test_trusted_construction_does_not_alias_its_input() -> None:
    raw = {"surface_graph": {"nodes": [{"id": "n"}]}}
    with canonical_input_trusted():
        section = GraphSection.from_document(raw)
    raw["surface_graph"]["nodes"].append({"id": "m"})
    raw["surface_graph"]["nodes"][0]["id"] = "changed"
    assert section.to_document() == {"surface_graph": {"nodes": [{"id": "n"}]}}


def test_untrusted_still_canonicalizes_and_validates() -> None:
    assert list(canonical_form_unless_trusted({"b": 1, "a": 2})) == ["a", "b"]
    with pytest.raises(TypeError):
        canonical_form_unless_trusted({1: "non-str key"})


def test_trust_scope_is_restored_after_an_exception() -> None:
    with pytest.raises(RuntimeError), canonical_input_trusted():
        raise RuntimeError
    assert list(canonical_form_unless_trusted({"b": 1, "a": 2})) == ["a", "b"]


def test_migrate_returns_the_same_dto_when_already_current() -> None:
    dto = SectionDTO(
        section_kind="graph",
        section_schema_version=dto_mod.SECTION_SCHEMA_VERSIONS["graph"],
        payload={"surface_graph": {}},
    )
    assert migrate_section_dto(dto) is dto


def test_malformed_section_is_still_refused_on_the_fast_path() -> None:
    sectioned = _sectioned(_CORPUS[0])
    kind = next(iter(sectioned[SECTIONS_KEY]))
    sectioned[SECTIONS_KEY][kind]["section_kind"] = "not-" + kind
    with pytest.raises(ValueError, match="stores a SectionDTO for kind"):
        from_sectioned_document(sectioned)
