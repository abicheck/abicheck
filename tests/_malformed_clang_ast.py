"""Shared fixtures for the malformed-clang-AST contract (non-test helper).

Used by test_header_graph_projection_cache.py. The misshapen trees, the pass
names they must not read as collected, and the status predicate are the
test's oracle, kept in one place so the cache test and any sibling that
states the same contract cannot drift apart."""

from __future__ import annotations

from typing import Any

# Pass names as the graph records them (literal, so the oracle does not depend
# on the implementation's own constants). A pass reads as *collected* only when
# it was stamped as run and is not marked degraded.
CALL_GRAPH_PASS = "header_call_graph"
AST_DERIVED_PASSES = (
    "header_call_graph",
    "header_override_graph",
    "header_virtual_dispatch_graph",
    "header_template_graph",
    "header_callback_graph",
)

# Misshapen clang ASTs: a container of the wrong JSON type (a number, string,
# mapping, null, or a list of non-objects) at a different node position each.
# One fixture per position, so no single placement is the only one guarded.
MISSHAPEN_ASTS = [
    ("inner is a number", {"kind": "TranslationUnitDecl", "inner": 1}),
    ("inner is a string", {"kind": "TranslationUnitDecl", "inner": "x"}),
    ("inner is a mapping", {"kind": "TranslationUnitDecl", "inner": {"a": 1}}),
    ("inner is null", {"kind": "TranslationUnitDecl", "inner": None}),
    (
        "inner is a list of non-objects",
        {"kind": "TranslationUnitDecl", "inner": [1, "x"]},
    ),
    (
        "a child's inner is a number",
        {
            "kind": "TranslationUnitDecl",
            "inner": [{"kind": "FunctionDecl", "name": "f", "inner": 7}],
        },
    ),
    (
        "a child's inner is a string",
        {
            "kind": "TranslationUnitDecl",
            "inner": [{"kind": "CXXRecordDecl", "name": "R", "inner": "x"}],
        },
    ),
    (
        "a child's inner is a mapping",
        {
            "kind": "TranslationUnitDecl",
            "inner": [{"kind": "FunctionDecl", "name": "f", "inner": {"a": 1}}],
        },
    ),
    (
        "a child's inner is null",
        {
            "kind": "TranslationUnitDecl",
            "inner": [{"kind": "FunctionDecl", "name": "f", "inner": None}],
        },
    ),
    (
        "a function body's inner is a mapping",
        {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "FunctionDecl",
                    "name": "f",
                    "inner": [{"kind": "CompoundStmt", "inner": {"a": 1}}],
                }
            ],
        },
    ),
    (
        "a grandchild's inner is a string",
        {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "NamespaceDecl",
                    "name": "n",
                    "inner": [{"kind": "FunctionDecl", "name": "f", "inner": "x"}],
                }
            ],
        },
    ),
    (
        "a grandchild's inner is null",
        {
            "kind": "TranslationUnitDecl",
            "inner": [
                {
                    "kind": "NamespaceDecl",
                    "name": "n",
                    "inner": [{"kind": "FunctionDecl", "name": "f", "inner": None}],
                }
            ],
        },
    ),
    (
        "a child is a bare string",
        {
            "kind": "TranslationUnitDecl",
            "inner": [{"kind": "NamespaceDecl", "name": "n", "inner": ["oops"]}],
        },
    ),
]


def reads_as_collected(graph: Any, name: str) -> bool:
    """A pass reads as collected only when stamped as run and not degraded."""
    return bool(graph.extractor_passes.get(name)) and not graph.degraded_passes.get(
        name
    )
