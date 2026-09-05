#include "v1.h"

namespace mylib {

enumerable_thread_specific::enumerable_thread_specific(int initial_value)
    : value_(initial_value) {}

int enumerable_thread_specific::local() const { return value_; }

} // namespace mylib
