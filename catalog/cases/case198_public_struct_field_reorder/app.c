/* DEMO: the consumer was compiled against v1's field order and reads `id`
   at offset 0. Under v2 that offset holds `flags`, so the value silently
   changes with no crash and no link error. */
#include "v1.h"
#include <stdio.h>

int main(void) {
    Record r;
    record_init(&r, 42);
    /* Read through the consumer's own (v1) layout, not through the library. */
    printf("consumer sees id = %d (expected 42)\n", r.id);
    /* Read through the library, which uses its own layout. */
    printf("library sees id = %d (expected 42)\n", record_id(&r));
    if (r.id != 42)
        printf("MISREAD: the consumer's offset for `id` no longer matches\n");
    return 0;
}
