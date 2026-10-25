#pragma once
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
