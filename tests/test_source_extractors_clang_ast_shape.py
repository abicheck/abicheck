"""A corrupt clang AST is a failed L4 extraction, never an empty surface.

The L4 readers in ``source_extractors/clang*.py`` walk every node's ``inner``
as a list of objects. A wrong-typed ``inner`` used to read as "no children",
so a corrupt tree reported an empty public surface. ``source_abi_from_clang_ast``
now refuses such a tree with :class:`SourceExtractionError`, which the replay
driver records as a failed extraction (ADR-028 D3).

The misshapen trees are the shared fixtures in ``_malformed_clang_ast`` (also
used by the header-graph projection test), so the bug class is stated once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _malformed_clang_ast import MISSHAPEN_ASTS

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.clang_ast_run import TuAstDump
from abicheck.buildsource.source_extractors.base import SourceExtractionError
from abicheck.buildsource.source_extractors.clang import (
    ClangSourceExtractor,
    source_abi_from_clang_ast,
)

PUBLIC_HEADER = "include/foo.h"


def _cu(**kw: object) -> CompileUnit:
    base: dict[str, object] = {
        "id": "cu://src/foo.cpp#cfg",
        "source": "src/foo.cpp",
        "language": "CXX",
        "standard": "c++17",
    }
    base.update(kw)
    return CompileUnit(**base)  # type: ignore[arg-type]


def _valid_ast() -> dict[str, Any]:
    """A translation unit whose only public entity is the function ``add``."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "add",
                "loc": {"file": PUBLIC_HEADER, "line": 1},
                "mangledName": "_Z3addii",
                "type": {"qualType": "int (int, int)"},
                "inner": [
                    {"kind": "ParmVarDecl", "name": "x", "type": {"qualType": "int"}},
                    {"kind": "ParmVarDecl", "name": "y", "type": {"qualType": "int"}},
                ],
            }
        ],
    }


@pytest.mark.parametrize(("label", "ast"), MISSHAPEN_ASTS)
def test_a_misshapen_ast_is_refused_not_read_as_an_empty_surface(
    label: str, ast: dict[str, Any]
) -> None:
    """Every independently-placed wrong-typed container is a failed extraction.

    No ``SourceAbiTu`` comes back: an empty one would read as "this TU declares
    nothing", which is the false-clean outcome the refusal exists to prevent.
    """
    with pytest.raises(SourceExtractionError, match="clang AST is malformed"):
        source_abi_from_clang_ast(ast, _cu(), [PUBLIC_HEADER], "target://libfoo")


@pytest.mark.parametrize(("label", "ast"), MISSHAPEN_ASTS)
def test_extract_records_a_misshapen_ast_as_a_failed_extraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    label: str,
    ast: dict[str, Any],
) -> None:
    """The real entry point: ``extract()`` fails for the TU, it does not return one.

    The replay driver turns a ``SourceExtractionError`` into a per-TU diagnostic,
    so this is the path that makes a corrupt tree an explicit failure rather
    than a clean, empty L4 result.
    """
    monkeypatch.setattr(ClangSourceExtractor, "available", lambda self: True)
    monkeypatch.setattr(
        ClangSourceExtractor,
        "dump_for",
        lambda self, cu: TuAstDump(ast, returncode=0),
    )
    with pytest.raises(SourceExtractionError, match="clang AST is malformed"):
        ClangSourceExtractor().extract(
            _cu(directory=str(tmp_path)), public_header_roots=[PUBLIC_HEADER]
        )


def test_a_well_formed_ast_still_yields_its_public_surface() -> None:
    """Control: the refusal must not cost a valid tree its declarations.

    Without this, the parametrized refusals above would also pass for a gate
    that rejects every tree.
    """
    tu = source_abi_from_clang_ast(
        _valid_ast(), _cu(), [PUBLIC_HEADER], "target://libfoo"
    )
    assert [f.qualified_name for f in tu.functions] == ["add"]
    assert tu.functions[0].mangled_name == "_Z3addii"


@pytest.mark.parametrize(("label", "ast"), MISSHAPEN_ASTS)
def test_the_l5_path_refuses_a_misshapen_tree_before_anything_walks_it(
    monkeypatch: pytest.MonkeyPatch, label: str, ast: dict[str, Any]
) -> None:
    """On the shared L4/L5 dump the L5 gate runs before the location materializer.

    The materializer walks every node's ``inner``, so on an L4 cache hit (no L4
    reader has validated the tree yet) it would be the first reader of a corrupt
    tree. A spy that fails the test if it ever sees the tree makes the ordering
    observable. Each L5 family must read as not run for this TU.
    """
    import abicheck.buildsource.l5_ast_pass as l5

    def materialize(root: object) -> object:
        raise AssertionError(f"{label}: the materializer walked an unvalidated tree")

    monkeypatch.setattr(l5, "materialize_locations", materialize)
    results, diags = l5.consume_tu_dump(
        TuAstDump(ast, returncode=0), _cu(), l5.L5_AST_PASSES
    )
    assert all(result is None for result in results), label
    for own in diags:
        assert any("clang AST is malformed" in d for d in own), label


def test_the_l5_path_materializes_a_well_formed_tree() -> None:
    """Control for the ordering test: a valid tree does reach the materializer.

    Without it, a spy that is never called would make the refusal test above
    pass for any gate, including one that never runs.
    """
    import abicheck.buildsource.l5_ast_pass as l5

    seen: list[object] = []
    real = l5.materialize_locations

    def spy(root: Any) -> Any:
        seen.append(root)
        return real(root)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(l5, "materialize_locations", spy)
        l5.consume_tu_dump(TuAstDump(_valid_ast(), returncode=0), _cu(), [])
    assert len(seen) == 1
