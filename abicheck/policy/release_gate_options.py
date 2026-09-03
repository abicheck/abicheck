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

from .severity import PRESET_DEFAULT, SeverityConfig, resolve_severity_config

#: The three spellings `--exit-code-scheme`/`.abicheck.yml`'s `exit_code_
#: scheme` accept (`cli_scan.py`'s own `click.Choice`, mirrored by
#: `compare`'s equivalent option) -- `None` (not stated) is separately
#: valid at every call site below, so it isn't included here.
_VALID_EXIT_CODE_SCHEMES = ("auto", "legacy", "severity")


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
    (read-only) dataclass fields can never satisfy structurally."""

    @property
    def severity_levels(self) -> Mapping[str, Any]: ...
    @property
    def exit_code_scheme(self) -> str | None: ...
    @property
    def resolved_exit_code_scheme(self) -> str | None: ...


def apply_release_gate_pack(
    pack_application: _GatePackApplication | None,
    *,
    release_exit_code_scheme: str | None,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None]:
    """Fold a selected ``kind: gate`` pack's contribution into the release
    fan-out's own raw exit-code-scheme/severity inputs (CLI cleanup phase
    two, "PR B" slice 2).

    Returns ``(release_exit_code_scheme, severity_preset,
    severity_abi_breaking, severity_potential_breaking,
    severity_quality_issues, severity_addition)`` -- the exact six raw
    values. Since ADR-064's ``GateOptions`` rewrite, callers should not call
    this directly: :func:`resolve_release_gate_options` calls it exactly
    once, ahead of the one resolution into :class:`GateOptions` every
    downstream severity/exit-code consumer (``_compute_release_severity_
    exit_code``, ``_fold_release_global_severity``, and the per-library
    JSON write, via ``GateOptions.severity``) now reads instead of
    independently re-deriving from the raw strings -- this function stays a
    separate, directly-unit-tested step of that pipeline rather than being
    inlined into it.

    The release fan-out has no ``ResolvedCompareConfig``-shaped object of
    its own to fold onto the way :func:`~abicheck.pack_application.
    apply_to_compare_config` does for a single-pair ``compare`` -- its
    severity/exit-code-scheme resolution is a set of raw CLI-or-config
    strings, re-derived at several call sites. So this mirrors that
    function's *logic* against the release fan-out's raw-string shape
    instead: a pack-supplied ``gate.severity.<category>`` overrides the
    matching raw string (only ever reached when nothing more explicit --
    ``--severity-<category>``/``.abicheck.yml`` -- already stated it,
    since :func:`~abicheck.pack_application.pack_application` already
    excludes a field an explicit source shadowed), and a pack-supplied
    ``gate.exit_code_scheme`` overrides *that* raw string the same way --
    with the identical "resolver's own already-decided ``auto`` answer,
    not a re-derivation" fallback when only a severity level moved and no
    scheme was directly assigned (see ``apply_to_compare_config``'s own
    docstring for why re-deriving one here would be wrong: a severity
    level *is* severity being configured, and the resolver's own
    ``resolved_exit_code_scheme`` already reflects that while still
    letting an explicit ``--exit-code-scheme``/``.abicheck.yml`` value
    outrank it).

    A no-op when *pack_application* is ``None`` (no ``--pack`` given) or
    contributed neither field -- every pre-existing invocation reaches the
    six inputs completely unchanged.
    """
    if pack_application is None:
        return (
            release_exit_code_scheme,
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
    scheme = pack_application.exit_code_scheme
    if scheme is None and levels:
        scheme = pack_application.resolved_exit_code_scheme
    if scheme is not None:
        release_exit_code_scheme = scheme
    return (
        release_exit_code_scheme,
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
    together both ways that can happen (an explicitly forced ``legacy``
    scheme, or no severity configuration at all), the same simplification
    ``ResolvedCompareConfig``'s own severity field already gives
    `compare`/`scan`. ``exit_code_scheme``/``severity_preset`` are kept
    alongside it for provenance/reporting (e.g. a dry-run scheme label) --
    they are not authoritative for "should severity be folded", ``severity``
    is.
    """

    exit_code_scheme: str | None
    severity_preset: str | None
    severity: SeverityConfig | None


def resolve_release_gate_options(
    pack_application: _GatePackApplication | None,
    *,
    release_exit_code_scheme: str | None,
    severity_preset: str | None,
    severity_abi_breaking: str | None,
    severity_potential_breaking: str | None,
    severity_quality_issues: str | None,
    severity_addition: str | None,
) -> GateOptions:
    """Resolve the release fan-out's :class:`GateOptions` exactly once.

    Folds a selected ``kind: gate`` pack's contribution
    (:func:`apply_release_gate_pack`), then resolves the severity config
    (:func:`_resolve_release_severity_config`) and applies the same two
    scheme-dependent corrections ``compare_release_cmd`` used to apply at
    its own call site: a resolved ``exit_code_scheme == "severity"`` with no
    severity setting actually in effect falls back to
    :data:`PRESET_DEFAULT` (mirroring single-pair ``compare``'s
    ``ResolvedCompareConfig.severity``, which is unconditionally populated
    and only *gated* by scheme, never left ``None``); an explicit
    ``exit_code_scheme == "legacy"`` clears the severity config regardless
    of what was otherwise configured, so a forced legacy run never scores a
    severity-based exit even if a severity block is technically present.
    """
    (
        release_exit_code_scheme,
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
    ) = apply_release_gate_pack(
        pack_application,
        release_exit_code_scheme=release_exit_code_scheme,
        severity_preset=severity_preset,
        severity_abi_breaking=severity_abi_breaking,
        severity_potential_breaking=severity_potential_breaking,
        severity_quality_issues=severity_quality_issues,
        severity_addition=severity_addition,
    )
    if (
        release_exit_code_scheme is not None
        and release_exit_code_scheme not in _VALID_EXIT_CODE_SCHEMES
    ):
        # Every caller here is past its own front-end's usage-error
        # handling (Click's `--exit-code-scheme` is a `click.Choice`; a
        # pack's `gate.exit_code_scheme` is validated at load time) --
        # except a typed `CompareRequest`/`ScanRequest` caller, which has
        # none: an unchecked, misspelled scheme (e.g. "legacy " with
        # trailing whitespace) would otherwise silently fail neither the
        # `== "severity"`/`== "legacy"` branch below, so a caller that also
        # set a severity_preset would get the severity algorithm merely
        # because `severity_config` came back non-None -- a breaking
        # change could then exit 0 instead of the misspelling being
        # rejected (Codex review, PR #1032). `ValueError`, matching
        # `resolve_severity_config`'s own contract for an invalid
        # `severity_preset` just below.
        raise ValueError(
            f"invalid exit_code_scheme {release_exit_code_scheme!r}; "
            f"must be one of {_VALID_EXIT_CODE_SCHEMES} or None"
        )
    severity_config = _resolve_release_severity_config(
        severity_preset,
        severity_abi_breaking,
        severity_potential_breaking,
        severity_quality_issues,
        severity_addition,
    )
    if release_exit_code_scheme == "severity" and severity_config is None:
        severity_config = PRESET_DEFAULT
        severity_preset = "default"
    if release_exit_code_scheme == "legacy":
        severity_config = None
    return GateOptions(
        exit_code_scheme=release_exit_code_scheme,
        severity_preset=severity_preset,
        severity=severity_config,
    )
