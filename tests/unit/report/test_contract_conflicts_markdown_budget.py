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
    ("name", "demangled", "root"),
    [
        (
            "_ZZNSt8__detail18__to_chars_10_implIjEEvPcjT_E8__digits",
            "std::__detail::__to_chars_10_impl<unsigned int>(char*, unsigned int, unsigned int)::__digits",
            "std",
        ),
        ("_ZN5mylib3addEv", "mylib::add()", "mylib"),
        ("_Z3foov", "foo()", "(global)"),
        ("_Zbogus", None, "(undemangled)"),
        ("std::vector<int>::size()", None, "std"),
    ],
)
def test_review_family_label_demangles_mangled_names(
    name: str, demangled: str | None, root: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pinned demangler output, so the label does not depend on which backend
    # (cxxfilt, c++filt, none) this machine has.
    import abicheck.demangle as demangle_module
    from abicheck.report.render_markdown_document import _review_namespace_root

    monkeypatch.setattr(demangle_module, "demangle", lambda *_a, **_k: demangled)
    assert _review_namespace_root(name) == root


@pytest.mark.parametrize(
    ("entity", "origin"),
    [
        ("_ZNSolsEi", "toolchain runtime"),  # std::ostream::operator<<, `So`
        ("_ZNKSs4sizeEv", "toolchain runtime"),  # std::string, `Ss`
        ("_ZNSi3getEv", "toolchain runtime"),  # std::istream, `Si`
        ("_ZTVSd", "toolchain runtime"),  # vtable for std::iostream
        ("_ZSt4cout", "toolchain runtime"),
        ("_ZNSt6vectorIiE4sizeEv", "toolchain runtime"),
        ("_ZN9__gnu_cxx3fooEv", "toolchain runtime"),
        ("_ZGVZNSt3fooEvE1x", "toolchain runtime"),
        ("std::once_flag", "toolchain runtime"),
        ("_ZN3Std1fEv", "library"),  # a user namespace spelled `Std`
        ("_ZN5mylib3fooEv", "library"),
        ("_Z3foov", "library"),
        ("mylib_init", "library"),
    ],
)
def test_conflict_origin_reads_itanium_std_substitutions(
    entity: str, origin: str
) -> None:
    from abicheck.report.contract_conflicts_markdown import conflict_origin

    assert conflict_origin(entity) == origin


def test_mangled_runtime_rows_do_not_take_library_first_slots() -> None:
    runtime = tuple(_row(f"_ZNSolsE{i}") for i in range(CONFLICT_ROW_LIMIT))
    rows = (*runtime, _row("mylib_init"))
    text = "\n".join(render_contract_conflicts_section(ContractConflictsSection(rows)))
    assert "| exported_but_undeclared | library | 1 |" in text
    assert (
        f"| exported_but_undeclared | toolchain runtime | {CONFLICT_ROW_LIMIT} |"
        in text
    )
    itemised = [line for line in text.splitlines() if line.startswith("- **")]
    assert itemised[0].endswith("`mylib_init`")
