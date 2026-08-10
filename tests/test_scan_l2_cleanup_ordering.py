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


def test_scan_l2_seed_cleanup_runs_before_embed(monkeypatch, tmp_path):
    events: list[str] = []
    seed_kwargs: dict = {}

    def fake_seed(**kwargs):
        seed_kwargs.update(kwargs)
        events.append("seed")
        # Faithful to the real seed_l2_includes: an inferred-CMake seed produces a
        # flock-release cleanup that, given a defer_cleanup list, is pushed there
        # (and returned as [] pending) — otherwise returned pending for the caller
        # to drain locally. So with the *bug* (outer list passed) the cleanup lands
        # on the outer list and never runs before embed; with the fix (None) it
        # comes back pending and the finally drains it first.
        cleanup = lambda: events.append("cleanup")  # noqa: E731
        defer = kwargs["defer_cleanup"]
        if defer is not None:
            defer.append(cleanup)
            return list(kwargs["includes"]), []
        return list(kwargs["includes"]), [cleanup]

    def fake_resolve(*args, **kwargs):
        events.append("resolve")
        return object()

    def fake_embed(*args, **kwargs):
        events.append("embed")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve)
    monkeypatch.setattr("abicheck.cli_buildsource.embed_build_source", fake_embed)

    sources = tmp_path / "src"
    sources.mkdir()
    _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=sources,
        collect_mode="build",  # non-"off" → embed_build_source runs
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],  # the outer scan list — the seed must NOT use it
    )

    # The seed was told to keep its cleanups local (not on the outer list), so the
    # flock releases in the finally before embed's own inferred query.
    assert seed_kwargs.get("defer_cleanup") is None
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
        return object()

    monkeypatch.setattr(
        "abicheck.buildsource.l2_seed.seed_l2_includes",
        lambda **kwargs: (list(kwargs["includes"]), []),
    )
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


def test_scan_returns_seeded_includes_for_baseline(monkeypatch, tmp_path):
    # _build_new_snapshot returns the *effective* (seeded) includes so a --baseline
    # compare can header-parse the old native library with the same build-derived
    # dependency include dirs (Codex review).
    seeded = tmp_path / "buildinc"
    seeded.mkdir()

    def fake_seed(**kwargs):
        return [seeded], []  # seed adds a build-derived dir, no cleanup

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **k: object())
    monkeypatch.setattr(
        "abicheck.cli_buildsource.embed_build_source", lambda *a, **k: None
    )

    snap, eff_includes = _build_new_snapshot(
        binary=tmp_path / "lib.so",
        headers=[tmp_path / "h.h"],
        includes=[],
        sources=tmp_path,
        collect_mode="build",
        lang="c++",
        allow_build_query=False,
        defer_cleanup=[],
    )
    assert seeded in eff_includes  # effective includes carry the seed for the baseline


def test_scan_l2_seed_cleanup_runs_even_when_resolve_raises(monkeypatch, tmp_path):
    # The flock must be released on the error path too (finally), so a failed L2
    # parse still can't wedge a later inferred query.
    events: list[str] = []

    def fake_seed(**kwargs):
        return list(kwargs["includes"]), [lambda: events.append("cleanup")]

    def fake_resolve(*args, **kwargs):
        from abicheck.errors import AbicheckError

        raise AbicheckError("boom")

    monkeypatch.setattr("abicheck.buildsource.l2_seed.seed_l2_includes", fake_seed)
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
    ``dump --include-dependencies`` baseline workflow -- scan has no
    ``--include-dependencies`` flag of its own, so the candidate's mode is
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
