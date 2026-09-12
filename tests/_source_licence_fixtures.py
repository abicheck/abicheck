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
    """
    snapshot.from_headers = True
    snapshot.live_source_evidence = True
    return snapshot
