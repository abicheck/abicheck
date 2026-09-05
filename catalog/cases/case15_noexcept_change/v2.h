// v2.h — Buffer declaration as seen by consumers compiled against v2
// Key change: reset() loses noexcept — behavioural contract break
#pragma once

class Buffer {
public:
    Buffer();
    ~Buffer();
    void reset();  // <-- noexcept REMOVED (was noexcept in v1)
private:
    int* data_;
    int  size_;
};

extern "C" Buffer* make_buf();
extern "C" void    free_buf(Buffer* b);
