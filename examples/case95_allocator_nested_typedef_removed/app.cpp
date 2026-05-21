// Consumer code that breaks at compile time against v2.
//
// Real STL-style consumer template:
//
//     template <class A>
//     typename A::value_type fetch(A& a, typename A::size_type i);
//
// resolving `A::value_type` against v2's `my_allocator` is a compile error
// because the nested typedef was removed.
#include "v1.h"  // swap to v2.h to see the source break

int main() {
    mylib::my_allocator a;
    // Use the public nested typedef as STL consumers historically did.
    mylib::my_allocator::value_type* p = a.allocate(4);
    a.deallocate(p, 4);
    return 0;
}
