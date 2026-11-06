# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""C++20 concepts and requires-clauses at the header (L2) level, clang backend.

Invariant: two versions of a public header compare, through the clang
backend's concept table and the ``concept_constraints`` detector, exactly as
a C++ reader judges the constraint edit -- a requirement only added is a
tightening (``concept_tightened``), a requirement only removed or a rewrite
is ``concept_constraint_changed``, and a pure rename (of a concept, or of a
template parameter) or no edit at all is no finding. The oracle is the
hand-written ``_CASES`` table, not the implementation's atom helpers.
A side that did not model constraints (castxml) yields no finding and keeps
the coverage gap named.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.compare.concept_constraints import diff_concept_constraint_tables
from abicheck.extract.declaration_provenance import build_public_set
from abicheck.extract.headers.clang.concept_constraints import (
    collect_cxx_concept_constraints,
)
from abicheck.model import AbiSnapshot
from abicheck.policy.analysis_assurance._concepts import concept_constraint_gap

_ADDABLE = "template <class T> concept Addable = requires(T a, T b) { a + b; };\n"
_ADDABLE_DC = (
    "template <class T> concept Addable = requires(T a, T b) { a + b; T(); };\n"
)
_SMALL = "template <class T> concept Small = sizeof(T) <= 8;\n"

# (id, v1 body, v2 body, expected {(kind, subject)})
_CASES = [
    (
        "concept_body_tightened",
        _ADDABLE + "template <Addable T> T sum(T, T);\n",
        _ADDABLE_DC + "template <Addable T> T sum(T, T);\n",
        {("concept_tightened", "lib::Addable")},
    ),
    (
        "concept_body_loosened",
        _ADDABLE_DC + "template <Addable T> T sum(T, T);\n",
        _ADDABLE + "template <Addable T> T sum(T, T);\n",
        {("concept_constraint_changed", "lib::Addable")},
    ),
    (
        "concept_body_rewritten",
        _ADDABLE + "template <Addable T> T sum(T, T);\n",
        "template <class T> concept Addable = requires(T a, T b) { a - b; };\n"
        "template <Addable T> T sum(T, T);\n",
        {("concept_constraint_changed", "lib::Addable")},
    ),
    (
        "requires_clause_added_to_function_template",
        _SMALL + "template <class T> void put(T);\n",
        _SMALL + "template <class T> requires Small<T> void put(T);\n",
        {("concept_tightened", "lib::put(void (type-param-0))")},
    ),
    (
        "trailing_requires_clause_added",
        _SMALL + "template <class T> void put(T);\n",
        _SMALL + "template <class T> void put(T) requires Small<T>;\n",
        {("concept_tightened", "lib::put(void (type-param-0))")},
    ),
    (
        "requires_clause_removed",
        _SMALL + "template <class T> requires Small<T> void put(T);\n",
        _SMALL + "template <class T> void put(T);\n",
        {("concept_constraint_changed", "lib::put(void (type-param-0))")},
    ),
    (
        "requires_clause_added_to_class_template",
        _SMALL + "template <class T> struct Box { T v; };\n",
        _SMALL + "template <class T> requires Small<T> struct Box { T v; };\n",
        {("concept_tightened", "lib::Box")},
    ),
    (
        "concept_renamed_same_constraint",
        _ADDABLE + "template <Addable T> T sum(T, T);\n",
        "template <class T> concept Summable = requires(T a, T b) { a + b; };\n"
        "template <Summable T> T sum(T, T);\n",
        set(),
    ),
    (
        "template_parameter_renamed",
        _SMALL + "template <class T> requires Small<T> void put(T);\n",
        _SMALL + "template <class U> requires Small<U> void put(U);\n",
        set(),
    ),
    (
        "unchanged",
        _ADDABLE + _SMALL + "template <Addable T> requires Small<T> T sum(T, T);\n",
        _ADDABLE + _SMALL + "template <Addable T> requires Small<T> T sum(T, T);\n",
        set(),
    ),
]


def _table(tmp_path: Path, tag: str, body: str) -> dict[str, str] | None:
    header = tmp_path / f"{tag}.h"
    header.write_text(f"#pragma once\nnamespace lib {{\n{body}}}\n")
    proc = subprocess.run(
        [
            "clang++",
            "-x",
            "c++-header",
            "-std=c++20",
            "-fsyntax-only",
            "-Xclang",
            "-ast-dump=json",
            str(header),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    segs, dirs, have = build_public_set([str(header)], None)
    return collect_cxx_concept_constraints(json.loads(proc.stdout), segs, dirs, have)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("v1", "v2", "expected"),
    [(a, b, e) for _, a, b, e in _CASES],
    ids=[i for i, *_ in _CASES],
)
def test_constraint_edit_reads_as_a_cxx_reader_judges_it(
    tmp_path: Path, v1: str, v2: str, expected: set[tuple[str, str]]
) -> None:
    if shutil.which("clang++") is None:
        pytest.skip("clang++ not installed")
    old, new = _table(tmp_path, "v1", v1), _table(tmp_path, "v2", v2)
    got = {(c.kind.value, c.symbol) for c in diff_concept_constraint_tables(old, new)}
    assert got == expected


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (None, {"concept lib::C": "conj:a"}),
        ({"concept lib::C": "conj:a"}, None),
        (None, None),
    ],
)
def test_an_unmodelled_side_yields_no_finding(
    old: dict[str, str] | None, new: dict[str, str] | None
) -> None:
    assert diff_concept_constraint_tables(old, new) == []


def test_symbol_present_on_one_side_only_is_not_reported() -> None:
    old = {"concept lib::A": "conj:x"}
    new = {"concept lib::B": "conj:x,y"}
    assert diff_concept_constraint_tables(old, new) == []


@pytest.mark.parametrize(
    ("table", "named"),
    [(None, True), ({}, False), ({"concept lib::C": "conj:a"}, False)],
)
def test_gap_is_named_only_for_an_unmodelled_side(
    table: dict[str, str] | None, named: bool
) -> None:
    snap = AbiSnapshot(library="l", version="1")
    snap.cxx_concept_header_scan = True
    snap.cxx_concept_constraints = table
    gap, notes = concept_constraint_gap(None, snap)
    assert (gap, len(notes)) == (named, int(named))


def test_no_public_set_is_not_modelled() -> None:
    assert (
        collect_cxx_concept_constraints({"kind": "TranslationUnitDecl"}, [], [], False)
        is None
    )


@pytest.mark.parametrize("value", [None, {}, {"concept lib::C": "conj:a,b"}])
def test_table_round_trips_through_a_saved_snapshot(
    tmp_path: Path, value: dict[str, str] | None
) -> None:
    from abicheck.serialization import load_snapshot
    from abicheck.storage.snapshot_codec import save_snapshot

    snap = AbiSnapshot(library="l", version="1")
    snap.cxx_concept_constraints = value
    path = tmp_path / "snap.json"
    save_snapshot(snap, path)
    assert load_snapshot(path).cxx_concept_constraints == value


@pytest.mark.parametrize("table", [None, {}, {"concept:Small": "C:sizeof(T) <= 4"}])
def test_hybrid_merge_keeps_the_clang_concept_table(
    table: dict[str, str] | None,
) -> None:
    """Only clang models concepts: a hybrid castxml+clang merge must carry
    clang's table verbatim (``None`` when clang did not model it), never the
    castxml side's absent one -- or a hybrid compare misses concept_tightened."""
    from abicheck.workflows.dump.hybrid_merge import merge_snapshots
    from tests.test_dumper_hybrid import _snap

    castxml = _snap(ast_producer="castxml")
    clang = _snap(ast_producer="clang")
    clang.cxx_concept_constraints = table
    merged = merge_snapshots(castxml, clang)
    assert merged.ast_producer == "hybrid"
    assert merged.cxx_concept_constraints == table
