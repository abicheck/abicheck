#pragma once
int foo(int);
#ifdef SYCL_LANGUAGE_VERSION
#include <sycl/sycl.hpp>
int foo(sycl::queue&);
sycl::event* bar(sycl::queue&, int);
#endif
