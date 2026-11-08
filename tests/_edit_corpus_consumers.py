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

_C_SHAPES = {"call": _C_CALL, "alloc": _C_ALLOC, "field": _C_FIELD, "enum": _C_ENUM}

CONSUMERS: dict[str, dict[str, str]] = {
    "c_core": dict(_C_SHAPES),
    "cxx_classes": {**_C_SHAPES, "derive": _CXX_DERIVE, "modern": _CXX_MODERN},
    "cxx_stdlib": {"stdlib": _CXX_STDLIB},
}
