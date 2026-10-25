#pragma once
#include <cstddef>
class Table {
public:
  template <class T> int setArrays(T* p, std::size_t n) { return (int)n + (p ? 1 : 0); }
  template <class T> int setImpl(T* p) { return p ? 1 : 0; }
  int fill() { double d = 0; return setArrays<double>(&d, 1); }
  int other(int h);
};
