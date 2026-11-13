# SPDX-License-Identifier: Apache-2.0
"""A failed optional header pass is a recorded limitation, never a clean read.

Bug class ``evidence.unread_producer_read_as_confirmed_absence``: the castxml
backend's clang joins (vector/complex/_BitInt spellings and function-type
calling conventions; const variable templates), the clang backend's
explicit-instantiation text pass and its record-layout pass each swallowed
their own failure and returned the parse as if they had run. A ``float
_Complex`` -> ``double _Complex`` parameter then compared as two equal
``Unimplemented`` spellings: ``NO_CHANGE``, assurance ``complete``, exit 0.

Each failure now records a reason under ``dump_provenance
["extraction_limitations"]`` (the list the assurance rollup reads), and makes
the open snapshot-cache recording store nothing, so the next run retries the
pass instead of being served the gapped snapshot. Legitimate absence -- a
successful pass, no construct to join, every record already laid out --
still records nothing.
"""

from __future__ import annotations

import itertools
import re
import subprocess
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract import cache_dep_manifest
from abicheck.extract.extraction_limitations import (
    EXTRACTION_LIMITATIONS_KEY,
    JOIN_GAPS_ATTR,
    add_snapshot_limitation,
    join_gaps,
    merged_extraction_provenance,
    note_parser_limitation,
    parser_limitations,
    provenance_with_limitations,
    unresolved_spelling_limitation,
    with_join_gap,
)
from abicheck.extract.header_ast_fields import parse_header_ast_fields
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Variable
from abicheck.model.entities import TypeField

_JOIN_INPUTS: dict[str, Any] = {
    "extra_includes": [],
    "compiler": "c++",
    "gcc_path": None,
    "gcc_prefix": None,
    "gcc_options": None,
    "gcc_option_tokens": (),
    "sysroot": None,
    "nostdinc": False,
    "lang": None,
    "extra_hash_dirs": (),
    "exported_symbols": frozenset(),
}


class _MissStore:
    """An AST-cache store with no entry: every join runs clang."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.published: list[object] = []

    def path(self, key: str) -> Path:
        return self.root / "entry.json"

    def lookup(self, key: str, memo: object = None) -> object:
        return None

    def publish(
        self, key: str, payload: object, deps: object, memo: object = None
    ) -> None:
        self.published.append(payload)


def _vector_root() -> Element:
    root = Element("GCC_XML")
    root.append(Element("Namespace", id="_1", name="::"))
    root.append(Element("Unimplemented", id="_26", type_class="Complex"))
    fn = Element(
        "Function",
        id="_13",
        name="take",
        mangled="_Z4takeCf",
        context="_1",
        returns="_99",
    )
    fn.append(Element("Argument", id="_40", name="x", type="_26"))
    root.append(fn)
    return root


def _run_join(
    which: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fails: bool
) -> tuple[Element, Element, cache_dep_manifest.DepManifest | None, _MissStore]:
    """Run the *which* join over a root that needs it; clang fails or answers."""
    import abicheck.extract.headers.castxml.clang_semantics_join as sj
    import abicheck.extract.headers.castxml.variable_template_join as vtj
    import abicheck.extract.headers.castxml.vector_join as vj

    module = {"vector": vj, "variable_template": vtj, "semantics": sj}[which]
    store = _MissStore(tmp_path)
    monkeypatch.setattr(module, "ast_cache_store", lambda key, backend: store)

    def clang(*args: object, **kwargs: object) -> tuple[dict, None, bool]:
        if fails:
            raise SnapshotError("clang unavailable")
        return {"kind": "TranslationUnitDecl", "inner": []}, None, False

    monkeypatch.setattr(module, "clang_header_dump", clang)
    header = tmp_path / "api.hpp"
    header.write_text(
        "template <class T> inline constexpr int vt = 1;\n"
        "template <class T> void g(T) noexcept;\n"
    )
    root = _vector_root()
    join = {
        "vector": vj.join_vector_spellings,
        "variable_template": vtj.join_variable_templates,
        "semantics": sj.join_clang_semantics,
    }[which]
    with cache_dep_manifest.recording() as rec:
        returned = join(root, "key", headers=[header], **_JOIN_INPUTS)
    return root, returned, rec.manifest(), store


@pytest.mark.parametrize("which", ["vector", "variable_template"])
def test_failed_clang_join_records_its_gap_on_the_root(
    which: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, returned, deps, store = _run_join(which, monkeypatch, tmp_path, fails=True)
    gaps = join_gaps(returned)
    assert len(gaps) == 1
    assert "clang join failed" in gaps[0]
    # The parsed tree is never written; the copy keeps every declaration.
    assert root.get(JOIN_GAPS_ATTR) is None
    assert [c.get("id") for c in returned] == [c.get("id") for c in root]
    # Not cached at either tier: the next run retries the join.
    assert deps is None
    assert store.published == []


@pytest.mark.parametrize("which", ["vector", "variable_template", "semantics"])
def test_failed_clang_join_is_never_stored_in_the_snapshot_cache(
    which: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, deps, _ = _run_join(which, monkeypatch, tmp_path, fails=True)
    assert deps is None


@pytest.mark.parametrize("which", ["vector", "variable_template", "semantics"])
def test_successful_clang_join_records_no_gap(
    which: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, returned, deps, _ = _run_join(which, monkeypatch, tmp_path, fails=False)
    assert join_gaps(returned) == ()
    assert deps is not None


def test_join_gaps_accumulate_once_each_in_order() -> None:
    root = with_join_gap(with_join_gap(Element("GCC_XML"), "a"), "b")
    assert join_gaps(with_join_gap(root, "a")) == ("a", "b")


@pytest.mark.parametrize("raw", ["{", '{"a": 1}', "[1]", '"text"'])
def test_malformed_join_gap_attribute_is_one_gap_not_none(raw: str) -> None:
    root = Element("GCC_XML", {JOIN_GAPS_ATTR: raw})
    (gap,) = join_gaps(root)
    assert "unknown" in gap


# --- unresolved spellings: an exhaustive small domain --------------------------

_SPELLINGS = (
    "int",
    "Unimplemented",
    "Unimplemented*",
    "const Unimplemented &",
    "Unimplemented[4]",
    "UnimplementedType",
    "my_Unimplemented",
    "float __attribute__((vector_size(16)))",
    "",
)


def _oracle_unresolved(spelling: str) -> bool:
    """Independent of the implementation's regex: split into identifiers."""
    return "Unimplemented" in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", spelling)


@pytest.mark.parametrize(
    ("ret", "param", "var", "fld", "td"),
    list(
        itertools.product(
            _SPELLINGS[:5],
            _SPELLINGS[4:],
            ("int",),
            _SPELLINGS[::3],
            ("int", "Unimplemented"),
        )
    ),
)
def test_unresolved_spelling_count_matches_every_recorded_slot(
    ret: str, param: str, var: str, fld: str, td: str
) -> None:
    fn = Function("f", "_Z1fv", ret, params=[Param("p", param)])
    variables = [Variable("v", "v", var)]
    types = [RecordType(name="S", kind="struct", fields=[TypeField("x", fld)])]
    expected = sum(_oracle_unresolved(s) for s in (ret, param, var, fld, td))
    got = unresolved_spelling_limitation([fn], variables, types, {"T": td})
    if expected == 0:
        assert got is None
    else:
        assert got is not None
        assert got.startswith(f"unresolved type spellings: {expected} recorded")


# --- header-field assembly ------------------------------------------------------


class _FakeParser:
    """A header-AST parser exposing only the protocol's parse methods."""

    def __init__(self, functions: list[Function]) -> None:
        self._functions = functions

    def parse_functions(self) -> list[Function]:
        return self._functions

    def parse_variables(self) -> list[Variable]:
        return []

    def parse_types(self) -> list[RecordType]:
        return []

    def parse_enums(self) -> list[Any]:
        return []

    def parse_typedefs(self) -> dict[str, str]:
        return {}

    def parse_typedefs_qualified(self) -> dict[str, str]:
        return {}

    def parse_typedef_entity_ids(self) -> dict[str, Any]:
        return {}

    def parse_typedef_abi_facts(self) -> dict[str, Any]:
        return {}

    def parse_constants(self) -> dict[str, str]:
        return {}

    def parse_constant_entity_ids(self) -> dict[str, Any]:
        return {}


def _fn(spelling: str) -> Function:
    return Function("take", "_Z4take", "void", params=[Param("x", spelling)])


def test_unresolved_parameter_spelling_reaches_the_extraction_provenance() -> None:
    fields = parse_header_ast_fields(
        _FakeParser([_fn("Unimplemented")]), producer="castxml"
    )
    (reason,) = fields.extraction_provenance[EXTRACTION_LIMITATIONS_KEY]
    assert reason.startswith("unresolved type spellings: 1 recorded")


def test_parser_limitation_reaches_the_extraction_provenance() -> None:
    parser = _FakeParser([_fn("float _Complex")])
    note_parser_limitation(parser, "explicit template instantiations: failed")
    note_parser_limitation(parser, "explicit template instantiations: failed")
    fields = parse_header_ast_fields(parser, producer="clang")
    assert fields.extraction_provenance == {
        EXTRACTION_LIMITATIONS_KEY: ["explicit template instantiations: failed"]
    }


def test_fully_read_parse_records_no_provenance() -> None:
    fields = parse_header_ast_fields(
        _FakeParser([_fn("float _Complex")]), producer="castxml"
    )
    assert fields.extraction_provenance is None
    assert parser_limitations(_FakeParser([])) == ()


# --- end to end through compare: the reported false clean ----------------------


def _snapshot(spelling: str) -> AbiSnapshot:
    fields = parse_header_ast_fields(_FakeParser([_fn(spelling)]), producer="castxml")
    return AbiSnapshot(
        library="libapi.so",
        version="1",
        functions=list(fields.functions),
        ast_producer="castxml",
        **fields.clang_ast_tables(),
    )


def test_unresolved_complex_parameter_compares_partial_not_clean() -> None:
    """``float _Complex`` -> ``double _Complex`` with the join failed on both
    sides: both spell ``Unimplemented``, so no finding can be produced -- the
    assurance must say so instead of ``complete``."""
    from abicheck.checker import compare

    result = compare(_snapshot("Unimplemented"), _snapshot("Unimplemented"))
    assurance = result.analysis_assurance
    assert assurance is not None
    assert assurance.status == "partial"
    assert any("unresolved type spellings" in n for n in assurance.notes)


def test_resolved_complex_parameter_change_still_reported_cleanly() -> None:
    from abicheck.checker import compare

    result = compare(_snapshot("float _Complex"), _snapshot("double _Complex"))
    assert result.verdict.name == "BREAKING"
    assert result.analysis_assurance.status == "complete"


def test_limitation_survives_a_stored_round_trip(tmp_path: Path) -> None:
    from abicheck.serialization import load_snapshot, save_snapshot

    path = tmp_path / "s.json"
    save_snapshot(_snapshot("Unimplemented"), path)
    loaded = load_snapshot(path)
    assert loaded.dump_provenance is not None
    (reason,) = loaded.dump_provenance[EXTRACTION_LIMITATIONS_KEY]
    assert reason.startswith("unresolved type spellings")


# --- clang backend passes --------------------------------------------------------


class _Run:
    def __init__(self, *, timeout: bool = False, returncode: int = 0) -> None:
        self.timeout = timeout
        self.returncode = returncode

    def __call__(self, cmd: list[str], *, timeout: int, on_created: Any) -> Any:
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


def _explicit_facts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str, run: _Run
) -> object:
    import abicheck.extract.headers.clang.backend as cb

    monkeypatch.setattr(cb, "_resolve_clang_system_includes", lambda *a, **k: ())
    monkeypatch.setattr(
        cb, "_build_clang_header_command", lambda *a, **k: ["clang", "-ast-dump=json"]
    )
    header = tmp_path / "api.hpp"
    header.write_text(text)

    class _Req:
        headers = [header]
        extra_includes: list[Path] = []
        compiler = "c++"
        gcc_path = gcc_prefix = gcc_options = sysroot = None
        gcc_option_tokens: tuple[str, ...] = ()
        nostdinc = False

    return cb._explicit_instantiation_facts(
        _Req(), {}, clang_bin="clang", force_cpp=True, run_ast=run
    )


@pytest.mark.parametrize("run", [_Run(timeout=True), _Run(returncode=1)])
def test_failed_explicit_instantiation_pass_is_unknown_not_empty(
    run: _Run, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = "template <class T> struct Box {};\ntemplate struct Box<int>;\n"
    assert _explicit_facts(monkeypatch, tmp_path, text, run) is None


def test_header_without_explicit_instantiation_reads_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _explicit_facts(monkeypatch, tmp_path, "int f(void);\n", _Run()) == {}


def _layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, records: object, sizes: list
) -> tuple[AbiSnapshot, cache_dep_manifest.DepManifest | None]:
    import abicheck.extract.headers.clang.layout_tool as lt

    monkeypatch.setattr(lt, "find_layout_tool_bin", lambda: None)
    monkeypatch.setattr(lt, "_binary_abi_target", lambda *a, **k: None)
    monkeypatch.setattr(lt, "run_layout_tool", lambda *a, **k: records)
    header = tmp_path / "a.h"
    header.write_text("struct A { int a; }; struct B { char b; };")
    types = [
        RecordType(name=n, kind="struct", size_bits=s, data_size_bits=s)
        for n, s in zip(("A", "B"), sizes, strict=True)
    ]
    snap = AbiSnapshot(library="l", version="1", ast_producer="clang", types=types)
    with cache_dep_manifest.recording() as rec:
        out = lt.attach_clang_layout(snap, [header], [], lang=None, compile=None)
    return out, rec.manifest()


def test_failed_layout_pass_names_the_records_it_left_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, deps = _layout(monkeypatch, tmp_path, None, [None, 8])
    (reason,) = out.dump_provenance[EXTRACTION_LIMITATIONS_KEY]
    assert "1 record(s)" in reason
    assert deps is None


@pytest.mark.parametrize(
    ("records", "sizes"),
    [
        ([{"qualified_name": "A", "size_bits": 32}], [None, None]),  # pass ran
        (None, [32, 8]),  # nothing was missing: no pass needed
    ],
)
def test_layout_pass_that_ran_or_was_not_needed_records_nothing(
    records: object, sizes: list, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, deps = _layout(monkeypatch, tmp_path, records, sizes)
    assert out.dump_provenance is None
    assert deps is not None


# --- carriers ------------------------------------------------------------------


def test_multi_tu_merge_unions_every_unit_limitation() -> None:
    merged = merged_extraction_provenance(
        [
            {EXTRACTION_LIMITATIONS_KEY: ["a", "b"]},
            None,
            {EXTRACTION_LIMITATIONS_KEY: ["b", "c"]},
            {"other": 1},
        ]
    )
    assert merged == {EXTRACTION_LIMITATIONS_KEY: ["a", "b", "c"]}
    assert merged_extraction_provenance([None, {}]) is None


def test_run_tu_loop_keeps_a_fragment_limitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abicheck.extract.headers.manifest as mf
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.tu_fragment import TuFragment

    fragments = [
        TuFragment(
            tu_name="a", extraction_provenance={EXTRACTION_LIMITATIONS_KEY: ["x"]}
        ),
        TuFragment(tu_name="b"),
    ]
    monkeypatch.setattr(mf, "_run_tu_fragments", lambda *a, **k: fragments)
    merged = mf.run_tu_loop(
        [TranslationUnit(name="a"), TranslationUnit(name="b")],
        header_ast_parser=lambda *a, **k: None,
        roots=[],
        backend="castxml",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert merged.extraction_provenance == {EXTRACTION_LIMITATIONS_KEY: ["x"]}


def test_snapshot_limitation_keeps_existing_provenance() -> None:
    snap = AbiSnapshot(library="l", version="1", dump_provenance={"frontend": "clang"})
    add_snapshot_limitation(snap, "r")
    add_snapshot_limitation(snap, "r")
    assert snap.dump_provenance == {
        "frontend": "clang",
        EXTRACTION_LIMITATIONS_KEY: ["r"],
    }
    assert provenance_with_limitations(None, ["", ""]) is None


@pytest.mark.parametrize(
    ("facts", "gapped"),
    [(None, True), ({}, False), ({1: object()}, False)],
)
def test_explicit_instantiation_facts_reach_the_parser_and_name_a_failed_pass(
    facts: Any, gapped: bool
) -> None:
    import abicheck.extract.headers.clang.backend as cb

    parser = _FakeParser([])
    with cache_dep_manifest.recording() as rec:
        cb._apply_explicit_instantiations(parser, facts)
    assert parser.explicit_instantiations == (facts or {})
    assert parser_limitations(parser) == (
        (cb.EXPLICIT_INSTANTIATION_GAP,) if gapped else ()
    )
    assert (rec.manifest() is None) is gapped


def test_layout_pass_that_cannot_resolve_its_headers_names_the_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A header input the pass cannot expand is a failed pass too."""
    import abicheck.extract.headers.clang.layout_tool as lt

    monkeypatch.setattr(lt, "find_layout_tool_bin", lambda: "/fake/tool")
    snap = AbiSnapshot(
        library="l",
        version="1",
        ast_producer="clang",
        types=[RecordType(name="A", kind="struct")],
    )
    with cache_dep_manifest.recording() as rec:
        out = lt.attach_clang_layout(
            snap, [tmp_path / "missing.h"], [], lang=None, compile=None
        )
    (reason,) = out.dump_provenance[EXTRACTION_LIMITATIONS_KEY]
    assert "1 record(s)" in reason
    assert rec.manifest() is None


@pytest.mark.parametrize("root", [None, "GCC_XML", {"abicheck-clang-join-gaps": "[]"}])
def test_join_gaps_of_a_non_element_root_is_empty(root: object) -> None:
    assert join_gaps(root) == ()
