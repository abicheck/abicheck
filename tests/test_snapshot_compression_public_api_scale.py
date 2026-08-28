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

"""Public-API/CLI-level compression round-trip coverage at production scale
(bug-class-regression-testing.md Phase 7).

Split out of ``test_snapshot_compression.py`` (already at that file's own
1500-line AI-readiness soft cap) rather than grown in place -- reuses
``_graph_heavy_snapshot`` from there, the same fixture builder
``test_zstd_round_trip_at_production_scale_and_level``/
``test_gzip_round_trip_at_production_scale`` already use to produce a real,
>8 MiB serialized snapshot (the threshold where zstd's auto-selected window
actually reproduces the real oneDAL-scale ADR-059 §12 regression, rather
than collapsing to the content size the way a toy-scale fixture would).

Those two existing tests are real and already go through the true public
entry point *one layer down* -- ``abicheck.snapshot_io.write_snapshot_bytes``/
``read_snapshot_bytes``, exactly what ``dump``/``compare`` call internally --
but stop there. The registered gap this file closes
(``tests/regressions/manifest.py``'s ``storage.third_party_contract_at_scale``
``BugClass``) is one layer higher still: no test previously went through
``abicheck.serialization.save_snapshot``/``load_snapshot`` (the public
Python API a caller of this library actually uses) or through the real
``compare`` CLI command, at this same realistic scale, for either supported
algorithm. A bug reintroduced above ``write_snapshot_bytes`` itself --
e.g. ``save_snapshot``'s compression-from-suffix resolution
(``resolve_write_compression``), or ``service.py``'s ``sniff_text_format``/
``resolve_input`` dispatch a live ``compare`` invocation goes through --
would not be caught by either existing test, since neither one is ever
routed through those functions.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from test_snapshot_compression import _graph_heavy_snapshot

from abicheck.cli import main
from abicheck.serialization import load_snapshot, save_snapshot, snapshot_to_dict
from abicheck.snapshot_io import SnapshotCompression, detect_snapshot_compression


def _production_scale_bytes(snap) -> int:
    """Sanity-checks and returns the serialized size, matching the >8 MiB
    threshold the sibling module's own production-scale tests assert --
    the point past which zstd's auto-selected window stops collapsing to
    the content size and actually reproduces the real ADR-059 §12 shape."""
    size = len(json.dumps(snapshot_to_dict(snap)).encode())
    assert size > 8 * 1024 * 1024
    return size


@pytest.mark.slow
@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(".abicheck.json.zst", id="zstd"),
        pytest.param(".abicheck.json.gz", id="gzip"),
    ],
)
def test_save_load_snapshot_round_trips_at_production_scale(tmp_path, suffix):
    """``abicheck.serialization.save_snapshot``/``load_snapshot`` -- the
    public Python API layer ``dump``'s/``compare``'s implicit-dump operand
    actually call, one layer above ``write_snapshot_bytes``/
    ``read_snapshot_bytes`` -- round-trip a real, production-scale snapshot
    losslessly, for both supported compression algorithms.

    ``suffix`` alone selects the algorithm via ``save_snapshot``'s default
    ``compression="auto"`` (``resolve_write_compression``, ADR-059 Section
    3.5) -- exactly how a real caller picks compression, by filename, not by
    passing an explicit ``SnapshotCompression`` the way the sibling
    module's lower-level tests do.
    """
    zstandard = pytest.importorskip("zstandard")

    original = _graph_heavy_snapshot(n=8600)
    _production_scale_bytes(original)

    p = tmp_path / f"production_scale{suffix}"
    save_snapshot(original, p)

    expected_compression = (
        SnapshotCompression.ZSTD
        if suffix.endswith(".zst")
        else SnapshotCompression.GZIP
    )
    assert detect_snapshot_compression(p) is expected_compression
    if expected_compression is SnapshotCompression.ZSTD:
        # Same auto-selected-window sanity check the sibling module's own
        # zstd production-scale test performs, confirming this really is
        # the realistic (not toy-collapsed) frame shape.
        frame = zstandard.get_frame_parameters(p.read_bytes())
        assert frame.window_size == 8 * 1024 * 1024

    reloaded = load_snapshot(p)
    # A full dataclass round trip -- not merely "it loaded without raising".
    assert reloaded == original


@pytest.mark.slow
@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(".abicheck.json.zst", id="zstd"),
        pytest.param(".abicheck.json.gz", id="gzip"),
    ],
)
def test_compare_cli_round_trips_a_compressed_snapshot_at_production_scale(
    tmp_path, suffix
):
    """The real ``compare`` CLI command -- ``service.resolve_input``'s
    JSON-format dispatch (``sniff_text_format`` -> ``load_snapshot``, see
    ``service.py``) -- reads a production-scale, genuinely compressed
    snapshot file operand losslessly and reports it as unchanged against
    itself, with no compiler/castxml/binary toolchain involved (both
    operands are plain snapshot files, exactly the established pattern in
    ``tests/test_build_source_cli.py::test_compare_without_evidence_is_
    unchanged``).

    This is the CLI-level half of the same registered gap the sibling test
    above closes at the Python-API level -- a regression anywhere in
    ``sniff_text_format``'s bounded-prefix compression sniff, or in
    ``resolve_input``'s JSON-format branch, would show up here even if
    ``save_snapshot``/``load_snapshot`` themselves stayed correct.
    """
    pytest.importorskip("zstandard")

    snap = _graph_heavy_snapshot(n=8600)
    _production_scale_bytes(snap)

    p = tmp_path / f"production_scale{suffix}"
    save_snapshot(snap, p)

    result = CliRunner().invoke(main, ["compare", str(p), str(p)])
    assert result.exit_code == 0, result.output
