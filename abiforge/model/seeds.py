"""Seed translation units for the semantic edit corpus.

A seed is a header + source pair built from *roles*. A role is one declaration
(its header text) and, when the declaration has an out-of-line definition, the
matching source text. Templates place role texts with ``@@role@@`` lines, so an
operator edits one role and the seed re-renders both files. A role whose text
is empty is simply absent from the rendered file.

Both seeds use the same ``core`` roles so that an operator written against them
applies to C and C++ alike; the C++ seed adds class, template and enum-class
roles that only C++ operators require. Nothing here is a ground truth: the
expected ChangeKinds live with the operators in ``abiforge.operators``.
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
    # A seed whose baseline differs on purpose (a build that already carries a
    # feature an edit then removes) takes only the operators written for it:
    # an operator applies only when its roles include ``gate_role``.
    gate_role: str | None = None

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
    "elf.cref": (
        "const int *cref(void);",
        "static int s_cref = 8;\nconst int *cref(void) { return &s_cref; }",
    ),
    "elf.cpp": (
        "const int **cpp(void);",
        "static const int s_cpp = 9;\nstatic const int *s_cppp = &s_cpp;\n"
        "const int **cpp(void) { return &s_cppp; }",
    ),
    "elf.ms": ("int msf(int a, int b);", "int msf(int a, int b) { return a * b + 1; }"),
    "elf.attr": (
        "int put(char *p);\nchar *dupc(char *s) __attribute__((returns_nonnull));",
        "int put(char *p) { return p ? *p : 0; }\n"
        "static char s_dupc[2];\nchar *dupc(char *s) { return s ? s : s_dupc; }",
    ),
    # Declared, not defined: the declaration names no export until one is added.
    "elf.late": ("int late_f(int x);\nextern int g_late;", ""),
    "elf.va": (
        "int vlog(const char *fmt, void *ap);\nint vlog2(const char *fmt, va_list ap);",
        "int vlog(const char *fmt, void *ap) { return fmt[0] + (ap != 0); }\n"
        "int vlog2(const char *fmt, va_list ap) { return fmt[0] + va_arg(ap, int); }",
    ),
    "elf.ser": (
        "enum widget_tag_id { WIDGET_A = 1, WIDGET_B = 2 };\n"
        "int widget_tag(enum widget_tag_id t);",
        "int widget_tag(enum widget_tag_id t) { return t == WIDGET_A ? 10 : 20; }",
    ),
    "elf.ren": (
        "int r_init(void);\nint r_run(int n);",
        "int r_init(void) { return 1; }\nint r_run(int n) { return n + 1; }",
    ),
    # A reserved-name exported table (glibc's `_IO_2_1_stdin_` shape).
    "elf.itab": ("extern int _lib_table[4];", "int _lib_table[4] = {1, 2, 3, 4};"),
    "elf.cread": (
        "int cread(const int *p);",
        "int cread(const int *p) { return *p + 2; }",
    ),
    # Declarations an operator adds land here.
    "elf.extra": ("", ""),
}

ELF_SEED = Seed(
    name="c_elf",
    lang="c",
    header_template=(
        "#ifndef EDIT_SEED_ELF_H",
        "#define EDIT_SEED_ELF_H",
        "#include <stdarg.h>",
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
        "@@elf.cref@@",
        "@@elf.cpp@@",
        "@@elf.cread@@",
        "@@elf.ms@@",
        "@@elf.attr@@",
        "@@elf.late@@",
        "@@elf.va@@",
        "@@elf.ser@@",
        "@@elf.ren@@",
        "@@elf.itab@@",
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
        "@@elf.cref@@",
        "@@elf.cpp@@",
        "@@elf.cread@@",
        "@@elf.ms@@",
        "@@elf.attr@@",
        "@@elf.late@@",
        "@@elf.va@@",
        "@@elf.ser@@",
        "@@elf.ren@@",
        "@@elf.itab@@",
        "@@elf.extra@@",
    ),
    roles=_ELF_ROLES,
)

# A second C library seen as an ELF object, whose *baseline* already carries
# what a c_elf edit adds: an executable stack, packed relative relocations,
# DT_RPATH, a text relocation, initial-exec TLS, an IFUNC, a symbol-less
# version node, fortified and newer-glibc imports, no SONAME. An edit removes
# one. Only operators that name ``alt.gate`` apply (``Seed.gate_role``), so
# c_elf's link edits, which rewrite the flags from c_elf's own baseline, do
# not land here. Role names start with ``alt.``.
_ALT_LDFLAGS = (
    "-Wl,--no-as-needed -lm -lgcc_s -Wl,--as-needed -Wl,-z,now -Wl,-z,execstack "
    "-Wl,-z,pack-relative-relocs -Wl,-z,notext -Wl,-rpath,/opt/edit/lib "
    "-Wl,--disable-new-dtags"
)
_ALT_ROLES: dict[str, RoleText] = {
    "build.cflags": ("-fstack-protector-strong -ftls-model=initial-exec", ""),
    "build.ldflags": (_ALT_LDFLAGS, ""),
    # LIBEDIT_1.1 binds no symbol: no consumer can ever require it.
    "build.vscript": (
        "LIBEDIT_1.0 { global: *; };\nLIBEDIT_1.1 { local: edit_no_such_*; } LIBEDIT_1.0;",
        "",
    ),
    "build.soname": ("", ""),
    "alt.gate": ("", ""),
    "alt.obj": ("extern int g_alt;", "int g_alt = 1;"),
    "alt.textrel": (
        "",
        # An absolute address in .text: a relocation against the code segment.
        "#ifdef __x86_64__\n"
        '__asm__(".text\\n.p2align 3\\nedit_textref: .quad g_alt\\n.previous");\n'
        # x86 only: elsewhere an absolute word in .text may not link at all.
        "#elif defined(__i386__)\n"
        '__asm__(".text\\n.p2align 2\\nedit_textref: .long g_alt\\n.previous");\n'
        "#endif",
    ),
    "alt.tls": (
        "extern __thread int t_ie;\nint tie_get(void);",
        "__thread int t_ie = 4;\nint tie_get(void) { return t_ie; }",
    ),
    "alt.ifunc": (
        "int fast(int x);",
        "static int fast_impl(int x) { return x + 7; }\n"
        "static void *fast_resolve(void) { return (void *)fast_impl; }\n"
        'int fast(int x) __attribute__((ifunc("fast_resolve")));',
    ),
    "alt.env": (
        "int envq(int x);",
        "#include <stdlib.h>\n"
        'int envq(int x) { return x + (getenv("EDIT_CORPUS_NONE") != 0); }',
    ),
    "alt.rnd": (
        "int rnd(int x);",
        "#include <sys/random.h>\n"
        "int rnd(int x) { return x + (int)getrandom((void *)0, 0, 0); }",
    ),
    "alt.chk": (
        "int chk_copy(char *dst, const char *src, int n);",
        "#include <stddef.h>\n"
        "void *__memcpy_chk(void *d, const void *s, size_t n, size_t dn);\n"
        "int chk_copy(char *dst, const char *src, int n) "
        "{ __memcpy_chk(dst, src, (size_t)n, 64); return n; }",
    ),
    "alt.ver": (
        "int api_v1_open(int x);\nint api_v1_read(int x);\nint api_v1_close(int x);",
        "int api_v1_open(int x) { return x + 1; }\n"
        "int api_v1_read(int x) { return x + 2; }\n"
        "int api_v1_close(int x) { return x + 3; }",
    ),
    "alt.pair": (
        "struct pair2 { long a; long b; };\nstruct pair2 mkpair(long a);",
        "struct pair2 mkpair(long a) { struct pair2 p = { a, a + 1 }; return p; }",
    ),
    "alt.ld": (
        "long double ld_half(long double x);",
        "long double ld_half(long double x) { return x / 2; }",
    ),
    "alt.int": (
        "typedef int edit_int;\nedit_int iscale(edit_int v);\nedit_int ishift(edit_int v);\n"
        "edit_int imix(edit_int a, edit_int b);",
        "edit_int iscale(edit_int v) { return v * 3; }\n"
        "edit_int ishift(edit_int v) { return v << 1; }\n"
        "edit_int imix(edit_int a, edit_int b) { return a ^ b; }",
    ),
    "alt.time": (
        "#include <time.h>\ntime_t when_next(time_t t);",
        "time_t when_next(time_t t) { return t + 1; }",
    ),
    "alt.inl": (
        "static __inline__ int hscale(int v, int f) { return v * f; }\nint huse(int v);",
        "int huse(int v) { return hscale(v, 2); }",
    ),
    "alt.helper": (
        "int edit_internal_helper(int x);",
        "int edit_internal_helper(int x) { return x - 1; }",
    ),
}

ALT_SEED = Seed(
    name="c_elf_alt",
    lang="c",
    header_template=(
        "#ifndef EDIT_SEED_ALT_H",
        "#define EDIT_SEED_ALT_H",
        _VIS_OPEN,
        "@@alt.obj@@",
        "@@alt.tls@@",
        "@@alt.ifunc@@",
        "@@alt.env@@",
        "@@alt.rnd@@",
        "@@alt.chk@@",
        "@@alt.ver@@",
        "@@alt.pair@@",
        "@@alt.ld@@",
        "@@alt.int@@",
        "@@alt.time@@",
        "@@alt.inl@@",
        "@@alt.helper@@",
        _VIS_CLOSE,
        "#endif",
    ),
    source_template=(
        '#include "edit_seed.h"',
        "@@alt.obj@@",
        "@@alt.textrel@@",
        "@@alt.tls@@",
        "@@alt.ifunc@@",
        "@@alt.env@@",
        "@@alt.rnd@@",
        "@@alt.chk@@",
        "@@alt.ver@@",
        "@@alt.pair@@",
        "@@alt.ld@@",
        "@@alt.int@@",
        "@@alt.time@@",
        "@@alt.inl@@",
        "@@alt.helper@@",
    ),
    roles=_ALT_ROLES,
    gate_role="alt.gate",
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
    # Layout probes for the padding-absorbed field edits: the hole between
    # ``a`` and ``b`` (inserted into), and a member that sits in that hole.
    "x.inspad": (
        "struct IP { char a; int b; };\nint ip_use(IP* p);",
        "int ip_use(IP* p) { return p->b; }",
    ),
    "x.rmpad": (
        "struct RP { char a; char x; int b; };\nint rp_use(RP* p);",
        "int rp_use(RP* p) { return p->b; }",
    ),
    "x.helper": ("int enp_a(int a);", "int enp_a(int a) { return a; }"),
    # A dynamic exception specification (before C++17) and an inline function.
    "x.throws": (
        "#if __cplusplus >= 201703L\n#define EDIT_THROWS(...)\n#else\n"
        "#define EDIT_THROWS(...) throw(__VA_ARGS__)\n#endif\n"
        "int thrower(int a) EDIT_THROWS(int);",
        "int thrower(int a) EDIT_THROWS(int) { if (a < 0) throw 1; return a; }",
    ),
    "x.inl": ("inline int inl_add(int a, int b) { return a + b; }", ""),
    "x.extra": ("", ""),
}

# Record- and base-layout probes, one per ``abiforge.model.edit_model`` class the
# layout operators (``abiforge.operators.layout``) generate. Members are
# char/short/int/unsigned only, so the layout is the same at -m32 and LP64.
_LAYOUT_ROLES: dict[str, RoleText] = {
    "lay.ihead": (
        "struct LIH { int a; int b; };\nint lih_use(LIH* p);",
        "int lih_use(LIH* p) { return p->b; }",
    ),
    "lay.imid": (
        "struct LIM { int a; int b; };\nint lim_use(LIM* p);",
        "int lim_use(LIM* p) { return p->b; }",
    ),
    "lay.rhead": (
        "struct LRH { int z; int a; int b; };\nint lrh_use(LRH* p);",
        "int lrh_use(LRH* p) { return p->b; }",
    ),
    "lay.rmid": (
        "struct LRM { int a; int z; int b; };\nint lrm_use(LRM* p);",
        "int lrm_use(LRM* p) { return p->b; }",
    ),
    "lay.rtail": (
        "struct LRT { int a; char b; char c; };\nint lrt_use(LRT* p);",
        "int lrt_use(LRT* p) { return p->a; }",
    ),
    "lay.tsame": (
        "struct LTS { int a; int b; };\nint lts_use(LTS* p);",
        "int lts_use(LTS* p) { return p->a; }",
    ),
    "lay.tshrink": (
        "struct LTK { int a; char c; };\nint ltk_use(LTK* p);",
        "int ltk_use(LTK* p) { return p->c; }",
    ),
    "lay.talign": (
        "struct LTA { char a; char b[4]; };\nint lta_use(LTA* p);",
        "int lta_use(LTA* p) { return p->a; }",
    ),
    "lay.ashrink": (
        "struct LAS { char a[8]; int b; };\nint las_use(LAS* p);",
        "int las_use(LAS* p) { return p->b; }",
    ),
    "lay.agrow": (
        "struct LAG { char a[1]; int b; };\nint lag_use(LAG* p);",
        "int lag_use(LAG* p) { return p->b; }",
    ),
    "lay.ralign": (
        "struct LRA { int a; };\nint lra_use(LRA* p);",
        "int lra_use(LRA* p) { return p->a; }",
    ),
    "lay.radd": ("", ""),
    "lay.bwabs": (
        "struct LBW { unsigned a : 3; unsigned b : 4; };\nint lbw_use(LBW* p);",
        "int lbw_use(LBW* p) { return (int)p->a; }",
    ),
    "lay.bwshift": (
        "struct LBS { unsigned a : 16; unsigned b : 16; int c; };\nint lbs_use(LBS* p);",
        "int lbs_use(LBS* p) { return p->c; }",
    ),
    "lay.bftof": (
        "struct LBF { unsigned a : 8; unsigned b : 8; int c; };\nint lbf_use(LBF* p);",
        "int lbf_use(LBF* p) { return p->c; }",
    ),
    "layb.remove": (
        "struct LB0 { int k; };\nstruct LD0 : LB0 { int d; };\nint ld0_use(LD0* p);",
        "int ld0_use(LD0* p) { return p->d; }",
    ),
    "layb.add": (
        "struct LB1 { int k; };\nstruct LD1 { int d; };\nint ld1_use(LD1* p);",
        "int ld1_use(LD1* p) { return p->d; }",
    ),
    "layb.nonvirt": (
        "struct LB2 { int k; };\nstruct LD2 : virtual LB2 { int d; };\nint ld2_use(LD2* p);\nint ld2_make();",
        "int ld2_use(LD2* p) { return p->d; }\n"
        "// Constructing LD2 makes the compiler emit its full DWARF definition (a\n"
        "// class with a virtual base and no key function is otherwise declaration-only).\n"
        "int ld2_make() { LD2 v; v.d = 1; return v.d; }",
    ),
    "layb.access": (
        "struct LB3 { int k; };\nstruct LD3 : public LB3 { int d; };\nint ld3_use(LD3* p);",
        "int ld3_use(LD3* p) { return p->d; }",
    ),
    "lay.tcopy": (
        "struct LTC { int a; };\nint ltc_get(LTC v);",
        "int ltc_get(LTC v) { return v.a; }",
    ),
    "lay.stdlay": (
        "struct LSL { int a; int b; };\nint lsl_use(LSL* p);",
        "int lsl_use(LSL* p) { return p->a; }",
    ),
}
_XCXX_ROLES.update(_LAYOUT_ROLES)
_LAYOUT_SLOTS: tuple[str, ...] = tuple(f"@@{r}@@" for r in _LAYOUT_ROLES)

# Baselines for operators that each target one ChangeKind no other operator
# reaches (``abiforge.operators.kinds``): one self-contained declaration group
# per role, named ``K*``/``k*`` so they collide with nothing else here.
_KIND_ROLES: dict[str, RoleText] = {
    "k.vbo": (
        "struct KVA { int a; };\nstruct KVB { int b; };\n"
        "struct KVD : virtual KVA, virtual KVB { int d; };\nint kvd_use(KVD* p);\n"
        "int kvd_make();",
        "int kvd_use(KVD* p) { return p->a + p->b + p->d; }\n"
        "int kvd_make() { KVD v; v.a = 1; v.b = 2; v.d = 3; return kvd_use(&v); }",
    ),
    "k.svg": (
        "struct KSA { virtual int f(); };\nstruct KSB { int x; };\n"
        "struct KSD : KSA, KSB { int d; };\nKSD* ksd_make();",
        "int KSA::f() { return 1; }\nKSD* ksd_make() { return new KSD(); }",
    ),
    # Not POD for layout (a user-provided constructor): a derived class may
    # reuse KTP's tail padding, so its data size (dsize) is ABI.
    "k.tpr": (
        "struct KTP { KTP(); int i; char c; };\nint ktp_get(KTP* p);",
        "KTP::KTP() : i(1), c(2) {}\nint ktp_get(KTP* p) { return p->i + p->c; }",
    ),
    "k.rsv": (
        "struct KRS { int a; int reserved1; };\nint krs_get(KRS* p);",
        "int krs_get(KRS* p) { return p->a; }",
    ),
    "k.tvs": (
        "typedef char* kfoo_version_1_2_3;\nint kfoo_len(const char* s);",
        "int kfoo_len(const char* s) { return s[0]; }",
    ),
    "k.hnd": (
        "struct KHC;\ntypedef KHC* khandle;\nkhandle kh_new();",
        "struct KHC { int v; };\nkhandle kh_new() { return new KHC(); }",
    ),
    "k.opq": (
        "struct KOC;\nKOC* koc_new();\nint koc_get(KOC* p);",
        "struct KOC { int v; };\nKOC* koc_new() { return new KOC(); }\n"
        "int koc_get(KOC* p) { return p->v; }",
    ),
    # C names, so the ISA suffix ends the symbol (no mangled parameter list).
    "k.isa": (
        'extern "C" {\nint ksum_avx2(int a);\nint kdot_avx2(int a);\n'
        "int kscl_avx2(int a);\nint ksum_sse2(int a);\nint kdot_sse2(int a);\n"
        "int kscl_sse2(int a);\n}",
        'extern "C" {\nint ksum_avx2(int a) { return a + 1; }\n'
        "int kdot_avx2(int a) { return a + 2; }\nint kscl_avx2(int a) { return a + 3; }\n"
        "int ksum_sse2(int a) { return a + 4; }\nint kdot_sse2(int a) { return a + 5; }\n"
        "int kscl_sse2(int a) { return a + 6; }\n}",
    ),
    "k.inst": (
        # The header promises both instantiations (explicit instantiation
        # declarations), so each is part of the public contract.
        "template <class T> T ktwice(T v);\nextern template int ktwice<int>(int);\n"
        "extern template double ktwice<double>(double);",
        "template <class T> T ktwice(T v) { return v + v; }\n"
        "template int ktwice<int>(int);\ntemplate double ktwice<double>(double);",
    ),
    "k.ovl": (
        "int kput(int a);\nint kput(double a);",
        "int kput(int a) { return a; }\nint kput(double a) { return (int)a + 1; }",
    ),
    "k.inv": (
        "namespace kv { inline namespace v1 { int kv_f(int a); } }",
        "namespace kv { inline namespace v1 { int kv_f(int a) { return a; } } }",
    ),
    "k.exp": (
        "namespace kx { namespace experimental { int kx_f(int a); int kx_g(int a); } }",
        "namespace kx { namespace experimental {\n"
        "int kx_f(int a) { return a; }\nint kx_g(int a) { return a + 1; }\n} }",
    ),
    "k.tag": (
        "struct ktag_a {};\ntemplate <class T> int kfam();",
        "template <class T> int kfam() { return (int)sizeof(T); }\n"
        "template int kfam<ktag_a>();",
    ),
    "k.cpo": (
        "namespace kc { int kc_swap(int a); }",
        "namespace kc { int kc_swap(int a) { return -a; } }",
    ),
    "k.ret": (
        "template <class T> struct KBx { T v; };\nKBx<int> kbx_get();",
        "KBx<int> kbx_get() { return KBx<int>{1}; }",
    ),
    "k.tpp": (
        'template <class T> struct KPb { T v; };\nextern "C" int kpb_take(KPb<int> b);',
        'extern "C" int kpb_take(KPb<int> b) { return (int)b.v; }',
    ),
    "k.slk": (
        "struct KSK { int a; };\nint ksk_get(KSK* p);",
        "int ksk_get(KSK* p) { return p->a; }",
    ),
    "k.mtp": (
        "template <class T> struct KMb { T v; };\nint kmb_use(KMb<int>* p);",
        "int kmb_use(KMb<int>* p) { return p->v; }",
    ),
    "k.aeq": (
        "struct KAE { long a; };\nint kae_get(KAE* p);",
        "int kae_get(KAE* p) { return (int)p->a; }",
    ),
    "k.del": (
        "int kdl_keep(int a);\nint kdl_gone(int a);",
        "int kdl_keep(int a) { return a; }\nint kdl_gone(int a) { return a + 1; }",
    ),
    # A public class reaching a detail:: type through a pimpl pointer from an
    # inline member function.
    "k.ibr": (
        "namespace kib { namespace detail { struct Impl { int count; }; }\n"
        "class KW { detail::Impl* p; public: KW(); int n() const { return p->count; } }; }",
        "kib::KW::KW() : p(new detail::Impl{3}) {}",
    ),
    # An inline function returning a lambda: callers can hold the result
    # only in ``auto``. kurt_use calls it, so the library emits (weak)
    # symbols for kmake and the closure's call operator. A deduced return
    # type is C++14: under C++11 the group is empty (the operator needs the
    # ``deduced_return`` feature, so no C++11 cell runs it).
    "k.urt": (
        "#if __cplusplus >= 201402L\n"
        "inline auto kmake() { return [](int x) { return x + 7; }; }\n"
        "int kurt_use(int a);\n#endif",
        "#if __cplusplus >= 201402L\nint kurt_use(int a) { return kmake()(a); }\n#endif",
    ),
    # A namespace-scope using-declaration re-exporting a std function: consumer
    # source may spell krx::abs.
    "k.rex": (
        "#include <cstdlib>\nnamespace krx { using std::abs; int krx_f(int a); }",
        "namespace krx { int krx_f(int a) { return std::abs(a); } }",
    ),
    # Empty baselines: the operator adds the declaration.
    "k.pnv": ("", ""),
    "k.stl": ("", ""),
    "k.anon": ("", ""),
}
_XCXX_ROLES.update(_KIND_ROLES)
_LAYOUT_SLOTS += tuple(f"@@{r}@@" for r in _KIND_ROLES)

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
        "@@x.inspad@@",
        "@@x.rmpad@@",
        "@@x.helper@@",
        "@@x.throws@@",
        "@@x.inl@@",
        "@@x.extra@@",
        *_LAYOUT_SLOTS,
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
        "@@x.inspad@@",
        "@@x.rmpad@@",
        "@@x.helper@@",
        "@@x.throws@@",
        "@@x.inl@@",
        "@@x.extra@@",
        *_LAYOUT_SLOTS,
    ),
    roles=_XCXX_ROLES,
)

# A second C++ library whose baseline carries what a cxx_more edit could
# add: a GNU-unique static, replaced global allocation functions, std::string
# signatures, a by-value trivially copyable struct, an internal type a public
# one embeds, a header-only inline. Gated like c_elf_alt (``xa.gate``).
_NOEXCEPT = "noexcept"
_DEFAULT_VIS = '__attribute__((visibility("default")))'
_XA_ROLES: dict[str, RoleText] = {
    "build.cflags": ("", ""),
    "xa.gate": ("", ""),
    "xa.uniq": (
        "inline int &uniq_counter() { static int n = 0; return n; }\nint uniq_bump();",
        "int uniq_bump() { return ++uniq_counter(); }",
    ),
    "xa.alloc": (
        "",
        # Default visibility, so -fvisibility=hidden still exports them.
        f"{_DEFAULT_VIS} void *operator new(std::size_t n) "
        "{ void *p = std::malloc(n ? n : 1); if (!p) throw std::bad_alloc(); return p; }\n"
        f"{_DEFAULT_VIS} void operator delete(void *p) {_NOEXCEPT} {{ std::free(p); }}\n"
        f"{_DEFAULT_VIS} void operator delete(void *p, std::size_t) {_NOEXCEPT} "
        "{ std::free(p); }",
    ),
    "xa.str": (
        "int s_len(const std::string &s);\nint s_first(const std::string &s);\n"
        "std::string s_up(const std::string &s);\nstd::string s_cat(const std::string &a, "
        "const std::string &b);\nbool s_empty(const std::string &s);\n"
        "std::string s_make(int n);",
        "int s_len(const std::string &s) { return (int)s.size(); }\n"
        "int s_first(const std::string &s) { return s.empty() ? 0 : s[0]; }\n"
        "std::string s_up(const std::string &s) { std::string r = s; "
        "for (char &c : r) if (c >= 'a' && c <= 'z') c = (char)(c - 32); return r; }\n"
        "std::string s_cat(const std::string &a, const std::string &b) { return a + b; }\n"
        "bool s_empty(const std::string &s) { return s.empty(); }\n"
        "std::string s_make(int n) { return std::string((std::size_t)n, 'x'); }",
    ),
    "xa.tv": (
        "struct Tv { int a; };\nint tv_get(Tv t);",
        "int tv_get(Tv t) { return t.a + 1; }",
    ),
    "xa.leak": (
        "namespace lib {\nnamespace detail { struct Impl { int a; }; }\n"
        "struct Holder { detail::Impl impl; int x; };\nint holder_get(const Holder *h);\n}",
        "namespace lib { int holder_get(const Holder *h) { return h->impl.a + h->x; } }",
    ),
    # Never called by the library, so never emitted: header-only.
    "xa.inl": (
        "namespace demo { inline int clampi(int v, int lo, int hi) "
        "{ return v < lo ? lo : v > hi ? hi : v; } }",
        "",
    ),
    # Two explicit instantiations of an internal template, declared
    # ``extern template`` in the header and reached through a public inline.
    "xa.tmpl": (
        'namespace lib {\nnamespace __detail {\ntemplate <class T> __attribute__((visibility("default"))) T walk(T *a, T *b);\n'
        "extern template int walk<int>(int *, int *);\n"
        "extern template float walk<float>(float *, float *);\n}\n"
        "template <class T> inline T sum_range(T *a, T *b) { return __detail::walk<T>(a, b); }\n}",
        "namespace lib { namespace __detail {\n"
        "template <class T> T walk(T *a, T *b) { T s = T(); for (; a != b; ++a) s += *a; return s; }\n"
        "template int walk<int>(int *, int *);\ntemplate float walk<float>(float *, float *);\n} }",
    ),
    "xa.ch": (
        "int count_chars(const char *s);",
        "int count_chars(const char *s) { int n = 0; while (s[n]) ++n; return n; }",
    ),
}

XA_SEED = Seed(
    name="cxx_alt",
    lang="c++",
    header_template=(
        "#ifndef EDIT_SEED_XA_HPP",
        "#define EDIT_SEED_XA_HPP",
        "#include <string>",
        _VIS_OPEN,
        "@@xa.uniq@@",
        "@@xa.str@@",
        "@@xa.tv@@",
        "@@xa.leak@@",
        "@@xa.inl@@",
        "@@xa.tmpl@@",
        "@@xa.ch@@",
        _VIS_CLOSE,
        "#endif",
    ),
    source_template=(
        '#include "edit_seed.h"',
        "#include <cstdlib>",
        "#include <new>",
        "@@xa.uniq@@",
        "@@xa.alloc@@",
        "@@xa.str@@",
        "@@xa.tv@@",
        "@@xa.leak@@",
        "@@xa.tmpl@@",
        "@@xa.ch@@",
    ),
    roles=_XA_ROLES,
    gate_role="xa.gate",
)

SEEDS: tuple[Seed, ...] = (
    C_SEED,
    CXX_SEED,
    STDLIB_SEED,
    ELF_SEED,
    ALT_SEED,
    XCXX_SEED,
    XA_SEED,
)
SEED_BY_NAME: dict[str, Seed] = {s.name: s for s in SEEDS}
