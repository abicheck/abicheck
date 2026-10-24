#pragma once
#include <cstddef>
class Table {
public:
  template <class T> int setArrays(T* p, std::size_t n) { return (int)n + (p ? 1 : 0); }
  template <class T> int setArrays(T* p, T* q, std::size_t n) { return (int)n + (p == q ? 1 : 0); }
  int fill() { return other(); }
  int other();
};
