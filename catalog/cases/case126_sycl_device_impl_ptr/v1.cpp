#include "v1.h"

namespace sycl {
namespace detail { struct device_impl { int id = 7; }; }

// The shared_ptr is left empty: this example exercises the *layout* of the
// member (sizeof == 16 on LP64), not its allocation, so no control block is
// instantiated.
device::device() : impl() {}
device::device(const device &rhs) = default;
device::~device() = default;
int device::get_id() const { return impl ? impl->id : -1; }
} // namespace sycl

extern "C" sycl::device *sycl_make_device() { return new sycl::device(); }
