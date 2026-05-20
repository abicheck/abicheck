// v1: libutil is a thin layer over libcore; does not export shared_util.
extern "C" int core_add(int, int);
extern "C" int util_double_add(int a, int b) { return core_add(a, b) + core_add(a, b); }
