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

"""Tests for the Flow-2 producer side (ADR-035 D5, G19.4): the ``inputs_emit``
pack writer and the ``abicheck-cc`` compiler wrapper. The producer emits a pack
that round-trips through ``ingest_inputs_pack`` — no compiler is run here."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from abicheck.buildsource import (
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    append_source_facts,
    ingest_inputs_pack,
    init_inputs_pack,
    write_inputs_pack,
)
from abicheck.buildsource.inputs_emit import facts_filename
from abicheck.cc_wrapper import (
    compile_unit_from_command,
    emit_facts_for_command,
    main,
    run_cc_wrapper,
)


def _tu(name: str, *, mangled: str, source: str = "src/foo.cpp") -> SourceAbiTu:
    ent = SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=mangled,
        signature_hash="sig1",
        source_location=SourceLocation(path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id=f"cu://{source}", target_id="target://libfoo", source=source,
        public_header_roots=[f"include/{name}.h"], functions=[ent],
    )


# -- pack writer round-trip --------------------------------------------------


def test_write_inputs_pack_round_trips_through_ingest(tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps([
        {"directory": str(tmp_path), "file": "src/foo.cpp",
         "arguments": ["c++", "-std=c++17", "-c", "src/foo.cpp"]}
    ]))
    root = write_inputs_pack(
        tmp_path / "abicheck_inputs",
        library="libfoo.so", version="1.0", created_by="test",
        tus=[_tu("foo", mangled="_Z3foov")], compile_db=cdb,
    )
    ingested = ingest_inputs_pack(root)
    assert ingested.tu_count == 1
    assert ingested.manifest.created_by == "test"
    assert ingested.pack.build_evidence is not None  # compile DB copied + parsed
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


def test_incremental_init_then_append_round_trips(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", version="1.0", created_by="abicheck-cc")
    # Two per-TU appends, as a wrapper would do across two compile invocations.
    append_source_facts(pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp"))
    append_source_facts(pack, [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
                        filename=facts_filename("src/bar.cpp"))
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"foo", "bar"} <= names


def test_manifest_write_is_atomic_no_temp_leftover(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    # No straggler temp files from the atomic temp+replace write.
    assert not list(pack.glob(".manifest.*.tmp"))
    assert (pack / "manifest.json").is_file()


def test_init_recovers_from_partial_manifest(tmp_path: Path) -> None:
    # A manifest left half-written by some non-atomic writer must re-initialize,
    # not raise (which would lose this TU's facts in the wrapper's best-effort path).
    pack = tmp_path / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    (pack / "manifest.json").write_text('{"kind": "abicheck_inputs"', encoding="utf-8")  # truncated JSON
    m = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    assert m.library == "libfoo.so"
    # Manifest is now valid and round-trips.
    assert json.loads((pack / "manifest.json").read_text())["library"] == "libfoo.so"


def test_init_inputs_pack_is_idempotent(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    m1 = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    m2 = init_inputs_pack(pack, library="OTHER", created_by="OTHER")
    # Second call loads the existing manifest, does not clobber it.
    assert m2.library == m1.library == "libfoo.so"
    assert m2.created_by == "abicheck-cc"


def test_facts_filename_deterministic_and_collision_resistant() -> None:
    assert facts_filename("src/foo.cpp") == facts_filename("src/foo.cpp")
    # Same basename, different dir → different file.
    assert facts_filename("a/foo.cpp") != facts_filename("b/foo.cpp")
    assert facts_filename("src/foo.cpp").endswith(".jsonl")


# -- compile_unit_from_command -----------------------------------------------


def test_compile_unit_from_command_parses_flags(tmp_path: Path) -> None:
    cu = compile_unit_from_command(
        ["c++", "-std=c++17", "-DFOO=1", "-Iinc", "-c", "src/foo.cpp", "-o", "foo.o"],
        tmp_path,
    )
    assert cu is not None
    assert cu.source == "src/foo.cpp"
    assert cu.language == "CXX"
    assert cu.standard == "c++17"
    assert cu.defines.get("FOO") == "1"


def test_compile_unit_from_command_none_for_link_or_no_source(tmp_path: Path) -> None:
    assert compile_unit_from_command(["c++", "-shared", "foo.o", "-o", "libfoo.so"], tmp_path) is None
    assert compile_unit_from_command(["c++"], tmp_path) is None


def test_compile_unit_skips_preprocess_only_invocations(tmp_path: Path) -> None:
    # Preprocess-/dependency-only runs produce no shipped object → no facts, so
    # a build that pipes -E/-M steps through the wrapper can't pollute the pack.
    assert compile_unit_from_command(["c++", "-E", "src/foo.cpp"], tmp_path) is None
    assert compile_unit_from_command(["c++", "-M", "src/foo.cpp"], tmp_path) is None
    assert compile_unit_from_command(["c++", "-MM", "src/foo.cpp"], tmp_path) is None
    # -MD/-MMD are additive with a real -c compile and must NOT be skipped.
    cu = compile_unit_from_command(["c++", "-MD", "-c", "src/foo.cpp"], tmp_path)
    assert cu is not None and cu.source == "src/foo.cpp"


# -- run_cc_wrapper pass-through + best-effort -------------------------------


class _Proc:
    def __init__(self, rc: int) -> None:
        self.returncode = rc


def test_wrapper_preserves_exit_code_and_emits_on_success(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def fake_emit(command, directory, **kw):
        calls.append((tuple(command), kw))
        return None

    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"],
        runner=lambda c: _Proc(0),
        env={"ABICHECK_INPUTS_DIR": str(tmp_path / "pk")},
        emit=fake_emit,
    )
    assert rc == 0
    assert len(calls) == 1  # emit called on a successful compile


def test_wrapper_skips_emit_on_failed_compile() -> None:
    calls: list = []
    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"],
        runner=lambda c: _Proc(5),
        env={},
        emit=lambda *a, **k: calls.append(1),
    )
    assert rc == 5
    assert not calls  # no extraction when the compile failed


def test_wrapper_disable_env_is_pure_passthrough() -> None:
    calls: list = []
    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"],
        runner=lambda c: _Proc(0),
        env={"ABICHECK_CC_DISABLE": "1"},
        emit=lambda *a, **k: calls.append(1),
    )
    assert rc == 0
    assert not calls


def test_wrapper_swallows_extraction_errors() -> None:
    def boom(*a, **k):
        raise RuntimeError("extractor blew up")

    # A fact-extraction failure must never change the compiler's exit code.
    rc = run_cc_wrapper(["c++", "-c", "src/foo.cpp"], runner=lambda c: _Proc(0), env={}, emit=boom)
    assert rc == 0


def test_empty_command_errors() -> None:
    assert run_cc_wrapper([], runner=lambda c: _Proc(0)) == 2


def test_main_empty_args_returns_2() -> None:
    assert main([]) == 2


def test_default_runner_executes_real_command(tmp_path: Path, monkeypatch) -> None:
    # Exercise the real subprocess default-runner path with a trivial, portable
    # command (no compiler, no source TU → emit is a no-op).
    monkeypatch.chdir(tmp_path)
    assert run_cc_wrapper([sys.executable, "-c", ""]) == 0


# -- emit_facts_for_command with a stub backend (producer → merge) -----------


def test_emit_appends_extracted_tu(tmp_path: Path, monkeypatch) -> None:
    captured = _tu("foo", mangled="_Z3foov")

    class _FakeBackend:
        def extract(self, cu, *, public_header_roots, target_id=""):
            return captured

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, _FakeBackend()),
    )
    pack = tmp_path / "abicheck_inputs"
    tu = emit_facts_for_command(
        ["c++", "-c", "src/foo.cpp"], tmp_path,
        inputs_dir=pack, library="libfoo.so",
    )
    assert tu is captured
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    assert ingested.manifest.created_by == "abicheck-cc"


def test_emit_captures_all_sources_in_multi_source_compile(tmp_path: Path, monkeypatch) -> None:
    # `gcc -c a.cpp b.cpp` builds both objects; both must contribute facts.
    def _extract(cu, *, public_header_roots, target_id=""):
        stem = Path(cu.source).stem
        return _tu(stem, mangled=f"_Z3{stem}v", source=cu.source)

    class _FakeBackend:
        extract = staticmethod(_extract)

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, _FakeBackend()),
    )
    pack = tmp_path / "abicheck_inputs"
    emit_facts_for_command(
        ["g++", "-std=c++17", "-c", "a.cpp", "b.cpp"], tmp_path,
        inputs_dir=pack, library="libfoo.so",
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"a", "b"} <= names


def test_emit_none_when_no_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, None),
    )
    out = emit_facts_for_command(["c++", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk")
    assert out is None
