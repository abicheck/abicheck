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

"""``scan --abi3``'s dry-run precondition check (CLI cleanup phase two, PR 5
follow-up), coordinating scan behaviour for the dry-run preview -- ADR-061
routes that to ``workflows/``, not the ``frontends/`` renderers themselves,
which may not import ``python_ext`` (``extract``) directly.

:func:`apply_abi3_dry_run_check`/:func:`apply_abi3_dry_run_check_set` are the
one place ``scan --dry-run``/``scan --artifact-set --dry-run`` validate
``--abi3`` against the same recognition the real run's
:func:`~abicheck.scan_engine._run_abi3_audit` precondition checks, so a
preview cannot drift from it. The candidate-resolution logic itself
(binary-container recognition, then a serialized-snapshot fallback) lives in
:mod:`abicheck.scan_abi3_resolve` -- a *flat* ``workflows``-legacy module,
not this migrated package -- because it needs ``serialization.
load_snapshot``, which has no ADR-061 layer classification of its own; a
module physically under this migrated ``workflows/`` package may not import
an unclassified first-party module (``scripts/check_architecture.py``'s
``unclassified-import`` check), while a flat legacy-paths sibling can. See
that module's own docstring for the full reasoning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..scan_abi3_resolve import abi3_non_extension_members, abi3_single_binary_blocker


def apply_abi3_dry_run_check(
    result: Any, artifact: Path, abi3_floor: tuple[int, int] | None
) -> None:
    """Validate ``--abi3`` for a single-binary ``scan --dry-run`` preview,
    mutating *result* (a :class:`~abicheck.dry_run.DryRunResult`) with a
    blocker or an informational line. No-op when *abi3_floor* is ``None``."""
    if abi3_floor is None:
        return
    blocker = abi3_single_binary_blocker(artifact, abi3_floor)
    if blocker:
        result.block(blocker)
    else:
        result.add(
            "Consumer/contract scoping",
            f"--abi3 {abi3_floor[0]}.{abi3_floor[1]} stable-ABI audit: will run",
        )


def apply_abi3_dry_run_check_set(
    result: Any, members: list[tuple[str, Path]], abi3_floor: tuple[int, int] | None
) -> None:
    """The ``--artifact-set --dry-run`` sibling of
    :func:`apply_abi3_dry_run_check`: a non-extension member doesn't abort
    the real set-scan (``service_scan.run_scan_set``'s per-member
    ``_EvidenceContractError`` handling), so this lists every affected
    member rather than a single opaque blocker."""
    if abi3_floor is None:
        return
    bad = abi3_non_extension_members(members)
    if bad:
        result.block(
            f"--abi3 {abi3_floor[0]}.{abi3_floor[1]} was given but "
            f"{len(bad)} of {len(members)} member(s) are not recognisable "
            "CPython extension modules (no PyInit_* export and no CPython "
            "C-API imports): "
            + ", ".join(f"{n} ({p})" for n, p in bad)
            + ". Each such member's own scan would report "
            "EVIDENCE_CONTRACT_ERROR (exit 7)."
        )
    else:
        result.add(
            "Consumer/contract scoping",
            f"--abi3 {abi3_floor[0]}.{abi3_floor[1]} stable-ABI audit: "
            f"will run for all {len(members)} member(s)",
        )


__all__ = [
    "apply_abi3_dry_run_check",
    "apply_abi3_dry_run_check_set",
]
