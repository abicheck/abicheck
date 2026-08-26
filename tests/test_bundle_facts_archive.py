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

    @pytest.mark.slow
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

        loaded = load_bundle_facts(out, format="archive")
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
        (Codex review).

        Written *before* the cap is lowered (an archive written under a
        real/looser cap -- an older abicheck, or one written before a later
        security tightening -- being read back under a stricter one is the
        realistic shape of this scenario): the write path enforces the
        identical aggregate cap now too (see the sibling write-side test
        below), so writing under the already-lowered cap would raise here
        instead of at load."""
        import abicheck.bundle_facts as bundle_facts_module

        metadata = {
            f"lib{i}.so": _meta(soname=f"lib{i}.so", exports=[f"sym{i}"]) for i in range(5)
        }
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "many.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 100)
        with pytest.raises(SnapshotError, match="safety limit"):
            load_bundle_facts(out, format="archive")

    def test_load_rejects_a_manifest_naming_too_many_libraries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEFAULT_MAX_BUNDLE_DECODED_BYTES only charges a shared blob's
        bytes once, but each library name sharing it still materializes
        its own AbiSnapshot object graph on load -- a manifest naming far
        more libraries than any real bundle needs could otherwise amplify
        one small, size-capped blob into an unbounded number of live
        Python objects. Rejected on name count alone, before any blob is
        even read. Built as a raw archive (bypassing `write_bundle_facts_
        archive`'s own, now-symmetric write-time name-count cap) so this
        exercises the reader's *independent* enforcement of the same cap
        -- e.g. against a manifest hand-crafted or written by an older,
        less-strict writer."""
        import abicheck.bundle_facts as bundle_facts_module
        from abicheck.storage.bundle_archive import (
            BundleArchiveReader,
            BundleArchiveWriter,
        )

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_LIBRARY_COUNT", 3)
        out = tmp_path / "many-names.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(b'{"library": "shared.so"}')
            writer.write_manifest(
                {"library_blobs": {f"lib{i}.so": h for i in range(5)}}
            )

        real_read_blob = BundleArchiveReader.read_blob
        blob_reads: list[object] = []

        def _tracking_read_blob(self, h, **kw):  # type: ignore[no-untyped-def]
            blob_reads.append(h)
            return real_read_blob(self, h, **kw)

        monkeypatch.setattr(BundleArchiveReader, "read_blob", _tracking_read_blob)

        with pytest.raises(SnapshotError, match="exceeding the 3 safety limit"):
            load_bundle_facts(out, format="archive")
        assert blob_reads == []

    def test_save_rejects_a_write_that_would_exceed_the_reader_own_member_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: nothing on the write path checked
        member count at all -- a manifest naming exactly
        `MAX_ARCHIVE_MEMBERS` distinct-content libraries wrote successfully,
        producing `MAX_ARCHIVE_MEMBERS + 1` zip members (one blob per
        library plus `manifest.json`), which the reader's own preflight
        (checked directly against `MAX_ARCHIVE_MEMBERS` in
        tests/test_bundle_archive.py) would then refuse to reopen. The
        write must fail up front instead, before producing an archive the
        reader can never open."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "MAX_ARCHIVE_MEMBERS", 3)
        # Four *distinct*-content snapshots (soname differs per entry, and
        # each gets its own stamped `AbiSnapshot.library`) -- each gets its
        # own blob, so this can't be sidestepped by dedup the way
        # test_save_does_not_over_reject_many_names_sharing_one_blob's
        # deliberately-shared-object fixture is.
        metadata = {
            f"lib{i}.so": _meta(soname=f"lib{i}.so", exports=[f"f{i}"])
            for i in range(4)
        }
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "too-many-members.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="more than 3 zip members"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_save_does_not_over_reject_many_names_sharing_one_blob(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: the member-cap check's first
        revision counted raw *names*, not *distinct* blobs -- so a manifest
        naming far more names than the cap, but all sharing one identical
        snapshot, was incorrectly rejected even though the real archive
        needs only one blob member. Ten shared-content names against a
        member cap of 3 (1 blob + 1 manifest.json = 2 real members) must
        still succeed.

        The *literal same* `AbiSnapshot` object is deliberately reused
        under all ten keys (per the G40 plan's own "Round 1" finding,
        documented in `docs/contribute/plans/g40-content-addressed-
        bundle-archive.md`): `AbiSnapshot.library` is always stamped with
        the dict key by `_per_library_snapshots`, so two *different*-keyed
        entries built the ordinary way can never serialize byte-identical
        -- true dedup across names is only observable this way."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "MAX_ARCHIVE_MEMBERS", 3)
        shared_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": shared_snap for i in range(10)}
        )
        out = tmp_path / "shared-blob-many-names.bundlefacts.archive.zip"

        save_bundle_facts(facts, out, format="archive")
        loaded = load_bundle_facts(out, format="archive")
        assert set(loaded.per_library_snapshots) == {f"lib{i}.so" for i in range(10)}

    def test_save_rejects_a_write_that_would_exceed_the_reader_own_library_count_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `BundleFacts` naming more library names than the reader's own
        `DEFAULT_MAX_LIBRARY_COUNT` -- even when every name shares one
        identical blob, so the member-count/aggregate-byte caps alone
        would let it through -- must fail on write, before producing an
        archive its own paired reader would refuse to reopen (Codex
        review, fresh evidence)."""
        import abicheck.bundle_facts as bundle_facts_module_local

        monkeypatch.setattr(
            bundle_facts_module_local, "DEFAULT_MAX_LIBRARY_COUNT", 3
        )
        shared_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": shared_snap for i in range(5)}
        )
        out = tmp_path / "too-many-names.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="5 library names.*exceed the 3"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_save_rejects_the_library_count_cap_before_serializing_any_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The library-name-count cap must be checked *before* the
        serialization loop, not after building `library_blobs` from it --
        many names referencing one large shared snapshot would otherwise
        serialize that same payload once per name (possibly terabytes of
        work) before ever getting a chance to reject the write (Codex
        review, fresh evidence)."""
        import abicheck.bundle_facts as bundle_facts_module_local
        import abicheck.serialization as serialization_module

        monkeypatch.setattr(
            bundle_facts_module_local, "DEFAULT_MAX_LIBRARY_COUNT", 3
        )
        serialize_calls = 0
        real_snapshot_to_dict = serialization_module.snapshot_to_dict

        def _counting_snapshot_to_dict(snap: AbiSnapshot) -> dict:
            nonlocal serialize_calls
            serialize_calls += 1
            return real_snapshot_to_dict(snap)

        monkeypatch.setattr(
            serialization_module, "snapshot_to_dict", _counting_snapshot_to_dict
        )
        shared_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": shared_snap for i in range(5)}
        )
        out = tmp_path / "rejected-before-serializing.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="5 library names.*exceed the 3"):
            save_bundle_facts(facts, out, format="archive")
        assert serialize_calls == 0

    def test_load_charges_each_duplicate_name_own_copy_against_the_aggregate_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The aggregate decoded-byte cap only charges a shared blob's
        bytes once (via blob_cache) -- but every duplicate name still
        materializes its own independent, deep-copied AbiSnapshot object
        graph on load, so a manifest naming many names against one
        moderately-sized blob could otherwise amplify past the promised
        aggregate limit in live Python objects alone. Each duplicate's own
        copy must be charged against the same budget too (Codex review,
        fresh evidence)."""
        import abicheck.bundle_facts as bundle_facts_module_local

        shared_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": shared_snap for i in range(5)}
        )
        out = tmp_path / "duplicate-copies.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        # A cap big enough for one copy but not for all five duplicate
        # copies charged on top of it.
        monkeypatch.setattr(
            bundle_facts_module_local, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 500
        )
        with pytest.raises(SnapshotError, match="exceeds the 500 byte"):
            load_bundle_facts(out, format="archive")

    def test_save_rejects_a_write_that_would_exceed_the_reader_own_duplicate_copy_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The write-side aggregate cap must mirror the reader's own
        duplicate-copy accounting, not just each unique blob's bytes once
        -- many names sharing one blob whose *unique* size passes the cap
        can still exceed it once every duplicate name's own copy is
        counted the way the reader counts it on load, publishing an
        archive its own paired reader would then refuse to reopen (Codex
        review, fresh evidence)."""
        import abicheck.bundle_facts as bundle_facts_module_local

        shared_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": shared_snap for i in range(5)}
        )
        out = tmp_path / "too-many-duplicate-copies.bundlefacts.archive.zip"

        # A cap big enough for one copy (~2987 bytes; the incremental
        # unique-bytes check the loop itself now performs would pass --
        # every one of these 5 names shares the identical content, so
        # only one distinct payload is ever added to unique_payloads) but
        # not for all five duplicate copies together (~14935 bytes).
        monkeypatch.setattr(
            bundle_facts_module_local, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 5000
        )
        with pytest.raises(SnapshotError, match="once every duplicate"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_the_aggregate_cap_is_enforced_incrementally_during_serialization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The aggregate-byte cap must be checked *while* distinct
        snapshots are being serialized, not only after every one of them
        has already been held in memory at once -- otherwise many large
        distinct snapshots could consume far more memory than the cap
        itself before the rejection ever fires (Codex review, fresh
        evidence). Confirmed here by counting `snapshot_to_dict` calls: a
        cap that rejects after the 2nd of 3 distinct snapshots must never
        let the 3rd be serialized at all."""
        import abicheck.bundle_facts as bundle_facts_module_local
        from abicheck.serialization import snapshot_to_dict

        metadata = {
            f"lib{i}.so": _meta(soname=f"lib{i}.so", exports=[f"sym{i}"]) for i in range(3)
        }
        facts = BundleFacts(per_library_snapshots=_per_library_snapshots(metadata))

        calls: list[str] = []

        def _tracking_to_dict(snap: AbiSnapshot) -> dict[str, object]:
            calls.append(snap.library)
            return snapshot_to_dict(snap)

        # Each real per-library blob here is ~2.7 KiB (per the sibling
        # cap-shrinking test above) -- big enough to reject after the 2nd
        # distinct snapshot but not the 1st.
        monkeypatch.setattr(bundle_facts_module_local, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 4000)
        out = tmp_path / "incremental-cap.bundlefacts.archive.zip"
        with pytest.raises(SnapshotError, match="already exceeds"):
            bundle_facts_module_local.write_bundle_facts_archive(
                facts, out, snapshot_to_dict=_tracking_to_dict
            )
        assert not out.exists()
        # Sorted by name (lib0.so, lib1.so, lib2.so): the cap trips on the
        # 2nd, so the 3rd must never have been serialized at all.
        assert calls == ["lib0.so", "lib1.so"]

    def test_save_rejects_a_write_whose_manifest_blob_alone_would_exceed_the_aggregate_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The write-side aggregate-byte accounting only summed hashes
        referenced by `library_blobs` -- a `manifest_blob` whose hash
        isn't already shared with any library (the common case) never
        contributed to that sum at all, even though the reader's own
        `_cached_blob()` genuinely charges it once on load. A bundle with
        no (or small) library snapshots but an oversized manifest would
        therefore pass this check and then be rejected on load (Codex
        review, fresh evidence)."""
        import abicheck.bundle_facts as bundle_facts_module_local

        monkeypatch.setattr(
            bundle_facts_module_local, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 100
        )
        manifest = InstantiationManifest(
            entries=tuple(
                ManifestEntry(symbol=f"sym_{i}", optional_provider=False)
                for i in range(20)
            )
        )
        facts = capture_bundle_facts({}, manifest=manifest)  # no library snapshots at all
        out = tmp_path / "oversized-manifest-only.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="exceed the 100"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_save_rejects_a_write_that_would_exceed_the_reader_own_aggregate_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: nothing on the write path checked
        the aggregate decoded-byte budget either -- several distinct,
        individually-small library snapshots could sum past
        `DEFAULT_MAX_BUNDLE_DECODED_BYTES` and still write successfully,
        producing an archive `read_bundle_facts_archive()`'s own aggregate
        cap would then always refuse once its remaining allowance runs out.
        The check must be dedup-aware -- charging each *unique* blob's
        bytes once, mirroring the reader's own cache -- not the informational
        `decoded_size_bytes` total, which intentionally counts every name's
        payload regardless of dedup; this is verified by having every
        payload be genuinely distinct (soname differs per entry, so none of
        them share a blob)."""
        import abicheck.bundle_facts as bundle_facts_module

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 100)
        metadata = {
            f"lib{i}.so": _meta(soname=f"lib{i}.so", exports=[f"sym{i}"]) for i in range(5)
        }
        facts = capture_bundle_facts(_per_library_snapshots(metadata))
        out = tmp_path / "too-much-content.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="100 byte aggregate safety limit"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_save_rejects_a_write_whose_manifest_json_would_exceed_the_reader_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: neither the member-count nor the
        aggregate-decoded-bytes cap charges the *container* `manifest.json`
        member's own bytes -- both only cover blob content. A `BundleFacts`
        with a large `filesystem_aliases` mapping could otherwise write a
        `manifest.json` the reader's own `DEFAULT_MAX_MANIFEST_BYTES` cap
        would then always refuse. Verified with a small library set (so
        neither of the other two caps fires) and a large filesystem_aliases
        mapping to inflate manifest.json specifically."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 200)
        facts = BundleFacts(
            per_library_snapshots=_per_library_snapshots(_old_metadata()),
            filesystem_aliases={
                "libcore.so": tuple(f"libcore.so.{i}" for i in range(50)),
            },
        )
        out = tmp_path / "big-manifest.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="200 byte safety limit"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

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

    def test_load_rejects_a_non_string_library_blobs_value(
        self, tmp_path: Path
    ) -> None:
        """A 'library_blobs' value that isn't a string must raise this
        module's own error type -- not a raw, unhashable-type TypeError out
        of snapshot_cache.get(h)/blob_cache.get(h) (CodeRabbit review)."""
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        out = tmp_path / "bad_value.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            writer.write_manifest({"library_blobs": {"a.so": ["not", "a", "hash"]}})

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
                {"library_blobs": {}, "manifest_blob": ["not", "a", "hash"]}
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
