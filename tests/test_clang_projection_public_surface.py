"""The clang projection carries only the public-header surface.

Plan ``l4-l2-extraction-convergence`` Phase 4 (b2 item 1): ``fclose`` and other
system-header declarations must not reach the projected entities. Oracle: the
declaring header named in each AST node, not the projection's own helper.
"""

from __future__ import annotations

from abicheck.buildsource.source_extractors.clang_projection import (
    PublicSurfaceClangParser,
    project_clang_surface,
)


def _fn(name: str, file: str) -> dict:
    return {
        "kind": "FunctionDecl",
        "name": name,
        "loc": {"file": file, "line": 3},
        "mangledName": f"_Z{len(name)}{name}v",
        "type": {"qualType": "void ()"},
        "inner": [],
    }


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def _parser() -> PublicSurfaceClangParser:
    root = _tu(
        _fn("mylib_open", "include/mylib.h"),
        _fn("fclose", "/usr/include/stdio.h"),
    )
    return PublicSurfaceClangParser(
        root, set(), set(), public_header_paths=["include/mylib.h"]
    )


def test_unfiltered_parse_still_carries_the_system_declaration() -> None:
    # The base parser is unchanged: the narrowing is the subclass's own.
    from abicheck.dumper_clang import _ClangAstParser

    root = _tu(
        _fn("mylib_open", "include/mylib.h"), _fn("fclose", "/usr/include/stdio.h")
    )
    base = _ClangAstParser(root, set(), set(), public_header_paths=["include/mylib.h"])
    assert {f.name for f in base.parse_functions()} == {"mylib_open", "fclose"}


def test_projection_excludes_system_header_declarations() -> None:
    names = {
        e.qualified_name
        for e in project_clang_surface(_parser())
        if e.kind == "function"
    }
    assert names == {"mylib_open"}


def test_no_public_set_projects_nothing() -> None:
    root = _tu(_fn("fclose", "/usr/include/stdio.h"))
    assert project_clang_surface(PublicSurfaceClangParser(root, set(), set())) == []
