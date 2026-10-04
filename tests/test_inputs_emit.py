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

import gzip
import json
import sys
from pathlib import Path

import pytest

from abicheck.buildsource import (
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
    append_source_facts,
    ingest_inputs_pack,
    init_inputs_pack,
)
from abicheck.buildsource.inputs_emit import (
    facts_filename,
)
from abicheck.cc_wrapper import (
    compile_units_from_command,
    emit_facts_for_command,
    main,
    run_cc_wrapper,
)
from tests._inputs_pack_writer import write_inputs_pack


def _tu(name: str, *, mangled: str, source: str = "src/foo.cpp") -> SourceAbiTu:
    ent = SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=mangled,
        signature_hash="sig1",
        source_location=SourceLocation(
            path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id=f"cu://{source}",
        target_id="target://libfoo.so",
        source=source,
        public_header_roots=[f"include/{name}.h"],
        functions=[ent],
    )


# -- pack writer round-trip --------------------------------------------------


def test_write_inputs_pack_round_trips_through_ingest(tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": "src/foo.cpp",
                    "arguments": ["c++", "-std=c++17", "-c", "src/foo.cpp"],
                }
            ]
        )
    )
    root = write_inputs_pack(
        tmp_path / "abicheck_inputs",
        library="libfoo.so",
        version="1.0",
        created_by="test",
        tus=[_tu("foo", mangled="_Z3foov")],
        compile_db=cdb,
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
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename=facts_filename("src/foo.cpp")
    )
    append_source_facts(
        pack,
        [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename=facts_filename("src/bar.cpp"),
    )
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
    (pack / "manifest.json").write_text(
        '{"kind": "abicheck_inputs"', encoding="utf-8"
    )  # truncated JSON
    m = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    assert m.library == "libfoo.so"
    # Manifest is now valid and round-trips.
    assert json.loads((pack / "manifest.json").read_text())["library"] == "libfoo.so"


def test_init_inputs_pack_is_idempotent(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    m1 = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    # A repeated call naming the *same* library (created_by may vary -- not
    # part of the target-isolation identity) loads the existing manifest.
    m2 = init_inputs_pack(pack, library="libfoo.so", created_by="OTHER")
    assert m2.library == m1.library == "libfoo.so"
    assert m2.created_by == "abicheck-cc"


def test_init_inputs_pack_rejects_conflicting_library(tmp_path: Path) -> None:
    """PR3 target isolation (latest-main Clang plugin review): a second
    invocation naming a *different* library against the same pack root is
    exactly the same-source/two-library collision the prior first-writer-
    wins manifest allowed -- raise instead of silently keeping the first
    library's identity."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    with pytest.raises(ValueError, match="library"):
        init_inputs_pack(pack, library="libbar.so", created_by="abicheck-cc")
    # The pack must be untouched by the rejected call.
    assert json.loads((pack / "manifest.json").read_text())["library"] == "libfoo.so"


def test_init_inputs_pack_rejects_conflicting_version(tmp_path: Path) -> None:
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", version="1.0", created_by="abicheck-cc")
    with pytest.raises(ValueError, match="version"):
        init_inputs_pack(
            pack, library="libfoo.so", version="2.0", created_by="abicheck-cc"
        )


def test_init_inputs_pack_allows_unspecified_library_or_version(tmp_path: Path) -> None:
    """Omitting library/version (the default "") is never treated as a
    conflict either way -- only two *different* non-empty values collide."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", version="1.0", created_by="abicheck-cc")
    m = init_inputs_pack(pack, created_by="abicheck-cc")  # no library/version passed
    assert m.library == "libfoo.so"
    assert m.version == "1.0"


def test_init_inputs_pack_race_loser_validates_against_winner(
    tmp_path: Path, monkeypatch
) -> None:
    """Two racing first-TU invocations for *different* libraries sharing one
    out= directory must not let the second one silently win: simulate the
    exists()-then-write TOCTOU by making this call's os.link() lose to a
    manifest a "concurrent" process publishes in between -- the loser must
    re-read and validate against it (raising on the library conflict), not
    blindly overwrite it (Codex review)."""
    import os as os_module

    pack = tmp_path / "abicheck_inputs"
    real_link = os_module.link

    def _racing_link(src, dst, *a, **k):
        # Simulate a concurrent winner publishing its own manifest for a
        # *different* library right before our os.link() call runs.
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_text(
            json.dumps(
                {
                    "kind": "abicheck_inputs",
                    "abicheck_inputs_version": 1,
                    "library": "libbar.so",
                    "version": "",
                }
            )
        )
        raise FileExistsError(17, "File exists")

    monkeypatch.setattr(os_module, "link", _racing_link)
    with pytest.raises(ValueError, match="library"):
        init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    monkeypatch.setattr(os_module, "link", real_link)
    # The "winner"'s manifest must be exactly what it published -- our losing
    # call must never have overwritten it.
    assert json.loads((pack / "manifest.json").read_text())["library"] == "libbar.so"


def test_init_inputs_pack_race_loser_agreeing_target_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """The same race, but the concurrent winner named the *same* library --
    the loser must load and return it cleanly instead of raising."""
    import os as os_module

    pack = tmp_path / "abicheck_inputs"

    def _racing_link(src, dst, *a, **k):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_text(
            json.dumps(
                {
                    "kind": "abicheck_inputs",
                    "abicheck_inputs_version": 1,
                    "library": "libfoo.so",
                    "version": "",
                }
            )
        )
        raise FileExistsError(17, "File exists")

    monkeypatch.setattr(os_module, "link", _racing_link)
    m = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    assert m.library == "libfoo.so"


def test_init_inputs_pack_rejects_wrong_kind_manifest(tmp_path: Path) -> None:
    # A directory with a manifest.json for a different pack kind (e.g. a
    # BuildSourcePack) must be rejected, not silently accepted -- this is
    # the very first point of contact for a build's pack, so silently
    # accepting it would let every subsequent append_source_facts() call
    # write source_facts/*.jsonl into that unrelated directory (CodeRabbit
    # review, P2).
    pack = tmp_path / "pack"
    pack.mkdir()
    manifest_path = pack / "manifest.json"
    original_manifest = json.dumps({"build_source_pack_version": 1})
    manifest_path.write_text(original_manifest)
    with pytest.raises(ValueError, match="does not declare kind"):
        init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    # A rejected wrong-kind pack must be left completely untouched -- not
    # just its directory listing, but the manifest's own bytes -- including
    # no stray source_facts/ directory created before the kind check ran
    # (CodeRabbit review, P2).
    assert manifest_path.read_text() == original_manifest
    assert sorted(p.name for p in pack.iterdir()) == ["manifest.json"]


def test_init_inputs_pack_recovers_from_truly_malformed_manifest(
    tmp_path: Path,
) -> None:
    # A manifest left partial by a non-atomic writer (our own writes are
    # atomic) is genuinely malformed JSON, not a different pack -- this
    # case must still re-initialize rather than raise (the original
    # defensive behavior this fix must not regress).
    pack = tmp_path / "abicheck_inputs"
    pack.mkdir()
    (pack / "manifest.json").write_text('{"kind": "abicheck_inputs", "library":')
    manifest = init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    assert manifest.library == "libfoo.so"


def test_facts_filename_deterministic_and_collision_resistant() -> None:
    assert facts_filename("src/foo.cpp") == facts_filename("src/foo.cpp")
    # Same basename, different dir → different file.
    assert facts_filename("a/foo.cpp") != facts_filename("b/foo.cpp")
    assert facts_filename("src/foo.cpp").endswith(".jsonl")


# -- compression (P1 #22) -----------------------------------------------------


def test_write_inputs_pack_compress_round_trips_through_ingest(tmp_path: Path) -> None:
    root = write_inputs_pack(
        tmp_path / "abicheck_inputs",
        library="libfoo.so",
        version="1.0",
        created_by="test",
        tus=[_tu("foo", mangled="_Z3foov")],
        compress=True,
    )
    facts = list((root / "source_facts").glob("*.jsonl.gz"))
    assert len(facts) == 1
    ingested = ingest_inputs_pack(root)
    assert ingested.tu_count == 1
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


def test_append_source_facts_compress_supports_incremental_appends(
    tmp_path: Path,
) -> None:
    # Compression is execution policy: two separate compressed appends (as
    # parallel wrapper invocations sharing one file would do) must still
    # decode to both TUs, exactly like the uncompressed incremental path.
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename="shared.jsonl.gz"
    )
    append_source_facts(
        pack,
        [_tu("bar", mangled="_Z3barv", source="src/bar.cpp")],
        filename="shared.jsonl.gz",
    )
    assert not (pack / "source_facts" / "shared.jsonl").exists()
    assert (pack / "source_facts" / "shared.jsonl.gz").is_file()
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"foo", "bar"} <= names


def test_append_source_facts_infers_compression_from_gz_filename(
    tmp_path: Path,
) -> None:
    # A caller-supplied ".gz" filename must be written compressed, never as
    # plaintext under a misleading name that read_source_facts() would then
    # fail to decompress (CodeRabbit review, P2).
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    path = append_source_facts(
        pack, [_tu("foo", mangled="_Z3foov")], filename="custom.jsonl.gz"
    )
    assert path.name == "custom.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        fh.read()  # must decompress cleanly -- would raise on plaintext
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1


@pytest.mark.parametrize(
    ("filename", "expected_name"),
    [
        ("tu", "tu.jsonl"),
        ("tu.gz", "tu.jsonl.gz"),
        ("tu.json", "tu.json"),
        ("tu.json.gz", "tu.json.gz"),
        ("tu.jsonl.gz", "tu.jsonl.gz"),
    ],
)
def test_append_source_facts_normalizes_extensionless_filename(
    tmp_path: Path, filename: str, expected_name: str
) -> None:
    """The default directory scan _iter_source_fact_files() runs on every
    later read only recognizes *.jsonl(.gz)/*.json(.gz) -- a caller-
    supplied filename without one of those extensions (e.g.
    append_source_facts(..., filename="tu.gz")) wrote a file
    that scan could never find. The write succeeded with no diagnostic,
    but ingest/validate silently read zero TUs from it -- the same class
    of bug already fixed for compact_inputs_pack's output_filename
    (Codex review, P2)."""
    pack = tmp_path / "abicheck_inputs"
    init_inputs_pack(pack, library="libfoo.so", created_by="abicheck-cc")
    path = append_source_facts(pack, [_tu("foo", mangled="_Z3foov")], filename=filename)
    assert path.name == expected_name

    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert "foo" in names


# -- post-build compaction (P1 #21) -------------------------------------------


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
    rc = run_cc_wrapper(
        ["c++", "-c", "src/foo.cpp"], runner=lambda c: _Proc(0), env={}, emit=boom
    )
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
        ["c++", "-c", "src/foo.cpp"],
        tmp_path,
        inputs_dir=pack,
        library="libfoo.so",
    )
    assert tu is captured
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 1
    assert ingested.manifest.created_by == "abicheck-cc"


def test_emit_captures_all_sources_in_multi_source_compile(
    tmp_path: Path, monkeypatch
) -> None:
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
        ["g++", "-std=c++17", "-c", "a.cpp", "b.cpp"],
        tmp_path,
        inputs_dir=pack,
        library="libfoo.so",
    )
    ingested = ingest_inputs_pack(pack)
    assert ingested.tu_count == 2
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert {"a", "b"} <= names


def test_compile_units_capture_forced_language_source(tmp_path: Path) -> None:
    # `clang++ -x c++ -c generated` builds a real TU with no source extension;
    # forced-language discovery must still capture it.
    units = compile_units_from_command(
        ["clang++", "-x", "c++", "-c", "generated"], tmp_path
    )
    assert [u.source for u in units] == ["generated"]
    assert units[0].language == "CXX"


def test_emit_continues_after_per_tu_extraction_failure(
    tmp_path: Path, monkeypatch
) -> None:
    # In `g++ -c a.cpp b.cpp`, a backend that raises on a.cpp must not drop b.cpp.
    def _extract(cu, *, public_header_roots, target_id=""):
        if Path(cu.source).stem == "a":
            raise RuntimeError("cannot parse a.cpp")
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
        ["g++", "-c", "a.cpp", "b.cpp"],
        tmp_path,
        inputs_dir=pack,
        library="libfoo.so",
    )
    ingested = ingest_inputs_pack(pack)
    names = {e.qualified_name for e in ingested.pack.source_abi.reachable_declarations}
    assert names == {"b"}  # a.cpp failed, b.cpp survived


def test_emit_none_when_no_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, None),
    )
    out = emit_facts_for_command(
        ["c++", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk"
    )
    assert out is None


def test_emit_warns_when_no_backend_resolves(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The old silent-empty behavior (no diagnostic at all) is the bug being
    # regression-tested here, not just the return value.
    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        lambda extractor, **kw: (None, None),
    )
    out = emit_facts_for_command(
        ["icpx", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk"
    )
    assert out is None
    assert "no usable source-ABI extractor" in capsys.readouterr().err


def test_emit_defaults_clang_bin_to_clang_family_wrapped_compiler(
    tmp_path: Path, monkeypatch
) -> None:
    # An icpx/dpcpp-only image never has a bare "clang" on PATH — the wrapper
    # must resolve the clang backend against the compiler it is actually
    # wrapping, not a hardcoded "clang" that silently reads as unavailable.
    captured: dict = {}

    def _fake_select(extractor, **kw):
        captured.update(kw)
        return None, None

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        _fake_select,
    )
    emit_facts_for_command(
        ["icpx", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk"
    )
    assert captured["clang_bin"] == "icpx"


def test_emit_strips_launcher_before_resolving_clang_bin(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict = {}

    def _fake_select(extractor, **kw):
        captured.update(kw)
        return None, None

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        _fake_select,
    )
    emit_facts_for_command(
        ["ccache", "clang++", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk"
    )
    assert captured["clang_bin"] == "clang++"


def test_emit_skips_ccache_config_overrides_before_resolving_clang_bin(
    tmp_path: Path, monkeypatch
) -> None:
    # ccache's own documented invocation form, `ccache KEY=VALUE ... compiler
    # [compiler options]` (ccache manual, "Configuration" section), prefixes
    # the real compiler with bare KEY=VALUE config-override tokens — those
    # must be skipped too, not just the launcher name itself.
    captured: dict = {}

    def _fake_select(extractor, **kw):
        captured.update(kw)
        return None, None

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        _fake_select,
    )
    emit_facts_for_command(
        ["ccache", "compiler_check=content", "icpx", "-c", "src/foo.cpp"],
        tmp_path,
        inputs_dir=tmp_path / "pk",
    )
    assert captured["clang_bin"] == "icpx"


def test_emit_keeps_default_clang_bin_for_non_clang_family_compiler(
    tmp_path: Path, monkeypatch
) -> None:
    # A GCC/MSVC wrapped compiler is not itself a clang-compatible driver —
    # the plain "clang" default (resolved separately on PATH) is unchanged.
    captured: dict = {}

    def _fake_select(extractor, **kw):
        captured.update(kw)
        return None, None

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        _fake_select,
    )
    emit_facts_for_command(
        ["g++", "-c", "src/foo.cpp"], tmp_path, inputs_dir=tmp_path / "pk"
    )
    assert captured["clang_bin"] == "clang"


# -- Generalized clang_bin resolution matrix ----------------------------------
#
# The bug this whole section guards against has one shape, seen twice already
# (a hardcoded "clang" default silently ignoring the real wrapped compiler;
# then a launcher-config-override token silently ignoring it a second way):
# *some* wrapped-compiler spelling reaches the fallback path instead of its
# own real driver, with no diagnostic. A handful of hand-picked examples only
# proves the cases someone thought to write down. This matrix is the
# generalization — every clang-family alias, crossed with every launcher
# wrapping shape (bare / ccache / ccache+overrides / chained launchers),
# checked against the exact same resolver `dump --sources`'s own `--compiler`
# handling uses (`dumper_clang.resolve_source_frontend_clang_bin`), so this
# stays a same-answer check rather than a second, independently-guessable one.


def _resolved_clang_bin(command: list[str], tmp_path: Path, monkeypatch) -> str | None:
    captured: dict = {}

    def _fake_select(extractor, **kw):
        captured.update(kw)
        return None, None

    monkeypatch.setattr(
        "abicheck.buildsource.source_extractors.resolver.select_source_backend",
        _fake_select,
    )
    emit_facts_for_command(command, tmp_path, inputs_dir=tmp_path / "pk")
    return captured.get("clang_bin")


#: Every alias `_is_clang_family_binary` recognizes (dumper_clang.py), plus a
#: versioned/CL-style spelling of each shape it must also cover.
_CLANG_FAMILY_COMPILERS = [
    "clang",
    "clang++",
    "clang-18",
    "clang-cl",
    "clang-cl-20",
    "icx",
    "icpx",
    "dpcpp",
    "dpcpp-cl",
]
#: Real, non-clang-family compilers — must always resolve to the "clang"
#: fallback (a bare "clang" on PATH is a separate, independently-resolved
#: concern; this wrapper must never guess a GCC/MSVC binary is clang-safe).
_NON_CLANG_FAMILY_COMPILERS = ["gcc", "g++", "cc", "c++", "cl", "cl.exe", "icc"]
#: Launcher-wrapping shapes to cross every compiler spelling against.
_LAUNCHER_PREFIXES: list[list[str]] = [
    [],
    ["ccache"],
    ["ccache", "compiler_check=content"],
    ["ccache", "debug=true", "run_second_cpp=false"],
    ["sccache"],
    ["ccache", "distcc"],
]


@pytest.mark.parametrize("compiler", _CLANG_FAMILY_COMPILERS)
@pytest.mark.parametrize(
    "launcher_prefix", _LAUNCHER_PREFIXES, ids=lambda p: "+".join(p) or "bare"
)
def test_clang_family_compiler_always_resolves_regardless_of_launcher(
    compiler: str, launcher_prefix: list[str], tmp_path: Path, monkeypatch
) -> None:
    command = [*launcher_prefix, compiler, "-c", "src/foo.cpp"]
    assert _resolved_clang_bin(command, tmp_path, monkeypatch) == compiler


@pytest.mark.parametrize("compiler", _NON_CLANG_FAMILY_COMPILERS)
@pytest.mark.parametrize(
    "launcher_prefix", _LAUNCHER_PREFIXES, ids=lambda p: "+".join(p) or "bare"
)
def test_non_clang_family_compiler_falls_back_to_plain_clang(
    compiler: str, launcher_prefix: list[str], tmp_path: Path, monkeypatch
) -> None:
    command = [*launcher_prefix, compiler, "-c", "src/foo.cpp"]
    assert _resolved_clang_bin(command, tmp_path, monkeypatch) == "clang"
