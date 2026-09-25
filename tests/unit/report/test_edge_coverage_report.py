"""The report's "Relationship coverage" surface (evidence-entity-model
Phase 4, I4): JSON ``edge_coverage``, and the Markdown/HTML section built by
the ``compute_edge_coverage_section`` / ``render_edge_coverage_*`` pair.

Driven through the public workflow -- ``checker.compare`` and the real
renderers -- over live snapshots, with the expected rows stated by hand.
"""

from __future__ import annotations

import json

from abicheck import reporter
from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.html_report import generate_html_report
from abicheck.model import AbiSnapshot, Function, RecordType
from abicheck.model.dwarf_facts import DwarfMetadata, StructLayout
from abicheck.report.edge_coverage_section import (
    compute_edge_coverage_section,
    edge_coverage_section_from_mapping,
)
from abicheck.schemas import REPORT_SCHEMA_VERSION, load_compare_report_schema
from tests.schema_validation import validate_instance


def _snap(version, *, elf, from_headers=True, types=(), dwarf=None):
    return AbiSnapshot(
        library="libx.so",
        version=version,
        functions=[
            Function(name="f", mangled="f", return_type="int", source_header="x.h")
        ],
        types=list(types),
        elf=elf,
        dwarf=dwarf,
        from_headers=from_headers,
        dependency_scope="filtered",
    )


def _read(*names):
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names], machine="EM_X86_64")


def _full_pair():
    types = [RecordType(name="S", kind="struct", size_bits=64, source_header="x.h")]
    dwarf = DwarfMetadata(
        has_dwarf=True, structs={"S": StructLayout(name="S", byte_size=8)}
    )
    old = _snap("1", elf=_read("f", "g"), types=types, dwarf=dwarf)
    new = _snap("2", elf=_read("f"), types=types, dwarf=dwarf)
    return old, new


def _degraded_pair():
    # NEW: a never-read export table, no header AST, a stripped binary.
    types = [RecordType(name="S", kind="struct", size_bits=64, source_header="x.h")]
    old = _snap("1", elf=_read("f"), types=types)
    new = _snap("2", elf=ElfMetadata(), from_headers=False, types=types)
    return old, new


def test_schema_version_is_bumped_for_edge_coverage():
    assert REPORT_SCHEMA_VERSION == "5.4"


def test_json_carries_both_sides_and_validates():
    result = compare(*_degraded_pair())
    payload = json.loads(reporter.to_json(result))
    validate_instance(payload, load_compare_report_schema())
    cov = payload["edge_coverage"]
    assert set(cov) == {"old", "new"}
    new_exports = cov["new"]["exports"]
    assert [(r["producer"], r["run"], r["reason"]) for r in new_exports["records"]] == [
        ("export_table", "failed", "export_table_not_read"),
        ("header_ast", "not_run", "no_header_ast"),
    ]
    assert new_exports["answers"]["declarations"] == {
        "present": 0,
        "proven_absent": 0,
        "unknown": 1,
    }
    assert new_exports["absence"] == "unknown"
    assert cov["old"]["exports"]["absence"] == "proven_absent"
    assert cov["new"]["debug_type_of"]["answers"]["header_types"]["unknown"] == 1


def test_no_baseline_side_is_null():
    _, new = _full_pair()
    payload = json.loads(reporter.to_json(compare(None, new)))
    assert payload["edge_coverage"]["old"] is None
    # The block's own sub-schema: a no-baseline report's unrelated
    # analysis_assurance.schema_staleness_status is outside this change.
    schema = load_compare_report_schema()["properties"]["edge_coverage"]
    validate_instance(payload["edge_coverage"], schema)


def test_section_lists_only_the_unknown_relationships():
    section = compute_edge_coverage_section(compare(*_degraded_pair()))
    assert section is not None
    got = {(r.side, r.edge_kind) for r in section.rows}
    # OLD: tables read, headers parsed, but no DWARF -> its one header type is
    # unknown to the debug join. NEW loses every producer.
    assert got == {
        ("old", "debug_type_of"),
        ("new", "debug_type_of"),
        ("new", "exports"),
        ("new", "declares"),
        ("new", "references"),
    }


def test_fully_covered_comparison_states_so():
    section = compute_edge_coverage_section(compare(*_full_pair()))
    assert section is not None and section.rows == ()
    md = reporter.to_markdown(compare(*_full_pair()))
    assert "## Relationship Coverage" in md
    assert "each reported absence is proven" in md


def test_markdown_and_html_render_unknown_rows():
    result = compare(*_degraded_pair())
    md = reporter.to_markdown(result)
    assert "## Relationship Coverage" in md
    assert "| new | `exports` (resolved_join) | declarations | 0 | 0 | 1 |" in md
    assert "export_table[elf]: failed (export_table_not_read)" in md
    html = generate_html_report(result)
    assert "Relationship Coverage" in html
    assert "export_table[elf]: failed (export_table_not_read)" in html


def test_hand_built_result_renders_no_section():
    from abicheck.checker_policy import Verdict
    from abicheck.checker_types import DiffResult

    result = DiffResult(
        old_version="1",
        new_version="2",
        library="l",
        changes=[],
        verdict=Verdict.NO_CHANGE,
    )
    assert compute_edge_coverage_section(result) is None
    assert "Relationship Coverage" not in reporter.to_markdown(result)


def test_section_round_trips_through_its_mapping():
    import dataclasses

    section = compute_edge_coverage_section(compare(*_degraded_pair()))
    mapping = json.loads(json.dumps(dataclasses.asdict(section)))
    assert edge_coverage_section_from_mapping(mapping) == section
    assert edge_coverage_section_from_mapping(None) is None
