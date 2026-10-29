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

"""The L5 structural diff matches edges on their full relation identity
(ADR-046 D1 ``relation_key()``) and reports a role change (ADR-077 D4).

Bug class ``identity.edge_matched_on_coarse_key``: before the fix,
``diff_source_graph`` keyed edges on ``(src, dst, kind)``, so a public
declaration that kept a dependency but changed its role (``param`` ->
``return``) diffed empty, and adding a second role was invisible.

The exhaustive domain below enumerates, for every role-bearing edge kind,
every pair of role sets of size 0-2 drawn from that kind's roles plus the
role-less ``""`` (an edge stored before roles were stamped). The oracle is
the raw set of ``relation_key()`` tuples on each side, never the diff's own
grouping helper.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.buildsource.source_graph_compare import diff_source_graph
from abicheck.buildsource.source_graph_findings import diff_source_graph_findings
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import ROLE_COVERAGE_MATRIX
from abicheck.policy.classification import Verdict, compute_verdict
from abicheck.policy.severity import PRESET_DEFAULT, compute_exit_code
from abicheck.policy.source_graph_role_findings import role_change_findings
from tests._abicheck_subprocess import abicheck_command

REPO_DIR = Path(__file__).resolve().parent.parent

_SRC = "decl://api"
_DST = "type://Impl"
_ROLE_CHANGED = ChangeKind.PUBLIC_DEPENDENCY_ROLE_CHANGED
_ROLE_UNKNOWN = ChangeKind.PUBLIC_DEPENDENCY_ROLE_UNKNOWN


def _role_sets(kind: str) -> list[frozenset[str]]:
    """Every role set of size 0-2 over *kind*'s roles plus ``""``."""
    alphabet = ("", *ROLE_COVERAGE_MATRIX[kind])
    return [frozenset(c) for n in range(3) for c in itertools.combinations(alphabet, n)]


_DOMAIN = [
    (kind, old, new)
    for kind in sorted(ROLE_COVERAGE_MATRIX)
    for old in _role_sets(kind)
    for new in _role_sets(kind)
]


def _graph(
    kind: str, roles: frozenset[str], *, visibility: str = "public_header"
) -> SourceGraphSummary:
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id=_SRC, kind="source_decl", label="api", attrs={"visibility": visibility}
        )
    )
    g.add_node(GraphNode(id=_DST, kind="record_type", label="Impl"))
    for role in sorted(roles):
        g.add_edge(
            GraphEdge(
                src=_SRC, dst=_DST, kind=kind, attrs={"role": role} if role else {}
            )
        )
    return g


def _oracle(old: SourceGraphSummary, new: SourceGraphSummary) -> tuple[bool, bool]:
    """``(relation differs, comparable)`` from raw relation keys alone, for
    the one dependency both sides keep."""
    old_rk = {e.relation_key() for e in old.edges}
    new_rk = {e.relation_key() for e in new.edges}
    old_blank = any(rk[3] == "" for rk in old_rk)
    new_blank = any(rk[3] == "" for rk in new_rk)
    return old_rk != new_rk, old_blank == new_blank


@pytest.mark.parametrize(("kind", "old_roles", "new_roles"), _DOMAIN)
def test_role_transition_is_never_dropped(
    kind: str, old_roles: frozenset[str], new_roles: frozenset[str]
) -> None:
    old, new = _graph(kind, old_roles), _graph(kind, new_roles)
    delta = diff_source_graph(old, new)
    findings = role_change_findings(old, new, {})
    kinds = [f.kind for f in findings]

    if not old_roles or not new_roles:
        # The dependency itself entered or left the graph: ADR-046 D1's
        # coarse added/removed sets carry it, and it is no role change.
        assert len(delta.added_edges) + len(delta.removed_edges) == (
            old_roles != new_roles
        )
        assert not delta.changed_edges and not delta.role_unknown_edges
        assert kinds == []
        return

    differs, comparable = _oracle(old, new)
    # Kept dependency: the coarse sets never see it, whatever the roles.
    assert not delta.added_edges and not delta.removed_edges
    if not differs:
        assert not delta.changed and not delta.role_unknown_edges
        assert kinds == []
    elif comparable:
        assert [(c.old_roles, c.new_roles) for c in delta.changed_edges] == [
            (tuple(sorted(old_roles)), tuple(sorted(new_roles)))
        ]
        assert not delta.role_unknown_edges
        assert kinds == [_ROLE_CHANGED]
        assert findings[0].old_value != findings[0].new_value
    else:
        assert not delta.changed_edges and len(delta.role_unknown_edges) == 1
        assert not delta.changed
        assert kinds == [_ROLE_UNKNOWN]


@pytest.mark.parametrize(("kind", "old_roles", "new_roles"), _DOMAIN)
def test_role_findings_are_symmetric(
    kind: str, old_roles: frozenset[str], new_roles: frozenset[str]
) -> None:
    """Swapping the sides swaps old/new values and never changes which
    finding kind fires."""
    old, new = _graph(kind, old_roles), _graph(kind, new_roles)
    forward = role_change_findings(old, new, {})
    backward = role_change_findings(new, old, {})
    assert [f.kind for f in forward] == [f.kind for f in backward]
    for f, b in zip(forward, backward, strict=True):
        assert (f.old_value, f.new_value) == (b.new_value, b.old_value)


@pytest.mark.parametrize(("kind", "old_roles", "new_roles"), _DOMAIN)
def test_private_entry_role_change_is_structural_only(
    kind: str, old_roles: frozenset[str], new_roles: frozenset[str]
) -> None:
    """The structural diff records every role change; only a public entry's
    reaches the report."""
    old = _graph(kind, old_roles, visibility="private_header")
    new = _graph(kind, new_roles, visibility="private_header")
    assert role_change_findings(old, new, {}) == []


@pytest.mark.parametrize("kind", sorted(ROLE_COVERAGE_MATRIX))
@pytest.mark.parametrize("roles_index", range(9))
def test_identical_graphs_diff_empty(kind: str, roles_index: int) -> None:
    """No churn: a graph diffed against itself, or against a copy whose
    edges arrive in reverse order, reports nothing."""
    role_sets = _role_sets(kind)
    roles = role_sets[roles_index % len(role_sets)]
    g = _graph(kind, roles)
    reordered = SourceGraphSummary()
    for n in g.nodes:
        reordered.add_node(GraphNode.from_dict(n.to_dict()))
    for e in reversed(g.edges):
        reordered.add_edge(GraphEdge.from_dict(e.to_dict()))
    for other in (g, reordered):
        delta = diff_source_graph(g, other)
        assert not delta.changed and not delta.role_unknown_edges
        assert diff_source_graph_findings(g, other) == []


def _strip_roles(graph: SourceGraphSummary) -> SourceGraphSummary:
    """A stored graph from before roles were stamped: the same JSON with
    every role removed from attrs, facts and the merged view."""
    payload = graph.to_dict()
    for e in payload["edges"]:
        e["attrs"].pop("role", None)
        e["resolved"].pop("role", None)
        for fact in e.get("facts", []):
            fact.get("attrs", {}).pop("role", None)
    return SourceGraphSummary.from_dict(json.loads(json.dumps(payload)))


@pytest.mark.parametrize("kind", sorted(ROLE_COVERAGE_MATRIX))
@pytest.mark.parametrize("stored_side", ["old", "new"])
def test_role_less_stored_graph_is_a_gap_not_a_change(
    kind: str, stored_side: str
) -> None:
    """Every comparable role on one side against a role-less stored graph on
    the other: a coverage note per kind, never a fabricated role change."""
    current = _graph(kind, frozenset(ROLE_COVERAGE_MATRIX[kind][:2]))
    stored = _strip_roles(current)
    assert {e.role for e in stored.edges} == {""}
    old, new = (stored, current) if stored_side == "old" else (current, stored)
    delta = diff_source_graph(old, new)
    assert not delta.changed_edges and len(delta.role_unknown_edges) == 1
    findings = diff_source_graph_findings(old, new)
    assert [f.kind for f in findings] == [_ROLE_UNKNOWN]


@pytest.mark.parametrize("kind", sorted(ROLE_COVERAGE_MATRIX))
def test_role_one_side_never_examined_is_a_gap(kind: str) -> None:
    """A role the two sides' producers disagree on having examined
    (``untrusted_roles``) cannot have changed as far as evidence shows."""
    first, *rest = ROLE_COVERAGE_MATRIX[kind]
    added = rest[0] if rest else first
    old = _graph(kind, frozenset({first}))
    new = _graph(kind, frozenset({first, added}) if rest else frozenset({""}))
    findings = role_change_findings(old, new, {kind: frozenset({added})})
    assert [f.kind for f in findings] == [_ROLE_UNKNOWN]


def test_callback_slot_discriminator_is_not_a_usage_role() -> None:
    """``CALLBACK_MAY_INVOKE`` stores its slot id under ``role``: a different
    slot is structural evidence, not a usage-role finding."""
    old = SourceGraphSummary()
    new = SourceGraphSummary()
    for g, slot in ((old, "field://S::a"), (new, "field://S::b")):
        g.add_node(
            GraphNode(
                id=_SRC, kind="source_decl", attrs={"visibility": "public_header"}
            )
        )
        g.add_node(GraphNode(id="decl://h", kind="source_decl"))
        g.add_edge(
            GraphEdge(
                src=_SRC,
                dst="decl://h",
                kind="CALLBACK_MAY_INVOKE",
                attrs={"role": slot},
            )
        )
    assert len(diff_source_graph(old, new).changed_edges) == 1
    assert role_change_findings(old, new, {}) == []


@pytest.mark.parametrize(
    ("old_roles", "new_roles", "kind", "verdict"),
    [
        ({"param"}, {"return"}, _ROLE_CHANGED, Verdict.COMPATIBLE_WITH_RISK),
        ({"param"}, {"param", "return"}, _ROLE_CHANGED, Verdict.COMPATIBLE_WITH_RISK),
        ({""}, {"param"}, _ROLE_UNKNOWN, Verdict.COMPATIBLE),
        ({"return"}, {""}, _ROLE_UNKNOWN, Verdict.COMPATIBLE),
    ],
)
def test_role_finding_alone_never_gates(
    old_roles: set[str], new_roles: set[str], kind: ChangeKind, verdict: Verdict
) -> None:
    """Each new finding as the only change: its verdict comes from the
    catalog, and under the default severity config it never fails the run
    (exit 0) -- it explains, it does not decide."""
    old = _graph("DECL_HAS_TYPE", frozenset(old_roles))
    new = _graph("DECL_HAS_TYPE", frozenset(new_roles))
    findings = diff_source_graph_findings(old, new)
    assert [f.kind for f in findings] == [kind]
    assert compute_verdict(findings) == verdict
    assert compute_exit_code(findings, PRESET_DEFAULT) == 0


# ── Public workflow: dump + compare through the CLI ─────────────────────

#: (case id, v1 header body, v2 header body, expected role finding values or
#: None for no role finding). Four independent declarations of the same
#: public function over the same struct.
_CLI_CASES = [
    (
        "param_to_return",
        "void api(struct Impl *p);",
        "struct Impl *api(void);",
        ("param", "return"),
    ),
    (
        "return_to_param",
        "struct Impl *api(void);",
        "void api(struct Impl *p);",
        ("return", "param"),
    ),
    (
        "second_role_added",
        "void api(struct Impl *p);",
        "struct Impl *api(struct Impl *p);",
        ("param", "param, return"),
    ),
    ("unchanged", "void api(struct Impl *p);", "void api(struct Impl *p);", None),
]


def _c_definition(header: str) -> str:
    if header.startswith("void"):
        return "void api(struct Impl *p) { (void)p; }\n"
    params = "struct Impl *p" if "(struct Impl *p)" in header else "void"
    unused = "(void)p; " if params != "void" else ""
    return (
        f"static struct Impl g;\nstruct Impl *api({params}) {{ {unused}return &g; }}\n"
    )


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so pairs")
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required")
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml required")
@pytest.mark.skipif(
    shutil.which("clang") is None,
    reason="clang required for the header-only type-graph pass",
)
@pytest.mark.parametrize(
    ("case", "v1", "v2", "expected"), _CLI_CASES, ids=[c[0] for c in _CLI_CASES]
)
def test_cli_compare_reports_public_role_change(
    case: str, v1: str, v2: str, expected: tuple[str, str] | None, tmp_path: Path
) -> None:
    dumps = []
    for side, decl in (("v1", v1), ("v2", v2)):
        side_dir = tmp_path / side
        side_dir.mkdir()
        header = side_dir / "api.h"
        header.write_text(f"struct Impl {{ int x; }};\n{decl}\n")
        source = side_dir / "api.c"
        source.write_text(f'#include "api.h"\n{_c_definition(decl)}')
        lib = side_dir / "libapi.so"
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-g", str(source), "-o", str(lib)],
            check=True,
            capture_output=True,
        )
        out = tmp_path / f"{side}.json"
        dump = subprocess.run(
            abicheck_command(
                "dump",
                str(lib),
                "--header",
                str(header),
                "-o",
                str(out),
            ),
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
        )
        assert dump.returncode == 0, dump.stderr
        dumps.append(out)

    report = tmp_path / "report.json"
    result = subprocess.run(
        abicheck_command(
            "compare",
            *map(str, dumps),
            "-o",
            f"json={report}",
        ),
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    assert report.is_file(), result.stderr
    payload = json.loads(report.read_text())
    # The role finding explains; the artifact diff decides. A changed case is
    # BREAKING / exit 4 from its signature findings, the unchanged one is clean.
    assert (payload["verdict"], result.returncode) == (
        ("BREAKING", 4) if expected else ("NO_CHANGE", 0)
    ), (case, payload["verdict"], result.returncode)
    changes = payload["changes"]
    role = [
        (c["old_value"], c["new_value"])
        for c in changes
        if c["kind"] == _ROLE_CHANGED.value
    ]
    assert not [c for c in changes if c["kind"] == _ROLE_UNKNOWN.value]
    assert role == ([expected] if expected else []), (case, changes)


def test_diff_to_dict_carries_changed_and_unknown_relations() -> None:
    """The serialized diff reports both new lists and their counts."""
    old = _graph("DECL_HAS_TYPE", frozenset({"param"}))
    new = _graph("DECL_HAS_TYPE", frozenset({"return"}))
    payload = diff_source_graph(old, new).to_dict()
    assert payload["changed_edges"] == [
        {
            "edge": "DECL_HAS_TYPE",
            "src": _SRC,
            "dst": _DST,
            "old_roles": ["param"],
            "new_roles": ["return"],
        }
    ]
    assert payload["counts"]["changed_edges"] == 1
    assert payload["counts"]["role_unknown_edges"] == 0
    stored = diff_source_graph(_strip_roles(old), new).to_dict()
    assert stored["changed_edges"] == []
    assert stored["role_unknown_edges"][0]["old_roles"] == [""]
    assert stored["counts"]["role_unknown_edges"] == 1


@pytest.mark.parametrize("dependencies", [1, 3, 4, 7])
def test_role_gap_note_is_one_per_kind_and_counts_every_dependency(
    dependencies: int,
) -> None:
    """Many role-less dependencies against a role-stamped graph: one note per
    edge kind, naming the total and at most three examples."""

    def build(role: str) -> SourceGraphSummary:
        g = SourceGraphSummary()
        g.add_node(
            GraphNode(
                id=_SRC, kind="source_decl", attrs={"visibility": "public_header"}
            )
        )
        for i in range(dependencies):
            g.add_node(GraphNode(id=f"type://T{i}", kind="record_type", label=f"T{i}"))
            g.add_edge(
                GraphEdge(
                    src=_SRC,
                    dst=f"type://T{i}",
                    kind="DECL_HAS_TYPE",
                    attrs={"role": role} if role else {},
                )
            )
        return g

    findings = role_change_findings(build(""), build("param"), {})
    assert [f.kind for f in findings] == [_ROLE_UNKNOWN]
    text = findings[0].description
    assert text.startswith(f"{dependencies} public DECL_HAS_TYPE dependenc")
    assert text.count(" -> T") == min(dependencies, 3)
    assert ("more" in text) == (dependencies > 3)
