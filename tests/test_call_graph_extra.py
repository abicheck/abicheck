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

"""Sibling split of ``test_call_graph.py`` (which sits at its 2000-line hard
cap) for ``_ref_is_virtual``'s own coverage: whether a member call whose
resolved static target is itself an override classifies as virtual, and the
known residual gap for an override redeclared with neither ``override``/
``final`` nor a repeated ``"virtual": true``."""

from __future__ import annotations

from abicheck.buildsource.call_graph import (
    CALL_KIND_DIRECT,
    CALL_KIND_VIRTUAL,
    RESOLUTION_EXACT,
    RESOLUTION_OVERAPPROX,
    CallEdge,
    parse_clang_ast_calls,
)


def _b_d_override_call_ast(d_f_inner: list[dict]) -> dict:
    """``struct B { virtual void f(); }; struct D : B { void f()<extra>;
    void h(){ f(); } };`` -- shared by the two ``_ref_is_virtual`` tests
    below, which differ only in ``D::f``'s own ``inner`` list. Neither
    carries ``"virtual": true`` on ``D::f``: clang only repeats that flag on
    the slot's original declaring ancestor (real Clang 17 output)."""
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "CXXRecordDecl",
                "name": "B",
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xb",
                        "name": "f",
                        "mangledName": "_ZN1B1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    }
                ],
            },
            {
                "kind": "CXXRecordDecl",
                "name": "D",
                "bases": [{"type": {"qualType": "B"}}],
                "inner": [
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xd",
                        "name": "f",
                        "mangledName": "_ZN1D1fEv",
                        "type": {"qualType": "void ()"},
                        "inner": d_f_inner,
                    },
                    {
                        "kind": "CXXMethodDecl",
                        "id": "0xh",
                        "name": "h",
                        "mangledName": "_ZN1D1hEv",
                        "type": {"qualType": "void ()"},
                        "inner": [
                            {
                                "kind": "CompoundStmt",
                                "inner": [
                                    {
                                        "kind": "CXXMemberCallExpr",
                                        "inner": [
                                            {
                                                "kind": "MemberExpr",
                                                "referencedMemberDecl": "0xd",
                                                "inner": [{"kind": "CXXThisExpr"}],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    },
                ],
            },
        ],
    }


def test_parse_call_to_override_without_own_virtual_flag_is_still_virtual() -> None:
    """Codex review, fresh evidence (see ``_b_d_override_call_ast``): when the
    resolved static target of a member call is itself an override, reading
    only ``ref.get("virtual")`` misclassified the call as
    ``CALL_KIND_DIRECT``/``RESOLUTION_EXACT`` -- excluding a real
    further-derived-override chain from ``VIRTUAL_CALL_MAY_DISPATCH_TO``
    entirely. ``D::f`` carries an ``OverrideAttr`` but no ``"virtual": true``
    of its own."""
    edges = parse_clang_ast_calls(_b_d_override_call_ast([{"kind": "OverrideAttr"}]))
    assert edges == [
        CallEdge(
            "_ZN1D1hEv",
            "_ZN1D1fEv",
            CALL_KIND_VIRTUAL,
            RESOLUTION_OVERAPPROX,
        )
    ]


def test_parse_call_to_implicit_override_with_no_keyword_is_a_known_false_negative() -> (
    None
):
    """Codex review, fresh evidence: a derived method redeclaring a virtual
    signature with NEITHER ``override``/``final`` NOR a repeated
    ``"virtual": true`` carries neither of ``_ref_is_virtual``'s two
    signals, so this call reads ``direct``/``exact`` instead of
    ``virtual``/``overapprox`` -- an already-documented residual gap
    (``_ref_is_virtual``'s own docstring). Deliberately not closed here:
    knowing ``D::f`` overrides ``B::f`` without either local signal needs
    the same whole-hierarchy walk ``override_graph.py`` already performs
    (``parse_clang_ast_virtual_methods``); threading it in here would form
    an import cycle with the reverse, already-established direction (same
    docstring). Pinned as ADR-028 D3's conservative false-negative default,
    not silently accepted."""
    edges = parse_clang_ast_calls(_b_d_override_call_ast([]))
    assert edges == [
        CallEdge(
            "_ZN1D1hEv",
            "_ZN1D1fEv",
            CALL_KIND_DIRECT,
            RESOLUTION_EXACT,
        )
    ]
