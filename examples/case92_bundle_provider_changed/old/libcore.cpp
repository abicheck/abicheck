// v1: libcore exports both core_add and shared_util.
extern "C" int core_add(int a, int b) { return a + b; }
extern "C" int shared_util(int x) { return x * 2; }
