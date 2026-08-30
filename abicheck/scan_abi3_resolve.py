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

"""Snapshot-aware ``--abi3`` candidate resolution for the ``scan --dry-run``
previews (CLI cleanup phase two, PR 5 follow-up).

A real ``scan ARTIFACT`` accepts either a binary container or a pre-dumped
(optionally gzip/zstd-compressed) JSON snapshot (ADR-059), so a dry-run
preview that only recognized container magic bytes would misreport a valid
snapshot-based ``--abi3`` scan as "not an extension" (Codex review). The
natural fallback -- ``serialization.load_snapshot`` -- already imports
``python_ext`` (for ``PythonExtMetadata``/``detect_python_extension``), so
``python_ext`` importing ``serialization`` back would form a real
two-module import cycle (AI-readiness ``import-cycle-growth``, fresh
evidence).

This module is a *flat, legacy* root module (ADR-061's ``workflows``
responsibility, via ``architecture/modules.yaml``'s ``legacy_paths`` --
deliberately not placed under the migrated ``abicheck/workflows/`` package
itself): the stricter ``unclassified-import`` check
(``scripts/check_architecture.py``) only applies to a module physically
under a layer's own migrated ``path``, and ``serialization.py`` has no
layer classification of its own. A legacy-paths-classified sibling can
depend on it without tripping that check, the same way ``service_scan.py``
already can.

``detect_python_extension_from_binary`` is looked up as a ``python_ext``
module attribute at call time, not imported by name, so a test double
patched onto ``abicheck.python_ext.detect_python_extension_from_binary``
(the identical target the real dry-run previews already exercise) is
honored here too.
"""

from __future__ import annotations

from pathlib import Path

from . import python_ext
from .python_ext import PythonExtMetadata


def resolve_python_ext(path: Path) -> PythonExtMetadata | None:
    """Binary-container recognition first, then a serialized-snapshot
    fallback (plain, gzip, or zstd, ADR-059) -- the two real ``scan
    ARTIFACT`` input shapes a dry-run preview must recognize identically to
    the real run's own ``service.resolve_input``.

    Resolves a GNU ld linker script chain up front (Codex review, fresh
    evidence): ``detect_python_extension_from_binary`` already follows one
    to probe container bytes, but doesn't hand the resolved path back, so
    the snapshot fallback below was re-reading the original script text
    instead of the snapshot it actually points to -- a script pointing at a
    real snapshot misreported "not an extension" the identical way a script
    pointing at a real binary once did.

    Only attempts the snapshot fallback when *resolved_path* isn't itself a
    recognised binary container (Codex review, fresh evidence): a real,
    non-qualifying ELF/PE/Mach-O binary already got a definitive "not an
    extension" answer from the probe above, so re-reading it as
    ``load_snapshot``'s plain-text/JSON path would needlessly buffer the
    whole file (up to its 1 GiB safety limit) only to fail UTF-8/JSON
    validation -- wasted for every non-extension ``--artifact-set`` member.
    """
    from . import binary_utils

    resolved_path = binary_utils.resolve_linker_script_chain(Path(path))
    ext = python_ext.detect_python_extension_from_binary(resolved_path)
    if ext is not None:
        return ext
    if binary_utils.detect_binary_format(resolved_path) is not None:
        return None
    from .serialization import load_snapshot

    try:
        return load_snapshot(resolved_path).python_ext
    except Exception:
        return None


def qualifies_for_abi3(path: Path) -> bool:
    ext = resolve_python_ext(path)
    return ext is not None and ext.is_extension


def abi3_single_binary_blocker(binary: Path, abi3_floor: tuple[int, int]) -> str | None:
    """``None`` when *binary* would satisfy ``scan --abi3``'s real-run
    precondition; else the same blocker message the real run raises, for a
    single-binary ``scan --dry-run`` preview."""
    if qualifies_for_abi3(binary):
        return None
    return python_ext.abi3_precondition_message(abi3_floor, binary.name)


def abi3_non_extension_members(
    members: list[tuple[str, Path]],
) -> list[tuple[str, Path]]:
    """The ``(name, path)`` entries of *members* that would fail ``scan
    --abi3``'s real-run precondition, for a ``--artifact-set`` dry-run
    preview -- empty when every member qualifies."""
    return [(name, path) for name, path in members if not qualifies_for_abi3(path)]


__all__ = [
    "resolve_python_ext",
    "qualifies_for_abi3",
    "abi3_single_binary_blocker",
    "abi3_non_extension_members",
]
