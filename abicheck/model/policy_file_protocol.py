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

"""``PolicyFileProtocol``/``ReclassifyRuleProtocol`` — the ``model -> policy``
field-typing fix ADR-061's Phase 2/4 investigation designed and left
"decided, but not yet actionable" pending one co-prerequisite (see
``docs/contribute/adr/061-responsibility-package-architecture.md``'s Phase 4
section, "PolicyFile"/"ReclassifyRule" paragraphs): ``ChangeKind`` classified
somewhere a ``model``-owned module can import without tripping
``unclassified-import``. That prerequisite is satisfied today —
``ChangeKind`` (and ``Verdict``) already live in
``model/change_catalog/kinds.py``/``model/change_catalog/registry.py`` — so
this module implements the mechanism that investigation selected rather than
proposing a new one.

``checker_types.py``'s ``DiffResult`` (``model``-classified) needs to *hold*
a real, method-bearing ``PolicyFile`` (``compute_verdict`` in particular is
policy's own resolution algorithm, not a fact about a document — see
``abicheck/policy_file.py``'s own docstring) without importing the concrete
class, which would be a real ``model -> policy`` edge once ``policy_file.py``
is classified ``policy``. Python's structural typing (PEP 544) resolves
this: a ``model``-owned :class:`Protocol` describing exactly the surface
``DiffResult``/``BundleDiffResult`` consumers actually read off a
``policy_file`` field is satisfied by the real ``PolicyFile``/
``ReclassifyRule`` classes without either importing or inheriting from it —
so ``PolicyFile``/``ReclassifyRule`` stay exactly as they are in
``policy_file.py``/``reclassify.py``, unmoved and unmodified.

Two things this narrow fix does **not** claim to resolve — recorded so a
later reader doesn't re-litigate them as if this module were meant to:

1. It closes only the *field's declared type*. ``DiffResult``'s own methods
   (``_effective_kind_sets``/``_effective_verdict_for_change``) still
   *execute* real policy-resolution algorithms in their bodies (module-level
   imports of ``checker_policy``/``reclassify``, not annotations) — a
   second, real ``model``-holds-policy-logic tension the ADR's own Phase 4
   section records as an explicit, unaudited known gap, not something this
   module's Protocol pair touches.
2. ``reclassify.py`` itself is not reclassified and does not need to be —
   it remains **deliberately unclassified** (``check_architecture.py``
   never checks an unclassified module's own imports), so nothing here
   requires ``ReclassifyRule``'s constructor-time ``suppression.py``
   dependency, or its own ``checker_policy`` imports, to be resolved first.

Exhaustiveness of the member lists below was verified against every real
``<something>.policy_file.<member>`` read in the codebase (``reporter.py``,
``reporter_markdown.py``, ``sarif.py``, ``bundle_models.py``,
``compatibility_evaluation_frontend.py``, plus ``checker_types.py``'s own
method bodies) at the time this module was added, not assumed complete from
``PolicyFile``'s docstring alone — the ADR's own investigation found real
gaps doing exactly that (see its five-Codex-round member-list history).
Widening either protocol later needs the identical grep-and-verify
treatment, not a guess.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .change_catalog.kinds import ChangeKind
from .change_catalog.registry import Verdict

__all__ = [
    "PolicyFileProtocol",
    "ReclassifyRuleProtocol",
]


class ReclassifyRuleProtocol(Protocol):
    """Structural surface of :class:`abicheck.reclassify.ReclassifyRule`.

    Five members — matching every real consumer of a rule read off a
    ``policy_file.reclassify`` sequence (``reporter.py``,
    ``reporter_markdown.py``, ``sarif.py``, and
    ``reclassify.first_matching_reclassify_verdict`` itself, which reads
    ``to_verdict``, not one of the four methods alone).
    """

    @property
    def to_verdict(self) -> Verdict:
        """The verdict this rule reclassifies a matching change to."""
        ...

    def matches(self, change: Any, today: date | None = None) -> bool:
        """Whether this rule's selector matches *change*, unexpired."""
        ...

    def is_expired(self, today: date | None = None) -> bool:
        """Whether this rule's ``expires:`` date has passed *today*."""
        ...

    def describe(self) -> str:
        """One-line human-readable summary of this rule's selector."""
        ...

    def to_report_dict(self) -> dict[str, str]:
        """This rule's disclosure shape for JSON/SARIF/Markdown reports."""
        ...


class PolicyFileProtocol(Protocol):
    """Structural surface of :class:`abicheck.policy_file.PolicyFile`.

    Five members — matching every real consumer of a ``DiffResult``/
    ``BundleDiffResult`` ``.policy_file`` field: ``overrides``/``reclassify``
    (``checker_types.py`` itself), ``source_path``/``overrides``/
    ``reclassify`` (``reporter.py``, ``reporter_markdown.py``, ``sarif.py``),
    ``base_policy`` (``compatibility_evaluation_frontend.py``, off its own
    unrelated ``ExplicitCompatibilityInputs.policy_file`` — same member,
    included here for completeness), and ``compute_verdict()``
    (``bundle_models.py``). ``reclassify``'s ``Sequence[...]`` (not
    ``list[...]``) is deliberate: ``Sequence`` is covariant, so
    ``PolicyFile.reclassify: list[ReclassifyRule]`` (concrete, mutable)
    satisfies this read-only property structurally without ``PolicyFile``
    changing its own field type.
    """

    @property
    def base_policy(self) -> str:
        """The built-in policy profile this document starts from."""
        ...

    @property
    def overrides(self) -> Mapping[ChangeKind, Verdict]:
        """Kind-global verdict overrides this document states."""
        ...

    @property
    def reclassify(self) -> Sequence[ReclassifyRuleProtocol]:
        """Selector-scoped reclassify rules, in file (priority) order."""
        ...

    @property
    def source_path(self) -> Path | None:
        """Path this document was loaded from, or ``None`` if constructed
        directly rather than via :meth:`~abicheck.policy_file.PolicyFile.load`.
        """
        ...

    def compute_verdict(self, changes: list[Any]) -> Verdict:
        """Compute the overall verdict for *changes* under this policy."""
        ...
