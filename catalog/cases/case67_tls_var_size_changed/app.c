/* DEMO: app compiled against v1 where tls_error.message is at offset 4.
   v2 inserts a 'severity' field at offset 4, pushing message to offset 8.
   The app reads tls_error.message at offset 4 (v1 layout) but v2 wrote
   the severity integer there — the app gets garbage instead of the string. */
#include "v1.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    /* Library sets error using v2 layout */
    logger_set_error(404, "not found");

    /* Read error code via library function — works fine */
    int code = logger_get_error_code();
    printf("error code = %d (expected 404)\n", code);

    /* Read message directly using v1 compiled layout.
       v1: message is at offset 4 (immediately after code)
       v2: severity=3 is at offset 4, message is at offset 8
       The app reads offset 4 as a char[] and gets the severity int bytes. */
    printf("message = \"%s\" (expected \"not found\")\n", tls_error.message);

    if (strcmp(tls_error.message, "not found") != 0) {
        printf("CORRUPTION: TLS struct layout changed — app reads v1 offset "
               "but library wrote v2 layout!\n");
        return 1;
    }
    return 0;
}
