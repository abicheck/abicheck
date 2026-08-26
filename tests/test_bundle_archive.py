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

"""Unit tests for :mod:`abicheck.storage.bundle_archive` (G40) -- the
low-level, content-addressed zip-archive primitive.

These tests exercise the module on its own terms (raw bytes/dicts) since it
deliberately knows nothing about ``BundleFacts``/``AbiSnapshot`` -- see the
module's own docstring for why. The ``BundleFacts``-aware round-trip lives
in ``tests/test_bundle_facts.py``'s own archive-format tests, exercised
through ``serialization.save_bundle_facts``/``load_bundle_facts``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.bundle_archive import (
    DEFAULT_MAX_BLOB_BYTES,
    BundleArchiveReader,
    BundleArchiveWriter,
    content_hash,
)


class TestContentHash:
    def test_deterministic(self) -> None:
        assert content_hash(b"hello") == content_hash(b"hello")

    def test_distinguishes_different_content(self) -> None:
        assert content_hash(b"hello") != content_hash(b"world")


class TestBundleArchiveWriterReader:
    def test_round_trip_single_blob(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b'{"library": "libfoo.so"}'
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": {"libfoo.so": h}})

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            assert manifest["library_blobs"]["libfoo.so"] == h
            assert reader.read_blob(h) == payload

    def test_dedup_identical_payloads_share_one_blob_member(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b'{"shared": true}'
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(payload)
            h2 = writer.put_blob(payload)  # byte-identical -> same hash
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

        assert h1 == h2
        with zipfile.ZipFile(path) as zf:
            blob_members = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_members) == 1

        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
            assert (
                manifest["library_blobs"]["a.so"] == manifest["library_blobs"]["b.so"]
            )

    def test_distinct_payloads_get_distinct_blob_members(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            h1 = writer.put_blob(b'{"a": 1}')
            h2 = writer.put_blob(b'{"a": 2}')
            writer.write_manifest({"library_blobs": {"a.so": h1, "b.so": h2}})

        assert h1 != h2
        with zipfile.ZipFile(path) as zf:
            blob_members = [n for n in zf.namelist() if n.startswith("blobs/")]
            assert len(blob_members) == 2

    def test_partial_load_reads_exactly_one_blob_member(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production-scale-shaped partial-load proof (AGENTS.md's
        "Third-party-boundary tests" convention): a real, multi-blob
        archive where reading one blob touches exactly that member's data,
        not the whole archive -- proving lazy access is real, not merely
        API-shaped."""
        path = tmp_path / "bundle.archive.zip"
        payloads = {
            f"lib{i}.so": f'{{"library": "lib{i}.so", "padding": "{"x" * 5000}"}}'.encode()
            for i in range(25)
        }
        hashes: dict[str, str] = {}
        with BundleArchiveWriter(path) as writer:
            for name, payload in payloads.items():
                hashes[name] = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": hashes})

        opened_members: list[str] = []
        real_open = zipfile.ZipFile.open

        def _tracking_open(self: zipfile.ZipFile, name: str, *a: object, **kw: object):  # type: ignore[no-untyped-def]
            opened_members.append(name if isinstance(name, str) else name.filename)
            return real_open(self, name, *a, **kw)

        monkeypatch.setattr(zipfile.ZipFile, "open", _tracking_open)

        with BundleArchiveReader.open(path) as reader:
            target_name = "lib7.so"
            data = reader.read_blob(hashes[target_name])
            assert data == payloads[target_name]

        blob_opens = [m for m in opened_members if m.startswith("blobs/")]
        assert blob_opens == [f"blobs/{hashes[target_name]}.json.zst"]

    def test_read_blob_for_unreferenced_hash_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        with BundleArchiveWriter(path) as writer:
            writer.write_manifest({"library_blobs": {}})

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="no corresponding archive member"):
                reader.read_blob("0" * 64)

    def test_write_manifest_twice_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        writer.write_manifest({})
        with pytest.raises(SnapshotError, match="write_manifest"):
            writer.write_manifest({})
        writer.close()

    def test_close_without_manifest_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        writer = BundleArchiveWriter(path)
        writer.put_blob(b"x")
        with pytest.raises(SnapshotError, match="no manifest.json"):
            writer.close()

    def test_context_manager_propagates_exception_without_requiring_manifest(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bundle.archive.zip"
        with pytest.raises(ValueError, match="boom"):
            with BundleArchiveWriter(path) as writer:
                writer.put_blob(b"x")
                raise ValueError("boom")

    def test_read_blob_enforces_max_decoded_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.archive.zip"
        payload = b"y" * (1024 * 64)
        with BundleArchiveWriter(path) as writer:
            h = writer.put_blob(payload)
            writer.write_manifest({"library_blobs": {"a.so": h}})

        with BundleArchiveReader.open(path) as reader:
            with pytest.raises(SnapshotError, match="safety limit"):
                reader.read_blob(h, max_decoded_bytes=1024)
            # Unbounded (default cap) read still succeeds for the same blob.
            assert reader.read_blob(h) == payload

    def test_default_max_blob_bytes_is_one_gib(self) -> None:
        assert DEFAULT_MAX_BLOB_BYTES == 1024 * 1024 * 1024
