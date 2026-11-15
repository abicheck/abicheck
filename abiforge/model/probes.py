"""Baselines for the edit-class probes of ``cxx_more``.

One self-contained declaration group per role, each the old side of exactly
one ``abiforge.model.edit_model`` class that no older operator generated:
``vt.*`` virtual functions and vtables (``abiforge.operators.vtable``),
``en.*``/``ef.*``/``ec.*`` unscoped, fixed-underlying and scoped enums
(``abiforge.operators.enums``), ``vr.*`` variables
(``abiforge.operators.variables``), ``td.*`` typedefs and ``tp.*`` templates
(``abiforge.operators.aliases``). Names are prefixed so the groups collide
with nothing else in the seed.

Every class that must reach the binary has an out-of-line member or a
``*_make``/``*_use`` function defined in the source, so its vtable, type
information and DWARF are emitted by the library.
"""

from __future__ import annotations

RoleText = tuple[str, str]

__all__ = ["ENUM_PROBES", "PROBE_ROLES", "enum_probe_text"]

_VTABLE_ROLES: dict[str, RoleText] = {
    "vt.append": (
        "struct VAp { virtual ~VAp(); virtual int f(); };\nVAp* vap_make();",
        "VAp::~VAp() {}\nint VAp::f() { return 1; }\nVAp* vap_make() { return new VAp(); }",
    ),
    "vt.mkvirt": (
        "struct VMv { virtual ~VMv(); int g(); };\nVMv* vmv_make();",
        "VMv::~VMv() {}\nint VMv::g() { return 2; }\nVMv* vmv_make() { return new VMv(); }",
    ),
    "vt.mknv": (
        "struct VNv { virtual ~VNv(); virtual int g(); };\nVNv* vnv_make();",
        "VNv::~VNv() {}\nint VNv::g() { return 3; }\nVNv* vnv_make() { return new VNv(); }",
    ),
    "vt.ovadd": (
        "struct VOB { virtual ~VOB(); virtual int f(); };\n"
        "struct VOD : VOB { int d; };\nVOB* vod_make();",
        "VOB::~VOB() {}\nint VOB::f() { return 4; }\nVOB* vod_make() { return new VOD(); }",
    ),
    "vt.ovrm": (
        "struct VRB { virtual ~VRB(); virtual int f(); };\n"
        "struct VRD : VRB { int d; int f(); };\nVRB* vrd_make();",
        "VRB::~VRB() {}\nint VRB::f() { return 5; }\nint VRD::f() { return 6; }\n"
        "VRB* vrd_make() { return new VRD(); }",
    ),
    "vt.final": (
        "struct VFn { virtual ~VFn(); virtual int f(); };\nVFn* vfn_make();",
        "VFn::~VFn() {}\nint VFn::f() { return 7; }\nVFn* vfn_make() { return new VFn(); }",
    ),
    "vt.unfinal": (
        "struct VUf { virtual ~VUf(); virtual int f() final; };\nVUf* vuf_make();",
        "VUf::~VUf() {}\nint VUf::f() { return 14; }\nVUf* vuf_make() { return new VUf(); }",
    ),
    "vt.dtoradd": (
        "struct VDa { virtual int f(); ~VDa(); };\nVDa* vda_make();",
        "int VDa::f() { return 8; }\nVDa::~VDa() {}\nVDa* vda_make() { return new VDa(); }",
    ),
    "vt.dtorrm": (
        "struct VDr { virtual int f(); virtual ~VDr(); };\nVDr* vdr_make();",
        "int VDr::f() { return 9; }\nVDr::~VDr() {}\nVDr* vdr_make() { return new VDr(); }",
    ),
    # f is the key function: the only non-inline, non-pure virtual.
    "vt.key": (
        "struct VKy { virtual int f(); int k; };\nVKy* vky_make();",
        "int VKy::f() { return 10; }\nVKy* vky_make() { return new VKy(); }",
    ),
    "vt.cov": (
        "struct VCB { virtual ~VCB(); virtual VCB* clone(); };\n"
        "struct VCD : VCB { VCB* clone(); };\nVCB* vcd_make();",
        "VCB::~VCB() {}\nVCB* VCB::clone() { return new VCB(); }\n"
        "VCB* VCD::clone() { return new VCD(); }\nVCB* vcd_make() { return new VCD(); }",
    ),
    "vt.unpoly": (
        "struct VUp { virtual int f(); int a; };\nint vup_use(VUp* p);",
        "int VUp::f() { return 11; }\nint vup_use(VUp* p) { return p->a; }",
    ),
}

# --- enums: one probe per (construct, edit) -----------------------------------
#
# ``(prefix, construct, opening)``: the probe enum's opening keyword(s) per
# construct; ``{n}`` is its name.
ENUM_PROBES: tuple[tuple[str, str, str], ...] = (
    ("en", "enum", "enum {n}"),
    ("ef", "enum_fixed_underlying", "enum {n} : int"),
    ("ec", "enum_class", "enum class {n}"),
)

#: The enum edits a probe exists for, per construct prefix.
ENUM_PROBE_EDITS: dict[str, tuple[str, ...]] = {
    "en": ("alias", "added", "removed"),
    "ef": (
        "append",
        "insert",
        "remove",
        "value",
        "swap",
        "rename",
        "alias",
        "scoped",
        "added",
        "removed",
        "opaque",
    ),
    "ec": ("insert", "swap", "rename", "alias", "added", "removed", "opaque"),
}


def enum_names(prefix: str, edit: str) -> tuple[str, str, bool]:
    """(enum name, enumerator prefix, scoped) of one enum probe."""
    name = f"{prefix.upper()}_{edit}"
    return name, f"{prefix.upper()}{edit.upper()}_", prefix == "ec"


def enum_probe_text(
    prefix: str, edit: str, enumerators: str | None, *, opening: str | None = None
) -> str:
    """The header text of one enum probe: the enum with *enumerators* (None:
    an opaque declaration), and, unless it is the added/removed probe, the
    function taking it by value that makes the library's DWARF carry it."""
    name, _, _ = enum_names(prefix, edit)
    head = (opening or dict((p, o) for p, _, o in ENUM_PROBES)[prefix]).format(n=name)
    decl = f"{head};" if enumerators is None else f"{head} {{ {enumerators} }};"
    if edit in ("added", "removed"):
        return decl
    return f"{decl}\nint {name.lower()}_use({name} e);"


def enum_baseline(prefix: str, edit: str) -> RoleText:
    name, p, _ = enum_names(prefix, edit)
    if edit == "added":
        return ("", "")
    header = enum_probe_text(prefix, edit, f"{p}A, {p}B, {p}C")
    if edit == "removed":
        return (header, "")
    return (header, f"int {name.lower()}_use({name} e) {{ return (int)e; }}")


_ENUM_ROLES: dict[str, RoleText] = {
    f"{prefix}.{edit}": enum_baseline(prefix, edit)
    for prefix, edits in ENUM_PROBE_EDITS.items()
    for edit in edits
}

_VARIABLE_ROLES: dict[str, RoleText] = {
    "vr.shrink": ("extern long long g_vshr;", "long long g_vshr = 1;"),
    "vr.same": ("extern int g_vsame;", "int g_vsame = 2;"),
    "vr.weak": ("extern int g_vweak;", "int g_vweak = 3;"),
    "vr.rename": ("extern int g_vold;", "int g_vold = 4;"),
    # vinl_get odr-uses the variable, so the library emits it either way.
    "vr.inline": (
        "extern int g_vinl;\nint vinl_get();",
        "int g_vinl = 5;\nint vinl_get() { return g_vinl; }",
    ),
}

_ALIAS_ROLES: dict[str, RoleText] = {
    "td.same": (
        "typedef int TdS;\nint tds_use(TdS v);",
        "int tds_use(TdS v) { return v; }",
    ),
    "td.added": ("", ""),
    "td.respell": (
        "typedef int TdR;\nint tdr_use(TdR v);",
        "int tdr_use(TdR v) { return v; }",
    ),
    "tp.add": ("", ""),
    "tp.adddef": (
        "template <class T> struct TpA { T v; };\nint tpa_use(TpA<int>* p);",
        "int tpa_use(TpA<int>* p) { return p->v; }",
    ),
    "tp.rmparam": (
        "template <class T, class U = int> struct TpR { T v; };\nint tpr_use(TpR<int>* p);",
        "int tpr_use(TpR<int>* p) { return p->v; }",
    ),
    "tp.retype": (
        "template <int N> struct TpK { int v[N]; };\nint tpk_use(TpK<2>* p);",
        "int tpk_use(TpK<2>* p) { return p->v[0]; }",
    ),
    "tp.instadd": (
        "template <class T> T tpi(T v);\nextern template int tpi<int>(int);",
        "template <class T> T tpi(T v) { return v + 1; }\ntemplate int tpi<int>(int);",
    ),
    "tp.specadd": (
        "template <class T> int tps(T v) { return (int)sizeof(v); }\nint tps_use(int v);",
        "int tps_use(int v) { return tps(v); }",
    ),
    "tp.specrm": (
        "template <class T> int tpz(T v) { return (int)sizeof(v); }\n"
        "template <> int tpz<int>(int v);",
        "template <> int tpz<int>(int v) { return v; }",
    ),
}

PROBE_ROLES: dict[str, RoleText] = {
    **_VTABLE_ROLES,
    **_ENUM_ROLES,
    **_VARIABLE_ROLES,
    **_ALIAS_ROLES,
}
