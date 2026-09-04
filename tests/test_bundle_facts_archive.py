# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Unit tests for ``BundleFacts``' G40 ``format="archive"`` support.

Split out of ``tests/test_bundle_facts.py`` (a ``debt.yaml``-tracked,
no-growth test module -- new coverage goes in a sibling file instead of
growing it further) -- mirrors that file's own small ``ElfMetadata``
fixture style. The low-level, ``BundleFacts``-agnostic content-addressed
container itself is tested directly in ``tests/test_bundle_archive.py``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from abicheck.bundle_facts import (
    BUNDLE_ARCHIVE_ARTIFACT_TYPE,
    BundleFacts,
    capture_bundle_facts,
)
from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_bundle_facts, save_bundle_facts


def _meta(
    *,
    soname: str = "",
    needed: list[str] | None = None,
    exports: list[str] | None = None,
    imports: list[str] | None = None,
) -> ElfMetadata:
    syms = [ElfSymbol(name=name, visibility="default") for name in exports or []]
    imps = [ElfImport(name=name) for name in imports or []]
    return ElfMetadata(
        soname=soname or "", needed=needed or [], symbols=syms, imports=imps
    )


def _old_metadata() -> dict[str, ElfMetadata]:
    return {
        "libcore.so": _meta(soname="libcore.so", exports=["core_mul", "core_add"]),
        "libalgo.so": _meta(
            soname="libalgo.so",
            needed=["libcore.so"],
            imports=["core_mul"],
        ),
    }


def _per_library_snapshots(metadata: dict[str, ElfMetadata]) -> dict[str, AbiSnapshot]:
    return {
        name: AbiSnapshot(library=name, version="old", elf=meta)
        for name, meta in metadata.items()
    }


def _graph_heavy_snapshot(n: int) -> AbiSnapshot:
    """Real, if repeated, JSON-compressible content at production scale --
    mirrors ``tests/test_snapshot_compression.py``'s identically-named
    helper (not imported from there: that module's own fixture is scoped
    to the plain-JSON snapshot format's compression boundary, and this
    format's own boundary -- a per-*blob* zstd frame inside a zip, not a
    whole-document one -- deserves its own local fixture per this file's
    module docstring). Used by
    ``TestBundleFactsArchiveFormat::test_save_load_round_trip_at_production_scale``
    per AGENTS.md's "Third-party-boundary tests" convention: a toy-sized
    payload's auto-selected zstd window collapses to the content size and
    never exercises the reader's real 128 MiB ``max_window_size`` contract."""
    from abicheck.model import Function, Param, Visibility

    funcs = [
        Function(
            name=f"widget_call_{i}",
            mangled=f"_ZN6widget4callE{i}i",
            return_type="int",
            params=[Param(name="x", type="int"), Param(name="y", type="const char*")],
            visibility=Visibility.PUBLIC,
            source_location=f"/usr/include/widget/detail/generated_{i % 20}.h:{i}",
        )
        for i in range(n)
    ]
    return AbiSnapshot(library="libwidget.so", version="1.0", functions=funcs)


class TestBundleFactsArchiveFormat:
    """G40: ``format="archive"``'s content-addressed zip container, exercised
    at the ``BundleFacts`` level (the low-level container primitive itself
    is tested directly in ``tests/test_bundle_archive.py``)."""

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()), variant_fingerprint="cpu"
        )
        out = tmp_path / "old.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")
        loaded = load_bundle_facts(out)  # "auto" sniff, not forced

        assert loaded.schema_version == facts.schema_version
        assert loaded.variant_fingerprint == "cpu"
        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)
        assert loaded.per_library_snapshots["libcore.so"].elf is not None
        assert loaded.per_library_snapshots["libcore.so"].elf.soname == "libcore.so"

    def test_auto_format_sniff_and_archive_parse_share_one_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A concurrent atomic replacement of *path* between the format
        sniff and a separate follow-up open could swap in a different,
        individually-valid generation the sniff result no longer
        describes -- `load_bundle_facts(format="auto")` must open the
        path exactly once for the archive resolution, not sniff-then-
        reopen (Codex review, fresh evidence)."""
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        # The sniff opens via os.open() (not the builtin open()) as of the
        # FIFO-TOCTOU fix -- see bundle_archive.open_regular_file_for_
        # format_sniff's own comment -- so tracked there instead.
        import os as os_module

        opened_paths: list[object] = []
        real_os_open = os_module.open

        def _tracking_open(file, *a, **kw):  # type: ignore[no-untyped-def]
            if file == out or file == str(out):
                opened_paths.append(file)
            return real_os_open(file, *a, **kw)

        monkeypatch.setattr(os_module, "open", _tracking_open)
        loaded = load_bundle_facts(out)  # "auto"

        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)
        assert len(opened_paths) == 1

    def test_save_load_round_trip_forced_format(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.archive"  # no ".zip" suffix -- format= is explicit
        save_bundle_facts(facts, out, format="archive")
        loaded = load_bundle_facts(out, format="archive")
        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)

    def test_write_result_reports_the_outer_envelope_not_the_inner_codec(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: the saved file is a ZIP (uncompressed
        manifest + zstd-compressed per-blob members), not a raw zstd stream --
        ``detect_snapshot_compression()``/``read_snapshot_storage_info()``
        sniff a file's own magic bytes and would report ``NONE`` for this ZIP
        envelope, never ``ZSTD``. The write result must agree with what an
        independent sniff of the same file would say, not describe an internal
        per-member codec as if it were the whole file's compression."""
        from abicheck.snapshot_io import (
            SnapshotCompression,
            detect_snapshot_compression,
        )

        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.bundlefacts.archive.zip"
        result = save_bundle_facts(facts, out, format="archive")

        assert result.compression == SnapshotCompression.NONE
        assert detect_snapshot_compression(out) == result.compression

    def test_save_load_round_trip_at_production_scale(self, tmp_path: Path) -> None:
        """The postmortem-shaped counterpart to
        ``test_snapshot_compression.py::test_zstd_round_trip_at_production_scale_and_level``
        (ADR-059 §12), for *this* format's own compression boundary
        (Codex review, fresh evidence): the existing archive-format fixtures
        (this file, ``tests/test_bundle_archive.py``) all use ~5 KiB blobs,
        which exercise the container's *shape* -- dedup, member layout,
        manifest round-trip -- but never the reader's real 128 MiB
        ``max_window_size`` contract or production-level-19 compression
        behavior, since a payload that small collapses zstd's auto-selected
        window straight down to the content size regardless of the cap.
        Scaled past the same ~8 MiB threshold that fixture's own comment
        documents as where the window stops collapsing, and driven through
        the actual public entry points (``save_bundle_facts``/
        ``load_bundle_facts``), not the lower-level ``BundleArchiveWriter``/
        ``BundleArchiveReader`` primitives directly."""
        import json

        zstandard = pytest.importorskip("zstandard")
        from abicheck.serialization import snapshot_to_dict

        snap = _graph_heavy_snapshot(n=8600)
        facts = capture_bundle_facts({"libwidget.so": snap})
        serialized_size = len(json.dumps(snapshot_to_dict(snap)).encode())
        assert serialized_size > 8 * 1024 * 1024  # past the window-collapse point

        out = tmp_path / "production_scale.bundlefacts.archive.zip"
        result = save_bundle_facts(facts, out, format="archive")

        # Sanity-check the premise before trusting the round trip below:
        # the real per-blob writer (level=19, no explicit window override)
        # must actually produce a non-trivial window -- if a future
        # zstandard/libzstd upgrade changed that auto-selection, this
        # assertion (not a silent pass) is what would say the fixture needs
        # revisiting, mirroring the snapshot_io.py sibling test's own
        # reasoning exactly.
        with zipfile.ZipFile(out) as zf:
            blob_names = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_names) == 1
            raw_frame = zf.read(blob_names[0])
        frame = zstandard.get_frame_parameters(raw_frame)
        assert frame.window_size >= 4 * 1024 * 1024  # nowhere near collapsed to 0

        # ADR-063 Phase 5 grew every Function's own serialized shape with a
        # Fact[...] sibling per case-(b) field, so this fixture's real
        # container-node count now exceeds DEFAULT_MAX_JSON_OBJECT_NODES --
        # a known-large, trusted payload by construction (built above, not
        # untrusted input), which is exactly the escape hatch the guard's
        # own error message names.
        loaded = load_bundle_facts(
            out, format="archive", max_json_object_nodes=5_000_000
        )
        loaded_snap = loaded.per_library_snapshots["libwidget.so"]
        assert loaded_snap.functions is not None
        assert len(loaded_snap.functions) == len(snap.functions)
        assert loaded_snap.functions[-1].name == snap.functions[-1].name
        assert result.stored_sha256 is not None

    def test_round_trip_with_non_null_instantiation_manifest(
        self, tmp_path: Path
    ) -> None:
        """The manifest-blob gap a Codex review caught in the G40 *plan*
        before any code existed (docs/contribute/plans/g40-...: an earlier
        draft never specified where a non-null InstantiationManifest's
        content actually lives in the archive) -- pinned here at the
        implementation level so it can't silently regress."""
        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="core_mul", optional_provider=False),)
        )
        facts = capture_bundle_facts(
            _per_library_snapshots(_old_metadata()), manifest=manifest
        )
        out = tmp_path / "old.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")
        loaded = load_bundle_facts(out)

        assert loaded.manifest is not None
        assert len(loaded.manifest.entries) == 1
        assert loaded.manifest.entries[0].symbol == "core_mul"

    def test_no_manifest_round_trips_to_none_and_allocates_no_manifest_blob(
        self, tmp_path: Path
    ) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        assert facts.manifest is None
        out = tmp_path / "old.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        loaded = load_bundle_facts(out)
        assert loaded.manifest is None

        # Exactly one blob member per library, none for the absent manifest.
        with zipfile.ZipFile(out) as zf:
            blob_members = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_members) == len(facts.per_library_snapshots)

    def test_dedup_across_libraries_with_identical_snapshots(
        self, tmp_path: Path
    ) -> None:
        """Two libraries whose captured AbiSnapshot serializes identically
        share one blob -- BundleArchiveWriter.put_blob's own hash-addressed
        dedup, exercised here through the real BundleFacts save path."""
        snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={"a.so": snap, "b.so": snap},
        )
        out = tmp_path / "dup.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        with zipfile.ZipFile(out) as zf:
            blob_members = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_members) == 1

        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == {"a.so", "b.so"}

    def test_load_gives_each_name_its_own_snapshot_object_even_when_sharing_a_blob(
        self, tmp_path: Path
    ) -> None:
        """CodeRabbit review: two library names sharing one content-hash
        blob must still get *independent* AbiSnapshot objects in the
        loaded mapping -- AbiSnapshot is a mutable dataclass and
        BundleFacts is public API, so a caller mutating one entry must
        never be able to reach through to another entry's own object
        (matching serialization.bundle_facts_from_dict()'s existing
        one-instance-per-entry contract). Parsed-object *caching* (the
        thing the dedup this test's predecessor guarded) is preserved
        separately -- this only asserts the returned objects don't alias."""
        snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(per_library_snapshots={"a.so": snap, "b.so": snap})
        out = tmp_path / "dup.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        loaded = load_bundle_facts(out)
        assert (
            loaded.per_library_snapshots["a.so"]
            is not loaded.per_library_snapshots["b.so"]
        )
        assert (
            loaded.per_library_snapshots["a.so"] == loaded.per_library_snapshots["b.so"]
        )
        # Mutating one must not leak into the other.
        loaded.per_library_snapshots["a.so"].version = "mutated"
        assert loaded.per_library_snapshots["b.so"].version != "mutated"

    def test_save_produces_identical_bytes_regardless_of_dict_insertion_order(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence: two logically-equal BundleFacts
        values populated in different insertion order must still produce
        byte-identical archives -- library_blobs/filesystem_aliases/
        library_filenames are unordered-by-name maps, not order-sensitive
        content, so their key order in the written manifest must not leak
        Python dict insertion order into the archive's own bytes."""
        snaps = _per_library_snapshots(_old_metadata())
        forward = BundleFacts(per_library_snapshots=dict(snaps.items()))
        reversed_facts = BundleFacts(
            per_library_snapshots=dict(reversed(list(snaps.items())))
        )
        out_forward = tmp_path / "forward.bundlefacts.archive.zip"
        out_reversed = tmp_path / "reversed.bundlefacts.archive.zip"
        r1 = save_bundle_facts(forward, out_forward, format="archive")
        r2 = save_bundle_facts(reversed_facts, out_reversed, format="archive")

        assert r1.stored_sha256 == r2.stored_sha256
        assert out_forward.read_bytes() == out_reversed.read_bytes()

    def test_load_reads_a_shared_blob_exactly_once_across_library_names(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two library names sharing one blob (the dedup this format
        provides) decompress that blob once on load, not once per name --
        both a CPU-waste fix and a step toward the aggregate cap actually
        bounding real distinct content rather than being trivially defeated
        by re-reading the same bytes repeatedly (Codex review)."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(per_library_snapshots={"a.so": snap, "b.so": snap})
        out = tmp_path / "dup.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        real_read_blob = bundle_archive_module.BundleArchiveReader.read_blob
        call_count = 0

        def _counting_read_blob(self, content_hash_hex, **kw):  # type: ignore[no-untyped-def]
            nonlocal call_count
            call_count += 1
            return real_read_blob(self, content_hash_hex, **kw)

        monkeypatch.setattr(
            bundle_archive_module.BundleArchiveReader, "read_blob", _counting_read_blob
        )
        loaded = load_bundle_facts(out)
        assert set(loaded.per_library_snapshots) == {"a.so", "b.so"}
        assert call_count == 1

    def test_back_compat_plain_json_fixture_still_loads_via_auto(
        self, tmp_path: Path
    ) -> None:
        """Every plain-JSON ``BundleFacts`` file this repo already produces
        keeps loading unchanged once the archive format ships -- no re-save
        required."""
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, out)  # format="json" default, unchanged
        loaded = load_bundle_facts(out)  # format="auto" default, unchanged
        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)

    def test_save_default_format_is_json_not_auto(self, tmp_path: Path) -> None:
        """save_bundle_facts() has no "auto" format -- there is nothing to
        sniff at a not-yet-written destination path. Confirmed by checking
        the written file's own magic bytes rather than trusting the
        parameter name."""
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "default_format.bundlefacts"
        save_bundle_facts(facts, out)
        with open(out, "rb") as f:
            prefix = f.read(4)
        assert not prefix.startswith(b"PK")  # not a zip archive

    def test_save_unknown_format_raises(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        with pytest.raises(ValueError, match="unknown format"):
            save_bundle_facts(facts, tmp_path / "x", format="yaml")

    def test_load_unknown_format_raises(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.bundlefacts.json"
        save_bundle_facts(facts, out)
        with pytest.raises(ValueError, match="unknown format"):
            load_bundle_facts(out, format="yaml")

    def test_load_rejects_a_newer_container_schema_version(
        self, tmp_path: Path
    ) -> None:
        """The container's own schema_version (manifest/blob shape) is a
        separate axis from bundle_facts_schema_version -- a newer one must
        fail closed, not be silently misread as version 1 (Codex review)."""
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "future.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 999,
                    "library_blobs": {},
                }
            )

        with pytest.raises(IncompatibleSnapshotSchemaError, match="999"):
            load_bundle_facts(out, format="archive")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("schema_version", 0),
            ("schema_version", -1),
            ("bundle_facts_schema_version", 0),
            ("bundle_facts_schema_version", -1),
        ],
    )
    def test_load_rejects_a_schema_version_that_never_existed(
        self, tmp_path: Path, field: str, value: int
    ) -> None:
        """0 or a negative integer never existed as a real schema version
        -- only checking the *upper* bound let it silently masquerade as
        v1's layout instead of failing closed the same way a too-new
        version already does (Codex review, fresh evidence)."""
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "never_existed_schema_version.bundlefacts.archive.zip"
        manifest: dict[str, object] = {
            "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
            "schema_version": 1,
            "bundle_facts_schema_version": 1,
            "library_blobs": {},
        }
        manifest[field] = value
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(manifest)

        with pytest.raises(IncompatibleSnapshotSchemaError, match=str(value)):
            load_bundle_facts(out, format="archive")

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("schema_version", 1.9),
            ("schema_version", True),
            ("schema_version", "1"),
            ("schema_version", None),
            ("bundle_facts_schema_version", 1.9),
            ("bundle_facts_schema_version", True),
            ("bundle_facts_schema_version", "1"),
            ("bundle_facts_schema_version", None),
        ],
    )
    def test_load_rejects_a_non_integer_schema_version(
        self, tmp_path: Path, field: str, value: object
    ) -> None:
        """A bare ``int()`` coercion silently truncates 1.9 -> 1, accepts
        True/False as 1/0 (bool is an int subclass), parses the string
        "1" as if it were a real JSON integer, and leaks a raw TypeError
        for None -- a malformed or hostile manifest could otherwise read
        as a supported schema version, or crash with the wrong exception
        type, instead of failing this module's own SnapshotError contract
        (Codex review, fresh evidence)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "bad-schema-version.bundlefacts.archive.zip"
        manifest: dict[str, object] = {
            "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
            "schema_version": 1,
            "bundle_facts_schema_version": 1,
            "library_blobs": {},
        }
        manifest[field] = value
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(manifest)

        with pytest.raises(SnapshotError, match=f"{field} must be an integer"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_manifest_missing_library_blobs(
        self, tmp_path: Path
    ) -> None:
        """A manifest.json with no 'library_blobs' key at all (a malformed
        or unrelated zip) must be rejected outright rather than silently
        loading as an empty, valid-looking BundleFacts (Codex review)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "malformed.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                }
            )

        with pytest.raises(ValueError, match="library_blobs"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_non_string_library_blobs_value(
        self, tmp_path: Path
    ) -> None:
        """A 'library_blobs' value that isn't a string must raise this
        module's own error type -- not a raw, unhashable-type TypeError out
        of snapshot_cache.get(h)/blob_cache.get(h) (CodeRabbit review)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "bad_value.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {"a.so": ["not", "a", "hash"]},
                }
            )

        with pytest.raises(ValueError, match="library_blobs\\['a.so'\\]"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_non_string_manifest_blob(self, tmp_path: Path) -> None:
        """Same class of bug as 'library_blobs' values above, for the
        second blob-reference field: a non-string 'manifest_blob' reaches
        _cached_blob()'s own blob_cache.get(h), a keyed dict, raising a
        raw unhashable-type TypeError instead of this module's own error
        vocabulary (Codex review, fresh evidence)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "bad_manifest_blob.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {},
                    "manifest_blob": ["not", "a", "hash"],
                }
            )

        with pytest.raises(ValueError, match="manifest_blob"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_library_blob_that_decodes_to_a_non_dict(
        self, tmp_path: Path
    ) -> None:
        """reader.read_blob() verifies the blob's *hash*, not its JSON
        *shape* -- a blob that legitimately decodes to a list or scalar
        must raise this module's normal error type, not a raw
        AttributeError out of snapshot_from_dict (CodeRabbit review)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "bad_shape.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(b"[1, 2, 3]")
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {"a.so": h},
                }
            )

        with pytest.raises(ValueError, match="must decode to a JSON object"):
            load_bundle_facts(out, format="archive")

    def test_load_translates_a_malformed_nested_snapshot_shape(
        self, tmp_path: Path
    ) -> None:
        """A library blob that decodes to a JSON *object* (passing the
        prior check) but has a malformed nested shape -- e.g. a null
        entry in `functions` -- makes `snapshot_from_dict()` leak a raw
        TypeError/KeyError/AttributeError instead of this module's own
        error vocabulary (Codex review, fresh evidence)."""
        import json as json_module

        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "malformed_snapshot_shape.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(
                json_module.dumps({"functions": [None]}).encode("utf-8")
            )
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {"a.so": h},
                }
            )

        with pytest.raises(SnapshotError, match="malformed snapshot shape"):
            load_bundle_facts(out, format="archive")

    def test_load_translates_a_deeply_nested_library_blob(self, tmp_path: Path) -> None:
        """A content-addressed library blob is just as reachable by a
        hostile archive as manifest.json is -- a deeply nested payload
        (`[[[...]]]`) blows Python's json decoder's own recursion budget,
        which must not surface as a raw `RecursionError` (Codex review,
        fresh evidence)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "deeply_nested_blob.bundlefacts.archive.zip"
        deeply_nested = ("[" * 10_000) + ("]" * 10_000)
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(deeply_nested.encode("utf-8"))
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {"a.so": h},
                }
            )

        with pytest.raises(SnapshotError, match="too deeply nested"):
            load_bundle_facts(out, format="archive")

    def test_load_translates_a_deeply_nested_manifest_blob(
        self, tmp_path: Path
    ) -> None:
        """Same as above, for the second blob-decode site (`manifest_blob`,
        the `InstantiationManifest` payload)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "deeply_nested_manifest_blob.bundlefacts.archive.zip"
        deeply_nested = ("[" * 10_000) + ("]" * 10_000)
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(deeply_nested.encode("utf-8"))
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {},
                    "manifest_blob": h,
                }
            )

        with pytest.raises(SnapshotError, match="too deeply nested"):
            load_bundle_facts(out, format="archive")

    def test_load_translates_a_recursion_error_when_cloning_a_shared_snapshot(
        self, tmp_path: Path
    ) -> None:
        """Two library names sharing one blob hash take a different code
        path for the second name -- copy.deepcopy() on the already-
        deserialized snapshot, not snapshot_from_dict() again. A
        sufficiently deep value (Python's json C decoder handles far
        deeper nesting than deepcopy's own pure-Python recursion) can
        blow deepcopy's own recursion budget even though the first name's
        own snapshot_from_dict() already succeeded (Codex review, fresh
        evidence)."""
        import json as json_module

        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "recursion_on_clone.bundlefacts.archive.zip"
        depth = 900  # json.loads succeeds here; copy.deepcopy does not
        nested = json_module.loads(("[" * depth) + ("]" * depth))
        payload = json_module.dumps(
            {"library": "a.so", "version": "1", "constants": {"DEEP": nested}}
        )
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(payload.encode("utf-8"))
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {"a.so": h, "b.so": h},
                }
            )

        with pytest.raises(SnapshotError, match="too deeply nested"):
            load_bundle_facts(out, format="archive")

    def test_load_translates_invalid_json_in_a_library_blob(
        self, tmp_path: Path
    ) -> None:
        """Even ordinary malformed JSON (not just a recursion-limit
        payload) in a blob must translate to this module's own error
        vocabulary, not a raw `json.JSONDecodeError`."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "invalid_json_blob.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(b"{not valid json")
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {"a.so": h},
                }
            )

        with pytest.raises(SnapshotError, match="not valid JSON"):
            load_bundle_facts(out, format="archive")

    def test_save_rejects_a_non_default_compression_for_archive_format(
        self, tmp_path: Path
    ) -> None:
        """compression= is JSON-format-only -- an archive's blobs are
        always zstd (ADR-059), so a non-default request against
        format="archive" must be a usage error, not silently ignored
        (CodeRabbit review)."""
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        with pytest.raises(ValueError, match="compression"):
            save_bundle_facts(
                facts, tmp_path / "x.zip", format="archive", compression="gzip"
            )

    def test_save_rejects_zstd_compression_for_archive_format(
        self, tmp_path: Path
    ) -> None:
        """Same as the gzip case above -- an explicit outer "zstd" request
        is equally meaningless for a format whose blobs are already always
        zstd-compressed, one layer down."""
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        with pytest.raises(ValueError, match="compression"):
            save_bundle_facts(
                facts, tmp_path / "x.zip", format="archive", compression="zstd"
            )

    def test_save_accepts_explicit_none_compression_for_archive_format(
        self, tmp_path: Path
    ) -> None:
        """compression="none" is semantically compatible with
        format="archive": the archive format has no *outer* envelope
        compression layer regardless (each blob is independently
        zstd-compressed inside it), which is exactly what "none" already
        means -- unlike "gzip"/"zstd", it must be accepted as a no-op, not
        rejected identically to those (Codex review, fresh evidence: the
        previous check rejected any value other than the literal string
        "auto", "none" included, even though it's compatible)."""
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "x.zip"
        save_bundle_facts(facts, out, format="archive", compression="none")
        loaded = load_bundle_facts(out, format="archive")
        assert loaded.per_library_snapshots.keys() == facts.per_library_snapshots.keys()


# ---------------------------------------------------------------------------
# artifact_type discriminator on the archive container itself (CLI cleanup
# phase two, PR I prerequisite -- "The archive container ... is a separate
# axis and gets the same treatment" as the plain-JSON BundleFacts document).
# Unlike that document's own marker, this one is required, not defaulted:
# the archive format has never shipped in a release.
# ---------------------------------------------------------------------------


class TestBundleFactsArchiveArtifactTypeDiscriminator:
    def test_save_writes_the_marker(self, tmp_path: Path) -> None:
        import json as _json
        import zipfile as _zipfile

        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "x.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        with _zipfile.ZipFile(out) as zf:
            manifest = _json.loads(zf.read("manifest.json"))
        assert manifest["artifact_type"] == BUNDLE_ARCHIVE_ARTIFACT_TYPE

    def test_load_rejects_a_missing_artifact_type(self, tmp_path: Path) -> None:
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "no_marker.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {},
                }
            )

        with pytest.raises(IncompatibleSnapshotSchemaError, match="artifact_type"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_mismatched_artifact_type(self, tmp_path: Path) -> None:
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "wrong_marker.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": "something-else",
                    "schema_version": 1,
                    "bundle_facts_schema_version": 1,
                    "library_blobs": {},
                }
            )

        with pytest.raises(IncompatibleSnapshotSchemaError, match="something-else"):
            load_bundle_facts(out, format="archive")

    def test_save_stamps_the_current_facts_schema_version_not_a_preserved_one(
        self, tmp_path: Path
    ) -> None:
        # Codex review, fresh evidence: the manifest/blob shape this writer
        # produces is always the current BundleFacts representation --
        # writing a *loaded* facts object's own preserved schema_version
        # (e.g. 1, from a legacy v1 document) into
        # bundle_facts_schema_version would claim v1 while the archive
        # actually contains the current shape, mirroring the bug already
        # fixed for bundle_facts_to_dict()'s own schema_version field.
        import json as _json
        import zipfile as _zipfile

        from abicheck.bundle_facts import BUNDLE_FACTS_SCHEMA_VERSION

        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        facts.schema_version = 1  # simulate a loaded legacy v1 document
        out = tmp_path / "stamped.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        with _zipfile.ZipFile(out) as zf:
            manifest = _json.loads(zf.read("manifest.json"))
        assert manifest["bundle_facts_schema_version"] == BUNDLE_FACTS_SCHEMA_VERSION

        # And the reloaded object reflects the stamped, current version --
        # not the preserved 1 the in-memory facts object claimed.
        reloaded = load_bundle_facts(out, format="archive")
        assert reloaded.schema_version == BUNDLE_FACTS_SCHEMA_VERSION

    def test_load_rejects_a_non_string_variant_fingerprint(
        self, tmp_path: Path
    ) -> None:
        """The archive manifest's own ``variant_fingerprint`` field must go
        through the same ``validated_variant_fingerprint()`` check the
        plain-JSON loader uses, not a bare ``str(...)`` coercion -- a
        malformed ``variant_fingerprint: 1`` and a genuine
        ``variant_fingerprint: "1"`` must not both load as the identical
        string ``"1"`` (Codex review, PR #1060, round 10). The check now
        delegates to ``storage.guards.identity_text()`` (round 11), which
        raises ``TypeError`` rather than a bare ``ValueError``."""
        from abicheck.bundle_facts import BUNDLE_FACTS_SCHEMA_VERSION
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "bad-fingerprint.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest(
                {
                    "artifact_type": BUNDLE_ARCHIVE_ARTIFACT_TYPE,
                    "schema_version": 1,
                    "bundle_facts_schema_version": BUNDLE_FACTS_SCHEMA_VERSION,
                    "variant_fingerprint": 1,
                    "library_blobs": {},
                }
            )

        with pytest.raises(TypeError, match="variant_fingerprint"):
            load_bundle_facts(out, format="archive")
