# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""storage-format-v2 A1.5: shared build/source evidence is stored once.

Several libraries of one project usually embed the same ``BuildSourcePack``
(one ``--build-info`` capture for the whole build). A package stores each
section as a content-addressed object, so byte-identical ``build`` sections
must collapse to one object however many artifacts reference it -- and
genuinely different ones must not be merged. This states both directions as
a property over the number of libraries and the shape of the evidence, and
checks the round trip still hands every library its own pack back.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings, strategies as st

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.project_snapshot_store import DirectoryObjectStore
from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
from abicheck.storage.import_v1 import export_legacy_snapshot, import_legacy_snapshot

_SOURCES = st.lists(
    st.text(alphabet="abcdefgh/._", min_size=1, max_size=12), min_size=1, max_size=20
)


def _snapshot(library: str, sources: list[str]) -> AbiSnapshot:
    snap = AbiSnapshot(
        library=library,
        version="1",
        functions=[
            Function(
                name=library,
                mangled=library,
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    snap.build_source = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(
            compile_units=[
                CompileUnit(id=f"cu{i}", source=s) for i, s in enumerate(sources)
            ]
        ),
    )
    return snap


def _import(tmp: Path, snaps: list[AbiSnapshot]):
    store = DirectoryObjectStore(tmp)
    manifests = [
        import_legacy_snapshot(
            snapshot_to_dict(s),
            store=store,
            artifact_id=s.library,
            max_known_schema_version=SCHEMA_VERSION,
        )
        for s in snaps
    ]
    return store, [m.artifact_refs[0] for m in manifests]


@settings(max_examples=25, deadline=None)
@given(count=st.integers(2, 5), shared=_SOURCES)
def test_identical_build_evidence_is_one_object(
    tmp_path_factory, count, shared
) -> None:
    tmp = tmp_path_factory.mktemp("pkg")
    snaps = [_snapshot(f"lib{i}.so", shared) for i in range(count)]
    store, artifacts = _import(tmp, snaps)
    build_digests = {a.sections["build"].digest for a in artifacts}
    assert len(build_digests) == 1
    # Each library still has its own declarations: dedup is by content only.
    assert len({a.sections["declarations"].digest for a in artifacts}) == count
    # The shared object is readable back into every library's own pack.
    for artifact in artifacts:
        doc = export_legacy_snapshot(
            artifact, store=store, source_schema_version=SCHEMA_VERSION
        )
        assert len(doc["build_source"]["build_evidence"]["compile_units"]) == len(
            shared
        )


@settings(max_examples=25, deadline=None)
@given(a=_SOURCES, b=_SOURCES)
def test_different_build_evidence_is_never_merged(tmp_path_factory, a, b) -> None:
    tmp = tmp_path_factory.mktemp("pkg")
    _, artifacts = _import(tmp, [_snapshot("liba.so", a), _snapshot("libb.so", b)])
    same = (
        artifacts[0].sections["build"].digest == artifacts[1].sections["build"].digest
    )
    assert same == (a == b)
