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

"""Finding-identity helpers a frontend needs for its own report rows, plus
the probe-matrix JSON I/O `abicheck/workflows/AGENTS.md`'s entry-point table
promises for this module.

ADR-061 Phase 4. ``finding_identity`` belongs to the ``compare`` ring, which a
frontend may not import; ``missing_contract_finding``/
``report_canonical_finding_id``/``report_finding_id`` answer "what stable id
does this finding carry" for a report row a CLI is about to print, and
``diff_matrix`` is re-exported unchanged. Those four are re-export only.

``matrix_snapshot_to_json``/``matrix_snapshot_from_dict``/
``write_matrix_snapshot``/``load_matrix_snapshot`` are NOT re-exports:
this is their real implementation (ADR-061 gap E). ``probe_harness.py``
(``compare``, ``may_import: [model]``) defines ``ProbeResult``/
``MatrixSnapshot`` as pure value objects, but converting one to/from JSON
needs ``AbiSnapshot``'s own ``storage``-owned codec
(``serialization.snapshot_to_dict``/``snapshot_from_dict``) — a persistence
operation ``compare`` must not own (root `AGENTS.md`'s D1 table). This
module is `workflows`, which may import both `compare` (for the dataclasses)
and ``abicheck.serialization`` (a documented ``public_root_surfaces``
compatibility facade), so it is where that conversion belongs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..diff_build_config import diff_matrix
from ..finding_identity import (
    missing_contract_finding,
    report_canonical_finding_id,
    report_finding_id,
)
from ..probe_harness import MatrixSnapshot, ProbeResult
from ..serialization import snapshot_from_dict, snapshot_to_dict


def probe_result_to_dict(result: ProbeResult) -> dict[str, Any]:
    """JSON-safe projection of one ``ProbeResult``, snapshot included."""
    out: dict[str, Any] = {
        "configuration_id": result.configuration_id,
        "probe_id": result.probe_id,
        "object_path": result.object_path,
        "error": result.error,
    }
    if result.snapshot is not None:
        out["snapshot"] = snapshot_to_dict(result.snapshot)
    return out


def _probe_result_from_dict(data: dict[str, Any]) -> ProbeResult:
    snap = None
    if data.get("snapshot") is not None:
        snap = snapshot_from_dict(data["snapshot"])
    return ProbeResult(
        configuration_id=data["configuration_id"],
        probe_id=data["probe_id"],
        object_path=data.get("object_path"),
        snapshot=snap,
        error=data.get("error"),
    )


def matrix_snapshot_to_json(matrix: MatrixSnapshot) -> str:
    """Serialize a whole ``MatrixSnapshot`` (every result's snapshot
    included) to an indented JSON string."""
    from .evidence_transport import PROBE_MATRIX_SCHEMA

    return json.dumps({
        # Self-describing from plan Phase 7n on: `--build-info` now carries
        # probe observations *and* compile context, and routes on the
        # document rather than on a filename. A snapshot written before this
        # tag existed stays classifiable through its required-key contract
        # (`evidence_transport.is_probe_matrix_document`), and
        # `matrix_snapshot_from_dict` ignores the key, so both directions of
        # the round trip are unaffected.
        "schema": PROBE_MATRIX_SCHEMA,
        "library": matrix.library,
        "version": matrix.version,
        "spec_name": matrix.spec_name,
        "cxx_stds": matrix.cxx_stds,
        "defaults": matrix.defaults,
        "results": [probe_result_to_dict(r) for r in matrix.results],
    }, indent=2)


def matrix_snapshot_from_dict(data: dict[str, Any]) -> MatrixSnapshot:
    """Inverse of ``matrix_snapshot_to_json`` (post-``json.loads``)."""
    return MatrixSnapshot(
        library=data["library"],
        version=data["version"],
        spec_name=data["spec_name"],
        cxx_stds=data.get("cxx_stds", {}),
        defaults=data.get("defaults", {}),
        results=[_probe_result_from_dict(r) for r in data.get("results", [])],
    )


def write_matrix_snapshot(matrix: MatrixSnapshot, path: str | Path) -> None:
    Path(path).write_text(matrix_snapshot_to_json(matrix), encoding="utf-8")


def load_matrix_snapshot(path: str | Path) -> MatrixSnapshot:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return matrix_snapshot_from_dict(data)


__all__ = [
    "diff_matrix",
    "load_matrix_snapshot",
    "matrix_snapshot_from_dict",
    "matrix_snapshot_to_json",
    "missing_contract_finding",
    "probe_result_to_dict",
    "report_canonical_finding_id",
    "report_finding_id",
    "write_matrix_snapshot",
]
