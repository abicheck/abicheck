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

import json
from pathlib import Path
from typing import Any

import pytest
from _malformed_clang_ast import MISSHAPEN_ASTS

from abicheck.buildsource.build_evidence import CompileUnit
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

    def run_ast_to_file(
        self: ClangSourceExtractor, cmd: list[str], directory: str, source_label: str
    ) -> tuple[Path, str, int]:
        ast_path = tmp_path / "ast.json"
        ast_path.write_text(json.dumps(ast))
        return ast_path, "", 0

    monkeypatch.setattr(ClangSourceExtractor, "_run_ast_to_file", run_ast_to_file)
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
