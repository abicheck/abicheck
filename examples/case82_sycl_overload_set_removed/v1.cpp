#include "v1.h"

namespace mylib {

result_t compute (const descriptor&, const table&) { return {0}; }
result_t train   (const descriptor&, const table&) { return {0}; }
result_t infer   (const descriptor&, const table&) { return {0}; }
result_t finalize(const descriptor&, const table&) { return {0}; }

result_t compute (sycl::queue&, const descriptor&, const table&) { return {1}; }
result_t train   (sycl::queue&, const descriptor&, const table&) { return {1}; }
result_t infer   (sycl::queue&, const descriptor&, const table&) { return {1}; }
result_t finalize(sycl::queue&, const descriptor&, const table&) { return {1}; }

}  // namespace mylib
