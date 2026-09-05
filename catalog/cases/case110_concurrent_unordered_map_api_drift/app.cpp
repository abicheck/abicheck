// Consumer code that calls the v1 insert form (with rehash hint).
#include "v1.h"  // swap to v2.h to see source break + link break

int main() {
    mylib::concurrent_unordered_map_int m;
    m.insert(42, /*rehash_hint=*/8);   // not present in v2
    return m.size();
}
