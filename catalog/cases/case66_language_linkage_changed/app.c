/* DEMO: C application that calls parse_config() and validate_config().
   Compiled against v1 (extern "C" symbols). When v2 is swapped in,
   the unmangled symbols are gone — replaced by C++ mangled versions.
   The dynamic linker cannot find "parse_config" and kills the process. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    int result = parse_config("/etc/app.conf");
    printf("parse_config = %d (expected 1)\n", result);

    int valid = validate_config("/etc/app.conf");
    printf("validate_config = %d (expected 1)\n", valid);

    return (result == 1 && valid == 1) ? 0 : 1;
}
