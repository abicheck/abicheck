/* case31 app: Demonstrate enum member rename (source break, binary compatible).
 *
 * Compiled against v1.h which defines LOG_ERR=1, LOG_WARN=2, LOG_DBG=3.
 * v2.h renames these to LOG_ERROR, LOG_WARNING, LOG_DEBUG (same integer values).
 *
 * At the binary level, enum constants are compiled into immediate integer values.
 * The binary works fine with v2 because the values are unchanged.
 * But recompilation against v2 headers fails (old names don't exist).
 */
#include "v1.h"
#include <stdio.h>

int main(void) {
    printf("Enum rename demo (compiled against v1.h):\n\n");

    /* These enum constants are baked into the binary as integers */
    printf("Enum values compiled into binary:\n");
    printf("  LOG_NONE = %d\n", LOG_NONE);
    printf("  LOG_ERR  = %d\n", LOG_ERR);
    printf("  LOG_WARN = %d\n", LOG_WARN);
    printf("  LOG_DBG  = %d\n", LOG_DBG);
    printf("  LOG_MAX  = %d\n", LOG_MAX);

    /* Call library function with v1 enum name */
    printf("\nCalling set_log_level(LOG_ERR)  [value=%d] ... ", LOG_ERR);
    set_log_level(LOG_ERR);
    printf("OK\n");

    printf("Calling set_log_level(LOG_WARN) [value=%d] ... ", LOG_WARN);
    set_log_level(LOG_WARN);
    printf("OK\n");

    printf("Calling set_log_level(LOG_DBG)  [value=%d] ... ", LOG_DBG);
    set_log_level(LOG_DBG);
    printf("OK\n");

    printf("\nSummary:\n");
    printf("  - Binary works with v2 lib: enum values are identical\n");
    printf("    LOG_ERR=1 == LOG_ERROR=1, LOG_WARN=2 == LOG_WARNING=2, etc.\n");
    printf("  - Source break: recompiling against v2.h fails because\n");
    printf("    LOG_ERR, LOG_WARN, LOG_DBG no longer exist as identifiers\n");
    printf("  - This is a SOURCE-level ABI break, not a binary-level one\n");

    return 0;
}
