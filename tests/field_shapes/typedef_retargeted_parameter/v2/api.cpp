#include "api.h"
namespace lib {
V scale(V v, float f) { for (float& x : v.d) x *= f; return v; }
void init(State* s) { s->n = 0; }
float sum(const State* s) { return static_cast<float>(s->n); }
}
