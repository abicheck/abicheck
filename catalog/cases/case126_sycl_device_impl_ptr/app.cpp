/* DEMO: reproduces the ABI mismatch from intel/llvm PR #20821 in miniature.
 *
 * The app is compiled against v1's header, where sycl::device is 16 bytes
 * (it holds a std::shared_ptr<device_impl>). If it is then linked against a
 * v2 library — where sycl::device is 8 bytes (a raw device_impl*) — every
 * place the app embedded a device by value now has the wrong size and the two
 * sides disagree on the object's layout. Educational only. */
#include "v1.h"
#include <cstdio>

struct Holder {
    char before[8] = "BEFORE!";
    sycl::device dev;   /* app bakes in v1's 16-byte layout here */
    char after[8]  = "AFTER!!";
};

int main() {
    std::printf("app sees sizeof(sycl::device) = %zu (v1 layout = 16)\n",
                sizeof(sycl::device));

    /* The library was built with its own view of sycl::device. If that view
     * is v2 (8 bytes) the factory returns an object the app reads with the
     * wrong stride. */
    sycl::device *d = sycl_make_device();
    std::printf("device id via library = %d\n", d->get_id());

    Holder h;
    std::printf("before = %s\n", h.before);
    std::printf("after  = %s\n", h.after);
    return 0;
}
