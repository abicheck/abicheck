/* case30 v2: Field qualifier changes applied */
#ifndef V2_H
#define V2_H

#ifdef __cplusplus
extern "C" {
#endif

struct SensorConfig {
    const int    sample_rate;    /* became const: callers can no longer write this field */
    volatile int raw_value;      /* became volatile: hardware-mapped register, no caching */
    int          cache_hits;     /* unchanged */
};

int sensor_read(struct SensorConfig *cfg);

#ifdef __cplusplus
}
#endif
#endif
