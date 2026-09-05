#pragma once
#include <memory>

#if defined(_WIN32)
#  define SYCL_EXPORT __declspec(dllexport)
#else
#  define SYCL_EXPORT __attribute__((visibility("default")))
#endif

namespace sycl {
namespace detail { struct device_impl; }

// Models sycl::device as it stored its implementation BEFORE intel/llvm PR
// #20821: a reference-counted std::shared_ptr<device_impl>. On a 64-bit
// target sizeof(std::shared_ptr) is two pointers (16 bytes), so the device's
// only data member occupies 16 bytes and sizeof(sycl::device) == 16.
class SYCL_EXPORT device {
public:
    device();
    device(const device &rhs);
    ~device();
    int get_id() const;
private:
    std::shared_ptr<detail::device_impl> impl;
};
} // namespace sycl

extern "C" SYCL_EXPORT sycl::device *sycl_make_device();
