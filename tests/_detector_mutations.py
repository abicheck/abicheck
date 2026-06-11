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

"""Shared catalogue of controlled ABI mutations with their *oracle* outcomes.

A mutation takes a unique ``tag`` and returns ``(old_extra, new_extra,
expected_kind, is_breaking)``: two snapshot *fragments* that differ by exactly
one known edit, plus the ``ChangeKind`` that edit must produce and whether the
verdict must be breaking. Each fragment is a dict with any of the keys
``functions`` / ``types`` / ``enums`` / ``variables``.

Two test modules consume this:

* ``test_detector_oracle.py`` — deterministic, fast, **in mutmut's scope** so
  mutation testing actually measures these oracle assertions;
* ``test_detector_properties.py`` — wraps each mutation in a Hypothesis-randomized
  context (``slow``) to check the same oracle holds for any surroundings.

Every mapping here was verified against the live detectors before being asserted.
"""
from __future__ import annotations

from collections.abc import Callable

from abicheck.checker_policy import ChangeKind
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

# Context identifiers are prefixed so they can never collide with a mutation's
# target identifiers ("tgt"/"Tgt"); used by the false-positive guard.
CTX_PREFIX = "ctx_"

# A mutation builder: tag -> (old_extra, new_extra, expected_kind, is_breaking).
Mutation = Callable[[int], "tuple[dict, dict, ChangeKind, bool]"]


def _api(tag: int, ret: str = "void", params: tuple[str, ...] = ()) -> Function:
    """A public function that keeps a target type/enum reachable (in-surface).

    It is identical on both sides of a comparison, so it never itself produces a
    change — only the target it references does.
    """
    return Function(
        name=f"tgt_api_{tag}",
        mangled=f"_Z9tgt_api_{tag}E",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _m_func_param_changed(tag: int):
    def mk(ptype: str) -> dict:
        return {"functions": [
            Function(name=f"tgt_{tag}", mangled=f"_Z5tgt_{tag}i", return_type="void",
                     params=[Param(name="a", type=ptype)], visibility=Visibility.PUBLIC)]}

    return mk("int"), mk("long long"), ChangeKind.FUNC_PARAMS_CHANGED, True


def _m_func_return_changed(tag: int):
    base = dict(name=f"tgt_{tag}", mangled=f"_Z5tgt_{tag}v", params=[],
                visibility=Visibility.PUBLIC)
    return ({"functions": [Function(return_type="int", **base)]},
            {"functions": [Function(return_type="long long", **base)]},
            ChangeKind.FUNC_RETURN_CHANGED, True)


def _m_func_removed(tag: int):
    fn = Function(name=f"tgt_{tag}", mangled=f"_Z5tgt_{tag}v", return_type="void",
                  visibility=Visibility.PUBLIC)
    return {"functions": [fn]}, {"functions": []}, ChangeKind.FUNC_REMOVED, True


def _m_func_added(tag: int):
    fn = Function(name=f"tgt_{tag}", mangled=f"_Z5tgt_{tag}v", return_type="void",
                  visibility=Visibility.PUBLIC)
    return {"functions": []}, {"functions": [fn]}, ChangeKind.FUNC_ADDED, False


def _m_noexcept_added(tag: int):
    base = dict(name=f"tgt_{tag}", mangled=f"_Z5tgt_{tag}v", return_type="void",
                visibility=Visibility.PUBLIC)
    return ({"functions": [Function(is_noexcept=False, **base)]},
            {"functions": [Function(is_noexcept=True, **base)]},
            ChangeKind.FUNC_NOEXCEPT_ADDED, False)


def _m_struct_size_changed(tag: int):
    name = f"Tgt{tag}"
    api = _api(tag, ret=f"{name} *")
    return ({"functions": [api], "types": [RecordType(name=name, kind="struct", size_bits=64)]},
            {"functions": [api], "types": [RecordType(name=name, kind="struct", size_bits=128)]},
            ChangeKind.TYPE_SIZE_CHANGED, True)


def _m_field_type_changed(tag: int):
    name = f"Tgt{tag}"
    api = _api(tag, ret=f"{name} *")

    def mk(ftype: str) -> RecordType:
        return RecordType(name=name, kind="struct", size_bits=64,
                          fields=[TypeField(name="x", type=ftype)])

    return ({"functions": [api], "types": [mk("int")]},
            {"functions": [api], "types": [mk("double")]},
            ChangeKind.TYPE_FIELD_TYPE_CHANGED, True)


def _m_enum_value_changed(tag: int):
    name = f"Tgt{tag}"
    api = _api(tag, params=(name,))

    def mk(v: int) -> EnumType:
        return EnumType(name=name,
                        members=[EnumMember(name="A", value=0), EnumMember(name="B", value=v)],
                        underlying_type="int")

    return ({"functions": [api], "enums": [mk(1)]},
            {"functions": [api], "enums": [mk(2)]},
            ChangeKind.ENUM_MEMBER_VALUE_CHANGED, True)


def _m_enum_member_added(tag: int):
    name = f"Tgt{tag}"
    api = _api(tag, params=(name,))
    return ({"functions": [api], "enums": [EnumType(name=name,
                members=[EnumMember(name="A", value=0)], underlying_type="int")]},
            {"functions": [api], "enums": [EnumType(name=name,
                members=[EnumMember(name="A", value=0), EnumMember(name="B", value=1)],
                underlying_type="int")]},
            ChangeKind.ENUM_MEMBER_ADDED, False)


def _m_var_removed(tag: int):
    v = Variable(name=f"tgt_{tag}", mangled=f"_ZV5tgt_{tag}", type="int",
                 visibility=Visibility.PUBLIC)
    return {"variables": [v]}, {"variables": []}, ChangeKind.VAR_REMOVED, True


# --- C++ vtable / inheritance edits (real ABI breaks the flat C cases miss) ---

def _m_vtable_method_added(tag: int):
    name = f"Tgt{tag}"
    api = _api(tag, ret=f"{name} *")

    def cls(vtable: list[str]) -> RecordType:
        return RecordType(name=name, kind="class", size_bits=64, vtable=vtable)

    return ({"functions": [api], "types": [cls(["foo()"])]},
            {"functions": [api], "types": [cls(["foo()", "bar()"])]},
            ChangeKind.TYPE_VTABLE_CHANGED, True)


def _m_base_class_added(tag: int):
    name, base = f"Tgt{tag}", f"Base{tag}"
    api = _api(tag, ret=f"{name} *")
    base_t = RecordType(name=base, kind="class", size_bits=8)  # present both sides

    def cls(bases: list[str]) -> RecordType:
        return RecordType(name=name, kind="class", size_bits=64, vtable=["foo()"], bases=bases)

    return ({"functions": [api], "types": [cls([]), base_t]},
            {"functions": [api], "types": [cls([base]), base_t]},
            ChangeKind.TYPE_BASE_CHANGED, True)


def _m_method_became_virtual(tag: int):
    base = dict(name=f"C{tag}::foo", mangled=f"_ZN1C{tag}3fooEv", return_type="void",
                visibility=Visibility.PUBLIC, access=AccessLevel.PUBLIC)
    return ({"functions": [Function(is_virtual=False, **base)]},
            {"functions": [Function(is_virtual=True, **base)]},
            ChangeKind.FUNC_VIRTUAL_ADDED, True)


def _m_method_became_pure(tag: int):
    base = dict(name=f"C{tag}::foo", mangled=f"_ZN1C{tag}3fooEv", return_type="void",
                visibility=Visibility.PUBLIC, access=AccessLevel.PUBLIC, is_virtual=True)
    return ({"functions": [Function(is_pure_virtual=False, **base)]},
            {"functions": [Function(is_pure_virtual=True, **base)]},
            ChangeKind.FUNC_VIRTUAL_BECAME_PURE, True)


def _m_virtual_method_added(tag: int):
    """A new virtual method on a pre-existing class whose vtable array does not
    record the growth (DWARF/symbol-only blind spot) → VIRTUAL_METHOD_ADDED."""
    cls_name = f"Cv{tag}"
    n = len(cls_name)  # Itanium source-name length prefix (valid for multi-digit tags)
    cls = RecordType(name=cls_name, kind="class", size_bits=64, vtable=[])
    keep = Function(name=f"{cls_name}::foo", mangled=f"_ZN{n}{cls_name}3fooEv", return_type="void",
                    visibility=Visibility.PUBLIC, access=AccessLevel.PUBLIC, is_virtual=True)
    new = Function(name=f"{cls_name}::bar", mangled=f"_ZN{n}{cls_name}3barEv", return_type="void",
                   visibility=Visibility.PUBLIC, access=AccessLevel.PUBLIC, is_virtual=True)
    return ({"functions": [keep], "types": [cls]},
            {"functions": [keep, new], "types": [cls]},
            ChangeKind.VIRTUAL_METHOD_ADDED, True)


def _m_overload_added(tag: int):
    """A second overload added to a previously unique public name → OVERLOAD_ADDED.

    The finding attaches to the *original* declaration, so the reverse edit (a
    removal of the new overload) surfaces a different symbol — direction
    symmetry does not hold; see ASYMMETRIC below."""
    base = f"ov{tag}"
    n = len(base)  # Itanium source-name length prefix (valid for multi-digit tags)
    f1 = Function(name=base, mangled=f"_Z{n}{base}i", return_type="void",
                  params=[Param(name="a", type="int")], visibility=Visibility.PUBLIC)
    f2 = Function(name=base, mangled=f"_Z{n}{base}d", return_type="void",
                  params=[Param(name="a", type="double")], visibility=Visibility.PUBLIC)
    return ({"functions": [f1]}, {"functions": [f1, f2]},
            ChangeKind.OVERLOAD_ADDED, False)


# Mutations whose reverse is legitimately a non-change, so touched-symbol
# direction-symmetry does NOT hold and must not be asserted: making a virtual
# method pure is a break, but the reverse (providing a concrete implementation)
# is ABI-compatible and emits nothing. Adding an overload flags the original
# declaration, but the reverse (removing the new overload) touches a different
# symbol, so it is asymmetric too.
ASYMMETRIC = {"_m_method_became_pure", "_m_overload_added"}


MUTATIONS: list[Mutation] = [
    _m_func_param_changed,
    _m_func_return_changed,
    _m_func_removed,
    _m_func_added,
    _m_noexcept_added,
    _m_struct_size_changed,
    _m_field_type_changed,
    _m_enum_value_changed,
    _m_enum_member_added,
    _m_var_removed,
    _m_vtable_method_added,
    _m_base_class_added,
    _m_method_became_virtual,
    _m_method_became_pure,
    _m_virtual_method_added,
    _m_overload_added,
]


def build_snapshot(version: str, context: dict, extra: dict) -> AbiSnapshot:
    """Merge a shared *context* with a mutation *extra* into a snapshot."""
    return AbiSnapshot(
        library="liboracle.so.1",
        version=version,
        functions=context.get("functions", []) + extra.get("functions", []),
        types=context.get("types", []) + extra.get("types", []),
        enums=context.get("enums", []) + extra.get("enums", []),
        variables=context.get("variables", []) + extra.get("variables", []),
    )


def context_identifiers(context: dict) -> set[str]:
    """Names/mangleds of context symbols — used to assert they stay unflagged."""
    out: set[str] = set()
    for fn in context.get("functions", []):
        out.add(fn.name)
        out.add(fn.mangled)
    for t in context.get("types", []):
        out.add(t.name)
    return out
