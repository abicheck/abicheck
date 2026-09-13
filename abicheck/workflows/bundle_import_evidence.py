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

"""Is a bundle-internal import accounted for, and on what evidence?

One place answers the three questions the unresolved-import detectors share,
and the answers are deliberately *not* interchangeable:

* **Did a version-compatible sibling provide this symbol in OLD?** That, and
  only that, licenses ``bundle_intra_dep_removed`` -- a ``BREAKING`` kind
  whose description asserts that runtime load *will* fail. It stays with the
  detector, which is the only caller that needs it.
* **Did OLD carry this same import at all?**
  (:func:`import_existed_in_old`.) Evidence of *externality*: a release that
  shipped the same unresolved import loaded, so something outside the bundle
  provides it.
* **Do this consumer's outward ``DT_NEEDED`` edges all resolve to system
  providers?** (:func:`extra_needed_all_system`.)

:func:`classify_unresolved_import` is the one decision built out of them,
and it exists because those three were previously conflated at the call
site: the outward-edge answer is *vacuously true* for a consumer with zero
``DT_NEEDED`` (Intel MKL's shape -- every ``libmkl_*.so`` carries none), and
reading that as evidence *against* an import being internal is what made 293
``fflush``/``sincos``/``MPI_Finalize`` imports read as removals of a release
that removed nothing. Reading it as evidence that a *new* import is fine is
the mirror-image error, and drops a vendor-symbol typo that fails at load
time. Neither is recoverable from the other's answer, so both are asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..bundle_detector_heuristics import (
    DEFAULT_SYSTEM_SYMBOLS,
    _looks_system,
    _looks_system_symbol,
)
from ..bundle_models import BundleFinding
from ..bundle_soname import soname_matches_providers
from ..model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ..bundle_models import BundleSnapshot, ConsumerEntry, ResolutionGraph


def import_existed_in_old(
    consumer: ConsumerEntry,
    symbol: str,
    old: BundleSnapshot,
) -> bool:
    """Did OLD carry this same (library, symbol, version) import?

    The removal precondition ``ever_provided_in_bundle`` answers whether a
    *bundle sibling* used to satisfy an import. This answers the separate,
    weaker question its callers also need: whether the import *existed at
    all* before this release.

    The two are independent, and conflating them is how a newly introduced
    unresolved import gets dropped. "The import shipped unresolved before"
    is real evidence that something outside the bundle provides it -- the
    release before this one loaded. A *new* import has no such history, so
    outward ``DT_NEEDED`` edges (vacuously satisfied for a zero-``DT_NEEDED``
    consumer) prove nothing about it, and suppressing it on that basis would
    let a vendor-symbol typo in a new library produce a clean bundle result.

    Matched on the library *and* the exact required symbol version: an import
    that moved from ``sym@V1`` to ``sym@V2`` is a new requirement, and OLD
    having loaded the former says nothing about the latter. Matching on the
    symbol name alone would be the more permissive (finding-dropping)
    direction, which is the wrong way to fail here.
    """
    return any(
        entry.library == consumer.library and entry.version == consumer.version
        for entry in old.resolution.consumers_of(symbol)
    )


def extra_needed_all_system(
    consumer_library: str,
    resolution: ResolutionGraph,
    system_providers: set[str],
) -> bool:
    """Whether every DT_NEEDED edge of *consumer_library* that resolves
    **outside** the bundle is covered by the system-provider allow-list.

    The shared primitive behind both unresolved-import detectors'
    "this import is satisfied from outside the bundle" suppression
    (``bundle_detectors._detect_intra_dep_removed`` and its audit-mode
    sibling ``_detect_unresolved_intra_dependency``). Extracted because
    both hand-rolled it, and both hand-rolled the *same bug*.

    **The empty case is vacuously true, and that is the whole point.** Both
    call sites previously spelled this ``extra_edges and all(...)``. The
    non-empty guard was added to avoid a bare ``all([])`` -- but it made
    "declares no outward DT_NEEDED edge at all" (the strongest possible
    evidence that nothing in this bundle satisfies the import: the library
    declares no dependencies whatsoever, so the loader must resolve every
    undefined symbol it has from the global namespace its *host process*
    assembles) indistinguishable from "declares an outward edge that is not
    on the allow-list" (real evidence against). Intel MKL is the case that
    exposed it: every ``libmkl_*.so`` carries zero DT_NEEDED, so all 293 of
    its ``fflush``/``sincos``/``MPI_Finalize`` imports fell through to the
    symbol-name allow-list and were reported as *removed intra-bundle
    dependencies* of a release that had removed nothing.

    Note this answers only the **outward-edge** question. Neither caller may
    use it alone: a symbol a *sibling* used to provide can be dropped by the
    same refactor that left the consumer needing libc, and this function
    cannot see that. ``_detect_intra_dep_removed`` pairs it with the OLD
    side's own provider evidence; ``_detect_unresolved_intra_dependency``,
    which has no OLD side, pairs it with a ``not intra_needed`` requirement.

    And the two callers deliberately differ on the empty case, which is why
    this function states it rather than deciding it for them. The diff-driven
    detector takes the vacuous-true reading above, because it has separately
    established that no sibling ever provided the symbol -- so there is
    nothing this release could have removed. Audit mode keeps its own
    ``extra_edges and ...`` requirement on top: with no OLD side, "this
    consumer declares no dependencies" is not evidence that its import was
    ever satisfied, so a zero-DT_NEEDED consumer's unresolved import stays
    reported at ``COMPATIBLE_WITH_RISK``. Sharing the *coverage* rule while
    each caller supplies its own emptiness policy is the point of the split.
    """
    extra = resolution.extra_needed.get(consumer_library, [])
    return all(
        soname_matches_providers(e, system_providers) or _looks_system(e) for e in extra
    )


def classify_unresolved_import(
    consumer: ConsumerEntry,
    symbol: str,
    old: BundleSnapshot,
    *,
    outward_edges_all_system: bool,
) -> BundleFinding | None:
    """The finding an unaccounted-for import deserves, or ``None`` to drop it.

    Reached only once the caller has established that *no version-compatible
    sibling ever provided* ``symbol`` to this consumer -- so nothing was
    removed, and the diff-confirmed ``BREAKING`` kind is out of scope here by
    construction. What is left is whether anything accounts for the import,
    and the answer turns on whether OLD carried it:

    * **It did.** The release before this one shipped the same unresolved
      import and loaded, which is real evidence that something outside the
      bundle provides it. Outward-edge evidence and the symbol-name
      allow-list may then drop it.
    * **It did not.** Nothing observed anywhere shows the import was ever
      satisfied. Outward ``DT_NEEDED`` edges say nothing about it -- they are
      *vacuously* all-system for a consumer that has none -- so dropping on
      that basis would let a vendor-symbol typo in a new library produce a
      clean bundle result while failing at load time. Only the symbol-name
      allow-list still applies, because a new library calling ``memcpy``
      is the ordinary case and that evidence is the name itself, independent
      of release history.

    Never returns the ``BREAKING`` kind: an import nothing in the bundle ever
    provided is at most a ``COMPATIBLE_WITH_RISK`` deployment fact.
    """
    existed_in_old = import_existed_in_old(consumer, symbol, old)
    if existed_in_old and outward_edges_all_system:
        return None
    if symbol in DEFAULT_SYSTEM_SYMBOLS or _looks_system_symbol(symbol):
        return None
    description = (
        (
            f"{consumer.library} imports {symbol}, and no library in the "
            f"bundle exports it -- in the new bundle or the old one. Not a "
            f"regression of this release: the import was already satisfied "
            f"from outside the bundle (or not at all) before it."
        )
        if existed_in_old
        else (
            f"{consumer.library} imports {symbol}, which is new in this "
            f"release and which no library in the bundle exports. Nothing "
            f"observed shows the import was ever satisfied, so it must be "
            f"provided from outside the bundle at load time."
        )
    )
    return BundleFinding(
        kind=ChangeKind.BUNDLE_UNRESOLVED_INTRA_DEPENDENCY,
        symbol=symbol,
        description=description,
        consumer_library=consumer.library,
        affected_libraries=[consumer.library],
    )
