/* CRYPTO_1.0 version node removed — only CRYPTO_2.0 remains.
   Old binaries that were linked against crypto_hash@CRYPTO_1.0
   will fail to load: the dynamic linker cannot satisfy the version
   requirement. */
#include "v2.h"

int crypto_hash(const char *data, int len) {
    unsigned int h = 5381;
    for (int i = 0; i < len; i++)
        h = ((h << 5) + h) + (unsigned char)data[i];
    return (int)h;
}

int crypto_verify(const char *data, int len, int hash) {
    return crypto_hash(data, len) == hash;
}
