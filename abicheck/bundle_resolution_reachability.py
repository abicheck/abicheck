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

"""``DT_NEEDED``-reachability primitive for the bundle layer (ADR-023).

Extracted from :mod:`abicheck.bundle` (Codex review, G38 Phase 4) so a
second bundle-level module could share it without importing ``bundle.py``
itself: :mod:`abicheck.bundle_signature_evidence` is a deliberate leaf
module with respect to ``bundle.py`` (see that module's own docstring for
why -- ``bundle.py`` must be free to import it, never the reverse), so a
function it needs that previously lived only in ``bundle.py`` had no home
either module could reach without breaking that direction. This tiny leaf
module -- one function, no dependency beyond :mod:`abicheck.bundle_models`
-- is that home; both ``bundle.py`` and ``bundle_signature_evidence.py``
import from it, and ``bundle.py`` re-imports it under its original private
name (``_reachable_intra_libraries``) so none of its own call sites needed
to change.
"""

from __future__ import annotations

from .bundle_models import BundleSnapshot


def reachable_intra_libraries(snapshot: BundleSnapshot, root: str) -> set[str]:
    """BFS over intra-bundle ``DT_NEEDED`` edges starting at ``root``.

    Returns every library transitively reachable from ``root`` through the
    bundle's own resolution graph (i.e. what would actually be loaded when
    ``root`` is loaded) -- not including ``root`` itself. Used by
    ``bundle._detect_unresolved_intra_dependency``/``_detect_intra_dep_
    removed`` and by :func:`abicheck.bundle_signature_evidence.
    find_unverified_signature_findings` so a symbol is only considered
    resolved (or previously reached) by a provider the consumer can
    actually reach, not merely one present somewhere else in the snapshot.
    """
    seen: set[str] = set()
    queue = [root]
    while queue:
        lib = queue.pop()
        for soname in snapshot.resolution.intra_needed.get(lib, []):
            # Resolve via the same soname_to_name map
            # _compute_resolution_graph() used to classify this edge as
            # intra in the first place, so the two agree even for a library
            # discovered via a differently-named symlink alias.
            target = snapshot.resolution.soname_to_name.get(soname)
            if target is None or target == lib or target in seen:
                continue
            seen.add(target)
            queue.append(target)
    return seen
