#include "v2.h"

namespace mylib {

template <typename M, typename T>
descriptor<M, T>::descriptor() {}

template <typename M, typename T>
int descriptor<M, T>::kind() const { return 1; }

template class descriptor<method::search_brute, task::classification>;
template class descriptor<method::kd_tree,      task::classification>;

}  // namespace mylib
