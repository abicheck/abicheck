#include "v1.hpp"
#include <new>

template<typename T>
Buffer<T>::Buffer(std::size_t n) : data_(new T[n]), size_(n) {}

template<typename T>
Buffer<T>::~Buffer() { delete[] data_; }

template<typename T>
T* Buffer<T>::data() { return data_; }

template<typename T>
std::size_t Buffer<T>::size() const { return size_; }

// Explicit instantiation definition
template class Buffer<int>;
