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

SEEDS: tuple[Seed, ...] = (C_SEED, CXX_SEED, STDLIB_SEED)
SEED_BY_NAME: dict[str, Seed] = {s.name: s for s in SEEDS}
