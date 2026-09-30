#include <alpha/alpha.h>
int alpha_init(struct alpha_ctx *ctx) { ctx->flags = 0; ctx->size = sizeof *ctx; return 0; }
