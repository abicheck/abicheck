// case74 v1 — public class inherits from detail::descriptor_base.
//
// This mirrors the oneDAL pattern:
//
//     namespace oneapi::dal::detail {
//         template <typename Task> class descriptor_base { ... };
//     }
//     namespace oneapi::dal::knn {
//         template <typename Method = ..., typename Task = ...>
//         class descriptor : public detail::descriptor_base<Task> { ... };
//     }
//
// The base class lives in the `detail::` namespace and is conceptually
// "internal", but it is part of the effective public ABI of `descriptor`.
#pragma once

namespace mylib {
namespace detail {

// "Internal" base class — users are not supposed to depend on its layout.
class descriptor_base {
public:
    descriptor_base();
    int get_class_count() const;
protected:
    int class_count_;
};

} // namespace detail

class knn_descriptor : public detail::descriptor_base {
public:
    knn_descriptor();
    int get_neighbor_count() const;
private:
    int neighbor_count_;
};

extern "C" knn_descriptor* mylib_make_descriptor();
extern "C" void mylib_free_descriptor(knn_descriptor*);

} // namespace mylib
