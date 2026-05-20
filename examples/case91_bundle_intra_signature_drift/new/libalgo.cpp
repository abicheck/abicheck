// new libalgo is byte-identical to old libalgo: same v1 declaration of
// core_add (int, int). When shipped alongside the v2 libcore that
// exports core_add(long, long) at the same mangled name, callers pass
// arguments in the wrong registers — the linker is happy, the ABI is not.
extern "C" int core_add(int a, int b);
extern "C" int algo_sum(int lo, int hi) {
    int s = 0;
    for (int i = lo; i <= hi; ++i) s = core_add(s, i);
    return s;
}
