#include "v1.h"

namespace mylib {

template <typename T, typename U> Box<T, U>::Box() {}
template <typename T, typename U> int Box<T, U>::size() const { return 0; }

template class Box<int>;

}  // namespace mylib
