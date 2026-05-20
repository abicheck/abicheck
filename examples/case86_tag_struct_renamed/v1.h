// case86 v1 — empty tag structs used for template specialization.
// Mirrors oneDAL's method::* / task::* tag families.
#pragma once

namespace mylib {

namespace method {
struct brute_force {};
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

extern template class descriptor<method::brute_force, task::classification>;
extern template class descriptor<method::kd_tree,     task::classification>;

}  // namespace mylib
