"""Frozen Itanium C++ consumer of a changed public base path."""

import os
import subprocess
import sys

import pytest


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="compiles shared ELF libraries")
def test_public_base_path_controls_crosscast_and_catch(tmp_path):
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
int main() {
    std::puts(dynamic_cast<Category*>(make_item()) ? "category" : "no category");
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
            ["g++", "-std=c++17", "-shared", "-fPIC", source, "-o", library], check=True
        )
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
            ["category", "base handler"]
            if access == "public"
            else ["no category", "fallback handler"]
        )
        assert output.splitlines() == expected
    assert export_sets[0] == export_sets[1] == export_sets[2]
