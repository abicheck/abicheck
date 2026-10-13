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

"""castxml's L4 replay emulates the compiler mode the unit's language needs.

In ``--castxml-cc-gnu`` mode castxml injects the emulated compiler's C++
``-std=gnu++NN``, which its parser rejects for a C source ("not allowed with
'C'"), so every C unit's replay failed. The L2 header parse already used
``--castxml-cc-gnu-c`` for a C parse; the replay now does too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import CompileUnit
from abicheck.buildsource.source_extractors import build_castxml_command


def _cu(language: str) -> CompileUnit:
    return CompileUnit(
        id="cu://src/a.c#cfg",
        source="src/a.c",
        language=language,
        argv=["cc", "-c", "src/a.c"],
        directory="/work",
    )


@pytest.mark.parametrize(
    ("language", "compiler", "mode"),
    [
        ("C", None, "gnu-c"),
        ("CXX", None, "gnu"),
        ("", None, "gnu"),
        ("C", "clang", "gnu-c"),
        ("CXX", "clang++", "gnu"),
        ("C", "cl.exe", "msvc"),
        ("CXX", "cl.exe", "msvc"),
    ],
)
def test_castxml_emulation_mode_follows_the_unit_language(
    language: str, compiler: str | None, mode: str
) -> None:
    """Exactly one `--castxml-cc-<mode>` flag per command: C emulation for a
    GNU-family C unit, C++ for every other GNU unit, MSVC whatever the
    language (castxml's MSVC emulation has no separate C mode)."""
    cmd = build_castxml_command(
        _cu(language), Path("a.c"), Path("o.xml"), compiler_binary=compiler
    )
    modes = [a for a in cmd if a.startswith("--castxml-cc-")]
    assert modes == [f"--castxml-cc-{mode}"]
