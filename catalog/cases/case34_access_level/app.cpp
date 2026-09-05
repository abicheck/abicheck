#ifdef USE_V2
#include "v2.hpp"
#else
#include "v1.hpp"
#endif
#include <stdio.h>

int main() {
    Widget w;

    /* render() stays public in both v1 and v2 */
    w.render();
    printf("render() called OK\n");

    /* helper() is public in v1 but private in v2
     * This compiles against v1.hpp, and the binary still works
     * with v2's .so because access specifiers are compile-time only. */
    w.helper();
    printf("helper() called OK\n");

    /* cache is public in v1 but private in v2 */
    w.cache = 123;
    printf("cache = %d\n", w.cache);

    return 0;
}
