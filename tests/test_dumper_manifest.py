# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""ADR-050 D3 (G32 Phase B) — the per-TU dump loop and placeholder merge.

Scope: pure-Python tests over abicheck.dumper_manifest's TuFragment/merge
logic (fake entities, no compiler) and the run_tu_fragment/run_tu_loop
orchestration (a stub header_ast_parser, so the per-TU wiring -- which
includes get which headers, which public_header_paths, optional-TU-skip --
is verified without needing castxml/clang.

Two classes of real, unmocked end-to-end tests -- deliberately *not* marked
``integration`` (that marker's Linux gate requires castxml; the point here is
proving the loop works with just clang, the same castxml-absent-host
reasoning ``test_clang_header_backend_integration.py`` documents for itself),
each self-skipping when clang/g++ are unavailable:

- ``test_run_tu_loop_real_clang_backend_*`` inject ``dumper._header_ast_parser``
  itself with ``backend="clang"`` over real header TUs, at the
  ``dumper_manifest.run_tu_loop`` layer directly.
- ``TestDumpWithManifest`` goes one layer up, through ``dumper.dump()``
  itself with a real compiled ELF ``.so`` and a real ``DumpManifest`` --
  proving the ELF format handler's manifest-driven branch (wired in
  ``_dump_elf``) end to end, not just ``run_tu_loop`` in isolation.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.dump_manifest import DumpManifest, IncludeEntry, TranslationUnit
from abicheck.dumper import dump
from abicheck.dumper_manifest import (
    MergedTuFragments,
    TuFragment,
    entity_key,
    merge_tu_fragments,
    run_tu_fragment,
    run_tu_loop,
)
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import EnumType, Function, RecordType, Variable


def _fn(name: str, mangled: str | None = None) -> Function:
    return Function(name=name, mangled=mangled or name, return_type="void")


def _var(name: str, mangled: str | None = None) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int")


def _record(name: str) -> RecordType:
    return RecordType(name=name, kind="struct")


def _enum(name: str) -> EnumType:
    return EnumType(name=name)


# ---------------------------------------------------------------------------
# entity_key / merge_tu_fragments (no compiler needed)
# ---------------------------------------------------------------------------


def test_entity_key_is_kind_and_name_pair():
    assert entity_key("function", "_Z3fooi") == ("function", "_Z3fooi")


def test_merge_empty_fragments_returns_empty_result():
    merged = merge_tu_fragments([])
    assert isinstance(merged, MergedTuFragments)
    assert merged.functions == ()
    assert merged.typedefs == {}
    assert merged.ast_producer == "castxml"


def test_merge_concatenates_disjoint_fragments():
    a = TuFragment(tu_name="a", functions=(_fn("foo"),), ast_producer="clang")
    b = TuFragment(tu_name="b", functions=(_fn("bar"),), ast_producer="clang")
    merged = merge_tu_fragments([a, b])
    assert {fn.name for fn in merged.functions} == {"foo", "bar"}
    assert merged.ast_producer == "clang"


def test_merge_concatenates_every_entity_kind():
    a = TuFragment(
        tu_name="a",
        functions=(_fn("foo"),),
        variables=(_var("g_x"),),
        types=(_record("Point"),),
        enums=(_enum("Color"),),
        typedefs={"size_type": "unsigned long"},
        constants={"MAX": "100"},
    )
    merged = merge_tu_fragments([a])
    assert [fn.name for fn in merged.functions] == ["foo"]
    assert [v.name for v in merged.variables] == ["g_x"]
    assert [t.name for t in merged.types] == ["Point"]
    assert [e.name for e in merged.enums] == ["Color"]
    assert merged.typedefs == {"size_type": "unsigned long"}
    assert merged.constants == {"MAX": "100"}


def test_merge_raises_on_duplicate_function_across_tus():
    a = TuFragment(tu_name="a", functions=(_fn("foo", "_Z3fooi"),))
    b = TuFragment(tu_name="b", functions=(_fn("foo", "_Z3fooi"),))
    with pytest.raises(SnapshotError, match="redeclares function '_Z3fooi'"):
        merge_tu_fragments([a, b])


def test_merge_raises_on_duplicate_type_across_tus():
    a = TuFragment(tu_name="a", types=(_record("Point"),))
    b = TuFragment(tu_name="b", types=(_record("Point"),))
    with pytest.raises(SnapshotError, match="redeclares type 'Point'"):
        merge_tu_fragments([a, b])


def test_merge_raises_on_duplicate_typedef_across_tus():
    a = TuFragment(tu_name="a", typedefs={"size_type": "unsigned long"})
    b = TuFragment(tu_name="b", typedefs={"size_type": "unsigned long"})
    with pytest.raises(SnapshotError, match="redeclares typedef 'size_type'"):
        merge_tu_fragments([a, b])


def test_merge_does_not_raise_on_duplicate_within_a_single_fragment():
    # A single TU's own parser output is trusted as internally consistent;
    # only *cross*-TU repeats are this placeholder's concern.
    a = TuFragment(tu_name="a", functions=(_fn("foo"), _fn("foo")))
    merged = merge_tu_fragments([a])
    assert len(merged.functions) == 2


def test_merge_uses_first_fragment_ast_provenance_as_representative():
    a = TuFragment(tu_name="a", ast_producer="clang", ast_toolchain={"id": "clang-18"})
    b = TuFragment(tu_name="b", ast_producer="clang", ast_toolchain={"id": "clang-18"})
    merged = merge_tu_fragments([a, b])
    assert merged.ast_toolchain == {"id": "clang-18"}


# ---------------------------------------------------------------------------
# run_tu_fragment / run_tu_loop (stub header_ast_parser, no compiler needed)
# ---------------------------------------------------------------------------


class _StubParser:
    """Minimal stand-in for _CastxmlParser/_ClangAstParser."""

    def __init__(self, functions=(), fail: bool = False):
        self._functions = functions
        self._fail = fail
        if fail:
            raise SnapshotError("stub parser: simulated extraction failure")

    def parse_functions(self):
        return list(self._functions)

    def parse_variables(self):
        return []

    def parse_types(self):
        return []

    def parse_enums(self):
        return []

    def parse_typedefs(self):
        return {}

    def parse_constants(self):
        return {}


def _make_stub_header_ast_parser(calls: list, *, fail_for: frozenset = frozenset()):
    def _stub(headers, extra_includes, **kwargs):
        calls.append(
            {
                "headers": list(headers),
                "extra_includes": list(extra_includes),
                "public_header_paths": list(kwargs["public_header_paths"]),
                "public_dir_paths": list(kwargs["public_dir_paths"]),
            }
        )
        tu_marker = str(headers[0]) if headers else "<empty>"
        return _StubParser(
            functions=(_fn(Path(tu_marker).stem),),
            fail=tu_marker in fail_for,
        )

    return _stub


def _tu(name: str, header: str, *includes: str, required: bool = True, contributes: bool = True) -> TranslationUnit:
    return TranslationUnit(
        name=name,
        forced_includes=(Path(header),),
        includes=tuple(IncludeEntry(path=Path(p)) for p in includes),
        required=required,
        contributes_to_abi=contributes,
    )


def test_run_tu_fragment_calls_parser_with_tu_own_headers_and_includes():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls)
    tu = _tu("main", "foo.h", "vendor")
    fragment = run_tu_fragment(
        tu,
        header_ast_parser=stub,
        backend="auto",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=["foo.h"],
        public_dir_paths=[],
    )
    assert fragment.tu_name == "main"
    assert [fn.name for fn in fragment.functions] == ["foo"]
    assert calls[0]["headers"] == [Path("foo.h")]
    assert calls[0]["extra_includes"] == [Path("vendor")]


def test_run_tu_fragment_ast_producer_defaults_to_castxml_for_non_clang_parser():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls)
    tu = _tu("main", "foo.h")
    fragment = run_tu_fragment(
        tu,
        header_ast_parser=stub,
        backend="auto",
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )
    assert fragment.ast_producer == "castxml"


def test_run_tu_loop_calls_once_per_tu_with_shared_public_header_paths():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls)
    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    merged = run_tu_loop(
        tus,
        header_ast_parser=stub,
        roots=[Path("a.h"), Path("b.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert len(calls) == 2
    # Every TU's call sees the *manifest's* roots, not just its own header.
    assert calls[0]["public_header_paths"] == ["a.h", "b.h"]
    assert calls[1]["public_header_paths"] == ["a.h", "b.h"]
    assert {fn.name for fn in merged.functions} == {"a", "b"}


def test_run_tu_loop_required_tu_failure_propagates():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls, fail_for=frozenset({"a.h"}))
    tus = (_tu("a", "a.h", required=True),)
    with pytest.raises(SnapshotError, match="simulated extraction failure"):
        run_tu_loop(
            tus,
            header_ast_parser=stub,
            roots=[Path("a.h")],
            backend="auto",
            compiler="c++",
            exported_dynamic=set(),
            exported_static=set(),
        )


def test_run_tu_loop_optional_tu_failure_is_skipped():
    calls: list = []
    stub = _make_stub_header_ast_parser(calls, fail_for=frozenset({"a.h"}))
    tus = (
        _tu("a", "a.h", required=False, contributes=False),
        _tu("b", "b.h"),
    )
    merged = run_tu_loop(
        tus,
        header_ast_parser=stub,
        roots=[Path("b.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert {fn.name for fn in merged.functions} == {"b"}


def test_run_tu_loop_raises_on_duplicate_entity_across_tus():
    calls: list = []

    def _stub(headers, extra_includes, **kwargs):
        calls.append(1)
        return _StubParser(functions=(_fn("shared", "_Z6sharedv"),))

    tus = (_tu("a", "a.h"), _tu("b", "b.h"))
    with pytest.raises(SnapshotError, match="redeclares function"):
        run_tu_loop(
            tus,
            header_ast_parser=_stub,
            roots=[Path("a.h"), Path("b.h")],
            backend="auto",
            compiler="c++",
            exported_dynamic=set(),
            exported_static=set(),
        )


def test_run_tu_loop_empty_tus_returns_empty_merge():
    merged = run_tu_loop(
        (),
        header_ast_parser=_make_stub_header_ast_parser([]),
        roots=[Path("a.h")],
        backend="auto",
        compiler="c++",
        exported_dynamic=set(),
        exported_static=set(),
    )
    assert merged.functions == ()


# ---------------------------------------------------------------------------
# Real clang backend, no stub (see module docstring)
# ---------------------------------------------------------------------------


def test_run_tu_loop_real_clang_backend_merges_two_translation_units(tmp_path):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend dumper_manifest test")
    from abicheck.dumper import _header_ast_parser

    header_a = tmp_path / "a.h"
    header_a.write_text("int add_a(int a, int b);\n")
    header_b = tmp_path / "b.h"
    header_b.write_text("int add_b(int a, int b);\n")

    tus = (
        _tu("tu_a", str(header_a)),
        _tu("tu_b", str(header_b)),
    )
    merged = run_tu_loop(
        tus,
        header_ast_parser=_header_ast_parser,
        roots=[header_a, header_b],
        backend="clang",
        compiler="c++",
        exported_dynamic={"add_a", "add_b"},
        exported_static=set(),
    )
    assert {fn.name for fn in merged.functions} == {"add_a", "add_b"}
    assert merged.ast_producer == "clang"


def test_run_tu_loop_real_clang_backend_raises_on_duplicate_across_tus(tmp_path):
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend dumper_manifest test")
    from abicheck.dumper import _header_ast_parser

    header_a = tmp_path / "a.h"
    header_a.write_text("int shared_fn(int a, int b);\n")
    header_b = tmp_path / "b.h"
    header_b.write_text("int shared_fn(int a, int b);\n")

    tus = (
        _tu("tu_a", str(header_a)),
        _tu("tu_b", str(header_b)),
    )
    with pytest.raises(SnapshotError, match="redeclares function"):
        run_tu_loop(
            tus,
            header_ast_parser=_header_ast_parser,
            roots=[header_a, header_b],
            backend="clang",
            compiler="c++",
            exported_dynamic={"shared_fn"},
            exported_static=set(),
        )


# ---------------------------------------------------------------------------
# dumper.dump() itself, through the ELF format handler's manifest branch
# ---------------------------------------------------------------------------


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


class TestDumpWithManifest:
    """dumper.dump(so_path, [], dump_manifest=...) -- the real ELF entry
    point, not just run_tu_loop in isolation. Requires clang + g++ (real
    compile, real clang -ast-dump=json; no castxml needed)."""

    def _build_two_tu_lib(self, tmp_path: Path) -> Path:
        header_a = tmp_path / "a.h"
        header_a.write_text("int add_a(int a, int b);\n")
        header_b = tmp_path / "b.h"
        header_b.write_text("int add_b(int a, int b);\n")
        src = tmp_path / "lib.c"
        src.write_text(
            "int add_a(int a, int b) { return a + b; }\n"
            "int add_b(int a, int b) { return a - b; }\n"
        )
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        return so

    def _manifest(self, tmp_path: Path) -> DumpManifest:
        return DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(tmp_path / "a.h", tmp_path / "b.h"),
            translation_units=(
                TranslationUnit(name="tu_a", forced_includes=(tmp_path / "a.h",)),
                TranslationUnit(name="tu_b", forced_includes=(tmp_path / "b.h",)),
            ),
        )

    def test_dump_merges_two_tus_into_one_snapshot(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        snap = dump(
            so,
            [],
            version="1.0",
            compiler="cc",
            header_backend="clang",
            dump_manifest=self._manifest(tmp_path),
        )
        assert snap.from_headers is True
        assert {f.name for f in snap.functions} == {"add_a", "add_b"}
        assert snap.ast_producer == "clang"
        assert snap.contract is not None
        assert snap.contract.scope_fingerprint is not None

    def test_dump_rejects_headers_with_manifest(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="mutually exclusive"):
            dump(
                so,
                [tmp_path / "a.h"],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_rejects_public_headers_with_manifest(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="mutually exclusive"):
            dump(
                so,
                [],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                public_headers=[tmp_path / "a.h"],
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_rejects_manifest_for_hybrid_frontend(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        so = self._build_two_tu_lib(tmp_path)
        with pytest.raises(ValidationError, match="hybrid"):
            dump(
                so,
                [],
                version="1.0",
                compiler="cc",
                header_backend="hybrid",
                dump_manifest=self._manifest(tmp_path),
            )

    def test_dump_manifest_duplicate_across_tus_raises(self, tmp_path: Path):
        if not (_have("clang") and _have("gcc")):
            pytest.skip("clang and gcc are required for this end-to-end test")
        header_a = tmp_path / "a.h"
        header_a.write_text("int shared_fn(int a, int b);\n")
        header_b = tmp_path / "b.h"
        header_b.write_text("int shared_fn(int a, int b);\n")
        src = tmp_path / "lib.c"
        src.write_text("int shared_fn(int a, int b) { return a + b; }\n")
        so = tmp_path / "liblib.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
            check=True,
            capture_output=True,
        )
        manifest = DumpManifest(
            base_dir=tmp_path,
            compiler="cc",
            roots=(header_a, header_b),
            translation_units=(
                TranslationUnit(name="tu_a", forced_includes=(header_a,)),
                TranslationUnit(name="tu_b", forced_includes=(header_b,)),
            ),
        )
        with pytest.raises(SnapshotError, match="redeclares function"):
            dump(
                so,
                [],
                version="1.0",
                compiler="cc",
                header_backend="clang",
                dump_manifest=manifest,
            )
