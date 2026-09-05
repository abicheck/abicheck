#include "v2.h"
#include <cstdlib>

namespace mylib {

int* my_allocator::allocate(unsigned long n) {
    return static_cast<int*>(std::malloc(n * sizeof(int)));
}

void my_allocator::deallocate(int* p, unsigned long) {
    std::free(p);
}

} // namespace mylib
