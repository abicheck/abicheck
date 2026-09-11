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

"""Reading facts back out of a snapshot document `abicheck dump` wrote.

A non-``test_`` helper module (same convention as ``_strict_process.py``).
It exists so this repository's own out-of-band snapshot readers have one
owner instead of being inlined in whichever consumer needed them first --
``validate_examples.py`` held ``embedded_present_layers`` until it grew past
``architecture/debt.yaml``'s no-growth baseline, and the responsibility, not
the line count, is what moved.

Every function here unwraps ``storage.sectioned_document``'s envelope before
indexing: ADR-062/ADR-063 Phase 8 nests what used to be top-level snapshot
fields under ``sections.<kind>.payload``, so a reader that skips the unwrap
reads ``None`` for every real ``dump`` output and cannot tell that apart from
the field genuinely being absent. Bug class
``storage.out_of_band_snapshot_reader_envelope_drift`` -- see
``tests/regressions/manifest.py`` and
``tests/test_snapshot_envelope_out_of_band_readers.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: `LayerCoverage.layer` value -> the short tag callers report it as.
_LAYER_TAGS = {"L3_build": "L3", "L4_source_abi": "L4", "L5_source_graph": "L5"}


def load_snapshot_document(snap_path: Path) -> dict[str, Any]:
    """*snap_path*'s JSON, flattened out of the sectioned envelope.

    ``{}`` for an unreadable or non-JSON file, so a caller that treats
    "nothing found" as a legitimate answer keeps that behaviour. A flat
    document (an older ``.abi.json``, or a hand-written fixture) passes
    through untouched, which is what keeps unit fixtures usable here.
    """
    from abicheck.storage.sectioned_document import (
        from_sectioned_document,
        is_sectioned_document,
    )

    try:
        data = json.loads(snap_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return from_sectioned_document(data) if is_sectioned_document(data) else data


def embedded_present_layers(snap_path: Path) -> set[str]:
    """Short tags (``L3``/``L4``/``L5``) for layers the dumped snapshot's embedded
    build_source actually carries with ``present`` coverage.

    Directory/flag presence is not enough: ``dump --sources`` degrades to a
    partial/empty surface (exit 0) when the source-replay front-end is missing
    or no TU parses, so the inline opt-ins must be confirmed from the *real*
    embedded coverage rather than assumed (Codex).
    """
    data = load_snapshot_document(snap_path)
    pack = data.get("build_source")
    if not isinstance(pack, dict):
        return set()
    coverage = (pack.get("manifest") or {}).get("coverage") or []
    present: set[str] = set()
    for row in coverage:
        if not isinstance(row, dict):
            continue
        tag = _LAYER_TAGS.get(str(row.get("layer", "")))
        if tag and str(row.get("status", "")) == "present":
            present.add(tag)
    return present
