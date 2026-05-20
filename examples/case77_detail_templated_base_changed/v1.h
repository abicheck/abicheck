// case77 v1 — public class inherits from a *templated* detail:: base.
//
// Mirrors the oneDAL pattern exactly:
//
//     namespace oneapi::dal::detail {
//         template <typename Task> class descriptor_base { ... };
//     }
//     namespace oneapi::dal::knn {
//         template <typename Float, typename Method, typename Task>
//         class descriptor : public detail::descriptor_base<Task> { ... };
//     }
//
// This differs from case74: there the detail:: base is non-templated, so
// reachability is a simple nominal lookup. Here the public class reaches
// the internal base via a *template argument* — the leak detector must
// traverse template-instantiation edges, not just nominal base edges.
#pragma once

namespace mylib {
namespace task {
struct classification {};
struct regression {};
}

namespace detail {

template <typename Task>
class descriptor_base {
public:
    descriptor_base();
    int get_class_count() const;
protected:
    int class_count_;
};

}  // namespace detail

template <typename Task = task::classification>
class knn_descriptor : public detail::descriptor_base<Task> {
public:
    knn_descriptor();
    int get_neighbor_count() const;
private:
    int neighbor_count_;
};

// Explicit instantiation declarations — match oneDAL's shipped surface.
extern template class detail::descriptor_base<task::classification>;
extern template class detail::descriptor_base<task::regression>;
extern template class knn_descriptor<task::classification>;
extern template class knn_descriptor<task::regression>;

extern "C" knn_descriptor<task::classification>* mylib_make_classification();
extern "C" void mylib_free_classification(knn_descriptor<task::classification>*);

}  // namespace mylib
