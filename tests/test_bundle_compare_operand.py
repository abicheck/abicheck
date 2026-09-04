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

"""Unit tests for :mod:`abicheck.workflows.bundle_compare_operand` (CLI
cleanup phase two, PR I): the classifier that replaced the removed
``compare --old-bundle-facts`` flag.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.serialization import save_bundle_facts
from abicheck.workflows.bundle_compare_operand import (
    BundleCompareRequest,
    classify_bundle_compare_operands,
    looks_like_stored_bundle_facts,
)

_MARKER_JSON = json.dumps(
    {
        "artifact_type": "abicheck.bundle-facts",
        "schema_version": 2,
        "per_library_snapshots": {},
    }
)


class TestLooksLikeStoredBundleFacts:
    def test_plain_json_with_marker_is_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "old.bundlefacts.json"
        p.write_text(_MARKER_JSON)
        assert looks_like_stored_bundle_facts(p) is True

    def test_pretty_printed_json_with_marker_is_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "old.bundlefacts.json"
        p.write_text(json.dumps(json.loads(_MARKER_JSON), indent=2))
        assert looks_like_stored_bundle_facts(p) is True

    def test_reordered_root_keys_still_classify_as_stored(self, tmp_path: Path) -> None:
        """Codex review, PR #1042 (round 3): bundle_facts_to_dict always
        writes artifact_type first, but a document re-serialized by
        another conforming tool (a pretty-printer, a key-sorting
        formatter) can freely reorder root members -- bundle_facts_from_
        dict itself never requires a particular order, so classification
        must not either."""
        p = tmp_path / "reordered.json"
        # schema_version and per_library_snapshots both precede
        # artifact_type here, unlike the writer's own real output.
        p.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "per_library_snapshots": {},
                    "artifact_type": "abicheck.bundle-facts",
                    "variant_fingerprint": "default",
                }
            )
        )
        assert looks_like_stored_bundle_facts(p) is True

    def test_reordered_marker_after_large_content_still_classifies_as_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 4): the order-independent scan
        (test_reordered_root_keys_still_classify_as_stored above) only
        helps if the marker actually falls inside the decoded window --
        a document with a sizeable per_library_snapshots member placed
        *before* artifact_type could push the marker past a small fixed
        prefix. Build a document whose per_library_snapshots content
        alone is well beyond the plain-marker-scan default (4 KiB) but
        still comfortably inside the enlarged scan window
        (_MARKER_SCAN_BYTES) this fix adds, with artifact_type as the
        last root key."""
        padding_library = {
            f"function_{i}": {"symbol": f"_Z{i}foo", "return_type": "int"}
            for i in range(400)
        }
        p = tmp_path / "reordered_large.json"
        p.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "per_library_snapshots": {
                        f"lib{i}.so": padding_library for i in range(15)
                    },
                    "variant_fingerprint": "default",
                    "artifact_type": "abicheck.bundle-facts",
                }
            )
        )
        assert p.stat().st_size > 8192
        assert looks_like_stored_bundle_facts(p) is True

    def test_marker_first_large_document_exceeding_scan_cap_is_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 11, P1): a real --bundle-facts-out
        document -- the writer always emits the marker first, nothing to
        do with a duplicate key -- can exceed _MARKER_SCAN_BYTES before
        its own root object closes, simply from having enough ordinary
        snapshot facts. The round-10 duplicate-key fix must not silently
        discard an already-found marker just because neither probe window
        reaches the closing brace."""
        padding_library = {
            f"function_{i}": {"symbol": f"_Z{i}foo", "return_type": "int"}
            for i in range(400)
        }
        p = tmp_path / "large_marker_first.json"
        p.write_text(
            json.dumps(
                {
                    "artifact_type": "abicheck.bundle-facts",
                    "schema_version": 2,
                    "per_library_snapshots": {
                        f"lib{i}.so": padding_library for i in range(80)
                    },
                }
            )
        )
        # Premise check: the document's own root object doesn't close
        # within either probe window, exactly the scenario the finding
        # describes.
        assert p.stat().st_size > 1024 * 1024
        assert looks_like_stored_bundle_facts(p) is True

    def test_tar_member_deliberately_named_as_the_marker_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 6): a tar stream's very first
        bytes are its first member's own name field -- unlike every other
        archive format this module rules out, tar has no leading magic, so
        a member deliberately named a complete, valid, self-closing JSON
        marker object satisfies every token-level check on its own. Only a
        whole-file structural check (is this really a tar stream?) can
        catch it."""
        tar_path = tmp_path / "release.tar"
        with tarfile.open(tar_path, "w") as tf:
            info = tarfile.TarInfo(name=_MARKER_JSON)
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))
        assert looks_like_stored_bundle_facts(tar_path) is False

    def test_gzipped_tar_member_named_as_the_marker_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Same attack as above, through the gzip-compressed shape a real
        --bundle-facts-out JSON document could also take (bounded_decoded_
        prefix decodes gzip transparently either way)."""
        tar_path = tmp_path / "release.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            info = tarfile.TarInfo(name=_MARKER_JSON)
            info.size = 0
            tf.addfile(info, io.BytesIO(b""))
        assert looks_like_stored_bundle_facts(tar_path) is False

    def test_garbage_between_reordered_root_tokens_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """A non-whitespace byte between two recognized root-level tokens
        means this isn't a real JSON document at all (a genuine encoding,
        minified or pretty-printed, never separates tokens with anything
        but whitespace) -- must not be silently skipped the way an
        unconstrained token scan otherwise would."""
        p = tmp_path / "garbage_gap.json"
        p.write_bytes(
            b'{"schema_version":2,\x00\x01\x02"artifact_type":"abicheck.bundle-facts"}'
        )
        assert looks_like_stored_bundle_facts(p) is False

    def test_wheel_with_a_forged_json_preamble_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 7): zip permits arbitrary bytes
        before its first local file header (self-extracting archives rely
        on this), and a real zip reader locates entries via the central
        directory at the *end* of the file, not the magic at byte 0. A
        real .whl prepended with a crafted marker preamble is still a
        perfectly valid wheel to zipfile/WheelExtractor, fails the G40
        byte-0-only magic check, and must not fall through to the marker
        scan on that preamble."""
        whl_path = tmp_path / "fake_package-1.0-py3-none-any.whl"
        with open(whl_path, "wb") as fh:
            fh.write(_MARKER_JSON.encode())
        with zipfile.ZipFile(whl_path, "a") as zf:
            zf.writestr("fake_package/__init__.py", "")
            zf.writestr("fake_package-1.0.dist-info/METADATA", "Name: fake_package\n")
        # Sanity-check the fixture is itself still a real, readable wheel,
        # exactly as the finding describes.
        assert zipfile.is_zipfile(whl_path)
        with zipfile.ZipFile(whl_path) as zf:
            assert "fake_package/__init__.py" in zf.namelist()
        assert looks_like_stored_bundle_facts(whl_path) is False

    def test_real_wheel_with_a_gzip_marker_preamble_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 9): a real wheel (real members, a
        genuine central directory) can legitimately carry a complete,
        independently-decodable gzip stream as its own permitted zip
        preamble (the same "arbitrary bytes before the first local
        header" allowance round 7 exploited with plain JSON). An earlier
        round's fix ("skip the zip check whenever the file already looks
        gzip-compressed") would misidentify this exact file as "not
        zip-shaped" and let the marker scan run on the decoded preamble --
        the fix must check for real zip *members*, not merely whether the
        raw file starts with a compression magic."""
        payload = json.dumps(
            {
                "artifact_type": "abicheck.bundle-facts",
                "schema_version": 2,
                "per_library_snapshots": {},
                # Padding past the small-probe window so the marker is
                # found without ever reading past the gzip member's own
                # end into the zip bytes that follow it.
                "padding": "x" * 5000,
            }
        ).encode()
        buf = io.BytesIO()
        buf.write(gzip.compress(payload))
        with zipfile.ZipFile(buf, "a") as zf:
            zf.writestr("fake_package/__init__.py", "")
            zf.writestr("fake_package-1.0.dist-info/METADATA", "Name: fake_package\n")
        raw = buf.getvalue()
        whl_path = tmp_path / "fake_package-1.0-py3-none-any.whl"
        whl_path.write_bytes(raw)
        # Premise check: the fixture really is both a valid zip with real
        # members and starts with gzip magic, exactly as the finding
        # describes.
        assert zipfile.is_zipfile(whl_path)
        with zipfile.ZipFile(whl_path) as zf:
            assert "fake_package/__init__.py" in zf.namelist()
        assert raw[:2] == b"\x1f\x8b"
        assert looks_like_stored_bundle_facts(whl_path) is False

    def test_gzip_fextra_forging_a_central_directory_is_still_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 10): a fake central-directory
        record plus EOCD, both fully self-consistent and entirely
        embedded within a gzip FEXTRA sub-field, satisfies zipfile.
        is_zipfile() *and* ZipFile.namelist() (a nonempty entry) with zero
        real zip content anywhere -- ZipFile.__init__ only ever reads the
        central directory, never validating that a real local file header
        backs each entry. The fixture is still a genuine, independently-
        decodable gzip stream carrying the real marker."""
        import struct
        import zlib

        def _gzip_with_forged_central_directory(payload: bytes) -> bytes:
            co = zlib.compressobj(9, zlib.DEFLATED, -15)
            compressed = co.compress(payload) + co.flush()
            trailer = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) + struct.pack(
                "<I", len(payload) & 0xFFFFFFFF
            )
            tail_after_header = compressed + trailer
            fname = b"fake.txt"
            cd_entry = (
                struct.pack(
                    "<IHHHHHHIIIHHHHHII",
                    0x02014B50,
                    20,
                    20,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    len(fname),
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
                + fname
            )
            cd_size = len(cd_entry)
            header_prefix = (
                b"\x1f\x8b\x08"
                + bytes([0x04])  # FLG = FEXTRA
                + struct.pack("<I", 0)  # MTIME
                + b"\x02\xff"  # XFL, OS
            )
            si = b"AB"
            data_start = len(header_prefix) + 2 + len(si) + 2
            eocd = struct.pack(
                "<IHHHHIIH",
                0x06054B50,
                0,
                0,
                1,
                1,
                cd_size,
                data_start,
                len(tail_after_header),
            )
            subfield_data = cd_entry + eocd
            subfield = si + struct.pack("<H", len(subfield_data)) + subfield_data
            return (
                header_prefix
                + struct.pack("<H", len(subfield))
                + subfield
                + tail_after_header
            )

        data = _gzip_with_forged_central_directory(_MARKER_JSON.encode())
        # Premise check: the crafted bytes fool a raw zip probe, including
        # reporting a nonempty namelist -- but the "member" isn't real.
        assert zipfile.is_zipfile(io.BytesIO(data)) is True
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.namelist() == ["fake.txt"]
            with pytest.raises(zipfile.BadZipFile):
                zf.read("fake.txt")
        p = tmp_path / "crafted.json.gz"
        p.write_bytes(data)
        assert looks_like_stored_bundle_facts(p) is True

    def test_duplicate_root_marker_key_uses_the_last_value(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 10): JSON permits a duplicate
        root key, and json.loads() (and therefore load_bundle_facts())
        resolves it to the *last* occurrence, the same way a Python dict
        literal would. The scanner must not report the first occurrence's
        value instead."""
        p = tmp_path / "duplicate_key.json"
        p.write_text(
            '{"artifact_type": "other", "artifact_type": '
            '"abicheck.bundle-facts", "schema_version": 2, '
            '"per_library_snapshots": {}}'
        )
        # Premise check: json.loads() really does keep the last value.
        assert json.loads(p.read_text())["artifact_type"] == "abicheck.bundle-facts"
        assert looks_like_stored_bundle_facts(p) is True

    def test_duplicate_root_marker_key_other_value_last_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """The inverse of the case above: when the *last* occurrence is
        not the real marker value, the document must not classify as
        stored, even though an earlier occurrence was."""
        p = tmp_path / "duplicate_key_other_last.json"
        p.write_text(
            '{"artifact_type": "abicheck.bundle-facts", "artifact_type": '
            '"other", "schema_version": 2, "per_library_snapshots": {}}'
        )
        assert json.loads(p.read_text())["artifact_type"] == "other"
        assert looks_like_stored_bundle_facts(p) is False

    def test_gzip_with_many_tiny_members_and_a_leading_marker_is_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 8): a valid, load_bundle_facts()-
        readable gzip stream with unusually high per-member overhead (many
        tiny concatenated members) can need far more *compressed* input to
        produce _MARKER_SCAN_BYTES of *decoded* output than bounded_decoded_
        prefix's own raw-read escalation cap allows -- even though the
        marker sits in the first few hundred decoded bytes, exactly where
        it normally does. Requesting the large window directly (an earlier
        round's fix) would fail outright here; the two-phase probe (small
        window first) must not."""
        payload = _MARKER_JSON.encode()
        tiny_member = gzip.compress(b"0" * 10)
        buf = io.BytesIO()
        buf.write(gzip.compress(payload))
        raw_so_far = len(buf.getvalue())
        # Enough repeated tiny members that the *raw* stream exceeds the
        # classifier's own 1 MiB decoded-window request by itself, so a
        # direct large-window decode attempt truncates mid-member.
        needed = (1024 * 1024 - raw_so_far) // len(tiny_member) + 2000
        buf.write(tiny_member * needed)
        raw = buf.getvalue()
        assert len(raw) > 1024 * 1024  # premise: exceeds the large window
        p = tmp_path / "many_members.json.gz"
        p.write_bytes(raw)
        # Premise check: a direct large-window decode of this exact stream
        # really does fail outright (proves the fixture reproduces the
        # finding, not just that the classifier happens to already cope).
        with pytest.raises((EOFError, zlib.error, gzip.BadGzipFile)):
            gzip.GzipFile(fileobj=io.BytesIO(raw[: 1024 * 1024])).read(1024 * 1024)
        assert looks_like_stored_bundle_facts(p) is True

    def test_small_probe_candidate_survives_a_large_probe_decode_failure(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 12): the small probe can decode
        fine and find the marker (the document's root doesn't close
        within SMALL_MARKER_SCAN_BYTES, so this alone is inconclusive),
        while the *large* probe's own decode fails outright -- the same
        pathologically-high-overhead encoding round 8 answered, scaled up
        so even the 1 MiB raw-read cap can't reach the compressed
        stream's natural end. The large probe's own "no information"
        answer must not silently override the small probe's real,
        already-found candidate."""
        payload = json.dumps(
            {
                "artifact_type": "abicheck.bundle-facts",
                "schema_version": 2,
                "per_library_snapshots": {},
                # Large enough that the root doesn't close within the
                # small probe's own window, but the marker is still found
                # well within it (right at the front).
                "padding": "x" * 500000,
            }
        ).encode()
        buf = io.BytesIO()
        chunk_size = 10
        for i in range(0, len(payload), chunk_size):
            buf.write(gzip.compress(payload[i : i + chunk_size]))
        raw = buf.getvalue()
        assert len(raw) > 1024 * 1024  # premise: exceeds the large window
        p = tmp_path / "small_probe_survives.json.gz"
        p.write_bytes(raw)
        # Premise checks: the small probe's own target decodes fine and
        # finds the marker, while a large-window decode of the same
        # stream fails outright -- both halves of the finding.
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as g:
            small = g.read(4096)
        assert b'"artifact_type": "abicheck.bundle-facts"' in small
        with pytest.raises((EOFError, zlib.error, gzip.BadGzipFile)):
            gzip.GzipFile(fileobj=io.BytesIO(raw[: 1024 * 1024])).read(1024 * 1024)
        assert looks_like_stored_bundle_facts(p) is True

    def test_marker_after_a_string_truncated_by_the_small_probe_is_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 13): a long string value before
        the marker, truncated by the small probe's own decoded-prefix
        window right in the middle of the string, must not be
        misclassified as a structural violation -- it's a genuinely
        valid document, simply cut short by the bounded read."""
        long_string = "abc123 def456 ghi789 " * 300
        payload = json.dumps(
            {
                "padding_field": long_string,
                "artifact_type": "abicheck.bundle-facts",
                "schema_version": 2,
                "per_library_snapshots": {},
            }
        )
        p = tmp_path / "truncated_string.json"
        p.write_text(payload)
        # Premise check: the small probe's own window really does land
        # inside the long string, not at a token boundary.
        assert len(payload.encode()[:4096]) == 4096
        assert payload.encode()[4095:4096] not in (b" ", b'"')
        assert looks_like_stored_bundle_facts(p) is True

    def test_gzip_fextra_forging_a_zip_eocd_is_still_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 8): zipfile.is_zipfile() reads
        path's raw bytes, which for a genuinely gzip-compressed BundleFacts
        document are the *compressed* envelope, not JSON -- a gzip FEXTRA
        sub-field is arbitrary, decoder-ignored bytes that can coincidentally
        (or, as here, deliberately) land an EOCD-shaped sequence at the
        file's tail, satisfying is_zipfile() despite the file being a real,
        independently-decodable gzip stream with nothing zip-like about its
        actual content. storage/bundle_archive.py documents and defends
        against this exact construction for its own, structurally similar
        tail probe; this classifier's zip check must skip a recognized
        compression envelope the same way."""
        import struct
        import zlib

        def _gzip_with_eocd_in_extra_field(payload: bytes) -> bytes:
            co = zlib.compressobj(9, zlib.DEFLATED, -15)
            compressed = co.compress(payload) + co.flush()
            trailer = struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) + struct.pack(
                "<I", len(payload) & 0xFFFFFFFF
            )
            tail_after_header = compressed + trailer
            header_prefix = (
                b"\x1f\x8b\x08"
                + bytes([0x04])  # FLG = FEXTRA
                + struct.pack("<I", 0)  # MTIME
                + b"\x02\xff"  # XFL, OS
            )
            si = b"AB"
            comment_len = len(tail_after_header)
            eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0, 0, 0, comment_len)
            subfield = si + struct.pack("<H", len(eocd)) + eocd
            return (
                header_prefix
                + struct.pack("<H", len(subfield))
                + subfield
                + tail_after_header
            )

        data = _gzip_with_eocd_in_extra_field(_MARKER_JSON.encode())
        # Premise check: the crafted bytes really do fool a raw zip probe.
        assert zipfile.is_zipfile(io.BytesIO(data)) is True
        p = tmp_path / "crafted.json.gz"
        p.write_bytes(data)
        assert looks_like_stored_bundle_facts(p) is True

    def test_escaped_marker_key_still_classifies_as_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 5): a conforming JSON producer may
        escape a key without changing what it means -- \\u005f is just
        "_" -- and load_bundle_facts() accepts that fine since ordinary
        JSON decoding collapses the escape either way. The classifier must
        decode the candidate key the same way rather than comparing its
        raw, still-escaped spelling against the literal "artifact_type"
        token."""
        p = tmp_path / "escaped_key.json"
        p.write_text(
            '{"artifact\\u005ftype": "abicheck.bundle-facts", '
            '"schema_version": 2, "per_library_snapshots": {}}'
        )
        assert looks_like_stored_bundle_facts(p) is True

    def test_artifact_type_as_a_sibling_value_does_not_confuse_the_scan(
        self, tmp_path: Path
    ) -> None:
        """The literal string "artifact_type" appearing as some other
        field's *value* (not a key) at the root must not be mistaken for
        the marker key itself."""
        p = tmp_path / "confusing.json"
        p.write_text(
            json.dumps(
                {
                    "some_field": "artifact_type",
                    "another_field": "abicheck.bundle-facts",
                }
            )
        )
        assert looks_like_stored_bundle_facts(p) is False

    def test_ordinary_abisnapshot_json_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "snap.json"
        p.write_text(json.dumps({"library": "libfoo.so", "functions": []}))
        assert looks_like_stored_bundle_facts(p) is False

    def test_empty_object_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("{}")
        assert looks_like_stored_bundle_facts(p) is False

    def test_malformed_json_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "malformed.json"
        p.write_text("not json{")
        assert looks_like_stored_bundle_facts(p) is False

    def test_directory_is_not_stored(self, tmp_path: Path) -> None:
        d = tmp_path / "adir"
        d.mkdir()
        assert looks_like_stored_bundle_facts(d) is False

    def test_missing_path_is_not_stored(self, tmp_path: Path) -> None:
        assert looks_like_stored_bundle_facts(tmp_path / "nonexistent") is False

    def test_g40_archive_is_stored(self, tmp_path: Path) -> None:
        """Codex review, PR #1042: the G40 content-addressed zip archive
        format is a real, supported BundleFacts encoding -- it starts with
        a zip local-file-header magic, not JSON, so the plain marker scan
        alone would never recognize it; --old-bundle-facts used to route
        it to load_bundle_facts(format="auto"), which reads either shape,
        but without this classifier fix there is no way to reach that
        reader at all post-flag-removal."""
        facts = capture_bundle_facts({})
        archive_path = tmp_path / "old.bundlefacts.zip"
        save_bundle_facts(facts, archive_path, format="archive")
        assert looks_like_stored_bundle_facts(archive_path) is True

    def test_a_real_wheel_is_not_stored(self, tmp_path: Path) -> None:
        """A .whl is itself a zip file (same PK\\x03\\x04 magic as a G40
        archive) but is not a BundleFacts archive -- must not be
        misrecognized just because it opens as a zip."""
        whl_path = tmp_path / "fake_package-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl_path, "w") as zf:
            zf.writestr("fake_package/__init__.py", "")
            zf.writestr("fake_package-1.0.dist-info/METADATA", "Name: fake_package\n")
        assert looks_like_stored_bundle_facts(whl_path) is False

    def test_a_corrupted_zip_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "broken.zip"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
        assert looks_like_stored_bundle_facts(p) is False

    def test_package_archive_with_nested_marker_text_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 1): a compressed release package
        (e.g. a .tar.gz of shared libraries) whose *nested* member content
        coincidentally contains the marker text (e.g. a BundleFacts fixture
        bundled inside a test release archive) must not misclassify the
        whole package as a stored-facts document. Closed by root-anchoring
        the marker match (round 2), not by excluding recognized packages
        outright (round 1's own fix, reverted -- see
        test_bundle_facts_json_with_a_package_like_suffix_is_still_stored
        for why): a tar/gzip stream's own framing (a 512-byte tar header
        block before any member content) never decodes to bytes starting
        with ``{"artifact_type"`` at position 0, root-anchoring rules this
        out on its own."""
        tar_path = tmp_path / "release.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            data = _MARKER_JSON.encode()
            info = tarfile.TarInfo(name="nested/embedded_fixture.bundlefacts.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        assert looks_like_stored_bundle_facts(tar_path) is False

    def test_a_real_deb_package_is_not_stored(self, tmp_path: Path) -> None:
        """.deb's own ar-archive magic bytes never decode to ``{...`` at
        position 0 either -- root-anchoring rules it out the same way as
        the .tar.gz/.whl cases above, with no is_package() call involved."""
        import shutil
        import subprocess

        ar = shutil.which("ar")
        if ar is None:
            import pytest

            pytest.skip("ar is not available")
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "dummy.txt").write_text("dummy\n")
        deb_path = tmp_path / "fake.deb"
        subprocess.run(
            [ar, "rcs", str(deb_path), "dummy.txt"],
            cwd=staging,
            check=True,
            capture_output=True,
        )
        assert looks_like_stored_bundle_facts(deb_path) is False

    def test_bundle_facts_json_with_a_package_like_suffix_is_still_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 2): a genuine stored BundleFacts
        JSON document named with a package-like suffix (a plausible
        --bundle-facts-out output path from a templated CI naming
        convention, e.g. baseline.tar.gz) must still classify as stored --
        round 1's is_package() pre-check would have vetoed this purely by
        filename suffix, with no remaining route back to the BundleFacts
        loader post-flag-removal. Not an actual tar/gzip stream -- just a
        plain JSON file wearing that extension, exactly what
        --bundle-facts-out would write there."""
        p = tmp_path / "baseline.tar.gz"
        p.write_text(_MARKER_JSON)
        assert looks_like_stored_bundle_facts(p) is True

    def test_nested_artifact_type_in_an_ordinary_snapshot_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 2), fresh evidence: an ordinary
        AbiSnapshot whose own `constants` mapping happens to define a C
        constant literally named "artifact_type" with this exact string
        value JSON-serializes as a *nested* object -- an unanchored search
        matched it too, misrouting a real single-snapshot compare into the
        BundleFacts loader. The root object's own first key is
        "constants"' sibling top-level AbiSnapshot fields (library/version/
        functions/...), never "artifact_type" -- root-anchoring rejects
        this shape correctly."""
        p = tmp_path / "snap.json"
        p.write_text(
            json.dumps(
                {
                    "library": "libfoo.so",
                    "version": "1.0",
                    "functions": [],
                    "variables": [],
                    "types": [],
                    "enums": [],
                    "typedefs": [],
                    "constants": [
                        {
                            "name": "artifact_type",
                            "value": "abicheck.bundle-facts",
                        }
                    ],
                }
            )
        )
        assert looks_like_stored_bundle_facts(p) is False


class TestClassifyBundleCompareOperands:
    def test_stored_old_live_new(self, tmp_path: Path) -> None:
        old = tmp_path / "old.json"
        old.write_text(_MARKER_JSON)
        new = tmp_path / "new_dir"
        new.mkdir()
        req = classify_bundle_compare_operands(old, new)
        assert req == BundleCompareRequest(old_is_stored=True, new_is_stored=False)
        assert req.any_stored is True

    def test_live_old_live_new(self, tmp_path: Path) -> None:
        old = tmp_path / "old_dir"
        new = tmp_path / "new_dir"
        old.mkdir()
        new.mkdir()
        req = classify_bundle_compare_operands(old, new)
        assert req == BundleCompareRequest(old_is_stored=False, new_is_stored=False)
        assert req.any_stored is False

    def test_stored_new_is_classified_too(self, tmp_path: Path) -> None:
        old = tmp_path / "old_dir"
        old.mkdir()
        new = tmp_path / "new.json"
        new.write_text(_MARKER_JSON)
        req = classify_bundle_compare_operands(old, new)
        assert req.new_is_stored is True
        assert req.old_is_stored is False
