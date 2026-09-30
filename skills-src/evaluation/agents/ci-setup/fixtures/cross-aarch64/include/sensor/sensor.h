#ifndef SENSOR_H
#define SENSOR_H
#include <stdint.h>
typedef struct sensor_reading { uint64_t ts_ns; int32_t value; uint16_t flags; } sensor_reading;
int sensor_read(int fd, sensor_reading *out);
#endif
