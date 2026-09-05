#include "v1.h"

namespace mylib {
namespace detail {

descriptor_base::descriptor_base() : class_count_(2) {}
int descriptor_base::get_class_count() const { return class_count_; }

} // namespace detail

knn_descriptor::knn_descriptor() : neighbor_count_(5) {}
int knn_descriptor::get_neighbor_count() const { return neighbor_count_; }

extern "C" knn_descriptor* mylib_make_descriptor() { return new knn_descriptor(); }
extern "C" void mylib_free_descriptor(knn_descriptor* p) { delete p; }

} // namespace mylib
