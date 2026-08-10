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

"""Tests for G29 Phase 5 item 4: the callback/function-pointer graph
(``DECL_REGISTERS_CALLBACK``/``DECL_TAKES_ADDRESS_OF``/``CALLBACK_MAY_INVOKE``).

Part B's pure AST parser is exercised against hand-built
``clang -ast-dump=json`` trees (no compiler required), mirroring
``test_type_graph.py``'s/``test_macro_graph.py``'s established fixture style.
Part A's join is a pure graph transformation over a hand-built
``SourceGraphSummary``, mirroring ``test_virtual_dispatch_graph.py``. Live
clang extraction and the full fold pipeline are exercised only under
``@pytest.mark.integration``.
"""

from __future__ import annotations

import shutil

import pytest

from abicheck.buildsource.callback_graph import (
    EDGE_CALLBACK_MAY_INVOKE,
    EDGE_DECL_REGISTERS_CALLBACK,
    EDGE_DECL_TAKES_ADDRESS_OF,
    CallbackDispatchResult,
    CallbackEdge,
    ClangCallbackGraphExtractor,
    augment_graph_with_callback_invocations,
    augment_graph_with_callback_registrations,
    parse_clang_ast_callbacks,
)
from abicheck.buildsource.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
)
from abicheck.buildsource.inline_graph_fold import fold_call_graph, fold_callback_graph
from abicheck.buildsource.source_graph import SourceGraphSummary

# ── Part B fixtures: hand-built clang AST node shapes ───────────────────────


def _func_decl(
    name: str,
    node_id: str,
    params: list[dict] | None = None,
    *,
    qual_type: str = "void ()",
    mangled: str | None = None,
    extra_inner: list[dict] | None = None,
) -> dict:
    d: dict = {
        "kind": "FunctionDecl",
        "id": node_id,
        "name": name,
        "type": {"qualType": qual_type},
        "inner": [*(params or []), *(extra_inner or [])],
    }
    if mangled:
        d["mangledName"] = mangled
    return d


def _param(name: str, qual_type: str) -> dict:
    return {"kind": "ParmVarDecl", "name": name, "type": {"qualType": qual_type}}


def _ref(func_node: dict) -> dict:
    """A ``DeclRefExpr`` naming *func_node* the same way real clang emits a
    call-target/address-of reference stub (Codex-verified shape): no
    ``mangledName`` on the stub itself, so resolution goes through the
    id-index built from the full declaration."""
    return {
        "kind": "DeclRefExpr",
        "referencedDecl": {
            "id": func_node["id"],
            "kind": func_node["kind"],
            "name": func_node["name"],
            "type": func_node["type"],
        },
    }


def _addr_of(func_node: dict) -> dict:
    return {"kind": "UnaryOperator", "opcode": "&", "inner": [_ref(func_node)]}


def _decay(func_node: dict) -> dict:
    return {
        "kind": "ImplicitCastExpr",
        "castKind": "FunctionToPointerDecay",
        "inner": [_ref(func_node)],
    }


def _call(callee_func_node: dict, args: list[dict]) -> dict:
    return {"kind": "CallExpr", "inner": [_decay(callee_func_node), *args]}


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


# ── Part B: parse_clang_ast_callbacks ───────────────────────────────────────


class TestParseCallbacksRegistersCallback:
    def test_explicit_address_of_in_call_registers_callback(self) -> None:
        my_handler = _func_decl(
            "my_handler", "F1", [_param("x", "int")], mangled="_Z10my_handleri"
        )
        signal_register = _func_decl(
            "signal_register", "F2", [_param("h", "void (*)(int)")]
        )
        call = _call(signal_register, [_addr_of(my_handler)])
        use = _func_decl(
            "use", "F3", extra_inner=[{"kind": "CompoundStmt", "inner": [call]}]
        )
        ast = _tu(my_handler, signal_register, use)

        edges = parse_clang_ast_callbacks(ast)

        assert edges == [
            CallbackEdge(
                "_Z10my_handleri",
                "h",
                EDGE_DECL_REGISTERS_CALLBACK,
                CONF_HIGH,
                "void (*)(int)",
            )
        ]

    def test_implicit_decay_bare_name_registers_callback(self) -> None:
        other_handler = _func_decl(
            "other_handler", "F1", [_param("x", "int")], mangled="_Z13other_handleri"
        )
        signal_register = _func_decl(
            "signal_register", "F2", [_param("h", "void (*)(int)")]
        )
        call = _call(signal_register, [_decay(other_handler)])
        use = _func_decl(
            "use", "F3", extra_inner=[{"kind": "CompoundStmt", "inner": [call]}]
        )
        ast = _tu(other_handler, signal_register, use)

        edges = parse_clang_ast_callbacks(ast)

        assert edges == [
            CallbackEdge(
                "_Z13other_handleri",
                "h",
                EDGE_DECL_REGISTERS_CALLBACK,
                CONF_HIGH,
                "void (*)(int)",
            )
        ]

    def test_argument_at_a_non_function_pointer_parameter_is_not_registered(
        self,
    ) -> None:
        """An address-of expression landing on a parameter whose own type is
        not function-pointer-shaped (e.g. an ordinary ``void *``) must not be
        recorded as a registration -- the type-shape check is what filters
        an unrelated "pass this function's address as an opaque cookie" call
        from a genuine callback registration."""
        handler = _func_decl("handler", "F1", mangled="_Z7handleri")
        take_cookie = _func_decl("take_cookie", "F2", [_param("cookie", "void *")])
        call = _call(take_cookie, [_addr_of(handler)])
        use = _func_decl(
            "use", "F3", extra_inner=[{"kind": "CompoundStmt", "inner": [call]}]
        )
        ast = _tu(handler, take_cookie, use)

        assert parse_clang_ast_callbacks(ast) == []

    def test_address_of_a_non_call_expression_is_not_registration(self) -> None:
        """An address-of that flows into a call argument position but whose
        target is not itself a FunctionDecl (e.g. taking the address of a
        variable) is simply not resolved -- no crash, no edge."""
        var_ref = {
            "kind": "UnaryOperator",
            "opcode": "&",
            "inner": [
                {
                    "kind": "DeclRefExpr",
                    "referencedDecl": {
                        "id": "V1",
                        "kind": "VarDecl",
                        "name": "g",
                        "type": {"qualType": "int"},
                    },
                }
            ],
        }
        take_cb = _func_decl("take_cb", "F2", [_param("h", "void (*)(int)")])
        call = _call(take_cb, [var_ref])
        use = _func_decl(
            "use", "F3", extra_inner=[{"kind": "CompoundStmt", "inner": [call]}]
        )
        ast = _tu(take_cb, use)

        assert parse_clang_ast_callbacks(ast) == []


class TestParseCallbacksTakesAddressOf:
    def test_field_assignment_records_takes_address_of(self) -> None:
        my_handler = _func_decl("my_handler", "F1", mangled="_Z10my_handleri")
        member_expr = {
            "kind": "MemberExpr",
            "name": "cb",
            "referencedMemberDecl": "FLD1",
            "type": {"qualType": "handler_t", "desugaredQualType": "void (*)(int)"},
            "inner": [
                {
                    "kind": "DeclRefExpr",
                    "referencedDecl": {
                        "id": "P1",
                        "kind": "ParmVarDecl",
                        "name": "w",
                        "type": {"qualType": "Widget *"},
                    },
                }
            ],
        }
        widget = {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "inner": [
                {
                    "kind": "FieldDecl",
                    "id": "FLD1",
                    "name": "cb",
                    "type": {
                        "qualType": "handler_t",
                        "desugaredQualType": "void (*)(int)",
                    },
                }
            ],
        }
        assign = {
            "kind": "BinaryOperator",
            "opcode": "=",
            "inner": [member_expr, _addr_of(my_handler)],
        }
        assign_field = _func_decl(
            "assign_field",
            "F2",
            [
                {
                    "kind": "ParmVarDecl",
                    "id": "P1",
                    "name": "w",
                    "type": {"qualType": "Widget *"},
                }
            ],
            qual_type="void (Widget *)",
            extra_inner=[{"kind": "CompoundStmt", "inner": [assign]}],
        )
        ast = _tu(my_handler, widget, assign_field)

        edges = parse_clang_ast_callbacks(ast)

        assert edges == [
            CallbackEdge(
                "_Z10my_handleri",
                "cb",
                EDGE_DECL_TAKES_ADDRESS_OF,
                CONF_REDUCED,
                "void (*)(int)",
            )
        ]

    def test_var_decl_initializer_records_takes_address_of(self) -> None:
        other_handler = _func_decl("other_handler", "F1", mangled="_Z13other_handleri")
        global_cb = {
            "kind": "VarDecl",
            "name": "global_cb",
            "type": {"qualType": "handler_t", "desugaredQualType": "void (*)(int)"},
            "inner": [_decay(other_handler)],
        }
        ast = _tu(other_handler, global_cb)

        edges = parse_clang_ast_callbacks(ast)

        assert edges == [
            CallbackEdge(
                "_Z13other_handleri",
                "global_cb",
                EDGE_DECL_TAKES_ADDRESS_OF,
                CONF_REDUCED,
                "void (*)(int)",
            )
        ]

    def test_assignment_to_non_function_pointer_target_not_recorded(self) -> None:
        """An address-of assigned into an ordinary (non-function-pointer)
        variable is not a callback wire-up -- must not be recorded."""
        handler = _func_decl("handler", "F1", mangled="_Z7handleri")
        var_ref = {
            "kind": "DeclRefExpr",
            "referencedDecl": {
                "id": "V1",
                "kind": "VarDecl",
                "name": "g",
                "type": {"qualType": "void *"},
            },
        }
        assign = {
            "kind": "BinaryOperator",
            "opcode": "=",
            "inner": [var_ref, _addr_of(handler)],
        }
        fn = _func_decl(
            "f", "F2", extra_inner=[{"kind": "CompoundStmt", "inner": [assign]}]
        )
        ast = _tu(handler, fn)

        assert parse_clang_ast_callbacks(ast) == []

    def test_address_of_used_only_to_compare_is_not_recorded(self) -> None:
        """An address-of that is neither a call argument nor an assignment/
        initializer target (e.g. an operand of a comparison) is structurally
        unreachable from either detection path -- correctly produces no
        edge, since this module only recognizes the two shapes it documents."""
        handler = _func_decl("handler", "F1", mangled="_Z7handleri")
        compare = {
            "kind": "BinaryOperator",
            "opcode": "==",
            "inner": [_addr_of(handler), _addr_of(handler)],
        }
        fn = _func_decl(
            "f", "F2", extra_inner=[{"kind": "CompoundStmt", "inner": [compare]}]
        )
        ast = _tu(handler, fn)

        assert parse_clang_ast_callbacks(ast) == []


# ── Part A: augment_graph_with_callback_invocations ─────────────────────────


def _decl_node(ident: str, *, confidence: str = CONF_HIGH) -> GraphNode:
    return GraphNode(
        id=f"decl://{ident}",
        kind="source_decl",
        provenance="call_graph",
        confidence=confidence,
    )


def _fp_call_edge(caller: str, slot: str) -> GraphEdge:
    return GraphEdge(
        src=f"decl://{caller}",
        dst=f"decl://{slot}",
        kind="DECL_CALLS_DECL",
        provenance="call_graph",
        confidence=CONF_REDUCED,
        attrs={"call_kind": "function_pointer", "resolution": "unknown"},
    )


def _registration_edge(
    fn: str, slot: str, *, kind: str = EDGE_DECL_REGISTERS_CALLBACK
) -> GraphEdge:
    return GraphEdge(
        src=f"decl://{fn}",
        dst=f"decl://{slot}",
        kind=kind,
        provenance="callback_graph",
        confidence=CONF_HIGH if kind == EDGE_DECL_REGISTERS_CALLBACK else CONF_REDUCED,
    )


class TestCallbackMayInvoke:
    def test_registered_function_reached_via_callback_may_invoke(self) -> None:
        g = SourceGraphSummary()
        for ident in ("caller", "h", "my_handler"):
            g.add_node(_decl_node(ident))
        g.add_edge(_fp_call_edge("caller", "h"))
        g.add_edge(_registration_edge("my_handler", "h"))

        result = augment_graph_with_callback_invocations(g)

        assert result.function_pointer_call_edges_seen == 1
        assert result.dispatch_edges_added == 1
        dispatch = [e for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE]
        assert len(dispatch) == 1
        assert dispatch[0].src == "decl://caller"
        assert dispatch[0].dst == "decl://my_handler"
        assert dispatch[0].resolved["resolution"] == "overapprox"
        assert dispatch[0].resolved["slot"] == "decl://h"
        assert dispatch[0].confidence == CONF_REDUCED

    def test_bare_name_collision_across_unrelated_functions_is_a_known_false_positive(
        self,
    ) -> None:
        """Documents a known, accepted correctness gap (Codex review, fresh
        evidence -- see the module docstring's own "real consequence"
        section), not a bug to silently chase here: two *unrelated*
        functions each declaring their own same-named function-pointer
        parameter `h` (`register(handler_t h)` and `invoke(handler_t h)`)
        collide onto the identical `decl://h` slot node, since neither this
        module nor `call_graph.py` scope-qualifies a parameter/field
        identity. A function registered only via `register`'s `h` is
        therefore (incorrectly) reported as a possible target of a call
        made through `invoke`'s completely unrelated `h`. Fixing this needs
        a scope-qualified identity in `call_graph.py` itself (shared
        infrastructure, its own scoped follow-up) -- this test pins the
        current, documented behavior rather than asserting it should not
        happen, so a future identity fix has a clear regression signal to
        flip."""
        g = SourceGraphSummary()
        for ident in ("register_caller", "invoke_caller", "h", "my_handler"):
            g.add_node(_decl_node(ident))
        # `invoke_caller` calls through its OWN, unrelated `h` parameter --
        # never through `register_caller`'s.
        g.add_edge(_fp_call_edge("invoke_caller", "h"))
        # `my_handler` was registered only onto `register_caller`'s `h`.
        g.add_edge(_registration_edge("my_handler", "h"))

        result = augment_graph_with_callback_invocations(g)

        # The known-bad outcome: invoke_caller's call is (incorrectly)
        # reported as possibly invoking my_handler, purely because both
        # slots share the bare identity "h".
        assert result.dispatch_edges_added == 1
        dispatch = [e for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE]
        assert dispatch[0].src == "decl://invoke_caller"
        assert dispatch[0].dst == "decl://my_handler"

    def test_unregistered_slot_contributes_no_edge(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node("caller"))
        g.add_node(_decl_node("h"))
        g.add_edge(_fp_call_edge("caller", "h"))

        result = augment_graph_with_callback_invocations(g)

        assert result.function_pointer_call_edges_seen == 1
        assert result.dispatch_edges_added == 0
        assert not [e for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE]

    def test_multiple_functions_registered_onto_same_slot_all_reached(self) -> None:
        g = SourceGraphSummary()
        for ident in ("caller", "h", "handler_a", "handler_b"):
            g.add_node(_decl_node(ident))
        g.add_edge(_fp_call_edge("caller", "h"))
        g.add_edge(_registration_edge("handler_a", "h"))
        g.add_edge(
            _registration_edge("handler_b", "h", kind=EDGE_DECL_TAKES_ADDRESS_OF)
        )

        result = augment_graph_with_callback_invocations(g)

        assert result.dispatch_edges_added == 2
        dispatch = [e for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE]
        assert {e.dst for e in dispatch} == {"decl://handler_a", "decl://handler_b"}
        assert all(e.src == "decl://caller" for e in dispatch)
        by_dst = {e.dst: e.resolved["registration_kind"] for e in dispatch}
        assert by_dst["decl://handler_a"] == EDGE_DECL_REGISTERS_CALLBACK
        assert by_dst["decl://handler_b"] == EDGE_DECL_TAKES_ADDRESS_OF

    def test_distinct_slots_reached_from_their_own_callers_only(self) -> None:
        g = SourceGraphSummary()
        for ident in ("caller1", "caller2", "slot1", "slot2", "fn1", "fn2"):
            g.add_node(_decl_node(ident))
        g.add_edge(_fp_call_edge("caller1", "slot1"))
        g.add_edge(_fp_call_edge("caller2", "slot2"))
        g.add_edge(_registration_edge("fn1", "slot1"))
        g.add_edge(_registration_edge("fn2", "slot2"))

        augment_graph_with_callback_invocations(g)

        dispatch = {
            (e.src, e.dst) for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE
        }
        assert dispatch == {
            ("decl://caller1", "decl://fn1"),
            ("decl://caller2", "decl://fn2"),
        }

    def test_non_function_pointer_call_is_ignored(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node("caller"))
        g.add_node(_decl_node("callee"))
        g.add_edge(
            GraphEdge(
                src="decl://caller",
                dst="decl://callee",
                kind="DECL_CALLS_DECL",
                provenance="call_graph",
                confidence=CONF_HIGH,
                attrs={"call_kind": "direct", "resolution": "exact"},
            )
        )

        result = augment_graph_with_callback_invocations(g)

        assert result.function_pointer_call_edges_seen == 0
        assert result.dispatch_edges_added == 0

    def test_empty_graph_is_a_no_op(self) -> None:
        g = SourceGraphSummary()
        result = augment_graph_with_callback_invocations(g)
        assert result == CallbackDispatchResult()
        assert g.edges == []

    def test_idempotent_second_run_adds_no_duplicate_edges(self) -> None:
        g = SourceGraphSummary()
        for ident in ("caller", "h", "my_handler"):
            g.add_node(_decl_node(ident))
        g.add_edge(_fp_call_edge("caller", "h"))
        g.add_edge(_registration_edge("my_handler", "h"))

        augment_graph_with_callback_invocations(g)
        first = len([e for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE])
        augment_graph_with_callback_invocations(g)
        second = len([e for e in g.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE])

        assert first == second == 1


# ── Part B fold: augment_graph_with_callback_registrations ─────────────────


class TestAugmentGraphWithCallbackRegistrations:
    def test_joins_onto_existing_nodes_and_stamps_signature(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node("my_handler"))
        g.add_node(_decl_node("h"))
        edges = [
            CallbackEdge(
                "my_handler",
                "h",
                EDGE_DECL_REGISTERS_CALLBACK,
                CONF_HIGH,
                "void (*)(int)",
            )
        ]

        result = augment_graph_with_callback_registrations(g, edges)

        assert result.edges_seen == 1
        assert result.edges_joined == 1
        joined = [e for e in g.edges if e.kind == EDGE_DECL_REGISTERS_CALLBACK]
        assert len(joined) == 1
        assert joined[0].src == "decl://my_handler"
        assert joined[0].dst == "decl://h"
        slot_node = next(n for n in g.nodes if n.id == "decl://h")
        assert slot_node.resolved.get("function_pointer_signature") == "void (*)(int)"

    def test_mints_missing_src_node(self) -> None:
        """Codex review, fresh evidence: a private handler used only as a
        callback (never itself called, so call_graph.py never creates a
        node for it) has no pre-existing source_decl node -- an earlier,
        strict join-only version silently dropped the whole registration.
        This module's own AST pass has complete, real information about
        the function, so it mints the missing node rather than skip."""
        g = SourceGraphSummary()
        g.add_node(_decl_node("h"))  # only the slot exists, not the function
        edges = [
            CallbackEdge("my_handler", "h", EDGE_DECL_REGISTERS_CALLBACK, CONF_HIGH, "")
        ]

        result = augment_graph_with_callback_registrations(g, edges)

        assert result.edges_joined == 1
        joined = [e for e in g.edges if e.kind == EDGE_DECL_REGISTERS_CALLBACK]
        assert len(joined) == 1
        minted = next(n for n in g.nodes if n.id == "decl://my_handler")
        assert minted.kind == "source_decl"
        assert minted.confidence == CONF_REDUCED

    def test_mints_missing_dst_node(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node("my_handler"))  # only the function exists, not the slot
        edges = [
            CallbackEdge("my_handler", "h", EDGE_DECL_REGISTERS_CALLBACK, CONF_HIGH, "")
        ]

        result = augment_graph_with_callback_registrations(g, edges)

        assert result.edges_joined == 1
        minted = next(n for n in g.nodes if n.id == "decl://h")
        assert minted.kind == "source_decl"
        assert minted.confidence == CONF_REDUCED

    def test_mints_both_endpoints_when_neither_exists(self) -> None:
        g = SourceGraphSummary()
        edges = [
            CallbackEdge("my_handler", "h", EDGE_DECL_REGISTERS_CALLBACK, CONF_HIGH, "")
        ]

        result = augment_graph_with_callback_registrations(g, edges)

        assert result.edges_joined == 1
        assert {n.id for n in g.nodes} == {"decl://my_handler", "decl://h"}
        assert len(g.edges) == 1

    def test_empty_signature_stamps_no_node_fact(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_decl_node("my_handler"))
        g.add_node(_decl_node("h"))
        edges = [
            CallbackEdge(
                "my_handler", "h", EDGE_DECL_TAKES_ADDRESS_OF, CONF_REDUCED, ""
            )
        ]

        augment_graph_with_callback_registrations(g, edges)

        slot_node = next(n for n in g.nodes if n.id == "decl://h")
        assert "function_pointer_signature" not in slot_node.resolved


# ── fold_callback_graph wiring ───────────────────────────────────────────────


class TestFoldCallbackGraphGracefulDegradation:
    def test_missing_clang_records_failed_extractor_row(self, monkeypatch) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence

        monkeypatch.setattr(shutil, "which", lambda _: None)
        graph = SourceGraphSummary()
        rows: list = []
        fold_callback_graph(graph, BuildEvidence(), "clang", rows, ())

        assert any(
            r.name == "callback_graph:clang" and r.status == "failed" for r in rows
        )
        assert not graph.extractor_passes.get("callback_graph")


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
class TestFoldCallbackGraphEndToEnd:
    def test_real_registration_and_indirect_call_join_end_to_end(
        self, tmp_path
    ) -> None:
        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

        src = tmp_path / "t.c"
        src.write_text(
            "typedef void (*handler_t)(int);\n"
            "void my_handler(int x) {}\n"
            "void signal_register(handler_t h);\n"
            "void invoke_it(handler_t h) { h(1); }\n"
            "void use(void) {\n"
            # `my_handler` must itself appear as some direct-call callee
            # somewhere in the TU, or call_graph.py never mints a
            # source_decl node for it at all -- and the join-only-onto-an-
            # existing-node discipline this module reapplies means
            # DECL_REGISTERS_CALLBACK is correctly skipped, not minted,
            # for an endpoint the graph doesn't already carry (verified:
            # omitting this line reproduces exactly that skip).
            "    my_handler(0);\n"
            "    signal_register(&my_handler);\n"
            "    invoke_it(my_handler);\n"
            "}\n"
        )
        merged = BuildEvidence(
            compile_units=[
                CompileUnit(
                    id="cu://t",
                    source=str(src),
                    directory=str(tmp_path),
                    language="C",
                )
            ]
        )
        graph = SourceGraphSummary()
        extractors: list = []
        fold_call_graph(graph, merged, "clang", extractors)
        fold_callback_graph(graph, merged, "clang", extractors)

        assert graph.extractor_passes.get("callback_graph") is True
        assert any(e.kind == EDGE_DECL_REGISTERS_CALLBACK for e in graph.edges)
        may_invoke = [e for e in graph.edges if e.kind == EDGE_CALLBACK_MAY_INVOKE]
        assert may_invoke, "expected at least one CALLBACK_MAY_INVOKE edge"
        assert any(e.resolved.get("resolution") == "overapprox" for e in may_invoke)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("clang") is None, reason="clang not installed")
def test_extractor_unavailable_returns_empty_and_records_diagnostic() -> None:
    extractor = ClangCallbackGraphExtractor(clang_bin="clang-does-not-exist")
    assert extractor.available() is False
    from abicheck.buildsource.build_evidence import BuildEvidence

    assert extractor.extract_from_build(BuildEvidence()) == []
    assert extractor.diagnostics
