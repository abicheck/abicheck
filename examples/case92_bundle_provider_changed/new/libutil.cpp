// v2: libutil now hosts shared_util in addition to util_double_add.
extern "C" int core_add(int, int);
extern "C" int util_double_add(int a, int b) { return core_add(a, b) + core_add(a, b); }
extern "C" int shared_util(int x) { return x * 2; }
