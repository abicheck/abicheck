#include "v1.h"
#include <stdio.h>

int main(void) {
    struct SensorConfig cfg;
    cfg.sample_rate = 1000;
    cfg.raw_value = 42;
    cfg.cache_hits = 0;

    int val = sensor_read(&cfg);
    printf("sensor_read = %d\n", val);

    if (val != 42) {
        printf("WRONG RESULT: field qualifier change broke runtime contract\n");
        return 1;
    }

    /* Layout unchanged: binary runs correctly against v2 lib.
     * The break is semantic/source only:
     *   - sample_rate gained const → writing to it is UB in v2
     *   - raw_value gained volatile → compiler may cache reads against contract
     * The app cannot detect this at runtime without compile-time knowledge of v2 API.
     */
    printf("OK (semantic break only: field qualifiers changed, binary runs identically)\n");
    return 0;
}
