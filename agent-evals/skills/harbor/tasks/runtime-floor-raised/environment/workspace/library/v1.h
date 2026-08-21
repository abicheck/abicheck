
#pragma once

class Buffer {
public:
    Buffer();
    ~Buffer();
    void reset() noexcept;  
private:
    int* data_;
    int  size_;
};

extern "C" Buffer* make_buf();
extern "C" void    free_buf(Buffer* b);
