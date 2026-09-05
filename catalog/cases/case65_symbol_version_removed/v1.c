#include "v1.h"

/* Two versions of crypto_hash: the old CRYPTO_1.0 and the current CRYPTO_2.0.
   Old binaries linked against CRYPTO_1.0 still resolve to the compat impl. */

/* Legacy implementation (CRYPTO_1.0) */
int crypto_hash_v1(const char *data, int len) {
    int h = 0;
    for (int i = 0; i < len; i++)
        h = h * 31 + data[i];
    return h;
}

/* Current implementation (CRYPTO_2.0) — better hash */
int crypto_hash_v2(const char *data, int len) {
    unsigned int h = 5381;
    for (int i = 0; i < len; i++)
        h = ((h << 5) + h) + (unsigned char)data[i];
    return (int)h;
}

int crypto_verify(const char *data, int len, int hash) {
    return crypto_hash_v2(data, len) == hash;
}

/* Symbol versioning: both CRYPTO_1.0 and CRYPTO_2.0 versions exported */
__asm__(".symver crypto_hash_v1,crypto_hash@CRYPTO_1.0");
__asm__(".symver crypto_hash_v2,crypto_hash@@CRYPTO_2.0");
