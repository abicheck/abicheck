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

"""CLI cleanup phase two, "PR B" (effective-config receipt parity), first
slice: giving the directory/package release fan-out one place to fold its
already-resolved evaluation configuration onto each library it compares.

New code under ADR-061 goes to its target responsibility package rather than
extending a flat, ``debt.yaml``-tracked legacy module -- this coordinates a
per-library *release* comparison result, which the root ``AGENTS.md``
ownership table places under ``workflows/`` ("Coordinate dump, compare, scan,
release, aggregate, project, or dependency behavior"). ``abicheck.
cli_compare_release``/``cli_compare_release_helpers`` (the legacy flat
modules this replaces a would-be extension of) remain the release fan-out's
own orchestration; they call this module rather than growing further
themselves.

Deliberately untyped beyond ``Any`` for both parameters: a real type import
(``checker_types.DiffResult``, ``pack_application.PackApplication``) would
pull two more flat, not-yet-ADR-061-classified legacy modules into this
package's dependency graph purely for an annotation, and this function only
ever does simple attribute access on either object -- no method call, no
construction. See ``abicheck/workflows/AGENTS.md`` for the permitted-import
list this avoids widening.
"""

from __future__ import annotations

from typing import Any


def stamp_release_evaluation_config(diff: Any, pack_application: Any) -> None:
    """Stamp *diff* with the release's already-resolved
    ``CompatibilityEvaluationConfig``, the way ``cli_compare_receipt.
    record_resolved_config`` does for single-pair ``compare``.

    Closes the "release per-library digest loses pack identity" gap
    documented in ``effective_config_digest``'s own module docstring: without
    this, the release fan-out never installed a rich-tier config onto each
    library's own ``DiffResult``, so a release run under two different pack
    *revisions* that happen to project the same current policy/severity
    assignments produced the identical per-library digest -- even though the
    rich tier's whole point is real, versioned pack identities.

    *pack_application* is resolved once for the whole release (not per
    library, unlike a single-pair ``compare``'s own ``PackApplication`` --
    see ``cli_compare_receipt.resolve_release_pack_application``'s own
    docstring for why), so every library in the release ends up sharing the
    identical ``resolved_config`` object, which is exactly what makes
    ``effective_config_digest``'s rich tier sensitive to *which pack
    revision* ran rather than only to its current field assignments.

    A no-op — *diff*'s ``evaluation_config`` stays ``None``, the pre-existing
    baseline-tier behavior — whenever no ``--pack`` was given at all
    (*pack_application* is ``None``) or *pack_application* was hand-built
    without going through the ``pack_application()`` factory (its
    ``resolved_config`` stays ``None`` too), matching ``record_resolved_
    config``'s own contract.
    """
    if pack_application is not None and pack_application.resolved_config is not None:
        diff.evaluation_config = pack_application.resolved_config
