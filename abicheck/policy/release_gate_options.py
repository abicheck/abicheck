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

"""The directory/package release fan-out's gate-configuration resolution
(ADR-064, "``GateOptions`` -- the release fan-out's own prerequisite
rewrite"): folding a selected ``kind: gate`` pack into the release fan-out's
raw severity/exit-code-scheme inputs, and resolving the result into one
:class:`GateOptions` object -- this package's job (deciding gate/severity
effect), per ``abicheck/policy/AGENTS.md``.

Split out of :mod:`abicheck.cli_compare_release_helpers` (CLI cleanup phase
two, ADR-064 stage 1b) both to give this gating logic its ADR-061-owned
package and to keep that flat, unclassified module under the AI-readiness
file-size no-growth baseline (``architecture/debt.yaml``). Re-exported from
``cli_compare_release_helpers`` (and, from there, ``cli_compare_release``)
to preserve the pre-existing public import surface.

Depends on ``PackApplication`` (``abicheck/pack_application.py``) only
*structurally*, via :class:`_GatePackApplication` below, not by importing
the real class: ``pack_application.py`` is a grandfathered flat
``legacy_root_module`` (ADR-061's incremental migration), not a declared
``public_root_surface`` this package may depend on
(``abicheck/policy/AGENTS.md``'s "Permitted imports" -- ``policy`` may
depend only on ``model``, ``compare``, and the public root surfaces), so an
`import` of it here would be a real, checked dependency-direction violation,
not a style choice.

The severity fold itself is no longer written here at all: both this module
and ``pack_application.apply_to_compare_config`` call the one shared
:func:`~abicheck.policy.gate_pack_fold.fold_gate_pack_severity`
(duplication-and-convergence-assessment T6), which lives in a leaf module
inward of both -- the shape ADR-061 prescribes for logic two differently
classified modules share, and the only one that does not invert this
package's own permitted-import direction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .effective_gate import EffectiveGate, GateSeverityState
from .gate_pack_fold import gate_exit_code_scheme
from .severity import SeverityConfig, resolve_severity_config


class _GatePackApplication(Protocol):
    """The structural shape of
    :class:`~abicheck.pack_application.PackApplication` this module needs --
    a :class:`~typing.Protocol`, not an import of the real class, for the
    dependency-direction reason this module's own docstring explains.
    Every member here mirrors that class's field of the same name and type
    exactly; a real ``PackApplication`` instance satisfies this
    structurally, with no coupling beyond attribute names. Declared as
    read-only properties, not plain attributes: a `Protocol` attribute is
    implicitly settable (get *and* set), which the real class's frozen
    (read-only) dataclass fields can never satisfy structurally.

    CLI cleanup phase two PR G2: no ``exit_code_scheme``/
    ``resolved_exit_code_scheme`` members any more -- a gate pack can no
    longer assign the (now deleted) manual algorithm selector at all; see
    ``PackApplication``'s own field docstrings and
    ``compatibility_evaluation_wiring.py``'s pack-field routing table for
    where that assignment is now rejected at load time.
    """

    @property
    def severity_levels(self) -> Mapping[str, Any]: ...


def apply_release_gate_pack(
    pack_application: _GatePackApplication | None,
    *,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Fold a selected ``kind: gate`` pack's severity contribution into the
    release fan-out's own raw severity inputs (CLI cleanup phase two, "PR
    B" slice 2; narrowed by PR G2 to severity only -- a gate pack can no
    longer assign an exit-code-scheme override at all, see this module's
    own docstring).

    Returns ``(severity_preset, severity_abi_breaking,
    severity_potential_breaking, severity_quality_issues,
    severity_addition)`` -- the exact five raw values. Since ADR-064's
    ``GateOptions`` rewrite, callers should not call this directly:
    :func:`resolve_release_gate_options` calls it exactly once, ahead of
    the one resolution into :class:`GateOptions` every downstream severity
    consumer (``_compute_release_severity_exit_code``,
    ``_fold_release_global_severity``, and the per-library JSON write, via
    ``GateOptions.severity``) now reads instead of independently
    re-deriving from the raw strings -- this function stays a separate,
    directly-unit-tested step of that pipeline rather than being inlined
    into it.

    The fold rule itself is not written here (duplication-and-convergence-
    assessment T6): :func:`~abicheck.policy.gate_pack_fold.
    fold_gate_pack_severity` owns it, and ``pack_application.
    apply_to_compare_config`` -- single-pair ``compare``'s own gate-pack
    application -- calls the identical function, and (P0's own follow-on)
    both callers now fold through the identical
    :class:`~abicheck.policy.effective_gate.GateSeverityState` value type
    rather than each constructing its own ad hoc mapping. What stays this
    function's own job is the *shape* difference the two call sites
    genuinely have: the release fan-out folds onto four independent optional
    raw CLI-or-config strings, before any :class:`SeverityConfig` exists,
    where ``compare`` folds onto an already-resolved one. That difference is
    real (a release run must still be able to distinguish "no severity
    setting in effect" from "the default levels", which a resolved config
    cannot express), so it is expressed here, in how this function builds
    and reads back a :class:`GateSeverityState`, rather than by a second
    copy of the fold.

    A no-op when *pack_application* is ``None`` (no ``--pack`` given) or
    contributed no severity level -- every pre-existing invocation reaches
    the five inputs completely unchanged.
    """
    levels = {} if pack_application is None else pack_application.severity_levels
    state = GateSeverityState(
        levels={
            "abi_breaking": severity_abi_breaking,
            "potential_breaking": severity_potential_breaking,
            "quality_issues": severity_quality_issues,
            "addition": severity_addition,
        },
        # Whether a severity setting is already in effect on *this* raw
        # shape has no bearing on the fold itself (the release fan-out
        # resolves that fact afterward, over all five raw inputs including
        # the preset, via `_resolve_release_severity_config`) -- this state
        # is built only to carry `levels` through the shared fold and is
        # never read back for `.active`.
        active=False,
    ).folded(levels)
    folded = state.levels
    return (
        severity_preset,
        folded["abi_breaking"],
        folded["potential_breaking"],
        folded["quality_issues"],
        folded["addition"],
    )


def _resolve_release_severity_config(
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> SeverityConfig | None:
    """Resolve the severity config, or None when no severity setting was in effect."""
    if not any(
        v is not None
        for v in (
            severity_preset,
            severity_abi_breaking,
            severity_potential_breaking,
            severity_quality_issues,
            severity_addition,
        )
    ):
        return None
    return resolve_severity_config(
        severity_preset,
        abi_breaking=severity_abi_breaking,
        potential_breaking=severity_potential_breaking,
        quality_issues=severity_quality_issues,
        addition=severity_addition,
    )


@dataclass(frozen=True)
class GateOptions:
    """The release fan-out's one resolved severity/exit-code-scheme gate
    configuration (ADR-064, "``GateOptions`` — the release fan-out's own
    prerequisite rewrite").

    Before this type existed, the six raw preset/category/scheme strings a
    directory/package release run carries were threaded independently
    through three functions -- :func:`_resolve_release_severity_config`,
    ``_compute_release_severity_exit_code``,
    ``_fold_release_global_severity`` (both in
    :mod:`abicheck.cli_compare_release_helpers`) -- each re-deriving the
    identical :class:`SeverityConfig` from the same strings. They could not
    actually *disagree* (``compare_release_cmd`` reassigns the six raw
    values exactly once, early, before any of the three ever read them),
    but the redundant re-derivation was exactly the shape PR B's own
    "finalized" note flagged as unsafe to fix reactively, ahead of this
    ADR's settled design. :func:`resolve_release_gate_options` now performs
    that resolution exactly once; every downstream consumer takes the
    resulting ``GateOptions`` instead of the raw strings.

    ``severity is None`` is this object's single source of truth for "no
    severity setting is in effect for this release run" -- it already folds
    together both ways that could happen before CLI cleanup phase two PR G2
    removed the manual algorithm selector (an explicitly forced ``legacy``
    scheme, or no severity configuration at all) -- since PR G2 there is
    only one way: ``severity is None`` exactly when no severity setting is
    in effect. Still the same simplification ``ResolvedCompareConfig``'s
    own severity field already gives `compare`/`scan`.

    ``exit_code_scheme`` is kept alongside it for provenance/reporting (e.g.
    a dry-run scheme label), and is a **derived property, not a field**
    (duplication-and-convergence-assessment T6). It was documented as
    "purely derived" from the moment PR G2 deleted the manual selector, but
    stayed an independently constructible dataclass field beside
    ``severity``, so the *model* still permitted the two to disagree -- and
    two unit-test helpers were already constructing a ``GateOptions``
    carrying ``exit_code_scheme=None`` beside a real ``SeverityConfig``,
    which is exactly the state a reader of this docstring would have
    believed impossible. Deriving it makes the documented invariant
    structural: it is not authoritative for "should severity be folded"
    (``severity`` is), and it can no longer be stated at all.
    """

    severity_preset: str | None
    severity: SeverityConfig | None
    #: ADR-065's own two release-fan-out gate axes (Codex review, PR #1192,
    #: third follow-up round): the ``--on-incomplete-scope`` policy and
    #: ``--fail-on-removed-library``. Unlike ``require_complete_analysis``/
    #: scoped-gate selection (per-library, single-pair facts this object's
    #: own ``effective_gate`` property below deliberately leaves at their
    #: default), these two ARE resolved once at this object's own release-
    #: fan-out scope -- so they belong here as real fields, not only passed
    #: ad hoc to ``effective_gate`` by a caller that happens to have them.
    #: ``None`` when unresolved (matches every pre-existing constructor call
    #: this dataclass had before this fix, none of which knew about these
    #: axes -- so their exit-code behavior is completely unaffected, only
    #: this object's/``EffectiveGate``'s own digest-facing fields gain a
    #: value).
    on_incomplete_scope: str | None = None
    fail_on_removed_library: bool | None = None

    @property
    def exit_code_scheme(self) -> str:
        """``"severity"`` when a severity setting is in effect, else ``"legacy"``.

        Derived through the one shared
        :func:`~abicheck.policy.gate_pack_fold.gate_exit_code_scheme` rule
        that ``ResolvedCompareConfig`` and the
        ``CompatibilityEvaluationConfig`` front end also state their scheme
        with, so the release fan-out cannot drift from either.
        """
        return gate_exit_code_scheme(self.severity is not None)

    @property
    def effective_gate(self) -> EffectiveGate:
        """This object, projected onto the plan's shared
        :class:`~abicheck.policy.effective_gate.EffectiveGate` shape
        (duplication-and-convergence-assessment P0) -- the same shape
        ``cli_helpers_compare.ResolvedCompareConfig.effective_gate`` exposes
        for single-pair ``compare``, so a caller holding either object can
        ask the identical question. The release fan-out resolves no
        ``--require-complete-analysis``/scoped-gate (``--used-by``/
        ``--required-symbol``) concept of its own at this object's scope --
        those are per-library, single-pair facts -- so both stay at their
        default (``False``/``None``) here; a caller that does have one
        should build an :class:`EffectiveGate` directly via
        :meth:`EffectiveGate.from_severity` instead of through this
        property. ``on_incomplete_scope``/``fail_on_removed_library`` ARE
        this object's own scope (see this dataclass's own field docstring),
        so they pass straight through instead of defaulting away.
        """
        return EffectiveGate.from_severity(
            self.severity,
            on_incomplete_scope=self.on_incomplete_scope,
            fail_on_removed_library=self.fail_on_removed_library,
        )


def resolve_release_gate_options(
    pack_application: _GatePackApplication | None,
    *,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
    on_incomplete_scope: str | None = None,
    fail_on_removed_library: bool | None = None,
) -> GateOptions:
    """Resolve the release fan-out's :class:`GateOptions` exactly once.

    Folds a selected ``kind: gate`` pack's severity contribution
    (:func:`apply_release_gate_pack`), then resolves the severity config
    (:func:`_resolve_release_severity_config`). The one automatic gate
    algorithm (ADR-064/CLI cleanup phase two PR G2) needs nothing stated
    here at all any more: :attr:`GateOptions.exit_code_scheme` derives
    itself from whether a severity setting ended up in effect
    (``severity is not None``) -- there is no manual override any more.
    (Before PR G2, an explicit
    ``--exit-code-scheme``/``.abicheck.yml``/pack override could force
    either direction regardless of what severity configuration was
    present; removed along with the CLI flag, the config key, and the
    pack field.)

    *on_incomplete_scope*/*fail_on_removed_library* (Codex review, PR #1192,
    third follow-up round) carry straight through onto the returned
    :class:`GateOptions` unchanged -- this function resolves no fold or
    default for either (the caller's own already-resolved ``--on-
    incomplete-scope``/``--fail-on-removed-library`` values are the single
    source, the same ones the real exit-code computation elsewhere in the
    release fan-out already reads); they exist as parameters here purely so
    ``GateOptions``/``EffectiveGate`` can carry the identical values a
    caller already has, rather than a caller reaching around this
    resolution to set them by hand afterward.
    """
    (
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
    ) = apply_release_gate_pack(
        pack_application,
        severity_preset=severity_preset,
        severity_abi_breaking=severity_abi_breaking,
        severity_potential_breaking=severity_potential_breaking,
        severity_quality_issues=severity_quality_issues,
        severity_addition=severity_addition,
    )
    severity_config = _resolve_release_severity_config(
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
    )
    return GateOptions(
        severity_preset=severity_preset,
        severity=severity_config,
        on_incomplete_scope=on_incomplete_scope,
        fail_on_removed_library=fail_on_removed_library,
    )
