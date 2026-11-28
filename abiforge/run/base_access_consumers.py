"""Generate a frozen C++ client and libraries with different direct-base access."""

import subprocess
from pathlib import Path


def build_cases(tmp_path: Path, link_flags: tuple[str, ...]):
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
    libraries = {}
    for access in ("public", "protected", "private"):
        root = tmp_path / access
        root.mkdir()
        (root / "api.hpp").write_text(header.replace("ACCESS", access))
        source = root / "api.cpp"
        source.write_text(provider)
        library = root / "libitem.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-shared",
                "-s",
                "-fPIC",
                *link_flags,
                source,
                "-o",
                library,
            ],
            check=True,
        )
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
        libraries[access] = library
    return exe, libraries
