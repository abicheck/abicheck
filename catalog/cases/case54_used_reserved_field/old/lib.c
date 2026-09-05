/* bad.c — v1: struct has reserved fields as placeholders. */
#include "lib.h"
#include <stdlib.h>

Config* config_create(void) {
    Config *c = calloc(1, sizeof(Config));
    if (!c) return NULL;
    c->version = 1;
    c->flags = 0;
    return c;
}

void config_destroy(Config *c) { free(c); }
int config_get_flags(const Config *c) { return c->flags; }
