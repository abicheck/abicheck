#include "v1.h"

namespace mylib {

template <typename F, typename D> descriptor<F, D>::descriptor() {}
template <typename F, typename D> int descriptor<F, D>::dimension() const { return 0; }

template class descriptor<float>;
template class descriptor<double>;

}  // namespace mylib
