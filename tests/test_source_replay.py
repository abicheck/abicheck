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

"""Tests for the ADR-030 phase-7 source ABI replay orchestration.

Scope selection, the per-TU cache, and the replay driver are pure (no real
extractor); a fake extractor stands in so the whole pipeline is exercised in the
fast lane.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit, Target
from abicheck.buildsource.source_abi import SourceAbiTu, SourceEntity, SourceLocation
from abicheck.buildsource.source_extractors.base import SourceExtractionError
from abicheck.buildsource.source_replay import (
    CI_MODE_TO_SCOPE,
    REPLAY_SCOPES,
    SourceAbiCache,
    compute_tu_cache_key,
    public_header_roots_for,
    run_source_replay,
    scope_for_ci_mode,
    select_compile_units,
)


def _cu(cu_id: str, source: str, target_id: str = "", **kw: object) -> CompileUnit:
    return CompileUnit(id=cu_id, source=source, target_id=target_id, language="CXX", **kw)  # type: ignore[arg-type]


def _build() -> BuildEvidence:
    return BuildEvidence(
        targets=[
            Target(
                id="target://libfoo",
                public_headers=["include/foo.h"],
                private_headers=["src/internal.h"],
            ),
            Target(id="target://libbar", public_headers=["include/bar.h"]),
        ],
        compile_units=[
            _cu("cu://a", "src/a.cpp", "target://libfoo"),
            _cu("cu://b", "src/b.cpp", "target://libfoo"),
            _cu("cu://c", "src/c.cpp", "target://libbar"),
            _cu("cu://d", "src/d.cpp", ""),  # not attached to a target
        ],
    )


class _FakeExtractor:
    """A SourceAbiExtractor that records calls and returns a canned per-TU dump."""

    name = "fake-source"
    version = "9.9"

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_for = fail_for or set()

    def extract(self, compile_unit, *, public_header_roots, target_id=""):  # type: ignore[no-untyped-def]
        self.calls.append(compile_unit.id)
        if compile_unit.id in self.fail_for:
            raise SourceExtractionError(f"boom for {compile_unit.id}")
        ent = SourceEntity(
            id=f"id::{compile_unit.id}",
            kind="function",
            qualified_name=f"fn_{compile_unit.source}",
            mangled_name=f"_Z{len(compile_unit.id)}",
            source_location=SourceLocation(path="include/foo.h", origin="PUBLIC_HEADER"),
            visibility="public_header",
            api_relevant=True,
        )
        return SourceAbiTu(
            tu_id=compile_unit.id,
            target_id=target_id or compile_unit.target_id,
            extractor={"name": self.name, "version": self.version},
            source=compile_unit.source,
            public_header_roots=list(public_header_roots),
            functions=[ent],
        )


# -- scope selection ---------------------------------------------------------


def test_scope_off_selects_nothing() -> None:
    assert select_compile_units(_build(), scope="off") == []


def test_scope_full_selects_every_unit() -> None:
    units = select_compile_units(_build(), scope="full")
    assert {u.id for u in units} == {"cu://a", "cu://b", "cu://c", "cu://d"}


def test_scope_target_selects_units_of_target() -> None:
    units = select_compile_units(_build(), scope="target", target_id="target://libfoo")
    assert {u.id for u in units} == {"cu://a", "cu://b"}


def test_scope_target_without_id_uses_attached_units() -> None:
    # No explicit target: every unit attached to *some* target (drops cu://d).
    units = select_compile_units(_build(), scope="target")
    assert {u.id for u in units} == {"cu://a", "cu://b", "cu://c"}


def test_scope_headers_only_picks_one_unit_per_header_target() -> None:
    units = select_compile_units(_build(), scope="headers-only")
    # First (by id) unit of each target that declares public headers.
    assert {u.id for u in units} == {"cu://a", "cu://c"}


def test_scope_headers_only_falls_back_when_no_public_headers() -> None:
    build = BuildEvidence(compile_units=[_cu("cu://x", "x.cpp")])
    # No targets/public headers to scope by → fall back to all units.
    assert {u.id for u in select_compile_units(build, scope="headers-only")} == {"cu://x"}


def test_scope_changed_matches_source_paths() -> None:
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["src/b.cpp"]
    )
    assert {u.id for u in units} == {"cu://b"}


def test_scope_changed_includes_units_of_target_owning_changed_header() -> None:
    # Editing a public header of libfoo pulls in every libfoo TU (they include it).
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["include/foo.h"]
    )
    assert {u.id for u in units} == {"cu://a", "cu://b"}


def test_scope_changed_matches_absolute_vs_relative_paths() -> None:
    build = BuildEvidence(
        compile_units=[_cu("cu://abs", "/work/repo/src/a.cpp", "target://t")]
    )
    units = select_compile_units(build, scope="changed", changed_paths=["src/a.cpp"])
    assert {u.id for u in units} == {"cu://abs"}


def test_scope_changed_empty_paths_selects_nothing() -> None:
    assert select_compile_units(_build(), scope="changed", changed_paths=[]) == []


def test_scope_changed_falls_back_for_header_without_target_metadata() -> None:
    # Codex #339 P2: compile-DB-only evidence (compile units, no Target records),
    # a changed *header* that maps to no TU must fail open to all TUs, not select
    # nothing — else source-only header changes vanish from PR-mode replay.
    build = BuildEvidence(
        compile_units=[_cu("cu://a", "src/a.cpp"), _cu("cu://b", "src/b.cpp")]
    )
    units = select_compile_units(build, scope="changed", changed_paths=["include/api.h"])
    assert {u.id for u in units} == {"cu://a", "cu://b"}


def test_scope_changed_non_header_no_match_stays_empty() -> None:
    # A changed *non-header* that matches no source must NOT trigger the
    # fail-open header fallback (no over-broad replay for an unrelated file).
    build = BuildEvidence(compile_units=[_cu("cu://a", "src/a.cpp")])
    assert select_compile_units(build, scope="changed", changed_paths=["README.md"]) == []


def test_scope_changed_unowned_header_fails_open_despite_target_metadata() -> None:
    # Codex #339 P2: even when targets carry header metadata, a changed header
    # that no target owns must fail open to all TUs, not select nothing. Target
    # public/private header lists name a target's *own* headers, not the
    # transitive private headers it includes (e.g. a config header pulled in by
    # a public header), so an unowned header can still affect any TU. The per-TU
    # cache then skips units whose read_files did not actually change.
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["include/detail/config.h"]
    )
    assert {u.id for u in units} == {u.id for u in _build().compile_units}


# -- scope selection with an include graph (ADR-030 follow-up #4) -------------


def test_headers_only_set_cover_picks_minimal_units() -> None:
    # One target owning both its public headers: the greedy cover picks the single
    # owning TU that includes both, not one representative per source file.
    build = BuildEvidence(
        targets=[Target(id="t", public_headers=["include/a.h", "include/b.h"])],
        compile_units=[
            _cu("cu://x", "x.cpp", "t"),
            _cu("cu://y", "y.cpp", "t"),
        ],
    )
    include_map = {
        "cu://x": ["include/a.h", "include/b.h"],  # owns + includes both
        "cu://y": ["include/a.h"],
    }
    units = select_compile_units(build, scope="headers-only", include_map=include_map)
    assert {u.id for u in units} == {"cu://x"}


def test_headers_only_set_cover_ignores_non_owning_includer() -> None:
    # A downstream TU (different target) that merely *includes* a public header
    # must NOT cover it — only the owning target's TU may, so the header is
    # fingerprinted under the right compile context (Codex review).
    build = BuildEvidence(
        targets=[
            Target(id="lib", public_headers=["include/foo.h"]),
            Target(id="app", public_headers=[]),
        ],
        compile_units=[
            _cu("cu://lib", "lib.cpp", "lib"),
            _cu("cu://app", "app.cpp", "app"),
        ],
    )
    # The app TU includes foo.h but does not own it; the lib TU owns it.
    include_map = {
        "cu://app": ["include/foo.h"],
        "cu://lib": ["include/foo.h"],
    }
    units = select_compile_units(build, scope="headers-only", include_map=include_map)
    assert {u.id for u in units} == {"cu://lib"}


def test_headers_only_set_cover_needs_two_units() -> None:
    # No single TU covers both headers → cover needs two (one per header).
    include_map = {
        "cu://b": ["include/foo.h"],
        "cu://c": ["include/bar.h"],
    }
    units = select_compile_units(
        _build(), scope="headers-only", include_map=include_map
    )
    assert {u.id for u in units} == {"cu://b", "cu://c"}


def test_headers_only_falls_back_when_include_graph_covers_no_header() -> None:
    # Include graph present but reaches none of the public headers → defer to the
    # representative-per-target heuristic rather than emit an empty surface.
    include_map = {"cu://a": ["src/unrelated.h"]}
    units = select_compile_units(
        _build(), scope="headers-only", include_map=include_map
    )
    assert {u.id for u in units} == {"cu://a", "cu://c"}


def test_headers_only_partial_cover_falls_back_to_heuristic() -> None:
    # Graph reaches foo.h (via cu://b) but NOT bar.h → the greedy cover cannot
    # satisfy every public header, so defer to the representative-per-target
    # heuristic rather than drop bar.h's TUs (Codex review).
    include_map = {"cu://b": ["include/foo.h"]}
    units = select_compile_units(
        _build(), scope="headers-only", include_map=include_map
    )
    assert {u.id for u in units} == {"cu://a", "cu://c"}


def test_changed_with_include_graph_is_precise() -> None:
    # Only cu://a actually includes the changed header; cu://b is in the same
    # target but does NOT include it, so target-ownership would over-select it.
    include_map = {
        "cu://a": ["include/foo.h"],
        "cu://b": ["include/other.h"],
        "cu://c": ["include/bar.h"],
        "cu://d": ["include/bar.h"],
    }
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["include/foo.h"],
        include_map=include_map,
    )
    assert {u.id for u in units} == {"cu://a"}


def test_changed_full_include_graph_no_match_selects_nothing() -> None:
    # Every TU is covered by the graph and none includes the changed header →
    # authoritative: select nothing (no fail-open fan-out).
    include_map = {
        "cu://a": ["include/other.h"],
        "cu://b": ["include/other.h"],
        "cu://c": ["include/other.h"],
        "cu://d": ["include/other.h"],
    }
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["include/ghost.h"],
        include_map=include_map,
    )
    assert units == []


def test_changed_partial_include_graph_falls_back_to_fan_out() -> None:
    # Graph covers only some TUs and the changed header matches none of them →
    # a header changed with incomplete coverage still fails open to all units.
    include_map = {"cu://a": ["include/other.h"]}
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["include/ghost.h"],
        include_map=include_map,
    )
    assert {u.id for u in units} == {u.id for u in _build().compile_units}


def test_changed_with_graph_still_matches_changed_source() -> None:
    # A TU whose own source changed is selected even if the graph says it
    # includes nothing relevant.
    include_map = {"cu://b": ["include/other.h"]}
    units = select_compile_units(
        _build(), scope="changed", changed_paths=["src/b.cpp"],
        include_map=include_map,
    )
    assert {u.id for u in units} == {"cu://b"}


def test_unknown_scope_raises() -> None:
    with pytest.raises(ValueError, match="unknown replay scope"):
        select_compile_units(_build(), scope="bogus")


def test_public_header_roots_collected_from_targets() -> None:
    assert public_header_roots_for(_build()) == ["include/bar.h", "include/foo.h"]
    assert public_header_roots_for(_build(), "target://libfoo") == ["include/foo.h"]


# -- CI mode mapping (ADR-033 D2) --------------------------------------------


def test_ci_mode_to_scope_mapping() -> None:
    assert scope_for_ci_mode("source-changed") == "changed"
    assert scope_for_ci_mode("source-target") == "target"
    assert scope_for_ci_mode("build") == "off"
    # Every mapped scope is a real replay scope.
    assert set(CI_MODE_TO_SCOPE.values()) <= set(REPLAY_SCOPES)


def test_ci_mode_unknown_fails_safe_to_off() -> None:
    assert scope_for_ci_mode("totally-unknown") == "off"


# -- per-TU cache (ADR-030 D8) -----------------------------------------------


def test_cache_key_is_none_when_source_unreadable() -> None:
    # No file on disk → uncacheable (prefer a false miss over a false hit, D8).
    key = compute_tu_cache_key(
        extractor_name="clang-source",
        extractor_version="0.1",
        compile_unit=_cu("cu://x", "does/not/exist.cpp"),
        public_header_roots=[],
    )
    assert key is None


def test_cache_key_changes_with_source_content(tmp_path: Path) -> None:
    src = tmp_path / "foo.cpp"
    src.write_text("int a;\n")
    cu = _cu("cu://x", str(src))
    k1 = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[],
    )
    src.write_text("int a; int b;\n")
    k2 = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[],
    )
    assert k1 and k2 and k1 != k2


def test_cache_key_changes_with_extractor_and_flags(tmp_path: Path) -> None:
    src = tmp_path / "foo.cpp"
    src.write_text("int a;\n")
    cu = _cu("cu://x", str(src), standard="c++17")
    base = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[],
    )
    other_tool = compute_tu_cache_key(
        extractor_name="castxml-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[],
    )
    cu_flag = _cu("cu://x", str(src), standard="c++17", abi_relevant_flags=["-m32"])
    flagged = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu_flag, public_header_roots=[],
    )
    assert base != other_tool != flagged != base


def test_cache_key_changes_with_header_content(tmp_path: Path) -> None:
    src = tmp_path / "foo.cpp"
    src.write_text("int a;\n")
    hdr = tmp_path / "foo.h"
    hdr.write_text("int x;\n")
    cu = _cu("cu://x", str(src))
    k1 = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[str(hdr)],
    )
    hdr.write_text("int x; int y;\n")
    k2 = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[str(hdr)],
    )
    assert k1 and k2 and k1 != k2


def test_cache_roundtrip_and_miss(tmp_path: Path) -> None:
    cache = SourceAbiCache(tmp_path / "cache")
    assert cache.get("missing") is None
    assert cache.get(None) is None  # uncacheable key is always a miss
    tu = SourceAbiTu(tu_id="cu://x", functions=[SourceEntity(id="e", kind="function")])
    cache.put("k1", tu)
    loaded = cache.get("k1")
    assert loaded is not None and loaded.tu_id == "cu://x"
    cache.put(None, tu)  # no-op, must not raise


def test_cache_invalidates_when_included_header_changes(tmp_path: Path) -> None:
    # Codex #339 P1: a TU that included a private header (not a configured root)
    # must miss the cache once that header changes, or stale inline/default/
    # constexpr facts are linked and a real source ABI change is silently lost.
    hdr = tmp_path / "detail" / "config.h"
    hdr.parent.mkdir()
    hdr.write_text("#define N 1\n")
    cache = SourceAbiCache(tmp_path / "cache")
    tu = SourceAbiTu(tu_id="cu://x", read_files=[str(hdr)])
    cache.put("k1", tu)
    assert cache.get("k1") is not None  # unchanged dependency → hit
    hdr.write_text("#define N 2\n")  # edit the transitively included header
    assert cache.get("k1") is None  # changed dependency → miss (re-extract)


def test_cache_invalidates_when_dependency_deleted(tmp_path: Path) -> None:
    hdr = tmp_path / "h.h"
    hdr.write_text("x\n")
    cache = SourceAbiCache(tmp_path / "cache")
    cache.put("k1", SourceAbiTu(tu_id="cu://x", read_files=[str(hdr)]))
    hdr.unlink()  # a vanished dependency must miss, not hit (prefer false miss)
    assert cache.get("k1") is None


def test_cache_get_ignores_corrupt_entry(tmp_path: Path) -> None:
    cache = SourceAbiCache(tmp_path / "cache")
    cache.cache_dir.mkdir(parents=True)
    (cache.cache_dir / "k.json").write_text("{ not valid json")
    assert cache.get("k") is None  # corrupt entry is a miss, never an error


def test_cache_get_ignores_non_object_entry(tmp_path: Path) -> None:
    cache = SourceAbiCache(tmp_path / "cache")
    cache.cache_dir.mkdir(parents=True)
    (cache.cache_dir / "k.json").write_text("[1, 2, 3]")
    assert cache.get("k") is None


def test_cache_get_ignores_non_dict_deps(tmp_path: Path) -> None:
    # CodeRabbit: a malformed entry (deps not a dict) is a miss, never a crash.
    import json as _json

    cache = SourceAbiCache(tmp_path / "cache")
    cache.cache_dir.mkdir(parents=True)
    (cache.cache_dir / "k.json").write_text(
        _json.dumps({"deps": "not-a-dict", "tu": {"tu_id": "x"}})
    )
    assert cache.get("k") is None


def test_cache_get_ignores_bad_tu_payload(tmp_path: Path) -> None:
    # A structurally bad `tu` payload (missing required entity id) is a miss.
    import json as _json

    cache = SourceAbiCache(tmp_path / "cache")
    cache.cache_dir.mkdir(parents=True)
    # An entity dict without "id" makes SourceEntity.from_dict raise KeyError.
    (cache.cache_dir / "k.json").write_text(
        _json.dumps({"deps": {}, "tu": {"tu_id": "x", "functions": [{}]}})
    )
    assert cache.get("k") is None


def test_cache_key_changes_with_argv_forced_include(tmp_path: Path) -> None:
    # Codex #339 P2: a forced-include change lives only in argv (not the
    # structured fields), so the key must fold in the replayed argv flags or a
    # `-include old.h` -> `-include new.h` swap would reuse a stale dump.
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    old = _cu("cu://x", str(src), argv=["clang++", "-include", "old.h", "-c", "a.cpp"])
    new = _cu("cu://x", str(src), argv=["clang++", "-include", "new.h", "-c", "a.cpp"])
    k_old = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=old, public_header_roots=[],
    )
    k_new = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=new, public_header_roots=[],
    )
    assert k_old and k_new and k_old != k_new


def test_cache_key_changes_with_iquote_path(tmp_path: Path) -> None:
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    a = _cu("cu://x", str(src), argv=["clang++", "-iquote", "dirA", "-c", "a.cpp"])
    b = _cu("cu://x", str(src), argv=["clang++", "-iquote", "dirB", "-c", "a.cpp"])
    ka = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=a, public_header_roots=[],
    )
    kb = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=b, public_header_roots=[],
    )
    assert ka and kb and ka != kb


def test_cache_key_includes_source_location(tmp_path: Path) -> None:
    # CodeRabbit: two distinct TUs with identical content must not collide.
    src_a = tmp_path / "a" / "foo.cpp"
    src_b = tmp_path / "b" / "foo.cpp"
    src_a.parent.mkdir()
    src_b.parent.mkdir()
    src_a.write_text("int x;\n")
    src_b.write_text("int x;\n")  # identical content, different location
    key_a = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=_cu("cu://a", str(src_a)), public_header_roots=[],
    )
    key_b = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=_cu("cu://b", str(src_b)), public_header_roots=[],
    )
    assert key_a and key_b and key_a != key_b


def test_cache_key_changes_with_header_directory_content(tmp_path: Path) -> None:
    # A header *root that is a directory* folds in every contained file's content,
    # so editing any header under it invalidates the key (_digest_path dir branch).
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "h.h").write_text("int x;\n")
    cu = _cu("cu://x", str(src))
    k1 = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[str(inc)],
    )
    (inc / "h.h").write_text("int x; int y;\n")
    k2 = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=[str(inc)],
    )
    assert k1 and k2 and k1 != k2


def test_cache_key_uses_path_string_for_missing_root(tmp_path: Path) -> None:
    # A missing header root contributes only its path string (the source being
    # readable is what makes the TU cacheable; an absent root is not a miss-maker).
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    cu = _cu("cu://x", str(src))
    key = compute_tu_cache_key(
        extractor_name="clang-source", extractor_version="0.1",
        compile_unit=cu, public_header_roots=["does/not/exist.h"],
    )
    assert key is not None


# -- driver ------------------------------------------------------------------


def test_run_source_replay_links_selected_units() -> None:
    extractor = _FakeExtractor()
    surface, diagnostics = run_source_replay(
        _build(), extractor, scope="target", target_id="target://libfoo",
        public_header_roots=["include/foo.h"],
    )
    assert diagnostics == []
    assert extractor.calls == ["cu://a", "cu://b"]
    assert len(surface.reachable_declarations) == 2
    assert surface.coverage["replay_scope"] == "target"
    assert surface.coverage["compile_units_parsed"] == 2


def test_run_source_replay_forwards_include_graph_for_precise_changed() -> None:
    extractor = _FakeExtractor()
    surface, _ = run_source_replay(
        _build(), extractor, scope="changed",
        changed_paths=["include/foo.h"],
        public_header_roots=["include/foo.h"],
        include_map={"cu://a": ["include/foo.h"], "cu://b": ["include/other.h"],
                     "cu://c": ["include/bar.h"], "cu://d": ["include/bar.h"]},
    )
    # Only the TU that includes the changed header is parsed (precise mapping).
    assert extractor.calls == ["cu://a"]
    assert surface.coverage["include_graph_used"] is True


def test_run_source_replay_records_failures_as_diagnostics() -> None:
    extractor = _FakeExtractor(fail_for={"cu://b"})
    surface, diagnostics = run_source_replay(
        _build(), extractor, scope="target", target_id="target://libfoo",
        public_header_roots=["include/foo.h"],
    )
    # cu://b failed → recorded as a diagnostic, cu://a still linked (partial L4).
    assert len(diagnostics) == 1 and "cu://b" in diagnostics[0]
    assert len(surface.reachable_declarations) == 1
    assert surface.coverage["extractor_failures"] == 1


def test_run_source_replay_off_scope_is_empty() -> None:
    extractor = _FakeExtractor()
    surface, diagnostics = run_source_replay(_build(), extractor, scope="off")
    assert extractor.calls == [] and diagnostics == []
    assert surface.reachable_declarations == []


def test_run_source_replay_uses_cache_to_skip_reextraction(tmp_path: Path) -> None:
    # A real on-disk source so the cache key is computable.
    src = tmp_path / "a.cpp"
    src.write_text("int a;\n")
    build = BuildEvidence(
        targets=[Target(id="target://t", public_headers=["foo.h"])],
        compile_units=[_cu("cu://a", str(src), "target://t")],
    )
    cache = SourceAbiCache(tmp_path / "cache")
    first = _FakeExtractor()
    run_source_replay(
        build, first, scope="full", public_header_roots=[], cache=cache,
    )
    assert first.calls == ["cu://a"]
    # Second run hits the cache: the extractor is never called again.
    second = _FakeExtractor()
    surface, _ = run_source_replay(
        build, second, scope="full", public_header_roots=[], cache=cache,
    )
    assert second.calls == []
    assert len(surface.reachable_declarations) == 1


# ── ADR-033 D3 PR-diff localizer ─────────────────────────────────────────────


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("paths,expected", [
    (["CMakeLists.txt"], "build"),
    (["cmake/foo.cmake"], "build"),
    (["Makefile", "docs/x.md"], "build"),
    (["BUILD.bazel"], "build"),
    (["meson.build"], "build"),
    (["src/foo.cpp"], "source-changed"),
    (["include/foo.hpp"], "source-changed"),
    (["src/foo.cpp", "CMakeLists.txt"], "source-changed"),  # source wins (superset)
    (["README.md", "docs/x.rst"], "off"),
    ([], "off"),
])
def test_recommend_collect_mode(paths, expected):
    from abicheck.buildsource.source_replay import recommend_collect_mode
    assert recommend_collect_mode(paths) == expected


def test_graph_full_maps_to_full_scope():
    """ADR-033 D2 (Codex): graph-full collects the full replay scope, not target."""
    from abicheck.buildsource.source_replay import (
        collection_for_ci_mode,
        scope_for_ci_mode,
    )
    assert scope_for_ci_mode("graph-full") == "full"
    scope, layers = collection_for_ci_mode("graph-full")
    assert scope == "full"
    assert layers == ("L3", "L4", "L5")
