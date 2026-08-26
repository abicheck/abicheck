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

"""``BundleFacts`` G40 ``format="archive"`` resource-limit hardening tests.

Split out of ``tests/test_bundle_facts_archive.py`` purely to keep both
files under the ADR-061 1200-line test cap -- this file holds every test
about a *safety cap* (aggregate decoded-byte budget, member/library-name
counts, oversized-manifest/oversized-string rejection, duplicate-copy
charging, shared-hash accounting) on either the read or write side; the
sibling file keeps the basic round-trip/format-detection/back-compat
coverage. See that file's own module docstring for the low-level
container primitive's own tests (``tests/test_bundle_archive.py``) and
its own hardening split (``tests/test_bundle_archive_writer_hardening.py``,
``tests/test_bundle_archive_cd_guard.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.bundle_facts import BundleFacts, capture_bundle_facts
from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot
from abicheck.serialization import (
    load_bundle_facts,
    save_bundle_facts,
    snapshot_to_dict,
)


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


class TestBundleFactsArchiveResourceLimits:
    """G40 ``format="archive"``'s safety caps -- aggregate decoded-byte
    budget, member/library-name counts, oversized-manifest/oversized-
    string rejection, and duplicate-copy/shared-hash charging -- on both
    the read and write sides, verified symmetric."""

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

    def test_many_names_sharing_one_snapshot_object_serialize_it_only_once(
        self, tmp_path: Path
    ) -> None:
        """The dedup that skips *adding* an already-seen hash to
        `unique_payloads` never skipped the expensive *serialization*
        itself -- many names referencing the identical `AbiSnapshot`
        object re-ran `snapshot_to_dict`/`json.dumps` once per name
        regardless (Codex review, fresh evidence: 20,000 names sharing
        one large snapshot could perform ~2 TiB of redundant
        serialization work before the aggregate check ever runs). Now
        cached per object identity."""
        import abicheck.bundle_facts as bundle_facts_module_local
        from abicheck.serialization import snapshot_to_dict

        shared_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": shared_snap for i in range(50)}
        )

        calls: list[str] = []

        def _tracking_to_dict(snap: AbiSnapshot) -> dict[str, object]:
            calls.append(snap.library)
            return snapshot_to_dict(snap)

        out = tmp_path / "shared-object.bundlefacts.archive.zip"
        result = bundle_facts_module_local.write_bundle_facts_archive(
            facts, out, snapshot_to_dict=_tracking_to_dict
        )
        # 50 names, but the identical object was serialized exactly once.
        assert calls == ["libcore.so"]
        # decoded_size_bytes still reports the full logical total, as if
        # every name were independently serialized -- caching how the
        # work is done must not change what is reported.
        import json as _json

        one_payload_len = len(
            _json.dumps(snapshot_to_dict(shared_snap), indent=2).encode("utf-8")
        )
        assert result.decoded_size_bytes == 50 * one_payload_len

    def test_distinct_objects_sharing_content_are_still_bounded_incrementally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Distinct-but-equal `AbiSnapshot` objects (not the same object
        identity -- e.g. independently built or deep-copied) miss the
        identity cache every time, so each is fully, legitimately
        re-serialized. Only the *deduped* `unique_payloads` total was
        checked incrementally -- the duplicate-aware total (matching
        what the reader actually pays per name on load) was still only
        checked once, at the very end of the loop, letting many such
        objects perform unbounded serialization work first (Codex
        review, fresh evidence)."""
        import copy

        import abicheck.bundle_facts as bundle_facts_module_local
        from abicheck.serialization import snapshot_to_dict

        base_snap = _per_library_snapshots(_old_metadata())["libcore.so"]
        facts = BundleFacts(
            per_library_snapshots={f"lib{i}.so": copy.deepcopy(base_snap) for i in range(3)}
        )

        calls: list[str] = []

        def _tracking_to_dict(snap: AbiSnapshot) -> dict[str, object]:
            calls.append(snap.library)
            return snapshot_to_dict(snap)

        # Each real payload here is ~2987 bytes -- big enough to reject
        # after the 2nd distinct-but-equal object, not the 1st.
        monkeypatch.setattr(bundle_facts_module_local, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 4000)
        out = tmp_path / "distinct-equal.bundlefacts.archive.zip"
        with pytest.raises(SnapshotError, match="once every duplicate"):
            bundle_facts_module_local.write_bundle_facts_archive(
                facts, out, snapshot_to_dict=_tracking_to_dict
            )
        assert not out.exists()
        # Sorted by name (lib0.so, lib1.so, lib2.so): rejected on the
        # 2nd, so the 3rd distinct object must never be serialized.
        assert calls == ["libcore.so", "libcore.so"]

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

        # Matches either raise site: the manifest's own now-bounded encode
        # may reject it directly (a real correctness *improvement* from a
        # later fix -- rejecting before the aggregate check even runs,
        # rather than only after), or the aggregate check below it if not.
        # Both share this phrase; which one fires is an implementation
        # detail, not what this test is pinning.
        with pytest.raises(SnapshotError, match="byte aggregate safety limit"):
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

    def test_write_routes_per_snapshot_encoding_through_bounded_encode_utf8(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: `write_bundle_facts_archive()`'s
        per-library-snapshot encode previously called `json.dumps()` +
        `.encode()` directly -- materializing a full copy of an oversized
        snapshot's serialization before the aggregate cap check (checked
        only *after*) ever got a chance to reject it. Now routes through
        `bounded_encode_utf8()` (the same streamed, remaining-allowance-
        aware helper the manifest-level fix uses), verified here by a
        tracking wrapper -- proof of *routing*, not just of the numeric
        outcome, since a numeric-only test can't distinguish "rejected
        after full materialization" from "rejected without it"."""
        import abicheck.storage.bundle_archive_json_guard as guard_module

        real_fn = guard_module.bounded_encode_utf8
        calls: list[object] = []

        def _tracking(obj: object, limit: int) -> bytes | None:
            calls.append(obj)
            return real_fn(obj, limit)

        monkeypatch.setattr(guard_module, "bounded_encode_utf8", _tracking)

        snap = AbiSnapshot(library="libcore.so", version="old")
        facts = BundleFacts(per_library_snapshots={"libcore.so": snap})
        out = tmp_path / "routes-through-bounded-encode.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        assert len(calls) == 1
        assert isinstance(calls[0], dict)
        assert calls[0].get("library") == "libcore.so"

    def test_write_rejects_a_snapshot_with_one_oversized_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Functional correctness companion to the routing test above: a
        single library snapshot whose own serialization alone exceeds the
        cap (via one oversized string field, not many small ones summing
        past it) must still be rejected with the correct error."""
        import abicheck.bundle_facts as bundle_facts_module

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1000)
        snap = AbiSnapshot(library="libbig.so", version="x" * 5000)
        facts = BundleFacts(per_library_snapshots={"libbig.so": snap})
        out = tmp_path / "oversized-snapshot-field.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="1000 byte aggregate safety limit"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_write_routes_the_instantiation_manifest_through_bounded_encode_utf8(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: only the per-library-snapshot
        encode above was routed through `bounded_encode_utf8()` -- the
        `InstantiationManifest` payload still called `json.dumps()` +
        `.encode()` directly, the identical unbounded-materialization gap
        one level up. Same routing proof as the snapshot-level test."""
        import abicheck.storage.bundle_archive_json_guard as guard_module

        real_fn = guard_module.bounded_encode_utf8
        calls: list[object] = []

        def _tracking(obj: object, limit: int) -> bytes | None:
            calls.append(obj)
            return real_fn(obj, limit)

        monkeypatch.setattr(guard_module, "bounded_encode_utf8", _tracking)

        manifest = InstantiationManifest(
            entries=(ManifestEntry(symbol="core_mul", optional_provider=False),)
        )
        facts = BundleFacts(
            per_library_snapshots=_per_library_snapshots(_old_metadata()),
            manifest=manifest,
        )
        out = tmp_path / "manifest-routes-through-bounded-encode.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")

        # One call per library snapshot, plus exactly one more for the
        # manifest itself -- a dict with "provides", never "library".
        manifest_calls = [c for c in calls if isinstance(c, dict) and "provides" in c]
        assert len(manifest_calls) == 1

    def test_write_rejects_an_oversized_instantiation_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Functional correctness companion: an `InstantiationManifest`
        whose own serialization alone exceeds the cap must still be
        rejected with the correct error, not exhaust memory first."""
        import abicheck.bundle_facts as bundle_facts_module

        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1000)
        manifest = InstantiationManifest(
            entries=tuple(
                ManifestEntry(symbol=f"sym_{i}" * 50, optional_provider=False)
                for i in range(50)
            )
        )
        facts = BundleFacts(per_library_snapshots={}, manifest=manifest)
        out = tmp_path / "oversized-manifest.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="1000 byte aggregate safety limit"):
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

    def test_the_manifest_cap_is_checked_before_materializing_the_full_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: this high-level preflight must
        reject an oversized manifest.json without first fully
        materializing it via `json.dumps()` (and a second, separate
        UTF-8-encoded copy of it) -- patches `json.dumps` itself to fail
        the test if `write_bundle_facts_archive` still calls it."""
        import json as json_module

        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 200)
        real_dumps = json_module.dumps

        def _guarded_dumps(obj: object, *a: object, **kw: object) -> str:
            # Only the container manifest itself (the object this
            # preflight bounds) must never reach json.dumps() -- the
            # per-library snapshot/instantiation-manifest blob encodes
            # earlier in the same function are unrelated and must still
            # work normally.
            if isinstance(obj, dict) and "filesystem_aliases" in obj:
                raise AssertionError(
                    "write_bundle_facts_archive() must not call "
                    "json.dumps() on the container manifest for its size "
                    "preflight -- it should encode incrementally via "
                    "json.JSONEncoder.iterencode()"
                )
            return real_dumps(obj, *a, **kw)  # type: ignore[no-any-return]

        monkeypatch.setattr(json_module, "dumps", _guarded_dumps)
        facts = BundleFacts(
            per_library_snapshots=_per_library_snapshots(_old_metadata()),
            filesystem_aliases={
                "libcore.so": tuple(f"libcore.so.{i}" for i in range(50)),
            },
        )
        out = tmp_path / "big-manifest-no-materialize.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="200 byte safety limit"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_a_single_oversized_string_is_rejected_before_iterencode_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: `iterencode()` yields one whole
        escaped string as a single chunk, so the chunk-by-chunk length
        check the previous test covers can't reject a manifest containing
        one string value alone larger than the cap before that one
        allocation already happened. This high-level preflight must
        pre-check every string leaf directly first."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(bundle_archive_module, "DEFAULT_MAX_MANIFEST_BYTES", 200)
        facts = BundleFacts(
            per_library_snapshots=_per_library_snapshots(_old_metadata()),
            filesystem_aliases={"libcore.so": (("x" * 400),)},
        )
        out = tmp_path / "single-oversized-string.bundlefacts.archive.zip"

        with pytest.raises(SnapshotError, match="alone exceeding the 200 byte"):
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

    def test_manifest_blob_sharing_a_library_hash_is_charged_for_its_own_materialization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: when `manifest_blob` shares a
        content hash with an already-fetched `library_blobs` entry,
        `_cached_blob()` correctly charges the raw bytes only once (a
        cache hit) -- but `json.loads()`/`manifest_from_dict()` still
        build a *second*, independent object graph from them, the same
        "duplicate materialization" class the per-library-name loop
        already re-charges for its own deep-copied AbiSnapshot. A large
        shared blob could otherwise be parsed twice while billed once,
        bypassing the aggregate decoded-byte budget."""
        import json as json_module

        import abicheck.bundle_facts as bundle_facts_module
        from abicheck.storage.bundle_archive import BundleArchiveWriter

        # A payload that parses successfully as *both* an AbiSnapshot (via
        # snapshot_from_dict) and an InstantiationManifest (via
        # manifest_from_dict, which only requires a list-valued
        # "provides") -- real content shared between the two roles this
        # finding is about, not a synthetic shortcut.
        snap = AbiSnapshot(library="libcore.so", version="old")
        payload_dict = snapshot_to_dict(snap)
        payload_dict["provides"] = []
        payload = json_module.dumps(payload_dict, indent=2).encode("utf-8")

        out = tmp_path / "shared-manifest-blob.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest(
                {
                    "library_blobs": {"libcore.so": h},
                    "manifest_blob": h,
                    "filesystem_aliases": {},
                    "library_filenames": {},
                }
            )

        # A cap generous enough for exactly one charge of the payload's
        # own bytes, but not two -- the fix's own regression signal.
        cap = len(payload) + 100
        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", cap)
        with pytest.raises(SnapshotError, match="second materialization"):
            load_bundle_facts(out, format="archive")

    def test_manifest_blob_sharing_a_library_hash_still_loads_under_the_cap(
        self, tmp_path: Path
    ) -> None:
        """Positive control for the finding above: the same shared-hash
        shape must still load correctly (both the library snapshot and
        the instantiation manifest populated) when the aggregate cap has
        room for the second charge."""
        import json as json_module

        from abicheck.storage.bundle_archive import BundleArchiveWriter

        snap = AbiSnapshot(library="libcore.so", version="old")
        payload_dict = snapshot_to_dict(snap)
        payload_dict["provides"] = []
        payload = json_module.dumps(payload_dict, indent=2).encode("utf-8")

        out = tmp_path / "shared-manifest-blob-ok.bundlefacts.archive.zip"
        with BundleArchiveWriter(out) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest(
                {
                    "library_blobs": {"libcore.so": h},
                    "manifest_blob": h,
                    "filesystem_aliases": {},
                    "library_filenames": {},
                }
            )

        loaded = load_bundle_facts(out, format="archive")
        assert loaded.per_library_snapshots["libcore.so"].library == "libcore.so"
        assert loaded.manifest is not None
        assert loaded.manifest.entries == ()

    def test_write_charges_manifest_blob_even_when_its_hash_is_shared(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex review, fresh evidence: the reader now charges
        `manifest_blob`'s bytes a second time whenever its hash is shared
        with an already-fetched `library_blobs` entry (the finding just
        above), but the *writer*'s own mirrored aggregate-byte preflight
        only added that charge when the hash was *not* shared -- so the
        writer could accept an archive its own paired reader would then
        reject. Forces a hash collision via a patched `content_hash` (the
        real reader/writer's normal per-content hashing gives no way to
        make two different payloads collide without literally breaking
        SHA-256) so the accounting bug is exercised directly, independent
        of whether any real content happens to collide."""
        import json as json_module

        import abicheck.bundle_facts as bundle_facts_module
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(
            bundle_archive_module, "content_hash", lambda payload: "deadbeef" * 8
        )
        snap = AbiSnapshot(library="libcore.so", version="old")
        facts = BundleFacts(
            per_library_snapshots={"libcore.so": snap},
            manifest=InstantiationManifest(entries=()),
        )
        out = tmp_path / "write-side-shared-manifest-blob.bundlefacts.archive.zip"

        # The library snapshot's own serialized size is the one real charge
        # (hash_counts sums it once); a cap between that and double it is
        # exceeded only if the manifest's own second charge is included --
        # the fix's regression signal, mirroring the reader-side test.
        library_payload = json_module.dumps(
            snapshot_to_dict(snap), indent=2
        ).encode("utf-8")
        cap = len(library_payload) + 10
        monkeypatch.setattr(bundle_facts_module, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", cap)
        with pytest.raises(SnapshotError, match="byte aggregate safety limit"):
            save_bundle_facts(facts, out, format="archive")
        assert not out.exists()

    def test_write_still_succeeds_with_a_shared_manifest_blob_under_a_sufficient_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive control for the finding above: the same forced-
        collision shape must still write successfully once the cap has
        room for both charges."""
        import abicheck.storage.bundle_archive as bundle_archive_module

        monkeypatch.setattr(
            bundle_archive_module, "content_hash", lambda payload: "deadbeef" * 8
        )
        snap = AbiSnapshot(library="libcore.so", version="old")
        facts = BundleFacts(
            per_library_snapshots={"libcore.so": snap},
            manifest=InstantiationManifest(entries=()),
        )
        out = tmp_path / "write-side-shared-manifest-blob-ok.bundlefacts.archive.zip"
        save_bundle_facts(facts, out, format="archive")
        assert out.exists()
