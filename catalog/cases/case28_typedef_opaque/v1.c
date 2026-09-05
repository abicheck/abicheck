#include "v1.h"
#include <stdlib.h>
#include <string.h>
dim_t get_dimension(int axis) { return (dim_t)axis; }
handle_t create_handle(void) { return 42; }
struct Context *context_create(void) {
    struct Context *c = malloc(sizeof(struct Context));
    memset(c, 0, sizeof(*c));
    return c;
}
void context_destroy(struct Context *ctx) { free(ctx); }
