"""Toolchain header paths classify as system headers on every platform.

The duplicate-declaration collapse trusts ``is_system_header`` to say a
header is toolchain code. The expected values below are written from the
platform's documented layouts, not from the implementation, so a classifier
that only knew Linux roots fails here on the macOS, MSVC, UCRT and mingw
shapes.
"""

from __future__ import annotations

import pytest

from abicheck.extract.declaration_provenance import is_system_header


@pytest.mark.parametrize(
    "header",
    [
        # Linux: distribution and C++ standard-library roots
        "/usr/include/stdio.h",
        "/usr/include/c++/13/vector",
        "/usr/lib/gcc/x86_64-linux-gnu/13/include/stdarg.h",
        # macOS: Command Line Tools and Xcode SDK sysroots
        "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/stdio.h",
        "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform"
        "/Developer/SDKs/MacOSX15.sdk/usr/include/stdio.h",
        # Windows MSVC toolset, backslash and forward-slash spellings
        "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools"
        "\\MSVC\\14.40.33807\\include\\vector",
        "C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC"
        "/14.40.33807/include/vector",
        # Windows SDK: UCRT and um
        "C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.22621.0\\ucrt"
        "\\corecrt.h",
        "C:/Program Files (x86)/Windows Kits/10/Include/10.0.22621.0/um/windows.h",
    ],
)
def test_toolchain_header_is_system_on_every_platform(header: str) -> None:
    assert is_system_header(header) is True


@pytest.mark.parametrize(
    "header",
    [
        # A library's own headers in a project tree, on each platform.
        "/home/user/proj/include/mylib.h",
        "C:/src/mylib/include/mylib.h",
        "C:\\src\\mylib\\include\\mylib.h",
        "/Users/dev/mylib/include/mylib.h",
    ],
)
def test_project_header_is_not_system(header: str) -> None:
    # Negative control: a project header is never toolchain code, so its
    # same-key duplicates are the library's own and must keep warning.
    assert is_system_header(header) is False


@pytest.mark.parametrize("header", [None, ""])
def test_missing_provenance_is_not_system(header: str | None) -> None:
    assert is_system_header(header) is False
