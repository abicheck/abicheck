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

"""The scan L2 include-seeding cleanup must run *locally* (before L3/L4 collection
replays its own inferred build query), not on the outer scan cleanup list.

Regression for a self-deadlock: the seed may run the inferred-CMake query and hold
its build dir under an exclusive flock until the cleanup runs. embed_build_source()
runs a second inferred query in the same call; deferring the seed cleanup to the
outer drain (which happens after embed) would make that second query block on the
still-held lock until the 600s timeout (Codex review)."""

from __future__ import annotations

from abicheck.cli_scan import _build_new_snapshot
from abicheck.compile_context import CompileContext
from abicheck.model import AbiSnapshot


def _stub_snapshot() -> AbiSnapshot:
    """A stand-in for what ``service.resolve_input`` really returns.

    Deliberately a real :class:`AbiSnapshot` rather than a bare ``object()``:
    ``_build_new_snapshot`` reads several of its fields after the call (the
    ``parsed_with_build_context`` stamp, the ADR-039 collector's own gate), so
    a stub that answers none of them only passes as long as every one of those
    reads happens to be short-circuited by something else first.
    """
    return AbiSnapshot(library="lib.so", version="1.0")


#: Keyword names shared verbatim between seed_includes_and_fold_compile_context's
#: own signature and CompileContext's fields -- one place to update if either
#: gains a field, instead of three hand-copied constructors (CodeRabbit review).
_CC_FIELDS = (
    "gcc_path",
    "gcc_prefix",
    "gcc_options",
    "gcc_option_tokens",
    "sysroot",
    "nostdinc",
    "frontend",
    "frontend_context",
)


def _explicit_ctx_from_kwargs(kwargs: dict) -> CompileContext:
    return CompileContext(**{k: kwargs[k] for k in _CC_FIELDS})


def test_scan_l2_seed_cleanup_runs_before_embed(monkeypatch, tmp_path):
    events: list[str] = []
    seed_kwargs: dict = {}

    def fake_seed_and_fold(**kwargs):
        seed_kwargs.update(kwargs)
        events.append("seed")
        # Faithful to the real seed_includes_and_fold_compile_context: an
        # inferred-CMake seed produces a flock-release cleanup, appended to
        # the caller-owned pending_cleanups list -- which _build_new_snapshot
        # must keep LOCAL (its own fresh list, never the outer scan
        # defer_cleanup) and drain in its finally, before embed_build_source
        # below replays its own inferred query on the same lock.
        kwargs["pending_cleanups"].append(lambda: events.append("cleanup"))
        explicit_ctx = _explicit_ctx_from_kwargs(kwargs)
        return list(kwargs["includes"]), False, explicit_ctx, ()

    def fake_resolve(*args, **kwargs):
        events.append("resolve")
        return _stub_snapshot()

    def fake_embed(*args, **kwargs):
        events.append("embed")

    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)
    monkeypatch.setattr("abicheck.buildsource.embed.embed_build_source", fake_embed)

    sources = tmp_path / "src"
    sources.mkdir()
    outer_cleanups: list = []
    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=sources,
        collect_mode="build",  # non-"off" → embed_build_source runs
        lang="c++",
        allow_build_query=False,
        defer_cleanup=outer_cleanups,  # the outer scan list — the seed must NOT use it
    )

    # The seed+fold's pending cleanups were a genuinely different list object
    # from the outer scan list (Codex review: `is not None` alone also
    # passes for the outer list itself, since defer_cleanup=[] is never None
    # either — this must catch a regression that reuses it).
    assert seed_kwargs["pending_cleanups"] is not outer_cleanups
    assert outer_cleanups == []
    # Ordering invariant: seed → resolve → cleanup (flock release) → embed.
    assert events == ["seed", "resolve", "cleanup", "embed"]
    assert events.index("cleanup") < events.index("embed")


def test_scan_candidate_filters_dependency_scope_by_default(monkeypatch, tmp_path):
    """Codex review: scan's own candidate resolve_input() call used to leave
    include_dependencies at its True/"full" default, mismatching a `dump`-
    produced --against baseline's "filtered" default and hard-failing the
    new comparability gate on the routine "scan against a plain dump'd
    baseline" workflow."""
    resolve_kwargs: dict = {}

    def fake_resolve(*args, **kwargs):
        resolve_kwargs.update(kwargs)
        return _stub_snapshot()

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=None,
        collect_mode="off",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
    )

    assert resolve_kwargs["include_dependencies"] is False


def test_scan_candidate_folds_l3_compile_context_into_header_parse(
    monkeypatch, tmp_path
):
    """P0.3 L3->L2 fold (AGENTS.md's former "The native ELF `abicheck dump`
    path never applies L3 build context..." known gap -- scan's own
    candidate resolution shared the identical gap, closed here alongside
    dump/PE-Mach-O): `_build_new_snapshot` calls `service.resolve_input`
    directly, not `resolve_side_snapshot`, so a real compile database's
    `-std=`/`-D` flags never reached the candidate's own header-AST parse.
    Without this a scan candidate and a dump-produced baseline of the same
    project resolved under genuinely different extraction recipes and were
    rejected as NOT_COMPARABLE for reasons neither command's own
    diagnostics named."""
    import json

    from abicheck.model import AbiSnapshot

    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "c++",
                        "-c",
                        str(src),
                        "-o",
                        "out.o",
                        "-std=c++20",
                        "-DFOO=1",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_resolve(*args, **kwargs):
        captured["compile"] = kwargs["compile"]
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)
    monkeypatch.setattr(
        "abicheck.buildsource.embed.embed_build_source", lambda *a, **k: None
    )

    _res = _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[hdr],
        includes=[],
        sources=tmp_path,
        collect_mode="source-target",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
    )

    tokens = captured["compile"].gcc_option_tokens
    assert "-std=c++20" in tokens
    assert "-DFOO=1" in tokens
    assert _res.snapshot.parsed_with_build_context is True


def test_scan_candidate_lang_c_omits_conflicting_derived_cxx_standard(
    monkeypatch, tmp_path
):
    """Codex review, PR #782: `lang="c"` is never scan's own Click default
    (only "c++" is), so it is always a genuine explicit request -- the fold
    must treat it as such (lang_explicit=True) rather than the hard-coded
    False the first revision of this fix used, or a matched C++ compile
    unit's own -std=c++20 would reach a parse `scan --lang c` is explicitly
    forcing into C mode, which a real compiler rejects outright."""
    import json

    from abicheck.model import AbiSnapshot

    hdr = tmp_path / "widget.h"
    hdr.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": ["c++", "-c", str(src), "-o", "out.o", "-std=c++20"],
                }
            ]
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_resolve(*args, **kwargs):
        captured["compile"] = kwargs["compile"]
        return AbiSnapshot(library="lib.so", version="1.0", from_headers=True)

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)
    monkeypatch.setattr(
        "abicheck.buildsource.embed.embed_build_source", lambda *a, **k: None
    )

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[hdr],
        includes=[],
        sources=tmp_path,
        collect_mode="source-target",
        lang="c",
        allow_build_query=False,
        defer_cleanup=[],
    )

    tokens = captured["compile"].gcc_option_tokens
    assert not any(t.startswith("-std=") for t in tokens)


def test_scan_returns_seeded_includes_for_baseline(monkeypatch, tmp_path):
    # _build_new_snapshot returns the *effective* (seeded) includes so a --baseline
    # compare can header-parse the old native library with the same build-derived
    # dependency include dirs (Codex review).
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    def fake_seed_and_fold(**kwargs):
        # seed adds a build-derived dir, no cleanup
        return [seeded], False, _explicit_ctx_from_kwargs(kwargs), ()

    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr(
        "abicheck.service.resolve_input", lambda *a, **k: _stub_snapshot()
    )
    monkeypatch.setattr(
        "abicheck.buildsource.embed.embed_build_source", lambda *a, **k: None
    )

    _res = _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=tmp_path,
        collect_mode="build",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
    )
    # effective includes carry the seed for the baseline
    assert seeded in _res.effective_includes


def test_scan_candidate_expands_public_header_dirs_before_embed(monkeypatch, tmp_path):
    """PR C (dump/scan resolver convergence) — pinned the OTHER way after a
    real regression was caught by review.

    An earlier revision of this call switched ``embed_build_source``'s
    ``public_headers``/``public_header_dirs`` to the simpler, unexpanded raw
    pass-through ``service_input_resolution.embed_side_build_source`` uses,
    reasoning that ``source_extractors._argv.split_public_roots``/
    ``_ClassifyContext`` already classify a directory root correctly via
    prefix/segment matching, making the expansion redundant. That reasoning
    holds for *that* consumer — but a *second*, differently-shaped consumer
    of the same ``public_header_roots`` list,
    ``clang_public_roots._equivalent_public_roots_for_unit`` (the
    install-tree-vs-build-tree "mirror detection" heuristic), needs >= 2
    sampled header matches to promote a *directory* root as an equivalent
    build-tree root, but promotes on a single match for a *file* root — so a
    build include directory that happens to mirror only one header out of a
    larger public root lost that promotion entirely once the directory
    stopped being pre-expanded (Codex review on #804, confirmed by direct
    reproduction against ``_equivalent_public_roots_for_unit`` itself before
    this revert). Reverted to keep the expansion. This test pins the correct
    (expanded) shape so a future "simplify this like the other primitive"
    pass doesn't reintroduce the same regression.
    """
    pub_dir = tmp_path / "include"
    pub_dir.mkdir()
    (pub_dir / "api.h").write_text("void api(void);\n", encoding="utf-8")
    pub_file = tmp_path / "standalone.h"
    pub_file.write_text("void standalone(void);\n", encoding="utf-8")

    monkeypatch.setattr(
        "abicheck.service.resolve_input", lambda *a, **k: _stub_snapshot()
    )

    embed_kwargs: dict = {}

    def fake_embed(*args, **kwargs):
        embed_kwargs.update(kwargs)

    monkeypatch.setattr("abicheck.buildsource.embed.embed_build_source", fake_embed)

    _build_new_snapshot(
        # Empty `headers` (PR 3A, dump/scan L4 root-set convergence): this
        # test's subject is `public_headers`/`public_header_dirs`'s own
        # expansion, and `headers` now separately contributes to the same L4
        # set (see `_build_new_snapshot`'s own comment) -- a nonexistent
        # placeholder path here would make `expand_public_header_inputs`'s
        # best-effort expansion degrade to a raw pass-through for everything,
        # not just isolate the one axis this test means to pin.
        binary=tmp_path / "lib.so",
        headers=[],
        includes=[],
        sources=tmp_path,
        collect_mode="build",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
        public_headers=[pub_file],
        public_header_dirs=[pub_dir],
    )

    # public_headers carries the individually-expanded files from BOTH the
    # standalone file and the directory's own contents; public_header_dirs
    # stays as given (also fed to embed_build_source, which unions both).
    assert set(embed_kwargs["public_headers"]) == {
        str(pub_file),
        str(pub_dir / "api.h"),
    }
    assert embed_kwargs["public_header_dirs"] == (str(pub_dir),)


def test_scan_candidate_widens_l4_roots_with_a_lone_header_file(monkeypatch, tmp_path):
    """A lone ``-H`` *file* with no directory reaches L4 replay even though it
    does not activate L2/crosscheck-origin provenance (PR 3A, dump/scan L4
    root-set convergence).

    ``cli_scan_baseline._public_provenance_set`` deliberately returns
    ``([], [])`` for exactly this shape (a lone file can't establish a public
    directory boundary), so a real ``scan --against`` a real ``dump``
    baseline for such a project silently degraded L4 source-ABI replay to
    zero matched declarations while the dump-produced baseline (whose
    write-time embed derives its own roots via the more permissive
    ``split_public_header_inputs``, same as ``compare``'s implicit-dump
    operand) matched normally -- producing a spurious
    ``source_decl_binary_symbol_mismatch``/``source_to_binary_mapping_changed``
    RISK finding on an otherwise-unchanged library purely from this L2-vs-L4
    asymmetry. Reproduced end to end in
    ``tests/test_dump_scan_l3_comparability.py``; this pins the mechanism
    directly. Confirmed to fail against the pre-fix ``_build_new_snapshot``
    (``embed_kwargs["public_headers"]`` was empty).
    """
    lone_header = tmp_path / "widget.h"
    lone_header.write_text("struct Widget { int x; };\n", encoding="utf-8")

    monkeypatch.setattr(
        "abicheck.service.resolve_input", lambda *a, **k: _stub_snapshot()
    )

    embed_kwargs: dict = {}

    def fake_embed(*args, **kwargs):
        embed_kwargs.update(kwargs)

    monkeypatch.setattr("abicheck.buildsource.embed.embed_build_source", fake_embed)

    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[lone_header],
        includes=[],
        sources=tmp_path,
        collect_mode="build",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
        # The narrow, `_public_provenance_set`-derived set for this shape --
        # empty, since a lone file establishes no directory boundary.
        public_headers=[],
        public_header_dirs=[],
    )

    assert embed_kwargs["public_headers"] == (str(lone_header),)


def test_scan_l2_seed_cleanup_runs_even_when_resolve_raises(monkeypatch, tmp_path):
    # The flock must be released on the error path too (finally), so a failed L2
    # parse still can't wedge a later inferred query.
    events: list[str] = []

    def fake_seed_and_fold(**kwargs):
        kwargs["pending_cleanups"].append(lambda: events.append("cleanup"))
        explicit_ctx = _explicit_ctx_from_kwargs(kwargs)
        return list(kwargs["includes"]), False, explicit_ctx, ()

    def fake_resolve(*args, **kwargs):
        from abicheck.errors import AbicheckError

        raise AbicheckError("boom")

    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
        fake_seed_and_fold,
    )
    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)

    import click
    import pytest

    with pytest.raises(click.ClickException):
        _build_new_snapshot(
            binary=tmp_path / "lib.so",
            headers=[tmp_path / "h.h"],
            includes=[],
            sources=tmp_path,
            collect_mode="build",
            lang="c++",
            allow_build_query=False,
            defer_cleanup=[],
        )
    assert events == ["cleanup"]  # released despite the failure


class TestScanCandidateIncludeDependencies:
    """Codex review, fresh evidence: ``scan``'s candidate now defaults to
    filtered dependency scope (matching a default ``dump`` baseline), but
    that alone would hard-break the inverse, explicit
    ``dump --include-system-declarations`` baseline workflow -- scan has no
    ``--include-system-declarations`` flag of its own, so the candidate's mode is
    derived from a JSON baseline's own explicit tag instead."""

    def test_no_baseline_defaults_to_filtered(self):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        assert _scan_candidate_include_dependencies(None) is False

    def test_native_baseline_defaults_to_filtered(self, tmp_path):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        native = tmp_path / "libfoo.so"
        native.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert _scan_candidate_include_dependencies(native) is False

    def test_native_baseline_short_circuits_before_reading_as_text(
        self, tmp_path, monkeypatch
    ):
        """Codex review, fresh evidence: a recognized magic number must
        short-circuit to the filtered default without ever opening the file
        as UTF-8 text -- json.load()'s fp.read() would otherwise decode the
        entire file merely to fail parsing it, a real, avoidable memory/I/O
        cost for a large native baseline."""
        from abicheck import scan_engine

        native = tmp_path / "libfoo.so"
        native.write_bytes(b"\x7fELF" + b"\x00" * 100)

        def fail_if_opened(*args, **kwargs):
            raise AssertionError("must not open the native binary as text")

        monkeypatch.setattr(scan_engine, "open", fail_if_opened, raising=False)
        assert scan_engine._scan_candidate_include_dependencies(native) is False

    def test_json_baseline_tagged_full_matches_full(self, tmp_path):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.json"
        baseline.write_text('{"dependency_scope": "full"}', encoding="utf-8")
        assert _scan_candidate_include_dependencies(baseline) is True

    def test_project_snapshot_package_dir_tagged_full_matches_full(self, tmp_path):
        """ADR-062/063 storage-v2 (Codex review): a `--against` operand can
        also be a `ProjectSnapshot` package *directory* (`dump
        --project-snapshot-dir --include-system-declarations`), not just a
        JSON file. Every branch above this test opens `baseline` as a file,
        so a directory used to fall through -- silently defaulting to
        filtered -- instead of being read the same way a JSON baseline is."""
        from abicheck.model.snapshot import AbiSnapshot
        from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
        from abicheck.scan_engine import _scan_candidate_include_dependencies
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict

        snap = AbiSnapshot(
            library="libfoo.so.1", version="1.0.0", dependency_scope="full"
        )
        root = tmp_path / "pkg"
        write_legacy_snapshot_package(
            snapshot_to_dict(snap),
            root,
            artifact_id=snap.library,
            max_known_schema_version=SCHEMA_VERSION,
        )
        assert _scan_candidate_include_dependencies(root) is True

    def test_project_snapshot_package_dir_tagged_filtered_stays_filtered(
        self, tmp_path
    ):
        from abicheck.model.snapshot import AbiSnapshot
        from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
        from abicheck.scan_engine import _scan_candidate_include_dependencies
        from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict

        snap = AbiSnapshot(
            library="libfoo.so.1", version="1.0.0", dependency_scope="filtered"
        )
        root = tmp_path / "pkg"
        write_legacy_snapshot_package(
            snapshot_to_dict(snap),
            root,
            artifact_id=snap.library,
            max_known_schema_version=SCHEMA_VERSION,
        )
        assert _scan_candidate_include_dependencies(root) is False

    def test_plain_directory_stays_filtered(self, tmp_path):
        """A directory that is not a real ProjectSnapshot package (no
        manifest.json) must fall back to the filtered default, not raise."""
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        plain_dir = tmp_path / "not_a_package"
        plain_dir.mkdir()
        assert _scan_candidate_include_dependencies(plain_dir) is False

    def test_json_baseline_tagged_filtered_stays_filtered(self, tmp_path):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.json"
        baseline.write_text('{"dependency_scope": "filtered"}', encoding="utf-8")
        assert _scan_candidate_include_dependencies(baseline) is False

    def test_json_baseline_with_no_tag_stays_filtered(self, tmp_path):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.json"
        baseline.write_text("{}", encoding="utf-8")
        assert _scan_candidate_include_dependencies(baseline) is False

    def test_json_baseline_with_library_like_name_still_read(self, tmp_path):
        """Codex review, fresh evidence: a real JSON snapshot saved under a
        library-like filename (no recognized binary magic bytes, so
        cli_scan_baseline._baseline_is_native_library falls back to its
        ".so" in name filename heuristic and would call this native) must
        still have its dependency_scope tag read -- pre-filtering on that
        helper before attempting the JSON parse would silently skip the
        peek for exactly this baseline shape."""
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "libfoo.so"
        baseline.write_text('{"dependency_scope": "full"}', encoding="utf-8")
        assert _scan_candidate_include_dependencies(baseline) is True

    def test_unreadable_json_baseline_falls_back_to_filtered(self, tmp_path):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.json"
        baseline.write_text("not json", encoding="utf-8")
        assert _scan_candidate_include_dependencies(baseline) is False

    def test_json_baseline_with_non_object_top_level_falls_back_to_filtered(
        self, tmp_path
    ):
        """Codex review: valid JSON whose top level isn't a mapping (e.g. a
        bare `[]`) must not raise AttributeError out of this best-effort
        helper -- it should degrade to the filtered default like any other
        unreadable/malformed baseline."""
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.json"
        baseline.write_text("[]", encoding="utf-8")
        assert _scan_candidate_include_dependencies(baseline) is False

    def test_large_full_baseline_avoids_full_json_parse(self, tmp_path, monkeypatch):
        """Codex review, fresh evidence: dependency_scope is one of
        AbiSnapshot's last serialized fields (model.py), so a real
        `dump`-produced snapshot (json.dumps(..., indent=2), never
        minified) carries the tag within the file's last ~4KB regardless of
        how large the functions/types/DWARF payload before it is -- an
        explicitly unfiltered "full" snapshot is precisely the mode most
        likely to carry the largest such payload. The tail-scan fast path
        must resolve this without ever calling json.load."""
        import json as json_mod

        from abicheck import scan_engine
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        huge_payload = {"functions": [{"name": f"f{i}"} for i in range(50_000)]}
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json_mod.dumps(
                {**huge_payload, "dependency_scope": "full", "schema_version": 18},
                indent=2,
            ),
            encoding="utf-8",
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("must not fully json.load a large baseline")

        monkeypatch.setattr(scan_engine.json, "load", fail_if_called)
        assert _scan_candidate_include_dependencies(baseline) is True

    def test_large_filtered_baseline_avoids_full_json_parse(
        self, tmp_path, monkeypatch
    ):
        import json as json_mod

        from abicheck import scan_engine
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        huge_payload = {"functions": [{"name": f"f{i}"} for i in range(50_000)]}
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json_mod.dumps(
                {**huge_payload, "dependency_scope": "filtered", "schema_version": 18},
                indent=2,
            ),
            encoding="utf-8",
        )

        def fail_if_called(*args, **kwargs):
            raise AssertionError("must not fully json.load a large baseline")

        monkeypatch.setattr(scan_engine.json, "load", fail_if_called)
        assert _scan_candidate_include_dependencies(baseline) is False

    def test_tail_scan_miss_falls_back_to_full_parse(self, tmp_path):
        """A file whose tail doesn't confidently resolve the tag (e.g. the
        key sits earlier than the last 4KB, an unusual formatting choice)
        must still fall back to the full json.load path rather than
        silently guessing wrong."""
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.json"
        padding = " " * 8192
        baseline.write_text(
            '{"dependency_scope": "full", "padding": "' + padding + '"}',
            encoding="utf-8",
        )
        assert _scan_candidate_include_dependencies(baseline) is True


class TestScanCandidateIncludeDependenciesCompressed:
    """Codex review, PR #699 (ADR-059): a compressed baseline's raw stored
    bytes aren't JSON text, so neither the tail-byte-scan heuristic nor a
    plain-text json.load could ever find dependency_scope in them -- both
    silently failed closed to the filtered default regardless of the real
    tag, hard-failing the inverse "candidate implicitly matches an
    unfiltered baseline" workflow via NOT_COMPARABLE."""

    def test_gzip_baseline_tagged_full_matches_full(self, tmp_path):
        import json as json_mod

        from abicheck.scan_engine import _scan_candidate_include_dependencies
        from abicheck.snapshot_io import SnapshotCompression, write_snapshot_text

        baseline = tmp_path / "baseline.abicheck.json.gz"
        write_snapshot_text(
            json_mod.dumps({"dependency_scope": "full", "schema_version": 18}),
            baseline,
            compression=SnapshotCompression.GZIP,
        )
        assert _scan_candidate_include_dependencies(baseline) is True

    def test_zstd_baseline_tagged_full_matches_full(self, tmp_path):
        import json as json_mod

        from abicheck.scan_engine import _scan_candidate_include_dependencies
        from abicheck.snapshot_io import SnapshotCompression, write_snapshot_text

        baseline = tmp_path / "baseline.abicheck.json.zst"
        write_snapshot_text(
            json_mod.dumps({"dependency_scope": "full", "schema_version": 18}),
            baseline,
            compression=SnapshotCompression.ZSTD,
        )
        assert _scan_candidate_include_dependencies(baseline) is True

    def test_compressed_baseline_tagged_filtered_stays_filtered(self, tmp_path):
        import json as json_mod

        from abicheck.scan_engine import _scan_candidate_include_dependencies
        from abicheck.snapshot_io import SnapshotCompression, write_snapshot_text

        baseline = tmp_path / "baseline.abicheck.json.gz"
        write_snapshot_text(
            json_mod.dumps({"dependency_scope": "filtered", "schema_version": 18}),
            baseline,
            compression=SnapshotCompression.GZIP,
        )
        assert _scan_candidate_include_dependencies(baseline) is False

    def test_corrupt_compressed_baseline_falls_back_to_filtered(self, tmp_path):
        from abicheck.scan_engine import _scan_candidate_include_dependencies

        baseline = tmp_path / "baseline.abicheck.json.gz"
        baseline.write_bytes(b"\x1f\x8b\x08\x00not a real gzip stream")
        assert _scan_candidate_include_dependencies(baseline) is False
