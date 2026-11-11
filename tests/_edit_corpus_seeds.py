"""Seed translation units for the semantic edit corpus.

A seed is a header + source pair built from *roles*. A role is one declaration
(its header text) and, when the declaration has an out-of-line definition, the
matching source text. Templates place role texts with ``@@role@@`` lines, so an
operator edits one role and the seed re-renders both files. A role whose text
is empty is simply absent from the rendered file.

Both seeds use the same ``core`` roles so that an operator written against them
applies to C and C++ alike; the C++ seed adds class, template and enum-class
roles that only C++ operators require. Nothing here is a ground truth: the
expected ChangeKinds live with the operators in ``_edit_corpus_operators.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# (header_text, source_text). "" means "no such line in this file".
RoleText = tuple[str, str]

# Build roles carry how the library is built, not what it declares: their
# header slot holds the value and no template places them. ``Seed.build_args``
# reads them; an operator edits one like any other role (a link-level edit:
# a version script, a SONAME, an extra NEEDED library, a hardening flag).
BUILD_ROLES: tuple[str, ...] = (
    "build.cflags",  # extra compile flags, space-separated
    "build.ldflags",  # extra link flags, space-separated
    "build.vscript",  # GNU ld version-script text ("" = none)
    "build.soname",  # DT_SONAME ("" = none); absent role = the harness default
)


@dataclass(frozen=True)
class BuildArgs:
    cflags: tuple[str, ...] = ()
    ldflags: tuple[str, ...] = ()
    vscript: str = ""
    # None: the seed has no ``build.soname`` role, the harness picks its default.
    soname: str | None = None


@dataclass(frozen=True)
class Seed:
    name: str
    lang: str  # "c" or "c++"
    header_template: tuple[str, ...]
    source_template: tuple[str, ...]
    roles: Mapping[str, RoleText]

    @property
    def header_name(self) -> str:
        return "edit_seed.h"

    @property
    def source_name(self) -> str:
        return "edit_seed.c" if self.lang == "c" else "edit_seed.cpp"

    def build_args(self, overrides: Mapping[str, RoleText] | None = None) -> BuildArgs:
        """The build roles (with *overrides* applied) as compile/link inputs."""
        current: dict[str, RoleText] = dict(self.roles)
        if overrides:
            current.update(overrides)

        def text(role: str) -> str | None:
            return current[role][0] if role in current else None

        return BuildArgs(
            cflags=tuple((text("build.cflags") or "").split()),
            ldflags=tuple((text("build.ldflags") or "").split()),
            vscript=text("build.vscript") or "",
            soname=text("build.soname"),
        )

    def render(
        self, overrides: Mapping[str, RoleText] | None = None
    ) -> tuple[str, str]:
        """Return (header_text, source_text) with *overrides* applied on top."""
        current: dict[str, RoleText] = dict(self.roles)
        if overrides:
            current.update(overrides)
        return (
            _render(
                self.header_template, current, side=0, guard=self.name, kind="header"
            ),
            _render(
                self.source_template, current, side=1, guard=self.name, kind="source"
            ),
        )


def _render(
    template: tuple[str, ...],
    roles: Mapping[str, RoleText],
    *,
    side: int,
    guard: str,
    kind: str,
) -> str:
    out: list[str] = []
    for line in template:
        if line.startswith("@@") and line.endswith("@@"):
            role = line[2:-2]
            if role not in roles:
                raise KeyError(
                    f"seed {guard}: {kind} template names unknown role {role!r}"
                )
            text = roles[role][side]
            if text:
                out.append(text)
        else:
            out.append(line)
    return "\n".join(out) + "\n"


# Roles shared by both seeds. Each is one declaration, its definition if any.
_CORE_ROLES: dict[str, RoleText] = {
    "struct.point": ("struct point { int x; int y; };", ""),
    "struct.rec": ("struct rec { int id; double weight; char tag[8]; };", ""),
    "enum.color": ("enum color { RED, GREEN, BLUE };", ""),
    "typedef.handle": ("typedef int handle_t;", ""),
    "func.add": (
        "int add_ints(int a, int b);",
        "int add_ints(int a, int b) { return a + b; }",
    ),
    # Bodies are asymmetric in their inputs so a layout or value change is
    # visible to the execution oracle: (x, y) and (y, x) scale differently.
    "func.scale": (
        "double scale(struct point p, double f);",
        "double scale(struct point p, double f) { return (p.x * 3 + p.y) * f; }",
    ),
    "func.count": (
        "int get_count(void);",
        "int get_count(void) { return g_counter; }",
    ),
    "func.make": (
        "struct rec make_rec(int id);",
        "struct rec make_rec(int id) { struct rec r = {0}; r.id = id; return r; }",
    ),
    # Maps by enumerator *name*, so a renumbering is visible at run time; it
    # names only RED and GREEN so every enum edit still compiles.
    "func.color": (
        "int color_code(enum color c);",
        "int color_code(enum color c) { return c == RED ? 100 : c == GREEN ? 200 : 300 + (int)c; }",
    ),
    # Writes every byte of the caller's record, so a size change shows as
    # bytes written past (or short of) the consumer's sizeof.
    "func.fill": (
        "void fill_rec(struct rec *r, int id);",
        "void fill_rec(struct rec *r, int id) { memset(r, 0x11, sizeof *r); r->id = id; r->weight = 2.5; }",
    ),
    # Declarations moved here sit outside the default-visibility block.
    "hidden.slot": ("", ""),
    "func.handle": (
        "handle_t handle_of(int x);",
        "handle_t handle_of(int x) { return x; }",
    ),
    "var.counter": ("extern int g_counter;", "int g_counter = 0;"),
}

_CORE_HEADER: tuple[str, ...] = (
    "@@struct.point@@",
    "@@struct.rec@@",
    "@@enum.color@@",
    "@@typedef.handle@@",
    "@@func.add@@",
    "@@func.scale@@",
    "@@func.count@@",
    "@@func.make@@",
    "@@func.color@@",
    "@@func.handle@@",
    "@@func.fill@@",
    "@@var.counter@@",
)

_CORE_SOURCE: tuple[str, ...] = (
    '#include "edit_seed.h"',
    "#include <string.h>",
    "@@var.counter@@",
    "@@func.add@@",
    "@@func.scale@@",
    "@@func.count@@",
    "@@func.make@@",
    "@@func.color@@",
    "@@func.handle@@",
    "@@func.fill@@",
)

# Every public declaration keeps default visibility under -fvisibility=hidden,
# the way a library's export macro would; ``hidden.slot`` is outside it.
_VIS_OPEN = "#pragma GCC visibility push(default)"
_VIS_CLOSE = "#pragma GCC visibility pop"

C_SEED = Seed(
    name="c_core",
    lang="c",
    header_template=(
        "#ifndef EDIT_SEED_H",
        "#define EDIT_SEED_H",
        _VIS_OPEN,
        *_CORE_HEADER,
        _VIS_CLOSE,
        "@@hidden.slot@@",
        "#endif",
    ),
    source_template=_CORE_SOURCE,
    roles=_CORE_ROLES,
)

_CXX_ROLES: dict[str, RoleText] = {
    **_CORE_ROLES,
    "base.decl": ("class Base { public: int base_field; };", ""),
    "shape.open": ("class Shape {", ""),
    "shape.area": (
        "  virtual int area() const;",
        "int Shape::area() const { return w; }",
    ),
    "shape.kind": (
        "  virtual int kind() const;",
        "int Shape::kind() const { return 1; }",
    ),
    "shape.w": ("  int w;", ""),
    "buf.decl": ("template <typename T, int N = 4> struct Buf { T data[N]; };", ""),
    "enum.mode": ("enum class mode { fast, slow };", ""),
    "func.twice": ("int twice(int x);", "int twice(int x) { return 2 * x; }"),
    # Calls through the vtable of whatever object the consumer passes. Takes a
    # non-const reference so the body still compiles when area() loses const.
    "func.shape_area": (
        "int shape_area(Shape& s);",
        "int shape_area(Shape& s) { return s.area() * 10; }",
    ),
    "struct.tagged": ("struct Empty {};\nstruct Tagged { Empty e; int v; };", ""),
    "func.tagged": (
        "int tagged_v(const Tagged& t);",
        "int tagged_v(const Tagged& t) { return t.v; }",
    ),
    "tmpl.clamp": (
        "template <typename T> T clampv(T v) { return v < 0 ? T(0) : v; }",
        "",
    ),
    "func.mode": (
        "int mode_code(mode m);",
        "int mode_code(mode m) { return m == mode::fast ? 7 : 9; }",
    ),
}

_CXX_HEADER: tuple[str, ...] = (
    "#ifndef EDIT_SEED_HPP",
    "#define EDIT_SEED_HPP",
    "#pragma GCC visibility push(default)",
    "@@struct.point@@",
    "@@struct.rec@@",
    "@@enum.color@@",
    "@@typedef.handle@@",
    "@@base.decl@@",
    "@@shape.open@@",
    "public:",
    "@@shape.area@@",
    "@@shape.kind@@",
    "@@shape.w@@",
    "};",
    "@@buf.decl@@",
    "@@enum.mode@@",
    "@@struct.tagged@@",
    "@@tmpl.clamp@@",
    "@@func.add@@",
    "@@func.scale@@",
    "@@func.count@@",
    "@@func.make@@",
    "@@func.color@@",
    "@@func.handle@@",
    "@@func.twice@@",
    "@@func.fill@@",
    "@@func.shape_area@@",
    "@@func.tagged@@",
    "@@func.mode@@",
    "@@var.counter@@",
    "#pragma GCC visibility pop",
    "@@hidden.slot@@",
    "#endif",
)

_CXX_SOURCE: tuple[str, ...] = (
    '#include "edit_seed.h"',
    "#include <string.h>",
    "@@var.counter@@",
    "@@shape.area@@",
    "@@shape.kind@@",
    "@@func.add@@",
    "@@func.scale@@",
    "@@func.count@@",
    "@@func.make@@",
    "@@func.color@@",
    "@@func.handle@@",
    "@@func.twice@@",
    "@@func.fill@@",
    "@@func.shape_area@@",
    "@@func.tagged@@",
    "@@func.mode@@",
)

CXX_SEED = Seed(
    name="cxx_classes",
    lang="c++",
    header_template=_CXX_HEADER,
    source_template=_CXX_SOURCE,
    roles=_CXX_ROLES,
)

# A libstdc++ surface: std::string in signatures carries the dual-ABI
# (``_GLIBCXX_USE_CXX11_ABI``) tag in its mangled names.
_STDLIB_ROLES: dict[str, RoleText] = {
    "func.greet": (
        "std::string greet(const std::string& who);",
        'std::string greet(const std::string& who) { return "hi " + who; }',
    ),
    "func.name_len": (
        "int name_len(const std::string& s);",
        "int name_len(const std::string& s) { return (int)s.size(); }",
    ),
    "func.add": _CORE_ROLES["func.add"],
}

STDLIB_SEED = Seed(
    name="cxx_stdlib",
    lang="c++",
    header_template=(
        "#ifndef EDIT_SEED_STDLIB_HPP",
        "#define EDIT_SEED_STDLIB_HPP",
        "#include <string>",
        "#pragma GCC visibility push(default)",
        "@@func.greet@@",
        "@@func.name_len@@",
        "@@func.add@@",
        "#pragma GCC visibility pop",
        "#endif",
    ),
    source_template=(
        '#include "edit_seed.h"',
        "@@func.greet@@",
        "@@func.name_len@@",
        "@@func.add@@",
    ),
    roles=_STDLIB_ROLES,
)

# A C library seen as an ELF object: symbol versions, SONAME, NEEDED,
# hardening, TLS, binding and visibility, exported data, header constants,
# unions, bitfields, flexible arrays, deprecation and parameter qualifiers.
# Its role names start with ``elf.`` so the core operators never apply to it.
_ELF_ROLES: dict[str, RoleText] = {
    "build.cflags": ("-fstack-protector-strong", ""),
    "build.ldflags": ("-Wl,--no-as-needed -lm -lgcc_s -Wl,--as-needed -Wl,-z,now", ""),
    "build.vscript": ("LIBEDIT_1.0 { global: *; };", ""),
    "build.soname": ("libedit.so", ""),
    "elf.const": ("static const int EDIT_LIMIT = 16;", ""),
    "elf.union": ("union num { int i; float f; };", ""),
    "elf.flags": ("struct flags { int a; int b; int c; };", ""),
    "elf.buf": ("struct buf { int n; int d[1]; };", ""),
    "elf.ctx": ("struct ctx { char a; int b; };", ""),
    "elf.level": ("enum level { LOW, MID, LEVEL_LAST };", ""),
    "elf.count_t": ("typedef int count_t;", ""),
    "elf.legacy": (
        "struct __attribute__((deprecated)) legacy { int x; };\n"
        "enum __attribute__((deprecated)) old_mode { OLD_A, OLD_B };\n"
        "extern int g_old __attribute__((deprecated));\n"
        "__attribute__((deprecated)) int old_api(struct legacy *l, int x);",
        "int g_old = 4;\nint old_api(struct legacy *l, int x) { return (l ? l->x : 0) + x - 1; }",
    ),
    "elf.vget": ("int vget(int x);", "int vget(int x) { return x * 2; }"),
    "elf.wget": ("int wget(int x);", "int wget(int x) { return x * 3 + 1; }"),
    "elf.weak": (
        "int soft(int x);",
        "__attribute__((weak)) int soft(int x) { return x + 5; }",
    ),
    "elf.vsum": (
        "int vsum(int n);",
        "int vsum(int n) { char b[32]; memset(b, n, sizeof b); return b[1] + n; }",
    ),
    "elf.vfmt": ("int vfmt(int n, ...);", "int vfmt(int n, ...) { return n * 4; }"),
    "elf.pread": ("int pread_int(int *p);", "int pread_int(int *p) { return *p + 1; }"),
    "elf.pref": (
        "int *pref(void);",
        "static int s_ref = 7;\nint *pref(void) { return &s_ref; }",
    ),
    "elf.num_i": ("int num_i(union num u);", "int num_i(union num u) { return u.i; }"),
    "elf.flags_b": (
        "int flags_b(struct flags *f);",
        "int flags_b(struct flags *f) { return f->b + f->c; }",
    ),
    "elf.buf_n": (
        "int buf_n(struct buf *b);",
        "int buf_n(struct buf *b) { return b->n; }",
    ),
    "elf.ctx_a": (
        "int ctx_a(struct ctx *c);",
        "int ctx_a(struct ctx *c) { return c->a; }",
    ),
    "elf.level_code": (
        "int level_code(enum level l);",
        "int level_code(enum level l) { return l == LOW ? 1 : l == MID ? 2 : 3; }",
    ),
    "elf.counted": (
        "count_t counted(count_t c);",
        "count_t counted(count_t c) { return c + 1; }",
    ),
    "elf.tls": (
        "extern __thread int t_slot;\nint tls_get(void);",
        "__thread int t_slot = 9;\nint tls_get(void) { return t_slot; }",
    ),
    "elf.obj": ("extern int g_obj;", "int g_obj = 11;"),
    "elf.tab": ("extern const int g_tab[4];", "const int g_tab[4] = {1, 2, 3, 4};"),
    "elf.k": ("extern const int g_k;", "const int g_k = 3;"),
    "elf.al": (
        "extern int g_al __attribute__((aligned(16)));",
        "int g_al __attribute__((aligned(16))) = 5;",
    ),
    "elf.banner": ("extern const char banner[];", 'const char banner[] = "edit";'),
    # Declarations an operator adds land here.
    "elf.extra": ("", ""),
}

ELF_SEED = Seed(
    name="c_elf",
    lang="c",
    header_template=(
        "#ifndef EDIT_SEED_ELF_H",
        "#define EDIT_SEED_ELF_H",
        _VIS_OPEN,
        "@@elf.const@@",
        "@@elf.union@@",
        "@@elf.flags@@",
        "@@elf.buf@@",
        "@@elf.ctx@@",
        "@@elf.level@@",
        "@@elf.count_t@@",
        "@@elf.legacy@@",
        "@@elf.vget@@",
        "@@elf.wget@@",
        "@@elf.weak@@",
        "@@elf.vsum@@",
        "@@elf.vfmt@@",
        "@@elf.pread@@",
        "@@elf.pref@@",
        "@@elf.num_i@@",
        "@@elf.flags_b@@",
        "@@elf.buf_n@@",
        "@@elf.ctx_a@@",
        "@@elf.level_code@@",
        "@@elf.counted@@",
        "@@elf.tls@@",
        "@@elf.obj@@",
        "@@elf.tab@@",
        "@@elf.k@@",
        "@@elf.al@@",
        "@@elf.banner@@",
        "@@elf.extra@@",
        _VIS_CLOSE,
        "#endif",
    ),
    source_template=(
        '#include "edit_seed.h"',
        "#include <string.h>",
        "@@elf.legacy@@",
        "@@elf.vget@@",
        "@@elf.wget@@",
        "@@elf.weak@@",
        "@@elf.vsum@@",
        "@@elf.vfmt@@",
        "@@elf.pread@@",
        "@@elf.pref@@",
        "@@elf.num_i@@",
        "@@elf.flags_b@@",
        "@@elf.buf_n@@",
        "@@elf.ctx_a@@",
        "@@elf.level_code@@",
        "@@elf.counted@@",
        "@@elf.tls@@",
        "@@elf.obj@@",
        "@@elf.tab@@",
        "@@elf.k@@",
        "@@elf.al@@",
        "@@elf.banner@@",
        "@@elf.extra@@",
    ),
    roles=_ELF_ROLES,
)

# C++ class and linkage shapes: constructors, final, access, static and
# ref-qualified members, overrides, pure virtuals, inline namespaces, ABI
# tags, virtual and multiple inheritance, overloads, scoped enums, hidden
# friends. Role names start with ``x.``.
_XCXX_ROLES: dict[str, RoleText] = {
    "x.widget": (
        "class Widget {\npublic:\n  Widget();\n  explicit Widget(int v);\n"
        "  virtual ~Widget();\n  virtual int get() const;\n  int v;\n};",
        "Widget::Widget() : v(1) {}\nWidget::Widget(int x) : v(x) {}\n"
        "Widget::~Widget() {}\nint Widget::get() const { return v * 10; }",
    ),
    "x.gadget": (
        "struct Gadget { Gadget(long g); long g; };",
        "Gadget::Gadget(long x) : g(x) {}",
    ),
    "x.sealed": (
        # Polymorphic, so `final` lets callers devirtualize `v()`.
        "class Sealed final { public: int s; int get() const; "
        "virtual int v() const; };",
        "int Sealed::get() const { return s + 1; }\n"
        "int Sealed::v() const { return s + 2; }",
    ),
    "x.calc": (
        "class Calc {\npublic:\n  int m(int a);\n  int n(int a);\n  int r();\n"
        "  int g();\n  int g() const;\n  int safe() noexcept;\n  int field;\n"
        "  int cache;\n};",
        "int Calc::m(int a) { return a + 1; }\nint Calc::n(int a) { return a + 2; }\n"
        "int Calc::r() { return 3; }\nint Calc::g() { return 4; }\n"
        "int Calc::g() const { return 5; }\nint Calc::safe() noexcept { return 6; }",
    ),
    "x.poly": (
        "struct Base { virtual int m(); virtual ~Base(); };\n"
        "struct Derived : Base { int m(); };",
        "int Base::m() { return 1; }\nBase::~Base() {}\nint Derived::m() { return 2; }",
    ),
    "x.ns": (
        "namespace lib {\ninline namespace v1 {\nint f1(int a);\nint f2(int a);\n}\n}",
        "namespace lib {\ninline namespace v1 {\nint f1(int a) { return a + 1; }\n"
        "int f2(int a) { return a + 2; }\n}\n}",
    ),
    "x.tagged": ("int tagf(int a);", "int tagf(int a) { return a * 7; }"),
    "x.vbase": (
        "struct VB { int b; virtual ~VB(); };\nstruct VD : VB { int d; virtual ~VD(); };",
        "VB::~VB() {}\nVD::~VD() {}",
    ),
    "x.multi": (
        "struct B1 { int a; virtual ~B1(); virtual int f1(); };\n"
        "struct B2 { int b; virtual ~B2(); virtual int f2(); };\n"
        "struct MI : B1, B2 { virtual ~MI(); int f2(); };",
        "B1::~B1() {}\nint B1::f1() { return 1; }\nB2::~B2() {}\n"
        "int B2::f2() { return 2; }\nMI::~MI() {}\nint MI::f2() { return 22; }",
    ),
    "x.plain": (
        "struct Plain { int a; int get(); };",
        "int Plain::get() { return a + 1; }",
    ),
    "x.over": ("int pick(int a);", "int pick(int a) { return a; }"),
    "x.tone": (
        "enum tone { T_LO, T_HI };\nint tone_code(tone t);",
        "int tone_code(tone t) { return t == T_LO ? 1 : 2; }",
    ),
    "x.shade": (
        "enum class shade { dark, light };\nint shade_code(shade s);",
        "int shade_code(shade s) { return s == shade::dark ? 1 : 2; }",
    ),
    "x.cmp": (
        "struct Cmp { int a; };\nstruct Eq { int a; "
        "friend bool operator==(Eq x, Eq y) { return x.a == y.a; } };",
        "",
    ),
    # Member qualifiers and a static data member: layout-neutral edits.
    "x.quals": (
        "struct Quals {\n  mutable int hits;\n  const int k;\n  volatile int reg;\n"
        "  static int count;\n  int get() const;\n};",
        "int Quals::count = 0;\nint Quals::get() const { return k + hits; }",
    ),
    "x.shape": (
        "struct Shape { virtual int area() = 0; virtual ~Shape(); };",
        "Shape::~Shape() {}",
    ),
    "x.ovr": (
        "struct OBase { virtual int h(); virtual ~OBase(); };\n"
        "struct ODer : OBase { int h() override; };",
        "int OBase::h() { return 1; }\nOBase::~OBase() {}\nint ODer::h() { return 2; }",
    ),
    # Default member initializers, default arguments, deprecation, a
    # private static member, restrict and extern "C" linkage.
    "x.defs": (
        "struct Cfg {\n  int depth = 4;\n  int mode = 1;\n"
        "  [[deprecated]] int legacy;\nprivate:\n  static int total;\npublic:\n"
        "  int sum() const;\n};\n"
        "int tune(int a, int b = 3);\nint shrink(int a, int b = 5);\n"
        "int fill(int* __restrict p, int n);\n"
        'extern "C" int cfun(int a);',
        "int Cfg::total = 0;\nint Cfg::sum() const { return depth + mode + total; }\n"
        "int tune(int a, int b) { return a + b; }\n"
        "int shrink(int a, int b) { return a - b; }\n"
        "int fill(int* __restrict p, int n) { return p ? n : 0; }\n"
        'extern "C" int cfun(int a) { return a * 3; }',
    ),
    # A virtual gaining noexcept, a second converting constructor, a VTT
    # that grows, a field that fits the tail padding, a source-only export.
    "x.vnx": (
        "struct VN { virtual int f(); virtual ~VN(); };",
        "int VN::f() { return 1; }\nVN::~VN() {}",
    ),
    "x.ctor2": ("struct CO { CO(int a); int v; };", "CO::CO(int a) : v(a) {}"),
    "x.vtt": (
        "struct VA { int a; virtual ~VA(); };\n"
        "struct VB2 : virtual VA { int b; virtual ~VB2(); };",
        "VA::~VA() {}\nVB2::~VB2() {}",
    ),
    "x.tailpad": (
        "struct FC { int a; char b; };\nint fc_use(FC* p);",
        "int fc_use(FC* p) { return p->a; }",
    ),
    "x.helper": ("int enp_a(int a);", "int enp_a(int a) { return a; }"),
    "x.extra": ("", ""),
}

XCXX_SEED = Seed(
    name="cxx_more",
    lang="c++",
    header_template=(
        "#ifndef EDIT_SEED_MORE_HPP",
        "#define EDIT_SEED_MORE_HPP",
        _VIS_OPEN,
        "@@x.widget@@",
        "@@x.gadget@@",
        "@@x.sealed@@",
        "@@x.calc@@",
        "@@x.poly@@",
        "@@x.ns@@",
        "@@x.tagged@@",
        "@@x.vbase@@",
        "@@x.multi@@",
        "@@x.plain@@",
        "@@x.over@@",
        "@@x.tone@@",
        "@@x.shade@@",
        "@@x.cmp@@",
        "@@x.quals@@",
        "@@x.shape@@",
        "@@x.ovr@@",
        "@@x.defs@@",
        "@@x.vnx@@",
        "@@x.ctor2@@",
        "@@x.vtt@@",
        "@@x.tailpad@@",
        "@@x.helper@@",
        "@@x.extra@@",
        _VIS_CLOSE,
        "#endif",
    ),
    source_template=(
        '#include "edit_seed.h"',
        "@@x.widget@@",
        "@@x.gadget@@",
        "@@x.sealed@@",
        "@@x.calc@@",
        "@@x.poly@@",
        "@@x.ns@@",
        "@@x.tagged@@",
        "@@x.vbase@@",
        "@@x.multi@@",
        "@@x.plain@@",
        "@@x.over@@",
        "@@x.tone@@",
        "@@x.shade@@",
        "@@x.quals@@",
        "@@x.shape@@",
        "@@x.ovr@@",
        "@@x.defs@@",
        "@@x.vnx@@",
        "@@x.ctor2@@",
        "@@x.vtt@@",
        "@@x.tailpad@@",
        "@@x.helper@@",
        "@@x.extra@@",
    ),
    roles=_XCXX_ROLES,
)

SEEDS: tuple[Seed, ...] = (C_SEED, CXX_SEED, STDLIB_SEED, ELF_SEED, XCXX_SEED)
SEED_BY_NAME: dict[str, Seed] = {s.name: s for s in SEEDS}
