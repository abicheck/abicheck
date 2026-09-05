#ifndef SENSOR_H
#define SENSOR_H

/* Virtual method added — class now has a vtable pointer!
   Layout: [vptr(8 bytes @ offset 0)] [value_(8 bytes @ offset 8)]
           [id_(4 bytes @ offset 16)] [pad(4)] → sizeof = 24
   All data member offsets shift by 8 bytes (pointer size on x86-64). */
class Sensor {
public:
    double value_;   /* was offset 0, now offset 8  (shifted by vptr) */
    int    id_;      /* was offset 8, now offset 16 (shifted by vptr) */

    Sensor(int id, double initial);
    virtual ~Sensor();             /* NEW: virtual destructor */
    virtual double read() const;   /* was non-virtual, now virtual */
    void   calibrate(double offset);
    int    get_id() const;
};

extern "C" Sensor* sensor_create(int id, double initial);
extern "C" void    sensor_destroy(Sensor* s);

#endif
