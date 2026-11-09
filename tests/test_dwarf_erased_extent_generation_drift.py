"""A pre-44 DWARF baseline's ``T[]`` against a fresh ``T[N]`` is drift, not a
break (bug class ``storage.cross_generation_spelling_drift``).

Generation 44 started spelling DWARF array bounds. A stored DWARF-only
baseline still says ``char[]`` for ``char tag[8]``, and a stored ``T[]``
cannot say which bound it erased, so no load-time rewrite is sound:
comparison demotes the finding to ``COMPATIBLE_WITH_RISK`` and keeps it
visible (ADR-067), only when the stale side is the DWARF-sourced older one.

Oracle: whether a spelling pair is "the newer one with its bounds erased" is
decided here by an explicit list of hand-written pairs, not by the matcher.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model import AbiSnapshot, RecordType, TypeField
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.model.dwarf_facts import DwarfMetadata
from abicheck.model.identity import entity_id_for_type
from abicheck.policy.generation_drift_demotion import (
    GENERATION_DRIFT_RULE,
    is_erased_extent_drift,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.generation_migrations import (
    FACT_GENERATIONS_KEY,
    erased_extents_restored_between,
)


@pytest.mark.parametrize(
    ("stale", "live", "expected"),
    [
        ("char[]", "char[8]", True),
        ("int[]", "int[2][3]", True),
        ("P[]", "P[3]", True),
        ("int[] *", "int[4] *", True),
        ("char[]", "char[]", False),  # identical: nothing to demote
        ("char[]", "int[8]", False),  # the element changed too
        ("char[6]", "char[8]", False),  # a real bound change
        ("char[8]", "char[]", False),  # the stale side kept a bound
        ("char", "char[8]", False),  # scalar became an array
        # An array DIE nested in another wrote one ``[]`` per DIE.
        ("int[][]", "int[2][3]", True),
        ("int[][]", "int[2][3][4]", True),
        ("int[][]", "int[8]", False),  # more erased dims than live ones
        ("int[][3]", "int[2][3]", False),  # the stale side kept a bound
        ("int[] *[]", "int[2] *[3]", True),  # each run erased on its own
        ("int[] *", "int[2] *[3]", False),  # a run appeared
    ],
)
def test_erased_extent_drift_is_exactly_the_bound_erasure(
    stale: str, live: str, expected: bool
) -> None:
    assert is_erased_extent_drift(stale, live) is expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(43, 44, True), (44, 43, True), (0, 50, True), (44, 44, False), (42, 43, False)],
)
def test_the_generation_44_row_is_what_declares_it(
    a: int, b: int, expected: bool
) -> None:
    assert erased_extents_restored_between(a, b) is expected


#: The producer each debug source stamps on its canonical entities; the
#: debug block itself is shared storage and names none of them.
_PRODUCER = {"dwarf": "dwarf", "pdb": "pdb", "btf": "btf", "ctf": "ctf"}


def _snapshot(field_type: str, *, source: str = "dwarf") -> AbiSnapshot:
    """A one-record snapshot whose types came from *source*: ``dwarf``,
    ``pdb``, ``btf`` or ``ctf`` (each writes the same debug block, with
    ``has_dwarf=True``), ``headers`` (castxml), ``unknown`` (a debug block
    but no producer recorded) or ``symbols`` (no debug info at all)."""
    record = RecordType(
        name="rec",
        kind="struct",
        size_bits=128,
        fields=[
            TypeField(name="id", type="int", offset_bits=0),
            TypeField(name="tag", type=field_type, offset_bits=32),
        ],
        entity_id=entity_id_for_type((), "rec"),
    )
    snap = AbiSnapshot(
        library="libx.so.1",
        version="1",
        types=[record],
        dwarf=DwarfMetadata(has_dwarf=source != "symbols"),
        platform="pe" if source == "pdb" else "elf",
    )
    snap.from_headers = source == "headers"
    producer = "castxml" if source == "headers" else _PRODUCER.get(source)
    if producer is not None:
        snap.semantic_ir = normalize_header_ast(
            types=[record],
            enums=[],
            typedefs_qualified={},
            typedef_entity_ids={},
            producer=producer,
        )
    return snap


def _stamped(snapshot: AbiSnapshot, extractor: int) -> AbiSnapshot:
    doc = snapshot_to_dict(snapshot)
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 2, "source": 0}
    loaded = snapshot_from_dict(doc)
    loaded.from_headers = snapshot.from_headers
    return loaded


def _field_finding(result: object):  # type: ignore[no-untyped-def]
    hits = [
        c
        for c in result.changes  # type: ignore[attr-defined]
        if c.kind is ChangeKind.TYPE_FIELD_TYPE_CHANGED
    ]
    assert len(hits) == 1, [c.kind for c in result.changes]  # type: ignore[attr-defined]
    return hits[0]


@pytest.mark.parametrize("live", ["char[8]", "char[2][4]"])
def test_a_pre_44_dwarf_baseline_reads_the_erasure_as_drift(live: str) -> None:
    result = compare(_stamped(_snapshot("char[]"), 43), _snapshot(live))
    finding = _field_finding(result)
    assert finding.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
    assert finding.modulation_rule == GENERATION_DRIFT_RULE
    assert "bounded array" in finding.modulation_reason
    assert (finding.old_value, finding.new_value) == ("char[]", live)
    assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_the_newer_operand_on_the_old_side_is_read_the_same_way() -> None:
    result = compare(_snapshot("char[8]"), _stamped(_snapshot("char[]"), 43))
    assert _field_finding(result).modulation_rule == GENERATION_DRIFT_RULE


@pytest.mark.parametrize(
    ("stale_generation", "source"),
    [
        (44, "dwarf"),
        (43, "headers"),
        (43, "pdb"),
        (43, "btf"),
        (43, "ctf"),
        (43, "unknown"),
        (43, "symbols"),
    ],
    ids=[
        "same_generation",
        "header_sourced_stale_side",
        "pdb_sourced_stale_side",
        "btf_sourced_stale_side",
        "ctf_sourced_stale_side",
        "unknown_producer_stale_side",
        "no_debug_info_stale_side",
    ],
)
def test_no_demotion_without_an_older_dwarf_sourced_side(
    stale_generation: int, source: str
) -> None:
    old = _stamped(_snapshot("char[]", source=source), stale_generation)
    result = compare(old, _snapshot("char[8]"))
    assert _field_finding(result).modulation_rule != GENERATION_DRIFT_RULE
    assert result.verdict is Verdict.BREAKING


def test_a_real_element_change_across_generations_stays_breaking() -> None:
    result = compare(_stamped(_snapshot("char[]"), 43), _snapshot("int[8]"))
    assert _field_finding(result).modulation_rule != GENERATION_DRIFT_RULE
    assert result.verdict is Verdict.BREAKING
