/* DEMO: app compiled against v1 (non-virtual Sensor, sizeof=16).
   v2 adds virtual methods — inserting a vtable pointer at offset 0.
   The app accesses value_ at offset 0 but gets the vtable pointer,
   and accesses id_ at offset 8 but gets value_ instead. */
#include "v1.h"
#include <cstdio>
#include <cmath>

/* sensor_create and sensor_destroy are declared in v1.h */

int main() {
    /* Create sensor via library (v2 allocates 24 bytes with vptr) */
    Sensor* s = sensor_create(7, 98.6);

    /* Direct field access using v1 compiled offsets:
       v1: value_ at offset 0, id_ at offset 8
       v2: vptr at offset 0, value_ at offset 8, id_ at offset 16
       So s->value_ (v1 offset 0) reads the vtable pointer as a double!
       And s->id_ (v1 offset 8) reads value_ (98.6) as an int! */
    double val = s->value_;
    int id = s->id_;

    std::printf("sizeof(Sensor) = %zu (v1=16, v2=24)\n", sizeof(Sensor));
    std::printf("id    = %d (expected 7)\n", id);
    std::printf("value = %.1f (expected 98.6)\n", val);

    int broken = 0;
    if (id != 7) {
        std::printf("CORRUPTION: id_ at v1 offset 8 reads v2's value_ field!\n");
        broken = 1;
    }
    if (std::fabs(val - 98.6) > 0.001) {
        std::printf("CORRUPTION: value_ at v1 offset 0 reads v2's vtable pointer!\n");
        broken = 1;
    }

    sensor_destroy(s);
    return broken;
}
