// case82 v2 — DPC++ build dropped. CPU entry points unchanged, SYCL
// overloads gone. Consumers compiled against v1 with `compute(queue, ...)`
// in their call graph get N unresolved symbols.
#pragma once

namespace sycl { class queue { public: int id = 0; }; }

namespace mylib {

struct descriptor { int n = 0; };
struct table      { int rows = 0; };
struct result_t   { int code = 0; };

result_t compute    (const descriptor& d, const table& t);
result_t train      (const descriptor& d, const table& t);
result_t infer      (const descriptor& d, const table& t);
result_t finalize   (const descriptor& d, const table& t);

// SYCL overloads removed — DPC++ build disabled.

}  // namespace mylib
