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

"""Collapse benign duplicate declarations of one function key at the source.

castxml reports two ``Function`` records under one snapshot key in two
dependency-header shapes, and ``AbiSnapshot.index`` then warned
"Duplicate mangled symbols skipped (first-wins)" for each:

* glibc's C++ ``<wchar.h>``/``<string.h>`` declare ``const`` and non-``const``
  overloads of ``wcschr``/``strchr``/``memchr``/... bound to one asm name --
  one symbol, two declarations differing only in cv-qualification.
* libstdc++ declares a defaulted copy/move constructor *and* a converting
  constructor template whose instantiation for the class's own argument has the
  identical parameter list, so both synthesize the same
  ``__abicheck_ctor__std::...(...)`` placeholder key. Overload resolution
  always picks the non-template one; the template instance is not a second
  entry point.

A group is collapsed to its first declaration (castxml document order, so the
result is deterministic) only when *every* member is provably a dependency
declaration -- in a toolchain/system header outside the public set
(``location.decl_is_system_dependency``) -- and the members' signatures agree
modulo cv-qualification (exception specification and variadicity equal). Anything else -- a member from the library's own
headers (public, private or of unknown provenance), or signatures that
genuinely conflict -- is left for the snapshot index, which still warns.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from xml.etree.ElementTree import Element

from ....model import Function

_CV = re.compile(r"\b(?:const|volatile)\b")


#: GCC/Clang's ``<stdarg.h>`` spellings of one type
#: (``va_list`` -> ``__gnuc_va_list`` -> ``__builtin_va_list``): mingw-w64
#: declares ``_vswprintf`` once through the typedef and once with the builtin.
#: MSVC's ``va_list`` is ``char*`` and has no builtin spelling, so it never
#: reaches this mapping.
_VA_LIST = re.compile(r"\b(?:__builtin_va_list|__gnuc_va_list)\b")


def _cv_free(spelling: str) -> str:
    return " ".join(_VA_LIST.sub("va_list", _CV.sub(" ", spelling)).split())


def _signature(
    func: Function,
) -> tuple[str, str, tuple[str, ...], bool | None, bool, str | None]:
    # Exception specifications are not mangled, so two same-key records can
    # disagree on them; such a pair is not one entity.
    return (
        func.name,
        _cv_free(func.return_type),
        tuple(_cv_free(p.type) for p in func.params),
        func.is_variadic,
        func.is_noexcept,
        func.exception_spec,
    )


def is_benign_duplicate_group(
    funcs: Sequence[Function], dependency: Sequence[bool]
) -> bool:
    """Whether same-key *funcs* are one dependency-header entity, declared twice.

    *dependency* holds, per member, whether its declaration is provably in a
    system (dependency) header rather than the library's own.
    """
    if len(funcs) < 2 or not all(dependency):
        return False
    first = _signature(funcs[0])
    return all(_signature(f) == first for f in funcs[1:])


def collapse_benign_duplicates(
    parsed: Sequence[tuple[Element, Function]],
    is_dependency: Callable[[Element], bool],
) -> list[Function]:
    """*parsed* functions, with each benign duplicate group reduced to its first
    member. *is_dependency* (``decl_is_system_dependency``) is asked only
    about duplicates."""
    groups: dict[str, list[int]] = {}
    for index, (_, func) in enumerate(parsed):
        groups.setdefault(func.mangled, []).append(index)
    drop: set[int] = set()
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        funcs = [parsed[i][1] for i in indexes]
        dependency = [is_dependency(parsed[i][0]) for i in indexes]
        if is_benign_duplicate_group(funcs, dependency):
            drop.update(indexes[1:])
    return [func for i, (_, func) in enumerate(parsed) if i not in drop]
