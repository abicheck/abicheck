# SPDX-License-Identifier: Apache-2.0
"""``public_api_exposes_stl_by_value`` ignores ``std::`` aliases of fundamental types.

Bug class: the clang header backend spells a type as written
(``std::size_t``), castxml as its canonical type (``unsigned long``), so a
detector keyed on the ``std::`` prefix fired on clang only (catalog case17:
``Buffer<int>::size()`` returning ``std::size_t``). The invariant: a ``std::``
name crosses the ABI boundary as a *library type* only when it is a class
type; a fundamental type or an enum over one (``std::byte``) does not.
Oracle: the C++ compiler's own ``std::is_class`` for every spelling.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.idioms import _is_std_by_value
from abicheck.model import ParamKind

_SPELLINGS = [
    "std::size_t", "std::ptrdiff_t", "std::nullptr_t", "std::max_align_t",
    "std::byte", "std::int8_t", "std::uint16_t", "std::int32_t", "std::uint64_t",
    "std::int_least8_t", "std::uint_fast64_t", "std::intmax_t", "std::uintptr_t",
    "std::string", "std::vector<int>", "std::vector<std::size_t>",
    "std::pair<std::size_t, int>", "std::optional<std::size_t>",
    "std::shared_ptr<int>", "std::array<std::uint8_t, 4>",
]  # fmt: skip

_CLASS_TYPES = {
    "std::string", "std::vector<int>", "std::vector<std::size_t>",
    "std::pair<std::size_t, int>", "std::optional<std::size_t>",
    "std::shared_ptr<int>", "std::array<std::uint8_t, 4>",
}  # fmt: skip


#: ``std::`` re-exports of C ``<stddef.h>`` types whose representation the
#: C ABI fixes per platform (``std::is_class`` differs between glibc and libc++).
_C_LIBRARY_TYPES = {"std::max_align_t"}


@pytest.mark.parametrize("spelling", _SPELLINGS)
def test_only_std_class_types_count_as_stl_by_value(spelling: str) -> None:
    assert _is_std_by_value(spelling, 0, ParamKind.VALUE) is (spelling in _CLASS_TYPES)
    assert _is_std_by_value(f"const {spelling}", 0, ParamKind.VALUE) is (
        spelling in _CLASS_TYPES
    )


@pytest.mark.parametrize("spelling", ["std::string", "std::size_t"])
def test_indirection_never_counts(spelling: str) -> None:
    assert not _is_std_by_value(f"{spelling} *", 1, ParamKind.POINTER)
    assert not _is_std_by_value(f"const {spelling} &", 0, ParamKind.REFERENCE)


@pytest.mark.integration
@pytest.mark.skipif(not shutil.which("g++"), reason="needs g++")
def test_detector_agrees_with_the_compilers_is_class(tmp_path: Path) -> None:
    src = tmp_path / "probe.cpp"
    lines = [
        "#include <cstddef>", "#include <cstdint>", "#include <string>",
        "#include <vector>", "#include <utility>", "#include <optional>",
        "#include <memory>", "#include <array>", "#include <type_traits>",
        "#include <cstdio>", "int main() {",
    ]  # fmt: skip
    for s in _SPELLINGS:
        lines.append(f'  std::printf("%d\\n", (int)std::is_class_v<{s}>);')
    lines.append("}")
    src.write_text("\n".join(lines))
    exe = tmp_path / "probe"
    subprocess.run(["g++", "-std=c++17", str(src), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    is_class = [line == "1" for line in out.stdout.split()]
    assert len(is_class) == len(_SPELLINGS)
    for spelling, cls in zip(_SPELLINGS, is_class, strict=True):
        if spelling in _C_LIBRARY_TYPES:
            # A struct on glibc, ``long double`` on libc++: its layout is the
            # platform C ABI's, not a C++ library's, so it never counts.
            assert not _is_std_by_value(spelling, 0, ParamKind.VALUE), spelling
            continue
        assert _is_std_by_value(spelling, 0, ParamKind.VALUE) is cls, spelling
