"""Unit cases for ``extract/dwarf_member_functions.py`` on hand-built DIEs.

The compiled-library tests (``tests/test_dwarf_member_functions.py``) reach
the shapes gcc and clang emit; these pin the branches a producer may write
that those two compilers do not (a broken reference, an abstract instance,
a ``this`` with no pointer type, a declaration met after its definition),
each against the answer the DWARF standard gives, not the module's helpers.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from abicheck.extract import dwarf_member_functions as members
from abicheck.model.availability import FactStatus
from abicheck.model.identity import Namespace, Record


class _CU:
    """A compile unit that resolves CU-relative references by offset."""

    cu_offset = 0

    def __init__(self) -> None:
        self.dies: dict[int, Any] = {}

    def get_DIE_from_refaddr(self, offset: int) -> Any:  # noqa: N802 -- pyelftools name
        return self.dies[offset]


def _die(
    cu: _CU,
    offset: int,
    tag: str,
    attrs: dict[str, Any] | None = None,
    children: tuple[Any, ...] = (),
) -> SimpleNamespace:
    die = SimpleNamespace(
        offset=offset,
        tag=tag,
        attributes={
            k: SimpleNamespace(
                value=v, form="DW_FORM_ref4" if isinstance(v, _Ref) else "DW_FORM_data1"
            )
            for k, v in (attrs or {}).items()
        },
        iter_children=lambda: iter(children),
    )
    for attr in die.attributes.values():
        if isinstance(attr.value, _Ref):
            attr.value = attr.value.offset
    cu.dies[offset] = die
    return die


class _Ref:
    def __init__(self, offset: int) -> None:
        self.offset = offset


def _this_param(cu: _CU, offset: int, pointee_quals: tuple[str, ...]) -> Any:
    """``T quals *`` at *offset*: a pointer to a chain of cv DIEs ending at a class."""
    _die(cu, offset + 50, "DW_TAG_class_type", {"DW_AT_name": "C"})
    target = offset + 50
    for i, q in enumerate(pointee_quals):
        tag = {"const": "DW_TAG_const_type", "volatile": "DW_TAG_volatile_type"}[q]
        _die(cu, offset + 40 + i, tag, {"DW_AT_type": _Ref(target)})
        target = offset + 40 + i
    _die(cu, offset + 30, "DW_TAG_pointer_type", {"DW_AT_type": _Ref(target)})
    return _die(
        cu,
        offset,
        "DW_TAG_formal_parameter",
        {"DW_AT_type": _Ref(offset + 30), "DW_AT_artificial": True},
    )


@pytest.mark.parametrize(
    ("quals", "expected"),
    [
        ((), (True, False, False)),
        (("const",), (True, True, False)),
        (("volatile",), (True, False, True)),
        (("const", "volatile"), (True, True, True)),
    ],
)
def test_qualifiers_are_read_from_the_this_pointee(
    quals: tuple[str, ...], expected: tuple[bool, bool, bool]
) -> None:
    cu = _CU()
    this = _this_param(cu, 100, quals)
    decl = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_declaration": True}, (this,))
    q = members.member_qualifiers(decl, cu)
    assert (q.has_this, q.is_const, q.is_volatile) == expected


@pytest.mark.parametrize(
    ("attr", "expected"),
    [("DW_AT_reference", "&"), ("DW_AT_rvalue_reference", "&&"), (None, "")],
)
def test_ref_qualifier_is_read_from_the_dwarf5_attribute(
    attr: str | None, expected: str
) -> None:
    cu = _CU()
    this = _this_param(cu, 100, ())
    attrs: dict[str, Any] = {"DW_AT_declaration": True}
    if attr:
        attrs[attr] = True
    decl = _die(cu, 1, "DW_TAG_subprogram", attrs, (this,))
    assert members.member_qualifiers(decl, cu).ref_qualifier == expected


def test_object_pointer_attribute_names_this() -> None:
    cu = _CU()
    this = _this_param(cu, 100, ("const",))
    decl = _die(
        cu,
        1,
        "DW_TAG_subprogram",
        {"DW_AT_declaration": True, "DW_AT_object_pointer": _Ref(100)},
        (),
    )
    assert this is cu.dies[100]
    assert members.member_qualifiers(decl, cu).is_const is True


def test_no_artificial_parameter_is_a_static_member() -> None:
    cu = _CU()
    plain = _die(cu, 5, "DW_TAG_formal_parameter", {"DW_AT_name": "x"})
    decl = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_declaration": True}, (plain,))
    scoped = members.qualifiers_in_scope(decl, cu, (Record("C", "public"),))
    assert (scoped.has_this, scoped.is_static) == (False, True)


@pytest.mark.parametrize("pointee", ["missing_type", "not_a_pointer"])
def test_an_unreadable_this_reads_unqualified(pointee: str) -> None:
    cu = _CU()
    if pointee == "missing_type":
        this = _die(cu, 9, "DW_TAG_formal_parameter", {"DW_AT_artificial": True})
    else:
        _die(cu, 20, "DW_TAG_base_type", {"DW_AT_name": "int"})
        this = _die(
            cu,
            9,
            "DW_TAG_formal_parameter",
            {"DW_AT_artificial": True, "DW_AT_type": _Ref(20)},
        )
    decl = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_declaration": True}, (this,))
    q = members.member_qualifiers(decl, cu)
    # The object parameter exists, so the member is not static, whatever its
    # type DIE says.
    assert (q.has_this, q.is_const, q.is_volatile) == (True, False, False)
    scoped = members.qualifiers_in_scope(decl, cu, (Record("C", "public"),))
    assert scoped.is_static is False


def test_a_non_member_is_static_only_without_external_linkage() -> None:
    cu = _CU()
    external = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_external": True})
    internal = _die(cu, 2, "DW_TAG_subprogram", {})
    scope = (Namespace("lib"),)
    assert members.qualifiers_in_scope(external, cu, scope).is_static is False
    assert members.qualifiers_in_scope(internal, cu, scope).is_static is True


def test_a_definition_resolves_through_origin_and_specification() -> None:
    cu = _CU()
    decl = _die(
        cu,
        10,
        "DW_TAG_subprogram",
        {
            "DW_AT_name": "f",
            "DW_AT_declaration": True,
            "DW_AT_linkage_name": "_ZN1C1fEv",
        },
    )
    _die(cu, 20, "DW_TAG_subprogram", {"DW_AT_specification": _Ref(10)})
    concrete = _die(cu, 30, "DW_TAG_subprogram", {"DW_AT_abstract_origin": _Ref(20)})
    assert members.member_declaration(concrete, cu) is decl
    assert members.chained_linkage_name(concrete, cu) == "_ZN1C1fEv"


@pytest.mark.parametrize(
    "shape", ["abstract_instance", "no_link", "broken_reference", "not_a_declaration"]
)
def test_no_declaration_is_claimed_without_a_chain_to_one(shape: str) -> None:
    cu = _CU()
    _die(
        cu, 10, "DW_TAG_subprogram", {"DW_AT_name": "f"}
    )  # a definition, not a declaration
    attrs: dict[str, Any] = {
        "abstract_instance": {"DW_AT_inline": 1, "DW_AT_specification": _Ref(10)},
        "no_link": {},
        "broken_reference": {"DW_AT_specification": _Ref(999)},
        "not_a_declaration": {"DW_AT_specification": _Ref(10)},
    }[shape]
    die = _die(cu, 30, "DW_TAG_subprogram", attrs)
    assert members.member_declaration(die, cu) is None


def test_a_linkage_name_absent_from_the_whole_chain_reads_empty() -> None:
    cu = _CU()
    _die(cu, 10, "DW_TAG_subprogram", {"DW_AT_name": "f", "DW_AT_declaration": True})
    die = _die(cu, 30, "DW_TAG_subprogram", {"DW_AT_specification": _Ref(10)})
    broken = _die(cu, 40, "DW_TAG_subprogram", {"DW_AT_specification": _Ref(999)})
    assert members.chained_linkage_name(die, cu) == ""
    assert members.chained_linkage_name(broken, cu) == ""


def test_parameters_resolve_through_their_abstract_origin_and_skip_this() -> None:
    cu = _CU()
    _die(cu, 50, "DW_TAG_formal_parameter", {"DW_AT_name": "x", "DW_AT_type": _Ref(60)})
    _die(
        cu,
        51,
        "DW_TAG_formal_parameter",
        {"DW_AT_artificial": True, "DW_AT_type": _Ref(60)},
    )
    concrete_x = _die(
        cu, 70, "DW_TAG_formal_parameter", {"DW_AT_abstract_origin": _Ref(50)}
    )
    concrete_this = _die(
        cu, 71, "DW_TAG_formal_parameter", {"DW_AT_abstract_origin": _Ref(51)}
    )
    dangling = _die(
        cu, 72, "DW_TAG_formal_parameter", {"DW_AT_abstract_origin": _Ref(999)}
    )
    other = _die(cu, 73, "DW_TAG_variable", {})
    fn = _die(
        cu, 80, "DW_TAG_subprogram", {}, (concrete_this, concrete_x, dangling, other)
    )
    assert [p.offset for p in members.parameter_dies(fn, cu)] == [50, 72]


def test_the_artificial_flag_is_read_from_either_parameter_die() -> None:
    cu = _CU()
    _die(cu, 50, "DW_TAG_formal_parameter", {"DW_AT_artificial": True})
    _die(cu, 51, "DW_TAG_formal_parameter", {"DW_AT_name": "x"})
    # Each concrete parameter repeats its own type.
    this_by_origin = _die(
        cu,
        70,
        "DW_TAG_formal_parameter",
        {"DW_AT_abstract_origin": _Ref(50), "DW_AT_type": _Ref(60)},
    )
    x_local = _die(
        cu,
        71,
        "DW_TAG_formal_parameter",
        {"DW_AT_abstract_origin": _Ref(51), "DW_AT_type": _Ref(60)},
    )
    this_local = _die(
        cu,
        72,
        "DW_TAG_formal_parameter",
        {"DW_AT_artificial": True, "DW_AT_type": _Ref(60)},
    )
    fn = _die(cu, 80, "DW_TAG_subprogram", {}, (this_by_origin, x_local, this_local))
    assert [p.offset for p in members.parameter_dies(fn, cu)] == [71]


@pytest.mark.parametrize(
    ("mangled", "attr", "status", "value"),
    [
        ("_ZN1CC2Ei", True, FactStatus.PRESENT, True),  # explicit constructor
        ("_ZN1CC2Ei", None, FactStatus.PRESENT, False),  # implicit constructor
        ("_ZNK1CcviEv", True, FactStatus.PRESENT, True),  # explicit conversion operator
        ("_ZN1C1fEv", None, FactStatus.NOT_APPLICABLE, None),  # ordinary method
        ("f", None, FactStatus.NOT_APPLICABLE, None),  # extern "C"
    ],
)
def test_explicit_is_read_only_where_it_can_apply(
    mangled: str, attr: bool | None, status: FactStatus, value: bool | None
) -> None:
    cu = _CU()
    decl = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_explicit": attr} if attr else {})
    fact = members.explicit_specifier(mangled, decl)
    assert (fact.status, fact.value) == (status, value)


def test_a_definition_before_its_declaration_is_deferred_once_then_resolved() -> None:
    cu = _CU()
    scopes = members.MemberScopes()
    decl = _die(
        cu,
        10,
        "DW_TAG_subprogram",
        {
            "DW_AT_name": "f",
            "DW_AT_declaration": True,
            "DW_AT_linkage_name": "_ZN1C1fEv",
        },
    )
    definition = _die(cu, 30, "DW_TAG_subprogram", {"DW_AT_specification": _Ref(10)})
    member_scope = ("C", (Record("C", "public"),), "private")

    # Met first: no scope yet, so it is deferred, and a retry does not defer again.
    assert scopes.resolve(definition, cu, "", (), "public", retry=False) is None
    assert scopes.resolve(definition, cu, "", (), "public", retry=True) is None
    assert scopes.resolve(decl, cu, *member_scope, retry=False) == (
        "f",
        None,
        *member_scope,
    )
    deferred = list(scopes.drain())
    assert len(deferred) == 1 and deferred[0][0] is definition
    # drain() forgets the compile unit's declarations: they do not cross units.
    assert scopes.scope_of(decl) is None
    scopes.remember(decl, *member_scope)
    assert scopes.resolve(definition, cu, "", (), "public", retry=True) == (
        "f",
        decl,
        *member_scope,
    )


@pytest.mark.parametrize("missing", ["name", "linkage"])
def test_a_declaration_without_name_or_linkage_records_nothing(missing: str) -> None:
    cu = _CU()
    scopes = members.MemberScopes()
    attrs: dict[str, Any] = {"DW_AT_declaration": True}
    if missing != "name":
        attrs["DW_AT_name"] = "f"
    decl = _die(cu, 10, "DW_TAG_subprogram", attrs)
    definition = _die(cu, 30, "DW_TAG_subprogram", {"DW_AT_specification": _Ref(10)})
    scopes.remember(decl, "C", (Record("C", "public"),), "private")
    assert scopes.resolve(definition, cu, "", (), "public", retry=False) is None


def test_a_namespace_declaration_without_linkage_is_an_extern_c_function() -> None:
    # GCC: ``namespace lib { extern "C" int f(); }`` defined as ``lib::f``.
    cu = _CU()
    scopes = members.MemberScopes()
    decl = _die(
        cu, 10, "DW_TAG_subprogram", {"DW_AT_declaration": True, "DW_AT_name": "f"}
    )
    definition = _die(cu, 30, "DW_TAG_subprogram", {"DW_AT_specification": _Ref(10)})
    ns_scope = ("lib", (Namespace("lib"),), "public")
    scopes.remember(decl, *ns_scope)
    assert members.chained_linkage_name(definition, cu) == ""
    assert scopes.resolve(definition, cu, "", (), "public", retry=False) == (
        "f",
        decl,
        *ns_scope,
    )


def test_a_pure_virtual_member_is_recognized_only_inside_a_record() -> None:
    cu = _CU()
    pure = _die(
        cu, 1, "DW_TAG_subprogram", {"DW_AT_declaration": True, "DW_AT_virtuality": 2}
    )
    plain = _die(
        cu, 2, "DW_TAG_subprogram", {"DW_AT_declaration": True, "DW_AT_virtuality": 1}
    )
    in_class = (Record("C", "public"),)
    assert members.is_pure_virtual_member(pure, in_class) is True
    assert members.is_pure_virtual_member(plain, in_class) is False
    assert members.is_pure_virtual_member(pure, (Namespace("lib"),)) is False
    assert members.is_pure_virtual_member(pure, ()) is False


def test_a_broken_object_pointer_falls_back_to_the_artificial_parameter() -> None:
    cu = _CU()
    this = _this_param(cu, 100, ("const",))
    decl = _die(
        cu,
        1,
        "DW_TAG_subprogram",
        {"DW_AT_declaration": True, "DW_AT_object_pointer": _Ref(999)},
        (this,),
    )
    assert members.member_qualifiers(decl, cu).is_const is True


def test_a_member_with_no_parameters_has_no_this() -> None:
    cu = _CU()
    decl = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_declaration": True})
    assert members.member_qualifiers(decl, cu).has_this is False


@pytest.mark.parametrize("shape", ["cv_without_type", "broken_pointee"])
def test_an_unresolvable_pointee_reads_unqualified(shape: str) -> None:
    cu = _CU()
    if shape == "cv_without_type":
        _die(cu, 40, "DW_TAG_const_type", {})
        _die(cu, 30, "DW_TAG_pointer_type", {"DW_AT_type": _Ref(40)})
    else:
        _die(cu, 30, "DW_TAG_pointer_type", {"DW_AT_type": _Ref(999)})
    this = _die(
        cu,
        9,
        "DW_TAG_formal_parameter",
        {"DW_AT_artificial": True, "DW_AT_type": _Ref(30)},
    )
    decl = _die(cu, 1, "DW_TAG_subprogram", {"DW_AT_declaration": True}, (this,))
    q = members.member_qualifiers(decl, cu)
    assert (q.has_this, q.is_volatile) == (True, False)
    # A const with no type still says const; a broken reference says nothing.
    assert q.is_const is (shape == "cv_without_type")


def test_a_nameless_die_with_no_declaration_records_nothing() -> None:
    cu = _CU()
    die = _die(cu, 30, "DW_TAG_subprogram", {})
    scopes = members.MemberScopes()
    assert scopes.resolve(die, cu, "", (), "public", retry=False) is None
    assert list(scopes.drain()) == []


def _named_definition_case() -> tuple[_CU, Any, Any, tuple[Any, ...]]:
    cu = _CU()
    decl = _die(
        cu,
        10,
        "DW_TAG_subprogram",
        {
            "DW_AT_name": "f",
            "DW_AT_declaration": True,
            "DW_AT_linkage_name": "_ZN1C1fEv",
        },
    )
    # A definition that repeats the name and still links its declaration.
    definition = _die(
        cu,
        30,
        "DW_TAG_subprogram",
        {"DW_AT_name": "f", "DW_AT_specification": _Ref(10)},
    )
    return cu, decl, definition, ("C", (Record("C", "public"),), "private")


def test_a_named_definition_takes_its_declarations_scope() -> None:
    cu, decl, definition, member_scope = _named_definition_case()
    scopes = members.MemberScopes()
    scopes.remember(decl, *member_scope)
    assert scopes.resolve(definition, cu, "", (), "public", retry=False) == (
        "f",
        decl,
        *member_scope,
    )


def test_a_named_definition_met_first_is_deferred_then_answers_for_itself() -> None:
    cu, _decl, definition, _scope = _named_definition_case()
    scopes = members.MemberScopes()
    assert scopes.resolve(definition, cu, "", (), "public", retry=False) is None
    assert [d[0] for d in scopes.drain()] == [definition]
    # The declaration never came by: recorded from where it sits, as before.
    assert scopes.resolve(definition, cu, "ns", (), "public", retry=True) == (
        "f",
        None,
        "ns",
        (),
        "public",
    )


def test_a_named_die_linked_to_no_declaration_answers_for_itself() -> None:
    cu = _CU()
    scopes = members.MemberScopes()
    target = _die(cu, 10, "DW_TAG_subprogram", {"DW_AT_name": "g"})
    die = _die(
        cu,
        30,
        "DW_TAG_subprogram",
        {"DW_AT_name": "f", "DW_AT_specification": _Ref(10)},
    )
    assert target is cu.dies[10]
    assert scopes.resolve(die, cu, "ns", (), "public", retry=False) == (
        "f",
        None,
        "ns",
        (),
        "public",
    )


@pytest.mark.parametrize(
    ("value", "form", "slot"),
    [
        ([0x10, 2], "DW_FORM_exprloc", 2),
        ([0x10, 0], "DW_FORM_exprloc", 0),
        # uleb128 200 = 0xC8 0x01
        ([0x10, 0xC8, 0x01], "DW_FORM_exprloc", 200),
        (bytes([0x10, 5]), "DW_FORM_block1", 5),
        (7, "DW_FORM_data1", 7),
        ([0x11, 2], "DW_FORM_exprloc", None),  # DW_OP_consts: not decoded
        ([0x10, 2, 0x22], "DW_FORM_exprloc", None),  # a second operation
        ([0x10, 0x80], "DW_FORM_exprloc", None),  # truncated uleb128
        ([0x10], "DW_FORM_exprloc", None),
        ([], "DW_FORM_exprloc", None),
    ],
)
def test_a_vtable_slot_is_decoded_or_left_unknown(
    value: Any, form: str, slot: int | None
) -> None:
    die = SimpleNamespace(
        attributes={
            "DW_AT_vtable_elem_location": SimpleNamespace(value=value, form=form)
        }
    )
    assert members.vtable_slot(die) == slot
    assert members.vtable_slot(SimpleNamespace(attributes={})) is None
