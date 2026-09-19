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

"""``storage/incremental_encode.py`` -- the bounded compressed-output write.

Per AGENTS.md's third-party-boundary rule, every supported algorithm is
exercised through the module's *actual* entry point
(:func:`write_snapshot_text_stream`), at a content scale where the codec
genuinely has work to do, and read back through the real
``read_snapshot_bytes`` chokepoint -- never only through a hand-constructed
shortcut into ``zlib``/``zstandard``.

The differential oracle is the pre-existing one-shot encoder
(``snapshot_io.encode_snapshot_bytes``), which is a genuinely independent
implementation of the same contract, not a re-derivation of the streaming
one's own formula.
"""

from __future__ import annotations

import gzip
import json
import random

import pytest

from abicheck.errors import SnapshotError
from abicheck.snapshot_io import (
    ZSTD_LEVEL_BASELINE,
    SnapshotCompression,
    encode_snapshot_bytes,
    read_snapshot_bytes,
)
from abicheck.storage.incremental_encode import encode_chunks
from abicheck.storage.snapshot_stream_write import write_snapshot_text_stream

pytest.importorskip("zstandard")


def _document(members: int = 400) -> str:
    """A realistically-shaped, realistically-sized JSON document.

    Big enough that deflate/zstd fill their windows several times over and
    the chunking actually matters -- a toy fixture collapses the behaviour
    under test to something trivial (ADR-059 §12's own lesson).
    """
    return json.dumps(
        {
            "schema_version": 48,
            "libraries": {
                f"libmember{i}.so": {
                    "functions": [
                        {
                            "mangled": f"_ZN2ns6Klass{i}7method_{j}Eiipkc",
                            "name": f"ns::Klass{i}::method_{j}",
                            "ret": "void",
                            "params": ["int", "int", "char const*"],
                        }
                        for j in range(40)
                    ]
                }
                for i in range(members)
            },
        },
        indent=2,
    )


def _fragments(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


ALGORITHMS = [SnapshotCompression.NONE, SnapshotCompression.GZIP, SnapshotCompression.ZSTD]
SUFFIX = {
    SnapshotCompression.NONE: ".json",
    SnapshotCompression.GZIP: ".json.gz",
    SnapshotCompression.ZSTD: ".json.zst",
}


class TestRoundTripAtProductionScale:
    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    @pytest.mark.parametrize("chunk", [1024, 65536])
    def test_real_entry_point_round_trip(self, tmp_path, algorithm, chunk):
        text = _document()
        assert len(text) > 1_000_000  # vacuity guard: the scale is the point
        dest = tmp_path / f"facts{SUFFIX[algorithm]}"
        result = write_snapshot_text_stream(
            _fragments(text, chunk), dest, compression=algorithm
        )
        assert result.compression is algorithm
        assert read_snapshot_bytes(dest).decode("utf-8") == text
        assert result.decoded_size_bytes == len(text.encode("utf-8"))
        assert result.stored_size_bytes == dest.stat().st_size
        if algorithm is not SnapshotCompression.NONE:
            assert result.stored_size_bytes < result.decoded_size_bytes


class TestByteIdentityWithTheOneShotEncoder:
    """The streaming path must not silently produce a different envelope."""

    @pytest.mark.parametrize("chunk", [1, 7, 4096, 1 << 20])
    def test_gzip_is_identical_for_any_chunking(self, chunk):
        raw = _document(members=60).encode("utf-8")
        streamed = b"".join(
            encode_chunks(
                iter([raw[i : i + chunk] for i in range(0, len(raw), chunk)]),
                SnapshotCompression.GZIP,
            )
        )
        assert streamed == encode_snapshot_bytes(raw, SnapshotCompression.GZIP)
        assert gzip.decompress(streamed) == raw

    @pytest.mark.parametrize("chunk", [1, 7, 4096, 1 << 20])
    def test_zstd_is_identical_when_the_decoded_size_is_known(self, chunk):
        raw = _document(members=60).encode("utf-8")
        streamed = b"".join(
            encode_chunks(
                iter([raw[i : i + chunk] for i in range(0, len(raw), chunk)]),
                SnapshotCompression.ZSTD,
                decoded_size=len(raw),
            )
        )
        assert streamed == encode_snapshot_bytes(
            raw, SnapshotCompression.ZSTD, zstd_level=ZSTD_LEVEL_BASELINE
        )

    def test_zstd_without_a_size_still_round_trips(self, tmp_path):
        """The documented tradeoff: a legal frame, no declared content size."""
        raw = _document(members=60).encode("utf-8")
        streamed = b"".join(
            encode_chunks(iter([raw]), SnapshotCompression.ZSTD, decoded_size=None)
        )
        assert streamed != encode_snapshot_bytes(raw, SnapshotCompression.ZSTD)
        dest = tmp_path / "f.json.zst"
        dest.write_bytes(streamed)
        assert read_snapshot_bytes(dest) == raw

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_output_is_deterministic_across_runs_and_chunkings(self, algorithm):
        raw = _document(members=60).encode("utf-8")
        def run(chunk):
            return b"".join(
                encode_chunks(
                    iter([raw[i : i + chunk] for i in range(0, len(raw), chunk)]),
                    algorithm,
                    decoded_size=len(raw),
                )
            )
        assert run(512) == run(512) == run(1 << 20)


class TestIncrementality:
    """The claim is *bounded buffering*, so observe the mechanism, not only
    the output: the encoder must emit before it has consumed the input, and
    must never pull the whole stream into a list of its own."""

    def test_output_starts_before_the_input_is_exhausted(self):
        consumed = []

        def source():
            for i in range(200):
                block = (f"{i:06d}" * 4000).encode("utf-8")
                consumed.append(i)
                yield block

        stream = encode_chunks(source(), SnapshotCompression.GZIP)
        first = next(stream)
        assert first  # a real buffer, not an empty priming yield
        assert len(consumed) < 200, "the encoder drained its whole input first"
        for _ in stream:
            pass
        assert len(consumed) == 200

    def test_the_writer_consumes_buffers_one_at_a_time(self, tmp_path, monkeypatch):
        import abicheck.snapshot_io as sio

        live = []
        real = sio._atomic_write_bytes

        def spy(data, path):
            def counted():
                for buf in data:
                    live.append(len(buf))
                    yield buf

            return real(counted(), path)

        monkeypatch.setattr(sio, "_atomic_write_bytes", spy)
        monkeypatch.setattr(
            "abicheck.storage.snapshot_stream_write._atomic_write_bytes", spy
        )
        # Deliberately low-redundancy: a highly compressible document
        # compresses down to a handful of buffers whatever the encoder
        # does, which would make the buffer count say nothing.
        rng = random.Random(1234)
        text = "".join(
            f'  {{"id": "{rng.getrandbits(80):020x}", "n": {rng.getrandbits(30)}}},\n'
            for _ in range(120_000)
        )
        dest = tmp_path / "f.json.gz"
        write_snapshot_text_stream(
            _fragments(text, 8192), dest, compression=SnapshotCompression.GZIP
        )
        assert len(live) > 5, "a single buffer means the document was joined"
        assert max(live) < len(text), "one buffer held the whole document"
        assert read_snapshot_bytes(dest).decode("utf-8") == text


class TestFailureLeavesTheDestinationIntact:
    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_a_producer_error_preserves_an_existing_file(self, tmp_path, algorithm):
        dest = tmp_path / f"f{SUFFIX[algorithm]}"
        original = b"previous contents, must survive"
        dest.write_bytes(original)

        def exploding():
            yield "[" + "x" * 500_000
            raise RuntimeError("producer failed mid-document")

        with pytest.raises(RuntimeError, match="producer failed"):
            write_snapshot_text_stream(exploding(), dest, compression=algorithm)
        assert dest.read_bytes() == original
        assert sorted(p.name for p in tmp_path.iterdir()) == [dest.name], (
            "a temp file was left behind"
        )

    @pytest.mark.parametrize("algorithm", ALGORITHMS)
    def test_a_producer_error_creates_no_file_at_all(self, tmp_path, algorithm):
        dest = tmp_path / f"f{SUFFIX[algorithm]}"

        def exploding():
            yield "x" * 500_000
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            write_snapshot_text_stream(exploding(), dest, compression=algorithm)
        assert not dest.exists()
        assert list(tmp_path.iterdir()) == []

    def test_a_mis_declared_zstd_size_is_a_hard_error_not_a_bad_frame(self, tmp_path):
        dest = tmp_path / "f.json.zst"
        with pytest.raises(SnapshotError, match="declared decoded size"):
            write_snapshot_text_stream(
                ["abc"],
                dest,
                compression=SnapshotCompression.ZSTD,
                decoded_size=999,
            )
        assert not dest.exists()
