#include "v1.h"
#include <stdio.h>

int main(void) {
    /* v1 ABI: lib_version is int and expected to be 1 */
    printf("lib_version = %d (as int)\n", lib_version);
    if (lib_version != 1) {
        printf("WRONG RESULT: global variable type/value contract changed\n");
        return 1;
    }
    return 0;
}
