#include "v2.h"

namespace mylib {
namespace detail {

template <typename Task>
descriptor_base<Task>::descriptor_base() : class_count_(2), max_iter_(100) {}

template <typename Task>
int descriptor_base<Task>::get_class_count() const { return class_count_; }

template <typename Task>
int descriptor_base<Task>::get_max_iter() const { return max_iter_; }

template class descriptor_base<task::classification>;
template class descriptor_base<task::regression>;

}  // namespace detail

template <typename Task>
knn_descriptor<Task>::knn_descriptor() : neighbor_count_(5) {}

template <typename Task>
int knn_descriptor<Task>::get_neighbor_count() const { return neighbor_count_; }

template class knn_descriptor<task::classification>;
template class knn_descriptor<task::regression>;

extern "C" knn_descriptor<task::classification>* mylib_make_classification() {
    return new knn_descriptor<task::classification>();
}
extern "C" void mylib_free_classification(knn_descriptor<task::classification>* p) {
    delete p;
}

}  // namespace mylib
