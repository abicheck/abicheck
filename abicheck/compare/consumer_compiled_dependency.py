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

"""``PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED`` (G29 Phase 6), header-graph
tier: the declarations a public inline or template body calls or references
changed between two versions.

The producer is the header-only (L2) semantic graph
(:mod:`abicheck.buildsource.header_graph`). It parses every body written in a
public header in full, marks that declaration ``header_body``, and records its
``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` edges. Such a body is compiled into
every translation unit that includes the header, so a consumer must recompile
to pick up a change in what it depends on. This module compares, per public
declaration whose body is header-written on both sides, the set of its
dependencies old vs new.

It lives in ``compare/`` because it is a pure old-vs-new comparison over two
summaries and needs only the model layer and the header pass's own flags.

Conservative by construction:

* **RISK, never a break.** Without consumer build evidence nothing says which
  consumers include the header, so the finding stays
  ``COMPATIBLE_WITH_RISK``. A ``BREAKING`` verdict would need that evidence,
  which no source yet provides.
* **No consumer inference.** Whether a consumer uses the changed dependency is
  never guessed from a symbol name. The finding says the body is compiled into
  consumers; it does not say any particular consumer depends on the change.
* **Set semantics.** Dependencies are compared as frozensets, so a reordered
  but otherwise identical set is no change.
* **Missing evidence yields nothing.** If the header call pass did not run to
  completion on either side (absent, narrowed or degraded), no finding is
  emitted. Nothing is claimed clean either: silence means "not established".
* **Public on both sides only.** A body behind a private header is not part of
  the consumer surface and is skipped.
* **Header-written bodies only.** An ordinary out-of-line definition's body is
  never in a header, so its dependencies are not in this graph at all and it
  is skipped. The build-integrated (L5) graph is owned by the internal-
  dependency detector and is not re-reported here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.source_graph_coverage import HEADER_BODY_ATTR, HEADER_CALL_GRAPH_PASS
from ..model.source_graph_query import is_public_dependency_node

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..model.source_graph import SourceGraphSummary

_DEPENDENCY_KINDS = frozenset({"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"})

#: The boundary a finding is attributed to. Header-only evidence, so not L5.
_BOUNDARY = "[L2_HEADER_GRAPH]"

#: How many example dependencies a description names per direction before "+N more".
_EXAMPLE_LIMIT = 2


def _header_pass_complete(graph: SourceGraphSummary) -> bool:
    """Whether the header-only call pass ran to completion on *graph*.

    A narrowed or degraded header pass has not parsed every header body, so
    its absence of a dependency is not proof of absence.
    """
    return (
        graph.extractor_passes.get(HEADER_CALL_GRAPH_PASS, False)
        and not graph.narrowed_passes.get(HEADER_CALL_GRAPH_PASS, False)
        and not graph.degraded_passes.get(HEADER_CALL_GRAPH_PASS, False)
    )


def _compiled_dependencies(graph: SourceGraphSummary) -> dict[str, frozenset[str]]:
    """``public header-written body id -> the declarations it depends on``.

    A public body with no dependency maps to the empty set: that is an observed
    "calls nothing" fact, not a missing entry, and it is what lets a body that
    *gains* its first dependency register as a change.
    """
    node_by_id = {n.id: n for n in graph.nodes}
    exported = {e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"}
    header_bodies = {n.id for n in graph.nodes if n.attrs.get(HEADER_BODY_ATTR) is True}
    dependencies: dict[str, set[str]] = {}
    for e in graph.edges:
        if e.kind in _DEPENDENCY_KINDS:
            src = graph.resolve_node_id(e.src)
            dependencies.setdefault(src, set()).add(graph.resolve_node_id(e.dst))
    return {
        body: frozenset(dependencies.get(body, ()))
        for body in header_bodies
        if is_public_dependency_node(body, node_by_id, exported)
    }


def _names(targets: Sequence[str], labels: dict[str, str], prefix: str) -> str:
    if not targets:
        return ""
    shown = ", ".join(labels.get(t, t) for t in targets[:_EXAMPLE_LIMIT])
    more = len(targets) - _EXAMPLE_LIMIT
    extra = f" (+{more} more)" if more > 0 else ""
    return f" {prefix} {shown}{extra}."


def consumer_compiled_dependency_findings(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> list[Change]:
    """``PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED`` findings between two graphs.

    Returns ``[]`` unless the header call pass completed on both sides.
    """
    if not (_header_pass_complete(old) and _header_pass_complete(new)):
        return []

    old_deps = _compiled_dependencies(old)
    new_deps = _compiled_dependencies(new)
    # A removed dependency exists only on the old side, and an added one only on
    # the new side: name each from whichever graph declares it.
    labels = {n.id: (n.label or n.id) for n in old.nodes}
    labels.update({n.id: (n.label or n.id) for n in new.nodes})

    findings: list[Change] = []
    for body in sorted(old_deps.keys() & new_deps.keys()):
        before, after = old_deps[body], new_deps[body]
        if before == after:
            continue
        label = labels.get(body, body)
        description = (
            f"The inline or template body of public declaration {label!r} is "
            "compiled into every consumer that includes its header, and the set of "
            f"declarations it calls or references changed ({len(before)} → "
            f"{len(after)}). Consumers must recompile to pick up the new body. "
            "This is header-only source-graph risk, not an artifact-proven ABI break: "
            "no consumer build evidence was consulted, so which consumers include "
            "this header is unknown."
            + _names(sorted(after - before), labels, "Added:")
            + _names(sorted(before - after), labels, "Removed:")
        )
        findings.append(
            Change(
                kind=ChangeKind.PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED,
                symbol=label,
                description=description,
                old_value=f"{len(before)} compiled-in dependencies",
                new_value=f"{len(after)} compiled-in dependencies",
                source_location=_BOUNDARY,
            )
        )
    return findings
