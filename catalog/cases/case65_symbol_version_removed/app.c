/* DEMO: app explicitly linked against crypto_hash@CRYPTO_1.0.
   When v2 is swapped in (CRYPTO_1.0 version node removed), the dynamic
   linker refuses to load the library — version requirement unsatisfied.

   This simulates a binary that was built against an old version of the
   library and depends on the CRYPTO_1.0 symbol version. */
#include <stdio.h>

/* Request the CRYPTO_1.0 version of crypto_hash explicitly.
   In real life, a binary built against a library that only provided
   CRYPTO_1.0 would have this version recorded automatically. */
int crypto_hash_v1(const char *data, int len);
__asm__(".symver crypto_hash_v1,crypto_hash@CRYPTO_1.0");

int main(void) {
    const char *msg = "hello";
    int h = crypto_hash_v1(msg, 5);
    printf("hash(\"%s\") = %d\n", msg, h);

    if (h != 0) {
        printf("OK: crypto_hash@CRYPTO_1.0 resolved successfully\n");
        return 0;
    }
    return 1;
}
