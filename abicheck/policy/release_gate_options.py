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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

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

    The release fan-out has no ``ResolvedCompareConfig``-shaped object of
    its own to fold onto the way :func:`~abicheck.pack_application.
    apply_to_compare_config` does for a single-pair ``compare`` -- its
    severity resolution is a set of raw CLI-or-config strings, re-derived
    at several call sites, so applying a pack-supplied
    ``gate.severity.<category>`` is necessarily shaped differently here
    (overriding one of five independent optional raw strings, only ever
    reached when nothing more explicit -- ``--severity-<category>``/
    ``.abicheck.yml`` -- already stated it, since :func:`~abicheck.
    pack_application.pack_application` already excludes a field an explicit
    source shadowed) than it is for ``apply_to_compare_config`` (a single
    ``dataclasses.replace`` on an already-resolved ``SeverityConfig``).

    A no-op when *pack_application* is ``None`` (no ``--pack`` given) or
    contributed no severity level -- every pre-existing invocation reaches
    the five inputs completely unchanged.
    """
    if pack_application is None:
        return (
            severity_preset,
            severity_abi_breaking,
            severity_potential_breaking,
            severity_quality_issues,
            severity_addition,
        )
    levels = pack_application.severity_levels
    if levels:
        severity_abi_breaking = levels.get("abi_breaking", severity_abi_breaking)
        severity_potential_breaking = levels.get(
            "potential_breaking", severity_potential_breaking
        )
        severity_quality_issues = levels.get("quality_issues", severity_quality_issues)
        severity_addition = levels.get("addition", severity_addition)
    return (
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
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
    own severity field already gives `compare`/`scan`. ``exit_code_scheme``
    is kept alongside it, purely derived (``"severity"`` when ``severity``
    is not ``None``, else ``"legacy"``) for provenance/reporting (e.g. a
    dry-run scheme label) -- it is not authoritative for "should severity be
    folded", ``severity`` is, and it is no longer a settable input anywhere
    in this module.
    """

    exit_code_scheme: str
    severity_preset: str | None
    severity: SeverityConfig | None


def resolve_release_gate_options(
    pack_application: _GatePackApplication | None,
    *,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> GateOptions:
    """Resolve the release fan-out's :class:`GateOptions` exactly once.

    Folds a selected ``kind: gate`` pack's severity contribution
    (:func:`apply_release_gate_pack`), then resolves the severity config
    (:func:`_resolve_release_severity_config`). The one automatic gate
    algorithm (ADR-064/CLI cleanup phase two PR G2): ``exit_code_scheme`` is
    ``"severity"`` exactly when a severity setting ended up in effect
    (``severity_config is not None``), else ``"legacy"`` -- there is no
    manual override any more. (Before PR G2, an explicit
    ``--exit-code-scheme``/``.abicheck.yml``/pack override could force
    either direction regardless of what severity configuration was
    present; removed along with the CLI flag, the config key, and the
    pack field.)
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
        exit_code_scheme="severity" if severity_config is not None else "legacy",
        severity_preset=severity_preset,
        severity=severity_config,
    )
