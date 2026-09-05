// case86 v2 — `method::brute_force` renamed to `method::search_brute`.
// The struct is empty so layout-based detectors see no change. But every
// explicit instantiation that referenced `brute_force` is re-mangled and
// the old symbols disappear.
#pragma once

namespace mylib {

namespace method {
struct search_brute {};   // <-- was `brute_force`
struct kd_tree {};
}
namespace task {
struct classification {};
struct regression {};
}

template <typename Method, typename Task>
class descriptor {
public:
    descriptor();
    int kind() const;
};

extern template class descriptor<method::search_brute, task::classification>;
extern template class descriptor<method::kd_tree,      task::classification>;

}  // namespace mylib
