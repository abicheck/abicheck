#ifndef SENSOR_H
#define SENSOR_H

/* Non-virtual class — no vtable pointer, sizeof = 16 bytes
   Layout: [value_(8 bytes @ offset 0)] [id_(4 bytes @ offset 8)] [pad(4)] */
class Sensor {
public:
    double value_;   /* offset 0,  8 bytes */
    int    id_;      /* offset 8,  4 bytes */
    /* + 4 bytes padding → sizeof = 16 */

    Sensor(int id, double initial);
    double read() const;
    void   calibrate(double offset);
    int    get_id() const;
};

extern "C" Sensor* sensor_create(int id, double initial);
extern "C" void    sensor_destroy(Sensor* s);

#endif
