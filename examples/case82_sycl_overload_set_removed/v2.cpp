#include "v2.h"

namespace mylib {

result_t compute (const descriptor&, const table&) { return {0}; }
result_t train   (const descriptor&, const table&) { return {0}; }
result_t infer   (const descriptor&, const table&) { return {0}; }
result_t finalize(const descriptor&, const table&) { return {0}; }

// All SYCL overloads omitted — no longer present in this build.

}  // namespace mylib
