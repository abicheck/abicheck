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

"""The snapshot-envelope byte ceilings as public operator knobs.

Split out of `test_snapshot_compression.py` (its own no-growth baseline)
because this is a different claim: not that a round trip preserves bytes,
but that the ceiling an operator sets is the ceiling the real read path
enforces -- per supported algorithm, through `write_snapshot_bytes`/
`read_snapshot_bytes` rather than a shortcut into gzip/zstandard's own
lower-level API (ADR-059 §12).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.snapshot_io import (
    SnapshotCompression,
    read_snapshot_bytes,
    write_snapshot_bytes,
)


class TestPublicByteLimitEnvKnobs:
    """The decompression-bomb ceilings are public, documented knobs.

    Exercised through the real `read_snapshot_bytes` entry point with no
    explicit `max_decoded_bytes` argument -- the only path a CI operator
    can actually reach -- rather than by calling the parser directly.
    """

    def _write(self, tmp_path: Path, payload: bytes) -> Path:
        p = tmp_path / "s.json"
        p.write_bytes(payload)
        return p

    def test_public_spelling_lowers_the_decoded_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = self._write(tmp_path, b'{"a": "' + b"x" * 200 + b'"}')
        monkeypatch.setenv("ABICHECK_SNAPSHOT_MAX_DECODED_BYTES", "10")
        with pytest.raises(SnapshotError):
            read_snapshot_bytes(p)

    def test_public_spelling_raises_the_decoded_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = b'{"a": "' + b"x" * 200 + b'"}'
        p = self._write(tmp_path, payload)
        monkeypatch.setenv("ABICHECK_SNAPSHOT_MAX_DECODED_BYTES", "10")
        monkeypatch.setenv("_ABICHECK_SNAPSHOT_MAX_DECODED_BYTES", str(len(payload)))
        # The legacy private spelling wins when both are set, so an existing
        # CI job that already sets it keeps its exact behavior.
        assert read_snapshot_bytes(p) == payload

    @pytest.mark.parametrize(
        "algorithm", [SnapshotCompression.GZIP, SnapshotCompression.ZSTD]
    )
    def test_the_ceiling_is_enforced_inside_real_decompression(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        algorithm: SnapshotCompression,
    ) -> None:
        """Per supported algorithm, through the real write/read chokepoints.

        A plain file's cap is decided by the stored-size precheck, so a
        plain-only test never reaches the incremental decoded-size
        enforcement inside `_decompress_gzip`/`_decompress_zstd` -- the
        code the ceiling actually exists for. Each algorithm is written by
        `write_snapshot_bytes` and read back by `read_snapshot_bytes`, not
        by hand-driving gzip/zstandard's own lower-level API (ADR-059 §12:
        the shortcut passed identically before and after a real regression).
        """
        payload = json.dumps({"pad": "x" * 5000}).encode("utf-8")
        p = tmp_path / "s.json"
        write_snapshot_bytes(payload, p, compression=algorithm)
        # Compressed on disk: the stored file is smaller than the payload,
        # so a cap between the two can only be hit while decompressing.
        assert p.stat().st_size < len(payload)

        monkeypatch.setenv("ABICHECK_SNAPSHOT_MAX_DECODED_BYTES", "500")
        with pytest.raises(SnapshotError):
            read_snapshot_bytes(p)

        monkeypatch.setenv("ABICHECK_SNAPSHOT_MAX_DECODED_BYTES", str(len(payload)))
        assert read_snapshot_bytes(p) == payload

    @pytest.mark.parametrize("bad", ["", "0", "-1", "not-a-number"])
    def test_a_malformed_or_non_positive_value_is_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
    ) -> None:
        """A ceiling of 0/-1/garbage would reject every read -- never what
        an operator raising a limit meant."""
        payload = b'{"a": 1}'
        p = self._write(tmp_path, payload)
        monkeypatch.setenv("ABICHECK_SNAPSHOT_MAX_DECODED_BYTES", bad)
        monkeypatch.setenv("ABICHECK_SNAPSHOT_MAX_STORED_BYTES", bad)
        assert read_snapshot_bytes(p) == payload
