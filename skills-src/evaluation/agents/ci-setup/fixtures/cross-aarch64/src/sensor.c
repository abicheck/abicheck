#include "sensor/sensor.h"
int sensor_read(int fd, sensor_reading *out) { out->ts_ns = 0; out->value = fd; out->flags = 0; return 0; }
