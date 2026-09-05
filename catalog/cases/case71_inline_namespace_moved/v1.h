#ifndef CRYPTO_H
#define CRYPTO_H

namespace crypto {
inline namespace v1 {
struct Context {
    int algo;
    int key_size;
};

int encrypt(const Context *ctx, const char *data, int len);
int decrypt(const Context *ctx, const char *data, int len);
} // namespace v1
} // namespace crypto

#endif
