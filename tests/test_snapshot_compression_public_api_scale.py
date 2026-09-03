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
1500-line AI-readiness soft cap) rather than grown in place -- like that
module, reuses the shared, cached production-scale fixtures in the leaf
helper module ``_production_scale_snapshot.py``
(``graph_heavy_snapshot_at_scale()``/``graph_heavy_snapshot_at_scale_json_
bytes()``/``graph_heavy_snapshot_at_scale_compressed_bytes()``,
``functools``-cached around ``graph_heavy_snapshot``, the underlying
fixture builder), the same >8 MiB serialized snapshot
``test_zstd_round_trip_at_production_scale_and_level``/
``test_gzip_round_trip_at_production_scale`` already use (the threshold
where zstd's auto-selected window actually reproduces the real oneDAL-scale
ADR-059 §12 regression, rather than collapsing to the content size the way a
toy-scale fixture would) -- cached and shared rather than each test/
parametrize case here independently rebuilding, re-serializing, and
re-compressing its own ~8600-entry copy, since the content is identical and
pure/deterministic either way (see that module's own docstring).

Those two existing tests are real and already go through the true public
entry point *one layer down* -- ``abicheck.snapshot_io.write_snapshot_bytes``/
``read_snapshot_bytes``, exactly what ``dump``/``compare`` call internally --
but stop there. The registered gap this file closes
(``tests/regressions/manifest.py``'s ``storage.third_party_contract_at_scale``
``BugClass``) is one layer higher still. Two genuinely distinct public
surfaces, each earning its own ``public_surfaces`` tag in that registry entry
(see that field's own docstring: ``"python-api"`` requires a call through
``abicheck.service``, not merely ``abicheck.serialization``):

- ``abicheck.serialization.save_snapshot``/``load_snapshot`` -- the plain
  Python compatibility surface a caller of this library uses directly.
- ``abicheck.service.resolve_input`` -- the actual ``abicheck.service`` typed
  entry point ``dump``'s/``compare``'s implicit-dump operand call, which
  dispatches a JSON-format input to ``load_snapshot`` internally
  (``sniff_text_format`` -> ``load_snapshot``, see ``service.py``).
- The real ``compare`` CLI command, which itself calls ``resolve_input``.

A bug reintroduced above ``write_snapshot_bytes`` itself -- e.g.
``save_snapshot``'s compression-from-suffix resolution
(``resolve_write_compression``), or ``service.py``'s ``sniff_text_format``/
``resolve_input`` dispatch -- would not be caught by either of the two
existing lower-level tests, since neither is ever routed through those
functions.

Every round trip here proves *lossless, correct* decoding, not merely "it
didn't raise": the two Python-level tests assert full dataclass equality
against the original in-memory snapshot, and the CLI test diffs two
genuinely distinct (not self-identical) production-scale operands and
asserts the CLI's own JSON output names the exact one expected finding --
comparing a compressed file against itself would pass even if the CLI
silently returned the same truncated/default snapshot for both operands
(Codex review, PR #911), which this design rules out.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from _production_scale_snapshot import (
    graph_heavy_snapshot_at_scale,
    graph_heavy_snapshot_at_scale_compressed_bytes,
    graph_heavy_snapshot_at_scale_json_bytes,
)
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import Function, Visibility
from abicheck.serialization import load_snapshot, save_snapshot
from abicheck.service import resolve_input
from abicheck.snapshot_io import (
    ZSTD_LEVEL_BASELINE,
    SnapshotCompression,
    detect_snapshot_compression,
)

_MARKER_NAME = "brand_new_marker_fn"
_MARKER_MANGLED = "_Z20brand_new_marker_fnv"


def _write_cached_production_scale_file(path, suffix: str) -> None:
    """Writes the shared cached compressed bytes for *suffix*'s algorithm
    directly to *path* -- byte-identical to what a real
    ``save_snapshot(graph_heavy_snapshot_at_scale(), path)`` call would
    produce (see ``graph_heavy_snapshot_at_scale_compressed_bytes``'s own
    docstring for why that's a safe substitution), without repeating the
    compression work ``test_save_load_snapshot_round_trips_at_production_
    scale`` already proves end-to-end via the real ``save_snapshot`` entry
    point. Use this only for a file whose content is the unmodified shared
    fixture -- a caller with genuinely different content (the CLI test's
    ``new_snap``) must still go through a real ``save_snapshot`` call."""
    compression = _compression_for_suffix(suffix)
    zstd_level = (
        ZSTD_LEVEL_BASELINE if compression is SnapshotCompression.ZSTD else None
    )
    path.write_bytes(
        graph_heavy_snapshot_at_scale_compressed_bytes(compression, zstd_level)
    )


def _production_scale_size() -> int:
    """The shared, cached production-scale content's serialized length --
    every test below drives a different public entry point over the *same*
    cached ~8600-entry graph (`_production_scale_snapshot.py`'s
    `graph_heavy_snapshot_at_scale()`/`graph_heavy_snapshot_at_scale_
    json_bytes()`) rather than each independently rebuilding and
    re-serializing its own copy, so this is the >8 MiB threshold every test
    here relies on (the point past which zstd's auto-selected window stops
    collapsing to the content size and actually reproduces the real
    ADR-059 §12 shape) -- computed lazily, on first call from an actual
    (`slow`-marked) test body, not at collection/import time, so a run that
    never executes these tests never pays for it."""
    return len(graph_heavy_snapshot_at_scale_json_bytes())


def _assert_realistic_zstd_window(zstandard, p) -> None:
    """Same auto-selected-window sanity check the sibling module's own
    zstd production-scale test performs, confirming this really is the
    realistic (not toy-collapsed) frame shape."""
    frame = zstandard.get_frame_parameters(p.read_bytes())
    assert frame.window_size == 8 * 1024 * 1024


def _compression_for_suffix(suffix: str) -> SnapshotCompression:
    return (
        SnapshotCompression.ZSTD
        if suffix.endswith(".zst")
        else SnapshotCompression.GZIP
    )


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
    public Python compatibility surface -- round-trip a real, production-
    scale snapshot losslessly, for both supported compression algorithms.

    ``suffix`` alone selects the algorithm via ``save_snapshot``'s default
    ``compression="auto"`` (``resolve_write_compression``, ADR-059 Section
    3.5) -- exactly how a real caller picks compression, by filename, not by
    passing an explicit ``SnapshotCompression`` the way the sibling
    module's lower-level tests do.

    ``zstandard`` is only ever imported for the zstd case -- the gzip case
    must not be skipped just because that optional dependency is absent
    (CodeRabbit review, PR #911).
    """
    expected_compression = _compression_for_suffix(suffix)
    zstandard = (
        pytest.importorskip("zstandard")
        if expected_compression is SnapshotCompression.ZSTD
        else None
    )

    original = graph_heavy_snapshot_at_scale()
    assert _production_scale_size() > 8 * 1024 * 1024

    p = tmp_path / f"production_scale{suffix}"
    save_snapshot(original, p)

    assert detect_snapshot_compression(p) is expected_compression
    if zstandard is not None:
        _assert_realistic_zstd_window(zstandard, p)

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
def test_service_resolve_input_round_trips_a_compressed_snapshot_at_production_scale(
    tmp_path, suffix
):
    """``abicheck.service.resolve_input`` -- the documented ``python-api``
    entry point (``AGENTS.md``'s ``public_surfaces`` convention requires a
    call through ``abicheck.service``, not only ``abicheck.serialization``)
    -- reads a production-scale, genuinely compressed snapshot file
    losslessly, via its JSON-format dispatch branch (``sniff_text_format``
    detects the compression from a bounded decoded prefix, then delegates
    to ``load_snapshot``).

    ``zstandard`` is only ever imported for the zstd case -- the gzip case
    must not be skipped just because that optional dependency is absent
    (CodeRabbit review, PR #911).

    The file itself is written via the shared cached compressed bytes
    (``_write_cached_production_scale_file``), not a fresh ``save_snapshot``
    call -- byte-identical either way (see that helper's own docstring); the
    real write path is what
    ``test_save_load_snapshot_round_trips_at_production_scale`` proves, this
    test's job is the *read* path.
    """
    is_zstd = _compression_for_suffix(suffix) is SnapshotCompression.ZSTD
    zstandard = pytest.importorskip("zstandard") if is_zstd else None

    original = graph_heavy_snapshot_at_scale()
    assert _production_scale_size() > 8 * 1024 * 1024

    p = tmp_path / f"production_scale{suffix}"
    _write_cached_production_scale_file(p, suffix)

    if zstandard is not None:
        _assert_realistic_zstd_window(zstandard, p)

    reloaded = resolve_input(p)
    assert reloaded == original


@pytest.mark.slow
@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param(".abicheck.json.zst", id="zstd"),
        pytest.param(".abicheck.json.gz", id="gzip"),
    ],
)
def test_compare_cli_diffs_compressed_snapshots_at_production_scale(tmp_path, suffix):
    """The real ``compare`` CLI command -- ``service.resolve_input``'s
    JSON-format dispatch (``sniff_text_format`` -> ``load_snapshot``) --
    reads two production-scale, genuinely compressed snapshot file operands
    losslessly and correctly identifies the *one* real difference between
    them, with no compiler/castxml/binary toolchain involved (both operands
    are plain snapshot files, the established pattern in
    ``tests/test_build_source_cli.py::test_compare_without_evidence_is_
    unchanged``).

    Deliberately does NOT compare a compressed file against itself: doing so
    would pass even if the CLI path silently decoded both operands to the
    same truncated or default snapshot, which would not support the claim
    that the CLI reads the real content losslessly (Codex review, PR #911).
    Instead, ``new`` is ``old`` plus exactly one added public function
    (``_MARKER_MANGLED``) buried among ~8600 other functions/types -- the
    CLI's own JSON output must name that one finding and no other, which
    only holds if the full, real (not truncated/replaced) content of both
    multi-megabyte compressed operands was decoded and diffed correctly.

    ``zstandard`` is only ever required for the zstd case -- the gzip case
    must not be skipped just because that optional dependency is absent
    (CodeRabbit review, PR #911).

    ``old_p`` is written from the shared cached compressed bytes rather than
    a fresh ``save_snapshot`` call (see ``_write_cached_production_scale_
    file``'s docstring); ``new_p`` -- genuinely distinct content -- still
    goes through a real ``save_snapshot`` call, same as before.
    """
    if _compression_for_suffix(suffix) is SnapshotCompression.ZSTD:
        pytest.importorskip("zstandard")

    old_snap = graph_heavy_snapshot_at_scale()
    assert _production_scale_size() > 8 * 1024 * 1024

    # dataclasses.replace(), not `new_snap.functions.append(...)`: `old_snap`
    # is the shared, process-wide cached fixture (see its own docstring) --
    # mutating it in place would corrupt every other test in this worker
    # process that reads it afterwards. This derives a distinct AbiSnapshot
    # with a new `functions` list (old_snap's own ~8600 Function objects,
    # reused by reference since they're never mutated, plus the one marker)
    # without rebuilding or deep-copying the underlying graph.
    new_snap = dataclasses.replace(
        old_snap,
        functions=[
            *old_snap.functions,
            Function(
                name=_MARKER_NAME,
                mangled=_MARKER_MANGLED,
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
    )

    old_p = tmp_path / f"old{suffix}"
    new_p = tmp_path / f"new{suffix}"
    # `old_p` is the unmodified shared fixture's content -- write the cached
    # compressed bytes (see `_write_cached_production_scale_file`'s
    # docstring) rather than recompressing it a third time. `new_p` is
    # genuinely distinct content (one added function), so it still needs a
    # real compression -- via the real `save_snapshot` entry point, same as
    # before.
    _write_cached_production_scale_file(old_p, suffix)
    save_snapshot(new_snap, new_p)

    result = CliRunner().invoke(
        main, ["compare", str(old_p), str(new_p), "--format", "json"]
    )
    assert result.exit_code in (0, 2, 4), result.output
    payload = json.loads(result.stdout)
    changes = payload["changes"]
    assert [(c["kind"], c["symbol"]) for c in changes] == [
        ("func_added", _MARKER_MANGLED)
    ]
