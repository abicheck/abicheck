"""Edit operators for the ``cxx_more`` seed: C++ classes, members and linkage.

Ground truth is declared from the Itanium C++ ABI and the language rules
(mangling, vtable and VTT layout, base-subobject placement, overload
resolution), never from abicheck's answer; see ``_edit_corpus_operators`` for
the ``EditOp`` fields.

Evidence floors: a change in a mangled name is visible from the binary
alone; a layout or vtable shape needs DWARF; a specifier with no mangling or
layout effect (``explicit``, ``final``, ``override``, access, ``noexcept`` on
a member, a hidden friend, a scoped enum) needs headers.
"""

from __future__ import annotations

from typing import Any

from tests._edit_corpus_expect import Rule
from tests._edit_corpus_opkit import (
    _REMOVED,
    Edit,
    EditOp,
    Roles,
    _binary_mangled,
    _set,
)
from tests._edit_corpus_seeds import XCXX_SEED

__all__ = ["CXX_OPERATORS"]


def _op(name: str, roles: tuple[str, ...], edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", roles)
    return EditOp(name, roles, edit, why, **kw)


def _replace(
    role: str, old: str, new: str, *, source: tuple[str, str] | None = None
) -> Edit:
    """Re-spell one fragment of *role*'s header (and optionally its source) text."""
    base_h, base_c = XCXX_SEED.roles[role]
    if old not in base_h:
        raise ValueError(f"{role}: {old!r} is not in its header text")
    header = base_h.replace(old, new, 1)
    src = None
    if source is not None:
        if source[0] not in base_c:
            raise ValueError(f"{role}: {source[0]!r} is not in its source text")
        src = base_c.replace(source[0], source[1], 1)

    def edit(r: Roles) -> dict[str, tuple[str, str]]:
        return _set(r, role, header, src)

    return edit


_COMPAT_ONLY: dict[str, Any] = {
    "verdict_min": "COMPATIBLE",
    "verdict_max": "COMPATIBLE",
}
_RISK: dict[str, Any] = {
    "verdict_min": "COMPATIBLE_WITH_RISK",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}
_SOURCE_BREAK: dict[str, Any] = {"verdict_min": "API_BREAK", "verdict_max": "API_BREAK"}
_BINARY = {"min_evidence": "binary"}
_HEADERS = {"min_evidence": "headers"}

_SPECIFIERS = (
    _op(
        "ctor_explicit_removed",
        ("x.widget",),
        _replace("x.widget", "explicit Widget(int v);", "Widget(int v);"),
        "an implicit converting constructor: old binaries still bind, but "
        "rebuilt callers can now convert an int silently and resolve "
        "overloads differently, a behaviour risk",
        expected=frozenset({"CTOR_EXPLICIT_REMOVED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "ctor_explicit_added",
        ("x.gadget",),
        _replace("x.gadget", "Gadget(long g);", "explicit Gadget(long g);"),
        "copy-initialization from long (Gadget g = 6L) no longer compiles",
        expected=frozenset({"CTOR_EXPLICIT_ADDED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "class_became_final",
        ("x.widget",),
        _replace("x.widget", "class Widget {", "class Widget final {"),
        "classes deriving from Widget no longer compile",
        expected=frozenset({"TYPE_BECAME_FINAL"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "class_lost_final",
        ("x.sealed",),
        _replace("x.sealed", "class Sealed final {", "class Sealed {"),
        "no symbol changes, but code built while Sealed was final may have "
        "devirtualized calls through a Sealed*; a new subclass's override "
        "is then never called from it, a risk",
        expected=frozenset({"TYPE_LOST_FINAL"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "override_specifier_added",
        ("x.poly",),
        _replace(
            "x.poly",
            "struct Derived : Base { int m(); };",
            "struct Derived : Base { int m() override; };",
        ),
        "override only checks the declaration; the vtable is unchanged",
        expected=frozenset({"FUNC_OVERRIDE_SPECIFIER_ADDED"}),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
    _op(
        "member_noexcept_removed",
        ("x.calc",),
        _replace(
            "x.calc",
            "int safe() noexcept;",
            "int safe();",
            source=("int Calc::safe() noexcept", "int Calc::safe()"),
        ),
        "callers relying on the no-throw guarantee lose it; the mangled name is unchanged",
        expected=frozenset({"FUNC_NOEXCEPT_REMOVED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "method_access_narrowed",
        ("x.calc",),
        _replace(
            "x.calc", "  int n(int a);\n", "protected:\n  int n(int a);\npublic:\n"
        ),
        "outside callers of Calc::n no longer compile; the symbol stays",
        expected=frozenset({"METHOD_ACCESS_CHANGED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "field_access_narrowed",
        ("x.calc",),
        _replace("x.calc", "  int field;\n", "private:\n  int field;\npublic:\n"),
        "outside users of Calc::field no longer compile; the layout is unchanged",
        expected=frozenset({"FIELD_ACCESS_CHANGED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "field_became_mutable",
        ("x.calc",),
        _replace("x.calc", "  int cache;\n", "  mutable int cache;\n"),
        "mutable relaxes const-correctness; the layout is unchanged",
        expected=frozenset({"FIELD_BECAME_MUTABLE"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_HEADERS,
    ),
    _op(
        "hidden_friend_added",
        ("x.cmp",),
        _replace(
            "x.cmp",
            "struct Cmp { int a; };",
            "struct Cmp { int a; friend bool operator<(Cmp x, Cmp y) { return x.a < y.a; } };",
        ),
        "a hidden friend found only by ADL adds an operation; nothing breaks",
        expected=frozenset({"HIDDEN_FRIEND_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_HEADERS,
    ),
    _op(
        "hidden_friend_removed",
        ("x.cmp",),
        _replace(
            "x.cmp",
            "struct Eq { int a; friend bool operator==(Eq x, Eq y) { return x.a == y.a; } };",
            "struct Eq { int a; };",
        ),
        "e1 == e2 no longer compiles; the friend was inline, so no symbol leaves",
        expected=frozenset({"HIDDEN_FRIEND_REMOVED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "enum_became_scoped",
        ("x.tone",),
        _replace(
            "x.tone",
            "enum tone { T_LO, T_HI };",
            "enum class tone { T_LO, T_HI };",
            source=("t == T_LO", "t == tone::T_LO"),
        ),
        "unqualified T_LO and integer conversions no longer compile",
        expected=frozenset({"ENUM_BECAME_SCOPED"}),
        rules=(
            Rule(
                lambda c: c.flags.short_enums,
                {
                    "expected": frozenset(
                        {"ENUM_BECAME_SCOPED", "ENUM_UNDERLYING_SIZE_CHANGED"}
                    ),
                    "verdict_min": "BREAKING",
                    "verdict_max": None,
                },
                "-fshort-enums packs the unscoped enum into 1 byte; an enum class "
                "is int-sized whatever the flag, so sizeof(tone) 1 -> 4 also "
                "breaks every by-value use",
            ),
        ),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "enum_lost_scoped",
        ("x.shade",),
        _replace(
            "x.shade",
            "enum class shade { dark, light };",
            "enum shade { dark, light };",
        ),
        "the enumerators leak into the enclosing scope and convert to int",
        expected=frozenset({"ENUM_LOST_SCOPED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_HEADERS,
    ),
)

_MEMBERS = (
    _op(
        "method_became_static",
        ("x.calc",),
        _replace("x.calc", "  int m(int a);\n", "  static int m(int a);\n"),
        "a static member takes no this: callers pass the object where a is expected",
        expected=frozenset({"FUNC_STATIC_CHANGED"}),
    ),
    _op(
        "method_ref_qualified",
        ("x.calc",),
        _replace(
            "x.calc",
            "  int r();\n",
            "  int r() &;\n",
            source=("int Calc::r()", "int Calc::r() &"),
        ),
        "a ref-qualifier is part of the mangled name: Calc::r() leaves the export table",
        expected=frozenset({"FUNC_REF_QUAL_CHANGED"}),
        rules=(_binary_mangled("FUNC_REF_QUAL_CHANGED"),),
        **_BINARY,
    ),
    _op(
        "method_deleted",
        ("x.calc",),
        _replace(
            "x.calc",
            "  int n(int a);\n",
            "  int n(int a) = delete;\n",
            source=("int Calc::n(int a) { return a + 2; }\n", ""),
        ),
        "a deleted member is no longer defined: callers fail to compile and to load",
        # castxml 0.7 drops a = delete'd member (known-gaps "frontend kind
        # parity", case38), so the header lane names the deletion a removal.
        any_of=(frozenset({"FUNC_DELETED", "FUNC_DELETED_DWARF"}) | _REMOVED,),
        **_BINARY,
    ),
    _op(
        "const_overload_removed",
        ("x.calc",),
        _replace(
            "x.calc",
            "  int g() const;\n",
            "",
            source=("int Calc::g() const { return 5; }", ""),
        ),
        "calls on a const Calc no longer compile and their symbol is gone",
        expected=frozenset({"REMOVED_CONST_OVERLOAD"}),
        any_of=(_REMOVED,),
    ),
    _op(
        "overload_added",
        ("x.over",),
        lambda r: _set(
            r,
            "x.over",
            "int pick(int a);\nint pick(double a);",
            "int pick(int a) { return a; }\nint pick(double a) { return (int)a + 100; }",
        ),
        "a new overload can capture calls that converted before",
        expected=frozenset({"OVERLOAD_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _op(
        "pure_virtual_added",
        ("x.poly",),
        _replace(
            "x.poly",
            "struct Base { virtual int m(); virtual ~Base(); };",
            "struct Base { virtual int m(); virtual int z() = 0; virtual ~Base(); };",
        ),
        "Base becomes abstract (new Base no longer compiles) and its vtable grows",
        any_of=(
            frozenset(
                {
                    "FUNC_PURE_VIRTUAL_ADDED",
                    "TYPE_BECAME_ABSTRACT",
                    "VTABLE_SLOT_COUNT_CHANGED",
                }
            ),
        ),
        **_BINARY,
    ),
)

_LINKAGE = (
    _op(
        "inline_namespace_bumped",
        ("x.ns",),
        lambda r: _set(
            r,
            "x.ns",
            XCXX_SEED.roles["x.ns"][0].replace(
                "inline namespace v1", "inline namespace v2"
            ),
            XCXX_SEED.roles["x.ns"][1].replace(
                "inline namespace v1", "inline namespace v2"
            ),
        ),
        "lib::v1::f1 becomes lib::v2::f1: every mangled name changes, the spelling does not",
        expected=frozenset({"INLINE_NAMESPACE_MOVED"}),
        rules=(_binary_mangled("INLINE_NAMESPACE_MOVED", "FUNC_LIKELY_RENAMED"),),
        **_BINARY,
    ),
    _op(
        "abi_tag_added",
        ("x.tagged",),
        lambda r: _set(
            r,
            "x.tagged",
            '__attribute__((abi_tag("v2"))) int tagf(int a);',
            '__attribute__((abi_tag("v2"))) int tagf(int a) { return a * 7; }',
        ),
        "an ABI tag is mangled into the name (tagf[abi:v2]): the old symbol is gone",
        expected=frozenset({"ABI_TAG_CHANGED"}),
        **_BINARY,
    ),
    _op(
        "base_became_virtual",
        ("x.vbase",),
        _replace("x.vbase", "struct VD : VB {", "struct VD : virtual VB {"),
        "a virtual base moves the VB subobject behind a vbase offset and adds a VTT",
        expected=frozenset({"BASE_CLASS_VIRTUAL_CHANGED"}),
        any_of=(frozenset({"RTTI_INHERITANCE_CHANGED", "VTABLE_THUNK_SET_CHANGED"}),),
        rules=(
            Rule(
                lambda c: c.objfmt != "elf",
                {"any_of": ()},
                "the RTTI-size and thunk-set kinds read the ELF symbol table "
                "(diff_elf_layout requires an ELF snapshot)",
            ),
        ),
    ),
    _op(
        "bases_reordered",
        ("x.multi",),
        _replace("x.multi", "struct MI : B1, B2 {", "struct MI : B2, B1 {"),
        "swapping bases swaps their subobject offsets and the primary vtable",
        expected=frozenset({"BASE_CLASS_POSITION_CHANGED"}),
        any_of=(frozenset({"BASE_CLASS_OFFSET_CHANGED", "TYPE_BASE_CHANGED"}),),
    ),
    _op(
        "primary_base_grew",
        ("x.multi",),
        _replace(
            "x.multi",
            "struct B1 { int a; virtual",
            "struct B1 { int a; long pad; virtual",
        ),
        "B2 moves inside MI: the this-adjusting thunk for MI::f2 gets a new offset",
        any_of=(
            frozenset({"VTABLE_THUNK_OFFSET_CHANGED", "BASE_CLASS_OFFSET_CHANGED"}),
        ),
        rules=(
            Rule(
                lambda c: c.objfmt == "pe",
                {"any_of": ()},
                "PE (MinGW): thunk names are read from the ELF symbol table and "
                "the PE lane records no base-class offsets; this corpus's scope "
                "is ELF, so the layout break is owed only as BREAKING here",
            ),
        ),
        **_BINARY,
    ),
    _op(
        "vptr_introduced",
        ("x.plain",),
        _replace(
            "x.plain",
            "struct Plain { int a; int get(); };",
            "struct Plain { int a; virtual int get(); };",
        ),
        "a first virtual adds a vptr at offset 0: a moves and the size grows",
        expected=frozenset({"VPTR_INTRODUCED"}),
    ),
)

_QUALIFIERS = (
    _op(
        "field_lost_mutable",
        ("x.quals",),
        _replace("x.quals", "  mutable int hits;\n", "  int hits;\n"),
        "code that writes q.hits through a const Quals no longer compiles; "
        "the layout is unchanged",
        expected=frozenset({"FIELD_LOST_MUTABLE"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "field_lost_const",
        ("x.quals",),
        _replace("x.quals", "  const int k;\n", "  int k;\n"),
        "dropping const on a member only permits more writes; size, offsets "
        "and mangled names are unchanged",
        expected=frozenset({"FIELD_LOST_CONST"}),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
    _op(
        "field_lost_volatile",
        ("x.quals",),
        _replace("x.quals", "  volatile int reg;\n", "  int reg;\n"),
        "the layout is unchanged, but rebuilt code may cache or elide reads "
        "of reg that the library or hardware relies on, a risk",
        expected=frozenset({"FIELD_LOST_VOLATILE"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "static_member_access_narrowed",
        ("x.quals",),
        _replace(
            "x.quals",
            "  static int count;\n",
            "private:\n  static int count;\npublic:\n",
        ),
        "outside uses of Quals::count no longer compile; its symbol stays exported",
        expected=frozenset({"VAR_ACCESS_CHANGED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "type_lost_abstract",
        ("x.shape",),
        _replace(
            "x.shape",
            "virtual int area() = 0;",
            "virtual int area();",
            source=(
                "Shape::~Shape() {}",
                "Shape::~Shape() {}\nint Shape::area() { return 0; }",
            ),
        ),
        "Shape becomes instantiable; the vtable keeps its shape (the pure "
        "slot now names Shape::area) and no existing use breaks",
        expected=frozenset({"TYPE_LOST_ABSTRACT"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_HEADERS,
    ),
    _op(
        "override_specifier_removed",
        ("x.ovr",),
        _replace("x.ovr", "int h() override;", "int h();"),
        "the vtable is unchanged, but a later signature drift in OBase::h "
        "would silently stop ODer::h overriding it, a risk",
        expected=frozenset({"FUNC_OVERRIDE_SPECIFIER_REMOVED"}),
        **_RISK,
        **_HEADERS,
    ),
)

CXX_OPERATORS: tuple[EditOp, ...] = (*_SPECIFIERS, *_MEMBERS, *_LINKAGE, *_QUALIFIERS)
