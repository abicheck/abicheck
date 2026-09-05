// Both old and new libalgo are compiled against the v1 declaration of
// core_add. The .cpp does not depend on any shared header so the new
// bundle ships a byte-identical libalgo.so even when libcore changes.
extern "C" int core_add(int a, int b);
extern "C" int algo_sum(int lo, int hi) {
    int s = 0;
    for (int i = lo; i <= hi; ++i) s = core_add(s, i);
    return s;
}
