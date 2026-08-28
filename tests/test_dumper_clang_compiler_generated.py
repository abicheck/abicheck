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

"""``Function.is_compiler_generated`` on the direct-clang L2 backend — closes
the castxml L4 extractor bug documented in ``AGENTS.md``'s "PR C" known-gaps
entry. Split out of ``test_dumper_clang.py`` to keep that module under the
AI-readiness file-size hard cap; see ``test_castxml_compiler_generated.py``
(the castxml parser level) and ``test_castxml_l4_phantom_members.py`` (the
real end-to-end L4 extraction) for this same fix's other test coverage.
"""

from __future__ import annotations

from abicheck.dumper_clang import _ClangAstParser


def _tu(*inner: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(inner)}


def test_parse_functions_is_never_compiler_generated() -> None:
    # Unlike castxml, this backend's own `_walk` skips `_categorize`
    # entirely whenever a node is `isImplicit` (before it can ever become a
    # Function at all) -- so every Function this backend produces is
    # structurally guaranteed to have been written by the user.
    # `is_compiler_generated` is therefore stamped unconditionally False,
    # not derived per-node.
    root = _tu(
        {
            "kind": "FunctionDecl",
            "name": "add",
            "loc": {"file": "include/foo.h", "line": 3},
            "mangledName": "_Z3addii",
            "type": {"qualType": "int (int, int)"},
        }
    )
    (fn,) = _ClangAstParser(root, {"_Z3addii"}, set()).parse_functions()
    assert fn.is_compiler_generated is False


def test_parse_functions_skips_implicit_declarations_entirely() -> None:
    # The structural guarantee the previous test relies on, made explicit:
    # a node marked isImplicit (a compiler-synthesized default constructor,
    # in this fixture) never reaches parse_functions()'s output at all.
    root = _tu(
        {
            "kind": "CXXRecordDecl",
            "name": "Widget",
            "loc": {"file": "include/widget.h", "line": 1},
            "inner": [
                {
                    "kind": "CXXConstructorDecl",
                    "name": "Widget",
                    "isImplicit": True,
                    "loc": {"file": "include/widget.h", "line": 1},
                    "mangledName": "_ZN6WidgetC1Ev",
                    "type": {"qualType": "void ()"},
                }
            ],
        }
    )
    assert _ClangAstParser(root, {"_ZN6WidgetC1Ev"}, set()).parse_functions() == []
