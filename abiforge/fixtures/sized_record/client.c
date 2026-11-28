// SPDX-License-Identifier: Apache-2.0
#include "api.h"
#include <stdio.h>

int main(void) {
    // Backing storage is always complete. The caller exposes only a prefix.
    struct record short_record = {offsetof(struct record, optional), 3, 41};
    struct record full_record = {sizeof(struct record), 3, 41};
    unsigned short_sum = read_record(&short_record);
    unsigned full_sum = read_record(&full_record);
    write_record(&short_record);
    write_record(&full_record);
    printf("%u %u %u / %u %u %u\n", short_sum, short_record.required,
           short_record.optional, full_sum, full_record.required, full_record.optional);
    return !(short_sum == 3 && short_record.required == 7 && short_record.optional == 41 &&
             full_sum == 44 && full_record.required == 7 && full_record.optional == 11);
}
