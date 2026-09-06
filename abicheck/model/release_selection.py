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

"""ADR-065 S1: an explicit, identity-keyed release member *selection*.

Before this module, the release fan-out (``cli_compare_release_helpers.
_match_release_keys``) only ever inferred what to compare from filename
set-difference between two discovered directories -- there was no way for a
caller to *declare* "these are the members I expect", independent of what a
directory listing happens to contain. That inference is still how members
are discovered and matched (S1 does not touch it -- see the ADR-065 deletion
gate in ``docs/contribute/plans/vision-api-abi-evolution.md`` section A); what
was missing is a typed, optional overlay a caller can supply on top of it.

:class:`ReleaseSelection` is that overlay: ``{canonical release-matching key:
required}`` (the identical key space ``_canonical_library_key`` already
produces and ``ScopeAcquisitionRecord.members`` already keys by -- never a
raw filename stem, so a SONAME bump or a relocated library still resolves to
the same declared identity). Mirrors
:class:`abicheck.workflows.aggregate.resolve.ExpectedTargets`'s own
``{target_id: required}`` shape deliberately -- that is this codebase's
existing, reviewed answer to "a caller declares an expected set with a
required/optional split", and a release's expected-library set is the same
shape of question asked about a different identity space. Not the same
*type*, because the two carry different domain concepts (a target id is
opaque to this module; ``ExpectedTargets`` also carries manifest-specific
``head_sha``/gate-policy fields a release selection has no use for) -- but
the field shape and the required/optional split are intentionally identical
rather than a second, independently-invented vocabulary.

A ``ReleaseSelection`` changes what :func:`abicheck.workflows.release_scope.
build_release_scope_record` does in exactly two ways (see that function's
own ``selection=`` parameter):

* every discovered member *not* named in the selection is
  :attr:`~abicheck.model.scope_acquisition.AcquisitionState.OUT_OF_SCOPE`
  (declared, not inferred -- ADR-065 D9's own narrow inference is a
  *different*, filename-shape-triggered rule that still applies when no
  selection is given at all);
* a declared member absent from *both* sides is
  :attr:`~abicheck.model.scope_acquisition.AcquisitionState.
  EXPECTED_NOT_PRODUCED` -- a state that existed in the enum since S2 but
  had no producer until a declared inventory existed to need it (see that
  enum member's own docstring).

*required* is per-member, not per-run: an *optional* declared member missing
from one side does not make ``ScopeAcquisitionRecord.is_incomplete`` true
(see :attr:`MemberAcquisition.required <abicheck.model.scope_acquisition.
MemberAcquisition.required>`) -- exactly the knob a caller needs to declare
"I expect these N libraries, but M of them are platform-conditional and
their absence on one side is not a gap in what I checked." A *required*
member missing is still governed by the existing ``--on-incomplete-scope
warn|block`` policy (ADR-065 D6) -- this slice does not add a second,
selection-specific gate parallel to that one; see this module's own
docstring in ``workflows/release_scope.py`` for why that is a deliberate,
scoped limitation rather than an oversight.

A leaf ``model`` module (no imports beyond the standard library), same
convention as ``model/scope_acquisition.py`` -- ``workflows`` and the CLI
depend on it, never the reverse.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ReleaseSelection"]


@dataclass(frozen=True)
class ReleaseSelection:
    """An explicit, identity-keyed release member selection (ADR-065 S1).

    ``members`` maps a canonical release-matching key (``_canonical_
    library_key`` -- e.g. ``libfoo.so`` for a discovered ``libfoo.so.1.2``)
    to whether that member is *required*. Never empty -- an empty selection
    is not a selection at all, and every constructor below rejects it, the
    same "no expected targets given" discipline
    :meth:`~abicheck.workflows.aggregate.resolve.ExpectedTargets.from_lists`
    already applies for the aggregate manifest's own declared-set input.
    """

    members: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.members:
            raise ValueError(
                "ReleaseSelection needs at least one declared member "
                "(an empty selection would select nothing, not everything)"
            )

    def __bool__(self) -> bool:
        return bool(self.members)

    def __contains__(self, key: object) -> bool:
        return key in self.members

    @property
    def required_members(self) -> frozenset[str]:
        """Declared members that gate under ``--on-incomplete-scope``."""
        return frozenset(k for k, required in self.members.items() if required)

    @property
    def optional_members(self) -> frozenset[str]:
        """Declared members whose absence never makes the scope incomplete."""
        return frozenset(k for k, required in self.members.items() if not required)

    @classmethod
    def from_lists(
        cls, *, required: Iterable[str] = (), optional: Iterable[str] = ()
    ) -> ReleaseSelection:
        """Build from two flat key lists -- the CLI's ``--select-required``/
        ``--select`` shape. *required* wins on a key present in both (the
        stronger claim), matching ``ExpectedTargets.from_lists``'s own
        precedence for the identical ambiguity."""
        members: dict[str, bool] = {k: False for k in optional}
        for k in required:
            members[k] = True
        return cls(members=members)

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of this value."""
        return {"members": dict(self.members)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReleaseSelection:
        """Parse the JSON shape :meth:`to_dict` produces. Every entry is
        validated eagerly and completely, matching this codebase's
        ``PolicyFile``/``BuildConfig`` "hard load error, not warning-and-skip"
        convention for a structurally invalid declared input."""
        raw = data.get("members")
        if not isinstance(raw, dict) or not raw:
            raise ValueError(
                "release selection: 'members' must be a non-empty mapping "
                "of canonical key -> required (bool)"
            )
        members: dict[str, bool] = {}
        for key, required in raw.items():
            if not isinstance(key, str) or not key:
                raise ValueError(
                    f"release selection: member key must be a non-empty "
                    f"string, got {key!r}"
                )
            if not isinstance(required, bool):
                raise ValueError(
                    f"release selection.{key}: 'required' must be a boolean, "
                    f"got {type(required).__name__}"
                )
            members[key] = required
        return cls(members=members)
