// SPDX-License-Identifier: Apache-2.0
#include "api.h"

static int has_optional(const struct record *record) {
#ifdef IGNORE_EXTENT
    return 1;
#else
    return record->extent >= offsetof(struct record, optional) + sizeof(record->optional);
#endif
}

unsigned read_record(const struct record *record) {
    return record->required + (has_optional(record) ? record->optional : 0);
}

void write_record(struct record *record) {
    record->required = 7;
    if (has_optional(record)) record->optional = 11;
}
