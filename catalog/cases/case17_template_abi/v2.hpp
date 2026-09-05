#pragma once
#include <cstddef>

// v2: Buffer<T> gains a capacity_ field — changes layout!
template<typename T>
class Buffer {
public:
    explicit Buffer(std::size_t n);
    ~Buffer();
    T* data();
    std::size_t size() const;
    std::size_t capacity() const;
private:
    T*          data_;      // offset 0  (same)
    std::size_t size_;      // offset sizeof(T*)  (same)
    std::size_t capacity_;  // NEW: offset 2*sizeof(T*) — shifts nothing before it,
                            //      but sizeof(Buffer<int>) GROWS by sizeof(size_t)
};

// Explicit instantiation declaration
extern template class Buffer<int>;
