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

"""Declared business/runtime use cases, promoted to graph facts (G29 Phase 4
slice 2, an amendment to ADR-057).

``abicheck.impact.consumer_graph`` (slice 1) joins a real ``--used-by``
binary's *symbol-level* requirements onto the library's own graph. This
module is the declared, human-authored counterpart: an optional
``impact-use-cases.yaml`` manifest names a project's own business/runtime
use cases (e.g. "the DAL training workflow") and which public entry points
and tests exercise each one, so a finding can eventually be scoped not just
to "some consumer requires this symbol" but to "the training workflow does".

**Deliberately a separate schema from
``docs/contribute/usecase-registry.yaml``** — that registry tracks
abicheck's *own* feature coverage (does abicheck support header-only
analysis?), a completely different, tool-internal concept. Reusing it here
would conflate two unrelated meanings of "use case" under one file format.

Same discipline as :mod:`abicheck.impact.consumer_graph` throughout:
everything degrades to "no answer" rather than to a wrong one. A manifest
entry naming an entrypoint the library graph cannot resolve is silently
skipped — not an error, and not evidence the entrypoint doesn't exist; only
a structurally malformed manifest *document* (not a YAML list, an entry
that's not a mapping, a missing/blank ``use_case`` name) raises
:class:`~abicheck.errors.UseCaseManifestError`.

**Not implemented in this slice** (see ADR-057's "Deliberately not
implemented this slice", G29 Phase 4): runtime-trace ingestion
(``TRACE_OBSERVED_ENTRY``/``TRACE_OBSERVED_EDGE`` stay reserved vocabulary,
same as :mod:`abicheck.impact.consumer_graph`'s reserved consumer kinds), and
any *report-schema* ``Change.affected_use_cases`` field or
``USE_CASE_IMPACT_CONFIRMED`` finding kind wired into ``compare`` itself
(G29 Phase 6 — that needs its own schema bump and FP-gate examples, not a
drive-by extension here).

:func:`explain_use_case_impact` (G29 Phase 4, added alongside the
``project validate-use-cases --against-new`` CLI mode) is a narrower, real
step past graph-building alone: given a real two-snapshot diff's changed
symbols, it answers *which declared use case(s)' own entrypoints reach
each one* — but it is a **read-only report view**, not a `Change`
mutation. It constructs no new node/edge, sets no field on any `Change`
object, and is invisible to `compare`'s own report schema/exit code; a
caller renders its answer directly (today, only the CLI command above
does). This is the same "enrichment lives outside the object being
enriched" shape :mod:`abicheck.impact.engine`'s `assess_change` already
uses for `ImpactAssessment` — just without even a cached field on `Change`,
since nothing in this slice re-reads the answer more than once per run.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..buildsource.graph_facts import (
    CONF_HIGH,
    CONF_UNKNOWN,
    USE_CASE_EDGE_KINDS as USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS as USE_CASE_NODE_KINDS,
    GraphEdge,
    GraphNode,
)
from ..errors import UseCaseManifestError

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary

# USE_CASE_NODE_KINDS/USE_CASE_EDGE_KINDS are re-exported (imported above)
# from buildsource.graph_facts, the leaf that owns the whole graph vocabulary
# — see the comment there for why they live in a leaf rather than beside this
# producer.

#: ``provenance`` tag every node/edge this module creates carries, so a
#: declared-use-case fact is distinguishable from a build-evidence,
#: replay, or consumer (ADR-057 slice 1) one in the ADR-046 D2 merge
#: (``GraphFact.producer``).
USE_CASE_PROVENANCE = "declared_use_case"


class _DuplicateKeyCheckingLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` with one behavior change: a mapping that repeats a
    key is a load error instead of the PyYAML default of silently keeping
    only the last value.

    A manifest author who accidentally repeats a field (two ``entrypoints:``
    lines in one use-case entry, most plausibly from a copy-paste) would
    otherwise have part of their declared coverage silently dropped with no
    signal at all — exactly the "coverage quietly disappears" failure mode
    :func:`load_use_case_manifest`'s docstring already promises this module
    never allows. Scoped to this loader class alone (not a process-wide
    ``yaml`` monkeypatch), so it affects nothing outside this module.
    """


def _construct_mapping_rejecting_duplicates(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """PyYAML's own ``SafeConstructor.construct_mapping`` already rejects an
    unhashable key (e.g. ``- {[a, b]: x}``, a YAML sequence used as a
    mapping key) with a ``ConstructorError`` — a check this override must
    keep, not just the duplicate-key check it adds, or a syntactically
    valid-but-unhashable-keyed document raises a bare ``TypeError`` that
    escapes ``load_use_case_manifest``'s ``except yaml.YAMLError`` and the
    documented :class:`~abicheck.errors.UseCaseManifestError` contract
    entirely (Codex review, fresh evidence)."""
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found unhashable key: {key!r}",
                key_node.start_mark,
            ) from exc
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyCheckingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_rejecting_duplicates,
)


def use_case_node_id(name: str) -> str:
    """Node id for a declared use case. Mirrors the ``<scheme>://<name>``
    convention every other id helper in this package/``source_graph.py``
    uses."""
    return f"use_case://{name}"


def test_case_node_id(name: str) -> str:
    """Node id for a declared test case."""
    return f"test_case://{name}"


@dataclass(frozen=True)
class UseCaseDefinition:
    """One ``impact-use-cases.yaml`` entry, already validated.

    ``entrypoints``/``tests`` are free-form labels from the manifest author's
    own vocabulary — a symbol name, a qualified declaration name, or any
    other string a human chose to write there. Only ``entrypoints`` is ever
    matched against the library graph (:func:`build_use_case_graph`);
    ``tests`` are recorded as-is, since there is no graph node kind for an
    external test identifier to resolve against.
    """

    use_case: str
    entrypoints: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()


#: The only keys a manifest entry may declare (Codex review, fresh evidence):
#: an unrecognized key (e.g. a misspelled ``entrypoint``/``test`` instead of
#: ``entrypoints``/``tests``) must be a hard error, not silently ignored --
#: ``mapping.get(...)`` treats an unknown key as absent and would otherwise
#: load successfully while quietly dropping the coverage the author actually
#: declared, exactly the failure mode this module's docstring already
#: promises never happens.
_MANIFEST_ENTRY_KEYS = frozenset({"use_case", "entrypoints", "tests"})


def _require_mapping(entry: Any, index: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: entry {index} must be a mapping, got "
            f"{type(entry).__name__}"
        )
    # Sorted by repr, not the bare keys (Codex review, fresh evidence): a
    # syntactically valid YAML mapping may use heterogeneous key types (an
    # integer key alongside a string one, e.g. `1: foo`) -- sorted() on the
    # raw values would raise a bare TypeError comparing int to str *before*
    # UseCaseManifestError could even be constructed, breaking this
    # function's own single-exception contract for exactly the malformed
    # input it exists to reject cleanly.
    unknown = sorted((set(entry) - _MANIFEST_ENTRY_KEYS), key=repr)
    if unknown:
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: entry {index} has unknown field(s) "
            f"{unknown!r} — expected only {sorted(_MANIFEST_ENTRY_KEYS)}"
        )
    return entry


def _string_list(value: Any, *, field_name: str, use_case: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: use case {use_case!r}'s {field_name!r} "
            "must be a YAML list of strings"
        )
    return tuple(value)


def parse_use_case_manifest(raw: Any) -> list[UseCaseDefinition]:
    """Parse an already-``yaml.safe_load``-ed document into
    :class:`UseCaseDefinition`\\ s.

    Split from :func:`load_use_case_manifest` so a caller that already has
    the parsed YAML (e.g. folded into a larger project-config document) does
    not need a real file on disk to validate it — the same split
    ``policy_file.py``'s own parser/loader pair uses.

    A missing manifest is not this function's concern: an *empty* document
    (``raw is None``, an empty file) is a valid, empty manifest — no use
    cases declared — not an error.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise UseCaseManifestError(
            "impact-use-cases.yaml: top-level document must be a YAML list "
            f"of use cases, got {type(raw).__name__}"
        )
    definitions: list[UseCaseDefinition] = []
    for index, entry in enumerate(raw):
        mapping = _require_mapping(entry, index)
        name = mapping.get("use_case")
        if not isinstance(name, str) or not name.strip():
            raise UseCaseManifestError(
                f"impact-use-cases.yaml: entry {index} is missing a non-empty "
                "'use_case' name"
            )
        definitions.append(
            UseCaseDefinition(
                use_case=name,
                entrypoints=_string_list(
                    mapping.get("entrypoints"), field_name="entrypoints", use_case=name
                ),
                tests=_string_list(
                    mapping.get("tests"), field_name="tests", use_case=name
                ),
            )
        )
    return definitions


def load_use_case_manifest(path: str | Path) -> list[UseCaseDefinition]:
    """Load and parse an ``impact-use-cases.yaml`` manifest from disk.

    Raises :class:`~abicheck.errors.UseCaseManifestError` for a structurally
    malformed document (not a list, a non-mapping entry, a missing/blank
    ``use_case`` name), a syntactically invalid one, one that repeats a
    mapping key (:class:`_DuplicateKeyCheckingLoader`), or one that isn't
    valid UTF-8 — the same hard-load-error discipline
    ``policy_file.py``/``suppression.py`` already use for user-supplied
    YAML, since silently skipping or overwriting a malformed entry could
    make a use case's declared coverage quietly disappear.

    A missing/unreadable *path* itself (``OSError`` — no such file, a
    directory, a permissions error) is deliberately left as-is, not wrapped
    — that is an ordinary filesystem error a caller already knows how to
    handle, distinct from "the file exists but its contents are malformed"
    (Codex review, fresh evidence: the read used to happen *outside* this
    function's guarded block at all, so a real-world non-UTF-8 manifest
    raised a bare ``UnicodeDecodeError`` instead of the documented
    exception type).

    Also wraps a bare ``ValueError`` from PyYAML's own implicit-resolver
    scalar constructors (Codex review, fresh evidence): a value that looks
    like a YAML timestamp but has an invalid component (``2023-99-99`` —
    no such month) is resolved to the timestamp tag by PyYAML's *resolver*
    before construction even reaches this loader's own overrides, and its
    built-in timestamp constructor raises a plain ``ValueError`` (not a
    ``yaml.YAMLError`` subclass) for an out-of-range date/time part — a
    document shape neither the syntax nor the UTF-8 guard above catches.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
        # `_DuplicateKeyCheckingLoader` subclasses `yaml.SafeLoader` and only
        # replaces its mapping constructor with the duplicate-key check;
        # bandit's B506 flags every `Loader=` it cannot name-match against
        # `SafeLoader`/`CSafeLoader`, subclasses included (same annotation as
        # `dump_manifest._load_strict_yaml`).
        raw = yaml.load(text, Loader=_DuplicateKeyCheckingLoader)  # nosec B506
    except UnicodeError as exc:
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: {path}: not valid UTF-8: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: {path}: invalid YAML syntax: {exc}"
        ) from exc
    except ValueError as exc:
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: {path}: invalid scalar value: {exc}"
        ) from exc
    return parse_use_case_manifest(raw)


def _public_entry_index(library_graph: SourceGraphSummary) -> dict[str, str]:
    """Every string a manifest ``entrypoints`` value might name -> the node
    id it resolves to in *library_graph*.

    A "public entry" here is either an exported ``binary_symbol`` node (a
    library's export table names a real, consumer-visible entry point even
    when no source graph exists to walk from it — e.g. a binary-only or
    header-only graph) or a ``source_decl`` node whose own declared
    ``visibility`` is public (``PUBLIC_VISIBILITIES`` — the same set
    :mod:`abicheck.impact.consumer_graph`'s direct-requirement check reads).

    Deliberately restricted to these two kinds, not any public-visibility
    node (Codex review, fresh evidence): a public ``record_type``/
    ``enum_type``/``typedef`` can share ``PUBLIC_VISIBILITIES`` too, but a
    type is never something a use case "exercises" — it has no outgoing
    ``DECL_CALLS_DECL`` edge for a consumer-impact walk to follow, and a
    manifest entry naming one by label is far more likely to be an author's
    mistake than a genuine business/runtime entrypoint. Accepting it would
    emit a ``USE_CASE_USES_ENTRY`` edge to a node the graph's own
    entry-domain predicates (``is_consumer_compiled_public_entry`` and
    friends) never treat as a real call-graph starting point.

    A node's own id is always registered (exact-id lookups can never be
    ambiguous — two distinct nodes never share one id). A node's ``label``
    (when set) is registered only when it resolves *uniquely* across every
    public node in the graph, **after** coalescing a declaration onto the
    binary symbol it maps to (below) — overloaded C++ entry points routinely
    share one display label with no such mapping between them, and silently
    picking an arbitrary one of them would hand a manifest's
    ``entrypoints: [foo]`` a wrong-but-confident answer instead of the
    "degrade to no answer" this module otherwise guarantees
    (:func:`build_use_case_graph`). An id also always wins over a label —
    built as a separate pass, an ambiguous or colliding label can never
    shadow an exact id already registered.

    Coalescing (Codex review, fresh evidence): an ordinary exported C
    function like ``train`` produces **two** public nodes sharing the exact
    label ``"train"`` — the ``source_decl`` (``build_source_graph``'s
    ``label=ent.qualified_name or ent.identity()``) and the ``binary_symbol``
    it maps to (``label=symbol``, joined by a ``SOURCE_DECL_MAPS_TO_SYMBOL``
    edge) — since a plain C function's declared name, qualified name, and
    linker symbol are all the identical string. Without coalescing, this is
    exactly the common case a manifest's plain-label ``entrypoints: [train]``
    is meant to name, yet it would read as a genuine two-way ambiguity and be
    silently dropped. A declaration and the export it maps to are one public
    entry represented twice in the graph, not two candidates competing for
    one label — collapsed onto the mapped symbol id before the
    ambiguity/uniqueness check runs, so only a *real* overload collision
    (two distinct declarations sharing a label with no mapping edge joining
    them) is still treated as ambiguous.
    """
    from ..buildsource.source_graph_query import PUBLIC_VISIBILITIES

    def is_public(node: GraphNode) -> bool:
        if node.kind == "binary_symbol":
            return True
        if node.kind != "source_decl":
            return False
        visibility = str((node.resolved or node.attrs).get("visibility", ""))
        return visibility in PUBLIC_VISIBILITIES

    public_nodes = [n for n in library_graph.nodes if is_public(n)]
    public_ids = {n.id for n in public_nodes}

    index: dict[str, str] = {n.id: n.id for n in public_nodes}

    # A public decl's mapped-to public symbol id, when *exactly one* exists
    # — the coalescing key. Restricted to edges between two *public* nodes
    # so a mapping onto an internal/unresolved target can't smuggle a
    # non-public id into the label index.
    #
    # Collected as a set per src, not a plain {src: dst} dict comprehension
    # (Codex review, fresh evidence): a decl legitimately mapped to more
    # than one symbol (versioned exports, aliases, or facts merged from more
    # than one graph producer under ADR-046 D2's evidence-preserving merge —
    # add_edge dedups only on the full (src, dst, kind, role) relation key,
    # so two such edges coexist rather than collapsing) would otherwise have
    # a plain dict comprehension silently keep whichever destination came
    # last in ``library_graph.edges`` iteration order — an
    # order-dependent, wrong-but-confident coalescing target instead of the
    # "degrade to no answer" this whole function otherwise guarantees for a
    # genuine ambiguity.
    mapped_symbols: dict[str, set[str]] = {}
    for e in library_graph.edges:
        if (
            e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
            and e.src in public_ids
            and e.dst in public_ids
        ):
            mapped_symbols.setdefault(e.src, set()).add(e.dst)

    def coalesced(node_id: str) -> str:
        targets = mapped_symbols.get(node_id)
        if targets is not None and len(targets) == 1:
            return next(iter(targets))
        return node_id

    label_targets: dict[str, set[str]] = {}
    for node in public_nodes:
        if node.label:
            label_targets.setdefault(node.label, set()).add(coalesced(node.id))
    for label, targets in label_targets.items():
        if label in index:
            continue  # an exact id already claims this string; it wins
        if len(targets) == 1:
            index[label] = next(iter(targets))
        # else: label is ambiguous across >1 public node — left unresolved
    return index


@dataclass(frozen=True)
class UseCaseResolution:
    """Per-use-case entrypoint resolution against a real library graph —
    the read view :func:`resolve_use_case_entrypoints` returns.

    Deliberately a *report*, not a graph: ``build_use_case_graph`` already
    silently skips an unresolvable ``entrypoints`` value by design (this
    module's own "absence, never a wrong answer" discipline — see its
    docstring), which is correct for graph construction but leaves a
    manifest author with zero visibility into *which* of their declared
    entrypoints actually matched anything. This dataclass is that missing
    visibility, for a caller (``project validate-use-cases``) that wants to
    report it without changing what the graph itself records.
    """

    use_case: str
    resolved_entrypoints: tuple[str, ...] = ()
    unresolved_entrypoints: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()


def resolve_use_case_entrypoints(
    definitions: list[UseCaseDefinition], library_graph: SourceGraphSummary
) -> list[UseCaseResolution]:
    """Resolve every definition's ``entrypoints`` against *library_graph*,
    reporting which matched and which didn't — a read-only companion to
    :func:`build_use_case_graph`, not a replacement for it.

    Reuses :func:`_public_entry_index` (the exact same resolution
    :func:`build_use_case_graph` performs) so this can never disagree with
    what the graph actually ends up recording — a manifest entrypoint this
    function reports as resolved is guaranteed to be exactly the set
    :func:`build_use_case_graph` would silently keep, and one reported
    unresolved is exactly the set it would silently skip. Order preserved
    from *definitions*; a use case's own ``resolved_entrypoints``/
    ``unresolved_entrypoints`` preserve the manifest's declared order too
    (not sorted), so a report reads in the same order the manifest author
    wrote it.
    """
    entries = _public_entry_index(library_graph)
    resolutions: list[UseCaseResolution] = []
    for definition in definitions:
        resolved = tuple(e for e in definition.entrypoints if e in entries)
        unresolved = tuple(e for e in definition.entrypoints if e not in entries)
        resolutions.append(
            UseCaseResolution(
                use_case=definition.use_case,
                resolved_entrypoints=resolved,
                unresolved_entrypoints=unresolved,
                tests=definition.tests,
            )
        )
    return resolutions


def build_use_case_graph(
    definitions: list[UseCaseDefinition], library_graph: SourceGraphSummary
) -> SourceGraphSummary:
    """Promote *definitions* to ``use_case``/``test_case`` graph facts,
    joined onto *library_graph*'s own public-entry nodes.

    An ``entrypoints`` value that cannot be resolved against
    *library_graph* (see :func:`_public_entry_index`) is silently skipped —
    no node, no edge, no error — the same "absence, never a wrong answer"
    discipline :mod:`abicheck.impact.consumer_graph` already follows for an
    unresolvable required symbol. A use case with none of its declared
    entrypoints resolved still gets its own ``use_case`` node (and its
    ``test_case`` nodes/edges, which never depend on entrypoint
    resolution) — only the ``USE_CASE_USES_ENTRY`` edges are conditional.

    A resolved entrypoint also gets a same-id placeholder node registered
    alongside its edge (kind copied from the real library node) — mirroring
    :func:`abicheck.impact.consumer_graph.build_consumer_graph`'s identical
    pattern for its ``binary_symbol`` targets. Without it, only an *edge*
    would point at the library node and the join (:func:`join_use_case_graph`)
    would never actually deposit a ``USE_CASE_PROVENANCE`` fact onto the
    shared node — the merge (ADR-046 D2) only happens on a node/edge
    registration, not implicitly because an edge names an id.

    Deliberately registered at ``CONF_UNKNOWN``, not ``CONF_HIGH`` (Codex
    review, fresh evidence): ``SourceGraphSummary.add_node``'s ADR-046 D2
    merge doesn't keep ``attrs``/``provenance``/``confidence`` per-producer —
    ``provenance``/``confidence`` are each set from the single
    highest-*precedence* fact across every producer that ever registered the
    node (``ensure_facts_and_resolve``'s ``top = min(entity.facts, key=
    _precedence_key)``), and precedence ranks confidence first. A
    ``kythe``/``codeql``-sourced or indirectly-resolved ``call_graph``
    fallback node carries no ``consumer_compiled_body`` attr and is exactly
    ``CONF_REDUCED`` — lower than this module's placeholder would be at
    ``CONF_HIGH`` — which would let a use-case reference *win* that node's
    ``provenance`` outright. `source_graph.is_consumer_compiled_node`` falls
    back to reading ``provenance`` exactly when no ``consumer_compiled_body``
    attr is present, so a provenance flip from a real "unproven body" signal
    to ``declared_use_case`` (not itself one of the three provenances that
    signal excludes) would flip that predicate from correctly conservative
    (``False``) to wrongly permissive (``True``) — letting a later
    consumer-impact call-graph traversal walk straight through an out-of-line
    body it was never proven to reach, and report a false impact path.
    ``CONF_UNKNOWN`` is the lowest rank (:data:`~abicheck.buildsource.
    graph_facts._CONFIDENCE_RANK`), so this placeholder can never outrank
    *any* real evidence the target node already carries — it only ever wins
    when the target has no competing fact at all, which cannot happen here
    (:func:`_public_entry_index` only ever resolves an id/label already
    present in *library_graph*).

    Every ``test_case`` named in ``tests`` gets its own node and a
    ``TEST_COVERS_USE_CASE`` edge onto its use case, unconditionally: unlike
    an entrypoint, a test identifier has no corresponding node kind in the
    library graph to resolve against, so there is nothing to fail to
    resolve.
    """
    from ..model.source_graph import SourceGraphSummary

    entries = _public_entry_index(library_graph)
    node_by_id = {n.id: n for n in library_graph.nodes}
    graph = SourceGraphSummary()
    for definition in definitions:
        uc_id = use_case_node_id(definition.use_case)
        graph.add_node(
            GraphNode(
                id=uc_id,
                kind="use_case",
                label=definition.use_case,
                provenance=USE_CASE_PROVENANCE,
                confidence=CONF_HIGH,
            )
        )
        for entry_name in definition.entrypoints:
            target = entries.get(entry_name)
            if target is None:
                continue
            target_kind = node_by_id[target].kind
            graph.add_node(
                GraphNode(
                    id=target,
                    kind=target_kind,
                    provenance=USE_CASE_PROVENANCE,
                    confidence=CONF_UNKNOWN,
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=uc_id,
                    dst=target,
                    kind="USE_CASE_USES_ENTRY",
                    provenance=USE_CASE_PROVENANCE,
                    confidence=CONF_HIGH,
                )
            )
        for test_name in definition.tests:
            test_id = test_case_node_id(test_name)
            graph.add_node(
                GraphNode(
                    id=test_id,
                    kind="test_case",
                    label=test_name,
                    provenance=USE_CASE_PROVENANCE,
                    confidence=CONF_HIGH,
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=test_id,
                    dst=uc_id,
                    kind="TEST_COVERS_USE_CASE",
                    provenance=USE_CASE_PROVENANCE,
                    confidence=CONF_HIGH,
                )
            )
    return graph


def join_use_case_graph(
    library_graph: SourceGraphSummary, use_cases: SourceGraphSummary
) -> SourceGraphSummary:
    """Fold *use_cases*'s nodes/edges into a **deep copy** of *library_graph*.

    Mirrors :func:`abicheck.impact.consumer_graph.join_consumer_graph`
    exactly, including the reasoning: a shallow re-registration of the
    library graph's own :class:`~abicheck.buildsource.graph_facts.GraphNode`
    objects would work today (nothing here mutates a node's `attrs`
    directly), but ``SourceGraphSummary.add_node`` merges into the *stored*
    object in place (ADR-046 D2) — the library graph is read off an
    ``AbiSnapshot``'s embedded pack and is shared with every other consumer
    of that snapshot (``internal_leak``'s walks, ``source_graph_findings``'
    diff, and now also :mod:`abicheck.impact.consumer_graph`'s own join). A
    shallow fold would leak one project's declared-use-case facts onto the
    library graph's own public-entry nodes, corrupting every unrelated
    analysis of the same run — exactly the failure mode the consumer-graph
    join's own regression test guards against. Deep-copying first keeps this
    join's blast radius confined to its own returned graph.

    Within the copy, that same ADR-046 D2 merge performs the join itself: a
    node the library graph already has (a ``binary_symbol`` or public
    ``source_decl``) and this module's edge also names ends up as **one**
    node/edge carrying both producers' facts — there is nothing else to
    "join" beyond registering into the same store.

    ``coverage``/``extractor_passes``/``narrowed_passes``/``degraded_passes``
    are carried over from *library_graph* unchanged, for the identical
    reason ``join_consumer_graph`` keeps them unchanged: a declared use case
    is not a source-extraction pass, and rewriting those flags would make
    the library's own coverage honesty describe a pass that never ran.
    Deliberately not :meth:`~abicheck.model.source_graph.SourceGraphSummary.finalize`\\ d
    either, for the same transient-graph reason.

    Restores every already-existing node's own ``provenance``/``confidence``
    to their pre-join value afterward (Codex review, fresh evidence — a
    residual gap in the earlier ``CONF_UNKNOWN`` placeholder-confidence fix):
    ``ensure_facts_and_resolve``'s ADR-046 D2 merge picks **one** globally
    winning fact for a node's ``provenance``/``confidence`` via a full
    precedence order (confidence rank first, then producer name as a
    tiebreak) — there is no way to mark a fact as "never eligible to win",
    so no confidence level :func:`build_use_case_graph`'s coalescing
    placeholder could choose is safe against *every* real producer's own
    confidence/name combination. ``CONF_UNKNOWN`` (the lowest rank) stops a
    higher-confidence real fact from ever losing to it, but a real fact that
    is *also* ``CONF_UNKNOWN`` (e.g. a loaded or hand-built graph whose node
    omitted a confidence) ties on rank, and the producer-name tiebreak alone
    can still pick ``"declared_use_case"`` over a real producer whose name
    sorts later (``"kythe"``, ``"codeql"``, …) — the same
    ``is_consumer_compiled_node()`` provenance-fallback risk the earlier fix
    closed for the confidence-ordering case, reopened for the tied case.
    Restoring the pre-join value afterward is correct regardless of
    ordering, rather than attempting to out-rank every possible producer
    name — this module's own ``facts`` contribution (what
    :func:`join_use_case_graph`'s own docstring above and its regression
    test check) is untouched, only the single summary ``provenance``/
    ``confidence`` fields are reset to what the library graph already
    resolved before this join ran.

    **Known residual gap** (Codex review, fresh evidence): this restoration
    only patches the *already-resolved* ``provenance``/``confidence``
    fields on the graph this function returns — the risky
    ``declared_use_case``/``CONF_UNKNOWN`` fact itself is still sitting in
    ``node.facts`` (deliberately; that is the join's whole point). Nothing
    in the current codebase re-runs ``ensure_facts_and_resolve`` on this
    specific returned graph afterward (confirmed: :func:`join_use_case_graph`
    has no other caller today — this module isn't wired into any real
    ``compare``/report pipeline yet, per this file's own module docstring),
    so this fix is correct for every real code path that exists right now.
    But a *future* caller that merges this joined graph's nodes into another
    graph via :meth:`~abicheck.model.source_graph.SourceGraphSummary.add_node`
    again, or round-trips it through ``to_dict()``/``from_dict()``, would
    re-trigger the same tied-confidence provenance risk this function just
    fixed, since both recompute ``provenance``/``confidence`` fresh from the
    unchanged ``facts`` list. Closing that residual gap for real needs a
    change to the shared merge precedence itself (a way to mark a
    :class:`~abicheck.buildsource.graph_facts.GraphFact` as never eligible
    to win the "top" pick in ``graph_facts._precedence_key``/
    ``ensure_facts_and_resolve``) — every graph producer in the codebase
    shares that one precedence function, so this is a scoped design
    decision of its own, not a drive-by fix bundled into this module's join.
    Whoever wires :func:`join_use_case_graph`'s result into a real pipeline
    that might re-merge or re-serialize it must close this gap first.

    Clears the copied ``graph_id`` before returning (Codex review, fresh
    evidence): unlike :func:`~abicheck.impact.consumer_graph.join_consumer_graph`
    — whose result never leaves ``appcompat.py``'s own in-memory analysis —
    this function is the module's *documented public Python API* (see
    ``docs/contribute/use-case-impact.md``), so a caller following that doc and
    calling ``joined.to_dict()`` on the result is a real, reachable path,
    not a hypothetical future pipeline. ``library_graph`` may already carry
    a non-empty ``graph_id`` from its own :meth:`~abicheck.model.source_graph.SourceGraphSummary.finalize`;
    left untouched, the deep copy would inherit that same id even though the
    node/edge content just changed, and ``to_dict()`` only recomputes an id
    when the stored value is empty — silently describing this join's
    different content under the library graph's own unrelated id, which
    could corrupt a content-addressed cache or an identity comparison keyed
    on it. Clearing (rather than eagerly recomputing via
    :meth:`~abicheck.model.source_graph.SourceGraphSummary.compute_graph_id`)
    matches this function's own choice to leave the rest of ``finalize()``'s
    output (``coverage``/etc.) untouched — the join result is still not
    :meth:`~abicheck.model.source_graph.SourceGraphSummary.finalize`\\ d,
    so an empty id is the honest "not finalized" signal a caller who does
    want one can resolve via ``compute_graph_id()``/``finalize()`` itself.
    """
    original = {n.id: (n.provenance, n.confidence) for n in library_graph.nodes}
    joined = copy.deepcopy(library_graph)
    for node in use_cases.nodes:
        joined.add_node(copy.deepcopy(node))
    for edge in use_cases.edges:
        joined.add_edge(copy.deepcopy(edge))
    for node in joined.nodes:
        restore = original.get(node.id)
        if restore is not None:
            node.provenance, node.confidence = restore
    joined.graph_id = ""
    return joined


def explain_use_case_impact(
    definitions: list[UseCaseDefinition],
    library_graph: SourceGraphSummary,
    symbols: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    """For each of *symbols* (an ordinary two-snapshot diff's own
    ``Change.symbol`` values), which declared use case(s)' own resolved
    entrypoints reach it via *library_graph*'s call/reference edges (G29
    Phase 4, the use-case counterpart of
    :func:`abicheck.impact.consumer_graph.explain_required_symbols`).

    The key difference from that function: its root set is *every*
    consumer-compiled public entry in the library, because a real
    consumer's requirement is proven by the export table alone, independent
    of which declared use case (if any) covers it. Here the roots are
    restricted to exactly the entrypoints *this manifest itself declares* —
    "every public entry reaches it" says nothing about whether a
    *specific* use case's own surface does, and attributing a change to
    every use case whenever *any* public entry reaches it would make the
    field meaningless (every use case would show every public-reachable
    change). No :func:`join_use_case_graph` fold is needed for this walk:
    entrypoint resolution reads only :func:`_public_entry_index` against the
    plain library graph, the identical resolution :func:`build_use_case_graph`
    and :func:`resolve_use_case_entrypoints` already perform.

    Returns ``{symbol: (use_case_name, ...)}`` for exactly the symbols this
    manifest's own entrypoints can be shown to reach — a symbol absent from
    the mapping is not evidence no use case touches it, only that this
    function found no proof, mirroring
    :func:`~abicheck.impact.consumer_graph.explain_required_symbols`'s own
    "absent, never speculative" contract. Two structural limitations, both
    inherited from that same sibling function rather than introduced here:
    a *type*-shaped change (no ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge — no
    declaration ever backs a `record_type`/`enum_type` node) can never be
    named, only a function/variable-shaped one; and only a
    *consumer-compiled* entrypoint (:func:`~abicheck.buildsource.
    source_graph.is_consumer_compiled_public_entry` — inline/template
    bodies, not an ordinary out-of-line exported function's own internal
    calls) can walk past its own declaration, so a use case naming only
    ordinary exported functions as entrypoints still gets direct-hit
    attribution (the symbol's own declaration is one of the resolved
    entrypoints) but never transitive attribution through it.
    """
    wanted = set(symbols)
    if not wanted or not definitions:
        return {}

    from ..buildsource.source_graph_query import is_consumer_compiled_public_entry
    from ..internal_leak import (
        CALL_GRAPH_TRAVERSAL_POLICY,
        _consumer_compiled_reachability,
    )

    entry_index = _public_entry_index(library_graph)
    # Merge (not overwrite) across repeated `use_case` entries (Codex
    # review, fresh evidence): a manifest is not required to give each
    # use case exactly one list entry -- `parse_use_case_manifest` never
    # rejects a repeated `use_case` name (the duplicate-key check is per
    # *mapping*, and each entry is a separate list item, not a mapping
    # key), and `build_use_case_graph` already merges every entry sharing
    # a name onto the *same* `use_case` graph node, registering a
    # `USE_CASE_USES_ENTRY` edge for each entry's own entrypoints. A plain
    # `use_case_entries[name] = ids` assignment here disagreed with that
    # graph it claims to mirror: the later entry's entrypoint set would
    # silently replace the earlier one's, so a change reachable only
    # through an earlier entry's entrypoints would never be attributed.
    use_case_entries: dict[str, set[str]] = {}
    for definition in definitions:
        ids = {entry_index[e] for e in definition.entrypoints if e in entry_index}
        if ids:
            use_case_entries.setdefault(definition.use_case, set()).update(ids)
    if not use_case_entries:
        return {}

    # decl node id -> every *wanted* symbol name it maps to (plural -- Codex
    # review, fresh evidence: a single declaration can map to more than one
    # exported symbol, e.g. two versioned exports foo@V1/foo@V2 of the same
    # definition; a plain dict[str, str] here silently kept only the last
    # one seen, so a change to foo@V2 could be mis-attributed through
    # decl://foo to a use case that named foo@V1 specifically, or a genuine
    # foo@V1 change could vanish if foo@V2's edge happened to be visited
    # later), and the reverse symbol id -> decl id(s) -- the latter to
    # resolve a coalesced ``binary_symbol://`` entry (`_public_entry_index`
    # prefers the mapped symbol id over the declaring decl id, see its own
    # docstring) back to the decl node(s) the call-graph's own edges are
    # keyed on: a ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` edge's
    # src/dst is always a decl (or type) node id, never a bare
    # exported-symbol one, so walking from the coalesced binary_symbol id
    # directly would find no outgoing edges at all and silently explain
    # nothing.
    decl_to_symbols: dict[str, set[str]] = {}
    # Every decl that maps onto a given symbol, not just one (Codex review,
    # fresh evidence): the same export can legitimately be reached by more
    # than one SOURCE_DECL_MAPS_TO_SYMBOL edge -- e.g. an inline/weak
    # definition captured once per TU, all mangling to the identical symbol
    # name. A plain dict[str, str] here kept only the first decl seen
    # (edge-iteration-order dependent), and a manifest entry naming that
    # exact binary_symbol could then walk from a declaration with no
    # captured call edges (a forward declaration, an extern with no body in
    # *this* TU's evidence) while the sibling decl carrying the real body --
    # and its transitive calls -- was silently never walked at all.
    symbol_to_decls: dict[str, set[str]] = {}
    for edge in library_graph.edges:
        if edge.kind != "SOURCE_DECL_MAPS_TO_SYMBOL":
            continue
        symbol_to_decls.setdefault(edge.dst, set()).add(edge.src)
        name = edge.dst.removeprefix("binary_symbol://")
        if name in wanted:
            decl_to_symbols.setdefault(edge.src, set()).add(name)

    def walk_roots_for(entry: str) -> set[str]:
        return symbol_to_decls.get(entry, {entry})

    node_by_id = {n.id: n for n in library_graph.nodes}
    exported_decls = {
        e.src for e in library_graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    all_entries = sorted({eid for ids in use_case_entries.values() for eid in ids})
    walk_roots = sorted(
        {
            root
            for eid in all_entries
            for root in walk_roots_for(eid)
            if is_consumer_compiled_public_entry(root, node_by_id, exported_decls)
        }
    )
    reachability = (
        _consumer_compiled_reachability(
            library_graph, CALL_GRAPH_TRAVERSAL_POLICY, walk_roots, node_by_id
        )
        if walk_roots
        else {}
    )

    result: dict[str, set[str]] = {}
    for use_case, entry_ids in use_case_entries.items():
        for entry in entry_ids:
            # An entry that names one *exact* binary_symbol id directly
            # (the manifest wrote the versioned symbol itself, or
            # _public_entry_index left it uncoalesced because the owning
            # decl maps to more than one symbol -- see that function's own
            # ambiguity rule) is attributed to exactly that symbol alone,
            # never the decl's whole symbol set (Codex review, fresh
            # evidence): a use case that named foo@V1 specifically must not
            # also pick up a foo@V2-only change just because they share one
            # declaration.
            if entry.startswith("binary_symbol://"):
                exact_symbol = entry.removeprefix("binary_symbol://")
                if exact_symbol in wanted:
                    result.setdefault(exact_symbol, set()).add(use_case)
            else:
                # A decl-shaped entry (or a label that coalesced onto one)
                # didn't disambiguate a specific version at all, so every
                # wanted symbol that decl maps to counts.
                for direct_symbol in decl_to_symbols.get(entry, ()):
                    result.setdefault(direct_symbol, set()).add(use_case)
            for root in walk_roots_for(entry):
                seen, _came_from, _degraded = reachability.get(
                    root, (frozenset(), {}, frozenset())
                )
                for decl in seen:
                    for sym in decl_to_symbols.get(decl, ()):
                        result.setdefault(sym, set()).add(use_case)

    return {symbol: tuple(sorted(names)) for symbol, names in result.items()}
