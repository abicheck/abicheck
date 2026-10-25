#ifndef GENERIC_GUARD_API_H
#define GENERIC_GUARD_API_H
int base(int);
#ifdef FOO
int foo_one(int);
int foo_two(double);
#endif
#ifdef SYCL_LANGUAGE_VERSION
namespace sycl { class queue; }
int on_queue(sycl::queue&);
#endif
#endif
