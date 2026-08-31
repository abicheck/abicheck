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

"""``augment_graph_with_templates`` and its node-id helpers -- split out of
``template_graph.py`` (which was sitting exactly at the repository's
absolute 2000-line hard cap, `check_ai_readiness.py`'s file-size ERROR
threshold -- ADR-061 Phase 5 item 2's own writeup names this file as one of
the four remaining internal-caller exceptions precisely because that cap
left no room to fix its last facade import in place).

This is the **fold** half the parent module's own docstring already
describes as architecturally distinct from parsing: ``template_graph.py``
keeps :func:`~abicheck.buildsource.template_graph.parse_clang_ast_templates`
(a pure function over a ``clang -ast-dump=json`` tree, producing
:class:`~abicheck.buildsource.template_graph.TemplateInstantiation` records)
and :class:`~abicheck.buildsource.template_graph_extractor.
ClangTemplateGraphExtractor` (the live-clang wrapper, already its own
sibling module); this module owns the second half --
:func:`augment_graph_with_templates`, which folds those already-parsed
records into a :class:`~abicheck.model.source_graph.SourceGraphSummary` --
mirroring the identical parse-vs-fold split ``type_graph.py``/
``call_graph.py`` each already keep as two functions in one file (small
enough there to not need a physical split). The section boundary matches
exactly what the parent module's own ``# ── graph augmentation ──`` comment
delimited before this split; nothing here was redesigned, only relocated,
including this module's own facade-import fix below.

**Closes ADR-061 Phase 5 item 2's `template_graph.py` exception.** The one
name this whole split exists to unblock -- :func:`augment_graph_with_templates`'s
own ``_decl_node_id``/``_symbol_node_id``/``_type_node_id`` triple, previously
reached through the ``buildsource/source_graph.py`` back-compat facade because
the parent module had no line budget left to expand a one-line facade import
into the two-line direct-owner form -- now imports its real owners directly:
``_decl_node_id``/``_type_node_id`` from :mod:`abicheck.model.graph_identity`
(re-exported by ``model/graph_facts.py``, but imported from its own defining
module here, the same way every other already-closed exception in this ADR
item does) and ``_symbol_node_id`` from :mod:`abicheck.model.source_graph`.
The ``SourceGraphSummary`` type-only import moves the same way, for the same
reason: it was still spelled ``from .source_graph import SourceGraphSummary``
in the parent module (a stale, unmigrated case the ADR's own prose had not
yet caught up to) and is only ever used here now, so it moves to the direct
``abicheck.model.source_graph`` owner rather than staying on the facade.

The remaining ``call_graph._file_in_project`` import is unrelated to this
split -- ``call_graph.py`` is that name's real, non-facade owner already,
same as before the move.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    _normalize_graph_identity,
)
from .template_graph import (
    _FUNCTION_KIND,
    _VALUE_DECL_KINDS,
    TemplateInstantiation,
    _normalize_mangled,
    _type_node_kind,
)

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary

EDGE_DECL_INSTANTIATES_TEMPLATE = "DECL_INSTANTIATES_TEMPLATE"
EDGE_TEMPLATE_USES_TYPE = "TEMPLATE_USES_TYPE"
EDGE_TEMPLATE_USES_DECL = "TEMPLATE_USES_DECL"
EDGE_INSTANTIATION_EMITS_SYMBOL = "INSTANTIATION_EMITS_SYMBOL"

NODE_TEMPLATE_DECL = "template_decl"
NODE_TEMPLATE_INSTANTIATION = "template_instantiation"
#: A resolved TEMPLATE_USES_DECL target's node kind (call_graph.py/
#: type_graph.py's own function/variable declaration node kind).
NODE_SOURCE_DECL = "source_decl"

#: ``provenance`` tag on every node/edge this module creates.
TEMPLATE_GRAPH_PROVENANCE = "template_graph"


def template_decl_node_id(qname: str, signature: str | None = None) -> str:
    """Node id for one template declaration (the abstract pattern).

    A **class** template is keyed by its qname alone in the common case — a
    class template can't be overloaded, so two declarations sharing a qname
    with no *signature* really are the same template (matches how
    ``Holder<int>``'s and ``Holder<double>``'s own class-template pattern is
    correctly shared across every instantiation). It is additionally
    disambiguated by *signature* when known, needed by two distinct callers:
    a **function** template's own pattern *signature* (e.g. ``"T (T, T)"``)
    distinguishes two overloads sharing one name (``f<T>(T)`` vs.
    ``f<T>(T,T)``), which would otherwise collapse their abstract
    declarations onto one shared ``template_decl`` node even after their own
    instantiation nodes were separated by mangled name — a real gap the
    earlier instantiation-id fix didn't close (Codex review, empirically
    confirmed against real clang AST output: both overloads'
    ``DECL_INSTANTIATES_TEMPLATE`` edges still terminated at the identical
    ``template_decl://f`` node). A **class** template's own partial-
    specialization pattern signature (e.g. ``"type-parameter-0-0 *"``,
    see :attr:`~abicheck.buildsource.template_graph.TemplateInstantiation.
    template_signature`) distinguishes an
    instantiation selecting that pattern (``C<int*>``) from one selecting
    the primary (``C<int>``) — otherwise both terminated at the identical
    ``template_decl://C`` node despite instantiating distinct declared
    patterns (Codex review, fresh evidence, confirmed against real clang 18
    AST output)."""
    # Normalized like _decl_node_id/_type_node_id: a signature can embed a checkout marker.
    qname = _normalize_graph_identity(qname)
    if signature:
        return f"template_decl://{qname}#{_normalize_graph_identity(signature)}"
    return f"template_decl://{qname}"


def template_instantiation_node_id(label: str, mangled: str | None = None) -> str:
    """Node id for one concrete instantiation.

    A class instantiation is keyed by its own human label
    (``"Wrapper<internal::Detail>"``) — it has no single symbol of its own
    (it emits one per instantiated member), so the label is the only
    identity available. A **function** instantiation is keyed by its own
    unique mangled name instead, when known (*mangled* set): two distinct
    overloads of the same function template (``f<T>(T)`` vs. ``f<T>(T,T)``)
    both instantiated with ``T=int`` produce the identical *label* (built
    only from template arguments — arity/signature isn't one), so keying by
    label alone collapsed both overloads onto a single node (Codex review,
    empirically confirmed against real clang AST output: only one
    ``DECL_INSTANTIATES_TEMPLATE`` edge survived for two genuinely distinct
    instantiations). The mangled name always differs between overloads, so
    it's the correct, collision-free identity for this kind."""
    if mangled:
        return f"template_instantiation://{mangled}"
    # A class instantiation's own label (built from its args' spellings) can embed the
    # identical checkout-dependent lambda marker for a lambda-typed argument (Codex
    # review, confirmed against real clang output); a mangled name never does.
    return f"template_instantiation://{_normalize_graph_identity(label)}"


def _symbol_node_ids(graph: SourceGraphSummary) -> frozenset[str]:
    return frozenset(n.id for n in graph.nodes if n.kind == "binary_symbol")


def _resolve_emitted_symbol(
    symbol: str,
    known_symbols: frozenset[str],
    symbol_node_id: Callable[[str], str],
) -> str | None:
    """The resolved *spelling* -- ``symbol`` itself, or its
    :func:`~abicheck.buildsource.template_graph._normalize_mangled` form --
    that has a matching ``binary_symbol``
    node, or ``None`` if neither does. Returns the **spelling**, not the
    node id: a caller needing the node id derives it with
    ``symbol_node_id(result)``, and a caller needing a stable identity
    (``function_mangled``) shares this resolution instead of computing it
    twice and risking disagreement (Codex review, second round: an earlier
    revision separately normalized ``function_mangled``, merging two
    distinct instantiations -- each with its own real, independently
    exported asm-labeled symbol, ``__Zfake``/``_Zfake`` -- onto one node).

    Tries *symbol* exactly first; only when unmatched does it retry the
    Mach-O-stripped form. **Narrower than** :mod:`archive_graph`'s own
    fallback (gates on real object-magic evidence); this one falls back
    unconditionally, though still safer than the pre-fix strip.

    **Known residual gap, investigated and deliberately not closed**
    (Codex review, third round, confirmed via a real repro): a hidden/
    discarded ``__Zfake``-labeled instantiation with no real export still
    triggers this fallback on ELF, wrongly attributing an unrelated,
    genuinely-exported ``_Zfake``. Gating on real platform evidence
    (mirroring :mod:`archive_graph`) was investigated and rejected: the one
    cheap candidate, ``CompileUnit.target_triple``, is unreliable for
    exactly the case that matters -- a real macOS build using the default
    system ``clang++`` with no explicit ``--target=`` leaves it empty, so
    gating on it would reintroduce the original Mach-O join *failures*
    this fix closed, in the common case. A real fix needs a genuine
    toolchain-identity probe -- already tracked as separate, deferred work
    (AGENTS.md's "toolchain profile" known-gaps entry)."""
    if symbol_node_id(symbol) in known_symbols:
        return symbol
    normalized = _normalize_mangled(symbol)
    if normalized == symbol:
        return None
    return normalized if symbol_node_id(normalized) in known_symbols else None


def augment_graph_with_templates(
    graph: SourceGraphSummary,
    instantiations: list[TemplateInstantiation],
    project_files: frozenset[str] | None = None,
) -> int:
    """Fold *instantiations* into *graph* (G29 Phase 5 item 1).

    Mints a ``template_decl`` node per distinct :attr:`~abicheck.buildsource.
    template_graph.TemplateInstantiation.
    template_qname` and a ``template_instantiation`` node per instantiation,
    joined by :data:`EDGE_DECL_INSTANTIATES_TEMPLATE`. A resolved *type*
    argument gets a :data:`EDGE_TEMPLATE_USES_TYPE` edge onto
    ``type://<qname>``; a resolved *declaration* argument
    (``target_decl_kind`` in :data:`~abicheck.buildsource.template_graph.
    _VALUE_DECL_KINDS`) gets
    :data:`EDGE_TEMPLATE_USES_DECL` onto ``decl://<identity>`` instead —
    the shared-node-id join, same principle as every other producer here.

    **An :data:`EDGE_INSTANTIATION_EMITS_SYMBOL` edge is only emitted for a
    mangled name the graph already carries a ``binary_symbol://`` node
    for** — the identical ADR-057 D1 "one shared node id is the whole join
    mechanism" rule :mod:`archive_graph` already applies: an instantiated
    member the linker discarded has no export-table entry, so minting a
    symbol node for it would inflate the graph for no analytical gain. See
    :func:`_resolve_emitted_symbol` for the exact-then-fallback resolution
    order this join uses and why.

    Returns the number of edges added.
    """
    from ..model.graph_identity import _decl_node_id, _type_node_id
    from ..model.source_graph import _symbol_node_id
    from .call_graph import _file_in_project

    node_by_id: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
    known_symbols = _symbol_node_ids(graph)
    added = 0

    def ensure_node(
        node_id: str, kind: str, label: str, attrs: dict[str, Any] | None = None
    ) -> None:
        if node_id in node_by_id:
            return
        node = GraphNode(
            id=node_id,
            kind=kind,
            label=label,
            provenance=TEMPLATE_GRAPH_PROVENANCE,
            confidence=CONF_HIGH,
            attrs=dict(attrs or {}),
        )
        graph.add_node(node)
        node_by_id[node_id] = node

    def add_edge(src: str, dst: str, kind: str, confidence: str) -> None:
        nonlocal added
        before = len(graph.edges)
        graph.add_edge(
            GraphEdge(
                src=src,
                dst=dst,
                kind=kind,
                provenance=TEMPLATE_GRAPH_PROVENANCE,
                confidence=confidence,
            )
        )
        added += len(graph.edges) - before

    for inst in instantiations:
        template_id = template_decl_node_id(
            inst.template_qname, inst.template_signature
        )
        ensure_node(template_id, NODE_TEMPLATE_DECL, inst.template_qname)

        # The instantiation's own *resolved* symbol identity, NOT an
        # independent normalization (self-review round, second pass;
        # see _resolve_emitted_symbol's own docstring for why). Falls
        # back to the raw spelling only when nothing resolves.
        function_mangled = None
        if inst.kind == _FUNCTION_KIND and inst.emitted_symbols:
            primary_symbol = inst.emitted_symbols[0]
            function_mangled = (
                _resolve_emitted_symbol(primary_symbol, known_symbols, _symbol_node_id)
                or primary_symbol
            )
        inst_id = template_instantiation_node_id(inst.label, function_mangled)
        dst_in_project = bool(
            project_files and inst.file and _file_in_project(inst.file, project_files)
        )
        ensure_node(
            inst_id,
            NODE_TEMPLATE_INSTANTIATION,
            inst.label,
            attrs=(
                {"defined_in_project": True, "def_file": inst.file}
                if dst_in_project
                else {}
            ),
        )
        add_edge(inst_id, template_id, EDGE_DECL_INSTANTIATES_TEMPLATE, CONF_HIGH)

        for arg in inst.args:
            if not arg.target_qname:
                continue
            if arg.target_decl_kind in _VALUE_DECL_KINDS:
                # TEMPLATE_USES_DECL: target_qname is already the resolved
                # decl:// identity (see template_graph_value_decls.py).
                decl_id = _decl_node_id(arg.target_qname)
                ensure_node(decl_id, NODE_SOURCE_DECL, arg.target_qname)
                add_edge(inst_id, decl_id, EDGE_TEMPLATE_USES_DECL, CONF_HIGH)
                continue
            # arg.target_decl_kind is the target's raw clang decl kind
            # (populated by _resolve_arg_targets) -- record_type is still the
            # right default for an unrecognized/absent kind (e.g. a nested
            # specialization resolved via _resolve_specialization_qname,
            # which is always itself a record), matching
            # augment_graph_with_types's own fallback for the identical
            # situation.
            type_id = _type_node_id(arg.target_qname)
            ensure_node(
                type_id, _type_node_kind(arg.target_decl_kind or ""), arg.target_qname
            )
            add_edge(inst_id, type_id, EDGE_TEMPLATE_USES_TYPE, CONF_HIGH)

        for symbol in inst.emitted_symbols:
            resolved = _resolve_emitted_symbol(symbol, known_symbols, _symbol_node_id)
            if resolved is not None:
                add_edge(
                    inst_id,
                    _symbol_node_id(resolved),
                    EDGE_INSTANTIATION_EMITS_SYMBOL,
                    CONF_REDUCED,
                )

    return added
