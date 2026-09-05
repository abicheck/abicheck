/* bad.c — v1: natural alignment, sizeof(Record) = 12. */
#include "bad.h"
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
