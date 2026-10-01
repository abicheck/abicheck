# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The snapshot load/write fast paths produce exactly what the paths they
short-circuit produce.

Four shortcuts, each skipping work whose result was already known:

* the ``semantic_ir`` section is decoded once on load
  (`semantic_ir_codec.PredecodedSemanticIR`) instead of decoded, re-encoded
  and decoded again;
* an owned, already-canonical section payload is used as is
  (`section_payload.current_section_payload` under `canonical.owned_input`)
  instead of copied through `canonical_form`;
* a document whose text holds no closure/anonymous marker skips the
  load-time spelling normalization and renumbering walks
  (`closure_marking.json_text_may_hold_marker`), and a document that does
  hold one walks the snapshot once for both steps instead of twice;
* the write packages the ``semantic_ir`` encoding `snapshot_to_dict` just
  produced (`import_v1.legacy_section_dtos(semantic_ir_encoded_here=True)`)
  instead of decoding and re-encoding it.

The oracle for each is the slow path itself, reached by a route the
shortcut cannot touch: a flat document decoded by `snapshot_from_dict`
outside both load contexts (no pre-decoded IR, no owned payloads, no
marker proof), and the sectioned packaging with the IR decoded and
re-encoded. Snapshots are generated -- occurrence and conflict insertion
order, raw (pre-strip), stripped and ordinal closure markers, and
marker-free documents all vary -- so the claim is checked well beyond the
one large real snapshot the shortcuts were measured on.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.model import AbiSnapshot, RecordType
from abicheck.model.fact import Fact
from abicheck.model.identity import Namespace, entity_id_for_type
from abicheck.model.occurrence import OccurrenceId
from abicheck.model.semantic_ir import (
    CanonicalEntity,
    SemanticIR,
    semantic_ir_conflict_key,
)
from abicheck.serialization import (
    SCHEMA_VERSION,
    load_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
    write_snapshot,
)
from abicheck.storage.closure_identity import renumber_anonymous_closure_identities
from abicheck.storage.closure_marking import json_text_may_hold_marker
from abicheck.storage.sectioned_document import (
    from_sectioned_document,
    to_sectioned_document,
)
from abicheck.storage.snapshot_load_normalization import (
    normalize_and_renumber_closure_identities_on_load,
    normalize_anonymous_type_spellings_on_load,
)

#: Type-name spellings covering every state the load-time steps act on.
_NAME_SHAPES = st.sampled_from(
    [
        "Plain",
        "Wrapper<int>",
        "(lambda at /old/tree/a.h:{line}:7)",  # raw: stripped, then renumbered
        "(lambda:a.h:{line}:7)",  # stripped: renumbered
        "(lambda:a.h#{line})",  # ordinal: already final
        "(unnamed struct:b.h:{line}:3)",
        "Holder<(anonymous union:c.h:{line}:1)>",
    ]
)


@st.composite
def _snapshots(draw: st.DrawFn) -> AbiSnapshot:
    count = draw(st.integers(min_value=0, max_value=6))
    names: list[str] = []
    for index in range(count):
        shape = draw(_NAME_SHAPES)
        names.append(shape.format(line=draw(st.integers(1, 40))) + f"_{index}")
    types = [
        RecordType(name=name, kind="struct", qualified_name=f"ns::{name}", size_bits=8)
        for name in names
    ]
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    for name in draw(st.permutations(names)):
        occurrence = OccurrenceId(
            entity_id_for_type((Namespace("ns"),), name),
            disambiguator=draw(st.sampled_from(["", "tu-a", "tu-b"])),
        )
        occurrences[occurrence] = CanonicalEntity(
            canonical_spelling=Fact.present(f"ns::{name}")
        )
    conflicts: dict[str, str] = {}
    for occurrence in draw(st.permutations(list(occurrences))):
        if draw(st.booleans()):
            conflicts[semantic_ir_conflict_key(occurrence, "canonical_spelling")] = (
                repr(draw(st.text(max_size=6)))
            )
    has_ir = draw(st.booleans())
    return AbiSnapshot(
        library="libgen.so.1",
        version="1.0",
        types=types,
        semantic_ir=SemanticIR(occurrences=occurrences) if has_ir else None,
        semantic_ir_conflicts=conflicts if has_ir else {},
    )


def _dumped(snap: AbiSnapshot) -> str:
    """The snapshot's encoding plus the in-memory iteration order of its IR
    and conflict maps -- the encoder sorts both, so their order would
    otherwise be invisible here, and it is observable to every consumer
    that iterates them."""
    ir = snap.canonical_ir
    return json.dumps(
        [
            snapshot_to_dict(snap),
            [repr(key) for key in ir.occurrences] if ir is not None else None,
            list(snap.semantic_ir_conflicts or {}),
        ],
        default=str,
    )


def _reference_sectioned_text(snap: AbiSnapshot) -> str:
    """The write's slow path: the IR decoded and re-encoded."""
    return json.dumps(
        to_sectioned_document(
            snapshot_to_dict(snap),
            max_known_schema_version=SCHEMA_VERSION,
            semantic_ir_encoded_here=False,
        ),
        indent=2,
    )


def _reference_load(text: str) -> AbiSnapshot:
    """The load's slow path: a fully encoded flat document, decoded outside
    `load_snapshot`'s contexts -- no pre-decoded IR, every payload copied,
    every load-time walk run."""
    flat = from_sectioned_document(json.loads(text))
    return snapshot_from_dict(flat)


_SETTINGS = settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@_SETTINGS
@given(_snapshots())
def test_write_is_byte_identical_to_the_reencoding_packaging(
    tmp_path: Path, snap: AbiSnapshot
) -> None:
    path = tmp_path / "snap.json"
    expected = _reference_sectioned_text(snap)
    write_snapshot(snap, path, compression="none")
    assert path.read_text() == expected


@_SETTINGS
@given(_snapshots())
def test_load_matches_the_reference_decode(tmp_path: Path, snap: AbiSnapshot) -> None:
    path = tmp_path / "snap.json"
    write_snapshot(snap, path, compression="none")
    text = path.read_text()
    assert _dumped(load_snapshot(path)) == _dumped(_reference_load(text))


@_SETTINGS
@given(_snapshots())
def test_load_matches_the_reference_decode_of_a_legacy_unstripped_text(
    tmp_path: Path, snap: AbiSnapshot
) -> None:
    # A document as a pre-strip writer left it: the same snapshot, every
    # stripped closure marker re-expanded to its raw `at <path>` spelling.
    # The load must strip and renumber it exactly as the reference does.
    path = tmp_path / "legacy.json"
    write_snapshot(snap, path, compression="none")
    raw = path.read_text().replace("(lambda:a.h:", "(lambda at /legacy/a.h:")
    path.write_text(raw)
    assert _dumped(load_snapshot(path)) == _dumped(_reference_load(raw))


@given(
    st.text(max_size=12),
    st.sampled_from(["(lambda", "(unnamed", "(anonymous"]),
    st.text(max_size=12),
    st.booleans(),
)
def test_marker_scan_never_misses_a_marker_in_the_decoded_text(
    before: str, prefix: str, after: str, ensure_ascii: bool
) -> None:
    text = json.dumps({"name": before + prefix + after}, ensure_ascii=ensure_ascii)
    assert json_text_may_hold_marker(text)


@given(st.sampled_from(["(lambda", "(unnamed", "(anonymous"]), st.data())
def test_marker_scan_sees_through_json_ascii_escapes(prefix: str, data: Any) -> None:
    # JSON may spell any character as `\\u00XX`; a hand-edited document can
    # hide a marker prefix that way, so an escaped ASCII character must
    # count as "may hold a marker".
    escaped_at = data.draw(st.integers(0, len(prefix) - 1))
    spelled = (
        prefix[:escaped_at]
        + f"\\u{ord(prefix[escaped_at]):04x}"
        + prefix[escaped_at + 1 :]
    )
    text = '{"name": "' + spelled + ' at x.h:1:2)"}'
    assert json.loads(text)["name"].startswith(prefix)
    assert json_text_may_hold_marker(text)


def test_marker_scan_clears_a_marker_free_document() -> None:
    text = json.dumps({"name": "ns::Plain", "other": "café"})
    assert not json_text_may_hold_marker(text)


def test_snapshot_from_dict_never_aliases_a_callers_document(tmp_path: Path) -> None:
    # Only `load_snapshot` owns its document. A caller handing in its own
    # dict keeps it: mutating that dict afterwards must not reach the
    # snapshot, which would be the case if its payloads were kept uncopied.
    snap = AbiSnapshot(
        library="lib.so",
        version="1",
        types=[
            RecordType(name="R", kind="struct", qualified_name="ns::R", size_bits=8)
        ],
    )
    path = tmp_path / "snap.json"
    write_snapshot(snap, path, compression="none")
    document = json.loads(path.read_text())
    loaded = snapshot_from_dict(document)
    before = _dumped(loaded)

    def _scramble(value: Any) -> None:
        if isinstance(value, dict):
            for key in list(value):
                if isinstance(value[key], str):
                    value[key] = "MUTATED"
                else:
                    _scramble(value[key])
        elif isinstance(value, list):
            for item in value:
                _scramble(item)

    _scramble(document)
    assert _dumped(loaded) == before


@_SETTINGS
@given(_snapshots(), st.randoms(use_true_random=False))
def test_load_of_a_hand_reordered_ir_section_matches_the_reference_decode(
    tmp_path: Path, snap: AbiSnapshot, rng: Any
) -> None:
    # Our writer emits occurrences and conflicts sorted; a hand-edited (or
    # foreign) document need not. The single decode must still leave both
    # maps in the order the decode-encode-decode round trip left them.
    path = tmp_path / "snap.json"
    write_snapshot(snap, path, compression="none")
    document = json.loads(path.read_text())
    section = document["sections"].get("semantic_ir")
    if section is not None:
        payload = section["payload"]
        if "semantic_ir" in payload:
            rng.shuffle(payload["semantic_ir"]["occurrences"])
        conflicts = payload.get("semantic_ir_conflicts")
        if conflicts:
            items = list(conflicts.items())
            rng.shuffle(items)
            payload["semantic_ir_conflicts"] = dict(items)
    text = json.dumps(document, indent=2)
    path.write_text(text)
    assert _dumped(load_snapshot(path)) == _dumped(_reference_load(text))


@_SETTINGS
@given(_snapshots())
def test_fused_load_normalization_matches_the_two_separate_steps(
    snap: AbiSnapshot,
) -> None:
    # The fused step walks once where strip-then-renumber walked twice; on
    # any snapshot -- marker-free, raw (pre-strip), stripped or ordinal --
    # its result must be the two original steps' result.
    expected = copy.deepcopy(snap)
    normalize_anonymous_type_spellings_on_load(expected)
    renumber_anonymous_closure_identities(expected)
    normalize_and_renumber_closure_identities_on_load(snap)
    assert _dumped(snap) == _dumped(expected)
