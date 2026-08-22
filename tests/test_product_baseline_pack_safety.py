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

"""Pack-time failure-path safety tests for :mod:`abicheck.product_baseline`
(split out of ``test_product_baseline.py`` to keep that file under the
AI-readiness 2000-line hard cap): fd hygiene on a mid-pack failure, and
pack_product_baseline() refusing to publish an archive that
unpack_product_baseline() would always reject."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.product_baseline import pack_product_baseline, unpack_product_baseline


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


class TestPackProductBaselineFdHygiene:
    def test_pack_closes_temp_fd_when_chmod_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # os.chmod() failing after mkstemp() succeeds must not leak the
        # descriptor -- it hasn't been handed to os.fdopen() yet at that
        # point, so ownership is still ours to close (Codex).
        from abicheck import product_baseline as pb

        product = _make_product(tmp_path)
        out = tmp_path / "baseline.tar.zst"

        # Track the mkstemp() fd specifically and check its *liveness* via
        # os.fstat() rather than counting os.close() calls -- fd numbers get
        # reused (e.g. by _umask_derived_mode()'s own short-lived probe fd,
        # called earlier in pack_product_baseline()), so "was os.close()
        # called with this number at some point" is not reliable evidence
        # that *this* fd was closed (Codex, verified: an earlier version of
        # this test spuriously passed pre-fix because of exactly that reuse).
        real_mkstemp = pb.tempfile.mkstemp
        captured: dict[str, int] = {}

        def _recording_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
            fd, name = real_mkstemp(*args, **kwargs)  # type: ignore[arg-type]
            captured["fd"] = fd
            return fd, name

        monkeypatch.setattr(pb.tempfile, "mkstemp", _recording_mkstemp)

        def _failing_chmod(path: object, mode: int) -> None:
            raise OSError("simulated chmod failure")

        monkeypatch.setattr(pb.os, "chmod", _failing_chmod)

        with pytest.raises(OSError, match="simulated chmod failure"):
            pack_product_baseline(product, out)

        assert "fd" in captured, "mkstemp() was never called"
        try:
            os.fstat(captured["fd"])
        except OSError:
            pass  # closed, as expected
        else:
            os.close(captured["fd"])  # clean up before failing, not after
            pytest.fail("mkstemp()'s own fd is still open after chmod failed")
        assert not out.exists()
        # No leftover .abicheck-product-baseline-*.tmp file either.
        assert not list(tmp_path.glob(".abicheck-product-baseline-*"))


class TestPackProductBaselineReaderLimits:
    """pack_product_baseline() must refuse to publish an archive that
    unpack_product_baseline() (via TarExtractor's decompression-bomb
    defenses) would always reject -- otherwise a successful pack can
    produce something that can never be unpacked (Codex)."""

    def test_rejects_when_member_count_exceeds_the_readers_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_ABICHECK_TAR_ZST_MAX_MEMBERS", "3")
        # _make_product(): 5 real paths (liba.so.1.2.3, liba.so symlink,
        # libb.so, include/api/a.h, README.txt) + 1 manifest = 6 members.
        product = _make_product(tmp_path)
        out = tmp_path / "b.tar.zst"
        with pytest.raises(SnapshotError, match="member safety limit"):
            pack_product_baseline(product, out)
        assert not out.exists()

    def test_accepts_member_count_within_the_readers_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _make_product() packs 4 real files (liba.so.1.2.3, liba.so symlink,
        # libb.so, include/api/a.h) + README.txt = 5, plus the manifest = 6.
        monkeypatch.setenv("_ABICHECK_TAR_ZST_MAX_MEMBERS", "6")
        product = _make_product(tmp_path)
        out = tmp_path / "b.tar.zst"
        pack_product_baseline(product, out)
        assert out.exists()

    def test_rejects_when_content_bytes_exceed_the_readers_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_ABICHECK_TAR_ZST_MAX_DECODED_BYTES", "10")
        product = _make_product(tmp_path)  # libb.so alone is 1500 bytes
        out = tmp_path / "b.tar.zst"
        with pytest.raises(SnapshotError, match="decompressed tar stream"):
            pack_product_baseline(product, out)
        assert not out.exists()

    def test_accepts_content_bytes_within_the_readers_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_ABICHECK_TAR_ZST_MAX_DECODED_BYTES", "1000000")
        product = _make_product(tmp_path)
        out = tmp_path / "b.tar.zst"
        pack_product_baseline(product, out)
        assert out.exists()

    def test_rejects_on_tar_framing_overhead_alone_even_when_declared_content_is_tiny(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The actual bug: many tiny files have negligible declared content
        # but each still costs a 512-byte tar header plus padding -- a
        # pre-write estimate over declared content alone would pass a limit
        # the real, framing-inclusive decompressed stream exceeds (Codex).
        product = tmp_path / "product"
        (product / "lib").mkdir(parents=True)
        for i in range(200):
            (product / "lib" / f"f{i}.so").write_bytes(b"x")  # 1 byte each
        # 200 files * 1 byte = 200 bytes of declared content -- comfortably
        # under this limit -- but 200 * (512-byte header + 512-byte padded
        # data) + manifest + end-of-archive marker is well over it.
        monkeypatch.setenv("_ABICHECK_TAR_ZST_MAX_DECODED_BYTES", "5000")
        out = tmp_path / "b.tar.zst"
        with pytest.raises(SnapshotError, match="decompressed tar stream"):
            pack_product_baseline(product, out)
        assert not out.exists()

    def test_pack_and_unpack_agree_at_a_tight_byte_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Round-trip proof that pack's exact in-stream byte count really
        # matches what unpack enforces: pack succeeds at a limit set from
        # the real archive's own decompressed size, and a real unpack under
        # the identical limit succeeds too -- neither side's check is an
        # estimate that could disagree with the other's.
        product = _make_product(tmp_path)
        out = tmp_path / "b.tar.zst"
        pack_product_baseline(product, out)  # default (generous) limits

        import zstandard

        dctx = zstandard.ZstdDecompressor()
        real_stream_len = len(
            dctx.decompress(out.read_bytes(), max_output_size=1 << 30)
        )

        # Re-pack under a limit set to the real archive's own exact size --
        # deterministic packing (same inputs) means this must succeed.
        monkeypatch.setenv("_ABICHECK_TAR_ZST_MAX_DECODED_BYTES", str(real_stream_len))
        out2 = tmp_path / "b2.tar.zst"
        pack_product_baseline(product, out2)
        assert out2.exists()

        dest = tmp_path / "unpacked"
        unpack_product_baseline(out2, dest)  # must not raise under the same limit

    def test_rejects_when_manifest_bytes_exceed_the_readers_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Deliberately below member-count/content-byte thresholds -- only
        # the serialized manifest payload itself is over budget here.
        monkeypatch.setenv("_ABICHECK_PRODUCT_BASELINE_MAX_MANIFEST_BYTES", "10")
        product = _make_product(tmp_path)
        out = tmp_path / "b.tar.zst"
        with pytest.raises(SnapshotError, match="manifest.*safety limit"):
            pack_product_baseline(product, out)
        assert not out.exists()

    def test_accepts_manifest_bytes_within_the_readers_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_ABICHECK_PRODUCT_BASELINE_MAX_MANIFEST_BYTES", "1000000")
        product = _make_product(tmp_path)
        out = tmp_path / "b.tar.zst"
        pack_product_baseline(product, out)
        assert out.exists()
