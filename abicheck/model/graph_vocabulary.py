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

"""L5 source-graph confidence labels and node/edge-kind vocabulary
(ADR-031 D2/D9).

Split out of ``graph_facts.py`` (moved to `abicheck/model/` as part of
ADR-061 Phase 5 item 2's follow-up, keeping this leaf-vocabulary half
separate to stay under the new-file 800-line production cap) — the family
vocabulary sets below (``CONSUMER_*``, ``USE_CASE_*``, ``TEMPLATE_*``,
``MACRO_DEP_*``, ``VIRTUAL_DISPATCH_*``, ``CALLBACK_*``,
``LINK_PROVENANCE_*``) union into ``source_graph.NODE_KINDS``/``EDGE_KINDS``;
each family's own producer module re-exports its pair. ``graph_facts.py``
re-exports ``CONF_HIGH``/``CONF_REDUCED``/``CONF_UNKNOWN`` from here for
backward compatibility, and ``buildsource/graph_facts.py`` (the flat
compat facade) re-exports everything transitively.
"""

from __future__ import annotations

#: Confidence labels (ADR-031 D9). Mirrors the evidence-model vocabulary so the
#: coverage report and graph speak the same language. Canonical home of these
#: constants — ``source_graph.py`` re-exports them for backward compatibility.
CONF_HIGH = "high"
CONF_REDUCED = "reduced"
CONF_UNKNOWN = "unknown"

#: Confidence precedence for the merge in ``graph_facts.py`` — higher ranks
#: resolve a per-key disagreement first. Anything not in this mapping (an
#: unrecognized confidence label from a hand-built/future pack) ranks
#: alongside ``CONF_UNKNOWN`` rather than erroring.
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
#: :func:`~abicheck.model.graph_facts.register_fact`) on the callback slot's
#: own ``source_decl`` node. Stays registered as edge vocabulary only so a
#: hand-built or future graph naming it directly is never rejected.
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
