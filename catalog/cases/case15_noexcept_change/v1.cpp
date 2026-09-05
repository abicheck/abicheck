// v1: reset() declared noexcept — callers assume no exception propagation
#include <stdexcept>

class Buffer {
public:
    Buffer();
    ~Buffer();
    void reset() noexcept;  // <-- noexcept guarantee
private:
    int* data_;
    int  size_;
};

Buffer::Buffer() : data_(new int[64]), size_(64) {}
Buffer::~Buffer() { delete[] data_; }

void Buffer::reset() noexcept {
    for (int i = 0; i < size_; ++i)
        data_[i] = 0;
}

extern "C" Buffer* make_buf()        { return new Buffer(); }
extern "C" void    reset_buf(Buffer* b) { b->reset(); }
extern "C" void    free_buf(Buffer* b)  { delete b; }
