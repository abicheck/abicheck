/* good.c — v2: packed, sizeof(Record) = 6. */
#include "good.h"
#include <stdlib.h>

Record* record_create(char tag, int value, char status) {
    Record *r = malloc(sizeof(Record));
    r->tag = tag;
    r->value = value;
    r->status = status;
    return r;
}

void record_destroy(Record *r) { free(r); }
int record_get_value(const Record *r) { return r->value; }
