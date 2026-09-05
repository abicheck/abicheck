#include "v1.h"

namespace mylib {

namespace detail {
algorithm_iface::~algorithm_iface() = default;
} // namespace detail

svm_algorithm::svm_algorithm() : state_(0) {}
int svm_algorithm::run() { state_ = 1; return 0; }
int svm_algorithm::status() const { return state_; }

extern "C" detail::algorithm_iface* mylib_make_svm() { return new svm_algorithm(); }
extern "C" void mylib_free_algo(detail::algorithm_iface* p) { delete p; }

} // namespace mylib
