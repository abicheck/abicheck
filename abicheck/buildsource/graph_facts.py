# Copyright 2026 Nikolay Petrov
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

"""L5 source-graph node/edge schema (ADR-031 D2) and the ADR-046 D1/D2
evidence-preserving fact merge. Split out of ``source_graph.py`` (moved here
across two rounds — first the merge machinery, then ``GraphNode``/
``GraphEdge`` themselves) to keep that module under the AI-readiness
line-count cap; ``source_graph.py`` imports and re-exports every public name
here so existing ``from .source_graph import GraphNode``/``CONF_HIGH`` etc.
call sites are unaffected.

Replaces the v1 first-writer-wins ``SourceGraphSummary.add_node``/``add_edge``
behavior: a node or edge accumulates one :class:`GraphFact` per producer that
ever registered it, folded into one order-independent ``resolved`` dict via
:func:`merge_graph_facts`, with genuine cross-producer disagreements recorded
as :class:`FactConflict` instead of silently dropped (D2). :func:`edge_relation_key`
adds a role-aware edge identity alongside the coarse ``(src, dst, kind)`` one,
and :func:`edge_occurrence_id` layers an opt-in per-call-site occurrence
identity on top of it (D1). See ADR-046 and
``docs/contribute/plans/g29-impact-analysis-layer.md`` Phase 2.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..name_classification import (
    _declaring_header_discriminator,
    _quoted_spans,
    strip_anonymous_type_location,
)

#: Confidence labels (ADR-031 D9). Mirrors the evidence-model vocabulary so the
#: coverage report and graph speak the same language. Canonical home of these
#: constants — ``source_graph.py`` re-exports them for backward compatibility.
CONF_HIGH = "high"
CONF_REDUCED = "reduced"
CONF_UNKNOWN = "unknown"

#: Confidence precedence for the merge below — higher ranks resolve a per-key
#: disagreement first. Anything not in this mapping (an unrecognized
#: confidence label from a hand-built/future pack) ranks alongside
#: ``CONF_UNKNOWN`` rather than erroring.
_CONFIDENCE_RANK: dict[str, int] = {CONF_HIGH: 2, CONF_REDUCED: 1, CONF_UNKNOWN: 0}

#: The consumer half of the graph vocabulary (G29 Phase 4, ADR-057) —
#: ``source_graph.NODE_KINDS``/``EDGE_KINDS`` union these in, and
#: ``abicheck.impact.consumer_graph`` (which populates them) re-exports them.
#:
#: They live in this leaf module rather than beside the rest of the vocabulary
#: for two reasons: ``source_graph.py`` sits at its 2000-line hard cap, and
#: the producer imports ``source_graph`` (via a function-local import) so
#: ``source_graph`` cannot import the producer back without forming a cycle.
#:
#: Only ``consumer_binary``, ``CONSUMER_REQUIRES_SYMBOL`` and
#: ``CONSUMER_REQUIRES_VERSION`` are populated today. The rest are **reserved**
#: — same "registered so a hand-built or newer graph naming one is never
#: rejected, but no normalized data source yet" pattern as the archive/linker
#: kinds in ``source_graph.py``: a consumer's *compiled* header/instantiation
#: dependencies need consumer-side build evidence, and a runtime resolution
#: failure needs trace ingestion, both later slices of the same phase.
#:
#: Deliberately no ``consumer_required_symbol`` node kind: a requirement is an
#: edge onto the existing ``binary_symbol`` node, which is what makes the
#: consumer and library graphs join on one shared node id at all.
CONSUMER_NODE_KINDS: frozenset[str] = frozenset(
    {"consumer_binary", "consumer_object", "runtime_probe"}
)
CONSUMER_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "CONSUMER_REQUIRES_SYMBOL",
        "CONSUMER_REQUIRES_VERSION",
        "CONSUMER_INSTANTIATES_DECL",
        "CONSUMER_COMPILED_FROM_HEADER",
        "RUNTIME_FAILED_TO_RESOLVE_SYMBOL",
    }
)

#: The use-case half of the graph vocabulary (G29 Phase 4 slice 2, ADR-057
#: amendment) — ``source_graph.NODE_KINDS``/``EDGE_KINDS`` union these in the
#: same way ``CONSUMER_NODE_KINDS``/``CONSUMER_EDGE_KINDS`` above are unioned
#: in, and ``abicheck.impact.use_cases`` (which populates them) re-exports
#: them. Same two reasons for living in this leaf module rather than beside
#: the rest of the vocabulary: ``source_graph.py`` is at its 2000-line hard
#: cap, and the producer imports ``source_graph`` (function-local, to avoid a
#: cycle), so ``source_graph`` cannot import the producer back.
#:
#: A ``use_case``/``test_case`` node is declared by an optional, hand-authored
#: ``impact-use-cases.yaml`` manifest — deliberately a **separate** schema
#: from ``docs/contribute/usecase-registry.yaml`` (which tracks abicheck's own
#: feature coverage, not a consumer project's business use cases; conflating
#: the two would read "abicheck supports header-only analysis" and "the DAL
#: training workflow uses ``train()``" as the same kind of fact).
#:
#: Only ``USE_CASE_USES_ENTRY`` and ``TEST_COVERS_USE_CASE`` are populated in
#: this slice, both from the manifest alone. ``TRACE_OBSERVED_ENTRY``/
#: ``TRACE_OBSERVED_EDGE`` are **reserved** — same "registered so a hand-built
#: or newer graph naming one is never rejected, but no normalized data source
#: yet" pattern ``CONSUMER_INSTANTIATES_DECL`` etc. use above: runtime-trace
#: ingestion is explicitly out of scope for this slice (ADR-057's
#: "Deliberately not implemented this slice" — absence of a trace must never
#: read as "not used", a semantics decision with no data to validate against
#: yet).
USE_CASE_NODE_KINDS: frozenset[str] = frozenset({"use_case", "test_case"})
USE_CASE_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "USE_CASE_USES_ENTRY",
        "TEST_COVERS_USE_CASE",
        "TRACE_OBSERVED_ENTRY",
        "TRACE_OBSERVED_EDGE",
    }
)

#: The template-instantiation half of the graph vocabulary (G29 Phase 5 item
#: 1, G29.6's first-priority open graph family) — ``source_graph.NODE_KINDS``/
#: ``EDGE_KINDS`` union these in the same way ``CONSUMER_NODE_KINDS``/
#: ``USE_CASE_NODE_KINDS`` above are, and ``abicheck.buildsource.
#: template_graph`` (which populates them) re-exports them. Same two reasons
#: for living in this leaf module: ``source_graph.py`` is at its 2000-line
#: hard cap, and the producer would need to import ``source_graph`` back.
#:
#: Closes the "public template → concrete instantiation → internal
#: specialization → emitted exported symbol" chain: a template's own
#: declaration is often internal-type-free, but a specific instantiation can
#: both depend on an internal type through its arguments and emit a real,
#: linkable symbol — neither of which the pre-existing ``type_graph``/
#: ``call_graph`` passes capture (they only see the template *pattern*).
#:
#: ``DECL_INSTANTIATES_TEMPLATE``, ``TEMPLATE_USES_TYPE``,
#: ``INSTANTIATION_EMITS_SYMBOL``, and (a G29 Phase 5 item 1 follow-up)
#: ``TEMPLATE_USES_DECL`` are populated — see ``template_graph.py``'s own
#: module docstring for the full, empirically-grounded reasoning
#: (``TEMPLATE_USES_DECL`` is scoped to free-function/namespace-scope-
#: variable NTTP targets only; a class-member target is a known, left-open
#: gap). The rest are **reserved**, same "registered so a hand-built or
#: newer graph naming one is never rejected, but no normalized data source
#: yet" pattern as ``CONSUMER_INSTANTIATES_DECL``/``TRACE_OBSERVED_ENTRY``
#: above: ``INSTANTIATION_MAPS_TO_EXPORT`` is redundant with reading
#: ``BINARY_EXPORTS_SYMBOL`` off the same joined ``binary_symbol`` node (the
#: identical ADR-057 D1 reasoning), ``DECL_USES_DEFAULT_TEMPLATE_ARG`` needs
#: to distinguish an explicit argument from a clang-filled default, and
#: ``CONSTRAINT_DEPENDS_ON_DECL`` (C++20 concepts) is a separate AST
#: subsystem needing its own empirical pass.
TEMPLATE_NODE_KINDS: frozenset[str] = frozenset(
    {"template_decl", "template_instantiation"}
)
TEMPLATE_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "DECL_INSTANTIATES_TEMPLATE",
        "TEMPLATE_USES_DECL",
        "TEMPLATE_USES_TYPE",
        "INSTANTIATION_EMITS_SYMBOL",
        "INSTANTIATION_MAPS_TO_EXPORT",
        "DECL_USES_DEFAULT_TEMPLATE_ARG",
        "CONSTRAINT_DEPENDS_ON_DECL",
    }
)

#: The macro/config-dependency half of the graph vocabulary (G29 Phase 5 item
#: 2, G29.6's second open graph family) — ``source_graph.EDGE_KINDS`` unions
#: this in the same way ``TEMPLATE_EDGE_KINDS``/``LINK_PROVENANCE_EDGE_KINDS``
#: above are, and ``abicheck.buildsource.macro_graph`` (which populates it)
#: re-exports it. Same two reasons for living in this leaf module:
#: ``source_graph.py`` is at its 2000-line hard cap, and the producer would
#: need to import ``source_graph`` back. **No new node kind** — every edge
#: below joins onto the existing ``macro``/``source_decl`` node kinds
#: ``source_graph.NODE_KINDS`` already declares (same "no new node kind
#: needed" shape as ``CONSUMER_EDGE_KINDS``'s deliberate absence of a
#: ``consumer_required_symbol`` node kind above).
#:
#: Only ``MACRO_CONTROLS_DECL`` (a declaration compiled only under a simple
#: ``#ifdef``/``#ifndef``/``#if defined``/``#if !defined`` guard) and
#: ``DECL_USES_MACRO`` (a declaration's own text references a macro name
#: defined earlier in the same file — a textual heuristic, not semantic
#: preprocessing) are populated this slice — see ``macro_graph.py``'s own
#: module docstring for the full reasoning, including a load-bearing
#: empirical clang AST-dump finding. The rest are **reserved**, same
#: "registered so a hand-built or newer graph naming one is never rejected,
#: but no normalized data source yet" pattern as ``TEMPLATE_USES_DECL``/
#: ``CONSTRAINT_DEPENDS_ON_DECL`` above: ``MACRO_EXPANDS_TO_VALUE``/
#: ``MACRO_EXPANDS_TO_TYPE`` need real macro-expansion tracing (a full
#: preprocessor substitution model), and ``MACRO_CONTROLS_EDGE`` needs
#: per-edge (not per-declaration) conditional attribution — knowing that one
#: specific call/reference *inside* a declaration's body is itself nested
#: under a *different* macro guard than the declaration's own, a finer-
#: grained walk than this slice's whole-declaration region join.
MACRO_DEP_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "MACRO_CONTROLS_DECL",
        "DECL_USES_MACRO",
        "MACRO_EXPANDS_TO_VALUE",
        "MACRO_EXPANDS_TO_TYPE",
        "MACRO_CONTROLS_EDGE",
    }
)

#: The virtual-dispatch half of the graph vocabulary (G29 Phase 5 item 3,
#: G29.6's third open graph family) — ``source_graph.NODE_KINDS``/
#: ``EDGE_KINDS`` union these in the same way ``TEMPLATE_NODE_KINDS``/
#: ``MACRO_DEP_EDGE_KINDS`` above are, and ``abicheck.buildsource.
#: virtual_dispatch_graph`` (which populates the two live members below)
#: re-exports them. Same two reasons for living in this leaf module:
#: ``source_graph.py`` is at its 2000-line hard cap, and the producer would
#: need to import ``source_graph`` back.
#:
#: ``DECL_OVERRIDES_DECL`` is registered but **deliberately has no producer**:
#: it is not a gap, it is a closed one. ``override_graph.py`` (ADR-041 P2
#: item 1, which predates this family) already emits
#: ``METHOD_POSSIBLE_OVERRIDE`` edges whose ``attrs["resolution"]`` is either
#: ``"override_confirmed"`` (clang's own ``OverrideAttr`` — the ``override``
#: keyword was written and the compiler checked the override relationship
#: against the base's virtual slot, name+signature match included) or the
#: weaker ``"override_signature_match"`` (no compiler confirmation, a
#: name+type match only). A confirmed edge already carries the exact fact
#: ``DECL_OVERRIDES_DECL`` would: "this declaration overrides that one,
#: checked by the compiler." Minting a second, redundant edge kind for the
#: same fact would fork one piece of evidence into two, with no consumer able
#: to tell which one is authoritative when they inevitably drift on
#: confidence or scope — exactly the duplication ADR-046 D2's evidence-merge
#: machinery exists to prevent, not reproduce. A reader who wants "decl X
#: overrides decl Y, confirmed" reads a ``METHOD_POSSIBLE_OVERRIDE`` edge with
#: ``resolution == "override_confirmed"``; ``DECL_OVERRIDES_DECL`` stays
#: registered only so a hand-built or future graph naming it directly (e.g. an
#: external backend, Phase 7) is never rejected as unknown vocabulary — same
#: "registered vocabulary, no producer" pattern the reserved kinds below use,
#: just for a different reason (satisfied by an existing kind, not deferred
#: for missing evidence).
#:
#: ``VIRTUAL_CALL_MAY_DISPATCH_TO`` and ``TYPE_HAS_VTABLE`` are populated this
#: slice — see ``virtual_dispatch_graph.py``'s own module docstring for the
#: full reasoning. ``VIRTUAL_CALL_MAY_DISPATCH_TO`` is explicitly
#: ``resolution: "overapprox"``, never ``"exact"`` (mirroring
#: ``call_graph.RESOLUTION_OVERAPPROX``): it names the possible runtime
#: dispatch *target set* a virtual call may reach, not a proof of which
#: target a given call actually takes.
#:
#: ``VTABLE_SLOT_MAPS_TO_DECL`` remains **reserved, unpopulated** vocabulary:
#: a precise per-slot Itanium vtable layout (offset-to-top and typeinfo
#: pointer slots, primary vs. secondary vtables under multiple inheritance,
#: virtual-inheritance vtables, covariant-return thunks shifting a slot's
#: target) is a much harder, easy-to-get-subtly-wrong claim than "this class
#: has a vtable" or "this call's target set may include these declarations" —
#: exactly the distinction this family's own design brief draws between
#: "the vtable slot provably changed" and "the possible dispatch target set
#: changed." ``diff_elf_layout.py``'s existing binary-only vtable-slot-*count*
#: detector (a completely different, complementary evidence source — approximates
#: a slot count from an ELF ``_ZTV<mangled-type>`` symbol's size, with no
#: per-slot identity at all) documents the same real-world layout complexity
#: in its own module docstring. A naive "declaration order" per-slot model
#: would get exactly those cases wrong, not merely approximately right, and
#: this codebase's discipline is to degrade to no fact rather than emit a
#: wrong one (ADR-028 D3) — so this edge waits for a real, verified Itanium
#: layout model, not a drive-by guess.
VIRTUAL_DISPATCH_NODE_KINDS: frozenset[str] = frozenset({"vtable"})
VIRTUAL_DISPATCH_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "DECL_OVERRIDES_DECL",
        "VIRTUAL_CALL_MAY_DISPATCH_TO",
        "VTABLE_SLOT_MAPS_TO_DECL",
        "TYPE_HAS_VTABLE",
    }
)

#: Object/link provenance (ADR-041 P1 #2) — ``source_graph.NODE_KINDS``/
#: ``EDGE_KINDS`` union these in the same way the vocabulary above is;
#: relocated here from inline additions in ``source_graph.py`` itself once
#: that file reached its 2000-line hard cap (this leaf module has plenty of
#: room). A symbol change attributed to "which object/archive member/link
#: step", not only "which target". ``object_file``/``static_library``/
#: ``version_script`` and ``TARGET_HAS_LINK_UNIT``/``COMPILE_UNIT_EMITS_OBJECT``/
#: ``LINK_UNIT_HAS_INPUT``/``LINK_UNIT_USES_VERSION_SCRIPT``/
#: ``LINK_UNIT_EXPORTS_SYMBOL`` are populated from
#: ``BuildEvidence.compile_units``/``link_units`` (``source_graph.
#: _fold_link_provenance``); ``archive_member``/``ARCHIVE_CONTAINS_OBJECT``/
#: ``OBJECT_DEFINES_SYMBOL`` are populated by ``archive_graph.
#: augment_graph_with_archives`` (G29 Phase 5 item 6, via
#: ``inline_graph_fold.fold_archive_graph``) — a real ``ar``-index
#: introspection pass, not build-evidence alone. ``linker_script``/
#: ``export_map``/``comdat_group`` remain reserved (no normalized data
#: source yet) for a future linker-artifact extractor.
#: The callback/function-pointer half of the graph vocabulary (G29 Phase 5
#: item 4, G29.6's fourth open graph family) — ``source_graph.EDGE_KINDS``
#: unions this in the same way ``MACRO_DEP_EDGE_KINDS``/
#: ``VIRTUAL_DISPATCH_EDGE_KINDS`` above are, and ``abicheck.buildsource.
#: callback_graph`` (which populates the three live members below)
#: re-exports it. Same two reasons for living in this leaf module:
#: ``source_graph.py`` is at its 2000-line hard cap, and the producer would
#: need to import ``source_graph`` back. **No new node kind**: a callback
#: slot and the function whose address flows into it are both already
#: ``source_decl`` nodes seeded by ``call_graph.py``/``type_graph.py`` — same
#: "no new node kind needed" shape as ``MACRO_DEP_EDGE_KINDS``'s deliberate
#: absence of one.
#:
#: ``CALLBACK_MAY_INVOKE`` (a pure join, no new clang pass — reuses
#: ``call_graph.py``'s existing function-pointer-kind ``DECL_CALLS_DECL``
#: edges), ``DECL_REGISTERS_CALLBACK`` (a function's address taken as a
#: direct argument to a call whose matching parameter is function-pointer
#: typed — the plugin/event-loop/C-API registration shape), and
#: ``DECL_TAKES_ADDRESS_OF`` (the broader case: an address-of flowing into a
#: function-pointer-typed variable/field via assignment or initializer, not
#: necessarily a direct call argument) are all populated — see
#: ``callback_graph.py``'s own module docstring for the full reasoning,
#: including the identity design Part A's join depends on and two empirical
#: AST-shape findings (an explicit ``&func``/an implicit function-to-pointer
#: decay, and a documented, inherited join gap for a struct-field-typed slot
#: invoked through member-call syntax).
#:
#: ``FUNCTION_POINTER_HAS_SIGNATURE`` is registered but has **no edge
#: producer** — investigated and found genuinely unmet by any pre-existing
#: edge (unlike ``DECL_OVERRIDES_DECL`` above, which was already covered), but
#: a function pointer's signature is a property of exactly one declaration,
#: not a relation between two entities, so it doesn't fit this schema's edge
#: shape at all. ``callback_graph.py`` instead populates the real data as a
#: ``function_pointer_signature`` **node-level** fact (via
#: :func:`register_fact`) on the callback slot's own ``source_decl`` node.
#: Stays registered as edge vocabulary only so a hand-built or future graph
#: naming it directly is never rejected.
CALLBACK_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "DECL_TAKES_ADDRESS_OF",
        "DECL_REGISTERS_CALLBACK",
        "CALLBACK_MAY_INVOKE",
        "FUNCTION_POINTER_HAS_SIGNATURE",
    }
)

LINK_PROVENANCE_NODE_KINDS: frozenset[str] = frozenset(
    {
        "object_file",
        "archive_member",
        "static_library",
        "linker_script",
        "version_script",
        "export_map",
        "comdat_group",
    }
)
LINK_PROVENANCE_EDGE_KINDS: frozenset[str] = frozenset(
    {
        "TARGET_HAS_LINK_UNIT",
        "COMPILE_UNIT_EMITS_OBJECT",
        "LINK_UNIT_HAS_INPUT",
        "LINK_UNIT_USES_VERSION_SCRIPT",
        "LINK_UNIT_EXPORTS_SYMBOL",
        "ARCHIVE_CONTAINS_OBJECT",
        "OBJECT_DEFINES_SYMBOL",
    }
)


def _precedence_key(fact: GraphFact) -> tuple[int, str, str]:
    """Deterministic total order over facts: highest confidence first, tie
    broken by producer name, and a further tie (the same producer
    contributing two facts at equal confidence with different attrs) by a
    JSON-content sort — so arrival/registration order never decides a
    winner, satisfying ``merge_graph_facts``'s order-independence property.
    """
    return (
        -_CONFIDENCE_RANK.get(fact.confidence, 0),
        fact.producer,
        json.dumps(fact.attrs, sort_keys=True, default=str),
    )


@dataclass
class GraphFact:
    """One producer's contribution to a node/edge's ``attrs`` (ADR-046 D2).

    A node/edge accumulates one ``GraphFact`` per producer that ever
    registered it, instead of the v1 first-writer-wins behavior silently
    dropping every registration after the first.
    """

    producer: str
    confidence: str = CONF_UNKNOWN
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer": self.producer,
            "confidence": self.confidence,
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphFact:
        return cls(
            producer=str(d.get("producer", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            attrs=dict(d.get("attrs", {})),
        )


@dataclass
class FactConflict:
    """A genuine attrs disagreement between two facts at equal precedence
    (ADR-046 D2) — e.g. ``is_virtual: true`` vs. ``is_virtual: false`` from
    two producers of the same confidence. Advisory only (never authoritative
    on its own, ADR-028 D3): recorded so the disagreement is visible instead
    of one value silently winning with no trace of the other.
    """

    key: str
    winning_value: Any
    winning_producer: str
    losing_value: Any
    losing_producer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "winning_value": self.winning_value,
            "winning_producer": self.winning_producer,
            "losing_value": self.losing_value,
            "losing_producer": self.losing_producer,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FactConflict:
        return cls(
            key=str(d.get("key", "")),
            winning_value=d.get("winning_value"),
            winning_producer=str(d.get("winning_producer", "")),
            losing_value=d.get("losing_value"),
            losing_producer=str(d.get("losing_producer", "")),
        )


def merge_graph_facts(
    facts: list[GraphFact],
) -> tuple[dict[str, Any], list[FactConflict]]:
    """Fold ``facts`` into one ``resolved`` attrs dict (ADR-046 D2).

    Order-independent: the result depends only on each fact's confidence,
    producer name, and content, never on registration order, so the same set
    of facts always resolves identically regardless of which producer ran
    first (the property PR #607's review repeatedly needed and had to
    hand-verify per call site). Per key, the highest-confidence fact wins; a
    tie is broken by a stable producer-name sort, and a further tie (the same
    producer contributing two facts at equal confidence with different attrs
    — e.g. an initial registration and a later backfill) by a deterministic
    JSON-content sort so arrival order still never decides the winner. A
    genuine value disagreement between two facts that both contribute a key
    is recorded as a :class:`FactConflict`, not silently dropped.
    """
    ordered = sorted(facts, key=_precedence_key)
    resolved: dict[str, Any] = {}
    winners: dict[str, GraphFact] = {}
    conflicts: list[FactConflict] = []
    for fact in ordered:
        for k, v in fact.attrs.items():
            if k not in resolved:
                resolved[k] = v
                winners[k] = fact
            elif resolved[k] != v:
                conflicts.append(
                    FactConflict(
                        key=k,
                        winning_value=resolved[k],
                        winning_producer=winners[k].producer,
                        losing_value=v,
                        losing_producer=fact.producer,
                    )
                )
    return resolved, conflicts


@dataclass
class GraphNode:
    """A single ABI/API-relevant graph node (ADR-031 D2).

    ``facts``/``resolved``/``conflicts``: the ADR-046 D2 evidence-preserving
    merge. ``attrs``/``provenance``/``confidence`` stay real fields (v1
    read-compat), (re)populated from the merged facts, not frozen at
    first registration.
    """

    id: str
    kind: str  # one of source_graph.NODE_KINDS (preserved even if unknown)
    label: str = ""  # human-readable name/path (redacted upstream)
    attrs: dict[str, Any] = field(default_factory=dict)
    provenance: str = ""  # how this node was derived, e.g. "build_evidence"
    confidence: str = CONF_UNKNOWN
    facts: list[GraphFact] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)
    conflicts: list[FactConflict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "attrs": dict(self.attrs),
            "provenance": self.provenance,
            "confidence": self.confidence,
            "facts": [f.to_dict() for f in self.facts],
            "resolved": dict(self.resolved),
            "conflicts": [c.to_dict() for c in self.conflicts],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphNode:
        # v1 pack has no "facts" key; ensure_facts_and_resolve synthesizes it
        # from attrs/provenance/confidence (no forced re-collection). A stored
        # "resolved"/"conflicts" is never trusted — always recomputed, so a
        # hand-edited pack self-heals instead of persisting a stale merge.
        #
        # id passes through _normalize_graph_identity on load (Codex review,
        # fresh evidence) so a pack persisted before that normalization
        # existed self-heals the same way: without this, an old pack's raw,
        # checkout-path-bearing decl/type node id would never match the
        # normalized id a freshly-built graph now produces for the identical
        # declaration, and diff_source_graph's direct id comparison would
        # read it as removed+added rather than unchanged (the exact false
        # positive this whole normalization exists to close, just reached
        # from a stale-pack angle instead of a fresh-vs-fresh one). Safe
        # unconditionally: the substitution only ever touches an embedded
        # "(unnamed .../lambda at ...)"/"lambda at ...:line:col" marker, so
        # it is a no-op for every other node id (source://, header://,
        # build_option://, symbol://, target://, vtable://, ...) and
        # idempotent for an id a current build already normalized. ``label``
        # needs no explicit normalization here — ensure_facts_and_resolve
        # below (called unconditionally for every loaded node) already
        # covers it, the same single choke point a freshly-added node's
        # label goes through via SourceGraphSummary.add_node.
        node = cls(
            id=_normalize_graph_identity(str(d["id"])),
            kind=str(d.get("kind", "file")),
            label=str(d.get("label", "")),
            attrs=dict(d.get("attrs", {})),
            provenance=str(d.get("provenance", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            facts=[GraphFact.from_dict(f) for f in d.get("facts", [])],
        )
        ensure_facts_and_resolve(node)
        return node


@dataclass
class GraphEdge:
    """A directed edge between two nodes, with provenance + confidence (D2, D9).

    ``attrs`` carries edge-kind-specific labels — most importantly the
    ``call_kind``/``resolution`` pair for ``DECL_CALLS_DECL`` edges (ADR-031
    D4). ``facts``/``resolved``/``conflicts`` are the ADR-046 D2
    evidence-preserving merge — see :class:`GraphNode`. ``occurrences`` is
    D1's second half: the deduplicated set of per-call-site
    :func:`edge_occurrence_id` values contributed by this edge's facts (empty
    when no fact carries occurrence-level attrs — the common case today).
    """

    src: str
    dst: str
    kind: str  # one of source_graph.EDGE_KINDS (preserved even if unknown)
    provenance: str = ""
    confidence: str = CONF_UNKNOWN
    attrs: dict[str, Any] = field(default_factory=dict)
    facts: list[GraphFact] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)
    conflicts: list[FactConflict] = field(default_factory=list)
    occurrences: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str, str]:
        """Identity for diffing/de-dup: (src, dst, kind) — ADR-046 D1's
        coarsest (role-blind) projection. Still used by
        :func:`~abicheck.buildsource.source_graph.diff_source_graph`'s
        edge-set comparison (deliberately role-blind there); no longer used
        by ``SourceGraphSummary.add_edge``, which dedups on
        :meth:`relation_key` instead (a follow-up fix — see that method's
        docstring). Role-aware code should use :meth:`relation_key`.
        """
        return (self.src, self.dst, self.kind)

    def relation_key(self) -> tuple[str, str, str, str]:
        """Role-aware identity (ADR-046 D1) — see :func:`edge_relation_key`.
        Falls back to raw ``attrs`` pre-registration, when ``resolved`` is
        still empty.
        """
        return edge_relation_key(
            self.src, self.dst, self.kind, self.resolved or self.attrs
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge": self.kind,
            "src": self.src,
            "dst": self.dst,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "attrs": dict(self.attrs),
            "facts": [f.to_dict() for f in self.facts],
            "resolved": dict(self.resolved),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "occurrences": list(self.occurrences),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GraphEdge:
        # See GraphNode.from_dict: v1-pack compat + always-recompute-from-facts
        # apply identically here, including the src/dst normalize-on-load
        # migration for a pack persisted before decl/type node ids were
        # checkout-directory-normalized.
        edge = cls(
            src=_normalize_graph_identity(str(d["src"])),
            dst=_normalize_graph_identity(str(d["dst"])),
            kind=str(d.get("edge", d.get("kind", ""))),
            provenance=str(d.get("provenance", "")),
            confidence=str(d.get("confidence", CONF_UNKNOWN)),
            attrs=dict(d.get("attrs", {})),
            facts=[GraphFact.from_dict(f) for f in d.get("facts", [])],
        )
        ensure_facts_and_resolve(edge)
        return edge


def ensure_facts_and_resolve(entity: GraphNode | GraphEdge) -> None:
    """Ensure ``entity.facts`` is non-empty and (re)derive ``resolved``/
    ``conflicts``/``attrs``/``confidence``/``provenance`` from it.

    Synthesizes a single fact from ``attrs``/``provenance``/``confidence``
    when ``facts`` is empty — the common case for a bare ``GraphNode(...)``/
    ``GraphEdge(...)`` construction that bypasses
    ``SourceGraphSummary.add_node``/``add_edge`` (a v1-shaped call site, a
    loaded v1 pack with no ``facts`` key, or constructor-seeded test/builder
    code). Always recomputes ``resolved``/``conflicts`` from the (possibly
    just-synthesized) fact list via :func:`merge_graph_facts`, and
    ``confidence``/``provenance`` from the top-precedence fact — so a
    hand-edited or stale stored ``resolved`` value in a loaded pack never
    silently persists; it self-heals to what the facts actually support.

    Also normalizes a :class:`GraphNode`'s own ``label`` (Codex review, fresh
    evidence): this is the one choke point every decl/type producer already
    routes a fresh node through, on first registration, via
    ``SourceGraphSummary.add_node`` — the merge path for an
    already-registered node's *second* registration never touches ``label``
    at all ("kind/label keep the first registration's value" — see
    :meth:`SourceGraphSummary.add_node`'s own docstring), so normalizing
    here covers every producer's label the same way ``source_graph.
    _decl_node_id``/``_type_node_id`` already cover every producer's node
    id, rather than needing an explicit ``_normalize_graph_identity(...)``
    call duplicated at each producer's own node-construction site (a prior
    revision of this fix normalized only the two call sites that happened to
    build ``GraphNode``s directly in ``source_graph.py``/``type_graph.py``,
    silently leaving every other producer's label unnormalized). Safe
    unconditionally, same reasoning as :func:`_normalize_graph_identity`
    itself: the substitution only ever touches an embedded anonymous/lambda
    location marker, so it is a no-op for any other label.
    """
    if not entity.facts:
        entity.facts = [
            GraphFact(
                producer=entity.provenance,
                confidence=entity.confidence,
                attrs=dict(entity.attrs),
            )
        ]
    entity.resolved, entity.conflicts = merge_graph_facts(entity.facts)
    entity.attrs = dict(entity.resolved)
    top = min(entity.facts, key=_precedence_key)
    entity.confidence = top.confidence
    entity.provenance = top.producer
    if isinstance(entity, GraphNode):
        entity.label = _normalize_graph_identity(entity.label)
    if isinstance(entity, GraphEdge):
        entity.occurrences = _compute_occurrences(entity)


def register_fact(
    entity: GraphNode | GraphEdge,
    provenance: str,
    confidence: str,
    attrs: dict[str, Any],
) -> None:
    """Merge one more producer's fact into an already-registered node/edge.

    The evidence-preserving counterpart of the v1 first-writer-wins drop: a
    duplicate ``(producer, confidence, attrs)`` registration is a no-op
    (idempotent re-registration), a genuinely new fact is appended, and
    ``resolved``/``conflicts``/``confidence``/``provenance`` are recomputed
    over the full accumulated fact set.
    """
    new_fact = GraphFact(producer=provenance, confidence=confidence, attrs=dict(attrs))
    if new_fact not in entity.facts:
        entity.facts.append(new_fact)
    ensure_facts_and_resolve(entity)


def merge_entity_facts(
    existing: GraphNode | GraphEdge, incoming: GraphNode | GraphEdge
) -> None:
    """Merge every fact from an already-registered *incoming* node/edge into
    *existing* (Codex review, fresh evidence).

    ``SourceGraphSummary.add_node``/``add_edge``'s duplicate-registration
    branch used to call :func:`register_fact` with just *incoming*'s own
    top-level ``provenance``/``confidence``/``attrs`` — correct for the
    common case where *incoming* is a bare, single-producer
    ``GraphNode(...)``/``GraphEdge(...)`` construction, but wrong for an
    *incoming* that already carries multiple facts of its own (e.g. a node
    re-added from an already evidence-merged graph): only one flattened fact
    got appended, silently discarding the individual per-producer facts (and
    any ``conflicts`` already recorded) *incoming* carried. Resolves
    *incoming* first (so a *incoming* whose evidence still lives only in
    ``facts``, not yet mirrored into ``attrs``, is not missed either — same
    fix as the ``add_edge`` resolve-before-index bug), then merges its full
    ``facts`` list into *existing*, one fact at a time (duplicates are a
    no-op, matching :func:`register_fact`'s own idempotence).
    """
    ensure_facts_and_resolve(incoming)
    for fact in incoming.facts:
        if fact not in existing.facts:
            existing.facts.append(fact)
    ensure_facts_and_resolve(existing)


def edge_relation_key(
    src: str, dst: str, kind: str, resolved: dict[str, Any]
) -> tuple[str, str, str, str]:
    """ADR-046 D1 role-aware edge identity: (src, dst, kind, role).

    Adds ``resolved.get("role", "")`` (D2's merged view, not raw ``attrs``)
    as a fourth discriminator to the coarse ``(src, dst, kind)`` key
    (``GraphEdge.key()``), so two structurally different dependencies that
    happen to share that triple — e.g. a type used as a ``"return"`` type on
    one edge and as a ``"param"`` type on another, both ``DECL_HAS_TYPE`` —
    stay distinguishable to code that needs that distinction.
    ``SourceGraphSummary.add_edge`` dedups on this role-aware key (a
    follow-up fix, Codex review on PR #620 — deduping on the coarse
    ``key()`` alone silently folded two real, role-distinct edges into one).
    ``diff_source_graph``'s edge-set comparison deliberately keeps using the
    coarser ``key()`` — role-level diff granularity is out of scope for this
    ADR's D1 slice.

    D1's second half — the full, non-deduplicated per-call-site/
    per-configuration evidence trail a ``relation_key`` can back many of —
    is :func:`edge_occurrence_id`/:class:`GraphEdge`'s ``occurrences`` field,
    kept deliberately opt-in (a no-op unless a producer supplies
    occurrence-level attrs) so it never lands on a default, always-on path
    without the pack-size cost-model check ADR-046's Costs section calls for.
    """
    return (src, dst, kind, str(resolved.get("role", "")))


#: The four ADR-046 D1 occurrence-level attrs an edge fact may carry, beyond
#: its ``relation_key``, to pin down exactly *which* call site/configuration/
#: template instantiation it came from. No current producer populates any of
#: these — :func:`edge_occurrence_id` returns ``None`` when none is present,
#: so occurrence tracking costs nothing until a producer opts in.
OCCURRENCE_ATTR_KEYS = (
    "source_location",
    "configuration_id",
    "instantiation_id",
    "callsite_id",
)


def edge_occurrence_id(
    relation_key: tuple[str, str, str, str], attrs: dict[str, Any]
) -> str | None:
    """ADR-046 D1's per-call-site occurrence identity: a stable
    ``sha256:<hex>`` over ``(relation_key, source_location, configuration_id,
    instantiation_id, callsite_id)``, read from *attrs*.

    Two facts that share a ``relation_key`` (and so collapse onto one
    :class:`GraphEdge`) but come from different call sites, ``#ifdef``
    configurations, or template instantiations get distinct occurrence ids —
    preserving the full evidence trail a ``relation_key``-deduped edge would
    otherwise discard.

    Returns ``None`` when *attrs* carries none of :data:`OCCURRENCE_ATTR_KEYS`
    — the common case today, since no producer populates them yet — so a
    fact with no occurrence-level data contributes nothing to
    :class:`GraphEdge`'s ``occurrences`` list rather than a spurious
    all-``None`` id.
    """
    if not any(k in attrs for k in OCCURRENCE_ATTR_KEYS):
        return None
    blob = json.dumps(
        {
            "relation_key": list(relation_key),
            **{k: attrs.get(k) for k in OCCURRENCE_ATTR_KEYS},
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _compute_occurrences(edge: GraphEdge) -> list[str]:
    """Recompute :attr:`GraphEdge.occurrences` from *edge*'s current facts.

    Called by :func:`ensure_facts_and_resolve` alongside ``resolved``/
    ``conflicts`` — always derived fresh from ``facts``, never trusted from a
    loaded pack, matching that function's self-healing convention.
    """
    rk = edge.relation_key()
    seen: list[str] = []
    for fact in edge.facts:
        oid = edge_occurrence_id(rk, fact.attrs)
        if oid is not None and oid not in seen:
            seen.append(oid)
    return sorted(seen)


# ── decl/type identity normalization (ADR-031/ADR-048) ─────────────────────
#
# Split out here (rather than kept in source_graph.py, where the choke-point
# functions that consume this live) purely to stay under source_graph.py's
# AI-readiness line-count cap -- see this module's own docstring for the
# established precedent of moving content here for exactly that reason.

#: Same location-shaped text :func:`strip_anonymous_type_location` strips,
#: but *without* the leading ``(`` that function anchors on. clang's own
#: declaration-name spelling for a lambda closure's implicit record (as
#: opposed to the *type* printer's ``"(lambda at ...)"`` qualType spelling
#: `strip_anonymous_type_location` targets) has been observed reaching the
#: L5 source-graph pipeline bare -- e.g. a ``SourceEntity.identity()``/
#: ``qualified_name`` of exactly ``"lambda at /a/foo.hpp:4:37"``, no
#: wrapping parens at all -- so a real fix here needs both shapes covered,
#: not just the parenthesized one `strip_anonymous_type_location` already
#: handles. Anchored the same way (``\b`` word boundary before the marker)
#: to avoid rewriting unrelated text that merely contains the substring
#: "at" followed by something colon-shaped.
#:
#: The path group is a negative-lookahead-guarded ``.*`` -- greedy, but
#: refusing to consume across a *later* marker's own ``lambda at``/``unnamed
#: <kind> at`` trigger -- rather than plain non-greedy ``.*?`` (Codex review,
#: fresh evidence): a non-greedy path group stops at the FIRST ``:\d+:\d+``
#: it can find, which is wrong the moment the checkout path itself contains
#: a colon-digit-colon-digit-shaped segment (a timestamped build directory,
#: ``/tmp/build-2026T12:34:56/src/foo.hpp:4:37``) -- it silently keeps the
#: real, checkout-dependent tail (``/src/foo.hpp:4:37``) unmodified past the
#: truncated match. A plain greedy ``.*`` fixes that (it finds the
#: *rightmost* valid ``:\d+:\d+``) but then over-matches a string embedding
#: *two* markers (a quoted lookalike alongside a real one, or two real
#: markers), swallowing both into one match. The lookahead guard gets both
#: right: greedy within one marker's own text, but bounded at the next
#: marker's trigger.
_BARE_ANON_TYPE_LOCATION_RE = re.compile(
    r"\b(lambda|unnamed\s+\w+)\s+at\s+"
    r"((?:(?!(?:lambda|unnamed\s+\w+)\s+at\s).)*):(\d+):(\d+)\b"
)


def _strip_bare_anonymous_type_location(name: str) -> str:
    """Strip the checkout-dependent directory out of a *bare* (unparenthesized)
    ``lambda at <path>:<line>:<col>``/``unnamed <kind> at <path>:<line>:<col>``
    spelling, mirroring :func:`strip_anonymous_type_location`'s contract
    (keep the declaring header's own basename + ``:<line>:<col>`` as a
    discriminator, drop only the checkout-dependent directory) for the shape
    that function's own paren-anchored regex does not match. See
    :func:`_normalize_graph_identity`.

    A match that falls inside a ``"..."`` quoted literal is left completely
    untouched, mirroring `strip_anonymous_type_location`'s own protection
    (Codex review, fresh evidence): a real anonymous/lambda marker is never
    itself quoted, so a match starting inside quotes can only be ordinary
    string-literal *content* that happens to spell location-shaped text --
    e.g. a C++20 fixed-string NTTP argument like ``Tag<"lambda at
    /a/foo.hpp:1:2">``. Without this guard, two distinct specializations
    quoting *different* paths (``Tag<"lambda at /a/foo.hpp:1:2">`` vs.
    ``Tag<"lambda at /b/foo.hpp:1:2">``) would collapse onto the identical
    normalized identity, fabricating a same-identity collision between two
    genuinely different declarations.
    """
    quoted_spans = _quoted_spans(name)

    def _inside_quotes(pos: int) -> bool:
        return any(start <= pos < end for start, end in quoted_spans)

    def _replace(match: re.Match[str]) -> str:
        if _inside_quotes(match.start()):
            return match.group(0)
        marker, path, line, col = match.groups()
        return f"{marker}:{_declaring_header_discriminator(path)}:{line}:{col}"

    return _BARE_ANON_TYPE_LOCATION_RE.sub(_replace, name)


def _normalize_graph_identity(identity: str) -> str:
    """Strip a checkout-dependent directory out of *identity* before it
    becomes (part of) an L5 decl/type node id (ADR-031/ADR-048).

    A ``SourceEntity``'s ``identity()``/``qualified_name`` falls back to the
    raw declaration spelling clang/castxml emit for an anonymous-tag or
    lambda-closure type -- ``"(unnamed struct at /a/foo.h:56:5)"``,
    ``"raii_guard<(lambda at /a/foo.h:4:37)>"``, or (observed directly in a
    real L5 graph, with no wrapping parens at all) a bare ``"lambda at
    /a/foo.h:4:37"`` -- which embeds an *absolute* path. ``dumper_castxml.py``'s
    L2 header-AST backend already strips the parenthesized form at
    extraction time (see :func:`strip_anonymous_type_location`'s own
    docstring), but nothing under ``abicheck/buildsource/`` did: two builds
    of the identical, unedited declaration under different checkout roots
    produced two different L5 node ids for it, which
    ``graph_reconcile``/``diff_source_graph`` then read as a real rename
    (``declaration_renamed``) purely from directory taint. ``source_graph.
    _decl_node_id``/``_type_node_id`` are the one choke point every producer
    in ``abicheck/buildsource/`` (``type_graph.py``, ``call_graph.py``,
    ``override_graph.py``, ``callback_graph.py``, ``template_graph.py``,
    ``header_graph.py``, ``macro_graph.py``, ``source_graph.py`` itself)
    routes a decl/type identity through -- for both a node's own id and
    every edge endpoint naming it -- so normalizing here closes the whole
    class at once rather than one producer at a time. Both the
    parenthesized (L2-style) and bare shapes are stripped, since only
    real-world evidence -- not either producer's own documented output
    contract -- tells us which one a given decl/type identity actually
    carries by the time it reaches this package.
    """
    return _strip_bare_anonymous_type_location(strip_anonymous_type_location(identity))


def _decl_node_id(identity: str) -> str:
    return f"decl://{_normalize_graph_identity(identity)}"


def _type_node_id(identity: str) -> str:
    return f"type://{_normalize_graph_identity(identity)}"
