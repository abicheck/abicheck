#ifndef STREAM_H
#define STREAM_H

class Device {
public:
    Device();
    virtual ~Device();
    virtual const char* kind() const;
    int device_id;
};

/* v2: Device became a VIRTUAL base — the classic preparation for a
   diamond hierarchy (a future DuplexStream : InStream, OutStream must
   share one Device). This rewrites the whole object model:
   Layout (x86-64): [Stream vptr @0] [bytes_moved @8]
                    [virtual Device base: vptr @16, device_id @24]
   → sizeof(Stream) = 32, bytes_moved moved from @16 to @8, the Device
   subobject moved to the END, and Stream now needs a VTT + vbase
   offsets to locate its own base at runtime. */
class Stream : public virtual Device {
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
