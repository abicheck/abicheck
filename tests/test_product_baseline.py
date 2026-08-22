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

"""Unit tests for :mod:`abicheck.product_baseline` -- the pack/unpack
library primitives. Library-only surface -- no CLI wiring."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat as stat_mod
import tarfile
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


def _rewrite_manifest_member(
    tmp_path: Path, archive: Path, manifest_payload: bytes
) -> Path:
    """Rewrite *archive*'s manifest member to *manifest_payload*, keeping
    every other member as-is -- for tests exercising unpack's handling of
    a hand-edited/adversarial manifest that a real pack_product_baseline()
    call could never produce."""
    import zstandard

    raw = archive.read_bytes()
    dctx = zstandard.ZstdDecompressor()
    tar_bytes = dctx.decompress(raw, max_output_size=1 << 30)
    rewritten = tmp_path / f"{archive.stem}-rewritten.tar"
    with (
        tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as src,
        tarfile.open(rewritten, mode="w") as dst,
    ):
        for member in src.getmembers():
            if member.name == MANIFEST_MEMBER_NAME:
                info = tarfile.TarInfo(name=MANIFEST_MEMBER_NAME)
                info.size = len(manifest_payload)
                dst.addfile(info, io.BytesIO(manifest_payload))
            else:
                fh = src.extractfile(member)
                dst.addfile(member, fh)

    bad_archive = tmp_path / f"{archive.stem}-bad.tar.zst"
    cctx = zstandard.ZstdCompressor()
    with (
        open(rewritten, "rb") as in_fh,
        open(bad_archive, "wb") as out_fh,
    ):
        cctx.copy_stream(in_fh, out_fh)
    return bad_archive


def _rewrite_archive_member(
    tmp_path: Path, archive: Path, member_name: str, payload: bytes
) -> Path:
    """Rewrite *archive*'s *member_name* member to *payload*, keeping every
    other member (the manifest included) as-is -- for tests exercising
    unpack's handling of an archive whose actual content diverges from what
    its own manifest declares (a stale/tampered archive), which a real
    pack_product_baseline() call could never produce."""
    import zstandard

    raw = archive.read_bytes()
    dctx = zstandard.ZstdDecompressor()
    tar_bytes = dctx.decompress(raw, max_output_size=1 << 30)
    rewritten = tmp_path / f"{archive.stem}-rewritten.tar"
    with (
        tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as src,
        tarfile.open(rewritten, mode="w") as dst,
    ):
        for member in src.getmembers():
            if member.name == member_name:
                info = tarfile.TarInfo(name=member_name)
                info.size = len(payload)
                dst.addfile(info, io.BytesIO(payload))
            else:
                fh = src.extractfile(member)
                dst.addfile(member, fh)

    tampered_archive = tmp_path / f"{archive.stem}-tampered.tar.zst"
    cctx = zstandard.ZstdCompressor()
    with (
        open(rewritten, "rb") as in_fh,
        open(tampered_archive, "wb") as out_fh,
    ):
        cctx.copy_stream(in_fh, out_fh)
    return tampered_archive


def _add_symlink_loop_member(tmp_path: Path, archive: Path, name: str) -> Path:
    """Add a new, self-referential symlink member (*name* -> *name*) to
    *archive*, keeping every existing member as-is -- for tests exercising
    unpack's handling of an untrusted archive containing a genuine
    symlink loop, which a real pack_product_baseline() call could never
    produce."""
    import zstandard

    raw = archive.read_bytes()
    dctx = zstandard.ZstdDecompressor()
    tar_bytes = dctx.decompress(raw, max_output_size=1 << 30)
    rewritten = tmp_path / f"{archive.stem}-rewritten.tar"
    with (
        tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as src,
        tarfile.open(rewritten, mode="w") as dst,
    ):
        for member in src.getmembers():
            fh = src.extractfile(member)
            dst.addfile(member, fh)
        loop_info = tarfile.TarInfo(name=name)
        loop_info.type = tarfile.SYMTYPE
        loop_info.linkname = name
        dst.addfile(loop_info)

    bad_archive = tmp_path / f"{archive.stem}-loop.tar.zst"
    cctx = zstandard.ZstdCompressor()
    with (
        open(rewritten, "rb") as in_fh,
        open(bad_archive, "wb") as out_fh,
    ):
        cctx.copy_stream(in_fh, out_fh)
    return bad_archive


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
        # Mirrors this codebase's established defensive from_dict() contract --
        # degrades to empty, no raise (a newer/hand-edited pack never aborts).
        manifest = ProductBaselineManifest.from_dict(
            {"libraries": "not-a-list", "header_roots": None, "extra": "ignored"}
        )
        assert manifest.libraries == ()
        assert manifest.header_roots == ()
        assert manifest.schema == PRODUCT_BASELINE_SCHEMA

    def test_from_dict_tolerates_non_numeric_size_and_file_count(self) -> None:
        # A hand-edited/corrupt manifest.json could carry a non-numeric
        # "size"/"file_count" -- must degrade to 0, not raise ValueError (Codex).
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

    def test_pack_records_extensionless_elf_library_in_the_manifest(
        self, tmp_path: Path
    ) -> None:
        # _add_member's classification previously used the filename-only
        # _is_shared_library check -- an extensionless ELF DSO got no LibraryEntry.
        from tests.test_package import _make_minimal_elf_so

        product = _make_product(tmp_path)
        _make_minimal_elf_so(product / "lib" / "plugin_no_suffix")
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert "plugin_no_suffix" in names

    def test_pack_does_not_classify_split_debug_companion_as_a_library(
        self, tmp_path: Path
    ) -> None:
        # "libfoo.so.1.debug" contains the literal substring ".so." (the dot
        # before "1"), so a naive check misclassified a split-debug file (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "libb.so.1.debug").write_bytes(b"DEBUGINFO")
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert "libb.so.1.debug" not in names

    def test_pack_does_not_classify_a_linker_script_as_a_library(
        self, tmp_path: Path
    ) -> None:
        # A GNU ld INPUT()/GROUP() script is library-suffix-named but carries no
        # binary content -- used to be advertised as its own LibraryEntry (Codex).
        from tests.test_package import _make_minimal_elf_so

        product = _make_product(tmp_path)
        _make_minimal_elf_so(product / "lib" / "libscripted.so.1")
        (product / "lib" / "libscripted.so").write_text("INPUT(libscripted.so.1)\n")

        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert "libscripted.so.1" in names
        assert "libscripted.so" not in names

    def test_pack_does_not_misclassify_a_real_binary_containing_ld_text(
        self, tmp_path: Path
    ) -> None:
        # resolve_linker_script()'s probe is a regex search over the first 8KiB
        # with no binary-magic check -- a real binary containing "INPUT(" matched.
        from tests.test_package import _make_minimal_elf_so

        product = _make_product(tmp_path)
        elf_path = product / "lib" / "libreal.so"
        _make_minimal_elf_so(elf_path)
        # Append linker-script-shaped text after the real ELF header -- models an
        # embedded symbol name within resolve_linker_script's 8KiB probe window.
        with elf_path.open("ab") as fh:
            fh.write(b"\x00INPUT(libdoesnotexist.so)\x00")

        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        names = {lib.name for lib in manifest.libraries}
        assert "libreal.so" in names

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
        # Two trees differing only in file mode must still produce a byte-
        # identical archive -- unlike mtime/uid/gid, mode wasn't pinned (CodeRabbit).
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
        # A header root naming an existing but empty directory must still
        # exist after unpack (Codex: _discover_paths only archives
        # files/symlinks, so an empty directory used to vanish).
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

    def test_pack_rejects_empty_source_dir_with_in_tree_subdir_output(
        self, tmp_path: Path
    ) -> None:
        # OUTPUT under a not-yet-existing subdirectory is mkdir()'d before
        # discovery, but that scaffold carries no real content (Codex).
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SnapshotError, match="no files found"):
            pack_product_baseline(empty, empty / "artifacts" / "base.tar.zst")

    def test_pack_rejects_empty_source_dir_identically_on_repeated_calls(
        self, tmp_path: Path
    ) -> None:
        # The mkdir() call above leaves the scaffold dir behind on disk even
        # though pack raised -- a second call must not see it as real content.
        empty = tmp_path / "empty"
        empty.mkdir()
        out = empty / "artifacts" / "base.tar.zst"
        with pytest.raises(SnapshotError, match="no files found"):
            pack_product_baseline(empty, out)
        assert not (empty / "artifacts").exists()
        with pytest.raises(SnapshotError, match="no files found"):
            pack_product_baseline(empty, out)

    def test_pack_rejects_empty_source_dir_with_mixed_relative_absolute_spelling(
        self, tmp_path: Path
    ) -> None:
        # An absolute SOURCE_DIR paired with a RELATIVE OUTPUT under the identical
        # tree used to make the lexical containment check spuriously fail (CodeRabbit).
        empty = tmp_path / "empty"
        empty.mkdir()
        cwd = Path.cwd()
        os.chdir(empty)
        try:
            out = Path("artifacts") / "base.tar.zst"
            with pytest.raises(SnapshotError, match="no files found"):
                pack_product_baseline(empty, out)
        finally:
            os.chdir(cwd)

    def test_pack_rejects_empty_source_dir_when_source_is_a_symlink_alias(
        self, tmp_path: Path
    ) -> None:
        # SOURCE_DIR as a symlink alias, OUTPUT through the real directory -- a
        # purely lexical comparison never shares a prefix with the alias.
        real = tmp_path / "source"
        real.mkdir()
        alias = tmp_path / "source-link"
        alias.symlink_to(real, target_is_directory=True)
        out = real / "artifacts" / "base.tar.zst"
        with pytest.raises(SnapshotError, match="no files found"):
            pack_product_baseline(alias, out)

    def test_pack_skips_a_file_that_vanishes_during_the_walk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # os.walk() lists entries first; lstat() runs later -- a file removed
        # in that window must be skipped, not propagate FileNotFoundError.
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

    def test_pack_rejects_a_bare_string_header_roots(self, tmp_path: Path) -> None:
        # header_roots="include" -- a typo for ["include"] -- satisfies Sequence[str].
        product = _make_product(tmp_path)
        (product / "include").mkdir(exist_ok=True)
        with pytest.raises(SnapshotError, match="bare string"):
            pack_product_baseline(
                product, tmp_path / "b.tar.zst", header_roots="include"
            )

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

    def test_pack_rejects_header_root_that_is_a_regular_file(
        self, tmp_path: Path
    ) -> None:
        # compare_product_directories() only includes a header root when
        # .is_dir() is true -- one naming a regular file must be rejected at pack time.
        product = _make_product(tmp_path)
        with pytest.raises(SnapshotError, match="header root"):
            pack_product_baseline(
                product, tmp_path / "b.tar.zst", header_roots=["README.txt"]
            )

    def test_pack_rejects_symlink_escaping_source_dir(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        outside = tmp_path / "outside.so"
        outside.write_bytes(b"outside")
        # A *relative* target that still walks outside source_dir -- distinct
        # from an absolute target, rejected earlier regardless of where it points.
        (product / "lib" / "evil.so").symlink_to(Path("..") / ".." / "outside.so")
        with pytest.raises(SnapshotError, match="escapes"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_absolute_symlink_target(self, tmp_path: Path) -> None:
        # An absolute target inside SOURCE_DIR can't round-trip -- it names a path
        # that won't exist once unpacked elsewhere (TarExtractor's own check).
        product = _make_product(tmp_path)
        target = product / "lib" / "libb.so"
        (product / "lib" / "absolute-link.so").symlink_to(target.resolve())
        with pytest.raises(SnapshotError, match="absolute target"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_windows_drive_absolute_symlink_target(
        self, tmp_path: Path
    ) -> None:
        # os.path.isabs() doesn't recognize a Windows drive-absolute target as
        # absolute on POSIX, even though it's absolute on Windows (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "windows-abs.so").symlink_to("C:\\outside\\foo.dll")
        with pytest.raises(SnapshotError, match="absolute target"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_backslash_traversal_symlink_target(
        self, tmp_path: Path
    ) -> None:
        # A relative target spelled with backslash ".." is one opaque component
        # on POSIX -- but genuinely walks above root under Windows (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "evil.so").symlink_to("..\\..\\outside.so")
        with pytest.raises(SnapshotError, match="backslash"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_symlink_target_with_a_benign_backslash(
        self, tmp_path: Path
    ) -> None:
        # A backslash target decomposing into neither an escape nor an anchor
        # (`sub\file.so`) still restores fine on Windows (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "benign.so").symlink_to("sub\\file.so")
        with pytest.raises(SnapshotError, match="backslash"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_windows_root_relative_symlink_target(
        self, tmp_path: Path
    ) -> None:
        # A current-drive-rooted target (no drive letter, leading backslash) is
        # anchored on Windows -- but PureWindowsPath.is_absolute() is False (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "root-rel.so").symlink_to("\\outside\\foo.dll")
        with pytest.raises(SnapshotError, match="absolute target"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_windows_drive_relative_symlink_target(
        self, tmp_path: Path
    ) -> None:
        # A drive-relative target (C:outside\\foo.dll) resolves against the current directory on that drive on Windows -- also False under is_absolute() (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "drive-rel.so").symlink_to("C:outside\\foo.dll")
        with pytest.raises(SnapshotError, match="absolute target"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_windows_drive_anchored_member_name(
        self, tmp_path: Path
    ) -> None:
        # Only symlink *targets* got Windows-anchor validation -- a real file
        # named `C:library.dll` (valid on POSIX) is archived unchanged.
        product = _make_product(tmp_path)
        (product / "C:library.dll").write_bytes(b"MZ")
        with pytest.raises(SnapshotError, match="anchored under Windows"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_backslash_traversal_member_name(
        self, tmp_path: Path
    ) -> None:
        # A real filename literally containing backslashes decomposes into a
        # genuine ".." under Windows separators (unconditional rejection).
        product = _make_product(tmp_path)
        (product / "lib" / "dir\\..\\outside.so").write_bytes(b"ELF")
        with pytest.raises(SnapshotError, match="backslash"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_member_name_with_a_benign_backslash(
        self, tmp_path: Path
    ) -> None:
        # A backslash decomposing into neither an anchor nor a traversal
        # (`foo\bar.so`) still restores fine on Windows (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "foo\\bar.so").write_bytes(b"ELF")
        with pytest.raises(SnapshotError, match="backslash"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    @pytest.mark.skipif(
        os.name == "nt",
        reason="fixture needs a POSIX-only filename Windows cannot create",
    )
    def test_pack_rejects_a_reserved_windows_device_name(self, tmp_path: Path) -> None:
        # A reserved device name even with an extension decomposes safely but is unrepresentable on Windows.
        product = _make_product(tmp_path)
        (product / "aux.txt").write_bytes(b"MZ")
        with pytest.raises(SnapshotError, match="reserved Windows device name"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    @pytest.mark.skipif(
        os.name == "nt",
        reason="fixture needs a filename Windows cannot itself create",
    )
    @pytest.mark.parametrize(
        "reserved_name",
        ["CONIN$", "CONOUT$", "COM0", "LPT0", "COM¹", "COM²", "COM³", "LPT¹"],
    )
    def test_pack_rejects_further_reserved_windows_device_names(
        self, tmp_path: Path, reserved_name: str
    ) -> None:
        # Beyond CON/PRN/AUX/NUL/COM1-9/LPT1-9, Windows reserves CONIN$/CONOUT$
        # (console I/O) and COM0/LPT0, plus the superscript-digit spellings
        # COM¹/COM²/COM³/LPT¹/LPT²/LPT³ Windows treats as equivalent (Codex).
        product = _make_product(tmp_path)
        (product / f"{reserved_name}.txt").write_bytes(b"data")
        with pytest.raises(SnapshotError, match="reserved Windows device name"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    @pytest.mark.skipif(
        os.name == "nt",
        reason="fixture needs a POSIX-only filename Windows cannot create",
    )
    def test_pack_rejects_a_forbidden_windows_character(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        (product / "lib" / "dir:stream").write_bytes(b"ELF")
        with pytest.raises(SnapshotError, match="forbids in a path component"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    @pytest.mark.skipif(
        os.name == "nt",
        reason="fixture needs a POSIX-only filename Windows cannot create",
    )
    def test_pack_rejects_a_trailing_dot_component(self, tmp_path: Path) -> None:
        # Windows silently strips a trailing dot/space, so "name." and
        # "name" would collide once unpacked there (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "name.").write_bytes(b"ELF")
        with pytest.raises(SnapshotError, match="stripping its trailing dot/space"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_case_colliding_file_names(self, tmp_path: Path) -> None:
        # Foo.dll/foo.dll are distinct on a case-sensitive host but
        # unify on Windows extraction, overwriting one (Codex).
        product = _make_product(tmp_path)
        (product / "Foo.dll").write_bytes(b"MZ" + b"\x00" * 20)
        (product / "foo.dll").write_bytes(b"MZ" + b"\x00" * 30)
        with pytest.raises(SnapshotError, match="collides with"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_case_colliding_directory_names(self, tmp_path: Path) -> None:
        # `Lib/a.so` vs `lib/b.so` have distinct full arcnames, but
        # Windows unifies `Lib`/`lib` into one directory (Codex).
        product = _make_product(tmp_path)
        (product / "Lib").mkdir()
        (product / "lib" / "a.so").write_bytes(b"ELF")
        (product / "Lib" / "b.so").write_bytes(b"ELF")
        with pytest.raises(SnapshotError, match="collides with"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_unicode_normalization_colliding_file_names(
        self, tmp_path: Path
    ) -> None:
        # NFC/NFD spellings are distinct bytes on Linux -- casefold() doesn't
        # compose/decompose -- but APFS/HFS+ treat them as one filename (Codex).
        import unicodedata

        product = _make_product(tmp_path)
        nfc = unicodedata.normalize("NFC", "café.so")
        nfd = unicodedata.normalize("NFD", "café.so")
        assert nfc != nfd  # confirms these are genuinely distinct bytes
        (product / nfc).write_bytes(b"\x7fELF" + b"\x00" * 20)
        (product / nfd).write_bytes(b"\x7fELF" + b"\x00" * 30)
        with pytest.raises(SnapshotError, match="collides with"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_manifest_name_case_variant(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME.upper()).write_bytes(b"not the manifest")
        with pytest.raises(SnapshotError, match="collides with"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_a_self_referential_symlink_loop(self, tmp_path: Path) -> None:
        # A self-referential symlink made _add_member()'s Path.resolve() raise
        # RuntimeError, caught by the sibling `except ValueError` path (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "loop.so").symlink_to("loop.so")
        with pytest.raises(SnapshotError, match="symlink loop"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_records_a_standalone_library_shaped_symlink(
        self, tmp_path: Path
    ) -> None:
        # A library-shaped symlink whose target isn't itself library-shaped
        # was omitted from the manifest, so it couldn't round-trip (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "payload").write_bytes(b"not a library, just data")
        (product / "lib" / "libfoo.so").symlink_to("payload")

        archive = tmp_path / "baseline.tar.zst"
        manifest = pack_product_baseline(product, archive)
        by_path = {lib.path: lib for lib in manifest.libraries}
        assert "lib/libfoo.so" in by_path
        assert by_path["lib/libfoo.so"].size == len(b"not a library, just data")

        # liba.so -> liba.so.1.2.3 (from _make_product()) must NOT gain a
        # redundant entry -- its target is already independently declared.
        assert "lib/liba.so" not in by_path

        dest = tmp_path / "unpacked"
        unpack_product_baseline(archive, dest)
        restored = dest / "lib" / "libfoo.so"
        assert restored.is_symlink()
        assert restored.read_bytes() == b"not a library, just data"

    def test_pack_rejects_a_symlink_target_matching_a_sibling_only_by_case(
        self, tmp_path: Path
    ) -> None:
        # `libfoo.so -> payload` is dangling here (only `Payload` exists) -- but
        # on a case-insensitive host it resolves live, rejected as undeclared.
        product = _make_product(tmp_path)
        (product / "lib" / "Payload").write_bytes(b"\x7fELF" + b"\x00" * 20)
        (product / "lib" / "libfoo.so").symlink_to("payload")
        with pytest.raises(SnapshotError, match="case/Unicode-folded"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_allows_a_genuinely_dangling_symlink_with_no_sibling_match(
        self, tmp_path: Path
    ) -> None:
        # A dangling target with no case/Unicode-folded sibling anywhere is not
        # ambiguous across platforms -- must still pack.
        product = _make_product(tmp_path)
        (product / "lib" / "dangling.so").symlink_to("does-not-exist-at-all.so")
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        assert not any(lib.path == "lib/dangling.so" for lib in manifest.libraries)

    def test_pack_rejects_a_symlink_target_case_mismatched_on_an_intermediate_component(
        self, tmp_path: Path
    ) -> None:
        # The case/Unicode-fold check must walk every target component, not
        # just the basename: `links/libfoo.so -> ../Payload/data` is dangling
        # on the *intermediate* `payload` directory, not `data` (Codex).
        product = _make_product(tmp_path)
        (product / "links").mkdir()
        (product / "Payload").mkdir()
        (product / "Payload" / "data").write_bytes(b"\x7fELF" + b"\x00" * 20)
        (product / "links" / "libfoo.so").symlink_to("../payload/data")
        with pytest.raises(SnapshotError, match="case/Unicode-folded"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_allows_a_symlink_chain_dangling_on_a_broken_intermediate_link(
        self, tmp_path: Path
    ) -> None:
        # `libfoo.so -> alias -> missing`: the exact-match check must be
        # lexical (`os.path.lexists`), not `.exists()` (which follows `alias`,
        # reading a broken chain as absent) (Codex).
        product = _make_product(tmp_path)
        (product / "lib" / "alias").symlink_to("missing")
        (product / "lib" / "libfoo.so").symlink_to("alias")
        manifest = pack_product_baseline(product, tmp_path / "b.tar.zst")
        assert not any(lib.path == "lib/libfoo.so" for lib in manifest.libraries)

    def test_pack_skips_a_library_shaped_symlink_targeting_a_directory(
        self, tmp_path: Path
    ) -> None:
        # A suffix-shaped symlink can target a directory (`plugins.so ->
        # payload/`) -- the standalone-symlink fix above hashed unconditionally
        # and raised an uncaught IsADirectoryError (Codex review).
        product = _make_product(tmp_path)
        (product / "lib" / "payload").mkdir()
        (product / "lib" / "payload" / "inner.txt").write_text("hi")
        (product / "lib" / "plugins.so").symlink_to("payload")

        archive = tmp_path / "baseline.tar.zst"
        manifest = pack_product_baseline(product, archive)
        by_path = {lib.path: lib for lib in manifest.libraries}
        assert "lib/plugins.so" not in by_path

        dest = tmp_path / "unpacked"
        unpack_product_baseline(archive, dest)
        restored = dest / "lib" / "plugins.so"
        assert restored.is_symlink()
        assert (restored / "inner.txt").read_text() == "hi"

    def test_pack_preserves_hardlinked_duplicate_content(self, tmp_path: Path) -> None:
        # gettarinfo() converts a second path sharing an inode into a hardlink
        # (LNKTYPE) member -- fell through _add_member's isreg() guard (CodeRabbit).
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
        # A hardlink member's own info.size is 0 -- the first-archived-copy-only
        # path silently omitted a library-named hardlink from the manifest.
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
        # A real source file at the reserved manifest path would otherwise be
        # silently overwritten by the generated manifest member, added last.
        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME).write_text('{"not": "the real manifest"}\n')
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_empty_directory_colliding_with_manifest_name(
        self, tmp_path: Path
    ) -> None:
        # An empty directory at the reserved path isn't in `paths`, so the
        # file-only collision check missed it (Codex).
        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME).mkdir()
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_non_empty_directory_colliding_with_manifest_name(
        self, tmp_path: Path
    ) -> None:
        # A non-empty directory at the reserved path: children are in `paths`,
        # but the directory entry itself never is (Codex).
        product = _make_product(tmp_path)
        collide_dir = product / MANIFEST_MEMBER_NAME
        collide_dir.mkdir()
        (collide_dir / "child.txt").write_text("not the real manifest\n")
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_rejects_nested_empty_subdirectory_under_manifest_name(
        self, tmp_path: Path
    ) -> None:
        # A directory at the reserved path with only empty subdirectories
        # isn't itself in `empty_dirs` -- only its nested empty leaf is (CodeRabbit).
        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME / "sub").mkdir(parents=True)
        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, tmp_path / "b.tar.zst")

    def test_pack_repeated_invocation_into_source_dir_does_not_grow(
        self, tmp_path: Path
    ) -> None:
        # pack SOURCE_DIR SOURCE_DIR/baseline.tar.zst, run twice -- must not
        # embed the first run's own output (Codex).
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
        # If OUTPUT exists as a symlink, excluding by its *resolved* target --
        # not its own lexical path -- wrongly excludes a real file (Codex).
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
        # SOURCE_DIR as a symlink alias, OUTPUT through the real directory --
        # lexical comparison never shares a prefix with the alias (Codex).
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
        # OUTPUT under a not-yet-existing subdirectory: the first pack never
        # saw it, while a second (post-mkdir) pack discovered it as empty --
        # changing file_count (Codex).
        product = _make_product(tmp_path)
        out = product / "artifacts" / "base.tar.zst"
        manifest1 = pack_product_baseline(product, out)
        first_size = out.stat().st_size
        manifest2 = pack_product_baseline(product, out)
        assert out.stat().st_size == first_size
        assert manifest1.file_count == manifest2.file_count
        assert manifest1.libraries == manifest2.libraries

    def test_pack_excludes_the_output_scaffold_directory_from_the_archive(
        self, tmp_path: Path
    ) -> None:
        # The sibling test above only checked file_count/libraries equality,
        # never unpacking to see `artifacts/` was itself still a member.
        product = _make_product(tmp_path)
        out = product / "artifacts" / "base.tar.zst"
        pack_product_baseline(product, out)

        dest = tmp_path / "dest"
        unpack_product_baseline(out, dest)
        assert not (dest / "artifacts").exists()

    def test_pack_excludes_the_scaffold_directory_identically_across_reruns(
        self, tmp_path: Path
    ) -> None:
        # Existence-gated exclusion excluded `artifacts/` on the first pack but
        # silently included it on a second, identical pack (already existed).
        product = _make_product(tmp_path)
        out = product / "artifacts" / "base.tar.zst"
        pack_product_baseline(product, out)
        pack_product_baseline(product, out)  # second run, artifacts/ now exists

        dest = tmp_path / "dest"
        unpack_product_baseline(out, dest)
        assert not (dest / "artifacts").exists()

    def test_pack_preserves_a_declared_header_root_holding_the_output(
        self, tmp_path: Path
    ) -> None:
        # OUTPUT inside a pre-existing declared header root -- real content.
        # Unqualified exclusion dropped it anyway (Codex).
        product = _make_product(tmp_path)
        (product / "public_headers").mkdir()
        out = product / "public_headers" / "base.tar.zst"
        manifest = pack_product_baseline(product, out, header_roots=["public_headers"])
        assert manifest.header_roots == ("public_headers",)

        dest = tmp_path / "dest"
        unpack_product_baseline(out, dest)
        assert (dest / "public_headers").is_dir()

    def test_pack_rejects_header_root_matching_the_output_scaffold_directory(
        self, tmp_path: Path
    ) -> None:
        # output=SOURCE/newheaders/base.tar.zst, header root naming a dir that
        # doesn't exist: the output-parent scaffold mkdir() fabricated an empty
        # `newheaders/` that then wrongly passed validation, since validation
        # ran *after* the mkdir (Codex).
        product = _make_product(tmp_path)
        out = product / "newheaders" / "base.tar.zst"
        with pytest.raises(SnapshotError, match="header root"):
            pack_product_baseline(product, out, header_roots=["newheaders"])
        # Scaffold must not be left behind, same as any other rejection.
        assert not (product / "newheaders").exists()

    def test_pack_cleans_up_scaffold_after_manifest_collision_rejection(
        self, tmp_path: Path
    ) -> None:
        # The scaffold-cleanup fix originally only wired the empty-source
        # rejection -- a *different* rejection (colliding with the reserved
        # manifest name) left the scaffold dir behind (Codex).
        from abicheck.product_baseline import MANIFEST_MEMBER_NAME

        product = _make_product(tmp_path)
        (product / MANIFEST_MEMBER_NAME).write_text("not a real manifest")
        out = product / "artifacts" / "base.tar.zst"

        with pytest.raises(SnapshotError, match="reserved product baseline manifest"):
            pack_product_baseline(product, out)
        assert not (product / "artifacts").exists()

        (product / MANIFEST_MEMBER_NAME).unlink()
        manifest = pack_product_baseline(product, out)
        assert manifest.file_count > 0

    def test_pack_cleans_up_scaffold_when_mkstemp_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # tempfile.mkstemp() sat between the two scaffold-cleanup try/except
        # blocks, uncovered by either -- a failure (ENOSPC) left it behind.
        import abicheck.product_baseline as pb_mod

        product = _make_product(tmp_path)
        out = product / "artifacts" / "base.tar.zst"

        real_mkstemp = pb_mod.tempfile.mkstemp

        def _failing_mkstemp(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("simulated ENOSPC")

        monkeypatch.setattr(pb_mod.tempfile, "mkstemp", _failing_mkstemp)
        with pytest.raises(OSError, match="simulated ENOSPC"):
            pack_product_baseline(product, out)
        assert not (product / "artifacts").exists()

        monkeypatch.setattr(pb_mod.tempfile, "mkstemp", real_mkstemp)
        manifest = pack_product_baseline(product, out)
        assert manifest.file_count > 0

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
        # mkdtemp() (the staging dir) is always 0700 regardless of umask --
        # published DEST_DIR must not inherit that (CodeRabbit).
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
        # A symlink to a genuinely empty directory passed the empty-destination
        # check and crashed at publish: Path.rmdir() operates on the symlink
        # itself, raising unhandled NotADirectoryError (Codex).
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
        # Validate-before-populate: a missing/corrupt manifest must not
        # leave extracted content behind, or a retry fails "not empty" (Codex).
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

    def test_unpack_rejects_tampered_library_content_size_mismatch(
        self, tmp_path: Path
    ) -> None:
        # The manifest's size/sha256 are trusted at pack time but never
        # re-verified against extracted bytes at unpack (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        tampered = _rewrite_archive_member(
            tmp_path, archive, "lib/libb.so", b"TRUNCATED"
        )
        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="size mismatch"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_rejects_tampered_library_content_checksum_mismatch(
        self, tmp_path: Path
    ) -> None:
        # Same-length corruption -- the size check alone wouldn't catch
        # this; only the sha256 comparison does.
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        original_len = len(b"ELF-B" * 300)
        corrupted = b"X" * original_len
        tampered = _rewrite_archive_member(tmp_path, archive, "lib/libb.so", corrupted)
        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="checksum mismatch"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_rejects_a_blanked_sha256_even_with_a_correct_size(
        self, tmp_path: Path
    ) -> None:
        # An earlier revision used an `if lib.sha256:` truthiness guard,
        # letting an attacker blank the field to disable verification (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        tar_bytes = dctx.decompress(archive.read_bytes(), max_output_size=1 << 30)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
            manifest_fh = tf.extractfile(MANIFEST_MEMBER_NAME)
            assert manifest_fh is not None
            manifest_raw = json.loads(manifest_fh.read())
        for lib in manifest_raw["libraries"]:
            if lib["path"] == "lib/libb.so":
                lib["sha256"] = ""  # size is left correct/untouched
        payload = json.dumps(manifest_raw).encode("utf-8") + b"\n"
        tampered = _rewrite_manifest_member(tmp_path, archive, payload)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="missing or malformed sha256"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_rejects_an_extracted_library_with_no_manifest_entry(
        self, tmp_path: Path
    ) -> None:
        # The per-library checksum verification above only examines declared
        # entries -- an attacker could omit a LibraryEntry entirely (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        tar_bytes = dctx.decompress(archive.read_bytes(), max_output_size=1 << 30)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
            manifest_fh = tf.extractfile(MANIFEST_MEMBER_NAME)
            assert manifest_fh is not None
            manifest_raw = json.loads(manifest_fh.read())
        manifest_raw["libraries"] = [
            lib for lib in manifest_raw["libraries"] if lib["path"] != "lib/libb.so"
        ]
        payload = json.dumps(manifest_raw).encode("utf-8") + b"\n"
        tampered = _rewrite_manifest_member(tmp_path, archive, payload)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="never declared"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_rejects_an_undeclared_hardlink_library_alias(
        self, tmp_path: Path
    ) -> None:
        # The cross-check compares by identity, right for a symlink alias --
        # but a hardlink alias gets its own entry (Codex).
        product = _make_product(tmp_path)
        first = product / "lib" / "libb.so"
        second = product / "lib" / "libb-alias.so"
        second.hardlink_to(first)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        tar_bytes = dctx.decompress(archive.read_bytes(), max_output_size=1 << 30)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
            manifest_fh = tf.extractfile(MANIFEST_MEMBER_NAME)
            assert manifest_fh is not None
            manifest_raw = json.loads(manifest_fh.read())
        manifest_raw["libraries"] = [
            lib
            for lib in manifest_raw["libraries"]
            if lib["path"] != "lib/libb-alias.so"
        ]
        payload = json.dumps(manifest_raw).encode("utf-8") + b"\n"
        tampered = _rewrite_manifest_member(tmp_path, archive, payload)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="never declared"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_rejects_an_undeclared_hardlink_alias_masked_by_a_symlink(
        self, tmp_path: Path
    ) -> None:
        # The hardlink check compared against _discover_library_map()'s
        # deduplicated map -- a symlink alias sharing identity, sorted before
        # an undeclared hardlink alias, survives dedup as the sole
        # representative, hiding it (Codex).
        product = _make_product(tmp_path)
        target = product / "lib" / "z.so"
        target.write_bytes(b"ELF-CONTENT")
        (product / "lib" / "a.so").symlink_to("z.so")
        (product / "lib" / "b.so").hardlink_to(target)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        tar_bytes = dctx.decompress(archive.read_bytes(), max_output_size=1 << 30)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
            manifest_fh = tf.extractfile(MANIFEST_MEMBER_NAME)
            assert manifest_fh is not None
            manifest_raw = json.loads(manifest_fh.read())
        manifest_raw["libraries"] = [
            lib for lib in manifest_raw["libraries"] if lib["path"] != "lib/b.so"
        ]
        payload = json.dumps(manifest_raw).encode("utf-8") + b"\n"
        tampered = _rewrite_manifest_member(tmp_path, archive, payload)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="never declared"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_hashes_each_hardlinked_payload_only_once(
        self, tmp_path: Path
    ) -> None:
        # N library-shaped hardlink aliases of one payload each get their own
        # declared LibraryEntry (unlike a symlink alias) -- hashing every
        # entry's full content independently turns N declared aliases into N
        # full reads of the same bytes, a cost an attacker controls (Codex).
        product = _make_product(tmp_path)
        first = product / "lib" / "lib0.so"
        first.write_bytes(b"\x7fELF" + b"payload" * 100)
        n_aliases = 12
        for i in range(1, n_aliases):
            (product / "lib" / f"lib{i}.so").hardlink_to(first)
        archive = tmp_path / "baseline.tar.zst"
        manifest = pack_product_baseline(product, archive)
        # + the fixture's own liba.so.1.2.3 and libb.so, each a distinct entry
        # (liba.so itself, a symlink to a library-shaped target, gets none).
        assert len(manifest.libraries) == n_aliases + 2

        hash_calls = []
        real_sha256 = hashlib.sha256

        def counting_sha256(*args: object, **kwargs: object) -> object:
            hash_calls.append(1)
            return real_sha256(*args, **kwargs)

        import abicheck.product_baseline as pb

        original = pb.hashlib.sha256
        pb.hashlib.sha256 = counting_sha256
        try:
            unpack_product_baseline(archive, tmp_path / "dest")
        finally:
            pb.hashlib.sha256 = original
        # One hash per unique (dev, ino) identity: the shared hardlinked
        # payload once, plus liba.so.1.2.3 and libb.so each once.
        assert len(hash_calls) == 3

    def test_unpack_rejects_a_falsely_declared_non_library(
        self, tmp_path: Path
    ) -> None:
        # The inventory checks only catch an *undeclared* library -- a declared
        # ordinary file (README.txt) with a correct size/digest was accepted.
        product = _make_product(tmp_path)
        (product / "README.txt").write_bytes(b"hello world, not a library")
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        import hashlib

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        tar_bytes = dctx.decompress(archive.read_bytes(), max_output_size=1 << 30)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
            manifest_fh = tf.extractfile(MANIFEST_MEMBER_NAME)
            assert manifest_fh is not None
            manifest_raw = json.loads(manifest_fh.read())
            readme_fh = tf.extractfile("README.txt")
            assert readme_fh is not None
            readme_bytes = readme_fh.read()
        # Appended, not replaced: the product's own real libraries stay
        # declared, so only the added false entry is under test here.
        manifest_raw["libraries"].append(
            {
                "path": "README.txt",
                "size": len(readme_bytes),
                "sha256": hashlib.sha256(readme_bytes).hexdigest(),
            }
        )
        payload = json.dumps(manifest_raw).encode("utf-8") + b"\n"
        tampered = _rewrite_manifest_member(tmp_path, archive, payload)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="not library-shaped"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_translates_a_symlink_loop_into_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        # A self-referential symlink made Path.resolve() raise RuntimeError
        # from _resolve_under_root() -- unpack re-raised it raw (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        payload = (
            json.dumps(
                {
                    "schema": PRODUCT_BASELINE_SCHEMA,
                    "product": "",
                    "libraries": [
                        {
                            "name": "loop.so",
                            "path": "loop",
                            "sha256": "ab" * 32,
                            "size": 1,
                        }
                    ],
                    "header_roots": [],
                    "file_count": 1,
                }
            ).encode("utf-8")
            + b"\n"
        )
        looped = _rewrite_manifest_member(tmp_path, archive, payload)
        looped = _add_symlink_loop_member(tmp_path, looped, "loop")

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="invalid library path"):
            unpack_product_baseline(looped, dest)
        assert not dest.exists()

    def test_unpack_translates_an_embedded_nul_path_into_snapshot_error(
        self, tmp_path: Path
    ) -> None:
        # _resolve_under_root() only caught RuntimeError -- an embedded NUL
        # byte makes Path.resolve() raise ValueError instead (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        tar_bytes = dctx.decompress(archive.read_bytes(), max_output_size=1 << 30)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r") as tf:
            manifest_fh = tf.extractfile(MANIFEST_MEMBER_NAME)
            assert manifest_fh is not None
            manifest_raw = json.loads(manifest_fh.read())
        manifest_raw["header_roots"] = ["bad\x00path"]
        payload = json.dumps(manifest_raw).encode("utf-8") + b"\n"
        tampered = _rewrite_manifest_member(tmp_path, archive, payload)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="invalid header root"):
            unpack_product_baseline(tampered, dest)
        assert not dest.exists()

    def test_unpack_leaves_no_staging_directory_behind_on_failure(
        self, tmp_path: Path
    ) -> None:
        bogus = self._plain_tar_zst(tmp_path)
        with pytest.raises(SnapshotError):
            unpack_product_baseline(bogus, tmp_path / "dest")
        assert not list(tmp_path.glob(".abicheck-product-baseline-unpack-*"))

    def test_unpack_cleans_up_staging_when_publication_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failure during publication ran outside the extraction cleanup try
        # -- staging survived and the raw OSError escaped unwrapped (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        def _boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated publish failure")

        monkeypatch.setattr("abicheck.product_baseline.os.replace", _boom)

        dest = tmp_path / "dest"
        with pytest.raises(SnapshotError, match="failed to publish"):
            unpack_product_baseline(archive, dest)
        assert not dest.exists()
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
        # A corrupt/truncated .tar.zst must raise SnapshotError, not an
        # unhandled zstandard.ZstdError escaping extract() (Codex).
        bogus = tmp_path / "corrupt.tar.zst"
        bogus.write_bytes(b"\x28\xb5\x2f\xfd" + b"not really zstd content")
        with pytest.raises(SnapshotError, match="failed to extract"):
            unpack_product_baseline(bogus, tmp_path / "dest")

    def test_unpack_rejects_manifest_missing_schema_discriminator(
        self, tmp_path: Path
    ) -> None:
        # from_dict() defensively *defaults* a missing "schema" key -- applied
        # to the discriminator itself it would pass "{}" as recognized (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        bad_archive = _rewrite_manifest_member(tmp_path, archive, b"{}\n")
        with pytest.raises(SnapshotError, match="missing its schema discriminator"):
            unpack_product_baseline(bad_archive, tmp_path / "dest")

    def test_unpack_rejects_header_root_escaping_the_product_root(
        self, tmp_path: Path
    ) -> None:
        # header_roots is handed to a header-parsing tool, so an escaping root
        # must be rejected -- from_dict()'s str() coercion accepts it unchanged.
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        payload = (
            json.dumps(
                {
                    "schema": PRODUCT_BASELINE_SCHEMA,
                    "product": "",
                    "libraries": [],
                    "header_roots": ["../../etc"],
                    "file_count": 1,
                }
            ).encode("utf-8")
            + b"\n"
        )
        bad_archive = _rewrite_manifest_member(tmp_path, archive, payload)
        with pytest.raises(SnapshotError, match="invalid header root"):
            unpack_product_baseline(bad_archive, tmp_path / "dest")

    def test_unpack_rejects_library_path_escaping_the_product_root(
        self, tmp_path: Path
    ) -> None:
        # LibraryEntry.path carries the same untrusted-manifest risk as
        # header_roots -- an escaping path must be rejected the same way.
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        payload = (
            json.dumps(
                {
                    "schema": PRODUCT_BASELINE_SCHEMA,
                    "product": "",
                    "libraries": [
                        {
                            "name": "outside.so",
                            "path": "../../outside.so",
                            "sha256": "ab" * 32,
                            "size": 1,
                        }
                    ],
                    "header_roots": [],
                    "file_count": 1,
                }
            ).encode("utf-8")
            + b"\n"
        )
        bad_archive = _rewrite_manifest_member(tmp_path, archive, payload)
        with pytest.raises(SnapshotError, match="invalid library path"):
            unpack_product_baseline(bad_archive, tmp_path / "dest")

    def test_unpack_rejects_library_path_that_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        payload = (
            json.dumps(
                {
                    "schema": PRODUCT_BASELINE_SCHEMA,
                    "product": "",
                    "libraries": [
                        {
                            "name": "ghost.so",
                            "path": "lib/ghost.so",
                            "sha256": "ab" * 32,
                            "size": 1,
                        }
                    ],
                    "header_roots": [],
                    "file_count": 1,
                }
            ).encode("utf-8")
            + b"\n"
        )
        bad_archive = _rewrite_manifest_member(tmp_path, archive, payload)
        with pytest.raises(SnapshotError, match="invalid library path"):
            unpack_product_baseline(bad_archive, tmp_path / "dest")

    def test_unpack_rejects_header_root_that_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        # A header root resolving *under* root but naming nothing real never
        # round-tripped through a real pack() call -- hand-edited or corrupt.
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        payload = (
            json.dumps(
                {
                    "schema": PRODUCT_BASELINE_SCHEMA,
                    "product": "",
                    "libraries": [],
                    "header_roots": ["nonexistent-headers"],
                    "file_count": 1,
                }
            ).encode("utf-8")
            + b"\n"
        )
        bad_archive = _rewrite_manifest_member(tmp_path, archive, payload)
        with pytest.raises(SnapshotError, match="invalid header root"):
            unpack_product_baseline(bad_archive, tmp_path / "dest")

    def test_unpack_rejects_newer_major_schema(self, tmp_path: Path) -> None:
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        # Monkey-patch the schema forward-compat check directly, since a
        # real "future" archive would require writing this format twice.
        from abicheck import product_baseline as pb

        with pytest.raises(SnapshotError, match="newer than this abicheck build"):
            pb._check_schema_supported("abicheck.product-baseline/v999", archive)

    def test_unpack_rejects_nonexistent_older_major_schema(
        self, tmp_path: Path
    ) -> None:
        # Only major >= 1 was ever real; "newer than supported" alone let v0
        # (or negative) through, deserialized with v1's layout (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        from abicheck import product_baseline as pb

        with pytest.raises(SnapshotError, match="unrecognized product baseline schema"):
            pb._check_schema_supported("abicheck.product-baseline/v0", archive)
        with pytest.raises(SnapshotError, match="unrecognized product baseline schema"):
            pb._check_schema_supported("abicheck.product-baseline/v-1", archive)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics only")
    def test_unpack_preserves_existing_destination_permissions(
        self, tmp_path: Path
    ) -> None:
        # A pre-created DEST_DIR with non-default permissions: staging's mode
        # was previously re-derived from the umask, silently changing it (Codex).
        product = _make_product(tmp_path)
        archive = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, archive)

        dest = tmp_path / "dest"
        dest.mkdir(mode=0o700)
        os.chmod(dest, 0o700)  # mkdir's mode is umask-filtered; force it.
        unpack_product_baseline(archive, dest)
        assert stat_mod.S_IMODE(dest.stat().st_mode) == 0o700


class TestPackProductBaselinePermissions:
    def test_pack_output_gets_umask_derived_permissions_not_mkstemp_0600(
        self, tmp_path: Path
    ) -> None:
        # mkstemp() always creates mode 0600, and os.replace() carries
        # that across to OUTPUT -- a freshly packed archive was
        # unreadable by anyone but its own creator (Codex).
        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, out)
        mode = stat_mod.S_IMODE(out.stat().st_mode)
        umask = os.umask(0)
        os.umask(umask)
        assert mode == (0o666 & ~umask)
        assert mode != 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics only")
    def test_pack_preserves_existing_output_permissions_when_overwriting(
        self, tmp_path: Path
    ) -> None:
        # Repacking an existing group/world-readable release asset must
        # not silently strip its access just because mkstemp()'s own
        # 0600 default rides along on os.replace() (Codex).
        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"
        pack_product_baseline(product, out)
        os.chmod(out, 0o644)
        pack_product_baseline(product, out)
        assert stat_mod.S_IMODE(out.stat().st_mode) == 0o644

    def test_process_umask_is_queried_at_most_once_per_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # os.umask() only works by briefly mutating the process-wide
        # value -- a real race window (Codex). Caching pays it once.
        from abicheck import product_baseline as pb

        monkeypatch.setattr(pb, "_cached_umask", None)
        real_umask = os.umask
        calls: list[int] = []

        def counting_umask(mask: int) -> int:
            calls.append(mask)
            return real_umask(mask)

        monkeypatch.setattr(pb.os, "umask", counting_umask)

        first = pb._process_umask()
        second = pb._process_umask()

        assert first == second
        assert len(calls) == 2  # one os.umask(0) + one restore -- once total


class TestCompareProductDirectoriesHeaderRoots:
    def _capture_run_compare_calls(
        self, monkeypatch: pytest.MonkeyPatch, old_dir: Path, new_dir: Path
    ) -> list[dict[str, object]]:
        # Bypasses real library discovery/parsing -- this test is purely
        # about which header dirs resolve and reach run_compare, and
        # building real ELF objects needs a compiler this sandbox lacks.
        from abicheck import product_baseline as pb, service_compare_pipeline as scp

        calls: list[dict[str, object]] = []

        def _fake_discover_library_map(
            root: Path, *, include_private: bool
        ) -> dict[str, Path]:
            return {"libfoo.so": root / "lib" / "libfoo.so"}

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)
            raise SnapshotError("stop before a real compare runs")

        monkeypatch.setattr(pb, "_discover_library_map", _fake_discover_library_map)
        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        return calls

    def test_accepts_distinct_header_roots_per_side(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A product relocating its public headers can't be expressed by
        # one shared header_roots list -- the old code dropped the
        # nonexistent root, running that side with no evidence (Codex).
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "include").mkdir(parents=True)
        (old_dir / "include" / "a.h").write_text("struct A { int x; };\n")
        (new_dir / "sdk" / "include").mkdir(parents=True)
        (new_dir / "sdk" / "include" / "a.h").write_text("struct A { int x; };\n")

        calls = self._capture_run_compare_calls(monkeypatch, old_dir, new_dir)
        with pytest.raises(SnapshotError, match="stop before"):
            compare_product_directories(
                old_dir,
                new_dir,
                old_header_roots=["include"],
                new_header_roots=["sdk/include"],
            )

        assert len(calls) == 1
        assert calls[0]["old_headers"] == [old_dir / "include"]
        assert calls[0]["new_headers"] == [new_dir / "sdk" / "include"]

    def test_per_side_roots_default_to_the_shared_header_roots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "include").mkdir(parents=True)
        (new_dir / "include").mkdir(parents=True)

        calls = self._capture_run_compare_calls(monkeypatch, old_dir, new_dir)
        with pytest.raises(SnapshotError, match="stop before"):
            compare_product_directories(old_dir, new_dir, header_roots=["include"])

        assert len(calls) == 1
        assert calls[0]["old_headers"] == [old_dir / "include"]
        assert calls[0]["new_headers"] == [new_dir / "include"]


class TestCompareProductDirectoriesPerLibraryHeaderRoots:
    """HeaderRootsSpec's per-library mapping shape -- see
    docs/contribute/plans/product-baseline-per-library-header-roots.md."""

    def _capture_run_compare_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> list[dict[str, object]]:
        # Unlike the sibling helper above, this one does NOT raise on the
        # first call -- multiple libraries must all reach run_compare().
        from abicheck import (
            bundle as bundle_mod,
            product_baseline as pb,
            service_compare_pipeline as scp,
        )

        calls: list[dict[str, object]] = []

        def _fake_discover_library_map(
            root: Path, *, include_private: bool
        ) -> dict[str, Path]:
            return {
                "lib/liba.so": root / "lib" / "liba.so",
                "lib/libb.so": root / "lib" / "libb.so",
            }

        class _FakeDiffResult:
            def __init__(self, library: str) -> None:
                self.library = library

        class _FakeResult:
            def __init__(self, library: str) -> None:
                self.diff = _FakeDiffResult(library)

        def _fake_run_compare(old_lib, new_lib, **kwargs):  # type: ignore[no-untyped-def]
            calls.append({"old": old_lib, "new": new_lib, **kwargs})
            return _FakeResult(str(new_lib))

        def _fake_build_bundle_snapshot(libraries):  # type: ignore[no-untyped-def]
            return {"libraries": dict(libraries)}

        class _FakeBundleDiffResult(dict):  # type: ignore[type-arg]
            """Behaves like a dict for existing kwargs-based assertions,
            while also carrying a real, mutable ``bundle_findings`` list --
            compare_product_directories() appends standalone-removal
            findings to the real BundleDiffResult it gets back."""

            def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
                super().__init__(**kwargs)
                self.bundle_findings: list[object] = []

        def _fake_compare_bundle(old, new, per_library_results, **kwargs):  # type: ignore[no-untyped-def]
            return _FakeBundleDiffResult(**kwargs)

        monkeypatch.setattr(pb, "_discover_library_map", _fake_discover_library_map)
        monkeypatch.setattr(scp, "run_compare", _fake_run_compare)
        monkeypatch.setattr(
            bundle_mod, "build_bundle_snapshot", _fake_build_bundle_snapshot
        )
        monkeypatch.setattr(bundle_mod, "compare_bundle", _fake_compare_bundle)
        return calls

    def test_scopes_roots_to_one_library_and_not_its_sibling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "include" / "liba").mkdir(parents=True)
        (new_dir / "include" / "liba").mkdir(parents=True)
        # libb.so's own root deliberately doesn't exist on either side --
        # it must get zero headers, not liba's.

        calls = self._capture_run_compare_calls(monkeypatch)
        compare_product_directories(
            old_dir,
            new_dir,
            header_roots={"lib/liba.so": ["include/liba"]},
        )

        by_new = {c["new"]: c for c in calls}
        liba_call = by_new[new_dir / "lib" / "liba.so"]
        libb_call = by_new[new_dir / "lib" / "libb.so"]
        assert liba_call["old_headers"] == [old_dir / "include" / "liba"]
        assert liba_call["new_headers"] == [new_dir / "include" / "liba"]
        assert libb_call["old_headers"] == []
        assert libb_call["new_headers"] == []

    def test_a_library_absent_from_the_mapping_gets_no_headers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Not a fallback to the flat/shared list, and not an error --
        # matches this format's existing "no headers is a normal case"
        # tolerance elsewhere.
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "include").mkdir(parents=True)
        (new_dir / "include").mkdir(parents=True)

        calls = self._capture_run_compare_calls(monkeypatch)
        compare_product_directories(
            old_dir,
            new_dir,
            header_roots={"lib/some-other-library.so": ["include"]},
        )

        assert len(calls) == 2
        for call in calls:
            assert call["old_headers"] == []
            assert call["new_headers"] == []

    def test_old_and_new_header_roots_accept_per_library_mappings_independently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "old-include").mkdir(parents=True)
        (new_dir / "new-include").mkdir(parents=True)

        calls = self._capture_run_compare_calls(monkeypatch)
        compare_product_directories(
            old_dir,
            new_dir,
            old_header_roots={"lib/liba.so": ["old-include"]},
            new_header_roots={"lib/liba.so": ["new-include"]},
        )

        by_new = {c["new"]: c for c in calls}
        liba_call = by_new[new_dir / "lib" / "liba.so"]
        assert liba_call["old_headers"] == [old_dir / "old-include"]
        assert liba_call["new_headers"] == [new_dir / "new-include"]

    def test_flat_list_still_applies_to_every_library(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression guard: the pre-existing flat-Sequence[str] shape must
        # keep applying identically to every discovered library.
        from abicheck.product_baseline import compare_product_directories

        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        (old_dir / "include").mkdir(parents=True)
        (new_dir / "include").mkdir(parents=True)

        calls = self._capture_run_compare_calls(monkeypatch)
        compare_product_directories(old_dir, new_dir, header_roots=["include"])

        assert len(calls) == 2
        for call in calls:
            assert call["old_headers"] == [old_dir / "include"]
            assert call["new_headers"] == [new_dir / "include"]


class TestRootsForLibrary:
    def test_mapping_returns_the_matching_entry(self) -> None:
        from abicheck.product_baseline import _roots_for_library

        spec = {"liba.so": ["include/liba"], "libb.so": ["include/libb"]}
        assert _roots_for_library(spec, "liba.so") == ["include/liba"]

    def test_mapping_returns_empty_for_an_unlisted_library(self) -> None:
        from abicheck.product_baseline import _roots_for_library

        spec = {"liba.so": ["include/liba"]}
        assert _roots_for_library(spec, "libb.so") == ()

    def test_flat_sequence_applies_regardless_of_library_key(self) -> None:
        from abicheck.product_baseline import _roots_for_library

        spec = ["include"]
        assert _roots_for_library(spec, "liba.so") == ["include"]
        assert _roots_for_library(spec, "libb.so") == ["include"]

    def test_empty_flat_sequence_applies_to_every_library(self) -> None:
        from abicheck.product_baseline import _roots_for_library

        assert _roots_for_library((), "liba.so") == ()


class TestDiscoverLibraryMap:
    def test_keys_by_relative_path_not_bare_filename(self, tmp_path: Path) -> None:
        # Two distinct DSOs sharing a basename in different directories
        # previously collided on the same bare-filename dict key,
        # dropping one of them (Codex).
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "plugins" / "a").mkdir(parents=True)
        (root / "plugins" / "b").mkdir(parents=True)
        (root / "plugins" / "a" / "plugin.so").write_bytes(b"PLUGIN-A")
        (root / "plugins" / "b" / "plugin.so").write_bytes(b"PLUGIN-B")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"plugins/a/plugin.so", "plugins/b/plugin.so"}
        assert result["plugins/a/plugin.so"].read_bytes() == b"PLUGIN-A"
        assert result["plugins/b/plugin.so"].read_bytes() == b"PLUGIN-B"

    def test_discovers_dll_and_dylib_not_just_elf_so(self, tmp_path: Path) -> None:
        # _discover_library_map delegated to discover_shared_libraries(),
        # which only recognizes ELF -- a .dll/.dylib was invisible (Codex).
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "foo.dll").write_bytes(b"MZ-not-a-real-pe")
        (root / "bin" / "libfoo.dylib").write_bytes(b"not-a-real-macho")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"bin/foo.dll", "bin/libfoo.dylib"}

    def test_excludes_a_linker_script_alongside_its_real_target(
        self, tmp_path: Path
    ) -> None:
        # A GNU ld INPUT()/GROUP() script (`libfoo.so -> INPUT(libfoo.so.1)`)
        # is library-suffix-named but carries no binary content --
        # discovering it alongside its real target previously produced
        # two DiffResults for one library (Codex).
        from abicheck.product_baseline import _discover_library_map
        from tests.test_package import _make_minimal_elf_so

        root = tmp_path / "product"
        root.mkdir()
        _make_minimal_elf_so(root / "libfoo.so.1")
        (root / "libfoo.so").write_text("INPUT(libfoo.so.1)\n")

        result = _discover_library_map(root, include_private=True)
        assert set(result) == {"libfoo.so.1"}

    def test_excludes_an_unresolvable_linker_script(self, tmp_path: Path) -> None:
        # A script whose target isn't available in this tree at all (e.g.
        # an archive-only build) must not abort the whole comparison as
        # "cannot detect format" either -- excluded the same way, not
        # merely deduplicated against a target that happens to exist.
        from abicheck.product_baseline import _discover_library_map

        root = tmp_path / "product"
        root.mkdir()
        (root / "libfoo.so").write_text("INPUT(libfoo.so.1)\n")

        result = _discover_library_map(root, include_private=True)
        assert result == {}
