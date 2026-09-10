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

"""ADR-066 D4/S2: the versioning policy model, and D5's orthogonal
release-acceptance evaluation built on it.

This module is deliberately small and separable, per D4: five independent
controls (``scheme``, ``promise``, ``support_window``, ``deprecation_window``,
``enforcement``), a typed ``VersioningPolicy`` composing them, and a built-in
default equal to today's behavior ("strict SemVer advice, no windows,
advisory only") -- so resolving this namespace through
:func:`~abicheck.compatibility_evaluation_resolver.resolve_field` for a run
that never states it changes nothing.

:func:`evaluate_release_acceptance` is the one evaluator that lives here --
for one pairwise :class:`~abicheck.checker_types.DiffResult` (already scored
by :func:`abicheck.semver.recommend_release`), whether *this* release is
acceptable under the project's declared compatibility ``promise`` and
``enforcement``. Never touches the verdict, the finding set, or the observed
``bump``/``soname`` -- it is a strictly additional, orthogonal fact (see
:class:`~abicheck.semver.ReleaseRecommendation.policy_acceptance`), read-only
over already-computed facts (D5: "policy changes acceptance; it never
changes facts").

The *other* D4/D5 evaluator ADR-066 S2 adds --
``abicheck.workflows.history.evaluate_deprecation_compliance``, which checks
each observed ``removed`` lifecycle event against ``deprecation_window`` --
lives in ``workflows/history.py`` instead of here: it operates on
``LongitudinalHistoryResult``, a ``workflows``-owned type this leaf ``policy``
module may not import (ADR-061's dependency direction: ``workflows`` may
import ``policy``, never the reverse). ``support_window`` is a *declared*,
validated control here but is **not evaluated** for conformance in S2 --
evaluating a candidate release against a named support window requires
resolving which prior baselines it actually names (D3), which depends on
S3's CI publication/resolution channel existing at all; see
:class:`SupportWindow`'s own docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

from .classification import Verdict

if TYPE_CHECKING:
    from ..checker_types import DiffResult

__all__ = [
    "CompatibilityPromise",
    "DeprecationWindow",
    "PolicyAcceptance",
    "SupportWindow",
    "VersioningEnforcement",
    "VersioningPolicy",
    "VersioningScheme",
    "built_in_default_versioning_policy",
    "evaluate_release_acceptance",
    "integrate_policy_acceptance",
]


class VersioningScheme(str, Enum):
    """D4's ``scheme`` control: how version labels parse and order.

    ``STRICT_SEMVER`` is the built-in default -- ``abicheck/semver.py``'s
    existing table already assumes it. The other three name declared
    alternatives D4 lists (a relaxed SemVer, a calendar scheme, and freeform/
    opaque labels with no parseable order); none of them is a version
    *parser* implemented here -- see the module docstring's scope note.
    """

    STRICT_SEMVER = "strict_semver"
    RELAXED_SEMVER = "relaxed_semver"
    CALVER = "calver"
    FREEFORM = "freeform"


class CompatibilityPromise(str, Enum):
    """D4's ``promise`` control: what compatibility the project claims
    between two ordered versions.

    ``NONE`` is the built-in default (matching today's behavior: abicheck
    makes no assumption that a project promises anything) and is also the
    explicit "pre-1.0 / no compatibility guarantee" spelling D4 calls out --
    a project on this promise never has a policy-level compatibility
    deviation, whatever the observed verdict.
    """

    NONE = "none"
    ABI_WITHIN_MAJOR = "abi_within_major"
    API_WITHIN_MINOR = "api_within_minor"
    SOURCE_WITHIN_MINOR = "source_within_minor"


class VersioningEnforcement(str, Enum):
    """D4's ``enforcement`` control: what a policy deviation does.

    ``WARN`` is the built-in default -- advisory only, matching today's
    behavior (D4: "no existing run changes"). ``BLOCK`` is the only other
    value; D4 states enforcement is per-control, but with a single
    ``VersioningPolicy.enforcement`` field (no separate CLI/config surface
    yet to state five independent enforcement values), one shared value
    governs every control this module evaluates -- see the module
    docstring's scope note.
    """

    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class SupportWindow:
    """D4's ``support_window`` control: which prior releases/lines a
    candidate must remain compatible with.

    ``kind`` names which shape the window takes; the other fields carry that
    shape's own data. Declared and validated by this module, but **not
    evaluated for conformance** in S2 -- see the module docstring's scope
    note. A future slice that resolves real prior baselines (D3/S3) is what
    gives this evaluable meaning; until then a project may declare one for
    documentation/replay purposes with no behavioral effect.
    """

    kind: str = "none"
    last_n: int | None = None
    line: str | None = None
    versions: tuple[str, ...] = ()

    _VALID_KINDS = ("none", "last_n_minors", "compatibility_line", "explicit")

    def __post_init__(self) -> None:
        if self.kind not in self._VALID_KINDS:
            raise ValueError(
                f"SupportWindow.kind must be one of {self._VALID_KINDS}, "
                f"got {self.kind!r}"
            )
        if self.kind == "last_n_minors" and (self.last_n is None or self.last_n < 1):
            raise ValueError(
                "SupportWindow.last_n must be a positive int when kind='last_n_minors'"
            )
        if self.kind == "compatibility_line" and not self.line:
            raise ValueError(
                "SupportWindow.line must be non-empty when kind='compatibility_line'"
            )
        if self.kind == "explicit" and not self.versions:
            raise ValueError(
                "SupportWindow.versions must be non-empty when kind='explicit'"
            )
        object.__setattr__(self, "versions", tuple(self.versions))

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "last_n": self.last_n,
            "line": self.line,
            "versions": list(self.versions),
        }


@dataclass(frozen=True)
class DeprecationWindow:
    """D4's ``deprecation_window`` control: the minimum observed deprecation
    before a removal is policy-conforming.

    ``min_releases`` is S2's bounded, release-count evaluation of D4's real
    scheme-aware windows ("one minor", "one release on the line") -- see the
    module docstring's scope note. ``0`` (the default) means "none": an
    entity may be removed in the same release it was first marked deprecated
    with no policy deviation, matching today's advisory-only behavior.
    """

    min_releases: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.min_releases, int) or isinstance(
            self.min_releases, bool
        ):
            raise TypeError(
                f"DeprecationWindow.min_releases must be an int, not "
                f"{self.min_releases!r}"
            )
        if self.min_releases < 0:
            raise ValueError(
                "DeprecationWindow.min_releases must be >= 0, got "
                f"{self.min_releases!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return {"min_releases": self.min_releases}


@dataclass(frozen=True)
class VersioningPolicy:
    """D4's whole versioning policy: five independent controls.

    The built-in default (every field left unset) is deliberately equal to
    today's behavior -- strict SemVer advice, no windows, advisory only
    (D4: "no existing run changes"). Every field validates independently in
    its own ``__post_init__`` (:class:`SupportWindow`/:class:`DeprecationWindow`);
    this dataclass adds no further cross-field validation, since D4 states
    the five controls are independent.
    """

    scheme: VersioningScheme = VersioningScheme.STRICT_SEMVER
    promise: CompatibilityPromise = CompatibilityPromise.NONE
    support_window: SupportWindow = field(default_factory=SupportWindow)
    deprecation_window: DeprecationWindow = field(default_factory=DeprecationWindow)
    enforcement: VersioningEnforcement = VersioningEnforcement.WARN

    def __post_init__(self) -> None:
        if not isinstance(self.scheme, VersioningScheme):
            raise TypeError(
                f"VersioningPolicy.scheme must be a VersioningScheme, not "
                f"{self.scheme!r}"
            )
        if not isinstance(self.promise, CompatibilityPromise):
            raise TypeError(
                f"VersioningPolicy.promise must be a CompatibilityPromise, "
                f"not {self.promise!r}"
            )
        if not isinstance(self.support_window, SupportWindow):
            raise TypeError(
                f"VersioningPolicy.support_window must be a SupportWindow, "
                f"not {self.support_window!r}"
            )
        if not isinstance(self.deprecation_window, DeprecationWindow):
            raise TypeError(
                "VersioningPolicy.deprecation_window must be a "
                f"DeprecationWindow, not {self.deprecation_window!r}"
            )
        if not isinstance(self.enforcement, VersioningEnforcement):
            raise TypeError(
                "VersioningPolicy.enforcement must be a VersioningEnforcement, "
                f"not {self.enforcement!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "scheme": self.scheme.value,
            "promise": self.promise.value,
            "support_window": self.support_window.to_dict(),
            "deprecation_window": self.deprecation_window.to_dict(),
            "enforcement": self.enforcement.value,
        }


def built_in_default_versioning_policy() -> VersioningPolicy:
    """The built-in-default policy: equal to today's behavior (D4).

    A named function (rather than callers writing ``VersioningPolicy()``
    inline) so every resolver/wiring call site states the same intent this
    module's own docstring documents -- "the built-in default is the current
    behavior... so no existing run changes."
    """
    return VersioningPolicy()


# ---------------------------------------------------------------------------
# D5: release acceptance (orthogonal to the observed recommendation).
# ---------------------------------------------------------------------------

#: Verdicts that always mean "no compatibility promise was ever tested" --
#: acceptance is trivially True regardless of policy, since there is no
#: break to be either conforming or deviating. ``COMPATIBLE_WITH_RISK`` is
#: included: a deployment-floor risk is not a compatibility *break*, so it
#: cannot deviate from a compatibility promise either.
_NON_BREAKING_VERDICTS = frozenset(
    {Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK}
)


def _verdict_deviates_from_promise(
    verdict: Verdict, promise: CompatibilityPromise
) -> bool:
    """Does *verdict* violate *promise*, once ``promise`` is not ``NONE``?

    ``Verdict.API_BREAK`` is a source-level break with no observed ABI
    break; ``Verdict.BREAKING`` is an ABI break. ``ABI_WITHIN_MAJOR`` only
    promises ABI stability, so a source-only ``API_BREAK`` never deviates
    from it -- only ``BREAKING`` does (CodeRabbit review). ``API_WITHIN_MINOR``
    and ``SOURCE_WITHIN_MINOR`` promise source stability too, so either
    non-compatible verdict deviates from them.
    """
    if verdict in _NON_BREAKING_VERDICTS:
        return False
    if promise is CompatibilityPromise.ABI_WITHIN_MAJOR:
        return verdict is Verdict.BREAKING
    return True


@dataclass(frozen=True)
class PolicyAcceptance:
    """D5's *unmet release policy* axis: whether this release is acceptable
    under the project's own versioning policy, entirely separate from the
    observed recommendation (:class:`~abicheck.semver.ReleaseRecommendation`).

    Never derived from -- and never feeds back into -- the compatibility
    verdict, the finding set, or ``bump``/``soname`` (D5's executable
    invariant): only ``accepted``/``detail`` differ between a strict and a
    relaxed policy evaluated over the identical
    :class:`~abicheck.checker_types.DiffResult`.
    """

    accepted: bool
    enforcement: VersioningEnforcement
    promise: CompatibilityPromise
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "enforcement": self.enforcement.value,
            "promise": self.promise.value,
            "detail": self.detail,
        }


def evaluate_release_acceptance(
    result: DiffResult, policy: VersioningPolicy
) -> PolicyAcceptance:
    """D5: is *result*'s release acceptable under *policy*?

    Reads only ``result.verdict`` -- never the finding set, never evidence,
    never anything :func:`abicheck.semver.recommend_release` itself derives
    -- so this function cannot alter what was observed, only how it is
    accepted (D5's own executable invariant, restated as code rather than
    prose alone).

    * A non-breaking verdict (``NO_CHANGE``/``COMPATIBLE``, including the
      risk tier) is always accepted: no compatibility promise was tested.
    * ``promise=NONE`` (the default -- "no compatibility guarantee",
      e.g. a pre-1.0 project) means a break is *never* a policy deviation,
      whatever ``enforcement`` says: there is no promise to violate.
    * ``promise=ABI_WITHIN_MAJOR`` only promises ABI stability, so a
      source-only ``API_BREAK`` (no observed ABI break) is not a
      deviation from it -- only ``BREAKING`` is (CodeRabbit review; see
      :func:`_verdict_deviates_from_promise`). ``API_WITHIN_MINOR`` and
      ``SOURCE_WITHIN_MINOR`` make either non-compatible verdict a genuine
      deviation. ``enforcement=WARN`` (default) still accepts a deviation
      (advisory only, matching D4's "no existing run changes" default);
      ``enforcement=BLOCK`` does not.
    """
    if not _verdict_deviates_from_promise(result.verdict, policy.promise):
        return PolicyAcceptance(
            accepted=True,
            enforcement=policy.enforcement,
            promise=policy.promise,
            detail="no breaking or API-break verdict was observed -- no "
            "compatibility promise was tested."
            if result.verdict in _NON_BREAKING_VERDICTS
            else "the observed verdict does not fall within the declared "
            f"promise's scope ({policy.promise.value}) -- not a deviation.",
        )

    if policy.promise is CompatibilityPromise.NONE:
        return PolicyAcceptance(
            accepted=True,
            enforcement=policy.enforcement,
            promise=policy.promise,
            detail="the project's versioning policy declares no compatibility "
            "promise ('promise: none') -- a break here is policy-conforming "
            "regardless of severity.",
        )

    if policy.enforcement is VersioningEnforcement.WARN:
        return PolicyAcceptance(
            accepted=True,
            enforcement=policy.enforcement,
            promise=policy.promise,
            detail=f"the observed break deviates from the declared promise "
            f"({policy.promise.value}), but the policy's enforcement is "
            "'warn' -- accepted, with the deviation noted.",
        )

    return PolicyAcceptance(
        accepted=False,
        enforcement=policy.enforcement,
        promise=policy.promise,
        detail=f"the observed break deviates from the declared promise "
        f"({policy.promise.value}) and the policy's enforcement is "
        "'block' -- not accepted under the versioning policy.",
    )


#: Bound loosely (not to ``ReleaseRecommendation`` by name) so this leaf
#: ``policy`` module never has to import ``abicheck.semver`` -- doing so
#: would create a real ``policy -> semver -> policy`` import cycle, since
#: ``semver.py`` itself imports this module for the model/evaluator
#: (ADR-061 dependency direction; caught by
#: ``scripts/check_ai_readiness.py``'s ``import-cycle-growth`` check). Any
#: object with a ``policy_acceptance`` field ``dataclasses.replace`` can set
#: satisfies this -- in practice, always a real ``ReleaseRecommendation``.
_R = TypeVar("_R")


def integrate_policy_acceptance(
    recommendation: _R,
    result: DiffResult,
    policy: VersioningPolicy | None,
) -> _R:
    """Attach D5's acceptance axis to an already-computed *recommendation*
    (:class:`~abicheck.semver.ReleaseRecommendation`, though this module
    never imports that type -- see :data:`_R`'s own comment).

    ``policy is None`` returns *recommendation* unchanged (no versioning
    policy resolved for this run -- the built-in-default-shaped input, but
    represented as "not evaluated" rather than materializing a default
    object every caller must construct). Never touches ``bump``/``soname``/
    ``state``/``rationale`` -- only ever sets the additive
    ``policy_acceptance`` field, so a caller that never opts in observes
    the exact object :func:`abicheck.semver.recommend_release` always
    returned.
    """
    if policy is None:
        return recommendation
    from dataclasses import replace

    return replace(
        recommendation,  # type: ignore[type-var]
        policy_acceptance=evaluate_release_acceptance(result, policy),
    )
