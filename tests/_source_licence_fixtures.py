# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for the two source-evidence bug-class suites.

`test_stored_snapshot_source_licence.py` owns
`evidence.stored_snapshot_rederivation`; `test_source_input_completeness.py`
owns `coverage.discovery_derived_completeness`. They were one file until it
reached the architecture gate's test-size cap; the split follows the bug-class
boundary rather than cutting the file at an arbitrary line, and these helpers
are what both sides need.

Deliberately not a `conftest.py` fixture set: these are plain constructors, and
a caller reads better saying `_snapshot_recording([header])` than requesting a
fixture whose shape it then has to mutate.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.serialization import load_snapshot, write_snapshot

#: Two independent escalating constructs, so a test can assert on one kind
#: while varying the other -- a single-kind fixture cannot tell "the fold
#: refused this finding" apart from "the scan produced nothing at all".
PACKED_SOURCE = "#pragma pack(push, 1)\nstruct S { int a; };\n#pragma pack(pop)\n"
TEMPLATE_SOURCE = "template class Widget<int>;\n"


def write_tree(root: Path, contents: dict[str, str]) -> list[str]:
    paths = []
    for name, text in contents.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        paths.append(str(target))
    return paths


def snapshot_recording(headers: list[str], *, version: str = "1.0") -> AbiSnapshot:
    """A snapshot whose declarations record *headers* as their provenance."""
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z1f{i}v",
                return_type="void",
                source_header=h,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
            for i, h in enumerate(headers)
        ],
    )


def stored(snapshot: AbiSnapshot, tmp_path: Path, name: str) -> AbiSnapshot:
    """Round-trip *snapshot* through the real storage codec, as a baseline is."""
    out = tmp_path / name
    write_snapshot(snapshot, out)
    return load_snapshot(out)


def live(snapshot: AbiSnapshot) -> AbiSnapshot:
    """Mark *snapshot* as a header-derived live extraction of this run.

    Both halves matter: `live_source_evidence` is the licence, and
    `from_headers` is what makes granting it legitimate (see
    `extraction_read_source_inputs` -- a DWARF-only extraction records paths it
    never opened).

    An embedded build pack is stamped too, since that is a *second*,
    independently-licensed evidence source (`build_evidence_licence`): a
    harness saying "this whole side was collected live in this run" has to say
    it for both, and one that means only the headers should not use this helper
    for the pack.
    """
    snapshot.from_headers = True
    snapshot.live_source_evidence = True
    if snapshot.build_source is not None:
        snapshot.build_source.live_source_evidence = True
    return snapshot


def with_build_evidence(
    snapshot: AbiSnapshot, sources: list[str], *, live_pack: bool = False
) -> AbiSnapshot:
    """Attach an embedded L3 pack recording *sources* as its compile units.

    *live_pack* is the pack's own source-read licence
    (`BuildSourcePack.live_source_evidence`) -- the second, independently
    provenanced evidence source. Default `False`, matching every pack that came
    off disk: a pre-captured `--build-info` directory, or a stored snapshot's
    embedded payload.
    """
    snapshot.build_source = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(
            compile_units=[
                CompileUnit(id=f"cu{i}", source=s) for i, s in enumerate(sources)
            ]
        ),
        live_source_evidence=live_pack,
    )
    return snapshot
