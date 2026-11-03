#include "extra.h"

#include <stddef.h>

struct extra_ctx {
    enum extra_mode mode;
    unsigned long bytes;
};

static struct extra_ctx the_ctx;

int extra_version = 3;

extra_ctx *extra_open(const char *name, enum extra_mode mode) {
    the_ctx.mode = mode;
    the_ctx.bytes = name == NULL ? 0 : 1;
    return &the_ctx;
}

int extra_stat_of(const extra_ctx *ctx, struct extra_stat *out) {
    out->bytes = ctx->bytes;
    out->mode = (int)ctx->mode;
    return 0;
}

void extra_close(extra_ctx *ctx) { ctx->bytes = 0; }
