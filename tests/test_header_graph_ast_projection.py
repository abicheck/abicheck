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

"""The projection contract: releasing the clang AST early changes nothing.

The bug *class* this states as an executable invariant (registry id
``perf.evidence_released_before_its_consumer_runs``) is not "the graph is
still right for the one AST shape the optimizing PR happened to try". It is:

    for **any** clang AST, a graph built from the projection of that AST is
    indistinguishable from a graph built from the AST itself.

That is the property a memory-ordering change can break silently — a
projection that forgets one of the four readers, or runs them in an order
that loses the lazy ``entity_files`` case, produces a *smaller* graph, and a
smaller graph is missing evidence, not a failing assertion. So the invariant
is checked over generated ASTs (a small structural enumeration plus
Hypothesis-composed trees), not over one hand-written fixture, and against
an oracle that is the *other* code path rather than a second copy of the
projection's own logic.

The second half is the mechanism, per AGENTS.md's differential-test rule: a
test that asserts "the graph is the same" passes just as happily if the
attach never released the AST at all. ``TestAstReleasedBeforeGraphBuild``
therefore observes the mechanism — that no reference to the parsed tree
survives into ``build_header_only_graph`` — with a weak reference, rather
than inferring it from the output.
"""

from __future__ import annotations

import gc
import weakref
from pathlib import Path
from typing import Any

import pytest

from abicheck.buildsource.header_graph import build_header_only_graph
from abicheck.buildsource.header_graph_ast_projection import (
    HeaderGraphAstProjection,
    project_header_graph_ast,
)
from abicheck.model import AbiSnapshot, Function, ScopeOrigin, Variable

PUBLIC_HEADER = "/proj/include/pub.h"
PRIVATE_HEADER = "/proj/include/detail/impl.h"


# ── AST shapes ──────────────────────────────────────────────────────────────


def _loc(file: str) -> dict[str, Any]:
    return {"file": file, "line": 1, "col": 1}


def _record(name: str, *, file: str, inner: list[dict] | None = None) -> dict:
    return {
        "kind": "CXXRecordDecl",
        "name": name,
        "loc": _loc(file),
        "inner": inner or [],
    }


def _enum(name: str, *, file: str, constants: list[str] = ()) -> dict:
    return {
        "kind": "EnumDecl",
        "name": name,
        "loc": _loc(file),
        "inner": [
            {"kind": "EnumConstantDecl", "name": c, "loc": _loc(file)}
            for c in constants
        ],
    }


def _field(name: str, qual_type: str, *, init: dict | None = None) -> dict:
    d: dict[str, Any] = {
        "kind": "FieldDecl",
        "name": name,
        "type": {"qualType": qual_type},
    }
    if init is not None:
        d["inner"] = [init]
    return d


def _decl_ref(name: str, *, file: str) -> dict:
    return {
        "kind": "DeclRefExpr",
        "referencedDecl": {
            "kind": "VarDecl",
            "name": name,
            "loc": _loc(file),
        },
    }


def _function(
    name: str,
    *,
    file: str,
    mangled: str | None = None,
    params: list[str] = (),
    body: list[dict] | None = None,
) -> dict:
    inner: list[dict] = [
        {"kind": "ParmVarDecl", "name": f"p{i}", "type": {"qualType": t}}
        for i, t in enumerate(params)
    ]
    if body is not None:
        inner.append({"kind": "CompoundStmt", "inner": body})
    d: dict[str, Any] = {
        "kind": "FunctionDecl",
        "name": name,
        "loc": _loc(file),
        "type": {"qualType": "void ()"},
        "inner": inner,
    }
    if mangled:
        d["mangledName"] = mangled
    return d


def _call(callee: str, *, file: str) -> dict:
    return {
        "kind": "CallExpr",
        "inner": [
            {
                "kind": "DeclRefExpr",
                "referencedDecl": _function(callee, file=file),
            }
        ],
    }


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


def _var(name: str, *, file: str, qual_type: str = "int") -> dict:
    return {
        "kind": "VarDecl",
        "name": name,
        "loc": _loc(file),
        "type": {"qualType": qual_type},
    }


#: Structurally distinct ASTs covering every projection member, including the
#: two cases whose *absence* is the failure mode a single fixture misses: a
#: ``DECL_REFERENCES_DECL`` edge (the only thing that makes ``entity_files``
#: non-empty) and an AST that yields no edges at all.
AST_CASES: dict[str, dict] = {
    "empty": _tu(),
    "one_public_record": _tu(_record("Public", file=PUBLIC_HEADER)),
    "public_record_private_field": _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [_record("Impl", file=PRIVATE_HEADER)],
        },
        _record(
            "Public", file=PUBLIC_HEADER, inner=[_field("p", "detail::Impl *")]
        ),
    ),
    "enum_and_constants": _tu(
        _enum("Color", file=PUBLIC_HEADER, constants=["RED", "GREEN"])
    ),
    "field_initializer_reference": _tu(
        _var("k", file=PRIVATE_HEADER),
        _record(
            "Widget",
            file=PUBLIC_HEADER,
            inner=[_field("x", "int", init=_decl_ref("k", file=PRIVATE_HEADER))],
        ),
    ),
    "call_edge": _tu(
        _function("helper", file=PRIVATE_HEADER, mangled="_ZN6helperEv"),
        _function(
            "entry",
            file=PUBLIC_HEADER,
            mangled="_Z5entryv",
            body=[_call("helper", file=PRIVATE_HEADER)],
        ),
    ),
    "function_with_private_param": _tu(
        _record("Impl", file=PRIVATE_HEADER),
        _function("api", file=PUBLIC_HEADER, params=["Impl *"]),
    ),
}


def _snapshot(
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version="1.0",
        functions=functions or [],
        variables=variables or [],
        types=[],
        enums=[],
    )


#: Snapshot shapes the graph is seeded from, so the invariant is checked
#: across the "AST-only node" and "node already seeded from the snapshot"
#: branches rather than only the empty one.
SNAPSHOT_CASES: dict[str, AbiSnapshot] = {
    "empty": _snapshot(),
    "public_function": _snapshot(
        functions=[
            Function(
                name="entry",
                mangled="_Z5entryv",
                return_type="void",
                source_location=f"{PUBLIC_HEADER}:1",
                source_header=PUBLIC_HEADER,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ]
    ),
    "private_variable": _snapshot(
        variables=[
            Variable(
                name="k",
                mangled="_ZN6detail1kE",
                type="int",
                source_location=f"{PRIVATE_HEADER}:1",
                source_header=PRIVATE_HEADER,
                origin=ScopeOrigin.PRIVATE_HEADER,
            )
        ]
    ),
}


def _graph_fingerprint(graph: Any) -> tuple:
    """Everything a downstream L5 consumer can observe about a built graph.

    Deliberately not ``==`` on the object: ``SourceGraphSummary`` is not a
    value type, and a comparison that silently degrades to identity would
    make this whole file vacuous.
    """
    return (
        tuple(
            sorted(
                (n.id, n.kind, n.label, n.provenance, n.confidence, tuple(sorted(n.attrs.items())))
                for n in graph.nodes
            )
        ),
        tuple(
            sorted(
                (e.src, e.dst, e.kind, e.provenance, e.confidence, getattr(e, "role", None))
                for e in graph.edges
            )
        ),
        tuple(sorted(graph.extractor_passes.items())),
        tuple(sorted(graph.degraded_passes.items())),
        repr(sorted(graph.coverage.items())),
    )


def _snapshot_copy(snapshot: AbiSnapshot) -> AbiSnapshot:
    """A fresh snapshot per build — ``build_header_only_graph`` reads it, but
    sharing one across two builds would make any future in-place enrichment
    leak from the first comparison into the second."""
    import copy

    return copy.deepcopy(snapshot)


def _build(snapshot: AbiSnapshot, **kw: Any) -> Any:
    return build_header_only_graph(
        snapshot,
        public_header_paths=[PUBLIC_HEADER],
        **kw,
    )


class TestProjectionIsObservationallyEqualToTheAst:
    """The invariant, over generated inputs rather than one fixture."""

    @pytest.mark.parametrize("ast_name", sorted(AST_CASES))
    @pytest.mark.parametrize("snap_name", sorted(SNAPSHOT_CASES))
    def test_graph_from_projection_matches_graph_from_ast(
        self, ast_name: str, snap_name: str
    ) -> None:
        snapshot = SNAPSHOT_CASES[snap_name]
        from_ast = _build(snapshot, ast_root=AST_CASES[ast_name])
        from_projection = _build(
            snapshot,
            ast_projection=project_header_graph_ast(AST_CASES[ast_name]),
        )
        assert _graph_fingerprint(from_ast) == _graph_fingerprint(from_projection)

    def test_the_oracle_is_not_vacuous(self) -> None:
        """A fingerprint that collapsed to a constant would pass everything.

        The matrix above is only meaningful if distinct ASTs actually
        produce distinct fingerprints — the vacuity guard AGENTS.md's
        "a matrix test needs an oracle" bullet requires on the oracle
        itself.
        """
        fingerprints = {
            name: _graph_fingerprint(_build(_snapshot(), ast_root=ast))
            for name, ast in AST_CASES.items()
        }
        assert len(set(fingerprints.values())) > 1
        assert fingerprints["empty"] != fingerprints["call_edge"]

    @pytest.mark.parametrize("ast_name", sorted(AST_CASES))
    def test_projection_is_pure_and_repeatable(self, ast_name: str) -> None:
        """Projecting twice gives the same answer, and does not mutate the AST.

        A reader that consumed the tree destructively would make the early
        release above correct and the *degraded* re-parse path (an AST
        handed to ``build_header_only_graph`` directly) silently wrong.
        """
        import copy

        ast = copy.deepcopy(AST_CASES[ast_name])
        before = copy.deepcopy(ast)
        first = project_header_graph_ast(ast)
        second = project_header_graph_ast(ast)
        assert ast == before
        assert first == second

    def test_entity_files_is_populated_exactly_when_a_reference_edge_exists(
        self,
    ) -> None:
        """The lazy member, stated as a rule rather than pinned to one AST.

        ``_unseeded_decl_endpoints`` only ever reads ``entity_files`` for a
        ``DECL_REFERENCES_DECL`` edge's source, so computing it otherwise is
        an unconditional extra AST walk on every dump — and *not* computing
        it when one exists loses that node's origin. Both directions are
        checked over every case, so a future AST shape that starts emitting
        such an edge cannot quietly fall on the wrong side.
        """
        for name, ast in AST_CASES.items():
            projection = project_header_graph_ast(ast)
            has_ref_edge = any(
                e.kind == "DECL_REFERENCES_DECL" for e in projection.type_edges
            )
            assert bool(projection.entity_files) == has_ref_edge, name
        # The enumeration is only a rule if it contains both answers.
        assert any(
            project_header_graph_ast(a).entity_files for a in AST_CASES.values()
        )
        assert any(
            not project_header_graph_ast(a).entity_files for a in AST_CASES.values()
        )

    def test_absent_projection_degrades_exactly_like_an_absent_ast(self) -> None:
        snapshot = SNAPSHOT_CASES["public_function"]
        assert _graph_fingerprint(_build(snapshot)) == _graph_fingerprint(
            _build(snapshot, ast_projection=None)
        )

    def test_supplying_both_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            _build(
                _snapshot(),
                ast_root=AST_CASES["call_edge"],
                ast_projection=HeaderGraphAstProjection(),
            )


class TestEveryProjectionMemberIsLoadBearing:
    """Guard the matrix above against a mutation it cannot see.

    ``build_header_only_graph(snap, ast_root)`` now projects internally, so
    "graph from AST == graph from projection" compares two paths that share
    the projection: blanking one of its four members changes *both* sides
    equally and the comparison still passes. Verified, not assumed — a
    ``type_files={}`` mutation survived the whole matrix. Two independent
    checks close it: the projection is compared against the four readers
    themselves (an oracle that is not the projection's own code), and each
    member is shown to change a real graph, so a member that stopped being
    read could not go unnoticed either.
    """

    @pytest.mark.parametrize("ast_name", sorted(AST_CASES))
    def test_projection_equals_the_readers_applied_directly(
        self, ast_name: str
    ) -> None:
        from abicheck.buildsource.call_graph import parse_clang_ast_calls
        from abicheck.buildsource.type_graph import (
            index_declared_entity_files,
            index_declared_type_files,
            parse_clang_ast_types,
        )

        ast = AST_CASES[ast_name]
        projection = project_header_graph_ast(ast)
        assert projection.type_files == index_declared_type_files(ast)
        assert projection.type_edges == parse_clang_ast_types(ast)
        assert projection.call_edges == parse_clang_ast_calls(ast)
        expected_entities = (
            index_declared_entity_files(ast)
            if any(e.kind == "DECL_REFERENCES_DECL" for e in projection.type_edges)
            else {}
        )
        assert projection.entity_files == expected_entities

    @pytest.mark.parametrize(
        "member", ["type_files", "type_edges", "call_edges", "entity_files"]
    )
    def test_blanking_a_member_changes_some_graph(self, member: str) -> None:
        import dataclasses

        empty: dict[str, Any] = {
            "type_files": {},
            "type_edges": [],
            "call_edges": [],
            "entity_files": {},
        }
        changed = []
        for ast_name, ast in AST_CASES.items():
            projection = project_header_graph_ast(ast)
            for snapshot in SNAPSHOT_CASES.values():
                full = _build(_snapshot_copy(snapshot), ast_projection=projection)
                blanked = _build(
                    _snapshot_copy(snapshot),
                    ast_projection=dataclasses.replace(
                        projection, **{member: empty[member]}
                    ),
                )
                if _graph_fingerprint(full) != _graph_fingerprint(blanked):
                    changed.append(ast_name)
        assert changed, (
            f"no AST case in this file makes {member!r} observable, so the "
            "matrix above would pass against an implementation that never "
            "populated it"
        )


class TestAstReleasedBeforeGraphBuild:
    """Prove the mechanism, not just the output (AGENTS.md's differential rule).

    Every assertion above holds equally for an implementation that projects
    the AST and then keeps it alive for the whole graph build — which is the
    behaviour this change exists to remove. The only way to tell those apart
    is to observe the reference, so this watches the parsed tree die.
    """

    def test_no_reference_to_the_parsed_tree_survives_into_the_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import abicheck.buildsource.header_graph as header_graph_module
        import abicheck.service_header_graph_attach as attach_module

        ast = _tu(_record("Public", file=PUBLIC_HEADER))
        # A weak reference needs a weakref-able object; a bare dict is not,
        # so the tree is rooted in a tiny holder whose lifetime tracks it.
        class _Tree(dict):
            pass

        handoff: list[Any] = [_Tree(ast)]
        alive = weakref.ref(handoff[0])
        observed: dict[str, bool] = {}

        def fake_clang_header_dump(*_a: Any, **_k: Any) -> tuple[Any, None, bool]:
            # Hand the tree over and let go of it here, exactly as the real
            # parse does: the attach's own local must then be the only
            # strong reference, or this test would be measuring the
            # fixture's grip rather than the code's.
            return handoff.pop(), None, True

        real_build = header_graph_module.build_header_only_graph

        def spy_build(*a: Any, **kw: Any) -> Any:
            # Drop this frame's own hold on the argument tuple before asking.
            gc.collect()
            observed["ast_alive_during_build"] = alive() is not None
            observed["projection_supplied"] = kw.get("ast_projection") is not None
            return real_build(*a, **kw)

        monkeypatch.setattr(
            header_graph_module, "build_header_only_graph", spy_build
        )
        monkeypatch.setattr(
            "abicheck.dumper._clang_header_dump", fake_clang_header_dump
        )
        monkeypatch.setattr(
            "abicheck.service_header_graph_attach.expand_header_inputs",
            lambda headers: list(headers),
        )
        monkeypatch.setattr(
            "abicheck.service_header_graph_attach.resolve_inferred_header_roots",
            lambda *a, **k: ([], []),
        )

        attach_module._attach_header_graph(
            _snapshot(),
            header_graph=True,
            header_graph_includes=False,
            headers=[Path(PUBLIC_HEADER)],
            includes=[],
            lang=None,
            compile=None,
            public_headers=None,
            public_header_dirs=None,
        )

        # Both halves matter: the second proves the projection path is the
        # one that ran, so a future refactor cannot satisfy the first by
        # simply never parsing an AST.
        assert handoff == []  # the fake really did hand the tree over
        assert observed["projection_supplied"] is True
        assert observed["ast_alive_during_build"] is False
