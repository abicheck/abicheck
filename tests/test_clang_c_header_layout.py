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

"""A C record that grows is a size change on the clang backend without DWARF.

Formerly a known-gap canary: the clang record-layout pass probed C++
headers only, so a C struct built without ``-g`` had no size and its growth
read ``COMPATIBLE``. Extractor generation 39 probes C headers too (as
``struct N``, ``union N`` and a typedef name). The oracle is castxml on the
same pair, which has always laid C records out: both backends must report
``type_size_changed`` for every spelling of the grown record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_known_gap_canaries_header_backends import (
    _LINUX_ONLY,
    _compare_libraries,
    _kinds,
    _require,
    _write_pair,
)

pytestmark = [pytest.mark.integration, _LINUX_ONLY]

#: (id, old header, new header): each grows one C record passed by value.
_SPELLINGS = [
    (
        "struct_tag",
        "struct S { int a; };\nint f(struct S s);\n",
        "struct S { int a; int b; };\nint f(struct S s);\n",
    ),
    (
        "typedef_name",
        "typedef struct { int a; } T;\nint f(T s);\n",
        "typedef struct { int a; int b; } T;\nint f(T s);\n",
    ),
    (
        "union_tag",
        "union U { int a; };\nint f(union U s);\n",
        "union U { int a; double b; };\nint f(union U s);\n",
    ),
]
_SRC = '#include "api.h"\nint f({param} s) {{ (void)s; return 0; }}\n'
_PARAM = {"struct_tag": "struct S", "typedef_name": "T", "union_tag": "union U"}


@pytest.mark.parametrize(
    ("spelling", "old", "new"), _SPELLINGS, ids=[s[0] for s in _SPELLINGS]
)
def test_c_record_growth_is_a_size_change_on_both_backends(
    tmp_path: Path, spelling: str, old: str, new: str
) -> None:
    _require("clang", "castxml", "gcc")
    answers = {}
    for frontend in ("castxml", "clang"):
        work = tmp_path / frontend
        work.mkdir()
        report = _compare_libraries(
            work,
            frontend=frontend,
            lang="c",
            sides=_write_pair(
                work, "c", (old, new), _SRC.format(param=_PARAM[spelling])
            ),
            debug=False,
        )
        answers[frontend] = "type_size_changed" in _kinds(report)
    assert answers == {"castxml": True, "clang": True}
