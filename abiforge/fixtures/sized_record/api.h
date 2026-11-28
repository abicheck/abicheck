// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <stddef.h>

struct record {
    size_t extent;
#ifdef SWAP_FIELDS
    unsigned optional, required;
#else
    unsigned required, optional;
#endif
};

unsigned read_record(const struct record *record);
void write_record(struct record *record);
