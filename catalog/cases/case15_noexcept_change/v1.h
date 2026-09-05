// v1.h — Buffer declaration as seen by consumers compiled against v1
#pragma once

class Buffer {
public:
    Buffer();
    ~Buffer();
    void reset() noexcept;  // <-- noexcept guarantee in v1
private:
    int* data_;
    int  size_;
};

extern "C" Buffer* make_buf();
extern "C" void    free_buf(Buffer* b);
