#ifndef CRYPTO_H
#define CRYPTO_H

namespace crypto {
/* v2: functions moved to inline namespace v2.
   Mangled names change: crypto::v1::encrypt -> crypto::v2::encrypt
   e.g. _ZN6crypto2v17encryptEPKNS0_7ContextEPKci
     -> _ZN6crypto2v27encryptEPKNS0_7ContextEPKci */
inline namespace v2 {
struct Context {
    int algo;
    int key_size;
};

int encrypt(const Context *ctx, const char *data, int len);
int decrypt(const Context *ctx, const char *data, int len);
} // namespace v2
} // namespace crypto

#endif
