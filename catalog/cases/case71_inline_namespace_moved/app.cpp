#include "v1.h"
#include <cstdio>

int main() {
    /* Compiled against v1: symbols are in crypto::v1:: */
    crypto::Context ctx{1, 256};
    int result = crypto::encrypt(&ctx, "hello", 5);
    std::printf("encrypt() = %d\n", result);
    std::printf("Expected: 262\n");
    return 0;
}
