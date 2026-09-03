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

"""Shared, cached ``n=8600`` (~20 MB serialized) production-scale
``AbiSnapshot`` fixture -- a leaf, non-``test_`` helper module (tests/
CLAUDE.md's own convention; see ``_strict_process.py``, ``_workflow_exec.py``,
``_canonical_lane.py`` for the established pattern), split out rather than
grown into ``test_snapshot_compression.py`` a second time: that module was
already at exactly 1499 lines -- one line under the AI-readiness ``file-size``
soft-limit WARN -- the identical reason
``test_snapshot_compression_public_api_scale.py`` was split out of it in the
first place (see that module's own docstring).

Used by ``test_snapshot_compression.py``'s own two production-scale tests
(``test_zstd_round_trip_at_production_scale_and_level``/
``test_gzip_round_trip_at_production_scale``) and by
``test_snapshot_compression_public_api_scale.py``'s three -- every one of
these tests drives a *different* real public entry point
(``write_snapshot_bytes``, ``save_snapshot``, ``resolve_input``, the
``compare`` CLI) over the identical, deterministic content, so building/
serializing/compressing it fresh per test/parametrize case was pure repeated
cost with no effect on what any of them actually verifies -- see each
function's own docstring for exactly what is cached and why sharing it is
safe.
"""

from __future__ import annotations

import functools
import json

from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.serialization import snapshot_to_dict
from abicheck.snapshot_io import (
    ZSTD_LEVEL_BASELINE,
    SnapshotCompression,
    encode_snapshot_bytes,
)


def graph_heavy_snapshot(n: int = 200) -> AbiSnapshot:
    """A snapshot with repeated, JSON-compressible content -- a small
    stand-in for the real ~57 MB L5 header graph section a large library
    carries (`AGENTS.md`'s daal/oneapi::dal acceptance numbers), used to
    validate that repeated content compresses well without needing a
    multi-hundred-MB fixture in the test suite. Also the basis for
    :func:`graph_heavy_snapshot_at_scale` (``n=8600``) below -- moved here,
    alongside its production-scale derivatives, from
    ``test_snapshot_compression.py`` (which still imports it back under its
    original ``_graph_heavy_snapshot`` name for its own small-``n`` uses) so
    this leaf module has no dependency on either test module and both can
    import from it without a cycle."""
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


@functools.lru_cache(maxsize=1)
def graph_heavy_snapshot_at_scale() -> AbiSnapshot:
    """The shared ``n=8600`` (~20 MB serialized) production-scale fixture --
    cached (``functools.lru_cache``, not a pytest fixture, so every existing
    call site keeps its current signature) because it is pure and
    deterministic (``graph_heavy_snapshot`` takes no randomness), yet was
    previously rebuilt from scratch by up to ten separate test/parametrize
    cases across the two consumer modules above -- each an independent
    ~8600-``Function`` dataclass-graph construction, purely to feed a
    different public entry point with otherwise-identical content. That
    repetition inflated CI wall time (this fixture's own construction, not
    the compression each caller genuinely needs to exercise) without adding
    any additional coverage -- building it once per worker process and
    sharing it read-only cuts the redundancy without changing what any test
    actually verifies.

    Built once per pytest worker process (module-level ``lru_cache``, so
    xdist workers each pay for one build, not a shared cross-process cache).
    **Never mutate the returned object** -- a caller that needs a variant
    (e.g. one more function) must derive a new ``AbiSnapshot`` via
    ``dataclasses.replace()`` rather than mutating in place, since every
    other caller in the same worker process holds the identical reference.
    """
    return graph_heavy_snapshot(n=8600)


@functools.lru_cache(maxsize=1)
def graph_heavy_snapshot_at_scale_json_bytes() -> bytes:
    """The serialized JSON bytes of :func:`graph_heavy_snapshot_at_scale`,
    cached the same way and for the same reason -- ``snapshot_to_dict`` +
    ``json.dumps`` over an 8600-entry graph is itself real, measurable work
    (not just the compression downstream of it), and every caller that needs
    these bytes needs the *identical* bytes, so computing them once and
    sharing is lossless. Asserts the >8 MiB "past zstd's single-segment
    window collapse" premise here, once, rather than in every caller that
    used to re-derive these bytes just to check it."""
    encoded = json.dumps(snapshot_to_dict(graph_heavy_snapshot_at_scale())).encode()
    assert len(encoded) > 8 * 1024 * 1024  # past the single-segment collapse point
    return encoded


@functools.cache
def graph_heavy_snapshot_at_scale_compressed_bytes(
    compression: SnapshotCompression, zstd_level: int | None = None
) -> bytes:
    """The actual compressed bytes ``write_snapshot_bytes`` (and therefore
    ``save_snapshot``, which calls it) produces for
    :func:`graph_heavy_snapshot_at_scale_json_bytes` under *compression* --
    cached for the same reason and the same way as the two fixtures above.
    Compression (zstd level 19 above all -- see ``ZSTD_LEVEL_BASELINE``'s own
    comment) is the single dominant real cost of every production-scale test
    in both consumer modules, and it is deterministic: fixed level, no
    checksum, no embedded timestamp/filename (ADR-059's "deterministic
    compression settings"), confirmed directly -- two independent
    compressions of identical bytes at the identical level produce
    byte-identical output.

    A caller that only needs *a* real, correctly-compressed file on disk to
    exercise a *read* path (``resolve_input``, the ``compare`` CLI) doesn't
    need to independently re-pay for regenerating it --
    ``test_save_load_snapshot_round_trips_at_production_scale``/
    ``test_zstd_round_trip_at_production_scale_and_level``/
    ``test_gzip_round_trip_at_production_scale`` are what prove the *write*
    path (``save_snapshot``/``write_snapshot_bytes``) itself, and still call
    it for real, unshortcut. A caller writes the returned bytes to its own
    path with a plain ``Path.write_bytes()`` -- byte-identical to what a
    fresh ``save_snapshot``/``write_snapshot_bytes`` call to that same path
    would have produced."""
    level = zstd_level if zstd_level is not None else ZSTD_LEVEL_BASELINE
    return encode_snapshot_bytes(
        graph_heavy_snapshot_at_scale_json_bytes(), compression, zstd_level=level
    )
