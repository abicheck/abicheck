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

from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
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

    def test_save_load_round_trip_forced_format(self, tmp_path: Path) -> None:
        facts = capture_bundle_facts(_per_library_snapshots(_old_metadata()))
        out = tmp_path / "old.archive"  # no ".zip" suffix -- format= is explicit
        save_bundle_facts(facts, out, format="archive")
        loaded = load_bundle_facts(out, format="archive")
        assert set(loaded.per_library_snapshots) == set(facts.per_library_snapshots)

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
        assert loaded.per_library_snapshots["a.so"] is not loaded.per_library_snapshots["b.so"]
        assert loaded.per_library_snapshots["a.so"] == loaded.per_library_snapshots["b.so"]
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

    def test_load_rejects_a_bundle_exceeding_the_aggregate_decoded_size_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """storage.bundle_archive.read_blob's own max_decoded_bytes only
        bounds ONE blob at a time -- many distinct, individually-small
        blobs must still be bounded in aggregate across the whole load
        (Codex review)."""
        import abicheck.bundle_facts as bundle_facts_module

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 100)
        metadata = {
            f"lib{i}.so": _meta(soname=f"lib{i}.so", exports=[f"sym{i}"]) for i in range(5)
        }
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "many.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        with pytest.raises(SnapshotError, match="safety limit"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_manifest_naming_too_many_libraries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: DEFAULT_MAX_BUNDLE_DECODED_BYTES
        only charges a shared blob's bytes once, but each library name
        sharing it still materializes its own AbiSnapshot object graph on
        load -- a manifest naming far more libraries than any real bundle
        needs could otherwise amplify one small, size-capped blob into an
        unbounded number of live Python objects. Rejected on name count
        alone, before any blob is even read."""
        import abicheck.bundle_facts as bundle_facts_module
        from abicheck.storage.bundle_archive import BundleArchiveReader

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_LIBRARY_COUNT", 3)
        # All five names deliberately share one snapshot/blob -- proving
        # the cap fires on name *count*, not decoded size (a shared,
        # trivially small blob would otherwise sail under any byte cap).
        metadata = {f"lib{i}.so": _meta(soname="shared.so") for i in range(5)}
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "many-names.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        real_read_blob = BundleArchiveReader.read_blob
        blob_reads: list[object] = []

        def _tracking_read_blob(self, h, **kw):  # type: ignore[no-untyped-def]
            blob_reads.append(h)
            return real_read_blob(self, h, **kw)

        monkeypatch.setattr(BundleArchiveReader, "read_blob", _tracking_read_blob)

        with pytest.raises(SnapshotError, match="exceeding the 3 safety limit"):
            load_bundle_facts(out, format="archive")
        assert blob_reads == []

    def test_load_caps_each_blob_read_by_the_remaining_aggregate_allowance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: each blob read must be capped by
        the *remaining* aggregate allowance, not read_blob's own full
        per-blob default -- otherwise a second large distinct blob could
        fully decompress up to that far larger ceiling before the
        aggregate check (previously applied only after the read
        returned) ever gets a chance to reject it, letting peak decoded
        memory substantially exceed the promised whole-load limit."""
        import abicheck.bundle_facts as bundle_facts_module
        from abicheck.storage.bundle_archive import BundleArchiveReader

        # Generous enough for both real per-library JSON blobs (~2.7 KiB
        # each) to actually decode, so the assertions below exercise the
        # cap *shrinking* between reads rather than a decode failure.
        cap = 6000
        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", cap)
        metadata = {
            f"lib{i}.so": _meta(soname=f"lib{i}.so", exports=[f"sym{i}"]) for i in range(2)
        }
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "two.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        calls: list[object] = []
        real_read_blob = BundleArchiveReader.read_blob

        def _tracking_read_blob(self, h, **kw):  # type: ignore[no-untyped-def]
            calls.append(kw.get("max_decoded_bytes"))
            return real_read_blob(self, h, **kw)

        monkeypatch.setattr(BundleArchiveReader, "read_blob", _tracking_read_blob)
        load_bundle_facts(out, format="archive")

        assert len(calls) == 2
        assert calls[0] == cap  # the full remaining allowance for the first blob
        assert calls[1] is not None and calls[1] < cap  # reduced by the first blob's own size

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

    def test_load_rejects_a_newer_container_schema_version(self, tmp_path: Path) -> None:
        """The container's own schema_version (manifest/blob shape) is a
        separate axis from bundle_facts_schema_version -- a newer one must
        fail closed, not be silently misread as version 1 (Codex review)."""
        from abicheck.errors import IncompatibleSnapshotSchemaError
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "future.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest({"schema_version": 999, "library_blobs": {}})

        with pytest.raises(IncompatibleSnapshotSchemaError, match="999"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_manifest_missing_library_blobs(self, tmp_path: Path) -> None:
        """A manifest.json with no 'library_blobs' key at all (a malformed
        or unrelated zip) must be rejected outright rather than silently
        loading as an empty, valid-looking BundleFacts (Codex review)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "malformed.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest({"schema_version": 1})

        with pytest.raises(ValueError, match="library_blobs"):
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
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with pytest.raises(ValueError, match="must decode to a JSON object"):
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
