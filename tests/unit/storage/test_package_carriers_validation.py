# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D14/D15 package carriers read from disk are untrusted input.

`carriers_for_reader` must refuse every malformed carrier object, and must
refuse -- never block-and-continue -- any carrier that would leave the
`provenance` section (the snapshot's identity) unusable. A well-formed
carrier object round-trips through `to_wire`/`from_wire` unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.errors import IncompatibleSnapshotSchemaError
from abicheck.storage.package import ArtifactRef
from abicheck.storage.wire_extensions import WireExtensions, carriers_for_reader

_WHERE = "pkg artifact 'a'"


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "carriers",
        7,
        {"unknown_key": 1},
        {"passthrough_sections": []},
        {"passthrough_versions": "x"},
        {"header_extras": 3},
        {"record_extras": [["functions", "functions", "f"]]},  # short row
        {"record_extras": [["functions", "functions", "f", "0", {}]]},  # ordinal
        {"record_extras": [["functions", 1, "f", 0, {}]]},  # non-str key
        {"record_extras": [["functions", "functions", "f", 0, []]]},  # tree
        {"record_extras": ["row"]},
        {"availability": {"types": {"status": "bogus"}}},
        {"availability": []},
        {"document_must_understand": "x_name"},
    ],
)
def test_malformed_carriers_are_refused(raw: Any) -> None:
    with pytest.raises((ValueError, TypeError)):
        carriers_for_reader(raw, _WHERE)


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (
            {
                "availability": {
                    "provenance": {"status": "failed", "diagnostics": ["x"]}
                }
            },
            ValueError,
        ),
        (
            {
                "passthrough_sections": {"provenance": {}},
                "passthrough_versions": {"provenance": 1},
            },
            IncompatibleSnapshotSchemaError,
        ),
        (
            {"header_extras": {"provenance": {"must_understand": ["x_future"]}}},
            IncompatibleSnapshotSchemaError,
        ),
        ({"document_must_understand": ["x_future"]}, Exception),
    ],
    ids=["failed", "kept-undecoded", "must-understand", "document-level"],
)
def test_a_carrier_that_disables_provenance_refuses(
    raw: dict[str, Any], error: type[BaseException]
) -> None:
    with pytest.raises(error):
        carriers_for_reader(raw, _WHERE)


@pytest.mark.parametrize("section", ["types", "declarations", "debug", "layout"])
def test_a_non_identity_carrier_blocks_only_its_section(section: str) -> None:
    for raw in (
        {"availability": {section: {"status": "failed", "diagnostics": ["boom"]}}},
        {"header_extras": {section: {"must_understand": ["x_future"]}}},
    ):
        ext = carriers_for_reader(raw, _WHERE)
        assert [kind for kind, _ in ext.unreadable] == [section]


def test_well_formed_carriers_round_trip() -> None:
    raw = {
        "availability": {"debug": {"status": "failed", "diagnostics": ["pdb"]}},
        "header_extras": {"types": {"extensions": {"x_note": 1}}},
        "record_extras": [["functions", "functions", "f", 0, {"x_k": "v"}]],
        "document_must_understand": [],
    }
    ext = WireExtensions.from_wire(raw)
    assert WireExtensions.from_wire(ext.to_wire()).to_wire() == ext.to_wire()


def test_artifact_ref_carriers_must_be_an_object_ref() -> None:
    with pytest.raises(TypeError, match="carriers"):
        ArtifactRef(artifact_id="a", variant_id="v", kind="elf", carriers="sha256:x")  # type: ignore[arg-type]
