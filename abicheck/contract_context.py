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

"""ADR-049 Phase 4: assembling one :class:`PersistedContractContext` from a
real comparison.

:mod:`abicheck.contract_evidence` owns the block *shapes*;
:mod:`abicheck.contract_evidence_collect` produces the observed
``contract_evidence`` half. This module produces the other two blocks and
binds all three:

- ``evaluation_context`` -- the resolved
  :class:`~abicheck.compatibility_evaluation_config.CompatibilityEvaluationConfig`
  that produced the decisions, with field-level provenance. Built here from
  what ``checker.compare`` itself resolved, which is deliberately narrower
  than what a front end resolves (Phase 1's
  :mod:`abicheck.compatibility_evaluation_frontend` sees CLI/API/project
  inputs this core verb never receives): the provenance therefore records
  ``API_REQUEST`` for a value that arrived as a ``compare()`` argument,
  rather than claiming a CLI/recipe layer it cannot observe. A front end
  that has resolved the canonical object hands it over through
  :func:`with_resolved_config` (Phase 5), and what this module builds is
  what a front end that has not still gets -- under-claiming, since a wrong
  provenance layer is exactly what D7's precedence receipts exist to make
  impossible. The native ``compare`` CLI does hand one over
  (:mod:`abicheck.cli_compare_receipt`); the MCP ``abi_compare`` tool does
  not yet.
- ``decision_receipt`` -- the mode/root-dependent closure and the per-finding
  relevance map. Computed *from the evidence block's own persisted graph*
  (:func:`~abicheck.contract_evidence_collect.closure_from_graph`), not from
  the live surfaces, so the receipt is reproducible by a replay that has only
  the block (plan Section 5.1's round-trip requirement).

Nothing here is authoritative: the assembled context is an audit record of a
shadow evaluation (ADR-049 Phase 3), never consulted by verdict, policy, or
exit-code logic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from .change_registry_types import Verdict
from .checker_types import Change
from .compatibility_evaluation_config import (
    AssuranceConfig,
    CompatibilityEvaluationConfig,
    CompatibilityPolicyConfig,
    ContractConfig,
    DigestedItems,
    EvidenceConfig,
    GateConfig,
    SelectedByEntry,
    SuppressionConfig,
    SurfaceConfig,
    ValueProvenance,
)
from .contract_evidence import (
    ContractEvidenceBlock,
    DecisionReceiptBlock,
    EvaluationContextBlock,
    EvidenceSearchRecord,
    PersistedContractContext,
    TypeGraphSnapshot,
)
from .contract_evidence_collect import (
    DOMAIN_ROOT_PROVIDER,
    PROVIDER_FORCED_PUBLIC,
    PROVIDER_POST_MANIFEST,
    PROVIDER_PUBLIC_HEADER,
    closure_from_graph,
    content_digest,
    graph_node_index,
)
from .contract_relevance_types import (
    ContractMode,
    ContractRelevance,
    SelectorLayer,
    coerce_contract_mode,
)
from .severity import SeverityConfig

_API_REQUEST_REFERENCE = "checker.compare"


def _api_provenance(option: str) -> ValueProvenance:
    """Provenance for a value that reached ``compare()`` as an argument.

    ``API_REQUEST`` is D7's own layer for exactly this -- a typed caller
    stating a value -- and sits at the same precedence rank as
    ``EXPLICIT_CLI``. Claiming ``EXPLICIT_CLI`` here would assert a CLI
    option was typed, which this core verb cannot know.
    """
    return ValueProvenance(
        layer=SelectorLayer.API_REQUEST,
        source_kind="api_argument",
        reference=_API_REQUEST_REFERENCE,
        field_location=option,
        selected_by=(SelectedByEntry(layer=SelectorLayer.API_REQUEST, option=option),),
    )


#: The providers in a :class:`ContractEvidenceBlock` that are *explicit
#: overlays* -- a run-level input asserting membership -- rather than
#: observations of the library itself. ADR-049 D2 counts overlay-selected
#: roots as part of the ``public`` domain's root set, so a resolved
#: configuration that omitted them would understate what decided the run.
_OVERLAY_PROVIDERS = (PROVIDER_POST_MANIFEST, PROVIDER_FORCED_PUBLIC)


@dataclass(frozen=True)
class OverlaySelection:
    """The configuration half of whatever overlays a run applied.

    ``selectors`` names which overlay sources were selected (the same
    provider ids the evidence ledger records them under), and ``scope`` is
    their combined, content-digested item list.
    """

    selectors: tuple[str, ...]
    scope: DigestedItems | None


def overlay_selection(evidence: ContractEvidenceBlock) -> OverlaySelection:
    """Recover the run's explicit overlays from its own evidence ledger.

    ``--post-manifest``'s committed-export allowlist and
    ``--public-symbol``'s forced-public set both genuinely decide contract
    membership, so a persisted ``evaluation_context`` that left
    ``contract.overlays``/``surface.explicit_scope`` empty described a run
    that never happened -- exactly the class of wrong-resolved-configuration
    a D7 provenance receipt exists to prevent (Codex review, fresh evidence).

    Derived from the already-collected block rather than from the caller's
    own arguments so the two halves of one persisted context cannot disagree
    about which overlays applied. The digest combines each overlay's own
    per-provider ``input_identity`` (computed by
    :func:`~abicheck.contract_evidence_collect.content_digest` over the same
    items) rather than re-digesting the merged list: a combined list alone
    could not tell "one manifest naming A and B" apart from "two overlays
    naming A and B respectively", which is a real difference on replay.
    Returns no ``scope`` at all when no overlay was selected -- the
    documented difference between an unselected source and one that resolved
    to nothing.
    """
    selectors: set[str] = set()
    items: set[str] = set()
    digests: dict[str, str] = {}
    for entry in evidence.providers:
        provider = entry.record.provider
        if provider not in _OVERLAY_PROVIDERS:
            continue
        selectors.add(provider)
        items.update(entry.manifests)
        identity = entry.record.input_identity
        if identity is not None:
            # Both sides carry an identical record for one overlay (a
            # run-level input applied to whichever side a finding is judged
            # on), so keying by provider collapses the pair rather than
            # digesting the same content twice.
            digests[provider] = identity.sha256
    if not selectors:
        return OverlaySelection(selectors=(), scope=None)
    return OverlaySelection(
        selectors=tuple(sorted(selectors)),
        scope=DigestedItems(sha256=content_digest(digests), items=tuple(sorted(items))),
    )


def build_evaluation_context(
    *,
    mode: ContractMode,
    mode_provenance: ValueProvenance | None = None,
    policy: str = "strict_abi",
    policy_from_file: bool = False,
    internal_namespaces: Iterable[str] = (),
    internal_namespaces_stated: bool = False,
    policy_overrides: Mapping[str, Verdict] | None = None,
    suppressions: SuppressionConfig | None = None,
    overlays: OverlaySelection | None = None,
) -> EvaluationContextBlock:
    """The ``evaluation_context`` block for one comparison.

    *mode_provenance* is the receipt
    :func:`~abicheck.compatibility_evaluation_wiring.resolve_legacy_contract_mode`
    already returns for the legacy-alias path (``LEGACY_ALIAS``); pass it
    through rather than re-deriving, so the persisted receipt says which of
    D7's layers actually selected the domain. ``None`` means the caller
    stated the mode explicitly, which is recorded as ``API_REQUEST``.

    *policy_overrides* is the per-``ChangeKind`` override map a
    ``--policy-file`` contributes (``PolicyFile.overrides``). It belongs in
    the persisted context because it genuinely affected the comparison:
    recording only the base-policy name would make the "resolved"
    configuration wrong in exactly the way an audit consumer would act on
    (Codex review, fresh evidence). Its provenance is recorded as the
    policy-file source that supplied it.

    *suppressions* is the selected suppression source, already reduced to a
    :class:`~abicheck.compatibility_evaluation_config.SuppressionConfig` by
    :func:`suppression_config_for`. ``None`` means no source was selected --
    a different fact from a source that resolved to zero rules, which is
    exactly the distinction that class exists to preserve.

    *internal_namespaces* is the same ``--policy-file`` list
    (``PolicyFile.internal_namespaces``) that shaped which declarations the
    comparison treated as internal, so it gets a provenance entry of its own
    rather than sitting in the resolved config with no recorded source (Codex
    review) -- the identical rule *policy_overrides* follows, for the
    identical reason and from the identical file. *internal_namespaces_stated*
    is what keeps an explicitly empty list ("this project has none") from
    reading as an absent one: the tuple alone collapses both to ``()``, so a
    caller that can tell them apart -- ``PolicyFile.internal_namespaces_stated``
    -- forwards the distinction here, and a stated empty list keeps its
    provenance (CodeRabbit review). Neither stated nor populated means no
    policy file contributed, and claiming a source then would be a fabricated
    receipt.

    *overlays* is what :func:`overlay_selection` recovered from the run's own
    evidence ledger; ``None`` means no overlay was applied.
    """
    mode = coerce_contract_mode(mode)
    from .compatibility_evaluation_frontend import builtin_policy_identity

    overlays = overlays or OverlaySelection(selectors=(), scope=None)
    resolved_namespaces = tuple(internal_namespaces)
    provenance = {
        "contract.mode": mode_provenance or _api_provenance("contract_mode"),
        # A policy file's own `base_policy` *overrides* the `policy` argument
        # in `checker.compare` (`effective_policy`), so attributing the
        # resolved value to `policy` would name an input the run ignored
        # (Codex review, fresh evidence). Same rule, same file, as
        # `policy.overrides` and `surface.internal_namespaces` below.
        "policy.base": _api_provenance("policy_file" if policy_from_file else "policy"),
    }
    if resolved_namespaces or internal_namespaces_stated:
        provenance["surface.internal_namespaces"] = _api_provenance("policy_file")
    if policy_overrides:
        provenance["policy.overrides"] = _api_provenance("policy_file")
    if suppressions is not None:
        provenance["suppressions"] = _api_provenance("suppression")
    if overlays.selectors:
        provenance["contract.overlays"] = _api_provenance("overlays")
    if overlays.scope is not None:
        provenance["surface.explicit_scope"] = _api_provenance("overlays")
    config = CompatibilityEvaluationConfig(
        contract=ContractConfig(mode=mode, overlays=overlays.selectors),
        evidence=EvidenceConfig(),
        surface=SurfaceConfig(
            internal_namespaces=resolved_namespaces,
            explicit_scope=overlays.scope,
        ),
        assurance=AssuranceConfig(),
        policy=CompatibilityPolicyConfig(
            base=builtin_policy_identity(policy),
            overrides=dict(policy_overrides or {}),
        ),
        gate=GateConfig(),
        suppressions=suppressions,
        provenance=provenance,
    )
    return EvaluationContextBlock(resolved_config=config)


def _merged_overlay_provenance(
    *, stated: ValueProvenance | None, observed: ValueProvenance | None
) -> ValueProvenance | None:
    """The receipt for an overlay field that was both stated and observed.

    *stated* is the front end's entry (which option, which file, which
    digest); *observed* is the ledger's (that an overlay really applied).
    Neither subsumes the other, and a run can have only one of them -- a
    ``--post-manifest`` scope the front end cannot model, or a resolved value
    on a run whose ledger recorded no overlay -- so the merge keeps the
    richer entry and appends the other's hops rather than choosing between
    them. Duplicate hops are dropped, since a merged receipt naming the same
    selector twice would misreport one selection as two.
    """
    if stated is None:
        return observed
    if observed is None:
        return stated
    extra = tuple(h for h in observed.selected_by if h not in stated.selected_by)
    if not extra:
        return stated
    return replace(stated, selected_by=stated.selected_by + extra)


def with_resolved_config(
    context: PersistedContractContext,
    config: CompatibilityEvaluationConfig,
) -> PersistedContractContext:
    """Return *context* carrying the front end's own already-resolved config.

    ADR-049 Phase 5's "every front end consumes one
    :class:`CompatibilityEvaluationConfig`", as the one seam where that
    object replaces the narrower one ``checker.compare`` reconstructs from
    its own arguments. The core verb sees values, not the inputs that chose
    them, so it can claim no more than ``API_REQUEST`` for any of them (see
    this module's docstring);
    :func:`~abicheck.compatibility_evaluation_frontend.resolve_compatibility_evaluation_config`
    sees the CLI flags, the project config, and the selected packs, and
    resolves the real D7 layer per field.

    **Two overlay fields keep their observed values.**
    ``contract.overlays`` and ``surface.explicit_scope`` are not resolved
    from stated inputs at all: :func:`overlay_selection` recovers them from
    the run's *own evidence ledger*, so they name the overlays that actually
    applied -- including ``--post-manifest``, which no front-end input model
    describes. An observation of what ran outranks a resolution of what was
    asked for, so when the ledger recorded any overlay, both values survive.

    Their *provenance* follows a different rule, because value and receipt
    answer different questions. The core's entry for
    ``surface.explicit_scope`` is a contentless ``API_REQUEST`` hop, while
    the resolver's names the layer, the option, and -- for
    ``--public-symbols-list`` -- the file and digest that selected the scope.
    Taking the core's wholesale therefore dropped exactly the identification
    a replay needs (Codex review, fresh evidence). So when the front end
    really resolved a value of its own, its entry is kept and the observed
    hop is appended to it, leaving both recorded; when it did not (a
    ``--post-manifest``-only run, which it cannot model), the core's stands
    alone.

    ``contract.overlays`` is the one field where both sides can also state a
    *value*: a ``kind: contract`` pack assigns overlay selectors, and the
    ledger observes provider ids -- two different sets, both genuinely in
    effect. So that field's value is their union, not a replacement
    (CodeRabbit review: replacing dropped a pack's selection and its
    provenance whenever any overlay was observed).

    What this deliberately does *not* touch: the gate. Its values are
    resolved after ``compare()`` returns and are written by
    :func:`with_resolved_gate` from the configuration the run was really
    scored with -- see that function, and
    ``cli_compare_receipt.record_resolved_config`` for why the two are
    written from different sources.
    """
    from .compatibility_evaluation_frontend import (
        CONTRACT_OVERLAYS_FIELD,
        EXPLICIT_SCOPE_FIELD,
    )

    observed = context.evaluation_context.resolved_config
    if observed.contract.overlays:
        provenance = dict(config.provenance)
        # A `kind: contract` pack can assign `contract.overlays` too
        # (`compatibility_evaluation_wiring`'s route table), and those
        # selectors are a different set from the providers the ledger
        # observed -- both were genuinely in effect, so replacing one with
        # the other dropped a real selection and its receipt (CodeRabbit
        # review). Union the values, merge the entries.
        overlays = tuple(
            sorted({*config.contract.overlays, *observed.contract.overlays})
        )
        for field, entry in (
            (
                CONTRACT_OVERLAYS_FIELD,
                _merged_overlay_provenance(
                    stated=(
                        config.provenance.get(CONTRACT_OVERLAYS_FIELD)
                        if config.contract.overlays
                        else None
                    ),
                    observed=observed.provenance.get(CONTRACT_OVERLAYS_FIELD),
                ),
            ),
            (
                EXPLICIT_SCOPE_FIELD,
                _merged_overlay_provenance(
                    stated=(
                        config.provenance.get(EXPLICIT_SCOPE_FIELD)
                        if config.surface.explicit_scope is not None
                        else None
                    ),
                    observed=observed.provenance.get(EXPLICIT_SCOPE_FIELD),
                ),
            ),
        ):
            if entry is None:
                provenance.pop(field, None)
            else:
                provenance[field] = entry
        config = replace(
            config,
            contract=replace(config.contract, overlays=overlays),
            surface=replace(
                config.surface, explicit_scope=observed.surface.explicit_scope
            ),
            provenance=provenance,
        )
    return replace(
        context,
        evaluation_context=replace(context.evaluation_context, resolved_config=config),
    )


def with_resolved_gate(
    context: PersistedContractContext,
    *,
    exit_code_scheme: str,
    severity: SeverityConfig,
    severity_provenance: Mapping[str, ValueProvenance],
) -> PersistedContractContext:
    """Return *context* with the front end's real gate configuration recorded.

    :func:`build_evaluation_context` runs inside ``checker.compare``, which
    never sees the gate: the exit-code scheme and severity levels are
    resolved by the front end and applied to the returned result *after* the
    core verb finishes. So it recorded a default :class:`GateConfig` --
    which claims the built-in ``severity`` scheme and the built-in severity
    levels for *every* run, including a ``legacy``-scheme one and one whose
    a severity setting moved a category (Codex review, fresh evidence).
    The persisted ``evaluation_context`` is documented as the *complete*
    resolved configuration, so that default is a false receipt of the same
    kind a fabricated digest would be, not a harmless omission.

    A front end that has resolved its gate calls this once, before the
    context is serialized, and supplies the provenance layer it actually
    resolved each field from -- which the core verb cannot know and this
    function therefore does not guess.

    *severity_provenance* is keyed **per category**, not one entry for the
    whole block, because the four categories are resolved independently and
    routinely come from different layers: ``severity.abi_breaking: error``
    on the command line beside an ``addition`` level only ``.abicheck.yml``
    supplied. Collapsing them into a single ``gate.severity`` entry labelled
    the project-config category as CLI-selected, and used a key the canonical
    resolver does not have -- it tracks ``gate.severity.<category>``
    (``compatibility_evaluation_frontend.SEVERITY_CATEGORY_FIELDS``), which
    is what this writes (Codex review, fresh evidence). An unsupplied
    category simply gets no entry, the same "absent, not defaulted" rule the
    rest of this receipt follows.

    The gate is still ``NOT_APPLICABLE`` to contract membership
    (:class:`GateConfig`): nothing here changes a relevance decision, a
    closure, or the receipt's per-finding map -- only what the audit record
    says about how the run was gated. *exit_code_scheme* itself carries no
    provenance entry any more (PR G2 deleted the manual selector; purely
    derived now).
    """
    from .compatibility_evaluation_frontend import SEVERITY_CATEGORY_FIELDS

    config = context.evaluation_context.resolved_config
    provenance = dict(config.provenance)
    for category, entry in severity_provenance.items():
        provenance[SEVERITY_CATEGORY_FIELDS[category]] = entry
    return replace(
        context,
        evaluation_context=replace(
            context.evaluation_context,
            resolved_config=replace(
                config,
                gate=GateConfig(
                    exit_code_scheme=exit_code_scheme,
                    preset=config.gate.preset,
                    packs=config.gate.packs,
                    severity=severity,
                    require_complete_analysis=config.gate.require_complete_analysis,
                    scope=config.gate.scope,
                ),
                provenance=provenance,
            ),
        ),
    )


def suppression_config_for(suppression: object) -> SuppressionConfig | None:
    """Reduce a live ``SuppressionList`` to its persisted configuration.

    The rules and the source digest both affected which findings this
    comparison suppressed, so a context that left ``suppressions`` at
    ``None`` claimed no suppression source was selected at all and could not
    reconstruct the run (Codex review, fresh evidence). ``rule_identities()``
    is the list's own machine-facing receipt spelling -- built for exactly
    this field -- so nothing is re-derived here.

    Returns ``None`` only when no list was supplied at all.

    A list *without* a ``source_sha256`` is still a selected source: the
    public constructor and :meth:`~abicheck.suppression.SuppressionList.merge`
    both produce this digest-less but fully active form -- the ABICC
    compatibility front end (``compat/_helpers.py``) builds every one of its
    ``-skip-symbols``/``-skip-internal-*`` lists that way, and ``merge()``
    drops both inputs' digests even when each half *was* read from a file.
    Returning ``None`` for those recorded "no suppression source was selected
    at all" while rules were actively suppressing findings (Codex review,
    fresh evidence) -- the same absent-vs-empty conflation
    :class:`SuppressionConfig` exists to prevent, one layer up.

    So the digest falls back to a content digest of :meth:`rule_identities`
    itself. That is not a fabricated stand-in for the file digest: those
    identities are the canonical spelling of the rules that actually ran, are
    persisted verbatim in the same block, and the digest is computed over
    them with :func:`~abicheck.contract_evidence_collect.content_digest`, the
    ledger's own convention. Nothing in this codebase re-reads a suppression
    file to verify this field against its bytes, so the two cases differ only
    in *what content* the digest authenticates -- and in both cases it
    authenticates content the receipt itself carries.
    """
    if suppression is None:
        return None
    identities = getattr(suppression, "rule_identities", None)
    if not callable(identities):
        return None
    rules = tuple(identities())
    digest = getattr(suppression, "source_sha256", None) or content_digest(list(rules))
    return SuppressionConfig(sha256=digest, rules=rules)


@dataclass(frozen=True)
class PersistedDomainView:
    """One contract mode's view of a persisted evidence block, per side.

    The single implementation of "which providers supply this mode's roots,
    which entry carries the type graph, and what does the closure from those
    roots look like." Both consumers -- the receipt builder here and
    :class:`abicheck.contract_replay._PersistedDomain` -- read it rather than
    each walking ``evidence.providers`` themselves, so a re-evaluated closure
    cannot silently disagree with the persisted receipt (CodeRabbit review:
    the two copies were the exact divergence ``finding_key`` and
    ``authoritative_side`` were centralized to prevent).
    """

    #: Every node this mode counts as a root, overlay-named ones included --
    #: what the receipt reports as ``evaluated_contract_roots``.
    roots_by_side: dict[str, set[str]]
    #: The subset of :attr:`roots_by_side` the type closure is actually walked
    #: from: the *root provider's* own declarations, never an overlay's. Being
    #: named by ``--public-symbol``/``--post-manifest`` puts an entity in
    #: contract; it does not make that entity's signature types roots too (see
    #: :func:`persisted_domain_view`).
    closure_seeds_by_side: dict[str, set[str]]
    graph_by_side: dict[str, TypeGraphSnapshot]
    root_record_by_side: dict[str, EvidenceSearchRecord]
    header_record_by_side: dict[str, EvidenceSearchRecord]
    closure_by_side: dict[str, frozenset[str]]
    #: ``{side: {overlay record id: the nodes that overlay names}}``. A
    #: *direct-match* set, not a closure seed: an entity whose own nodes land
    #: here is in contract because the user said so, mirroring the live
    #: evaluator's own per-finding overlay overrides -- but nothing reachable
    #: *from* it is (see :func:`persisted_domain_view`). A decision resting on
    #: one of these nodes must also cite that record, not the header provider
    #: that merely supplied the graph (Codex review).
    overlay_roots_by_side: dict[str, dict[str, set[str]]]
    #: ``{side: (the manifest's record, the exact spellings it commits)}`` for
    #: a run configured with ``--post-manifest``, collected **in every mode**
    #: -- unlike :attr:`overlay_roots_by_side`, which is a ``public``-domain
    #: root contribution. The manifest's *exclusion* half also binds ``all``
    #: (the live evaluator checks it ahead of its own ``all``-mode shortcut),
    #: and spellings rather than resolved nodes are what that check needs,
    #: since it compares against ``Change.symbol`` verbatim. Absent for a run
    #: with no manifest; present with an empty spelling set for a manifest
    #: that commits to nothing, which is a selected source scoping everything
    #: out rather than an absent one.
    post_manifest_by_side: dict[str, tuple[EvidenceSearchRecord, frozenset[str]]]


#: Whether an overlay provider's own live matching follows lookup aliases.
#: ``--post-manifest`` is matched against ``Change.symbol`` verbatim by
#: ``contract_evaluation``, so its persisted roots must resolve exactly:
#: routing a bare ``foo`` through the alias tier also roots an unexported
#: ``ns::foo`` that carries the same bare tail, turning a live
#: ``PROVEN_OUT_OF_CONTRACT`` into a replayed ``IN_CONTRACT`` -- a flip
#: :func:`~abicheck.contract_replay.compare_decisions` rejects (Codex review,
#: fresh evidence). ``--public-symbol`` keeps the looser tier, matching its
#: own live semantics.
_OVERLAY_FOLLOWS_ALIASES: Mapping[str, bool] = {
    PROVIDER_POST_MANIFEST: False,
    PROVIDER_FORCED_PUBLIC: True,
}


def _resolved_overlay_roots(
    graph: TypeGraphSnapshot, spellings: Iterable[str], *, follow_aliases: bool
) -> set[str]:
    """Canonical node ids the overlay *spellings* name in *graph*.

    Built through :func:`~abicheck.contract_evidence_collect.graph_node_index`
    so an overlay entry resolves by the same rule its own live matching uses
    -- see :data:`_OVERLAY_FOLLOWS_ALIASES` for why that rule is per-provider
    rather than one shared tier.
    """
    spellings = tuple(spellings)
    if not spellings:
        return set()
    index = graph_node_index(graph, follow_aliases=follow_aliases)
    out: set[str] = set()
    for spelling in spellings:
        out |= index.get(spelling, set())
    return out


def persisted_domain_view(
    evidence: ContractEvidenceBlock, mode: ContractMode
) -> PersistedDomainView:
    """Gather *mode*'s roots, graphs, records, and per-side closure.

    The closure is walked over each side's own persisted type graph from that
    side's own roots -- never across sides, which would let an old-side root
    reach a new-side type that no old-side signature references.

    An explicit overlay (``--public-symbol``/``--post-manifest``) contributes
    roots to the ``public`` domain, exactly as ADR-049 D2 says ("roots
    selected by explicit overlays"). Without them, an entity the live run kept
    *because* the user named it re-evaluated to ``UNKNOWN_UNRESOLVED`` even
    though the overlay's own evidence entry sits in the same block (Codex
    review, fresh evidence). The overlay's manifest holds spellings, not node
    ids, so each is resolved through the persisted graph the same way a
    finding's own spelling is; one naming nothing the graph knows contributes
    no root rather than a guess.

    They are roots, but they are **not closure seeds** -- hence the separate
    ``closure_seeds_by_side``. Both live overlay checks are per-finding
    overrides (``force_public_symbols`` matches the finding's own symbol,
    ``public_surface_allowlist`` matches it exactly) and neither widens the
    surface the walk starts from. Seeding the walk with an overlay node pulled
    that declaration's whole signature closure in with it, so forcing
    ``hidden_api(Secret *)`` public turned a live ``PROVEN_OUT_OF_CONTRACT``
    on ``Secret`` into a replayed ``IN_CONTRACT`` (Codex review, fresh
    evidence).

    A ``--post-manifest`` run's own spellings are additionally reported in
    :attr:`~PersistedDomainView.post_manifest_by_side` for **every** mode,
    root contribution or not: the manifest's exclusion half binds ``all`` too
    (Codex review, fresh evidence -- see that attribute).
    """
    mode = coerce_contract_mode(mode)
    provider = DOMAIN_ROOT_PROVIDER[mode]
    roots_by_side: dict[str, set[str]] = {}
    graph_by_side: dict[str, TypeGraphSnapshot] = {}
    root_record_by_side: dict[str, EvidenceSearchRecord] = {}
    header_record_by_side: dict[str, EvidenceSearchRecord] = {}
    # Kept per (side, record) rather than pooled: the resolution rule differs
    # by provider, and a replayed decision must be able to cite the overlay
    # its root actually came from instead of the header provider (Codex
    # review, fresh evidence).
    overlay_entries: list[tuple[str, EvidenceSearchRecord, tuple[str, ...]]] = []
    post_manifest_by_side: dict[str, tuple[EvidenceSearchRecord, frozenset[str]]] = {}
    for entry in evidence.providers:
        side = entry.record.side
        if provider is not None and entry.record.provider == provider:
            roots_by_side.setdefault(side, set()).update(entry.declarations)
            root_record_by_side[side] = entry.record
        if entry.record.provider == PROVIDER_PUBLIC_HEADER:
            graph_by_side[side] = entry.type_graph
            header_record_by_side[side] = entry.record
        if entry.record.provider == PROVIDER_POST_MANIFEST:
            post_manifest_by_side[side] = (entry.record, frozenset(entry.manifests))
        if mode is ContractMode.PUBLIC and entry.record.provider in _OVERLAY_PROVIDERS:
            overlay_entries.append((side, entry.record, entry.manifests))
    # A ``--post-manifest`` overlay *narrows*: the manifest states the whole
    # committed-export surface, and `post_processing` treats a public
    # declaration missing from it as scoped out. Unioning it into the header
    # roots modelled it as purely additive, so a public symbol the manifest
    # deliberately omits stayed rooted and a live
    # ``PROVEN_OUT_OF_CONTRACT`` replayed as ``IN_CONTRACT`` (Codex review,
    # fresh evidence). When one is present, it *replaces* the header roots;
    # ``--public-symbol`` still only ever widens whatever remains.
    narrowing = {
        side
        for side, record, _ in overlay_entries
        if record.provider == PROVIDER_POST_MANIFEST
    }
    for side in narrowing:
        roots_by_side[side] = set()
    overlay_roots_by_side: dict[str, dict[str, set[str]]] = {}
    for side, record, manifests in overlay_entries:
        graph = graph_by_side.get(side)
        if graph is None:
            continue
        extra = _resolved_overlay_roots(
            graph,
            manifests,
            follow_aliases=_OVERLAY_FOLLOWS_ALIASES[record.provider],
        )
        if not extra:
            continue
        overlay_roots_by_side.setdefault(side, {})[record.id] = extra
    # Snapshotted before the overlay nodes are folded in: the walk starts from
    # the root provider's own declarations only.
    closure_seeds_by_side = {side: set(nodes) for side, nodes in roots_by_side.items()}
    for side, by_record in overlay_roots_by_side.items():
        for extra in by_record.values():
            roots_by_side.setdefault(side, set()).update(extra)
    closure_by_side = {
        side: closure_from_graph(graph, closure_seeds_by_side.get(side, set()))
        for side, graph in graph_by_side.items()
    }
    return PersistedDomainView(
        roots_by_side=roots_by_side,
        closure_seeds_by_side=closure_seeds_by_side,
        graph_by_side=graph_by_side,
        root_record_by_side=root_record_by_side,
        header_record_by_side=header_record_by_side,
        closure_by_side=closure_by_side,
        overlay_roots_by_side=overlay_roots_by_side,
        post_manifest_by_side=post_manifest_by_side,
    )


def domain_roots(
    evidence: ContractEvidenceBlock, mode: ContractMode
) -> tuple[str, ...]:
    """The persisted root declarations of *mode*'s domain, both sides unioned.

    ``all`` has no root provider (ADR-049 D2: no root/closure evidence is
    required for it), so it has no roots here either -- an empty tuple, not
    "every declaration". Reporting the full declaration set for ``all`` would
    read as a computed closure that the mode by definition never computes.
    """
    view = persisted_domain_view(evidence, mode)
    return tuple(
        sorted(
            set().union(*view.roots_by_side.values()) if view.roots_by_side else set()
        )
    )


def build_decision_receipt(
    evidence: ContractEvidenceBlock,
    mode: ContractMode,
    relevance_by_finding: Mapping[str, ContractRelevance] | None = None,
) -> DecisionReceiptBlock:
    """The ``decision_receipt`` block: roots, closure, per-finding relevance."""
    view = persisted_domain_view(evidence, mode)
    closure: set[str] = set()
    for side, side_closure in view.closure_by_side.items():
        if view.closure_seeds_by_side.get(side):
            closure |= side_closure
    return DecisionReceiptBlock(
        evaluated_contract_roots=domain_roots(evidence, mode),
        evaluated_type_closure=tuple(sorted(closure)),
        relevance_by_finding=dict(relevance_by_finding or {}),
    )


def relevance_map(
    changes: Sequence[Change], finding_id: Callable[[Change], object] | None = None
) -> dict[str, ContractRelevance]:
    """``{finding_id: relevance}`` for every already-stamped *change*.

    *finding_id* is injected as a callable rather than imported, for the same
    reason :func:`~abicheck.contract_evaluation.stamp_scoped_result_findings`
    injects it: importing ``reporter._finding_id`` here would close a
    ``checker -> contract_context -> reporter -> checker`` import cycle the
    ``import-cycle-growth`` AI-readiness gate rejects. With no callable given,
    findings are keyed by ``"<kind>:<symbol>"``, the same stable pair the
    report's own id is built from.
    """
    out: dict[str, ContractRelevance] = {}
    for change in changes:
        if change.contract_relevance is None:
            continue
        out[finding_key(change, finding_id)] = change.contract_relevance
    return out


def finding_key(
    change: Change, finding_id: Callable[[Change], object] | None = None
) -> str:
    """The receipt key for one finding -- see :func:`relevance_map`.

    Shared with :mod:`abicheck.contract_replay` so a re-evaluation's keys
    line up with the original receipt's without either side re-deriving the
    convention (a silent key mismatch would make every finding look newly
    added rather than re-decided).
    """
    if finding_id is not None:
        return str(finding_id(change))
    return f"{change.kind.value}:{change.symbol or ''}"


def build_persisted_context(
    evidence: ContractEvidenceBlock,
    *,
    mode: ContractMode,
    mode_provenance: ValueProvenance | None = None,
    policy: str = "strict_abi",
    policy_from_file: bool = False,
    internal_namespaces: Iterable[str] = (),
    internal_namespaces_stated: bool = False,
    policy_overrides: Mapping[str, Verdict] | None = None,
    suppressions: SuppressionConfig | None = None,
    changes: Sequence[Change] = (),
    finding_id: Callable[[Change], object] | None = None,
) -> PersistedContractContext:
    """Bind the three blocks ADR-049 Section 5.1 persists together."""
    return PersistedContractContext(
        contract_evidence=evidence,
        evaluation_context=build_evaluation_context(
            mode=mode,
            mode_provenance=mode_provenance,
            policy=policy,
            policy_from_file=policy_from_file,
            internal_namespaces=internal_namespaces,
            internal_namespaces_stated=internal_namespaces_stated,
            policy_overrides=policy_overrides,
            suppressions=suppressions,
            overlays=overlay_selection(evidence),
        ),
        decision_receipt=build_decision_receipt(
            evidence, mode, relevance_map(changes, finding_id)
        ),
    )
