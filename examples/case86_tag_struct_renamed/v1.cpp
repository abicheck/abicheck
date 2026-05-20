#include "v1.h"

namespace mylib {

template <typename M, typename T>
descriptor<M, T>::descriptor() {}

template <typename M, typename T>
int descriptor<M, T>::kind() const { return 1; }

template class descriptor<method::brute_force, task::classification>;
template class descriptor<method::kd_tree,     task::classification>;

}  // namespace mylib
