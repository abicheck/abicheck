// case87 v1 — header advertises:
//
//   template <typename Float, typename Distance = minkowski_distance<Float>>
//   class descriptor;
//
// The shipped library instantiates `descriptor<float>` which expands to
// `descriptor<float, minkowski_distance<float>>` — the explicit
// instantiation symbol embeds the default-substituted Distance type.
#pragma once

namespace mylib {

template <typename Float> struct minkowski_distance {};
template <typename Float> struct euclidean_distance {};

template <typename Float, typename Distance = minkowski_distance<Float>>
class descriptor {
public:
    descriptor();
    int dimension() const;
};

extern template class descriptor<float>;
extern template class descriptor<double>;

}  // namespace mylib
