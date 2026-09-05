#include "v2.h"
#include <cstring>

namespace crypto {
inline namespace v2 {
int encrypt(const Context *ctx, const char *data, int len) {
    (void)data;
    return ctx->algo + ctx->key_size + len;
}

int decrypt(const Context *ctx, const char *data, int len) {
    (void)data;
    return ctx->algo + ctx->key_size - len;
}
} // namespace v2
} // namespace crypto
