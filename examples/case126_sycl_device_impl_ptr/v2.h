#pragma once

#if defined(_WIN32)
#  define SYCL_EXPORT __declspec(dllexport)
#else
#  define SYCL_EXPORT __attribute__((visibility("default")))
#endif

namespace sycl {
namespace detail { struct device_impl; }

// Models sycl::device AFTER intel/llvm PR #20821: the
// std::shared_ptr<device_impl> was replaced with a raw device_impl*
// (device_impl is owned by the parent platform for the lifetime of the SYCL
// runtime, so the refcount was deemed unnecessary). A raw pointer is one word
// (8 bytes) instead of two, so sizeof(sycl::device) shrinks from 16 to 8 —
// an ABI break for every consumer that holds a device by value or embeds it.
class SYCL_EXPORT device {
public:
    device();
    device(const device &rhs);
    ~device();
    int get_id() const;
private:
    detail::device_impl *impl;
};
} // namespace sycl

extern "C" SYCL_EXPORT sycl::device *sycl_make_device();
