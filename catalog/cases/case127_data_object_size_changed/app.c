#include <stdio.h>
#include "v1.h"

/* Witness for the copy-relocation hazard.
 *
 * The app is built as a non-PIE executable (-fno-pie -no-pie, see
 * CMakeLists.txt) so that GCC and Clang alike resolve its reference to
 * config_table through an R_X86_64_COPY relocation: the executable reserves
 * CONFIG_SLOTS (16) * sizeof(int) = 64 bytes for its own copy, and every
 * reference -- including the library's own -- is bound to that copy.
 *
 * v2 grows the object to 128 bytes. Its config_reset() writes all 32 slots,
 * i.e. 64 bytes past the end of the executable's copy, into memory the
 * executable owns (other objects or alignment padding in its BSS). The app
 * snapshots those 64 bytes before the call and compares afterwards.
 *   v1: bytes unchanged, slot 15 == 115              -> exit 0
 *   v2: bytes past the copy overwritten (CORRUPTION) -> exit 1
 *
 * A PIE consumer built by Clang uses R_X86_64_GLOB_DAT instead, binds to the
 * library's own 128-byte object and is not corrupted -- the hazard is
 * conditional on the consumer carrying a copy relocation. */
int main(void)
{
    const volatile unsigned char *past =
        (const volatile unsigned char *)&config_table[CONFIG_SLOTS];
    unsigned char before[sizeof(int) * CONFIG_SLOTS];
    for (size_t i = 0; i < sizeof before; ++i)
        before[i] = past[i];

    config_reset();

    int corrupted = 0;
    for (size_t i = 0; i < sizeof before; ++i)
        if (past[i] != before[i]) { corrupted = 1; break; }
    int v = config_get(CONFIG_SLOTS - 1);
    printf("config_table[%d] = %d (expected %d)\n",
           CONFIG_SLOTS - 1, v, 100 + CONFIG_SLOTS - 1);
    if (corrupted)
        printf("CORRUPTION: library wrote past the executable's copy of config_table\n");
    return (!corrupted && v == 100 + CONFIG_SLOTS - 1) ? 0 : 1;
}
