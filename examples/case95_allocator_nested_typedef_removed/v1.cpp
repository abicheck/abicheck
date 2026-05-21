#include "v1.h"
#include <cstdlib>

namespace mylib {

int* my_allocator::allocate(size_type n) {
    return static_cast<int*>(std::malloc(n * sizeof(int)));
}

void my_allocator::deallocate(int* p, size_type) {
    std::free(p);
}

} // namespace mylib
