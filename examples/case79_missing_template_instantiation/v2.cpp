#include "v2.h"

namespace mylib {

template <typename Float>
descriptor<Float>::descriptor() : threshold_(Float(0)) {}

template <typename Float>
Float descriptor<Float>::threshold() const { return threshold_; }

template <typename Float>
void descriptor<Float>::set_threshold(Float v) { threshold_ = v; }

// v2 dropped the double instantiation. Header still declares it as extern.
template class descriptor<float>;
// template class descriptor<double>;   // <— removed

}  // namespace mylib
