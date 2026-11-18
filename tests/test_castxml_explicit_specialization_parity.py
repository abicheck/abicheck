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
"""castxml and clang agree on whether a template instance was spelled as an
explicit specialization, for the forms castxml can prove from its source line.

An explicit instantiation (``extern template``, ``template`` with no ``<``) is
not covered here: castxml cannot join it to its XML node yet (see the plan's
§11.2 row), so only the specialization and the implicit instance are compared.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model import ExplicitInstantiation
from tests._elf_capability import elf_capability, skip_reason

HEADER = """#pragma once
template <typename T> int spec_tpz(T v) { return static_cast<int>(v); }
template <> int spec_tpz<int>(int v) { return v; }
inline int use_implicit() { return spec_tpz(1.0); }
"""
SOURCE = '#include "api.h"\nint keep_library_nonempty() { return use_implicit(); }\n'


def _spelled(fact) -> ExplicitInstantiation | None:
    """The explicit form a fact records, or None when it records no form."""
    if fact is None or fact.value is None:
        return None
    return fact.value


@pytest.mark.integration
def test_castxml_and_clang_agree_on_specialization_and_implicit_instance(
    tmp_path: Path,
) -> None:
    prefix = elf_capability("gcc", "shared")
    if prefix is None:
        pytest.skip(skip_reason("gcc", "shared"))
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the clang header backend")
    (tmp_path / "api.h").write_text(HEADER)
    (tmp_path / "api.cpp").write_text(SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        [
            *prefix,
            "-g",
            "-O0",
            "-fPIC",
            "-shared",
            f"-I{tmp_path}",
            str(tmp_path / "api.cpp"),
            "-o",
            str(so),
        ],
        check=True,
    )
    header = tmp_path / "api.h"
    castxml = dump(so, [header], header_backend="castxml")
    clang = dump(so, [header], header_backend="clang")

    def by_instance(snap, marker: str):
        found = [
            f
            for f in snap.declarations.functions
            if f.name == "spec_tpz" and marker in f.mangled
        ]
        assert len(found) == 1, (marker, [f.mangled for f in found])
        return found[0]

    # Explicit specialization: spec_tpz<int> is spelled in the header.
    spec_c = _spelled(by_instance(castxml, "Ii").explicit_instantiation_fact)
    spec_k = _spelled(by_instance(clang, "Ii").explicit_instantiation_fact)
    assert spec_c == spec_k == ExplicitInstantiation.SPECIALIZATION, (spec_c, spec_k)

    # Implicit instance: spec_tpz<double> is only used, never spelled.
    impl_c = _spelled(by_instance(castxml, "Id").explicit_instantiation_fact)
    impl_k = _spelled(by_instance(clang, "Id").explicit_instantiation_fact)
    assert impl_c != ExplicitInstantiation.SPECIALIZATION, impl_c
    assert impl_k in (None, ExplicitInstantiation.ABSENT), impl_k
