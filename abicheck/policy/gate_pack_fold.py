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

"""The one gate-pack severity fold, and the one gate-algorithm derivation
(``docs/contribute/plans/duplication-and-convergence-assessment.md``'s T6).

Two call sites apply a selected ``kind: gate`` pack's
``gate.severity.<category>`` contribution, over two different runtime
shapes:

* single-pair ``compare`` -- :func:`~abicheck.pack_application.
  apply_to_compare_config`, folding onto an already-resolved
  :class:`~abicheck.policy.severity.SeverityConfig`;
* the directory/package release fan-out (and, through it, a typed
  ``ScanRequest``) -- :func:`~abicheck.policy.release_gate_options.
  apply_release_gate_pack`, folding onto four independent optional raw
  severity strings, before any :class:`SeverityConfig` exists.

Until this module existed each expressed the *identical* rule -- "a pack
level replaces the pre-pack value for exactly the categories the pack
supplied, and for no others" -- in its own idiom, guarded only after the
fact by ``tests/test_release_gate_pack_fold_parity.py``'s outcome-parity
property. Two independently-reasoned implementations of one algorithm is
this repository's own ``AGENTS.md`` "Primitive-level property tests" case,
so the rule lives here once and both callers invoke it; the parity property
now guards the two callers' *surrounding* shapes rather than two copies of
the fold itself.

**Why here.** ``policy/`` owns "deciding gate/severity effect" (see this
package's ``AGENTS.md``), and it is inward of both callers:
``pack_application.py`` is an unclassified flat ``legacy_root_module``
(ADR-061's incremental migration) that may depend on this package, while
``release_gate_options.py`` is a sibling in it. The reverse -- ``policy``
importing the flat-root ``pack_application`` -- is the real
dependency-direction violation this module exists to avoid, and is why
``release_gate_options.py`` consumes a ``_GatePackApplication``
:class:`~typing.Protocol` rather than that module's concrete class.

A leaf: it imports nothing from ``abicheck`` at all, so either caller can
depend on it without acquiring the other's dependencies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

_T = TypeVar("_T")

#: The four severity categories a ``kind: gate`` pack can assign, in
#: :class:`~abicheck.policy.severity.SeverityConfig` field-name spelling --
#: which is also ``PackApplication.severity_levels``' key spelling and
#: ``compatibility_evaluation_frontend.SEVERITY_CATEGORY_FIELDS``' key
#: spelling, so no translation happens anywhere in this fold.
#: ``tests/test_gate_pack_fold.py`` pins this tuple against
#: ``SeverityConfig``'s own fields, so a fifth category cannot be added
#: there and silently stay unfoldable here.
GATE_SEVERITY_CATEGORIES: tuple[str, ...] = (
    "abi_breaking",
    "potential_breaking",
    "quality_issues",
    "addition",
)

#: The two resolved gate algorithms (ADR-037 D12's set, minus the ``auto``
#: that is a resolution-time choice rather than a resolved value).
SEVERITY_SCHEME = "severity"
LEGACY_SCHEME = "legacy"


def fold_gate_pack_severity(
    current: Mapping[str, _T], levels: Mapping[str, Any]
) -> dict[str, Any]:
    """The per-category values *current* becomes once *levels* is folded in.

    *current* is the pre-pack value per category, in whatever vocabulary the
    caller carries it (resolved :class:`~abicheck.policy.severity.
    SeverityLevel` members for ``compare``; raw ``str | None`` CLI/config
    strings, ``None`` where nothing stated the category, for the release
    fan-out). *levels* is a pack's own contribution --
    ``PackApplication.severity_levels``, which by that class's contract holds
    an entry **only** for a category a pack actually supplied.

    Returns a fresh mapping over exactly :data:`GATE_SEVERITY_CATEGORIES`:
    the pack's level where it supplied one, *current*'s value otherwise.
    Keys of *current* outside those categories are dropped rather than
    carried, so a caller cannot accidentally widen what a fold writes back.

    A pack level can only ever reach a category no more-explicit source
    stated: the resolver exempts ``gate.severity.*`` from pack assignment
    whenever the CLI, a ``--profile``, or ``.abicheck.yml`` stated it (or
    stated a preset that owns it), so ``levels`` never carries a value D7
    precedence ruled out. This function does not re-check that -- re-deriving
    precedence here is exactly what ``pack_application``'s "read, don't
    re-derive" rule forbids.

    Raises:
        ValueError: if *levels* names a category outside
            :data:`GATE_SEVERITY_CATEGORIES`. Unknown keys are rejected
            rather than ignored: silently dropping one would make a pack
            assignment that configures nothing, which is the exact
            decorative-pack failure ``pack_application.py`` exists to
            prevent, and a caller splatting the result into a
            ``SeverityConfig`` would otherwise fail far from the cause.
        KeyError: if *current* omits a category *levels* does not supply --
            the result would otherwise be silently short a category.
    """
    unknown = sorted(set(levels) - set(GATE_SEVERITY_CATEGORIES))
    if unknown:
        raise ValueError(
            f"gate pack severity levels name unknown categories {unknown}; "
            f"valid categories are {list(GATE_SEVERITY_CATEGORIES)}"
        )
    folded: dict[str, Any] = {}
    for category in GATE_SEVERITY_CATEGORIES:
        if category in levels:
            folded[category] = levels[category]
        else:
            folded[category] = current[category]
    return folded


def gate_exit_code_scheme(severity_active: bool) -> str:
    """The one automatic gate algorithm (ADR-064/CLI cleanup phase two PR G2).

    ``"severity"`` exactly when a severity setting is in effect, else
    ``"legacy"``. There is no manual selector any more -- ``--exit-code-
    scheme``, ``.abicheck.yml``'s ``exit_code_scheme:`` key, and the
    ``gate.exit_code_scheme`` pack route were all deleted -- so every object
    that publishes a scheme derives it from its own "is severity in effect"
    predicate through this function rather than storing an independently
    settable field beside that predicate (the duplication-and-convergence-
    assessment plan's own "the *model* still permits disagreement"
    finding).
    """
    return SEVERITY_SCHEME if severity_active else LEGACY_SCHEME
