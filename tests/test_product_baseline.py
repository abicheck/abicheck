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

"""Unit tests for :mod:`abicheck.product_baseline` — the pack/unpack
primitives behind ``abicheck project baseline pack``/``unpack``. CLI-level
wiring is exercised separately in ``test_cli_project_baseline.py``."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.product_baseline import (
    MANIFEST_MEMBER_NAME,
    PRODUCT_BASELINE_SCHEMA,
    LibraryEntry,
    ProductBaselineManifest,
    pack_product_baseline,
    unpack_product_baseline,
)


def _make_product(root: Path) -> Path:
    product = root / "product"
    (product / "lib").mkdir(parents=True)
    (product / "include" / "api").mkdir(parents=True)
    (product / "lib" / "liba.so.1.2.3").write_bytes(b"ELF-A" * 200)
    (product / "lib" / "liba.so").symlink_to("liba.so.1.2.3")
    (product / "lib" / "libb.so").write_bytes(b"ELF-B" * 300)
    (product / "include" / "api" / "a.h").write_text("struct A { int x; };\n")
    (product / "README.txt").write_text("not a library\n")
    return product


class TestManifestRoundTrip:
    def test_to_dict_from_dict_round_trip(self) -> None:
        manifest = ProductBaselineManifest(
            product="demo",
            libraries=(
                LibraryEntry(
                    name="liba.so", path="lib/liba.so", sha256="ab" * 32, size=42
                ),
            ),
            header_roots=("include",),
            file_count=3,
        )
        restored = ProductBaselineManifest.from_dict(manifest.to_dict())
        assert restored == manifest

    def test_from_dict_tolerates_missing_and_wrong_shaped_fields(self) -> None:
        # Mirrors this codebase's established defensive from_dict() contract
        # (AGENTS.md: "every dataclass carries to_dict()/from_dict() with
        # defensive .get() parsing so a newer/hand-edited pack never aborts
        # a load") -- garbage-shaped input degrades to empty, never raises.
        manifest = ProductBaselineManifest.from_dict(
            {"libraries": "not-a-list", "header_roots": None, "extra": "ignored"}
        )
        assert manifest.libraries == ()
        assert manifest.header_roots == ()
        assert manifest.schema == PRODUCT_BASELINE_SCHEMA


class TestPackProductBaseline:
    def test_pack_produces_a_file(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"
        manifest = pack_product_baseline(product, out, product="demo")
        assert out.is_file()
        assert manifest.product == "demo"

    def test_pack_identifies_shared_libraries_only(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert names == {"liba.so.1.2.3", "libb.so"}
        # The symlink itself is archived (round-trip test covers that) but
        # is not separately counted as a library entry.
        assert "liba.so" not in names

    def test_pack_records_correct_hash_and_size(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        by_path = {lib.path: lib for lib in manifest.libraries}
        entry = by_path["lib/libb.so"]
        content = (product / "lib" / "libb.so").read_bytes()
        assert entry.size == len(content)
        assert entry.sha256 == hashlib.sha256(content).hexdigest()

    def test_pack_is_deterministic(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        out1 = tmp_path / "b1.tar.zst"
        out2 = tmp_path / "b2.tar.zst"
        pack_product_baseline(product, out1, product="demo", header_roots=["include"])
        pack_product_baseline(product, out2, product="demo", header_roots=["include"])
        assert out1.read_bytes() == out2.read_bytes()

    def test_pack_records_header_roots(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        manifest = pack_product_baseline(
            product, tmp_path / "b.tar.zst", header_roots=["include"]
        )
        assert manifest.header_roots == ("include",)

    def test_pack_rejects_missing_source_dir(self, tmp_path: Path) -> None:
        with pytest.raises(SnapshotError, match="not a directory"):
            pack_product_baseline(tmp_path / "nope", tmp_path / "out.tar.zst")

    def test_pack_rejects_non_tar_zst_output_suffix(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        with pytest.raises(SnapshotError, match="tar.zst"):
            pack_product_baseline(product, tmp_path / "out.tar.gz")

    def test_pack_rejects_empty_source_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SnapshotError, match="no files found"):
            pack_product_baseline(empty, tmp_path / "out.tar.zst")

    def test_pack_rejects_absolute_header_root(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        with pytest.raises(SnapshotError, match="header root"):
            pack_product_baseline(
                product, tmp_path / "b.tar.zst", header_roots=["/etc"]
            )

    def test_pack_rejects_escaping_header_root(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        with pytest.raises(SnapshotError, match="header root"):
            pack_product_baseline(
                product, tmp_path / "b.tar.zst", header_roots=["../escape"]
            )

    def test_pack_rejects_nonexistent_header_root(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        with pytest.raises(SnapshotError, match="header root"):
            pack_product_baseline(
                product, tmp_path / "b.tar.zst", header_roots=["does-not-exist"]
            )

    def test_pack_rejects_symlink_escaping_source_dir(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        outside = tmp_path / "outside.so"
        outside.write_bytes(b"outside")
        (product / "lib" / "evil.so").symlink_to(outside)
        with pytest.raises(SnapshotError, match="escapes"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_leaves_no_partial_output_on_failure(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        outside = tmp_path / "outside.so"
        outside.write_bytes(b"outside")
        (product / "lib" / "evil.so").symlink_to(outside)
        out = tmp_path / "b.tar.zst"
        with pytest.raises(SnapshotError):
            pack_product_baseline(product, out)
        assert not out.exists()
        assert not list(out.parent.glob(".abicheck-product-baseline-*"))


class TestUnpackProductBaseline:
    def test_round_trip_restores_content(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        packed = pack_product_baseline(
            product, archive, product="demo", header_roots=["include"]
        )

        dest = tmp_path / "unpacked"
        unpacked = unpack_product_baseline(archive, dest)

        assert unpacked == packed
        assert (dest / "lib" / "libb.so").read_bytes() == b"ELF-B" * 300
        assert (dest / "lib" / "liba.so").is_symlink()
        assert (dest / "include" / "api" / "a.h").is_file()
        assert (dest / MANIFEST_MEMBER_NAME).is_file()

    def test_unpack_rejects_non_tar_zst_suffix(self, tmp_path: Path) -> None:
        bogus = tmp_path / "baseline.tar.gz"
        bogus.write_bytes(b"not really a tar.zst")
        with pytest.raises(SnapshotError, match="tar.zst"):
            unpack_product_baseline(bogus, tmp_path / "dest")

    def test_unpack_rejects_missing_archive(self, tmp_path: Path) -> None:
        with pytest.raises(SnapshotError, match="not found"):
            unpack_product_baseline(tmp_path / "nope.tar.zst", tmp_path / "dest")

    def test_unpack_rejects_nonempty_destination(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "existing").write_text("already here\n")
        with pytest.raises(SnapshotError, match="not empty"):
            unpack_product_baseline(archive, dest)

    def test_unpack_rejects_archive_without_manifest(self, tmp_path: Path) -> None:
        import tarfile

        plain_tar = tmp_path / "plain.tar.zst"
        import zstandard

        payload = tmp_path / "payload.tar"
        with tarfile.open(payload, "w") as tf:
            info = tarfile.TarInfo(name="not-a-baseline.txt")
            data = b"hello\n"
            info.size = len(data)
            import io

            tf.addfile(info, io.BytesIO(data))
        cctx = zstandard.ZstdCompressor()
        plain_tar.write_bytes(cctx.compress(payload.read_bytes()))

        with pytest.raises(SnapshotError, match=MANIFEST_MEMBER_NAME):
            unpack_product_baseline(plain_tar, tmp_path / "dest")

    def test_unpack_rejects_newer_major_schema(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        # Monkey-patch the schema forward-compat check directly, since
        # constructing a real "future" archive would require writing
        # this format twice.
        from abicheck import product_baseline as pb

        with pytest.raises(SnapshotError, match="newer than this abicheck build"):
            pb._check_schema_supported("abicheck.product-baseline/v999", archive)
