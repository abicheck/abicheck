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

"""Primitive-level property tests for ``snapshot_io.bounded_decoded_prefix``
(AGENTS.md "Primitive-level property tests"; bug class
``storage.short_decode_mistaken_for_complete_decode`` in
``tests/regressions/manifest.py``).

**The bug class.** ``bounded_decoded_prefix`` reads a bounded *raw* prefix of
a compressed snapshot and asks the decoder for ``n`` decoded bytes, escalating
the raw read when the frame turns out to be cut mid-stream. It used to treat
"the decoder did not raise" as "the decode succeeded" -- but a decoder handed
a frame truncated at an arbitrary raw byte boundary returns a *short* result,
commonly ``b""``, with no exception at all (``zstandard``'s
``stream_reader.read()`` does this whenever the first compressed block is
still incomplete). That empty result was accepted and returned, so
``classify.AbiJsonClassifier``/``workflows.input_resolution`` saw an empty
prefix and reported ``Cannot detect format of '<file>'`` for a perfectly
valid ``.json.zst`` baseline. Reported against real oneDAL baselines
(``libonedal_core.so.abicheck.json.zst`` and five siblings, all ``ERROR``).

**Why the pre-existing tests did not catch it.** Both
``test_bounded_decoded_prefix_gzip_multi_read`` and
``test_bounded_decoded_prefix_zstd_with_realistic_window`` build fixtures
whose *compression ratio* is far above anything real: a 9 MiB run of ``"a"``,
or a 20 kB blob. At those ratios the first 4096 stored bytes already decode
to well over 4096 bytes, so the truncation branch is never reached. This is
exactly the "toy-shaped fixture whose actual required behavior collapses to
something trivial" failure AGENTS.md's third-party-boundary bullet names --
the same shape as the ADR-059 §12 window-size escape, one function over.

**The invariant these tests state**, for *any* valid snapshot storage
envelope and *any* requested prefix length::

    bounded_decoded_prefix(path, n) == read_snapshot_bytes(path)[:n]

The oracle is a full, independent decompression through the module's *other*
public entry point -- it shares no escalation arithmetic, no raw-prefix
sizing and no ``_try_decode_prefix`` call with the code under test, so it
cannot agree with the implementation by construction the way a
self-consistency assertion would.

Inputs are generated across the axes that actually decide whether the bug
fires -- compression ratio (the real one), payload size relative to the
4096-byte probe, compression level, algorithm, and the requested ``n`` --
rather than pinned to the one reported oneDAL file.
"""

from __future__ import annotations

import io
import json
import random

import pytest

from abicheck.snapshot_io import (
    SnapshotCompression,
    bounded_decoded_prefix,
    encode_snapshot_bytes,
    read_snapshot_bytes,
)

_ALGORITHMS = (SnapshotCompression.GZIP, SnapshotCompression.ZSTD)


def _payload(*, entries: int, entropy_bits: int, seed: int) -> bytes:
    """Snapshot-shaped JSON whose compression ratio is tunable.

    ``entropy_bits`` controls how much per-entry randomness the symbol names
    carry, which is the single knob that decides whether 4096 stored bytes
    decode to more or fewer than 4096 bytes -- i.e. whether the truncation
    branch under test is reached at all. Real C++ mangled names sit near the
    high end; a repeated-content fixture sits near zero, which is precisely
    why the pre-existing tests could not see this bug.
    """
    rng = random.Random(seed)
    ceiling = 1 << max(entropy_bits, 1)
    funcs = [
        {
            "name": f"sym_{i}_{rng.randrange(ceiling):x}",
            "mangled": f"_ZN6onedal{rng.randrange(ceiling):x}E{i}v",
            "return_type": "void",
            "params": [{"name": "x", "type": "int"}],
        }
        for i in range(entries)
    ]
    return json.dumps(
        {"schema_version": 44, "library": "libonedal_core.so", "functions": funcs}
    ).encode()


def _write(tmp_path, name: str, data: bytes, compression: SnapshotCompression):
    encoded = encode_snapshot_bytes(data, compression=compression)
    path = tmp_path / name
    path.write_bytes(encoded)
    return path, encoded


# ── The invariant, swept across the axes that decide whether it holds ────────


@pytest.mark.parametrize("compression", _ALGORITHMS)
@pytest.mark.parametrize("entropy_bits", [0, 8, 20, 48])
@pytest.mark.parametrize("entries", [1, 40, 1000, 3000])
def test_prefix_equals_full_decode_prefix(tmp_path, compression, entropy_bits, entries):
    """``bounded_decoded_prefix(p, n) == read_snapshot_bytes(p)[:n]`` for
    every (algorithm x compression ratio x payload size) combination -- the
    class invariant, against an oracle that decompresses the whole file
    through an independent entry point.

    ``entropy_bits=48`` at ``entries>=1000`` is the regime the reported
    oneDAL baselines occupy and the one that used to return ``b""``;
    ``entropy_bits=0``/``entries=1`` are the opposite corners (a
    trivially-compressible payload, and a file smaller than the probe)
    that must keep working.
    """
    zstd = compression is SnapshotCompression.ZSTD
    if zstd:
        pytest.importorskip("zstandard")

    data = _payload(
        entries=entries, entropy_bits=entropy_bits, seed=entries + entropy_bits
    )
    suffix = ".json.zst" if zstd else ".json.gz"
    path, _ = _write(tmp_path, f"lib{entries}{suffix}", data, compression)

    expected_full = read_snapshot_bytes(path)
    assert expected_full == data  # oracle sanity: the envelope is lossless

    got = bounded_decoded_prefix(path)
    assert got == expected_full[:4096]


@pytest.mark.parametrize("compression", _ALGORITHMS)
@pytest.mark.parametrize("n", [1, 7, 512, 4095, 4096, 4097, 20000])
def test_prefix_length_is_honored_for_every_requested_n(tmp_path, compression, n):
    """The invariant must not depend on ``n`` lining up with the module's own
    internal probe/escalation sizes (4096 and its multiples) -- an
    off-by-a-block requested length is exactly the sibling case a fixed-``n``
    regression test would leave open."""
    if compression is SnapshotCompression.ZSTD:
        pytest.importorskip("zstandard")

    data = _payload(entries=2000, entropy_bits=48, seed=n)
    suffix = ".json.zst" if compression is SnapshotCompression.ZSTD else ".json.gz"
    path, _ = _write(tmp_path, f"libn{suffix}", data, compression)

    assert bounded_decoded_prefix(path, n) == data[:n]


def test_low_ratio_zstd_would_regress_without_the_fix(tmp_path):
    """Guards the *mechanism*, not just the outcome: assert the fixture
    really is in the regime where the first 4096 stored bytes cannot
    produce 4096 decoded bytes, so a future change that quietly makes this
    file compressible again (and therefore stops exercising the branch) is
    visible as a failure here rather than as silent coverage loss."""
    pytest.importorskip("zstandard")

    from abicheck.snapshot_io import _try_decode_prefix

    data = _payload(entries=3000, entropy_bits=48, seed=1)
    path, encoded = _write(
        tmp_path, "lowratio.json.zst", data, SnapshotCompression.ZSTD
    )
    assert len(encoded) > 4096

    # The precondition the bug needs: a 4096-byte raw prefix decodes short.
    short = _try_decode_prefix(encoded[:4096], SnapshotCompression.ZSTD, 4096)
    assert short is None or len(short) < 4096

    # And the public primitive still answers correctly despite it.
    assert bounded_decoded_prefix(path) == data[:4096]


@pytest.mark.parametrize("compression", _ALGORITHMS)
def test_corrupt_envelope_still_reports_undecodable(tmp_path, compression):
    """The fix must not turn "genuinely undecodable" into a short/empty
    success: garbage carrying a valid magic prefix stays ``None``."""
    if compression is SnapshotCompression.ZSTD:
        pytest.importorskip("zstandard")

    data = _payload(entries=1500, entropy_bits=48, seed=3)
    suffix = ".json.zst" if compression is SnapshotCompression.ZSTD else ".json.gz"
    path, encoded = _write(tmp_path, f"corrupt{suffix}", data, compression)
    corrupted = bytearray(encoded)
    for i in range(16, min(len(corrupted), 3000)):
        corrupted[i] ^= 0xFF
    path.write_bytes(bytes(corrupted))

    assert bounded_decoded_prefix(path) is None


def test_randomized_ratio_sweep(tmp_path):
    """A deterministic randomized sweep over the ratio/size space, so the
    invariant is checked against inputs nobody chose by hand -- the
    "adversarially generated, not a fixed example" half of AGENTS.md's
    bug-class contract."""
    pytest.importorskip("zstandard")
    rng = random.Random(20260908)

    for case in range(12):
        entries = rng.choice([1, 3, 250, 1500, 3000])
        entropy_bits = rng.choice([0, 4, 16, 32, 64])
        compression = rng.choice(_ALGORITHMS)
        n = rng.choice([1, 100, 4096, 9000])
        data = _payload(entries=entries, entropy_bits=entropy_bits, seed=case)
        suffix = ".json.zst" if compression is SnapshotCompression.ZSTD else ".json.gz"
        path, _ = _write(tmp_path, f"sweep{case}{suffix}", data, compression)
        assert bounded_decoded_prefix(path, n) == read_snapshot_bytes(path)[:n], (
            f"case={case} entries={entries} entropy_bits={entropy_bits} "
            f"compression={compression.value} n={n}"
        )


# ── Multi-frame (concatenated) envelopes ────────────────────────────────────
#
# A second, independent way for a decode to come back short: `zstandard`'s
# `stream_reader.read()` stops at every *frame* boundary, so a valid
# concatenated stream yields only its first frame's payload on one call --
# however small that frame is. The escalation rule above cannot help here,
# because the file is not truncated at all; reading a larger raw prefix
# returns the same first frame. Codex review on PR #1165 caught this: the
# original fix accepted that short result as final once the file was
# exhausted, so a snapshot split after its opening `{` classified as `b"{"`.


def _concatenated(payload: bytes, *, cuts: list[int]) -> bytes:
    """Compress *payload* as several back-to-back zstd frames, split at *cuts*."""
    zstandard = pytest.importorskip("zstandard")

    cctx = zstandard.ZstdCompressor(write_checksum=False, write_content_size=True)
    bounds = [0, *cuts, len(payload)]
    return b"".join(
        cctx.compress(payload[a:b]) for a, b in zip(bounds, bounds[1:]) if b > a
    )


@pytest.mark.parametrize(
    "cuts",
    [
        [1],  # the reported shape: a single byte, then everything else
        [1, 2, 3],  # several pathologically tiny leading frames
        [10, 5000],  # a small frame, then two large ones
        [4095],  # a frame ending one byte short of the probe
        [4096],  # ... and exactly at it
    ],
)
def test_multi_frame_envelope_honors_the_invariant(tmp_path, cuts):
    """The invariant must hold for a concatenated envelope too, not only a
    single-frame one -- otherwise "for every valid storage envelope" is a
    claim the suite does not actually check."""
    pytest.importorskip("zstandard")

    data = _payload(entries=1500, entropy_bits=32, seed=sum(cuts))
    path = tmp_path / "multiframe.json.zst"
    path.write_bytes(_concatenated(data, cuts=cuts))

    full = read_snapshot_bytes(path)
    assert full == data, "oracle: the concatenated envelope is itself lossless"
    assert bounded_decoded_prefix(path) == full[:4096]


@pytest.mark.parametrize("n", [1, 512, 4096, 9000])
def test_multi_frame_prefix_length_is_honored(tmp_path, n):
    """...and independently of the requested length, since the frame
    boundary that truncates the read has nothing to do with `n`."""
    pytest.importorskip("zstandard")

    data = _payload(entries=1500, entropy_bits=32, seed=n)
    path = tmp_path / "multiframe_n.json.zst"
    path.write_bytes(_concatenated(data, cuts=[1, 37, 900]))

    assert bounded_decoded_prefix(path, n) == data[:n]


def test_a_tiny_leading_frame_would_regress_without_the_fix(tmp_path):
    """Guards the mechanism: assert a single `read()` really does stop at the
    first frame, so this file keeps exercising the branch rather than
    silently passing if the dependency's behavior changes."""
    zstandard = pytest.importorskip("zstandard")

    from abicheck.snapshot_io import _try_decode_prefix

    data = _payload(entries=1500, entropy_bits=32, seed=11)
    blob = _concatenated(data, cuts=[1])
    dctx = zstandard.ZstdDecompressor()
    with dctx.stream_reader(io.BytesIO(blob)) as reader:
        assert len(reader.read(4096)) == 1, (
            "the dependency no longer stops at a frame boundary; this file's "
            "fixtures no longer reproduce the multi-frame regime"
        )

    # The module's own decode crosses the boundary anyway.
    assert _try_decode_prefix(blob, SnapshotCompression.ZSTD, 4096) == data[:4096]


# ── The reported symptom, through the real public surface ───────────────────


_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"


def _dense(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(length))


def _realistic_zstd_snapshot(
    tmp_path, name: str = "libonedal_core.so.abicheck.json.zst"
):
    """A real :class:`AbiSnapshot` written through the real ``save_snapshot``
    write path, whose stored form sits in the low-ratio regime the classifier
    used to choke on.

    Deliberately *not* the shared ``graph_heavy_snapshot`` fixture: a
    snapshot of repeated, boilerplate-dominated declarations compresses at
    well over 100:1, so its first stored 4096 bytes already span several
    complete zstd blocks and the truncation branch is never reached -- the
    same toy-fixture trap this module's header describes. Long, mutually
    dissimilar mangled names (real oneDAL carries deeply-templated ones) are
    what push the ratio down far enough for a 4096-byte stored prefix to land
    mid-block.

    The precondition is *asserted*, not assumed: if a future zstd/serializer
    change moves this fixture back out of the regime, these tests say so
    instead of quietly passing for the wrong reason.
    """
    from abicheck.model import AbiSnapshot, Function, Param, Visibility
    from abicheck.serialization import save_snapshot
    from abicheck.snapshot_io import _try_decode_prefix

    rng = random.Random(4242)
    funcs = [
        Function(
            name=f"oneapi_dal_kernel_{i}_{_dense(rng, 2000)}",
            mangled=f"_ZN6oneapi3dal{_dense(rng, 4000)}6kernelE{i}v",
            return_type="void",
            params=[Param(name="p", type=_dense(rng, 200))],
            visibility=Visibility.PUBLIC,
        )
        for i in range(400)
    ]
    snap = AbiSnapshot(library="libonedal_core.so", version="2026.0", functions=funcs)
    path = tmp_path / name
    save_snapshot(snap, str(path))

    stored = path.read_bytes()
    assert len(stored) > 4096
    short = _try_decode_prefix(stored[:4096], SnapshotCompression.ZSTD, 4096)
    assert short is None or len(short) < 4096, (
        "fixture no longer reproduces the reported regime: its first 4096 "
        "stored bytes already decode to a full 4096-byte prefix, so these "
        "tests would pass without exercising the truncation branch at all"
    )
    return path, snap


def test_zstd_snapshot_resolves_through_service_entry_point(tmp_path):
    """The reported oneDAL failure, end to end: a low-ratio ``.json.zst``
    baseline must resolve through ``abicheck.service.resolve_input`` instead
    of failing with ``Cannot detect format of '<path>'``.

    This is the surface the six ``ERROR`` rows came from, one layer above
    the primitive the property tests above pin -- the same "prove it through
    the public workflow, not only the internal helper" rule AGENTS.md states
    for a shipped fix.
    """
    pytest.importorskip("zstandard")

    from abicheck.service import resolve_input

    path, snap = _realistic_zstd_snapshot(tmp_path)
    assert path.stat().st_size > 4096

    resolved = resolve_input(path)
    assert resolved.library == snap.library
    assert len(resolved.functions) == len(snap.functions)


def test_zstd_snapshot_classifies_as_abi_json(tmp_path):
    """The classifier itself (``classify.CompressedAbiJsonClassifier``) -- the
    actual consumer of the truncated prefix, and the one that turned a short
    decode into ``False`` -- must recognize the same file."""
    pytest.importorskip("zstandard")

    from abicheck.classify import CompressedAbiJsonClassifier

    path, _ = _realistic_zstd_snapshot(tmp_path, "libonedal.so.abicheck.json.zst")
    assert CompressedAbiJsonClassifier().accepts(path) is True
