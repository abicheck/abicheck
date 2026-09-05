#ifndef LOGGER_H
#define LOGGER_H

/* Thread-local error context — EXPANDED in v2.
   New 'severity' field inserted BEFORE message, shifting message offset.
   v1 message at offset 4, v2 message at offset 8.
   sizeof(ErrorCtx) changes from 68 to 72 bytes. */
typedef struct ErrorCtx {
    int   code;          /* offset 0, 4 bytes (unchanged) */
    int   severity;      /* offset 4, 4 bytes (NEW — shifts message!) */
    char  message[64];   /* offset 8, 64 bytes (was at offset 4) */
} ErrorCtx;
/* sizeof(ErrorCtx) = 72 */

extern __thread ErrorCtx tls_error;

void logger_set_error(int code, const char *msg);
int  logger_get_error_code(void);

#endif
