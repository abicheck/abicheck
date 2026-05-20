#include "v2.h"

namespace mylib {

descriptor::descriptor() : impl_(std::make_shared<detail::descriptor_impl>()) {}

}  // namespace mylib
