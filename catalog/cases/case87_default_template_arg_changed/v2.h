// case87 v2 — default Distance changed from minkowski_distance to
// euclidean_distance. Consumer source compiles fine ("we just changed
// the default"). But:
//
// - The header's `extern template class descriptor<float>` now expands to
//   descriptor<float, euclidean_distance<float>> — a DIFFERENT type with
//   a DIFFERENT mangled name from the v1 shipped symbol.
// - A consumer compiled against v1 references
//   `descriptor<float, minkowski_distance<float>>` symbols; v2 .so ships
//   only `descriptor<float, euclidean_distance<float>>`.
#pragma once

namespace mylib {

template <typename Float> struct minkowski_distance {};
template <typename Float> struct euclidean_distance {};

template <typename Float, typename Distance = euclidean_distance<Float>>
class descriptor {
public:
    descriptor();
    int dimension() const;
};

extern template class descriptor<float>;
extern template class descriptor<double>;

}  // namespace mylib
