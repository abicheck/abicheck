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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeVar

from .change_registry_types import Verdict
from .checker_policy import ChangeKind
from .contract_relevance_types import ContractMode, SelectorLayer
from .severity import SeverityConfig

_T = TypeVar("_T")


def _frozen_mapping(m: Mapping[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType(dict(m))


def _frozen_tuple(s: Sequence[object]) -> tuple[object, ...]:
    return tuple(s)


def _canonical_tuple(s: Sequence[_T], *, key: Callable[[_T], Any]) -> tuple[_T, ...]:
    """Sort+dedupe an unordered selection into a stable, canonical tuple.

    ADR-049 D8: "Pack order never decides semantics," and D7 requires
    "equivalent semantic inputs must resolve to an equivalent object" --
    two constructions that select the same packs/providers in a different
    order must compare equal. Sorting by a stable key (rather than leaving
    insertion order, or discarding order entirely via a set) gives both
    order-independent equality and a deterministic serialization order.
    Also drops exact duplicates: D7 "equivalent duplicate values are
    accepted" -- the same pack selected twice (e.g. once via a recipe
    default, once via an explicit CLI flag) must resolve the same as
    selecting it once. ``dict.fromkeys`` on the already-sorted sequence
    dedupes by equality while keeping the sorted order.
    """
    return tuple(dict.fromkeys(sorted(s, key=key)))


def _require_nonempty_digest(sha256: str, *, owner: str) -> None:
    if not sha256:
        raise ValueError(
            f"{owner}.sha256 must be a non-empty digest (ADR-049 D6): an "
            "empty string is exactly as unable to detect content drift on "
            "replay as no digest at all."
        )


# ADR-049 D9's unresolved_behavior is a closed, two-value vocabulary this
# module owns outright (every worked example in the ADR uses exactly one of
# these two strings) -- unlike e.g. ValueProvenance.source_kind, which is
# free-form text belonging to a not-yet-written resolver.
_VALID_UNRESOLVED_BEHAVIORS = frozenset({"not_checkable", "warn"})

# ADR-037 D12's exit-code scheme, minus "auto": cli.py's --exit-code-scheme
# click.Choice is {"auto", "legacy", "severity"}, but "auto" is resolved to
# one of the other two before an effective GateConfig is ever constructed.
_VALID_EXIT_CODE_SCHEMES = frozenset({"legacy", "severity"})

# ADR-049 D8: "An unknown ChangeKind in a custom policy is a hard load
# error" -- policy_file.py's PolicyFile.load() already enforces this for
# --policy-file YAML; CompatibilityPolicyConfig.overrides must reject the
# same typo'd/renamed slug regardless of which front end (service/API/
# manifest adapter) constructs it directly, not only the YAML path.
_VALID_CHANGE_KIND_SLUGS: frozenset[str] = frozenset(k.value for k in ChangeKind)


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

    ``shadowed_legacy`` is populated only by the documented ``--policy`` /
    ``--policy-file`` compatibility exception (D7): when
    ``compatibility_evaluation_resolver.resolve_field`` is called with
    ``require_legacy_alias_agreement=False`` and the legacy alias disagrees
    with the explicit value, the explicit value still wins, but D7 requires
    "provenance records the file-selected effective base and the shadowed
    ``--policy`` input" -- this field carries that suppressed legacy
    candidate's own provenance for audit/replay.
    """

    layer: SelectorLayer
    source_kind: str | None = None
    reference: str | None = None
    version: int | None = None
    path: str | None = None
    sha256: str | None = None
    field_location: str | None = None
    selected_by: tuple[SelectedByEntry, ...] = ()
    shadowed_legacy: ValueProvenance | None = None

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

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError(
                "ImmutableIdentity.id must be non-empty (ADR-049 D6): a "
                "persisted provider/base/preset/pack needs its identity "
                "name to say what the digest represents, the same "
                "replay-exactness guarantee sha256 already carries."
            )
        _require_nonempty_digest(self.sha256, owner="ImmutableIdentity")


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
        _require_nonempty_digest(self.sha256, owner="DigestedItems")


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
        if not isinstance(self.mode, ContractMode):
            raise TypeError(
                f"ContractConfig.mode must be a ContractMode member, not "
                f"{self.mode!r} -- the `mode: ContractMode` annotation isn't "
                "runtime-enforced, so an untyped service/manifest/API "
                "adapter could otherwise pass a typo'd string (e.g. "
                '"pubic") through to a state with no defined '
                "contract-membership semantics (ADR-049 D2)."
            )
        if self.unresolved not in _VALID_UNRESOLVED_BEHAVIORS:
            raise ValueError(
                f"ContractConfig.unresolved must be one of "
                f"{sorted(_VALID_UNRESOLVED_BEHAVIORS)} (ADR-049 D9), got "
                f"{self.unresolved!r}"
            )
        object.__setattr__(self, "overlays", _frozen_tuple(self.overlays))
        object.__setattr__(
            self, "packs", _canonical_tuple(self.packs, key=_pack_sort_key)
        )


def _pack_sort_key(identity: ImmutableIdentity) -> tuple[str, int, str]:
    # sha256 breaks ties between two entries that share (id, version) but
    # disagree on content -- without it a stable sort leaves such entries in
    # input order, so reversing the input would produce an unequal config.
    return (identity.id, identity.version, identity.sha256)


@dataclass(frozen=True)
class EvidenceProviderRequirement:
    """One evidence capability's requiredness and pinned implementation.

    ``implementation`` is required unconditionally, whether ``required`` is
    ``True`` or ``False``: ADR-049 D6 states "every selected provider ...
    carries an immutable identity/version/digest" with no carve-out for
    optional (enrichment) providers -- ``required`` governs whether missing
    *evidence* blocks evaluation, not whether a *selected* provider's
    implementation is recorded for replay. A provider that was not selected
    for a given run is represented by omitting its
    :class:`EvidenceProviderRequirement` entry from
    :attr:`EvidenceConfig.providers` entirely, not by an entry with
    ``implementation=None``.
    """

    capability: str
    required: bool
    implementation: ImmutableIdentity


def _provider_sort_key(
    req: EvidenceProviderRequirement,
) -> tuple[str, bool, str, int, str]:
    # implementation.sha256 breaks ties the same way _pack_sort_key's does.
    impl = req.implementation
    return (req.capability, req.required, impl.id, impl.version, impl.sha256)


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
        object.__setattr__(
            self, "providers", _canonical_tuple(self.providers, key=_provider_sort_key)
        )


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
        unknown = sorted(set(self.overrides) - _VALID_CHANGE_KIND_SLUGS)
        if unknown:
            raise ValueError(
                f"CompatibilityPolicyConfig.overrides has unknown "
                f"ChangeKind slugs: {unknown} (ADR-049 D8: a hard load "
                "error, matching policy_file.py's PolicyFile.load)"
            )
        non_verdict = sorted(
            slug for slug, v in self.overrides.items() if not isinstance(v, Verdict)
        )
        if non_verdict:
            raise TypeError(
                "CompatibilityPolicyConfig.overrides values must be Verdict "
                f"members, not raw strings: {non_verdict}. The `Mapping[str, "
                "Verdict]` annotation isn't runtime-enforced, so an untyped "
                'adapter passing a raw string (e.g. "BREAKING" or the '
                'YAML-facing "break") would otherwise freeze silently -- '
                "policy_file.py's _SEVERITY_MAP already normalizes the "
                "YAML spellings to real Verdict members before reaching "
                "this constructor; any other front end must do the same."
            )
        object.__setattr__(
            self, "packs", _canonical_tuple(self.packs, key=_pack_sort_key)
        )
        object.__setattr__(self, "overrides", _frozen_mapping(self.overrides))


@dataclass(frozen=True)
class GateConfig:
    """What blocks CI (ADR-049 D8) -- ``NOT_APPLICABLE`` to contract membership.

    ``severity`` reuses the existing :class:`~abicheck.severity.SeverityConfig`
    four-category model rather than inventing a second severity vocabulary.

    ``exit_code_scheme`` validates against ``{"legacy", "severity"}`` --
    ``"auto"`` (ADR-037 D12's third CLI-facing choice) is deliberately
    excluded here: ``auto`` means "resolve to legacy or severity based on
    whether a severity setting is in effect," and by the time a value
    reaches this *effective*, already-resolved configuration that choice
    must already be made (see ``cli.py``'s ``_announce_exit_scheme``: "auto
    already resolved to legacy or severity by the time we get here").
    """

    exit_code_scheme: str = "severity"
    preset: ImmutableIdentity | None = None
    packs: tuple[ImmutableIdentity, ...] = ()
    severity: SeverityConfig = field(default_factory=SeverityConfig)

    def __post_init__(self) -> None:
        if self.exit_code_scheme not in _VALID_EXIT_CODE_SCHEMES:
            raise ValueError(
                f"GateConfig.exit_code_scheme must be one of "
                f"{sorted(_VALID_EXIT_CODE_SCHEMES)} (ADR-037 D12; 'auto' is "
                "a resolution-time choice, not a valid resolved value), got "
                f"{self.exit_code_scheme!r}"
            )
        object.__setattr__(
            self, "packs", _canonical_tuple(self.packs, key=_pack_sort_key)
        )


@dataclass(frozen=True)
class SuppressionConfig:
    """Immutable rules and digest (ADR-049 D7: ``{rules: [], sha256: "..."}``,
    kept as its own field pair rather than :class:`DigestedItems` since the
    ADR's own key is ``rules``, not ``items``).

    ``sha256`` is unconditionally required, for the same reason as
    :class:`DigestedItems`: a suppression source explicitly selected and
    resolved to zero rules is a different fact from no suppression source
    being selected at all, and only the digest can prove the source was
    genuinely consulted and empty (not merely unconsidered) at decision
    time. "No suppression source selected" is represented by
    :attr:`CompatibilityEvaluationConfig.suppressions` being ``None``.
    """

    sha256: str
    rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", _frozen_tuple(self.rules))
        _require_nonempty_digest(self.sha256, owner="SuppressionConfig")


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
    #: ``None`` means no suppression source was selected at all; a
    #: ``SuppressionConfig`` (empty ``rules`` or not) means one was.
    suppressions: SuppressionConfig | None = None
    provenance: Mapping[str, ValueProvenance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "provenance", _frozen_mapping(self.provenance))
