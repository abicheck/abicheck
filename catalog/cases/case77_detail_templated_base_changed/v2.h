// case77 v2 — templated detail:: base grows a field, propagating layout
// breakage into every public knn_descriptor<Task> instantiation.
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
    int get_max_iter() const;  // new accessor
protected:
    int class_count_;
    int max_iter_;             // NEW FIELD — every instantiation grows
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

extern template class detail::descriptor_base<task::classification>;
extern template class detail::descriptor_base<task::regression>;
extern template class knn_descriptor<task::classification>;
extern template class knn_descriptor<task::regression>;

extern "C" knn_descriptor<task::classification>* mylib_make_classification();
extern "C" void mylib_free_classification(knn_descriptor<task::classification>*);

}  // namespace mylib
