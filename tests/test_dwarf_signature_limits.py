import shutil
import subprocess
import sys

import pytest

_GPP = "g++"
_HAS_GPP = sys.platform.startswith("linux") and shutil.which(_GPP) is not None


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
@pytest.mark.parametrize("with_definition", [False, True])
def test_signature_forward_decl_assurance(tmp_path, with_definition):
    from abicheck.dwarf_snapshot import extract_dwarf_declarations
    from abicheck.elf_metadata import parse_elf_metadata

    api = tmp_path / "api.cpp"
    api.write_text('struct Node; extern "C" void feed(Node&) {}')
    sources = [api]
    if with_definition:
        impl = tmp_path / "impl.cpp"
        impl.write_text("struct Node { int value; }; Node instance;")
        sources.append(impl)
    lib = tmp_path / "libvisitor.so"
    subprocess.run([_GPP, "-shared", "-fPIC", "-g", *sources, "-o", lib], check=True)
    coverage = extract_dwarf_declarations(
        lib, parse_elf_metadata(lib)
    ).extraction_limitations
    assert any("feed parameter 0" in note and "Node" in note for note in coverage) == (
        not with_definition
    )


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
@pytest.mark.parametrize(
    ("declarations", "missing"),
    [
        ("struct Node; using NodeRef = Node&; void feed(NodeRef) {}", True),
        (
            "namespace api {struct Node; using Alias = Node&;} void feed(api::Alias) {}",
            True,
        ),
        (
            "struct Node; Node* opaque; namespace api {struct Node {int x;};} void feed(api::Node& n) {++n.x;}",
            False,
        ),
    ],
)
def test_forward_declaration_lookup(tmp_path, declarations, missing):
    from abicheck.dwarf_snapshot import extract_dwarf_declarations
    from abicheck.elf_metadata import parse_elf_metadata

    src = tmp_path / "visitor.cpp"
    src.write_text(declarations)
    lib = tmp_path / "libvisitor.so"
    subprocess.run([_GPP, "-shared", "-fPIC", "-g", src, "-o", lib], check=True)
    result = extract_dwarf_declarations(lib, parse_elf_metadata(lib))
    assert any("feed" in note for note in result.extraction_limitations) == missing
