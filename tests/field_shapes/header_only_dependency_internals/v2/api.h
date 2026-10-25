#pragma once
// The new build targets AVX-512 (the -mavx512f build of the field report).
#define FAKESIMD_AVX512 1
#include <fakesimd/simd.h>
namespace lib {
using V = fakesimd::vec<float>;
struct State {
  V acc;
  int n;
};
void init(State* s);
void add(State* s, float x);
float sum(const State* s);
}  // namespace lib
