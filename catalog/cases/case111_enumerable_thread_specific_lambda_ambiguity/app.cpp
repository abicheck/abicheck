// Consumer that constructs the type with an int — unambiguous in v1, still
// callable in v2 but adjacent code patterns become ambiguous.
#include "v1.h"  // swap to v2.h to see the new lambda-init overload

int main() {
    mylib::enumerable_thread_specific ets(42);
    return ets.local();
}
