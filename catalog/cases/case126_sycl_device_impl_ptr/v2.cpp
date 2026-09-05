#include "v2.h"

namespace sycl {
namespace detail { struct device_impl { int id = 7; }; }

device::device() : impl(nullptr) {}
device::device(const device &rhs) = default;
device::~device() = default;
int device::get_id() const { return impl ? impl->id : -1; }
} // namespace sycl

extern "C" sycl::device *sycl_make_device() { return new sycl::device(); }
