# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Who owns a standard customization-point specialization.

A program may specialize a standard template only for a program-defined type
([namespace.std]/2). A specialization whose arguments are builtins, pointers,
references and cv-qualifications of them, or standard types is therefore the
implementation's (``std::less<const char*>``, ``std::hash<std::string>``);
one that involves a program-defined type anywhere is the library's.

The oracle is the construction itself: every name below is assembled from a
small Itanium type grammar whose atoms each carry "is program-defined", so
the expected owner is known before abicheck reads the name. It does not call
the scanner under test. ``-O2`` rebuilds of an old-ABI (``_GLIBCXX_USE_CXX11_ABI=0``)
``std::string`` library lose the weak ``std::less<const char*>::operator()``
copy the ``-O0`` build exported; reading it as user-owned kept its RISK.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
import sys

import pytest

from abicheck.extract.stdlib_local_origin import is_user_specializable_std_symbol

# (mangled type, involves a program-defined type)
_ATOMS: tuple[tuple[str, bool], ...] = (
    ("c", False),  # char
    ("i", False),  # int
    ("m", False),  # unsigned long
    ("Di", False),  # char32_t
    ("Ss", False),  # std::string (old ABI abbreviation)
    (
        "NSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE",
        False,
    ),  # std::__cxx11::string
    ("6MyType", True),
    ("N5mylib4CharE", True),
)

_QUALIFIERS = ("", "P", "PK", "R", "RK", "O", "PPK")


def _wrap(atom: tuple[str, bool]) -> list[tuple[str, bool]]:
    """*atom* and the standard templates instantiated over it."""
    t, owned = atom
    return [
        (t, owned),
        (f"St6vectorI{t}E", owned),
        (f"SaI{t}E", owned),
        (f"St4pairI{t}iE", owned),
    ]


_TYPES = [
    (q + t, owned) for atom in _ATOMS for t, owned in _wrap(atom) for q in _QUALIFIERS
]

# Customization points as the member names a translation unit exports.
_SHAPES = (
    "_ZNKSt4hashI{t}EclE{t}",
    "_ZNKSt4lessI{t}EclE{t}{t}",
    "_ZNKSt8equal_toI{t}EclE{t}{t}",
    "_ZNSt14numeric_limitsI{t}E3maxEv",
    "_ZNSt11char_traitsI{t}E6lengthEPKc",
    "_ZZNKSt4hashI{t}EclE{t}E1x",  # a local static of that member
    "_ZGVZNKSt4hashI{t}EclE{t}E1x",  # and its guard
)


@pytest.mark.parametrize(
    ("shape", "ty"),
    list(itertools.product(_SHAPES, _TYPES)),
    ids=lambda v: v if isinstance(v, str) else v[0],
)
def test_a_specialization_is_the_librarys_only_over_a_program_type(
    shape: str, ty: tuple[str, bool]
) -> None:
    mangled, program_defined = ty
    name = shape.format(t=mangled)
    assert is_user_specializable_std_symbol(name) is program_defined, name


@pytest.mark.parametrize(
    "name",
    [
        "_ZNKSt4hashI6MyTypeEclERKS0_",  # a substitution back to MyType
        "_ZNKSt4hashIL_Z1xEEclEv",  # an encoding inside a literal: unread
        "_ZNKSt4hashIXadL_Z1fEEEclEv",  # an expression argument: unread
    ],
)
def test_a_name_the_scanner_cannot_clear_stays_the_librarys(name: str) -> None:
    assert is_user_specializable_std_symbol(name) is True


def test_a_non_customization_point_is_never_user_specializable() -> None:
    assert (
        is_user_specializable_std_symbol("_ZNSt6vectorIiSaIiEE9push_backERKi") is False
    )
    assert is_user_specializable_std_symbol("_Z3fooi") is False


_CPP = """\
#include <functional>
#include <string>
#include <vector>
struct MyType { int v; };
template <> struct std::hash<MyType> {
    std::size_t operator()(const MyType& m) const noexcept { return m.v; }
};
template struct std::less<const char*>;
template struct std::equal_to<std::string>;
template struct std::less<std::vector<int>>;
template struct std::less<const MyType*>;
std::size_t touch(const MyType& m) { return std::hash<MyType>{}(m); }
"""


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/libstdc++ names")
@pytest.mark.parametrize("dual_abi", ["0", "1"])
def test_real_member_names_follow_the_program_type(tmp_path, dual_abi) -> None:
    """The names g++ emits, judged by c++filt's demangling: a member is the
    library's exactly when ``MyType`` appears in it."""
    if shutil.which("g++") is None or shutil.which("c++filt") is None:
        pytest.skip("g++/c++filt not installed")
    src = tmp_path / "cp.cpp"
    src.write_text(_CPP)
    obj = tmp_path / "cp.o"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-c",
            f"-D_GLIBCXX_USE_CXX11_ABI={dual_abi}",
            "-o",
            obj,
            src,
        ],
        check=True,
    )
    nm = subprocess.run(
        ["nm", "--defined-only", "-j", obj], capture_output=True, text=True, check=True
    ).stdout.split()
    picked = [
        n for n in nm if n.startswith(("_ZNKSt4hash", "_ZNKSt4less", "_ZNKSt8equal_to"))
    ]
    demangled = subprocess.run(
        ["c++filt"], input="\n".join(picked), capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert len(picked) >= 5, picked
    got = {n: is_user_specializable_std_symbol(n) for n in picked}
    want = {n: "MyType" in d for n, d in zip(picked, demangled, strict=True)}
    assert got == want
