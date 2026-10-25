#pragma once
// A header-only SIMD dependency shaped like intel/xvec: the library's vector
// typedef instantiates basic_vec<float> whose default ABI tag, hidden-friend
// operators, member typedefs and inline tables follow the target ISA.
#ifndef FAKESIMD_STORAGE
#define FAKESIMD_STORAGE 8
#endif
namespace fakesimd {
struct x86_avx2_tag {};
struct x86_avx512_tag {};
#if defined(FAKESIMD_AVX512)
using native_tag = x86_avx512_tag;
#define FAKESIMD_LANES 16
#else
using native_tag = x86_avx2_tag;
#define FAKESIMD_LANES 8
#endif
using target_tag = native_tag;
template <int N, typename Tag> struct fixed_size_abi {};
using native_abi = fixed_size_abi<FAKESIMD_LANES, native_tag>;
namespace detail {
template <typename Tag, typename T, int N> struct vec_traits {
  using register_type = T[N];
  using element_type = T;
};
#if defined(FAKESIMD_AVX512)
inline constexpr int permutexvar[4] = {3, 2, 1, 0};
#endif
}  // namespace detail
template <typename T, typename Abi = native_abi> struct basic_vec {
  using traits = detail::vec_traits<native_tag, T, FAKESIMD_LANES>;
  using register_type = typename traits::register_type;
  T data[FAKESIMD_STORAGE];
  basic_vec() = default;
  explicit basic_vec(T x) {
    for (auto& d : data) d = x;
  }
  friend basic_vec operator+(const basic_vec& a, const basic_vec& b) {
    basic_vec r = a;
    for (int i = 0; i < FAKESIMD_STORAGE; ++i) r.data[i] += b.data[i];
    return r;
  }
  friend basic_vec operator*(const basic_vec& a, T f) {
    basic_vec r = a;
    for (auto& d : r.data) d *= f;
    return r;
  }
  friend bool operator==(const basic_vec& a, const basic_vec& b) {
    for (int i = 0; i < FAKESIMD_STORAGE; ++i)
      if (a.data[i] != b.data[i]) return false;
    return true;
  }
};
template <typename T> T reduce(const basic_vec<T>& v) {
  T s{};
  for (auto d : v.data) s += d;
  return s;
}
template <typename T> using vec = basic_vec<T>;
}  // namespace fakesimd
