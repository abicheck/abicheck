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

"""``ChangeKind`` -- the model-owned enum, moved here from ``checker_policy.py``
(ADR-061 D9 / model-vs-policy split).

This is the co-prerequisite ADR-061's Phase 4 investigation named for
building a ``PolicyFileProtocol``: ``PolicyFile.overrides``'s key type and
``ReclassifyRule``'s ``to_verdict`` both need ``ChangeKind``, and a
`model`-owned protocol cannot import it from `checker_policy.py`, which
mixes it with real policy algorithms (``compute_verdict``,
``policy_kind_sets``, ...). ``ChangeKind`` itself carries none of that --
every member here is a bare ``(name, value)`` pair plus a documentation
comment, no policy logic at all -- so it moves cleanly.

Assembled via the functional ``Enum()`` API from three sibling files
(``kind_names_1.py``/``kind_names_2.py``/``kind_names_3.py``), concatenated
in that order, rather than declared as one class body: a single ~950-line
class definition exceeds this repository's 800-line production file-size
cap once physically placed under ``model/`` (`scripts/check_architecture.py`
enforces this for every file under `abicheck/`, migrated or not, and
explicitly forbids exempting a *new* file via `architecture/debt.yaml`'s
adoption-debt ledger -- confirmed by reading that check directly). The
three-way split is purely by original declaration-order line position, not
by taxonomy category (unlike ``ChangeKindMeta``'s deliberate 5-way
``symbols.py``/``types.py``/``platform.py``/``build.py``/``source.py``
split) -- specifically so concatenating them reproduces the *exact* original
member order, since nothing about splitting this enum needs taxonomy
grouping the way the metadata registry's D9 ownership split does.

Two members carry a Python identifier that is not simply the uppercased
value (verified against the original class body before this split, not
assumed): ``EVIDENCE_COVERAGE_ASYMMETRIC`` = ``"layer_coverage_asymmetric"``
and ``BUNDLE_INTRA_DEP_VERSION_DRIFT`` =
``"bundle_intra_dep_resolved_to_different_version"`` -- both intentional,
pre-existing renames where the enum's Python-facing name stayed the
original, more legible identifier while the serialized string value was
shortened or corrected. The functional API call below preserves both
exactly, since it uses the literal ``(name, value)`` pairs carried by the
three sibling files rather than deriving one from the other.

The ``_missing_`` back-compat hook (accepting the pre-rename
``evidence_coverage_asymmetric`` serialized value) cannot be passed through
the functional ``Enum()`` call directly -- that API only accepts member
names/values -- so it is declared on ``_ChangeKindBase``, a plain ``str``
mixin passed via ``type=``. Verified this preserves every property that
matters (identical JSON serialization, `isinstance(x, str)`, pickling by
qualified reference, `_missing_` lookup through the mixin's MRO) against a
minimal reproduction before trusting it, rather than assumed from reading
the ``enum`` module's docs alone.

``checker_policy.py`` re-exports ``ChangeKind`` from here (``from
.model.change_catalog.kinds import ChangeKind as ChangeKind``) rather than
routing it through ``change_registry.py``'s existing re-export point,
mirroring that module's own precedent for ``VALID_BASE_POLICIES``: nothing
outside `checker_policy.py` currently imports `ChangeKind` via
`change_registry`, so there is no reason to grow that file for a path
nothing uses. Every one of the ~220 existing ``from .checker_policy import
ChangeKind`` call sites keeps working unchanged.

Leaf module: no internal imports beyond its three sibling data files
(themselves leaves too), matching `model/change_catalog/registry.py`'s own
``model`` layer contract of ``may_import: []``.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from .kind_names_1 import KIND_NAMES_1
from .kind_names_2 import KIND_NAMES_2
from .kind_names_3 import KIND_NAMES_3

_ALL_KIND_NAMES: tuple[tuple[str, str, str | None], ...] = (
    *KIND_NAMES_1,
    *KIND_NAMES_2,
    *KIND_NAMES_3,
)


class _ChangeKindBase(str):
    """Plain ``str`` mixin carrying ``ChangeKind``'s one behavioral extra.

    A method, not a member -- the functional ``Enum()`` call below only
    accepts ``(name, value)`` pairs, so anything beyond that (here, just
    this one classmethod) has to arrive through the mixin passed as
    ``type=`` instead of the class body.
    """

    @classmethod
    def _missing_(cls, value: object) -> ChangeKind | None:
        # Back-compat: accept the pre-rename serialized value so reports and
        # policy files written before the evidence→buildsource rename still
        # deserialize. ``evidence_coverage_asymmetric`` was renamed to
        # ``layer_coverage_asymmetric``; the meaning is unchanged.
        if value == "evidence_coverage_asymmetric":
            return ChangeKind.EVIDENCE_COVERAGE_ASYMMETRIC
        return None


ChangeKind = Enum(
    "ChangeKind",
    [(name, value) for name, value, _comment in _ALL_KIND_NAMES],
    type=_ChangeKindBase,
    module=__name__,
    qualname="ChangeKind",
)
ChangeKind.__doc__ = (
    "One kind of detected ABI/API change (397 members, ADR-061 D9).\n\n"
    "See ``abicheck/change_registry.py`` for the single-declaration metadata "
    "registry (impact text, default verdict, policy overrides) each member "
    "here has exactly one corresponding entry in -- this enum carries only "
    "identity (name + serialized value), never classification."
)


class HasKind(Protocol):
    kind: ChangeKind
