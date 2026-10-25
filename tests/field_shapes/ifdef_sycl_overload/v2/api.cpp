#include "api.h"
int foo(int x) { return x; }
#ifdef SYCL_LANGUAGE_VERSION
int foo(sycl::queue&) { return 1; }
#endif
