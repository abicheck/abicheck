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

"""Runtime origin of a function-local static of a standard template.

A function-local ``static`` (and its guard variable) inside a ``std::``,
``__gnu_cxx::`` or ``__cxxabiv1::`` template is emitted weak/unique into every
translation unit that instantiates the template, so the C++ runtime owns it even
though no ``_ZNSt``-style prefix matches its ``_ZZ``/``_ZGVZ`` opener. The origin
table (:mod:`abicheck.elf_metadata`) and the symbol-hygiene audit
(:mod:`abicheck.buildsource.export_accounting`) both ask this module, so they name
the same owner for one symbol.

Two shapes stay with the library under test: a local static of a template
instantiated over the library's own type (``std::vector<MyType>::f``, emitted by
the library, not the runtime), and a customization-point specialization
(``std::hash<MyType>``), whose body the user wrote.
"""

from __future__ import annotations

import re

from ..name_classification import (
    _USER_SPECIALIZABLE_STD_TEMPLATE_RE,
    is_stdlib_local_name_symbol,
)
from .mangled_foreign_template_args import has_foreign_template_argument_in_local_name

__all__ = [
    "find_cxx_stdlib",
    "find_libcxx",
    "is_user_specializable_std_symbol",
    "stdlib_local_static_origin",
]

# The non-local form of the same customization-point test: a member of such a
# specialization (``std::hash<MyType>::operator()`` mangles as
# ``_ZNKSt4hashI6MyTypeEclERKS0_``, with no ``ZZ`` local-name opener).
_USER_SPECIALIZABLE_STD_MEMBER_RE = re.compile(
    r"^_Z(?:GV)?N[rVK]{0,3}[RO]?(?:St|3std)"
    r"(?:\d+__[A-Za-z0-9_]+?)?"
    r"(?:4hash|4less|7greater|8equal_to|12not_equal_to|10less_equal|"
    r"13greater_equal|11char_traits|14numeric_limits|15iterator_traits|"
    r"14default_delete|9formatter|4swap|10tuple_size|11common_type)I"
)


def is_user_specializable_std_symbol(name: str) -> bool:
    """Whether *name* is a member, local static or guard of a standard
    customization-point template specialization (``std::hash<T>`` and the
    others in ``name_classification``'s list).

    Such a symbol nominally lives in namespace ``std``, but its body is
    user-authored, so it is the library's own ABI and never the runtime's.
    """
    return bool(
        _USER_SPECIALIZABLE_STD_TEMPLATE_RE.match(name)
        or _USER_SPECIALIZABLE_STD_MEMBER_RE.match(name)
    )


def find_libcxx(needed_libs: list[str]) -> str | None:
    """Find a libc++ (not libstdc++) library in the needed list."""
    for lib in needed_libs:
        if "c++" in lib and "stdc++" not in lib:
            return lib
    return None


def find_cxx_stdlib(needed_libs: list[str]) -> str | None:
    """Find any C++ standard library (libstdc++ or libc++) in the needed list."""
    for lib in needed_libs:
        if "stdc++" in lib or "c++" in lib:
            return lib
    return None


def stdlib_local_static_origin(name: str, needed_libs: list[str]) -> str | None:
    """The runtime library a function-local std static *name* belongs to, or
    ``None`` when *name* is not one (or is a library-owned instantiation).

    libc++ names its standard library through the ``std::__1`` inline namespace
    (``St3__1``); the picker prefers the runtime the binary links. The result is
    a basename, as the owner-namespace path reports it (a Mach-O
    ``/usr/lib/libc++.1.dylib`` is named ``libc++.1.dylib``).
    """
    if not is_stdlib_local_name_symbol(name):
        return None
    if has_foreign_template_argument_in_local_name(name):
        return None
    if "St3__1" in name:
        runtime_lib = find_libcxx(needed_libs) or "libc++.so.1"
    else:
        runtime_lib = find_cxx_stdlib(needed_libs) or "libstdc++.so.6"
    return runtime_lib.rsplit("/", 1)[-1]
