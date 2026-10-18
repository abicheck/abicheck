"""A root ``inner`` of the wrong JSON type must never read as an empty, clean TU.

The header-graph projection has two readers of a cached clang AST: the
whole-tree validator (``validate_ast_shape``) and the streaming scanner
(``header_graph_ast_stream``), which a warm run takes for a document at or
above the streaming threshold. The validator rejects a root ``inner`` that is
not a list. The scanner's root-``inner`` search only accepts a key followed by
``: [``; any other value after the key is skipped as though the key were
absent, and a root with no ``inner`` is the legitimate empty translation unit.
So a cached document with ``"inner": {...}`` (or ``null``, or a scalar) came
back as a complete, empty projection with no error, and every AST-derived pass
read as collected.

The oracle is the status the graph records, not the projection's contents: a
malformed AST is failed evidence (ADR-028 D3), so no AST-derived pass may read
as collected, and the call graph must carry the explicit degraded marker.
Each shape is run cold (whole-tree parse) and warm (served from the AST cache),
and each run asserts the mechanism it claims to test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import test_header_graph_projection_cache as projection_tests
from _malformed_clang_ast import (
    AST_DERIVED_PASSES,
    CALL_GRAPH_PASS,
    reads_as_collected,
)

from abicheck.buildsource.header_graph_ast_projection import project_header_graph_ast
from abicheck.buildsource.header_graph_ast_stream import project_header_graph_ast_file

# Module-attribute access, not ``from ... import``: importing the harness class
# by name would make pytest collect its tests a second time in this module.
Harness = projection_tests.TestTheWarmRunReallySkipsTheParse

# Each shape is the root's ``inner``. The first two are valid and must stay a
# genuine zero; the last three are the malformed ones the validator refuses.
SHAPES: dict[str, dict] = {
    "list": projection_tests.AST_CASES["call_edge"],
    "empty_list": {"kind": "TranslationUnitDecl", "inner": []},
    "mapping": {"kind": "TranslationUnitDecl", "inner": {"a": 1}},
    "null": {"kind": "TranslationUnitDecl", "inner": None},
    "scalar": {"kind": "TranslationUnitDecl", "inner": 7},
}
MALFORMED = frozenset({"mapping", "null", "scalar"})


@pytest.mark.parametrize("phase", ["cold", "warm"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_root_inner_of_any_shape_is_never_read_as_a_clean_empty_tu(
    monkeypatch, tmp_path: Path, shape: str, phase: str
) -> None:
    # Threshold 0 forces the streaming path for every document, so the warm
    # run's projection comes from the scanner, the reader under test.
    monkeypatch.setenv("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", "0")
    counts = Harness._install(monkeypatch, tmp_path, SHAPES[shape])

    first = Harness._attach(Harness._snapshot())  # cold: no entry on disk
    second = Harness._attach(Harness._snapshot())  # warm: entry on disk

    # The cache path actually executed: the AST was read once, on the cold run,
    # and the warm run was served from the cache rather than re-parsed.
    assert counts["ast_reads"] == 1, "the warm run re-parsed the AST"
    if phase == "warm" and shape in MALFORMED:
        # The streamer was the reader under test on this run.
        assert counts["streams"] == 1, "the warm run never reached the stream"

    graph = first.surface_graph if phase == "cold" else second.surface_graph
    if shape in MALFORMED:
        for name in AST_DERIVED_PASSES:
            assert not reads_as_collected(graph, name), (
                f"{phase}/{shape}: {name} reads as collected from a malformed AST"
            )
        assert graph.degraded_passes.get(CALL_GRAPH_PASS) is True, f"{phase}/{shape}"
    else:
        # Positive control: a valid root is a genuine zero, read as collected.
        assert reads_as_collected(graph, CALL_GRAPH_PASS), f"{phase}/{shape}"
        assert graph.degraded_passes.get(CALL_GRAPH_PASS) is None


@pytest.mark.parametrize(
    "doc",
    [
        # `inner` as a value, not the array key: the scanner must skip it and
        # still find the real root `inner` that follows.
        {"kind": "inner", "inner": []},
        # A valid root array, streamed: the stream must agree with the tree.
        {"kind": "TranslationUnitDecl", "inner": []},
    ],
)
def test_a_streamed_valid_root_matches_the_whole_tree(
    tmp_path: Path, doc: dict
) -> None:
    """The streamed projection of a well-formed root equals the whole-tree one.

    The malformed roots are covered through the cache above. This pins the
    accepting side of the same scanner, so the change cannot make a valid
    document refuse, and it runs the stream on the changed lines itself.
    """
    path = tmp_path / "ast.json"
    path.write_text(json.dumps(doc))

    streamed = projection_tests._as_tuple(project_header_graph_ast_file(path))
    assert streamed == projection_tests._as_tuple(project_header_graph_ast(doc))
