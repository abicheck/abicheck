/* good.c — v2: reserved fields now have real functionality. */
#include "lib.h"
#include <stdlib.h>

Config* config_create(void) {
    Config *c = calloc(1, sizeof(Config));
    if (!c) return NULL;
    c->version = 2;
    c->priority = 5;
    c->max_retries = 3;
    c->flags = 0;
    return c;
}

void config_destroy(Config *c) { free(c); }
int config_get_flags(const Config *c) { return c->flags; }
