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
import os
import stat as stat_mod
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

    def test_from_dict_tolerates_non_numeric_size_and_file_count(self) -> None:
        # A hand-edited/corrupt archive's manifest.json could carry a
        # non-numeric "size"/"file_count" -- must degrade to 0, not raise
        # ValueError past unpack_product_baseline()'s SnapshotError handling
        # (Codex review, fresh evidence).
        manifest = ProductBaselineManifest.from_dict(
            {
                "libraries": [{"name": "a.so", "path": "a.so", "size": "not-a-number"}],
                "file_count": "also-not-a-number",
            }
        )
        assert manifest.libraries[0].size == 0
        assert manifest.file_count == 0


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

    def test_pack_does_not_classify_split_debug_companion_as_a_library(
        self, tmp_path: Path
    ) -> None:
        # "libfoo.so.1.debug" contains the literal substring ".so." (the
        # dot right before "1"), so a naive substring check misclassified
        # a conventional split-debug companion file as a shared library
        # (Codex review, fresh evidence).
        product = _make_product(tmp_path)
        (product / "lib" / "libb.so.1.debug").write_bytes(b"DEBUGINFO")
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert "libb.so.1.debug" not in names

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

    def test_pack_is_deterministic_across_differing_permission_bits(
        self, tmp_path: Path
    ) -> None:
        # Two byte-identical trees differing only in file mode (e.g. two
        # builders with different umasks) must still produce a
        # byte-identical archive -- info.mode was left at the on-disk
        # bits, unlike mtime/uid/gid/uname/gname, which were already
        # pinned (CodeRabbit review, fresh evidence).
        product_a = _make_product(tmp_path / "a")
        product_b = _make_product(tmp_path / "b")
        (product_b / "lib" / "libb.so").chmod(0o664)
        out_a = tmp_path / "a.tar.zst"
        out_b = tmp_path / "b.tar.zst"
        pack_product_baseline(product_a, out_a)
        pack_product_baseline(product_b, out_b)
        assert out_a.read_bytes() == out_b.read_bytes()

    def test_pack_records_header_roots(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        manifest = pack_product_baseline(
            product, tmp_path / "b.tar.zst", header_roots=["include"]
        )
        assert manifest.header_roots == ("include",)

    def test_pack_preserves_an_empty_header_root_directory(
        self, tmp_path: Path
    ) -> None:
        # A --header-root naming an existing but currently-empty directory
        # must still exist in DEST_DIR after unpack, or the documented
        # follow-on `compare -H` workflow is unusable (Codex review, fresh
        # evidence: _discover_paths only archives files/symlinks, so an
        # empty directory used to vanish entirely).
        product = _make_product(tmp_path)
        empty_header_root = product / "empty-include"
        empty_header_root.mkdir()
        archive = tmp_path / "b.tar.zst"
        pack_product_baseline(product, archive, header_roots=["empty-include"])

        dest = tmp_path / "unpacked"
        unpack_product_baseline(archive, dest)
        assert (dest / "empty-include").is_dir()

    def test_pack_preserves_a_nested_empty_directory(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        (product / "extra" / "nested" / "empty").mkdir(parents=True)
        archive = tmp_path / "b.tar.zst"
        pack_product_baseline(product, archive)

        dest = tmp_path / "unpacked"
        unpack_product_baseline(archive, dest)
        assert (dest / "extra" / "nested" / "empty").is_dir()

    def test_pack_rejects_missing_source_dir(self, tmp_path: Path) -> None:
        with pytest.raises(SnapshotError, match="not a directory"):
            pack_product_baseline(tmp_path / "nope", tmp_path / "out.tar.zst")

    def test_pack_rejects_non_tar_zst_output_suffix(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        with pytest.raises(SnapshotError, match=r"tar\.zst"):
            pack_product_baseline(product, tmp_path / "out.tar.gz")

    def test_pack_rejects_empty_source_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SnapshotError, match="no files found"):
            pack_product_baseline(empty, tmp_path / "out.tar.zst")

    def test_pack_skips_a_file_that_vanishes_during_the_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # os.walk() lists directory entries first; lstat() runs later --
        # a file removed in that window must be skipped, not propagate a
        # bare FileNotFoundError out of pack_product_baseline() (which the
        # CLI's `except SnapshotError` wouldn't catch either) (CodeRabbit
        # review, fresh evidence).
        product = _make_product(tmp_path)
        vanished = product / "lib" / "libb.so"
        real_lstat = Path.lstat

        def flaky_lstat(self: Path) -> os.stat_result:
            if self == vanished:
                raise FileNotFoundError(vanished)
            return real_lstat(self)

        monkeypatch.setattr(Path, "lstat", flaky_lstat)
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        assert "libb.so" not in {lib.name for lib in manifest.libraries}

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
        # A *relative* target that still walks outside source_dir --
        # distinct from an absolute target (test_pack_rejects_absolute_
        # symlink_target below), which is rejected earlier for a
        # different reason regardless of where it points.
        (product / "lib" / "evil.so").symlink_to(Path("..") / ".." / "outside.so")
        with pytest.raises(SnapshotError, match="escapes"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_absolute_symlink_target(self, tmp_path: Path) -> None:
        # An absolute target inside SOURCE_DIR at pack time can't round-trip
        # -- it names a path that won't exist at that same absolute
        # location once unpacked into a different staging directory, so
        # the paired unpack would fail TarExtractor's own symlink-escape
        # check on an archive that packed successfully (Codex review,
        # fresh evidence).
        product = _make_product(tmp_path)
        target = product / "lib" / "libb.so"
        (product / "lib" / "absolute-link.so").symlink_to(target.resolve())
        with pytest.raises(SnapshotError, match="absolute target"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_preserves_hardlinked_duplicate_content(self, tmp_path: Path) -> None:
        # gettarinfo() converts a second path sharing an inode with an
        # already-archived one into a hardlink (LNKTYPE) member -- which
        # is neither isreg() nor issym(), so it used to fall through
        # _add_member's "not info.isreg(): return None" guard and vanish
        # from the archive entirely (CodeRabbit review, fresh evidence).
        product = _make_product(tmp_path)
        first = product / "lib" / "libb.so"
        second = product / "lib" / "libb-alias.so"
        second.hardlink_to(first)

        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        dest = tmp_path / "unpacked"
        unpack_product_baseline(archive, dest)
        restored_first = dest / "lib" / "libb.so"
        restored_second = dest / "lib" / "libb-alias.so"
        assert restored_first.is_file()
        assert restored_second.is_file()
        assert restored_second.read_bytes() == restored_first.read_bytes()

    def test_pack_records_a_library_entry_for_a_hardlinked_library(
        self, tmp_path: Path
    ) -> None:
        # A hardlink member's own info.size is 0 -- no data bytes follow it
        # in the tar stream -- so the first-archived-copy-only code path
        # used to silently omit a library-named hardlink from the manifest
        # even though its content round-trips correctly (Codex review,
        # fresh evidence).
        product = _make_product(tmp_path)
        first = product / "lib" / "libb.so"
        second = product / "lib" / "libb-alias.so"
        second.hardlink_to(first)

        manifest = pack_product_baseline(product, tmp_path / "baseline.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert "libb.so" in names
        assert "libb-alias.so" in names
        by_name = {lib.name: lib for lib in manifest.libraries}
        assert by_name["libb-alias.so"].sha256 == by_name["libb.so"].sha256
        assert by_name["libb-alias.so"].size == by_name["libb.so"].size

    def test_pack_rejects_source_file_colliding_with_manifest_name(
        self, tmp_path: Path
    ) -> None:
        # A real source file at the reserved manifest path would otherwise
        # be silently overwritten on extraction by the generated manifest
        # member, added last (Codex review, fresh evidence).
        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME).write_text('{"not": "the real manifest"}\n')
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_empty_directory_colliding_with_manifest_name(
        self, tmp_path: Path
    ) -> None:
        # An empty directory at the reserved manifest path isn't in
        # `paths` (files/symlinks only), so the file-only collision check
        # missed it -- packing would succeed and write a tar requiring
        # the same path to be both a directory and (from the generated
        # manifest, added last) a regular file, which the paired unpack
        # then fails on with an unhandled IsADirectoryError (Codex
        # review, fresh evidence).
        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME).mkdir()
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_non_empty_directory_colliding_with_manifest_name(
        self, tmp_path: Path
    ) -> None:
        # A non-empty directory at the reserved path: its own children
        # are in `paths` (prefixed by the reserved name), but the
        # directory entry itself never is -- a distinct gap from the
        # empty-directory case above (Codex review, fresh evidence).
        product = _make_product(tmp_path)
        collide_dir = product / MANIFEST_MEMBER_NAME
        collide_dir.mkdir()
        (collide_dir / "child.txt").write_text("not the real manifest\n")
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_repeated_invocation_into_source_dir_does_not_grow(
        self, tmp_path: Path
    ) -> None:
        # pack SOURCE_DIR SOURCE_DIR/baseline.tar.zst, run twice -- the
        # second run must not embed the first run's own output as an input
        # member (Codex review, fresh evidence: this used to grow the
        # archive and break the determinism promise).
        product = _make_product(tmp_path)
        out = product / "baseline.tar.zst"
        manifest1 = pack_product_baseline(product, out)
        first_size = out.stat().st_size
        manifest2 = pack_product_baseline(product, out)
        assert out.stat().st_size == first_size
        assert manifest1.libraries == manifest2.libraries
        assert manifest1.file_count == manifest2.file_count

    def test_pack_excludes_output_when_output_path_is_itself_a_symlink(
        self, tmp_path: Path
    ) -> None:
        # If OUTPUT already exists as a symlink (e.g. left over from a
        # prior run pointing elsewhere), excluding by the *resolved*
        # target path -- instead of OUTPUT's own lexical path -- wrongly
        # excludes an unrelated real file while leaving the symlink
        # itself (keyed by its own name) in the archive, corrupting it
        # (Codex review, fresh evidence).
        product = _make_product(tmp_path)
        out = product / "baseline.tar.zst"
        out.symlink_to(product / "lib" / "libb.so")
        manifest = pack_product_baseline(product, out)
        names = {lib.name for lib in manifest.libraries}
        assert "libb.so" in names

        dest = tmp_path / "unpacked"
        unpack_product_baseline(out, dest)
        assert (dest / "lib" / "libb.so").is_file()

    def test_pack_repeated_invocation_via_source_dir_alias_does_not_grow(
        self, tmp_path: Path
    ) -> None:
        # SOURCE_DIR itself given as a symlink alias (`pack /tmp/link OUT`,
        # `/tmp/link -> /tmp/product`), with OUTPUT spelled through the
        # real, non-aliased directory -- a purely lexical (os.path.abspath)
        # comparison never shares a prefix with the alias path, so nothing
        # gets excluded and a rerun embeds the previous archive (Codex
        # review, fresh evidence, distinct from the in-tree-output and
        # symlinked-OUTPUT cases already covered above).
        product = _make_product(tmp_path)
        alias = tmp_path / "product-link"
        alias.symlink_to(product, target_is_directory=True)
        out = product / "baseline.tar.zst"

        manifest1 = pack_product_baseline(alias, out)
        first_size = out.stat().st_size
        manifest2 = pack_product_baseline(alias, out)
        assert out.stat().st_size == first_size
        assert manifest1.libraries == manifest2.libraries
        assert manifest1.file_count == manifest2.file_count

    def test_pack_into_not_yet_existing_subdirectory_is_deterministic_across_reruns(
        self, tmp_path: Path
    ) -> None:
        # OUTPUT under a not-yet-existing subdirectory of SOURCE_DIR
        # (SOURCE_DIR/artifacts/base.tar.zst): creating that directory
        # only after discovery meant the first pack never saw
        # `artifacts/` at all, while a second pack -- run after mkdir
        # already created it -- discovered it as an empty directory
        # (its only file, OUTPUT itself, excluded) and added an explicit
        # directory member, changing file_count and archive bytes between
        # two runs of the identical invocation (Codex review, fresh
        # evidence).
        product = _make_product(tmp_path)
        out = product / "artifacts" / "base.tar.zst"
        manifest1 = pack_product_baseline(product, out)
        first_size = out.stat().st_size
        manifest2 = pack_product_baseline(product, out)
        assert out.stat().st_size == first_size
        assert manifest1.file_count == manifest2.file_count
        assert manifest1.libraries == manifest2.libraries

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

    def test_unpack_destination_gets_umask_derived_permissions(
        self, tmp_path: Path
    ) -> None:
        # tempfile.mkdtemp() (the staging dir) is always 0700 regardless of
        # umask -- the published DEST_DIR must not inherit that, or a
        # later step running as a different user/UID can't even traverse
        # it (CodeRabbit review, fresh evidence).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        dest = tmp_path / "unpacked"
        unpack_product_baseline(archive, dest)
        mode = stat_mod.S_IMODE(dest.stat().st_mode)
        umask = os.umask(0)
        os.umask(umask)
        assert mode == (0o777 & ~umask)

    def test_unpack_rejects_non_tar_zst_suffix(self, tmp_path: Path) -> None:
        bogus = tmp_path / "baseline.tar.gz"
        bogus.write_bytes(b"not really a tar.zst")
        with pytest.raises(SnapshotError, match=r"tar\.zst"):
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

    def test_unpack_rejects_symlink_destination(self, tmp_path: Path) -> None:
        # A symlink to a genuinely empty directory used to pass the
        # empty-destination check and then crash at publish time:
        # Path.rmdir() operates on the symlink itself (POSIX rmdir()
        # never follows a final symlink component), raising an unhandled
        # NotADirectoryError past the CLI's SnapshotError-only catch and
        # leaving the already-validated staging directory behind
        # uncleaned (Codex review, fresh evidence).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        real_empty_dir = tmp_path / "real-empty"
        real_empty_dir.mkdir()
        dest = tmp_path / "dest-symlink"
        dest.symlink_to(real_empty_dir)
        with pytest.raises(SnapshotError, match="symlink"):
            unpack_product_baseline(archive, dest)

    def test_unpack_leaves_missing_destination_absent_on_bad_archive(
        self, tmp_path: Path
    ) -> None:
        # Validate-before-populate (Codex review, fresh evidence): a
        # missing/corrupt manifest must not leave any extracted content
        # behind, so a retry with a corrected archive doesn't fail with
        # "destination is not empty".
        bogus = self._plain_tar_zst(tmp_path)
        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match=MANIFEST_MEMBER_NAME):
            unpack_product_baseline(bogus, dest)
        assert not dest.exists()

    def test_unpack_leaves_existing_empty_destination_empty_on_bad_archive(
        self, tmp_path: Path
    ) -> None:
        bogus = self._plain_tar_zst(tmp_path)
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(SnapshotError, match=MANIFEST_MEMBER_NAME):
            unpack_product_baseline(bogus, dest)
        assert dest.is_dir()
        assert not list(dest.iterdir())

    def test_unpack_leaves_no_staging_directory_behind_on_failure(
        self, tmp_path: Path
    ) -> None:
        bogus = self._plain_tar_zst(tmp_path)
        with pytest.raises(SnapshotError):
            unpack_product_baseline(bogus, tmp_path / "dest")
        assert not list(tmp_path.glob(".abicheck-product-baseline-unpack-*"))

    @staticmethod
    def _plain_tar_zst(tmp_path: Path) -> Path:
        import io
        import tarfile

        import zstandard

        plain_tar = tmp_path / "plain.tar.zst"
        payload = tmp_path / "payload.tar"
        with tarfile.open(payload, "w") as tf:
            info = tarfile.TarInfo(name="not-a-baseline.txt")
            data = b"hello\n"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        cctx = zstandard.ZstdCompressor()
        plain_tar.write_bytes(cctx.compress(payload.read_bytes()))
        return plain_tar

    def test_unpack_rejects_archive_without_manifest(self, tmp_path: Path) -> None:
        plain_tar = self._plain_tar_zst(tmp_path)
        with pytest.raises(SnapshotError, match=MANIFEST_MEMBER_NAME):
            unpack_product_baseline(plain_tar, tmp_path / "dest")

    def test_unpack_translates_corrupt_zstd_data_to_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        # A corrupt/truncated .tar.zst must raise SnapshotError (which the
        # CLI catches and reports as exit 64), not an unhandled
        # zstandard.ZstdError escaping TarExtractor.extract() (Codex
        # review, fresh evidence).
        bogus = tmp_path / "corrupt.tar.zst"
        bogus.write_bytes(b"\x28\xb5\x2f\xfd" + b"not really zstd content")
        with pytest.raises(SnapshotError, match="failed to extract"):
            unpack_product_baseline(bogus, tmp_path / "dest")

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
