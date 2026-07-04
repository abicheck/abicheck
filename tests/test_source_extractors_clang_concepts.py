from __future__ import annotations

from abicheck.buildsource.model import LayerConfidence
from abicheck.buildsource.source_abi import SourceAbiTu
from abicheck.buildsource.source_extractors.clang import _walk


class _PublicCtx:
    confidence = LayerConfidence.HIGH

    def classify(self, file: str):
        return ("public_header", "PUBLIC_HEADER", True)


def test_clang_walk_emits_public_concept_decl() -> None:
    tu = SourceAbiTu()
    ast = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "NamespaceDecl",
                "name": "lib",
                "loc": {"file": "include/lib.h", "line": 1},
                "inner": [
                    {
                        "kind": "ConceptDecl",
                        "name": "Accepts",
                        "loc": {"line": 7},
                        "inner": [
                            {"kind": "TemplateTypeParmDecl", "name": "T"},
                            {
                                "kind": "RequiresExpr",
                                "inner": [
                                    {"kind": "TypeRequirement", "name": "iterator"}
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }

    _walk(ast, _PublicCtx(), tu, scope=[], current_file="")

    concepts = [entity for entity in tu.functions if entity.kind == "concept"]
    assert len(concepts) == 1
    assert concepts[0].qualified_name == "lib::Accepts"
    assert concepts[0].value
    assert concepts[0].body_hash == concepts[0].value
