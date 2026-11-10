# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Compiler-owned enum representation and ambiguity-safe type-slot identities."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from abicheck.extract.headers.backend_protocol import HeaderParseRequest
from abicheck.extract.headers.clang.backend import ClangBackend
from abicheck.extract.headers.clang.context import _Decl
from abicheck.extract.headers.clang.type_identity import TypeIdentityIndex


def _decl(name, scope=(), **extra):
    return _Decl(
        node={"name": name, **extra},
        scope=scope,
        file="api.h",
        access="public",
        extern_c=False,
    )


@pytest.mark.parametrize("wrapper", ["{}", "const {} *", "{} &", "{}[3]"])
@pytest.mark.parametrize("imported", [False, True])
def test_same_leaf_identity_uses_scope_and_imports(wrapper, imported):
    root = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {"kind": "NamespaceDecl", "id": "left", "name": "left"},
            {"kind": "NamespaceDecl", "id": "right", "name": "right"},
            *(
                [{"kind": "UsingDirectiveDecl", "nominatedNamespace": {"id": "right"}}]
                if imported
                else []
            ),
        ],
    }
    index = TypeIdentityIndex(
        root, [_decl("Cache", ("left",)), _decl("Cache", ("right",))], [], []
    )
    fact = index.resolve(wrapper.format("Cache"), () if imported else ("left",))
    assert fact.is_present
    assert fact.value == (("right::Cache" if imported else "left::Cache"),)
    assert index.resolve(wrapper.format("Cache"), ("unrelated",)).is_present is imported


def test_ambiguous_imports_and_alias_cycles_never_confirm_an_identity():
    root = {
        "kind": "TranslationUnitDecl",
        "inner": [{"kind": "NamespaceDecl", "id": n, "name": n} for n in ("a", "b")]
        + [
            {"kind": "UsingDirectiveDecl", "nominatedNamespace": {"id": n}}
            for n in ("a", "b")
        ],
    }
    index = TypeIdentityIndex(
        root,
        [_decl("Cache", (n,)) for n in ("a", "b")],
        [],
        [_decl("Loop", type={"qualType": "Loop"})],
    )
    assert not index.resolve("Cache", ()).is_present
    assert not index.resolve("Loop", ()).is_present


def test_template_argument_wrappers_are_part_of_the_identity():
    index = TypeIdentityIndex(
        {}, [_decl("Box<int>"), _decl("Box<int *>"), _decl("Box<const int>")], [], []
    )
    for name in ("Box<int>", "Box<int *>", "Box<const int>"):
        assert index.resolve(f"const {name} &", ()).value == (name,)


@pytest.mark.integration
@pytest.mark.parametrize("language", ["c", "c++"])
@pytest.mark.parametrize("scope", ["global", "record"])
@pytest.mark.parametrize("extra", ["", "__attribute__((packed))"])
@pytest.mark.parametrize("flags", [(), ("-fshort-enums",)])
@pytest.mark.parametrize(
    "members", ["LOW=0, HIGH=127", "LOW=-128, HIGH=127", "HIGH=65536"]
)
def test_enum_representation_matches_compiled_object_size(
    tmp_path: Path, language, scope, extra, flags, members
):
    compiler = shutil.which("clang++" if language == "c++" else "clang")
    if compiler is None:
        pytest.skip("needs a clang compiler")
    header = tmp_path / "api.h"
    declaration = f"enum {extra} E {{ {members} }};"
    header.write_text(
        declaration if scope == "global" else "struct Holder { " + declaration + " };\n"
    )
    source = tmp_path / "probe.c"
    spelling = "Holder::E" if scope == "record" and language == "c++" else "enum E"
    source.write_text(f'#include "api.h"\nchar oracle[sizeof({spelling})];\n')
    obj = tmp_path / "probe.o"
    subprocess.run(
        [compiler, "-x", language, *flags, "-c", str(source), "-o", str(obj)],
        check=True,
        capture_output=True,
    )
    with obj.open("rb") as stream:
        (symbol,) = (
            ELFFile(stream).get_section_by_name(".symtab").get_symbol_by_name("oracle")
        )
        expected = symbol["st_size"]
    parser = ClangBackend().parse(
        HeaderParseRequest(
            headers=[header],
            extra_includes=[],
            lang=language,
            gcc_path=compiler,
            gcc_option_tokens=flags,
        )
    )
    (enum,) = parser.parse_enums()
    # An independent object-size oracle; no calls to the probe's expression or decoder.
    sizes = {
        "char": 1,
        "signed char": 1,
        "unsigned char": 1,
        "short": 2,
        "unsigned short": 2,
        "int": 4,
        "unsigned int": 4,
        "long": 8,
        "unsigned long": 8,
        "long long": 8,
        "unsigned long long": 8,
    }
    assert sizes[enum.underlying_type] == expected
