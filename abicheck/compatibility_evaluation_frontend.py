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

"""ADR-049 Phase 1's closing slice: one whole
:class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
resolved from a real front end's own inputs.

``compatibility_evaluation_wiring.py`` resolves *individual* fields from real
input (``contract.mode`` from the legacy scope flag,
``surface.internal_namespaces`` from a ``--policy-file``, the three
``*.packs`` fields plus a policy pack's overrides from real manifests). This
module is the layer above it: it collects **every** field a front end can
actually state today, resolves each one through
:func:`~abicheck.compatibility_evaluation_resolver.resolve_field`, and
assembles the seven typed namespaces plus one provenance receipt entry per
resolved field -- the "one typed object" ADR-049 D7 requires and Phase 1's
own gate names: *"every front end resolves equivalent semantic input to an
equal ``CompatibilityEvaluationConfig`` and provenance receipt."*

Two real front ends are wired, and the gate above is executable rather than
aspirational -- :func:`cross_front_end_differences` compares two resolved
configs modulo exactly one permitted difference (*which* front end stated a
value: ``explicit_cli`` vs. ``api_request``, and the option spelling that
goes with it), and ``tests/test_compatibility_evaluation_frontend.py`` runs
the CLI's own ``compare`` kwargs against the equivalent
:class:`~abicheck.api_types.CompareRequest` through it:

- :func:`compare_cli_inputs` -- the ``compare`` command's real kwargs
  (``cli.py``'s option destinations: ``--contract``, ``--scope-public-headers``,
  ``--policy``/``--policy-file``, ``--severity-*``, ``--exit-code-scheme``,
  ``--public-symbol``, ``--suppress``), plus the set of parameters the user
  *actually typed* (Click's ``ctx.get_parameter_source(...)`` is what a live
  caller would pass), since several of those options carry a non-``None``
  click default that must not be mistaken for a stated value.
- :func:`compare_request_inputs` -- the same semantic fields off a typed
  :class:`~abicheck.api_types.CompareRequest`.
- :meth:`ProjectCompatibilityInputs.from_build_config` -- the project's own
  ``.abicheck.yml`` (``buildsource.inline.BuildConfig``), contributing at
  ``project_config`` tier.

**Still resolved to its built-in default, because no front end can state it
today** (recorded here rather than silently defaulted):
:class:`~abicheck.compatibility_evaluation_config.EvidenceConfig` in full (no
CLI/API surface selects evidence providers or a variant set),
``contract.unresolved``/``contract.overlays``/``assurance.require_evidence``
(no flag; a ``kind: contract`` pack is the only input that reaches them, via
:func:`~abicheck.compatibility_evaluation_wiring.resolve_pack_field_assignments`),
and ``--strict-suppressions``/``--require-justification``, which are real
inputs with no field in the ADR's own typed shape to carry them.

Not called from any live command: this module *resolves* configuration, it
does not apply it. Nothing here changes a verdict, a finding, or an exit
code -- consuming the resolved object in the authoritative comparison path is
Phase 5's "same typed config" work, and the default flip is Phase 7. See
``docs/contribute/plans/public-contract-default.md``.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .change_registry_types import Verdict
from .checker_policy import VALID_BASE_POLICIES, policy_kind_sets
from .compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    DigestedItems,
    EvidenceConfig,
    GateConfig,
    ImmutableIdentity,
    SelectedByEntry,
    SuppressionConfig,
    SurfaceConfig,
    ValueProvenance,
)
from .compatibility_evaluation_packs import PackKind
from .compatibility_evaluation_resolver import FieldCandidate, resolve_field
from .compatibility_evaluation_wiring import (
    BUILT_IN_DEFAULT_CONTRACT_MODE,
    CONTRACT_MODE_FIELD,
    INTERNAL_NAMESPACES_FIELD,
    internal_namespaces_candidate,
    legacy_contract_mode_candidate,
    resolve_pack_field_assignments,
    resolve_policy_pack_overrides,
    resolve_selected_packs,
)
from .contract_relevance_types import ContractMode, SelectorLayer, coerce_contract_mode
from .severity import SEVERITY_PRESETS, SeverityConfig, SeverityLevel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .api_types import CompareRequest
    from .buildsource.inline import BuildConfig
    from .policy_file import PolicyFile

# Field names double as provenance-receipt keys. Declared once so the receipt
# a consumer reads and the pack-route table in
# `compatibility_evaluation_wiring.py` cannot drift apart on a spelling.
CONTRACT_UNRESOLVED_FIELD = "contract.unresolved"
CONTRACT_OVERLAYS_FIELD = "contract.overlays"
CONTRACT_PACKS_FIELD = "contract.packs"
POLICY_BASE_FIELD = "policy.base"
POLICY_PACKS_FIELD = "policy.packs"
POLICY_OVERRIDES_FIELD = "policy.overrides"
EXPLICIT_SCOPE_FIELD = "surface.explicit_scope"
REQUIRE_EVIDENCE_FIELD = "assurance.require_evidence"
EXIT_CODE_SCHEME_FIELD = "gate.exit_code_scheme"
GATE_PRESET_FIELD = "gate.preset"
GATE_PACKS_FIELD = "gate.packs"
SUPPRESSIONS_FIELD = "suppressions"

#: ``gate.severity.<category>`` field name per :class:`SeverityConfig` field.
SEVERITY_CATEGORY_FIELDS: Mapping[str, str] = {
    category: f"gate.severity.{category}"
    for category in (
        "abi_breaking",
        "potential_breaking",
        "quality_issues",
        "addition",
    )
}


class FrontEnd(str, Enum):
    """Which front end stated a run's explicit inputs.

    ADR-049 D7 puts "explicit CLI" and "explicit API request" in the *same*
    precedence tier -- neither outranks the other, and a single resolution
    only ever has one of them -- so this selects nothing but the
    :class:`~abicheck.contract_relevance_types.SelectorLayer` each explicit
    candidate is tagged with, and the option spelling recorded alongside it.
    """

    CLI = "cli"
    API = "api"

    @property
    def layer(self) -> SelectorLayer:
        return (
            SelectorLayer.EXPLICIT_CLI
            if self is FrontEnd.CLI
            else SelectorLayer.API_REQUEST
        )


# --------------------------------------------------------------------------
# D6 immutable identities for the built-in (file-less) base policy and
# severity preset.
# --------------------------------------------------------------------------


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@functools.cache
def builtin_policy_identity(name: str) -> ImmutableIdentity:
    """The replayable identity of a built-in ``--policy`` base.

    ADR-049 D6 requires every selected base to carry an
    ``id``/``version``/``sha256``, but a built-in base is code, not a file --
    there are no bytes to digest. The digest is therefore taken over what the
    base actually *is*: the four ``ChangeKind`` sets
    :func:`~abicheck.checker_policy.policy_kind_sets` resolves it to. That
    makes the identity detect the drift a digest exists to detect -- a
    registry change that moves a kind between buckets changes the base's
    meaning and changes its digest -- which a bare ``id``/``version`` pair
    could not.

    ``version`` is ``1`` for every built-in base: the built-ins are not
    independently versioned artifacts, and the digest already distinguishes
    two revisions of the same name.

    Raises ``ValueError`` for a name outside
    :data:`~abicheck.checker_policy.VALID_BASE_POLICIES` -- ``policy_kind_sets``
    itself falls back to ``strict_abi`` for an unknown name, which is the
    right behavior for classification (never crash a comparison over a typo)
    but exactly wrong for an identity (it would mint a ``sdk_vendr`` identity
    carrying ``strict_abi``'s digest).
    """
    if name not in VALID_BASE_POLICIES:
        raise ValueError(
            f"unknown base policy {name!r}: choose from {sorted(VALID_BASE_POLICIES)}"
        )
    breaking, api_break, compatible, risk = policy_kind_sets(name)
    return ImmutableIdentity(
        id=name,
        version=1,
        sha256=_digest(
            {
                "base_policy": name,
                "breaking": sorted(k.value for k in breaking),
                "api_break": sorted(k.value for k in api_break),
                "compatible": sorted(k.value for k in compatible),
                "risk": sorted(k.value for k in risk),
            }
        ),
    )


@functools.cache
def severity_preset_identity(name: str) -> ImmutableIdentity:
    """The replayable identity of a built-in ``--severity-preset``.

    Same reasoning as :func:`builtin_policy_identity`: a preset is code, so
    the digest is taken over the four category levels it resolves to.
    The CLI spelling ``info-only`` and its programmatic alias ``info_only``
    (``SEVERITY_PRESETS`` carries both keys for the same
    :class:`~abicheck.severity.SeverityConfig`) normalize to one canonical id,
    so two equivalent semantic inputs resolve to an equal object (D7) instead
    of to two identities that differ only in punctuation.
    """
    preset = SEVERITY_PRESETS.get(name)
    if preset is None:
        raise ValueError(
            f"unknown severity preset {name!r}: choose from {sorted(SEVERITY_PRESETS)}"
        )
    canonical_id = name.replace("_", "-")
    return ImmutableIdentity(
        id=canonical_id,
        version=1,
        sha256=_digest(
            {
                "severity_preset": canonical_id,
                **{
                    category: getattr(preset, category).value
                    for category in SEVERITY_CATEGORY_FIELDS
                },
            }
        ),
    )


# --------------------------------------------------------------------------
# Normalized, front-end-agnostic inputs.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SuppressionSource:
    """A selected ``--suppress`` file, already read and digested.

    Kept separate from :class:`ExplicitCompatibilityInputs` so the resolver
    itself stays I/O-free: :meth:`from_file` is the one place that touches
    the filesystem, and a caller that already loaded the rule file (every
    real front end does, well before configuration is resolved) can build
    this from what it has.
    """

    path: str | None
    sha256: str
    rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", tuple(self.rules))

    @classmethod
    def from_file(
        cls, path: str | Path, *, require_justification: bool = False
    ) -> SuppressionSource:
        """Read, digest, and summarize a real suppression file.

        ``sha256`` digests the file's raw bytes (exact replay, ADR-049 D6);
        ``rules`` is :meth:`~abicheck.suppression.SuppressionList.rule_identities`.
        *require_justification* is forwarded to the loader so a front end
        running with ``--require-justification`` gets the same hard error
        here that it gets from its own load.
        """
        from .suppression import SuppressionList

        file_path = Path(path)
        raw = file_path.read_bytes()
        rules = SuppressionList.load(
            file_path, require_justification=require_justification
        ).rule_identities()
        return cls(
            path=str(file_path),
            sha256=hashlib.sha256(raw).hexdigest(),
            rules=rules,
        )


@dataclass(frozen=True)
class ExplicitCompatibilityInputs:
    """What the user stated on *this* invocation (CLI flags / API request).

    Every field's "not stated" value is ``None`` (or an empty collection), so
    an untouched option contributes no candidate at all and the next layer
    down wins -- ADR-049 D7's "a selector layer only participates when it
    actually selected something". A front end whose own default is
    non-``None`` (``--policy`` defaults to ``strict_abi``,
    ``--scope-public-headers`` to ``True``) must therefore decide
    *explicitness* before building this; :func:`compare_cli_inputs` does that
    from the set of parameters the user actually typed.
    """

    #: ``--contract`` / ``CompareRequest.contract_mode``.
    contract_mode: str | None = None
    #: ``--scope-public-headers``/``--no-`` (the D2 legacy alias for
    #: ``contract.mode``); ``None`` = flag untouched.
    scope_public_headers: bool | None = None
    #: ``--policy`` / ``CompareRequest.policy``.
    policy_base: str | None = None
    #: An already-loaded ``--policy-file`` document.
    policy_file: PolicyFile | None = None
    #: ``--public-symbol``/``--public-symbols-list`` /
    #: ``CompareRequest.force_public_symbols`` (ADR-024 D6 widening overlay).
    public_symbols: tuple[str, ...] = ()
    #: An already-read ``--suppress`` source.
    suppression: SuppressionSource | None = None
    #: ``--exit-code-scheme``; ``"auto"`` is a resolution-time choice, not a
    #: stated value (see :func:`resolve_compatibility_evaluation_config`).
    exit_code_scheme: str | None = None
    severity_preset: str | None = None
    severity_abi_breaking: str | None = None
    severity_potential_breaking: str | None = None
    severity_quality_issues: str | None = None
    severity_addition: str | None = None
    #: ADR-049 D8 pack-manifest paths. No CLI flag supplies these yet (see
    #: the plan's Phase 1 notes); the resolver accepts them so the composition
    #: path is real and tested ahead of the flag.
    pack_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_symbols", tuple(self.public_symbols))
        object.__setattr__(self, "pack_paths", tuple(str(p) for p in self.pack_paths))

    def severity_category(self, category: str) -> str | None:
        return cast("str | None", getattr(self, f"severity_{category}"))


@dataclass(frozen=True)
class ProjectCompatibilityInputs:
    """The project's ``.abicheck.yml`` contribution (``project_config`` tier).

    A deliberately narrow projection of ``BuildConfig``: only the keys that
    map onto a field of the ADR-049 typed configuration. The rest of the file
    (``build:``/``sources:``/``compile:``/``debug:``/``source:``) drives
    extraction, not compatibility evaluation.
    """

    path: str | None = None
    #: ``scope.public`` -- the project-config spelling of the same legacy
    #: alias ``--scope-public-headers`` is (D2), stated at ``project_config``
    #: tier rather than typed on this invocation.
    scope_public: bool | None = None
    #: ``scope.public_symbols``.
    public_symbols: tuple[str, ...] = ()
    #: ``exit_code_scheme:`` (``"auto"``/``None`` = unset).
    exit_code_scheme: str | None = None
    severity_preset: str | None = None
    severity_abi_breaking: str | None = None
    severity_potential_breaking: str | None = None
    severity_quality_issues: str | None = None
    severity_addition: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_symbols", tuple(self.public_symbols))

    def severity_category(self, category: str) -> str | None:
        return cast("str | None", getattr(self, f"severity_{category}"))

    @classmethod
    def from_build_config(
        cls, cfg: BuildConfig | None, *, path: str | Path | None = None
    ) -> ProjectCompatibilityInputs | None:
        """Project a loaded ``.abicheck.yml`` onto the fields this resolver
        understands, or ``None`` when there is no config at all.
        """
        if cfg is None:
            return None
        return cls(
            path=str(path) if path is not None else None,
            scope_public=cfg.scope_public,
            public_symbols=tuple(cfg.public_symbols),
            exit_code_scheme=cfg.exit_code_scheme,
            severity_preset=cfg.severity_preset,
            severity_abi_breaking=cfg.severity_abi_breaking,
            severity_potential_breaking=cfg.severity_potential_breaking,
            severity_quality_issues=cfg.severity_quality_issues,
            severity_addition=cfg.severity_addition,
        )


# --------------------------------------------------------------------------
# Candidate construction helpers.
# --------------------------------------------------------------------------


def _candidate(
    layer: SelectorLayer,
    value: Hashable,
    *,
    option: str | None = None,
    source_kind: str | None = None,
    reference: str | None = None,
    path: str | None = None,
    field_location: str | None = None,
) -> FieldCandidate:
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=layer,
            source_kind=source_kind,
            reference=reference,
            path=path,
            field_location=field_location,
            selected_by=(SelectedByEntry(layer=layer, option=option, path=path),),
        ),
        value=value,
    )


def _default(value: Hashable, *, source_kind: str | None = None) -> FieldCandidate:
    return FieldCandidate(
        provenance=ValueProvenance(
            layer=SelectorLayer.BUILT_IN_DEFAULT, source_kind=source_kind
        ),
        value=value,
    )


def _resolve(
    field_name: str,
    candidates: Sequence[FieldCandidate],
    *,
    default: FieldCandidate,
    pack_value: Hashable | None = None,
    pack_paths: Sequence[str] = (),
    pack_layer: SelectorLayer = SelectorLayer.EXPLICIT_CLI,
    require_legacy_alias_agreement: bool = True,
) -> tuple[Hashable, ValueProvenance]:
    """Resolve one field, letting a selected pack fill it only if nothing else did.

    ADR-049 D8 composes as "explicit per-kind override > selected packs >
    base". A pack is *selected by* a layer but is not itself a precedence
    layer (plan §3.2), so a pack assignment cannot be handed to
    :func:`resolve_field` as an ordinary candidate: tagged with the layer that
    selected it, it would tie with -- and be reported as conflicting with --
    an explicit value for the same field, which is precisely the case D8 says
    the explicit value resolves.

    So the rule is applied here instead, and deliberately in its conservative
    form: a pack contributes only when *no* other layer stated the field,
    project config included. D8's own wording covers the explicit override;
    extending it to "a pack never silently overrides a value the user or the
    project stated" is the reading that cannot surprise anyone, and a project
    that wants the pack's value simply stops stating its own.
    """
    all_candidates = list(candidates)
    if not all_candidates and pack_value is not None:
        all_candidates.append(
            _candidate(
                pack_layer,
                pack_value,
                option="--pack",
                source_kind="pack_manifest",
                field_location=field_name,
                path=pack_paths[0] if len(pack_paths) == 1 else None,
            )
        )
    return resolve_field(
        field_name,
        all_candidates,
        default=default,
        require_legacy_alias_agreement=require_legacy_alias_agreement,
    )


#: Marker value for "some other layer already states this field", used as the
#: value in the ``explicit_overrides`` mapping the pack functions take. Those
#: functions read only its keys (ADR-049 D8 exempts an already-stated field
#: from pack-vs-pack conflict detection), and several of the fields marked
#: this way have no resolved value yet at the point the mapping is built.
_STATED_ELSEWHERE: Hashable = "<stated by another layer>"


def _pack_assigned_only(
    routed: Mapping[str, Hashable], pinned: Mapping[str, Hashable]
) -> dict[str, Hashable]:
    """*routed* without the ``explicit_overrides`` entries re-applied onto it."""
    return {name: value for name, value in routed.items() if name not in pinned}


def _policy_file_override_slugs(policy_file: PolicyFile | None) -> dict[str, Verdict]:
    """``--policy-file`` ``overrides:`` as the slug-keyed mapping the typed
    config takes (``PolicyFile.overrides`` is keyed by ``ChangeKind``)."""
    if policy_file is None:
        return {}
    return {kind.value: verdict for kind, verdict in policy_file.overrides.items()}


def _explicit_scope(
    explicit: ExplicitCompatibilityInputs,
    project: ProjectCompatibilityInputs | None,
    layer: SelectorLayer,
) -> tuple[DigestedItems | None, ValueProvenance]:
    """Resolve ``surface.explicit_scope`` -- an *additive* overlay field.

    ``--public-symbol``/``--public-symbols-list`` and ``scope.public_symbols``
    are documented (ADR-037 D4, ``cli_helpers_compare.resolve_compare_config``)
    as merging, not overriding: the CLI list widens the project's rather than
    replacing it. That is a genuine exception to D7's per-field
    highest-layer-wins rule, and it is the live behavior, so the resolved
    value is the union of every contributing layer. The receipt records the
    highest-precedence contributor as the provenance layer and lists **every**
    contributor in ``selected_by``, so an additive resolution is still exactly
    replayable.

    ``sha256`` digests the canonical item list itself rather than an external
    source: for this field the items *are* the whole content (a symbol-name
    list, with no expansion step between the source and the items), and a
    ``--public-symbol`` overlay typed on the command line has no external
    source to digest at all. A future file-backed overlay that *expands* into
    items must digest the file instead -- that is exactly the drift
    :class:`DigestedItems` warns about, and it does not apply here.
    """
    selected_by: list[SelectedByEntry] = []
    merged: list[str] = []
    winning_layer = SelectorLayer.BUILT_IN_DEFAULT
    if explicit.public_symbols:
        winning_layer = layer
        selected_by.append(SelectedByEntry(layer=layer, option="--public-symbol"))
        merged.extend(explicit.public_symbols)
    if project is not None and project.public_symbols:
        if winning_layer is SelectorLayer.BUILT_IN_DEFAULT:
            winning_layer = SelectorLayer.PROJECT_CONFIG
        selected_by.append(
            SelectedByEntry(
                layer=SelectorLayer.PROJECT_CONFIG,
                option="scope.public_symbols",
                path=project.path,
            )
        )
        merged.extend(project.public_symbols)

    if not merged:
        return None, ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT)

    items = tuple(dict.fromkeys(sorted(merged)))
    return (
        DigestedItems(sha256=_digest({"explicit_scope": list(items)}), items=items),
        ValueProvenance(
            layer=winning_layer,
            source_kind="public_symbol_overlay",
            selected_by=tuple(selected_by),
        ),
    )


# --------------------------------------------------------------------------
# The whole-object resolver.
# --------------------------------------------------------------------------


def resolve_compatibility_evaluation_config(
    *,
    front_end: FrontEnd = FrontEnd.CLI,
    explicit: ExplicitCompatibilityInputs | None = None,
    project: ProjectCompatibilityInputs | None = None,
) -> CompatibilityEvaluationConfig:
    """Resolve one complete effective configuration plus its receipt.

    Every field is resolved independently through
    :func:`~abicheck.compatibility_evaluation_resolver.resolve_field`, so
    D7's precedence, its same-tier-conflict rule, and its
    equivalent-duplicates rule apply uniformly rather than per call site.
    The returned object's ``provenance`` maps each dotted field name to the
    :class:`ValueProvenance` of the layer that won it.

    Two D7 compatibility exceptions are applied, both with
    ``require_legacy_alias_agreement=False`` (the explicit value wins and the
    shadowed legacy input is retained in ``provenance[...].shadowed_legacy``)
    rather than as usage errors:

    - ``policy.base``: "when both ``--policy`` and ``--policy-file`` are
      supplied, ``--policy-file`` keeps winning as documented and tested
      today" (D7, verbatim) -- ``--policy``'s own help text already says it is
      ignored then.
    - ``contract.mode``: an explicit ``--contract`` outranks
      ``--scope-public-headers``/``--no-`` rather than colliding with it, per
      the Phase 6 flag's own documented contract ("an explicit value outranks
      those"). Making that pair an error would reject a combination the live
      CLI accepts today.

    ``"auto"`` never reaches a resolved value: ADR-037 D12's third
    ``--exit-code-scheme`` choice means "decide from whether a severity
    setting is in effect", and
    :class:`~abicheck.compatibility_evaluation_config.GateConfig` rejects it
    for that reason. An ``auto`` at any layer therefore contributes no
    candidate, and the built-in default carries the resolved answer -- the
    same rule ``cli_helpers_compare.resolve_compare_config`` applies today.

    Raises :class:`~abicheck.compatibility_evaluation_resolver.FieldResolutionError`
    (D7 usage errors), :class:`~abicheck.compatibility_evaluation_resolver.PackConflictError`
    (D8), or :class:`~abicheck.errors.PackManifestError` (a malformed or
    mis-scoped pack manifest). Mapping those to an exit code is a front end's
    job; this module has no notion of one.
    """
    explicit = explicit or ExplicitCompatibilityInputs()
    layer = front_end.layer
    prov: dict[str, ValueProvenance] = {}

    policy_overrides_explicit = _policy_file_override_slugs(explicit.policy_file)
    pack_paths = explicit.pack_paths

    # ── D8: which fields another layer already states, per pack namespace ────
    # A field stated elsewhere is exempt from pack-vs-pack conflict detection:
    # two packs may legitimately disagree about a field this resolution never
    # takes from a pack anyway. `detect_pack_conflicts` reads only the *keys*
    # of this mapping, so the value is a marker rather than the real resolved
    # value (which several of these fields do not have yet at this point).
    pinned_contract: dict[str, Hashable] = {}
    if explicit.policy_file is not None and explicit.policy_file.internal_namespaces:
        pinned_contract[INTERNAL_NAMESPACES_FIELD] = _STATED_ELSEWHERE
    pinned_gate: dict[str, Hashable] = {}
    if _stated_exit_code_scheme(explicit.exit_code_scheme) is not None or (
        project is not None
        and _stated_exit_code_scheme(project.exit_code_scheme) is not None
    ):
        pinned_gate[EXIT_CODE_SCHEME_FIELD] = _STATED_ELSEWHERE
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        if explicit.severity_category(category) is not None or (
            project is not None and project.severity_category(category) is not None
        ):
            pinned_gate[field_name] = _STATED_ELSEWHERE

    packs_by_field = resolve_selected_packs(
        pack_paths,
        explicit_overrides={
            PackKind.CONTRACT: pinned_contract,
            PackKind.GATE: pinned_gate,
            PackKind.POLICY: policy_overrides_explicit,
        },
    )
    # `resolve_pack_field_assignments` re-applies its `explicit_overrides` on
    # top of the routed result (D8's "explicit override > selected packs"), so
    # the markers above would come back out as values. Drop them: what this
    # resolver wants from these mappings is only what the packs themselves
    # assigned -- the stated value reaches the field through its own candidate.
    contract_pack_fields = _pack_assigned_only(
        resolve_pack_field_assignments(
            pack_paths, PackKind.CONTRACT, explicit_overrides=pinned_contract
        ),
        pinned_contract,
    )
    gate_pack_fields = _pack_assigned_only(
        resolve_pack_field_assignments(
            pack_paths, PackKind.GATE, explicit_overrides=pinned_gate
        ),
        pinned_gate,
    )

    # ── contract ────────────────────────────────────────────────────────────
    mode_candidates: list[FieldCandidate] = []
    if explicit.contract_mode is not None:
        mode_candidates.append(
            _candidate(
                layer,
                coerce_contract_mode(explicit.contract_mode),
                option="--contract" if front_end is FrontEnd.CLI else "contract_mode",
                source_kind="contract_mode",
            )
        )
    legacy_mode = legacy_contract_mode_candidate(
        scope_public_headers=bool(explicit.scope_public_headers),
        scope_public_headers_is_explicit=explicit.scope_public_headers is not None,
    )
    if legacy_mode is not None:
        mode_candidates.append(legacy_mode)
    project_mode = (
        legacy_contract_mode_candidate(
            scope_public_headers=bool(project.scope_public),
            scope_public_headers_is_explicit=project.scope_public is not None,
            layer=SelectorLayer.PROJECT_CONFIG,
        )
        if project is not None
        else None
    )
    if project_mode is not None:
        mode_candidates.append(project_mode)

    mode, prov[CONTRACT_MODE_FIELD] = _resolve(
        CONTRACT_MODE_FIELD,
        mode_candidates,
        default=_default(BUILT_IN_DEFAULT_CONTRACT_MODE),
        require_legacy_alias_agreement=False,
    )

    unresolved, prov[CONTRACT_UNRESOLVED_FIELD] = _resolve(
        CONTRACT_UNRESOLVED_FIELD,
        [],
        default=_default("not_checkable"),
        pack_value=contract_pack_fields.get(CONTRACT_UNRESOLVED_FIELD),
        pack_paths=pack_paths,
        pack_layer=layer,
    )
    overlays, prov[CONTRACT_OVERLAYS_FIELD] = _resolve(
        CONTRACT_OVERLAYS_FIELD,
        [],
        default=_default(()),
        pack_value=contract_pack_fields.get(CONTRACT_OVERLAYS_FIELD),
        pack_paths=pack_paths,
        pack_layer=layer,
    )
    contract_packs, prov[CONTRACT_PACKS_FIELD] = packs_by_field[CONTRACT_PACKS_FIELD]

    contract = ContractConfig(
        mode=cast(ContractMode, mode),
        unresolved=cast(str, unresolved),
        overlays=cast("tuple[str, ...]", overlays),
        packs=contract_packs,
    )

    # ── surface ─────────────────────────────────────────────────────────────
    namespace_candidate = internal_namespaces_candidate(
        policy_file=explicit.policy_file, layer=layer
    )
    internal_namespaces, prov[INTERNAL_NAMESPACES_FIELD] = _resolve(
        INTERNAL_NAMESPACES_FIELD,
        [] if namespace_candidate is None else [namespace_candidate],
        default=_default(()),
        pack_value=contract_pack_fields.get(INTERNAL_NAMESPACES_FIELD),
        pack_paths=pack_paths,
        pack_layer=layer,
    )
    explicit_scope, prov[EXPLICIT_SCOPE_FIELD] = _explicit_scope(
        explicit, project, layer
    )
    surface = SurfaceConfig(
        explicit_scope=explicit_scope,
        internal_namespaces=cast("tuple[str, ...]", internal_namespaces),
    )

    # ── assurance ───────────────────────────────────────────────────────────
    require_evidence, prov[REQUIRE_EVIDENCE_FIELD] = _resolve(
        REQUIRE_EVIDENCE_FIELD,
        [],
        default=_default(True),
        pack_value=contract_pack_fields.get(REQUIRE_EVIDENCE_FIELD),
        pack_paths=pack_paths,
        pack_layer=layer,
    )
    assurance = AssuranceConfig(require_evidence=cast(bool, require_evidence))

    # ── policy ──────────────────────────────────────────────────────────────
    base_candidates: list[FieldCandidate] = []
    if explicit.policy_file is not None:
        source_path = (
            str(explicit.policy_file.source_path)
            if explicit.policy_file.source_path
            else None
        )
        base_candidates.append(
            _candidate(
                layer,
                builtin_policy_identity(explicit.policy_file.base_policy),
                option="--policy-file",
                source_kind="policy_file",
                reference=explicit.policy_file.base_policy,
                path=source_path,
                field_location="base_policy",
            )
        )
    if explicit.policy_base is not None:
        base_candidates.append(
            _candidate(
                SelectorLayer.LEGACY_ALIAS,
                builtin_policy_identity(explicit.policy_base),
                option="--policy",
                source_kind="builtin_policy",
                reference=explicit.policy_base,
            )
        )
    policy_base, prov[POLICY_BASE_FIELD] = _resolve(
        POLICY_BASE_FIELD,
        base_candidates,
        default=_default(builtin_policy_identity("strict_abi")),
        require_legacy_alias_agreement=False,
    )

    policy_packs, prov[POLICY_PACKS_FIELD] = packs_by_field[POLICY_PACKS_FIELD]
    policy_overrides = resolve_policy_pack_overrides(
        pack_paths, explicit_overrides=policy_overrides_explicit
    )
    prov[POLICY_OVERRIDES_FIELD] = _overrides_provenance(
        layer,
        policy_file=explicit.policy_file,
        has_pack_overrides=bool(policy_overrides) and bool(pack_paths),
        explicit_overrides=policy_overrides_explicit,
    )
    policy = CompatibilityPolicyConfig(
        base=cast(ImmutableIdentity, policy_base),
        packs=policy_packs,
        overrides=policy_overrides,
    )

    # ── gate ────────────────────────────────────────────────────────────────
    preset_candidates: list[FieldCandidate] = []
    if explicit.severity_preset is not None:
        preset_candidates.append(
            _candidate(
                layer,
                severity_preset_identity(explicit.severity_preset),
                option="--severity-preset",
                source_kind="severity_preset",
                reference=explicit.severity_preset,
            )
        )
    if project is not None and project.severity_preset is not None:
        preset_candidates.append(
            _candidate(
                SelectorLayer.PROJECT_CONFIG,
                severity_preset_identity(project.severity_preset),
                option="severity.preset",
                source_kind="severity_preset",
                reference=project.severity_preset,
                path=project.path,
            )
        )
    gate_preset, prov[GATE_PRESET_FIELD] = _resolve(
        GATE_PRESET_FIELD, preset_candidates, default=_default(None)
    )

    preset_base = (
        SEVERITY_PRESETS[cast(ImmutableIdentity, gate_preset).id]
        if gate_preset is not None
        else SeverityConfig()
    )
    levels: dict[str, SeverityLevel] = {}
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        candidates: list[FieldCandidate] = []
        stated = explicit.severity_category(category)
        if stated is not None:
            candidates.append(
                _candidate(
                    layer,
                    SeverityLevel(stated),
                    option=f"--severity-{category.replace('_', '-')}",
                    source_kind="severity_override",
                )
            )
        project_stated = (
            project.severity_category(category) if project is not None else None
        )
        if project_stated is not None:
            candidates.append(
                _candidate(
                    SelectorLayer.PROJECT_CONFIG,
                    SeverityLevel(project_stated),
                    option=f"severity.{category}",
                    source_kind="severity_override",
                    path=project.path if project is not None else None,
                )
            )
        value, prov[field_name] = _resolve(
            field_name,
            candidates,
            default=_default(
                getattr(preset_base, category),
                source_kind="severity_preset" if gate_preset is not None else None,
            ),
            pack_value=gate_pack_fields.get(field_name),
            pack_paths=pack_paths,
            pack_layer=layer,
        )
        levels[category] = cast(SeverityLevel, value)

    severity_active = _severity_active(explicit, project, gate_pack_fields)
    scheme_candidates: list[FieldCandidate] = []
    stated_scheme = _stated_exit_code_scheme(explicit.exit_code_scheme)
    if stated_scheme is not None:
        scheme_candidates.append(
            _candidate(
                layer,
                stated_scheme,
                option="--exit-code-scheme",
                source_kind="exit_code_scheme",
            )
        )
    project_scheme = (
        _stated_exit_code_scheme(project.exit_code_scheme) if project else None
    )
    if project_scheme is not None:
        scheme_candidates.append(
            _candidate(
                SelectorLayer.PROJECT_CONFIG,
                project_scheme,
                option="exit_code_scheme",
                source_kind="exit_code_scheme",
                path=project.path if project is not None else None,
            )
        )
    exit_code_scheme, prov[EXIT_CODE_SCHEME_FIELD] = _resolve(
        EXIT_CODE_SCHEME_FIELD,
        scheme_candidates,
        default=_default(
            "severity" if severity_active else "legacy", source_kind="auto"
        ),
        pack_value=gate_pack_fields.get(EXIT_CODE_SCHEME_FIELD),
        pack_paths=pack_paths,
        pack_layer=layer,
    )

    gate_packs, prov[GATE_PACKS_FIELD] = packs_by_field[GATE_PACKS_FIELD]
    gate = GateConfig(
        exit_code_scheme=cast(str, exit_code_scheme),
        preset=cast("ImmutableIdentity | None", gate_preset),
        packs=gate_packs,
        severity=SeverityConfig(**levels),
    )

    # ── suppressions ────────────────────────────────────────────────────────
    suppressions: SuppressionConfig | None = None
    if explicit.suppression is not None:
        suppressions = SuppressionConfig(
            sha256=explicit.suppression.sha256, rules=explicit.suppression.rules
        )
        prov[SUPPRESSIONS_FIELD] = ValueProvenance(
            layer=layer,
            source_kind="suppression_file",
            path=explicit.suppression.path,
            selected_by=(
                SelectedByEntry(
                    layer=layer,
                    option="--suppress",
                    path=explicit.suppression.path,
                ),
            ),
        )
    else:
        prov[SUPPRESSIONS_FIELD] = ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT)

    return CompatibilityEvaluationConfig(
        contract=contract,
        # No front end selects evidence providers or a variant set today --
        # see this module's docstring.
        evidence=EvidenceConfig(),
        surface=surface,
        assurance=assurance,
        policy=policy,
        gate=gate,
        suppressions=suppressions,
        provenance=prov,
    )


def _stated_exit_code_scheme(value: str | None) -> str | None:
    """``None`` unless *value* names a real resolved scheme.

    ``"auto"`` is a resolution-time choice, not a value (ADR-037 D12), so it
    is treated as "not stated" and the built-in default carries the resolved
    answer -- see :func:`resolve_compatibility_evaluation_config`.
    """
    if value is None or value == "auto":
        return None
    return value


def _severity_active(
    explicit: ExplicitCompatibilityInputs,
    project: ProjectCompatibilityInputs | None,
    gate_pack_fields: Mapping[str, Hashable],
) -> bool:
    """Whether *any* severity setting is in effect (drives ``auto``).

    Mirrors ``cli_helpers_compare.resolve_compare_config``'s own
    ``severity_active`` -- a preset or any per-category value, from the CLI or
    the project config -- and additionally counts a ``kind: gate`` pack that
    assigns a category, since a pack-supplied severity is no less "in effect"
    than a config-supplied one.
    """
    if explicit.severity_preset is not None or (
        project is not None and project.severity_preset is not None
    ):
        return True
    for category, field_name in SEVERITY_CATEGORY_FIELDS.items():
        if explicit.severity_category(category) is not None:
            return True
        if project is not None and project.severity_category(category) is not None:
            return True
        if field_name in gate_pack_fields:
            return True
    return False


def _overrides_provenance(
    layer: SelectorLayer,
    *,
    policy_file: PolicyFile | None,
    has_pack_overrides: bool,
    explicit_overrides: Mapping[str, Verdict],
) -> ValueProvenance:
    """Receipt entry for the merged ``policy.overrides`` mapping.

    Like ``surface.explicit_scope``, this field is composed rather than
    won outright (D8: "explicit per-``ChangeKind`` override > selected packs
    > base policy" merges across sources instead of picking one), so the
    receipt records the highest-precedence contributor and lists each
    contributing source in ``selected_by``.
    """
    selected_by: list[SelectedByEntry] = []
    source_path = (
        str(policy_file.source_path)
        if policy_file is not None and policy_file.source_path
        else None
    )
    if explicit_overrides:
        selected_by.append(
            SelectedByEntry(layer=layer, option="--policy-file", path=source_path)
        )
    if has_pack_overrides:
        selected_by.append(SelectedByEntry(layer=layer, option="--pack"))
    if not selected_by:
        return ValueProvenance(layer=SelectorLayer.BUILT_IN_DEFAULT)
    return ValueProvenance(
        layer=layer,
        source_kind="policy_file" if explicit_overrides else "pack_manifest",
        path=source_path if explicit_overrides else None,
        selected_by=tuple(selected_by),
    )


# --------------------------------------------------------------------------
# Front-end adapters.
# --------------------------------------------------------------------------

#: ``compare`` kwargs that carry a non-``None`` click default, so their value
#: alone cannot distinguish "the user typed this" from "click filled it in".
#: A live Click caller resolves these with
#: ``ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE``.
DEFAULTED_COMPARE_PARAMETERS: frozenset[str] = frozenset(
    {"policy", "scope_public_headers"}
)


def compare_cli_inputs(
    kwargs: Mapping[str, Any],
    *,
    explicit_parameters: Collection[str] = (),
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
) -> ExplicitCompatibilityInputs:
    """Normalize the ``compare`` command's real kwargs into resolver inputs.

    *kwargs* is the command's own parameter mapping (``cli.py``'s option
    destinations, verbatim). *explicit_parameters* names the parameters the
    user actually typed; it only matters for
    :data:`DEFAULTED_COMPARE_PARAMETERS`, whose click defaults are
    indistinguishable from a stated value. Every other option already uses
    ``None``/``()`` for "not given", so it is read directly.

    Pure: *policy_file* and *suppression* are passed in already loaded, since
    the CLI loads both long before configuration would be resolved and this
    module performs no I/O of its own.
    """
    typed = set(explicit_parameters)

    def _defaulted(name: str) -> Any:
        return kwargs.get(name) if name in typed else None

    public_symbols = tuple(kwargs.get("public_symbols") or ())
    return ExplicitCompatibilityInputs(
        contract_mode=kwargs.get("contract_mode"),
        scope_public_headers=_defaulted("scope_public_headers"),
        policy_base=_defaulted("policy"),
        policy_file=policy_file,
        public_symbols=public_symbols,
        suppression=suppression,
        exit_code_scheme=kwargs.get("exit_code_scheme"),
        severity_preset=kwargs.get("severity_preset"),
        severity_abi_breaking=kwargs.get("severity_abi_breaking"),
        severity_potential_breaking=kwargs.get("severity_potential_breaking"),
        severity_quality_issues=kwargs.get("severity_quality_issues"),
        severity_addition=kwargs.get("severity_addition"),
        pack_paths=tuple(str(p) for p in kwargs.get("pack_paths") or ()),
    )


def compare_request_inputs(
    request: CompareRequest,
    *,
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
) -> ExplicitCompatibilityInputs:
    """Normalize a typed :class:`~abicheck.api_types.CompareRequest`.

    Every field is read as *stated*: unlike the CLI, the typed request has no
    "unset" representation for ``policy`` (defaults to ``"strict_abi"``) or
    ``scope_public`` (defaults to ``True``) -- a caller constructing the
    request chose those values, whether deliberately or by accepting the
    dataclass default. ``checker.compare`` treats the same two the same way
    and for the same reason (see ``_apply_contract_evaluation_shadow``'s
    ``scope_public_headers_is_explicit=True``).

    A ``CompareRequest`` carries no severity, exit-code-scheme, or pack
    inputs: those are gate/reporting concerns the Tier-2 comparison API does
    not own. They resolve to their built-in defaults here, which is what makes
    a CLI run stating none of them cross-front-end equal to the equivalent
    request (:func:`cross_front_end_differences`).
    """
    return ExplicitCompatibilityInputs(
        contract_mode=request.contract_mode,
        scope_public_headers=request.scope_public,
        policy_base=request.policy,
        policy_file=policy_file,
        public_symbols=tuple(sorted(request.force_public_symbols or ())),
        suppression=suppression,
    )


def compatibility_config_from_compare_request(
    request: CompareRequest,
    *,
    policy_file: PolicyFile | None = None,
    suppression: SuppressionSource | None = None,
    project: ProjectCompatibilityInputs | None = None,
) -> CompatibilityEvaluationConfig:
    """One-call adapter: typed request -> resolved effective configuration."""
    return resolve_compatibility_evaluation_config(
        front_end=FrontEnd.API,
        explicit=compare_request_inputs(
            request, policy_file=policy_file, suppression=suppression
        ),
        project=project,
    )


# --------------------------------------------------------------------------
# Phase 1's gate, as an executable comparison.
# --------------------------------------------------------------------------

_SECTIONS = ("contract", "evidence", "surface", "assurance", "policy", "gate")


def _normalized_provenance(prov: ValueProvenance) -> tuple[Any, ...]:
    """*prov* with every front-end-specific detail dropped.

    ADR-049 D7 puts ``explicit_cli`` and ``api_request`` in one precedence
    tier, and the same semantic input is spelled differently by construction
    (``--policy`` vs. the ``policy`` field). Both are legitimately different
    *records of how* a value was stated; neither may change *what* was
    resolved, which is what this comparison checks.
    """
    explicit = {SelectorLayer.EXPLICIT_CLI, SelectorLayer.API_REQUEST}

    def _layer(value: SelectorLayer) -> str:
        return "explicit" if value in explicit else value.value

    return (
        _layer(prov.layer),
        prov.source_kind,
        prov.reference,
        prov.version,
        prov.path,
        prov.field_location,
        tuple(_layer(entry.layer) for entry in prov.selected_by),
        None
        if prov.shadowed_legacy is None
        else _normalized_provenance(prov.shadowed_legacy),
    )


def cross_front_end_differences(
    a: CompatibilityEvaluationConfig, b: CompatibilityEvaluationConfig
) -> list[str]:
    """Every way *a* and *b* differ beyond which front end stated them.

    The executable form of ADR-049 Phase 1's gate: "every front end resolves
    equivalent semantic input to an equal ``CompatibilityEvaluationConfig``
    and provenance receipt." Values must be equal outright; provenance must be
    equal after normalizing the one permitted difference
    (:func:`_normalized_provenance`). Returns a human-readable list so a
    failing comparison says *which* field diverged, not merely that one did.
    """
    differences: list[str] = []
    for section in _SECTIONS:
        if getattr(a, section) != getattr(b, section):
            differences.append(
                f"{section}: {getattr(a, section)!r} != {getattr(b, section)!r}"
            )
    if a.suppressions != b.suppressions:
        differences.append(f"suppressions: {a.suppressions!r} != {b.suppressions!r}")

    keys_a, keys_b = set(a.provenance), set(b.provenance)
    for missing in sorted(keys_a ^ keys_b):
        differences.append(f"provenance: {missing!r} present on only one side")
    for key in sorted(keys_a & keys_b):
        norm_a = _normalized_provenance(a.provenance[key])
        norm_b = _normalized_provenance(b.provenance[key])
        if norm_a != norm_b:
            differences.append(f"provenance[{key!r}]: {norm_a!r} != {norm_b!r}")
    return differences


def cross_front_end_equivalent(
    a: CompatibilityEvaluationConfig, b: CompatibilityEvaluationConfig
) -> bool:
    """``True`` when :func:`cross_front_end_differences` finds nothing."""
    return not cross_front_end_differences(a, b)
