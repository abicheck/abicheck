#include "v2.h"

namespace mylib {

concurrent_unordered_map_int::concurrent_unordered_map_int() : size_(0) {}

void concurrent_unordered_map_int::insert(int /*key*/) {
    ++size_;
}

int concurrent_unordered_map_int::size() const { return size_; }

} // namespace mylib
