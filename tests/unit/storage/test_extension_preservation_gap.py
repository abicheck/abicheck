"""ADR-062 D14 -- recorded known gap, pinned as strict `xfail` probes.

D14: an unknown *optional* field or section is preserved opaquely and
written back unchanged; an unknown *must-understand* one blocks only the
analyses that depend on it. Neither holds yet (see
`docs/contribute/known-gaps.md`, "ADR-062 D14-D18 data-evolution rules are
partly implemented"). These probes state the target behavior over several
sibling record kinds; `strict=True` makes each one fail loudly the moment
the gap closes, so the gap entry cannot outlive the fix.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from abicheck.model import EnumMember, EnumType, Function, RecordType, Variable
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_json

_GAP = "ADR-062 D14 not implemented: see docs/contribute/known-gaps.md"


def _document() -> dict[str, Any]:
    snap = AbiSnapshot(
        library="libext.so.1",
        version="1.0",
        functions=[Function(name="f", mangled="f", return_type="int")],
        variables=[Variable(name="v", mangled="v", type="int")],
        types=[RecordType(name="T", kind="struct")],
        enums=[EnumType(name="E", members=[EnumMember(name="E0", value=0)])],
    )
    return json.loads(snapshot_to_json(snap))


def _records(
    document: dict[str, Any], section: str, field: str
) -> list[dict[str, Any]]:
    return document["sections"][section]["payload"][field]


@pytest.mark.xfail(strict=True, reason=_GAP)
@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("declarations", "functions"),
        ("declarations", "variables"),
        ("types", "types"),
        ("declarations", "enums"),
    ],
)
def test_an_unknown_optional_record_field_survives_a_re_save(
    section: str, field: str
) -> None:
    document = _document()
    if field not in document["sections"][section]["payload"]:
        pytest.fail(f"{field} is not in the {section} section of this build")
    _records(document, section, field)[0]["x_future_note"] = "kept"
    resaved = json.loads(snapshot_to_json(snapshot_from_dict(document)))
    assert _records(resaved, section, field)[0].get("x_future_note") == "kept"


@pytest.mark.xfail(strict=True, reason=_GAP, raises=ValueError)
def test_an_unknown_optional_section_does_not_reject_the_document() -> None:
    document = _document()
    document["sections"]["x_future"] = {
        "section_kind": "x_future",
        "section_schema_version": 1,
        "payload": {"note": "kept"},
    }
    document["section_schema_versions"]["x_future"] = 1
    resaved = json.loads(snapshot_to_json(snapshot_from_dict(document)))
    assert resaved["sections"]["x_future"]["payload"] == {"note": "kept"}
