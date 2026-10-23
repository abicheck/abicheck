"""The Markdown contract-conflicts section stays bounded (SVS field report).

A C++ library exporting libstdc++ template instantiations produced a 355 KB
section, one row per multi-KB demangled name, on a byte-identical rebuild.
"""

from __future__ import annotations

import pytest

from abicheck.report.contract_conflicts_markdown import (
    CONFLICT_ROW_LIMIT,
    ContractConflictRow,
    ContractConflictsSection,
    render_contract_conflicts_section,
)


def _row(entity: str, kind: str = "exported_but_undeclared") -> ContractConflictRow:
    return ContractConflictRow(
        conflict_kind=kind,
        entity=entity,
        side="new",
        reason_code="x",
        source_lines=(
            f"export_table: binary exports symbol '{entity}'",
            "header_declarations: no public header declares it",
        ),
    )


@pytest.mark.parametrize("n", [CONFLICT_ROW_LIMIT + 1, 500, 3000])
def test_section_size_is_bounded_regardless_of_row_count(n: int) -> None:
    long = "std::once_flag::_Prepare_execution<" + "std::call_once<int>," * 300 + ">"
    rows = tuple(_row(f"{long}{i}") for i in range(n))
    text = "\n".join(render_contract_conflicts_section(ContractConflictsSection(rows)))
    assert len(text) < 30_000
    assert f"| exported_but_undeclared | toolchain runtime | {n} |" in text
    assert f"and {n - CONFLICT_ROW_LIMIT} more" in text
    assert "`contract_conflicts`" in text


def test_library_entities_are_itemised_before_runtime_ones() -> None:
    runtime = tuple(_row(f"std::f<{i}>") for i in range(CONFLICT_ROW_LIMIT * 2))
    rows = (*runtime, _row("mylib_init", "manifest_narrowed"))
    text = "\n".join(render_contract_conflicts_section(ContractConflictsSection(rows)))
    assert "`mylib_init`" in text
    assert "| manifest_narrowed | library | 1 |" in text


def test_small_section_lists_every_row_without_a_more_line() -> None:
    rows = (_row("a"), _row("b"))
    text = "\n".join(render_contract_conflicts_section(ContractConflictsSection(rows)))
    assert "`a`" in text and "`b`" in text
    assert "more;" not in text


@pytest.mark.parametrize(
    ("name", "root"),
    [
        ("_ZZNSt8__detail18__to_chars_10_implIjEEvPcjT_E8__digits", "std"),
        ("_ZN5mylib3addEv", "mylib"),
        ("_Z3foov", "(global)"),
        ("_Zbogus", "(undemangled)"),
        ("std::vector<int>::size()", "std"),
    ],
)
def test_review_family_label_demangles_mangled_names(name: str, root: str) -> None:
    from abicheck.report.render_markdown_document import _review_namespace_root

    assert _review_namespace_root(name) == root
