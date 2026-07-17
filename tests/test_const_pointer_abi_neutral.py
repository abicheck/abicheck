# Copyright 2026 Nikolay Petrov
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

"""Pointer/reference const-qualifier ABI-neutrality.

Real-world false positives from the conda-forge validation campaign:

* ISSUE-29 / ISSUE-52 — Wayland ``wl_display *`` → ``const wl_display *``
  parameter constness reported as a hard binary break.
* ISSUE-30 / ISSUE-35 / ISSUE-65 — libuv ``uv_cpu_info_s::model``
  ``char *`` → ``const char *`` field churn reported as a hard binary break.

Adding/removing ``const``/``volatile`` on (or behind) a pointer or reference
never changes the calling convention, the pointer width, or a struct field's
size/offset, so it is at most a source/API-signature difference — not a binary
ABI break. Top-level *by-value* const (``int`` → ``const int``) is deliberately
NOT neutralised: abicheck treats that as a source-level contract change through
the dedicated ``field_qualifiers`` detector (see ``case30_field_qualifiers``).
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
    cv_qualifiers_only_differ,
    func_signature_cv_only_differ,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _snap(version: str, *, functions=None, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        types=types or [],
    )


def _fn(name: str, mangled: str, ret: str = "void", params=None) -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret,
        params=params or [], visibility=Visibility.PUBLIC,
    )


def _rec(name: str, fields: list[TypeField], *, is_union: bool = False) -> RecordType:
    return RecordType(
        name=name,
        kind="union" if is_union else "struct",
        size_bits=64,
        fields=fields,
        is_union=is_union,
    )


def _dwarf_snap(version: str, structs: dict[str, StructLayout]) -> AbiSnapshot:
    snap = AbiSnapshot(library="libtest.so", version=version)
    snap.dwarf = DwarfMetadata(has_dwarf=True, structs=structs)  # type: ignore[attr-defined]
    return snap


def _kinds(result) -> set[ChangeKind]:
    return {c.kind for c in result.changes}


# ── the predicate ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("old_t, new_t", [
    ("char *", "const char *"),
    ("const char *", "char *"),
    ("wl_display *", "const wl_display *"),
    ("int *", "int * const"),
    ("void*", "const void*"),
    ("int *", "volatile int *"),
    ("Foo &", "const Foo &"),
])
def test_pointer_cv_only_difference_is_detected(old_t, new_t):
    assert cv_qualifiers_only_differ(old_t, new_t) is True


@pytest.mark.parametrize("old_t, new_t", [
    # By-value top-level const is a source-level contract change, handled by the
    # field_qualifiers detector — must NOT be neutralised here.
    ("int", "const int"),
    ("volatile int", "int"),
    # Nested ``*``/``&`` inside a template argument or function-parameter list is
    # NOT a top-level pointer/reference: the type is passed/stored by value, so a
    # top-level const change on it must remain reported (reviewer edge case).
    ("Box<int *>", "const Box<int *>"),
    ("std::function<void(const int&)>", "std::function<void(int&)>"),
    ("std::array<char *, 4>", "const std::array<char *, 4>"),
    # Genuine type substitutions remain real differences.
    ("int *", "long *"),
    ("char *", "char **"),
    ("Foo *", "Bar *"),
    # Identical spellings are not a "difference".
    ("Foo *", "Foo *"),
    ("const char *", "const char *"),
])
def test_non_cv_only_difference_is_not_neutralised(old_t, new_t):
    assert cv_qualifiers_only_differ(old_t, new_t) is False


def test_top_level_reference_const_is_neutralised():
    # A reference whose top-level declarator is `&` is binary-neutral under const.
    assert cv_qualifiers_only_differ("vector<int> &", "const vector<int> &") is True


# ── parameters (ISSUE-29 / ISSUE-52) ──────────────────────────────────────────


def test_param_pointee_const_added_is_not_breaking():
    old = _snap("1", functions=[
        _fn("wl_display_flush", "wl_display_flush",
            params=[Param(name="d", type="wl_display *")]),
    ])
    new = _snap("2", functions=[
        _fn("wl_display_flush", "wl_display_flush",
            params=[Param(name="d", type="const wl_display *")]),
    ])
    r = compare(old, new)
    assert ChangeKind.FUNC_PARAMS_CHANGED not in _kinds(r)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


def test_param_real_pointee_change_still_breaking():
    # Negative control: a genuine pointee-type change is still a break.
    old = _snap("1", functions=[
        _fn("f", "f", params=[Param(name="p", type="int *")]),
    ])
    new = _snap("2", functions=[
        _fn("f", "f", params=[Param(name="p", type="long *")]),
    ])
    r = compare(old, new)
    assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(r)
    assert r.verdict == Verdict.BREAKING


# ── return types ──────────────────────────────────────────────────────────────


def test_return_pointee_const_added_is_not_breaking():
    old = _snap("1", functions=[_fn("get", "get", ret="char *")])
    new = _snap("2", functions=[_fn("get", "get", ret="const char *")])
    r = compare(old, new)
    assert ChangeKind.FUNC_RETURN_CHANGED not in _kinds(r)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


def test_return_real_change_still_breaking():
    old = _snap("1", functions=[_fn("get", "get", ret="int *")])
    new = _snap("2", functions=[_fn("get", "get", ret="float *")])
    r = compare(old, new)
    assert ChangeKind.FUNC_RETURN_CHANGED in _kinds(r)


# ── struct fields via diff_types (header / typed snapshot path) ────────────────


def test_type_field_pointee_const_change_is_not_breaking():
    old = _snap("1",
                functions=[_fn("api", "api", ret="Conf *")],
                types=[_rec("Conf", [TypeField(name="model", type="char *", offset_bits=0)])])
    new = _snap("2",
                functions=[_fn("api", "api", ret="Conf *")],
                types=[_rec("Conf", [TypeField(name="model", type="const char *", offset_bits=0)])])
    r = compare(old, new)
    assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in _kinds(r)


def test_type_field_real_change_still_breaking():
    old = _snap("1",
                functions=[_fn("api", "api", ret="Conf *")],
                types=[_rec("Conf", [TypeField(name="n", type="int", offset_bits=0)])])
    new = _snap("2",
                functions=[_fn("api", "api", ret="Conf *")],
                types=[_rec("Conf", [TypeField(name="n", type="float", offset_bits=0)])])
    r = compare(old, new)
    assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(r)


def test_union_field_pointee_const_change_is_not_breaking():
    old = _snap("1",
                functions=[_fn("api", "api", ret="U *")],
                types=[_rec("U", [TypeField(name="p", type="char *", offset_bits=0)], is_union=True)])
    new = _snap("2",
                functions=[_fn("api", "api", ret="U *")],
                types=[_rec("U", [TypeField(name="p", type="const char *", offset_bits=0)], is_union=True)])
    r = compare(old, new)
    assert ChangeKind.UNION_FIELD_TYPE_CHANGED not in _kinds(r)


# ── struct fields via diff_platform (DWARF layout path) ───────────────────────


def test_dwarf_struct_field_pointee_const_change_is_not_breaking():
    # ISSUE-30/35/65: libuv uv_cpu_info_s::model char* -> const char*.
    old_s = StructLayout(name="uv_cpu_info_s", byte_size=24, fields=[
        FieldInfo(name="model", type_name="char *", byte_offset=0, byte_size=8),
    ])
    new_s = StructLayout(name="uv_cpu_info_s", byte_size=24, fields=[
        FieldInfo(name="model", type_name="const char *", byte_offset=0, byte_size=8),
    ])
    r = compare(_dwarf_snap("1", {"uv_cpu_info_s": old_s}),
                _dwarf_snap("2", {"uv_cpu_info_s": new_s}))
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED not in _kinds(r)


def test_dwarf_struct_field_const_with_size_change_still_breaking():
    # A const change that *also* changes the field size is still reported
    # (the size component is a genuine layout break).
    old_s = StructLayout(name="S", byte_size=8, fields=[
        FieldInfo(name="v", type_name="int", byte_offset=0, byte_size=4),
    ])
    new_s = StructLayout(name="S", byte_size=8, fields=[
        FieldInfo(name="v", type_name="const long *", byte_offset=0, byte_size=8),
    ])
    r = compare(_dwarf_snap("1", {"S": old_s}), _dwarf_snap("2", {"S": new_s}))
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in _kinds(r)


def test_dwarf_struct_field_real_pointee_change_still_breaking():
    old_s = StructLayout(name="S", byte_size=8, fields=[
        FieldInfo(name="p", type_name="int *", byte_offset=0, byte_size=8),
    ])
    new_s = StructLayout(name="S", byte_size=8, fields=[
        FieldInfo(name="p", type_name="float *", byte_offset=0, byte_size=8),
    ])
    r = compare(_dwarf_snap("1", {"S": old_s}), _dwarf_snap("2", {"S": new_s}))
    assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in _kinds(r)


# ── top-level field const is still a (source) break (case30 guard) ────────────


def test_top_level_field_const_is_not_neutralised():
    # Regression guard: int -> const int (by value, no indirection) must remain
    # a reported field-type change — neutralising it would silently drop the
    # case30_field_qualifiers source-break escalation. Indirection (``*``/``&``)
    # is what makes a const change binary-neutral; a by-value field has none.
    old = _snap("1",
                functions=[_fn("api", "api", ret="Sensor *")],
                types=[_rec("Sensor", [TypeField(name="rate", type="int", offset_bits=0)])])
    new = _snap("2",
                functions=[_fn("api", "api", ret="Sensor *")],
                types=[_rec("Sensor", [TypeField(name="rate", type="const int", offset_bits=0)])])
    r = compare(old, new)
    assert ChangeKind.TYPE_FIELD_TYPE_CHANGED in _kinds(r)


# ── top-level by-value param/return cv IS neutralised (unlike fields) ─────────
#
# Unlike a field/variable, a function's own by-value parameter or return-type
# cv-qualifier has zero ABI/mangling effect (`void f(int)` and `void f(const
# int)` name the same function) and no dedicated compatible-classified
# detector to escalate through — so, in contrast to case30 above, these MUST
# be neutralised (Codex review, PR #582).


@pytest.mark.parametrize("old_t, new_t", [
    ("int", "volatile int"),
    ("int", "const int"),
    ("volatile int", "int"),
])
def test_func_signature_cv_only_differ_detects_by_value_change(old_t, new_t):
    assert func_signature_cv_only_differ(old_t, new_t) is True


@pytest.mark.parametrize("old_t, new_t", [
    ("int", "long"),
    ("Foo", "Foo"),
])
def test_func_signature_cv_only_differ_rejects_non_cv_or_identical(old_t, new_t):
    assert func_signature_cv_only_differ(old_t, new_t) is False


def test_param_by_value_volatile_added_is_not_breaking():
    old = _snap("1", functions=[_fn("f", "f", params=[Param(name="x", type="int")])])
    new = _snap("2", functions=[_fn("f", "f", params=[Param(name="x", type="volatile int")])])
    r = compare(old, new)
    assert ChangeKind.FUNC_PARAMS_CHANGED not in _kinds(r)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


def test_param_by_value_const_added_is_not_breaking():
    old = _snap("1", functions=[_fn("f", "f", params=[Param(name="x", type="int")])])
    new = _snap("2", functions=[_fn("f", "f", params=[Param(name="x", type="const int")])])
    r = compare(old, new)
    assert ChangeKind.FUNC_PARAMS_CHANGED not in _kinds(r)


def test_return_by_value_volatile_added_is_not_breaking():
    old = _snap("1", functions=[_fn("get", "get", ret="int")])
    new = _snap("2", functions=[_fn("get", "get", ret="volatile int")])
    r = compare(old, new)
    assert ChangeKind.FUNC_RETURN_CHANGED not in _kinds(r)
    assert r.verdict in (Verdict.NO_CHANGE, Verdict.COMPATIBLE)


def test_param_by_value_real_type_change_still_breaking():
    """Negative control: a by-value cv change must be neutralised, but a
    genuine by-value type substitution must still be reported."""
    old = _snap("1", functions=[_fn("f", "f", params=[Param(name="x", type="int")])])
    new = _snap("2", functions=[_fn("f", "f", params=[Param(name="x", type="long")])])
    r = compare(old, new)
    assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(r)


@pytest.mark.parametrize("old_t, new_t", [
    ("Box<const int>", "Box<int>"),
    ("Box< const int >", "Box< int >"),
    ("Box<int, const int>", "Box<int, int>"),
    ("std::function<void(const int)>", "std::function<void(int)>"),
])
def test_nested_template_cv_qualifier_is_not_neutralised(old_t, new_t):
    """Regression guard (Codex/CodeRabbit review, PR #582):
    ``_strip_cv_qualifiers`` used to strip const/volatile tokens at ANY
    nesting depth, so a cv qualifier hidden inside a template argument
    (``Box<const int>`` vs ``Box<int>``) — a genuinely different,
    unrelated C++ type, not a cv-qualified variant of the same type — was
    misclassified as a harmless cv-only difference by both
    ``cv_qualifiers_only_differ`` and ``func_signature_cv_only_differ``.
    Depending on the exact spelling this could silently suppress a real
    breaking ``FUNC_PARAMS_CHANGED``/``FUNC_RETURN_CHANGED`` finding."""
    assert func_signature_cv_only_differ(old_t, new_t) is False
    assert cv_qualifiers_only_differ(old_t + "*", new_t + "*") is False


def test_nested_template_cv_change_still_reported_as_param_change():
    """End-to-end: a function parameter changing from one template
    instantiation to a different one (only distinguished by a nested cv
    qualifier) must still report FUNC_PARAMS_CHANGED, not be silently
    neutralised as cv-only churn."""
    old = _snap("1", functions=[_fn("f", "f", params=[Param(name="x", type="Box<int, int>")])])
    new = _snap("2", functions=[_fn("f", "f", params=[Param(name="x", type="Box<int, const int>")])])
    r = compare(old, new)
    assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(r)


@pytest.mark.parametrize("old_t, new_t", [
    ("void (*)(int)", "void (*)(const int)"),
    ("void (*)(int, int)", "void (*)(int, const int)"),
    ("void (*)(int* const)", "void (*)(int*)"),
    ("void (*)(int* volatile)", "void (*)(int*)"),
    ("void (*)(void (*)(int))", "void (*)(void (*)(const int))"),
    ("void (*)(int*, int)", "void (*)(int*, const int)"),
    ("void (*)(int, int*)", "void (*)(const int, int*)"),
    ("void (*)(int*, int*)", "void (*)(int* const, int*)"),
])
def test_callback_by_value_cv_qualifier_is_neutralised(old_t, new_t):
    """Regression guard (Codex review, PR #582): the fix for nested TEMPLATE
    cv (``Box<const int>``, above) over-corrected by also blocking stripping
    inside a nested PAREN — a callback/function-pointer parameter's own
    by-value cv qualifier. But confirmed against real clang/gcc mangling,
    ``void (*)(int)`` and ``void (*)(const int)`` are the identical function
    pointer type: top-level cv on a by-value (or pointer-own, trailing
    ``* const``/``* volatile``) parameter is dropped for mangling purposes at
    every nesting level of a function type, not just the outermost — at
    every level of nesting (a callback-of-a-callback), and independently per
    comma-separated parameter within one callback's own parameter list (a
    later/earlier sibling parameter's unrelated pointer sigil must not affect
    this one's own verdict). Blocking the callback's own cv this way made an
    ABI-neutral header edit misfire the breaking FUNC_PARAMS_CHANGED path."""
    assert func_signature_cv_only_differ(old_t, new_t) is True


@pytest.mark.parametrize("old_t, new_t", [
    ("void (*)(int*)", "void (*)(long*)"),
    ("void (*)(int*)", "void (*)(const int*)"),
    ("void (*)(void (*)(int*))", "void (*)(void (*)(const int*))"),
    ("void (*)(int*, int*)", "void (*)(int*, const int*)"),
    ("void (*)(int[3])", "void (*)(const int[3])"),
    ("void (*)(int[3], int)", "void (*)(const int[3], int)"),
])
def test_callback_pointee_cv_qualifier_is_not_neutralised(old_t, new_t):
    """Negative control (Codex review, PR #589): a callback parameter's
    POINTEE cv IS a genuinely different type (confirmed against real
    mangling: ``void(*)(int*)`` and ``void(*)(const int*)`` are different,
    non-interchangeable function pointer types — unlike an ordinary
    top-level parameter, where ``T*`` implicitly converts to ``const T*``,
    a callback supplied by the caller with one pointee-cv signature can't be
    passed where the other is expected) — only the callback's own by-value/
    pointer-value cv is neutral, not its pointee's, at any nesting depth or
    parameter position. An array-typed parameter decays to a pointer too
    (``void(*)(int[3])`` and ``void(*)(const int[3])`` are confirmed by real
    mangling to be equally non-interchangeable, Codex review round 2) — the
    ``[...]`` handling must treat it the same as a real pointer sigil rather
    than as an opaque, ptr-blind skip. An earlier version of the fix above
    stripped cv indiscriminately inside any paren, wrongly neutralizing this
    case too."""
    assert func_signature_cv_only_differ(old_t, new_t) is False


def test_callback_cv_change_still_reported_as_param_change():
    """End-to-end: an outer function whose callback parameter's own by-value
    cv changed must NOT report FUNC_PARAMS_CHANGED (ABI-neutral), but a
    genuine callback signature change must still be reported."""
    neutral_old = _snap(
        "1", functions=[_fn("f", "f", params=[Param(name="cb", type="void (*)(int)")])]
    )
    neutral_new = _snap(
        "2", functions=[_fn("f", "f", params=[Param(name="cb", type="void (*)(const int)")])]
    )
    r = compare(neutral_old, neutral_new)
    assert ChangeKind.FUNC_PARAMS_CHANGED not in _kinds(r)

    breaking_old = _snap(
        "1", functions=[_fn("f", "f", params=[Param(name="cb", type="void (*)(int)")])]
    )
    breaking_new = _snap(
        "2", functions=[_fn("f", "f", params=[Param(name="cb", type="void (*)(long)")])]
    )
    r2 = compare(breaking_old, breaking_new)
    assert ChangeKind.FUNC_PARAMS_CHANGED in _kinds(r2)
