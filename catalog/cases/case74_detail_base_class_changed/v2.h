// case74 v2 — "internal" detail::descriptor_base gains a new field.
//
// From the public-API author's perspective this is a private change: only
// `detail::descriptor_base` was modified. From the consumer's perspective
// every binary using `knn_descriptor` is now broken: sizeof(knn_descriptor)
// changed, `neighbor_count_`'s offset moved, and any stack-allocated
// `knn_descriptor` corrupts its surroundings.
#pragma once

namespace mylib {
namespace detail {

class descriptor_base {
public:
    descriptor_base();
    int get_class_count() const;
    int get_max_iter() const;  // new accessor
protected:
    int class_count_;
    int max_iter_;             // NEW FIELD — leaks through public ABI
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
