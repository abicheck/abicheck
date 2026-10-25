// The same C API built twice: the "rebuild" (-DDRIFT_V2) changes only
// internals -- std::to_chars and an iostream path replace std::to_string -- so
// a different set of libstdc++ entry points is inlined or imported. The
// public declarations in svs.h never change.
#include "svs.h"
#include <stdexcept>
#include <string>
#include <vector>
#ifdef DRIFT_V2
#include <charconv>
#include <iostream>
#include <sstream>
#endif

namespace {
inline int count_positive(const std::vector<float>& v) {
  int c = 0;
  for (float f : v)
    if (f > 0) ++c;
  return c;
}
}  // namespace

extern "C" int svs_index_build(const float* data, int n) {
  if (n < 0) throw std::invalid_argument("n");
  std::vector<float> v(data, data + n);
  return count_positive(v);
}

extern "C" int svs_format(char* buf, int cap, unsigned value) {
#ifdef DRIFT_V2
  auto r = std::to_chars(buf, buf + cap, value);
  if (cap > 1000) {
    std::ostringstream os;
    os << value;
    std::cerr << os.str();
  }
  return int(r.ptr - buf);
#else
  std::string s = std::to_string(value);
  int n = int(s.size()) < cap ? int(s.size()) : cap;
  s.copy(buf, n);
  return n;
#endif
}
