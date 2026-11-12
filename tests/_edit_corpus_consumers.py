"""Consumer programs for the execution oracle, one set per seed.

Each consumer *shape* is a ``main`` written against the seed's **base**
(old) header, the way a downstream program built before the edit would be.
Its output depends on the library's behavior in a way an ABI break perturbs:

- ``call``: calls functions with fixed arguments (signatures, by-value structs);
- ``alloc``: allocates ``struct rec`` itself and lets the library fill it
  (size: bytes written past or short of the consumer's ``sizeof``);
- ``field``: writes and reads an exported variable (copy relocation size);
- ``enum``: passes enumerators to a function that maps them by name;
- ``derive``: subclasses ``Shape`` and calls virtuals both ways (vtable);
- ``modern``: empty-member layout, scoped enum, a function template;
- ``stdlib``: ``std::string`` across the boundary (dual ABI).

C shapes are C89 and also compile as C++ (the C++ seed has the same core).
"""

from __future__ import annotations

_C_CALL = r"""#include <stdio.h>
#include "edit_seed.h"
int main(void) {
  struct point p;
  p.x = 1;
  p.y = 2;
  printf("add=%d\n", add_ints(2, 3));
  printf("scale=%.3f\n", scale(p, 2.0));
  printf("count=%d\n", get_count());
  printf("handle=%lld\n", (long long)handle_of(5));
  return 0;
}
"""

_C_ALLOC = r"""#include <stdio.h>
#include <string.h>
#include "edit_seed.h"
union box { struct rec r; double align; unsigned char b[sizeof(struct rec) + 16]; };
int main(void) {
  union box u;
  struct rec m;
  size_t i;
  unsigned long sum = 0;
  memset(u.b, 0xAB, sizeof u.b);
  fill_rec(&u.r, 7);
  for (i = 0; i < sizeof(struct rec); i++) sum = sum * 31u + u.b[i];
  printf("rec_bytes=%lu\n", sum);
  for (i = sizeof(struct rec); i < sizeof u.b; i++) {
    if (u.b[i] != 0xAB) { printf("overrun_at=%lu\n", (unsigned long)i); break; }
  }
  printf("id=%d weight=%.3f\n", u.r.id, u.r.weight);
  m = make_rec(9);
  printf("make_id=%d\n", m.id);
  return 0;
}
"""

_C_FIELD = r"""#include <stdio.h>
#include "edit_seed.h"
int main(void) {
  g_counter = 41;
  printf("count=%d g=%d\n", get_count(), g_counter);
  return 0;
}
"""

_C_ENUM = r"""#include <stdio.h>
#include "edit_seed.h"
int main(void) {
  printf("red=%d green=%d blue=%d\n", color_code(RED), color_code(GREEN), color_code(BLUE));
  return 0;
}
"""

_CXX_DERIVE = r"""#include <cstdio>
#include "edit_seed.h"
struct D : Shape { int area() const override { return 42; } };
int main() {
  Shape* s = new Shape();
  s->w = 7;
  std::printf("area=%d kind=%d\n", s->area(), s->kind());
  D d;
  d.w = 3;
  std::printf("lib_base=%d lib_derived=%d\n", shape_area(*s), shape_area(d));
  delete s;
  return 0;
}
"""

_CXX_MODERN = r"""#include <cstdio>
#include "edit_seed.h"
int main() {
  Tagged t{};
  t.v = 5;
  std::printf("tagged=%d twice=%d\n", tagged_v(t), twice(4));
  std::printf("mode=%d,%d\n", mode_code(mode::fast), mode_code(mode::slow));
  std::printf("clamp=%d\n", (int)clampv((short)-3));
  Buf<int> b{};
  std::printf("buf=%d\n", (int)(sizeof b.data / sizeof b.data[0]));
  return 0;
}
"""

_CXX_STDLIB = r"""#include <cstdio>
#include <string>
#include "edit_seed.h"
int main() {
  std::string g = greet("bob");
  std::printf("greet=%s len=%d add=%d\n", g.c_str(), name_len(g), add_ints(2, 3));
  return 0;
}
"""

# cxx_more: constructs, derives from and calls through every class, by the
# old header. A moved base subobject, a thunk offset or a vtable slot shows
# as a wrong value; a renamed or removed symbol as a load error.
_MORE_CLASSES = r"""#include <cstdio>
#include "edit_seed.h"
struct MyWidget : Widget { int get() const override { return 99; } };
int main() {
  Widget w(4);
  MyWidget mw;
  Gadget gd = 6L;
  Sealed s; s.s = 2;
  Calc c; c.field = 1; c.cache = 2;
  const Calc& cc = c;
  std::printf("widget=%d,%d gadget=%ld sealed=%d\n", w.get(), mw.get(), gd.g, s.get());
  std::printf("calc=%d,%d,%d,%d,%d,%d\n", c.m(1), c.n(1), c.r(), c.g(), cc.g(), c.safe());
  std::printf("ns=%d,%d tag=%d pick=%d\n", lib::f1(1), lib::f2(1), tagf(2), pick(5));
  std::printf("tone=%d,%d shade=%d\n", tone_code(T_LO), tone_code(T_HI), shade_code(shade::light));
  Eq e1{1}, e2{1};
  Cmp q{3};
  std::printf("eq=%d cmp=%d\n", (int)(e1 == e2), q.a);
  return 0;
}
"""

_MORE_HIER = r"""#include <cstdio>
#include "edit_seed.h"
int main() {
  Derived d;
  Base b;
  Base* pb = &d;
  VD vd; vd.b = 1; vd.d = 2;
  VB* pvb = &vd;
  MI mi; mi.a = 3; mi.b = 4;
  B2* pb2 = &mi;
  Plain p; p.a = 5;
  std::printf("poly=%d,%d vb=%d,%d\n", pb->m(), b.m(), pvb->b, vd.d);
  std::printf("mi=%d,%d,%d plain=%d sz=%d\n", pb2->f2(), pb2->b, mi.f1(), p.get(),
              (int)sizeof(MI));
  return 0;
}
"""

_C_SHAPES = {"call": _C_CALL, "alloc": _C_ALLOC, "field": _C_FIELD, "enum": _C_ENUM}

# c_elf: every exported function and object, by the old header. A versioned
# or retyped symbol, a moved TLS slot or a resized object shows as a load
# error or a different value.
_ELF_CALL = r"""#include <stdio.h>
#include "edit_seed.h"
int main(void) {
  int v = 6;
  union num u;
  struct flags fl;
  struct buf b;
  struct ctx c;
  u.i = 21;
  fl.a = 1; fl.b = 2; fl.c = 3;
  b.n = 4; b.d[0] = 0;
  c.a = 8; c.b = 9;
  printf("vget=%d wget=%d soft=%d vsum=%d vfmt=%d\n", vget(2), wget(2), soft(1), vsum(3), vfmt(2, 9));
  printf("pread=%d pref=%d num=%d flags=%d\n", pread_int(&v), *pref(), num_i(u), flags_b(&fl));
  printf("buf=%d ctx=%d level=%d,%d counted=%d old=%d\n", buf_n(&b), ctx_a(&c),
         level_code(LOW), level_code(MID), (int)counted(4), old_api(0, 3));
  return 0;
}
"""

_ELF_DATA = r"""#include <stdio.h>
#include "edit_seed.h"
int main(void) {
  g_obj += 1;
  g_al += 2;
  printf("obj=%d al=%d tls=%d slot=%d\n", g_obj, g_al, tls_get(), t_slot);
  /* g_k is referenced (its removal must fail the load) but not printed: a
     changed value is the edit's meaning, not a break. */
  volatile int k = g_k;
  (void)k;
  printf("tab=%d,%d banner=%s limit=%d\n", g_tab[0], g_tab[3], banner, EDIT_LIMIT);
  return 0;
}
"""

# c_elf: the roles the symbols-taxonomy operators edit (qualifiers, conventions,
# contracts, va_list, tags, renamed entry points, a reserved-name table).
_ELF_SYMBOLS = r"""#include <stdio.h>
#include <stdarg.h>
#include "edit_seed.h"
static int via_vlog2(const char *fmt, ...) {
  va_list ap;
  int r;
  va_start(ap, fmt);
  r = vlog2(fmt, ap);
  va_end(ap);
  return r;
}
int main(void) {
  int v = 5;
  char c = 'q';
  const int **pp = cpp();
  printf("cref=%d cpp=%d cread=%d msf=%d\n", *cref(), **pp, cread(&v), msf(3, 4));
  printf("put=%d dupc=%c vlog=%d vlog2=%d\n", put(&c), *dupc(&c), vlog("a", 0),
         via_vlog2("b", 7));
  printf("tag=%d,%d init=%d run=%d table=%d,%d\n", widget_tag(WIDGET_A),
         widget_tag(WIDGET_B), r_init(), r_run(4), _lib_table[0], _lib_table[3]);
  return 0;
}
"""

# cxx_more: the roles the symbols-taxonomy operators edit.
_MORE_SYMBOLS = r"""#include <cstdio>
#include "edit_seed.h"
int main() {
  std::printf("thrower=%d inl=%d\n", thrower(3), inl_add(2, 5));
  return 0;
}
"""

# c_elf: the roles the types-taxonomy operators edit.
_ELF_TYPES = r"""#include <stdio.h>
#include "edit_seed.h"
int main(void) {
  /* Positional: a consumer never names the reserved slot. */
  struct res r = {3, 0};
  ctx_t h = ctx_open();
  struct opq *o = opq_new();
  printf("lng=%ld disp=%d,%d,%d\n", lng_f(21), k1(1), k2(1), k3(1));
  printf("avx2=%d,%d,%d sse42=%d,%d,%d\n", k1_avx2(1), k2_avx2(1), k3_avx2(1),
         k1_sse42(1), k2_sse42(1), k3_sse42(1));
  /* The stamp's value is the edit's meaning, not a break: only its presence. */
  printf("vstamp=%d res=%d ctx=%d opq=%d\n", mylib_vstamp() != 0, res_a(&r),
         ctx_close(h), opq_val(o));
  return 0;
}
"""

# cxx_more: the roles the types-taxonomy operators edit. Objects with private
# members are static (zero-initialized), so every run prints the same values.
_MORE_TYPES = r"""#include <cstdio>
#include "edit_seed.h"
static SL sl;
static TP tp;
int main() {
  SD sd; sd.b = 4;
  SA* pa = &sd;
  VD2 v2; v2.a = 1; v2.b = 2;
  VA1* pv = &v2;
  TC tc{7};
  Box<int> box; box.v = 5;
  Kd kd; kd.a = 1;
  lib::Desc desc;
  std::printf("sort=%d frob=%d getv=%d,%ld conv=%d\n", lib::sort(2),
              lib::experimental::frob(3), getv<int>(4), getv<long>(5L), conv<int>(6));
  std::printf("route=%d,%d sec=%d,%d vb=%d,%d\n", route(1), route(2.5), pa->f(), sd.b,
              pv->a, v2.b);
  std::printf("sl=%d tp=%d,%d tc=%d run=%d\n", sl.get(), tp.get(), tp_use(&tp),
              tc_get(tc), lib::run<lib::fast_tag>());
  std::printf("consume=%d box=%d kd=%d desc=%d\n", consume(&box), make_box(8).v,
              kd.get(), desc.count());
#if __cplusplus >= 201402L
  std::printf("cb=%d\n", make_cb()(9));
#endif
  return 0;
}
"""

CONSUMERS: dict[str, dict[str, str]] = {
    "c_core": dict(_C_SHAPES),
    "cxx_classes": {**_C_SHAPES, "derive": _CXX_DERIVE, "modern": _CXX_MODERN},
    "cxx_stdlib": {"stdlib": _CXX_STDLIB},
    "c_elf": {
        "call": _ELF_CALL,
        "data": _ELF_DATA,
        "symbols": _ELF_SYMBOLS,
        "types": _ELF_TYPES,
    },
    "cxx_more": {
        "classes": _MORE_CLASSES,
        "hier": _MORE_HIER,
        "symbols": _MORE_SYMBOLS,
    },
}
