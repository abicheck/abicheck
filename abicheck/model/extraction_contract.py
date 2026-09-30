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

"""The extraction contract and dependency ledger a snapshot was produced under.

``ExtractionContract`` records the scope and profile fingerprints ADR-050's
comparability gate compares two snapshots on; ``DependencyInfo`` records the
resolved dependency graph a scan observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DependencyInfo:
    """Resolved transitive dependency graph and symbol bindings.

    Populated when a snapshot is created with ``--follow-deps``.
    """

    nodes: list[dict[str, object]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    bindings_summary: dict[str, int] = field(default_factory=dict)
    missing_symbols: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExtractionContract:
    """ADR-050 D1 — profile/scope fingerprints proving two snapshots were
    extracted under a comparable contract, plus the resolved per-field
    inputs each fingerprint was computed from (so a mismatch report can show
    *what* differs, not just that the hashes don't match).

    Built by ``abicheck.comparability.compute_extraction_contract`` — never
    constructed by hand outside tests. Both fingerprints are independently
    optional: a symbols-only dump with no header-AST inputs but a real
    a public-header set still attaches a
    ``scope_fingerprint`` with ``profile_fingerprint=None`` (see that
    module's docstring for the full rationale).
    """

    profile_fingerprint: str | None = None
    scope_fingerprint: str | None = None
    # Named resolved sub-inputs, one string per component, keyed the same way
    # on both sides of a compare so a mismatch can be attributed to a specific
    # field instead of an opaque hash. See ``comparability.PROFILE_FIELD_KEYS``
    # / ``comparability.SCOPE_FIELD_KEYS`` for the recognized keys.
    profile_fields: dict[str, str] = field(default_factory=dict)
    scope_fields: dict[str, str] = field(default_factory=dict)
    # E-S1 (docs/contribute/plans/vision-api-abi-evolution.md, "E. Evidence
    # adequacy, contract-source conflicts, cross-profile comparison" /
    # cli-cleanup-phase-two.md Block 5): the toolchain-identity probe's own
    # ``model.availability.FactStatus.value`` behind this side's
    # ``profile_fields["compiler_family"]`` -- ``"present"`` when a host
    # compiler identity was actually resolved, ``"failed"`` when resolution
    # was attempted and errored (``dumper_toolchain._stamp_ast_parser``'s own
    # ``compiler_error``), or ``None`` when this contract predates the field
    # or no L2 frontend ran at all (nothing was ever attempted -- not a gap,
    # since nothing here asserts one). A plain ``str``, like every other
    # field on this dataclass, not a ``FactStatus`` instance itself: this
    # keeps the whole dataclass serializing through the ordinary
    # ``dataclasses.asdict()``/``json.dumps()`` round-trip
    # ``serialization.py`` already uses for it, with no new codec --
    # callers translate to/from ``FactStatus`` at the two edges
    # (``dumper_toolchain.py`` writes it, ``comparability_profile.py`` reads
    # it back via ``FactStatus(value)``). Deliberately never silently
    # collapsed into an absent/``None`` ``compiler_family`` on a probe
    # failure (see ``dumper_toolchain._compiler_family_from_toolchain``) --
    # that indistinguishability from "never attempted" is exactly what
    # previously let a mismatched GCC/Clang pair compare silently.
    compiler_identity_status: str | None = None


#: Version of the build-identity record :func:`build_identity_of` derives.
#: Bumped when the set of components that name "the build that produced this
#: evidence" changes, so a reader can tell an identity recorded under an older
#: rule from one recorded under the current one.
BUILD_IDENTITY_VERSION = 1


@dataclass(frozen=True)
class BuildIdentity:
    """Which build system, generator and root-target scope produced one side's
    L3 build evidence (WS-A / integration-lab P0.7 "build-system axis").

    Derived, never stored separately: every component already lives in the
    snapshot's own persisted ``build_source.build_evidence`` (``generators``
    and ``target_scope``), so a snapshot written before this record existed
    yields the same identity a fresh one does -- there is no second copy that
    could drift from the evidence it describes.

    ``build_systems`` is ``()`` when the evidence names no generator (a bare
    ``compile_commands.json`` carries none): the build system is then
    *unrecorded*, not "none". ``root_targets`` is ``None`` when no root-target
    scoping was requested (a workspace-wide collection) and the sorted
    requested labels otherwise -- the two are different extractions.
    Generator *versions* are deliberately excluded: a CMake point release is
    not a different build system and must not refuse a comparison.
    """

    build_systems: tuple[tuple[str, str], ...]
    root_targets: tuple[str, ...] | None
    version: int = BUILD_IDENTITY_VERSION

    @property
    def build_system_recorded(self) -> bool:
        return bool(self.build_systems)

    def describe_build_system(self) -> str:
        if not self.build_systems:
            return "unrecorded"
        return ", ".join(
            f"{kind} ({gen})" if gen else kind for kind, gen in self.build_systems
        )

    def describe_root_targets(self) -> str:
        if self.root_targets is None:
            return "unscoped (workspace-wide)"
        return ", ".join(self.root_targets) or "<none>"


def build_identity_of(snapshot: object) -> BuildIdentity | None:
    """*snapshot*'s :class:`BuildIdentity`, or ``None`` when it carries no L3
    build evidence at all (nothing it extracted is attributable to a build).

    Duck-typed over ``snapshot.build_source.build_evidence`` so this model
    module does not import ``buildsource``.
    """
    pack = getattr(snapshot, "build_source", None)
    evidence = getattr(pack, "build_evidence", None) if pack is not None else None
    if evidence is None:
        return None
    pairs = {
        (
            str(getattr(g, "kind", "") or "").strip().lower(),
            str(getattr(g, "generator", "") or "").strip(),
        )
        for g in getattr(evidence, "generators", None) or ()
    }
    # A "generic" (or kind-less) entry names no build system, whatever
    # backend string it carries.
    systems = sorted(p for p in pairs if p[0] not in ("", "generic"))
    scope = getattr(evidence, "target_scope", None)
    roots = (
        None
        if scope is None or not getattr(scope, "requested", None)
        else tuple(sorted({str(r) for r in scope.requested}))
    )
    return BuildIdentity(build_systems=tuple(systems), root_targets=roots)


#: What a build-identity divergence leaves unverified: the build drives the
#: compile flags the header AST was parsed under (declaration/layout) and the
#: L3-L5 source graph itself (source).
BUILD_IDENTITY_DIMENSIONS = frozenset({"declaration", "layout", "source"})


def build_identity_divergence(old: object, new: object) -> tuple[str, bool] | None:
    """``(reason, fatal)`` when two snapshots' build identities diverge, else
    ``None``.

    * Either side carries no L3 build evidence: not applicable (``None``).
    * Both record a build system and they differ, or both carry evidence and
      the requested root targets differ (including scoped vs. unscoped):
      **fatal** -- the two sides are different extractions, and diffing them
      would report build-system drift as ABI change.
    * Exactly one side records a build system (the other's evidence names no
      generator, e.g. a pre-identity baseline built from a bare compile DB):
      **non-fatal** -- the comparison runs, but that it compares the same
      build cannot be verified, so the result is bounded rather than read as
      a clean pass (absent is not removed; weaker evidence narrows).
    """
    a, b = build_identity_of(old), build_identity_of(new)
    if a is None or b is None:
        return None
    if a.root_targets != b.root_targets:
        return (
            "build identity: root targets differ (old: "
            f"{a.describe_root_targets()}; new: {b.describe_root_targets()}) -- "
            "the two sides' build evidence covers different target scopes",
            True,
        )
    if a.build_system_recorded and b.build_system_recorded:
        if a.build_systems != b.build_systems:
            return (
                "build identity: build system differs (old: "
                f"{a.describe_build_system()}; new: {b.describe_build_system()}) "
                "-- produce the baseline under the same build system/generator",
                True,
            )
        return None
    if a.build_system_recorded != b.build_system_recorded:
        return (
            "build identity: build system recorded on one side only (old: "
            f"{a.describe_build_system()}; new: {b.describe_build_system()}) -- "
            "comparison runs, but that both sides come from the same build "
            "system is unverified",
            False,
        )
    return None
