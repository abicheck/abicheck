// case113 v1 — internal helper template `__detail::walk<T>` is called
// from a public inline algorithm. Consumers do not name the helper, but
// every instantiation lands in their symbol table because the public
// inline body dispatches to it.
#pragma once

namespace lib {
namespace __detail {

template <typename T>
T walk(T* first, T* last);

// Explicit instantiations the library promises.
extern template int walk<int>(int*, int*);
extern template float walk<float>(float*, float*);

} // namespace __detail

template <typename T>
inline T sum_range(T* first, T* last) {
    return __detail::walk<T>(first, last);
}

} // namespace lib
