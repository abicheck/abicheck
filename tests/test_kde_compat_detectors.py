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

"""Detectors for KDE C++ binary-compatibility gaps.

Covers two rules from
https://community.kde.org/Policies/Binary_Compatibility_Issues_With_C%2B%2B that
previously had no dedicated detector:

- VIRTUAL_METHOD_ADDED — adding a virtual method to a class that already exists
  across versions ("do not add virtuals to a non-leaf class"). BREAKING. Scoped
  to the blind spot where the vtable array itself is not diff-able; when it is,
  TYPE_VTABLE_CHANGED already reports the growth.
- OVERLOAD_ADDED — adding an overload to a previously unique public name. Binary
  compatible but source-risky (`&f` ambiguity, resolution shifts).
  COMPATIBLE_WITH_RISK.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    Visibility,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _snap(
    version: str = "1.0",
    *,
    functions: list[Function] | None = None,
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        types=types or [],
    )


def _method(
    name: str, mangled: str, *, is_virtual: bool = False, params=None
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params or [],
        visibility=Visibility.PUBLIC,
        is_virtual=is_virtual,
    )


def _cls(name: str, *, vtable: list[str] | None = None) -> RecordType:
    return RecordType(name=name, kind="class", size_bits=64, vtable=vtable or [])


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ── VIRTUAL_METHOD_ADDED ─────────────────────────────────────────────────────


class TestVirtualMethodAdded:
    def test_new_virtual_on_existing_class_is_breaking(self):
        c_old = _cls("Widget")
        c_new = _cls("Widget")
        old = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[c_old],
        )
        new = _snap(
            functions=[
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
                _method("Widget::resize", "_ZN6Widget6resizeEv", is_virtual=True),
            ],
            types=[c_new],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_new_nonvirtual_method_is_compatible(self):
        """Adding a non-virtual method is a compatible addition, not a vtable break."""
        old = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[_cls("Widget")],
        )
        new = _snap(
            functions=[
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
                _method("Widget::helper", "_ZN6Widget6helperEv", is_virtual=False),
            ],
            types=[_cls("Widget")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_virtual_on_brand_new_class_is_compatible(self):
        """A new class (absent from old) with virtuals is an additive, compatible change."""
        old = _snap(functions=[], types=[])
        new = _snap(
            functions=[_method("Fresh::go", "_ZN5Fresh2goEv", is_virtual=True)],
            types=[_cls("Fresh")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)

    def test_diffable_vtable_growth_defers_to_vtable_change(self):
        """When the vtable array itself records the growth, TYPE_VTABLE_CHANGED
        owns the finding and VIRTUAL_METHOD_ADDED stays silent (no double-report).

        An anchor function keeps ``Widget`` in the ABI surface so the
        surface-scoped vtable detector engages (mirrors the oracle fixtures)."""
        anchor = Function(
            name="make",
            mangled="_Z4makev",
            return_type="Widget *",
            visibility=Visibility.PUBLIC,
        )
        old = _snap(
            functions=[
                anchor,
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
            ],
            types=[_cls("Widget", vtable=["_ZN6Widget5paintEv"])],
        )
        new = _snap(
            functions=[
                anchor,
                _method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True),
                _method("Widget::resize", "_ZN6Widget6resizeEv", is_virtual=True),
            ],
            types=[
                _cls("Widget", vtable=["_ZN6Widget5paintEv", "_ZN6Widget6resizeEv"])
            ],
        )
        result = compare(old, new)
        assert ChangeKind.TYPE_VTABLE_CHANGED in _kinds(result)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)
        assert result.verdict == Verdict.BREAKING

    def test_namespaced_owner_resolves(self):
        old = _snap(
            functions=[
                _method("kde::View::show", "_ZN3kde4View4showEv", is_virtual=True)
            ],
            types=[_cls("kde::View")],
        )
        new = _snap(
            functions=[
                _method("kde::View::show", "_ZN3kde4View4showEv", is_virtual=True),
                _method("kde::View::hide", "_ZN3kde4View4hideEv", is_virtual=True),
            ],
            types=[_cls("kde::View")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED in _kinds(result)

    def test_unchanged_class_no_finding(self):
        old = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[_cls("Widget")],
        )
        new = _snap(
            functions=[_method("Widget::paint", "_ZN6Widget5paintEv", is_virtual=True)],
            types=[_cls("Widget")],
        )
        result = compare(old, new)
        assert ChangeKind.VIRTUAL_METHOD_ADDED not in _kinds(result)


# ── OVERLOAD_ADDED ───────────────────────────────────────────────────────────


class TestOverloadAdded:
    def test_overload_added_to_unique_function_is_risk(self):
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")]),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)
        assert result.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_overload_added_to_method(self):
        old = _snap(
            functions=[
                _method("Img::at", "_ZN3Img2atEi", params=[Param(name="i", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method(
                    "Img::at", "_ZN3Img2atEi", params=[Param(name="i", type="int")]
                ),
                _method(
                    "Img::at", "_ZN3Img2atEll", params=[Param(name="i", type="long")]
                ),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED in _kinds(result)

    def test_adding_to_already_overloaded_name_is_compatible(self):
        """KDE allows adding further overloads to an already-overloaded name."""
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")]),
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")]),
                _method("draw", "_Z4drawf", params=[Param(name="x", type="float")]),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_brand_new_unique_function_is_not_overload(self):
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")]),
                _method("paint", "_Z5paintv"),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_signature_change_is_not_overload_added(self):
        """A pure signature change (remove+add of the same name) must not look
        like an overload addition: the original declaration is gone."""
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawd", params=[Param(name="x", type="double")])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_no_change_no_overload(self):
        old = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        new = _snap(
            functions=[
                _method("draw", "_Z4drawi", params=[Param(name="x", type="int")])
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)

    def test_same_leaf_different_scope_is_not_overload(self):
        """Regression for the castxml/header path: ``Function.name`` is recorded
        without namespace/class scope, so ``A::size`` and a newly added
        ``B::size`` both arrive as the leaf ``size``. Grouping must use the
        scope-qualified identity (from the mangled name) so adding ``B::size``
        does not look like a second overload of ``A::size``."""
        old = _snap(functions=[_method("size", "_ZN1A4sizeEv")])  # A::size
        new = _snap(
            functions=[
                _method("size", "_ZN1A4sizeEv"),  # A::size retained
                _method("size", "_ZN1B4sizeEv"),  # B::size added in a different scope
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)
        assert ChangeKind.FUNC_ADDED in _kinds(result)

    def test_graduated_namespace_is_not_overload(self):
        """case99 shape: a stable ``lib::sort`` is added alongside the retained
        ``lib::experimental::sort``. Different scopes → not an overload add."""
        old = _snap(functions=[_method("sort", "_ZN3lib12experimental4sortEv")])
        new = _snap(
            functions=[
                _method("sort", "_ZN3lib12experimental4sortEv"),
                _method("sort", "_ZN3lib4sortEv"),
            ]
        )
        result = compare(old, new)
        assert ChangeKind.OVERLOAD_ADDED not in _kinds(result)
