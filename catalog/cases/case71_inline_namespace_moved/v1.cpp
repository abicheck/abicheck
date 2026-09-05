#include "v1.h"
#include <cstring>

namespace crypto {
inline namespace v1 {
int encrypt(const Context *ctx, const char *data, int len) {
    (void)data;
    return ctx->algo + ctx->key_size + len;
}

int decrypt(const Context *ctx, const char *data, int len) {
    (void)data;
    return ctx->algo + ctx->key_size - len;
}
} // namespace v1
} // namespace crypto
