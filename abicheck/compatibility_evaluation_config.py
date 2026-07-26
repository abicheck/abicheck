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

"""ADR-049 Phase 1 slice 1: the ``CompatibilityEvaluationConfig`` typed object.

This module implements ADR-049 D7's "one typed effective configuration"
(plan Section 3.1) as a composition of small immutable dataclasses, plus
:class:`ValueProvenance` (Section 3.2's field-level provenance record). It
does **not** implement the resolver: no front end (CLI, ``.abicheck.yml``,
service/API, MCP) constructs one of these objects yet, and no per-field
precedence code exists here. That is the remainder of Phase 1 (plan Section
9), tracked separately in ``docs/contribute/plans/public-contract-default.md``.

Two existing, already-shipped types are reused rather than duplicated:

- :class:`~abicheck.change_registry_types.Verdict` for
  :attr:`CompatibilityPolicyConfig.overrides` (ADR-049 D8's per-``ChangeKind``
  override, e.g. ``soname_bump_recommended: break``).
- :class:`~abicheck.severity.SeverityConfig` for :attr:`GateConfig.severity`
  (the existing four-category ``abi_breaking``/``potential_breaking``/
  ``quality_issues``/``addition`` severity resolution ADR-049 D6 calls
  ``gate.severity_overrides``) -- there is already one severity model in this
  codebase and this module composes it instead of inventing a second one.

Every dataclass here is frozen. Container fields (mappings/sequences) are
normalized to :class:`types.MappingProxyType`/``tuple`` in ``__post_init__``
so a caller's later mutation of the collection it passed in cannot silently
change an already-constructed, supposedly-immutable config (ADR-049 D7:
equivalent semantic inputs must resolve to an *equal* object, which requires
the object to actually stay put).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from .change_registry_types import Verdict
from .contract_relevance_types import ContractMode, SelectorLayer
from .severity import SeverityConfig


def _frozen_mapping(m: Mapping[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType(dict(m))


def _frozen_tuple(s: Sequence[object]) -> tuple[object, ...]:
    return tuple(s)


def _require_digest_when_nonempty(
    items: Sequence[object], sha256: str | None, *, owner: str
) -> None:
    if items and not sha256:
        raise ValueError(
            f"{owner}.sha256 is required when its item/rule list is "
            "non-empty (ADR-049 D6): the digest identifies the external "
            "source that produced the list, not the list's contents, so a "
            "source change is detectable even when the names stay the same."
        )


# --------------------------------------------------------------------------
# D7: field-level provenance.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectedByEntry:
    """One hop in a field's selection chain (ADR-049 D7 ``selected_by``)."""

    layer: SelectorLayer
    option: str | None = None
    argument_index: int | None = None
    path: str | None = None


@dataclass(frozen=True)
class ValueProvenance:
    """Where one effective-config field's (or manifest's) value came from.

    A manifest is an *input selected by* a layer, not a precedence layer of
    its own (ADR-049 D7): a policy manifest picked by ``--policy-file`` has
    ``layer=EXPLICIT_CLI``, while the same manifest referenced from
    ``.abicheck.yml`` has ``layer=PROJECT_CONFIG`` -- the ``reference``/
    ``path``/``sha256`` identify *which* manifest, ``layer`` identifies *how
    it was chosen for this run*. ``version`` records the manifest's own
    immutable version separately from ``reference`` (its name/id): D7 states
    "path, digest, manifest identity/version, and field location identify
    the actual definition used for exact replay" -- a manifest can be
    revised under the same ``reference`` name, and only ``version`` (plus
    ``sha256``) can tell two revisions apart.
    """

    layer: SelectorLayer
    source_kind: str | None = None
    reference: str | None = None
    version: int | None = None
    path: str | None = None
    sha256: str | None = None
    field_location: str | None = None
    selected_by: tuple[SelectedByEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "selected_by", _frozen_tuple(self.selected_by))


# --------------------------------------------------------------------------
# D6/D7: immutable identity for a base/preset/pack (id + version + digest),
# needed for exact replay.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ImmutableIdentity:
    """Identity of a versioned, replayable base/preset/pack/provider impl.

    ``sha256`` is required, not optional: every worked example in ADR-049 D6/
    D7 shows a populated digest alongside ``id``/``version`` (e.g.
    ``{id: strict_abi, version: 1, sha256: "..."}``), and D6 states plainly
    that "every selected provider/base/preset/pack or rule set carries an
    immutable identity/version/digest" for exact replay. An identity with no
    digest cannot detect drift if the same id/version is redefined.
    """

    id: str
    version: int
    sha256: str


@dataclass(frozen=True)
class DigestedItems:
    """A content-addressed item list (ADR-049 D6: ``{items, sha256}``).

    ``sha256`` digests the *external source* that produced ``items`` (a
    variant-definition file, an explicit-scope manifest, ...), not the
    ``items`` tuple itself: two definitions can produce the same item names
    while differing in content, and only an externally supplied digest can
    catch that drift on replay. ``sha256`` is unconditionally required --
    including when ``items`` is empty -- because "a source was selected and
    it currently resolves to zero items" and "no source was selected at
    all" are different facts: if the digest were dropped for the empty case,
    the source could later gain items with no way to prove it was genuinely
    empty (as opposed to unconsidered) at decision time. A field that has no
    source to select at all (the common case) represents that as
    ``DigestedItems | None = None`` at the container level -- see
    :attr:`EvidenceConfig.variants` / :attr:`SurfaceConfig.explicit_scope` --
    rather than as an empty, digest-less ``DigestedItems``.
    """

    sha256: str
    items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", _frozen_tuple(self.items))


# --------------------------------------------------------------------------
# D7 namespaces: contract / evidence / surface / assurance / policy / gate /
# suppressions.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractConfig:
    """What is promised (ADR-049 D2/D8): mode, unresolved behavior, overlays."""

    mode: ContractMode
    #: ADR-049 D9's ``unresolved_behavior`` -- ``"not_checkable"`` (default,
    #: contributes the orthogonal coverage exit 1) or ``"warn"`` (the only
    #: ordinary mechanism for accepting incomplete contract coverage).
    unresolved: str = "not_checkable"
    overlays: tuple[str, ...] = ()
    #: Contract/language packs (e.g. ``rust_c_ffi``) defining an FFI
    #: boundary and its closure (ADR-049 D8). Versioned identities, not bare
    #: slugs: ADR-049 D6 requires "every selected provider/base/preset/pack
    #: or rule set carries an immutable identity/version/digest" so a pack
    #: revised under the same name can still be told apart for exact replay.
    packs: tuple[ImmutableIdentity, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "overlays", _frozen_tuple(self.overlays))
        object.__setattr__(self, "packs", _frozen_tuple(self.packs))


@dataclass(frozen=True)
class EvidenceProviderRequirement:
    """One evidence capability's requiredness and pinned implementation."""

    capability: str
    required: bool
    implementation: ImmutableIdentity | None = None


@dataclass(frozen=True)
class EvidenceConfig:
    """Providers, requirements, and variants (ADR-049 D5/D6)."""

    providers: tuple[EvidenceProviderRequirement, ...] = ()
    #: Declared compile/generated-header variant set (ADR-049 D5: "projects
    #: with configuration-dependent declarations must declare the variant
    #: set; all required variants must complete"). Content-digested, per
    #: ADR-049 D6's ``variants: {items: [], sha256: "..."}``. ``None`` means
    #: no variant source was selected at all; a ``DigestedItems`` with
    #: ``items=()`` means a source was selected and resolved to none.
    variants: DigestedItems | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "providers", _frozen_tuple(self.providers))


@dataclass(frozen=True)
class SurfaceConfig:
    """Explicit scope and surface hints (ADR-049 D8).

    ``internal_namespaces`` and similar hints inform reachability/
    out-of-contract proofs but cannot themselves demote a proven public fact
    (ADR-049 D8: surface hints "cannot themselves silently demote a public
    fact"); ADR-049 D6's illustrative ``hints: {internal_namespaces: []}``
    carries no digest, unlike ``explicit_scope``, so it stays a plain tuple
    here too.

    ``explicit_scope`` is content-digested (ADR-049 D6:
    ``explicit_scope: {items: [], sha256: "..."}``) since it directly
    decides root/closure membership and must detect drift on replay.
    ``None`` means no explicit-scope source was selected at all; a
    ``DigestedItems`` with ``items=()`` means a source was selected and
    resolved to no entries.
    """

    explicit_scope: DigestedItems | None = None
    internal_namespaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "internal_namespaces", _frozen_tuple(self.internal_namespaces)
        )


@dataclass(frozen=True)
class AssuranceConfig:
    """Evidence/coverage requirements (ADR-049 D8)."""

    require_evidence: bool = True


@dataclass(frozen=True)
class CompatibilityPolicyConfig:
    """What a change to an in-contract entity means (ADR-049 D8).

    ``overrides`` is the explicit per-``ChangeKind`` override that wins over
    every selected pack and the base policy (ADR-049 D8 composition order:
    "explicit per-ChangeKind override > selected packs > base policy").
    """

    base: ImmutableIdentity
    packs: tuple[ImmutableIdentity, ...] = ()
    overrides: Mapping[str, Verdict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packs", _frozen_tuple(self.packs))
        object.__setattr__(self, "overrides", _frozen_mapping(self.overrides))


@dataclass(frozen=True)
class GateConfig:
    """What blocks CI (ADR-049 D8) -- ``NOT_APPLICABLE`` to contract membership.

    ``severity`` reuses the existing :class:`~abicheck.severity.SeverityConfig`
    four-category model rather than inventing a second severity vocabulary.
    """

    exit_code_scheme: str = "severity"
    preset: ImmutableIdentity | None = None
    packs: tuple[ImmutableIdentity, ...] = ()
    severity: SeverityConfig = field(default_factory=SeverityConfig)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packs", _frozen_tuple(self.packs))


@dataclass(frozen=True)
class SuppressionConfig:
    """Immutable rules and digest (ADR-049 D7).

    ``sha256`` is required whenever ``rules`` is non-empty (ADR-049 D6:
    ``suppressions: {rules: [], sha256: "..."}`` -- same content-digest
    requirement as :class:`DigestedItems`, kept as its own field pair here
    since the ADR's own key is ``rules``, not ``items``).
    """

    rules: tuple[str, ...] = ()
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", _frozen_tuple(self.rules))
        _require_digest_when_nonempty(
            self.rules, self.sha256, owner="SuppressionConfig"
        )


# --------------------------------------------------------------------------
# D7: the one typed effective configuration.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CompatibilityEvaluationConfig:
    """All verdict-emitting comparison paths consume this one object.

    Resolved once at the Tier-2 service boundary and passed to ``compare``,
    the baseline-comparison portion of ``scan --against``, the Python/service
    API, release/package fan-out, and MCP/other adapters (ADR-049 D7). This
    class only carries the *shape*; no resolver in this codebase constructs
    one from real CLI/config/recipe input yet (see module docstring).
    """

    contract: ContractConfig
    evidence: EvidenceConfig
    surface: SurfaceConfig
    assurance: AssuranceConfig
    policy: CompatibilityPolicyConfig
    gate: GateConfig
    suppressions: SuppressionConfig
    provenance: Mapping[str, ValueProvenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", _frozen_mapping(self.provenance))
