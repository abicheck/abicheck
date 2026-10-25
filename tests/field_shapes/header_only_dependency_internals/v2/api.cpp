#include "api.h"
namespace lib {
void init(State* s) {
  s->acc = V(0.0f);
  s->n = 0;
}
void add(State* s, float x) {
  if (!(s->acc == V(x))) s->acc = s->acc + V(x) * 1.0f;
  ++s->n;
}
float sum(const State* s) { return fakesimd::reduce(s->acc); }
}  // namespace lib
