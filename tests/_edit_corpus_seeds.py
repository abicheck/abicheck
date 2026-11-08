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
    "func.scale": (
        "double scale(struct point p, double f);",
        "double scale(struct point p, double f) { return (p.x + p.y) * f; }",
    ),
    "func.count": (
        "int get_count(void);",
        "int get_count(void) { return g_counter; }",
    ),
    "func.make": (
        "struct rec make_rec(int id);",
        "struct rec make_rec(int id) { struct rec r = {0}; r.id = id; return r; }",
    ),
    "func.color": (
        "int color_code(enum color c);",
        "int color_code(enum color c) { return (int)c; }",
    ),
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
    "@@var.counter@@",
)

_CORE_SOURCE: tuple[str, ...] = (
    '#include "edit_seed.h"',
    "@@var.counter@@",
    "@@func.add@@",
    "@@func.scale@@",
    "@@func.count@@",
    "@@func.make@@",
    "@@func.color@@",
    "@@func.handle@@",
)

C_SEED = Seed(
    name="c_core",
    lang="c",
    header_template=(
        "#ifndef EDIT_SEED_H",
        "#define EDIT_SEED_H",
        *_CORE_HEADER,
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
}

_CXX_HEADER: tuple[str, ...] = (
    "#ifndef EDIT_SEED_HPP",
    "#define EDIT_SEED_HPP",
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
    "@@func.add@@",
    "@@func.scale@@",
    "@@func.count@@",
    "@@func.make@@",
    "@@func.color@@",
    "@@func.handle@@",
    "@@func.twice@@",
    "@@var.counter@@",
    "#endif",
)

_CXX_SOURCE: tuple[str, ...] = (
    '#include "edit_seed.h"',
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
)

CXX_SEED = Seed(
    name="cxx_classes",
    lang="c++",
    header_template=_CXX_HEADER,
    source_template=_CXX_SOURCE,
    roles=_CXX_ROLES,
)

SEEDS: tuple[Seed, ...] = (C_SEED, CXX_SEED)
