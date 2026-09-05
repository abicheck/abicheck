#ifdef USE_V2
#include "v2.h"
#else
#include "v1.h"
#endif
#include <cstdio>

int main() {
    // Relies on the default timeout. Compiles against v1.h; against v2.h it
    // fails with "too few arguments to function 'connect'".
    int rc = netcfg::connect("example.org");
    printf("rc=%d\n", rc);
    return 0;
}
