#include "v1.h"
#include <stdio.h>
#include <string.h>
#include <stdalign.h>

int main(void) {
    /* In v1: sizeof(Variant) = 8  (int tag + union{int,float} = 4+4)
     * In v2: sizeof(Variant) = 16 (int tag + padding + union{int,double} = 4+4+8)
     *
     * The app allocates based on v1's sizeof, but v2's library
     * expects the larger layout. To make the offset mismatch
     * deterministic, we embed the Variant in a zeroed buffer so
     * that bytes beyond the v1 allocation are known (0x00). When
     * v2's variant_get_int() reads v->i at offset 8 instead of 4,
     * it will read zeros instead of stack garbage. */

    /* Buffer large enough for v2's 16-byte layout, filled with
     * sentinel bytes (0xAA) to make out-of-bounds reads obvious.
     * Use alignas to satisfy v2's double alignment requirement
     * and avoid undefined behavior from misaligned pointer casts. */
    alignas(struct Variant) unsigned char buf[32];
    memset(buf, 0xAA, sizeof(buf));

    /* Place a v1-sized Variant at the start of the buffer. */
    struct Variant *v = (struct Variant *)buf;
    v->tag = 1;
    v->i = 42;

    printf("sizeof(Variant) = %zu (compiled against v1)\n", sizeof(struct Variant));
    printf("tag = %d, i = %d\n", v->tag, v->i);

    /* With v1 lib: variant_get_int reads v->i at offset 4 → returns 42.
     * With v2 lib: variant_get_int reads v->i at offset 8 (due to
     * double alignment) → reads sentinel bytes 0xAAAAAAAA instead.
     * The mismatch is now deterministic, not stack-dependent. */
    int result = variant_get_int(v);
    printf("variant_get_int() = %d\n", result);

    if (result != 42) {
        printf("ERROR: expected 42, got %d — ABI layout mismatch!\n", result);
        printf("  v2's variant_get_int() read 'i' at offset 8 instead of 4\n");
        printf("  (sentinel bytes 0xAA were read instead of the actual value)\n");
        return 2;
    }

    return 0;
}
