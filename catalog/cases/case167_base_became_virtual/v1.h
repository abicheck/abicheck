#ifndef STREAM_H
#define STREAM_H

class Device {
public:
    Device();
    virtual ~Device();
    virtual const char* kind() const;
    int device_id;
};

/* v1: ordinary (non-virtual) inheritance.
   Layout (x86-64): [Device: vptr @0, device_id @8, pad] [bytes_moved @16]
   → sizeof(Stream) = 24 */
class Stream : public Device {
public:
    Stream();
    ~Stream() override;
    const char* kind() const override;
    long bytes_moved;
};

extern "C" Stream* stream_create(int id);
extern "C" void    stream_destroy(Stream* s);
extern "C" long    stream_bytes(const Stream* s);

#endif
