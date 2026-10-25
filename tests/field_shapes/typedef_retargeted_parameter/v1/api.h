#pragma once
// The xvec shape without the dependency: V names basic_vec<float> on both
// sides, and a default template argument picks its width (an ISA flag does
// this in xvec). State completes the instantiation.
#define LIB_WIDTH 8
template <class T, int N = LIB_WIDTH> struct basic_vec { T d[N]; };
namespace lib {
using V = basic_vec<float>;
struct State { V acc; int n; };
V scale(V v, float f);
void init(State* s);
float sum(const State* s);
}
