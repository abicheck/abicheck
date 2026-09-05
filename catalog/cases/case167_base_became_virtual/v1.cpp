#include "v1.h"

Device::Device() : device_id(0) {}
Device::~Device() {}
const char* Device::kind() const { return "device"; }

Stream::Stream() : bytes_moved(0) {}
Stream::~Stream() {}
const char* Stream::kind() const { return "stream"; }

extern "C" Stream* stream_create(int id) {
    Stream* s = new Stream();
    s->device_id = id;
    s->bytes_moved = 4096;
    return s;
}

extern "C" void stream_destroy(Stream* s) { delete s; }

extern "C" long stream_bytes(const Stream* s) { return s->bytes_moved; }
