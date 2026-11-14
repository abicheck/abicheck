"""Edit operators for the ``class_virtual`` classes of ``abiforge.model.edit_model``.

One operator per vtable edit class that had none. Each edits one ``vt.*``
probe of the ``cxx_more`` seed (``abiforge.model.probes``). Ground truth is
declared from the Itanium C++ ABI: a polymorphic class's vtable holds one
slot per virtual function in declaration order (two for a virtual
destructor, complete and deleting), a call through a base pointer indexes
that slot, a consumer that derives from the class lays out its own vtable
after the base's slots, and an overrider's return type is not in its
mangled name.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.opkit import _OFFSET, _SIZE, EditOp
from abiforge.operators.layout import _op, _replace

__all__ = ["VTABLE_OPERATORS"]

_VTABLE = frozenset(
    {"TYPE_VTABLE_CHANGED", "VTABLE_SLOT_COUNT_CHANGED", "SYMBOL_SIZE_CHANGED"}
)
_BREAKING: dict[str, Any] = {"verdict_min": "BREAKING"}

VTABLE_OPERATORS: tuple[EditOp, ...] = (
    _op(
        "virtual_appended_after_last_slot",
        "vt.append",
        _replace(
            "vt.append",
            "struct VAp { virtual ~VAp(); virtual int f(); };",
            "struct VAp { virtual ~VAp(); virtual int f(); virtual int g(); };",
            source=(
                "int VAp::f() { return 1; }",
                "int VAp::f() { return 1; }\nint VAp::g() { return 12; }",
            ),
        ),
        "g gets a slot after every existing one: calls through old slots still "
        "land, but the vtable grows, so a consumer's class derived from VAp "
        "puts its own first virtual in the slot VAp::g now uses",
        # a brand-new virtual is a new function as well as a new slot
        any_of=(
            frozenset({"VIRTUAL_METHOD_ADDED", "FUNC_VIRTUAL_ADDED", "FUNC_ADDED"}),
            _VTABLE,
        ),
        **_BREAKING,
    ),
    _op(
        "method_became_virtual",
        "vt.mkvirt",
        _replace(
            "vt.mkvirt",
            "struct VMv { virtual ~VMv(); int g(); };",
            "struct VMv { virtual ~VMv(); virtual int g(); };",
        ),
        "VMv::g gains a vtable slot: the symbol keeps its name, but old callers "
        "bind g statically and a consumer's derived class shifts its own slots",
        any_of=(
            frozenset(
                {
                    "VIRTUAL_METHOD_ADDED",
                    "FUNC_VIRTUAL_ADDED",
                }
            ),
            _VTABLE,
        ),
        **_BREAKING,
    ),
    _op(
        "method_became_nonvirtual",
        "vt.mknv",
        _replace(
            "vt.mknv",
            "struct VNv { virtual ~VNv(); virtual int g(); };",
            "struct VNv { virtual ~VNv(); int g(); };",
        ),
        "VNv::g loses its slot: old callers dispatching through it read past the "
        "shrunken vtable, and consumer overrides of g are no longer called",
        any_of=(
            frozenset({"FUNC_VIRTUAL_REMOVED", "TYPE_VTABLE_CHANGED"}),
            _VTABLE,
        ),
        **_BREAKING,
    ),
    _op(
        "override_added",
        "vt.ovadd",
        _replace(
            "vt.ovadd",
            "struct VOD : VOB { int d; };",
            "struct VOD : VOB { int d; int f(); };",
            source=(
                "int VOB::f() { return 4; }",
                "int VOB::f() { return 4; }\nint VOD::f() { return 13; }",
            ),
        ),
        "VOD's inherited slot now names VOD::f: no slot moves and no symbol goes, "
        "so dispatch from old binaries reaches the new overrider",
        expected=frozenset({"FUNC_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="binary",
    ),
    _op(
        "override_removed",
        "vt.ovrm",
        _replace(
            "vt.ovrm",
            "struct VRD : VRB { int d; int f(); };",
            "struct VRD : VRB { int d; };",
            source=("int VRD::f() { return 6; }\n", ""),
        ),
        "VRD::f goes: its slot falls back to VRB::f, but code compiled against "
        "the old header that called VRD::f directly (qualified or devirtualized) "
        "no longer links",
        any_of=(frozenset({"FUNC_REMOVED", "FUNC_REMOVED_ELF_ONLY"}),),
        **_BREAKING,
        min_evidence="binary",
    ),
    _op(
        "virtual_became_final",
        "vt.final",
        _replace(
            "vt.final",
            "struct VFn { virtual ~VFn(); virtual int f(); };",
            "struct VFn { virtual ~VFn(); virtual int f() final; };",
        ),
        "a final virtual keeps its slot and symbol, but a consumer class that "
        "overrides VFn::f no longer compiles",
        expected=frozenset(),
        verdict_min="API_BREAK",
        verdict_max="API_BREAK",
        min_evidence="headers",
    ),
    _op(
        "destructor_became_virtual",
        "vt.dtoradd",
        _replace(
            "vt.dtoradd",
            "struct VDa { virtual int f(); ~VDa(); };",
            "struct VDa { virtual int f(); virtual ~VDa(); };",
        ),
        "the destructor gains two slots (complete and deleting) after f: the "
        "vtable grows and a consumer's derived class shifts its own slots",
        any_of=(
            frozenset(
                {
                    "VIRTUAL_METHOD_ADDED",
                    "FUNC_VIRTUAL_ADDED",
                }
            ),
            _VTABLE,
        ),
        **_BREAKING,
    ),
    _op(
        "destructor_became_nonvirtual",
        "vt.dtorrm",
        _replace(
            "vt.dtorrm",
            "struct VDr { virtual int f(); virtual ~VDr(); };",
            "struct VDr { virtual int f(); ~VDr(); };",
        ),
        "the destructor's two slots go: old code deleting through a VDr* calls "
        "the deleting-destructor slot past the end of the new vtable",
        any_of=(
            frozenset(
                {
                    "FUNC_VIRTUAL_REMOVED",
                    "POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR",
                }
            ),
            _VTABLE,
        ),
        **_BREAKING,
    ),
    _op(
        "key_function_inlined",
        "vt.key",
        _replace(
            "vt.key",
            "struct VKy { virtual int f(); int k; };",
            "struct VKy { virtual int f() { return 10; } int k; };",
            source=("int VKy::f() { return 10; }\n", ""),
        ),
        "VKy loses its key function: its vtable, type_info and VKy::f become "
        "vague-linkage (weak) copies every user emits, so the library no longer "
        "owns one strong definition and a consumer's copy can win",
        any_of=(
            frozenset(
                {
                    "SYMBOL_BINDING_CHANGED",
                    "FUNC_BECAME_INLINE",
                    "VTABLE_SYMBOL_IDENTITY_CHANGED",
                }
            ),
        ),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="binary",
    ),
    _op(
        "override_return_became_covariant",
        "vt.cov",
        _replace(
            "vt.cov",
            "struct VCD : VCB { VCB* clone(); };",
            "struct VCD : VCB { VCD* clone(); };",
            source=(
                "VCB* VCD::clone() { return new VCD(); }",
                "VCD* VCD::clone() { return new VCD(); }",
            ),
        ),
        "VCD::clone now returns VCD*: VCB is VCD's primary base at offset 0, so "
        "no thunk and no slot is added and the mangled name is unchanged; only "
        "rebuilt callers see the narrower return type",
        expected=frozenset({"FUNC_RETURN_COVARIANT_NARROWED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="dwarf",
    ),
    _op(
        "last_virtual_removed",
        "vt.unpoly",
        _replace(
            "vt.unpoly",
            "struct VUp { virtual int f(); int a; };",
            "struct VUp { int f(); int a; };",
        ),
        "VUp's only virtual goes, and its vptr: a moves 8 -> 0 (4 -> 0 at -m32) "
        "and sizeof shrinks",
        any_of=(
            frozenset({"FUNC_VIRTUAL_REMOVED", "TYPE_VTABLE_CHANGED"}),
            _OFFSET | _SIZE,
        ),
        **_BREAKING,
    ),
)
