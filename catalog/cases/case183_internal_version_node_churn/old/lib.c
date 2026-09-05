#include "lib.h"

/* A private worker, deliberately versioned onto an internal node (the same
 * author-signaled "this is not for you" convention as glibc's GLIBC_PRIVATE
 * or nettle's NETTLE_INTERNAL_x / HOGWEED_INTERNAL_x nodes).
 */
int foo_worker(int x) { return x * 2; }

int public_api(int x) { return foo_worker(x) + 1; }
