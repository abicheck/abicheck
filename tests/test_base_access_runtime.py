"""Frozen Itanium C++ consumer of a changed public base path."""

import os
import subprocess
import sys

import pytest


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="compiles shared ELF libraries")
def test_public_base_path_controls_crosscast_and_catch(tmp_path):
    from abiforge.run.itanium_base_edges import direct_base_flags

    header = """struct Anchor { virtual ~Anchor(); };
struct Category { virtual ~Category(); };
struct Item : public Anchor, ACCESS Category {};
Anchor* make_item();
void raise_item();
"""
    provider = """#include "api.hpp"
Anchor::~Anchor() = default;
Category::~Category() = default;
Anchor* make_item() { static Item value; return &value; }
void raise_item() { throw Item{}; }
"""
    client_source = tmp_path / "client.cpp"
    client_source.write_text(
        """#include "api.hpp"
#include <cstdio>
#include <cxxabi.h>
int main() {
    Anchor* object = make_item();
    const auto* info = dynamic_cast<const __cxxabiv1::__vmi_class_type_info*>(&typeid(*object));
    if (!info || info->__base_count != 2) return 2;
    const auto& edge = info->__base_info[1];
    if (*edge.__base_type != typeid(Category) || edge.__is_virtual_p()) return 3;
    std::puts(edge.__is_public_p() ? "public edge" : "nonpublic edge");
    std::puts(dynamic_cast<Category*>(object) ? "category" : "no category");
    try { raise_item(); }
    catch (const Category&) { std::puts("base handler"); }
    catch (...) { std::puts("fallback handler"); }
}
"""
    )
    exe = tmp_path / "client"
    export_sets = []
    for access in ("public", "protected", "private"):
        root = tmp_path / access
        root.mkdir()
        (root / "api.hpp").write_text(header.replace("ACCESS", access))
        source = root / "api.cpp"
        source.write_text(provider)
        library = root / "libitem.so"
        subprocess.run(
            ["g++", "-std=c++17", "-shared", "-s", "-fPIC", source, "-o", library],
            check=True,
        )
        if os.uname().machine == "x86_64":
            edges = direct_base_flags(library, "_ZTI4Item")
            assert edges is not None
            assert edges["_ZTI6Anchor"] == 2
            assert edges["_ZTI8Category"] == (8 << 8) | (2 if access == "public" else 0)
            assert direct_base_flags(library, "_ZTI6Anchor") is None
        exports = subprocess.check_output(
            ["nm", "-D", "--defined-only", "-P", library], text=True
        )
        export_sets.append({line.split()[0] for line in exports.splitlines()})
        if access == "public":
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-I",
                    root,
                    client_source,
                    "-L",
                    root,
                    "-litem",
                    "-o",
                    exe,
                ],
                check=True,
            )
        output = subprocess.check_output(
            [exe], env={**os.environ, "LD_LIBRARY_PATH": str(root)}, text=True
        )
        expected = (
            ["public edge", "category", "base handler"]
            if access == "public"
            else ["nonpublic edge", "no category", "fallback handler"]
        )
        assert output.splitlines() == expected
    assert export_sets[0] == export_sets[1] == export_sets[2]
