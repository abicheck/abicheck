#include "algo.h"
extern "C" int algo_sum(int lo, int hi) {
    int s = 0;
    for (int i = lo; i <= hi; ++i) s = core_add(s, i);
    return s;
}
extern "C" int algo_square(int x) { return core_mul(x, x); }
