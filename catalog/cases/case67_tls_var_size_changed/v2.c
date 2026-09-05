#include "v2.h"
#include <string.h>

__thread ErrorCtx tls_error = {0, 0, ""};

void logger_set_error(int code, const char *msg) {
    tls_error.code = code;
    tls_error.severity = 3;  /* new field — written at offset 4 */
    strncpy(tls_error.message, msg, sizeof(tls_error.message) - 1);
    tls_error.message[sizeof(tls_error.message) - 1] = '\0';
}

int logger_get_error_code(void) {
    return tls_error.code;
}
