// case113 v2 — `__detail::walk` instantiation set shifted: `walk<float>`
// dropped, `walk<double>` added. Public `sum_range<float>` consumers
// previously baked `walk<float>` into their symbol table; against v2
// they fail to link.
#pragma once

namespace lib {
namespace __detail {

template <typename T>
T walk(T* first, T* last);

extern template int walk<int>(int*, int*);
extern template double walk<double>(double*, double*);

} // namespace __detail

template <typename T>
inline T sum_range(T* first, T* last) {
    return __detail::walk<T>(first, last);
}

} // namespace lib
