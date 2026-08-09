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
same as :mod:`abicheck.impact.consumer_graph`'s reserved consumer kinds) and
any report-level ``affected_use_cases``/``USE_CASE_IMPACT_CONFIRMED``
surface (G29 Phase 6). This module only builds and joins the graph facts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..buildsource.graph_facts import (
    CONF_HIGH,
    USE_CASE_EDGE_KINDS as USE_CASE_EDGE_KINDS,
    USE_CASE_NODE_KINDS as USE_CASE_NODE_KINDS,
    GraphEdge,
    GraphNode,
)
from ..errors import UseCaseManifestError

if TYPE_CHECKING:
    from ..buildsource.source_graph import SourceGraphSummary

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
    unknown = sorted(set(entry) - _MANIFEST_ENTRY_KEYS)
    if unknown:
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: entry {index} has unknown field(s) "
            f"{unknown} — expected only {sorted(_MANIFEST_ENTRY_KEYS)}"
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
    ``use_case`` name), a syntactically invalid one, or one that repeats a
    mapping key (:class:`_DuplicateKeyCheckingLoader`) — the same
    hard-load-error discipline ``policy_file.py``/``suppression.py`` already
    use for user-supplied YAML, since silently skipping or overwriting a
    malformed entry could make a use case's declared coverage quietly
    disappear.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = yaml.load(text, Loader=_DuplicateKeyCheckingLoader)
    except yaml.YAMLError as exc:
        raise UseCaseManifestError(
            f"impact-use-cases.yaml: {path}: invalid YAML syntax: {exc}"
        ) from exc
    return parse_use_case_manifest(raw)


def _public_entry_index(library_graph: SourceGraphSummary) -> dict[str, str]:
    """Every string a manifest ``entrypoints`` value might name -> the node
    id it resolves to in *library_graph*.

    A "public entry" here is either an exported ``binary_symbol`` node (a
    library's export table names a real, consumer-visible entry point even
    when no source graph exists to walk from it — e.g. a binary-only or
    header-only graph) or a ``source_decl``/other node whose own declared
    ``visibility`` is public (``PUBLIC_VISIBILITIES`` — the same set
    :mod:`abicheck.impact.consumer_graph`'s direct-requirement check reads).

    A node's own id is always registered (exact-id lookups can never be
    ambiguous — two distinct nodes never share one id). A node's ``label``
    (when set) is registered only when it resolves *uniquely* across every
    public node in the graph: overloaded C++ entry points routinely share
    one display label, and silently picking an arbitrary one of them would
    hand a manifest's ``entrypoints: [foo]`` a wrong-but-confident answer
    instead of the "degrade to no answer" this module otherwise guarantees
    (:func:`build_use_case_graph`). An id also always wins over a label —
    built as a separate pass, an ambiguous or colliding label can never
    shadow an exact id already registered.
    """
    from ..buildsource.source_graph import PUBLIC_VISIBILITIES

    def is_public(node: GraphNode) -> bool:
        visibility = str((node.resolved or node.attrs).get("visibility", ""))
        return node.kind == "binary_symbol" or visibility in PUBLIC_VISIBILITIES

    public_nodes = [n for n in library_graph.nodes if is_public(n)]

    index: dict[str, str] = {n.id: n.id for n in public_nodes}

    label_targets: dict[str, set[str]] = {}
    for node in public_nodes:
        if node.label:
            label_targets.setdefault(node.label, set()).add(node.id)
    for label, targets in label_targets.items():
        if label in index:
            continue  # an exact id already claims this string; it wins
        if len(targets) == 1:
            index[label] = next(iter(targets))
        # else: label is ambiguous across >1 public node — left unresolved
    return index


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
    alongside its edge (kind copied from the real library node, so
    ``add_node``'s "first registration wins" rule never overrides it) —
    mirroring :func:`abicheck.impact.consumer_graph.build_consumer_graph`'s
    identical pattern for its ``binary_symbol`` targets. Without it, only an
    *edge* would point at the library node and the join
    (:func:`join_use_case_graph`) would never actually deposit a
    ``USE_CASE_PROVENANCE`` fact onto the shared node — the merge (ADR-046
    D2) only happens on a node/edge registration, not implicitly because an
    edge names an id.

    Every ``test_case`` named in ``tests`` gets its own node and a
    ``TEST_COVERS_USE_CASE`` edge onto its use case, unconditionally: unlike
    an entrypoint, a test identifier has no corresponding node kind in the
    library graph to resolve against, so there is nothing to fail to
    resolve.
    """
    from ..buildsource.source_graph import SourceGraphSummary

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
                    confidence=CONF_HIGH,
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
    Deliberately not :meth:`~abicheck.buildsource.source_graph.SourceGraphSummary.finalize`\\ d
    either, for the same transient-graph reason.
    """
    joined = copy.deepcopy(library_graph)
    for node in use_cases.nodes:
        joined.add_node(copy.deepcopy(node))
    for edge in use_cases.edges:
        joined.add_edge(copy.deepcopy(edge))
    return joined
