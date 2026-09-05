#include "v1.h"

namespace mylib {
namespace detail {
class descriptor_impl {
public:
    int class_count = 2;
};
}  // namespace detail

descriptor::descriptor() : impl_(std::make_shared<detail::descriptor_impl>()) {}
descriptor::~descriptor() = default;
int descriptor::get_class_count() const { return impl_->class_count; }
void descriptor::set_class_count(int v) { impl_->class_count = v; }

}  // namespace mylib
