// New side of libalgo is unchanged from old — it still calls core_mul
// even though the new libcore no longer exports it.  This is the
// canonical "looks-fine-in-isolation, broken-in-bundle" scenario.
#include "algo.h"
extern "C" int algo_sum(int lo, int hi) {
    int s = 0;
    for (int i = lo; i <= hi; ++i) s = core_add(s, i);
    return s;
}
extern "C" int algo_square(int x) { return core_mul(x, x); }
