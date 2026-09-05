// v2: shared_util moved out of libcore. core_add stays.
extern "C" int core_add(int a, int b) { return a + b; }
// shared_util removed from libcore — now lives in libutil (see new/libutil.cpp).
