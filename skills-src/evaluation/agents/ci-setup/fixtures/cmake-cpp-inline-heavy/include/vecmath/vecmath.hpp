#pragma once
#include <cstddef>
#define VECMATH_DEFAULT_ALIGN 16
#define VECMATH_API_LEVEL 3
namespace vecmath {
template <typename T, std::size_t N>
struct alignas(VECMATH_DEFAULT_ALIGN) Vec { T v[N]; };
template <typename T, std::size_t N>
inline T dot(const Vec<T, N> &a, const Vec<T, N> &b, T scale = T(1)) {
    T s{};
    for (std::size_t i = 0; i < N; ++i) s += a.v[i] * b.v[i];
    return s * scale;
}
constexpr int api_level() { return VECMATH_API_LEVEL; }
double norm3(const Vec<double, 3> &a);
}
