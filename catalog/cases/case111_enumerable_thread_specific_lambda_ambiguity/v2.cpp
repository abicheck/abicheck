#include "v2.h"

namespace mylib {

enumerable_thread_specific::enumerable_thread_specific(int initial_value)
    : value_(initial_value) {}

enumerable_thread_specific::enumerable_thread_specific(int_factory_t init)
    : value_(init ? init() : 0) {}

int enumerable_thread_specific::local() const { return value_; }

} // namespace mylib
