# Copyright 2026 Nikolay Petrov
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

"""Core snapshot storage-envelope tests (ADR-059).

Covers the canonical ``abicheck/snapshot_io.py`` layer directly, plus the
``serialization.load_snapshot``/``save_snapshot``/``write_snapshot``
compatibility surface built on top of it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os

import pytest

from abicheck.errors import SnapshotError
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
    write_snapshot,
)
from abicheck.snapshot_io import (
    GZIP_MAGIC,
    ZSTD_MAGIC,
    SnapshotCompression,
    detect_compression_from_bytes,
    detect_snapshot_compression,
    read_snapshot_bytes,
    resolve_write_compression,
    write_snapshot_bytes,
    write_snapshot_text,
)


def _sample_snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=[
            Function(
                name="foo_init",
                mangled="_Z8foo_initv",
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="Widget",
                kind="struct",
                size_bits=64,
                alignment_bits=32,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            ),
        ],
    )


def _graph_heavy_snapshot(n: int = 200) -> AbiSnapshot:
    """A snapshot with repeated, JSON-compressible content — a small stand-in
    for the real ~57 MB L5 header graph section a large library carries
    (`AGENTS.md`'s daal/oneapi::dal acceptance numbers), used to validate
    that repeated content compresses well without needing a multi-hundred-MB
    fixture in the test suite."""
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
    return AbiSnapshot(library="libwidget", version="1.0", functions=funcs)


# ── Round trips across encodings ────────────────────────────────────────


@pytest.mark.parametrize("compression", ["none", "gzip", "zstd"])
def test_round_trip_all_encodings(tmp_path, compression):
    snap = _sample_snapshot()
    # Canonical suffixes drive auto-resolution.
    suffix = {
        "none": ".abicheck.json",
        "gzip": ".abicheck.json.gz",
        "zstd": ".abicheck.json.zst",
    }[compression]
    path = tmp_path / f"snap{suffix}"
    result = write_snapshot(snap, path)
    assert result.compression.value == compression
    loaded = load_snapshot(path)
    assert loaded.library == snap.library
    assert len(loaded.functions) == 1
    assert loaded.functions[0].mangled == "_Z8foo_initv"
    assert len(loaded.types) == 1
    assert loaded.types[0].name == "Widget"


def test_snapshot_from_dict_equal_across_encodings(tmp_path):
    snap = _sample_snapshot()
    plain = tmp_path / "a.abicheck.json"
    gz = tmp_path / "a.abicheck.json.gz"
    zst = tmp_path / "a.abicheck.json.zst"
    write_snapshot(snap, plain)
    write_snapshot(snap, gz)
    write_snapshot(snap, zst)

    d_plain = snapshot_to_dict(load_snapshot(plain))
    d_gz = snapshot_to_dict(load_snapshot(gz))
    d_zst = snapshot_to_dict(load_snapshot(zst))
    assert d_plain == d_gz == d_zst


def test_decompressed_bytes_match_plain_writer_bytes(tmp_path):
    """The decoded payload from a compressed write must be byte-identical to
    what the plain writer produces for the same logical snapshot — P0
    deliberately keeps compression as a pure envelope around identical JSON
    bytes, no separate compact dialect."""
    snap = _sample_snapshot()
    plain_path = tmp_path / "p.abicheck.json"
    gz_path = tmp_path / "p.abicheck.json.gz"
    zst_path = tmp_path / "p.abicheck.json.zst"
    write_snapshot(snap, plain_path)
    write_snapshot(snap, gz_path)
    write_snapshot(snap, zst_path)

    plain_bytes = plain_path.read_bytes()
    assert read_snapshot_bytes(gz_path) == plain_bytes
    assert read_snapshot_bytes(zst_path) == plain_bytes


# ── Detection ────────────────────────────────────────────────────────────


def test_magic_byte_detection_without_suffix(tmp_path):
    snap = _sample_snapshot()
    neutral_gz = tmp_path / "neutral_gz.dat"
    neutral_zst = tmp_path / "neutral_zst.dat"
    write_snapshot(snap, neutral_gz, compression="gzip")
    write_snapshot(snap, neutral_zst, compression="zstd")

    assert detect_snapshot_compression(neutral_gz) == SnapshotCompression.GZIP
    assert detect_snapshot_compression(neutral_zst) == SnapshotCompression.ZSTD
    assert load_snapshot(neutral_gz).library == snap.library
    assert load_snapshot(neutral_zst).library == snap.library


def test_bounded_decoded_prefix_escalates_for_low_compression_content(tmp_path):
    """CodeRabbit review: reading only the first ``n`` *raw* bytes of a
    low-compression-ratio stream can produce fewer than ``n`` *decoded*
    bytes (or fail outright, mid-frame) -- bounded_decoded_prefix must
    escalate its raw read rather than giving up on the first attempt."""
    import random

    from abicheck.snapshot_io import bounded_decoded_prefix

    random.seed(99)
    # Low-entropy/incompressible payload wrapped in valid JSON so a
    # successful decode is still verifiable; large enough that its
    # compressed form exceeds a small requested prefix `n`.
    blob = "".join(chr(random.randrange(0x21, 0x7E)) for _ in range(20000))
    text = json.dumps({"library": "x", "version": "1", "blob": blob})

    gz_path = tmp_path / "incompressible.abicheck.json.gz"
    gz_path.write_bytes(gzip.compress(text.encode(), compresslevel=9))
    assert gz_path.stat().st_size > 4096  # compressed stream spans multiple reads

    prefix = bounded_decoded_prefix(gz_path, n=100)
    assert prefix is not None
    assert prefix.startswith(b'{"library"')


def test_detect_compression_from_bytes_plain():
    assert detect_compression_from_bytes(b"{not compressed") == SnapshotCompression.NONE
    assert (
        detect_compression_from_bytes(GZIP_MAGIC + b"rest") == SnapshotCompression.GZIP
    )
    assert (
        detect_compression_from_bytes(ZSTD_MAGIC + b"rest") == SnapshotCompression.ZSTD
    )


def test_suffix_vs_magic_mismatch_is_a_hard_error(tmp_path):
    """A ``.json.zst`` file that is actually plain JSON (or vice versa) is a
    loud, diagnosable error -- never a silent guess either direction."""
    bad = tmp_path / "bad.abicheck.json.zst"
    bad.write_text('{"library": "x", "version": "1"}', encoding="utf-8")
    with pytest.raises(SnapshotError, match="zstd"):
        load_snapshot(bad)

    bad2 = tmp_path / "bad2.abicheck.json"
    bad2.write_bytes(ZSTD_MAGIC + b"payload")
    with pytest.raises(SnapshotError):
        load_snapshot(bad2)


def test_resolve_write_compression_conflict(tmp_path):
    with pytest.raises(SnapshotError):
        resolve_write_compression(
            tmp_path / "x.abicheck.json.gz", SnapshotCompression.ZSTD
        )
    # Non-conflicting explicit requests are fine.
    assert (
        resolve_write_compression(
            tmp_path / "x.abicheck.json.gz", SnapshotCompression.GZIP
        )
        == SnapshotCompression.GZIP
    )
    assert (
        resolve_write_compression(tmp_path / "x.neutral", SnapshotCompression.ZSTD)
        == SnapshotCompression.ZSTD
    )
    assert (
        resolve_write_compression(
            tmp_path / "x.abicheck.json", SnapshotCompression.AUTO
        )
        == SnapshotCompression.NONE
    )


# ── Corruption / truncation ─────────────────────────────────────────────


def test_corrupt_gzip_raises_snapshot_error(tmp_path):
    p = tmp_path / "c.abicheck.json.gz"
    p.write_bytes(GZIP_MAGIC + b"\x00\x00garbage-not-really-gzip")
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_truncated_gzip_raises_snapshot_error(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "t.abicheck.json.gz"
    write_snapshot(snap, p)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_corrupt_zstd_raises_snapshot_error(tmp_path):
    p = tmp_path / "c.abicheck.json.zst"
    p.write_bytes(ZSTD_MAGIC + b"\x00\x00garbage-not-really-zstd-content")
    with pytest.raises(SnapshotError):
        load_snapshot(p)


def test_truncated_zstd_raises_snapshot_error(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "t.abicheck.json.zst"
    write_snapshot(snap, p)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    with pytest.raises(SnapshotError):
        load_snapshot(p)


# ── Decompression limits ────────────────────────────────────────────────


def test_decoded_size_overflow_gzip(tmp_path):
    big_text = json.dumps({"library": "x", "version": "1", "pad": "a" * 5000})
    p = tmp_path / "big.abicheck.json.gz"
    p.write_bytes(gzip.compress(big_text.encode(), compresslevel=9, mtime=0))
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_decoded_size_overflow_zstd(tmp_path):
    big_text = json.dumps({"library": "x", "version": "1", "pad": "a" * 5000})
    p = tmp_path / "big.abicheck.json.zst"
    write_snapshot_text(big_text, p, compression=SnapshotCompression.ZSTD)
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_plain_snapshot_overflow(tmp_path):
    p = tmp_path / "big.abicheck.json"
    p.write_text(json.dumps({"library": "x", "version": "1", "pad": "a" * 5000}))
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_zstd_max_window_size_is_kibibytes_not_bytes():
    """Codex review, PR #699: python-zstandard's ``ZstdDecompressor(
    max_window_size=...)`` takes its argument in *kibibytes*, confirmed
    against its own docstring -- passing a raw byte count (as an earlier
    version of this module did) silently permitted a window 1024x larger
    than the documented "2 GiB window ceiling" (effectively ~2 TiB)."""
    from abicheck.snapshot_io import _ZSTD_MAX_WINDOW_LOG, _ZSTD_MAX_WINDOW_SIZE_KIB

    intended_byte_ceiling = 1 << _ZSTD_MAX_WINDOW_LOG
    assert _ZSTD_MAX_WINDOW_SIZE_KIB * 1024 == intended_byte_ceiling
    assert intended_byte_ceiling == 2 * 1024 * 1024 * 1024  # 2 GiB, per the comment


def test_zstd_decoder_rejects_window_above_ceiling(tmp_path):
    """The *effective* max_window_size passed to python-zstandard's
    ZstdDecompressor must actually cap accepted windows at the ceiling --
    not silently allow an oversized one because of a unit-conversion bug.
    zstd's own ZSTD_WINDOWLOG_MAX is 31 (2 GiB) on a 64-bit build, i.e.
    exactly this module's ceiling, so a legitimate max-size frame (window_log
    31) must still decode successfully with the fixed KiB-denominated value
    -- if the conversion silently permitted a *smaller* effective window than
    intended, this would fail instead."""
    zstandard = pytest.importorskip("zstandard")

    params = zstandard.ZstdCompressionParameters(window_log=31)
    cctx = zstandard.ZstdCompressor(compression_params=params)
    compressed = cctx.compress(b"a" * (1 << 20))
    p = tmp_path / "max_window.abicheck.json.zst"
    p.write_bytes(compressed)
    # Not real JSON, so it'll fail JSON parsing downstream, but the
    # decompression step itself (the thing max_window_size gates) must
    # succeed rather than raising "too much memory" for a legitimate,
    # at-the-ceiling window.
    assert read_snapshot_bytes(p) == b"a" * (1 << 20)


def test_oversized_stored_file_rejected_before_full_read(tmp_path):
    """CodeRabbit review: the stored-file-size check (via stat(), before
    read_bytes()) must reject a file whose *stored* size alone already
    exceeds the limit (plus the compressed-file margin, see the boundary
    test below) -- using low-entropy content, well beyond the margin, so
    the compressed size stays large rather than shrinking under the limit
    itself."""
    import os as _os
    import random

    random.seed(1234)
    incompressible = bytes(random.randrange(256) for _ in range(200_000))
    p = tmp_path / "big.abicheck.json.gz"
    p.write_bytes(gzip.compress(incompressible, compresslevel=9))
    stored_size = _os.path.getsize(p)
    assert stored_size > 100
    with pytest.raises(SnapshotError, match="exceeds"):
        read_snapshot_bytes(p, max_decoded_bytes=100)


def test_tiny_compressed_payload_not_rejected_by_stored_size_precheck(tmp_path):
    """Codex review, PR #699: gzip/zstd container framing adds a small,
    fixed overhead independent of payload size (e.g. `{}` decodes to 2
    bytes but gzip-compresses to 22) -- the stored-size precheck above must
    not reject a legitimately tiny/boundary decoded payload purely because
    its *stored* size (with framing overhead) exceeds the exact decoded
    limit. Only a plain/uncompressed file, whose stored size equals its
    decoded size exactly, gets the tight comparison."""
    for compression in (SnapshotCompression.GZIP, SnapshotCompression.ZSTD):
        p = tmp_path / f"tiny.abicheck.json.{compression.value}"
        write_snapshot_text("{}", p, compression=compression)
        stored_size = p.stat().st_size
        assert stored_size > 2  # framing overhead really does exceed the payload
        assert read_snapshot_bytes(p, max_decoded_bytes=2) == b"{}"


# ── Determinism ──────────────────────────────────────────────────────────


def test_deterministic_gzip_bytes(tmp_path):
    snap = _sample_snapshot()
    p1 = tmp_path / "a.abicheck.json.gz"
    p2 = tmp_path / "b.abicheck.json.gz"
    write_snapshot(snap, p1)
    write_snapshot(snap, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_deterministic_zstd_bytes(tmp_path):
    snap = _sample_snapshot()
    p1 = tmp_path / "a.abicheck.json.zst"
    p2 = tmp_path / "b.abicheck.json.zst"
    write_snapshot(snap, p1)
    write_snapshot(snap, p2)
    assert p1.read_bytes() == p2.read_bytes()


def test_gzip_header_has_no_embedded_filename_or_mtime(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "some_very_specific_name.abicheck.json.gz"
    write_snapshot(snap, p)
    data = p.read_bytes()
    # gzip header: mtime is bytes 4-7 (must be zero); FNAME flag bit is bit 3
    # of the flags byte (byte 3) and must be unset.
    assert data[4:8] == b"\x00\x00\x00\x00"
    flags = data[3]
    assert not (flags & 0x08), "gzip FNAME flag must not be set (no embedded filename)"
    # OS-identifier byte: CPython's gzip module disagrees on this across
    # supported 3.10-3.14 interpreters (3 "Unix" vs. 255 "unknown") even with
    # mtime pinned, so it's forced to 255 explicitly -- see _compress_gzip.
    assert data[9] == 0xFF, (
        "gzip OS byte must be forced to 255 (unknown) for cross-version determinism"
    )


# ── Atomic writes ────────────────────────────────────────────────────────


def test_atomic_write_leaves_no_temp_file(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "x.abicheck.json.zst"
    write_snapshot(snap, p)
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics only")
def test_new_file_honors_umask_not_owner_only(tmp_path):
    """tempfile.mkstemp() always creates 0600; a snapshot write must not
    silently make every new file owner-only. It should also not ignore an
    explicit, more restrictive umask by forcing a fixed mode (two rounds of
    Codex/CodeRabbit review): the temp file is created via a direct
    os.open(..., 0o666) so the kernel applies the *caller's* umask
    atomically at creation time -- no process-wide os.umask() read/toggle
    involved either way."""
    old_umask = os.umask(0o022)
    try:
        snap = _sample_snapshot()
        p = tmp_path / "new.abicheck.json"
        write_snapshot(snap, p)
        assert oct(p.stat().st_mode & 0o777) == oct(0o644)
    finally:
        os.umask(old_umask)

    old_umask = os.umask(0o077)
    try:
        snap = _sample_snapshot()
        p2 = tmp_path / "restrictive.abicheck.json"
        write_snapshot(snap, p2)
        assert oct(p2.stat().st_mode & 0o777) == oct(0o600)
    finally:
        os.umask(old_umask)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file mode semantics only")
def test_existing_file_mode_is_preserved_across_rewrite(tmp_path):
    """Rewriting an existing snapshot (e.g. re-dumping a shared baseline)
    must not silently strip its group/world-readable permissions."""
    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    os.chmod(p, 0o640)
    write_snapshot(snap, p)
    assert oct(p.stat().st_mode & 0o777) == oct(0o640)


@pytest.mark.skipif(
    os.name == "nt", reason="os.chown/getgrall not available on Windows"
)
def test_existing_file_group_is_preserved_across_rewrite(tmp_path):
    """Codex review, PR #699: rewriting an existing snapshot used to
    silently revoke a deliberately-assigned group owner (e.g. an
    `abi-readers` group with no setgid parent directory) -- the fresh temp
    file inherits the writer's own default group, and only the mode was
    carried over to the replacement, not the group. Requires root or
    CAP_CHOWN to reassign an arbitrary group, so this is best-effort like
    the write path itself."""
    import grp

    try:
        target_gid = grp.getgrnam("daemon").gr_gid
    except KeyError:
        pytest.skip("no 'daemon' group on this system")

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    try:
        os.chown(p, -1, target_gid)
    except (OSError, AttributeError):
        pytest.skip("cannot chown to an arbitrary group in this environment")
    if p.stat().st_gid != target_gid:
        pytest.skip("chown did not take effect (insufficient privileges)")

    write_snapshot(snap, p)
    assert p.stat().st_gid == target_gid


@pytest.mark.skipif(
    os.name == "nt", reason="os.chown/getpwall not available on Windows"
)
def test_existing_file_owner_is_preserved_across_rewrite(tmp_path):
    """Codex review, PR #699 (second finding on the same fix): rewriting an
    existing snapshot also used to silently transfer ownership to the
    writer -- the fresh temp file is owned by the writer, and only the mode
    (then, after the first fix, the group) was carried over to the
    replacement, not the uid. Affects a shared baseline owned by a service
    account. Requires root/CAP_CHOWN to reassign an arbitrary uid, so this
    is best-effort like the write path itself."""
    import pwd

    try:
        target_uid = pwd.getpwnam("daemon").pw_uid
    except KeyError:
        pytest.skip("no 'daemon' user on this system")

    snap = _sample_snapshot()
    p = tmp_path / "existing.abicheck.json"
    p.write_text("placeholder")
    try:
        os.chown(p, target_uid, -1)
    except (OSError, AttributeError):
        pytest.skip("cannot chown to an arbitrary uid in this environment")
    if p.stat().st_uid != target_uid:
        pytest.skip("chown did not take effect (insufficient privileges)")

    write_snapshot(snap, p)
    assert p.stat().st_uid == target_uid


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks need elevated privileges on Windows"
)
def test_write_through_symlink_preserves_the_link(tmp_path):
    """Codex review, PR #699: os.replace(tmp_path, path) swaps *path*'s own
    directory entry, so writing to a symlinked destination used to destroy
    the link -- regressing the previous open(path, "w") behavior, which
    follows a symlink and writes through it. Writing to a path that is a
    symlink must update the link's *target* and leave the link itself
    intact, exactly like the plain-open behavior it replaced."""
    real = tmp_path / "real.abicheck.json"
    write_snapshot_text('{"a": 1}', real, compression=SnapshotCompression.NONE)
    link = tmp_path / "link.abicheck.json"
    link.symlink_to(real)

    write_snapshot_text('{"a": 2}', link, compression=SnapshotCompression.NONE)

    assert link.is_symlink()
    assert os.readlink(link) == str(real)
    assert real.read_text() == '{"a": 2}'
    assert link.read_text() == '{"a": 2}'


@pytest.mark.skipif(
    os.name == "nt", reason="symlinks need elevated privileges on Windows"
)
def test_write_through_dangling_symlink_creates_target(tmp_path):
    """A symlink whose target doesn't exist yet must still resolve -- the
    write creates the target file, not a plain file at the link's own
    path."""
    target = tmp_path / "does_not_exist_yet.abicheck.json"
    link = tmp_path / "link.abicheck.json"
    link.symlink_to(target)

    write_snapshot_text('{"a": 3}', link, compression=SnapshotCompression.NONE)

    assert link.is_symlink()
    assert target.read_text() == '{"a": 3}'


def test_failed_write_preserves_existing_destination(tmp_path, monkeypatch):
    snap = _sample_snapshot()
    p = tmp_path / "x.abicheck.json.zst"
    write_snapshot(snap, p)
    original_bytes = p.read_bytes()

    import abicheck.snapshot_io as snapshot_io_mod

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(snapshot_io_mod.os, "replace", _boom)
    with pytest.raises(OSError):
        write_snapshot(snap, p)
    # Destination must be untouched, and no stray temp file left behind.
    assert p.read_bytes() == original_bytes
    leftovers = [f for f in tmp_path.iterdir() if f != p]
    assert leftovers == []


# ── Content ──────────────────────────────────────────────────────────────


def test_unicode_content_round_trips(tmp_path):
    snap = AbiSnapshot(library="libéé", version="1.0-ä")
    for suffix in (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst"):
        p = tmp_path / f"u{suffix}"
        write_snapshot(snap, p)
        loaded = load_snapshot(p)
        assert loaded.library == snap.library
        assert loaded.version == snap.version


def test_empty_minimal_snapshot(tmp_path):
    snap = AbiSnapshot(library="empty", version="0")
    for suffix in (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst"):
        p = tmp_path / f"e{suffix}"
        write_snapshot(snap, p)
        loaded = load_snapshot(p)
        assert loaded.library == "empty"
        assert loaded.functions == []


def test_graph_heavy_snapshot_round_trips_and_compresses_well(tmp_path):
    snap = _graph_heavy_snapshot(400)
    plain = tmp_path / "g.abicheck.json"
    zst = tmp_path / "g.abicheck.json.zst"
    write_snapshot(snap, plain)
    result = write_snapshot(snap, zst)
    loaded = load_snapshot(zst)
    assert len(loaded.functions) == 400
    # Repeated, path-heavy content compresses well -- generous bound (not the
    # tuned ~10-15% target from ADR-059's real-world benchmark) so this stays
    # robust to CPU/zstd-version noise while still catching a regression to
    # "compression barely helps at all".
    assert result.stored_size_bytes < result.decoded_size_bytes * 0.5


def test_dump_provenance_survives_load_save_round_trip(tmp_path):
    """dump_provenance (folded into the payload dict by cli_dump_helpers, not
    an AbiSnapshot field) is opaque top-level JSON -- verify the storage layer
    itself doesn't drop or corrupt an arbitrary extra top-level key across
    every encoding, independent of the CLI fold step."""
    snap = _sample_snapshot()
    payload = snapshot_to_dict(snap)
    payload["dump_provenance"] = {
        "requested_depth": "headers",
        "effective_depth": "headers",
        "degraded": False,
        "frontend": "clang",
    }
    text = json.dumps(payload, indent=2)
    for suffix in (".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst"):
        p = tmp_path / f"prov{suffix}"
        write_snapshot_text(text, p)
        loaded_bytes = read_snapshot_bytes(p)
        loaded_payload = json.loads(loaded_bytes)
        assert loaded_payload["dump_provenance"]["frontend"] == "clang"
        # snapshot_from_dict must not choke on the unknown extra key either.
        assert snapshot_from_dict(loaded_payload).library == snap.library


def test_pre_compression_fixture_still_loads(tmp_path):
    """A snapshot written before ADR-059 (plain UTF-8 JSON, no magic bytes)
    must keep loading with zero behavior change."""
    snap = _sample_snapshot()
    p = tmp_path / "legacy.abi.json"
    p.write_text(json.dumps(snapshot_to_dict(snap), indent=2), encoding="utf-8")
    loaded = load_snapshot(p)
    assert loaded.library == snap.library


def test_save_snapshot_legacy_positional_signature_unchanged(tmp_path):
    """save_snapshot(snap, path) -- the historical two-positional-arg call --
    must keep working exactly as before; compression is keyword-only."""
    snap = _sample_snapshot()
    p = tmp_path / "legacy_call.abicheck.json"
    save_snapshot(snap, p)  # no keyword args at all
    assert load_snapshot(p).library == snap.library


def test_write_snapshot_bytes_result_fields(tmp_path):
    snap = _sample_snapshot()
    p = tmp_path / "r.abicheck.json.zst"
    result = write_snapshot(snap, p)
    assert result.path == p
    assert result.compression == SnapshotCompression.ZSTD
    assert result.decoded_size_bytes > 0
    assert result.stored_size_bytes > 0
    assert 0 < result.ratio <= 1.0
    assert result.stored_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_write_snapshot_bytes_direct(tmp_path):
    data = b'{"library": "x", "version": "1"}'
    p = tmp_path / "direct.abicheck.json.gz"
    result = write_snapshot_bytes(data, p, compression=SnapshotCompression.GZIP)
    assert result.decoded_size_bytes == len(data)
    assert read_snapshot_bytes(p) == data
