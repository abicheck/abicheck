#ifndef LOGGER_H
#define LOGGER_H

/* Thread-local error context — each thread gets its own copy */
typedef struct ErrorCtx {
    int   code;          /* offset 0, 4 bytes */
    char  message[64];   /* offset 4, 64 bytes */
} ErrorCtx;
/* sizeof(ErrorCtx) = 68 */

extern __thread ErrorCtx tls_error;

void logger_set_error(int code, const char *msg);
int  logger_get_error_code(void);

#endif
