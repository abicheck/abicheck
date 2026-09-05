// Consumer that uses the v1 enum-value attach API.
#include "v1.h"  // swap to v2.h to see source + link break

int main() {
    mylib::task_arena ta(mylib::attach_to_current);  // not available in v2
    return ta.concurrency();
}
