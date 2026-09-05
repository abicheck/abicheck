#pragma once
#include <cstddef>

// v1: Buffer<T> has two fields: data_ and size_
template<typename T>
class Buffer {
public:
    explicit Buffer(std::size_t n);
    ~Buffer();
    T* data();
    std::size_t size() const;
private:
    T*          data_;  // offset 0
    std::size_t size_;  // offset sizeof(T*)
};

// Explicit instantiation declaration — Buffer<int> lives in the .so
extern template class Buffer<int>;
